"""Repository für Fotos (als BLOB in der Datenbank)."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from models.enums import PhotoCategory
from models.photo import Photo
from repositories.base_repository import SqliteRepository

# Alle Spalten außer den Bilddaten, dazu die Bildgröße. Listenansichten laden
# damit nur wenige Bytes je Foto statt mehrerer Megabyte.
_METADATA_COLUMNS = (
    "t.photo_id, t.reading_id, t.apartment_id, t.category, t.description, "
    "t.file_name, t.uploaded_at, length(t.image_data) AS size_bytes"
)


class SqlitePhotoRepository(SqliteRepository[Photo]):
    """Zugriff auf die Tabelle ``photos``."""

    table_name = "photos"
    id_column = "photo_id"
    default_order = "t.uploaded_at DESC, t.photo_id DESC"
    entity_label = "Foto"

    def list_metadata(
        self,
        reading_id: int | None = None,
        standalone_only: bool = False,
    ) -> list[Photo]:
        """Lädt Fotos *ohne* Bilddaten.

        Args:
            reading_id: Nur Fotos dieses Zählerstands.
            standalone_only: Nur Fotos, die an keinem Zählerstand hängen
                (die allgemeine Fotodokumentation).
        """
        conditions: list[str] = []
        parameters: list[Any] = []
        if reading_id is not None:
            conditions.append("t.reading_id = ?")
            parameters.append(reading_id)
        if standalone_only:
            conditions.append("t.reading_id IS NULL")

        sql = f"SELECT {_METADATA_COLUMNS} FROM photos AS t"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += f" ORDER BY {self.default_order}"
        return [self._from_row(row) for row in self._db.query_all(sql, parameters)]

    def reading_ids_with_photos(self) -> set[int]:
        """IDs aller Zählerstände, zu denen mindestens ein Foto existiert."""
        rows = self._db.query_all(
            "SELECT DISTINCT reading_id FROM photos WHERE reading_id IS NOT NULL"
        )
        return {row["reading_id"] for row in rows}

    def _to_row(self, entity: Photo) -> dict[str, Any]:
        return {
            "reading_id": entity.reading_id,
            "apartment_id": entity.apartment_id,
            "category": entity.category.value,
            "description": entity.description,
            "file_name": entity.file_name,
            "image_data": entity.image_data,
            "uploaded_at": entity.uploaded_at.isoformat(timespec="seconds"),
        }

    def _from_row(self, row: sqlite3.Row) -> Photo:
        # Je nach Abfrage enthält die Zeile die Bilddaten oder nur deren Größe.
        columns = row.keys()
        image_data = bytes(row["image_data"]) if "image_data" in columns else b""
        size_bytes = row["size_bytes"] if "size_bytes" in columns else len(image_data)
        return Photo(
            id=row["photo_id"],
            reading_id=row["reading_id"],
            apartment_id=row["apartment_id"],
            category=PhotoCategory(row["category"]),
            description=row["description"],
            file_name=row["file_name"],
            image_data=image_data,
            uploaded_at=datetime.fromisoformat(row["uploaded_at"]),
            size_bytes=size_bytes,
        )
