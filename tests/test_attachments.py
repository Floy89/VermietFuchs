"""Tests für Belege (PDF/Foto) an Rechnungen: Service, Dialog, Drag-and-drop, Belegerkennung."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from datetime import date
from pathlib import Path
from typing import Any

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtCore import QTimer  # noqa: E402
from PyQt6.QtGui import QColor, QImage  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

import config  # noqa: E402
from app_context import AppContext  # noqa: E402
from models.cost import Cost  # noqa: E402
from models.enums import AllocationKey  # noqa: E402
from models.exceptions import NotFoundError, ValidationError  # noqa: E402
from models.property import Property  # noqa: E402
from models.value_objects import DateRange, Money  # noqa: E402
from services.attachment_service import read_attachment_file  # noqa: E402
from services.receipt_scan_service import ReceiptScanService, ScannerStatus  # noqa: E402
from tests.qt_helpers import drop_files  # noqa: E402
from ui.dialogs.cost_dialog import CostDialog  # noqa: E402
from ui.widgets.invoices_tab import InvoicesTab  # noqa: E402
from utils.config_manager import ConfigManager  # noqa: E402

YEAR = DateRange.for_year(2025)
PDF_BYTES = b"%PDF-1.4\n% Testbeleg\n%%EOF\n"


@pytest.fixture
def invoice(context: AppContext) -> Cost:
    """Eine gespeicherte Wasserrechnung in einem Testhaus."""
    house = context.properties.save_property(Property(name="Beleghaus", address="Aktenweg 1, 12345 Ordnerstadt"))
    assert house.id is not None
    return context.costs.save_cost(
        Cost(
            house.id,
            context.cost_types.get_by_name("Grundsteuer"),
            Money.from_decimal("742.80"),
            YEAR,
            AllocationKey.AREA,
            description="Stadtkasse",
        )
    )


@pytest.fixture
def pdf_file(tmp_path: Path) -> Path:
    path = tmp_path / "rechnung.pdf"
    path.write_bytes(PDF_BYTES)
    return path


@pytest.fixture
def image_file(qt_app: QApplication, tmp_path: Path) -> Path:
    path = tmp_path / "rechnung_foto.png"
    image = QImage(60, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("white"))
    assert image.save(str(path), "PNG")
    return path


def _dialog(context: AppContext, property_id: int, cost: Cost | None = None, **extra: Any) -> CostDialog:
    return CostDialog(
        context.costs.save_cost,
        context.cost_types.list_types(),
        [],
        property_id,
        YEAR,
        cost,
        attachments=context.attachments,
        **extra,
    )


class TestService:
    def test_add_list_load_delete(self, context: AppContext, invoice: Cost) -> None:
        assert invoice.id is not None
        saved = context.attachments.add(invoice.id, "rechnung.pdf", PDF_BYTES)
        context.attachments.add(invoice.id, "anschreiben.JPG", b"\xff\xd8\xff bild")
        assert saved.id is not None

        listed = context.attachments.list_for_cost(invoice.id)
        assert [a.file_name for a in listed] == ["rechnung.pdf", "anschreiben.JPG"]
        # Die Liste enthält nur Metadaten - die Daten kommen erst beim Laden.
        assert listed[0].file_data == b"" and listed[0].size_bytes == len(PDF_BYTES)
        assert listed[0].is_pdf and not listed[1].is_pdf
        assert context.attachments.load(saved.id).file_data == PDF_BYTES
        assert context.attachments.counts() == {invoice.id: 2}

        context.attachments.delete(saved.id)
        assert context.attachments.counts() == {invoice.id: 1}

    def test_attachments_are_deleted_with_the_invoice(self, context: AppContext, invoice: Cost) -> None:
        assert invoice.id is not None
        saved = context.attachments.add(invoice.id, "rechnung.pdf", PDF_BYTES)
        assert saved.id is not None

        context.costs.delete_cost(invoice.id)

        assert context.attachments.counts() == {}
        with pytest.raises(NotFoundError):
            context.attachments.load(saved.id)

    def test_invalid_files_are_rejected(
        self, context: AppContext, invoice: Cost, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        assert invoice.id is not None
        with pytest.raises(ValidationError, match="Nicht unterstütztes Format"):
            context.attachments.add(invoice.id, "tabelle.xlsx", b"daten")
        with pytest.raises(ValidationError, match="leer"):
            context.attachments.add(invoice.id, "leer.pdf", b"")
        monkeypatch.setattr(config, "MAX_ATTACHMENT_BYTES", 10)
        with pytest.raises(ValidationError, match="größer als"):
            context.attachments.add(invoice.id, "gross.pdf", PDF_BYTES)
        assert context.attachments.counts() == {}

    def test_read_attachment_file(self, pdf_file: Path, tmp_path: Path) -> None:
        assert read_attachment_file(pdf_file) == PDF_BYTES
        with pytest.raises(ValidationError):
            read_attachment_file(tmp_path / "fehlt.pdf")

    def test_audit_trail_records_attachment_without_file_content(self, context: AppContext, invoice: Cost) -> None:
        assert invoice.id is not None
        context.attachments.add(invoice.id, "rechnung.pdf", PDF_BYTES)

        entry = context.audit.list_entries(table_name="cost_attachments")[0]

        assert "rechnung.pdf" in (entry.new_value or "")
        assert f"<{len(PDF_BYTES)} Bytes>" in (entry.new_value or "")


class TestDialog:
    def test_new_invoice_is_saved_together_with_its_receipts(
        self, qt_app: QApplication, context: AppContext, invoice: Cost, pdf_file: Path, image_file: Path
    ) -> None:
        dialog = _dialog(context, invoice.property_id, attachment_paths=[pdf_file])
        assert drop_files(dialog, [image_file])
        dialog._amount_spin.set_money(Money.from_decimal("120.00"))
        # Vor dem Speichern ist noch nichts in der Datenbank.
        assert context.attachments.counts() == {}

        dialog.accept()

        new_invoice = next(c for c in context.costs.list_costs(invoice.property_id) if c.id != invoice.id)
        assert new_invoice.id is not None
        assert [a.file_name for a in context.attachments.list_for_cost(new_invoice.id)] == [
            pdf_file.name,
            image_file.name,
        ]

    def test_existing_invoice_remove_and_add(
        self, qt_app: QApplication, context: AppContext, invoice: Cost, pdf_file: Path, image_file: Path
    ) -> None:
        assert invoice.id is not None
        context.attachments.add(invoice.id, "alt.pdf", PDF_BYTES)
        dialog = _dialog(context, invoice.property_id, invoice)
        editor = dialog._attachments_editor
        assert editor is not None and editor.file_names() == ["alt.pdf"]

        editor._list.setCurrentRow(0)
        editor._remove_selected()
        editor.add_file(image_file)
        dialog.accept()

        assert [a.file_name for a in context.attachments.list_for_cost(invoice.id)] == [image_file.name]

    def test_cancel_changes_nothing(
        self, qt_app: QApplication, context: AppContext, invoice: Cost, image_file: Path
    ) -> None:
        assert invoice.id is not None
        context.attachments.add(invoice.id, "alt.pdf", PDF_BYTES)
        dialog = _dialog(context, invoice.property_id, invoice)
        editor = dialog._attachments_editor
        assert editor is not None
        editor._list.setCurrentRow(0)
        editor._remove_selected()
        editor.add_file(image_file)

        dialog.reject()

        assert [a.file_name for a in context.attachments.list_for_cost(invoice.id)] == ["alt.pdf"]

    def test_unsupported_file_is_not_accepted_as_drop(
        self, qt_app: QApplication, context: AppContext, invoice: Cost, tmp_path: Path
    ) -> None:
        text_file = tmp_path / "notiz.txt"
        text_file.write_text("kein Beleg", encoding="utf-8")
        dialog = _dialog(context, invoice.property_id, invoice)

        drop_files(dialog, [text_file])

        assert dialog._attachments_editor is not None and dialog._attachments_editor.file_names() == []

    def test_invoices_tab_shows_number_of_receipts(
        self, qt_app: QApplication, context: AppContext, invoice: Cost
    ) -> None:
        assert invoice.id is not None
        context.attachments.add(invoice.id, "rechnung.pdf", PDF_BYTES)
        tab = InvoicesTab(context)
        tab.refresh()

        assert tab._attachment_counts == {invoice.id: 1}


class TestRealisticDropTargets:
    """Ein echter Drop landet auf dem Widget unter dem Mauszeiger - meist einem Eingabefeld."""

    def test_drop_on_input_fields_attaches_the_file(
        self, qt_app: QApplication, context: AppContext, invoice: Cost, pdf_file: Path
    ) -> None:
        dialog = _dialog(context, invoice.property_id, invoice)
        editor = dialog._attachments_editor
        assert editor is not None

        # Textzeile, Betragsfeld, Datumsfeld, Auswahlfeld, Belegliste und der Hinweis darunter.
        targets = [
            dialog._description_edit,
            dialog._amount_spin.lineEdit(),
            dialog._from_edit.lineEdit(),
            dialog._type_combo,
            editor._list.viewport(),
            editor._hint,
        ]
        for target in targets:
            assert drop_files(target, [pdf_file]), f"Drop auf {type(target).__name__} wurde nicht angenommen"

        assert editor.file_names() == [pdf_file.name] * len(targets)
        # Der Dateipfad darf nicht als Text im Feld gelandet sein.
        assert dialog._description_edit.text() == "Stadtkasse"
        assert dialog._amount_spin.money() == Money.from_decimal("742.80")

    def test_drop_on_invoice_table_opens_new_invoice_with_receipts(
        self, qt_app: QApplication, context: AppContext, invoice: Cost, pdf_file: Path, image_file: Path
    ) -> None:
        tab = InvoicesTab(context)
        tab.refresh()
        seen: list[list[str]] = []

        timer = QTimer()
        timer.setInterval(10)

        def inspect_and_close() -> None:
            # Der Dialog öffnet sich erst nach dem Drop - so lange nachsehen, bis er da ist.
            dialog = QApplication.activeModalWidget()
            if isinstance(dialog, CostDialog) and dialog._attachments_editor is not None:
                timer.stop()
                seen.append(dialog._attachments_editor.file_names())
                dialog.reject()

        timer.timeout.connect(inspect_and_close)
        timer.start()

        assert drop_files(tab._invoice_table.viewport(), [pdf_file, image_file])
        timer.stop()

        assert seen == [[pdf_file.name, image_file.name]]
        # Abgebrochen: Es wurde nichts gespeichert.
        assert context.attachments.counts() == {}


class _FakeScanner:
    """Attrappe des KI-Modells: liefert feste Werte und merkt sich, was sie bekam."""

    def __init__(self) -> None:
        self.pages: list[bytes] = []

    def check(self) -> ScannerStatus:
        return ScannerStatus(True, "Bereit")

    def extract(self, images: Sequence[bytes], instructions: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        self.pages = list(images)
        return {
            "rechnungssteller": "Wasserwerke Musterstadt AG",
            "rechnungsnummer": "4711",
            "rechnungsdatum": "2026-02-14",
            "zeitraum_von": "2025-01-01",
            "zeitraum_bis": "2025-12-31",
            "gesamtbetrag_brutto": "1.512,40",
            "kostenart": "Wasserversorgung",
        }


class TestScanOfAttachedReceipt:
    def test_attached_receipt_can_be_read_out(
        self, qt_app: QApplication, context: AppContext, invoice: Cost, image_file: Path, tmp_path: Path
    ) -> None:
        scanner = _FakeScanner()
        scan_service = ReceiptScanService(ConfigManager(tmp_path / "scan.json"), lambda _settings: scanner)
        scan_service.status()  # Verfügbarkeit ist bekannt, bevor der Dialog aufgeht
        dialog = _dialog(context, invoice.property_id, receipt_scan=scan_service, attachment_paths=[image_file])
        editor = dialog._attachments_editor
        assert editor is not None and editor._scan_button.isVisibleTo(editor)

        editor._list.setCurrentRow(0)
        editor._request_scan()

        assert scanner.pages, "Der angehängte Beleg wurde nicht an die Erkennung übergeben."
        assert dialog._amount_spin.money() == Money.from_decimal("1512.40")
        assert dialog._selected_type() is not None and dialog._selected_type().name == "Wasserversorgung"  # type: ignore[union-attr]
        assert dialog._invoice_date_edit.value() == date(2026, 2, 14)

    def test_without_scanner_there_is_no_scan_button(
        self, qt_app: QApplication, context: AppContext, invoice: Cost
    ) -> None:
        dialog = _dialog(context, invoice.property_id, invoice)
        editor = dialog._attachments_editor
        assert editor is not None and not editor._scan_button.isVisibleTo(editor)
