"""Tests für die Differenz zwischen Gesamtverbrauch laut Rechnung und Summe der Wohnungszähler.

Beispiel: Der Hauptzähler zeigt 200 m³ Wasser, die Wohnungszähler messen
zusammen nur 148 m³. Die Rechnung über 1.000 € bezieht sich auf 200 m³ - die
Frage ist, wer die 52 m³ Differenz trägt.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app_context import AppContext
from models.apartment import Apartment
from models.cost import Cost
from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from models.exceptions import ValidationError
from models.meter_reading import MeterReading
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from services.cost_allocation_service import AllocationResult, CostAllocationService, same_unit
from utils.formatters import format_share_basis, format_share_title

YEAR = DateRange.for_year(2025)
WATER = CostType("Wasserversorgung", AllocationKey.CONSUMPTION, MeterType.COLD_WATER, id=3)
HEATING = CostType("Heizung", AllocationKey.CONSUMPTION_AREA, MeterType.HEATING, heating_rule=True, id=4)

APARTMENTS = [
    Apartment(property_id=1, number="EG", area_sqm=Decimal("80"), person_count=3, id=1),
    Apartment(property_id=1, number="1. OG", area_sqm=Decimal("70"), person_count=2, id=2),
    Apartment(property_id=1, number="DG", area_sqm=Decimal("50"), person_count=1, id=3),
]
TENANTS = [
    Tenant(apartment_id=1, name="Anna", start_date=date(2020, 1, 1), id=1),
    Tenant(apartment_id=2, name="Ben", start_date=date(2020, 1, 1), id=2),
    Tenant(apartment_id=3, name="Cem", start_date=date(2020, 1, 1), id=3),
]
#: Wohnungszähler: 60 + 48 + 40 = 148 m³
MEASURED = {1: Decimal("60"), 2: Decimal("48"), 3: Decimal("40")}


def _water(difference_key: AllocationKey = AllocationKey.CONSUMPTION, total: str | None = "200", unit: str = "m³") -> Cost:
    return Cost(
        property_id=1,
        cost_type=WATER,
        amount=Money.from_decimal("1000.00"),
        period=YEAR,
        allocation_key=AllocationKey.CONSUMPTION,
        description="Stadtwerke",
        consumption=Decimal(total) if total else None,
        consumption_unit=unit if total else "",
        difference_key=difference_key,
        id=1,
    )


def _allocate(cost: Cost, tenants: list[Tenant] | None = None) -> AllocationResult:
    return CostAllocationService().allocate(cost, APARTMENTS, tenants or TENANTS, MEASURED)


def _amount(result: AllocationResult, tenant_id: int) -> Money:
    return sum((share.amount for share in result.shares_by_tenant[tenant_id]), Money.zero())


class TestDefaultLikeMeasured:
    def test_amounts_are_unchanged_but_actual_quantity_is_shown(self) -> None:
        with_total = _allocate(_water())
        without_total = _allocate(_water(total=None))

        # Die Differenz wird wie gemessen verteilt - am Geld ändert sich nichts.
        for tenant_id in (1, 2, 3):
            assert _amount(with_total, tenant_id) == _amount(without_total, tenant_id)
        assert _amount(with_total, 1) == Money.from_decimal("405.41")

        (share,) = with_total.shares_by_tenant[1]
        # 60 von 148 m³ gemessen entsprechen 81,081 von 200 m³ tatsächlich.
        assert share.quantity_total == Decimal("200")
        assert share.quantity is not None and round(share.quantity, 3) == Decimal("81.081")
        assert format_share_basis(share) == "60 m³ von 148 m³ · entspricht 81,081 m³ von 200 m³ Gesamtverbrauch lt. Rechnung"
        assert format_share_title(share) == "Wasserversorgung – Stadtwerke"

    def test_without_total_nothing_is_added(self) -> None:
        (share,) = _allocate(_water(total=None)).shares_by_tenant[1]
        assert share.quantity is None
        assert format_share_basis(share) == "60 m³ von 148 m³"

    def test_total_equal_to_measured_shows_no_difference(self) -> None:
        (share,) = _allocate(_water(total="148")).shares_by_tenant[1]
        assert share.quantity is None


class TestDifferenceByOwnKey:
    def test_difference_split_equally_per_apartment(self) -> None:
        result = _allocate(_water(AllocationKey.UNITS))

        # 148/200 des Betrags (740 €) nach Zählern, 52/200 (260 €) zu gleichen Teilen.
        measured, difference = result.shares_by_tenant[1]
        assert (measured.component_amount, measured.amount) == (Money.from_decimal("740.00"), Money.from_decimal("300.00"))
        assert (difference.component_amount, difference.amount) == (Money.from_decimal("260.00"), Money.from_decimal("86.67"))
        assert _amount(result, 1) == Money.from_decimal("386.67")
        assert _amount(result, 2) == Money.from_decimal("326.67")
        assert _amount(result, 3) == Money.from_decimal("286.66")
        # Kein Cent geht verloren.
        assert result.allocated_amount == Money.from_decimal("1000.00")
        assert result.vacancy_amount.is_zero and not result.warnings

    def test_statement_shows_both_keys_and_actual_consumption(self) -> None:
        measured, difference = _allocate(_water(AllocationKey.UNITS)).shares_by_tenant[1]

        assert format_share_title(measured) == "Wasserversorgung – Stadtwerke (Verbrauch laut Zählern)"
        assert format_share_basis(measured) == "60 m³ von 148 m³"
        assert format_share_title(difference) == (
            "Wasserversorgung – Stadtwerke (Differenz zum Gesamtverbrauch nach Wohneinheiten)"
        )
        # 52 m³ Differenz / 3 Wohnungen = 17,333 m³; zusammen mit den 60 m³ vom Zähler 77,333 m³.
        assert format_share_basis(difference) == (
            "1 Einh. von 3 Einh. · entspricht 17,333 m³ von 52 m³ Differenz · Verbrauch gesamt 77,333 m³"
        )

    def test_quantities_add_up_to_invoice_total(self) -> None:
        result = _allocate(_water(AllocationKey.UNITS))
        totals = [
            next(share.tenant_quantity for share in result.shares_by_tenant[tenant_id] if share.is_difference)
            for tenant_id in (1, 2, 3)
        ]
        assert all(total is not None for total in totals)
        assert sum(totals, Decimal(0)) == Decimal("200")  # type: ignore[arg-type]

    def test_difference_by_area_and_by_persons(self) -> None:
        by_area = _allocate(_water(AllocationKey.AREA))
        by_persons = _allocate(_water(AllocationKey.PERSONS))

        # 260 € Differenz: nach Fläche 80/200, nach Personen 3/6.
        assert by_area.shares_by_tenant[1][1].amount == Money.from_decimal("104.00")
        assert by_persons.shares_by_tenant[1][1].amount == Money.from_decimal("130.00")
        assert by_area.allocated_amount == by_persons.allocated_amount == Money.from_decimal("1000.00")
        assert "nach Wohnfläche" in format_share_title(by_area.shares_by_tenant[1][1])

    def test_tenant_change_and_vacancy(self) -> None:
        # Wohnung 3: Mieter zieht am 30.06. aus, danach Leerstand (181 von 365 Tagen bewohnt).
        tenants = [*TENANTS[:2], Tenant(apartment_id=3, name="Cem", start_date=date(2020, 1, 1), end_date=date(2025, 6, 30), id=3)]
        result = _allocate(_water(AllocationKey.UNITS), tenants)

        measured, difference = result.shares_by_tenant[3]
        assert measured.days == difference.days == 181
        assert result.allocated_amount + result.vacancy_amount == Money.from_decimal("1000.00")
        assert not result.vacancy_amount.is_zero
        # Auch die zugerechnete Menge ist zeitanteilig: (40 + 17,333) × 181/365
        assert difference.tenant_quantity is not None
        assert round(difference.tenant_quantity, 3) == Decimal("28.431")
        assert "181 von 365 Tagen" in format_share_basis(difference)


class TestEdgeCases:
    def test_total_below_measured_is_reported(self) -> None:
        result = _allocate(_water(AllocationKey.UNITS, total="120"))

        assert len(result.shares_by_tenant[1]) == 1  # gewöhnliche Verteilung nach Zählern
        assert _amount(result, 1) == Money.from_decimal("405.41")
        assert len(result.warnings) == 1 and "kleiner als die Summe der Wohnungszähler" in result.warnings[0]

    def test_different_unit_is_reported(self) -> None:
        result = _allocate(_water(AllocationKey.UNITS, unit="kWh"))

        assert len(result.shares_by_tenant[1]) == 1
        assert len(result.warnings) == 1 and "Differenz kann nicht ermittelt werden" in result.warnings[0]
        # Ohne eigenen Differenz-Schlüssel ist eine fremde Einheit kein Problem.
        assert not _allocate(_water(unit="kWh")).warnings

    def test_units_are_compared_generously(self) -> None:
        assert same_unit("m3", "m³") and same_unit(" CBM ", "m³") and same_unit("kWh", "kwh")
        assert not same_unit("kWh", "m³")
        assert len(_allocate(_water(AllocationKey.UNITS, unit="m3")).shares_by_tenant[1]) == 2

    def test_only_for_pure_consumption_key(self) -> None:
        heating = Cost(
            1, HEATING, Money.from_decimal("1000.00"), YEAR, AllocationKey.CONSUMPTION_AREA,
            consumption=Decimal("20000"), consumption_unit="kWh", difference_key=AllocationKey.UNITS, id=2,
        )
        heating.validate()
        # Beim Mischschlüssel wird die Einstellung zurückgesetzt.
        assert heating.difference_key is AllocationKey.CONSUMPTION and not heating.splits_difference

    def test_invalid_difference_key_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            _water(AllocationKey.DIRECT).validate()

    def test_allocation_label_names_the_difference_key(self) -> None:
        assert _water(AllocationKey.UNITS).allocation_label == "Verbrauch, Differenz nach Wohneinheiten"
        assert _water().allocation_label == "Verbrauch"
        # Ohne Gesamtverbrauch gibt es keine Differenz zu verteilen.
        assert _water(AllocationKey.UNITS, total=None).allocation_label == "Verbrauch"


class TestEndToEnd:
    def test_setting_is_stored_and_used_in_billing(self, context: AppContext) -> None:
        house = context.properties.save_property(Property(name="Wasserhaus", address="Am Brunnen 1, 12345 Quellstadt"))
        assert house.id is not None
        water = context.cost_types.get_by_name("Wasserversorgung")
        for number, area, used in (("EG", "80", "60"), ("1. OG", "70", "48"), ("DG", "50", "40")):
            flat = context.apartments.save_apartment(Apartment(house.id, number, Decimal(area), 2))
            assert flat.id is not None
            context.tenants.save_tenant(
                Tenant(apartment_id=flat.id, name=f"Mieter {number}", start_date=date(2020, 1, 1)),
                initial_prepayment=Money.from_decimal("30.00"),
            )
            for value, day in (("100", date(2025, 1, 1)), (str(100 + int(used)), date(2025, 12, 31))):
                context.meter_readings.save_reading(MeterReading(flat.id, MeterType.COLD_WATER, Decimal(value), day))

        saved = context.costs.save_cost(
            Cost(
                house.id, water, Money.from_decimal("1000.00"), YEAR, AllocationKey.CONSUMPTION,
                description="Stadtwerke", consumption=Decimal("200"), consumption_unit="m³",
                difference_key=AllocationKey.UNITS,
            )
        )
        assert saved.id is not None
        (stored,) = context.costs.list_costs(house.id)
        assert stored.difference_key is AllocationKey.UNITS

        result = context.billing.create_billing(house.id, YEAR)

        assert not result.warnings
        assert result.total_allocated == Money.from_decimal("1000.00")
        ground_floor = next(s for s in result.statements if s.apartment.number == "EG")
        # 260 € / 3 = 86,67 + 86,67 + 86,66: Der Rest-Cent geht an die zuerst sortierten Wohnungen.
        assert [share.amount for share in ground_floor.shares] == [Money.from_decimal("300.00"), Money.from_decimal("86.66")]
        assert ground_floor.shares[1].tenant_quantity is not None
        assert round(ground_floor.shares[1].tenant_quantity, 3) == Decimal("77.333")
