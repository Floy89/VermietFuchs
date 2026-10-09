"""Rechengrundlagen eines Rechnungsabschnitts: Verbrauch aus Gasvolumen, Kosten aus Tarifangaben.

Versorger weisen Verbrauch und Kosten nicht immer fertig je Abschnitt aus.
Eine Gasrechnung nennt zum Beispiel je Ablesezeitraum nur

* das **Abrechnungsvolumen** in m³, die **Zustandszahl** und den **Brennwert**
  - daraus ergibt sich der Verbrauch in kWh -, und
* für die ganze Rechnung **Arbeitspreis**, **Grundpreis** und **Umsatzsteuer**
  - daraus ergeben sich die Kosten.

Mit diesen Angaben rechnet die Anwendung Verbrauch und Kosten je Abschnitt
selbst aus. So lässt sich etwa der Anteil eines Kalenderjahres bestimmen,
ohne die Rechnung von Hand auseinanderzuziehen.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Sequence

from models.value_objects import DateRange, Money

#: Bis zu dieser Abweichung gilt ein berechneter Gesamtbetrag als "derselbe" wie
#: der Rechnungsbetrag. Versorger runden jede Preiszeile einzeln, die Anwendung
#: erst am Ende - das ergibt Differenzen von wenigen Cent.
ROUNDING_TOLERANCE: Final[Money] = Money(10)


@dataclass(frozen=True)
class SplitCalculation:
    """Angaben, aus denen Verbrauch und Kosten eines Abschnitts berechnet werden.

    Alle Felder sind optional; ``None`` heißt "nicht angegeben".

    Attributes:
        volume: Abrechnungsvolumen in m³ (Differenz der Zählerstände).
        condition_number: Zustandszahl (rechnet Betriebs- in Normvolumen um).
        calorific_value: Brennwert in kWh/m³.
        working_price: Arbeitspreis netto je Verbrauchseinheit (z. B. €/kWh).
        base_price: Grundpreis netto je Jahr.
        vat_percent: Umsatzsteuersatz in Prozent.
    """

    volume: Decimal | None = None
    condition_number: Decimal | None = None
    calorific_value: Decimal | None = None
    working_price: Decimal | None = None
    base_price: Decimal | None = None
    vat_percent: Decimal | None = None

    @property
    def is_empty(self) -> bool:
        """``True``, wenn gar nichts angegeben ist."""
        return all(value is None for value in vars(self).values())

    @property
    def has_volume(self) -> bool:
        """``True``, wenn sich der Verbrauch aus dem Volumen berechnen lässt."""
        return all(
            value is not None and value > 0
            for value in (self.volume, self.condition_number, self.calorific_value)
        )

    @property
    def has_prices(self) -> bool:
        """``True``, wenn sich die Kosten aus Preisen berechnen lassen."""
        return any(value is not None and value > 0 for value in (self.working_price, self.base_price))

    def energy(self) -> Decimal | None:
        """Verbrauch in kWh: Volumen × Zustandszahl × Brennwert, auf ganze kWh gerundet.

        Die Rundung auf ganze Kilowattstunden je Abschnitt entspricht der
        Praxis der Versorger; dadurch stimmt die Summe der Abschnitte mit dem
        Gesamtverbrauch der Rechnung überein.
        """
        if not self.has_volume:
            return None
        assert self.volume is not None and self.condition_number is not None and self.calorific_value is not None
        exact = self.volume * self.condition_number * self.calorific_value
        return exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)

    def gross_cost(self, period: DateRange, consumption: Decimal | None) -> Decimal | None:
        """Kosten des Abschnitts inklusive Umsatzsteuer - *ungerundet*.

        ``(Verbrauch × Arbeitspreis + Grundpreis × Jahresanteil) × (1 + USt)``

        Gerundet wird erst über alle Abschnitte gemeinsam (siehe
        :func:`fit_amounts`), damit beim Summieren kein Cent entsteht oder fehlt.

        Returns:
            Den Betrag oder ``None``, wenn keine Preise angegeben sind.
        """
        if not self.has_prices:
            return None
        net = (consumption or Decimal(0)) * (self.working_price or Decimal(0))
        net += (self.base_price or Decimal(0)) * year_fraction(period)
        return net * (Decimal(1) + (self.vat_percent or Decimal(0)) / Decimal(100))


def year_fraction(period: DateRange) -> Decimal:
    """Anteil eines Zeitraums am Jahr, taggenau je Kalenderjahr.

    Ein Jahrespreis wird je Tag mit ``1/365`` bzw. im Schaltjahr ``1/366``
    berechnet. Ein Zeitraum über den Jahreswechsel setzt sich deshalb aus den
    Anteilen der beteiligten Jahre zusammen:
    01.06.2024–31.05.2025 = 214/366 + 151/365.
    """
    total = Decimal(0)
    for year in range(period.start.year, period.end.year + 1):
        overlap = period.intersection(DateRange(date(year, 1, 1), date(year, 12, 31)))
        if overlap is not None:
            total += Decimal(overlap.days) / Decimal(DateRange.for_year(year).days)
    return total


def fit_amounts(exact_amounts: Sequence[Decimal], invoice_amount: Money | None) -> list[Money]:
    """Rundet berechnete Beträge auf Cent - passend zum Rechnungsbetrag, wenn der nahe genug liegt.

    Weicht die Summe der berechneten Beträge höchstens um
    :data:`ROUNDING_TOLERANCE` vom Rechnungsbetrag ab, ist das Rundung beim
    Versorger. Dann wird der *Rechnungsbetrag* im Verhältnis der berechneten
    Beträge verteilt, so dass die Summe exakt stimmt. Bei größerer Abweichung
    (oder ohne Rechnungsbetrag) wird jeder Betrag für sich kaufmännisch
    gerundet - die Abweichung bleibt dann sichtbar und muss geklärt werden.
    """
    rounded = [Money(int((amount * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))) for amount in exact_amounts]
    total = sum(exact_amounts, Decimal(0))
    if invoice_amount is None or invoice_amount.cents <= 0 or total <= 0:
        return rounded
    computed_total = Money(int((total * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
    if abs(computed_total - invoice_amount) > ROUNDING_TOLERANCE:
        return rounded
    return invoice_amount.allocate(list(exact_amounts))
