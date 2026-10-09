"""Entity: Wohnung innerhalb einer Immobilie."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

from utils.validators import require_non_negative, require_positive, require_text


@dataclass
class Apartment:
    """Eine Mieteinheit.

    Wohnfläche und Personenzahl sind die Bezugsgrößen für die Umlageschlüssel
    "Wohnfläche" und "Personenzahl".
    """

    property_id: int
    number: str
    area_sqm: Decimal
    person_count: int = 1
    created_at: datetime = field(default_factory=datetime.now)
    id: int | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.number = require_text(self.number, "Wohnungsnummer", max_length=40)
        require_positive(self.area_sqm, "Wohnfläche")
        require_non_negative(self.person_count, "Personenanzahl")
