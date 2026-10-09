"""Tab 6 - Abrechnung: Verteilung der Kosten auf die Mietverhältnisse."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSplitter, QVBoxLayout, QWidget

from app_context import AppContext
from models.billing import BillingResult, CostShare, TenantStatement
from models.cost import Cost
from models.value_objects import DateRange, Money
from ui.helpers import ErrorGuard, make_button
from ui.widgets.common import (
    BaseTab,
    Card,
    Column,
    DateEdit,
    ObjectTable,
    PeriodBar,
    muted_label,
    page_title,
)
from utils.formatters import (
    format_money,
    format_period,
    format_period_short,
    format_piece_suffix,
    format_share_basis,
    format_share_title,
)


def balance_text(statement: TenantStatement) -> str:
    """Saldo als Text: "Guthaben 12,34 €" bzw. "Nachzahlung 12,34 €"."""
    if statement.balance.is_zero:
        return "ausgeglichen"
    label = "Guthaben" if statement.is_credit else "Nachzahlung"
    return f"{label} {format_money(abs(statement.balance))}"


def _whole_euro(money: Money) -> str:
    """Betrag ohne Nachkommastellen, z. B. "550 €" (Empfehlungen sind immer volle Zehner)."""
    return format_money(money).replace(",00", "")


def recommendation_text(statement: TenantStatement, short: bool = False) -> str:
    """Empfohlene monatliche Vorauszahlung samt Änderung gegenüber heute.

    Args:
        short: Kurzform für schmale Spalten ("550 € (+50 €)") statt
            "550 € (bisher 500 €, +50 €)".
    """
    adjustment = statement.prepayment_adjustment
    if adjustment is None or statement.current_rate is None:
        return "– (ausgezogen)"
    recommended = _whole_euro(statement.recommended_prepayment)
    if adjustment.is_zero:
        return f"{recommended} (passt)"
    change = f"{'+' if adjustment.cents > 0 else '−'}{_whole_euro(abs(adjustment))}"
    if short:
        return f"{recommended} ({change})"
    return f"{recommended} (bisher {_whole_euro(statement.current_rate)}, {change})"


def recommendation_color(statement: TenantStatement) -> str | None:
    """Farbe der Empfehlung: Erhöhung rot, Senkung grün, sonst neutral."""
    adjustment = statement.prepayment_adjustment
    if adjustment is None or adjustment.is_zero:
        return None
    return "danger" if adjustment.cents > 0 else "success"


RECOMMENDATION_TOOLTIP = (
    "Kostendeckende monatliche Vorauszahlung: Kostenanteil dieses Abrechnungszeitraums, auf ein Jahr "
    "hochgerechnet, durch zwölf geteilt und auf volle 10 € gerundet - verglichen mit der heute gültigen Rate."
)


def period_text(statement: TenantStatement) -> str:
    """Abgerechneter Zeitraum; ein Stern markiert eine Anpassung von Hand."""
    marker = " *" if statement.is_adjusted else ""
    return f"{format_period_short(statement.occupancy)}{marker}"


class BillingTab(BaseTab):
    """Oben die Kosten des Zeitraums, unten das automatisch berechnete Ergebnis.

    Die Kosten kommen aus dem Tab "Rechnungen". Jede Änderung an Immobilie,
    Zeitraum oder am Abrechnungszeitraum eines Mietverhältnisses löst sofort
    eine Neuberechnung aus - es gibt keinen "Berechnen"-Knopf.
    """

    def __init__(self, context: AppContext, parent: QWidget | None = None) -> None:
        super().__init__(context, parent)
        self._result: BillingResult | None = None

        self.root.addWidget(page_title("Abrechnung"))
        self._period_bar = PeriodBar()
        self._period_bar.changed.connect(self._recalculate)
        self.root.addWidget(self._period_bar)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_cost_card())
        splitter.addWidget(self._build_result_card())
        splitter.setSizes([260, 440])
        self.root.addWidget(splitter, 1)

    # ------------------------------------------------------------------ #
    # Aufbau
    # ------------------------------------------------------------------ #
    def _build_cost_card(self) -> Card:
        """Obere Karte: die Kosten, die in diese Abrechnung eingehen (nur Anzeige)."""
        card = Card("Kosten im Abrechnungszeitraum")
        source_hint = QLabel("Erfassen und ausschließen im Tab „Rechnungen“")
        source_hint.setObjectName("Muted")
        card.header.addWidget(source_hint)

        self._cost_table: ObjectTable[Cost] = ObjectTable(
            [
                Column("Kostenart", lambda c: c.cost_type.label),
                Column(
                    "Beschreibung",
                    lambda c: c.description + (format_piece_suffix(c) if c.split_period else ""),
                ),
                Column(
                    "Rechnungszeitraum",
                    lambda c: format_period(c.full_period or c.period),
                    lambda c: (c.full_period or c.period).start.isoformat(),
                ),
                Column("Umlageschlüssel", lambda c: c.allocation_label),
                Column(
                    "Rechnungsbetrag",
                    lambda c: format_money(c.full_amount or c.amount),
                    lambda c: (c.full_amount or c.amount).cents,
                    True,
                ),
                Column(
                    "davon im Zeitraum",
                    lambda c: format_money(c.amount),
                    lambda c: c.amount.cents,
                    True,
                    # Farblich hervorheben, wenn nur ein Teil der Rechnung zählt.
                    lambda c: "primary" if c.is_prorated else None,
                ),
                Column(
                    "umgelegt",
                    lambda c: format_money(self._allocated(c)),
                    lambda c: self._allocated(c).cents,
                    True,
                    # Rot, wenn (durch abweichende Schlüssel) mehr umgelegt wird als angefallen ist.
                    lambda c: "danger" if self._allocated(c) > c.amount else None,
                ),
            ],
            stretch_column=1,
        )
        card.body.addWidget(self._cost_table)
        return card

    def _build_result_card(self) -> Card:
        """Untere Karte: Summen, Hinweise, Mietverhältnisse und Detailaufstellung."""
        card = Card("Abrechnungsergebnis")
        self._summary_label = QLabel()
        self._summary_label.setTextFormat(Qt.TextFormat.RichText)
        card.body.addWidget(self._summary_label)

        self._warning_label = QLabel()
        self._warning_label.setObjectName("WarningBox")
        self._warning_label.setWordWrap(True)
        self._warning_label.hide()
        card.body.addWidget(self._warning_label)

        self._statement_table: ObjectTable[TenantStatement] = ObjectTable(
            [
                Column("Wohnung · Mieter", lambda s: f"{s.apartment.number} · {s.tenant.name}"),
                # Kurze Überschriften: Sechs Spalten müssen neben die Aufstellung passen.
                Column(
                    "Zeitraum",
                    period_text,
                    lambda s: s.occupancy.start.isoformat(),
                    color=lambda s: "primary" if s.is_adjusted else None,
                ),
                Column("Kosten", lambda s: format_money(s.total_costs), lambda s: s.total_costs.cents, True),
                Column("Vorausz.", lambda s: format_money(s.prepayments), lambda s: s.prepayments.cents, True),
                Column(
                    "Ergebnis",
                    balance_text,
                    lambda s: s.balance.cents,
                    True,
                    lambda s: None if s.balance.is_zero else "success" if s.is_credit else "danger",
                ),
                Column(
                    "Empf. Vorausz.",
                    lambda s: recommendation_text(s, short=True),
                    lambda s: s.recommended_prepayment.cents,
                    True,
                    recommendation_color,
                ),
            ],
            stretch_column=0,
        )
        self._statement_table.horizontalHeader().setToolTip(RECOMMENDATION_TOOLTIP)
        self._detail_title = muted_label("Aufstellung")
        self._detail_table: ObjectTable[CostShare] = ObjectTable(
            [
                Column("Kostenart", format_share_title),
                Column("Gesamt", lambda s: format_money(s.component_amount), lambda s: s.component_amount.cents, True),
                Column("Verteilung", format_share_basis),
                Column("Anteil", lambda s: format_money(s.amount), lambda s: s.amount.cents, True),
            ],
            stretch_column=0,
            # Kostenart und Verteilung können lang werden (Splits, Verbrauchsdifferenz):
            # Spaltenbreite begrenzen und die Zeilen umbrechen statt abzuschneiden.
            max_column_width=215,
        )
        self._detail_table.setWordWrap(True)

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(muted_label("Mietverhältnisse im Abrechnungszeitraum"))
        left_layout.addWidget(self._statement_table, 1)
        left_layout.addLayout(self._build_period_editor())

        right = QWidget()
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.addWidget(self._detail_title)
        right_layout.addWidget(self._detail_table)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(left)
        splitter.addWidget(right)
        splitter.setSizes([840, 540])
        card.body.addWidget(splitter, 1)

        self._statement_table.selection_changed.connect(self._on_statement_selected)
        return card

    def _build_period_editor(self) -> QHBoxLayout:
        """Zeile unter der Liste: Abrechnungszeitraum des markierten Mietverhältnisses.

        Vorgeschlagen wird der Vertragszeitraum innerhalb des Abrechnungsjahres
        (Einzug/Auszug). Der Benutzer kann ihn einschränken, z. B. für eine
        Teilabrechnung, und jederzeit zum Vorschlag zurückkehren.
        """
        self._billed_from = DateEdit()
        self._billed_to = DateEdit()
        self._apply_period_button = make_button("Übernehmen", variant="primary")
        self._reset_period_button = make_button("Zurücksetzen", tooltip="Wieder den Vertragszeitraum verwenden")
        self._period_hint = muted_label()

        row = QHBoxLayout()
        caption = QLabel("Zeitraum:")
        caption.setObjectName("Muted")
        row.addWidget(caption)
        row.addWidget(self._billed_from)
        until = QLabel("bis")
        until.setObjectName("Muted")
        row.addWidget(until)
        row.addWidget(self._billed_to)
        row.addWidget(self._apply_period_button)
        row.addWidget(self._reset_period_button)
        row.addWidget(self._period_hint, 1)

        self._apply_period_button.clicked.connect(self._apply_billed_period)
        self._reset_period_button.clicked.connect(self._reset_billed_period)
        return row

    def _allocated(self, cost: Cost) -> Money:
        """Summe der Mieteranteile an einer Kostenposition der aktuellen Abrechnung."""
        return self._result.allocated_for(cost) if self._result else Money.zero()

    # ------------------------------------------------------------------ #
    # Daten laden und berechnen
    # ------------------------------------------------------------------ #
    def refresh(self) -> None:
        """Aktualisiert die Immobilienliste und rechnet neu."""
        with ErrorGuard(self):
            self._period_bar.set_properties(self._context.properties.list_properties())
        self._recalculate()

    def _recalculate(self) -> None:
        """Berechnet die Abrechnung für die aktuelle Auswahl."""
        property_id = self._period_bar.property_id()
        period = self._period_bar.period()
        selected = self._statement_table.selected_item()
        selected_tenant_id = selected.tenant.id if selected else None
        self._result = None

        if property_id is None or period is None:
            self._cost_table.set_items([])
            self._statement_table.set_items([])
            self._summary_label.setText(
                "Bitte zuerst eine Immobilie anlegen." if property_id is None else "Der Zeitraum ist ungültig (Bis liegt vor Von)."
            )
            self._warning_label.hide()
            self._on_statement_selected()
            return

        with ErrorGuard(self):
            self._result = self._context.billing.create_billing(property_id, period)
        result = self._result
        if result is None:
            return

        self._cost_table.set_items(result.costs)
        self._summary_label.setText(
            f"Gesamtkosten <b>{format_money(result.total_costs)}</b> &nbsp;·&nbsp; "
            f"auf Mieter umgelegt <b>{format_money(result.total_allocated)}</b> &nbsp;·&nbsp; "
            f"{self._unallocated_text(result)} &nbsp;·&nbsp; "
            f"Vorauszahlungen <b>{format_money(result.total_prepayments)}</b>"
        )
        if result.warnings:
            self._warning_label.setText("Hinweise:\n• " + "\n• ".join(result.warnings))
            self._warning_label.show()
        else:
            self._warning_label.hide()

        self._statement_table.set_items(result.statements)
        if not self._statement_table.select_where(lambda s: s.tenant.id == selected_tenant_id):
            self._statement_table.select_first()
        self._on_statement_selected()

    @staticmethod
    def _unallocated_text(result: BillingResult) -> str:
        """Beschreibt, was nicht (oder zu viel) auf die Mieter umgelegt wird."""
        text = f"nicht umgelegt (Leerstand/Eigentümer) <b>{format_money(result.vacancy_amount)}</b>"
        if not result.deviation_amount.is_zero:
            direction = "weniger" if result.deviation_amount.cents > 0 else "mehr"
            text += (
                f" &nbsp;·&nbsp; durch abweichende Schlüssel <b>{format_money(abs(result.deviation_amount))} "
                f"{direction}</b> umgelegt"
            )
        return text

    def _on_statement_selected(self) -> None:
        """Zeigt Aufstellung und Abrechnungszeitraum des markierten Mietverhältnisses."""
        statement = self._statement_table.selected_item()
        has_statement = statement is not None
        for widget in (self._billed_from, self._billed_to, self._apply_period_button):
            widget.setEnabled(has_statement)
        self._reset_period_button.setEnabled(has_statement and statement.is_adjusted)

        if statement is None:
            self._detail_title.setText("Aufstellung")
            self._detail_table.set_items([])
            self._period_hint.setText("")
            return

        self._billed_from.set_value(statement.occupancy.start)
        self._billed_to.set_value(statement.occupancy.end)
        self._period_hint.setText(
            f"* angepasst (Vertrag: {format_period(statement.contract_occupancy)})"
            if statement.is_adjusted
            else "laut Mietvertrag"
        )
        rates = ", ".join(format_money(rate.amount) for rate in statement.prepayment_rates) or "keine"
        self._detail_title.setText(
            f"{statement.tenant.name} · {statement.occupancy.days} Tage · Vorauszahlungsrate(n): {rates} · "
            f"Kosten je Monat {format_money(statement.monthly_costs)} → empfohlene Vorauszahlung "
            f"{recommendation_text(statement)}"
        )
        self._detail_title.setToolTip(RECOMMENDATION_TOOLTIP)
        self._detail_table.set_items(statement.shares)
        self._detail_table.resizeRowsToContents()

    # ------------------------------------------------------------------ #
    # Aktionen
    # ------------------------------------------------------------------ #
    def _apply_billed_period(self) -> None:
        """Speichert den eingestellten Zeitraum für das markierte Mietverhältnis."""
        statement = self._statement_table.selected_item()
        period = self._period_bar.period()
        if statement is None or statement.tenant.id is None or period is None:
            return
        start, end = self._billed_from.value(), self._billed_to.value()
        with ErrorGuard(self) as guard:
            billed = DateRange(start, end)  # wirft bei Ende vor Beginn eine verständliche Meldung
            if billed == statement.contract_occupancy:
                # Entspricht dem Vorschlag -> keine Anpassung speichern.
                self._context.billing.reset_billed_period(statement.tenant.id, period)
            else:
                self._context.billing.set_billed_period(statement.tenant.id, period, billed)
        if guard.ok:
            self._recalculate()

    def _reset_billed_period(self) -> None:
        """Verwirft die Anpassung - es gilt wieder der Vertragszeitraum."""
        statement = self._statement_table.selected_item()
        period = self._period_bar.period()
        if statement is None or statement.tenant.id is None or period is None:
            return
        with ErrorGuard(self):
            self._context.billing.reset_billed_period(statement.tenant.id, period)
        self._recalculate()
