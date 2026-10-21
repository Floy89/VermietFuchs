"""Lesender Zugriff auf das Änderungsprotokoll für die Oberfläche."""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Final, Mapping

from models.audit_entry import AuditEntry
from models.enums import (
    AllocationKey,
    AuditAction,
    MeterType,
    PhotoCategory,
)
from models.value_objects import Money
from repositories.interfaces import AuditRepository
from utils.formatters import format_date, format_money

#: Deutsche Bezeichnungen der Tabellen für die Anzeige.
TABLE_LABELS: Final[Mapping[str, str]] = {
    "users": "Benutzer",
    "properties": "Immobilie",
    "apartments": "Wohnung",
    "tenants": "Mieter",
    "meter_readings": "Zählerstand",
    "costs": "Rechnung",
    "cost_types": "Kostenart",
    "payments": "Zahlung",
    "prepayment_rates": "Vorauszahlungsrate",
    "invoice_group_settings": "Aggregierte Rechnung",
    "billing_period_overrides": "Abrechnungszeitraum",
    "tenant_key_overrides": "Abweichender Umlageschlüssel",
    "cost_type_relevance": "Relevanz der Kostenart",
    "photos": "Foto",
    "cost_attachments": "Beleg",
    "invoice_bundles": "Sammelrechnung",
    "document_profiles": "Gelerntes Absender-Profil",
    "settings": "Einstellungen",
}


#: Deutsche Bezeichnungen der Spalten für die Anzeige von Änderungen.
FIELD_LABELS: Final[Mapping[str, str]] = {
    "name": "Name",
    "address": "Adresse",
    "number": "Nummer",
    "area_sqm": "Fläche",
    "person_count": "Personen",
    "person_changes": "Personen-Änderungen",
    "deposit_amount": "Kaution",
    "deposit_paid_on": "Kaution eingezahlt am",
    "deposit_rates": "Zinssätze Kaution",
    "contact": "Kontakt",
    "start_date": "Beginn",
    "valid_from": "gültig ab",
    "valid_to": "gültig bis",
    "invoice_date": "Rechnungsdatum",
    "consumption": "Verbrauch",
    "consumption_unit": "Einheit",
    "excluded": "ausgeschlossen",
    "splits": "Splits",
    "billed_from": "abgerechnet von",
    "billed_to": "abgerechnet bis",
    "year": "Jahr",
    "end_date": "Ende",
    "rent_amount": "Miete",
    "prepayment_amount": "Vorauszahlung",
    "meter_type": "Zählerart",
    "value": "Stand",
    "date": "Datum",
    "reader": "Ableser",
    "note": "Bemerkung",
    "cost_type": "Kostenart",
    "cost_type_id": "Kostenart-Nr.",
    "direct_shares": "Direkte Anteile (Cent je Mieter-Nr.)",
    "legal_basis": "Grundlage",
    "default_key": "Standard-Schlüssel",
    "description": "Beschreibung",
    "amount": "Betrag",
    "period_start": "von",
    "period_end": "bis",
    "allocation_key": "Schlüssel",
    "difference_key": "Verbrauchsdifferenz nach",
    "consumption_share": "Verbrauchsanteil",
    "reference": "Referenz",
    "payment_type": "Art",
    "file_name": "Datei",
    "category": "Kategorie",
}

#: Spalten, deren Werte Cent-Beträge sind und als Euro angezeigt werden.
_MONEY_FIELDS: Final[frozenset[str]] = frozenset({"amount", "rent_amount", "prepayment_amount"})

#: Spalten mit ISO-Datum, die als TT.MM.JJJJ angezeigt werden.
_DATE_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "start_date", "end_date", "date", "period_start", "period_end", "valid_from", "valid_to",
        "invoice_date", "billed_from", "billed_to",
    }
)

#: Spalten mit Enum-Werten, die mit ihrer deutschen Bezeichnung angezeigt werden.
_ENUM_FIELDS: Final[Mapping[str, Any]] = {
    "meter_type": MeterType,
    "default_key": AllocationKey,
    "allocation_key": AllocationKey,
    "difference_key": AllocationKey,
    "category": PhotoCategory,
}

#: Spalten, die einen Datensatz am besten kurz benennen (in dieser Reihenfolge).
_TITLE_FIELDS: Final[tuple[str, ...]] = (
    "name", "number", "description", "reference", "file_name", "date", "valid_from",
)


def table_label(table_name: str) -> str:
    """Deutsche Bezeichnung einer Tabelle (unbekannte Namen bleiben unverändert)."""
    return TABLE_LABELS.get(table_name, table_name)


def describe_change(entry: AuditEntry) -> str:
    """Fasst einen Protokolleintrag in einer lesbaren Zeile zusammen.

    * Änderung: nur die geänderten Felder als "Feld: alt → neu".
    * Anlegen/Löschen: die aussagekräftigsten Felder des Datensatzes.
    """
    old = _parse(entry.old_value)
    new = _parse(entry.new_value)

    if entry.action is AuditAction.UPDATE:
        changes = [
            f"{FIELD_LABELS.get(key, key)}: {_display(key, old.get(key))} → {_display(key, value)}"
            for key, value in new.items()
            if old.get(key) != value
        ]
        return "; ".join(changes)

    values = new if entry.action is AuditAction.INSERT else old
    parts = [_display(key, values[key]) for key in _TITLE_FIELDS if values.get(key)]
    if "amount" in values:
        parts.append(_display("amount", values["amount"]))
    return ", ".join(parts[:3])


def _parse(raw: str | None) -> dict[str, Any]:
    """Liest den JSON-Text eines Protokolleintrags; fehlerhafte Texte ergeben ``{}``."""
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _display(key: str, value: Any) -> str:
    """Formatiert einen einzelnen Spaltenwert für die Anzeige."""
    if value is None or value == "":
        return "–"
    if key in _MONEY_FIELDS and isinstance(value, int):
        return format_money(Money(value))
    if key in _DATE_FIELDS and isinstance(value, str):
        try:
            return format_date(date.fromisoformat(value))
        except ValueError:
            return value
    enum_type = _ENUM_FIELDS.get(key)
    if enum_type is not None:
        try:
            return str(enum_type(value).label)
        except ValueError:
            return str(value)
    return str(value)


class AuditService:
    """Stellt das Änderungsprotokoll für Dashboard und Berichte bereit."""

    def __init__(self, audit: AuditRepository) -> None:
        self._audit = audit

    def list_entries(self, limit: int = 500, table_name: str | None = None) -> list[AuditEntry]:
        """Die neuesten Einträge, optional nur für eine Tabelle."""
        return self._audit.list_entries(limit=limit, table_name=table_name)

    def list_table_names(self) -> list[str]:
        """Alle Tabellen, zu denen Protokolleinträge existieren."""
        return self._audit.list_table_names()
