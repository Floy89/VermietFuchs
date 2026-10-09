"""Kleine, wiederverwendbare Prüf-Funktionen für Domain-Objekte.

Jede Funktion gibt den (ggf. bereinigten) Wert zurück oder wirft eine
:class:`~models.exceptions.ValidationError` mit einer deutschen Meldung, die
direkt in der Oberfläche angezeigt werden kann.
"""

from __future__ import annotations

from decimal import Decimal

from models.exceptions import ValidationError


def require_text(value: str, field_name: str, max_length: int = 200) -> str:
    """Stellt sicher, dass ein Pflicht-Textfeld ausgefüllt und nicht zu lang ist.

    Returns:
        Den Text ohne führende/abschließende Leerzeichen.
    """
    cleaned = (value or "").strip()
    if not cleaned:
        raise ValidationError(f"Bitte '{field_name}' ausfüllen.")
    if len(cleaned) > max_length:
        raise ValidationError(f"'{field_name}' darf höchstens {max_length} Zeichen lang sein.")
    return cleaned


def optional_text(value: str | None, field_name: str, max_length: int = 500) -> str:
    """Bereinigt ein optionales Textfeld und prüft nur die Maximallänge."""
    cleaned = (value or "").strip()
    if len(cleaned) > max_length:
        raise ValidationError(f"'{field_name}' darf höchstens {max_length} Zeichen lang sein.")
    return cleaned


def require_positive(value: Decimal | int, field_name: str) -> None:
    """Stellt sicher, dass ein Zahlenwert größer als 0 ist."""
    if value <= 0:
        raise ValidationError(f"'{field_name}' muss größer als 0 sein.")


def require_non_negative(value: Decimal | int, field_name: str) -> None:
    """Stellt sicher, dass ein Zahlenwert nicht negativ ist."""
    if value < 0:
        raise ValidationError(f"'{field_name}' darf nicht negativ sein.")


def require_in_range(value: int, minimum: int, maximum: int, field_name: str) -> None:
    """Stellt sicher, dass ``value`` zwischen ``minimum`` und ``maximum`` liegt."""
    if not minimum <= value <= maximum:
        raise ValidationError(f"'{field_name}' muss zwischen {minimum} und {maximum} liegen.")
