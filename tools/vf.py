#!/usr/bin/env python3
"""Command line entry point for the vector-fidelity toolkit.

  python3 tools/vf.py analyze <image>
  python3 tools/vf.py run <image> --out <dir> [--max-iterations 3] [--renderer chromium|internal] [--force]
  python3 tools/vf.py validate <file.svg> [--width W --height H]
  python3 tools/vf.py review <out_dir> (--approve|--reject) --reviewer NAME [--note TEXT]

All processing is local. No image is uploaded anywhere.

`run` prints JSON with a machine-readable "outcome" and exits with:
  0  METRICS_PASS_NO_KNOWN_GAPS  (still needs human review; .ai NOT VERIFIED)
  3  INCOMPLETE_UNRESTORED or INCOMPLETE_FIDELITY_FAIL (valid SVG, not a full success)
  1  FAILED (no valid vector output)
  4  refused: output conflict (nothing was written)
`--force` only lets the tool update its own named output files in a
non-empty directory. The source image and existing Illustrator exports are
never overwritten, and unknown files are never touched.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vectorfidelity.analyze import choose_strategy, classify, compute_stats, stats_to_dict  # noqa: E402
from vectorfidelity.imageio import load_image  # noqa: E402
from vectorfidelity.outputs import OutputConflictError  # noqa: E402
from vectorfidelity.pipeline import (OUTCOME_FAILED, OUTCOME_METRICS_PASS,  # noqa: E402
                                     record_review, run)
from vectorfidelity.svg import validate_svg  # noqa: E402

EXIT_OK, EXIT_FAILED, EXIT_INCOMPLETE, EXIT_REFUSED = 0, 1, 3, 4


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="vf")
    sub = p.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("analyze")
    a.add_argument("image")
    r = sub.add_parser("run")
    r.add_argument("image")
    r.add_argument("--out", required=True)
    r.add_argument("--max-iterations", type=int, default=3)
    r.add_argument("--renderer", choices=["chromium", "internal"], default="chromium")
    r.add_argument("--force", action="store_true",
                   help="allow updating this tool's own output files in a non-empty directory")
    v = sub.add_parser("validate")
    v.add_argument("svg")
    v.add_argument("--width", type=int)
    v.add_argument("--height", type=int)
    rv = sub.add_parser("review")
    rv.add_argument("out_dir")
    g = rv.add_mutually_exclusive_group(required=True)
    g.add_argument("--approve", action="store_true")
    g.add_argument("--reject", action="store_true")
    rv.add_argument("--reviewer", required=True)
    rv.add_argument("--note", default="")
    args = p.parse_args(argv)

    if args.cmd == "analyze":
        img = load_image(args.image)
        st = compute_stats(img)
        cls = classify(st)
        print(json.dumps({"analysis": stats_to_dict(st), "class": cls,
                          "strategy": choose_strategy(st, cls).to_dict()}, indent=2))
        return 0
    if args.cmd == "run":
        try:
            res = run(args.image, args.out, args.max_iterations, args.renderer, force=args.force)
        except OutputConflictError as e:
            print(json.dumps({"outcome": "REFUSED_OUTPUT_CONFLICT", "error": str(e),
                              "conflicts": e.conflicts}, indent=2, ensure_ascii=False))
            return EXIT_REFUSED
        outcome = res["outcome"]
        print(json.dumps({"outcome": outcome,
                          "full_metric_success": outcome == OUTCOME_METRICS_PASS,
                          "known_gaps": res.get("known_gaps", []),
                          "status": res["status"], "out": str(Path(args.out).resolve()),
                          "report": str(Path(args.out, "report.md"))}, indent=2,
                         ensure_ascii=False))
        if outcome == OUTCOME_METRICS_PASS:
            return EXIT_OK
        return EXIT_FAILED if outcome == OUTCOME_FAILED else EXIT_INCOMPLETE
    if args.cmd == "validate":
        res = validate_svg(Path(args.svg).read_text(encoding="utf-8"), args.width, args.height)
        print(json.dumps(res.to_dict(), indent=2))
        return 0 if res.ok else 1
    if args.cmd == "review":
        res = record_review(args.out_dir, args.approve, args.reviewer, args.note)
        print(json.dumps({"overall": res["status"]["overall"], "outcome": res.get("outcome"),
                          "known_gaps": res.get("known_gaps", []), "status": res["status"]},
                         indent=2, ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
