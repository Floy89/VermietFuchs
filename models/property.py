"""Entity: Immobilie (Haus / Abrechnungseinheit)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from utils.validators import require_text


@dataclass
class Property:
    """Ein Haus, dessen Nebenkosten gemeinsam abgerechnet werden.

    Die Anzahl der Wohnungen wird nicht gespeichert, sondern ergibt sich aus
    den zugeordneten :class:`~models.apartment.Apartment`-Datensätzen. So kann
    sie nie vom tatsächlichen Bestand abweichen.
    """

    name: str
    address: str
    created_at: datetime = field(default_factory=datetime.now)
    id: int | None = None

    def validate(self) -> None:
        """Prüft die fachlichen Regeln; wirft ``ValidationError`` bei Verstößen."""
        self.name = require_text(self.name, "Name", max_length=120)
        self.address = require_text(self.address, "Adresse", max_length=300)
