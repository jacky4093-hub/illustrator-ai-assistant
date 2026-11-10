"""Simulated tests for the generated script's own export failure detection.

Background: on Illustrator 2021 (Windows) one real run returned from
exportFile(PNG24) without writing the PNG and without raising an error
(cause NOT VERIFIED). The script now checks each output once after it is
written, still saves the .ai last, and then raises "VF_EXPORT_MISSING".

The script is executed against a minimal ExtendScript stub in an ES3 engine:
Node.js when available, otherwise Windows Script Host JScript (cscript). This
is a SIMULATION. It proves the script's control flow, not Illustrator's
behaviour, and Illustrator is never contacted here.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from vectorfidelity.illustrator.jsx import _TEMPLATE, build_jsx, verify_jsx_text  # noqa: E402
from vectorfidelity.trace import VectorDocument, VectorRegion  # noqa: E402

TARGETS = ("illustrator_output.ai", "illustrator_output.svg", "illustrator_output.png")

STUB = r"""
function __out(s) {
    if (typeof WScript !== "undefined") { WScript.StdOut.WriteLine(s); } else { console.log(s); }
}
var MODE = __MODE__;
var PRE = __PRE__;
var log = [], written = {}, alerts = 0;
function File(p) {
    this.path = p;
    this.exists = (written[p] === true) || (PRE[p] === true);
    log.push("exists:" + p + "=" + (this.exists ? "1" : "0"));
}
function alert(m) { alerts++; log.push("alert"); }
function RGBColor() {}
function IllustratorSaveOptions() {}
function ExportOptionsSVG() {}
function ExportOptionsPNG24() {}
var DocumentColorSpace = {RGB: "RGB"};
var ExportType = {SVG: "SVG", PNG24: "PNG24"};
function mkPath() { return {setEntirePath: function () {}}; }
var doc = {
    artboards: [{}],
    layers: [{
        pathItems: {rectangle: function () { return {}; }, add: mkPath},
        compoundPathItems: {add: function () { return {pathItems: {add: mkPath}}; }}
    }],
    exportFile: function (f, t) {
        log.push("export:" + t);
        if (MODE === "throw_png" && t === "PNG24") { throw new Error("Error 1302: simulated export failure"); }
        if (MODE === "svg_missing" && t === "SVG") { return; }
        if (MODE === "png_missing" && t === "PNG24") { return; }
        written[f.path] = true;
    },
    saveAs: function (f) {
        log.push("saveAs");
        if (MODE !== "ai_missing") { written[f.path] = true; }
    }
};
var app = {documents: {add: function () { log.push("documents.add"); return doc; }}};
var __result = "OK";
try {
__BODY__
} catch (e) {
    __result = "ERROR:" + (e.message || e.description);
}
__out("RESULT:" + __result);
__out("ALERTS:" + alerts);
__out("LOG:" + log.join("|"));
"""


def _engine():
    node = shutil.which("node")
    if node:
        return [node]
    if os.name == "nt":
        cscript = shutil.which("cscript")
        if cscript:
            return [cscript, "//Nologo", "//E:jscript"]
    return None


def _code_lines(text: str) -> list[str]:
    return [l for l in text.split("\n") if l.strip() and not l.strip().startswith("//")]


@unittest.skipIf(_engine() is None, "no ES3 engine (node or Windows cscript) available")
class TestExportFailureSimulation(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-exp-")).resolve()
        self.doc = VectorDocument(10, 10, (255, 255, 255), [
            VectorRegion((255, 0, 0), [[(1.0, 1.0), (5.0, 1.0), (5.0, 5.0)]], 8)])
        self.ai, self.svg, self.png = (str(self.tmp / n).replace("\\", "/") for n in TARGETS)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def simulate(self, mode: str, preexisting=()):
        jsx = build_jsx(self.doc, self.ai, self.svg, self.png, check_existing=False)
        body = "\n".join(l for l in jsx.splitlines() if not l.startswith("#"))
        pre = json.dumps({p: True for p in preexisting})
        src = (STUB.replace("__MODE__", json.dumps(mode)).replace("__PRE__", pre)
               .replace("__BODY__", body))
        js = self.tmp / "sim.js"
        js.write_text(src, encoding="ascii")
        r = subprocess.run(_engine() + [str(js)], capture_output=True, text=True,
                           errors="replace", timeout=60)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = dict(l.split(":", 1) for l in r.stdout.splitlines() if ":" in l)
        return out["RESULT"], int(out["ALERTS"]), out["LOG"].split("|")

    @staticmethod
    def ops(log):
        return [e for e in log if e.startswith(("export:", "saveAs", "documents.add"))]

    def test_normal_outputs(self):
        result, alerts, log = self.simulate("ok")
        self.assertEqual(result, "OK")
        self.assertEqual(alerts, 0)
        self.assertEqual(self.ops(log), ["documents.add", "export:SVG", "export:PNG24", "saveAs"])
        # Three conflict probes before the document. Afterwards every output
        # appears twice: the File object handed to export/save (=0, not yet
        # written) and the check right after writing it (=1).
        i_add = log.index("documents.add")
        self.assertEqual(log[:i_add], [f"exists:{p}=0" for p in (self.ai, self.svg, self.png)])
        self.assertEqual([e for e in log[i_add:] if e.startswith("exists:")],
                         [f"exists:{self.svg}=0", f"exists:{self.svg}=1",
                          f"exists:{self.png}=0", f"exists:{self.png}=1",
                          f"exists:{self.ai}=0", f"exists:{self.ai}=1"])

    def test_png_missing_is_an_explicit_error(self):
        result, _, log = self.simulate("png_missing")
        self.assertTrue(result.startswith("ERROR:VF_EXPORT_MISSING"), result)
        self.assertIn("PNG " + self.png, result)
        self.assertNotIn("SVG ", result)
        self.assertNotIn("AI ", result)
        # Order kept; the .ai is still saved last so the evidence is complete.
        self.assertEqual(self.ops(log), ["documents.add", "export:SVG", "export:PNG24", "saveAs"])

    def test_svg_missing_is_an_explicit_error(self):
        result, _, log = self.simulate("svg_missing")
        self.assertTrue(result.startswith("ERROR:VF_EXPORT_MISSING"), result)
        self.assertIn("SVG " + self.svg, result)
        self.assertNotIn("PNG ", result)
        self.assertEqual(self.ops(log), ["documents.add", "export:SVG", "export:PNG24", "saveAs"])

    def test_ai_missing_is_an_explicit_error(self):
        result, _, _ = self.simulate("ai_missing")
        self.assertTrue(result.startswith("ERROR:VF_EXPORT_MISSING"), result)
        self.assertIn("AI " + self.ai, result)

    def test_export_exception_propagates_unchanged(self):
        result, _, log = self.simulate("throw_png")
        self.assertEqual(result, "ERROR:Error 1302: simulated export failure")
        # Nothing after the failing call runs: no save, no further checks.
        self.assertEqual(self.ops(log), ["documents.add", "export:SVG", "export:PNG24"])

    def test_existing_output_still_stops_before_any_document(self):
        result, alerts, log = self.simulate("ok", preexisting=[self.png])
        self.assertEqual(result, "OK")
        self.assertEqual(alerts, 1)
        self.assertNotIn("documents.add", log)
        self.assertEqual(self.ops(log), [])


class TestExportCheckStatic(unittest.TestCase):
    def test_each_output_checked_once_after_writing_and_ai_saved_last(self):
        code = "\n".join(_code_lines(_TEMPLATE))
        svg = code.index("doc.exportFile(new File(OUT_SVG), ExportType.SVG, svgOpts);")
        png = code.index("doc.exportFile(new File(OUT_PNG), ExportType.PNG24, pngOpts);")
        ai = code.index("doc.saveAs(new File(OUT_AI), new IllustratorSaveOptions());")
        c_svg = code.index('checkWritten(OUT_SVG, "SVG");')
        c_png = code.index('checkWritten(OUT_PNG, "PNG");')
        c_ai = code.index('checkWritten(OUT_AI, "AI");')
        throw = code.index('throw new Error("VF_EXPORT_MISSING')
        self.assertTrue(svg < c_svg < png < c_png < ai < c_ai < throw)
        self.assertEqual(code.count("checkWritten(OUT_"), 3)
        self.assertEqual(code.count("throw "), 1)
        self.assertEqual(code.count("doc.exportFile("), 2)
        self.assertEqual(code.count("saveAs("), 1)

    def test_no_waiting_retry_or_cleanup_added(self):
        code = "\n".join(_code_lines(_TEMPLATE)).lower()
        for word in ("while", "sleep", "settimeout", "retry", ".remove(", ".close(",
                     "app.quit", "activedocument", "for (var attempt"):
            with self.subTest(word=word):
                self.assertNotIn(word, code)

    def test_removing_the_check_breaks_integrity(self):
        tmp = Path(tempfile.mkdtemp(prefix="vf-exp-")).resolve()
        try:
            doc = VectorDocument(10, 10, (255, 255, 255), [
                VectorRegion((255, 0, 0), [[(1.0, 1.0), (5.0, 1.0), (5.0, 5.0)]], 8)])
            jsx = build_jsx(doc, *(str(tmp / n) for n in TARGETS))
            self.assertEqual(verify_jsx_text(jsx, str(tmp)), [])
            for name, tampered in {
                "png check removed": jsx.replace('    checkWritten(OUT_PNG, "PNG");\n', ""),
                "throw removed": re.sub(r'\s*if \(missing\.length > 0\) \{.*?\n    \}', "",
                                        jsx, flags=re.S),
            }.items():
                with self.subTest(name=name):
                    self.assertNotEqual(tampered, jsx)
                    self.assertTrue(verify_jsx_text(tampered, str(tmp)))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
