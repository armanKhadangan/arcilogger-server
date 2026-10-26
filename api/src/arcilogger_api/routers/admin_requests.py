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

Reading a request brings its ``admin_requests`` row up to date
(:meth:`arcilogger_api.admin_channel.Channel.view`), so the history outlives the agent's own
30-day cleanup of its status files.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from pydantic import BaseModel
from sqlalchemy import select, tuple_

from arcilogger_api.admin_channel import AdminRequestOut, ChannelDep, find_row
from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, require
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
