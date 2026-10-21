"""Tab 3 - Mieter: Mieter und ihre Verträge verwalten."""

from __future__ import annotations

from datetime import date

from PyQt6.QtWidgets import QCheckBox, QComboBox, QHBoxLayout, QWidget

from app_context import AppContext
from models.tenant import Tenant
from models.value_objects import Money
from ui.dialogs.deposit_dialog import DepositDialog
from ui.dialogs.key_overrides_dialog import KeyOverridesDialog
from ui.dialogs.prepayment_rates_dialog import PrepaymentRatesDialog
from ui.dialogs.tenant_dialog import TenantDialog
from ui.helpers import ErrorGuard, confirm, fill_combo, make_button
from ui.lookups import Lookups
from ui.widgets.common import BaseTab, Card, Column, ObjectTable, muted_label, page_title
from utils.formatters import format_date, format_money


class TenantTab(BaseTab):
    """Liste aller Mieter mit Filter nach Immobilie und Vertragsstatus."""

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._lookups: Lookups | None = None
        self.root.addWidget(page_title("Mieter und Verträge"))

        card = Card("Mieter")
        self._add_button = make_button("Neu", "plus", "primary")
        self._edit_button = make_button("Bearbeiten", "edit")
        self._delete_button = make_button("Löschen", "trash", "danger")
        self._rates_button = make_button(
            "Vorauszahlungen …", "coins", tooltip="Historie der vereinbarten Nebenkosten-Vorauszahlungen, Erhöhungen"
        )
        self._keys_button = make_button(
            "Umlageschlüssel …", "settings", tooltip="Im Mietvertrag abweichend vereinbarte Umlageschlüssel"
        )
        self._deposit_button = make_button(
            "Kaution …", "coins", tooltip="Kaution mit Zinsen und Rückzahlungsbetrag bei Auszug"
        )
        for button in (
            self._deposit_button, self._keys_button, self._rates_button,
            self._add_button, self._edit_button, self._delete_button,
        ):
            card.header.addWidget(button)

        # --- Filterzeile ---------------------------------------------------
        filters = QHBoxLayout()
        self._property_combo = QComboBox()
        self._property_combo.setMinimumWidth(240)
        self._active_only_check = QCheckBox("Nur laufende Verträge")
        self._count_label = muted_label()
        filters.addWidget(muted_label("Immobilie"))
        filters.addWidget(self._property_combo)
        filters.addSpacing(12)
        filters.addWidget(self._active_only_check)
        filters.addStretch(1)
        filters.addWidget(self._count_label)
        card.body.addLayout(filters)

        self._table: ObjectTable[Tenant] = ObjectTable(
            [
                Column("Name", lambda t: t.name),
                Column("Immobilie", lambda t: self._property_name(t)),
                Column("Wohnung", lambda t: self._lookups.apartment_number(t.apartment_id) if self._lookups else ""),
                Column("Beginn", lambda t: format_date(t.start_date), lambda t: t.start_date.isoformat()),
                Column(
                    "Ende",
                    lambda t: format_date(t.end_date) if t.end_date else "unbefristet",
                    lambda t: t.end_date.isoformat() if t.end_date else "9999",
                ),
                Column("Miete", lambda t: format_money(t.rent_amount), lambda t: t.rent_amount.cents, True),
                Column(
                    "NK-Vorauszahlung",
                    lambda t: format_money(self._current_prepayment(t)),
                    lambda t: self._current_prepayment(t).cents,
                    True,
                ),
                Column(
                    "Status",
                    lambda t: "aktiv" if t.is_active(date.today()) else "beendet" if t.end_date and t.end_date < date.today() else "zukünftig",
                    color=lambda t: "success" if t.is_active(date.today()) else "muted",
                ),
                Column(
                    "Kaution",
                    lambda t: format_money(t.deposit_amount) if t.has_deposit else "",
                    lambda t: t.deposit_amount.cents,
                    True,
                ),
                Column("Kontakt", lambda t: t.contact),
            ]
        )
        card.body.addWidget(self._table)
        self.root.addWidget(card, 1)

        self._keys_button.clicked.connect(self._edit_keys)
        self._deposit_button.clicked.connect(self._show_deposit)
        self._rates_button.clicked.connect(self._edit_rates)
        self._add_button.clicked.connect(self._add)
        self._edit_button.clicked.connect(self._edit)
        self._delete_button.clicked.connect(self._delete)
        self._table.item_activated.connect(lambda *_: self._edit())
        self._table.selection_changed.connect(self._update_buttons)
        self._property_combo.currentIndexChanged.connect(lambda *_: self._apply_filter())
        self._active_only_check.toggled.connect(lambda *_: self._apply_filter())

    def _property_name(self, tenant: Tenant) -> str:
        """Name der Immobilie, in der der Mieter wohnt."""
        if self._lookups is None:
            return ""
        prop = self._lookups.property_of_apartment(tenant.apartment_id)
        return prop.name if prop else ""

    def _current_prepayment(self, tenant: Tenant) -> Money:
        """Die heute gültige monatliche Vorauszahlung des Mieters."""
        return self._lookups.prepayment_on(tenant.id) if self._lookups else Money.zero()

    def refresh(self) -> None:
        """Lädt Stammdaten und Mieter neu."""
        with ErrorGuard(self):
            self._lookups = Lookups(self._context)
            fill_combo(self._property_combo, [("Alle Immobilien", None), *self._lookups.property_choices()])
        self._apply_filter()

    def _apply_filter(self) -> None:
        """Zeigt die Mieter passend zu den gewählten Filtern an."""
        if self._lookups is None:
            return
        property_id = self._property_combo.currentData()
        today = date.today()
        tenants = []
        for tenant in self._lookups.tenants:
            prop = self._lookups.property_of_apartment(tenant.apartment_id)
            if property_id is not None and (prop is None or prop.id != property_id):
                continue
            if self._active_only_check.isChecked() and not tenant.is_active(today):
                continue
            tenants.append(tenant)
        self._table.set_items(tenants)
        self._count_label.setText(f"{len(tenants)} Mieter")
        self._update_buttons()

    def _update_buttons(self) -> None:
        has_selection = self._table.selected_item() is not None
        self._edit_button.setEnabled(has_selection)
        self._delete_button.setEnabled(has_selection)
        self._rates_button.setEnabled(has_selection)
        self._keys_button.setEnabled(has_selection)
        selected = self._table.selected_item()
        self._deposit_button.setEnabled(selected is not None and selected.has_deposit)

    def _edit_keys(self) -> None:
        """Öffnet die abweichenden Umlageschlüssel des markierten Mieters."""
        tenant = self._table.selected_item()
        if tenant is not None:
            KeyOverridesDialog(self._context.key_overrides, tenant, self).exec()

    def _show_deposit(self) -> None:
        """Zeigt Kaution, Zinsen und Rückzahlungsbetrag des markierten Mieters."""
        tenant = self._table.selected_item()
        if tenant is not None and tenant.has_deposit:
            DepositDialog(tenant, self).exec()

    def _edit_rates(self) -> None:
        """Öffnet die Historie der Vorauszahlungen des markierten Mieters."""
        tenant = self._table.selected_item()
        if tenant is None:
            return
        PrepaymentRatesDialog(self._context.prepayment_rates, tenant, self).exec()
        self.refresh()

    def _add(self) -> None:
        if self._lookups is None:
            return
        dialog = TenantDialog(
            self._context.tenants.save_tenant,
            self._lookups,
            preselected_property_id=self._property_combo.currentData(),
            parent=self,
        )
        if dialog.exec():
            self.refresh()

    def _edit(self) -> None:
        tenant = self._table.selected_item()
        if tenant is None or self._lookups is None:
            return
        if TenantDialog(self._context.tenants.save_tenant, self._lookups, tenant, parent=self).exec():
            self.refresh()

    def _delete(self) -> None:
        tenant = self._table.selected_item()
        if tenant is None or tenant.id is None:
            return
        if not confirm(self, "Mieter löschen", f"Mieter '{tenant.name}' wirklich löschen?"):
            return
        with ErrorGuard(self):
            self._context.tenants.delete_tenant(tenant.id)
        self.refresh()
