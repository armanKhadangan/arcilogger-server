"""Alert e-mails: the message (plain text + simple HTML, Turkish first) and SMTP (stdlib).

Content (ADR-0010): for each alert the rule id, the severity, the time (Europe/Istanbul), the
source name when there is one, one short sentence and one line on what to do — all from
:mod:`.texts`, never an alert's own title or details — and a link to the ARCILOGGER web
interface (the only link; no images, no external resources).

SMTP (``[smtp]``): ``starttls`` (port 587) or ``ssl`` (implicit TLS, port 465) verify the server
certificate against the system trust store or ``ca_file`` — always, there is no switch to turn
it off. ``none`` is plain SMTP for a relay on this server. The password (secret
``smtp.password``) is used for ``AUTH`` only and never appears in an error text.
"""

from __future__ import annotations

import html
import smtplib
import ssl
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime as rfc2822
from email.utils import make_msgid
from typing import Any, Final

from arcilogger_core.config import SmtpConfig
from arcilogger_core.fmt import format_datetime
from arcilogger_core.timeutil import SITE_TZ_NAME
from arcilogger_worker.alerts.store import SEVERITY_RANK
from arcilogger_worker.alerts.texts import (
    SEVERITY_LABELS,
    STATE_LABELS,
    describe,
    rule_text,
    source_name,
)

PRODUCT: Final = "ARCILOGGER"
#: Most alerts listed one by one in an e-mail (the rest are counted).
MAX_LISTED: Final = 50
ERROR_MAX: Final = 300


@dataclass(frozen=True)
class MailItem:
    """One alert in an e-mail (``kind``: ``new`` or ``resolved``)."""

    alert_id: int
    kind: str
    rule: str
    severity: str
    state: str
    time: datetime
    resolved_at: datetime | None
    details: Mapping[str, Any]


@dataclass(frozen=True)
class Suppressed:
    """Alerts not e-mailed because of ``alerts.email_max_per_hour`` (the summary)."""

    count: int
    limit: int
    rules: Mapping[str, int] = field(default_factory=dict)


def _severity(name: str) -> tuple[str, str]:
    return SEVERITY_LABELS.get(name, (name, name))


def _state(item: MailItem) -> tuple[str, str]:
    return STATE_LABELS.get(item.state, (item.state, item.state))


def subject(items: Sequence[MailItem], suppressed: Suppressed | None = None) -> str:
    """``[ARCILOGGER] Yüksek: Log kaynağı sessiz — fortigate`` (one alert), else a count."""
    if len(items) == 1 and suppressed is None:
        item = items[0]
        text = rule_text(item.rule)
        source = source_name(item.details)
        what = f"{text.name_tr} — {source}" if source else text.name_tr
        if item.kind == "resolved":
            return f"[{PRODUCT}] Çözüldü: {what}"
        return f"[{PRODUCT}] {_severity(item.severity)[0]}: {what}"
    if not items and suppressed is not None:
        return f"[{PRODUCT}] E-posta sınırı: {suppressed.count} uyarı gönderilmedi"
    top = max((item.severity for item in items), key=lambda s: SEVERITY_RANK.get(s, 0))
    count = len(items) + (suppressed.count if suppressed else 0)
    return f"[{PRODUCT}] {_severity(top)[0]}: {count} uyarı"


def _lines(item: MailItem, lang: int) -> list[str]:
    """The lines of one alert in Turkish (``lang`` 0) or English (1)."""
    text = rule_text(item.rule)
    name = (text.name_tr, text.name_en)[lang]
    action = (text.action_tr, text.action_en)[lang]
    labels = (
        ("Zaman", "Kaynak", "Durum", "Yapılacak", "Çözüldü", "Önem"),
        ("Time", "Source", "State", "What to do", "Resolved", "Severity"),
    )[lang]
    time_label, source_label, state_label, action_label, resolved_label, severity_label = labels
    head = f"[{_severity(item.severity)[lang]}] {item.rule} — {name}"
    if item.kind == "resolved":
        head = f"[{(_state(item))[lang]}] {item.rule} — {name}"
    lines = [
        head,
        f"{severity_label}: {_severity(item.severity)[lang]}",
        f"{time_label}: {format_datetime(item.time)} ({SITE_TZ_NAME})",
    ]
    source = source_name(item.details)
    if source:
        lines.append(f"{source_label}: {source}")
    lines.append(f"{state_label}: {_state(item)[lang]}")
    if item.resolved_at is not None and item.state == "resolved":
        lines.append(f"{resolved_label}: {format_datetime(item.resolved_at)}")
    lines.append(describe(item.rule, item.details)[lang])
    if item.kind == "new" and item.state != "resolved":
        lines.append(f"{action_label}: {action}")
    return lines


def _summary_line(suppressed: Suppressed, lang: int) -> str:
    rules = ", ".join(f"{rule}: {count}" for rule, count in sorted(suppressed.rules.items()))
    if lang == 0:
        return (
            f"Saatlik e-posta sınırı ({suppressed.limit}) aşıldığı için {suppressed.count} uyarı "
            f"e-postayla gönderilmedi ({rules}). Hepsi ARCILOGGER'da kayıtlı."
        )
    return (
        f"{suppressed.count} alert(s) were not e-mailed because the hourly e-mail limit "
        f"({suppressed.limit}) was reached ({rules}). All of them are recorded in ARCILOGGER."
    )


def render_text(
    items: Sequence[MailItem], web_url: str, suppressed: Suppressed | None = None
) -> str:
    listed, rest = items[:MAX_LISTED], len(items) - MAX_LISTED
    parts: list[str] = []
    for lang, (title, more, link) in enumerate(
        (
            (f"{PRODUCT} uyarısı", "uyarı daha", "Web arayüzü"),
            (f"{PRODUCT} alert", "more alert(s)", "Web interface"),
        )
    ):
        block = [title, "=" * len(title), ""]
        if suppressed is not None:
            block += [_summary_line(suppressed, lang), ""]
        for item in listed:
            block += [*_lines(item, lang), ""]
        if rest > 0:
            block += [f"… {rest} {more}.", ""]
        block.append(f"{link}: {web_url}")
        parts.append("\n".join(block))
    return "\n\n".join(parts) + "\n"


_CELL = "padding:2px 8px 2px 0;vertical-align:top;color:#555;"


def _html_item(item: MailItem, lang: int) -> str:
    lines = _lines(item, lang)
    head, rows = lines[0], lines[1:]
    colour = {"critical": "#b42318", "high": "#c4320a"}.get(item.severity, "#344054")
    if item.kind == "resolved":
        colour = "#067647"
    cells = []
    for row in rows:
        label, sep, value = row.partition(": ")
        if sep and len(label) <= 20:
            cells.append(
                f'<tr><td style="{_CELL}">{html.escape(label)}</td>'
                f"<td>{html.escape(value)}</td></tr>"
            )
        else:
            cells.append(f'<tr><td colspan="2">{html.escape(row)}</td></tr>')
    return (
        f'<div style="margin:0 0 16px 0;padding:8px 12px;border-left:4px solid {colour};">'
        f'<div style="font-weight:bold;color:{colour};">{html.escape(head)}</div>'
        f'<table style="border-collapse:collapse;font-size:14px;">{"".join(cells)}</table></div>'
    )


def render_html(
    items: Sequence[MailItem], web_url: str, suppressed: Suppressed | None = None
) -> str:
    listed, rest = items[:MAX_LISTED], len(items) - MAX_LISTED
    sections = []
    for lang, (title, more, link) in enumerate(
        (
            (f"{PRODUCT} uyarısı", "uyarı daha", "Web arayüzü"),
            (f"{PRODUCT} alert", "more alert(s)", "Web interface"),
        )
    ):
        body = [f'<h2 style="font-size:18px;margin:16px 0 8px 0;">{html.escape(title)}</h2>']
        if suppressed is not None:
            body.append(f"<p>{html.escape(_summary_line(suppressed, lang))}</p>")
        body += [_html_item(item, lang) for item in listed]
        if rest > 0:
            body.append(f"<p>… {rest} {html.escape(more)}.</p>")
        url = html.escape(web_url, quote=True)
        body.append(f'<p>{html.escape(link)}: <a href="{url}">{url}</a></p>')
        sections.append("".join(body))
    return (
        '<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"></head>'
        '<body style="font-family:Segoe UI,Arial,sans-serif;color:#101828;">'
        + '<hr style="border:0;border-top:1px solid #d0d5dd;margin:24px 0;">'.join(sections)
        + "</body></html>"
    )


def _domain(address: str) -> str:
    return address.rpartition("@")[2] or "localhost"


def build_message(
    items: Sequence[MailItem],
    *,
    sender: str,
    recipient: str,
    web_url: str,
    now: datetime,
    suppressed: Suppressed | None = None,
) -> EmailMessage:
    message = EmailMessage()
    message["Subject"] = subject(items, suppressed)
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = rfc2822(now)
    message["Message-ID"] = make_msgid(idstring="arcilogger", domain=_domain(sender))
    message["Auto-Submitted"] = "auto-generated"
    # Quoted-printable: 7-bit safe for any server, Turkish letters included.
    message.set_content(render_text(items, web_url, suppressed), cte="quoted-printable")
    message.add_alternative(
        render_html(items, web_url, suppressed), subtype="html", cte="quoted-printable"
    )
    return message


def trial_message(*, sender: str, recipient: str, web_url: str, now: datetime) -> EmailMessage:
    """``arcilogger-worker alerts test-email``: shows that the SMTP settings work."""
    message = EmailMessage()
    message["Subject"] = f"[{PRODUCT}] Deneme e-postası / Test e-mail"
    message["From"] = sender
    message["To"] = recipient
    message["Date"] = rfc2822(now)
    message["Message-ID"] = make_msgid(idstring="arcilogger-test", domain=_domain(sender))
    message["Auto-Submitted"] = "auto-generated"
    when = f"{format_datetime(now)} ({SITE_TZ_NAME})"
    text = (
        f"Bu bir {PRODUCT} deneme e-postasıdır ({when}). Uyarı e-postaları bu ayarlarla "
        f"gönderilir.\n\nThis is an {PRODUCT} test e-mail ({when}). Alert e-mails are sent with "
        f"these settings.\n\nWeb arayüzü / Web interface: {web_url}\n"
    )
    message.set_content(text, cte="quoted-printable")
    url = html.escape(web_url, quote=True)
    message.add_alternative(
        '<!DOCTYPE html><html lang="tr"><head><meta charset="utf-8"></head>'
        '<body style="font-family:Segoe UI,Arial,sans-serif;">'
        f"<p>{html.escape(text.split(chr(10))[0])}</p>"
        f"<p>{html.escape(text.split(chr(10))[2])}</p>"
        f'<p>Web arayüzü / Web interface: <a href="{url}">{url}</a></p></body></html>',
        subtype="html",
        cte="quoted-printable",
    )
    return message


# -- SMTP -----------------------------------------------------------------------------------------


class SmtpSendError(Exception):
    """An e-mail was not accepted. ``permanent``: the server refused it for good (5xx on the
    sender, the recipient or the message) — retrying the same e-mail cannot help.
    ``recipient``: only this recipient was refused (the server itself works)."""

    def __init__(self, text: str, *, permanent: bool, recipient: bool = False) -> None:
        super().__init__(text)
        self.permanent = permanent
        self.recipient = recipient


def error_text(exc: BaseException, secret: str | None = None) -> str:
    """A short description of a failed send, without the password (even if a server echoed
    it)."""
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        replies = [f"{code} {_reply(reply)}" for code, reply in exc.recipients.values()]
        text = f"recipient refused: {'; '.join(replies)}"
    elif isinstance(exc, smtplib.SMTPResponseException):
        text = f"{type(exc).__name__}: {exc.smtp_code} {_reply(exc.smtp_error)}"
    elif isinstance(exc, TimeoutError):
        text = "timeout: no answer from the SMTP server"
    elif isinstance(exc, ssl.SSLError):
        text = f"TLS: {exc.reason or exc}"
    else:
        text = f"{type(exc).__name__}: {exc}"
    if secret:
        text = text.replace(secret, "***")
    return text[:ERROR_MAX]


def _reply(value: bytes | str) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _code(exc: BaseException) -> int | None:
    if isinstance(exc, smtplib.SMTPRecipientsRefused):
        codes = [code for code, _ in exc.recipients.values()]
        return min(codes) if codes else None
    if isinstance(exc, smtplib.SMTPResponseException) and not isinstance(
        exc, smtplib.SMTPAuthenticationError
    ):
        return int(exc.smtp_code)
    return None


@dataclass
class SmtpSender:
    """Sends one message to one recipient per connection (stdlib :mod:`smtplib`)."""

    config: SmtpConfig
    password: str | None = None

    def _context(self) -> ssl.SSLContext:
        cafile = str(self.config.ca_file) if self.config.ca_file is not None else None
        return ssl.create_default_context(cafile=cafile)

    def send(self, message: EmailMessage, recipient: str) -> None:
        """Deliver ``message`` to ``recipient``; :class:`SmtpSendError` when it was not accepted.

        Authentication errors, timeouts, connection and TLS errors and 4xx replies are
        temporary (the e-mail is retried); a 5xx reply to the sender, the recipient or the
        message is permanent.
        """
        settings = self.config
        if settings.host is None or settings.from_address is None:
            raise SmtpSendError("[smtp] host / from_address are not set", permanent=False)
        if settings.username and not self.password:
            raise SmtpSendError(
                "smtp.password is not stored (arcilogger-core secret set smtp.password)",
                permanent=False,
            )
        try:
            self._send(message, recipient)
        except (smtplib.SMTPException, OSError) as exc:
            code = _code(exc)
            raise SmtpSendError(
                error_text(exc, self.password),
                permanent=code is not None and 500 <= code < 600,
                recipient=isinstance(exc, smtplib.SMTPRecipientsRefused),
            ) from None
        except UnicodeEncodeError:  # smtplib's AUTH PLAIN and LOGIN send ASCII only
            raise SmtpSendError(
                "AUTH: the user name or password has characters smtplib cannot send (ASCII only)",
                permanent=False,
            ) from None

    def _send(self, message: EmailMessage, recipient: str) -> None:
        settings = self.config
        host, port, timeout = str(settings.host), settings.port, settings.timeout_seconds
        client: smtplib.SMTP
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=self._context())
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)
        with client:
            client.ehlo()
            if settings.security == "starttls":
                client.starttls(context=self._context())
                client.ehlo()
            if settings.username:
                client.login(settings.username, self.password or "")
            client.send_message(message, from_addr=str(settings.from_address), to_addrs=[recipient])
