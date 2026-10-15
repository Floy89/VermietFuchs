"""Schema-Version 11: Was die Anwendung beim Auslesen von Belegen je Absender gelernt hat.

Eine Zeile je Absender (erkannt an Umsatzsteuer-ID, Internet-Adresse oder
Firmenname). Sie hält fest, welche Kostenart, Beschreibung und welchen
Umlageschlüssel der Benutzer für diesen Absender gewählt hat und an welcher
Stelle im Beleg die Werte standen (``anchors`` als JSON). Liegt in der
Benutzerdatenbank - das Gelernte gehört zu den Daten des Benutzers.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V11: Final[str] = """
CREATE TABLE document_profiles (
    profile_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    issuer_key       TEXT    NOT NULL UNIQUE,
    issuer_name      TEXT    NOT NULL DEFAULT '',
    cost_type_id     INTEGER REFERENCES cost_types(cost_type_id) ON DELETE SET NULL,
    allocation_key   TEXT,
    description      TEXT    NOT NULL DEFAULT '',
    consumption_unit TEXT    NOT NULL DEFAULT '',
    vat_percent      TEXT,
    anchors          TEXT    NOT NULL DEFAULT '{}',
    times_used       INTEGER NOT NULL DEFAULT 0,
    updated_at       TEXT    NOT NULL
);
"""
