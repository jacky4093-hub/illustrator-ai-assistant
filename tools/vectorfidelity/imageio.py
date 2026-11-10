"""Image loading helpers. Everything stays local; nothing is uploaded."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image


@dataclass
class LoadedImage:
    path: str
    width: int
    height: int
    mode: str
    rgb: np.ndarray  # H x W x 3, uint8, composited on white
    has_alpha: bool
    partial_alpha_fraction: float  # pixels with 0 < alpha < 255
    transparent_fraction: float  # pixels with alpha == 0


def load_image(path: str | Path) -> LoadedImage:
    path = Path(path)
    with Image.open(path) as im:
        im.load()
        mode = im.mode
        width, height = im.size
        rgba = np.asarray(im.convert("RGBA"), dtype=np.uint8)
    alpha = rgba[..., 3].astype(np.float32) / 255.0
    has_alpha = bool((rgba[..., 3] < 255).any())
    rgb = rgba[..., :3].astype(np.float32)
    # Composite on white: transparency is not reconstructed in phase 1 and
    # is reported as an unrestored aspect when present.
    rgb = rgb * alpha[..., None] + 255.0 * (1.0 - alpha[..., None])
    rgb = np.clip(np.rint(rgb), 0, 255).astype(np.uint8)
    a = rgba[..., 3]
    return LoadedImage(
        path=str(path),
        width=width,
        height=height,
        mode=mode,
        rgb=rgb,
        has_alpha=has_alpha,
        partial_alpha_fraction=float(((a > 0) & (a < 255)).mean()),
        transparent_fraction=float((a == 0).mean()),
    )


def save_rgb(rgb: np.ndarray, path: str | Path) -> None:
    Image.fromarray(rgb.astype(np.uint8), "RGB").save(path)
