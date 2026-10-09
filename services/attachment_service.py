"""Anwendungsfälle rund um Belege (PDF/Foto) an Rechnungen."""

from __future__ import annotations

import logging
from pathlib import Path

from models.attachment import CostAttachment, validate_attachment
from models.exceptions import ValidationError
from repositories.interfaces import CostAttachmentRepository

logger = logging.getLogger(__name__)


def read_attachment_file(path: Path) -> bytes:
    """Liest eine Belegdatei von der Festplatte.

    Raises:
        ValidationError: Wenn die Datei kein unterstützter Beleg, zu groß oder
            nicht lesbar ist.
    """
    try:
        # Erst Typ und Größe prüfen, dann lesen - so landet keine riesige
        # Datei im Arbeitsspeicher, nur um danach abgelehnt zu werden.
        validate_attachment(path.name, path.stat().st_size)
        return path.read_bytes()
    except OSError as exc:
        raise ValidationError(f"Die Datei '{path.name}' kann nicht gelesen werden: {exc}") from exc


class AttachmentService:
    """Verwaltet die Belege, die an Rechnungen hängen."""

    def __init__(self, attachments: CostAttachmentRepository) -> None:
        self._attachments = attachments

    def list_for_cost(self, cost_id: int) -> list[CostAttachment]:
        """Die Belege einer Rechnung (ohne Dateidaten), älteste zuerst."""
        return self._attachments.list_metadata(cost_id)

    def list_for_bundle(self, bundle_id: int) -> list[CostAttachment]:
        """Die Belege einer Sammelrechnung (ohne Dateidaten), älteste zuerst."""
        return self._attachments.list_metadata(bundle_id=bundle_id)

    def counts(self) -> dict[int, int]:
        """Anzahl der Belege je Rechnungs-ID (Belege einer Sammelrechnung zählen bei jeder Position)."""
        return self._attachments.count_by_cost()

    def load(self, attachment_id: int) -> CostAttachment:
        """Lädt einen Beleg vollständig, also inklusive Dateidaten."""
        return self._attachments.get(attachment_id)

    def add(self, cost_id: int, file_name: str, file_data: bytes) -> CostAttachment:
        """Hängt einen Beleg an eine gespeicherte Rechnung.

        Raises:
            ValidationError: Bei nicht unterstütztem Format, leerer oder zu großer Datei.
        """
        attachment = CostAttachment(cost_id=cost_id, file_name=file_name, file_data=file_data)
        attachment.validate()
        saved = self._attachments.add(attachment)
        logger.info("Beleg gespeichert: %s (ID %s, Rechnung %s)", saved.file_name, saved.id, cost_id)
        return saved

    def add_to_bundle(self, bundle_id: int, file_name: str, file_data: bytes) -> CostAttachment:
        """Hängt einen Beleg an eine gespeicherte Sammelrechnung.

        Raises:
            ValidationError: Bei nicht unterstütztem Format, leerer oder zu großer Datei.
        """
        attachment = CostAttachment(bundle_id=bundle_id, file_name=file_name, file_data=file_data)
        attachment.validate()
        saved = self._attachments.add(attachment)
        logger.info("Beleg gespeichert: %s (ID %s, Sammelrechnung %s)", saved.file_name, saved.id, bundle_id)
        return saved

    def delete(self, attachment_id: int) -> None:
        """Entfernt einen Beleg."""
        self._attachments.delete(attachment_id)
        logger.info("Beleg gelöscht (ID %s)", attachment_id)
