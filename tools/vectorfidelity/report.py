"""Quality report writer (JSON + Markdown)."""

from __future__ import annotations

import json
from pathlib import Path


def _md(result: dict) -> str:
    s = result.get("status", {})
    L = ["# Vector Fidelity Quality Report", ""]
    src = result.get("source", {})
    L += [f"- Source: `{src.get('path')}` ({src.get('width')}x{src.get('height')}, {src.get('mode')})",
          f"- Generated: {result.get('generated_at')} by vector-fidelity {result.get('version')}",
          f"- Image class: **{result.get('strategy', {}).get('image_class')}**",
          f"- Strategy: {result.get('strategy', {}).get('method')}", ""]
    L += ["## Status", "", "| Item | Result |", "|---|---|"]
    for k in ("svg_generated", "svg_valid", "svg_render", "fidelity", "ai_file",
              "illustrator_script", "gradients", "text", "human_review", "overall"):
        L.append(f"| {k} | {s.get(k, 'NOT VERIFIED')} |")
    L.append(f"| outcome (machine-readable) | {result.get('outcome', 'n/a')} |")
    if result.get("known_gaps"):
        L += ["", "> **Not a full success:** known unrestored aspects: "
              + ", ".join(result["known_gaps"])
              + ". Metric PASS does not mean these were restored; human approval does not remove them."]
    L += ["", "> `ai_file` is NOT VERIFIED: phase 1 never runs Adobe Illustrator. "
          "`illustrator_build.jsx` is generated for manual execution only.", ""]

    fin = result.get("final")
    if fin:
        c = fin["comparison"]
        th = result.get("thresholds", {})
        L += ["## Fidelity metrics (best iteration "
              f"{result.get('best_iteration')}, renderer: {fin['renderer']})", "",
              "| Metric | Value | Threshold | Check |", "|---|---|---|---|",
              f"| Pixel match ratio (≤24/255 per channel) | {c['pixel_match_ratio']} | ≥ {th.get('min_pixel_match')} | {'PASS' if c['checks'].get('pixel_match_ratio') else 'FAIL'} |",
              f"| Mean ΔE76 | {c['mean_delta_e']} | ≤ {th.get('max_mean_delta_e')} | {'PASS' if c['checks'].get('mean_delta_e') else 'FAIL'} |",
              f"| Min color-region IoU | {c['min_color_iou']} | ≥ {th.get('min_color_iou')} | {'PASS' if c['checks'].get('min_color_iou') else 'FAIL'} |",
              f"| P95 ΔE76 | {c['p95_delta_e']} | (info) | - |",
              f"| MAE / RMSE / PSNR | {c['mae']} / {c['rmse']} / {c['psnr_db']} dB | (info) | - |",
              "", f"- Geometry: {fin['paths']} paths, {fin['loops']} loops, {fin['points']} points",
              f"- Palette ({len(fin['palette'])}): {', '.join(fin['palette'])}"]
        if fin.get("renderer_cross_check"):
            cc = fin["renderer_cross_check"]
            L.append(f"- Renderer cross-check (internal vs chromium): mean abs diff "
                     f"{cc['mean_abs_diff']}, pixels > 32 diff: {cc['pixels_over_32']}")
        L += ["", "### Worst regions (8x8 grid, mean ΔE)", ""]
        for w in c["worst_cells"]:
            L.append(f"- x={w['x']} y={w['y']} {w['w']}x{w['h']}: ΔE {w['mean_delta_e']}")
        L.append("")

    L += ["## Iterations", "", "| # | epsilon | colors | paths | points | renderer | result |",
          "|---|---|---|---|---|---|---|"]
    for it in result.get("iterations", []):
        L.append(f"| {it['iteration']} | {it['epsilon']} | {it['palette_size']} | {it['paths']} | "
                 f"{it['points']} | {it.get('renderer', '-')} | {it['result']} |")
    L.append("")
    if result.get("failures"):
        L += ["## Failure log", ""]
        for f in result["failures"]:
            L.append(f"- iteration {f['iteration']} / {f['stage']}: "
                     + json.dumps({k: v for k, v in f.items() if k not in ('iteration', 'stage')},
                                  ensure_ascii=False))
        L.append("")
    if result.get("unrestored"):
        L += ["## Not restored / known gaps", ""] + [f"- {u}" for u in result["unrestored"]] + [""]
    hr = result.get("human_review")
    if hr:
        L += ["## Human review", "", f"- Status: **{hr['status']}**"]
        L += [f"- Reason: {r}" for r in hr["reasons"]]
        if hr.get("reviewer"):
            L.append(f"- Reviewer: {hr['reviewer']} at {hr['decided_at']}: {hr.get('note') or ''}")
        L += ["", "Checklist for the reviewer:", "",
              "1. Open `vector.svg` and the original side by side.",
              "2. Check outline, proportions, colors; inspect `diff_heatmap.png` (red = larger error).",
              "3. Confirm the items under *Not restored* are acceptable.",
              "4. Record the decision: `python3 tools/vf.py review <out_dir> --approve|--reject "
              "--reviewer NAME --note TEXT`.", ""]
    return "\n".join(L)


def write_reports(result: dict, out_dir: Path) -> None:
    out_dir = Path(out_dir)
    (out_dir / "report.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (out_dir / "report.md").write_text(_md(result), encoding="utf-8")
