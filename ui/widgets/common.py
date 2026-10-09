"""Wiederverwendbare Bausteine der Oberfläche.

* :class:`ObjectTable` - Tabelle, die eine Liste von Python-Objekten anzeigt.
  Jede Spalte wird durch eine :class:`Column` beschrieben; dadurch braucht
  kein Tab eigenen Tabellen-Code.
* :class:`Card`, :class:`StatCard` - Karten-Container.
* :class:`MoneySpin`, :class:`DecimalSpin`, :class:`DateEdit` - Eingabefelder,
  die direkt Domain-Werte (``Money``, ``Decimal``, ``date``) liefern.
* :class:`PeriodBar` - Auswahl von Immobilie und Abrechnungszeitraum.
* :class:`BaseTab` - gemeinsame Basisklasse aller Tabs.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Generic, Sequence, TypeVar

from PyQt6.QtCore import (
    QAbstractTableModel,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDateEdit,
    QDoubleSpinBox,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QSpinBox,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from app_context import AppContext
from models.property import Property
from models.value_objects import DateRange, Money
from ui import icons, styles
from ui.helpers import FileDropFilter, fill_combo, to_date, to_qdate

T = TypeVar("T")

DATE_DISPLAY_FORMAT = "dd.MM.yyyy"


# ---------------------------------------------------------------------- #
# Objekt-Tabelle
# ---------------------------------------------------------------------- #
@dataclass(frozen=True)
class Column(Generic[T]):
    """Beschreibt eine Tabellenspalte.

    Attributes:
        title: Spaltenüberschrift.
        text: Liefert den anzuzeigenden Text für ein Objekt.
        sort_key: Liefert den Sortierwert (Zahl oder Text). Ohne Angabe wird
            nach dem angezeigten Text sortiert - für Beträge und Datumswerte
            sollte deshalb immer ein ``sort_key`` angegeben werden.
        align_right: Rechtsbündig (für Zahlen und Beträge).
        color: Optional: liefert den Namen einer Schemafarbe (z. B.
            ``"danger"``) oder ``None`` für die Standardfarbe.
    """

    title: str
    text: Callable[[T], str]
    sort_key: Callable[[T], Any] | None = None
    align_right: bool = False
    color: Callable[[T], str | None] | None = None


class _ObjectTableModel(QAbstractTableModel, Generic[T]):
    """Qt-Modell, das eine Objektliste anhand der Spaltenbeschreibungen darstellt."""

    SORT_ROLE = Qt.ItemDataRole.UserRole

    def __init__(self, columns: Sequence[Column[T]], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._columns = list(columns)
        self._items: list[T] = []

    def set_items(self, items: Sequence[T]) -> None:
        """Ersetzt den gesamten Inhalt der Tabelle."""
        self.beginResetModel()
        self._items = list(items)
        self.endResetModel()

    def item_at(self, row: int) -> T:
        """Das Objekt in der angegebenen (Quell-)Zeile."""
        return self._items[row]

    # --- Pflichtmethoden von QAbstractTableModel ----------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802 (Qt-Name)
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self._columns)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        column = self._columns[index.column()]
        item = self._items[index.row()]

        if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
            # Der Tooltip zeigt denselben Text - hilfreich, wenn die Spalte ihn abschneidet.
            return column.text(item)
        if role == self.SORT_ROLE:
            return column.sort_key(item) if column.sort_key else column.text(item)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            horizontal = Qt.AlignmentFlag.AlignRight if column.align_right else Qt.AlignmentFlag.AlignLeft
            return int(horizontal | Qt.AlignmentFlag.AlignVCenter)
        if role == Qt.ItemDataRole.ForegroundRole and column.color:
            color_name = column.color(item)
            return QColor(styles.color(color_name)) if color_name else None
        return None

    def headerData(  # noqa: N802
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> Any:
        if orientation != Qt.Orientation.Horizontal:
            return None
        if role == Qt.ItemDataRole.DisplayRole:
            return self._columns[section].title
        if role == Qt.ItemDataRole.TextAlignmentRole:
            right = self._columns[section].align_right
            horizontal = Qt.AlignmentFlag.AlignRight if right else Qt.AlignmentFlag.AlignLeft
            return int(horizontal | Qt.AlignmentFlag.AlignVCenter)
        return None


class ObjectTable(QTableView, Generic[T]):
    """Sortierbare Tabelle für eine Liste gleichartiger Objekte.

    Signals:
        selection_changed: Die Auswahl hat sich geändert.
        item_activated: Doppelklick/Enter auf einer Zeile; Argument ist das Objekt.
    """

    selection_changed = pyqtSignal()
    item_activated = pyqtSignal(object)

    def __init__(
        self,
        columns: Sequence[Column[T]],
        stretch_column: int | None = None,
        multi_select: bool = False,
        parent: QWidget | None = None,
        max_column_width: int = 360,
    ) -> None:
        """
        Args:
            columns: Spaltenbeschreibungen.
            stretch_column: Index der Spalte, die den freien Platz einnimmt
                (Standard: die letzte).
            multi_select: Mehrfachauswahl mit Strg/Umschalt erlauben.
            max_column_width: Obergrenze für die automatisch gewählte Breite
                der übrigen Spalten; längere Texte werden abgeschnitten und
                stehen vollständig im Tooltip.
        """
        super().__init__(parent)
        self._max_column_width = max_column_width
        self._model: _ObjectTableModel[T] = _ObjectTableModel(columns, self)
        self._proxy = QSortFilterProxyModel(self)
        self._proxy.setSourceModel(self._model)
        self._proxy.setSortRole(_ObjectTableModel.SORT_ROLE)
        self._proxy.setSortCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setModel(self._proxy)

        self.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.setSelectionMode(
            QAbstractItemView.SelectionMode.ExtendedSelection
            if multi_select
            else QAbstractItemView.SelectionMode.SingleSelection
        )
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setAlternatingRowColors(True)
        self.setShowGrid(False)
        self.setSortingEnabled(True)
        self.setWordWrap(False)
        self.verticalHeader().setVisible(False)
        self.verticalHeader().setDefaultSectionSize(34)

        header = self.horizontalHeader()
        header.setHighlightSections(False)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._stretch_column = len(columns) - 1 if stretch_column is None else stretch_column
        header.setSectionResizeMode(self._stretch_column, QHeaderView.ResizeMode.Stretch)
        # Anfangs unsortiert: Die Reihenfolge der Services bleibt erhalten.
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)

        self.selectionModel().selectionChanged.connect(lambda *_: self.selection_changed.emit())
        self.doubleClicked.connect(self._emit_activated)

    def set_items(self, items: Sequence[T]) -> None:
        """Zeigt eine neue Objektliste an und passt die Spaltenbreiten an."""
        self._model.set_items(items)
        self.resizeColumnsToContents()
        # Ein wenig Luft je Spalte, aber keine überbreiten Spalten.
        header = self.horizontalHeader()
        for column in range(self._model.columnCount()):
            if column != self._stretch_column:
                header.resizeSection(column, min(header.sectionSize(column) + 6, self._max_column_width))
        self.selection_changed.emit()

    def selected_item(self) -> T | None:
        """Das ausgewählte Objekt oder ``None``."""
        items = self.selected_items()
        return items[0] if items else None

    def selected_items(self) -> list[T]:
        """Alle ausgewählten Objekte in Anzeigereihenfolge."""
        rows = sorted(index.row() for index in self.selectionModel().selectedRows())
        return [
            self._model.item_at(self._proxy.mapToSource(self._proxy.index(row, 0)).row()) for row in rows
        ]

    def select_first(self) -> None:
        """Wählt die erste Zeile aus, falls vorhanden."""
        if self._proxy.rowCount() > 0:
            self.selectRow(0)

    def select_where(self, predicate: Callable[[T], bool]) -> bool:
        """Wählt die erste Zeile aus, deren Objekt die Bedingung erfüllt.

        Returns:
            ``True``, wenn eine passende Zeile gefunden wurde.
        """
        for row in range(self._proxy.rowCount()):
            source_row = self._proxy.mapToSource(self._proxy.index(row, 0)).row()
            if predicate(self._model.item_at(source_row)):
                self.selectRow(row)
                return True
        return False

    def _emit_activated(self, index: QModelIndex) -> None:
        """Übersetzt einen Doppelklick in das Signal ``item_activated``."""
        source_row = self._proxy.mapToSource(index).row()
        self.item_activated.emit(self._model.item_at(source_row))


# ---------------------------------------------------------------------- #
# Karten und Überschriften
# ---------------------------------------------------------------------- #
class Card(QFrame):
    """Weißer Kasten mit abgerundeten Ecken; optional mit Überschrift."""

    def __init__(self, title: str = "", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(16, 14, 16, 16)
        self.body.setSpacing(10)

        #: Zeile mit Überschrift (links) und Platz für Schaltflächen (rechts).
        self.header = QHBoxLayout()
        self.header.setSpacing(8)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("SectionTitle")
        self.header.addWidget(self.title_label)
        self.header.addStretch(1)
        self.body.addLayout(self.header)

    def set_title(self, title: str) -> None:
        """Ändert die Überschrift der Karte."""
        self.title_label.setText(title)


class StatCard(QFrame):
    """Kennzahl-Kachel für das Dashboard: Icon, großer Wert, Beschriftung."""

    def __init__(self, label: str, icon_name: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("Card")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(12)

        icon_label = QLabel()
        icon_label.setPixmap(icons.pixmap(icon_name, 30, styles.color("primary")))
        layout.addWidget(icon_label, 0, Qt.AlignmentFlag.AlignTop)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(0)
        self._value_label = QLabel("–")
        self._value_label.setObjectName("StatValue")
        caption = QLabel(label)
        caption.setObjectName("StatLabel")
        text_layout.addWidget(self._value_label)
        text_layout.addWidget(caption)
        layout.addLayout(text_layout, 1)

    def set_value(self, text: str) -> None:
        """Setzt den angezeigten Wert."""
        self._value_label.setText(text)


def page_title(text: str) -> QLabel:
    """Große Seitenüberschrift."""
    label = QLabel(text)
    label.setObjectName("PageTitle")
    return label


def muted_label(text: str = "") -> QLabel:
    """Dezenter, grauer Hinweistext."""
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(True)
    return label


# ---------------------------------------------------------------------- #
# Eingabefelder
# ---------------------------------------------------------------------- #
class MoneySpin(QDoubleSpinBox):
    """Eingabefeld für Euro-Beträge."""

    def __init__(self, allow_negative: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setDecimals(2)
        self.setRange(-9_999_999.99 if allow_negative else 0.0, 9_999_999.99)
        self.setSuffix(" €")
        self.setGroupSeparatorShown(True)
        self.setSingleStep(10.0)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def money(self) -> Money:
        """Der eingegebene Betrag."""
        # Über den Text-Umweg runden, damit aus 19.99 nicht 19.989999… wird.
        return Money.from_decimal(f"{self.value():.2f}")

    def set_money(self, money: Money) -> None:
        """Setzt den angezeigten Betrag."""
        self.setValue(float(money.amount))


class DecimalSpin(QDoubleSpinBox):
    """Eingabefeld für Dezimalzahlen (Flächen, Zählerstände)."""

    def __init__(
        self,
        decimals: int = 2,
        maximum: float = 99_999_999.0,
        suffix: str = "",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setDecimals(decimals)
        self.setRange(0.0, maximum)
        self.setSuffix(suffix)
        self.setGroupSeparatorShown(True)
        self.setAlignment(Qt.AlignmentFlag.AlignRight)

    def decimal_value(self) -> Decimal:
        """Der eingegebene Wert als ``Decimal``."""
        return Decimal(f"{self.value():.{self.decimals()}f}")

    def set_decimal(self, value: Decimal) -> None:
        """Setzt den angezeigten Wert."""
        self.setValue(float(value))


class DateEdit(QDateEdit):
    """Datumsfeld mit Kalender-Popup im Format TT.MM.JJJJ und Tastatureingabe."""

    def __init__(self, value: date | None = None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setCalendarPopup(True)
        self.setDisplayFormat(DATE_DISPLAY_FORMAT)
        self.setReadOnly(False)
        self.set_value(value or date.today())

    def value(self) -> date:
        """Das gewählte Datum."""
        return to_date(self.date())

    def set_value(self, value: date) -> None:
        """Setzt das angezeigte Datum."""
        self.setDate(to_qdate(value))


# ---------------------------------------------------------------------- #
# Auswahl von Immobilie und Zeitraum
# ---------------------------------------------------------------------- #
class PeriodBar(QWidget):
    """Leiste zur Auswahl von Immobilie und Abrechnungszeitraum.

    Das Jahr-Feld ist eine Abkürzung: Es setzt Von/Bis auf das Kalenderjahr.
    Von und Bis lassen sich danach frei anpassen (abweichendes Wirtschaftsjahr).

    Signals:
        changed: Immobilie oder Zeitraum wurden geändert.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self._property_combo = QComboBox()
        self._property_combo.setMinimumWidth(240)
        self._year_spin = QSpinBox()
        self._year_spin.setRange(1990, 2100)
        # Abgerechnet wird in aller Regel das abgelaufene Jahr.
        default_year = date.today().year - 1
        self._year_spin.setValue(default_year)
        self._from_edit = DateEdit(date(default_year, 1, 1))
        self._to_edit = DateEdit(date(default_year, 12, 31))

        for caption, widget in (
            ("Immobilie", self._property_combo),
            ("Jahr", self._year_spin),
            ("von", self._from_edit),
            ("bis", self._to_edit),
        ):
            layout.addWidget(muted_label(caption))
            layout.addWidget(widget)
        layout.addStretch(1)

        self._property_combo.currentIndexChanged.connect(lambda *_: self.changed.emit())
        self._year_spin.valueChanged.connect(self._apply_year)
        self._from_edit.dateChanged.connect(lambda *_: self.changed.emit())
        self._to_edit.dateChanged.connect(lambda *_: self.changed.emit())

    def set_properties(self, properties: Sequence[Property]) -> None:
        """Aktualisiert die Liste der Immobilien (die Auswahl bleibt erhalten)."""
        fill_combo(self._property_combo, [(prop.name, prop.id) for prop in properties])

    def property_id(self) -> int | None:
        """ID der gewählten Immobilie oder ``None``, wenn es keine gibt."""
        data = self._property_combo.currentData()
        return int(data) if data is not None else None

    def period(self) -> DateRange | None:
        """Der gewählte Zeitraum oder ``None``, wenn Bis vor Von liegt."""
        start, end = self._from_edit.value(), self._to_edit.value()
        return DateRange(start, end) if end >= start else None

    def _apply_year(self, year: int) -> None:
        """Setzt Von/Bis auf das gewählte Kalenderjahr und meldet *eine* Änderung."""
        for edit, value in ((self._from_edit, date(year, 1, 1)), (self._to_edit, date(year, 12, 31))):
            edit.blockSignals(True)
            edit.set_value(value)
            edit.blockSignals(False)
        self.changed.emit()


# ---------------------------------------------------------------------- #
# Drag-and-drop
# ---------------------------------------------------------------------- #
class FileDropArea(QWidget):
    """Fläche, auf die Dateien aus dem Explorer gezogen werden können.

    Die ganze Fläche samt ihrer Kind-Widgets (Tabellen, Schaltflächen) dient
    als Ablageziel (siehe :class:`~ui.helpers.FileDropFilter`).

    Signals:
        files_dropped: Liste der abgelegten Dateien (``list[Path]``) mit passender Endung.
    """

    files_dropped = pyqtSignal(list)

    def __init__(self, extensions: Sequence[str], parent: QWidget | None = None) -> None:
        """
        Args:
            extensions: Erlaubte Endungen in Kleinbuchstaben mit Punkt.
        """
        super().__init__(parent)
        self._drop_filter = FileDropFilter(self, extensions, self.files_dropped.emit)


# ---------------------------------------------------------------------- #
# Basisklasse der Tabs
# ---------------------------------------------------------------------- #
class BaseTab(QWidget):
    """Gemeinsame Basis aller Tabs.

    Jeder Tab bekommt den :class:`AppContext` und implementiert
    :meth:`refresh`. Das Hauptfenster ruft ``refresh()`` immer dann auf, wenn
    der Tab sichtbar wird - so zeigt jeder Tab stets aktuelle Daten, auch
    wenn sie in einem anderen Tab geändert wurden.
    """

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._context = context
        self.root = QVBoxLayout(self)
        self.root.setContentsMargins(20, 18, 20, 18)
        self.root.setSpacing(14)

    def refresh(self) -> None:
        """Lädt die Daten des Tabs neu. Von Unterklassen zu überschreiben."""
        raise NotImplementedError
