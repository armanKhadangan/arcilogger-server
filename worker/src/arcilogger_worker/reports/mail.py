"""The e-mail of a report run: subject, plain text, HTML body, attachments (ADR-0012).

Sent through the alert e-mail's SMTP client (:class:`arcilogger_worker.alerts.mail.SmtpSender`,
``[smtp]`` and the secret ``smtp.password``) — one SMTP implementation for ARCILOGGER. **One
e-mail per recipient** (``To`` is that recipient only; no ``Cc``/``Bcc``): nobody sees who else
receives the report, a refusing address affects only itself and every recipient has its own
delivery state, as with the alert e-mail (ADR-0010).

The HTML body is the report itself (:mod:`.html`) when the schedule's ``email_html`` format is
on, otherwise a short summary; the PDF and the workbook are attached unless together they are
larger than ``reports.max_attachment_mb`` — then the e-mail says so and links to the download on
the Raporlar page instead (the only link of the e-mail: the ARCILOGGER web address).
"""

from __future__ import annotations

import html as markup
from collections.abc import Mapping, Sequence
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime as rfc2822
from email.utils import make_msgid
from typing import Any, Final

from arcilogger_core.fmt import format_bytes
from arcilogger_worker.reports import texts as t
from arcilogger_worker.reports.html import FONT, MUTED, render_html, summary_line
from arcilogger_worker.reports.store import MEDIA_TYPES

MIB: Final = 1024 * 1024


def subject(document: Mapping[str, Any]) -> str:
    """``[ARCILOGGER] Tam gün raporu — 19.10.2026 (<kuruluş adı>)``; without an organisation
    name ``[ARCILOGGER] Tam gün raporu — 19.10.2026``."""
    report, data = document["report"], document["data"]
    period = t.period_text(data["period"]["from"], data["period"]["to"])
    site = t.site_name(report)
    return f"[{t.PRODUCT}] {report['name']} — {period}" + (f" ({site})" if site else "")


def download_link(web_url: str, run_id: int) -> str:
    """The run on the Raporlar page of the web interface."""
    return f"{web_url}/reports?run={int(run_id)}"


def delivery_lines(
    attachments: Sequence[Mapping[str, Any]],
    *,
    omitted: bool,
    limit_mb: int,
    link: str,
) -> list[str]:
    """What the e-mail says about its files (Turkish, then English)."""
    total = sum(int(item.get("size") or 0) for item in attachments)
    names = ", ".join(
        f"{item.get('name')} ({format_bytes(int(item.get('size') or 0))})" for item in attachments
    )
    if not attachments:
        return [f"Rapor ARCILOGGER'da / The report in ARCILOGGER: {link}"]
    if omitted:
        return [
            f"Ekler e-postaya eklenmedi: toplam {format_bytes(total)}, sınır {limit_mb} MB "
            f"(reports.max_attachment_mb). Raporlar sayfasından indirin: {link}",
            f"The files were not attached: {format_bytes(total, 'en')} in total, limit "
            f"{limit_mb} MB. Download them on the Reports page: {link}",
        ]
    return [
        f"Ekler / Attachments: {names}",
        f"Raporlar sayfasında da / Also on the Reports page: {link}",
    ]


def render_text(document: Mapping[str, Any], delivery: Sequence[str]) -> str:
    """The plain-text part: summary in both languages, method note, files, link."""
    report = document["report"]
    title = f"{t.PRODUCT} kullanım raporu — {report['name']}"
    lines = [
        title,
        "=" * len(title),
        "",
        summary_line(document, 0),
        "",
        summary_line(document, 1),
        "",
        t.METHOD_NOTE_TR,
        t.METHOD_NOTE_EN,
        "",
        *delivery,
        "",
        "Bu e-posta ARCILOGGER tarafından otomatik oluşturuldu. / Sent automatically.",
    ]
    return "\n".join(lines) + "\n"


def render_short_html(document: Mapping[str, Any], delivery: Sequence[str]) -> str:
    """The HTML body when the report itself is not the body (``email_html`` off)."""
    paragraphs = [summary_line(document, 0), summary_line(document, 1), t.METHOD_NOTE_TR]
    paragraphs += [t.METHOD_NOTE_EN, *delivery]
    body = "".join(
        f'<p style="{FONT}font-size:13px;margin:0 0 8px 0;">{markup.escape(text)}</p>'
        for text in paragraphs
    )
    title = markup.escape(f"{t.PRODUCT} kullanım raporu — {document['report']['name']}")
    return (
        '<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8">'
        f"<title>{title}</title></head>"
        f'<body style="margin:0;padding:16px;"><p style="{FONT}font-size:16px;'
        f'font-weight:bold;margin:0 0 12px 0;">{title}</p>{body}'
        f'<p style="{FONT}font-size:11px;color:{MUTED};">Bu e-posta ARCILOGGER tarafından '
        "otomatik oluşturuldu. / Sent automatically.</p></body></html>\n"
    )


def render_mail(
    document: Mapping[str, Any],
    *,
    email_html: bool,
    attachments: Sequence[Mapping[str, Any]],
    omitted: bool,
    limit_mb: int,
    run_id: int,
) -> tuple[str, str]:
    """``(html, text)`` of the run's e-mail."""
    link = download_link(str(document["report"]["web_url"]), run_id)
    delivery = delivery_lines(attachments, omitted=omitted, limit_mb=limit_mb, link=link)
    body = (
        render_html(document, delivery=delivery)
        if email_html
        else render_short_html(document, delivery)
    )
    return body, render_text(document, delivery)


def build_message(
    *,
    subject_line: str,
    sender: str,
    recipient: str,
    html: str,
    text: str,
    attachments: Sequence[tuple[str, str, bytes]],
    now: datetime,
) -> EmailMessage:
    """One e-mail to one recipient; ``attachments``: ``(name, format, bytes)``."""
    message = EmailMessage()
    message["Subject"] = subject_line
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = rfc2822(now)
    message["Message-ID"] = make_msgid(
        idstring="arcilogger-report", domain=sender.rpartition("@")[2] or "localhost"
    )
    message["Auto-Submitted"] = "auto-generated"
    # Quoted-printable: 7-bit safe for any server, Turkish letters included.
    message.set_content(text, cte="quoted-printable")
    message.add_alternative(html, subtype="html", cte="quoted-printable")
    for name, kind, data in attachments:
        maintype, _, rest = MEDIA_TYPES[kind].partition("/")
        message.add_attachment(data, maintype=maintype, subtype=rest.split(";")[0], filename=name)
    return message
