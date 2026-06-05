#!/usr/bin/env python3
"""
Prepare app icons from the provided PNG logo.

This does not redraw or stylize the logo. It copies brand-logo.png to icon.png
unchanged, then converts that same PNG to icon.ico for Windows/Inno Setup.
"""
from pathlib import Path
from shutil import copyfile

from PIL import Image


ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def main():
    base = Path(__file__).resolve().parent
    source = base / "brand-logo.png"
    icon_png = base / "icon.png"
    icon_ico = base / "icon.ico"

    if not source.exists():
        raise FileNotFoundError(f"Missing icon source: {source}")

    copyfile(source, icon_png)
    print(f"[OK] icon.png copied unchanged: {icon_png}")

    with Image.open(source) as img:
        img.convert("RGBA").save(icon_ico, format="ICO", sizes=ICO_SIZES)
    print(f"[OK] icon.ico converted from PNG: {icon_ico}")


if __name__ == "__main__":
    main()
