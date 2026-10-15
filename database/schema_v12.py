"""Schema-Version 12: CO2-Angaben je Rechnung (CO2-Kostenaufteilungsgesetz).

Brennstoff- und Heizungsrechnungen weisen seit 2023 die CO2-Kosten und den
CO2-Ausstoß aus. Der Vermieter meldet sie (zusammen mit Verbrauch und Betrag)
an den Messdienst, der daraus die Aufteilung zwischen Mieter und Vermieter
nach dem Stufenmodell berechnet.

* ``co2_cost``: CO2-Kosten brutto in Cent,
* ``co2_emission``: CO2-Ausstoß in kg (Text, damit keine Nachkommastelle verloren geht),
* ``co2_factor``: Emissionsfaktor in kg CO2 je kWh (optional).

Alle Spalten sind optional; ältere Rechnungen haben keine CO2-Angaben.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V12: Final[str] = """
ALTER TABLE costs ADD COLUMN co2_cost INTEGER;
ALTER TABLE costs ADD COLUMN co2_emission TEXT;
ALTER TABLE costs ADD COLUMN co2_factor TEXT;
"""
