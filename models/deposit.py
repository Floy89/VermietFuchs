"""Mietkaution: Verzinsung und Rückzahlungsbetrag bei Auszug.

Nach § 551 Abs. 3 BGB legt der Vermieter die Kaution getrennt von seinem
Vermögen verzinslich an (z. B. auf einem Mietkautionskonto). Die Zinsen stehen
dem Mieter zu und **erhöhen die Sicherheit** - sie werden also der Kaution
zugeschlagen und im Folgejahr mitverzinst.

Rechnung wie bei einem Sparkonto:

* Zinsen fallen taggenau an, vom Einzahlungstag bis zum Tag vor der
  Rückzahlung, je Tag mit ``Zinssatz / 365`` (im Schaltjahr ``/ 366``).
* Gutgeschrieben (und damit mitverzinst) wird am Jahresende sowie am Stichtag.
* Ändert sich der Zinssatz, gilt der neue ab seinem Datum.

Abzüge (offene Forderungen des Vermieters, von der Bank einbehaltene
Kapitalertragsteuer) rechnet das Modell nicht ein - es liefert den Betrag, der
dem Mieter ohne Abzüge zusteht.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from models.exceptions import ValidationError
from models.value_objects import DateRange, Money


@dataclass(frozen=True)
class InterestRate:
    """Zinssatz des Kautionskontos ab einem Datum, in Prozent pro Jahr."""

    valid_from: date
    percent: Decimal


@dataclass(frozen=True)
class DepositYear:
    """Ein Abschnitt der Kautionsentwicklung (Kalenderjahr bzw. Rest bis zum Stichtag).

    Attributes:
        period: Zinstage dieses Abschnitts.
        rates: Die angewandten Zinssätze als Text, z. B. ``"0,5 %"`` oder ``"0,5 % / ab 01.07.: 1 %"``.
        start_balance: Stand zu Beginn (Kaution plus bisherige Zinsen).
        interest: Zinsgutschrift am Ende des Abschnitts.
    """

    period: DateRange
    rates: str
    start_balance: Money
    interest: Money

    @property
    def end_balance(self) -> Money:
        """Stand nach der Zinsgutschrift."""
        return self.start_balance + self.interest


@dataclass(frozen=True)
class DepositStatement:
    """Kautionsabrechnung bis zu einem Stichtag."""

    amount: Money
    paid_on: date
    until: date
    years: list[DepositYear]

    @property
    def interest(self) -> Money:
        """Summe aller Zinsen."""
        return sum((year.interest for year in self.years), Money.zero())

    @property
    def payout(self) -> Money:
        """Rückzahlungsbetrag: Kaution plus Zinsen (vor möglichen Abzügen)."""
        return self.amount + self.interest


def rate_on(rates: Sequence[InterestRate], day: date) -> Decimal:
    """Zinssatz an einem Tag; vor dem ersten Eintrag 0 %."""
    current = Decimal(0)
    for rate in sorted(rates, key=lambda r: r.valid_from):
        if rate.valid_from > day:
            break
        current = rate.percent
    return current


def validate_rates(rates: Sequence[InterestRate]) -> None:
    """Zinssätze zwischen 0 und 20 %, je Datum höchstens einer."""
    for rate in rates:
        if not Decimal(0) <= rate.percent <= Decimal(20):
            raise ValidationError("Der Zinssatz der Kaution muss zwischen 0 und 20 % liegen.")
    days = [rate.valid_from for rate in rates]
    if len(set(days)) != len(days):
        raise ValidationError("Für jedes Datum darf es nur einen Zinssatz geben.")


def deposit_statement(amount: Money, paid_on: date, rates: Sequence[InterestRate], until: date) -> DepositStatement:
    """Entwicklung der Kaution vom Einzahlungstag bis zum Stichtag (Rückzahlung).

    Args:
        amount: Eingezahlte Kaution.
        paid_on: Tag der Einzahlung (erster Zinstag).
        rates: Zinssätze mit "gültig ab".
        until: Stichtag der Rückzahlung; der Zinslauf endet am Vortag.
    """
    years: list[DepositYear] = []
    balance = amount
    start = paid_on
    while start < until:
        year_end = date(start.year, 12, 31)
        last_day = min(year_end, until - timedelta(days=1))
        period = DateRange(start, last_day)
        exact, labels = _interest(balance, period, rates)
        interest = Money(int((exact * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP)))
        years.append(DepositYear(period, labels, balance, interest))
        balance += interest
        start = last_day + timedelta(days=1)
    return DepositStatement(amount, paid_on, until, years)


def _interest(balance: Money, period: DateRange, rates: Sequence[InterestRate]) -> tuple[Decimal, str]:
    """Zinsen eines Abschnitts innerhalb eines Kalenderjahres (ungerundet) und die Zinssätze als Text."""
    days_in_year = Decimal(DateRange.for_year(period.start.year).days)
    changes = sorted(
        (rate for rate in rates if period.start < rate.valid_from <= period.end), key=lambda r: r.valid_from
    )
    total = Decimal(0)
    start = period.start
    percent = rate_on(rates, start)
    labels = [_percent(percent)]
    for change in changes:
        total += balance.amount * percent / 100 * Decimal((change.valid_from - start).days) / days_in_year
        start, percent = change.valid_from, change.percent
        labels.append(f"ab {change.valid_from:%d.%m.}: {_percent(percent)}")
    total += balance.amount * percent / 100 * Decimal((period.end - start).days + 1) / days_in_year
    return total, " / ".join(labels)


def _percent(value: Decimal) -> str:
    text = f"{value.normalize():f}".replace(".", ",")
    return f"{text} %"
