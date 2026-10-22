"""Colours, paragraph styles and page decoration shared by the ARCILOGGER PDF reports.

Moved here from the official 5651 report (``arcilogger_api.reports.render``) unchanged, so that
the usage reports of the worker (ADR-0012) look the same without a second copy; the 5651 report
renders the same bytes as before (its tests re-render stored inputs and compare them).
"""

from __future__ import annotations

from typing import Any, Final

from reportlab.lib import colors  # type: ignore[import-untyped]
from reportlab.lib.styles import ParagraphStyle  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.pdfbase.pdfmetrics import stringWidth  # type: ignore[import-untyped]
from reportlab.pdfgen.canvas import Canvas  # type: ignore[import-untyped]

from arcilogger_worker.pdf.fonts import MONO, SANS, SANS_BOLD

INK: Final = colors.HexColor("#0F172A")
MUTED: Final = colors.HexColor("#475569")
PRIMARY: Final = colors.HexColor("#0F766E")
LINE: Final = colors.HexColor("#CBD5E1")
SUBTLE: Final = colors.HexColor("#F1F5F9")
DANGER: Final = colors.HexColor("#B91C1C")
WARNING: Final = colors.HexColor("#92400E")
MARGIN: Final = 18 * mm


def report_styles() -> dict[str, Any]:
    """The paragraph styles of the reports (``base``, ``title``, ``h2``, ``small``, …)."""
    base = ParagraphStyle(
        "base", fontName=SANS, fontSize=8.8, leading=11.6, textColor=INK, splitLongWords=1
    )
    return {
        "base": base,
        "title": ParagraphStyle(
            "title", parent=base, fontName=SANS_BOLD, fontSize=16, leading=20, textColor=PRIMARY
        ),
        "subtitle": ParagraphStyle("subtitle", parent=base, fontSize=9.5, textColor=MUTED),
        "h2": ParagraphStyle(
            "h2",
            parent=base,
            fontName=SANS_BOLD,
            fontSize=11.5,
            leading=14,
            spaceBefore=10,
            spaceAfter=5,
            textColor=PRIMARY,
        ),
        "h3": ParagraphStyle(
            "h3",
            parent=base,
            fontName=SANS_BOLD,
            fontSize=9.6,
            leading=12,
            spaceBefore=6,
            spaceAfter=3,
        ),
        "label": ParagraphStyle("label", parent=base, fontName=SANS_BOLD, fontSize=8.2),
        "small": ParagraphStyle("small", parent=base, fontSize=7.4, leading=9.6, textColor=MUTED),
        "mono": ParagraphStyle("mono", parent=base, fontName=MONO, fontSize=7.2, leading=9.2),
        "warn": ParagraphStyle("warn", parent=base, fontName=SANS_BOLD, textColor=DANGER),
        "note": ParagraphStyle("note", parent=base, textColor=WARNING),
        "bullet": ParagraphStyle("bullet", parent=base, leftIndent=9, bulletIndent=0),
    }


def fit(text: str, size: float, width: float) -> str:
    """``text`` cut with "…" so that it fits ``width`` points (header and footer lines do not
    wrap; a long file number or organisation name must not run off the page)."""
    if stringWidth(text, SANS, size) <= width:
        return text
    cut = len(text)
    while cut > 0 and stringWidth(text[:cut] + "…", SANS, size) > width:
        cut -= 1
    return text[:cut] + "…"


class PagedCanvas(Canvas):  # type: ignore[misc]
    """Keeps every page until the document is complete, then draws each page's header and
    footer with the page count known ("Sayfa n / N"): subclasses implement :meth:`decorate`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._pages: list[dict[str, Any]] = []

    def showPage(self) -> None:  # noqa: N802 - reportlab API
        self._pages.append(dict(self.__dict__))
        self._startPage()

    def save(self) -> None:
        total = len(self._pages)
        self.counted(total)
        for state in self._pages:
            self.__dict__.update(state)
            self.decorate(total)
            super().showPage()
        super().save()

    def counted(self, total: int) -> None:
        """Called once with the number of pages, before the first page is decorated."""

    def decorate(self, total: int) -> None:  # pragma: no cover - every subclass draws its own
        raise NotImplementedError
