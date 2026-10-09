"""Beispieldaten zum Ausprobieren der Anwendung.

Verwendung von der Kommandozeile (legt einen Demo-Benutzer an)::

    python -m tools.demo_data "C:\\Pfad\\zum\\Zielordner"

Die Funktion :func:`seed_demo_data` wird außerdem von den Tests und vom
Screenshot-Skript benutzt.
"""

from __future__ import annotations

import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

# Erlaubt den direkten Aufruf "python tools/demo_data.py" aus dem Projektordner.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config  # noqa: E402
from app_context import AppContext  # noqa: E402
from models.apartment import Apartment  # noqa: E402
from models.cost import Cost, InvoiceSplit  # noqa: E402
from models.enums import AllocationKey, MeterType  # noqa: E402
from models.meter_reading import MeterReading  # noqa: E402
from models.prepayment_rate import PrepaymentRate  # noqa: E402
from models.property import Property  # noqa: E402
from models.settings import LandlordSettings  # noqa: E402
from models.tenant import Tenant  # noqa: E402
from models.value_objects import DateRange, Money  # noqa: E402
from services.user_service import UserService  # noqa: E402
from utils.config_manager import ConfigManager  # noqa: E402

DEMO_USER_NAME = "Demo_Vermieter"


def seed_demo_data(context: AppContext, billing_year: int | None = None) -> None:
    """Füllt eine leere Datenbank mit zwei Häusern, Mietern, Zählerständen und Rechnungen.

    Args:
        context: Kontext einer (leeren) Benutzer-Datenbank.
        billing_year: Abrechnungsjahr der Beispieldaten (Standard: Vorjahr).
    """
    year = billing_year or date.today().year - 1
    period = DateRange.for_year(year)
    # Kostenarten aus dem mitgelieferten Katalog, über ihren Namen ansprechbar.
    types = {cost_type.name: cost_type for cost_type in context.cost_types.list_types()}

    context.settings.save_landlord(
        LandlordSettings(
            name="Max Beispiel",
            street="Am Stadtpark 7",
            city="50667 Köln",
            phone="0221 1234567",
            email="vermietung@example.org",
            bank_name="Musterbank Köln",
            iban="DE02 1203 0000 0000 2020 51",
            payment_term_days=30,
        )
    )

    # --- Haus 1: vier Wohnungen, ein Mieterwechsel mitten im Jahr -----------
    house = context.properties.save_property(
        Property(name="Musterstraße 12", address="Musterstraße 12, 50667 Köln")
    )
    assert house.id is not None
    flats = [
        context.apartments.save_apartment(Apartment(house.id, number, Decimal(area), persons))
        for number, area, persons in (
            ("EG links", "62.50", 2),
            ("EG rechts", "58.00", 1),
            ("1. OG links", "74.20", 3),
            ("1. OG rechts", "81.30", 4),
        )
    ]
    tenant_specs = [
        (flats[0], "Anna Schneider", date(year - 3, 4, 1), None, "520.00", "150.00"),
        (flats[1], "Jonas Weber", date(year - 1, 9, 1), date(year, 6, 30), "480.00", "120.00"),
        (flats[1], "Miriam Koch", date(year, 8, 1), None, "510.00", "125.00"),
        (flats[2], "Familie Yilmaz", date(year - 5, 1, 1), None, "690.00", "195.00"),
        (flats[3], "Familie Hoffmann", date(year - 2, 3, 1), None, "760.00", "215.00"),
    ]
    tenants = _create_tenants(context, tenant_specs)

    # Erhöhung der Vorauszahlung zur Jahresmitte: Die bisherige Rate endet
    # automatisch am Vortag, die Historie bleibt erhalten.
    hoffmann = tenants[-1]
    assert hoffmann.id is not None
    context.prepayment_rates.save_rate(
        PrepaymentRate(hoffmann.id, Money.from_decimal("230.00"), date(year, 7, 1), note="Anpassung nach Abrechnung")
    )

    heating = [("11250", "17980"), ("8420", "13110"), ("20310", "29480"), ("15600", "25940")]
    water = [("412.5", "498.1"), ("230.0", "281.4"), ("655.2", "786.9"), ("801.7", "967.3")]
    _create_readings(context, flats, MeterType.HEATING, heating, year)
    _create_readings(context, flats, MeterType.COLD_WATER, water, year)

    for type_name, amount, key, description in (
        ("Verbundene Heizungs- und Warmwasserversorgung", "6480.00", AllocationKey.CONSUMPTION_AREA, "Stadtwerke Gas"),
        ("Wasserversorgung", "1512.40", AllocationKey.CONSUMPTION, "Stadtwerke"),
        ("Müllbeseitigung", "894.60", AllocationKey.PERSONS, "Abfallgebühren"),
        ("Sach- und Haftpflichtversicherung", "1260.00", AllocationKey.AREA, "Gebäudeversicherung"),
        ("Grundsteuer", "742.80", AllocationKey.AREA, ""),
        ("Gebäudereinigung", "528.00", AllocationKey.AREA, "Treppenhaus"),
    ):
        context.costs.save_cost(
            Cost(house.id, types[type_name], Money.from_decimal(amount), period, key, 70, description)
        )

    # --- Haus 2: zwei Wohnungen -------------------------------------------
    house2 = context.properties.save_property(
        Property(name="Lindenweg 3", address="Lindenweg 3, 50859 Köln")
    )
    assert house2.id is not None
    flats2 = [
        context.apartments.save_apartment(Apartment(house2.id, number, Decimal(area), persons))
        for number, area, persons in (("Wohnung 1", "95.00", 3), ("Wohnung 2", "48.50", 1))
    ]
    tenants2 = _create_tenants(
        context,
        [
            (flats2[0], "Dr. Petra Lang", date(year - 4, 7, 1), None, "980.00", "230.00"),
            (flats2[1], "Tom Becker", date(year, 3, 1), None, "450.00", "95.00"),
        ],
    )
    tenants += tenants2
    _create_readings(context, flats2, MeterType.HEATING, [("30120", "41870"), ("5210", "9940")], year)
    for type_name, amount, key, description in (
        ("Heizung", "3190.00", AllocationKey.CONSUMPTION_AREA, "Fernwärme"),
        ("Wasserversorgung", "688.00", AllocationKey.PERSONS, "Stadtwerke"),
        ("Müllbeseitigung", "412.00", AllocationKey.PERSONS, "Abfallgebühren"),
        ("Sach- und Haftpflichtversicherung", "655.00", AllocationKey.AREA, "Gebäudeversicherung"),
    ):
        context.costs.save_cost(
            Cost(house2.id, types[type_name], Money.from_decimal(amount), period, key, 70, description)
        )

    # Rechnung über den Jahreswechsel (01.06.–31.05.): zählt nur taggenau anteilig.
    context.costs.save_cost(
        Cost(
            house2.id,
            types["Beleuchtung (Allgemeinstrom)"],
            Money.from_decimal("365.00"),
            DateRange(date(year - 1, 6, 1), date(year, 5, 31)),
            AllocationKey.AREA,
            description="Stadtwerke Strom",
        )
    )
    # Direkte Zuordnung: Beträge aus der Einzelabrechnung eines Messdienstes.
    lang, becker = tenants2
    assert lang.id is not None and becker.id is not None
    context.costs.save_cost(
        Cost(
            house2.id,
            types["Rauchwarnmelder-Wartung"],
            Money.from_decimal("96.00"),
            period,
            AllocationKey.DIRECT,
            description="Messdienst",
            direct_shares={lang.id: Money.from_decimal("48.00"), becker.id: Money.from_decimal("40.00")},
        )
    )

    # --- Rechnungsmanagement: Gasrechnungen über den Jahreswechsel -----------------
    # Sie werden mit Verbrauch erfasst und je Kalenderjahr zusammengefasst
    # (Meldung an den Messdienst). Umgelegt wird stattdessen dessen Abrechnung,
    # deshalb sind die aggregierten Gasrechnungen von der Abrechnung ausgeschlossen.
    gas = types["Brennstoffkosten (Gas/Öl)"]
    # Die erste Rechnung hat Splits (Ableseabschnitte laut Versorger): Ihre
    # Jahreszuordnung folgt den Abschnitten. Die zweite hat keine und wird
    # taggenau linear verteilt.
    gas_invoices = (
        ("1840.00", date(year - 1, 11, 1), date(year, 10, 31), "16800", [
            (date(year - 1, 11, 1), date(year - 1, 12, 31), "420.00", "3900"),
            (date(year, 1, 1), date(year, 3, 31), "780.00", "7100"),
            (date(year, 4, 1), date(year, 10, 31), "640.00", "5800"),
        ]),
        ("2010.00", date(year, 11, 1), date(year + 1, 10, 31), "17900", []),
    )
    for amount, start, end, kwh, splits in gas_invoices:
        context.costs.save_cost(
            Cost(
                house2.id,
                gas,
                Money.from_decimal(amount),
                DateRange(start, end),
                AllocationKey.CONSUMPTION_AREA,
                description="Gasversorger Jahresrechnung",
                invoice_date=date(end.year, 11, 12),
                consumption=Decimal(kwh),
                consumption_unit="kWh",
                splits=[
                    InvoiceSplit(DateRange(s_start, s_end), Money.from_decimal(s_amount), Decimal(s_kwh))
                    for s_start, s_end, s_amount, s_kwh in splits
                ],
            )
        )
    for group in context.invoice_groups.groups(house2.id):
        if group.cost_type.id == gas.id:
            context.invoice_groups.set_excluded(group, True)


def _create_tenants(
    context: AppContext,
    specs: list[tuple[Apartment, str, date, date | None, str, str]],
) -> list[Tenant]:
    """Legt Mieter aus (Wohnung, Name, Beginn, Ende, Miete, Vorauszahlung) an."""
    created: list[Tenant] = []
    for apartment, name, start, end, rent, prepayment in specs:
        assert apartment.id is not None
        contact = f"{name.split()[-1].lower()}@example.org"
        created.append(
            context.tenants.save_tenant(
                Tenant(
                    apartment_id=apartment.id,
                    name=name,
                    start_date=start,
                    end_date=end,
                    contact=contact,
                    rent_amount=Money.from_decimal(rent),
                ),
                initial_prepayment=Money.from_decimal(prepayment),
            )
        )
    return created


def _create_readings(
    context: AppContext,
    apartments: list[Apartment],
    meter_type: MeterType,
    values: list[tuple[str, str]],
    year: int,
) -> None:
    """Legt je Wohnung einen Anfangs- und einen Endstand für das Jahr an."""
    for apartment, (start_value, end_value) in zip(apartments, values):
        assert apartment.id is not None
        for value, day in ((start_value, date(year, 1, 2)), (end_value, date(year, 12, 30))):
            context.meter_readings.save_reading(
                MeterReading(apartment.id, meter_type, Decimal(value), day, reader="Hausverwaltung")
            )


def main() -> None:
    """Legt einen Demo-Benutzer im angegebenen Ordner an und befüllt ihn."""
    if len(sys.argv) != 2:
        print('Aufruf: python -m tools.demo_data "<Zielordner>"')
        raise SystemExit(2)
    service = UserService(ConfigManager(config.config_file()))
    entry = service.create_user(DEMO_USER_NAME, Path(sys.argv[1]))
    context = service.open_user(entry)
    try:
        seed_demo_data(context)
    finally:
        context.close()
    print(f"Demo-Benutzer '{entry.name}' angelegt: {entry.database_path}")


if __name__ == "__main__":
    main()
