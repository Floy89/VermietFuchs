"""Anwendungsfälle rund um Kostenpositionen."""

from __future__ import annotations

import logging

from models.cost import Cost
from models.value_objects import DateRange
from repositories.interfaces import CostRepository

logger = logging.getLogger(__name__)


class CostService:
    """Erfassen, Ändern und Löschen von Nebenkostenpositionen."""

    def __init__(self, costs: CostRepository) -> None:
        self._costs = costs

    def list_costs(self, property_id: int, period: DateRange | None = None) -> list[Cost]:
        """Kostenpositionen einer Immobilie.

        Args:
            property_id: Die Immobilie.
            period: Wenn angegeben, nur Positionen, deren Rechnungszeitraum
                diesen Zeitraum berührt (auch teilweise).
        """
        costs = self._costs.list_by_property(property_id)
        if period is None:
            return costs
        return [cost for cost in costs if period.overlaps(cost.period)]

    def list_all(self) -> list[Cost]:
        """Alle Kostenpositionen aller Immobilien."""
        return self._costs.list_all()

    def save_cost(self, cost: Cost) -> Cost:
        """Speichert eine neue oder geänderte Kostenposition.

        Raises:
            ValidationError: Bei ungültigen Angaben, z. B. einem für die
                Kostenart unzulässigen Umlageschlüssel.
        """
        cost.validate()
        if cost.id is None:
            saved = self._costs.add(cost)
            logger.info("Kostenposition angelegt: %s (ID %s)", saved.title, saved.id)
            return saved
        self._costs.update(cost)
        return cost

    def set_excluded(self, cost_id: int, excluded: bool) -> None:
        """Schließt eine Rechnung von Aggregation und Abrechnung aus bzw. nimmt sie wieder auf."""
        cost = self._costs.get(cost_id)
        cost.excluded = excluded
        self._costs.update(cost)

    def delete_cost(self, cost_id: int) -> None:
        """Löscht eine Kostenposition."""
        self._costs.delete(cost_id)
        logger.info("Kostenposition gelöscht (ID %s)", cost_id)
