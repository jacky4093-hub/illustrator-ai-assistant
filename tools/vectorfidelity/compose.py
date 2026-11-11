"""Compose mode: rebuild artwork that mixes text, regular shapes and icons.

Whole-image tracing turns small text into unreadable shapes. For price
lists, stickers, signs and similar layouts this module builds one SVG from a
layout spec (JSON) instead:

* ``texts``        -> real SVG <text> objects (editable type in Illustrator)
* ``shapes``       -> native rect / circle / ellipse / line / polygon / path
                      (cubic Beziers, optional linear gradients)
* ``icons``        -> each icon is cropped from the source image, enlarged,
                      flattened to a few solid colors and traced on its own
* ``placeholders`` -> dashed boxes for things tracing cannot restore
                      (official logos, QR codes); always reported as gaps

The spec is written by the model after looking at the image; text content,
positions and font sizes are therefore estimates that a person must check.
Nothing here contacts Illustrator. The result is an SVG to open with
File > Open; whether Illustrator shows the text as editable type with the
requested fonts is NOT VERIFIED by this tool.
"""

from __future__ import annotations

import json
import math
import os
import re
import time
from pathlib import Path
from xml.sax.saxutils import escape, quoteattr

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import __version__
from .analyze import assign_labels, kmeans_palette
from .compare import srgb_to_lab
from .imageio import load_image, save_rgb
from .outputs import OutputConflictError, _same_file
from .svg import SVG_NS, loops_to_path_data, validate_svg
from .trace import (boundary_loops, polygon_area, remove_collinear, simplify_closed,
                    smooth_staircase)

SPEC_VERSION = 1
OUTPUT_FILES = ("composed.svg", "preview.png", "compare.png", "compose_report.json",
                "compose_report.md")
ICON_DIR = "icons"

OUTCOME_COMPOSED = "COMPOSED_NEEDS_REVIEW"            # valid SVG, no declared gaps
OUTCOME_COMPOSED_GAPS = "COMPOSED_WITH_PLACEHOLDERS"  # valid SVG, placeholders / gaps remain
OUTCOME_INVALID = "FAILED"

_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$")
_FONT_FAMILY = re.compile(r"^[A-Za-z0-9 ._-]{1,80}$")
_GROUP = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,40}$")
_DASH = re.compile(r"^[0-9. ,]{1,40}$")


class SpecError(ValueError):
    """The layout spec is malformed. Nothing has been written."""


# --------------------------------------------------------------------------
# Spec validation
# --------------------------------------------------------------------------


def _num(v, what, lo=-1e6, hi=1e6):
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) \
            or not lo <= v <= hi:
        raise SpecError(f"{what}: expected a number between {lo:g} and {hi:g}, got {v!r}")
    return float(v)


def _pt(v, what):
    if not isinstance(v, (list, tuple)) or len(v) != 2:
        raise SpecError(f"{what}: expected [x, y]")
    return (_num(v[0], what), _num(v[1], what))


def _color(v, what, gradients=(), allow_none=True):
    if v is None or v == "none":
        if allow_none:
            return "none"
        raise SpecError(f"{what}: a color is required")
    if isinstance(v, str) and _COLOR.match(v):
        return v.lower()
    if isinstance(v, str) and v.startswith("gradient:") and v[9:] in gradients:
        return f"url(#{v[9:]})"
    raise SpecError(f"{what}: expected '#rrggbb', 'none' or 'gradient:<id>', got {v!r}")


def _box(v, what, width, height):
    if not isinstance(v, (list, tuple)) or len(v) != 4:
        raise SpecError(f"{what}: expected [x0, y0, x1, y1]")
    x0, y0, x1, y1 = (_num(c, what) for c in v)
    if not (0 <= x0 < x1 <= width and 0 <= y0 < y1 <= height):
        raise SpecError(f"{what}: box {v} is empty or outside the {width}x{height} canvas")
    return (x0, y0, x1, y1)


def _paint(item, what, gradients):
    out = {"fill": _color(item.get("fill"), f"{what}.fill", gradients)}
    stroke = _color(item.get("stroke"), f"{what}.stroke", gradients)
    if stroke != "none":
        out["stroke"] = stroke
        out["stroke_width"] = _num(item.get("stroke_width", 1), f"{what}.stroke_width", 0.01, 500)
        dash = item.get("dash")
        if dash is not None:
            if not isinstance(dash, str) or not _DASH.match(dash):
                raise SpecError(f"{what}.dash: expected numbers separated by spaces")
            out["dash"] = dash
    if out["fill"] == "none" and "stroke" not in out:
        raise SpecError(f"{what}: needs a fill or a stroke")
    return out


def normalize_spec(spec: dict, width: int, height: int) -> dict:
    """Validate the spec and return a normalized copy. Raises SpecError."""
    if not isinstance(spec, dict):
        raise SpecError("spec must be a JSON object")
    if spec.get("version") != SPEC_VERSION:
        raise SpecError(f"spec.version must be {SPEC_VERSION}")
    out = {"background": _color(spec.get("background", "#ffffff"), "background", allow_none=False)}

    fonts = {}
    for key, f in (spec.get("fonts") or {}).items():
        if not _NAME.match(str(key)) or not isinstance(f, dict):
            raise SpecError(f"fonts.{key}: invalid entry")
        fam = f.get("family")
        if not isinstance(fam, str) or not _FONT_FAMILY.match(fam):
            raise SpecError(f"fonts.{key}.family: use the font's PostScript or family name "
                            "(letters, digits, space, . _ -)")
        file = f.get("file")
        if file is not None and not isinstance(file, str):
            raise SpecError(f"fonts.{key}.file: expected a path string")
        fonts[key] = {"family": fam, "file": file}
    out["fonts"] = fonts

    grads = {}
    for gid, g in (spec.get("gradients") or {}).items():
        if not _GROUP.match(str(gid)) or not isinstance(g, dict):
            raise SpecError(f"gradients.{gid}: invalid id or entry")
        stops = g.get("stops")
        if not isinstance(stops, list) or len(stops) < 2:
            raise SpecError(f"gradients.{gid}.stops: need at least two [offset, color] stops")
        grads[gid] = {
            "x1": _num(g.get("x1"), f"gradients.{gid}.x1"), "y1": _num(g.get("y1"), f"gradients.{gid}.y1"),
            "x2": _num(g.get("x2"), f"gradients.{gid}.x2"), "y2": _num(g.get("y2"), f"gradients.{gid}.y2"),
            "stops": [(_num(s[0], f"gradients.{gid}.stops offset", 0, 1),
                       _color(s[1], f"gradients.{gid}.stops color", allow_none=False))
                      for s in stops if isinstance(s, (list, tuple)) and len(s) == 2],
        }
        if len(grads[gid]["stops"]) != len(stops):
            raise SpecError(f"gradients.{gid}.stops: each stop must be [offset, '#rrggbb']")
    out["gradients"] = grads

    shapes = []
    for i, s in enumerate(spec.get("shapes") or []):
        what = f"shapes[{i}]"
        if not isinstance(s, dict):
            raise SpecError(f"{what}: expected an object")
        kind = s.get("type")
        group = s.get("group", "shapes")
        if not isinstance(group, str) or not _GROUP.match(group):
            raise SpecError(f"{what}.group: invalid group name")
        n = {"type": kind, "group": group}
        if kind == "line":
            n.update(x1=_num(s.get("x1"), what), y1=_num(s.get("y1"), what),
                     x2=_num(s.get("x2"), what), y2=_num(s.get("y2"), what),
                     stroke=_color(s.get("stroke"), f"{what}.stroke", grads, allow_none=False),
                     stroke_width=_num(s.get("stroke_width", 1), what, 0.01, 500), fill="none")
        else:
            n.update(_paint(s, what, grads))
            if kind == "rect":
                n.update(x=_num(s.get("x"), what), y=_num(s.get("y"), what),
                         w=_num(s.get("w"), what, 0.01), h=_num(s.get("h"), what, 0.01),
                         rx=_num(s.get("rx", 0), what, 0))
            elif kind == "circle":
                n.update(cx=_num(s.get("cx"), what), cy=_num(s.get("cy"), what),
                         r=_num(s.get("r"), what, 0.01))
            elif kind == "ellipse":
                n.update(cx=_num(s.get("cx"), what), cy=_num(s.get("cy"), what),
                         rx=_num(s.get("rx"), what, 0.01), ry=_num(s.get("ry"), what, 0.01),
                         rotate=_num(s.get("rotate", 0), what, -360, 360))
            elif kind == "polygon":
                pts = s.get("points")
                if not isinstance(pts, list) or len(pts) < 3:
                    raise SpecError(f"{what}.points: need at least three points")
                n["points"] = [_pt(p, f"{what}.points") for p in pts]
            elif kind == "path":
                n["start"] = _pt(s.get("start"), f"{what}.start")
                segs = []
                for seg in s.get("segments") or []:
                    if isinstance(seg, (list, tuple)) and len(seg) == 2 and seg[0] == "L":
                        segs.append(("L", _pt(seg[1], what)))
                    elif isinstance(seg, (list, tuple)) and len(seg) == 4 and seg[0] == "C":
                        segs.append(("C", _pt(seg[1], what), _pt(seg[2], what), _pt(seg[3], what)))
                    else:
                        raise SpecError(f'{what}.segments: use ["L",[x,y]] or '
                                        '["C",[x,y],[x,y],[x,y]]')
                if not segs:
                    raise SpecError(f"{what}.segments: at least one segment is required")
                n["segments"] = segs
                n["closed"] = bool(s.get("closed", True))
            else:
                raise SpecError(f"{what}.type: unknown shape type {kind!r}")
        shapes.append(n)
    out["shapes"] = shapes

    texts = []
    for i, t in enumerate(spec.get("texts") or []):
        what = f"texts[{i}]"
        if not isinstance(t, dict) or not isinstance(t.get("text"), str) or not t["text"].strip():
            raise SpecError(f"{what}: needs a non-empty 'text'")
        if re.search(r"[\x00-\x08\x0b-\x1f]", t["text"]) or "\n" in t["text"]:
            raise SpecError(f"{what}.text: one line per text object, no control characters")
        font = t.get("font")
        if font not in fonts:
            raise SpecError(f"{what}.font: {font!r} is not defined under 'fonts'")
        anchor = t.get("anchor", "start")
        if anchor not in ("start", "middle", "end"):
            raise SpecError(f"{what}.anchor: start, middle or end")
        n = {"x": _num(t.get("x"), what), "y": _num(t.get("y"), what), "text": t["text"],
             "size": _num(t.get("size"), f"{what}.size", 1, 5000), "font": font,
             "fill": _color(t.get("fill", "#000000"), f"{what}.fill"), "anchor": anchor}
        stroke = _color(t.get("stroke"), f"{what}.stroke")
        if stroke != "none":
            n["stroke"] = stroke
            n["stroke_width"] = _num(t.get("stroke_width", 1), what, 0.01, 500)
        if n["fill"] == "none" and "stroke" not in n:
            raise SpecError(f"{what}: needs a fill or a stroke")
        texts.append(n)
    out["texts"] = texts

    icons, seen = [], set()
    for i, ic in enumerate(spec.get("icons") or []):
        what = f"icons[{i}]"
        name = ic.get("name") if isinstance(ic, dict) else None
        if not isinstance(name, str) or not _NAME.match(name) or name in seen:
            raise SpecError(f"{what}.name: unique name of letters, digits, _ . - required")
        seen.add(name)
        n = {"name": name, "box": _box(ic.get("box"), f"{what}.box", width, height),
             "colors": int(_num(ic.get("colors", 2), f"{what}.colors", 2, 8)),
             "scale": int(_num(ic.get("scale", 4), f"{what}.scale", 1, 8)),
             "keep_background": bool(ic.get("keep_background", False))}
        pal = ic.get("palette")
        if pal is not None:
            if not isinstance(pal, list) or len(pal) < 2:
                raise SpecError(f"{what}.palette: list at least two '#rrggbb' colors")
            n["palette"] = [_color(c, f"{what}.palette", allow_none=False) for c in pal]
        icons.append(n)
    out["icons"] = icons

    holders = []
    for i, p in enumerate(spec.get("placeholders") or []):
        what = f"placeholders[{i}]"
        name = p.get("name") if isinstance(p, dict) else None
        if not isinstance(name, str) or not _NAME.match(name):
            raise SpecError(f"{what}.name: letters, digits, _ . - required")
        note = p.get("note", "replace with the original artwork")
        if not isinstance(note, str) or len(note) > 200:
            raise SpecError(f"{what}.note: short text expected")
        holders.append({"name": name, "box": _box(p.get("box"), f"{what}.box", width, height),
                        "note": note})
    out["placeholders"] = holders

    if not (shapes or texts or icons):
        raise SpecError("spec has no shapes, texts or icons")
    return out


# --------------------------------------------------------------------------
# Icons
# --------------------------------------------------------------------------


def _hex(c) -> str:
    return "#%02x%02x%02x" % tuple(int(v) for v in c)


def _rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def _core_palette(rgb: np.ndarray, k: int) -> np.ndarray:
    """k-means palette, then each color is re-estimated from the core of its
    cluster so anti-aliased edge pixels do not wash the ink colors out."""
    pal = kmeans_palette(rgb, k)
    pts = rgb.reshape(-1, 3).astype(np.float32)
    d = ((pts[:, None, :] - pal[None, :, :]) ** 2).sum(-1)
    lab = d.argmin(1)
    out = pal.copy()
    for i in range(pal.shape[0]):
        m = lab == i
        if m.sum() < 4:
            continue
        own = d[m, i]
        core = pts[m][own <= np.median(own)]
        out[i] = np.rint(core.mean(0))
    return out


def trace_icon(source_rgb: np.ndarray, icon: dict) -> dict:
    """Crop, enlarge, flatten to solid colors and trace one icon.

    Returns paths in document coordinates. The color that dominates the
    border of the crop is treated as the icon's background and is not drawn
    unless ``keep_background`` is set. Tracing uses the same boundary,
    smoothing and simplification steps as whole-image mode.
    """
    x0, y0, x1, y1 = (int(round(v)) for v in icon["box"])
    crop = source_rgb[y0:y1, x0:x1]
    s = icon["scale"]
    if icon.get("palette"):
        palette = np.array([_rgb(c) for c in icon["palette"]], dtype=np.float32)
    else:
        palette = _core_palette(crop, icon["colors"])
    big = np.asarray(Image.fromarray(crop, "RGB").resize(
        (crop.shape[1] * s, crop.shape[0] * s), Image.LANCZOS), dtype=np.uint8)
    labels = assign_labels(big, palette)
    flat = palette[labels].astype(np.uint8)
    border = np.concatenate([labels[0], labels[-1], labels[:, 0], labels[:, -1]])
    counts = np.bincount(labels.ravel(), minlength=palette.shape[0])
    bg = int(np.bincount(border, minlength=palette.shape[0]).argmax())
    background = _hex(palette[bg])
    paths, points, dropped = [], 0, 0
    if icon["keep_background"]:
        paths.append({"fill": background,
                      "d": f"M{x0} {y0} L{x1} {y0} L{x1} {y1} L{x0} {y1} Z"})
    # Larger areas first, so smaller details are stacked on top.
    for idx in [int(i) for i in np.argsort(counts)[::-1] if counts[i] > 0 and i != bg]:
        loops = []
        for raw in boundary_loops(labels == idx):
            pts = smooth_staircase(remove_collinear([(float(x), float(y)) for x, y in raw]))
            simp = simplify_closed(pts, 0.3 * s)
            if len(simp) < 3 or abs(polygon_area(simp)) < 2.0 * s * s:
                dropped += 1
                continue
            loops.append([(x0 + px / s, y0 + py / s) for px, py in simp])
            points += len(simp)
        if loops:
            paths.append({"fill": _hex(palette[idx]), "d": loops_to_path_data(loops)})
    # Agreement between the flattened crop and the original crop (enlarged).
    diff = np.abs(flat.astype(np.int16) - big.astype(np.int16)).max(-1)
    return {"name": icon["name"], "box": [x0, y0, x1, y1], "paths": paths,
            "palette": [_hex(c) for c in palette], "background": background,
            "points": points, "regions": len(paths) - int(icon["keep_background"]),
            "dropped_tiny_loops": dropped,
            "flatten_match": round(float((diff <= 48).mean()), 4),
            "_flat": flat, "_scale": s}


# --------------------------------------------------------------------------
# SVG
# --------------------------------------------------------------------------


def _g(v: float) -> str:
    return ("%.3f" % v).rstrip("0").rstrip(".") or "0"


def _paint_attrs(n: dict) -> str:
    s = f' fill="{n.get("fill", "none")}"'
    if n.get("stroke"):
        s += f' stroke="{n["stroke"]}" stroke-width="{_g(n["stroke_width"])}"'
        if n.get("dash"):
            s += f' stroke-dasharray="{n["dash"]}"'
    return s


def _shape_svg(n: dict) -> str:
    k = n["type"]
    if k == "rect":
        rx = f' rx="{_g(n["rx"])}"' if n["rx"] else ""
        return (f'<rect x="{_g(n["x"])}" y="{_g(n["y"])}" width="{_g(n["w"])}" '
                f'height="{_g(n["h"])}"{rx}{_paint_attrs(n)}/>')
    if k == "circle":
        return f'<circle cx="{_g(n["cx"])}" cy="{_g(n["cy"])}" r="{_g(n["r"])}"{_paint_attrs(n)}/>'
    if k == "ellipse":
        rot = (f' transform="rotate({_g(n["rotate"])} {_g(n["cx"])} {_g(n["cy"])})"'
               if n["rotate"] else "")
        return (f'<ellipse cx="{_g(n["cx"])}" cy="{_g(n["cy"])}" rx="{_g(n["rx"])}" '
                f'ry="{_g(n["ry"])}"{rot}{_paint_attrs(n)}/>')
    if k == "line":
        return (f'<line x1="{_g(n["x1"])}" y1="{_g(n["y1"])}" x2="{_g(n["x2"])}" '
                f'y2="{_g(n["y2"])}" stroke="{n["stroke"]}" stroke-width="{_g(n["stroke_width"])}"/>')
    if k == "polygon":
        pts = " ".join(f"{_g(x)},{_g(y)}" for x, y in n["points"])
        return f'<polygon points="{pts}"{_paint_attrs(n)}/>'
    d = f'M{_g(n["start"][0])} {_g(n["start"][1])}'
    for seg in n["segments"]:
        if seg[0] == "L":
            d += f' L{_g(seg[1][0])} {_g(seg[1][1])}'
        else:
            d += " C" + " ".join(f"{_g(p[0])} {_g(p[1])}" for p in seg[1:])
    if n["closed"]:
        d += " Z"
    return f'<path d="{d}"{_paint_attrs(n)}/>'


def _text_svg(t: dict, fonts: dict) -> str:
    a = (f' x="{_g(t["x"])}" y="{_g(t["y"])}" font-family={quoteattr(fonts[t["font"]]["family"])}'
         f' font-size="{_g(t["size"])}" fill="{t["fill"]}"')
    if t["anchor"] != "start":
        a += f' text-anchor="{t["anchor"]}"'
    if t.get("stroke"):
        a += (f' stroke="{t["stroke"]}" stroke-width="{_g(t["stroke_width"])}"'
              ' stroke-linejoin="round"')
    return f"<text{a}>{escape(t['text'])}</text>"


def build_svg(spec: dict, icons: list[dict], width: int, height: int) -> str:
    out = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<svg xmlns="{SVG_NS}" width="{width}" height="{height}" '
           f'viewBox="0 0 {width} {height}">',
           "  <title>vector-fidelity composed layout</title>"]
    if spec["gradients"]:
        out.append("  <defs>")
        for gid, g in spec["gradients"].items():
            stops = "".join(f'<stop offset="{_g(o)}" stop-color="{c}"/>' for o, c in g["stops"])
            out.append(f'    <linearGradient id="{gid}" gradientUnits="userSpaceOnUse" '
                       f'x1="{_g(g["x1"])}" y1="{_g(g["y1"])}" x2="{_g(g["x2"])}" '
                       f'y2="{_g(g["y2"])}">{stops}</linearGradient>')
        out.append("  </defs>")
    out.append(f'  <g id="background"><rect x="0" y="0" width="{width}" height="{height}" '
               f'fill="{spec["background"]}"/></g>')
    groups: dict[str, list[str]] = {}
    for n in spec["shapes"]:
        groups.setdefault(n["group"], []).append(_shape_svg(n))
    for gname, items in groups.items():
        out.append(f'  <g id="shapes-{gname}">')
        out += ["    " + i for i in items]
        out.append("  </g>")
    if icons:
        out.append('  <g id="icons">')
        for ic in icons:
            out.append(f'    <g id="icon-{ic["name"]}">')
            out += [f'      <path fill="{p["fill"]}" fill-rule="evenodd" d="{p["d"]}"/>'
                    for p in ic["paths"]]
            out.append("    </g>")
        out.append("  </g>")
    if spec["texts"]:
        out.append('  <g id="text">')
        out += ["    " + _text_svg(t, spec["fonts"]) for t in spec["texts"]]
        out.append("  </g>")
    if spec["placeholders"]:
        out.append('  <g id="placeholders-replace-before-use">')
        for p in spec["placeholders"]:
            x0, y0, x1, y1 = p["box"]
            out.append(f'    <rect id="placeholder-{p["name"]}" x="{_g(x0)}" y="{_g(y0)}" '
                       f'width="{_g(x1 - x0)}" height="{_g(y1 - y0)}" fill="none" '
                       'stroke="#ff2d95" stroke-width="1.5" stroke-dasharray="6 4"/>')
        out.append("  </g>")
    out += ["</svg>", ""]
    return "\n".join(out)


def validate_composed_svg(text: str, width: int, height: int, n_texts: int) -> dict:
    """No embedded raster / scripts / external references, correct canvas,
    and every spec text present as a real <text> element."""
    base = validate_svg(text, width, height)
    errors = [e for e in base.errors if not e.startswith("no <path> elements")]
    found = len(re.findall(r"<text[ >]", text))
    if found != n_texts:
        errors.append(f"expected {n_texts} <text> elements, found {found}")
    return {"ok": not errors, "errors": errors, "text_elements": found,
            "path_elements": base.path_count}


# --------------------------------------------------------------------------
# Preview (approximate: PIL drawing, not Illustrator)
# --------------------------------------------------------------------------


def _bezier(p0, c1, c2, p1, n=24):
    return [tuple((1 - u) ** 3 * p0[i] + 3 * (1 - u) ** 2 * u * c1[i] + 3 * (1 - u) * u * u * c2[i]
                  + u ** 3 * p1[i] for i in (0, 1)) for u in (k / n for k in range(1, n + 1))]


def _preview_color(v, spec):
    if not v or v == "none":
        return None
    if v.startswith("url(#"):
        stops = spec["gradients"][v[5:-1]]["stops"]
        cols = np.array([_rgb(c) for _, c in stops], dtype=np.float32)
        return tuple(int(x) for x in np.rint(cols.mean(0)))  # gradient shown as its mean color
    return _rgb(v)


def _load_font(entry: dict, size: float, spec_dir: Path):
    file = entry.get("file")
    if file:
        for cand in (Path(file), spec_dir / file):
            if cand.is_file():
                try:
                    return ImageFont.truetype(str(cand), max(1, int(round(size)))), True
                except OSError:
                    break
    try:
        return ImageFont.load_default(max(1, int(round(size)))), False
    except TypeError:  # very old Pillow
        return ImageFont.load_default(), False


def render_preview(spec: dict, icons: list[dict], width: int, height: int,
                   spec_dir: Path) -> tuple[np.ndarray, dict]:
    im = Image.new("RGB", (width, height), _rgb(spec["background"]))
    d = ImageDraw.Draw(im)
    for n in spec["shapes"]:
        fill = _preview_color(n.get("fill"), spec)
        stroke = _preview_color(n.get("stroke"), spec)
        sw = max(1, int(round(n.get("stroke_width", 1))))
        k = n["type"]
        if k == "rect":
            d.rounded_rectangle([n["x"], n["y"], n["x"] + n["w"], n["y"] + n["h"]],
                                radius=min(n["rx"], n["w"] / 2, n["h"] / 2),
                                fill=fill, outline=stroke, width=sw)
        elif k == "circle":
            d.ellipse([n["cx"] - n["r"], n["cy"] - n["r"], n["cx"] + n["r"], n["cy"] + n["r"]],
                      fill=fill, outline=stroke, width=sw)
        elif k == "ellipse":
            ang = math.radians(n["rotate"])
            pts = [(n["cx"] + n["rx"] * math.cos(u) * math.cos(ang) - n["ry"] * math.sin(u) * math.sin(ang),
                    n["cy"] + n["rx"] * math.cos(u) * math.sin(ang) + n["ry"] * math.sin(u) * math.cos(ang))
                   for u in (i * math.pi / 24 for i in range(48))]
            d.polygon(pts, fill=fill, outline=stroke)
        elif k == "line":
            d.line([n["x1"], n["y1"], n["x2"], n["y2"]], fill=stroke, width=sw)
        elif k == "polygon":
            d.polygon(n["points"], fill=fill, outline=stroke)
        else:
            pts, cur = [n["start"]], n["start"]
            for seg in n["segments"]:
                if seg[0] == "C":
                    pts += _bezier(cur, seg[1], seg[2], seg[3])
                    cur = seg[3]
                else:
                    pts.append(seg[1])
                    cur = seg[1]
            if n["closed"] and fill and len(pts) >= 3:
                d.polygon(pts, fill=fill, outline=stroke)
            elif len(pts) >= 2:
                d.line(pts + ([pts[0]] if n["closed"] else []), fill=stroke or fill, width=sw)
    for ic in icons:
        x0, y0, x1, y1 = ic["box"]
        flat = Image.fromarray(ic["_flat"], "RGB").resize((x1 - x0, y1 - y0), Image.LANCZOS)
        if ic.get("_keep_background"):
            im.paste(flat, (x0, y0))
        else:
            arr = np.asarray(flat, dtype=np.int16)
            bg = np.array(_rgb(ic["background"]), dtype=np.int16)
            mask = Image.fromarray(((np.abs(arr - bg).max(-1) > 40) * 255).astype(np.uint8), "L")
            im.paste(flat, (x0, y0), mask)
    fonts_used = {}
    anchors = {"start": "ls", "middle": "ms", "end": "rs"}
    for t in spec["texts"]:
        font, real = _load_font(spec["fonts"][t["font"]], t["size"], spec_dir)
        fonts_used[t["font"]] = real
        kw = {}
        if t.get("stroke"):
            kw = {"stroke_width": max(1, int(round(t["stroke_width"] / 2))),
                  "stroke_fill": _rgb(t["stroke"])}
        fill = _rgb(t["fill"]) if t["fill"] != "none" else kw.get("stroke_fill")
        try:
            d.text((t["x"], t["y"]), t["text"], font=font, fill=fill, anchor=anchors[t["anchor"]], **kw)
        except (ValueError, UnicodeEncodeError):
            d.text((t["x"], t["y"] - t["size"]), t["text"], font=font, fill=fill)
    for p in spec["placeholders"]:
        x0, y0, x1, y1 = p["box"]
        d.rectangle([x0, y0, x1, y1], outline=(255, 45, 149), width=2)
    return np.asarray(im, dtype=np.uint8), fonts_used


# --------------------------------------------------------------------------
# Pipeline
# --------------------------------------------------------------------------


def _check_out_dir(out: Path, source: Path, spec_path: Path) -> None:
    if out.exists() and not out.is_dir():
        raise OutputConflictError(f"output path exists and is not a directory: {out}", [str(out)])
    if out.is_dir() and os.listdir(out):
        raise OutputConflictError(
            "refusing to run: output directory is not empty (compose never overwrites; "
            "use a new folder)", sorted(os.listdir(out)))
    for name in OUTPUT_FILES:
        for protected in (source, spec_path):
            if _same_file(out / name, protected):
                raise OutputConflictError("an output file would overwrite an input",
                                          [str(out / name)])


def compose(spec_path: str | Path, out_dir: str | Path) -> dict:
    """Build composed.svg from a layout spec. Raises SpecError or
    OutputConflictError before anything is written."""
    t0 = time.time()
    spec_path = Path(spec_path)
    try:
        raw = json.loads(spec_path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as e:
        raise SpecError(f"cannot read spec: {e}") from e
    src = raw.get("source") if isinstance(raw, dict) else None
    if not isinstance(src, str) or not src:
        raise SpecError("spec.source: path of the reference image is required")
    source = Path(src) if Path(src).is_absolute() else spec_path.parent / src
    if not source.is_file():
        raise SpecError(f"spec.source not found: {source}")
    img = load_image(source)
    spec = normalize_spec(raw, img.width, img.height)
    out = Path(out_dir)
    _check_out_dir(out, source, spec_path)

    icons = []
    for ic in spec["icons"]:
        traced = trace_icon(img.rgb, ic)
        traced["_keep_background"] = ic["keep_background"]
        icons.append(traced)
    svg = build_svg(spec, icons, img.width, img.height)
    validation = validate_composed_svg(svg, img.width, img.height, len(spec["texts"]))
    preview, fonts_used = render_preview(spec, icons, img.width, img.height, spec_path.parent)

    diff = np.abs(img.rgb.astype(np.int16) - preview.astype(np.int16)).max(-1)
    de = np.linalg.norm(srgb_to_lab(img.rgb) - srgb_to_lab(preview), axis=-1)
    side = np.concatenate([img.rgb, preview], axis=1)

    out.mkdir(parents=True, exist_ok=True)
    (out / "composed.svg").write_text(svg, encoding="utf-8", newline="\n")
    save_rgb(preview, out / "preview.png")
    save_rgb(side, out / "compare.png")
    if icons:
        (out / ICON_DIR).mkdir()
        for ic in icons:
            x0, y0, x1, y1 = ic["box"]
            body = "".join(f'<path fill="{p["fill"]}" fill-rule="evenodd" d="{p["d"]}"/>'
                           for p in ic["paths"])
            (out / ICON_DIR / f'{ic["name"]}.svg').write_text(
                f'<?xml version="1.0" encoding="UTF-8"?>\n<svg xmlns="{SVG_NS}" '
                f'width="{x1 - x0}" height="{y1 - y0}" viewBox="{x0} {y0} {x1 - x0} {y1 - y0}">'
                f'{body}</svg>\n', encoding="utf-8", newline="\n")

    fonts_report = [{"key": k, "family": v["family"],
                     "preview_used_real_font": bool(fonts_used.get(k, False)),
                     "used_by_texts": sum(1 for t in spec["texts"] if t["font"] == k)}
                    for k, v in spec["fonts"].items()]
    gaps = [f"Placeholder '{p['name']}': {p['note']}" for p in spec["placeholders"]]
    notes = [
        "Text content, positions and font sizes were estimated from the image: proofread "
        "every text and adjust positions in Illustrator.",
        "Fonts are referenced by name. If Illustrator reports a missing font, pick an "
        "installed one; the text content is unaffected.",
        "Icons are traced with straight segments from flattened crops; fine internal "
        "detail is simplified.",
        "preview.png is drawn with Pillow, not Illustrator. Fonts without a readable "
        "'file' use a stand-in font, and gradients are shown as one flat color.",
    ]
    ok = validation["ok"]
    result = {
        "tool": "vector-fidelity", "mode": "compose", "version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": {"path": str(source), "width": img.width, "height": img.height},
        "spec": str(spec_path),
        "counts": {"texts": len(spec["texts"]), "shapes": len(spec["shapes"]),
                   "icons": len(icons), "placeholders": len(spec["placeholders"]),
                   "gradients": len(spec["gradients"])},
        "icons": [{k: v for k, v in ic.items() if not k.startswith("_") and k != "paths"}
                  for ic in icons],
        "fonts": fonts_report,
        "svg_validation": validation,
        "preview_comparison": {
            "note": "informational only; the preview is approximate",
            "pixel_match_ratio": round(float((diff <= 24).mean()), 5),
            "mean_delta_e": round(float(de.mean()), 4)},
        "known_gaps": gaps,
        "notes": notes,
        "outputs": {"svg": "composed.svg", "preview_png": "preview.png",
                    "compare_png": "compare.png", "icon_svgs": ICON_DIR if icons else None,
                    "ai_file": None},
        "status": {
            "svg_generated": "PASS",
            "svg_valid": "PASS" if ok else "FAIL",
            "no_embedded_raster": "PASS" if ok else "FAIL",
            "text_as_text_objects": ("PASS" if ok and spec["texts"] else
                                     "none in spec" if ok else "FAIL"),
            "text_content": "NOT VERIFIED (transcribed from the image; proofread)",
            "layout_fidelity": "NOT VERIFIED (positions estimated; check compare.png)",
            "opens_in_illustrator": "NOT VERIFIED",
            "ai_file": "NOT VERIFIED (not produced; open composed.svg and save as .ai)",
            "placeholders": (f"NOT RESTORED ({len(gaps)} to replace)" if gaps else "none"),
            "human_review": "PENDING",
            "overall": ("FAILED" if not ok else
                        "NEEDS_ATTENTION_UNRESTORED" if gaps else "AWAITING_HUMAN_REVIEW"),
        },
        "outcome": (OUTCOME_INVALID if not ok else
                    OUTCOME_COMPOSED_GAPS if gaps else OUTCOME_COMPOSED),
        "elapsed_seconds": round(time.time() - t0, 2),
    }
    (out / "compose_report.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8", newline="\n")
    (out / "compose_report.md").write_text(_report_md(result), encoding="utf-8", newline="\n")
    return result


def _report_md(r: dict) -> str:
    L = ["# Vector Fidelity Compose Report", "",
         f"- Source: `{r['source']['path']}` ({r['source']['width']}x{r['source']['height']})",
         f"- Spec: `{r['spec']}`",
         f"- Generated: {r['generated_at']} by vector-fidelity {r['version']} (compose mode)",
         "- Contents: " + ", ".join(f"{v} {k}" for k, v in r["counts"].items()), "",
         "## Status", "", "| Item | Result |", "|---|---|"]
    L += [f"| {k} | {v} |" for k, v in r["status"].items()]
    L += [f"| outcome (machine-readable) | {r['outcome']} |", "",
          "> This is an SVG to open in Illustrator (File > Open). No .ai file was produced and "
          "nothing was run in Illustrator. Editable type, fonts and gradients inside "
          "Illustrator are NOT VERIFIED until a person has opened the file.", ""]
    if r["svg_validation"]["errors"]:
        L += ["## Validation errors", ""] + [f"- {e}" for e in r["svg_validation"]["errors"]] + [""]
    if r["fonts"]:
        L += ["## Fonts", "", "| Key | Font name | Texts | Preview used the real font |",
              "|---|---|---|---|"]
        L += [f"| {f['key']} | {f['family']} | {f['used_by_texts']} | "
              f"{'yes' if f['preview_used_real_font'] else 'no (stand-in)'} |" for f in r["fonts"]]
        L.append("")
    if r["icons"]:
        L += ["## Icons", "", "| Icon | Box | Colors | Points | Flatten match |", "|---|---|---|---|---|"]
        L += [f"| {i['name']} | {i['box']} | {', '.join(i['palette'])} | {i['points']} | "
              f"{i['flatten_match']} |" for i in r["icons"]]
        L += ["", "Flatten match compares the solid-color version of each crop with the "
              "original crop, not the final vector with the original.", ""]
    if r["known_gaps"]:
        L += ["## Not restored / to replace by hand", ""] + [f"- {g}" for g in r["known_gaps"]] + [""]
    L += ["## Notes", ""] + [f"- {n}" for n in r["notes"]] + [""]
    c = r["preview_comparison"]
    L += ["## Preview comparison (informational)", "",
          f"- Pixel match ratio: {c['pixel_match_ratio']}; mean ΔE76: {c['mean_delta_e']}",
          "- `compare.png`: original on the left, preview on the right.", ""]
    return "\n".join(L)
