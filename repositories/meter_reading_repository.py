"""Repository für Zählerstände."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any

from models.enums import MeterType
from models.meter_reading import MeterReading
from repositories.base_repository import SqliteRepository

_IN_PROPERTY = "t.apartment_id IN (SELECT apartment_id FROM apartments WHERE property_id = ?)"


class SqliteMeterReadingRepository(SqliteRepository[MeterReading]):
    """Zugriff auf die Tabelle ``meter_readings``."""

    table_name = "meter_readings"
    id_column = "reading_id"
    default_order = "t.date DESC, t.reading_id DESC"
    entity_label = "Zählerstand"

    def list_filtered(
        self,
        property_id: int | None = None,
        apartment_id: int | None = None,
        meter_type: MeterType | None = None,
    ) -> list[MeterReading]:
        """Zählerstände, optional eingeschränkt; neueste zuerst."""
        conditions: list[str] = []
        parameters: list[Any] = []
        if property_id is not None:
            conditions.append(_IN_PROPERTY)
            parameters.append(property_id)
        if apartment_id is not None:
            conditions.append("t.apartment_id = ?")
            parameters.append(apartment_id)
        if meter_type is not None:
            conditions.append("t.meter_type = ?")
            parameters.append(meter_type.value)
        return self._select(" AND ".join(conditions), parameters)

    def list_for_property_and_type(self, property_id: int, meter_type: MeterType) -> list[MeterReading]:
        """Alle Ablesungen einer Zählerart in einer Immobilie, älteste zuerst."""
        return self._select(
            f"{_IN_PROPERTY} AND t.meter_type = ?",
            (property_id, meter_type.value),
            order_by="t.apartment_id, t.date",
        )

    def _to_row(self, entity: MeterReading) -> dict[str, Any]:
        return {
            "apartment_id": entity.apartment_id,
            "meter_type": entity.meter_type.value,
            "value": str(entity.value),
            "date": entity.date.isoformat(),
            "reader": entity.reader,
            "note": entity.note,
        }

    def _from_row(self, row: sqlite3.Row) -> MeterReading:
        return MeterReading(
            id=row["reading_id"],
            apartment_id=row["apartment_id"],
            meter_type=MeterType(row["meter_type"]),
            value=Decimal(row["value"]),
            date=date.fromisoformat(row["date"]),
            reader=row["reader"],
            note=row["note"],
        )
