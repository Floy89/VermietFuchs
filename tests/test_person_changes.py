"""Belegungshistorie: Änderungen der Personenzahl mit "gültig ab", ohne frühere Abrechnungen zu verändern."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest
from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from database.migrations import MIGRATIONS, current_version, migrate
from models.apartment import Apartment, PersonChange
from models.cost import Cost
from models.enums import AllocationKey
from models.exceptions import ValidationError
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money

Y2025 = DateRange.for_year(2025)
Y2026 = DateRange.for_year(2026)


def _flat(changes: list[PersonChange] | None = None, persons: int = 2) -> Apartment:
    return Apartment(1, "EG", Decimal("105"), persons, person_changes=changes or [])


class TestModel:
    def test_persons_on_a_day(self) -> None:
        flat = _flat([PersonChange(date(2026, 7, 1), 3)])
        assert flat.persons_on(date(2026, 6, 30)) == 2
        assert flat.persons_on(date(2026, 7, 1)) == 3
        assert flat.persons_on(date(2027, 1, 1)) == 3

    def test_person_days_and_average(self) -> None:
        flat = _flat([PersonChange(date(2026, 7, 1), 3)])
        # 01.01.–30.06.: 181 Tage × 2, 01.07.–31.12.: 184 Tage × 3
        assert flat.person_days(Y2026) == 2 * 181 + 3 * 184
        assert flat.average_persons(Y2026) == Decimal(914) / Decimal(365)
        # Vorjahr unverändert
        assert flat.average_persons(Y2025) == Decimal(2)

    def test_change_on_first_day_counts_for_the_whole_period(self) -> None:
        # Stichtagsregel: Änderung zum 01.01. gilt das ganze Jahr.
        flat = _flat([PersonChange(date(2026, 1, 1), 3)])
        assert flat.average_persons(Y2026) == Decimal(3) and flat.average_persons(Y2025) == Decimal(2)
        assert flat.describe_persons(Y2026) == "3"

    def test_description(self) -> None:
        flat = _flat([PersonChange(date(2026, 7, 1), 3)])
        assert flat.describe_persons(Y2026) == "2, ab 01.07.2026: 3"
        assert flat.describe_persons(Y2025) == "2"

    def test_duplicate_dates_are_rejected(self) -> None:
        flat = _flat([PersonChange(date(2026, 7, 1), 3), PersonChange(date(2026, 7, 1), 4)])
        with pytest.raises(ValidationError):
            flat.validate()


def _house(context: AppContext) -> tuple[int, dict[str, int]]:
    house = context.properties.save_property(Property(name="Haus", address="Weg 1, 12345 Ort"))
    assert house.id is not None
    flats: dict[str, int] = {}
    for number, persons in (("EG", 2), ("1OG", 3), ("DG", 5)):
        flat = context.apartments.save_apartment(Apartment(house.id, number, Decimal("100"), persons))
        assert flat.id is not None
        flats[number] = flat.id
        context.tenants.save_tenant(Tenant(apartment_id=flat.id, name=f"Mieter {number}", start_date=date(2024, 1, 1)))
    garbage = context.cost_types.get_by_name("Müllbeseitigung")
    for period in (Y2025, Y2026):
        context.costs.save_cost(Cost(house.id, garbage, Money.from_decimal("500.00"), period, AllocationKey.PERSONS))
    return house.id, flats


def _garbage(context: AppContext, house_id: int, period: DateRange) -> dict[str, Money]:
    result = context.billing.create_billing(house_id, period)
    return {s.apartment.number: s.total_costs for s in result.statements}


class TestBilling:
    def test_changes_are_stored(self, context: AppContext) -> None:
        house_id, flats = _house(context)
        flat = context.apartments.get_apartment(flats["EG"])
        context.apartments.save_apartment(
            Apartment(flat.property_id, flat.number, flat.area_sqm, flat.person_count, flat.created_at, flat.id,
                      [PersonChange(date(2026, 7, 1), 3)])
        )
        stored = context.apartments.get_apartment(flats["EG"])
        assert stored.person_changes == [PersonChange(date(2026, 7, 1), 3)] and stored.person_count == 2

    def test_previous_year_stays_unchanged(self, context: AppContext) -> None:
        house_id, flats = _house(context)
        before = _garbage(context, house_id, Y2025)
        assert before == {"EG": Money.from_decimal("100.00"), "1OG": Money.from_decimal("150.00"),
                          "DG": Money.from_decimal("250.00")}

        # EG und 1OG bekommen 2026 Nachwuchs (ab 01.07.2026).
        for number in ("EG", "1OG"):
            flat = context.apartments.get_apartment(flats[number])
            flat.person_changes = [PersonChange(date(2026, 7, 1), flat.person_count + 1)]
            context.apartments.save_apartment(flat)

        assert _garbage(context, house_id, Y2025) == before
        after = _garbage(context, house_id, Y2026)
        # Personentage: EG 2×181 + 3×184 = 914, 1OG 3×181 + 4×184 = 1279, DG 5×365 = 1825 (Summe 4018)
        assert after["EG"] == Money.from_decimal(Decimal("500") * 914 / 4018)
        assert after["DG"] == Money.from_decimal(Decimal("500") * 1825 / 4018)
        assert sum(after.values(), Money.zero()) == Money.from_decimal("500.00")

    def test_basis_shows_average_persons(self, context: AppContext) -> None:
        from utils.formatters import format_share_basis

        house_id, flats = _house(context)
        flat = context.apartments.get_apartment(flats["EG"])
        flat.person_changes = [PersonChange(date(2026, 7, 1), 3)]
        context.apartments.save_apartment(flat)
        result = context.billing.create_billing(house_id, Y2026)
        (share,) = next(s for s in result.statements if s.apartment.number == "EG").shares
        assert format_share_basis(share).startswith("2,504 Pers. von")

    def test_pdf_header_names_the_change(self, context: AppContext, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from models.settings import LandlordSettings

        house_id, flats = _house(context)
        flat = context.apartments.get_apartment(flats["EG"])
        flat.person_changes = [PersonChange(date(2026, 7, 1), 3)]
        context.apartments.save_apartment(flat)
        result = context.billing.create_billing(house_id, Y2026)
        statement = next(s for s in result.statements if s.apartment.number == "EG")
        assert statement.apartment.describe_persons(statement.occupancy) == "2, ab 01.07.2026: 3"
        target = context.pdf_export.export_statements(result, [statement], LandlordSettings(), tmp_path / "a.pdf")
        assert target.stat().st_size > 0


class TestDialog:
    def test_add_change(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.property_dialog import ApartmentDialog

        house_id, flats = _house(context)
        flat = context.apartments.get_apartment(flats["EG"])
        dialog = ApartmentDialog(context.apartments.save_apartment, house_id, flat)
        dialog._changes_editor.add_row(date(2026, 7, 1), 3)
        dialog.accept()
        stored = context.apartments.get_apartment(flats["EG"])
        assert stored.person_changes == [PersonChange(date(2026, 7, 1), 3)] and stored.person_count == 2

        again = ApartmentDialog(context.apartments.save_apartment, house_id, stored)
        assert again._changes_editor.changes() == [PersonChange(date(2026, 7, 1), 3)]


class TestMigration:
    def test_existing_apartments_get_an_empty_history(self) -> None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        for target, script in MIGRATIONS:
            if target <= 13:
                connection.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")
        connection.execute(
            "INSERT INTO properties (name, address, created_at) VALUES ('Haus', 'Adresse 1', '2025-01-01T00:00:00')"
        )
        connection.execute(
            "INSERT INTO apartments (property_id, number, area_sqm, person_count, created_at) "
            "VALUES (1, 'EG', '105', 2, '2025-01-01T00:00:00')"
        )
        migrate(connection, None)
        assert current_version(connection) >= 14
        assert connection.execute("SELECT person_changes FROM apartments").fetchone()[0] == "[]"
