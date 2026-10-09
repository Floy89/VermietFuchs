"""Anwendungsfälle rund um Zählerstände und Verbrauchsermittlung."""

from __future__ import annotations

import logging
from collections import defaultdict
from decimal import Decimal

from models.enums import MeterType, PhotoCategory
from models.meter_reading import MeterReading, calculate_consumption
from models.photo import Photo
from models.value_objects import DateRange
from repositories.interfaces import (
    MeterReadingRepository,
    PhotoRepository,
    TransactionManager,
)

logger = logging.getLogger(__name__)


class MeterReadingService:
    """Erfassen von Zählerständen (optional mit Foto) und Berechnen des Verbrauchs."""

    def __init__(
        self,
        readings: MeterReadingRepository,
        photos: PhotoRepository,
        transactions: TransactionManager,
    ) -> None:
        self._readings = readings
        self._photos = photos
        self._transactions = transactions

    def list_readings(
        self,
        property_id: int | None = None,
        apartment_id: int | None = None,
        meter_type: MeterType | None = None,
    ) -> list[MeterReading]:
        """Zählerstände, optional gefiltert; neueste zuerst."""
        return self._readings.list_filtered(property_id, apartment_id, meter_type)

    def reading_ids_with_photos(self) -> set[int]:
        """IDs aller Zählerstände, zu denen ein Foto existiert."""
        return self._photos.reading_ids_with_photos()

    def photos_of_reading(self, reading_id: int) -> list[Photo]:
        """Metadaten der Fotos eines Zählerstands (ohne Bilddaten)."""
        return self._photos.list_metadata(reading_id=reading_id)

    def save_reading(
        self,
        reading: MeterReading,
        photo_file_name: str | None = None,
        photo_data: bytes | None = None,
    ) -> MeterReading:
        """Speichert einen Zählerstand, optional zusammen mit einem Foto.

        Zählerstand und Foto werden in *einer* Transaktion gespeichert: Schlägt
        das Speichern des Fotos fehl, wird auch der Zählerstand nicht angelegt.

        Raises:
            ValidationError: Bei ungültigen Angaben.
        """
        reading.validate()
        with self._transactions.transaction():
            if reading.id is None:
                saved = self._readings.add(reading)
            else:
                self._readings.update(reading)
                saved = reading

            if photo_file_name and photo_data and saved.id is not None:
                photo = Photo(
                    file_name=photo_file_name,
                    image_data=photo_data,
                    category=PhotoCategory.METER,
                    description=f"{reading.meter_type.label} vom {reading.date.strftime('%d.%m.%Y')}",
                    reading_id=saved.id,
                    apartment_id=reading.apartment_id,
                )
                photo.validate()
                self._photos.add(photo)
        return saved

    def delete_reading(self, reading_id: int) -> None:
        """Löscht einen Zählerstand samt seiner Fotos.

        Die Fotos werden ausdrücklich einzeln gelöscht (statt das der
        Datenbank per ``CASCADE`` zu überlassen), damit jedes Foto im
        Änderungsprotokoll auftaucht.
        """
        with self._transactions.transaction():
            for photo in self._photos.list_metadata(reading_id=reading_id):
                if photo.id is not None:
                    self._photos.delete(photo.id)
            self._readings.delete(reading_id)
        logger.info("Zählerstand gelöscht (ID %s)", reading_id)

    def consumption_by_apartment(
        self,
        property_id: int,
        meter_type: MeterType,
        period: DateRange,
    ) -> dict[int, Decimal]:
        """Verbrauch je Wohnung einer Immobilie im angegebenen Zeitraum.

        Wohnungen, deren Verbrauch sich nicht bestimmen lässt (zu wenige
        Ablesungen), fehlen im Ergebnis.
        """
        readings_by_apartment: dict[int, list[MeterReading]] = defaultdict(list)
        for reading in self._readings.list_for_property_and_type(property_id, meter_type):
            readings_by_apartment[reading.apartment_id].append(reading)

        result: dict[int, Decimal] = {}
        for apartment_id, readings in readings_by_apartment.items():
            consumption = calculate_consumption(readings, period)
            if consumption is not None:
                result[apartment_id] = consumption
        return result
