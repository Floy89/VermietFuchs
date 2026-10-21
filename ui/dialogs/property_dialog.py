"""Dialoge für Immobilien und Wohnungen."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from typing import Callable

from PyQt6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout, QWidget

from models.apartment import Apartment, PersonChange
from models.property import Property
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import make_button
from ui.widgets.common import DateEdit, DecimalSpin


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
        self._persons_spin.setToolTip("Personenzahl von Anfang an - spätere Änderungen darunter mit Datum eintragen.")
        self._changes_editor = PersonChangesEditor(apartment.person_changes if apartment else [])
        if apartment:
            self._area_spin.set_decimal(apartment.area_sqm)
            self._persons_spin.setValue(apartment.person_count)
        hint = QLabel(
            "Ändert sich die Personenzahl (Geburt, Einzug, Auszug), eine Änderung mit „ab“-Datum eintragen "
            "statt die Zahl oben zu überschreiben - frühere Abrechnungen bleiben dann unverändert."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        self.form.addRow("Wohnungsnummer", self._number_edit)
        self.form.addRow("Wohnfläche", self._area_spin)
        self.form.addRow("Personenanzahl", self._persons_spin)
        self.form.addRow("Änderungen", self._changes_editor)
        self.form.addRow("", hint)

    def _apply(self) -> None:
        if self._apartment is None:
            apartment = Apartment(
                property_id=self._property_id,
                number=self._number_edit.text(),
                area_sqm=self._area_spin.decimal_value(),
                person_count=self._persons_spin.value(),
                person_changes=self._changes_editor.changes(),
            )
        else:
            apartment = replace(
                self._apartment,
                number=self._number_edit.text(),
                area_sqm=self._area_spin.decimal_value(),
                person_count=self._persons_spin.value(),
                person_changes=self._changes_editor.changes(),
            )
        self._save(apartment)


class PersonChangesEditor(QWidget):
    """Liste der Änderungen der Personenzahl: je Zeile "ab Datum" und neue Anzahl."""

    def __init__(self, changes: list[PersonChange], parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._rows: list[tuple[QWidget, DateEdit, QSpinBox]] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.add_button = make_button("Änderung hinzufügen", "plus")
        self.add_button.clicked.connect(lambda: self.add_row(date(date.today().year + 1, 1, 1), 1))
        self._layout.addWidget(self.add_button)
        for change in sorted(changes, key=lambda c: c.valid_from):
            self.add_row(change.valid_from, change.person_count)

    def add_row(self, valid_from: date, persons: int) -> None:
        """Fügt eine Zeile hinzu (vor dem Knopf)."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("ab"))
        date_edit = DateEdit(valid_from)
        layout.addWidget(date_edit)
        spin = QSpinBox()
        spin.setRange(0, 99)
        spin.setValue(persons)
        spin.setSuffix(" Personen")
        layout.addWidget(spin, 1)
        remove = make_button("", "trash", "danger", tooltip="Änderung entfernen")
        remove.clicked.connect(lambda: self._remove(row))
        layout.addWidget(remove)
        self._rows.append((row, date_edit, spin))
        self._layout.insertWidget(self._layout.count() - 1, row)

    def _remove(self, row: QWidget) -> None:
        self._rows = [entry for entry in self._rows if entry[0] is not row]
        row.setParent(None)
        row.deleteLater()

    def changes(self) -> list[PersonChange]:
        """Die eingetragenen Änderungen, nach Datum sortiert."""
        return sorted(
            (PersonChange(date_edit.value(), spin.value()) for _row, date_edit, spin in self._rows),
            key=lambda change: change.valid_from,
        )
