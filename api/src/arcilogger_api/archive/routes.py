"""``/api/v1/archive-reloads`` — "arşivden yeniden yükle" (Wave 5 contract §5, G4 to G9).

* ``POST /archive-reloads`` with :class:`~.models.ArchiveReloadRequest` checks the request (at
  most 14 days, every day sealed in the vault, room on the database drive; 422
  ``archive_reload_too_large``, ``archive_day_not_in_vault``, ``archive_reload_disk_low``), writes
  the audit entry ``archive.reload`` (who, the days, the reason, the lookup) and then queues the
  request (202, :class:`~.models.ArchiveReload`, ``queued``). ArciIngest loads it.
* ``GET /archive-reloads/{id}`` — one request (the web app polls its progress).
* ``GET /archive-reloads`` — the active requests (``loading``, ``queued``, ``ready``), then the
  recent others.

**Who** (G6): starting a reload needs the permission of the function that asks — ``lookup.run``
for ``reason`` ``lookup`` (the 5651 lookup), ``search.read`` for ``search`` (the search page) —
and, like both, the accepted KVKK notice: a reload brings personal data back into the database
for ``db.reload_keep_days`` days. Reading requests needs either permission; administrators see
every request, everybody else only their own (another user's answers 404, as a lookup does). A
request holds no log content, so reading it is not audited. No licence gate (5651 Temel).

The router is mounted by ``routers/archive_reloads.py``.
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Path, Request, status
from sqlalchemy.orm import Session

from arcilogger_api.archive.models import ArchiveReload as ArchiveReloadOut
from arcilogger_api.archive.models import (
    ArchiveReloadList,
    ArchiveReloadReason,
    ArchiveReloadRequest,
)
from arcilogger_api.archive.service import create_reload, to_model, visible_reloads
from arcilogger_api.auth.privacy import ensure_privacy_notice
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.errors import ApiError
from arcilogger_api.routers.lookup import visible_lookup
from arcilogger_api.security import CurrentUser, CurrentUserDep, client_ip
from arcilogger_db.models import ArchiveReload

router = APIRouter(prefix="/archive-reloads", tags=["archive"])

#: The permission of the function that asks (G6).
PERMISSION: Final[dict[ArchiveReloadReason, str]] = {
    "lookup": "lookup.run",
    "search": "search.read",
}
#: Largest request number (PostgreSQL ``bigint``).
MAX_ID: Final = 2**63 - 1
ReloadId = Annotated[int, Path(ge=1, le=MAX_ID, description="Archive reload number")]


def _allowed(user: CurrentUser, *permissions: str) -> None:
    """403 as ``security.require`` answers it, unless ``user`` holds one of ``permissions``."""
    if user.must_change_password:
        raise ApiError(403, "password_change_required")
    if not any(user.has(permission) for permission in permissions):
        raise ApiError(403, "forbidden")


def _reader(user: CurrentUser) -> CurrentUser:
    _allowed(user, *PERMISSION.values())
    return user


def _visible(db: Session, user: CurrentUser, reload_id: int) -> ArchiveReload:
    row = db.get(ArchiveReload, reload_id)
    if row is None or not (user.role == "admin" or row.requested_by == user.username):
        raise ApiError(404, "not_found")
    return row


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def request_reload(
    body: ArchiveReloadRequest,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    user: CurrentUserDep,
) -> ArchiveReloadOut:
    """Queue a reload of sealed days (audited ``archive.reload`` before it is queued)."""
    _allowed(user, PERMISSION[body.reason])
    ensure_privacy_notice(db, user)
    if body.lookup_id is not None and body.reason == "lookup":
        visible_lookup(db, user, body.lookup_id)  # another user's lookup: 404
    row = create_reload(
        db, user=user, body=body, layout=layout, data_dir=config.data_dir, ip=client_ip(request)
    )
    return to_model(row)


@router.get("")
def list_reloads(db: DbSession, user: CurrentUserDep) -> ArchiveReloadList:
    """The active requests first (loading, queued, ready), then the recent others."""
    _reader(user)
    return ArchiveReloadList(items=[to_model(row) for row in visible_reloads(db, user)])


@router.get("/{reload_id}")
def get_reload(reload_id: ReloadId, db: DbSession, user: CurrentUserDep) -> ArchiveReloadOut:
    """One request with its progress (``bytes_done / bytes_total``)."""
    _reader(user)
    return to_model(_visible(db, user, reload_id))
