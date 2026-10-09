"""Tab 4 - Zählerstände: Ablesungen erfassen und Fotos dokumentieren."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path

from PyQt6.QtWidgets import QComboBox, QHBoxLayout, QTabWidget, QVBoxLayout, QWidget

from app_context import AppContext
from models.enums import MeterType
from models.meter_reading import MeterReading
from models.photo import Photo
from services.photo_service import SUPPORTED_EXTENSIONS
from ui.dialogs.meter_reading_dialog import MeterReadingDialog
from ui.dialogs.photo_dialog import PhotoDialog, PhotoViewerDialog
from ui.helpers import ErrorGuard, confirm, fill_combo, make_button
from ui.lookups import Lookups
from ui.widgets.common import BaseTab, Column, FileDropArea, ObjectTable, muted_label, page_title
from utils.formatters import format_date, format_datetime, format_decimal, format_file_size


class MeterReadingTab(BaseTab):
    """Zwei Unterseiten: Zählerstände und allgemeine Fotodokumentation."""

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._lookups: Lookups | None = None
        self._readings_with_photo: set[int] = set()
        self.root.addWidget(page_title("Zählerstände und Fotodokumentation"))

        self._pages = QTabWidget()
        self._pages.addTab(self._build_readings_page(), "Zählerstände")
        self._pages.addTab(self._build_photos_page(), "Fotodokumentation")
        self.root.addWidget(self._pages, 1)

    # ------------------------------------------------------------------ #
    # Aufbau: Zählerstände
    # ------------------------------------------------------------------ #
    def _build_readings_page(self) -> QWidget:
        # Ein Foto auf die Seite ziehen = neuer Zählerstand mit diesem Foto.
        page = FileDropArea(SUPPORTED_EXTENSIONS)
        page.files_dropped.connect(self._on_reading_photos_dropped)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(10)

        self._property_combo = QComboBox()
        self._property_combo.setMinimumWidth(220)
        self._apartment_combo = QComboBox()
        self._apartment_combo.setMinimumWidth(260)
        self._type_combo = QComboBox()
        fill_combo(self._type_combo, [("Alle Zählerarten", None), *[(m.label, m) for m in MeterType]])

        self._add_button = make_button("Neu", "plus", "primary")
        self._edit_button = make_button("Bearbeiten", "edit")
        self._duplicate_button = make_button("Duplizieren", "copy")
        self._photo_button = make_button("Foto anzeigen", "eye")
        self._delete_button = make_button("Löschen", "trash", "danger")

        toolbar = QHBoxLayout()
        for caption, combo in (("Immobilie", self._property_combo), ("Wohnung", self._apartment_combo), ("Zähler", self._type_combo)):
            toolbar.addWidget(muted_label(caption))
            toolbar.addWidget(combo)
        toolbar.addStretch(1)
        for button in (self._add_button, self._edit_button, self._duplicate_button, self._photo_button, self._delete_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self._readings_table: ObjectTable[MeterReading] = ObjectTable(
            [
                Column("Datum", lambda r: format_date(r.date), lambda r: r.date.isoformat()),
                Column("Wohnung", lambda r: self._lookups.apartment_label(r.apartment_id) if self._lookups else ""),
                Column("Zählerart", lambda r: r.meter_type.label),
                Column("Zählerstand", lambda r: format_decimal(r.value, 3), lambda r: float(r.value), True),
                Column("Einheit", lambda r: r.meter_type.unit),
                Column("Ableser", lambda r: r.reader),
                Column("Foto", lambda r: "✓" if r.id in self._readings_with_photo else ""),
                Column("Bemerkung", lambda r: r.note),
            ]
        )
        layout.addWidget(self._readings_table, 1)

        self._add_button.clicked.connect(lambda: self._add_reading())
        self._edit_button.clicked.connect(self._edit_reading)
        self._duplicate_button.clicked.connect(self._duplicate_reading)
        self._photo_button.clicked.connect(self._show_reading_photo)
        self._delete_button.clicked.connect(self._delete_reading)
        self._readings_table.item_activated.connect(lambda *_: self._edit_reading())
        self._readings_table.selection_changed.connect(self._update_buttons)
        self._property_combo.currentIndexChanged.connect(lambda *_: self._on_property_filter_changed())
        self._apartment_combo.currentIndexChanged.connect(lambda *_: self._load_readings())
        self._type_combo.currentIndexChanged.connect(lambda *_: self._load_readings())
        return page

    # ------------------------------------------------------------------ #
    # Aufbau: Fotodokumentation
    # ------------------------------------------------------------------ #
    def _build_photos_page(self) -> QWidget:
        page = FileDropArea(SUPPORTED_EXTENSIONS)
        page.files_dropped.connect(self._on_photos_dropped)
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 14, 0, 0)
        layout.setSpacing(10)

        self._add_photo_button = make_button("Foto hinzufügen", "camera", "primary")
        self._view_photo_button = make_button("Anzeigen", "eye")
        self._delete_photo_button = make_button("Löschen", "trash", "danger")
        toolbar = QHBoxLayout()
        toolbar.addWidget(
            muted_label(
                "Fotos zu Mängeln und sonstigen Vorgängen – auch per Drag-and-drop aus dem Explorer. "
                "Fotos von Zählern hängen direkt am jeweiligen Zählerstand."
            )
        )
        toolbar.addStretch(1)
        for button in (self._add_photo_button, self._view_photo_button, self._delete_photo_button):
            toolbar.addWidget(button)
        layout.addLayout(toolbar)

        self._photos_table: ObjectTable[Photo] = ObjectTable(
            [
                Column("Gespeichert am", lambda p: format_datetime(p.uploaded_at), lambda p: p.uploaded_at.isoformat()),
                Column("Kategorie", lambda p: p.category.label),
                Column("Wohnung", lambda p: self._lookups.apartment_label(p.apartment_id) if self._lookups else ""),
                Column("Beschreibung", lambda p: p.description),
                Column("Datei", lambda p: p.file_name),
                Column("Größe", lambda p: format_file_size(p.size_bytes), lambda p: p.size_bytes, True),
            ],
            stretch_column=3,
        )
        layout.addWidget(self._photos_table, 1)

        self._add_photo_button.clicked.connect(lambda: self._add_photo())
        self._view_photo_button.clicked.connect(self._view_photo)
        self._delete_photo_button.clicked.connect(self._delete_photo)
        self._photos_table.item_activated.connect(lambda *_: self._view_photo())
        self._photos_table.selection_changed.connect(self._update_buttons)
        return page

    # ------------------------------------------------------------------ #
    # Daten laden
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Lädt Stammdaten, Zählerstände und Fotos neu."""
        with ErrorGuard(self):
            self._lookups = Lookups(self._context)
            fill_combo(self._property_combo, [("Alle Immobilien", None), *self._lookups.property_choices()])
            self._fill_apartment_filter()
            self._photos_table.set_items(self._context.photos.list_documentation())
        self._load_readings()

    def _fill_apartment_filter(self) -> None:
        """Bietet im Wohnungsfilter nur Wohnungen der gewählten Immobilie an."""
        if self._lookups is None:
            return
        choices = self._lookups.apartment_choices(self._property_combo.currentData())
        fill_combo(self._apartment_combo, [("Alle Wohnungen", None), *choices])

    def _on_property_filter_changed(self) -> None:
        self._fill_apartment_filter()
        self._load_readings()

    def _load_readings(self) -> None:
        """Lädt die Zählerstände passend zu den Filtern."""
        with ErrorGuard(self):
            self._readings_with_photo = self._context.meter_readings.reading_ids_with_photos()
            self._readings_table.set_items(
                self._context.meter_readings.list_readings(
                    property_id=self._property_combo.currentData(),
                    apartment_id=self._apartment_combo.currentData(),
                    meter_type=self._type_combo.currentData(),
                )
            )
        self._update_buttons()

    def _update_buttons(self) -> None:
        reading = self._readings_table.selected_item()
        self._edit_button.setEnabled(reading is not None)
        self._duplicate_button.setEnabled(reading is not None)
        self._delete_button.setEnabled(reading is not None)
        self._photo_button.setEnabled(reading is not None and reading.id in self._readings_with_photo)
        has_photo = self._photos_table.selected_item() is not None
        self._view_photo_button.setEnabled(has_photo)
        self._delete_photo_button.setEnabled(has_photo)

    # ------------------------------------------------------------------ #
    # Aktionen: Zählerstände
    # ------------------------------------------------------------------ #
    def _add_reading(self, photo_path: Path | None = None) -> None:
        """Legt einen Zählerstand an, optional mit einem bereits gewählten Foto."""
        if self._lookups is None:
            return
        dialog = MeterReadingDialog(
            self._context.meter_readings.save_reading,
            self._lookups,
            preselected_apartment_id=self._apartment_combo.currentData(),
            parent=self,
            photo_path=photo_path,
            receipt_scan=self._context.receipt_scan,
        )
        if dialog.exec():
            self._load_readings()

    def _on_reading_photos_dropped(self, files: list[Path]) -> None:
        """Je abgelegtem Zählerfoto einen neuen Zählerstand erfassen."""
        for path in files:
            self._add_reading(path)

    def _edit_reading(self) -> None:
        reading = self._readings_table.selected_item()
        if reading is None or self._lookups is None:
            return
        dialog = MeterReadingDialog(
            self._context.meter_readings.save_reading,
            self._lookups,
            reading,
            parent=self,
            receipt_scan=self._context.receipt_scan,
        )
        dialog.duplicate_requested.connect(self._on_duplicate_reading_requested)
        if dialog.exec():
            self._load_readings()

    def _on_duplicate_reading_requested(self, reading: MeterReading) -> None:
        """Öffnet den Dialog für einen *neuen* Zählerstand mit den Werten von ``reading``.

        Die Kopie hat keine ID (sonst würde das Original überschrieben) und das
        heutige Datum. Gespeichert wird erst mit "Speichern"; ein Foto des
        Originals wird nicht mitkopiert.
        """
        if self._lookups is None:
            return
        duplicated = replace(reading, id=None, date=date.today())
        dialog = MeterReadingDialog(
            self._context.meter_readings.save_reading,
            self._lookups,
            duplicated,
            parent=self,
            receipt_scan=self._context.receipt_scan,
        )
        if dialog.exec():
            self._load_readings()

    def _duplicate_reading(self) -> None:
        """Dupliziert den markierten Zählerstand."""
        reading = self._readings_table.selected_item()
        if reading is not None:
            self._on_duplicate_reading_requested(reading)

    def _delete_reading(self) -> None:
        reading = self._readings_table.selected_item()
        if reading is None or reading.id is None:
            return
        text = f"Zählerstand vom {format_date(reading.date)} ({reading.meter_type.label}) wirklich löschen?"
        if reading.id in self._readings_with_photo:
            text += "\n\nDas zugehörige Foto wird ebenfalls gelöscht."
        if not confirm(self, "Zählerstand löschen", text):
            return
        with ErrorGuard(self):
            self._context.meter_readings.delete_reading(reading.id)
        self._load_readings()

    def _show_reading_photo(self) -> None:
        """Zeigt das neueste Foto des markierten Zählerstands."""
        reading = self._readings_table.selected_item()
        if reading is None or reading.id is None:
            return
        with ErrorGuard(self):
            photos = self._context.meter_readings.photos_of_reading(reading.id)
            if photos and photos[0].id is not None:
                PhotoViewerDialog(self._context.photos.load_photo(photos[0].id), self).exec()

    # ------------------------------------------------------------------ #
    # Aktionen: Fotodokumentation
    # ------------------------------------------------------------------ #
    def _add_photo(self, photo_path: Path | None = None) -> None:
        """Erfasst ein Dokumentationsfoto, optional mit einer bereits gewählten Datei."""
        if self._lookups is None:
            return
        if PhotoDialog(self._context.photos.add_photo, self._lookups, self, photo_path).exec():
            self.refresh()

    def _on_photos_dropped(self, files: list[Path]) -> None:
        """Je abgelegtem Foto den Dialog öffnen (Wohnung, Kategorie, Beschreibung)."""
        for path in files:
            self._add_photo(path)

    def _view_photo(self) -> None:
        photo = self._photos_table.selected_item()
        if photo is None or photo.id is None:
            return
        with ErrorGuard(self):
            PhotoViewerDialog(self._context.photos.load_photo(photo.id), self).exec()

    def _delete_photo(self) -> None:
        photo = self._photos_table.selected_item()
        if photo is None or photo.id is None:
            return
        if not confirm(self, "Foto löschen", f"Foto '{photo.description or photo.file_name}' wirklich löschen?"):
            return
        with ErrorGuard(self):
            self._context.photos.delete_photo(photo.id)
        self.refresh()
