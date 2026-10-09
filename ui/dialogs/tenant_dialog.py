"""Dialog für Mieter und ihren Mietvertrag."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable

from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QWidget

from models.exceptions import ValidationError
from models.tenant import Tenant
from models.value_objects import Money
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import fill_combo, select_data
from ui.lookups import Lookups
from ui.widgets.common import DateEdit, MoneySpin
from utils.formatters import format_money


class TenantDialog(FormDialog):
    """Mieter anlegen oder bearbeiten."""

    def __init__(
        self,
        save: Callable[[Tenant, Money | None], Tenant],
        lookups: Lookups,
        tenant: Tenant | None = None,
        preselected_property_id: int | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            save: Funktion, die den Mieter speichert (Service-Methode). Das
                zweite Argument ist die anfängliche Vorauszahlung eines neuen Mieters.
            lookups: Stammdaten für die Wohnungsauswahl.
            tenant: Zu bearbeitender Mieter; ``None`` legt einen neuen an.
            preselected_property_id: Schränkt die Wohnungsauswahl bei einem
                neuen Mieter auf diese Immobilie ein.
        """
        super().__init__("Mieter bearbeiten" if tenant else "Neuer Mieter", parent)
        self._save = save
        self._tenant = tenant

        self._apartment_combo = QComboBox()
        choices = lookups.apartment_choices(None if tenant else preselected_property_id)
        fill_combo(self._apartment_combo, choices or lookups.apartment_choices())

        self._name_edit = QLineEdit()
        self._contact_edit = QLineEdit()
        self._contact_edit.setPlaceholderText("Telefon, E-Mail …")
        self._start_edit = DateEdit()

        # Vertragsende ist optional: Ohne Häkchen läuft der Vertrag unbefristet.
        self._has_end_check = QCheckBox("Vertrag endet am")
        self._end_edit = DateEdit()
        self._end_edit.setEnabled(False)
        self._has_end_check.toggled.connect(self._end_edit.setEnabled)
        end_row = QHBoxLayout()
        end_row.addWidget(self._has_end_check)
        end_row.addWidget(self._end_edit, 1)

        self._rent_spin = MoneySpin()
        self._prepayment_spin = MoneySpin()

        self.form.addRow("Wohnung", self._apartment_combo)
        self.form.addRow("Name", self._name_edit)
        self.form.addRow("Kontakt", self._contact_edit)
        self.form.addRow("Vertragsbeginn", self._start_edit)
        self.form.addRow("Vertragsende", end_row)
        self.form.addRow("Mietzins (monatlich)", self._rent_spin)
        if tenant is None:
            self.form.addRow("NK-Vorauszahlung (monatlich)", self._prepayment_spin)
        else:
            # Bei bestehenden Mietern wird die Vorauszahlung als Historie
            # gepflegt (Erhöhungen mit Datum) - hier nur zur Information.
            current = QLabel(
                f"{format_money(lookups.prepayment_on(tenant.id))} aktuell – "
                "Änderungen über 'Vorauszahlungen …' in der Mieterliste"
            )
            current.setObjectName("Muted")
            current.setWordWrap(True)
            self.form.addRow("NK-Vorauszahlung", current)

        if tenant:
            self._load(tenant)

    def _load(self, tenant: Tenant) -> None:
        """Überträgt die Werte eines bestehenden Mieters in die Felder."""
        select_data(self._apartment_combo, tenant.apartment_id)
        self._name_edit.setText(tenant.name)
        self._contact_edit.setText(tenant.contact)
        self._start_edit.set_value(tenant.start_date)
        if tenant.end_date:
            self._has_end_check.setChecked(True)
            self._end_edit.set_value(tenant.end_date)
        self._rent_spin.set_money(tenant.rent_amount)

    def _apply(self) -> None:
        apartment_id = self._apartment_combo.currentData()
        if apartment_id is None:
            raise ValidationError("Bitte zuerst im Tab 'Immobilien' eine Wohnung anlegen.")

        values = {
            "apartment_id": int(apartment_id),
            "name": self._name_edit.text(),
            "contact": self._contact_edit.text(),
            "start_date": self._start_edit.value(),
            "end_date": self._end_edit.value() if self._has_end_check.isChecked() else None,
            "rent_amount": self._rent_spin.money(),
        }
        if self._tenant:
            self._save(replace(self._tenant, **values), None)
        else:
            self._save(Tenant(**values), self._prepayment_spin.money())
