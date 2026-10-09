"""Entity: Beleg (PDF oder Foto), der an einer Rechnung hängt."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import PurePath
from typing import Final

import config
from models.exceptions import ValidationError
from utils.validators import require_text

#: Dateiendungen, die als Beleg akzeptiert werden (PDF und die üblichen Bildformate).
ATTACHMENT_EXTENSIONS: Final[tuple[str, ...]] = (".pdf", ".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp")


@dataclass
class CostAttachment:
    """Ein Beleg, der als BLOB direkt in der Benutzer-Datenbank liegt.

    Eine Rechnung kann beliebig viele Belege haben (z. B. die Rechnung als PDF
    und ein Foto des Anschreibens). Wird die Rechnung gelöscht, verschwinden
    ihre Belege mit.

    Ein Beleg gehört entweder zu einer einzelnen Rechnung (``cost_id``) oder
    zu einer Sammelrechnung (``bundle_id``) - dann gilt er für alle ihre Positionen.

    ``file_data`` kann leer sein, wenn das Repository nur die Metadaten
    geladen hat (Listenansicht); ``size_bytes`` ist auch dann gefüllt.
    """

    file_name: str
    file_data: bytes = b""
    cost_id: int | None = None
    bundle_id: int | None = None
    uploaded_at: datetime = field(default_factory=datetime.now)
    size_bytes: int = 0
    id: int | None = None

    @property
    def is_pdf(self) -> bool:
        """``True`` bei einem PDF, sonst ist der Beleg ein Bild."""
        return PurePath(self.file_name).suffix.lower() == ".pdf"

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.file_name = require_text(self.file_name, "Dateiname", max_length=255)
        if (self.cost_id is None) == (self.bundle_id is None):
            raise ValidationError("Ein Beleg muss genau einer Rechnung oder Sammelrechnung zugeordnet sein.")
        validate_attachment(self.file_name, len(self.file_data))
        self.size_bytes = len(self.file_data)


def validate_attachment(file_name: str, size_bytes: int) -> None:
    """Prüft Dateityp und Größe eines Belegs.

    Raises:
        ValidationError: Bei nicht unterstütztem Format, leerer oder zu großer Datei.
    """
    if PurePath(file_name).suffix.lower() not in ATTACHMENT_EXTENSIONS:
        allowed = ", ".join(ATTACHMENT_EXTENSIONS)
        raise ValidationError(f"'{file_name}': Nicht unterstütztes Format. Erlaubt sind: {allowed}")
    if size_bytes <= 0:
        raise ValidationError(f"'{file_name}': Die Datei ist leer.")
    if size_bytes > config.MAX_ATTACHMENT_BYTES:
        limit_mb = config.MAX_ATTACHMENT_BYTES // (1024 * 1024)
        raise ValidationError(f"'{file_name}': Der Beleg ist größer als {limit_mb} MB.")
