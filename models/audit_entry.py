"""Entity: Eintrag im Änderungsprotokoll (Audit Trail)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from models.enums import AuditAction


@dataclass(frozen=True)
class AuditEntry:
    """Beantwortet die Frage: Wer hat was wann geändert?

    ``old_value`` und ``new_value`` enthalten den Datensatz vor bzw. nach der
    Änderung als JSON-Text. Beim Anlegen ist ``old_value`` leer, beim Löschen
    ``new_value``.
    """

    table_name: str
    record_id: int
    action: AuditAction
    timestamp: datetime
    old_value: str | None = None
    new_value: str | None = None
    user_id: int | None = None
    user_name: str = ""
    id: int | None = None
