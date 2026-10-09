"""Dialoge für die Fotodokumentation: Foto hinzufügen und Foto ansehen."""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from models.enums import PhotoCategory
from models.exceptions import ValidationError
from models.photo import Photo
from services.photo_service import SUPPORTED_EXTENSIONS, read_image_file
from ui.dialogs.base_dialog import FormDialog
from ui.dialogs.meter_reading_dialog import image_file_filter
from ui.helpers import ErrorGuard, FileDropFilter, fill_combo, make_button
from ui.lookups import Lookups
from utils.formatters import format_datetime, format_file_size

#: Funktion zum Speichern: (Wohnung, Kategorie, Beschreibung, Dateiname, Daten) -> Foto
AddPhoto = Callable[[int, PhotoCategory, str, str, bytes], Photo]


class PhotoDialog(FormDialog):
    """Neues Dokumentationsfoto (z. B. einen Mangel) erfassen.

    Das Foto lässt sich über "Foto wählen …" aussuchen oder direkt aus dem
    Explorer auf den Dialog ziehen.
    """

    def __init__(
        self,
        add_photo: AddPhoto,
        lookups: Lookups,
        parent: QWidget | None = None,
        photo_path: Path | None = None,
    ) -> None:
        """
        Args:
            add_photo: Funktion, die das Foto speichert (Service-Methode).
            lookups: Stammdaten für die Wohnungsauswahl.
            photo_path: Bereits gewähltes Foto (z. B. per Drag-and-drop abgelegt).
        """
        super().__init__("Foto hinzufügen", parent)
        self._add_photo = add_photo
        self._photo_path: Path | None = None

        self._apartment_combo = QComboBox()
        fill_combo(self._apartment_combo, lookups.apartment_choices())
        self._category_combo = QComboBox()
        fill_combo(
            self._category_combo,
            [(category.label, category) for category in (PhotoCategory.DEFECT, PhotoCategory.OTHER, PhotoCategory.METER)],
        )
        self._description_edit = QLineEdit()
        self._description_edit.setPlaceholderText("z. B. Wasserfleck Decke Bad")

        self._preview = QLabel("Kein Foto ausgewählt – Bild hierher ziehen oder „Foto wählen …“")
        self._preview.setObjectName("Muted")
        self._preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview.setMinimumHeight(170)
        choose_button = make_button("Foto wählen …", "camera")
        choose_button.clicked.connect(self._choose_photo)

        self.form.addRow("Wohnung", self._apartment_combo)
        self.form.addRow("Kategorie", self._category_combo)
        self.form.addRow("Beschreibung", self._description_edit)
        self.form.addRow("Foto", choose_button)
        self.form.addRow(self._preview)
        self._drop_filter = FileDropFilter(self, SUPPORTED_EXTENSIONS, lambda files: self._set_photo(files[0]))
        if photo_path is not None:
            self._set_photo(photo_path)

    def _choose_photo(self) -> None:
        """Öffnet den Dateidialog zur Auswahl eines Fotos."""
        file_name, _ = QFileDialog.getOpenFileName(self, "Foto auswählen", "", image_file_filter())
        if file_name:
            self._set_photo(Path(file_name))

    def _set_photo(self, path: Path) -> None:
        """Übernimmt das Foto und zeigt eine Vorschau."""
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.show_error("Die gewählte Datei ist kein lesbares Bild.")
            return
        self._photo_path = path
        self._preview.setPixmap(
            pixmap.scaled(400, 170, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        )

    def _apply(self) -> None:
        apartment_id = self._apartment_combo.currentData()
        if apartment_id is None:
            raise ValidationError("Bitte zuerst im Tab 'Immobilien' eine Wohnung anlegen.")
        if self._photo_path is None:
            raise ValidationError("Bitte ein Foto auswählen.")
        self._add_photo(
            int(apartment_id),
            self._category_combo.currentData(),
            self._description_edit.text(),
            self._photo_path.name,
            read_image_file(self._photo_path),
        )


class PhotoViewerDialog(QDialog):
    """Zeigt ein gespeichertes Foto an und erlaubt den Export als Datei."""

    def __init__(self, photo: Photo, parent: QWidget | None = None) -> None:
        """
        Args:
            photo: Vollständig geladenes Foto (inklusive Bilddaten).
        """
        super().__init__(parent)
        self._photo = photo
        self.setWindowTitle(photo.description or photo.file_name)
        self.resize(820, 640)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 14)

        image_label = QLabel()
        image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pixmap = QPixmap()
        if pixmap.loadFromData(photo.image_data):
            image_label.setPixmap(
                pixmap.scaled(780, 520, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            )
        else:
            image_label.setText("Das Bild kann nicht dargestellt werden.")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(image_label)
        layout.addWidget(scroll, 1)

        info = QLabel(
            f"{photo.file_name} · {format_file_size(photo.size_bytes)} · "
            f"gespeichert am {format_datetime(photo.uploaded_at)}"
        )
        info.setObjectName("Muted")
        export_button = make_button("Speichern unter …", "download")
        export_button.clicked.connect(self._export)
        close_button = make_button("Schließen")
        close_button.clicked.connect(self.accept)

        footer = QHBoxLayout()
        footer.addWidget(info, 1)
        footer.addWidget(export_button)
        footer.addWidget(close_button)
        layout.addLayout(footer)

    def _export(self) -> None:
        """Speichert die Bilddaten unverändert in eine vom Benutzer gewählte Datei."""
        file_name, _ = QFileDialog.getSaveFileName(self, "Foto speichern", self._photo.file_name)
        if not file_name:
            return
        with ErrorGuard(self):
            try:
                Path(file_name).write_bytes(self._photo.image_data)
            except OSError as exc:
                raise ValidationError(f"Das Foto kann nicht gespeichert werden: {exc}") from exc
