"""``arcilogger-worker alerts …`` (run by an administrator on the server)::

    arcilogger-worker alerts test-email --to ADDRESS   one test e-mail through [smtp]
    arcilogger-worker alerts list [--open] [--limit N]  the newest alerts

``test-email`` works with ``[smtp] enabled = false`` too (to try the settings before switching
the e-mail on) and prints either that the server accepted the e-mail or the SMTP error — never
the password. The link in it is ``alerts.web_base_url`` when the database is reachable, else the
address derived from the configuration.
"""

from __future__ import annotations

import re
import sys
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


def send_test_email(
    config: Config, recipient: str, *, engine: Engine | None = None, out: TextIO | None = None
) -> int:
    out = out or sys.stdout
    smtp = config.smtp
    if not _ADDRESS.match(recipient):
        print(f"Geçersiz e-posta adresi / invalid e-mail address: {recipient!r}", file=sys.stderr)
        return EXIT_CONFIG
    if smtp.host is None or smtp.from_address is None:
        print(
            "[smtp] host ve from_address ayarlı değil / [smtp] host and from_address are not set "
            "(arcilogger.toml)",
            file=sys.stderr,
        )
        return EXIT_CONFIG
    try:
        password = smtp_password(config)
    except (SecretStoreError, OSError, ValueError) as exc:
        print(f"smtp.password okunamadı / cannot read smtp.password: {exc}", file=sys.stderr)
        return EXIT_CONFIG
    now = utc_now()
    message = trial_message(
        sender=smtp.from_address, recipient=recipient, web_url=_web_url(engine, config), now=now
    )
    where = f"{smtp.host}:{smtp.port} ({smtp.security})"
    if not smtp.enabled:
        print(
            "Not: [smtp] enabled = false — uyarı e-postaları henüz gönderilmiyor / note: alert "
            "e-mails are not sent yet",
            file=out,
        )
    try:
        SmtpSender(smtp, password).send(message, recipient)
    except SmtpSendError as exc:
        print(f"Gönderilemedi / not sent ({where}): {exc}", file=sys.stderr)
        return EXIT_FAILED
    print(f"Gönderildi / sent: {recipient} — {where}", file=out)
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
