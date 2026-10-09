"""Tab 7 - Berichte: Abrechnungsschreiben als PDF und das Änderungsprotokoll."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import QComboBox, QFileDialog, QSplitter, QWidget

from app_context import AppContext
from models.audit_entry import AuditEntry
from models.billing import BillingResult, TenantStatement
from services.audit_service import describe_change, table_label
from ui.helpers import ErrorGuard, fill_combo, make_button
from ui.widgets.billing_tab import (
    RECOMMENDATION_TOOLTIP,
    balance_text,
    period_text,
    recommendation_color,
    recommendation_text,
)
from ui.widgets.common import BaseTab, Card, Column, ObjectTable, PeriodBar, muted_label, page_title
from utils.formatters import format_datetime, format_money

_AUDIT_LIMIT = 1000


class ReportsTab(BaseTab):
    """PDF-Export der Abrechnungen und Einsicht in den Audit Trail."""

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._result: BillingResult | None = None
        self.root.addWidget(page_title("Berichte"))

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_statement_card())
        splitter.addWidget(self._build_audit_card())
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        self.root.addWidget(splitter, 1)

    # ------------------------------------------------------------------ #
    # Aufbau
    # ------------------------------------------------------------------ #
    def _build_statement_card(self) -> Card:
        """Obere Karte: Abrechnungsschreiben auswählen und exportieren."""
        card = Card("Abrechnungsschreiben (PDF)")
        self._export_selected_button = make_button(
            "Auswahl als PDF …", "download", tooltip="Markierte Mieter exportieren (Mehrfachauswahl mit Strg/Umschalt)"
        )
        self._export_all_button = make_button("Alle als PDF …", "download", "primary")
        card.header.addWidget(self._export_selected_button)
        card.header.addWidget(self._export_all_button)

        self._period_bar = PeriodBar()
        self._period_bar.changed.connect(self._recalculate)
        card.body.addWidget(self._period_bar)

        self._statement_table: ObjectTable[TenantStatement] = ObjectTable(
            [
                Column("Wohnung", lambda s: s.apartment.number),
                Column("Mieter", lambda s: s.tenant.name),
                Column(
                    "Abrechnungszeitraum",
                    period_text,
                    lambda s: s.occupancy.start.isoformat(),
                    color=lambda s: "primary" if s.is_adjusted else None,
                ),
                Column("Kostenanteil", lambda s: format_money(s.total_costs), lambda s: s.total_costs.cents, True),
                Column("Vorauszahlungen", lambda s: format_money(s.prepayments), lambda s: s.prepayments.cents, True),
                Column(
                    "Ergebnis",
                    balance_text,
                    lambda s: s.balance.cents,
                    True,
                    lambda s: None if s.balance.is_zero else "success" if s.is_credit else "danger",
                ),
                Column(
                    "Empfohlene Vorauszahlung / Monat",
                    recommendation_text,
                    lambda s: s.recommended_prepayment.cents,
                    True,
                    recommendation_color,
                ),
            ],
            stretch_column=1,
            multi_select=True,
        )
        self._statement_table.horizontalHeader().setToolTip(RECOMMENDATION_TOOLTIP)
        card.body.addWidget(self._statement_table)
        self._hint_label = muted_label()
        card.body.addWidget(self._hint_label)

        self._export_selected_button.clicked.connect(lambda: self._export(self._statement_table.selected_items()))
        self._export_all_button.clicked.connect(lambda: self._export(self._result.statements if self._result else []))
        self._statement_table.selection_changed.connect(self._update_buttons)
        return card

    def _build_audit_card(self) -> Card:
        """Untere Karte: Änderungsprotokoll mit Filter nach Bereich."""
        card = Card("Änderungsprotokoll – wer hat was wann geändert?")
        self._audit_filter = QComboBox()
        self._audit_filter.setMinimumWidth(200)
        card.header.addWidget(muted_label("Bereich"))
        card.header.addWidget(self._audit_filter)

        self._audit_table: ObjectTable[AuditEntry] = ObjectTable(
            [
                Column("Zeitpunkt", lambda e: format_datetime(e.timestamp), lambda e: e.id or 0),
                Column("Benutzer", lambda e: e.user_name),
                Column("Bereich", lambda e: table_label(e.table_name)),
                Column("Datensatz", lambda e: f"#{e.record_id}" if e.record_id else "", lambda e: e.record_id, True),
                Column("Aktion", lambda e: e.action.label),
                Column("Details", describe_change),
            ]
        )
        card.body.addWidget(self._audit_table)
        self._audit_filter.currentIndexChanged.connect(lambda *_: self._load_audit())
        return card

    # ------------------------------------------------------------------ #
    # Daten laden
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Aktualisiert Immobilienliste, Abrechnung und Protokoll."""
        with ErrorGuard(self):
            self._period_bar.set_properties(self._context.properties.list_properties())
            tables = self._context.audit.list_table_names()
            fill_combo(self._audit_filter, [("Alle Bereiche", None), *[(table_label(t), t) for t in tables]])
        self._recalculate()
        self._load_audit()

    def _recalculate(self) -> None:
        """Berechnet die Abrechnung für die aktuelle Auswahl."""
        property_id = self._period_bar.property_id()
        period = self._period_bar.period()
        self._result = None
        if property_id is not None and period is not None:
            with ErrorGuard(self):
                self._result = self._context.billing.create_billing(property_id, period)

        statements = self._result.statements if self._result else []
        self._statement_table.set_items(statements)
        if self._result and self._result.warnings:
            self._hint_label.setText(
                f"Achtung: Die Abrechnung enthält {len(self._result.warnings)} Hinweis(e) - "
                "bitte vor dem Export im Tab 'Abrechnung' prüfen."
            )
        elif not statements:
            self._hint_label.setText("Für diese Auswahl gibt es keine Mieter im Abrechnungszeitraum.")
        else:
            self._hint_label.setText(
                "Vorauszahlungen laut den Raten im Tab 'Mieter'. * = Zeitraum von Hand angepasst. "
                "'Alle als PDF' schreibt ein Schreiben je Mieter in eine gemeinsame Datei."
            )
        self._update_buttons()

    def _load_audit(self) -> None:
        """Lädt das Änderungsprotokoll passend zum Bereichsfilter."""
        with ErrorGuard(self):
            self._audit_table.set_items(
                self._context.audit.list_entries(limit=_AUDIT_LIMIT, table_name=self._audit_filter.currentData())
            )

    def _update_buttons(self) -> None:
        has_statements = bool(self._result and self._result.statements)
        self._export_all_button.setEnabled(has_statements)
        self._export_selected_button.setEnabled(bool(self._statement_table.selected_items()))

    # ------------------------------------------------------------------ #
    # Export
    # ------------------------------------------------------------------ #
    def _export(self, statements: list[TenantStatement]) -> None:
        """Fragt den Speicherort ab, schreibt das PDF und öffnet es."""
        result = self._result
        if result is None or not statements:
            return

        if len(statements) == 1:
            suggestion = f"Nebenkostenabrechnung {result.period.end.year} {statements[0].tenant.name}.pdf"
        else:
            suggestion = f"Nebenkostenabrechnung {result.period.end.year} {result.property.name}.pdf"
        # Zeichen entfernen, die in Windows-Dateinamen verboten sind.
        suggestion = "".join("_" if char in '<>:"/\\|?*' else char for char in suggestion)

        file_name, _ = QFileDialog.getSaveFileName(
            self, "PDF speichern", str(Path.home() / "Documents" / suggestion), "PDF-Dateien (*.pdf)"
        )
        if not file_name:
            return
        target = Path(file_name)
        with ErrorGuard(self) as guard:
            self._context.pdf_export.export_statements(
                result, statements, self._context.settings.load_landlord(), target
            )
        if guard.ok:
            # Im Standard-PDF-Programm des Benutzers öffnen.
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))
