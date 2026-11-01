"""Revision ``0019_source_clock_samples`` (issue #73, ADR-0016 D28): up and down on native
partitions and on TimescaleDB, the table's key and check, empty after the upgrade (lines loaded
before it have no samples), the rows of ``messages`` kept by the downgrade."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from arcilogger_db.clocksamples import ClockKey, received_between, upsert_samples
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, upgrade
from arcilogger_db.testing import TestServer

BEFORE = "0018_nat_egresses"
REVISION = "0019_source_clock_samples"
TABLE = "source_clock_samples"


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


def _old_messages(engine: Engine) -> datetime:
    """Two lines loaded before the upgrade, one with a device time 3 hours behind."""
    at = (datetime.now(UTC) - timedelta(days=1)).replace(microsecond=0)
    with engine.begin() as connection:
        for line, offset in enumerate((timedelta(0), -timedelta(hours=3)), 1):
            connection.execute(
                text(
                    "INSERT INTO messages (time, time_received, source, sender, raw_day, "
                    "raw_line, raw_offset, type, text) VALUES (:time, :received, 'fortigate', "
                    "'172.31.255.1', :day, :line, 0, 'traffic', '')"
                ),
                {"time": at + offset, "received": at, "day": at.date(), "line": line},
            )
    return at


def _check_upgraded(engine: Engine, at: datetime) -> None:
    inspector = inspect(engine)
    assert TABLE in inspector.get_table_names()
    assert inspector.get_pk_constraint(TABLE)["constrained_columns"] == [
        "source",
        "minute",
        "seconds",
    ]
    assert _scalar(engine, f"SELECT count(*) FROM {TABLE}") == 0  # noqa: S608 - fixed name
    minute = at.replace(second=0)
    with engine.begin() as connection:
        key = ClockKey("fortigate", minute, -10800)
        upsert_samples(connection, {key: 2})
        upsert_samples(connection, {key: 3})  # adds to the row
        assert received_between(connection, "fortigate", minute, minute) == [(-10800, 5)]
    with pytest.raises(IntegrityError), engine.begin() as connection:
        connection.execute(
            text(
                f"INSERT INTO {TABLE} (source, minute, seconds, n) "  # noqa: S608 - fixed name
                "VALUES ('fortigate', now(), 0, 0)"
            )
        )
    if _scalar(engine, "SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'"):
        hypertable = _scalar(
            engine,
            "SELECT count(*) FROM timescaledb_information.hypertables WHERE hypertable_name = :t",
            t=TABLE,
        )
        assert hypertable == 0  # a plain table, as search_counts


def _check_downgraded(engine: Engine) -> None:
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    assert TABLE not in inspect(engine).get_table_names()
    assert _scalar(engine, "SELECT count(*) FROM messages") == 2  # the rows stay
    assert upgrade(engine, revision=REVISION).after == (REVISION,)  # and up again


def test_up_and_down_on_native_partitions(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    assert TABLE not in inspect(empty_engine).get_table_names()
    at = _old_messages(empty_engine)
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    _check_upgraded(empty_engine, at)
    _check_downgraded(empty_engine)


def test_up_and_down_on_timescaledb(empty_engine: Engine) -> None:
    """A plain table on TimescaleDB too (no hypertable): on the Linux CI job
    (``ARCILOGGER_EXPECT_TIMESCALE=1``) it fails instead of skipping."""
    if not _timescale(empty_engine):
        if os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1":
            pytest.fail("TimescaleDB expected but not installed")
        pytest.skip("TimescaleDB is not installed (the Linux CI job runs this)")
    upgrade(empty_engine, revision=BEFORE, timescale="require")
    at = _old_messages(empty_engine)
    assert upgrade(empty_engine, revision=REVISION).after == (REVISION,)
    _check_upgraded(empty_engine, at)
    _check_downgraded(empty_engine)
