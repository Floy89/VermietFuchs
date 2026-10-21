"""Dialog für Mieter und ihren Mietvertrag."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Callable

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from models.deposit import InterestRate
from models.exceptions import ValidationError
from models.tenant import Tenant
from models.value_objects import Money
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import fill_combo, make_button, select_data
from ui.lookups import Lookups
from ui.widgets.common import DateEdit, DecimalSpin, MoneySpin
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

        # Mietkaution: Betrag, Einzahlung (ohne Angabe = Vertragsbeginn) und Zinssätze des Kautionskontos.
        self._deposit_spin = MoneySpin()
        self._deposit_spin.setToolTip("Eingezahlte Mietkaution (höchstens drei Monatsmieten ohne Nebenkosten)")
        self._has_paid_on_check = QCheckBox("abweichend eingezahlt am")
        self._has_paid_on_check.setToolTip("Ohne Haken gilt der Vertragsbeginn als Tag der Einzahlung.")
        self._paid_on_edit = DateEdit()
        self._paid_on_edit.setEnabled(False)
        self._has_paid_on_check.toggled.connect(self._paid_on_edit.setEnabled)
        deposit_row = QHBoxLayout()
        deposit_row.addWidget(self._deposit_spin, 1)
        deposit_row.addWidget(self._has_paid_on_check)
        deposit_row.addWidget(self._paid_on_edit)
        self._rates_editor = InterestRatesEditor(self._start_edit.value)
        self._deposit_hint = QLabel()
        self._deposit_hint.setObjectName("Muted")
        self._deposit_hint.setWordWrap(True)
        self._deposit_spin.valueChanged.connect(lambda *_: self._update_deposit_hint())
        self._rent_spin.valueChanged.connect(lambda *_: self._update_deposit_hint())
        self._rates_editor.changed.connect(self._update_deposit_hint)

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
        self.form.addRow("Kaution", deposit_row)
        self.form.addRow("Zinssatz Kaution", self._rates_editor)
        self.form.addRow("", self._deposit_hint)

        if tenant:
            self._load(tenant)
        self._update_deposit_hint()

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
        self._deposit_spin.set_money(tenant.deposit_amount)
        if tenant.deposit_paid_on:
            self._has_paid_on_check.setChecked(True)
            self._paid_on_edit.set_value(tenant.deposit_paid_on)
        for rate in tenant.deposit_rates:
            self._rates_editor.add_row(rate.valid_from, rate.percent)

    def _update_deposit_hint(self) -> None:
        """Weist auf die Höchstgrenze hin: drei Monatsmieten ohne Nebenkosten (§ 551 Abs. 1 BGB)."""
        deposit, rent = self._deposit_spin.money(), self._rent_spin.money()
        text = ""
        if not deposit.is_zero and not rent.is_zero and deposit.cents > 3 * rent.cents:
            text = (
                f"Die Kaution ist höher als drei Monatsmieten ({format_money(Money(rent.cents * 3))}) - "
                "mehr ist nach § 551 Abs. 1 BGB nicht zulässig."
            )
        elif not deposit.is_zero and not self._rates_editor.rates():
            text = "Ohne Zinssatz wird die Kaution mit 0 % gerechnet."
        self._deposit_hint.setText(text)
        self.form.setRowVisible(self._deposit_hint, bool(text))

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
            "deposit_amount": self._deposit_spin.money(),
            "deposit_paid_on": self._paid_on_edit.value() if self._has_paid_on_check.isChecked() else None,
            "deposit_rates": self._rates_editor.rates(),
        }
        if self._tenant:
            self._save(replace(self._tenant, **values), None)
        else:
            self._save(Tenant(**values), self._prepayment_spin.money())


class InterestRatesEditor(QWidget):
    """Zinssätze des Kautionskontos: je Zeile "ab Datum" und Zinssatz in % pro Jahr.

    Signals:
        changed: Eine Zeile wurde hinzugefügt oder entfernt.
    """

    changed = pyqtSignal()

    def __init__(self, default_start: Callable[[], date], parent: QWidget | None = None) -> None:
        """
        Args:
            default_start: Liefert das Datum für die erste neue Zeile (Vertragsbeginn).
        """
        super().__init__(parent)
        self._default_start = default_start
        self._rows: list[tuple[QWidget, DateEdit, DecimalSpin]] = []
        self._layout = QVBoxLayout(self)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(4)
        self.add_button = make_button("Zinssatz hinzufügen", "plus", tooltip="z. B. Zinssatz des Mietkautionskontos")
        self.add_button.clicked.connect(self._add_default)
        self._layout.addWidget(self.add_button)

    def _add_default(self) -> None:
        start = self._rows[-1][1].value() if self._rows else self._default_start()
        if self._rows:
            start = date(start.year + 1, 1, 1)
        self.add_row(start, Decimal(0))

    def add_row(self, valid_from: date, percent: Decimal) -> None:
        """Fügt eine Zeile hinzu (vor dem Knopf)."""
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(QLabel("ab"))
        date_edit = DateEdit(valid_from)
        layout.addWidget(date_edit)
        spin = DecimalSpin(decimals=3, maximum=20.0, suffix=" % p. a.")
        spin.set_decimal(percent)
        layout.addWidget(spin, 1)
        remove = make_button("", "trash", "danger", tooltip="Zinssatz entfernen")
        remove.clicked.connect(lambda: self._remove(row))
        layout.addWidget(remove)
        self._rows.append((row, date_edit, spin))
        self._layout.insertWidget(self._layout.count() - 1, row)
        self.changed.emit()

    def _remove(self, row: QWidget) -> None:
        self._rows = [entry for entry in self._rows if entry[0] is not row]
        row.setParent(None)
        row.deleteLater()
        self.changed.emit()

    def rates(self) -> list[InterestRate]:
        """Die eingetragenen Zinssätze, nach Datum sortiert."""
        return sorted(
            (InterestRate(date_edit.value(), spin.decimal_value()) for _row, date_edit, spin in self._rows),
            key=lambda rate: rate.valid_from,
        )
