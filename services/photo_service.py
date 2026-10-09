"""Anwendungsfälle rund um die Fotodokumentation."""

from __future__ import annotations

import logging
from pathlib import Path

import config
from models.enums import PhotoCategory
from models.exceptions import ValidationError
from models.photo import Photo
from repositories.interfaces import PhotoRepository

logger = logging.getLogger(__name__)

#: Dateiendungen, die als Foto akzeptiert werden.
SUPPORTED_EXTENSIONS: tuple[str, ...] = (".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")


def read_image_file(path: Path) -> bytes:
    """Liest eine Bilddatei von der Festplatte.

    Raises:
        ValidationError: Wenn die Datei kein unterstütztes Bild, zu groß oder
            nicht lesbar ist.
    """
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        allowed = ", ".join(SUPPORTED_EXTENSIONS)
        raise ValidationError(f"Nicht unterstütztes Bildformat. Erlaubt sind: {allowed}")
    try:
        size = path.stat().st_size
        if size > config.MAX_PHOTO_BYTES:
            limit_mb = config.MAX_PHOTO_BYTES // (1024 * 1024)
            raise ValidationError(f"Das Foto ist größer als {limit_mb} MB.")
        return path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"Die Bilddatei kann nicht gelesen werden: {exc}") from exc


class PhotoService:
    """Verwaltet die allgemeine Fotodokumentation (Mängel, Sonstiges)."""

    def __init__(self, photos: PhotoRepository) -> None:
        self._photos = photos

    def list_documentation(self) -> list[Photo]:
        """Alle Fotos, die nicht an einem Zählerstand hängen (ohne Bilddaten)."""
        return self._photos.list_metadata(standalone_only=True)

    def load_photo(self, photo_id: int) -> Photo:
        """Lädt ein Foto vollständig, also inklusive Bilddaten."""
        return self._photos.get(photo_id)

    def add_photo(
        self,
        apartment_id: int,
        category: PhotoCategory,
        description: str,
        file_name: str,
        image_data: bytes,
    ) -> Photo:
        """Speichert ein neues Dokumentationsfoto zu einer Wohnung.

        Raises:
            ValidationError: Bei fehlenden oder zu großen Bilddaten.
        """
        photo = Photo(
            file_name=file_name,
            image_data=image_data,
            category=category,
            description=description,
            apartment_id=apartment_id,
        )
        photo.validate()
        saved = self._photos.add(photo)
        logger.info("Foto gespeichert: %s (ID %s)", saved.file_name, saved.id)
        return saved

    def delete_photo(self, photo_id: int) -> None:
        """Löscht ein Foto."""
        self._photos.delete(photo_id)

    def export_photo(self, photo_id: int, target: Path) -> None:
        """Schreibt die Bilddaten eines Fotos in eine Datei.

        Raises:
            ValidationError: Wenn die Zieldatei nicht geschrieben werden kann.
        """
        photo = self.load_photo(photo_id)
        try:
            target.write_bytes(photo.image_data)
        except OSError as exc:
            raise ValidationError(f"Das Foto kann nicht gespeichert werden: {exc}") from exc
