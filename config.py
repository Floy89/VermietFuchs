"""Zentrale Konfiguration: Anwendungsname, Version und alle wichtigen Pfade.

Dieses Modul kennt als einziges den Unterschied zwischen "läuft als
Python-Skript" und "läuft als PyInstaller-.exe". Alle anderen Module fragen
hier nach Pfaden, statt selbst mit ``sys`` zu hantieren.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Final

APP_NAME: Final[str] = "VermietFuchs"
#: Kurzbeschreibung unter dem Namen (Über-Dialog).
APP_TAGLINE: Final[str] = "Clever vermieten. Einfach verwalten."
APP_VERSION: Final[str] = "1.0.0"

#: Dateiname der SQLite-Datenbank in jedem Benutzerordner.
DATABASE_FILE_NAME: Final[str] = "benutzer.db"

#: Dateiname der zentralen Benutzer-Registry neben der .exe.
CONFIG_FILE_NAME: Final[str] = "config.json"

#: Maximale Größe eines Fotos, das in die Datenbank übernommen wird.
MAX_PHOTO_BYTES: Final[int] = 15 * 1024 * 1024
#: Größter Beleg (PDF/Foto), der an eine Rechnung gehängt werden kann.
MAX_ATTACHMENT_BYTES: Final[int] = 25 * 1024 * 1024


def is_frozen() -> bool:
    """Gibt ``True`` zurück, wenn die Anwendung als PyInstaller-.exe läuft."""
    return bool(getattr(sys, "frozen", False))


def application_dir() -> Path:
    """Ordner, in dem die .exe (bzw. im Entwicklungsmodus ``main.py``) liegt.

    Hier werden ``config.json`` und der Ordner ``logs`` abgelegt, damit die
    Anwendung portabel bleibt (z. B. auf einem USB-Stick).
    """
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_dir() -> Path:
    """Ordner mit den mitgelieferten Ressourcen (z. B. dem Stylesheet).

    PyInstaller entpackt eine Single-File-.exe beim Start in einen temporären
    Ordner und legt dessen Pfad in ``sys._MEIPASS`` ab. Im Entwicklungsmodus
    ist es einfach der Projektordner.
    """
    bundle_dir = getattr(sys, "_MEIPASS", None)
    if bundle_dir:
        return Path(bundle_dir)
    return Path(__file__).resolve().parent


def config_file() -> Path:
    """Pfad zur zentralen ``config.json`` (Liste aller Benutzer)."""
    return application_dir() / CONFIG_FILE_NAME


def log_dir() -> Path:
    """Ordner für die Logdateien."""
    return application_dir() / "logs"
