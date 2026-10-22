"""Database unavailable: nothing is lost, offsets do not move, recovery is automatic.

The database sits behind :class:`~arcilogger_ingest.testing.TcpProxy`; ``down()`` drops every
connection and refuses new ones (like a PostgreSQL restart), ``up()`` lets them through again.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from typing import Any

import pytest
from sqlalchemy import Connection, Engine, event
from sqlalchemy.exc import OperationalError

from arcilogger_db.bulk import copy_rows
from arcilogger_db.engine import engine_from_url
from arcilogger_ingest import loader
from arcilogger_ingest.loader import Batch, LoadResult
from arcilogger_ingest.service import EXIT_DB_UNAVAILABLE, EXIT_OK, IngestService
from arcilogger_ingest.status import read_status
from arcilogger_ingest.testing import (
    DAY,
    TcpProxy,
    World,
    check_exactly_once,
    dhcp_message,
    fortigate_message,
    loaded,
    offsets,
)


@pytest.fixture
def proxied_engine(proxy: TcpProxy, pg_dsn: str) -> Iterator[Engine]:
    engine = engine_from_url(proxy.dsn(pg_dsn), application="ingest-test")
    yield engine
    engine.dispose()


def wait_for(condition: Callable[[], bool], timeout: float = 30.0) -> None:
    deadline = time.monotonic() + timeout
    while not condition():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached in time")
        time.sleep(0.02)


class Running:
    """The service in a background thread (service mode, not ``--once``)."""

    def __init__(self, service: IngestService) -> None:
        self.service = service
        self.result: list[int] = []
        self.thread = threading.Thread(target=lambda: self.result.append(service.run()))
        self.thread.start()

    def stop(self) -> int:
        self.service.stop.set()
        self.thread.join(30)
        assert not self.thread.is_alive()
        return self.result[0]


def all_loaded(engine: Engine, world: World) -> Callable[[], bool]:
    return lambda: len(loaded(engine)) == len(world.expected())


def test_database_down_at_start_then_back(
    world: World,
    db_engine: Engine,
    proxy: TcpProxy,
    proxied_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="arcilogger.ingest")
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(50)))
    proxy.down()
    running = Running(world.service(proxied_engine))
    status_path = world.layout.state / "ingest-status.json"
    try:
        wait_for(lambda: proxy.refused >= 2)
        status = read_status(status_path)
        assert status is not None
        assert status["database"]["ok"] is False
        assert status["database"]["last_error"]
        assert offsets(db_engine) == {}  # nothing moved
        proxy.up()
        wait_for(all_loaded(db_engine, world))
    finally:
        assert running.stop() == EXIT_OK
    assert check_exactly_once(db_engine, world) == []
    assert "database unavailable, nothing is lost" in caplog.text
    assert "database available again" in caplog.text


def test_connection_lost_in_the_middle_of_a_batch(
    world: World,
    db_engine: Engine,
    proxy: TcpProxy,
    proxied_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The connection dies after the offsets UPDATE, before COPY (the hook runs before the copy
    step); the same batch is retried after the database is back."""
    caplog.set_level("INFO", logger="arcilogger.ingest")
    world.reconfigure(ingest={"batch_rows": 100})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(450)))
    calls: list[str] = []
    snapshot: dict[str, object] = {}

    def outage_during_second_copy(step: str) -> None:
        calls.append(step)
        if step == "copy" and calls.count("copy") == 2:
            snapshot["offsets"] = offsets(db_engine)
            proxy.down()
            threading.Timer(0.6, proxy.up).start()

    running = Running(world.service(proxied_engine, step_hook=outage_during_second_copy))
    try:
        wait_for(all_loaded(db_engine, world))
    finally:
        assert running.stop() == EXIT_OK
    assert check_exactly_once(db_engine, world) == []
    assert snapshot["offsets"] == {("core-r1", DAY): (snapshot_bytes(world, 100), 100, False)}
    assert calls.count("copy") >= 6  # the failed attempt(s) plus five loaded batches
    assert "retrying in" in caplog.text


def test_connection_lost_while_copy_streams_rows(
    world: World,
    db_engine: Engine,
    proxy: TcpProxy,
    proxied_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The proxy drops every connection after row 150 of the second batch's ``messages`` COPY."""
    caplog.set_level("INFO", logger="arcilogger.ingest")
    world.reconfigure(ingest={"batch_rows": 300})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(1200)))
    copies: list[int] = []
    snapshot: dict[str, object] = {}

    def copy_with_outage(
        connection: Connection,
        table: str,
        rows: Iterable[Mapping[str, Any]],
        *,
        columns: Sequence[str] | None = None,
    ) -> int:
        # Count the messages COPYs only: with the real parsers every batch also copies mt_dhcp.
        if table == "messages":
            copies.append(1)
        cut = table == "messages" and len(copies) == 2

        def rows_then_outage() -> Iterator[Mapping[str, Any]]:
            for index, row in enumerate(rows):
                if cut and index == 150:
                    snapshot["offsets"] = offsets(db_engine)
                    snapshot["loaded"] = len(loaded(db_engine))
                    proxy.down()
                    threading.Timer(0.6, proxy.up).start()
                yield row

        return copy_rows(connection, table, rows_then_outage(), columns=columns)

    monkeypatch.setattr(loader, "copy_rows", copy_with_outage)
    running = Running(world.service(proxied_engine))
    try:
        wait_for(all_loaded(db_engine, world))
    finally:
        assert running.stop() == EXIT_OK
    assert check_exactly_once(db_engine, world) == []
    assert snapshot == {
        "offsets": {("core-r1", DAY): (snapshot_bytes(world, 300), 300, False)},
        "loaded": 300,
    }
    assert len(copies) >= 5  # the broken COPY, its retry and the other three batches
    assert "database unavailable, nothing is lost" in caplog.text


def test_connection_lost_before_commit_reaches_the_server(
    world: World,
    db_engine: Engine,
    proxy: TcpProxy,
    proxied_engine: Engine,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Everything is sent, then the connection dies as COMMIT is sent: rolled back, retried."""
    world.reconfigure(ingest={"batch_rows": 100})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(400)))
    steps: list[str] = []
    dropped: list[int] = []

    def drop_at_second_commit(connection: Connection) -> None:
        if steps.count("commit") == 2 and not dropped:
            dropped.append(len(loaded(db_engine)))
            proxy.down()
            threading.Timer(0.6, proxy.up).start()

    event.listen(proxied_engine, "commit", drop_at_second_commit)
    running = Running(world.service(proxied_engine, step_hook=steps.append))
    try:
        wait_for(all_loaded(db_engine, world))
    finally:
        assert running.stop() == EXIT_OK
        event.remove(proxied_engine, "commit", drop_at_second_commit)
    assert dropped == [100]
    assert check_exactly_once(db_engine, world) == []
    assert "database unavailable, nothing is lost" in caplog.text


def test_commit_done_but_its_answer_lost(
    world: World,
    db_engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The batch was committed, but the connection died before the answer arrived.

    The service cannot know; it retries the same batch, the compare-and-set finds the offsets
    already moved, and it continues from the database — no line twice, none skipped.
    """
    world.reconfigure(ingest={"batch_rows": 100})
    world.append("core-r1", DAY, (dhcp_message(i) for i in range(400)))
    service = world.service(db_engine)
    real_load = service.loader.load
    calls: list[int] = []

    def load_then_lose_the_answer(batch: Batch) -> LoadResult:
        result = real_load(batch)
        calls.append(1)
        if len(calls) == 2:
            raise OperationalError("COMMIT", None, Exception("server closed the connection"))
        return result

    monkeypatch.setattr(service.loader, "load", load_then_lose_the_answer)
    running = Running(service)
    try:
        wait_for(all_loaded(db_engine, world))
    finally:
        assert running.stop() == EXIT_OK
    assert check_exactly_once(db_engine, world) == []
    assert offsets(db_engine)[("core-r1", DAY)][1] == 400
    assert "moved by another instance, or a lost COMMIT answer" in caplog.text


def snapshot_bytes(world: World, lines: int) -> int:
    written = world.written[("core-r1", DAY)]
    return written[lines - 1][1] + len(written[lines - 1][2])


def test_once_mode_reports_an_unreachable_database(
    world: World, proxy: TcpProxy, proxied_engine: Engine
) -> None:
    world.append("fortigate", DAY, [fortigate_message(0)])
    proxy.down()
    service = world.service(proxied_engine)
    assert service.run(once=True) == EXIT_DB_UNAVAILABLE
    assert service.counters.lines == 0


def test_once_mode_stops_at_a_failed_batch(
    world: World, db_engine: Engine, proxy: TcpProxy, proxied_engine: Engine
) -> None:
    world.reconfigure(ingest={"batch_rows": 10})
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(30)))
    calls: list[str] = []

    def down_on_second_batch(step: str) -> None:
        calls.append(step)
        if step == "offsets" and calls.count("offsets") == 2:
            proxy.down()

    service = world.service(proxied_engine, step_hook=down_on_second_batch)
    assert service.run(once=True) == EXIT_DB_UNAVAILABLE
    assert len(loaded(db_engine)) == 10
    proxy.up()
    assert world.service(proxied_engine).run(once=True) == EXIT_OK
    assert check_exactly_once(db_engine, world) == []


def test_stop_while_the_database_is_down(
    world: World, db_engine: Engine, proxy: TcpProxy, proxied_engine: Engine
) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(5)))
    running = Running(world.service(proxied_engine))
    wait_for(all_loaded(db_engine, world))
    proxy.down()
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(5, 9)))
    wait_for(lambda: running.service.db_ok is False)
    started = time.monotonic()
    assert running.stop() == EXIT_OK
    assert time.monotonic() - started < 5
    status = read_status(world.layout.state / "ingest-status.json")
    assert status is not None
    assert status["state"] == "stopped"
    assert len(loaded(db_engine)) == 5  # the unloaded lines stay in the spool
    proxy.up()
    assert world.service(proxied_engine).run(once=True) == EXIT_OK
    assert check_exactly_once(db_engine, world) == []
