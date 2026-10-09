"""Optionale Belegerkennung: Rechnungen (PDF/Foto) von einem lokalen KI-Modell auslesen lassen.

Die Erkennung ist bewusst **kein fester Bestandteil** der Anwendung. Sie
funktioniert nur, wenn der Benutzer selbst ein lokales Modell eingerichtet
hat (siehe ``ui/resources/belegerkennung.md``). Ohne diese Einrichtung meldet
:meth:`ReceiptScanService.status` "nicht verfügbar", und die Oberfläche zeigt
statt der Scan-Schaltfläche nur einen Hinweis.

Schichten:

* Dieser Service kennt nur das Protokoll :class:`ReceiptScanner` - er weiß
  nicht, welches Programm die Bilder tatsächlich auswertet.
* Die konkrete Anbindung (derzeit Ollama) liegt in ``integrations/``. Ein
  anderes Modell oder Programm anzubinden heißt: eine weitere Klasse mit
  denselben zwei Methoden schreiben.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Mapping, Protocol, Sequence

from models.exceptions import ScanError, ValidationError
from models.enums import MeterType
from models.receipt import MeterSuggestion, ReceiptSuggestion
from models.value_objects import DateRange, Money
from utils.config_manager import ConfigManager, ReceiptScanConfig

logger = logging.getLogger(__name__)

#: So lange gilt ein Ergebnis der Verfügbarkeitsprüfung, bevor im Hintergrund neu geprüft wird.
STATUS_MAX_AGE_SECONDS = 20.0

# Feldnamen der Modell-Antwort. Deutsch, weil das Modell deutsche Belege liest
# und sprechende deutsche Namen seine Trefferquote erhöhen.
_F_VENDOR = "rechnungssteller"
_F_NUMBER = "rechnungsnummer"
_F_DATE = "rechnungsdatum"
_F_FROM = "zeitraum_von"
_F_TO = "zeitraum_bis"
_F_AMOUNT = "gesamtbetrag_brutto"
_F_TYPE = "kostenart"

_INSTRUCTIONS = """Du liest einen Beleg (Rechnung oder Gebührenbescheid) für ein Mietshaus aus.
Gib ausschließlich die verlangten Felder zurück und erfinde nichts.

- rechnungssteller: Name des Unternehmens oder der Behörde, die den Beleg ausgestellt hat.
- rechnungsnummer: Rechnungs-, Bescheid- oder Belegnummer.
- rechnungsdatum: Datum des Belegs im Format JJJJ-MM-TT.
- zeitraum_von / zeitraum_bis: Leistungs-, Liefer- oder Abrechnungszeitraum im Format JJJJ-MM-TT.
- gesamtbetrag_brutto: Gesamtbetrag der Rechnung in Euro inklusive Umsatzsteuer, genau so
  geschrieben wie auf dem Beleg, mit Cent (z. B. "1.234,56"). Nicht ein noch zu zahlender
  Restbetrag nach Abzug von Abschlägen.
- kostenart: der am besten passende Eintrag aus der Liste unten, exakt so geschrieben.

Ist eine Angabe auf dem Beleg nicht zu finden: leere Zeichenkette.

Mögliche Kostenarten:
"""


_M_VALUE = "zaehlerstand"
_M_NUMBER = "zaehlernummer"

_METER_INSTRUCTIONS = """Du liest das Foto eines Verbrauchszählers ab ({label}, Einheit {unit}).
Gib ausschließlich die verlangten Felder zurück und erfinde nichts.

- zaehlerstand: der aktuell angezeigte Zählerstand, Ziffer für Ziffer so wie auf dem Zählwerk
  bzw. der Anzeige. Nachkommastellen (oft rot unterlegt oder hinter einem Komma) mit Komma
  abtrennen, z. B. "1234,567". Führende Nullen weglassen. Keine Einheit.
  Nicht die Zählernummer, nicht das Eichjahr und keine andere aufgedruckte Zahl.
- zaehlernummer: die aufgedruckte Geräte- bzw. Zählernummer.

Ist eine Angabe nicht sicher zu lesen: leere Zeichenkette.
"""


@dataclass(frozen=True)
class ScannerStatus:
    """Ergebnis der Verfügbarkeitsprüfung.

    Attributes:
        available: ``True``, wenn sofort gescannt werden kann.
        message: Deutscher Text für die Oberfläche (Erfolg oder Grund des Fehlens).
    """

    available: bool
    message: str


class ReceiptScanner(Protocol):
    """Anbindung an ein Programm, das Bilder mit einem KI-Modell auswertet."""

    def check(self) -> ScannerStatus:
        """Prüft, ob Programm und Modell betriebsbereit sind (schnell, ohne Ausnahme)."""
        ...

    def extract(self, images: Sequence[bytes], instructions: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        """Lässt das Modell die Bilder auswerten.

        Args:
            images: Seiten des Belegs als PNG- oder JPEG-Daten.
            instructions: Arbeitsanweisung für das Modell.
            schema: JSON-Schema, dem die Antwort entsprechen muss.

        Returns:
            Die Antwort des Modells als Wörterbuch.

        Raises:
            ScanError: Wenn das Modell nicht erreichbar ist oder Unbrauchbares liefert.
        """
        ...


#: Erzeugt aus den Einstellungen die konkrete Anbindung.
ScannerFactory = Callable[[ReceiptScanConfig], ReceiptScanner]


class ReceiptScanService:
    """Anwendungsfälle der optionalen Belegerkennung."""

    def __init__(self, config: ConfigManager, scanner_factory: ScannerFactory) -> None:
        """
        Args:
            config: Zugriff auf die ``config.json`` (dort stehen Adresse und Modell).
            scanner_factory: Baut aus den Einstellungen die Anbindung.
        """
        self._config = config
        self._scanner_factory = scanner_factory
        # Zuletzt ermittelter Status für die gespeicherten Einstellungen (siehe quick_status).
        self._status_lock = threading.Lock()
        self._last_status: ScannerStatus | None = None
        self._last_status_at = 0.0
        self._status_check_running = False

    # ------------------------------------------------------------------ #
    # Einstellungen und Status
    # ------------------------------------------------------------------ #
    def settings(self) -> ReceiptScanConfig:
        """Die gespeicherten Verbindungseinstellungen."""
        return self._config.receipt_scan_config()

    def save_settings(self, settings: ReceiptScanConfig) -> None:
        """Speichert die Verbindungseinstellungen.

        Raises:
            ValidationError: Bei leerer oder offensichtlich ungültiger Eingabe.
        """
        url = settings.url.strip().rstrip("/")
        model = settings.model.strip()
        if not url.startswith(("http://", "https://")):
            raise ValidationError("Die Adresse muss mit http:// oder https:// beginnen.")
        if not model:
            raise ValidationError("Bitte den Namen des Modells angeben.")
        self._config.set_receipt_scan_config(ReceiptScanConfig(url=url, model=model))
        with self._status_lock:
            self._last_status = None  # gilt für die alten Einstellungen

    def status(self, settings: ReceiptScanConfig | None = None) -> ScannerStatus:
        """Prüft, ob die Belegerkennung nutzbar ist.

        Args:
            settings: Zu prüfende Einstellungen; Standard sind die gespeicherten.
                So lässt sich eine Eingabe testen, bevor sie gespeichert wird.
        """
        saved = self.settings()
        status = self._check(settings or saved)
        if settings is None or settings == saved:
            self._remember(status)
        return status

    def quick_status(self) -> ScannerStatus:
        """Der zuletzt bekannte Status - sofort, ohne auf das Netzwerk zu warten.

        Die eigentliche Prüfung fragt den Ollama-Dienst an. Läuft der nicht,
        dauert allein die Feststellung "nicht erreichbar" unter Windows
        mehrere Sekunden. Dialoge fragen deshalb hier: Sie bekommen sofort das
        letzte Ergebnis, und ist es älter als :data:`STATUS_MAX_AGE_SECONDS`,
        wird im Hintergrund neu geprüft. Beim allerersten Aufruf ist noch
        nichts bekannt - dann gilt die Erkennung zunächst als nicht verfügbar.

        Folge: Wird Ollama bei laufender Anwendung gestartet, erscheint die
        Scan-Schaltfläche erst beim nächsten Öffnen eines Dialogs nach der
        Hintergrundprüfung (oder sofort nach "Verbindung prüfen" in der Einrichtung).
        """
        with self._status_lock:
            status = self._last_status
            is_stale = status is None or time.monotonic() - self._last_status_at > STATUS_MAX_AGE_SECONDS
            start_check = is_stale and not self._status_check_running
            if start_check:
                self._status_check_running = True
        if start_check:
            # Die Einstellungen hier lesen, nicht im Thread: Die config.json
            # gehört dem Hauptthread.
            settings = self.settings()
            threading.Thread(
                target=self._check_in_background, args=(settings,), name="Belegerkennung-Status", daemon=True
            ).start()
        return status or ScannerStatus(False, "Die Verfügbarkeit der Belegerkennung wird noch geprüft.")

    def _check_in_background(self, settings: ReceiptScanConfig) -> None:
        """Prüft die Verfügbarkeit in einem eigenen Thread und merkt sich das Ergebnis."""
        try:
            self._remember(self._check(settings))
        finally:
            with self._status_lock:
                self._status_check_running = False

    def _check(self, settings: ReceiptScanConfig) -> ScannerStatus:
        """Fragt die Anbindung nach ihrem Status; Fehler werden zu "nicht verfügbar"."""
        try:
            return self._scanner_factory(settings).check()
        except Exception as exc:  # Die Prüfung darf die Anwendung nie stören.
            logger.warning("Statusprüfung der Belegerkennung fehlgeschlagen: %s", exc)
            return ScannerStatus(False, f"Die Belegerkennung ist nicht verfügbar ({exc}).")

    def _remember(self, status: ScannerStatus) -> None:
        with self._status_lock:
            self._last_status = status
            self._last_status_at = time.monotonic()

    # ------------------------------------------------------------------ #
    # Scannen
    # ------------------------------------------------------------------ #
    def scan(self, images: Sequence[bytes], cost_type_names: Sequence[str]) -> ReceiptSuggestion:
        """Liest einen Beleg aus und gibt einen Vorschlag für eine Kostenposition zurück.

        Args:
            images: Seiten des Belegs als Bilddaten.
            cost_type_names: Namen der Kostenarten, aus denen das Modell wählen darf.

        Raises:
            ScanError: Wenn die Erkennung fehlschlägt oder nichts erkannt wurde.
        """
        if not images:
            raise ScanError("Der Beleg enthält keine lesbaren Seiten.")

        scanner = self._scanner_factory(self.settings())
        # Die Liste der Kostenarten muss im Text stehen: Das Schema beschränkt
        # zwar die Antwort, das Modell bekommt es aber nicht zu lesen. Ohne
        # die Liste im Text würde es blind einen erlaubten Eintrag wählen.
        instructions = _INSTRUCTIONS + "\n".join(f"- {name}" for name in cost_type_names)
        data = scanner.extract(images, instructions, self._schema(cost_type_names))
        suggestion = parse_suggestion(data, cost_type_names)
        logger.info(
            "Beleg ausgelesen: Betrag %s, Zeitraum %s, Kostenart '%s'",
            suggestion.total_amount,
            suggestion.period,
            suggestion.cost_type_name,
        )
        if suggestion.is_empty:
            raise ScanError("Auf dem Beleg konnten keine verwertbaren Angaben erkannt werden.")
        return suggestion

    def scan_meter(self, images: Sequence[bytes], meter_type: MeterType) -> MeterSuggestion:
        """Liest den Zählerstand vom Foto eines Zählers ab.

        Args:
            images: Das Foto als Bilddaten (in der Regel genau eines).
            meter_type: Zählerart - sagt dem Modell, was es vor sich hat.

        Raises:
            ScanError: Wenn die Erkennung fehlschlägt oder kein Stand lesbar war.
        """
        if not images:
            raise ScanError("Das Foto konnte nicht gelesen werden.")

        scanner = self._scanner_factory(self.settings())
        text = {"type": "string"}
        schema = {
            "type": "object",
            # Text statt Zahl - aus demselben Grund wie beim Rechnungsbetrag (Komma).
            "properties": {_M_VALUE: text, _M_NUMBER: text},
            "required": [_M_VALUE, _M_NUMBER],
        }
        instructions = _METER_INSTRUCTIONS.format(label=meter_type.label, unit=meter_type.unit)
        suggestion = parse_meter_suggestion(scanner.extract(images, instructions, schema))
        if suggestion is None:
            raise ScanError("Auf dem Foto konnte kein Zählerstand sicher gelesen werden.")
        logger.info("Zählerfoto ausgelesen: Stand %s, Zählernummer '%s'", suggestion.value, suggestion.meter_number)
        return suggestion

    @staticmethod
    def _schema(cost_type_names: Sequence[str]) -> dict[str, Any]:
        """JSON-Schema der erwarteten Antwort; die Kostenart ist auf den Katalog beschränkt."""
        text = {"type": "string"}
        return {
            "type": "object",
            "properties": {
                _F_VENDOR: text,
                _F_NUMBER: text,
                _F_DATE: text,
                _F_FROM: text,
                _F_TO: text,
                # Bewusst Text statt Zahl: In einem Zahlenfeld darf kein Komma
                # stehen. Liest das Modell "312,60" vom Beleg ab, würde die
                # Zahl nach "312" enden und die Cent gingen verloren.
                _F_AMOUNT: text,
                _F_TYPE: {"type": "string", "enum": [*cost_type_names, ""]},
            },
            "required": [_F_VENDOR, _F_NUMBER, _F_DATE, _F_FROM, _F_TO, _F_AMOUNT, _F_TYPE],
        }


# ---------------------------------------------------------------------- #
# Auswertung der Modell-Antwort (reine Funktionen, leicht testbar)
# ---------------------------------------------------------------------- #
def parse_suggestion(data: Mapping[str, Any], cost_type_names: Sequence[str]) -> ReceiptSuggestion:
    """Wandelt die rohe Modell-Antwort in einen geprüften Vorschlag um.

    Das Modell liefert nur Text und Zahlen. Hier wird alles, was nicht sauber
    zu deuten ist, verworfen statt geraten: ein unlesbares Datum wird zu
    ``None``, ein Zeitraum mit Ende vor Beginn entfällt, eine unbekannte
    Kostenart wird leer.
    """
    start = _parse_date(data.get(_F_FROM))
    end = _parse_date(data.get(_F_TO))
    period = DateRange(start, end) if start and end and end >= start else None

    by_folded_name = {name.casefold(): name for name in cost_type_names}
    cost_type_name = by_folded_name.get(str(data.get(_F_TYPE) or "").strip().casefold(), "")

    return ReceiptSuggestion(
        vendor=_clean_text(data.get(_F_VENDOR)),
        invoice_number=_clean_text(data.get(_F_NUMBER)),
        invoice_date=_parse_date(data.get(_F_DATE)),
        period=period,
        total_amount=_parse_amount(data.get(_F_AMOUNT)),
        cost_type_name=cost_type_name,
    )


def parse_meter_suggestion(data: Mapping[str, Any]) -> MeterSuggestion | None:
    """Wandelt die rohe Modell-Antwort zu einem Zählerfoto in einen geprüften Vorschlag um.

    Returns:
        Den Vorschlag - oder ``None``, wenn kein gültiger Zählerstand dabei ist.
    """
    value = _parse_quantity(data.get(_M_VALUE))
    if value is None:
        return None
    return MeterSuggestion(value=value, meter_number=_clean_text(data.get(_M_NUMBER))[:40])


def _parse_quantity(value: Any) -> Decimal | None:
    """Versteht Zählerstände wie ``"1.234,567"``, ``"01234,5 m³"`` oder ``1234.5``; Unlesbares ergibt ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        text = repr(value)
    else:
        # Nur Ziffern und Trennzeichen behalten: Einheiten und Leerzeichen fallen weg.
        # (Bewusst keine str.isdigit(): Das hält auch die "³" aus "m³" für eine Ziffer.)
        text = "".join(character for character in str(value) if character in "0123456789.,")
        if "," in text:
            text = text.replace(".", "").replace(",", ".")
    if not any(character.isdigit() for character in text):
        return None
    try:
        number = Decimal(text)
    except InvalidOperation:
        return None
    # Mehr als zehn Stellen hat kein Zählwerk - dann hat sich das Modell verrannt
    # (z. B. eine endlose Reihe Nullen) und der Wert wäre irreführend.
    if not number.is_finite() or number < 0 or number >= Decimal(10) ** 10:
        return None
    return number.quantize(Decimal("0.001"))


def _clean_text(value: Any) -> str:
    """Text ohne Zeilenumbrüche und Randleerzeichen, auf 120 Zeichen begrenzt."""
    return " ".join(str(value or "").split())[:120]


def _parse_date(value: Any) -> date | None:
    """Versteht ``JJJJ-MM-TT`` und ``TT.MM.JJJJ``; alles andere ergibt ``None``."""
    text = str(value or "").strip()
    for pattern in ("%Y-%m-%d", "%d.%m.%Y"):
        try:
            return datetime.strptime(text, pattern).date()
        except ValueError:
            continue
    return None


def _parse_amount(value: Any) -> Money | None:
    """Versteht Zahlen sowie Texte wie ``"1.512,40 €"``; 0 und Unlesbares ergeben ``None``."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        text = repr(value)
    else:
        text = str(value).replace("€", "").replace("EUR", "").replace(" ", "").strip()
        if "," in text:
            # Deutsche Schreibweise: Punkt = Tausender, Komma = Dezimaltrenner.
            text = text.replace(".", "").replace(",", ".")
    try:
        amount = Money.from_decimal(text)
    except ValidationError:
        return None
    return amount if amount.cents > 0 else None
