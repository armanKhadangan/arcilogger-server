"""``/api/v1/dashboard`` — Panel (spec 12.3 page 2; docs/MVP-CONTRACTS.md section 14).

``GET /dashboard/summary`` needs ``dashboard.view``; every box has its own permission as well
and is ``null`` without it (the web app hides the box):

==========  ===============================  ===================================================
Box         Permission                       Read from
==========  ===============================  ===================================================
``ingest``  ``sources.read`` or              the sources R15 watches (``sources`` and the
            ``dashboard.view``               collector's ``state/collector-status.json``, the
                                             rule of :mod:`arcilogger_worker.alerts.r15`) and
                                             ``messages`` of the last 60 whole minutes
``seal``    ``seal.read``                    the sealing page's day states
                                             (:class:`arcilogger_api.sealinfo.VaultView`)
``credit``  ``seal.read``                    ``state/credit.jsonl``
                                             (:func:`arcilogger_api.sealinfo.credit_history`)
``clock``   ``system.read`` or ``seal.read``  ``state/clock/<day>.jsonl`` (the hourly probe)
``alerts``  ``alerts.read``                  ``alerts`` (as ``GET /alerts/summary``)
==========  ===============================  ===================================================

``parse_failures`` ("Ayrıştırma hataları", contract 2.17) has the permission of ``ingest``: today's
loaded lines (``ingest_offsets``) and parse failures (``parse_failures``) by source and pattern,
with an example line per pattern, and the totals of the 6 days before — patterns and counts
only.

**Messages per minute** count ``messages`` by ``time`` over the 60 whole minutes before the
current one (the current minute is still filling): one index-only range scan of the primary key
``(time, id)`` of the month's partition, grouped per minute — no extra index and no counter
table. Measured (``api/tests/test_dashboard_api.py``, ``poe test-perf``): about 30-50 ms for
100,000 messages in the hour of 400,000 rows, about 80-150 ms for 300,000 (5,000 a minute, a
busy hour) — the guard is :data:`MESSAGE_RATE_BUDGET_SECONDS`. ``time`` is the device time (the
receive time is not indexed), so a device whose clock is wrong counts in another minute.

The web app refreshes the dashboard every 30 seconds with ``X-Arci-Background: 1`` (no session
activity). Nothing here is audited (reads only, no person data).
"""

from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.routers.alerts import AlertOut, OpenBySeverity, latest_active, open_by_severity
from arcilogger_api.sealinfo import VaultView, credit_history, on_demand_verifications
from arcilogger_api.search.schemas import RawRef
from arcilogger_api.security import CurrentUser, require
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, to_site, utc_now
from arcilogger_db.models import IngestOffset, Message, ParseFailure
from arcilogger_worker.alerts.r15 import newest_message, watched_sources
from arcilogger_worker.correlation.watermark import collector_received

router = APIRouter(prefix="/dashboard", tags=["dashboard"])

ViewerDep = Annotated[CurrentUser, Depends(require("dashboard.view"))]

#: Minutes of the sparkline (and of the mean messages per minute).
SPARK_MINUTES: Final = 60
#: The message-rate query on 300,000 messages in the hour must answer within this (test).
MESSAGE_RATE_BUDGET_SECONDS: Final = 1.0
#: Days of the seal calendar (ending yesterday: today is never sealed yet).
CALENDAR_DAYS: Final = 30
#: The clock probe runs hourly: an older sample says nothing about the clock now.
CLOCK_STALE: Final = timedelta(hours=3)
#: Credit used per day when the history cannot tell: one stamp per sealed day (spec 6.5).
DEFAULT_STAMPS_PER_DAY: Final = 1.0
#: History the credit consumption is measured over.
CREDIT_WINDOW: Final = timedelta(days=30)
#: The parse-failure box: sources and patterns shown, days of history (today included).
FAILURE_SOURCES: Final = 5
FAILURE_PATTERNS: Final = 3
FAILURE_DAYS: Final = 7

SealStatus = Literal["sealed", "pending", "failed", "no_data"]
#: Day state of the sealing page → the dashboard's four.
SEAL_STATUS: Final[dict[str, SealStatus]] = {
    "verified": "sealed",
    "sealed": "sealed",
    "late": "sealed",
    "pending": "pending",
    "open": "pending",
    "failed": "failed",
    "no_data": "no_data",
    "deleted": "no_data",
    "future": "no_data",
}


class SilentSource(BaseModel):
    name: str
    #: Newest message (loaded or received by the collector); ``null``: never.
    last_seen: datetime | None


class MinuteCount(BaseModel):
    #: Start of the minute (site time).
    minute: datetime
    count: int


class DashboardIngest(BaseModel):
    #: Enabled, expected, configured sources (the ones R15 watches).
    sources_total: int
    #: Of those, the ones that sent within their heartbeat.
    sources_active: int
    #: The others, by name.
    silent: list[SilentSource]
    #: Mean of the sparkline (messages by device time), one decimal.
    messages_per_minute: float
    #: The 60 whole minutes before the current one, oldest first, empty minutes as 0.
    sparkline: list[MinuteCount]


class SealYesterday(BaseModel):
    day: date
    status: SealStatus
    sealed_at: datetime | None
    #: First 8 hex digits of the time-stamp token's serial number, ``…`` when longer.
    serial_short: str | None
    #: The newest verification of the day passed (``null``: never verified).
    verified: bool | None


class SealCalendarDay(BaseModel):
    day: date
    status: SealStatus


class DashboardSeal(BaseModel):
    yesterday: SealYesterday
    #: The 30 days ending yesterday, oldest first.
    calendar: list[SealCalendarDay]


class DashboardCredit(BaseModel):
    #: Newest known Kamu SM credit (``null``: never read, or a provider without credit).
    remaining: int | None
    #: ``remaining`` divided by the use per day measured over the last 30 days of checks
    #: (one stamp a day when that cannot be measured).
    estimated_days: int | None
    #: ``[tsa] credit_alert_threshold``.
    threshold: int | None


class DashboardClock(BaseModel):
    #: Offset of the server clock from the NTP server in the newest sample.
    offset_ms: float | None
    #: The server that answered (the configured one when none did, or there is no sample).
    ntp_server: str | None
    checked_at: datetime | None
    #: Within ``[clock] alert_offset_ms``; ``false`` when no server answered; ``null`` without
    #: a sample of the last 3 hours.
    ok: bool | None


class DashboardAlerts(BaseModel):
    open_by_severity: OpenBySeverity
    #: The newest open or acknowledged alerts (at most 5).
    latest: list[AlertOut]


class SourceFailures(BaseModel):
    source: str
    #: Lines ingest loaded today from the source's file.
    lines: int
    failures: int
    #: Failures per 1,000 loaded lines, one decimal (``null`` without lines).
    permille: float | None


class FailurePattern(BaseModel):
    source: str
    pattern: str
    count: int
    #: One line of the pattern (the raw-line view opens it).
    example: RawRef


class FailureDay(BaseModel):
    day: date
    lines: int
    failures: int


class DashboardParseFailures(BaseModel):
    """Today's parse failures ("Ayrıştırma hataları"): counts and patterns, never content."""

    day: date
    lines: int
    failures: int
    permille: float | None
    #: The sources with the most failures today (at most 5).
    by_source: list[SourceFailures]
    #: Today's most frequent patterns (at most 3).
    top_patterns: list[FailurePattern]
    #: Today and the 6 days before, oldest first.
    last7: list[FailureDay]


class DashboardSummary(BaseModel):
    generated_at: datetime
    ingest: DashboardIngest | None
    seal: DashboardSeal | None
    credit: DashboardCredit | None
    clock: DashboardClock | None
    alerts: DashboardAlerts | None
    parse_failures: DashboardParseFailures | None


# -- ingest ---------------------------------------------------------------------------------------


def message_rate(db: Session, now: datetime) -> tuple[list[MinuteCount], float]:
    """Messages per minute of the 60 whole minutes before ``now``'s minute, and their mean."""
    end = now.astimezone(UTC).replace(second=0, microsecond=0)
    start = end - timedelta(minutes=SPARK_MINUTES)
    minute = func.date_trunc("minute", Message.time)
    rows = db.execute(
        select(minute, func.count())
        .where(Message.time >= start, Message.time < end)
        .group_by(minute)
    ).all()
    counts = {moment.astimezone(UTC): int(count) for moment, count in rows}
    points = [
        MinuteCount(minute=to_site(at), count=counts.get(at, 0))
        for at in (start + timedelta(minutes=n) for n in range(SPARK_MINUTES))
    ]
    total = sum(point.count for point in points)
    return points, round(total / SPARK_MINUTES, 1)


def ingest_box(db: Session, layout: Layout, now: datetime) -> DashboardIngest:
    received = collector_received(layout, now) or {}
    active = 0
    silent: list[SilentSource] = []
    sources = watched_sources(db)
    for source in sources:
        last = newest_message(source, received)
        if last is not None and now - last <= timedelta(minutes=source.heartbeat_minutes):
            active += 1
        else:
            silent.append(
                SilentSource(name=source.name, last_seen=None if last is None else to_site(last))
            )
    sparkline, per_minute = message_rate(db, now)
    return DashboardIngest(
        sources_total=len(sources),
        sources_active=active,
        silent=silent,
        messages_per_minute=per_minute,
        sparkline=sparkline,
    )


def _permille(failures: int, lines: int) -> float | None:
    return round(1000 * failures / lines, 1) if lines else None


def parse_failures_box(db: Session, now: datetime) -> DashboardParseFailures:
    """Today's parse failures by source and pattern, and the last 7 days' totals.

    ``rawlog.invalid`` lines (not in the collector's format) have no ``messages`` row: the web
    app offers only their example line, not a search.
    """
    today = site_day(now)
    first = today - timedelta(days=FAILURE_DAYS - 1)
    lines: dict[tuple[date, str], int] = {
        (day, source): int(count)
        for day, source, count in db.execute(
            select(IngestOffset.day, IngestOffset.source, IngestOffset.line_number).where(
                IngestOffset.day >= first, IngestOffset.day <= today
            )
        )
    }
    failures: dict[tuple[date, str], int] = {
        (day, source): int(count)
        for day, source, count in db.execute(
            select(ParseFailure.day, ParseFailure.source, func.sum(ParseFailure.count))
            .where(ParseFailure.day >= first, ParseFailure.day <= today)
            .group_by(ParseFailure.day, ParseFailure.source)
        )
    }
    patterns = db.scalars(
        select(ParseFailure)
        .where(ParseFailure.day == today)
        .order_by(ParseFailure.count.desc(), ParseFailure.source, ParseFailure.pattern)
        .limit(FAILURE_PATTERNS)
    ).all()
    sources = {source for day, source in lines | failures if day == today}
    by_source = sorted(
        (
            SourceFailures(
                source=source,
                lines=lines.get((today, source), 0),
                failures=failures.get((today, source), 0),
                permille=_permille(failures.get((today, source), 0), lines.get((today, source), 0)),
            )
            for source in sources
            if failures.get((today, source), 0)
        ),
        key=lambda row: (-row.failures, row.source),
    )[:FAILURE_SOURCES]
    days = [first + timedelta(days=n) for n in range(FAILURE_DAYS)]
    last7 = [
        FailureDay(
            day=day,
            lines=sum(count for (d, _), count in lines.items() if d == day),
            failures=sum(count for (d, _), count in failures.items() if d == day),
        )
        for day in days
    ]
    total = last7[-1]
    return DashboardParseFailures(
        day=today,
        lines=total.lines,
        failures=total.failures,
        permille=_permille(total.failures, total.lines),
        by_source=by_source,
        top_patterns=[
            FailurePattern(
                source=row.source,
                pattern=row.pattern,
                count=int(row.count),
                example=RawRef(
                    source=row.source, day=row.day, line=row.example_line, offset=row.example_offset
                ),
            )
            for row in patterns
        ],
        last7=last7,
    )


# -- sealing, credit, clock -----------------------------------------------------------------------


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _serial_short(serial: Any) -> str | None:
    if not isinstance(serial, str) or not serial:
        return None
    return serial if len(serial) <= 8 else f"{serial[:8]}…"


def seal_box(db: Session, layout: Layout, config: Config, now: datetime) -> DashboardSeal:
    view = VaultView(layout, config, now=now)
    yesterday = view.today - timedelta(days=1)
    days = [yesterday - timedelta(days=n) for n in range(CALENDAR_DAYS - 1, -1, -1)]
    on_demand = on_demand_verifications(db, days[0], days[-1])
    cells = {day: view.summary(day, on_demand.get(day)) for day in days}
    last = cells[yesterday]
    verification = last.get("verification")
    return DashboardSeal(
        yesterday=SealYesterday(
            day=yesterday,
            status=SEAL_STATUS.get(str(last["state"]), "no_data"),
            sealed_at=_moment(last.get("sealed_at")),
            serial_short=_serial_short(last.get("serial")),
            verified=bool(verification["ok"]) if isinstance(verification, dict) else None,
        ),
        calendar=[
            SealCalendarDay(day=day, status=SEAL_STATUS.get(str(cell["state"]), "no_data"))
            for day, cell in cells.items()
        ],
    )


def estimated_days(entries: list[dict[str, Any]]) -> int | None:
    """Days the newest known credit lasts at the use of the last 30 days of checks."""
    known = [(entry["time"], entry["credits"]) for entry in entries if entry["credits"] is not None]
    if not known:
        return None
    newest_at, remaining = known[-1]
    recent = [(at, credits) for at, credits in known if at >= newest_at - CREDIT_WINDOW]
    span = (recent[-1][0] - recent[0][0]).total_seconds() / 86400
    # Only decreases count: a purchase raises the credit and is not negative use.
    used = sum(max(0, before - after) for (_, before), (_, after) in pairwise(recent))
    per_day = used / span if span >= 1 and used > 0 else DEFAULT_STAMPS_PER_DAY
    return int(max(0, remaining) // per_day)


def credit_box(layout: Layout, config: Config) -> DashboardCredit:
    entries, _ = credit_history(layout)
    known = [entry["credits"] for entry in entries if entry["credits"] is not None]
    return DashboardCredit(
        remaining=known[-1] if known else None,
        estimated_days=estimated_days(entries),
        threshold=config.tsa.credit_alert_threshold,
    )


def _last_sample(path: Path) -> dict[str, Any] | None:
    """The last JSON object of a clock samples file (read from its end)."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(0, handle.tell() - 16384))
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            return entry
    return None


def clock_box(layout: Layout, config: Config, now: datetime) -> DashboardClock:
    today = site_day(now)
    sample = _last_sample(layout.clock_samples(today)) or _last_sample(
        layout.clock_samples(today - timedelta(days=1))
    )
    # The server the clock check asks first: the local time source, else the public fallback.
    configured = next(iter(config.clock.servers), None)
    if sample is None:
        return DashboardClock(offset_ms=None, ntp_server=configured, checked_at=None, ok=None)
    at = _moment(sample.get("time"))
    offset = sample.get("offset_us")
    offset_us: int | None = offset if sample.get("ok") is True and type(offset) is int else None
    server = sample.get("server")
    ok: bool | None = None
    if at is not None and now - at <= CLOCK_STALE:
        ok = offset_us is not None and abs(offset_us) <= config.clock.alert_offset_ms * 1000
    return DashboardClock(
        offset_ms=None if offset_us is None else offset_us / 1000,
        ntp_server=server if isinstance(server, str) and server else configured,
        checked_at=at,
        ok=ok,
    )


# -- the endpoint ---------------------------------------------------------------------------------


@router.get("/summary")
def dashboard_summary(
    db: DbSession, user: ViewerDep, layout: LayoutDep, config: ConfigDep
) -> DashboardSummary:
    """Every box the user may see (see the module text); the others are ``null``."""
    now = utc_now()
    ingest_visible = user.has("sources.read") or user.has("dashboard.view")
    return DashboardSummary(
        generated_at=to_site(now),
        ingest=ingest_box(db, layout, now) if ingest_visible else None,
        seal=seal_box(db, layout, config, now) if user.has("seal.read") else None,
        credit=credit_box(layout, config) if user.has("seal.read") else None,
        clock=clock_box(layout, config, now)
        if user.has("system.read") or user.has("seal.read")
        else None,
        alerts=DashboardAlerts(open_by_severity=open_by_severity(db), latest=latest_active(db))
        if user.has("alerts.read")
        else None,
        parse_failures=parse_failures_box(db, now) if ingest_visible else None,
    )
