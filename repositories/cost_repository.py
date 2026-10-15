"""Repository für Kostenpositionen."""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from decimal import Decimal
from typing import Any, Sequence

from database.db_manager import DatabaseManager
from models.tariff import SplitCalculation
from models.cost import Cost, InvoiceSplit
from models.cost_type import CostType
from models.enums import AllocationKey
from models.exceptions import NotFoundError
from models.value_objects import DateRange, Money
from repositories.base_repository import SqliteRepository
from repositories.interfaces import AuditRecorder, CostTypeRepository


class SqliteCostRepository(SqliteRepository[Cost]):
    """Zugriff auf die Tabelle ``costs``.

    Jede Kostenposition verweist auf eine Kostenart. Damit nicht für jede
    Zeile eine eigene Abfrage nötig ist, werden die (wenigen) Kostenarten vor
    jedem Lesevorgang einmal geladen und beim Zusammenbauen der Entities
    zugeordnet.
    """

    table_name = "costs"
    id_column = "cost_id"
    default_order = "t.period_start, t.cost_type_id, t.cost_id"
    entity_label = "Kostenposition"

    def __init__(
        self,
        database: DatabaseManager,
        audit: AuditRecorder,
        cost_types: CostTypeRepository,
    ) -> None:
        """
        Args:
            database: Die geöffnete Benutzer-Datenbank.
            audit: Empfänger für die Einträge des Änderungsprotokolls.
            cost_types: Repository der Kostenarten.
        """
        super().__init__(database, audit)
        self._cost_types = cost_types
        self._types_by_id: dict[int | None, CostType] = {}

    def list_by_property(self, property_id: int) -> list[Cost]:
        """Alle Kostenpositionen einer Immobilie, älteste zuerst."""
        return self._select("t.property_id = ?", (property_id,))

    def list_by_bundle(self, bundle_id: int) -> list[Cost]:
        """Die Positionen einer Sammelrechnung in der Reihenfolge ihrer Erfassung."""
        return self._select("t.bundle_id = ?", (bundle_id,), order_by="t.cost_id")

    # --- Kostenarten vor dem Lesen bereitstellen ----------------------------
    def find(self, entity_id: int) -> Cost | None:
        self._load_types()
        return super().find(entity_id)

    def _select(
        self,
        where: str = "",
        parameters: Sequence[Any] = (),
        order_by: str | None = None,
    ) -> list[Cost]:
        self._load_types()
        return super()._select(where, parameters, order_by)

    def _load_types(self) -> None:
        """Lädt alle Kostenarten in den Zwischenspeicher dieses Lesevorgangs."""
        self._types_by_id = {cost_type.id: cost_type for cost_type in self._cost_types.list_all()}

    # --- Abbildung Entity <-> Tabellenzeile -------------------------------
    def _to_row(self, entity: Cost) -> dict[str, Any]:
        if entity.cost_type.id is None:
            raise NotFoundError("Die Kostenart der Position wurde noch nicht gespeichert.")
        # Direkte Anteile als JSON {"<Mieter-ID>": Cent}; sortiert, damit
        # gleiche Inhalte immer denselben Text ergeben (wichtig fürs Protokoll).
        shares = {str(tenant_id): share.cents for tenant_id, share in sorted(entity.direct_shares.items())}
        return {
            "property_id": entity.property_id,
            "cost_type_id": entity.cost_type.id,
            "description": entity.description,
            "amount": entity.amount.cents,
            "period_start": entity.period.start.isoformat(),
            "period_end": entity.period.end.isoformat(),
            "allocation_key": entity.allocation_key.value,
            "consumption_share": entity.consumption_share,
            "direct_shares": json.dumps(shares),
            "invoice_date": entity.invoice_date.isoformat() if entity.invoice_date else None,
            "consumption": str(entity.consumption) if entity.consumption is not None else None,
            "consumption_unit": entity.consumption_unit,
            "excluded": int(entity.excluded),
            "difference_key": entity.difference_key.value,
            "bundle_id": entity.bundle_id,
            "co2_cost": entity.co2_cost.cents if entity.co2_cost is not None else None,
            "co2_emission": str(entity.co2_emission) if entity.co2_emission is not None else None,
            "co2_factor": str(entity.co2_factor) if entity.co2_factor is not None else None,
            "splits": json.dumps(
                [
                    {
                        "from": split.period.start.isoformat(),
                        "to": split.period.end.isoformat(),
                        "amount": split.amount.cents,
                        "consumption": str(split.consumption) if split.consumption is not None else None,
                        **_calculation_to_json(split.calculation),
                        **({"manual": True} if split.amount_corrected else {}),
                        **({"difference": True} if split.is_difference else {}),
                        **({"co2_cost": split.co2_cost.cents} if split.co2_cost is not None else {}),
                        **({"co2_kg": str(split.co2_emission)} if split.co2_emission is not None else {}),
                    }
                    for split in entity.splits
                ]
            ),
        }

    def _from_row(self, row: sqlite3.Row) -> Cost:
        cost_type = self._types_by_id.get(row["cost_type_id"])
        if cost_type is None:
            raise NotFoundError(f"Kostenart (ID {row['cost_type_id']}) wurde nicht gefunden.")
        raw_shares: dict[str, int] = json.loads(row["direct_shares"] or "{}")
        return Cost(
            id=row["cost_id"],
            property_id=row["property_id"],
            cost_type=cost_type,
            description=row["description"],
            amount=Money(row["amount"]),
            period=DateRange(
                date.fromisoformat(row["period_start"]),
                date.fromisoformat(row["period_end"]),
            ),
            allocation_key=AllocationKey(row["allocation_key"]),
            consumption_share=row["consumption_share"],
            direct_shares={int(tenant_id): Money(cents) for tenant_id, cents in raw_shares.items()},
            invoice_date=date.fromisoformat(row["invoice_date"]) if row["invoice_date"] else None,
            consumption=Decimal(row["consumption"]) if row["consumption"] else None,
            consumption_unit=row["consumption_unit"],
            excluded=bool(row["excluded"]),
            difference_key=AllocationKey(row["difference_key"]),
            bundle_id=row["bundle_id"],
            co2_cost=Money(row["co2_cost"]) if row["co2_cost"] is not None else None,
            co2_emission=Decimal(row["co2_emission"]) if row["co2_emission"] else None,
            co2_factor=Decimal(row["co2_factor"]) if row["co2_factor"] else None,
            splits=[
                InvoiceSplit(
                    period=DateRange(date.fromisoformat(raw["from"]), date.fromisoformat(raw["to"])),
                    amount=Money(raw["amount"]),
                    consumption=Decimal(raw["consumption"]) if raw["consumption"] is not None else None,
                    calculation=_calculation_from_json(raw.get("calc")),
                    amount_corrected=bool(raw.get("manual", False)),
                    is_difference=bool(raw.get("difference", False)),
                    co2_cost=Money(raw["co2_cost"]) if raw.get("co2_cost") is not None else None,
                    co2_emission=Decimal(raw["co2_kg"]) if raw.get("co2_kg") else None,
                )
                for raw in json.loads(row["splits"] or "[]")
            ],
        )


def _calculation_to_json(calculation: SplitCalculation | None) -> dict[str, Any]:
    """Rechengrundlagen eines Splits als JSON-Teil ``{"calc": {...}}`` - leer, wenn es keine gibt.

    Zahlen werden als Text abgelegt, damit keine Nachkommastelle verloren geht.
    """
    if calculation is None or calculation.is_empty:
        return {}
    return {"calc": {name: str(value) for name, value in vars(calculation).items() if value is not None}}


def _calculation_from_json(raw: Any) -> SplitCalculation | None:
    """Liest die Rechengrundlagen eines Splits; ältere Splits haben keine."""
    if not isinstance(raw, dict) or not raw:
        return None
    known = SplitCalculation.__dataclass_fields__
    return SplitCalculation(**{name: Decimal(str(value)) for name, value in raw.items() if name in known})
