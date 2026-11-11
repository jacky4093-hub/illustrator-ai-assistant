"""Tests for compose mode (native text + shapes + separately traced icons).

Everything runs locally on a self-made image generated into a temporary
folder. Illustrator is never contacted: whether the SVG opens there with
editable type is NOT covered by these tests.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "examples" / "compose"))

import make_compose_example  # noqa: E402
from vectorfidelity.compose import SpecError, compose  # noqa: E402
from vectorfidelity.outputs import OutputConflictError  # noqa: E402
from vectorfidelity.render import chromium_available, render_chromium  # noqa: E402

VF = [sys.executable, str(ROOT / "tools" / "vf.py")]
SPEC = json.loads((ROOT / "examples" / "compose" / "price_card.spec.json").read_text(encoding="utf-8"))
NS = "{http://www.w3.org/2000/svg}"
CHROMIUM_OK, CHROMIUM_WHY = chromium_available()


class ComposeCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-compose-")).resolve()
        self.image = make_compose_example.make(self.tmp / "in" / "price_card.png")
        self.image_hash = hashlib.sha256(self.image.read_bytes()).hexdigest()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def spec(self, **changes) -> Path:
        s = copy.deepcopy(SPEC)
        s.update(changes)
        p = self.tmp / "in" / "spec.json"
        p.write_text(json.dumps(s, ensure_ascii=False), encoding="utf-8")
        return p

    def root(self, out: Path):
        return ET.parse(out / "composed.svg").getroot()


class TestComposeOutput(ComposeCase):
    def test_example_builds_text_shapes_icons_and_placeholder(self):
        out = self.tmp / "out"
        r = compose(self.spec(), out)
        self.assertEqual(r["status"]["svg_valid"], "PASS", r["svg_validation"])
        self.assertEqual(r["counts"], {"texts": 5, "shapes": 3, "icons": 2, "placeholders": 1,
                                       "gradients": 0})
        root = self.root(out)
        self.assertEqual((root.get("width"), root.get("height"), root.get("viewBox")),
                         ("480", "300", "0 0 480 300"))
        texts = root.findall(f".//{NS}text")
        self.assertEqual([t.text for t in texts],
                         ["WASH & DRY", "Standard 25 kg", "$250",
                          "Close the door before starting.", "Thank you!"])
        self.assertEqual(texts[0].get("font-family"), "Arial-Black")
        self.assertEqual(texts[4].get("text-anchor"), "middle")
        tags = {el.tag.replace(NS, "") for el in root.iter()}
        self.assertTrue({"text", "rect", "line", "path", "g"} <= tags)
        self.assertFalse(tags & {"image", "foreignObject", "script", "style"})
        raw = (out / "composed.svg").read_text(encoding="utf-8")
        self.assertNotIn("data:", raw)
        self.assertNotIn("href", raw)
        for f in ("preview.png", "compare.png", "compose_report.json", "compose_report.md",
                  "icons/basket.svg", "icons/drop.svg"):
            self.assertTrue((out / f).is_file(), f)
        self.assertEqual(hashlib.sha256(self.image.read_bytes()).hexdigest(), self.image_hash)

    def test_never_claims_an_ai_file_or_full_success(self):
        out = self.tmp / "out"
        r = compose(self.spec(), out)
        st = r["status"]
        self.assertTrue(st["ai_file"].startswith("NOT VERIFIED"))
        self.assertEqual(st["opens_in_illustrator"], "NOT VERIFIED")
        self.assertTrue(st["text_content"].startswith("NOT VERIFIED"))
        self.assertTrue(st["layout_fidelity"].startswith("NOT VERIFIED"))
        self.assertEqual(st["human_review"], "PENDING")
        self.assertEqual(st["overall"], "NEEDS_ATTENTION_UNRESTORED")
        self.assertEqual(r["outcome"], "COMPOSED_WITH_PLACEHOLDERS")
        self.assertTrue(any("logo" in g for g in r["known_gaps"]))
        self.assertIsNone(r["outputs"]["ai_file"])
        self.assertFalse(list(out.rglob("*.ai")))
        self.assertFalse(list(out.rglob("*.jsx")))  # nothing to run in Illustrator
        md = (out / "compose_report.md").read_text(encoding="utf-8")
        self.assertIn("NOT VERIFIED", md)
        self.assertIn("Not restored", md)

    def test_without_placeholders_awaits_review(self):
        r = compose(self.spec(placeholders=[]), self.tmp / "out")
        self.assertEqual(r["outcome"], "COMPOSED_NEEDS_REVIEW")
        self.assertEqual(r["status"]["overall"], "AWAITING_HUMAN_REVIEW")

    def test_output_is_deterministic(self):
        a, b = self.tmp / "a", self.tmp / "b"
        compose(self.spec(), a)
        compose(self.spec(), b)
        self.assertEqual((a / "composed.svg").read_bytes(), (b / "composed.svg").read_bytes())
        self.assertNotIn(b"\r", (a / "composed.svg").read_bytes())

    def test_text_is_escaped_and_keeps_cjk(self):
        fonts = {"f": {"family": "NotoSansTC-Bold"}}
        texts = [{"x": 10, "y": 40, "text": '洗脫烘 <b> & "25kg"', "size": 20, "font": "f",
                  "fill": "#0d3b73", "stroke": "#ffffff", "stroke_width": 3}]
        out = self.tmp / "out"
        r = compose(self.spec(fonts=fonts, texts=texts), out)
        self.assertEqual(r["status"]["svg_valid"], "PASS", r["svg_validation"])
        t = self.root(out).findall(f".//{NS}text")
        self.assertEqual(len(t), 1)
        self.assertEqual(t[0].text, '洗脫烘 <b> & "25kg"')
        self.assertEqual(t[0].get("font-family"), "NotoSansTC-Bold")
        self.assertEqual(t[0].get("stroke"), "#ffffff")

    def test_gradients_and_curves(self):
        grads = {"swoosh": {"x1": 0, "y1": 0, "x2": 100, "y2": 0,
                            "stops": [[0, "#d6eefb"], [1, "#5db9ec"]]}}
        shapes = [{"type": "path", "group": "decor", "start": [20, 280], "fill": "gradient:swoosh",
                   "segments": [["C", [100, 260], [200, 300], [300, 270]], ["L", [300, 290]],
                                ["C", [200, 310], [100, 280], [20, 295]]]},
                  {"type": "ellipse", "cx": 400, "cy": 200, "rx": 20, "ry": 10, "rotate": -30,
                   "fill": "#ffffff", "stroke": "#0d3b73"},
                  {"type": "polygon", "points": [[10, 10], [40, 10], [25, 30]], "fill": "#f79628"},
                  {"type": "circle", "cx": 60, "cy": 60, "r": 12, "fill": "#249ed3"}]
        out = self.tmp / "out"
        r = compose(self.spec(gradients=grads, shapes=shapes, placeholders=[]), out)
        self.assertEqual(r["status"]["svg_valid"], "PASS", r["svg_validation"])
        root = self.root(out)
        grad = root.find(f".//{NS}linearGradient")
        self.assertEqual(grad.get("id"), "swoosh")
        self.assertEqual(len(grad.findall(f"{NS}stop")), 2)
        curve = [p for p in root.iter(f"{NS}path") if p.get("fill") == "url(#swoosh)"]
        self.assertEqual(len(curve), 1)
        self.assertIn(" C", curve[0].get("d"))
        self.assertIsNotNone(root.find(f".//{NS}ellipse").get("transform"))


class TestIcons(ComposeCase):
    def paths(self, out, name):
        g = [g for g in self.root(out).iter(f"{NS}g") if g.get("id") == f"icon-{name}"][0]
        return g.findall(f"{NS}path")

    def test_background_comes_from_crop_border_not_from_area(self):
        # In the basket crop the navy ink covers more pixels than the pale
        # card background; the background must still be the border color.
        out = self.tmp / "out"
        r = compose(self.spec(), out)
        basket = [i for i in r["icons"] if i["name"] == "basket"][0]
        self.assertEqual(basket["background"], "#e6f3fb")
        p = self.paths(out, "basket")
        self.assertEqual([x.get("fill") for x in p], ["#0d3b73"])
        self.assertGreater(basket["flatten_match"], 0.9)

    def test_icon_geometry_stays_inside_its_box_and_keeps_holes(self):
        out = self.tmp / "out"
        compose(self.spec(), out)
        d = self.paths(out, "drop")[0].get("d")
        self.assertEqual(d.count("M"), 2)  # outline plus the white highlight hole
        nums = [float(v) for v in d.replace("M", " ").replace("L", " ").replace("Z", " ").split()]
        xs, ys = nums[0::2], nums[1::2]
        self.assertTrue(302 <= min(xs) and max(xs) <= 358 and 96 <= min(ys) and max(ys) <= 180)
        self.assertEqual(self.paths(out, "drop")[0].get("fill-rule"), "evenodd")

    def test_explicit_palette_and_keep_background(self):
        icons = [{"name": "drop", "box": [302, 96, 358, 180], "palette": ["#ffffff", "#1188cc"],
                  "keep_background": True}]
        out = self.tmp / "out"
        compose(self.spec(icons=icons), out)
        self.assertEqual([p.get("fill") for p in self.paths(out, "drop")], ["#ffffff", "#1188cc"])


class TestSpecAndOutputSafety(ComposeCase):
    def assert_bad(self, **changes):
        out = self.tmp / "bad"
        with self.assertRaises(SpecError):
            compose(self.spec(**changes), out)
        self.assertFalse(out.exists())  # refused before anything was written

    def test_bad_specs_are_refused_before_writing(self):
        t = SPEC["texts"][0]
        self.assert_bad(version=2)
        self.assert_bad(source="missing.png")
        self.assert_bad(texts=[dict(t, font="nope")])
        self.assert_bad(texts=[dict(t, fill="red")])
        self.assert_bad(texts=[dict(t, text="line1\nline2")])
        self.assert_bad(texts=[dict(t, text="   ")])
        self.assert_bad(icons=[{"name": "x", "box": [400, 250, 600, 400]}])
        self.assert_bad(icons=[{"name": "a b", "box": [0, 0, 10, 10]}])
        self.assert_bad(shapes=[{"type": "blob", "fill": "#000000"}])
        self.assert_bad(shapes=[{"type": "rect", "x": 0, "y": 0, "w": 5, "h": 5}])  # no paint
        self.assert_bad(shapes=[], texts=[], icons=[])

    def test_markup_cannot_be_injected_through_names(self):
        t = SPEC["texts"][0]
        self.assert_bad(fonts={"title": {"family": 'Arial"><script>alert(1)</script>'}})
        self.assert_bad(shapes=[dict(SPEC["shapes"][0], group='x"><image href="a.png"/>')])
        self.assert_bad(shapes=[dict(SPEC["shapes"][0], fill="url(http://example.com/x)")])
        self.assert_bad(shapes=[dict(SPEC["shapes"][1], dash='4"/><script/>')])
        self.assert_bad(placeholders=[{"name": 'p"/><image/>', "box": [0, 0, 10, 10]}])
        self.assert_bad(gradients={"g": {"x1": 0, "y1": 0, "x2": 1, "y2": 1,
                                         "stops": [[0, "#000000"], [1, 'x" onload="y']]}})
        self.assert_bad(texts=[dict(t, anchor='middle" onclick="x')])

    def test_non_empty_output_folder_is_refused_and_untouched(self):
        out = self.tmp / "out"
        out.mkdir()
        (out / "notes.txt").write_text("keep", encoding="utf-8")
        with self.assertRaises(OutputConflictError):
            compose(self.spec(), out)
        self.assertEqual(os.listdir(out), ["notes.txt"])
        compose(self.spec(), self.tmp / "fresh")
        with self.assertRaises(OutputConflictError):  # never re-run over an earlier result
            compose(self.spec(), self.tmp / "fresh")

    def test_cli_exit_codes(self):
        def cli(spec, out):
            r = subprocess.run(VF + ["compose", str(spec), "--out", str(out)],
                               capture_output=True, text=True)
            return r.returncode, json.loads(r.stdout)
        code, data = cli(self.spec(), self.tmp / "c1")
        self.assertEqual((code, data["outcome"]), (3, "COMPOSED_WITH_PLACEHOLDERS"))
        self.assertFalse(data["ai_file_verified"])
        code, data = cli(self.spec(placeholders=[]), self.tmp / "c2")
        self.assertEqual((code, data["outcome"]), (0, "COMPOSED_NEEDS_REVIEW"))
        code, data = cli(self.spec(version=9), self.tmp / "c3")
        self.assertEqual((code, data["outcome"]), (4, "REFUSED_BAD_SPEC"))
        code, data = cli(self.spec(), self.tmp / "c1")
        self.assertEqual((code, data["outcome"]), (4, "REFUSED_OUTPUT_CONFLICT"))


@unittest.skipUnless(CHROMIUM_OK, f"Chromium renderer unavailable: {CHROMIUM_WHY}")
class TestComposedSvgRenders(ComposeCase):
    def test_browser_renders_shapes_icons_and_text(self):
        out = self.tmp / "out"
        compose(self.spec(), out)
        img = render_chromium(out / "composed.svg", 480, 300)
        self.assertTrue(abs(img[20, 400].astype(int) - (13, 59, 115)).max() < 12)   # header bar
        self.assertTrue(abs(img[170, 213].astype(int) - (13, 59, 115)).max() < 12)  # basket icon
        self.assertTrue(abs(img[215, 300].astype(int) - 255).max() < 6)             # empty area
        # Text was drawn (font may be a fallback): light glyph pixels inside
        # the header bar, and dark glyph pixels on the "Close the door" line.
        self.assertGreater((img[25:60, 28:240].min(-1) > 200).sum(), 100)
        self.assertGreater((img[224:244, 20:230].max(-1) < 140).sum(), 60)


if __name__ == "__main__":
    unittest.main()
