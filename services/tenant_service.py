"""Anwendungsfälle rund um Mieter und Mietverträge."""

from __future__ import annotations

import logging

from models.exceptions import ValidationError
from models.prepayment_rate import PrepaymentRate
from models.tenant import Tenant
from models.value_objects import Money
from repositories.interfaces import (
    ApartmentRepository,
    CostRepository,
    PrepaymentRateRepository,
    TenantRepository,
    TransactionManager,
)
from utils.formatters import format_date

logger = logging.getLogger(__name__)


class TenantService:
    """Anlegen, Ändern und Löschen von Mietern."""

    def __init__(
        self,
        tenants: TenantRepository,
        apartments: ApartmentRepository,
        costs: CostRepository,
        prepayment_rates: PrepaymentRateRepository,
        transactions: TransactionManager,
    ) -> None:
        self._tenants = tenants
        self._apartments = apartments
        self._costs = costs
        self._prepayment_rates = prepayment_rates
        self._transactions = transactions

    def list_tenants(self, property_id: int | None = None) -> list[Tenant]:
        """Alle Mieter, optional nur die einer Immobilie."""
        if property_id is None:
            return self._tenants.list_all()
        return self._tenants.list_by_property(property_id)

    def get_tenant(self, tenant_id: int) -> Tenant:
        """Lädt einen Mieter; wirft ``NotFoundError``, wenn er fehlt."""
        return self._tenants.get(tenant_id)

    def save_tenant(self, tenant: Tenant, initial_prepayment: Money | None = None) -> Tenant:
        """Speichert einen neuen oder geänderten Mieter.

        Args:
            tenant: Der Mieter.
            initial_prepayment: Nur bei einem *neuen* Mieter: vereinbarte
                monatliche Nebenkosten-Vorauszahlung. Sie wird als erste Rate
                der Historie ab Vertragsbeginn angelegt. Spätere Änderungen
                laufen über den ``PrepaymentRateService``.

        Raises:
            ValidationError: Bei ungültigen Angaben oder wenn sich der
                Vertragszeitraum mit einem anderen Mieter derselben Wohnung
                überschneidet.
        """
        tenant.validate()
        self._apartments.get(tenant.apartment_id)  # wirft NotFoundError, falls gelöscht
        self._ensure_no_overlap(tenant)
        if tenant.id is not None:
            self._tenants.update(tenant)
            return tenant

        # Mieter und erste Vorauszahlungsrate gehören zusammen: beides oder nichts.
        with self._transactions.transaction():
            saved = self._tenants.add(tenant)
            if initial_prepayment is not None and not initial_prepayment.is_zero and saved.id is not None:
                rate = PrepaymentRate(saved.id, initial_prepayment, saved.start_date)
                rate.validate()
                self._prepayment_rates.add(rate)
        logger.info("Mieter angelegt: %s (ID %s)", saved.name, saved.id)
        return saved

    def delete_tenant(self, tenant_id: int) -> None:
        """Löscht einen Mieter.

        Raises:
            ValidationError: Solange dem Mieter in Rechnungen Beträge direkt
                zugeordnet sind.
        """
        referencing = [cost.title for cost in self._costs.list_all() if tenant_id in cost.direct_shares]
        if referencing:
            raise ValidationError(
                "Diesem Mieter sind in Kostenpositionen Beträge direkt zugeordnet "
                f"({', '.join(referencing[:3])}). Bitte diese Positionen zuerst anpassen."
            )
        # Die Historie der Vorauszahlungen gehört zum Mieter und wird mit gelöscht -
        # einzeln, damit jede Rate im Änderungsprotokoll erscheint.
        with self._transactions.transaction():
            for rate in self._prepayment_rates.list_by_tenant(tenant_id):
                if rate.id is not None:
                    self._prepayment_rates.delete(rate.id)
            self._tenants.delete(tenant_id)
        logger.info("Mieter gelöscht (ID %s)", tenant_id)

    def _ensure_no_overlap(self, tenant: Tenant) -> None:
        """Eine Wohnung kann zu jedem Zeitpunkt nur einen Mietvertrag haben.

        Ohne diese Regel würden Kosten für denselben Tag doppelt verteilt.
        """
        for other in self._tenants.list_by_apartment(tenant.apartment_id):
            if other.id == tenant.id:
                continue
            if other.contract_period.overlaps(tenant.contract_period):
                until = format_date(other.end_date) if other.end_date else "unbefristet"
                raise ValidationError(
                    f"Der Vertragszeitraum überschneidet sich mit dem Mieter '{other.name}' "
                    f"({format_date(other.start_date)} – {until})."
                )
