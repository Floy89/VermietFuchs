"""Repository für Sammelrechnungen."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from models.invoice_bundle import InvoiceBundle
from models.value_objects import Money
from repositories.base_repository import SqliteRepository


class SqliteInvoiceBundleRepository(SqliteRepository[InvoiceBundle]):
    """Zugriff auf die Tabelle ``invoice_bundles``."""

    table_name = "invoice_bundles"
    id_column = "bundle_id"
    default_order = "t.invoice_date, t.bundle_id"
    entity_label = "Sammelrechnung"

    def list_by_property(self, property_id: int) -> list[InvoiceBundle]:
        """Alle Sammelrechnungen einer Immobilie."""
        return self._select("t.property_id = ?", (property_id,))

    def _to_row(self, entity: InvoiceBundle) -> dict[str, Any]:
        return {
            "property_id": entity.property_id,
            "description": entity.description,
            "invoice_date": entity.invoice_date.isoformat() if entity.invoice_date else None,
            "total_amount": entity.total_amount.cents,
        }

    def _from_row(self, row: sqlite3.Row) -> InvoiceBundle:
        return InvoiceBundle(
            id=row["bundle_id"],
            property_id=row["property_id"],
            description=row["description"],
            invoice_date=date.fromisoformat(row["invoice_date"]) if row["invoice_date"] else None,
            total_amount=Money(row["total_amount"]),
        )
