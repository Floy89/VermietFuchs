"""Tests für Money, DateRange und die deutsche Formatierung."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from models.exceptions import ValidationError
from models.value_objects import DateRange, Money
from utils.formatters import format_decimal, format_money, format_quantity


class TestMoney:
    def test_from_decimal_rounds_half_up(self) -> None:
        assert Money.from_decimal("10.005").cents == 1001
        assert Money.from_decimal("10.004").cents == 1000

    def test_from_decimal_rejects_garbage(self) -> None:
        with pytest.raises(ValidationError):
            Money.from_decimal("zwölf")

    def test_arithmetic(self) -> None:
        assert Money(150) + Money(250) == Money(400)
        assert Money(150) - Money(250) == Money(-100)
        assert abs(Money(-100)) == Money(100)

    def test_percentage(self) -> None:
        assert Money(10000).percentage(70) == Money(7000)
        assert Money(333).percentage(50) == Money(167)  # 166,5 -> aufgerundet

    def test_allocate_distributes_every_cent(self) -> None:
        parts = Money(10000).allocate([1, 1, 1])
        assert [part.cents for part in parts] == [3334, 3333, 3333]
        assert sum(part.cents for part in parts) == 10000

    def test_allocate_with_zero_weight(self) -> None:
        parts = Money(999).allocate([Decimal("0"), Decimal("2.5"), Decimal("7.5")])
        assert parts[0] == Money(0)
        assert sum(part.cents for part in parts) == 999

    def test_allocate_negative_amount(self) -> None:
        parts = Money(-100).allocate([1, 2])
        assert sum(part.cents for part in parts) == -100

    def test_allocate_rejects_zero_total(self) -> None:
        with pytest.raises(ValueError):
            Money(100).allocate([0, 0])


class TestDateRange:
    def test_days_are_inclusive(self) -> None:
        assert DateRange.for_year(2025).days == 365
        assert DateRange.for_year(2024).days == 366
        assert DateRange(date(2025, 1, 1), date(2025, 1, 1)).days == 1

    def test_end_before_start_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            DateRange(date(2025, 2, 1), date(2025, 1, 1))

    def test_intersection(self) -> None:
        year = DateRange.for_year(2025)
        contract = DateRange(date(2025, 7, 1), date(2027, 6, 30))
        overlap = year.intersection(contract)
        assert overlap == DateRange(date(2025, 7, 1), date(2025, 12, 31))
        assert overlap is not None and overlap.days == 184

    def test_no_intersection(self) -> None:
        assert DateRange.for_year(2024).intersection(DateRange.for_year(2025)) is None

    def test_encloses(self) -> None:
        year = DateRange.for_year(2025)
        assert year.encloses(DateRange(date(2025, 1, 1), date(2025, 6, 30)))
        assert not year.encloses(DateRange(date(2024, 12, 1), date(2025, 6, 30)))


class TestFormatters:
    def test_german_number_format(self) -> None:
        assert format_decimal(Decimal("1234567.891")) == "1.234.567,89"
        assert format_money(Money(-123456)) == "-1.234,56 €"

    def test_quantity_drops_trailing_zeros(self) -> None:
        assert format_quantity(Decimal("65.00")) == "65"
        assert format_quantity(Decimal("12.50")) == "12,5"
        assert format_quantity(Decimal("1200")) == "1.200"
