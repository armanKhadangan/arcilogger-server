"""Performance guard of the ``search_counts`` recount (``search.counts_backfill``,
``arcilogger-db search-counts rebuild``; #55, from the Wave 4 W4-A perf work).

The recount (:func:`arcilogger_db.searchcounts.rebuild_stream`) joins a file's ``messages`` rows to
its typed rows and reads every row through a server-side cursor. On a site whose partition holds
one busy day beside quiet ones, ``ANALYZE`` underestimated the quiet days' rows, and the cursor's
plan for its first rows (``cursor_tuple_fraction`` 0.1) became a nested loop that compared every
message of the file with every typed row of it: 36 s for a 27,500-line file of the Uyum Durumu perf
data (``api/tests/test_compliance_perf.py``), 0.3 s with a hash join. The recount now plans for all
its rows and without a nested loop (:data:`arcilogger_db.searchcounts.RECOUNT_PLANNER`).

**Data**, written with ``INSERT … SELECT generate_series`` and analysed: one busy site day (75,000
web filter and 125,000 DNS filter lines) and seven quiet days before it (5,000 and 8,750 each), from
two FortiGate sources, with their typed rows; a quarter of the Uyum Durumu perf data, which still
gave six of the eight days the nested loop on PostgreSQL 16 (about 3 s for a quiet day's file).

**Checked:** the plan each file's recount gets has no nested loop: the settings in force and the
``EXPLAIN`` of the cursor are caught from the recount itself as it declares its cursor (and, on
plain PostgreSQL, the same statement without them still plans some files as a nested loop, so the
data keeps showing the problem), and the recount of the oldest day's files, through the
worker job, stays under :data:`FILE_SECONDS` each with every line counted.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, event, func, insert, select, text

from arcilogger_core.timeutil import day_window
from arcilogger_db.models import IngestOffset, SearchCount
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.searchcounts import RECOUNT_PLANNER, file_recount_query, rebuild_stream
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_worker.search_jobs import backfill_counts

pytestmark = pytest.mark.perf

NEWEST = date(2026, 10, 27)
QUIET_DAYS = 7
#: Lines of a site day: ``(web filter, DNS filter)``, alternately from the two sources.
BUSY = (75_000, 125_000)
QUIET = (5_000, 8_750)
SOURCES = ("fortigate", "fortigate-2")
#: Bound of one quiet file's recount (seconds); about 0.1 s measured, 3 s with the nested loop.
FILE_SECONDS = 1.0

_COLUMNS = "time, time_received, source, sender, raw_day, raw_line, raw_offset"
_SERIES = (
    "WITH g AS (SELECT n, CAST(:start AS timestamptz) + n * CAST(:step AS interval) AS t, "
    "CASE WHEN n % 2 = 0 THEN 'fortigate' ELSE 'fortigate-2' END AS src, "
    "CAST(CASE WHEN n % 2 = 0 THEN '172.31.255.1' ELSE '172.31.255.2' END AS inet) AS snd, "
    "CAST(:first AS bigint) + n AS line "
    "FROM generate_series(CAST(1 AS bigint), CAST(:rows AS bigint)) AS n) "
)
_COMMON = "t, t, src, snd, :day, line, line * 600"
_CLIENT = "CAST('172.20.' || (ARRAY[41,42,43,60])[n % 4 + 1] || '.' || (10 + n % 150) AS inet)"
MESSAGES_SQL = text(
    _SERIES  # noqa: S608 - fixed SQL fragments
    + f"INSERT INTO messages ({_COLUMNS}, severity, facility, host, type, subtype, text) "
    + f"SELECT {_COMMON}, 6, 23, 'FIREWALL-01', 'utm', :subtype, "
    + "'type=\"utm\" subtype=\"' || :subtype || '\" n=' || n FROM g"
)
TYPED_SQL = {
    "webfilter": text(
        _SERIES  # noqa: S608 - fixed SQL fragments
        + f"INSERT INTO fg_web ({_COLUMNS}, srcip, srcport, dstip, dstport, hostname, url, "
        + "catdesc, action, profile, sessionid) "
        + f"SELECT {_COMMON}, {_CLIENT}, 40000 + n % 20000, "
        + "CAST('203.0.113.' || (n % 200 + 10) AS inet), 443, "
        + "'host-' || (n % 997) || '.example.com', '/sayfa/' || (n % 101), 'Sentetik: Haber', "
        + "CASE WHEN n % 20 = 0 THEN 'blocked' ELSE 'passthrough' END, 'default', n FROM g"
    ),
    "dns": text(
        _SERIES  # noqa: S608 - fixed SQL fragments
        + f"INSERT INTO fg_dns ({_COLUMNS}, srcip, srcport, dstip, dstport, qname, qtype, "
        + "action) "
        + f"SELECT {_COMMON}, {_CLIENT}, 40000 + n % 20000, CAST('172.20.20.53' AS inet), 53, "
        + "'host-' || (n % 997) || '.example.com', 'A', 'pass' FROM g"
    ),
}


def _days() -> list[date]:
    return [NEWEST - timedelta(days=offset) for offset in range(QUIET_DAYS, -1, -1)]


def _load_day(connection: Connection, day: date, lines: tuple[int, int]) -> None:
    start, end = day_window(day)
    first = 0
    for subtype, count in zip(("webfilter", "dns"), lines, strict=True):
        step = f"{(end - start).total_seconds() * 1_000_000 / (count + 1):.3f} microseconds"
        params = {"start": start, "step": step, "rows": count, "day": day, "first": first}
        connection.execute(MESSAGES_SQL, {**params, "subtype": subtype})
        connection.execute(TYPED_SQL[subtype], params)
        first += count


@pytest.fixture
def skewed_site(db_engine: Engine) -> Engine:
    """One busy day beside seven quiet ones, analysed, every file marked for the recount."""
    if _timescale(db_engine):
        pause_compression_jobs(db_engine)  # no chunk compressed while the rows are written
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=NEWEST, months_back=1)
    for day in _days():
        with db_engine.begin() as connection:
            _load_day(connection, day, BUSY if day == NEWEST else QUIET)
            connection.execute(
                insert(IngestOffset).values(
                    [
                        {"source": s, "day": day, "counts_from_line": 0, "finished_at": func.now()}
                        for s in SOURCES
                    ]
                )
            )
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("ANALYZE"))
    return db_engine


@dataclass
class _Declared:
    """The recount's streamed statement as :func:`rebuild_stream` declared its cursor: the
    planner settings then in force and the plan the cursor got."""

    settings: dict[str, str] = field(default_factory=dict)
    plan: str = ""


def _recount_as_run(engine: Engine, source: str, day: date) -> _Declared:
    """Run the file's recount (rolled back) and catch its cursor's settings and plan from the
    same connection, just before the cursor is declared."""
    declared = _Declared()

    def before(
        conn: Any, cursor: Any, statement: str, parameters: Any, context: Any, many: Any
    ) -> None:
        if not context.execution_options.get("stream_results"):
            return
        probe = cursor.connection.cursor()  # the same session, so the same settings
        for name, _ in RECOUNT_PLANNER:
            probe.execute(f"SHOW {name}")
            declared.settings[name] = probe.fetchone()[0]
        probe.execute(f"EXPLAIN DECLARE c CURSOR FOR {statement}", parameters)
        declared.plan = "\n".join(row[0] for row in probe.fetchall())
        probe.close()

    with engine.connect() as connection:
        event.listen(connection, "before_cursor_execute", before)
        with connection.begin() as transaction:
            rebuild_stream(connection, source, day)
            transaction.rollback()
    return declared


def _plan_without_settings(engine: Engine, source: str, day: date) -> str:
    with engine.begin() as connection:
        query = file_recount_query(connection, source, day)
        sql = query.compile(dialect=connection.dialect, compile_kwargs={"literal_binds": True})
        explained = connection.execute(text(f"EXPLAIN DECLARE c CURSOR FOR {sql}"))
        return "\n".join(row[0] for row in explained)


def _timescale(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
            )
        )


def test_the_recount_of_a_quiet_day_is_never_a_nested_loop(skewed_site: Engine) -> None:
    unguarded = []
    for day in _days():
        for source in SOURCES:
            declared = _recount_as_run(skewed_site, source, day)
            print(f"\n{day} {source}: {declared.plan.splitlines()[0]}")
            assert declared.settings == dict(RECOUNT_PLANNER), (day, source)
            assert "Nested Loop" not in declared.plan, (day, source, declared.plan)
            assert "Hash" in declared.plan or "Merge" in declared.plan, (day, source)
            if "Nested Loop" in _plan_without_settings(skewed_site, source, day):
                unguarded.append(day)
    print(f"\nnested loop without the recount's settings: {len(unguarded)} of 16 files")
    if not _timescale(skewed_site):
        # The data still shows the problem on plain PostgreSQL (1-day chunks with statistics of
        # their own may plan every file well even without the settings).
        assert unguarded


def test_the_backfill_recounts_the_oldest_day_fast(skewed_site: Engine) -> None:
    oldest = _days()[0]
    with skewed_site.begin() as connection:
        connection.execute(
            text("UPDATE ingest_offsets SET counts_from_line = NULL WHERE day <> :day"),
            {"day": oldest},
        )
    began = time.perf_counter()
    report = backfill_counts(skewed_site)
    took = time.perf_counter() - began
    print(f"\nbackfill of {oldest}: {took:.2f} s for {len(report.files)} files")
    assert sorted(item["source"] for item in report.files) == list(SOURCES)
    assert [item["lines"] for item in report.files] == [sum(QUIET) // 2] * len(SOURCES)
    assert took < FILE_SECONDS * len(SOURCES), took
    with skewed_site.connect() as connection:
        counted = connection.scalar(
            select(func.sum(SearchCount.n)).where(SearchCount.raw_day == oldest)
        )
    assert counted == sum(QUIET)
