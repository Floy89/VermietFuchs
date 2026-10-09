"""Anwendungsfälle rund um Sammelrechnungen (eine Rechnung, mehrere Kostenarten)."""

from __future__ import annotations

import logging
from dataclasses import replace
from typing import Sequence

from models.cost import Cost
from models.enums import AllocationKey
from models.exceptions import ValidationError
from models.invoice_bundle import InvoiceBundle
from models.value_objects import Money
from repositories.interfaces import CostRepository, InvoiceBundleRepository, TransactionManager

logger = logging.getLogger(__name__)


class InvoiceBundleService:
    """Legt Sammelrechnungen samt ihren Positionen an und hält beides zusammen.

    Eine Position ist eine gewöhnliche Rechnung (:class:`Cost`) mit Verweis
    auf ihre Sammelrechnung. Abrechnung, Checkliste und aggregierte Rechnungen
    arbeiten deshalb unverändert - für sie macht es keinen Unterschied, ob
    eine Position einzeln oder als Teil einer Sammelrechnung erfasst wurde.
    """

    def __init__(
        self,
        bundles: InvoiceBundleRepository,
        costs: CostRepository,
        transactions: TransactionManager,
    ) -> None:
        self._bundles = bundles
        self._costs = costs
        self._transactions = transactions

    def get(self, bundle_id: int) -> InvoiceBundle:
        """Lädt eine Sammelrechnung; wirft ``NotFoundError``, wenn sie fehlt."""
        return self._bundles.get(bundle_id)

    def positions(self, bundle_id: int) -> list[Cost]:
        """Die Positionen einer Sammelrechnung."""
        return self._costs.list_by_bundle(bundle_id)

    def save(self, bundle: InvoiceBundle, positions: Sequence[Cost]) -> InvoiceBundle:
        """Speichert die Sammelrechnung und gleicht ihre Positionen ab.

        Beschreibung, Rechnungsdatum und Immobilie der Sammelrechnung werden in
        jede Position übernommen. Positionen mit ID werden aktualisiert, neue
        angelegt, und bisherige Positionen, die nicht mehr dabei sind, gelöscht.
        Alles geschieht in einer Transaktion - entweder ganz oder gar nicht.

        Raises:
            ValidationError: Wenn keine Position vorhanden ist, eine Position
                ungültig ist oder die Summe nicht zum Gesamtbetrag passt.
        """
        bundle.validate()
        if not positions:
            raise ValidationError("Bitte mindestens eine Position mit Kostenart und Betrag eintragen.")
        if any(position.allocation_key is AllocationKey.DIRECT for position in positions):
            raise ValidationError(
                "Positionen mit direkter Zuordnung je Mieter bitte als einzelne Rechnung erfassen."
            )
        total = sum((position.amount for position in positions), Money.zero())
        if not bundle.total_amount.is_zero and total != bundle.total_amount:
            difference = bundle.total_amount - total
            raise ValidationError(
                f"Die Summe der Positionen ({_euro(total)}) entspricht nicht dem Gesamtbetrag der Rechnung "
                f"({_euro(bundle.total_amount)}). Differenz: {_euro(difference)}."
            )

        prepared = [
            replace(
                position,
                property_id=bundle.property_id,
                description=bundle.description,
                invoice_date=bundle.invoice_date,
            )
            for position in positions
        ]
        for position in prepared:
            position.validate()

        with self._transactions.transaction():
            if bundle.id is None:
                saved = self._bundles.add(bundle)
            else:
                self._bundles.update(bundle)
                saved = bundle
            assert saved.id is not None
            kept_ids = {position.id for position in prepared if position.id is not None}
            for existing in self._costs.list_by_bundle(saved.id):
                if existing.id is not None and existing.id not in kept_ids:
                    self._costs.delete(existing.id)
            for position in prepared:
                position.bundle_id = saved.id
                if position.id is None:
                    self._costs.add(position)
                else:
                    self._costs.update(position)
        logger.info("Sammelrechnung gespeichert: '%s' mit %d Position(en)", saved.description, len(prepared))
        return saved

    def delete(self, bundle_id: int) -> None:
        """Löscht eine Sammelrechnung mit allen Positionen und Belegen."""
        with self._transactions.transaction():
            for position in self._costs.list_by_bundle(bundle_id):
                if position.id is not None:
                    self._costs.delete(position.id)
            self._bundles.delete(bundle_id)
        logger.info("Sammelrechnung gelöscht (ID %s)", bundle_id)

    def remove_if_empty(self, bundle_id: int) -> bool:
        """Räumt eine Sammelrechnung ab, deren letzte Position gelöscht wurde.

        Returns:
            ``True``, wenn die Sammelrechnung (samt Belegen) entfernt wurde.
        """
        if self._costs.list_by_bundle(bundle_id):
            return False
        self._bundles.delete(bundle_id)
        return True


def _euro(money: Money) -> str:
    return f"{money.amount:.2f} €".replace(".", ",")
