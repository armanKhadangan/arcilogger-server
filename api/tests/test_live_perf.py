"""Canlı Akış under load (contract 3.5, ``poe test-perf``): a separate writer process appends
10,000 lines a second for 10 s to two spool files while three streams are open, one of them with
a typed filter.

* The hub's CPU — the whole test process's ``process_time``, which also holds the server and the
  three readers — stays at or below 0.4 s per wall second on a core as fast as the one the budget
  was set on.
* The hub's read thread needs at most six gauge lines of CPU a line (below). The hub reads at most
  1 MiB per file per poll and parses at most ``parse_lines_per_second``, so its CPU per wall
  second grows little when its work costs more; its CPU per line grows in full.
* The same work costs more CPU on a slower core (a slower CI runner) or on one whose neighbours
  share its caches. So after every read cycle the read thread itself runs fixed work shaped like
  the hub's per line, with the standard library only (a slower hub does not slow it): the gauge.
  Its CPU a line is the unit of the read thread's budget and raises the 0.4 s budget in
  proportion (never lowers it).
* Every stream stays at or below its rate cap, and the rest is reported: delivered + dropped =
  matching lines, exactly (the filters here match every line, so the count is known).
* The buffer stays bounded; a line written once the burst is over arrives within 1.5 s.
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx2 as httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.live.hub import Hub, Plan, ReadResult
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
CPU_PER_SECOND = 0.4
#: The gauge's CPU a line on the core the 0.4 s budget was set on, where the hub took 0.31 s a
#: second (4.19 s of CPU). On an idle 4-CPU Linux machine the hub took 0.34 to 0.39 s a second
#: with the gauge at 10.9 to 11.3 µs a line: at most 0.035 s a second per µs, and 0.31 / 0.035 =
#: 8.8 µs. So the budget keeps the margin it was set with over the costliest run (0.39 s against
#: 0.50 there); busy neighbours raised the gauge (up to 20 µs) more than the hub's CPU.
GAUGE_LINE = 8.8e-6
#: The read thread's CPU a line, in gauge lines. Measured 3.2 to 4.3, idle machine to busy; a hub
#: whose reading costs twice as much took 7.4 and more, and 25 µs more a line 6.8 and more.
READ_GAUGE_LINES = 6
#: Lines in one round of the gauge (about 35 ms; ``thread_time`` counts in 15.6 ms steps on
#: Windows).
GAUGE_LINES = 3_000
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    hub: Hub = app.state.arci_live
    try:
        for reader in readers:
            reader.wait(lambda r: bool(r.of("hello")))
        reads = _count_reads(hub, monkeypatch)
        cpu, wall = time.process_time(), time.monotonic()
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
        # the gauge's CPU is the test's, not the hub's
        used, elapsed = time.process_time() - cpu - reads.gauge, time.monotonic() - wall
        gauge_line = reads.gauge / (reads.rounds * GAUGE_LINES)
        # a slower core than the budget's raises it in proportion; a faster one leaves it
        budget = CPU_PER_SECOND * max(1.0, gauge_line / GAUGE_LINE)
        read_line = reads.seconds / reads.lines
        print(
            f"\nlive tail: {used:.2f} s CPU in {elapsed:.2f} s ({used / elapsed:.2f} s/s), "
            f"{len(hub.buffer)} lines buffered; budget {budget:.2f} s/s; read thread "
            f"{reads.seconds:.2f} s CPU for {reads.lines} lines ({read_line * 1e6:.1f} µs, "
            f"{read_line / gauge_line:.2f} gauge lines a line); gauge {gauge_line * 1e6:.2f} µs "
            f"a line ({reads.rounds} rounds)"
        )
        for stream_id in streams:
            stream = hub.streams[stream_id]
            print(f"  {stream.query or '(empty)'}: {stream.delivered} delivered, {stream.dropped}")
            assert stream.delivered + sum(stream.dropped.values()) == written, stream.query
            assert stream.delivered <= stream.settings.max_lines_per_second * (elapsed + 1)
        typed = hub.streams[streams[2]]
        assert typed.dropped.get("parse_budget", 0) > 0  # most lines were beyond the parse budget
        assert len(hub.buffer) <= hub.settings.buffer_lines
        assert used / elapsed <= budget
        assert read_line <= READ_GAUGE_LINES * gauge_line
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


@dataclass
class ReadCost:
    seconds: float = 0.0
    lines: int = 0
    #: The gauge's CPU, in ``rounds`` of ``GAUGE_LINES`` lines.
    gauge: float = 0.0
    rounds: int = 0


def _count_reads(hub: Hub, monkeypatch: pytest.MonkeyPatch) -> ReadCost:
    """The CPU of the hub's read thread (``Hub._read`` runs there, one cycle at a time) and the
    lines it read, from now on; a cycle that skipped a backlog (``behind``: lines counted, not
    read) is left out. After every cycle the same thread runs a round of the gauge: on the same
    core as the cycle, with the same neighbours."""
    cost, read = ReadCost(), hub._read
    line = format_line(datetime.now(UTC), "172.31.255.1", traffic().encode()).rstrip(b"\n")

    def timed(plan: Plan) -> ReadResult:
        began = time.thread_time()
        result = read(plan)
        if not result.behind:
            cost.seconds += time.thread_time() - began
            cost.lines += len(result.entries)
        cost.gauge += _gauge(line)
        cost.rounds += 1
        return result

    monkeypatch.setattr(hub, "_read", timed)
    return cost


def _gauge(line: bytes) -> float:
    """This thread's CPU for a round of the gauge: ``line`` cut, decoded, time-parsed, folded,
    searched and split into its fields, ``GAUGE_LINES`` times."""
    began, fields = time.thread_time(), 0
    for _ in range(GAUGE_LINES):
        stamp, sender, message = line.split(b" ", 2)
        datetime.fromisoformat(stamp.decode("ascii"))
        sender.decode("ascii")
        if b'type="traffic"' in message.lower():
            pairs = PAIR.findall(message)
            fields += len({key.decode("ascii"): value.strip(b'"').decode() for key, value in pairs})
    spent = time.thread_time() - began
    assert fields > GAUGE_LINES
    return spent


def _settled(hub: Hub, stream_id: str, written: int) -> bool:
    """Every written line of the stream is delivered or counted, and its queue is empty."""
    stream = hub.streams.get(stream_id)
    if stream is None or stream.connection is None or not stream.connection.queue.empty():
        return False
    return stream.delivered + sum(stream.dropped.values()) == written
