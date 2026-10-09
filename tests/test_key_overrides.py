"""Tests für abweichende Umlageschlüssel je Mieter.

Standard ist immer der Schlüssel der Rechnung. Nur wo beim Mieter für eine
Kostenart etwas anderes hinterlegt ist, wird sein Anteil danach berechnet -
die Anteile der übrigen Mieter ändern sich dadurch nicht.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app_context import AppContext
from models.apartment import Apartment
from models.cost import Cost
from models.enums import AllocationKey, MeterType
from models.exceptions import ValidationError
from models.meter_reading import MeterReading
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from utils.formatters import format_share_basis

YEAR = DateRange.for_year(2025)


class _House:
    """Drei Wohnungen: A 50 m²/1 Person, B 100 m²/4 Personen, C 50 m²/1 Person."""

    def __init__(self, context: AppContext) -> None:
        self.context = context
        house = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert house.id is not None
        self.id = house.id
        self.tenant_ids: dict[str, int] = {}
        self.flat_ids: dict[str, int] = {}
        for name, area, persons, start in (
            ("A", "50", 1, date(2020, 1, 1)),
            ("B", "100", 4, date(2020, 1, 1)),
            ("C", "50", 1, date(2020, 1, 1)),
        ):
            flat = context.apartments.save_apartment(Apartment(self.id, name, Decimal(area), persons))
            assert flat.id is not None
            tenant = context.tenants.save_tenant(Tenant(flat.id, f"Mieter {name}", start))
            assert tenant.id is not None
            self.flat_ids[name], self.tenant_ids[name] = flat.id, tenant.id

    def type_id(self, name: str) -> int:
        cost_type = self.context.cost_types.get_by_name(name)
        assert cost_type.id is not None
        return cost_type.id

    def add_cost(self, type_name: str, amount: str, key: AllocationKey) -> None:
        cost_type = self.context.cost_types.get_by_name(type_name)
        self.context.costs.save_cost(Cost(self.id, cost_type, Money.from_decimal(amount), YEAR, key))

    def totals(self) -> dict[str, Money]:
        result = self.context.billing.create_billing(self.id, YEAR)
        return {s.apartment.number: s.total_costs for s in result.statements}


@pytest.fixture()
def house(context: AppContext) -> _House:
    built = _House(context)
    built.add_cost("Müllbeseitigung", "600.00", AllocationKey.AREA)  # Standard: A 150, B 300, C 150
    return built


def _money(value: str) -> Money:
    return Money.from_decimal(value)


def test_standard_is_the_fallback(house: _House) -> None:
    """Ohne Abweichung gilt für alle der Schlüssel der Rechnung."""
    assert house.totals() == {"A": _money("150"), "B": _money("300"), "C": _money("150")}
    result = house.context.billing.create_billing(house.id, YEAR)
    assert result.deviation_amount.is_zero and not result.warnings


def test_override_changes_only_that_tenant(house: _House) -> None:
    """Mieter A zahlt Müll laut Vertrag nach Personen: 600 * 1/6 = 100 statt 150."""
    context = house.context
    context.key_overrides.set_override(house.tenant_ids["A"], house.type_id("Müllbeseitigung"), AllocationKey.PERSONS)

    result = context.billing.create_billing(house.id, YEAR)
    totals = {s.apartment.number: s.total_costs for s in result.statements}
    assert totals == {"A": _money("100"), "B": _money("300"), "C": _money("150")}  # B und C unverändert

    # Es wird weniger umgelegt als angefallen - die Differenz trägt der Eigentümer.
    assert result.deviation_amount == _money("50")
    assert result.total_allocated + result.vacancy_amount + result.deviation_amount == result.total_costs
    assert result.unallocated_amount == _money("50")
    assert not result.warnings

    share = next(s for s in result.statements if s.apartment.number == "A").shares[0]
    assert share.individual_key and "1 Pers. von 6 Pers." in format_share_basis(share)
    assert format_share_basis(share).endswith("lt. Mietvertrag")


def test_over_allocation_is_reported_not_silently_cut(house: _House) -> None:
    """Mieter B nach Personen: 600 * 4/6 = 400 statt 300 -> insgesamt 700 von 600 umgelegt."""
    context = house.context
    context.key_overrides.set_override(house.tenant_ids["B"], house.type_id("Müllbeseitigung"), AllocationKey.PERSONS)

    result = context.billing.create_billing(house.id, YEAR)
    assert {s.apartment.number: s.total_costs for s in result.statements} == {
        "A": _money("150"), "B": _money("400"), "C": _money("150"),
    }
    assert result.deviation_amount == _money("-100")
    assert result.allocated_for(result.costs[0]) == _money("700")
    assert len(result.warnings) == 1 and "mehr als der Betrag" in result.warnings[0]
    # Die Buchführung geht trotzdem auf: umgelegt + Leerstand + Abweichung = Rechnung.
    assert result.total_allocated + result.vacancy_amount + result.deviation_amount == result.total_costs


def test_override_applies_only_to_its_cost_type(house: _House) -> None:
    context = house.context
    house.add_cost("Grundsteuer", "400.00", AllocationKey.AREA)
    context.key_overrides.set_override(house.tenant_ids["A"], house.type_id("Müllbeseitigung"), AllocationKey.PERSONS)
    statement = next(s for s in context.billing.create_billing(house.id, YEAR).statements if s.apartment.number == "A")
    by_type = {share.cost.cost_type.name: share for share in statement.shares}
    assert by_type["Müllbeseitigung"].amount == _money("100") and by_type["Müllbeseitigung"].individual_key
    assert by_type["Grundsteuer"].amount == _money("100") and not by_type["Grundsteuer"].individual_key  # 400 * 50/200


def test_same_key_as_standard_and_removal(house: _House) -> None:
    context = house.context
    waste = house.type_id("Müllbeseitigung")
    # Abweichung, die dem Standard entspricht, ändert nichts.
    context.key_overrides.set_override(house.tenant_ids["A"], waste, AllocationKey.AREA)
    result = context.billing.create_billing(house.id, YEAR)
    assert house.totals()["A"] == _money("150") and result.deviation_amount.is_zero
    # Ändern (statt doppelt anlegen) und wieder entfernen.
    context.key_overrides.set_override(house.tenant_ids["A"], waste, AllocationKey.UNITS)
    overrides = context.key_overrides.list_for_tenant(house.tenant_ids["A"])
    assert [o.allocation_key for o in overrides] == [AllocationKey.UNITS]
    assert house.totals()["A"] == _money("200")  # 600 / 3 Wohneinheiten
    assert overrides[0].id is not None
    context.key_overrides.remove_override(overrides[0].id)
    assert house.totals()["A"] == _money("150")


def test_override_respects_move_in_date(context: AppContext) -> None:
    """Abweichender Schlüssel und Einzug mitten im Jahr: Anteil × Tage."""
    house = _House(context)
    house.add_cost("Müllbeseitigung", "730.00", AllocationKey.AREA)
    tenant = context.tenants.get_tenant(house.tenant_ids["A"])
    tenant.start_date = date(2025, 7, 1)  # 184 von 365 Tagen
    context.tenants.save_tenant(tenant)
    context.key_overrides.set_override(house.tenant_ids["A"], house.type_id("Müllbeseitigung"), AllocationKey.UNITS)
    # 730 / 3 Einheiten * 184 / 365 Tage
    assert house.totals()["A"] == _money("122.67")


def test_override_by_consumption_loads_meter_readings(context: AppContext) -> None:
    """Rechnung nach Wohnfläche, ein Mieter laut Vertrag nach Verbrauch."""
    house = _House(context)
    house.add_cost("Entwässerung", "1000.00", AllocationKey.AREA)
    for name, used in (("A", 10), ("B", 60), ("C", 30)):
        for value, day in ((0, date(2025, 1, 1)), (used, date(2025, 12, 31))):
            context.meter_readings.save_reading(
                MeterReading(house.flat_ids[name], MeterType.COLD_WATER, Decimal(value), day)
            )
    context.key_overrides.set_override(house.tenant_ids["A"], house.type_id("Entwässerung"), AllocationKey.CONSUMPTION)
    totals = house.totals()
    assert totals == {"A": _money("100"), "B": _money("500"), "C": _money("250")}  # A: 10 von 100 m³


def test_rules_for_overrides(house: _House) -> None:
    context = house.context
    tenant = house.tenant_ids["A"]
    with pytest.raises(ValidationError, match="gesetzlich vorgegeben"):
        context.key_overrides.set_override(tenant, house.type_id("Heizung"), AllocationKey.AREA)
    with pytest.raises(ValidationError, match="nur diese Schlüssel"):
        # Müll hat keinen Zähler -> "Verbrauch" ist nicht möglich.
        context.key_overrides.set_override(tenant, house.type_id("Müllbeseitigung"), AllocationKey.CONSUMPTION)
    with pytest.raises(ValidationError):
        context.key_overrides.set_override(tenant, house.type_id("Müllbeseitigung"), AllocationKey.DIRECT)
    assert all(t.override_keys() for t in context.key_overrides.overridable_types())
    assert "Heizung" not in [t.name for t in context.key_overrides.overridable_types()]


def test_heating_ignores_overrides_of_other_keys(house: _House) -> None:
    """Direkt zugeordnete Rechnungen bleiben von Abweichungen unberührt."""
    context = house.context
    a, b = house.tenant_ids["A"], house.tenant_ids["B"]
    context.costs.save_cost(
        Cost(
            house.id, context.cost_types.get_by_name("Rauchwarnmelder-Wartung"), _money("90"), YEAR,
            AllocationKey.DIRECT, direct_shares={a: _money("30"), b: _money("60")},
        )
    )
    context.key_overrides.set_override(a, house.type_id("Rauchwarnmelder-Wartung"), AllocationKey.AREA)
    statement = next(s for s in context.billing.create_billing(house.id, YEAR).statements if s.apartment.number == "A")
    direct = next(s for s in statement.shares if s.cost.allocation_key is AllocationKey.DIRECT)
    assert direct.amount == _money("30") and not direct.individual_key
