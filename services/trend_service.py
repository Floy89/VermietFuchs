"""Entwicklung der Nebenkosten und Vorauszahlungen über mehrere Jahre, mit Prognose."""

from __future__ import annotations

import logging
from decimal import ROUND_HALF_UP, Decimal

from models.billing import TenantStatement
from models.trend import CostTrend, ForecastPoint, YearPoint, linear_trend, project
from models.value_objects import DateRange, Money
from repositories.interfaces import CostRepository
from services.billing_service import BillingService
from services.invoice_checklist_service import InvoiceChecklistService

logger = logging.getLogger(__name__)

#: Mehr Jahre auf einmal werden nicht ausgewertet (jedes Jahr ist eine vollständige Abrechnung).
MAX_YEARS = 30


class TrendService:
    """Rechnet je Jahr die Abrechnung und stellt Kosten und Vorauszahlungen gegenüber.

    Der Service erfindet keine eigenen Zahlen: Für jedes Jahr wird die
    gewöhnliche Abrechnung berechnet (:class:`BillingService`) - der Verlauf
    stimmt deshalb immer mit dem Tab "Abrechnung" überein.
    """

    def __init__(
        self,
        billing: BillingService,
        checklist: InvoiceChecklistService,
        costs: CostRepository,
    ) -> None:
        self._billing = billing
        self._checklist = checklist
        self._costs = costs

    def years_with_invoices(self, property_id: int) -> list[int]:
        """Alle Kalenderjahre, die von Rechnungen der Immobilie berührt werden (aufsteigend)."""
        years: set[int] = set()
        for cost in self._costs.list_by_property(property_id):
            if not cost.excluded:
                years.update(range(cost.period.start.year, cost.period.end.year + 1))
        return sorted(years)

    def build(
        self,
        property_id: int,
        first_year: int,
        last_year: int,
        apartment_id: int | None = None,
        forecast_years: int = 5,
    ) -> CostTrend:
        """Erstellt Verlauf und Prognose.

        Args:
            property_id: Die Immobilie.
            first_year / last_year: Der betrachtete Zeitraum (einschließlich).
            apartment_id: Nur diese Wohnung; ``None`` = alle Mieter der Immobilie zusammen.
            forecast_years: Wie viele Jahre über ``last_year`` hinaus prognostiziert wird.

        Raises:
            NotFoundError: Wenn die Immobilie nicht existiert.
        """
        if last_year < first_year:
            first_year, last_year = last_year, first_year
        last_year = min(last_year, first_year + MAX_YEARS - 1)

        trend = CostTrend()
        latest_statements: list[TenantStatement] = []
        for year in range(first_year, last_year + 1):
            period = DateRange.for_year(year)
            result = self._billing.create_billing(property_id, period)
            statements = [
                statement
                for statement in result.statements
                if apartment_id is None or statement.apartment.id == apartment_id
            ]
            latest_statements = statements or latest_statements
            trend.points.append(
                YearPoint(
                    year=year,
                    costs=sum((s.total_costs for s in statements), Money.zero()),
                    prepayments=sum((s.prepayments for s in statements), Money.zero()),
                    complete=self._checklist.build(property_id, period).is_complete,
                )
            )

        # Heutige Vorauszahlung: die Raten der Mieter, deren Mietverhältnis noch läuft.
        active = {s.tenant.id: s.current_rate for s in latest_statements if s.current_rate is not None}
        if active:
            trend.current_monthly_prepayment = sum(active.values(), Money.zero())

        line = linear_trend([(point.year, point.costs) for point in trend.trend_points])
        if line is not None and forecast_years > 0:
            trend.yearly_change = Money(int(line[0].quantize(Decimal(1), rounding=ROUND_HALF_UP)))
            trend.forecast = [
                ForecastPoint(year, project(line, year)) for year in range(last_year + 1, last_year + 1 + forecast_years)
            ]
        logger.info(
            "Kostenverlauf berechnet: Immobilie %s, %d–%d, %d Prognosejahr(e)",
            property_id, first_year, last_year, len(trend.forecast),
        )
        return trend
