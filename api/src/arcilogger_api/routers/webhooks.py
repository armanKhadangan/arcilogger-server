"""``/api/v1/webhooks`` — the alert webhooks (Wave 6 contract §2.4.4; W6-H).

The webhooks are configured **on the server only** (``[[webhooks]]`` in ``arcilogger.toml`` and
the secret store, decision D4): the browser lists them, shows their deliveries and starts a test.

* ``GET /webhooks`` (``alerts.read``) — every ``[[webhooks]]`` entry of the configuration file as it
  is now: name, format, host (never the URL's path or query; for ``teams`` and ``slack`` the host
  of the stored URL, ``""`` when it cannot be read), ``min_severity``, ``rules``, ``enabled``,
  whether it is ``ready`` to send and, if not, why (``reason``: ``disabled``, ``secret_missing``,
  ``url_missing``; a code only, the page has the texts), its last delivery and the deliveries of
  the last 7 days by status.
* ``GET /webhooks/{name}/deliveries?limit=50`` (``alerts.read``) — its newest deliveries
  (``alert_deliveries`` with ``kind = 'webhook'``, decision D10). ``last_error`` is the worker's
  text: the status code and at most 300 characters of the reply, without the URL's query, the
  secret or any header value.

``webhook_not_found`` (404) for a name that is not in ``[[webhooks]]``. Reads are not audited
(like the alert list).
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Final, Literal
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_api.admin_channel import Channel, ChannelDep
from arcilogger_api.auth.schemas import StrictModel
from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, require
from arcilogger_core.config import WebhookConfig
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import Alert, AlertDelivery

router = APIRouter(prefix="/webhooks", tags=["webhooks"])
ReaderDep = Annotated[CurrentUser, Depends(require("alerts.read"))]

KIND: Final = "webhook"
COUNT_DAYS: Final = 7
DELIVERIES_MAX: Final = 200
NOT_FOUND: Final = ("Böyle bir webhook yok.", "No such webhook.")

WebhookFormat = Literal["generic", "teams", "slack"]
DeliveryStatus = Literal["queued", "sent", "failed"]
#: Why a webhook does not send (``GET /webhooks``): switched off in ``[[webhooks]]``, its HMAC
#: secret (``generic``) or its URL (``teams``, ``slack``) not in the secret store.
NotReady = Literal["disabled", "secret_missing", "url_missing"]


class WebhookLastDelivery(StrictModel):
    time: datetime
    status: DeliveryStatus


class WebhookCounts(StrictModel):
    sent: int
    failed: int
    queued: int


class WebhookOut(StrictModel):
    name: str
    format: WebhookFormat
    #: The URL's host only: never its path, query or a secret.
    host: str
    min_severity: str
    #: Empty: every rule, R15 and R20 included.
    rules: list[str]
    enabled: bool
    #: Enabled and its secret or URL stored (the worker then sends).
    ready: bool
    reason: NotReady | None
    last_delivery: WebhookLastDelivery | None
    counts_7d: WebhookCounts


class WebhookList(StrictModel):
    webhooks: list[WebhookOut]


class WebhookDelivery(StrictModel):
    alert_id: int
    rule: str
    severity: str
    status: DeliveryStatus
    attempts: int
    last_error: str | None
    next_attempt_at: datetime | None
    created_at: datetime
    sent_at: datetime | None


class WebhookDeliveries(StrictModel):
    name: str
    items: list[WebhookDelivery]


def not_found() -> ApiError:
    return ApiError(404, "webhook_not_found", *NOT_FOUND)


def configured(channel: Channel, name: str) -> WebhookConfig:
    """The ``[[webhooks]]`` entry ``name`` of the configuration file now, or 404."""
    found = next((w for w in channel.files.config.webhooks if w.name == name), None)
    if found is None:
        raise not_found()
    return found


def readiness(webhook: WebhookConfig, stored: frozenset[str]) -> NotReady | None:
    if not webhook.enabled:
        return "disabled"
    if webhook.format == "generic":
        return None if webhook.secret_name in stored else "secret_missing"
    return None if webhook.url_name in stored else "url_missing"


def host_of(webhook: WebhookConfig, channel: Channel) -> str:
    """The target host: from ``url`` (``generic``) or from the stored URL (``teams``,
    ``slack``; ``""`` when it is not stored or cannot be read). Nothing else of the URL."""
    url = webhook.url
    if webhook.format != "generic":
        if webhook.url_name not in channel.files.stored:
            return ""
        try:
            url = channel.store().get(webhook.url_name) or ""
        except (OSError, ValueError, SecretStoreError):
            return ""
    try:
        return urlsplit(url).hostname or ""
    except ValueError:
        return ""


def _statuses(db: Session, since: datetime) -> dict[str, dict[str, int]]:
    rows = db.execute(
        select(AlertDelivery.recipient, AlertDelivery.status, func.count())
        .where(AlertDelivery.kind == KIND, AlertDelivery.created_at >= since)
        .group_by(AlertDelivery.recipient, AlertDelivery.status)
    ).all()
    found: dict[str, dict[str, int]] = {}
    for recipient, status, number in rows:
        found.setdefault(str(recipient), {})[str(status)] = int(number)
    return found


def _last(db: Session) -> dict[str, WebhookLastDelivery]:
    """The newest delivery of every webhook (its sending time, else its queueing time)."""
    moment = func.coalesce(AlertDelivery.sent_at, AlertDelivery.created_at)
    newest = (
        select(AlertDelivery.recipient, AlertDelivery.status, moment.label("at"))
        .where(AlertDelivery.kind == KIND)
        .distinct(AlertDelivery.recipient)
        .order_by(AlertDelivery.recipient, moment.desc(), AlertDelivery.id.desc())
    )
    return {
        str(recipient): WebhookLastDelivery(time=at, status=status)
        for recipient, status, at in db.execute(newest).all()
    }


@router.get("", response_model=WebhookList)
def list_webhooks(db: DbSession, user: ReaderDep, channel: ChannelDep) -> WebhookList:
    """The webhooks of ``[[webhooks]]`` with their readiness and deliveries."""
    files = channel.files
    counts = _statuses(db, utc_now() - timedelta(days=COUNT_DAYS))
    last = _last(db)
    items = []
    for webhook in files.config.webhooks:
        reason = readiness(webhook, files.stored)
        found = counts.get(webhook.name, {})
        items.append(
            WebhookOut(
                name=webhook.name,
                format=webhook.format,
                host=host_of(webhook, channel),
                min_severity=webhook.min_severity,
                rules=list(webhook.rules),
                enabled=webhook.enabled,
                ready=reason is None,
                reason=reason,
                last_delivery=last.get(webhook.name),
                counts_7d=WebhookCounts(
                    sent=found.get("sent", 0),
                    failed=found.get("failed", 0),
                    queued=found.get("queued", 0),
                ),
            )
        )
    return WebhookList(webhooks=items)


@router.get("/{name}/deliveries", response_model=WebhookDeliveries)
def deliveries(
    db: DbSession,
    user: ReaderDep,
    channel: ChannelDep,
    name: Annotated[str, Path(pattern=r"^[a-z0-9-]{1,32}$")],
    limit: Annotated[int, Query(ge=1, le=DELIVERIES_MAX)] = 50,
) -> WebhookDeliveries:
    """The newest deliveries of one webhook."""
    configured(channel, name)
    rows = db.execute(
        select(AlertDelivery, Alert.rule, Alert.severity)
        .join(Alert, Alert.id == AlertDelivery.alert_id)
        .where(AlertDelivery.kind == KIND, AlertDelivery.recipient == name)
        .order_by(AlertDelivery.created_at.desc(), AlertDelivery.id.desc())
        .limit(limit)
    ).all()
    return WebhookDeliveries(
        name=name,
        items=[
            WebhookDelivery(
                alert_id=row.alert_id,
                rule=rule,
                severity=severity,
                status=row.status,
                attempts=row.attempts,
                last_error=row.last_error,
                next_attempt_at=row.next_attempt_at,
                created_at=row.created_at,
                sent_at=row.sent_at,
            )
            for row, rule, severity in rows
        ],
    )
