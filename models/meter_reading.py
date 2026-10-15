"""Entity: Zählerstand sowie die Verbrauchsermittlung aus Zählerständen."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Sequence

from models.enums import MeterType
from models.value_objects import DateRange
from utils.validators import optional_text, require_non_negative

#: Ablesungen finden selten exakt am Stichtag statt. Ein Zählerstand zählt
#: deshalb noch zum Periodenbeginn bzw. -ende, wenn er bis zu so viele Tage
#: danach abgelesen wurde (z. B. Ablesung am 03.01. für den 31.12.).
READING_TOLERANCE_DAYS = 14


@dataclass
class MeterReading:
    """Ein abgelesener Zählerstand einer Wohnung."""

    apartment_id: int
    meter_type: MeterType
    value: Decimal
    date: date
    reader: str = ""
    note: str = ""
    id: int | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        require_non_negative(self.value, "Zählerstand")
        self.reader = optional_text(self.reader, "Ableser", max_length=120)
        self.note = optional_text(self.note, "Bemerkung", max_length=500)


def calculate_consumption(readings: Sequence[MeterReading], period: DateRange) -> Decimal | None:
    """Ermittelt den Verbrauch eines Zählers innerhalb eines Zeitraums.

    Vorgehen:

    1. *Anfangsstand* ist die letzte Ablesung bis zum Periodenbeginn (plus
       Toleranz). Gibt es keine, wird die früheste Ablesung in der Periode
       genommen (z. B. bei Einbau des Zählers im laufenden Jahr).
    2. *Endstand* ist die letzte Ablesung bis zum Periodenende (plus Toleranz).
    3. Verbrauch = Endstand − Anfangsstand.

    Args:
        readings: Ablesungen *eines* Zählers (eine Wohnung, eine Zählerart).
        period: Der Abrechnungszeitraum.

    Returns:
        Den Verbrauch oder ``None``, wenn er sich nicht bestimmen lässt
        (weniger als zwei passende Ablesungen oder ein rückläufiger Zähler,
        etwa nach einem Zählerwechsel).
    """
    pair = reading_pair(readings, period)
    if pair is None:
        return None
    consumption = pair[1].value - pair[0].value
    return consumption if consumption >= 0 else None


def reading_pair(readings: Sequence[MeterReading], period: DateRange) -> tuple[MeterReading, MeterReading] | None:
    """Anfangs- und Endablesung, aus denen der Verbrauch im Zeitraum berechnet wird.

    Regeln wie bei :func:`calculate_consumption`. ``None``, wenn es keine zwei
    verschiedenen passenden Ablesungen gibt.
    """
    ordered = sorted(readings, key=lambda reading: reading.date)
    tolerance = timedelta(days=READING_TOLERANCE_DAYS)

    start_candidates = [r for r in ordered if r.date <= period.start + tolerance]
    if start_candidates:
        start_reading = start_candidates[-1]
    else:
        in_period = [r for r in ordered if period.contains(r.date)]
        if not in_period:
            return None
        start_reading = in_period[0]

    end_candidates = [r for r in ordered if r.date <= period.end + tolerance]
    if not end_candidates:
        return None
    end_reading = end_candidates[-1]

    # Dieselbe Ablesung als Anfang und Ende heißt: Es fehlt ein zweiter Wert.
    if end_reading is start_reading or end_reading.date <= start_reading.date:
        return None
    return start_reading, end_reading


def readings_enclose(pair: tuple[MeterReading, MeterReading], period: DateRange) -> bool:
    """``True``, wenn die beiden Ablesungen ``period`` umschließen (mit der üblichen Toleranz)."""
    tolerance = timedelta(days=READING_TOLERANCE_DAYS)
    return pair[0].date <= period.start + tolerance and pair[1].date >= period.end - tolerance
