"""Revision ``0021_db_window`` (Wave 5 contract G1, G2, G4; issue #110): the stored
``db.retention_days`` converted (400 → 30, below 7 → 7, other values kept), both keys after an
upgrade, the table ``archive_reloads`` and its checks, the empty monthly partitions ahead dropped
(daily partitions, UTC days, from the end of the current month on), and the downgrade that
merges daily partitions back into monthly ones (split by local month); on native partitions and
on TimescaleDB."""

from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Connection, Engine, func, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, current_revisions, upgrade
from arcilogger_db.models import TIME_SERIES_TABLES, ArchiveReload
from arcilogger_db.partitions import (
    DROPPED_KEY,
    add_months,
    day_bounds,
    day_partition_name,
    drop_before,
    dropped_before,
    ensure_days,
    ensure_partitions,
    list_partitions,
    month_bounds,
    month_of,
    partition_name,
)
from arcilogger_db.testing import TestServer

BEFORE = "0020_fg_traffic_transip_time"
MIGRATION = importlib.import_module("arcilogger_db.migrations.versions.v0021_db_window")
REVISION = "0021_db_window"
#: The status row of the G1 change (``arcilogger_worker.db_window.CHANGE_KEY`` audits it).
CHANGE_KEY = "db.retention_days_migration"
TABLE = "archive_reloads"
#: The objects this session holds a lock on in PostgreSQL's shared lock table (one entry each,
#: whatever the modes; fast-path locks are not in it).
HELD_LOCKS = (
    "SELECT count(*) FROM (SELECT DISTINCT locktype, database, relation, classid, objid, "
    "objsubid, transactionid FROM pg_locks WHERE pid = pg_backend_pid() AND NOT fastpath) AS held"
)
CHECKS = ("reason", "status", "range", "requested_by", "lookup", "bytes", "error_code", "ready")
COLUMNS = (
    "id",
    "requested_by",
    "requested_at",
    "reason",
    "lookup_id",
    "first_day",
    "last_day",
    "status",
    "bytes_total",
    "bytes_done",
    "error_code",
    "ready_at",
    "expires_at",
)


@pytest.fixture
def empty_engine(pg_server: TestServer) -> Iterator[Engine]:
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(name)


def _scalar(engine: Engine, sql: str, **params: Any) -> Any:
    with engine.connect() as connection:
        return connection.execute(text(sql), params).scalar()


def _timescale(engine: Engine) -> bool:
    available = _scalar(engine, "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
    return available is not None


def _migrate(engine: Engine, revision: str) -> None:
    """Only the migration (no seed, no partitions), so its own writes can be compared."""
    with engine.begin() as connection:
        if revision == BEFORE:
            command.downgrade(alembic_config(connection), revision)
        else:
            command.upgrade(alembic_config(connection), revision)


def _setting(engine: Engine, key: str) -> tuple[Any, str] | None:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT value, updated_by FROM settings WHERE key = :key"), {"key": key}
        ).one_or_none()
    return None if row is None else (row[0], row[1])


def _as_before(engine: Engine, value: Any, author: str) -> None:
    """The settings an earlier version left: ``db.retention_days`` as given, no
    ``db.reload_keep_days`` (the seed of this version wrote both at ``upgrade(…, BEFORE)``)."""
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM settings WHERE key = 'db.reload_keep_days'"))
        connection.execute(
            text(
                "UPDATE settings SET value = CAST(:value AS jsonb), updated_by = :author "
                "WHERE key = 'db.retention_days'"
            ),
            {"value": str(value), "author": author},
        )


@pytest.mark.parametrize(
    ("value", "author", "converted"),
    [
        (400, ":seed", (30, ":seed")),  # the untouched old default: follows the seed again
        (400, "tolga", (30, ":migration")),
        (3, "tolga", (7, ":migration")),
        (1, "tolga", (7, ":migration")),
        (7, "tolga", (7, "tolga")),
        (45, "tolga", (45, "tolga")),
        (730, "tolga", (730, "tolga")),
    ],
)
def test_the_window_setting_is_converted(
    empty_engine: Engine, value: int, author: str, converted: tuple[int, str]
) -> None:
    """G1: 400 → 30, below 7 → 7, every other value kept, and a change leaves the status row
    the window job audits (``settings.change``; no revision writes audit entries); the
    downgrade takes back only the 30 it or the seed wrote, and the status row."""
    upgrade(empty_engine, revision=BEFORE, timescale="off", months_back=1)  # few locks to merge
    _as_before(empty_engine, value, author)
    _migrate(empty_engine, REVISION)
    assert _setting(empty_engine, "db.retention_days") == converted
    assert _setting(empty_engine, "db.reload_keep_days") is None  # the seed inserts it
    change = {
        "old": value,
        "new": converted[0],
        "was_set_by": author,
        "migration": REVISION,
        "audited": False,
    }
    expected = (change, ":migration") if converted[0] != value else None
    assert _setting(empty_engine, CHANGE_KEY) == expected
    result = upgrade(empty_engine)
    assert _setting(empty_engine, "db.retention_days") == converted
    assert _setting(empty_engine, "db.reload_keep_days") == (7, ":seed")
    assert _setting(empty_engine, CHANGE_KEY) == expected  # the seed leaves it to the job
    assert result.seed.settings_inserted == ["db.reload_keep_days"]
    assert "db.retention_days" not in result.seed.settings_refreshed
    changes = "SELECT count(*) FROM audit_log WHERE action = 'settings.change'"
    assert _scalar(empty_engine, changes) == 0
    _migrate(empty_engine, BEFORE)
    back = (400, converted[1]) if converted[0] == 30 else converted
    assert _setting(empty_engine, "db.retention_days") == back
    assert _setting(empty_engine, "db.reload_keep_days") is None
    assert _setting(empty_engine, CHANGE_KEY) is None


def test_a_new_database_gets_both_keys_and_the_table(empty_engine: Engine) -> None:
    assert upgrade(empty_engine).after == (REVISION,)
    assert _setting(empty_engine, "db.retention_days") == (30, ":seed")
    assert _setting(empty_engine, "db.reload_keep_days") == (7, ":seed")
    inspector = inspect(empty_engine)
    assert tuple(c["name"] for c in inspector.get_columns(TABLE)) == COLUMNS
    assert inspector.get_pk_constraint(TABLE)["constrained_columns"] == ["id"]
    checks = {c["name"] for c in inspector.get_check_constraints(TABLE)}
    assert checks == {f"ck_{TABLE}_{name}" for name in CHECKS}
    indexes = {i["name"]: i for i in inspector.get_indexes(TABLE)}
    assert indexes["uq_archive_reloads_loading"]["unique"]
    assert indexes["ix_archive_reloads_status_id"]["column_names"] == ["status", "id"]


def _insert(connection: Connection, **values: Any) -> int:
    row = {
        "requested_by": "tolga",
        "reason": "lookup",
        "first_day": date(2026, 9, 1),
        "last_day": date(2026, 9, 3),
        "status": "queued",
        "error_code": None,
        "ready_at": None,
        "expires_at": None,
        "lookup_id": None,
        **values,
    }
    return int(
        connection.execute(
            text(
                f"INSERT INTO {TABLE} (requested_by, reason, first_day, last_day, "  # noqa: S608
                "status, error_code, ready_at, expires_at, lookup_id) VALUES (:requested_by, "
                ":reason, :first_day, :last_day, :status, :error_code, :ready_at, :expires_at, "
                ":lookup_id) RETURNING id"
            ),
            row,
        ).scalar_one()
    )


@pytest.mark.parametrize(
    ("constraint", "values"),
    [
        ("ck_archive_reloads_reason", {"reason": "evidence"}),
        ("ck_archive_reloads_status", {"status": "done"}),
        ("ck_archive_reloads_range", {"last_day": date(2026, 8, 31)}),
        ("ck_archive_reloads_requested_by", {"requested_by": " "}),
        ("ck_archive_reloads_error_code", {"status": "failed"}),
        ("ck_archive_reloads_error_code", {"error_code": "archive_day_not_in_vault"}),
        ("ck_archive_reloads_ready", {"status": "ready"}),
        ("ck_archive_reloads_ready", {"status": "expired", "ready_at": datetime.now(UTC)}),
        ("ck_archive_reloads_lookup", {"reason": "search", "lookup_id": 1}),
        ("fk_archive_reloads_lookup_id_lookups", {"lookup_id": 999_999}),
    ],
)
def test_the_table_refuses_inconsistent_rows(
    db_engine: Engine, constraint: str, values: dict[str, Any]
) -> None:
    with db_engine.connect() as connection, pytest.raises(IntegrityError, match=constraint):
        _insert(connection, **values)


def test_one_reload_loads_at_a_time_and_a_deleted_lookup_leaves_the_reload(
    db_engine: Engine,
) -> None:
    now = datetime.now(UTC)
    with db_engine.begin() as connection:
        lookup = connection.execute(
            text(
                "INSERT INTO lookups (username, authority, file_no, request_date, reason, query) "
                "VALUES ('tolga', 'Örnek Savcılık', '2026/41', CURRENT_DATE, 'örnek', '{}') "
                "RETURNING id"
            )
        ).scalar_one()
        first = _insert(connection, status="loading", lookup_id=lookup)
        _insert(connection, status="queued", reason="search")
        _insert(connection, status="ready", ready_at=now, expires_at=now + timedelta(days=7))
        _insert(connection, status="failed", error_code="archive_day_not_in_vault")
    with db_engine.connect() as connection, pytest.raises(IntegrityError, match="uq_archive"):
        _insert(connection, status="loading")
    with db_engine.begin() as connection:
        connection.execute(text("DELETE FROM lookups WHERE id = :id"), {"id": lookup})
    with Session(db_engine) as session:
        reload = session.get(ArchiveReload, first)
        assert reload is not None
        assert (reload.lookup_id, reload.status, reload.bytes_total, reload.bytes_done) == (
            None,
            "loading",
            0,
            0,
        )
        assert reload.requested_at is not None
        session.add(
            ArchiveReload(
                requested_by="tolga",
                reason="search",
                first_day=date(2026, 9, 1),
                last_day=date(2026, 9, 14),
            )
        )
        session.commit()
        queued = select(func.count()).where(ArchiveReload.status == "queued")
        assert session.scalar(queued) == 2


# -- partitions -----------------------------------------------------------------------------------


def _months(engine: Engine, table: str) -> list[date]:
    with engine.connect() as connection:
        return [p.month for p in list_partitions(connection, table) if p.month is not None]


def _daily(engine: Engine, table: str) -> list[date]:
    with engine.connect() as connection:
        return [p.day for p in list_partitions(connection, table) if p.day is not None]


def test_the_empty_months_ahead_are_dropped_and_daily_partitions_follow(
    empty_engine: Engine,
) -> None:
    """G2: an earlier version made monthly partitions three months ahead; the empty ones after
    the current month go, one holding rows (a device clock in the future) stays whole, and the
    daily partitions start where the current month ends (its last UTC day's three hours after
    local midnight, then the UTC days of the next month); down again, the daily partitions are
    the next month's monthly one, with their rows."""
    with empty_engine.begin() as connection:
        command.upgrade(alembic_config(connection, timescale="off"), BEFORE)
        before = month_of(site_day(utc_now()))
        ensure_partitions(connection, today=site_day(utc_now()), months_back=2, months_ahead=3)
    future = add_months(before, 2)
    with empty_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO mt_dhcp (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, server, action, ip, mac) VALUES (:at, :at, 'core-r1', "
                "'172.20.10.1', :day, 1, 0, 'dhcp60', 'assigned', '172.20.60.183', "
                "'00:00:5e:00:53:46')"
            ),
            {"at": day_window(future)[0] + timedelta(hours=12), "day": future},
        )
    _migrate(empty_engine, REVISION)
    after = month_of(site_day(utc_now()))  # a site midnight on a 1st may pass in between
    for table in TIME_SERIES_TABLES:
        months = _months(empty_engine, table)
        assert [add_months(before, n) for n in (-2, -1, 0)] == months[:3], table
        assert all(month <= after for month in months if month != future), table
        assert (future in months) is (table == "mt_dhcp"), table
    assert upgrade(empty_engine).after == (REVISION,)  # seed and the window's partitions
    for _ in range(5):
        with empty_engine.begin() as connection:
            if ensure_partitions(connection).remaining == 0:
                break
    next_month = add_months(after, 1)
    first_daily = next_month - timedelta(days=1)  # the UTC day the current month ends in
    for table in TIME_SERIES_TABLES:
        days = _daily(empty_engine, table)
        assert all(day >= first_daily for day in days), table
        if site_day(utc_now()) + timedelta(days=7) >= first_daily:
            assert days[0] == first_daily, table
        else:
            assert days == [], table  # the current month's partition covers the days ahead
    # The change-over slice, made now (the last day of the month as today).
    with empty_engine.begin() as connection:
        ensure_partitions(connection, today=first_daily)
        (slice_,) = [p for p in list_partitions(connection, "mt_dhcp") if p.day == first_daily]
    month_end = month_bounds(after)[1]
    assert (slice_.start, slice_.end) == (month_end, day_bounds(next_month)[0])
    with empty_engine.begin() as connection:
        _insert_dhcp(connection, month_end, 2)  # the next month's first instant
    assert _where(empty_engine, "mt_dhcp")[-1] == slice_.name
    _migrate(empty_engine, BEFORE)
    assert _daily(empty_engine, "mt_dhcp") == []
    assert next_month in _months(empty_engine, "mt_dhcp")
    assert _where(empty_engine, "mt_dhcp") == [
        partition_name("mt_dhcp", future),
        partition_name("mt_dhcp", next_month),
    ]


def _insert_dhcp(connection: Connection, when: datetime, line: int) -> None:
    connection.execute(
        text(
            "INSERT INTO mt_dhcp (time, time_received, source, sender, raw_day, raw_line, "
            "raw_offset, server, action, ip, mac) VALUES (:at, :at, 'core-r1', '172.20.10.1', "
            ":day, :line, 0, 'dhcp60', 'assigned', '172.20.60.183', '00:00:5e:00:53:46')"
        ),
        {"at": when, "day": site_day(when), "line": line},
    )


def _where(engine: Engine, table: str) -> list[str]:
    with engine.connect() as connection:
        sql = f"SELECT CAST(CAST(tableoid AS regclass) AS text) FROM {table} ORDER BY id"  # noqa: S608
        return list(connection.execute(text(sql)).scalars())


def _index_count(engine: Engine, table: str) -> int:
    return int(_scalar(engine, "SELECT count(*) FROM pg_indexes WHERE tablename = :t", t=table))


def test_down_merges_the_daily_partitions_into_monthly_ones(empty_engine: Engine) -> None:
    """The previous version knows monthly partitions only: the downgrade gives it one for every
    local month with daily partitions, the rows (and the default partition's rows of that
    month) in it — the rows of the UTC day a month ends in split between the two months —, the
    indexes of the parent on it; up again."""
    assert upgrade(empty_engine, timescale="off").after == (REVISION,)
    # The first run's days (today - 8 … today + 7) and the next month's 1st, not the whole
    # window: the downgrade holds a lock on every object of every daily partition until its
    # commit (test_the_downgrade_checks_its_locks_first).
    following = add_months(month_of(site_day(utc_now())), 1)
    with empty_engine.begin() as connection:
        ensure_days(connection, following, following)
    days = _daily(empty_engine, "mt_dhcp")
    first, last = days[0], days[-1]
    outside = first - timedelta(days=1)  # no partition: waits in the default partition
    # The local midnight of the last month's 1st lies in the UTC day before it: that daily
    # partition holds both months' rows.
    boundary = month_bounds(month_of(last))[0]
    at = [day_window(day)[0] + timedelta(hours=12) for day in (first, last, outside)]
    at += [boundary - timedelta(microseconds=1), boundary]
    with empty_engine.begin() as connection:
        for number, when in enumerate(at, 1):
            _insert_dhcp(connection, when, number)
    where = _where(empty_engine, "mt_dhcp")
    assert where[2] == "mt_dhcp_default"
    assert where[3] == where[4] == f"mt_dhcp_p{boundary.astimezone(UTC).date():%Y%m%d}"
    with empty_engine.begin() as connection:  # the window job's status row goes with it
        drop_before(connection, outside - timedelta(days=40))
        assert dropped_before(connection) is not None
    daily_indexes = _index_count(empty_engine, f"mt_dhcp_p{first:%Y%m%d}")
    months = sorted({month_of(site_day(start)) for start, _ in map(day_bounds, days)})
    _migrate(empty_engine, BEFORE)
    with empty_engine.connect() as connection:
        assert current_revisions(connection) == (BEFORE,)
    assert TABLE not in inspect(empty_engine).get_table_names()
    for table in TIME_SERIES_TABLES:
        assert _daily(empty_engine, table) == [], table
        assert _months(empty_engine, table) == months, table
    merged = month_of(outside) in months
    assert _where(empty_engine, "mt_dhcp") == [
        partition_name("mt_dhcp", month_of(first)),
        partition_name("mt_dhcp", month_of(last)),
        partition_name("mt_dhcp", month_of(outside)) if merged else "mt_dhcp_default",
        partition_name("mt_dhcp", month_of(site_day(boundary - timedelta(microseconds=1)))),
        partition_name("mt_dhcp", month_of(last)),
    ]
    assert _index_count(empty_engine, partition_name("mt_dhcp", months[0])) == daily_indexes
    assert _setting(empty_engine, "db.retention_days") == (400, ":seed")
    assert _setting(empty_engine, "db.reload_keep_days") is None
    assert _setting(empty_engine, DROPPED_KEY) is None
    assert upgrade(empty_engine, timescale="off").after == (REVISION,)  # and up again
    assert _setting(empty_engine, "db.retention_days") == (30, ":seed")
    assert len(_where(empty_engine, "mt_dhcp")) == 5


def test_down_merges_the_change_over_into_monthly_ones(empty_engine: Engine) -> None:
    """An installation upgraded with its monthly partitions, down again after a month and more
    of daily partitions (W5-G-12): the change-over slice after the last monthly partition (the
    three hours of its UTC day after local midnight) goes to the next month, a UTC day with a
    local month's midnight in it is split between the two months, and the month that kept its
    monthly partition gets none of the daily partitions' rows; every row stays, once; up again."""
    with empty_engine.begin() as connection:
        command.upgrade(alembic_config(connection, timescale="off"), BEFORE)
        ensure_partitions(connection, today=site_day(utc_now()), months_back=1)
    _migrate(empty_engine, REVISION)
    assert upgrade(empty_engine, timescale="off").after == (REVISION,)
    current = month_of(site_day(utc_now()))  # a site midnight on a 1st may pass in between
    following, after_that = add_months(current, 1), add_months(current, 2)
    # Only the daily partitions the rows need (the downgrade holds every lock until its commit):
    # the change-over slice, a day of the next month, the UTC day across its end, a day ahead.
    last = after_that - timedelta(days=1)
    for site in (following, following + timedelta(days=14), last, after_that + timedelta(days=2)):
        with empty_engine.begin() as connection:
            ensure_days(connection, site, site)
    month_end, slice_end = month_bounds(current)[1], day_bounds(following)[0]
    slice_name = day_partition_name("mt_dhcp", following - timedelta(days=1))
    with empty_engine.connect() as connection:
        partitions = {p.name: p for p in list_partitions(connection, "mt_dhcp")}
    assert (partitions[slice_name].start, partitions[slice_name].end) == (month_end, slice_end)
    across = month_bounds(after_that)[0]  # the local midnight inside the UTC day of ``last``
    assert day_bounds(last)[0] < across < day_bounds(last)[1]
    tiny = timedelta(microseconds=1)
    # Each row's moment and the monthly partition it belongs in after the downgrade.
    rows = {
        # the last UTC day of the monthly partition, before its end: it stays where it is
        month_end - tiny: partition_name("mt_dhcp", current),
        # the slice, its first and its last instant: the next month's
        month_end: partition_name("mt_dhcp", following),
        slice_end - tiny: partition_name("mt_dhcp", following),
        # a whole daily partition of the next month
        day_window(following + timedelta(days=14))[0] + timedelta(hours=12): partition_name(
            "mt_dhcp", following
        ),
        # the UTC day across the next month's end: split at the local midnight
        across - tiny: partition_name("mt_dhcp", following),
        across: partition_name("mt_dhcp", after_that),
        # a day ahead, in the month after
        day_window(after_that + timedelta(days=2))[0] + timedelta(hours=12): partition_name(
            "mt_dhcp", after_that
        ),
    }
    with empty_engine.begin() as connection:
        for number, when in enumerate(rows, 1):
            _insert_dhcp(connection, when, number)
    across_name = day_partition_name("mt_dhcp", last)
    assert _where(empty_engine, "mt_dhcp") == [
        partition_name("mt_dhcp", current),
        slice_name,
        slice_name,
        day_partition_name("mt_dhcp", following + timedelta(days=14)),
        across_name,
        across_name,
        day_partition_name("mt_dhcp", after_that + timedelta(days=2)),
    ]
    _migrate(empty_engine, BEFORE)
    for table in TIME_SERIES_TABLES:
        assert _daily(empty_engine, table) == [], table
        assert _months(empty_engine, table)[-3:] == [current, following, after_that], table
    with empty_engine.connect() as connection:
        bounds = {p.month: (p.start, p.end) for p in list_partitions(connection, "mt_dhcp")}
        times = connection.execute(text("SELECT time FROM mt_dhcp ORDER BY id")).scalars().all()
    assert bounds[following] == month_bounds(following)
    assert bounds[after_that] == month_bounds(after_that)
    assert _where(empty_engine, "mt_dhcp") == list(rows.values())
    assert times == list(rows)
    assert upgrade(empty_engine, timescale="off").after == (REVISION,)  # and up again
    assert _where(empty_engine, "mt_dhcp") == list(rows.values())


def test_the_downgrade_checks_its_locks_first(empty_engine: Engine) -> None:
    """The downgrade merges in one transaction and holds every lock until its commit: it first
    estimates them (:func:`locks_needed`, never fewer than it takes) and refuses, before it
    changes anything, when PostgreSQL's lock table has no room left for them
    (``Install-Arcilogger.ps1 -Rollback``, #122, runs it on an installation)."""
    with empty_engine.begin() as connection:  # the revision and a few daily partitions only
        command.upgrade(alembic_config(connection, timescale="off"), REVISION)
        today = site_day(utc_now())
        ensure_days(connection, today, today + timedelta(days=2))
    with empty_engine.connect() as connection:
        needed = MIGRATION.locks_needed(connection)
        with pytest.raises(RuntimeError, match=r"geri alınamadı: .* was not downgraded: "):
            MIGRATION.check_lock_table(connection, 10**9)
        MIGRATION.check_lock_table(connection, needed)
    assert _daily(empty_engine, "messages")
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
        held = connection.execute(text(HELD_LOCKS)).scalar_one()
    assert _daily(empty_engine, "messages") == []
    assert held <= needed < 2 * held
    with empty_engine.connect() as connection:
        assert MIGRATION.locks_needed(connection) == 0  # nothing left to merge


def test_up_and_down_on_timescaledb(empty_engine: Engine) -> None:
    """The setting and the table on TimescaleDB; the hypertables are not touched. On the Linux
    CI job (``ARCILOGGER_EXPECT_TIMESCALE=1``) it fails instead of skipping."""
    if not _timescale(empty_engine):
        if os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1":
            pytest.fail("TimescaleDB expected but not installed")
        pytest.skip("TimescaleDB is not installed (the Linux CI job runs this)")
    upgrade(empty_engine, revision=BEFORE, timescale="require")
    _as_before(empty_engine, 400, ":seed")
    hypertables = "SELECT count(*) FROM timescaledb_information.hypertables"
    count = _scalar(empty_engine, hypertables)
    assert count == len(TIME_SERIES_TABLES)
    _migrate(empty_engine, REVISION)
    assert _setting(empty_engine, "db.retention_days") == (30, ":seed")
    change = {"old": 400, "new": 30, "was_set_by": ":seed", "migration": REVISION, "audited": False}
    assert _setting(empty_engine, CHANGE_KEY) == (change, ":migration")
    assert TABLE in inspect(empty_engine).get_table_names()
    assert _scalar(empty_engine, hypertables) == count
    assert _scalar(empty_engine, "SELECT count(*) FROM pg_partitioned_table") == 0
    _migrate(empty_engine, BEFORE)
    assert TABLE not in inspect(empty_engine).get_table_names()
    assert _setting(empty_engine, "db.retention_days") == (400, ":seed")
    assert _setting(empty_engine, CHANGE_KEY) is None
    assert upgrade(empty_engine).after == (REVISION,)
