"""Tests für das Ablesen von Zählerständen aus Fotos (optionale Erkennung, hier mit Attrappe)."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from decimal import Decimal
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app_context import AppContext  # noqa: E402
from models.enums import MeterType  # noqa: E402
from models.exceptions import ScanError  # noqa: E402
from models.receipt import MeterSuggestion  # noqa: E402
from services.receipt_scan_service import ReceiptScanService, ScannerStatus, parse_meter_suggestion  # noqa: E402
from tools.demo_data import seed_demo_data  # noqa: E402
from ui.dialogs.meter_reading_dialog import MeterReadingDialog  # noqa: E402
from ui.lookups import Lookups  # noqa: E402
from utils.config_manager import ConfigManager  # noqa: E402


class _FakeScanner:
    """Attrappe des KI-Modells mit fester Antwort."""

    def __init__(self, answer: dict[str, Any]) -> None:
        self._answer = answer
        self.instructions = ""
        self.pages: list[bytes] = []

    def check(self) -> ScannerStatus:
        return ScannerStatus(True, "Bereit")

    def extract(self, images: Sequence[bytes], instructions: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        self.pages = list(images)
        self.instructions = instructions
        return self._answer


def _service(tmp_path: Path, scanner: _FakeScanner) -> ReceiptScanService:
    service = ReceiptScanService(ConfigManager(tmp_path / "scan.json"), lambda _settings: scanner)
    service.status()  # wie in der Anwendung: Die Verfügbarkeit ist bekannt, bevor ein Dialog aufgeht.
    return service


@pytest.fixture
def photo(qt_app: QApplication, tmp_path: Path) -> Path:
    path = tmp_path / "wasserzaehler.png"
    image = QImage(80, 60, QImage.Format.Format_RGB32)
    image.fill(QColor("#39424E"))
    assert image.save(str(path), "PNG")
    return path


class TestParsing:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("1234,567", "1234.567"),
            ("01.234,5 m³", "1234.500"),
            ("00412", "412.000"),
            ("17 980", "17980.000"),
            (498.1, "498.100"),
            (17980, "17980.000"),
            ("1234.5", "1234.500"),
        ],
    )
    def test_readable_values(self, raw: object, expected: str) -> None:
        suggestion = parse_meter_suggestion({"zaehlerstand": raw, "zaehlernummer": " 48 221 907 "})
        assert suggestion == MeterSuggestion(Decimal(expected), "48 221 907")

    @pytest.mark.parametrize(
        "raw", ["", None, "unleserlich", "1,2,3", True, -5, float("nan"), "1" + "0" * 31, "m³"]
    )
    def test_unreadable_values_are_discarded(self, raw: object) -> None:
        assert parse_meter_suggestion({"zaehlerstand": raw, "zaehlernummer": "123"}) is None


class TestService:
    def test_scan_meter_tells_the_model_what_it_looks_at(self, tmp_path: Path) -> None:
        scanner = _FakeScanner({"zaehlerstand": "498,1", "zaehlernummer": "K-4711"})

        suggestion = _service(tmp_path, scanner).scan_meter([b"bild"], MeterType.COLD_WATER)

        assert suggestion == MeterSuggestion(Decimal("498.100"), "K-4711")
        assert MeterType.COLD_WATER.label in scanner.instructions
        assert MeterType.COLD_WATER.unit in scanner.instructions

    def test_no_reading_raises(self, tmp_path: Path) -> None:
        scanner = _FakeScanner({"zaehlerstand": "", "zaehlernummer": "K-4711"})
        with pytest.raises(ScanError, match="kein Zählerstand"):
            _service(tmp_path, scanner).scan_meter([b"bild"], MeterType.HEATING)
        with pytest.raises(ScanError):
            _service(tmp_path, scanner).scan_meter([], MeterType.HEATING)


class TestDialog:
    def test_value_and_meter_number_are_suggested(
        self, qt_app: QApplication, context: AppContext, photo: Path, tmp_path: Path
    ) -> None:
        seed_demo_data(context, billing_year=2025)
        scanner = _FakeScanner({"zaehlerstand": "18.042,750", "zaehlernummer": "48221907"})
        dialog = MeterReadingDialog(
            context.meter_readings.save_reading,
            Lookups(context),
            photo_path=photo,
            receipt_scan=_service(tmp_path, scanner),
        )
        assert dialog._scan_button.isVisibleTo(dialog) and dialog._scan_button.isEnabled()

        dialog._scan_photo()

        assert scanner.pages, "Das Foto wurde nicht an die Erkennung übergeben."
        assert dialog._value_spin.decimal_value() == Decimal("18042.750")
        assert dialog._note_edit.text() == "Zähler-Nr. 48221907"
        assert "bitte mit dem Foto vergleichen" in dialog._scan_info.text()
        # Gespeichert wird erst mit "Speichern" - und dann samt Foto.
        before = len(context.meter_readings.list_readings())
        dialog.accept()
        readings = context.meter_readings.list_readings()
        assert len(readings) == before + 1
        newest = max(readings, key=lambda r: r.id or 0)
        assert newest.value == Decimal("18042.750") and newest.id is not None
        assert [p.file_name for p in context.meter_readings.photos_of_reading(newest.id)] == [photo.name]

    def test_existing_note_is_kept(
        self, qt_app: QApplication, context: AppContext, photo: Path, tmp_path: Path
    ) -> None:
        seed_demo_data(context, billing_year=2025)
        scanner = _FakeScanner({"zaehlerstand": "100", "zaehlernummer": "48221907"})
        dialog = MeterReadingDialog(
            context.meter_readings.save_reading, Lookups(context), photo_path=photo, receipt_scan=_service(tmp_path, scanner)
        )
        dialog._note_edit.setText("Zwischenablesung")

        dialog._scan_photo()

        assert dialog._note_edit.text() == "Zwischenablesung"

    def test_button_needs_scanner_and_photo(self, qt_app: QApplication, context: AppContext, tmp_path: Path) -> None:
        seed_demo_data(context, billing_year=2025)
        without_scanner = MeterReadingDialog(context.meter_readings.save_reading, Lookups(context))
        assert not without_scanner._scan_button.isVisibleTo(without_scanner)

        scanner = _FakeScanner({"zaehlerstand": "1", "zaehlernummer": ""})
        without_photo = MeterReadingDialog(
            context.meter_readings.save_reading, Lookups(context), receipt_scan=_service(tmp_path, scanner)
        )
        assert without_photo._scan_button.isVisibleTo(without_photo) and not without_photo._scan_button.isEnabled()
