"""Generische Basisklasse aller SQLite-Repositories.

Die Basisklasse erledigt alles, was für jede Tabelle gleich ist: Laden,
Einfügen, Ändern, Löschen - und das lückenlose Mitschreiben im
Änderungsprotokoll. Eine konkrete Unterklasse muss nur noch sagen, *wie* ihr
Entity in eine Tabellenzeile übersetzt wird und umgekehrt.
"""

from __future__ import annotations

import dataclasses
import sqlite3
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, Sequence, TypeVar

from database.db_manager import DatabaseManager
from models.enums import AuditAction
from models.exceptions import NotFoundError
from repositories.interfaces import AuditRecorder

T = TypeVar("T")


class SqliteRepository(ABC, Generic[T]):
    """Basisklasse für ein Repository über genau eine Tabelle.

    Unterklassen setzen die Klassenattribute und implementieren
    :meth:`_to_row` und :meth:`_from_row`.

    Attributes:
        table_name: Name der Tabelle.
        id_column: Name der Primärschlüsselspalte.
        default_order: ``ORDER BY``-Ausdruck für :meth:`list_all`.
        entity_label: Deutsche Bezeichnung für Fehlermeldungen.
    """

    table_name: ClassVar[str]
    id_column: ClassVar[str]
    default_order: ClassVar[str]
    entity_label: ClassVar[str] = "Datensatz"

    def __init__(self, database: DatabaseManager, audit: AuditRecorder) -> None:
        """
        Args:
            database: Die geöffnete Benutzer-Datenbank.
            audit: Empfänger für die Einträge des Änderungsprotokolls.
        """
        self._db = database
        self._audit = audit

    # ------------------------------------------------------------------ #
    # Von Unterklassen zu implementieren
    # ------------------------------------------------------------------ #
    @abstractmethod
    def _to_row(self, entity: T) -> dict[str, Any]:
        """Übersetzt ein Entity in Spaltenwerte (ohne Primärschlüssel)."""

    @abstractmethod
    def _from_row(self, row: sqlite3.Row) -> T:
        """Übersetzt eine Tabellenzeile in ein Entity."""

    # ------------------------------------------------------------------ #
    # Lesen
    # ------------------------------------------------------------------ #
    def find(self, entity_id: int) -> T | None:
        """Lädt einen Datensatz oder gibt ``None`` zurück."""
        row = self._fetch_row(entity_id)
        return self._from_row(row) if row is not None else None

    def get(self, entity_id: int) -> T:
        """Lädt einen Datensatz.

        Raises:
            NotFoundError: Wenn es keinen Datensatz mit dieser ID gibt.
        """
        entity = self.find(entity_id)
        if entity is None:
            raise NotFoundError(f"{self.entity_label} (ID {entity_id}) wurde nicht gefunden.")
        return entity

    def list_all(self) -> list[T]:
        """Lädt alle Datensätze in der Standard-Sortierung."""
        return self._select()

    # ------------------------------------------------------------------ #
    # Schreiben (jeweils mit Protokolleintrag in derselben Transaktion)
    # ------------------------------------------------------------------ #
    def add(self, entity: T) -> T:
        """Speichert einen neuen Datensatz.

        Returns:
            Eine Kopie des Entity mit der von der Datenbank vergebenen ID.
        """
        row = self._to_row(entity)
        columns = ", ".join(row)
        placeholders = ", ".join("?" for _ in row)
        with self._db.transaction():
            cursor = self._db.execute(
                f"INSERT INTO {self.table_name} ({columns}) VALUES ({placeholders})",
                tuple(row.values()),
            )
            new_id = int(cursor.lastrowid or 0)
            self._audit.record(self.table_name, new_id, AuditAction.INSERT, None, row)
        # Alle Entities sind Dataclasses mit einem Feld "id".
        return dataclasses.replace(entity, id=new_id)  # type: ignore[type-var]

    def update(self, entity: T) -> None:
        """Überschreibt einen bestehenden Datensatz.

        Raises:
            NotFoundError: Wenn der Datensatz nicht (mehr) existiert.
        """
        entity_id = self._require_id(entity)
        row = self._to_row(entity)
        assignments = ", ".join(f"{column} = ?" for column in row)
        with self._db.transaction():
            old_row = self._require_row(entity_id)
            self._db.execute(
                f"UPDATE {self.table_name} SET {assignments} WHERE {self.id_column} = ?",
                (*row.values(), entity_id),
            )
            old_values = {column: old_row[column] for column in row}
            # Nur protokollieren, wenn sich wirklich etwas geändert hat.
            if old_values != row:
                self._audit.record(self.table_name, entity_id, AuditAction.UPDATE, old_values, row)

    def delete(self, entity_id: int) -> None:
        """Löscht einen Datensatz.

        Raises:
            NotFoundError: Wenn der Datensatz nicht (mehr) existiert.
            IntegrityViolationError: Wenn noch abhängige Datensätze existieren.
        """
        with self._db.transaction():
            old_row = self._require_row(entity_id)
            self._db.execute(
                f"DELETE FROM {self.table_name} WHERE {self.id_column} = ?", (entity_id,)
            )
            old_values = {key: old_row[key] for key in old_row.keys() if key != self.id_column}
            self._audit.record(self.table_name, entity_id, AuditAction.DELETE, old_values, None)

    # ------------------------------------------------------------------ #
    # Hilfsmethoden für Unterklassen
    # ------------------------------------------------------------------ #
    def _select(
        self,
        where: str = "",
        parameters: Sequence[Any] = (),
        order_by: str | None = None,
    ) -> list[T]:
        """Lädt Entities mit optionaler ``WHERE``-Bedingung.

        Die Tabelle erhält den Alias ``t``, damit Unterklassen in ``where``
        eindeutig auf ihre Spalten verweisen können.
        """
        sql = f"SELECT t.* FROM {self.table_name} AS t"
        if where:
            sql += f" WHERE {where}"
        sql += f" ORDER BY {order_by or self.default_order}"
        return [self._from_row(row) for row in self._db.query_all(sql, parameters)]

    def _fetch_row(self, entity_id: int) -> sqlite3.Row | None:
        """Lädt die rohe Tabellenzeile zu einer ID."""
        return self._db.query_one(
            f"SELECT * FROM {self.table_name} WHERE {self.id_column} = ?", (entity_id,)
        )

    def _require_row(self, entity_id: int) -> sqlite3.Row:
        """Wie :meth:`_fetch_row`, wirft aber ``NotFoundError`` statt ``None``."""
        row = self._fetch_row(entity_id)
        if row is None:
            raise NotFoundError(f"{self.entity_label} (ID {entity_id}) wurde nicht gefunden.")
        return row

    def _require_id(self, entity: T) -> int:
        """Gibt die ID eines bereits gespeicherten Entity zurück."""
        entity_id = getattr(entity, "id", None)
        if entity_id is None:
            raise NotFoundError(f"{self.entity_label} wurde noch nicht gespeichert.")
        return int(entity_id)
