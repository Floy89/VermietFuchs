"""Tests für die Rechnungs-Checkliste und den frei wählbaren Zeitraum im Rechnungsmanagement.

Szenario: Abgerechnet wird 2025. Aus dem Vorjahr liegen Rechnungen vor, für
2025 erst ein Teil. Die Checkliste soll zeigen, was fehlt, welche Rechnung es
im Vorjahr war und wann die nächste voraussichtlich kommt.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app_context import AppContext
from models.cost import Cost
from models.enums import AllocationKey
from models.invoice_checklist import ChecklistItem, ChecklistStatus, InvoiceChecklist
from models.property import Property
from models.value_objects import DateRange, Money

PERIOD = DateRange.for_year(2025)


class TestDateRangeHelpers:
    def test_uncovered_by_returns_gaps(self) -> None:
        year = DateRange.for_year(2025)
        covered = [DateRange(date(2024, 6, 1), date(2025, 5, 31)), DateRange(date(2025, 9, 1), date(2025, 9, 30))]
        assert year.uncovered_by(covered) == [
            DateRange(date(2025, 6, 1), date(2025, 8, 31)),
            DateRange(date(2025, 10, 1), date(2025, 12, 31)),
        ]

    def test_uncovered_by_without_ranges_is_whole_period(self) -> None:
        assert PERIOD.uncovered_by([]) == [PERIOD]

    def test_uncovered_by_handles_overlapping_and_unsorted_ranges(self) -> None:
        covered = [
            DateRange(date(2025, 7, 1), date(2026, 3, 31)),
            DateRange(date(2025, 1, 1), date(2025, 8, 15)),
            DateRange(date(2025, 2, 1), date(2025, 2, 28)),
        ]
        assert PERIOD.uncovered_by(covered) == []

    def test_uncovered_by_ignores_ranges_outside(self) -> None:
        assert PERIOD.uncovered_by([DateRange.for_year(2024), DateRange.for_year(2026)]) == [PERIOD]

    def test_shift_years_handles_leap_day(self) -> None:
        leap = DateRange(date(2024, 2, 29), date(2024, 12, 31))
        assert leap.shift_years(1) == DateRange(date(2025, 2, 28), date(2025, 12, 31))
        assert DateRange.for_year(2025).shift_years(-1) == DateRange.for_year(2024)


@pytest.fixture
def house_id(context: AppContext) -> int:
    """Ein Haus mit Vorjahresrechnungen und einer erst teilweise erfassten Abrechnung 2025."""
    house = context.properties.save_property(Property(name="Testhaus", address="Testweg 1, 12345 Teststadt"))
    assert house.id is not None

    def add(
        type_name: str,
        amount: str,
        start: date,
        end: date,
        description: str,
        invoice_date: date | None = None,
        excluded: bool = False,
    ) -> None:
        context.costs.save_cost(
            Cost(
                property_id=house.id,
                cost_type=context.cost_types.get_by_name(type_name),
                amount=Money.from_decimal(amount),
                period=DateRange(start, end),
                allocation_key=AllocationKey.AREA,
                description=description,
                invoice_date=invoice_date,
                excluded=excluded,
            )
        )

    # Wasser: Kalenderjahresrechnung, kam im Februar des Folgejahres.
    add("Wasserversorgung", "1500.00", date(2024, 1, 1), date(2024, 12, 31), "Wasserwerke Musterstadt", date(2025, 2, 14))
    # Strom: läuft von Juni bis Mai, kommt jeweils im Juni.
    add("Beleuchtung (Allgemeinstrom)", "360.00", date(2023, 6, 1), date(2024, 5, 31), "Stromversorger Nord", date(2024, 6, 20))
    add("Beleuchtung (Allgemeinstrom)", "365.00", date(2024, 6, 1), date(2025, 5, 31), "Stromversorger Nord", date(2025, 6, 18))
    # Müll: im Vorjahr ohne Rechnungsdatum erfasst.
    add("Müllbeseitigung", "800.00", date(2024, 1, 1), date(2024, 12, 31), "Abfallgebühren")
    # Versicherung: für 2025 bereits vollständig da.
    add("Sach- und Haftpflichtversicherung", "1200.00", date(2024, 1, 1), date(2024, 12, 31), "Allianz", date(2024, 1, 10))
    add("Sach- und Haftpflichtversicherung", "1260.00", date(2025, 1, 1), date(2025, 12, 31), "Allianz", date(2025, 1, 9))
    return house.id


def _item(checklist: InvoiceChecklist, type_name: str) -> ChecklistItem:
    return next(item for item in checklist.items if item.cost_type.name == type_name)


class TestChecklist:
    def test_missing_invoice_with_reminder_from_previous_year(self, context: AppContext, house_id: int) -> None:
        water = _item(context.invoice_checklist.build(house_id, PERIOD), "Wasserversorgung")

        assert water.relevant and not water.relevance_is_manual
        assert water.status is ChecklistStatus.MISSING
        assert water.coverage_percent == Decimal("0.0")
        assert water.missing == [PERIOD]
        assert [p.description for p in water.previous] == ["Wasserwerke Musterstadt"]
        # Vorjahresrechnung vom 14.02.2025 -> die nächste wird um den 14.02.2026 erwartet.
        assert water.expected_date == date(2026, 2, 14)

    def test_partial_coverage_shows_percent_and_gap(self, context: AppContext, house_id: int) -> None:
        power = _item(context.invoice_checklist.build(house_id, PERIOD), "Beleuchtung (Allgemeinstrom)")

        assert power.status is ChecklistStatus.PARTIAL
        assert power.missing == [DateRange(date(2025, 6, 1), date(2025, 12, 31))]
        # 01.01.–31.05.2025 = 151 von 365 Tagen
        assert power.covered_days == 151
        assert power.coverage_percent == Decimal("41.4")
        # Beide Stromrechnungen berühren das Vorjahr, aber nur die zweite
        # (Juni–Mai) hat einen Nachfolger, der in die Lücke fällt.
        assert len(power.previous) == 2
        assert [p.invoice_date for p in power.outstanding] == [date(2025, 6, 18)]
        assert power.expected_date == date(2026, 6, 18)
        # Erfasst ist bisher nur der Anteil Januar–Mai der zweiten Rechnung.
        assert power.amount_in_period == Money.from_decimal("365.00").allocate([151, 214])[0]

    def test_complete_and_not_relevant(self, context: AppContext, house_id: int) -> None:
        checklist = context.invoice_checklist.build(house_id, PERIOD)

        insurance = _item(checklist, "Sach- und Haftpflichtversicherung")
        assert insurance.status is ChecklistStatus.COMPLETE
        assert insurance.coverage_percent == Decimal("100.0")
        assert insurance.expected_date is None and not insurance.expectation_unknown

        # Nie eine Rechnung erfasst -> standardmäßig nicht relevant.
        tax = _item(checklist, "Grundsteuer")
        assert not tax.relevant and tax.status is ChecklistStatus.NOT_RELEVANT

    def test_ready_date_is_latest_expected_invoice(self, context: AppContext, house_id: int) -> None:
        checklist = context.invoice_checklist.build(house_id, PERIOD)

        assert {item.cost_type.name for item in checklist.relevant_items} == {
            "Wasserversorgung",
            "Beleuchtung (Allgemeinstrom)",
            "Müllbeseitigung",
            "Sach- und Haftpflichtversicherung",
        }
        assert not checklist.is_complete
        assert len(checklist.open_items) == 3
        # Wasser kommt im Februar, Strom erst im Juni -> Abrechnung ab Juni möglich.
        assert checklist.ready_date == date(2026, 6, 18)
        # Müll hat kein Rechnungsdatum im Vorjahr: keine Schätzung möglich.
        assert [item.cost_type.name for item in checklist.unknown_items] == ["Müllbeseitigung"]

    def test_relevance_can_be_overridden(self, context: AppContext, house_id: int) -> None:
        service = context.invoice_checklist
        tax_id = context.cost_types.get_by_name("Grundsteuer").id
        water_id = context.cost_types.get_by_name("Wasserversorgung").id
        assert tax_id is not None and water_id is not None

        service.set_relevant(house_id, tax_id, True)
        service.set_relevant(house_id, water_id, False)
        service.set_relevant(house_id, water_id, False)  # doppelt setzen legt nichts doppelt an
        checklist = service.build(house_id, PERIOD)

        tax = _item(checklist, "Grundsteuer")
        assert tax.relevant and tax.relevance_is_manual
        assert tax.status is ChecklistStatus.MISSING and tax.expectation_unknown
        water = _item(checklist, "Wasserversorgung")
        assert not water.relevant and water.status is ChecklistStatus.NOT_RELEVANT
        assert water not in checklist.open_items

        # Zurückstellen ändert den bestehenden Eintrag.
        service.set_relevant(house_id, water_id, True)
        assert _item(service.build(house_id, PERIOD), "Wasserversorgung").relevant

    def test_relevance_is_stored_per_property(self, context: AppContext, house_id: int) -> None:
        other = context.properties.save_property(Property(name="Zweithaus", address="Nebenweg 2, 12345 Teststadt"))
        tax_id = context.cost_types.get_by_name("Grundsteuer").id
        assert other.id is not None and tax_id is not None

        context.invoice_checklist.set_relevant(house_id, tax_id, True)

        assert not _item(context.invoice_checklist.build(other.id, PERIOD), "Grundsteuer").relevant

    def test_excluded_invoices_do_not_count(self, context: AppContext, house_id: int) -> None:
        insurance = next(
            c for c in context.costs.list_costs(house_id, PERIOD) if c.description == "Allianz" and c.period == PERIOD
        )
        assert insurance.id is not None
        context.costs.set_excluded(insurance.id, True)

        item = _item(context.invoice_checklist.build(house_id, PERIOD), "Sach- und Haftpflichtversicherung")

        assert item.status is ChecklistStatus.MISSING
        assert item.expected_date == date(2025, 1, 10)

    def test_complete_checklist(self, context: AppContext, house_id: int) -> None:
        # Für 2024 liegt alles vor, was 2024 oder 2023 berührt.
        checklist = context.invoice_checklist.build(house_id, DateRange.for_year(2024))

        assert checklist.is_complete
        assert checklist.ready_date is None and not checklist.unknown_items

    def test_non_calendar_period(self, context: AppContext, house_id: int) -> None:
        # Wirtschaftsjahr 01.06.2025–31.05.2026: Vorjahr ist 01.06.2024–31.05.2025.
        period = DateRange(date(2025, 6, 1), date(2026, 5, 31))
        power = _item(context.invoice_checklist.build(house_id, period), "Beleuchtung (Allgemeinstrom)")

        assert power.status is ChecklistStatus.MISSING
        assert [p.invoice_date for p in power.previous] == [date(2025, 6, 18)]
        assert power.expected_date == date(2026, 6, 18)


class TestGroupsForCustomPeriod:
    def test_one_group_per_cost_type_for_the_period(self, context: AppContext, house_id: int) -> None:
        period = DateRange(date(2024, 7, 1), date(2025, 6, 30))

        groups = context.invoice_groups.groups(house_id, period=period)
        insurance = next(g for g in groups if g.cost_type.name == "Sach- und Haftpflichtversicherung")

        assert len([g for g in groups if g.cost_type.id == insurance.cost_type.id]) == 1
        assert insurance.period == period
        assert "01.07.24–30.06.25" in insurance.name
        # Juli–Dezember 2024 aus der einen, Januar–Juni 2025 aus der anderen Rechnung.
        assert [p.part.period for p in insurance.parts] == [
            DateRange(date(2024, 7, 1), date(2024, 12, 31)),
            DateRange(date(2025, 1, 1), date(2025, 6, 30)),
        ]
        expected = (
            Money.from_decimal("1200.00").allocate([182, 184])[1] + Money.from_decimal("1260.00").allocate([181, 184])[0]
        )
        assert insurance.total_amount == expected

    def test_full_calendar_year_as_period_equals_year_filter(self, context: AppContext, house_id: int) -> None:
        by_period = context.invoice_groups.groups(house_id, period=PERIOD)
        by_year = context.invoice_groups.groups(house_id, 2025)

        assert [(g.name, g.total_amount) for g in by_period] == [(g.name, g.total_amount) for g in by_year]
        assert all(g.period is None for g in by_period)
