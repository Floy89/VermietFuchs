"""Tests für das Datumsfeld: Getippte Daten müssen vollständig ankommen.

Vorher (QDateEdit) wurde aus getipptem "31.12.2025" der 01.12.2025, und
"31122025" oder ein neues Jahr wurden gar nicht übernommen.
"""

from __future__ import annotations

from datetime import date

import pytest
from PyQt6.QtCore import QDate, QPoint, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLineEdit, QVBoxLayout, QWidget

from ui.widgets.common import DateEdit
from ui.widgets.date_edit import parse_date


class TestParsing:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("31.12.2025", date(2025, 12, 31)),
            ("31.12.25", date(2025, 12, 31)),
            ("3.1.2026", date(2026, 1, 3)),
            ("31122025", date(2025, 12, 31)),
            ("311225", date(2025, 12, 31)),
            ("2025-12-31", date(2025, 12, 31)),
            ("13.09.", date(2024, 9, 13)),  # ohne Jahr: das bisherige Jahr
            (" 01.01.2026 ", date(2026, 1, 1)),
        ],
    )
    def test_readable(self, text: str, expected: date) -> None:
        assert parse_date(text, fallback_year=2024) == expected

    @pytest.mark.parametrize("text", ["31.02.2025", "32.01.2025", "morgen", "", "1.2", "12345"])
    def test_unreadable(self, text: str) -> None:
        assert parse_date(text, fallback_year=2024) is None

    def test_short_forms_wait_until_typing_is_done(self) -> None:
        # Unterwegs zu "31.12.2025" darf nicht kurz der 31.12.2020 entstehen.
        assert parse_date("31.12.20", 2024, allow_short_year=False) is None
        assert parse_date("31.12.", 2024, allow_short_year=False) is None
        assert parse_date("31.12.2025", 2024, allow_short_year=False) == date(2025, 12, 31)


def _type_into(field: DateEdit, keys: str, x_fraction: float = 0.1) -> list[str]:
    """Klickt ins Feld, tippt, verlässt es mit Tab; gibt die gemeldeten Daten zurück."""
    seen: list[str] = []
    field.dateChanged.connect(lambda value: seen.append(value.toString("dd.MM.yyyy")))
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.addWidget(field)
    layout.addWidget(QLineEdit())
    host.show()
    QApplication.processEvents()
    QTest.mouseClick(field, Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(int(field.width() * x_fraction), field.height() // 2))
    QApplication.processEvents()
    QApplication.processEvents()
    focus = QApplication.focusWidget()
    assert focus is field
    QTest.keyClicks(focus, keys)
    QTest.keyClick(focus, Qt.Key.Key_Tab)
    QApplication.processEvents()
    field.setParent(None)
    host.deleteLater()
    return seen


class TestTyping:
    @pytest.mark.parametrize("keys", ["31.12.2025", "31122025", "31.12.25"])
    def test_whole_date_replaces_the_old_one(self, qt_app: QApplication, keys: str) -> None:
        field = DateEdit(date(2025, 1, 1))

        seen = _type_into(field, keys)

        assert field.value() == date(2025, 12, 31)
        assert field.text() == "31.12.2025"
        assert seen == ["31.12.2025"]  # genau eine Meldung, keine Zwischenstände

    def test_day_that_does_not_exist_in_the_old_month(self, qt_app: QApplication) -> None:
        field = DateEdit(date(2025, 2, 1))

        _type_into(field, "30.12.2025")

        assert field.value() == date(2025, 12, 30)

    def test_unreadable_input_keeps_the_old_date(self, qt_app: QApplication) -> None:
        field = DateEdit(date(2025, 2, 1))

        seen = _type_into(field, "31.02.2025")

        assert field.value() == date(2025, 2, 1) and field.text() == "01.02.2025"
        assert seen == []

    def test_programmatic_value_and_qdate_compatibility(self, qt_app: QApplication) -> None:
        field = DateEdit(date(2025, 1, 1))
        seen: list[QDate] = []
        field.dateChanged.connect(seen.append)

        field.set_value(date(2025, 1, 1))  # unverändert: kein Signal
        field.setDate(QDate(2026, 9, 30))

        assert field.value() == date(2026, 9, 30) and field.text() == "30.09.2026"
        assert field.date() == QDate(2026, 9, 30) and len(seen) == 1
        assert field.lineEdit() is field
