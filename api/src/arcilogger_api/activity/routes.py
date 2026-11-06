"""``/api/v1/activity`` — Canlı Durum (Wave 5 contract §6.2): ``GET /activity/devices`` (M2).

Access (M3), as ``/usage/subjects``: ``usage.person`` or ``usage.anonymous`` with the accepted
KVKK notice, pseudonyms by the same rule (:func:`~arcilogger_api.routers.usage.viewer_for`), and
the paid module ``usage_analytics`` (QB1: the per-person table belongs to it, like Kullanım).

**Audit** ``activity.view`` (target ``activity:devices``, the window and the row count; never a
name): at a user's first request, then at most once every :data:`AUDIT_EVERY` while they keep
polling (the page polls every 10 s). The last entry is read from the audit log itself, so every
process of the API keeps the same pace.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Final

from fastapi import APIRouter, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api.activity.devices import devices_now
from arcilogger_api.activity.models import ActivityDevices, ActivityWindow
from arcilogger_api.audit import record_audit
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.errors import ApiError
from arcilogger_api.licensing import require_module
from arcilogger_api.routers.usage import UsageReader, viewer_for
from arcilogger_api.security import CurrentUser, client_ip
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import AuditLog

router = APIRouter(prefix="/activity", tags=["activity"])

#: The windows of ``GET /activity/devices``, in minutes, and the default.
WINDOWS: Final[tuple[ActivityWindow, ...]] = (1, 5, 15)
DEFAULT_WINDOW: Final[ActivityWindow] = 5
#: ``activity.view`` is written at most this often per user.
AUDIT_EVERY: Final = timedelta(minutes=15)
AUDIT_ACTION: Final = "activity.view"
AUDIT_TARGET: Final = "activity:devices"

MESSAGES: Final = {
    "activity_window_invalid": (
        "Zaman aralığı geçersiz: 1, 5 ya da 15 dakika olabilir.",
        "Invalid window: it can be 1, 5 or 15 minutes.",
    ),
}


def _error(status: int, code: str) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(status, code, text_tr, text_en)


def window_of(value: str | None) -> ActivityWindow:
    """The ``window`` parameter: 1, 5 or 15 minutes (5 when absent), else 422."""
    if value is None:
        return DEFAULT_WINDOW
    for window in WINDOWS:
        if value.strip() == str(window):
            return window
    raise _error(422, "activity_window_invalid")


def audit_due(db: Session, user: CurrentUser, now: datetime) -> bool:
    """Whether ``user`` has no ``activity.view`` entry younger than :data:`AUDIT_EVERY`."""
    newest = db.execute(
        select(AuditLog.time)
        .where(AuditLog.actor_id == user.id, AuditLog.action == AUDIT_ACTION)
        .order_by(AuditLog.time.desc())
        .limit(1)
    ).scalar_one_or_none()
    return newest is None or now - newest >= AUDIT_EVERY


@router.get("/devices", dependencies=[require_module("usage_analytics")])
def devices(
    request: Request,
    db: DbSession,
    user: UsageReader,
    config: ConfigDep,
    layout: LayoutDep,
    window: Annotated[
        str | None,
        Query(description="Minutes: 1, 5 or 15 (default 5).", examples=["5"]),
    ] = None,
) -> ActivityDevices:
    """Who is sending and receiving data over the last ``window`` minutes (M2)."""
    minutes = window_of(window)
    viewer = viewer_for(db, user, config)
    now = utc_now()
    answer = devices_now(db, viewer, minutes, now, layout.activity)
    if audit_due(db, user, now):
        record_audit(
            db,
            actor=user,
            action=AUDIT_ACTION,
            target=AUDIT_TARGET,
            details={"window": minutes, "rows": len(answer.rows)},
            ip=client_ip(request),
        )
    return answer
