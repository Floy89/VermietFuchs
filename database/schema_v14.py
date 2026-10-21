"""Schema-Version 14: Belegungshistorie je Wohnung.

Die Personenzahl einer Wohnung ändert sich (Geburt, Einzug, Auszug). Damit
Abrechnungen früherer Jahre dabei unverändert bleiben, bekommt jede Änderung ein
"gültig ab"-Datum. ``person_count`` bleibt die Personenzahl von Anfang an.

* ``person_changes``: JSON-Liste ``[{"from": "2026-07-01", "persons": 3}, ...]``,
  leer = die Personenzahl hat sich nie geändert.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V14: Final[str] = """
ALTER TABLE apartments ADD COLUMN person_changes TEXT NOT NULL DEFAULT '[]';
"""
