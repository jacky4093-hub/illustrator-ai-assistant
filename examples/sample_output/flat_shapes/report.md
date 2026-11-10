# Vector Fidelity Quality Report

- Source: `examples/images/flat_shapes.png` (240x160, RGB)
- Generated: 2026-11-10T14:47:21+0000 by vector-fidelity 0.1.0
- Image class: **flat_graphic**
- Strategy: color-region tracing (pixel-exact boundaries + RDP simplification)

## Status

| Item | Result |
|---|---|
| svg_generated | PASS |
| svg_valid | PASS |
| svg_render | PASS (chromium) |
| fidelity | PASS |
| ai_file | NOT VERIFIED |
| illustrator_script | NOT VERIFIED |
| gradients | none detected |
| text | NOT VERIFIED (no text detection in phase 1) |
| human_review | PENDING |
| overall | AWAITING_HUMAN_REVIEW |
| outcome (machine-readable) | METRICS_PASS_NO_KNOWN_GAPS |

> `ai_file` is NOT VERIFIED: phase 1 never runs Adobe Illustrator. `illustrator_build.jsx` is generated for manual execution only.

## Fidelity metrics (best iteration 1, renderer: chromium)

| Metric | Value | Threshold | Check |
|---|---|---|---|
| Pixel match ratio (≤24/255 per channel) | 0.9881 | ≥ 0.97 | PASS |
| Mean ΔE76 | 0.4325 | ≤ 3.0 | PASS |
| Min color-region IoU | 0.98779 | ≥ 0.95 | PASS |
| P95 ΔE76 | 1.1883 | (info) | - |
| MAE / RMSE / PSNR | 0.7897 / 5.7898 / 32.877 dB | (info) | - |

- Geometry: 3 paths, 3 loops, 71 points
- Palette (4): #ffffff, #dc2828, #1e5ac8, #28a046
- Renderer cross-check (internal vs chromium): mean abs diff 0.0755, pixels > 32 diff: 0.0

### Worst regions (8x8 grid, mean ΔE)

- x=210 y=120 30x20: ΔE 2.129
- x=180 y=60 30x20: ΔE 1.56
- x=150 y=60 30x20: ΔE 1.421
- x=120 y=60 30x20: ΔE 1.316
- x=180 y=120 30x20: ΔE 1.31

## Iterations

| # | epsilon | colors | paths | points | renderer | result |
|---|---|---|---|---|---|---|
| 1 | 0.35 | 4 | 3 | 71 | chromium | PASS |

## Not restored / known gaps

- Text: no text detection/OCR in phase 1; any lettering is traced as shapes, not editable text objects.
- Curves: output uses straight line segments (polylines); no Bezier curve fitting in phase 1.

## Human review

- Status: **PENDING**
- Reason: Every result requires human visual confirmation before use.

Checklist for the reviewer:

1. Open `vector.svg` and the original side by side.
2. Check outline, proportions, colors; inspect `diff_heatmap.png` (red = larger error).
3. Confirm the items under *Not restored* are acceptable.
4. Record the decision: `python3 tools/vf.py review <out_dir> --approve|--reject --reviewer NAME --note TEXT`.
