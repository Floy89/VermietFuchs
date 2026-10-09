"""Value Object: Stammdaten des Vermieters für Abrechnungsschreiben."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LandlordSettings:
    """Absender- und Bankdaten, die auf den PDF-Abrechnungen erscheinen."""

    name: str = ""
    street: str = ""
    city: str = ""
    phone: str = ""
    email: str = ""
    bank_name: str = ""
    iban: str = ""
    payment_term_days: int = 30

    @property
    def address_line(self) -> str:
        """Einzeilige Absenderangabe, z. B. für die Zeile über dem Adressfeld."""
        parts = [part for part in (self.name, self.street, self.city) if part]
        return " · ".join(parts)
