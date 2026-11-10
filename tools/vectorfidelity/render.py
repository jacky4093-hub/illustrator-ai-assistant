"""Render SVG to RGB arrays.

Two renderers:

* ``chromium``  - independent renderer (Playwright + Chromium via Node.js,
  ``tools/render_svg.cjs``). Used as the primary evidence when available.
  All network requests are blocked inside the page.
* ``internal``  - pure numpy even-odd scanline rasterizer for the SVG subset
  this toolkit writes (<rect>, <path> with M/L/H/V/Z). Used as a fallback
  and as a cross-check. It is NOT a general SVG renderer.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
from PIL import Image

RENDER_SCRIPT = Path(__file__).resolve().parent.parent / "render_svg.cjs"


class RenderError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# Internal rasterizer
# --------------------------------------------------------------------------

_TOK = re.compile(r"[MmLlHhVvZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?|[^\s,]")


def parse_path_subpaths(d: str) -> list[list[tuple[float, float]]]:
    toks = _TOK.findall(d)
    subs: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = 0.0
    sx = sy = 0.0
    cmd = None
    i = 0

    def num():
        nonlocal i
        v = float(toks[i])
        i += 1
        return v

    while i < len(toks):
        t = toks[i]
        if re.fullmatch(r"[A-Za-z]", t):
            if t not in "MmLlHhVvZz":
                raise RenderError(f"internal rasterizer does not support path command {t!r}")
            cmd = t
            i += 1
            if cmd in "Zz":
                if cur:
                    subs.append(cur)
                cur = []
                x, y = sx, sy
                continue
        if cmd is None:
            raise RenderError("path data must start with a command")
        if cmd in "Mm":
            nx, ny = num(), num()
            if cmd == "m":
                nx, ny = x + nx, y + ny
            if cur:
                subs.append(cur)
            cur = [(nx, ny)]
            x, y, sx, sy = nx, ny, nx, ny
            cmd = "L" if cmd == "M" else "l"
        elif cmd in "Ll":
            nx, ny = num(), num()
            if cmd == "l":
                nx, ny = x + nx, y + ny
            cur.append((nx, ny))
            x, y = nx, ny
        elif cmd in "Hh":
            nx = num()
            x = x + nx if cmd == "h" else nx
            cur.append((x, y))
        elif cmd in "Vv":
            ny = num()
            y = y + ny if cmd == "v" else ny
            cur.append((x, y))
    if cur:
        subs.append(cur)
    return subs


def _parse_color(v: str) -> tuple[int, int, int]:
    v = v.strip()
    if re.fullmatch(r"#[0-9a-fA-F]{6}", v):
        return tuple(int(v[i:i + 2], 16) for i in (1, 3, 5))
    if re.fullmatch(r"#[0-9a-fA-F]{3}", v):
        return tuple(int(c * 2, 16) for c in v[1:])
    raise RenderError(f"internal rasterizer: unsupported color {v!r}")


def _fill_evenodd(subpaths, ss_w: int, ss_h: int, s: int) -> np.ndarray:
    segs = []
    for sp in subpaths:
        if len(sp) < 2:
            continue
        p = np.array(sp, dtype=np.float64) * s
        q = np.roll(p, -1, axis=0)
        segs.append(np.hstack([p, q]))
    mask = np.zeros((ss_h, ss_w), dtype=bool)
    if not segs:
        return mask
    e = np.vstack(segs)
    x0, y0, x1, y1 = e.T
    nonh = y0 != y1
    x0, y0, x1, y1 = x0[nonh], y0[nonh], x1[nonh], y1[nonh]
    ymin = np.minimum(y0, y1)
    ymax = np.maximum(y0, y1)
    r0 = max(int(np.floor(ymin.min())), 0) if ymin.size else 0
    r1 = min(int(np.ceil(ymax.max())), ss_h) if ymax.size else 0
    for r in range(r0, r1):
        yc = r + 0.5
        act = (ymin <= yc) & (ymax > yc)
        if not act.any():
            continue
        ax0, ay0, ax1, ay1 = x0[act], y0[act], x1[act], y1[act]
        xs = np.sort(ax0 + (yc - ay0) * (ax1 - ax0) / (ay1 - ay0))
        row = mask[r]
        for a, b in zip(xs[0::2], xs[1::2]):
            c0 = max(int(np.ceil(a - 0.5)), 0)
            c1 = min(int(np.ceil(b - 0.5)), ss_w)
            if c1 > c0:
                row[c0:c1] ^= True
    return mask


def render_internal(svg_text: str, width: int, height: int, supersample: int = 4) -> np.ndarray:
    root = ET.fromstring(svg_text.encode("utf-8"))
    s = supersample
    canvas = np.full((height * s, width * s, 3), 255, dtype=np.uint8)
    for el in root.iter():
        tag = el.tag.split("}", 1)[-1]
        if tag == "rect":
            col = _parse_color(el.get("fill", "#000000"))
            x = float(el.get("x", 0)) * s
            y = float(el.get("y", 0)) * s
            w = float(el.get("width")) * s
            h = float(el.get("height")) * s
            canvas[int(round(y)):int(round(y + h)), int(round(x)):int(round(x + w))] = col
        elif tag == "path":
            if el.get("fill-rule", "nonzero") != "evenodd":
                raise RenderError("internal rasterizer only supports fill-rule=evenodd")
            col = _parse_color(el.get("fill", "#000000"))
            m = _fill_evenodd(parse_path_subpaths(el.get("d", "")), width * s, height * s, s)
            canvas[m] = col
        elif tag not in ("svg", "g", "title", "desc"):
            raise RenderError(f"internal rasterizer: unsupported element <{tag}>")
    out = canvas.reshape(height, s, width, s, 3).astype(np.float32).mean(axis=(1, 3))
    return np.clip(np.rint(out), 0, 255).astype(np.uint8)


# --------------------------------------------------------------------------
# Chromium renderer
# --------------------------------------------------------------------------


def chromium_available() -> tuple[bool, str]:
    node = shutil.which("node")
    if not node:
        return False, "node not found on PATH"
    if not RENDER_SCRIPT.exists():
        return False, f"missing {RENDER_SCRIPT}"
    try:
        r = subprocess.run([node, "-e", "require('playwright')"], capture_output=True,
                           text=True, timeout=30)
    except Exception as e:  # pragma: no cover
        return False, f"node check failed: {e}"
    if r.returncode != 0:
        return False, "playwright module not resolvable from node"
    return True, "ok"


def render_chromium(svg_path: str | Path, width: int, height: int,
                    timeout: int = 120) -> np.ndarray:
    ok, why = chromium_available()
    if not ok:
        raise RenderError(f"chromium renderer unavailable: {why}")
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "render.png")
        r = subprocess.run(
            [shutil.which("node"), str(RENDER_SCRIPT), str(svg_path), out, str(width), str(height)],
            capture_output=True, text=True, timeout=timeout,
        )
        if r.returncode != 0 or not os.path.exists(out):
            raise RenderError(f"chromium render failed: {r.stderr.strip()[:500]}")
        with Image.open(out) as im:
            arr = np.asarray(im.convert("RGB"), dtype=np.uint8).copy()
    if arr.shape[:2] != (height, width):
        raise RenderError(f"chromium output size {arr.shape[1]}x{arr.shape[0]} != {width}x{height}")
    return arr


def render(svg_path: str | Path, width: int, height: int, prefer: str = "chromium"):
    """Return (rgb, renderer_name, notes)."""
    notes = []
    if prefer == "chromium":
        try:
            return render_chromium(svg_path, width, height), "chromium", notes
        except RenderError as e:
            notes.append(str(e))
    text = Path(svg_path).read_text(encoding="utf-8")
    return render_internal(text, width, height), "internal", notes
