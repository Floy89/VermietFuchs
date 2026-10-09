"""Schema-Version 10: Sammelrechnungen (eine Rechnung mit mehreren Kostenarten).

Beispiel: der jährliche Grundbesitzabgabenbescheid der Stadt mit Grundsteuer,
Müllabfuhr, Straßenreinigung und Niederschlagswasser. Jede Kostenart bleibt
eine eigene Zeile in ``costs`` (mit eigenem Umlageschlüssel) - die neue
Tabelle ``invoice_bundles`` hält sie zusammen und trägt, was für alle gilt:
Rechnungssteller, Rechnungsdatum, Gesamtbetrag und die Belege.

``cost_attachments`` wird dafür neu aufgebaut: Ein Beleg gehört jetzt
*entweder* zu einer einzelnen Rechnung *oder* zu einer Sammelrechnung
(SQLite kann ``NOT NULL`` nicht nachträglich von einer Spalte entfernen).
"""

from __future__ import annotations

from typing import Final

SCHEMA_V10: Final[str] = """
CREATE TABLE invoice_bundles (
    bundle_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id  INTEGER NOT NULL REFERENCES properties(property_id) ON DELETE CASCADE,
    description  TEXT    NOT NULL DEFAULT '',
    invoice_date TEXT,
    total_amount INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_invoice_bundles_property ON invoice_bundles(property_id);

ALTER TABLE costs ADD COLUMN bundle_id INTEGER REFERENCES invoice_bundles(bundle_id) ON DELETE SET NULL;
CREATE INDEX idx_costs_bundle ON costs(bundle_id);

CREATE TABLE cost_attachments_new (
    attachment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    cost_id       INTEGER REFERENCES costs(cost_id) ON DELETE CASCADE,
    bundle_id     INTEGER REFERENCES invoice_bundles(bundle_id) ON DELETE CASCADE,
    file_name     TEXT    NOT NULL,
    file_data     BLOB    NOT NULL,
    uploaded_at   TEXT    NOT NULL,
    -- genau ein Besitzer: Rechnung oder Sammelrechnung
    CHECK ((cost_id IS NULL) <> (bundle_id IS NULL))
);
INSERT INTO cost_attachments_new (attachment_id, cost_id, file_name, file_data, uploaded_at)
    SELECT attachment_id, cost_id, file_name, file_data, uploaded_at FROM cost_attachments;
DROP TABLE cost_attachments;
ALTER TABLE cost_attachments_new RENAME TO cost_attachments;
CREATE INDEX idx_cost_attachments_cost ON cost_attachments(cost_id);
CREATE INDEX idx_cost_attachments_bundle ON cost_attachments(bundle_id);
"""
