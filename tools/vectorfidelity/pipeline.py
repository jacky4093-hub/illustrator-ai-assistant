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
from .outputs import ILLUSTRATOR_TARGETS, MAX_ITERATION_FILES, plan_outputs
from .render import render, render_internal
from .report import write_reports
from .svg import validate_svg, write_svg
from .trace import vectorize_labels

MAX_ITERATIONS_CAP = MAX_ITERATION_FILES

# Machine-readable outcome codes (also mapped to CLI exit codes in vf.py).
OUTCOME_METRICS_PASS = "METRICS_PASS_NO_KNOWN_GAPS"      # still needs human review; .ai NOT VERIFIED
OUTCOME_UNRESTORED = "INCOMPLETE_UNRESTORED"             # known unrestored aspects (e.g. gradients)
OUTCOME_FIDELITY_FAIL = "INCOMPLETE_FIDELITY_FAIL"       # thresholds not met
OUTCOME_FAILED = "FAILED"                                # no valid vector output


def _overall_and_outcome(fidelity_passed: bool, limitations: list[str]) -> tuple[str, str]:
    if limitations:
        return "NEEDS_ATTENTION_UNRESTORED", OUTCOME_UNRESTORED
    if not fidelity_passed:
        return "NEEDS_ATTENTION", OUTCOME_FIDELITY_FAIL
    return "AWAITING_HUMAN_REVIEW", OUTCOME_METRICS_PASS


def _score(comp) -> float:
    return comp.pixel_match_ratio + comp.min_color_iou - comp.mean_delta_e / 100.0


def run(image_path: str | Path, out_dir: str | Path, max_iterations: int = 3,
        renderer: str = "chromium", thresholds: Thresholds | None = None,
        force: bool = False) -> dict:
    t0 = time.time()
    max_iterations = max(1, min(int(max_iterations), MAX_ITERATIONS_CAP))
    # All safety checks happen before anything is created or written;
    # raises outputs.OutputConflictError on any conflict.
    plan = plan_outputs(image_path, out_dir, force=force)
    out = plan.out_dir
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
        svg_path = plan.path(f"iter{it + 1}.svg")
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
        result["outcome"] = OUTCOME_FAILED
        result["known_gaps"] = list(strategy.expected_limitations)
        result["output_safety"] = {"force": force, "updated_existing": plan.will_update,
                                   "untouched_unknown_files": plan.existing_unknown}
        write_reports(result, out)
        return result

    final_svg = plan.path("vector.svg")
    final_svg.write_text(best["svg"], encoding="utf-8")
    save_rgb(best["rendered"], plan.path("preview.png"))
    save_rgb(best["heat"], plan.path("diff_heatmap.png"))
    # Cross-check: the internal rasterizer must agree with the main renderer.
    cross = None
    if best["renderer"] == "chromium":
        internal = render_internal(best["svg"], img.width, img.height)
        d = np.abs(internal.astype(np.int16) - best["rendered"].astype(np.int16))
        cross = {"mean_abs_diff": round(float(d.mean()), 4),
                 "pixels_over_32": round(float((d.max(-1) > 32).mean()), 6)}

    bridge = get_bridge("script-export")
    jsx_path = plan.path("illustrator_build.jsx")
    ai_t, svg_t, png_t = (str((out / n).resolve()) for n in ILLUSTRATOR_TARGETS)
    jsx = build_jsx(best["doc"], ai_t, svg_t, png_t)  # refuses existing targets
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
    limitations = list(strategy.expected_limitations)
    overall, outcome = _overall_and_outcome(comp.passed, limitations)
    if comp.passed:
        fidelity = "PASS (metrics only)" if limitations else "PASS"
    else:
        fidelity = "FAIL"
    result["outcome"] = outcome
    result["known_gaps"] = limitations
    result["output_safety"] = {"force": force, "updated_existing": plan.will_update,
                               "untouched_unknown_files": plan.existing_unknown}
    result["status"] = {
        "svg_generated": "PASS",
        "svg_valid": "PASS" if best["validation"].ok else "FAIL",
        "svg_render": f"PASS ({best['renderer']})",
        "fidelity": fidelity,
        "ai_file": "NOT VERIFIED",
        "illustrator_script": "NOT VERIFIED",
        "gradients": ("NOT RESTORED (posterized)" if "gradients" in strategy.expected_limitations
                      else "none detected"),
        "text": "NOT VERIFIED (no text detection in phase 1)",
        "human_review": "PENDING",
        "overall": overall,
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
    # Human approval never removes known gaps: "unrestored", "known_gaps",
    # "outcome" and the per-item statuses (e.g. "gradients") are left as is.
    known_gaps = data.get("known_gaps") or data.get("strategy", {}).get("expected_limitations") or []
    if not approve:
        data["status"]["overall"] = "REJECTED_BY_REVIEWER"
    elif known_gaps or data["status"].get("fidelity") != "PASS":
        data["status"]["overall"] = "APPROVED_WITH_KNOWN_GAPS"
    else:
        # The AI file stays NOT VERIFIED regardless of human approval.
        data["status"]["overall"] = "SVG_APPROVED_AI_NOT_VERIFIED"
    write_reports(data, out)
    return data
