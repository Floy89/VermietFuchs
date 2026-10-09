"""Entity: Sammelrechnung - eine Rechnung, die mehrere Kostenarten enthält."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from models.value_objects import Money
from utils.validators import optional_text


@dataclass
class InvoiceBundle:
    """Der gemeinsame Kopf mehrerer Rechnungspositionen.

    Manche Rechnungen enthalten mehrere Kostenarten - etwa der
    Grundbesitzabgabenbescheid der Stadt (Grundsteuer, Müll, Straßenreinigung).
    Jede Kostenart wird als eigene Position (:class:`~models.cost.Cost`) mit
    eigenem Umlageschlüssel geführt; die Sammelrechnung verbindet sie und
    trägt, was für alle gilt.

    Attributes:
        property_id: Immobilie, für die die Kosten angefallen sind.
        description: Rechnungssteller bzw. Bezeichnung, z. B. "Stadt Bochum,
            Grundbesitzabgaben". Sie wird in jede Position übernommen.
        invoice_date: Rechnungsdatum laut Beleg (optional).
        total_amount: Gesamtbetrag laut Rechnung. Ist er angegeben (> 0), muss
            die Summe der Positionen ihm exakt entsprechen.
    """

    property_id: int
    description: str = ""
    invoice_date: date | None = None
    total_amount: Money = Money.zero()
    id: int | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.description = optional_text(self.description, "Beschreibung", max_length=300)
