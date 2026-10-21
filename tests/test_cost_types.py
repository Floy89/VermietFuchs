"""Tests für den Kostenarten-Katalog, eigene Kostenarten und die Schema-Migration."""

from __future__ import annotations

import sqlite3
from datetime import date
from pathlib import Path

import pytest

from app_context import AppContext
from database.db_manager import DatabaseManager
from database.schema import SCHEMA_V1
from models.cost import Cost
from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from models.exceptions import ValidationError
from models.property import Property
from models.value_objects import DateRange, Money
from services.user_service import UserService


class TestCatalog:
    def test_catalog_covers_betrkv(self, context: AppContext) -> None:
        types = context.cost_types.list_types()
        names = [t.name for t in types]
        assert len(types) >= 30 and all(t.is_builtin for t in types)
        for expected in ("Grundsteuer", "Wasserversorgung", "Aufzug", "Hauswart", "Rauchwarnmelder-Wartung"):
            assert expected in names
        # Katalog-Reihenfolge folgt den Nummern der Verordnung.
        assert names[0] == "Grundsteuer" and names[-1] == "Sonstige Betriebskosten"

    def test_heating_types_follow_heating_rule(self, context: AppContext) -> None:
        heating = context.cost_types.get_by_name("Verbundene Heizungs- und Warmwasserversorgung")
        assert heating.allowed_keys() == (AllocationKey.CONSUMPTION_AREA, AllocationKey.DIRECT, AllocationKey.OWNER)

    def test_builtin_types_are_protected(self, context: AppContext) -> None:
        builtin = context.cost_types.get_by_name("Grundsteuer")
        assert builtin.id is not None
        builtin.name = "Umbenannt"
        with pytest.raises(ValidationError):
            context.cost_types.save_type(builtin)
        with pytest.raises(ValidationError):
            context.cost_types.delete_type(builtin.id)


class TestCustomTypes:
    def test_create_use_and_delete(self, context: AppContext) -> None:
        custom = context.cost_types.save_type(
            CostType("Wartung Hebeanlage", AllocationKey.UNITS)
        )
        assert custom.id is not None and not custom.is_builtin
        # Eigene Kostenarten stehen hinter dem Katalog.
        assert context.cost_types.list_types()[-1].name == "Wartung Hebeanlage"

        prop = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert prop.id is not None
        cost = context.costs.save_cost(
            Cost(prop.id, custom, Money.from_decimal("120"), DateRange.for_year(2025), AllocationKey.UNITS)
        )
        assert context.costs.list_costs(prop.id)[0].cost_type.name == "Wartung Hebeanlage"

        # Solange sie verwendet wird, lässt sie sich nicht löschen.
        with pytest.raises(ValidationError):
            context.cost_types.delete_type(custom.id)
        assert cost.id is not None
        context.costs.delete_cost(cost.id)
        context.cost_types.delete_type(custom.id)

    def test_rename_and_duplicate_name(self, context: AppContext) -> None:
        custom = context.cost_types.save_type(CostType("Poolpflege", meter_type=MeterType.COLD_WATER))
        custom.name = "Schwimmbadpflege"
        context.cost_types.save_type(custom)
        assert context.cost_types.get_by_name("schwimmbadpflege").id == custom.id
        with pytest.raises(ValidationError):
            context.cost_types.save_type(CostType("grundsteuer"))  # gibt es schon im Katalog


def test_migration_from_schema_1(tmp_path: Path) -> None:
    """Eine Datenbank der Version 1 wird beim Öffnen verlustfrei angehoben."""
    path = tmp_path / "benutzer.db"
    old = sqlite3.connect(path)
    old.executescript(SCHEMA_V1)
    old.execute("PRAGMA user_version = 1")
    old.execute("INSERT INTO properties (name, address, created_at) VALUES ('Haus', 'Weg 1', '2025-01-01T00:00:00')")
    for old_type, amount, key in (
        ("HEATING", 100000, "CONSUMPTION_AREA"),
        ("COLD_WATER", 20000, "CONSUMPTION"),
        ("WASTE", 30000, "PERSONS"),
        ("OTHER", 40000, "AREA"),
    ):
        old.execute(
            "INSERT INTO costs (property_id, cost_type, description, amount, period_start, period_end, "
            "allocation_key, consumption_share) VALUES (1, ?, 'alt', ?, '2025-01-01', '2025-12-31', ?, 60)",
            (old_type, amount, key),
        )
    old.commit()
    old.close()

    database = DatabaseManager(path)
    database.open()
    context = AppContext(database, UserService._ensure_owner(database, "Tester"))
    try:
        costs = context.costs.list_costs(1)
        assert {c.cost_type.name: c.amount.cents for c in costs} == {
            "Verbundene Heizungs- und Warmwasserversorgung": 100000,
            "Wasserversorgung": 20000,
            "Müllbeseitigung": 30000,
            "Sonstige Betriebskosten": 40000,
        }
        assert all(c.consumption_share == 60 and c.description == "alt" for c in costs)
        for cost in costs:
            cost.validate()  # die alten Schlüssel sind weiterhin zulässig
        assert costs[0].period == DateRange(date(2025, 1, 1), date(2025, 12, 31))
    finally:
        context.close()
    # Die Tabelle der Einzelzahlungen gibt es nicht mehr ...
    check = sqlite3.connect(path)
    tables = {row[0] for row in check.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    check.close()
    assert "payments" not in tables and {"invoice_group_settings", "billing_period_overrides"} <= tables
    # ... in der Sicherungskopie von vor der Migration aber schon.
    assert (tmp_path / "benutzer.db.v1.bak").is_file()
