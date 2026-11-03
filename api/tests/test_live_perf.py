"""Canlı Akış under load (contract 3.5, ``poe test-perf``): a separate writer process appends
10,000 lines a second for 10 s to two spool files while three streams are open, one of them with
a typed filter.

* The hub's CPU — the whole test process's ``process_time``, which also holds the server and the
  three readers — stays at or below 0.4 s per wall second on a core as fast as CI's runners.
* The event loop's CPU a delivered line (the SSE side: each stream's events, Starlette's and
  Uvicorn's writes), counted in lines of the gauge, stays at or below its bound. On CI (and with
  ``ARCILOGGER_PERF_STRICT=1``) the bound is tighter on a faster core: a costlier write path
  fails on a fast runner and on a slow one alike. Elsewhere it is a flat ``OFF_CI_GAUGE_LINES``,
  and CI's bound and the margin to it are shown, not asserted.
* That split is not a fix of the root cause (#125). On some cloud-session hosts the gauge runs
  warm (the fastest of back-to-back rounds, mostly C library code) and the SSE path cold (short
  runs of interpreted code after the loop idled): at the same gauge CI's ubuntu-latest read 11.71
  us and 4.80 gauge lines, such a host 11.70 us and 7.47, so no bound from the gauge alone passes
  unchanged code there and fails a 40 % costlier path on CI. CI is the gate. Off CI a path 40 %
  costlier fails only on that fast-gauge class (11 to 13 us); #31's +400 us fails everywhere.
* Not covered: a starved hub (8 busy neighbours on 4 CPUs) runs fewer, larger cycles and its
  figure a line falls, so a path 39 us a line costlier can pass. CI's runners are not loaded so.
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
import os
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

from arcilogger_api.live.events import Entry
from arcilogger_api.live.hub import Hub
from arcilogger_api.live.testing import LiveServer, sign_in
from arcilogger_api.rawlines import display_text
from arcilogger_api.testing import EXAMPLE_SITE_NAME
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import site_day

from .test_live_api import STREAM_HEADERS, Reader, traffic

pytestmark = pytest.mark.perf

LINES_PER_SECOND = 10_000
SECONDS = 10
#: The test process's CPU a wall second on a core like CI's runners (0.18 to 0.37 s/s there).
CPU_PER_SECOND = 0.4
#: The gauge (seconds a gauge line, the fastest round) on CI's runners: the slowest one seen on
#: each system, so that every runner keeps 0.4 s/s. From the throughput guards of CI runs
#: 41368179019, 41407611313, 41414800703, 41422165382, 41430160072, 41438160162 and 41446215228,
#: seven on each system: windows-latest 9.13 to 17.76 us, ubuntu-latest 7.88 to 15.90 us. Other
#: systems take Linux's figures. Three later runs on each system (41455124190, 41465153350,
#: 41478486887; ubuntu-latest up to 16.04 us, inside the tolerance) passed with these figures.
REFERENCE_GAUGE = 17.76e-6 if sys.platform == "win32" else 15.90e-6
#: A gauge up to this many times the reference leaves the CPU a second at 0.4 s (the gauge's
#: noise); beyond it the bound grows with the excess only, so a gauge that reads high never
#: loosens it by more than its excess over the tolerance.
TOLERANCE = 1.2
#: The per-line bound is CI's (``LOOP_GAUGE_LINES``, tighter on a faster core) on GitHub's
#: runners (``GITHUB_ACTIONS=true``) or with ``ARCILOGGER_PERF_STRICT=1``, those values exactly,
#: else the flat ``OFF_CI_GAUGE_LINES``.
STRICT = (
    os.environ.get("GITHUB_ACTIONS") == "true" or os.environ.get("ARCILOGGER_PERF_STRICT") == "1"
)
#: The event loop's CPU a delivered line on a core as fast as the reference, in gauge lines (the
#: gauge's fastest round, on the same clock), so the bound follows the core's speed: a write path
#: 40 % costlier fails on a fast runner as on a slow one. From the ten runs above, 41477960420,
#: 41617780788 and 41633136399 (13 on each system), each brought to the reference by
#: ``LOOP_EXPONENT``: at least 1.10 times the costliest and below 1.4 times the cheapest / 1.05.
#: ubuntu-latest read 5.10 to 6.14 there (6.76 to 6.80 allowed), windows-latest 4.49 to 5.01
#: (5.51 to 5.99); a path 40 % costlier is over the limit by at least 5.1 % and 12.3 %.
LOOP_GAUGE_LINES = 5.6 if sys.platform == "win32" else 6.8
#: A faster core gets a tighter limit, in gauge lines: the limit goes with the gauge, relative to
#: the reference, to this power. On those runs the loop's CPU a line went with the gauge to the
#: power 1.25 on ubuntu-latest and 1.20 on windows-latest (log-log slopes); their mean less 1 is
#: 0.22 (0.23 on #88's 22 readings), taken to 0.20, not 0.25: at 0.25 the rule above leaves
#: ubuntu-latest 6.904 to 6.909, at 0.3 nothing since 41477960420 (8.65 us, 5.39).
LOOP_EXPONENT = 0.20
#: The per-line bound off CI, in gauge lines, the same on every core: 1.10 times the costliest
#: cloud-session reading (8.52, gauge 11.90 us; that class read 7.01 to 8.52, at 11.27 to 12.60
#: us where the gauge was shown, #88's slow class 5.74 to 6.41 at 21.47 to 23.42 us). Taken on
#: Linux cloud sessions only: on a Windows machine off CI it is loose by far (windows-latest read
#: 4.03 to 4.98).
OFF_CI_GAUGE_LINES = 9.4
#: Below this fraction of the reference the limit no longer falls, in gauge lines. CI's gauges ran
#: from about 0.5 to 1 times the reference (the fastest at the floor); the bound is shown to hold
#: over that range.
LOOP_FLOOR = 0.5
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
    diag = I125(hub, monkeypatch) if I125_PROBE else None  # #125's diagnostic, never merged
    loop = LoopCpu(hub, monkeypatch)
    if diag is not None:
        diag.loop = loop
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
        if diag is not None:
            diag.start(rounds)
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
        later = _gauge(GAUGE_ROUNDS)
        gauge = Gauge.of(rounds + later)
        # a core slower than the tolerance allows raises the bound by its excess only
        scale = max(1.0, gauge.seconds / (TOLERANCE * REFERENCE_GAUGE))
        # a core faster than the reference gets a tighter limit a line, in gauge lines
        speed = min(max(gauge.seconds / REFERENCE_GAUGE, LOOP_FLOOR), 1.0)
        ci_limit = LOOP_GAUGE_LINES * speed**LOOP_EXPONENT
        limit = ci_limit if STRICT else OFF_CI_GAUGE_LINES
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
                f"rounds: {Gauge.of(rounds).seconds * 1e6:.2f} before the burst, "
                f"{Gauge.of(later).seconds * 1e6:.2f} after it; wall {gauge.wall * 1e6:.2f}; "
                f"reference {REFERENCE_GAUGE * 1e6:.2f}), "
                f"scale {scale:.2f}, budget {CPU_PER_SECOND * scale:.2f} s/s"
            )
            print(
                f"  event loop: {line:.2f} gauge lines a delivered line (median of {len(cycles)} "
                f"cycles; {whole:.2f} over the whole span), about {line * gauge.seconds * 1e6:.1f} "
                f"us (limit {limit:.2f}, speed factor {speed:.2f})"
            )
            margin = f"margin {(ci_limit / line - 1) * 100:+.1f} %"
            print(
                f"  per-line check: strict (CI's bound), limit {ci_limit:.2f}, {margin}"
                if STRICT
                else f"  per-line check: off CI, limit {limit:.2f}; strict limit {ci_limit:.2f}, "
                f"{margin} (not asserted)"
            )
            if diag is not None:
                block = diag.report(
                    gauge=gauge,
                    before=Gauge.of(rounds),
                    after=Gauge.of(later),
                    span=(wall, wall + elapsed),
                    cycles=cycles,
                    line=line,
                    figures={
                        "cpu_s": used / elapsed,
                        "budget": CPU_PER_SECOND * scale,
                        "limit": limit,
                        "ci_limit": ci_limit,
                    },
                )
                print("\n".join(block))
        for stream_id in streams:
            stream = hub.streams[stream_id]
            assert stream.delivered + sum(stream.dropped.values()) == written, stream.query
            assert stream.delivered <= stream.settings.max_lines_per_second * (elapsed + 1)
        typed = hub.streams[streams[2]]
        assert typed.dropped.get("parse_budget", 0) > 0  # most lines were beyond the parse budget
        assert len(hub.buffer) <= hub.settings.buffer_lines
        assert used / elapsed <= CPU_PER_SECOND * scale
        assert line <= limit
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
        if diag is not None:
            with capsys.disabled():
                print(diag.burst())


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


# --- #125's diagnostic dispatch (a draft pull request that is never merged) -------------------
#: ``none``, ``plus40`` or ``plus400``: the in-loop gauge, the round distribution, the cold/hot
#: burst and that probe, printed as an ``I125:`` block; unset, the guard runs as it is.
I125_PROBE = os.environ.get("I125_PROBE")
#: ``none`` writes its loop CPU a delivered line here; ``plus40`` spins 0.4 times that.
I125_CALIBRATION = os.environ.get("I125_CALIBRATION")
#: Lines in a round of the in-loop gauge (one at each cycle start, about 0.7 % of the loop).
I125_ROUND_LINES = 200
#: The cold/hot burst: trials of each mode, and the pause before a cold one.
I125_TRIALS = 10
I125_PAUSE = 0.5
#: plus40: a probe calibrated from the first cycles (no calibration run) waits for this many.
I125_FIRST_CYCLES = 3


class I125:
    """The diagnostic's probes: an in-loop gauge round at each cycle start, counted as the
    cycle's own CPU (``LoopCpu`` wraps this), and a CPU spin before each delivered line's frame,
    as #31's probe did in ``events``."""

    def __init__(self, hub: Hub, monkeypatch: pytest.MonkeyPatch) -> None:
        assert I125_PROBE in ("none", "plus40", "plus400"), I125_PROBE
        self.probe = I125_PROBE
        #: ``_thread_cpu`` a second (1 off Windows) and the spin a delivered line, in its units.
        self.per_second = 1.0
        self.spin = 0.0
        self.calibrated = "no probe"
        self.started = False
        self.loop: LoopCpu | None = None
        #: The in-loop rounds: (monotonic, a line, a line of the first 20, a line of the rest).
        self.rounds: list[tuple[float, float, float, float]] = []
        stamp = datetime(2026, 10, 26, 7, 0, tzinfo=UTC)
        self.line = format_line(stamp, "172.31.255.1", traffic().encode()).rstrip(b"\n")
        cycle, frame = hub._cycle, Entry.frame

        async def probed_cycle() -> None:
            self.rounds.append((time.monotonic(), *self._round()))
            if self.started and self.probe == "plus40" and not self.spin:
                self._from_first_cycles()
            await cycle()

        def spun(entry: Entry, epoch: str, site: Any) -> bytes:
            if self.spin:
                began = _thread_cpu()
                while _thread_cpu() - began < self.spin:
                    pass
            return frame(entry, epoch, site)

        monkeypatch.setattr(hub, "_cycle", probed_cycle)
        monkeypatch.setattr(Entry, "frame", spun)

    def start(self, rounds: list[Round]) -> None:
        """Sets the probe before the burst, from the gauge's first rounds."""
        if sys.platform == "win32":
            self.per_second = sum(r.cpu for r in rounds) / sum(r.wall for r in rounds)
        if self.probe == "plus400":
            self.spin, self.calibrated = 400e-6 * self.per_second, "#31's +400 us"
        elif self.probe == "plus40":
            if I125_CALIBRATION and Path(I125_CALIBRATION).is_file():
                path = json.loads(Path(I125_CALIBRATION).read_text())["path_cpu"]
                self.spin, self.calibrated = 0.4 * path, "0.4 x the calibration run's path"
            else:
                self.calibrated = f"0.4 x the first {I125_FIRST_CYCLES} cycles' path"
        self.started = True

    def _from_first_cycles(self) -> None:
        assert self.loop is not None
        marks = self.loop.marks
        cycles = [
            (cpu - began) / (lines - before)
            for (began, before), (cpu, lines) in itertools.pairwise(marks)
            if lines - before >= CYCLE_LINES
        ]
        if len(cycles) >= I125_FIRST_CYCLES:
            self.spin = 0.4 * statistics.median(cycles)

    def _round(self) -> tuple[float, float, float]:
        """A round of the gauge's own work on the loop thread: a line, the first 20, the rest."""
        began, size = _thread_cpu(), 0
        first = began
        for index in range(I125_ROUND_LINES):
            if index == 20:
                first = _thread_cpu()
            stamp_text, sender, message = self.line.split(b" ", 2)
            datetime.fromisoformat(stamp_text.decode("ascii"))
            sender.decode("ascii")
            if b'type="traffic"' in message.lower():
                fields = {
                    key.decode("ascii"): value.strip(b'"').decode()
                    for key, value in PAIR.findall(message)
                }
                size += len(json.dumps(fields, ensure_ascii=False, separators=(",", ":")))
        end = _thread_cpu()
        assert size > I125_ROUND_LINES
        return (
            (end - began) / I125_ROUND_LINES,
            (first - began) / 20,
            (end - first) / (I125_ROUND_LINES - 20),
        )

    def burst(self) -> str:
        """measure.md 3.3 on this thread, once the streams are closed (a reader's read times out
        after 10 s without a byte): W (``display_text`` and ``json.dumps`` of 20 delivered lines'
        data) and G (20 gauge lines), back to back and after a sleep of ``I125_PAUSE``."""
        items = []
        for index in range(20):
            message = traffic(srcip=f"172.20.41.{index + 10}").encode()
            data: dict[str, Any] = {
                "seq": 1000 + index,
                "received": f"2026-10-26T07:00:{index:02d}.123456+00:00",
                "source": "fortigate",
                "sender": "172.31.255.1",
                "day": "2026-10-26",
                "line": 41234 + index,
                "offset": 9876543 + index,
                "kind": "fortigate",
                "type": "traffic",
                "subtype": "forward",
                "summary": {
                    "srcip": f"172.20.41.{index + 10}",
                    "srcport": 51544 + index,
                    "dstip": "203.0.113.10",
                    "dstport": 443,
                    "proto": "tcp",
                    "action": "accept",
                },
                "text": None,
                "parsed": True,
            }
            items.append((message, data))

        def work() -> float:
            began = _thread_cpu()
            for message, data in items:
                data["text"] = display_text(message)
                json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
            return (_thread_cpu() - began) / len(items)

        def gauge() -> float:
            began = _thread_cpu()
            for _ in range(20):
                stamp_text, sender, message = self.line.split(b" ", 2)
                datetime.fromisoformat(stamp_text.decode("ascii"))
                sender.decode("ascii")
                if b'type="traffic"' in message.lower():
                    fields = {
                        key.decode("ascii"): value.strip(b'"').decode()
                        for key, value in PAIR.findall(message)
                    }
                    json.dumps(fields, ensure_ascii=False, separators=(",", ":"))
            return (_thread_cpu() - began) / 20

        for _ in range(3):
            work(), gauge()
        figures: dict[str, dict[str, list[float]]] = {}
        for mode in ("hot", "sleep"):
            w, g = [], []
            for _ in range(I125_TRIALS):
                if mode == "sleep":
                    time.sleep(I125_PAUSE)
                w.append(work())
                if mode == "sleep":
                    time.sleep(I125_PAUSE)
                g.append(gauge())
            figures[mode] = {"W": w, "G": g}
        us = 1e6 / self.per_second
        return (
            f"I125: cold/hot burst (after a sleep of {I125_PAUSE} s against back to back, "
            f"{I125_TRIALS} trials, streams closed): "
            + "; ".join(
                f"{name} {statistics.median(hot) * us:.1f} -> {statistics.median(cold) * us:.1f} "
                f"us, median {statistics.median(cold) / statistics.median(hot):.3f}, min "
                f"{min(cold) / min(hot):.3f}"
                for name, (hot, cold) in (
                    (name, (figures["hot"][name], figures["sleep"][name])) for name in ("W", "G")
                )
            )
        )

    def report(
        self,
        *,
        gauge: Gauge,
        before: Gauge,
        after: Gauge,
        span: tuple[float, float],
        cycles: list[float],
        line: float,
        figures: dict[str, float],
    ) -> list[str]:
        """The ``I125:`` block; also writes the calibration of a ``none`` run."""
        us = 1e6 / self.per_second
        path = statistics.median(cycles) if cycles else float("nan")
        if self.probe == "none" and I125_CALIBRATION and cycles:
            Path(I125_CALIBRATION).write_text(json.dumps({"path_cpu": path}))
        per_round = sorted(r.cpu / GAUGE_LINES for r in gauge.rounds)
        inside = [r for r in self.rounds if span[0] <= r[0] <= span[1]]
        rounds = inside or self.rounds
        in_loop = statistics.median(r[1] for r in rounds)
        cold = statistics.median(r[2] / r[3] for r in rounds)
        today = (
            f"s/s {figures['cpu_s']:.2f} (budget {figures['budget']:.2f}), gauge "
            f"{gauge.seconds * 1e6:.2f} us, {line:.2f} gl, {line * gauge.seconds * 1e6:.1f} us a "
            f"line, limit {figures['limit']:.2f} ({'strict' if STRICT else 'off CI'}; CI's "
            f"{figures['ci_limit']:.2f}, margin {(figures['ci_limit'] / line - 1) * 100:+.1f} %)"
        )
        per_line = line <= figures["limit"]
        budget = figures["cpu_s"] <= figures["budget"]
        return [
            f"I125: probe {self.probe}: spin {self.spin * us:.1f} us a delivered line "
            f"({self.calibrated}); _thread_cpu {self.per_second:.4g} a second",
            f"I125: today: {today}",
            f"I125: guard gauge rounds ({len(per_round)}): min {per_round[0] * us:.2f} / median "
            f"{statistics.median(per_round) * us:.2f} / max {per_round[-1] * us:.2f} us; median / "
            f"min {statistics.median(per_round) / per_round[0]:.3f}; fastest before "
            f"{before.cpu * us:.2f}, after {after.cpu * us:.2f}",
            f"I125: in-loop gauge ({I125_ROUND_LINES}-line rounds at each cycle start, "
            f"{len(inside)} in the span of {len(self.rounds)}): median {in_loop * us:.2f} / min "
            f"{min(r[1] for r in rounds) * us:.2f} us; in-loop median / guard gauge "
            f"{in_loop / gauge.cpu:.3f}; first 20 / other {I125_ROUND_LINES - 20} {cold:.3f}",
            f"I125: path {path * us:.1f} us a delivered line = {path / gauge.cpu:.2f} guard gauge "
            f"lines = {path / in_loop:.2f} in-loop gauge lines",
            f"I125 RESULT probe={self.probe}: per-line {'PASS' if per_line else 'FAIL'} "
            f"({line:.2f} {'<=' if per_line else '>'} {figures['limit']:.2f}), budget "
            f"{'PASS' if budget else 'FAIL'} ({figures['cpu_s']:.2f} "
            f"{'<=' if budget else '>'} {figures['budget']:.2f} s/s)",
        ]
