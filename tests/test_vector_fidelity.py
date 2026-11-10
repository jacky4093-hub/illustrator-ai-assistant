"""Automated tests for the phase-1 vector-fidelity toolkit.

Run:  python3 -m unittest discover -s tests -v
Only stdlib unittest + numpy + Pillow are required. Tests needing the
Chromium renderer or Node.js are skipped (and reported as skipped) when
those are unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "examples"))

import make_test_images  # noqa: E402
from vectorfidelity.analyze import (assign_labels_spatial, build_palette,  # noqa: E402
                                    choose_strategy, classify, compute_stats)
from vectorfidelity.illustrator import get_bridge  # noqa: E402
from vectorfidelity.imageio import load_image  # noqa: E402
from vectorfidelity.pipeline import record_review, run  # noqa: E402
from vectorfidelity.render import chromium_available, render_chromium, render_internal  # noqa: E402
from vectorfidelity.svg import validate_svg, write_svg  # noqa: E402
from vectorfidelity.trace import boundary_loops, vectorize_labels  # noqa: E402

CHROMIUM_OK, CHROMIUM_WHY = chromium_available()
IMAGES = {p.stem: p for p in make_test_images.all_images()}


def _labels_for(name):
    img = load_image(IMAGES[name])
    st = compute_stats(img)
    strat = choose_strategy(st, classify(st))
    pal = build_palette(img.rgb, strat)
    return img, pal, assign_labels_spatial(img.rgb, pal)


class TestAnalysis(unittest.TestCase):
    def test_size_and_aspect_preserved(self):
        img = load_image(IMAGES["flat_shapes"])
        st = compute_stats(img)
        self.assertEqual((st.width, st.height), (240, 160))
        self.assertAlmostEqual(st.aspect_ratio, 1.5)

    def test_classification(self):
        expected = {"flat_shapes": "flat_graphic", "ring_with_hole": "flat_graphic",
                    "linear_gradient": "graphic_with_gradients",
                    "noise_texture": "photographic"}
        for name, cls in expected.items():
            with self.subTest(name=name):
                self.assertEqual(classify(compute_stats(load_image(IMAGES[name]))), cls)

    def test_dominant_palette_excludes_antialias_colors(self):
        _, pal, _ = _labels_for("flat_shapes")
        cols = {tuple(int(v) for v in c) for c in pal}
        self.assertEqual(cols, {(255, 255, 255), (220, 40, 40), (30, 90, 200), (40, 160, 70)})

    def test_photographic_requires_human_review(self):
        st = compute_stats(load_image(IMAGES["noise_texture"]))
        self.assertTrue(choose_strategy(st, classify(st)).human_review_reasons)


class TestTracing(unittest.TestCase):
    def test_unsimplified_trace_is_pixel_exact(self):
        img, pal, labels = _labels_for("ring_with_hole")
        doc = vectorize_labels(labels, pal, epsilon=0.0, min_loop_area=0, smooth=False)
        rendered = render_internal(write_svg(doc), img.width, img.height)
        self.assertTrue(np.array_equal(assign_labels_spatial(rendered, pal), labels))

    def test_hole_produces_inner_loop(self):
        mask = np.zeros((10, 10), bool)
        mask[1:9, 1:9] = True
        mask[4:6, 4:6] = False
        self.assertEqual(len(boundary_loops(mask)), 2)

    def test_diagonal_touching_pixels(self):
        mask = np.array([[1, 0], [0, 1]], bool)
        loops = boundary_loops(mask)
        self.assertEqual(sum(len(lp) for lp in loops), 8)
        self.assertEqual(len(loops), 2)


class TestSvgValidation(unittest.TestCase):
    def test_generated_svg_is_valid_vector(self):
        img, pal, labels = _labels_for("flat_shapes")
        svg = write_svg(vectorize_labels(labels, pal, 0.35))
        res = validate_svg(svg, img.width, img.height)
        self.assertTrue(res.ok, res.errors)
        self.assertGreaterEqual(res.path_count, 3)
        self.assertNotIn("<image", svg)
        self.assertNotIn("data:", svg)

    def test_rejects_embedded_raster(self):
        bad = ('<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
               'width="10" height="10" viewBox="0 0 10 10"><image width="10" height="10" '
               'xlink:href="data:image/png;base64,AAAA"/></svg>')
        res = validate_svg(bad, 10, 10)
        self.assertFalse(res.ok)
        self.assertTrue(any("image" in e for e in res.errors))

    def test_rejects_wrong_aspect_and_malformed(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="20" height="10" viewBox="0 0 20 10">'
               '<path d="M0 0 L5 5 Z"/></svg>')
        self.assertFalse(validate_svg(svg, 10, 10).ok)
        self.assertFalse(validate_svg("<svg><path", 10, 10).ok)

    def test_rejects_svg_without_paths(self):
        svg = ('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" viewBox="0 0 10 10">'
               '<rect width="10" height="10" fill="#fff"/></svg>')
        self.assertFalse(validate_svg(svg, 10, 10).ok)


@unittest.skipUnless(CHROMIUM_OK, f"Chromium renderer unavailable: {CHROMIUM_WHY}")
class TestChromiumRenderer(unittest.TestCase):
    def test_chromium_agrees_with_internal_rasterizer(self):
        img, pal, labels = _labels_for("ring_with_hole")
        svg = write_svg(vectorize_labels(labels, pal, 0.35))
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "t.svg"
            p.write_text(svg, encoding="utf-8")
            chrome = render_chromium(p, img.width, img.height)
        internal = render_internal(svg, img.width, img.height)
        d = np.abs(chrome.astype(int) - internal.astype(int))
        self.assertLess(d.mean(), 1.0)
        self.assertLess((d.max(-1) > 32).mean(), 0.005)


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="vf-test-")
        cls.renderer = "chromium" if CHROMIUM_OK else "internal"
        cls.results = {n: run(IMAGES[n], Path(cls.tmp) / n, renderer=cls.renderer)
                       for n in IMAGES}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_flat_images_pass_fidelity(self):
        for n in ("flat_shapes", "ring_with_hole"):
            with self.subTest(n=n):
                r = self.results[n]
                self.assertEqual(r["status"]["svg_valid"], "PASS")
                self.assertEqual(r["status"]["fidelity"], "PASS", r["final"]["comparison"])
                self.assertEqual(r["final"]["renderer"], self.renderer)
                for f in ("vector.svg", "preview.png", "diff_heatmap.png", "report.json",
                          "report.md", "illustrator_build.jsx"):
                    self.assertTrue((Path(self.tmp) / n / f).exists(), f)

    def test_ai_file_never_marked_verified(self):
        for n, r in self.results.items():
            with self.subTest(n=n):
                self.assertEqual(r["status"]["ai_file"], "NOT VERIFIED")
                self.assertEqual(r["status"]["illustrator_script"], "NOT VERIFIED")
                self.assertIsNone(r["outputs"].get("ai_file"))
                self.assertFalse(list((Path(self.tmp) / n).glob("*.ai")))

    def test_gradient_reported_as_not_restored(self):
        r = self.results["linear_gradient"]
        self.assertTrue(r["status"]["gradients"].startswith("NOT RESTORED"))
        self.assertTrue(any("Gradients" in u for u in r["unrestored"]))
        self.assertEqual(r["human_review"]["status"], "PENDING")

    def test_photographic_failure_is_logged(self):
        r = self.results["noise_texture"]
        self.assertNotEqual(r["status"].get("fidelity"), "PASS")
        self.assertTrue(r["failures"])

    def test_iteration_cap(self):
        for r in self.results.values():
            self.assertLessEqual(len(r["iterations"]), 3)

    def test_preview_png_matches_source_size(self):
        from PIL import Image
        with Image.open(Path(self.tmp) / "flat_shapes" / "preview.png") as im:
            self.assertEqual(im.size, (240, 160))

    def test_human_review_flow(self):
        d = Path(self.tmp) / "ring_with_hole"
        data = record_review(d, approve=True, reviewer="tester", note="looks right")
        self.assertEqual(data["status"]["human_review"], "APPROVED")
        self.assertEqual(data["status"]["ai_file"], "NOT VERIFIED")
        self.assertEqual(data["status"]["overall"], "SVG_APPROVED_AI_NOT_VERIFIED")
        saved = json.loads((d / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["human_review"]["reviewer"], "tester")


class TestIllustratorLayer(unittest.TestCase):
    def test_only_script_export_backend(self):
        st = get_bridge().probe()
        self.assertFalse(st.executes_illustrator)
        self.assertEqual(st.verification, "NOT VERIFIED")
        with self.assertRaises(ValueError):
            get_bridge("windows-com")

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_generated_jsx_is_syntactically_valid_javascript(self):
        from vectorfidelity.illustrator import build_jsx
        img, pal, labels = _labels_for("ring_with_hole")
        doc = vectorize_labels(labels, pal, 0.35)
        jsx = build_jsx(doc, "C:\\out\\a.ai", "C:\\out\\a.svg", "C:\\out\\a.png")
        self.assertIn('"C:/out/a.ai"', jsx)
        body = "\n".join(l for l in jsx.splitlines() if not l.startswith("#"))
        r = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0,'utf8'))"],
                           input=body, capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
