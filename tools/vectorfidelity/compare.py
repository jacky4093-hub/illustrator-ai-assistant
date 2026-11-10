"""Compare the original raster with the rendered vector reconstruction."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .analyze import assign_labels_spatial

PIXEL_TOLERANCE = 24  # max per-channel difference counted as "matching"


@dataclass
class Thresholds:
    min_pixel_match: float = 0.97
    max_mean_delta_e: float = 3.0
    min_color_iou: float = 0.95
    min_iou_area_share: float = 0.002  # ignore IoU of colors covering less than this

    def to_dict(self):
        return asdict(self)


@dataclass
class Comparison:
    size_match: bool
    mae: float
    rmse: float
    psnr_db: float
    pixel_match_ratio: float
    mean_delta_e: float
    p95_delta_e: float
    color_iou: list[dict]
    min_color_iou: float
    worst_cells: list[dict]
    checks: dict = field(default_factory=dict)
    passed: bool = False

    def to_dict(self):
        return asdict(self)


def srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    c = rgb.astype(np.float64) / 255.0
    c = np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)
    m = np.array([[0.4124564, 0.3575761, 0.1804375],
                  [0.2126729, 0.7151522, 0.0721750],
                  [0.0193339, 0.1191920, 0.9503041]])
    xyz = c @ m.T
    xyz /= np.array([0.95047, 1.0, 1.08883])
    f = np.where(xyz > 216 / 24389, np.cbrt(xyz), (24389 / 27 * xyz + 16) / 116)
    L = 116 * f[..., 1] - 16
    a = 500 * (f[..., 0] - f[..., 1])
    b = 200 * (f[..., 1] - f[..., 2])
    return np.stack([L, a, b], axis=-1)


def compare_images(original: np.ndarray, rendered: np.ndarray, palette: np.ndarray,
                   original_labels: np.ndarray, thresholds: Thresholds | None = None,
                   grid: int = 8) -> tuple[Comparison, np.ndarray]:
    t = thresholds or Thresholds()
    if original.shape != rendered.shape:
        comp = Comparison(False, 255.0, 255.0, 0.0, 0.0, 100.0, 100.0, [], 0.0, [],
                          checks={"size_match": False}, passed=False)
        return comp, np.zeros_like(original)
    o = original.astype(np.float64)
    r = rendered.astype(np.float64)
    diff = np.abs(o - r)
    mae = float(diff.mean())
    mse = float((diff ** 2).mean())
    rmse = float(np.sqrt(mse))
    psnr = float("inf") if mse == 0 else float(20 * np.log10(255.0 / rmse))
    pixel_match = float((diff.max(-1) <= PIXEL_TOLERANCE).mean())
    de = np.linalg.norm(srgb_to_lab(original) - srgb_to_lab(rendered), axis=-1)
    mean_de = float(de.mean())
    p95_de = float(np.percentile(de, 95))

    # Rendered output has exact palette colors in region interiors, so the
    # edge-aware labeller classifies its anti-aliased seams robustly.
    rendered_labels = assign_labels_spatial(rendered, palette)
    total = original_labels.size
    ious = []
    for i, col in enumerate(palette):
        a = original_labels == i
        b = rendered_labels == i
        share = float(a.sum() / total)
        union = (a | b).sum()
        iou = float((a & b).sum() / union) if union else 1.0
        ious.append({"color": "#%02x%02x%02x" % tuple(int(v) for v in col),
                     "area_share": round(share, 5), "iou": round(iou, 5)})
    relevant = [c["iou"] for c in ious if c["area_share"] >= t.min_iou_area_share]
    min_iou = float(min(relevant)) if relevant else 1.0

    h, w = de.shape
    cells = []
    for gy in range(grid):
        for gx in range(grid):
            y0, y1 = gy * h // grid, (gy + 1) * h // grid
            x0, x1 = gx * w // grid, (gx + 1) * w // grid
            if y1 <= y0 or x1 <= x0:
                continue
            cells.append({"x": x0, "y": y0, "w": x1 - x0, "h": y1 - y0,
                          "mean_delta_e": round(float(de[y0:y1, x0:x1].mean()), 3)})
    cells.sort(key=lambda c: c["mean_delta_e"], reverse=True)

    checks = {
        "size_match": True,
        "pixel_match_ratio": pixel_match >= t.min_pixel_match,
        "mean_delta_e": mean_de <= t.max_mean_delta_e,
        "min_color_iou": min_iou >= t.min_color_iou,
    }
    comp = Comparison(
        size_match=True, mae=round(mae, 4), rmse=round(rmse, 4),
        psnr_db=round(psnr, 3) if np.isfinite(psnr) else 999.0,
        pixel_match_ratio=round(pixel_match, 5), mean_delta_e=round(mean_de, 4),
        p95_delta_e=round(p95_de, 4), color_iou=ious, min_color_iou=round(min_iou, 5),
        worst_cells=cells[:5], checks=checks, passed=all(checks.values()),
    )
    heat = np.clip(de * (255.0 / 20.0), 0, 255).astype(np.uint8)
    heat_rgb = np.stack([heat, np.zeros_like(heat), 255 - heat], axis=-1)
    return comp, heat_rgb
