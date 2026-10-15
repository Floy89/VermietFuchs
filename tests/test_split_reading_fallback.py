"""Fehlen für einen Split die Zählerstände, gilt der Verbrauch des übergeordneten Zeitraums.

Beispiel wie bei Versorgern üblich: Die Wasserrechnung 14.09.2024–12.09.2025
hat Splits zu den Ableseterminen des Versorgers. Der Vermieter liest seine
Wohnungszähler aber zu anderen Terminen ab (31.12. und 31.05.) - für den Split
31.05.–12.09.2025 gibt es also keinen Endstand. Statt nach Wohnfläche zu
verteilen, wird der Split in den Abrechnungszeitraum zusammengezogen: Dessen
Verbrauch je Wohnung bestimmt die Anteile.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app_context import AppContext
from models.apartment import Apartment
from models.cost import Cost, InvoiceSplit
from models.enums import AllocationKey, MeterType
from models.meter_reading import MeterReading
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money

YEAR = DateRange(date(2025, 1, 1), date(2025, 12, 31))
INVOICE = DateRange(date(2024, 9, 14), date(2025, 9, 12))
SPLITS = [
    InvoiceSplit(DateRange(date(2024, 9, 14), date(2024, 12, 31)), Money.from_decimal("225.15")),
    InvoiceSplit(DateRange(date(2025, 1, 1), date(2025, 5, 30)), Money.from_decimal("317.64")),
    InvoiceSplit(DateRange(date(2025, 5, 31), date(2025, 9, 12)), Money.from_decimal("221.07")),
]
#: Zählerstände je Wohnung: 31.12.2024, 31.05.2025, 31.12.2025 (Jahresverbrauch 54 / 54 / 40 m³).
READINGS = {"EG": ("43", "65", "97"), "1OG": ("51", "76", "105"), "DG": ("91", "131", "131")}


def _house(context: AppContext, readings: dict[str, tuple[str, ...]] = READINGS) -> int:
    house = context.properties.save_property(Property(name="Zählerhaus", address="Weg 7, 12345 Ort"))
    assert house.id is not None
    for number, values in readings.items():
        flat = context.apartments.save_apartment(Apartment(house.id, number, Decimal("70"), 2))
        assert flat.id is not None
        context.tenants.save_tenant(Tenant(apartment_id=flat.id, name=f"Mieter {number}", start_date=date(2020, 1, 1)))
        for value, day in zip(values, (date(2024, 12, 31), date(2025, 5, 31), date(2025, 12, 31))):
            context.meter_readings.save_reading(MeterReading(flat.id, MeterType.COLD_WATER, Decimal(value), day))
    context.costs.save_cost(
        Cost(house.id, context.cost_types.get_by_name("Wasserversorgung"), Money.from_decimal("763.86"), INVOICE,
             AllocationKey.CONSUMPTION, description="Wasserwerk", splits=SPLITS)
    )
    return house.id


def _summer_shares(context: AppContext, house_id: int) -> dict[str, Money]:
    result = context.billing.create_billing(house_id, YEAR)
    return {
        statement.apartment.number: next(s.amount for s in statement.shares if s.cost.period.start == date(2025, 5, 31))
        for statement in result.statements
    }


class TestSplitWithoutReadings:
    def test_split_uses_consumption_of_the_billing_period(self, context: AppContext) -> None:
        house_id = _house(context)
        result = context.billing.create_billing(house_id, YEAR)

        assert any(
            "31.05.2025 – 12.09.2025 fehlen Kaltwasser-Zählerstände" in w and "01.01.2025 – 31.12.2025" in w
            for w in result.warnings
        ), result.warnings
        assert not any("nach Wohnfläche" in w for w in result.warnings)
        # 221,07 € im Verhältnis des Jahresverbrauchs 54 : 54 : 40.
        assert _summer_shares(context, house_id) == {
            "EG": Money.from_decimal("80.66"), "1OG": Money.from_decimal("80.66"), "DG": Money.from_decimal("59.75"),
        }

    def test_split_with_readings_is_unchanged(self, context: AppContext) -> None:
        house_id = _house(context)
        result = context.billing.create_billing(house_id, YEAR)
        spring = {
            s.apartment.number: next(x.amount for x in s.shares if x.cost.period.start == date(2025, 1, 1))
            for s in result.statements
        }
        # 01.01.–30.05.: eigene Ablesungen 31.12.2024 / 31.05.2025 -> 22 : 25 : 40 m³.
        assert spring == {
            "EG": Money.from_decimal("80.32"), "1OG": Money.from_decimal("91.28"), "DG": Money.from_decimal("146.04"),
        }

    def test_without_any_enclosing_readings_area_is_the_fallback(self, context: AppContext) -> None:
        # Nur Ablesungen bis 31.05.2025: Kein Zeitraum umschließt den Split 31.05.–12.09.
        house_id = _house(context, {name: values[:2] for name, values in READINGS.items()})
        result = context.billing.create_billing(house_id, YEAR)

        assert any("ersatzweise wurde nach Wohnfläche verteilt" in w for w in result.warnings), result.warnings
        # Gleiche Wohnflächen: 221,07 € / 3.
        assert set(_summer_shares(context, house_id).values()) == {Money.from_decimal("73.69")}
