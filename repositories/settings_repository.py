"""Repository für die Schlüssel-Wert-Einstellungen einer Benutzer-Datenbank."""

from __future__ import annotations

from typing import Mapping

from database.db_manager import DatabaseManager
from models.enums import AuditAction
from repositories.interfaces import AuditRecorder


class SqliteSettingsRepository:
    """Zugriff auf die Tabelle ``settings``."""

    def __init__(self, database: DatabaseManager, audit: AuditRecorder) -> None:
        """
        Args:
            database: Die geöffnete Benutzer-Datenbank.
            audit: Empfänger für die Einträge des Änderungsprotokolls.
        """
        self._db = database
        self._audit = audit

    def get_all(self) -> dict[str, str]:
        """Alle Einstellungen als Wörterbuch."""
        return {row["key"]: row["value"] for row in self._db.query_all("SELECT key, value FROM settings")}

    def set_many(self, values: Mapping[str, str]) -> None:
        """Speichert mehrere Einstellungen in einer Transaktion.

        Nur tatsächlich geänderte Werte werden geschrieben und protokolliert.
        """
        with self._db.transaction():
            current = self.get_all()
            changed = {key: value for key, value in values.items() if current.get(key) != value}
            if not changed:
                return
            for key, value in changed.items():
                self._db.execute(
                    "INSERT INTO settings (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            old_values = {key: current.get(key) for key in changed}
            # Einstellungen haben keine numerische ID - Protokoll unter ID 0.
            self._audit.record("settings", 0, AuditAction.UPDATE, old_values, changed)
