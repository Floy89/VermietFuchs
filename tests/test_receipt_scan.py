"""Tests für die optionale Belegerkennung.

Ein echtes KI-Modell steht in Tests nicht zur Verfügung. Stattdessen läuft
ein winziger HTTP-Server, der sich wie Ollama verhält. Geprüft wird damit
alles, was die Anwendung selbst verantwortet: Statusprüfung, Aufbau der
Anfrage und die vorsichtige Auswertung der Antwort.
"""

from __future__ import annotations

import base64
import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Iterator

import pytest

from integrations.ollama_client import OllamaReceiptScanner
from models.exceptions import ScanError, ValidationError
from models.value_objects import DateRange, Money
from services.receipt_scan_service import ReceiptScanService, parse_suggestion
from utils.config_manager import ConfigManager, ReceiptScanConfig

COST_TYPES = ["Grundsteuer", "Wasserversorgung", "Beleuchtung (Allgemeinstrom)"]


class _FakeOllama:
    """Minimaler Ollama-Ersatz: merkt sich die letzte Anfrage und antwortet wie vorgegeben."""

    def __init__(self) -> None:
        self.models = ["qwen3-vl:8b-instruct"]
        self.answer: Any = {}
        self.thinking = ""
        self.last_request: dict[str, Any] = {}
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 (Name von http.server vorgegeben)
                self._send({"models": [{"name": name} for name in fake.models]})

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                fake.last_request = json.loads(self.rfile.read(length))
                content = fake.answer if isinstance(fake.answer, str) else json.dumps(fake.answer)
                message = {"role": "assistant", "content": content}
                if fake.thinking:
                    message["thinking"] = fake.thinking
                self._send({"message": message})

            def _send(self, payload: dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:  # Testausgabe nicht zumüllen
                pass

        self._server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self._server.server_port}"
        threading.Thread(target=self._server.serve_forever, daemon=True).start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()


@pytest.fixture()
def ollama() -> Iterator[_FakeOllama]:
    fake = _FakeOllama()
    yield fake
    fake.stop()


@pytest.fixture()
def service(ollama: _FakeOllama, tmp_path: Path) -> ReceiptScanService:
    scan_service = ReceiptScanService(ConfigManager(tmp_path / "config.json"), OllamaReceiptScanner)
    scan_service.save_settings(ReceiptScanConfig(url=ollama.url, model="qwen3-vl:8b-instruct"))
    return scan_service


class TestStatus:
    def test_not_set_up_is_reported_not_raised(self, tmp_path: Path) -> None:
        """Ohne Ollama: kein Fehler, nur 'nicht verfügbar' - die App läuft normal weiter."""
        scan_service = ReceiptScanService(ConfigManager(tmp_path / "config.json"), OllamaReceiptScanner)
        scan_service.save_settings(ReceiptScanConfig(url="http://127.0.0.1:9", model="qwen3-vl:8b-instruct"))
        status = scan_service.status()
        assert not status.available and "nicht erreichbar" in status.message

    def test_ready_when_model_is_installed(self, service: ReceiptScanService) -> None:
        status = service.status()
        assert status.available and "qwen3-vl:8b-instruct" in status.message

    def test_missing_model_names_the_pull_command(self, service: ReceiptScanService, ollama: _FakeOllama) -> None:
        ollama.models = ["llama3:latest"]
        status = service.status()
        assert not status.available and "ollama pull qwen3-vl:8b-instruct" in status.message

    def test_settings_are_validated_and_stored(self, tmp_path: Path) -> None:
        config = ConfigManager(tmp_path / "config.json")
        scan_service = ReceiptScanService(config, OllamaReceiptScanner)
        assert scan_service.settings() == ReceiptScanConfig()  # Standardwerte ohne Datei
        with pytest.raises(ValidationError):
            scan_service.save_settings(ReceiptScanConfig(url="localhost:11434", model="x"))
        with pytest.raises(ValidationError):
            scan_service.save_settings(ReceiptScanConfig(url="http://localhost:11434", model=" "))
        scan_service.save_settings(ReceiptScanConfig(url="http://localhost:11434/", model="qwen3-vl:4b-instruct"))
        assert config.receipt_scan_config() == ReceiptScanConfig("http://localhost:11434", "qwen3-vl:4b-instruct")
        # Die Benutzerliste in derselben Datei bleibt unberührt.
        assert config.list_users() == []

    def test_local_address_detection(self) -> None:
        assert OllamaReceiptScanner.is_local("http://localhost:11434")
        assert OllamaReceiptScanner.is_local("http://127.0.0.1:11434")
        assert not OllamaReceiptScanner.is_local("https://ki.example.org")


class TestScan:
    def test_scan_sends_images_and_returns_suggestion(self, service: ReceiptScanService, ollama: _FakeOllama) -> None:
        ollama.answer = {
            "rechnungssteller": "Stadtwerke Musterstadt GmbH",
            "rechnungsnummer": "RE-2025-0815",
            "rechnungsdatum": "2025-06-06",
            "zeitraum_von": "2024-06-01",
            "zeitraum_bis": "2025-05-31",
            "gesamtbetrag_brutto": "312,60",
            "kostenart": "Beleuchtung (Allgemeinstrom)",
        }
        suggestion = service.scan([b"seite-1", b"seite-2"], COST_TYPES)

        assert suggestion.total_amount == Money.from_decimal("312.60")
        assert suggestion.period == DateRange(date(2024, 6, 1), date(2025, 5, 31))
        assert suggestion.cost_type_name == "Beleuchtung (Allgemeinstrom)"
        assert suggestion.description == "Stadtwerke Musterstadt GmbH, Rechnung RE-2025-0815"

        request = ollama.last_request
        assert request["model"] == "qwen3-vl:8b-instruct" and request["stream"] is False
        assert [base64.b64decode(i) for i in request["messages"][0]["images"]] == [b"seite-1", b"seite-2"]
        # Die Kostenart ist auf den Katalog beschränkt - das Modell kann keine erfinden.
        assert request["format"]["properties"]["kostenart"]["enum"] == [*COST_TYPES, ""]
        # ... und die Liste steht auch in der Anweisung, sonst wählt es blind.
        assert all(f"- {name}" in request["messages"][0]["content"] for name in COST_TYPES)
        # Der Betrag wird als Text verlangt, damit deutsche Kommas keine Cent kosten.
        assert request["format"]["properties"]["gesamtbetrag_brutto"] == {"type": "string"}

    def test_garbage_answer_raises_scan_error(self, service: ReceiptScanService, ollama: _FakeOllama) -> None:
        ollama.answer = "Das ist leider kein JSON."
        with pytest.raises(ScanError):
            service.scan([b"seite"], COST_TYPES)

    def test_thinking_variant_is_explained(self, service: ReceiptScanService, ollama: _FakeOllama) -> None:
        """Ein 'Thinking'-Modell denkt nur nach und antwortet nicht - der Benutzer erfährt, was zu tun ist."""
        ollama.answer = ""
        ollama.thinking = "<think>Okay, let's tackle this step by step ..."
        with pytest.raises(ScanError, match="-instruct"):
            service.scan([b"seite"], COST_TYPES)
        assert ollama.last_request["think"] is False

    def test_nothing_recognised_raises_scan_error(self, service: ReceiptScanService, ollama: _FakeOllama) -> None:
        ollama.answer = {
            "rechnungssteller": "", "rechnungsnummer": "", "rechnungsdatum": "",
            "zeitraum_von": "", "zeitraum_bis": "", "gesamtbetrag_brutto": "", "kostenart": "",
        }
        with pytest.raises(ScanError):
            service.scan([b"seite"], COST_TYPES)

    def test_no_pages_raises_scan_error(self, service: ReceiptScanService) -> None:
        with pytest.raises(ScanError):
            service.scan([], COST_TYPES)


class TestParsing:
    """Unklare Angaben werden verworfen, nicht geraten."""

    def test_german_formats_are_understood(self) -> None:
        suggestion = parse_suggestion(
            {
                "rechnungssteller": "  Wasserwerke Musterstadt\nAG ",
                "rechnungsdatum": "26.09.2024",
                "zeitraum_von": "01.01.2024",
                "zeitraum_bis": "13.09.2024",
                "gesamtbetrag_brutto": "1.512,40 €",
                "kostenart": "wasserversorgung",
            },
            COST_TYPES,
        )
        assert suggestion.vendor == "Wasserwerke Musterstadt AG"
        assert suggestion.invoice_date == date(2024, 9, 26)
        assert suggestion.period == DateRange(date(2024, 1, 1), date(2024, 9, 13))
        assert suggestion.total_amount == Money.from_decimal("1512.40")
        assert suggestion.cost_type_name == "Wasserversorgung"  # Schreibweise aus dem Katalog

    def test_amount_spellings(self) -> None:
        def amount(raw: object) -> Money | None:
            return parse_suggestion({"gesamtbetrag_brutto": raw}, COST_TYPES).total_amount

        assert amount("312,60") == Money.from_decimal("312.60")
        assert amount("312.60") == Money.from_decimal("312.60")  # Punkt als Dezimaltrenner
        assert amount("4.484,66 EUR") == Money.from_decimal("4484.66")
        assert amount(533.55) == Money.from_decimal("533.55")  # Zahl statt Text ist auch in Ordnung
        assert amount("") is None and amount("unbekannt") is None

    def test_implausible_values_are_dropped(self) -> None:
        suggestion = parse_suggestion(
            {
                "rechnungssteller": "Firma",
                "zeitraum_von": "2025-12-31",
                "zeitraum_bis": "2025-01-01",  # Ende vor Beginn
                "gesamtbetrag_brutto": -5,
                "kostenart": "Erfundene Kostenart",
                "rechnungsdatum": "irgendwann",
            },
            COST_TYPES,
        )
        assert suggestion.period is None
        assert suggestion.total_amount is None
        assert suggestion.cost_type_name == ""
        assert suggestion.invoice_date is None
        assert not suggestion.is_empty  # der Rechnungssteller wurde erkannt
