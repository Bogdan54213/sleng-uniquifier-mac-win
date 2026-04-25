#!/usr/bin/env python3
"""
Generates icon.ico for Sleng Унікалізатор.
Requires Pillow: pip install pillow
"""
from PIL import Image, ImageDraw, ImageFont
import math, os

SIZES = [16, 32, 48, 64, 128, 256]

# Brand colors (matching the dark UI)
BG_TOP    = (13,  27,  52)   # #0d1b34
BG_BOT    = (22,  47,  100)  # #162f64
ACCENT    = (99, 179, 255)   # #63b3ff  — light blue highlight
WHITE     = (255, 255, 255)
GOLD      = (255, 210, 90)   # #ffd25a


def make_icon(size: int) -> Image.Image:
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d   = ImageDraw.Draw(img)
    r   = size // 5          # corner radius

    # ── Rounded rect background (gradient-like: two rects blended) ────────────
    for y in range(size):
        t   = y / size
        col = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        d.line([(r if y < r or y > size - r else 0, y),
                (size - (r if y < r or y > size - r else 0) - 1, y)],
               fill=col + (255,))

    # Rounded corners mask
    mask = Image.new("L", (size, size), 0)
    dm   = ImageDraw.Draw(mask)
    dm.rounded_rectangle([0, 0, size - 1, size - 1], radius=r, fill=255)
    img.putalpha(mask)

    d = ImageDraw.Draw(img)

    # ── Film strip holes (top & bottom) ────────────────────────────────────────
    hole_r = max(1, size // 20)
    hole_y_top = size // 10
    hole_y_bot = size - size // 10
    n_holes = 5
    for i in range(n_holes):
        x = int(size * (i + 0.5) / n_holes)
        for hy in [hole_y_top, hole_y_bot]:
            d.ellipse(
                [x - hole_r, hy - hole_r, x + hole_r, hy + hole_r],
                fill=(255, 255, 255, 60)
            )

    # ── Letter "S" ─────────────────────────────────────────────────────────────
    cx, cy = size // 2, size // 2
    font_size = int(size * 0.58)
    font = None
    for fname in [
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/calibrib.ttf",
    ]:
        if os.path.exists(fname):
            try:
                font = ImageFont.truetype(fname, font_size)
                break
            except Exception:
                pass

    letter = "S"
    if font:
        bb = d.textbbox((0, 0), letter, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        tx = cx - tw // 2 - bb[0]
        ty = cy - th // 2 - bb[1]

        # Shadow
        shadow_off = max(1, size // 40)
        d.text((tx + shadow_off, ty + shadow_off), letter, font=font,
               fill=(0, 0, 0, 120))
        # Main letter
        d.text((tx, ty), letter, font=font, fill=WHITE + (255,))

        # Accent underline
        ul_y  = ty + th + max(1, size // 40)
        ul_h  = max(2, size // 30)
        ul_x1 = cx - tw // 2
        ul_x2 = cx + tw // 2
        d.rounded_rectangle([ul_x1, ul_y, ul_x2, ul_y + ul_h],
                            radius=ul_h // 2, fill=ACCENT + (220,))

    return img


def main():
    base = os.path.dirname(__file__)

    # Save 512x512 PNG — electron-builder converts to ICO automatically
    img_png = make_icon(512)
    png_out = os.path.join(base, "icon.png")
    img_png.save(png_out, format="PNG")
    print(f"[OK] icon.png saved: {png_out}")

    # Also save .ico for other uses (inno setup etc.)
    ordered = sorted(SIZES, reverse=True)
    frames  = [make_icon(s).convert("RGB") for s in ordered]
    ico_out = os.path.join(base, "icon.ico")
    frames[0].save(ico_out, format="ICO", append_images=frames[1:])
    print(f"[OK] icon.ico saved: {ico_out}")


if __name__ == "__main__":
    main()
