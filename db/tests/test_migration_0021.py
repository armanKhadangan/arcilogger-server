"""Revision ``0021_db_window`` (Wave 5 contract G1, G2, G4; issue #110): the stored
``db.retention_days`` converted (400 → 30, below 7 → 7, other values kept), both keys after an
upgrade, the table ``archive_reloads`` and its checks, the empty monthly partitions ahead dropped
(daily partitions from the next month on), and the downgrade that merges daily partitions back
into monthly ones; on native partitions and on TimescaleDB."""

from __future__ import annotations

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
    drop_before,
    dropped_before,
    ensure_partitions,
    list_partitions,
    month_of,
    partition_name,
)
from arcilogger_db.testing import TestServer

BEFORE = "0020_fg_traffic_transip_time"
REVISION = "0021_db_window"
TABLE = "archive_reloads"
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
    """G1: 400 → 30, below 7 → 7, every other value kept; the downgrade takes back only the
    30 it or the seed wrote."""
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    _as_before(empty_engine, value, author)
    _migrate(empty_engine, REVISION)
    assert _setting(empty_engine, "db.retention_days") == converted
    assert _setting(empty_engine, "db.reload_keep_days") is None  # the seed inserts it
    result = upgrade(empty_engine)
    assert _setting(empty_engine, "db.retention_days") == converted
    assert _setting(empty_engine, "db.reload_keep_days") == (7, ":seed")
    assert result.seed.settings_inserted == ["db.reload_keep_days"]
    assert "db.retention_days" not in result.seed.settings_refreshed
    _migrate(empty_engine, BEFORE)
    back = (400, converted[1]) if converted[0] == 30 else converted
    assert _setting(empty_engine, "db.retention_days") == back
    assert _setting(empty_engine, "db.reload_keep_days") is None


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
    daily partitions start on the 1st of the next month."""
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
    first_daily = add_months(after, 1)
    for table in TIME_SERIES_TABLES:
        days = _daily(empty_engine, table)
        assert all(day >= first_daily for day in days), table
        if site_day(utc_now()) + timedelta(days=7) >= first_daily:
            assert days[0] == first_daily, table
        else:
            assert days == [], table  # the current month's partition covers the days ahead


def _where(engine: Engine, table: str) -> list[str]:
    with engine.connect() as connection:
        sql = f"SELECT CAST(CAST(tableoid AS regclass) AS text) FROM {table} ORDER BY id"  # noqa: S608
        return list(connection.execute(text(sql)).scalars())


def _index_count(engine: Engine, table: str) -> int:
    return int(_scalar(engine, "SELECT count(*) FROM pg_indexes WHERE tablename = :t", t=table))


def test_down_merges_the_daily_partitions_into_monthly_ones(empty_engine: Engine) -> None:
    """The previous version knows monthly partitions only: the downgrade gives it one for every
    month with daily partitions, the rows (and the default partition's rows of that month) in
    it, the indexes of the parent on it; up again."""
    assert upgrade(empty_engine, timescale="off").after == (REVISION,)
    for _ in range(5):
        with empty_engine.begin() as connection:
            if ensure_partitions(connection).remaining == 0:
                break
    days = _daily(empty_engine, "mt_dhcp")
    first, last = days[0], days[-1]
    outside = first - timedelta(days=1)  # no partition: waits in the default partition
    at = [day_window(day)[0] + timedelta(hours=12) for day in (first, last, outside)]
    with empty_engine.begin() as connection:
        for number, when in enumerate(at, 1):
            connection.execute(
                text(
                    "INSERT INTO mt_dhcp (time, time_received, source, sender, raw_day, "
                    "raw_line, raw_offset, server, action, ip, mac) VALUES (:at, :at, 'core-r1', "
                    "'172.20.10.1', :day, :line, 0, 'dhcp60', 'assigned', '172.20.60.183', "
                    "'00:00:5e:00:53:46')"
                ),
                {"at": when, "day": site_day(when), "line": number},
            )
    assert _where(empty_engine, "mt_dhcp")[2] == "mt_dhcp_default"
    with empty_engine.begin() as connection:  # the window job's status row goes with it
        drop_before(connection, outside - timedelta(days=40))
        assert dropped_before(connection) is not None
    daily_indexes = _index_count(empty_engine, f"mt_dhcp_p{first:%Y%m%d}")
    months = sorted({month_of(day) for day in days})
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
    ]
    assert _index_count(empty_engine, partition_name("mt_dhcp", months[0])) == daily_indexes
    assert _setting(empty_engine, "db.retention_days") == (400, ":seed")
    assert _setting(empty_engine, "db.reload_keep_days") is None
    assert _setting(empty_engine, DROPPED_KEY) is None
    assert upgrade(empty_engine, timescale="off").after == (REVISION,)  # and up again
    assert _setting(empty_engine, "db.retention_days") == (30, ":seed")
    assert len(_where(empty_engine, "mt_dhcp")) == 3


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
    assert TABLE in inspect(empty_engine).get_table_names()
    assert _scalar(empty_engine, hypertables) == count
    assert _scalar(empty_engine, "SELECT count(*) FROM pg_partitioned_table") == 0
    _migrate(empty_engine, BEFORE)
    assert TABLE not in inspect(empty_engine).get_table_names()
    assert _setting(empty_engine, "db.retention_days") == (400, ":seed")
    assert upgrade(empty_engine).after == (REVISION,)
