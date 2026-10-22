"""The usage report as an HTML e-mail (and as the stored ``.html`` file of a run).

Written for e-mail clients, not browsers (Outlook renders with Word, Gmail strips ``<style>``):

* layout with nested ``<table role="presentation">`` only — no ``div`` layout, no flexbox, no
  grid, no CSS classes; widths as ``width`` attributes, colours also as ``bgcolor``;
* every style inline; system fonts (Segoe UI, Arial) — no web fonts, no images, no scripts, no
  forms, nothing loaded from anywhere;
* one link only: the ARCILOGGER web interface (``alerts.web_base_url``), to the Raporlar page;
  site names in the tables are plain text, never links;
* Turkish first, an English summary line under the title and English sub-titles;
* every value escaped (:func:`html.escape`): names and domains come from the network.

Sections: summary, method note (every duration is an estimate), company totals with the top
sites and categories, departments, **every person** (names, owner decision 2026-10-20) with
their top sites and categories, notable changes, the alerts of the period, the sealing status
of the previous day, data quality notes; then the delivery note of the e-mail (attachments, or
the download link when they were too large) and the footer.
"""

from __future__ import annotations

import html
from collections.abc import Iterable, Mapping, Sequence
from typing import Any, Final

from arcilogger_worker.alerts.texts import SEVERITY_LABELS, STATE_LABELS
from arcilogger_worker.reports import texts as t

FONT: Final = "font-family:'Segoe UI',Arial,Helvetica,sans-serif;"
INK: Final = "#0f172a"
MUTED: Final = "#475569"
PRIMARY: Final = "#0f766e"
LINE: Final = "#cbd5e1"
SUBTLE: Final = "#f1f5f9"
DANGER: Final = "#b91c1c"
WARNING: Final = "#92400e"
NOTE_BG: Final = "#fffbeb"
WIDTH: Final = 680
#: Sites and categories of the company shown in the e-mail (the PDF and Excel show all).
TOP_COMPANY: Final = 10
#: Per person: sites and categories in the e-mail (spec 10.5: top 5 sites, top 3 categories).
TOP_PERSON_SITES: Final = 5
TOP_PERSON_CATEGORIES: Final = 3
SEVERITY_COLOURS: Final = {"critical": DANGER, "high": "#c2410c", "medium": WARNING}


def _e(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def _cell(text: str, *, align: str = "left", header: bool = False, extra: str = "") -> str:
    weight = "font-weight:bold;" if header else ""
    colour = MUTED if header else INK
    background = f' bgcolor="{SUBTLE}"' if header else ""
    if align != "left" and not header:
        extra = "white-space:nowrap;" + extra  # numbers and durations stay on one line
    return (
        f'<td align="{align}" valign="top"{background} style="{FONT}font-size:12px;'
        f'color:{colour};{weight}padding:5px 6px;border-bottom:1px solid {LINE};{extra}">'
        f"{text}</td>"
    )


def _table(
    headers: Sequence[str], rows: Iterable[Sequence[str]], aligns: Sequence[str] | None = None
) -> str:
    """A data table; ``headers`` and cell texts are already HTML (escaped)."""
    aligns = aligns or ["left"] * len(headers)
    head = "".join(
        _cell(header, align=align, header=True)
        for header, align in zip(headers, aligns, strict=True)
    )
    body = "".join(
        "<tr>"
        + "".join(_cell(value, align=align) for value, align in zip(row, aligns, strict=True))
        + "</tr>"
        for row in rows
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;width:100%;">'
        f"<tr>{head}</tr>{body}</table>"
    )


def _row(content: str, *, padding: str = "4px 24px 12px 24px", bgcolor: str = "#ffffff") -> str:
    return (
        f'<tr><td bgcolor="{bgcolor}" style="padding:{padding};background-color:{bgcolor};">'
        f"{content}</td></tr>"
    )


def _heading(text_tr: str, text_en: str) -> str:
    return _row(
        f'<div style="{FONT}font-size:16px;font-weight:bold;color:{PRIMARY};">{_e(text_tr)} '
        f'<span style="font-size:12px;font-weight:normal;color:{MUTED};">/ {_e(text_en)}</span>'
        "</div>",
        padding="18px 24px 6px 24px",
    )


def _paragraph(text: str, *, size: int = 13, colour: str = INK) -> str:
    return f'<p style="{FONT}font-size:{size}px;color:{colour};margin:0 0 6px 0;">{text}</p>'


def _kv(rows: Sequence[tuple[str, str, str]]) -> str:
    """Label (Turkish), label (English) and value (HTML) rows."""
    cells = "".join(
        "<tr>"
        + _cell(f'{_e(tr)}<br><span style="color:{MUTED};font-size:11px;">{_e(en)}</span>')
        + _cell(f"<b>{value}</b>", align="right")
        + "</tr>"
        for tr, en, value in rows
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;width:100%;">{cells}</table>'
    )


def _tiles(items: Sequence[tuple[str, str]]) -> str:
    """Four key figures side by side (a table row; e-mail clients keep the columns)."""
    width = 100 // len(items)
    cells = "".join(
        f'<td width="{width}%" align="center" valign="top" bgcolor="{SUBTLE}" '
        f'style="background-color:{SUBTLE};padding:10px 4px;border:4px solid #ffffff;">'
        f'<div style="{FONT}font-size:20px;font-weight:bold;color:{PRIMARY};">{value}</div>'
        f'<div style="{FONT}font-size:11px;color:{MUTED};">{label}</div></td>'
        for label, value in items
    )
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f"<tr>{cells}</tr></table>"
    )


def _note(text_tr: str, text_en: str, *, colour: str = WARNING) -> str:
    return (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0">'
        f'<tr><td bgcolor="{NOTE_BG}" style="background-color:{NOTE_BG};padding:8px 10px;'
        f'border-left:4px solid {colour};">'
        f'<div style="{FONT}font-size:12px;color:{colour};font-weight:bold;">{_e(text_tr)}</div>'
        f'<div style="{FONT}font-size:11px;color:{MUTED};">{_e(text_en)}</div>'
        "</td></tr></table>"
    )


def _muted(text: str) -> str:
    return f'<span style="color:{MUTED};font-size:11px;">{text}</span>'


# -- sections -------------------------------------------------------------------------------------


def summary_line(document: Mapping[str, Any], lang: int) -> str:
    """One sentence with the key figures (the English one sits under the Turkish title); the
    organisation name in brackets after the period, left out when none is set."""
    report, data = document["report"], document["data"]
    company = data.get("company") or {}
    period = t.period_text(data["period"]["from"], data["period"]["to"])
    site = t.site_name(report)
    if site:
        period = f"{period} ({site})"
    alerts = int((document.get("alerts") or {}).get("total") or 0)
    seal = t.seal_label(str((document.get("seal") or {}).get("status")), lang)
    values = (
        t.number(company.get("active_users"), lang),
        t.duration(company.get("active_seconds"), lang),
        t.volume(company.get("download_bytes"), lang),
        t.volume(company.get("upload_bytes"), lang),
        t.number(company.get("blocked"), lang),
    )
    if lang == 0:
        return (
            f"{t.kind_name(report['kind'])} {period}: {values[0]} aktif kullanıcı, {values[1]} "
            f"aktif süre (tahmini), {values[2]} indirme, {values[3]} yükleme, {values[4]} "
            f"engellenen deneme, {alerts} güvenlik uyarısı; önceki gün: {seal}."
        )
    return (
        f"{t.kind_name(report['kind'], 1)} {period}: {values[0]} active users, {values[1]} "
        f"active time (estimated), {values[2]} downloaded, {values[3]} uploaded, {values[4]} "
        f"blocked attempts, {alerts} security alerts; previous day: {seal}."
    )


def _company(data: Mapping[str, Any]) -> list[str]:
    company = data.get("company") or {}
    parts = [
        _heading("Şirket geneli", "Company totals"),
        _row(
            _tiles(
                [
                    ("Aktif kullanıcı", _e(t.number(company.get("active_users")))),
                    ("Aktif süre*", _e(t.duration(company.get("active_seconds")))),
                    ("İndirilen", _e(t.volume(company.get("download_bytes")))),
                    ("Engellenen deneme", _e(t.number(company.get("blocked")))),
                ]
            ),
            padding="4px 20px 8px 20px",
        ),
        _row(
            _kv(
                [
                    ("Aktif kullanıcı", "Active users", _e(t.number(company.get("active_users")))),
                    (
                        "Çevrimiçi süre (toplam)*",
                        "Online time (total)",
                        _e(t.duration(company.get("online_seconds"))),
                    ),
                    (
                        "Aktif süre (toplam)*",
                        "Active time (total)",
                        _e(t.duration(company.get("active_seconds"))),
                    ),
                    (
                        "Mesai dışı aktif süre*",
                        "Active time off hours",
                        _e(t.duration(company.get("off_hours_active_seconds"))),
                    ),
                    ("İndirilen", "Downloaded", _e(t.volume(company.get("download_bytes")))),
                    ("Yüklenen", "Uploaded", _e(t.volume(company.get("upload_bytes")))),
                    ("Oturum", "Sessions", _e(t.number(company.get("sessions")))),
                    (
                        "Engellenen deneme",
                        "Blocked attempts",
                        _e(t.number(company.get("blocked"))),
                    ),
                ]
            )
        ),
    ]
    sites = (company.get("top_sites") or [])[:TOP_COMPANY]
    if sites:
        parts += [
            _heading(f"En çok kullanılan {len(sites)} site", "Top sites"),
            _row(
                _table(
                    ["Site", "Kategori", "Süre*", "Pay", "İndirilen"],
                    (
                        [
                            _e(site.get("domain")),
                            _e(site.get("category") or t.UNKNOWN_CATEGORY),
                            _e(t.duration(site.get("seconds"))),
                            _e(t.share(site.get("share"))),
                            _e(t.volume(site.get("download_bytes"))),
                        ]
                        for site in sites
                    ),
                    ["left", "left", "right", "right", "right"],
                )
            ),
        ]
    categories = (company.get("top_categories") or [])[:TOP_COMPANY]
    if categories:
        parts += [
            _heading(f"En çok kullanılan {len(categories)} kategori", "Top categories"),
            _row(
                _table(
                    ["Kategori", "Süre*", "Pay"],
                    (
                        [
                            _e(item.get("category") or t.UNKNOWN_CATEGORY),
                            _e(t.duration(item.get("seconds"))),
                            _e(t.share(item.get("share"))),
                        ]
                        for item in categories
                    ),
                    ["left", "right", "right"],
                )
            ),
        ]
    return parts


def _departments(data: Mapping[str, Any]) -> list[str]:
    rows = data.get("departments") or []
    if not rows:
        return []
    return [
        _heading("Departmanlar", "Departments"),
        _row(
            _table(
                ["Departman", "Aktif kullanıcı", "Aktif süre*", "İndirilen", "Yüklenen", "Engel."],
                (
                    [
                        _e(t.department(row.get("name"))),
                        _e(t.number(row.get("active_users"))),
                        _e(t.duration(row.get("active_seconds"))),
                        _e(t.volume(row.get("download_bytes"))),
                        _e(t.volume(row.get("upload_bytes"))),
                        _e(t.number(row.get("blocked"))),
                    ]
                    for row in rows
                ),
                ["left", "right", "right", "right", "right", "right"],
            )
        ),
    ]


def _person_rows(person: Mapping[str, Any], *, dated: bool) -> str:
    sites = ", ".join(
        f"{_e(site.get('domain'))} {_e(t.duration(site.get('seconds')))}"
        for site in (person.get("top_sites") or [])[:TOP_PERSON_SITES]
    )
    categories = ", ".join(
        f"{_e(item.get('category') or t.UNKNOWN_CATEGORY)} {_e(t.duration(item.get('seconds')))}"
        for item in (person.get("top_categories") or [])[:TOP_PERSON_CATEGORIES]
    )
    main = [
        f"<b>{_e(person.get('label'))}</b><br>{_muted(_e(t.department(person.get('department'))))}",
        # Over several days with the dates: first and last activity on two lines.
        ("<br>" if dated else "-").join(
            _e(t.activity(person.get(field), dated=dated))
            for field in ("first_activity", "last_activity")
        ),
        _e(t.duration(person.get("online_seconds"))),
        _e(t.duration(person.get("active_seconds"))),
        f"{_e(t.volume(person.get('download_bytes')))}<br>"
        f"{_muted('↑ ' + _e(t.volume(person.get('upload_bytes'))))}",
        _e(t.number(person.get("sessions"))),
        _e(t.number(person.get("blocked"))),
        _e(t.duration(person.get("off_hours_active_seconds"))),
    ]
    aligns = ["left", "center", "right", "right", "right", "right", "right", "right"]
    first = "".join(
        _cell(value, align=align, extra="border-bottom:0;")
        for value, align in zip(main, aligns, strict=True)
    )
    detail = (
        f"{_muted('Siteler / Sites:')} {sites or '—'}<br>"
        f"{_muted('Kategoriler / Categories:')} {categories or '—'}"
    )
    second = (
        f'<td colspan="8" style="{FONT}font-size:11px;color:{INK};padding:0 6px 6px 6px;'
        f'border-bottom:1px solid {LINE};">{detail}</td>'
    )
    return f"<tr>{first}</tr><tr>{second}</tr>"


def _persons(data: Mapping[str, Any]) -> list[str]:
    if not data.get("person_level"):
        return [
            _heading("Kişiler", "Persons"),
            _row(
                _paragraph(
                    _e(
                        "Bu zamanlama kişi düzeyinde değil: yalnız şirket ve departman toplamları "
                        "var. / This schedule is not person-level."
                    ),
                    colour=MUTED,
                )
            ),
        ]
    persons = data.get("persons") or []
    headers = [
        "Kişi / departman",
        "İlk-son",
        "Çevrimiçi*",
        "Aktif*",
        "İndirme / yükleme",
        "Oturum",
        "Engel.",
        "Mesai dışı*",
    ]
    aligns = ["left", "center", "right", "right", "right", "right", "right", "right"]
    head = "".join(
        _cell(_e(header), align=align, header=True)
        for header, align in zip(headers, aligns, strict=True)
    )
    dated = t.several_days(data["period"]["from"], data["period"]["to"])
    body = "".join(_person_rows(person, dated=dated) for person in persons)
    pseudonymized = " (takma adlarla / pseudonymised)" if data.get("pseudonymized") else ""
    table = (
        '<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'style="border-collapse:collapse;width:100%;"><tr>{head}</tr>{body}</table>'
    )
    return [
        _heading(f"Kişiler ({len(persons)}){pseudonymized}", "Persons, all with activity"),
        _row(table if persons else _paragraph(_e("Etkinliği olan kişi yok. / No activity."))),
    ]


def _changes(data: Mapping[str, Any]) -> list[str]:
    changes = data.get("changes") or []
    if not changes:
        return []
    rows = (
        [
            f"{_e(item.get('text_tr'))}<br>{_muted(_e(item.get('text_en')))}",
            _e(t.percent(item.get("change_percent"))),
        ]
        for item in changes
    )
    return [
        _heading("Dikkat çeken değişiklikler", "Notable changes vs. the 7-day average"),
        _row(_table(["Değişiklik", "7 günlük ort."], rows, ["left", "right"])),
    ]


def _alerts(document: Mapping[str, Any]) -> list[str]:
    alerts = document.get("alerts") or {}
    listed = alerts.get("listed") or []
    total = int(alerts.get("total") or 0)
    parts = [_heading(f"Güvenlik uyarıları ({total})", "Security alerts of the period")]
    if not listed:
        parts.append(_row(_paragraph(_e("Bu dönemde uyarı yok. / No alerts in this period."))))
        return parts
    rows = []
    for item in listed:
        severity = str(item.get("severity"))
        colour = SEVERITY_COLOURS.get(severity, INK)
        label = SEVERITY_LABELS.get(severity, (severity, severity))[0]
        state = STATE_LABELS.get(str(item.get("state")), (item.get("state"), ""))[0]
        rows.append(
            [
                _e(t.when(item.get("time"))),
                f'<span style="color:{colour};font-weight:bold;">{_e(label)}</span>',
                f"<b>{_e(item.get('rule'))}</b> {_e(item.get('name_tr'))}<br>"
                f"{_muted(_e(item.get('text_tr')))}",
                _e(state),
            ]
        )
    parts.append(_row(_table(["Zaman", "Önem", "Kural", "Durum"], rows)))
    if total > len(listed):
        more = f"… {total - len(listed)} uyarı daha ARCILOGGER'da. / … more in ARCILOGGER."
        parts.append(_row(_paragraph(_e(more), size=12, colour=MUTED)))
    return parts


def seal_text(seal: Mapping[str, Any], lang: int) -> str:
    """``18.10.2026: Mühürlendi (20.10.2026 00:31, seri 1234)`` and the test-stamp warning."""
    text = f"{t.day_text(seal.get('day'))}: {t.seal_label(str(seal.get('status')), lang)}"
    facts = []
    if seal.get("sealed_at"):
        facts.append(t.when(seal["sealed_at"]))
    if seal.get("serial"):
        facts.append(("seri " if lang == 0 else "serial ") + str(seal["serial"]))
    if facts:
        text += f" ({', '.join(facts)})"
    if seal.get("test_stamp"):
        text += (
            " — test damgası (mock), yasal geçerli değil"
            if lang == 0
            else " — test stamp (mock), not legally valid"
        )
    return text


def _seal(document: Mapping[str, Any]) -> list[str]:
    seal = document.get("seal") or {}
    if not seal:
        return []
    ok = seal.get("status") in ("sealed", "skipped")
    colour = PRIMARY if ok and not seal.get("test_stamp") else DANGER
    return [
        _heading("Önceki günün mühürü", "Sealing of the previous day"),
        _row(
            _paragraph(
                f'<span style="color:{colour};font-weight:bold;">{_e(seal_text(seal, 0))}</span>'
                f"<br>{_muted(_e(seal_text(seal, 1)))}"
            )
        ),
    ]


def _quality(data: Mapping[str, Any]) -> list[str]:
    quality = data.get("data_quality") or {}
    notes_tr, notes_en = quality.get("notes_tr") or [], quality.get("notes_en") or []
    lines = [f"Hesaplanan son an / computed until: {t.when(data.get('computed_until'))}"]
    if quality.get("unattributed_seconds"):
        lines.append(
            "Kişiye bağlanamayan süre / not linked to a person: "
            f"{t.duration(quality['unattributed_seconds'])}"
        )
    lines += [*notes_tr, *notes_en]
    return [
        _heading("Veri kalitesi", "Data quality"),
        _row("".join(_paragraph(_e(line), size=12, colour=MUTED) for line in lines)),
    ]


# -- the document ---------------------------------------------------------------------------------


def render_html(document: Mapping[str, Any], *, delivery: Sequence[str] = ()) -> str:
    """The report as one HTML document; ``delivery``: lines about the attachments or the
    download link (e-mail only, plain text)."""
    report, data = document["report"], document["data"]
    title_tr = f"{t.PRODUCT} kullanım raporu — {report['name']}"
    period = t.period_text(data["period"]["from"], data["period"]["to"])
    web = str(report.get("web_url") or "")
    rows = [
        _row(
            f'<div style="{FONT}font-size:12px;color:#ccfbf1;letter-spacing:1px;">'
            f"{_e(t.joined(t.PRODUCT, t.site_name(report)))}</div>"
            f'<div style="{FONT}font-size:22px;font-weight:bold;color:#ffffff;">'
            f"{_e(report['name'])}</div>"
            f'<div style="{FONT}font-size:13px;color:#ccfbf1;">{_e(t.kind_name(report["kind"]))}'
            f" · {_e(period)} · {_e(t.kind_name(report['kind'], 1))}</div>",
            padding="18px 24px",
            bgcolor=PRIMARY,
        ),
        _row(
            _paragraph(_e(summary_line(document, 0)))
            + _paragraph(_e(summary_line(document, 1)), size=12, colour=MUTED),
            padding="16px 24px 4px 24px",
        ),
        _row(_note("* " + t.METHOD_NOTE_TR, t.METHOD_NOTE_EN)),
    ]
    if report.get("preview"):
        rows.append(
            _row(_note("Önizleme: kaydedilmedi, gönderilmedi.", "Preview: not stored, not sent."))
        )
    rows += _company(data)
    rows += _departments(data)
    rows += _persons(data)
    rows += _changes(data)
    rows += _alerts(document)
    rows += _seal(document)
    rows += _quality(data)
    if delivery:
        rows.append(_heading("Ekler", "Attachments"))
        rows.append(_row("".join(_paragraph(_e(line), size=12) for line in delivery)))
    footer = (
        f"Oluşturma / generated: {t.when(report.get('generated_at'))} · {t.PRODUCT} "
        f"{report.get('version', '')}"
    )
    link = ""
    if web:
        target = _e(f"{web}/reports")
        link = f'<br>Raporlar / Reports: <a href="{target}" style="color:{PRIMARY};">{target}</a>'
    rows.append(
        _row(
            f'<div style="{FONT}font-size:11px;color:{MUTED};">{_e(footer)}{link}<br>'
            f"{_e('Bu e-posta ARCILOGGER tarafından otomatik oluşturuldu. / Sent automatically.')}"
            "</div>",
            padding="14px 24px 18px 24px",
            bgcolor=SUBTLE,
        )
    )
    preheader = (
        '<div style="display:none;max-height:0;overflow:hidden;mso-hide:all;'
        f'font-size:1px;color:{SUBTLE};">{_e(summary_line(document, 0))}</div>'
    )
    return (
        '<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        f"<title>{_e(title_tr)}</title></head>"
        f'<body style="margin:0;padding:0;background-color:{SUBTLE};">{preheader}'
        f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="{SUBTLE}" style="background-color:{SUBTLE};"><tr>'
        '<td align="center" style="padding:16px 8px;">'
        f'<table role="presentation" width="{WIDTH}" cellpadding="0" cellspacing="0" border="0" '
        f'bgcolor="#ffffff" style="width:{WIDTH}px;max-width:100%;background-color:#ffffff;'
        f'border:1px solid {LINE};">' + "".join(rows) + "</table></td></tr></table></body></html>\n"
    )
