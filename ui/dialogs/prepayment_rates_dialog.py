"""Dialoge für die Historie der vereinbarten Nebenkosten-Vorauszahlungen."""

from __future__ import annotations

from dataclasses import replace
from datetime import date

from PyQt6.QtWidgets import QCheckBox, QDialog, QHBoxLayout, QLabel, QLineEdit, QVBoxLayout, QWidget

from models.prepayment_rate import PrepaymentRate
from models.tenant import Tenant
from services.prepayment_rate_service import PrepaymentRateService
from ui.dialogs.base_dialog import FormDialog
from ui.helpers import ErrorGuard, confirm, make_button
from ui.widgets.common import Column, DateEdit, MoneySpin, ObjectTable
from utils.formatters import format_date, format_money


class PrepaymentRateEditDialog(FormDialog):
    """Eine Vorauszahlungsrate anlegen (z. B. eine Erhöhung) oder bearbeiten."""

    def __init__(
        self,
        service: PrepaymentRateService,
        tenant: Tenant,
        rate: PrepaymentRate | None = None,
        suggested_start: date | None = None,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            service: Service der Vorauszahlungsraten.
            tenant: Der Mieter, für den die Rate gilt.
            rate: Zu bearbeitende Rate; ``None`` legt eine neue an.
            suggested_start: Vorschlag für "Gültig ab" bei einer neuen Rate.
        """
        super().__init__("Vorauszahlung bearbeiten" if rate else "Neue Vorauszahlung", parent)
        self._service = service
        self._tenant = tenant
        self._rate = rate

        self._amount_spin = MoneySpin()
        self._from_edit = DateEdit(suggested_start or tenant.start_date)
        self._has_end_check = QCheckBox("Gültig bis")
        self._to_edit = DateEdit()
        self._to_edit.setEnabled(False)
        self._has_end_check.toggled.connect(self._to_edit.setEnabled)
        end_row = QHBoxLayout()
        end_row.addWidget(self._has_end_check)
        end_row.addWidget(self._to_edit, 1)
        self._note_edit = QLineEdit()
        self._note_edit.setPlaceholderText("z. B. Anpassung nach Abrechnung 2025")
        hint = QLabel(
            "Ohne 'Gültig bis' gilt die Rate bis auf Weiteres. Läuft eine frühere Rate noch "
            "unbefristet, wird sie automatisch zum Vortag dieser Rate beendet."
        )
        hint.setObjectName("Muted")
        hint.setWordWrap(True)

        self.form.addRow("Monatliche Vorauszahlung", self._amount_spin)
        self.form.addRow("Gültig ab", self._from_edit)
        self.form.addRow("Ende", end_row)
        self.form.addRow("Bemerkung", self._note_edit)
        self.form.addRow(hint)

        if rate:
            self._amount_spin.set_money(rate.amount)
            self._from_edit.set_value(rate.valid_from)
            if rate.valid_to:
                self._has_end_check.setChecked(True)
                self._to_edit.set_value(rate.valid_to)
            self._note_edit.setText(rate.note)

    def _apply(self) -> None:
        assert self._tenant.id is not None  # der Dialog wird nur für gespeicherte Mieter geöffnet
        values = {
            "tenant_id": self._tenant.id,
            "amount": self._amount_spin.money(),
            "valid_from": self._from_edit.value(),
            "valid_to": self._to_edit.value() if self._has_end_check.isChecked() else None,
            "note": self._note_edit.text(),
        }
        rate = replace(self._rate, **values) if self._rate else PrepaymentRate(**values)
        self._service.save_rate(rate)


class PrepaymentRatesDialog(QDialog):
    """Zeigt die Historie der Vorauszahlungen eines Mieters und erlaubt Änderungen."""

    def __init__(self, service: PrepaymentRateService, tenant: Tenant, parent: QWidget | None = None) -> None:
        """
        Args:
            service: Service der Vorauszahlungsraten.
            tenant: Der Mieter, dessen Historie angezeigt wird.
        """
        super().__init__(parent)
        self._service = service
        self._tenant = tenant
        self.setWindowTitle(f"Vorauszahlungen – {tenant.name}")
        self.resize(680, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        intro = QLabel(
            "Vereinbarte monatliche Nebenkosten-Vorauszahlungen. Eine Erhöhung ist eine neue Rate "
            "mit späterem Beginn - so bleibt die Historie erhalten. Das Soll einer Abrechnung wird "
            "aus diesen Raten taggenau berechnet."
        )
        intro.setObjectName("Muted")
        intro.setWordWrap(True)
        layout.addWidget(intro)

        today = date.today()
        self._table: ObjectTable[PrepaymentRate] = ObjectTable(
            [
                Column("Gültig ab", lambda r: format_date(r.valid_from), lambda r: r.valid_from.isoformat()),
                Column(
                    "Gültig bis",
                    lambda r: format_date(r.valid_to) if r.valid_to else "bis auf Weiteres",
                    lambda r: r.valid_to.isoformat() if r.valid_to else "9999",
                ),
                Column("Monatlich", lambda r: format_money(r.amount), lambda r: r.amount.cents, True),
                Column(
                    "Status",
                    lambda r: "aktuell" if r.period.contains(today) else "künftig" if r.valid_from > today else "abgelaufen",
                    color=lambda r: "success" if r.period.contains(today) else "muted",
                ),
                Column("Bemerkung", lambda r: r.note),
            ]
        )
        layout.addWidget(self._table, 1)

        self._add_button = make_button("Neue Rate / Erhöhung", "plus", "primary")
        self._edit_button = make_button("Bearbeiten", "edit")
        self._delete_button = make_button("Löschen", "trash", "danger")
        close_button = make_button("Schließen")
        buttons = QHBoxLayout()
        for button in (self._add_button, self._edit_button, self._delete_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._add_button.clicked.connect(self._add)
        self._edit_button.clicked.connect(self._edit)
        self._delete_button.clicked.connect(self._delete)
        close_button.clicked.connect(self.accept)
        self._table.selection_changed.connect(self._update_buttons)
        self._table.item_activated.connect(lambda *_: self._edit())
        self._reload()

    def _rates(self) -> list[PrepaymentRate]:
        """Lädt die Historie des Mieters."""
        assert self._tenant.id is not None
        return self._service.list_rates(self._tenant.id)

    def _reload(self) -> None:
        with ErrorGuard(self):
            self._table.set_items(self._rates())
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = self._table.selected_item() is not None
        self._edit_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)

    def _add(self) -> None:
        # Für die erste Rate den Vertragsbeginn vorschlagen, sonst "heute".
        suggested = date.today() if self._table.model().rowCount() else self._tenant.start_date
        if PrepaymentRateEditDialog(self._service, self._tenant, None, suggested, self).exec():
            self._reload()

    def _edit(self) -> None:
        rate = self._table.selected_item()
        if rate and PrepaymentRateEditDialog(self._service, self._tenant, rate, parent=self).exec():
            self._reload()

    def _delete(self) -> None:
        rate = self._table.selected_item()
        if rate is None or rate.id is None:
            return
        text = f"Rate über {format_money(rate.amount)} ab {format_date(rate.valid_from)} wirklich löschen?"
        if not confirm(self, "Vorauszahlung löschen", text):
            return
        with ErrorGuard(self):
            self._service.delete_rate(rate.id)
        self._reload()
