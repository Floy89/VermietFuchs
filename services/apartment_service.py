"""Anwendungsfälle rund um Wohnungen."""

from __future__ import annotations

import logging

from models.apartment import Apartment
from models.exceptions import ValidationError
from repositories.interfaces import (
    ApartmentRepository,
    MeterReadingRepository,
    TenantRepository,
)

logger = logging.getLogger(__name__)


class ApartmentService:
    """Anlegen, Ändern und Löschen von Wohnungen."""

    def __init__(
        self,
        apartments: ApartmentRepository,
        tenants: TenantRepository,
        readings: MeterReadingRepository,
    ) -> None:
        self._apartments = apartments
        self._tenants = tenants
        self._readings = readings

    def list_apartments(self, property_id: int | None = None) -> list[Apartment]:
        """Alle Wohnungen, optional nur die einer Immobilie."""
        if property_id is None:
            return self._apartments.list_all()
        return self._apartments.list_by_property(property_id)

    def get_apartment(self, apartment_id: int) -> Apartment:
        """Lädt eine Wohnung; wirft ``NotFoundError``, wenn sie fehlt."""
        return self._apartments.get(apartment_id)

    def save_apartment(self, apartment: Apartment) -> Apartment:
        """Speichert eine neue oder geänderte Wohnung.

        Raises:
            ValidationError: Bei ungültigen Angaben oder wenn die
                Wohnungsnummer in der Immobilie schon vergeben ist.
        """
        apartment.validate()
        self._ensure_unique_number(apartment)
        if apartment.id is None:
            saved = self._apartments.add(apartment)
            logger.info("Wohnung angelegt: %s (ID %s)", saved.number, saved.id)
            return saved
        self._apartments.update(apartment)
        return apartment

    def delete_apartment(self, apartment_id: int) -> None:
        """Löscht eine Wohnung.

        Raises:
            ValidationError: Solange noch Mieter oder Zählerstände an der
                Wohnung hängen.
        """
        if self._tenants.list_by_apartment(apartment_id):
            raise ValidationError(
                "Der Wohnung sind noch Mieter zugeordnet. Bitte zuerst die Mieter löschen."
            )
        if self._readings.list_filtered(apartment_id=apartment_id):
            raise ValidationError(
                "Zur Wohnung sind noch Zählerstände erfasst. Bitte diese zuerst löschen."
            )
        self._apartments.delete(apartment_id)
        logger.info("Wohnung gelöscht (ID %s)", apartment_id)

    def _ensure_unique_number(self, apartment: Apartment) -> None:
        """Verhindert doppelte Wohnungsnummern innerhalb einer Immobilie."""
        wanted = apartment.number.casefold()
        for other in self._apartments.list_by_property(apartment.property_id):
            if other.id != apartment.id and other.number.casefold() == wanted:
                raise ValidationError(
                    f"Die Wohnungsnummer '{apartment.number}' ist in dieser Immobilie bereits vergeben."
                )
