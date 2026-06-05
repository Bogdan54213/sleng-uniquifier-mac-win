#!/usr/bin/env python3
"""
Generate app icons from the source brand PNG.

The source file is kept in the repo so CI builds use the exact same logo as the
desktop UI and splash screen.
"""
from pathlib import Path

from PIL import Image


SIZES = [16, 24, 32, 48, 64, 128, 256]
SOURCE_NAME = "brand-logo.png"


def fit_square(src: Image.Image, size: int) -> Image.Image:
    src = src.convert("RGBA")
    fitted = Image.new("RGBA", (size, size), (0, 0, 0, 255))

    scale = min(size / src.width, size / src.height)
    new_size = (max(1, round(src.width * scale)), max(1, round(src.height * scale)))
    resized = src.resize(new_size, Image.Resampling.LANCZOS)
    pos = ((size - resized.width) // 2, (size - resized.height) // 2)
    fitted.alpha_composite(resized, pos)
    return fitted


def main():
    base = Path(__file__).resolve().parent
    source = base / SOURCE_NAME
    if not source.exists():
        raise FileNotFoundError(f"Missing icon source: {source}")

    src = Image.open(source)

    png = fit_square(src, 1024)
    png.save(base / "icon.png", format="PNG")
    print(f"[OK] icon.png saved: {base / 'icon.png'}")

    frames = [fit_square(src, s) for s in sorted(SIZES, reverse=True)]
    frames[0].save(base / "icon.ico", format="ICO", append_images=frames[1:])
    print(f"[OK] icon.ico saved: {base / 'icon.ico'}")


if __name__ == "__main__":
    main()
