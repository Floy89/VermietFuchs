"""Farbschemata (Hell/Dunkel) und Laden des Stylesheets.

Das Aussehen entsteht aus drei Bausteinen:

1. dem Qt-Stil "Fusion" - er sieht auf allen Betriebssystemen gleich aus,
2. einer ``QPalette`` mit den Grundfarben (wirkt auch auf Kalender-Popups,
   Dateidialoge usw., die das Stylesheet nicht erreicht) und
3. der Datei ``stylesheet.qss`` für alle Feinheiten.
"""

from __future__ import annotations

import atexit
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Final, Mapping

from PyQt6.QtCore import QByteArray, Qt
from PyQt6.QtGui import QColor, QPainter, QPalette, QPixmap
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import QApplication

import config

logger = logging.getLogger(__name__)

THEME_LIGHT: Final[str] = "light"
THEME_DARK: Final[str] = "dark"

_LIGHT: Final[Mapping[str, str]] = {
    "bg": "#F3F5F9",
    "surface": "#FFFFFF",
    "input": "#FFFFFF",
    "header": "#F7F9FC",
    "alt_row": "#FAFBFD",
    "border": "#DCE1EA",
    "text": "#1D2530",
    "muted": "#667284",
    "disabled": "#A9B2BF",
    "primary": "#2F6FED",
    "primary_hover": "#2459C7",
    "selection": "#DDE9FF",
    "hover": "#EEF2F8",
    "pressed": "#E2E8F1",
    "scroll": "#C5CCD8",
    "danger": "#D14343",
    "success": "#1E8E5A",
    "warning_bg": "#FFF6DD",
    "warning_border": "#EBCB6B",
    "warning_text": "#6B5200",
}

_DARK: Final[Mapping[str, str]] = {
    "bg": "#15181E",
    "surface": "#1D2129",
    "input": "#171A21",
    "header": "#222731",
    "alt_row": "#20242D",
    "border": "#323946",
    "text": "#E5E9F0",
    "muted": "#98A3B3",
    "disabled": "#5B6473",
    "primary": "#5C8FF5",
    "primary_hover": "#77A2F8",
    "selection": "#2B3F66",
    "hover": "#282E39",
    "pressed": "#313846",
    "scroll": "#454E5E",
    "danger": "#EF6B6B",
    "success": "#4CC38A",
    "warning_bg": "#3A3115",
    "warning_border": "#7A6520",
    "warning_text": "#F1D98A",
}

_THEMES: Final[Mapping[str, Mapping[str, str]]] = {THEME_LIGHT: _LIGHT, THEME_DARK: _DARK}

#: Kleine Grafiken, die das Stylesheet als Bilddatei braucht:
#: Name -> (SVG-Pfad, Farbe). Die Farbe ist entweder der Name einer
#: Schemafarbe oder ein fester Hex-Wert.
_GLYPHS: Final[Mapping[str, tuple[str, str]]] = {
    "arrow_down": ("M6 9l6 6 6-6", "muted"),
    "arrow_up": ("M6 15l6-6 6 6", "muted"),
    "check": ("M5 12.5l4.5 4.5L19 7.5", "#FFFFFF"),
}

# Temporärer Ordner für die Pfeil-Grafiken; wird beim ersten Bedarf angelegt.
_arrow_dir: Path | None = None

# Name des aktuell angewendeten Farbschemas (für Icons und farbige Tabellenzellen).
_current_theme: str = THEME_LIGHT


def current_theme() -> str:
    """Name des aktiven Farbschemas."""
    return _current_theme


def color(name: str) -> str:
    """Hex-Farbwert des aktiven Farbschemas, z. B. ``color("primary")``."""
    return _THEMES[_current_theme][name]


def apply_theme(app: QApplication, theme: str) -> None:
    """Wendet ein Farbschema auf die gesamte Anwendung an.

    Args:
        app: Die laufende ``QApplication``.
        theme: ``THEME_LIGHT`` oder ``THEME_DARK``; unbekannte Namen fallen
            auf das helle Schema zurück.
    """
    global _current_theme
    _current_theme = theme if theme in _THEMES else THEME_LIGHT
    colors = _THEMES[_current_theme]

    app.setStyle("Fusion")
    app.setPalette(_build_palette(colors))
    app.setStyleSheet(_render_stylesheet(colors))


def _build_palette(colors: Mapping[str, str]) -> QPalette:
    """Erzeugt die Qt-Farbpalette aus einem Farbschema."""
    palette = QPalette()
    roles = {
        QPalette.ColorRole.Window: "bg",
        QPalette.ColorRole.WindowText: "text",
        QPalette.ColorRole.Base: "surface",
        QPalette.ColorRole.AlternateBase: "alt_row",
        QPalette.ColorRole.Text: "text",
        QPalette.ColorRole.Button: "surface",
        QPalette.ColorRole.ButtonText: "text",
        QPalette.ColorRole.ToolTipBase: "text",
        QPalette.ColorRole.ToolTipText: "surface",
        QPalette.ColorRole.Highlight: "primary",
        QPalette.ColorRole.PlaceholderText: "disabled",
        QPalette.ColorRole.Link: "primary",
    }
    for role, name in roles.items():
        palette.setColor(role, QColor(colors[name]))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor("#FFFFFF"))
    for role in (QPalette.ColorRole.WindowText, QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors["disabled"]))
    return palette


def _render_stylesheet(colors: Mapping[str, str]) -> str:
    """Lädt ``stylesheet.qss`` und setzt die Farben des Schemas ein."""
    path = config.resource_dir() / "ui" / "styles" / "stylesheet.qss"
    try:
        template = path.read_text(encoding="utf-8")
    except OSError:
        # Ohne Stylesheet bleibt die Anwendung benutzbar (Fusion + Palette).
        logger.exception("Stylesheet '%s' kann nicht geladen werden.", path)
        return ""
    replacements = {**colors, **_arrow_files(colors)}
    for name, value in replacements.items():
        template = template.replace("{{" + name + "}}", value)
    return template


def _arrow_files(colors: Mapping[str, str]) -> dict[str, str]:
    """Zeichnet Pfeilspitzen und Häkchen und gibt ihre Dateipfade zurück.

    Ein Qt-Stylesheet kann solche Grafiken nur als Bilddatei einbinden
    (``image: url(...)``). Statt Bilddateien mitzuliefern, werden sie beim
    Anwenden des Farbschemas in einen temporären Ordner gezeichnet - so
    passen sie immer zur aktuellen Farbe, und PyInstaller muss nichts
    zusätzlich einpacken. Der Ordner wird beim Beenden wieder gelöscht.
    """
    global _arrow_dir
    if _arrow_dir is None:
        _arrow_dir = Path(tempfile.mkdtemp(prefix="nebenkosten_ui_"))
        atexit.register(shutil.rmtree, _arrow_dir, ignore_errors=True)

    files: dict[str, str] = {}
    for name, (path_data, colour) in _GLYPHS.items():
        stroke = colors.get(colour, colour)
        svg = (
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" '
            f'stroke="{stroke}" stroke-width="2.6" stroke-linecap="round" '
            f'stroke-linejoin="round"><path d="{path_data}"/></svg>'
        )
        image = QPixmap(48, 48)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        QSvgRenderer(QByteArray(svg.encode("utf-8"))).render(painter)
        painter.end()
        target = _arrow_dir / f"{_current_theme}_{name}.png"
        image.save(str(target), "PNG")
        # Qt-Stylesheets erwarten auch unter Windows Schrägstriche.
        files[name] = target.as_posix()
    return files
