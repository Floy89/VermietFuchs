"""CO2-Angaben je Rechnung (CO2-Kostenaufteilungsgesetz) für die Meldung an den Messdienst.

Die Belegtexte sind erfunden, aufgebaut wie zwei übliche Formen auf Gasrechnungen:
eine Tabelle je Zeitraum mit "Gesamtkosten" und ein Absatz mit den Pflichtangaben.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal

import pytest
from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from models.cost import CO2_PRICE_EUR_PER_T, CO2_VAT_RATE, Cost, InvoiceSplit
from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from models.exceptions import ValidationError
from models.property import Property
from models.value_objects import DateRange, Money
from services.document_reader import co2_rows, find_co2, read_document, split_lines

#: CO2-Tabelle je Zeitraum (Brennwert-Emissionsfaktor, Energiegehalt, Emission, Zertifikatspreis, Kosten brutto).
TABLE = """
Weiterführende Informationen zum CO2-Kostenaufteilungsgesetz: https://www.beispiel-energie.de/co2
Rechnungsbetrag 4.130,51 €
Information gemäß CO2-Kostenaufteilungsgesetz
Zeitraum Brennwertemissionsfaktor Energiegehalt Brennstoffemission Zertifikatspreis USt Kosten Brutto
05.07.2025 - 31.12.2025 0,18139 kg/kWh 16.949 kWh 3.074,38 kg 55,00 €/t 19% 201,22 €
01.01.2026 - 30.06.2026 0,18139 kg/kWh 24.109 kWh 4.373,13 kg 60,00 €/t 19% 312,24 €
Gesamtkosten 513,46 €
"""

#: Pflichtangaben als Absatz - so, wie sie die Texterkennung liest ("C02" mit Null).
PARAGRAPH = """
Konzessionsabgabe , 447,00 EUR Emissionszertifikate, 0,00 EUR Bilanzierungsumlage
Informationen zum C02-Kostenaufteilungsgesetz
Brennstoffemissionen der Erdgaslieferung in 7.438,44 kg/C02
Preisbestandteile C02-Kosten (inkl. Umsatzsteuer) 447,00 EUR
Heizwertbezogener Emissionsfaktor 0,1813900 kg C02
"""


class TestReading:
    def test_table_with_total(self) -> None:
        assert find_co2(split_lines(TABLE)) == (Money.from_decimal("513.46"), Decimal("7447.51"), Decimal("0.18139"))

    def test_paragraph_from_text_recognition(self) -> None:
        assert find_co2(split_lines(PARAGRAPH)) == (Money.from_decimal("447.00"), Decimal("7438.44"), Decimal("0.1813900"))

    def test_rows_per_period(self) -> None:
        assert co2_rows(split_lines(TABLE)) == [
            (DateRange(date(2025, 7, 5), date(2025, 12, 31)), Money.from_decimal("201.22"), Decimal("3074.38")),
            (DateRange(date(2026, 1, 1), date(2026, 6, 30)), Money.from_decimal("312.24"), Decimal("4373.13")),
        ]

    def test_no_co2_section(self) -> None:
        assert find_co2(split_lines("Trinkwasser\nRechnungsbetrag 787,08 Euro")) == (None, None, None)

    def test_draft_carries_the_values(self) -> None:
        draft = read_document(TABLE, ["Brennstoffkosten (Gas/Öl)"])
        assert (draft.co2_cost, draft.co2_emission) == (Money.from_decimal("513.46"), Decimal("7447.51"))


def _gas(context: AppContext, house_id: int, **values: object) -> Cost:
    return Cost(
        house_id, context.cost_types.get_by_name("Brennstoffkosten (Gas/Öl)"), Money.from_decimal("4130.51"),
        DateRange(date(2025, 7, 5), date(2026, 6, 30)), AllocationKey.CONSUMPTION_AREA,
        consumption=Decimal("41058"), consumption_unit="kWh", **values,  # type: ignore[arg-type]
    )


class TestInvoices:
    def test_values_are_saved(self, context: AppContext) -> None:
        house = context.properties.save_property(Property(name="Gashaus", address="Weg 1, 12345 Ort"))
        assert house.id is not None
        context.costs.save_cost(
            _gas(context, house.id, co2_cost=Money.from_decimal("513.46"), co2_emission=Decimal("7447.51"),
                 co2_factor=Decimal("0.18139"))
        )
        (stored,) = context.costs.list_costs(house.id)
        assert (stored.co2_cost, stored.co2_emission, stored.co2_factor) == (
            Money.from_decimal("513.46"), Decimal("7447.51"), Decimal("0.18139"),
        )

    def test_negative_values_are_rejected(self, context: AppContext) -> None:
        with pytest.raises(ValidationError, match="CO2"):
            _gas(context, 1, co2_cost=Money(-1)).validate()

    def test_year_share_follows_consumption(self, context: AppContext) -> None:
        # Splits: 2025 16.949 kWh, 2026 24.109 kWh - CO2 nach kWh, nicht nach Tagen oder Betrag.
        house = context.properties.save_property(Property(name="Gashaus", address="Weg 2, 12345 Ort"))
        assert house.id is not None
        splits = [
            InvoiceSplit(DateRange(date(2025, 7, 5), date(2025, 12, 31)), Money.from_decimal("1754.71"), Decimal("16949")),
            InvoiceSplit(DateRange(date(2026, 1, 1), date(2026, 6, 30)), Money.from_decimal("2375.80"), Decimal("24109")),
        ]
        context.costs.save_cost(
            _gas(context, house.id, co2_cost=Money.from_decimal("513.46"), co2_emission=Decimal("7447.51"), splits=splits)
        )

        (group_2025,) = context.invoice_groups.groups(house.id, 2025)
        (group_2026,) = context.invoice_groups.groups(house.id, 2026)

        # 7447,51 × 16949 / 41058 = 3074,38 kg - genau, was die Rechnung für 2025 ausweist.
        assert group_2025.total_co2_emission == Decimal("3074.38")
        assert group_2025.total_co2_cost is not None and group_2026.total_co2_cost is not None
        assert group_2025.total_co2_cost + group_2026.total_co2_cost == Money.from_decimal("513.46")
        assert group_2025.total_co2_emission + (group_2026.total_co2_emission or 0) == Decimal("7447.51")
        assert group_2025.co2_missing == 0

    def test_co2_per_split_beats_the_kwh_share(self, context: AppContext) -> None:
        # Der CO2-Preis steigt zum Jahreswechsel (55 -> 60 €/t): Kosten je Jahr laut Rechnung, nicht nach kWh.
        house = context.properties.save_property(Property(name="Gashaus", address="Weg 5, 12345 Ort"))
        assert house.id is not None
        splits = [
            InvoiceSplit(DateRange(date(2025, 7, 5), date(2025, 12, 31)), Money.from_decimal("1754.71"), Decimal("16949"),
                         co2_cost=Money.from_decimal("201.22"), co2_emission=Decimal("3074.38")),
            InvoiceSplit(DateRange(date(2026, 1, 1), date(2026, 6, 30)), Money.from_decimal("2375.80"), Decimal("24109"),
                         co2_cost=Money.from_decimal("312.24"), co2_emission=Decimal("4373.13")),
        ]
        context.costs.save_cost(
            _gas(context, house.id, co2_cost=Money.from_decimal("513.46"), co2_emission=Decimal("7447.51"), splits=splits)
        )
        (stored,) = context.costs.list_costs(house.id)
        assert stored.splits[0].co2_cost == Money.from_decimal("201.22")

        (group_2025,) = context.invoice_groups.groups(house.id, 2025)
        assert (group_2025.total_co2_cost, group_2025.total_co2_emission) == (
            Money.from_decimal("201.22"), Decimal("3074.38"),
        )

    def test_incomplete_split_co2_falls_back_to_kwh(self, context: AppContext) -> None:
        house = context.properties.save_property(Property(name="Gashaus", address="Weg 6, 12345 Ort"))
        assert house.id is not None
        splits = [
            InvoiceSplit(DateRange(date(2025, 7, 5), date(2025, 12, 31)), Money.from_decimal("1754.71"), Decimal("16949"),
                         co2_cost=Money.from_decimal("201.22")),
            InvoiceSplit(DateRange(date(2026, 1, 1), date(2026, 6, 30)), Money.from_decimal("2375.80"), Decimal("24109")),
        ]
        context.costs.save_cost(_gas(context, house.id, co2_cost=Money.from_decimal("513.46"), splits=splits))
        (group_2025,) = context.invoice_groups.groups(house.id, 2025)
        assert group_2025.total_co2_cost == Money.from_decimal("211.96")  # 513,46 × 16949 / 41058

    def test_year_sum_over_several_invoices(self, context: AppContext) -> None:
        house = context.properties.save_property(Property(name="Gashaus", address="Weg 3, 12345 Ort"))
        assert house.id is not None
        gas = context.cost_types.get_by_name("Brennstoffkosten (Gas/Öl)")
        for period, amount, kwh, co2, kg in (
            (DateRange(date(2025, 1, 1), date(2025, 6, 30)), "1500.00", "20000", "250.00", "3600.00"),
            (DateRange(date(2025, 7, 1), date(2025, 12, 31)), "1000.00", "12000", "160.00", "2200.00"),
            (DateRange(date(2025, 3, 1), date(2025, 3, 31)), "50.00", "500", None, None),  # ohne CO2-Angaben
        ):
            context.costs.save_cost(
                Cost(house.id, gas, Money.from_decimal(amount), period, AllocationKey.CONSUMPTION_AREA,
                     consumption=Decimal(kwh), consumption_unit="kWh",
                     co2_cost=Money.from_decimal(co2) if co2 else None, co2_emission=Decimal(kg) if kg else None)
            )
        (group,) = context.invoice_groups.groups(house.id, 2025)
        assert (group.total_co2_cost, group.total_co2_emission) == (Money.from_decimal("410.00"), Decimal("5800.00"))
        assert group.co2_missing == 1


class TestDialog:
    def _dialog(self, context: AppContext, type_name: str, cost: Cost | None = None):  # type: ignore[no-untyped-def]
        from ui.dialogs.cost_dialog import CostDialog

        types = context.cost_types.list_types()
        dialog = CostDialog(context.costs.save_cost, types, [], cost.property_id if cost else 1,
                            DateRange.for_year(2025), cost=cost)
        dialog._type_combo.setCurrentIndex(next(i for i, t in enumerate(types) if t.name == type_name))
        return dialog

    def test_only_for_heating_costs(self, qt_app: QApplication, context: AppContext) -> None:
        gas = self._dialog(context, "Brennstoffkosten (Gas/Öl)")
        assert gas.form.isRowVisible(gas._co2_row)
        water = self._dialog(context, "Wasserversorgung")
        assert not water.form.isRowVisible(water._co2_row)

    def test_entering_and_saving(self, qt_app: QApplication, context: AppContext) -> None:
        house = context.properties.save_property(Property(name="Dialoghaus", address="Weg 4, 12345 Ort"))
        assert house.id is not None
        saved = context.costs.save_cost(_gas(context, house.id))
        dialog = self._dialog(context, "Brennstoffkosten (Gas/Öl)", saved)
        dialog._co2_cost_spin.set_money(Money.from_decimal("513.46"))
        dialog._co2_emission_spin.set_decimal(Decimal("7447.51"))
        dialog.accept()

        (stored,) = context.costs.list_costs(house.id)
        assert (stored.co2_cost, stored.co2_emission, stored.co2_factor) == (
            Money.from_decimal("513.46"), Decimal("7447.51"), None,
        )
        again = self._dialog(context, "Brennstoffkosten (Gas/Öl)", stored)
        assert again._co2_cost_spin.money() == Money.from_decimal("513.46")

    def test_split_dialog_columns(self, qt_app: QApplication) -> None:
        from ui.dialogs.splits_dialog import _COL_CO2_COST, _COL_CO2_EMISSION, SplitsDialog

        period = DateRange(date(2025, 7, 5), date(2026, 6, 30))
        splits = [
            InvoiceSplit(DateRange(date(2025, 7, 5), date(2025, 12, 31)), Money.from_decimal("1754.71"), Decimal("16949"),
                         co2_cost=Money.from_decimal("201.22"), co2_emission=Decimal("3074.38")),
            InvoiceSplit(DateRange(date(2026, 1, 1), date(2026, 6, 30)), Money.from_decimal("2375.80"), Decimal("24109")),
        ]
        dialog = SplitsDialog(period, Money.from_decimal("4130.51"), Decimal("41058"), "kWh", splits)
        assert not dialog._table.isColumnHidden(_COL_CO2_COST)
        assert "unvollständig" in dialog._status.text()

        dialog._table.cellWidget(1, _COL_CO2_COST).set_money(Money.from_decimal("312.24"))  # type: ignore[union-attr]
        dialog._table.cellWidget(1, _COL_CO2_EMISSION).set_decimal(Decimal("4373.13"))  # type: ignore[union-attr]

        assert [s.co2_cost for s in dialog._read_splits()] == [Money.from_decimal("201.22"), Money.from_decimal("312.24")]
        assert "CO2 der Splits: 513,46 €" in dialog._status.text() and "unvollständig" not in dialog._status.text()
        water = SplitsDialog(period, Money.from_decimal("100.00"), Decimal("10"), "m³", [])
        assert water._table.isColumnHidden(_COL_CO2_COST)

    def test_read_values_are_filled_in(self, qt_app: QApplication, context: AppContext) -> None:
        dialog = self._dialog(context, "Brennstoffkosten (Gas/Öl)")
        dialog._apply_draft(read_document(TABLE, [t.name for t in context.cost_types.list_types()]))
        assert dialog._co2_cost_spin.money() == Money.from_decimal("513.46")
        assert dialog._co2_emission_spin.decimal_value() == Decimal("7447.51")
        assert dialog._co2_factor_spin.decimal_value() == Decimal("0.1813900")
        assert "CO2-Angaben" in dialog._scan_info.text()


# ---------------------------------------------------------------------- #
# CO2-Preis je Jahr bei Rechnungen ohne CO2-Angabe je Zeitraum
# (echte Werte der Gasrechnungen 2024-2026)
# ---------------------------------------------------------------------- #
GAS = CostType("Brennstoffkosten (Gas/Öl)", AllocationKey.CONSUMPTION_AREA, MeterType.HEATING, heating_rule=True, id=33)
FACTOR = Decimal("0.1813900")


def _split(start: date, end: date, amount: str, kwh: str, **co2: object) -> InvoiceSplit:
    return InvoiceSplit(DateRange(start, end), Money.from_decimal(amount), Decimal(kwh), **co2)  # type: ignore[arg-type]


def _gruenwelt_annual() -> Cost:
    """Grünwelt 01.06.2024–31.05.2025: CO2 nur als Summe (7.438,44 kg / 447,00 €), Mischpreis 45/55 €/t."""
    return Cost(
        1, GAS, Money.from_decimal("3114.15"), DateRange(date(2024, 6, 1), date(2025, 5, 31)),
        AllocationKey.CONSUMPTION_AREA, consumption=Decimal("41008"), consumption_unit="kWh",
        co2_cost=Money.from_decimal("447.00"), co2_emission=Decimal("7438.44"), co2_factor=FACTOR,
        splits=[
            _split(date(2024, 6, 1), date(2024, 6, 30), "108.23", "1292"),
            _split(date(2024, 7, 1), date(2024, 9, 30), "275.10", "3163"),
            _split(date(2024, 10, 1), date(2024, 12, 31), "1046.03", "14011"),
            _split(date(2025, 1, 1), date(2025, 5, 31), "1684.79", "22542"),
        ],
        id=44,
    )


def _gruenwelt_final() -> Cost:
    """Grünwelt-Schlussrechnung 01.06.–04.07.2025 mit eigener Angabe 111,55 kg / 7,31 €."""
    return Cost(
        1, GAS, Money.from_decimal("92.31"), DateRange(date(2025, 6, 1), date(2025, 7, 4)),
        AllocationKey.CONSUMPTION_AREA, consumption=Decimal("615"), consumption_unit="kWh",
        co2_cost=Money.from_decimal("7.31"), co2_emission=Decimal("111.55"), co2_factor=FACTOR,
        splits=[
            _split(date(2025, 6, 1), date(2025, 6, 30), "84.48", "570"),
            _split(date(2025, 7, 1), date(2025, 7, 4), "7.83", "45"),
        ],
        id=43,
    )


def _maingau() -> Cost:
    """Maingau 05.07.2025–30.06.2026 mit CO2-Angaben je Zeitraum."""
    return Cost(
        1, GAS, Money.from_decimal("4130.51"), DateRange(date(2025, 7, 5), date(2026, 6, 30)),
        AllocationKey.CONSUMPTION_AREA, consumption=Decimal("41058"), consumption_unit="kWh",
        co2_cost=Money.from_decimal("513.46"), co2_emission=Decimal("7447.51"), co2_factor=FACTOR,
        splits=[
            _split(date(2025, 7, 5), date(2025, 12, 31), "1754.71", "16949",
                   co2_cost=Money.from_decimal("201.22"), co2_emission=Decimal("3074.38")),
            _split(date(2026, 1, 1), date(2026, 6, 30), "2375.80", "24109",
                   co2_cost=Money.from_decimal("312.24"), co2_emission=Decimal("4373.13")),
        ],
        id=42,
    )


def _co2_cost(kg: Decimal, year: int) -> Money:
    return Money.from_decimal(kg / 1000 * CO2_PRICE_EUR_PER_T[year] * (1 + CO2_VAT_RATE))


class TestCo2PricePerYear:
    def test_constants(self) -> None:
        assert CO2_PRICE_EUR_PER_T == {2024: Decimal("45"), 2025: Decimal("55"), 2026: Decimal("60")}
        assert CO2_VAT_RATE == Decimal("0.19")

    def test_2025_part_is_priced_at_55_euro(self) -> None:
        invoice = _gruenwelt_annual()
        (piece,) = invoice.pieces(DateRange.for_year(2025))

        assert piece.consumption == Decimal("22542")
        # 22.542 kWh × 0,18139 kg/kWh = 4.088,89 kg; × 55 €/t × 1,19 = 267,62 €.
        assert piece.co2_emission == Decimal("4088.89")
        assert piece.co2_cost == Money.from_decimal("267.62")
        # Bisher: 447,00 € nach kWh-Anteil - der Mischpreis aus 2024 und 2025.
        assert Money.from_decimal(Decimal("447.00") * 22542 / 41008) == Money.from_decimal("245.71")
        prorated = invoice.prorate(DateRange.for_year(2025))
        assert prorated is not None and prorated.co2_cost == Money.from_decimal("267.62")

    def test_2024_parts_are_priced_at_45_euro(self) -> None:
        pieces = _gruenwelt_annual().pieces(DateRange.for_year(2024))

        assert [piece.consumption for piece in pieces] == [Decimal("1292"), Decimal("3163"), Decimal("14011")]
        june = pieces[0]
        assert june.period == DateRange(date(2024, 6, 1), date(2024, 6, 30))
        assert june.co2_emission == Decimal("234.36")  # 1.292 kWh × 0,18139
        assert june.co2_cost == Money.from_decimal("12.55") == _co2_cost(Decimal("234.36"), 2024)
        for piece in pieces:
            assert piece.co2_emission is not None
            assert piece.co2_cost == _co2_cost(piece.co2_emission, 2024)

    def test_stored_invoice_value_is_unchanged(self) -> None:
        invoice = _gruenwelt_annual()
        invoice.pieces(DateRange.for_year(2025))
        assert (invoice.co2_cost, invoice.co2_emission) == (Money.from_decimal("447.00"), Decimal("7438.44"))
        # Über die ganze Rechnung gilt weiter ihre eigene Angabe.
        whole = invoice.prorate(invoice.period)
        assert whole is invoice and whole.co2_cost == Money.from_decimal("447.00")

    def test_final_invoice_within_one_year_is_unchanged(self) -> None:
        invoice = _gruenwelt_final()
        prorated = invoice.prorate(DateRange.for_year(2025))
        assert prorated is not None
        assert (prorated.co2_cost, prorated.co2_emission) == (Money.from_decimal("7.31"), Decimal("111.55"))
        # Auch die Stücke der Abrechnung ergeben die Angabe der Rechnung (nicht 7,30 € aus kWh × Preis).
        pieces = invoice.pieces(DateRange.for_year(2025))
        assert sum((piece.co2_cost or Money.zero() for piece in pieces), Money.zero()) == Money.from_decimal("7.31")

    def test_invoice_with_co2_per_period_is_unchanged(self) -> None:
        invoice = _maingau()
        part_2025 = invoice.prorate(DateRange.for_year(2025))
        part_2026 = invoice.prorate(DateRange.for_year(2026))
        assert part_2025 is not None and part_2026 is not None
        assert (part_2025.co2_cost, part_2025.co2_emission) == (Money.from_decimal("201.22"), Decimal("3074.38"))
        assert (part_2026.co2_cost, part_2026.co2_emission) == (Money.from_decimal("312.24"), Decimal("4373.13"))

    def test_piece_across_a_year_boundary_keeps_the_share_of_the_invoice(self) -> None:
        # Ohne Splits: Ein Ausschnitt 01.12.2024–31.01.2025 hat keinen einheitlichen Jahrespreis.
        invoice = replace(_gruenwelt_annual(), splits=[])
        (piece,) = invoice.pieces(DateRange(date(2024, 12, 1), date(2025, 1, 31)))
        assert piece.consumption is not None
        assert piece.co2_cost == Money.from_decimal(Decimal("447.00") * piece.consumption / 41008)

    def test_calendar_year_2025_cross_check(self, context: AppContext) -> None:
        house = context.properties.save_property(Property(name="Gashaus", address="Weg 9, 12345 Ort"))
        assert house.id is not None
        gas = context.cost_types.get_by_name("Brennstoffkosten (Gas/Öl)")
        for invoice in (_gruenwelt_annual(), _gruenwelt_final(), _maingau()):
            context.costs.save_cost(replace(invoice, property_id=house.id, cost_type=gas, id=None))

        (group,) = context.invoice_groups.groups(house.id, 2025)
        assert group.total_consumption == Decimal("40106")
        # Gegenprobe über das ganze Jahr: 40.106 kWh × 0,18139 = 7.274,83 kg -> 476,14 € brutto.
        expected_kg = (Decimal(40106) * FACTOR).quantize(Decimal("0.01"))
        assert expected_kg == Decimal("7274.83") and _co2_cost(expected_kg, 2025) == Money.from_decimal("476.14")
        # Summe der Rechnungen: 4.088,89 + 111,55 + 3.074,38 kg bzw. 267,62 + 7,31 + 201,22 €. Je ein
        # Rundungscent Abstand: Jede Rechnung rundet für sich, die Schlussrechnung weist 7,31 € aus (exakt 7,30 €).
        assert group.total_co2_emission == Decimal("7274.82")
        assert group.total_co2_cost == Money.from_decimal("476.15")
