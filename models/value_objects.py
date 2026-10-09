"""Value Objects: unveränderliche Werte ohne eigene Identität.

Zwei Value Objects tragen die gesamte Rechengenauigkeit der Anwendung:

* :class:`Money` rechnet intern in ganzen Cent. Damit gibt es keine
  Gleitkomma-Rundungsfehler, und beim Verteilen von Kosten geht kein Cent
  verloren.
* :class:`DateRange` beschreibt einen Zeitraum mit *inklusivem* Start- und
  Enddatum, so wie Abrechnungszeiträume im Mietrecht üblich sind
  (01.01.–31.12. sind 365 Tage).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Sequence

from models.exceptions import ValidationError

_CENT = Decimal("0.01")


@dataclass(frozen=True, order=True)
class Money:
    """Geldbetrag in Euro, intern als ganze Cent gespeichert."""

    cents: int = 0

    # ------------------------------------------------------------------ #
    # Konstruktion
    # ------------------------------------------------------------------ #
    @classmethod
    def zero(cls) -> Money:
        """Der Betrag 0,00 €."""
        return cls(0)

    @classmethod
    def from_decimal(cls, amount: Decimal | int | str) -> Money:
        """Erzeugt einen Betrag aus einem Euro-Wert (kaufmännisch gerundet).

        Raises:
            ValidationError: Wenn ``amount`` keine gültige Zahl ist.
        """
        try:
            value = Decimal(str(amount))
        except InvalidOperation as exc:
            raise ValidationError(f"'{amount}' ist kein gültiger Geldbetrag.") from exc
        if not value.is_finite():
            raise ValidationError(f"'{amount}' ist kein gültiger Geldbetrag.")
        return cls(int((value / _CENT).quantize(Decimal(1), rounding=ROUND_HALF_UP)))

    # ------------------------------------------------------------------ #
    # Abfragen
    # ------------------------------------------------------------------ #
    @property
    def amount(self) -> Decimal:
        """Der Betrag als ``Decimal`` in Euro mit zwei Nachkommastellen."""
        return (Decimal(self.cents) * _CENT).quantize(_CENT)

    @property
    def is_zero(self) -> bool:
        """``True`` bei genau 0,00 €."""
        return self.cents == 0

    @property
    def is_negative(self) -> bool:
        """``True`` bei Beträgen unter 0,00 €."""
        return self.cents < 0

    # ------------------------------------------------------------------ #
    # Rechnen
    # ------------------------------------------------------------------ #
    def __add__(self, other: Money) -> Money:
        return Money(self.cents + other.cents)

    def __sub__(self, other: Money) -> Money:
        return Money(self.cents - other.cents)

    def __neg__(self) -> Money:
        return Money(-self.cents)

    def __abs__(self) -> Money:
        return Money(abs(self.cents))

    def percentage(self, percent: int | Decimal) -> Money:
        """Gibt ``percent`` Prozent dieses Betrags zurück (kaufmännisch gerundet)."""
        raw = Decimal(self.cents) * Decimal(percent) / Decimal(100)
        return Money(int(raw.quantize(Decimal(1), rounding=ROUND_HALF_UP)))

    def allocate(self, weights: Sequence[Decimal | int]) -> list[Money]:
        """Verteilt den Betrag centgenau nach Gewichten.

        Verwendet das Verfahren der größten Reste (Hare/Niemeyer): Jeder
        Anteil wird zunächst abgerundet, die übrig gebliebenen Cent gehen an
        die Anteile mit dem größten abgeschnittenen Rest. Dadurch ist die
        Summe der Teile *immer* exakt gleich dem Ausgangsbetrag.

        Args:
            weights: Nicht-negative Gewichte (z. B. Quadratmeter oder Tage).

        Returns:
            Eine Liste von Teilbeträgen in der Reihenfolge der Gewichte.

        Raises:
            ValueError: Bei negativen Gewichten oder Gewichtssumme 0.
        """
        decimal_weights = [Decimal(weight) for weight in weights]
        if any(weight < 0 for weight in decimal_weights):
            raise ValueError("Gewichte dürfen nicht negativ sein.")
        total = sum(decimal_weights, Decimal(0))
        if total <= 0:
            raise ValueError("Die Summe der Gewichte muss größer als 0 sein.")

        # Mit dem Absolutbetrag rechnen und das Vorzeichen am Ende wieder
        # anbringen - so funktioniert das Verfahren auch für Gutschriften.
        sign = -1 if self.cents < 0 else 1
        remaining_total = abs(self.cents)

        exact_shares = [Decimal(remaining_total) * weight / total for weight in decimal_weights]
        floored = [int(share) for share in exact_shares]  # int() schneidet ab
        leftover = remaining_total - sum(floored)

        # Indizes nach größtem Rest sortieren; bei Gleichstand gewinnt der
        # frühere Eintrag, damit das Ergebnis reproduzierbar bleibt.
        by_remainder = sorted(
            range(len(exact_shares)),
            key=lambda index: (exact_shares[index] - floored[index], -index),
            reverse=True,
        )
        for index in by_remainder[:leftover]:
            floored[index] += 1

        return [Money(sign * cents) for cents in floored]

    def __str__(self) -> str:
        return f"{self.amount} EUR"


@dataclass(frozen=True)
class DateRange:
    """Zeitraum mit inklusivem Start- und Enddatum."""

    start: date
    end: date

    def __post_init__(self) -> None:
        if self.end < self.start:
            raise ValidationError("Das Enddatum darf nicht vor dem Startdatum liegen.")

    @classmethod
    def for_year(cls, year: int) -> DateRange:
        """Das komplette Kalenderjahr ``year`` (01.01.–31.12.)."""
        return cls(date(year, 1, 1), date(year, 12, 31))

    @property
    def days(self) -> int:
        """Anzahl der Tage inklusive Start- und Endtag."""
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        """``True``, wenn ``day`` im Zeitraum liegt."""
        return self.start <= day <= self.end

    def encloses(self, other: DateRange) -> bool:
        """``True``, wenn ``other`` vollständig in diesem Zeitraum liegt."""
        return self.start <= other.start and other.end <= self.end

    def intersection(self, other: DateRange) -> DateRange | None:
        """Gemeinsamer Zeitraum mit ``other`` oder ``None``, falls keiner existiert."""
        start = max(self.start, other.start)
        end = min(self.end, other.end)
        if end < start:
            return None
        return DateRange(start, end)

    def overlaps(self, other: DateRange) -> bool:
        """``True``, wenn sich beide Zeiträume mindestens einen Tag teilen."""
        return self.intersection(other) is not None

    def uncovered_by(self, ranges: Sequence[DateRange]) -> list[DateRange]:
        """Die Teile dieses Zeitraums, die von keinem der ``ranges`` abgedeckt sind.

        Die ``ranges`` dürfen sich überschneiden und über diesen Zeitraum
        hinausragen. Beispiel: Das Jahr 2025, abgedeckt durch 01.06.2024–31.05.2025,
        ergibt die Lücke 01.06.2025–31.12.2025.
        """
        one_day = timedelta(days=1)
        gaps: list[DateRange] = []
        cursor = self.start
        for covered in sorted(ranges, key=lambda r: r.start):
            if covered.end < cursor or covered.start > self.end:
                continue
            if covered.start > cursor:
                gaps.append(DateRange(cursor, covered.start - one_day))
            cursor = max(cursor, covered.end + one_day)
            if cursor > self.end:
                return gaps
        if cursor <= self.end:
            gaps.append(DateRange(cursor, self.end))
        return gaps

    def shift_years(self, years: int) -> DateRange:
        """Derselbe Zeitraum, um ganze Jahre verschoben (29.02. wird zum 28.02.)."""
        return DateRange(_shift_year(self.start, years), _shift_year(self.end, years))


def _shift_year(day: date, years: int) -> date:
    """Verschiebt ein Datum um ganze Jahre; den 29.02. gibt es nur in Schaltjahren."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)
