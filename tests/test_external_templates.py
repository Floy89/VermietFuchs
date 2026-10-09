"""Gegenprobe mit fremden Excel-Vorlagen.

Zwei frei verfügbare Excel-Vorlagen (Nebenkosten- und Heizkostenabrechnung,
whk-controlling.de) wurden mit 15 Datensätzen gefüllt und von Excel selbst
durchgerechnet. Eingaben und Excel-Ergebnisse liegen in ``tests/data``; die
Vorlagen selbst sind nicht Teil des Projekts.

Der Test rechnet dieselben Datensätze in der App und verlangt, dass jeder
Einzelwert höchstens einen Cent vom Excel-Wert abweicht. Ein Cent ist
unvermeidlich: Excel rundet jeden Anteil für sich, die App verteilt so, dass
die Summe aller Anteile exakt die Rechnung ergibt.

Abgedeckt: Umlage nach Wohnfläche und Personenzahl, Leerstand, Heizkosten
mit Grund- und Verbrauchsanteil (50/60/70 %), Vorauszahlungen für ein volles
Jahr. NICHT abgedeckt (die Vorlagen können es nicht): Mieterwechsel,
Rechnungen über den Jahreswechsel, Wohneinheiten-Schlüssel, direkte Zuordnung.
"""

from __future__ import annotations

import json
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any, Iterator

import pytest

from app_context import AppContext
from database.db_manager import IN_MEMORY, DatabaseManager
from models.apartment import Apartment
from models.billing import BillingResult
from models.cost import Cost
from models.enums import AllocationKey, MeterType
from models.meter_reading import MeterReading
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from services.user_service import UserService

DATA = Path(__file__).parent / "data"
VARIANTS = json.loads((DATA / "template_variants.json").read_text(encoding="utf-8"))
EXCEL = json.loads((DATA / "template_excel_results.json").read_text(encoding="utf-8-sig"))
YEAR = DateRange.for_year(2024)

#: Zelle der Nebenkosten-Vorlage -> (Kostenart der App, Umlageschlüssel).
#: Die Reihenfolge entspricht den Zeilen der Einzelabrechnung in der Vorlage.
NK_CELLS: dict[str, tuple[str, AllocationKey]] = {
    "B4": ("Grundsteuer", AllocationKey.AREA),
    "B5": ("Wasserversorgung", AllocationKey.PERSONS),
    "B8": ("Müllbeseitigung", AllocationKey.PERSONS),
    "B9": ("Straßenreinigung", AllocationKey.AREA),
    "B10": ("Sach- und Haftpflichtversicherung", AllocationKey.AREA),
    "B11": ("Sach- und Haftpflichtversicherung", AllocationKey.AREA),
    "B12": ("Hauswart", AllocationKey.AREA),
    "B13": ("Gebäudereinigung", AllocationKey.AREA),
    "B14": ("Gartenpflege", AllocationKey.AREA),
    "B15": ("Beleuchtung (Allgemeinstrom)", AllocationKey.AREA),
    "B16": ("Schornsteinreinigung", AllocationKey.AREA),
}


def _excel_cents(value: float) -> int:
    """Rundet einen Excel-Wert kaufmännisch auf ganze Cent."""
    return int((Decimal(repr(value)) * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _assert_close(label: str, excel_value: float, app_cents: int) -> None:
    assert abs(app_cents - _excel_cents(excel_value)) <= 1, (
        f"{label}: Excel {excel_value:.4f} EUR, App {app_cents / 100:.2f} EUR"
    )


@pytest.fixture()
def fresh_context() -> Iterator[AppContext]:
    database = DatabaseManager(IN_MEMORY)
    database.open()
    app_context = AppContext(database, UserService._ensure_owner(database, "Vergleich"))
    yield app_context
    app_context.close()


@pytest.mark.parametrize(
    ("variant", "excel"),
    list(zip(VARIANTS["nk"], EXCEL["nk"])),
    ids=[v["name"] for v in VARIANTS["nk"]],
)
def test_nebenkosten_template(fresh_context: AppContext, variant: dict[str, Any], excel: dict[str, Any]) -> None:
    """Wohnfläche, Personenzahl, Leerstand und Vorauszahlungen wie in der Excel-Vorlage."""
    context = fresh_context
    types = {t.name: t for t in context.cost_types.list_types()}
    house = context.properties.save_property(Property("Haus", "Adresse"))
    assert house.id is not None

    tenant_of_unit: dict[int, int] = {}
    for index in range(6):
        persons = variant["persons"][index]
        flat = context.apartments.save_apartment(
            Apartment(house.id, f"W{index + 1}", Decimal(str(variant["areas"][index])), persons or 0)
        )
        assert flat.id is not None
        if persons is not None:  # ``None`` = Leerstand: Fläche zählt mit, aber es gibt keinen Mieter
            tenant = context.tenants.save_tenant(
                Tenant(flat.id, f"Mieter {index + 1}", date(2020, 1, 1)),
                initial_prepayment=Money.from_decimal(str(variant["rates"][index])),
            )
            assert tenant.id is not None
            tenant_of_unit[index] = tenant.id

    for cell, (type_name, key) in NK_CELLS.items():
        amount = Money.from_decimal(str(variant["costs"][cell]))
        context.costs.save_cost(Cost(house.id, types[type_name], amount, YEAR, key, description=cell))

    result: BillingResult = context.billing.create_billing(house.id, YEAR)
    statements = {s.tenant.id: s for s in result.statements}

    for excel_tenant in excel["tenants"]:
        statement = statements[tenant_of_unit[excel_tenant["unit"]]]
        app_lines = {share.cost.description: share.amount.cents for share in statement.shares}
        for cell, excel_line in zip(NK_CELLS, excel_tenant["lines"]):
            _assert_close(f"W{excel_tenant['unit'] + 1} {cell}", excel_line, app_lines[cell])
        _assert_close("Vorauszahlungen", excel_tenant["prepaid"], statement.prepayments.cents)

    # Was Excel nicht prüft: In der App geht die Rechnungssumme immer exakt auf.
    assert result.total_allocated + result.vacancy_amount == result.total_costs


@pytest.mark.parametrize(
    ("variant", "excel"),
    list(zip(VARIANTS["hk"], EXCEL["hk"])),
    ids=[v["name"] for v in VARIANTS["hk"]],
)
def test_heizkosten_template(fresh_context: AppContext, variant: dict[str, Any], excel: dict[str, Any]) -> None:
    """Grundkosten nach Fläche plus Verbrauchskosten wie in der Excel-Vorlage."""
    context = fresh_context
    types = {t.name: t for t in context.cost_types.list_types()}
    house = context.properties.save_property(Property("Haus", "Adresse"))
    assert house.id is not None

    tenant_of_unit: dict[int, int] = {}
    for index in range(5):
        flat = context.apartments.save_apartment(
            Apartment(house.id, f"W{index + 1}", Decimal(str(variant["areas"][index])))
        )
        assert flat.id is not None
        tenant = context.tenants.save_tenant(Tenant(flat.id, f"Mieter {index + 1}", date(2020, 1, 1)))
        assert tenant.id is not None
        tenant_of_unit[index] = tenant.id
        for value, day in ((0, date(2024, 1, 1)), (variant["cons"][index], date(2024, 12, 31))):
            context.meter_readings.save_reading(
                MeterReading(flat.id, MeterType.HEATING, Decimal(str(value)), day)
            )

    consumption_share = round((1 - variant["x"]) * 100)
    context.costs.save_cost(
        Cost(
            house.id,
            types["Heizung"],
            Money.from_decimal(str(variant["total"])),
            YEAR,
            AllocationKey.CONSUMPTION_AREA,
            consumption_share,
        )
    )
    result = context.billing.create_billing(house.id, YEAR)
    statements = {s.tenant.id: s for s in result.statements}

    assert not result.warnings
    for excel_tenant in excel["tenants"]:
        statement = statements[tenant_of_unit[excel_tenant["unit"]]]
        parts = {share.component: share.amount.cents for share in statement.shares}
        _assert_close("Grundkosten", excel_tenant["base"], parts["Wohnfläche"])
        _assert_close("Verbrauchskosten", excel_tenant["usage"], parts["Verbrauch"])
    assert result.total_allocated == result.total_costs
