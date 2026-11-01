"""Canlı Akış under load (contract 3.5, ``poe test-perf``): a separate writer process appends
10,000 lines a second for 10 s to two spool files while three streams are open, one of them with
a typed filter.

* The hub's CPU — the whole test process's ``process_time``, which also holds the server and the
  three readers — stays at or below 0.4 s per wall second on a core as fast as CI's runners.
* The event loop's CPU a delivered line (the SSE side: each stream's events, Starlette's and
  Uvicorn's writes) stays at or below its bound, so a costlier write path fails also on a slower
  core, where the CPU a second has more room.
* A gauge — a fixed piece of the hub's per-line work, run outside the hub, the fastest of
  several rounds — tells how fast this core is. Up to ``TOLERANCE`` times the reference both
  bounds stay as they are; on a slower core they grow with the excess beyond it only.
* Every stream stays at or below its rate cap, and the rest is reported: delivered + dropped =
  matching lines, exactly (the filters here match every line, so the count is known).
* The buffer stays bounded; a line written once the burst is over arrives within 1.5 s.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

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

T = TypeVar("T")

LINES_PER_SECOND = 10_000
SECONDS = 10
#: The test process's CPU a wall second on a core like CI's runners (0.18 to 0.36 s/s there).
CPU_PER_SECOND = 0.4
#: The event loop's CPU a delivered line on such a core, outside the hub's read cycles.
#: PROVISIONAL: 4-CPU cloud container, gauge 15.2 to 16.0 us: 95 to 99 us a line; set it from
#: the figures CI prints (``event loop:``) before review.
LOOP_CPU_PER_LINE = 125e-6
#: The gauge (seconds a gauge line, the fastest round) on CI's runners. PROVISIONAL: the same
#: container, whose live tail took 0.30 to 0.33 s/s like CI's; set it from the figures CI prints
#: (``gauge:``) before review.
REFERENCE_GAUGE = 15e-6
#: A gauge up to this many times the reference leaves both bounds as they are (the gauge's
#: noise, runners of one class); beyond it they grow with the excess only, so a gauge that reads
#: high never loosens a bound by more than its excess over the tolerance.
TOLERANCE = 1.2
#: Lines in a round of the gauge (about 30 ms) and rounds before the burst and again after it.
GAUGE_LINES = 2_000
GAUGE_ROUNDS = 8
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
        gauged = _gauge(GAUGE_ROUNDS)
        cpu, wall, served = time.process_time(), time.monotonic(), loop.cpu()
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
        delivered = sum(hub.streams[stream_id].delivered for stream_id in streams)
        line_cpu = (loop.cpu() - served) / delivered
        gauge = min(gauged + _gauge(GAUGE_ROUNDS))
        # a core slower than the tolerance allows raises the bounds by its excess only
        scale = max(1.0, gauge / (TOLERANCE * REFERENCE_GAUGE))
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
                f"  gauge: {gauge * 1e6:.2f} us a line (fastest of {2 * GAUGE_ROUNDS} rounds, "
                f"reference {REFERENCE_GAUGE * 1e6:.2f}), scale {scale:.2f}, "
                f"budget {CPU_PER_SECOND * scale:.2f} s/s"
            )
            print(
                f"  event loop: {line_cpu * 1e6:.1f} us a delivered line "
                f"(limit {LOOP_CPU_PER_LINE * scale * 1e6:.1f})"
            )
        for stream_id in streams:
            stream = hub.streams[stream_id]
            assert stream.delivered + sum(stream.dropped.values()) == written, stream.query
            assert stream.delivered <= stream.settings.max_lines_per_second * (elapsed + 1)
        typed = hub.streams[streams[2]]
        assert typed.dropped.get("parse_budget", 0) > 0  # most lines were beyond the parse budget
        assert len(hub.buffer) <= hub.settings.buffer_lines
        assert used / elapsed <= CPU_PER_SECOND * scale
        assert line_cpu <= LOOP_CPU_PER_LINE * scale
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
    """The event loop thread's CPU outside the hub's read cycles, from now on.

    ``Hub._cycle`` runs on the loop for every *written* line (buffer, filters, queues); its own
    CPU is left out. What is left is the SSE side, a delivered line at a time: each stream's
    ``events``, the frame, the response's, the middlewares' and Uvicorn's writes, and waking the
    streams' tasks. Other tasks that run on the loop while a cycle waits for its read thread are
    counted, not left out with the cycle.
    """

    def __init__(self, hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
        self.hub = hub
        #: The cycles' own CPU, and the loop's CPU while they waited for their read thread.
        self.cycles = 0.0
        self.waits = 0.0
        cycle, to_thread = hub._cycle, asyncio.to_thread

        async def timed_cycle() -> None:
            began, waits = time.thread_time(), self.waits
            await cycle()
            self.cycles += time.thread_time() - began - (self.waits - waits)

        async def timed_to_thread(func: Callable[..., T], /, *args: Any, **kwargs: Any) -> T:
            if func != hub._read:
                return await to_thread(func, *args, **kwargs)
            began = time.thread_time()
            try:
                return await to_thread(func, *args, **kwargs)
            finally:
                self.waits += time.thread_time() - began

        monkeypatch.setattr(hub, "_cycle", timed_cycle)
        monkeypatch.setattr(asyncio, "to_thread", timed_to_thread)

    def cpu(self) -> float:
        """The loop thread's CPU so far, less the cycles' own."""

        async def now() -> float:
            return time.thread_time()

        assert self.hub._task is not None
        loop = self.hub._task.get_loop()
        return asyncio.run_coroutine_threadsafe(now(), loop).result(timeout=10) - self.cycles


def _gauge(rounds: int) -> list[float]:
    """Seconds a line of the gauge, a figure a round: a fixed FortiGate spool line cut, decoded,
    time-parsed, folded, searched, split into its fields and written as JSON, ``GAUGE_LINES``
    times. It runs on this thread, outside the hub and outside the measured span. Wall time:
    ``thread_time`` counts in 15.6 ms steps on Windows, and the fastest round is one that
    nothing interrupted (no other thread, no neighbour)."""
    stamp = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)
    line = format_line(stamp, "172.31.255.1", traffic().encode()).rstrip(b"\n")
    figures = []
    for _ in range(rounds):
        began, size = time.perf_counter(), 0
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
        figures.append((time.perf_counter() - began) / GAUGE_LINES)
        assert size > GAUGE_LINES
    return figures
