"""Native partitions of the time-series tables (plain PostgreSQL without TimescaleDB): daily
partitions of the database window, the change-over from the monthly partitions of earlier
versions, dropping by cutoff (Wave 5 contract G2, G3), and ``drop_chunks`` on TimescaleDB."""

from __future__ import annotations

import itertools
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, func, insert, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_core.timeutil import day_window, site_day
from arcilogger_db import partitions as partitions_module
from arcilogger_db.clocksamples import ClockKey, minute_of, upsert_samples
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import upgrade
from arcilogger_db.models import TIME_SERIES_TABLES, Message, MtDhcp, SearchCount
from arcilogger_db.partitions import (
    DAYS_AHEAD,
    DAYS_PER_RUN,
    DROPPED_KEY,
    MIN_WINDOW_DAYS,
    MONTHS_BACK,
    PARTITION_LOCK_KEY,
    RAW_DAY_MARGIN,
    LockBudget,
    Partition,
    PartitionLockTimeoutError,
    PartitionReport,
    add_months,
    day_bounds,
    day_partition_name,
    default_partition_name,
    drop_before,
    dropped_before,
    ensure_days,
    ensure_partitions,
    hypertables,
    include_name,
    is_partition_table,
    list_partitions,
    lock_partitions,
    maintain_window,
    month_bounds,
    months_back_for,
    next_drop_cutoff,
    partition_name,
    partition_status,
    partitioned_tables,
    retention_days,
    utc_days,
)
from arcilogger_db.searchcounts import CountKey, count_key, upsert_counts
from arcilogger_db.settings import DEFAULT_SETTINGS, set_setting
from arcilogger_db.testing import TestServer, pause_compression_jobs

TODAY = date(2026, 10, 12)
#: The moment plain_engine's upgrade makes its partitions for (12:00 site time on TODAY). Tests
#: compute what to expect from the same moment: with the real clock a site midnight between the
#: fixture's upgrade and the test body would move the window by a day.
MOMENT = datetime(2026, 10, 12, 9, tzinfo=UTC)
WINDOW = int(DEFAULT_SETTINGS["db.retention_days"])
#: The daily partitions are UTC days: the window's site days take one more, the UTC day of its
#: first local midnight (21:00 UTC the day before).
WINDOW_DAYS = WINDOW + 1


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=n) for n in range((last - first).days + 1)]


@contextmanager
def _engine(pg_server: TestServer) -> Iterator[Engine]:
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(name)


@pytest.fixture
def plain_engine(pg_server: TestServer, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A database migrated without TimescaleDB (works on a TimescaleDB server too), its
    partitions made around :data:`MOMENT` instead of the real clock: the first run of the daily
    window (a new installation)."""
    monkeypatch.setattr("arcilogger_db.partitions.utc_now", lambda: MOMENT)
    with _engine(pg_server) as engine:
        upgrade(engine, timescale="off")
        yield engine


@pytest.fixture
def monthly_engine(pg_server: TestServer, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """A database as an earlier version left it, after revision 0021_db_window dropped the
    empty months ahead: monthly partitions July to October 2026 (around :data:`MOMENT`)."""
    monkeypatch.setattr("arcilogger_db.partitions.utc_now", lambda: MOMENT)
    with _engine(pg_server) as engine:
        upgrade(engine, timescale="off", months_back=3, months_ahead=0)
        yield engine


def _complete(engine: Engine, today: date) -> list[str]:
    """Runs of :func:`ensure_partitions` until the window is complete (what the worker's next
    runs do); the partitions they created."""
    created: list[str] = []
    for _ in range(10):
        with engine.begin() as connection:
            report = ensure_partitions(connection, today=today)
        created += report.created
        if report.remaining == 0:
            return created
    raise AssertionError("the window is still not complete after 10 runs")


def _window(engine: Engine, cutoff: date, today: date) -> tuple[PartitionReport, int]:
    """The steps of :func:`maintain_window` until it reaches ``cutoff`` with the window complete,
    each in its own transaction (as the job ``db.window`` runs them); what they did together and
    how many steps."""
    total = PartitionReport()
    for steps in range(1, 65):
        with engine.begin() as connection:
            step = maintain_window(connection, cutoff, today=today)
        total.created += step.created
        total.dropped += step.dropped
        total.purged += step.purged
        total.counts_deleted += step.counts_deleted
        total.clock_samples_deleted += step.clock_samples_deleted
        total.applied = step.applied or total.applied
        if step.remaining == 0 and step.applied == cutoff:
            return total, steps
    raise AssertionError("the window is still not reached after 64 steps")


def _spans(engine: Engine, table: str) -> list[tuple[datetime, datetime]]:
    with engine.connect() as connection:
        return [
            (p.start, p.end)
            for p in list_partitions(connection, table)
            if p.start is not None and p.end is not None
        ]


def _daily(engine: Engine, table: str) -> list[date]:
    with engine.connect() as connection:
        return [p.day for p in list_partitions(connection, table) if p.day is not None]


def _dhcp(when: datetime, ip: str = "172.20.60.183") -> MtDhcp:
    return MtDhcp(
        time=when,
        time_received=when,
        source="core-r1",
        sender="172.20.10.1",
        raw_day=site_day(when),
        raw_line=1,
        raw_offset=0,
        server="dhcp60",
        vlan=60,
        action="assigned",
        ip=ip,
        mac="00:00:5e:00:53:46",
    )


def _where(engine: Engine) -> list[tuple[int, str]]:
    with engine.connect() as connection:
        return [
            (int(row[0]), str(row[1]))
            for row in connection.execute(
                text("SELECT id, tableoid::regclass::text FROM mt_dhcp ORDER BY id")
            )
        ]


def _local(day: date, hour: int = 12) -> datetime:
    """``hour`` o'clock site time on ``day`` (UTC)."""
    return day_window(day)[0].astimezone(UTC) + timedelta(hours=hour)


# -- names and bounds -----------------------------------------------------------------------------


def test_month_and_day_arithmetic_and_bounds_golden_values() -> None:
    assert add_months(date(2026, 11, 1), 2) == date(2027, 1, 1)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    # Local midnight Europe/Istanbul (UTC+3) of the 1st, inclusive start, exclusive end.
    assert month_bounds(date(2026, 10, 12)) == (
        datetime(2026, 9, 30, 21, tzinfo=UTC),
        datetime(2026, 10, 31, 21, tzinfo=UTC),
    )
    # A daily partition is a UTC day (TimescaleDB's chunks): midnight UTC to midnight UTC.
    assert day_bounds(date(2026, 10, 12)) == (
        datetime(2026, 10, 12, tzinfo=UTC),
        datetime(2026, 10, 13, tzinfo=UTC),
    )
    # A site day's device times lie in two UTC days: 11.10. 21:00 UTC to 12.10. 20:59 UTC.
    assert utc_days(date(2026, 10, 12), date(2026, 10, 12)) == [
        date(2026, 10, 11),
        date(2026, 10, 12),
    ]
    assert utc_days(date(2026, 10, 1), date(2026, 10, 3)) == _days(
        date(2026, 9, 30), date(2026, 10, 3)
    )
    assert partition_name("fg_traffic", date(2026, 10, 1)) == "fg_traffic_p202610"
    assert day_partition_name("fg_traffic", date(2026, 10, 12)) == "fg_traffic_p20261012"
    assert default_partition_name("mt_dhcp") == "mt_dhcp_default"
    monthly = Partition("mt_dhcp", "mt_dhcp_p202610", date(2026, 10, 1), False)
    daily = Partition("mt_dhcp", "mt_dhcp_p20261012", None, False, date(2026, 10, 12))
    default = Partition("mt_dhcp", "mt_dhcp_default", None, True)
    foreign = Partition("mt_dhcp", "mt_dhcp_old", None, False)
    assert (monthly.named, daily.named, default.named, foreign.named) == (True, True, False, False)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("fg_traffic_p202610", True),
        ("fg_traffic_p20261012", True),
        ("mt_interface_default", True),
        ("fg_traffic", False),
        ("users_default", False),  # not a time-series table
        ("users_p20261012", False),
        ("fg_traffic_p2026", False),
        ("fg_traffic_p2026101", False),
    ],
)
def test_partition_names_are_recognised(name: str, expected: bool) -> None:
    assert is_partition_table(name) is expected
    assert include_name(name, "table", {}) is not expected
    assert include_name(name, "index", {}) is True


# -- daily partitions of the window ---------------------------------------------------------------


def test_plain_upgrade_makes_daily_partitions_today_and_ahead_first(plain_engine: Engine) -> None:
    """A new installation: the first run makes today, the days ahead and the newest past days
    (:data:`DAYS_PER_RUN` days, within PostgreSQL's default lock table), the next runs the rest
    of the window; no monthly partition."""
    ahead = _days(TODAY, TODAY + timedelta(days=DAYS_AHEAD))
    past = DAYS_PER_RUN - len(ahead)
    first_run = _days(TODAY - timedelta(days=past), TODAY + timedelta(days=DAYS_AHEAD))
    with plain_engine.connect() as connection:
        assert partitioned_tables(connection) == list(TIME_SERIES_TABLES)
        status = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
        for table in TIME_SERIES_TABLES:
            partitions = list_partitions(connection, table)
            assert partitions[-1].is_default
            assert partitions[-1].name == f"{table}_default"
            assert all(p.month is None for p in partitions), table
            assert [p.day for p in partitions[:-1]] == first_run, table
            assert [p.name for p in partitions[:-1]] == [
                day_partition_name(table, day) for day in first_run
            ]
        overview = partition_status(connection)
    assert status["storage"] == "partitions"
    assert status["partitioned"] == list(TIME_SERIES_TABLES)
    assert overview.tables == 16
    assert overview.covered_until == TODAY + timedelta(days=DAYS_AHEAD)
    assert overview.default_rows == dict.fromkeys(TIME_SERIES_TABLES, False)
    with plain_engine.begin() as connection:
        second = ensure_partitions(connection, today=TODAY)
    window = WINDOW_DAYS + DAYS_AHEAD + 1
    assert len(second.created) == 16 * DAYS_PER_RUN
    assert second.remaining == window - 2 * DAYS_PER_RUN
    _complete(plain_engine, TODAY)
    for table in TIME_SERIES_TABLES:
        assert _daily(plain_engine, table) == _days(
            TODAY - timedelta(days=WINDOW_DAYS), TODAY + timedelta(days=DAYS_AHEAD)
        )
        assert _spans(plain_engine, table) == [
            day_bounds(day)
            for day in _days(
                TODAY - timedelta(days=WINDOW_DAYS), TODAY + timedelta(days=DAYS_AHEAD)
            )
        ]
    with plain_engine.begin() as connection:
        again = ensure_partitions(connection, today=TODAY)
    assert (again.created, again.moved, again.remaining) == ([], {}, 0)


def test_rows_are_routed_by_utc_day_and_outliers_go_to_the_default(plain_engine: Engine) -> None:
    start, end = day_bounds(TODAY)
    with Session(plain_engine) as session:
        session.add_all(
            [
                _dhcp(start),  # first instant of the UTC day (03:00 site time)
                _dhcp(end - timedelta(microseconds=1)),  # its last instant
                _dhcp(end),  # the next UTC day
                _dhcp(day_window(TODAY)[0]),  # the site day's midnight: the UTC day before
                _dhcp(datetime(2001, 1, 1, tzinfo=UTC)),
            ]
        )
        session.commit()
    assert _where(plain_engine) == [
        (1, day_partition_name("mt_dhcp", TODAY)),
        (2, day_partition_name("mt_dhcp", TODAY)),
        (3, day_partition_name("mt_dhcp", TODAY + timedelta(days=1))),
        (4, day_partition_name("mt_dhcp", TODAY - timedelta(days=1))),
        (5, "mt_dhcp_default"),
    ]


def test_late_partition_takes_over_rows_from_the_default(plain_engine: Engine) -> None:
    """Rows of a day without a partition wait in the default partition and move later."""
    later = date(2027, 6, 15)  # beyond the created horizon
    when = _local(later, 12)
    with Session(plain_engine) as session:
        session.add_all([_dhcp(when, "172.20.60.37"), _dhcp(when, "172.20.60.81")])
        session.add(_dhcp(datetime(2001, 1, 1, tzinfo=UTC)))
        session.commit()
    assert {where for _, where in _where(plain_engine)} == {"mt_dhcp_default"}
    with plain_engine.begin() as connection:
        report = ensure_partitions(connection, today=later)
    june = day_partition_name("mt_dhcp", later)
    assert report.moved == {june: 2}
    assert june in report.created
    assert _where(plain_engine) == [(1, june), (2, june), (3, "mt_dhcp_default")]
    with Session(plain_engine) as session:
        session.add(_dhcp(when, "172.20.60.82"))  # identity continues across partitions
        session.commit()
        rows = session.scalars(select(MtDhcp).order_by(MtDhcp.id)).all()
    assert [(r.id, r.ip) for r in rows] == [
        (1, "172.20.60.37"),
        (2, "172.20.60.81"),
        (3, "172.20.60.183"),
        (4, "172.20.60.82"),
    ]
    _complete(plain_engine, later)
    with plain_engine.begin() as connection:
        again = ensure_partitions(connection, today=later)
    assert (again.created, again.moved) == ([], {})


def test_ensure_days_makes_the_days_of_an_archive_reload(plain_engine: Engine) -> None:
    """The site days a reload brings back get the partitions of their UTC days (rows already
    waiting in the default partition move); days already covered are left alone; idempotent."""
    old = date(2026, 8, 2)
    with Session(plain_engine) as session:
        session.add(_dhcp(_local(old)))
        session.commit()
    with plain_engine.begin() as connection:
        report = ensure_days(connection, date(2026, 8, 1), date(2026, 8, 3))
        nothing = ensure_days(connection, date(2026, 8, 3), date(2026, 8, 1))  # empty range
    assert sorted(report.created) == sorted(
        day_partition_name(table, day)
        for table in TIME_SERIES_TABLES
        for day in _days(date(2026, 7, 31), date(2026, 8, 3))  # 01.08. 00:00 is 31.07. 21:00 UTC
    )
    assert report.moved == {day_partition_name("mt_dhcp", old): 1}
    assert nothing.created == []
    assert _where(plain_engine) == [(1, day_partition_name("mt_dhcp", old))]
    first = TODAY - timedelta(days=DAYS_PER_RUN - DAYS_AHEAD - 1)  # the oldest day made so far
    with plain_engine.begin() as connection:
        overlap = ensure_days(connection, first - timedelta(days=2), first + timedelta(days=1))
        again = ensure_days(connection, date(2026, 8, 1), date(2026, 8, 3))
    assert sorted({name.rsplit("_p", 1)[1] for name in overlap.created}) == [
        f"{first - timedelta(days=3):%Y%m%d}",
        f"{first - timedelta(days=2):%Y%m%d}",
        f"{first - timedelta(days=1):%Y%m%d}",
    ]
    assert again.created == []


def test_partitions_cover_the_whole_window_and_follow_a_longer_one(plain_engine: Engine) -> None:
    """Live lines whose device clock is days behind, and the vault re-load of any day the
    database keeps, find their day: nothing of the window lands in the default partition, which
    every later partition would have to scan under an exclusive lock."""
    _complete(plain_engine, TODAY)
    expected = _days(TODAY - timedelta(days=WINDOW_DAYS), TODAY + timedelta(days=DAYS_AHEAD))
    for table in TIME_SERIES_TABLES:
        assert _daily(plain_engine, table) == expected, table
    with Session(plain_engine) as session:
        set_setting(session, "db.retention_days", 60, updated_by="tolga")
        session.commit()
    created = _complete(plain_engine, TODAY)
    # The UTC day of the window's first local midnight (21:00 UTC the day before) is in.
    assert day_partition_name("mt_dhcp", TODAY - timedelta(days=61)) in created
    assert day_partition_name("mt_dhcp", TODAY - timedelta(days=62)) not in created
    assert len(created) == 16 * 30


def test_retention_days_reads_the_setting_and_never_less_than_a_week(
    plain_engine: Engine,
) -> None:
    with plain_engine.begin() as connection:
        assert retention_days(connection) == WINDOW == 30
        connection.execute(
            text("UPDATE settings SET value = CAST('3' AS jsonb) WHERE key = 'db.retention_days'")
        )
        assert retention_days(connection) == MIN_WINDOW_DAYS == 7
        connection.execute(
            text("UPDATE settings SET value = CAST('\"x\"' AS jsonb) WHERE key = :key"),
            {"key": "db.retention_days"},
        )
        assert retention_days(connection) == WINDOW


# -- the change-over from monthly partitions ------------------------------------------------------


def test_monthly_partitions_are_not_split_and_daily_ones_start_next_month(
    monthly_engine: Engine,
) -> None:
    """G2: the monthly partitions of an earlier version stay whole; the daily partitions start
    where the last one ends (01.11. 00:00 site time, 31.10. 21:00 UTC): the first one, named for
    that UTC day, covers the three hours to its UTC midnight, then whole UTC days (the
    change-over rule); a monthly partition goes once the UTC day it ends in has ended by the
    cutoff (the drop rule), so up to one extra month stays during the change-over."""
    with monthly_engine.connect() as connection:
        months = [p.month for p in list_partitions(connection, "mt_dhcp") if p.month]
    assert months == [date(2026, 7, 1), date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1)]
    with monthly_engine.begin() as connection:
        report = ensure_partitions(connection, today=date(2026, 10, 25))
    # Every instant up to 31.10. 21:00 UTC is in a monthly partition: only the rest of that UTC
    # day and 01.11. (UTC, the window's last site day ends in it) are new.
    assert report.created == [
        day_partition_name(t, day)
        for t in TIME_SERIES_TABLES
        for day in (date(2026, 10, 31), date(2026, 11, 1))
    ]
    assert report.remaining == 0
    november = date(2026, 11, 20)
    _complete(monthly_engine, november)
    october_end = month_bounds(date(2026, 10, 1))[1]
    assert october_end == datetime(2026, 10, 31, 21, tzinfo=UTC)
    assert _spans(monthly_engine, "mt_dhcp") == [
        month_bounds(date(2026, 7, 1)),
        month_bounds(date(2026, 8, 1)),
        month_bounds(date(2026, 9, 1)),
        month_bounds(date(2026, 10, 1)),
        (october_end, datetime(2026, 11, 1, tzinfo=UTC)),  # mt_dhcp_p20261031: three hours
        *[day_bounds(day) for day in _days(date(2026, 11, 1), date(2026, 11, 27))],
    ]
    with Session(monthly_engine) as session:
        session.add_all(
            [
                _dhcp(_local(date(2026, 9, 10)), "172.20.60.10"),
                _dhcp(_local(date(2026, 10, 15)), "172.20.60.11"),
                _dhcp(_local(date(2026, 11, 3)), "172.20.60.12"),
                _dhcp(_local(date(2026, 11, 10)), "172.20.60.13"),
            ]
        )
        session.commit()
    first, _ = _window(monthly_engine, date(2026, 10, 21), november)
    assert {"mt_dhcp_p202607", "mt_dhcp_p202608", "mt_dhcp_p202609"} <= set(first.dropped)
    assert "mt_dhcp_p202610" not in first.dropped  # holds the cutoff: kept whole
    assert [ip for _, ip in _ips(monthly_engine)] == [
        "172.20.60.11",
        "172.20.60.12",
        "172.20.60.13",
    ]
    december = date(2026, 12, 5)
    second, _ = _window(monthly_engine, date(2026, 11, 5), december)
    assert "mt_dhcp_p202610" in second.dropped
    # Everything that ends by 05.11. 00:00 site time (04.11. 21:00 UTC) goes: the three hours,
    # the UTC days to 03.11.; the UTC day 04.11. holds that midnight and stays whole.
    assert {
        day_partition_name("mt_dhcp", d) for d in _days(date(2026, 10, 31), date(2026, 11, 3))
    } <= set(second.dropped)
    assert day_partition_name("mt_dhcp", date(2026, 11, 4)) not in second.dropped
    assert day_partition_name("mt_dhcp", december) in second.created
    assert [ip for _, ip in _ips(monthly_engine)] == ["172.20.60.13"]
    assert _spans(monthly_engine, "mt_dhcp")[0] == day_bounds(date(2026, 11, 4))


def test_no_device_time_of_the_change_over_falls_into_the_default(monthly_engine: Engine) -> None:
    """The change-over rule on both sides of monthly partitions: after the last month the daily
    partition of its UTC day starts at the month's end; before a monthly partition kept ahead
    (it held rows when revision 0021 ran) the one of its UTC day ends at its start. The
    partitions are contiguous from July to December: no instant lands in the default
    partition, and ``arcilogger-db check`` names the last day covered whole."""
    with monthly_engine.begin() as connection:
        ensure_partitions(connection, today=date(2026, 12, 15), months_back=0, months_ahead=0)
    _complete(monthly_engine, date(2026, 11, 25))
    december = month_bounds(date(2026, 12, 1))
    spans = _spans(monthly_engine, "mt_dhcp")
    assert spans[0][0] == month_bounds(date(2026, 7, 1))[0]
    assert spans[-1] == december
    assert all(high == low for (_, high), (low, _) in itertools.pairwise(spans))
    with monthly_engine.connect() as connection:
        by_name = {p.name: (p.start, p.end) for p in list_partitions(connection, "mt_dhcp")}
        assert partition_status(connection).covered_until == date(2026, 12, 31)
    assert by_name["mt_dhcp_p20261130"] == (datetime(2026, 11, 30, tzinfo=UTC), december[0])
    assert "mt_dhcp_p20261201" not in by_name  # in December's monthly partition
    october_end = month_bounds(date(2026, 10, 1))[1]
    times = [
        october_end - timedelta(microseconds=1),
        october_end,
        datetime(2026, 11, 1, tzinfo=UTC),
        december[0] - timedelta(microseconds=1),
        december[0],
    ]
    with Session(monthly_engine) as session:
        session.add_all([_dhcp(when) for when in times])
        session.commit()
    assert [where for _, where in _where(monthly_engine)] == [
        "mt_dhcp_p202610",
        "mt_dhcp_p20261031",
        "mt_dhcp_p20261101",
        "mt_dhcp_p20261130",
        "mt_dhcp_p202612",
    ]
    with monthly_engine.begin() as connection:
        again = ensure_partitions(connection, today=date(2026, 11, 25))
    assert (again.created, again.remaining) == ([], 0)


def test_a_monthly_partition_goes_with_the_change_over_partition_after_it(
    monthly_engine: Engine,
) -> None:
    """The drop rule: October's partition ends at 31.10. 21:00 UTC, ``mt_dhcp_p20261031`` after
    it at the UTC midnight; both stay at the cutoff 01.11. and go together at 02.11. Were the
    month to go alone, the name of that UTC day would keep an archive reload of 31.10. from
    getting the rest of the day, and 21 hours of its rows would wait in the default partition
    (#110 review). A reload of the days after both went gets whole UTC days."""
    november = date(2026, 11, 20)
    _complete(monthly_engine, november)
    pair = {"mt_dhcp_p202610", day_partition_name("mt_dhcp", date(2026, 10, 31))}
    first, _ = _window(monthly_engine, date(2026, 11, 1), november)
    assert "mt_dhcp_p202609" in first.dropped
    assert pair.isdisjoint(first.dropped)
    second, _ = _window(monthly_engine, date(2026, 11, 2), november)
    assert pair <= set(second.dropped)
    assert day_partition_name("mt_dhcp", date(2026, 11, 1)) not in second.dropped
    with monthly_engine.begin() as connection:
        reload = ensure_days(connection, date(2026, 10, 27), date(2026, 10, 31))
    assert [name for name in reload.created if name.startswith("mt_dhcp_")] == [
        day_partition_name("mt_dhcp", day) for day in _days(date(2026, 10, 26), date(2026, 10, 31))
    ]
    start = day_window(date(2026, 10, 27))[0].astimezone(UTC)
    times = [start + timedelta(minutes=30 * n) for n in range(5 * 48)]
    with Session(monthly_engine) as session:
        session.add_all([_dhcp(when) for when in times])
        session.commit()
    assert [where for _, where in _where(monthly_engine)] == [
        f"mt_dhcp_p{when:%Y%m%d}" for when in times
    ]


def test_a_utc_day_reads_one_daily_partition(plain_engine: Engine) -> None:
    """The lookup engine asks a range of a day or more one UTC day at a time
    (``lookup.engine.CHUNK``, written for TimescaleDB's chunks): such a step must be planned on
    one daily partition. Over two, an ordered ``LIMIT 1`` merges both and reads the second
    one's rows of the address before it answers (#110: the D15 check's 3.8 guards failed with
    partitions cut at site midnight). A site day's range reads two; the search page reads a
    site day in time order, which is an ordered ``Append`` of the two, newest first (it stops in
    the first one), never a ``Merge Append`` and never the default partition."""
    day = TODAY - timedelta(days=2)

    def read(start: datetime, end: datetime) -> set[str]:
        with plain_engine.connect() as connection:
            plan = connection.execute(
                text(
                    "EXPLAIN SELECT time FROM fg_traffic WHERE time >= :start AND time < :end "
                    "ORDER BY transip, time LIMIT 1"
                ),
                {"start": start, "end": end},
            ).scalars()
            return set(re.findall(r"fg_traffic_p\d{8}", "\n".join(plan)))

    assert read(*day_bounds(day)) == {day_partition_name("fg_traffic", day)}
    piece = day_bounds(day)[0] + timedelta(hours=11), day_bounds(day)[1]  # a step's last piece
    assert read(*piece) == {day_partition_name("fg_traffic", day)}
    assert read(*day_window(day)) == {
        day_partition_name("fg_traffic", day - timedelta(days=1)),
        day_partition_name("fg_traffic", day),
    }
    with plain_engine.connect() as connection:
        plan = "\n".join(
            connection.execute(
                text(
                    "EXPLAIN SELECT id FROM messages WHERE time >= :start AND time < :end "
                    "ORDER BY time DESC, id DESC LIMIT 101"
                ),
                dict(zip(("start", "end"), day_window(day), strict=True)),
            ).scalars()
        )
    assert "Merge Append" not in plan
    assert "Append" in plan
    assert re.findall(r"on (messages_\w+)", plan) == [
        day_partition_name("messages", day),
        day_partition_name("messages", day - timedelta(days=1)),
    ]


def _ips(engine: Engine) -> list[tuple[int, str]]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT id, host(ip) FROM mt_dhcp ORDER BY id"))
        return [(int(row[0]), str(row[1])) for row in rows]


def test_retention_drops_whole_months_and_purges_old_default_rows(monthly_engine: Engine) -> None:
    old = datetime(2026, 7, 20, tzinfo=UTC)
    with Session(monthly_engine) as session:
        session.add_all([_dhcp(old), _dhcp(datetime(2001, 1, 1, tzinfo=UTC))])
        session.add(_dhcp(datetime(2026, 9, 10, tzinfo=UTC)))
        session.commit()
    with monthly_engine.begin() as connection:
        report = drop_before(connection, date(2026, 9, 15))
        left = {p.name for p in list_partitions(connection, "mt_dhcp")}
    # July and August end before 15.09. and go; September (containing the cutoff) stays whole.
    assert "mt_dhcp_p202607" in report.dropped
    assert "mt_dhcp_p202608" in report.dropped
    assert "mt_dhcp_p202609" not in report.dropped
    assert {"mt_dhcp_p202609", "mt_dhcp_p202610", "mt_dhcp_default"} <= left
    assert report.purged == 1  # the 2001 outlier
    assert [where for _, where in _where(monthly_engine)] == ["mt_dhcp_p202609"]


# -- dropping by cutoff ---------------------------------------------------------------------------


def _counts_of_the_rows(connection: Connection) -> Counter[CountKey]:
    rows = connection.execute(
        select(Message.time, Message.source, Message.raw_day, Message.type, Message.subtype)
    ).mappings()
    return Counter(count_key(row, None, None) for row in rows)


def _stored_counts(connection: Connection) -> Counter[CountKey]:
    stored: Counter[CountKey] = Counter()
    c = SearchCount
    for hour, source, raw_day, kind, tbl, action, net, n in connection.execute(
        select(c.hour, c.source, c.raw_day, c.kind, c.tbl, c.action, c.net, c.n)
    ):
        net_text = None if net is None else str(net)
        stored[CountKey(hour, source, raw_day, kind, tbl, action, net_text)] += n
    return stored


def _heartbeats(connection: Connection, times: list[datetime]) -> None:
    """One ``messages`` row a time, its search count and its clock sample (received at once)."""
    connection.execute(
        insert(Message),
        [
            {
                "time": when,
                "time_received": when,
                "source": "core-r1",
                "sender": "172.20.10.1",
                "raw_day": site_day(when),
                "raw_line": number,
                "raw_offset": 100 * number,
                "type": "heartbeat",
                "text": "ARCI-HEARTBEAT CORE-R1",
            }
            for number, when in enumerate(times, start=1)
        ],
    )
    upsert_counts(connection, _counts_of_the_rows(connection))
    assert sum(_stored_counts(connection).values()) == len(times)
    upsert_samples(connection, {ClockKey("core-r1", minute_of(when), 0): 1 for when in times})


def _samples(connection: Connection) -> int:
    return int(connection.execute(text("SELECT count(*) FROM source_clock_samples")).scalar_one())


def test_retention_deletes_the_search_counts_of_what_it_drops(monthly_engine: Engine) -> None:
    """``search_counts`` follows the rows (contract R6): the counts of the dropped months and of
    the purged outliers go; the month holding the cutoff keeps its rows and so its counts."""
    times = [
        datetime(2001, 1, 1, 9, tzinfo=UTC),  # default partition: purged
        datetime(2026, 7, 20, 9, tzinfo=UTC),  # July: dropped
        datetime(2026, 8, 31, 20, 59, tzinfo=UTC),  # 31.08. 23:59 site time: August, dropped
        datetime(2026, 8, 31, 21, 0, tzinfo=UTC),  # 01.09. 00:00 site time: September, kept
        datetime(2026, 9, 10, 9, tzinfo=UTC),  # before the cutoff, in its month: kept
        datetime(2026, 10, 1, 9, tzinfo=UTC),  # after the cutoff
    ]
    with monthly_engine.begin() as connection:
        _heartbeats(connection, times)
    with monthly_engine.begin() as connection:
        report = drop_before(connection, date(2026, 9, 15))
        assert report.counts_deleted == 3
        assert report.clock_samples_deleted == 3  # received before September, site time
        assert report.purged == 1
        assert sum(_counts_of_the_rows(connection).values()) == 3
        assert _stored_counts(connection) == _counts_of_the_rows(connection)


def test_the_window_drops_days_their_counts_and_clock_samples(plain_engine: Engine) -> None:
    """Daily partitions: every partition that ends on or before the cutoff day's local midnight
    goes (the UTC day holding that midnight stays whole, with up to 21 hours of the day before,
    as a TimescaleDB chunk), the default partition's older rows too, and the counts and samples
    of exactly those rows."""
    _complete(plain_engine, TODAY)
    cutoff = TODAY - timedelta(days=WINDOW - 3)  # 15.09.
    midnight = day_window(cutoff)[0].astimezone(UTC)  # 14.09. 21:00 UTC
    times = [
        datetime(2001, 1, 1, 9, tzinfo=UTC),  # default partition: purged
        midnight - timedelta(days=2),  # an old day of the window: dropped
        midnight - timedelta(hours=21, minutes=1),  # 13.09. 23:59 UTC: dropped
        midnight - timedelta(hours=21),  # 14.09. 00:00 UTC, the kept UTC day: kept
        midnight - timedelta(minutes=1),  # 14.09. 23:59 site time: kept with its UTC day
        midnight,  # 15.09. 00:00 site time: kept
        midnight + timedelta(days=5),
        _local(TODAY + timedelta(days=3)),  # ahead of today (a clock running fast)
    ]
    with plain_engine.begin() as connection:
        _heartbeats(connection, times)
    report, steps = _window(plain_engine, cutoff, TODAY)
    assert steps == 1  # 3 days to drop: within one step's 16 days
    with plain_engine.connect() as connection:
        left = [p.day for p in list_partitions(connection, "messages") if p.day]
        assert dropped_before(connection) == cutoff
        assert report.created == []  # the window was complete
        assert report.purged == 1
        assert report.counts_deleted == 3
        assert report.clock_samples_deleted == 3
        assert _samples(connection) == 5
        assert sum(_counts_of_the_rows(connection).values()) == 5
        assert _stored_counts(connection) == _counts_of_the_rows(connection)
    assert left == _days(cutoff - timedelta(days=1), TODAY + timedelta(days=DAYS_AHEAD))
    dropped = _days(TODAY - timedelta(days=WINDOW_DAYS), cutoff - timedelta(days=2))
    assert sorted(report.dropped) == sorted(
        day_partition_name(table, day) for table in TIME_SERIES_TABLES for day in dropped
    )
    with plain_engine.begin() as connection:
        again = drop_before(connection, cutoff)  # idempotent
    assert (again.dropped, again.purged, again.counts_deleted) == ([], 0, 0)


def test_the_window_never_drops_the_last_week(plain_engine: Engine) -> None:
    """A wrong cutoff (a clock far ahead, a caller's error) cannot empty the database."""
    with (
        plain_engine.begin() as connection,
        pytest.raises(ValueError, match="less than 7 days"),
    ):
        maintain_window(connection, TODAY - timedelta(days=MIN_WINDOW_DAYS - 1), today=TODAY)
    _complete(plain_engine, TODAY)
    report, _ = _window(plain_engine, TODAY - timedelta(days=MIN_WINDOW_DAYS), TODAY)
    assert report.dropped  # the older days of the window went
    # The UTC day of the cutoff's local midnight stays.
    assert _daily(plain_engine, "mt_dhcp")[0] == TODAY - timedelta(days=MIN_WINDOW_DAYS + 1)


def test_a_long_backlog_is_dropped_in_bounded_steps(plain_engine: Engine) -> None:
    """The first run after the upgrade from the 400-day window has months to drop: each step
    (one transaction) drops at most :data:`DAYS_PER_RUN` days of partitions, oldest first, so
    no transaction runs out of PostgreSQL's lock table; the steps reach the cutoff."""
    _complete(plain_engine, TODAY)
    oldest = TODAY - timedelta(days=WINDOW + 40)
    for first in range(0, 40, 8):  # 8 days (128 partitions) a transaction
        with plain_engine.begin() as connection:
            start = oldest + timedelta(days=first)
            ensure_days(connection, start, start + timedelta(days=7))
    cutoff = TODAY - timedelta(days=WINDOW)
    with plain_engine.begin() as connection:
        assert next_drop_cutoff(connection, cutoff) == oldest + timedelta(days=DAYS_PER_RUN)
        step = maintain_window(connection, cutoff, today=TODAY)
    assert step.applied == oldest + timedelta(days=DAYS_PER_RUN)
    assert len(step.dropped) == len(TIME_SERIES_TABLES) * DAYS_PER_RUN
    with plain_engine.connect() as connection:
        assert dropped_before(connection) == step.applied
    report, steps = _window(plain_engine, cutoff, TODAY)
    assert steps == 2  # the other 24 days: 16 + 8
    assert len(report.dropped) == len(TIME_SERIES_TABLES) * (40 - DAYS_PER_RUN)
    assert _daily(plain_engine, "fg_traffic")[0] == cutoff - timedelta(days=1)  # its midnight
    with plain_engine.connect() as connection:
        assert dropped_before(connection) == cutoff


def test_the_applied_cutoff_is_recorded_and_never_goes_back(plain_engine: Engine) -> None:
    """The status row the API reads (gone days): the newest cutoff ever applied, in the drop's
    transaction; a smaller window later does not bring the dropped days back."""
    cutoff = TODAY - timedelta(days=20)
    with plain_engine.begin() as connection:
        assert dropped_before(connection) is None
        drop_before(connection, cutoff)
        assert dropped_before(connection) == cutoff
        drop_before(connection, cutoff - timedelta(days=5))
        assert dropped_before(connection) == cutoff
        connection.rollback()  # the row goes with the drop's transaction
    with plain_engine.begin() as connection:
        assert dropped_before(connection) is None
        drop_before(connection, cutoff + timedelta(days=1))
    with plain_engine.connect() as connection:
        row = connection.execute(
            text("SELECT value, updated_by FROM settings WHERE key = :key"), {"key": DROPPED_KEY}
        ).one()
    assert row[0]["cutoff"] == (cutoff + timedelta(days=1)).isoformat()
    assert row[1] == ":system"
    assert DROPPED_KEY not in DEFAULT_SETTINGS  # a status row, never an administrator's setting


def test_a_raw_days_rows_reach_two_partitions_either_side() -> None:
    """The margin the window job keeps around an unsealed day and an archive reload loads
    around its days: a row is at most 25 hours from its raw day (``COUNTS_TIME_MARGIN``)."""
    assert RAW_DAY_MARGIN == 2


def test_template_storage_matches_partitioned_tables(db_engine: Engine) -> None:
    """With TimescaleDB the tables are hypertables and partition maintenance does nothing."""
    with db_engine.begin() as connection:
        status: dict[str, Any] = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
        tables = partitioned_tables(connection)
        report = ensure_partitions(connection)
        reload = ensure_days(connection, date(2026, 8, 1), date(2026, 8, 3))
        timescale = hypertables(connection)
    if status["storage"] == "hypertables":
        assert tables == []
        assert (report.tables, report.created, reload.created) == ([], [], [])
        assert timescale == list(TIME_SERIES_TABLES)
    else:
        assert tables == list(TIME_SERIES_TABLES)
        assert timescale == []


def test_months_back_follow_the_database_retention() -> None:
    assert months_back_for(date(2026, 10, 12), 400) == 13  # 2025-09-08 → September 2025
    assert months_back_for(date(2026, 10, 31), 400) == 13  # 2025-09-26
    assert months_back_for(date(2026, 10, 1), 400) == 14  # 2025-08-27
    assert months_back_for(date(2026, 10, 12), 10) == MONTHS_BACK  # never fewer
    assert months_back_for(date(2026, 1, 15), 365) == 12


# -- TimescaleDB ----------------------------------------------------------------------------------


def test_drop_before_drops_whole_chunks_on_timescaledb(db_engine: Engine) -> None:
    """G2/G3 on TimescaleDB: ``drop_chunks`` older than the cutoff's local midnight (a
    compressed chunk too). The chunk holding that instant (a UTC day) is kept whole, and with it
    its rows of the day before and their counts. On the Linux CI job
    (``ARCILOGGER_EXPECT_TIMESCALE=1``) it fails instead of skipping."""
    with db_engine.connect() as connection:
        status = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
    if status["storage"] != "hypertables":
        if os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1":
            pytest.fail(f"TimescaleDB expected but the template has {status['storage']}")
        pytest.skip("TimescaleDB is not installed (the Linux CI job runs this)")
    pause_compression_jobs(db_engine)
    cutoff = date(2026, 9, 15)  # local midnight 14.09. 21:00 UTC
    times = [
        datetime(2026, 9, 12, 9, tzinfo=UTC),  # chunk 12.09.: dropped
        datetime(2026, 9, 13, 23, tzinfo=UTC),  # chunk 13.09. (14.09. 02:00 site time): dropped
        datetime(2026, 9, 14, 10, tzinfo=UTC),  # chunk 14.09., before the cutoff: kept whole
        datetime(2026, 9, 14, 22, tzinfo=UTC),  # 15.09. 01:00 site time: kept
        datetime(2026, 9, 20, 9, tzinfo=UTC),
    ]
    with db_engine.begin() as connection:
        _heartbeats(connection, times)
        connection.execute(
            insert(MtDhcp),
            [
                {
                    "time": when,
                    "time_received": when,
                    "source": "core-r1",
                    "sender": "172.20.10.1",
                    "raw_day": site_day(when),
                    "raw_line": 1,
                    "raw_offset": 0,
                    "server": "dhcp60",
                    "vlan": 60,
                    "action": "assigned",
                    "ip": "172.20.60.183",
                    "mac": "00:00:5e:00:53:46",
                }
                for when in (times[0], times[4])
            ],
        )
        if status.get("compression"):
            connection.execute(
                text(
                    "SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks("
                    "'messages', older_than => CAST(:before AS timestamptz)) AS c"
                ),
                {"before": datetime(2026, 9, 13, tzinfo=UTC)},
            )
    with db_engine.begin() as connection:
        report = drop_before(connection, cutoff)
        assert report.tables == []
        assert report.hypertables == list(TIME_SERIES_TABLES)
        assert len([name for name in report.dropped if "_hyper_" in name]) == len(report.dropped)
        assert len(report.dropped) == 3  # messages: 12.09. and 13.09.; mt_dhcp: 12.09.
        kept = connection.execute(text("SELECT time FROM messages ORDER BY time")).scalars().all()
        assert kept == times[2:]
        assert connection.execute(text("SELECT count(*) FROM mt_dhcp")).scalar_one() == 1
        assert report.counts_deleted == 2
        assert report.clock_samples_deleted == 2
        assert _stored_counts(connection) == _counts_of_the_rows(connection)
        first_chunk = connection.execute(
            text(
                "SELECT min(range_start) FROM timescaledb_information.chunks "
                "WHERE hypertable_name = 'messages'"
            )
        ).scalar_one()
        assert first_chunk == datetime(2026, 9, 14, tzinfo=UTC)
    with db_engine.begin() as connection:
        again = drop_before(connection, cutoff)
    assert (again.dropped, again.counts_deleted, again.clock_samples_deleted) == ([], 0, 0)


def _with_compression(engine: Engine) -> None:
    """The deadlock tests of #133 need hypertables with compression (on the Linux CI job,
    ``ARCILOGGER_EXPECT_TIMESCALE=1``, they fail instead of skipping). The compression policies
    are paused: the tests play the policy themselves, step by step."""
    with engine.connect() as connection:
        status = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
    if status["storage"] != "hypertables" or not status.get("compression"):
        if os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1":
            pytest.fail(f"TimescaleDB with compression expected, the template has {status}")
        pytest.skip("TimescaleDB with compression is not available (the Linux CI job runs this)")
    pause_compression_jobs(engine)


def _chunks_by_id(engine: Engine, table: str) -> list[str]:
    """The chunks of ``table`` as ``CAST(chunk AS regclass)`` prints them, in chunk-id order."""
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT CAST(c AS text) FROM show_chunks(CAST(:table AS regclass)) AS c "
                    "ORDER BY _timescaledb_functions.chunk_id_from_relid(c)"
                ),
                {"table": table},
            ).scalars()
        )


def _sqlstate(error: BaseException) -> str | None:
    return getattr(getattr(error, "orig", error), "sqlstate", None)


class _Backend:
    """One transaction run in its own thread (one backend): its pid, its result or its error.
    :meth:`wait_for_lock` returns once the backend waits for a lock, which is how the tests
    order their steps (no sleeps that guess a duration)."""

    def __init__(self, engine: Engine, work: Callable[[Connection], object]) -> None:
        self.engine, self.work = engine, work
        self.pid: list[int] = []
        self.results: list[object] = []
        self.errors: list[BaseException] = []
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def _run(self) -> None:
        try:
            with self.engine.begin() as connection:
                self.pid.append(connection.execute(text("SELECT pg_backend_pid()")).scalar_one())
                self.results.append(self.work(connection))
        except BaseException as exc:  # the test thread asserts on it
            self.errors.append(exc)

    def wait_for_lock(self, watcher: Connection, relation: str | None = None) -> None:
        deadline = time.monotonic() + 30
        while True:
            waiting = watcher.execute(
                text(
                    "SELECT count(*) FROM pg_locks WHERE pid = ANY(CAST(:pid AS integer[])) "
                    "AND NOT granted AND (CAST(:relation AS text) IS NULL "
                    "OR relation = CAST(CAST(:relation AS text) AS regclass))"
                ),
                {"pid": self.pid, "relation": relation},
            ).scalar_one()
            watcher.rollback()
            if waiting:
                return
            assert self.thread.is_alive(), f"finished without waiting for {relation}: {self.errors}"
            assert time.monotonic() < deadline, f"never waited for {relation}"
            time.sleep(0.02)

    def join(self) -> None:
        self.thread.join(30)
        assert not self.thread.is_alive()


def _empty(engine: Engine, table: str) -> bool:
    with engine.connect() as connection:
        rows = connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one()  # noqa: S608
    return rows == 0 and _chunks_by_id(engine, table) == []


@pytest.mark.parametrize("partial", [False, True], ids=["compression", "recompression"])
def test_a_compression_that_has_opened_a_chunk_is_not_a_deadlock_for_the_drop(
    db_engine: Engine, partial: bool
) -> None:
    """#133: the compression policy opens a chunk (``compress_chunk`` locks its table), then
    locks the chunk's catalog row; ``drop_chunks`` locks the catalog rows of its chunks, then
    their tables. :func:`drop_before` locks the chunks that go first, so it waits for the open
    one without holding a row the compression waits for: both finish, none is aborted with
    ``deadlock detected``. ``recompression``: the chunk was compressed and a late row made it
    partial, so the policy compresses it again."""
    _with_compression(db_engine)
    cutoff = date(2026, 9, 15)  # local midnight 14.09. 21:00 UTC
    with Session(db_engine) as session:
        session.add_all(
            [
                _dhcp(datetime(2026, 9, 12, 9, tzinfo=UTC)),
                _dhcp(datetime(2026, 9, 13, 9, tzinfo=UTC)),
            ]
        )
        session.commit()
    chunks = _chunks_by_id(db_engine, "mt_dhcp")
    assert len(chunks) == 2
    opened = chunks[1]  # 13.09.
    if partial:
        with db_engine.begin() as connection:
            connection.execute(
                text("SELECT compress_chunk(CAST(:chunk AS regclass))"), {"chunk": opened}
            )
        with Session(db_engine) as session:
            session.add(_dhcp(datetime(2026, 9, 13, 10, tzinfo=UTC), ip="172.20.60.184"))
            session.commit()
    with db_engine.connect() as compression, db_engine.connect() as watcher:
        # 1. The compression has opened the chunk.
        compression.execute(text(f"LOCK TABLE {opened} IN ACCESS SHARE MODE"))
        # 2. The step of the window drops what is older than the cutoff: it waits for the chunk.
        step = _Backend(db_engine, lambda connection: drop_before(connection, cutoff))
        step.wait_for_lock(watcher, opened)
        # 3. The compression goes on with the chunk it has opened, and commits. A recompression
        # gives up instead when another transaction waits for the chunk (SQLSTATE 40001,
        # "aborting recompression due to concurrent DML on uncompressed data, retrying with
        # next policy run"): TimescaleDB's own way out, not a deadlock.
        outcome: object
        try:
            outcome = compression.execute(
                text("SELECT CAST(compress_chunk(CAST(:chunk AS regclass)) AS text)"),
                {"chunk": opened},
            ).scalar_one()
            compression.commit()
        except DBAPIError as error:
            compression.rollback()
            outcome = _sqlstate(error)
        step.join()
    assert step.errors == []
    assert outcome == ("40001" if partial else opened)
    assert _empty(db_engine, "mt_dhcp")


def test_a_compression_that_comes_after_the_drop_has_locked_the_chunk_is_no_deadlock(
    db_engine: Engine,
) -> None:
    """#133, the other order: the step has locked the chunk when the compression policy comes
    to it. The compression waits for the chunk's table holding no catalog row, and fails
    without a deadlock once the step has dropped the chunk ("chunk deleted by other
    transaction"); the policy tries again on its next run. A TimescaleDB whose
    ``compress_chunk`` locked the catalog row first would turn this into a deadlock."""
    _with_compression(db_engine)
    cutoff = date(2026, 9, 15)
    with Session(db_engine) as session:
        session.add_all(
            [
                _dhcp(datetime(2026, 9, 12, 9, tzinfo=UTC)),
                _dhcp(datetime(2026, 9, 13, 9, tzinfo=UTC)),
            ]
        )
        session.commit()
    locked, held = _chunks_by_id(db_engine, "mt_dhcp")  # 12.09., 13.09.
    with db_engine.connect() as blocker, db_engine.connect() as watcher:
        # 1. Another reader holds the second chunk: the step locks the first and waits there.
        blocker.execute(text(f"LOCK TABLE {held} IN ACCESS SHARE MODE"))
        step = _Backend(db_engine, lambda connection: drop_before(connection, cutoff))
        step.wait_for_lock(watcher, held)
        # 2. The compression comes to the first chunk, which the step has locked.
        compression = _Backend(
            db_engine,
            lambda connection: connection.execute(
                text("SELECT compress_chunk(CAST(:chunk AS regclass))"), {"chunk": locked}
            ).scalar_one(),
        )
        compression.wait_for_lock(watcher, locked)
        # 3. The reader ends: the step drops both chunks.
        blocker.commit()
        step.join()
        compression.join()
    assert step.errors == []
    assert [_sqlstate(error) for error in compression.errors] == ["55P03"]  # lock_not_available
    assert _empty(db_engine, "mt_dhcp")


def test_the_drop_locks_the_chunks_in_the_order_a_reader_does(db_engine: Engine) -> None:
    """#133: :func:`drop_before` locks its chunks in chunk-id order, the order in which a query
    of the whole hypertable (``min(time)`` of ``usage`` or of the lookup's window) locks them.
    In name order (``_10_`` before ``_9_``) the step held the chunks from 10 on, the reader
    those before 10, and each waited for the other's."""
    _with_compression(db_engine)
    cutoff = date(2026, 9, 15)
    with Session(db_engine) as session:
        session.add_all(
            [_dhcp(datetime(2026, 9, 1, 9, tzinfo=UTC) + timedelta(days=day)) for day in range(12)]
        )
        session.commit()
    chunks = _chunks_by_id(db_engine, "mt_dhcp")
    assert len(chunks) == 12
    assert sorted(chunks) != chunks, "the chunk ids must cross a digit (1 to 9, then 10 on)"
    last = chunks[-1]  # the last chunk the step locks
    with db_engine.connect() as blocker, db_engine.connect() as watcher:
        # 1. Another reader holds the last chunk: the step has locked all the others, waits.
        blocker.execute(text(f"LOCK TABLE {last} IN ACCESS SHARE MODE"))
        step = _Backend(db_engine, lambda connection: drop_before(connection, cutoff))
        step.wait_for_lock(watcher, last)
        # 2. A query of every chunk waits for the first one it needs that the step holds.
        reader = _Backend(
            db_engine,
            lambda connection: connection.execute(select(func.min(MtDhcp.time))).scalar_one(),
        )
        reader.wait_for_lock(watcher)
        # 3. The other reader ends: the step goes on with the chunks it has not locked yet.
        blocker.commit()
        step.join()
        reader.join()
    assert step.errors == []
    assert (reader.errors, reader.results) == ([], [None])  # the chunks went before it read
    assert _empty(db_engine, "mt_dhcp")


def test_the_lock_of_a_step_is_waited_for_its_timeout_only(db_engine: Engine) -> None:
    """``lock_partitions(timeout_ms=…)`` (W5-G-24): a lock held shared (an export) is waited for
    that long, then :class:`PartitionLockTimeoutError` (``lock_not_available``) and the transaction
    is aborted; any other failure is raised as it is; once it is granted, the transaction's other
    lock waits keep the ``lock_timeout`` they had."""
    other = engine_from_url(db_engine.url, poolclass=NullPool)
    try:
        with other.connect() as holder:
            holder.execute(
                text("SELECT pg_advisory_lock_shared(:key)"), {"key": PARTITION_LOCK_KEY}
            )
            holder.commit()
            with db_engine.connect() as connection:
                started = time.monotonic()
                with pytest.raises(PartitionLockTimeoutError):
                    lock_partitions(connection, timeout_ms=200)
                assert 0.2 <= time.monotonic() - started < 2.0
                with pytest.raises(DBAPIError, match="aborted"):
                    connection.execute(text("SELECT 1"))
                connection.rollback()
                # Any other failure of the statement goes on as it is (here a cancel).
                connection.execute(text("SET LOCAL statement_timeout = '100ms'"))
                with pytest.raises(DBAPIError) as failed:
                    lock_partitions(connection, timeout_ms=5000)
                assert not isinstance(failed.value, PartitionLockTimeoutError)
                assert getattr(failed.value.orig, "sqlstate", None) == "57014"
                connection.rollback()
            holder.execute(
                text("SELECT pg_advisory_unlock_shared(:key)"), {"key": PARTITION_LOCK_KEY}
            )
            holder.commit()
    finally:
        other.dispose()
    with db_engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '7s'"))
        lock_partitions(connection, timeout_ms=200)
        assert connection.execute(text("SHOW lock_timeout")).scalar_one() == "7s"
    with db_engine.begin() as connection:
        lock_partitions(connection, timeout_ms=200)
        assert connection.execute(text("SHOW lock_timeout")).scalar_one() == "0"


def test_a_steps_lock_waits_share_one_budget(db_engine: Engine) -> None:
    """W5-G-24, #124 stage 2: a step's wait for the partition lock (an export holds it shared)
    and its table locks after it (a reader that takes no partition lock, a plain ``SELECT``,
    holds the table: on plain PostgreSQL the parent, on TimescaleDB the chunk) share one
    :class:`LockBudget`. The table is waited for what the partition lock left: the step gives up
    after its budget in all (here at least 1 s for the lock, then the rest for the table: 2 s,
    not 3 s), with :class:`PartitionLockTimeoutError`, nothing dropped. A step whose locks are
    free keeps the transaction's ``lock_timeout``."""
    cutoff = date(2026, 9, 15)  # local midnight 14.09. 21:00 UTC
    with db_engine.begin() as connection:
        hyper = bool(hypertables(connection))
        ensure_days(connection, date(2026, 9, 12), date(2026, 9, 13))  # plain PostgreSQL
    if hyper:
        pause_compression_jobs(db_engine)
    with Session(db_engine) as session:
        session.add_all(
            [
                _dhcp(datetime(2026, 9, 12, 9, tzinfo=UTC)),
                _dhcp(datetime(2026, 9, 13, 9, tzinfo=UTC)),
            ]
        )
        session.commit()
    held = _chunks_by_id(db_engine, "mt_dhcp")[0] if hyper else "mt_dhcp"
    budget_ms = 2000
    export = engine_from_url(db_engine.url, poolclass=NullPool)
    try:
        with (
            export.connect() as holder,
            db_engine.connect() as reader,
            db_engine.connect() as watcher,
        ):
            holder.execute(
                text("SELECT pg_advisory_lock_shared(:key)"), {"key": PARTITION_LOCK_KEY}
            )
            holder.commit()
            assert reader.execute(text("SELECT count(*) FROM mt_dhcp")).scalar_one() == 2
            started = time.monotonic()
            step = _Backend(
                db_engine,
                lambda connection: drop_before(connection, cutoff, budget=LockBudget(budget_ms)),
            )
            step.wait_for_lock(watcher)  # the partition lock
            time.sleep(budget_ms / 2000)  # half the budget goes to it, at least
            holder.execute(
                text("SELECT pg_advisory_unlock_shared(:key)"), {"key": PARTITION_LOCK_KEY}
            )
            holder.commit()
            step.wait_for_lock(watcher, held)  # then the table the reader holds
            step.join()
            took = time.monotonic() - started
            reader.rollback()
    finally:
        export.dispose()
    assert [type(error) for error in step.errors] == [PartitionLockTimeoutError]
    assert budget_ms / 1000 <= took < budget_ms / 1000 + 0.8  # not half the budget more
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM mt_dhcp")).scalar_one() == 2
    with db_engine.begin() as connection:
        connection.execute(text("SET LOCAL lock_timeout = '7s'"))
        report = drop_before(connection, cutoff, budget=LockBudget(200))
        assert connection.execute(text("SHOW lock_timeout")).scalar_one() == "7s"
    assert report.dropped
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM mt_dhcp")).scalar_one() == 0


def test_a_budget_used_up_still_bounds_the_wait(db_engine: Engine) -> None:
    """#124 stage 2 review: a lock granted in the budget's last moment can leave nothing (the
    wait counts the round trip): what is left is 1 ms then, never 0, which PostgreSQL takes as
    no limit (a table lock after it would wait as long as a reader holds it)."""
    budget = LockBudget(100)
    budget.waited = 0.2
    assert budget.left_ms() == 1
    with db_engine.begin() as connection:
        budget.bound(connection)
        assert connection.execute(text("SHOW lock_timeout")).scalar_one() == "1ms"


def _ms(setting: str) -> int:
    """A ``lock_timeout`` as PostgreSQL shows it (``1500ms``, ``2s``), in milliseconds."""
    if setting.endswith("ms"):
        return int(setting[:-2])
    assert setting.endswith("s"), setting
    return int(setting[:-1]) * 1000


@contextmanager
def _export_holding(engine: Engine) -> Iterator[Connection]:
    """The partition lock held shared at session level, as a running search export holds it;
    the connection lets go of it with :func:`_let_go`."""
    export = engine_from_url(engine.url, poolclass=NullPool)
    try:
        with export.connect() as holder:
            holder.execute(
                text("SELECT pg_advisory_lock_shared(:key)"), {"key": PARTITION_LOCK_KEY}
            )
            holder.commit()
            yield holder
    finally:
        export.dispose()


def _made(engine: Engine, table: str, day: date) -> bool:
    """Whether ``table`` has a daily partition of the site day ``day`` (its UTC days)."""
    with engine.connect() as connection:
        return any(p.day in utc_days(day, day) for p in list_partitions(connection, table))


def _let_go(holder: Connection) -> None:
    holder.execute(text("SELECT pg_advisory_unlock_shared(:key)"), {"key": PARTITION_LOCK_KEY})
    holder.commit()


def test_ensure_days_waits_for_the_lock_and_a_table_within_one_budget(db_engine: Engine) -> None:
    """W5-G-24, #124 stage 2 review: ingest's ``ensure_days(budget=)`` as the window step: an
    export holds the partition lock for half the budget, then a reader that takes no partition
    lock holds the first table: the table is waited for what is left, the call gives up after
    the budget in all with :class:`PartitionLockTimeoutError`, nothing made. On TimescaleDB
    there is nothing to make and no lock is taken."""
    day = date(2026, 6, 10)  # before any window the fixtures make
    with db_engine.connect() as connection:
        tables = partitioned_tables(connection)
    if not tables:  # TimescaleDB
        with _export_holding(db_engine), db_engine.begin() as connection:
            assert ensure_days(connection, day, day, budget=LockBudget(200)).created == []
        return
    budget_ms = 2000
    with (
        _export_holding(db_engine) as holder,
        db_engine.connect() as reader,
        db_engine.connect() as watcher,
    ):
        reader.execute(text(f"SELECT count(*) FROM {tables[0]}"))  # noqa: S608
        started = time.monotonic()
        step = _Backend(
            db_engine,
            lambda connection: ensure_days(connection, day, day, budget=LockBudget(budget_ms)),
        )
        step.wait_for_lock(watcher)  # the partition lock
        time.sleep(budget_ms / 2000)  # half the budget goes to it, at least
        _let_go(holder)
        step.wait_for_lock(watcher, tables[0])  # then the table the reader holds
        step.join()
        took = time.monotonic() - started
        reader.rollback()
    assert [type(error) for error in step.errors] == [PartitionLockTimeoutError]
    assert budget_ms / 1000 <= took < budget_ms / 1000 + 0.8  # not half the budget more
    assert not _made(db_engine, tables[0], day)


def test_the_statements_after_a_lock_wait_get_what_is_left(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """W5-G-24, #124 stage 2 review: within a :class:`LockBudget` the statements after a wait
    wait at most what is left of it, not what was left before the wait: the partitions' table
    locks after the partition lock (an export held it 0.5 s of 2 s), and those after a table
    lock that a reader held 0.5 s. The step then makes its partitions."""
    day = date(2026, 6, 11)
    with db_engine.connect() as connection:
        tables = partitioned_tables(connection)
    if not tables:  # TimescaleDB: nothing to make
        with db_engine.begin() as connection:
            assert ensure_days(connection, day, day, budget=LockBudget(200)).created == []
        return
    seen: list[int] = []
    real = partitions_module._lock_table

    def lock_table(connection: Connection, name: str, budget: LockBudget | None) -> None:
        seen.append(_ms(connection.execute(text("SHOW lock_timeout")).scalar_one()))
        real(connection, name, budget)

    monkeypatch.setattr(partitions_module, "_lock_table", lock_table)
    with (
        _export_holding(db_engine) as holder,
        db_engine.connect() as reader,
        db_engine.connect() as watcher,
    ):
        reader.execute(text(f"SELECT count(*) FROM {tables[0]}"))  # noqa: S608
        step = _Backend(
            db_engine, lambda connection: ensure_days(connection, day, day, budget=LockBudget(2000))
        )
        step.wait_for_lock(watcher)  # the partition lock
        time.sleep(0.5)
        _let_go(holder)
        step.wait_for_lock(watcher, tables[0])
        time.sleep(0.5)
        reader.rollback()
        step.join()
    assert step.errors == []
    first, after_table = seen[0], seen[1]
    assert first <= 1500  # what the partition lock left, not the whole 2 s
    assert after_table <= first - 500  # what the table lock left
    assert _made(db_engine, tables[0], day)
