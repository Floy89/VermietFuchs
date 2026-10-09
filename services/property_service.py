"""Anwendungsfälle rund um Immobilien."""

from __future__ import annotations

import logging

from models.exceptions import ValidationError
from models.property import Property
from repositories.interfaces import ApartmentRepository, CostRepository, PropertyRepository

logger = logging.getLogger(__name__)


class PropertyService:
    """Anlegen, Ändern und Löschen von Immobilien."""

    def __init__(
        self,
        properties: PropertyRepository,
        apartments: ApartmentRepository,
        costs: CostRepository,
    ) -> None:
        self._properties = properties
        self._apartments = apartments
        self._costs = costs

    def list_properties(self) -> list[Property]:
        """Alle Immobilien, alphabetisch sortiert."""
        return self._properties.list_all()

    def get_property(self, property_id: int) -> Property:
        """Lädt eine Immobilie; wirft ``NotFoundError``, wenn sie fehlt."""
        return self._properties.get(property_id)

    def apartment_counts(self) -> dict[int, int]:
        """Anzahl der Wohnungen je Immobilien-ID."""
        counts: dict[int, int] = {}
        for apartment in self._apartments.list_all():
            counts[apartment.property_id] = counts.get(apartment.property_id, 0) + 1
        return counts

    def save_property(self, prop: Property) -> Property:
        """Speichert eine neue oder geänderte Immobilie.

        Ob angelegt oder geändert wird, entscheidet ``prop.id``.

        Raises:
            ValidationError: Bei ungültigen Angaben.
        """
        prop.validate()
        if prop.id is None:
            saved = self._properties.add(prop)
            logger.info("Immobilie angelegt: %s (ID %s)", saved.name, saved.id)
            return saved
        self._properties.update(prop)
        return prop

    def delete_property(self, property_id: int) -> None:
        """Löscht eine Immobilie.

        Raises:
            ValidationError: Solange noch Wohnungen oder Kostenpositionen
                zur Immobilie gehören.
        """
        if self._apartments.list_by_property(property_id):
            raise ValidationError(
                "Die Immobilie enthält noch Wohnungen. Bitte zuerst alle Wohnungen löschen."
            )
        if self._costs.list_by_property(property_id):
            raise ValidationError(
                "Zur Immobilie sind noch Kostenpositionen erfasst. Bitte diese zuerst löschen."
            )
        self._properties.delete(property_id)
        logger.info("Immobilie gelöscht (ID %s)", property_id)
