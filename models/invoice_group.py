"""Aggregierte Rechnungen: alle Rechnungsanteile einer Kostenart in einem Kalenderjahr.

Rechnungen laufen oft über den Jahreswechsel (Gas 01.11.–31.10.). Für die
Abrechnung und für die Meldung an den Messdienst zählt aber das Kalenderjahr.
Eine :class:`InvoiceGroup` fasst deshalb für *eine Kostenart* und *ein Jahr*
die taggenauen Anteile aller betroffenen Rechnungen zusammen - mit Summe der
Kosten und des Verbrauchs.

Gruppen werden nie gespeichert, sondern aus den Rechnungen berechnet. In der
Datenbank steht nur, was der Benutzer dazu festlegt
(:class:`InvoiceGroupSetting`): ein eigener Name und ob die Gruppe in die
Abrechnung übernommen wird.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from models.cost import Cost
from models.cost_type import CostType
from models.value_objects import DateRange, Money


@dataclass
class InvoiceGroupSetting:
    """Benutzer-Einstellungen zu einer aggregierten Rechnung.

    Attributes:
        property_id / cost_type_id / year: Identifizieren die Gruppe.
        excluded: ``True`` = nicht in die Abrechnung übernehmen (z. B. Gas,
            das nur dem Messdienst gemeldet und über dessen Abrechnung umgelegt wird).
        name: Eigener Name; leer bedeutet "Kostenart + Jahr".
    """

    property_id: int
    cost_type_id: int
    year: int
    excluded: bool = False
    name: str = ""
    id: int | None = None


@dataclass(frozen=True)
class InvoicePart:
    """Der Teil einer Rechnung, der in ein Kalenderjahr fällt.

    Attributes:
        invoice: Die Originalrechnung.
        part: Zeitanteilige Kopie (Betrag, Verbrauch und Zeitraum im Jahr);
            identisch mit ``invoice``, wenn die Rechnung ganz im Jahr liegt.
    """

    invoice: Cost
    part: Cost


@dataclass
class InvoiceGroup:
    """Aggregierte Rechnung: eine Kostenart in einem Kalenderjahr."""

    property_id: int
    cost_type: CostType
    year: int
    custom_name: str = ""
    excluded: bool = False
    parts: list[InvoicePart] = field(default_factory=list)
    #: Zeitraum der Gruppe, falls er vom Kalenderjahr ``year`` abweicht
    #: (nicht-kalendarische Abrechnung); sonst ``None``.
    period: DateRange | None = None

    @property
    def name(self) -> str:
        """Anzeigename, z. B. "Brennstoffkosten (Gas/Öl) 2024"."""
        if self.custom_name:
            return self.custom_name
        if self.period is not None:
            span = f"{self.period.start.strftime('%d.%m.%y')}–{self.period.end.strftime('%d.%m.%y')}"
            return f"{self.cost_type.name} {span}"
        return f"{self.cost_type.name} {self.year}"

    @property
    def total_amount(self) -> Money:
        """Summe der Kostenanteile im Jahr."""
        return sum((part.part.amount for part in self.parts), Money.zero())

    @property
    def consumption_unit(self) -> str:
        """Gemeinsame Einheit der Verbräuche; leer, wenn keine oder gemischte Einheiten."""
        units = {part.part.consumption_unit for part in self.parts if part.part.consumption is not None}
        return units.pop() if len(units) == 1 else ""

    @property
    def total_consumption(self) -> Decimal | None:
        """Summe der Verbrauchsanteile im Jahr.

        ``None``, wenn keine Rechnung einen Verbrauch nennt oder die Einheiten
        nicht zusammenpassen (kWh und m³ lassen sich nicht addieren).
        """
        values = [part.part.consumption for part in self.parts if part.part.consumption is not None]
        if not values or not self.consumption_unit:
            return None
        return sum(values, Decimal(0))

    @property
    def total_co2_cost(self) -> Money | None:
        """Summe der CO2-Kosten (brutto) im Jahr; ``None``, wenn keine Rechnung welche nennt."""
        values = [part.part.co2_cost for part in self.parts if part.part.co2_cost is not None]
        return sum(values, Money.zero()) if values else None

    @property
    def total_co2_emission(self) -> Decimal | None:
        """Summe des CO2-Ausstoßes in kg im Jahr; ``None``, wenn keine Rechnung einen nennt."""
        values = [part.part.co2_emission for part in self.parts if part.part.co2_emission is not None]
        return sum(values, Decimal(0)) if values else None

    @property
    def co2_missing(self) -> int:
        """Wie viele Rechnungsanteile keine CO2-Angaben haben (nur bei Heizkosten von Belang)."""
        return sum(1 for part in self.parts if part.part.co2_cost is None and part.part.co2_emission is None)
