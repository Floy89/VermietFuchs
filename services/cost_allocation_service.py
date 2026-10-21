"""Kostenverteilung: das fachliche Herzstück der Nebenkostenabrechnung.

Der :class:`CostAllocationService` ist ein reiner Rechen-Service. Er greift
weder auf die Datenbank noch auf die Oberfläche zu, sondern bekommt alle
Daten übergeben und liefert das Ergebnis zurück. Dadurch ist er leicht
testbar und in jeder Umgebung wiederverwendbar.

Die Verteilung einer Kostenposition läuft in drei Schritten:

1. **Zerlegen** - Die Position wird in Bestandteile zerlegt. Die meisten
   Schlüssel haben genau einen Bestandteil; Heizkosten haben zwei
   (z. B. 70 % nach Verbrauch, 30 % nach Wohnfläche).
2. **Auf Wohnungen verteilen** - Jeder Bestandteil wird nach den Gewichten
   der Wohnungen (m², Personen oder Verbrauch) centgenau aufgeteilt.
3. **Auf Mieter verteilen** - Der Anteil einer Wohnung wird tagesgenau auf
   die Mieter aufgeteilt, die im Kostenzeitraum dort gewohnt haben. Tage
   ohne Mieter (Leerstand) trägt der Eigentümer.

Sonderfall "Direkte Zuordnung": Hier gibt es nichts zu rechnen - die Beträge
je Mieter stehen bereits in der Kostenposition (z. B. aus der Abrechnung
eines Messdienstes) und werden unverändert übernommen.

Erweiterbarkeit (Open/Closed-Prinzip): Ein neuer Umlageschlüssel braucht nur
eine neue :class:`WeightStrategy` und einen Eintrag in
:data:`DEFAULT_COMPONENT_BUILDERS` - der Verteil-Algorithmus bleibt unberührt.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Callable, Mapping, Protocol, Sequence

from models.apartment import Apartment
from models.billing import CostShare
from models.cost import Cost
from models.enums import AllocationKey
from models.tenant import Tenant
from models.value_objects import Money

#: Verbrauch je Wohnungs-ID im Zeitraum der Kostenposition.
ConsumptionMap = Mapping[int, Decimal]


# ---------------------------------------------------------------------- #
# Gewichtungs-Strategien
# ---------------------------------------------------------------------- #
class WeightStrategy(Protocol):
    """Bestimmt das Gewicht einer Wohnung für einen Umlageschlüssel."""

    label: str
    unit: str

    def weight(self, apartment: Apartment, consumption: ConsumptionMap) -> Decimal:
        """Gewicht der Wohnung (z. B. ihre Fläche); nie negativ."""
        ...


@dataclass(frozen=True)
class AreaWeight:
    """Verteilung nach Wohnfläche."""

    label: str = "Wohnfläche"
    unit: str = "m²"

    def weight(self, apartment: Apartment, consumption: ConsumptionMap) -> Decimal:
        """Die Wohnfläche in Quadratmetern."""
        return apartment.area_sqm


@dataclass(frozen=True)
class PersonWeight:
    """Verteilung nach Personenzahl."""

    label: str = "Personenzahl"
    unit: str = "Pers."

    def weight(self, apartment: Apartment, consumption: ConsumptionMap) -> Decimal:
        """Die Anzahl der Personen - im Zeitraum der Position tagesgenau gemittelt, falls bekannt."""
        if apartment.persons_in_period is not None:
            return apartment.persons_in_period
        return Decimal(apartment.person_count)


@dataclass(frozen=True)
class UnitWeight:
    """Gleichmäßige Verteilung: Jede Wohnung zählt als eine Einheit."""

    label: str = "Wohneinheiten"
    unit: str = "Einh."

    def weight(self, apartment: Apartment, consumption: ConsumptionMap) -> Decimal:
        """Immer 1 - unabhängig von Größe und Belegung."""
        return Decimal(1)


@dataclass(frozen=True)
class ConsumptionWeight:
    """Verteilung nach gemessenem Verbrauch."""

    label: str = "Verbrauch"
    unit: str = "Einh."

    def weight(self, apartment: Apartment, consumption: ConsumptionMap) -> Decimal:
        """Der Verbrauch der Wohnung; ohne Messwert 0."""
        if apartment.id is None:
            return Decimal(0)
        return consumption.get(apartment.id, Decimal(0))


@dataclass(frozen=True)
class CostComponent:
    """Ein Bestandteil einer Kostenposition mit seiner Gewichtungs-Strategie.

    Attributes:
        amount: Betrag, der in diesem Bestandteil verteilt wird.
        strategy: Wonach verteilt wird.
        quantity_total: Optional die Verbrauchsmenge, die mit diesem Bestandteil
            verteilt wird (für die Darstellung "entspricht x m³").
        quantity_unit: Einheit der Menge.
        is_difference: ``True`` für die Differenz zwischen Gesamtverbrauch
            und Wohnungszählern.
    """

    amount: Money
    strategy: WeightStrategy
    quantity_total: Decimal | None = None
    quantity_unit: str = ""
    is_difference: bool = False


#: Funktion, die eine Kostenposition in ihre Bestandteile zerlegt.
ComponentBuilder = Callable[[Cost], list[CostComponent]]


def _consumption_strategy(cost: Cost) -> ConsumptionWeight:
    """Erzeugt die Verbrauchs-Strategie mit der zur Kostenart passenden Einheit."""
    meter_type = cost.cost_type.meter_type
    return ConsumptionWeight(unit=meter_type.unit if meter_type else "Einh.")


def _by_area(cost: Cost) -> list[CostComponent]:
    return [CostComponent(cost.amount, AreaWeight())]


def _by_persons(cost: Cost) -> list[CostComponent]:
    return [CostComponent(cost.amount, PersonWeight())]


def _by_units(cost: Cost) -> list[CostComponent]:
    return [CostComponent(cost.amount, UnitWeight())]


def _by_consumption(cost: Cost) -> list[CostComponent]:
    return [CostComponent(cost.amount, _consumption_strategy(cost))]


def _by_consumption_and_area(cost: Cost) -> list[CostComponent]:
    """Mischschlüssel der Heizkostenverordnung: Verbrauchsanteil + Flächenanteil."""
    consumption_part = cost.amount.percentage(cost.consumption_share)
    # Der Flächenanteil ist der Rest - so geht beim Aufteilen kein Cent verloren.
    area_part = cost.amount - consumption_part
    return [
        CostComponent(consumption_part, _consumption_strategy(cost)),
        CostComponent(area_part, AreaWeight()),
    ]


DEFAULT_COMPONENT_BUILDERS: Mapping[AllocationKey, ComponentBuilder] = {
    AllocationKey.AREA: _by_area,
    AllocationKey.PERSONS: _by_persons,
    AllocationKey.CONSUMPTION: _by_consumption,
    AllocationKey.CONSUMPTION_AREA: _by_consumption_and_area,
    AllocationKey.UNITS: _by_units,
}

#: Strategien für die Verteilung einer Verbrauchsdifferenz (``Cost.difference_key``).
_DIFFERENCE_STRATEGIES: Mapping[AllocationKey, WeightStrategy] = {
    AllocationKey.UNITS: UnitWeight(),
    AllocationKey.AREA: AreaWeight(),
    AllocationKey.PERSONS: PersonWeight(),
}

MEASURED_LABEL = "Verbrauch laut Zählern"
DIFFERENCE_LABEL = "Differenz zum Gesamtverbrauch nach {key}"


def same_unit(first: str, second: str) -> bool:
    """Vergleicht zwei Einheiten großzügig ("m3", "cbm" und "m³" gelten als gleich)."""

    def normalize(unit: str) -> str:
        text = unit.strip().casefold().replace(" ", "")
        return {"m3": "m³", "cbm": "m³", "kubikmeter": "m³"}.get(text, text)

    return normalize(first) == normalize(second)


#: Text, der bei direkt zugeordneten Beträgen statt einer Rechenformel erscheint.
DIRECT_NOTE = "laut Einzelabrechnung"


# ---------------------------------------------------------------------- #
# Ergebnis
# ---------------------------------------------------------------------- #
@dataclass
class AllocationResult:
    """Ergebnis der Verteilung *einer* Kostenposition.

    Attributes:
        shares_by_tenant: Kostenanteile je Mieter-ID.
        vacancy_amount: Betrag, der auf Leerstand entfällt (trägt der Eigentümer).
        deviation_amount: Wirkung abweichender Schlüssel einzelner Mieter:
            Standard-Anteil minus tatsächlich berechneter Anteil, summiert.
            Positiv = es wird weniger umgelegt, negativ = mehr.
        warnings: Hinweise, z. B. auf fehlende Verbrauchswerte.
        owner_amount: Betrag einer nicht umlegbaren Position (Schlüssel ``OWNER``).
    """

    shares_by_tenant: dict[int, list[CostShare]] = field(default_factory=lambda: defaultdict(list))
    vacancy_amount: Money = Money.zero()
    owner_amount: Money = Money.zero()
    deviation_amount: Money = Money.zero()
    warnings: list[str] = field(default_factory=list)

    @property
    def allocated_amount(self) -> Money:
        """Summe aller auf Mieter verteilten Beträge."""
        return sum(
            (share.amount for shares in self.shares_by_tenant.values() for share in shares),
            Money.zero(),
        )


# ---------------------------------------------------------------------- #
# Service
# ---------------------------------------------------------------------- #
class CostAllocationService:
    """Verteilt Kostenpositionen auf Wohnungen und Mieter."""

    def __init__(self, builders: Mapping[AllocationKey, ComponentBuilder] | None = None) -> None:
        """
        Args:
            builders: Zerlegungsregeln je Umlageschlüssel. Standard sind die
                Regeln aus :data:`DEFAULT_COMPONENT_BUILDERS`.
        """
        self._builders = dict(builders or DEFAULT_COMPONENT_BUILDERS)

    def allocate(
        self,
        cost: Cost,
        apartments: Sequence[Apartment],
        tenants: Sequence[Tenant],
        consumption: ConsumptionMap,
        key_overrides: Mapping[int, AllocationKey] | None = None,
    ) -> AllocationResult:
        """Verteilt eine Kostenposition.

        Die Summe aus ``allocated_amount``, ``vacancy_amount`` und
        ``deviation_amount`` des Ergebnisses ist immer exakt ``cost.amount``.
        Ohne abweichende Schlüssel ist ``deviation_amount`` 0.

        Args:
            cost: Die zu verteilende Kostenposition.
            apartments: Alle Wohnungen der Immobilie.
            tenants: Alle Mieter dieser Wohnungen (auch ehemalige).
            consumption: Verbrauch je Wohnungs-ID im Zeitraum der Position.
            key_overrides: Abweichender Schlüssel je Mieter-ID für die
                Kostenart dieser Position (laut Mietvertrag). Mieter ohne
                Eintrag werden nach dem Schlüssel der Position abgerechnet.

        Returns:
            Die Anteile je Mieter, den Leerstandsanteil und etwaige Hinweise.
        """
        result = AllocationResult()

        if cost.allocation_key is AllocationKey.OWNER:
            # Nicht umlegbar: Nichts wird verteilt, der ganze Betrag bleibt beim Eigentümer.
            result.owner_amount = cost.amount
            return result
        if cost.allocation_key is AllocationKey.DIRECT:
            self._allocate_direct(cost, tenants, result)
            return result

        if not apartments:
            result.vacancy_amount = cost.amount
            result.warnings.append(
                f"'{cost.title}': Die Immobilie hat keine Wohnungen - die Kosten wurden nicht verteilt."
            )
            return result

        tenants_by_apartment: dict[int, list[Tenant]] = defaultdict(list)
        for tenant in tenants:
            tenants_by_apartment[tenant.apartment_id].append(tenant)

        for component in self._components_for(cost, apartments, consumption, result):
            self._allocate_component(cost, component, apartments, tenants_by_apartment, consumption, result)
        self._add_tenant_quantities(result)

        if key_overrides:
            self._apply_key_overrides(cost, apartments, tenants, consumption, key_overrides, result)
        return result

    def allocate_components(
        self,
        cost: Cost,
        components: Sequence[CostComponent],
        apartments: Sequence[Apartment],
        tenants: Sequence[Tenant],
        consumption: ConsumptionMap,
    ) -> AllocationResult:
        """Verteilt eine Position, deren Bestandteile schon feststehen (z. B. nach der Wasserregel).

        Wie :meth:`allocate`, nur ohne Zerlegung nach dem Umlageschlüssel und ohne
        abweichende Schlüssel einzelner Mieter. Die Summe aus ``allocated_amount``
        und ``vacancy_amount`` ist die Summe der Bestandteile.
        """
        result = AllocationResult()
        if not apartments:
            result.vacancy_amount = sum((component.amount for component in components), Money.zero())
            result.warnings.append(
                f"'{cost.title}': Die Immobilie hat keine Wohnungen - die Kosten wurden nicht verteilt."
            )
            return result
        tenants_by_apartment: dict[int, list[Tenant]] = defaultdict(list)
        for tenant in tenants:
            tenants_by_apartment[tenant.apartment_id].append(tenant)
        for component in components:
            self._allocate_component(cost, component, apartments, tenants_by_apartment, consumption, result)
        self._add_tenant_quantities(result)
        return result

    # ------------------------------------------------------------------ #
    # Interna
    # ------------------------------------------------------------------ #
    def _components_for(
        self,
        cost: Cost,
        apartments: Sequence[Apartment],
        consumption: ConsumptionMap,
        result: AllocationResult,
    ) -> list[CostComponent]:
        """Zerlegt die Position in Bestandteile - samt Verbrauchsdifferenz, falls es eine gibt.

        Nennt eine nach Verbrauch umgelegte Rechnung einen Gesamtverbrauch G
        (z. B. 200 m³ am Hauptzähler) und messen die Wohnungszähler zusammen
        nur M (148 m³), dann bezieht sich der Rechnungsbetrag auf G:

        * Standard (``difference_key`` = Verbrauch): Die Differenz wird wie
          der gemessene Verbrauch verteilt. Am Betrag ändert das nichts; die
          Abrechnung weist aber zusätzlich aus, welcher Menge von G der Anteil
          entspricht.
        * Anderer Schlüssel: Der Betrag wird geteilt - ``Betrag × M / G`` nach
          gemessenem Verbrauch, der Rest (die Differenz) nach dem gewählten
          Schlüssel, z. B. gleichmäßig je Wohnung.

        Ohne Gesamtverbrauch, bei abweichender Einheit oder wenn die Zähler
        nicht weniger messen als die Rechnung nennt, bleibt es bei der
        gewöhnlichen Verteilung nach gemessenem Verbrauch.
        """
        components = self._builders[cost.allocation_key](cost)
        meter_type = cost.cost_type.meter_type
        if cost.allocation_key is not AllocationKey.CONSUMPTION or cost.consumption is None or meter_type is None:
            return components

        if not same_unit(cost.consumption_unit, meter_type.unit):
            if cost.splits_difference:
                result.warnings.append(
                    f"'{cost.title}': Der Gesamtverbrauch ist in '{cost.consumption_unit}' angegeben, die Zähler "
                    f"messen in '{meter_type.unit}' - die Differenz kann nicht ermittelt werden. "
                    "Verteilt wurde nach gemessenem Verbrauch."
                )
            return components

        total = cost.consumption
        measured = sum((consumption.get(a.id, Decimal(0)) for a in apartments if a.id is not None), Decimal(0))
        if measured <= 0:
            return components  # keine Zählerstände: Die Auffangregel meldet das.
        if total < measured:
            result.warnings.append(
                f"'{cost.title}': Der Gesamtverbrauch laut Rechnung ({_quantity(total)} {meter_type.unit}) ist "
                f"kleiner als die Summe der Wohnungszähler ({_quantity(measured)} {meter_type.unit}). "
                "Verteilt wurde nach gemessenem Verbrauch - bitte Rechnung und Zählerstände prüfen."
            )
            return components
        if total == measured:
            return components

        base = components[0]
        strategy = _DIFFERENCE_STRATEGIES.get(cost.difference_key)
        if strategy is None:
            # Differenz wie gemessen verteilen: gleicher Betrag, zusätzlich die Menge ausweisen.
            return [replace(base, quantity_total=total, quantity_unit=meter_type.unit)]

        exact = Decimal(cost.amount.cents) * measured / total
        measured_amount = Money(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)))
        return [
            CostComponent(
                measured_amount,
                replace(base.strategy, label=MEASURED_LABEL),  # type: ignore[type-var]
                quantity_total=measured,
                quantity_unit=meter_type.unit,
            ),
            CostComponent(
                cost.amount - measured_amount,
                replace(strategy, label=DIFFERENCE_LABEL.format(key=strategy.label)),  # type: ignore[type-var]
                quantity_total=total - measured,
                quantity_unit=meter_type.unit,
                is_difference=True,
            ),
        ]

    @staticmethod
    def _add_tenant_quantities(result: AllocationResult) -> None:
        """Trägt am Differenz-Bestandteil den gesamten zugerechneten Verbrauch des Mieters ein."""
        for tenant_id, shares in result.shares_by_tenant.items():
            if not any(share.is_difference for share in shares):
                continue
            total = sum((share.quantity or Decimal(0) for share in shares), Decimal(0))
            result.shares_by_tenant[tenant_id] = [
                replace(share, tenant_quantity=total) if share.is_difference else share for share in shares
            ]

    def _apply_key_overrides(
        self,
        cost: Cost,
        apartments: Sequence[Apartment],
        tenants: Sequence[Tenant],
        consumption: ConsumptionMap,
        key_overrides: Mapping[int, AllocationKey],
        result: AllocationResult,
    ) -> None:
        """Ersetzt bei Mietern mit abweichendem Schlüssel den Standard-Anteil.

        Vorgehen: Zuerst wurde für *alle* Mieter nach dem Schlüssel der
        Rechnung verteilt. Für jeden Mieter mit abweichendem Schlüssel wird
        dieser Anteil nun durch eine eigene Rechnung ersetzt::

            Betrag × Gewicht seiner Wohnung / Gesamtgewicht × seine Tage / Tage des Zeitraums

        Die übrigen Mieter behalten exakt ihren Standard-Anteil - niemand
        zahlt mehr oder weniger, weil ein *anderer* Mieter einen abweichenden
        Vertrag hat. Dadurch ergänzen sich die Anteile nicht mehr zu 100 %;
        die Differenz wird in ``deviation_amount`` festgehalten.
        """
        apartments_by_id = {apartment.id: apartment for apartment in apartments}
        period_days = cost.period.days

        for tenant in tenants:
            if tenant.id is None or tenant.id not in key_overrides:
                continue
            key = key_overrides[tenant.id]
            standard_shares = result.shares_by_tenant.get(tenant.id)
            apartment = apartments_by_id.get(tenant.apartment_id)
            # Gleicher Schlüssel wie der Standard, oder der Mieter hat im
            # Zeitraum gar nicht gewohnt -> nichts zu ersetzen.
            if key is cost.allocation_key or not standard_shares or apartment is None:
                continue

            strategy = self._builders[key](cost)[0].strategy
            weights = {a.id: strategy.weight(a, consumption) for a in apartments}
            total_weight = sum(weights.values(), Decimal(0))
            if total_weight <= 0:
                result.warnings.append(
                    f"'{cost.title}': Für den abweichenden Schlüssel '{strategy.label}' von "
                    f"{tenant.name} liegen keine Werte vor - es bleibt beim Standard."
                )
                continue

            days = tenant.occupied_days(cost.period)
            exact = Decimal(cost.amount.cents) * weights[apartment.id] / total_weight * days / period_days
            amount = Money(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)))
            standard_amount = sum((share.amount for share in standard_shares), Money.zero())

            result.shares_by_tenant[tenant.id] = [
                CostShare(
                    cost=cost,
                    component=strategy.label,
                    unit=strategy.unit,
                    component_amount=cost.amount,
                    apartment_weight=weights[apartment.id],
                    total_weight=total_weight,
                    days=days,
                    period_days=period_days,
                    amount=amount,
                    individual_key=True,
                )
            ]
            result.deviation_amount += standard_amount - amount

    @staticmethod
    def _allocate_direct(cost: Cost, tenants: Sequence[Tenant], result: AllocationResult) -> None:
        """Übernimmt die in der Kostenposition hinterlegten Beträge je Mieter.

        Was nicht zugeordnet ist (z. B. der Anteil eines Leerstands in der
        Messdienst-Abrechnung), verbleibt beim Eigentümer.
        """
        period_days = cost.period.days
        assigned = Money.zero()
        for tenant in tenants:
            if tenant.id is None or tenant.id not in cost.direct_shares:
                continue
            amount = cost.direct_shares[tenant.id]
            assigned += amount
            result.shares_by_tenant[tenant.id].append(
                CostShare(
                    cost=cost,
                    component="Direkte Zuordnung",
                    unit="",
                    component_amount=cost.amount,
                    apartment_weight=Decimal(0),
                    total_weight=Decimal(0),
                    days=tenant.occupied_days(cost.period),
                    period_days=period_days,
                    amount=amount,
                    note=DIRECT_NOTE,
                )
            )
        result.vacancy_amount += cost.amount - assigned

    def _allocate_component(
        self,
        cost: Cost,
        component: CostComponent,
        apartments: Sequence[Apartment],
        tenants_by_apartment: Mapping[int, list[Tenant]],
        consumption: ConsumptionMap,
        result: AllocationResult,
    ) -> None:
        """Verteilt einen Bestandteil auf Wohnungen und anschließend auf Mieter."""
        strategy = component.strategy
        weights = [strategy.weight(apartment, consumption) for apartment in apartments]
        total_weight = sum(weights, Decimal(0))

        # Ohne jede Bezugsgröße (z. B. keine Zählerstände erfasst) lässt sich
        # nicht nach diesem Schlüssel verteilen. Ersatzweise gilt die
        # Wohnfläche - das entspricht der gesetzlichen Auffangregel
        # (§ 556a Abs. 1 BGB) und wird dem Benutzer als Hinweis gemeldet.
        if total_weight <= 0:
            fallback = AreaWeight()
            result.warnings.append(
                f"'{cost.title}': Für den Schlüssel '{strategy.label}' liegen keine Werte vor - "
                "ersatzweise wurde nach Wohnfläche verteilt."
            )
            strategy = fallback
            weights = [strategy.weight(apartment, consumption) for apartment in apartments]
            total_weight = sum(weights, Decimal(0))
            if total_weight <= 0:
                result.vacancy_amount += component.amount
                return

        apartment_amounts = component.amount.allocate(weights)
        period_days = cost.period.days

        for apartment, weight, apartment_amount in zip(apartments, weights, apartment_amounts):
            apartment_tenants = [
                tenant
                for tenant in tenants_by_apartment.get(apartment.id or -1, [])
                if tenant.occupied_days(cost.period) > 0
            ]
            tenant_days = [tenant.occupied_days(cost.period) for tenant in apartment_tenants]
            # Tage ohne Mietvertrag. max() schützt vor negativen Werten, falls
            # sich Verträge (entgegen der Eingabeprüfung) überschneiden.
            vacancy_days = max(0, period_days - sum(tenant_days))

            parts = apartment_amount.allocate([*tenant_days, vacancy_days])
            result.vacancy_amount += parts[-1]

            for tenant, days, amount in zip(apartment_tenants, tenant_days, parts):
                if tenant.id is None:
                    continue
                result.shares_by_tenant[tenant.id].append(
                    CostShare(
                        cost=cost,
                        component=strategy.label,
                        unit=strategy.unit,
                        component_amount=component.amount,
                        apartment_weight=weight,
                        total_weight=total_weight,
                        days=days,
                        period_days=period_days,
                        amount=amount,
                        quantity=(
                            component.quantity_total * weight / total_weight * days / period_days
                            if component.quantity_total is not None
                            else None
                        ),
                        quantity_total=component.quantity_total,
                        quantity_unit=component.quantity_unit,
                        is_difference=component.is_difference,
                    )
                )


def _quantity(value: Decimal) -> str:
    """Menge für Hinweistexte: höchstens drei Nachkommastellen, deutsches Komma."""
    text = f"{value.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")
