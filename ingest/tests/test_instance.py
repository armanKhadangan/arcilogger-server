"""One ingest instance per database (PostgreSQL advisory lock), and what a second one does.

A second instance (e.g. ``arcilogger-ingest run`` typed while the ``ArciIngest`` service runs)
loads nothing and writes no status file while the first runs; ``--once`` exits with code 4.
The lock is taken again after a database restart, and if another instance got it meanwhile the
first one steps back, waits, and continues from the offsets in the database.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator

import pytest
from sqlalchemy import Engine

from arcilogger_db.engine import engine_from_url
from arcilogger_ingest.cli import EXIT_LOCKED, main
from arcilogger_ingest.instance import InstanceLock
from arcilogger_ingest.service import EXIT_OK, IngestService
from arcilogger_ingest.status import read_status
from arcilogger_ingest.testing import (
    DAY,
    TcpProxy,
    World,
    check_exactly_once,
    dhcp_message,
    fortigate_message,
    loaded,
    write_config_file,
)


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


@pytest.fixture
def other_instance(db_engine: Engine) -> Iterator[InstanceLock]:
    """The lock as another ingest instance would hold it (released after the test)."""
    lock = InstanceLock(db_engine)
    yield lock
    lock.release()


def test_the_lock_is_exclusive_and_released_with_its_connection(db_engine: Engine) -> None:
    first, second = InstanceLock(db_engine), InstanceLock(db_engine)
    try:
        assert first.acquire()
        assert first.held
        assert first.acquire()  # checking a held lock keeps it
        assert not second.acquire()
        assert not second.held
        first.release()
        first.release()  # idempotent
        # Unlocked explicitly: free at once, not only when the closed session's backend has
        # exited (a `run --once` right after the service stopped would exit 4 on a busy server).
        assert second.acquire()
        second.release()
        for _ in range(20):
            assert first.acquire()
            first.release()
            assert second.acquire()
            second.release()
    finally:
        first.release()
        second.release()


def test_a_second_once_run_exits_4_and_writes_no_status(
    world: World,
    db_engine: Engine,
    pg_dsn: str,
    other_instance: InstanceLock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(10)))
    assert other_instance.acquire()
    config = write_config_file(world, pg_dsn)
    assert main(["run", "--config", str(config), "--once"]) == EXIT_LOCKED
    assert loaded(db_engine) == []
    assert not (world.layout.state / "ingest-status.json").exists()
    assert "another ingest instance is running" in caplog.text
    other_instance.release()
    assert main(["run", "--config", str(config), "--once"]) == EXIT_OK
    assert check_exactly_once(db_engine, world) == []


def test_a_second_service_waits_and_takes_over_when_the_first_stops(
    world: World,
    db_engine: Engine,
    other_instance: InstanceLock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level("INFO", logger="arcilogger.ingest")
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(10)))
    assert other_instance.acquire()
    running = Running(world.service(db_engine))
    status = world.layout.state / "ingest-status.json"
    try:
        wait_for(lambda: "another ingest instance is running" in caplog.text)
        time.sleep(0.5)  # several lock retries (0.2 s in tests)
        assert loaded(db_engine) == []
        assert not status.exists()  # the status file belongs to the running instance
        other_instance.release()
        wait_for(lambda: len(loaded(db_engine)) == 10)
        assert "takes over" in caplog.text
        wait_for(status.exists)
    finally:
        assert running.stop() == EXIT_OK
    data = read_status(status)
    assert data is not None
    assert data["state"] == "stopped"
    assert check_exactly_once(db_engine, world) == []
    assert other_instance.acquire()  # released on stop


def test_the_lock_is_taken_again_after_a_database_restart(
    world: World, db_engine: Engine, proxy: TcpProxy, pg_dsn: str
) -> None:
    engine = engine_from_url(proxy.dsn(pg_dsn), application="ingest-test")
    probe = InstanceLock(db_engine)
    try:
        running = Running(world.service(engine))
        try:
            world.append("core-r1", DAY, (dhcp_message(i) for i in range(5)))
            wait_for(lambda: len(loaded(db_engine)) == 5)
            assert not probe.acquire()
            proxy.down()  # the lock connection dies too: PostgreSQL drops the lock
            wait_for(probe.acquire)
            probe.release()
            proxy.up()
            world.append("core-r1", DAY, (dhcp_message(i) for i in range(5, 9)))
            wait_for(lambda: len(loaded(db_engine)) == 9)
            assert not probe.acquire()  # held again by the service
        finally:
            assert running.stop() == EXIT_OK
    finally:
        probe.release()
        engine.dispose()
    assert check_exactly_once(db_engine, world) == []


def test_lock_taken_by_another_instance_during_an_outage(
    world: World,
    db_engine: Engine,
    proxy: TcpProxy,
    pg_dsn: str,
    other_instance: InstanceLock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The first instance steps back (drops what it read), waits, and continues correctly."""
    caplog.set_level("INFO", logger="arcilogger.ingest")
    engine = engine_from_url(proxy.dsn(pg_dsn), application="ingest-test")
    try:
        running = Running(world.service(engine))
        try:
            world.append("core-r1", DAY, (dhcp_message(i) for i in range(5)))
            wait_for(lambda: len(loaded(db_engine)) == 5)
            proxy.down()
            wait_for(other_instance.acquire)  # "the other instance" starts during the outage
            proxy.up()
            world.append("core-r1", DAY, (dhcp_message(i) for i in range(5, 12)))
            wait_for(lambda: "another ingest instance is running" in caplog.text)
            time.sleep(0.5)
            assert len(loaded(db_engine)) == 5  # nothing loaded while locked out
            other_instance.release()
            wait_for(lambda: len(loaded(db_engine)) == 12)
            assert "takes over" in caplog.text
        finally:
            assert running.stop() == EXIT_OK
    finally:
        engine.dispose()
    assert check_exactly_once(db_engine, world) == []
