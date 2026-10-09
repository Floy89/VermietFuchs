"""Nachschlagehilfe für Anzeigenamen (Immobilie, Wohnung, Mieter).

Viele Tabellen zeigen statt nackter IDs lesbare Bezeichnungen wie
"Musterstraße 12 · EG links". Damit nicht jede Tabellenzeile eine eigene
Datenbankabfrage auslöst, lädt :class:`Lookups` die Stammdaten einmal und
beantwortet alle weiteren Fragen aus dem Speicher.
"""

from __future__ import annotations

from datetime import date

from app_context import AppContext
from models.apartment import Apartment
from models.prepayment_rate import PrepaymentRate, rate_on
from models.property import Property
from models.tenant import Tenant
from models.value_objects import Money


class Lookups:
    """Momentaufnahme der Stammdaten für die Anzeige.

    Ein Objekt gilt für einen ``refresh()``-Durchlauf eines Tabs und wird
    danach verworfen - es kann also nie veraltete Daten anzeigen.
    """

    def __init__(self, context: AppContext) -> None:
        """Lädt Immobilien, Wohnungen, Mieter und Vorauszahlungsraten (vier Abfragen)."""
        self.properties: list[Property] = context.properties.list_properties()
        self.apartments: list[Apartment] = context.apartments.list_apartments()
        self.tenants: list[Tenant] = context.tenants.list_tenants()
        self._rates: dict[int, list[PrepaymentRate]] = context.prepayment_rates.rates_by_tenant()

        self._properties_by_id = {prop.id: prop for prop in self.properties}
        self._apartments_by_id = {apartment.id: apartment for apartment in self.apartments}
        self._tenants_by_id = {tenant.id: tenant for tenant in self.tenants}

    # --- Einzelne Objekte ------------------------------------------------
    def apartment(self, apartment_id: int | None) -> Apartment | None:
        """Die Wohnung zu einer ID."""
        return self._apartments_by_id.get(apartment_id)

    def tenant(self, tenant_id: int | None) -> Tenant | None:
        """Der Mieter zu einer ID."""
        return self._tenants_by_id.get(tenant_id)

    def property_of_apartment(self, apartment_id: int | None) -> Property | None:
        """Die Immobilie, zu der eine Wohnung gehört."""
        apartment = self.apartment(apartment_id)
        return self._properties_by_id.get(apartment.property_id) if apartment else None

    def prepayment_on(self, tenant_id: int | None, day: date | None = None) -> Money:
        """Die am Stichtag (Standard: heute) vereinbarte monatliche Vorauszahlung."""
        if tenant_id is None:
            return Money.zero()
        return rate_on(self._rates.get(tenant_id, []), day or date.today())

    # --- Anzeigenamen ----------------------------------------------------
    def property_name(self, property_id: int | None) -> str:
        """Name einer Immobilie (leer, falls unbekannt)."""
        prop = self._properties_by_id.get(property_id)
        return prop.name if prop else ""

    def apartment_number(self, apartment_id: int | None) -> str:
        """Nummer einer Wohnung (leer, falls unbekannt)."""
        apartment = self.apartment(apartment_id)
        return apartment.number if apartment else ""

    def apartment_label(self, apartment_id: int | None) -> str:
        """Bezeichnung "Immobilie · Wohnung"."""
        apartment = self.apartment(apartment_id)
        if apartment is None:
            return ""
        return f"{self.property_name(apartment.property_id)} · {apartment.number}"

    def tenant_name(self, tenant_id: int | None) -> str:
        """Name eines Mieters (leer, falls unbekannt)."""
        tenant = self.tenant(tenant_id)
        return tenant.name if tenant else ""

    def current_tenant(self, apartment_id: int | None, on: date | None = None) -> Tenant | None:
        """Der Mieter, der die Wohnung am Stichtag (Standard: heute) bewohnt."""
        on = on or date.today()
        for tenant in self.tenants:
            if tenant.apartment_id == apartment_id and tenant.is_active(on):
                return tenant
        return None

    def current_tenant_name(self, apartment_id: int | None, on: date | None = None) -> str:
        """Name des aktuellen Mieters ("–" bei Leerstand)."""
        tenant = self.current_tenant(apartment_id, on)
        return tenant.name if tenant else "–"

    # --- Auswahllisten für Kombinationsfelder --------------------------------
    def property_choices(self) -> list[tuple[str, int | None]]:
        """(Name, ID) aller Immobilien."""
        return [(prop.name, prop.id) for prop in self.properties]

    def apartment_choices(self, property_id: int | None = None) -> list[tuple[str, int | None]]:
        """(Bezeichnung, ID) aller Wohnungen, optional nur einer Immobilie."""
        return [
            (self.apartment_label(apartment.id), apartment.id)
            for apartment in self.apartments
            if property_id is None or apartment.property_id == property_id
        ]

    def tenant_choices(self, property_id: int | None = None) -> list[tuple[str, int | None]]:
        """(Bezeichnung, ID) aller Mieter, optional nur einer Immobilie."""
        choices: list[tuple[str, int | None]] = []
        for tenant in self.tenants:
            apartment = self.apartment(tenant.apartment_id)
            if apartment is None:
                continue
            if property_id is not None and apartment.property_id != property_id:
                continue
            choices.append((f"{tenant.name} ({self.apartment_label(apartment.id)})", tenant.id))
        return choices
