# Vector Fidelity Quality Report

- Source: `examples/images/noise_texture.png` (128x96, RGB)
- Generated: 2026-11-10T14:47:23+0000 by vector-fidelity 0.1.0
- Image class: **photographic**
- Strategy: posterized color-region tracing (low expected fidelity)

## Status

| Item | Result |
|---|---|
| svg_generated | FAIL |
| svg_valid | FAIL |
| svg_render | NOT VERIFIED |
| fidelity | NOT VERIFIED |
| ai_file | NOT VERIFIED |
| illustrator_script | NOT VERIFIED |
| gradients | NOT VERIFIED |
| text | NOT VERIFIED |
| human_review | PENDING |
| overall | FAILED |
| outcome (machine-readable) | FAILED |

> **Not a full success:** known unrestored aspects: photographic texture, gradients. Metric PASS does not mean these were restored; human approval does not remove them.

> `ai_file` is NOT VERIFIED: phase 1 never runs Adobe Illustrator. `illustrator_build.jsx` is generated for manual execution only.

## Iterations

| # | epsilon | colors | paths | points | renderer | result |
|---|---|---|---|---|---|---|
| 1 | 0.6 | 32 | 0 | 0 | - | FAIL |

## Failure log

- iteration 1 / svg_validation: {"reasons": ["no <path> elements: nothing was vectorized"]}
