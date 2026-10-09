"""Kennzahlen für das Dashboard."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from models.property import Property
from models.value_objects import DateRange, Money
from repositories.interfaces import ApartmentRepository, PropertyRepository, TenantRepository
from services.billing_service import BillingService


@dataclass(frozen=True)
class PropertyYearSummary:
    """Jahresübersicht einer Immobilie - dieselben Zahlen wie im Tab "Abrechnung".

    Attributes:
        property: Die Immobilie.
        apartment_count: Anzahl der Wohnungen.
        active_tenant_count: Anzahl der Mieter mit heute laufendem Vertrag.
        costs: Summe der im Jahr abzurechnenden Nebenkosten.
        allocated: Davon auf Mieter umgelegt.
        prepayments: Vorauszahlungen der Mieter laut vereinbarten Raten.
        has_warnings: ``True``, wenn die Abrechnung Hinweise enthält.
    """

    property: Property
    apartment_count: int
    active_tenant_count: int
    costs: Money
    allocated: Money
    prepayments: Money
    has_warnings: bool = False

    @property
    def unallocated(self) -> Money:
        """Was beim Vermieter bleibt: Kosten, die auf keinen Mieter umgelegt werden.

        Das sind Leerstand, nicht abgerechnete Zeiträume, nicht zugeordnete
        Teile direkt zugeordneter Rechnungen und die Wirkung abweichender
        Umlageschlüssel. Negativ bedeutet: Es wird mehr umgelegt als angefallen.
        """
        return self.costs - self.allocated

    @property
    def tenant_balance(self) -> Money:
        """Saldo aller Mieter: Vorauszahlungen minus umgelegte Kosten.

        Positiv = insgesamt Guthaben der Mieter, negativ = Nachzahlungen.
        """
        return self.prepayments - self.allocated


@dataclass(frozen=True)
class DashboardSummary:
    """Alle Kennzahlen, die das Dashboard anzeigt."""

    year: int
    property_count: int
    apartment_count: int
    active_tenant_count: int
    total_area: Decimal
    total_costs: Money
    total_allocated: Money
    total_prepayments: Money
    properties: list[PropertyYearSummary] = field(default_factory=list)

    @property
    def total_unallocated(self) -> Money:
        """Summe dessen, was über alle Immobilien beim Vermieter bleibt."""
        return self.total_costs - self.total_allocated


class DashboardService:
    """Berechnet die Übersichtszahlen aus dem aktuellen Datenbestand."""

    def __init__(
        self,
        properties: PropertyRepository,
        apartments: ApartmentRepository,
        tenants: TenantRepository,
        billing: BillingService,
    ) -> None:
        self._properties = properties
        self._apartments = apartments
        self._tenants = tenants
        self._billing = billing

    def summary(self, year: int, today: date | None = None) -> DashboardSummary:
        """Erstellt die Übersicht für ein Kalenderjahr.

        Für jede Immobilie wird die vollständige Abrechnung des Jahres
        berechnet. Dadurch stimmen die Zahlen garantiert mit dem Tab
        "Abrechnung" überein - einschließlich Splits, ausgeschlossener
        Rechnungen, angepasster Zeiträume und abweichender Umlageschlüssel.

        Args:
            year: Das Kalenderjahr.
            today: Stichtag für "aktive" Mieter (Standard: heute).
        """
        today = today or date.today()
        period = DateRange.for_year(year)

        apartments = self._apartments.list_all()
        property_of_apartment = {a.id: a.property_id for a in apartments}
        apartment_counts: dict[int, int] = defaultdict(int)
        for apartment in apartments:
            apartment_counts[apartment.property_id] += 1

        active_counts: dict[int, int] = defaultdict(int)
        for tenant in self._tenants.list_all():
            property_id = property_of_apartment.get(tenant.apartment_id)
            if property_id is not None and tenant.is_active(today):
                active_counts[property_id] += 1

        rows = []
        for prop in self._properties.list_all():
            assert prop.id is not None  # aus der Datenbank geladen
            result = self._billing.create_billing(prop.id, period)
            rows.append(
                PropertyYearSummary(
                    property=prop,
                    apartment_count=apartment_counts[prop.id],
                    active_tenant_count=active_counts[prop.id],
                    costs=result.total_costs,
                    allocated=result.total_allocated,
                    prepayments=result.total_prepayments,
                    has_warnings=bool(result.warnings),
                )
            )

        return DashboardSummary(
            year=year,
            property_count=len(rows),
            apartment_count=len(apartments),
            active_tenant_count=sum(active_counts.values()),
            total_area=sum((a.area_sqm for a in apartments), Decimal(0)),
            total_costs=sum((row.costs for row in rows), Money.zero()),
            total_allocated=sum((row.allocated for row in rows), Money.zero()),
            total_prepayments=sum((row.prepayments for row in rows), Money.zero()),
            properties=rows,
        )
