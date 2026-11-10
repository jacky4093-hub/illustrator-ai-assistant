"""Convert a color label map into vector regions (closed polygon loops).

Each palette color becomes one compound path filled with the even-odd rule.
Loops are traced along pixel boundaries (so the unsimplified result
reproduces the label mask exactly) and then simplified with
Ramer-Douglas-Peucker within a tolerance measured in pixels.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

Point = tuple[float, float]


@dataclass
class VectorRegion:
    color: tuple[int, int, int]
    loops: list[list[Point]]
    pixel_area: int

    @property
    def hex(self) -> str:
        return "#%02x%02x%02x" % self.color


@dataclass
class VectorDocument:
    width: int
    height: int
    background: tuple[int, int, int]
    regions: list[VectorRegion] = field(default_factory=list)
    dropped_tiny_loops: int = 0

    @property
    def path_count(self) -> int:
        return len(self.regions)

    @property
    def loop_count(self) -> int:
        return sum(len(r.loops) for r in self.regions)

    @property
    def point_count(self) -> int:
        return sum(len(lp) for r in self.regions for lp in r.loops)


# --------------------------------------------------------------------------
# Boundary extraction
# --------------------------------------------------------------------------

_DIRS = {(1, 0): 0, (0, 1): 1, (-1, 0): 2, (0, -1): 3}  # screen coords, y down


def boundary_loops(mask: np.ndarray) -> list[list[tuple[int, int]]]:
    """Trace all boundary loops of a boolean mask along pixel edges.

    Edges are oriented clockwise (on screen) around filled pixels, so every
    vertex has equal in/out degree and the edge set decomposes into closed
    loops. Under the even-odd rule the fill depends only on the edge set,
    so the loops reproduce the mask exactly.
    """
    m = np.pad(mask.astype(bool), 1)
    inside = m[1:-1, 1:-1]
    edges: dict[tuple[int, int], list[tuple[int, int]]] = {}

    def add(ys, xs, sx, sy, ex, ey):
        for y, x in zip(ys.tolist(), xs.tolist()):
            edges.setdefault((x + sx, y + sy), []).append((x + ex, y + ey))

    ys, xs = np.nonzero(inside & ~m[:-2, 1:-1])   # top edge: (x,y)->(x+1,y)
    add(ys, xs, 0, 0, 1, 0)
    ys, xs = np.nonzero(inside & ~m[1:-1, 2:])    # right: (x+1,y)->(x+1,y+1)
    add(ys, xs, 1, 0, 1, 1)
    ys, xs = np.nonzero(inside & ~m[2:, 1:-1])    # bottom: (x+1,y+1)->(x,y+1)
    add(ys, xs, 1, 1, 0, 1)
    ys, xs = np.nonzero(inside & ~m[1:-1, :-2])   # left: (x,y+1)->(x,y)
    add(ys, xs, 0, 1, 0, 0)

    loops: list[list[tuple[int, int]]] = []
    while edges:
        start = next(iter(edges))
        loop = [start]
        cur = start
        prev_dir = None
        while True:
            outs = edges[cur]
            if len(outs) == 1 or prev_dir is None:
                nxt = outs.pop(0)
            else:
                # Ambiguous vertex (diagonally touching pixels): prefer the
                # right turn so diagonal neighbours become separate loops.
                def turn(p):
                    d = (p[0] - cur[0], p[1] - cur[1])
                    return (_DIRS[d] - _DIRS[prev_dir]) % 4
                outs.sort(key=lambda p: {1: 0, 0: 1, 3: 2, 2: 3}[turn(p)])
                nxt = outs.pop(0)
            if not outs:
                del edges[cur]
            prev_dir = (nxt[0] - cur[0], nxt[1] - cur[1])
            cur = nxt
            if cur == start:
                # Balanced in/out degree guarantees the walk returns here.
                # Unused edges at a pinch vertex form their own loop later.
                break
            loop.append(cur)
        loops.append(loop)
    return loops


# --------------------------------------------------------------------------
# Simplification
# --------------------------------------------------------------------------


def remove_collinear(loop: list[Point]) -> list[Point]:
    n = len(loop)
    if n < 4:
        return list(loop)
    out = []
    for i in range(n):
        a, b, c = loop[i - 1], loop[i], loop[(i + 1) % n]
        cross = (b[0] - a[0]) * (c[1] - b[1]) - (b[1] - a[1]) * (c[0] - b[0])
        if cross != 0:
            out.append(b)
    return out


def smooth_staircase(loop: list[Point], corner_min_len: float = 3.0) -> list[Point]:
    """Replace pixel staircases by the midpoints of their segments.

    A vertex is kept as a sharp corner only when both adjacent segments are
    at least ``corner_min_len`` px long (e.g. corners of a rectangle);
    otherwise it is replaced by the midpoints of its segments, which turns
    1-px stair steps into a straight diagonal / smooth curve.
    """
    n = len(loop)
    if n < 4:
        return list(loop)
    out: list[Point] = []
    for i in range(n):
        a, b, c = loop[i - 1], loop[i], loop[(i + 1) % n]
        la = abs(b[0] - a[0]) + abs(b[1] - a[1])
        lc = abs(c[0] - b[0]) + abs(c[1] - b[1])
        if la >= corner_min_len and lc >= corner_min_len:
            out.append(b)
        out.append(((b[0] + c[0]) / 2.0, (b[1] + c[1]) / 2.0))
    return out


def _rdp(points: np.ndarray, eps: float) -> np.ndarray:
    if points.shape[0] < 3:
        return points
    keep = np.zeros(points.shape[0], dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, points.shape[0] - 1)]
    while stack:
        i, j = stack.pop()
        if j <= i + 1:
            continue
        a, b = points[i], points[j]
        seg = points[i + 1:j]
        ab = b - a
        L = np.hypot(*ab)
        if L == 0:
            d = np.hypot(*(seg - a).T)
        else:
            d = np.abs(ab[0] * (seg[:, 1] - a[1]) - ab[1] * (seg[:, 0] - a[0])) / L
        k = int(d.argmax())
        if d[k] > eps:
            idx = i + 1 + k
            keep[idx] = True
            stack.append((i, idx))
            stack.append((idx, j))
    return points[keep]


def simplify_closed(loop: list[Point], eps: float) -> list[Point]:
    pts = np.array(loop, dtype=np.float64)
    if pts.shape[0] <= 4 or eps <= 0:
        return [tuple(p) for p in pts.tolist()]
    # Split the ring at the vertex farthest from the first vertex.
    far = int(np.hypot(*(pts - pts[0]).T).argmax())
    a = np.vstack([pts[:far + 1]])
    b = np.vstack([pts[far:], pts[:1]])
    ra = _rdp(a, eps)
    rb = _rdp(b, eps)
    merged = np.vstack([ra[:-1], rb[:-1]])
    return [tuple(p) for p in merged.tolist()]


def polygon_area(loop: list[Point]) -> float:
    if len(loop) < 3:
        return 0.0
    p = np.array(loop, dtype=np.float64)
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


# --------------------------------------------------------------------------
# Label map -> vector document
# --------------------------------------------------------------------------


def vectorize_labels(labels: np.ndarray, palette: np.ndarray, epsilon: float,
                     min_loop_area: float = 2.0, smooth: bool = True) -> VectorDocument:
    h, w = labels.shape
    counts = np.bincount(labels.ravel(), minlength=palette.shape[0])
    order = [int(i) for i in np.argsort(counts)[::-1] if counts[i] > 0]
    bg_idx = order[0]
    bg = tuple(int(v) for v in palette[bg_idx])
    doc = VectorDocument(width=w, height=h, background=bg)
    # Background is drawn as a full-canvas rectangle; remaining colors are
    # stacked from largest to smallest area so that tiny seams between
    # simplified neighbouring regions reveal a plausible neighbour color.
    for idx in order[1:]:
        mask = labels == idx
        loops_out = []
        for raw in boundary_loops(mask):
            pts = remove_collinear([(float(x), float(y)) for x, y in raw])
            if smooth:
                pts = smooth_staircase(pts)
            simp = simplify_closed(pts, epsilon)
            if len(simp) < 3 or abs(polygon_area(simp)) < min_loop_area:
                doc.dropped_tiny_loops += 1
                continue
            loops_out.append(simp)
        if loops_out:
            doc.regions.append(VectorRegion(
                color=tuple(int(v) for v in palette[idx]),
                loops=loops_out,
                pixel_area=int(counts[idx]),
            ))
    return doc
