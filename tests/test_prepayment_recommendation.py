"""Tests für die Empfehlung einer kostendeckenden Vorauszahlung und für das Häkchen-Verhalten im Tab Rechnungen."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QTreeWidget

from app_context import AppContext
from models.apartment import Apartment
from models.billing import TenantStatement
from models.cost import Cost
from models.enums import AllocationKey
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from tools.demo_data import seed_demo_data
from ui.widgets.billing_tab import recommendation_color, recommendation_text
from ui.widgets.invoices_tab import InvoicesTab

YEAR = DateRange.for_year(2025)


def _statement(
    context: AppContext,
    yearly_costs: str,
    current_rate: str,
    start: date = date(2020, 1, 1),
    end: date | None = None,
) -> TenantStatement:
    """Ein Haus mit einer Wohnung und einem Mieter, der die gesamten Kosten trägt."""
    house = context.properties.save_property(Property(name="Einhaus", address="Solostraße 1, 12345 Einstadt"))
    assert house.id is not None
    flat = context.apartments.save_apartment(Apartment(house.id, "EG", Decimal("80"), 2))
    assert flat.id is not None
    context.tenants.save_tenant(
        Tenant(apartment_id=flat.id, name="Mieterin", start_date=start, end_date=end),
        initial_prepayment=Money.from_decimal(current_rate),
    )
    context.costs.save_cost(
        Cost(house.id, context.cost_types.get_by_name("Grundsteuer"), Money.from_decimal(yearly_costs), YEAR, AllocationKey.AREA)
    )
    (statement,) = context.billing.create_billing(house.id, YEAR).statements
    return statement


class TestRecommendation:
    @pytest.mark.parametrize(
        ("yearly_costs", "monthly", "recommended"),
        [
            ("6552.00", "546.00", "550.00"),  # das Beispiel: 546 -> 550
            ("6528.00", "544.00", "540.00"),  # abrunden
            ("6540.00", "545.00", "550.00"),  # genau die Mitte: kaufmännisch aufrunden
            ("6600.00", "550.00", "550.00"),
            ("48.00", "4.00", "0.00"),
        ],
    )
    def test_rounds_to_full_tens(self, context: AppContext, yearly_costs: str, monthly: str, recommended: str) -> None:
        statement = _statement(context, yearly_costs, "500.00")

        assert statement.monthly_costs == Money.from_decimal(monthly)
        assert statement.recommended_prepayment == Money.from_decimal(recommended)

    def test_increase(self, context: AppContext) -> None:
        statement = _statement(context, "6552.00", "500.00")

        assert statement.current_rate == Money.from_decimal("500.00")
        assert statement.prepayment_adjustment == Money.from_decimal("50.00")
        assert recommendation_text(statement) == "550 € (bisher 500 €, +50 €)"
        assert recommendation_text(statement, short=True) == "550 € (+50 €)"
        assert recommendation_color(statement) == "danger"

    def test_decrease(self, context: AppContext) -> None:
        statement = _statement(context, "6552.00", "620.00")

        assert statement.prepayment_adjustment == Money.from_decimal("-70.00")
        assert recommendation_text(statement) == "550 € (bisher 620 €, −70 €)"
        assert recommendation_color(statement) == "success"

    def test_already_cost_covering(self, context: AppContext) -> None:
        statement = _statement(context, "6552.00", "550.00")

        assert statement.prepayment_adjustment == Money.zero()
        assert recommendation_text(statement) == "550 € (passt)"
        assert recommendation_color(statement) is None

    def test_part_year_tenancy_is_extrapolated(self, context: AppContext) -> None:
        # Einzug am 01.07.: Der Mieter trägt 184/365 der Kosten - je Monat bleibt es bei 546 €.
        statement = _statement(context, "6552.00", "500.00", start=date(2025, 7, 1))

        assert statement.occupancy.days == 184
        assert statement.total_costs < Money.from_decimal("6552.00")
        assert statement.monthly_costs == Money.from_decimal("546.00")
        assert statement.recommended_prepayment == Money.from_decimal("550.00")

    def test_no_recommendation_for_ended_tenancy(self, context: AppContext) -> None:
        statement = _statement(context, "6552.00", "500.00", end=date(2025, 9, 30))

        assert statement.current_rate is None and statement.prepayment_adjustment is None
        assert recommendation_text(statement) == "– (ausgezogen)"
        assert recommendation_color(statement) is None

    def test_tenancy_ending_in_the_future_still_gets_one(self, context: AppContext) -> None:
        statement = _statement(context, "6552.00", "500.00", end=date.today() + timedelta(days=30))

        assert statement.current_rate == Money.from_decimal("500.00")


def _click_checkbox(tree: QTreeWidget, row: int) -> None:
    """Klickt wie mit der Maus auf das Kästchen des obersten Eintrags ``row``."""
    rect = tree.visualItemRect(tree.topLevelItem(row))
    QTest.mouseClick(
        tree.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
        QPoint(rect.left() + 10, rect.center().y()),
    )
    QApplication.processEvents()


class TestCheckboxesDoNotCrash:
    """Früher stürzte das Programm ab, wenn ein Häkchen per Mausklick geändert wurde.

    Ursache: Der Baum wurde neu aufgebaut, während Qt den Klick auf einen
    seiner Einträge noch verarbeitete.
    """

    @pytest.fixture
    def tab(self, qt_app: QApplication, context: AppContext) -> InvoicesTab:
        seed_demo_data(context, billing_year=2025)
        tab = InvoicesTab(context)
        tab.resize(1400, 800)
        tab.show()
        tab.refresh()
        QApplication.processEvents()
        return tab

    def test_excluding_aggregated_invoices_by_click(self, tab: InvoicesTab, context: AppContext) -> None:
        property_id = tab._property_id()
        assert property_id is not None
        count = tab._tree.topLevelItemCount()
        assert count >= 3
        included_before = [not g.excluded for g in context.invoice_groups.groups(property_id, 2025)]

        for row in range(count):
            _click_checkbox(tab._tree, row)

        # Jeder Klick hat genau eine aggregierte Rechnung umgeschaltet - und der Baum zeigt es an.
        included_after = [not g.excluded for g in context.invoice_groups.groups(property_id, 2025)]
        assert included_after == [not value for value in included_before]
        shown = [
            tab._tree.topLevelItem(row).checkState(0) == Qt.CheckState.Checked  # type: ignore[union-attr]
            for row in range(tab._tree.topLevelItemCount())
        ]
        assert shown == included_after

    def test_toggling_relevance_in_checklist_by_click(self, tab: InvoicesTab, context: AppContext) -> None:
        tab.show_checklist()
        QApplication.processEvents()
        property_id = tab._property_id()
        assert property_id is not None
        rows = tab._checklist.topLevelItemCount()
        assert rows >= 3

        for row in range(rows):
            # Zeilen mit erfassten Rechnungen bleiben stehen - jede einmal anklicken.
            # Zugeklappt, damit die Zeilenhöhe beim Klicken nicht springt.
            tab._checklist.collapseAll()
            _click_checkbox(tab._checklist, row)

        relevant = context.invoice_checklist.build(property_id, DateRange.for_year(2025)).relevant_items
        assert relevant == []
