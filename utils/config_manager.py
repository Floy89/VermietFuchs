"""Verwaltung der zentralen ``config.json`` (Liste aller Benutzer und ihrer DB-Pfade).

Aufbau der Datei::

    {
      "users": [
        {
          "name": "Hausverwaltung_Meier",
          "database_path": "D:\\\\Backups\\\\...\\\\benutzer.db",
          "created": "2026-10-07"
        }
      ],
      "last_user": "Hausverwaltung_Meier",
      "receipt_scan": {"url": "http://localhost:11434", "model": "qwen3-vl:4b-instruct"}
    }

``receipt_scan`` gehört zur optionalen Belegerkennung. Die Angaben stehen hier
und nicht in der Benutzer-Datenbank, weil sie vom *Rechner* abhängen: Eine
Datenbank auf dem NAS kann von mehreren Rechnern geöffnet werden, das lokale
KI-Modell ist aber nur dort vorhanden, wo es eingerichtet wurde.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from models.exceptions import ConfigError, ValidationError

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class UserEntry:
    """Ein Eintrag der Benutzerliste in ``config.json``."""

    name: str
    database_path: Path
    created: date

    @property
    def database_exists(self) -> bool:
        """``True``, wenn die Datenbankdatei aktuell erreichbar ist.

        Liegt die Datei auf einem NAS oder USB-Laufwerk, das gerade nicht
        verbunden ist, liefert dies ``False`` - der Eintrag bleibt aber
        erhalten.
        """
        try:
            return self.database_path.is_file()
        except OSError:
            return False


@dataclass(frozen=True)
class ReceiptScanConfig:
    """Verbindung zur optionalen Belegerkennung (lokaler Ollama-Dienst)."""

    url: str = "http://localhost:11434"
    model: str = "qwen3-vl:4b-instruct"


class ConfigManager:
    """Liest und schreibt die Benutzer-Registry.

    Die Klasse hält keinen Zustand im Speicher: Jede Operation liest die Datei
    frisch ein. Das ist bei der winzigen Datei unkritisch und verhindert, dass
    Änderungen von außen (z. B. ein zurückgespieltes Backup) überschrieben
    werden.
    """

    def __init__(self, path: Path) -> None:
        """
        Args:
            path: Vollständiger Pfad zur ``config.json``.
        """
        self._path = path

    @property
    def path(self) -> Path:
        """Pfad der verwalteten Konfigurationsdatei."""
        return self._path

    # ------------------------------------------------------------------ #
    # Öffentliche Operationen
    # ------------------------------------------------------------------ #
    def list_users(self) -> list[UserEntry]:
        """Gibt alle registrierten Benutzer zurück (alphabetisch sortiert)."""
        users = [self._entry_from_json(raw) for raw in self._read().get("users", [])]
        return sorted(users, key=lambda entry: entry.name.casefold())

    def find_user(self, name: str) -> UserEntry | None:
        """Sucht einen Benutzer anhand seines Namens (Groß-/Kleinschreibung egal)."""
        wanted = name.casefold()
        return next((u for u in self.list_users() if u.name.casefold() == wanted), None)

    def add_user(self, entry: UserEntry) -> None:
        """Trägt einen neuen Benutzer ein.

        Raises:
            ValidationError: Wenn es bereits einen Benutzer mit diesem Namen gibt.
        """
        if self.find_user(entry.name) is not None:
            raise ValidationError(f"Es gibt bereits einen Benutzer namens '{entry.name}'.")
        data = self._read()
        data.setdefault("users", []).append(self._entry_to_json(entry))
        self._write(data)
        logger.info("Benutzer '%s' registriert (%s).", entry.name, entry.database_path)

    def remove_user(self, name: str) -> None:
        """Entfernt einen Benutzer aus der Liste. Die Datenbankdatei bleibt unberührt."""
        data = self._read()
        wanted = name.casefold()
        data["users"] = [
            raw for raw in data.get("users", []) if str(raw.get("name", "")).casefold() != wanted
        ]
        if str(data.get("last_user", "")).casefold() == wanted:
            data.pop("last_user", None)
        self._write(data)
        logger.info("Benutzer '%s' aus der Liste entfernt.", name)

    def last_user(self) -> str | None:
        """Name des zuletzt geöffneten Benutzers (für die Vorauswahl im Startdialog)."""
        value = self._read().get("last_user")
        return str(value) if value else None

    def set_last_user(self, name: str) -> None:
        """Merkt sich den zuletzt geöffneten Benutzer."""
        data = self._read()
        data["last_user"] = name
        self._write(data)

    def receipt_scan_config(self) -> ReceiptScanConfig:
        """Einstellungen der Belegerkennung; fehlende Angaben werden durch Standardwerte ersetzt."""
        defaults = ReceiptScanConfig()
        raw = self._read().get("receipt_scan")
        if not isinstance(raw, dict):
            return defaults
        return ReceiptScanConfig(
            url=str(raw.get("url") or defaults.url),
            model=str(raw.get("model") or defaults.model),
        )

    def set_receipt_scan_config(self, settings: ReceiptScanConfig) -> None:
        """Speichert die Einstellungen der Belegerkennung."""
        data = self._read()
        data["receipt_scan"] = {"url": settings.url, "model": settings.model}
        self._write(data)

    # ------------------------------------------------------------------ #
    # Interna
    # ------------------------------------------------------------------ #
    def _read(self) -> dict[str, Any]:
        """Liest die Datei; eine fehlende Datei gilt als leere Konfiguration."""
        if not self._path.exists():
            return {"users": []}
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(
                f"Die Konfigurationsdatei '{self._path}' kann nicht gelesen werden: {exc}"
            ) from exc
        if not isinstance(data, dict) or not isinstance(data.get("users", []), list):
            raise ConfigError(f"Die Konfigurationsdatei '{self._path}' hat ein ungültiges Format.")
        return data

    def _write(self, data: dict[str, Any]) -> None:
        """Schreibt die Datei atomar.

        Erst wird eine temporäre Datei vollständig geschrieben, dann ersetzt
        sie in einem Schritt das Original. Ein Absturz mitten im Schreiben
        hinterlässt so nie eine halbe ``config.json``.
        """
        temp_path = self._path.with_suffix(".json.tmp")
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with temp_path.open("w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2, ensure_ascii=False)
            os.replace(temp_path, self._path)
        except OSError as exc:
            raise ConfigError(
                f"Die Konfigurationsdatei '{self._path}' kann nicht geschrieben werden: {exc}"
            ) from exc

    @staticmethod
    def _entry_from_json(raw: dict[str, Any]) -> UserEntry:
        """Wandelt einen JSON-Eintrag in ein ``UserEntry`` um."""
        try:
            return UserEntry(
                name=str(raw["name"]),
                database_path=Path(str(raw["database_path"])),
                created=date.fromisoformat(str(raw["created"])),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ConfigError(f"Ungültiger Benutzereintrag in der Konfiguration: {raw}") from exc

    @staticmethod
    def _entry_to_json(entry: UserEntry) -> dict[str, str]:
        """Wandelt ein ``UserEntry`` in einen JSON-Eintrag um."""
        return {
            "name": entry.name,
            "database_path": str(entry.database_path),
            "created": entry.created.isoformat(),
        }
