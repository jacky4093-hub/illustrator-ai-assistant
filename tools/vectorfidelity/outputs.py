"""Output-directory safety checks.

Rules (phase-1 acceptance fix):

* The source image is never written, with or without ``force``. Paths are
  compared after resolving relative paths, ``..``, symbolic links and case
  (on case-insensitive systems), and with ``os.path.samefile`` so hard links
  are caught too.
* By default the output directory must not exist or must be empty.
* ``force`` only allows the pipeline's own, explicitly named output files
  to be updated. Unknown files are never touched or deleted.
* Illustrator export targets (``illustrator_output.*``) are never
  overwritten, not even with ``force``.
* A target that is a symbolic link is refused (writing would follow it).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MAX_ITERATION_FILES = 5  # equals pipeline.MAX_ITERATIONS_CAP

MANAGED_FILES = (
    ["vector.svg", "preview.png", "diff_heatmap.png", "report.json", "report.md",
     "illustrator_build.jsx"]
    + [f"iter{i}.svg" for i in range(1, MAX_ITERATION_FILES + 1)]
)
ILLUSTRATOR_TARGETS = ("illustrator_output.ai", "illustrator_output.svg",
                       "illustrator_output.png")
# Record of an automated Illustrator run attempt. Its presence blocks any
# further run in the same folder (no automatic retry), even with force.
ILLUSTRATOR_RUN_RECORD = "illustrator_run.json"
NEVER_OVERWRITTEN = ILLUSTRATOR_TARGETS + (ILLUSTRATOR_RUN_RECORD,)


class OutputConflictError(RuntimeError):
    def __init__(self, message: str, conflicts: list[str]):
        super().__init__(message)
        self.conflicts = conflicts


@dataclass
class OutputPlan:
    out_dir: Path
    source: Path
    force: bool
    existing_unknown: list[str] = field(default_factory=list)
    will_update: list[str] = field(default_factory=list)

    def path(self, name: str) -> Path:
        if name not in MANAGED_FILES:
            raise ValueError(f"{name!r} is not a managed output file")
        return self.out_dir / name


def _key(p: Path) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(p)))


def _same_file(a: Path, b: Path) -> bool:
    if _key(a) == _key(b):
        return True
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


def plan_outputs(source: str | Path, out_dir: str | Path, force: bool = False) -> OutputPlan:
    """Check the output directory before anything is written.

    Raises OutputConflictError (nothing created or modified) on any conflict.
    """
    src = Path(source)
    out = Path(out_dir)
    if not src.is_file():
        raise OutputConflictError(f"source image not found: {src}", [str(src)])
    if out.exists() and not out.is_dir():
        raise OutputConflictError(f"output path exists and is not a directory: {out}", [str(out)])
    if _same_file(out, src):
        raise OutputConflictError("output directory is the source image", [str(out)])

    conflicts: list[str] = []
    # Source protection: applies regardless of force and of whether the
    # directory exists yet (it may be reached through a symlinked parent).
    for name in MANAGED_FILES + list(NEVER_OVERWRITTEN):
        target = out / name
        if _same_file(target, src):
            conflicts.append(f"{name}: resolves to the source image")
    if conflicts:
        raise OutputConflictError(
            "refusing to run: an output file would overwrite the source image "
            "(never allowed, --force does not apply)", conflicts)

    plan = OutputPlan(out_dir=out, source=src, force=force)
    if not out.exists():
        return plan

    entries = sorted(os.listdir(out))
    if not entries:
        return plan
    if not force:
        raise OutputConflictError(
            "refusing to run: output directory is not empty "
            "(use a new/empty directory, or --force to update only this tool's own files)",
            entries)

    for name in entries:
        target = out / name
        if name in NEVER_OVERWRITTEN:
            conflicts.append(f"{name}: Illustrator output / run record is never overwritten")
        elif name in MANAGED_FILES:
            if target.is_symlink():
                conflicts.append(f"{name}: is a symbolic link; refusing to write through it")
            elif not target.is_file():
                conflicts.append(f"{name}: exists but is not a regular file")
            else:
                plan.will_update.append(name)
        else:
            plan.existing_unknown.append(name)
    if conflicts:
        raise OutputConflictError("refusing to run even with --force", conflicts)
    return plan


def check_illustrator_targets(paths: list[str | Path]) -> None:
    """Refuse to prepare an Illustrator script whose export targets exist."""
    existing = [str(p) for p in paths if os.path.lexists(p)]
    if existing:
        raise OutputConflictError(
            "Illustrator export target(s) already exist; refusing to generate a script "
            "that could overwrite them", existing)
