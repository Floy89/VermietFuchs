"""Anwendungsfälle rund um die vereinbarten Vorauszahlungen (Historie, Erhöhungen)."""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import replace
from datetime import date, timedelta

from models.exceptions import ValidationError
from models.prepayment_rate import PrepaymentRate, rate_on
from models.value_objects import Money
from repositories.interfaces import (
    PrepaymentRateRepository,
    TenantRepository,
    TransactionManager,
)
from utils.formatters import format_date

logger = logging.getLogger(__name__)


class PrepaymentRateService:
    """Verwaltet je Mieter die Abfolge der vereinbarten Monatsraten."""

    def __init__(
        self,
        rates: PrepaymentRateRepository,
        tenants: TenantRepository,
        transactions: TransactionManager,
    ) -> None:
        self._rates = rates
        self._tenants = tenants
        self._transactions = transactions

    def list_rates(self, tenant_id: int) -> list[PrepaymentRate]:
        """Die Historie eines Mieters, älteste Rate zuerst."""
        return self._rates.list_by_tenant(tenant_id)

    def rates_by_tenant(self) -> dict[int, list[PrepaymentRate]]:
        """Alle Raten, gruppiert nach Mieter-ID (für Listenansichten)."""
        grouped: dict[int, list[PrepaymentRate]] = defaultdict(list)
        for rate in self._rates.list_all():
            grouped[rate.tenant_id].append(rate)
        return grouped

    def rate_on(self, tenant_id: int, day: date) -> Money:
        """Die am Stichtag vereinbarte Monatsrate eines Mieters."""
        return rate_on(self._rates.list_by_tenant(tenant_id), day)

    def save_rate(self, rate: PrepaymentRate) -> PrepaymentRate:
        """Speichert eine neue oder geänderte Rate.

        Komfort für den häufigsten Fall "Erhöhung ab Datum X": Läuft eine
        frühere Rate noch unbefristet, wird sie automatisch zum Vortag der
        neuen beendet. Alle anderen Überschneidungen werden abgelehnt - an
        einem Tag darf nur eine Rate gelten.

        Raises:
            ValidationError: Bei ungültigen Angaben oder Überschneidungen.
            NotFoundError: Wenn der Mieter nicht (mehr) existiert.
        """
        rate.validate()
        self._tenants.get(rate.tenant_id)

        with self._transactions.transaction():
            for other in self._rates.list_by_tenant(rate.tenant_id):
                if other.id == rate.id:
                    continue
                if other.valid_to is None and other.valid_from < rate.valid_from:
                    other = replace(other, valid_to=rate.valid_from - timedelta(days=1))
                    self._rates.update(other)
                if other.period.overlaps(rate.period):
                    until = format_date(other.valid_to) if other.valid_to else "offen"
                    raise ValidationError(
                        "Der Zeitraum überschneidet sich mit einer anderen Rate "
                        f"({format_date(other.valid_from)} – {until})."
                    )
            if rate.id is None:
                saved = self._rates.add(rate)
                logger.info("Vorauszahlungsrate angelegt (Mieter %s, ab %s)", rate.tenant_id, rate.valid_from)
                return saved
            self._rates.update(rate)
            return rate

    def delete_rate(self, rate_id: int) -> None:
        """Löscht eine Rate aus der Historie."""
        self._rates.delete(rate_id)
