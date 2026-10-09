"""Tests für den Verlauf der Nebenkosten über mehrere Jahre und die Prognose."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from models.apartment import Apartment
from models.cost import Cost
from models.enums import AllocationKey
from models.prepayment_rate import PrepaymentRate
from models.property import Property
from models.tenant import Tenant
from models.trend import CostTrend, ForecastPoint, YearPoint, linear_trend, project
from models.value_objects import DateRange, Money
from ui.widgets.trend_tab import TrendTab, describe_trend


def _money(value: str) -> Money:
    return Money.from_decimal(value)


class TestLinearTrend:
    def test_exact_line_is_continued(self) -> None:
        line = linear_trend([(2021, _money("1000")), (2022, _money("1100")), (2023, _money("1200"))])

        assert line is not None
        assert line[0] == Decimal(10000)  # +100,00 € je Jahr, in Cent
        assert project(line, 2024) == _money("1300")
        assert project(line, 2027) == _money("1600")

    def test_scattered_values_get_the_best_fitting_line(self) -> None:
        # 1000, 1300, 1150, 1450: Ausgleichsgerade mit +120 € je Jahr.
        line = linear_trend(
            [(2021, _money("1000")), (2022, _money("1300")), (2023, _money("1150")), (2024, _money("1450"))]
        )

        assert line is not None and line[0] == Decimal(12000)
        assert project(line, 2025) == _money("1525")

    def test_needs_two_different_years(self) -> None:
        assert linear_trend([]) is None
        assert linear_trend([(2024, _money("1000"))]) is None
        assert linear_trend([(2024, _money("1000")), (2024, _money("1200"))]) is None

    def test_projection_never_goes_below_zero(self) -> None:
        line = linear_trend([(2021, _money("300")), (2022, _money("100"))])
        assert line is not None and project(line, 2025) == Money.zero()


def _trend(costs: list[str], monthly_prepayment: str | None, forecast: list[str], first_year: int = 2021) -> CostTrend:
    points = [YearPoint(first_year + i, _money(c), _money("1200")) for i, c in enumerate(costs)]
    last = points[-1].year
    return CostTrend(
        points=points,
        forecast=[ForecastPoint(last + 1 + i, _money(c)) for i, c in enumerate(forecast)],
        yearly_change=_money("100"),
        current_monthly_prepayment=_money(monthly_prepayment) if monthly_prepayment else None,
    )


class TestCrossing:
    def test_first_year_in_which_costs_exceed_the_prepayment(self) -> None:
        # Vorauszahlung 110 €/Monat = 1.320 €/Jahr; Prognose 1.300, 1.400, 1.500.
        trend = _trend(["1000", "1100", "1200"], "110", ["1300", "1400", "1500"])

        assert not trend.already_exceeded
        assert trend.crossing == ForecastPoint(2025, _money("1400"))
        # 1.400 € / 12 = 116,67 € -> auf volle Zehner: 120 €
        assert trend.recommended_prepayment(trend.crossing) == _money("120")
        assert "übersteigen die Kosten sie voraussichtlich 2025" in describe_trend(trend)
        assert "120 € je Monat nötig" in describe_trend(trend)

    def test_prepayment_already_too_low(self) -> None:
        trend = _trend(["1000", "1100", "1200"], "90", ["1300", "1400"])

        assert trend.already_exceeded and trend.crossing is None
        text = describe_trend(trend)
        assert "liegt schon unter den Kosten von 2023" in text and "100 € je Monat ist jetzt angezeigt" in text

    def test_prepayment_sufficient_for_the_whole_forecast(self) -> None:
        trend = _trend(["1000", "1100", "1200"], "200", ["1300", "1400"])

        assert trend.crossing is None and not trend.already_exceeded
        assert "reicht nach dieser Prognose bis mindestens 2025" in describe_trend(trend)

    def test_without_active_tenant_there_is_nothing_to_compare(self) -> None:
        trend = _trend(["1000", "1100"], None, ["1200"])

        assert trend.crossing is None and trend.current_yearly_prepayment is None
        assert "keinen aktiven Mieter" in describe_trend(trend)

    def test_falling_costs(self) -> None:
        trend = _trend(["1200", "1100"], "200", ["1000"])
        trend.yearly_change = _money("-100")

        assert "sinken im Mittel um 100 € je Jahr" in describe_trend(trend)

    def test_messages_without_data_or_forecast(self) -> None:
        assert "keine umgelegten Kosten" in describe_trend(CostTrend(points=[YearPoint(2024, Money.zero(), Money.zero())]))
        single = CostTrend(points=[YearPoint(2024, _money("1000"), _money("900"))])
        assert "mindestens zwei vollständige Jahre" in describe_trend(single)

    def test_incomplete_years_are_named_and_excluded(self) -> None:
        trend = _trend(["1000", "1100", "400"], "200", ["1200"])
        trend.points[-1] = YearPoint(2023, _money("400"), _money("1200"), complete=False)

        assert [p.year for p in trend.trend_points] == [2021, 2022]
        assert "fehlen Rechnungen): 2023" in describe_trend(trend)


@pytest.fixture
def house(context: AppContext) -> tuple[int, int, int]:
    """Ein Haus mit zwei gleich großen Wohnungen und steigenden Kosten 2021–2024.

    Returns:
        ``(Immobilie, Wohnung EG, Wohnung OG)`` als IDs.
    """
    prop = context.properties.save_property(Property(name="Trendhaus", address="Kurvenweg 1, 12345 Steigstadt"))
    assert prop.id is not None
    flats = []
    for number, rate in (("EG", "50.00"), ("OG", "70.00")):
        flat = context.apartments.save_apartment(Apartment(prop.id, number, Decimal("50"), 2))
        assert flat.id is not None
        tenant = context.tenants.save_tenant(
            Tenant(apartment_id=flat.id, name=f"Mieter {number}", start_date=date(2020, 1, 1)),
            initial_prepayment=_money(rate),
        )
        assert tenant.id is not None
        flats.append((flat.id, tenant.id))
    tax = context.cost_types.get_by_name("Grundsteuer")
    # Zusammen 1.200 / 1.320 / 1.440 / 1.560 € -> je Wohnung die Hälfte.
    for year, amount in ((2021, "1200.00"), (2022, "1320.00"), (2023, "1440.00"), (2024, "1560.00")):
        context.costs.save_cost(Cost(prop.id, tax, _money(amount), DateRange.for_year(year), AllocationKey.AREA))
    # Der Mieter im EG zahlt seit 2023 mehr.
    context.prepayment_rates.save_rate(PrepaymentRate(flats[0][1], _money("60.00"), date(2023, 1, 1)))
    return prop.id, flats[0][0], flats[1][0]


class TestService:
    def test_history_of_the_whole_house(self, context: AppContext, house: tuple[int, int, int]) -> None:
        trend = context.trends.build(house[0], 2021, 2024, forecast_years=3)

        assert [(p.year, p.costs, p.prepayments) for p in trend.points] == [
            (2021, _money("1200"), _money("1440")),  # 50 + 70 = 120 €/Monat
            (2022, _money("1320"), _money("1440")),
            (2023, _money("1440"), _money("1560")),  # ab 2023: 60 + 70 = 130 €/Monat
            (2024, _money("1560"), _money("1560")),
        ]
        assert all(p.complete for p in trend.points)
        assert trend.yearly_change == _money("120")
        assert [(p.year, p.costs) for p in trend.forecast] == [
            (2025, _money("1680")), (2026, _money("1800")), (2027, _money("1920")),
        ]
        # Heute zahlen die Mieter zusammen 130 €/Monat = 1.560 €/Jahr -> ab 2025 reicht das nicht mehr.
        assert trend.current_monthly_prepayment == _money("130")
        assert trend.crossing is not None and trend.crossing.year == 2025
        assert trend.recommended_prepayment(trend.crossing) == _money("140")

    def test_single_apartment(self, context: AppContext, house: tuple[int, int, int]) -> None:
        property_id, _ground_floor, upper_floor = house

        trend = context.trends.build(property_id, 2021, 2024, apartment_id=upper_floor, forecast_years=4)

        assert [p.costs for p in trend.points] == [_money("600"), _money("660"), _money("720"), _money("780")]
        assert {p.prepayments for p in trend.points} == {_money("840")}
        assert trend.current_monthly_prepayment == _money("70")
        # 70 €/Monat = 840 €/Jahr: Prognose 840 (2025, noch gedeckt), 900 (2026).
        assert trend.crossing is not None and trend.crossing.year == 2026

    def test_incomplete_year_is_marked_and_ignored_in_forecast(self, context: AppContext, house: tuple[int, int, int]) -> None:
        # 2025 ist die Grundsteuer noch nicht erfasst, dafür schon eine andere Rechnung.
        context.costs.save_cost(
            Cost(house[0], context.cost_types.get_by_name("Gebäudereinigung"), _money("100"), DateRange.for_year(2025), AllocationKey.AREA)
        )

        trend = context.trends.build(house[0], 2021, 2025, forecast_years=1)

        assert [p.complete for p in trend.points] == [True, True, True, True, False]
        assert trend.yearly_change == _money("120")  # unverändert: 2025 zählt nicht mit
        assert trend.forecast == [ForecastPoint(2026, _money("1800"))]

    def test_years_with_invoices_and_swapped_range(self, context: AppContext, house: tuple[int, int, int]) -> None:
        assert context.trends.years_with_invoices(house[0]) == [2021, 2022, 2023, 2024]
        assert [p.year for p in context.trends.build(house[0], 2023, 2022).points] == [2022, 2023]

    def test_no_forecast_with_zero_horizon_or_one_year(self, context: AppContext, house: tuple[int, int, int]) -> None:
        assert context.trends.build(house[0], 2021, 2024, forecast_years=0).forecast == []
        assert context.trends.build(house[0], 2024, 2024, forecast_years=5).forecast == []


class TestTab:
    def test_tab_shows_chart_table_and_summary(
        self, qt_app: QApplication, context: AppContext, house: tuple[int, int, int]
    ) -> None:
        tab = TrendTab(context)
        tab.resize(1200, 800)
        tab.refresh()
        tab._from_spin.setValue(2021)
        tab._to_spin.setValue(2024)

        assert tab._table.model().rowCount() == 4
        assert "steigen im Mittel um 120 € je Jahr" in tab._summary.text()
        assert "voraussichtlich 2025" in tab._summary.text()
        # Das Diagramm lässt sich zeichnen (auch ohne Prognose und ganz ohne Daten).
        assert not tab._chart.grab().isNull()
        tab._forecast_spin.setValue(0)
        assert not tab._chart.grab().isNull()
        tab._chart.set_trend(CostTrend())
        assert not tab._chart.grab().isNull()

    def test_scope_can_be_narrowed_to_one_apartment(
        self, qt_app: QApplication, context: AppContext, house: tuple[int, int, int]
    ) -> None:
        tab = TrendTab(context)
        tab.refresh()
        tab._from_spin.setValue(2021)
        tab._to_spin.setValue(2024)

        tab._scope_combo.setCurrentIndex(tab._scope_combo.findData(house[2]))

        assert "voraussichtlich 2026" in tab._summary.text()
