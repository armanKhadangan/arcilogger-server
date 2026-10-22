"""``/api/v1/lookups`` — list of 5651 lookups (spec 12.3 page 6, 16: cursor pagination).

Needs ``lookup.run`` and the accepted KVKK notice. Administrators see every lookup, everybody else
only their own (the rule of :func:`arcilogger_api.routers.lookup.sees_all_lookups`). The list
shows the request details and the result count, never the outcome (person-level data is opened
with ``GET /lookup/{id}``, which is audited), so listing is not audited — like the audit log
page. Newest first; ``cursor`` = ``next_cursor`` of the previous page.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import select

from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.routers.lookup import MAX_ID, RunnerDep, sees_all_lookups
from arcilogger_db.models import Lookup, LookupReport

router = APIRouter(prefix="/lookups", tags=["lookup"])


class LookupSummary(BaseModel):
    id: int
    created_at: datetime
    user_id: int | None
    username: str
    authority: str
    file_no: str
    request_date: date
    mode: str | None
    result_count: int
    #: SHA-256 of the official report, ``null`` until it was generated.
    report_sha256: str | None


class LookupPage(BaseModel):
    items: list[LookupSummary]
    next_cursor: int | None
    #: ``true`` when the list holds every user's lookups (administrators).
    all_users: bool


@router.get("")
def list_lookups(
    db: DbSession,
    user: RunnerDep,
    cursor: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    username: Annotated[str | None, Query(max_length=64)] = None,
    mine: bool = False,
    mode: Literal["exact", "candidates", "internal", "none"] | None = None,
    time_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    time_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
) -> LookupPage:
    """Lookups newest first (``from`` inclusive, ``to`` exclusive, times with offset)."""
    if time_from is not None and time_to is not None and time_from >= time_to:
        raise ApiError(422, "validation_error", details=[{"loc": ["query", "to"]}])
    all_users = sees_all_lookups(user) and not mine
    query = (
        select(Lookup, LookupReport.sha256)
        .outerjoin(LookupReport, LookupReport.lookup_id == Lookup.id)
        .order_by(Lookup.id.desc())
        .limit(limit + 1)
    )
    if not all_users:
        query = query.where(Lookup.user_id == user.id)
    if username:
        query = query.where(Lookup.username == username)
    if mode is not None:
        query = query.where(Lookup.mode == mode)
    if time_from is not None:
        query = query.where(Lookup.created_at >= time_from)
    if time_to is not None:
        query = query.where(Lookup.created_at < time_to)
    if cursor is not None:
        query = query.where(Lookup.id < cursor)
    rows = list(db.execute(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return LookupPage(
        items=[
            LookupSummary(
                id=row.id,
                created_at=row.created_at,
                user_id=row.user_id,
                username=row.username,
                authority=row.authority,
                file_no=row.file_no,
                request_date=row.request_date,
                mode=row.mode,
                result_count=row.result_count,
                report_sha256=sha256,
            )
            for row, sha256 in rows
        ],
        next_cursor=rows[-1][0].id if more else None,
        all_users=all_users,
    )
