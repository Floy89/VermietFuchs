"""Repository für Immobilien."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from models.property import Property
from repositories.base_repository import SqliteRepository


class SqlitePropertyRepository(SqliteRepository[Property]):
    """Zugriff auf die Tabelle ``properties``."""

    table_name = "properties"
    id_column = "property_id"
    default_order = "t.name COLLATE NOCASE"
    entity_label = "Immobilie"

    def _to_row(self, entity: Property) -> dict[str, Any]:
        return {
            "name": entity.name,
            "address": entity.address,
            "created_at": entity.created_at.isoformat(timespec="seconds"),
        }

    def _from_row(self, row: sqlite3.Row) -> Property:
        return Property(
            id=row["property_id"],
            name=row["name"],
            address=row["address"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
