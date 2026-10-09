"""Deutsche Formatierung von Beträgen, Zahlen, Datumswerten und Kostenanteilen.

Die Funktionen sind bewusst unabhängig von Qt, damit sie auch im PDF-Export
und in Tests verwendet werden können.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal

from models.billing import CostShare
from models.cost import Cost
from models.value_objects import DateRange, Money


def format_decimal(value: Decimal | int, places: int = 2) -> str:
    """Formatiert eine Zahl deutsch: Punkt als Tausender-, Komma als Dezimaltrenner.

    Beispiel: ``format_decimal(Decimal("1234.5"))`` ergibt ``"1.234,50"``.
    """
    quantum = Decimal(1).scaleb(-places)
    rounded = Decimal(value).quantize(quantum, rounding=ROUND_HALF_UP)
    english = f"{rounded:,.{places}f}"
    # Trennzeichen tauschen; "X" dient als Zwischenspeicher.
    return english.replace(",", "X").replace(".", ",").replace("X", ".")


def format_quantity(value: Decimal | int, max_places: int = 3) -> str:
    """Wie :func:`format_decimal`, aber ohne überflüssige Nullen am Ende.

    Beispiel: ``65`` → ``"65"``, ``Decimal("12.50")`` → ``"12,5"``.
    """
    text = format_decimal(value, max_places)
    if "," in text:
        text = text.rstrip("0").rstrip(",")
    return text


def format_money(money: Money) -> str:
    """Formatiert einen Geldbetrag, z. B. ``"1.234,56 €"``."""
    return f"{format_decimal(money.amount, 2)} €"


def format_date(value: date | None) -> str:
    """Formatiert ein Datum als ``TT.MM.JJJJ``; ``None`` ergibt einen leeren Text."""
    return value.strftime("%d.%m.%Y") if value else ""


def format_datetime(value: datetime | None) -> str:
    """Formatiert einen Zeitstempel als ``TT.MM.JJJJ HH:MM``."""
    return value.strftime("%d.%m.%Y %H:%M") if value else ""


def format_period(period: DateRange) -> str:
    """Formatiert einen Zeitraum, z. B. ``"01.01.2025 – 31.12.2025"``."""
    return f"{format_date(period.start)} – {format_date(period.end)}"


def format_period_short(period: DateRange) -> str:
    """Zeitraum in Kurzform mit zweistelligem Jahr, z. B. ``"01.11.23 – 31.10.24"`` (für Tabellen)."""
    return f"{period.start.strftime('%d.%m.%y')} – {period.end.strftime('%d.%m.%y')}"


def format_file_size(size_bytes: int) -> str:
    """Formatiert eine Dateigröße in KB oder MB."""
    if size_bytes >= 1024 * 1024:
        return f"{format_decimal(Decimal(size_bytes) / Decimal(1024 * 1024), 1)} MB"
    return f"{format_decimal(Decimal(size_bytes) / Decimal(1024), 0)} KB"


def format_piece_suffix(cost: Cost) -> str:
    """Zusatz, der einen zeitlichen Ausschnitt einer Rechnung kenntlich macht.

    * Stück aus einem Split: `` · Abschnitt 01.01.24 – 17.02.24`` (und der
      Tagesanteil, falls der Split nur teilweise im Abrechnungszeitraum liegt),
    * Rechnung ohne Splits, die über den Zeitraum hinausragt:
      `` – anteilig 152 von 366 Tagen``,
    * vollständige Rechnung: leer.
    """
    if cost.split_period is not None:
        suffix = f" · Abschnitt {format_period_short(cost.split_period)}"
        if cost.period != cost.split_period:
            suffix += f" (anteilig {cost.period.days} von {cost.split_period.days} Tagen)"
        return suffix
    if cost.full_period is not None:
        return f" – anteilig {cost.period.days} von {cost.full_period.days} Tagen"
    return ""


def format_share_title(share: CostShare) -> str:
    """Bezeichnung eines Kostenanteils.

    Bei Mischschlüsseln (Heizung) wird der Bestandteil angehängt, z. B.
    ``"Heizung/Warmwasser (Verbrauch)"``.
    """
    title = share.cost.title
    if share.component_amount != share.cost.amount:
        title += f" ({share.component})"
    title += format_piece_suffix(share.cost)
    return title


def format_share_basis(share: CostShare) -> str:
    """Beschreibt die Berechnungsgrundlage eines Kostenanteils in Worten.

    Beispiel: ``"65 m² von 240 m² · 184 von 365 Tagen"``. Der Zeitanteil wird
    nur genannt, wenn der Mieter nicht den gesamten Zeitraum gewohnt hat.
    """
    if share.note:
        return share.note
    basis = (
        f"{format_quantity(share.apartment_weight)} {share.unit} "
        f"von {format_quantity(share.total_weight)} {share.unit}"
    )
    if share.days != share.period_days:
        basis += f" · {share.days} von {share.period_days} Tagen"
    if share.quantity is not None and share.quantity_total is not None:
        unit = share.quantity_unit
        amount = f"{format_quantity(share.quantity)} {unit} von {format_quantity(share.quantity_total)} {unit}"
        if share.is_difference:
            # Anteil an der Differenz zwischen Gesamtverbrauch und Wohnungszählern.
            basis += f" · entspricht {amount} Differenz"
        elif share.component_amount == share.cost.amount:
            # Differenz wird wie gemessen verteilt: tatsächlicher Anteil am Gesamtverbrauch.
            basis += f" · entspricht {amount} Gesamtverbrauch lt. Rechnung"
    if share.tenant_quantity is not None:
        basis += f" · Verbrauch gesamt {format_quantity(share.tenant_quantity)} {share.quantity_unit}"
    if share.individual_key:
        # Anteil nach abweichendem Schlüssel statt nach dem Standard der Rechnung.
        basis += " · lt. Mietvertrag"
    return basis
