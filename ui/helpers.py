"""Kleine Helfer für die Oberfläche: Fehlerbehandlung, Rückfragen, Schaltflächen, Auswahlfelder."""

from __future__ import annotations

import logging
from datetime import date
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Iterable, Sequence

from PyQt6.QtCore import QDate, QEvent, QMimeData, QObject, QSize, QTimer
from PyQt6.QtWidgets import QApplication, QComboBox, QMessageBox, QPushButton, QWidget

from models.exceptions import AppError
from ui import icons, styles

logger = logging.getLogger(__name__)


class ErrorGuard:
    """Kontextmanager, der Fehler abfängt und dem Benutzer anzeigt.

    Verwendung::

        with ErrorGuard(self) as guard:
            self._context.properties.delete_property(property_id)
        if guard.ok:
            self.refresh()

    * Erwartete Fehler (:class:`AppError`, z. B. eine verletzte fachliche
      Regel) erscheinen als Hinweis mit ihrer deutschen Meldung.
    * Alle anderen Ausnahmen sind Programmfehler: Sie werden vollständig
      geloggt und als Fehlerdialog gemeldet - die Anwendung läuft weiter.
    """

    def __init__(self, parent: QWidget | None) -> None:
        self._parent = parent
        #: ``True``, wenn der Block ohne Fehler durchgelaufen ist.
        self.ok = False

    def __enter__(self) -> ErrorGuard:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> bool:
        if exc is None:
            self.ok = True
            return False
        if isinstance(exc, AppError):
            logger.warning("Vorgang abgelehnt: %s", exc)
            QMessageBox.warning(self._parent, "Hinweis", str(exc))
            return True  # Ausnahme gilt als behandelt
        if isinstance(exc, Exception):
            logger.error("Unerwarteter Fehler", exc_info=(type(exc), exc, traceback))
            QMessageBox.critical(
                self._parent,
                "Unerwarteter Fehler",
                f"Es ist ein unerwarteter Fehler aufgetreten:\n\n{exc}\n\n"
                "Details stehen in der Logdatei im Ordner 'logs'.",
            )
            return True
        return False  # KeyboardInterrupt usw. nicht verschlucken


def confirm(parent: QWidget | None, title: str, text: str, confirm_label: str = "Löschen") -> bool:
    """Zeigt eine Sicherheitsabfrage mit deutschem Bestätigen-/Abbrechen-Knopf.

    Returns:
        ``True``, wenn der Benutzer bestätigt hat.
    """
    box = QMessageBox(parent)
    box.setIcon(QMessageBox.Icon.Question)
    box.setWindowTitle(title)
    box.setText(text)
    confirm_button = box.addButton(confirm_label, QMessageBox.ButtonRole.AcceptRole)
    cancel_button = box.addButton("Abbrechen", QMessageBox.ButtonRole.RejectRole)
    box.setDefaultButton(cancel_button)
    box.exec()
    return box.clickedButton() is confirm_button


def make_button(
    text: str,
    icon_name: str | None = None,
    variant: str | None = None,
    tooltip: str = "",
) -> QPushButton:
    """Erzeugt eine einheitlich gestaltete Schaltfläche.

    Args:
        text: Beschriftung.
        icon_name: Optionales Icon aus :mod:`ui.icons`.
        variant: ``"primary"`` (hervorgehoben), ``"danger"`` (löschen) oder ``None``.
        tooltip: Optionaler Hinweistext.
    """
    button = QPushButton(text)
    if variant:
        # Das Stylesheet wertet diese Eigenschaft aus: QPushButton[variant="primary"]
        button.setProperty("variant", variant)
    if icon_name:
        if variant == "primary":
            icon_color = "#FFFFFF"
        elif variant == "danger":
            icon_color = styles.color("danger")
        else:
            icon_color = styles.color("text")
        button.setIcon(icons.icon(icon_name, icon_color))
        button.setIconSize(QSize(16, 16))
    if tooltip:
        button.setToolTip(tooltip)
    return button


def fill_combo(combo: QComboBox, items: Iterable[tuple[str, Any]]) -> None:
    """Befüllt ein Auswahlfeld neu und stellt die vorherige Auswahl wieder her.

    Während des Befüllens sind die Signale blockiert, damit kein
    ``currentIndexChanged`` mit Zwischenständen ausgelöst wird.

    Args:
        combo: Das Auswahlfeld.
        items: Paare aus (angezeigter Text, zugehörige Daten).
    """
    previous = combo.currentData()
    combo.blockSignals(True)
    combo.clear()
    for label, data in items:
        combo.addItem(label, data)
    index = combo.findData(previous) if previous is not None else -1
    combo.setCurrentIndex(index if index >= 0 else 0)
    combo.blockSignals(False)


def select_data(combo: QComboBox, data: Any) -> None:
    """Wählt den Eintrag mit den angegebenen Daten aus (falls vorhanden)."""
    index = combo.findData(data)
    if index >= 0:
        combo.setCurrentIndex(index)


def to_qdate(value: date) -> QDate:
    """Wandelt ein Python-Datum in ein ``QDate`` um."""
    return QDate(value.year, value.month, value.day)


def to_date(value: QDate) -> date:
    """Wandelt ein ``QDate`` in ein Python-Datum um."""
    return date(value.year(), value.month(), value.day())


def dropped_files(mime_data: QMimeData | None, extensions: Sequence[str]) -> list[Path]:
    """Die per Drag-and-drop abgelegten lokalen Dateien mit einer der Endungen.

    Args:
        mime_data: Inhalt des Drag-Vorgangs.
        extensions: Erlaubte Endungen in Kleinbuchstaben mit Punkt, z. B. ``(".jpg", ".png")``.
    """
    if mime_data is None or not mime_data.hasUrls():
        return []
    paths = [Path(url.toLocalFile()) for url in mime_data.urls() if url.isLocalFile()]
    return [path for path in paths if path.suffix.lower() in extensions]


def dispose_all_widgets(qt_app: QApplication) -> None:
    """Räumt alle Fenster geordnet ab, bevor das Programm endet.

    Beim Beenden baut Python seine Objekte in zufälliger Reihenfolge ab. Lebt
    dann noch ein Fenster, während die ``QApplication`` schon weg ist - oder
    wird beim Abbau eines Fensters noch Python-Code aufgerufen (etwa ein
    Ereignisfilter) -, kann der Prozess abstürzen. Deshalb werden alle
    Fenster hier gelöscht, solange noch alles intakt ist.
    """
    for widget in qt_app.topLevelWidgets():
        widget.deleteLater()
    # deleteLater() wirkt erst, wenn die zugehörigen Ereignisse verarbeitet sind.
    qt_app.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    qt_app.processEvents()


class FileDropFilter(QObject):
    """Macht ein Fenster oder einen Bereich samt aller Kind-Widgets zum Ablageziel für Dateien.

    Warum ein Ereignisfilter statt ``dropEvent`` am Fenster: Eingabefelder
    (Textzeilen, Datums- und Zahlenfelder) nehmen Drag-and-drop selbst an.
    Zieht man eine Datei über ein solches Feld, käme das Ereignis nie beim
    Fenster an - das Feld würde es ablehnen oder den Dateipfad als Text
    einfügen. Der Filter hängt deshalb an jedem Kind-Widget (auch an später
    hinzugefügten) und fängt Datei-Drops überall im Ziel ab.

    Die Rückruffunktion läuft erst *nach* dem Drop-Ereignis. Sonst würde ein
    Dialog, den sie öffnet, das Programm blockieren, aus dem gezogen wurde
    (z. B. den Explorer), bis der Dialog wieder geschlossen ist.
    """

    def __init__(
        self,
        target: QWidget,
        extensions: Sequence[str],
        on_drop: Callable[[list[Path]], None],
    ) -> None:
        """
        Args:
            target: Fenster oder Bereich, der Dateien annehmen soll.
            extensions: Erlaubte Endungen in Kleinbuchstaben mit Punkt.
            on_drop: Wird mit den abgelegten Dateien aufgerufen.
        """
        super().__init__(target)
        self._target = target
        self._extensions = tuple(extensions)
        self._on_drop = on_drop
        target.setAcceptDrops(True)
        self._watch(target)

    def _watch(self, widget: QObject) -> None:
        """Hängt den Filter an ein Widget und alle seine Nachfahren."""
        widget.installEventFilter(self)
        for child in widget.findChildren(QWidget):
            child.installEventFilter(self)

    def eventFilter(self, watched: QObject | None, event: QEvent | None) -> bool:  # noqa: N802 (Qt-Name)
        if watched is None or event is None:
            return False
        kind = event.type()
        if kind == QEvent.Type.ChildAdded:
            child = event.child()  # type: ignore[attr-defined]
            if child is not None and child.isWidgetType():
                self._watch(child)
            return False
        if kind not in (QEvent.Type.DragEnter, QEvent.Type.DragMove, QEvent.Type.Drop):
            return False
        # Eigene Fenster von Kindern (z. B. ein geöffneter Unterdialog) gehören nicht zum Ziel.
        if isinstance(watched, QWidget) and watched.window() is not self._target.window():
            return False
        files = dropped_files(event.mimeData(), self._extensions)  # type: ignore[attr-defined]
        if not files:
            return False
        event.acceptProposedAction()  # type: ignore[attr-defined]
        if kind == QEvent.Type.Drop:
            QTimer.singleShot(0, lambda: self._on_drop(files))
        return True
