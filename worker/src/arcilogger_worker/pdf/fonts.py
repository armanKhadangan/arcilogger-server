"""Fonts of the PDF reports: DejaVu Sans (text) and DejaVu Sans Mono (hashes, raw lines).

Bundled in ``arcilogger_worker/fonts/`` with their licence (Bitstream Vera / Arev licence, DejaVu
changes in the public domain — permissive) and a README naming the exact source (npm package
``dejavu-fonts-ttf`` 2.37.3) and each file's SHA-256. No font is loaded from the system or the
network, so a report looks the same on every server. Two reports use them: the official 5651
report of the API (``arcilogger_api.reports``, which depends on this package) and the scheduled
usage reports of the worker (:mod:`arcilogger_worker.reports`, ADR-0012) — one copy of the
files, one registration, the same registered names (the 5651 report's bytes depend on them).

The fonts are embedded as subsets with a ``ToUnicode`` map (reportlab does both for TrueType
fonts), so the Turkish text of a report can be searched and copied.
"""

from __future__ import annotations

import hashlib
import threading
from pathlib import Path
from typing import Final

# reportlab ships no type information (no py.typed, no stubs in the lock file).
from reportlab.lib.fonts import addMapping  # type: ignore[import-untyped]
from reportlab.pdfbase import pdfmetrics  # type: ignore[import-untyped]
from reportlab.pdfbase.ttfonts import TTFont  # type: ignore[import-untyped]

FONT_DIR: Final = Path(__file__).resolve().parent.parent / "fonts"
SANS: Final = "ArciSans"
SANS_BOLD: Final = "ArciSans-Bold"
MONO: Final = "ArciMono"
#: Registered name → file in :data:`FONT_DIR`.
FILES: Final = {
    SANS: "DejaVuSans.ttf",
    SANS_BOLD: "DejaVuSans-Bold.ttf",
    MONO: "DejaVuSansMono.ttf",
}
#: Letters of the Turkish alphabet that are not in ASCII; every report font must have them.
TURKISH: Final = "çÇğĞıİöÖşŞüÜâÂîÎûÛ"

_lock = threading.Lock()
_registered = False


def register_fonts() -> None:
    """Register the bundled fonts with reportlab (once per process; thread-safe)."""
    global _registered
    with _lock:
        if _registered:
            return
        for name, file in FILES.items():
            pdfmetrics.registerFont(TTFont(name, str(FONT_DIR / file)))
        pdfmetrics.registerFontFamily(
            SANS, normal=SANS, bold=SANS_BOLD, italic=SANS, boldItalic=SANS_BOLD
        )
        addMapping(MONO, 0, 0, MONO)
        addMapping(MONO, 1, 0, MONO)
        addMapping(MONO, 0, 1, MONO)
        addMapping(MONO, 1, 1, MONO)
        _registered = True


def missing_glyphs(font_name: str, text: str = TURKISH) -> list[str]:
    """Characters of ``text`` the registered font has no glyph for (glyph 0 is "missing")."""
    register_fonts()
    font = pdfmetrics.getFont(font_name)
    mapping: dict[int, int] = font.face.charToGlyph
    return [char for char in text if not mapping.get(ord(char))]


def font_fingerprints() -> dict[str, str]:
    """``file name → SHA-256`` of the bundled fonts (recorded with every report)."""
    return {
        file: hashlib.sha256((FONT_DIR / file).read_bytes()).hexdigest()
        for file in sorted(FILES.values())
    }
