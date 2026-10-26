"""SMTP for ARCILOGGER's e-mails (stdlib :mod:`smtplib`; ADR-0010, ADR-0014).

The transport the worker's alert and report e-mails use (``arcilogger_worker.alerts.mail``
re-exports it) and the administration agent's trial e-mail with the candidate values of a
browser change (``arcilogger_core.admin.probe``). It lives in core because the agent runs
without the worker.

``[smtp]``: ``starttls`` (port 587) or ``ssl`` (implicit TLS, port 465) verify the server
certificate against the system trust store or ``ca_file`` — always, there is no switch to turn it
off. ``none`` is plain SMTP for a relay on this server. The password (secret ``smtp.password``)
is used for ``AUTH`` only and never appears in an error text.
"""

from __future__ import annotations

import html
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime
from email.message import EmailMessage
from email.utils import format_datetime as rfc2822
from email.utils import make_msgid
from typing import Final

from arcilogger_core.config import SmtpConfig
from arcilogger_core.fmt import format_datetime
from arcilogger_core.timeutil import SITE_TZ_NAME

PRODUCT: Final = "ARCILOGGER"
#: Longest error text kept (an SMTP reply can be long).
ERROR_MAX: Final = 300


def _domain(address: str) -> str:
    return address.rpartition("@")[2] or "localhost"


def trial_message(*, sender: str, recipient: str, web_url: str, now: datetime) -> EmailMessage:
    """The trial e-mail (``arcilogger-worker alerts test-email``, the browser's SMTP check):
    shows that the SMTP settings work."""
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


class SmtpSendError(Exception):
    """An e-mail was not accepted. ``permanent``: the server refused it for good (5xx on the
    sender, the recipient or the message) — retrying the same e-mail cannot help.
    ``recipient``: only this recipient was refused (the server itself works). ``smtp_code``: the
    server's reply code, when there was one."""

    def __init__(
        self,
        text: str,
        *,
        permanent: bool,
        recipient: bool = False,
        smtp_code: int | None = None,
    ) -> None:
        super().__init__(text)
        self.permanent = permanent
        self.recipient = recipient
        self.smtp_code = smtp_code


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
                smtp_code=code,
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
