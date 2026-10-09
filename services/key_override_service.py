"""Abweichende Umlageschlüssel je Mieter (laut Mietvertrag)."""

from __future__ import annotations

import logging
from dataclasses import replace

from models.cost_type import CostType
from models.enums import AllocationKey
from models.exceptions import ValidationError
from models.tenant import TenantKeyOverride
from repositories.interfaces import (
    CostTypeRepository,
    TenantKeyOverrideRepository,
    TenantRepository,
)

logger = logging.getLogger(__name__)


class KeyOverrideService:
    """Verwaltet, für welche Kostenart ein Mieter anders abgerechnet wird als der Standard.

    Standard ist immer der Schlüssel der Rechnung. Ein Eintrag hier gilt nur
    für *diesen* Mieter und *diese* Kostenart; alle anderen Mieter und
    Kostenarten bleiben beim Standard.
    """

    def __init__(
        self,
        overrides: TenantKeyOverrideRepository,
        tenants: TenantRepository,
        cost_types: CostTypeRepository,
    ) -> None:
        self._overrides = overrides
        self._tenants = tenants
        self._cost_types = cost_types

    def list_for_tenant(self, tenant_id: int) -> list[TenantKeyOverride]:
        """Alle Abweichungen eines Mieters."""
        return self._overrides.list_by_tenant(tenant_id)

    def overridable_types(self) -> list[CostType]:
        """Kostenarten, für die ein abweichender Schlüssel überhaupt möglich ist."""
        return [cost_type for cost_type in self._cost_types.list_all() if cost_type.override_keys()]

    def keys_by_tenant(self, property_id: int) -> dict[tuple[int, int], AllocationKey]:
        """Alle Abweichungen einer Immobilie als ``{(Mieter-ID, Kostenart-ID): Schlüssel}``."""
        return {
            (override.tenant_id, override.cost_type_id): override.allocation_key
            for override in self._overrides.list_by_property(property_id)
        }

    def set_override(self, tenant_id: int, cost_type_id: int, key: AllocationKey) -> None:
        """Legt für einen Mieter und eine Kostenart einen abweichenden Schlüssel fest.

        Raises:
            ValidationError: Wenn der Schlüssel für die Kostenart nicht
                möglich ist (z. B. Heizkosten oder Verbrauch ohne Zähler).
            NotFoundError: Wenn Mieter oder Kostenart nicht existieren.
        """
        self._tenants.get(tenant_id)
        cost_type = self._cost_types.get(cost_type_id)
        allowed = cost_type.override_keys()
        if not allowed:
            raise ValidationError(
                f"Für '{cost_type.name}' ist kein abweichender Schlüssel möglich - "
                "die Verteilung ist gesetzlich vorgegeben."
            )
        if key not in allowed:
            labels = ", ".join(k.label for k in allowed)
            raise ValidationError(f"Für '{cost_type.name}' sind nur diese Schlüssel möglich: {labels}.")

        existing = next(
            (o for o in self._overrides.list_by_tenant(tenant_id) if o.cost_type_id == cost_type_id), None
        )
        if existing is None:
            self._overrides.add(TenantKeyOverride(tenant_id, cost_type_id, key))
        else:
            self._overrides.update(replace(existing, allocation_key=key))
        logger.info("Abweichender Schlüssel: Mieter %s, Kostenart %s -> %s", tenant_id, cost_type.name, key.value)

    def remove_override(self, override_id: int) -> None:
        """Entfernt eine Abweichung - es gilt wieder der Standard der Rechnung."""
        self._overrides.delete(override_id)
