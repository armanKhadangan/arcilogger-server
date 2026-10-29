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
gave six of the eight days the nested loop on PostgreSQL 16 (about 9 s for a quiet day's file).

**Checked:** the plan each file's recount gets (``EXPLAIN`` of the cursor, with the recount's
planner settings) has no nested loop, and the recount of the oldest day's files, through the
worker job, stays under :data:`FILE_SECONDS` each with every line counted.
"""

from __future__ import annotations

import time
from datetime import date, timedelta

import pytest
from sqlalchemy import Connection, Engine, func, insert, select, text

from arcilogger_core.timeutil import day_window
from arcilogger_db.models import IngestOffset, SearchCount
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.searchcounts import file_recount_query, recount_planner, restore_planner
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_worker.search_jobs import backfill_counts

pytestmark = pytest.mark.perf

NEWEST = date(2026, 10, 27)
QUIET_DAYS = 7
#: Lines of a site day: ``(web filter, DNS filter)``, alternately from the two sources.
BUSY = (75_000, 125_000)
QUIET = (5_000, 8_750)
SOURCES = ("fortigate", "fortigate-2")
#: Bound of one quiet file's recount (seconds); about 0.1 s measured, 9 s with the nested loop.
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
    with db_engine.connect() as connection:
        timescale = connection.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
        )
    if timescale:
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


def _cursor_plan(engine: Engine, source: str, day: date) -> str:
    """The plan of the file's recount as the cursor gets it, with the recount's settings."""
    with engine.begin() as connection:
        query = file_recount_query(connection, source, day)
        sql = query.compile(dialect=connection.dialect, compile_kwargs={"literal_binds": True})
        previous = recount_planner(connection)
        explained = connection.execute(text(f"EXPLAIN DECLARE c CURSOR FOR {sql}"))
        plan = "\n".join(row[0] for row in explained)
        restore_planner(connection, previous)
        assert connection.scalar(text("SHOW enable_nestloop")) == previous["enable_nestloop"]
    return plan


def test_the_recount_of_a_quiet_day_is_never_a_nested_loop(skewed_site: Engine) -> None:
    for day in _days():
        for source in SOURCES:
            plan = _cursor_plan(skewed_site, source, day)
            print(f"\n{day} {source}: {plan.splitlines()[0]}")
            assert "Nested Loop" not in plan, (day, source, plan)
            assert "Hash" in plan or "Merge" in plan, (day, source, plan)


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
