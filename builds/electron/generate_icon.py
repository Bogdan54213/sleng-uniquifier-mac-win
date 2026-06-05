#!/usr/bin/env python3
"""
Prepare app icons from the provided PNG logo.

icon.png — копія brand-logo.png без змін (square yellow, для splash screen).
icon.ico — pre-rounded corners + лагідний внутрішній padding, щоб Windows shell
не клипав у дивні форми і таскбар-іконка виглядала як цілісний жовтий
заокруглений квадрат із S, без чорних шматків зовні.
"""
from pathlib import Path
from shutil import copyfile

from PIL import Image, ImageDraw


# Multi-size ICO: 16-256 — стандартний набір для Windows shell, taskbar, alt-tab.
ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]

# Радіус заокруглення = 18% від ширини. Це відповідає Apple-style squircle,
# і має достатньо запасу щоб Windows-rounded-mask не клипав видимий жовтий.
CORNER_RADIUS_RATIO = 0.18


def make_rounded(img: Image.Image, radius_ratio: float = CORNER_RADIUS_RATIO) -> Image.Image:
    """Застосувати rounded-corner alpha-маску.

    На вхід — будь-який RGBA-зразок. На вихід — той самий зразок із прозорими кутами,
    усе всередині rounded-rect лишається 100% непрозорим.
    """
    img = img.convert("RGBA")
    w, h = img.size
    radius = int(min(w, h) * radius_ratio)

    mask = Image.new("L", (w, h), 0)
    draw = ImageDraw.Draw(mask)
    draw.rounded_rectangle([(0, 0), (w - 1, h - 1)], radius=radius, fill=255)

    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    out.paste(img, (0, 0), mask)
    return out


def main():
    base = Path(__file__).resolve().parent
    source = base / "brand-logo.png"
    icon_png = base / "icon.png"
    icon_ico = base / "icon.ico"

    if not source.exists():
        raise FileNotFoundError(f"Missing icon source: {source}")

    # icon.png — копія без змін (splash screen, brand display)
    copyfile(source, icon_png)
    print(f"[OK] icon.png copied unchanged: {icon_png}")

    # icon.ico — pre-rounded, щоб у Windows-таскбарі / alt-tab / file-explorer
    # бачили чистий заокруглений жовтий квадрат із S, без чорних артефактів
    with Image.open(source) as img:
        rounded = make_rounded(img.convert("RGBA"))
        rounded.save(icon_ico, format="ICO", sizes=ICO_SIZES)
    print(f"[OK] icon.ico with rounded corners (r={CORNER_RADIUS_RATIO}): {icon_ico}")


if __name__ == "__main__":
    main()
