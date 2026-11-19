"""Canlı Durum's snapshot under load (the M4 guards of #201, Wave 5 contract §6.2 M4 as redefined
by the owner on #201, 2026-11-06 07:08Z, deviation W5-M-4): the snapshot's thread keeps up with
2,000 lines per second (after :data:`TICKS` ticks no more sessions wait to be counted than after
the warm-up, and ``data_until`` is never more than :data:`LAG_LIMIT` behind the newest traffic
row), and while it counts, a probe job in the worker's loop starts within :data:`PROBE_LATE` of
when it is due, every time.

The load is synthetic, shaped like the load generator's browsing (``tools/loadgen``: its sites,
their typical durations and volumes, lognormal spread, an interim log every 120 s): per session a
DNS answer, a web filter log with the host name and the traffic logs, from devices without a
lease (``ip:`` subjects). The rows are written straight into the tables. The 95 minutes before
"now" (11:00: the snapshot's hour and the session overlap) are written at full density, so each
tick's read meets the table it would meet on site; then each tick's 10 s of rows are written and
the thread's tick runs, while the loop runs its probe. The figures include the per-tick read.
Before the thread counts, what is loaded is frozen out of the collector's full passes, as the
service does at its start; the load's lines still to be written (ingest's on site) with it.
``ARCILOGGER_ACTIVITY_PERF``: ``1`` (default) both loads, ``0`` skips. Run with ``-s`` to see the
figures (recorded on #211 and in W5-M-4).
"""

from __future__ import annotations

import gc
import importlib.util
import itertools
import json
import math
import os
import random
import sys
import threading
import time
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, delete, event, insert, select
from sqlalchemy.orm import Session, SessionTransaction, sessionmaker

from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.layout import Layout
from arcilogger_db.bulk import copy_rows
from arcilogger_db.models import FgDns, FgTraffic, FgWeb
from arcilogger_db.partitions import ensure_partitions
from arcilogger_worker import service as service_module
from arcilogger_worker.activity import Runner
from arcilogger_worker.service import Job, JobContext, Worker, freeze_loaded
from arcilogger_worker.testing import tr

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(
        os.environ.get("ARCILOGGER_ACTIVITY_PERF", "1") == "0", reason="ARCILOGGER_ACTIVITY_PERF=0"
    ),
]
LOADGEN = Path(__file__).resolve().parents[2] / "tools" / "loadgen" / "arcilogger_loadgen.py"
DAY = date(2026, 10, 14)
NOW = tr(DAY, 11)
#: M4 guards (b) and (c): ``data_until`` behind the newest traffic row at most this much, and
#: the loop's probe job, due every :data:`PROBE_EVERY`, started at most this late.
LAG_LIMIT = timedelta(seconds=30)
PROBE_EVERY = timedelta(seconds=2)
PROBE_LATE = timedelta(seconds=2)
TICK = timedelta(seconds=10)
TICKS = 30
INTERIM = 120
#: The history before "now": the last 60 minutes and the session overlap, at full density,
#: written as it is generated, this many lines at a time.
HISTORY = timedelta(minutes=95)
CHUNK = 100_000
#: Chunks of a table waiting to be written, at most (:class:`History`).
BEHIND = 2


@cache
def loadgen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen_activity", LOADGEN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


Line = tuple[Any, dict[str, Any]]


@dataclass
class Load:
    """The lines (``(table, row)``) of the ticks after "now", and how many came before it."""

    later: list[Line] = field(default_factory=list)
    before: int = 0


def generate(
    rate: float, devices: int, seed: int, write_before: Callable[[list[Line]], object]
) -> Load:
    """Sessions over :data:`HISTORY` before "now" and the ticks after it, ``rate`` lines per
    second on average over ``devices`` addresses. The lines before "now" go to
    ``write_before``, :data:`CHUNK` at a time; the later ones are kept."""
    sites = [site for site in loadgen().SITES if not site.blocked]
    weights = [site.weight for site in sites]
    rng = random.Random(seed)  # noqa: S311 - synthetic load
    # Lines per session: a DNS answer, a web log, the final log and the interim logs.
    per_session = 3 + sum(
        site.weight * max(0.0, site.duration * math.exp(0.405) / INTERIM - 0.5) for site in sites
    ) / sum(weights)
    session_rate = rate / per_session
    begin, finish = NOW - HISTORY, NOW + TICK * TICKS
    load = Load()
    pending: list[Line] = []
    moment = begin
    number = 0
    while True:
        moment += timedelta(seconds=rng.expovariate(session_rate))
        if moment >= finish:
            write_before(pending)
            load.before += len(pending)
            return load
        number += 1
        site = rng.choices(sites, weights=weights)[0]
        device = rng.randrange(devices)
        ip = f"172.20.{44 + device // 250}.{1 + device % 250}"
        dstip = site.addresses[number % 2]
        duration = max(1, int(rng.lognormvariate(0, 0.9) * site.duration))
        received = max(200, int(rng.lognormvariate(0, 1.0) * site.received))
        sent = max(120, received // rng.randint(8, 40))
        port = 1024 + number % 60000
        lines: list[Line] = [
            (
                FgDns,
                {
                    "time": moment - timedelta(seconds=rng.uniform(0.05, 2)),
                    "srcip": ip,
                    "dstip": "172.20.40.1",
                    "qname": site.hostname,
                    "answers": [dstip],
                    "action": "pass",
                },
            ),
            (
                FgWeb,
                {
                    "time": moment + timedelta(milliseconds=30),
                    "srcip": ip,
                    "sessionid": number,
                    "dstip": dstip,
                    "hostname": f"www.{site.hostname}",
                    "url": "/",
                    "cat": site.category,
                    "catdesc": site.catdesc,
                    "action": "passthrough",
                },
            ),
        ]
        common = {"srcip": ip, "srcport": port, "dstip": dstip, "dstport": 443, "sessionid": number}
        logged = (0, 0, 0)
        elapsed = INTERIM
        while elapsed < duration:
            share = elapsed / duration
            now = (elapsed, int(sent * share), int(received * share))
            lines.append(
                (
                    FgTraffic,
                    {
                        **common,
                        "time": moment + timedelta(seconds=elapsed),
                        "logid": "0000000020",
                        "is_interim": True,
                        "action": "accept",
                        "duration": now[0],
                        "sentbyte": now[1],
                        "rcvdbyte": now[2],
                        "durationdelta": now[0] - logged[0],
                        "sentdelta": now[1] - logged[1],
                        "rcvddelta": now[2] - logged[2],
                    },
                )
            )
            logged = now
            elapsed += INTERIM
        lines.append(
            (
                FgTraffic,
                {
                    **common,
                    "time": moment + timedelta(seconds=duration),
                    "logid": "0000000013",
                    "action": "close",
                    "duration": duration,
                    "sentbyte": sent,
                    "rcvdbyte": received,
                },
            )
        )
        for line in lines:
            (load.later if line[1]["time"] >= NOW else pending).append(line)
        if len(pending) >= CHUNK:
            write_before(pending)
            load.before += len(pending)
            pending = []


def rows_by_table(lines: list[Line]) -> dict[Any, list[dict[str, Any]]]:
    """The rows of ``lines`` as ingest would store them, in time order, per table."""
    by_table: dict[Any, list[dict[str, Any]]] = {}
    for number, (table, row) in enumerate(sorted(lines, key=lambda line: line[1]["time"])):
        values = {
            **row,
            "time_received": row["time"],
            "source": "fortigate",
            "sender": "192.0.2.1",
            "raw_day": DAY,
            "raw_line": number + 1,
            "raw_offset": 0,
        }
        if table is FgTraffic:
            values.update(subtype="forward", proto=6, trandisp="snat")
        by_table.setdefault(table, []).append(values)
    return by_table


def write(session: Session, lines: list[Line]) -> int:
    """Insert ``lines`` (in time order, as ingest would); returns how many."""
    for table, rows in rows_by_table(lines).items():
        for start in range(0, len(rows), 5000):
            session.execute(insert(table), rows[start : start + 5000])
    session.commit()
    return len(lines)


#: What a column a row leaves out holds (its server default); every other one is NULL.
OMITTED: dict[str, object] = {"is_interim": False}


def copy(session: Session, table: Any, rows: list[dict[str, Any]]) -> None:
    """Write ``rows`` (one table's, from :func:`rows_by_table`) with ``COPY``
    (:func:`~arcilogger_db.bulk.copy_rows`) and commit: the values :func:`write` inserts, in the
    same order, so the same ids."""
    # Every column a row names (an interim log names more than a final one), in the table's
    # order; a row without one gets what the database would give it.
    present = set().union(*(row.keys() for row in rows))
    columns = [column.name for column in table.__table__.columns if column.name in present]
    copy_rows(
        session.connection(),
        table.__tablename__,
        ({**OMITTED, **row} for row in rows),
        columns=columns,
    )
    session.commit()


class History:
    """The history's ``write_before`` (#227): each table's rows of a chunk go to :func:`copy` on
    a thread and a connection of that table's, while the next chunk is generated. A table's
    chunks are written in order, so its ids are :func:`write`'s; at most :data:`BEHIND` of them
    wait, so the history is never held in memory. Leaving the ``with`` waits for the last."""

    def __init__(self, factory: sessionmaker[Session]) -> None:
        self.factory = factory
        self.writers: dict[Any, tuple[ThreadPoolExecutor, Session, list[Future[None]]]] = {}

    def __enter__(self) -> History:
        return self

    def __call__(self, lines: list[Line]) -> int:
        for table, rows in rows_by_table(lines).items():
            if table not in self.writers:
                self.writers[table] = (ThreadPoolExecutor(max_workers=1), self.factory(), [])
            pool, session, queued = self.writers[table]
            while len(queued) >= BEHIND:
                queued.pop(0).result()
            queued.append(pool.submit(copy, session, table, rows))
        return len(lines)

    def __exit__(self, *exc_info: object) -> None:
        try:
            for _, _, queued in self.writers.values():
                for future in queued:
                    future.result()
        finally:
            for pool, session, _ in self.writers.values():
                pool.shutdown(cancel_futures=True)
                session.close()


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def backlog(details: dict[str, Any]) -> int:
    """Sessions waiting to be counted: ended, and open ones whose logs changed."""
    return int(details["ended_waiting"]) + int(details["stale_sessions"])


@dataclass
class Collections:
    """The collector's passes while the loop runs, through :data:`gc.callbacks`: generation,
    start (:func:`time.monotonic`) and seconds of each (#328: a probe late on a Windows runner;
    here the worst probe gap held a full pass). A full pass holds every thread, the loop's too."""

    passes: list[tuple[int, float, float]] = field(default_factory=list)
    _started: float | None = None

    def __call__(self, phase: str, info: dict[str, Any]) -> None:
        if phase == "start":
            self._started = time.monotonic()
        elif self._started is not None:
            began, self._started = self._started, None
            self.passes.append((int(info["generation"]), began, time.monotonic() - began))

    def by_generation(self) -> dict[int, tuple[int, float]]:
        """Per generation: the passes and the longest one's seconds."""
        return {
            generation: (
                sum(1 for g, _at, _s in self.passes if g == generation),
                round(max((s for g, _at, s in self.passes if g == generation), default=0.0), 3),
            )
            for generation in (0, 1, 2)
        }

    def between(self, first: float, last: float) -> list[tuple[int, float]]:
        """The passes of 10 ms or more that began in ``[first, last]``: generation, seconds."""
        return [(g, round(s, 3)) for g, at, s in self.passes if first <= at <= last and s >= 0.01]


#: The name of the worker loop's thread in the test, whose parts :class:`Turns` records.
LOOP = "worker-loop"


@dataclass
class Turns:
    """The parts of the worker loop's turns, in its thread (:data:`LOOP`): name, start
    (:func:`time.monotonic`), seconds, and for a ``wait`` the seconds it was asked for (#328: a
    probe 9.891 s late on a Windows runner, the collector 0.016 s of that gap). A turn of
    ``Worker.run`` is a ``tick``: ``reload_config``, then ``run_job``: its two records, a
    ``transaction`` each, and the ``job`` between them; then ``write_status``: the status built,
    then the file written (``atomic_write_text``, its ``fsync`` within it); then the ``wait``
    for the next job. A ``tick`` longer than its parts was in the code between them (the
    history's load, ``due``, ``idle``, ``gate``); a ``wait`` far longer than it was asked for
    is a thread that did not run, not a database or a disk."""

    parts: list[tuple[str, float, float, float | None]] = field(default_factory=list)
    _transactions: dict[int, float] = field(default_factory=dict)

    def timed(
        self, name: str, func: Callable[..., Any], *, asked: bool = False
    ) -> Callable[..., Any]:
        """``func``, its calls in the loop's thread recorded as ``name``; with ``asked``, its
        timeout too (the first argument or ``timeout``)."""

        def timed_call(*args: Any, **kwargs: Any) -> Any:
            if threading.current_thread().name != LOOP:
                return func(*args, **kwargs)
            timeout = (args[0] if args else kwargs.get("timeout")) if asked else None
            began = time.monotonic()
            try:
                return func(*args, **kwargs)
            finally:
                self.parts.append((name, began, time.monotonic() - began, timeout))

        return timed_call

    def watch(self, worker: Worker, stop: threading.Event, monkeypatch: pytest.MonkeyPatch) -> None:
        """Record ``worker``'s turns: its methods and ``stop.wait`` on the instances, the file
        write and the fsync where ``service`` calls them, the transactions of its sessions."""
        for name in ("tick", "reload_config", "run_job", "write_status"):
            monkeypatch.setattr(worker, name, self.timed(name, getattr(worker, name)))
        monkeypatch.setattr(stop, "wait", self.timed("wait", stop.wait, asked=True))
        write = self.timed("atomic_write_text", atomic_write_text)
        monkeypatch.setattr(service_module, "atomic_write_text", write)
        monkeypatch.setattr(os, "fsync", self.timed("fsync", os.fsync))

        def created(session: Session, transaction: SessionTransaction) -> None:
            if transaction.parent is None and threading.current_thread().name == LOOP:
                self._transactions[id(transaction)] = time.monotonic()

        def ended(session: Session, transaction: SessionTransaction) -> None:
            began = self._transactions.pop(id(transaction), None)
            if began is not None:
                self.parts.append(("transaction", began, time.monotonic() - began, None))

        event.listen(worker.sessions, "after_transaction_create", created)
        event.listen(worker.sessions, "after_transaction_end", ended)

    def by_name(self) -> dict[str, tuple[int, float]]:
        """Per part: how many, and the longest one's seconds."""
        names = dict.fromkeys(part[0] for part in self.parts)
        return {
            name: (
                sum(1 for part in self.parts if part[0] == name),
                round(max(part[2] for part in self.parts if part[0] == name), 3),
            )
            for name in names
        }

    def between(self, first: float, last: float) -> list[tuple[Any, ...]]:
        """The parts of 10 ms or more that overlap ``[first, last]``, in the order they began:
        name, the second of the gap they began at (negative: before it) and their seconds; for
        a ``wait`` also the seconds it was asked for and its overrun."""
        found: list[tuple[Any, ...]] = []
        for name, at, s, timeout in sorted(self.parts, key=lambda part: part[1]):
            if at <= last and at + s >= first and s >= 0.01:
                part: tuple[Any, ...] = (name, round(at - first, 3), round(s, 3))
                if timeout is not None:
                    part += (round(timeout, 3), round(s - timeout, 3))
                found.append(part)
        return found


def test_the_turns_record_the_loops_parts_in_its_gap() -> None:
    """:class:`Turns` records the calls of the loop's thread, not another thread's, places a
    part in the gap it overlaps, and gives a wait what it was asked for and its overrun."""
    turns = Turns()
    nap = turns.timed("nap", time.sleep)
    wait = turns.timed("wait", threading.Event().wait, asked=True)
    first = time.monotonic()
    loop = threading.Thread(target=lambda: (nap(0.05), wait(0.02)), name=LOOP)
    other = threading.Thread(target=nap, args=(0.05,))
    loop.start()
    other.start()
    loop.join()
    other.join()
    last = time.monotonic()
    assert [part[0] for part in turns.parts] == ["nap", "wait"]
    assert turns.by_name()["nap"][0] == 1
    found = turns.between(first, last)
    assert [(part[0], part[1] >= 0) for part in found] == [("nap", True), ("wait", True)]
    assert len(found[0]) == 3  # a part that is not a wait has no timeout
    _name, _at, seconds, timeout, overrun = found[1]
    assert timeout == 0.02
    assert abs(seconds - timeout - overrun) <= 0.001  # the overrun is what it took beyond it
    assert turns.between(last + 1, last + 2) == []


def test_the_collections_record_a_full_pass_in_its_gap() -> None:
    """:class:`Collections` sees a full pass (generation 2) and places it in the gap it began in."""
    collections = Collections()
    gc.callbacks.append(collections)
    try:
        first = time.monotonic()
        gc.collect(2)
        last = time.monotonic()
    finally:
        gc.callbacks.remove(collections)
    assert [g for g, _at, _s in collections.passes] == [2]
    assert collections.by_generation()[2][0] == 1
    assert [(g, first <= at <= last) for g, at, _s in collections.passes] == [(2, True)]


@pytest.fixture
def unfreeze() -> Iterator[None]:
    """The objects the test froze (:func:`freeze_loaded`) given back to the collector, and its
    thresholds as they were."""
    thresholds = gc.get_threshold()
    yield
    gc.unfreeze()
    gc.set_threshold(*thresholds)


@pytest.mark.parametrize(
    ("rate", "devices"),
    [
        pytest.param(3.0, 100, id="default"),  # the load generator's default site, work hours
        pytest.param(2000.0, 1000, id="2000-lines-per-second"),
    ],
)
def test_the_snapshot_keeps_up_and_the_loop_is_not_held(
    db_engine: Engine,
    db_session: Session,
    db_session_factory: sessionmaker[Session],
    layout: Layout,
    unfreeze: None,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    rate: float,
    devices: int,
) -> None:
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
    started = time.monotonic()
    with History(db_session_factory) as history:
        load = generate(rate, devices, seed=5651, write_before=history)
    later = load.later
    figures: dict[str, Any] = {
        "lines_per_second": round(
            sum(1 for line in later if line[1]["time"] < NOW + TICK * TICKS)
            / (TICK * TICKS).total_seconds()
        ),
        "written_before": load.before,
        "write_before_s": round(time.monotonic() - started),
    }

    # What is loaded now is frozen, as the service freezes what it loaded at its start
    # (``cli.run``), before Canlı Durum's thread counts. Here that includes the load's lines still
    # to be written, which on site are ingest's, not the worker's: left in, they made each full
    # pass of the collector (it holds every thread) about twice as long as the worker's own.
    freeze_loaded()
    clock = Clock(NOW)
    runner = Runner(db_engine, layout.activity, gate=lambda now, engine: None, clock=clock)
    warm = []
    while True:
        assert runner.once() == "ok"
        warm.append(runner.details)
        if not runner.details["warming_up"]:
            break
    figures["warm_up_ticks"] = len(warm)
    figures["warm_up_tick_max_s"] = max(details["seconds"] for details in warm)
    figures["backlog_after_warm_up"] = backlog(warm[-1])

    # The worker's loop beside the thread, with a probe job: when does it start?
    starts: list[float] = []

    def probe(ctx: JobContext) -> dict[str, Any]:
        starts.append(time.monotonic())
        return {}

    turns = Turns()
    worker = Worker(
        engine=db_engine,
        layout=layout,
        jobs=[Job("probe", turns.timed("job", probe), every=PROBE_EVERY)],
        activity=False,
    )
    stop = threading.Event()
    loop = threading.Thread(target=worker.run, args=(stop,), name=LOOP)
    turns.watch(worker, stop, monkeypatch)
    collections = Collections()
    gc.callbacks.append(collections)
    loop.start()
    ticks, lags = [], []
    try:
        with db_session_factory() as writer:
            for index in range(1, TICKS + 1):
                lo, hi = NOW + TICK * (index - 1), NOW + TICK * index
                lines = [line for line in later if lo <= line[1]["time"] < hi]
                write(writer, lines)
                clock.now = hi
                began = time.monotonic()
                assert runner.once() == "ok"
                ticks.append(runner.details)
                newest = max(row["time"] for table, row in lines if table is FgTraffic)
                until = json.loads(layout.activity.read_text(encoding="utf-8"))["data_until"]
                lags.append((newest - datetime.fromisoformat(until)).total_seconds())
                # Each tick takes at least the probe's interval: the loop runs its probe at every
                # tick (10 s of logs in this time is more than the thread would count for real).
                stop.wait(max(0.0, PROBE_EVERY.total_seconds() - (time.monotonic() - began)))
    finally:
        stop.set()
        loop.join(timeout=30)
        gc.callbacks.remove(collections)
    late = [b - a - PROBE_EVERY.total_seconds() for a, b in itertools.pairwise(starts)]
    # The worst gap between two probe starts, the collector's passes and the loop's parts in it
    # (#328).
    worst = max(itertools.pairwise(starts), key=lambda pair: pair[1] - pair[0], default=None)
    figures["tick_max_s"] = max(details["seconds"] for details in ticks)
    figures["tick_mean_s"] = round(sum(details["seconds"] for details in ticks) / len(ticks), 3)
    figures["write_max_s"] = max(details["write_seconds"] for details in [*warm, *ticks])
    figures["backlog"] = [backlog(details) for details in ticks]
    figures["data_until_lag_max_s"] = max(lags)
    figures["probe_runs"] = len(starts)
    figures["probe_late_max_s"] = round(max(late, default=0.0), 3)
    figures["open_sessions"] = ticks[-1]["open_sessions"]
    figures["file_bytes"] = layout.activity.stat().st_size
    figures["gc"] = collections.by_generation()
    figures["gc_in_worst_gap"] = [] if worst is None else collections.between(*worst)
    figures["loop"] = turns.by_name()
    figures["loop_in_worst_gap"] = [] if worst is None else turns.between(*worst)
    # Printed on a pass too (as the live tail's guard does), so CI's log shows each run's margin.
    with capsys.disabled():
        print(f"\nactivity snapshot at {rate:g} lines/s, {devices} devices: {figures}")
    assert backlog(ticks[-1]) <= figures["backlog_after_warm_up"]
    assert figures["data_until_lag_max_s"] <= LAG_LIMIT.total_seconds()
    assert len(starts) >= TICKS
    assert figures["probe_late_max_s"] < PROBE_LATE.total_seconds(), (
        f"a probe started {figures['probe_late_max_s']} s late; the collector's passes in that "
        f"gap (generation, seconds): {figures['gc_in_worst_gap']}, all: {figures['gc']}; the "
        f"loop's parts in it (name, second of the gap, seconds): {figures['loop_in_worst_gap']}, "
        f"all (count, longest): {figures['loop']}"
    )


def test_the_history_is_written_as_the_inserts_write_it(
    db_engine: Engine,
    db_session: Session,
    db_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#227: :class:`History` writes what :func:`write` writes, over several chunks: the same
    tables, the same rows and the same content, in the same id order."""
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
    monkeypatch.setitem(globals(), "CHUNK", 1000)
    tables = (FgTraffic, FgWeb, FgDns)

    def stored() -> dict[str, list[dict[str, Any]]]:
        """Each table's rows by id, the ids counted from the table's first."""
        result = {}
        for table in tables:
            rows = db_session.execute(select(table.__table__).order_by(table.id)).mappings().all()
            first = rows[0]["id"] if rows else 0
            result[table.__tablename__] = [{**row, "id": row["id"] - first} for row in rows]
            db_session.execute(delete(table))
        db_session.commit()
        return result

    old = generate(3.0, 100, seed=5651, write_before=lambda lines: write(db_session, lines))
    inserted = stored()
    with History(db_session_factory) as history:
        new = generate(3.0, 100, seed=5651, write_before=history)
    copied = stored()
    assert new.before == old.before > 5 * CHUNK
    assert {name: len(rows) for name, rows in copied.items()} == {
        name: len(rows) for name, rows in inserted.items()
    }
    assert all(inserted.values())
    assert copied == inserted
