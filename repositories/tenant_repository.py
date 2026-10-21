"""Repository für Mieter."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from models.deposit import InterestRate
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
            "deposit_amount": entity.deposit_amount.cents,
            "deposit_paid_on": entity.deposit_paid_on.isoformat() if entity.deposit_paid_on else None,
            "deposit_rates": json.dumps(
                [{"from": rate.valid_from.isoformat(), "percent": str(rate.percent)} for rate in entity.deposit_rates]
            ),
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
            deposit_amount=Money(row["deposit_amount"]),
            deposit_paid_on=date.fromisoformat(row["deposit_paid_on"]) if row["deposit_paid_on"] else None,
            deposit_rates=[
                InterestRate(date.fromisoformat(raw["from"]), Decimal(raw["percent"]))
                for raw in json.loads(row["deposit_rates"] or "[]")
            ],
        )
