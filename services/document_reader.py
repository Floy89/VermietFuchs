"""Belege ohne KI auslesen: feste Suchregeln für deutsche Versorgerrechnungen plus gelernte Fundstellen.

Reine Funktionen auf Text - ohne Qt, ohne Datenbank -, damit sie sich mit
Beispieltexten prüfen lassen. Woher der Text kommt (PDF oder
Texterkennung), entscheidet die Oberfläche.

Vorgehen:

1. **Text vereinheitlichen** - typische Lesefehler der Texterkennung
   beheben ("31 .12.25", "15.102026", "m'" statt "m³").
2. **Allgemeine Regeln** - Schlüsselwörter wie "Rechnungsdatum",
   "Rechnungsbetrag", "Zeitraum vom … bis …", "Gesamtverbrauch",
   "Umsatzsteuer 7 %", Preise je m³/kWh, Grundpreis je Jahr/Tage/Monat,
   Zwischenablesungen.
3. **Gelernte Fundstellen** - hat der Benutzer bei diesem Absender schon
   einmal korrigiert, gilt die Zeile, in der damals der richtige Wert stand
   (siehe :class:`~models.document.FieldAnchor`).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Sequence

from models.cost import InvoiceSplit
from models.document import DocumentDraft, DocumentProfile, FieldAnchor
from models.tariff import SplitCalculation, fit_amounts, year_fraction
from models.value_objects import DateRange, Money
from utils.numbers import to_decimal

_DATE = r"\d{1,2}\.\d{1,2}\.(?:\d{4}|\d{2})(?!\d)"
_NUMBER = r"-?\d{1,3}(?:\.\d{3})+(?:,\d+)?|-?\d+(?:,\d+)?"
_DATE_RE = re.compile(rf"(?<![\d.]){_DATE}")
_NUMBER_RE = re.compile(rf"(?<![\d.,])(?:{_NUMBER})(?![\d])")
_UNIT = r"(m³|kwh)"

#: Wörter, die nur Einheiten oder Füllwörter sind - sie machen eine Zeile nicht "aussagekräftig".
_FILLER = {"euro", "eur", "€", "m³", "kwh", "tage", "tag", "%", "von", "bis", "zum", "vom", "-", "/", ":"}

#: Schlüsselwörter für den Rechnungsbetrag, wichtigstes zuerst.
_AMOUNT_KEYWORDS = (
    "rechnungsbetrag", "gesamtbetrag", "summe brutto", "bruttobetrag", "endbetrag", "rechnungssumme", "gesamtsumme",
)
#: Zeilen mit diesen Wörtern enthalten nie den Rechnungsbetrag.
_AMOUNT_EXCLUDE = ("umsatzsteuer", "mwst", "netto", "abschlag", "zahlung", "gezahlt", "zu zahlen", "guthaben")

#: Erkennung der Kostenart: (Suchwort im Beleg, Kostenart im Katalog), spezifische zuerst.
_COST_TYPE_HINTS: tuple[tuple[str, str], ...] = (
    ("grundsteuer", "Grundsteuer"),
    ("niederschlag", "Niederschlagswasser"),
    ("schmutzwasser", "Entwässerung"),
    ("abwasser", "Entwässerung"),
    ("entwässerung", "Entwässerung"),
    ("straßenreinigung", "Straßenreinigung"),
    ("winterdienst", "Winterdienst"),
    ("abfall", "Müllbeseitigung"),
    ("müll", "Müllbeseitigung"),
    ("schornstein", "Schornsteinreinigung"),
    ("gebäudeversicherung", "Sach- und Haftpflichtversicherung"),
    ("versicherung", "Sach- und Haftpflichtversicherung"),
    ("fernwärme", "Heizung"),
    ("erdgas", "Brennstoffkosten (Gas/Öl)"),
    ("gasverbrauch", "Brennstoffkosten (Gas/Öl)"),
    ("heizöl", "Brennstoffkosten (Gas/Öl)"),
    ("trinkwasser", "Wasserversorgung"),
    ("wasserverbrauch", "Wasserversorgung"),
    ("allgemeinstrom", "Beleuchtung (Allgemeinstrom)"),
    ("strom", "Beleuchtung (Allgemeinstrom)"),
    ("gas", "Brennstoffkosten (Gas/Öl)"),
    ("wasser", "Wasserversorgung"),
)

#: Monatsnamen für ausgeschriebene Daten ("09. Juli 2026"), auch abgekürzt.
_MONTHS: dict[str, int] = {
    "januar": 1, "jan": 1, "februar": 2, "feb": 2, "märz": 3, "maerz": 3, "mär": 3, "mrz": 3, "april": 4, "apr": 4,
    "mai": 5, "juni": 6, "jun": 6, "juli": 7, "jul": 7, "august": 8, "aug": 8, "september": 9, "sep": 9, "sept": 9,
    "oktober": 10, "okt": 10, "november": 11, "nov": 11, "dezember": 12, "dez": 12,
}
_TEXT_DATE_RE = re.compile(
    r"(?<![\d.])(\d{1,2})\.\s*(" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?\s+(\d{4})(?!\d)", re.I
)

_COMPANY_RE = re.compile(
    r"([A-ZÄÖÜ][\wÄÖÜäöüß&.\-]*(?:\s+[A-ZÄÖÜ&][\wÄÖÜäöüß&.\-]*){0,4}\s+(?:AG|GmbH(?:\s*&\s*Co\.\s*KG)?|KG|SE|eG|e\.\s?V\.))(?![\w])"
)
_VAT_ID_RE = re.compile(r"(?:USt-?Id(?:Nr)?\.?|Umsatzsteuer-?Id(?:entifikationsnummer)?|USt-?ID)\s*:?\s*(DE\s?\d{3}\s?\d{3}\s?\d{3})", re.I)
_DOMAIN_RE = re.compile(r"(?:@|www\.)([a-z0-9-]+(?:\.[a-z0-9-]+)*\.(?:de|com|net|eu|org|info))\b", re.I)


# ---------------------------------------------------------------------- #
# Text vereinheitlichen und zerlegen
# ---------------------------------------------------------------------- #
def normalize_text(text: str) -> str:
    """Behebt typische Lesefehler der Texterkennung und vereinheitlicht Einheiten."""
    text = text.replace(" ", " ").replace("\r", "")
    text = re.sub(r"(\d)\s+\.(\d)", r"\1.\2", text)                      # "31 .12.25"
    text = re.sub(r"(\d)\s+,(\d)", r"\1,\2", text)                        # "1 ,9829"
    text = re.sub(r"/\s*m[3'’`´](?![\w])", "/ m³", text)                   # "Euro / m'"
    text = re.sub(r"(?<=\d)\s*rn[3'’](?![\w])", " m³", text)                 # "493 rn3"
    text = re.sub(r"(?<![\d.])(\d{1,2})\.(\d{2})(\d{4})(?!\d)", r"\1.\2.\3", text)  # "15.102026"
    text = re.sub(r"(?<=\d)\s*m[3'’`´](?![\w])", " m³", text)                # "290 m'" / "290 m3"
    text = re.sub(r"(?i)\bkwh\b", "kWh", text)
    text = re.sub(r"\bE(?:LJ|IJ|lJ|U\s)R\b", "EUR", text)                    # "ELJR", "EIJR"
    text = re.sub(r"(?i)/\s*J-?\s?ahr\b", "/Jahr", text)                     # "/J-ahr"
    # "09. Juli 2026" -> "09.07.2026": dann gelten für solche Daten dieselben Regeln.
    text = _TEXT_DATE_RE.sub(
        lambda m: f"{int(m.group(1)):02d}.{_MONTHS[m.group(2).lower()]:02d}.{m.group(3)}", text
    )
    return text


def split_lines(text: str) -> list[str]:
    """Die nicht leeren Zeilen des Textes, ohne Randleerzeichen."""
    return [line.strip() for line in normalize_text(text).splitlines() if line.strip()]


def line_pattern(line: str) -> str:
    """Die Zeile mit Platzhaltern statt Daten (``D``) und Zahlen (``#``), klein und ohne Doppelleerzeichen."""
    pattern = _DATE_RE.sub(" D ", line)
    pattern = _NUMBER_RE.sub(" # ", pattern)
    return " ".join(pattern.lower().split())


def dates_in(line: str) -> list[date]:
    """Alle Daten der Zeile (zweistellige Jahre als 20xx); unmögliche Daten werden übersprungen."""
    found: list[date] = []
    for match in _DATE_RE.findall(line):
        parsed = parse_date(match)
        if parsed is not None:
            found.append(parsed)
    return found


def numbers_in(line: str) -> list[Decimal]:
    """Alle Zahlen der Zeile, Daten ausgenommen."""
    without_dates = _DATE_RE.sub(" ", line)
    values = [to_decimal(match) for match in _NUMBER_RE.findall(without_dates)]
    return [value for value in values if value is not None]


def parse_date(text: str) -> date | None:
    """Liest "31.12.2025" oder "31.12.25"."""
    try:
        day, month, year = (int(part) for part in text.split("."))
    except ValueError:
        return None
    if year < 100:
        year += 2000
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _money_values(line: str) -> list[Decimal]:
    """Zahlen mit genau zwei Nachkommastellen - so stehen Euro-Beträge auf Rechnungen."""
    without_dates = _DATE_RE.sub(" ", line)
    return [
        value
        for match in _NUMBER_RE.findall(without_dates)
        if re.search(r",\d{2}$", match) and (value := to_decimal(match)) is not None
    ]


# ---------------------------------------------------------------------- #
# Absender
# ---------------------------------------------------------------------- #
def identify_issuer(text: str) -> tuple[str, str]:
    """Erkennt den Absender: ``(Schlüssel, Name)``.

    Der Schlüssel ist möglichst eindeutig und ändert sich von Jahr zu Jahr
    nicht: zuerst die Umsatzsteuer-ID, sonst die Internet-Adresse, sonst der
    Firmenname. Beides leer, wenn nichts zu finden ist.
    """
    normalized = normalize_text(text)
    name = _company_name(normalized)
    if vat_id := _VAT_ID_RE.search(normalized):
        return "ust:" + re.sub(r"\s", "", vat_id.group(1)).upper(), name
    if domain := _DOMAIN_RE.search(normalized):
        return "web:" + domain.group(1).lower(), name
    if name:
        return "name:" + name.casefold(), name
    return "", ""


def _company_name(text: str) -> str:
    """Der Firmenname des Absenders - so lang, wie er im Beleg wiederholt vorkommt.

    Der reguläre Ausdruck erfasst bis zu fünf Wörter vor der Rechtsform; davor
    steht manchmal Logo-Text, den die Texterkennung mitliest ("Maingau Jr
    Energie Maingaij Energie GmbH"). Deshalb wird jeder Treffer ab den letzten
    zwei Wörtern ("Energie GmbH") nur so weit nach vorn verlängert, wie die
    längere Form noch mindestens halb so oft vorkommt (und mehr als einmal).
    Unter allen Treffern gewinnt der häufigste vollständige Name.
    """
    flat = " ".join(text.split())

    def count(candidate: str) -> int:
        return len(re.findall(rf"(?<![\w]){re.escape(candidate)}(?![\w])", flat, re.I))

    names: dict[str, int] = {}
    for match in _COMPANY_RE.finditer(text):
        words = match.group(1).split()
        size = min(2, len(words))
        name = " ".join(words[-size:])
        while size < len(words):
            longer = " ".join(words[-(size + 1):])
            if count(longer) == count(name) or count(longer) >= max(2, count(name) / 2):
                name, size = longer, size + 1
            else:
                break
        names.setdefault(name, count(name))
    # "Energie GmbH" steckt in "MAINGAU Energie GmbH" und kommt darum immer öfter vor - ein
    # Name, der nur das Ende eines anderen gefundenen Namens ist, scheidet aus.
    full = {name: hits for name, hits in names.items() if not any(other.endswith(" " + name) for other in names)}
    if not full:
        return ""
    return max(full, key=lambda name: (full[name], len(name)))


def readable_name(name: str) -> str:
    """Firmennamen in Großbuchstaben lesbar schreiben: "GELSENWASSER AG" -> "Gelsenwasser AG"."""
    words = []
    for word in name.split():
        if word.isupper() and len(word) > 3 and word not in {"GMBH"}:
            words.append(word.capitalize())
        else:
            words.append(word)
    return " ".join(words)


# ---------------------------------------------------------------------- #
# Allgemeine Regeln
# ---------------------------------------------------------------------- #
def find_invoice_date(lines: Sequence[str]) -> date | None:
    """Datum hinter "Rechnungsdatum" (oder in der Zeile darunter)."""
    for index, line in enumerate(lines):
        lowered = line.lower()
        if "rechnungsdatum" in lowered or "datum der rechnung" in lowered or "rechnungs-datum" in lowered:
            for candidate in (line, *lines[index + 1:index + 5]):
                if candidate is not line and re.search(r"zeitraum|vom|bis", candidate, re.I):
                    continue
                found = dates_in(candidate)
                if found:
                    return found[0]
    return None


def find_period(lines: Sequence[str]) -> DateRange | None:
    """Abrechnungszeitraum: "vom … bis (zum) …" oder zwei Daten in einer Zeitraum-Zeile."""
    text = "\n".join(lines)
    if match := re.search(rf"vom\s+({_DATE})\s+bis\s+(?:zum\s+)?({_DATE})", text, re.I):
        start, end = parse_date(match.group(1)), parse_date(match.group(2))
        if start and end and end >= start:
            return DateRange(start, end)
    for line in lines:
        if re.search(r"(abrechnungs|leistungs|liefer)?zeitraum", line, re.I):
            found = dates_in(line)
            if len(found) >= 2 and found[1] >= found[0]:
                return DateRange(found[0], found[1])
    for line in lines:
        if match := re.search(rf"({_DATE})\s*[-–]\s*({_DATE})", line):
            start, end = parse_date(match.group(1)), parse_date(match.group(2))
            if start and end and end > start:
                return DateRange(start, end)
    return None


def find_amount(lines: Sequence[str]) -> Money | None:
    """Rechnungsbetrag (brutto): Betrag in der Zeile eines Schlüsselworts oder knapp darunter.

    Ein Treffer in derselben Zeile hat Vorrang vor einem in den Folgezeilen;
    Zeilen mit Umsatzsteuer, Abschlägen oder Zahlungen werden übergangen.
    """
    look_ahead: Money | None = None
    for keyword in _AMOUNT_KEYWORDS:
        for index, line in enumerate(lines):
            lowered = line.lower()
            if keyword not in lowered or any(word in lowered for word in _AMOUNT_EXCLUDE):
                continue
            # Nur, wenn das Wort vorn in der Zeile steht: "Ihr Rechnungsbetrag 787,08 Euro" ja,
            # "... hat sich Ihr Rechnungsbetrag um 69,68 Euro verringert" (Fließtext) nicht.
            if len(lowered[:lowered.index(keyword)].split()) > 3:
                continue
            values = _money_values(line)
            if values:
                return Money.from_decimal(values[-1])
            if look_ahead is None:
                # Steht der Betrag nicht in der Zeile, nur eine Folgezeile annehmen, die aus
                # nichts als einem Betrag besteht ("787,08 Euro") - sonst landet man bei einer
                # Überschrift wie "Ihr Rechnungsbetrag" in der ersten Preiszeile darunter.
                for following in lines[index + 1:index + 4]:
                    if any(word in following.lower() for word in _AMOUNT_EXCLUDE):
                        continue
                    if re.fullmatch(rf"\s*({_NUMBER})\s*(?:euro|eur|€)?\s*", following, re.I):
                        following_values = _money_values(following)
                        if following_values:
                            look_ahead = Money.from_decimal(following_values[0])
                            break
    return look_ahead or _amount_from_net_and_vat(lines)


def _amount_from_net_and_vat(lines: Sequence[str]) -> Money | None:
    """Rückfall: Bruttobetrag aus "Umsatzsteuer 7 % von 735,59 Euro 51,49 Euro" (netto + Steuer).

    Hilft, wenn die Texterkennung den (oft fett gedruckten) Rechnungsbetrag
    nicht lesen konnte - die Zeile mit der Umsatzsteuer nennt beide Teile.
    """
    for line in lines:
        if re.search(r"umsatzsteuer|mwst", line, re.I) and re.search(r"\bvon\b", line, re.I):
            values = _money_values(line)
            if len(values) >= 2:
                return Money.from_decimal(values[-2] + values[-1])
    return None


def find_consumption(lines: Sequence[str]) -> tuple[Decimal | None, str]:
    """Gesamtverbrauch mit Einheit, z. B. ``(290, "m³")``.

    Fehlt die Einheit in der Zeile (Lesefehler der Texterkennung), wird die
    im Beleg am häufigsten genannte Einheit genommen.
    """
    for line in lines:
        if "gesamtverbrauch" in line.lower() and (match := re.search(rf"({_NUMBER})\s*{_UNIT}", line, re.I)):
            value = to_decimal(match.group(1))
            if value is not None and value > 0:
                return value, _unit(match.group(2))
    # Summenzeile unter einer Verbrauchstabelle: "Summe 41.058 kWh"
    for line in lines:
        if match := re.fullmatch(rf"(?:summe|gesamt|gesamtmenge)\s*:?\s*({_NUMBER})\s*{_UNIT}", line, re.I):
            value = to_decimal(match.group(1))
            if value is not None and value > 0:
                return value, _unit(match.group(2))
    for keywords in (("verbrauch", "abrechnungsmenge"),):
        for line in lines:
            if any(keyword in line.lower() for keyword in keywords):
                if match := re.search(rf"({_NUMBER})\s*{_UNIT}", line, re.I):
                    value = to_decimal(match.group(1))
                    if value is not None and value > 0:
                        return value, _unit(match.group(2))
    for line in lines:
        if "gesamtverbrauch" in line.lower() and (values := numbers_in(line)):
            if values[-1] > 0:
                return values[-1], _most_common_unit(lines)
    return None, ""


def _most_common_unit(lines: Sequence[str]) -> str:
    """Die im Beleg häufigste Verbrauchseinheit (leer, wenn keine vorkommt)."""
    text = "\n".join(lines)
    cubic, kwh = text.count("m³"), len(re.findall(r"kwh", text, re.I))
    if not cubic and not kwh:
        return ""
    return "m³" if cubic >= kwh else "kWh"


def find_vat(lines: Sequence[str]) -> Decimal | None:
    """Umsatzsteuersatz, z. B. aus "Umsatzsteuer 7 % von 735,59 Euro".

    Rückfall: die Steuerspalte der Preiszeilen ("AP … 8,260 Ct/kWh 16.949 kWh 19% 1.399,99 €").
    """
    for line in lines:
        if re.search(r"umsatzsteuer|mwst|ust\b", line, re.I):
            if match := re.search(r"(\d{1,2}(?:,\d+)?)\s*%", line):
                return to_decimal(match.group(1))
    for line in lines:
        if re.search(r"mengenpreis|arbeitspreis|grundpreis|\bAP\b|\bGP\b", line, re.I if "AP" not in line else 0):
            if match := re.search(r"(?<![\d,])(0|5|7|16|19)\s*%", line):
                return Decimal(match.group(1))
    # Prozentangabe unleserlich: "Umsatzsteuer … 2.616,93 EUR 497,22 EUR" -> 497,22 / 2.616,93 = 19 %
    for line in lines:
        if re.search(r"umsatzsteuer|mwst", line, re.I) and len(values := _money_values(line)) >= 2 and values[-2] > 0:
            rate = (values[-1] / values[-2] * 100).quantize(Decimal("0.1"))
            for usual in (Decimal(19), Decimal(7), Decimal(16), Decimal(5)):
                if abs(rate - usual) <= Decimal("0.1"):
                    return usual
    return None


#: Ein Preis, der Zeitraum, für den er gilt (``None``: ohne Zeitraum angegeben), und der
#: Nettobetrag, den die Rechnung in dieser Zeile ausweist (``None``, wenn keiner dasteht).
PricedPeriod = tuple[DateRange | None, Decimal, Decimal | None]


def _line_net(line: str) -> Decimal | None:
    """Der Betrag am Ende einer Preiszeile ("… 19% 74,56 €"), sonst ``None``."""
    if re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*$", line, re.I):
        values = _money_values(line)
        return values[-1] if values else None
    return None


def _line_range(line: str) -> DateRange | None:
    """Der Zeitraum am Anfang einer Preiszeile ("05.07.2025 - 31.12.2025 AP …"), sonst ``None``."""
    found = dates_in(line)
    if len(found) >= 2 and found[1] >= found[0]:
        return DateRange(found[0], found[1])
    return None


def working_prices(lines: Sequence[str]) -> list[PricedPeriod]:
    """Alle Arbeits- bzw. Mengenpreise netto je m³ oder kWh, mit Zeitraum, falls die Zeile einen nennt.

    Erkennt "Arbeitspreis", "Mengenpreis", "Verbrauchspreis" und die Abkürzung
    "AP"; Cent-Angaben werden in Euro umgerechnet.
    """
    result: list[PricedPeriod] = []
    previous_was_price = False
    for line in lines:
        is_price_line = bool(
            re.search(r"mengenpreis|arbeitspreis|verbrauchspreis|\bAP\b", line, re.I if "AP" not in line else 0)
        )
        # Fortsetzungszeile ohne Stichwort direkt darunter: "01.01.25 - 12.09.25 200 m³ 1,9829 Euro / m³ …"
        if not is_price_line and previous_was_price and re.match(rf"\s*{_DATE}\s*[-–]?\s*{_DATE}", line):
            is_price_line = True
        previous_was_price = is_price_line
        if not is_price_line:
            continue
        if "brutto" in line.lower():  # "Ihr aktueller Arbeitspreis beträgt 11,10 ct/kWh (brutto)" - gerechnet wird netto
            continue
        value: Decimal | None = None
        if match := re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*/\s*{_UNIT}", line, re.I):
            value = to_decimal(match.group(1))
        elif match := re.search(rf"({_NUMBER})\s*(?:ct|cent)\s*/\s*{_UNIT}", line, re.I):
            cents = to_decimal(match.group(1))
            value = cents / 100 if cents is not None else None
        if value is not None and value > 0:
            result.append((_line_range(line), value, _line_net(line)))
    return result


def base_prices(lines: Sequence[str]) -> list[PricedPeriod]:
    """Alle Grundpreise netto auf ein Jahr gerechnet ("153,00 Euro / 365 Tage", "… EUR/Jahr", "… €/Monat").

    Erkennt "Grundpreis" und die Abkürzung "GP".
    """
    result: list[PricedPeriod] = []
    previous_was_price = False
    for line in lines:
        is_price_line = "grundpreis" in line.lower() or bool(re.search(r"\bGP\b", line))
        if not is_price_line and previous_was_price and re.match(rf"\s*{_DATE}\s*[-–]?\s*{_DATE}", line):
            is_price_line = True
        previous_was_price = is_price_line
        if not is_price_line:
            continue
        if "brutto" in line.lower():
            continue
        value: Decimal | None = None
        if match := re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*/\s*(\d+)\s*tag", line, re.I):
            amount, days = to_decimal(match.group(1)), int(match.group(2))
            value = amount * 365 / days if amount is not None and days else None
        elif match := re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*/\s*jahr", line, re.I):
            value = to_decimal(match.group(1))
        elif match := re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*/\s*monat", line, re.I):
            monthly = to_decimal(match.group(1))
            value = monthly * 12 if monthly is not None else None
        if value is not None and value > 0:
            result.append((_line_range(line), value, _line_net(line)))
    return result


def find_working_price(lines: Sequence[str]) -> Decimal | None:
    """Der (erste) Arbeits- bzw. Mengenpreis netto je m³ oder kWh."""
    prices = working_prices(lines)
    return prices[0][1] if prices else None


def find_base_price(lines: Sequence[str]) -> Decimal | None:
    """Der (erste) Grundpreis netto je Jahr."""
    prices = base_prices(lines)
    return prices[0][1] if prices else None


def price_for(period: DateRange, prices: Sequence[PricedPeriod], fallback: Decimal | None) -> Decimal | None:
    """Der Preis, der für ``period`` gilt: der mit dem Zeitraum, der ihn enthält (bzw. am meisten überlappt)."""
    best: tuple[int, Decimal] | None = None
    for valid, value, _net in prices:
        if valid is None:
            continue
        overlap = period.intersection(valid)
        if overlap is not None and (best is None or overlap.days > best[0]):
            best = (overlap.days, value)
    return best[1] if best else fallback


def allocate_stated_nets(
    periods: Sequence[DateRange], weights: Sequence[Decimal], prices: Sequence[PricedPeriod]
) -> list[Decimal | None]:
    """Verteilt die in Preiszeilen ausgewiesenen Nettobeträge auf die Splits.

    Eine Preiszeile kann mehrere Splits umfassen ("Mengenpreis 01.01.25 -
    12.09.25 200 m³ … 396,58 Euro" für zwei Ablesezeiträume). Ihr Betrag wird
    dann im Verhältnis der ``weights`` (Verbrauch beim Arbeitspreis, Tage beim
    Grundpreis) centgenau aufgeteilt - so rechnet der Versorger, auch bei
    Eigenheiten wie "153,00 Euro / 365 Tage" im Schaltjahr.

    Returns:
        Je Split den Nettobetrag - oder ``None``, wo keine Preiszeile ihn mit
        Betrag vollständig abdeckt (dann wird aus dem Preis gerechnet).
    """
    result: list[Decimal | None] = [None] * len(periods)
    for valid, _value, net in prices:
        if valid is None or net is None:
            continue
        inside = [i for i, period in enumerate(periods) if valid.start <= period.start and period.end <= valid.end]
        # Nur wenn die Splits den Zeitraum der Zeile genau ausfüllen - sonst gehört ein Teil woandershin.
        if not inside or sum(periods[i].days for i in inside) != valid.days:
            continue
        shares = [weights[i] for i in inside]
        if sum(shares, Decimal(0)) <= 0:
            continue
        for i, part in zip(inside, Money.from_decimal(net).allocate(shares)):
            result[i] = part.amount
    return result


def find_cost_type(text: str, cost_type_names: Sequence[str]) -> str:
    """Kostenart nach Suchwörtern im Beleg; nur Namen aus dem Katalog.

    Gezählt wird, wie oft die Suchwörter jeder Kostenart vorkommen - ein
    einzelnes "Abwasser" im Kleingedruckten einer Trinkwasserrechnung soll
    nicht den Ausschlag geben. Die allgemeinen Wörter "wasser", "gas" und
    "strom" zählen nur halb.
    """
    lowered = text.lower()
    available = set(cost_type_names)
    scores: dict[str, float] = {}
    for keyword, name in _COST_TYPE_HINTS:
        if name not in available:
            continue
        hits = len(re.findall(rf"(?<![a-zäöüß]){re.escape(keyword)}", lowered))
        if hits:
            weight = 0.5 if keyword in {"wasser", "gas", "strom"} else 1.0
            scores[name] = scores.get(name, 0.0) + hits * weight
    if not scores:
        return ""
    # Bei Gleichstand gewinnt die zuerst genannte (spezifischere) Kostenart.
    order = [name for _keyword, name in _COST_TYPE_HINTS]
    return max(scores, key=lambda name: (scores[name], -order.index(name)))


def find_reading_splits(lines: Sequence[str], period: DateRange | None) -> list[tuple[DateRange, Decimal]]:
    """Abschnitte aus Zählerständen: Zeilen mit Datum, Zählerstand und Verbrauch.

    Beispiel (Wasserrechnung)::

        Anfangszählerstand 13.09.25 418 m³
        Zwischenablesung   31.12.25 493 m³  75 m³
        Turnusablesung     30.09.26 708 m³ 215 m³

    ergibt 13.09.2025–31.12.2025 mit 75 m³ und 01.01.2026–30.09.2026 mit 215 m³.
    Nur mit mindestens zwei Ablesungen - eine einzige ist kein Split.
    """
    readings: list[tuple[date, Decimal]] = []
    start: date | None = None
    for line in lines:
        # Die Einheit beim Verbrauch fehlt in der Texterkennung manchmal ("493 m³ 75").
        if match := re.search(rf"({_DATE})\s+({_NUMBER})\s*{_UNIT}\s+({_NUMBER})(?:\s*{_UNIT})?\s*$", line, re.I):
            day, used = parse_date(match.group(1)), to_decimal(match.group(4))
            if day is not None and used is not None:
                readings.append((day, used))
        elif "anfang" in line.lower() and (found := dates_in(line)):
            start = found[0]
    if len(readings) < 2:
        return []
    readings.sort()
    start = start or (period.start if period else None)
    if start is None:
        return []
    result: list[tuple[DateRange, Decimal]] = []
    for day, used in readings:
        if day < start:
            return []
        result.append((DateRange(start, day), used))
        start = day + timedelta(days=1)
    return result


#: Ein Abschnitt einer Gasrechnung: Zeitraum, Volumen/Zustandszahl/Brennwert und - falls
#: angegeben - der daraus berechnete Verbrauch in kWh, wie er auf der Rechnung steht.
VolumeRow = tuple[DateRange, SplitCalculation, Decimal | None]


def find_volume_splits(lines: Sequence[str]) -> list[VolumeRow]:
    """Abschnitte einer Gasrechnung: Zeitraum, Abrechnungsvolumen (m³), Zustandszahl, Brennwert.

    Die Spalten stehen je Versorger anders; erkannt werden die beiden Faktoren
    an ihrer Größe (Zustandszahl um 1 mit vier Nachkommastellen, Brennwert
    zwischen 8 und 14), das Volumen ist die Zahl direkt davor. Beispiele::

        1053045 01.06.2024 30.06.2024 67.819 67.935 S 116 0,9626 11,570
        05.07.2025 - 31.12.2025 71.555 73.069 1) 1.514 11,630 0,9626 16.949 kWh
    """
    result: list[VolumeRow] = []
    for line in lines:
        match = re.search(rf"({_DATE})\s*[-–]?\s*({_DATE})\s+(.+)$", line)
        if not match:
            continue
        start, end = parse_date(match.group(1)), parse_date(match.group(2))
        rest = re.sub(r"(?<![\d.,])\d\)", " ", match.group(3))  # Fußnoten wie "1)"
        tokens = _NUMBER_RE.findall(rest)
        values = [to_decimal(token) for token in tokens]
        condition_at = next(
            (i for i, t in enumerate(tokens) if re.fullmatch(r"[01],\d{3,5}", t) and Decimal("0.8") <= values[i] <= Decimal("1.1")),  # type: ignore[operator]
            -1,
        )
        if condition_at < 0:
            continue
        neighbours = [i for i in (condition_at - 1, condition_at + 1) if 0 <= i < len(tokens)]
        calorific_at = next(
            (i for i in neighbours if re.fullmatch(r"\d{1,2},\d{1,3}", tokens[i]) and Decimal(8) <= values[i] <= Decimal(14)),  # type: ignore[operator]
            -1,
        )
        volume_at = min(condition_at, calorific_at) - 1
        if calorific_at < 0 or volume_at < 0 or not (start and end and end >= start):
            continue
        volume, condition, calorific = values[volume_at], values[condition_at], values[calorific_at]
        if not (volume and condition and calorific):
            continue
        stated: Decimal | None = None
        if energy := re.search(rf"({_NUMBER})\s*kWh\s*$", rest):
            stated = to_decimal(energy.group(1))
        result.append((DateRange(start, end), SplitCalculation(volume, condition, calorific), stated))
    return result


def _unit(raw: str) -> str:
    return "kWh" if raw.lower() == "kwh" else "m³"


# ---------------------------------------------------------------------- #
# Gelernte Fundstellen
# ---------------------------------------------------------------------- #
def _value_at(line: str, kind: str, index: int) -> date | Decimal | None:
    values: list = dates_in(line) if kind == "date" else numbers_in(line)  # type: ignore[type-arg]
    return values[index] if 0 <= index < len(values) else None


def apply_anchor(lines: Sequence[str], anchor: FieldAnchor, kind: str) -> date | Decimal | None:
    """Sucht die gelernte Zeile und liefert den Wert an der gelernten Stelle (``None``, wenn es sie nicht gibt)."""
    for line in lines:
        if line_pattern(line) == anchor.pattern:
            value = _value_at(line, kind, anchor.index)
            if value is not None:
                return value
    return None


def learn_anchor(lines: Sequence[str], value: date | Decimal, kind: str) -> FieldAnchor | None:
    """Merkt sich die aussagekräftigste Zeile, in der ``value`` vorkommt.

    Steht der Wert mehrfach im Beleg (der Rechnungsbetrag etwa auf der ersten
    und auf der letzten Seite), gewinnt die Zeile mit den meisten echten
    Wörtern - "Rechnungsbetrag 787,08 Euro" schlägt ein nacktes "787,08 Euro".
    """
    best: tuple[int, FieldAnchor] | None = None
    for line in lines:
        values: list = dates_in(line) if kind == "date" else numbers_in(line)  # type: ignore[type-arg]
        for index, candidate in enumerate(values):
            if candidate != value:
                continue
            pattern = line_pattern(line)
            words = [w for w in re.findall(r"[a-zäöüß][a-zäöüß\-]+", pattern) if w not in _FILLER]
            if not words:
                continue
            score = len(words)
            if best is None or score > best[0]:
                best = (score, FieldAnchor(pattern, index))
    return best[1] if best else None


# ---------------------------------------------------------------------- #
# Alles zusammen
# ---------------------------------------------------------------------- #
def read_document(
    text: str,
    cost_type_names: Sequence[str],
    profile: DocumentProfile | None = None,
    source: str = "pdf",
) -> DocumentDraft:
    """Liest einen Beleg aus und gibt einen Vorschlag für die Rechnung zurück.

    Args:
        text: Text des Belegs.
        cost_type_names: Namen der Kostenarten, aus denen gewählt werden darf.
        profile: Gelerntes Profil des Absenders - seine Fundstellen und
            gemerkten Werte haben Vorrang vor den allgemeinen Regeln.
        source: "pdf" oder "ocr" (nur zur Anzeige).
    """
    lines = split_lines(text)
    issuer_key, issuer_name = identify_issuer(text)
    draft = DocumentDraft(text=text, source=source, issuer_key=issuer_key, issuer_name=issuer_name, profile=profile)
    draft.invoice_date = find_invoice_date(lines)
    draft.period = find_period(lines)
    draft.amount = find_amount(lines)
    draft.consumption, draft.consumption_unit = find_consumption(lines)
    draft.vat_percent = find_vat(lines)
    draft.working_price = find_working_price(lines)
    draft.base_price_per_year = find_base_price(lines)
    draft.cost_type_name = find_cost_type("\n".join(lines), cost_type_names)
    draft.description = readable_name(issuer_name)

    if profile is not None:
        _apply_profile(draft, lines, profile)
    draft.splits = _build_splits(draft, lines)
    return draft


def _apply_profile(draft: DocumentDraft, lines: Sequence[str], profile: DocumentProfile) -> None:
    """Gelerntes übernehmen: Fundstellen vor allgemeinen Regeln, gemerkte Werte vor Vermutungen."""
    found: dict[str, date | Decimal] = {}
    for field_name, anchor in profile.anchors.items():
        kind = "date" if field_name in ("invoice_date", "period_start", "period_end") else "number"
        value = apply_anchor(lines, anchor, kind)
        if value is not None:
            found[field_name] = value
            draft.learned_fields.append(field_name)
    if isinstance(found.get("invoice_date"), date):
        draft.invoice_date = found["invoice_date"]  # type: ignore[assignment]
    start = found.get("period_start", draft.period.start if draft.period else None)
    end = found.get("period_end", draft.period.end if draft.period else None)
    if isinstance(start, date) and isinstance(end, date) and end >= start:
        draft.period = DateRange(start, end)
    if isinstance(found.get("amount"), Decimal):
        draft.amount = Money.from_decimal(found["amount"])  # type: ignore[arg-type]
    if isinstance(found.get("consumption"), Decimal):
        draft.consumption = found["consumption"]  # type: ignore[assignment]

    draft.cost_type_id = profile.cost_type_id
    draft.allocation_key = profile.allocation_key
    if profile.description:
        draft.description = profile.description
    if profile.consumption_unit:
        draft.consumption_unit = profile.consumption_unit
    if draft.vat_percent is None and profile.vat_percent is not None:
        draft.vat_percent = profile.vat_percent


def _invoice_style_gross(
    calc: SplitCalculation,
    period: DateRange,
    used: Decimal,
    work_net: Decimal | None = None,
    base_net: Decimal | None = None,
) -> Decimal | None:
    """Bruttokosten eines Abschnitts so gerechnet, wie Versorger es auf der Rechnung tun.

    Arbeits- und Grundpreis-Zeile werden je für sich auf Cent gerundet, erst
    die Summe bekommt die Umsatzsteuer. Weist die Rechnung den Nettobetrag
    einer Zeile für genau diesen Abschnitt aus (``work_net``/``base_net``),
    gilt dieser - er ist robuster als ein Preis, den die Texterkennung
    vielleicht verstümmelt hat ("151 €/Jahr" statt "151,20 €/Jahr").
    """
    if not calc.has_prices:
        return None
    cent = Decimal("0.01")
    work = work_net if work_net is not None else (used * (calc.working_price or Decimal(0))).quantize(cent, rounding=ROUND_HALF_UP)
    base = base_net if base_net is not None else (
        ((calc.base_price or Decimal(0)) * year_fraction(period)).quantize(cent, rounding=ROUND_HALF_UP)
    )
    return (work + base) * (Decimal(1) + (calc.vat_percent or Decimal(0)) / Decimal(100))


def _build_splits(draft: DocumentDraft, lines: Sequence[str]) -> list[InvoiceSplit]:
    """Splits aus Zwischenablesungen bzw. Gas-Abschnitten - nur mit Preisen, sonst fehlen die Beträge.

    Die Beträge werden aus den Preisen berechnet und - wie im Split-Dialog -
    auf den Rechnungsbetrag abgestimmt (Rundungsdifferenzen bis 10 Cent).
    """
    if draft.working_price is None and draft.base_price_per_year is None:
        return []
    work, base = working_prices(lines), base_prices(lines)

    def prices(period: DateRange) -> dict[str, Decimal | None]:
        # Ändern sich die Preise im Abrechnungszeitraum, gilt je Abschnitt der Preis seines Zeitraums.
        return {
            "working_price": price_for(period, work, draft.working_price),
            "base_price": price_for(period, base, draft.base_price_per_year),
            "vat_percent": draft.vat_percent,
        }

    pieces: list[tuple[DateRange, Decimal, SplitCalculation]] = []
    volume_rows = find_volume_splits(lines)
    if len(volume_rows) >= 2:
        for period, calc, stated in volume_rows:
            calc = SplitCalculation(calc.volume, calc.condition_number, calc.calorific_value, **prices(period))
            energy = stated or calc.energy()
            if energy is None:
                return []
            pieces.append((period, energy, calc))
    else:
        for period, used in find_reading_splits(lines, draft.period):
            pieces.append((period, used, SplitCalculation(**prices(period))))
    if len(pieces) < 2:
        return []

    periods = [period for period, _used, _calc in pieces]
    work_nets = allocate_stated_nets(periods, [used for _p, used, _c in pieces], work)
    base_nets = allocate_stated_nets(periods, [Decimal(period.days) for period in periods], base)
    exact = [
        _invoice_style_gross(
            calc, period, used,
            work_nets[i] if calc.working_price else None,
            base_nets[i] if calc.base_price else None,
        )
        for i, (period, used, calc) in enumerate(pieces)
    ]
    if any(value is None for value in exact):
        return []
    amounts = fit_amounts([value for value in exact if value is not None], draft.amount)
    return [
        InvoiceSplit(period, amount, used, calc)
        for (period, used, calc), amount in zip(pieces, amounts)
    ]
