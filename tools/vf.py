#!/usr/bin/env python3
"""Command line entry point for the vector-fidelity toolkit.

  python3 tools/vf.py analyze <image>
  python3 tools/vf.py run <image> --out <dir> [--max-iterations 3] [--renderer chromium|internal]
  python3 tools/vf.py validate <file.svg> [--width W --height H]
  python3 tools/vf.py review <out_dir> (--approve|--reject) --reviewer NAME [--note TEXT]

All processing is local. No image is uploaded anywhere.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from vectorfidelity.analyze import choose_strategy, classify, compute_stats, stats_to_dict  # noqa: E402
from vectorfidelity.imageio import load_image  # noqa: E402
from vectorfidelity.pipeline import record_review, run  # noqa: E402
from vectorfidelity.svg import validate_svg  # noqa: E402


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
        res = run(args.image, args.out, args.max_iterations, args.renderer)
        print(json.dumps({"status": res["status"], "out": str(Path(args.out).resolve()),
                          "report": str(Path(args.out, "report.md"))}, indent=2))
        return 0 if res["status"].get("svg_valid") == "PASS" else 1
    if args.cmd == "validate":
        res = validate_svg(Path(args.svg).read_text(encoding="utf-8"), args.width, args.height)
        print(json.dumps(res.to_dict(), indent=2))
        return 0 if res.ok else 1
    if args.cmd == "review":
        res = record_review(args.out_dir, args.approve, args.reviewer, args.note)
        print(json.dumps(res["status"], indent=2))
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
