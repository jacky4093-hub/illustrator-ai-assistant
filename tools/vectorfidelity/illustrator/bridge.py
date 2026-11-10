"""Cross-platform Illustrator connection interface.

Planned backends (see docs/illustrator-mcp-evaluation.md):

* ``script-export`` (implemented): writes a .jsx file for a human to run
  in Illustrator. Never executes anything; result is NOT VERIFIED.
* ``windows-com`` (implemented, opt-in): starts the PowerShell launcher
  ``tools/illustrator/run_illustrator_jsx.ps1``, which calls
  ``Illustrator.Application.DoJavaScriptFile``. Only obtainable through
  ``get_execution_bridge`` (never through ``get_bridge``). Automated tests
  only simulate the launcher; the COM path needs real Windows + Illustrator.
* ``macos-applescript`` (design only): ``osascript`` + ``do javascript``.
* ``mcp`` (design only): a reviewed Illustrator MCP server, after human
  approval of its source, dependencies and licence.

No backend ever reports the .ai file as verified: a run that returns with
all three files present is still "NOT VERIFIED" until a human has opened
the .ai file and confirmed it contains editable vectors.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path

from ..outputs import ILLUSTRATOR_RUN_RECORD
from .jsx import JSX_FILENAME, TARGET_NAMES, jsx_sha256, verify_jsx_text


@dataclass
class BridgeStatus:
    backend: str
    available: bool
    executes_illustrator: bool
    verification: str  # "PASS" | "FAIL" | "NOT VERIFIED"
    detail: str
    run: dict | None = None  # set only by executing backends

    def to_dict(self):
        return asdict(self)


class IllustratorBridge(ABC):
    name = "abstract"

    @abstractmethod
    def probe(self) -> BridgeStatus:
        """Report whether this backend can reach Illustrator (no side effects)."""

    @abstractmethod
    def build(self, jsx_text: str, jsx_path: Path) -> BridgeStatus:
        """Deliver the build script to Illustrator (or to the user)."""


class ScriptExportBridge(IllustratorBridge):
    name = "script-export"

    def probe(self) -> BridgeStatus:
        return BridgeStatus(self.name, True, False, "NOT VERIFIED",
                            "Writes an ExtendScript file only; Illustrator is not contacted.")

    def build(self, jsx_text: str, jsx_path: Path) -> BridgeStatus:
        jsx_path = Path(jsx_path)
        jsx_path.write_text(jsx_text, encoding="utf-8")
        return BridgeStatus(
            self.name, True, False, "NOT VERIFIED",
            f"Script written to {jsx_path.name}. It must be run manually in "
            "Adobe Illustrator on Windows; .ai/.svg/.png exports from Illustrator "
            "are not verified.",
        )


# Run states reported by WindowsComBridge (BridgeStatus.run["state"]).
RUN_REFUSED_PRECHECK = "REFUSED_PRECHECK"            # stopped before the launcher was started
RUN_REFUSED_BY_LAUNCHER = "REFUSED_BY_LAUNCHER"      # launcher refused; Illustrator did not run the script
RUN_FILES_PRESENT = "RETURNED_FILES_PRESENT"         # script returned, 3 files exist (still not verified)
RUN_FILES_MISSING = "RETURNED_FILES_MISSING"         # script returned, some output missing
RUN_SCRIPT_ERROR = "SCRIPT_ERROR"                    # Illustrator reported an error
RUN_UNKNOWN_TIMEOUT = "UNKNOWN_TIMEOUT"              # no answer in time; Illustrator state unknown
RUN_UNKNOWN_LAUNCHER = "UNKNOWN_LAUNCHER_FAILURE"    # launcher ended without a usable result

LAUNCHER_PATH = Path(__file__).resolve().parents[2] / "illustrator" / "run_illustrator_jsx.ps1"


def _powershell_exe() -> str:
    # Absolute path to Windows PowerShell; PATH is not searched.
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return os.path.join(root, "System32", "WindowsPowerShell", "v1.0", "powershell.exe")


class WindowsComBridge(IllustratorBridge):
    """Runs the generated script in Illustrator through the COM launcher.

    One attempt only. Nothing is retried, killed, closed or cleaned up:
    on error or timeout all files stay in place as evidence.
    """

    name = "windows-com"

    def __init__(self, timeout: float = 180, launcher_cmd: list[str] | None = None,
                 platform: str | None = None):
        self.timeout = float(timeout)
        self.platform = platform or sys.platform
        # launcher_cmd exists so tests can substitute a simulated launcher.
        self._launcher_cmd = launcher_cmd

    def _command_prefix(self) -> list[str]:
        if self._launcher_cmd is not None:
            return list(self._launcher_cmd)
        # -ExecutionPolicy Bypass applies to this one process only; no
        # machine or user policy is changed.
        return [_powershell_exe(), "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                "Bypass", "-File", str(LAUNCHER_PATH)]

    def probe(self) -> BridgeStatus:
        if self.platform != "win32":
            return BridgeStatus(self.name, False, True, "NOT VERIFIED",
                                f"Windows only (this platform is {self.platform}).")
        prefix = self._command_prefix()
        if not os.path.isfile(prefix[0]):
            return BridgeStatus(self.name, False, True, "NOT VERIFIED",
                                f"launcher executable not found: {prefix[0]}")
        if self._launcher_cmd is None and not LAUNCHER_PATH.is_file():
            return BridgeStatus(self.name, False, True, "NOT VERIFIED",
                                f"missing {LAUNCHER_PATH}")
        return BridgeStatus(self.name, True, True, "NOT VERIFIED",
                            "Launcher present. Whether Illustrator answers over COM is "
                            "only known when a run is attempted.")

    def _status(self, state: str, verification: str, detail: str, **extra) -> BridgeStatus:
        run = {"state": state, "attempts": extra.pop("attempts", 0),
               "retried": False, "timeout_seconds": self.timeout}
        run.update(extra)
        return BridgeStatus(self.name, True, True, verification, detail, run=run)

    def build(self, jsx_text: str, jsx_path: Path) -> BridgeStatus:
        jsx_path = Path(jsx_path)
        out_dir = jsx_path.resolve().parent
        record_path = out_dir / ILLUSTRATOR_RUN_RECORD

        # Pre-checks: nothing has been sent to Illustrator yet.
        problems = []
        if self.platform != "win32":
            problems.append(f"windows-com requires Windows (platform is {self.platform})")
        if jsx_path.name != JSX_FILENAME:
            problems.append(f"script must be named {JSX_FILENAME}")
        problems += verify_jsx_text(jsx_text, str(out_dir))
        if os.path.lexists(record_path):
            problems.append(f"{ILLUSTRATOR_RUN_RECORD} exists: a run was already attempted "
                            "in this folder and is never repeated automatically")
        problems += [f"{n} already exists; nothing is overwritten"
                     for n in TARGET_NAMES if os.path.lexists(out_dir / n)]
        if problems:
            return self._status(RUN_REFUSED_PRECHECK, "NOT VERIFIED",
                                "Illustrator run refused before start: " + "; ".join(problems),
                                problems=problems)

        jsx_path.write_text(jsx_text, encoding="utf-8")
        cmd = self._command_prefix() + ["-JsxPath", str(jsx_path.resolve()),
                                        "-ExpectedSha256", jsx_sha256(jsx_text),
                                        "-ResultPath", str(record_path)]
        # Output goes to a temp file, not a pipe, so a launcher that is still
        # waiting for Illustrator can never block this process.
        log = tempfile.TemporaryFile()
        try:
            proc = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=log,
                                    stderr=subprocess.STDOUT)
        except OSError as e:
            log.close()
            return self._status(RUN_UNKNOWN_LAUNCHER, "NOT VERIFIED",
                                f"launcher could not be started: {e}")
        try:
            code = proc.wait(timeout=self.timeout)
        except subprocess.TimeoutExpired:
            # Deliberately no kill, no retry, no cleanup.
            log.close()
            return self._status(
                RUN_UNKNOWN_TIMEOUT, "NOT VERIFIED",
                f"No result after {self.timeout:g} s. The state of Illustrator is UNKNOWN: it "
                "may still be working or waiting on a dialog. Nothing was stopped, closed or "
                "retried. Check Illustrator manually; files in the output folder are kept as "
                "evidence.", attempts=1, launcher_pid=proc.pid,
                record=_read_json(record_path), outputs=_outputs(out_dir))
        log.seek(0)
        stdout = log.read().decode("utf-8", "replace").strip()
        log.close()
        record = _read_json(record_path)
        outputs = _outputs(out_dir)
        common = dict(attempts=1, launcher_exit_code=code, record=record, outputs=outputs)

        if record is None:
            refusal = _last_json_line(stdout)
            if code == 2 and refusal and refusal.get("status") == "REFUSED":
                return self._status(
                    RUN_REFUSED_BY_LAUNCHER, "NOT VERIFIED",
                    f"Launcher refused ({refusal.get('reason')}): {refusal.get('detail')}. "
                    "Illustrator was not asked to run the script.",
                    reason=refusal.get("reason"), **common)
            return self._status(RUN_UNKNOWN_LAUNCHER, "NOT VERIFIED",
                                f"Launcher exited with code {code} without a run record; "
                                "state unknown. Not retried.", launcher_output=stdout[-2000:],
                                **common)
        state = record.get("status")
        if state == "SCRIPT_ERROR":
            return self._status(RUN_SCRIPT_ERROR, "FAIL",
                                f"Illustrator reported a script error: {record.get('error')}. "
                                "Not retried; files left in place.", **common)
        if state == "SCRIPT_RETURNED" and code == 0:
            if all(o["exists"] and o["bytes"] > 0 for o in outputs):
                return self._status(
                    RUN_FILES_PRESENT, "NOT VERIFIED",
                    "Illustrator returned and .ai/.svg/.png exist. This does NOT verify the "
                    ".ai file: a human must open it and confirm editable vector paths.",
                    **common)
            return self._status(RUN_FILES_MISSING, "FAIL",
                                "Illustrator returned but not all output files were created.",
                                **common)
        return self._status(RUN_UNKNOWN_LAUNCHER, "NOT VERIFIED",
                            f"Run record status {state!r} with launcher exit code {code}; "
                            "state unknown. Not retried.", **common)


def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return None


def _last_json_line(text: str):
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                return json.loads(line)
            except ValueError:
                return None
    return None


def _outputs(out_dir: Path) -> list[dict]:
    res = []
    for n in TARGET_NAMES:
        p = Path(out_dir) / n
        ok = p.is_file()
        res.append({"name": n, "exists": ok, "bytes": p.stat().st_size if ok else 0})
    return res


# get_bridge() only ever returns non-executing backends.
_BACKENDS = {"script-export": ScriptExportBridge}
_EXECUTION_BACKENDS = {"windows-com": WindowsComBridge}


def get_execution_bridge(name: str, **kwargs) -> IllustratorBridge:
    """Explicit opt-in factory for backends that run Illustrator."""
    if name not in _EXECUTION_BACKENDS:
        raise ValueError(f"unknown execution backend {name!r} "
                         f"(available: {', '.join(_EXECUTION_BACKENDS)})")
    return _EXECUTION_BACKENDS[name](**kwargs)


def get_bridge(name: str = "script-export") -> IllustratorBridge:
    if name not in _BACKENDS:
        raise ValueError(
            f"Illustrator backend {name!r} is not implemented in phase 1 "
            f"(available: {', '.join(_BACKENDS)})")
    return _BACKENDS[name]()
