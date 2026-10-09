"""Einstiegspunkt der Anwendung.

Start im Entwicklungsmodus::

    python main.py

Ablauf: Logging einrichten → Qt starten → Benutzer wählen → Hauptfenster.
"Benutzer wechseln" schließt das Hauptfenster und zeigt wieder die
Benutzerauswahl.
"""

from __future__ import annotations

import logging
import sys

from PyQt6.QtCore import QLibraryInfo, QLocale, QTranslator
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox

import config
from integrations.ollama_client import OllamaReceiptScanner
from services.receipt_scan_service import ReceiptScanService
from services.user_service import UserService
from ui import icons, styles
from ui.helpers import dispose_all_widgets
from ui.dialogs.load_user_dialog import LoadUserDialog
from ui.main_window import MainWindow
from utils.config_manager import ConfigManager
from utils.logger import setup_logging

logger = logging.getLogger(__name__)


class Application:
    """Steuert den Lebenszyklus: Benutzerauswahl ↔ Hauptfenster."""

    def __init__(self, qt_app: QApplication) -> None:
        """
        Args:
            qt_app: Die laufende ``QApplication``.
        """
        self._qt_app = qt_app
        config_manager = ConfigManager(config.config_file())
        # Die Belegerkennung ist optional: Der Service existiert immer, meldet
        # aber "nicht verfügbar", solange der Benutzer nichts eingerichtet hat.
        receipt_scan = ReceiptScanService(config_manager, OllamaReceiptScanner)
        # Verfügbarkeit schon jetzt im Hintergrund prüfen - dann liegt das Ergebnis
        # vor, wenn der erste Dialog geöffnet wird.
        receipt_scan.quick_status()
        self._users = UserService(config_manager, receipt_scan)
        self._window: MainWindow | None = None

    def start(self) -> bool:
        """Zeigt die Benutzerauswahl und danach das Hauptfenster.

        Returns:
            ``False``, wenn der Benutzer die Auswahl abgebrochen hat - die
            Anwendung soll dann beendet werden.
        """
        styles.apply_theme(self._qt_app, styles.THEME_LIGHT)
        dialog = LoadUserDialog(self._users)
        if dialog.exec() != QDialog.DialogCode.Accepted or dialog.context is None:
            return False

        context = dialog.context
        styles.apply_theme(self._qt_app, context.settings.theme())
        self._window = MainWindow(context)
        self._window.switch_user_requested.connect(self._switch_user)
        self._window.show()
        return True

    def _switch_user(self) -> None:
        """Schließt das Hauptfenster (und damit die Datenbank) und beginnt von vorn."""
        if self._window is not None:
            # Verhindern, dass Qt beim Schließen des letzten Fensters die
            # gesamte Anwendung beendet.
            self._qt_app.setQuitOnLastWindowClosed(False)
            self._window.close()
            self._window = None
        started = self.start()
        self._qt_app.setQuitOnLastWindowClosed(True)
        if not started:
            self._qt_app.quit()


def _install_german_translation(qt_app: QApplication) -> None:
    """Stellt Qt-eigene Texte (z. B. Dateidialoge, Kalender) auf Deutsch um."""
    QLocale.setDefault(QLocale(QLocale.Language.German, QLocale.Country.Germany))
    translator = QTranslator(qt_app)
    translations_dir = QLibraryInfo.path(QLibraryInfo.LibraryPath.TranslationsPath)
    if translator.load("qtbase_de", translations_dir):
        qt_app.installTranslator(translator)
    else:
        logger.warning("Deutsche Qt-Übersetzung nicht gefunden in %s", translations_dir)


def _handle_uncaught_exception(exc_type, exc, traceback) -> None:  # type: ignore[no-untyped-def]
    """Letztes Sicherheitsnetz: loggt jeden nicht abgefangenen Fehler.

    Ohne diesen Haken würde PyQt6 die Anwendung bei einer Ausnahme in einem
    Signal-Handler kommentarlos beenden.
    """
    logger.critical("Nicht abgefangener Fehler", exc_info=(exc_type, exc, traceback))
    QMessageBox.critical(
        None,
        "Unerwarteter Fehler",
        f"Es ist ein unerwarteter Fehler aufgetreten:\n\n{exc}\n\n"
        "Details stehen in der Logdatei im Ordner 'logs'.",
    )


def main() -> int:
    """Startet die Anwendung und gibt den Exit-Code zurück."""
    setup_logging(config.log_dir())
    logger.info("%s %s wird gestartet.", config.APP_NAME, config.APP_VERSION)

    qt_app = QApplication(sys.argv)
    qt_app.setApplicationName(config.APP_NAME)
    if sys.platform == "win32":
        # Eigene Kennung für die Taskleiste: Sonst zeigt Windows beim Start über
        # python.exe das Python-Symbol statt des App-Symbols.
        import ctypes

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Floy89.VermietFuchs")
    qt_app.setWindowIcon(icons.app_icon())
    qt_app.setApplicationVersion(config.APP_VERSION)
    _install_german_translation(qt_app)
    sys.excepthook = _handle_uncaught_exception

    application = Application(qt_app)
    exit_code = qt_app.exec() if application.start() else 0
    dispose_all_widgets(qt_app)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
