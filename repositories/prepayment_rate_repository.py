"""Repository für die Historie der vereinbarten Vorauszahlungen."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from models.prepayment_rate import PrepaymentRate
from models.value_objects import Money
from repositories.base_repository import SqliteRepository


class SqlitePrepaymentRateRepository(SqliteRepository[PrepaymentRate]):
    """Zugriff auf die Tabelle ``prepayment_rates``."""

    table_name = "prepayment_rates"
    id_column = "rate_id"
    default_order = "t.tenant_id, t.valid_from"
    entity_label = "Vorauszahlungsrate"

    def list_by_tenant(self, tenant_id: int) -> list[PrepaymentRate]:
        """Alle Raten eines Mieters, älteste zuerst."""
        return self._select("t.tenant_id = ?", (tenant_id,))

    def list_by_property(self, property_id: int) -> list[PrepaymentRate]:
        """Die Raten aller Mieter einer Immobilie (eine Abfrage für die Abrechnung)."""
        return self._select(
            "t.tenant_id IN (SELECT tn.tenant_id FROM tenants AS tn "
            "JOIN apartments AS ap ON ap.apartment_id = tn.apartment_id "
            "WHERE ap.property_id = ?)",
            (property_id,),
        )

    def _to_row(self, entity: PrepaymentRate) -> dict[str, Any]:
        return {
            "tenant_id": entity.tenant_id,
            "amount": entity.amount.cents,
            "valid_from": entity.valid_from.isoformat(),
            "valid_to": entity.valid_to.isoformat() if entity.valid_to else None,
            "note": entity.note,
        }

    def _from_row(self, row: sqlite3.Row) -> PrepaymentRate:
        return PrepaymentRate(
            id=row["rate_id"],
            tenant_id=row["tenant_id"],
            amount=Money(row["amount"]),
            valid_from=date.fromisoformat(row["valid_from"]),
            valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            note=row["note"],
        )
