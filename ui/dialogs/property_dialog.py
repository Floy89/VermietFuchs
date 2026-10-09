"""Dialoge für Immobilien und Wohnungen."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from PyQt6.QtWidgets import QLineEdit, QSpinBox, QWidget

from models.apartment import Apartment
from models.property import Property
from ui.dialogs.base_dialog import FormDialog
from ui.widgets.common import DecimalSpin


class PropertyDialog(FormDialog):
    """Immobilie anlegen oder bearbeiten."""

    def __init__(
        self,
        save: Callable[[Property], Property],
        prop: Property | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            save: Funktion, die die Immobilie speichert (Service-Methode).
            prop: Zu bearbeitende Immobilie; ``None`` legt eine neue an.
        """
        super().__init__("Immobilie bearbeiten" if prop else "Neue Immobilie", parent)
        self._save = save
        self._property = prop

        self._name_edit = QLineEdit(prop.name if prop else "")
        self._name_edit.setPlaceholderText("z. B. Musterstraße 12")
        self._address_edit = QLineEdit(prop.address if prop else "")
        self._address_edit.setPlaceholderText("Straße Hausnummer, PLZ Ort")
        self.form.addRow("Name", self._name_edit)
        self.form.addRow("Adresse", self._address_edit)

    def _apply(self) -> None:
        if self._property is None:
            prop = Property(name=self._name_edit.text(), address=self._address_edit.text())
        else:
            # Mit einer Kopie arbeiten: Scheitert das Speichern, bleibt das
            # Original (das noch in der Tabelle angezeigt wird) unverändert.
            prop = replace(self._property, name=self._name_edit.text(), address=self._address_edit.text())
        self._save(prop)


class ApartmentDialog(FormDialog):
    """Wohnung anlegen oder bearbeiten."""

    def __init__(
        self,
        save: Callable[[Apartment], Apartment],
        property_id: int,
        apartment: Apartment | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            save: Funktion, die die Wohnung speichert (Service-Methode).
            property_id: Immobilie, zu der die Wohnung gehört.
            apartment: Zu bearbeitende Wohnung; ``None`` legt eine neue an.
        """
        super().__init__("Wohnung bearbeiten" if apartment else "Neue Wohnung", parent)
        self._save = save
        self._property_id = property_id
        self._apartment = apartment

        self._number_edit = QLineEdit(apartment.number if apartment else "")
        self._number_edit.setPlaceholderText("z. B. 1. OG links")
        self._area_spin = DecimalSpin(decimals=2, maximum=9_999.0, suffix=" m²")
        self._persons_spin = QSpinBox()
        self._persons_spin.setRange(0, 99)
        self._persons_spin.setValue(1)
        if apartment:
            self._area_spin.set_decimal(apartment.area_sqm)
            self._persons_spin.setValue(apartment.person_count)

        self.form.addRow("Wohnungsnummer", self._number_edit)
        self.form.addRow("Wohnfläche", self._area_spin)
        self.form.addRow("Personenanzahl", self._persons_spin)

    def _apply(self) -> None:
        if self._apartment is None:
            apartment = Apartment(
                property_id=self._property_id,
                number=self._number_edit.text(),
                area_sqm=self._area_spin.decimal_value(),
                person_count=self._persons_spin.value(),
            )
        else:
            apartment = replace(
                self._apartment,
                number=self._number_edit.text(),
                area_sqm=self._area_spin.decimal_value(),
                person_count=self._persons_spin.value(),
            )
        self._save(apartment)
