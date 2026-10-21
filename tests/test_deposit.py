"""Mietkaution: Verzinsung mit jährlicher Gutschrift und Rückzahlungsbetrag bei Auszug."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from database.migrations import MIGRATIONS, current_version, migrate
from models.apartment import Apartment
from models.deposit import InterestRate, deposit_statement, rate_on
from models.exceptions import ValidationError
from models.property import Property
from models.tenant import Tenant
from models.value_objects import Money

EUR = Money.from_decimal


class TestCalculation:
    def test_yearly_credit_with_compound_interest(self) -> None:
        # 3.000 € ab 01.03.2024: 0,5 %, ab 01.01.2025 1 %; Auszug 31.03.2026 -> Rückzahlung zum 01.04.2026.
        rates = [InterestRate(date(2024, 3, 1), Decimal("0.5")), InterestRate(date(2025, 1, 1), Decimal("1"))]
        statement = deposit_statement(EUR("3000"), date(2024, 3, 1), rates, date(2026, 4, 1))

        assert [year.interest for year in statement.years] == [EUR("12.54"), EUR("30.13"), EUR("7.50")]
        # 2024: 3.000 × 0,5 % × 306/366; 2025: 3.012,54 × 1 %; 2026: 3.042,67 × 1 % × 90/365
        assert [year.start_balance for year in statement.years] == [EUR("3000"), EUR("3012.54"), EUR("3042.67")]
        assert statement.years[0].period.days == 306 and statement.years[2].period.days == 90
        assert statement.interest == EUR("50.17")
        assert statement.payout == EUR("3050.17")

    def test_rate_change_within_a_year(self) -> None:
        rates = [InterestRate(date(2025, 1, 1), Decimal("1")), InterestRate(date(2025, 7, 1), Decimal("2"))]
        (year,) = deposit_statement(EUR("1000"), date(2025, 1, 1), rates, date(2026, 1, 1)).years
        # 1.000 × 1 % × 181/365 + 1.000 × 2 % × 184/365 = 4,96 + 10,08
        assert year.interest == EUR("15.04")
        assert year.rates == "1 % / ab 01.07.: 2 %"

    def test_without_rate_there_is_no_interest(self) -> None:
        statement = deposit_statement(EUR("900"), date(2025, 1, 1), [], date(2026, 1, 1))
        assert statement.interest.is_zero and statement.payout == EUR("900")
        assert rate_on([], date(2025, 6, 1)) == Decimal(0)

    def test_payout_before_first_interest_day(self) -> None:
        statement = deposit_statement(EUR("900"), date(2025, 1, 1), [], date(2025, 1, 1))
        assert statement.years == [] and statement.payout == EUR("900")


class TestTenant:
    def test_defaults_to_contract_start_and_day_after_contract_end(self) -> None:
        tenant = Tenant(apartment_id=1, name="Anna", start_date=date(2024, 3, 1), end_date=date(2026, 3, 31),
                        deposit_amount=EUR("3000"),
                        deposit_rates=[InterestRate(date(2024, 3, 1), Decimal("0.5")),
                                       InterestRate(date(2025, 1, 1), Decimal("1"))])
        statement = tenant.deposit_statement()
        assert (statement.paid_on, statement.until) == (date(2024, 3, 1), date(2026, 4, 1))
        assert statement.payout == EUR("3050.17")

    def test_validation(self) -> None:
        base = dict(apartment_id=1, name="Anna", start_date=date(2024, 3, 1), deposit_amount=EUR("3000"))
        with pytest.raises(ValidationError, match="Zinssatz"):
            Tenant(**base, deposit_rates=[InterestRate(date(2024, 3, 1), Decimal("25"))]).validate()  # type: ignore[arg-type]
        with pytest.raises(ValidationError, match="nach dem Vertragsende"):
            Tenant(**base, end_date=date(2025, 1, 31), deposit_paid_on=date(2025, 2, 1)).validate()  # type: ignore[arg-type]


def _tenant(context: AppContext, **values: object) -> Tenant:
    house = context.properties.save_property(Property(name="Haus", address="Weg 1, 12345 Ort"))
    assert house.id is not None
    flat = context.apartments.save_apartment(Apartment(house.id, "EG", Decimal("80"), 2))
    assert flat.id is not None
    return context.tenants.save_tenant(
        Tenant(apartment_id=flat.id, name="Anna", start_date=date(2024, 3, 1), rent_amount=EUR("800"), **values)  # type: ignore[arg-type]
    )


class TestStorage:
    def test_values_are_saved(self, context: AppContext) -> None:
        rates = [InterestRate(date(2024, 3, 1), Decimal("0.5")), InterestRate(date(2025, 1, 1), Decimal("1"))]
        saved = _tenant(context, deposit_amount=EUR("2400"), deposit_paid_on=date(2024, 3, 15), deposit_rates=rates)
        assert saved.id is not None
        stored = context.tenants.get_tenant(saved.id)
        assert (stored.deposit_amount, stored.deposit_paid_on, stored.deposit_rates) == (
            EUR("2400"), date(2024, 3, 15), rates,
        )

    def test_tenant_without_deposit(self, context: AppContext) -> None:
        saved = _tenant(context)
        assert saved.id is not None
        stored = context.tenants.get_tenant(saved.id)
        assert not stored.has_deposit and stored.deposit_rates == [] and stored.deposit_paid_on is None

    def test_migration_adds_the_columns(self) -> None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        for target, script in MIGRATIONS:
            if target <= 14:
                connection.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")
        connection.execute("INSERT INTO properties (name, address, created_at) VALUES ('H', 'A 1', '2025-01-01T00:00:00')")
        connection.execute(
            "INSERT INTO apartments (property_id, number, area_sqm, person_count, created_at) "
            "VALUES (1, 'EG', '80', 2, '2025-01-01T00:00:00')"
        )
        connection.execute("INSERT INTO tenants (apartment_id, name, start_date) VALUES (1, 'Alt', '2020-01-01')")
        migrate(connection, None)
        assert current_version(connection) >= 15
        assert connection.execute("SELECT deposit_amount, deposit_paid_on, deposit_rates FROM tenants").fetchone() == (
            0, None, "[]",
        )


class TestDialogs:
    def test_tenant_dialog_saves_the_deposit(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.tenant_dialog import TenantDialog
        from ui.lookups import Lookups

        tenant = _tenant(context)
        dialog = TenantDialog(context.tenants.save_tenant, Lookups(context), tenant)
        dialog._deposit_spin.set_money(EUR("2400"))
        dialog._rates_editor.add_row(date(2024, 3, 1), Decimal("0.5"))
        assert "0 %" not in dialog._deposit_hint.text()
        dialog.accept()

        assert tenant.id is not None
        stored = context.tenants.get_tenant(tenant.id)
        assert stored.deposit_amount == EUR("2400") and stored.deposit_paid_on is None
        assert stored.deposit_rates == [InterestRate(date(2024, 3, 1), Decimal("0.5"))]

    def test_hint_above_three_months_rent(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.tenant_dialog import TenantDialog
        from ui.lookups import Lookups

        tenant = _tenant(context)
        dialog = TenantDialog(context.tenants.save_tenant, Lookups(context), tenant)
        dialog._deposit_spin.set_money(EUR("2500"))  # Miete 800 € -> höchstens 2.400 €
        assert "drei Monatsmieten" in dialog._deposit_hint.text()

    def test_deposit_dialog_shows_the_payout(self, qt_app: QApplication) -> None:
        from ui.dialogs.deposit_dialog import DepositDialog

        tenant = Tenant(apartment_id=1, name="Anna", start_date=date(2024, 3, 1), end_date=date(2026, 3, 31),
                        deposit_amount=EUR("3000"),
                        deposit_rates=[InterestRate(date(2024, 3, 1), Decimal("0.5")),
                                       InterestRate(date(2025, 1, 1), Decimal("1"))])
        dialog = DepositDialog(tenant)
        assert "01.04.2026" in dialog._result_label.text() and "3.050,17" in dialog._result_label.text()
        dialog._until_edit.set_value(date(2025, 1, 1))
        assert "3.012,54" in dialog._result_label.text()
