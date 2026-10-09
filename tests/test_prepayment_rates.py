"""Tests für die Historie der vereinbarten Vorauszahlungen (inkl. Erhöhungen)."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app_context import AppContext
from database.db_manager import DatabaseManager
from database.schema import SCHEMA_V1
from models.apartment import Apartment
from models.exceptions import ValidationError
from models.prepayment_rate import PrepaymentRate
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from services.user_service import UserService
from tools.demo_data import seed_demo_data


def _money(value: str) -> Money:
    return Money.from_decimal(value)


@pytest.fixture()
def tenant(context: AppContext) -> Tenant:
    """Ein Mieter mit 150 EUR Vorauszahlung ab Vertragsbeginn 01.04.2022."""
    prop = context.properties.save_property(Property("Haus", "Adresse 1"))
    assert prop.id is not None
    apartment = context.apartments.save_apartment(Apartment(prop.id, "1", Decimal("50")))
    assert apartment.id is not None
    return context.tenants.save_tenant(
        Tenant(apartment.id, "Mieter", date(2022, 4, 1)), initial_prepayment=_money("150")
    )


def test_new_tenant_gets_first_rate(context: AppContext, tenant: Tenant) -> None:
    assert tenant.id is not None
    rates = context.prepayment_rates.list_rates(tenant.id)
    assert [(r.amount, r.valid_from, r.valid_to) for r in rates] == [(_money("150"), date(2022, 4, 1), None)]


def test_increase_closes_previous_rate_and_keeps_history(context: AppContext, tenant: Tenant) -> None:
    assert tenant.id is not None
    context.prepayment_rates.save_rate(PrepaymentRate(tenant.id, _money("165"), date(2025, 7, 1), note="Erhöhung"))

    rates = context.prepayment_rates.list_rates(tenant.id)
    assert [(r.amount, r.valid_from, r.valid_to) for r in rates] == [
        (_money("150"), date(2022, 4, 1), date(2025, 6, 30)),  # automatisch zum Vortag beendet
        (_money("165"), date(2025, 7, 1), None),
    ]
    assert context.prepayment_rates.rate_on(tenant.id, date(2025, 6, 30)) == _money("150")
    assert context.prepayment_rates.rate_on(tenant.id, date(2025, 7, 1)) == _money("165")
    # Auch das automatische Beenden steht im Änderungsprotokoll.
    actions = [e.action.value for e in context.audit.list_entries(table_name="prepayment_rates")]
    assert actions == ["INSERT", "UPDATE", "INSERT"]


def test_overlapping_rates_are_rejected(context: AppContext, tenant: Tenant) -> None:
    assert tenant.id is not None
    context.prepayment_rates.save_rate(PrepaymentRate(tenant.id, _money("165"), date(2025, 7, 1)))
    with pytest.raises(ValidationError):
        # Liegt mitten in der (inzwischen befristeten) ersten Rate.
        context.prepayment_rates.save_rate(
            PrepaymentRate(tenant.id, _money("155"), date(2024, 1, 1), date(2024, 12, 31))
        )
    # Die fehlgeschlagene Eingabe hat nichts verändert.
    assert len(context.prepayment_rates.list_rates(tenant.id)) == 2


def test_deleting_tenant_removes_rates(context: AppContext, tenant: Tenant) -> None:
    assert tenant.id is not None
    context.tenants.delete_tenant(tenant.id)
    assert context.prepayment_rates.list_rates(tenant.id) == []


def test_billing_uses_rates(context: AppContext) -> None:
    """Demo-Daten: Familie Hoffmann zahlt bis 30.06. 215 EUR, ab 01.07. 230 EUR."""
    seed_demo_data(context, billing_year=2025)
    house = next(p for p in context.properties.list_properties() if p.name == "Musterstraße 12")
    assert house.id is not None
    result = context.billing.create_billing(house.id, DateRange.for_year(2025))
    hoffmann = next(s for s in result.statements if s.tenant.name == "Familie Hoffmann")

    # Sechs Raten zu 215 und sechs zu 230.
    assert hoffmann.prepayments == _money("2670.00")
    assert [r.amount for r in hoffmann.prepayment_rates] == [_money("215"), _money("230")]


def test_migration_turns_single_value_into_first_rate(tmp_path: Path) -> None:
    """Der frühere Einzelwert je Mieter wird beim Öffnen zur ersten Rate der Historie."""
    path = tmp_path / "benutzer.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA_V1)
    old.execute("PRAGMA user_version = 1")
    old.execute("INSERT INTO properties (name, address, created_at) VALUES ('Haus', 'Weg 1', '2025-01-01T00:00:00')")
    old.execute(
        "INSERT INTO apartments (property_id, number, area_sqm, person_count, created_at) "
        "VALUES (1, 'EG', '60', 2, '2025-01-01T00:00:00')"
    )
    old.execute(
        "INSERT INTO tenants (apartment_id, name, start_date, rent_amount, prepayment_amount) "
        "VALUES (1, 'Altmieter', '2023-05-01', 50000, 12000)"
    )
    old.execute(
        "INSERT INTO tenants (apartment_id, name, start_date, end_date, rent_amount, prepayment_amount) "
        "VALUES (1, 'Ohne Vorauszahlung', '2020-01-01', '2023-04-30', 50000, 0)"
    )
    old.commit()
    old.close()

    database = DatabaseManager(path)
    database.open()
    context = AppContext(database, UserService._ensure_owner(database, "Tester"))
    try:
        tenants = {t.name: t for t in context.tenants.list_tenants()}
        assert tenants["Altmieter"].rent_amount == _money("500")
        rates = context.prepayment_rates.list_rates(tenants["Altmieter"].id or 0)
        assert [(r.amount, r.valid_from, r.valid_to) for r in rates] == [(_money("120"), date(2023, 5, 1), None)]
        assert context.prepayment_rates.list_rates(tenants["Ohne Vorauszahlung"].id or 0) == []
    finally:
        context.close()
