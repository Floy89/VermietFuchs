"""Schema-Version 6: abweichende Umlageschlüssel je Mieter.

Der Umlageschlüssel ist eine Vereinbarung im einzelnen Mietvertrag. Standard
bleibt der Schlüssel, der an der Rechnung eingestellt ist. Nur wenn hier für
einen Mieter und eine Kostenart ein Eintrag steht, wird dessen Anteil nach
dem abweichenden Schlüssel berechnet.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V6: Final[str] = """
CREATE TABLE tenant_key_overrides (
    override_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id      INTEGER NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    cost_type_id   INTEGER NOT NULL REFERENCES cost_types(cost_type_id) ON DELETE CASCADE,
    allocation_key TEXT    NOT NULL,
    UNIQUE (tenant_id, cost_type_id)
);
"""
