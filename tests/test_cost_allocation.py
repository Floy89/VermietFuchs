"""Tests für die Kostenverteilung (reine Rechenlogik, ohne Datenbank)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from models.apartment import Apartment
from models.cost import Cost
from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from models.exceptions import ValidationError
from models.meter_reading import MeterReading, calculate_consumption
from models.prepayment_rate import PrepaymentRate, expected_prepayment, rate_on
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from services.cost_allocation_service import CostAllocationService

YEAR = DateRange.for_year(2025)

# Kostenarten, wie sie der Katalog liefert (hier ohne Datenbank nachgebaut).
OTHER = CostType("Sonstige Betriebskosten", id=1)
WASTE = CostType("Müllbeseitigung", AllocationKey.PERSONS, id=2)
COLD_WATER = CostType("Wasserversorgung", AllocationKey.CONSUMPTION, MeterType.COLD_WATER, id=3)
HEATING = CostType("Heizung", AllocationKey.CONSUMPTION_AREA, MeterType.HEATING, heating_rule=True, id=4)


def _apartment(apartment_id: int, area: str, persons: int) -> Apartment:
    return Apartment(property_id=1, number=f"W{apartment_id}", area_sqm=Decimal(area), person_count=persons, id=apartment_id)


def _tenant(tenant_id: int, apartment_id: int, start: date, end: date | None = None) -> Tenant:
    return Tenant(apartment_id=apartment_id, name=f"Mieter {tenant_id}", start_date=start, end_date=end, id=tenant_id)


def _cost(cost_type: CostType, amount: str, key: AllocationKey, share: int = 70, **extra: object) -> Cost:
    return Cost(1, cost_type, Money.from_decimal(amount), YEAR, key, share, id=1, **extra)  # type: ignore[arg-type]


@pytest.fixture()
def apartments() -> list[Apartment]:
    return [_apartment(1, "60", 2), _apartment(2, "40", 1)]


@pytest.fixture()
def full_year_tenants() -> list[Tenant]:
    return [_tenant(1, 1, date(2020, 1, 1)), _tenant(2, 2, date(2020, 1, 1))]


def _total(result_shares: list) -> Money:
    return sum((share.amount for share in result_shares), Money.zero())


def test_area_key(apartments: list[Apartment], full_year_tenants: list[Tenant]) -> None:
    cost = _cost(OTHER, "1000.00", AllocationKey.AREA)
    result = CostAllocationService().allocate(cost, apartments, full_year_tenants, {})
    assert _total(result.shares_by_tenant[1]) == Money(60000)
    assert _total(result.shares_by_tenant[2]) == Money(40000)
    assert result.vacancy_amount.is_zero


def test_person_key(apartments: list[Apartment], full_year_tenants: list[Tenant]) -> None:
    cost = _cost(WASTE, "300.00", AllocationKey.PERSONS)
    result = CostAllocationService().allocate(cost, apartments, full_year_tenants, {})
    assert _total(result.shares_by_tenant[1]) == Money(20000)
    assert _total(result.shares_by_tenant[2]) == Money(10000)


def test_units_key_splits_equally(apartments: list[Apartment], full_year_tenants: list[Tenant]) -> None:
    """Schlüssel "Wohneinheiten": gleiche Anteile, unabhängig von Fläche und Personen."""
    cost = _cost(OTHER, "153.00", AllocationKey.UNITS)
    result = CostAllocationService().allocate(cost, apartments, full_year_tenants, {})
    assert _total(result.shares_by_tenant[1]) == Money(7650)
    assert _total(result.shares_by_tenant[2]) == Money(7650)


def test_direct_key_takes_given_amounts(apartments: list[Apartment]) -> None:
    """Direkte Zuordnung: Beträge werden übernommen, der Rest bleibt beim Eigentümer."""
    tenants = [_tenant(1, 1, date(2025, 3, 14)), _tenant(2, 2, date(2020, 1, 1))]
    cost = _cost(
        HEATING,
        "4484.66",
        AllocationKey.DIRECT,
        direct_shares={1: Money.from_decimal("968.38"), 2: Money.from_decimal("1189.14")},
    )
    cost.validate()
    result = CostAllocationService().allocate(cost, apartments, tenants, {})
    # Keine Kürzung nach Tagen - der Messdienst hat den Einzug bereits berücksichtigt.
    assert _total(result.shares_by_tenant[1]) == Money.from_decimal("968.38")
    assert _total(result.shares_by_tenant[2]) == Money.from_decimal("1189.14")
    assert result.vacancy_amount == Money.from_decimal("2327.14")
    assert result.shares_by_tenant[1][0].note


def test_direct_shares_must_not_exceed_amount() -> None:
    cost = _cost(OTHER, "100.00", AllocationKey.DIRECT, direct_shares={1: Money(6000), 2: Money(5000)})
    with pytest.raises(ValidationError):
        cost.validate()
    with pytest.raises(ValidationError):
        _cost(OTHER, "100.00", AllocationKey.DIRECT).validate()  # gar kein Betrag eingetragen


def test_heating_mixed_key(apartments: list[Apartment], full_year_tenants: list[Tenant]) -> None:
    """70 % nach Verbrauch (1:3), 30 % nach Fläche (60:40)."""
    cost = _cost(HEATING, "1000.00", AllocationKey.CONSUMPTION_AREA, 70)
    consumption = {1: Decimal("1000"), 2: Decimal("3000")}
    result = CostAllocationService().allocate(cost, apartments, full_year_tenants, consumption)
    # Wohnung 1: 700 * 1/4 + 300 * 0,6 = 175 + 180 = 355
    assert _total(result.shares_by_tenant[1]) == Money(35500)
    # Wohnung 2: 700 * 3/4 + 300 * 0,4 = 525 + 120 = 645
    assert _total(result.shares_by_tenant[2]) == Money(64500)
    assert len(result.shares_by_tenant[1]) == 2  # zwei Bestandteile
    assert not result.warnings


def test_tenant_change_and_vacancy(apartments: list[Apartment]) -> None:
    """Mieterwechsel: Auszug 30.06., Einzug 01.08. - der Juli ist Leerstand."""
    tenants = [
        _tenant(1, 1, date(2020, 1, 1), date(2025, 6, 30)),  # 181 Tage
        _tenant(2, 1, date(2025, 8, 1)),  # 153 Tage
        _tenant(3, 2, date(2020, 1, 1)),
    ]
    cost = _cost(OTHER, "3650.00", AllocationKey.AREA)
    result = CostAllocationService().allocate(cost, apartments, tenants, {})

    # Wohnung 1 trägt 60 % = 2190,00 € = 6,00 € je Tag.
    assert _total(result.shares_by_tenant[1]) == Money(108600)
    assert _total(result.shares_by_tenant[2]) == Money(91800)
    assert result.vacancy_amount == Money(18600)  # 31 Tage Juli
    assert result.allocated_amount + result.vacancy_amount == cost.amount


def test_missing_consumption_falls_back_to_area(apartments: list[Apartment], full_year_tenants: list[Tenant]) -> None:
    cost = _cost(COLD_WATER, "500.00", AllocationKey.CONSUMPTION)
    result = CostAllocationService().allocate(cost, apartments, full_year_tenants, {})
    assert _total(result.shares_by_tenant[1]) == Money(30000)
    assert len(result.warnings) == 1


def test_sum_is_always_exact(apartments: list[Apartment]) -> None:
    """Auch bei krummen Beträgen und Zeitanteilen geht kein Cent verloren."""
    tenants = [
        _tenant(1, 1, date(2025, 2, 17), date(2025, 9, 3)),
        _tenant(2, 2, date(2025, 5, 11)),
    ]
    odd_apartments = [_apartment(1, "61.37", 3), _apartment(2, "47.91", 2)]
    for amount in ("0.01", "999.99", "1234.57", "77777.77"):
        cost = _cost(HEATING, amount, AllocationKey.CONSUMPTION_AREA, 55)
        result = CostAllocationService().allocate(
            cost, odd_apartments, tenants, {1: Decimal("1234.5"), 2: Decimal("987.25")}
        )
        assert result.allocated_amount + result.vacancy_amount == cost.amount


class TestCostRules:
    def test_heating_rejects_plain_keys(self) -> None:
        with pytest.raises(ValidationError):
            _cost(HEATING, "100.00", AllocationKey.PERSONS).validate()

    def test_heating_share_must_be_50_to_70(self) -> None:
        with pytest.raises(ValidationError):
            _cost(HEATING, "100.00", AllocationKey.CONSUMPTION_AREA, 80).validate()
        _cost(HEATING, "100.00", AllocationKey.CONSUMPTION_AREA, 50).validate()

    def test_consumption_needs_a_meter(self) -> None:
        assert AllocationKey.CONSUMPTION not in OTHER.allowed_keys()
        assert COLD_WATER.allowed_keys()[0] is AllocationKey.CONSUMPTION
        with pytest.raises(ValidationError):
            _cost(OTHER, "100.00", AllocationKey.CONSUMPTION).validate()

    def test_custom_type_default_key_must_be_possible(self) -> None:
        with pytest.raises(ValidationError):
            CostType("Eigene", AllocationKey.CONSUMPTION).validate()  # kein Zähler gewählt
        CostType("Eigene", AllocationKey.CONSUMPTION, MeterType.ELECTRICITY).validate()


class TestProration:
    """Rechnungen, die über den Abrechnungszeitraum hinausragen."""

    def test_invoice_across_two_years(self) -> None:
        # Stromrechnung 01.06.2023–31.05.2024 (366 Tage), Abrechnungsjahr 2024.
        cost = Cost(
            1, OTHER, Money.from_decimal("261.09"),
            DateRange(date(2023, 6, 1), date(2024, 5, 31)), AllocationKey.AREA,
        )
        part = cost.prorate(DateRange.for_year(2024))
        assert part is not None and part.is_prorated
        assert part.period == DateRange(date(2024, 1, 1), date(2024, 5, 31))  # 152 Tage
        assert part.amount == Money.from_decimal("108.43")  # 261,09 / 366 * 152
        assert part.full_amount == cost.amount

    def test_inside_and_outside(self) -> None:
        cost = _cost(OTHER, "100.00", AllocationKey.AREA)
        assert cost.prorate(YEAR) is cost
        assert cost.prorate(DateRange.for_year(2023)) is None


class TestExpectedPrepayment:
    """Vorauszahlungen aus den Raten: volle Monate voll, Teilmonate nach Tagen."""

    @staticmethod
    def _rate(amount: str, start: date, end: date | None = None) -> PrepaymentRate:
        return PrepaymentRate(1, Money.from_decimal(amount), start, end)

    def test_full_year_is_exactly_twelve_rates(self) -> None:
        rates = [self._rate("300.00", date(2020, 1, 1))]
        assert expected_prepayment(rates, DateRange.for_year(2024)) == Money.from_decimal("3600.00")
        assert expected_prepayment(rates, DateRange.for_year(2025)) == Money.from_decimal("3600.00")

    def test_full_months_count_fully(self) -> None:
        """April bis Dezember = genau 9 Raten, obwohl die Monate verschieden lang sind."""
        rates = [self._rate("330.00", date(2024, 4, 1))]
        assert expected_prepayment(rates, DateRange.for_year(2024)) == Money.from_decimal("2970.00")

    def test_partial_month_is_prorated_by_its_days(self) -> None:
        rates = [self._rate("330.00", date(2024, 3, 14))]
        occupancy = DateRange(date(2024, 3, 14), date(2024, 12, 31))
        # März: 18 von 31 Tagen = 191,61; danach 9 volle Monate = 2.970,00
        assert expected_prepayment(rates, occupancy) == Money.from_decimal("3161.61")

    def test_move_out_on_the_first_of_a_month(self) -> None:
        """Auszug am 01.10.: neun volle Monate plus 1/31 des Oktobers."""
        rates = [self._rate("300.00", date(2024, 1, 1))]
        occupancy = DateRange(date(2024, 1, 1), date(2024, 10, 1))
        assert expected_prepayment(rates, occupancy) == Money.from_decimal("2709.68")

    def test_increase_mid_year(self) -> None:
        """Erhöhung zum 01.07.: sechs alte und sechs neue Raten."""
        rates = [
            self._rate("150.00", date(2022, 4, 1), date(2025, 6, 30)),
            self._rate("165.00", date(2025, 7, 1)),
        ]
        assert expected_prepayment(rates, YEAR) == Money.from_decimal("1890.00")
        assert rate_on(rates, date(2025, 6, 30)) == Money.from_decimal("150.00")
        assert rate_on(rates, date(2025, 7, 1)) == Money.from_decimal("165.00")

    def test_gap_without_rate_counts_nothing(self) -> None:
        rates = [self._rate("100.00", date(2025, 1, 1), date(2025, 1, 31))]
        assert expected_prepayment(rates, YEAR) == Money.from_decimal("100.00")
        assert rate_on(rates, date(2025, 2, 1)).is_zero
        assert expected_prepayment([], YEAR).is_zero


class TestConsumption:
    @staticmethod
    def _reading(value: str, day: date) -> MeterReading:
        return MeterReading(1, MeterType.HEATING, Decimal(value), day)

    def test_start_and_end_reading(self) -> None:
        readings = [self._reading("100", date(2025, 1, 2)), self._reading("450", date(2025, 12, 30))]
        assert calculate_consumption(readings, YEAR) == Decimal("350")

    def test_reading_shortly_after_period_end_counts(self) -> None:
        readings = [self._reading("100", date(2024, 12, 31)), self._reading("480", date(2026, 1, 3))]
        assert calculate_consumption(readings, YEAR) == Decimal("380")

    def test_single_reading_is_not_enough(self) -> None:
        assert calculate_consumption([self._reading("100", date(2025, 1, 2))], YEAR) is None

    def test_decreasing_meter_is_rejected(self) -> None:
        readings = [self._reading("500", date(2025, 1, 2)), self._reading("100", date(2025, 12, 30))]
        assert calculate_consumption(readings, YEAR) is None
