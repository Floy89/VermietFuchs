"""Entity: Dokumentationsfoto (Zählerstand, Mangel, Sonstiges)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import config
from models.enums import PhotoCategory
from models.exceptions import ValidationError
from utils.validators import optional_text, require_text


@dataclass
class Photo:
    """Ein Foto, das als BLOB direkt in der Benutzer-Datenbank liegt.

    Ein Foto hängt entweder an einem Zählerstand (``reading_id``) oder direkt
    an einer Wohnung (``apartment_id``), z. B. bei einem dokumentierten Mangel.

    ``image_data`` kann leer sein, wenn das Repository nur die Metadaten
    geladen hat (Listenansicht); ``size_bytes`` ist auch dann gefüllt.
    """

    file_name: str
    image_data: bytes = b""
    category: PhotoCategory = PhotoCategory.OTHER
    description: str = ""
    reading_id: int | None = None
    apartment_id: int | None = None
    uploaded_at: datetime = field(default_factory=datetime.now)
    size_bytes: int = 0
    id: int | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.file_name = require_text(self.file_name, "Dateiname", max_length=255)
        self.description = optional_text(self.description, "Beschreibung", max_length=500)
        if not self.image_data:
            raise ValidationError("Das Foto enthält keine Bilddaten.")
        if len(self.image_data) > config.MAX_PHOTO_BYTES:
            limit_mb = config.MAX_PHOTO_BYTES // (1024 * 1024)
            raise ValidationError(f"Das Foto ist größer als {limit_mb} MB.")
        if self.reading_id is None and self.apartment_id is None:
            raise ValidationError("Ein Foto muss einer Wohnung oder einem Zählerstand zugeordnet sein.")
        self.size_bytes = len(self.image_data)
