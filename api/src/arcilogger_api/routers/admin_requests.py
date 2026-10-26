"""``/api/v1/admin/requests`` — Ayarlar → Geçmiş: the requests of the browser administration
(ADR-0014; contract 2.10, 2.11.2).

Every change of the machine configuration and every action ("Şimdi mühürle", "Kontör sorgula",
the stamp, replica and e-mail tests, the lease rebuild) is a request with a state (contract
2.10: ``queued`` … ``active``, ``succeeded``, ``failed`` …), its steps, the masked changes and,
for an action, its result. The web page follows one with ``GET /admin/requests/{id}`` (every
second for 15 s, then every 3 s, ``X-Arci-Background: 1``) until a terminal state.

* ``GET /admin/requests?kind=&cursor=&limit=`` — newest first (``settings.manage``);
* ``GET /admin/requests/{id}`` — one request (``settings.manage``; ``seal.manage`` is enough for
  one's own ``action.seal`` and ``action.credit``).
* ``DELETE /admin/requests/{id}`` — cancel (``settings.manage``): a request the agent has not
  taken yet is removed from the inbox by the API (``cancelled`` at once; a queued worker row
  likewise); a held change (``pending``) gets a signed ``config.cancel`` (re-authentication),
  after which the agent reports the target ``cancelled``. Anything else is 409
  ``request_not_cancellable``. Audited ``admin.request_cancel {state_before}``.
* ``POST /admin/requests/{id}/revert`` — "Geri al" (``settings.manage``, re-authentication): a
  signed ``config.revert`` of an applied configuration change; the agent builds the inverse from
  the version it saved before the change, restricted to the paths the change touched, and puts
  it through the same rules. It keeps the target's section busy. 409 ``request_not_revertible``
  for anything but an applied change with a version.

Reading a request brings its ``admin_requests`` row up to date
(:meth:`arcilogger_api.admin_channel.Channel.view`), so the history outlives the agent's own
30-day cleanup of its status files.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select, tuple_
from sqlalchemy.orm import Session

from arcilogger_api.admin_channel import (
    AdminAccepted,
    AdminRequestOut,
    ChannelDep,
    admin_error,
    find_row,
    require_reauth,
)
from arcilogger_api.audit import record_audit
from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_core.admin.contract import RequestKind, WorkerKind
from arcilogger_db.models import AdminRequest as AdminRequestRow

router = APIRouter(prefix="/admin/requests", tags=["admin"])

SettingsDep = Annotated[CurrentUser, Depends(require("settings.manage"))]
SignedInDep = Annotated[CurrentUser, Depends(require())]
RequestId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$", description="Request id")]
#: The actions a user with ``seal.manage`` (and without ``settings.manage``) may follow.
SEAL_ACTIONS = ("action.seal", "action.credit")


class AdminRequestPage(BaseModel):
    items: list[AdminRequestOut]
    #: Pass as ``cursor`` for the next page; ``null`` on the last page.
    next_cursor: str | None


@router.get("")
def list_requests(
    db: DbSession,
    user: SettingsDep,
    channel: ChannelDep,
    kind: RequestKind | WorkerKind | None = None,
    cursor: Annotated[str | None, Query(pattern=r"^[0-9a-f]{32}$")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> AdminRequestPage:
    """Requests, newest first (cursor: the id of the last request of the previous page)."""
    query = select(AdminRequestRow).order_by(
        AdminRequestRow.created_at.desc(), AdminRequestRow.id.desc()
    )
    if kind is not None:
        query = query.where(AdminRequestRow.kind == kind)
    if cursor is not None:
        last = db.get(AdminRequestRow, cursor)
        if last is not None:
            query = query.where(
                tuple_(AdminRequestRow.created_at, AdminRequestRow.id)
                < tuple_(last.created_at, last.id)
            )
    rows = list(db.scalars(query.limit(limit + 1)))
    more = len(rows) > limit
    rows = rows[:limit]
    return AdminRequestPage(
        items=[channel.view(db, row) for row in rows],
        next_cursor=rows[-1].id if more and rows else None,
    )


@router.get("/{request_id}")
def get_request(
    request_id: RequestId, db: DbSession, user: SignedInDep, channel: ChannelDep
) -> AdminRequestOut:
    """One request with its steps (``AdminRequestOut``)."""
    row = find_row(db, request_id)
    own_action = (
        user.has("seal.manage") and row.kind in SEAL_ACTIONS and row.requested_by_id == user.id
    )
    if not user.has("settings.manage") and not own_action:
        raise ApiError(403, "forbidden")
    return channel.view(db, row)


#: States in which the agent has not taken a request yet (its file is still in the inbox).
UNCLAIMED = ("queued", "waiting_for_agent")
#: A configuration change these states report applied can be reverted.
REVERTIBLE = ("applied", "active", "partially_active")


def _cancelled(db: Session, row: AdminRequestRow, now: datetime) -> None:
    row.state, row.terminal = "cancelled", True
    row.updated_at = now
    db.flush()


@router.delete("/{request_id}", status_code=status.HTTP_202_ACCEPTED)
def cancel_request(
    request_id: RequestId, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """Cancel a request that has not started, or a held change."""
    row = find_row(db, request_id)
    before = channel.view(db, row).state
    if row.channel == "worker":
        if before != "queued":
            raise admin_error("request_not_cancellable")
        _cancelled(db, row, channel.now)
    elif before in UNCLAIMED and channel.remove_queued(row.id):
        _cancelled(db, row, channel.now)
    elif channel.view(db, row).state == "pending":
        require_reauth(user, True)
        record_audit(
            db,
            actor=user,
            action="admin.request_cancel",
            target=f"admin-request:{row.id}",
            details={"state_before": "pending"},
            ip=client_ip(request),
        )
        return channel.submit(db, user, request, kind="config.cancel", options={"target": row.id})
    else:
        raise admin_error("request_not_cancellable")
    record_audit(
        db,
        actor=user,
        action="admin.request_cancel",
        target=f"admin-request:{row.id}",
        details={"state_before": before},
        ip=client_ip(request),
    )
    return channel.accepted(row, state="cancelled")


def _inverse(changes: Any) -> dict[str, dict[str, Any]]:
    """The target's changes the other way round (a secret stays ``set``/``unset`` only)."""
    found: dict[str, dict[str, Any]] = {}
    for item in changes if isinstance(changes, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("path"), str):
            continue
        change = {key: value for key, value in item.items() if key != "path"}
        change["old"], change["new"] = item.get("new"), item.get("old")
        found[item["path"]] = change
    return found


@router.post("/{request_id}/revert", status_code=status.HTTP_202_ACCEPTED)
def revert_request(
    request_id: RequestId, request: Request, db: DbSession, user: SettingsDep, channel: ChannelDep
) -> AdminAccepted:
    """Put back what an applied configuration change changed ("Geri al")."""
    row = find_row(db, request_id)
    view = channel.view(db, row)
    if (
        row.kind not in ("config.apply", "config.revert")
        or view.state not in REVERTIBLE
        or view.version is None
        or row.section is None
    ):
        raise admin_error("request_not_revertible")
    require_reauth(user, True)
    params = row.params or {}
    return channel.submit(
        db,
        user,
        request,
        kind="config.revert",
        options={"target": row.id},
        changes=_inverse(params.get("changes")),
        effects=[str(effect) for effect in params.get("effects") or []],
        busy_section=row.section,
    )
