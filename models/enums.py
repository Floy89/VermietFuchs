"""Aufzählungstypen der Domäne.

Alle Enums erben zusätzlich von ``str``. Dadurch lässt sich ihr Wert direkt
in SQLite speichern und als JSON ausgeben, ohne Umwandlungscode. Die deutsche
Bezeichnung für die Oberfläche liefert jeweils die Eigenschaft ``label``.
"""

from __future__ import annotations

from enum import Enum


class MeterType(str, Enum):
    """Art eines Zählers in einer Wohnung."""

    HEATING = "HEATING"
    WARM_WATER = "WARM_WATER"
    #: Zweiter Name für die Warmwasserzähler (z. B. Ablesewerte aus der ISTA-Jahresabrechnung).
    #: Bewusst kein eigener Typ: Es sind dieselben Zähler, gespeichert als ``WARM_WATER``.
    HOT_WATER = "WARM_WATER"
    COLD_WATER = "COLD_WATER"
    ELECTRICITY = "ELECTRICITY"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung."""
        return _METER_LABELS[self]

    @property
    def unit(self) -> str:
        """Maßeinheit des Zählers."""
        return _METER_UNITS[self]


class AllocationKey(str, Enum):
    """Umlageschlüssel: Wonach wird eine Kostenposition verteilt?"""

    AREA = "AREA"
    PERSONS = "PERSONS"
    CONSUMPTION = "CONSUMPTION"
    CONSUMPTION_AREA = "CONSUMPTION_AREA"
    #: Jede Wohnung trägt den gleichen Anteil (z. B. Grundpreise).
    UNITS = "UNITS"
    #: Fertige Beträge je Mieter, z. B. aus der Abrechnung eines Messdienstes.
    DIRECT = "DIRECT"
    #: Nur als Verteilung der Verbrauchsdifferenz einer Wasserrechnung: Grundgebühr je Wohnung,
    #: Kaltwasser für die Warmwasserbereitung getrennt, Restmenge nach Wohnfläche.
    WATER_RULES = "WATER_RULES"
    #: Nicht umlegbar: Der Eigentümer trägt die Kosten allein (z. B. Miete der Rauchwarnmelder).
    OWNER = "OWNER"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung."""
        return _ALLOCATION_LABELS[self]


class PhotoCategory(str, Enum):
    """Kategorie eines Dokumentationsfotos."""

    METER = "METER"
    DEFECT = "DEFECT"
    OTHER = "OTHER"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung."""
        return _PHOTO_LABELS[self]


class AuditAction(str, Enum):
    """Art einer protokollierten Änderung."""

    INSERT = "INSERT"
    UPDATE = "UPDATE"
    DELETE = "DELETE"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung."""
        return _AUDIT_LABELS[self]


# Die Zuordnungstabellen stehen bewusst unterhalb der Klassen: Sie brauchen
# die fertigen Enum-Mitglieder als Schlüssel.
_METER_LABELS: dict[MeterType, str] = {
    MeterType.HEATING: "Heizung",
    MeterType.WARM_WATER: "Warmwasser",
    MeterType.COLD_WATER: "Kaltwasser",
    MeterType.ELECTRICITY: "Strom",
}

_METER_UNITS: dict[MeterType, str] = {
    MeterType.HEATING: "kWh",
    MeterType.WARM_WATER: "m³",
    MeterType.COLD_WATER: "m³",
    MeterType.ELECTRICITY: "kWh",
}

_ALLOCATION_LABELS: dict[AllocationKey, str] = {
    AllocationKey.AREA: "Wohnfläche",
    AllocationKey.PERSONS: "Personenzahl",
    AllocationKey.CONSUMPTION: "Verbrauch",
    AllocationKey.CONSUMPTION_AREA: "Verbrauch + Wohnfläche",
    AllocationKey.UNITS: "Wohneinheiten",
    AllocationKey.DIRECT: "Direkte Zuordnung",
    AllocationKey.WATER_RULES: "Wasserregel",
    AllocationKey.OWNER: "Nicht umlegbar (trägt Eigentümer)",
}

_PHOTO_LABELS: dict[PhotoCategory, str] = {
    PhotoCategory.METER: "Zählerstand",
    PhotoCategory.DEFECT: "Mangel",
    PhotoCategory.OTHER: "Sonstiges",
}

_AUDIT_LABELS: dict[AuditAction, str] = {
    AuditAction.INSERT: "Angelegt",
    AuditAction.UPDATE: "Geändert",
    AuditAction.DELETE: "Gelöscht",
}
