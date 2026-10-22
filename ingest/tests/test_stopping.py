"""Graceful stop: which events stop the service, and the batch in flight is loaded first."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

import pytest
from sqlalchemy import Engine

from arcilogger_ingest.service import EXIT_OK
from arcilogger_ingest.stopping import (
    CTRL_BREAK_EVENT,
    CTRL_C_EVENT,
    CTRL_CLOSE_EVENT,
    CTRL_LOGOFF_EVENT,
    CTRL_SHUTDOWN_EVENT,
    console_event_action,
    handle_console_event,
    install_stop_handlers,
)
from arcilogger_ingest.testing import DAY, World, check_exactly_once, fortigate_message, loaded


@pytest.mark.parametrize(
    ("event", "action"),
    [
        (CTRL_C_EVENT, "stop"),
        (CTRL_BREAK_EVENT, "stop"),
        (CTRL_CLOSE_EVENT, "stop_and_wait"),
        (CTRL_SHUTDOWN_EVENT, "stop_and_wait"),
        # a service receives LOGOFF whenever any user logs off the server: not a reason to stop
        (CTRL_LOGOFF_EVENT, "ignore"),
        (99, "ignore"),
    ],
)
def test_console_event_action(event: int, action: str) -> None:
    assert console_event_action(event) == action


def test_close_event_waits_until_the_loop_has_stopped() -> None:
    stop, stopped = threading.Event(), threading.Event()
    threading.Timer(0.1, stopped.set).start()
    started = time.monotonic()
    assert handle_console_event(CTRL_CLOSE_EVENT, stop, stopped, grace=5.0) is True
    waited = time.monotonic() - started
    assert stop.is_set()
    assert 0.05 <= waited < 4.0  # returned when the loop was done, not after the grace


def test_close_event_gives_up_after_the_grace_period() -> None:
    stop, stopped = threading.Event(), threading.Event()
    started = time.monotonic()
    assert handle_console_event(CTRL_SHUTDOWN_EVENT, stop, stopped, grace=0.2) is True
    assert time.monotonic() - started >= 0.2
    assert stop.is_set()


def test_ctrl_c_returns_at_once_and_logoff_is_ignored() -> None:
    stop, stopped = threading.Event(), threading.Event()
    assert handle_console_event(CTRL_LOGOFF_EVENT, stop, stopped, grace=5.0) is True
    assert not stop.is_set()
    started = time.monotonic()
    assert handle_console_event(CTRL_C_EVENT, stop, stopped, grace=5.0) is True
    assert time.monotonic() - started < 1.0
    assert stop.is_set()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signals")
@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_posix_signals_set_the_stop_flag(signum: int) -> None:
    stop, stopped = threading.Event(), threading.Event()
    before = signal.getsignal(signum)
    uninstall = install_stop_handlers(stop, stopped)
    try:
        os.kill(os.getpid(), signum)
        assert stop.wait(5)
    finally:
        uninstall()
    assert signal.getsignal(signum) == before


def test_stop_loads_the_batch_that_is_still_being_collected(
    world: World, db_engine: Engine
) -> None:
    """A batch that is not due yet (batch_seconds not reached) is loaded before exiting."""
    world.reconfigure(ingest={"batch_rows": 5000, "batch_seconds": 60, "poll_seconds": 0.02})
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(25)))
    service = world.service(db_engine)
    results: list[int] = []
    thread = threading.Thread(target=lambda: results.append(service.run()))
    thread.start()
    try:
        deadline = time.monotonic() + 20
        while service._head.lines < 25 and time.monotonic() < deadline:
            time.sleep(0.01)
        assert loaded(db_engine) == []  # read, but not loaded yet
    finally:
        service.stop.set()
        thread.join(20)
    assert results == [EXIT_OK]
    assert check_exactly_once(db_engine, world) == []
    assert service.stopped.is_set()


def test_stop_during_a_load_finishes_that_load_and_nothing_more(
    world: World, db_engine: Engine
) -> None:
    world.reconfigure(ingest={"batch_rows": 10})
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(50)))
    calls: list[str] = []

    def stop_in_second_batch(step: str) -> None:
        calls.append(step)
        if step == "copy" and calls.count("copy") == 2:
            service.stop.set()  # Ctrl+C arrives while the batch is being written

    service = world.service(db_engine, step_hook=stop_in_second_batch)
    assert service.run() == EXIT_OK
    assert len(loaded(db_engine)) == 20
    assert service.run(once=False) == EXIT_OK  # stop is still set: starts and stops at once
    assert len(loaded(db_engine)) == 20
