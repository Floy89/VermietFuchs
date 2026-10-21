"""Ergebnisobjekte einer Nebenkostenabrechnung.

Diese Klassen werden nicht gespeichert. Der
:class:`~services.billing_service.BillingService` berechnet sie jedes Mal
frisch aus Stammdaten, Rechnungen, Zählerständen und Vorauszahlungsraten.
Dadurch kann eine Abrechnung nie von den zugrunde liegenden Daten abweichen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

from models.apartment import Apartment
from models.cost import Cost
from models.prepayment_rate import PrepaymentRate
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money


#: Empfohlene Vorauszahlungen werden auf volle 10 € gerundet (546 € -> 550 €).
PREPAYMENT_STEP: Final[Money] = Money(1000)
_DAYS_PER_MONTH: Final[Decimal] = Decimal(365) / Decimal(12)


@dataclass
class BillingPeriodOverride:
    """Von Hand festgelegter Abrechnungszeitraum für ein Mietverhältnis.

    Normalerweise wird ein Mieter für die Tage abgerechnet, an denen sein
    Vertrag im Abrechnungszeitraum bestand. Mit einem Override lässt sich
    dieser Zeitraum einschränken, etwa für eine Teilabrechnung.

    Attributes:
        tenant_id: Der Mieter.
        period: Der Abrechnungszeitraum der Immobilie, für den der Override gilt.
        billed: Der für diesen Mieter abzurechnende Zeitraum.
    """

    tenant_id: int
    period: DateRange
    billed: DateRange
    id: int | None = None


@dataclass(frozen=True)
class CostShare:
    """Anteil eines Mieters an *einem Bestandteil* einer Kostenposition.

    Eine Position mit Mischschlüssel (Heizung: Verbrauch + Fläche) erzeugt
    zwei Bestandteile und damit zwei ``CostShare``-Objekte je Mieter.

    Alle Angaben, die für die nachvollziehbare Darstellung im
    Abrechnungsschreiben nötig sind, werden mitgeführt:
    ``component_amount × apartment_weight / total_weight × days / period_days``.

    Attributes:
        cost: Die zugrunde liegende Kostenposition.
        component: Bezeichnung des Bestandteils ("Verbrauch", "Wohnfläche", ...).
        unit: Einheit der Gewichte ("m²", "Pers.", "kWh", ...).
        component_amount: Betrag, der in diesem Bestandteil insgesamt verteilt wird.
        apartment_weight: Gewicht der Wohnung des Mieters.
        total_weight: Summe der Gewichte aller Wohnungen.
        days: Tage des Kostenzeitraums, die für den Mieter abgerechnet werden.
        period_days: Gesamtzahl der Tage des Kostenzeitraums.
        amount: Der auf den Mieter entfallende Betrag.
        note: Ersetzt die Rechenformel in der Darstellung, wenn der Betrag
            nicht berechnet, sondern direkt zugeordnet wurde.
        individual_key: ``True``, wenn der Anteil nach einem abweichenden
            Schlüssel laut Mietvertrag berechnet wurde (nicht nach dem
            Standard der Rechnung).
        quantity: Dem Mieter in diesem Bestandteil zugerechnete Verbrauchsmenge.
            Nur gesetzt, wenn die Rechnung einen Gesamtverbrauch nennt, der
            über der Summe der Wohnungszähler liegt.
        quantity_total: Menge, die in diesem Bestandteil insgesamt verteilt
            wird (Gesamtverbrauch, gemessene Summe oder Differenz).
        quantity_unit: Einheit der Mengen, z. B. "m³".
        is_difference: ``True`` für den Bestandteil, der die Differenz
            zwischen Gesamtverbrauch und Wohnungszählern verteilt.
        tenant_quantity: Am Differenz-Bestandteil: der dem Mieter insgesamt
            zugerechnete Verbrauch (gemessen + Anteil an der Differenz).
    """

    cost: Cost
    component: str
    unit: str
    component_amount: Money
    apartment_weight: Decimal
    total_weight: Decimal
    days: int
    period_days: int
    amount: Money
    note: str = ""
    individual_key: bool = False
    quantity: Decimal | None = None
    quantity_total: Decimal | None = None
    quantity_unit: str = ""
    is_difference: bool = False
    tenant_quantity: Decimal | None = None


@dataclass
class TenantStatement:
    """Die Abrechnung für ein Mietverhältnis.

    Attributes:
        tenant: Der Mieter (mit seinem echten Vertragszeitraum).
        apartment: Seine Wohnung.
        period: Abrechnungszeitraum der Immobilie.
        occupancy: Der für diesen Mieter abgerechnete Zeitraum.
        contract_occupancy: Vertragszeitraum innerhalb von ``period`` - der
            Vorschlag, von dem ``occupancy`` nur bei einer Anpassung von Hand abweicht.
        shares: Kostenanteile.
        prepayments: Vorauszahlungen laut vereinbarten Raten für ``occupancy``.
        prepayment_rates: Die dabei berücksichtigten Raten.
        current_rate: Die *heute* gültige monatliche Vorauszahlung - Grundlage
            für die Empfehlung einer Anpassung. ``None``, wenn das
            Mietverhältnis bereits beendet ist.
    """

    tenant: Tenant
    apartment: Apartment
    period: DateRange
    occupancy: DateRange
    contract_occupancy: DateRange
    shares: list[CostShare] = field(default_factory=list)
    prepayments: Money = Money.zero()
    prepayment_rates: list[PrepaymentRate] = field(default_factory=list)
    current_rate: Money | None = None

    @property
    def monthly_costs(self) -> Money:
        """Die Kosten des Mieters, umgerechnet auf einen Monat.

        Der Kostenanteil wird taggenau auf ein Jahr hochgerechnet und durch
        zwölf geteilt. So ergibt auch ein unterjähriges Mietverhältnis einen
        vergleichbaren Monatswert.
        """
        exact = Decimal(self.total_costs.cents) / Decimal(self.occupancy.days) * _DAYS_PER_MONTH
        return Money(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)))

    @property
    def recommended_prepayment(self) -> Money:
        """Kostendeckende monatliche Vorauszahlung, auf volle 10 € gerundet.

        Gerundet wird kaufmännisch zur nächsten Zehnerstelle - 546 € werden
        zu 550 €, 544 € zu 540 €. Die Empfehlung kann also über oder unter der
        bisherigen Vorauszahlung liegen. Grundlage sind allein die Kosten
        dieses Abrechnungszeitraums; absehbare Preisänderungen sind nicht
        eingerechnet.
        """
        steps = (Decimal(self.monthly_costs.cents) / Decimal(PREPAYMENT_STEP.cents)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        return Money(max(0, int(steps)) * PREPAYMENT_STEP.cents)

    @property
    def prepayment_adjustment(self) -> Money | None:
        """Empfohlene Änderung der Vorauszahlung (positiv = Erhöhung).

        ``None``, wenn das Mietverhältnis beendet ist - dann gibt es nichts anzupassen.
        """
        if self.current_rate is None:
            return None
        return self.recommended_prepayment - self.current_rate

    @property
    def is_adjusted(self) -> bool:
        """``True``, wenn der Abrechnungszeitraum von Hand angepasst wurde."""
        return self.occupancy != self.contract_occupancy

    @property
    def total_costs(self) -> Money:
        """Summe aller auf den Mieter entfallenden Kostenanteile."""
        return sum((share.amount for share in self.shares), Money.zero())

    @property
    def balance(self) -> Money:
        """Saldo aus Sicht des Mieters.

        Positiv = Guthaben (Vorauszahlungen höher als Kosten),
        negativ = Nachzahlung.
        """
        return self.prepayments - self.total_costs

    @property
    def is_credit(self) -> bool:
        """``True``, wenn der Mieter Geld zurückbekommt."""
        return self.balance.cents > 0


@dataclass
class BillingResult:
    """Das Gesamtergebnis einer Abrechnung für eine Immobilie und einen Zeitraum.

    Attributes:
        property: Die abgerechnete Immobilie.
        period: Der Abrechnungszeitraum.
        costs: Alle berücksichtigten Kostenpositionen; Rechnungen, die über
            den Abrechnungszeitraum hinausragen, sind hier bereits anteilig.
        statements: Je Mietverhältnis eine Einzelabrechnung.
        vacancy_amount: Kostenanteil, der auf Leerstand bzw. nicht
            abgerechnete Zeiten entfällt und damit beim Eigentümer verbleibt.
        deviation_amount: Wirkung abweichender Umlageschlüssel: um so viel
            wird *weniger* umgelegt als nach dem Standard (negativ = es wird
            mehr umgelegt). Ohne abweichende Schlüssel immer 0,00 €.
        warnings: Hinweise für den Benutzer, z. B. fehlende Zählerstände.
        owner_amount: Summe der nicht umlegbaren Positionen (trägt der Eigentümer).
    """

    property: Property
    period: DateRange
    costs: list[Cost] = field(default_factory=list)
    statements: list[TenantStatement] = field(default_factory=list)
    vacancy_amount: Money = Money.zero()
    deviation_amount: Money = Money.zero()
    warnings: list[str] = field(default_factory=list)
    owner_amount: Money = Money.zero()

    @property
    def total_costs(self) -> Money:
        """Summe aller berücksichtigten Kostenpositionen."""
        return sum((cost.amount for cost in self.costs), Money.zero())

    @property
    def unallocated_amount(self) -> Money:
        """Was nicht auf Mieter umgelegt wird (Leerstand plus Wirkung abweichender Schlüssel).

        Negativ bedeutet: Es wird insgesamt mehr umgelegt, als angefallen ist.
        """
        return self.total_costs - self.total_allocated

    def allocated_for(self, cost: Cost) -> Money:
        """Summe der Mieteranteile an genau dieser Kostenposition."""
        return sum(
            (share.amount for statement in self.statements for share in statement.shares if share.cost is cost),
            Money.zero(),
        )

    @property
    def total_allocated(self) -> Money:
        """Summe der auf Mieter umgelegten Kosten."""
        return sum((statement.total_costs for statement in self.statements), Money.zero())

    @property
    def total_prepayments(self) -> Money:
        """Summe der Vorauszahlungen aller Mietverhältnisse."""
        return sum((statement.prepayments for statement in self.statements), Money.zero())
