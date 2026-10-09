"""Schema-Version 4: Rechnungsmanagement, Abrechnungszeitraum je Mietverhältnis, Zahlungen entfallen.

Änderungen:

* ``payments`` entfällt. Einzelne Zahlungen werden nicht mehr erfasst; die
  Vorauszahlungen einer Abrechnung ergeben sich aus den vereinbarten Raten
  (``prepayment_rates``). Vor der Migration legt die Anwendung wie immer eine
  Sicherungskopie der Datenbank an - bereits erfasste Zahlungen bleiben dort
  erhalten.
* ``costs`` (die Originalrechnungen) erhält Rechnungsdatum, Verbrauch samt
  Einheit (für die Meldung an den Messdienst) und ein Ausschluss-Kennzeichen.
* ``invoice_group_settings`` merkt sich je Immobilie, Kostenart und Jahr, ob
  die "aggregierte Rechnung" in die Abrechnung übernommen wird, und ihren Namen.
* ``billing_period_overrides`` speichert einen von Hand angepassten
  Abrechnungszeitraum für ein Mietverhältnis.
* Neue Katalog-Kostenart für Brennstoffrechnungen, die nur an den Messdienst
  gemeldet und nicht selbst umgelegt werden.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V4: Final[str] = """
DROP TABLE payments;

ALTER TABLE costs ADD COLUMN invoice_date TEXT;
ALTER TABLE costs ADD COLUMN consumption TEXT;
ALTER TABLE costs ADD COLUMN consumption_unit TEXT NOT NULL DEFAULT '';
ALTER TABLE costs ADD COLUMN excluded INTEGER NOT NULL DEFAULT 0;

CREATE TABLE invoice_group_settings (
    setting_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id  INTEGER NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    cost_type_id INTEGER NOT NULL REFERENCES cost_types(cost_type_id) ON DELETE CASCADE,
    year         INTEGER NOT NULL,
    excluded     INTEGER NOT NULL DEFAULT 0,
    name         TEXT    NOT NULL DEFAULT '',
    UNIQUE (property_id, cost_type_id, year)
);

CREATE TABLE billing_period_overrides (
    override_id  INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    INTEGER NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    period_start TEXT    NOT NULL,
    period_end   TEXT    NOT NULL,
    billed_from  TEXT    NOT NULL,
    billed_to    TEXT    NOT NULL,
    UNIQUE (tenant_id, period_start, period_end)
);

INSERT OR IGNORE INTO cost_types
    (name, legal_basis, default_key, meter_type, heating_rule, is_builtin, sort_order)
VALUES
    ('Brennstoffkosten (Gas/Öl)', '§ 2 Nr. 4 a BetrKV', 'CONSUMPTION_AREA', 'HEATING', 1, 1, 42);
"""
