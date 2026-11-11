# Illustrator AI Assistant

A Claude Code plugin (`illustrator-ai-assistant`) that takes a reference image and
rebuilds it as **real, editable vector paths**. It compares the vector render against
the original, refines it a limited number of times, and writes a quality report. The
report states plainly which parts were not restored.

> **Phase 1 status:** SVG vectorization, validation, rendering, comparison, reporting
> and the human-review step are implemented and tested on Linux. Adobe Illustrator is
> **not** driven yet. The plugin generates an ExtendScript file
> (`illustrator_build.jsx`) for building the `.ai` file, but nobody has run it in
> Illustrator, so `.ai` output is **NOT VERIFIED**.

## What it does

1. Analyses and classifies the image (flat graphic / with gradients / complex / photographic).
2. Keeps the exact pixel size and aspect ratio (`viewBox` = source size).
3. Extracts the palette, traces each color region, and smooths and simplifies the paths.
4. Validates the SVG. Embedded rasters (`<image>`, `data:` URIs, `foreignObject`) are rejected.
5. Renders the SVG (Chromium via Playwright, or the built-in rasterizer) and compares
   it with the original (pixel match, ΔE76, per-color IoU, worst regions).
6. Refines at most 3 times, logging the reason for each failure.
7. Writes `report.md` / `report.json`, then waits for a human approve/reject decision.

## Two modes

- **Trace mode** (`vf.py run`): traces the whole image into color regions. Suits logos,
  icons and flat illustrations.
- **Compose mode** (`vf.py compose`): for price lists, stickers and signs. Text becomes
  real text objects, boxes and arrows are native shapes, each icon is traced on its own,
  and logos / QR codes are left as marked placeholders. It is driven by a layout spec
  (see `examples/compose/`). The SVG has not been verified inside Illustrator.

## Layout

```
.claude-plugin/plugin.json      plugin manifest
skills/vector-fidelity/SKILL.md the skill Claude follows
tools/vf.py                     CLI (analyze | run | validate | review)
tools/vectorfidelity/           Python package (analysis, tracing, SVG, render, compare, report)
tools/vectorfidelity/illustrator/  Illustrator connection interface + .jsx generator (NOT VERIFIED)
tools/render_svg.cjs            Chromium SVG renderer (network blocked)
tests/                          unittest suite
examples/make_test_images.py    generates the self-made test images in examples/images/
docs/                           architecture, Illustrator MCP evaluation, test report
```

## Requirements

- Python 3.10+ with `numpy` and `Pillow` (`requirements.txt`).
- Optional: Node.js and the `playwright` package with Chromium, for the independent renderer.
  Without it, the built-in rasterizer is used and the report names the renderer.

Nothing is installed automatically. Ask before installing anything.

## Usage

```bash
python3 tools/vf.py run path/to/image.png --out path/to/output
python3 tools/vf.py compose path/to/layout_spec.json --out path/to/new_output
python3 tools/vf.py review path/to/output --approve --reviewer NAME --note "checked"
python3 -m unittest discover -s tests -v
```

Inside Claude Code, load the plugin (for example `claude --plugin-dir <this folder>`)
and ask Claude to vectorize an image. The `vector-fidelity` skill drives the workflow.

## Privacy

All processing is local. Images are never uploaded to an external service. The
Chromium renderer aborts every network request from the page.
