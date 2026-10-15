"""Zahlenfelder, in die man einfach tippen kann.

Warum nicht ``QDoubleSpinBox``? Die Drehfelder von Qt prüfen jede Taste
einzeln gegen das fertige Zahlenformat. Das führte zu Eingaben, die
verschluckt oder falsch gelesen wurden:

* Steht im Feld "0,00 €" und der Cursor am Ende, wird "12,5" ignoriert;
  steht er am Anfang, wird daraus 1.250,00 €.
* Zeigt das Feld "–" (nicht angegeben), lässt es sich gar nicht beschreiben.
* Ein Punkt gilt immer als Tausendertrenner: "62.5" wird zu 6.250.

:class:`NumberEdit` ist deshalb ein gewöhnliches Textfeld: Getippt wird frei,
gelesen wird großzügig (Komma *oder* Punkt als Dezimaltrenner, Tausenderpunkte,
"€" und Einheiten werden übergangen), und erst beim Verlassen des Feldes wird
die Zahl schön formatiert. Beim Hineinklicken ist der ganze Inhalt markiert -
Lostippen ersetzt ihn.
"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFocusEvent
from PyQt6.QtWidgets import QLineEdit, QWidget

from models.value_objects import Money

_ALLOWED = re.compile(r"[0-9.,\-]")


def parse_number(text: str) -> tuple[Decimal | None, bool]:
    """Liest eine getippte Zahl.

    Regeln (deutsche Schreibweise zuerst):

    * Komma ist der Dezimaltrenner, Punkte sind Tausendertrenner: "1.234,56".
    * Steht nur ein einzelner Punkt in der Zahl, ist er ein Dezimaltrenner -
      außer es folgen genau drei Ziffern: "1.9829" = 1,9829, "62.5" = 62,5,
      aber "1.500" = 1500 (so steht es auf deutschen Rechnungen).
    * Leerzeichen, "€" und Einheiten werden übergangen.

    Returns:
        ``(Wert, gültig)`` - ein leeres Feld ergibt ``(None, True)``,
        Unlesbares ``(None, False)``.
    """
    raw = "".join(character for character in text if _ALLOWED.match(character))
    if not raw:
        return None, not any(character.isdigit() for character in text) and not text.strip(" €")
    negative = raw.startswith("-")
    digits = raw.lstrip("-")
    if "-" in digits or digits.count(",") > 1 or not any(c.isdigit() for c in digits):
        return None, False
    if "," in digits:
        digits = digits.replace(".", "").replace(",", ".")
    elif digits.count(".") == 1:
        whole, fraction = digits.split(".")
        if len(fraction) == 3 and whole:
            digits = whole + fraction  # Tausenderpunkt
    elif digits.count(".") > 1:
        digits = digits.replace(".", "")
    try:
        value = Decimal(digits)
    except InvalidOperation:
        return None, False
    return (-value if negative else value), True


def format_number(value: Decimal, decimals: int, trim_zeros: bool, grouped: bool = True) -> str:
    """Deutsche Schreibweise: "1.234,56" (mit ``grouped=False`` ohne Tausenderpunkte)."""
    quantized = value.quantize(Decimal(1).scaleb(-decimals), rounding=ROUND_HALF_UP) if decimals else value.quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )
    text = f"{quantized:,.{decimals}f}" if grouped else f"{quantized:.{decimals}f}"
    if trim_zeros and "." in text:
        text = text.rstrip("0").rstrip(".")
    # Englische Trenner gegen deutsche tauschen.
    return text.replace(",", "\x00").replace(".", ",").replace("\x00", ".")


class NumberEdit(QLineEdit):
    """Textfeld für eine Zahl mit fester Zahl an Nachkommastellen.

    Bietet die Methoden, die die Dialoge bisher von den Drehfeldern genutzt
    haben (``value``, ``setValue``, ``valueChanged``, ``setSuffix`` ...), damit
    es sie ohne Umbau ersetzen kann.

    Signals:
        valueChanged: Der Wert hat sich geändert (bei jeder Eingabe); Argument
            ist der Wert als ``float`` (0 bei leerem oder ungültigem Feld).
    """

    valueChanged = pyqtSignal(float)

    def __init__(
        self,
        decimals: int = 2,
        minimum: float = 0.0,
        maximum: float = 99_999_999.0,
        suffix: str = "",
        trim_zeros: bool = True,
        parent: QWidget | None = None,
    ) -> None:
        """
        Args:
            decimals: Nachkommastellen, auf die der Wert gerundet wird.
            minimum / maximum: Zulässiger Bereich; beim Verlassen wird begrenzt.
            suffix: Einheit hinter der Zahl, z. B. " €" (nur in der Anzeige).
            trim_zeros: Nullen am Ende weglassen ("1,9829" statt "1,98290000").
        """
        super().__init__(parent)
        self._decimals = decimals
        self._minimum = Decimal(str(minimum))
        self._maximum = Decimal(str(maximum))
        self._suffix = suffix
        self._trim_zeros = trim_zeros
        self._empty_allowed = False
        self._value: Decimal | None = Decimal(0)
        self.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.setToolTip("Komma oder Punkt als Dezimaltrenner, z. B. 12,5 oder 12.5")
        self.textEdited.connect(self._on_text_edited)
        self.editingFinished.connect(self._finish_editing)
        self._render()

    # ------------------------------------------------------------------ #
    # Kompatibel zu den bisherigen Drehfeldern
    # ------------------------------------------------------------------ #
    def lineEdit(self) -> QLineEdit:  # noqa: N802 (Qt-Name)
        """Das Textfeld selbst (bei Drehfeldern war es ein Kind-Widget)."""
        return self

    def decimals(self) -> int:
        """Anzahl der Nachkommastellen."""
        return self._decimals

    def setDecimals(self, decimals: int) -> None:  # noqa: N802
        self._decimals = decimals
        self._render()

    def setRange(self, minimum: float, maximum: float) -> None:  # noqa: N802
        self._minimum, self._maximum = Decimal(str(minimum)), Decimal(str(maximum))

    def setSuffix(self, suffix: str) -> None:  # noqa: N802
        """Einheit hinter der Zahl, z. B. " kWh"."""
        self._suffix = suffix
        self._render()

    def setSpecialValueText(self, text: str) -> None:  # noqa: N802
        """Ein leeres Feld ist erlaubt (bedeutet 0 bzw. "nicht angegeben") und zeigt ``text`` als Platzhalter."""
        self._empty_allowed = True
        self.setPlaceholderText(text)
        self._render()

    def setSingleStep(self, _step: float) -> None:  # noqa: N802
        """Ohne Pfeile gibt es keine Schrittweite - nur der Vollständigkeit halber vorhanden."""

    def setGroupSeparatorShown(self, _shown: bool) -> None:  # noqa: N802
        """Tausenderpunkte werden immer angezeigt - nur der Vollständigkeit halber vorhanden."""

    def value(self) -> float:
        """Der Wert als ``float`` (0 bei leerem Feld)."""
        return float(self._value or 0)

    def setValue(self, value: float) -> None:  # noqa: N802
        self.set_decimal(Decimal(str(value)))

    # ------------------------------------------------------------------ #
    # Werte
    # ------------------------------------------------------------------ #
    def decimal_value(self) -> Decimal:
        """Der Wert, gerundet auf die Nachkommastellen (0 bei leerem Feld)."""
        return self._quantize(self._value or Decimal(0))

    def set_decimal(self, value: Decimal | None) -> None:
        """Setzt den Wert, ohne ``valueChanged`` auszulösen... außer er ändert sich wirklich."""
        new_value = None if value is None else self._clamp(self._quantize(value))
        changed = new_value != self._value
        self._value = new_value
        self._render()
        if changed:
            self.valueChanged.emit(self.value())

    def is_empty(self) -> bool:
        """``True``, wenn nichts eingetragen ist."""
        return self._value is None

    # ------------------------------------------------------------------ #
    # Eingabe
    # ------------------------------------------------------------------ #
    def focusInEvent(self, event: QFocusEvent | None) -> None:  # noqa: N802 (Qt-Name)
        super().focusInEvent(event)
        if self.isReadOnly():
            return
        # Zum Bearbeiten ohne Tausenderpunkte und Einheit - und alles markiert,
        # damit Lostippen den alten Wert ersetzt. (Erst nach dem Klick markieren,
        # sonst hebt der Mausklick die Markierung gleich wieder auf.)
        self.setText(self._edit_text())
        QTimer.singleShot(0, self.selectAll)

    def focusOutEvent(self, event: QFocusEvent | None) -> None:  # noqa: N802
        super().focusOutEvent(event)
        self._finish_editing()

    def _on_text_edited(self, text: str) -> None:
        value, valid = parse_number(text)
        if valid and value is not None and value < 0 and self._minimum >= 0:
            valid = False
        self._set_invalid(not valid)
        if valid:
            new_value = value if value is not None else (None if self._empty_allowed else Decimal(0))
            if new_value != self._value:
                self._value = new_value
                self.valueChanged.emit(self.value())

    def _finish_editing(self) -> None:
        """Beim Verlassen: Bereich einhalten, Unlesbares verwerfen, schön formatieren."""
        if self._value is not None:
            clamped = self._clamp(self._quantize(self._value))
            if clamped != self._value:
                self._value = clamped
                self.valueChanged.emit(self.value())
        self._set_invalid(False)  # unlesbare Eingabe -> letzter gültiger Wert bleibt stehen
        if not self.hasFocus():
            self._render()

    # ------------------------------------------------------------------ #
    # Darstellung
    # ------------------------------------------------------------------ #
    def _render(self) -> None:
        if self.hasFocus() and not self.isReadOnly():
            return  # nicht in die laufende Eingabe hineinschreiben
        if self._value is None or (self._empty_allowed and self._value == 0):
            self.setText("")
            return
        self.setText(format_number(self._value, self._decimals, self._trim_zeros) + self._suffix)

    def _edit_text(self) -> str:
        if self._value is None or (self._empty_allowed and self._value == 0):
            return ""
        return format_number(self._value, self._decimals, self._trim_zeros, grouped=False)

    def _set_invalid(self, invalid: bool) -> None:
        if bool(self.property("invalid")) == invalid:
            return
        self.setProperty("invalid", invalid)
        self.style().unpolish(self)
        self.style().polish(self)

    def _quantize(self, value: Decimal) -> Decimal:
        return value.quantize(Decimal(1).scaleb(-self._decimals), rounding=ROUND_HALF_UP)

    def _clamp(self, value: Decimal) -> Decimal:
        return max(self._minimum, min(self._maximum, value))


class MoneyEdit(NumberEdit):
    """Eingabefeld für Euro-Beträge (immer zwei Nachkommastellen, "€" dahinter)."""

    def __init__(self, allow_negative: bool = False, parent: QWidget | None = None) -> None:
        super().__init__(
            decimals=2,
            minimum=-9_999_999.99 if allow_negative else 0.0,
            maximum=9_999_999.99,
            suffix=" €",
            trim_zeros=False,
            parent=parent,
        )

    def money(self) -> Money:
        """Der eingegebene Betrag (0,00 € bei leerem Feld)."""
        return Money.from_decimal(self.decimal_value())

    def set_money(self, money: Money) -> None:
        """Setzt den angezeigten Betrag."""
        self.set_decimal(money.amount)
