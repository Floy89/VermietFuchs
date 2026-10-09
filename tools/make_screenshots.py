"""Erzeugt die Screenshots der Oberfläche mit Demo-Daten.

Aufruf aus dem Projektordner::

    python -m tools.make_screenshots

Die Bilder landen in ``docs/screenshots``. Das Skript arbeitet in einem
temporären Ordner und fasst weder die echte ``config.json`` noch echte
Benutzer-Datenbanken an.
"""

from __future__ import annotations

import sys
import tempfile
from datetime import date
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import QBuffer, QIODevice, QRect, Qt  # noqa: E402
from PyQt6.QtGui import QColor, QFont, QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication, QWidget  # noqa: E402

from app_context import AppContext  # noqa: E402
from main import _install_german_translation  # noqa: E402
from models.enums import AllocationKey, PhotoCategory  # noqa: E402
from models.cost import Cost, InvoiceSplit  # noqa: E402
from models.invoice_bundle import InvoiceBundle  # noqa: E402
from models.tariff import SplitCalculation  # noqa: E402
from models.value_objects import DateRange, Money  # noqa: E402
from ui.helpers import dispose_all_widgets, select_data  # noqa: E402
from integrations.ollama_client import OllamaReceiptScanner  # noqa: E402
from services.receipt_scan_service import ReceiptScanService, ScannerStatus  # noqa: E402
from services.user_service import UserService  # noqa: E402
from tools.demo_data import DEMO_USER_NAME, seed_demo_data  # noqa: E402
from ui import styles  # noqa: E402
from ui.dialogs.bundle_dialog import BundleDialog  # noqa: E402
from ui.dialogs.cost_dialog import CostDialog  # noqa: E402
from ui.dialogs.cost_type_dialog import CostTypeManagerDialog  # noqa: E402
from ui.dialogs.key_overrides_dialog import KeyOverridesDialog  # noqa: E402
from ui.dialogs.load_user_dialog import LoadUserDialog  # noqa: E402
from ui.dialogs.meter_reading_dialog import MeterReadingDialog  # noqa: E402
from ui.dialogs.receipt_scan_dialog import ReceiptScanSetupDialog  # noqa: E402
from ui.dialogs.splits_dialog import SplitsDialog  # noqa: E402
from ui.dialogs.prepayment_rates_dialog import PrepaymentRatesDialog  # noqa: E402
from ui.dialogs.tenant_dialog import TenantDialog  # noqa: E402
from ui.lookups import Lookups  # noqa: E402
from ui.main_window import _TAB_DEFINITIONS, MainWindow  # noqa: E402
from utils.config_manager import ConfigManager, ReceiptScanConfig  # noqa: E402

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "docs" / "screenshots"


class _ReadyScanner:
    """Attrappe einer eingerichteten Belegerkennung - nur für den Screenshot."""

    def check(self) -> ScannerStatus:
        return ScannerStatus(True, "Bereit")

    def extract(self, images: object, instructions: str, schema: object) -> dict[str, object]:
        return {}


def _fake_meter_photo(reading: str) -> bytes:
    """Zeichnet ein Platzhalter-"Foto" eines Zählers und gibt es als PNG zurück."""
    image = QImage(640, 420, QImage.Format.Format_RGB32)
    image.fill(QColor("#39424E"))
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor("#E9EDF2"))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRect(110, 90, 420, 240), 18, 18)
    painter.setBrush(QColor("#111418"))
    painter.drawRoundedRect(QRect(150, 150, 340, 90), 8, 8)
    painter.setPen(QColor("#F4F6F8"))
    painter.setFont(QFont("Consolas", 40, QFont.Weight.Bold))
    painter.drawText(QRect(150, 150, 340, 90), Qt.AlignmentFlag.AlignCenter, reading)
    painter.setPen(QColor("#39424E"))
    painter.setFont(QFont("Segoe UI", 14))
    painter.drawText(QRect(110, 255, 420, 50), Qt.AlignmentFlag.AlignCenter, "kWh  ·  Zähler-Nr. 48 221 907")
    painter.end()

    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


def _add_demo_photos(context: AppContext) -> None:
    """Hängt an einen Zählerstand ein Foto und legt ein Mängelfoto an."""
    readings = context.meter_readings.list_readings()
    if readings:
        newest = readings[0]
        context.meter_readings.save_reading(newest, "zaehler_heizung.png", _fake_meter_photo("17 980"))
        context.photos.add_photo(
            newest.apartment_id, PhotoCategory.DEFECT, "Wasserfleck Decke Bad", "bad_decke.png", _fake_meter_photo("- - -")
        )


def _grab(widget: QWidget, name: str) -> None:
    """Speichert ein Bildschirmfoto des Widgets."""
    QApplication.processEvents()
    QApplication.processEvents()
    path = OUTPUT_DIR / f"{name}.png"
    widget.grab().save(str(path))
    print(f"  {path.name}")


def _grab_checklist(context: AppContext, window: QWidget, invoices_tab: QWidget, year: int) -> None:
    """Fotografiert die Checkliste des Folgejahres und die Ansicht für einen eigenen Zeitraum."""
    house = next(p for p in context.properties.list_properties() if p.name == "Musterstraße 12")
    assert house.id is not None
    types = {cost_type.name: cost_type for cost_type in context.cost_types.list_types()}

    # Wann die Rechnungen des Abrechnungsjahres kamen (Kostenart -> Rechnungsdatum).
    invoice_dates = {
        "Verbundene Heizungs- und Warmwasserversorgung": date(year + 1, 3, 18),
        "Wasserversorgung": date(year + 1, 2, 14),
        "Müllbeseitigung": date(year, 1, 22),
        "Sach- und Haftpflichtversicherung": date(year, 1, 9),
        "Grundsteuer": date(year, 2, 3),
        "Gebäudereinigung": date(year + 1, 1, 15),
    }
    for invoice in context.costs.list_costs(house.id):
        invoice.invoice_date = invoice_dates.get(invoice.cost_type.name)
        context.costs.save_cost(invoice)

    added = [
        context.costs.save_cost(
            Cost(
                house.id,
                types[type_name],
                Money.from_decimal(amount),
                DateRange(date(year + 1, 1, 1), end),
                AllocationKey.AREA,
                description=description,
                invoice_date=invoice_date,
            )
        )
        for type_name, amount, end, description, invoice_date in (
            ("Müllbeseitigung", "921.40", date(year + 1, 12, 31), "Abfallgebühren", date(year + 1, 1, 20)),
            ("Sach- und Haftpflichtversicherung", "1310.00", date(year + 1, 12, 31), "Gebäudeversicherung", date(year + 1, 1, 8)),
            ("Grundsteuer", "742.80", date(year + 1, 12, 31), "", date(year + 1, 2, 2)),
            ("Gebäudereinigung", "270.00", date(year + 1, 6, 30), "Treppenhaus", date(year + 1, 7, 3)),
        )
    ]

    tabs = window._tabs  # type: ignore[attr-defined]
    tabs.setCurrentIndex(4)
    select_data(invoices_tab._property_combo, house.id)
    select_data(invoices_tab._year_combo, year + 1)
    invoices_tab.show_checklist()
    _grab(window, "22_rechnungen_checkliste")

    invoices_tab._pages.setCurrentIndex(0)
    invoices_tab.set_custom_period(DateRange(date(year, 7, 1), date(year + 1, 6, 30)))
    _grab(window, "23_rechnungen_eigener_zeitraum")

    for invoice in added:
        assert invoice.id is not None
        context.costs.delete_cost(invoice.id)
    select_data(invoices_tab._year_combo, year)


def _grab_consumption_difference(context: AppContext, window: QWidget, year: int) -> None:
    """Zeigt Dialog und Abrechnung, wenn der Hauptzähler mehr misst als die Wohnungszähler.

    Die Wasserrechnung bekommt dafür vorübergehend einen Gesamtverbrauch, der
    über der Summe der Wohnungszähler liegt; die Differenz wird je Wohnung
    verteilt. Danach wird die Rechnung wieder zurückgesetzt.
    """
    house = next(p for p in context.properties.list_properties() if p.name == "Musterstraße 12")
    assert house.id is not None
    water = next(c for c in context.costs.list_costs(house.id) if c.cost_type.name == "Wasserversorgung")
    original = (water.consumption, water.consumption_unit, water.difference_key)
    water.consumption, water.consumption_unit, water.difference_key = Decimal("480"), "m³", AllocationKey.UNITS
    context.costs.save_cost(water)

    tabs = window._tabs  # type: ignore[attr-defined]
    dialog = CostDialog(
        context.costs.save_cost,
        context.cost_types.list_types(),
        tabs.widget(4)._tenant_choices(house.id),
        house.id,
        water.period,
        water,
        window,
    )
    dialog.show()
    _grab(dialog, "24_dialog_verbrauchsdifferenz")
    dialog.close()

    billing_tab = tabs.widget(5)
    tabs.setCurrentIndex(5)
    select_data(billing_tab._period_bar._property_combo, house.id)
    billing_tab.refresh()
    billing_tab._statement_table.select_first()
    _grab(window, "25_abrechnung_verbrauchsdifferenz")

    water.consumption, water.consumption_unit, water.difference_key = original
    context.costs.save_cost(water)
    select_data(billing_tab._period_bar._property_combo, context.properties.list_properties()[0].id)


def _grab_attachments(context: AppContext, window: QWidget, scan_service: ReceiptScanService) -> None:
    """Zeigt den Rechnungsdialog mit angehängten Belegen und die Spalte "Belege" im Tab."""
    house = next(p for p in context.properties.list_properties() if p.name == "Musterstraße 12")
    assert house.id is not None
    water = next(c for c in context.costs.list_costs(house.id) if c.cost_type.name == "Wasserversorgung")
    assert water.id is not None
    context.attachments.add(water.id, "Stadtwerke_Wasser_Jahresrechnung.pdf", b"%PDF-1.4 " + b"0" * 184_000)
    context.attachments.add(water.id, "Anschreiben_Foto.jpg", _fake_meter_photo("- - -"))

    tabs = window._tabs  # type: ignore[attr-defined]
    invoices_tab = tabs.widget(4)
    dialog = CostDialog(
        context.costs.save_cost,
        context.cost_types.list_types(),
        invoices_tab._tenant_choices(house.id),
        house.id,
        water.period,
        water,
        window,
        receipt_scan=scan_service,
        attachments=context.attachments,
    )
    dialog.show()
    _grab(dialog, "26_dialog_belege")
    dialog.close()

    tabs.setCurrentIndex(4)
    select_data(invoices_tab._property_combo, house.id)
    invoices_tab.refresh()
    _grab(window, "27_rechnungen_belege")


def _grab_trend(context: AppContext, window: QWidget, year: int) -> None:
    """Zeigt den Tab "Entwicklung" - dafür bekommt das Demo-Haus vorübergehend Rechnungen früherer Jahre."""
    house = next(p for p in context.properties.list_properties() if p.name == "Musterstraße 12")
    assert house.id is not None
    current = [c for c in context.costs.list_costs(house.id) if c.period == DateRange.for_year(year)]
    added = []
    # Je Jahr zurück rund 6 % weniger - mit etwas Streuung, damit es nicht wie mit dem Lineal gezogen aussieht.
    for years_back, factor in ((1, "0.95"), (2, "0.87"), (3, "0.84"), (4, "0.76")):
        for invoice in current:
            amount = Money(int(invoice.amount.cents * Decimal(factor)))
            added.append(
                context.costs.save_cost(
                    Cost(house.id, invoice.cost_type, amount, DateRange.for_year(year - years_back),
                         invoice.allocation_key, invoice.consumption_share, invoice.description)
                )
            )

    tabs = window._tabs  # type: ignore[attr-defined]
    trend_tab = tabs.widget(7)
    tabs.setCurrentIndex(7)
    select_data(trend_tab._property_combo, house.id)
    trend_tab.refresh()
    trend_tab._from_spin.setValue(year - 4)
    trend_tab._to_spin.setValue(year)
    apartment = next(a for a in context.apartments.list_apartments(house.id) if a.number == "1. OG links")
    select_data(trend_tab._scope_combo, apartment.id)
    _grab(window, "34_entwicklung_wohnung")
    select_data(trend_tab._scope_combo, None)
    trend_tab._scope_combo.setCurrentIndex(0)
    _grab(window, "35_entwicklung_haus")

    for invoice in added:
        assert invoice.id is not None
        context.costs.delete_cost(invoice.id)


def main() -> None:
    """Erzeugt alle Screenshots."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    qt_app = QApplication(sys.argv)
    _install_german_translation(qt_app)
    styles.apply_theme(qt_app, styles.THEME_LIGHT)

    with tempfile.TemporaryDirectory() as temp:
        temp_dir = Path(temp)
        users = UserService(ConfigManager(temp_dir / "config.json"))
        entry = users.create_user(DEMO_USER_NAME, temp_dir / "Daten")
        users.create_user("Hausverwaltung_Beispiel", temp_dir / "Daten")

        # --- Startdialog ---------------------------------------------------
        load_dialog = LoadUserDialog(users)
        # Statt des temporären Ordners einen neutralen Speicherort anzeigen.
        for row in range(load_dialog._list.count()):
            entry_item = load_dialog._list.item(row)
            entry_item.setText(
                entry_item.text().replace(str(temp_dir / "Daten"), r"C:\Users\Beispiel\Documents\Nebenkostenabrechnung")
            )
        load_dialog.show()
        _grab(load_dialog, "00_benutzerauswahl")
        load_dialog.close()

        context = users.open_user(entry)
        seed_demo_data(context)
        _add_demo_photos(context)

        # --- Die sieben Tabs (hell) -----------------------------------------
        window = MainWindow(context)
        window.resize(1440, 900)
        # Die Demo-Datenbank liegt in einem temporären Ordner des angemeldeten
        # Windows-Benutzers - für die Bilder einen neutralen Pfad anzeigen.
        window._status_label.setText(
            "Datenbank: C:\\Users\\Beispiel\\Documents\\Nebenkostenabrechnung\\Demo_Vermieter\\benutzer.db"
        )
        window.show()
        tabs = window._tabs
        assert tabs is not None
        for index, (title, _icon, _cls) in enumerate(_TAB_DEFINITIONS):
            tabs.setCurrentIndex(index)
            _grab(window, f"{index + 1:02d}_{title.lower().replace('ä', 'ae')}")

        # --- Dialoge --------------------------------------------------------
        house = context.properties.list_properties()[1]
        assert house.id is not None
        cost = context.costs.list_costs(house.id)[0]
        billing_tab = tabs.widget(4)
        cost_dialog = CostDialog(
            context.costs.save_cost,
            context.cost_types.list_types(),
            billing_tab._tenant_choices(house.id),
            house.id,
            DateRange.for_year(cost.period.start.year),
            cost,
            window,
        )
        cost_dialog.show()
        _grab(cost_dialog, "08_dialog_kostenposition")
        cost_dialog.close()

        # Direkte Zuordnung (Messdienst) am Beispiel des zweiten Hauses
        house2 = context.properties.list_properties()[0]
        assert house2.id is not None
        direct_cost = next(c for c in context.costs.list_costs(house2.id) if c.direct_shares)
        direct_dialog = CostDialog(
            context.costs.save_cost,
            context.cost_types.list_types(),
            billing_tab._tenant_choices(house2.id),
            house2.id,
            direct_cost.period,
            direct_cost,
            window,
        )
        direct_dialog.show()
        _grab(direct_dialog, "12_dialog_direkte_zuordnung")
        direct_dialog.close()

        split_invoice = next(c for c in context.costs.list_costs(house2.id) if c.splits)
        splits_dialog = SplitsDialog(
            split_invoice.period, split_invoice.amount, split_invoice.consumption,
            split_invoice.consumption_unit, split_invoice.splits, window,
        )
        splits_dialog.show()
        _grab(splits_dialog, "19_splits")
        splits_dialog.close()

        # Gasrechnung, bei der Verbrauch und Kosten je Abschnitt aus Volumen und Preisen berechnet werden.
        gas_prices = {"working_price": Decimal("0.0612"), "base_price": Decimal("174.00"), "vat_percent": Decimal(19)}
        gas_splits = [
            InvoiceSplit(
                DateRange(start, end), Money.from_decimal(amount), Decimal(kwh),
                SplitCalculation(Decimal(volume), Decimal("0.9581"), Decimal(calorific), **gas_prices),
            )
            for start, end, volume, calorific, kwh, amount in (
                (date(2024, 6, 1), date(2024, 6, 30), "132", "11.420", "1444", "122.13"),
                (date(2024, 7, 1), date(2024, 9, 30), "301", "11.420", "3293", "291.87"),
                (date(2024, 10, 1), date(2024, 12, 31), "1187", "11.420", "12988", "997.94"),
                (date(2025, 1, 1), date(2025, 5, 31), "1954", "11.436", "21410", "1644.91"),
            )
        ]
        calculation_dialog = SplitsDialog(
            DateRange(date(2024, 6, 1), date(2025, 5, 31)), Money.from_decimal("3056.85"), Decimal("39135"),
            "kWh", gas_splits, window,
        )
        calculation_dialog.show()
        _grab(calculation_dialog, "29_splits_berechnung")
        calculation_dialog.close()

        types_dialog = CostTypeManagerDialog(context.cost_types, window)
        types_dialog.show()
        _grab(types_dialog, "13_kostenarten")
        types_dialog.close()

        # --- Optionale Belegerkennung ----------------------------------------
        # Zustand 1: nicht eingerichtet -> im Dialog erscheint nur ein Hinweis.
        new_cost_dialog = CostDialog(
            context.costs.save_cost,
            context.cost_types.list_types(),
            billing_tab._tenant_choices(house.id),
            house.id,
            DateRange.for_year(cost.period.start.year),
            parent=window,
            receipt_scan=None,
        )
        new_cost_dialog.show()
        _grab(new_cost_dialog, "15_kostenposition_ohne_belegerkennung")
        new_cost_dialog.close()

        # Zustand 2: eingerichtet -> Schaltfläche "Beleg scannen" ist da.
        ready_service = ReceiptScanService(ConfigManager(temp_dir / "scan.json"), lambda _settings: _ReadyScanner())
        ready_service.status()  # Verfügbarkeit vorab ermitteln (die Dialoge warten nicht darauf)
        scan_cost_dialog = CostDialog(
            context.costs.save_cost,
            context.cost_types.list_types(),
            billing_tab._tenant_choices(house.id),
            house.id,
            DateRange.for_year(cost.period.start.year),
            parent=window,
            receipt_scan=ready_service,
        )
        scan_cost_dialog.show()
        _grab(scan_cost_dialog, "16_kostenposition_mit_belegerkennung")
        scan_cost_dialog.close()

        # Einrichtungsdialog mit Anleitung (hier: Ollama nicht erreichbar).
        offline_config = ConfigManager(temp_dir / "offline.json")
        offline_config.set_receipt_scan_config(ReceiptScanConfig(url="http://localhost:9"))
        setup_dialog = ReceiptScanSetupDialog(ReceiptScanService(offline_config, OllamaReceiptScanner), window)
        setup_dialog.show()
        _grab(setup_dialog, "17_belegerkennung_einrichten")
        setup_dialog.close()

        lookups = Lookups(context)
        tenant_dialog = TenantDialog(context.tenants.save_tenant, lookups, lookups.tenants[0], parent=window)
        tenant_dialog.show()
        _grab(tenant_dialog, "09_dialog_mieter")
        tenant_dialog.close()

        hoffmann = next(t for t in lookups.tenants if t.name == "Familie Hoffmann")
        rates_dialog = PrepaymentRatesDialog(context.prepayment_rates, hoffmann, window)
        rates_dialog.show()
        _grab(rates_dialog, "14_vorauszahlungen_historie")
        rates_dialog.close()

        # --- Abweichender Umlageschlüssel (nur für die Screenshots, danach wieder entfernt) ---
        becker = next(t for t in lookups.tenants if t.name == "Tom Becker")
        waste = context.cost_types.get_by_name("Müllbeseitigung")
        assert becker.id is not None and waste.id is not None
        context.key_overrides.set_override(becker.id, waste.id, AllocationKey.AREA)
        keys_dialog = KeyOverridesDialog(context.key_overrides, becker, window)
        keys_dialog.show()
        _grab(keys_dialog, "20_umlageschluessel_mieter")
        keys_dialog.close()
        tabs.setCurrentIndex(5)
        tabs.widget(5).refresh()
        _grab(window, "21_abrechnung_abweichender_schluessel")
        for override in context.key_overrides.list_for_tenant(becker.id):
            assert override.id is not None
            context.key_overrides.remove_override(override.id)

        # --- Rechnungs-Checkliste und eigener Zeitraum -------------------------
        # Für das Folgejahr sind erst einige Rechnungen da; die Rechnungsdaten des
        # Abrechnungsjahres dienen als Schätzung, wann der Rest kommt. Die
        # zusätzlichen Rechnungen werden danach wieder entfernt.
        _grab_checklist(context, window, tabs.widget(4), cost.period.start.year)

        # --- Differenz zwischen Gesamtverbrauch und Wohnungszählern -------------
        _grab_consumption_difference(context, window, cost.period.start.year)

        # --- Belege an Rechnungen ----------------------------------------------
        _grab_attachments(context, window, ready_service)

        # --- Zählerstand vom Foto ablesen (Erkennung eingerichtet) ---------------
        photo_file = temp_dir / "wasserzaehler_keller.png"
        photo_file.write_bytes(_fake_meter_photo("00412"))
        reading_dialog = MeterReadingDialog(
            context.meter_readings.save_reading, lookups, parent=window, photo_path=photo_file, receipt_scan=ready_service
        )
        reading_dialog.show()
        _grab(reading_dialog, "28_dialog_zaehlerstand_ablesen")
        reading_dialog.close()

        # --- Sammelrechnung: eine Rechnung, mehrere Kostenarten -------------------
        year = cost.period.start.year
        city_bundle = context.bundles.save(
            InvoiceBundle(house.id, "Stadt Köln, Grundbesitzabgaben", date(year, 1, 20), Money.from_decimal("1335.40")),
            [
                Cost(house.id, context.cost_types.get_by_name(name), Money.from_decimal(amount), DateRange.for_year(year), key)
                for name, amount, key in (
                    ("Straßenreinigung", "186.20", AllocationKey.AREA),
                    ("Niederschlagswasser", "254.40", AllocationKey.AREA),
                    ("Entwässerung", "894.80", AllocationKey.AREA),
                )
            ],
        )
        assert city_bundle.id is not None
        context.attachments.add_to_bundle(city_bundle.id, "Abgabenbescheid_Stadt.pdf", b"%PDF-1.4 " + b"0" * 96_000)
        bundle_dialog = BundleDialog(
            context.bundles, context.attachments, context.cost_types.list_types(), house.id,
            DateRange.for_year(year), city_bundle, context.bundles.positions(city_bundle.id), window,
        )
        bundle_dialog.show()
        _grab(bundle_dialog, "30_dialog_sammelrechnung")
        bundle_dialog.close()
        tabs.setCurrentIndex(4)
        select_data(tabs.widget(4)._property_combo, house.id)
        tabs.widget(4).refresh()
        _grab(window, "31_rechnungen_sammelrechnung")
        context.bundles.delete(city_bundle.id)

        # --- Empfehlung für die Vorauszahlung ---------------------------------------
        tabs.setCurrentIndex(5)
        select_data(tabs.widget(5)._period_bar._property_combo, house.id)
        tabs.widget(5).refresh()
        _grab(window, "32_abrechnung_empfehlung")
        tabs.setCurrentIndex(6)
        select_data(tabs.widget(6)._period_bar._property_combo, house.id)
        tabs.widget(6).refresh()
        _grab(window, "33_berichte_empfehlung")

        # --- Entwicklung der Nebenkosten mit Prognose -------------------------------
        _grab_trend(context, window, cost.period.start.year)

        # --- Dunkles Design ---------------------------------------------------
        window._toggle_theme()
        tabs = window._tabs
        assert tabs is not None
        tabs.setCurrentIndex(0)
        _grab(window, "10_dashboard_dunkel")
        tabs.setCurrentIndex(5)
        _grab(window, "11_abrechnung_dunkel")

        # --- Beispiel-PDF -----------------------------------------------------
        result = context.billing.create_billing(house.id, DateRange.for_year(cost.period.start.year))
        pdf_path = OUTPUT_DIR.parent / "Beispiel_Nebenkostenabrechnung.pdf"
        context.pdf_export.export_statements(result, result.statements, context.settings.load_landlord(), pdf_path)
        print(f"  {pdf_path.name}")

        window.close()  # schließt auch die Datenbank
        dispose_all_widgets(qt_app)


if __name__ == "__main__":
    main()
