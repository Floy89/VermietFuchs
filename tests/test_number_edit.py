"""Tests für die Zahlenfelder: Was man tippt, muss auch ankommen.

Vorher (Drehfelder von Qt) wurde aus getipptem "12,5" je nach Cursorposition
gar nichts oder 1.250 €, ein leeres Feld ("–") ließ sich nicht beschreiben und
"62.5" wurde zu 6.250.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from models.value_objects import Money
from ui.widgets.common import DecimalSpin, MoneySpin
from ui.widgets.number_edit import NumberEdit, format_number, parse_number


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("12,5", "12.5"),
            ("12.5", "12.5"),             # Punkt als Dezimaltrenner
            ("1,9829", "1.9829"),
            ("1.9829", "1.9829"),
            ("1.234,56", "1234.56"),      # deutsche Schreibweise mit Tausenderpunkt
            ("1.500", "1500"),            # genau drei Ziffern nach dem Punkt: Tausenderpunkt
            ("17.980", "17980"),          # wie auf Rechnungen und Zählern
            ("1.234.567", "1234567"),
            ("153,00 €", "153.00"),
            ("  290 m³ ", "290"),
            ("0", "0"),
            ("-12,5", "-12.5"),
        ],
    )
    def test_readable_input(self, text: str, expected: str) -> None:
        assert parse_number(text) == (Decimal(expected), True)

    @pytest.mark.parametrize("text", ["abc", "1,2,3", "12-5", ",", "-"])
    def test_unreadable_input(self, text: str) -> None:
        assert parse_number(text) == (None, False)

    def test_empty_input(self) -> None:
        assert parse_number("") == (None, True)
        assert parse_number("  ") == (None, True)
        assert parse_number(" € ") == (None, True)

    def test_formatting(self) -> None:
        assert format_number(Decimal("1234.5"), 2, trim_zeros=False) == "1.234,50"
        assert format_number(Decimal("1.98290000"), 8, trim_zeros=True) == "1,9829"
        assert format_number(Decimal("62"), 2, trim_zeros=True) == "62"
        assert format_number(Decimal("1234.5"), 2, trim_zeros=False, grouped=False) == "1234,50"


def _type_into(widget: NumberEdit, keys: str, click_at_end: bool = True) -> None:
    """Klickt wie mit der Maus ins Feld, tippt und verlässt es mit Tab."""
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.addWidget(widget)
    layout.addWidget(QLineEdit())  # Ziel für den Tab
    host.show()
    QApplication.processEvents()
    x = widget.width() - 5 if click_at_end else 5
    QTest.mouseClick(widget, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier, QPoint(x, widget.height() // 2))
    QApplication.processEvents()
    QApplication.processEvents()  # die Markierung nach dem Klick
    QTest.keyClicks(widget, keys)
    QTest.keyClick(widget, Qt.Key.Key_Tab)
    QApplication.processEvents()
    widget.setParent(None)
    host.deleteLater()


class TestTyping:
    @pytest.mark.parametrize("click_at_end", [True, False])
    def test_typing_replaces_the_old_amount_wherever_one_clicks(self, qt_app: QApplication, click_at_end: bool) -> None:
        field = MoneySpin()
        field.set_money(Money.from_decimal("787.08"))

        _type_into(field, "12,5", click_at_end)

        assert field.money() == Money.from_decimal("12.50")
        assert field.text() == "12,50 €"

    def test_empty_field_can_be_filled(self, qt_app: QApplication) -> None:
        field = DecimalSpin(decimals=3)
        field.setSpecialValueText("–")
        assert field.text() == "" and field.placeholderText() == "–"

        _type_into(field, "75")

        assert field.decimal_value() == Decimal("75.000")

    def test_dot_and_comma_both_work_for_prices(self, qt_app: QApplication) -> None:
        with_comma, with_dot = DecimalSpin(decimals=8), DecimalSpin(decimals=8)

        _type_into(with_comma, "1,9829")
        _type_into(with_dot, "1.9829")

        assert with_comma.decimal_value() == with_dot.decimal_value() == Decimal("1.9829")
        assert with_dot.text() == "1,9829"

    def test_values_are_live_while_typing(self, qt_app: QApplication) -> None:
        field = MoneySpin()
        seen: list[float] = []
        field.valueChanged.connect(seen.append)

        _type_into(field, "45")

        assert seen == [4.0, 45.0]

    def test_unreadable_input_keeps_the_last_value(self, qt_app: QApplication) -> None:
        field = MoneySpin()
        field.set_money(Money.from_decimal("50"))

        _type_into(field, "abc")

        assert field.money() == Money.from_decimal("50") and field.text() == "50,00 €"

    def test_range_is_enforced_when_leaving(self, qt_app: QApplication) -> None:
        percent = DecimalSpin(decimals=1, maximum=100.0)
        negative = MoneySpin()

        _type_into(percent, "250")
        _type_into(negative, "-5")

        assert percent.decimal_value() == Decimal("100.0")
        assert negative.money() == Money.zero()

    def test_read_only_field_is_not_reformatted_for_editing(self, qt_app: QApplication) -> None:
        field = MoneySpin()
        field.set_money(Money.from_decimal("1234.5"))
        field.setReadOnly(True)

        _type_into(field, "99")

        assert field.money() == Money.from_decimal("1234.50") and field.text() == "1.234,50 €"

    def test_programmatic_values_and_suffix(self, qt_app: QApplication) -> None:
        field = DecimalSpin(decimals=3, suffix=" m³")
        seen: list[float] = []
        field.valueChanged.connect(seen.append)

        field.set_decimal(Decimal("17980"))
        field.set_decimal(Decimal("17980"))  # unverändert: kein zweites Signal
        field.setSuffix(" kWh")

        assert field.text() == "17.980 kWh"
        assert seen == [17980.0]
        assert field.value() == 17980.0 and field.lineEdit() is field
