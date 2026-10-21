"""Wasserregel: Grundgebühr je Wohnung, Mengenpreis nach Zählern, Warmwasser getrennt, Rest nach Fläche.

Grundlage sind die echten Wasserrechnungen 2024/25 und 2025/26 und die Zählerstände
2025 des Hauses (Gesamtverbrauch 2025 laut Rechnungen 275 m³, Wohnungszähler 148 m³,
Warmwasserzähler laut ISTA 70,63 m³).
"""

from __future__ import annotations

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from database.migrations import MIGRATIONS, current_version, migrate
from models.apartment import Apartment
from models.billing import BillingResult
from models.cost import Cost, InvoiceSplit
from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from models.exceptions import ValidationError
from models.meter_reading import MeterReading
from models.property import Property
from models.tariff import SplitCalculation
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from services.water_allocation import (
    BASE_LABEL_AREA,
    BASE_LABEL_UNITS as BASE_LABEL,
    HOT_WATER_COST_TYPE,
    MEASURED_LABEL,
    REST_LABEL,
    base_fee,
    water_breakdown,
)

YEAR = DateRange.for_year(2025)
WATER = CostType("Wasserversorgung", AllocationKey.CONSUMPTION, MeterType.COLD_WATER, id=2)

#: Gelsenwasser: Arbeitspreis netto je m³, Grundpreis 153 € je Jahr, 7 % USt.
PRICE_2024 = SplitCalculation(working_price=Decimal("1.938"), base_price=Decimal("153"), vat_percent=Decimal("7"),
                              base_price_days=Decimal("365"))
PRICE_2025 = SplitCalculation(working_price=Decimal("1.9829"), base_price=Decimal("153"), vat_percent=Decimal("7"),
                              base_price_days=Decimal("365"))
PRICE_2025_CALENDAR = SplitCalculation(working_price=Decimal("1.9829"), base_price=Decimal("153"), vat_percent=Decimal("7"))


def _split(start: date, end: date, amount: str, m3: str, calculation: SplitCalculation | None) -> InvoiceSplit:
    return InvoiceSplit(DateRange(start, end), Money.from_decimal(amount), Decimal(m3), calculation)


def _invoices(house_id: int = 1, cost_type: CostType = WATER, key: AllocationKey = AllocationKey.WATER_RULES_UNITS,
              with_prices: bool = True) -> list[Cost]:
    """Die beiden Gelsenwasser-Rechnungen mit ihren Splits (Werte laut Beleg)."""
    p24, p25, p25c = (PRICE_2024, PRICE_2025, PRICE_2025_CALENDAR) if with_prices else (None, None, None)
    return [
        Cost(house_id, cost_type, Money.from_decimal("763.86"), DateRange(date(2024, 9, 14), date(2025, 9, 12)),
             AllocationKey.CONSUMPTION, description="Gelsenwasser AG", consumption=Decimal("285"),
             consumption_unit="m³", difference_key=key, splits=[
                 _split(date(2024, 9, 14), date(2024, 12, 31), "225.15", "85", p24),
                 _split(date(2025, 1, 1), date(2025, 5, 30), "317.64", "118", p25),
                 _split(date(2025, 5, 31), date(2025, 9, 12), "221.07", "82", p25),
             ]),
        Cost(house_id, cost_type, Money.from_decimal("787.08"), DateRange(date(2025, 9, 13), date(2026, 9, 30)),
             AllocationKey.CONSUMPTION, description="Gelsenwasser", consumption=Decimal("290"),
             consumption_unit="m³", difference_key=key, splits=[
                 _split(date(2025, 9, 13), date(2025, 12, 31), "208.47", "75", p25c),
                 _split(date(2026, 1, 1), date(2026, 9, 30), "578.61", "215", p25c),
             ]),
    ]


def _pieces_2025(**kwargs: object) -> list[Cost]:
    return [piece for invoice in _invoices(**kwargs) for piece in invoice.pieces(YEAR)]  # type: ignore[arg-type]


# ---------------------------------------------------------------------- #
# Rechenteil
# ---------------------------------------------------------------------- #
class TestBreakdown:
    def test_water_costs_2025_from_both_invoices(self) -> None:
        pieces = _pieces_2025()
        assert [piece.consumption for piece in pieces] == [Decimal("118"), Decimal("82"), Decimal("75")]
        assert sum((piece.amount for piece in pieces), Money.zero()) == Money.from_decimal("747.18")
        assert sum((piece.consumption or 0 for piece in pieces), Decimal(0)) == Decimal("275")

    def test_base_fee_from_the_split_prices(self) -> None:
        fees = [base_fee(piece) for piece in _pieces_2025()]
        # 153 € × 150/365 = 62,88 netto; × 105/365 = 44,01; × 110/365 = 46,11 (kalendergenau) - je zzgl. 7 %.
        assert fees == [Decimal("62.88") * Decimal("1.07"), Decimal("44.01") * Decimal("1.07"),
                        Decimal("46.11") * Decimal("1.07")]

    def test_breakdown_2025(self) -> None:
        warnings: list[str] = []
        result = water_breakdown(_pieces_2025(), Decimal("148"), Decimal("70.63"), warnings)

        assert not warnings
        assert result.amount == Money.from_decimal("747.18")
        assert result.base_amount == Money.from_decimal("163.71")
        assert result.quantity_amount == Money.from_decimal("583.47")
        # Mengenpreis brutto: 583,47 € / 275 m³ = 2,1217 €/m³ (= 1,9829 € netto × 1,07).
        assert round(result.price_per_unit, 4) == Decimal("2.1217")
        assert (result.total_quantity, result.measured_quantity) == (Decimal("275"), Decimal("148"))
        assert (result.hot_water_quantity, result.rest_quantity) == (Decimal("70.63"), Decimal("56.37"))
        assert result.measured_amount == Money.from_decimal("314.01")  # 148 m³
        assert result.hot_water_amount == Money.from_decimal("149.86")  # 70,63 m³ × 2,1217 €/m³
        assert result.rest_amount == Money.from_decimal("119.60")  # 56,37 m³, nimmt Rundungscents auf
        assert (result.base_amount + result.measured_amount + result.hot_water_amount + result.rest_amount
                == result.amount)

    def test_hot_water_amount_is_quantity_times_price(self) -> None:
        result = water_breakdown(_pieces_2025(), Decimal("148"), Decimal("70.63"), [])
        exact = Decimal("70.63") * Decimal(result.quantity_amount.cents) / 100 / Decimal("275")
        assert result.hot_water_amount == Money.from_decimal(exact)
        rest = Decimal("56.37") * Decimal(result.quantity_amount.cents) / 100 / Decimal("275")
        assert abs(result.rest_amount - Money.from_decimal(rest)) <= Money(1)

    def test_without_hot_water_the_whole_difference_is_rest(self) -> None:
        result = water_breakdown(_pieces_2025(), Decimal("148"), None, [])
        assert (result.hot_water_quantity, result.rest_quantity) == (Decimal(0), Decimal("127"))
        assert result.hot_water_amount.is_zero
        assert result.rest_amount == Money.from_decimal("269.46")  # 583,47 − 314,01

    def test_no_difference_means_all_by_measured_consumption(self) -> None:
        warnings: list[str] = []
        equal = water_breakdown(_pieces_2025(), Decimal("275"), None, warnings)
        assert equal.measured_amount == equal.quantity_amount and equal.rest_amount.is_zero and not warnings

        below = water_breakdown(_pieces_2025(), Decimal("300"), Decimal("70.63"), warnings)
        assert below.measured_amount == below.quantity_amount and below.hot_water_amount.is_zero
        assert any("kleiner als die Summe der Wohnungszähler" in w for w in warnings)
        assert any("kein Warmwasser-Kaltwasseranteil" in w for w in warnings)

    def test_hot_water_above_the_difference_is_capped(self) -> None:
        warnings: list[str] = []
        result = water_breakdown(_pieces_2025(), Decimal("250"), Decimal("30"), warnings)
        assert (result.hot_water_quantity, result.rest_quantity) == (Decimal("25"), Decimal(0))
        assert any("messen mehr als die Differenz" in w for w in warnings)
        assert result.base_amount + result.measured_amount + result.hot_water_amount + result.rest_amount == result.amount

    def test_without_base_price_a_warning_is_given(self) -> None:
        warnings: list[str] = []
        result = water_breakdown(_pieces_2025(with_prices=False), Decimal("148"), Decimal("70.63"), warnings)
        assert result.base_amount.is_zero and result.quantity_amount == Money.from_decimal("747.18")
        assert any("kein Grundpreis hinterlegt" in w for w in warnings)


class TestSettings:
    def test_only_for_cold_water(self) -> None:
        heating = CostType("Hausstrom", AllocationKey.CONSUMPTION, MeterType.ELECTRICITY, id=9)
        with pytest.raises(ValidationError, match="Kaltwasserzähler"):
            _invoices(cost_type=heating)[0].validate()
        _invoices()[0].validate()

    def test_label_and_flag(self) -> None:
        invoice = _invoices()[0]
        assert invoice.uses_water_rules
        assert invoice.allocation_label.startswith("Wasserregel")
        assert not _invoices(key=AllocationKey.AREA)[0].uses_water_rules

    def test_hot_water_meter_type(self) -> None:
        # HOT_WATER ist ein zweiter Name der Warmwasserzähler - keine doppelte Auswahl in der Oberfläche.
        assert MeterType.HOT_WATER is MeterType.WARM_WATER
        assert MeterType.HOT_WATER.unit == "m³" == MeterType.COLD_WATER.unit
        assert MeterType.HOT_WATER.label == "Warmwasser"
        assert [meter.label for meter in MeterType].count("Warmwasser") == 1


# ---------------------------------------------------------------------- #
# Abrechnung mit Datenbank
# ---------------------------------------------------------------------- #
#: Kaltwasser je Wohnung: 31.12.2024, 31.05.2025, 31.12.2025 (2025: 54 / 54 / 40 m³).
COLD = {"EG": ("43", "65", "97"), "1OG": ("51", "76", "105"), "DG": ("91", "131", "131")}
#: Warmwasser laut ISTA (ALT/NEU); die 2024er-Stände von EG und DG sind noch Platzhalter.
HOT = {
    "EG": [(date(2024, 12, 31), "70.69", "Platzhalter bis ISTA 2024"), (date(2025, 12, 31), "88.60", "ISTA 2025")],
    "1OG": [(date(2023, 12, 31), "117.75", "ISTA 2024"), (date(2024, 12, 31), "138.32", "ISTA 2024/2025"),
            (date(2025, 12, 31), "160.91", "ISTA 2025")],
    "DG": [(date(2024, 12, 31), "72.26", "Platzhalter bis ISTA 2024"), (date(2025, 12, 31), "102.39", "ISTA 2025")],
}
FLATS = (("EG", "105", 2, "Anna"), ("1OG", "158.66", 3, "Ben"), ("DG", "105", 5, "Cem"))


def _house(context: AppContext, *, hot: dict | None = HOT, cold: dict = COLD,  # type: ignore[type-arg]
           key: AllocationKey = AllocationKey.WATER_RULES_UNITS) -> tuple[int, dict[str, int]]:
    house = context.properties.save_property(Property(name="Wasserhaus", address="Weg 1, 12345 Ort"))
    assert house.id is not None
    flats: dict[str, int] = {}
    for number, area, persons, name in FLATS:
        flat = context.apartments.save_apartment(Apartment(house.id, number, Decimal(area), persons))
        assert flat.id is not None
        flats[number] = flat.id
        context.tenants.save_tenant(Tenant(apartment_id=flat.id, name=name, start_date=date(2024, 1, 1)))
        for value, day in zip(cold.get(number, ()), (date(2024, 12, 31), date(2025, 5, 31), date(2025, 12, 31))):
            context.meter_readings.save_reading(MeterReading(flat.id, MeterType.COLD_WATER, Decimal(value), day))
        for day, value, note in (hot or {}).get(number, []):
            context.meter_readings.save_reading(
                MeterReading(flat.id, MeterType.HOT_WATER, Decimal(value), day, reader="ISTA", note=note)
            )
    water = context.cost_types.get_by_name("Wasserversorgung")
    for invoice in _invoices(house.id, water, key):
        context.costs.save_cost(invoice)
    return house.id, flats


def _parts(result: BillingResult) -> dict[str, dict[str, Money]]:
    """Wasseranteile je Wohnung und Bestandteil."""
    parts: dict[str, dict[str, Money]] = {}
    for statement in result.statements:
        for share in statement.shares:
            if share.cost.cost_type.meter_type is None:
                continue
            name = share.component if share.cost.cost_type is not HOT_WATER_COST_TYPE else "Warmwasser"
            parts.setdefault(statement.apartment.number, {})[name] = share.amount
    return parts


class TestBilling:
    def test_hot_water_readings_are_stored_and_evaluated(self, context: AppContext) -> None:
        house_id, flats = _house(context)
        stored = context.meter_readings.list_readings(house_id, meter_type=MeterType.HOT_WATER)
        assert len(stored) == 7 and all(r.meter_type is MeterType.HOT_WATER for r in stored)
        row = context.database.query_one("SELECT meter_type FROM meter_readings WHERE value = '160.91'")
        assert row is not None and row["meter_type"] == "WARM_WATER"

        hot = context.meter_readings.consumption_by_apartment(house_id, MeterType.HOT_WATER, YEAR)
        assert hot == {flats["1OG"]: Decimal("22.59"), flats["DG"]: Decimal("30.13"), flats["EG"]: Decimal("17.91")}
        assert sum(hot.values()) == Decimal("70.63")
        cold = context.meter_readings.consumption_by_apartment(house_id, MeterType.COLD_WATER, YEAR)
        assert cold == {flats["EG"]: Decimal("54"), flats["1OG"]: Decimal("54"), flats["DG"]: Decimal("40")}
        # 2024: Für EG und DG fehlt der Stand vom 31.12.2023 (ISTA 2024 noch nicht erfasst).
        hot_2024 = context.meter_readings.consumption_by_apartment(house_id, MeterType.HOT_WATER, DateRange.for_year(2024))
        assert hot_2024 == {flats["1OG"]: Decimal("20.57")}

    def test_distribution_2025(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        result = context.billing.create_billing(house_id, YEAR)

        water, hot = result.costs
        assert water.cost_type.name == "Wasserversorgung" and hot.cost_type is HOT_WATER_COST_TYPE
        assert (water.amount, hot.amount) == (Money.from_decimal("597.32"), Money.from_decimal("149.86"))
        assert (water.consumption, hot.consumption) == (Decimal("204.37"), Decimal("70.63"))
        assert water.period == hot.period == YEAR

        assert _parts(result) == {
            "EG": {BASE_LABEL: Money.from_decimal("54.57"), MEASURED_LABEL: Money.from_decimal("114.57"),
                   REST_LABEL: Money.from_decimal("34.06"), "Warmwasser": Money.from_decimal("38.00")},
            "1OG": {BASE_LABEL: Money.from_decimal("54.57"), MEASURED_LABEL: Money.from_decimal("114.57"),
                    REST_LABEL: Money.from_decimal("51.47"), "Warmwasser": Money.from_decimal("47.93")},
            "DG": {BASE_LABEL: Money.from_decimal("54.57"), MEASURED_LABEL: Money.from_decimal("84.87"),
                   REST_LABEL: Money.from_decimal("34.07"), "Warmwasser": Money.from_decimal("63.93")},
        }

    def test_invoice_total_is_fully_distributed(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        result = context.billing.create_billing(house_id, YEAR)

        components: dict[str, Money] = {}
        for apartment in _parts(result).values():
            for name, amount in apartment.items():
                components[name] = components.get(name, Money.zero()) + amount
        assert components == {
            BASE_LABEL: Money.from_decimal("163.71"), MEASURED_LABEL: Money.from_decimal("314.01"),
            "Warmwasser": Money.from_decimal("149.86"), REST_LABEL: Money.from_decimal("119.60"),
        }
        # Grundgebühr + Wohnungswasser + Warmwasser + Rest = Rechnungsbetrag
        assert sum(components.values(), Money.zero()) == Money.from_decimal("747.18")
        assert result.total_costs == result.total_allocated == Money.from_decimal("747.18")
        assert result.vacancy_amount.is_zero

    def test_placeholder_readings_are_reported(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        warnings = context.billing.create_billing(house_id, YEAR).warnings
        placeholders = [w for w in warnings if "Platzhalter" in w]
        assert len(placeholders) == 2
        assert any("EG vom 31.12.2024" in w for w in placeholders)
        assert any("DG vom 31.12.2024" in w for w in placeholders)
        assert len(warnings) == 2

    def test_missing_hot_water_readings(self, context: AppContext) -> None:
        hot = {name: rows for name, rows in HOT.items() if name != "DG"}
        house_id, _ = _house(context, hot=hot)
        result = context.billing.create_billing(house_id, YEAR)

        assert any("fehlen Warmwasser-Zählerstände" in w and "DG" in w for w in result.warnings), result.warnings
        (water,) = result.costs  # keine Warmwasser-Position
        assert water.amount == Money.from_decimal("747.18")
        rest = sum((parts[REST_LABEL] for parts in _parts(result).values()), Money.zero())
        assert rest == Money.from_decimal("269.46")  # 127 m³ Differenz nach Wohnfläche
        assert result.total_allocated == Money.from_decimal("747.18")

    def test_missing_cold_water_readings(self, context: AppContext) -> None:
        cold = {**COLD, "DG": ("91", "131")}  # kein Stand am 31.12.2025
        house_id, _ = _house(context, cold=cold)
        result = context.billing.create_billing(house_id, YEAR)

        assert any("fehlen Kaltwasser-Zählerstände" in w and "DG" in w for w in result.warnings), result.warnings
        # Keine stille Verteilung nach der Wasserregel: Die Abschnitte bleiben wie bisher.
        assert len(result.costs) == 3 and all(cost.split_period is not None for cost in result.costs)
        assert result.total_costs == Money.from_decimal("747.18")

    def test_other_difference_keys_are_unchanged(self, context: AppContext) -> None:
        house_id, _ = _house(context, key=AllocationKey.CONSUMPTION)
        result = context.billing.create_billing(house_id, YEAR)
        assert len(result.costs) == 3 and not any(cost.cost_type is HOT_WATER_COST_TYPE for cost in result.costs)
        assert not any("Platzhalter" in w for w in result.warnings)

    def test_setting_is_stored(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        assert all(cost.difference_key is AllocationKey.WATER_RULES_UNITS for cost in context.costs.list_costs(house_id))

    def test_dialog_offers_the_water_rule(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.cost_dialog import CostDialog

        house_id, _ = _house(context)
        invoice = context.costs.list_costs(house_id)[0]
        dialog = CostDialog(context.costs.save_cost, context.cost_types.list_types(), [], house_id, YEAR, cost=invoice)
        combo = dialog._difference_combo
        keys = [combo.itemData(i) for i in range(combo.count())]
        assert AllocationKey.WATER_RULES in keys and AllocationKey.WATER_RULES_UNITS in keys
        assert combo.currentData() is AllocationKey.WATER_RULES_UNITS
        assert combo.itemText(keys.index(AllocationKey.WATER_RULES)).startswith("Wasserregel: Grundgebühr nach Wohnfläche")
        assert combo.itemText(keys.index(AllocationKey.WATER_RULES_UNITS)).startswith("Wasserregel: Grundgebühr je Wohnung")


# ---------------------------------------------------------------------- #
# Heizkostenrechnung enthält das Kaltwasser für die Warmwasserbereitung
# ---------------------------------------------------------------------- #
HEATING = "Verbundene Heizungs- und Warmwasserversorgung"


def _ista(context: AppContext, house_id: int, period: DateRange = YEAR, *, includes_cold_water: bool = True) -> Cost:
    """Messdienst-Abrechnung (direkte Zuordnung) der verbundenen Heizungs- und Warmwasserversorgung."""
    tenants = context.tenants.list_tenants(house_id)
    return context.costs.save_cost(
        Cost(house_id, context.cost_types.get_by_name(HEATING), Money.from_decimal("4500.00"), period,
             AllocationKey.DIRECT, description="ISTA",
             direct_shares={tenant.id: Money.from_decimal("1500.00") for tenant in tenants if tenant.id is not None},
             includes_cold_water=includes_cold_water)
    )


class TestColdWaterInHeatingCosts:
    def test_setting_is_stored_and_only_for_heating_costs(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        saved = _ista(context, house_id)
        assert next(c for c in context.costs.list_costs(house_id) if c.id == saved.id).includes_cold_water

        water = _invoices(house_id, context.cost_types.get_by_name("Wasserversorgung"))[0]
        water.includes_cold_water = True
        water.validate()
        assert not water.includes_cold_water  # Wasserrechnung: Einstellung gilt nicht

    def test_no_separate_hot_water_position(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        _ista(context, house_id)
        result = context.billing.create_billing(house_id, YEAR)

        assert not any(cost.cost_type is HOT_WATER_COST_TYPE for cost in result.costs)
        water = next(cost for cost in result.costs if cost.cost_type.name == "Wasserversorgung")
        assert water.amount == Money.from_decimal("597.32")  # 747,18 − 149,86 (steckt in der ISTA-Rechnung)
        # Grundgebühr, Wohnungswasser und Rest bleiben wie ohne Haken.
        parts = _parts(result)
        assert {name: parts[name].get(REST_LABEL) for name in parts} == {
            "EG": Money.from_decimal("34.06"), "1OG": Money.from_decimal("51.47"), "DG": Money.from_decimal("34.07"),
        }
        assert all("Warmwasser" not in apartment for apartment in parts.values())
        notes = [w for w in result.warnings if "Kaltwasser für die Warmwasserbereitung" in w]
        assert len(notes) == 1
        assert "70,63 m³" in notes[0] and "149,86" in notes[0] and "nicht umgelegt" in notes[0]
        assert result.total_allocated == result.total_costs == Money.from_decimal("5097.32")

    def test_without_the_setting_the_position_stays(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        _ista(context, house_id, includes_cold_water=False)
        result = context.billing.create_billing(house_id, YEAR)
        hot = next(cost for cost in result.costs if cost.cost_type is HOT_WATER_COST_TYPE)
        assert hot.amount == Money.from_decimal("149.86")

    def test_partial_coverage_keeps_the_position_and_warns(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        _ista(context, house_id, DateRange(date(2025, 1, 1), date(2025, 6, 30)))
        result = context.billing.create_billing(house_id, YEAR)

        assert any(cost.cost_type is HOT_WATER_COST_TYPE for cost in result.costs)
        assert any("nicht den ganzen Zeitraum" in w and "doppelt" in w for w in result.warnings), result.warnings

    def test_two_invoices_together_cover_the_year(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        _ista(context, house_id, DateRange(date(2025, 1, 1), date(2025, 6, 30)))
        _ista(context, house_id, DateRange(date(2025, 7, 1), date(2025, 12, 31)))
        result = context.billing.create_billing(house_id, YEAR)
        assert not any(cost.cost_type is HOT_WATER_COST_TYPE for cost in result.costs)

    def test_dialog_checkbox(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.cost_dialog import CostDialog

        house_id, _ = _house(context)
        saved = _ista(context, house_id, includes_cold_water=False)
        types = context.cost_types.list_types()
        tenants = [(tenant, tenant.name) for tenant in context.tenants.list_tenants(house_id)]
        dialog = CostDialog(context.costs.save_cost, types, tenants, house_id, YEAR, cost=saved)
        assert dialog.form.isRowVisible(dialog._cold_water_check)
        dialog._cold_water_check.setChecked(True)
        dialog.accept()
        assert next(c for c in context.costs.list_costs(house_id) if c.id == saved.id).includes_cold_water

        water = CostDialog(context.costs.save_cost, types, [], house_id, YEAR,
                           cost=next(c for c in context.costs.list_costs(house_id) if c.cost_type.name == "Wasserversorgung"))
        assert not water.form.isRowVisible(water._cold_water_check)

    def test_migration_adds_the_column(self) -> None:
        connection = sqlite3.connect(":memory:", isolation_level=None)
        for target, script in MIGRATIONS:
            if target <= 12:
                connection.executescript(f"BEGIN;\n{script}\nPRAGMA user_version = {target};\nCOMMIT;")
        connection.execute(
            "INSERT INTO properties (name, address, created_at) VALUES ('Haus', 'Adresse 1', '2025-01-01T00:00:00')"
        )
        connection.execute(
            "INSERT INTO costs (property_id, cost_type_id, description, amount, period_start, period_end, allocation_key) "
            "VALUES (1, 1, 'Alt', 1000, '2025-01-01', '2025-12-31', 'AREA')"
        )
        migrate(connection, None)
        assert current_version(connection) >= 13
        assert connection.execute("SELECT includes_cold_water FROM costs").fetchone()[0] == 0


class TestOrder:
    def test_positions_follow_the_catalog_order(self, context: AppContext) -> None:
        house_id, _ = _house(context)
        light = context.cost_types.get_by_name("Beleuchtung (Allgemeinstrom)")
        tax = context.cost_types.get_by_name("Grundsteuer")
        for cost_type, start, end, amount in (
            (light, date(2024, 6, 1), date(2025, 5, 31), "312.60"),
            (tax, date(2025, 1, 1), date(2025, 12, 31), "812.90"),
            (light, date(2025, 6, 1), date(2026, 5, 31), "329.11"),
        ):
            context.costs.save_cost(Cost(house_id, cost_type, Money.from_decimal(amount), DateRange(start, end),
                                         AllocationKey.AREA))
        result = context.billing.create_billing(house_id, YEAR)

        names = [cost.cost_type.name for cost in result.costs]
        assert names == ["Grundsteuer", "Wasserversorgung", "Warmwasser – Kaltwasseranteil",
                         "Beleuchtung (Allgemeinstrom)", "Beleuchtung (Allgemeinstrom)"]
        lights = [cost for cost in result.costs if cost.cost_type.name.startswith("Beleuchtung")]
        assert lights[0].period.start < lights[1].period.start
        # Die Mieterschreiben folgen derselben Reihenfolge.
        statement = result.statements[0]
        assert [share.cost.cost_type.name for share in statement.shares][0] == "Grundsteuer"


class TestBaseFeeByArea:
    """Standard der Wasserregel: Grundgebühr nach Wohnfläche (§ 556a Abs. 1 BGB, ohne Vereinbarung)."""

    def test_base_fee_is_distributed_by_area(self, context: AppContext) -> None:
        house_id, _ = _house(context, key=AllocationKey.WATER_RULES)
        result = context.billing.create_billing(house_id, YEAR)

        base = {name: parts[BASE_LABEL_AREA] for name, parts in _parts(result).items()}
        assert sum(base.values(), Money.zero()) == Money.from_decimal("163.71")
        # 105 / 158,66 / 105 m² von 368,66 m²
        assert base["1OG"] > base["EG"] and abs(base["EG"] - base["DG"]) <= Money(1)
        assert base["1OG"] == Money.from_decimal("70.45")  # Rest-Cents an EG und DG (0,71 > 0,57 Cent)
        # Alles andere bleibt wie bei "je Wohnung".
        assert _parts(result)["DG"][MEASURED_LABEL] == Money.from_decimal("84.87")
        assert result.total_allocated == Money.from_decimal("747.18")

    def test_labels(self) -> None:
        area = _invoices(key=AllocationKey.WATER_RULES)[0]
        units = _invoices(key=AllocationKey.WATER_RULES_UNITS)[0]
        assert area.uses_water_rules and units.uses_water_rules
        assert "Grundgebühr nach Wohnfläche" in area.allocation_label
        assert "Grundgebühr je Wohnung" in units.allocation_label
