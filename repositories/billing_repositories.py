"""Repositories für Abrechnungs-Einstellungen: Zeiträume, aggregierte Rechnungen, abweichende Schlüssel."""

from __future__ import annotations

import sqlite3
from datetime import date
from typing import Any

from models.billing import BillingPeriodOverride
from models.enums import AllocationKey
from models.invoice_checklist import CostTypeRelevance
from models.invoice_group import InvoiceGroupSetting
from models.tenant import TenantKeyOverride
from models.value_objects import DateRange
from repositories.base_repository import SqliteRepository


class SqliteBillingPeriodOverrideRepository(SqliteRepository[BillingPeriodOverride]):
    """Zugriff auf die Tabelle ``billing_period_overrides``."""

    table_name = "billing_period_overrides"
    id_column = "override_id"
    default_order = "t.tenant_id, t.period_start"
    entity_label = "Abrechnungszeitraum"

    def list_by_period(self, period: DateRange) -> list[BillingPeriodOverride]:
        """Alle Overrides, die für genau diesen Abrechnungszeitraum gelten."""
        return self._select(
            "t.period_start = ? AND t.period_end = ?",
            (period.start.isoformat(), period.end.isoformat()),
        )

    def _to_row(self, entity: BillingPeriodOverride) -> dict[str, Any]:
        return {
            "tenant_id": entity.tenant_id,
            "period_start": entity.period.start.isoformat(),
            "period_end": entity.period.end.isoformat(),
            "billed_from": entity.billed.start.isoformat(),
            "billed_to": entity.billed.end.isoformat(),
        }

    def _from_row(self, row: sqlite3.Row) -> BillingPeriodOverride:
        return BillingPeriodOverride(
            id=row["override_id"],
            tenant_id=row["tenant_id"],
            period=DateRange(date.fromisoformat(row["period_start"]), date.fromisoformat(row["period_end"])),
            billed=DateRange(date.fromisoformat(row["billed_from"]), date.fromisoformat(row["billed_to"])),
        )


class SqliteInvoiceGroupSettingRepository(SqliteRepository[InvoiceGroupSetting]):
    """Zugriff auf die Tabelle ``invoice_group_settings``."""

    table_name = "invoice_group_settings"
    id_column = "setting_id"
    default_order = "t.year DESC, t.cost_type_id"
    entity_label = "Aggregierte Rechnung"

    def list_by_property(self, property_id: int) -> list[InvoiceGroupSetting]:
        """Alle Einstellungen zu den aggregierten Rechnungen einer Immobilie."""
        return self._select("t.property_id = ?", (property_id,))

    def _to_row(self, entity: InvoiceGroupSetting) -> dict[str, Any]:
        return {
            "property_id": entity.property_id,
            "cost_type_id": entity.cost_type_id,
            "year": entity.year,
            "excluded": int(entity.excluded),
            "name": entity.name,
        }

    def _from_row(self, row: sqlite3.Row) -> InvoiceGroupSetting:
        return InvoiceGroupSetting(
            id=row["setting_id"],
            property_id=row["property_id"],
            cost_type_id=row["cost_type_id"],
            year=row["year"],
            excluded=bool(row["excluded"]),
            name=row["name"],
        )


class SqliteTenantKeyOverrideRepository(SqliteRepository[TenantKeyOverride]):
    """Zugriff auf die Tabelle ``tenant_key_overrides``."""

    table_name = "tenant_key_overrides"
    id_column = "override_id"
    default_order = "t.tenant_id, t.cost_type_id"
    entity_label = "Abweichender Umlageschlüssel"

    def list_by_tenant(self, tenant_id: int) -> list[TenantKeyOverride]:
        """Alle Abweichungen eines Mieters."""
        return self._select("t.tenant_id = ?", (tenant_id,))

    def list_by_property(self, property_id: int) -> list[TenantKeyOverride]:
        """Die Abweichungen aller Mieter einer Immobilie (eine Abfrage für die Abrechnung)."""
        return self._select(
            "t.tenant_id IN (SELECT tn.tenant_id FROM tenants AS tn "
            "JOIN apartments AS ap ON ap.apartment_id = tn.apartment_id "
            "WHERE ap.property_id = ?)",
            (property_id,),
        )

    def _to_row(self, entity: TenantKeyOverride) -> dict[str, Any]:
        return {
            "tenant_id": entity.tenant_id,
            "cost_type_id": entity.cost_type_id,
            "allocation_key": entity.allocation_key.value,
        }

    def _from_row(self, row: sqlite3.Row) -> TenantKeyOverride:
        return TenantKeyOverride(
            id=row["override_id"],
            tenant_id=row["tenant_id"],
            cost_type_id=row["cost_type_id"],
            allocation_key=AllocationKey(row["allocation_key"]),
        )


class SqliteCostTypeRelevanceRepository(SqliteRepository[CostTypeRelevance]):
    """Zugriff auf die Tabelle ``cost_type_relevance``."""

    table_name = "cost_type_relevance"
    id_column = "relevance_id"
    default_order = "t.property_id, t.cost_type_id"
    entity_label = "Relevanz der Kostenart"

    def list_by_property(self, property_id: int) -> list[CostTypeRelevance]:
        """Alle von Hand gesetzten Relevanz-Einträge einer Immobilie."""
        return self._select("t.property_id = ?", (property_id,))

    def _to_row(self, entity: CostTypeRelevance) -> dict[str, Any]:
        return {
            "property_id": entity.property_id,
            "cost_type_id": entity.cost_type_id,
            "relevant": int(entity.relevant),
        }

    def _from_row(self, row: sqlite3.Row) -> CostTypeRelevance:
        return CostTypeRelevance(
            id=row["relevance_id"],
            property_id=row["property_id"],
            cost_type_id=row["cost_type_id"],
            relevant=bool(row["relevant"]),
        )
