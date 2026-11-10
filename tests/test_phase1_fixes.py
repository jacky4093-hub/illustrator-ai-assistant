"""Regression tests for the phase-1 acceptance fixes.

1. Gradient (known unrestored aspects) must never read as full success,
   before or after human approval, in the report or the CLI.
2. Output safety: source image protection, non-empty directory refusal,
   --force limited to the tool's own files, Illustrator target protection.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "examples"))

import make_test_images  # noqa: E402
from vectorfidelity.illustrator import build_jsx  # noqa: E402
from vectorfidelity.outputs import MANAGED_FILES, OutputConflictError, plan_outputs  # noqa: E402
from vectorfidelity.pipeline import record_review, run  # noqa: E402
from vectorfidelity.trace import VectorDocument, VectorRegion  # noqa: E402

IMAGES = {p.stem: p for p in make_test_images.all_images()}
VF = [sys.executable, str(ROOT / "tools" / "vf.py")]
FULL_SUCCESS_OVERALL = {"AWAITING_HUMAN_REVIEW", "SVG_APPROVED_AI_NOT_VERIFIED"}


def _md5(p: Path) -> str:
    return hashlib.md5(Path(p).read_bytes()).hexdigest()


def _snapshot(d: Path) -> dict:
    return {p.name: _md5(p) for p in sorted(d.iterdir()) if p.is_file()}


def _cli(*args):
    r = subprocess.run(VF + [str(a) for a in args], capture_output=True, text=True)
    try:
        data = json.loads(r.stdout)
    except json.JSONDecodeError:
        data = None
    return r.returncode, data, r


class TestGradientNotFullSuccess(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-fix-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_gradient_status_before_and_after_approval(self):
        out = self.tmp / "grad"
        r = run(IMAGES["linear_gradient"], out, renderer="internal")
        st = r["status"]
        self.assertEqual(st["overall"], "NEEDS_ATTENTION_UNRESTORED")
        self.assertEqual(st["fidelity"], "PASS (metrics only)")
        self.assertEqual(r["outcome"], "INCOMPLETE_UNRESTORED")
        self.assertIn("gradients", r["known_gaps"])
        self.assertNotIn(st["overall"], FULL_SUCCESS_OVERALL)
        unrestored_before = list(r["unrestored"])

        data = record_review(out, approve=True, reviewer="tester", note="ok")
        self.assertEqual(data["status"]["overall"], "APPROVED_WITH_KNOWN_GAPS")
        self.assertNotIn(data["status"]["overall"], FULL_SUCCESS_OVERALL)
        # Approval must not erase any gap information.
        self.assertEqual(data["unrestored"], unrestored_before)
        self.assertIn("gradients", data["known_gaps"])
        self.assertEqual(data["outcome"], "INCOMPLETE_UNRESTORED")
        self.assertTrue(data["status"]["gradients"].startswith("NOT RESTORED"))
        self.assertEqual(data["status"]["fidelity"], "PASS (metrics only)")
        self.assertEqual(data["status"]["ai_file"], "NOT VERIFIED")
        md = (out / "report.md").read_text(encoding="utf-8")
        self.assertIn("Not a full success", md)
        self.assertIn("APPROVED_WITH_KNOWN_GAPS", md)

    def test_gradient_rejection(self):
        out = self.tmp / "grad"
        run(IMAGES["linear_gradient"], out, renderer="internal")
        data = record_review(out, approve=False, reviewer="tester", note="no")
        self.assertEqual(data["status"]["overall"], "REJECTED_BY_REVIEWER")
        self.assertIn("gradients", data["known_gaps"])

    def test_cli_outcome_and_exit_codes(self):
        code, data, raw = _cli("run", IMAGES["linear_gradient"], "--out", self.tmp / "g",
                               "--renderer", "internal")
        self.assertEqual(code, 3, raw.stderr)
        self.assertEqual(data["outcome"], "INCOMPLETE_UNRESTORED")
        self.assertFalse(data["full_metric_success"])
        code, data, raw = _cli("review", self.tmp / "g", "--approve", "--reviewer", "t")
        self.assertEqual(data["overall"], "APPROVED_WITH_KNOWN_GAPS")
        self.assertEqual(data["outcome"], "INCOMPLETE_UNRESTORED")

        code, data, raw = _cli("run", IMAGES["flat_shapes"], "--out", self.tmp / "f",
                               "--renderer", "internal")
        self.assertEqual(code, 0, raw.stderr)
        self.assertEqual(data["outcome"], "METRICS_PASS_NO_KNOWN_GAPS")
        self.assertTrue(data["full_metric_success"])
        self.assertEqual(data["status"]["ai_file"], "NOT VERIFIED")

        code, data, raw = _cli("run", IMAGES["noise_texture"], "--out", self.tmp / "n",
                               "--renderer", "internal")
        self.assertEqual(code, 1)
        self.assertEqual(data["outcome"], "FAILED")


class TestOutputSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-safe-"))

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _src_in(self, d: Path, name: str) -> Path:
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        shutil.copy(IMAGES["flat_shapes"], p)
        return p

    def test_source_named_like_output_is_never_overwritten(self):
        d = self.tmp / "work"
        src = self._src_in(d, "preview.png")
        before = _md5(src)
        for force in (False, True):
            with self.subTest(force=force):
                with self.assertRaises(OutputConflictError):
                    run(src, d, renderer="internal", force=force)
                self.assertEqual(_md5(src), before)
                self.assertEqual(sorted(os.listdir(d)), ["preview.png"])

    def test_source_via_relative_and_dotdot_paths(self):
        d = self.tmp / "work"
        src = self._src_in(d, "vector.svg")  # odd name on purpose
        before = _md5(src)
        cwd = os.getcwd()
        try:
            os.chdir(self.tmp)
            with self.assertRaises(OutputConflictError) as cm:
                plan_outputs("work/vector.svg", "work/../work/./", force=True)
            self.assertTrue(any("source image" in c for c in cm.exception.conflicts))
        finally:
            os.chdir(cwd)
        self.assertEqual(_md5(src), before)

    def test_symlinked_output_dir_and_file_cannot_reach_source(self):
        d = self.tmp / "work"
        src = self._src_in(d, "report.json")
        before = _md5(src)
        link_dir = self.tmp / "linkdir"
        try:
            os.symlink(d, link_dir, target_is_directory=True)
        except (OSError, NotImplementedError) as e:
            self.skipTest(f"symlinks unavailable: {e}")
        with self.assertRaises(OutputConflictError):
            run(src, link_dir, renderer="internal", force=True)
        # A managed file that is a symlink to the source.
        other = self.tmp / "other"
        other.mkdir()
        os.symlink(src, other / "preview.png")
        with self.assertRaises(OutputConflictError):
            run(src, other, renderer="internal", force=True)
        self.assertEqual(_md5(src), before)

    def test_hardlink_to_source_is_detected(self):
        src = self._src_in(self.tmp / "in", "image.png")
        out = self.tmp / "out"
        out.mkdir()
        try:
            os.link(src, out / "vector.svg")
        except OSError as e:
            self.skipTest(f"hard links unavailable: {e}")
        with self.assertRaises(OutputConflictError):
            run(src, out, renderer="internal", force=True)
        self.assertEqual(_md5(src), _md5(IMAGES["flat_shapes"]))

    def test_non_empty_directory_refused_without_force(self):
        out = self.tmp / "out"
        out.mkdir()
        (out / "notes.txt").write_text("keep me", encoding="utf-8")
        (out / "vector.svg").write_text("old", encoding="utf-8")
        snap = _snapshot(out)
        with self.assertRaises(OutputConflictError):
            run(IMAGES["flat_shapes"], out, renderer="internal")
        self.assertEqual(_snapshot(out), snap)  # nothing written or deleted
        code, data, _ = _cli("run", IMAGES["flat_shapes"], "--out", out, "--renderer", "internal")
        self.assertEqual(code, 4)
        self.assertEqual(data["outcome"], "REFUSED_OUTPUT_CONFLICT")
        self.assertEqual(_snapshot(out), snap)

    def test_empty_existing_directory_allowed(self):
        out = self.tmp / "empty"
        out.mkdir()
        r = run(IMAGES["flat_shapes"], out, renderer="internal")
        self.assertEqual(r["outcome"], "METRICS_PASS_NO_KNOWN_GAPS")

    def test_force_updates_only_managed_files_and_keeps_unknown(self):
        out = self.tmp / "out"
        out.mkdir()
        (out / "notes.txt").write_text("keep me", encoding="utf-8")
        (out / "iter5.svg").write_text("stale", encoding="utf-8")
        (out / "vector.svg").write_text("old", encoding="utf-8")
        r = run(IMAGES["flat_shapes"], out, renderer="internal", force=True)
        self.assertEqual((out / "notes.txt").read_text(encoding="utf-8"), "keep me")
        self.assertTrue((out / "iter5.svg").exists())  # never deleted
        self.assertNotEqual((out / "vector.svg").read_text(encoding="utf-8"), "old")
        self.assertIn("notes.txt", r["output_safety"]["untouched_unknown_files"])
        self.assertIn("vector.svg", r["output_safety"]["updated_existing"])
        written = {p.name for p in out.iterdir()} - {"notes.txt"}
        self.assertTrue(written <= set(MANAGED_FILES), written)

    def test_existing_illustrator_outputs_never_overwritten(self):
        out = self.tmp / "out"
        out.mkdir()
        ai = out / "illustrator_output.ai"
        ai.write_bytes(b"user artwork")
        for force in (False, True):
            with self.subTest(force=force):
                with self.assertRaises(OutputConflictError):
                    run(IMAGES["flat_shapes"], out, renderer="internal", force=force)
                self.assertEqual(ai.read_bytes(), b"user artwork")
                self.assertEqual(sorted(os.listdir(out)), ["illustrator_output.ai"])


class TestJsxSafety(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-jsx-"))
        self.doc = VectorDocument(10, 10, (255, 255, 255), [
            VectorRegion((255, 0, 0), [[(1.0, 1.0), (5.0, 1.0), (5.0, 5.0)]], 8)])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generator_refuses_existing_targets(self):
        ai, sv, pn = (str(self.tmp / n) for n in ("a.ai", "a.svg", "a.png"))
        Path(pn).write_bytes(b"existing")
        with self.assertRaises(OutputConflictError) as cm:
            build_jsx(self.doc, ai, sv, pn)
        self.assertEqual(cm.exception.conflicts, [pn])
        self.assertEqual(Path(pn).read_bytes(), b"existing")

    def test_runtime_guard_precedes_document_creation(self):
        jsx = build_jsx(self.doc, str(self.tmp / "a.ai"), str(self.tmp / "a.svg"),
                        str(self.tmp / "a.png"))
        guard = jsx.index(".exists")
        stop = jsx.index("return;")
        create = jsx.index("app.documents.add(")
        self.assertLess(guard, create)
        self.assertLess(stop, create)
        # Only the newly created document is touched.
        for forbidden in ("activeDocument", "app.documents[", "documents.getByName",
                          ".close(", "app.open("):
            self.assertNotIn(forbidden, jsx)
        self.assertEqual(jsx.count("saveAs("), 1)
        self.assertIn("doc.saveAs(", jsx)

    @unittest.skipUnless(shutil.which("node"), "node not available")
    def test_runtime_guard_logic_with_stubbed_extendscript(self):
        """Execute the generated script in Node with a minimal ExtendScript
        stub to check the guard stops before any document is created.
        (Simulation only; real Illustrator behaviour is NOT VERIFIED.)"""
        ai = str(self.tmp / "a.ai")
        jsx = build_jsx(self.doc, ai, str(self.tmp / "a.svg"), str(self.tmp / "a.png"))
        body = "\n".join(l for l in jsx.splitlines() if not l.startswith("#"))
        stub = (
            "var created=0, alerts=[];"
            "function File(p){this.exists=(p===%s);}"
            "function alert(m){alerts.push(m);}"
            "var app={documents:{add:function(){created++;throw new Error('created');}}};"
            % json.dumps(ai.replace("\\", "/"))
        )
        tail = ";console.log(JSON.stringify({created:created, alerts:alerts.length}));"
        r = subprocess.run(["node", "-e", stub + body + tail], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), {"created": 0, "alerts": 1})

    def test_export_order_svg_png_then_save_ai(self):
        """SVG export, then PNG export, then save .ai as the last document
        operation (order verified on Illustrator 2021 / Windows; this test only
        checks the generated script, not Illustrator itself)."""
        ai, sv, pn = (str(self.tmp / n) for n in ("a.ai", "a.svg", "a.png"))
        jsx = build_jsx(self.doc, ai, sv, pn)

        # Static checks on the script text.
        self.assertEqual(jsx.count("doc.exportFile("), 2)
        self.assertEqual(jsx.count("ExportType.SVG"), 1)
        self.assertEqual(jsx.count("ExportType.PNG24"), 1)
        self.assertEqual(jsx.count("saveAs("), 1)
        i_guard = jsx.index(".exists")
        i_create = jsx.index("app.documents.add(")
        i_svg = jsx.index("doc.exportFile(new File(OUT_SVG), ExportType.SVG")
        i_png = jsx.index("doc.exportFile(new File(OUT_PNG), ExportType.PNG24")
        i_ai = jsx.index("doc.saveAs(new File(OUT_AI)")
        self.assertLess(i_guard, i_create)
        self.assertLess(i_create, i_svg)
        self.assertLess(i_svg, i_png)
        self.assertLess(i_png, i_ai)

        if not shutil.which("node"):
            self.skipTest("node not available for the execution-order simulation")
        # Execution order with a minimal ExtendScript stub (simulation only;
        # real Illustrator behaviour was verified separately on Windows).
        stub = r"""
var log = [];
function File(p){ this.path = p; log.push("exists-check"); this.exists = false; }
function alert(m){ log.push("alert"); }
function RGBColor(){}
function IllustratorSaveOptions(){}
function ExportOptionsSVG(){}
function ExportOptionsPNG24(){}
var DocumentColorSpace = {RGB: "RGB"};
var ExportType = {SVG: "SVG", PNG24: "PNG24"};
function mkPath(){ return {setEntirePath: function(){}}; }
var doc = {
  artboards: [{}],
  layers: [{
    pathItems: {rectangle: function(){ return {}; }, add: mkPath},
    compoundPathItems: {add: function(){ return {pathItems: {add: mkPath}}; }}
  }],
  exportFile: function(f, t){ log.push("export:" + t + ":" + f.path); },
  saveAs: function(f){ log.push("saveAs:" + f.path); },
  close: function(){ log.push("close"); }
};
var app = {documents: {add: function(){ log.push("documents.add"); return doc; }}};
"""
        body = "\n".join(l for l in jsx.splitlines() if not l.startswith("#"))
        tail = "\nconsole.log(JSON.stringify(log));"
        r = subprocess.run(["node", "-e", stub + body + tail], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        log = json.loads(r.stdout)
        fwd = lambda p: p.replace("\\", "/")  # noqa: E731
        i_add = log.index("documents.add")
        # Conflict check (3 File.exists probes, no alert) happens before creation.
        self.assertEqual(log[:i_add], ["exists-check"] * 3)
        ops = [e for e in log if e.startswith(("export:", "saveAs:", "close"))]
        self.assertEqual(ops, [f"export:SVG:{fwd(sv)}", f"export:PNG24:{fwd(pn)}",
                               f"saveAs:{fwd(ai)}"])
        self.assertTrue(log[-1].startswith("saveAs:"), log[-1])


if __name__ == "__main__":
    unittest.main()
