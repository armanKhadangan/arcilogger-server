"""Hash-chained audit log: chain, spool projection, concurrency, tampering, sealing."""

from __future__ import annotations

import asyncio
import gzip
import hashlib
import itertools
import json
import threading
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import pytest
from fastapi import APIRouter, FastAPI, Request
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from arcilogger_api import audit
from arcilogger_api import testing as api_testing
from arcilogger_api.app import create_app
from arcilogger_api.audit import (
    GENESIS_HASH,
    SystemActor,
    canonical_entry,
    entry_hash,
    entry_of,
    flush_audit_spool,
    record_audit,
    repair_torn_tail,
    run_periodic_flush,
    spool_line,
    verify_audit_chain,
    verify_audit_lines,
)
from arcilogger_api.deps import AppState, DbSession
from arcilogger_api.security import CurrentUserDep, client_ip
from arcilogger_api.testing import LoggedIn
from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import verify_day
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ, site_day
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import AuditLog, Setting
from arcilogger_db.settings import DEFAULT_SETTINGS

TOLGA = SystemActor(username="tolga", id=7)


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


def _rows(factory: sessionmaker[Session]) -> list[AuditLog]:
    with factory() as session:
        return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


def _spool_lines(layout: Layout) -> list[bytes]:
    folder = layout.spool / "_audit"
    lines: list[bytes] = []
    for path in sorted(folder.glob("*.jsonl")):
        lines += path.read_bytes().splitlines()
    return lines


def test_canonical_entry_and_hash_formula() -> None:
    """Golden value: sha256(prev_hash ASCII || RFC 8785 bytes of the entry)."""
    entry = canonical_entry(
        entry_id=1,
        time=datetime(2026, 10, 12, 18, 19, 7, 123456, tzinfo=SITE_TZ),
        actor_id=7,
        actor="tolga",
        action="lookup.run",
        target="lookup:1",
        details={"reason": "Savcılık talebi", "public_ip": "192.0.2.44"},
        ip="172.20.10.37",
    )
    expected_bytes = (
        '{"action":"lookup.run","actor":"tolga","actor_id":7,'
        '"details":{"public_ip":"192.0.2.44","reason":"Savcılık talebi"},'
        '"id":1,"ip":"172.20.10.37","target":"lookup:1",'
        '"time":"2026-10-12T15:19:07.123456+00:00","v":1}'
    ).encode()
    assert canonical_json(entry) == expected_bytes
    assert entry_hash(GENESIS_HASH, entry) == hashlib.sha256(b"0" * 64 + expected_bytes).hexdigest()


def test_chain_of_entries_in_db_and_spool(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    for number in range(4):
        with app_session_factory() as session:
            record_audit(
                session,
                actor=TOLGA if number else None,
                action=f"test.{number}",
                target=f"t:{number}",
                details={"n": number, "when": datetime(2026, 10, 12, 10, tzinfo=UTC)},
                ip="172.20.10.37",
            )
            session.commit()
    rows = _rows(app_session_factory)
    assert [r.action for r in rows] == ["test.0", "test.1", "test.2", "test.3"]
    assert rows[0].prev_hash == GENESIS_HASH
    assert rows[0].actor_username == "system"
    assert rows[0].actor_id is None
    assert rows[1].actor_id == 7
    assert rows[1].details == {"n": 1, "when": "2026-10-12T10:00:00.000000+00:00"}
    for previous, row in itertools.pairwise(rows):
        assert row.prev_hash == previous.hash
        assert row.id > previous.id
    with app_session_factory() as session:
        result = verify_audit_chain(session)
    assert (result.ok, result.checked, result.last_hash) == (True, 4, rows[-1].hash)
    lines = _spool_lines(layout)
    assert lines == [spool_line(row).rstrip(b"\n") for row in rows]
    check = verify_audit_lines(lines, prev_hash=GENESIS_HASH)
    assert (check.ok, check.checked, check.last_hash) == (True, 4, rows[-1].hash)
    first = json.loads(lines[0])
    assert set(first) == {"v", "id", "time", "actor_id", "actor", "action", "target", "details",
                          "ip", "prev_hash", "hash"}  # fmt: skip


def test_rolled_back_entry_leaves_no_trace(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="kept")
        session.commit()
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="rolled.back")
        session.rollback()
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="after")
        session.commit()
    rows = _rows(app_session_factory)
    assert [r.action for r in rows] == ["kept", "after"]
    assert rows[1].prev_hash == rows[0].hash
    assert len(_spool_lines(layout)) == 2
    with app_session_factory() as session:
        assert verify_audit_chain(session).ok


@pytest.mark.parametrize(
    ("details", "message"),
    [
        ({"ratio": 0.5}, "cannot be stored exactly"),
        ({"big": 2**60}, "cannot be stored exactly"),
        ({"text": "a\x00b"}, "NUL"),
    ],
)
def test_details_that_cannot_be_hashed_exactly_are_refused(
    app_session_factory: sessionmaker[Session], details: dict[str, Any], message: str
) -> None:
    with app_session_factory() as session, pytest.raises(ValueError, match=message):
        record_audit(session, actor=TOLGA, action="x", details=details)


def test_invalid_client_address_does_not_fail_the_entry(
    app_session_factory: sessionmaker[Session],
) -> None:
    with app_session_factory() as session:
        row = record_audit(session, actor=TOLGA, action="x", ip="testclient")
        session.commit()
    assert row.ip is None


def test_record_audit_needs_a_spool(db_session: Session) -> None:
    with pytest.raises(RuntimeError, match="no audit spool"):
        record_audit(db_session, actor=TOLGA, action="x")


def test_ipv6_and_ipv4_addresses_hash_the_same_way_after_storage(
    app_session_factory: sessionmaker[Session],
) -> None:
    for address in ("172.20.10.37", "2001:db8::1", "::ffff:192.0.2.1", "fe80::1:0:0:1"):
        with app_session_factory() as session:
            record_audit(session, actor=TOLGA, action="ip", ip=address)
            session.commit()
    with app_session_factory() as session:
        assert verify_audit_chain(session).ok


def test_concurrent_writers_form_one_chain(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    errors: list[BaseException] = []
    barrier = threading.Barrier(6)

    def writer(number: int) -> None:
        try:
            barrier.wait()
            for index in range(5):
                with app_session_factory() as session:
                    record_audit(
                        session,
                        actor=SystemActor(username=f"writer{number}"),
                        action="concurrent",
                        details={"writer": number, "index": index},
                    )
                    session.commit()
        except BaseException as exc:  # pragma: no cover - reported below
            errors.append(exc)

    threads = [threading.Thread(target=writer, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    rows = _rows(app_session_factory)
    assert len(rows) == 30
    with app_session_factory() as session:
        result = verify_audit_chain(session)
    assert result.ok, result.errors
    assert len({row.prev_hash for row in rows}) == 30  # no fork
    lines = _spool_lines(layout)
    assert [json.loads(line)["id"] for line in lines] == [row.id for row in rows]
    assert verify_audit_lines(lines, prev_hash=GENESIS_HASH).ok


def test_tampering_in_the_database_is_detected(
    app_session_factory: sessionmaker[Session], db_engine: Engine
) -> None:
    for number in range(5):
        with app_session_factory() as session:
            record_audit(session, actor=TOLGA, action=f"a{number}", details={"n": number})
            session.commit()
    rows = _rows(app_session_factory)
    with db_engine.begin() as connection:  # only the table owner can lift the guard
        connection.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
        connection.execute(
            text("UPDATE audit_log SET details = '{\"n\": 99}' WHERE id = :id"), {"id": rows[2].id}
        )
        connection.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": rows[3].id})
        connection.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))
    with app_session_factory() as session:
        result = verify_audit_chain(session)
    assert not result.ok
    assert result.first_bad_id == rows[2].id
    assert any("modified" in error for error in result.errors)
    assert any(f"#{rows[4].id}: prev_hash" in error for error in result.errors)


def test_tampering_in_spool_lines_is_detected(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    for number in range(4):
        with app_session_factory() as session:
            record_audit(session, actor=TOLGA, action="line", details={"n": number})
            session.commit()
    lines = _spool_lines(layout)
    assert verify_audit_lines(lines).ok
    edited = [*lines]
    edited[1] = edited[1].replace(b'"n":1', b'"n":7')
    assert not verify_audit_lines(edited).ok
    swapped = [lines[0], lines[2], lines[1], lines[3]]
    assert not verify_audit_lines(swapped).ok
    assert not verify_audit_lines([*lines[:2], *lines[3:]]).ok
    pretty = [json.dumps(json.loads(line), indent=1).encode() for line in lines]
    assert not verify_audit_lines(pretty).ok  # not canonical
    assert not verify_audit_lines([b"not json"]).ok


def test_flush_is_idempotent_and_catches_up(
    app_session_factory: sessionmaker[Session], db_engine: Engine, layout: Layout
) -> None:
    for number in range(3):
        with app_session_factory() as session:
            record_audit(session, actor=TOLGA, action=f"f{number}")
            session.commit()
    assert flush_audit_spool(db_engine, layout) == 0
    # Crash after appending, before the watermark was saved: nothing is written twice.
    with app_session_factory() as session:
        session.execute(delete(Setting).where(Setting.key == audit.WATERMARK_KEY))
        session.commit()
    assert flush_audit_spool(db_engine, layout) == 0
    assert len(_spool_lines(layout)) == 3
    # A lost spool file (flush failed) is rebuilt from the database.
    for path in (layout.spool / "_audit").glob("*.jsonl"):
        path.unlink()
    with app_session_factory() as session:
        session.execute(delete(Setting).where(Setting.key == audit.WATERMARK_KEY))
        session.commit()
    assert flush_audit_spool(db_engine, layout) == 3
    assert verify_audit_lines(_spool_lines(layout), prev_hash=GENESIS_HASH).ok


def _disk_full(path: Path, data: bytes) -> None:
    raise OSError("disk full")


def test_failed_flush_does_not_fail_the_commit(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "_append", _disk_full)
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="while.disk.full")
        session.commit()
    assert len(_rows(app_session_factory)) == 1
    assert _spool_lines(layout) == []
    monkeypatch.undo()
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="next")
        session.commit()
    assert [json.loads(x)["action"] for x in _spool_lines(layout)] == ["while.disk.full", "next"]


def _at(day: date, hour: int) -> Callable[[], datetime]:
    return lambda: datetime.combine(day, time(hour), tzinfo=SITE_TZ).astimezone(UTC)


def _torn_append(path: Path, data: bytes) -> None:
    """A power cut in the middle of the append: half of the bytes reach the disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(data[: len(data) // 2])
    raise OSError("power cut")


def test_torn_tail_is_cut_before_the_next_append(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for number in range(2):
        with app_session_factory() as session:
            record_audit(session, actor=TOLGA, action=f"a{number}")
            session.commit()
    monkeypatch.setattr(audit, "_append", _torn_append)
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="torn")
        session.commit()  # the entry is committed; only its spool line is torn
    monkeypatch.undo()
    (path,) = (layout.spool / "_audit").glob("*.jsonl")
    assert not path.read_bytes().endswith(b"\n")
    assert flush_audit_spool(db_engine, layout) == 1
    data = path.read_bytes()
    assert data.endswith(b"\n")
    lines = data.splitlines()
    assert [json.loads(line)["action"] for line in lines] == ["a0", "a1", "torn"]
    assert verify_audit_lines(lines, prev_hash=GENESIS_HASH).ok
    assert repair_torn_tail(path) == 0  # nothing left to repair


def test_torn_tail_of_a_closed_day_is_repaired_and_then_sealed(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    api_config: Config,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Crash at 23:00 mid-append: the sealer refuses the torn file, the API repairs it after
    midnight and writes the entry into the new day's file, then the day is sealed."""
    day, next_day = date(2026, 10, 12), date(2026, 10, 13)
    monkeypatch.setattr(audit, "utc_now", _at(day, 22))
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="evening")
        session.commit()
    monkeypatch.setattr(audit, "utc_now", _at(day, 23))
    append = audit._append
    monkeypatch.setattr(audit, "_append", _torn_append)
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="crash")
        session.commit()
    monkeypatch.setattr(audit, "_append", append)
    config = Config.model_validate(
        {**api_config.model_dump(), "seal": {"quiet_seconds": 0}, "tsa": {"provider": "mock"}}
    )
    tsa = MockTsa(layout.state / "mock-tsa", key_type="ec")

    def sealer(hour: int) -> Sealer:
        return Sealer(
            config,
            provider=MockTsaProvider(tsa),
            roots=[tsa.root_certificate],
            clock=_at(next_day, hour),
            clock_status=lambda: None,
        )

    refused = sealer(0).seal_day(day)
    assert refused.status == "pending"
    assert "incomplete" in refused.message
    monkeypatch.setattr(audit, "utc_now", _at(next_day, 1))
    assert flush_audit_spool(db_engine, layout) == 1
    assert sealer(2).seal_day(day).status == "sealed"
    vault = gzip.decompress((layout.vault_day(day) / "audit.jsonl.gz").read_bytes()).splitlines()
    moved = layout.audit_spool(next_day).read_bytes().splitlines()
    assert [json.loads(x)["action"] for x in vault] == ["evening"]
    assert [json.loads(x)["action"] for x in moved] == ["crash"]
    assert verify_audit_lines([*vault, *moved], prev_hash=GENESIS_HASH).ok
    assert json.loads(layout.manifest(day).read_bytes())["audit_log"]["entries"] == 1


def test_last_spooled_id_skips_torn_and_foreign_lines(tmp_path: Path) -> None:
    path = tmp_path / "2026-10-12.jsonl"
    long_line = json.dumps({"id": 2, "details": {"x": "y" * 200_000}}).encode()
    path.write_bytes(b'{"id": 1}\n' + long_line + b"\n" + b'{"id": 3, "trunc')
    assert audit._last_line_id(path) == 2  # the torn {"id": 3 … is no entry yet
    path.write_bytes(b'{"id": 1}\n' + long_line + b"\nnot json\n")
    assert audit._last_line_id(path) == 2
    path.write_bytes(b"garbage without newline")
    assert audit._last_line_id(path) is None
    assert repair_torn_tail(path) == len(b"garbage without newline")
    assert path.read_bytes() == b""
    assert audit._last_line_id(tmp_path / "missing.jsonl") is None


def test_audit_refuses_a_snapshot_isolation_transaction(
    app_session_factory: sessionmaker[Session], db_engine: Engine
) -> None:
    """A REPEATABLE READ snapshot taken before the chain lock would fork the chain."""
    with app_session_factory() as session:
        session.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        with pytest.raises(RuntimeError, match="READ COMMITTED"):
            record_audit(session, actor=TOLGA, action="x")
    # And the schema itself refuses a second entry continuing the same predecessor.
    insert = text(
        "INSERT INTO audit_log (time, action, details, prev_hash, hash) "
        "VALUES (now(), 'x', '{}', :p, :h)"
    )
    with db_engine.begin() as connection:
        connection.execute(insert, {"p": GENESIS_HASH, "h": "a" * 64})
    with pytest.raises(IntegrityError, match="uq_audit_log_prev_hash"), db_engine.begin() as c:
        c.execute(insert, {"p": GENESIS_HASH, "h": "b" * 64})


def test_every_settings_change_can_be_audited(app_session_factory: sessionmaker[Session]) -> None:
    """Spec 8.5 audits settings changes with old and new value: every shipped value fits."""
    with app_session_factory() as session:
        for key, value in DEFAULT_SETTINGS.items():
            record_audit(
                session,
                actor=TOLGA,
                action="settings.change",
                target=f"setting:{key}",
                details={"old": value, "new": value},
            )
        session.commit()
        assert verify_audit_chain(session).checked == len(DEFAULT_SETTINGS)


def test_app_start_catches_up_an_entry_whose_flush_failed(
    app: FastAPI,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(audit, "_append", _disk_full)
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="while.disk.full")
        session.commit()
    monkeypatch.undo()
    assert _spool_lines(layout) == []
    with TestClient(app):  # no further audited action: the lifespan flush catches up
        deadline = monotonic() + 15
        while not _spool_lines(layout) and monotonic() < deadline:
            sleep(0.05)
    assert [json.loads(x)["action"] for x in _spool_lines(layout)] == ["while.disk.full"]


def test_periodic_flush_survives_errors_and_stops(
    db_engine: Engine, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[int] = []

    def flaky(engine: Engine, spool: Layout) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise OSError("disk full")
        return 0

    monkeypatch.setattr(audit, "flush_audit_spool", flaky)

    async def scenario() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(
            run_periodic_flush(lambda: db_engine, layout, stop, interval=0.01)
        )
        while len(calls) < 3:
            await asyncio.sleep(0.01)
        stop.set()
        await asyncio.wait_for(task, timeout=5)

    asyncio.run(scenario())
    assert len(calls) >= 3


def test_lines_go_to_the_day_they_are_written_never_to_a_closed_day(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day, next_day = date(2026, 10, 12), date(2026, 10, 13)
    monkeypatch.setattr(audit, "utc_now", _at(day, 23))  # 23:00 Istanbul = 20:00 UTC
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="late.evening")
        session.commit()
    assert [json.loads(x)["action"] for x in layout.audit_spool(day).read_bytes().splitlines()] == [
        "late.evening"
    ]
    # An entry of day 12 whose flush failed is caught up after midnight: it goes into day 13's
    # file, day 12's file (which the sealer takes after midnight) is never touched again.
    monkeypatch.setattr(audit, "_append", _disk_full)
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="just.before.midnight")
        session.commit()
    monkeypatch.undo()
    monkeypatch.setattr(audit, "utc_now", _at(next_day, 0))
    before = layout.audit_spool(day).read_bytes()
    assert flush_audit_spool(db_engine, layout) == 1
    assert layout.audit_spool(day).read_bytes() == before
    caught_up = layout.audit_spool(next_day).read_bytes().splitlines()
    assert [json.loads(x)["action"] for x in caught_up] == ["just.before.midnight"]
    lines = [*before.splitlines(), *caught_up]
    assert verify_audit_lines(lines, prev_hash=GENESIS_HASH).ok  # one chain across the files


def test_duplicate_guard_looks_into_the_sealed_file(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day, next_day = date(2026, 10, 12), date(2026, 10, 13)
    monkeypatch.setattr(audit, "utc_now", _at(day, 22))
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="sealed.later")
        session.commit()
    # Crash after the append, before the watermark was saved; then the day is sealed.
    with app_session_factory() as session:
        session.execute(delete(Setting).where(Setting.key == audit.WATERMARK_KEY))
        session.commit()
    sealed = layout.vault_day(day) / "audit.jsonl.gz"
    sealed.parent.mkdir(parents=True)
    sealed.write_bytes(gzip.compress(layout.audit_spool(day).read_bytes()))
    layout.audit_spool(day).unlink()
    monkeypatch.setattr(audit, "utc_now", _at(next_day, 1))
    assert flush_audit_spool(db_engine, layout) == 0
    assert not layout.audit_spool(next_day).exists()


def test_sealer_seals_the_audit_spool(
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    day = date(2026, 10, 12)
    for hour in (9, 12, 17):
        monkeypatch.setattr(audit, "utc_now", _at(day, hour))
        with app_session_factory() as session:
            record_audit(session, actor=TOLGA, action="lookup.run", details={"hour": hour})
            session.commit()
    rows = _rows(app_session_factory)
    assert all(site_day(row.time) == day for row in rows)
    config = Config.model_validate(
        {**api_config.model_dump(), "seal": {"quiet_seconds": 0}, "tsa": {"provider": "mock"}}
    )
    tsa = MockTsa(layout.state / "mock-tsa", key_type="ec")
    sealer = Sealer(
        config,
        provider=MockTsaProvider(tsa),
        roots=[tsa.root_certificate],
        clock=_at(day + timedelta(days=1), 0),
        clock_status=lambda: None,
    )
    outcome = sealer.seal_day(day)
    assert outcome.status == "sealed", outcome.message
    manifest = json.loads(layout.manifest(day).read_bytes())
    assert manifest["audit_log"]["entries"] == 3
    assert manifest["audit_log"]["last_entry_hash"] == rows[-1].hash
    assert not layout.audit_spool(day).exists()  # the sealer took it over
    vault_lines = gzip.decompress((layout.vault_day(day) / "audit.jsonl.gz").read_bytes())
    check = verify_audit_lines(vault_lines.splitlines(), prev_hash=GENESIS_HASH)
    assert (check.ok, check.last_hash) == (True, rows[-1].hash)
    assert verify_day(layout, day, [tsa.root_certificate], deep=True).ok
    with app_session_factory() as session:
        stored = [entry_of(r) for r in session.scalars(select(AuditLog).order_by(AuditLog.id))]
    assert [json.loads(x)["id"] for x in vault_lines.splitlines()] == [e["id"] for e in stored]


def test_endpoint_audit_reaches_the_spool(
    app: FastAPI,
    client: TestClient,
    login_as: Callable[..., LoggedIn],
    layout: Layout,
) -> None:
    router = APIRouter(prefix="/api/v1/_audited")

    @router.post("/do")
    def do(request: Request, user: CurrentUserDep, db: DbSession) -> dict[str, int]:
        row = record_audit(
            db,
            actor=user,
            action="settings.change",
            target="setting:lookup.tolerance_seconds",
            details={"old": 60, "new": 90},
            ip=client_ip(request),
        )
        return {"id": row.id}

    app.include_router(router)
    session = login_as(client, "admin")
    response = client.post("/api/v1/_audited/do")
    assert response.status_code == 200
    (line,) = _spool_lines(layout)
    entry = json.loads(line)
    assert entry["id"] == response.json()["id"]
    assert (entry["actor"], entry["actor_id"]) == (session.username, session.user_id)
    assert entry["details"] == {"new": 90, "old": 60}


# -- the after-commit flush never takes a second pooled connection --------------------------------


def test_audited_request_with_a_pool_of_one_connection(
    api_config: Config, db_engine: Engine
) -> None:
    """Regression (B5 report): the after-commit flush ran while the request still held its
    connection and took a second one. With ``pool_size=1`` the request then waited the whole
    pool timeout and the flush failed, so the entry reached the spool only with the periodic
    flush. The flush now runs after the connection went back to the pool."""
    # A long pool timeout tells a request that waited for the pool (the bug) from a slow runner
    # (a Windows CI runner under coverage took 3 s for the three requests).
    engine = engine_from_url(db_engine.url, pool_size=1, max_overflow=0, pool_timeout=30)
    try:
        app = create_app(api_config, engine)
        router = APIRouter(prefix="/api/v1/_audited")

        @router.post("/do")
        def do(request: Request, user: CurrentUserDep, db: DbSession) -> dict[str, int]:
            row = record_audit(db, actor=user, action="lookup.run", ip=client_ip(request))
            return {"id": row.id}

        app.include_router(router)
        state: AppState = app.state.arci
        factory = state.session_factory()
        client = TestClient(app, base_url="http://testserver")  # no lifespan: no periodic flush
        api_testing.login(client, factory, api_testing.make_user(factory, "admin"))
        started = monotonic()
        responses = [client.post("/api/v1/_audited/do") for _ in range(3)]
        elapsed = monotonic() - started
        assert [r.status_code for r in responses] == [200, 200, 200]
        assert elapsed < 20  # no request waited for the pool timeout (30 s)
        layout = Layout(api_config.data_dir)
        assert [json.loads(line)["id"] for line in _spool_lines(layout)] == [
            r.json()["id"] for r in responses
        ]
        assert isinstance(engine.pool, QueuePool)
        assert engine.pool.checkedout() == 0
    finally:
        engine.dispose()


def test_savepoints_neither_flush_early_nor_forget_the_outer_entries(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="outer")
        savepoint = session.begin_nested()
        record_audit(session, actor=TOLGA, action="inner.rolled.back")
        savepoint.rollback()
        with session.begin_nested():
            pass  # a savepoint commit is not the commit of the entries
        assert _spool_lines(layout) == []
        session.commit()
    assert [json.loads(x)["action"] for x in _spool_lines(layout)] == ["outer"]


def test_rolled_back_entries_are_not_flushed_by_a_later_commit(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    with app_session_factory() as session:
        record_audit(session, actor=TOLGA, action="discarded")
        session.rollback()
        session.execute(text("SELECT 1"))
        session.commit()
    assert _spool_lines(layout) == []
    assert _rows(app_session_factory) == []
