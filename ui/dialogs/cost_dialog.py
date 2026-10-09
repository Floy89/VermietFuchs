"""Dialog für Rechnungen (Kostenpositionen)."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from decimal import Decimal
from typing import Callable, Sequence

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QSpinBox,
    QWidget,
)

from models.attachment import ATTACHMENT_EXTENSIONS
from models.cost import (
    InvoiceSplit,
    DEFAULT_CONSUMPTION_SHARE,
    DIFFERENCE_KEYS,
    MAX_CONSUMPTION_SHARE,
    MIN_CONSUMPTION_SHARE,
    Cost,
)
from models.cost_type import CostType
from models.enums import AllocationKey
from models.exceptions import ValidationError
from models.receipt import ReceiptSuggestion
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from services.attachment_service import AttachmentService
from services.receipt_scan_service import ReceiptScanService
from ui.dialogs.base_dialog import FormDialog
from ui.dialogs.receipt_scan_dialog import OPTIONAL_HINT, scan_receipt_file
from ui.dialogs.splits_dialog import SplitsDialog
from ui.helpers import FileDropFilter, fill_combo, make_button, select_data
from ui.widgets.attachments import AttachmentsEditor, attachment_file_filter
from ui.widgets.common import DateEdit, DecimalSpin, MoneySpin
from utils.formatters import format_money

#: Ein Mieter zusammen mit der Bezeichnung seiner Wohnung.
TenantChoice = tuple[Tenant, str]


class DirectSharesEditor(QWidget):
    """Eingabe der Beträge je Mieter für den Schlüssel "Direkte Zuordnung".

    Signals:
        changed: Ein Betrag wurde geändert.
    """

    changed = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setHorizontalSpacing(12)
        self._grid.setVerticalSpacing(6)
        self._grid.setColumnStretch(0, 1)
        self._spins: dict[int, MoneySpin] = {}

    def set_tenants(self, tenants: Sequence[TenantChoice], values: dict[int, Money]) -> None:
        """Baut die Eingabezeilen neu auf.

        Args:
            tenants: Die Mieter, für die ein Betrag eingetragen werden kann.
            values: Vorbelegung je Mieter-ID (bereits eingegebene Beträge).
        """
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget() if item else None
            if widget is not None:
                widget.deleteLater()
        self._spins.clear()

        for row, (tenant, apartment_label) in enumerate(tenants):
            if tenant.id is None:
                continue
            spin = MoneySpin()
            spin.set_money(values.get(tenant.id, Money.zero()))
            spin.valueChanged.connect(lambda *_: self.changed.emit())
            self._grid.addWidget(QLabel(f"{tenant.name} ({apartment_label})"), row, 0)
            self._grid.addWidget(spin, row, 1)
            self._spins[tenant.id] = spin

        if not self._spins:
            hint = QLabel("Im gewählten Zeitraum gibt es keine Mieter.")
            hint.setObjectName("Muted")
            self._grid.addWidget(hint, 0, 0)

    def shares(self) -> dict[int, Money]:
        """Die eingegebenen Beträge je Mieter-ID (ohne Nullbeträge)."""
        return {
            tenant_id: spin.money() for tenant_id, spin in self._spins.items() if not spin.money().is_zero
        }


class CostDialog(FormDialog):
    """Rechnung anlegen oder bearbeiten.

    Die Auswahl des Umlageschlüssels richtet sich nach der Kostenart: Es
    werden nur die für sie zulässigen Schlüssel angeboten.

    Signals:
        duplicate_requested: Der Benutzer möchte die Rechnung duplizieren;
            Argument ist die Rechnung. Der Dialog ist dann bereits geschlossen.
    """

    duplicate_requested = pyqtSignal(object)

    def __init__(
        self,
        save: Callable[[Cost], Cost],
        cost_types: Sequence[CostType],
        tenants: Sequence[TenantChoice],
        property_id: int,
        default_period: DateRange,
        cost: Cost | None = None,
        parent: QWidget | None = None,
        receipt_scan: ReceiptScanService | None = None,
        preselected_type_id: int | None = None,
        attachments: AttachmentService | None = None,
        attachment_paths: Sequence[Path] = (),
    ) -> None:
        """
        Args:
            attachments: Service der Belege. Ist er angegeben, lassen sich
                PDFs und Fotos an die Rechnung hängen (auch per Drag-and-drop).
            attachment_paths: Dateien, die gleich als Belege vorgemerkt werden
                (z. B. auf den Tab gezogene Dateien).
            preselected_type_id: Bei einer neuen Rechnung vorgewählte Kostenart
                (z. B. beim Anlegen aus der Checkliste).
            receipt_scan: Optionale Belegerkennung. Ist sie eingerichtet,
                erscheint die Schaltfläche "Beleg scannen"; sonst nur ein Hinweis.
            save: Funktion, die die Position speichert (Service-Methode).
            cost_types: Alle auswählbaren Kostenarten.
            tenants: Mieter der Immobilie (für die direkte Zuordnung).
            property_id: Immobilie, für die die Kosten angefallen sind.
            default_period: Vorschlag für den Zeitraum einer neuen Position.
            cost: Zu bearbeitende Position; ``None`` legt eine neue an. Eine
                Position ohne ID dient als Vorlage für eine neue (Duplikat).
        """
        is_existing = cost is not None and cost.id is not None
        super().__init__("Rechnung bearbeiten" if is_existing else "Neue Rechnung", parent)
        self.setMinimumWidth(540)
        self._save = save
        self._tenants = list(tenants)
        self._property_id = property_id
        self._cost = cost
        self._receipt_scan = receipt_scan
        self._cost_types = list(cost_types)
        # quick_status(): sofortige Antwort - das Öffnen des Dialogs wartet nicht auf Ollama.
        self._scan_available = receipt_scan is not None and receipt_scan.quick_status().available
        self._attachments_editor: AttachmentsEditor | None = None
        if attachments is not None:
            self._attachments_editor = AttachmentsEditor(
                attachments, cost.id if cost else None, scan_available=self._scan_available
            )
            self._attachments_editor.scan_requested.connect(self._scan_file)

        self.form.addRow(self._build_scan_row())

        self._type_combo = QComboBox()
        self._type_combo.setMaxVisibleItems(18)
        fill_combo(self._type_combo, [(cost_type.name, cost_type) for cost_type in cost_types])
        self._basis_label = QLabel()
        self._basis_label.setObjectName("Muted")
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText("z. B. Stadtwerke, Rechnung 4711")
        self._amount_spin = MoneySpin()
        self._from_edit = DateEdit(default_period.start)
        self._to_edit = DateEdit(default_period.end)
        self._period_hint = QLabel(
            "Rechnungszeitraum laut Beleg. Ragt er über das Abrechnungsjahr hinaus, "
            "wird der Betrag automatisch taggenau anteilig berücksichtigt."
        )
        self._period_hint.setObjectName("Muted")
        self._period_hint.setWordWrap(True)
        # Rechnungsdatum ist optional - ohne Häkchen bleibt es leer.
        self._has_invoice_date = QCheckBox()
        self._invoice_date_edit = DateEdit()
        self._invoice_date_edit.setEnabled(False)
        self._has_invoice_date.toggled.connect(self._invoice_date_edit.setEnabled)
        invoice_date_row = QHBoxLayout()
        invoice_date_row.addWidget(self._has_invoice_date)
        invoice_date_row.addWidget(self._invoice_date_edit, 1)

        # Gesamtverbrauch laut Rechnung: für die Meldung an den Messdienst und -
        # beim Schlüssel "Verbrauch" - als Bezugsmenge für die Differenz zu den Wohnungszählern.
        self._consumption_spin = DecimalSpin(decimals=3)
        self._consumption_spin.setSpecialValueText("–")  # 0 bedeutet "nicht angegeben"
        self._unit_edit = QLineEdit()
        self._unit_edit.setPlaceholderText("Einheit, z. B. kWh")
        self._unit_edit.setMaximumWidth(150)
        consumption_row = QHBoxLayout()
        consumption_row.addWidget(self._consumption_spin, 1)
        consumption_row.addWidget(self._unit_edit)

        # Splits (Teilabschnitte laut Rechnung) werden in einem eigenen Dialog gepflegt.
        self._splits: list[InvoiceSplit] = list(cost.splits) if cost else []
        self._splits_button = make_button(
            "Splits / Berechnung …",
            "edit",
            tooltip="Abschnitte der Rechnung erfassen - oder Verbrauch und Kosten aus Volumen und Preisen berechnen",
        )
        self._splits_button.clicked.connect(self._edit_splits)
        self._splits_label = QLabel()
        self._splits_label.setObjectName("Muted")
        self._splits_label.setWordWrap(True)
        splits_row = QHBoxLayout()
        splits_row.addWidget(self._splits_button)
        splits_row.addWidget(self._splits_label, 1)

        self._excluded_check = QCheckBox("Aus Abrechnung ausschließen")
        self._excluded_check.setToolTip("Die Rechnung wird weder aggregiert noch auf die Mieter umgelegt.")

        self._key_combo = QComboBox()
        self._share_spin = QSpinBox()
        self._share_spin.setRange(MIN_CONSUMPTION_SHARE, MAX_CONSUMPTION_SHARE)
        self._share_spin.setSuffix(" % nach Verbrauch")
        self._share_spin.setValue(DEFAULT_CONSUMPTION_SHARE)
        self._key_hint = QLabel()
        self._key_hint.setObjectName("Muted")
        self._key_hint.setWordWrap(True)
        self._direct_editor = DirectSharesEditor()
        self._difference_combo = QComboBox()
        self._difference_combo.setToolTip(
            "Nur von Bedeutung, wenn ein Gesamtverbrauch laut Rechnung eingetragen ist, der über der "
            "Summe der Wohnungszähler liegt (z. B. Hauptzähler 200 m³, Wohnungszähler zusammen 148 m³)."
        )
        fill_combo(
            self._difference_combo,
            [
                (
                    "wie gemessener Verbrauch" if key is AllocationKey.CONSUMPTION else f"nach {key.label}",
                    key,
                )
                for key in DIFFERENCE_KEYS
            ],
        )

        self.form.addRow("Kostenart", self._type_combo)
        self.form.addRow("", self._basis_label)
        self.form.addRow("Beschreibung", self._description_edit)
        self.form.addRow("Rechnungsdatum", invoice_date_row)
        self.form.addRow("Gesamtbetrag", self._amount_spin)
        self.form.addRow("Zeitraum von", self._from_edit)
        self.form.addRow("Zeitraum bis", self._to_edit)
        self.form.addRow("", self._period_hint)
        self.form.addRow("Verbrauch laut Rechnung", consumption_row)
        self.form.addRow("Splits", splits_row)
        self.form.addRow("Umlageschlüssel", self._key_combo)
        self.form.addRow("Verbrauchsanteil", self._share_spin)
        self.form.addRow("Verbrauchsdifferenz", self._difference_combo)
        self.form.addRow("Betrag je Mieter", self._direct_editor)
        self.form.addRow("", self._key_hint)
        if self._attachments_editor is not None:
            self.form.addRow("Belege", self._attachments_editor)
        self.form.addRow("", self._excluded_check)

        if cost is None and preselected_type_id is not None:
            self._type_combo.setCurrentIndex(
                next((i for i, t in enumerate(cost_types) if t.id == preselected_type_id), 0)
            )
        if cost:
            # Kostenarten werden bei jedem Laden neu erzeugt - deshalb über
            # die ID suchen statt über das Objekt.
            index = next((i for i, t in enumerate(cost_types) if t.id == cost.cost_type.id), 0)
            self._type_combo.setCurrentIndex(index)
            self._description_edit.setText(cost.description)
            self._amount_spin.set_money(cost.amount)
            self._from_edit.set_value(cost.period.start)
            self._to_edit.set_value(cost.period.end)
            self._share_spin.setValue(cost.consumption_share)
            if cost.invoice_date:
                self._has_invoice_date.setChecked(True)
                self._invoice_date_edit.set_value(cost.invoice_date)
            if cost.consumption is not None:
                self._consumption_spin.set_decimal(cost.consumption)
            self._unit_edit.setText(cost.consumption_unit)
            self._excluded_check.setChecked(cost.excluded)
            select_data(self._difference_combo, cost.difference_key)

        self._update_splits_label()
        self._update_keys(use_default=cost is None)
        if cost:
            select_data(self._key_combo, cost.allocation_key)
        self._rebuild_direct_editor(cost.direct_shares if cost else {})
        self._update_key_details()

        if is_existing:
            duplicate_button = make_button(
                "Duplizieren", "copy", tooltip="Neue Rechnung mit diesen Werten anlegen (ohne zu speichern)"
            )
            duplicate_button.clicked.connect(self._on_duplicate)
            self.add_action_button(duplicate_button)

        self.add_attachment_files(attachment_paths)
        if self._attachments_editor is not None:
            # Erst jetzt, wenn alle Felder existieren: Dateien lassen sich überall
            # im Dialog ablegen, auch über Eingabefeldern.
            self._drop_filter = FileDropFilter(self, ATTACHMENT_EXTENSIONS, self.add_attachment_files)

        self._type_combo.currentIndexChanged.connect(lambda *_: self._update_keys(use_default=True))
        self._key_combo.currentIndexChanged.connect(lambda *_: self._update_key_details())
        self._share_spin.valueChanged.connect(lambda *_: self._update_key_details())
        self._amount_spin.valueChanged.connect(lambda *_: self._update_key_details())
        self._direct_editor.changed.connect(self._update_key_details)
        self._from_edit.dateChanged.connect(lambda *_: self._on_period_changed())
        self._to_edit.dateChanged.connect(lambda *_: self._on_period_changed())

    # ------------------------------------------------------------------ #
    # Optionale Belegerkennung
    # ------------------------------------------------------------------ #
    def _build_scan_row(self) -> QWidget:
        """Oberste Zeile: Scan-Schaltfläche, wenn eingerichtet - sonst nur ein Hinweis."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 4)
        self._scan_info = QLabel()
        self._scan_info.setObjectName("Muted")
        self._scan_info.setWordWrap(True)

        if self._scan_available:
            scan_button = make_button("Beleg scannen …", "camera")
            scan_button.clicked.connect(self._scan_receipt)
            layout.addWidget(scan_button)
            text = "Liest Rechnungssteller, Zeitraum, Betrag und Kostenart aus einem PDF oder Foto."
            if self._attachments_editor is not None:
                text += " Der Beleg wird dabei an die Rechnung gehängt."
            self._scan_info.setText(text)
        else:
            self._scan_info.setText(OPTIONAL_HINT)
        layout.addWidget(self._scan_info, 1)
        return row

    def _scan_receipt(self) -> None:
        """Fragt nach einem Beleg, hängt ihn an die Rechnung und liest ihn aus."""
        file_name, _ = QFileDialog.getOpenFileName(self, "Beleg auswählen", "", attachment_file_filter())
        if not file_name:
            return
        path = Path(file_name)
        if self._attachments_editor is not None and not self._attachments_editor.add_file(path):
            return
        self._scan_file(path)

    def _scan_file(self, path: Path) -> None:
        """Lässt eine Belegdatei auslesen und trägt die Werte als Vorschlag ein."""
        if self._receipt_scan is None:
            return
        names = [cost_type.name for cost_type in self._cost_types]
        suggestion = scan_receipt_file(self, self._receipt_scan, names, path)
        if suggestion is not None:
            self._apply_suggestion(suggestion)

    # ------------------------------------------------------------------ #
    # Belege per Drag-and-drop
    # ------------------------------------------------------------------ #
    def add_attachment_files(self, paths: Sequence[Path]) -> None:
        """Merkt Dateien als Belege vor; gespeichert werden sie mit der Rechnung."""
        if self._attachments_editor is None:
            return
        for path in paths:
            self._attachments_editor.add_file(path)

    def _apply_suggestion(self, suggestion: ReceiptSuggestion) -> None:
        """Überträgt erkannte Werte in die Felder; nicht Erkanntes bleibt unverändert."""
        applied: list[str] = []
        index = next((i for i, t in enumerate(self._cost_types) if t.name == suggestion.cost_type_name), -1)
        if index >= 0:
            self._type_combo.setCurrentIndex(index)
            applied.append("Kostenart")
        if suggestion.description:
            self._description_edit.setText(suggestion.description)
            applied.append("Beschreibung")
        if suggestion.total_amount is not None:
            self._amount_spin.set_money(suggestion.total_amount)
            applied.append("Betrag")
        if suggestion.period is not None:
            self._from_edit.set_value(suggestion.period.start)
            self._to_edit.set_value(suggestion.period.end)
            applied.append("Zeitraum")
        if suggestion.invoice_date is not None:
            self._has_invoice_date.setChecked(True)
            self._invoice_date_edit.set_value(suggestion.invoice_date)
            applied.append("Rechnungsdatum")
        self._scan_info.setObjectName("ErrorLabel" if not applied else "Muted")
        self._scan_info.setText(
f"Übernommen: {', '.join(applied)}. Bitte alle Werte prüfen."
            if applied
            else "Auf dem Beleg wurde nichts Verwertbares erkannt."
        )

    # ------------------------------------------------------------------ #
    # Splits
    # ------------------------------------------------------------------ #
    def _entered_consumption(self) -> Decimal | None:
        """Der eingetragene Gesamtverbrauch (``None``, wenn das Feld leer ist)."""
        return self._consumption_spin.decimal_value() if self._consumption_spin.value() > 0 else None

    def _edit_splits(self) -> None:
        """Öffnet den Split-Dialog mit den aktuell eingetragenen Gesamtwerten."""
        start, end = self._from_edit.value(), self._to_edit.value()
        if end < start:
            self.show_error("Bitte zuerst einen gültigen Rechnungszeitraum eintragen.")
            return
        dialog = SplitsDialog(
            DateRange(start, end),
            self._amount_spin.money(),
            self._entered_consumption(),
            self._unit_edit.text().strip(),
            self._splits,
            self,
        )
        if dialog.exec():
            self._splits = dialog.splits
            # Wurden im Dialog die berechneten Summen als Rechnungssummen
            # übernommen, gehören sie auch hier in die Felder.
            if dialog.invoice_amount != self._amount_spin.money():
                self._amount_spin.set_money(dialog.invoice_amount)
            if dialog.invoice_consumption != self._entered_consumption():
                self._consumption_spin.set_decimal(dialog.invoice_consumption or Decimal(0))
            self._update_splits_label()

    def _update_splits_label(self) -> None:
        """Zeigt, ob und wie viele Splits hinterlegt sind."""
        if self._splits:
            self._splits_label.setText(
                f"{len(self._splits)} Split(s) hinterlegt – die Jahreszuordnung folgt den Splits."
            )
        else:
            self._splits_label.setText("Keine – die Rechnung wird taggenau linear verteilt.")

    # ------------------------------------------------------------------ #
    # Reaktionen auf Eingaben
    # ------------------------------------------------------------------ #
    def _selected_type(self) -> CostType | None:
        """Die gewählte Kostenart."""
        data = self._type_combo.currentData()
        return data if isinstance(data, CostType) else None

    def _update_keys(self, use_default: bool) -> None:
        """Bietet nur die für die gewählte Kostenart zulässigen Schlüssel an."""
        cost_type = self._selected_type()
        if cost_type is None:
            return
        self._basis_label.setText(cost_type.legal_basis or "Eigene Kostenart")
        # Einheit des Verbrauchs vorschlagen, solange der Benutzer keine eingetragen hat.
        if not self._unit_edit.text().strip() and cost_type.meter_type is not None:
            self._unit_edit.setText(cost_type.meter_type.unit)
        fill_combo(self._key_combo, [(key.label, key) for key in cost_type.allowed_keys()])
        if use_default:
            select_data(self._key_combo, cost_type.default_key)
        self._update_key_details()

    def _on_period_changed(self) -> None:
        """Bei geändertem Zeitraum können andere Mieter betroffen sein."""
        self._rebuild_direct_editor(self._direct_editor.shares())
        self._update_key_details()

    def _rebuild_direct_editor(self, values: dict[int, Money]) -> None:
        """Zeigt Eingabezeilen für alle Mieter, die im Zeitraum gewohnt haben."""
        start, end = self._from_edit.value(), self._to_edit.value()
        if end < start:
            return
        period = DateRange(start, end)
        relevant = [(t, label) for t, label in self._tenants if t.occupancy(period) is not None]
        self._direct_editor.set_tenants(relevant, values)

    def _update_key_details(self) -> None:
        """Blendet die zum Schlüssel passenden Felder ein und aktualisiert den Hinweis."""
        key = self._key_combo.currentData()
        is_mixed = key is AllocationKey.CONSUMPTION_AREA
        is_direct = key is AllocationKey.DIRECT
        self.form.setRowVisible(self._share_spin, is_mixed)
        self.form.setRowVisible(self._difference_combo, key is AllocationKey.CONSUMPTION)
        self.form.setRowVisible(self._direct_editor, is_direct)

        if is_mixed:
            rest = 100 - self._share_spin.value()
            hint = (
                f"Heizkostenverordnung: {MIN_CONSUMPTION_SHARE}–{MAX_CONSUMPTION_SHARE} % nach Verbrauch, "
                f"der Rest ({rest} %) nach Wohnfläche."
            )
        elif is_direct:
            assigned = sum(self._direct_editor.shares().values(), Money.zero())
            rest_amount = self._amount_spin.money() - assigned
            hint = (
                "Beträge aus einer fertigen Einzelabrechnung übernehmen (z. B. Messdienst). "
                f"Zugeordnet: {format_money(assigned)} · beim Eigentümer verbleiben: {format_money(rest_amount)}"
            )
        elif key is AllocationKey.UNITS:
            hint = "Jede Wohnung trägt den gleichen Anteil - unabhängig von Größe und Personenzahl."
        elif key is AllocationKey.CONSUMPTION:
            hint = (
                "Verteilung nach den Zählerständen der Wohnungen im Zeitraum der Position. Ist oben ein "
                "Gesamtverbrauch eingetragen, der über der Summe der Wohnungszähler liegt, wird die Differenz "
                "wie unter „Verbrauchsdifferenz“ gewählt verteilt und in der Abrechnung ausgewiesen."
            )
        else:
            hint = ""
        self._key_hint.setText(hint)
        self.form.setRowVisible(self._key_hint, bool(hint))

    # ------------------------------------------------------------------ #
    # Duplikation
    # ------------------------------------------------------------------ #
    def _on_duplicate(self) -> None:
        """Schließt den Dialog ohne zu speichern und meldet den Wunsch zu duplizieren."""
        if self._cost is not None:
            self.reject()
            self.duplicate_requested.emit(self._cost)

    # ------------------------------------------------------------------ #
    # Speichern
    # ------------------------------------------------------------------ #
    def _apply(self) -> None:
        cost_type = self._selected_type()
        if cost_type is None:
            raise ValidationError("Bitte eine Kostenart wählen.")
        start, end = self._from_edit.value(), self._to_edit.value()
        if end < start:
            raise ValidationError("Das Ende des Zeitraums darf nicht vor dem Beginn liegen.")

        key: AllocationKey = self._key_combo.currentData()
        values = {
            "property_id": self._property_id,
            "cost_type": cost_type,
            "description": self._description_edit.text(),
            "amount": self._amount_spin.money(),
            "period": DateRange(start, end),
            "allocation_key": key,
            "consumption_share": self._share_spin.value(),
            "direct_shares": self._direct_editor.shares() if key is AllocationKey.DIRECT else {},
            "invoice_date": self._invoice_date_edit.value() if self._has_invoice_date.isChecked() else None,
            "consumption": self._entered_consumption(),
            "consumption_unit": self._unit_edit.text() if self._consumption_spin.value() > 0 else "",
            "excluded": self._excluded_check.isChecked(),
            "difference_key": (
                self._difference_combo.currentData() if key is AllocationKey.CONSUMPTION else AllocationKey.CONSUMPTION
            ),
            # Werden Betrag, Verbrauch oder Zeitraum nach dem Erfassen der Splits
            # geändert, schlägt die Summenprüfung beim Speichern an (Cost.validate).
            "splits": list(self._splits),
        }
        cost = replace(self._cost, **values) if self._cost else Cost(**values)
        saved = self._save(cost)
        # Ab jetzt ist die Rechnung gespeichert: Scheitert danach ein Beleg,
        # darf ein zweiter Klick auf "Speichern" sie nicht noch einmal anlegen.
        self._cost = saved
        if self._attachments_editor is not None and saved.id is not None:
            self._attachments_editor.commit(saved.id)
