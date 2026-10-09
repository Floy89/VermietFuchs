"""Schema-Version 9: Belege (PDF/Foto) an Rechnungen.

Die Dateien liegen wie die Fotos als BLOB in der Benutzer-Datenbank - so
bleibt alles in einer Datei, die sich als Ganzes sichern lässt. Mit der
Rechnung werden auch ihre Belege gelöscht.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V9: Final[str] = """
CREATE TABLE cost_attachments (
    attachment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cost_id       INTEGER NOT NULL REFERENCES costs(cost_id) ON DELETE CASCADE,
    file_name     TEXT    NOT NULL,
    file_data     BLOB    NOT NULL,
    uploaded_at   TEXT    NOT NULL
);
CREATE INDEX idx_cost_attachments_cost ON cost_attachments(cost_id);
"""
