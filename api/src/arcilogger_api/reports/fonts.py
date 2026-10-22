"""The report fonts, under their old import path (moved to :mod:`arcilogger_worker.pdf.fonts`).

The DejaVu files and their registration live in the worker package since the scheduled usage
reports (ADR-0012) render PDFs too and the worker may not import the API; the official 5651
report keeps the same registered names and files, so its bytes do not change.
"""

from __future__ import annotations

from arcilogger_worker.pdf.fonts import (
    FILES,
    FONT_DIR,
    MONO,
    SANS,
    SANS_BOLD,
    TURKISH,
    font_fingerprints,
    missing_glyphs,
    register_fonts,
)

__all__ = [
    "FILES",
    "FONT_DIR",
    "MONO",
    "SANS",
    "SANS_BOLD",
    "TURKISH",
    "font_fingerprints",
    "missing_glyphs",
    "register_fonts",
]
