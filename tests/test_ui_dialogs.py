"""Tests der Oberfläche: Tabs und Dialoge lassen sich aufbauen, Duplizieren und Drag-and-drop funktionieren.

Die Tests laufen ohne sichtbares Fenster (Qt-Plattform "offscreen"). Sie
fangen Fehler ab, die reine Service-Tests nicht sehen - etwa ein fehlendes
Icon oder einen Dialog, der beim Öffnen abstürzt.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QMimeData, QTimer, QUrl  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication, QDialog  # noqa: E402

from app_context import AppContext  # noqa: E402
from models.enums import PhotoCategory  # noqa: E402
from services.photo_service import SUPPORTED_EXTENSIONS  # noqa: E402
from tests.qt_helpers import drop_files  # noqa: E402
from tools.demo_data import seed_demo_data  # noqa: E402
from ui.dialogs.cost_dialog import CostDialog  # noqa: E402
from ui.dialogs.meter_reading_dialog import MeterReadingDialog  # noqa: E402
from ui.dialogs.photo_dialog import PhotoDialog  # noqa: E402
from ui.helpers import dropped_files  # noqa: E402
from ui.lookups import Lookups  # noqa: E402
from ui.main_window import _TAB_DEFINITIONS  # noqa: E402
from ui.widgets.common import FileDropArea  # noqa: E402
from ui.widgets.invoices_tab import InvoicesTab  # noqa: E402
from ui.widgets.meter_reading_tab import MeterReadingTab  # noqa: E402

YEAR = 2025


@pytest.fixture
def demo(qt_app: QApplication, context: AppContext) -> AppContext:
    seed_demo_data(context, billing_year=YEAR)
    return context


@pytest.fixture
def image_file(qt_app: QApplication, tmp_path: Path) -> Path:
    """Ein kleines, echtes PNG auf der Festplatte."""
    path = tmp_path / "zaehler.png"
    image = QImage(40, 30, QImage.Format.Format_RGB32)
    image.fill(QColor("#336699"))
    assert image.save(str(path), "PNG")
    return path


@pytest.fixture
def answer_dialog() -> Iterator[Callable[[], None]]:
    """Bestätigt den nächsten modalen Dialog automatisch mit "Speichern".

    Bleibt der Dialog danach offen (Eingabe abgelehnt), wird er abgebrochen,
    damit der Test nicht hängen bleibt.
    """
    timers: list[QTimer] = []

    def arm() -> None:
        # Der Dialog erscheint unter Umständen zeitversetzt (nach einem Drop):
        # so lange nachsehen, bis einer da ist - und dann genau einmal antworten.
        timer = QTimer()
        timer.setInterval(10)

        def press_save() -> None:
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, QDialog):
                timer.stop()
                dialog.accept()
                if dialog.isVisible():
                    dialog.reject()

        timer.timeout.connect(press_save)
        timer.start()
        timers.append(timer)

    yield arm
    for timer in timers:
        timer.stop()


class TestTabsAndDialogsOpen:
    def test_all_tabs_can_be_built_and_refreshed(self, demo: AppContext) -> None:
        for _title, _icon, tab_class in _TAB_DEFINITIONS:
            tab = tab_class(demo)
            tab.refresh()
            tab.deleteLater()

    def test_edit_dialogs_open_for_existing_entries(self, demo: AppContext) -> None:
        reading = demo.meter_readings.list_readings()[0]
        MeterReadingDialog(demo.meter_readings.save_reading, Lookups(demo), reading).deleteLater()

        house = demo.properties.list_properties()[0]
        assert house.id is not None
        invoice = demo.costs.list_costs(house.id)[0]
        tab = InvoicesTab(demo)
        dialog = CostDialog(
            demo.costs.save_cost, demo.cost_types.list_types(), tab._tenant_choices(house.id), house.id, invoice.period, invoice
        )
        assert dialog.windowTitle() == "Rechnung bearbeiten"
        dialog.deleteLater()


class TestDuplicate:
    def test_duplicate_reading_creates_new_entry(self, demo: AppContext, answer_dialog: Callable[[], None]) -> None:
        tab = MeterReadingTab(demo)
        tab.refresh()
        tab._readings_table.select_first()
        original = tab._readings_table.selected_item()
        assert original is not None and original.id is not None
        before = demo.meter_readings.list_readings()

        answer_dialog()
        tab._duplicate_reading()

        after = demo.meter_readings.list_readings()
        assert len(after) == len(before) + 1
        # Das Original bleibt unverändert, die Kopie ist ein eigener Eintrag.
        unchanged = next(r for r in after if r.id == original.id)
        assert unchanged.date == original.date and unchanged.value == original.value
        copy = next(r for r in after if r.id not in {r.id for r in before})
        assert (copy.apartment_id, copy.meter_type, copy.value) == (
            original.apartment_id, original.meter_type, original.value,
        )

    def test_duplicate_invoice_creates_new_entry(self, demo: AppContext, answer_dialog: Callable[[], None]) -> None:
        tab = InvoicesTab(demo)
        tab.refresh()
        tab._invoice_table.select_first()
        original = tab._invoice_table.selected_item()
        assert original is not None and original.id is not None
        before = demo.costs.list_costs(original.property_id)

        answer_dialog()
        tab._duplicate_invoice()

        after = demo.costs.list_costs(original.property_id)
        assert len(after) == len(before) + 1
        unchanged = next(c for c in after if c.id == original.id)
        assert unchanged.invoice_date == original.invoice_date and unchanged.amount == original.amount
        copy = next(c for c in after if c.id not in {c.id for c in before})
        assert (copy.cost_type.id, copy.amount, copy.period) == (original.cost_type.id, original.amount, original.period)

    def test_duplicate_button_in_edit_dialog_closes_without_saving(self, demo: AppContext) -> None:
        reading = demo.meter_readings.list_readings()[0]
        count = len(demo.meter_readings.list_readings())
        dialog = MeterReadingDialog(demo.meter_readings.save_reading, Lookups(demo), reading)
        requested: list[object] = []
        dialog.duplicate_requested.connect(requested.append)

        dialog._on_duplicate()

        assert requested == [reading]
        assert dialog.result() == QDialog.DialogCode.Rejected
        assert len(demo.meter_readings.list_readings()) == count

    def test_template_without_id_is_treated_as_new(self, demo: AppContext) -> None:
        from dataclasses import replace

        reading = demo.meter_readings.list_readings()[0]
        dialog = MeterReadingDialog(demo.meter_readings.save_reading, Lookups(demo), replace(reading, id=None))
        assert dialog.windowTitle() == "Neuer Zählerstand"


class TestDragAndDrop:
    def test_dropped_files_keeps_only_local_images(self, image_file: Path, tmp_path: Path) -> None:
        text_file = tmp_path / "notiz.txt"
        text_file.write_text("kein Bild", encoding="utf-8")
        mime = QMimeData()
        mime.setUrls([QUrl.fromLocalFile(str(image_file)), QUrl.fromLocalFile(str(text_file)), QUrl("https://example.org/a.png")])

        assert dropped_files(mime, SUPPORTED_EXTENSIONS) == [image_file]
        assert dropped_files(QMimeData(), SUPPORTED_EXTENSIONS) == []

    def test_drop_area_reports_files(self, qt_app: QApplication, image_file: Path) -> None:
        area = FileDropArea(SUPPORTED_EXTENSIONS)
        received: list[list[Path]] = []
        area.files_dropped.connect(received.append)

        assert drop_files(area, [image_file])

        assert received == [[image_file]]

    def test_photo_dialog_accepts_dropped_image(self, demo: AppContext, image_file: Path) -> None:
        before = len(demo.photos.list_documentation())
        dialog = PhotoDialog(demo.photos.add_photo, Lookups(demo))

        # Auf das Beschreibungsfeld gezogen - nicht auf eine freie Stelle des Dialogs.
        assert drop_files(dialog._description_edit, [image_file])
        assert dialog._description_edit.text() == ""
        dialog._category_combo.setCurrentIndex(dialog._category_combo.findData(PhotoCategory.DEFECT))
        dialog.accept()

        photos = demo.photos.list_documentation()
        assert len(photos) == before + 1
        assert any(photo.file_name == image_file.name for photo in photos)

    def test_reading_dialog_saves_dropped_photo(self, demo: AppContext, image_file: Path) -> None:
        reading = demo.meter_readings.list_readings()[0]
        assert reading.id is not None
        dialog = MeterReadingDialog(demo.meter_readings.save_reading, Lookups(demo), reading)

        # Auf das Zahlenfeld gezogen: Auch dort muss das Foto ankommen.
        assert drop_files(dialog._value_spin.lineEdit(), [image_file])
        dialog.accept()

        assert [p.file_name for p in demo.meter_readings.photos_of_reading(reading.id)] == [image_file.name]

    def test_dropping_on_photo_page_opens_dialog_with_file(
        self, demo: AppContext, image_file: Path, answer_dialog: Callable[[], None]
    ) -> None:
        tab = MeterReadingTab(demo)
        tab.refresh()
        before = len(demo.photos.list_documentation())

        answer_dialog()
        tab._on_photos_dropped([image_file])

        assert len(demo.photos.list_documentation()) == before + 1

    def test_dropping_on_readings_table_opens_new_reading_with_photo(
        self, demo: AppContext, image_file: Path, answer_dialog: Callable[[], None]
    ) -> None:
        tab = MeterReadingTab(demo)
        tab.refresh()
        before = len(demo.meter_readings.list_readings())

        answer_dialog()
        # Die Tabelle liegt über der Ablagefläche - der Drop muss trotzdem ankommen.
        assert drop_files(tab._readings_table.viewport(), [image_file])

        readings = demo.meter_readings.list_readings()
        assert len(readings) == before + 1
        newest = max(readings, key=lambda r: r.id or 0)
        assert newest.id is not None
        assert [p.file_name for p in demo.meter_readings.photos_of_reading(newest.id)] == [image_file.name]

    def test_other_files_are_left_to_the_widget(self, demo: AppContext, tmp_path: Path) -> None:
        text_file = tmp_path / "notiz.txt"
        text_file.write_text("kein Bild", encoding="utf-8")
        dialog = PhotoDialog(demo.photos.add_photo, Lookups(demo))

        drop_files(dialog, [text_file])

        assert dialog._photo_path is None
