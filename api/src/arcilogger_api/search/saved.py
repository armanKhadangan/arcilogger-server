"""Personal saved searches (contract 2.11, owner decision D10).

A saved search is a name, a query text and a time range — a preset (``15m`` … ``30d``, resolved
against the current time when it runs) or a fixed ``[from, to)``. Only its owner sees, changes
or runs it: another user's id answers 404 ``saved_search_not_found`` (nothing tells that it
exists). At most ``search.saved_max_per_user`` (100) per user (409 ``saved_search_limit``);
names are unique per owner ignoring case (409 ``saved_search_name_taken``; compared with
PostgreSQL's ``lower()``, as the unique index ``uq_saved_searches_owner_name`` does). The query
must parse when it is saved; VLANs and departments are bound only when it runs.

Running one is ``POST /search`` with ``saved_id``: the server takes the query and the range from
the row, sets ``last_run_at``, and ``search.run`` carries ``saved_id``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, literal, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arcilogger_api.search.gate import check_range
from arcilogger_api.search.grammar import parse_query
from arcilogger_api.search.messages import SearchQueryError, query_error, search_error
from arcilogger_api.search.schemas import FixedRange, PresetRange, SavedSearchIn
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.models import SavedSearch

#: Presets that end now: how far back they reach.
SPANS: Final = {
    "15m": timedelta(minutes=15),
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "7d": timedelta(days=7),
    "30d": timedelta(days=30),
}


def resolve_preset(preset: str, now: datetime) -> tuple[datetime, datetime]:
    """``[from, to)`` of a preset at ``now`` (``today``: since site midnight; ``yesterday``: the
    whole previous site day)."""
    if preset in SPANS:
        return now - SPANS[preset], now
    today = site_day(now)
    if preset == "today":
        start = day_window(today)[0]
        return start, max(now, start + timedelta(seconds=1))
    if preset == "yesterday":
        return day_window(today - timedelta(days=1))
    raise ValueError(f"unknown preset {preset!r}")


def range_of(row: SavedSearch, now: datetime | None = None) -> tuple[datetime, datetime]:
    """The time range a saved search runs on."""
    if row.range_preset is not None:
        return resolve_preset(row.range_preset, now or utc_now())
    if row.range_from is None or row.range_to is None:  # pragma: no cover - a check constraint
        raise ValueError("saved search without a range")
    return row.range_from, row.range_to


def describe(row: SavedSearch) -> dict[str, Any]:
    """``SavedSearch`` of a row."""
    range_: dict[str, Any] = (
        {"preset": row.range_preset}
        if row.range_preset is not None
        else {"from": row.range_from, "to": row.range_to}
    )
    return {
        "id": row.id,
        "name": row.name,
        "q": row.query,
        "range": range_,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "last_run_at": row.last_run_at,
    }


def own(session: Session, owner_id: int, saved_id: int) -> SavedSearch:
    """The user's saved search; 404 ``saved_search_not_found`` for another user's or none."""
    row = session.get(SavedSearch, saved_id)
    if row is None or row.owner_id != owner_id:
        raise search_error("saved_search_not_found")
    return row


def list_own(session: Session, owner_id: int) -> list[SavedSearch]:
    return list(
        session.scalars(
            select(SavedSearch)
            .where(SavedSearch.owner_id == owner_id)
            .order_by(func.lower(SavedSearch.name), SavedSearch.id)
        )
    )


def _check(body: SavedSearchIn, max_days: int) -> None:
    try:
        parse_query(body.q)
    except SearchQueryError as exc:
        raise query_error(exc) from None
    if isinstance(body.range, FixedRange):
        check_range(body.range.from_, body.range.to, max_days)


def _name_taken(session: Session, owner_id: int, name: str, saved_id: int | None) -> bool:
    query = select(SavedSearch.id).where(
        SavedSearch.owner_id == owner_id,
        func.lower(SavedSearch.name) == func.lower(literal(name)),
    )
    if saved_id is not None:
        query = query.where(SavedSearch.id != saved_id)
    return session.scalar(query.limit(1)) is not None


def _apply(row: SavedSearch, body: SavedSearchIn) -> None:
    row.name = body.name
    row.query = body.q
    if isinstance(body.range, PresetRange):
        row.range_preset, row.range_from, row.range_to = body.range.preset, None, None
    else:
        row.range_preset, row.range_from, row.range_to = None, body.range.from_, body.range.to


def _flush(session: Session) -> None:
    """Flush in a savepoint: a name taken at the same moment is 409, not 500."""
    try:
        with session.begin_nested():
            session.flush()
    except IntegrityError:
        raise search_error("saved_search_name_taken") from None


def create(
    session: Session, owner_id: int, body: SavedSearchIn, *, limit: int, max_days: int
) -> SavedSearch:
    _check(body, max_days)
    count = session.scalar(
        select(func.count()).select_from(SavedSearch).where(SavedSearch.owner_id == owner_id)
    )
    if (count or 0) >= limit:
        raise search_error("saved_search_limit", {"n": limit})
    if _name_taken(session, owner_id, body.name, None):
        raise search_error("saved_search_name_taken")
    row = SavedSearch(owner_id=owner_id)
    _apply(row, body)
    session.add(row)
    _flush(session)
    session.refresh(row)
    return row


def update(
    session: Session, owner_id: int, saved_id: int, body: SavedSearchIn, *, max_days: int
) -> SavedSearch:
    row = own(session, owner_id, saved_id)
    _check(body, max_days)
    if _name_taken(session, owner_id, body.name, saved_id):
        raise search_error("saved_search_name_taken")
    _apply(row, body)
    row.updated_at = utc_now()
    _flush(session)
    session.refresh(row)
    return row


def delete(session: Session, owner_id: int, saved_id: int) -> SavedSearch:
    row = own(session, owner_id, saved_id)
    session.delete(row)
    session.flush()
    return row
