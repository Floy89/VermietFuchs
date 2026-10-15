"""Dialog zum Erfassen der Splits (Teilabschnitte) einer Rechnung - wahlweise mit Berechnung.

Je Abschnitt lassen sich Betrag und Verbrauch direkt eintragen **oder** von
der Anwendung ausrechnen:

* Verbrauch aus Abrechnungsvolumen × Zustandszahl × Brennwert (Gasrechnung),
* Betrag aus Arbeitspreis, Grundpreis und Umsatzsteuer.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QTableWidget,
    QVBoxLayout,
    QWidget,
)

from models.cost import InvoiceSplit, uncovered_periods, validate_splits
from models.exceptions import ValidationError
from models.tariff import SplitCalculation, fit_amounts
from models.value_objects import DateRange, Money
from ui import styles
from ui.helpers import make_button
from ui.widgets.common import DateEdit, DecimalSpin, MoneySpin, muted_label
from utils.formatters import format_money, format_period, format_quantity

(
    _COL_FROM,
    _COL_TO,
    _COL_VOLUME,
    _COL_CONDITION,
    _COL_CALORIFIC,
    _COL_CONSUMPTION,
    _COL_WORKING_PRICE,
    _COL_BASE_PRICE,
    _COL_AMOUNT,
) = range(9)

#: Preise werden von Versorgern mit bis zu acht Nachkommastellen angegeben.
_PRICE_DECIMALS = 8
_DEFAULT_VAT = Decimal(19)
#: Spalten, die nur für die Umrechnung von Gasvolumen in kWh gebraucht werden.
_VOLUME_COLUMNS = (_COL_VOLUME, _COL_CONDITION, _COL_CALORIFIC)


def _optional(value: Decimal) -> Decimal | None:
    """0 im Eingabefeld bedeutet "nicht angegeben"."""
    return value if value > 0 else None


class SplitsDialog(QDialog):
    """Tabelle der Splits mit Berechnung und laufender Summenprüfung.

    Der Dialog ändert nichts an der Rechnung selbst. Nach ``exec()`` stehen
    die geprüften Splits in :attr:`splits`. Hat der Benutzer die berechneten
    Summen als Rechnungssummen übernommen, stehen diese in
    :attr:`invoice_amount` und :attr:`invoice_consumption` - der aufrufende
    Dialog trägt sie in seine Felder ein.
    """

    def __init__(
        self,
        invoice_period: DateRange,
        invoice_amount: Money,
        invoice_consumption: Decimal | None,
        unit: str,
        splits: list[InvoiceSplit],
        parent: QWidget | None = None,
        show_volume: bool | None = None,
        default_vat: Decimal = _DEFAULT_VAT,
    ) -> None:
        """
        Args:
            show_volume: Spalten für Volumen, Zustandszahl und Brennwert zeigen
                (Gas: m³ -> kWh). ``None`` = nur, wenn die Rechnung in kWh
                abgerechnet wird oder ein Split schon solche Angaben hat.
                Bei Wasser, Strom usw. stören sie nur.
            default_vat: Vorgeschlagener Umsatzsteuersatz, solange kein Split
                einen eigenen hat (Trinkwasser 7 %, Gas und Strom 19 %).
            invoice_period: Rechnungszeitraum - Splits müssen darin liegen.
            invoice_amount: Gesamtbetrag der Rechnung (Soll der Split-Summe);
                0, wenn er sich erst aus der Berechnung ergeben soll.
            invoice_consumption: Gesamtverbrauch; ``None`` = (noch) nicht angegeben.
            unit: Einheit des Verbrauchs für die Anzeige.
            splits: Bereits vorhandene Splits.
        """
        super().__init__(parent)
        self._period = invoice_period
        self._unit = unit
        if show_volume is None:
            has_volume = any(
                s.calculation is not None and s.calculation.volume is not None for s in splits
            )
            show_volume = has_volume or unit.strip().casefold() == "kwh"
        self._show_volume = show_volume
        self._default_vat = default_vat
        self._updating = False
        #: Ungerundete Summe der aus Preisen berechneten Beträge (nur wenn alle Zeilen berechnet sind).
        self._computed_total: Decimal | None = None
        #: Gesamtbetrag der Rechnung; kann über "Summen übernehmen" neu gesetzt werden.
        self.invoice_amount = invoice_amount
        #: Gesamtverbrauch der Rechnung; kann über "Summen übernehmen" neu gesetzt werden.
        self.invoice_consumption = invoice_consumption
        #: Ergebnis nach Bestätigung.
        self.splits: list[InvoiceSplit] = list(splits)

        self.setWindowTitle("Splits und Berechnung der Rechnung")
        self.resize(1440, 560)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 16)
        layout.setSpacing(10)

        self._intro = QLabel()
        self._intro.setObjectName("Muted")
        self._intro.setWordWrap(True)
        layout.addWidget(self._intro)

        layout.addLayout(self._build_tariff_bar(splits))

        unit_label = unit or "Einheit"
        self._table = QTableWidget(0, 9)
        self._table.setHorizontalHeaderLabels(
            [
                "von",
                "bis",
                "Volumen (m³)",
                "Zustandszahl",
                "Brennwert",
                f"Verbrauch ({unit})" if unit else "Verbrauch",
                f"Arbeitspreis (€/{unit_label})",
                "Grundpreis (€/Jahr)",
                "Betrag (brutto)",
            ]
        )
        self._table.verticalHeader().setVisible(False)
        self._table.verticalHeader().setDefaultSectionSize(40)
        self._table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        for column in _VOLUME_COLUMNS:
            self._table.setColumnHidden(column, not self._show_volume)
        if not self._show_volume:
            self.resize(1180, 560)
        layout.addWidget(self._table, 1)
        volume_text = (
            "der Verbrauch aus Volumen × Zustandszahl × Brennwert (auf ganze Einheiten gerundet), "
            if self._show_volume
            else ""
        )
        layout.addWidget(
            muted_label(
                f"Grau = berechnet: {volume_text}der Betrag aus (Verbrauch × Arbeitspreis + Grundpreis × Tage "
                "des Abschnitts / Tage des Jahres) × (1 + USt). Preisfelder leer lassen (0), um den Betrag "
                "selbst einzutragen."
            )
        )

        self._status = QLabel()
        self._status.setWordWrap(True)
        layout.addWidget(self._status)

        add_button = make_button("Split hinzufügen", "plus")
        remove_button = make_button("Markierten entfernen", "trash", "danger")
        clear_button = make_button("Alle entfernen")
        self._adopt_button = make_button(
            "Summen als Rechnungssummen übernehmen",
            tooltip="Setzt Gesamtbetrag und Gesamtverbrauch der Rechnung auf die Summen der Splits",
        )
        self._ok_button = make_button("Übernehmen", variant="primary")
        cancel_button = make_button("Abbrechen")
        buttons = QHBoxLayout()
        for button in (add_button, remove_button, clear_button, self._adopt_button):
            buttons.addWidget(button)
        buttons.addStretch(1)
        buttons.addWidget(self._ok_button)
        buttons.addWidget(cancel_button)
        layout.addLayout(buttons)

        add_button.clicked.connect(lambda: self._add_row(None))
        remove_button.clicked.connect(self._remove_selected)
        clear_button.clicked.connect(self._clear)
        self._adopt_button.clicked.connect(self._adopt_sums)
        self._ok_button.clicked.connect(self._accept_if_valid)
        cancel_button.clicked.connect(self.reject)

        for split in splits:
            self._add_row(split)
        self._recalculate()

    # ------------------------------------------------------------------ #
    # Aufbau
    # ------------------------------------------------------------------ #
    def _build_tariff_bar(self, splits: list[InvoiceSplit]) -> QHBoxLayout:
        """Zeile mit den Preisen der Rechnung - Vorgabe für neue Abschnitte."""
        priced = next((s.calculation for s in splits if s.calculation and s.calculation.has_prices), None)
        with_vat = next((s.calculation for s in splits if s.calculation and s.calculation.vat_percent is not None), None)

        self._working_price_spin = DecimalSpin(decimals=_PRICE_DECIMALS, maximum=9999.0, suffix=" €")
        self._base_price_spin = DecimalSpin(decimals=_PRICE_DECIMALS, maximum=999_999.0, suffix=" €")
        self._vat_spin = DecimalSpin(decimals=1, maximum=100.0, suffix=" %")
        if priced is not None:
            self._working_price_spin.set_decimal(priced.working_price or Decimal(0))
            self._base_price_spin.set_decimal(priced.base_price or Decimal(0))
        self._vat_spin.set_decimal(
            with_vat.vat_percent if with_vat and with_vat.vat_percent is not None else self._default_vat
        )
        self._vat_spin.setToolTip("Laut Rechnung - z. B. Trinkwasser 7 %, Gas, Strom und Fernwärme 19 %")
        for spin in (self._working_price_spin, self._base_price_spin):
            spin.setMinimumWidth(150)
        apply_button = make_button(
            "Auf alle Abschnitte anwenden", tooltip="Trägt Arbeits- und Grundpreis in jede Zeile ein"
        )
        apply_button.clicked.connect(self._apply_prices_to_all)
        self._vat_spin.valueChanged.connect(lambda *_: self._recalculate())

        bar = QHBoxLayout()
        bar.setSpacing(8)
        unit_label = self._unit or "Einheit"
        for caption, widget in (
            (f"Arbeitspreis netto je {unit_label}", self._working_price_spin),
            ("Grundpreis netto je Jahr", self._base_price_spin),
            ("Umsatzsteuer", self._vat_spin),
        ):
            bar.addWidget(muted_label(caption))
            bar.addWidget(widget)
        bar.addWidget(apply_button)
        bar.addStretch(1)
        return bar

    def _update_intro(self) -> None:
        total_consumption = (
            f" · {format_quantity(self.invoice_consumption)} {self._unit}"
            if self.invoice_consumption is not None
            else ""
        )
        amount = format_money(self.invoice_amount) if not self.invoice_amount.is_zero else "Betrag noch offen"
        self._intro.setText(
            f"Rechnung {format_period(self._period)}: {amount}{total_consumption}\n"
            "Tragen Sie die Abschnitte laut Rechnung ein (z. B. je Ablesetermin). Mit Splits rechnet das "
            "Programm jeden Abschnitt dem Jahr zu, in dem er liegt. Ohne Splits wird die Rechnung "
            "gleichmäßig nach Tagen verteilt. "
            + (
                "Nennt die Rechnung nur Volumen und Preise, tragen Sie diese ein - Verbrauch und Betrag je "
                "Abschnitt werden berechnet."
                if self._show_volume
                else "Nennt die Rechnung Preise statt fertiger Beträge je Abschnitt, tragen Sie die Preise ein - "
                "der Betrag je Abschnitt wird berechnet."
            )
        )

    # ------------------------------------------------------------------ #
    # Zeilen
    # ------------------------------------------------------------------ #
    def _add_row(self, split: InvoiceSplit | None) -> None:
        """Fügt eine Zeile an; ohne Vorgabe schließt sie an den letzten Split an."""
        if split is None:
            current = self._read_splits()
            start = current[-1].period.end + timedelta(days=1) if current else self._period.start
            start = min(start, self._period.end)
            # Als Vorschlag den noch nicht verteilten Rest eintragen.
            rest_amount = self.invoice_amount - sum((s.amount for s in current), Money.zero())
            rest_consumption = None
            if self.invoice_consumption is not None:
                rest_consumption = self.invoice_consumption - sum(
                    (s.consumption or Decimal(0) for s in current), Decimal(0)
                )
            # Neue Zeilen übernehmen die Preise aus der Leiste oben.
            prices = SplitCalculation(
                working_price=_optional(self._working_price_spin.decimal_value()),
                base_price=_optional(self._base_price_spin.decimal_value()),
            )
            split = InvoiceSplit(
                DateRange(start, self._period.end),
                rest_amount if not rest_amount.is_negative else Money.zero(),
                max(rest_consumption, Decimal(0)) if rest_consumption is not None else None,
                None if prices.is_empty else prices,
            )

        calculation = split.calculation or SplitCalculation()
        row = self._table.rowCount()
        self._table.insertRow(row)
        from_edit, to_edit = DateEdit(split.period.start), DateEdit(split.period.end)
        volume_spin = DecimalSpin(decimals=3)
        condition_spin = DecimalSpin(decimals=4, maximum=10.0)
        calorific_spin = DecimalSpin(decimals=3, maximum=100.0)
        consumption_spin = DecimalSpin(decimals=3)
        working_price_spin = DecimalSpin(decimals=_PRICE_DECIMALS, maximum=9999.0)
        base_price_spin = DecimalSpin(decimals=_PRICE_DECIMALS, maximum=999_999.0)
        amount_spin = MoneySpin()

        volume_spin.set_decimal(calculation.volume or Decimal(0))
        condition_spin.set_decimal(calculation.condition_number or Decimal(0))
        calorific_spin.set_decimal(calculation.calorific_value or Decimal(0))
        consumption_spin.set_decimal(split.consumption or Decimal(0))
        working_price_spin.set_decimal(calculation.working_price or Decimal(0))
        base_price_spin.set_decimal(calculation.base_price or Decimal(0))
        amount_spin.set_money(split.amount)

        widgets = (
            from_edit, to_edit, volume_spin, condition_spin, calorific_spin,
            consumption_spin, working_price_spin, base_price_spin, amount_spin,
        )
        for column, widget in enumerate(widgets):
            if column not in (_COL_FROM, _COL_TO):
                widget.setSpecialValueText("–")  # 0 = nicht angegeben
            self._table.setCellWidget(row, column, widget)
        from_edit.dateChanged.connect(lambda *_: self._recalculate())
        to_edit.dateChanged.connect(lambda *_: self._recalculate())
        for spin in widgets[2:]:
            spin.valueChanged.connect(lambda *_: self._recalculate())
        self._table.selectRow(row)
        self._recalculate()

    def _remove_selected(self) -> None:
        row = self._table.currentRow()
        if row >= 0:
            self._table.removeRow(row)
            self._recalculate()

    def _clear(self) -> None:
        self._table.setRowCount(0)
        self._recalculate()

    def _spin(self, row: int, column: int) -> DecimalSpin:
        widget = self._table.cellWidget(row, column)
        assert isinstance(widget, DecimalSpin)
        return widget

    def _amount_spin(self, row: int) -> MoneySpin:
        widget = self._table.cellWidget(row, _COL_AMOUNT)
        assert isinstance(widget, MoneySpin)
        return widget

    def _row_period(self, row: int) -> DateRange | None:
        """Zeitraum einer Zeile; ``None``, wenn 'bis' vor 'von' liegt."""
        from_edit = self._table.cellWidget(row, _COL_FROM)
        to_edit = self._table.cellWidget(row, _COL_TO)
        assert isinstance(from_edit, DateEdit) and isinstance(to_edit, DateEdit)
        if to_edit.value() < from_edit.value():
            return None
        return DateRange(from_edit.value(), to_edit.value())

    def _row_calculation(self, row: int) -> SplitCalculation:
        """Die Rechengrundlagen einer Zeile (leere Felder als ``None``)."""
        has_prices = self._spin(row, _COL_WORKING_PRICE).value() > 0 or self._spin(row, _COL_BASE_PRICE).value() > 0
        return SplitCalculation(
            volume=_optional(self._spin(row, _COL_VOLUME).decimal_value()),
            condition_number=_optional(self._spin(row, _COL_CONDITION).decimal_value()),
            calorific_value=_optional(self._spin(row, _COL_CALORIFIC).decimal_value()),
            working_price=_optional(self._spin(row, _COL_WORKING_PRICE).decimal_value()),
            base_price=_optional(self._spin(row, _COL_BASE_PRICE).decimal_value()),
            # Die Umsatzsteuer gilt für die ganze Rechnung, zählt aber nur mit Preisen.
            vat_percent=self._vat_spin.decimal_value() if has_prices else None,
        )

    def _read_splits(self) -> list[InvoiceSplit]:
        """Liest die Zeilen aus. Zeilen mit Ende vor Beginn werden übersprungen (und gemeldet)."""
        result: list[InvoiceSplit] = []
        for row in range(self._table.rowCount()):
            period = self._row_period(row)
            if period is None:
                continue
            calculation = self._row_calculation(row)
            consumption = self._spin(row, _COL_CONSUMPTION).decimal_value()
            # Ohne Gesamtverbrauch und ohne Eintrag hat der Split keinen Verbrauch.
            has_consumption = self.invoice_consumption is not None or consumption > 0
            result.append(
                InvoiceSplit(
                    period,
                    self._amount_spin(row).money(),
                    consumption if has_consumption else None,
                    None if calculation.is_empty else calculation,
                )
            )
        return result

    # ------------------------------------------------------------------ #
    # Berechnung
    # ------------------------------------------------------------------ #
    def _apply_prices_to_all(self) -> None:
        """Trägt die Preise aus der Leiste in jede Zeile ein."""
        self._updating = True
        for row in range(self._table.rowCount()):
            self._spin(row, _COL_WORKING_PRICE).set_decimal(self._working_price_spin.decimal_value())
            self._spin(row, _COL_BASE_PRICE).set_decimal(self._base_price_spin.decimal_value())
        self._updating = False
        self._recalculate()

    def _recalculate(self) -> None:
        """Rechnet Verbrauch und Betrag der Zeilen neu, für die Rechengrundlagen eingetragen sind."""
        if self._updating:
            return
        self._updating = True
        try:
            priced_rows: list[int] = []
            exact_amounts: list[Decimal] = []
            for row in range(self._table.rowCount()):
                calculation = self._row_calculation(row)
                consumption_spin = self._spin(row, _COL_CONSUMPTION)
                energy = calculation.energy()
                if energy is not None:
                    consumption_spin.set_decimal(energy)
                self._mark_computed(consumption_spin, energy is not None)

                period = self._row_period(row)
                exact = (
                    calculation.gross_cost(period, consumption_spin.decimal_value()) if period is not None else None
                )
                self._mark_computed(self._amount_spin(row), exact is not None)
                if exact is not None:
                    priced_rows.append(row)
                    exact_amounts.append(exact)

            # An den Rechnungsbetrag anpassen geht nur, wenn *alle* Zeilen berechnet sind.
            all_priced = len(priced_rows) == self._table.rowCount()
            amounts = fit_amounts(exact_amounts, self.invoice_amount if all_priced else None)
            for row, amount in zip(priced_rows, amounts):
                self._amount_spin(row).set_money(amount)
            self._computed_total = sum(exact_amounts, Decimal(0)) if all_priced and priced_rows else None
        finally:
            self._updating = False
        self._update_status()

    @staticmethod
    def _mark_computed(spin: DecimalSpin | MoneySpin, computed: bool) -> None:
        """Berechnete Felder sind gesperrt und grau - der Wert kommt aus den Rechengrundlagen."""
        spin.setReadOnly(computed)
        spin.setStyleSheet(f"color: {styles.color('muted')};" if computed else "")
        spin.setFocusPolicy(Qt.FocusPolicy.NoFocus if computed else Qt.FocusPolicy.StrongFocus)

    def _adopt_sums(self) -> None:
        """Setzt Gesamtbetrag und -verbrauch der Rechnung auf die Summen der Splits."""
        splits = self._read_splits()
        if not splits:
            return
        self.invoice_amount = sum((s.amount for s in splits), Money.zero())
        consumptions = [s.consumption for s in splits if s.consumption is not None]
        self.invoice_consumption = sum(consumptions, Decimal(0)) if consumptions else None
        self._recalculate()

    # ------------------------------------------------------------------ #
    # Prüfung
    # ------------------------------------------------------------------ #
    def _check(self) -> str | None:
        """Gibt die Fehlermeldung der Prüfung zurück oder ``None``, wenn alles passt."""
        splits = self._read_splits()
        if len(splits) != self._table.rowCount():
            return "Bei mindestens einem Split liegt 'bis' vor 'von'."
        try:
            validate_splits(splits, self._period, self.invoice_amount, self.invoice_consumption)
        except ValidationError as exc:
            return str(exc)
        return None

    def _update_status(self) -> None:
        """Zeigt die laufenden Summen und das Ergebnis der Prüfung farbig an."""
        self._update_intro()
        splits = self._read_splits()
        self._adopt_button.setEnabled(bool(splits))
        if self._table.rowCount() == 0:
            self._status.setText("Keine Splits – die Rechnung wird taggenau linear verteilt.")
            self._status.setStyleSheet(f"color: {styles.color('muted')};")
            return

        amount_sum = sum((s.amount for s in splits), Money.zero())
        text = f"Summe Beträge: {format_money(amount_sum)} von {format_money(self.invoice_amount)}"
        consumption_sum = sum((s.consumption or Decimal(0) for s in splits), Decimal(0))
        if self.invoice_consumption is not None:
            text += (
                f"   ·   Summe Verbrauch: {format_quantity(consumption_sum)} von "
                f"{format_quantity(self.invoice_consumption)} {self._unit}"
            )
        elif consumption_sum > 0:
            text += f"   ·   Summe Verbrauch: {format_quantity(consumption_sum)} {self._unit}"

        computed = self._computed_total
        if computed is not None:
            computed_money = Money.from_decimal(computed)
            difference = self.invoice_amount - computed_money
            text += f"\nAus den Preisen berechnet: {format_money(computed_money)}"
            if not self.invoice_amount.is_zero and not difference.is_zero and amount_sum == self.invoice_amount:
                text += (
                    f" – {format_money(abs(difference))} Rundungsdifferenz zur Rechnung, "
                    "auf den Rechnungsbetrag verteilt"
                )

        error = self._check()
        # Nicht abgedeckte Tage immer nennen - auch wenn die Summen (noch) nicht
        # stimmen: Eine Lücke von einem Tag erklärt oft einen Teil der Differenz.
        gaps = uncovered_periods(splits, self._period)
        gap_text = ""
        if gaps:
            listed = ", ".join(format_period(gap) for gap in gaps)
            gap_text = f"\n⚠ Nicht abgedeckt: {listed}. Für diese Tage werden keine Kosten berechnet."
        if error is not None and computed is not None and not self.invoice_amount.is_zero:
            error += " Bitte Umsatzsteuersatz, Preise und die Zeiträume der Abschnitte mit der Rechnung vergleichen."
        if error is None and gaps:
            # Summen stimmen, aber nicht alle Tage der Rechnung gehören zu einem
            # Split. Erlaubt, jedoch meist ein Tippfehler im Datum -> hinweisen.
            self._status.setText(f"✓ {text}{gap_text}")
            self._status.setStyleSheet(f"color: {styles.color('warning_text')}; font-weight: 600;")
        elif error is None:
            self._status.setText(f"✓ {text}")
            self._status.setStyleSheet(f"color: {styles.color('success')}; font-weight: 600;")
        else:
            self._status.setText(f"{text}\n{error}{gap_text}")
            self._status.setStyleSheet(f"color: {styles.color('danger')}; font-weight: 600;")

    def _accept_if_valid(self) -> None:
        """Übernimmt die Splits nur, wenn die Prüfung bestanden ist."""
        if self._check() is not None:
            self._update_status()
            return
        self.splits = sorted(self._read_splits(), key=lambda split: split.period.start)
        self.accept()
