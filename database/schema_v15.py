"""Schema-Version 15: Mietkaution je Mietverhältnis.

* ``deposit_amount``: eingezahlte Kaution in Cent (0 = keine Kaution erfasst),
* ``deposit_paid_on``: Tag der Einzahlung (leer = Vertragsbeginn),
* ``deposit_rates``: Zinssätze des Kautionskontos als JSON-Liste
  ``[{"from": "2024-01-01", "percent": "0.5"}, ...]``.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V15: Final[str] = """
ALTER TABLE tenants ADD COLUMN deposit_amount INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tenants ADD COLUMN deposit_paid_on TEXT;
ALTER TABLE tenants ADD COLUMN deposit_rates TEXT NOT NULL DEFAULT '[]';
"""
