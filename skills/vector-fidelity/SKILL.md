---
name: vector-fidelity
description: Rebuild a reference raster image (PNG/JPG) as real, editable vector paths (SVG), render it, compare it with the original, refine a bounded number of times, and write a quality report that lists what was not restored. Use when the user hands over an image and asks to vectorize, redraw, trace, or recreate it as vector art or for Adobe Illustrator.
---

# Vector Fidelity (phase 1)

Goal: a vector reconstruction that is faithful to the source image **and**
honest about its gaps. Never fake vectorization by embedding the raster
(`<image>`, `data:` URIs, `foreignObject`); the validator rejects that and
the report must then say FAIL.

## Hard rules

1. Work only on local files. Do **not** upload the user's image to any
   external service, website or API. (The image is processed on this machine
   by `tools/vf.py`.)
2. Do **not** install packages, MCP servers or global tools without explicit
   human approval. Required: Python 3.10+ with `numpy` and `Pillow`.
   Optional: Node.js + Playwright Chromium (independent SVG renderer).
3. Never report the `.ai` file as created or verified. Phase 1 does not run
   Adobe Illustrator. It only writes `illustrator_build.jsx`, whose status is
   always `NOT VERIFIED` until it has been run in Illustrator on Windows and
   the result has been checked.
4. Use the three result labels exactly: **PASS** (there is test evidence),
   **FAIL** (a check failed), **NOT VERIFIED** (not tested).
5. At most 3 refinement iterations by default (hard cap 5).

## Locating the tools

The scripts live in the plugin root, two directories above this file:
`<plugin-root>/tools/vf.py`. If `${CLAUDE_PLUGIN_ROOT}` is set, use
`${CLAUDE_PLUGIN_ROOT}/tools/vf.py`.

## Workflow

1. **Inspect the image yourself** (Read it) and note: subject, number of
   distinct flat colors, gradients, text, transparency, fine detail.
2. **Analyse / classify**:
   `python3 <root>/tools/vf.py analyze <image>`
   Classes and rules (see `tools/vectorfidelity/analyze.py`):
   - `flat_graphic`: top-16 colors ≥ 80 % of pixels and ≥ 97 % of pixels are
     those colors or within 2 px of them (anti-aliasing) → exact palette.
   - `photographic`: > 25 % of neighbour pairs differ strongly → posterize,
     low expected fidelity, **ask the human whether to continue**.
   - `graphic_with_gradients`: ≥ 5 % smooth neighbour transitions → k-means
     posterization; gradients are reported as NOT RESTORED.
   - `complex_graphic`: everything else → k-means palette.
3. **Run the pipeline** (output folder next to the user's work, never inside
   the plugin directory):
   `python3 <root>/tools/vf.py run <image> --out <out_dir> [--max-iterations 3]`
   It: keeps the exact pixel size and aspect ratio (viewBox = source size),
   builds the palette, traces each color region along pixel boundaries,
   smooths stair-steps, simplifies with Ramer-Douglas-Peucker, writes
   `vector.svg`, validates it, renders it (Chromium, else the internal
   rasterizer), compares, and refines (tighter tolerance for geometry
   errors, larger palette for color errors).
4. **Read `<out_dir>/report.md`** and look at `preview.png` and
   `diff_heatmap.png` (red = larger color error) yourself.
5. **Report to the user** with the status table, the metrics vs thresholds,
   the "Not restored / known gaps" list and the failure log. Do not round a
   FAIL into a PASS.
6. **Human confirmation**: ask the user to review. Record their decision:
   `python3 <root>/tools/vf.py review <out_dir> --approve|--reject --reviewer NAME --note TEXT`
   Approval never changes `ai_file` from NOT VERIFIED.

## Fidelity thresholds (defaults)

| Metric | Threshold |
|---|---|
| Pixel match ratio (max channel diff ≤ 24) | ≥ 0.97 |
| Mean ΔE76 (CIELAB) | ≤ 3.0 |
| Min IoU per palette color (colors ≥ 0.2 % of area) | ≥ 0.95 |

Passing these thresholds means "close at pixel level", not "identical
artwork". Always add your own visual judgement.

## Outputs

`vector.svg`, `preview.png` (render of the SVG), `diff_heatmap.png`,
`report.json`, `report.md`, `illustrator_build.jsx` (NOT VERIFIED),
`iterN.svg` for each iteration.

## Known phase-1 limits (always mention the relevant ones)

- Straight line segments only (no Bézier fitting).
- Gradients become flat bands (no vector gradients).
- No text detection: lettering is traced as shapes, not editable text.
- Transparency is composited on white.
- Sharp corners next to diagonal edges can be slightly shifted (< 1 px).
- The `.jsx` → Illustrator path, `.ai` saving and Illustrator SVG/PNG export
  have never been executed: NOT VERIFIED.
