"""Anbindung der Belegerkennung an Ollama (https://ollama.com).

Ollama ist ein eigenständiges Programm, das KI-Modelle lokal ausführt und
über eine kleine HTTP-Schnittstelle auf dem eigenen Rechner anbietet
(Standard: ``http://localhost:11434``). Diese Klasse spricht ausschließlich
diese Schnittstelle an - mit Bordmitteln von Python, ohne Zusatzpakete.

Verwendete Endpunkte:

* ``GET  /api/tags`` - Liste der installierten Modelle (für die Statusprüfung)
* ``POST /api/chat`` - Bilder + Anweisung an das Modell; das Feld ``format``
  enthält ein JSON-Schema, an das sich die Antwort halten muss.
"""

from __future__ import annotations

import base64
import json
import logging
import socket
import urllib.error
import urllib.request
from typing import Any, Final, Mapping, Sequence
from urllib.parse import urlparse

from models.exceptions import ScanError
from services.receipt_scan_service import ScannerStatus
from utils.config_manager import ReceiptScanConfig

logger = logging.getLogger(__name__)

#: Zeitgrenze für die Abfrage der installierten Modelle bei der Statusprüfung.
_CHECK_TIMEOUT_SECONDS: Final[float] = 2.0
#: Zeitgrenze für die Vorprüfung "lauscht dort überhaupt ein Dienst?". Ohne sie
#: wartet Windows bei einem nicht gestarteten Ollama rund zwei Sekunden je
#: Adresse (IPv6 und IPv4), bevor es "nicht erreichbar" meldet.
_PROBE_TIMEOUT_LOCAL_SECONDS: Final[float] = 0.25
_PROBE_TIMEOUT_REMOTE_SECONDS: Final[float] = 2.0
#: Das Auslesen darf dauern: Auf Rechnern mit kleiner Grafikkarte rechnet ein
#: Teil des Modells auf dem Prozessor, und beim ersten Aufruf wird es erst geladen.
_SCAN_TIMEOUT_SECONDS: Final[float] = 900.0
#: Kontextgröße in Tokens. Der Ollama-Standard ist für mehrere Bildseiten zu klein.
_CONTEXT_TOKENS: Final[int] = 8192
#: Obergrenze für die Länge der Antwort. Die erwartete Antwort hat rund 150
#: Tokens; die Grenze verhindert, dass ein "abschweifendes" Modell minutenlang
#: weiterschreibt.
_MAX_ANSWER_TOKENS: Final[int] = 700
_LOCAL_HOSTS: Final[frozenset[str]] = frozenset({"localhost", "127.0.0.1", "::1"})


class OllamaReceiptScanner:
    """Wertet Belege über ein in Ollama installiertes Vision-Modell aus."""

    def __init__(self, settings: ReceiptScanConfig) -> None:
        """
        Args:
            settings: Adresse des Ollama-Dienstes und Name des Modells.
        """
        self._url = settings.url.rstrip("/")
        self._model = settings.model

    @staticmethod
    def is_local(url: str) -> bool:
        """``True``, wenn die Adresse auf den eigenen Rechner zeigt.

        Nur dann verlassen die Belege den Rechner garantiert nicht.
        """
        return (urlparse(url).hostname or "") in _LOCAL_HOSTS

    # ------------------------------------------------------------------ #
    # Protokoll ReceiptScanner
    # ------------------------------------------------------------------ #
    def check(self) -> ScannerStatus:
        """Prüft, ob Ollama läuft und das gewünschte Modell installiert ist."""
        unreachable = ScannerStatus(
            False, f"Ollama ist unter {self._url} nicht erreichbar (nicht installiert oder nicht gestartet)."
        )
        if not self._is_listening():
            return unreachable
        try:
            response = self._request("/api/tags", None, _CHECK_TIMEOUT_SECONDS)
        except ScanError:
            return unreachable

        installed = [str(model.get("name", "")) for model in response.get("models", [])]
        if not self._is_installed(installed):
            return ScannerStatus(
                False,
                f"Ollama läuft, aber das Modell '{self._model}' ist nicht installiert. "
                f"Befehl zum Laden: ollama pull {self._model}",
            )
        return ScannerStatus(True, f"Bereit: Modell '{self._model}' über {self._url}")

    def extract(self, images: Sequence[bytes], instructions: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        """Schickt die Belegseiten an das Modell und gibt dessen JSON-Antwort zurück.

        Raises:
            ScanError: Bei Verbindungsfehlern, Zeitüberschreitung oder wenn
                die Antwort kein gültiges JSON-Objekt ist.
        """
        payload: dict[str, Any] = {
            "model": self._model,
            "stream": False,
            "format": schema,
            # Viele neuere Modelle "denken" vor der Antwort laut nach. Für das
            # Abschreiben von Feldern bringt das nichts, kostet aber auf
            # schwächeren Rechnern viele Minuten - deshalb ausgeschaltet.
            "think": False,
            # temperature 0: immer die wahrscheinlichste Lesart, keine "Kreativität".
            "options": {"temperature": 0, "num_ctx": _CONTEXT_TOKENS, "num_predict": _MAX_ANSWER_TOKENS},
            "messages": [
                {
                    "role": "user",
                    "content": instructions,
                    "images": [base64.b64encode(image).decode("ascii") for image in images],
                }
            ],
        }
        logger.info("Belegerkennung gestartet: %d Seite(n), Modell %s", len(images), self._model)
        try:
            response = self._request("/api/chat", payload, _SCAN_TIMEOUT_SECONDS)
        except ScanError as exc:
            # Modelle ohne Denkmodus lehnen das Feld "think" ab - dann ohne wiederholen.
            if "think" not in str(exc).lower():
                raise
            del payload["think"]
            response = self._request("/api/chat", payload, _SCAN_TIMEOUT_SECONDS)

        message = response.get("message", {})
        content = str(message.get("content", ""))
        if not content.strip() and message.get("thinking"):
            # Erfahrungswert: "Thinking"-Varianten ignorieren das Abschalten des
            # Denkmodus und verbrauchen die gesamte Antwortlänge fürs Nachdenken.
            raise ScanError(
                f"Das Modell '{self._model}' ist eine 'Thinking'-Variante und liefert keine direkte "
                "Antwort. Bitte eine Variante mit '-instruct' im Namen verwenden "
                "(siehe Anleitung unter Belegerkennung)."
            )
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            logger.warning("Unlesbare Modell-Antwort: %.300s", content)
            raise ScanError("Das Modell hat keine auswertbare Antwort geliefert.") from exc
        if not isinstance(data, dict):
            raise ScanError("Das Modell hat keine auswertbare Antwort geliefert.")
        return data

    # ------------------------------------------------------------------ #
    # Interna
    # ------------------------------------------------------------------ #
    def _is_listening(self) -> bool:
        """Schnelle Vorprüfung: Nimmt unter der Adresse überhaupt ein Dienst Verbindungen an?"""
        parsed = urlparse(self._url)
        host = parsed.hostname or ""
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        timeout = _PROBE_TIMEOUT_LOCAL_SECONDS if host in _LOCAL_HOSTS else _PROBE_TIMEOUT_REMOTE_SECONDS
        # "localhost" steht für zwei Adressen; beide einzeln mit kurzer Zeitgrenze probieren.
        candidates = ("127.0.0.1", "::1") if host == "localhost" else (host,)
        for candidate in candidates:
            try:
                with socket.create_connection((candidate, port), timeout=timeout):
                    return True
            except OSError:
                continue
        return False

    def _is_installed(self, installed: Sequence[str]) -> bool:
        """Vergleicht Modellnamen; ohne Angabe eines Tags gilt ``:latest``."""
        wanted = self._model if ":" in self._model else f"{self._model}:latest"
        return wanted in installed

    def _request(self, path: str, payload: Mapping[str, Any] | None, timeout: float) -> dict[str, Any]:
        """Führt einen HTTP-Aufruf aus (GET ohne, POST mit ``payload``).

        Raises:
            ScanError: Bei jedem Verbindungs-, Zeit- oder Formatfehler.
        """
        body = json.dumps(payload).encode("utf-8") if payload is not None else None
        request = urllib.request.Request(
            self._url + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 (Adresse vom Benutzer)
                result = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise ScanError(f"Ollama meldet einen Fehler ({exc.code}): {detail}") from exc
        except (TimeoutError, socket.timeout) as exc:
            raise ScanError("Zeitüberschreitung: Ollama hat nicht rechtzeitig geantwortet.") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ScanError(f"Ollama ist unter {self._url} nicht erreichbar: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise ScanError("Ollama hat eine unlesbare Antwort geschickt.") from exc
        if not isinstance(result, dict):
            raise ScanError("Ollama hat eine unerwartete Antwort geschickt.")
        return result
