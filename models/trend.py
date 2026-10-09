"""Entwicklung der Nebenkosten über mehrere Jahre samt Prognose.

Die Zahlen werden nie gespeichert, sondern aus den Abrechnungen der
einzelnen Jahre berechnet. Verglichen werden je Jahr

* die **Kosten**, die auf die Mieter umgelegt wurden, und
* die **Vorauszahlungen**, die sie dafür geleistet haben.

Die Prognose legt eine Gerade durch die Kosten der vollständigen Jahre
(lineare Regression) und verlängert sie in die Zukunft. Dem steht die *heute*
vereinbarte Vorauszahlung gegenüber. Wo die Gerade sie schneidet, reichen die
Vorauszahlungen voraussichtlich nicht mehr - dort wird eine Anhebung fällig.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from models.billing import PREPAYMENT_STEP
from models.value_objects import Money


@dataclass(frozen=True)
class YearPoint:
    """Die Werte eines Abrechnungsjahres.

    Attributes:
        year: Das Kalenderjahr.
        costs: Auf die Mieter umgelegte Kosten.
        prepayments: Vorauszahlungen der Mieter laut vereinbarten Raten.
        complete: ``True``, wenn laut Rechnungs-Checkliste alle Rechnungen des
            Jahres vorliegen. Unvollständige Jahre zeigen zu niedrige Kosten
            und fließen deshalb nicht in die Prognose ein.
    """

    year: int
    costs: Money
    prepayments: Money
    complete: bool = True

    @property
    def balance(self) -> Money:
        """Vorauszahlungen minus Kosten (negativ = die Mieter mussten nachzahlen)."""
        return self.prepayments - self.costs

    @property
    def monthly_costs(self) -> Money:
        """Die Kosten je Monat (ein Zwölftel)."""
        return _divide(self.costs, 12)


@dataclass(frozen=True)
class ForecastPoint:
    """Der prognostizierte Wert eines künftigen Jahres."""

    year: int
    costs: Money

    @property
    def monthly_costs(self) -> Money:
        """Die prognostizierten Kosten je Monat."""
        return _divide(self.costs, 12)


@dataclass
class CostTrend:
    """Verlauf und Prognose für eine Immobilie oder eine einzelne Wohnung.

    Attributes:
        points: Die Jahre des gewählten Zeitraums, aufsteigend.
        forecast: Die prognostizierten Folgejahre (leer, wenn es für eine
            Prognose zu wenige vollständige Jahre gibt).
        yearly_change: Steigung der Trendgeraden: um so viel ändern sich die
            Kosten im Mittel je Jahr (``None`` ohne Prognose).
        current_monthly_prepayment: Summe der heute gültigen Monatsraten der
            aktiven Mieter; ``None``, wenn es keinen aktiven Mieter gibt.
    """

    points: list[YearPoint] = field(default_factory=list)
    forecast: list[ForecastPoint] = field(default_factory=list)
    yearly_change: Money | None = None
    current_monthly_prepayment: Money | None = None

    @property
    def trend_points(self) -> list[YearPoint]:
        """Die Jahre, auf denen die Prognose beruht (vollständig und mit Kosten)."""
        return [point for point in self.points if point.complete and not point.costs.is_zero]

    @property
    def current_yearly_prepayment(self) -> Money | None:
        """Die heutige Vorauszahlung auf ein Jahr gerechnet."""
        if self.current_monthly_prepayment is None:
            return None
        return Money(self.current_monthly_prepayment.cents * 12)

    @property
    def already_exceeded(self) -> bool:
        """``True``, wenn schon die Kosten des letzten vollständigen Jahres über der heutigen Vorauszahlung liegen."""
        prepayment = self.current_yearly_prepayment
        return bool(prepayment is not None and self.trend_points and self.trend_points[-1].costs > prepayment)

    @property
    def crossing(self) -> ForecastPoint | None:
        """Das erste Prognosejahr, in dem die Kosten die heutige Vorauszahlung übersteigen.

        ``None``, wenn es keine Prognose oder keine aktive Vorauszahlung gibt,
        die Kosten im Prognosezeitraum darunter bleiben - oder sie schon jetzt
        darüber liegen (siehe :attr:`already_exceeded`).
        """
        prepayment = self.current_yearly_prepayment
        if prepayment is None or self.already_exceeded:
            return None
        return next((point for point in self.forecast if point.costs > prepayment), None)

    def recommended_prepayment(self, point: ForecastPoint | YearPoint) -> Money:
        """Kostendeckende Monatsrate für ein Jahr, auf volle 10 € gerundet."""
        steps = (Decimal(point.monthly_costs.cents) / Decimal(PREPAYMENT_STEP.cents)).quantize(
            Decimal(1), rounding=ROUND_HALF_UP
        )
        return Money(max(0, int(steps)) * PREPAYMENT_STEP.cents)


def linear_trend(values: Sequence[tuple[int, Money]]) -> tuple[Decimal, Decimal] | None:
    """Legt eine Gerade durch die Wertepaare (Jahr, Betrag) - Methode der kleinsten Quadrate.

    Returns:
        ``(Steigung in Cent je Jahr, Achsenabschnitt in Cent)`` oder ``None``,
        wenn weniger als zwei verschiedene Jahre vorliegen (durch einen Punkt
        geht keine eindeutige Gerade).
    """
    if len({year for year, _amount in values}) < 2:
        return None
    count = Decimal(len(values))
    mean_x = sum((Decimal(year) for year, _amount in values), Decimal(0)) / count
    mean_y = sum((Decimal(amount.cents) for _year, amount in values), Decimal(0)) / count
    numerator = sum(
        ((Decimal(year) - mean_x) * (Decimal(amount.cents) - mean_y) for year, amount in values), Decimal(0)
    )
    denominator = sum(((Decimal(year) - mean_x) ** 2 for year, _amount in values), Decimal(0))
    slope = numerator / denominator
    return slope, mean_y - slope * mean_x


def project(trend: tuple[Decimal, Decimal], year: int) -> Money:
    """Wert der Trendgeraden in einem Jahr (nie unter 0 €)."""
    slope, intercept = trend
    cents = (slope * Decimal(year) + intercept).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return Money(max(0, int(cents)))


def _divide(money: Money, divisor: int) -> Money:
    return Money(int((Decimal(money.cents) / Decimal(divisor)).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
