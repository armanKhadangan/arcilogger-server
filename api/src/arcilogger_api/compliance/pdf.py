"""The Uyum Durumu Raporu ("Compliance status report", Wave 4 contract 2.3.4): the ten cards of
:func:`arcilogger_api.compliance.cards.compute_status` as an A4 PDF.

Made on request (``GET /compliance/report.pdf``): never stored and never time-stamped (contract
D8, owner question Q14); the route audits its SHA-256. Same building blocks as the other reports
(:mod:`arcilogger_worker.pdf`): the bundled fonts, the shared styles, invariant mode (the same
status gives the same bytes) and "Sayfa n / N" with the neutral footer note of the 5651 report.

Content, in this order: the title in both languages; the site name; the generation time; the
signed-in account that exported it (display name and username, the one exception of contract
0.1 item 4); the summary line; the notice; then per card its number and titles, the tone as text
("[TAMAM] / [OK]": never colour alone), both reasons, the facts (Turkish label and value), the
verify-on-site note, the active alerts (rule, severity, opened) and the time of the check. The
cards carry no data about log subjects, so neither does the report.
"""

from __future__ import annotations

import hashlib
import io
from dataclasses import dataclass
from typing import Any, Final
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    CondPageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from arcilogger_api.compliance.models import ComplianceCard, ComplianceStatus, ComplianceTone
from arcilogger_api.compliance.texts import NONE, TONE_LABELS, Text
from arcilogger_api.reports.texts import FOOTER_NOTE_EN, FOOTER_NOTE_TR
from arcilogger_core import __version__
from arcilogger_core.fmt import format_datetime
from arcilogger_worker.alerts.texts import SEVERITY_LABELS
from arcilogger_worker.pdf.fonts import SANS, register_fonts
from arcilogger_worker.pdf.styles import LINE, MARGIN, MUTED, PagedCanvas, fit, report_styles

WIDTH: Final = A4[0] - 2 * MARGIN
LABEL_WIDTH: Final = 60 * mm

TITLE: Final[Text] = ("Uyum Durumu Raporu", "Compliance status report")
#: The summary line of the page (``compliance.summary``, contract 2.8.5).
SUMMARY: Final[Text] = ("{total} denetimden {ok} tanesi tamam", "{ok} of {total} checks OK")
SITE: Final = "Kurum / Site: {name}"
GENERATED: Final = "Oluşturulma / Generated: {time} (Europe/Istanbul)"
EXPORTED_BY: Final = "Oluşturan / Exported by: {display_name} ({username})"
VERIFY_ON_SITE: Final = "Sahada doğrulayın / Verify on site"
ALERTS: Final = "Açık uyarılar / Active alerts"
CHECKED: Final = "Denetlendi / Checked: {time}"
#: The style a tone's text is set in (the text says it; the colour only repeats it).
TONE_STYLES: Final[dict[ComplianceTone, str]] = {
    "ok": "label",
    "warning": "note",
    "error": "warn",
    "neutral": "label",
}


@dataclass(frozen=True)
class RenderedReport:
    pdf: bytes
    pages: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pdf).hexdigest()


def _x(value: object) -> str:
    """Escape for reportlab's paragraph markup."""
    return escape(str(value))


def tone_text(tone: ComplianceTone) -> str:
    """``[TAMAM] / [OK]``."""
    tone_tr, tone_en = TONE_LABELS[tone]
    return f"[{tone_tr}] / [{tone_en}]"


def summary_text(status: ComplianceStatus) -> str:
    """``10 denetimden 6 tanesi tamam / 6 of 10 checks OK``."""
    values = {"total": len(status.cards), "ok": status.counts.ok}
    return f"{SUMMARY[0].format(**values)} / {SUMMARY[1].format(**values)}"


def alert_text(rule: str, severity: str, opened: str) -> str:
    """``R20-backup, Yüksek / High, 28.10.2026 09:00:00``."""
    severity_tr, severity_en = SEVERITY_LABELS.get(severity, (severity, severity))
    return f"{rule}, {severity_tr} / {severity_en}, {opened}"


class _Builder:
    def __init__(self, status: ComplianceStatus, *, display_name: str, username: str) -> None:
        self.status = status
        self.exporter = EXPORTED_BY.format(display_name=display_name, username=username)
        self.s = report_styles()
        self.story: list[Any] = []

    def p(self, markup: str, style: str = "base") -> Any:
        return Paragraph(markup, self.s[style])

    def add(self, *flowables: Any) -> None:
        self.story.extend(flowables)

    def bilingual(self, text_tr: str, text_en: str) -> None:
        self.add(self.p(_x(text_tr)), self.p(_x(text_en), "small"), Spacer(1, 3))

    def facts(self, card: ComplianceCard) -> Any:
        rows = [
            [self.p(_x(fact.label_tr), "label"), self.p(_x(fact.value_tr))] for fact in card.facts
        ]
        table = Table(rows, colWidths=[LABEL_WIDTH, WIDTH - LABEL_WIDTH], hAlign="LEFT")
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), SANS),
                    ("GRID", (0, 0), (-1, -1), 0.4, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 2.5),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
                ]
            )
        )
        return table

    def build(self) -> list[Any]:
        self.header()
        for card in self.status.cards:
            self.card(card)
        return self.story

    def header(self) -> None:
        status = self.status
        self.add(
            self.p(_x(TITLE[0]), "title"),
            self.p(_x(TITLE[1]), "subtitle"),
            Spacer(1, 4),
            self.p(_x(SITE.format(name=status.site_name or NONE))),
            self.p(_x(GENERATED.format(time=format_datetime(status.generated_at)))),
            self.p(_x(self.exporter)),
            Spacer(1, 4),
            self.p(_x(summary_text(status)), "label"),
            Spacer(1, 4),
        )
        self.bilingual(status.notice_tr, status.notice_en)

    def card(self, card: ComplianceCard) -> None:
        self.add(
            CondPageBreak(40 * mm),
            self.p(_x(f"{card.number}. {card.title_tr} / {card.title_en}"), "h2"),
            self.p(_x(tone_text(card.tone)), TONE_STYLES[card.tone]),
            Spacer(1, 2),
        )
        self.bilingual(card.reason_tr, card.reason_en)
        if card.facts:
            self.add(self.facts(card), Spacer(1, 3))
        if card.verify_on_site_tr is not None and card.verify_on_site_en is not None:
            self.add(self.p(_x(VERIFY_ON_SITE), "h3"))
            self.bilingual(card.verify_on_site_tr, card.verify_on_site_en)
        if card.alerts:
            self.add(self.p(_x(ALERTS), "h3"))
            for alert in card.alerts:
                opened = format_datetime(alert.opened_at)
                self.add(self.p(_x(alert_text(alert.rule, alert.severity, opened)), "bullet"))
            self.add(Spacer(1, 3))
        self.add(self.p(_x(CHECKED.format(time=format_datetime(card.checked_at))), "small"))


class _Canvas(PagedCanvas):
    """Header (title and site) and footer (version, time, page n / N, the neutral note)."""

    def __init__(self, *args: Any, header: str, left: str, pages: list[int], **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._header = header
        self._left = left
        self._count = pages

    def counted(self, total: int) -> None:
        self._count.append(total)

    def decorate(self, total: int) -> None:
        width, height = A4
        page = f"Sayfa {self._pageNumber} / {total}"
        self.saveState()
        self.setStrokeColor(LINE)
        self.setLineWidth(0.5)
        self.setFont(SANS, 7.5)
        self.setFillColor(MUTED)
        self.drawString(MARGIN, height - 11 * mm, fit(self._header, 7.5, width - 2 * MARGIN))
        self.line(MARGIN, height - 12.5 * mm, width - MARGIN, height - 12.5 * mm)
        self.line(MARGIN, 15 * mm, width - MARGIN, 15 * mm)
        room = width - 2 * MARGIN - self.stringWidth(page, SANS, 7.5) - 6 * mm
        self.drawString(MARGIN, 11 * mm, fit(self._left, 7.5, room))
        self.drawRightString(width - MARGIN, 11 * mm, page)
        self.setFont(SANS, 6.3)
        self.drawString(MARGIN, 7.6 * mm, fit(FOOTER_NOTE_TR, 6.3, width - 2 * MARGIN))
        self.drawString(MARGIN, 5 * mm, fit(FOOTER_NOTE_EN, 6.3, width - 2 * MARGIN))
        self.restoreState()


def render_report(status: ComplianceStatus, *, display_name: str, username: str) -> RenderedReport:
    """The report of ``status``, exported by the signed-in account ``display_name (username)``
    (the same status and account give the same bytes)."""
    register_fonts()
    generated = format_datetime(status.generated_at)
    header = " · ".join(part for part in (f"{TITLE[0]} / {TITLE[1]}", status.site_name) if part)
    left = f"ARCILOGGER {__version__} · Oluşturulma {generated}"
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"{TITLE[0]} — {generated}",
        author=f"ARCILOGGER {__version__}",
        subject=TITLE[1],
        creator=f"ARCILOGGER {__version__}",
        invariant=1,
        lang="tr-TR",
        initialFontName=SANS,
    )
    pages: list[int] = []
    document.build(
        _Builder(status, display_name=display_name, username=username).build(),
        canvasmaker=lambda *args, **kwargs: _Canvas(
            *args, header=header, left=left, pages=pages, **kwargs
        ),
    )
    return RenderedReport(pdf=buffer.getvalue(), pages=pages[-1] if pages else 0)
