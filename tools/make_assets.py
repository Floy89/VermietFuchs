"""Erzeugt aus den Originalbildern in ``pictures/`` die Grafiken, die die Anwendung verwendet.

Aufruf aus dem Projektordner::

    python -m tools.make_assets

Ergebnis in ``ui/resources``:

* ``app_icon.png`` - das App-Symbol, 256 × 256, Ecken durchsichtig (Fenstersymbol, Kopfzeile)
* ``app_icon.ico`` - dasselbe in mehreren Größen für die ``.exe``
* ``logo.png``     - das Logo mit Schriftzug, durchsichtiger Hintergrund (Startdialog)

Die Originale haben einen weißen Hintergrund. Er wird von den Bildecken aus
"geflutet" und durchsichtig gemacht - Weiß *innerhalb* der Zeichnung (etwa
das Fell des Fuchses) bleibt erhalten, weil es von außen nicht erreichbar ist.

Braucht Pillow (kommt mit ReportLab ohnehin mit).
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SOURCE_DIR = ROOT / "pictures"
TARGET_DIR = ROOT / "ui" / "resources"

ICON_SOURCE = "Verspieltes Fuchs-Haus-App-Symbol.png"
LOGO_SOURCE = "VermietFuchs.png"

_ICON_SIZES = [16, 24, 32, 48, 64, 128, 256]


def remove_background(image: Image.Image, min_brightness: int) -> Image.Image:
    """Macht den von den Ecken aus erreichbaren hellen, farblosen Hintergrund durchsichtig.

    Als Hintergrund gilt ein Pixel, das hell *und* farbneutral ist (Weiß bis
    Hellgrau - also auch der weiche Schatten unter dem App-Symbol). Das
    cremefarbene Fell des Fuchses ist zwar ähnlich hell, aber nicht neutral,
    und bleibt deshalb auch dort erhalten, wo es an den Hintergrund grenzt.

    Args:
        min_brightness: Ab dieser Helligkeit (0–255) zählt ein neutrales Pixel als Hintergrund.
    """
    rgb = image.convert("RGB")
    red, green, blue = rgb.split()
    brightest = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    darkest = ImageChops.darker(ImageChops.darker(red, green), blue)
    neutral = ImageChops.subtract(brightest, darkest).point(lambda v: 255 if v <= 14 else 0)
    light = darkest.point(lambda v: 255 if v >= min_brightness else 0)
    candidates = ImageChops.multiply(neutral, light)  # 255 = könnte Hintergrund sein

    # Von den Ecken aus fluten: Nur was mit dem Bildrand verbunden ist, ist wirklich Hintergrund.
    width, height = candidates.size
    for corner in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1)):
        if candidates.getpixel(corner) == 255:
            ImageDraw.floodfill(candidates, corner, 128, thresh=0)
    alpha = candidates.point(lambda v: 0 if v == 128 else 255)
    # Den hellen Saum am Rand abtragen und die Kante weichzeichnen.
    alpha = alpha.filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(0.8))
    result = rgb.convert("RGBA")
    result.putalpha(alpha)
    return result


def trim(image: Image.Image, padding: int = 0) -> Image.Image:
    """Schneidet den durchsichtigen Rand weg (mit optionalem Abstand)."""
    box = image.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if box is None:
        return image
    left, top, right, bottom = box
    return image.crop((max(0, left - padding), max(0, top - padding), min(image.width, right + padding), min(image.height, bottom + padding)))


def make_icon() -> None:
    """App-Symbol als PNG und als ICO mit mehreren Größen."""
    # Niedrige Schwelle: Auch der hellgraue Schatten unter dem Symbol soll verschwinden.
    icon = trim(remove_background(Image.open(SOURCE_DIR / ICON_SOURCE), min_brightness=150))
    side = max(icon.size)
    square = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    square.paste(icon, ((side - icon.width) // 2, (side - icon.height) // 2))
    square.resize((256, 256), Image.Resampling.LANCZOS).save(TARGET_DIR / "app_icon.png", optimize=True)
    square.save(TARGET_DIR / "app_icon.ico", sizes=[(size, size) for size in _ICON_SIZES])


def make_logo() -> None:
    """Logo mit Schriftzug, auf 720 Pixel Breite verkleinert."""
    logo = trim(remove_background(Image.open(SOURCE_DIR / LOGO_SOURCE), min_brightness=236), padding=12)
    width = 720
    logo.resize((width, round(logo.height * width / logo.width)), Image.Resampling.LANCZOS).save(
        TARGET_DIR / "logo.png", optimize=True
    )


def main() -> None:
    """Erzeugt alle Grafiken."""
    missing = [name for name in (ICON_SOURCE, LOGO_SOURCE) if not (SOURCE_DIR / name).exists()]
    if missing:
        sys.exit(f"Originalbild(er) fehlen in {SOURCE_DIR}: {', '.join(missing)}")
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    make_icon()
    make_logo()
    for name in ("app_icon.png", "app_icon.ico", "logo.png"):
        print(f"  {name}: {(TARGET_DIR / name).stat().st_size // 1024} KB")


if __name__ == "__main__":
    main()
