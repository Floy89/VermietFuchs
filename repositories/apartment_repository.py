"""Repository für Wohnungen."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any

from models.apartment import Apartment
from repositories.base_repository import SqliteRepository


class SqliteApartmentRepository(SqliteRepository[Apartment]):
    """Zugriff auf die Tabelle ``apartments``."""

    table_name = "apartments"
    id_column = "apartment_id"
    default_order = "t.property_id, t.number COLLATE NOCASE"
    entity_label = "Wohnung"

    def list_by_property(self, property_id: int) -> list[Apartment]:
        """Alle Wohnungen einer Immobilie, sortiert nach Wohnungsnummer."""
        return self._select("t.property_id = ?", (property_id,))

    def _to_row(self, entity: Apartment) -> dict[str, Any]:
        return {
            "property_id": entity.property_id,
            "number": entity.number,
            "area_sqm": str(entity.area_sqm),
            "person_count": entity.person_count,
            "created_at": entity.created_at.isoformat(timespec="seconds"),
        }

    def _from_row(self, row: sqlite3.Row) -> Apartment:
        return Apartment(
            id=row["apartment_id"],
            property_id=row["property_id"],
            number=row["number"],
            area_sqm=Decimal(row["area_sqm"]),
            person_count=row["person_count"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
