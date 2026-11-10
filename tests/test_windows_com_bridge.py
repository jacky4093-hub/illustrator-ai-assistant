"""Static and SIMULATED tests for the Windows Illustrator COM bridge.

Nothing here contacts Adobe Illustrator or runs PowerShell. The launcher is
replaced by a small Python stand-in, so these tests cover the Python side
(integrity checks, conflict handling, error / timeout handling, no retry)
and static properties of the .ps1 file only. They are NOT a Windows COM
verification.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "examples"))

import make_test_images  # noqa: E402
from vectorfidelity.illustrator import (WindowsComBridge, build_jsx, get_bridge,  # noqa: E402
                                        get_execution_bridge)
from vectorfidelity.illustrator.bridge import LAUNCHER_PATH  # noqa: E402
from vectorfidelity.illustrator.jsx import (DATA_LINE_PATTERNS, jsx_sha256,  # noqa: E402
                                            skeleton_sha256, verify_jsx_text)
from vectorfidelity.outputs import OutputConflictError  # noqa: E402
from vectorfidelity.pipeline import BridgeUnavailableError, run  # noqa: E402
from vectorfidelity.trace import VectorDocument, VectorRegion  # noqa: E402

IMAGES = {p.stem: p for p in make_test_images.all_images()}
VF = [sys.executable, str(ROOT / "tools" / "vf.py")]
TARGETS = ("illustrator_output.ai", "illustrator_output.svg", "illustrator_output.png")

FAKE_LAUNCHER = r'''
import json, os, sys, time
a = sys.argv[1:]
arg = {a[i]: a[i + 1] for i in range(0, len(a), 2)}
jsx, result = arg["-JsxPath"], arg["-ResultPath"]
d = os.path.dirname(jsx)
with open(os.environ["FAKE_COUNTER"], "a") as f:
    f.write("%d\n" % os.getpid())
mode = os.environ.get("FAKE_MODE", "ok")
if mode == "refuse":
    print(json.dumps({"status": "REFUSED", "reason": "OPEN_DOCUMENTS", "detail": "1 open"}))
    sys.exit(2)
if mode == "crash":
    sys.exit(1)
rec = {"status": "STARTED", "launcher_pid": os.getpid(), "jsx_sha256": arg["-ExpectedSha256"]}
with open(result, "x") as f:
    json.dump(rec, f)
if mode == "hang":
    time.sleep(60)
    sys.exit(0)
if mode == "error":
    with open(os.path.join(d, "illustrator_output.svg"), "wb") as f:
        f.write(b"partial")
    rec.update(status="SCRIPT_ERROR", error="Error 1302: No such element")
    json.dump(rec, open(result, "w"))
    sys.exit(3)
names = ["illustrator_output.svg", "illustrator_output.png"]
if mode != "missing":
    names.append("illustrator_output.ai")
for n in names:
    with open(os.path.join(d, n), "wb") as f:
        f.write(b"simulated")
rec.update(status="SCRIPT_RETURNED")
json.dump(rec, open(result, "w"))
'''


def _doc():
    return VectorDocument(10, 10, (255, 255, 255), [
        VectorRegion((255, 0, 0), [[(1.0, 1.0), (5.0, 1.5), (5.0, 5.0)]], 8)])


class SimulatedLauncherCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vf-com-")).resolve()
        self.fake = self.tmp / "fake_launcher.py"
        self.fake.write_text(FAKE_LAUNCHER, encoding="utf-8")
        self.counter = self.tmp / "launches.txt"
        self.out = self.tmp / "out"
        self.out.mkdir()
        self._env = {k: os.environ.get(k) for k in ("FAKE_MODE", "FAKE_COUNTER")}
        os.environ["FAKE_COUNTER"] = str(self.counter)

    def tearDown(self):
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        shutil.rmtree(self.tmp, ignore_errors=True)

    def bridge(self, mode="ok", timeout=20):
        os.environ["FAKE_MODE"] = mode
        return WindowsComBridge(timeout=timeout, platform="win32",
                                launcher_cmd=[sys.executable, str(self.fake)])

    def jsx(self, out=None):
        out = out or self.out
        return build_jsx(_doc(), *(str(out / n) for n in TARGETS))

    def launches(self):
        return self.counter.read_text().split() if self.counter.exists() else []


class TestIntegrity(SimulatedLauncherCase):
    def test_generated_script_verifies(self):
        self.assertEqual(verify_jsx_text(self.jsx(), str(self.out)), [])
        # CRLF (Windows text mode) must not change the verdict or the hash.
        crlf = self.jsx().replace("\n", "\r\n")
        self.assertEqual(verify_jsx_text(crlf, str(self.out)), [])
        self.assertEqual(jsx_sha256(crlf), jsx_sha256(self.jsx()))

    def test_arbitrary_or_tampered_scripts_are_rejected(self):
        good = self.jsx()
        cases = {
            "arbitrary": "alert('hi');\n" * 20,
            "extra code": good.replace("doc.saveAs(", "app.quit(); doc.saveAs("),
            "appended": good + "\napp.documents[0].close();\n",
            "code in data": good.replace("var BG = [255, 255, 255];",
                                         "var BG = [255, 255, 255]; app.quit();"),
            "code in regions": good.replace('"loops":[[', '"loops":[[(function(){app.quit()})(),'),
            "string breakout": good.replace('illustrator_output.ai";',
                                            'illustrator_output.ai"; app.quit(); "";'),
        }
        for name, text in cases.items():
            with self.subTest(name=name):
                self.assertTrue(verify_jsx_text(text, str(self.out)))

    def test_output_paths_must_stay_in_script_folder(self):
        other = self.tmp / "elsewhere"
        other.mkdir()
        self.assertTrue(verify_jsx_text(self.jsx(other), str(self.out)))
        swapped = build_jsx(_doc(), str(self.out / "user_artwork.ai"),
                            str(self.out / TARGETS[1]), str(self.out / TARGETS[2]))
        self.assertTrue(verify_jsx_text(swapped, str(self.out)))

    def test_bridge_refuses_unverified_script_without_launching(self):
        st = self.bridge().build(self.jsx() + "\napp.quit();\n", self.out / "illustrator_build.jsx")
        self.assertEqual(st.run["state"], "REFUSED_PRECHECK")
        self.assertEqual(self.launches(), [])
        self.assertEqual(os.listdir(self.out), [])  # nothing written
        st = self.bridge().build(self.jsx(), self.out / "other_name.jsx")
        self.assertEqual(st.run["state"], "REFUSED_PRECHECK")
        self.assertEqual(self.launches(), [])


class TestSimulatedRuns(SimulatedLauncherCase):
    def test_success_is_still_not_verified(self):
        st = self.bridge("ok").build(self.jsx(), self.out / "illustrator_build.jsx")
        self.assertEqual(st.run["state"], "RETURNED_FILES_PRESENT")
        self.assertEqual(st.verification, "NOT VERIFIED")  # files existing is not proof
        self.assertEqual((st.run["attempts"], st.run["retried"]), (1, False))
        self.assertEqual(len(self.launches()), 1)

    def test_existing_outputs_block_the_run(self):
        for name in TARGETS:
            with self.subTest(name=name):
                out = self.tmp / ("o_" + name.replace(".", "_"))
                out.mkdir()
                jsx = self.jsx(out)
                (out / name).write_bytes(b"user file")
                st = self.bridge("ok").build(jsx, out / "illustrator_build.jsx")
                self.assertEqual(st.run["state"], "REFUSED_PRECHECK")
                self.assertEqual((out / name).read_bytes(), b"user file")
                self.assertEqual(self.launches(), [])

    def test_script_error_keeps_evidence_and_is_not_retried(self):
        st = self.bridge("error").build(self.jsx(), self.out / "illustrator_build.jsx")
        self.assertEqual(st.run["state"], "SCRIPT_ERROR")
        self.assertEqual(st.verification, "FAIL")
        self.assertIn("1302", st.detail)
        self.assertEqual(len(self.launches()), 1)
        # Evidence stays: script, run record and the partial output.
        self.assertEqual(sorted(os.listdir(self.out)),
                         ["illustrator_build.jsx", "illustrator_output.svg", "illustrator_run.json"])
        # A second attempt in the same folder is refused without launching.
        st2 = self.bridge("ok").build(self.jsx_text_for_retry(), self.out / "illustrator_build.jsx")
        self.assertEqual(st2.run["state"], "REFUSED_PRECHECK")
        self.assertEqual(len(self.launches()), 1)
        self.assertEqual((self.out / "illustrator_output.svg").read_bytes(), b"partial")

    def jsx_text_for_retry(self):
        # build_jsx itself refuses existing targets; bypass it to prove the
        # bridge's own guard.
        return build_jsx(_doc(), *(str(self.out / n) for n in TARGETS), check_existing=False)

    def test_missing_output_is_fail(self):
        st = self.bridge("missing").build(self.jsx(), self.out / "illustrator_build.jsx")
        self.assertEqual(st.run["state"], "RETURNED_FILES_MISSING")
        self.assertEqual(st.verification, "FAIL")

    def test_launcher_refusal_and_crash(self):
        st = self.bridge("refuse").build(self.jsx(), self.out / "illustrator_build.jsx")
        self.assertEqual(st.run["state"], "REFUSED_BY_LAUNCHER")
        self.assertEqual(st.run["reason"], "OPEN_DOCUMENTS")
        self.assertFalse((self.out / "illustrator_run.json").exists())
        out2 = self.tmp / "out2"
        out2.mkdir()
        st = self.bridge("crash").build(self.jsx(out2), out2 / "illustrator_build.jsx")
        self.assertEqual(st.run["state"], "UNKNOWN_LAUNCHER_FAILURE")
        self.assertEqual(len(self.launches()), 2)  # one launch per folder, none repeated

    @unittest.skipIf(os.name == "nt", "process liveness check uses POSIX signals")
    def test_timeout_reports_unknown_and_kills_nothing(self):
        t0 = time.time()
        st = self.bridge("hang", timeout=1.5).build(self.jsx(), self.out / "illustrator_build.jsx")
        self.assertLess(time.time() - t0, 15)
        self.assertEqual(st.run["state"], "UNKNOWN_TIMEOUT")
        self.assertEqual(st.verification, "NOT VERIFIED")
        self.assertIn("UNKNOWN", st.detail)
        self.assertEqual((st.run["attempts"], st.run["retried"]), (1, False))
        pid = st.run["launcher_pid"]
        try:
            os.kill(pid, 0)  # raises if the launcher had been terminated
            self.assertEqual(len(self.launches()), 1)  # not retried
            self.assertEqual(st.run["record"]["status"], "STARTED")  # evidence kept
            self.assertTrue((self.out / "illustrator_run.json").exists())
        finally:
            os.kill(pid, signal.SIGKILL)  # clean up the stand-in process only
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                pass


class TestPipelineAndCli(SimulatedLauncherCase):
    def test_default_never_contacts_illustrator(self):
        r = run(IMAGES["flat_shapes"], self.tmp / "p0", renderer="internal")
        self.assertIsNone(r["illustrator"]["run"])
        self.assertEqual(r["status"]["ai_file"], "NOT VERIFIED")
        self.assertEqual(self.launches(), [])
        self.assertFalse(get_bridge().probe().executes_illustrator)

    def test_run_requires_named_approval_and_windows(self):
        out = self.tmp / "p1"
        with self.assertRaises(BridgeUnavailableError):
            run(IMAGES["flat_shapes"], out, renderer="internal", bridge=self.bridge("ok"))
        with self.assertRaises(BridgeUnavailableError):
            run(IMAGES["flat_shapes"], out, renderer="internal",
                bridge=get_execution_bridge("windows-com", platform="linux"),
                illustrator_approved_by="tester")
        self.assertFalse(out.exists())  # refused before anything was written
        self.assertEqual(self.launches(), [])

    def test_pipeline_status_per_run_state(self):
        expect = {
            "ok": ("RETURNED_FILES_PRESENT", "NOT VERIFIED"),
            "error": ("SCRIPT_ERROR", "FAIL"),
            "missing": ("RETURNED_FILES_MISSING", "FAIL"),
            "refuse": ("REFUSED_BY_LAUNCHER", "NOT VERIFIED"),
        }
        for mode, (state, ai_prefix) in expect.items():
            with self.subTest(mode=mode):
                out = self.tmp / ("p_" + mode)
                r = run(IMAGES["flat_shapes"], out, renderer="internal",
                        bridge=self.bridge(mode), illustrator_approved_by="tester")
                self.assertEqual(r["illustrator"]["run"]["state"], state)
                self.assertTrue(r["status"]["ai_file"].startswith(ai_prefix), r["status"]["ai_file"])
                self.assertNotIn("PASS", r["status"]["ai_file"])
                self.assertEqual(r["illustrator"]["approved_by"], "tester")
                md = (out / "report.md").read_text(encoding="utf-8")
                self.assertIn(state, md)
                self.assertIn("never marked PASS automatically", md)

    def test_force_cannot_repeat_a_run(self):
        out = self.tmp / "p2"
        run(IMAGES["flat_shapes"], out, renderer="internal", bridge=self.bridge("error"),
            illustrator_approved_by="tester")
        before = (out / "illustrator_run.json").read_bytes()
        with self.assertRaises(OutputConflictError):
            run(IMAGES["flat_shapes"], out, renderer="internal", force=True,
                bridge=self.bridge("ok"), illustrator_approved_by="tester")
        self.assertEqual((out / "illustrator_run.json").read_bytes(), before)
        self.assertEqual(len(self.launches()), 1)

    def test_cli_flag_is_off_by_default_and_guarded(self):
        def cli(*args):
            r = subprocess.run(VF + [str(a) for a in args], capture_output=True, text=True)
            return r.returncode, json.loads(r.stdout)
        code, data = cli("run", IMAGES["flat_shapes"], "--out", self.tmp / "c0",
                         "--renderer", "internal")
        self.assertEqual((code, data["illustrator_run"]), (0, "NOT_REQUESTED"))
        self.assertFalse(data["ai_file_verified"])
        code, data = cli("run", IMAGES["flat_shapes"], "--out", self.tmp / "c1",
                         "--renderer", "internal", "--run-illustrator")
        self.assertEqual((code, data["outcome"]), (4, "REFUSED_ILLUSTRATOR_RUN"))
        self.assertFalse((self.tmp / "c1").exists())
        if sys.platform != "win32":
            code, data = cli("run", IMAGES["flat_shapes"], "--out", self.tmp / "c2",
                             "--renderer", "internal", "--run-illustrator", "--approved-by", "t")
            self.assertEqual((code, data["outcome"]), (4, "REFUSED_ILLUSTRATOR_RUN"))
            self.assertFalse((self.tmp / "c2").exists())


class TestLauncherScriptStatic(unittest.TestCase):
    """Text-level checks of the .ps1 (it cannot be executed on this platform)."""

    @classmethod
    def setUpClass(cls):
        cls.raw = LAUNCHER_PATH.read_bytes()
        cls.text = cls.raw.decode("ascii")  # ASCII only: safe for Windows PowerShell 5.1
        cls.code = "\n".join(l for l in cls.text.splitlines() if not l.lstrip().startswith("#"))

    def test_skeleton_hash_matches_template(self):
        m = re.search(r"\$SkeletonSha256 = '([0-9a-f]{64})'", self.text)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), skeleton_sha256())

    def test_data_line_patterns_match_python(self):
        for pat in DATA_LINE_PATTERNS:
            self.assertIn("'" + pat + "'", self.text)

    def test_no_destructive_or_dynamic_operations(self):
        forbidden = ["Stop-Process", "taskkill", ".Kill(", ".Quit(", ".Close(", "Remove-Item",
                     "Invoke-Expression", "iex ", "DoJavaScript(", "Start-Process",
                     "Invoke-WebRequest", "Net.WebClient", "Set-ExecutionPolicy", "Start-Sleep",
                     "while", "do {", "[System.IO.File]::Delete", "Move-Item", "-Force"]
        for word in forbidden:
            with self.subTest(word=word):
                self.assertNotIn(word.lower(), self.code.lower())

    def test_single_com_call_after_all_checks(self):
        self.assertEqual(self.code.count("DoJavaScriptFile("), 1)
        call = self.code.index("DoJavaScriptFile(")
        for marker in ("'HASH_MISMATCH'", "'NOT_A_GENERATED_SCRIPT'", "'BAD_OUTPUT_PATH'",
                       "'OUTPUT_EXISTS'", "'RUN_RECORD_EXISTS'", "'OPEN_DOCUMENTS'",
                       "[System.IO.FileMode]::CreateNew"):
            with self.subTest(marker=marker):
                self.assertIn(marker, self.code)
                self.assertLess(self.code.index(marker), call)
        self.assertEqual(self.code.count("New-Object -ComObject"), 1)

    def test_launcher_is_inside_plugin(self):
        self.assertEqual(LAUNCHER_PATH, ROOT / "tools" / "illustrator" / "run_illustrator_jsx.ps1")


if __name__ == "__main__":
    unittest.main()
