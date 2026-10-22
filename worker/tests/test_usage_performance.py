"""Usage rollup performance (ADR-0011 budget): one full working day of a 100-device site.

The load generator (``tools/loadgen``, synthetic) writes a Wednesday for 50 people — a PC and a
phone each (100 devices) plus desk phones, printers and servers — as raw spool files; the real
ingest service loads them (FortiGate traffic with interim logs, web filter, DNS, application
control; MikroTik DHCP/ARP), the worker rebuilds the lease timeline and every PC is assigned to
its person (phones stay "device" subjects). Then the day's usage rollup is computed and written
(:func:`arcilogger_worker.usage.jobs.rebuild_day`) and timed.

``ARCILOGGER_USAGE_PERF`` sets the load: ``1`` (default) the generator's default intensity
(30 browsing sessions per user and work hour on the PC), ``heavy`` five times that (closer to a
site with "log all URLs" on), ``0`` skips. Budget: :data:`BUDGET_SECONDS` for the rollup of the
day (measured numbers: docs/USAGE.md "Performance"). Run with ``-s`` to see the timings.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import time
from datetime import date, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, func, select, text
from sqlalchemy.orm import Session

from arcilogger_core.config import Config
from arcilogger_db.assignments import Planner, apply, load_history, lock_devices
from arcilogger_db.models import Department, Device, FgTraffic, Person
from arcilogger_db.partitions import ensure_partitions
from arcilogger_ingest.service import IngestService
from arcilogger_worker.correlation.leases import rebuild_days
from arcilogger_worker.testing import tr
from arcilogger_worker.usage.jobs import rebuild_day

MODE = os.environ.get("ARCILOGGER_USAGE_PERF", "1")
pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(MODE == "0", reason="ARCILOGGER_USAGE_PERF=0"),
]
LOADGEN = Path(__file__).resolve().parents[2] / "tools" / "loadgen" / "arcilogger_loadgen.py"
DAY = date(2026, 10, 14)  # a Wednesday
USERS = 50
INTENSITY = 150.0 if MODE == "heavy" else 30.0
#: Seconds for the rollup of the whole day (compute + write) on the development machine.
BUDGET_SECONDS = 120.0 if MODE == "heavy" else 30.0


@cache
def loadgen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen_usage", LOADGEN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _config(root: Path) -> Config:
    lg = loadgen()
    return Config.model_validate(
        {
            "data_dir": str(root / "data"),
            "secrets_dir": str(root / "secrets"),
            "sources": [
                {"name": s.name, "type": s.type, "address": s.address} for s in lg.DEFAULT_SOURCES
            ],
            "ingest": {"batch_rows": 5000, "batch_seconds": 0.2, "poll_seconds": 0.05},
        }
    )


def test_full_day_rollup_of_a_hundred_devices(
    tmp_path: Path, db_engine: Engine, db_session: Session
) -> None:
    lg = loadgen()
    settings = lg.Settings(seed=5651, users=USERS, intensity=INTENSITY, guests_per_day=3)
    simulation = lg.Simulation(settings)
    (output,) = list(simulation.run(DAY, DAY))
    config = _config(tmp_path)
    timings: dict[str, Any] = {"messages": len(output.messages)}
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
    lg.write_spool(
        config.data_dir,
        [output],
        addresses={s.name: s.address for s in lg.DEFAULT_SOURCES},
    )
    started = time.perf_counter()
    assert IngestService(config, db_engine).run(once=True) == 0
    timings["ingest_s"] = round(time.perf_counter() - started, 1)
    started = time.perf_counter()
    rebuild_days(db_session, DAY, DAY, now=tr(DAY + timedelta(days=2), 9))
    timings["leases_s"] = round(time.perf_counter() - started, 1)
    # Every PC to its person (the phones stay devices), as an administrator would.
    departments = {d.name: d.id for d in db_session.scalars(select(Department))}
    for user in lg.build_users(settings):
        person = Person(name=user.name, department_id=departments.get(user.department))
        db_session.add(person)
        db_session.flush()
        if db_session.get(Device, user.pc.base_mac) is None:
            continue
        owners = lock_devices(db_session, [user.pc.base_mac])
        planner = Planner(
            load_history(db_session, [user.pc.base_mac]), now=tr(DAY, 0), owners=owners
        )
        planner.assign(user.pc.base_mac, person.id, tr(DAY - timedelta(days=30), 9))
        apply(db_session, planner.changes, assigned_by="perf")
    db_session.commit()
    timings["traffic_rows"] = db_session.scalar(select(func.count()).select_from(FgTraffic))
    timings["web_rows"] = db_session.scalar(text("SELECT count(*) FROM fg_web"))
    timings["dns_rows"] = db_session.scalar(text("SELECT count(*) FROM fg_dns"))
    timings["app_rows"] = db_session.scalar(text("SELECT count(*) FROM fg_app"))
    started = time.perf_counter()
    details = rebuild_day(db_session, DAY, now=tr(DAY + timedelta(days=1), 9))
    elapsed = time.perf_counter() - started
    timings["rollup_s"] = round(elapsed, 2)
    timings["rows_written"] = details["rows"]
    # A second run of the same day (the 5-minute refresh does this all day long).
    started = time.perf_counter()
    rebuild_day(db_session, DAY, now=tr(DAY + timedelta(days=1), 9, 5))
    timings["rollup_again_s"] = round(time.perf_counter() - started, 2)
    print(f"\nusage rollup ({MODE}, {USERS} users, intensity {INTENSITY}): {timings}")
    subjects = db_session.scalar(text("SELECT count(DISTINCT subject) FROM usage_daily"))
    assert subjects is not None
    assert subjects >= USERS
    assert elapsed < BUDGET_SECONDS
