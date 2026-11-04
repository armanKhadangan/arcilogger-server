"""Range partitions of the time-series tables on plain PostgreSQL (no TimescaleDB), and the
database window: everything older than a cutoff day goes as whole partitions or chunks.

Without TimescaleDB the baseline creates every time-series table as ``PARTITION BY RANGE
(time)`` with a ``<table>_default`` partition (ADR-0005: at 1-5 million messages a day,
unpartitioned heaps with DELETE-based retention would not be workable on the 4-core Windows
server). The database keeps the last ``db.retention_days`` days (default 30, Wave 5 contract G1);
older days come back from the sealed vault on request (archive reload, G4). This module

* creates the **daily** partitions ``<table>_pYYYYMMDD`` — one per **UTC day**, ``[D 00:00 UTC,
  D+1 00:00 UTC)``, as TimescaleDB's 1-day chunks are — for the window (the device times of the
  site days ``today - db.retention_days`` up to :data:`DAYS_AHEAD` days ahead,
  :func:`ensure_partitions`), and those of the site days an archive reload brings back
  (:func:`ensure_days`). UTC days, not site days: the lookup engine asks a range of a day or
  more one UTC day at a time (``lookup.engine.CHUNK``), and each such step then reads one
  partition; a site day (+03:00) would be two, and an ordered ``LIMIT 1`` over two partitions
  walks the second one's rows of the address before it answers. A site day's device times lie
  in two daily partitions (:func:`utc_days`). The **monthly** partitions ``<table>_pYYYYMM`` of
  earlier versions (bounds at local midnight, Europe/Istanbul) are never split (G2): revision
  ``0021_db_window`` drops the empty ones of the months after the migration, and the daily
  partition of the UTC day a monthly one ends in (or begins in) covers only the part of that day
  outside it (**change-over rule**: a daily partition is its UTC day less every partition that
  overlaps it, :func:`_free_part`). After the current month (ending at 21:00 UTC the day before
  the 1st) comes a 3-hour daily partition named for that UTC day (e.g. ``_p20261130``, ``[30.11.
  21:00 UTC, 01.12. 00:00 UTC)``), then whole UTC days; before a monthly partition kept ahead
  (it held rows) a 21-hour one. No device time between them falls into the default partition.
  The monthly ones of the past go with the daily partition after them (the drop rule below), up
  to one extra month during the change-over. An explicit ``months_back`` still asks for a fixed
  range of monthly partitions (the test template, the performance tests);
* moves rows that landed in the default partition before their day existed into the new
  partition (the default partition otherwise only holds outliers, e.g. a device clock in 2001);
* drops everything older than a cutoff day (:func:`drop_before`; :func:`maintain_window` for
  the worker job ``db.window``, G3): whole partitions — no DELETE of hundreds of millions of
  rows, no vacuum storm — once the UTC day they end in has ended by the cutoff's local midnight
  (**drop rule**, :func:`_drop_end`), the older rows of the default partitions, on TimescaleDB the
  chunks that end by that midnight (``drop_chunks``): the same rule on both, a daily partition or
  chunk holding that midnight keeps the 21 hours of the day before it, and a monthly partition
  (ending at 21:00 UTC) goes together with the change-over daily partition after it, so an
  archive reload of its days finds their UTC days free and none of their device times falls into
  the default partition. And the search summary counts (``search_counts``)
  and clock samples (``source_clock_samples``) of what went. The newest cutoff ever applied is
  kept in the status row :data:`DROPPED_KEY` in the same transaction (:func:`dropped_before`):
  the API reads it to know which days are gone. :func:`maintain_window` drops a backlog in
  steps of at most :data:`DAYS_PER_RUN` days a transaction, oldest first (a first run after an
  upgrade from the 400-day window would otherwise lock thousands of partitions or chunks at
  once and fail for want of shared memory, every day).

Partitions and chunks are by device time (``time``); a raw day's file (``raw_day``) has rows
up to :data:`~arcilogger_db.searchcounts.COUNTS_TIME_MARGIN` either side of its day
(:func:`~arcilogger_db.searchcounts.stream_window`), so its rows lie in the site days of
:data:`RAW_DAY_MARGIN` days either side. Callers that keep a raw day whole (the unsealed days of
``db.window``) or load one again (archive reload) widen their site days by it; the functions
here take site days and find the partitions of their device times.

Callers: ``arcilogger-db upgrade`` and ``arcilogger-db partitions`` (both run
:func:`ensure_partitions`), the worker (``partitions.ensure`` daily, ``db.window``), ArciIngest
before an archive reload (:func:`ensure_days`). With TimescaleDB (hypertables) the partition
functions find no partitioned table and do nothing; :func:`drop_before` drops chunks.

SQL: partition DDL cannot take bind parameters, so statements are built as text. Identifiers are
generated here from :data:`TIME_SERIES_TABLES` and checked by ``_quoted``; bounds are computed
datetimes (``_literal``) — nothing comes from user input (hence the ``noqa: S608`` markers).

Locking: each table is handled under ``LOCK TABLE <parent> IN ACCESS EXCLUSIVE MODE`` (parent
before partition, the same order as an insert, so no deadlock with the ingest service); ingest
waits for the few milliseconds a new, empty partition takes. Only the rare catch-up of rows
from the default partition holds the lock longer. A transaction-level advisory lock serialises
concurrent maintenance runs (:func:`lock_partitions`); the caller provides the transaction
(``engine.begin()``). The searches, the lookups and an export hold it shared; the worker's steps
wait for it at most half of ``search.request_budget_ms`` (:func:`step_lock_timeout_ms`, W5-G-24)
and try again at their next run, so a search does not queue behind a step that waits. On
TimescaleDB :func:`drop_before` locks the chunks that go after that lock and before
``drop_chunks``, in chunk-id order (a query's planning locks them in it), so that neither the
compression policy nor a reader of the chunks meets it in the opposite order (#133).
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from typing import Final

from alembic.runtime.environment import NameFilterParentNames, NameFilterType
from sqlalchemy import Connection, delete, func, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError

from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.models import TIME_SERIES_TABLES, SearchCount, Setting, SourceClockSample
from arcilogger_db.searchcounts import COUNTS_TIME_MARGIN
from arcilogger_db.settings import DEFAULT_SETTINGS, INTEGER_RANGES, SYSTEM_AUTHOR

#: Days created ahead of today (the worker runs daily; this is the safety margin, G2).
DAYS_AHEAD: Final = 7
#: The most days one call of :func:`ensure_partitions` creates partitions for (16 tables each,
#: about 11 locks a partition with its indexes, all held until the commit): 256 partitions stay
#: within PostgreSQL's default lock table (``max_locks_per_transaction`` 64 per connection) as
#: the 17 monthly ones of every table an earlier version's first upgrade created did. A new
#: database gets today and the days ahead first, the window's past days with the next runs (the
#: worker's at its start-up); a sealed day loaded before (ingest) calls :func:`ensure_days`.
DAYS_PER_RUN: Final = 16
#: ``arcilogger-db check`` asks for attention when fewer days ahead are covered.
DAYS_AHEAD_WARNING: Final = 3
#: The smallest window (the lower bound of ``db.retention_days``): :func:`maintain_window` never
#: drops a day this recent.
MIN_WINDOW_DAYS: Final = INTEGER_RANGES["db.retention_days"][0]
#: Months created ahead of the current one by an explicit monthly range (``months_back``).
MONTHS_AHEAD: Final = 3
#: Fewest months :func:`months_back_for` names.
MONTHS_BACK: Final = 1
#: pg_advisory_xact_lock key ("ARCIPART" as a signed 64-bit integer).
PARTITION_LOCK_KEY: Final = int.from_bytes(b"ARCIPART", "big") - 2**63
#: The worker's steps (``db.window``, ``partitions.ensure``) wait for :data:`PARTITION_LOCK_KEY`
#: at most ``search.request_budget_ms`` divided by this (W5-G-24, :func:`step_lock_timeout_ms`).
STEP_LOCK_DIVISOR: Final = 2
#: PostgreSQL ``lock_not_available``: ``lock_timeout`` ran out.
LOCK_NOT_AVAILABLE: Final = "55P03"
#: Whole days a raw day's rows reach into the partitions either side of it (module text): 2, the
#: day of a device time 25 hours before the raw day's midnight.
RAW_DAY_MARGIN: Final = math.ceil(COUNTS_TIME_MARGIN / timedelta(days=1))
#: The status row of the newest cutoff :func:`drop_before` applied, ``{"cutoff": "YYYY-MM-DD",
#: "at": <ISO time>}``: the site days before it have been dropped, whole or in part (every
#: partition or chunk the drop rule let go at its local midnight; the one holding that midnight
#: keeps up to 21 hours of the day before), the days from it on are complete (archive reloads
#: bring days back). Never in ``DEFAULT_SETTINGS`` and never editable (like ``db.timescale``);
#: written with the drop, in its transaction.
DROPPED_KEY: Final = "db.window_dropped_before"

_MONTHLY = re.compile(r"^(?P<table>[a-z_]+)_p(?P<year>\d{4})(?P<month>\d{2})$")
_DAILY = re.compile(r"^(?P<table>[a-z_]+)_p(?P<year>\d{4})(?P<month>\d{2})(?P<day>\d{2})$")
_DEFAULT = re.compile(r"^(?P<table>[a-z_]+)_default$")


# -- names and bounds -----------------------------------------------------------------------------


def month_of(day: date) -> date:
    """First day of ``day``'s month."""
    return day.replace(day=1)


def add_months(month: date, count: int) -> date:
    index = month.year * 12 + month.month - 1 + count
    return date(index // 12, index % 12 + 1, 1)


def month_bounds(month: date) -> tuple[datetime, datetime]:
    """``[start, end)`` of a month: local midnight of its 1st day and of the next month's."""
    return day_window(month_of(month))[0], day_window(add_months(month_of(month), 1))[0]


def day_bounds(day: date) -> tuple[datetime, datetime]:
    """``[start, end)`` of the daily partition of the UTC day ``day``: its UTC midnight and the
    next one (less what a monthly partition covers, module text)."""
    start = datetime.combine(day, dt_time.min, UTC)
    return start, start + timedelta(days=1)


def utc_days(first_day: date, last_day: date) -> list[date]:
    """The UTC days whose daily partitions hold the device times of the site days ``first_day``
    … ``last_day``: from the UTC day of ``first_day``'s local midnight (the day before, at
    +03:00) to that of ``last_day``'s last instant."""
    first = day_window(first_day)[0].astimezone(UTC).date()
    last = (day_window(last_day)[1] - timedelta(microseconds=1)).astimezone(UTC).date()
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


def months_back_for(today: date, retention_days: int) -> int:
    """Months from the oldest day the database keeps (``today - retention_days``) to ``today``'s
    month — 13 for 400 days — and never fewer than :data:`MONTHS_BACK`."""
    oldest = month_of(today - timedelta(days=max(0, retention_days)))
    current = month_of(today)
    return max(MONTHS_BACK, (current.year - oldest.year) * 12 + current.month - oldest.month)


def retention_days(connection: Connection) -> int:
    """Setting ``db.retention_days``: the shipped default when unset or not a number, never
    fewer than :data:`MIN_WINDOW_DAYS`."""
    default = int(DEFAULT_SETTINGS["db.retention_days"])
    value = connection.execute(
        text("SELECT value FROM settings WHERE key = 'db.retention_days'")
    ).scalar_one_or_none()
    if isinstance(value, int) and not isinstance(value, bool) and value > 0:
        return max(MIN_WINDOW_DAYS, value)
    return default


class PartitionLockTimeoutError(TimeoutError):
    """:func:`lock_partitions` waited its ``timeout_ms`` and did not get the lock; the caller's
    transaction is aborted (it changed nothing that stays)."""


def step_lock_timeout_ms(connection: Connection) -> int:
    """How long a step of the worker waits for :data:`PARTITION_LOCK_KEY` (W5-G-24): half of
    setting ``search.request_budget_ms`` (the shipped default when unset or out of its range).

    A step that waits holds a request for the lock exclusively, and every search that comes
    meanwhile queues behind it, waiting at most its ``search.request_budget_ms`` for the lock
    shared (W5-G-18). A running export holds it shared for minutes (W5-G-23): the step gives
    up after half that budget, so a search queued behind it keeps at least the other half for
    its scan and answers no 504 because of the step."""
    key = "search.request_budget_ms"
    low, high = INTEGER_RANGES[key]
    value = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": key}
    ).scalar_one_or_none()
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or value < low
        or (high is not None and value > high)
    ):
        value = int(DEFAULT_SETTINGS[key])
    return value // STEP_LOCK_DIVISOR


def lock_partitions(connection: Connection, *, timeout_ms: int | None = None) -> None:
    """Take :data:`PARTITION_LOCK_KEY` exclusively for the caller's transaction.

    ``timeout_ms`` (the worker's steps, :func:`step_lock_timeout_ms`): wait at most that long
    (:class:`PartitionLockTimeoutError`; PostgreSQL has aborted the transaction then); the other
    lock waits of the transaction keep the ``lock_timeout`` they had. ``None``: wait as long as
    it takes (the upgrade, ``arcilogger-db partitions``, a lock the transaction holds
    already)."""
    if timeout_ms is None:
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
        return
    before = connection.execute(text("SELECT current_setting('lock_timeout')")).scalar_one()
    try:
        connection.execute(
            text(
                "SELECT pg_advisory_xact_lock(:key) "
                "FROM (SELECT set_config('lock_timeout', :wait, true)) AS deadline"
            ),
            {"key": PARTITION_LOCK_KEY, "wait": f"{max(1, timeout_ms)}ms"},
        )
    except DBAPIError as exc:
        if getattr(exc.orig, "sqlstate", None) == LOCK_NOT_AVAILABLE:
            raise PartitionLockTimeoutError(
                f"partition lock not granted within {timeout_ms} ms"
            ) from exc
        raise
    connection.execute(text("SELECT set_config('lock_timeout', :before, true)"), {"before": before})


def partition_name(table: str, month: date) -> str:
    """Name of a monthly partition (``<table>_pYYYYMM``)."""
    return f"{table}_p{month:%Y%m}"


def day_partition_name(table: str, day: date) -> str:
    """Name of the daily partition of a UTC day (``<table>_pYYYYMMDD``)."""
    return f"{table}_p{day:%Y%m%d}"


def default_partition_name(table: str) -> str:
    return f"{table}_default"


def is_partition_table(name: str) -> bool:
    """Whether ``name`` is a partition created here (Alembic compares only the parents)."""
    match = _MONTHLY.match(name) or _DAILY.match(name) or _DEFAULT.match(name)
    return match is not None and match.group("table") in TIME_SERIES_TABLES


def include_name(
    name: str | None, type_: NameFilterType, parent_names: NameFilterParentNames
) -> bool:
    """Alembic ``include_name`` hook: partitions are maintenance objects, not model tables."""
    return not (type_ == "table" and name is not None and is_partition_table(name))


def _literal(value: datetime) -> str:
    """Partition bounds are DDL: no bind parameters. Values are computed here, never input."""
    return f"'{value.isoformat()}'"


def _quoted(name: str) -> str:
    if not re.fullmatch(r"[a-z_][a-z0-9_]*", name):  # pragma: no cover - names are generated
        raise ValueError(f"unexpected identifier {name!r}")
    return f'"{name}"'


# -- inspection -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Partition:
    table: str
    name: str
    #: First day of the month of a monthly partition, else ``None``.
    month: date | None
    is_default: bool
    #: The UTC day of a daily partition, else ``None``.
    day: date | None = None
    #: ``[start, end)`` of device times, as the catalog has them (a daily partition of the
    #: change-over covers part of its UTC day only); ``None`` for the default partition or an
    #: unbounded one (``MINVALUE``, ``MAXVALUE``).
    start: datetime | None = None
    end: datetime | None = None

    @property
    def named(self) -> bool:
        """A monthly or daily partition made here (only these are dropped)."""
        return self.month is not None or self.day is not None


def partitioned_tables(connection: Connection) -> list[str]:
    """Time-series tables that are natively partitioned (empty with TimescaleDB)."""
    rows = connection.execute(
        text(
            "SELECT c.relname FROM pg_partitioned_table p "
            "JOIN pg_class c ON c.oid = p.partrelid "
            "WHERE c.relnamespace = CAST(current_schema() AS regnamespace) "
            "AND c.relname = ANY(:tables)"
        ),
        {"tables": list(TIME_SERIES_TABLES)},
    ).scalars()
    found = set(rows)
    return [table for table in TIME_SERIES_TABLES if table in found]


def hypertables(connection: Connection) -> list[str]:
    """Time-series tables that are TimescaleDB hypertables (empty on plain PostgreSQL)."""
    installed = connection.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'timescaledb'")
    ).scalar_one_or_none()
    if installed is None:
        return []
    rows = connection.execute(
        text(
            "SELECT hypertable_name FROM timescaledb_information.hypertables "
            "WHERE hypertable_schema = current_schema() AND hypertable_name = ANY(:tables)"
        ),
        {"tables": list(TIME_SERIES_TABLES)},
    ).scalars()
    found = set(rows)
    return [table for table in TIME_SERIES_TABLES if table in found]


#: The bounds of a range partition as the catalog prints them (``MINVALUE`` and ``MAXVALUE``
#: give ``NULL``), read by :func:`_bound`.
_BOUNDS_SQL: Final = (
    "SELECT c.relname, b.expr = 'DEFAULT', m[1], m[2] "
    "FROM pg_inherits i JOIN pg_class c ON c.oid = i.inhrelid "
    "CROSS JOIN LATERAL (SELECT pg_get_expr(c.relpartbound, c.oid) AS expr) b "
    "LEFT JOIN LATERAL regexp_match(b.expr, "
    "'^FOR VALUES FROM \\(''([^'']*)''\\) TO \\(''([^'']*)''\\)$') m ON true "
    "WHERE i.inhparent = CAST(:table AS regclass)"
)


def _bound(value: str | None) -> datetime | None:
    """A bound printed in PostgreSQL's default ``DateStyle`` ISO (``2026-11-30 21:00:00+00``,
    always with its offset, whatever the session's ``TimeZone``). A session with another
    ``DateStyle`` fails here (``ValueError``) instead of reading a wrong bound: its time zone
    abbreviations are ambiguous ("IST")."""
    return None if value is None else datetime.fromisoformat(value)


def list_partitions(connection: Connection, table: str) -> list[Partition]:
    """Partitions of ``table`` in time order (monthly and daily ones), then the default
    partition."""
    rows = connection.execute(text(_BOUNDS_SQL), {"table": table}).all()
    found: list[Partition] = []
    for name, is_default, start, end in rows:
        month = day = None
        if (match := _MONTHLY.match(name)) and match.group("table") == table:
            month = date(int(match.group("year")), int(match.group("month")), 1)
        elif (match := _DAILY.match(name)) and match.group("table") == table:
            day = date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
        found.append(
            Partition(table, name, month, bool(is_default), day, _bound(start), _bound(end))
        )

    def order(partition: Partition) -> tuple[bool, datetime, str]:
        start = partition.start or datetime.max.replace(tzinfo=UTC)
        return partition.is_default, start, partition.name

    return sorted(found, key=order)


def _overlaps(partitions: list[Partition], start: datetime, end: datetime) -> bool:
    """Whether a partition overlaps the device times ``[start, end)``."""
    return any(
        p.start is not None and p.end is not None and p.start < end and start < p.end
        for p in partitions
    )


def _drop_end(end: datetime) -> datetime:
    """The instant from which a partition or chunk ending at ``end`` may go (module text, the drop
    rule): the end of the UTC day it ends in. ``end`` itself for a daily partition or a chunk; the
    UTC midnight after a monthly partition's 21:00 UTC, so that it goes with the daily partition
    after it, which the name of that UTC day would otherwise keep from covering the rest of it
    (:func:`_needed`)."""
    return day_bounds((end - timedelta(microseconds=1)).astimezone(UTC).date())[1]


def _free_part(
    partitions: list[Partition], start: datetime, end: datetime
) -> tuple[datetime, datetime] | None:
    """The part of ``[start, end)`` that no partition covers, from its first free instant to the
    next partition (module text, the change-over rule); ``None`` when it is covered whole. A UTC
    day overlaps at most a monthly partition that ends in it or one that begins in it (a month
    is longer than a day), so the free part is one range."""
    bounded = sorted(
        (p.start, p.end) for p in partitions if p.start is not None and p.end is not None
    )
    for low, high in bounded:
        if high <= start or end <= low:
            continue
        if low <= start:
            start = high  # covers the beginning: the free part starts after it
        else:
            end = low  # begins inside: the free part ends there
            break
    return (start, end) if start < end else None


# -- maintenance ----------------------------------------------------------------------------------


@dataclass
class PartitionReport:
    tables: list[str] = field(default_factory=list)
    created: list[str] = field(default_factory=list)
    #: Rows moved out of a default partition, by new partition.
    moved: dict[str, int] = field(default_factory=dict)
    #: Partitions dropped, and on TimescaleDB the chunks (``schema.name``).
    dropped: list[str] = field(default_factory=list)
    #: Rows older than the cutoff deleted from default partitions.
    purged: int = 0
    #: ``search_counts`` rows deleted with the rows they count (:func:`drop_before`).
    counts_deleted: int = 0
    #: ``source_clock_samples`` rows of the minutes received before the kept rows.
    clock_samples_deleted: int = 0
    #: TimescaleDB hypertables handled by :func:`drop_before`.
    hypertables: list[str] = field(default_factory=list)
    #: Days of the window still without partitions (:data:`DAYS_PER_RUN`); the next run
    #: creates them.
    remaining: int = 0
    #: Days the call created partitions for (each of them for every table that lacked one).
    days_created: int = 0
    #: The cutoff :func:`maintain_window` applied: its step, which may be before the cutoff it
    #: was given (``None``: nothing dropped by this call, the partitions took its share).
    applied: date | None = None


def ensure_partitions(
    connection: Connection,
    *,
    today: date | None = None,
    months_ahead: int = MONTHS_AHEAD,
    months_back: int | None = None,
    limit: int = DAYS_PER_RUN,
    since: date | None = None,
    lock_timeout_ms: int | None = None,
) -> PartitionReport:
    """Create the missing partitions around ``today`` (site day); idempotent.

    Without ``months_back`` (``arcilogger-db upgrade``, ``arcilogger-db partitions``, the worker):
    the daily partitions of the window, the UTC days of the site days ``today -
    db.retention_days`` up to :data:`DAYS_AHEAD` days ahead (:func:`utc_days`: one more than
    site days, the first one for the window's first three hours), at most ``limit``
    (:data:`DAYS_PER_RUN`) days a call (today and ahead first, then the past newest first;
    ``remaining`` counts the rest), none that a cutoff ``since`` is about to drop (the drop rule:
    a UTC day that ends by its local midnight); ``months_ahead`` does not apply. With
    ``months_back``: the monthly partitions of ``months_back`` months before ``today``'s up to
    ``months_ahead`` after it, as earlier versions made them; a month that already has a
    partition (a daily one) is left as it is. ``lock_timeout_ms``: the partition maintenance
    lock is waited for at most that long (:func:`lock_partitions`; the worker's
    ``partitions.ensure``).
    """
    report = PartitionReport(tables=partitioned_tables(connection))
    if not report.tables:
        return report
    lock_partitions(connection, timeout_ms=lock_timeout_ms)
    day = today or site_day(utc_now())
    if months_back is None:
        # Today and the days ahead first, then the window's past days newest first.
        oldest = day - timedelta(days=retention_days(connection))
        days = utc_days(oldest, day + timedelta(days=DAYS_AHEAD))
        window = [d for d in days if d >= day] + [d for d in reversed(days) if d < day]
        if since is not None:
            window = [d for d in window if day_bounds(d)[1] > day_window(since)[0]]
        missing = _missing(connection, report.tables, window)
        report.remaining = max(0, len(missing) - max(0, limit))
        report.days_created = len(missing[: max(0, limit)])
        _ensure_days(connection, report, missing[: max(0, limit)])
        return report
    current = month_of(day)
    months = [add_months(current, n) for n in range(-months_back, months_ahead + 1)]
    for table in report.tables:
        existing = list_partitions(connection, table)
        default = next((p.name for p in existing if p.is_default), None)
        for month in months:
            if not _overlaps(existing, *month_bounds(month)):
                name = partition_name(table, month)
                _create(connection, report, table, name, month_bounds(month), default)
    return report


def ensure_days(connection: Connection, first_day: date, last_day: date) -> PartitionReport:
    """Create the daily partitions that hold the device times of the site days ``first_day`` …
    ``last_day`` (both included; :func:`utc_days`) where no partition covers them yet, e.g.
    before an archive reload loads them; idempotent."""
    report = PartitionReport(tables=partitioned_tables(connection))
    if not report.tables or last_day < first_day:
        return report
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
    days = utc_days(first_day, last_day)
    report.days_created = len(_missing(connection, report.tables, days))
    _ensure_days(connection, report, days)
    return report


def _missing(connection: Connection, tables: list[str], days: list[date]) -> list[date]:
    """The UTC days of ``days`` (in their order) that some table still needs a partition for."""
    existing = {table: list_partitions(connection, table) for table in tables}
    return [
        day
        for day in days
        if any(_needed(partitions, table, day) for table, partitions in existing.items())
    ]


def _needed(existing: list[Partition], table: str, day: date) -> tuple[datetime, datetime] | None:
    """The bounds of the UTC day's daily partition when it has none yet: the part of the day no
    partition covers (module text, the change-over rule); ``None`` when it is covered whole or
    its name is taken (a part a partition leaves free in the middle of the day, not made here:
    its rows wait in the default partition)."""
    name = day_partition_name(table, day)
    if any(p.name == name for p in existing):
        return None
    return _free_part(existing, *day_bounds(day))


def _ensure_days(connection: Connection, report: PartitionReport, days: list[date]) -> None:
    for table in report.tables:
        existing = list_partitions(connection, table)
        default = next((p.name for p in existing if p.is_default), None)
        for day in days:
            _ensure_day(connection, report, table, existing, default, day)


def _ensure_day(
    connection: Connection,
    report: PartitionReport,
    table: str,
    existing: list[Partition],
    default: str | None,
    day: date,
) -> None:
    bounds = _needed(existing, table, day)
    if bounds is None:
        return
    name = day_partition_name(table, day)
    _create(connection, report, table, name, bounds, default)
    existing.append(Partition(table, name, None, False, day, *bounds))


def _create(
    connection: Connection,
    report: PartitionReport,
    table: str,
    name: str,
    bounds: tuple[datetime, datetime],
    default: str | None,
) -> None:
    moved = _create_partition(connection, table, name, bounds, default)
    report.created.append(name)
    if moved:
        report.moved[name] = moved


def _create_partition(
    connection: Connection,
    table: str,
    name: str,
    bounds: tuple[datetime, datetime],
    default: str | None,
) -> int:
    start, end = bounds
    values = f"FROM ({_literal(start)}) TO ({_literal(end)})"
    in_range = f"time >= {_literal(start)} AND time < {_literal(end)}"
    connection.execute(text(f"LOCK TABLE {_quoted(table)} IN ACCESS EXCLUSIVE MODE"))
    stray = 0
    if default is not None:
        stray = int(
            connection.execute(
                text(f"SELECT count(*) FROM {_quoted(default)} WHERE {in_range}")  # noqa: S608
            ).scalar_one()
        )
    if stray == 0:
        connection.execute(
            text(f"CREATE TABLE {_quoted(name)} PARTITION OF {_quoted(table)} FOR VALUES {values}")
        )
        return 0
    # Rows of this range arrived before the partition existed: move them, then attach
    # (PostgreSQL refuses to create a partition whose range still has rows in the default one).
    if default is None:  # pragma: no cover - stray rows imply a default partition
        raise RuntimeError(f"{table}: rows to move but no default partition")
    connection.execute(
        text(
            f"CREATE TABLE {_quoted(name)} (LIKE {_quoted(table)} INCLUDING DEFAULTS "
            "INCLUDING CONSTRAINTS INCLUDING STORAGE)"
        )
    )
    connection.execute(
        text(
            f"INSERT INTO {_quoted(name)} SELECT * FROM {_quoted(default)} "  # noqa: S608
            f"WHERE {in_range}"
        )
    )
    connection.execute(text(f"DELETE FROM {_quoted(default)} WHERE {in_range}"))  # noqa: S608
    connection.execute(
        text(f"ALTER TABLE {_quoted(table)} ATTACH PARTITION {_quoted(name)} FOR VALUES {values}")
    )
    return stray


def drop_before(connection: Connection, cutoff: date) -> PartitionReport:
    """Drop everything older than the site day ``cutoff``: every partition the drop rule lets go
    at its first instant (local midnight; :func:`_drop_end`), the older rows of the default
    partitions, on TimescaleDB every chunk that ends on or before that instant (``drop_chunks``),
    and the search
    summary counts (``search_counts``, ADR-0015) and clock samples (``source_clock_samples``,
    ADR-0016 D28) of the rows that went.

    A partition or chunk holding the cutoff is kept whole (no DELETE): a monthly partition of the
    change-over keeps up to one month more (and the 3 hours after it, until the daily partition
    of its last UTC day goes with it), a daily partition or a TimescaleDB chunk (a UTC day) up to
    21 hours of the day before. The counts stay with their rows: a count goes only when its
    hour lies before the first kept partition or chunk of ``messages`` (or before the cutoff
    when that starts later), so the summary table keeps counting exactly the rows that are
    left. The status row :data:`DROPPED_KEY` keeps the newest cutoff applied (a cutoff before it
    changes nothing there: those days went earlier).

    Unbounded: one call drops the whole backlog in the caller's transaction (tests, small
    ranges); :func:`maintain_window` steps through a long one.
    """
    report = PartitionReport(
        tables=partitioned_tables(connection), hypertables=hypertables(connection)
    )
    limit = day_window(cutoff)[0]
    kept_from = limit
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
    for table in report.tables:
        for partition in list_partitions(connection, table):
            start, end = partition.start, partition.end
            if partition.is_default:
                deleted = connection.execute(
                    text(
                        f"DELETE FROM {_quoted(partition.name)} "  # noqa: S608
                        f"WHERE time < {_literal(limit)}"
                    )
                )
                report.purged += int(deleted.rowcount or 0)
            elif not partition.named or start is None or end is None:
                continue  # not made here: left alone
            elif _drop_end(end) <= limit:
                connection.execute(text(f"LOCK TABLE {_quoted(table)} IN ACCESS EXCLUSIVE MODE"))
                connection.execute(text(f"DROP TABLE {_quoted(partition.name)}"))
                report.dropped.append(partition.name)
            elif start < limit and table == "messages":
                kept_from = min(kept_from, start)
    # The chunks that go are locked first (#133): ``drop_chunks`` locks the catalog rows of its
    # chunks before their tables, ``compress_chunk`` (the compression policy) its chunk's table
    # before the catalog row. Waiting on the tables before holding any of those rows lets a
    # policy run that has opened a chunk finish (it commits per chunk). The order is chunk id,
    # the order in which a query's planning locks the chunks: any other order (the names sort
    # ``_10_`` before ``_9_``) deadlocks with a reader of all the chunks. ``drop_chunks`` itself
    # locks by OID, the same order until the OID counter wraps around. A chunk created after
    # ``show_chunks`` (a late row of an old day) is only locked by ``drop_chunks``; no policy
    # can have opened one that new.
    for table in report.hypertables:
        old = connection.execute(
            text(
                "SELECT CAST(c AS text) FROM show_chunks(CAST(:table AS regclass), "
                "older_than => CAST(:limit AS timestamptz)) AS c "
                "ORDER BY _timescaledb_functions.chunk_id_from_relid(c)"
            ),
            {"table": table, "limit": limit},
        ).scalars()
        for chunk in list(old):  # a regclass as text: quoted where needed
            connection.execute(text(f"LOCK TABLE {chunk} IN ACCESS EXCLUSIVE MODE"))
    for table in report.hypertables:
        chunks = connection.execute(
            text(
                "SELECT CAST(c AS text) FROM drop_chunks(CAST(:table AS regclass), "
                "older_than => CAST(:limit AS timestamptz)) AS c"
            ),
            {"table": table, "limit": limit},
        ).scalars()
        report.dropped.extend(chunks)
    if "messages" in report.hypertables:
        first_chunk = connection.execute(
            text(
                "SELECT min(range_start) FROM timescaledb_information.chunks "
                "WHERE hypertable_schema = current_schema() AND hypertable_name = 'messages'"
            )
        ).scalar_one_or_none()
        if first_chunk is not None:
            kept_from = min(kept_from, first_chunk)
    # Hours are UTC, the bounds of daily partitions and chunks UTC midnights, those of monthly
    # partitions (and the daily ones beside them) local midnights (+03:00): whole hours, no hour
    # is split.
    counted = connection.execute(delete(SearchCount).where(SearchCount.hour < kept_from))
    report.counts_deleted = int(counted.rowcount or 0)
    # The clock samples by receive time: a row is received within a day of its device time, so
    # the minutes received before the kept rows sample the rows that went (and a day more).
    sampled = connection.execute(
        delete(SourceClockSample).where(SourceClockSample.minute < kept_from)
    )
    report.clock_samples_deleted = int(sampled.rowcount or 0)
    _record_dropped(connection, cutoff)
    return report


def dropped_before(connection: Connection) -> date | None:
    """The newest cutoff :func:`drop_before` applied (:data:`DROPPED_KEY`): every device time
    before its local midnight has been dropped. ``None`` when nothing was dropped yet (or the
    status row cannot be read: a database restored without it)."""
    value = connection.execute(
        text("SELECT value FROM settings WHERE key = :key"), {"key": DROPPED_KEY}
    ).scalar_one_or_none()
    try:
        return date.fromisoformat(str(value["cutoff"])) if isinstance(value, dict) else None
    except (KeyError, ValueError):
        return None


def _record_dropped(connection: Connection, cutoff: date) -> None:
    """Keep the newest applied cutoff in :data:`DROPPED_KEY` (an earlier one leaves it)."""
    earlier = dropped_before(connection)
    if earlier is not None and earlier >= cutoff:
        return
    value = {"cutoff": cutoff.isoformat(), "at": utc_now().isoformat(timespec="seconds")}
    connection.execute(
        insert(Setting)
        .values(key=DROPPED_KEY, value=value, updated_by=SYSTEM_AUTHOR)
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "updated_by": SYSTEM_AUTHOR, "updated_at": func.now()},
        )
    )


def next_drop_cutoff(connection: Connection, cutoff: date, *, days: int = DAYS_PER_RUN) -> date:
    """The cutoff of the next bounded step towards ``cutoff``: the oldest partition or chunk that
    the drop rule lets go at its local midnight (:func:`_drop_end`) starts at ``S``; the step
    drops what ends by ``S + days`` (the first local midnight at or after it: ``days`` daily
    partitions or chunks), at least that oldest partition or chunk whole (e.g. a monthly one of
    the change-over, with the daily partition after it), never beyond ``cutoff``. ``cutoff``
    itself when nothing that old is left (the default partitions' rows are a DELETE, not a lock
    per day)."""
    limit = day_window(cutoff)[0]
    starts: list[datetime] = []
    ends: list[datetime] = []
    for table in partitioned_tables(connection):
        for partition in list_partitions(connection, table):
            start, end = partition.start, partition.end
            if partition.named and start is not None and end is not None:
                gone = _drop_end(end)
                if gone <= limit:
                    starts.append(start)
                    ends.append(gone)
    tables = hypertables(connection)
    if tables:
        first = connection.execute(
            text(
                "SELECT min(range_start), min(range_end) FROM timescaledb_information.chunks "
                "WHERE hypertable_schema = current_schema() AND hypertable_name = ANY(:tables) "
                "AND range_end <= CAST(:limit AS timestamptz)"
            ),
            {"tables": tables, "limit": limit},
        ).one()
        if first[0] is not None:
            starts.append(first[0])
            ends.append(first[1])
    if not starts:
        return cutoff
    # Drop ends and chunk ends are UTC midnights: a step's local midnight is not before.
    step = max(
        _day_at_or_after(min(starts) + timedelta(days=max(1, days))), _day_at_or_after(min(ends))
    )
    return min(cutoff, step)


def _day_at_or_after(instant: datetime) -> date:
    """The site day whose local midnight is the first at or after ``instant``."""
    return site_day(instant - timedelta(microseconds=1)) + timedelta(days=1)


def maintain_window(
    connection: Connection,
    cutoff: date,
    *,
    today: date | None = None,
    days: int = DAYS_PER_RUN,
) -> PartitionReport:
    """One step of the database window (worker job ``db.window``, G3) in the caller's
    transaction: the daily partitions of the window and ahead (:func:`ensure_partitions`), then
    everything older than the next step towards ``cutoff`` dropped (:func:`next_drop_cutoff`,
    :func:`drop_before`). Created and dropped days share ``days`` (:data:`DAYS_PER_RUN`, the lock
    table's measure): ``report.applied`` is the step's cutoff (``None`` when the partitions
    took every day), and the caller runs further steps, each in its own transaction, until it
    reaches ``cutoff`` and ``remaining`` is 0.

    The caller computes ``cutoff`` (the earliest of ``today - db.retention_days``, the oldest
    day not sealed yet less :data:`RAW_DAY_MARGIN` and the oldest day of an active archive
    reload); a cutoff less than :data:`MIN_WINDOW_DAYS` before ``today`` is refused
    (``ValueError``), so a wrong clock or caller can never empty the database.
    """
    day = today or site_day(utc_now())
    if cutoff > day - timedelta(days=MIN_WINDOW_DAYS):
        raise ValueError(f"cutoff {cutoff} is less than {MIN_WINDOW_DAYS} days before {day}")
    report = ensure_partitions(connection, today=day, limit=days, since=cutoff)
    budget = days - report.days_created
    if budget <= 0:
        return report
    step = next_drop_cutoff(connection, cutoff, days=budget)
    dropped = drop_before(connection, step)
    report.applied = step
    report.dropped = dropped.dropped
    report.purged = dropped.purged
    report.counts_deleted = dropped.counts_deleted
    report.clock_samples_deleted = dropped.clock_samples_deleted
    report.hypertables = dropped.hypertables
    return report


@dataclass(frozen=True)
class PartitionStatus:
    tables: int
    #: Earliest last site day covered whole by any table (``None`` without native partitions):
    #: the day before the one its newest partition ends in (a daily partition ends at 03:00
    #: site time).
    covered_until: date | None
    #: Tables whose default partition holds rows (times outside every partition).
    default_rows: dict[str, bool]


def partition_status(connection: Connection) -> PartitionStatus:
    """Summary for ``arcilogger-db check``."""
    tables = partitioned_tables(connection)
    last: list[date] = []
    default_rows: dict[str, bool] = {}
    for table in tables:
        partitions = list_partitions(connection, table)
        ends = [p.end for p in partitions if p.named and p.end is not None]
        last.append(site_day(max(ends)) - timedelta(days=1) if ends else date.min)
        for p in partitions:
            if p.is_default:
                default_rows[table] = bool(
                    connection.execute(
                        text(f"SELECT EXISTS (SELECT 1 FROM {_quoted(p.name)})")  # noqa: S608
                    ).scalar_one()
                )
    return PartitionStatus(
        tables=len(tables), covered_until=min(last) if last else None, default_rows=default_rows
    )
