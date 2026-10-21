"""Dialog für Rechnungen (Kostenpositionen)."""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path
from decimal import Decimal
from typing import Callable, Sequence

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QApplication,
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
    WATER_RULES_LABEL,
    Cost,
    validate_splits,
)
from models.cost_type import CostType
from models.document import DocumentDraft
from models.enums import AllocationKey, MeterType
from models.exceptions import AppError, ValidationError
from models.receipt import ReceiptSuggestion
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from integrations.windows_ocr import WindowsOcr
from services.attachment_service import AttachmentService
from services.document_service import DocumentService
from services.receipt_scan_service import ReceiptScanService
from ui.dialogs.base_dialog import FormDialog
from ui.dialogs.receipt_scan_dialog import OPTIONAL_HINT, scan_receipt_file
from ui.dialogs.splits_dialog import SplitsDialog
from ui.helpers import ErrorGuard, FileDropFilter, fill_combo, make_button, select_data
from ui.receipt_images import OCR_MAX_EDGE, OCR_MAX_PAGES, has_usable_text, load_receipt_images, pdf_text
from ui.widgets.attachments import AttachmentsEditor, attachment_file_filter
from ui.widgets.common import DateEdit, DecimalSpin, MoneySpin
from utils.formatters import format_money

logger = logging.getLogger(__name__)

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
        vat_lookup: Callable[[int], Decimal | None] | None = None,
        documents: DocumentService | None = None,
        ocr: WindowsOcr | None = None,
    ) -> None:
        """
        Args:
            documents: Belege ohne KI auslesen (Text aus PDFs) und aus den
                Korrekturen beim Speichern lernen.
            ocr: Windows-Texterkennung für Fotos und gescannte PDFs (``None``,
                wenn auf diesem Rechner nicht verfügbar).
            vat_lookup: Liefert zu einer Kostenart den Umsatzsteuersatz, der bei
                einer früheren Rechnung eingetragen wurde (Vorschlag im Split-Dialog).
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
        self._vat_lookup = vat_lookup
        self._documents = documents
        self._ocr = ocr
        #: Der zuletzt ohne KI gelesene Beleg - aus ihm wird beim Speichern gelernt.
        self._draft: DocumentDraft | None = None
        # quick_status(): sofortige Antwort - das Öffnen des Dialogs wartet nicht auf Ollama.
        self._ai_available = receipt_scan is not None and receipt_scan.quick_status().available
        self._scan_available = self._ai_available or documents is not None
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

        # CO2-Angaben laut Rechnung (CO2-Kostenaufteilungsgesetz) - nur bei Brennstoff-/Heizkosten.
        # Sie werden nicht umgelegt, sondern dem Messdienst gemeldet (Tab "Rechnungen", Jahressumme).
        self._co2_cost_spin = MoneySpin()
        self._co2_emission_spin = DecimalSpin(decimals=2, suffix=" kg")
        self._co2_factor_spin = DecimalSpin(decimals=7, maximum=10.0, suffix=" kg/kWh")
        for spin, tip in (
            (self._co2_cost_spin, "CO2-Kosten brutto laut Rechnung (inkl. Umsatzsteuer)"),
            (self._co2_emission_spin, "CO2-Ausstoß bzw. Brennstoffemissionen laut Rechnung in kg"),
            (self._co2_factor_spin, "Emissionsfaktor laut Rechnung (optional), z. B. 0,18139 kg/kWh"),
        ):
            spin.setSpecialValueText("–")
            spin.setToolTip(tip)
        co2_row = QHBoxLayout()
        for caption, spin in (
            ("Kosten brutto", self._co2_cost_spin),
            ("Ausstoß", self._co2_emission_spin),
            ("Emissionsfaktor", self._co2_factor_spin),
        ):
            label = QLabel(caption)
            label.setObjectName("Muted")
            co2_row.addWidget(label)
            co2_row.addWidget(spin, 1)
        self._co2_row = QWidget()
        self._co2_row.setLayout(co2_row)
        co2_row.setContentsMargins(0, 0, 0, 0)

        # Nur Heiz-/Warmwasserkosten: Rechnet der Messdienst das Kaltwasser für das Warmwasser mit ab?
        self._cold_water_check = QCheckBox("Enthält das Kaltwasser für die Warmwasserbereitung")
        self._cold_water_check.setToolTip(
            "Setzen, wenn diese Rechnung (z. B. die Messdienst-Abrechnung der verbundenen Heizungs- und "
            "Warmwasserversorgung) die Kosten des Kaltwassers für das Warmwasser schon enthält. Dann weist die "
            "Wasserregel diesen Anteil nicht noch einmal als eigene Position aus."
        )

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
                    "wie gemessener Verbrauch"
                    if key is AllocationKey.CONSUMPTION
                    else WATER_RULES_LABEL
                    if key is AllocationKey.WATER_RULES
                    else f"nach {key.label}",
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
        self.form.addRow("CO2 laut Rechnung", self._co2_row)
        self.form.addRow("Warmwasser", self._cold_water_check)
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
            self._co2_cost_spin.set_money(cost.co2_cost or Money.zero())
            self._co2_emission_spin.set_decimal(cost.co2_emission or Decimal(0))
            self._co2_factor_spin.set_decimal(cost.co2_factor or Decimal(0))
            self._excluded_check.setChecked(cost.excluded)
            self._cold_water_check.setChecked(cost.includes_cold_water)
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
            scan_button = make_button("Beleg auslesen …", "camera")
            scan_button.clicked.connect(self._scan_receipt)
            layout.addWidget(scan_button)
            sources = ["PDFs direkt"]
            if self._ocr is not None:
                sources.append("Fotos und Scans per Windows-Texterkennung")
            if self._ai_available:
                sources.append("bei Bedarf mit der KI-Belegerkennung")
            text = (
                "Liest Absender, Datum, Zeitraum, Betrag, Verbrauch, Preise und Zwischenablesungen aus "
                f"dem Beleg ({', '.join(sources)}) und lernt aus Ihren Korrekturen."
            )
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
        """Liest eine Belegdatei aus und trägt die Werte als Vorschlag ein.

        Reihenfolge: Text aus dem PDF (sofort, ohne KI) - sonst Windows-
        Texterkennung (Fotos, Scans) - und nur wenn beides nichts ergibt, die
        optionale KI-Belegerkennung.
        """
        names = [cost_type.name for cost_type in self._cost_types]
        if self._documents is not None:
            text, source = self._document_text(path)
            if text:
                draft = self._documents.read(text, names, source)
                if not draft.is_empty:
                    self._apply_draft(draft)
                    return
        if self._ai_available and self._receipt_scan is not None:
            suggestion = scan_receipt_file(self, self._receipt_scan, names, path)
            if suggestion is not None:
                self._draft = None
                self._apply_suggestion(suggestion)
            return
        self._scan_info.setObjectName("ErrorLabel")
        self._scan_info.setText(
            "In diesem Beleg wurde kein lesbarer Text gefunden. "
            + (
                "Für Fotos und Scans fehlt die Windows-Texterkennung (Sprachpaket Deutsch)."
                if self._ocr is None
                else "Auch die Texterkennung hat nichts Verwertbares erkannt."
            )
        )
        self._scan_info.style().polish(self._scan_info)

    def _document_text(self, path: Path) -> tuple[str, str]:
        """Text des Belegs: aus dem PDF, sonst per Texterkennung. ``("", "")``, wenn keiner zu bekommen ist."""
        with ErrorGuard(self) as guard:
            if path.suffix.lower() == ".pdf":
                text = pdf_text(path)
                if has_usable_text(text):
                    return text, "pdf"
            if self._ocr is None:
                return "", ""
            QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
            try:
                text = self._ocr.recognize(load_receipt_images(path, max_pages=OCR_MAX_PAGES, max_edge=OCR_MAX_EDGE))
            finally:
                QApplication.restoreOverrideCursor()
            return (text, "ocr") if has_usable_text(text) else ("", "")
        if not guard.ok:
            return "", ""
        return "", ""

    def _apply_draft(self, draft: DocumentDraft) -> None:
        """Überträgt einen ohne KI gelesenen Vorschlag in die Felder; Nicht-Erkanntes bleibt unverändert."""
        self._draft = draft
        applied: list[str] = []
        index = -1
        if draft.cost_type_id is not None:
            index = next((i for i, t in enumerate(self._cost_types) if t.id == draft.cost_type_id), -1)
        if index < 0 and draft.cost_type_name:
            index = next((i for i, t in enumerate(self._cost_types) if t.name == draft.cost_type_name), -1)
        if index >= 0:
            self._type_combo.setCurrentIndex(index)
            applied.append("Kostenart")
        if draft.allocation_key is not None and self._key_combo.findData(draft.allocation_key) >= 0:
            select_data(self._key_combo, draft.allocation_key)
        if draft.description:
            self._description_edit.setText(draft.description)
            applied.append("Beschreibung")
        if draft.invoice_date is not None:
            self._has_invoice_date.setChecked(True)
            self._invoice_date_edit.set_value(draft.invoice_date)
            applied.append("Rechnungsdatum")
        if draft.period is not None:
            self._from_edit.set_value(draft.period.start)
            self._to_edit.set_value(draft.period.end)
            applied.append("Zeitraum")
        if draft.amount is not None:
            self._amount_spin.set_money(draft.amount)
            applied.append("Betrag")
        if draft.consumption is not None:
            self._consumption_spin.set_decimal(draft.consumption)
            if draft.consumption_unit:
                self._unit_edit.setText(draft.consumption_unit)
            applied.append("Verbrauch")
        if draft.co2_cost is not None or draft.co2_emission is not None:
            self._co2_cost_spin.set_money(draft.co2_cost or Money.zero())
            self._co2_emission_spin.set_decimal(draft.co2_emission or Decimal(0))
            self._co2_factor_spin.set_decimal(draft.co2_factor or Decimal(0))
            applied.append("CO2-Angaben")
        if draft.splits and draft.period is not None and draft.amount is not None:
            try:
                validate_splits(draft.splits, draft.period, draft.amount, draft.consumption)
            except ValidationError as exc:
                logger.info("Erkannte Splits passen nicht zur Rechnung: %s", exc)
            else:
                self._splits = list(draft.splits)
                self._update_splits_label()
                applied.append(f"{len(draft.splits)} Splits mit Preisen")

        source = "aus dem PDF-Text" if draft.source == "pdf" else "per Windows-Texterkennung"
        if not applied:
            self._scan_info.setObjectName("ErrorLabel")
            self._scan_info.setText(f"Im Beleg ({source}) wurde nichts Verwertbares erkannt.")
        else:
            self._scan_info.setObjectName("Muted")
            text = f"Übernommen ({source}, ohne KI): {', '.join(applied)}."
            if draft.profile is not None:
                text += (
                    f" Gelerntes von {draft.profile.times_used} früheren Beleg(en) dieses Absenders berücksichtigt"
                    " (ansehen und ändern: Benutzer-Menü → Gelernte Absender …)."
                )
            text += " Bitte prüfen - Korrekturen merkt sich das Programm beim Speichern."
            self._scan_info.setText(text)
        self._scan_info.style().polish(self._scan_info)

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
            default_vat=self._default_vat(),
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

    def _default_vat(self) -> Decimal:
        """Vorschlag für den Umsatzsteuersatz im Split-Dialog.

        Am zuverlässigsten ist der Satz, der bei einer früheren Rechnung derselben
        Kostenart eingetragen wurde. Ohne eine solche gilt: Trinkwasser 7 %,
        alles andere (Gas, Strom, Fernwärme, Dienstleistungen) 19 %.
        """
        cost_type = self._selected_type()
        if cost_type is not None and cost_type.id is not None and self._vat_lookup is not None:
            remembered = self._vat_lookup(cost_type.id)
            if remembered is not None:
                return remembered
        if cost_type is not None and cost_type.meter_type is MeterType.COLD_WATER:
            return Decimal(7)
        return Decimal(19)

    def _update_splits_label(self) -> None:
        """Zeigt, ob und wie viele Splits hinterlegt sind."""
        if self._splits:
            text = f"{len(self._splits)} Split(s) hinterlegt – die Jahreszuordnung folgt den Splits."
            corrected = sum(1 for split in self._splits if split.amount_corrected)
            if corrected:
                text += f" {corrected} Betrag/Beträge von Hand korrigiert."
            if any(split.is_difference for split in self._splits):
                text += " Mit Differenz-Eintrag (linear verteilt)."
            self._splits_label.setText(text)
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
        # CO2-Kostenaufteilungsgesetz: gilt für Brennstoffe von Heizung und Warmwasser.
        self.form.setRowVisible(self._co2_row, cost_type.heating_rule)
        self.form.setRowVisible(self._cold_water_check, cost_type.heating_rule)
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
        elif key is AllocationKey.OWNER:
            hint = (
                "Die Kosten trägt der Eigentümer allein (z. B. Miete der Rauchwarnmelder, Verwaltung, "
                "Reparaturen). Sie erscheinen in keiner Mieterabrechnung, zählen aber in der Übersicht mit."
            )
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
    def _co2_values(self, cost_type: CostType) -> dict[str, object]:
        """Die CO2-Angaben aus dem Formular - leer bzw. bei anderen Kostenarten ``None``."""
        if not cost_type.heating_rule:
            return {"co2_cost": None, "co2_emission": None, "co2_factor": None}
        cost = self._co2_cost_spin.money()
        emission = self._co2_emission_spin.decimal_value()
        factor = self._co2_factor_spin.decimal_value()
        return {
            "co2_cost": cost if not cost.is_zero else None,
            "co2_emission": emission if emission > 0 else None,
            "co2_factor": factor if factor > 0 else None,
        }

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
            **self._co2_values(cost_type),
            "includes_cold_water": cost_type.heating_rule and self._cold_water_check.isChecked(),
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
        if self._draft is not None and self._documents is not None:
            # Lernen darf das Speichern nie verhindern - die Rechnung ist schon gespeichert.
            try:
                self._documents.learn(self._draft, saved)
            except AppError:
                logger.exception("Aus dem Beleg konnte nicht gelernt werden")
