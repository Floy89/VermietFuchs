"""Kostenaufstellung im PDF: nur Kategorien, gleich verteilte Rechnungen einer Kategorie in einer Zeile."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from app_context import AppContext
from models.apartment import Apartment
from models.cost import Cost
from models.enums import AllocationKey
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from services.pdf_export_service import category_lines

YEAR = DateRange.for_year(2025)


def _billing(context: AppContext, move_in: date = date(2024, 1, 1)):  # type: ignore[no-untyped-def]
    house = context.properties.save_property(Property(name="Haus", address="Weg 1, 12345 Ort"))
    assert house.id is not None
    for number, area, start in (("EG", "105", move_in), ("1OG", "158", date(2024, 1, 1))):
        flat = context.apartments.save_apartment(Apartment(house.id, number, Decimal(area), 2))
        assert flat.id is not None
        context.tenants.save_tenant(Tenant(apartment_id=flat.id, name=f"Mieter {number}", start_date=start))
    light = context.cost_types.get_by_name("Beleuchtung (Allgemeinstrom)")
    for start, end, amount, supplier in (
        (date(2024, 6, 1), date(2025, 5, 31), "312.60", "Octopus Energy, Rechnung vom 16.06.2025"),
        (date(2025, 6, 1), date(2026, 5, 31), "329.11", "Octopus Energy"),
    ):
        context.costs.save_cost(Cost(house.id, light, Money.from_decimal(amount), DateRange(start, end),
                                     AllocationKey.AREA, description=supplier))
    context.costs.save_cost(Cost(house.id, context.cost_types.get_by_name("Grundsteuer"), Money.from_decimal("263.00"),
                                 YEAR, AllocationKey.AREA, description="Stadt Musterstadt"))
    return context.billing.create_billing(house.id, YEAR)


class TestCategoryLines:
    def test_only_categories_and_same_distribution_merged(self, context: AppContext) -> None:
        result = _billing(context)
        statement = next(s for s in result.statements if s.apartment.number == "EG")
        lines = category_lines(statement.shares)

        assert [line.title for line in lines] == ["Grundsteuer", "Beleuchtung (Allgemeinstrom)"]
        light = lines[1]
        assert light.total == sum((c.amount for c in result.costs if c.cost_type.name.startswith("Beleuchtung")),
                                  Money.zero())
        assert light.basis == "105 m² von 263 m²"
        assert not any("Octopus" in line.title or "Stadt" in line.title for line in lines)
        assert sum((line.amount for line in lines), Money.zero()) == statement.total_costs

    def test_partial_occupancy_keeps_separate_lines(self, context: AppContext) -> None:
        # Einzug am 01.03.2025: Die beiden Stromrechnungen haben unterschiedliche Tagesanteile.
        result = _billing(context, move_in=date(2025, 3, 1))
        statement = next(s for s in result.statements if s.apartment.number == "EG")
        lines = category_lines(statement.shares)
        assert [line.title for line in lines].count("Beleuchtung (Allgemeinstrom)") == 2
        assert sum((line.amount for line in lines), Money.zero()) == statement.total_costs
