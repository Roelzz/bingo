"""Build the emblem and app icons from assets/emblem-source.jpg.

- fills the empty ribbons with "UTRECHT" and "09"
- removes the paper background
- writes an ink version (for light backgrounds) and a cream version (for dark backgrounds)
- writes the PWA icons

Run: uv run python scripts/make_assets.py
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "assets" / "emblem-source.jpg"
STATIC = ROOT / "app" / "static"
FONT = STATIC / "fonts" / "cinzel-latin-900-normal.woff2"

INK = (27, 25, 22)
CREAM = (240, 231, 210)

BIG_RIBBON = {"text": "UTRECHT", "center": (710, 1083), "radius": 531, "size": 33, "tracking": 7}
SMALL_RIBBON = {"text": "09", "pos": (706, 694), "size": 36}


def arc_text(img: Image.Image, text: str, center, radius: float, size: int, tracking: int) -> None:
    """Draw text along the top of a circle, letters standing upright on the arc."""
    font = ImageFont.truetype(str(FONT), size)
    widths = [font.getlength(ch) + tracking for ch in text]
    total = sum(widths) - tracking
    angle = -total / 2 / radius
    cx, cy = center
    for ch, w in zip(text, widths):
        mid = angle + (w - tracking) / 2 / radius
        tile = Image.new("RGBA", (size * 2, size * 2), (0, 0, 0, 0))
        ImageDraw.Draw(tile).text((size, size), ch, font=font, fill=INK + (255,), anchor="mm")
        tile = tile.rotate(-math.degrees(mid), resample=Image.BICUBIC)
        x = cx + radius * math.sin(mid)
        y = cy - radius * math.cos(mid)
        img.alpha_composite(tile, (round(x - size), round(y - size)))
        angle += w / radius


def straight_text(img: Image.Image, text: str, pos, size: int) -> None:
    """Centre the visible glyphs (Cinzel's numerals sit low, so metric centring looks off)."""
    font = ImageFont.truetype(str(FONT), size)
    draw = ImageDraw.Draw(img)
    l, t, r, b = draw.textbbox((0, 0), text, font=font, anchor="lt")
    draw.text((pos[0] - (l + r) / 2, pos[1] - (t + b) / 2), text, font=font, fill=INK + (255,), anchor="lt")


def alpha_from_paper(img: Image.Image) -> Image.Image:
    """Dark ink becomes opaque, paper becomes transparent, with soft edges."""
    lum = ImageOps.grayscale(img.convert("RGB"))
    paper, ink = 226, 70
    return lum.point(lambda v: 0 if v >= paper else 255 if v <= ink else round((paper - v) * 255 / (paper - ink)))


def coloured(alpha: Image.Image, rgb) -> Image.Image:
    out = Image.new("RGBA", alpha.size, rgb + (0,))
    out.putalpha(alpha)
    return out


def icon(crest_cream: Image.Image, size: int, pad: float, path: Path, rgb_only=False) -> None:
    bg = Image.new("RGBA", (size, size), INK + (255,))
    inner = round(size * (1 - 2 * pad))
    c = crest_cream.copy()
    c.thumbnail((inner, inner), Image.LANCZOS)
    bg.alpha_composite(c, ((size - c.width) // 2, (size - c.height) // 2))
    bg = bg.convert("RGB")
    if not rgb_only:
        bg = bg.quantize(colors=48, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
    bg.save(path, optimize=True)


def main() -> None:
    img = Image.open(SRC).convert("RGBA")
    arc_text(img, BIG_RIBBON["text"], BIG_RIBBON["center"], BIG_RIBBON["radius"], BIG_RIBBON["size"], BIG_RIBBON["tracking"])
    straight_text(img, SMALL_RIBBON["text"], SMALL_RIBBON["pos"], SMALL_RIBBON["size"])

    alpha = alpha_from_paper(img)
    box = alpha.point(lambda v: 255 if v > 60 else 0).getbbox()
    alpha = alpha.crop(box)

    out = STATIC / "img"
    out.mkdir(parents=True, exist_ok=True)
    # Cream crest flattened on ink: a small JPEG. The CSS uses mix-blend-mode: lighten,
    # so the ink background disappears into any dark background behind it.
    crest = coloured(alpha, CREAM)
    crest.thumbnail((480, 480), Image.LANCZOS)  # sharp at 2x for a 240px display
    flat = Image.new("RGBA", crest.size, INK + (255,))
    flat.alpha_composite(crest)
    flat.convert("RGB").save(out / "crest.jpg", quality=84, optimize=True, progressive=True)

    cream = coloured(alpha, CREAM)
    icons = STATIC / "icons"
    icon(cream, 192, 0.08, icons / "icon-192.png")
    icon(cream, 512, 0.08, icons / "icon-512.png")
    icon(cream, 512, 0.18, icons / "icon-maskable.png")
    icon(cream, 180, 0.07, icons / "apple-touch-icon.png", rgb_only=True)
    badge = coloured(alpha, (255, 255, 255))
    badge.thumbnail((92, 92), Image.LANCZOS)
    b = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    b.alpha_composite(badge, ((96 - badge.width) // 2, (96 - badge.height) // 2))
    b.save(icons / "badge-96.png", optimize=True)
    print("crest box", box, "->", out)


if __name__ == "__main__":
    main()
