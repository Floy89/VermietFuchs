"""Texterkennung (OCR) mit den Bordmitteln von Windows 10/11 - ohne KI-Modell, ohne Zusatzprogramm.

Windows bringt eine Texterkennung mit (``Windows.Media.Ocr``); sie wird über
die Python-Pakete ``winrt-*`` angesprochen. Voraussetzung ist ein
installiertes Sprachpaket mit Texterkennung - für deutsche Belege "Deutsch"
(in Windows unter Einstellungen → Zeit und Sprache → Sprache).

Die Erkennung liefert Wörter mit ihrer Position. Tabellen kämen sonst
spaltenweise heraus ("Datum, Datum, Datum, Zählerstand, Zählerstand ...");
:func:`rows_from_words` setzt die Wörter deshalb wieder zu Zeilen zusammen,
wie sie auf dem Papier stehen.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass
from functools import lru_cache
from statistics import median
from typing import Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Word:
    """Ein erkanntes Wort mit seiner Position (Bildpunkte, links oben = 0/0)."""

    text: str
    x: float
    y: float
    width: float
    height: float

    @property
    def center_y(self) -> float:
        return self.y + self.height / 2


def rows_from_words(words: Sequence[Word]) -> list[str]:
    """Fügt Wörter zu Zeilen zusammen: gleiche Höhe = gleiche Zeile, von links nach rechts.

    Zwei Wörter gehören zur selben Zeile, wenn ihre Mitten um weniger als die
    halbe übliche Wortgröße auseinanderliegen.
    """
    if not words:
        return []
    tolerance = median(word.height for word in words) * 0.5
    rows: list[list[Word]] = []
    for word in sorted(words, key=lambda w: w.center_y):
        if rows and abs(rows[-1][-1].center_y - word.center_y) <= tolerance:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [" ".join(word.text for word in sorted(row, key=lambda w: w.x)) for row in rows]


class WindowsOcr:
    """Erkennt Text in Bildern mit der Texterkennung von Windows."""

    def __init__(self, language: str = "de-DE") -> None:
        self._language = language

    @staticmethod
    @lru_cache(maxsize=1)
    def available() -> bool:
        """``True``, wenn Windows-Texterkennung samt deutscher Sprache nutzbar ist (einmal geprüft)."""
        if sys.platform != "win32":
            return False
        try:
            from winrt.windows.globalization import Language
            from winrt.windows.media.ocr import OcrEngine

            return OcrEngine.try_create_from_language(Language("de-DE")) is not None
        except Exception as exc:  # Paket fehlt, Sprache fehlt, ältere Windows-Version ...
            logger.info("Windows-Texterkennung nicht verfügbar: %s", exc)
            return False

    def recognize(self, images: Sequence[bytes]) -> str:
        """Erkennt den Text mehrerer Seiten (PNG- oder JPEG-Daten), Seiten durch Leerzeilen getrennt."""
        return "\n\n".join(asyncio.run(self._recognize_page(image)) for image in images)

    async def _recognize_page(self, image: bytes) -> str:
        from winrt.windows.globalization import Language
        from winrt.windows.graphics.imaging import BitmapDecoder
        from winrt.windows.media.ocr import OcrEngine
        from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

        stream = InMemoryRandomAccessStream()
        writer = DataWriter(stream)
        writer.write_bytes(image)
        await writer.store_async()
        await writer.flush_async()
        writer.detach_stream()
        stream.seek(0)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_language(Language(self._language))
        if engine is None:
            return ""
        result = await engine.recognize_async(bitmap)
        words = [
            Word(word.text, word.bounding_rect.x, word.bounding_rect.y, word.bounding_rect.width, word.bounding_rect.height)
            for line in result.lines
            for word in line.words
        ]
        return "\n".join(rows_from_words(words))


def default_ocr() -> WindowsOcr | None:
    """Die Texterkennung, wenn sie auf diesem Rechner nutzbar ist - sonst ``None``."""
    return WindowsOcr() if WindowsOcr.available() else None
