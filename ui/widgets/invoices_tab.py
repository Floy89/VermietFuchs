"""Tab 5 - Rechnungen: Originalrechnungen erfassen, zusammenfassen und auf Vollständigkeit prüfen."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QFont
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QSplitter,
    QTabWidget,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from app_context import AppContext
from models.attachment import ATTACHMENT_EXTENSIONS
from models.cost import Cost
from models.invoice_checklist import (
    ChecklistItem,
    ChecklistStatus,
    InvoiceChecklist,
    expected_arrival,
)
from models.invoice_group import InvoiceGroup
from models.value_objects import DateRange
from ui import styles
from ui.dialogs.bundle_dialog import BundleDialog
from ui.dialogs.cost_dialog import CostDialog, TenantChoice
from ui.dialogs.cost_type_dialog import CostTypeManagerDialog
from ui.helpers import ErrorGuard, confirm, fill_combo, make_button, select_data
from ui.widgets.common import (
    BaseTab,
    Card,
    Column,
    DateEdit,
    FileDropArea,
    ObjectTable,
    muted_label,
    page_title,
)
from utils.formatters import (
    format_date,
    format_decimal,
    format_money,
    format_period_short,
    format_quantity,
)

_GROUP_ROLE = Qt.ItemDataRole.UserRole
_ITEM_ROLE = Qt.ItemDataRole.UserRole + 1

#: Eintrag des Auswahlfelds "Zeitraum" für einen frei gewählten Zeitraum.
_CUSTOM_PERIOD = "custom"

#: Schemafarbe je Stand einer Kostenart in der Checkliste.
_STATUS_COLORS = {
    ChecklistStatus.COMPLETE: "success",
    ChecklistStatus.PARTIAL: "warning_text",
    ChecklistStatus.MISSING: "danger",
    ChecklistStatus.NOT_RELEVANT: "disabled",
}

_MONTHS = (
    "Januar", "Februar", "März", "April", "Mai", "Juni",
    "Juli", "August", "September", "Oktober", "November", "Dezember",
)


def format_consumption(cost: Cost) -> str:
    """Verbrauch einer Rechnung mit Einheit, z. B. "8.000 kWh" (leer, wenn nicht angegeben)."""
    if cost.consumption is None:
        return ""
    return f"{format_quantity(cost.consumption, 0)} {cost.consumption_unit}"


def format_month(day: date) -> str:
    """Monat und Jahr ausgeschrieben, z. B. "Dezember 2026"."""
    return f"{_MONTHS[day.month - 1]} {day.year}"


def describe_readiness(checklist: InvoiceChecklist, today: date) -> str:
    """Fasst in einem Satz zusammen, ob und ab wann abgerechnet werden kann.

    Die Schätzung beruht auf den Rechnungsdaten des Vorjahres (siehe
    :attr:`InvoiceChecklist.ready_date`).
    """
    relevant = checklist.relevant_items
    if not relevant:
        return (
            "Für diesen Zeitraum und das Vorjahr sind keine Rechnungen erfasst. Über "
            "„Alle Kostenarten anzeigen“ lässt sich festlegen, was abgerechnet werden soll."
        )
    done = len(relevant) - len(checklist.open_items)
    text = f"{done} von {len(relevant)} Kostenarten vollständig."
    if checklist.is_complete:
        return f"{text} Alle Rechnungen liegen vor – die Abrechnung kann erstellt werden."

    ready = checklist.ready_date
    unknown = len(checklist.unknown_items)
    if ready is not None:
        last = max(
            (item for item in checklist.open_items if item.expected_date is not None),
            key=lambda item: item.expected_date or date.min,
        )
        if ready < today:
            text += (
                f" Die letzte fehlende Rechnung ({last.cost_type.name}) war nach dem Vorjahr schon um den "
                f"{format_date(ready)} zu erwarten – sie ist überfällig."
            )
        else:
            text += (
                f" Abrechnung voraussichtlich möglich ab {format_month(ready)} – zuletzt kommt "
                f"erfahrungsgemäß {last.cost_type.name} (um den {format_date(ready)})."
            )
    if unknown:
        text += (
            f" Für {unknown} offene Kostenart(en) gibt es keine Vorjahresrechnung mit Rechnungsdatum – "
            "dort ist keine Schätzung möglich."
        )
    return text


class InvoicesTab(BaseTab):
    """Rechnungen einer Immobilie für ein Kalenderjahr oder einen frei gewählten Zeitraum.

    Zwei Unterseiten teilen sich die Auswahl oben:

    * **Rechnungen:** links die Originalrechnungen mit ihrem echten
      Rechnungszeitraum, rechts die daraus gebildeten Summen je Kostenart
      ("aggregierte Rechnung") - die Werte für die Abrechnung bzw. die Meldung
      an den Messdienst.
    * **Checkliste:** je Kostenart, ob für den gewählten Zeitraum eine Rechnung
      vorliegt, wie viel davon abgedeckt ist, welche Rechnung es im Vorjahr war
      und wann die nächste voraussichtlich kommt.
    """

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._building_tree = False
        self._building_checklist = False
        self.root.addWidget(page_title("Rechnungen"))

        bar = QHBoxLayout()
        self._property_combo = QComboBox()
        self._property_combo.setMinimumWidth(240)
        self._year_combo = QComboBox()
        self._year_combo.setToolTip(
            "Kalenderjahr, alle Jahre oder ein eigener Zeitraum (z. B. für eine Abrechnung 01.07.–30.06.)"
        )
        last_year = date.today().year - 1
        self._from_label = muted_label("von")
        self._from_edit = DateEdit(date(last_year, 1, 1))
        self._to_label = muted_label("bis")
        self._to_edit = DateEdit(date(last_year, 12, 31))
        bar.addWidget(muted_label("Immobilie"))
        bar.addWidget(self._property_combo)
        bar.addSpacing(8)
        bar.addWidget(muted_label("Zeitraum"))
        bar.addWidget(self._year_combo)
        for widget in (self._from_label, self._from_edit, self._to_label, self._to_edit):
            bar.addWidget(widget)
            widget.setVisible(False)
        bar.addStretch(1)
        self._types_button = make_button("Kostenarten …", "settings", tooltip="Katalog ansehen, eigene Kostenarten anlegen")
        self._types_button.clicked.connect(self._manage_cost_types)
        bar.addWidget(self._types_button)
        self.root.addLayout(bar)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_invoice_drop_area())
        splitter.addWidget(self._build_group_card())
        splitter.setSizes([700, 700])

        self._pages = QTabWidget()
        self._pages.addTab(splitter, "Rechnungen")
        self._pages.addTab(self._build_checklist_page(), "Checkliste")
        self.root.addWidget(self._pages, 1)

        self._property_combo.currentIndexChanged.connect(lambda *_: self._on_property_changed())
        self._year_combo.currentIndexChanged.connect(lambda *_: self._on_period_choice_changed())
        self._from_edit.dateChanged.connect(lambda *_: self._reload())
        self._to_edit.dateChanged.connect(lambda *_: self._reload())

    # ------------------------------------------------------------------ #
    # Aufbau
    # ------------------------------------------------------------------ #
    def _build_invoice_drop_area(self) -> QWidget:
        """Die linke Karte als Ablageziel: PDFs/Fotos darauf ziehen legt eine Rechnung mit diesen Belegen an."""
        area = FileDropArea(ATTACHMENT_EXTENSIONS)
        layout = QVBoxLayout(area)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_invoice_card())
        area.files_dropped.connect(self._on_receipts_dropped)
        return area

    def _build_invoice_card(self) -> Card:
        """Linke Karte: die einzelnen Rechnungen."""
        #: Anzahl der Belege je Rechnungs-ID (für die Spalte "Belege").
        self._attachment_counts: dict[int, int] = {}
        card = Card("Rechnungs-Positionen")
        self._add_button = make_button("Neu", "plus", "primary")
        self._edit_button = make_button("Bearbeiten", "edit")
        self._duplicate_button = make_button("Duplizieren", "copy")
        self._bundle_button = make_button(
            "Sammelrechnung",
            "plus",
            tooltip="Eine Rechnung mit mehreren Kostenarten erfassen (z. B. Abgabenbescheid der Stadt)",
        )
        self._exclude_button = make_button("Ausschließen", tooltip="Rechnung weder aggregieren noch abrechnen")
        self._delete_button = make_button("Löschen", "trash", "danger")
        for button in (
            self._add_button,
            self._bundle_button,
            self._edit_button,
            self._duplicate_button,
            self._exclude_button,
            self._delete_button,
        ):
            card.header.addWidget(button)

        self._invoice_table: ObjectTable[Cost] = ObjectTable(
            [
                Column("Kostenart", lambda c: c.cost_type.label),
                Column(
                    "Beschreibung",
                    # Positionen einer Sammelrechnung kenntlich machen: Bearbeiten öffnet die ganze Rechnung.
                    lambda c: f"{c.description} · Sammelrechnung".lstrip(" ·") if c.bundle_id else c.description,
                ),
                Column(
                    "Datum",
                    lambda c: format_date(c.invoice_date),
                    lambda c: c.invoice_date.isoformat() if c.invoice_date else "",
                ),
                Column("Zeitraum", lambda c: format_period_short(c.period), lambda c: c.period.start.isoformat()),
                Column("Betrag", lambda c: format_money(c.amount), lambda c: c.amount.cents, True),
                Column("Verbrauch", format_consumption, lambda c: float(c.consumption or 0), True),
                Column("Splits", lambda c: str(len(c.splits)) if c.splits else "–", lambda c: len(c.splits), True),
                Column(
                    "Belege",
                    lambda c: str(self._attachment_counts.get(c.id or -1, 0) or "–"),
                    lambda c: self._attachment_counts.get(c.id or -1, 0),
                    True,
                ),
                Column(
                    "Abrechnung",
                    lambda c: "ausgeschlossen" if c.excluded else "ja",
                    color=lambda c: "danger" if c.excluded else "success",
                ),
            ],
            stretch_column=1,
        )
        card.body.addWidget(self._invoice_table)

        self._add_button.clicked.connect(lambda: self._open_invoice_dialog(None))
        self._edit_button.clicked.connect(self._edit_invoice)
        self._bundle_button.clicked.connect(lambda: self._open_bundle_dialog(None))
        self._duplicate_button.clicked.connect(self._duplicate_invoice)
        self._exclude_button.clicked.connect(self._toggle_invoice_excluded)
        self._delete_button.clicked.connect(self._delete_invoice)
        self._invoice_table.item_activated.connect(lambda *_: self._edit_invoice())
        self._invoice_table.selection_changed.connect(self._update_buttons)
        return card

    def _build_group_card(self) -> Card:
        """Rechte Karte: aggregierte Rechnungen als Baum."""
        card = Card("Aggregierte Rechnungen")
        self._rename_button = make_button("Umbenennen", "edit")
        self._expand_button = make_button("Alle aufklappen")
        self._collapse_button = make_button("Alle zuklappen")
        for button in (self._rename_button, self._expand_button, self._collapse_button):
            card.header.addWidget(button)

        self._tree = QTreeWidget()
        self._tree.setColumnCount(4)
        self._tree.setHeaderLabels(["In Abrechnung · Bezeichnung", "Anteil im Zeitraum", "Betrag", "Verbrauch"])
        self._tree.setAlternatingRowColors(True)
        self._tree.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._tree.setUniformRowHeights(True)
        header = self._tree.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        card.body.addWidget(self._tree)
        card.body.addWidget(
            muted_label(
                "Häkchen = wird in die Abrechnung des Jahres übernommen. Ohne Häkchen bleibt die Summe "
                "sichtbar (z. B. Gas für die Meldung an den Messdienst), wird aber nicht umgelegt."
            )
        )

        self._rename_button.clicked.connect(self._rename_group)
        self._expand_button.clicked.connect(self._tree.expandAll)
        self._collapse_button.clicked.connect(self._tree.collapseAll)
        self._tree.itemChanged.connect(self._on_tree_item_changed)
        self._tree.itemSelectionChanged.connect(self._update_buttons)
        return card

    def _build_checklist_page(self) -> QWidget:
        """Unterseite "Checkliste": Vollständigkeit der Rechnungen je Kostenart."""
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 10, 0, 0)
        layout.setSpacing(10)

        self._checklist_card = Card("Checkliste")
        self._show_all_check = QCheckBox("Alle Kostenarten anzeigen")
        self._show_all_check.setToolTip(
            "Zeigt auch Kostenarten, für die weder in diesem Zeitraum noch im Vorjahr eine Rechnung "
            "erfasst ist - um sie per Häkchen als relevant zu markieren."
        )
        self._checklist_add_button = make_button(
            "Rechnung anlegen", "plus", "primary", "Neue Rechnung für die markierte Kostenart erfassen"
        )
        self._checklist_card.header.addWidget(self._show_all_check)
        self._checklist_card.header.addWidget(self._checklist_add_button)

        self._readiness_label = QLabel()
        self._readiness_label.setWordWrap(True)
        self._readiness_label.setObjectName("SectionTitle")
        self._checklist_card.body.addWidget(self._readiness_label)

        self._checklist = QTreeWidget()
        self._checklist.setColumnCount(7)
        self._checklist.setHeaderLabels(
            ["Relevant · Kostenart", "Status", "Abdeckung", "Fehlender Zeitraum", "Erfasst", "Im Vorjahr", "Erwartet"]
        )
        self._checklist.setAlternatingRowColors(True)
        self._checklist.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._checklist.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, 7):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setStretchLastSection(False)
        self._checklist_card.body.addWidget(self._checklist, 1)
        self._checklist_card.body.addWidget(
            muted_label(
                "Häkchen = Kostenart wird für diese Immobilie abgerechnet. Voreingestellt ist alles, wofür in "
                "diesem Zeitraum oder im Vorjahr eine Rechnung erfasst wurde. „Erwartet“ ist eine Schätzung: "
                "Rechnungsdatum der Vorjahresrechnung plus ein Jahr. Aufgeklappt zeigt jede Kostenart die "
                "Rechnungen des Vorjahres als Gedächtnisstütze."
            )
        )
        layout.addWidget(self._checklist_card, 1)

        self._show_all_check.toggled.connect(lambda *_: self._reload_checklist())
        self._checklist_add_button.clicked.connect(self._add_invoice_for_checklist_item)
        self._checklist.itemChanged.connect(self._on_checklist_item_changed)
        self._checklist.itemSelectionChanged.connect(self._update_buttons)
        return page

    # ------------------------------------------------------------------ #
    # Auswahl
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Lädt Immobilien, Jahre, Rechnungen, Aggregation und Checkliste neu."""
        with ErrorGuard(self):
            properties = self._context.properties.list_properties()
            fill_combo(self._property_combo, [(prop.name, prop.id) for prop in properties])
        self._on_property_changed()

    def _property_id(self) -> int | None:
        data = self._property_combo.currentData()
        return int(data) if data is not None else None

    def _period(self) -> DateRange | None:
        """Der gewählte Zeitraum; ``None`` bei "Alle Jahre" oder ungültigem Von/Bis."""
        choice = self._year_combo.currentData()
        if choice is None:
            return None
        if choice == _CUSTOM_PERIOD:
            start, end = self._from_edit.value(), self._to_edit.value()
            return DateRange(start, end) if end >= start else None
        return DateRange.for_year(int(choice))

    def _on_property_changed(self) -> None:
        """Bietet die Jahre an, die von Rechnungen der Immobilie berührt werden."""
        property_id = self._property_id()
        years: set[int] = {date.today().year - 1}
        if property_id is not None:
            with ErrorGuard(self):
                for cost in self._context.costs.list_costs(property_id):
                    years.update(range(cost.period.start.year, cost.period.end.year + 1))
        first_fill = self._year_combo.count() == 0
        fill_combo(
            self._year_combo,
            [
                ("Alle Jahre", None),
                *[(str(y), y) for y in sorted(years, reverse=True)],
                ("Eigener Zeitraum …", _CUSTOM_PERIOD),
            ],
        )
        if first_fill:
            # Beim ersten Anzeigen das zuletzt abgelaufene Jahr vorwählen.
            self._year_combo.blockSignals(True)
            select_data(self._year_combo, date.today().year - 1)
            self._year_combo.blockSignals(False)
        self._on_period_choice_changed()

    def _on_period_choice_changed(self) -> None:
        """Blendet Von/Bis nur für den eigenen Zeitraum ein und lädt neu."""
        custom = self._year_combo.currentData() == _CUSTOM_PERIOD
        for widget in (self._from_label, self._from_edit, self._to_label, self._to_edit):
            widget.setVisible(custom)
        self._reload()

    def set_custom_period(self, period: DateRange) -> None:
        """Wählt einen eigenen Zeitraum aus (z. B. ein abweichendes Wirtschaftsjahr)."""
        for edit, value in ((self._from_edit, period.start), (self._to_edit, period.end)):
            edit.blockSignals(True)
            edit.set_value(value)
            edit.blockSignals(False)
        select_data(self._year_combo, _CUSTOM_PERIOD)
        self._on_period_choice_changed()

    def show_checklist(self) -> None:
        """Wechselt auf die Unterseite "Checkliste"."""
        self._pages.setCurrentIndex(1)

    # ------------------------------------------------------------------ #
    # Daten laden
    # ------------------------------------------------------------------ #
    def _reload(self) -> None:
        """Füllt Tabelle, Baum und Checkliste passend zur Auswahl."""
        property_id = self._property_id()
        choice = self._year_combo.currentData()
        period = self._period()
        invoices: list[Cost] = []
        groups: list[InvoiceGroup] = []
        # Bei ungültigem eigenem Zeitraum (Bis vor Von) bleibt alles leer.
        if property_id is not None and (period is not None or choice is None):
            with ErrorGuard(self):
                invoices = self._context.costs.list_costs(property_id, period)
                self._attachment_counts = self._context.attachments.counts()
                if choice == _CUSTOM_PERIOD:
                    groups = self._context.invoice_groups.groups(property_id, period=period)
                else:
                    groups = self._context.invoice_groups.groups(property_id, choice)
        self._invoice_table.set_items(invoices)
        self._fill_tree(groups)
        self._reload_checklist()

    def _reload_checklist(self) -> None:
        """Berechnet die Checkliste für den gewählten Zeitraum neu."""
        property_id = self._property_id()
        period = self._period()
        checklist: InvoiceChecklist | None = None
        if property_id is not None and period is not None:
            with ErrorGuard(self):
                checklist = self._context.invoice_checklist.build(property_id, period)
        self._fill_checklist(checklist)
        self._update_buttons()

    def _fill_tree(self, groups: list[InvoiceGroup]) -> None:
        """Baut den Baum: je aggregierter Rechnung ein Knoten, darunter die Rechnungsanteile."""
        self._building_tree = True
        self._tree.clear()
        bold = QFont(self._tree.font())
        bold.setBold(True)
        muted = QBrush(QColor(styles.color("disabled")))

        for group in groups:
            consumption = group.total_consumption
            top = QTreeWidgetItem(
                [
                    group.name,
                    f"{len(group.parts)} Rechnung(en)",
                    format_money(group.total_amount),
                    f"{format_quantity(consumption, 0)} {group.consumption_unit}" if consumption is not None else "",
                ]
            )
            top.setData(0, _GROUP_ROLE, group)
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            top.setCheckState(0, Qt.CheckState.Unchecked if group.excluded else Qt.CheckState.Checked)
            for column in range(4):
                top.setFont(column, bold)
                if group.excluded:
                    top.setForeground(column, muted)

            for part in group.parts:
                invoice, share = part.invoice, part.part
                if not share.is_prorated:
                    portion = "vollständig"
                elif invoice.splits:
                    # Mit Splits ist der Anteil nicht proportional zu den Tagen.
                    portion = f"{format_period_short(share.period)} · laut Splits"
                else:
                    portion = f"{format_period_short(share.period)} · {share.period.days} von {invoice.period.days} Tagen"
                child = QTreeWidgetItem(
                    [
                        f"{invoice.description or invoice.cost_type.label} ({format_period_short(invoice.period)})",
                        portion,
                        format_money(share.amount),
                        format_consumption(share),
                    ]
                )
                if group.excluded:
                    for column in range(4):
                        child.setForeground(column, muted)
                top.addChild(child)

            for item in (top, *[top.child(i) for i in range(top.childCount())]):
                for column in (2, 3):
                    item.setTextAlignment(column, int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter))
            self._tree.addTopLevelItem(top)

        self._tree.expandAll()
        self._building_tree = False

    def _fill_checklist(self, checklist: InvoiceChecklist | None) -> None:
        """Baut die Checkliste: je Kostenart eine Zeile, darunter die Vorjahresrechnungen."""
        self._building_checklist = True
        self._checklist.clear()
        if checklist is None:
            self._checklist_card.set_title("Checkliste")
            self._readiness_label.setText(
                "Bitte oben ein Jahr oder einen eigenen Zeitraum wählen – die Checkliste prüft immer "
                "einen bestimmten Abrechnungszeitraum."
            )
            self._building_checklist = False
            return

        self._checklist_card.set_title(f"Checkliste {format_period_short(checklist.period)}")
        self._readiness_label.setText(describe_readiness(checklist, date.today()))
        bold = QFont(self._checklist.font())
        bold.setBold(True)
        muted = QBrush(QColor(styles.color("disabled")))
        right = int(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        show_all = self._show_all_check.isChecked()

        for entry in checklist.items:
            # Ausgeblendet wird nur, was weder Rechnungen hat noch je markiert wurde.
            if not show_all and not entry.relevant and not entry.relevance_is_manual and not entry.invoices:
                continue
            status = entry.status
            top = QTreeWidgetItem(
                [
                    entry.cost_type.name,
                    status.label,
                    f"{format_decimal(entry.coverage_percent, 0)} %" if entry.relevant else "",
                    ", ".join(format_period_short(gap) for gap in entry.missing) if entry.relevant else "",
                    self._describe_recorded(entry),
                    self._describe_previous(entry),
                    self._describe_expected(entry),
                ]
            )
            top.setData(0, _ITEM_ROLE, entry)
            top.setFlags(top.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            top.setCheckState(0, Qt.CheckState.Checked if entry.relevant else Qt.CheckState.Unchecked)
            top.setFont(0, bold)
            top.setFont(1, bold)
            top.setForeground(1, QBrush(QColor(styles.color(_STATUS_COLORS[status]))))
            top.setTextAlignment(2, right)
            if not entry.relevant:
                for column in (0, 2, 3, 4, 5, 6):
                    top.setForeground(column, muted)

            for previous in entry.previous:
                outstanding = previous in entry.outstanding
                expected = expected_arrival(previous)
                child = QTreeWidgetItem(
                    [
                        f"Vorjahr: {previous.description or previous.cost_type.name}",
                        "Nachfolger fehlt" if outstanding else "Nachfolger liegt vor",
                        "",
                        "",
                        f"{format_money(previous.amount)} · {format_period_short(previous.period)}",
                        f"Rechnung vom {format_date(previous.invoice_date)}"
                        if previous.invoice_date
                        else "ohne Rechnungsdatum",
                        f"um den {format_date(expected)}" if outstanding and expected else "",
                    ]
                )
                for column in range(7):
                    child.setForeground(column, QBrush(QColor(styles.color("muted"))))
                top.addChild(child)

            self._checklist.addTopLevelItem(top)
            # Offene Positionen aufklappen: Dort hilft die Gedächtnisstütze.
            top.setExpanded(entry.relevant and status is not ChecklistStatus.COMPLETE)

        self._building_checklist = False

    @staticmethod
    def _describe_recorded(entry: ChecklistItem) -> str:
        """Was für den Zeitraum bereits erfasst ist, z. B. "2 Rechnungen · 1.234,00 €"."""
        if not entry.invoices:
            return "–"
        count = len(entry.invoices)
        noun = "Rechnung" if count == 1 else "Rechnungen"
        return f"{count} {noun} · {format_money(entry.amount_in_period)}"

    @staticmethod
    def _describe_previous(entry: ChecklistItem) -> str:
        """Gedächtnisstütze: von wem die Rechnung im Vorjahr kam."""
        if not entry.previous:
            return "–"
        if len(entry.previous) > 1:
            issuers = sorted({p.description for p in entry.previous if p.description})
            suffix = f" ({', '.join(issuers)})" if issuers else ""
            return f"{len(entry.previous)} Rechnungen{suffix}"
        previous = entry.previous[0]
        issuer = previous.description or "Rechnung"
        return f"{issuer} · {format_date(previous.invoice_date)}" if previous.invoice_date else issuer

    @staticmethod
    def _describe_expected(entry: ChecklistItem) -> str:
        """Wann die noch fehlende Rechnung voraussichtlich kommt."""
        if entry.status in (ChecklistStatus.COMPLETE, ChecklistStatus.NOT_RELEVANT):
            return ""
        expected = entry.expected_date
        if expected is None:
            return "unbekannt"
        if expected < date.today():
            return f"überfällig (um den {format_date(expected)})"
        return f"um den {format_date(expected)}"

    def _selected_group(self) -> InvoiceGroup | None:
        """Die aggregierte Rechnung des markierten Baumknotens (auch bei markiertem Kind)."""
        items = self._tree.selectedItems()
        if not items:
            return None
        item = items[0].parent() or items[0]
        group = item.data(0, _GROUP_ROLE)
        return group if isinstance(group, InvoiceGroup) else None

    def _selected_checklist_item(self) -> ChecklistItem | None:
        """Die Kostenart der markierten Checklisten-Zeile (auch bei markierter Vorjahresrechnung)."""
        items = self._checklist.selectedItems()
        if not items:
            return None
        item = items[0].parent() or items[0]
        entry = item.data(0, _ITEM_ROLE)
        return entry if isinstance(entry, ChecklistItem) else None

    def _update_buttons(self) -> None:
        invoice = self._invoice_table.selected_item()
        has_property = self._property_id() is not None
        self._add_button.setEnabled(has_property)
        self._bundle_button.setEnabled(has_property)
        self._edit_button.setEnabled(invoice is not None)
        self._duplicate_button.setEnabled(invoice is not None)
        self._delete_button.setEnabled(invoice is not None)
        self._exclude_button.setEnabled(invoice is not None)
        self._exclude_button.setText("Einbeziehen" if invoice is not None and invoice.excluded else "Ausschließen")
        self._rename_button.setEnabled(self._selected_group() is not None)
        self._checklist_add_button.setEnabled(self._selected_checklist_item() is not None)

    # ------------------------------------------------------------------ #
    # Aktionen: Rechnungen
    # ------------------------------------------------------------------ #
    def _tenant_choices(self, property_id: int) -> list[TenantChoice]:
        """Mieter der Immobilie samt Wohnungsnummer (für die direkte Zuordnung)."""
        numbers = {a.id: a.number for a in self._context.apartments.list_apartments(property_id)}
        tenants = self._context.tenants.list_tenants(property_id)
        return sorted(
            ((tenant, numbers.get(tenant.apartment_id, "")) for tenant in tenants),
            key=lambda choice: (choice[1].casefold(), choice[0].start_date),
        )

    def _open_invoice_dialog(
        self,
        invoice: Cost | None,
        default_period: DateRange | None = None,
        preselected_type_id: int | None = None,
        attachment_paths: list[Path] | None = None,
    ) -> None:
        """Öffnet den Dialog zum Anlegen (``invoice`` ist ``None``) oder Bearbeiten.

        Args:
            invoice: Die zu bearbeitende Rechnung oder ``None`` für eine neue.
            default_period: Vorschlag für den Rechnungszeitraum einer neuen
                Rechnung (Standard: der oben gewählte Zeitraum).
            preselected_type_id: Vorgewählte Kostenart einer neuen Rechnung.
            attachment_paths: Dateien, die als Belege vorgemerkt werden.
        """
        property_id = invoice.property_id if invoice else self._property_id()
        if property_id is None:
            return
        period = default_period or self._period() or DateRange.for_year(date.today().year - 1)
        dialog: CostDialog | None = None
        with ErrorGuard(self):
            dialog = CostDialog(
                self._context.costs.save_cost,
                self._context.cost_types.list_types(),
                self._tenant_choices(property_id),
                property_id,
                invoice.period if invoice else period,
                invoice,
                self,
                receipt_scan=self._context.receipt_scan,
                preselected_type_id=preselected_type_id,
                attachments=self._context.attachments,
                attachment_paths=attachment_paths or (),
            )
        if dialog is None:
            return
        dialog.duplicate_requested.connect(self._open_duplicate)
        if dialog.exec():
            self._on_property_changed()

    def _on_receipts_dropped(self, files: list[Path]) -> None:
        """Auf die Rechnungsliste gezogene Dateien: neue Rechnung mit diesen Belegen."""
        self._open_invoice_dialog(None, attachment_paths=files)

    def _edit_invoice(self) -> None:
        invoice = self._invoice_table.selected_item()
        if invoice is None:
            return
        if invoice.bundle_id is not None:
            # Eine Position gehört zur ganzen Sammelrechnung - diese bearbeiten.
            self._open_bundle_dialog(invoice.bundle_id)
        else:
            self._open_invoice_dialog(invoice)

    def _open_bundle_dialog(self, bundle_id: int | None) -> None:
        """Öffnet den Dialog für eine neue (``None``) oder bestehende Sammelrechnung."""
        property_id = self._property_id()
        if property_id is None:
            return
        period = self._period() or DateRange.for_year(date.today().year - 1)
        dialog: BundleDialog | None = None
        with ErrorGuard(self):
            bundle = self._context.bundles.get(bundle_id) if bundle_id is not None else None
            dialog = BundleDialog(
                self._context.bundles,
                self._context.attachments,
                self._context.cost_types.list_types(),
                bundle.property_id if bundle else property_id,
                period,
                bundle,
                self._context.bundles.positions(bundle_id) if bundle_id is not None else (),
                self,
            )
        if dialog is not None and dialog.exec():
            self._on_property_changed()

    def _duplicate_invoice(self) -> None:
        """Dupliziert die markierte Rechnung."""
        invoice = self._invoice_table.selected_item()
        if invoice is not None:
            self._open_duplicate(invoice)

    def _open_duplicate(self, invoice: Cost) -> None:
        """Öffnet den Dialog für eine *neue* Rechnung mit den Werten von ``invoice``.

        Die Kopie hat keine ID (sonst würde das Original überschrieben) und das
        heutige Rechnungsdatum. Gespeichert wird erst mit "Speichern".
        """
        # Die Kopie ist eine eigenständige Rechnung - auch wenn das Original zu
        # einer Sammelrechnung gehört.
        self._open_invoice_dialog(replace(invoice, id=None, bundle_id=None, invoice_date=date.today()))

    def _toggle_invoice_excluded(self) -> None:
        invoice = self._invoice_table.selected_item()
        if invoice is None or invoice.id is None:
            return
        with ErrorGuard(self):
            self._context.costs.set_excluded(invoice.id, not invoice.excluded)
        self._reload()

    def _delete_invoice(self) -> None:
        invoice = self._invoice_table.selected_item()
        if invoice is None or invoice.id is None:
            return
        question = f"'{invoice.title}' ({format_money(invoice.amount)}) wirklich löschen?"
        if invoice.bundle_id is not None:
            question += (
                "\n\nDie Position gehört zu einer Sammelrechnung. Gelöscht wird nur diese Position; "
                "mit der letzten Position verschwinden auch die Sammelrechnung und ihre Belege."
            )
        if not confirm(self, "Rechnung löschen", question):
            return
        with ErrorGuard(self):
            self._context.costs.delete_cost(invoice.id)
            if invoice.bundle_id is not None:
                self._context.bundles.remove_if_empty(invoice.bundle_id)
        self._on_property_changed()

    def _manage_cost_types(self) -> None:
        CostTypeManagerDialog(self._context.cost_types, self).exec()
        self._reload()

    # ------------------------------------------------------------------ #
    # Aktionen: aggregierte Rechnungen
    # ------------------------------------------------------------------ #
    def _on_tree_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Reagiert auf das Häkchen einer aggregierten Rechnung."""
        if self._building_tree or column != 0:
            return
        group = item.data(0, _GROUP_ROLE)
        if not isinstance(group, InvoiceGroup):
            return
        excluded = item.checkState(0) != Qt.CheckState.Checked
        if excluded == group.excluded:
            return
        with ErrorGuard(self):
            self._context.invoice_groups.set_excluded(group, excluded)
        # Nicht sofort neu aufbauen: Dieses Signal kommt mitten aus der
        # Verarbeitung des Mausklicks. Würde der Baum jetzt geleert, griffe Qt
        # danach auf den gerade gelöschten Eintrag zu - das Programm stürzte ab.
        QTimer.singleShot(0, self._reload)

    def _rename_group(self) -> None:
        group = self._selected_group()
        if group is None:
            return
        name, accepted = QInputDialog.getText(
            self, "Aggregierte Rechnung umbenennen", "Name (leer = Kostenart + Jahr):", text=group.custom_name
        )
        if not accepted:
            return
        with ErrorGuard(self):
            self._context.invoice_groups.rename(group, name)
        self._reload()

    # ------------------------------------------------------------------ #
    # Aktionen: Checkliste
    # ------------------------------------------------------------------ #
    def _on_checklist_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Reagiert auf das Relevanz-Häkchen einer Kostenart."""
        if self._building_checklist or column != 0:
            return
        entry = item.data(0, _ITEM_ROLE)
        property_id = self._property_id()
        if not isinstance(entry, ChecklistItem) or entry.cost_type.id is None or property_id is None:
            return
        relevant = item.checkState(0) == Qt.CheckState.Checked
        if relevant == entry.relevant:
            return
        with ErrorGuard(self):
            self._context.invoice_checklist.set_relevant(property_id, entry.cost_type.id, relevant)
        # Erst nach dem Mausklick neu aufbauen (siehe _on_tree_item_changed).
        QTimer.singleShot(0, self._reload_checklist)

    def _add_invoice_for_checklist_item(self) -> None:
        """Legt eine Rechnung für die markierte Kostenart an - vorbelegt mit dem fehlenden Zeitraum."""
        entry = self._selected_checklist_item()
        if entry is None:
            return
        self._open_invoice_dialog(
            None,
            default_period=entry.missing[0] if entry.missing else entry.period,
            preselected_type_id=entry.cost_type.id,
        )
