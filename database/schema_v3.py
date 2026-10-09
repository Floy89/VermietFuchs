"""Schema-Version 3: Historie der vereinbarten Vorauszahlungen.

Bis Version 2 hatte jeder Mieter genau einen Wert ``tenants.prepayment_amount``.
Eine Erhöhung ließ sich damit nicht abbilden. Ab Version 3 gibt es je Mieter
beliebig viele Raten mit Gültigkeitszeitraum in ``prepayment_rates``.

Migration: Der bisherige Einzelwert wird als erste Rate übernommen (gültig ab
Vertragsbeginn, ohne Enddatum); danach entfällt die alte Spalte, damit es nur
noch eine Quelle der Wahrheit gibt.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V3: Final[str] = """
CREATE TABLE prepayment_rates (
    rate_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id  INTEGER NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    amount     INTEGER NOT NULL CHECK (amount >= 0),
    valid_from TEXT    NOT NULL,
    valid_to   TEXT,
    note       TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX idx_prepayment_rates_tenant ON prepayment_rates(tenant_id, valid_from);

INSERT INTO prepayment_rates (tenant_id, amount, valid_from)
SELECT tenant_id, prepayment_amount, start_date
FROM tenants
WHERE prepayment_amount > 0;

ALTER TABLE tenants DROP COLUMN prepayment_amount;
"""
