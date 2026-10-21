"""Schema-Version 13: Heizkostenrechnung enthält das Kaltwasser für die Warmwasserbereitung.

Die Abrechnung des Messdiensts für die verbundene Heizungs- und Warmwasserversorgung
kann die Kosten des Kaltwassers für die Warmwasserbereitung schon enthalten. Ist das
bei einer Rechnung der Fall, weist die Wasserregel diesen Anteil nicht noch einmal
als eigene Position aus.

* ``includes_cold_water``: 1 = die Rechnung enthält das Kaltwasser für die
  Warmwasserbereitung, 0 = nicht (Standard).
"""

from __future__ import annotations

from typing import Final

SCHEMA_V13: Final[str] = """
ALTER TABLE costs ADD COLUMN includes_cold_water INTEGER NOT NULL DEFAULT 0;
"""
