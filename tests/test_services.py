"""Integrationstests: Services + Repositories + SQLite (Speicher-Datenbank)."""

from __future__ import annotations

import json
import time
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from app_context import AppContext
from models.apartment import Apartment
from models.cost import Cost
from models.enums import AllocationKey, AuditAction, MeterType, PhotoCategory
from models.exceptions import NotFoundError, ValidationError
from models.meter_reading import MeterReading
from models.property import Property
from models.settings import LandlordSettings
from models.tenant import Tenant
from models.value_objects import DateRange, Money
from services.user_service import UserService
from tools.demo_data import seed_demo_data
from utils.config_manager import ConfigManager

YEAR = 2025
PERIOD = DateRange.for_year(YEAR)


@pytest.fixture()
def demo(context: AppContext) -> AppContext:
    seed_demo_data(context, billing_year=YEAR)
    return context


class TestCrudAndRules:
    def test_property_roundtrip_and_audit(self, context: AppContext) -> None:
        prop = context.properties.save_property(Property(name=" Haus A ", address="Weg 1, 12345 Ort"))
        assert prop.id is not None and prop.name == "Haus A"

        prop.name = "Haus B"
        context.properties.save_property(prop)
        assert context.properties.get_property(prop.id).name == "Haus B"

        entries = context.audit.list_entries(table_name="properties")
        assert [entry.action for entry in entries] == [AuditAction.UPDATE, AuditAction.INSERT]
        assert entries[0].user_name == "Testbenutzer"
        assert json.loads(entries[0].old_value or "{}")["name"] == "Haus A"
        assert json.loads(entries[0].new_value or "{}")["name"] == "Haus B"

    def test_property_with_apartments_cannot_be_deleted(self, context: AppContext) -> None:
        prop = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert prop.id is not None
        apartment = context.apartments.save_apartment(Apartment(prop.id, "1", Decimal("50")))
        with pytest.raises(ValidationError):
            context.properties.delete_property(prop.id)
        assert apartment.id is not None
        context.apartments.delete_apartment(apartment.id)
        context.properties.delete_property(prop.id)
        with pytest.raises(NotFoundError):
            context.properties.get_property(prop.id)

    def test_duplicate_apartment_number_is_rejected(self, context: AppContext) -> None:
        prop = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert prop.id is not None
        context.apartments.save_apartment(Apartment(prop.id, "EG", Decimal("50")))
        with pytest.raises(ValidationError):
            context.apartments.save_apartment(Apartment(prop.id, "eg", Decimal("60")))

    def test_overlapping_tenants_are_rejected(self, context: AppContext) -> None:
        prop = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert prop.id is not None
        apartment = context.apartments.save_apartment(Apartment(prop.id, "1", Decimal("50")))
        assert apartment.id is not None
        context.tenants.save_tenant(Tenant(apartment.id, "Alt", date(2020, 1, 1), date(2025, 6, 30)))
        with pytest.raises(ValidationError):
            context.tenants.save_tenant(Tenant(apartment.id, "Neu", date(2025, 6, 30)))
        context.tenants.save_tenant(Tenant(apartment.id, "Neu", date(2025, 7, 1)))

    def test_failed_photo_rolls_back_reading(self, context: AppContext) -> None:
        """Zählerstand + Foto sind atomar: ungültiges Foto -> auch kein Zählerstand."""
        prop = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert prop.id is not None
        apartment = context.apartments.save_apartment(Apartment(prop.id, "1", Decimal("50")))
        assert apartment.id is not None
        reading = MeterReading(apartment.id, MeterType.HEATING, Decimal("10"), date(2025, 1, 1))
        too_big = b"x" * (16 * 1024 * 1024)
        with pytest.raises(ValidationError):
            context.meter_readings.save_reading(reading, "gross.jpg", too_big)
        assert context.meter_readings.list_readings() == []

    def test_reading_with_photo_and_cleanup(self, context: AppContext) -> None:
        prop = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert prop.id is not None
        apartment = context.apartments.save_apartment(Apartment(prop.id, "1", Decimal("50")))
        assert apartment.id is not None
        reading = context.meter_readings.save_reading(
            MeterReading(apartment.id, MeterType.HEATING, Decimal("10"), date(2025, 1, 1)),
            "zaehler.jpg",
            b"\xff\xd8fake-jpeg",
        )
        assert reading.id is not None
        photos = context.meter_readings.photos_of_reading(reading.id)
        assert len(photos) == 1 and photos[0].image_data == b"" and photos[0].size_bytes == 11
        assert photos[0].id is not None
        assert context.photos.load_photo(photos[0].id).image_data == b"\xff\xd8fake-jpeg"
        # Bilddaten dürfen nicht im Änderungsprotokoll landen.
        log = context.audit.list_entries(table_name="photos")[0]
        assert "<11 Bytes>" in (log.new_value or "")

        context.meter_readings.delete_reading(reading.id)
        assert context.meter_readings.reading_ids_with_photos() == set()

    def test_standalone_photo(self, context: AppContext) -> None:
        prop = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert prop.id is not None
        apartment = context.apartments.save_apartment(Apartment(prop.id, "1", Decimal("50")))
        assert apartment.id is not None
        context.photos.add_photo(apartment.id, PhotoCategory.DEFECT, "Schimmel Bad", "bad.png", b"png")
        assert [p.description for p in context.photos.list_documentation()] == ["Schimmel Bad"]

    def test_settings_roundtrip(self, context: AppContext) -> None:
        context.settings.save_landlord(LandlordSettings(name="V. Mieter", iban="DE00", payment_term_days=14))
        loaded = context.settings.load_landlord()
        assert (loaded.name, loaded.iban, loaded.payment_term_days) == ("V. Mieter", "DE00", 14)
        context.settings.set_theme("dark")
        assert context.settings.theme() == "dark"


class TestBilling:
    def test_every_cent_is_accounted_for(self, demo: AppContext) -> None:
        for prop in demo.properties.list_properties():
            assert prop.id is not None
            result = demo.billing.create_billing(prop.id, PERIOD)
            assert result.costs, "Demo-Daten sollten Kosten enthalten"
            assert result.total_allocated + result.vacancy_amount == result.total_costs

    def test_tenant_change_is_split_by_days(self, demo: AppContext) -> None:
        house = demo.properties.list_properties()[1]  # "Musterstraße 12"
        assert house.name == "Musterstraße 12" and house.id is not None
        result = demo.billing.create_billing(house.id, PERIOD)

        by_name = {statement.tenant.name: statement for statement in result.statements}
        assert set(by_name) == {"Anna Schneider", "Jonas Weber", "Miriam Koch", "Familie Yilmaz", "Familie Hoffmann"}
        assert by_name["Jonas Weber"].occupancy.days == 181
        assert by_name["Miriam Koch"].occupancy.days == 153
        # Der Juli steht die Wohnung leer -> Eigentümeranteil.
        assert result.vacancy_amount.cents > 0
        # Vorauszahlungen aus den Raten: sechs bzw. fünf volle Monate.
        assert by_name["Jonas Weber"].prepayments == Money.from_decimal("720.00")
        assert by_name["Miriam Koch"].prepayments == Money.from_decimal("625.00")
        assert by_name["Anna Schneider"].prepayments == Money.from_decimal("1800.00")
        assert not result.warnings

    def test_invoice_across_year_end_counts_pro_rata(self, demo: AppContext) -> None:
        """Rechnung 01.07.–30.06.: Im Abrechnungsjahr zählen nur 184 von 365 Tagen."""
        house = demo.properties.list_properties()[1]
        assert house.id is not None
        before = demo.billing.create_billing(house.id, PERIOD)
        demo.costs.save_cost(
            Cost(
                house.id,
                demo.cost_types.get_by_name("Gartenpflege"),
                Money.from_decimal("365.00"),
                DateRange(date(YEAR, 7, 1), date(YEAR + 1, 6, 30)),
                AllocationKey.AREA,
            )
        )
        result = demo.billing.create_billing(house.id, PERIOD)
        assert result.total_costs - before.total_costs == Money.from_decimal("184.00")
        assert result.total_allocated + result.vacancy_amount == result.total_costs
        # Im Folgejahr zählt der Rest - zusammen wieder die volle Rechnung.
        next_year = demo.billing.create_billing(house.id, DateRange.for_year(YEAR + 1))
        garden = [c for c in next_year.costs if c.cost_type.name == "Gartenpflege"]
        assert [c.amount for c in garden] == [Money.from_decimal("181.00")]

    def test_direct_assignment_and_prepayments_from_rates(self, demo: AppContext) -> None:
        house = demo.properties.list_properties()[0]  # "Lindenweg 3"
        assert house.name == "Lindenweg 3" and house.id is not None
        result = demo.billing.create_billing(house.id, PERIOD)
        by_name = {s.tenant.name: s for s in result.statements}

        direct = [sh for sh in by_name["Tom Becker"].shares if sh.cost.allocation_key is AllocationKey.DIRECT]
        assert [sh.amount for sh in direct] == [Money.from_decimal("40.00")]
        # Einzug 01.03., 95 EUR monatlich -> zehn volle Monate.
        assert by_name["Tom Becker"].prepayments == Money.from_decimal("950.00")
        assert by_name["Dr. Petra Lang"].prepayments == Money.from_decimal("2760.00")

    def test_excluded_invoices_are_not_billed(self, demo: AppContext) -> None:
        """Ausschluss wirkt auf zwei Ebenen: einzelne Rechnung und aggregierte Rechnung."""
        house = demo.properties.list_properties()[0]  # "Lindenweg 3" mit den Gasrechnungen
        assert house.id is not None
        result = demo.billing.create_billing(house.id, PERIOD)
        # Die aggregierte Gasrechnung ist ausgeschlossen -> keine Gaskosten in der Abrechnung.
        assert all("Brennstoff" not in cost.cost_type.name for cost in result.costs)

        # Aggregierte Rechnung wieder einbeziehen: beide Gasrechnungen zählen anteilig.
        gas_group = next(
            g for g in demo.invoice_groups.groups(house.id, YEAR) if "Brennstoff" in g.cost_type.name
        )
        demo.invoice_groups.set_excluded(gas_group, False)
        with_gas = demo.billing.create_billing(house.id, PERIOD)
        # Rechnung 1 nach Splits: 780,00 + 640,00; Rechnung 2 linear: 2.010,00 * 61/365 Tage
        assert with_gas.total_costs - result.total_costs == Money.from_decimal("1755.92")

        # Einzelne Rechnung ausschließen: nur noch der Anteil der anderen bleibt.
        first_invoice = gas_group.parts[0].invoice
        assert first_invoice.id is not None
        demo.costs.set_excluded(first_invoice.id, True)
        one_left = demo.billing.create_billing(house.id, PERIOD)
        assert one_left.total_costs - result.total_costs == Money.from_decimal("335.92")

    def test_invoice_groups_split_amount_and_consumption_by_year(self, demo: AppContext) -> None:
        house = demo.properties.list_properties()[0]
        assert house.id is not None
        groups = {g.year: g for g in demo.invoice_groups.groups(house.id) if "Brennstoff" in g.cost_type.name}
        assert set(groups) == {YEAR - 1, YEAR, YEAR + 1}

        this_year = groups[YEAR]
        assert this_year.excluded and len(this_year.parts) == 2
        assert this_year.total_amount == Money.from_decimal("1755.92")
        # Rechnung 1 laut Splits 7.100 + 5.800 kWh, Rechnung 2 linear 17.900 kWh * 61/365 -
        # der Wert für die Meldung an den Messdienst
        assert this_year.total_consumption == Decimal("12900") + Decimal("2991.507")
        assert this_year.consumption_unit == "kWh"
        # Über alle Jahre verteilt ergibt jede Rechnung wieder ihren vollen Betrag.
        total = sum((g.total_amount for g in groups.values()), Money.zero())
        assert total == Money.from_decimal("3850.00")

        demo.invoice_groups.rename(this_year, "Gas")
        renamed = next(g for g in demo.invoice_groups.groups(house.id, YEAR) if g.cost_type.id == this_year.cost_type.id)
        assert renamed.name == "Gas" and renamed.excluded

    def test_billed_period_can_be_adjusted_per_tenancy(self, demo: AppContext) -> None:
        """Abrechnungszeitraum je Mietverhältnis: Standard = Vertrag, Anpassung von Hand möglich."""
        house = demo.properties.list_properties()[1]  # "Musterstraße 12"
        assert house.id is not None
        before = demo.billing.create_billing(house.id, PERIOD)
        anna_before = next(s for s in before.statements if s.tenant.name == "Anna Schneider")
        others_before = {s.tenant.name: s.total_costs for s in before.statements if s.tenant.name != "Anna Schneider"}
        assert not anna_before.is_adjusted and anna_before.occupancy == PERIOD
        assert anna_before.tenant.id is not None

        half_year = DateRange(date(YEAR, 1, 1), date(YEAR, 6, 30))
        demo.billing.set_billed_period(anna_before.tenant.id, PERIOD, half_year)
        after = demo.billing.create_billing(house.id, PERIOD)
        anna_after = next(s for s in after.statements if s.tenant.name == "Anna Schneider")

        assert anna_after.is_adjusted and anna_after.occupancy == half_year
        assert anna_after.contract_occupancy == PERIOD
        assert anna_after.total_costs < anna_before.total_costs
        assert anna_after.prepayments == Money.from_decimal("900.00")  # sechs Raten zu 150
        # Die anderen Mieter zahlen deshalb keinen Cent mehr - der Rest bleibt beim Eigentümer.
        assert {s.tenant.name: s.total_costs for s in after.statements if s.tenant.name != "Anna Schneider"} == others_before
        assert after.total_allocated + after.vacancy_amount == after.total_costs

        with pytest.raises(ValidationError):
            demo.billing.set_billed_period(
                anna_before.tenant.id, PERIOD, DateRange(date(YEAR + 2, 1, 1), date(YEAR + 2, 2, 1))
            )
        demo.billing.reset_billed_period(anna_before.tenant.id, PERIOD)
        restored = demo.billing.create_billing(house.id, PERIOD)
        assert next(s for s in restored.statements if s.tenant.name == "Anna Schneider").total_costs == anna_before.total_costs

    def test_tenant_with_direct_share_cannot_be_deleted(self, context: AppContext) -> None:
        prop = context.properties.save_property(Property("Haus", "Adresse 1"))
        assert prop.id is not None
        apartment = context.apartments.save_apartment(Apartment(prop.id, "1", Decimal("50")))
        assert apartment.id is not None
        tenant = context.tenants.save_tenant(Tenant(apartment.id, "Mieter", date(2020, 1, 1)))
        assert tenant.id is not None
        cost = context.costs.save_cost(
            Cost(
                prop.id,
                context.cost_types.get_by_name("Heizung"),
                Money.from_decimal("500"),
                PERIOD,
                AllocationKey.DIRECT,
                direct_shares={tenant.id: Money.from_decimal("420")},
            )
        )
        # Die direkten Anteile überstehen Speichern und Laden.
        assert context.costs.list_costs(prop.id)[0].direct_shares == {tenant.id: Money.from_decimal("420")}
        with pytest.raises(ValidationError):
            context.tenants.delete_tenant(tenant.id)
        assert cost.id is not None
        context.costs.delete_cost(cost.id)
        context.tenants.delete_tenant(tenant.id)

    def test_dashboard_summary(self, demo: AppContext) -> None:
        summary = demo.dashboard.summary(YEAR, today=date(YEAR, 12, 1))
        assert summary.property_count == 2
        assert summary.apartment_count == 6
        assert summary.active_tenant_count == 6
        assert summary.total_area == Decimal("419.50")
        # 16.362,80 volle Jahresrechnungen + 96,00 Rauchwarnmelder
        # + 151,00 anteiliger Strom (365,00 * 151 / 365 Tage).
        assert summary.total_costs == Money.from_decimal("16609.80")
        # Was beim Vermieter bleibt, entspricht der Summe aus den Abrechnungen beider Häuser.
        unallocated = Money.zero()
        for prop in demo.properties.list_properties():
            assert prop.id is not None
            result = demo.billing.create_billing(prop.id, PERIOD)
            unallocated += result.unallocated_amount
            row = next(r for r in summary.properties if r.property.id == prop.id)
            assert (row.costs, row.allocated, row.prepayments) == (
                result.total_costs, result.total_allocated, result.total_prepayments,
            )
        assert summary.total_unallocated == unallocated and unallocated.cents > 0
        assert summary.total_costs == summary.total_allocated + summary.total_unallocated

    def test_pdf_export(self, demo: AppContext, tmp_path: Path) -> None:
        house = demo.properties.list_properties()[1]
        assert house.id is not None
        result = demo.billing.create_billing(house.id, PERIOD)
        target = tmp_path / "abrechnung.pdf"
        demo.pdf_export.export_statements(result, result.statements, demo.settings.load_landlord(), target)
        content = target.read_bytes()
        assert content.startswith(b"%PDF") and len(content) > 5000

    def test_performance_with_many_apartments(self, context: AppContext) -> None:
        """Anforderung: zügige Antworten auch mit 100+ Wohnungen."""
        prop = context.properties.save_property(Property("Großes Haus", "Allee 1"))
        assert prop.id is not None
        with context.database.transaction():
            for number in range(150):
                apartment = context.apartments.save_apartment(
                    Apartment(prop.id, f"W{number:03d}", Decimal("55.5"), 2)
                )
                assert apartment.id is not None
                context.tenants.save_tenant(Tenant(apartment.id, f"Mieter {number}", date(2020, 1, 1)))
                for value, day in (("100", date(YEAR, 1, 1)), ("900", date(YEAR, 12, 31))):
                    context.meter_readings.save_reading(
                        MeterReading(apartment.id, MeterType.HEATING, Decimal(value), day)
                    )
            for type_name, key in (
                ("Heizung", AllocationKey.CONSUMPTION_AREA),
                ("Müllbeseitigung", AllocationKey.PERSONS),
                ("Grundsteuer", AllocationKey.AREA),
            ):
                cost_type = context.cost_types.get_by_name(type_name)
                context.costs.save_cost(Cost(prop.id, cost_type, Money.from_decimal("12345.67"), PERIOD, key))

        started = time.perf_counter()
        result = context.billing.create_billing(prop.id, PERIOD)
        elapsed = time.perf_counter() - started

        assert len(result.statements) == 150
        assert result.total_allocated == result.total_costs
        assert elapsed < 0.5, f"Abrechnung dauerte {elapsed:.3f} s"


class TestUserManagement:
    def test_create_open_and_reregister(self, tmp_path: Path) -> None:
        service = UserService(ConfigManager(tmp_path / "config.json"))
        entry = service.create_user("Benutzer_Test", tmp_path / "daten")
        assert entry.database_path == tmp_path / "daten" / "Benutzer_Test" / "benutzer.db"
        assert entry.database_path.is_file()

        context = service.open_user(entry)
        assert context.user.name == "Benutzer_Test"
        context.properties.save_property(Property("Haus", "Adresse"))
        context.close()
        assert service.last_user_name() == "Benutzer_Test"

        # Aus der Liste entfernen lässt die Datenbank unangetastet ...
        service.remove_user("Benutzer_Test")
        assert service.list_users() == [] and entry.database_path.is_file()
        # ... und sie lässt sich wieder einbinden, inklusive ihrer Daten.
        restored = service.register_existing(entry.database_path)
        assert restored.name == "Benutzer_Test"
        context = service.open_user(restored)
        assert len(context.properties.list_properties()) == 1
        context.close()

    def test_duplicate_and_invalid_names(self, tmp_path: Path) -> None:
        service = UserService(ConfigManager(tmp_path / "config.json"))
        service.create_user("Anna", tmp_path)
        with pytest.raises(ValidationError):
            service.create_user("anna", tmp_path)
        with pytest.raises(ValidationError):
            service.create_user("A/B", tmp_path)

    def test_missing_database_is_reported(self, tmp_path: Path) -> None:
        service = UserService(ConfigManager(tmp_path / "config.json"))
        entry = service.create_user("Weg", tmp_path)
        entry.database_path.unlink()
        with pytest.raises(NotFoundError):
            service.open_user(entry)
