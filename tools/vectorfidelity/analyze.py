"""Image analysis, classification and vectorization-strategy selection."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import numpy as np

from .imageio import LoadedImage

# Neighbour differences (max over RGB channels) up to this value are
# considered a "smooth" transition (gradient / anti-aliasing); above it a
# "hard" edge.
SMOOTH_STEP_MAX = 12
AA_RADIUS = 2


@dataclass
class ImageStats:
    width: int
    height: int
    aspect_ratio: float
    unique_colors: int
    top8_coverage: float
    top16_coverage: float
    aa_adjusted_coverage: float  # top-16 colors plus pixels within 2 px of them
    smooth_transition_fraction: float
    hard_edge_fraction: float
    has_alpha: bool
    partial_alpha_fraction: float


@dataclass
class Strategy:
    image_class: str
    method: str
    palette_mode: str  # "dominant" | "kmeans"
    max_colors: int
    simplify_epsilons: list[float]
    min_loop_area: float
    human_review_reasons: list[str] = field(default_factory=list)
    expected_limitations: list[str] = field(default_factory=list)

    def to_dict(self):
        return asdict(self)


def _pack(rgb: np.ndarray) -> np.ndarray:
    flat = rgb.reshape(-1, 3).astype(np.uint32)
    return (flat[:, 0] << 16) | (flat[:, 1] << 8) | flat[:, 2]


def _unpack(packed: np.ndarray) -> np.ndarray:
    return np.stack(
        [(packed >> 16) & 255, (packed >> 8) & 255, packed & 255], axis=-1
    ).astype(np.uint8)


def _dilate(mask: np.ndarray, r: int) -> np.ndarray:
    out = mask.copy()
    h, w = mask.shape
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            if dy == 0 and dx == 0:
                continue
            src = mask[max(0, -dy):h - max(0, dy), max(0, -dx):w - max(0, dx)]
            out[max(0, dy):h - max(0, -dy), max(0, dx):w - max(0, -dx)] |= src
    return out


def compute_stats(img: LoadedImage) -> ImageStats:
    rgb = img.rgb
    packed = _pack(rgb)
    _, counts = np.unique(packed, return_counts=True)
    counts = np.sort(counts)[::-1]
    total = packed.size
    signed = rgb.astype(np.int16)
    dx = np.abs(signed[:, 1:] - signed[:, :-1]).max(-1)
    dy = np.abs(signed[1:, :] - signed[:-1, :]).max(-1)
    diffs = np.concatenate([dx.ravel(), dy.ravel()])
    n = max(diffs.size, 1)
    # Anti-aliasing-aware coverage: pixels that are one of the 16 most
    # frequent colors, or lie within AA_RADIUS px of such a pixel. Flat art
    # with anti-aliased edges scores ~1.0; gradients and photos score low.
    values, vcounts = np.unique(packed, return_counts=True)
    top = values[np.argsort(vcounts)[::-1][:16]]
    dom = np.isin(packed, top).reshape(img.height, img.width)
    near = _dilate(dom, AA_RADIUS)
    return ImageStats(
        width=img.width,
        height=img.height,
        aspect_ratio=round(img.width / img.height, 6),
        unique_colors=int(counts.size),
        top8_coverage=float(counts[:8].sum() / total),
        top16_coverage=float(counts[:16].sum() / total),
        aa_adjusted_coverage=float(near.mean()),
        smooth_transition_fraction=float(
            ((diffs > 0) & (diffs <= SMOOTH_STEP_MAX)).sum() / n
        ),
        hard_edge_fraction=float((diffs > SMOOTH_STEP_MAX).sum() / n),
        has_alpha=img.has_alpha,
        partial_alpha_fraction=img.partial_alpha_fraction,
    )


def classify(stats: ImageStats) -> str:
    """Rule-based classification. Thresholds are documented in SKILL.md."""
    if stats.aa_adjusted_coverage >= 0.97 and stats.top16_coverage >= 0.80:
        return "flat_graphic"
    if stats.hard_edge_fraction > 0.25:
        return "photographic"
    if stats.smooth_transition_fraction >= 0.05:
        return "graphic_with_gradients"
    return "complex_graphic"


def choose_strategy(stats: ImageStats, image_class: str) -> Strategy:
    if image_class == "flat_graphic":
        s = Strategy(
            image_class=image_class,
            method="color-region tracing (pixel-exact boundaries + RDP simplification)",
            palette_mode="dominant",
            max_colors=16,
            simplify_epsilons=[0.35, 0.2, 0.1],
            min_loop_area=2.0,
        )
    elif image_class == "graphic_with_gradients":
        s = Strategy(
            image_class=image_class,
            method="posterized color-region tracing (gradients approximated by flat bands)",
            palette_mode="kmeans",
            max_colors=24,
            simplify_epsilons=[0.35, 0.2, 0.1],
            min_loop_area=2.0,
            human_review_reasons=[
                "Smooth color transitions detected; phase 1 approximates gradients "
                "with flat color bands instead of real vector gradients."
            ],
            expected_limitations=["gradients"],
        )
    elif image_class == "photographic":
        s = Strategy(
            image_class=image_class,
            method="posterized color-region tracing (low expected fidelity)",
            palette_mode="kmeans",
            max_colors=32,
            simplify_epsilons=[0.6, 0.35, 0.2],
            min_loop_area=4.0,
            human_review_reasons=[
                "Image looks photographic / highly textured; faithful editable "
                "vector reconstruction is not expected. Human must decide whether "
                "to continue."
            ],
            expected_limitations=["photographic texture", "gradients"],
        )
    else:
        s = Strategy(
            image_class=image_class,
            method="color-region tracing with k-means palette",
            palette_mode="kmeans",
            max_colors=16,
            simplify_epsilons=[0.35, 0.2, 0.1],
            min_loop_area=2.0,
        )
    if stats.has_alpha:
        s.human_review_reasons.append(
            "Source has transparency; phase 1 composites onto white and does "
            "not reconstruct transparency."
        )
        s.expected_limitations.append("transparency")
    return s


# --------------------------------------------------------------------------
# Palette extraction
# --------------------------------------------------------------------------


def dominant_palette(rgb: np.ndarray, max_colors: int, min_share: float = 0.002,
                     merge_dist: float = 24.0) -> np.ndarray:
    """Exact colors that form solid interior areas.

    Only "plateau" pixels (whose 3x3 neighbourhood is a single color) are
    counted, so anti-aliasing / resampling in-between colors that occur
    only along edges are excluded from the palette and later assigned to
    the nearest palette color.
    """
    h, w, _ = rgb.shape
    full = _pack(rgb).reshape(h, w)
    plateau = np.ones((h, w), dtype=bool)
    pad = np.pad(full, 1, mode="edge")
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            plateau &= pad[dy:dy + h, dx:dx + w] == full
    packed = full[plateau] if plateau.any() else full.ravel()
    values, counts = np.unique(packed, return_counts=True)
    order = np.argsort(counts)[::-1]
    values, counts = values[order], counts[order]
    min_count = max(int(min_share * h * w), 4)
    chosen: list[np.ndarray] = []
    for v, c in zip(values, counts):
        if c < min_count or len(chosen) >= max_colors:
            break
        col = _unpack(np.array([v]))[0].astype(np.float32)
        if all(np.linalg.norm(col - p) >= merge_dist for p in chosen):
            chosen.append(col)
    if not chosen:
        chosen.append(_unpack(values[:1])[0].astype(np.float32))
    return np.array(chosen, dtype=np.float32)


def kmeans_palette(rgb: np.ndarray, k: int, seed: int = 0, iters: int = 20,
                   sample: int = 20000) -> np.ndarray:
    """Deterministic k-means (k-means++ init) on RGB pixels."""
    rng = np.random.default_rng(seed)
    pts = rgb.reshape(-1, 3).astype(np.float32)
    if pts.shape[0] > sample:
        pts = pts[rng.choice(pts.shape[0], sample, replace=False)]
    uniq = np.unique(pts, axis=0)
    k = int(min(k, uniq.shape[0]))
    centers = [pts[rng.integers(pts.shape[0])]]
    d2 = ((pts - centers[0]) ** 2).sum(-1)
    for _ in range(1, k):
        if d2.sum() <= 0:
            break
        idx = rng.choice(pts.shape[0], p=d2 / d2.sum())
        centers.append(pts[idx])
        d2 = np.minimum(d2, ((pts - pts[idx]) ** 2).sum(-1))
    c = np.array(centers, dtype=np.float32)
    for _ in range(iters):
        lab = assign_labels_flat(pts, c)
        new = np.array([pts[lab == i].mean(0) if (lab == i).any() else c[i]
                        for i in range(c.shape[0])], dtype=np.float32)
        if np.allclose(new, c, atol=0.5):
            c = new
            break
        c = new
    return np.rint(c).astype(np.float32)


def assign_labels_flat(pts: np.ndarray, palette: np.ndarray) -> np.ndarray:
    d = ((pts[:, None, :].astype(np.float32) - palette[None, :, :]) ** 2).sum(-1)
    return d.argmin(1)


def assign_labels(rgb: np.ndarray, palette: np.ndarray) -> np.ndarray:
    h, w, _ = rgb.shape
    flat = rgb.reshape(-1, 3).astype(np.float32)
    out = np.empty(flat.shape[0], dtype=np.int32)
    step = 65536
    for i in range(0, flat.shape[0], step):
        out[i:i + step] = assign_labels_flat(flat[i:i + step], palette)
    return out.reshape(h, w)


def assign_labels_spatial(rgb: np.ndarray, palette: np.ndarray, radius: int = 2,
                          exact_tol: float = 6.0) -> np.ndarray:
    """Edge-aware labelling for anti-aliased flat artwork.

    Pixels within ``exact_tol`` of a palette color keep that label. Every
    other (in-between) pixel may only take a label that occurs among such
    solid pixels within ``radius`` px; this stops edge blends between two
    colors from being labelled as an unrelated third palette color.
    Falls back to plain nearest-color when no solid neighbour exists.
    """
    h, w, _ = rgb.shape
    flat = rgb.reshape(-1, 3).astype(np.float32)
    d = np.empty((flat.shape[0], palette.shape[0]), dtype=np.float32)
    step = 65536
    for i in range(0, flat.shape[0], step):
        blk = flat[i:i + step]
        d[i:i + step] = ((blk[:, None, :] - palette[None, :, :]) ** 2).sum(-1)
    nearest = d.argmin(1)
    exact = d[np.arange(d.shape[0]), nearest] <= exact_tol ** 2
    allowed = np.zeros_like(d, dtype=bool)
    exact2 = exact.reshape(h, w)
    near2 = nearest.reshape(h, w)
    for k in range(palette.shape[0]):
        allowed[:, k] = _dilate(exact2 & (near2 == k), radius).ravel()
    has_any = allowed.any(1)
    d_masked = np.where(allowed, d, np.inf)
    constrained = d_masked.argmin(1)
    out = np.where(exact | ~has_any, nearest, constrained)
    return out.reshape(h, w).astype(np.int32)


def build_palette(rgb: np.ndarray, strategy: Strategy, max_colors: int | None = None) -> np.ndarray:
    k = max_colors or strategy.max_colors
    if strategy.palette_mode == "dominant":
        return dominant_palette(rgb, k)
    return kmeans_palette(rgb, k)


def stats_to_dict(stats: ImageStats) -> dict:
    return asdict(stats)
