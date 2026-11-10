"""End-to-end vector-fidelity pipeline with a bounded refinement loop."""

from __future__ import annotations

import json
import time
from pathlib import Path

import numpy as np

from . import __version__
from .analyze import (assign_labels, assign_labels_spatial, build_palette, choose_strategy, classify,
                      compute_stats, stats_to_dict)
from .compare import Thresholds, compare_images
from .illustrator import build_jsx, get_bridge
from .imageio import load_image, save_rgb
from .render import render, render_internal
from .report import write_reports
from .svg import validate_svg, write_svg
from .trace import vectorize_labels

MAX_ITERATIONS_CAP = 5


def _score(comp) -> float:
    return comp.pixel_match_ratio + comp.min_color_iou - comp.mean_delta_e / 100.0


def run(image_path: str | Path, out_dir: str | Path, max_iterations: int = 3,
        renderer: str = "chromium", thresholds: Thresholds | None = None) -> dict:
    t0 = time.time()
    max_iterations = max(1, min(int(max_iterations), MAX_ITERATIONS_CAP))
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    th = thresholds or Thresholds()

    img = load_image(image_path)
    stats = compute_stats(img)
    image_class = classify(stats)
    strategy = choose_strategy(stats, image_class)

    iterations = []
    failures = []
    best = None
    max_colors = strategy.max_colors
    eps_list = list(strategy.simplify_epsilons)
    eps_i = 0
    for it in range(max_iterations):
        eps = eps_list[min(eps_i, len(eps_list) - 1)]
        palette = build_palette(img.rgb, strategy, max_colors)
        if strategy.palette_mode == "dominant":
            labels = assign_labels_spatial(img.rgb, palette)
        else:
            labels = assign_labels(img.rgb, palette)
        doc = vectorize_labels(labels, palette, eps, strategy.min_loop_area)
        svg_text = write_svg(doc)
        svg_path = out / f"iter{it + 1}.svg"
        svg_path.write_text(svg_text, encoding="utf-8")
        val = validate_svg(svg_text, img.width, img.height)
        rec = {"iteration": it + 1, "epsilon": eps, "palette_size": int(palette.shape[0]),
               "paths": doc.path_count, "loops": doc.loop_count, "points": doc.point_count,
               "dropped_tiny_loops": doc.dropped_tiny_loops,
               "svg_bytes": len(svg_text.encode("utf-8")), "svg_validation": val.to_dict()}
        if not val.ok:
            rec["result"] = "FAIL"
            failures.append({"iteration": it + 1, "stage": "svg_validation", "reasons": val.errors})
            iterations.append(rec)
            break
        rendered, used, notes = render(svg_path, img.width, img.height, prefer=renderer)
        rec["renderer"] = used
        rec["renderer_notes"] = notes
        comp, heat = compare_images(img.rgb, rendered, palette, labels, th)
        rec["comparison"] = comp.to_dict()
        rec["result"] = "PASS" if comp.passed else "FAIL"
        iterations.append(rec)
        if best is None or _score(comp) > _score(best["comp"]):
            best = {"it": it + 1, "doc": doc, "svg": svg_text, "rendered": rendered,
                    "heat": heat, "comp": comp, "palette": palette, "labels": labels,
                    "renderer": used, "validation": val}
        if comp.passed:
            break
        reasons = [k for k, v in comp.checks.items() if not v]
        failures.append({"iteration": it + 1, "stage": "fidelity", "failed_checks": reasons,
                         "worst_cells": comp.worst_cells[:3]})
        # Refinement policy: geometry problems -> tighter tolerance;
        # pure color problems -> larger palette.
        changed = False
        if (not comp.checks["min_color_iou"] or not comp.checks["pixel_match_ratio"]) \
                and eps_i + 1 < len(eps_list):
            eps_i += 1
            changed = True
        if not comp.checks["mean_delta_e"] and strategy.palette_mode == "kmeans" \
                and max_colors < 64:
            max_colors = min(int(max_colors * 1.5), 64)
            changed = True
        if not changed:
            failures[-1]["note"] = "no refinement parameter left to adjust; stopping"
            break

    result = {
        "tool": "vector-fidelity", "version": __version__,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": {"path": str(image_path), "width": img.width, "height": img.height,
                   "mode": img.mode},
        "analysis": stats_to_dict(stats),
        "strategy": strategy.to_dict(),
        "thresholds": th.to_dict(),
        "iterations": iterations,
        "failures": failures,
        "outputs": {},
        "status": {},
    }

    if best is None:
        result["status"] = {
            "svg_generated": "FAIL", "svg_valid": "FAIL", "svg_render": "NOT VERIFIED",
            "fidelity": "NOT VERIFIED", "ai_file": "NOT VERIFIED",
            "illustrator_script": "NOT VERIFIED", "human_review": "PENDING",
            "overall": "FAILED",
        }
        write_reports(result, out)
        return result

    final_svg = out / "vector.svg"
    final_svg.write_text(best["svg"], encoding="utf-8")
    save_rgb(best["rendered"], out / "preview.png")
    save_rgb(best["heat"], out / "diff_heatmap.png")
    # Cross-check: the internal rasterizer must agree with the main renderer.
    cross = None
    if best["renderer"] == "chromium":
        internal = render_internal(best["svg"], img.width, img.height)
        d = np.abs(internal.astype(np.int16) - best["rendered"].astype(np.int16))
        cross = {"mean_abs_diff": round(float(d.mean()), 4),
                 "pixels_over_32": round(float((d.max(-1) > 32).mean()), 6)}

    bridge = get_bridge("script-export")
    jsx_path = out / "illustrator_build.jsx"
    jsx = build_jsx(best["doc"], str((out / "illustrator_output.ai").resolve()),
                    str((out / "illustrator_output.svg").resolve()),
                    str((out / "illustrator_output.png").resolve()))
    bstatus = bridge.build(jsx, jsx_path)

    comp = best["comp"]
    unrestored = []
    if "gradients" in strategy.expected_limitations:
        unrestored.append("Gradients: approximated by flat posterized bands; no vector "
                          "gradient objects were created.")
    if "transparency" in strategy.expected_limitations:
        unrestored.append("Transparency: composited onto white; alpha not reconstructed.")
    if "photographic texture" in strategy.expected_limitations:
        unrestored.append("Photographic texture: cannot be faithfully represented by "
                          "flat color regions.")
    unrestored.append("Text: no text detection/OCR in phase 1; any lettering is traced as "
                      "shapes, not editable text objects.")
    unrestored.append("Curves: output uses straight line segments (polylines); no Bezier "
                      "curve fitting in phase 1.")
    if best["doc"].dropped_tiny_loops:
        unrestored.append(f"Fine detail: {best['doc'].dropped_tiny_loops} tiny loops "
                          f"(< {strategy.min_loop_area} px²) were dropped.")
    if not comp.passed:
        unrestored.append("Fidelity thresholds not met after "
                          f"{len(iterations)} iteration(s); see failures and worst regions.")

    review_reasons = list(strategy.human_review_reasons)
    if not comp.passed:
        review_reasons.append("Fidelity thresholds not met.")
    review_reasons.append("Every result requires human visual confirmation before use.")

    result["outputs"] = {
        "svg": final_svg.name, "preview_png": "preview.png", "diff_heatmap": "diff_heatmap.png",
        "illustrator_script": jsx_path.name, "ai_file": None,
        "iteration_svgs": [f"iter{r['iteration']}.svg" for r in iterations],
    }
    result["best_iteration"] = best["it"]
    result["final"] = {
        "renderer": best["renderer"], "renderer_cross_check": cross,
        "palette": ["#%02x%02x%02x" % tuple(int(v) for v in c) for c in best["palette"]],
        "paths": best["doc"].path_count, "loops": best["doc"].loop_count,
        "points": best["doc"].point_count, "comparison": comp.to_dict(),
    }
    result["unrestored"] = unrestored
    result["illustrator"] = bstatus.to_dict()
    result["human_review"] = {"status": "PENDING", "reasons": review_reasons,
                              "reviewer": None, "note": None, "decided_at": None}
    result["status"] = {
        "svg_generated": "PASS",
        "svg_valid": "PASS" if best["validation"].ok else "FAIL",
        "svg_render": f"PASS ({best['renderer']})",
        "fidelity": "PASS" if comp.passed else "FAIL",
        "ai_file": "NOT VERIFIED",
        "illustrator_script": "NOT VERIFIED",
        "gradients": ("NOT RESTORED (posterized)" if "gradients" in strategy.expected_limitations
                      else "none detected"),
        "text": "NOT VERIFIED (no text detection in phase 1)",
        "human_review": "PENDING",
        "overall": "AWAITING_HUMAN_REVIEW" if comp.passed else "NEEDS_ATTENTION",
    }
    result["elapsed_seconds"] = round(time.time() - t0, 2)
    write_reports(result, out)
    return result


def record_review(out_dir: str | Path, approve: bool, reviewer: str, note: str) -> dict:
    out = Path(out_dir)
    data = json.loads((out / "report.json").read_text(encoding="utf-8"))
    if "human_review" not in data:
        raise ValueError("report has no human_review section (pipeline failed early)")
    data["human_review"].update({
        "status": "APPROVED" if approve else "REJECTED", "reviewer": reviewer,
        "note": note, "decided_at": time.strftime("%Y-%m-%dT%H:%M:%S%z")})
    data["status"]["human_review"] = data["human_review"]["status"]
    if not approve:
        data["status"]["overall"] = "REJECTED_BY_REVIEWER"
    elif data["status"].get("fidelity") == "PASS":
        # The AI file stays NOT VERIFIED regardless of human approval.
        data["status"]["overall"] = "SVG_APPROVED_AI_NOT_VERIFIED"
    else:
        data["status"]["overall"] = "APPROVED_WITH_KNOWN_GAPS"
    write_reports(data, out)
    return data
