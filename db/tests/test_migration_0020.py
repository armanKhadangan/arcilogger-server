"""Revision ``0020_fg_traffic_transip_time`` (issue #71): up and down on native partitions and on
TimescaleDB with a compressed ``fg_traffic`` chunk in place; the index on every partition or
chunk that holds rows, a port-less probe step on it, the rows kept by the downgrade."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, text
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, upgrade
from arcilogger_db.testing import TestServer, pause_compression_jobs

BEFORE = "0019_source_clock_samples"
REVISION = "0020_fg_traffic_transip_time"
INDEX = "ix_fg_traffic_transip_time"
#: A port-less probe step as ``_Probe.seen_overlap`` asks it.
STEP = (
    "SELECT time FROM fg_traffic WHERE transip = '100.64.12.9' AND time >= :low "
    "AND time <= :high ORDER BY transip, time LIMIT 1"
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


def _old_rows(engine: Engine, days_ago: int) -> datetime:
    """Two sessions through the site's NAT address, written before the upgrade."""
    at = (datetime.now(UTC) - timedelta(days=days_ago)).replace(microsecond=0)
    with engine.begin() as connection:
        for number in (1, 2):
            connection.execute(
                text(
                    "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, "
                    "raw_line, raw_offset, srcip, transip, transport, duration) VALUES (:time, "
                    ":time, 'fortigate', '172.31.255.1', :day, :line, 0, '172.20.42.57', "
                    "'100.64.12.9', :port, 30)"
                ),
                {"time": at + timedelta(seconds=number), "day": at.date(), "line": number,
                 "port": 40000 + number},
            )  # fmt: skip
    return at


def _indexed_tables(engine: Engine) -> int:
    """The partitions or chunks of ``fg_traffic`` that hold the index."""
    return int(
        _scalar(
            engine,
            "SELECT count(*) FROM pg_index i JOIN pg_inherits h ON h.inhrelid = i.indrelid "
            "WHERE h.inhparent = CAST('fg_traffic' AS regclass) "
            "AND CAST(CAST(i.indexrelid AS regclass) AS text) LIKE '%transip_time%'",
        )
    )


def _found(engine: Engine, at: datetime) -> list[datetime]:
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(STEP), {"low": at - timedelta(minutes=1), "high": at + timedelta(minutes=1)}
            ).scalars()
        )


def _check_upgraded(engine: Engine, at: datetime) -> None:
    definition = _scalar(
        engine, "SELECT indexdef FROM pg_indexes WHERE indexname = :name", name=INDEX
    )
    assert definition is not None
    assert '(transip, "time")' in definition
    assert _found(engine, at) == [at + timedelta(seconds=1)]


def _check_downgraded(engine: Engine, at: datetime) -> None:
    with engine.begin() as connection:
        command.downgrade(alembic_config(connection), BEFORE)
    assert _scalar(engine, "SELECT count(*) FROM pg_indexes WHERE indexname = :n", n=INDEX) == 0
    assert _indexed_tables(engine) == 0
    assert _scalar(engine, "SELECT count(*) FROM fg_traffic") == 2  # the rows stay
    assert _found(engine, at) == [at + timedelta(seconds=1)]
    assert upgrade(engine, revision=REVISION).after == (REVISION,)  # and up again


def test_up_and_down_on_native_partitions(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    at = _old_rows(empty_engine, 20)
    assert _indexed_tables(empty_engine) == 0
    assert upgrade(empty_engine, revision=REVISION, timescale="off").after == (REVISION,)
    _check_upgraded(empty_engine, at)
    partitions = _scalar(
        empty_engine,
        "SELECT count(*) FROM pg_inherits WHERE inhparent = CAST('fg_traffic' AS regclass)",
    )
    assert _indexed_tables(empty_engine) == partitions  # every partition, the default one too
    _check_downgraded(empty_engine, at)


def test_up_and_down_on_timescaledb_with_a_compressed_chunk(empty_engine: Engine) -> None:
    """The index is created with a compressed chunk in place, which stays compressed and is
    read as before. On the Linux CI job (``ARCILOGGER_EXPECT_TIMESCALE=1``) it fails instead of
    skipping."""
    expected = os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1"
    available = _scalar(
        empty_engine, "SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'"
    )
    if available is None:
        if expected:
            pytest.fail("TimescaleDB expected but not installed")
        pytest.skip("TimescaleDB is not installed (the Linux CI job runs this)")
    upgrade(empty_engine, revision=BEFORE, timescale="require")
    status = _scalar(empty_engine, "SELECT value FROM settings WHERE key = 'db.timescale'")
    if not status.get("compression"):
        if expected:
            pytest.fail(f"TimescaleDB expected with compression: {status}")
        pytest.skip(f"no compression in this TimescaleDB edition: {status}")
    pause_compression_jobs(empty_engine)
    at = _old_rows(empty_engine, 30)
    with empty_engine.begin() as connection:
        compressed = connection.execute(
            text(
                "SELECT count(compress_chunk(chunk, if_not_compressed => true)) "
                "FROM show_chunks('fg_traffic', older_than => INTERVAL '7 days') AS chunk"
            )
        ).scalar_one()
    assert compressed >= 1
    assert upgrade(empty_engine, revision=REVISION).after == (REVISION,)
    _check_upgraded(empty_engine, at)
    still = _scalar(
        empty_engine,
        "SELECT count(*) FROM timescaledb_information.chunks "
        "WHERE hypertable_name = 'fg_traffic' AND is_compressed",
    )
    assert still == compressed  # the index was created with the chunk still compressed
    with empty_engine.begin() as connection:  # a new row lands in a chunk with the index
        connection.execute(
            text(
                "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, transip, transport) VALUES (now(), now(), 'fortigate', "
                "'172.31.255.1', current_date, 3, 0, '100.64.12.9', 40003)"
            )
        )
    assert _indexed_tables(empty_engine) >= 1
    with empty_engine.begin() as connection:
        connection.execute(text("DELETE FROM fg_traffic WHERE raw_line = 3"))
    _check_downgraded(empty_engine, at)
