"""Rechnungs-Checkliste: Welche Rechnungen liegen für einen Abrechnungszeitraum vor, welche fehlen?

Die Checkliste wird nie gespeichert, sondern aus den erfassten Rechnungen
berechnet. Gespeichert wird nur, wenn der Benutzer eine Kostenart von Hand
als (nicht) relevant markiert (:class:`CostTypeRelevance`).

Als Gedächtnisstütze dient der Vorjahreszeitraum: Welche Rechnung kam damals
von wem und wann? Daraus ergibt sich, worauf noch gewartet wird und ab wann
die Abrechnung voraussichtlich möglich ist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import Enum

from models.cost import Cost
from models.cost_type import CostType
from models.value_objects import DateRange, Money


class ChecklistStatus(str, Enum):
    """Stand einer Kostenart in der Checkliste."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    MISSING = "MISSING"
    NOT_RELEVANT = "NOT_RELEVANT"

    @property
    def label(self) -> str:
        """Deutsche Bezeichnung."""
        return {
            ChecklistStatus.COMPLETE: "vollständig",
            ChecklistStatus.PARTIAL: "teilweise",
            ChecklistStatus.MISSING: "fehlt",
            ChecklistStatus.NOT_RELEVANT: "nicht relevant",
        }[self]


@dataclass
class CostTypeRelevance:
    """Vom Benutzer festgelegt: Ist eine Kostenart für eine Immobilie relevant?

    Ohne Eintrag entscheidet die Anwendung selbst (relevant ist, wofür im
    Abrechnungs- oder im Vorjahreszeitraum eine Rechnung erfasst wurde).
    """

    property_id: int
    cost_type_id: int
    relevant: bool
    id: int | None = None


@dataclass
class ChecklistItem:
    """Eine Zeile der Checkliste: der Stand einer Kostenart.

    Attributes:
        cost_type: Die Kostenart.
        relevant: Ob die Kostenart für diese Immobilie abgerechnet wird.
        relevance_is_manual: ``True``, wenn der Benutzer das von Hand festgelegt hat.
        period: Der Abrechnungszeitraum.
        invoices: Erfasste Rechnungen, die den Zeitraum berühren.
        missing: Teile des Zeitraums, die von keiner Rechnung abgedeckt sind.
        previous: Rechnungen des Vorjahreszeitraums (Gedächtnisstütze).
        outstanding: Diejenigen Vorjahresrechnungen, deren Nachfolger noch fehlt.
    """

    cost_type: CostType
    relevant: bool
    relevance_is_manual: bool
    period: DateRange
    invoices: list[Cost] = field(default_factory=list)
    missing: list[DateRange] = field(default_factory=list)
    previous: list[Cost] = field(default_factory=list)
    outstanding: list[Cost] = field(default_factory=list)

    @property
    def covered_days(self) -> int:
        """Tage des Zeitraums, für die eine Rechnung vorliegt."""
        return self.period.days - sum(gap.days for gap in self.missing)

    @property
    def coverage_percent(self) -> Decimal:
        """Abdeckung des Zeitraums in Prozent (0–100)."""
        return (Decimal(self.covered_days) * 100 / Decimal(self.period.days)).quantize(Decimal("0.1"))

    @property
    def status(self) -> ChecklistStatus:
        """Stand der Kostenart."""
        if not self.relevant:
            return ChecklistStatus.NOT_RELEVANT
        if not self.invoices:
            return ChecklistStatus.MISSING
        return ChecklistStatus.PARTIAL if self.missing else ChecklistStatus.COMPLETE

    @property
    def amount_in_period(self) -> Money:
        """Summe der erfassten Rechnungsanteile im Zeitraum."""
        total = Money.zero()
        for invoice in self.invoices:
            part = invoice.prorate(self.period)
            if part is not None:
                total += part.amount
        return total

    @property
    def expected_date(self) -> date | None:
        """Wann die letzte noch fehlende Rechnung voraussichtlich kommt.

        Schätzung: Rechnungsdatum der entsprechenden Vorjahresrechnung plus
        ein Jahr. ``None``, wenn nichts fehlt oder es keinen Vorjahreswert
        mit Rechnungsdatum gibt.
        """
        dates = [expected_arrival(invoice) for invoice in self.outstanding]
        known = [day for day in dates if day is not None]
        return max(known) if known else None

    @property
    def expectation_unknown(self) -> bool:
        """``True``, wenn eine Rechnung fehlt, sich ihr Eintreffen aber nicht schätzen lässt."""
        return self.status in (ChecklistStatus.PARTIAL, ChecklistStatus.MISSING) and self.expected_date is None


@dataclass
class InvoiceChecklist:
    """Die Checkliste einer Immobilie für einen Abrechnungszeitraum."""

    property_id: int
    period: DateRange
    items: list[ChecklistItem] = field(default_factory=list)

    @property
    def relevant_items(self) -> list[ChecklistItem]:
        """Nur die Kostenarten, die abgerechnet werden."""
        return [item for item in self.items if item.relevant]

    @property
    def open_items(self) -> list[ChecklistItem]:
        """Relevante Kostenarten, für die noch eine Rechnung fehlt."""
        return [item for item in self.relevant_items if item.status is not ChecklistStatus.COMPLETE]

    @property
    def is_complete(self) -> bool:
        """``True``, wenn für alle relevanten Kostenarten der ganze Zeitraum abgedeckt ist."""
        return bool(self.relevant_items) and not self.open_items

    @property
    def ready_date(self) -> date | None:
        """Ab wann die Abrechnung voraussichtlich möglich ist.

        Das späteste geschätzte Eintreffen aller noch fehlenden Rechnungen;
        ``None``, wenn nichts fehlt oder sich nichts schätzen lässt.
        """
        dates = [item.expected_date for item in self.open_items if item.expected_date is not None]
        return max(dates) if dates else None

    @property
    def unknown_items(self) -> list[ChecklistItem]:
        """Offene Kostenarten ohne Schätzung (kein Vorjahreswert mit Rechnungsdatum)."""
        return [item for item in self.open_items if item.expectation_unknown]


def expected_arrival(previous_invoice: Cost) -> date | None:
    """Schätzt, wann die Nachfolgerin einer Vorjahresrechnung eintrifft: gleiches Datum, ein Jahr später."""
    if previous_invoice.invoice_date is None:
        return None
    return DateRange(previous_invoice.invoice_date, previous_invoice.invoice_date).shift_years(1).start
