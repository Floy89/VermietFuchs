"""Kautionsabrechnung eines Mieters: Entwicklung mit Zinsen und Rückzahlungsbetrag."""

from __future__ import annotations

from datetime import date, timedelta

from PyQt6.QtWidgets import QDialog, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from models.deposit import DepositYear
from models.tenant import Tenant
from ui.helpers import make_button
from ui.widgets.common import Column, DateEdit, ObjectTable, muted_label
from utils.formatters import format_date, format_money, format_period


class DepositDialog(QDialog):
    """Zeigt, wie sich die Kaution mit Zinsen entwickelt hat und was bei Auszug zurückzuzahlen ist."""

    def __init__(self, tenant: Tenant, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._tenant = tenant
        self.setWindowTitle(f"Kaution – {tenant.name}")
        self.resize(720, 460)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        layout.addWidget(
            muted_label(
                f"Kaution {format_money(tenant.deposit_amount)}, eingezahlt am {format_date(tenant.deposit_start)}. "
                "Die Zinsen werden taggenau berechnet und jeweils zum Jahresende gutgeschrieben; sie erhöhen "
                "die Kaution (§ 551 Abs. 3 BGB)."
            )
        )

        until_row = QHBoxLayout()
        until_row.addWidget(QLabel("Rückzahlung zum"))
        default_until = tenant.end_date + timedelta(days=1) if tenant.end_date else date.today()
        self._until_edit = DateEdit(default_until)
        self._until_edit.setToolTip(
            "Stichtag der Rückzahlung. Bei einem Vertragsende ist das der Tag danach - der letzte Vertragstag "
            "zählt noch als Zinstag."
        )
        until_row.addWidget(self._until_edit)
        until_row.addStretch(1)
        layout.addLayout(until_row)

        self._table: ObjectTable[DepositYear] = ObjectTable(
            [
                Column("Zeitraum", lambda y: format_period(y.period)),
                Column("Zinssatz", lambda y: y.rates),
                Column("Stand zu Beginn", lambda y: format_money(y.start_balance), lambda y: y.start_balance.cents, True),
                Column("Zinsen", lambda y: format_money(y.interest), lambda y: y.interest.cents, True),
                Column("Stand am Ende", lambda y: format_money(y.end_balance), lambda y: y.end_balance.cents, True),
            ],
            stretch_column=1,
        )
        layout.addWidget(self._table, 1)

        self._result_label = QLabel()
        self._result_label.setWordWrap(True)
        layout.addWidget(self._result_label)
        layout.addWidget(
            muted_label(
                "Ohne Abzüge: Offene Forderungen (z. B. eine Nachzahlung aus der Nebenkostenabrechnung oder "
                "Schäden) dürfen verrechnet werden. Hat die Bank Kapitalertragsteuer auf die Zinsen einbehalten, "
                "ist der ausgezahlte Betrag entsprechend niedriger."
            )
        )

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = make_button("Schließen")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._until_edit.dateChanged.connect(lambda *_: self._recalculate())
        self._recalculate()

    def _recalculate(self) -> None:
        """Rechnet die Kaution zum gewählten Stichtag neu."""
        until = self._until_edit.value()
        if until <= self._tenant.deposit_start:
            self._table.set_items([])
            self._result_label.setText("Der Stichtag liegt vor der Einzahlung der Kaution.")
            return
        statement = self._tenant.deposit_statement(until)
        self._table.set_items(statement.years)
        self._result_label.setText(
            f"<b>Rückzahlung zum {format_date(until)}: {format_money(statement.payout)}</b> "
            f"(Kaution {format_money(statement.amount)} + Zinsen {format_money(statement.interest)})"
        )
