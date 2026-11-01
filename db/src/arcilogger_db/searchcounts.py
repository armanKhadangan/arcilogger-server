"""The hourly summary table ``search_counts``: one key, one exact line count (ADR-0015, 2.7.1).

Every ``messages`` row is counted once under the key :class:`CountKey` — its hour (device time,
UTC), its spool file (``source``, ``raw_day``), its kind (:func:`arcilogger_db.kinds.kind_of`), its
typed table (``tbl``, ``''`` for none), the typed row's ``action`` (lower-cased) and the /24 of the
typed row's client address (:data:`arcilogger_db.kinds.CLIENT_IP`) when that is a private IPv4
address. The statistics of a broad search (totals, histogram, the five facets) are sums over these
keys instead of counts over millions of rows.

* :func:`count_key` is the one definition of a key, pure, shared by the ingest loader (which adds
  a batch's counts in the transaction that loads its rows: exactly once, like the rows) and by
  :func:`rebuild_stream` (which recounts one file from its rows). A line whose typed row was not
  loaded (dropped by the row checks, salvaged without it) counts with ``tbl = ''``, exactly as the
  rows are: the search that drives over typed tables does not see it either.
* :func:`upsert_counts` adds a :class:`collections.Counter` of keys (``INSERT … ON CONFLICT … DO
  UPDATE SET n = n + EXCLUDED.n``, keys in sorted order so two writers never deadlock).
* :func:`rebuild_stream` deletes a file's keys and counts its rows again; the worker job
  ``search.counts_backfill`` uses it for files loaded before revision ``0016_search``
  (``ingest_offsets.counts_from_line``), ``arcilogger-db search-counts rebuild`` for one file.
* :func:`counts_complete` says whether every file that can hold rows of a time range is counted;
  until then the statistics count directly.
* :func:`verify` recounts recent hours from the rows and compares (``arcilogger-db search-counts
  verify``).

A file's rows lie within :data:`COUNTS_TIME_MARGIN` of its site day: a device time further than
``DEFAULT_MAX_CLOCK_SKEW`` (1 day) from the receive time is not used (``ingest.parsers.syslog``),
and the receive day is the file's day. That bounds every scan here to three days of partitions.
"""

from __future__ import annotations

import ipaddress
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from functools import lru_cache
from typing import Any, Final, NamedTuple

from sqlalchemy import (
    Connection,
    FromClause,
    RowMapping,
    Select,
    and_,
    delete,
    exists,
    func,
    literal,
    null,
    select,
    text,
    union_all,
)

from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.kinds import (
    ACTION_TABLES,
    CLIENT_IP,
    TYPED_TABLES,
    ascii_lower,
    kind_of,
    tables_of,
)
from arcilogger_db.models import Base, IngestOffset, Message, SearchCount

#: How far a file's rows may lie outside its site day (device clock skew plus one hour).
COUNTS_TIME_MARGIN: Final = timedelta(days=1, hours=1)
#: Keys per ``INSERT`` statement of :func:`upsert_counts`.
UPSERT_CHUNK: Final = 500
#: :func:`upsert_counts`' one statement: the keys as parallel arrays, so its text is the same for
#: every batch (a multi-row ``INSERT`` built and compiled per batch cost about 5 ms of the
#: ingest's service process; INGEST.md).
UPSERT: Final = text(
    "INSERT INTO search_counts (hour, source, raw_day, kind, tbl, action, net, n) "
    "SELECT * FROM unnest(CAST(:hours AS timestamptz[]), CAST(:sources AS text[]), "
    "CAST(:days AS date[]), CAST(:kinds AS text[]), CAST(:tbls AS text[]), "
    "CAST(:actions AS text[]), CAST(:nets AS cidr[]), CAST(:ns AS bigint[])) "
    "ON CONFLICT ON CONSTRAINT uq_search_counts_key "
    "DO UPDATE SET n = search_counts.n + EXCLUDED.n"
)
#: Rows fetched at a time while recounting.
FETCH_ROWS: Final = 20_000
#: Planner settings of the recount statement, for the rest of the caller's transaction until it
#: has run. It reads every row, so the plan for its first rows that a server-side cursor asks for
#: (``cursor_tuple_fraction`` 0.1) is never wanted, nor a nested loop joining a file's messages to
#: its typed rows: with a few large days in a partition's statistics the small days' rows were
#: underestimated, and the cursor's nested loop compared every message with every typed row
#: (36 s for 27,500 lines instead of 0.3 s; ``worker/tests/test_search_counts_performance.py``).
RECOUNT_PLANNER: Final = (("cursor_tuple_fraction", "1"), ("enable_nestloop", "off"))
#: Private IPv4 ranges whose /24 is counted (RFC 1918 and the shared address space RFC 6598).
PRIVATE_NETWORKS: Final = tuple(
    ipaddress.IPv4Network(network)
    for network in ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "100.64.0.0/10")
)


class CountKey(NamedTuple):
    """One row of ``search_counts`` without its count."""

    hour: datetime
    source: str
    raw_day: date
    kind: str
    tbl: str
    action: str | None
    net: str | None

    def sort_key(self) -> tuple[Any, ...]:
        """A total order (``None`` first), the order keys are written in."""
        return (
            self.hour,
            self.source,
            self.raw_day,
            self.kind,
            self.tbl,
            self.action is not None,
            self.action or "",
            self.net is not None,
            self.net or "",
        )


def hour_of(time: datetime) -> datetime:
    """``date_trunc('hour', time)`` in UTC."""
    return time.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


@lru_cache(maxsize=4096)
def _net24(address: str) -> str | None:
    try:
        ip = ipaddress.ip_interface(address).ip
    except ValueError:
        return None
    if not isinstance(ip, ipaddress.IPv4Address) or not any(ip in n for n in PRIVATE_NETWORKS):
        return None
    return str(ipaddress.IPv4Network(f"{ip}/24", strict=False))


def net_of(address: Any) -> str | None:
    """The /24 of a private IPv4 address (text, :mod:`ipaddress` object or ``None``)."""
    return None if address is None else _net24(str(address))


def count_key(
    message: Mapping[str, Any] | RowMapping, table: str | None, typed: Mapping[str, Any] | None
) -> CountKey:
    """The key of one loaded line: its ``messages`` row (``time``, ``source``, ``raw_day``,
    ``type``, ``subtype``) and its loaded typed row (``table``, ``typed``), or ``None``."""
    action = net = None
    if table is not None and typed is not None:
        if table in ACTION_TABLES:
            value = typed.get("action")
            action = None if value is None else ascii_lower(str(value))
        client = CLIENT_IP.get(table)
        if client is not None:
            net = net_of(typed.get(client))
    else:
        table = None
    return CountKey(
        hour=hour_of(message["time"]),
        source=message["source"],
        raw_day=message["raw_day"],
        kind=kind_of(message["type"], message.get("subtype")),
        tbl=table or "",
        action=action,
        net=net,
    )


def upsert_counts(connection: Connection, counts: Mapping[CountKey, int]) -> int:
    """Add ``counts`` to ``search_counts`` (inside the caller's transaction); keys written."""
    keys = sorted((key for key, n in counts.items() if n > 0), key=CountKey.sort_key)
    for start in range(0, len(keys), UPSERT_CHUNK):
        chunk = keys[start : start + UPSERT_CHUNK]
        connection.execute(
            UPSERT,
            {
                "hours": [key.hour for key in chunk],
                "sources": [key.source for key in chunk],
                "days": [key.raw_day for key in chunk],
                "kinds": [key.kind for key in chunk],
                "tbls": [key.tbl for key in chunk],
                "actions": [key.action for key in chunk],
                "nets": [key.net for key in chunk],
                "ns": [counts[key] for key in chunk],
            },
        )
    return len(keys)


# -- recounting from the rows ---------------------------------------------------------------------


def _typed_union(tables: Iterable[str], where: Any) -> FromClause | None:
    """``(tbl, time, source, raw_day, raw_line, action, client)`` of the typed rows of
    ``tables`` that satisfy ``where(table)``."""
    parts: list[Select[Any]] = []
    for name in tables:
        table = Base.metadata.tables[name]
        client = CLIENT_IP.get(name)
        parts.append(
            select(
                literal(name).label("tbl"),
                table.c.time,
                table.c.source,
                table.c.raw_day,
                table.c.raw_line,
                (table.c.action if name in ACTION_TABLES else null()).label("action"),
                (table.c[client] if client is not None else null()).label("client"),
            ).where(where(table))
        )
    if not parts:
        return None
    return (union_all(*parts) if len(parts) > 1 else parts[0]).subquery("typed")


def recount_query(messages_where: Any, typed: FromClause | None) -> Select[Any]:
    """The rows :func:`_recount` counts: the ``messages`` rows of ``messages_where``, each with
    its typed row of ``typed`` (:func:`_typed_union`) when it has one."""
    m = Message.__table__
    columns = [m.c.time, m.c.source, m.c.raw_day, m.c.type, m.c.subtype]
    if typed is None:
        return select(*columns).where(messages_where)
    return (
        select(*columns, typed.c.tbl, typed.c.action, typed.c.client)
        .select_from(
            m.outerjoin(
                typed,
                and_(
                    typed.c.time == m.c.time,
                    typed.c.source == m.c.source,
                    typed.c.raw_day == m.c.raw_day,
                    typed.c.raw_line == m.c.raw_line,
                ),
            )
        )
        .where(messages_where)
    )


def recount_planner(connection: Connection) -> dict[str, str]:
    """Set :data:`RECOUNT_PLANNER` for the caller's transaction (``set_config`` with
    ``is_local``, as ``SET LOCAL``); the values before, for :func:`restore_planner`."""
    previous = {}
    for name, value in RECOUNT_PLANNER:
        previous[name] = str(connection.scalar(select(func.current_setting(name))))
        connection.execute(select(func.set_config(name, value, True)))
    return previous


def restore_planner(connection: Connection, previous: Mapping[str, str]) -> None:
    for name, value in previous.items():
        connection.execute(select(func.set_config(name, value, True)))


def _recount(connection: Connection, query: Select[Any]) -> Counter[CountKey]:
    """The keys of :func:`recount_query`'s rows, read with :data:`RECOUNT_PLANNER`."""
    counts: Counter[CountKey] = Counter()
    previous = recount_planner(connection)
    # Statement options: Connection.execution_options would stream every later statement too.
    result = connection.execute(query.execution_options(stream_results=True, yield_per=FETCH_ROWS))
    for row in result.mappings():
        table = row.get("tbl")
        values = None
        if table is not None:
            client = CLIENT_IP.get(table)
            values = {"action": row["action"], **({client: row["client"]} if client else {})}
        counts[count_key(row, table, values)] += 1
    # A failed recount rolls the caller's transaction back, and the settings with it.
    restore_planner(connection, previous)
    return counts


def stream_window(day: date) -> tuple[datetime, datetime]:
    """``[start, end)`` of device times the rows of a site day's file can have."""
    start, end = day_window(day)
    return start - COUNTS_TIME_MARGIN, end + COUNTS_TIME_MARGIN


@dataclass(frozen=True)
class RebuildResult:
    source: str
    day: date
    #: ``messages`` rows counted.
    lines: int
    #: Keys written.
    keys: int


def file_recount_query(connection: Connection, source: str, day: date) -> Select[Any]:
    """:func:`recount_query` of the file ``spool/<source>/<day>.log``: its rows, joined to the
    typed tables of the kinds it holds."""
    lo, hi = stream_window(day)
    m = Message.__table__
    in_file = and_(m.c.source == source, m.c.raw_day == day, m.c.time >= lo, m.c.time < hi)
    kinds = {
        kind_of(type_, subtype)
        for type_, subtype in connection.execute(
            select(m.c.type, m.c.subtype).where(in_file).distinct()
        )
    }

    def typed_in_file(table: Any) -> Any:
        return and_(
            table.c.source == source, table.c.raw_day == day, table.c.time >= lo, table.c.time < hi
        )

    return recount_query(in_file, _typed_union(tables_of(kinds), typed_in_file))


def rebuild_stream(connection: Connection, source: str, day: date) -> RebuildResult:
    """Count the file ``spool/<source>/<day>.log`` again from its rows (the caller's
    transaction; the caller clears ``ingest_offsets.counts_from_line`` in it)."""
    counts = _recount(connection, file_recount_query(connection, source, day))
    connection.execute(
        delete(SearchCount).where(SearchCount.raw_day == day, SearchCount.source == source)
    )
    keys = upsert_counts(connection, counts)
    return RebuildResult(source, day, sum(counts.values()), keys)


def counts_complete(connection: Connection, start: datetime, end: datetime) -> bool:
    """Whether every file that can hold rows of ``[start, end)`` is in ``search_counts``: no
    ``ingest_offsets`` row with ``counts_from_line`` set and a day within two days of it."""
    first = site_day(start) - timedelta(days=2)
    last = site_day(end) + timedelta(days=2)
    pending = exists().where(
        IngestOffset.counts_from_line.is_not(None),
        IngestOffset.day >= first,
        IngestOffset.day <= last,
    )
    return not connection.scalar(select(pending))


@dataclass
class VerifyReport:
    """What :func:`verify` found for ``[start, end)``."""

    start: datetime
    end: datetime
    #: Keys compared (files still waiting for their recount are left out).
    keys: int = 0
    lines: int = 0
    #: ``(key, counted from the rows, stored)`` for every key that differs.
    differences: list[tuple[CountKey, int, int]] = field(default_factory=list)
    #: Files left out: ``counts_from_line`` is still set.
    pending: list[tuple[str, date]] = field(default_factory=list)
    #: Rows in the range whose name is not stored in lower case (``fg_dns.qname``,
    #: ``fg_web.hostname``): ``domain:`` and ``qname:`` assume lower case (ADR-0015, D6).
    mixed_case: dict[str, int] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.differences


def verify(connection: Connection, hours: int, *, now: datetime | None = None) -> VerifyReport:
    """Recount the last ``hours`` full hours and the current one from the rows and compare with
    ``search_counts``.

    Rows and counts are written in one transaction, so they agree at every snapshot: run this in
    a ``REPEATABLE READ`` transaction (both reads then see one snapshot while ingest goes on).
    """
    if hours < 0:
        raise ValueError("hours must not be negative")
    end = hour_of(now or utc_now()) + timedelta(hours=1)
    start = end - timedelta(hours=hours + 1)
    report = VerifyReport(start, end)
    report.pending = [
        (row.source, row.day)
        for row in connection.execute(
            select(IngestOffset.source, IngestOffset.day).where(
                IngestOffset.counts_from_line.is_not(None),
                IngestOffset.day >= site_day(start) - timedelta(days=2),
                IngestOffset.day <= site_day(end) + timedelta(days=2),
            )
        )
    ]
    skipped = set(report.pending)
    m = Message.__table__

    def in_range(table: Any) -> Any:
        return and_(table.c.time >= start, table.c.time < end)

    counted = _recount(connection, recount_query(in_range(m), _typed_union(TYPED_TABLES, in_range)))
    stored: Counter[CountKey] = Counter()
    c = SearchCount
    for hour, source, raw_day, kind, tbl, action, net, n in connection.execute(
        select(c.hour, c.source, c.raw_day, c.kind, c.tbl, c.action, c.net, c.n).where(
            c.hour >= start, c.hour < end
        )
    ):
        net_text = None if net is None else str(net)  # psycopg returns an ipaddress network
        stored[CountKey(hour, source, raw_day, kind, tbl, action, net_text)] += n
    for key in sorted(set(counted) | set(stored), key=CountKey.sort_key):
        if (key.source, key.raw_day) in skipped:
            continue
        report.keys += 1
        report.lines += counted[key]
        if counted[key] != stored[key]:
            report.differences.append((key, counted[key], stored[key]))
    for name, column in (("fg_dns", "qname"), ("fg_web", "hostname")):
        table = Base.metadata.tables[name]
        value = table.c[column]
        report.mixed_case[f"{name}.{column}"] = int(
            connection.scalar(
                select(func.count()).where(in_range(table), value != func.lower(value))
            )
            or 0
        )
    return report
