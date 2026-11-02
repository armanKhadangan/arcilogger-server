"""Canlı Akış under load (contract 3.5, ``poe test-perf``): a separate writer process appends
10,000 lines a second for 10 s to two spool files while three streams are open, one of them with
a typed filter.

* The hub's CPU — the whole test process's ``process_time``, which also holds the server and the
  three readers — stays at or below 0.4 s per wall second on a core as fast as CI's runners.
* The event loop's CPU a delivered line (the SSE side: each stream's events, Starlette's and
  Uvicorn's writes), counted in lines of the gauge, stays at or below its bound: a costlier write
  path fails on a fast core and on a slow one alike.
* A gauge — a fixed piece of the hub's per-line work, run outside the hub, the fastest of
  several rounds by this thread's CPU — tells how fast this core is. Up to ``TOLERANCE`` times
  the reference the CPU a second stays at 0.4 s; on a slower core it grows with the excess beyond
  it only. A busy machine (time-slicing) does not raise the gauge: it counts CPU, not wall time.
* Every stream stays at or below its rate cap, and the rest is reported: delivered + dropped =
  matching lines, exactly (the filters here match every line, so the count is known).
* The buffer stays bounded; a line written once the burst is over arrives within 1.5 s.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import re
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.live.hub import Hub
from arcilogger_api.live.testing import LiveServer, sign_in
from arcilogger_api.testing import EXAMPLE_SITE_NAME
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import site_day

from .test_live_api import STREAM_HEADERS, Reader, traffic

pytestmark = pytest.mark.perf

LINES_PER_SECOND = 10_000
SECONDS = 10
#: The test process's CPU a wall second on a core like CI's runners (0.18 to 0.36 s/s there).
CPU_PER_SECOND = 0.4
#: The gauge (seconds a gauge line, the fastest round) on CI's runners: the slowest one seen, so
#: that every runner keeps 0.4 s/s. PROVISIONAL, from a 4-CPU cloud container whose live tail
#: took 0.30 to 0.33 s/s like CI's slower runners (gauge 15.1 to 16.0 us). CI's runners come in
#: two speed classes (0.18-0.24 and 0.29-0.36 s/s, at times for the same commit): set it, before
#: review, to the slowest ``gauge:`` figure of at least ten runs of the throughput guards on each
#: of ubuntu-latest and windows-latest, runs of both classes among them.
REFERENCE_GAUGE = 15e-6
#: A gauge up to this many times the reference leaves the CPU a second at 0.4 s (the gauge's
#: noise); beyond it the bound grows with the excess only, so a gauge that reads high never
#: loosens it by more than its excess over the tolerance.
TOLERANCE = 1.2
#: The event loop's CPU a delivered line, in gauge lines (the gauge's fastest round, on the same
#: clock), so the bound follows the core's speed: a write path 40 % costlier fails on a fast
#: runner as on a slow one. Above the costliest run, below 1.4 times the cheapest. PROVISIONAL,
#: from the same container: 6.05 to 6.83 (17 runs), 8.83 to 9.41 with every delivered line
#: 39 us costlier; set it from CI's ``event loop:`` figures (both systems) before review.
LOOP_GAUGE_LINES = 7.8
#: A cycle counts in the event loop's figure when at least this many of its lines reached the
#: streams (about 150 in a busy one).
CYCLE_LINES = 50
#: Lines in a round of the gauge (about 30 ms) and rounds before the burst and again after it.
GAUGE_LINES = 2_000
GAUGE_ROUNDS = 8
#: Windows' ``thread_time`` counts whole clock ticks of 1/64 s: each of the gauge's two runs of
#: rounds reads within one tick of its CPU.
WINDOWS_TICK = 1 / 64
#: A spool line's ``key=value`` pairs, the value quoted or not.
PAIR = re.compile(rb'([a-z]+)=("[^"]*"|[^ ]*)')
#: The writer: ``python -c WRITER <file> <file> <lines a second> <seconds> <message>``.
WRITER = """
import sys, time
from datetime import UTC, datetime
from arcilogger_core.rawlog import format_line
paths, rate, seconds, message = sys.argv[1:3], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5]
handles = [open(path, "ab") for path in paths]
batch = rate // 100 // len(handles)  # every 10 ms, to each file
began = time.monotonic()
for tick in range(seconds * 100):
    line = format_line(datetime.now(UTC), "172.31.255.1", message.encode())
    for handle in handles:
        handle.write(line * batch)
        handle.flush()
    delay = began + (tick + 1) / 100 - time.monotonic()
    if delay > 0:
        time.sleep(delay)
"""

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _current_thread = _kernel32.GetCurrentThread
    _current_thread.argtypes = []
    _current_thread.restype = wintypes.HANDLE
    _thread_cycles = _kernel32.QueryThreadCycleTime
    _thread_cycles.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.c_uint64)]
    _thread_cycles.restype = wintypes.BOOL

    def _thread_cpu() -> float:
        """This thread's CPU in clock cycles: exact, where ``thread_time`` counts whole ticks."""
        cycles = ctypes.c_uint64()
        if not _thread_cycles(_current_thread(), ctypes.byref(cycles)):
            raise ctypes.WinError(ctypes.get_last_error())
        return float(cycles.value)

else:

    def _thread_cpu() -> float:
        """This thread's CPU in seconds."""
        return time.thread_time()


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "site_name": EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
                {"name": "fortigate2", "type": "fortigate", "address": "172.31.255.2"},
            ],
        }
    )


@pytest.fixture
def server(app: FastAPI) -> Iterator[LiveServer]:
    app.state.arci_live = Hub(app.state.arci)  # the production intervals
    with LiveServer(app) as live:
        yield live


def test_three_streams_under_ten_thousand_lines_a_second(
    app: FastAPI,
    server: LiveServer,
    layout: Layout,
    app_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    hub: Hub = app.state.arci_live
    loop = LoopCpu(hub, monkeypatch)
    today = site_day(datetime.now(UTC))
    paths = [layout.spool_file(folder, today) for folder in ("fortigate", "fortigate2")]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    clients, readers, streams = [], [], []
    for q in ("", "source:fortigate,fortigate2", "type:traffic"):
        client = httpx.Client(base_url=server.url, timeout=10)
        sign_in(client, app_session_factory)
        response = client.post("/api/v1/live/streams", json={"q": q, "backfill": 0})
        assert response.status_code == 201, response.text
        clients.append(client)
        streams.append(response.json()["stream_id"])
        readers.append(Reader(client, streams[-1]))
    try:
        for reader in readers:
            reader.wait(lambda r: bool(r.of("hello")))
        rounds = _gauge(GAUGE_ROUNDS)
        cpu, wall, (served, sent) = time.process_time(), time.monotonic(), loop.now()
        writer = subprocess.run(
            [
                sys.executable,
                "-c",
                WRITER,
                *map(str, paths),
                str(LINES_PER_SECOND),
                str(SECONDS),
                traffic(),
            ],
            check=True,
            timeout=SECONDS + 30,
        )
        assert writer.returncode == 0
        written = LINES_PER_SECOND * SECONDS
        # the hub catches up: every line shown or counted, nothing left in a queue
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not all(
            _settled(hub, stream_id, written) for stream_id in streams
        ):
            time.sleep(0.05)
        used, elapsed = time.process_time() - cpu, time.monotonic() - wall
        spent, delivered = loop.now()
        cycles = loop.per_line(served)
        gauge = Gauge.of(rounds + _gauge(GAUGE_ROUNDS))
        # a core slower than the tolerance allows raises the bound by its excess only
        scale = max(1.0, gauge.seconds / (TOLERANCE * REFERENCE_GAUGE))
        # In gauge lines, the median of the busy cycles: a pause in one of them (a garbage
        # collection, a neighbour) does not move it. A run that delivered nothing fails below,
        # after the first assertions.
        line = statistics.median(cycles) / gauge.cpu if cycles else float("inf")
        whole = (spent - served) / max(delivered - sent, 1) / gauge.cpu
        # Shown also when the test passes (CI's log), as the ingest throughput gate does.
        with capsys.disabled():
            print(
                f"\nlive tail: {used:.2f} s CPU in {elapsed:.2f} s ({used / elapsed:.2f} s/s), "
                f"{len(hub.buffer)} lines buffered"
            )
            for stream_id in streams:
                stream = hub.streams[stream_id]
                print(
                    f"  {stream.query or '(empty)'}: {stream.delivered} delivered, {stream.dropped}"
                )
            print(
                f"  gauge: {gauge.seconds * 1e6:.2f} us a line (fastest of {len(gauge.rounds)} "
                f"rounds, wall {gauge.wall * 1e6:.2f}; reference {REFERENCE_GAUGE * 1e6:.2f}), "
                f"scale {scale:.2f}, budget {CPU_PER_SECOND * scale:.2f} s/s"
            )
            print(
                f"  event loop: {line:.2f} gauge lines a delivered line (median of {len(cycles)} "
                f"cycles; {whole:.2f} over the whole span), about {line * gauge.seconds * 1e6:.1f} "
                f"us (limit {LOOP_GAUGE_LINES:.2f})"
            )
        for stream_id in streams:
            stream = hub.streams[stream_id]
            assert stream.delivered + sum(stream.dropped.values()) == written, stream.query
            assert stream.delivered <= stream.settings.max_lines_per_second * (elapsed + 1)
        typed = hub.streams[streams[2]]
        assert typed.dropped.get("parse_budget", 0) > 0  # most lines were beyond the parse budget
        assert len(hub.buffer) <= hub.settings.buffer_lines
        assert used / elapsed <= CPU_PER_SECOND * scale
        assert line <= LOOP_GAUGE_LINES
        # once the burst is over, a single line arrives at once
        time.sleep(1.5)
        before = len(readers[0].lines())
        with paths[0].open("ab") as handle:
            handle.write(
                format_line(
                    datetime.now(UTC), "172.31.255.1", traffic(srcip="172.20.41.7").encode()
                )
            )
        began = time.monotonic()
        readers[0].wait(lambda r: len(r.lines()) > before, seconds=5)
        assert time.monotonic() - began <= 1.5
        assert readers[0].lines()[-1]["summary"]["srcip"] == "172.20.41.7"
    finally:
        for reader in readers:
            reader.close()
        for client, stream_id in zip(clients, streams, strict=True):
            client.delete(f"/api/v1/live/streams/{stream_id}", headers=STREAM_HEADERS)
            client.close()


def _settled(hub: Hub, stream_id: str, written: int) -> bool:
    """Every written line of the stream is delivered or counted, and its queue is empty."""
    stream = hub.streams.get(stream_id)
    if stream is None or stream.connection is None or not stream.connection.queue.empty():
        return False
    return stream.delivered + sum(stream.dropped.values()) == written


class LoopCpu:
    """The event loop thread's CPU outside the hub's read cycles (``_thread_cpu``), from now on.

    ``Hub._cycle`` runs on the loop for every *written* line (buffer, filters, queues); its own
    CPU is left out. What is left is the SSE side, a delivered line at a time: each stream's
    ``events``, the frame, the response's, the middlewares' and Uvicorn's writes, and waking the
    streams' tasks. Other tasks that run on the loop while a cycle waits for its read thread are
    counted, not left out with the cycle. A cycle hands its lines to the streams, which send them
    before the next cycle starts (0.5 s later), so the figure is also taken cycle by cycle.
    """

    def __init__(self, hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hub = hub
        #: The cycles' own CPU so far, their waits for the read thread left out.
        self.cycles = 0.0
        #: The loop's CPU so far while a cycle waited for its read thread (other tasks' work).
        self.waits = 0.0
        #: At each cycle's start: the loop's CPU outside the cycles so far, and lines delivered.
        self.marks: list[tuple[float, int]] = []
        cycle, to_thread = hub._cycle, asyncio.to_thread

        async def timed_cycle() -> None:
            began, waits = _thread_cpu(), self.waits
            self.marks.append((began - self.cycles, self._delivered()))
            await cycle()
            self.cycles += _thread_cpu() - began - (self.waits - waits)

        async def timed_to_thread[T](func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
            if func != hub._read:
                return await to_thread(func, *args, **kwargs)
            began = _thread_cpu()
            try:
                return await to_thread(func, *args, **kwargs)
            finally:
                self.waits += _thread_cpu() - began

        monkeypatch.setattr(hub, "_cycle", timed_cycle)
        monkeypatch.setattr(asyncio, "to_thread", timed_to_thread)

    def _delivered(self) -> int:
        return sum(stream.delivered for stream in self.hub.streams.values())

    def now(self) -> tuple[float, int]:
        """The loop thread's CPU so far less the cycles' own, and the lines delivered so far:
        both read on the loop, so that no cycle ends between the readings."""

        async def read() -> tuple[float, int]:
            return _thread_cpu() - self.cycles, self._delivered()

        assert self.hub._task is not None
        loop = self.hub._task.get_loop()
        return asyncio.run_coroutine_threadsafe(read(), loop).result(timeout=10)

    def per_line(self, since: float) -> list[float]:
        """For each cycle from the CPU figure ``since`` on whose lines reached the streams (at
        least ``CYCLE_LINES``): the loop's CPU outside the cycles from its start to the next
        cycle's, a line delivered in that time."""
        marks = [mark for mark in self.marks if mark[0] >= since]
        return [
            (cpu - began) / (lines - before)
            for (began, before), (cpu, lines) in itertools.pairwise(marks)
            if lines - before >= CYCLE_LINES
        ]


@dataclass(frozen=True)
class Round:
    """A round of the gauge: its wall time, its ``thread_time`` and its ``_thread_cpu``."""

    wall: float
    thread: float
    cpu: float


@dataclass(frozen=True)
class Gauge:
    """The gauge a line: in seconds (for the CPU a second) and in ``_thread_cpu`` units (for the
    event loop's CPU a delivered line), the fastest round by this thread's CPU; ``wall`` is the
    fastest round's wall time, shown only."""

    rounds: list[Round]
    seconds: float
    cpu: float
    wall: float

    @classmethod
    def of(cls, rounds: list[Round]) -> Gauge:
        wall = min(r.wall for r in rounds) / GAUGE_LINES
        if sys.platform == "win32":
            # thread_time counts whole ticks there: the fastest round's wall time, but no more
            # than the rounds' CPU allows (time-slicing makes a round's wall time longer)
            lines = len(rounds) * GAUGE_LINES
            seconds = min(wall, (sum(r.thread for r in rounds) + 2 * WINDOWS_TICK) / lines)
        else:
            seconds = min(r.thread for r in rounds) / GAUGE_LINES
        return cls(rounds, seconds, min(r.cpu for r in rounds) / GAUGE_LINES, wall)


def _gauge(rounds: int) -> list[Round]:
    """Rounds of the gauge: a fixed FortiGate spool line cut, decoded, time-parsed, folded,
    searched, split into its fields and written as JSON, ``GAUGE_LINES`` times a round. It runs
    on this thread, outside the hub and outside the measured span."""
    stamp = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)
    line = format_line(stamp, "172.31.255.1", traffic().encode()).rstrip(b"\n")
    figures = []
    for _ in range(rounds):
        wall, thread, cpu, size = time.perf_counter(), time.thread_time(), _thread_cpu(), 0
        for _ in range(GAUGE_LINES):
            stamp_text, sender, message = line.split(b" ", 2)
            datetime.fromisoformat(stamp_text.decode("ascii"))
            sender.decode("ascii")
            if b'type="traffic"' in message.lower():
                fields = {
                    key.decode("ascii"): value.strip(b'"').decode()
                    for key, value in PAIR.findall(message)
                }
                size += len(json.dumps(fields, ensure_ascii=False, separators=(",", ":")))
        figures.append(
            Round(time.perf_counter() - wall, time.thread_time() - thread, _thread_cpu() - cpu)
        )
        assert size > GAUGE_LINES
    return figures
