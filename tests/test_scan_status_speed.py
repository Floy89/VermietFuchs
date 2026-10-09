"""Tests dafür, dass die Verfügbarkeitsprüfung der Belegerkennung die Oberfläche nicht aufhält.

Hintergrund: Läuft Ollama nicht, dauerte allein die Feststellung "nicht
erreichbar" rund vier Sekunden - und zwar bei jedem Öffnen eines Rechnungs-
oder Zählerstand-Dialogs.
"""

from __future__ import annotations

import socket
import threading
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from integrations.ollama_client import OllamaReceiptScanner
from services import receipt_scan_service
from services.receipt_scan_service import ReceiptScanService, ScannerStatus
from utils.config_manager import ConfigManager, ReceiptScanConfig


class _SlowScanner:
    """Attrappe: Die Prüfung dauert spürbar lange und zählt ihre Aufrufe."""

    checks = 0
    release = threading.Event()

    def __init__(self, _settings: ReceiptScanConfig) -> None:
        pass

    def check(self) -> ScannerStatus:
        type(self).checks += 1
        type(self).release.wait(timeout=5)
        return ScannerStatus(True, "Bereit")

    def extract(self, images: Sequence[bytes], instructions: str, schema: Mapping[str, Any]) -> dict[str, Any]:
        return {}


def _wait_until(condition: Any, seconds: float = 3.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.01)
    return False


def _service(tmp_path: Path) -> ReceiptScanService:
    _SlowScanner.checks = 0
    _SlowScanner.release = threading.Event()
    return ReceiptScanService(ConfigManager(tmp_path / "config.json"), _SlowScanner)


class TestQuickStatus:
    def test_answers_immediately_and_checks_in_background(self, tmp_path: Path) -> None:
        service = _service(tmp_path)

        started = time.monotonic()
        first = service.quick_status()
        elapsed = time.monotonic() - started

        # Sofortige Antwort, obwohl die Prüfung noch hängt; bis dahin "nicht verfügbar".
        assert elapsed < 0.2
        assert not first.available and "wird noch geprüft" in first.message

        _SlowScanner.release.set()
        assert _wait_until(lambda: service.quick_status().available)

    def test_result_is_reused_while_fresh(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        _SlowScanner.release.set()
        service.status()  # wie beim Programmstart bzw. "Verbindung prüfen"

        for _ in range(20):
            assert service.quick_status().available

        assert _SlowScanner.checks == 1

    def test_only_one_background_check_at_a_time(self, tmp_path: Path) -> None:
        service = _service(tmp_path)

        for _ in range(10):
            service.quick_status()
        assert _wait_until(lambda: _SlowScanner.checks == 1)
        time.sleep(0.05)
        assert _SlowScanner.checks == 1
        _SlowScanner.release.set()

    def test_stale_result_triggers_new_check(self, tmp_path: Path, monkeypatch: Any) -> None:
        service = _service(tmp_path)
        _SlowScanner.release.set()
        service.status()
        monkeypatch.setattr(receipt_scan_service, "STATUS_MAX_AGE_SECONDS", 0.0)
        time.sleep(0.05)  # die Uhr von Windows tickt nur etwa alle 15 ms

        # Das alte Ergebnis wird weiter geliefert, im Hintergrund aber neu geprüft.
        assert service.quick_status().available
        assert _wait_until(lambda: _SlowScanner.checks == 2)

    def test_saving_settings_forgets_the_old_result(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        _SlowScanner.release.set()
        service.status()

        service.save_settings(ReceiptScanConfig(url="http://localhost:11434", model="anderes-modell"))
        _SlowScanner.release = threading.Event()  # die neue Prüfung hängt noch

        assert not service.quick_status().available
        _SlowScanner.release.set()

    def test_checking_unsaved_settings_does_not_touch_the_result(self, tmp_path: Path) -> None:
        service = _service(tmp_path)
        _SlowScanner.release.set()

        service.status(ReceiptScanConfig(url="http://localhost:1", model="probe"))

        # Geprüft wurde nur eine Eingabe aus dem Einrichtungsdialog - für die
        # gespeicherten Einstellungen ist weiterhin nichts bekannt.
        _SlowScanner.release = threading.Event()
        assert not service.quick_status().available
        _SlowScanner.release.set()


class TestOllamaProbe:
    def test_closed_local_port_is_detected_quickly(self) -> None:
        # Einen freien Port ermitteln und wieder schließen: Dort lauscht niemand.
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        scanner = OllamaReceiptScanner(ReceiptScanConfig(url=f"http://localhost:{port}", model="modell"))

        started = time.monotonic()
        status = scanner.check()
        elapsed = time.monotonic() - started

        assert not status.available and "nicht erreichbar" in status.message
        assert elapsed < 1.5, f"Die Prüfung dauerte {elapsed:.1f} s"
