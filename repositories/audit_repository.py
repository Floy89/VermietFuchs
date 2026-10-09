"""Repository für das Änderungsprotokoll (Audit Trail)."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Mapping

from database.db_manager import DatabaseManager
from models.audit_entry import AuditEntry
from models.enums import AuditAction


class SqliteAuditRepository:
    """Schreibt und liest die Tabelle ``audit_log``.

    Das Repository erbt bewusst *nicht* von ``SqliteRepository``:
    Protokolleinträge werden nie geändert oder gelöscht, und sie dürfen
    selbst kein weiteres Protokoll erzeugen.
    """

    def __init__(self, database: DatabaseManager) -> None:
        """
        Args:
            database: Die geöffnete Benutzer-Datenbank.
        """
        self._db = database

    def record(
        self,
        table_name: str,
        record_id: int,
        action: AuditAction,
        old_values: Mapping[str, Any] | None,
        new_values: Mapping[str, Any] | None,
    ) -> None:
        """Schreibt einen Protokolleintrag.

        Wird von den Repositories innerhalb ihrer Transaktion aufgerufen. Als
        Benutzer wird der an der Datenbank angemeldete Benutzer eingetragen.
        """
        self._db.execute(
            "INSERT INTO audit_log (table_name, record_id, action, old_value, new_value, user_id, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                table_name,
                record_id,
                action.value,
                self._to_json(old_values),
                self._to_json(new_values),
                self._db.current_user_id,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )

    def list_entries(self, limit: int = 500, table_name: str | None = None) -> list[AuditEntry]:
        """Gibt die neuesten Protokolleinträge zurück (neueste zuerst)."""
        sql = (
            "SELECT a.*, COALESCE(u.name, '') AS user_name "
            "FROM audit_log AS a LEFT JOIN users AS u ON u.user_id = a.user_id"
        )
        parameters: list[Any] = []
        if table_name:
            sql += " WHERE a.table_name = ?"
            parameters.append(table_name)
        sql += " ORDER BY a.log_id DESC LIMIT ?"
        parameters.append(limit)

        return [
            AuditEntry(
                id=row["log_id"],
                table_name=row["table_name"],
                record_id=row["record_id"],
                action=AuditAction(row["action"]),
                old_value=row["old_value"],
                new_value=row["new_value"],
                user_id=row["user_id"],
                user_name=row["user_name"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
            )
            for row in self._db.query_all(sql, parameters)
        ]

    def list_table_names(self) -> list[str]:
        """Alle Tabellennamen, zu denen Protokolleinträge existieren."""
        rows = self._db.query_all("SELECT DISTINCT table_name FROM audit_log ORDER BY table_name")
        return [row["table_name"] for row in rows]

    @staticmethod
    def _to_json(values: Mapping[str, Any] | None) -> str | None:
        """Wandelt Spaltenwerte in JSON um.

        Binärdaten (Fotos) werden nicht ins Protokoll kopiert - das würde die
        Datenbank unnötig aufblähen. Stattdessen wird nur die Größe vermerkt.
        """
        if values is None:
            return None
        printable = {
            key: f"<{len(value)} Bytes>" if isinstance(value, (bytes, bytearray)) else value
            for key, value in values.items()
        }
        return json.dumps(printable, ensure_ascii=False)
