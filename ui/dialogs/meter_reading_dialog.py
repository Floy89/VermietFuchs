"""Dialog zum Erfassen eines Zählerstands, optional mit Foto."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QWidget,
)

from models.enums import MeterType
from models.exceptions import ValidationError
from models.meter_reading import MeterReading
from services.photo_service import SUPPORTED_EXTENSIONS, read_image_file
from services.receipt_scan_service import ReceiptScanService
from ui.dialogs.base_dialog import FormDialog
from ui.dialogs.receipt_scan_dialog import scan_meter_photo
from ui.helpers import FileDropFilter, confirm, fill_combo, make_button, select_data
from ui.lookups import Lookups
from ui.widgets.common import DateEdit, DecimalSpin
from utils.formatters import format_date, format_quantity

#: Funktion zum Speichern: (Zählerstand, Foto-Dateiname, Foto-Daten) -> Zählerstand
SaveReading = Callable[[MeterReading, str | None, bytes | None], MeterReading]
#: Funktion, die gespeicherte Ablesungen desselben Zählers mit gleichem Stand an anderen Tagen liefert.
FindSameValue = Callable[[MeterReading], list[MeterReading]]


def same_value_text(reading: MeterReading, matches: list[MeterReading], apartment_label: str) -> str:
    """Text der Rückfrage, wenn derselbe Zählerstand schon an einem anderen Tag erfasst ist."""
    days = ", ".join(format_date(match.date) for match in matches)
    value = f"{format_quantity(reading.value)} {reading.meter_type.unit}"
    return (
        f"{apartment_label} – {reading.meter_type.label}: Derselbe Zählerstand {value} ist bereits "
        f"am {days} erfasst.\n\n"
        "Zwischen zwei Ablesungen mit gleichem Stand wurde nichts verbraucht. Häufig ist das ein "
        "Duplikat, bei dem nur das Datum geändert wurde.\n\n"
        "Bitte Zählerstand und Ablesedatum prüfen. Trotzdem speichern?"
    )


def image_file_filter() -> str:
    """Dateifilter für den Öffnen-Dialog, z. B. ``"Bilder (*.jpg *.png)"``."""
    patterns = " ".join(f"*{extension}" for extension in SUPPORTED_EXTENSIONS)
    return f"Bilder ({patterns})"


class MeterReadingDialog(FormDialog):
    """Zählerstand anlegen oder bearbeiten.

    Ein Foto lässt sich über "Foto wählen …" aussuchen oder direkt aus dem
    Explorer auf den Dialog ziehen.

    Signals:
        duplicate_requested: Der Benutzer möchte den Zählerstand duplizieren;
            Argument ist der Zählerstand. Der Dialog ist dann bereits geschlossen.
    """

    duplicate_requested = pyqtSignal(object)

    def __init__(
        self,
        save: SaveReading,
        lookups: Lookups,
        reading: MeterReading | None = None,
        preselected_apartment_id: int | None = None,
        parent: QWidget | None = None,
        photo_path: Path | None = None,
        receipt_scan: ReceiptScanService | None = None,
        find_same_value: FindSameValue | None = None,
    ) -> None:
        """
        Args:
            save: Funktion, die Zählerstand und optionales Foto speichert.
            find_same_value: Optionale Prüfung vor dem Speichern. Liefert sie Ablesungen
                mit gleichem Stand an anderen Tagen, fragt der Dialog nach, ob trotzdem
                gespeichert werden soll.
            lookups: Stammdaten für die Wohnungsauswahl.
            reading: Zu bearbeitender Zählerstand; ``None`` legt einen neuen an.
                Ein Zählerstand ohne ID dient als Vorlage für einen neuen (Duplikat).
            preselected_apartment_id: Vorauswahl der Wohnung bei Neuanlage.
            photo_path: Bereits gewähltes Foto (z. B. per Drag-and-drop abgelegt).
            receipt_scan: Optionale Erkennung. Ist sie eingerichtet, lässt sich
                der Zählerstand vom gewählten Foto ablesen.
        """
        is_existing = reading is not None and reading.id is not None
        super().__init__("Zählerstand bearbeiten" if is_existing else "Neuer Zählerstand", parent)
        self._save = save
        self._find_same_value = find_same_value
        self._lookups = lookups
        self._reading = reading
        self._photo_path: Path | None = None
        self._receipt_scan = receipt_scan
        # quick_status(): sofortige Antwort - das Öffnen des Dialogs wartet nicht auf Ollama.
        scan_available = receipt_scan is not None and receipt_scan.quick_status().available

        self._apartment_combo = QComboBox()
        fill_combo(self._apartment_combo, lookups.apartment_choices())
        self._type_combo = QComboBox()
        fill_combo(self._type_combo, [(meter.label, meter) for meter in MeterType])
        self._value_spin = DecimalSpin(decimals=3)
        self._date_edit = DateEdit()
        self._reader_edit = QLineEdit()
        self._reader_edit.setPlaceholderText("Wer hat abgelesen?")
        self._note_edit = QLineEdit()

        self._photo_label = QLabel("Kein Foto ausgewählt – hierher ziehen oder wählen")
        self._photo_label.setObjectName("Muted")
        choose_button = make_button("Foto wählen …", "camera")
        choose_button.clicked.connect(self._choose_photo)
        photo_row = QHBoxLayout()
        photo_row.addWidget(self._photo_label, 1)
        photo_row.addWidget(choose_button)
        self._scan_button = make_button(
            "Stand ablesen", "gauge", tooltip="Zählerstand vom gewählten Foto ablesen lassen (Vorschlag, bitte prüfen)"
        )
        self._scan_button.setVisible(scan_available)
        self._scan_button.setEnabled(False)
        self._scan_button.clicked.connect(self._scan_photo)
        photo_row.addWidget(self._scan_button)
        self._scan_info = QLabel()
        self._scan_info.setObjectName("Muted")
        self._scan_info.setWordWrap(True)

        self.form.addRow("Wohnung", self._apartment_combo)
        self.form.addRow("Zählerart", self._type_combo)
        self.form.addRow("Zählerstand", self._value_spin)
        self.form.addRow("Ablesedatum", self._date_edit)
        self.form.addRow("Ableser", self._reader_edit)
        self.form.addRow("Bemerkung", self._note_edit)
        self.form.addRow("Foto hinzufügen" if is_existing else "Foto", photo_row)
        self.form.addRow("", self._scan_info)
        self.form.setRowVisible(self._scan_info, False)

        self._type_combo.currentIndexChanged.connect(self._update_unit)

        if is_existing:
            duplicate_button = make_button(
                "Duplizieren", "copy", tooltip="Neuen Zählerstand mit diesen Werten anlegen (ohne zu speichern)"
            )
            duplicate_button.clicked.connect(self._on_duplicate)
            self.add_action_button(duplicate_button)

        if reading:
            select_data(self._apartment_combo, reading.apartment_id)
            select_data(self._type_combo, reading.meter_type)
            self._value_spin.set_decimal(reading.value)
            self._date_edit.set_value(reading.date)
            self._reader_edit.setText(reading.reader)
            self._note_edit.setText(reading.note)
        elif preselected_apartment_id is not None:
            select_data(self._apartment_combo, preselected_apartment_id)
        self._update_unit()
        # Fotos lassen sich überall im Dialog ablegen, auch über Eingabefeldern.
        self._drop_filter = FileDropFilter(self, SUPPORTED_EXTENSIONS, lambda files: self._set_photo(files[0]))
        if photo_path is not None:
            self._set_photo(photo_path)

    def _update_unit(self) -> None:
        """Zeigt die zur Zählerart passende Einheit hinter dem Wert an."""
        meter_type: MeterType = self._type_combo.currentData()
        self._value_spin.setSuffix(f" {meter_type.unit}")

    def _choose_photo(self) -> None:
        """Öffnet den Dateidialog zur Auswahl eines Fotos."""
        file_name, _ = QFileDialog.getOpenFileName(self, "Foto auswählen", "", image_file_filter())
        if file_name:
            self._set_photo(Path(file_name))

    def _set_photo(self, path: Path) -> None:
        """Merkt sich das Foto, das beim Speichern angehängt wird."""
        self._photo_path = path
        self._photo_label.setText(path.name)
        self._scan_button.setEnabled(True)

    def _scan_photo(self) -> None:
        """Lässt den Zählerstand vom gewählten Foto ablesen und trägt ihn als Vorschlag ein."""
        if self._receipt_scan is None or self._photo_path is None:
            return
        suggestion = scan_meter_photo(self, self._receipt_scan, self._photo_path, self._type_combo.currentData())
        if suggestion is None:
            return
        self._value_spin.set_decimal(suggestion.value)
        text = "Zählerstand übernommen – bitte mit dem Foto vergleichen."
        if suggestion.meter_number:
            text += f" Zählernummer laut Foto: {suggestion.meter_number}."
            if not self._note_edit.text().strip():
                self._note_edit.setText(f"Zähler-Nr. {suggestion.meter_number}")
        self._scan_info.setText(text)
        self.form.setRowVisible(self._scan_info, True)

    def _on_duplicate(self) -> None:
        """Schließt den Dialog ohne zu speichern und meldet den Wunsch zu duplizieren."""
        if self._reading is not None:
            self.reject()
            self.duplicate_requested.emit(self._reading)

    def _apply(self) -> None:
        apartment_id = self._apartment_combo.currentData()
        if apartment_id is None:
            raise ValidationError("Bitte zuerst im Tab 'Immobilien' eine Wohnung anlegen.")

        values = {
            "apartment_id": int(apartment_id),
            "meter_type": self._type_combo.currentData(),
            "value": self._value_spin.decimal_value(),
            "date": self._date_edit.value(),
            "reader": self._reader_edit.text(),
            "note": self._note_edit.text(),
        }
        reading = replace(self._reading, **values) if self._reading else MeterReading(**values)

        if self._find_same_value is not None:
            matches = self._find_same_value(reading)
            if matches:
                text = same_value_text(reading, matches, self._lookups.apartment_label(reading.apartment_id))
                if not confirm(self, "Gleicher Zählerstand", text, confirm_label="Trotzdem speichern"):
                    raise ValidationError("Nicht gespeichert - bitte Zählerstand und Ablesedatum prüfen.")

        photo_name: str | None = None
        photo_data: bytes | None = None
        if self._photo_path is not None:
            photo_data = read_image_file(self._photo_path)
            photo_name = self._photo_path.name
        self._save(reading, photo_name, photo_data)
