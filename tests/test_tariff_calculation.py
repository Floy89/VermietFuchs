"""Tests für die Berechnung von Verbrauch und Kosten je Abschnitt aus Volumen und Preisen.

Grundlage ist eine erfundene Gasrechnung 01.06.2024–31.05.2025, aufgebaut wie
die Rechnungen der Versorger: vier Ablesezeiträume mit Abrechnungsvolumen,
Zustandszahl und Brennwert, ein Arbeitspreis, ein Grundpreis je Jahr und 19 %
Umsatzsteuer. Die Rechnung weist 39135 kWh und 3.056,85 € brutto aus - aber
weder Verbrauch noch Kosten für das Kalenderjahr 2025 allein.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from models.cost import Cost, InvoiceSplit
from models.enums import AllocationKey
from models.property import Property
from models.tariff import SplitCalculation, fit_amounts, year_fraction
from models.value_objects import DateRange, Money
from ui.dialogs.splits_dialog import (
    _COL_BASE_PRICE,
    _COL_CALORIFIC,
    _COL_CONDITION,
    _COL_CONSUMPTION,
    _COL_VOLUME,
    _COL_WORKING_PRICE,
    SplitsDialog,
)

WORKING_PRICE = Decimal("0.0612")
BASE_PRICE = Decimal("174.00")
VAT = Decimal(19)
INVOICE_PERIOD = DateRange(date(2024, 6, 1), date(2025, 5, 31))
INVOICE_AMOUNT = Money.from_decimal("3056.85")

#: (von, bis, Abrechnungsvolumen m³, Zustandszahl, Brennwert, erwartete kWh, erwarteter Bruttobetrag)
ROWS = [
    (date(2024, 6, 1), date(2024, 6, 30), "132", "0.9581", "11.420", "1444", "122.13"),
    (date(2024, 7, 1), date(2024, 9, 30), "301", "0.9581", "11.420", "3293", "291.87"),
    (date(2024, 10, 1), date(2024, 12, 31), "1187", "0.9581", "11.420", "12988", "997.95"),
    (date(2025, 1, 1), date(2025, 5, 31), "1954", "0.9581", "11.436", "21410", "1644.90"),
]


def _calculation(volume: str, condition: str, calorific: str) -> SplitCalculation:
    return SplitCalculation(Decimal(volume), Decimal(condition), Decimal(calorific), WORKING_PRICE, BASE_PRICE, VAT)


def _splits() -> list[InvoiceSplit]:
    return [
        InvoiceSplit(DateRange(start, end), Money.from_decimal(amount), Decimal(kwh), _calculation(volume, z, h))
        for start, end, volume, z, h, kwh, amount in ROWS
    ]


class TestCalculation:
    def test_energy_from_volume_matches_the_invoice(self) -> None:
        energies = [_calculation(volume, z, h).energy() for _s, _e, volume, z, h, _k, _a in ROWS]

        assert energies == [Decimal(kwh) for *_rest, kwh, _amount in ROWS]
        assert sum(energies, Decimal(0)) == Decimal("39135")  # type: ignore[arg-type]

    def test_base_price_is_charged_per_day_of_each_calendar_year(self) -> None:
        # 2024 ist ein Schaltjahr: 214 von 366 Tagen; 2025: 151 von 365 Tagen.
        part_2024 = BASE_PRICE * year_fraction(DateRange(date(2024, 6, 1), date(2024, 12, 31)))
        part_2025 = BASE_PRICE * year_fraction(DateRange(date(2025, 1, 1), date(2025, 5, 31)))

        assert round(part_2024, 2) == Decimal("101.74")
        assert round(part_2025, 2) == Decimal("71.98")
        assert year_fraction(DateRange.for_year(2024)) == year_fraction(DateRange.for_year(2025)) == 1
        assert year_fraction(INVOICE_PERIOD) == Decimal(214) / 366 + Decimal(151) / 365

    def test_costs_per_split_add_up_to_the_invoice(self) -> None:
        exact = [
            _calculation(volume, z, h).gross_cost(DateRange(start, end), Decimal(kwh))
            for start, end, volume, z, h, kwh, _amount in ROWS
        ]

        amounts = fit_amounts([e for e in exact if e is not None], INVOICE_AMOUNT)

        assert amounts == [Money.from_decimal(amount) for *_rest, amount in ROWS]
        assert sum(amounts, Money.zero()) == INVOICE_AMOUNT

    def test_small_rounding_difference_is_spread_over_the_splits(self) -> None:
        exact = [Decimal("100.004"), Decimal("200.004"), Decimal("300.004")]  # zusammen 600,012

        # Der Versorger hat anders gerundet und weist 600,03 aus.
        fitted = fit_amounts(exact, Money.from_decimal("600.03"))

        assert sum(fitted, Money.zero()) == Money.from_decimal("600.03")
        assert all(abs(f.cents - round(e * 100)) <= 1 for f, e in zip(fitted, exact))

    def test_large_difference_is_not_hidden(self) -> None:
        exact = [Decimal("100.00"), Decimal("200.00")]

        # 50 € Abweichung ist keine Rundung: Die Beträge bleiben, wie berechnet.
        assert fit_amounts(exact, Money.from_decimal("350.00")) == [Money(10000), Money(20000)]
        assert fit_amounts(exact, None) == [Money(10000), Money(20000)]

    def test_incomplete_inputs_calculate_nothing(self) -> None:
        only_volume = SplitCalculation(volume=Decimal("100"))
        assert only_volume.energy() is None and not only_volume.has_volume
        assert only_volume.gross_cost(INVOICE_PERIOD, Decimal("10")) is None
        assert SplitCalculation().is_empty and not only_volume.is_empty

    def test_base_price_only_and_working_price_only(self) -> None:
        year = DateRange.for_year(2025)
        base_only = SplitCalculation(base_price=Decimal("120"), vat_percent=Decimal(7))
        working_only = SplitCalculation(working_price=Decimal("2.50"))

        assert base_only.gross_cost(year, None) == Decimal("128.40")
        assert working_only.gross_cost(year, Decimal("100")) == Decimal("250.00")


class TestBasePricePer365Days:
    """Manche Versorger rechnen den Grundpreis starr mit 1/365 je Tag - auch im Schaltjahr."""

    #: Gelsenwasser 2024/25: "153,00 Euro / 365 Tage", Abschnitt 14.09.-31.12.2024 (109 Tage).
    PERIOD = DateRange(date(2024, 9, 14), date(2024, 12, 31))

    def test_calendar_exact_and_fixed_days(self) -> None:
        calendar = SplitCalculation(working_price=Decimal("1.938"), base_price=Decimal("153.00"), vat_percent=Decimal(7))
        fixed = SplitCalculation(
            working_price=Decimal("1.938"), base_price=Decimal("153.00"), vat_percent=Decimal(7),
            base_price_days=Decimal(365),
        )
        assert calendar.net_parts(self.PERIOD, Decimal(85)) == (Decimal("164.73"), Decimal("45.57"))  # 109/366
        assert fixed.net_parts(self.PERIOD, Decimal(85)) == (Decimal("164.73"), Decimal("45.69"))  # 109/365 wie auf der Rechnung
        assert fit_amounts([fixed.gross_cost(self.PERIOD, Decimal(85)) or Decimal(0)], None) == [Money.from_decimal("225.15")]

    def test_setting_is_saved_with_the_split(self, context: AppContext) -> None:
        house = context.properties.save_property(Property(name="Wasserhaus", address="Weg 9, 12345 Ort"))
        assert house.id is not None
        calc = SplitCalculation(working_price=Decimal("1.938"), base_price=Decimal("153"), base_price_days=Decimal(365))
        context.costs.save_cost(
            Cost(house.id, context.cost_types.get_by_name("Wasserversorgung"), Money.from_decimal("225.15"), self.PERIOD,
                 AllocationKey.CONSUMPTION, consumption=Decimal(85), consumption_unit="m³",
                 splits=[InvoiceSplit(self.PERIOD, Money.from_decimal("225.15"), Decimal(85), calc)])
        )
        (stored,) = context.costs.list_costs(house.id)
        assert stored.splits[0].calculation is not None and stored.splits[0].calculation.base_price_days == Decimal(365)

    def test_checkbox_in_the_dialog(self, qt_app: QApplication) -> None:
        split = InvoiceSplit(
            self.PERIOD, Money.zero(), Decimal(85),
            SplitCalculation(working_price=Decimal("1.938"), base_price=Decimal("153.00"), vat_percent=Decimal(7)),
        )
        dialog = SplitsDialog(self.PERIOD, Money.from_decimal("225.15"), Decimal(85), "m³", [split])
        assert not dialog._fixed_days_check.isChecked()
        assert dialog._amount_spin(0).money() == Money.from_decimal("225.02")  # kalendergenau: 13 Cent weniger

        dialog._fixed_days_check.setChecked(True)

        assert dialog._amount_spin(0).money() == Money.from_decimal("225.15")
        assert dialog._check() is None
        assert dialog._read_splits()[0].calculation.base_price_days == Decimal(365)  # type: ignore[union-attr]
        # Beim erneuten Öffnen ist der Haken wieder gesetzt.
        again = SplitsDialog(self.PERIOD, Money.from_decimal("225.15"), Decimal(85), "m³", dialog._read_splits())
        assert again._fixed_days_check.isChecked()


class TestInBilling:
    def _invoice(self, context: AppContext) -> Cost:
        house = context.properties.save_property(Property(name="Gashaus", address="Am Brenner 1, 12345 Heizstadt"))
        assert house.id is not None
        return context.costs.save_cost(
            Cost(
                house.id,
                context.cost_types.get_by_name("Brennstoffkosten (Gas/Öl)"),
                INVOICE_AMOUNT,
                INVOICE_PERIOD,
                AllocationKey.CONSUMPTION_AREA,
                description="Gasversorger",
                consumption=Decimal("39135"),
                consumption_unit="kWh",
                splits=_splits(),
            )
        )

    def test_calendar_year_2025_gets_exactly_the_last_split(self, context: AppContext) -> None:
        invoice = self._invoice(context)

        part_2025 = invoice.prorate(DateRange.for_year(2025))
        part_2024 = invoice.prorate(DateRange.for_year(2024))

        assert part_2025 is not None and part_2024 is not None
        assert (part_2025.amount, part_2025.consumption) == (Money.from_decimal("1644.90"), Decimal("21410"))
        assert (part_2024.amount, part_2024.consumption) == (Money.from_decimal("1411.95"), Decimal("17725"))
        assert part_2024.amount + part_2025.amount == INVOICE_AMOUNT

    def test_calculation_inputs_survive_saving(self, context: AppContext) -> None:
        invoice = self._invoice(context)

        (stored,) = context.costs.list_costs(invoice.property_id)

        assert stored.splits == _splits()
        last = stored.splits[-1].calculation
        assert last is not None
        assert (last.volume, last.calorific_value, last.working_price) == (
            Decimal("1954"), Decimal("11.436"), WORKING_PRICE,
        )

    def test_splits_without_calculation_still_load(self, context: AppContext) -> None:
        # So sehen Splits aus, die vor dieser Funktion gespeichert wurden.
        invoice = self._invoice(context)
        context.database.execute(
            "UPDATE costs SET splits = ? WHERE cost_id = ?",
            ('[{"from": "2024-06-01", "to": "2025-05-31", "amount": 305685, "consumption": "39135"}]', invoice.id),
        )

        (stored,) = context.costs.list_costs(invoice.property_id)

        assert stored.splits[0].calculation is None

    def test_single_split_over_whole_period_behaves_like_no_split(self, context: AppContext) -> None:
        whole = InvoiceSplit(
            INVOICE_PERIOD, INVOICE_AMOUNT, Decimal("39135"),
            SplitCalculation(working_price=WORKING_PRICE, base_price=BASE_PRICE, vat_percent=VAT),
        )
        with_split = Cost(1, self._invoice(context).cost_type, INVOICE_AMOUNT, INVOICE_PERIOD, AllocationKey.CONSUMPTION_AREA,
                          consumption=Decimal("39135"), consumption_unit="kWh", splits=[whole], id=7)
        without = Cost(1, with_split.cost_type, INVOICE_AMOUNT, INVOICE_PERIOD, AllocationKey.CONSUMPTION_AREA,
                       consumption=Decimal("39135"), consumption_unit="kWh", id=7)

        (piece,) = with_split.pieces(DateRange.for_year(2025))
        (plain,) = without.pieces(DateRange.for_year(2025))

        # Taggenau 151 von 365 Tagen - und nicht als "Abschnitt" gekennzeichnet.
        assert (piece.amount, piece.consumption, piece.period) == (plain.amount, plain.consumption, plain.period)
        assert piece.split_period is None


class TestDialog:
    def _fill(self, dialog: SplitsDialog) -> None:
        dialog._working_price_spin.set_decimal(WORKING_PRICE)
        dialog._base_price_spin.set_decimal(BASE_PRICE)
        for index, (start, end, volume, z, h, _kwh, _amount) in enumerate(ROWS):
            dialog._add_row(InvoiceSplit(DateRange(start, end), Money.zero()))
            dialog._spin(index, _COL_VOLUME).set_decimal(Decimal(volume))
            dialog._spin(index, _COL_CONDITION).set_decimal(Decimal(z))
            dialog._spin(index, _COL_CALORIFIC).set_decimal(Decimal(h))
        dialog._apply_prices_to_all()

    def test_entering_volume_and_prices_reproduces_the_invoice(self, qt_app: QApplication) -> None:
        dialog = SplitsDialog(INVOICE_PERIOD, INVOICE_AMOUNT, Decimal("39135"), "kWh", [])

        self._fill(dialog)

        assert dialog._check() is None, dialog._status.text()
        assert dialog._status.text().startswith("✓")
        dialog._accept_if_valid()
        assert [(s.consumption, s.amount) for s in dialog.splits] == [
            (Decimal(kwh), Money.from_decimal(amount)) for *_rest, kwh, amount in ROWS
        ]
        assert dialog.splits == _splits()
        # Berechnete Felder sind gesperrt.
        assert dialog._spin(0, _COL_CONSUMPTION).isReadOnly() and dialog._amount_spin(0).isReadOnly()

    def test_totals_can_be_taken_from_the_calculation(self, qt_app: QApplication) -> None:
        # Der Benutzer hat weder Gesamtbetrag noch Gesamtverbrauch eingetragen.
        dialog = SplitsDialog(INVOICE_PERIOD, Money.zero(), None, "kWh", [])
        self._fill(dialog)
        assert dialog._check() is not None

        dialog._adopt_sums()

        assert dialog._check() is None, dialog._status.text()
        # Ohne Rechnungsbetrag wird jede Zeile für sich gerundet - mit Arbeits- und
        # Grundpreisanteil je auf Cent (wie beim Versorger) trifft das die Rechnung genau.
        assert dialog.invoice_amount == INVOICE_AMOUNT
        assert dialog.invoice_consumption == Decimal("39135")

    def test_existing_calculation_is_shown_again(self, qt_app: QApplication) -> None:
        dialog = SplitsDialog(INVOICE_PERIOD, INVOICE_AMOUNT, Decimal("39135"), "kWh", _splits())

        assert dialog._working_price_spin.decimal_value() == WORKING_PRICE
        assert dialog._vat_spin.decimal_value() == VAT
        assert dialog._spin(3, _COL_VOLUME).decimal_value() == Decimal("1954")
        assert dialog._spin(3, _COL_WORKING_PRICE).decimal_value() == WORKING_PRICE
        assert dialog._spin(3, _COL_BASE_PRICE).decimal_value() == BASE_PRICE
        assert dialog._check() is None

    def test_manual_entry_without_calculation_still_works(self, qt_app: QApplication) -> None:
        manual = [
            InvoiceSplit(DateRange(date(2024, 6, 1), date(2024, 12, 31)), Money.from_decimal("1411.94"), Decimal("17725")),
            InvoiceSplit(DateRange(date(2025, 1, 1), date(2025, 5, 31)), Money.from_decimal("1644.91"), Decimal("21410")),
        ]
        dialog = SplitsDialog(INVOICE_PERIOD, INVOICE_AMOUNT, Decimal("39135"), "kWh", manual)

        assert dialog._check() is None
        assert not dialog._amount_spin(0).isReadOnly()
        dialog._accept_if_valid()
        assert dialog.splits == manual  # keine Rechengrundlagen -> calculation bleibt None


class TestWaterInvoice:
    """Trinkwasser: Preis je m³, Grundpreis je Jahr, 7 % Umsatzsteuer - ohne Gasumrechnung.

    Aufbau wie eine echte Wasserrechnung 13.09.2025–30.09.2026 mit Zwischenablesung
    zum 31.12.2025 (Zahlen erfunden): 290 m³ zu 1,9829 €, Grundpreis 153 € je Jahr.
    """

    PERIOD = DateRange(date(2025, 9, 13), date(2026, 9, 30))
    TOTAL = Money.from_decimal("787.08")

    def _splits(self, end_2025: date, vat: str) -> list[InvoiceSplit]:
        prices = {"working_price": Decimal("1.9829"), "base_price": Decimal("153"), "vat_percent": Decimal(vat)}
        return [
            InvoiceSplit(DateRange(date(2025, 9, 13), end_2025), Money.zero(), Decimal("75"), SplitCalculation(**prices)),
            InvoiceSplit(DateRange(date(2026, 1, 1), date(2026, 9, 30)), Money.zero(), Decimal("215"), SplitCalculation(**prices)),
        ]

    def test_base_price_is_charged_per_day_and_reproduces_the_invoice(self, qt_app: QApplication) -> None:
        dialog = SplitsDialog(self.PERIOD, self.TOTAL, Decimal("290"), "m³", self._splits(date(2025, 12, 31), "7"))

        assert dialog._check() is None, dialog._status.text()
        # 110 Tage 2025 und 273 Tage 2026 - zusammen die 383 Tage der Rechnung.
        assert [dialog._amount_spin(row).money() for row in range(2)] == [
            Money.from_decimal("208.47"), Money.from_decimal("578.61"),
        ]

    def test_wrong_vat_and_a_missing_day_are_reported(self, qt_app: QApplication) -> None:
        dialog = SplitsDialog(self.PERIOD, self.TOTAL, Decimal("290"), "m³", self._splits(date(2025, 12, 30), "19"))

        status = dialog._status.text()
        assert dialog._check() is not None
        assert "Umsatzsteuersatz" in status
        # Der fehlende 31.12. wird genannt, obwohl die Summe ohnehin nicht stimmt.
        assert "Nicht abgedeckt: 31.12.2025" in status

    def test_gas_columns_only_where_they_make_sense(self, qt_app: QApplication) -> None:
        water = SplitsDialog(self.PERIOD, self.TOTAL, Decimal("290"), "m³", [])
        gas = SplitsDialog(INVOICE_PERIOD, INVOICE_AMOUNT, Decimal("39135"), "kWh", [])
        gas_without_unit = SplitsDialog(INVOICE_PERIOD, INVOICE_AMOUNT, None, "", _splits())

        assert all(water._table.isColumnHidden(c) for c in (_COL_VOLUME, _COL_CONDITION, _COL_CALORIFIC))
        assert not any(gas._table.isColumnHidden(c) for c in (_COL_VOLUME, _COL_CONDITION, _COL_CALORIFIC))
        # Vorhandene Volumenangaben bleiben sichtbar, auch wenn die Einheit fehlt.
        assert not gas_without_unit._table.isColumnHidden(_COL_VOLUME)

    def test_vat_suggestion(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.cost_dialog import CostDialog

        types = context.cost_types.list_types()
        water = context.cost_types.get_by_name("Wasserversorgung")
        gas = context.cost_types.get_by_name("Brennstoffkosten (Gas/Öl)")

        def dialog_for(cost_type: object, lookup: object = None) -> CostDialog:
            dialog = CostDialog(context.costs.save_cost, types, [], 1, self.PERIOD, vat_lookup=lookup)  # type: ignore[arg-type]
            dialog._type_combo.setCurrentIndex(dialog._type_combo.findText(cost_type.name))  # type: ignore[attr-defined]
            return dialog

        assert dialog_for(water)._default_vat() == Decimal(7)
        assert dialog_for(gas)._default_vat() == Decimal(19)
        # Ein bei einer früheren Rechnung eingetragener Satz hat Vorrang.
        assert dialog_for(water, lambda _type_id: Decimal("0"))._default_vat() == Decimal(0)
        dialog = SplitsDialog(self.PERIOD, self.TOTAL, Decimal("290"), "m³", [], default_vat=Decimal(7))
        assert dialog._vat_spin.decimal_value() == Decimal(7)
