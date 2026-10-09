"""Schema-Version 7: Relevanz von Kostenarten je Immobilie (für die Rechnungs-Checkliste).

Ein Eintrag bedeutet: Der Benutzer hat von Hand festgelegt, dass eine
Kostenart für eine Immobilie (nicht) abgerechnet wird. Ohne Eintrag leitet
die Anwendung das aus den erfassten Rechnungen ab.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V7: Final[str] = """
CREATE TABLE cost_type_relevance (
    relevance_id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id  INTEGER NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    cost_type_id INTEGER NOT NULL REFERENCES cost_types(cost_type_id) ON DELETE CASCADE,
    relevant     INTEGER NOT NULL,
    UNIQUE (property_id, cost_type_id)
);
"""
