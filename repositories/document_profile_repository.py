"""Repository für die gelernten Absender-Profile (Belege ohne KI auslesen)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from decimal import Decimal
from typing import Any

from models.document import DocumentProfile, FieldAnchor
from models.enums import AllocationKey
from repositories.base_repository import SqliteRepository


class SqliteDocumentProfileRepository(SqliteRepository[DocumentProfile]):
    """Zugriff auf die Tabelle ``document_profiles``."""

    table_name = "document_profiles"
    id_column = "profile_id"
    default_order = "t.issuer_name, t.profile_id"
    entity_label = "Absender-Profil"

    def find_by_issuer(self, issuer_key: str) -> DocumentProfile | None:
        """Das Profil eines Absenders oder ``None``, wenn noch nichts gelernt wurde."""
        found = self._select("t.issuer_key = ?", (issuer_key,))
        return found[0] if found else None

    def _to_row(self, entity: DocumentProfile) -> dict[str, Any]:
        anchors = {name: {"pattern": a.pattern, "index": a.index} for name, a in sorted(entity.anchors.items())}
        return {
            "issuer_key": entity.issuer_key,
            "issuer_name": entity.issuer_name,
            "cost_type_id": entity.cost_type_id,
            "allocation_key": entity.allocation_key.value if entity.allocation_key else None,
            "description": entity.description,
            "consumption_unit": entity.consumption_unit,
            "vat_percent": str(entity.vat_percent) if entity.vat_percent is not None else None,
            "anchors": json.dumps(anchors, ensure_ascii=False),
            "times_used": entity.times_used,
            "updated_at": entity.updated_at.isoformat(timespec="seconds"),
        }

    def _from_row(self, row: sqlite3.Row) -> DocumentProfile:
        raw_anchors: dict[str, dict[str, Any]] = json.loads(row["anchors"] or "{}")
        return DocumentProfile(
            id=row["profile_id"],
            issuer_key=row["issuer_key"],
            issuer_name=row["issuer_name"],
            cost_type_id=row["cost_type_id"],
            allocation_key=AllocationKey(row["allocation_key"]) if row["allocation_key"] else None,
            description=row["description"],
            consumption_unit=row["consumption_unit"],
            vat_percent=Decimal(row["vat_percent"]) if row["vat_percent"] else None,
            anchors={name: FieldAnchor(raw["pattern"], int(raw.get("index", 0))) for name, raw in raw_anchors.items()},
            times_used=row["times_used"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
