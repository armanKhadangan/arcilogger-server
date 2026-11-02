"""``arcilogger-worker alerts …`` (run by an administrator on the server)::

    arcilogger-worker alerts test-email --to ADDRESS   one test e-mail through [smtp]
    arcilogger-worker alerts list [--open] [--limit N]  the newest alerts

``test-email`` works with ``[smtp] enabled = false`` too (to try the settings before switching
the e-mail on) and prints either that the server accepted the e-mail or the SMTP error — never
the password. The link in it is ``alerts.web_base_url`` when the database is reachable, else the
address derived from the configuration. The browser's "Test e-postası gönder" (ADR-0014, worker
job ``admin.requests``) sends the same e-mail with :func:`trial_email`.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from email.message import EmailMessage
from typing import TextIO

from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError

from arcilogger_core.config import EMAIL_ADDRESS, Config
from arcilogger_core.fmt import format_datetime
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_core.timeutil import utc_now
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert
from arcilogger_db.settings import get_setting
from arcilogger_worker.alerts.mail import SmtpSender, SmtpSendError, trial_message
from arcilogger_worker.alerts.notify import smtp_password, web_base_url
from arcilogger_worker.alerts.store import ACTIVE_STATES

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG = 2
EXIT_DATABASE = 3
_ADDRESS = re.compile(EMAIL_ADDRESS)


def _web_url(engine: Engine | None, config: Config) -> str:
    if engine is None:
        return web_base_url(None, config)
    try:
        with session_factory(engine)() as session:
            return web_base_url(get_setting(session, "alerts.web_base_url"), config)
    except SQLAlchemyError:
        return web_base_url(None, config)


class TrialEmailRefusedError(ValueError):
    """No test e-mail can be sent with this configuration (``not_configured``: no SMTP server
    or sender; else the password is unreadable). ``text`` says why, Turkish and English, in a
    fixed sentence; ``detail`` is the underlying error (it may name a file of the server, never
    the password), which ``str()`` adds for the console."""

    def __init__(self, text: str, *, not_configured: bool, detail: str | None = None) -> None:
        super().__init__(text if detail is None else f"{text}: {detail}")
        self.text = text
        self.not_configured = not_configured
        self.detail = detail


@dataclass(frozen=True)
class TrialEmailOutcome:
    """A test e-mail handed to the SMTP server, or why the server did not take it."""

    ok: bool
    #: ``host:port (security)`` it went to.
    where: str
    #: The SMTP reply code of a refusal, when the server gave one.
    smtp_code: int | None = None
    #: The SMTP error (never the password; it may name the recipient).
    error_text: str | None = None


@dataclass(frozen=True)
class TrialEmail:
    """One test e-mail ready to go (:func:`trial_email`)."""

    sender: SmtpSender
    message: EmailMessage
    recipient: str
    where: str

    def send(self) -> TrialEmailOutcome:
        try:
            self.sender.send(self.message, self.recipient)
        except SmtpSendError as exc:
            return TrialEmailOutcome(False, self.where, exc.smtp_code, str(exc))
        return TrialEmailOutcome(True, self.where)


def trial_email(config: Config, recipient: str, *, web_url: str) -> TrialEmail:
    """The test e-mail to ``recipient`` through ``[smtp]`` (also while ``enabled`` is false);
    :class:`TrialEmailRefusedError` when the configuration cannot send one. Shared by
    ``alerts test-email`` and the worker job ``admin.requests``."""
    smtp = config.smtp
    if smtp.host is None or smtp.from_address is None:
        raise TrialEmailRefusedError(
            "[smtp] host ve from_address ayarlı değil / [smtp] host and from_address are not set "
            "(arcilogger.toml)",
            not_configured=True,
        )
    try:
        password = smtp_password(config)
    except (SecretStoreError, OSError, ValueError) as exc:
        raise TrialEmailRefusedError(
            "smtp.password okunamadı / cannot read smtp.password",
            not_configured=False,
            detail=str(exc),
        ) from exc
    message = trial_message(
        sender=smtp.from_address, recipient=recipient, web_url=web_url, now=utc_now()
    )
    where = f"{smtp.host}:{smtp.port} ({smtp.security})"
    return TrialEmail(SmtpSender(smtp, password), message, recipient, where)


def send_test_email(
    config: Config, recipient: str, *, engine: Engine | None = None, out: TextIO | None = None
) -> int:
    out = out or sys.stdout
    if not _ADDRESS.fullmatch(recipient):  # ``$`` alone would let a final newline through
        print(f"Geçersiz e-posta adresi / invalid e-mail address: {recipient!r}", file=sys.stderr)
        return EXIT_CONFIG
    try:
        trial = trial_email(config, recipient, web_url=_web_url(engine, config))
    except TrialEmailRefusedError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG
    if not config.smtp.enabled:
        print(
            "Not: [smtp] enabled = false — uyarı e-postaları henüz gönderilmiyor / note: alert "
            "e-mails are not sent yet",
            file=out,
        )
    outcome = trial.send()
    if not outcome.ok:
        print(f"Gönderilemedi / not sent ({outcome.where}): {outcome.error_text}", file=sys.stderr)
        return EXIT_FAILED
    print(f"Gönderildi / sent: {recipient} — {outcome.where}", file=out)
    return EXIT_OK


def list_alerts(
    engine: Engine, *, only_open: bool = False, limit: int = 50, out: TextIO | None = None
) -> int:
    out = out or sys.stdout
    query = select(Alert).order_by(Alert.time.desc(), Alert.id.desc()).limit(max(1, limit))
    if only_open:
        query = query.where(Alert.state.in_(ACTIVE_STATES))
    try:
        with session_factory(engine)() as session:
            rows = session.scalars(query).all()
    except SQLAlchemyError as exc:
        print(f"Veritabanı hatası / database error: {type(exc).__name__}", file=sys.stderr)
        return EXIT_DATABASE
    if not rows:
        print("Uyarı yok / no alerts", file=out)
        return EXIT_OK
    for alert in rows:
        print(
            f"{alert.id:>7}  {format_datetime(alert.time)}  {alert.state:<12} "
            f"{alert.severity:<8} {alert.rule:<16} {alert.title}",
            file=out,
        )
    return EXIT_OK
