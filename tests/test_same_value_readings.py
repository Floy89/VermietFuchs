"""Prüfung auf gleiche Zählerstände an verschiedenen Tagen.

Anlass: Beim DG wurde der Stand vom 31.05.2025 (131 m³) dupliziert und nur das Datum
auf den 31.12.2025 geändert - der Verbrauch für Juni bis Dezember fiel dadurch auf 0.
"""

from __future__ import annotations

import os
from datetime import date
from decimal import Decimal

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from app_context import AppContext  # noqa: E402
from models.apartment import Apartment  # noqa: E402
from models.enums import MeterType  # noqa: E402
from models.meter_reading import MeterReading, same_value_readings, unchanged_pairs  # noqa: E402
from models.property import Property  # noqa: E402
from models.tenant import Tenant  # noqa: E402
from models.value_objects import DateRange  # noqa: E402
from ui.dialogs.meter_reading_dialog import MeterReadingDialog, same_value_text  # noqa: E402
from ui.lookups import Lookups  # noqa: E402

YEAR = DateRange.for_year(2025)
COLD = MeterType.COLD_WATER


def _reading(value: str, day: date, apartment_id: int = 3, meter_type: MeterType = COLD, id: int | None = None) -> MeterReading:
    return MeterReading(apartment_id, meter_type, Decimal(value), day, id=id)


class TestModel:
    def test_same_value_on_another_day_is_found(self) -> None:
        stored = [
            _reading("91", date(2024, 12, 31), id=1),
            _reading("131", date(2025, 5, 31), id=2),
            _reading("131", date(2025, 5, 31), apartment_id=1, id=3),  # andere Wohnung
            _reading("131", date(2025, 3, 1), meter_type=MeterType.WARM_WATER, id=4),  # anderer Zähler
        ]
        new = _reading("131", date(2025, 12, 31))
        assert [r.id for r in same_value_readings(new, stored)] == [2]

    def test_the_reading_itself_and_the_same_day_do_not_count(self) -> None:
        stored = [_reading("131", date(2025, 5, 31), id=2)]
        assert same_value_readings(_reading("131", date(2025, 5, 31), id=2), stored) == []  # nur bearbeitet
        assert same_value_readings(_reading("131", date(2025, 5, 31)), stored) == []  # gleicher Tag
        assert same_value_readings(_reading("191", date(2025, 12, 31)), stored) == []  # anderer Wert

    def test_unchanged_pairs_touching_the_period(self) -> None:
        readings = [
            _reading("91", date(2024, 12, 31)),
            _reading("131", date(2025, 5, 31)),
            _reading("131", date(2025, 12, 31)),
            _reading("131", date(2026, 3, 1)),
        ]
        pairs = unchanged_pairs(readings, YEAR)
        assert [(a.date, b.date) for a, b in pairs] == [(date(2025, 5, 31), date(2025, 12, 31))]
        # Das Paar 31.12.2025 / 01.03.2026 gehört zur Abrechnung 2026.
        later = unchanged_pairs(readings, DateRange.for_year(2026))
        assert [(a.date, b.date) for a, b in later] == [(date(2025, 12, 31), date(2026, 3, 1))]


def _house(context: AppContext, cold: dict[str, tuple[str, ...]], tenants_from: date = date(2024, 1, 1)) -> int:
    house = context.properties.save_property(Property(name="Haus", address="Weg 1, 12345 Ort"))
    assert house.id is not None
    for number, values in cold.items():
        flat = context.apartments.save_apartment(Apartment(house.id, number, Decimal("100"), 2))
        assert flat.id is not None
        context.tenants.save_tenant(Tenant(apartment_id=flat.id, name=f"Mieter {number}", start_date=tenants_from))
        for value, day in zip(values, (date(2024, 12, 31), date(2025, 5, 31), date(2025, 12, 31))):
            context.meter_readings.save_reading(MeterReading(flat.id, COLD, Decimal(value), day))
    return house.id


def _hints(context: AppContext, house_id: int) -> list[str]:
    return [w for w in context.billing.create_billing(house_id, YEAR).warnings if w.startswith("Zählerstände")]


class TestBillingHint:
    def test_unchanged_reading_is_reported(self, context: AppContext) -> None:
        house_id = _house(context, {"EG": ("43", "65", "97"), "DG": ("91", "131", "131")})
        assert _hints(context, house_id) == [
            "Zählerstände DG (Kaltwasser): Am 31.05.2025 und am 31.12.2025 ist derselbe Stand 131 m³ erfasst - "
            "dazwischen kein Verbrauch. Bitte prüfen, ob ein Wert beim Duplizieren nicht angepasst wurde."
        ]

    def test_corrected_reading_gives_no_hint(self, context: AppContext) -> None:
        house_id = _house(context, {"DG": ("91", "131", "191")})
        assert _hints(context, house_id) == []

    def test_zero_and_vacancy_are_not_reported(self, context: AppContext) -> None:
        # Stand 0: Zähler noch nicht in Betrieb.
        zero = _house(context, {"EG": ("0", "0", "12")})
        assert _hints(context, zero) == []
        # Leerstand: Einzug erst am 01.01.2026 - kein Verbrauch ist hier erklärbar.
        vacant = _house(context, {"EG": ("40", "40", "40")}, tenants_from=date(2026, 1, 1))
        assert _hints(context, vacant) == []


class TestDialog:
    def test_text_names_the_other_day(self) -> None:
        text = same_value_text(_reading("131", date(2025, 12, 31)), [_reading("131", date(2025, 5, 31))], "DG")
        assert text.startswith("DG – Kaltwasser: Derselbe Zählerstand 131 m³ ist bereits am 31.05.2025 erfasst.")
        assert "Duplikat, bei dem nur das Datum geändert wurde" in text

    def _dialog(self, context: AppContext) -> tuple[MeterReadingDialog, int]:
        _house(context, {"DG": ("91", "131")})
        reading = next(r for r in context.meter_readings.list_readings() if r.value == Decimal("131"))
        duplicate = MeterReading(reading.apartment_id, COLD, reading.value, date(2025, 12, 31))
        dialog = MeterReadingDialog(
            context.meter_readings.save_reading,
            Lookups(context),
            duplicate,
            find_same_value=context.meter_readings.same_value_readings,
        )
        return dialog, len(context.meter_readings.list_readings())

    def test_declined_question_keeps_the_dialog_open(
        self, qt_app: QApplication, context: AppContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        asked: list[str] = []
        monkeypatch.setattr(
            "ui.dialogs.meter_reading_dialog.confirm",
            lambda _p, title, _text, confirm_label="": asked.append(f"{title}/{confirm_label}") or False,
        )
        dialog, before = self._dialog(context)
        dialog.accept()
        assert asked == ["Gleicher Zählerstand/Trotzdem speichern"]
        assert len(context.meter_readings.list_readings()) == before  # nichts gespeichert
        assert "Nicht gespeichert" in dialog._error_label.text()

    def test_confirmed_question_saves(
        self, qt_app: QApplication, context: AppContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("ui.dialogs.meter_reading_dialog.confirm", lambda *_args, **_kwargs: True)
        dialog, before = self._dialog(context)
        dialog.accept()
        assert len(context.meter_readings.list_readings()) == before + 1

    def test_no_question_for_a_new_value(
        self, qt_app: QApplication, context: AppContext, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def fail(*_args: object, **_kwargs: object) -> bool:
            raise AssertionError("Bei einem neuen Wert darf nicht nachgefragt werden.")

        monkeypatch.setattr("ui.dialogs.meter_reading_dialog.confirm", fail)
        dialog, before = self._dialog(context)
        dialog._value_spin.set_decimal(Decimal("191"))
        dialog.accept()
        assert len(context.meter_readings.list_readings()) == before + 1
