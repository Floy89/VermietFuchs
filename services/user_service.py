"""Benutzerverwaltung: anlegen, einbinden, entfernen und anmelden.

Ein "Benutzer" besteht aus zwei Dingen:

* einem Ordner mit der Datei ``benutzer.db`` (kann auf jedem Laufwerk, NAS
  oder USB-Stick liegen) und
* einem Eintrag in der zentralen ``config.json``, der auf diese Datei zeigt.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path

import config
from app_context import AppContext
from database.db_manager import DatabaseManager
from models.exceptions import NotFoundError, ValidationError
from models.user import User, validate_user_name
from repositories.audit_repository import SqliteAuditRepository
from repositories.user_repository import SqliteUserRepository
from services.receipt_scan_service import ReceiptScanService
from utils.config_manager import ConfigManager, UserEntry

logger = logging.getLogger(__name__)


class UserService:
    """Verwaltet die Benutzerliste und öffnet Benutzer-Datenbanken."""

    def __init__(self, config_manager: ConfigManager, receipt_scan: ReceiptScanService | None = None) -> None:
        """
        Args:
            config_manager: Zugriff auf die zentrale ``config.json``.
            receipt_scan: Optionale Belegerkennung; wird an jeden geöffneten
                Benutzer-Kontext weitergereicht.
        """
        self._config = config_manager
        self._receipt_scan = receipt_scan

    # ------------------------------------------------------------------ #
    # Benutzerliste
    # ------------------------------------------------------------------ #
    def list_users(self) -> list[UserEntry]:
        """Alle in der ``config.json`` registrierten Benutzer."""
        return self._config.list_users()

    def last_user_name(self) -> str | None:
        """Name des zuletzt angemeldeten Benutzers."""
        return self._config.last_user()

    def create_user(self, name: str, parent_folder: Path) -> UserEntry:
        """Legt einen neuen Benutzer an.

        Erstellt ``<parent_folder>/<name>/benutzer.db`` mit leerem Schema und
        trägt den Benutzer in die ``config.json`` ein.

        Raises:
            ValidationError: Bei ungültigem Namen, doppeltem Benutzer, nicht
                erreichbarem Ordner oder bereits vorhandener Datenbank.
        """
        name = validate_user_name(name)
        if self._config.find_user(name) is not None:
            raise ValidationError(f"Es gibt bereits einen Benutzer namens '{name}'.")

        user_folder = parent_folder / name
        database_path = user_folder / config.DATABASE_FILE_NAME
        if database_path.exists():
            raise ValidationError(
                f"Im Ordner '{user_folder}' liegt bereits eine Datenbank. "
                "Bitte 'Vorhandene Datenbank einbinden' verwenden."
            )
        try:
            user_folder.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise ValidationError(f"Der Ordner '{user_folder}' kann nicht angelegt werden: {exc}") from exc

        # Datenbank anlegen (open() erzeugt das Schema) und Besitzer eintragen.
        database = DatabaseManager(database_path)
        database.open()
        try:
            self._ensure_owner(database, name)
        finally:
            database.close()

        entry = UserEntry(name=name, database_path=database_path, created=date.today())
        self._config.add_user(entry)
        return entry

    def register_existing(self, database_path: Path) -> UserEntry:
        """Bindet eine vorhandene ``benutzer.db`` ein (z. B. nach einem Umzug).

        Der Benutzername wird aus der Datenbank gelesen.

        Raises:
            ValidationError: Wenn die Datei fehlt, keinen Benutzer enthält
                oder der Benutzer bereits registriert ist.
        """
        if not database_path.is_file():
            raise ValidationError(f"Die Datei '{database_path}' wurde nicht gefunden.")

        database = DatabaseManager(database_path)
        database.open()
        try:
            users = SqliteUserRepository(database, SqliteAuditRepository(database)).list_all()
        finally:
            database.close()
        if not users:
            raise ValidationError("Die gewählte Datei enthält keinen Benutzer.")

        entry = UserEntry(name=users[0].name, database_path=database_path, created=users[0].created_at.date())
        self._config.add_user(entry)
        return entry

    def remove_user(self, name: str) -> None:
        """Entfernt einen Benutzer aus der Liste; seine Datenbank bleibt erhalten."""
        self._config.remove_user(name)

    # ------------------------------------------------------------------ #
    # Anmeldung
    # ------------------------------------------------------------------ #
    def open_user(self, entry: UserEntry) -> AppContext:
        """Öffnet die Datenbank eines Benutzers und liefert den fertigen Kontext.

        Raises:
            NotFoundError: Wenn die Datenbankdatei nicht erreichbar ist
                (z. B. NAS nicht verbunden).
            DatabaseError: Wenn die Datei nicht geöffnet werden kann.
        """
        if not entry.database_exists:
            raise NotFoundError(
                f"Die Datenbank '{entry.database_path}' ist nicht erreichbar. "
                "Ist das Laufwerk bzw. der Netzwerkspeicher verbunden?"
            )
        database = DatabaseManager(entry.database_path)
        database.open()
        try:
            user = self._ensure_owner(database, entry.name)
        except Exception:
            database.close()
            raise
        self._config.set_last_user(entry.name)
        logger.info("Benutzer '%s' angemeldet.", entry.name)
        return AppContext(database, user, self._receipt_scan)

    @staticmethod
    def _ensure_owner(database: DatabaseManager, name: str) -> User:
        """Stellt sicher, dass der Benutzer in der Datenbank existiert, und meldet ihn an.

        Setzt außerdem ``database.current_user_id``, damit alle folgenden
        Änderungen im Protokoll diesem Benutzer zugeordnet werden.
        """
        repository = SqliteUserRepository(database, SqliteAuditRepository(database))
        user = repository.find_by_name(name)
        if user is None:
            new_user = User(name=name)
            new_user.validate()
            user = repository.add(new_user)
        assert user.id is not None  # frisch aus der Datenbank geladen
        database.current_user_id = user.id
        now = datetime.now()
        repository.touch_last_login(user.id, now)
        user.last_login = now
        return user
