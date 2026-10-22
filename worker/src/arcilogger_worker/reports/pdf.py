"""The usage report as a PDF (reportlab, bundled DejaVu fonts — Turkish letters, searchable).

Same building blocks as the official 5651 report (:mod:`arcilogger_worker.pdf`): invariant mode,
so the same document gives the same bytes; every page carries "Sayfa n / N", the report, the
generation time and the method note (every duration is an estimate).

1. **Cover**: report details, the method note, company totals, the top sites and categories,
   the department table, notable changes, the alerts of the period, the sealing status of the
   previous day, data quality notes.
2. **One section per department**, each on a new page: its totals and the table of its persons.
3. **Per person**, inside its department's section: the totals (first/last activity, online,
   active and off-hours time, volumes, sessions), the top 20 sites with time and category, the
   categories and the blocked attempts (at most :data:`MAX_BLOCKED_PER_PERSON`; the workbook
   lists every one the report holds). The detail rows are matched to a person by its subject key,
   never by the name shown: two employees may share a name, a device may carry one.

Without person-level data (a schedule with ``person_level`` false) the report is the cover only.
"""

from __future__ import annotations

import hashlib
import io
from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final
from xml.sax.saxutils import escape

from reportlab.lib.pagesizes import A4  # type: ignore[import-untyped]
from reportlab.lib.units import mm  # type: ignore[import-untyped]
from reportlab.platypus import (  # type: ignore[import-untyped]
    CondPageBreak,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from arcilogger_worker.alerts.texts import SEVERITY_LABELS, STATE_LABELS
from arcilogger_worker.pdf.fonts import SANS, SANS_BOLD, register_fonts
from arcilogger_worker.pdf.styles import (
    LINE,
    MARGIN,
    MUTED,
    SUBTLE,
    PagedCanvas,
    fit,
    report_styles,
)
from arcilogger_worker.reports import texts as t
from arcilogger_worker.reports.html import seal_text, summary_line

WIDTH: Final = A4[0] - 2 * MARGIN
#: Blocked attempts listed per person in the PDF (the workbook has all of them).
MAX_BLOCKED_PER_PERSON: Final = 100
#: Sites per person (contract: top 20 with time).
TOP_SITES: Final = 20


@dataclass(frozen=True)
class RenderedPdf:
    pdf: bytes
    pages: int

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.pdf).hexdigest()


def _x(value: Any) -> str:
    """Escape for reportlab's paragraph markup."""
    return escape("" if value is None else str(value))


def _subject(row: Mapping[str, Any]) -> str:
    """Whose row it is: the subject key (``person:12``, a pseudonym's token), else the label."""
    return str(row.get("subject") or row.get("label"))


class _Builder:
    def __init__(self, document: Mapping[str, Any]) -> None:
        self.document = document
        self.report = document["report"]
        self.data = document["data"]
        self.s = report_styles()
        self.s["cell"] = self.s["base"].clone("cell", fontSize=7.4, leading=9.2)
        self.s["cellbold"] = self.s["cell"].clone("cellbold", fontName=SANS_BOLD)
        self.story: list[Any] = []
        # Per person, indexed once: a monthly report has every person-site pair and every blocked
        # attempt of the month.
        self.categories: dict[str, dict[str, str | None]] = defaultdict(dict)
        for row in self.data.get("person_sites") or []:
            self.categories[_subject(row)][str(row.get("domain"))] = row.get("category")
        self.blocked: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
        for row in self.data.get("blocked") or []:
            self.blocked[_subject(row)].append(row)
        period = self.data["period"]
        #: Over several days the tables date the first and last activity.
        self.dated = t.several_days(period["from"], period["to"])

    def p(self, markup: str, style: str = "base") -> Any:
        return Paragraph(markup, self.s[style])

    def add(self, *flowables: Any) -> None:
        self.story.extend(flowables)

    def heading(self, text_tr: str, text_en: str, style: str = "h2") -> None:
        self.add(
            CondPageBreak(30 * mm),
            self.p(f"{_x(text_tr)} <font size=8 color='#475569'>/ {_x(text_en)}</font>", style),
        )

    def table(
        self,
        headers: Sequence[str],
        rows: Sequence[Sequence[Any]],
        widths: Sequence[float],
        *,
        right: Sequence[int] = (),
    ) -> Any:
        """A table with a repeated header row; plain strings use the embedded font."""
        cells = [[self.p(_x(h), "cellbold") for h in headers]]
        for row in rows:
            cells.append(
                [
                    value if not isinstance(value, str) else self.p(_x(value), "cell")
                    for value in row
                ]
            )
        commands: list[tuple[Any, ...]] = [
            ("FONTNAME", (0, 0), (-1, -1), SANS),
            ("FONTSIZE", (0, 0), (-1, -1), 7.4),
            ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
            ("BACKGROUND", (0, 0), (-1, 0), SUBTLE),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 3),
            ("RIGHTPADDING", (0, 0), (-1, -1), 3),
            ("TOPPADDING", (0, 0), (-1, -1), 2),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
        ]
        for column in right:
            commands.append(("ALIGN", (column, 0), (column, -1), "RIGHT"))
        scale = WIDTH / sum(widths)
        table = Table(
            cells,
            colWidths=[width * scale for width in widths],
            repeatRows=1,
            hAlign="LEFT",
            splitInRow=1,
        )
        table.setStyle(TableStyle(commands))
        return table

    def kv(self, rows: Sequence[tuple[str, str, str]]) -> Any:
        """Turkish label, English label, value."""
        data = [
            [
                self.p(f"{_x(tr)} <font size=6.6 color='#475569'>/ {_x(en)}</font>", "cell"),
                self.p(_x(value), "cellbold"),
            ]
            for tr, en, value in rows
        ]
        table = Table(data, colWidths=[WIDTH * 0.62, WIDTH * 0.38], hAlign="LEFT", splitInRow=1)
        table.setStyle(
            TableStyle(
                [
                    ("FONTNAME", (0, 0), (-1, -1), SANS),
                    ("LINEBELOW", (0, 0), (-1, -1), 0.4, LINE),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("TOPPADDING", (0, 0), (-1, -1), 2),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ]
            )
        )
        return table

    # -- cover ------------------------------------------------------------------------------------

    def cover(self) -> None:
        report, data = self.report, self.data
        self.add(
            self.p(_x(f"{t.PRODUCT} kullanım raporu — {report['name']}"), "title"),
            self.p(
                _x(
                    t.joined(
                        t.kind_name(report["kind"]),
                        t.period_text(data["period"]["from"], data["period"]["to"]),
                        t.site_name(report),
                    )
                    + f" / {t.kind_name(report['kind'], 1)}"
                ),
                "subtitle",
            ),
            Spacer(1, 6),
            self.p(_x(summary_line(self.document, 0))),
            self.p(_x(summary_line(self.document, 1)), "small"),
            Spacer(1, 4),
            self.p(_x(t.METHOD_NOTE_TR), "note"),
            self.p(_x(t.METHOD_NOTE_EN), "small"),
            Spacer(1, 4),
        )
        details = [
            ("Rapor", "Report", report["name"]),
            ("Tür", "Kind", t.kind_name(report["kind"])),
            ("Dönem", "Period", t.period_text(data["period"]["from"], data["period"]["to"])),
            ("Oluşturma zamanı", "Generated", t.when(report.get("generated_at"))),
            ("Hesaplanan son an", "Computed until", t.when(data.get("computed_until"))),
            ("Kişi düzeyinde", "Person-level", "evet" if data.get("person_level") else "hayır"),
            ("Takma adlarla", "Pseudonymised", "evet" if data.get("pseudonymized") else "hayır"),
        ]
        if report.get("run_id") is not None:
            details.append(("Çalıştırma no", "Run", str(report["run_id"])))
        if report.get("preview"):
            details.append(("Önizleme", "Preview", "kaydedilmedi, gönderilmedi"))
        self.add(self.kv(details))
        self.company()
        self.departments_table()
        self.changes()
        self.alerts()
        self.seal()
        self.quality()

    def company(self) -> None:
        company = self.data.get("company") or {}
        self.heading("Şirket geneli", "Company totals")
        self.add(
            self.kv(
                [
                    ("Aktif kullanıcı", "Active users", t.number(company.get("active_users"))),
                    (
                        "Çevrimiçi süre (toplam, tahmini)",
                        "Online time",
                        t.duration(company.get("online_seconds")),
                    ),
                    (
                        "Aktif süre (toplam, tahmini)",
                        "Active time",
                        t.duration(company.get("active_seconds")),
                    ),
                    (
                        "Mesai dışı aktif süre (tahmini)",
                        "Off-hours active time",
                        t.duration(company.get("off_hours_active_seconds")),
                    ),
                    ("İndirilen", "Downloaded", t.volume(company.get("download_bytes"))),
                    ("Yüklenen", "Uploaded", t.volume(company.get("upload_bytes"))),
                    ("Oturum", "Sessions", t.number(company.get("sessions"))),
                    ("Engellenen deneme", "Blocked attempts", t.number(company.get("blocked"))),
                ]
            )
        )
        sites = company.get("top_sites") or []
        if sites:
            self.heading("En çok kullanılan siteler", "Top sites", "h3")
            self.add(
                self.table(
                    ["Site", "Kategori", "Süre", "Pay", "İndirilen", "Yüklenen"],
                    [
                        [
                            str(site.get("domain")),
                            site.get("category") or t.UNKNOWN_CATEGORY,
                            t.duration(site.get("seconds")),
                            t.share(site.get("share")),
                            t.volume(site.get("download_bytes")),
                            t.volume(site.get("upload_bytes")),
                        ]
                        for site in sites
                    ],
                    [48, 40, 20, 12, 20, 20],
                    right=(2, 3, 4, 5),
                )
            )
        categories = company.get("top_categories") or []
        if categories:
            self.heading("En çok kullanılan kategoriler", "Top categories", "h3")
            self.add(
                self.table(
                    ["Kategori", "Süre", "Pay"],
                    [
                        [
                            item.get("category") or t.UNKNOWN_CATEGORY,
                            t.duration(item.get("seconds")),
                            t.share(item.get("share")),
                        ]
                        for item in categories
                    ],
                    [90, 30, 20],
                    right=(1, 2),
                )
            )

    def departments_table(self) -> None:
        rows = self.data.get("departments") or []
        if not rows:
            return
        self.heading("Departmanlar", "Departments")
        self.add(
            self.table(
                ["Departman", "Aktif kullanıcı", "Aktif süre", "İndirilen", "Yüklenen", "Engel."],
                [
                    [
                        t.department(row.get("name")),
                        t.number(row.get("active_users")),
                        t.duration(row.get("active_seconds")),
                        t.volume(row.get("download_bytes")),
                        t.volume(row.get("upload_bytes")),
                        t.number(row.get("blocked")),
                    ]
                    for row in rows
                ],
                [50, 20, 22, 22, 22, 14],
                right=(1, 2, 3, 4, 5),
            )
        )

    def changes(self) -> None:
        changes = self.data.get("changes") or []
        if not changes:
            return
        self.heading("Dikkat çeken değişiklikler", "Notable changes vs. the 7-day average")
        self.add(
            self.table(
                ["Değişiklik", "Fark"],
                [
                    [
                        f"{item.get('text_tr')} / {item.get('text_en')}",
                        t.percent(item.get("change_percent")),
                    ]
                    for item in changes
                ],
                [150, 24],
                right=(1,),
            )
        )

    def alerts(self) -> None:
        alerts = self.document.get("alerts") or {}
        listed = alerts.get("listed") or []
        total = int(alerts.get("total") or 0)
        self.heading(f"Güvenlik uyarıları ({total})", "Security alerts of the period")
        if not listed:
            self.add(self.p(_x("Bu dönemde uyarı yok. / No alerts in this period."), "small"))
            return
        rows = []
        for item in listed:
            severity = str(item.get("severity"))
            state = str(item.get("state"))
            rows.append(
                [
                    t.when(item.get("time")),
                    SEVERITY_LABELS.get(severity, (severity, severity))[0],
                    f"{item.get('rule')} — {item.get('name_tr')}: {item.get('text_tr')}",
                    STATE_LABELS.get(state, (state, state))[0],
                ]
            )
        self.add(self.table(["Zaman", "Önem", "Uyarı", "Durum"], rows, [26, 16, 112, 20]))
        if total > len(listed):
            self.add(self.p(_x(f"… {total - len(listed)} uyarı daha ARCILOGGER'da."), "small"))

    def seal(self) -> None:
        seal = self.document.get("seal") or {}
        if not seal:
            return
        self.heading("Önceki günün mühürü", "Sealing of the previous day")
        ok = seal.get("status") in ("sealed", "skipped") and not seal.get("test_stamp")
        self.add(self.p(_x(seal_text(seal, 0)), "base" if ok else "warn"))
        self.add(self.p(_x(seal_text(seal, 1)), "small"))

    def quality(self) -> None:
        quality = self.data.get("data_quality") or {}
        self.heading("Veri kalitesi", "Data quality")
        if quality.get("unattributed_seconds"):
            self.add(
                self.p(
                    _x(
                        "Kişiye bağlanamayan süre (tahmini) / not linked to a person: "
                        f"{t.duration(quality['unattributed_seconds'])}"
                    )
                )
            )
        for note in [*(quality.get("notes_tr") or []), *(quality.get("notes_en") or [])]:
            self.add(self.p(_x(note), "small"))

    # -- departments and persons ------------------------------------------------------------------

    def persons_by_department(self) -> list[tuple[str | None, list[Mapping[str, Any]]]]:
        groups: dict[str | None, list[Mapping[str, Any]]] = defaultdict(list)
        for person in self.data.get("persons") or []:
            groups[person.get("department") or None].append(person)
        order = [row.get("name") for row in self.data.get("departments") or []]
        named = sorted(
            (name for name in groups if name is not None),
            key=lambda name: (order.index(name) if name in order else len(order), name),
        )
        result: list[tuple[str | None, list[Mapping[str, Any]]]] = [
            (name, groups[name]) for name in named
        ]
        if None in groups:
            result.append((None, groups[None]))
        return result

    def department_section(self, name: str | None, persons: list[Mapping[str, Any]]) -> None:
        label = t.department(name)
        self.add(PageBreak())
        self.add(self.p(_x(f"Departman: {label}"), "title"))
        group = name or t.NO_DEPARTMENT[0]  # the engine's name of the group without one
        totals = next(
            (row for row in self.data.get("departments") or [] if row.get("name") == group),
            None,
        )
        if totals is not None:
            self.add(
                self.kv(
                    [
                        ("Aktif kullanıcı", "Active users", t.number(totals.get("active_users"))),
                        (
                            "Aktif süre (tahmini)",
                            "Active time",
                            t.duration(totals.get("active_seconds")),
                        ),
                        ("İndirilen", "Downloaded", t.volume(totals.get("download_bytes"))),
                        ("Yüklenen", "Uploaded", t.volume(totals.get("upload_bytes"))),
                        ("Engellenen deneme", "Blocked attempts", t.number(totals.get("blocked"))),
                    ]
                )
            )
            if name is None:
                self.add(
                    self.p(
                        _x(
                            "Toplamlar departmanı olmayan kişileri, cihazları ve IP adreslerini "
                            "birlikte sayar. / The totals count the persons, devices and IP "
                            "addresses without a department together."
                        ),
                        "small",
                    )
                )
        self.heading(f"Kişiler ({len(persons)})", "Persons")
        self.add(
            self.table(
                [
                    "Kişi",
                    "İlk",
                    "Son",
                    "Çevrimiçi",
                    "Aktif",
                    "İndirme",
                    "Yükleme",
                    "Oturum",
                    "Engel.",
                    "Mesai dışı",
                ],
                [
                    [
                        str(person.get("label")),
                        t.activity(person.get("first_activity"), dated=self.dated),
                        t.activity(person.get("last_activity"), dated=self.dated),
                        t.duration(person.get("online_seconds")),
                        t.duration(person.get("active_seconds")),
                        t.volume(person.get("download_bytes")),
                        t.volume(person.get("upload_bytes")),
                        t.number(person.get("sessions")),
                        t.number(person.get("blocked")),
                        t.duration(person.get("off_hours_active_seconds")),
                    ]
                    for person in persons
                ],
                # Dated first and last activity need wider columns (a date is not split).
                [27, 16, 16, 17, 15, 13, 14, 13, 11, 16]
                if self.dated
                else [34, 10, 10, 20, 18, 16, 16, 14, 12, 18],
                right=(3, 4, 5, 6, 7, 8, 9),
            )
        )
        for person in persons:
            self.person(person)

    def person(self, person: Mapping[str, Any]) -> None:
        label = str(person.get("label"))
        self.add(CondPageBreak(60 * mm))
        self.add(
            self.p(
                f"{_x(label)} <font size=8 color='#475569'>· "
                f"{_x(t.department(person.get('department')))}</font>",
                "h2",
            )
        )
        who = _subject(person)
        self.add(
            KeepTogether(
                self.kv(
                    [
                        (
                            "İlk / son etkinlik",
                            "First / last activity",
                            t.activity_span(
                                person.get("first_activity"), person.get("last_activity")
                            ),
                        ),
                        (
                            "Çevrimiçi süre (tahmini)",
                            "Online time",
                            t.duration(person.get("online_seconds")),
                        ),
                        (
                            "Aktif süre (tahmini)",
                            "Active time",
                            t.duration(person.get("active_seconds")),
                        ),
                        (
                            "Mesai dışı aktif süre",
                            "Off-hours active time",
                            t.duration(person.get("off_hours_active_seconds")),
                        ),
                        (
                            "İndirilen / yüklenen",
                            "Downloaded / uploaded",
                            f"{t.volume(person.get('download_bytes'))} / "
                            f"{t.volume(person.get('upload_bytes'))}",
                        ),
                        ("Oturum", "Sessions", t.number(person.get("sessions"))),
                        ("Engellenen deneme", "Blocked attempts", t.number(person.get("blocked"))),
                    ]
                )
            )
        )
        categories_of = self.categories.get(who, {})
        sites = (person.get("top_sites") or [])[:TOP_SITES]
        if sites:
            self.add(self.p(_x("En çok kullanılan siteler / Top sites"), "h3"))
            self.add(
                self.table(
                    ["Site", "Kategori", "Süre"],
                    [
                        [
                            str(site.get("domain")),
                            categories_of.get(str(site.get("domain"))) or t.UNKNOWN_CATEGORY,
                            t.duration(site.get("seconds")),
                        ]
                        for site in sites
                    ],
                    [80, 60, 30],
                    right=(2,),
                )
            )
        categories = person.get("top_categories") or []
        if categories:
            self.add(self.p(_x("Kategoriler / Categories"), "h3"))
            self.add(
                self.table(
                    ["Kategori", "Süre"],
                    [
                        [
                            item.get("category") or t.UNKNOWN_CATEGORY,
                            t.duration(item.get("seconds")),
                        ]
                        for item in categories
                    ],
                    [120, 50],
                    right=(1,),
                )
            )
        blocked = self.blocked.get(who, [])
        # The count can exceed the list: a report lists at most 50 000 attempts, and of a day over
        # usage.blocked_per_day only the stored ones (the data quality notes say so).
        count = person.get("blocked")
        total = count if isinstance(count, int) and count > len(blocked) else len(blocked)
        if total:
            self.add(self.p(_x(f"Engellenen denemeler ({total}) / Blocked attempts"), "h3"))
        if blocked:
            self.add(
                self.table(
                    ["Zaman", "Site", "Kategori", "Kaynak"],
                    [
                        [
                            t.when(row.get("time")),
                            str(row.get("domain") or "—"),
                            row.get("category") or t.UNKNOWN_CATEGORY,
                            t.SOURCE_LABELS.get(str(row.get("source")), (row.get("source"),))[0]
                            or "—",
                        ]
                        for row in blocked[:MAX_BLOCKED_PER_PERSON]
                    ],
                    [30, 60, 50, 30],
                )
            )
            if len(blocked) > MAX_BLOCKED_PER_PERSON:
                rest = len(blocked) - MAX_BLOCKED_PER_PERSON
                self.add(self.p(_x(f"… {rest} deneme daha (Excel: Engellenenler)."), "small"))
        if total > len(blocked):
            self.add(
                self.p(
                    _x(
                        f"… {total - len(blocked)} deneme daha sayıldı ama listelenmedi (kayıt "
                        "sınırı). / More counted but not listed (row limit)."
                    ),
                    "small",
                )
            )

    def build(self) -> list[Any]:
        self.cover()
        if self.data.get("person_level"):
            for name, persons in self.persons_by_department():
                self.department_section(name, persons)
        return self.story


class _Canvas(PagedCanvas):
    """Header (report and period) and footer (version, site, time, page n / N, method note)."""

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
        self.drawString(MARGIN, 7.6 * mm, fit(t.METHOD_NOTE_TR, 6.3, width - 2 * MARGIN))
        self.drawString(MARGIN, 5 * mm, fit(t.METHOD_NOTE_EN, 6.3, width - 2 * MARGIN))
        self.restoreState()


def render_pdf(document: Mapping[str, Any]) -> RenderedPdf:
    """The PDF of ``document`` (same document → same bytes)."""
    register_fonts()
    report, data = document["report"], document["data"]
    period = t.period_text(data["period"]["from"], data["period"]["to"])
    header = f"{t.PRODUCT} kullanım raporu · {report['name']} · {period}"
    left = t.joined(
        f"{t.PRODUCT} {report.get('version', '')}",
        t.site_name(report),
        f"Oluşturma {t.when(report.get('generated_at'))}",
    )
    buffer = io.BytesIO()
    pdf = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=18 * mm,
        bottomMargin=20 * mm,
        title=f"{t.PRODUCT} kullanım raporu — {report['name']} — {period}",
        author=f"{t.PRODUCT} {report.get('version', '')}",
        subject=t.kind_name(report["kind"]),
        creator=f"{t.PRODUCT} {report.get('version', '')}",
        invariant=1,
        lang="tr-TR",
        initialFontName=SANS,
    )
    pages: list[int] = []
    pdf.build(
        _Builder(document).build(),
        canvasmaker=lambda *args, **kwargs: _Canvas(
            *args, header=header, left=left, pages=pages, **kwargs
        ),
    )
    return RenderedPdf(pdf=buffer.getvalue(), pages=pages[-1] if pages else 0)
