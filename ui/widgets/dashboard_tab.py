"""Tab 1 - Dashboard: Kennzahlen, Jahresübersicht je Immobilie, letzte Änderungen."""

from __future__ import annotations

from datetime import date

from PyQt6.QtWidgets import QHBoxLayout, QSpinBox, QWidget

from app_context import AppContext
from models.audit_entry import AuditEntry
from services.audit_service import describe_change, table_label
from services.dashboard_service import PropertyYearSummary
from ui.helpers import ErrorGuard
from ui.widgets.common import BaseTab, Card, Column, ObjectTable, StatCard, muted_label, page_title
from utils.formatters import format_datetime, format_money

_RECENT_CHANGES_LIMIT = 12


class DashboardTab(BaseTab):
    """Übersicht über den Datenbestand und das gewählte Abrechnungsjahr."""

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)

        # --- Kopfzeile mit Jahresauswahl ------------------------------------
        header = QHBoxLayout()
        header.addWidget(page_title(f"Willkommen, {context.user.name}"))
        header.addStretch(1)
        header.addWidget(muted_label("Abrechnungsjahr"))
        self._year_spin = QSpinBox()
        self._year_spin.setRange(1990, 2100)
        self._year_spin.setValue(date.today().year - 1)
        self._year_spin.valueChanged.connect(lambda *_: self.refresh())
        header.addWidget(self._year_spin)
        self.root.addLayout(header)

        # --- Kennzahl-Kacheln ---------------------------------------------
        self._properties_card = StatCard("Immobilien", "building")
        self._apartments_card = StatCard("Wohnungen", "home")
        self._tenants_card = StatCard("Aktive Mieter", "users")
        self._costs_card = StatCard("Nebenkosten im Jahr", "calculator")
        self._unallocated_card = StatCard("Bleibt beim Vermieter", "wallet")
        self._prepayments_card = StatCard("Vorauszahlungen im Jahr", "coins")
        cards = QHBoxLayout()
        cards.setSpacing(12)
        for card in (
            self._properties_card,
            self._apartments_card,
            self._tenants_card,
            self._costs_card,
            self._unallocated_card,
            self._prepayments_card,
        ):
            cards.addWidget(card, 1)
        self.root.addLayout(cards)

        # --- Jahresübersicht je Immobilie ----------------------------------
        self._overview_card = Card("Abrechnungsübersicht")
        self._overview_table: ObjectTable[PropertyYearSummary] = ObjectTable(
            [
                Column("Immobilie", lambda row: row.property.name),
                Column("Adresse", lambda row: row.property.address),
                Column("Wohnungen", lambda row: str(row.apartment_count), lambda row: row.apartment_count, True),
                Column("Aktive Mieter", lambda row: str(row.active_tenant_count), lambda row: row.active_tenant_count, True),
                Column("Nebenkosten", lambda row: format_money(row.costs), lambda row: row.costs.cents, True),
                Column("umgelegt", lambda row: format_money(row.allocated), lambda row: row.allocated.cents, True),
                Column(
                    "Bleibt beim Vermieter",
                    lambda row: format_money(row.unallocated),
                    lambda row: row.unallocated.cents,
                    True,
                    # Rot bei negativem Wert: Es wird mehr umgelegt, als angefallen ist.
                    lambda row: "danger" if row.unallocated.is_negative else "primary" if row.unallocated.cents else None,
                ),
                Column("Vorauszahlungen", lambda row: format_money(row.prepayments), lambda row: row.prepayments.cents, True),
                Column(
                    "Saldo Mieter",
                    lambda row: format_money(row.tenant_balance),
                    lambda row: row.tenant_balance.cents,
                    True,
                    lambda row: "danger" if row.tenant_balance.is_negative else "success",
                ),
                Column("Hinweise", lambda row: "prüfen" if row.has_warnings else "", color=lambda row: "danger"),
            ],
            stretch_column=1,
        )
        self._overview_card.body.addWidget(self._overview_table)
        self._overview_card.body.addWidget(
            muted_label(
                "Bleibt beim Vermieter = Nebenkosten, die auf keinen Mieter umgelegt werden (Leerstand, nicht "
                "abgerechnete Zeiträume, nicht zugeordnete Messdienst-Anteile, abweichende Umlageschlüssel). "
                "Saldo Mieter = Vorauszahlungen − umgelegte Kosten; rot bedeutet: insgesamt Nachzahlungen."
            )
        )
        self.root.addWidget(self._overview_card, 3)

        # --- Letzte Änderungen (Audit Trail) --------------------------------
        changes_card = Card("Letzte Änderungen")
        self._changes_table: ObjectTable[AuditEntry] = ObjectTable(
            [
                Column("Zeitpunkt", lambda e: format_datetime(e.timestamp), lambda e: e.timestamp.isoformat()),
                Column("Benutzer", lambda e: e.user_name),
                Column("Bereich", lambda e: table_label(e.table_name)),
                Column("Aktion", lambda e: e.action.label),
                Column("Details", describe_change),
            ]
        )
        changes_card.body.addWidget(self._changes_table)
        self.root.addWidget(changes_card, 2)

    def refresh(self) -> None:
        """Lädt Kennzahlen, Jahresübersicht und die letzten Änderungen neu."""
        year = self._year_spin.value()
        with ErrorGuard(self):
            summary = self._context.dashboard.summary(year)
            self._properties_card.set_value(str(summary.property_count))
            self._apartments_card.set_value(str(summary.apartment_count))
            self._tenants_card.set_value(str(summary.active_tenant_count))
            self._costs_card.set_value(format_money(summary.total_costs))
            self._unallocated_card.set_value(format_money(summary.total_unallocated))
            self._prepayments_card.set_value(format_money(summary.total_prepayments))
            self._overview_card.set_title(f"Abrechnungsübersicht {year}")
            self._overview_table.set_items(summary.properties)
            self._changes_table.set_items(self._context.audit.list_entries(limit=_RECENT_CHANGES_LIMIT))
