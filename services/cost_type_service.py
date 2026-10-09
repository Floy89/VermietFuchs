"""Anwendungsfälle rund um Kostenarten (Katalog + eigene Kostenarten)."""

from __future__ import annotations

import logging

from models.cost_type import CostType
from models.exceptions import ValidationError
from repositories.interfaces import CostTypeRepository

logger = logging.getLogger(__name__)


class CostTypeService:
    """Listet Kostenarten auf und verwaltet die vom Benutzer angelegten."""

    def __init__(self, cost_types: CostTypeRepository) -> None:
        self._cost_types = cost_types

    def list_types(self) -> list[CostType]:
        """Alle Kostenarten: erst der Katalog in fester Reihenfolge, dann die eigenen."""
        return self._cost_types.list_all()

    def get_by_name(self, name: str) -> CostType:
        """Sucht eine Kostenart anhand ihres Namens.

        Raises:
            ValidationError: Wenn es keine Kostenart mit diesem Namen gibt.
        """
        wanted = name.casefold()
        for cost_type in self._cost_types.list_all():
            if cost_type.name.casefold() == wanted:
                return cost_type
        raise ValidationError(f"Die Kostenart '{name}' gibt es nicht.")

    def save_type(self, cost_type: CostType) -> CostType:
        """Speichert eine neue oder geänderte *eigene* Kostenart.

        Raises:
            ValidationError: Bei ungültigen Angaben, doppeltem Namen oder
                beim Versuch, einen Katalog-Eintrag zu ändern.
        """
        if cost_type.is_builtin:
            raise ValidationError("Kostenarten aus dem mitgelieferten Katalog können nicht geändert werden.")
        cost_type.validate()
        self._ensure_unique_name(cost_type)
        if cost_type.id is None:
            saved = self._cost_types.add(cost_type)
            logger.info("Kostenart angelegt: %s (ID %s)", saved.name, saved.id)
            return saved
        self._cost_types.update(cost_type)
        return cost_type

    def delete_type(self, cost_type_id: int) -> None:
        """Löscht eine eigene Kostenart.

        Raises:
            ValidationError: Bei Katalog-Einträgen oder wenn die Kostenart
                noch in Kostenpositionen verwendet wird.
        """
        cost_type = self._cost_types.get(cost_type_id)
        if cost_type.is_builtin:
            raise ValidationError("Kostenarten aus dem mitgelieferten Katalog können nicht gelöscht werden.")
        used = self._cost_types.count_costs(cost_type_id)
        if used:
            raise ValidationError(
                f"Die Kostenart '{cost_type.name}' wird noch in {used} Kostenposition(en) verwendet."
            )
        self._cost_types.delete(cost_type_id)
        logger.info("Kostenart gelöscht: %s", cost_type.name)

    def _ensure_unique_name(self, cost_type: CostType) -> None:
        """Verhindert zwei Kostenarten mit demselben Namen."""
        wanted = cost_type.name.casefold()
        for other in self._cost_types.list_all():
            if other.id != cost_type.id and other.name.casefold() == wanted:
                raise ValidationError(f"Es gibt bereits eine Kostenart namens '{other.name}'.")
