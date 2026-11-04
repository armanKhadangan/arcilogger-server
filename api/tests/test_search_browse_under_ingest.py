"""Arama's Q1 browse while ingest writes (#119; ``poe test-perf``, both CI jobs).

#90 found that a search on a busy disk waits in its commits and the audit spool's fsync, not in
its SQL; ``test_search_perf`` times a settled server (#118). Here the same Q1 request (the empty
query, ``limit`` 100, over the newest day) runs while the real ingest service — its own process,
started with ``python -m arcilogger_ingest run`` and the production ``[ingest]`` defaults (batch
of 5,000 rows or 0.5 s, 2 parse workers) — loads ``LINES_PER_SECOND`` lines a second that a
thread of this test appends to the spool files, paced against absolute deadlines.

**Data:** one day of ``test_search_perf``'s synthetic lines (the day before ``NEWEST``), then a
``CHECKPOINT``, so the load's write-back does not run into the timing (skipped, and said so, for
a role without ``pg_checkpoint``, as there). The fed lines are the format samples in
``write_realistic_spool``'s mix, received on ``NEWEST`` with the receive time as device time, so
the rows land in the browsed day, as on a site where today is browsed while today's lines
arrive; the preloaded day is another one, so no raw reference occurs twice. The request is
``test_search_perf``'s "Q1 browse 1 d", the data is not: the browsed day holds only the fed rows
(tens of thousands, not 2,000,000), so the figures compare with each other, not one to one with
that guard's.

**The feeder** runs in this process and shares its interpreter with the timed requests: it
stamps and writes a tick's lines (about 0.8 ms of CPU every 20 ms at 2,000 lines/s on the
development VM), which lands in "other". Ingest itself is another process.

**Timing:** ``REQUESTS`` requests while ingest runs, one every ``INTERVAL`` seconds (the next
starts at once when one runs over), then ``SETTLED_REQUESTS`` on the same server once ingest has
stopped and a ``CHECKPOINT`` has written its rows back, for comparison. For each the time of the
call and where it went (SQL, commits, fsyncs, other; ``test_search_perf.Timer``) are kept; the
median, the 95th percentile (nearest rank, so the split shown is that request's), the slowest
request and the mean are printed past pytest's capture. Of the commits counted, the read-only
one after authentication (``reading()``) writes no WAL: one POST /search flushes WAL three times
(activity, audit row, spool watermark) and fsyncs the spool once (#119).

**Guards:** every request answers a full page; the 95th percentile under ingest stays below
``CEILING``, which only a broken server misses (the real budget follows from both CI jobs'
figures, #119); the feeder kept its rate and ingest loaded at least half of what was fed during
the measurement, so the test cannot pass with the feed or ingest idle.
"""

from __future__ import annotations

import contextlib
import math
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from datetime import time as clock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from arcilogger_api.app import create_app
from arcilogger_api.deps import AppState
from arcilogger_api.search.testing import sign_in
from arcilogger_core.config import Config, IngestConfig
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import SITE_TZ, day_window
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_db.settings import set_setting
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_ingest.instance import InstanceLock
from arcilogger_ingest.testing import (
    SENDERS,
    World,
    dhcp_message,
    fortigate_message,
    write_config_file,
)

from .test_search_perf import (
    API,
    BROWSE,
    NEWEST,
    Split,
    Timer,
    _checkpoint,
    _load_day,
    _timescale,
)

pytestmark = pytest.mark.perf

#: Lines a second offered to ingest: ``test_throughput``'s floor, which both CI runners pass, so
#: ingest keeps up (7,400-10,500 lines/s with 2 workers on the development VM, docs/INGEST.md);
#: 70 % FortiGate, the rest MikroTik DHCP, as ``write_realistic_spool``.
LINES_PER_SECOND = 2_000
#: The feeder appends every 20 ms (``measure_live``'s tick).
TICK = 0.02
#: Synthetic lines of the preloaded day (``test_search_perf``'s other days have 100,000).
PRELOADED_ROWS = 200_000
REQUESTS = 100
#: As many as under ingest: with 20, the 95th percentile would be the second-slowest request.
SETTLED_REQUESTS = REQUESTS
#: One request every this many seconds: 100 requests over about 20 s, across many ingest batches.
INTERVAL = 0.2
#: Lines ingest has loaded before the timing starts (one second of the feed).
WARM_LINES = LINES_PER_SECOND
#: Only a broken server misses it: ten times the browse budget ``BROWSE`` (500 ms). Ten times the
#: settled figure (53-72 ms best, docs/SEARCH.md §13) would be 0.5-0.7 s, inside the +500 ms that a
#: Windows runner's write-back adds to one request (#90), so it could fail on a sound server.
CEILING = 10 * BROWSE
#: The device time of a format-sample line: FortiGate ``date``/``time`` and ``eventtime``
#: (nanoseconds in the samples), MikroTik's RFC 3339 stamp.
FORTIGATE_TIME = re.compile(rb"date=\S+ time=\S+")
EVENTTIME = re.compile(rb"eventtime=\d+")
MIKROTIK_TIME = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+03:00")


def _stamped(message: bytes, moment: datetime) -> bytes:
    """A format-sample line with ``moment`` as its device time (a clock that agrees with the
    server's). Every fed line gets its own time: the samples' fixed times would put thousands
    of rows on one instant, which no device sends, and slow the typed rows' lookup by time."""
    local = moment.astimezone(SITE_TZ)
    nanoseconds = round(moment.timestamp() * 1_000_000) * 1_000
    message = FORTIGATE_TIME.sub(f"date={local:%Y-%m-%d} time={local:%H:%M:%S}".encode(), message)
    message = EVENTTIME.sub(b"eventtime=%d" % nanoseconds, message)
    return MIKROTIK_TIME.sub(local.isoformat(timespec="milliseconds").encode(), message)


class Feed:
    """Appends ``LINES_PER_SECOND`` lines a second to ``NEWEST``'s spool files of the FortiGate
    and the MikroTik until :meth:`close`; receive times advance with the feed from 09:45."""

    def __init__(self, world: World) -> None:
        self.paths = {
            source: world.layout.spool_file(source, NEWEST) for source in ("fortigate", "core-r1")
        }
        for path in self.paths.values():
            path.parent.mkdir(parents=True, exist_ok=True)
        self.start = datetime.combine(NEWEST, clock(9, 45), tzinfo=SITE_TZ)
        #: Lines appended so far (read by the test while the thread writes).
        self.written = 0
        #: What ended the thread early (a spool file it could not write).
        self.error: BaseException | None = None
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="arci-feed", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        try:
            self._feed()
        except BaseException as exc:
            self.error = exc
            raise

    def _feed(self) -> None:
        began = time.perf_counter()
        while not self._stop.wait(TICK):
            due = int((time.perf_counter() - began) * LINES_PER_SECOND)
            with contextlib.ExitStack() as stack:
                files = {s: stack.enter_context(p.open("ab")) for s, p in self.paths.items()}
                for index in range(self.written, due):
                    received = self.start + timedelta(seconds=index / LINES_PER_SECOND)
                    if index % 100 < 70:
                        source, message = "fortigate", fortigate_message(index)
                    else:
                        source, message = "core-r1", dhcp_message(index)
                    line = format_line(received, SENDERS[source], _stamped(message, received))
                    files[source].write(line)
            self.written = due

    def alive(self) -> None:
        """Raises what stopped the feed, if anything has."""
        if self.error is not None:
            raise self.error
        assert self._thread.is_alive(), "the feed stopped"

    def close(self) -> None:
        self._stop.set()
        self._thread.join(10)
        assert not self._thread.is_alive(), "the feed did not stop"
        if self.error is not None:
            raise self.error


class Ingest:
    """The ingest service in its own process (no shared GIL, engine or ``os.fsync``), stopped as
    the service wrapper stops it: Ctrl+Break on Windows (process group), SIGINT on POSIX."""

    def __init__(self, world: World, dsn: str, engine: Engine) -> None:
        self.engine = engine
        config = write_config_file(world, dsn, **IngestConfig().model_dump())
        command = [sys.executable, "-m", "arcilogger_ingest", "run", "--config", str(config)]
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP
        else:
            flags = 0
        # Its log goes to a file: a pipe nobody reads would fill and stop the service.
        self.log = world.root / "ingest.log"
        with self.log.open("wb") as log:
            self.process = subprocess.Popen(command, stderr=log, creationflags=flags)

    def alive(self) -> None:
        assert self.process.poll() is None, self.text()

    def text(self) -> str:
        return self.log.read_text(encoding="utf-8", errors="replace")[-4000:]

    def close(self) -> None:
        if self.process.poll() is None:
            if sys.platform == "win32":
                self.process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                self.process.send_signal(signal.SIGINT)
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=30)
        # On Windows the venv's python is a launcher: killing it can leave its interpreter
        # child loading (and its session holding the ingest lock) while the next guards run.
        # The lock is free only once no ingest is left (as in ingest's test_exactly_once).
        probe = InstanceLock(self.engine)
        deadline = time.monotonic() + 60
        while not probe.acquire():
            assert time.monotonic() < deadline, "an ingest process outlived the test"
            time.sleep(0.1)
        probe.release()


def loaded(engine: Engine) -> int:
    """Lines of ``NEWEST``'s spool files committed by ingest."""
    with engine.connect() as connection:
        return int(
            connection.execute(
                text("SELECT coalesce(sum(line_number), 0) FROM ingest_offsets WHERE day = :day"),
                {"day": NEWEST},
            ).scalar_one()
        )


@dataclass
class Browse:
    client: TestClient
    engine: Engine
    dsn: str
    world: World
    #: Prints past pytest's capture, so CI's log shows the figures of a passing run too.
    show: Callable[[str], None]
    timer: Timer


@pytest.fixture
def browse(
    tmp_path: Path,
    pg_dsn: str,
    db_engine: Engine,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Browse]:
    def show(line: str) -> None:
        with capsys.disabled():
            print(line)

    preloaded = NEWEST - timedelta(days=1)
    began = time.perf_counter()
    if _timescale(db_engine):
        pause_compression_jobs(db_engine)
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=NEWEST, months_back=2)
        _load_day(connection, preloaded, PRELOADED_ROWS)
    with db_engine.begin() as connection:
        for source in ("fortigate", "core-r1"):
            rebuild_stream(connection, source, preloaded)
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE"))
    checkpoint = _checkpoint(db_engine)
    show(
        f"\nbrowse under ingest data: {PRELOADED_ROWS} lines on {preloaded}, ready in "
        f"{time.perf_counter() - began:.0f} s, {checkpoint}"
    )
    config = Config.model_validate(
        {
            "site_name": "Örnek A.Ş. (arama ölçümü)",
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
                {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
            ],
        }
    )
    timer = Timer()
    remove_timer = timer.install(db_engine, monkeypatch)
    app = create_app(config, db_engine, audit_flush_seconds=None)
    state: AppState = app.state.arci
    factory = state.session_factory()
    with factory() as session:
        set_setting(session, "search.runs_per_minute", 600, updated_by="perf")
        session.commit()
    with TestClient(app, base_url="http://testserver") as client:
        who = sign_in(client, factory)
        with factory() as session:
            session.execute(
                text(
                    "UPDATE user_sessions SET reauthenticated_at = now() + interval '1 hour' "
                    "WHERE user_id = :u"
                ),
                {"u": who.user_id},
            )
            session.commit()
        try:
            yield Browse(client, db_engine, pg_dsn, World.create(tmp_path / "ingest"), show, timer)
        finally:
            remove_timer()


# -- measuring ------------------------------------------------------------------------------------


def q1(browse: Browse) -> None:
    """Q1 browse of the newest day, as ``test_search_perf`` sends it."""
    start, end = day_window(NEWEST)
    response = browse.client.post(
        API, json={"q": "", "from": start.isoformat(), "to": end.isoformat(), "limit": 100}
    )
    assert response.status_code == 200, response.text
    assert len(response.json()["items"]) == 100


def series(browse: Browse, count: int) -> list[tuple[float, Split]]:
    """``count`` Q1 requests, one every ``INTERVAL`` s: each one's time and split."""
    timer = browse.timer
    results = []
    began = time.perf_counter()
    for number in range(count):
        delay = began + number * INTERVAL - time.perf_counter()
        if delay > 0:
            time.sleep(delay)
        timer.current = Split()
        called = time.perf_counter()
        q1(browse)
        results.append((time.perf_counter() - called, timer.current))
    return results


def rank(results: list[tuple[float, Split]], percent: int) -> tuple[float, Split]:
    """The request at ``percent`` (nearest rank)."""
    ordered = sorted(results, key=lambda result: result[0])
    return ordered[math.ceil(percent / 100 * len(ordered)) - 1]


def mean(results: list[tuple[float, Split]]) -> tuple[float, Split]:
    count = len(results)
    splits = [split for _, split in results]
    return sum(total for total, _ in results) / count, Split(
        sql=sum(s.sql for s in splits) / count,
        commits=sum(s.commits for s in splits) / count,
        commit_count=round(sum(s.commit_count for s in splits) / count),
        fsyncs=sum(s.fsyncs for s in splits) / count,
        fsync_count=round(sum(s.fsync_count for s in splits) / count),
    )


def report(browse: Browse, name: str, results: list[tuple[float, Split]]) -> float:
    """Prints the median, 95th percentile, slowest and mean request with their splits; returns
    the 95th percentile."""
    p50, p95, top, average = rank(results, 50), rank(results, 95), rank(results, 100), mean(results)
    browse.show(
        f"{name}: {len(results)} requests, p50 {p50[0] * 1000:.0f} ms, p95 {p95[0] * 1000:.0f} ms, "
        f"max {top[0] * 1000:.0f} ms (ceiling {CEILING * 1000:.0f} ms)"
    )
    for label, (total, split) in (("p50", p50), ("p95", p95), ("max", top), ("mean", average)):
        browse.show(f"  {label}: {total * 1000:.0f} ms: {split.text(total)}")
    return p95[0]


# -- the guard ------------------------------------------------------------------------------------


def test_browse_under_concurrent_ingest(browse: Browse) -> None:
    ingest = Ingest(browse.world, browse.dsn, browse.engine)
    try:
        feed = Feed(browse.world)
        try:
            # Ingest is up and has filled the browsed day's first page (and more).
            deadline = time.perf_counter() + 120
            while loaded(browse.engine) < WARM_LINES:
                ingest.alive()
                feed.alive()
                assert time.perf_counter() < deadline, ingest.text()
                time.sleep(0.1)
            q1(browse)  # warm-up
            fed, before, began = feed.written, loaded(browse.engine), time.perf_counter()
            under = series(browse, REQUESTS)
            seconds = time.perf_counter() - began
            fed, done = feed.written - fed, loaded(browse.engine) - before
            ingest.alive()
            feed.alive()
        finally:
            feed.close()
    finally:
        ingest.close()
    # The same server settled: ingest stopped, its writes back on disk.
    checkpoint = _checkpoint(browse.engine)
    settled = series(browse, SETTLED_REQUESTS)
    browse.show(
        f"ingest during the timing: {LINES_PER_SECOND} lines/s offered for {seconds:.1f} s "
        f"({fed} lines), {done} loaded ({done / seconds:.0f} lines/s); afterwards {checkpoint}"
    )
    p95 = report(browse, "Q1 browse 1 d under ingest", under)
    report(browse, "Q1 browse 1 d settled (ingest stopped)", settled)
    assert fed >= 0.9 * LINES_PER_SECOND * seconds, f"the feed fell behind: {fed} lines"
    assert done >= fed / 2, f"ingest loaded {done} of {fed} lines fed: {ingest.text()}"
    assert p95 < CEILING
