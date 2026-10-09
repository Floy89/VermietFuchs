"""Oberfläche der optionalen Belegerkennung: Einrichtung und Scan-Ablauf."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Generic, Sequence, TypeVar

from PyQt6.QtCore import Qt, QThread
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QProgressDialog,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

import config
from integrations.ollama_client import OllamaReceiptScanner
from models.enums import MeterType
from models.exceptions import AppError
from models.receipt import MeterSuggestion, ReceiptSuggestion
from services.receipt_scan_service import ReceiptScanService
from ui import styles
from ui.helpers import ErrorGuard, make_button
from ui.receipt_images import load_receipt_images, receipt_file_filter
from utils.config_manager import ReceiptScanConfig

logger = logging.getLogger(__name__)

R = TypeVar("R")

#: Hinweis, den die Oberfläche zeigt, solange die Belegerkennung nicht eingerichtet ist.
OPTIONAL_HINT = (
    "Optional: Rechnungen (PDF/Foto) automatisch auslesen lassen. "
    "Einrichtung über das Benutzer-Menü → Belegerkennung …"
)


def guide_path() -> Path:
    """Pfad der mitgelieferten Installationsanleitung."""
    return config.resource_dir() / "ui" / "resources" / "belegerkennung.md"


# ---------------------------------------------------------------------- #
# Scan-Ablauf
# ---------------------------------------------------------------------- #
class _ScanWorker(QThread, Generic[R]):
    """Führt die Erkennung in einem eigenen Thread aus.

    Das Auslesen kann Minuten dauern. Liefe es im Hauptthread, wäre das
    Fenster so lange eingefroren ("Keine Rückmeldung").
    """

    def __init__(self, task: Callable[[], R]) -> None:
        """
        Args:
            task: Die eigentliche Erkennung (ein Aufruf des Services).
        """
        super().__init__()
        self._task = task
        #: Ergebnis bei Erfolg.
        self.suggestion: R | None = None
        #: Fehlermeldung bei Misserfolg.
        self.error: str | None = None

    def run(self) -> None:  # noqa: D401 (Qt-Methode)
        """Wird von Qt im neuen Thread aufgerufen."""
        try:
            self.suggestion = self._task()
        except AppError as exc:
            self.error = str(exc)
        except Exception as exc:  # Ein Thread darf nie mit einer Ausnahme enden.
            logger.exception("Unerwarteter Fehler bei der Belegerkennung")
            self.error = f"Unerwarteter Fehler: {exc}"


def scan_receipt_interactively(
    parent: QWidget,
    service: ReceiptScanService,
    cost_type_names: Sequence[str],
) -> ReceiptSuggestion | None:
    """Fragt nach einer Belegdatei, lässt sie auslesen und gibt den Vorschlag zurück.

    Während der Erkennung zeigt ein Wartefenster, dass gearbeitet wird.

    Returns:
        Den Vorschlag - oder ``None``, wenn der Benutzer abgebrochen hat oder
        die Erkennung fehlgeschlagen ist (die Fehlermeldung wurde dann bereits
        angezeigt).
    """
    file_name, _ = QFileDialog.getOpenFileName(parent, "Beleg auswählen", "", receipt_file_filter())
    if not file_name:
        return None
    return scan_receipt_file(parent, service, cost_type_names, Path(file_name))


def scan_receipt_file(
    parent: QWidget,
    service: ReceiptScanService,
    cost_type_names: Sequence[str],
    path: Path,
) -> ReceiptSuggestion | None:
    """Lässt eine bereits gewählte Belegdatei (PDF oder Bild) auslesen.

    Returns:
        Den Vorschlag - oder ``None``, wenn die Erkennung fehlgeschlagen ist
        (die Fehlermeldung wurde dann bereits angezeigt).
    """
    images: list[bytes] = []
    with ErrorGuard(parent) as guard:
        images = load_receipt_images(path)
    if not guard.ok:
        return None
    return _run_with_progress(parent, "Der Beleg wird ausgelesen …", lambda: service.scan(images, cost_type_names))


def scan_meter_photo(
    parent: QWidget,
    service: ReceiptScanService,
    path: Path,
    meter_type: MeterType,
) -> MeterSuggestion | None:
    """Lässt den Zählerstand von einem Foto ablesen.

    Returns:
        Den Vorschlag - oder ``None``, wenn die Erkennung fehlgeschlagen ist
        (die Fehlermeldung wurde dann bereits angezeigt).
    """
    images: list[bytes] = []
    with ErrorGuard(parent) as guard:
        images = load_receipt_images(path)
    if not guard.ok:
        return None
    return _run_with_progress(
        parent, "Der Zählerstand wird abgelesen …", lambda: service.scan_meter(images, meter_type)
    )


def _run_with_progress(parent: QWidget, title: str, task: Callable[[], R]) -> R | None:
    """Führt eine Erkennung im Hintergrund aus und zeigt so lange ein Wartefenster.

    Returns:
        Das Ergebnis - oder ``None`` nach einem Fehler (er wurde bereits angezeigt).
    """
    progress = QProgressDialog(
        f"{title}\n\nDas kann je nach Rechner einige Minuten dauern,\nbeim ersten Mal am längsten.",
        "",
        0,
        0,  # 0..0 = Laufbalken ohne Prozentangabe
        parent,
    )
    progress.setWindowTitle("Belegerkennung")
    progress.setCancelButton(None)  # Eine laufende Erkennung lässt sich nicht sauber abbrechen.
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setMinimumDuration(0)

    worker: _ScanWorker[R] = _ScanWorker(task)
    worker.finished.connect(progress.close)
    worker.start()
    progress.exec()
    worker.wait()

    if worker.error is not None:
        with ErrorGuard(parent):
            raise AppError(worker.error)
        return None
    return worker.suggestion


# ---------------------------------------------------------------------- #
# Einrichtung
# ---------------------------------------------------------------------- #
class ReceiptScanSetupDialog(QDialog):
    """Verbindungseinstellungen, Statusprüfung und die Installationsanleitung."""

    def __init__(self, service: ReceiptScanService, parent: QWidget | None = None) -> None:
        """
        Args:
            service: Service der Belegerkennung.
        """
        super().__init__(parent)
        self._service = service
        self.setWindowTitle("Belegerkennung (optional)")
        self.resize(820, 720)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(12)

        self._status_label = QLabel()
        self._status_label.setWordWrap(True)
        self._status_label.setObjectName("SectionTitle")
        layout.addWidget(self._status_label)

        settings = service.settings()
        self._url_edit = QLineEdit(settings.url)
        self._model_edit = QLineEdit(settings.model)
        self._privacy_label = QLabel()
        self._privacy_label.setObjectName("WarningBox")
        self._privacy_label.setWordWrap(True)
        form = QFormLayout()
        form.setHorizontalSpacing(16)
        form.addRow("Adresse von Ollama", self._url_edit)
        form.addRow("Modell", self._model_edit)
        layout.addLayout(form)
        layout.addWidget(self._privacy_label)

        check_button = make_button("Verbindung prüfen", "gauge")
        save_button = make_button("Speichern", variant="primary")
        close_button = make_button("Schließen")
        buttons = QHBoxLayout()
        buttons.addWidget(check_button)
        buttons.addStretch(1)
        buttons.addWidget(save_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        guide = QTextBrowser()
        guide.setOpenExternalLinks(True)
        try:
            guide.setMarkdown(guide_path().read_text(encoding="utf-8"))
        except OSError:
            logger.exception("Anleitung zur Belegerkennung nicht gefunden")
            guide.setPlainText("Die Anleitung wurde nicht gefunden. Siehe README, Abschnitt Belegerkennung.")
        layout.addWidget(guide, 1)

        check_button.clicked.connect(self._check)
        save_button.clicked.connect(self._save)
        close_button.clicked.connect(self.accept)
        self._url_edit.textChanged.connect(lambda *_: self._update_privacy_hint())
        self._update_privacy_hint()
        self._check()

    def _entered_settings(self) -> ReceiptScanConfig:
        """Die aktuell im Dialog eingetragenen (noch nicht gespeicherten) Werte."""
        return ReceiptScanConfig(url=self._url_edit.text().strip(), model=self._model_edit.text().strip())

    def _update_privacy_hint(self) -> None:
        """Warnt, sobald die Adresse nicht auf den eigenen Rechner zeigt."""
        is_local = OllamaReceiptScanner.is_local(self._url_edit.text().strip())
        self._privacy_label.setVisible(not is_local)
        self._privacy_label.setText(
            "Achtung: Diese Adresse zeigt nicht auf diesen Rechner. "
            "Belege würden beim Scannen dorthin übertragen."
        )

    def _check(self) -> None:
        """Prüft die eingetragenen Werte und zeigt das Ergebnis farbig an."""
        status = self._service.status(self._entered_settings())
        colour = styles.color("success" if status.available else "muted")
        prefix = "✓ " if status.available else "Nicht eingerichtet – "
        self._status_label.setText(prefix + status.message)
        self._status_label.setStyleSheet(f"color: {colour};")

    def _save(self) -> None:
        """Speichert die Einstellungen und prüft sie anschließend."""
        with ErrorGuard(self) as guard:
            self._service.save_settings(self._entered_settings())
        if guard.ok:
            self._check()
