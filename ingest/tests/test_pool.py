"""Parse workers produce exactly what the service process would, and failures fall back."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import sys
import threading
import time
from concurrent.futures import Future, ProcessPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from arcilogger_core.rawlog import format_line
from arcilogger_ingest.pool import PendingChunk, Preparer, _ignore_stop_signals
from arcilogger_ingest.prepare import PreparedChunk, StreamInfo, prepare_chunk
from arcilogger_ingest.testing import dhcp_message, fortigate_message

STREAM = StreamInfo("fortigate", "fortigate", date(2026, 9, 23))
START = datetime(2026, 9, 23, 8, 0, tzinfo=timezone(timedelta(hours=3)))


def chunk_data(first: int, count: int) -> bytes:
    return b"".join(
        format_line(
            START + timedelta(milliseconds=i),
            "172.31.255.1",
            fortigate_message(i) if i % 3 else dhcp_message(i),
        )
        for i in range(first, first + count)
    )


def same(a: PreparedChunk, b: PreparedChunk) -> None:
    assert (a.end_offset, a.end_line) == (b.end_offset, b.end_line)
    assert [(x.line, x.offset, x.rows, x.failures) for x in a.lines] == [
        (y.line, y.offset, y.rows, y.failures) for y in b.lines
    ]


def test_workers_prepare_exactly_what_the_service_process_would() -> None:
    preparer = Preparer(workers=2)
    try:
        pending = []
        offset = 0
        for index in range(4):
            data = chunk_data(index * 50, 50) + (b"garbage\n" if index == 2 else b"")
            pending.append((preparer.submit(STREAM, offset, index * 51, data), offset, data))
            offset += len(data)
        for chunk, start, data in pending:
            same(chunk.result(), prepare_chunk(STREAM, start, chunk.line_count, data))
    finally:
        preparer.close()


def test_a_dead_worker_means_the_chunk_is_prepared_here() -> None:
    data = chunk_data(0, 10)
    future: Future[PreparedChunk] = Future()
    future.set_exception(BrokenProcessPool("worker died"))
    pending = PendingChunk(STREAM, 0, 0, data, future)
    same(pending.result(), prepare_chunk(STREAM, 0, 0, data))
    assert pending.result() is pending.result()  # computed once, bytes released
    assert pending.data == b""


def test_a_broken_pool_falls_back_to_the_service_process_until_it_is_replaced() -> None:
    now = [100.0]
    preparer = Preparer(workers=1, backoff=5, max_backoff=8, clock=lambda: now[0])
    pool = preparer._pool
    assert pool is not None
    pool.shutdown()  # e.g. the workers were killed
    data = chunk_data(0, 5)
    pending = preparer.submit(STREAM, 0, 0, data)
    same(pending.result(), prepare_chunk(STREAM, 0, 0, data))
    assert preparer._pool is None
    assert preparer.status() == {
        "configured": 1,
        "active": 0,
        "restarts": 0,
        "last_error": "RuntimeError: cannot schedule new futures after shutdown",
    }
    now[0] += 4.9  # still waiting: prepared here
    assert preparer.submit(STREAM, 0, 0, data).data == b""
    assert preparer.active_workers == 0
    now[0] += 0.2  # the wait is over: a new pool
    replaced = preparer.submit(STREAM, 0, 0, data)
    same(replaced.result(), prepare_chunk(STREAM, 0, 0, data))
    assert (preparer.active_workers, preparer.restarts) == (1, 1)
    second = _pool_of(preparer)
    assert second is not None
    second.shutdown()
    preparer.submit(STREAM, 0, 0, data)
    assert preparer._retry_at == now[0] + 8  # 5 doubled, capped at 8
    preparer.close()
    now[0] += 100
    preparer.submit(STREAM, 0, 0, data)
    assert preparer._pool is None  # never recreated after close


def _pool_of(preparer: Preparer) -> ProcessPoolExecutor | None:
    return preparer._pool


def _workers_alive(preparer: Preparer) -> list[int]:
    pool = _pool_of(preparer)
    assert pool is not None
    processes = pool._processes or {}
    return [pid for pid, process in processes.items() if process.is_alive()]


def test_a_killed_worker_is_replaced() -> None:
    """One parse worker dies (antivirus, out of memory, an external kill): the chunks are still
    prepared — never lost, never blocked — and the pool is replaced after the backoff."""
    preparer = Preparer(workers=2, backoff=0.1)
    try:
        data = chunk_data(0, 20)
        same(preparer.submit(STREAM, 0, 0, data).result(), prepare_chunk(STREAM, 0, 0, data))
        first = preparer._pool
        alive = _workers_alive(preparer)
        assert alive
        assert first is not None
        first._processes[alive[0]].kill()

        results: list[PreparedChunk] = []

        def keep_submitting() -> None:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                results.append(preparer.submit(STREAM, 0, 0, data).result())
                if preparer.restarts >= 1 and preparer._pool is not first:
                    return
                time.sleep(0.05)

        worker = threading.Thread(target=keep_submitting, daemon=True)
        worker.start()
        worker.join(30)
        assert not worker.is_alive(), "the service blocked after a worker died"
        for result in results:
            same(result, prepare_chunk(STREAM, 0, 0, data))
        assert preparer.restarts == 1
        assert preparer.active_workers == 2
        assert preparer.last_error is not None
        same(preparer.submit(STREAM, 0, 0, data).result(), prepare_chunk(STREAM, 0, 0, data))
    finally:
        preparer.close()


def test_inline_preparer_prepares_at_once() -> None:
    preparer = Preparer(workers=0)
    pending = preparer.submit(STREAM, 0, 0, chunk_data(0, 3))
    assert pending.data == b""  # already done
    assert len(pending.result().lines) == 3
    preparer.close()


def test_workers_ignore_stop_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[int, object]] = []
    monkeypatch.setattr(signal, "signal", lambda signum, handler: calls.append((signum, handler)))
    _ignore_stop_signals()
    assert (signal.SIGINT, signal.SIG_IGN) in calls
    if sys.platform == "win32":
        assert len(calls) == 2  # SIGINT and SIGBREAK (console Ctrl+C / Ctrl+Break)
    else:
        # SIGTERM must stay deliverable: CPython ends the surviving workers of a broken pool
        # with it while holding the pool's lock.
        assert calls == [(signal.SIGINT, signal.SIG_IGN)]


_SERVICE = """
import time
from datetime import date, datetime, timezone
from arcilogger_core.rawlog import format_line
from arcilogger_ingest.pool import Preparer
from arcilogger_ingest.prepare import StreamInfo

stream = StreamInfo("fortigate", "fortigate", date(2026, 10, 12))
line = format_line(datetime(2026, 10, 12, 5, tzinfo=timezone.utc), "172.31.255.1", b"<189>x")
preparer = Preparer(workers=2)
for _ in range(4):
    preparer.submit(stream, 0, 0, line).result()
print(" ".join(str(pid) for pid in preparer._pool._processes), flush=True)
time.sleep(120)
"""


def _cmdline(pid: int) -> bytes:
    try:
        return Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return b""


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
    except (OSError, IndexError):
        return False
    return state != "Z"


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_workers_end_when_the_service_process_is_killed() -> None:
    """A killed service (crash, kill, service wrapper timeout) leaves no orphaned parse
    workers waiting for work for ever."""
    service = subprocess.Popen([sys.executable, "-c", _SERVICE], stdout=subprocess.PIPE, text=True)
    pids: list[int] = []
    survivors: list[int] = []
    try:
        assert service.stdout is not None
        pids = [int(pid) for pid in service.stdout.readline().split()]
        assert pids
        assert all(_alive(pid) for pid in pids)
        service.kill()
        service.wait(30)
        deadline = time.monotonic() + 20
        while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
            time.sleep(0.1)
        survivors = [pid for pid in pids if _alive(pid)]
    finally:
        if service.poll() is None:
            service.kill()
            service.wait(30)
        # Never leave orphans on the machine, also when this test fails (workers of a broken
        # version may ignore SIGTERM): SIGKILL what is still a parse worker of this test.
        if sys.platform != "win32":
            for pid in pids:
                if _alive(pid) and b"multiprocessing" in _cmdline(pid):
                    with contextlib.suppress(OSError):
                        os.kill(pid, signal.SIGKILL)
    assert not survivors, "orphaned parse workers"
