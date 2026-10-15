"""Tests für das Auslesen von Belegen ohne KI und das Lernen aus Korrekturen.

Die Belegtexte sind erfunden, aber aufgebaut wie echte Versorgerrechnungen
(Text aus dem PDF bzw. aus der Windows-Texterkennung).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from integrations.windows_ocr import Word, rows_from_words
from models.document import FieldAnchor
from models.enums import AllocationKey
from models.property import Property
from models.value_objects import DateRange, Money
from services.document_reader import (
    find_amount,
    find_cost_type,
    identify_issuer,
    learn_anchor,
    line_pattern,
    normalize_text,
    read_document,
    split_lines,
)

NAMES = [
    "Grundsteuer", "Wasserversorgung", "Entwässerung", "Niederschlagswasser", "Müllbeseitigung",
    "Brennstoffkosten (Gas/Öl)", "Beleuchtung (Allgemeinstrom)", "Straßenreinigung", "Heizung",
]

#: Wasserrechnung mit Zwischenablesung - so liefert das PDF seinen Text.
WATER = """
WASSERWERKE MUSTERSTADT AG · Postfach 12 34 · 12345 Musterstadt
Rechnungsdatum: 15.10.2026
Trinkwasser: Ihre Jahresrechnung 2026
vielen Dank, dass wir Sie im Zeitraum vom 13.09.25 bis zum 30.09.26 versorgen durften.
Trinkwasser 787,08 Euro
Rechnungsbetrag
darin enthaltene Umsatzsteuer 51,49 Euro
787,08 Euro
- eingegangene Zahlungen 720,00 Euro
Ihr zu zahlender Betrag 67,08 Euro
Ihr Trinkwasserverbrauch
Anfangszählerstand 13.09.25 418 m³
Zwischenablesung - Internet 31.12.25 493 m³ 75 m³
Turnusablesung - Internet 30.09.26 708 m³ 215 m³
Gesamtverbrauch 290 m³
Mengenpreis 13.09.25 - 30.09.26 290 m³ 1,9829 Euro / m³ 575,04 Euro
Grundpreis 13.09.25 - 30.09.26 383 Tage 153,00 Euro / 365 Tage 160,55 Euro
Summe 735,59 Euro
Umsatzsteuer 7 % von 735,59 Euro 51,49 Euro
Rechnungsbetrag 787,08 Euro
Hinweise zum Abwasser erhalten Sie von Ihrer Stadt.
USt-IdNr.: DE 123456789
"""

#: Dieselbe Seite, wie sie die Texterkennung liefert - mit typischen Lesefehlern.
WATER_OCR = """
Rechnungsdatum: 15.102026
Anfangszählerstand 13.09.25 418 m'
Zwischenablesung - Internet 31 .12.25 493 m³ 75
Turnusablesung - Internet 30.09.26 708 rn3 215 m³
Gesamtverbrauch 290
Mengenpreis 13.09.25 - 30.09.26 290 m³ 1 ,9829 Euro / m' 575,04 Euro
Grundpreis 13.09.25 - 30.09.26 383 Tage 153,00 Euro / 365 Tage 160,55 Euro
Summe 735,59 Euro
Umsatzsteuer 7 % von 735,59 Euro 51 ,49 Euro
Rechnungsbetra 87 08 Eu
"""

#: Gasrechnung: Abschnitte mit Volumen, Zustandszahl und Brennwert, Preise je kWh und Jahr.
GAS = """
Stadtwerke Beispielstadt GmbH
service@stadtwerke-beispielstadt.de
Rechnungsdatum 12.07.2025
Abrechnungszeitraum 01.06.2024 - 31.05.2025
Zählernummer Zeitraum Zählerstand alt Zählerstand neu Ableseart Abrechnungsvolumen Zustandszahl Brennwert
1053045 01.06.2024 30.06.2024 67.819 67.951 S 132 0,9581 11,420
1053045 01.07.2024 30.09.2024 67.951 68.252 S 301 0,9581 11,420
1053045 01.10.2024 31.12.2024 68.252 69.439 K 1.187 0,9581 11,420
1053045 01.01.2025 31.05.2025 69.439 71.393 K 1.954 0,9581 11,436
Ihr Gesamtverbrauch 39.135 kWh
Arbeitspreis 01.06.2024 31.05.2025 39.135 kWh 0,0612 EUR/kWh 2.395,06 EUR
Grundpreis 01.06.2024 31.05.2025 365 Tag(e) 174,00 EUR/Jahr 174,00 EUR
Umsatzsteuer 01.06.2024 31.05.2025 2.569,06 EUR 19 % 488,12 EUR
Summe brutto 3.057,18 EUR
"""


#: Gasrechnung mit anderem Aufbau: ausgeschriebenes Datum, Preise ändern sich zum Jahreswechsel,
#: Abkürzungen AP/GP, Tabelle mit Brennwert vor Zustandszahl und kWh am Zeilenende.
GAS_SPLIT_PRICES = """
LOGO Musterenergie
Musterenergie GmbH, Hauptstr. 1, 12345 Musterstadt
Rechnungsdatum 09. Juli 2026
Abrechnungszeitraum: 05.07.2025 - 30.06.2026
Durch den Wegfall der Umlage hat sich Ihr Rechnungsbetrag um 69,68 Euro verringert.
Gas 0 kWh (0) 41.058 kWh (361) 3.471,02 659,49 (19%) 4.130,51
Gesamtbetrag 3.471,02 659,49 4.130,51
bis 07.07.2026 gezahlt -4.212,00
Zeitraum Zählerstand in m³ Menge
05.07.2025 - 31.12.2025 71.555 73.069 1) 1.514 11,630 0,9626 16.949 kWh
01.01.2026 - 30.06.2026 73.069 75.222 1) 2.153 11,633 0,9626 24.109 kWh
Summe 41.058 kWh
05.07.2025 - 31.12.2025 AP Komforttarif 8,260 Ct/kWh 16.949 kWh 19% 1.399,99 €
01.01.2026 - 30.06.2026 AP Komforttarif 7,970 Ct/kWh 24.109 kWh 19% 1.921,49 €
05.07.2025 - 31.12.2025 GP Komforttarif {base} €/Jahr 180 Tage 19% 74,56 €
01.01.2026 - 30.06.2026 GP Komforttarif {base} €/Jahr 181 Tage 19% 74,98 €
Summe 3.471,02 €
Quelle: Musterenergie GmbH
USt-IdNr: DE 987654321
"""


class TestTextBasics:
    def test_ocr_mistakes_are_repaired(self) -> None:
        assert normalize_text("Rechnungsdatum 09. Juli 2026, 1. Dez. 2025") == "Rechnungsdatum 09.07.2026, 01.12.2025"
        text = normalize_text("Datum 15.102026 / 31 .12.25 / 1 ,9829 Euro / m' / 493 rn3 / 290 m'")
        assert "15.10.2026" in text and "31.12.25" in text and "1,9829" in text
        assert "/ m³" in text and "493 m³" in text and "290 m³" in text

    def test_line_pattern_replaces_numbers_and_dates(self) -> None:
        assert line_pattern("Rechnungsbetrag 787,08 Euro") == "rechnungsbetrag # euro"
        assert line_pattern("Rechnungsdatum: 15.10.2026") == "rechnungsdatum: d"

    def test_issuer_from_vat_id_web_or_name(self) -> None:
        assert identify_issuer(WATER) == ("ust:DE123456789", "WASSERWERKE MUSTERSTADT AG")
        assert identify_issuer(GAS) == ("web:stadtwerke-beispielstadt.de", "Stadtwerke Beispielstadt GmbH")
        assert identify_issuer("Müller Hausdienste GmbH\nRechnung") == ("name:müller hausdienste gmbh", "Müller Hausdienste GmbH")
        assert identify_issuer("nichts Erkennbares") == ("", "")
        # Logo-Text vor dem Namen (Texterkennung) fällt weg, der vollständige Name bleibt.
        noisy = "Musterenergie Jr Energie MUSTERENERGIF Energie GmbH\nMusterenergie Energie GmbH\nan Musterenergie Energie GmbH"
        assert identify_issuer(noisy)[1] == "Musterenergie Energie GmbH"

    def test_cost_type_by_frequency(self) -> None:
        # Ein einzelnes "Abwasser" im Kleingedruckten einer Trinkwasserrechnung gibt nicht den Ausschlag.
        assert find_cost_type(WATER, NAMES) == "Wasserversorgung"
        assert find_cost_type(GAS + "Erdgas", NAMES) == "Brennstoffkosten (Gas/Öl)"
        assert find_cost_type("Grundbesitzabgaben: Grundsteuer B", NAMES) == "Grundsteuer"
        assert find_cost_type("Rechnung", NAMES) == ""

    def test_amount_skips_headings_tax_and_payments(self) -> None:
        heading = ["Ihr Rechnungsbetrag", "Zeitraum Menge Preis", "Mengenpreis 290 m³ 1,98 Euro 575,04 Euro"]
        assert find_amount(heading) is None
        assert find_amount(["Rechnungsbetrag", "darin Umsatzsteuer 51,49 Euro", "787,08 Euro"]) == Money.from_decimal("787.08")
        # Rückfall: netto + Steuer aus der Umsatzsteuer-Zeile.
        assert find_amount(["Umsatzsteuer 7 % von 735,59 Euro 51,49 Euro"]) == Money.from_decimal("787.08")

    def test_rows_from_ocr_words(self) -> None:
        # Die Texterkennung liefert eine Tabelle spaltenweise; gleiche Höhe = gleiche Zeile.
        words = [
            Word("Zwischenablesung", 10, 100, 120, 12), Word("Turnusablesung", 10, 130, 110, 12),
            Word("31.12.25", 300, 101, 60, 12), Word("30.09.26", 300, 131, 60, 12),
            Word("75", 500, 99, 20, 12), Word("215", 500, 129, 25, 12),
        ]
        assert rows_from_words(words) == ["Zwischenablesung 31.12.25 75", "Turnusablesung 30.09.26 215"]
        assert rows_from_words([]) == []


class TestReading:
    def test_water_invoice_with_splits(self) -> None:
        draft = read_document(WATER, NAMES)

        assert draft.issuer_key == "ust:DE123456789" and draft.description == "Wasserwerke Musterstadt AG"
        assert draft.cost_type_name == "Wasserversorgung"
        assert draft.invoice_date == date(2026, 10, 15)
        assert draft.period == DateRange(date(2025, 9, 13), date(2026, 9, 30))
        assert draft.amount == Money.from_decimal("787.08")
        assert (draft.consumption, draft.consumption_unit) == (Decimal("290"), "m³")
        assert (draft.working_price, draft.base_price_per_year, draft.vat_percent) == (
            Decimal("1.9829"), Decimal("153.00"), Decimal("7"),
        )
        # Zwischenablesung am 31.12. -> zwei Splits, Beträge exakt passend zur Rechnung.
        assert [(s.period, s.consumption, s.amount) for s in draft.splits] == [
            (DateRange(date(2025, 9, 13), date(2025, 12, 31)), Decimal("75"), Money.from_decimal("208.47")),
            (DateRange(date(2026, 1, 1), date(2026, 9, 30)), Decimal("215"), Money.from_decimal("578.61")),
        ]

    def test_same_page_from_text_recognition(self) -> None:
        draft = read_document(WATER_OCR, NAMES, source="ocr")

        assert draft.source == "ocr"
        assert draft.invoice_date == date(2026, 10, 15)
        assert draft.amount == Money.from_decimal("787.08")  # aus netto + Steuer, der Betrag selbst war unleserlich
        assert (draft.consumption, draft.consumption_unit) == (Decimal("290"), "m³")
        assert [s.amount for s in draft.splits] == [Money.from_decimal("208.47"), Money.from_decimal("578.61")]

    def test_gas_invoice_with_volume_rows(self) -> None:
        draft = read_document(GAS, NAMES)

        assert draft.period == DateRange(date(2024, 6, 1), date(2025, 5, 31))
        assert draft.amount == Money.from_decimal("3057.18")
        assert (draft.consumption, draft.consumption_unit) == (Decimal("39135"), "kWh")
        assert (draft.working_price, draft.base_price_per_year, draft.vat_percent) == (
            Decimal("0.0612"), Decimal("174.00"), Decimal("19"),
        )
        assert [s.consumption for s in draft.splits] == [Decimal("1444"), Decimal("3293"), Decimal("12988"), Decimal("21410")]
        assert sum((s.amount for s in draft.splits), Money.zero()) == Money.from_decimal("3057.18")
        assert draft.splits[2].calculation is not None and draft.splits[2].calculation.volume == Decimal("1187")

    def test_gas_invoice_with_prices_per_period(self) -> None:
        draft = read_document(GAS_SPLIT_PRICES.format(base="151,20"), NAMES)

        assert draft.description == "Musterenergie GmbH"
        assert draft.invoice_date == date(2026, 7, 9)  # "09. Juli 2026"
        assert draft.period == DateRange(date(2025, 7, 5), date(2026, 6, 30))
        assert draft.amount == Money.from_decimal("4130.51")  # nicht die 69,68 aus dem Fließtext
        assert (draft.consumption, draft.consumption_unit) == (Decimal("41058"), "kWh")
        # Je Abschnitt der Preis seines Zeitraums - und Beträge auf den Cent wie auf der Rechnung.
        assert [(s.consumption, s.amount, s.calculation.working_price) for s in draft.splits] == [  # type: ignore[union-attr]
            (Decimal("16949"), Money.from_decimal("1754.71"), Decimal("0.0826")),
            (Decimal("24109"), Money.from_decimal("2375.80"), Decimal("0.0797")),
        ]
        assert draft.splits[0].calculation.calorific_value == Decimal("11.630")  # type: ignore[union-attr]

    def test_stated_line_amounts_beat_misread_prices(self) -> None:
        # Die Texterkennung hat "151,20" als "151" gelesen; die Zeilenbeträge stimmen aber.
        draft = read_document(GAS_SPLIT_PRICES.format(base="151"), NAMES, source="ocr")
        assert [s.amount for s in draft.splits] == [Money.from_decimal("1754.71"), Money.from_decimal("2375.80")]

    def test_gas_invoice_from_text_recognition(self) -> None:
        # Typische Lesefehler: "EUR/Jahr" als "ELJR/J-ahr", Prozentzeichen der Steuer verschluckt -
        # und darunter ein Bruttopreis, der nicht als Grundpreis gelten darf.
        scanned = (
            GAS.replace("174,00 EUR/Jahr", "174,00 ELJR/J-ahr")
            .replace("2.569,06 EUR 19 % 488,12 EUR", "2.569,06 EUR 488,12 EUR")
            + "Ihr aktueller Arbeitspreis beträgt 9,90 ct/kWh (brutto) und Ihr Grundpreis 240,00 EUR/Jahr (brutto).\n"
        )
        expected = read_document(GAS, NAMES)
        draft = read_document(scanned, NAMES, source="ocr")

        assert (draft.working_price, draft.base_price_per_year, draft.vat_percent) == (
            Decimal("0.0612"), Decimal("174.00"), Decimal("19"),
        )
        assert [s.amount for s in draft.splits] == [s.amount for s in expected.splits]

    def test_price_change_on_continuation_line(self) -> None:
        text = WATER.replace(
            "Mengenpreis 13.09.25 - 30.09.26 290 m³ 1,9829 Euro / m³ 575,04 Euro\n",
            "Mengenpreis 13.09.25 - 31.12.25 75 m³ 1,938 Euro / m³ 145,35 Euro\n"
            "01.01.26 - 30.09.26 215 m³ 1,9829 Euro / m³ 426,32 Euro\n",
        ).replace("Summe 735,59 Euro", "Summe 732,22 Euro").replace(
            "Umsatzsteuer 7 % von 735,59 Euro 51,49 Euro\nRechnungsbetrag 787,08 Euro",
            "Umsatzsteuer 7 % von 732,22 Euro 51,26 Euro\nRechnungsbetrag 783,48 Euro",
        )
        draft = read_document(text, NAMES)

        assert [s.calculation.working_price for s in draft.splits] == [Decimal("1.938"), Decimal("1.9829")]  # type: ignore[union-attr]
        # Grundpreis 160,55 € nach Tagen (110/383, 273/383) aufgeteilt, wie die Rechnung ihn ausweist.
        assert [s.amount for s in draft.splits] == [Money.from_decimal("204.86"), Money.from_decimal("578.62")]
        assert sum((s.amount for s in draft.splits), Money.zero()) == draft.amount

    def test_no_splits_without_prices(self) -> None:
        text = "\n".join(line for line in WATER.splitlines() if "preis" not in line.lower())
        assert read_document(text, NAMES).splits == []


class TestLearning:
    #: Generische Regeln greifen hier daneben: "Gesamtbetrag" ist der Nettobetrag dieses Absenders.
    ODD = """
    Hausmeisterei Beispiel GmbH
    Rechnungsdatum 03.02.2026
    Leistungszeitraum 01.01.2025 - 31.12.2025
    Gesamtbetrag {net} EUR
    Endsumme inkl. MwSt {gross} EUR
    """

    def test_anchor_points_to_the_best_line(self) -> None:
        lines = split_lines(WATER)
        anchor = learn_anchor(lines, Decimal("787.08"), "number")
        # Nicht die nackte Zeile "787,08 Euro", sondern die mit dem aussagekräftigen Wort.
        assert anchor in (FieldAnchor("rechnungsbetrag # euro", 0), FieldAnchor("trinkwasser # euro", 0))

    def test_correction_is_used_next_time(self, context: AppContext) -> None:
        house = context.properties.save_property(Property(name="Lernhaus", address="Weg 1, 12345 Ort"))
        assert house.id is not None
        first = context.documents.read(self.ODD.format(net="100,00", gross="119,00"), NAMES)
        assert first.amount == Money.from_decimal("100.00")  # allgemeine Regel liegt daneben

        # Der Benutzer korrigiert auf den Bruttobetrag, wählt Kostenart, Beschreibung und Schlüssel - und speichert.
        cost_type = context.cost_types.get_by_name("Hauswart") if any(
            t.name == "Hauswart" for t in context.cost_types.list_types()
        ) else context.cost_types.get_by_name("Grundsteuer")
        from models.cost import Cost

        saved = context.costs.save_cost(
            Cost(house.id, cost_type, Money.from_decimal("119.00"), DateRange.for_year(2025), AllocationKey.AREA,
                 description="Hausmeister", invoice_date=date(2026, 2, 3))
        )
        profile = context.documents.learn(first, saved)
        assert profile is not None and profile.times_used == 1
        assert profile.anchors["amount"] == FieldAnchor("endsumme inkl. mwst # eur", 0)

        # Im nächsten Jahr: andere Zahlen, gleicher Aufbau - jetzt stimmt alles.
        second = context.documents.read(self.ODD.format(net="110,00", gross="130,90"), NAMES)
        assert second.amount == Money.from_decimal("130.90")
        assert "amount" in second.learned_fields
        assert (second.cost_type_id, second.description, second.allocation_key) == (cost_type.id, "Hausmeister", AllocationKey.AREA)

    def test_profiles_survive_in_the_database(self, context: AppContext) -> None:
        from models.cost import Cost

        house = context.properties.save_property(Property(name="Haus", address="Weg 2, 12345 Ort"))
        assert house.id is not None
        draft = context.documents.read(WATER, NAMES)
        water = context.cost_types.get_by_name("Wasserversorgung")
        saved = context.costs.save_cost(
            Cost(house.id, water, draft.amount or Money.zero(), draft.period or DateRange.for_year(2025),
                 AllocationKey.CONSUMPTION, description="Wasser", consumption=Decimal("290"), consumption_unit="m³",
                 splits=draft.splits)
        )
        context.documents.learn(draft, saved)
        context.documents.learn(draft, saved)

        (profile,) = context.documents.profiles()
        assert profile.issuer_key == "ust:DE123456789" and profile.times_used == 2
        assert profile.vat_percent == Decimal("7") and profile.consumption_unit == "m³"
        assert set(profile.anchors) >= {"amount", "period_start", "period_end", "consumption"}
        assert profile.id is not None
        context.documents.forget(profile.id)
        assert context.documents.profiles() == []

    def test_unknown_issuer_learns_nothing(self, context: AppContext) -> None:
        from models.cost import Cost

        draft = context.documents.read("Rechnungsbetrag 50,00 Euro", NAMES)
        assert draft.issuer_key == ""
        cost = Cost(1, context.cost_types.get_by_name("Grundsteuer"), Money(5000), DateRange.for_year(2025), AllocationKey.AREA)
        assert context.documents.learn(draft, cost) is None


class TestDialog:
    def test_dialog_fills_fields_and_learns_on_save(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.cost_dialog import CostDialog

        house = context.properties.save_property(Property(name="Dialoghaus", address="Weg 3, 12345 Ort"))
        assert house.id is not None
        dialog = CostDialog(
            context.costs.save_cost, context.cost_types.list_types(), [], house.id, DateRange.for_year(2025),
            documents=context.documents,
        )
        dialog._apply_draft(context.documents.read(WATER, [t.name for t in context.cost_types.list_types()]))

        assert dialog._selected_type() is not None and dialog._selected_type().name == "Wasserversorgung"  # type: ignore[union-attr]
        assert dialog._amount_spin.money() == Money.from_decimal("787.08")
        assert dialog._consumption_spin.decimal_value() == Decimal("290.000") and dialog._unit_edit.text() == "m³"
        assert len(dialog._splits) == 2 and "2 Splits mit Preisen" in dialog._scan_info.text()

        dialog._description_edit.setText("Wasser Hauptzähler")
        dialog.accept()

        (saved,) = context.costs.list_costs(house.id)
        assert saved.description == "Wasser Hauptzähler" and len(saved.splits) == 2
        (profile,) = context.documents.profiles()
        assert profile.description == "Wasser Hauptzähler"

    def test_scan_button_is_there_without_ai(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.cost_dialog import CostDialog

        dialog = CostDialog(
            context.costs.save_cost, context.cost_types.list_types(), [], 1, DateRange.for_year(2025),
            documents=context.documents,
        )
        assert dialog._scan_available and "lernt aus Ihren Korrekturen" in dialog._scan_info.text()


def _learn_water(context: AppContext) -> int:
    """Liest die Wasserrechnung, speichert sie und lernt daraus; gibt die Profil-ID zurück."""
    from models.cost import Cost

    house = context.properties.save_property(Property(name="Profilhaus", address="Weg 4, 12345 Ort"))
    assert house.id is not None
    draft = context.documents.read(WATER, NAMES)
    saved = context.costs.save_cost(
        Cost(house.id, context.cost_types.get_by_name("Wasserversorgung"), draft.amount or Money.zero(),
             draft.period or DateRange.for_year(2025), AllocationKey.CONSUMPTION, description="Wasser",
             invoice_date=draft.invoice_date, consumption=Decimal("290"), consumption_unit="m³", splits=draft.splits)
    )
    profile = context.documents.learn(draft, saved)
    assert profile is not None and profile.id is not None
    return profile.id


class TestProfilesDialog:
    def test_anchor_is_described_readably(self) -> None:
        assert FieldAnchor("rechnungsbetrag # euro", 0).describe("number") == "„Rechnungsbetrag ‹Zahl› euro“, 1. Zahl der Zeile"
        assert FieldAnchor("zeitraum vom d bis d", 1).describe("date") == "„Zeitraum vom ‹Datum› bis ‹Datum›“, 2. Datum der Zeile"

    def test_lists_profiles_and_their_anchors(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.document_profiles_dialog import DocumentProfilesDialog

        empty = DocumentProfilesDialog(context.documents, context.cost_types.list_types())
        assert not empty._empty_label.isHidden() and not empty._forget_all_button.isEnabled()

        _learn_water(context)
        dialog = DocumentProfilesDialog(context.documents, context.cost_types.list_types())
        assert dialog._empty_label.isHidden()
        assert dialog._table.selected_item() is not None
        assert "USt-IdNr. DE123456789" in dialog._source_label.text() and "7 %" in dialog._source_label.text()
        rows = [dialog._anchor_list.item(i).text() for i in range(dialog._anchor_list.count())]  # type: ignore[union-attr]
        assert any(row.startswith("Rechnungsbetrag: „") for row in rows)
        assert any(row.startswith("Verbrauch: „Gesamtverbrauch ‹Zahl› m³“") for row in rows)

    def test_forget_a_single_anchor(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.document_profiles_dialog import DocumentProfilesDialog

        _learn_water(context)
        dialog = DocumentProfilesDialog(context.documents, context.cost_types.list_types())
        before = dialog._anchor_list.count()
        dialog._anchor_list.setCurrentRow(0)
        assert dialog._forget_anchor_button.isEnabled()
        dialog._forget_anchor()

        assert dialog._anchor_list.count() == before - 1
        (profile,) = context.documents.profiles()
        assert len(profile.anchors) == before - 1 and profile.times_used == 1

    def test_edit_defaults(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.document_profiles_dialog import DocumentProfileEditDialog
        from ui.helpers import select_data

        _learn_water(context)
        (profile,) = context.documents.profiles()
        dialog = DocumentProfileEditDialog(context.documents, profile, context.cost_types.list_types())
        assert dialog._type_combo.currentText() == "Wasserversorgung"
        assert dialog._key_combo.currentData() == AllocationKey.CONSUMPTION
        assert dialog._vat_combo.currentText() == "7 %"
        dialog._name_edit.setText("Wasserwerke")
        dialog._description_edit.setText("Trinkwasser Haus 4")
        select_data(dialog._key_combo, AllocationKey.PERSONS)
        dialog._vat_combo.setCurrentIndex(0)
        dialog.accept()

        (changed,) = context.documents.profiles()
        assert (changed.issuer_name, changed.description, changed.allocation_key, changed.vat_percent) == (
            "Wasserwerke", "Trinkwasser Haus 4", AllocationKey.PERSONS, None,
        )
        assert changed.anchors == profile.anchors  # Fundstellen bleiben
        # Der nächste Beleg übernimmt die geänderten Vorgaben.
        draft = context.documents.read(WATER, NAMES)
        assert (draft.description, draft.allocation_key) == ("Trinkwasser Haus 4", AllocationKey.PERSONS)

    def test_forget_profile_and_everything(self, qt_app: QApplication, context: AppContext, monkeypatch: pytest.MonkeyPatch) -> None:
        import ui.dialogs.document_profiles_dialog as module

        monkeypatch.setattr(module, "confirm", lambda *_args: True)
        _learn_water(context)
        dialog = module.DocumentProfilesDialog(context.documents, context.cost_types.list_types())
        dialog._forget()
        assert context.documents.profiles() == [] and not dialog._empty_label.isHidden()

        _learn_water(context)
        dialog._reload()
        dialog._forget_all()
        assert context.documents.profiles() == []
        assert context.costs.list_costs(context.properties.list_properties()[0].id)  # Rechnungen bleiben


@pytest.mark.skipif("not __import__('integrations.windows_ocr', fromlist=['WindowsOcr']).WindowsOcr.available()")
class TestWindowsOcr:
    """Nur auf Rechnern mit Windows-Texterkennung und deutschem Sprachpaket."""

    def test_reads_an_invoice_image(self) -> None:
        from integrations.windows_ocr import WindowsOcr

        # Fest abgelegtes Bild: Qt rendert im Testmodus (offscreen) ohne Schriften.
        image = (Path(__file__).parent / "data" / "ocr_beleg.png").read_bytes()
        text = WindowsOcr().recognize([image])
        draft = read_document(text, NAMES, source="ocr")

        assert draft.invoice_date == date(2026, 10, 15)
        assert draft.amount == Money.from_decimal("787.08")
        assert (draft.consumption, draft.consumption_unit) == (Decimal("290"), "m³")  # "rn3" repariert
