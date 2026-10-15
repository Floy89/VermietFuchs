"""Wandelt Belegdateien (PDF oder Bild) in Bilddaten für die Belegerkennung um.

Das KI-Modell versteht nur Bilder. PDF-Seiten werden deshalb mit dem in Qt
enthaltenen PDF-Modul gerendert - ohne Zusatzprogramm.
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

from PyQt6.QtCore import QBuffer, QIODevice, QSize, Qt
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtPdf import QPdfDocument

from models.exceptions import ValidationError
from services.photo_service import SUPPORTED_EXTENSIONS

#: Mehr Seiten werden nicht ausgewertet: Betrag und Zeitraum stehen praktisch
#: immer auf den ersten Seiten, und jede weitere Seite kostet Rechenzeit.
MAX_PAGES: Final[int] = 3
#: Längste Bildkante in Pixeln. Reicht für gut lesbaren Text einer A4-Seite
#: und hält den Speicherbedarf des Modells im Rahmen.
MAX_EDGE: Final[int] = 1600


def receipt_file_filter() -> str:
    """Dateifilter für den Öffnen-Dialog (PDF und Bilder)."""
    patterns = " ".join(f"*{extension}" for extension in (".pdf", *SUPPORTED_EXTENSIONS))
    return f"Belege ({patterns})"


def load_receipt_images(path: Path, max_pages: int = MAX_PAGES, max_edge: int = MAX_EDGE) -> list[bytes]:
    """Lädt einen Beleg und gibt seine Seiten als PNG-Daten zurück.

    Raises:
        ValidationError: Wenn die Datei nicht gelesen oder dargestellt werden kann.
    """
    if path.suffix.lower() == ".pdf":
        pages = _render_pdf(path, max_pages, max_edge)
    else:
        image = QImage(str(path))
        if image.isNull():
            raise ValidationError("Die gewählte Datei ist kein lesbares Bild.")
        pages = [image]
    return [_to_png(_limit_size(page, max_edge)) for page in pages]


def _render_pdf(path: Path, max_pages: int = MAX_PAGES, max_edge: int = MAX_EDGE) -> list[QImage]:
    """Rendert die ersten Seiten eines PDFs auf weißem Hintergrund."""
    document = QPdfDocument(None)
    document.load(str(path))
    if document.pageCount() <= 0:
        raise ValidationError("Das PDF kann nicht geöffnet werden oder enthält keine Seiten.")

    pages: list[QImage] = []
    for index in range(min(document.pageCount(), max_pages)):
        point_size = document.pagePointSize(index)
        scale = max_edge / max(point_size.width(), point_size.height(), 1.0)
        size = QSize(int(point_size.width() * scale), int(point_size.height() * scale))
        rendered = document.render(index, size)
        # PDF-Seiten haben keinen eigenen Hintergrund - ohne Weiß wäre er
        # durchsichtig und der Text für das Modell kaum lesbar.
        page = QImage(size, QImage.Format.Format_RGB32)
        page.fill(QColor("white"))
        painter = QPainter(page)
        painter.drawImage(0, 0, rendered)
        painter.end()
        pages.append(page)
    document.close()
    return pages


def _limit_size(image: QImage, max_edge: int = MAX_EDGE) -> QImage:
    """Verkleinert sehr große Bilder (z. B. Handyfotos) auf die Maximalgröße."""
    if max(image.width(), image.height()) <= max_edge:
        return image
    return image.scaled(
        max_edge, max_edge, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation
    )


def _to_png(image: QImage) -> bytes:
    """Kodiert ein Bild verlustfrei als PNG."""
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    return bytes(buffer.data())


#: Für die Texterkennung gescannter PDFs: so viele Seiten höchstens (Details stehen oft hinten).
OCR_MAX_PAGES: Final[int] = 8
#: Auflösung für die Texterkennung. Ausprobiert an einer echten Versorgerrechnung: 1600 Pixel lieferten
#: alle Werte richtig, 2600 Pixel deutlich mehr Lesefehler ("rn3" statt "m³", getrennte Wörter).
OCR_MAX_EDGE: Final[int] = 1600


def pdf_text(path: Path) -> str:
    """Der im PDF enthaltene Text aller Seiten - leer bei gescannten PDFs (dort ist der Text nur ein Bild)."""
    document = QPdfDocument(None)
    document.load(str(path))
    if document.pageCount() <= 0:
        raise ValidationError("Das PDF kann nicht geöffnet werden oder enthält keine Seiten.")
    text = "\n".join(document.getAllText(index).text() for index in range(document.pageCount()))
    document.close()
    return text


def has_usable_text(text: str) -> bool:
    """``True``, wenn der Text nach einem echten Beleg aussieht (nicht nur ein paar Zeichen eines Scans)."""
    return sum(character.isdigit() for character in text) >= 10 and len(text.strip()) >= 80
