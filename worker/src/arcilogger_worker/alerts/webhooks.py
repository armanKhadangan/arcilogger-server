"""``alerts.webhooks`` — alerts to the webhooks of ``[[webhooks]]`` (every minute, after
``alerts.notify``; module ``security_rules``; Wave 6 contract §2.4.2).

1. **Start.** The first run that sees an enabled webhook records its start
   (``state/webhooks.json``). Alerts opened before it are never sent: no backfill, also not
   after the webhook was switched off and on again (its start is forgotten while it is off).
2. **Queue.** For each enabled webhook one ``alert_deliveries`` row (``kind = 'webhook'``,
   ``recipient`` = its name, decision D10) per alert opened since its start and in the last
   :data:`LOOKBACK` whose severity reaches ``min_severity``, whose rule is listed in ``rules``
   (empty: every rule) and whose ``details.actions.webhook`` is not ``false`` (alerts without
   actions, R15 and R20 among them, count as ``true``). ``R20-webhook:<name>`` is never queued
   for ``<name>`` itself. ``(alert_id, kind, recipient)`` is unique: queued once.
3. **Lease, send, record.** One alert per request, oldest first, at most :data:`BATCH` per
   webhook and run: the row gets ``attempts + 1`` and its next attempt time, committed; then the
   ``POST``; then the outcome is committed. When the target cannot be reached at all (no HTTP
   status), the webhook's other rows wait for the next run.
4. **Retry.** A failed attempt is tried again after 1, 5, 15 and 60 minutes, then every hour,
   until :data:`GIVE_UP` after the first attempt (the row is queued and tried in the same run);
   a 4xx reply other than 408 and 429 fails at once. A failed row opens ``R20-webhook:<name>``
   (high); the webhook's next ``sent`` row resolves it.
5. **Removed or switched off.** Queued rows of a webhook that is no longer enabled in
   ``[[webhooks]]`` become ``failed`` with :data:`RETIRED`, and its ``R20-webhook`` is resolved.

``last_error`` is :func:`arcilogger_core.webhook.error_text`'s: the status code and at most 300
characters of the reply, without the URL's path or query, the secret or any header value. The
payloads carry the alert's titles and link, never a person's name or a log line (§2.4.3).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final
from urllib.parse import urlsplit

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import (
    WEBHOOK_SECRET_LENGTHS,
    Config,
    WebhookConfig,
    webhook_url_problem,
)
from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStore, SecretStoreError
from arcilogger_core.timeutil import isoformat_site, require_aware
from arcilogger_core.webhook import Target, WebhookAlert, WebhookError, encode, payload, send
from arcilogger_db.models import ALERT_SEVERITIES, Alert, AlertDelivery
from arcilogger_db.settings import get_setting
from arcilogger_worker import __version__
from arcilogger_worker.alerts.notify import web_base_url
from arcilogger_worker.alerts.store import SEVERITY_RANK, active_alert, open_alert, resolve

log = logging.getLogger("arcilogger_worker.alerts")

KIND: Final = "webhook"
WEBHOOK_RULE: Final = "R20-webhook"
#: Waits after the 1st, 2nd, 3rd, 4th failed attempt; every later one waits the last.
RETRY: Final = (
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
)
#: A delivery not sent this long after its first attempt is ``failed``.
GIVE_UP: Final = timedelta(hours=24)
#: Only alerts opened this recently are queued (a worker that was stopped for days does not
#: send what is long past).
LOOKBACK: Final = timedelta(hours=24)
#: Most requests to one webhook in one run.
BATCH: Final = 20
STATE_FILE: Final = "webhooks.json"
RETIRED: Final = "webhook kaldırıldı ya da kapatıldı / webhook removed or switched off"

Send = Callable[[Target, bytes], int]


def dedup_key(name: str) -> str:
    return f"{WEBHOOK_RULE}:{name}"


def retry_after(attempts: int) -> timedelta:
    return RETRY[min(max(attempts, 1), len(RETRY)) - 1]


@dataclass
class WebhookReport:
    started: list[str] = field(default_factory=list)
    queued: int = 0
    sent: int = 0
    retried: int = 0
    failed: int = 0
    retired: int = 0
    errors: list[str] = field(default_factory=list)
    skipped: str | None = None

    def details(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "started": self.started,
            "queued": self.queued,
            "sent": self.sent,
            "retried": self.retried,
            "failed": self.failed,
            "retired": self.retired,
            "errors": self.errors[:20],
        }
        if self.skipped:
            result.update(_skipped=True, reason=self.skipped)
        return result


# -- 1. start -------------------------------------------------------------------------------------


def state_path(layout: Layout) -> Path:
    return layout.state / STATE_FILE


def load_starts(layout: Layout) -> dict[str, datetime]:
    """The start of every webhook seen enabled (an unreadable file: none, so nothing old is
    sent)."""
    try:
        data = json.loads(state_path(layout).read_text(encoding="utf-8"))
        started = data["started"]
        return {
            str(name): require_aware(datetime.fromisoformat(str(value)))
            for name, value in started.items()
        }
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.warning("%s unreadable, every webhook starts now: %s", STATE_FILE, exc)
        return {}


def save_starts(layout: Layout, starts: Mapping[str, datetime]) -> None:
    text = json.dumps(
        {"started": {name: isoformat_site(at) for name, at in sorted(starts.items())}},
        separators=(",", ":"),
    )
    path = state_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, text + "\n")


# -- 2. queue -------------------------------------------------------------------------------------


def _severities(minimum: str) -> list[str]:
    floor = SEVERITY_RANK[minimum]
    return [name for name in ALERT_SEVERITIES if SEVERITY_RANK[name] >= floor]


def enqueue(session: Session, webhook: WebhookConfig, since: datetime, now: datetime) -> int:
    """Queue the alerts ``webhook`` takes (caller commits); how many rows."""
    actions = Alert.details["actions"]["webhook"].astext
    query = select(Alert.id).where(
        Alert.created_at >= max(since, now - LOOKBACK),
        Alert.severity.in_(_severities(webhook.min_severity)),
        or_(Alert.dedup_key.is_(None), Alert.dedup_key != dedup_key(webhook.name)),
        or_(actions.is_(None), actions != "false"),
    )
    if webhook.rules:
        query = query.where(Alert.rule.in_(webhook.rules))
    ids = session.scalars(query.order_by(Alert.id)).all()
    if not ids:
        return 0
    rows = [
        {"alert_id": alert_id, "kind": KIND, "recipient": webhook.name, "created_at": now}
        for alert_id in ids
    ]
    statement = (
        insert(AlertDelivery)
        .values(rows)
        .on_conflict_do_nothing(index_elements=["alert_id", "kind", "recipient"])
        .returning(AlertDelivery.id)
    )
    return len(session.execute(statement).all())


def retire(session: Session, names: Sequence[str], now: datetime) -> int:
    """Queued rows of webhooks not in ``names`` (removed or switched off) become ``failed``;
    their ``R20-webhook`` is resolved."""
    rows = session.scalars(
        select(AlertDelivery)
        .where(
            AlertDelivery.kind == KIND,
            AlertDelivery.status == "queued",
            AlertDelivery.recipient.not_in(names or ("",)),
        )
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        row.status, row.last_error = "failed", RETIRED
    for alert in session.scalars(
        select(Alert).where(Alert.rule == WEBHOOK_RULE, Alert.state.in_(("open", "acknowledged")))
    ):
        name = (alert.details or {}).get("webhook")
        if isinstance(name, str) and name not in names:
            resolve(alert, reason="webhook_removed", now=now)
    return len(rows)


# -- 3.-4. send -----------------------------------------------------------------------------------


def target_of(webhook: WebhookConfig, config: Config, store: SecretStore | None) -> Target:
    """Where ``webhook`` sends; :class:`WebhookError` (to be retried) when its secret or URL
    is missing from the secret store or not usable."""
    url, secret = webhook.url, None
    try:
        if webhook.format == "generic":
            secret = store.get(webhook.secret_name) if store is not None else None
            if secret is None or len(secret) not in WEBHOOK_SECRET_LENGTHS:
                raise WebhookError(
                    f"{webhook.secret_name}: not set or not 32-256 characters "
                    f"(arcilogger-core secret set {webhook.secret_name})"
                )
        else:
            url = (store.get(webhook.url_name) if store is not None else None) or ""
            problem = webhook_url_problem(url) if url else "not set"
            if problem is not None:
                raise WebhookError(
                    f"{webhook.url_name}: {problem} (arcilogger-core secret set {webhook.url_name})"
                )
    except SecretStoreError as exc:
        raise WebhookError(f"secret store: {exc}") from None
    return Target(
        url=url,
        format=webhook.format,
        secret=secret,
        timeout_seconds=webhook.timeout_seconds,
        ca_file=webhook.ca_file,
        https_proxy=config.network.https_proxy,
    )


def webhook_alert(alert: Alert, web_url: str) -> WebhookAlert:
    details = alert.details or {}
    count = details.get("count")
    return WebhookAlert(
        id=alert.id,
        rule=alert.rule,
        severity=alert.severity,
        state=alert.state,
        title_tr=alert.title,
        title_en=str(details.get("title_en") or alert.title),
        time=alert.time,
        count=count if type(count) is int and count > 0 else 1,
        url=f"{web_url}/security?alert={alert.id}",
    )


@dataclass(frozen=True)
class _Leased:
    id: int
    body: bytes
    first_attempt: datetime


def _lease(
    sessions: sessionmaker[Session], webhook: WebhookConfig, web_url: str, now: datetime
) -> _Leased | None:
    """The oldest due row of ``webhook``, leased (committed), with its request body."""
    with sessions() as session:
        row = session.scalars(
            select(AlertDelivery)
            .where(
                AlertDelivery.kind == KIND,
                AlertDelivery.recipient == webhook.name,
                AlertDelivery.status == "queued",
                or_(AlertDelivery.next_attempt_at.is_(None), AlertDelivery.next_attempt_at <= now),
            )
            .order_by(AlertDelivery.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            return None
        alert = session.get(Alert, row.alert_id)
        if alert is None:  # cannot normally happen (deleted with the alert)
            row.status, row.last_error = "failed", "alert not found"
            session.commit()
            return _lease(sessions, webhook, web_url, now)
        site = urlsplit(web_url).hostname or web_url
        document = payload(
            webhook.format, webhook_alert(alert, web_url), version=__version__, site=site
        )
        row.attempts += 1
        row.next_attempt_at = now + retry_after(row.attempts)
        leased = _Leased(row.id, encode(document), row.created_at)
        session.commit()
        return leased


def _record(
    sessions: sessionmaker[Session],
    webhook: WebhookConfig,
    leased: _Leased,
    error: WebhookError | None,
    report: WebhookReport,
    now: datetime,
) -> None:
    with sessions() as session:
        row = session.get(AlertDelivery, leased.id, with_for_update=True)
        if row is None:
            return
        if error is None:
            row.status, row.sent_at, row.last_error = "sent", now, None
            report.sent += 1
            existing = active_alert(session, dedup_key(webhook.name))
            if existing is not None:
                resolve(existing, reason="webhook_sent", now=now)
            session.commit()
            return
        row.last_error = str(error)
        report.errors.append(f"{webhook.name}: {error}")
        if error.permanent or now - leased.first_attempt >= GIVE_UP:
            row.status = "failed"
            report.failed += 1
            _webhook_fails(session, webhook.name, error, now)
        else:
            report.retried += 1
        session.commit()


def _webhook_fails(session: Session, name: str, error: WebhookError, now: datetime) -> None:
    """``R20-webhook:<name>``: the webhook could not deliver (never sent to ``name`` itself)."""
    open_alert(
        session,
        rule=WEBHOOK_RULE,
        severity="high",
        title=f"Webhook teslim edilemiyor: {name}",
        details={
            "title_en": f"Webhook cannot deliver: {name}",
            "webhook": name,
            "error": str(error),
            "status": error.status,
            "permanent": error.permanent,
        },
        key=dedup_key(name),
        time=now,
        repeat=True,
        now=now,
    )


def deliver(
    sessions: sessionmaker[Session],
    webhook: WebhookConfig,
    target: Target | WebhookError,
    web_url: str,
    report: WebhookReport,
    *,
    now: datetime,
    sender: Send,
) -> None:
    for _ in range(BATCH):
        leased = _lease(sessions, webhook, web_url, now)
        if leased is None:
            return
        error: WebhookError | None = target if isinstance(target, WebhookError) else None
        if not isinstance(target, WebhookError):
            try:
                sender(target, leased.body)
            except WebhookError as exc:
                error = exc
        if error is not None:
            log.warning("webhook %s: %s", webhook.name, error)
        _record(sessions, webhook, leased, error, report, now)
        if error is not None and error.status is None:
            return  # unreachable or not configured: the other rows wait for the next run


def _secret_store(config: Config) -> SecretStore | None:
    try:
        return SecretStore(config.secrets_dir / "secret.key", config.secrets_dir / "secrets.json")
    except (SecretStoreError, OSError, ValueError) as exc:
        log.warning("secret store unreadable for the webhooks: %s", exc)
        return None


def run_webhooks(
    sessions: sessionmaker[Session],
    config: Config,
    layout: Layout,
    *,
    now: datetime,
    sender: Send | None = None,
) -> WebhookReport:
    """One run of ``alerts.webhooks`` (see the module docstring)."""
    report = WebhookReport()
    enabled = [webhook for webhook in config.webhooks if webhook.enabled]
    names = [webhook.name for webhook in enabled]
    starts = load_starts(layout)
    known = {name: at for name, at in starts.items() if name in names}
    report.started = [name for name in names if name not in known]
    known.update(dict.fromkeys(report.started, now))
    if known != starts:
        save_starts(layout, known)
    with sessions() as session:
        report.retired = retire(session, names, now)
        for webhook in enabled:
            if webhook.name not in report.started:
                report.queued += enqueue(session, webhook, known[webhook.name], now)
        web_url = web_base_url(get_setting(session, "alerts.web_base_url"), config)
        session.commit()
    if not enabled:
        report.skipped = "no enabled [[webhooks]]"
        return report
    store = _secret_store(config)
    post = sender or (lambda target, body: send(target, body, version=__version__, now=now))
    for webhook in enabled:
        try:
            target: Target | WebhookError = target_of(webhook, config, store)
        except WebhookError as exc:
            target = exc
        deliver(sessions, webhook, target, web_url, report, now=now, sender=post)
    return report


def counts(session: Session, name: str, since: datetime) -> dict[str, int]:
    """Deliveries of ``name`` created since ``since``, by status (the API's 7-day counts)."""
    rows = session.execute(
        select(AlertDelivery.status, func.count())
        .where(
            AlertDelivery.kind == KIND,
            AlertDelivery.recipient == name,
            AlertDelivery.created_at >= since,
        )
        .group_by(AlertDelivery.status)
    ).all()
    return {str(status): int(number) for status, number in rows}
