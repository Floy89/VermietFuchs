"""Repository für Belege von Rechnungen (als BLOB in der Datenbank)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from models.attachment import CostAttachment
from repositories.base_repository import SqliteRepository

# Alle Spalten außer den Dateidaten, dazu die Dateigröße. Listenansichten laden
# damit nur wenige Bytes je Beleg statt mehrerer Megabyte.
_METADATA_COLUMNS = (
    "t.attachment_id, t.cost_id, t.bundle_id, t.file_name, t.uploaded_at, length(t.file_data) AS size_bytes"
)


class SqliteCostAttachmentRepository(SqliteRepository[CostAttachment]):
    """Zugriff auf die Tabelle ``cost_attachments``."""

    table_name = "cost_attachments"
    id_column = "attachment_id"
    default_order = "t.uploaded_at, t.attachment_id"
    entity_label = "Beleg"

    def list_metadata(self, cost_id: int | None = None, bundle_id: int | None = None) -> list[CostAttachment]:
        """Die Belege einer Rechnung bzw. einer Sammelrechnung *ohne* Dateidaten, älteste zuerst."""
        column, value = ("t.bundle_id", bundle_id) if bundle_id is not None else ("t.cost_id", cost_id)
        sql = f"SELECT {_METADATA_COLUMNS} FROM cost_attachments AS t WHERE {column} = ? ORDER BY {self.default_order}"
        return [self._from_row(row) for row in self._db.query_all(sql, (value,))]

    def count_by_cost(self) -> dict[int, int]:
        """Anzahl der Belege je Rechnungs-ID (nur Rechnungen mit mindestens einem Beleg).

        Bei der Position einer Sammelrechnung zählen deren Belege mit.
        """
        rows = self._db.query_all(
            "SELECT c.cost_id, COUNT(a.attachment_id) AS number FROM costs AS c "
            "JOIN cost_attachments AS a ON a.cost_id = c.cost_id OR a.bundle_id = c.bundle_id "
            "GROUP BY c.cost_id"
        )
        return {row["cost_id"]: row["number"] for row in rows}

    def _to_row(self, entity: CostAttachment) -> dict[str, Any]:
        return {
            "cost_id": entity.cost_id,
            "bundle_id": entity.bundle_id,
            "file_name": entity.file_name,
            "file_data": entity.file_data,
            "uploaded_at": entity.uploaded_at.isoformat(timespec="seconds"),
        }

    def _from_row(self, row: sqlite3.Row) -> CostAttachment:
        # Je nach Abfrage enthält die Zeile die Dateidaten oder nur deren Größe.
        columns = row.keys()
        file_data = bytes(row["file_data"]) if "file_data" in columns else b""
        size_bytes = row["size_bytes"] if "size_bytes" in columns else len(file_data)
        return CostAttachment(
            id=row["attachment_id"],
            cost_id=row["cost_id"],
            bundle_id=row["bundle_id"],
            file_name=row["file_name"],
            file_data=file_data,
            uploaded_at=datetime.fromisoformat(row["uploaded_at"]),
            size_bytes=size_bytes,
        )
