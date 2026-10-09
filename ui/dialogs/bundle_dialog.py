"""Dialog für Sammelrechnungen: eine Rechnung, mehrere Kostenarten."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from models.attachment import ATTACHMENT_EXTENSIONS
from models.cost import Cost
from models.cost_type import CostType
from models.enums import AllocationKey
from models.exceptions import ValidationError
from models.invoice_bundle import InvoiceBundle
from models.value_objects import DateRange, Money
from services.attachment_service import AttachmentService
from services.invoice_bundle_service import InvoiceBundleService
from ui import styles
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import FileDropFilter, fill_combo, make_button, select_data
from ui.widgets.attachments import AttachmentsEditor
from ui.widgets.common import DateEdit, MoneySpin
from utils.formatters import format_money

_COL_TYPE, _COL_AMOUNT, _COL_KEY = range(3)


@dataclass
class _Row:
    """Eine Zeile der Positionstabelle samt der Position, aus der sie entstanden ist."""

    type_combo: QComboBox
    amount_spin: MoneySpin
    key_combo: QComboBox
    #: Die gespeicherte Position (``None`` bei einer neuen Zeile). Sie wird beim
    #: Speichern aktualisiert statt neu angelegt - so bleiben ihre ID und alles
    #: erhalten, was an ihr hängt (z. B. der Ausschluss aus der Abrechnung).
    original: Cost | None = None


class BundleDialog(FormDialog):
    """Sammelrechnung anlegen oder bearbeiten.

    Oben steht, was für die ganze Rechnung gilt (Rechnungssteller, Datum,
    Zeitraum, Gesamtbetrag, Belege), darunter je Kostenart eine Position mit
    Betrag und Umlageschlüssel. Die Summe der Positionen wird laufend gegen
    den Gesamtbetrag geprüft.
    """

    def __init__(
        self,
        service: InvoiceBundleService,
        attachments: AttachmentService,
        cost_types: Sequence[CostType],
        property_id: int,
        default_period: DateRange,
        bundle: InvoiceBundle | None = None,
        positions: Sequence[Cost] = (),
        parent: QWidget | None = None,
        attachment_paths: Sequence[Path] = (),
    ) -> None:
        """
        Args:
            service: Service der Sammelrechnungen (speichert Kopf und Positionen).
            attachments: Service der Belege.
            cost_types: Alle auswählbaren Kostenarten.
            property_id: Immobilie, für die die Kosten angefallen sind.
            default_period: Vorschlag für den Zeitraum einer neuen Sammelrechnung.
            bundle: Zu bearbeitende Sammelrechnung; ``None`` legt eine neue an.
            positions: Ihre bisherigen Positionen.
            attachment_paths: Dateien, die gleich als Belege vorgemerkt werden.
        """
        super().__init__("Sammelrechnung bearbeiten" if bundle else "Neue Sammelrechnung", parent)
        self.setMinimumWidth(720)
        self._service = service
        self._cost_types = list(cost_types)
        self._property_id = property_id
        self._bundle = bundle
        self._rows: list[_Row] = []

        intro = QLabel(
            "Für Rechnungen mit mehreren Kostenarten, z. B. den Abgabenbescheid der Stadt. Jede Kostenart wird "
            "eine eigene Position mit eigenem Umlageschlüssel - der Beleg hängt einmal an der ganzen Rechnung."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)

        self._description_edit = QLineEdit(bundle.description if bundle else "")
        self._description_edit.setPlaceholderText("z. B. Stadt Bochum, Grundbesitzabgaben 2025")
        # Rechnungsdatum ist optional - ohne Häkchen bleibt es leer.
        self._has_invoice_date = QCheckBox()
        self._invoice_date_edit = DateEdit()
        self._invoice_date_edit.setEnabled(False)
        self._has_invoice_date.toggled.connect(self._invoice_date_edit.setEnabled)
        if bundle and bundle.invoice_date:
            self._has_invoice_date.setChecked(True)
            self._invoice_date_edit.set_value(bundle.invoice_date)
        invoice_date_row = QHBoxLayout()
        invoice_date_row.addWidget(self._has_invoice_date)
        invoice_date_row.addWidget(self._invoice_date_edit, 1)

        period = positions[0].period if positions else default_period
        self._from_edit = DateEdit(period.start)
        self._to_edit = DateEdit(period.end)
        period_row = QHBoxLayout()
        period_row.addWidget(self._from_edit, 1)
        until = QLabel("bis")
        until.setObjectName("Muted")
        period_row.addWidget(until)
        period_row.addWidget(self._to_edit, 1)

        self._total_spin = MoneySpin()
        self._total_spin.setSpecialValueText("– (ohne Prüfung)")  # 0 = Summe nicht prüfen
        if bundle:
            self._total_spin.set_money(bundle.total_amount)

        self._table = QTableWidget(0, 3)
        self._table.setHorizontalHeaderLabels(["Kostenart", "Betrag", "Umlageschlüssel"])
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        header = self._table.horizontalHeader()
        header.setSectionResizeMode(_COL_TYPE, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(_COL_AMOUNT, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(_COL_KEY, QHeaderView.ResizeMode.Stretch)
        self._table.setMinimumHeight(210)

        add_button = make_button("Position hinzufügen", "plus")
        remove_button = make_button("Markierte entfernen", "trash", "danger")
        self._status = QLabel()
        self._status.setWordWrap(True)
        table_buttons = QHBoxLayout()
        table_buttons.addWidget(add_button)
        table_buttons.addWidget(remove_button)
        table_buttons.addWidget(self._status, 1)
        positions_box = QVBoxLayout()
        positions_box.setSpacing(6)
        positions_box.addWidget(self._table)
        positions_box.addLayout(table_buttons)

        self._attachments_editor = AttachmentsEditor(attachments, None, bundle_id=bundle.id if bundle else None)

        self.form.addRow(intro)
        self.form.addRow("Rechnungssteller", self._description_edit)
        self.form.addRow("Rechnungsdatum", invoice_date_row)
        self.form.addRow("Zeitraum von", period_row)
        self.form.addRow("Gesamtbetrag", self._total_spin)
        self.form.addRow("Positionen", positions_box)
        self.form.addRow("Belege", self._attachments_editor)

        add_button.clicked.connect(lambda: self._add_row(None))
        remove_button.clicked.connect(self._remove_selected)
        self._total_spin.valueChanged.connect(lambda *_: self._update_status())

        for position in positions:
            self._add_row(position)
        if not positions:
            self._add_row(None)
        for path in attachment_paths:
            self._attachments_editor.add_file(path)
        # Dateien lassen sich überall im Dialog ablegen, auch über Eingabefeldern.
        self._drop_filter = FileDropFilter(self, ATTACHMENT_EXTENSIONS, self._add_attachment_files)
        self._update_status()

    # ------------------------------------------------------------------ #
    # Positionen
    # ------------------------------------------------------------------ #
    def _add_row(self, position: Cost | None) -> None:
        """Fügt eine Position an; eine neue bekommt als Vorschlag den noch offenen Restbetrag."""
        type_combo = QComboBox()
        type_combo.setMaxVisibleItems(18)
        fill_combo(type_combo, [(cost_type.name, cost_type.id) for cost_type in self._cost_types])
        amount_spin = MoneySpin()
        key_combo = QComboBox()
        row = _Row(type_combo, amount_spin, key_combo, position)

        if position is not None:
            select_data(type_combo, position.cost_type.id)
            amount_spin.set_money(position.amount)
        else:
            rest = self._total_spin.money() - self._sum()
            if rest.cents > 0:
                amount_spin.set_money(rest)
            # Nicht dieselbe Kostenart noch einmal vorschlagen.
            used = {self._selected_type(existing).id for existing in self._rows if self._selected_type(existing)}
            unused = next((t for t in self._cost_types if t.id not in used), None)
            if unused is not None:
                select_data(type_combo, unused.id)
        self._fill_keys(row, position.allocation_key if position is not None else None)

        index = self._table.rowCount()
        self._table.insertRow(index)
        self._table.setCellWidget(index, _COL_TYPE, type_combo)
        self._table.setCellWidget(index, _COL_AMOUNT, amount_spin)
        self._table.setCellWidget(index, _COL_KEY, key_combo)
        self._rows.append(row)
        type_combo.currentIndexChanged.connect(lambda *_: self._fill_keys(row, None))
        amount_spin.valueChanged.connect(lambda *_: self._update_status())
        self._table.selectRow(index)
        self._update_status()

    def _remove_selected(self) -> None:
        index = self._table.currentRow()
        if 0 <= index < len(self._rows):
            self._table.removeRow(index)
            del self._rows[index]
            self._update_status()

    def _selected_type(self, row: _Row) -> CostType | None:
        type_id = row.type_combo.currentData()
        return next((cost_type for cost_type in self._cost_types if cost_type.id == type_id), None)

    def _fill_keys(self, row: _Row, selected: AllocationKey | None) -> None:
        """Bietet die für die Kostenart zulässigen Schlüssel an (ohne direkte Zuordnung je Mieter)."""
        cost_type = self._selected_type(row)
        if cost_type is None:
            return
        keys = [key for key in cost_type.allowed_keys() if key is not AllocationKey.DIRECT]
        fill_combo(row.key_combo, [(key.label, key) for key in keys])
        select_data(row.key_combo, selected if selected in keys else cost_type.default_key)

    def _sum(self) -> Money:
        return sum((row.amount_spin.money() for row in self._rows), Money.zero())

    def _update_status(self) -> None:
        """Zeigt die Summe der Positionen und ob sie zum Gesamtbetrag passt."""
        total, current = self._total_spin.money(), self._sum()
        if total.is_zero:
            self._status.setText(f"Summe der Positionen: {format_money(current)}")
            self._status.setStyleSheet(f"color: {styles.color('muted')};")
        elif current == total:
            self._status.setText(f"✓ Summe {format_money(current)} entspricht dem Gesamtbetrag")
            self._status.setStyleSheet(f"color: {styles.color('success')}; font-weight: 600;")
        else:
            self._status.setText(
                f"Summe {format_money(current)} von {format_money(total)} · Differenz {format_money(total - current)}"
            )
            self._status.setStyleSheet(f"color: {styles.color('danger')}; font-weight: 600;")

    # ------------------------------------------------------------------ #
    # Belege
    # ------------------------------------------------------------------ #
    def _add_attachment_files(self, paths: Sequence[Path]) -> None:
        for path in paths:
            self._attachments_editor.add_file(path)

    # ------------------------------------------------------------------ #
    # Speichern
    # ------------------------------------------------------------------ #
    def _apply(self) -> None:
        start, end = self._from_edit.value(), self._to_edit.value()
        if end < start:
            raise ValidationError("Das Ende des Zeitraums darf nicht vor dem Beginn liegen.")
        period = DateRange(start, end)

        positions: list[Cost] = []
        for row in self._rows:
            cost_type = self._selected_type(row)
            key = row.key_combo.currentData()
            if cost_type is None or key is None:
                continue
            if row.original is not None:
                positions.append(
                    replace(row.original, cost_type=cost_type, amount=row.amount_spin.money(), period=period, allocation_key=key)
                )
            else:
                positions.append(Cost(self._property_id, cost_type, row.amount_spin.money(), period, key))

        bundle = InvoiceBundle(
            property_id=self._property_id,
            description=self._description_edit.text(),
            invoice_date=self._invoice_date_edit.value() if self._has_invoice_date.isChecked() else None,
            total_amount=self._total_spin.money(),
            id=self._bundle.id if self._bundle else None,
        )
        saved = self._service.save(bundle, positions)
        # Ab jetzt ist die Sammelrechnung gespeichert: Scheitert danach ein Beleg,
        # darf ein zweiter Klick auf "Speichern" sie nicht noch einmal anlegen.
        # (Der Service ersetzt dann ihre Positionen durch die Zeilen des Dialogs.)
        self._bundle = saved
        assert saved.id is not None
        self._attachments_editor.commit(bundle_id=saved.id)
