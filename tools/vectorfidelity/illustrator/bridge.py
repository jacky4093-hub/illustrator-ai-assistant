"""Cross-platform Illustrator connection interface.

Planned backends (see docs/illustrator-mcp-evaluation.md):

* ``script-export`` (implemented): writes a .jsx file for a human to run
  in Illustrator. Never executes anything; result is NOT VERIFIED.
* ``windows-com`` (design only): Illustrator.Application COM object,
  ``DoJavaScriptFile``. Requires real Windows + Illustrator.
* ``macos-applescript`` (design only): ``osascript`` + ``do javascript``.
* ``mcp`` (design only): a reviewed Illustrator MCP server, after human
  approval of its source, dependencies and licence.

Only ``script-export`` exists in phase 1, so no code path can claim an
.ai file was written by Illustrator.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class BridgeStatus:
    backend: str
    available: bool
    executes_illustrator: bool
    verification: str  # "PASS" | "FAIL" | "NOT VERIFIED"
    detail: str

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


_BACKENDS = {"script-export": ScriptExportBridge}


def get_bridge(name: str = "script-export") -> IllustratorBridge:
    if name not in _BACKENDS:
        raise ValueError(
            f"Illustrator backend {name!r} is not implemented in phase 1 "
            f"(available: {', '.join(_BACKENDS)})")
    return _BACKENDS[name]()
