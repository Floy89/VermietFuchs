"""Einrichtung des Loggings (Datei + Konsole).

Aufruf einmalig beim Programmstart über :func:`setup_logging`. Alle anderen
Module holen sich ihren Logger wie üblich mit ``logging.getLogger(__name__)``.
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s  %(levelname)-8s  %(name)s: %(message)s"
_LOG_FILE_NAME = "nebenkostenabrechnung.log"


def setup_logging(directory: Path, level: int = logging.INFO) -> None:
    """Konfiguriert den Root-Logger.

    Die Logdatei rotiert bei 1 MB und behält drei alte Versionen. Lässt sich
    der Log-Ordner nicht anlegen (z. B. schreibgeschütztes Laufwerk), wird nur
    auf die Konsole geloggt - die Anwendung startet trotzdem.

    Args:
        directory: Ordner, in dem die Logdatei liegen soll.
        level: Mindest-Loglevel.
    """
    root = logging.getLogger()
    root.setLevel(level)
    # Bei mehrfachem Aufruf (z. B. in Tests) keine doppelten Handler anhängen.
    root.handlers.clear()

    formatter = logging.Formatter(_LOG_FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    try:
        directory.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            directory / _LOG_FILE_NAME,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
    except OSError as exc:
        root.warning("Logdatei kann nicht angelegt werden (%s) - nur Konsolen-Logging.", exc)
        return

    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)
