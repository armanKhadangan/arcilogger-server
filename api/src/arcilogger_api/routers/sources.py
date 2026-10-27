"""``/api/v1/sources`` — Kaynaklar: log sources and their health (spec 12.3 page 3, contract 2.16).

Kaynaklar only reads; the sources are configured in Ayarlar → Kaynaklar
(``/api/v1/admin/config/sources``). "Kaynak olarak tanımla" on an unknown sender opens that page's
add dialog filled in with ``suggested``.

==============================  ====================================================  ===========
Endpoint                        Permission                                            Audit
==============================  ====================================================  ===========
``GET /sources``                ``sources.read`` (counts only; cached 15 s)           —
``GET /sources/{name}``         ``sources.read`` (cached 30 s per source); a          —
                                pattern's ``error`` only with ``search.read`` or
                                ``lookup.run`` and the KVKK notice (it can echo a
                                value of the line)
``GET /sources/{name}/tail``    ``sources.read`` **and** (``search.read`` or          ``raw.tail``
                                ``lookup.run``) and the KVKK notice; 30 a minute
                                per user
==============================  ====================================================  ===========

**Tail** (Son satırlar): the last ``limit`` (≤ 200) complete lines of today's spool file, else of
yesterday's — the vault is never decompressed for it; with neither, ``lines: []`` and ``reason:
"no_file_today"``. Line numbers and offsets are exact (the raw references ``GET /raw`` opens).
``range_sha256`` is the SHA-256 of the file's bytes ``[from_offset, to_offset)``: the lines shown
with their line feeds, so an auditor can recompute it from the file (``raw.tail`` stores it with
the range and the count, never the content). Read errors are ``raw_access_denied`` and
``raw_io_error`` (503), as for ``GET /raw``.

No licence module gates any of it (5651 Temel). See :mod:`arcilogger_api.sourcehealth`.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from datetime import date, datetime
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from arcilogger_api import sourcehealth
from arcilogger_api.audit import record_audit
from arcilogger_api.auth.accounts import privacy_accepted, privacy_required, privacy_version
from arcilogger_api.auth.privacy import ensure_privacy_notice
from arcilogger_api.auth.ratelimit import RateLimiter
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep, TtlCache
from arcilogger_api.errors import ApiError
from arcilogger_api.live.events import message_part
from arcilogger_api.rawlines import display_text, os_error, valid_source_name
from arcilogger_api.rawtail import line_anchor, read_tail
from arcilogger_api.routers.raw import MESSAGES as RAW_MESSAGES
from arcilogger_api.search.schemas import RawRef
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_core.timeutil import site_day, utc_now

router = APIRouter(prefix="/sources", tags=["sources"])
log = logging.getLogger("arcilogger.api.sources")

MESSAGES: Final = {
    "source_not_found": (
        "Bu adla bir log kaynağı tanımlı değil veya hiç görülmedi.",
        "No log source with this name is configured or has been seen.",
    ),
}
#: Either permission shows a parse failure's detail and opens the raw lines (as ``GET /raw``).
RAW_PERMISSIONS: Final = ("search.read", "lookup.run")
OVERVIEW_SECONDS: Final = 15.0
DETAIL_SECONDS: Final = 30.0
#: ``GET /sources/{name}/tail``: requests per user and window (seconds).
TAIL_LIMIT: Final = (30, 60.0)
TAIL_MAX: Final = 200

ReaderDep = Annotated[CurrentUser, Depends(require("sources.read"))]
SourceName = Annotated[str, Path(min_length=1, max_length=81)]


def _error(code: str, status: int = 404) -> ApiError:
    message_tr, message_en = MESSAGES[code]
    return ApiError(status, code, message_tr, message_en)


def _raw_error(code: str) -> ApiError:
    message_tr, message_en = RAW_MESSAGES[code]
    return ApiError(503, code, message_tr, message_en)


class SourcesState:
    """The caches and the tail's rate limit of one application (``app.state.arci_sources``)."""

    def __init__(self) -> None:
        self.overview: TtlCache[dict[str, Any]] = TtlCache(OVERVIEW_SECONDS)
        self.details: dict[str, TtlCache[dict[str, Any]]] = {}
        self.detail_seconds = DETAIL_SECONDS
        self.tail_limit = RateLimiter(*TAIL_LIMIT)
        self._lock = threading.Lock()

    def detail(self, name: str) -> TtlCache[dict[str, Any]]:
        with self._lock:
            cache = self.details.get(name)
            if cache is None:
                if len(self.details) > 1000:  # names are bounded by the sources table anyway
                    self.details.clear()
                cache = self.details[name] = TtlCache(self.detail_seconds)
            return cache


def state_of(request: Request) -> SourcesState:
    state: SourcesState | None = getattr(request.app.state, "arci_sources", None)
    if state is None:
        state = request.app.state.arci_sources = SourcesState()
    return state


# -- answers --------------------------------------------------------------------------------------


class Answer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


class CollectorState(Answer):
    #: When the collector last wrote ``state/collector-status.json``.
    updated_at: datetime | None
    #: Within 5 minutes: its receive times count for the state.
    fresh: bool


class Suggested(Answer):
    """What "Kaynak olarak tanımla" fills in for an unknown sender."""

    name: str
    type: Literal["fortigate", "mikrotik", "generic"]


class SourceHealth(Answer):
    name: str
    type: str
    address: str | None
    configured: bool
    #: A member ``<name>-<ip>`` of the configured network ``name``.
    network_of: str | None
    unknown: bool
    enabled: bool
    expected: bool
    heartbeat_minutes: int
    first_seen: datetime | None
    last_message: datetime | None
    last_message_origin: Literal["collector", "database"] | None
    state: Literal["active", "silent", "never", "disabled", "unknown", "promoted"]
    silent_minutes: int | None
    messages_per_minute: float
    #: Messages a minute (device time) of the 60 whole minutes before now, oldest first.
    sparkline: list[int]
    lines_today: int
    #: Bytes of today's spool file ingest has not loaded yet (the ingest lag).
    pending_bytes: int
    parse_failures_today: int
    parse_failure_permille: float | None
    #: A promoted unknown sender: the source it is written under now.
    promoted_to: str | None
    suggested: Suggested | None


class SourcesOverview(Answer):
    generated_at: datetime
    today: date
    collector: CollectorState
    items: list[SourceHealth]


class HourCount(Answer):
    hour: datetime
    count: int


class DayHealth(Answer):
    day: date
    lines: int
    failures: int
    sealed: bool


class SourceGap(Answer):
    from_: datetime = Field(alias="from")
    to: datetime
    minutes: int
    reason: str
    #: ``manifest``: recorded by the sealer; ``live``: today's, since the last message.
    origin: Literal["manifest", "live"]


class SourceParseFailure(Answer):
    day: date
    pattern: str
    count: int
    first_seen: datetime
    last_seen: datetime
    #: The parser's detail; ``null`` without ``search.read`` or ``lookup.run``, or until the
    #: current KVKK notice is accepted.
    error: str | None
    example: RawRef


class SourceDetail(SourceHealth):
    #: Lines per hour of the last 48 hours, oldest first.
    hourly: list[HourCount]
    #: The last 14 days, today first.
    daily: list[DayHealth]
    #: The last 30 days, newest first.
    gaps: list[SourceGap]
    #: The 50 most frequent patterns of the last 7 days.
    parse_failures: list[SourceParseFailure]


class TailLine(Answer):
    line: int
    offset: int
    received: datetime | None
    sender: str | None
    #: The message, display-safe (the spool's escapes visible).
    text: str
    #: SHA-256 of the line as stored (without its line feed), as ``GET /raw`` gives it.
    sha256: str


class SourceTail(Answer):
    source: str
    day: date | None
    origin: Literal["spool"] | None
    from_line: int | None
    to_line: int | None
    from_offset: int | None
    #: Offset after the last line's line feed.
    to_offset: int | None
    #: SHA-256 of the file's bytes ``[from_offset, to_offset)``.
    range_sha256: str | None
    lines: list[TailLine]
    #: ``no_file_today``: neither today's nor yesterday's spool file exists.
    reason: Literal["no_file_today"] | None = None


# -- endpoints ------------------------------------------------------------------------------------


def _overview(
    request: Request, db: DbSession, config: ConfigDep, layout: LayoutDep
) -> dict[str, Any]:
    return state_of(request).overview.get_or_compute(
        lambda: sourcehealth.overview(db, config, layout, utc_now())
    )


@router.get("")
def sources_overview(
    request: Request, db: DbSession, config: ConfigDep, layout: LayoutDep, user: ReaderDep
) -> SourcesOverview:
    """Every log source with its state (the web app refreshes it every 30 s in the background)."""
    return SourcesOverview.model_validate(_overview(request, db, config, layout))


@router.get("/{name}")
def source_detail(
    name: SourceName,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    user: ReaderDep,
) -> SourceDetail:
    """One source: its last 48 hours, 14 days, gaps and parse failures."""
    if not valid_source_name(name) or not sourcehealth.known(db, config, name):
        raise _error("source_not_found")
    summary = _overview(request, db, config, layout)
    entry = next((item for item in summary["items"] if item["name"] == name), None)
    if entry is None:  # pragma: no cover - known() and the overview read the same rows
        raise _error("source_not_found")
    members = [item["name"] for item in summary["items"] if item["network_of"] == name]
    data = (
        state_of(request)
        .detail(name)
        .get_or_compute(lambda: sourcehealth.detail(db, config, layout, entry, members, utc_now()))
    )
    if not shows_line_values(db, user):
        data = {
            **data,
            "parse_failures": [{**row, "error": None} for row in data["parse_failures"]],
        }
    return SourceDetail.model_validate(data)


def shows_line_values(db: Session, user: CurrentUser) -> bool:
    """A parse failure's ``error`` can echo a value of the line: shown under the rule of the raw
    lines (``search.read`` or ``lookup.run``, and the current KVKK notice accepted)."""
    if not any(user.has(permission) for permission in RAW_PERMISSIONS):
        return False
    return not privacy_required(user.role) or privacy_accepted(db, user.id, privacy_version(db))


def get_tail_reader(db: DbSession, user: ReaderDep) -> CurrentUser:
    """``sources.read`` and (``search.read`` or ``lookup.run``), KVKK notice accepted."""
    if not any(user.has(permission) for permission in RAW_PERMISSIONS):
        raise ApiError(403, "forbidden")
    ensure_privacy_notice(db, user)
    return user


TailReaderDep = Annotated[CurrentUser, Depends(get_tail_reader)]


@router.get("/{name}/tail")
def source_tail(
    name: SourceName,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    user: TailReaderDep,
    limit: Annotated[int, Query(ge=1, le=TAIL_MAX)] = 50,
) -> SourceTail:
    """The source's last lines from the spool, byte-exact references, audited ``raw.tail``."""
    decision = state_of(request).tail_limit.hit(str(user.id))
    if not decision.allowed:
        raise ApiError(429, "rate_limited", headers={"Retry-After": str(decision.retry_after)})
    if not valid_source_name(name) or not sourcehealth.known(db, config, name):
        raise _error("source_not_found")
    now = utc_now()
    day = sourcehealth.spool_day(layout, name, site_day(now))
    empty = SourceTail(
        source=name,
        day=None,
        origin=None,
        from_line=None,
        to_line=None,
        from_offset=None,
        to_offset=None,
        range_sha256=None,
        lines=[],
        reason="no_file_today",
    )
    if day is None:
        return empty
    try:
        found = read_tail(layout, name, day, limit, line_anchor(db, layout, name, day))
    except FileNotFoundError:
        return empty  # sealed and deleted just now
    except OSError as exc:
        error = os_error(layout.spool_file(name, day), exc)
        log.error("tail of %s/%s: %s", name, day, error)
        raise _raw_error(error.code) from None
    lines = found.lines
    data = b"".join(line.data + b"\n" for line in lines)
    first, last = (lines[0], lines[-1]) if lines else (None, None)
    answer = SourceTail(
        source=name,
        day=day,
        origin="spool",
        from_line=first.line if first else None,
        to_line=last.line if last else None,
        from_offset=first.offset if first else None,
        to_offset=last.offset + len(last.data) + 1 if last else None,
        range_sha256=hashlib.sha256(data).hexdigest() if lines else None,
        lines=[_tail_line(line) for line in lines],
    )
    record_audit(
        db,
        actor=user,
        action="raw.tail",
        target=f"raw:{name}/{day.isoformat()}",
        details={
            "source": name,
            "day": day.isoformat(),
            "origin": "spool",
            "from_line": answer.from_line,
            "to_line": answer.to_line,
            "from_offset": answer.from_offset,
            "to_offset": answer.to_offset,
            "count": len(lines),
            "range_sha256": answer.range_sha256,
        },
        ip=client_ip(request),
    )
    return answer


def _tail_line(line: Any) -> TailLine:
    record = line.record()
    return TailLine(
        line=line.line,
        offset=line.offset,
        received=record.received if record else None,
        sender=record.sender if record else None,
        text=display_text(message_part(line.data) if record else line.data),
        sha256=line.sha256,
    )
