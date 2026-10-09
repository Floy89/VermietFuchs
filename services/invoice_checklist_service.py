"""Rechnungs-Checkliste: Vollständigkeit der Rechnungen für einen Abrechnungszeitraum."""

from __future__ import annotations

import logging
from collections import defaultdict

from models.cost import Cost
from models.invoice_checklist import ChecklistItem, CostTypeRelevance, InvoiceChecklist
from models.value_objects import DateRange
from repositories.interfaces import CostRepository, CostTypeRelevanceRepository, CostTypeRepository

logger = logging.getLogger(__name__)


class InvoiceChecklistService:
    """Vergleicht die erfassten Rechnungen mit dem, was für eine Abrechnung gebraucht wird.

    Grundlage ist der Vorjahreszeitraum (derselbe Zeitraum, ein Jahr früher):

    * **Relevanz:** Eine Kostenart gilt als relevant, wenn für sie im
      Abrechnungs- oder im Vorjahreszeitraum eine Rechnung erfasst ist. Der
      Benutzer kann das je Kostenart von Hand überschreiben.
    * **Abdeckung:** Welcher Teil des Abrechnungszeitraums ist durch die
      Rechnungszeiträume der erfassten Rechnungen abgedeckt?
    * **Erwartung:** Für jeden nicht abgedeckten Teil wird die Vorjahresrechnung
      gesucht, die den entsprechenden Zeitraum ein Jahr zuvor abgedeckt hat.
      Ihr Rechnungssteller ist die Gedächtnisstütze, ihr Rechnungsdatum plus
      ein Jahr die Schätzung für das Eintreffen.
    """

    def __init__(
        self,
        costs: CostRepository,
        cost_types: CostTypeRepository,
        relevance: CostTypeRelevanceRepository,
    ) -> None:
        self._costs = costs
        self._cost_types = cost_types
        self._relevance = relevance

    def build(self, property_id: int, period: DateRange) -> InvoiceChecklist:
        """Erstellt die Checkliste.

        Einzeln ausgeschlossene Rechnungen zählen nicht. Rechnungen einer von
        der Abrechnung ausgeschlossenen *aggregierten* Rechnung (z. B. Gas für
        die Meldung an den Messdienst) zählen dagegen mit - auch sie müssen
        vorliegen, bevor abgerechnet werden kann.

        Returns:
            Je Kostenart des Katalogs eine Zeile, in Katalog-Reihenfolge.
        """
        previous_period = period.shift_years(-1)
        manual = {r.cost_type_id: r.relevant for r in self._relevance.list_by_property(property_id)}

        current_by_type: dict[int, list[Cost]] = defaultdict(list)
        previous_by_type: dict[int, list[Cost]] = defaultdict(list)
        for invoice in self._costs.list_by_property(property_id):
            if invoice.excluded or invoice.cost_type.id is None:
                continue
            if invoice.period.overlaps(period):
                current_by_type[invoice.cost_type.id].append(invoice)
            if invoice.period.overlaps(previous_period):
                previous_by_type[invoice.cost_type.id].append(invoice)

        checklist = InvoiceChecklist(property_id=property_id, period=period)
        for cost_type in self._cost_types.list_all():
            assert cost_type.id is not None  # aus der Datenbank geladen
            current = sorted(current_by_type.get(cost_type.id, []), key=lambda c: c.period.start)
            previous = sorted(previous_by_type.get(cost_type.id, []), key=lambda c: c.period.start)
            missing = period.uncovered_by([invoice.period for invoice in current])
            # Eine Vorjahresrechnung "steht aus", wenn ihr um ein Jahr
            # verschobener Zeitraum in eine der Lücken fällt.
            outstanding = [
                invoice
                for invoice in previous
                if any(invoice.period.shift_years(1).overlaps(gap) for gap in missing)
            ]
            checklist.items.append(
                ChecklistItem(
                    cost_type=cost_type,
                    relevant=manual.get(cost_type.id, bool(current or previous)),
                    relevance_is_manual=cost_type.id in manual,
                    period=period,
                    invoices=current,
                    missing=missing,
                    previous=previous,
                    outstanding=outstanding,
                )
            )
        return checklist

    def set_relevant(self, property_id: int, cost_type_id: int, relevant: bool) -> None:
        """Legt von Hand fest, ob eine Kostenart für die Immobilie relevant ist."""
        existing = next(
            (r for r in self._relevance.list_by_property(property_id) if r.cost_type_id == cost_type_id), None
        )
        if existing is None:
            self._relevance.add(CostTypeRelevance(property_id, cost_type_id, relevant))
        elif existing.relevant != relevant:
            existing.relevant = relevant
            self._relevance.update(existing)
        logger.info("Kostenart %s für Immobilie %s relevant = %s", cost_type_id, property_id, relevant)
