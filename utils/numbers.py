"""Lesen von Zahlen in deutscher (und englischer) Schreibweise - für Eingabefelder und Belege."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

_ALLOWED = re.compile(r"[0-9.,\-]")


def parse_number(text: str) -> tuple[Decimal | None, bool]:
    """Liest eine Zahl, wie Menschen sie tippen oder Rechnungen sie drucken.

    Regeln (deutsche Schreibweise zuerst):

    * Komma ist der Dezimaltrenner, Punkte sind Tausendertrenner: "1.234,56".
    * Steht nur ein einzelner Punkt in der Zahl, ist er ein Dezimaltrenner -
      außer es folgen genau drei Ziffern: "1.9829" = 1,9829, "62.5" = 62,5,
      aber "1.500" = 1500 (so steht es auf deutschen Rechnungen).
    * Leerzeichen, "€" und Einheiten werden übergangen.

    Returns:
        ``(Wert, gültig)`` - ein leerer Text ergibt ``(None, True)``,
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


def to_decimal(text: str) -> Decimal | None:
    """Wie :func:`parse_number`, gibt aber nur den Wert zurück (``None`` bei Unlesbarem)."""
    value, valid = parse_number(text)
    return value if valid else None
