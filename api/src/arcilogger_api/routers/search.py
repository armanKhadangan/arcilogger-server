"""``/api/v1/search`` — Arama: arama, istatistik, dışa aktarma (spec 12.3 page 4; ADR-0015).

One audited search, many follow-ups (contract 2.6.2, R3): ``POST /search`` parses the query,
plans it, runs its first page, keeps it under a ``search_id`` and writes ``search.run``. Its
pages (``POST /search/page``), statistics (``POST /search/stats``), evidence references
(``POST /search/refs``) and export (``POST /search/export``) take only that id, so every row,
count and file a user sees belongs to an audited search; pages and statistics are not audited
again (docs/ARAMA.md says so). A query never travels in a URL: everything that runs one is a
POST (no personal data in access logs, decision 10).

* **Access** (contract 2.18): ``search.read`` and the accepted KVKK notice (administrators and
  auditors); ``POST /search/parse`` and ``GET /search/fields`` need ``search.read`` only (no
  database rows). The export also needs ``search.export`` and a re-authentication within
  5 minutes (administrators), the evidence references ``evidence.create``. No licence gate
  (5651 Temel).
* **Read-only and bounded** (contract 2.5.1): the request's authentication transaction is
  committed, the request waits for a slot of ``search.max_concurrent`` (:mod:`~.search.gate`),
  then ``SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY`` opens one snapshot for every
  window, fill-in and count of the request, each statement under ``statement_deadline``; the
  transaction is rolled back and the audit entry written in a fresh one, committed **before**
  the response. No row leaves the API without its audit entry.
* **Saved searches** (``/search/saved``): personal (:mod:`~.search.saved`).

Errors are ``search.messages`` (Turkish first, with the span of a refused query in
``details``); the event drawer (``GET /search/event``) reads one row by its key and is audited
(``search.event``): a row that is not there is 404 ``search_event_not_found``, a read that ran out
of time 504 ``search_event_timeout`` (never "not found").
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from time import monotonic
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, FastAPI, Path, Query, Request, Response, status
from fastapi.responses import StreamingResponse
from pydantic import AwareDatetime
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session
from starlette.background import BackgroundTask

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep, TtlCache
from arcilogger_api.errors import ApiError
from arcilogger_api.search import export as exports
from arcilogger_api.search import refs as references
from arcilogger_api.search import saved as saved_searches
from arcilogger_api.search.cursor import decode, encode, utc_text
from arcilogger_api.search.fields import SiteNetwork, catalogue_fields, catalogue_kinds
from arcilogger_api.search.gate import (
    ExportLocks,
    RunLimiter,
    SearchGate,
    check_export_rate,
    check_range,
)
from arcilogger_api.search.grammar import LIMITS, parse_query
from arcilogger_api.search.grammar import Query as ParsedQuery
from arcilogger_api.search.messages import (
    SearchQueryError,
    error_item,
    notice,
    query_error,
    search_error,
)
from arcilogger_api.search.planner import (
    Plan,
    describe_clauses,
    describe_query,
    plan_query,
)
from arcilogger_api.search.registry import Entry, SearchRegistry, new_search_id
from arcilogger_api.search.schemas import (
    DEFAULT_LIMIT,
    FieldCatalogue,
    ParseRequest,
    ParseResult,
    SavedSearchIn,
    SavedSearchList,
    SearchEvent,
    SearchExportRequest,
    SearchIdRequest,
    SearchPage,
    SearchPageRequest,
    SearchRefs,
    SearchRefsRequest,
    SearchRequest,
    SearchStats,
)
from arcilogger_api.search.schemas import (
    SavedSearch as SavedSearchOut,
)
from arcilogger_api.search.stats import SiteLabels, run_stats
from arcilogger_api.search.windows import (
    Clock,
    Page,
    SearchTimeoutError,
    WindowHook,
    WindowTimeoutError,
    compression_horizons,
    display,
    page_rows,
    run_page,
    run_statement,
)
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_core.config import Config
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.kinds import kind_of, kind_of_row
from arcilogger_db.models import TIME_SERIES_TABLES, Base, Message, SavedSearch, Source, Vlan
from arcilogger_db.settings import get_settings

router = APIRouter(prefix="/search", tags=["search"])

ReaderDep = Annotated[CurrentUser, Depends(require_privacy_notice("search.read"))]
CatalogueDep = Annotated[CurrentUser, Depends(require("search.read"))]
ExporterDep = Annotated[
    CurrentUser, Depends(require_privacy_notice("search.read", "search.export", recent=True))
]
ReferrerDep = Annotated[
    CurrentUser, Depends(require_privacy_notice("search.read", "evidence.create"))
]

#: The first statement of every search transaction (the only SQL text of the search API besides
#: the deadline's ``set_config``): one snapshot, nothing written.
READ_ONLY: Final = "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"
#: How long the oldest ``messages.time`` (notice ``vault_only_before``) is kept.
OLDEST_SECONDS: Final = 600.0
#: How long the compression horizons (TimescaleDB, ``windows.compression_horizons``) are kept.
HORIZONS_SECONDS: Final = 60.0
#: Deadline of the single statements outside the window scan (event drawer, oldest row).
STATEMENT_SECONDS: Final = 4.0
MAX_ROW_ID: Final = 2**63 - 1
_STATE_ATTRIBUTE: Final = "arci_search"


@dataclass(frozen=True)
class Oldest:
    """The oldest device time in ``messages`` (``None``: no row)."""

    time: datetime | None


@dataclass(frozen=True)
class Horizons:
    """Per table, the end of its newest compressed chunk (empty: native partitions)."""

    tables: Mapping[str, datetime]


@dataclass
class SearchState:
    """The search API's in-process state (one Uvicorn worker, ADR-0001)."""

    registry: SearchRegistry = field(default_factory=SearchRegistry)
    gate: SearchGate = field(default_factory=SearchGate)
    runs: RunLimiter = field(default_factory=RunLimiter)
    exports: ExportLocks = field(default_factory=ExportLocks)
    oldest: TtlCache[Oldest] = field(default_factory=lambda: TtlCache[Oldest](OLDEST_SECONDS))
    horizons: TtlCache[Horizons] = field(
        default_factory=lambda: TtlCache[Horizons](HORIZONS_SECONDS)
    )
    #: The budget clock (seconds) and a hook run in every window (tests).
    clock: Clock = monotonic
    hook: WindowHook | None = None


_create_lock = threading.Lock()


def state_of(app: FastAPI) -> SearchState:
    """The application's search state, created on first use (tests set its clock and hook)."""
    found = getattr(app.state, _STATE_ATTRIBUTE, None)
    if found is None:
        with _create_lock:
            found = getattr(app.state, _STATE_ATTRIBUTE, None)
            if found is None:
                found = SearchState()
                setattr(app.state, _STATE_ATTRIBUTE, found)
    if not isinstance(found, SearchState):  # pragma: no cover - misconfiguration
        raise TypeError("app.state.arci_search must be SearchState")
    return found


def search_state(request: Request) -> SearchState:
    return state_of(request.app)


StateDep = Annotated[SearchState, Depends(search_state)]


# -- helpers --------------------------------------------------------------------------------------


def _settings(db: Session) -> dict[str, Any]:
    return get_settings(db, prefix="search.")


def _seconds(settings: dict[str, Any], key: str) -> float:
    return int(settings[key]) / 1000


@contextmanager
def reading(
    db: Session, state: SearchState, user: CurrentUser, settings: dict[str, Any]
) -> Iterator[None]:
    """A slot of the gate and one read-only snapshot (see the module text); rolled back."""
    db.commit()  # the authentication's transaction; a waiting request holds no connection
    with state.gate.slot(user.id, int(settings["search.max_concurrent"])):
        db.execute(text(READ_ONLY))
        try:
            yield
        finally:
            db.rollback()


def _parse(text_: str) -> ParsedQuery:
    try:
        return parse_query(text_)
    except SearchQueryError as exc:
        raise query_error(exc) from None


def _plan(query: ParsedQuery, site: SiteNetwork | None) -> Plan:
    try:
        return plan_query(query, site)
    except SearchQueryError as exc:
        raise query_error(exc) from None


def _check_limit(limit: int, settings: dict[str, Any]) -> None:
    maximum = int(settings["search.page_size_max"])
    if limit > maximum:
        raise ApiError(
            422,
            "validation_error",
            details=[
                {
                    "loc": ["body", "limit"],
                    "type": "less_than_equal",
                    "msg": f"Input should be less than or equal to {maximum}",
                }
            ],
        )


def _entry(state: SearchState, search_id: str, user: CurrentUser) -> Entry:
    return state.registry.get(search_id, user.id, user.session_token_hash)


def _shown(seconds: float) -> float:
    """Seconds as a message shows them (``4``, ``0.5``)."""
    return int(seconds) if seconds == int(seconds) else seconds


def _timeout(settings: dict[str, Any]) -> ApiError:
    seconds = _seconds(settings, "search.request_budget_ms")
    return search_error("search_timeout", {"seconds": _shown(seconds)})


def _oldest_notice(db: Session, state: SearchState, start: datetime) -> list[dict[str, Any]]:
    """``vault_only_before`` when ``from`` lies before the oldest row of ``messages``."""

    def compute() -> Oldest:
        rows = run_statement(db, select(func.min(Message.time).label("t")), STATEMENT_SECONDS)
        return Oldest(rows[0]["t"] if rows else None)

    try:
        oldest = state.oldest.get_or_compute(compute)
    except WindowTimeoutError:
        return []
    if oldest.time is None or start >= oldest.time:
        return []
    return [notice("vault_only_before", date=site_day(oldest.time).isoformat())]


def _horizons(db: Session, state: SearchState) -> Mapping[str, datetime]:
    """Where each table's compressed chunks end (the page statements split there); a catalogue
    that does not answer in time splits nothing."""

    def compute() -> Horizons:
        return Horizons(compression_horizons(db, STATEMENT_SECONDS))

    try:
        return state.horizons.get_or_compute(compute).tables
    except WindowTimeoutError:
        return {}


def _page_answer(entry: Entry, items: list[dict[str, Any]], page: Page) -> SearchPage:
    return SearchPage.model_validate(
        {
            "search_id": entry.search_id,
            "expires_at": entry.expires_at,
            "query": describe_query(entry.plan.query),
            "range": {"from": entry.start, "to": entry.end},
            "plan": entry.plan.describe(entry.start, entry.end),
            "items": items,
            "next_cursor": None if page.next_cursor is None else encode(page.next_cursor),
            "scan": page.scan.to_dict(),
            "notices": [*entry.plan.notices, *entry.notices],
        }
    )


# -- search and its pages -------------------------------------------------------------------------


@router.post("")
def run_search(
    body: SearchRequest,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    user: ReaderDep,
    state: StateDep,
) -> SearchPage:
    """Run a search: its first page, kept under a ``search_id`` (audited ``search.run``)."""
    settings = _settings(db)
    state.runs.hit(user.id, int(settings["search.runs_per_minute"]))
    if body.saved_id is not None:
        row = saved_searches.own(db, user.id, body.saved_id)
        q = row.query
        start, end = saved_searches.range_of(row)
    elif body.from_ is None or body.to is None:
        raise search_error("search_range_invalid")
    else:
        q, start, end = body.q, body.from_, body.to
    limit = body.limit or min(DEFAULT_LIMIT, int(settings["search.page_size_max"]))
    _check_limit(limit, settings)
    check_range(start, end, int(settings["search.max_days"]))
    query = _parse(q)
    site = SiteNetwork.load(db, config)
    plan = _plan(query, site)
    search_id = new_search_id()
    budget = _seconds(settings, "search.request_budget_ms")
    began = monotonic()
    details: dict[str, Any] = {
        "search_id": search_id,
        "q": query.normalized,
        "from": start,
        "to": end,
        "kinds": list(plan.kinds),
        "driver": plan.driver,
        "tables": list(plan.tables),
        "saved_id": body.saved_id,
    }
    try:
        with reading(db, state, user, settings):
            page = run_page(
                db,
                plan,
                start,
                end,
                None,
                limit,
                budget,
                clock=state.clock,
                hook=state.hook,
                horizons=_horizons(db, state),
            )
            items = page_rows(db, plan, page.rows, site)
            found = _oldest_notice(db, state, start)
    except SearchTimeoutError:
        details |= {
            "search_id": None,
            "rows": 0,
            "complete": False,
            "outcome": "timeout",
            "elapsed_ms": int((monotonic() - began) * 1000),
        }
        record_audit(db, actor=user, action="search.run", details=details, ip=client_ip(request))
        db.commit()
        raise _timeout(settings) from None
    entry = Entry(
        search_id=search_id,
        user_id=user.id,
        session_hash=user.session_token_hash,
        plan=plan,
        start=start,
        end=end,
        limit=limit,
        saved_id=body.saved_id,
        created_at=utc_now(),
        notices=tuple(found),
    )
    details |= {
        "rows": len(items),
        "complete": page.scan.complete,
        "outcome": "ok",
        "elapsed_ms": page.scan.elapsed_ms,
    }
    record_audit(
        db,
        actor=user,
        action="search.run",
        target=f"search:{search_id}",
        details=details,
        ip=client_ip(request),
    )
    if body.saved_id is not None:
        db.execute(
            update(SavedSearch).where(SavedSearch.id == body.saved_id).values(last_run_at=utc_now())
        )
    db.commit()  # audited before the rows leave the API
    state.registry.add(entry)
    return _page_answer(entry, items, page)


@router.post("/page")
def next_page(
    body: SearchPageRequest,
    db: DbSession,
    config: ConfigDep,
    user: ReaderDep,
    state: StateDep,
) -> SearchPage:
    """The next page of an audited search (not audited again)."""
    settings = _settings(db)
    entry = _entry(state, body.search_id, user)
    limit = body.limit or entry.limit
    _check_limit(limit, settings)
    cursor = decode(body.cursor, start=entry.start, end=entry.end, ranks=entry.plan.ranks)
    site = SiteNetwork.load(db, config)
    budget = _seconds(settings, "search.request_budget_ms")
    try:
        with reading(db, state, user, settings):
            page = run_page(
                db,
                entry.plan,
                entry.start,
                entry.end,
                cursor,
                limit,
                budget,
                clock=state.clock,
                hook=state.hook,
                horizons=_horizons(db, state),
            )
            items = page_rows(db, entry.plan, page.rows, site)
    except SearchTimeoutError:
        raise _timeout(settings) from None
    return _page_answer(entry, items, page)


@router.post("/stats")
def search_stats(
    body: SearchIdRequest,
    db: DbSession,
    config: ConfigDep,
    user: ReaderDep,
    state: StateDep,
) -> SearchStats:
    """Total, histogram and facets of an audited search (not audited again)."""
    settings = _settings(db)
    entry = _entry(state, body.search_id, user)
    labels = SiteLabels(
        SiteNetwork.load(db, config),
        {
            vlan_id: name
            for vlan_id, name in db.execute(select(Vlan.vlan_id, Vlan.name_tr))
            if vlan_id is not None
        },
    )
    with reading(db, state, user, settings):
        counted = run_stats(
            db,
            entry.plan,
            entry.start,
            entry.end,
            labels=labels,
            budget_seconds=_seconds(settings, "search.stats_budget_ms"),
            sample_rows=int(settings["search.facet_sample_rows"]),
            clock=state.clock,
            hook=state.hook,
        )
    return SearchStats.model_validate({"search_id": entry.search_id, **counted})


# -- the query language ---------------------------------------------------------------------------


@router.post("/parse")
def parse(body: ParseRequest, _: CatalogueDep) -> ParseResult:
    """Check a query while it is typed (no database work)."""
    try:
        query = parse_query(body.q)
        plan = plan_query(query)
    except SearchQueryError as exc:
        return ParseResult.model_validate(
            {
                "normalized": "",
                "clauses": [],
                "kinds": [],
                "selective": False,
                "errors": [error_item(exc)],
            }
        )
    return ParseResult.model_validate(
        {
            "normalized": query.normalized,
            "clauses": describe_clauses(query),
            "kinds": list(plan.kinds),
            "selective": plan.selective,
            "errors": [],
        }
    )


def _sources(db: Session, config: Config) -> list[dict[str, Any]]:
    """Configured sources and those ingest has seen (``unknown-<ip>`` included)."""
    found = {
        source.name: {"name": source.name, "type": source.type, "network": source.is_network}
        for source in config.sources
    }
    for name, type_ in db.execute(select(Source.name, Source.type)):
        found.setdefault(name, {"name": name, "type": type_, "network": False})
    return [found[name] for name in sorted(found)]


@router.get("/fields")
def fields(db: DbSession, config: ConfigDep, _: CatalogueDep) -> FieldCatalogue:
    """The field catalogue: fields, kinds, sources and limits (syntax help, completion)."""
    settings = _settings(db)
    return FieldCatalogue.model_validate(
        {
            "fields": catalogue_fields(),
            "kinds": catalogue_kinds(),
            "sources": _sources(db, config),
            "limits": {
                **LIMITS,
                "max_days": int(settings["search.max_days"]),
                "page_size_max": int(settings["search.page_size_max"]),
            },
        }
    )


# -- one row --------------------------------------------------------------------------------------


def _plain(value: Any) -> Any:
    """A column value as JSON (addresses as text, times and days as ISO)."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_plain(item) for item in value]
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


EVENT_SKIPPED: Final = frozenset({"extra"})


@router.get("/event")
def event(
    request: Request,
    db: DbSession,
    user: ReaderDep,
    state: StateDep,
    table: Annotated[str, Query(min_length=1, max_length=32)],
    time: Annotated[AwareDatetime, Query()],
    id: Annotated[int, Query(ge=0, le=MAX_ROW_ID)],
) -> SearchEvent:
    """Every field of one row and its line's ``messages`` row (audited ``search.event``)."""
    if table not in TIME_SERIES_TABLES:
        raise search_error("search_event_not_found")
    settings = _settings(db)
    found = Base.metadata.tables[table]
    with reading(db, state, user, settings):
        try:
            rows = run_statement(
                db,
                select(found).where(found.c.time == time, found.c.id == id),
                STATEMENT_SECONDS,
            )
            row = rows[0] if rows else None
            message = row if table == "messages" else None
            if row is not None and message is None:
                m = Message.__table__
                messages = run_statement(
                    db,
                    select(m).where(
                        m.c.time == row["time"],
                        m.c.source == row["source"],
                        m.c.raw_day == row["raw_day"],
                        m.c.raw_line == row["raw_line"],
                    ),
                    STATEMENT_SECONDS,
                )
                message = messages[0] if messages else None
        except WindowTimeoutError:  # the row may well exist: say that time ran out
            raise search_error(
                "search_event_timeout", {"seconds": _shown(STATEMENT_SECONDS)}
            ) from None
    if row is None:
        raise search_error("search_event_not_found")
    raw = {
        "source": row["source"],
        "day": row["raw_day"],
        "line": int(row["raw_line"]),
        "offset": int(row["raw_offset"]),
    }
    if message is not None:
        kind = kind_of(message["type"], message["subtype"])
    else:
        kind = kind_of_row(table, row.get("event")) or "other"
    record_audit(
        db,
        actor=user,
        action="search.event",
        target=f"{table}:{utc_text(row['time'])}:{row['id']}",
        details={"table": table, "id": int(row["id"]), "time": row["time"], "raw": raw},
        ip=client_ip(request),
    )
    fields_ = {
        name: (display(value, 2000) if isinstance(value, str) else _plain(value))
        for name, value in row.items()
        if name not in EVENT_SKIPPED
    }
    message_out = None
    if message is not None:
        message_out = {
            "type": message["type"],
            "subtype": message["subtype"],
            "severity": message["severity"],
            "facility": message["facility"],
            "program": message["program"],
            "host": message["host"],
            "text": display(message["text"] or "", 2000),
            "parse_error": message["parse_error"],
        }
    return SearchEvent.model_validate(
        {
            "table": table,
            "kind": kind,
            "fields": fields_,
            "extra": _plain_extra(row.get("extra")),
            "message": message_out,
            "raw": raw,
        }
    )


def _safe(value: Any) -> Any:
    """A value of ``extra`` (JSON from a device's line) with every text display-safe."""
    if isinstance(value, str):
        return display(value, 2000)
    if isinstance(value, dict):
        return {display(str(key), 200): _safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_safe(item) for item in value]
    return value


def _plain_extra(extra: Any) -> dict[str, Any]:
    return _safe(extra) if isinstance(extra, dict) else {}


# -- evidence references and export ---------------------------------------------------------------


@router.post("/refs")
def search_refs(
    body: SearchRefsRequest,
    request: Request,
    db: DbSession,
    layout: LayoutDep,
    user: ReferrerDep,
    state: StateDep,
) -> SearchRefs:
    """The raw references of a search, for an evidence package (audited ``search.refs``)."""
    settings = _settings(db)
    entry = _entry(state, body.search_id, user)
    with reading(db, state, user, settings):
        found = references.collect(
            db,
            entry.plan,
            entry.start,
            entry.end,
            body.limit,
            _seconds(settings, "search.stats_budget_ms"),
            clock=state.clock,
            hook=state.hook,
            horizons=_horizons(db, state),
        )
    sha256 = references.refs_sha256(found.refs)
    record_audit(
        db,
        actor=user,
        action="search.refs",
        target=f"search:{entry.search_id}",
        details={
            "search_id": entry.search_id,
            "q": entry.plan.query.normalized,
            "from": entry.start,
            "to": entry.end,
            "refs": len(found.refs),
            "truncated": found.truncated,
            "complete": found.complete,
            "sha256": sha256,
        },
        ip=client_ip(request),
    )
    db.commit()
    return SearchRefs.model_validate(
        {
            "refs": found.refs,
            "truncated": found.truncated,
            "complete": found.complete,
            "unsealed_days": references.unsealed_days(layout, found.refs),
            "sha256": sha256,
        }
    )


@router.post(
    "/export",
    response_class=StreamingResponse,
    responses={200: {"content": {media: {} for media in exports.MEDIA_TYPES.values()}}},
)
def search_export(
    body: SearchExportRequest,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    user: ExporterDep,
    state: StateDep,
) -> StreamingResponse:
    """The rows of a search as a file (audited ``search.export`` before it is sent)."""
    settings = _settings(db)
    entry = _entry(state, body.search_id, user)
    with state.exports.hold(user.id):
        check_export_rate(db, user.id, int(settings["search.export_max_per_hour"]))
        site = SiteNetwork.load(db, config)
        info = exports.Info(
            query=entry.plan.query.normalized,
            start=entry.start,
            end=entry.end,
            username=user.username,
            created_at=utc_now(),
        )
        state_ = exports.Reading()
        with reading(db, state, user, settings):
            rows = exports.read_rows(
                db,
                entry.plan,
                entry.start,
                entry.end,
                site,
                state_,
                max_rows=int(settings["search.export_max_rows"]),
                budget_seconds=float(settings["search.export_budget_seconds"]),
                clock=state.clock,
                hook=state.hook,
                horizons=_horizons(db, state),
            )
            built = exports.build(rows, body.format, info, state_)
        try:
            record_audit(
                db,
                actor=user,
                action="search.export",
                target=f"search:{entry.search_id}",
                details={
                    "search_id": entry.search_id,
                    "q": entry.plan.query.normalized,
                    "from": entry.start,
                    "to": entry.end,
                    "format": body.format,
                    "rows": built.rows,
                    "truncated": built.truncated,
                    "bytes": built.size,
                    "sha256": built.sha256,
                },
                ip=client_ip(request),
            )
            db.commit()  # audited and committed before the file is sent
        except BaseException:
            built.close()
            raise
    headers = {
        "Content-Disposition": f'attachment; filename="{built.filename}"',
        "Content-Length": str(built.size),
        "Repr-Digest": built.repr_digest,
        "Cache-Control": "no-store",
    }
    if built.truncated is not None:
        headers["X-Arci-Export-Truncated"] = built.truncated
    return StreamingResponse(
        built.chunks(),
        media_type=built.media_type,
        headers=headers,
        background=BackgroundTask(built.close),
    )


# -- saved searches -------------------------------------------------------------------------------


def _saved_out(row: SavedSearch) -> SavedSearchOut:
    return SavedSearchOut.model_validate(saved_searches.describe(row))


@router.get("/saved")
def list_saved(db: DbSession, user: ReaderDep) -> SavedSearchList:
    """The user's own saved searches."""
    rows = saved_searches.list_own(db, user.id)
    return SavedSearchList(items=[_saved_out(row) for row in rows])


@router.post("/saved", status_code=status.HTTP_201_CREATED)
def create_saved(
    body: SavedSearchIn, request: Request, db: DbSession, user: ReaderDep
) -> SavedSearchOut:
    """Save a search (personal; names unique ignoring case)."""
    settings = _settings(db)
    row = saved_searches.create(
        db,
        user.id,
        body,
        limit=int(settings["search.saved_max_per_user"]),
        max_days=int(settings["search.max_days"]),
    )
    record_audit(
        db,
        actor=user,
        action="search.saved_create",
        target=f"saved-search:{row.id}",
        details={"saved_id": row.id, "name": row.name, "q": row.query},
        ip=client_ip(request),
    )
    return _saved_out(row)


SavedId = Annotated[int, Path(ge=1, le=MAX_ROW_ID, description="Saved search number")]


@router.put("/saved/{saved_id}")
def update_saved(
    saved_id: SavedId, body: SavedSearchIn, request: Request, db: DbSession, user: ReaderDep
) -> SavedSearchOut:
    """Rename or change a saved search."""
    settings = _settings(db)
    row = saved_searches.update(
        db, user.id, saved_id, body, max_days=int(settings["search.max_days"])
    )
    record_audit(
        db,
        actor=user,
        action="search.saved_update",
        target=f"saved-search:{row.id}",
        details={"saved_id": row.id, "name": row.name, "q": row.query},
        ip=client_ip(request),
    )
    return _saved_out(row)


@router.delete("/saved/{saved_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved(saved_id: SavedId, request: Request, db: DbSession, user: ReaderDep) -> Response:
    """Delete a saved search."""
    row = saved_searches.delete(db, user.id, saved_id)
    record_audit(
        db,
        actor=user,
        action="search.saved_delete",
        target=f"saved-search:{saved_id}",
        details={"saved_id": saved_id, "name": row.name},
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
