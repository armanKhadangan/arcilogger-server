"""Continuing from the sealed copy: across batches, in the sealer's crash window, and the cases
where a day must *not* be marked finished."""

from __future__ import annotations

import json
from datetime import date, timedelta

import pytest
from sqlalchemy import Engine, text

from arcilogger_core.immutability import unlock
from arcilogger_db.partitions import ensure_partitions
from arcilogger_ingest.service import IngestService
from arcilogger_ingest.status import read_status
from arcilogger_ingest.testing import (
    DAY,
    World,
    check_exactly_once,
    dhcp_message,
    fortigate_message,
    loaded,
    offsets,
)

NEXT = DAY + timedelta(days=1)


def run_once(world: World, engine: Engine) -> IngestService:
    service = world.service(engine)
    assert service.run(once=True) == 0
    return service


def source_error(world: World, source: str) -> str | None:
    status = read_status(world.layout.state / "ingest-status.json")
    assert status is not None
    error = status["sources"][source]["last_error"]
    return None if error is None else str(error)


def test_vault_continuation_across_many_small_batches(world: World, db_engine: Engine) -> None:
    """The vault reader keeps its place between batches and takes back what a batch left."""
    world.reconfigure(ingest={"batch_rows": 7})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10)))
    run_once(world, db_engine)
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10, 95)))
    world.seal(DAY)
    world.set_today(NEXT)
    service = run_once(world, db_engine)
    assert service.counters.lines == 85
    assert service.counters.batches >= 85 // 7
    assert check_exactly_once(db_engine, world) == []
    assert offsets(db_engine)[("core-r1", DAY)][1:] == (95, True)


def test_crash_window_spool_deleted_but_day_not_yet_recorded_as_sealed(
    world: World, db_engine: Engine
) -> None:
    """The sealer deletes the spool before it records "sealed" (and finishes after a crash).

    Ingest continues a stream it already knows from the verified vault copy, but marks the day
    finished only once the seal state says so. (A day ingest never saw is found by the vault
    scan, which only looks at sealed days.)
    """
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(8)))
    run_once(world, db_engine)
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(8, 20)))
    world.seal(DAY)
    state_path = world.layout.seal_state(DAY)
    sealed_state = state_path.read_text("utf-8")
    state_path.write_text(json.dumps({"day": DAY.isoformat(), "status": "pending"}), "utf-8")
    world.set_today(NEXT)
    assert run_once(world, db_engine).counters.lines == 12
    assert check_exactly_once(db_engine, world) == []
    assert offsets(db_engine)[("fortigate", DAY)][1:] == (20, False)

    state_path.write_text(sealed_state, "utf-8")  # the sealer's next run records it
    run_once(world, db_engine)
    assert offsets(db_engine)[("fortigate", DAY)][1:] == (20, True)
    assert len(loaded(db_engine)) == 20


def test_spool_missing_and_day_not_sealed_is_reported_not_finished(
    world: World, db_engine: Engine
) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(3)))
    run_once(world, db_engine)
    world.layout.spool_file("fortigate", DAY).unlink()  # removed by hand: not the sealer
    world.set_today(NEXT)
    run_once(world, db_engine)
    assert source_error(world, "fortigate") == "spool file missing and the day is not sealed yet"
    assert offsets(db_engine)[("fortigate", DAY)][1:] == (3, False)


def test_a_source_missing_from_the_manifest_is_reported(world: World, db_engine: Engine) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(2)))
    world.seal(DAY)
    with db_engine.begin() as connection:  # e.g. a spool file that appeared after sealing
        connection.execute(
            text("INSERT INTO ingest_offsets (source, day) VALUES ('switch-7', :day)"),
            {"day": DAY},
        )
    world.set_today(NEXT)
    run_once(world, db_engine)
    assert source_error(world, "switch-7") == (
        "spool file missing and the source is not in the day's manifest"
    )
    assert offsets(db_engine)[("switch-7", DAY)] == (0, 0, False)
    assert offsets(db_engine)[("fortigate", DAY)][1:] == (2, True)


def test_a_damaged_sealed_file_is_reported_and_retried(
    world: World, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(400)))
    world.seal(DAY)
    vault_file = world.layout.vault_day(DAY) / "fortigate.log.gz"
    good = vault_file.read_bytes()
    unlock(vault_file)
    vault_file.write_bytes(good[: len(good) // 2])
    world.set_today(NEXT)
    run_once(world, db_engine)
    error = source_error(world, "fortigate")
    assert error is not None
    assert error.startswith("sealed file not readable")
    assert not offsets(db_engine).get(("fortigate", DAY), (0, 0, False))[2]

    vault_file.write_bytes(good)  # restored from the replica
    run_once(world, db_engine)
    assert check_exactly_once(db_engine, world) == []
    assert offsets(db_engine)[("fortigate", DAY)][2] is True


def test_more_bytes_loaded_than_sealed_is_never_finished(world: World, db_engine: Engine) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(4)))
    run_once(world, db_engine)
    world.seal(DAY)
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE ingest_offsets SET byte_offset = byte_offset + 10 WHERE day = :day"),
            {"day": DAY},
        )
    world.set_today(NEXT)
    run_once(world, db_engine)
    error = source_error(world, "fortigate")
    assert error is not None
    assert "not finishing this day" in error
    assert offsets(db_engine)[("fortigate", DAY)][2] is False


def test_a_vault_day_months_old_lands_in_its_monthly_partition(
    world: World, db_engine: Engine
) -> None:
    """After a database loss ingest re-loads every sealed day of db.retention_days from the
    vault; the partitions cover that same window, so nothing lands in the default partition
    (which every later monthly partition would have to scan under an exclusive lock)."""
    old = DAY - timedelta(days=153)  # 2026-04-23
    with db_engine.begin() as connection:
        report = ensure_partitions(connection, today=DAY)  # what upgrade and the worker do
    if not report.tables:
        pytest.skip("TimescaleDB hypertables: no monthly partitions")
    world.append("core-r1", old, (dhcp_message(i) for i in range(6)))
    world.append("fortigate", old, (fortigate_message(i) for i in range(4)))
    world.seal(old)
    world.set_today(DAY)
    assert run_once(world, db_engine).counters.lines == 10
    assert offsets(db_engine)[("core-r1", old)][1:] == (6, True)

    def count(table: str) -> int:
        with db_engine.connect() as connection:
            query = text(f"SELECT count(*) FROM {table}")  # noqa: S608 - fixed table names
            return int(connection.execute(query).scalar_one())

    for table in ("messages", "mt_dhcp", "fg_traffic"):
        assert count(f"{table}_default") == 0, table
    assert count("messages_p202604") == 10
    with db_engine.begin() as connection:
        later = ensure_partitions(connection, today=date(2026, 11, 2))
    assert later.moved == {}
