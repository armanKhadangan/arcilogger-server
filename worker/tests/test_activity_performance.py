"""Canlı Durum's snapshot under load (the M4 guards of #201, Wave 5 contract §6.2 M4 as redefined
by the owner on #201, 2026-11-06 07:08Z, deviation W5-M-4): the snapshot's thread keeps up with
2,000 lines per second (after :data:`TICKS` ticks no more sessions wait to be counted than after
the warm-up, and ``data_until`` is never more than :data:`LAG_LIMIT` behind the newest traffic
row), and while it counts, a probe job in the worker's loop starts within :data:`PROBE_LATE` of
when it is due, every time.

The load is synthetic, shaped like the load generator's browsing (``tools/loadgen``: its sites,
their typical durations and volumes, lognormal spread, an interim log every 120 s): per session a
DNS answer, a web filter log with the host name and the traffic logs, from devices without a
lease (``ip:`` subjects). The rows are written straight into the tables. Before "now" (11:00)
the last three minutes are written whole (the warm-up's last slices read them at full density)
together with every session still open at 11:00 and a tenth of the older closed sessions (they
fill the snapshot's earlier minutes); then each tick's 10 s of rows are written and the thread's
tick runs, while the loop runs its probe. ``ARCILOGGER_ACTIVITY_PERF``: ``1`` (default) both
loads, ``0`` skips. Run with ``-s`` to see the figures (recorded on #211 and in W5-M-4).
"""

from __future__ import annotations

import importlib.util
import itertools
import json
import math
import os
import random
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, insert
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.layout import Layout
from arcilogger_db.models import FgDns, FgTraffic, FgWeb
from arcilogger_db.partitions import ensure_partitions
from arcilogger_worker.activity import Runner
from arcilogger_worker.service import Job, JobContext, Worker
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
#: Written whole before "now" (the warm-up's dense slices), and the share of older sessions.
DENSE = timedelta(minutes=3)
SAMPLE = 0.1
HISTORY = timedelta(minutes=95)


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
    """The lines (``(table, row)``) written before "now", and those of the ticks after it."""

    before: list[Line] = field(default_factory=list)
    later: list[Line] = field(default_factory=list)


def generate(rate: float, devices: int, seed: int) -> Load:
    """Sessions over :data:`HISTORY` before "now" and the ticks after it, ``rate`` lines per
    second on average over ``devices`` addresses; only the lines written are kept (module
    text)."""
    sites = [site for site in loadgen().SITES if not site.blocked]
    weights = [site.weight for site in sites]
    rng, sample = random.Random(seed), random.Random(seed + 1)  # noqa: S311 - synthetic load
    # Lines per session: a DNS answer, a web log, the final log and the interim logs.
    per_session = 3 + sum(
        site.weight * max(0.0, site.duration * math.exp(0.405) / INTERIM - 0.5) for site in sites
    ) / sum(weights)
    session_rate = rate / per_session
    begin, finish = NOW - HISTORY, NOW + TICK * TICKS
    load = Load()
    moment = begin
    number = 0
    while True:
        moment += timedelta(seconds=rng.expovariate(session_rate))
        if moment >= finish:
            return load
        number += 1
        site = rng.choices(sites, weights=weights)[0]
        device = rng.randrange(devices)
        ip = f"172.20.{44 + device // 250}.{1 + device % 250}"
        dstip = site.addresses[number % 2]
        duration = max(1, int(rng.lognormvariate(0, 0.9) * site.duration))
        received = max(200, int(rng.lognormvariate(0, 1.0) * site.received))
        sent = max(120, received // rng.randint(8, 40))
        end = moment + timedelta(seconds=duration)
        if end < NOW - DENSE and sample.random() >= SAMPLE:
            continue  # a closed session left out: only its sample fills the earlier minutes
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
            (load.later if line[1]["time"] >= NOW else load.before).append(line)


def write(session: Session, lines: list[Line]) -> int:
    """Insert ``lines`` (in time order, as ingest would); returns how many."""
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
    for table, rows in by_table.items():
        for start in range(0, len(rows), 5000):
            session.execute(insert(table), rows[start : start + 5000])
    session.commit()
    return len(lines)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def backlog(details: dict[str, Any]) -> int:
    """Sessions waiting to be counted: ended, and open ones whose logs changed."""
    return int(details["ended_waiting"]) + int(details["stale_sessions"])


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
    rate: float,
    devices: int,
) -> None:
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
    load = generate(rate, devices, seed=5651)
    later = load.later
    figures: dict[str, Any] = {
        "lines_per_second": round(
            sum(1 for line in later if line[1]["time"] < NOW + TICK * TICKS)
            / (TICK * TICKS).total_seconds()
        ),
        "written_before": write(db_session, load.before),
    }
    load.before.clear()

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

    worker = Worker(
        engine=db_engine,
        layout=layout,
        jobs=[Job("probe", probe, every=PROBE_EVERY)],
        activity=False,
    )
    stop = threading.Event()
    loop = threading.Thread(target=worker.run, args=(stop,))
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
    late = [b - a - PROBE_EVERY.total_seconds() for a, b in itertools.pairwise(starts)]
    figures["tick_max_s"] = max(details["seconds"] for details in ticks)
    figures["tick_mean_s"] = round(sum(details["seconds"] for details in ticks) / len(ticks), 3)
    figures["write_max_s"] = max(details["write_seconds"] for details in [*warm, *ticks])
    figures["backlog"] = [backlog(details) for details in ticks]
    figures["data_until_lag_max_s"] = max(lags)
    figures["probe_runs"] = len(starts)
    figures["probe_late_max_s"] = round(max(late, default=0.0), 3)
    figures["open_sessions"] = ticks[-1]["open_sessions"]
    figures["file_bytes"] = layout.activity.stat().st_size
    print(f"\nactivity snapshot at {rate:g} lines/s, {devices} devices: {figures}")
    assert backlog(ticks[-1]) <= figures["backlog_after_warm_up"]
    assert figures["data_until_lag_max_s"] <= LAG_LIMIT.total_seconds()
    assert len(starts) >= TICKS
    assert figures["probe_late_max_s"] < PROBE_LATE.total_seconds()
