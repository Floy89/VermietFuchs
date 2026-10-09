"""Referenzfall: eine reale Jahresabrechnung (Schaltjahr 2024), anonymisiert.

Die Zahlen stammen aus einer von Hand in Excel erstellten Abrechnung für ein
Haus mit drei Wohnungen. Der Fall deckt alle Besonderheiten ab, die über die
einfache Flächenumlage hinausgehen:

* Einzug eines Mieters mitten im Jahr (14.03.), davor Leerstand,
* Heizung und Rauchwarnmelder als fertige Beträge eines Messdienstes
  (Schlüssel "Direkte Zuordnung"),
* Wasser-Mengenpreis nach Wohnungszählern in vier Teilzeiträumen,
* Wasser-Grundpreis zu gleichen Teilen je Wohneinheit,
* zwei Stromrechnungen, die über den Jahreswechsel laufen (zeitanteilig).

Für die beiden ganzjährig bewohnten Wohnungen muss die App exakt die
Excel-Ergebnisse liefern. Beim unterjährig eingezogenen Mieter weicht sie
bewusst leicht ab: Excel rechnete in Monaten (9,58/12), die App taggenau.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest

from app_context import AppContext
from models.apartment import Apartment
from models.billing import BillingResult
from models.cost import Cost
from models.enums import AllocationKey, MeterType
from models.meter_reading import MeterReading
from models.property import Property
from models.tenant import Tenant
from models.value_objects import DateRange, Money

YEAR = DateRange.for_year(2024)


def _money(value: str) -> Money:
    return Money.from_decimal(value)


@pytest.fixture()
def result(context: AppContext) -> BillingResult:
    """Baut den Referenzfall auf und berechnet die Abrechnung."""
    types = {t.name: t for t in context.cost_types.list_types()}
    house = context.properties.save_property(Property("Referenzhaus", "Beispielweg 1, 12345 Ort"))
    assert house.id is not None

    flats = {
        name: context.apartments.save_apartment(Apartment(house.id, name, Decimal(area)))
        for name, area in (("EG", "105"), ("OG", "158.66"), ("DG", "105"))
    }
    tenants = {
        name: context.tenants.save_tenant(
            Tenant(flats[name].id, label, start),  # type: ignore[arg-type]
            initial_prepayment=_money(prepayment),
        )
        for name, label, start, prepayment in (
            ("EG", "Mieter EG", date(2024, 3, 14), "330"),
            ("OG", "Nutzer OG", date(2020, 1, 1), "400"),
            ("DG", "Mieter DG", date(2020, 1, 1), "300"),
        )
    }
    ids = {name: tenant.id for name, tenant in tenants.items()}

    def add(type_name: str, amount: str, key: AllocationKey, period: DateRange = YEAR, **extra: object) -> None:
        context.costs.save_cost(Cost(house.id, types[type_name], _money(amount), period, key, **extra))  # type: ignore[arg-type]

    # --- Umlage nach Wohnfläche -------------------------------------------
    for type_name, amount in (
        ("Grundsteuer", "533.55"),
        ("Entwässerung", "1108.48"),
        ("Straßenreinigung", "170.10"),
        ("Müllbeseitigung", "508.40"),
        ("Sach- und Haftpflichtversicherung", "1928.61"),
        ("Sach- und Haftpflichtversicherung", "171.59"),
        ("Sonstige Betriebskosten", "11.00"),
    ):
        add(type_name, amount, AllocationKey.AREA)

    # --- Stromrechnungen über den Jahreswechsel ---------------------------
    add("Beleuchtung (Allgemeinstrom)", "261.09", AllocationKey.AREA, DateRange(date(2023, 6, 1), date(2024, 5, 31)))
    add("Beleuchtung (Allgemeinstrom)", "312.60", AllocationKey.AREA, DateRange(date(2024, 6, 1), date(2025, 5, 31)))

    # --- Messdienst-Abrechnung: fertige Beträge je Nutzer ---------------------
    add(
        "Verbundene Heizungs- und Warmwasserversorgung", "4484.66", AllocationKey.DIRECT,
        direct_shares={ids["EG"]: _money("968.38"), ids["OG"]: _money("1779.26"), ids["DG"]: _money("1189.14")},
    )
    add(
        "Rauchwarnmelder-Wartung", "321.68", AllocationKey.DIRECT,
        direct_shares={ids["EG"]: _money("51.41"), ids["OG"]: _money("64.26"), ids["DG"]: _money("86.94")},
    )

    # --- Wasser: Grundpreis je Wohneinheit, Mengenpreis nach Zählern ------------
    add("Wasserversorgung", "153.00", AllocationKey.UNITS, description="Grundpreis")
    for amount, start, end in (
        ("58.14", date(2024, 1, 1), date(2024, 2, 17)),
        ("27.13", date(2024, 2, 18), date(2024, 3, 13)),
        ("288.76", date(2024, 3, 14), date(2024, 9, 13)),
        ("164.73", date(2024, 9, 14), date(2024, 12, 31)),
    ):
        add("Wasserversorgung", amount, AllocationKey.CONSUMPTION, DateRange(start, end), description="Mengenpreis")

    reading_days = (date(2024, 1, 1), date(2024, 2, 17), date(2024, 3, 13), date(2024, 9, 13), date(2024, 12, 31))
    for name, values in (("EG", (0, 1, 1, 28, 43)), ("OG", (0, 0, 3, 34, 51)), ("DG", (0, 2, 9, 60, 91))):
        for day, value in zip(reading_days, values):
            context.meter_readings.save_reading(
                MeterReading(flats[name].id, MeterType.COLD_WATER, Decimal(value), day)  # type: ignore[arg-type]
            )

    return context.billing.create_billing(house.id, YEAR)


def test_full_year_tenants_match_excel(result: BillingResult) -> None:
    """Ganzjährig bewohnte Wohnungen: identisch mit der Excel-Abrechnung."""
    by_flat = {s.apartment.number: s for s in result.statements}
    assert by_flat["OG"].total_costs.amount == pytest.approx(Decimal("4062.06"), abs=Decimal("0.02"))
    assert by_flat["DG"].total_costs.amount == pytest.approx(Decimal("2946.31"), abs=Decimal("0.02"))
    # Volles Jahr: exakt zwölf Monatsraten.
    assert by_flat["OG"].prepayments == _money("4800.00")
    assert by_flat["DG"].prepayments == _money("3600.00")


def test_mid_year_tenant_is_day_exact(result: BillingResult) -> None:
    """Einzug am 14.03.: taggenau (293 von 366 Tagen) statt 9,58 von 12 Monaten."""
    tenant = {s.apartment.number: s for s in result.statements}["EG"]
    assert tenant.occupancy.days == 293
    # Excel (monatsbasiert): 2.245,56 EUR - die taggenaue Rechnung liegt wenige Euro darüber.
    assert Decimal("2245.56") < tenant.total_costs.amount < Decimal("2252.00")
    # 330 EUR monatlich: März anteilig (18 von 31 Tagen), danach neun volle Monate.
    assert tenant.prepayments == _money("3161.61")


def test_nothing_is_lost_and_no_warnings(result: BillingResult) -> None:
    assert not result.warnings
    # Rechnungssumme im Jahr: Excel 10.221,54 EUR (Rundung je Position: ±2 Cent).
    assert result.total_costs.amount == pytest.approx(Decimal("10221.54"), abs=Decimal("0.03"))
    assert result.total_allocated + result.vacancy_amount == result.total_costs
