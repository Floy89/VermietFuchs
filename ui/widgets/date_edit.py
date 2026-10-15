"""Datumsfeld, in das man ein Datum einfach eintippen kann - oder es im Kalender wählt.

Warum nicht ``QDateEdit``? Dort wird jede Taste einem Abschnitt (Tag, Monat,
Jahr) zugeordnet. Getippte Daten kamen dadurch verstümmelt oder gar nicht an:
Aus "31.12.2025" wurde der 01.12.2025, "31122025" wurde ignoriert, und ein
Jahr ließ sich nicht überschreiben.

:class:`DateEdit` ist ein gewöhnliches Textfeld mit Kalender-Knopf. Verstanden
werden "31.12.2025", "31.12.25", "31122025", "311225", "3.1.2026",
"31.12." (Jahr bleibt) und "2025-12-31".
"""

from __future__ import annotations

import re
from datetime import date

from PyQt6.QtCore import QDate, QPoint, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QAction, QFocusEvent
from PyQt6.QtWidgets import QCalendarWidget, QLineEdit, QMenu, QWidget, QWidgetAction

from ui import icons, styles

_DOTTED = re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{2}|\d{4})?$")
_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$")


def parse_date(text: str, fallback_year: int, allow_short_year: bool = True) -> date | None:
    """Liest ein getipptes Datum; ``None``, wenn es (noch) keines ist.

    Args:
        fallback_year: Jahr für Eingaben ohne Jahr ("31.12.").
        allow_short_year: Zweistellige oder fehlende Jahre ("31.12.25", "31.12.")
            erlauben. Während des Tippens aus, damit aus "31.12.20" (unterwegs
            zu 2025) nicht kurz der 31.12.2020 wird.
    """
    text = text.strip()
    day = month = year = None
    if match := _DOTTED.match(text):
        day, month = int(match.group(1)), int(match.group(2))
        year_text = match.group(3)
        if year_text is None:
            # "31.12." - beim Tippen noch unvollständig, erst beim Verlassen gilt das bisherige Jahr.
            year = fallback_year if allow_short_year else None
        elif len(year_text) == 4:
            year = int(year_text)
        elif allow_short_year:
            year = 2000 + int(year_text)
    elif match := _ISO.match(text):
        year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
    elif text.isdigit() and len(text) == 8:
        day, month, year = int(text[:2]), int(text[2:4]), int(text[4:])
    elif text.isdigit() and len(text) == 6 and allow_short_year:
        day, month, year = int(text[:2]), int(text[2:4]), 2000 + int(text[4:])
    if day is None or month is None or year is None:
        return None
    try:
        return date(year, month, day)
    except ValueError:
        return None


class DateEdit(QLineEdit):
    """Textfeld für ein Datum (TT.MM.JJJJ) mit Kalender zum Anklicken.

    Signals:
        dateChanged: Das Datum hat sich geändert; Argument ist das ``QDate``
            (wie bei ``QDateEdit``, damit die Dialoge unverändert bleiben).
    """

    dateChanged = pyqtSignal(QDate)

    def __init__(self, value: date | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = value or date.today()
        self.setPlaceholderText("TT.MM.JJJJ")
        self.setToolTip("Datum eintippen (z. B. 31.12.2025 oder 31122025) oder im Kalender wählen")
        self.setMinimumWidth(120)
        self._calendar_action = QAction(icons.icon("calendar", styles.color("muted")), "Kalender", self)
        self._calendar_action.triggered.connect(self._show_calendar)
        self.addAction(self._calendar_action, QLineEdit.ActionPosition.TrailingPosition)
        self.textEdited.connect(self._on_text_edited)
        self.editingFinished.connect(self._finish_editing)
        self._render()

    # ------------------------------------------------------------------ #
    # Werte
    # ------------------------------------------------------------------ #
    def value(self) -> date:
        """Das gewählte Datum."""
        return self._value

    def set_value(self, value: date) -> None:
        """Setzt das Datum; ``dateChanged`` nur, wenn es sich wirklich ändert."""
        changed = value != self._value
        self._value = value
        if not self.hasFocus():
            self._render()
        else:
            self.setText(self._text())
        if changed:
            self.dateChanged.emit(QDate(value.year, value.month, value.day))

    # Kompatibel zu QDateEdit
    def date(self) -> QDate:
        """Das Datum als ``QDate``."""
        return QDate(self._value.year, self._value.month, self._value.day)

    def setDate(self, value: QDate) -> None:  # noqa: N802 (Qt-Name)
        self.set_value(date(value.year(), value.month(), value.day()))

    def lineEdit(self) -> QLineEdit:  # noqa: N802
        """Das Textfeld selbst (bei ``QDateEdit`` war es ein Kind-Widget)."""
        return self

    # ------------------------------------------------------------------ #
    # Eingabe
    # ------------------------------------------------------------------ #
    def focusInEvent(self, event: QFocusEvent | None) -> None:  # noqa: N802 (Qt-Name)
        super().focusInEvent(event)
        if not self.isReadOnly():
            # Alles markieren: Lostippen ersetzt das alte Datum. (Nach dem Klick,
            # sonst hebt der Mausklick die Markierung gleich wieder auf.)
            QTimer.singleShot(0, self.selectAll)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self._finish_editing()

    def _on_text_edited(self, text: str) -> None:
        parsed = parse_date(text, self._value.year, allow_short_year=False)
        self._set_invalid(False)
        if parsed is not None and parsed != self._value:
            self._value = parsed
            self.dateChanged.emit(self.date())

    def _finish_editing(self) -> None:
        """Beim Verlassen: auch Kurzformen übernehmen, Unlesbares verwerfen, formatieren."""
        parsed = parse_date(self.text(), self._value.year)
        if parsed is not None and parsed != self._value:
            self._value = parsed
            self.dateChanged.emit(self.date())
        self._set_invalid(False)
        self._render()

    def _show_calendar(self) -> None:
        """Kalender unter dem Feld aufklappen; ein Klick auf einen Tag übernimmt ihn."""
        if self.isReadOnly() or not self.isEnabled():
            return
        menu = QMenu(self)
        calendar = QCalendarWidget(menu)
        calendar.setGridVisible(False)
        calendar.setSelectedDate(self.date())
        action = QWidgetAction(menu)
        action.setDefaultWidget(calendar)
        menu.addAction(action)

        def take(selected: QDate) -> None:
            self.setDate(selected)
            menu.close()

        calendar.clicked.connect(take)
        calendar.activated.connect(take)
        menu.exec(self.mapToGlobal(QPoint(0, self.height())))

    # ------------------------------------------------------------------ #
    # Darstellung
    # ------------------------------------------------------------------ #
    def _text(self) -> str:
        return self._value.strftime("%d.%m.%Y")

    def _render(self) -> None:
        self.setText(self._text())

    def _set_invalid(self, invalid: bool) -> None:
        if bool(self.property("invalid")) == invalid:
            return
        self.setProperty("invalid", invalid)
        self.style().unpolish(self)
        self.style().polish(self)

    def keyPressEvent(self, event) -> None:  # type: ignore[no-untyped-def]  # noqa: N802
        # Alt+Pfeil runter öffnet den Kalender - wie bei Auswahlfeldern üblich.
        if event.key() == Qt.Key.Key_Down and event.modifiers() & Qt.KeyboardModifier.AltModifier:
            self._show_calendar()
            return
        super().keyPressEvent(event)
