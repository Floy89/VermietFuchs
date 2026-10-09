"""Repository für Benutzer."""

from __future__ import annotations

import sqlite3
from datetime import datetime
from typing import Any

from models.user import User
from repositories.base_repository import SqliteRepository


class SqliteUserRepository(SqliteRepository[User]):
    """Zugriff auf die Tabelle ``users``."""

    table_name = "users"
    id_column = "user_id"
    default_order = "t.name COLLATE NOCASE"
    entity_label = "Benutzer"

    def find_by_name(self, name: str) -> User | None:
        """Sucht einen Benutzer anhand seines Namens (Groß-/Kleinschreibung egal)."""
        users = self._select("t.name = ? COLLATE NOCASE", (name,))
        return users[0] if users else None

    def touch_last_login(self, user_id: int, when: datetime) -> None:
        """Aktualisiert den Zeitpunkt der letzten Anmeldung.

        Bewusst ohne Protokolleintrag: Eine Anmeldung ist keine Datenänderung
        und würde das Änderungsprotokoll nur mit Rauschen füllen.
        """
        self._db.execute(
            "UPDATE users SET last_login = ? WHERE user_id = ?",
            (when.isoformat(timespec="seconds"), user_id),
        )

    def _to_row(self, entity: User) -> dict[str, Any]:
        return {
            "name": entity.name,
            "created_at": entity.created_at.isoformat(timespec="seconds"),
            "last_login": entity.last_login.isoformat(timespec="seconds") if entity.last_login else None,
        }

    def _from_row(self, row: sqlite3.Row) -> User:
        return User(
            id=row["user_id"],
            name=row["name"],
            created_at=datetime.fromisoformat(row["created_at"]),
            last_login=datetime.fromisoformat(row["last_login"]) if row["last_login"] else None,
        )
