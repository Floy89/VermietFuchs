"""Entity: vereinbarte Nebenkosten-Vorauszahlung eines Mieters für einen Zeitraum.

Jeder Mieter hat eine *Historie* solcher Raten. Eine Erhöhung ist einfach
eine weitere Rate mit späterem Beginn - die frühere endet am Vortag. So
bleibt nachvollziehbar, wann welche Vorauszahlung vereinbart war, und der
Abzug in der Abrechnung lässt sich abschnittsweise berechnen.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from models.exceptions import ValidationError
from models.value_objects import DateRange, Money
from utils.validators import optional_text, require_non_negative


@dataclass
class PrepaymentRate:
    """Eine monatliche Vorauszahlung, gültig von ``valid_from`` bis ``valid_to``.

    Attributes:
        tenant_id: Der Mieter, mit dem die Rate vereinbart ist.
        amount: Monatliche Vorauszahlung.
        valid_from: Erster Tag, für den die Rate gilt.
        valid_to: Letzter Tag; ``None`` bedeutet "bis auf Weiteres".
        note: Freitext, z. B. "Anpassung nach Abrechnung 2024".
    """

    tenant_id: int
    amount: Money
    valid_from: date
    valid_to: date | None = None
    note: str = ""
    id: int | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        require_non_negative(self.amount.cents, "Vorauszahlung")
        self.note = optional_text(self.note, "Bemerkung", max_length=200)
        if self.valid_to is not None and self.valid_to < self.valid_from:
            raise ValidationError("'Gültig bis' darf nicht vor 'Gültig ab' liegen.")

    @property
    def period(self) -> DateRange:
        """Gültigkeitszeitraum; offene Raten enden rechnerisch am ``date.max``."""
        return DateRange(self.valid_from, self.valid_to or date.max)


def rate_on(rates: Sequence[PrepaymentRate], day: date) -> Money:
    """Die am Stichtag gültige Monatsrate (0,00 €, wenn keine gilt)."""
    for rate in rates:
        if rate.period.contains(day):
            return rate.amount
    return Money.zero()


def expected_prepayment(rates: Sequence[PrepaymentRate], period: DateRange) -> Money:
    """Vorauszahlungen für ``period`` laut den vereinbarten Monatsraten.

    Gerechnet wird Monat für Monat:

    * Gilt eine Rate den *ganzen* Kalendermonat, zählt sie voll - so, wie der
      Mieter sie auch überweist. Ein volles Jahr ergibt exakt 12 Raten, neun
      volle Monate exakt 9.
    * Gilt sie nur einen *Teil* des Monats (Einzug am 14., Auszug am 20.),
      zählt sie anteilig nach Tagen dieses Monats: Rate × Tage / Monatstage.
      Dadurch spielt es keine Rolle, dass Monate unterschiedlich lang sind.

    Die Summe muss dem entsprechen, was der Mieter tatsächlich gezahlt hat -
    einzelne Zahlungen erfasst die Anwendung nicht. Hat ein Mieter z. B. erst
    ab dem Folgemonat des Einzugs gezahlt, wird "Gültig ab" der Rate auf
    diesen Monatsersten gesetzt.

    Args:
        rates: Die Raten *eines* Mieters.
        period: Der Zeitraum, für den die Summe gesucht ist - üblicherweise
            der abgerechnete Zeitraum des Mietverhältnisses.
    """
    total = Decimal(0)
    for rate in rates:
        active = rate.period.intersection(period)
        if active is None:
            continue
        year, month = active.start.year, active.start.month
        while (year, month) <= (active.end.year, active.end.month):
            days_in_month = calendar.monthrange(year, month)[1]
            whole_month = DateRange(date(year, month, 1), date(year, month, days_in_month))
            in_month = active.intersection(whole_month)
            if in_month is not None:
                total += Decimal(rate.amount.cents) * in_month.days / days_in_month
            year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return Money(int(total.quantize(Decimal(1), rounding=ROUND_HALF_UP)))
