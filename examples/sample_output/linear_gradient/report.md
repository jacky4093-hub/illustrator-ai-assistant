# Vector Fidelity Quality Report

- Source: `examples/images/linear_gradient.png` (200x120, RGB)
- Generated: 2026-11-10T14:47:22+0000 by vector-fidelity 0.1.0
- Image class: **graphic_with_gradients**
- Strategy: posterized color-region tracing (gradients approximated by flat bands)

## Status

| Item | Result |
|---|---|
| svg_generated | PASS |
| svg_valid | PASS |
| svg_render | PASS (chromium) |
| fidelity | PASS (metrics only) |
| ai_file | NOT VERIFIED |
| illustrator_script | NOT VERIFIED |
| gradients | NOT RESTORED (posterized) |
| text | NOT VERIFIED (no text detection in phase 1) |
| human_review | PENDING |
| overall | NEEDS_ATTENTION_UNRESTORED |
| outcome (machine-readable) | INCOMPLETE_UNRESTORED |

> **Not a full success:** known unrestored aspects: gradients. Metric PASS does not mean these were restored; human approval does not remove them.

> `ai_file` is NOT VERIFIED: phase 1 never runs Adobe Illustrator. `illustrator_build.jsx` is generated for manual execution only.

## Fidelity metrics (best iteration 1, renderer: chromium)

| Metric | Value | Threshold | Check |
|---|---|---|---|
| Pixel match ratio (≤24/255 per channel) | 1.0 | ≥ 0.97 | PASS |
| Mean ΔE76 | 1.9307 | ≤ 3.0 | PASS |
| Min color-region IoU | 1.0 | ≥ 0.95 | PASS |
| P95 ΔE76 | 4.0415 | (info) | - |
| MAE / RMSE / PSNR | 1.875 / 2.2851 / 40.953 dB | (info) | - |

- Geometry: 23 paths, 23 loops, 92 points
- Palette (24): #515ca3, #f1c22e, #bda154, #aa9562, #938773, #797686, #b49b5b, #2340c4, #3047bb, #5c649b, #ddb53d, #d0ad47, #676a93, #eabe34, #e3ba38, #8a8179, #4555ab, #f8c72a, #3b4eb3, #827c7f, #c7a84d, #9e8e6b, #d7b142, #70708c
- Renderer cross-check (internal vs chromium): mean abs diff 0.0, pixels > 32 diff: 0.0

### Worst regions (8x8 grid, mean ΔE)

- x=0 y=0 25x15: ΔE 2.679
- x=0 y=15 25x15: ΔE 2.679
- x=0 y=30 25x15: ΔE 2.679
- x=0 y=45 25x15: ΔE 2.679
- x=0 y=60 25x15: ΔE 2.679

## Iterations

| # | epsilon | colors | paths | points | renderer | result |
|---|---|---|---|---|---|---|
| 1 | 0.35 | 24 | 23 | 92 | chromium | PASS |

## Not restored / known gaps

- Gradients: approximated by flat posterized bands; no vector gradient objects were created.
- Text: no text detection/OCR in phase 1; any lettering is traced as shapes, not editable text objects.
- Curves: output uses straight line segments (polylines); no Bezier curve fitting in phase 1.

## Human review

- Status: **PENDING**
- Reason: Smooth color transitions detected; phase 1 approximates gradients with flat color bands instead of real vector gradients.
- Reason: Every result requires human visual confirmation before use.

Checklist for the reviewer:

1. Open `vector.svg` and the original side by side.
2. Check outline, proportions, colors; inspect `diff_heatmap.png` (red = larger error).
3. Confirm the items under *Not restored* are acceptable.
4. Record the decision: `python3 tools/vf.py review <out_dir> --approve|--reject --reviewer NAME --note TEXT`.
