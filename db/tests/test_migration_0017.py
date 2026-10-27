"""Revision ``0017_fortigate_dhcp`` (Wave 3 contract 2.2.1, 3.2): up and down on native
partitions and on TimescaleDB with a compressed ``fg_traffic`` chunk (``ADD COLUMN srcmac``,
V13), the storage of ``fg_dhcp`` and its place in the ``db.timescale`` status, the two lease
checks, the lease index, and the downgrade's deletion of the FortiGate leases."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from arcilogger_core.timeutil import site_day
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, upgrade
from arcilogger_db.models import TIME_SERIES_TABLES, Base
from arcilogger_db.partitions import (
    include_name,
    list_partitions,
    month_of,
    partition_name,
    partitioned_tables,
)
from arcilogger_db.testing import TestServer, pause_compression_jobs

BEFORE = "0016_search"
REVISION = "0017_fortigate_dhcp"
TABLE = "fg_dhcp"
FG_DHCP_COLUMNS = (
    "id",
    "time",
    "time_received",
    "source",
    "sender",
    "logid",
    "subtype",
    "level",
    "logdesc",
    "dhcp_msg",
    "interface",
    "ip",
    "mac",
    "hostname",
    "lease",
    "msg",
    "device_time",
    "extra",
    "raw_day",
    "raw_line",
    "raw_offset",
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


def _settings(engine: Engine) -> dict[str, tuple[Any, str]]:
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT key, value, updated_by FROM settings")).all()
    return {key: (value, author) for key, value, author in rows}


def _migrate(engine: Engine, revision: str) -> None:
    """Only the migration (no seed, no partitions), so its own writes can be compared."""
    with engine.begin() as connection:
        if revision == BEFORE:
            command.downgrade(alembic_config(connection), revision)
        else:
            command.upgrade(alembic_config(connection), revision)


def _old_data(engine: Engine, days_ago: int) -> datetime:
    """Rows written before the upgrade: two traffic rows ``days_ago`` and two leases."""
    at = (datetime.now(UTC) - timedelta(days=days_ago)).replace(microsecond=0)
    with engine.begin() as connection:
        for number in (1, 2):
            connection.execute(
                text(
                    "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, "
                    "raw_line, raw_offset, srcip, srcintf, transip, transport) VALUES (:time, "
                    ":time, 'fortigate', '172.31.255.1', :day, :line, :offset, :srcip, "
                    "'vlan42', '100.64.12.9', :port)"
                ),
                {
                    "time": at + timedelta(seconds=number),
                    "day": at.date(),
                    "line": number,
                    "offset": 400 * number,
                    "srcip": f"172.20.42.{56 + number}",
                    "port": 51000 + number,
                },
            )
        connection.execute(
            text(
                'INSERT INTO leases (ip, mac, vlan, server, start, "end", end_reason, '
                "confidence, source_table, last_evidence) VALUES "
                "('172.20.42.57', '00:00:5e:00:53:57', 42, 'dhcp42', :start, NULL, 'open', "
                "'high', 'mt_dhcp', :start), "
                "('172.20.10.23', '00:00:5e:00:53:43', 10, NULL, :start, :end, 'arp', "
                "'medium', 'mt_arp', :start)"
            ),
            {"start": at, "end": at + timedelta(hours=1)},
        )
    return at


def _fortigate_lease(engine: Engine, at: datetime) -> None:
    """A FortiGate DHCP row and the lease built from it (what WP-B's timeline writes)."""
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO fg_dhcp (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, logid, subtype, level, logdesc, dhcp_msg, interface, ip, mac, "
                "hostname, lease, msg, device_time) VALUES (:time, :time, 'fortigate', "
                "'172.31.255.1', :day, 9, 3600, '0100026001', 'system', 'information', "
                "'DHCP Ack log', 'Ack', 'vlan42', '172.20.42.58', '00:00:5e:00:53:58', "
                "'PLN-PC15', 86400, 'DHCP server sends a DHCPACK', true)"
            ),
            {"time": at, "day": at.date()},
        )
        connection.execute(
            text(
                "INSERT INTO leases (ip, mac, vlan, server, start, end_reason, confidence, "
                "source_table, last_evidence, lease_seconds) VALUES ('172.20.42.58', "
                "'00:00:5e:00:53:58', 42, 'vlan42', :at, 'open', 'high', 'fg_dhcp', :at, 86400)"
            ),
            {"at": at},
        )


def _refused(engine: Engine, constraint: str, source_table: str, seconds: int | None) -> None:
    with engine.connect() as connection, pytest.raises(IntegrityError, match=constraint):
        connection.execute(
            text(
                "INSERT INTO leases (ip, mac, start, confidence, source_table, lease_seconds) "
                "VALUES ('172.20.42.59', '00:00:5e:00:53:59', now(), 'low', :tbl, :seconds)"
            ),
            {"tbl": source_table, "seconds": seconds},
        )


def _check_upgraded(engine: Engine, storage: str) -> None:
    inspector = inspect(engine)
    columns = {c["name"]: c for c in inspector.get_columns(TABLE)}
    assert tuple(columns) == FG_DHCP_COLUMNS
    for name in ("ip", "mac", "device_time", "time", "raw_line"):
        assert columns[name]["nullable"] is False, name
    assert str(columns["mac"]["type"]) == "MACADDR"
    assert str(columns["lease"]["type"]) == "BIGINT"
    assert {
        index["name"]: tuple(index["column_names"]) for index in inspector.get_indexes(TABLE)
    } == {"ix_fg_dhcp_ip_time": ("ip", "time"), "ix_fg_dhcp_mac_time": ("mac", "time")}
    srcmac = {c["name"]: c for c in inspector.get_columns("fg_traffic")}["srcmac"]
    assert str(srcmac["type"]) == "MACADDR"
    assert srcmac["nullable"] is True
    assert srcmac["default"] is None
    assert not any("srcmac" in i["column_names"] for i in inspector.get_indexes("fg_traffic"))
    lease_seconds = {c["name"]: c for c in inspector.get_columns("leases")}["lease_seconds"]
    assert str(lease_seconds["type"]) == "INTEGER"
    assert ("source_table", "end") in {
        tuple(index["column_names"]) for index in inspector.get_indexes("leases")
    }
    checks = {check["name"] for check in inspector.get_check_constraints("leases")}
    assert {"ck_leases_source_table", "ck_leases_lease_seconds"} <= checks
    assert (
        _scalar(engine, "SELECT obj_description(CAST('fg_dhcp' AS regclass), 'pg_class')")
        == Base.metadata.tables[TABLE].comment
    )
    status = _scalar(engine, "SELECT value FROM settings WHERE key = 'db.timescale'")
    assert status[storage] == list(TIME_SERIES_TABLES)  # fg_dhcp appended, once
    assert status[storage][-1] == TABLE


def _check_rules(engine: Engine, at: datetime) -> None:
    """The two checks of ``leases`` and a FortiGate lease next to the old ones."""
    _refused(engine, "ck_leases_source_table", "x", None)
    _refused(engine, "ck_leases_source_table", "fg_event", None)
    _refused(engine, "ck_leases_lease_seconds", "fg_dhcp", 10)
    _refused(engine, "ck_leases_lease_seconds", "fg_dhcp", 299)
    _refused(engine, "ck_leases_lease_seconds", "fg_dhcp", 8_640_001)
    with engine.begin() as connection:
        for seconds in (300, 8_640_000, None):
            connection.execute(
                text(
                    'INSERT INTO leases (ip, mac, start, "end", end_reason, confidence, '
                    "source_table, lease_seconds) VALUES ('172.20.42.60', '00:00:5e:00:53:60', "
                    ":at, :at, 'timeout', 'high', 'fg_dhcp', :seconds)"
                ),
                {"at": at, "seconds": seconds},
            )
    _fortigate_lease(engine, at)
    with engine.connect() as connection:
        connection.execute(text("SET enable_seqscan = off"))
        plan = "\n".join(
            connection.execute(
                text(
                    "EXPLAIN SELECT id FROM leases WHERE source_table = 'fg_dhcp' "
                    'AND ("end" IS NULL OR "end" > now() - INTERVAL \'1 day\')'
                )
            ).scalars()
        )
    assert "ix_leases_source_table_end" in plan


def _check_downgraded(engine: Engine, before: dict[str, tuple[Any, str]], traffic: int) -> None:
    _migrate(engine, BEFORE)
    inspector = inspect(engine)
    assert TABLE not in inspector.get_table_names()
    assert "srcmac" not in {c["name"] for c in inspector.get_columns("fg_traffic")}
    assert "lease_seconds" not in {c["name"] for c in inspector.get_columns("leases")}
    assert ("source_table", "end") not in {
        tuple(index["column_names"]) for index in inspector.get_indexes("leases")
    }
    checks = {check["name"] for check in inspector.get_check_constraints("leases")}
    assert not {"ck_leases_source_table", "ck_leases_lease_seconds"} & checks
    with engine.connect() as connection:
        leases = connection.execute(
            text("SELECT source_table, count(*) FROM leases GROUP BY source_table")
        ).all()
    assert dict(leases) == {"mt_dhcp": 1, "mt_arp": 1}  # only the FortiGate leases deleted
    assert _scalar(engine, "SELECT count(*) FROM fg_traffic") == traffic  # every row stays
    # the downgrade writes back the status it found, without fg_dhcp
    after = _settings(engine)
    assert after.keys() == before.keys()
    assert {k: v for k, v in after.items() if k != "db.timescale"} == {
        k: v for k, v in before.items() if k != "db.timescale"
    }
    assert after["db.timescale"] == before["db.timescale"]
    assert upgrade(engine).after == (REVISION,)  # and up again
    status = _scalar(engine, "SELECT value FROM settings WHERE key = 'db.timescale'")
    assert TABLE in status["hypertables"] + status["partitioned"]
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_name": include_name}
        )
        assert compare_metadata(context, Base.metadata) == []


def _upgrade_alone(engine: Engine) -> dict[str, tuple[Any, str]]:
    """Run v0017 alone; it writes no settings row but the ``db.timescale`` status update."""
    before = _settings(engine)
    _migrate(engine, REVISION)
    after = _settings(engine)
    assert after.keys() == before.keys()  # no row added
    changed = {key for key in before if after[key] != before[key]}
    assert changed == {"db.timescale"}
    status, author = after["db.timescale"]
    old, _ = before["db.timescale"]
    assert author == ":migration"
    assert {k: v for k, v in status.items() if k not in ("hypertables", "partitioned")} == {
        k: v for k, v in old.items() if k not in ("hypertables", "partitioned")
    }
    return before


def test_up_and_down_on_native_partitions(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision=BEFORE, timescale="off")
    assert TABLE not in inspect(empty_engine).get_table_names()
    at = _old_data(empty_engine, 20)
    before = _upgrade_alone(empty_engine)
    assert upgrade(empty_engine, timescale="off").after == (REVISION,)  # seed and partitions
    _check_upgraded(empty_engine, "partitioned")
    status = _scalar(empty_engine, "SELECT value FROM settings WHERE key = 'db.timescale'")
    assert status["hypertables"] == []
    with empty_engine.connect() as connection:
        assert partitioned_tables(connection) == list(TIME_SERIES_TABLES)
        partitions = list_partitions(connection, TABLE)
        reference = list_partitions(connection, "mt_dhcp")
    assert partitions[-1].is_default
    assert partitions[-1].name == "fg_dhcp_default"
    months = [partition.month for partition in partitions if partition.month is not None]
    assert months == [partition.month for partition in reference if partition.month is not None]
    assert _scalar(empty_engine, "SELECT count(*) FROM fg_traffic WHERE srcmac IS NULL") == 2
    _check_rules(empty_engine, at)
    holder = _scalar(empty_engine, "SELECT CAST(CAST(tableoid AS regclass) AS text) FROM fg_dhcp")
    assert holder == partition_name(TABLE, month_of(site_day(at)))  # not the default partition
    _check_downgraded(empty_engine, before, traffic=2)


def test_up_and_down_on_timescaledb_with_a_compressed_chunk(empty_engine: Engine) -> None:
    """V13: ``ALTER TABLE fg_traffic ADD COLUMN srcmac`` with a compressed chunk in place. On
    the Linux CI job (``ARCILOGGER_EXPECT_TIMESCALE=1``) it fails instead of skipping."""
    expected = os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1"
    if not _timescale(empty_engine):
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
    old = _old_data(empty_engine, 30)
    with empty_engine.begin() as connection:
        compressed = connection.execute(
            text(
                "SELECT count(compress_chunk(chunk, if_not_compressed => true)) "
                "FROM show_chunks('fg_traffic', older_than => INTERVAL '7 days') AS chunk"
            )
        ).scalar_one()
    assert compressed >= 1
    before = _upgrade_alone(empty_engine)
    assert upgrade(empty_engine).after == (REVISION,)
    _check_upgraded(empty_engine, "hypertables")
    status = _scalar(empty_engine, "SELECT value FROM settings WHERE key = 'db.timescale'")
    assert status["partitioned"] == []
    assert "compression_reason" not in status
    still = _scalar(
        empty_engine,
        "SELECT count(*) FROM timescaledb_information.chunks "
        "WHERE hypertable_name = 'fg_traffic' AND is_compressed",
    )
    assert still == compressed  # the column was added with the chunk still compressed
    with empty_engine.begin() as connection:  # a new row, and a late one into the old chunk
        for number, time in ((3, datetime.now(UTC)), (4, old + timedelta(seconds=3))):
            connection.execute(
                text(
                    "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, "
                    "raw_line, raw_offset, srcip, srcintf, srcmac) VALUES (:time, now(), "
                    "'fortigate', '172.31.255.1', current_date, :line, 0, '172.20.42.57', "
                    "'vlan42', '00:00:5E:00:53:57')"
                ),
                {"time": time, "line": number},
            )
    with empty_engine.connect() as connection:
        found = connection.execute(
            text("SELECT raw_line, srcmac FROM fg_traffic ORDER BY raw_line")
        ).all()
    assert [tuple(row) for row in found] == [
        (1, None),  # read from the compressed chunk
        (2, None),
        (3, "00:00:5e:00:53:57"),
        (4, "00:00:5e:00:53:57"),
    ]
    with empty_engine.connect() as connection:
        hypertable = connection.execute(
            text(
                "SELECT compression_enabled FROM timescaledb_information.hypertables "
                "WHERE hypertable_name = 'fg_dhcp'"
            )
        ).scalar_one()
        interval = connection.execute(
            text(
                "SELECT time_interval FROM timescaledb_information.dimensions "
                "WHERE hypertable_name = 'fg_dhcp' AND column_name = 'time'"
            )
        ).scalar_one()
        policies = (
            connection.execute(
                text(
                    "SELECT config FROM timescaledb_information.jobs "
                    "WHERE proc_name = 'policy_compression' AND hypertable_name = 'fg_dhcp'"
                )
            )
            .scalars()
            .all()
        )
        segmentby = connection.execute(
            text(
                "SELECT attname, segmentby_column_index, orderby_column_index, "
                "orderby_asc FROM timescaledb_information.compression_settings "
                "WHERE hypertable_name = 'fg_dhcp' ORDER BY attname"
            )
        ).all()
    assert hypertable is True
    assert interval == timedelta(days=1)
    assert [policy["compress_after"] for policy in policies] == ["7 days"]
    assert [tuple(row) for row in segmentby] == [
        ("id", None, 2, False),
        ("source", 1, None, None),
        ("time", None, 1, False),
    ]
    _check_rules(empty_engine, old)
    _check_downgraded(empty_engine, before, traffic=4)
    jobs = _scalar(
        empty_engine,
        "SELECT count(*) FROM timescaledb_information.jobs WHERE hypertable_name = 'fg_dhcp'",
    )
    assert jobs == 1  # the re-upgrade's policy only: the downgrade removed the first one
