"""Repository für Kostenarten."""

from __future__ import annotations

import sqlite3
from typing import Any

from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from repositories.base_repository import SqliteRepository


class SqliteCostTypeRepository(SqliteRepository[CostType]):
    """Zugriff auf die Tabelle ``cost_types``."""

    table_name = "cost_types"
    id_column = "cost_type_id"
    default_order = "t.sort_order, t.name COLLATE NOCASE"
    entity_label = "Kostenart"

    def count_costs(self, cost_type_id: int) -> int:
        """Anzahl der Kostenpositionen, die diese Kostenart verwenden."""
        row = self._db.query_one(
            "SELECT COUNT(*) AS n FROM costs WHERE cost_type_id = ?", (cost_type_id,)
        )
        return int(row["n"]) if row else 0

    def _to_row(self, entity: CostType) -> dict[str, Any]:
        return {
            "name": entity.name,
            "legal_basis": entity.legal_basis,
            "default_key": entity.default_key.value,
            "meter_type": entity.meter_type.value if entity.meter_type else None,
            "heating_rule": int(entity.heating_rule),
            "is_builtin": int(entity.is_builtin),
            "sort_order": entity.sort_order,
        }

    def _from_row(self, row: sqlite3.Row) -> CostType:
        return CostType(
            id=row["cost_type_id"],
            name=row["name"],
            legal_basis=row["legal_basis"],
            default_key=AllocationKey(row["default_key"]),
            meter_type=MeterType(row["meter_type"]) if row["meter_type"] else None,
            heating_rule=bool(row["heating_rule"]),
            is_builtin=bool(row["is_builtin"]),
            sort_order=row["sort_order"],
        )
