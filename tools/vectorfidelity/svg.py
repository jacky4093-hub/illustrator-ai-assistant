"""SVG writing and validation.

The validator enforces the core product rule: the output must be real
vector geometry, never an embedded raster image disguised as a vector.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from .trace import VectorDocument

SVG_NS = "http://www.w3.org/2000/svg"
XLINK_NS = "http://www.w3.org/1999/xlink"


def _fmt(v: float) -> str:
    s = ("%.2f" % v).rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def loops_to_path_data(loops) -> str:
    parts = []
    for loop in loops:
        x0, y0 = loop[0]
        seg = ["M%s %s" % (_fmt(x0), _fmt(y0))]
        seg += ["L%s %s" % (_fmt(x), _fmt(y)) for x, y in loop[1:]]
        seg.append("Z")
        parts.append(" ".join(seg))
    return " ".join(parts)


def write_svg(doc: VectorDocument, title: str = "vector-fidelity reconstruction") -> str:
    w, h = doc.width, doc.height
    bg = "#%02x%02x%02x" % doc.background
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        f'<svg xmlns="{SVG_NS}" width="{w}" height="{h}" viewBox="0 0 {w} {h}" '
        'shape-rendering="geometricPrecision">',
        f"  <title>{title}</title>",
        f'  <rect id="background" x="0" y="0" width="{w}" height="{h}" fill="{bg}"/>',
        '  <g id="regions">',
    ]
    for i, r in enumerate(doc.regions):
        d = loops_to_path_data(r.loops)
        lines.append(
            f'    <path id="region-{i:03d}" fill="{r.hex}" fill-rule="evenodd" d="{d}"/>'
        )
    lines += ["  </g>", "</svg>", ""]
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

_FORBIDDEN_TAGS = {"image", "foreignObject", "feImage", "script", "iframe",
                   "video", "canvas", "object", "embed"}
_PATH_TOKEN = re.compile(r"[MmLlHhVvZzCcSsQqTtAa]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_PATH_ALLOWED = re.compile(r"^[MmLlHhVvZzCcSsQqTtAa0-9eE.,+\-\s]*$")


@dataclass
class SvgValidation:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    path_count: int = 0
    width: float | None = None
    height: float | None = None
    viewbox: tuple[float, float, float, float] | None = None

    def to_dict(self):
        return {
            "ok": self.ok, "errors": self.errors, "warnings": self.warnings,
            "path_count": self.path_count, "width": self.width,
            "height": self.height, "viewbox": self.viewbox,
        }


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def validate_svg(text: str, expect_width: int | None = None,
                 expect_height: int | None = None) -> SvgValidation:
    res = SvgValidation(ok=True)
    try:
        root = ET.fromstring(text.encode("utf-8") if isinstance(text, str) else text)
    except ET.ParseError as e:
        return SvgValidation(ok=False, errors=[f"XML parse error: {e}"])
    if _local(root.tag) != "svg":
        res.errors.append(f"root element is <{_local(root.tag)}>, expected <svg>")
    try:
        res.width = float(root.get("width")) if root.get("width") else None
        res.height = float(root.get("height")) if root.get("height") else None
    except ValueError:
        res.errors.append("width/height are not plain numbers")
    vb = root.get("viewBox")
    if vb:
        try:
            nums = tuple(float(v) for v in re.split(r"[\s,]+", vb.strip()))
            if len(nums) != 4:
                raise ValueError
            res.viewbox = nums
        except ValueError:
            res.errors.append(f"invalid viewBox: {vb!r}")
    else:
        res.errors.append("missing viewBox")
    if expect_width is not None and expect_height is not None:
        if res.viewbox and (res.viewbox[2], res.viewbox[3]) != (expect_width, expect_height):
            res.errors.append(
                f"viewBox size {res.viewbox[2:]} != source {expect_width}x{expect_height}")
        if res.width is not None and res.height is not None:
            if abs(res.width / res.height - expect_width / expect_height) > 1e-6:
                res.errors.append("aspect ratio differs from source image")

    for el in root.iter():
        tag = _local(el.tag)
        if tag in _FORBIDDEN_TAGS:
            res.errors.append(f"forbidden element <{tag}> (raster/script embedding)")
        for k, v in el.attrib.items():
            lk = _local(k)
            if lk == "href" or k.endswith("}href"):
                res.errors.append(f"external/embedded reference on <{tag}>: {v[:40]!r}")
            if "data:" in v and lk not in ("d",):
                res.errors.append(f"data: URI in attribute {lk} of <{tag}>")
            if lk.startswith("on"):
                res.errors.append(f"event handler attribute {lk} on <{tag}>")
        if tag == "style" and el.text and ("url(" in el.text or "data:" in el.text):
            res.errors.append("<style> references url()/data:")
        if tag == "path":
            d = el.get("d", "")
            if not d.strip():
                res.errors.append(f"path {el.get('id')} has empty d")
            elif not _PATH_ALLOWED.match(d):
                res.errors.append(f"path {el.get('id')} has invalid characters in d")
            else:
                toks = _PATH_TOKEN.findall(d)
                if not toks or toks[0] not in "Mm":
                    res.errors.append(f"path {el.get('id')} does not start with moveto")
            res.path_count += 1
    if res.path_count == 0:
        res.errors.append("no <path> elements: nothing was vectorized")
    res.ok = not res.errors
    return res
