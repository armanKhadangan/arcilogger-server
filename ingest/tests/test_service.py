"""The ingest service against a real PostgreSQL: offsets, resume, partial lines, day rollover,
vault continuation, failures, sources and alerts."""

from __future__ import annotations

import os
import re
import threading
import time
from datetime import date, timedelta
from datetime import time as clock_time
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from arcilogger_core.rawlog import RawRecord
from arcilogger_core.rawlog import parse_line as parse_raw
from arcilogger_core.timeutil import to_utc
from arcilogger_db.settings import SYSTEM_AUTHOR, set_setting
from arcilogger_ingest import prepare
from arcilogger_ingest.parsers import LinePosition, ParsedRow, ParseOutcome, get_parser
from arcilogger_ingest.service import IngestService
from arcilogger_ingest.status import build_status, read_status
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


def run_once(world: World, engine: Engine, **kwargs: Any) -> IngestService:
    service = world.service(engine, **kwargs)
    assert service.run(once=True) == 0
    return service


def fill(world: World, day: date = DAY, fortigate: int = 40, mikrotik: int = 30) -> None:
    world.append("fortigate", day, (fortigate_message(i) for i in range(fortigate)))
    world.append("core-r1", day, (dhcp_message(i) for i in range(mikrotik)))


def expected_type(source: str, raw: bytes) -> str:
    """messages.type the real parsers give: FortiGate's own type=…, MikroTik DHCP lines 'dhcp'."""
    if source == "fortigate":
        match = re.search(rb' type="([a-z]+)"', raw)
        assert match is not None
        return match.group(1).decode()
    return "dhcp"


def query(engine: Engine, sql: str, **params: Any) -> list[Any]:
    with engine.connect() as connection:
        return list(connection.execute(text(sql), params).all())


def size(world: World, source: str, day: date = DAY) -> int:
    return world.layout.spool_file(source, day).stat().st_size


def test_every_line_once_and_raw_references_point_at_the_line(
    world: World, db_engine: Engine
) -> None:
    fill(world)
    run_once(world, db_engine)
    assert check_exactly_once(db_engine, world) == []
    rows = query(
        db_engine,
        "SELECT source, raw_day, raw_line, raw_offset, time_received, host(sender), type, "
        "parse_error FROM messages",
    )
    assert len(rows) == 70
    for source, day, line, offset, received, sender, kind, parse_error in rows:
        with world.layout.spool_file(source, day).open("rb") as handle:
            handle.seek(offset)
            raw = handle.readline()
        # the byte offset is the start of the line with that 1-based number
        assert raw == world.written[(source, day)][line - 1][2]
        record = parse_raw(raw)
        assert to_utc(record.received) == received
        assert record.sender == sender
        assert kind == expected_type(source, raw)
        assert parse_error is None
    assert offsets(db_engine) == {
        ("fortigate", DAY): (size(world, "fortigate"), 40, False),
        ("core-r1", DAY): (size(world, "core-r1"), 30, False),
    }


def test_resume_after_restart_loads_only_new_lines(world: World, db_engine: Engine) -> None:
    fill(world)
    first = run_once(world, db_engine)
    assert first.counters.lines == 70
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(40, 65)))
    second = run_once(world, db_engine)  # a new process: offsets come from the database
    assert second.counters.lines == 25
    assert check_exactly_once(db_engine, world) == []
    assert offsets(db_engine)[("fortigate", DAY)] == (size(world, "fortigate"), 65, False)
    third = run_once(world, db_engine)
    assert third.counters.lines == 0
    assert len(loaded(db_engine)) == 95


def test_a_partial_last_line_waits_for_its_newline(world: World, db_engine: Engine) -> None:
    complete = 3
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(complete)))
    before_partial = size(world, "core-r1")
    tail = world.append  # the collector's next line, written in two pieces
    rest_line_start = before_partial
    tail("core-r1", DAY, [dhcp_message(3)])
    full = world.layout.spool_file("core-r1", DAY).read_bytes()
    world.layout.spool_file("core-r1", DAY).write_bytes(full[: rest_line_start + 20])

    run_once(world, db_engine)
    assert offsets(db_engine)[("core-r1", DAY)] == (before_partial, complete, False)
    assert len(loaded(db_engine)) == complete

    world.layout.spool_file("core-r1", DAY).write_bytes(full)  # the line is complete now
    run_once(world, db_engine)
    assert check_exactly_once(db_engine, world) == []
    assert ("core-r1", DAY, 4, rest_line_start) in loaded(db_engine)


def test_day_rollover(world: World, db_engine: Engine) -> None:
    fill(world, DAY, fortigate=5, mikrotik=5)
    run_once(world, db_engine)
    world.set_today(NEXT, hh=0)
    world.append("fortigate", DAY, [fortigate_message(99)], start=clock_time(23, 59, 59))
    fill(world, NEXT, fortigate=4, mikrotik=2)
    run_once(world, db_engine)
    assert check_exactly_once(db_engine, world) == []
    state = offsets(db_engine)
    assert state[("fortigate", DAY)] == (size(world, "fortigate"), 6, False)
    assert state[("fortigate", NEXT)] == (size(world, "fortigate", NEXT), 4, False)
    # yesterday is closed but not sealed: it is not finished
    assert not any(finished for _, _, finished in state.values())


def test_spool_deleted_by_the_sealer_continues_from_the_vault(
    world: World, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    fill(world, fortigate=30, mikrotik=10)
    run_once(world, db_engine)
    loaded_bytes = size(world, "fortigate")
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(30, 50)))
    world.set_today(NEXT)
    world.seal(DAY)  # compresses, stamps, verifies and DELETES the spool files
    assert not world.layout.spool_file("fortigate", DAY).exists()

    caplog.set_level("INFO", logger="arcilogger.ingest")
    service = run_once(world, db_engine)
    assert service.counters.lines == 20
    assert f"continuing from fortigate.log.gz at byte {loaded_bytes}" in caplog.text
    assert check_exactly_once(db_engine, world) == []
    state = offsets(db_engine)
    # both sources are sealed and completely loaded: finished
    assert state[("fortigate", DAY)][1:] == (50, True)
    assert state[("core-r1", DAY)][1:] == (10, True)
    assert service.streams == {}
    assert run_once(world, db_engine).counters.lines == 0


def test_a_day_sealed_before_ingest_ever_saw_it_is_loaded_from_the_vault(
    world: World, db_engine: Engine
) -> None:
    """E.g. the service was down over a weekend and the sealer deleted the spool files."""
    fill(world, fortigate=12, mikrotik=7)
    world.set_today(NEXT)
    world.seal(DAY)
    assert world.layout.spool_files_for(DAY) == []
    service = run_once(world, db_engine)
    assert service.counters.lines == 19
    assert check_exactly_once(db_engine, world) == []
    assert {key: value[2] for key, value in offsets(db_engine).items()} == {
        ("fortigate", DAY): True,
        ("core-r1", DAY): True,
    }


def test_sealed_days_older_than_the_database_retention_are_not_loaded(
    world: World, db_engine: Engine, db_session: Session
) -> None:
    """The vault keeps 2 years, the database less (db.retention_days)."""
    set_setting(db_session, "db.retention_days", 2, updated_by=SYSTEM_AUTHOR)
    db_session.commit()
    fill(world, fortigate=3, mikrotik=0)
    world.seal(DAY)
    world.set_today(DAY + timedelta(days=3))
    run_once(world, db_engine)
    assert loaded(db_engine) == []
    world.set_today(DAY + timedelta(days=2))
    run_once(world, db_engine)
    assert len(loaded(db_engine)) == 3


def test_seal_while_the_service_is_running(world: World, db_engine: Engine) -> None:
    """The real sealer deletes the spool file under a running service; nothing is lost."""
    stop = threading.Event()
    service = world.service(db_engine, stop=stop)
    runner = threading.Thread(target=service.run)
    runner.start()
    try:
        for round_number in range(5):
            world.append(
                "fortigate",
                DAY,
                (fortigate_message(i) for i in range(round_number * 10, round_number * 10 + 10)),
            )
            time.sleep(0.05)
        world.set_today(NEXT)
        world.seal(DAY)
        world.append("fortigate", NEXT, (fortigate_message(i) for i in range(20)))
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            state = offsets(db_engine)
            if (
                state.get(("fortigate", DAY), (0, 0, False))[2]
                and state.get(("fortigate", NEXT), (0, 0, False))[1] == 20
            ):
                break
            time.sleep(0.05)
    finally:
        stop.set()
        runner.join(10)
    assert not runner.is_alive()
    assert check_exactly_once(db_engine, world) == []
    assert offsets(db_engine)[("fortigate", DAY)][2] is True


def test_today_is_never_finished_and_the_audit_folder_is_ignored(
    world: World, db_engine: Engine
) -> None:
    fill(world, fortigate=2, mikrotik=0)
    audit = world.layout.audit_spool(DAY)
    audit.parent.mkdir(parents=True)
    audit.write_bytes(b'{"hash": "x"}\n')
    (world.layout.spool / "fortigate" / "notes.txt").write_text("not a spool file", "utf-8")
    (world.layout.spool / "fortigate" / "not-a-day.log").write_text("x\n", "utf-8")
    run_once(world, db_engine)
    assert set(offsets(db_engine)) == {("fortigate", DAY)}
    assert offsets(db_engine)[("fortigate", DAY)][2] is False


def test_rowcheck_failures_become_parse_failures_and_the_messages_still_load(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fake(record: RawRecord, position: LinePosition) -> ParseOutcome:
        outcome = get_parser("mikrotik").parse(record, position)
        common = {
            "time": to_utc(record.received),
            "time_received": to_utc(record.received),
            "source": position.source,
            "sender": record.sender,
            "raw_day": position.raw_day,
            "raw_line": position.raw_line,
            "raw_offset": position.raw_offset,
            "extra": {},
            "server": "dhcp60",
            "action": "assigned",
            "mac": "02:00:5e:00:53:5b",
        }
        bad = position.raw_line % 3 == 0
        row = ParsedRow("mt_dhcp", {**common, "ip": "999.1.1.1" if bad else "172.20.60.9"})
        return ParseOutcome(parser="mikrotik", message=outcome.message, rows=(row,))

    monkeypatch.setattr(prepare, "parse_line", fake)
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(10)))
    run_once(world, db_engine)
    assert check_exactly_once(db_engine, world) == []
    assert query(db_engine, "SELECT count(*) FROM mt_dhcp")[0][0] == 7
    failures = query(
        db_engine,
        "SELECT source, day, pattern, count, example_line, example_offset, error "
        "FROM parse_failures",
    )
    assert len(failures) == 1
    source, day, pattern, count, example_line, example_offset, error = failures[0]
    assert (source, day, pattern, count) == ("core-r1", DAY, "rowcheck.mt_dhcp", 3)
    assert (example_line, example_offset) == (3, world.written[("core-r1", DAY)][2][1])
    assert "mt_dhcp.ip" in error
    flagged = query(db_engine, "SELECT raw_line FROM messages WHERE parse_error IS NOT NULL")
    assert sorted(r[0] for r in flagged) == [3, 6, 9]


def test_parse_failures_aggregate_across_batches_and_runs(world: World, db_engine: Engine) -> None:
    world.reconfigure(ingest={"batch_rows": 4})
    path = world.layout.spool_file("core-r1", DAY)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"garbage one\n" * 10)
    run_once(world, db_engine)
    path.open("ab").write(b"garbage two\n" * 5)
    run_once(world, db_engine)
    rows = query(
        db_engine,
        "SELECT pattern, count, example_line, example_offset, first_seen <= last_seen "
        "FROM parse_failures",
    )
    assert rows == [("rawlog.invalid", 15, 1, 0, True)]
    assert loaded(db_engine) == []
    assert offsets(db_engine)[("core-r1", DAY)] == (path.stat().st_size, 15, False)


def test_unknown_sender_source_and_a_single_new_source_alert(
    world: World, db_engine: Engine
) -> None:
    folder = "unknown-10.9.9.9"
    world.append(folder, DAY, (fortigate_message(i) for i in range(3)), sender="10.9.9.9")
    run_once(world, db_engine)
    world.append(folder, DAY, (fortigate_message(i) for i in range(3, 6)), sender="10.9.9.9")
    run_once(world, db_engine)
    with db_engine.begin() as connection:
        connection.execute(text("UPDATE alerts SET state = 'resolved'"))
    world.append(folder, NEXT, [fortigate_message(7)], sender="10.9.9.9")
    run_once(world, db_engine)
    sources = query(
        db_engine,
        "SELECT type, host(address), unknown, expected, first_seen < last_seen "
        "FROM sources WHERE name = :name",
        name=folder,
    )
    assert sources == [("fortigate", "10.9.9.9", True, False, True)]
    alerts = query(db_engine, "SELECT rule, severity, state, title, dedup_key, details FROM alerts")
    assert len(alerts) == 1, "raised once, when the sender was first recorded"
    rule, severity, _state, title, dedup_key, details = alerts[0]
    assert (rule, severity, dedup_key) == ("new_source", "medium", f"new_source:{folder}")
    assert title == "Yeni log kaynağı: 10.9.9.9"
    assert details["source"] == folder
    assert details["detected_type"] == "fortigate"
    assert check_exactly_once(db_engine, world) == []


def test_sources_are_kept_up_to_date(world: World, db_engine: Engine) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(3)))
    world.append("ap-172.20.10.23", DAY, [b"<13>ap hello"], sender="172.20.10.23")
    run_once(world, db_engine)
    rows = {
        row[0]: row[1:]
        for row in query(
            db_engine,
            "SELECT name, type, host(address), unknown, expected, heartbeat_minutes, "
            "first_seen IS NOT NULL, last_seen IS NOT NULL FROM sources",
        )
    }
    assert rows == {
        "fortigate": ("fortigate", "172.31.255.1", False, True, 10, True, True),
        # configured, nothing received yet: listed so a silent source is visible
        "core-r1": ("mikrotik", "172.20.10.1", False, True, 15, False, False),
        "ap-172.20.10.23": ("generic", "172.20.10.23", False, False, 15, True, True),
    }
    first, last = query(
        db_engine, "SELECT first_seen, last_seen FROM sources WHERE name = 'fortigate'"
    )[0]
    written = world.written[("fortigate", DAY)]
    assert first == to_utc(parse_raw(written[0][2]).received)
    assert last == to_utc(parse_raw(written[-1][2]).received)


def test_a_line_copy_refuses_is_isolated_and_counted(
    world: World, db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """check_row passes, COPY fails (a lone surrogate cannot be encoded): only that line."""

    def fake(record: RawRecord, position: LinePosition) -> ParseOutcome:
        outcome = get_parser("generic").parse(record, position)
        if position.raw_line == 7:
            message = {**outcome.message, "host": "bad\udc80host"}
            return ParseOutcome(parser="generic", message=message)
        if position.raw_line == 9:
            common = {k: outcome.message[k] for k in ("time", "time_received", "source")}
            row = ParsedRow(
                "mt_event",
                {
                    **common,
                    "sender": record.sender,
                    "raw_day": position.raw_day,
                    "raw_line": position.raw_line,
                    "raw_offset": position.raw_offset,
                    "extra": {},
                    "event": "x\udc80",
                },
            )
            return ParseOutcome(parser="generic", message=outcome.message, rows=(row,))
        return outcome

    monkeypatch.setattr(prepare, "parse_line", fake)
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(20)))
    run_once(world, db_engine)
    lines = [row[2] for row in loaded(db_engine)]
    assert lines == [n for n in range(1, 21) if n != 7]  # line 9 keeps its messages row
    failures = query(db_engine, "SELECT pattern, count, example_line FROM parse_failures")
    assert sorted(failures) == [("copy.messages", 1, 7), ("copy.mt_event", 1, 9)]
    assert query(db_engine, "SELECT parse_error FROM messages WHERE raw_line = 9") == [
        ("copy.mt_event",)
    ]
    assert offsets(db_engine)[("core-r1", DAY)][1] == 20


def test_status_file(world: World, db_engine: Engine) -> None:
    fill(world, fortigate=4, mikrotik=2)
    world.append("unknown-10.9.9.9", NEXT, [b"<13>x"], sender="10.9.9.9")
    world.set_today(NEXT)
    service = run_once(world, db_engine)
    status = read_status(world.layout.state / "ingest-status.json")
    assert status is not None
    assert status["state"] == "stopped"
    assert status["database"] == {"ok": True, "last_error": None}
    assert status["parse_workers"] == {
        "configured": service.workers,
        "active": 0,  # stopped: the pool is closed
        "restarts": 0,
        "last_error": None,
    }
    assert status["totals"]["lines"] == 7 == service.counters.lines
    fortigate = status["sources"]["fortigate"]
    assert fortigate["day"] == DAY.isoformat()
    assert (fortigate["byte_offset"], fortigate["line_number"]) == (size(world, "fortigate"), 4)
    assert fortigate["lag_bytes"] == 0
    assert fortigate["lag_seconds"] == 0.0
    assert fortigate["read_from"] == "spool"
    assert fortigate["unfinished_days"] == [DAY.isoformat()]
    assert fortigate["last_received"].endswith("+03:00")
    assert set(status["sources"]) == {"fortigate", "core-r1", "unknown-10.9.9.9"}


def test_status_lag_while_behind(world: World, db_engine: Engine) -> None:
    fill(world, fortigate=4, mikrotik=0)
    run_once(world, db_engine)
    world.append("fortigate", DAY, [fortigate_message(5)])
    service = world.service(db_engine)
    service._load_offsets()
    entry = build_status(service)["sources"]["fortigate"]
    assert entry["lag_bytes"] == len(world.written[("fortigate", DAY)][-1][2])
    assert entry["lag_seconds"] is None  # nothing loaded by this process yet


def test_a_spool_file_shorter_than_the_loaded_offset_is_not_read(
    world: World, db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    fill(world, fortigate=5, mikrotik=0)
    run_once(world, db_engine)
    path = world.layout.spool_file("fortigate", DAY)
    path.write_bytes(path.read_bytes()[:100])  # the evidence was changed
    service = run_once(world, db_engine)
    assert service.counters.lines == 0
    assert "the raw file was changed" in caplog.text
    status = read_status(world.layout.state / "ingest-status.json")
    assert status is not None
    assert "changed" in status["sources"]["fortigate"]["last_error"]


def test_parse_workers_load_the_same_rows(world: World, db_engine: Engine) -> None:
    world.reconfigure(ingest={"batch_rows": 300})
    fill(world, fortigate=700, mikrotik=500)
    service = run_once(world, db_engine, workers=2)
    assert service.counters.lines == 1200
    assert service.parse_workers()["configured"] == 2
    assert service.parse_workers()["restarts"] == 0
    assert check_exactly_once(db_engine, world) == []
    texts = query(db_engine, "SELECT raw_line, text FROM messages WHERE source = 'core-r1'")
    by_line = dict(texts)
    for number, _, raw in world.written[("core-r1", DAY)]:
        expected = get_parser("mikrotik").parse(
            parse_raw(raw), LinePosition("core-r1", "mikrotik", DAY, number, 0)
        )
        assert by_line[number] == expected.message["text"]


def test_a_folder_that_cannot_be_listed_is_skipped_and_retried(
    world: World,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """E.g. an antivirus scan or a wrong ACL on Windows: the other sources keep loading."""
    fill(world, fortigate=3, mikrotik=2)
    blocked = str(world.layout.spool / "core-r1")
    original = os.scandir

    def scandir(path: Any) -> Any:
        if str(path) == blocked:
            raise PermissionError(13, "Access is denied", blocked)
        return original(path)

    monkeypatch.setattr(os, "scandir", scandir)  # the wrapper passes everything else on
    run_once(world, db_engine)
    run_once(world, db_engine)
    assert {row[0] for row in loaded(db_engine)} == {"fortigate"}
    assert caplog.text.count("cannot list") == 2  # once per service instance, not every poll
    monkeypatch.setattr(os, "scandir", original)
    run_once(world, db_engine)
    assert check_exactly_once(db_engine, world) == []


def test_retention_changes_are_picked_up_while_running(
    world: World, db_engine: Engine, db_session: Session
) -> None:
    set_setting(db_session, "db.retention_days", 1, updated_by=SYSTEM_AUTHOR)
    db_session.commit()
    fill(world, fortigate=3, mikrotik=0)
    world.seal(DAY)
    world.set_today(DAY + timedelta(days=5))
    stop = threading.Event()
    service = world.service(db_engine, stop=stop, vault_scan_seconds=0.05)
    runner = threading.Thread(target=service.run)
    runner.start()
    try:
        time.sleep(0.3)
        assert loaded(db_engine) == []
        set_setting(db_session, "db.retention_days", 400, updated_by=SYSTEM_AUTHOR)
        db_session.commit()
        deadline = time.monotonic() + 20
        while len(loaded(db_engine)) < 3 and time.monotonic() < deadline:
            time.sleep(0.05)
    finally:
        stop.set()
        runner.join(10)
    assert check_exactly_once(db_engine, world) == []
