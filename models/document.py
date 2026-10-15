"""Belege ohne KI auslesen: Vorschlag aus dem Text und das, was die Anwendung je Absender gelernt hat."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from models.cost import InvoiceSplit
from models.enums import AllocationKey
from models.value_objects import DateRange, Money

#: Felder, deren Fundstelle im Beleg gelernt werden kann.
LEARNABLE_FIELDS: tuple[str, ...] = ("amount", "invoice_date", "period_start", "period_end", "consumption")


@dataclass(frozen=True)
class FieldAnchor:
    """Wo im Text eines Absenders ein Wert steht.

    Gelernt wird nicht der Wert selbst (der ist jedes Jahr anders), sondern
    die Zeile, in der er steht - mit allen Zahlen und Daten durch Platzhalter
    ersetzt. Aus "Rechnungsbetrag 787,08 Euro" wird ``"rechnungsbetrag # euro"``.
    Steht im nächsten Beleg desselben Absenders wieder eine solche Zeile, ist
    die Zahl an derselben Stelle (``index``) der gesuchte Wert.

    Attributes:
        pattern: Die Zeile mit Platzhaltern (``#`` für Zahlen, ``D`` für Daten).
        index: Die wievielte Zahl bzw. das wievielte Datum der Zeile (ab 0).
    """

    pattern: str
    index: int = 0


@dataclass
class DocumentProfile:
    """Was die Anwendung über die Belege eines Absenders gelernt hat.

    Entsteht beim ersten Speichern einer Rechnung, deren Werte aus einem Beleg
    gelesen wurden, und wird bei jedem weiteren Beleg desselben Absenders
    aktualisiert - so fließen Korrekturen des Benutzers in den nächsten
    Vorschlag ein.

    Attributes:
        issuer_key: Erkennungsmerkmal des Absenders, z. B. "ust:DE124978719"
            (Umsatzsteuer-ID) oder "web:gelsenwasser.de".
        issuer_name: Name des Absenders, wie er im Beleg steht (zur Anzeige).
        cost_type_id: Zuletzt gewählte Kostenart.
        allocation_key: Zuletzt gewählter Umlageschlüssel.
        description: Zuletzt gewählte Beschreibung der Rechnung.
        consumption_unit: Einheit des Verbrauchs ("m³", "kWh").
        vat_percent: Zuletzt verwendeter Umsatzsteuersatz.
        anchors: Gelernte Fundstellen je Feld (siehe :data:`LEARNABLE_FIELDS`).
        times_used: Wie oft das Profil schon beim Speichern bestätigt wurde.
    """

    issuer_key: str
    issuer_name: str = ""
    cost_type_id: int | None = None
    allocation_key: AllocationKey | None = None
    description: str = ""
    consumption_unit: str = ""
    vat_percent: Decimal | None = None
    anchors: dict[str, FieldAnchor] = field(default_factory=dict)
    times_used: int = 0
    updated_at: datetime = field(default_factory=datetime.now)
    id: int | None = None


@dataclass
class DocumentDraft:
    """Was aus dem Text eines Belegs gelesen wurde - ein Vorschlag für die Rechnung.

    Alle Werte sind Vorschläge; nicht Gefundenes bleibt ``None`` bzw. leer.

    Attributes:
        text: Der Text des Belegs (aus dem PDF oder der Texterkennung).
        source: "pdf" (Text stand im PDF) oder "ocr" (Windows-Texterkennung).
        issuer_key / issuer_name: Erkannter Absender (leer, wenn unbekannt).
        profile: Das gelernte Profil des Absenders, falls schon vorhanden.
        learned_fields: Felder, deren Wert über eine gelernte Fundstelle kam.
        splits: Abschnitte aus Zwischenablesungen, mit Preisen, falls erkannt.
    """

    text: str
    source: str = "pdf"
    issuer_key: str = ""
    issuer_name: str = ""
    profile: DocumentProfile | None = None
    invoice_date: date | None = None
    period: DateRange | None = None
    amount: Money | None = None
    consumption: Decimal | None = None
    consumption_unit: str = ""
    cost_type_name: str = ""
    cost_type_id: int | None = None
    allocation_key: AllocationKey | None = None
    description: str = ""
    working_price: Decimal | None = None
    base_price_per_year: Decimal | None = None
    vat_percent: Decimal | None = None
    splits: list[InvoiceSplit] = field(default_factory=list)
    learned_fields: list[str] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        """``True``, wenn nichts Verwertbares gefunden wurde."""
        return not (self.amount or self.period or self.invoice_date or self.consumption or self.cost_type_name)
