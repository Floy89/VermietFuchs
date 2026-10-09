"""Anwendungsfälle rund um die Einstellungen eines Benutzers."""

from __future__ import annotations

from models.exceptions import ValidationError
from models.settings import LandlordSettings
from repositories.interfaces import SettingsRepository

# Schlüssel in der Tabelle "settings".
_KEY_NAME = "landlord.name"
_KEY_STREET = "landlord.street"
_KEY_CITY = "landlord.city"
_KEY_PHONE = "landlord.phone"
_KEY_EMAIL = "landlord.email"
_KEY_BANK = "landlord.bank_name"
_KEY_IBAN = "landlord.iban"
_KEY_PAYMENT_TERM = "billing.payment_term_days"
_KEY_THEME = "ui.theme"

THEME_LIGHT = "light"
THEME_DARK = "dark"


class SettingsService:
    """Liest und speichert Vermieterdaten und Oberflächen-Einstellungen."""

    def __init__(self, settings: SettingsRepository) -> None:
        self._settings = settings

    def load_landlord(self) -> LandlordSettings:
        """Lädt die Absender- und Bankdaten für Abrechnungsschreiben."""
        values = self._settings.get_all()
        defaults = LandlordSettings()
        try:
            payment_term = int(values.get(_KEY_PAYMENT_TERM, defaults.payment_term_days))
        except ValueError:
            payment_term = defaults.payment_term_days
        return LandlordSettings(
            name=values.get(_KEY_NAME, ""),
            street=values.get(_KEY_STREET, ""),
            city=values.get(_KEY_CITY, ""),
            phone=values.get(_KEY_PHONE, ""),
            email=values.get(_KEY_EMAIL, ""),
            bank_name=values.get(_KEY_BANK, ""),
            iban=values.get(_KEY_IBAN, ""),
            payment_term_days=payment_term,
        )

    def save_landlord(self, landlord: LandlordSettings) -> None:
        """Speichert die Absender- und Bankdaten.

        Raises:
            ValidationError: Bei einer unplausiblen Zahlungsfrist.
        """
        if not 1 <= landlord.payment_term_days <= 365:
            raise ValidationError("Die Zahlungsfrist muss zwischen 1 und 365 Tagen liegen.")
        self._settings.set_many(
            {
                _KEY_NAME: landlord.name.strip(),
                _KEY_STREET: landlord.street.strip(),
                _KEY_CITY: landlord.city.strip(),
                _KEY_PHONE: landlord.phone.strip(),
                _KEY_EMAIL: landlord.email.strip(),
                _KEY_BANK: landlord.bank_name.strip(),
                _KEY_IBAN: landlord.iban.strip(),
                _KEY_PAYMENT_TERM: str(landlord.payment_term_days),
            }
        )

    def theme(self) -> str:
        """Gewähltes Farbschema (``"light"`` oder ``"dark"``)."""
        value = self._settings.get_all().get(_KEY_THEME, THEME_LIGHT)
        return value if value in (THEME_LIGHT, THEME_DARK) else THEME_LIGHT

    def set_theme(self, theme: str) -> None:
        """Speichert das Farbschema."""
        if theme not in (THEME_LIGHT, THEME_DARK):
            raise ValidationError(f"Unbekanntes Farbschema: {theme}")
        self._settings.set_many({_KEY_THEME: theme})
