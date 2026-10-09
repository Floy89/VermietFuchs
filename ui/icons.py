"""Einheitliche Linien-Icons, direkt als SVG im Code hinterlegt.

Vorteile gegenüber Bilddateien:

* Keine zusätzlichen Dateien, die PyInstaller einpacken müsste.
* Die Icons werden zur Laufzeit in der passenden Farbe gezeichnet und passen
  sich damit automatisch dem hellen bzw. dunklen Farbschema an.
* Alle Icons teilen Raster (24 × 24), Strichstärke und Linienenden - das
  sorgt für ein konsistentes Erscheinungsbild.
"""

from __future__ import annotations

from typing import Final, Mapping

from PyQt6.QtCore import QByteArray, QSize, Qt
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtSvg import QSvgRenderer

from ui import styles

_SVG_TEMPLATE: Final[str] = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
    'stroke="{color}" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
)

#: Zeichenanweisungen je Icon-Name (nur der Inhalt des <svg>-Elements).
_ICON_BODIES: Final[Mapping[str, str]] = {
    "dashboard": '<rect x="3" y="3" width="8" height="8" rx="1.5"/><rect x="13" y="3" width="8" height="5" rx="1.5"/>'
    '<rect x="13" y="10" width="8" height="11" rx="1.5"/><rect x="3" y="13" width="8" height="8" rx="1.5"/>',
    "building": '<path d="M4 21V5l8-2v18"/><path d="M12 9l8 2v10"/><path d="M2 21h20"/>'
    '<path d="M7.5 8v.01M7.5 12v.01M7.5 16v.01M16 14v.01M16 17.5v.01"/>',
    "users": '<circle cx="9" cy="8" r="3.5"/><path d="M2.5 20c0-3.6 2.9-6 6.5-6s6.5 2.4 6.5 6"/>'
    '<path d="M16 4.8a3.5 3.5 0 0 1 0 6.4"/><path d="M18 14.3c2.1.8 3.5 2.8 3.5 5.7"/>',
    "gauge": '<path d="M4.5 18a9 9 0 1 1 15 0"/><path d="M12 14l4-5"/><circle cx="12" cy="14" r="1.3"/>',
    "calculator": '<rect x="5" y="2.5" width="14" height="19" rx="2"/><path d="M8.5 7h7"/>'
    '<path d="M8.5 12v.01M12 12v.01M15.5 12v.01M8.5 16v.01M12 16v.01M15.5 16v.01"/>',
    "wallet": '<path d="M3 8V7a2 2 0 0 1 2-2h13v3"/><rect x="3" y="8" width="18" height="12" rx="2"/>'
    '<path d="M16.5 14h.01"/>',
    "report": '<path d="M6 2.5h8l4 4v15H6z"/><path d="M14 2.5v4h4"/><path d="M9 12h6M9 16h6"/>',
    "chart": '<path d="M4 4v16h16"/><path d="M7.5 15.5l4-5 3 2.5 4.5-6.5"/>',
    "plus": '<path d="M12 5v14M5 12h14"/>',
    "edit": '<path d="M4 20h4L19 9l-4-4L4 16z"/><path d="M13.5 6.5l4 4"/>',
    "copy": '<rect x="8.5" y="8.5" width="12" height="12" rx="2"/>'
    '<path d="M15.5 8.5V5.5a2 2 0 0 0-2-2h-8a2 2 0 0 0-2 2v8a2 2 0 0 0 2 2h3"/>',
    "trash":'<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 13h10l1-13"/><path d="M10 11v6M14 11v6"/>',
    "user": '<circle cx="12" cy="8" r="4"/><path d="M4 21c0-4.4 3.6-7 8-7s8 2.6 8 7"/>',
    "settings": '<path d="M4 6h8M20 6h-2M4 12h2M12 12h8M4 18h10M20 18h0"/>'
    '<circle cx="15" cy="6" r="2.2"/><circle cx="9" cy="12" r="2.2"/><circle cx="17" cy="18" r="2.2"/>',
    "info": '<circle cx="12" cy="12" r="9"/><path d="M12 11v6M12 7.5v.01"/>',
    "switch": '<path d="M4 8h15l-3.5-3.5"/><path d="M20 16H5l3.5 3.5"/>',
    "moon": '<path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z"/>',
    "sun": '<circle cx="12" cy="12" r="4"/><path d="M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2'
    'M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4"/>',
    "camera": '<path d="M3 8h4l1.5-2.5h7L17 8h4v11H3z"/><circle cx="12" cy="13" r="3.5"/>',
    "download": '<path d="M12 4v11M7.5 11l4.5 4.5 4.5-4.5"/><path d="M4 20h16"/>',
    "eye": '<path d="M2.5 12S6 5.5 12 5.5 21.5 12 21.5 12 18 18.5 12 18.5 2.5 12 2.5 12z"/>'
    '<circle cx="12" cy="12" r="3"/>',
    "folder": '<path d="M3 6h6l2 2.5h10V19H3z"/>',
    "home": '<path d="M3 11.5L12 4l9 7.5"/><path d="M5.5 10v10h13V10"/><path d="M10 20v-5.5h4V20"/>',
    "area": '<rect x="4" y="4" width="16" height="16" rx="1.5"/><path d="M4 9h3M4 14h3M9 20v-3M14 20v-3"/>',
    "coins": '<ellipse cx="12" cy="7" rx="7" ry="3"/><path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7"/>'
    '<path d="M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5"/>',
}

# Fertig gerenderte Icons; Schlüssel ist (Name, Farbe).
_cache: dict[tuple[str, str], QIcon] = {}


def icon(name: str, color: str | None = None) -> QIcon:
    """Gibt ein Icon in der gewünschten Farbe zurück.

    Args:
        name: Schlüssel aus der Icon-Sammlung, z. B. ``"plus"``.
        color: Hex-Farbe; Standard ist die Textfarbe des aktiven Farbschemas.

    Raises:
        KeyError: Bei unbekanntem Icon-Namen (Programmierfehler).
    """
    resolved_color = color or styles.color("text")
    key = (name, resolved_color)
    if key not in _cache:
        _cache[key] = QIcon(_render(name, resolved_color))
    return _cache[key]


def pixmap(name: str, size: int, color: str | None = None) -> QPixmap:
    """Gibt ein Icon als Pixmap fester Größe zurück (z. B. für ein ``QLabel``)."""
    return icon(name, color).pixmap(QSize(size, size))


def _render(name: str, color: str, size: int = 64) -> QPixmap:
    """Zeichnet das SVG in eine transparente Pixmap."""
    svg = _SVG_TEMPLATE.format(color=color, body=_ICON_BODIES[name])
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    image = QPixmap(size, size)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter)
    painter.end()
    return image
