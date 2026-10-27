"""Native monthly partitions of the time-series tables (plain PostgreSQL without TimescaleDB)."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, insert, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import upgrade
from arcilogger_db.models import TIME_SERIES_TABLES, Message, MtDhcp, SearchCount
from arcilogger_db.partitions import (
    MONTHS_AHEAD,
    MONTHS_BACK,
    add_months,
    default_partition_name,
    drop_before,
    ensure_partitions,
    include_name,
    is_partition_table,
    list_partitions,
    month_bounds,
    months_back_for,
    partition_name,
    partition_status,
    partitioned_tables,
)
from arcilogger_db.searchcounts import CountKey, count_key, upsert_counts
from arcilogger_db.settings import set_setting
from arcilogger_db.testing import TestServer

TODAY = date(2026, 10, 12)


@pytest.fixture
def plain_engine(pg_server: TestServer) -> Iterator[Engine]:
    """A database migrated without TimescaleDB (works on a TimescaleDB server too)."""
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        upgrade(engine, timescale="off")
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(name)


def _dhcp(when: datetime, ip: str = "172.20.60.183") -> MtDhcp:
    return MtDhcp(
        time=when,
        time_received=when,
        source="core-r1",
        sender="172.20.10.1",
        raw_day=when.date(),
        raw_line=1,
        raw_offset=0,
        server="dhcp60",
        vlan=60,
        action="assigned",
        ip=ip,
        mac="02:00:5e:00:53:46",
    )


def _where(engine: Engine) -> list[tuple[int, str]]:
    with engine.connect() as connection:
        return [
            (int(row[0]), str(row[1]))
            for row in connection.execute(
                text("SELECT id, tableoid::regclass::text FROM mt_dhcp ORDER BY id")
            )
        ]


def test_month_arithmetic_and_bounds_golden_values() -> None:
    assert add_months(date(2026, 11, 1), 2) == date(2027, 1, 1)
    assert add_months(date(2026, 1, 1), -1) == date(2025, 12, 1)
    # Local midnight Europe/Istanbul (UTC+3) of the 1st, inclusive start, exclusive end.
    assert month_bounds(date(2026, 10, 12)) == (
        datetime(2026, 9, 30, 21, tzinfo=UTC),
        datetime(2026, 10, 31, 21, tzinfo=UTC),
    )
    assert partition_name("fg_traffic", date(2026, 10, 1)) == "fg_traffic_p202610"
    assert default_partition_name("mt_dhcp") == "mt_dhcp_default"


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("fg_traffic_p202610", True),
        ("mt_interface_default", True),
        ("fg_traffic", False),
        ("users_default", False),  # not a time-series table
        ("fg_traffic_p2026", False),
    ],
)
def test_partition_names_are_recognised(name: str, expected: bool) -> None:
    assert is_partition_table(name) is expected
    assert include_name(name, "table", {}) is not expected
    assert include_name(name, "index", {}) is True


def test_plain_upgrade_partitions_every_time_series_table(plain_engine: Engine) -> None:
    with plain_engine.connect() as connection:
        assert partitioned_tables(connection) == list(TIME_SERIES_TABLES)
        status = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
        for table in TIME_SERIES_TABLES:
            partitions = list_partitions(connection, table)
            assert partitions[-1].is_default
            assert partitions[-1].name == f"{table}_default"
            months = [p.month for p in partitions if p.month is not None]
            # The whole db.retention_days window (400 days: 13 or 14 months) and 3 ahead.
            back = months_back_for(site_day(utc_now()), 400)
            assert len(months) == back + 1 + MONTHS_AHEAD
            assert months == sorted(months)
        overview = partition_status(connection)
    assert status["storage"] == "partitions"
    assert status["partitioned"] == list(TIME_SERIES_TABLES)
    assert status["hypertables"] == []
    assert overview.tables == 15
    assert overview.default_rows == dict.fromkeys(TIME_SERIES_TABLES, False)


def test_rows_are_routed_by_month_and_outliers_go_to_the_default(plain_engine: Engine) -> None:
    with plain_engine.connect() as connection:
        months = [p.month for p in list_partitions(connection, "mt_dhcp") if p.month]
    inside = month_bounds(months[1])[0]  # first instant of a covered month
    with Session(plain_engine) as session:
        session.add_all([_dhcp(inside), _dhcp(datetime(2001, 1, 1, tzinfo=UTC))])
        session.commit()
    assert _where(plain_engine) == [
        (1, partition_name("mt_dhcp", months[1])),
        (2, "mt_dhcp_default"),
    ]


def test_late_partition_takes_over_rows_from_the_default(plain_engine: Engine) -> None:
    """Rows of a month without a partition wait in the default partition and move later."""
    later = date(2027, 6, 15)  # beyond the created horizon
    when = datetime(2027, 6, 15, 9, tzinfo=UTC)
    with Session(plain_engine) as session:
        session.add_all([_dhcp(when, "172.20.60.37"), _dhcp(when, "172.20.60.81")])
        session.add(_dhcp(datetime(2001, 1, 1, tzinfo=UTC)))
        session.commit()
    assert {where for _, where in _where(plain_engine)} == {"mt_dhcp_default"}
    with plain_engine.begin() as connection:
        report = ensure_partitions(connection, today=later)
    june = partition_name("mt_dhcp", date(2027, 6, 1))
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
    with plain_engine.begin() as connection:
        again = ensure_partitions(connection, today=later)
    assert (again.created, again.moved) == ([], {})


def test_retention_drops_whole_months_and_purges_old_default_rows(plain_engine: Engine) -> None:
    with plain_engine.begin() as connection:
        ensure_partitions(connection, today=TODAY, months_back=3, months_ahead=0)
    old = datetime(2026, 7, 20, tzinfo=UTC)
    with Session(plain_engine) as session:
        session.add_all([_dhcp(old), _dhcp(datetime(2001, 1, 1, tzinfo=UTC))])
        session.add(_dhcp(datetime(2026, 9, 10, tzinfo=UTC)))
        session.commit()
    with plain_engine.begin() as connection:
        report = drop_before(connection, date(2026, 9, 15))
        left = {p.name for p in list_partitions(connection, "mt_dhcp")}
    # July and August end before 15.09. and go; September (containing the cutoff) stays whole.
    assert "mt_dhcp_p202607" in report.dropped
    assert "mt_dhcp_p202608" in report.dropped
    assert "mt_dhcp_p202609" not in report.dropped
    assert {"mt_dhcp_p202609", "mt_dhcp_p202610", "mt_dhcp_default"} <= left
    assert report.purged == 1  # the 2001 outlier
    assert [where for _, where in _where(plain_engine)] == ["mt_dhcp_p202609"]


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


def test_retention_deletes_the_search_counts_of_what_it_drops(plain_engine: Engine) -> None:
    """``search_counts`` follows the rows (contract R6): the counts of the dropped months and of
    the purged outliers go; the month holding the cutoff keeps its rows and so its counts."""
    with plain_engine.begin() as connection:
        ensure_partitions(connection, today=TODAY, months_back=3, months_ahead=0)
    times = [
        datetime(2001, 1, 1, 9, tzinfo=UTC),  # default partition: purged
        datetime(2026, 7, 20, 9, tzinfo=UTC),  # July: dropped
        datetime(2026, 8, 31, 20, 59, tzinfo=UTC),  # 31.08. 23:59 site time: August, dropped
        datetime(2026, 8, 31, 21, 0, tzinfo=UTC),  # 01.09. 00:00 site time: September, kept
        datetime(2026, 9, 10, 9, tzinfo=UTC),  # before the cutoff, in its month: kept
        datetime(2026, 10, 1, 9, tzinfo=UTC),  # after the cutoff
    ]
    with plain_engine.begin() as connection:
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
        assert sum(_stored_counts(connection).values()) == 6
    with plain_engine.begin() as connection:
        report = drop_before(connection, date(2026, 9, 15))
        assert report.counts_deleted == 3
        assert report.purged == 1
        assert sum(_counts_of_the_rows(connection).values()) == 3
        assert _stored_counts(connection) == _counts_of_the_rows(connection)


def test_template_storage_matches_partitioned_tables(db_engine: Engine) -> None:
    """With TimescaleDB the tables are hypertables and partition maintenance does nothing."""
    with db_engine.begin() as connection:
        status: dict[str, Any] = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
        tables = partitioned_tables(connection)
        report = ensure_partitions(connection)
    if status["storage"] == "hypertables":
        assert tables == []
        assert (report.tables, report.created) == ([], [])
    else:
        assert tables == list(TIME_SERIES_TABLES)


def test_months_back_follow_the_database_retention() -> None:
    assert months_back_for(date(2026, 10, 12), 400) == 13  # 2025-09-08 → September 2025
    assert months_back_for(date(2026, 10, 31), 400) == 13  # 2025-09-26
    assert months_back_for(date(2026, 10, 1), 400) == 14  # 2025-08-27
    assert months_back_for(date(2026, 10, 12), 10) == MONTHS_BACK  # never fewer
    assert months_back_for(date(2026, 1, 15), 365) == 12


def test_partitions_cover_the_whole_retention_window(plain_engine: Engine) -> None:
    """The vault re-ingest of any day the database keeps (db.retention_days) finds its month:
    nothing of that window lands in the default partition, which every later partition would
    have to scan under an exclusive lock."""
    with plain_engine.begin() as connection:
        ensure_partitions(connection, today=TODAY)
        for table in TIME_SERIES_TABLES:
            months = {p.month for p in list_partitions(connection, table) if p.month}
            expected = {add_months(date(2025, 9, 1), n) for n in range(0, 17)}  # 2025-09 … 2027-01
            assert expected <= months, table
    with Session(plain_engine) as session:
        set_setting(session, "db.retention_days", 800, updated_by="tolga")
        session.commit()
    with plain_engine.begin() as connection:
        report = ensure_partitions(connection, today=TODAY)
    assert partition_name("mt_dhcp", date(2024, 8, 1)) in report.created
    assert report.moved == {}
