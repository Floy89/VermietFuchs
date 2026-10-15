"""Erstellung der Nebenkostenabrechnung für eine Immobilie und einen Zeitraum."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import date
from decimal import Decimal

from models.apartment import Apartment
from models.billing import BillingPeriodOverride, BillingResult, TenantStatement
from models.cost import Cost
from models.enums import AllocationKey
from models.exceptions import ValidationError
from models.prepayment_rate import PrepaymentRate, expected_prepayment, rate_on
from models.tenant import Tenant
from models.value_objects import DateRange
from repositories.interfaces import (
    ApartmentRepository,
    BillingPeriodOverrideRepository,
    CostRepository,
    PrepaymentRateRepository,
    PropertyRepository,
    TenantRepository,
)
from services.cost_allocation_service import CostAllocationService
from services.invoice_group_service import InvoiceGroupService
from services.key_override_service import KeyOverrideService
from services.meter_reading_service import MeterReadingService
from utils.formatters import format_money, format_period

logger = logging.getLogger(__name__)

#: Umlageschlüssel, die Verbrauchswerte aus Zählerständen benötigen.
_CONSUMPTION_KEYS = frozenset({AllocationKey.CONSUMPTION, AllocationKey.CONSUMPTION_AREA})


class BillingService:
    """Sammelt alle Daten ein und lässt den ``CostAllocationService`` rechnen.

    Der Service speichert keine Ergebnisse. Jede Abrechnung wird aus dem
    aktuellen Datenbestand neu berechnet - Korrekturen an Rechnungen,
    Zählerständen oder Vorauszahlungsraten wirken sich dadurch sofort aus.
    Gespeichert wird nur, was der Benutzer festlegt: ein von Hand angepasster
    Abrechnungszeitraum für ein Mietverhältnis.
    """

    def __init__(
        self,
        properties: PropertyRepository,
        apartments: ApartmentRepository,
        tenants: TenantRepository,
        costs: CostRepository,
        prepayment_rates: PrepaymentRateRepository,
        overrides: BillingPeriodOverrideRepository,
        invoice_groups: InvoiceGroupService,
        key_overrides: KeyOverrideService,
        meter_readings: MeterReadingService,
        allocator: CostAllocationService,
    ) -> None:
        self._properties = properties
        self._apartments = apartments
        self._tenants = tenants
        self._costs = costs
        self._prepayment_rates = prepayment_rates
        self._overrides = overrides
        self._invoice_groups = invoice_groups
        self._key_overrides = key_overrides
        self._meter_readings = meter_readings
        self._allocator = allocator

    # ------------------------------------------------------------------ #
    # Abrechnung berechnen
    # ------------------------------------------------------------------ #
    def create_billing(self, property_id: int, period: DateRange) -> BillingResult:
        """Berechnet die Abrechnung.

        * **Kosten:** alle nicht ausgeschlossenen Rechnungen der Immobilie,
          deren Rechnungszeitraum den Abrechnungszeitraum berührt - taggenau
          anteilig, falls sie darüber hinausragen.
        * **Zeitraum je Mietverhältnis:** standardmäßig die Vertragsdauer
          innerhalb des Abrechnungszeitraums (Einzug/Auszug werden also
          automatisch berücksichtigt). Ein von Hand gesetzter Zeitraum
          (:meth:`set_billed_period`) schränkt das weiter ein.
        * **Vorauszahlungen:** aus den vereinbarten Raten für den
          abgerechneten Zeitraum.

        Raises:
            NotFoundError: Wenn die Immobilie nicht existiert.
        """
        prop = self._properties.get(property_id)
        apartments = self._apartments.list_by_property(property_id)
        tenants = self._tenants.list_by_property(property_id)

        result = BillingResult(property=prop, period=period)
        invoices = self._billable_invoices(property_id, period)
        result.costs = self._cut_to_period(invoices, period)
        self._warn_about_split_gaps(invoices, period, result)

        overrides = {o.tenant_id: o.billed for o in self._overrides.list_by_period(period)}
        rates_by_tenant: dict[int, list[PrepaymentRate]] = defaultdict(list)
        for rate in self._prepayment_rates.list_by_property(property_id):
            rates_by_tenant[rate.tenant_id].append(rate)

        # Für jedes Mietverhältnis im Abrechnungszeitraum eine (zunächst
        # leere) Einzelabrechnung anlegen. "billed_tenants" enthält Kopien der
        # Mieter, deren Vertragsdaten auf den abzurechnenden Zeitraum gekürzt
        # sind - damit rechnet die Kostenverteilung, ohne etwas von Overrides
        # wissen zu müssen.
        apartments_by_id = {apartment.id: apartment for apartment in apartments}
        statements: dict[int, TenantStatement] = {}
        billed_tenants: list[Tenant] = []
        for tenant in tenants:
            contract_occupancy = tenant.occupancy(period)
            apartment = apartments_by_id.get(tenant.apartment_id)
            if contract_occupancy is None or apartment is None or tenant.id is None:
                continue
            occupancy = contract_occupancy
            if tenant.id in overrides:
                clipped = contract_occupancy.intersection(overrides[tenant.id])
                if clipped is None:
                    # Kann passieren, wenn der Vertrag nachträglich geändert wurde.
                    result.warnings.append(
                        f"{tenant.name}: Der von Hand gesetzte Abrechnungszeitraum liegt außerhalb des "
                        "Mietvertrags und wurde ignoriert."
                    )
                else:
                    occupancy = clipped

            all_rates = rates_by_tenant.get(tenant.id, [])
            rates = [r for r in all_rates if r.period.overlaps(occupancy)]
            # Für die Empfehlung einer Anpassung zählt, was der Mieter *heute* zahlt.
            today = date.today()
            is_active = tenant.end_date is None or tenant.end_date >= today
            statements[tenant.id] = TenantStatement(
                tenant=tenant,
                apartment=apartment,
                period=period,
                occupancy=occupancy,
                contract_occupancy=contract_occupancy,
                prepayments=expected_prepayment(rates, occupancy),
                prepayment_rates=rates,
                current_rate=rate_on(all_rates, today) if is_active else None,
            )
            billed_tenants.append(replace(tenant, start_date=occupancy.start, end_date=occupancy.end))

        # Jede Kostenposition verteilen und die Anteile einsammeln. Standard
        # ist der Schlüssel der Rechnung; nur wo im Mietvertrag eines Mieters
        # für die Kostenart etwas anderes vereinbart ist, gilt dessen Schlüssel.
        all_overrides = self._key_overrides.keys_by_tenant(property_id)
        for cost in result.costs:
            overrides = self._overrides_for(cost, all_overrides)
            needs_consumption = AllocationKey.CONSUMPTION in overrides.values()
            consumption = self._consumption_for(cost, apartments, result, force=needs_consumption)
            allocation = self._allocator.allocate(cost, apartments, billed_tenants, consumption, overrides)
            result.vacancy_amount += allocation.vacancy_amount
            result.deviation_amount += allocation.deviation_amount
            result.warnings.extend(allocation.warnings)
            for tenant_id, shares in allocation.shares_by_tenant.items():
                if tenant_id in statements:
                    statements[tenant_id].shares.extend(shares)

        result.statements = sorted(
            statements.values(),
            key=lambda statement: (statement.apartment.number.casefold(), statement.occupancy.start),
        )
        self._warn_about_adjusted_direct_shares(result)
        self._warn_about_over_allocation(result)
        logger.info(
            "Abrechnung berechnet: %s, %s, %d Positionen, %d Mietverhältnisse",
            prop.name,
            format_period(period),
            len(result.costs),
            len(result.statements),
        )
        return result

    def costs_in_period(self, property_id: int, period: DateRange) -> list[Cost]:
        """Die abzurechnenden Kostenpositionen, zugeschnitten auf den Abrechnungszeitraum.

        Nicht enthalten sind einzeln ausgeschlossene Rechnungen und alle
        Rechnungen einer Kostenart, deren aggregierte Rechnung für das Jahr
        ausgeschlossen ist (Tab "Rechnungen"). Jede Rechnung wird in die Stücke
        zerlegt, die in den Zeitraum fallen (siehe ``Cost.pieces``): mit Splits
        je Abschnitt eines, ohne Splits ein taggenau anteiliges.
        """
        return self._cut_to_period(self._billable_invoices(property_id, period), period)

    def _billable_invoices(self, property_id: int, period: DateRange) -> list[Cost]:
        """Die Originalrechnungen, die in die Abrechnung des Zeitraums eingehen."""
        excluded_types = self._invoice_groups.excluded_cost_type_ids(property_id, period.start.year)
        return [
            cost
            for cost in self._costs.list_by_property(property_id)
            if not cost.excluded and cost.cost_type.id not in excluded_types and cost.period.overlaps(period)
        ]

    @staticmethod
    def _cut_to_period(invoices: list[Cost], period: DateRange) -> list[Cost]:
        """Zerlegt Rechnungen in ihre Stücke im Zeitraum.

        Mit Splits liefert eine Rechnung mehrere Stücke (je Abschnitt eins),
        ohne Splits genau eines (taggenau linear).
        """
        return [piece for cost in invoices for piece in cost.pieces(period) if not piece.amount.is_zero]

    @staticmethod
    def _warn_about_split_gaps(invoices: list[Cost], period: DateRange, result: BillingResult) -> None:
        """Meldet Rechnungen, deren Splits Tage im Abrechnungszeitraum nicht abdecken."""
        for cost in invoices:
            gaps = [gap for gap in cost.split_gaps if gap.overlaps(period)]
            if gaps:
                listed = ", ".join(format_period(gap) for gap in gaps)
                result.warnings.append(
                    f"'{cost.title}': Die Splits decken den Rechnungszeitraum nicht vollständig ab "
                    f"({listed}). Für diese Tage werden keine Kosten berechnet - bitte die Daten der Splits prüfen."
                )

    # ------------------------------------------------------------------ #
    # Abrechnungszeitraum je Mietverhältnis
    # ------------------------------------------------------------------ #
    def set_billed_period(self, tenant_id: int, period: DateRange, billed: DateRange) -> None:
        """Legt für ein Mietverhältnis einen eigenen Abrechnungszeitraum fest.

        Args:
            tenant_id: Der Mieter.
            period: Der Abrechnungszeitraum der Immobilie, für den das gilt.
            billed: Der für den Mieter abzurechnende Zeitraum.

        Raises:
            ValidationError: Wenn ``billed`` keinen Tag mit Mietvertrag und
                Abrechnungszeitraum gemeinsam hat.
        """
        tenant = self._tenants.get(tenant_id)
        contract_occupancy = tenant.occupancy(period)
        if contract_occupancy is None or contract_occupancy.intersection(billed) is None:
            raise ValidationError(
                "Der gewählte Zeitraum liegt außerhalb des Mietvertrags bzw. des Abrechnungszeitraums."
            )
        existing = next((o for o in self._overrides.list_by_period(period) if o.tenant_id == tenant_id), None)
        if existing is None:
            self._overrides.add(BillingPeriodOverride(tenant_id, period, billed))
        else:
            self._overrides.update(replace(existing, billed=billed))

    def reset_billed_period(self, tenant_id: int, period: DateRange) -> None:
        """Entfernt die Anpassung - es gilt wieder der Vertragszeitraum."""
        for override in self._overrides.list_by_period(period):
            if override.tenant_id == tenant_id and override.id is not None:
                self._overrides.delete(override.id)

    # ------------------------------------------------------------------ #
    # Interna
    # ------------------------------------------------------------------ #
    def _consumption_for(
        self,
        cost: Cost,
        apartments: list[Apartment],
        result: BillingResult,
        force: bool = False,
    ) -> dict[int, Decimal]:
        """Ermittelt die Verbrauchswerte, falls die Position welche benötigt.

        Fehlen für den Zeitraum eines Splits die Ablesungen (der Versorger
        liest z. B. am 12.09. ab, der Vermieter am 31.05. und 31.12.), gilt der
        Verbrauch zwischen den engsten Ablesungen, die den Split umschließen
        (31.05.-31.12.). Der Split wird so mit den angrenzenden Splits in
        diesem Zeitraum zusammengefasst und nach derselben Verteilung
        abgerechnet. Die Werte werden nach Tagen auf den Split umgerechnet -
        das ändert die Verhältnisse nicht, hält aber die angezeigten Mengen
        plausibel. Erst wenn es auch das nicht gibt, greift die Wohnfläche.

        Args:
            force: Auch dann laden, wenn der Schlüssel der Rechnung keinen
                Verbrauch braucht - weil ein Mieter abweichend nach Verbrauch
                abgerechnet wird.
        """
        meter_type = cost.cost_type.meter_type
        if meter_type is None or (cost.allocation_key not in _CONSUMPTION_KEYS and not force):
            return {}

        consumption = self._meter_readings.consumption_by_apartment(cost.property_id, meter_type, cost.period)
        if not consumption:
            enclosing = self._meter_readings.consumption_enclosing(cost.property_id, meter_type, cost.period)
            if enclosing:
                consumption = {
                    apartment_id: (value * Decimal(cost.period.days) / Decimal(reference.days)).quantize(Decimal("0.001"))
                    for apartment_id, (value, reference) in enclosing.items()
                }
                references = ", ".join(
                    f"{reference.start:%d.%m.%Y} und {reference.end:%d.%m.%Y}"
                    for reference in sorted({reference for _value, reference in enclosing.values()}, key=lambda r: r.start)
                )
                result.warnings.append(
                    f"'{cost.title}': Für den Abschnitt {format_period(cost.period)} fehlen "
                    f"{meter_type.label}-Zählerstände - verteilt nach dem Verbrauch zwischen den Ablesungen "
                    f"{references} (zusammen mit den angrenzenden Abschnitten)."
                )
        # Fehlen nur für *einzelne* Wohnungen Werte, wird deren Verbrauch als
        # 0 gewertet. Das ist fast immer ein Erfassungsfehler - also melden.
        if consumption:
            missing = [a.number for a in apartments if a.id not in consumption]
            if missing:
                result.warnings.append(
                    f"'{cost.title}': Für folgende Wohnungen fehlen {meter_type.label}-Zählerstände, "
                    f"ihr Verbrauch wurde mit 0 angesetzt: {', '.join(missing)}."
                )
        return consumption

    @staticmethod
    def _overrides_for(
        cost: Cost,
        all_overrides: dict[tuple[int, int], AllocationKey],
    ) -> dict[int, AllocationKey]:
        """Abweichende Schlüssel je Mieter-ID für die Kostenart dieser Position.

        Positionen mit Mischschlüssel (Heizkosten) oder direkter Zuordnung
        kennen keine Abweichungen: Dort ist die Verteilung gesetzlich
        vorgegeben bzw. steht je Mieter bereits fest.
        """
        if cost.allocation_key in (AllocationKey.DIRECT, AllocationKey.CONSUMPTION_AREA):
            return {}
        return {
            tenant_id: key
            for (tenant_id, cost_type_id), key in all_overrides.items()
            if cost_type_id == cost.cost_type.id
        }

    @staticmethod
    def _warn_about_over_allocation(result: BillingResult) -> None:
        """Warnt, wenn durch abweichende Schlüssel mehr umgelegt wird, als angefallen ist.

        Der Vermieter darf auf die Mieter insgesamt nicht mehr verteilen als
        den Rechnungsbetrag. Die Anwendung kürzt nicht selbst - wer weniger
        zahlen soll, muss der Vermieter entscheiden.
        """
        for cost in result.costs:
            allocated = result.allocated_for(cost)
            if allocated > cost.amount:
                result.warnings.append(
                    f"'{cost.title}': Durch abweichende Umlageschlüssel werden {format_money(allocated)} "
                    f"umgelegt - mehr als der Betrag von {format_money(cost.amount)}. "
                    "Bitte die Schlüssel der Mieter prüfen."
                )

    @staticmethod
    def _warn_about_adjusted_direct_shares(result: BillingResult) -> None:
        """Direkt zugeordnete Beträge werden bei einem gekürzten Zeitraum nicht mitgekürzt.

        Ein Betrag aus einer Messdienst-Abrechnung gilt für den Zeitraum, den
        der Messdienst zugrunde gelegt hat. Verkürzt der Benutzer den
        Abrechnungszeitraum von Hand, kann die Anwendung nicht wissen, welcher
        Teil davon auf die kürzere Zeit entfällt - sie übernimmt ihn
        unverändert und weist darauf hin.
        """
        for statement in result.statements:
            if not statement.is_adjusted:
                continue
            if any(share.cost.allocation_key is AllocationKey.DIRECT for share in statement.shares):
                result.warnings.append(
                    f"{statement.tenant.name}: Der Abrechnungszeitraum wurde von Hand angepasst. "
                    "Direkt zugeordnete Beträge (z. B. Messdienst) wurden unverändert übernommen - "
                    "bitte prüfen, ob sie zum gewählten Zeitraum passen."
                )
