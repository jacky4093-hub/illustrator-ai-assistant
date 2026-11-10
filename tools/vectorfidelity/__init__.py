"""Vector Fidelity toolkit for the Illustrator AI Assistant plugin.

Phase 1 scope: analyse a raster image, build a true vector (SVG path)
reconstruction, render it, compare it against the original and write a
quality report. Illustrator (.ai) output is only *prepared* (ExtendScript
generation) and is never reported as verified.

Runtime dependencies: Python 3.10+, numpy, Pillow.
Optional: Node.js + Playwright Chromium for an independent SVG renderer.
"""

__version__ = "0.1.0"
