"""Tests für Sammelrechnungen: eine Rechnung mit mehreren Kostenarten (z. B. Abgabenbescheid der Stadt)."""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from database.migrations import MIGRATIONS, current_version, migrate
from models.apartment import Apartment
from models.cost import Cost
from models.enums import AllocationKey
from models.exceptions import NotFoundError, ValidationError
from models.invoice_bundle import InvoiceBundle
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from ui.dialogs.bundle_dialog import BundleDialog
from ui.widgets.invoices_tab import InvoicesTab

YEAR = DateRange.for_year(2025)
INVOICE_DATE = date(2025, 1, 20)
PDF_BYTES = b"%PDF-1.4 Abgabenbescheid"

#: Der Bescheid der Stadt: Kostenart, Betrag, Umlageschlüssel
CITY_POSITIONS = [
    ("Grundsteuer", "742.80", AllocationKey.AREA),
    ("Müllbeseitigung", "894.60", AllocationKey.PERSONS),
    ("Straßenreinigung", "186.20", AllocationKey.AREA),
    ("Niederschlagswasser", "254.40", AllocationKey.AREA),
]
CITY_TOTAL = Money.from_decimal("2078.00")


@pytest.fixture
def house_id(context: AppContext) -> int:
    house = context.properties.save_property(Property(name="Stadthaus", address="Rathausplatz 1, 44787 Bochum"))
    assert house.id is not None
    for number, area, persons in (("EG", "60", 1), ("OG", "40", 3)):
        flat = context.apartments.save_apartment(Apartment(house.id, number, Decimal(area), persons))
        assert flat.id is not None
        context.tenants.save_tenant(
            Tenant(apartment_id=flat.id, name=f"Mieter {number}", start_date=date(2020, 1, 1)),
            initial_prepayment=Money.from_decimal("100.00"),
        )
    return house.id


def _positions(context: AppContext, house_id: int) -> list[Cost]:
    return [
        Cost(house_id, context.cost_types.get_by_name(name), Money.from_decimal(amount), YEAR, key)
        for name, amount, key in CITY_POSITIONS
    ]


def _save_city_invoice(context: AppContext, house_id: int) -> InvoiceBundle:
    bundle = InvoiceBundle(house_id, "Stadt Bochum, Grundbesitzabgaben", INVOICE_DATE, CITY_TOTAL)
    return context.bundles.save(bundle, _positions(context, house_id))


class TestService:
    def test_one_invoice_creates_one_position_per_cost_type(self, context: AppContext, house_id: int) -> None:
        bundle = _save_city_invoice(context, house_id)
        assert bundle.id is not None

        positions = context.bundles.positions(bundle.id)

        assert [(p.cost_type.name, p.amount, p.allocation_key) for p in positions] == [
            (name, Money.from_decimal(amount), key) for name, amount, key in CITY_POSITIONS
        ]
        # Kopfdaten stehen in jeder Position - so erscheinen sie in Listen und in der Abrechnung.
        assert {p.description for p in positions} == {"Stadt Bochum, Grundbesitzabgaben"}
        assert {p.invoice_date for p in positions} == {INVOICE_DATE}
        assert {p.bundle_id for p in positions} == {bundle.id}
        assert len(context.costs.list_costs(house_id)) == 4

    def test_sum_must_match_the_invoice_total(self, context: AppContext, house_id: int) -> None:
        bundle = InvoiceBundle(house_id, "Stadt", INVOICE_DATE, Money.from_decimal("2100.00"))

        with pytest.raises(ValidationError, match=r"Differenz: 22,00 €"):
            context.bundles.save(bundle, _positions(context, house_id))

        # Nichts wurde gespeichert - weder Kopf noch Positionen.
        assert context.costs.list_costs(house_id) == []

    def test_without_total_the_sum_is_not_checked(self, context: AppContext, house_id: int) -> None:
        bundle = context.bundles.save(InvoiceBundle(house_id, "Stadt"), _positions(context, house_id))
        assert bundle.id is not None and len(context.bundles.positions(bundle.id)) == 4

    def test_invalid_bundles_are_rejected(self, context: AppContext, house_id: int) -> None:
        with pytest.raises(ValidationError, match="mindestens eine Position"):
            context.bundles.save(InvoiceBundle(house_id, "Stadt"), [])

        water = context.cost_types.get_by_name("Wasserversorgung")
        direct = Cost(house_id, water, Money.from_decimal("10.00"), YEAR, AllocationKey.DIRECT, direct_shares={1: Money(1000)})
        with pytest.raises(ValidationError, match="einzelne Rechnung"):
            context.bundles.save(InvoiceBundle(house_id, "Stadt"), [direct])

        # Ein für die Kostenart unzulässiger Schlüssel verhindert das Speichern aller Positionen.
        wrong = Cost(house_id, water, Money.from_decimal("10.00"), YEAR, AllocationKey.CONSUMPTION_AREA)
        with pytest.raises(ValidationError):
            context.bundles.save(InvoiceBundle(house_id, "Stadt"), [*_positions(context, house_id), wrong])
        assert context.costs.list_costs(house_id) == []

    def test_editing_updates_adds_and_removes_positions(self, context: AppContext, house_id: int) -> None:
        bundle = _save_city_invoice(context, house_id)
        assert bundle.id is not None
        tax, waste, street, rain = context.bundles.positions(bundle.id)
        assert waste.id is not None
        context.costs.set_excluded(waste.id, True)
        waste = next(p for p in context.bundles.positions(bundle.id) if p.id == waste.id)

        # Grundsteuer wird teurer, Straßenreinigung entfällt, Winterdienst kommt dazu.
        tax.amount = Money.from_decimal("800.00")
        winter = Cost(house_id, context.cost_types.get_by_name("Winterdienst"), Money.from_decimal("90.00"), YEAR, AllocationKey.AREA)
        bundle.total_amount = Money.from_decimal("2039.00")
        bundle.description = "Stadt Bochum, Änderungsbescheid"
        context.bundles.save(bundle, [tax, waste, rain, winter])

        positions = context.bundles.positions(bundle.id)
        assert [p.cost_type.name for p in positions] == ["Grundsteuer", "Müllbeseitigung", "Niederschlagswasser", "Winterdienst"]
        # Bestehende Positionen behalten ihre ID - und damit z. B. ihren Ausschluss.
        assert positions[0].id == tax.id and positions[0].amount == Money.from_decimal("800.00")
        assert positions[1].id == waste.id and positions[1].excluded
        assert {p.description for p in positions} == {"Stadt Bochum, Änderungsbescheid"}
        assert street.id is not None
        assert all(c.id != street.id for c in context.costs.list_costs(house_id))

    def test_delete_removes_positions_and_receipts(self, context: AppContext, house_id: int) -> None:
        bundle = _save_city_invoice(context, house_id)
        assert bundle.id is not None
        context.attachments.add_to_bundle(bundle.id, "bescheid.pdf", PDF_BYTES)

        context.bundles.delete(bundle.id)

        assert context.costs.list_costs(house_id) == []
        assert context.attachments.counts() == {}
        with pytest.raises(NotFoundError):
            context.bundles.get(bundle.id)

    def test_bundle_disappears_with_its_last_position(self, context: AppContext, house_id: int) -> None:
        bundle = _save_city_invoice(context, house_id)
        assert bundle.id is not None
        positions = context.bundles.positions(bundle.id)

        for position in positions[:-1]:
            assert position.id is not None
            context.costs.delete_cost(position.id)
            assert not context.bundles.remove_if_empty(bundle.id)
        last = positions[-1]
        assert last.id is not None
        context.costs.delete_cost(last.id)

        assert context.bundles.remove_if_empty(bundle.id)
        with pytest.raises(NotFoundError):
            context.bundles.get(bundle.id)


class TestReceipts:
    def test_one_receipt_counts_for_every_position(self, context: AppContext, house_id: int) -> None:
        bundle = _save_city_invoice(context, house_id)
        assert bundle.id is not None
        single = context.costs.save_cost(
            Cost(house_id, context.cost_types.get_by_name("Gebäudereinigung"), Money.from_decimal("50.00"), YEAR, AllocationKey.AREA)
        )
        assert single.id is not None

        context.attachments.add_to_bundle(bundle.id, "bescheid.pdf", PDF_BYTES)
        context.attachments.add(single.id, "reinigung.pdf", PDF_BYTES)

        counts = context.attachments.counts()
        assert [counts[p.id] for p in context.bundles.positions(bundle.id)] == [1, 1, 1, 1]  # type: ignore[index]
        assert counts[single.id] == 1
        assert [a.file_name for a in context.attachments.list_for_bundle(bundle.id)] == ["bescheid.pdf"]
        # In der Datenbank liegt der Bescheid trotzdem nur einmal.
        assert context.database.query_one("SELECT COUNT(*) AS n FROM cost_attachments")["n"] == 2  # type: ignore[index]

    def test_receipt_needs_exactly_one_owner(self, context: AppContext, house_id: int) -> None:
        from models.attachment import CostAttachment

        with pytest.raises(ValidationError, match="genau einer"):
            CostAttachment(file_name="a.pdf", file_data=PDF_BYTES).validate()
        with pytest.raises(ValidationError, match="genau einer"):
            CostAttachment(file_name="a.pdf", file_data=PDF_BYTES, cost_id=1, bundle_id=1).validate()


class TestBilling:
    def test_each_position_is_billed_with_its_own_key(self, context: AppContext, house_id: int) -> None:
        _save_city_invoice(context, house_id)

        result = context.billing.create_billing(house_id, YEAR)

        assert result.total_costs == CITY_TOTAL and result.total_allocated == CITY_TOTAL
        ground_floor = next(s for s in result.statements if s.apartment.number == "EG")
        by_type = {share.cost.cost_type.name: share.amount for share in ground_floor.shares}
        # EG: 60 von 100 m², aber nur 1 von 4 Personen.
        assert by_type["Grundsteuer"] == Money.from_decimal("445.68")
        assert by_type["Müllbeseitigung"] == Money.from_decimal("223.65")
        assert all(share.cost.description == "Stadt Bochum, Grundbesitzabgaben" for share in ground_floor.shares)


class TestMigration:
    def test_existing_receipts_survive_the_upgrade(self) -> None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        connection.execute("PRAGMA foreign_keys = ON")
        # Datenbank auf dem Stand vor den Sammelrechnungen (Schema 9) aufbauen.
        for target, script in MIGRATIONS:
            if target <= 9:
                connection.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")
        connection.execute(
            "INSERT INTO properties (name, address, created_at) VALUES ('Haus', 'Adresse 1', '2025-01-01T00:00:00')"
        )
        type_id = connection.execute("SELECT cost_type_id FROM cost_types LIMIT 1").fetchone()[0]
        connection.execute(
            "INSERT INTO costs (property_id, cost_type_id, description, amount, period_start, period_end, allocation_key) "
            "VALUES (1, ?, 'Alt', 1000, '2025-01-01', '2025-12-31', 'AREA')",
            (type_id,),
        )
        connection.execute(
            "INSERT INTO cost_attachments (cost_id, file_name, file_data, uploaded_at) VALUES (1, 'alt.pdf', ?, '2025-02-01T10:00:00')",
            (PDF_BYTES,),
        )

        migrate(connection, None)

        assert current_version(connection) >= 10
        row = connection.execute("SELECT cost_id, bundle_id, file_name, file_data FROM cost_attachments").fetchone()
        assert (row[0], row[1], row[2], bytes(row[3])) == (1, None, "alt.pdf", PDF_BYTES)
        assert connection.execute("SELECT bundle_id FROM costs").fetchone()[0] is None
        # Die Regel "genau ein Besitzer" gilt jetzt in der Datenbank.
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO cost_attachments (file_name, file_data, uploaded_at) VALUES ('x.pdf', x'00', '2025-01-01T00:00:00')"
            )


class TestDialog:
    def _new_dialog(self, context: AppContext, house_id: int, **extra: object) -> BundleDialog:
        return BundleDialog(
            context.bundles, context.attachments, context.cost_types.list_types(), house_id, YEAR, **extra  # type: ignore[arg-type]
        )

    def test_new_bundle_with_positions_and_receipt(
        self, qt_app: QApplication, context: AppContext, house_id: int, tmp_path: Path
    ) -> None:
        receipt = tmp_path / "bescheid.pdf"
        receipt.write_bytes(PDF_BYTES)
        dialog = self._new_dialog(context, house_id, attachment_paths=[receipt])
        dialog._description_edit.setText("Stadt Bochum")
        dialog._total_spin.set_money(CITY_TOTAL)
        types = {t.name: t.id for t in context.cost_types.list_types()}
        for index, (name, amount, key) in enumerate(CITY_POSITIONS):
            if index > 0:
                dialog._add_row(None)
            row = dialog._rows[index]
            row.type_combo.setCurrentIndex(row.type_combo.findData(types[name]))
            row.amount_spin.set_money(Money.from_decimal(amount))
            # Der Standard-Schlüssel der Kostenart ist vorgewählt.
            assert row.key_combo.currentData() is key
        assert dialog._status.text().startswith("✓")

        dialog.accept()

        positions = context.costs.list_costs(house_id)
        assert [(p.cost_type.name, p.amount) for p in sorted(positions, key=lambda p: p.id or 0)] == [
            (name, Money.from_decimal(amount)) for name, amount, _key in CITY_POSITIONS
        ]
        bundle_id = positions[0].bundle_id
        assert bundle_id is not None
        assert [a.file_name for a in context.attachments.list_for_bundle(bundle_id)] == ["bescheid.pdf"]

    def test_mismatch_keeps_the_dialog_open(self, qt_app: QApplication, context: AppContext, house_id: int) -> None:
        dialog = self._new_dialog(context, house_id)
        dialog._total_spin.set_money(Money.from_decimal("100.00"))
        dialog._rows[0].amount_spin.set_money(Money.from_decimal("60.00"))

        dialog.accept()

        assert dialog.result() != BundleDialog.DialogCode.Accepted
        assert "Differenz" in dialog._error_label.text()
        assert context.costs.list_costs(house_id) == []

    def test_new_row_suggests_the_remaining_amount_and_an_unused_type(
        self, qt_app: QApplication, context: AppContext, house_id: int
    ) -> None:
        dialog = self._new_dialog(context, house_id)
        dialog._total_spin.set_money(Money.from_decimal("100.00"))
        dialog._rows[0].amount_spin.set_money(Money.from_decimal("60.00"))

        dialog._add_row(None)

        assert dialog._rows[1].amount_spin.money() == Money.from_decimal("40.00")
        assert dialog._rows[1].type_combo.currentData() != dialog._rows[0].type_combo.currentData()

    def test_editing_an_existing_bundle(self, qt_app: QApplication, context: AppContext, house_id: int) -> None:
        bundle = _save_city_invoice(context, house_id)
        assert bundle.id is not None
        before = context.bundles.positions(bundle.id)
        dialog = self._new_dialog(context, house_id, bundle=bundle, positions=before)
        assert dialog._description_edit.text() == "Stadt Bochum, Grundbesitzabgaben"
        assert len(dialog._rows) == 4 and dialog._status.text().startswith("✓")

        # Straßenreinigung entfernen und den Gesamtbetrag anpassen.
        dialog._table.selectRow(2)
        dialog._remove_selected()
        dialog._total_spin.set_money(Money.from_decimal("1891.80"))
        dialog.accept()

        after = context.bundles.positions(bundle.id)
        assert [p.id for p in after] == [before[0].id, before[1].id, before[3].id]
        assert context.bundles.get(bundle.id).total_amount == Money.from_decimal("1891.80")


class TestInvoicesTab:
    def test_editing_a_position_opens_the_whole_invoice(
        self, qt_app: QApplication, context: AppContext, house_id: int
    ) -> None:
        _save_city_invoice(context, house_id)
        tab = InvoicesTab(context)
        tab.refresh()
        tab._invoice_table.select_first()
        opened: list[tuple[str, int]] = []
        timer = QTimer()
        timer.setInterval(10)

        def inspect_and_close() -> None:
            dialog = QApplication.activeModalWidget()
            if dialog is not None:
                timer.stop()
                rows = len(dialog._rows) if isinstance(dialog, BundleDialog) else -1
                opened.append((type(dialog).__name__, rows))
                dialog.reject()  # type: ignore[attr-defined]

        timer.timeout.connect(inspect_and_close)
        timer.start()
        tab._edit_invoice()
        timer.stop()

        assert opened == [("BundleDialog", 4)]

    def test_positions_are_marked_and_show_the_shared_receipt(
        self, qt_app: QApplication, context: AppContext, house_id: int
    ) -> None:
        bundle = _save_city_invoice(context, house_id)
        assert bundle.id is not None
        context.attachments.add_to_bundle(bundle.id, "bescheid.pdf", PDF_BYTES)
        tab = InvoicesTab(context)
        tab.refresh()

        model = tab._invoice_table.model()
        descriptions = {model.index(row, 1).data() for row in range(model.rowCount())}
        assert descriptions == {"Stadt Bochum, Grundbesitzabgaben · Sammelrechnung"}
        assert set(tab._attachment_counts.values()) == {1} and len(tab._attachment_counts) == 4
