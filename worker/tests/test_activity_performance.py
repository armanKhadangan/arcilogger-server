"""Canlı Durum's snapshot under load (the M4 guards of #201, Wave 5 contract §6.2 M4 as amended by
deviation W5-M-4): one tick of 10 s of new rows at 2,000 lines per second takes under
:data:`TICK_BUDGET`, and no warm-up slice more than :data:`SLICE_BUDGET`.

The load is synthetic, shaped like the load generator's browsing (``tools/loadgen``: its sites,
their typical durations and volumes, lognormal spread, an interim log every 120 s): per session a
DNS answer, a web filter log with the host name and the traffic logs, from devices without a
lease (``ip:`` subjects). The rows are written straight into the tables. Before "now" (11:00)
the last three minutes are written whole (the warm-up's last slices read them at full density)
together with every session still open at 11:00 and a tenth of the older closed sessions (they
fill the snapshot's earlier minutes); then each tick's 10 s of rows are written and the tick is
timed. ``ARCILOGGER_ACTIVITY_PERF``: ``1`` (default) both loads, ``0`` skips. Run with ``-s`` to
see the figures (recorded on #211).
"""

from __future__ import annotations

import importlib.util
import math
import os
import random
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, insert
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_db.models import FgDns, FgTraffic, FgWeb
from arcilogger_db.partitions import ensure_partitions
from arcilogger_worker.activity import Snapshot
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
#: Seconds one tick of new rows may take, and one warm-up slice (M4 guards (b) and (c)).
TICK_BUDGET = 2.0
SLICE_BUDGET = 2.0
TICK = timedelta(seconds=10)
TICKS = 12
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


@pytest.mark.parametrize(
    ("rate", "devices"),
    [
        pytest.param(3.0, 100, id="default"),  # the load generator's default site, work hours
        pytest.param(2000.0, 1000, id="2000-lines-per-second"),
    ],
)
def test_ticks_and_warm_up_slices_stay_within_budget(
    db_engine: Engine, db_session: Session, layout: Layout, rate: float, devices: int
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

    snapshot = Snapshot(layout.activity)
    slices = []
    while True:
        details = snapshot.tick(db_session, NOW)
        slices.append(details)
        if not details["warming_up"]:
            break
    figures["slices"] = len(slices)
    figures["slice_max_s"] = max(details["seconds"] for details in slices)
    figures["slice_rows_max"] = max(details["rows"] for details in slices)
    ticks = []
    for index in range(1, TICKS + 1):
        lo, hi = NOW + TICK * (index - 1), NOW + TICK * index
        written = write(db_session, [line for line in later if lo <= line[1]["time"] < hi])
        details = snapshot.tick(db_session, hi)
        ticks.append({**details, "lines": written})
    figures["tick_max_s"] = max(details["seconds"] for details in ticks)
    figures["tick_rows_min"] = min(details["rows"] for details in ticks)
    figures["open_sessions"] = ticks[-1]["open_sessions"]
    figures["write_max_s"] = max(details["write_seconds"] for details in [*slices, *ticks])
    figures["file_bytes"] = layout.activity.stat().st_size
    phases = ("read_seconds", "count_seconds", "write_seconds")
    figures["slice_phases_max"] = {name: max(d[name] for d in slices) for name in phases}
    figures["tick_phases_max"] = {name: max(d[name] for d in ticks) for name in phases}
    figures["stale_after_ticks"] = [details["stale_sessions"] for details in ticks]
    figures["recounted"] = [details["recounted"] for details in ticks]
    figures["ended_waiting"] = [details["ended_waiting"] for details in ticks]
    print(f"\nactivity snapshot at {rate:g} lines/s, {devices} devices: {figures}")
    assert figures["slice_max_s"] < SLICE_BUDGET
    assert figures["tick_max_s"] < TICK_BUDGET
