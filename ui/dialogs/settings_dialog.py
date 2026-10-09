"""Dialog für die Einstellungen (Vermieter- und Bankdaten)."""

from __future__ import annotations

from PyQt6.QtWidgets import QLabel, QLineEdit, QSpinBox, QWidget

from models.settings import LandlordSettings
from services.settings_service import SettingsService
from ui.dialogs.base_dialog import FormDialog


class SettingsDialog(FormDialog):
    """Bearbeitet die Daten, die als Absender auf den PDF-Abrechnungen stehen."""

    def __init__(self, settings: SettingsService, parent: QWidget | None = None) -> None:
        """
        Args:
            settings: Service zum Laden und Speichern der Einstellungen.
        """
        super().__init__("Einstellungen", parent)
        self._settings = settings
        landlord = settings.load_landlord()

        hint = QLabel("Diese Angaben erscheinen als Absender auf den Abrechnungsschreiben.")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        self.form.addRow(hint)

        self._name_edit = QLineEdit(landlord.name)
        self._street_edit = QLineEdit(landlord.street)
        self._city_edit = QLineEdit(landlord.city)
        self._city_edit.setPlaceholderText("PLZ Ort")
        self._phone_edit = QLineEdit(landlord.phone)
        self._email_edit = QLineEdit(landlord.email)
        self._bank_edit = QLineEdit(landlord.bank_name)
        self._iban_edit = QLineEdit(landlord.iban)
        self._term_spin = QSpinBox()
        self._term_spin.setRange(1, 365)
        self._term_spin.setSuffix(" Tage")
        self._term_spin.setValue(landlord.payment_term_days)

        self.form.addRow("Name / Firma", self._name_edit)
        self.form.addRow("Straße, Hausnummer", self._street_edit)
        self.form.addRow("PLZ, Ort", self._city_edit)
        self.form.addRow("Telefon", self._phone_edit)
        self.form.addRow("E-Mail", self._email_edit)
        self.form.addRow("Bank", self._bank_edit)
        self.form.addRow("IBAN", self._iban_edit)
        self.form.addRow("Zahlungsfrist für Nachzahlungen", self._term_spin)


    def _apply(self) -> None:
        self._settings.save_landlord(
            LandlordSettings(
                name=self._name_edit.text(),
                street=self._street_edit.text(),
                city=self._city_edit.text(),
                phone=self._phone_edit.text(),
                email=self._email_edit.text(),
                bank_name=self._bank_edit.text(),
                iban=self._iban_edit.text(),
                payment_term_days=self._term_spin.value(),
            )
        )
