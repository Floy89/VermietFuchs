"""Wasserregel: die Wasserrechnungen eines Abrechnungszeitraums gemeinsam aufteilen.

Der Hauptwasserzähler misst mehr als die Wohnungszähler zusammen. Ein Teil der
Differenz ist Kaltwasser, das in die Warmwasserbereitung fließt (gemessen über
die Warmwasserzähler der Wohnungen), der Rest geht in Heizungsnachspeisung,
Allgemein-Zapfstellen und Verluste (nicht gemessen). Für Rechnungen, deren
Verbrauchsdifferenz nach der *Wasserregel* verteilt wird, gilt deshalb:

1. **Grundgebühr** (Grundpreis aus den Rechengrundlagen der Splits) - nach Wohnfläche
   (gesetzlicher Standard) oder, wenn so vereinbart, zu gleichen Teilen je Wohnung.
2. **Mengenpreis** - der Rest der Rechnung. Er bezieht sich auf den Gesamtverbrauch
   laut Rechnung G und wird nach Mengen aufgeteilt:

   * Wohnungswasser M (Summe der Kaltwasserzähler) nach gemessenem Verbrauch,
   * Warmwasser-Kaltwasseranteil H (Summe der Warmwasserzähler) als eigene
     Position, verteilt nach Warmwasserverbrauch,
   * Rest R = G − M − H nach Wohnfläche. Er nimmt auch die Rundungscents auf, so dass
     Grundgebühr + Wohnungswasser + Warmwasser + Rest genau den Rechnungsbetrag ergeben.

   Ist G nicht größer als M, wird der ganze Mengenpreis nach gemessenem Verbrauch verteilt.

Die Rechnungen werden über den ganzen Abrechnungszeitraum zusammengefasst, nicht je
Split: Die Wohnungszähler werden zu anderen Terminen abgelesen als der Hauptzähler, und
die Warmwasserzähler nur einmal im Jahr (ISTA). Je Abschnitt ließe sich die Differenz
nicht bestimmen.

Das Modul rechnet nur; Zählerstände lädt der
:class:`~services.billing_service.BillingService`.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import ROUND_HALF_UP, Decimal
from typing import Final, Mapping, Sequence

from models.cost import Cost
from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from models.value_objects import DateRange, Money
from services.cost_allocation_service import AreaWeight, ConsumptionWeight, CostComponent, UnitWeight

#: Kennzeichen in der Bemerkung eines Zählerstands, der noch nicht bestätigt ist.
PLACEHOLDER_MARK: Final[str] = "Platzhalter"

#: Kostenart der Position "Kaltwasser für die Warmwasserbereitung". Sie gehört zu den
#: Kosten der Warmwasserversorgung (§ 2 Nr. 5 BetrKV, § 8 HeizkostenV), nicht zu den
#: Wasserkosten. Nur für die Abrechnung, nicht gespeichert.
HOT_WATER_COST_TYPE: Final[CostType] = CostType(
    "Warmwasser – Kaltwasseranteil",
    default_key=AllocationKey.CONSUMPTION,
    meter_type=MeterType.HOT_WATER,
    heating_rule=True,
    legal_basis="§ 2 Nr. 5 BetrKV, § 8 HeizkostenV",
    sort_order=21,  # direkt nach der Wasserversorgung (20)
)

BASE_LABEL_AREA: Final[str] = "Grundgebühr nach Wohnfläche"
BASE_LABEL_UNITS: Final[str] = "Grundgebühr nach Wohneinheiten"
MEASURED_LABEL: Final[str] = "Wohnungswasser laut Zählern"
REST_LABEL: Final[str] = "Restmenge nach Wohnfläche"
HOT_WATER_LABEL: Final[str] = "Warmwasserverbrauch"

_CENT: Final[Decimal] = Decimal("0.01")


@dataclass(frozen=True)
class WaterBreakdown:
    """Aufteilung der Wasserrechnungen eines Zeitraums in Grundgebühr und Mengen.

    Attributes:
        period: Zeitraum, den die Rechnungsausschnitte zusammen abdecken.
        amount: Summe der Ausschnitte.
        base_amount: Grundgebühr brutto.
        measured_amount / hot_water_amount / rest_amount: Mengenpreis-Anteile von
            Wohnungswasser, Warmwasser-Kaltwasser und Restmenge.
        total_quantity: Gesamtverbrauch laut Rechnungen (G).
        measured_quantity: Summe der Wohnungszähler (M).
        hot_water_quantity: Davon getrennt ausgewiesenes Warmwasser (H).
        rest_quantity: Restmenge (G − M − H, nie negativ).
    """

    period: DateRange
    amount: Money
    base_amount: Money
    measured_amount: Money
    hot_water_amount: Money
    rest_amount: Money
    total_quantity: Decimal
    measured_quantity: Decimal
    hot_water_quantity: Decimal
    rest_quantity: Decimal
    unit: str = "m³"

    @property
    def quantity_amount(self) -> Money:
        """Mengenpreis-Anteil der Rechnungen (alles außer der Grundgebühr)."""
        return self.amount - self.base_amount

    @property
    def price_per_unit(self) -> Decimal:
        """Mengenpreis brutto je m³ (Mengenpreis-Anteil / Gesamtverbrauch)."""
        if self.total_quantity <= 0:
            return Decimal(0)
        return Decimal(self.quantity_amount.cents) / 100 / self.total_quantity


@dataclass(frozen=True)
class WaterPosition:
    """Eine Abrechnungsposition der Wasserregel samt Bestandteilen und Verbrauchswerten."""

    cost: Cost
    components: list[CostComponent]
    consumption: Mapping[int, Decimal]


def base_fee(piece: Cost) -> Decimal | None:
    """Grundgebühr brutto (ungerundet) eines Rechnungsausschnitts.

    Grundlage ist der Grundpreis aus den Rechengrundlagen des Splits - je Tag
    starr oder kalendergenau wie beim Versorger, netto auf Cent gerundet und mit
    der Umsatzsteuer des Splits. ``None``, wenn kein Grundpreis hinterlegt ist.
    """
    calculation = piece.calculation
    if calculation is None and len(piece.splits) == 1:
        calculation = piece.splits[0].calculation  # ganze Rechnung mit einem einzigen Split
    if calculation is None or not calculation.base_price:
        return None
    net = (calculation.base_price * calculation.base_fraction(piece.period)).quantize(_CENT, rounding=ROUND_HALF_UP)
    return net * (1 + (calculation.vat_percent or Decimal(0)) / 100)


def water_breakdown(
    pieces: Sequence[Cost],
    measured: Decimal,
    hot_water: Decimal | None,
    warnings: list[str],
) -> WaterBreakdown:
    """Teilt die Rechnungsausschnitte nach der Wasserregel auf.

    Args:
        pieces: Ausschnitte der Wasserrechnungen im Abrechnungszeitraum (mit Gesamtverbrauch).
        measured: Summe der Wohnungszähler (Kaltwasser) im selben Zeitraum.
        hot_water: Summe der Warmwasserzähler oder ``None``, wenn sie nicht vollständig ist.
        warnings: Hier landen Hinweise (fehlender Grundpreis, unplausible Mengen).
    """
    title = pieces[0].cost_type.label
    period = DateRange(min(p.period.start for p in pieces), max(p.period.end for p in pieces))
    amount = sum((piece.amount for piece in pieces), Money.zero())
    total = sum((piece.consumption or Decimal(0) for piece in pieces), Decimal(0))

    exact_base = Decimal(0)
    without_base: list[str] = []
    for piece in pieces:
        fee = base_fee(piece)
        if fee is None:
            without_base.append(_period_text(piece.period))
        else:
            exact_base += fee
    if without_base:
        warnings.append(
            f"'{title}': Für {', '.join(without_base)} ist kein Grundpreis hinterlegt - der Betrag zählt dort "
            "ganz zum Mengenpreis. Bitte den Grundpreis in den Splits der Rechnung eintragen."
        )
    base = min(Money(int((exact_base * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))), amount)
    work = amount - base

    hot_quantity = Decimal(0)
    if total <= measured:
        if total < measured:
            warnings.append(
                f"'{title}': Der Gesamtverbrauch laut Rechnung ({_quantity(total)} m³) ist kleiner als die Summe "
                f"der Wohnungszähler ({_quantity(measured)} m³). Verteilt wurde nach gemessenem Verbrauch - "
                "bitte Rechnung und Zählerstände prüfen."
            )
        if hot_water:
            warnings.append(
                f"'{title}': Neben den Wohnungszählern bleibt keine Menge für das Warmwasser - "
                "es wurde kein Warmwasser-Kaltwasseranteil ausgewiesen."
            )
        return WaterBreakdown(
            period, amount, base, work, Money.zero(), Money.zero(), total, measured, Decimal(0), Decimal(0)
        )

    difference = total - measured
    if hot_water:
        hot_quantity = min(hot_water, difference)
        if hot_water > difference:
            warnings.append(
                f"'{title}': Die Warmwasserzähler ({_quantity(hot_water)} m³) messen mehr als die Differenz "
                f"zwischen Rechnung und Wohnungszählern ({_quantity(difference)} m³). Als Warmwasser-"
                f"Kaltwasseranteil wurden {_quantity(hot_quantity)} m³ angesetzt - bitte Zählerstände prüfen."
            )
    rest_quantity = difference - hot_quantity
    measured_amount = _share(work, measured, total)
    hot_amount = _share(work, hot_quantity, total)
    rest_amount = work - measured_amount - hot_amount  # nimmt die Rundungscents auf
    return WaterBreakdown(
        period, amount, base, measured_amount, hot_amount, rest_amount,
        total, measured, hot_quantity, rest_quantity,
    )


def water_positions(
    pieces: Sequence[Cost],
    breakdown: WaterBreakdown,
    cold_water: Mapping[int, Decimal],
    hot_water: Mapping[int, Decimal],
    hot_water_in_heating_costs: bool = False,
) -> list[WaterPosition]:
    """Die Abrechnungspositionen zur Aufteilung: Wasserversorgung und - falls vorhanden - Warmwasser.

    Die Wasserversorgung hat bis zu drei Bestandteile (Grundgebühr, Wohnungswasser,
    Restmenge); der Warmwasser-Kaltwasseranteil ist eine eigene Position mit der
    Kostenart :data:`HOT_WATER_COST_TYPE`.

    Args:
        hot_water_in_heating_costs: ``True``, wenn die Heizkostenrechnung (Messdienst)
            das Kaltwasser für die Warmwasserbereitung schon enthält. Dann gibt es keine
            eigene Warmwasser-Position - der Betrag steckt in der Heizkostenrechnung.
    """
    descriptions = list(dict.fromkeys(piece.description for piece in pieces if piece.description))
    water_amount = breakdown.amount - breakdown.hot_water_amount
    water = replace(
        pieces[0],
        amount=water_amount,
        period=breakdown.period,
        consumption=breakdown.total_quantity - breakdown.hot_water_quantity,
        description=", ".join(descriptions),
        direct_shares={},
        splits=[],
        co2_cost=None,
        co2_emission=None,
        co2_factor=None,
        id=None,
        full_amount=None,
        full_period=None,
        split_period=None,
        calculation=None,
    )
    unit = breakdown.unit
    components: list[CostComponent] = []
    if not breakdown.base_amount.is_zero:
        base_strategy = (
            UnitWeight(label=BASE_LABEL_UNITS)
            if pieces[0].difference_key is AllocationKey.WATER_RULES_UNITS
            else AreaWeight(label=BASE_LABEL_AREA)
        )
        components.append(CostComponent(breakdown.base_amount, base_strategy))
    components.append(
        CostComponent(
            breakdown.measured_amount,
            ConsumptionWeight(label=MEASURED_LABEL, unit=unit),
            quantity_total=breakdown.measured_quantity,
            quantity_unit=unit,
        )
    )
    if breakdown.rest_quantity > 0 or not breakdown.rest_amount.is_zero:
        components.append(
            CostComponent(
                breakdown.rest_amount,
                AreaWeight(label=REST_LABEL),
                quantity_total=breakdown.rest_quantity,
                quantity_unit=unit,
                is_difference=True,
            )
        )
    positions = [WaterPosition(water, components, cold_water)]

    if breakdown.hot_water_quantity > 0 and not hot_water_in_heating_costs:
        hot = replace(
            water,
            cost_type=HOT_WATER_COST_TYPE,
            amount=breakdown.hot_water_amount,
            consumption=breakdown.hot_water_quantity,
            difference_key=AllocationKey.CONSUMPTION,
        )
        component = CostComponent(breakdown.hot_water_amount, ConsumptionWeight(label=HOT_WATER_LABEL, unit=unit))
        positions.append(WaterPosition(hot, [component], hot_water))
    return positions


def _share(amount: Money, part: Decimal, total: Decimal) -> Money:
    exact = Decimal(amount.cents) * part / total
    return Money(int(exact.quantize(Decimal(1), rounding=ROUND_HALF_UP)))


def _quantity(value: Decimal) -> str:
    text = f"{value.quantize(Decimal('0.001'), rounding=ROUND_HALF_UP):f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _period_text(period: DateRange) -> str:
    return f"{period.start:%d.%m.%Y} – {period.end:%d.%m.%Y}"
