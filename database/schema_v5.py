"""Schema-Version 5: Splits (Teilabschnitte) je Rechnung.

``costs.splits`` enthält eine JSON-Liste der Abschnitte einer Rechnung::

    [{"from": "2024-01-01", "to": "2024-02-17", "amount": 91630, "consumption": "10692"}, ...]

``amount`` in Cent, ``consumption`` als Dezimaltext oder ``null``. Eine leere
Liste (Standard) bedeutet: Rechnung ohne Splits - sie wird wie bisher
taggenau linear über ihren Rechnungszeitraum verteilt.

Die Splits stehen als JSON bei der Rechnung statt in einer eigenen Tabelle,
weil sie nie ohne ihre Rechnung gelesen oder geändert werden und so
automatisch im Änderungsprotokoll der Rechnung erscheinen.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V5: Final[str] = """
ALTER TABLE costs ADD COLUMN splits TEXT NOT NULL DEFAULT '[]';
"""
