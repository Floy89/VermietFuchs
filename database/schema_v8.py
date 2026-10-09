"""Schema-Version 8: Verteilung der Differenz zwischen Gesamtverbrauch und Wohnungszählern.

Nennt eine nach Verbrauch umgelegte Rechnung einen Gesamtverbrauch (z. B.
200 m³ Wasser am Hauptzähler), der höher ist als die Summe der Wohnungszähler
(148 m³), legt ``costs.difference_key`` fest, wonach die Differenz verteilt
wird. Der Standard ``CONSUMPTION`` entspricht dem bisherigen Verhalten:
anteilig wie der gemessene Verbrauch.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V8: Final[str] = """
ALTER TABLE costs ADD COLUMN difference_key TEXT NOT NULL DEFAULT 'CONSUMPTION';
"""
