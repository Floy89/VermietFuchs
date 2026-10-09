"""SQL-Definition des Datenbankschemas (Version 1).

Spätere Änderungen stehen in eigenen Modulen (``schema_v2.py`` ...) und
werden von ``migrations.py`` der Reihe nach eingespielt.

Konventionen:

* Geldbeträge werden als ganze Cent in ``INTEGER``-Spalten gespeichert.
* Dezimalzahlen (Flächen, Zählerstände) stehen als Text in der Datenbank und
  werden in Python zu ``Decimal`` - so gehen keine Nachkommastellen verloren.
* Datumswerte sind ISO-Texte (``JJJJ-MM-TT``), Zeitstempel ISO-Texte mit Uhrzeit.
* Fremdschlüssel verwenden ``ON DELETE RESTRICT``: Die Datenbank verweigert
  das Löschen, solange abhängige Datensätze existieren. Nur Fotos eines
  Zählerstands werden mit diesem zusammen gelöscht (``CASCADE``).

Abweichungen von der ursprünglichen Tabellenskizze (bewusste Entscheidungen):

* ``meter_readings.photo_id`` entfällt. ``photos.reading_id`` zeigt bereits
  auf den Zählerstand; ein zweiter Verweis in Gegenrichtung wäre eine
  zirkuläre Abhängigkeit und würde nur ein Foto je Ablesung erlauben.
* ``photos`` hat zusätzlich ``apartment_id``, ``category``, ``description``
  und ``file_name``, damit auch Mängel ohne Zählerstand dokumentiert werden
  können.
* ``tenants.prepayment_amount`` und ``payments.payment_type`` trennen die
  Nebenkosten-Vorauszahlung von der Miete - ohne diese Trennung lässt sich
  kein Saldo berechnen.
* ``costs.consumption_share`` und ``costs.description`` ergänzen den
  Umlageschlüssel um den Verbrauchsanteil (50–70 %) und einen Freitext.
* ``settings`` speichert die Absenderdaten für die PDF-Abrechnung.
"""

from __future__ import annotations

from typing import Final

SCHEMA_V1: Final[str] = """
CREATE TABLE users (
    user_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL UNIQUE,
    created_at  TEXT    NOT NULL,
    last_login  TEXT
);

CREATE TABLE properties (
    property_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT    NOT NULL,
    address     TEXT    NOT NULL,
    created_at  TEXT    NOT NULL
);

CREATE TABLE apartments (
    apartment_id INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id  INTEGER NOT NULL REFERENCES properties(property_id) ON DELETE RESTRICT,
    number       TEXT    NOT NULL,
    area_sqm     TEXT    NOT NULL,
    person_count INTEGER NOT NULL DEFAULT 1 CHECK (person_count >= 0),
    created_at   TEXT    NOT NULL,
    UNIQUE (property_id, number)
);
CREATE INDEX idx_apartments_property ON apartments(property_id);

CREATE TABLE tenants (
    tenant_id         INTEGER PRIMARY KEY AUTOINCREMENT,
    apartment_id      INTEGER NOT NULL REFERENCES apartments(apartment_id) ON DELETE RESTRICT,
    name              TEXT    NOT NULL,
    contact           TEXT    NOT NULL DEFAULT '',
    start_date        TEXT    NOT NULL,
    end_date          TEXT,
    rent_amount       INTEGER NOT NULL DEFAULT 0,
    prepayment_amount INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX idx_tenants_apartment ON tenants(apartment_id);

CREATE TABLE meter_readings (
    reading_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    apartment_id INTEGER NOT NULL REFERENCES apartments(apartment_id) ON DELETE RESTRICT,
    meter_type   TEXT    NOT NULL,
    value        TEXT    NOT NULL,
    date         TEXT    NOT NULL,
    reader       TEXT    NOT NULL DEFAULT '',
    note         TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX idx_readings_apartment ON meter_readings(apartment_id, meter_type, date);

CREATE TABLE costs (
    cost_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id       INTEGER NOT NULL REFERENCES properties(property_id) ON DELETE RESTRICT,
    cost_type         TEXT    NOT NULL,
    description       TEXT    NOT NULL DEFAULT '',
    amount            INTEGER NOT NULL,
    period_start      TEXT    NOT NULL,
    period_end        TEXT    NOT NULL,
    allocation_key    TEXT    NOT NULL,
    consumption_share INTEGER NOT NULL DEFAULT 70
);
CREATE INDEX idx_costs_property ON costs(property_id, period_start);

CREATE TABLE payments (
    payment_id   INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id    INTEGER NOT NULL REFERENCES tenants(tenant_id) ON DELETE RESTRICT,
    amount       INTEGER NOT NULL,
    date         TEXT    NOT NULL,
    reference    TEXT    NOT NULL DEFAULT '',
    payment_type TEXT    NOT NULL DEFAULT 'PREPAYMENT'
);
CREATE INDEX idx_payments_tenant ON payments(tenant_id, date);

CREATE TABLE photos (
    photo_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    reading_id   INTEGER REFERENCES meter_readings(reading_id) ON DELETE CASCADE,
    apartment_id INTEGER REFERENCES apartments(apartment_id) ON DELETE RESTRICT,
    category     TEXT    NOT NULL DEFAULT 'OTHER',
    description  TEXT    NOT NULL DEFAULT '',
    file_name    TEXT    NOT NULL,
    image_data   BLOB    NOT NULL,
    uploaded_at  TEXT    NOT NULL
);
CREATE INDEX idx_photos_reading ON photos(reading_id);
CREATE INDEX idx_photos_apartment ON photos(apartment_id);

CREATE TABLE audit_log (
    log_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    table_name TEXT    NOT NULL,
    record_id  INTEGER NOT NULL,
    action     TEXT    NOT NULL,
    old_value  TEXT,
    new_value  TEXT,
    user_id    INTEGER REFERENCES users(user_id) ON DELETE SET NULL,
    timestamp  TEXT    NOT NULL
);
CREATE INDEX idx_audit_timestamp ON audit_log(timestamp);
CREATE INDEX idx_audit_record ON audit_log(table_name, record_id);

CREATE TABLE settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""
