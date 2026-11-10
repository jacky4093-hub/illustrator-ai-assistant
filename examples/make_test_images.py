#!/usr/bin/env python3
"""Generate the project's own deterministic test images (no external assets).

Shapes are drawn at 4x and downsampled with LANCZOS so edges are
anti-aliased like real artwork.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent / "images"
S = 4


def _finish(im: Image.Image, size, name: str) -> Path:
    im = im.resize(size, Image.LANCZOS)
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name
    im.save(p)
    return p


def flat_shapes() -> Path:
    w, h = 240, 160  # non-square to test aspect ratio preservation
    im = Image.new("RGB", (w * S, h * S), (255, 255, 255))
    d = ImageDraw.Draw(im)
    d.ellipse([20 * S, 30 * S, 110 * S, 120 * S], fill=(220, 40, 40))
    d.rectangle([130 * S, 25 * S, 210 * S, 75 * S], fill=(30, 90, 200))
    d.polygon([(140 * S, 140 * S), (225 * S, 140 * S), (182 * S, 88 * S)], fill=(40, 160, 70))
    return _finish(im, (w, h), "flat_shapes.png")


def ring_with_hole() -> Path:
    w = h = 160
    im = Image.new("RGB", (w * S, h * S), (250, 245, 230))
    d = ImageDraw.Draw(im)
    d.ellipse([20 * S, 20 * S, 140 * S, 140 * S], fill=(20, 20, 60))
    d.ellipse([50 * S, 50 * S, 110 * S, 110 * S], fill=(250, 245, 230))
    d.rectangle([72 * S, 72 * S, 88 * S, 88 * S], fill=(240, 180, 20))  # island in hole
    return _finish(im, (w, h), "ring_with_hole.png")


def linear_gradient() -> Path:
    w, h = 200, 120
    x = np.linspace(0, 1, w)[None, :, None]
    a = np.array([30, 60, 200], dtype=np.float64)
    b = np.array([250, 200, 40], dtype=np.float64)
    arr = np.repeat(a + (b - a) * x, h, axis=0)
    im = Image.fromarray(np.rint(arr).astype(np.uint8), "RGB")
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / "linear_gradient.png"
    im.save(p)
    return p


def noise_texture() -> Path:
    rng = np.random.default_rng(42)
    arr = rng.integers(0, 256, size=(96, 128, 3), dtype=np.uint8)
    p = OUT / "noise_texture.png"
    OUT.mkdir(parents=True, exist_ok=True)
    Image.fromarray(arr, "RGB").save(p)
    return p


def all_images() -> list[Path]:
    return [flat_shapes(), ring_with_hole(), linear_gradient(), noise_texture()]


if __name__ == "__main__":
    for p in all_images():
        print(p)
