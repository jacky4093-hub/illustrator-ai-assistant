---
name: vector-fidelity
description: Rebuild a reference raster image (PNG/JPG) as real, editable vector paths (SVG), render it, compare it with the original, refine a bounded number of times, and write a quality report that lists what was not restored. On Windows with Adobe Illustrator installed, and only after explicit human approval for each run, it can also run the generated ExtendScript (.jsx) through a PowerShell COM launcher so Illustrator writes an editable .ai file plus SVG and PNG exports; missing exports are reported as failures and never retried. Results are not guaranteed to be identical to the source. Load this skill first, before answering, whenever the user wants to vectorize, trace, redraw or convert an image into vector art, an SVG, an Adobe Illustrator (.ai) file or an editable Illustrator document, including questions about how that would work.
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
3. Never report the `.ai` file as verified. By default Adobe Illustrator is
   not run at all; only `illustrator_build.jsx` is written. Even after an
   Illustrator run (see "Running Illustrator on Windows"), a file that exists
   is not proof: `ai_file` stays `NOT VERIFIED` until a human has opened the
   `.ai` in Illustrator and confirmed it contains editable vector paths.
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
   Use a new or empty `<out_dir>`. A non-empty directory is refused (exit 4,
   nothing written). Only pass `--force` when the user explicitly asks to
   update a previous run; it updates this tool's own files only, never the
   source image, unknown files or existing `illustrator_output.*`.
   Read the printed `outcome`: `METRICS_PASS_NO_KNOWN_GAPS` (exit 0) is the
   only full metric success; `INCOMPLETE_UNRESTORED` / `INCOMPLETE_FIDELITY_FAIL`
   (exit 3) and `FAILED` (exit 1) must be reported as not fully successful.
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
   Approval never changes `ai_file` from NOT VERIFIED, and approving a result
   with known gaps yields `APPROVED_WITH_KNOWN_GAPS` (gaps stay listed).

## Running Illustrator on Windows (optional, needs approval every time)

Only on a Windows machine with Adobe Illustrator installed, in a Claude Code
session running on that machine. A cloud or remote Linux session cannot
control Illustrator; there `--run-illustrator` is refused (exit 4).

1. Finish the normal workflow first and show the user the report.
2. **Ask the user** each time, before running anything: "Run this script in
   Illustrator now? Please save and close all other Illustrator documents
   first." Do not proceed without a clear yes in this conversation. An
   earlier approval for another image or folder does not count.
3. Run in a **new** output folder, with the approver's name:
   `python <root>/tools/vf.py run <image> --out <new_dir> --run-illustrator --approved-by "<name>"`
4. Read `illustrator_run` in the printed JSON and report it as is:
   - `RETURNED_FILES_PRESENT`: Illustrator returned and the three files
     exist. Say exactly that. The `.ai` is still NOT VERIFIED; ask the user
     to open it and check the paths are editable.
   - `REFUSED_BY_LAUNCHER` / `REFUSED_PRECHECK`: nothing ran (for example
     other documents are open, or an output file exists). Tell the user why.
   - `SCRIPT_ERROR`, `RETURNED_FILES_MISSING`: report as FAIL with the error.
     An error starting with `VF_EXPORT_MISSING` means Illustrator returned
     from an export or save without writing that file (seen once for the PNG
     on Illustrator 2021; cause not verified). The other files and the open
     document are left as they are; do not save or retry. Suggest the user
     restarts Illustrator before a new, separately approved run.
   - `UNKNOWN_TIMEOUT`, `UNKNOWN_LAUNCHER_FAILURE`: say the state of
     Illustrator is unknown; it may still be working or waiting on a dialog.
     Ask the user to look at Illustrator.
5. After any failure, refusal after start, or timeout: **stop**. Never retry
   on your own, never close documents, never quit or kill Illustrator, never
   delete the output folder (it is the evidence). Wait for the user.
6. Never call `tools/illustrator/run_illustrator_jsx.ps1` directly, never
   run any other `.jsx`, and never edit `illustrator_build.jsx`: edited or
   foreign scripts are refused by the integrity check.

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
`iterN.svg` for each iteration. After an Illustrator run also
`illustrator_run.json` (run record) and `illustrator_output.ai/.svg/.png`.

## Known phase-1 limits (always mention the relevant ones)

- Straight line segments only (no Bézier fitting).
- Gradients become flat bands (no vector gradients).
- No text detection: lettering is traced as shapes, not editable text.
- Transparency is composited on white.
- Sharp corners next to diagonal edges can be slightly shifted (< 1 px).
- The automated Windows COM run is only simulated in the test suite. Treat
  every real run as unverified until the user has inspected the `.ai` file.
- Illustrator's exported PNG is not yet compared with the original.
- Illustrator 2021 once returned from the PNG export without writing the file
  or raising an error (cause not verified). The script now reports this as
  `VF_EXPORT_MISSING`; it does not wait for late files and never retries.
