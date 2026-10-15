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
from decimal import Decimal
from typing import Sequence

from models.cost import InvoiceSplit
from models.document import DocumentDraft, DocumentProfile, FieldAnchor
from models.tariff import SplitCalculation, fit_amounts
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
    company = _COMPANY_RE.search(normalized)
    name = " ".join(company.group(1).split()) if company else ""
    if vat_id := _VAT_ID_RE.search(normalized):
        return "ust:" + re.sub(r"\s", "", vat_id.group(1)).upper(), name
    if domain := _DOMAIN_RE.search(normalized):
        return "web:" + domain.group(1).lower(), name
    if name:
        return "name:" + name.casefold(), name
    return "", ""


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
    for keywords in (("gesamtverbrauch",), ("verbrauch", "abrechnungsmenge")):
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
    """Umsatzsteuersatz, z. B. aus "Umsatzsteuer 7 % von 735,59 Euro"."""
    for line in lines:
        if re.search(r"umsatzsteuer|mwst|ust\b", line, re.I):
            if match := re.search(r"(\d{1,2}(?:,\d+)?)\s*%", line):
                return to_decimal(match.group(1))
    return None


def find_working_price(lines: Sequence[str]) -> Decimal | None:
    """Arbeits- bzw. Mengenpreis netto je m³ oder kWh (Cent-Angaben werden umgerechnet)."""
    for line in lines:
        if not re.search(r"mengenpreis|arbeitspreis|verbrauchspreis", line, re.I):
            continue
        if match := re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*/\s*{_UNIT}", line, re.I):
            return to_decimal(match.group(1))
        if match := re.search(rf"({_NUMBER})\s*(?:ct|cent)\s*/\s*{_UNIT}", line, re.I):
            value = to_decimal(match.group(1))
            return value / 100 if value is not None else None
    return None


def find_base_price(lines: Sequence[str]) -> Decimal | None:
    """Grundpreis netto auf ein Jahr gerechnet ("153,00 Euro / 365 Tage", "… EUR/Jahr", "… €/Monat")."""
    for line in lines:
        if "grundpreis" not in line.lower():
            continue
        if match := re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*/\s*(\d+)\s*tag", line, re.I):
            value, days = to_decimal(match.group(1)), int(match.group(2))
            if value is not None and days:
                return value * 365 / days
        if match := re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*/\s*jahr", line, re.I):
            return to_decimal(match.group(1))
        if match := re.search(rf"({_NUMBER})\s*(?:euro|eur|€)\s*/\s*monat", line, re.I):
            value = to_decimal(match.group(1))
            return value * 12 if value is not None else None
    return None


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


def find_volume_splits(lines: Sequence[str]) -> list[tuple[DateRange, SplitCalculation]]:
    """Abschnitte einer Gasrechnung: Zeitraum, Abrechnungsvolumen (m³), Zustandszahl, Brennwert.

    Beispielzeile: "1053045 01.06.2024 30.06.2024 67.819 67.935 S 116 0,9626 11,570"
    """
    pattern = re.compile(
        rf"({_DATE})\s+({_DATE})\s+.*?(?<![\d.,])({_NUMBER})\s+(0,\d{{3,4}})\s+(\d{{1,2}},\d{{1,3}})\s*$"
    )
    result: list[tuple[DateRange, SplitCalculation]] = []
    for line in lines:
        if match := pattern.search(line):
            start, end = parse_date(match.group(1)), parse_date(match.group(2))
            volume, condition, calorific = (to_decimal(match.group(i)) for i in (3, 4, 5))
            if start and end and end >= start and volume and condition and calorific:
                result.append((DateRange(start, end), SplitCalculation(volume, condition, calorific)))
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


def _build_splits(draft: DocumentDraft, lines: Sequence[str]) -> list[InvoiceSplit]:
    """Splits aus Zwischenablesungen bzw. Gas-Abschnitten - nur mit Preisen, sonst fehlen die Beträge.

    Die Beträge werden aus den Preisen berechnet und - wie im Split-Dialog -
    auf den Rechnungsbetrag abgestimmt (Rundungsdifferenzen bis 10 Cent).
    """
    if draft.working_price is None and draft.base_price_per_year is None:
        return []
    prices = {
        "working_price": draft.working_price,
        "base_price": draft.base_price_per_year,
        "vat_percent": draft.vat_percent,
    }
    pieces: list[tuple[DateRange, Decimal, SplitCalculation]] = []
    volume_rows = find_volume_splits(lines)
    if len(volume_rows) >= 2:
        for period, calc in volume_rows:
            calc = SplitCalculation(calc.volume, calc.condition_number, calc.calorific_value, **prices)
            energy = calc.energy()
            if energy is None:
                return []
            pieces.append((period, energy, calc))
    else:
        for period, used in find_reading_splits(lines, draft.period):
            pieces.append((period, used, SplitCalculation(**prices)))
    if len(pieces) < 2:
        return []

    exact = [calc.gross_cost(period, used) for period, used, calc in pieces]
    if any(value is None for value in exact):
        return []
    amounts = fit_amounts([value for value in exact if value is not None], draft.amount)
    return [
        InvoiceSplit(period, amount, used, calc)
        for (period, used, calc), amount in zip(pieces, amounts)
    ]
