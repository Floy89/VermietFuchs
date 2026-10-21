"""Nicht umlegbare Kosten: Der Eigentümer trägt sie allein (z. B. Miete der Rauchwarnmelder)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from PyQt6.QtWidgets import QApplication

from app_context import AppContext
from models.apartment import Apartment
from models.cost import Cost
from models.enums import AllocationKey
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money

YEAR = DateRange.for_year(2025)


def _house(context: AppContext) -> int:
    house = context.properties.save_property(Property(name="Haus", address="Weg 1, 12345 Ort"))
    assert house.id is not None
    for number, area in (("EG", "105"), ("1OG", "158")):
        flat = context.apartments.save_apartment(Apartment(house.id, number, Decimal(area), 2))
        assert flat.id is not None
        context.tenants.save_tenant(Tenant(apartment_id=flat.id, name=f"Mieter {number}", start_date=date(2024, 1, 1)))
    return house.id


def _cost(context: AppContext, house_id: int, type_name: str, amount: str, key: AllocationKey) -> Cost:
    return context.costs.save_cost(
        Cost(house_id, context.cost_types.get_by_name(type_name), Money.from_decimal(amount), YEAR, key,
             description="ista, Miete Rauchwarnmelder" if key is AllocationKey.OWNER else "")
    )


class TestOwnerCosts:
    def test_offered_for_every_cost_type(self, context: AppContext) -> None:
        for cost_type in context.cost_types.list_types():
            assert cost_type.allowed_keys()[-1] is AllocationKey.OWNER or cost_type.default_key is AllocationKey.OWNER
            assert AllocationKey.OWNER not in cost_type.override_keys()

    def test_stays_with_the_owner(self, context: AppContext) -> None:
        house_id = _house(context)
        _cost(context, house_id, "Rauchwarnmelder-Wartung", "81.17", AllocationKey.OWNER)
        _cost(context, house_id, "Grundsteuer", "263.00", AllocationKey.AREA)

        result = context.billing.create_billing(house_id, YEAR)

        assert result.total_costs == Money.from_decimal("344.17")
        assert result.total_allocated == Money.from_decimal("263.00")
        assert result.owner_amount == Money.from_decimal("81.17")
        assert result.unallocated_amount == Money.from_decimal("81.17")
        assert result.vacancy_amount.is_zero and not result.warnings
        # In keinem Mieterschreiben
        assert all(share.cost.allocation_key is not AllocationKey.OWNER
                   for statement in result.statements for share in statement.shares)

    def test_key_overrides_do_not_apply(self, context: AppContext) -> None:
        house_id = _house(context)
        cost = _cost(context, house_id, "Rauchwarnmelder-Wartung", "81.17", AllocationKey.OWNER)
        tenant = context.tenants.list_tenants(house_id)[0]
        assert tenant.id is not None and cost.cost_type.id is not None
        context.key_overrides.set_override(tenant.id, cost.cost_type.id, AllocationKey.AREA)

        result = context.billing.create_billing(house_id, YEAR)
        assert result.total_allocated.is_zero and result.owner_amount == Money.from_decimal("81.17")

    def test_dialog_hint(self, qt_app: QApplication, context: AppContext) -> None:
        from ui.dialogs.cost_dialog import CostDialog
        from ui.helpers import select_data

        house_id = _house(context)
        types = context.cost_types.list_types()
        dialog = CostDialog(context.costs.save_cost, types, [], house_id, YEAR)
        dialog._type_combo.setCurrentIndex(next(i for i, t in enumerate(types) if t.name == "Rauchwarnmelder-Wartung"))
        select_data(dialog._key_combo, AllocationKey.OWNER)
        dialog._amount_spin.set_money(Money.from_decimal("81.17"))
        assert "Eigentümer allein" in dialog._key_hint.text()
        dialog.accept()
        (stored,) = context.costs.list_costs(house_id)
        assert stored.allocation_key is AllocationKey.OWNER
