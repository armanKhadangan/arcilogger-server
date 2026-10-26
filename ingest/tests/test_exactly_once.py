"""Exactly once: an interruption at any point never loads a line twice and never skips one.

Rows, offsets and the search summary counts (``search_counts``, written in the same transaction)
agree after every one of these:

* every step of the load transaction is interrupted in turn (the hook raises, like a crash);
* a second instance moves the offsets under a running one (compare-and-set refuses the batch);
* two instances race on the same files (both with the single-instance lock switched off: the
  compare-and-set is the safety net for the moments the lock cannot cover, e.g. while the lock
  connection is being re-established after a database restart);
* the real service process is killed (``SIGKILL`` / ``TerminateProcess``) several times.
"""

from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import date, timedelta

import pytest
from sqlalchemy import Connection, Engine, text

from arcilogger_core.rawlog import RawRecord
from arcilogger_core.timeutil import day_window
from arcilogger_db.engine import engine_from_url
from arcilogger_db.searchcounts import verify
from arcilogger_ingest import prepare
from arcilogger_ingest.loader import STEPS
from arcilogger_ingest.parsers import LinePosition, ParsedRow, ParseOutcome, get_parser
from arcilogger_ingest.status import read_status
from arcilogger_ingest.testing import (
    DAY,
    World,
    check_exactly_once,
    dhcp_message,
    fortigate_message,
    loaded,
    offsets,
    write_config_file,
)

#: ``searchcounts.verify`` recounts the hours before this moment; every row of these tests (lines
#: received on DAY - 1 or DAY, device times within a day of that) lies in them.
VERIFY_NOW = day_window(DAY + timedelta(days=3))[0]
VERIFY_HOURS = 24 * 7


class SimulatedCrashError(Exception):
    """Stands for the process dying at that point."""


def fill(world: World) -> None:
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(150)))
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(120)))
    world.append("unknown-10.9.9.9", DAY, [b"<13>who am i"] * 30, sender="10.9.9.9")


def counts_agree(connection: Connection) -> None:
    """``search_counts`` holds exactly the loaded rows: equal to their recount, nothing else."""
    report = verify(connection, VERIFY_HOURS, now=VERIFY_NOW)
    assert report.differences == []
    total = connection.execute(text("SELECT coalesce(sum(n), 0) FROM search_counts")).scalar()
    assert report.lines == total == len(loaded(connection))


def counts_consistent(engine: Engine) -> None:
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        counts_agree(connection)


def consistent(engine: Engine, world: World) -> None:
    """Rows, offsets and counts always agree: exactly the lines up to each committed offset."""
    # One snapshot: a COMMIT that a killed process sent just before it died can still land
    # between two separate reads, which then disagree although every commit was whole.
    with (
        engine.connect().execution_options(isolation_level="REPEATABLE READ") as connection,
        connection.begin(),
    ):
        committed = offsets(connection)
        rows = loaded(connection)
        counts_agree(connection)
    assert len(rows) == len(set(rows))
    expected = {
        (source, day, number, offset)
        for (source, day), lines in world.written.items()
        for number, offset, _ in lines
        if number <= committed.get((source, day), (0, 0, False))[1]
    }
    assert set(rows) == expected
    for (source, day), (byte_offset, line_number, _) in committed.items():
        lines = world.written[(source, day)]
        end = lines[line_number - 1][1] + len(lines[line_number - 1][2]) if line_number else 0
        assert byte_offset == end


def failing_rowcheck(record: RawRecord, position: LinePosition) -> ParseOutcome:
    """Every 10th line has an invalid typed row, so every batch also writes parse_failures."""
    outcome = get_parser("generic").parse(record, position)
    if position.raw_line % 10:
        return outcome
    row = ParsedRow("mt_arp", {**outcome.rows[0].values} if outcome.rows else {"ip": "bad"})
    return ParseOutcome(parser=outcome.parser, message=outcome.message, rows=(row,))


@pytest.mark.parametrize("step", STEPS)
def test_interruption_at_every_step_of_the_load(
    world: World, db_engine: Engine, step: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(prepare, "parse_line", failing_rowcheck)
    world.reconfigure(ingest={"batch_rows": 100})
    fill(world)
    calls: list[str] = []

    def crash_in_second_batch(name: str) -> None:
        calls.append(name)
        if name == step and calls.count("offsets") == 2:
            raise SimulatedCrashError(name)

    service = world.service(db_engine, step_hook=crash_in_second_batch)
    with pytest.raises(SimulatedCrashError):
        service.run(once=True)
    status = read_status(world.layout.state / "ingest-status.json")
    assert status is not None
    assert status["state"] == "failed"
    # exactly the first batch is in the database, rows and offsets agree
    assert service.counters.lines == 100 == len(loaded(db_engine))
    consistent(db_engine, world)
    with db_engine.connect() as connection:
        failures = connection.execute(text("SELECT sum(count) FROM parse_failures")).scalar()
    # lines 10, 20, … up to each source's committed line (the sources share the batch)
    assert failures == sum(line // 10 for _, line, _ in offsets(db_engine).values())

    restarted = world.service(db_engine)
    assert restarted.run(once=True) == 0
    assert check_exactly_once(db_engine, world) == []
    consistent(db_engine, world)  # every line counted once, the first batch's not twice
    with db_engine.connect() as connection:
        failures = connection.execute(text("SELECT sum(count) FROM parse_failures")).scalar()
        alerts = connection.execute(text("SELECT count(*) FROM alerts")).scalar()
    assert failures == 30  # lines 10, 20, … of each source, each counted once
    assert alerts == 1


def test_a_second_instance_moving_the_offsets_makes_the_first_one_back_off(
    world: World, db_engine: Engine, pg_dsn: str, caplog: pytest.LogCaptureFixture
) -> None:
    world.reconfigure(ingest={"batch_rows": 100})
    fill(world)
    other_engine = engine_from_url(pg_dsn)
    calls: list[str] = []

    def other_instance_runs_now(name: str) -> None:
        calls.append(name)
        if name == "offsets" and calls.count("offsets") == 2:
            other = world.service(other_engine, single_instance=False)
            assert other.run(once=True) == 0  # loads everything that is left

    try:
        first = world.service(db_engine, step_hook=other_instance_runs_now, single_instance=False)
        assert first.run(once=True) == 0
    finally:
        other_engine.dispose()
    assert "moved by another instance" in caplog.text
    assert first.counters.lines == 100  # only its first batch
    assert check_exactly_once(db_engine, world) == []
    counts_consistent(db_engine)  # the refused batch's counts went with its rows


def test_two_instances_racing_on_the_same_files(
    world: World, db_engine: Engine, pg_dsn: str
) -> None:
    world.reconfigure(ingest={"batch_rows": 40})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(1500)))
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(1000)))
    engines = [db_engine, engine_from_url(pg_dsn)]
    results: list[int] = []
    try:
        threads = [
            threading.Thread(
                target=lambda e=engine: results.append(
                    world.service(e, single_instance=False).run(once=True)
                )
            )
            for engine in engines
        ]
        for thread in threads:
            thread.start()
        # Only a guard against a hang: on a loaded machine two instances need well over a minute.
        for thread in threads:
            thread.join(300)
        assert not any(thread.is_alive() for thread in threads), "an instance did not finish"
    finally:
        engines[1].dispose()
    assert results == [0, 0]
    assert check_exactly_once(db_engine, world) == []
    counts_consistent(db_engine)


def _messages(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.execute(text("SELECT count(*) FROM messages")).scalar_one())


def test_killing_the_service_process_repeatedly(
    world: World, db_engine: Engine, pg_dsn: str
) -> None:
    """SIGKILL (POSIX) / TerminateProcess (Windows) at arbitrary moments, then one last run."""
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(12_000)))
    world.append("fortigate", date(2026, 9, 22), (fortigate_message(i) for i in range(8_000)))
    config = write_config_file(world, pg_dsn, batch_rows=300)
    command = [sys.executable, "-m", "arcilogger_ingest", "run", "--config", str(config)]
    kills = 0
    for delay in (0.0, 0.05, 0.13):
        before = _messages(db_engine)
        process = subprocess.Popen(command, stderr=subprocess.DEVNULL)
        try:
            deadline = time.monotonic() + 60
            while _messages(db_engine) == before and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(delay)
        finally:
            process.kill()
            process.wait(30)
        kills += 1
        consistent(db_engine, world)
    total = _messages(db_engine)
    assert kills == 3
    assert 0 < total < 20_000, "the kills happened while loading"
    final = subprocess.run([*command, "--once"], capture_output=True, timeout=120, check=False)
    assert final.returncode == 0, final.stderr.decode(errors="replace")
    assert check_exactly_once(db_engine, world) == []
    counts_consistent(db_engine)
