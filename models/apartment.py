"""Entity: Wohnung innerhalb einer Immobilie."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from models.exceptions import ValidationError
from models.value_objects import DateRange
from utils.validators import require_non_negative, require_positive, require_text


@dataclass(frozen=True)
class PersonChange:
    """Änderung der Personenzahl einer Wohnung ab einem Datum (z. B. Geburt, Einzug, Auszug)."""

    valid_from: date
    person_count: int


@dataclass
class Apartment:
    """Eine Mieteinheit.

    Wohnfläche und Personenzahl sind die Bezugsgrößen für die Umlageschlüssel
    "Wohnfläche" und "Personenzahl".

    Attributes:
        person_count: Personenzahl von Anfang an - bis zur ersten Änderung.
        person_changes: Spätere Änderungen der Personenzahl, je mit "gültig ab".
            So bleiben Abrechnungen früherer Jahre unverändert, wenn sich die
            Belegung ändert: Jeder Zeitraum rechnet mit der Belegung, die damals galt.
        persons_in_period: Nur in der Kostenverteilung: tagesgenauer Durchschnitt
            der Personenzahl im Zeitraum einer Kostenposition. Nicht gespeichert.
    """

    property_id: int
    number: str
    area_sqm: Decimal
    person_count: int = 1
    created_at: datetime = field(default_factory=datetime.now)
    id: int | None = None
    person_changes: list[PersonChange] = field(default_factory=list)
    persons_in_period: Decimal | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.number = require_text(self.number, "Wohnungsnummer", max_length=40)
        require_positive(self.area_sqm, "Wohnfläche")
        require_non_negative(self.person_count, "Personenanzahl")
        self.person_changes = sorted(self.person_changes, key=lambda change: change.valid_from)
        for change in self.person_changes:
            require_non_negative(change.person_count, "Personenanzahl")
        days = [change.valid_from for change in self.person_changes]
        if len(set(days)) != len(days):
            raise ValidationError("Für jedes Datum darf es nur eine Änderung der Personenzahl geben.")

    # ------------------------------------------------------------------ #
    # Belegung im Zeitverlauf
    # ------------------------------------------------------------------ #
    def persons_on(self, day: date) -> int:
        """Personenzahl an einem Tag."""
        count = self.person_count
        for change in sorted(self.person_changes, key=lambda c: c.valid_from):
            if change.valid_from > day:
                break
            count = change.person_count
        return count

    def changes_in(self, period: DateRange) -> list[PersonChange]:
        """Änderungen, die innerhalb des Zeitraums (nach seinem ersten Tag) wirksam werden."""
        ordered = sorted(self.person_changes, key=lambda c: c.valid_from)
        return [change for change in ordered if period.start < change.valid_from <= period.end]

    def person_days(self, period: DateRange) -> int:
        """Personentage im Zeitraum: Summe der Personenzahl über alle Tage."""
        total = 0
        start = period.start
        count = self.persons_on(start)
        for change in self.changes_in(period):
            total += count * (change.valid_from - start).days
            start, count = change.valid_from, change.person_count
        return total + count * ((period.end - start).days + 1)

    def average_persons(self, period: DateRange) -> Decimal:
        """Tagesgenauer Durchschnitt der Personenzahl im Zeitraum (Personentage / Tage)."""
        if not self.changes_in(period):
            return Decimal(self.persons_on(period.start))
        return Decimal(self.person_days(period)) / Decimal(period.days)

    def describe_persons(self, period: DateRange) -> str:
        """Personenzahl im Zeitraum als Text, z. B. ``"2, ab 01.07.2026: 3"``."""
        text = str(self.persons_on(period.start))
        for change in self.changes_in(period):
            text += f", ab {change.valid_from:%d.%m.%Y}: {change.person_count}"
        return text
