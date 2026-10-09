"""Schema-Version 2: Kostenarten als erweiterbarer Katalog.

Bis Version 1 war die Kostenart ein fester Text in ``costs.cost_type`` mit
vier möglichen Werten. Ab Version 2 gibt es die Tabelle ``cost_types``:

* Sie wird mit dem Katalog der Betriebskostenverordnung (§ 2 Nr. 1–17
  BetrKV) und gängigen "sonstigen Betriebskosten" vorbefüllt
  (``is_builtin = 1``).
* Der Benutzer kann eigene Kostenarten ergänzen (``is_builtin = 0``).

Außerdem erhält ``costs`` die Spalte ``direct_shares`` für den neuen
Umlageschlüssel "Direkte Zuordnung": ein JSON-Objekt ``{"<Mieter-ID>": Cent}``
mit den fertig ermittelten Beträgen je Mieter (z. B. laut Messdienst).

WICHTIG: Eine einmal ausgelieferte Migration wird nie mehr verändert -
bestehende Datenbanken haben sie bereits durchlaufen. Änderungen am Katalog
kommen als neue Migration (Version 3, 4, ...).
"""

from __future__ import annotations

from typing import Final

# Spalten: Name, Grundlage, Standard-Schlüssel, Zählerart, Heizkosten-Regel, Reihenfolge
_CATALOG: Final[tuple[tuple[str, str, str, str | None, int, int], ...]] = (
    ("Grundsteuer", "§ 2 Nr. 1 BetrKV", "AREA", None, 0, 10),
    ("Wasserversorgung", "§ 2 Nr. 2 BetrKV", "CONSUMPTION", "COLD_WATER", 0, 20),
    ("Entwässerung", "§ 2 Nr. 3 BetrKV", "AREA", "COLD_WATER", 0, 30),
    ("Niederschlagswasser", "§ 2 Nr. 3 BetrKV", "AREA", None, 0, 35),
    ("Heizung", "§ 2 Nr. 4 BetrKV", "CONSUMPTION_AREA", "HEATING", 1, 40),
    ("Wartung Etagenheizung/Gastherme", "§ 2 Nr. 4 d BetrKV", "UNITS", None, 0, 45),
    ("Warmwasser", "§ 2 Nr. 5 BetrKV", "CONSUMPTION_AREA", "WARM_WATER", 1, 50),
    ("Trinkwasseruntersuchung (Legionellen)", "§ 2 Nr. 5 BetrKV", "AREA", None, 0, 55),
    ("Verbundene Heizungs- und Warmwasserversorgung", "§ 2 Nr. 6 BetrKV", "CONSUMPTION_AREA", "HEATING", 1, 60),
    ("Aufzug", "§ 2 Nr. 7 BetrKV", "AREA", None, 0, 70),
    ("Straßenreinigung", "§ 2 Nr. 8 BetrKV", "AREA", None, 0, 80),
    ("Winterdienst", "§ 2 Nr. 8 BetrKV", "AREA", None, 0, 82),
    ("Müllbeseitigung", "§ 2 Nr. 8 BetrKV", "PERSONS", None, 0, 85),
    ("Gebäudereinigung", "§ 2 Nr. 9 BetrKV", "AREA", None, 0, 90),
    ("Ungezieferbekämpfung", "§ 2 Nr. 9 BetrKV", "AREA", None, 0, 95),
    ("Gartenpflege", "§ 2 Nr. 10 BetrKV", "AREA", None, 0, 100),
    ("Beleuchtung (Allgemeinstrom)", "§ 2 Nr. 11 BetrKV", "AREA", None, 0, 110),
    ("Schornsteinreinigung", "§ 2 Nr. 12 BetrKV", "AREA", None, 0, 120),
    ("Sach- und Haftpflichtversicherung", "§ 2 Nr. 13 BetrKV", "AREA", None, 0, 130),
    ("Hauswart", "§ 2 Nr. 14 BetrKV", "AREA", None, 0, 140),
    ("Gemeinschaftsantenne", "§ 2 Nr. 15 a BetrKV", "UNITS", None, 0, 150),
    ("Breitbandnetz (Betriebsstrom)", "§ 2 Nr. 15 b BetrKV", "UNITS", None, 0, 152),
    ("Glasfaser-Bereitstellungsentgelt", "§ 2 Nr. 15 c BetrKV", "UNITS", None, 0, 154),
    ("Wäschepflege (Gemeinschaftsgeräte)", "§ 2 Nr. 16 BetrKV", "UNITS", None, 0, 160),
    ("Rauchwarnmelder-Wartung", "§ 2 Nr. 17 BetrKV", "UNITS", None, 0, 170),
    ("Dachrinnenreinigung", "§ 2 Nr. 17 BetrKV", "AREA", None, 0, 172),
    ("Feuerlöscher-Wartung", "§ 2 Nr. 17 BetrKV", "AREA", None, 0, 174),
    ("Elektroprüfung (E-Check)", "§ 2 Nr. 17 BetrKV", "AREA", None, 0, 176),
    ("Blitzschutzprüfung", "§ 2 Nr. 17 BetrKV", "AREA", None, 0, 178),
    ("Wartung Lüftungs-/Rauchabzugsanlage", "§ 2 Nr. 17 BetrKV", "AREA", None, 0, 180),
    ("Brandmeldeanlage-Wartung", "§ 2 Nr. 17 BetrKV", "AREA", None, 0, 182),
    ("Sonstige Betriebskosten", "§ 2 Nr. 17 BetrKV", "AREA", None, 0, 900),
)


def _sql_text(value: str | None) -> str:
    """Wandelt einen Python-Wert in ein SQL-Literal um (Hochkommas verdoppelt)."""
    if value is None:
        return "NULL"
    return "'" + value.replace("'", "''") + "'"


def _catalog_inserts() -> str:
    """Erzeugt die ``INSERT``-Anweisung für den mitgelieferten Katalog."""
    rows = ",\n    ".join(
        f"({_sql_text(name)}, {_sql_text(basis)}, {_sql_text(key)}, {_sql_text(meter)}, {heating}, 1, {order})"
        for name, basis, key, meter, heating, order in _CATALOG
    )
    return (
        "INSERT INTO cost_types "
        "(name, legal_basis, default_key, meter_type, heating_rule, is_builtin, sort_order) VALUES\n    "
        f"{rows};"
    )


SCHEMA_V2: Final[str] = f"""
CREATE TABLE cost_types (
    cost_type_id INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    legal_basis  TEXT    NOT NULL DEFAULT '',
    default_key  TEXT    NOT NULL,
    meter_type   TEXT,
    heating_rule INTEGER NOT NULL DEFAULT 0,
    is_builtin   INTEGER NOT NULL DEFAULT 0,
    sort_order   INTEGER NOT NULL DEFAULT 1000
);

{_catalog_inserts()}

-- SQLite kann eine Spalte nicht nachträglich zum Pflicht-Fremdschlüssel
-- machen. Deshalb wird "costs" neu aufgebaut: neue Tabelle anlegen, Daten
-- umkopieren (alte Textwerte auf Katalog-Einträge abbilden), alte löschen.
CREATE TABLE costs_new (
    cost_id           INTEGER PRIMARY KEY AUTOINCREMENT,
    property_id       INTEGER NOT NULL REFERENCES properties(property_id) ON DELETE RESTRICT,
    cost_type_id      INTEGER NOT NULL REFERENCES cost_types(cost_type_id) ON DELETE RESTRICT,
    description       TEXT    NOT NULL DEFAULT '',
    amount            INTEGER NOT NULL,
    period_start      TEXT    NOT NULL,
    period_end        TEXT    NOT NULL,
    allocation_key    TEXT    NOT NULL,
    consumption_share INTEGER NOT NULL DEFAULT 70,
    direct_shares     TEXT    NOT NULL DEFAULT '{{}}'
);

INSERT INTO costs_new
    (cost_id, property_id, cost_type_id, description, amount,
     period_start, period_end, allocation_key, consumption_share)
SELECT
    c.cost_id, c.property_id,
    (SELECT cost_type_id FROM cost_types WHERE name =
        CASE c.cost_type
            WHEN 'HEATING'    THEN 'Verbundene Heizungs- und Warmwasserversorgung'
            WHEN 'COLD_WATER' THEN 'Wasserversorgung'
            WHEN 'WASTE'      THEN 'Müllbeseitigung'
            ELSE 'Sonstige Betriebskosten'
        END),
    c.description, c.amount, c.period_start, c.period_end, c.allocation_key, c.consumption_share
FROM costs AS c;

DROP TABLE costs;
ALTER TABLE costs_new RENAME TO costs;
CREATE INDEX idx_costs_property ON costs(property_id, period_start);
CREATE INDEX idx_costs_type ON costs(cost_type_id);
"""
