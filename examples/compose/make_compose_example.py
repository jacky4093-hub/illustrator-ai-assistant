#!/usr/bin/env python3
"""Generate the self-made compose-mode example image (no external assets).

A small price card: a header bar with a title, a price box, two icons and a
spot where a logo would go. Text is drawn with Pillow's bundled default
font, so the PNG bytes can differ between Pillow versions; tests therefore
generate it into a temporary folder instead of rewriting the repository copy.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W, H, S = 480, 300, 3
NAVY, BLUE, ORANGE, WHITE, PALE = (13, 59, 115), (36, 158, 211), (247, 150, 40), (255, 255, 255), (230, 243, 251)


def make(path: Path) -> Path:
    im = Image.new("RGB", (W * S, H * S), WHITE)
    d = ImageDraw.Draw(im)

    def box(x0, y0, x1, y1, **kw):
        d.rounded_rectangle([x0 * S, y0 * S, x1 * S, y1 * S], **kw)

    def text(x, y, s, size, fill, anchor="ls"):
        d.text((x * S, y * S), s, font=ImageFont.load_default(size * S), fill=fill, anchor=anchor)

    box(10, 10, 470, 70, radius=8 * S, fill=NAVY)
    text(30, 52, "WASH & DRY", 34, WHITE)
    box(20, 90, 250, 200, radius=10 * S, fill=PALE, outline=BLUE, width=2 * S)
    text(40, 130, "Standard 25 kg", 20, NAVY)
    text(40, 180, "$250", 40, ORANGE)
    # icon 1: basket (navy on pale)
    d.polygon([(190 * S, 150 * S), (236 * S, 150 * S), (228 * S, 190 * S), (198 * S, 190 * S)], fill=NAVY)
    d.rectangle([186 * S, 142 * S, 240 * S, 150 * S], fill=NAVY)
    # icon 2: drop (blue on white)
    d.polygon([(330 * S, 100 * S), (352 * S, 150 * S), (308 * S, 150 * S)], fill=BLUE)
    d.ellipse([306 * S, 128 * S, 354 * S, 176 * S], fill=BLUE)
    d.ellipse([318 * S, 146 * S, 330 * S, 158 * S], fill=WHITE)
    # logo stand-in (to be replaced by the official artwork)
    d.ellipse([390 * S, 100 * S, 450 * S, 160 * S], outline=NAVY, width=3 * S)
    text(420, 136, "LOGO", 14, NAVY, "ms")
    text(20, 240, "Close the door before starting.", 16, NAVY)
    d.line([20 * S, 258 * S, 460 * S, 258 * S], fill=BLUE, width=2 * S)
    text(240, 284, "Thank you!", 18, BLUE, "ms")
    im = im.resize((W, H), Image.LANCZOS)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    im.save(path)
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "price_card.png"
    print(make(target))
