"""Value Objects: aus einem Beleg oder Zählerfoto ausgelesene Angaben (Vorschläge)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from models.value_objects import DateRange, Money


@dataclass(frozen=True)
class ReceiptSuggestion:
    """Was die optionale Belegerkennung auf einer Rechnung gefunden hat.

    Alle Felder sind *Vorschläge*: Ein Sprachmodell kann sich verlesen. Die
    Oberfläche trägt die Werte nur in das Formular ein - gespeichert wird
    erst, wenn der Benutzer sie geprüft und bestätigt hat. Nicht erkannte
    Angaben bleiben leer bzw. ``None``.

    Attributes:
        vendor: Rechnungssteller, z. B. "Stadtwerke Musterstadt".
        invoice_number: Rechnungs- oder Bescheidnummer.
        invoice_date: Rechnungsdatum.
        period: Leistungs- bzw. Abrechnungszeitraum laut Beleg.
        total_amount: Rechnungsbetrag (brutto).
        cost_type_name: Name der am besten passenden Kostenart aus dem Katalog.
    """

    vendor: str = ""
    invoice_number: str = ""
    invoice_date: date | None = None
    period: DateRange | None = None
    total_amount: Money | None = None
    cost_type_name: str = ""

    @property
    def description(self) -> str:
        """Vorschlag für das Beschreibungsfeld, z. B. "Stadtwerke, Rechnung 4711"."""
        parts = [self.vendor]
        if self.invoice_number:
            parts.append(f"Rechnung {self.invoice_number}")
        return ", ".join(part for part in parts if part)

    @property
    def is_empty(self) -> bool:
        """``True``, wenn auf dem Beleg nichts Verwertbares erkannt wurde."""
        return not (self.vendor or self.invoice_number or self.period or self.total_amount or self.cost_type_name)


@dataclass(frozen=True)
class MeterSuggestion:
    """Was die optionale Erkennung auf dem Foto eines Zählers gelesen hat.

    Wie beim Beleg gilt: Es ist ein *Vorschlag*. Gerade bei Rollenzählwerken
    kann sich ein Modell um eine Ziffer verlesen - der Wert wird nur ins
    Formular eingetragen und muss vom Benutzer mit dem Foto verglichen werden.

    Attributes:
        value: Der abgelesene Zählerstand.
        meter_number: Aufgedruckte Zähler- bzw. Gerätenummer (leer, wenn nicht erkannt).
    """

    value: Decimal
    meter_number: str = ""
