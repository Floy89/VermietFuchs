"""Verwaltung der SQLite-Verbindung zu einer Benutzer-Datenbank.

Der :class:`DatabaseManager` ist die einzige Stelle, die ``sqlite3`` direkt
anspricht. Repositories arbeiten ausschließlich über seine Methoden.
"""

from __future__ import annotations

import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from database import migrations
from models.exceptions import DatabaseError, IntegrityViolationError

logger = logging.getLogger(__name__)

#: Sonderpfad von SQLite für eine reine Speicher-Datenbank (nur für Tests).
IN_MEMORY = ":memory:"


class DatabaseManager:
    """Kapselt eine geöffnete SQLite-Datenbank.

    Wichtige Einstellungen:

    * ``foreign_keys = ON`` - SQLite prüft Fremdschlüssel nur, wenn man es
      ausdrücklich einschaltet.
    * ``journal_mode = DELETE`` - der klassische Rollback-Journal-Modus.
      Der schnellere WAL-Modus funktioniert auf Netzlaufwerken (NAS) nicht
      zuverlässig und würde zusätzliche Dateien neben der ``benutzer.db``
      erzeugen, die beim Backup leicht vergessen werden.
    * ``synchronous = FULL`` - jede Transaktion wird sicher auf den
      Datenträger geschrieben, bevor sie als abgeschlossen gilt.
    """

    def __init__(self, path: Path | str) -> None:
        """
        Args:
            path: Pfad zur ``benutzer.db`` oder :data:`IN_MEMORY`.
        """
        self._path = path
        self._connection: sqlite3.Connection | None = None
        self._transaction_depth = 0
        #: ID des angemeldeten Benutzers; wird ins Änderungsprotokoll geschrieben.
        self.current_user_id: int | None = None

    # ------------------------------------------------------------------ #
    # Lebenszyklus
    # ------------------------------------------------------------------ #
    @property
    def path(self) -> Path | str:
        """Pfad der Datenbankdatei."""
        return self._path

    @property
    def is_open(self) -> bool:
        """``True``, solange die Verbindung besteht."""
        return self._connection is not None

    def open(self) -> None:
        """Öffnet die Datenbank und bringt das Schema auf den neuesten Stand.

        Eine noch nicht existierende Datei wird angelegt.

        Raises:
            DatabaseError: Wenn die Datei nicht geöffnet werden kann.
        """
        if self._connection is not None:
            return
        try:
            # isolation_level=None schaltet die automatische Transaktions-
            # steuerung des sqlite3-Moduls ab. Transaktionen werden
            # ausschließlich über transaction() gesteuert.
            connection = sqlite3.connect(str(self._path), isolation_level=None, timeout=10.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = DELETE")
            connection.execute("PRAGMA synchronous = FULL")
        except sqlite3.Error as exc:
            raise DatabaseError(f"Die Datenbank '{self._path}' kann nicht geöffnet werden: {exc}") from exc

        try:
            file_path = None if self._path == IN_MEMORY else Path(self._path)
            migrations.migrate(connection, file_path)
        except Exception:
            connection.close()
            raise

        self._connection = connection
        logger.info("Datenbank geöffnet: %s", self._path)

    def close(self) -> None:
        """Schließt die Verbindung (mehrfacher Aufruf ist unschädlich)."""
        if self._connection is not None:
            self._connection.close()
            self._connection = None
            self._transaction_depth = 0
            logger.info("Datenbank geschlossen: %s", self._path)

    # ------------------------------------------------------------------ #
    # Transaktionen
    # ------------------------------------------------------------------ #
    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Führt den ``with``-Block in einer Transaktion aus.

        Transaktionen dürfen verschachtelt werden: Nur der äußerste Block
        beginnt und beendet die echte Datenbank-Transaktion. Tritt irgendwo
        eine Ausnahme auf, wird *alles* zurückgerollt. So landen z. B. eine
        Datenänderung und ihr Protokolleintrag immer gemeinsam in der
        Datenbank - oder gar nicht.
        """
        connection = self._require_connection()
        is_outermost = self._transaction_depth == 0
        if is_outermost:
            self._run(connection, "BEGIN IMMEDIATE", ())
        self._transaction_depth += 1
        try:
            yield
        except BaseException:
            self._transaction_depth -= 1
            # Manche Fehler rollt SQLite bereits selbst zurück - deshalb erst
            # prüfen, ob überhaupt noch eine Transaktion offen ist.
            if is_outermost and connection.in_transaction:
                connection.execute("ROLLBACK")
            raise
        else:
            self._transaction_depth -= 1
            if is_outermost:
                self._run(connection, "COMMIT", ())

    # ------------------------------------------------------------------ #
    # SQL-Ausführung
    # ------------------------------------------------------------------ #
    def execute(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Cursor:
        """Führt eine einzelne SQL-Anweisung aus.

        Raises:
            IntegrityViolationError: Bei verletzten Fremdschlüssel- oder
                Eindeutigkeitsregeln.
            DatabaseError: Bei allen anderen Datenbankfehlern.
        """
        return self._run(self._require_connection(), sql, parameters)

    def query_all(self, sql: str, parameters: Sequence[Any] = ()) -> list[sqlite3.Row]:
        """Führt eine Abfrage aus und gibt alle Zeilen zurück."""
        return self.execute(sql, parameters).fetchall()

    def query_one(self, sql: str, parameters: Sequence[Any] = ()) -> sqlite3.Row | None:
        """Führt eine Abfrage aus und gibt die erste Zeile oder ``None`` zurück."""
        return self.execute(sql, parameters).fetchone()

    # ------------------------------------------------------------------ #
    # Interna
    # ------------------------------------------------------------------ #
    def _require_connection(self) -> sqlite3.Connection:
        """Gibt die offene Verbindung zurück oder meldet einen Fehler."""
        if self._connection is None:
            raise DatabaseError("Die Datenbank ist nicht geöffnet.")
        return self._connection

    @staticmethod
    def _run(connection: sqlite3.Connection, sql: str, parameters: Sequence[Any]) -> sqlite3.Cursor:
        """Führt SQL aus und übersetzt sqlite3-Fehler in Anwendungsfehler."""
        try:
            return connection.execute(sql, tuple(parameters))
        except sqlite3.IntegrityError as exc:
            raise IntegrityViolationError(
                "Der Vorgang verletzt eine Verknüpfungs- oder Eindeutigkeitsregel "
                f"der Datenbank ({exc})."
            ) from exc
        except sqlite3.Error as exc:
            logger.exception("Datenbankfehler bei: %s", sql)
            raise DatabaseError(f"Datenbankfehler: {exc}") from exc
