"""Tests für Splits (Teilabschnitte) einer Rechnung.

Regel: Sind Splits hinterlegt, bestimmen allein sie, welcher Teil der
Rechnung in welchen Zeitraum fällt. Ohne Splits wird taggenau linear verteilt.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app_context import AppContext
from models.apartment import Apartment
from models.cost import Cost, InvoiceSplit, uncovered_periods, validate_splits
from models.cost_type import CostType
from models.enums import AllocationKey, MeterType
from models.exceptions import ValidationError
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from utils.formatters import format_share_title

GAS = CostType("Brennstoffkosten (Gas/Öl)", AllocationKey.CONSUMPTION_AREA, MeterType.HEATING, heating_rule=True, id=1)
OTHER = CostType("Sonstige Betriebskosten", id=2)
INVOICE_PERIOD = DateRange(date(2023, 6, 1), date(2024, 5, 31))


def _split(start: date, end: date, amount: str, consumption: str | None = None) -> InvoiceSplit:
    return InvoiceSplit(DateRange(start, end), Money.from_decimal(amount), Decimal(consumption) if consumption else None)


def _gas_invoice() -> Cost:
    """Gasrechnung 01.06.2023–31.05.2024 mit sieben Ableseabschnitten (reale Struktur)."""
    return Cost(
        property_id=1,
        cost_type=GAS,
        amount=Money.from_decimal("3299.88"),
        period=INVOICE_PERIOD,
        allocation_key=AllocationKey.CONSUMPTION_AREA,
        consumption=Decimal("38276"),
        consumption_unit="kWh",
        splits=[
            _split(date(2023, 6, 1), date(2023, 6, 30), "79.24", "867"),
            _split(date(2023, 7, 1), date(2023, 9, 30), "235.72", "2579"),
            _split(date(2023, 10, 1), date(2023, 12, 31), "1064.05", "12416"),
            _split(date(2024, 1, 1), date(2024, 2, 17), "916.30", "10692"),
            _split(date(2024, 2, 18), date(2024, 3, 13), "410.07", "4785"),
            _split(date(2024, 3, 14), date(2024, 3, 31), "227.45", "2654"),
            _split(date(2024, 4, 1), date(2024, 5, 31), "367.05", "4283"),
        ],
    )


class TestYearAssignment:
    def test_splits_decide_which_year_gets_what(self) -> None:
        invoice = _gas_invoice()
        invoice.validate()

        part_2024 = invoice.prorate(DateRange.for_year(2024))
        part_2023 = invoice.prorate(DateRange.for_year(2023))
        assert part_2024 is not None and part_2023 is not None
        # 2024: die vier Abschnitte ab Januar - nicht 152/366 der Gesamtsumme.
        assert part_2024.amount == Money.from_decimal("1920.87")
        assert part_2024.consumption == Decimal("22414")
        assert part_2023.amount == Money.from_decimal("1379.01")
        assert part_2023.consumption == Decimal("15862")
        # Beide Jahre zusammen ergeben exakt die Rechnung.
        assert part_2023.amount + part_2024.amount == invoice.amount

    def test_without_splits_the_invoice_is_spread_linearly(self) -> None:
        invoice = _gas_invoice()
        invoice.splits = []
        part_2024 = invoice.prorate(DateRange.for_year(2024))
        assert part_2024 is not None
        # 3.299,88 * 152 / 366 Tage - im Winterhalbjahr deutlich zu wenig.
        assert part_2024.amount == Money.from_decimal("1370.44")
        assert part_2024.consumption == Decimal("15896.044")  # 38.276 kWh * 152 / 366

    def test_pieces_keep_their_own_periods(self) -> None:
        pieces = _gas_invoice().pieces(DateRange.for_year(2024))
        assert [p.amount for p in pieces] == [Money.from_decimal(a) for a in ("916.30", "410.07", "227.45", "367.05")]
        assert pieces[0].period == DateRange(date(2024, 1, 1), date(2024, 2, 17))
        assert all(p.split_period == p.period and p.full_amount == Money.from_decimal("3299.88") for p in pieces)

    def test_split_across_year_end_is_prorated_within_itself(self) -> None:
        """Ein Split 01.12.–31.01. (62 Tage) wird taggenau geteilt - nur dieser Split."""
        invoice = Cost(
            1, OTHER, Money.from_decimal("920.00"), DateRange(date(2024, 10, 1), date(2025, 3, 31)), AllocationKey.AREA,
            splits=[
                _split(date(2024, 10, 1), date(2024, 11, 30), "300.00"),
                _split(date(2024, 12, 1), date(2025, 1, 31), "620.00"),
            ],
        )
        invoice.validate()
        part_2024 = invoice.prorate(DateRange.for_year(2024))
        part_2025 = invoice.prorate(DateRange.for_year(2025))
        assert part_2024 is not None and part_2025 is not None
        assert part_2024.amount == Money.from_decimal("610.00")  # 300 + 620 * 31/62
        assert part_2025.amount == Money.from_decimal("310.00")  # 620 * 31/62; Februar/März ohne Split = 0
        piece = invoice.pieces(DateRange.for_year(2024))[1]
        assert piece.period == DateRange(date(2024, 12, 1), date(2024, 12, 31))
        assert piece.split_period == DateRange(date(2024, 12, 1), date(2025, 1, 31))


class TestSumCheck:
    """Die Summe der Splits muss bei Kosten UND Verbrauch dem Gesamtwert entsprechen."""

    def test_amount_mismatch_names_the_difference(self) -> None:
        invoice = _gas_invoice()
        invoice.amount = Money.from_decimal("3300.00")
        with pytest.raises(ValidationError, match="Differenz: 0,12 €"):
            invoice.validate()

    def test_consumption_mismatch_names_the_difference(self) -> None:
        invoice = _gas_invoice()
        invoice.consumption = Decimal("38300")
        with pytest.raises(ValidationError, match="Split-Verbräuche.*Differenz: 24"):
            invoice.validate()

    def test_consumption_must_be_complete(self) -> None:
        invoice = _gas_invoice()
        invoice.splits[2] = InvoiceSplit(invoice.splits[2].period, invoice.splits[2].amount, None)
        with pytest.raises(ValidationError, match="bei jedem Split"):
            invoice.validate()
        without_total = _gas_invoice()
        without_total.consumption, without_total.consumption_unit = None, ""
        with pytest.raises(ValidationError, match="Gesamtverbrauch"):
            without_total.validate()

    def test_periods_must_fit_and_not_overlap(self) -> None:
        amount = Money.from_decimal("100")
        with pytest.raises(ValidationError, match="Rechnungszeitraum"):
            validate_splits([_split(date(2023, 5, 1), date(2023, 6, 30), "100")], INVOICE_PERIOD, amount, None)
        with pytest.raises(ValidationError, match="überschneidet"):
            validate_splits(
                [_split(date(2023, 6, 1), date(2023, 8, 31), "50"), _split(date(2023, 8, 31), date(2023, 9, 30), "50")],
                INVOICE_PERIOD, amount, None,
            )
        validate_splits([], INVOICE_PERIOD, amount, None)  # ohne Splits ist nichts zu prüfen


class TestGaps:
    """Lücken zwischen Splits sind erlaubt, werden aber gemeldet."""

    def test_contiguous_splits_have_no_gaps(self) -> None:
        assert _gas_invoice().split_gaps == []
        invoice = _gas_invoice()
        invoice.splits = []
        assert invoice.split_gaps == []  # ohne Splits gibt es keine Lücken, nur lineare Verteilung

    def test_gaps_at_start_middle_and_end_are_found(self) -> None:
        year = DateRange.for_year(2025)
        splits = [
            _split(date(2025, 2, 1), date(2025, 3, 31), "400"),
            _split(date(2025, 7, 1), date(2025, 11, 30), "800"),
        ]
        validate_splits(splits, year, Money.from_decimal("1200"), None)  # Summe stimmt -> gültig
        assert uncovered_periods(splits, year) == [
            DateRange(date(2025, 1, 1), date(2025, 1, 31)),
            DateRange(date(2025, 4, 1), date(2025, 6, 30)),
            DateRange(date(2025, 12, 1), date(2025, 12, 31)),
        ]


class TestBillingWithSplits:
    @pytest.fixture()
    def house_id(self, context: AppContext) -> int:
        """Eine Wohnung mit Mieterwechsel zum 01.07.2025."""
        house = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert house.id is not None
        flat = context.apartments.save_apartment(Apartment(house.id, "1", Decimal("80")))
        assert flat.id is not None
        context.tenants.save_tenant(Tenant(flat.id, "Altmieter", date(2020, 1, 1), date(2025, 6, 30)))
        context.tenants.save_tenant(Tenant(flat.id, "Neumieter", date(2025, 7, 1)))
        return house.id

    def test_each_tenant_pays_the_splits_of_his_time(self, context: AppContext, house_id: int) -> None:
        year = DateRange.for_year(2025)
        invoice = context.costs.save_cost(
            Cost(
                house_id, context.cost_types.get_by_name("Gartenpflege"), Money.from_decimal("1800.00"), year,
                AllocationKey.AREA, description="Gärtner",
                splits=[
                    _split(date(2025, 1, 1), date(2025, 6, 30), "600.00"),
                    _split(date(2025, 7, 1), date(2025, 12, 31), "1200.00"),
                ],
            )
        )
        # Splits überstehen Speichern und Laden.
        assert context.costs.list_costs(house_id)[0].splits == invoice.splits

        result = context.billing.create_billing(house_id, year)
        by_name = {s.tenant.name: s for s in result.statements}
        assert by_name["Altmieter"].total_costs == Money.from_decimal("600.00")
        assert by_name["Neumieter"].total_costs == Money.from_decimal("1200.00")
        assert result.total_costs == Money.from_decimal("1800.00") and result.vacancy_amount.is_zero
        assert "Abschnitt 01.01.25 – 30.06.25" in format_share_title(by_name["Altmieter"].shares[0])

        # Gegenprobe ohne Splits: linear nach Tagen (181 bzw. 184 von 365).
        assert invoice.id is not None
        plain = context.costs.list_costs(house_id)[0]
        plain.splits = []
        context.costs.save_cost(plain)
        linear = {s.tenant.name: s.total_costs for s in context.billing.create_billing(house_id, year).statements}
        assert linear == {"Altmieter": Money.from_decimal("892.60"), "Neumieter": Money.from_decimal("907.40")}

    def test_gap_is_reported_in_billing(self, context: AppContext, house_id: int) -> None:
        """Tippfehler im Datum: April bis Juni fehlt - die Abrechnung weist darauf hin."""
        year = DateRange.for_year(2025)
        context.costs.save_cost(
            Cost(
                house_id, context.cost_types.get_by_name("Gartenpflege"), Money.from_decimal("1200.00"), year,
                AllocationKey.AREA, description="Gärtner",
                splits=[
                    _split(date(2025, 1, 1), date(2025, 3, 31), "400.00"),
                    _split(date(2025, 7, 1), date(2025, 12, 31), "800.00"),
                ],
            )
        )
        result = context.billing.create_billing(house_id, year)
        assert len(result.warnings) == 1
        assert "01.04.2025 – 30.06.2025" in result.warnings[0] and "nicht vollständig" in result.warnings[0]
        # Gerechnet wird trotzdem - die Summe der Rechnung bleibt erhalten.
        assert result.total_costs == Money.from_decimal("1200.00")
        # Liegt die Lücke außerhalb des Abrechnungszeitraums, gibt es keinen Hinweis.
        first_quarter = DateRange(date(2025, 1, 1), date(2025, 3, 31))
        assert not context.billing.create_billing(house_id, first_quarter).warnings

    def test_invalid_splits_are_rejected_on_save(self, context: AppContext, house_id: int) -> None:
        with pytest.raises(ValidationError, match="Split-Beträge"):
            context.costs.save_cost(
                Cost(
                    house_id, context.cost_types.get_by_name("Gartenpflege"), Money.from_decimal("1800.00"),
                    DateRange.for_year(2025), AllocationKey.AREA,
                    splits=[_split(date(2025, 1, 1), date(2025, 6, 30), "600.00")],
                )
            )
        assert context.costs.list_costs(house_id) == []
