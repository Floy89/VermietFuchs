"""Repository für Mieter."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from models.tenant import Tenant
from models.value_objects import Money
from repositories.base_repository import SqliteRepository


class SqliteTenantRepository(SqliteRepository[Tenant]):
    """Zugriff auf die Tabelle ``tenants``."""

    table_name = "tenants"
    id_column = "tenant_id"
    default_order = "t.name COLLATE NOCASE, t.start_date"
    entity_label = "Mieter"

    def list_by_apartment(self, apartment_id: int) -> list[Tenant]:
        """Alle (auch ehemaligen) Mieter einer Wohnung, älteste zuerst."""
        return self._select("t.apartment_id = ?", (apartment_id,), order_by="t.start_date")

    def list_by_property(self, property_id: int) -> list[Tenant]:
        """Alle Mieter sämtlicher Wohnungen einer Immobilie."""
        return self._select(
            "t.apartment_id IN (SELECT apartment_id FROM apartments WHERE property_id = ?)",
            (property_id,),
        )

    def _to_row(self, entity: Tenant) -> dict[str, Any]:
        return {
            "apartment_id": entity.apartment_id,
            "name": entity.name,
            "contact": entity.contact,
            "start_date": entity.start_date.isoformat(),
            "end_date": entity.end_date.isoformat() if entity.end_date else None,
            "rent_amount": entity.rent_amount.cents,
        }

    def _from_row(self, row: sqlite3.Row) -> Tenant:
        return Tenant(
            id=row["tenant_id"],
            apartment_id=row["apartment_id"],
            name=row["name"],
            contact=row["contact"],
            start_date=date.fromisoformat(row["start_date"]),
            end_date=date.fromisoformat(row["end_date"]) if row["end_date"] else None,
            rent_amount=Money(row["rent_amount"]),
        )
