"""SMTP for ARCILOGGER's e-mails (stdlib :mod:`smtplib`; ADR-0010, ADR-0014).

The transport the worker's alert and report e-mails use (``arcilogger_worker.alerts.mail``
re-exports it) and the administration agent's trial e-mail with the candidate values of a
browser change (``arcilogger_core.admin.probe``). It lives in core because the agent runs
without the worker.

``[smtp]``: ``starttls`` (port 587) or ``ssl`` (implicit TLS, port 465) verify the server
certificate against the system trust store or ``ca_file`` — always, there is no switch to turn it
off. ``none`` is plain SMTP for a relay on this server. The password (secret ``smtp.password``)
is used for ``AUTH`` only and never appears in an error text.

:func:`explain` turns a known refusal of Google's or Microsoft 365's servers into a short
explanation of what to change (0.4.0 S5), and any failure of a port and a security that do not
go together (465 with STARTTLS, 587 with SSL/TLS: :func:`mismatch`) into what to choose; the
raw reply is always shown next to it.
"""

from __future__ import annotations

import html
import re
import smtplib
import ssl
from collections.abc import Callable
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


_ADDRESS_IN_TEXT: Final = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)


def mask_address(address: str) -> str:
    """``bora@ornek.example`` → ``b***@ornek.example`` (ADR-0010 A4; ``***`` when it is not an
    address)."""
    local, at, domain = address.strip().rpartition("@")
    if not at or not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


def mask_addresses(text: str) -> str:
    """Every e-mail address in ``text`` masked (an SMTP reply may quote the recipient)."""
    return _ADDRESS_IN_TEXT.sub(lambda match: mask_address(match.group()), text)


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


# -- explanations of known refusals (0.4.0 S5) ---------------------------------------------------
#
# Sources, read 2026-11-08 (design/0.4.0-contract.md, Deviations): Google's "SMTP errors and
# codes" (support.google.com/a/answer/3726730, updated 2026-10-26; it prints 5.7.80 and 5.7.90
# where the contract's table had 5.7.8 and 5.7.9, so both forms are matched — sahada doğrula),
# "Transition from less secure apps to OAuth" (answer/176600) and "Route outgoing SMTP relay
# messages through Google" (answer/2956491); Microsoft Learn "How to set up a multifunction
# device or application to send email using Microsoft 365" (updated 2026-10-05) and "Fix issues
# with printers, scanners, and LOB applications that send email using Microsoft 365".

#: The mail server families with explanations, by the domain of ``[smtp] host``.
_FAMILIES: Final = {
    "gmail.com": "google",
    "googlemail.com": "google",
    "office365.com": "microsoft",
    "outlook.com": "microsoft",
}
#: An enhanced status code (RFC 3463) in a reply.
_STATUS: Final = re.compile(r"(?<![\d.])([245]\.\d{1,3}\.\d{1,3})(?![\d.])")


@dataclass(frozen=True)
class Explanation:
    """What to change when a server of ``family`` answers with one of ``statuses`` (and, when
    set, a reply containing ``phrase``)."""

    family: str
    statuses: frozenset[str]
    phrase: str | None
    tr: str
    en: str


#: The first entry that matches explains the reply.
EXPLANATIONS: Final[tuple[Explanation, ...]] = (
    Explanation(
        "google",
        frozenset({"5.7.9", "5.7.90"}),
        "application-specific password",
        "Bu Google hesabında 2 Adımlı Doğrulama açık: hesap parolası yerine 16 karakterlik bir "
        "uygulama şifresi gerekir (myaccount.google.com/apppasswords). Yönetici uygulama "
        "şifrelerine izin vermiyorsa “Google SMTP geçiş hizmeti” hazır ayarını kullanın.",
        "This Google account has 2-Step Verification on: it needs a 16-character app password "
        "instead of the account password (myaccount.google.com/apppasswords). If the "
        "administrator does not allow app passwords, use the “Google SMTP relay” preset.",
    ),
    Explanation(
        "google",
        frozenset({"5.7.8", "5.7.80", "5.7.9", "5.7.90", "5.7.14"}),
        None,
        "Google bu kullanıcı adı ve parolayla girişi kabul etmedi. Google Workspace hesapları "
        "1 Mayıs 2025'ten beri hesap parolasıyla SMTP girişini kabul etmiyor: 2 Adımlı "
        "Doğrulama açık hesabın uygulama şifresini girin ya da “Google SMTP geçiş hizmeti” "
        "hazır ayarını kullanın.",
        "Google did not accept this user name and password. Since 1 May 2025 Google Workspace "
        "accounts no longer accept the account password for SMTP: enter an app password of an "
        "account with 2-Step Verification, or use the “Google SMTP relay” preset.",
    ),
    Explanation(
        "google",
        frozenset({"5.7.0", "5.7.1"}),
        "relay",
        "Google SMTP geçiş hizmeti bu gönderimi kabul etmedi. Google Yönetici Konsolu'nda "
        "Apps → Google Workspace → Gmail → Routing → SMTP relay service altında bu sunucunun "
        "internete çıktığı genel IP adresini ekleyin; gönderen adresi kendi alan adınızda "
        "olmalı.",
        "The Google SMTP relay did not accept this e-mail. In the Google Admin console (Apps → "
        "Google Workspace → Gmail → Routing → SMTP relay service) add the public IP address "
        "this server reaches the internet from; the sender address must be in your own domain.",
    ),
    Explanation(
        "microsoft",
        frozenset({"5.7.3", "5.7.139"}),
        None,
        "Microsoft 365 girişi kabul etmedi. Parolayı kontrol edin; Microsoft 365 yöneticisi bu "
        "posta kutusu için kimliği doğrulanmış SMTP'yi (SMTP AUTH) açmalı. Kiracıda “Güvenlik "
        "varsayılanları” açıksa parolayla SMTP girişi çalışmaz.",
        "Microsoft 365 did not accept the sign-in. Check the password; the Microsoft 365 "
        "administrator must turn on authenticated SMTP (SMTP AUTH) for this mailbox. With the "
        "tenant's Security defaults on, SMTP sign-in with a password does not work.",
    ),
    Explanation(
        "microsoft",
        frozenset({"5.7.57"}),
        None,
        "Microsoft 365 oturum açılmadan gönderime izin vermiyor: kullanıcı adı ve parolayı "
        "girin; posta kutusu için SMTP AUTH açık olmalı.",
        "Microsoft 365 does not accept e-mail without a sign-in: enter the user name and "
        "password; SMTP AUTH must be on for the mailbox.",
    ),
    Explanation(
        "microsoft",
        frozenset({"5.7.60"}),
        None,
        "Microsoft 365 bu gönderen adresine izin vermiyor: gönderen adresi oturum açan posta "
        "kutusunun adresi olmalı ya da bu posta kutusuna o adres için “Farklı Gönder” (Send "
        "As) izni verilmeli.",
        "Microsoft 365 refuses this sender address: it must be the address of the signed-in "
        "mailbox, or that mailbox needs “Send As” permission for it.",
    ),
)


#: The port of SMTP with TLS from the first byte ("submissions", RFC 8314); 587 and 25 start in
#: plain text and switch with STARTTLS.
IMPLICIT_TLS_PORT: Final = 465
STARTTLS_PORTS: Final = frozenset({25, 587})


def mismatch(port: int | None, security: str | None) -> tuple[str, str] | None:
    """``(tr, en)`` when ``port`` and ``security`` do not go together, else None.

    465 with STARTTLS (or none) waits for the server's greeting while the server waits for the
    TLS handshake, until it drops the connection: "Connection unexpectedly closed" (seen on a
    site with Gmail on 2026-11-12). SSL/TLS on 587 or 25 sends a handshake to a server that
    answers in plain text: a TLS "wrong version number" error.
    """
    if port == IMPLICIT_TLS_PORT and security in ("starttls", "none"):
        return (
            "465 numaralı bağlantı noktası en baştan şifreli (SSL/TLS) bağlantı ister, ama "
            "bağlantı güvenliği “STARTTLS” ya da “Şifresiz” seçili. Ayarlar → E-posta'da "
            "bağlantı güvenliğini “SSL/TLS” yapın ya da 587 numaralı bağlantı noktasını "
            "“STARTTLS” ile kullanın.",
            "Port 465 expects an encrypted (SSL/TLS) connection from the start, but the "
            "connection security is “STARTTLS” or “none”. In Ayarlar → E-posta set the "
            "connection security to “SSL/TLS”, or use port 587 with “STARTTLS”.",
        )
    if port in STARTTLS_PORTS and security == "ssl":
        return (
            f"{port} numaralı bağlantı noktası önce şifresiz bağlanır, sonra STARTTLS ile "
            "şifreler, ama bağlantı güvenliği “SSL/TLS” seçili. Ayarlar → E-posta'da bağlantı "
            "güvenliğini “STARTTLS” yapın ya da 465 numaralı bağlantı noktasını kullanın.",
            f"Port {port} starts unencrypted and switches to encryption with STARTTLS, but the "
            "connection security is “SSL/TLS”. In Ayarlar → E-posta set the connection security "
            "to “STARTTLS”, or use port 465.",
        )
    return None


def family(host: str | None) -> str | None:
    """``google``, ``microsoft`` or None for the domain of ``host``."""
    name = (host or "").strip().rstrip(".").lower()
    for domain, found in _FAMILIES.items():
        if name == domain or name.endswith(f".{domain}"):
            return found
    return None


def explain(
    text: str | None,
    host: str | None,
    *,
    port: int | None = None,
    security: str | None = None,
) -> tuple[str, str] | None:
    """``(tr, en)``: what to change for the failure ``text`` (as :func:`error_text` writes it)
    of the server ``host``; None for a reply or a server without an explanation. A ``port``
    and ``security`` that do not go together (:func:`mismatch`) explain any failure first."""
    if not text:
        return None
    wrong = mismatch(port, security)
    if wrong is not None:
        return wrong
    found = family(host)
    if found is None:
        return None
    statuses = set(_STATUS.findall(text))
    lowered = text.lower()
    for entry in EXPLANATIONS:
        if entry.family != found or not statuses & entry.statuses:
            continue
        if entry.phrase is None or entry.phrase in lowered:
            return entry.tr, entry.en
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
        self._guarded(lambda: self._send(message, recipient))

    def check(self) -> None:
        """Connect, TLS as configured, EHLO and, with a user name, AUTH; then QUIT: nothing is
        sent (the self-test, 0.4.0 P3). :class:`SmtpSendError` as :meth:`send` raises it."""

        def session() -> None:
            with self._open():
                pass

        self._guarded(session)

    def _guarded(self, run: Callable[[], None]) -> None:
        settings = self.config
        if settings.host is None or settings.from_address is None:
            raise SmtpSendError("[smtp] host / from_address are not set", permanent=False)
        if settings.username and not self.password:
            raise SmtpSendError(
                "smtp.password is not stored (arcilogger-core secret set smtp.password)",
                permanent=False,
            )
        try:
            run()
        except (smtplib.SMTPException, OSError) as exc:
            code = _code(exc)
            raise SmtpSendError(
                error_text(exc, self.password),
                permanent=code is not None and 500 <= code < 600,
                recipient=isinstance(exc, smtplib.SMTPRecipientsRefused),
                smtp_code=code,
            ) from None
        except UnicodeEncodeError:  # smtplib's AUTH PLAIN and LOGIN send ASCII only
            raise SmtpSendError(
                "AUTH: the user name or password has characters smtplib cannot send (ASCII only)",
                permanent=False,
            ) from None

    def _open(self) -> smtplib.SMTP:
        """A connection past TLS and AUTH; leaving its ``with`` block sends QUIT."""
        settings = self.config
        host, port, timeout = str(settings.host), settings.port, settings.timeout_seconds
        client: smtplib.SMTP
        if settings.security == "ssl":
            client = smtplib.SMTP_SSL(host, port, timeout=timeout, context=self._context())
        else:
            client = smtplib.SMTP(host, port, timeout=timeout)
        try:
            client.ehlo()
            if settings.security == "starttls":
                client.starttls(context=self._context())
                client.ehlo()
            if settings.username:
                client.login(settings.username, self.password or "")
        except BaseException:
            client.close()
            raise
        return client

    def _send(self, message: EmailMessage, recipient: str) -> None:
        settings = self.config
        with self._open() as client:
            client.send_message(message, from_addr=str(settings.from_address), to_addrs=[recipient])
