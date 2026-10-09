"""Gemeinsame Test-Fixtures."""

from __future__ import annotations

import os
from typing import Any, Iterator

import pytest

from app_context import AppContext
from database.db_manager import IN_MEMORY, DatabaseManager
from services.user_service import UserService


#: Die eine Qt-Anwendung des Testlaufs. Der Verweis auf Modulebene hält sie bis
#: zum Ende am Leben: Würde sie zwischen zwei Testdateien abgeräumt, während
#: noch Fenster früherer Tests existieren, stürzte der Prozess ab.
_qt_application: Any = None


@pytest.fixture(scope="session")
def qt_app() -> Any:
    """Die Qt-Anwendung für Oberflächen-Tests (ohne sichtbares Fenster)."""
    global _qt_application
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtWidgets import QApplication

    if _qt_application is None:
        _qt_application = QApplication.instance() or QApplication([])
    return _qt_application


@pytest.fixture(autouse=True)
def _close_stuck_dialogs() -> Iterator[None]:
    """Sicherheitsnetz für Oberflächen-Tests: Kein Test darf an einem offenen Dialog hängen bleiben.

    Bleibt ein modaler Dialog länger als ein paar Sekunden offen, wird er
    abgebrochen - der Test schlägt dann an seiner Prüfung fehl, statt den
    ganzen Testlauf zu blockieren. Ohne laufende Qt-Anwendung passiert nichts.
    """
    try:
        from PyQt6.QtCore import QTimer
        from PyQt6.QtWidgets import QApplication, QDialog
    except ImportError:  # Tests der Rechenlogik brauchen kein Qt.
        yield
        return
    if QApplication.instance() is None:
        yield
        return

    def close_modal() -> None:
        dialog = QApplication.activeModalWidget()
        if isinstance(dialog, QDialog):
            dialog.reject()

    watchdog = QTimer()
    watchdog.setInterval(5000)
    watchdog.timeout.connect(close_modal)
    watchdog.start()
    yield
    watchdog.stop()


@pytest.fixture()
def context() -> Iterator[AppContext]:
    """Ein vollständiger Anwendungskontext auf einer leeren Speicher-Datenbank."""
    database = DatabaseManager(IN_MEMORY)
    database.open()
    user = UserService._ensure_owner(database, "Testbenutzer")
    app_context = AppContext(database, user)
    yield app_context
    app_context.close()
