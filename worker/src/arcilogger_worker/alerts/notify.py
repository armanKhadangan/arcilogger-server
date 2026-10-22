"""``alerts.notify`` — alert e-mail through ``[smtp]`` (every minute; ADR-0010).

Policy (database settings, ``alerts.email_*``) and the connection (``[smtp]`` in
``arcilogger.toml``, password: secret ``smtp.password``) are kept apart: the policy is changed and
audited like every setting, the connection with the secret stays out of the database.

1. **Queue.** Every active alert at or above ``alerts.email_min_severity`` whose problem was last
   seen in the last :data:`LOOKBACK` (its ``time``, an imported repeat or a check that found it
   again: :func:`~arcilogger_worker.alerts.store.last_seen`; importing an old ``alerts.jsonl``
   never mails history, a problem still going on is mailed even when it began earlier) gets one
   ``alert_deliveries`` row per recipient, kind ``new``. With
   ``alerts.email_resolved`` an alert resolved after its ``new`` e-mail went out gets a
   ``resolved`` row. ``(alert_id, kind, recipient)`` is unique: queued once, whatever happens.
   ``R20-email`` is never e-mailed (no loop).
2. **Digest.** A recipient's queued rows go out together in one e-mail once the oldest has
   waited ``alerts.email_digest_seconds`` (retries go out as soon as their backoff allows).
3. **Lease, send, record.** Before sending, the rows get ``attempts + 1`` and a retry time
   (:data:`BACKOFF`), committed; then the e-mail is sent; then ``sent`` is committed. A crash
   between sending and the last commit sends that e-mail once more after the backoff — never more.
4. **Failures.** A temporary failure (connection, timeout, TLS, authentication, 4xx) is retried
   with :data:`BACKOFF`; after :data:`MAX_ATTEMPTS`, or at once for a 5xx refusal, the rows are
   ``failed`` and **one** local alert ``R20-email`` is opened, naming the failing recipients; it
   is resolved once an e-mail reached each of them, or they were taken out of
   ``alerts.email_recipients``. ``last_error`` holds the SMTP reply, never the password.
5. **Rate limit.** At most ``alerts.email_max_per_hour`` e-mails per recipient in any 60 minutes.
   Beyond it the due rows become ``suppressed``; the next e-mail the limit allows starts with a
   summary of how many were not sent (and the rules), and marks them reported.
"""

from __future__ import annotations

import ipaddress
import logging
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final, Protocol

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.secretstore import SMTP_PASSWORD, SecretStore, SecretStoreError
from arcilogger_db.models import ALERT_SEVERITIES, Alert, AlertDelivery
from arcilogger_db.settings import get_settings
from arcilogger_worker.alerts.mail import (
    MailItem,
    SmtpSender,
    SmtpSendError,
    Suppressed,
    build_message,
)
from arcilogger_worker.alerts.store import (
    ACTIVE_STATES,
    SEEN_AT,
    SEVERITY_RANK,
    active_alert,
    last_seen,
    open_alert,
)
from arcilogger_worker.alerts.store import resolve as resolve_alert

log = logging.getLogger("arcilogger_worker.alerts")

EMAIL_RULE: Final = "R20-email"
#: Only alerts whose problem was last seen within this period are e-mailed.
LOOKBACK: Final = timedelta(hours=24)
#: Waits after the 1st, 2nd, … failed attempt (the last one repeats).
BACKOFF: Final = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=3),
)
#: Attempts per e-mail before its rows are ``failed`` and ``R20-email`` is raised.
MAX_ATTEMPTS: Final = 6
RATE_WINDOW: Final = timedelta(hours=1)
#: The web address when neither the setting nor ``[collector] bind`` names one: the installer's
#: default server name (``-ServerName``), never an address of a particular network.
DEFAULT_WEB_URL: Final = "https://arcilogger.internal"
REMOVED: Final = "alıcı listeden çıkarıldı / recipient removed from alerts.email_recipients"
EXPIRED: Final = "24 saat içinde gönderilemedi / not sent within 24 hours"


class Sender(Protocol):
    def send(self, message: Any, recipient: str) -> None: ...


def backoff(attempts: int) -> timedelta:
    return BACKOFF[min(max(attempts, 1), len(BACKOFF)) - 1]


def web_base_url(setting: Any, config: Config) -> str:
    """``alerts.web_base_url``; when empty: ``https://<[collector] bind>`` if that is one
    address of this server (not 0.0.0.0, not loopback), else :data:`DEFAULT_WEB_URL`."""
    if isinstance(setting, str) and setting.strip():
        return setting.strip().rstrip("/")
    try:
        bind = ipaddress.ip_address(config.collector.bind)
    except ValueError:
        return DEFAULT_WEB_URL
    if bind.is_unspecified or bind.is_loopback:
        return DEFAULT_WEB_URL
    return f"https://[{bind}]" if bind.version == 6 else f"https://{bind}"


@dataclass(frozen=True)
class Policy:
    recipients: tuple[str, ...]
    min_severity: str
    max_per_hour: int
    digest: timedelta
    resolved: bool
    web_url: str

    @classmethod
    def load(cls, session: Session, config: Config) -> Policy:
        values = get_settings(session, prefix="alerts.")
        recipients = values.get("alerts.email_recipients")
        minimum = values.get("alerts.email_min_severity")
        per_hour = values.get("alerts.email_max_per_hour")
        digest = values.get("alerts.email_digest_seconds")
        return cls(
            recipients=tuple(r for r in recipients if isinstance(r, str) and "@" in r)
            if isinstance(recipients, list)
            else (),
            min_severity=minimum if minimum in ALERT_SEVERITIES else "high",
            max_per_hour=per_hour if type(per_hour) is int and per_hour >= 1 else 20,
            digest=timedelta(seconds=digest if type(digest) is int and digest >= 0 else 300),
            resolved=values.get("alerts.email_resolved") is not False,
            web_url=web_base_url(values.get("alerts.web_base_url"), config),
        )

    @property
    def severities(self) -> list[str]:
        floor = SEVERITY_RANK[self.min_severity]
        return [name for name in ALERT_SEVERITIES if SEVERITY_RANK[name] >= floor]


@dataclass
class NotifyReport:
    queued: int = 0
    queued_resolved: int = 0
    emails: int = 0
    sent: int = 0
    failed: int = 0
    suppressed: int = 0
    #: Recipients whose rows wait for the digest window or the retry time.
    waiting: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None

    def details(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "queued": self.queued,
            "queued_resolved": self.queued_resolved,
            "emails": self.emails,
            "sent": self.sent,
            "failed": self.failed,
            "suppressed": self.suppressed,
            "waiting": len(self.waiting),
            "errors": self.errors[:20],
        }
        if self.skipped:
            result.update(_skipped=True, reason=self.skipped)
        return result


# -- 1. queue -------------------------------------------------------------------------------------


def enqueue(session: Session, policy: Policy, *, now: datetime) -> tuple[int, int]:
    """Queue the new and the resolved alerts; returns how many rows of each (caller commits)."""
    since = now - LOOKBACK
    candidates = session.scalars(
        select(Alert).where(
            Alert.severity.in_(policy.severities),
            Alert.rule != EMAIL_RULE,
            Alert.state.in_(ACTIVE_STATES),
            or_(
                Alert.time >= since,
                Alert.details.has_key("last_time"),
                Alert.details.has_key(SEEN_AT),
            ),
        )
    )
    # By the latest occurrence (the first one, an imported repeat, a check that found it again):
    # imported history stays out, a problem still going on is e-mailed however long it lasts.
    alert_ids = [alert.id for alert in candidates if last_seen(alert) >= since]
    new_rows = [
        {"alert_id": alert_id, "kind": "new", "recipient": recipient, "created_at": now}
        for alert_id in alert_ids
        for recipient in policy.recipients
    ]
    resolved_rows: list[dict[str, Any]] = []
    if policy.resolved:
        announced = session.execute(
            select(Alert.id, AlertDelivery.recipient)
            .join(AlertDelivery, AlertDelivery.alert_id == Alert.id)
            .where(
                Alert.state == "resolved",
                Alert.resolved_at >= since,
                AlertDelivery.kind == "new",
                AlertDelivery.status == "sent",
                AlertDelivery.sent_at < Alert.resolved_at,
                AlertDelivery.recipient.in_(policy.recipients),
            )
        ).all()
        resolved_rows = [
            {"alert_id": alert_id, "kind": "resolved", "recipient": recipient, "created_at": now}
            for alert_id, recipient in announced
        ]
    counts = []
    for rows in (new_rows, resolved_rows):
        if not rows:
            counts.append(0)
            continue
        statement = (
            insert(AlertDelivery)
            .values(rows)
            .on_conflict_do_nothing(index_elements=["alert_id", "kind", "recipient"])
            .returning(AlertDelivery.id)
        )
        counts.append(len(session.execute(statement).all()))
    _retire(session, policy, now=now)
    return counts[0], counts[1]


def _retire(session: Session, policy: Policy, *, now: datetime) -> None:
    """Queued rows of a recipient no longer listed, or never tried within a day: ``failed``."""
    stale = session.scalars(
        select(AlertDelivery)
        .where(
            AlertDelivery.status.in_(("queued", "suppressed")),
            AlertDelivery.message_id.is_(None),
            or_(
                AlertDelivery.recipient.not_in(policy.recipients or ("",)),
                (AlertDelivery.created_at < now - LOOKBACK) & (AlertDelivery.attempts == 0),
            ),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for row in stale:
        row.status = "failed"
        row.last_error = REMOVED if row.recipient not in policy.recipients else EXPIRED


# -- 2.-5. deliver ---------------------------------------------------------------------------------


def _due(session: Session, recipient: str, now: datetime) -> tuple[list[AlertDelivery], ...]:
    """``(queued rows due now, suppressed rows not reported yet)``, locked."""
    ready = or_(AlertDelivery.next_attempt_at.is_(None), AlertDelivery.next_attempt_at <= now)
    rows = session.scalars(
        select(AlertDelivery)
        .where(
            AlertDelivery.recipient == recipient,
            ready,
            or_(
                AlertDelivery.status == "queued",
                (AlertDelivery.status == "suppressed") & AlertDelivery.message_id.is_(None),
            ),
        )
        .order_by(AlertDelivery.id)
        .with_for_update(skip_locked=True)
    ).all()
    return (
        [row for row in rows if row.status == "queued"],
        [row for row in rows if row.status == "suppressed"],
    )


def _sent_in_window(session: Session, recipient: str, now: datetime) -> int:
    return int(
        session.scalar(
            select(func.count(func.distinct(AlertDelivery.message_id))).where(
                AlertDelivery.recipient == recipient,
                AlertDelivery.message_id.is_not(None),
                AlertDelivery.sent_at > now - RATE_WINDOW,
            )
        )
        or 0
    )


def _items(session: Session, rows: Sequence[AlertDelivery]) -> list[MailItem]:
    alerts = {
        alert.id: alert
        for alert in session.scalars(select(Alert).where(Alert.id.in_({r.alert_id for r in rows})))
    }
    items = []
    for row in rows:
        alert = alerts.get(row.alert_id)
        if alert is None:
            continue
        items.append(
            MailItem(
                alert_id=alert.id,
                kind=row.kind,
                rule=alert.rule,
                severity=alert.severity,
                state=alert.state,
                time=alert.time,
                resolved_at=alert.resolved_at,
                details=dict(alert.details or {}),
            )
        )
    # Most severe first, then by time.
    items.sort(key=lambda i: (-SEVERITY_RANK.get(i.severity, 0), i.time, i.alert_id))
    return items


@dataclass(frozen=True)
class _Leased:
    ids: tuple[int, ...]
    message: Any
    count: int


def _lease(
    sessions: sessionmaker[Session],
    policy: Policy,
    config: Config,
    recipient: str,
    report: NotifyReport,
    now: datetime,
) -> _Leased | None:
    """Pick and lease the rows of one e-mail to ``recipient`` (committed), or ``None``."""
    with sessions() as session:
        queued, unreported = _due(session, recipient, now)
        if not queued and not unreported:
            return None
        retry = any(row.attempts > 0 for row in [*queued, *unreported])
        oldest = min(row.created_at for row in [*queued, *unreported])
        if not retry and now - oldest < policy.digest:
            report.waiting.append(recipient)
            return None
        if _sent_in_window(session, recipient, now) >= policy.max_per_hour:
            for row in queued:
                row.status = "suppressed"
            report.suppressed += len(queued)
            session.commit()
            return None
        rows = [*queued, *unreported]
        suppressed = None
        if unreported:
            alerts = {
                alert.id: alert.rule
                for alert in session.scalars(
                    select(Alert).where(Alert.id.in_({r.alert_id for r in unreported}))
                )
            }
            suppressed = Suppressed(
                count=len(unreported),
                limit=policy.max_per_hour,
                rules=dict(Counter(alerts.get(r.alert_id, "?") for r in unreported)),
            )
        items = _items(session, queued)
        if not items and suppressed is None:  # nothing left to say (cannot normally happen)
            for row in queued:
                row.status, row.last_error = "failed", "alert not found"
            session.commit()
            return None
        message = build_message(
            items,
            sender=str(config.smtp.from_address),
            recipient=recipient,
            web_url=policy.web_url,
            now=now,
            suppressed=suppressed,
        )
        for row in rows:
            row.attempts += 1
            row.next_attempt_at = now + backoff(row.attempts)
        session.commit()
        return _Leased(tuple(row.id for row in rows), message, len(queued))


def _record(
    sessions: sessionmaker[Session],
    leased: _Leased,
    recipient: str,
    error: SmtpSendError | None,
    report: NotifyReport,
    now: datetime,
) -> None:
    with sessions() as session:
        rows = session.scalars(
            select(AlertDelivery).where(AlertDelivery.id.in_(leased.ids)).with_for_update()
        ).all()
        if error is None:
            message_id = str(leased.message["Message-ID"])
            for row in rows:
                if row.status == "queued":
                    row.status = "sent"
                row.sent_at, row.message_id, row.last_error = now, message_id, None
            report.emails += 1
            report.sent += leased.count
            _recipient_works(session, recipient, now)
            session.commit()
            return
        give_up = error.permanent or any(row.attempts >= MAX_ATTEMPTS for row in rows)
        for row in rows:
            row.last_error = str(error)
            if give_up:
                row.status = "failed"
        report.errors.append(f"{recipient}: {error}")
        if give_up:
            report.failed += len(rows)
            _recipient_fails(session, recipient, error, len(rows), now)
        session.commit()


def _recipient_fails(
    session: Session, recipient: str, error: SmtpSendError, count: int, now: datetime
) -> None:
    """The one local ``R20-email`` alert (never e-mailed), listing the failing recipients."""
    existing = active_alert(session, EMAIL_RULE)
    failing = set((existing.details or {}).get("recipients") or []) if existing else set()
    open_alert(
        session,
        rule=EMAIL_RULE,
        severity="high",
        title=(
            f"Uyarı e-postası gönderilemedi ({count} uyarı, {recipient}): {error}. "
            "[smtp] ayarlarını denetleyin: arcilogger-worker alerts test-email."
        ),
        details={
            "recipient": recipient,
            "recipients": sorted(failing | {recipient}),
            "alerts": count,
            "error": str(error),
            "permanent": error.permanent,
            "title_en": f"Alert e-mail could not be sent ({count} alerts)",
        },
        key=EMAIL_RULE,
        time=now,
        repeat=True,
        now=now,
    )


def _recipient_works(session: Session, recipient: str, now: datetime) -> None:
    """An e-mail to ``recipient`` went out: ``R20-email`` is resolved once no recipient it
    names is failing any more."""
    existing = active_alert(session, EMAIL_RULE)
    if existing is None:
        return
    failing = set((existing.details or {}).get("recipients") or []) - {recipient}
    if failing:
        existing.details = {**(existing.details or {}), "recipients": sorted(failing)}
    else:
        resolve_alert(existing, reason="email_sent", now=now)


def _forget_removed(session: Session, policy: Policy, now: datetime) -> None:
    """A recipient taken out of ``alerts.email_recipients`` is never sent to again:
    ``R20-email`` stops naming it, and is resolved once it names no listed recipient."""
    existing = active_alert(session, EMAIL_RULE)
    if existing is None:
        return
    named = set((existing.details or {}).get("recipients") or [])
    failing = named & set(policy.recipients)
    if failing == named:
        return
    if failing:
        existing.details = {**(existing.details or {}), "recipients": sorted(failing)}
    else:
        resolve_alert(existing, reason="recipient_removed", now=now)


def smtp_password(config: Config) -> str | None:
    """``smtp.password`` from the secret store (only when ``[smtp] username`` is set)."""
    if not config.smtp.username:
        return None
    store = SecretStore(config.secrets_dir / "secret.key", config.secrets_dir / "secrets.json")
    return store.get(SMTP_PASSWORD)


def deliver(
    sessions: sessionmaker[Session],
    policy: Policy,
    config: Config,
    sender: Sender,
    report: NotifyReport,
    *,
    now: datetime,
) -> None:
    for recipient in policy.recipients:
        leased = _lease(sessions, policy, config, recipient, report, now)
        if leased is None:
            continue
        error: SmtpSendError | None = None
        try:
            sender.send(leased.message, recipient)
        except SmtpSendError as exc:
            error = exc
            log.warning("alert e-mail to %s failed: %s", recipient, exc)
        _record(sessions, leased, recipient, error, report, now)
        if error is not None and not error.recipient:
            # The server itself failed (connection, timeout, TLS, authentication): the other
            # recipients wait for the next run instead of holding the worker loop for one
            # timeout each.
            break


def run_notify(
    sessions: sessionmaker[Session],
    config: Config,
    *,
    now: datetime,
    sender_factory: Callable[[Config], Sender] | None = None,
) -> NotifyReport:
    """One run of ``alerts.notify`` (see the module docstring)."""
    report = NotifyReport()
    if not config.smtp.enabled:
        report.skipped = "[smtp] enabled = false"
        return report
    with sessions() as session:
        policy = Policy.load(session, config)
        _forget_removed(session, policy, now)
        if not policy.recipients:
            report.skipped = "alerts.email_recipients is empty"
            _retire(session, policy, now=now)
            session.commit()
            return report
        report.queued, report.queued_resolved = enqueue(session, policy, now=now)
        session.commit()
    if sender_factory is not None:
        sender = sender_factory(config)
    else:
        try:
            password = smtp_password(config)
        except (SecretStoreError, OSError, ValueError) as exc:
            # Counted like a failed attempt of every due e-mail (retried, then R20-email).
            sender = _Failing(SmtpSendError(f"smtp.password: {exc}", permanent=False))
        else:
            sender = SmtpSender(config.smtp, password)
    deliver(sessions, policy, config, sender, report, now=now)
    return report


@dataclass(frozen=True)
class _Failing:
    error: SmtpSendError

    def send(self, message: Any, recipient: str) -> None:
        raise self.error
