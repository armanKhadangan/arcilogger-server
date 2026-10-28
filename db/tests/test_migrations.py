"""Migrations: baseline on an empty database, model/migration sync, TimescaleDB mode, guards."""

from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from typing import Any

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError, InternalError, ProgrammingError
from sqlalchemy.pool import NullPool

from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import (
    alembic_config,
    current_revisions,
    head_revisions,
    migration_status,
    upgrade,
)
from arcilogger_db.models import TIME_SERIES_TABLES, Base, Department, Source, Vlan
from arcilogger_db.partitions import include_name
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.testing import TestServer

BASELINE = "0001_baseline"
#: Newest revision (re-chained by the orchestrator when parallel revisions are merged).
HEAD = "0018_nat_egresses"


@pytest.fixture
def empty_engine(pg_server: TestServer) -> Iterator[Engine]:
    """A brand-new database that was never migrated."""
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(name)


def _timescale_available(engine: Engine) -> bool:
    with engine.connect() as connection:
        return (
            connection.execute(
                text("SELECT 1 FROM pg_available_extensions WHERE name = 'timescaledb'")
            ).scalar()
            is not None
        )


def _timescale_status(engine: Engine) -> dict[str, Any]:
    with engine.connect() as connection:
        value = connection.execute(
            text("SELECT value FROM settings WHERE key = 'db.timescale'")
        ).scalar_one()
    assert isinstance(value, dict)
    return value


def test_history_is_linear_with_one_head() -> None:
    # Parallel branches must be re-chained before merging: one head, no forks.
    assert head_revisions() == (HEAD,)


def test_upgrade_from_empty_database_then_idempotent(empty_engine: Engine) -> None:
    assert migration_status(empty_engine).empty
    first = upgrade(empty_engine)
    assert first.before == ()
    assert first.after == (HEAD,)
    assert sorted(first.seed.settings_inserted) == sorted(DEFAULT_SETTINGS)
    with empty_engine.connect() as connection:  # no shipped network (VLANs, departments)
        for model in (Vlan, Department, Source):
            assert connection.execute(select(func.count()).select_from(model)).scalar() == 0
    second = upgrade(empty_engine)
    assert second.before == second.after == (HEAD,)
    assert not second.seed.changed
    status = migration_status(empty_engine)
    assert status.up_to_date
    assert not status.empty


def test_models_and_migrations_are_in_sync(db_engine: Engine) -> None:
    """A model change without a migration (or the reverse) fails here."""
    with db_engine.connect() as connection:
        context = MigrationContext.configure(
            connection, opts={"compare_type": True, "include_name": include_name}
        )
        differences = compare_metadata(context, Base.metadata)
    assert differences == []


def test_time_series_tables_have_time_in_every_unique_index(db_engine: Engine) -> None:
    inspector = inspect(db_engine)
    for table in TIME_SERIES_TABLES:
        primary_key = inspector.get_pk_constraint(table)
        assert primary_key["constrained_columns"] == ["time", "id"], table
        columns = {column["name"]: column for column in inspector.get_columns(table)}
        for name in ("time", "time_received", "source", "sender", "raw_day", "raw_line"):
            assert columns[name]["nullable"] is False, (table, name)
        assert str(columns["raw_offset"]["type"]) == "BIGINT"
        for index in inspector.get_indexes(table):
            if index["unique"]:
                assert "time" in index["column_names"], (table, index)
        for unique in inspector.get_unique_constraints(table):
            assert "time" in unique["column_names"], (table, unique)


def test_spec_indexes_exist(db_engine: Engine) -> None:
    inspector = inspect(db_engine)

    def indexed(table: str) -> set[tuple[str, ...]]:
        return {
            tuple(str(c) for c in index["column_names"]) for index in inspector.get_indexes(table)
        }

    assert {
        ("srcip", "time"),
        ("transip", "transport", "time"),
        ("sessionid",),
        ("dstip", "dstport", "time"),
    } <= indexed("fg_traffic")
    assert {("ip", "time"), ("mac", "time")} <= indexed("mt_dhcp")
    assert ("ip", "time") in indexed("mt_arp")
    assert {("ip", "start"), ("mac", "start")} <= indexed("leases")
    for table in ("fg_web", "fg_app"):
        assert {("sessionid",), ("srcip", "time")} <= indexed(table)
    assert ("source", "time") in indexed("messages")
    assert ("time",) in indexed("audit_log")


def test_timescale_mode_matches_the_server(db_engine: Engine) -> None:
    status = _timescale_status(db_engine)
    if os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1":
        assert status["enabled"], f"TimescaleDB expected but not used: {status}"
    if not _timescale_available(db_engine):
        assert status["enabled"] is False
        assert status["reason"] == "timescaledb extension is not installed"
        assert status["hypertables"] == []
        return
    assert status["enabled"] is True
    assert status["hypertables"] == list(TIME_SERIES_TABLES)
    assert status["telemetry_off"] is True
    with db_engine.connect() as connection:
        hypertables = set(
            connection.execute(
                text("SELECT hypertable_name FROM timescaledb_information.hypertables")
            ).scalars()
        )
        intervals = set(
            connection.execute(
                text(
                    "SELECT time_interval FROM timescaledb_information.dimensions "
                    "WHERE column_name = 'time'"
                )
            ).scalars()
        )
    assert hypertables == set(TIME_SERIES_TABLES)
    assert {str(interval) for interval in intervals} == {"1 day, 0:00:00"}


def test_timescale_off_keeps_plain_tables(empty_engine: Engine) -> None:
    upgrade(empty_engine, timescale="off")
    status = _timescale_status(empty_engine)
    assert status["enabled"] is False
    assert status["mode"] == "off"
    assert "disabled" in status["reason"]


def test_timescale_require_fails_without_timescale(empty_engine: Engine) -> None:
    if _timescale_available(empty_engine):
        upgrade(empty_engine, timescale="require")
        assert _timescale_status(empty_engine)["enabled"] is True
        return
    with pytest.raises(RuntimeError, match="TimescaleDB is required"):
        upgrade(empty_engine, timescale="require")
    assert migration_status(empty_engine).empty  # nothing half-done


def test_non_utf8_database_is_refused(pg_server: TestServer) -> None:
    name = pg_server.new_name("t")
    with pg_server._admin.connect() as connection:
        connection.execute(
            text(
                f"CREATE DATABASE \"{name}\" TEMPLATE template0 ENCODING 'SQL_ASCII' "
                "LC_COLLATE 'C' LC_CTYPE 'C'"
            )
        )
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        with pytest.raises(RuntimeError, match="needs UTF8"):
            upgrade(engine)
        assert migration_status(engine).empty
    finally:
        engine.dispose()
        pg_server.drop_database(name)


def test_concurrent_upgrades_are_serialised(empty_engine: Engine) -> None:
    errors: list[BaseException] = []

    def run() -> None:
        try:
            upgrade(empty_engine)
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=run) for _ in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    with empty_engine.connect() as connection:
        assert current_revisions(connection) == (HEAD,)
        seeded = connection.execute(
            text("SELECT count(*) FROM settings WHERE updated_by = :seed"), {"seed": ":seed"}
        ).scalar()
        assert seeded == len(DEFAULT_SETTINGS)


def test_downgrade_to_base_removes_everything(empty_engine: Engine) -> None:
    upgrade(empty_engine)
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), "base")
    tables = set(inspect(empty_engine).get_table_names())
    assert tables <= {"alembic_version"}
    upgrade(empty_engine)  # and back up again
    assert migration_status(empty_engine).up_to_date


def _lease_row(engine: Engine) -> tuple[Any, str]:
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT value, updated_by FROM settings WHERE key = 'lease.server_seconds'")
        ).one()
    return row[0], row[1]


@pytest.mark.parametrize(("author", "kept_by"), [(":seed", ":migration"), ("tolga", "tolga")])
def test_lease_times_an_earlier_version_seeded_survive_the_new_empty_default(
    empty_engine: Engine, author: str, kept_by: str
) -> None:
    """0013: a guest server's lease time seeded by an earlier version is not reset to ``{}``."""
    upgrade(empty_engine, revision="0012_alert_events")
    with empty_engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE settings SET value = CAST(:value AS jsonb), updated_by = :author "
                "WHERE key = 'lease.server_seconds'"
            ),
            {"value": '{"dhcp61": 5400}', "author": author},
        )
    result = upgrade(empty_engine)
    assert result.after == (HEAD,)
    assert "lease.server_seconds" not in result.seed.settings_refreshed
    assert _lease_row(empty_engine) == ({"dhcp61": 5400}, kept_by)
    assert not upgrade(empty_engine).seed.changed  # and stays so
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0012_alert_events")
    assert _lease_row(empty_engine) == ({"dhcp61": 5400}, author)


def test_a_new_database_gets_the_empty_lease_default(empty_engine: Engine) -> None:
    upgrade(empty_engine)
    assert DEFAULT_SETTINGS["lease.server_seconds"] == {}
    assert _lease_row(empty_engine) == ({}, ":seed")


def test_audit_log_rejects_update_delete_truncate(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO audit_log (time, action, details, prev_hash, hash) "
                "VALUES (now(), 'test', '{}', :p, :h)"
            ),
            {"p": "0" * 64, "h": "a" * 64},
        )
    for statement in (
        "UPDATE audit_log SET action = 'changed'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ):
        with (
            pytest.raises((InternalError, ProgrammingError, DBAPIError), match="append-only"),
            db_engine.begin() as connection,
        ):
            connection.execute(text(statement))
    with db_engine.connect() as connection:
        assert connection.execute(text("SELECT action FROM audit_log")).scalar_one() == "test"
