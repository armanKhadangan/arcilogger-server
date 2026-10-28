"""Performance guard of ``GET /system/health`` (Wave 4 contract 2.6.2 and 3; ``poe test-perf``).

**Data** (contract 3, the part this endpoint reads): ``job_runs`` of 180 days at 17,000 rows a
day (3,060,000 rows, the jobs of ``default_jobs`` in turn, one run in a hundred ``failed``; the
table is not purged, owner question Q21), written with ``INSERT … SELECT generate_series`` (the
technique of ``test_dashboard_api.py``), then ``VACUUM ANALYZE``; today's and yesterday's
``state/clock`` files with 24 samples each; collector and ingest status files with 20 sources;
the worker's status file with every job; 30 sealed days in ``state/seal`` and the verification
results of the three verification tasks.

**Timing:** through the API (``TestClient``), best of three after one warm-up (the cold run is
printed too); the budget is one second. The probe of each job is explained as well: an index
scan of ``ix_job_runs_job_started_at``, never a ``Seq Scan``.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from arcilogger_api.app import API_PREFIX
from arcilogger_api.system_health import routes
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, site_day, utc_now
from arcilogger_worker.alerts.system import backup_state_path
from arcilogger_worker.service import JobState, default_jobs

pytestmark = pytest.mark.perf

API = f"{API_PREFIX}/system/health"
#: The endpoint answers within this on the data above (contract 2.6.2).
BUDGET_SECONDS: Final = 1.0
DAYS: Final = 180
RUNS_PER_DAY: Final = 17_000
SOURCES: Final = 20

LOAD = text(
    "INSERT INTO job_runs (job, started_at, finished_at, status, details) "
    "SELECT (CAST(:jobs AS text[]))[1 + g % :count], t, t + interval '50 milliseconds', "
    "CASE WHEN g % 100 = 0 THEN 'failed' ELSE 'ok' END, "
    "CAST('{\"duration_ms\": 50}' AS jsonb) "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT CAST(:start AS timestamptz) + g * CAST(:step AS interval) AS t) AS x"
)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The app with the health router (mounted by ``routers/system_health.py`` from W4-F on)."""
    if not any(isinstance(route, APIRoute) and route.path == API for route in app.routes):
        app.include_router(routes.router, prefix=API_PREFIX)
    return app


def verify_name(days: int) -> str:
    """The file ``arcilogger-core verify --last N`` writes after verifying ``days`` days (the
    expression of ``cmd_verify``, ``core/src/arcilogger_core/cli.py``)."""
    kind = f"last-{days}"
    return f"last-{kind}.json"


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_job_runs(engine: Engine, jobs: list[str]) -> None:
    now = utc_now()
    rows = DAYS * RUNS_PER_DAY
    with engine.begin() as connection:
        connection.execute(
            LOAD,
            {
                "jobs": jobs,
                "count": len(jobs),
                "rows": rows,
                "start": now - timedelta(days=DAYS),
                "step": f"{86_400_000 / RUNS_PER_DAY} milliseconds",
            },
        )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE job_runs"))


def write_state(layout: Layout, jobs: list[str]) -> None:
    now = utc_now()
    today = site_day(now)
    sources = {f"source-{n:02d}": {"messages": 1000 * n, "bytes": 1} for n in range(SOURCES)}
    stamp = isoformat_site(now, "seconds")
    write_json(layout.collector_status, {"updated_at": stamp, "sources": sources})
    write_json(layout.ingest_status, {"updated_at": stamp, "sources": sources})
    write_json(
        layout.worker_status,
        {
            "updated_at": stamp,
            "jobs": {
                name: JobState(
                    last_started=now - timedelta(seconds=30),
                    last_finished=now - timedelta(seconds=29),
                    last_status="ok",
                    next_due=now + timedelta(seconds=30),
                    runs=100,
                ).as_json()
                for name in jobs
            },
        },
    )
    for back in (1, 0):
        day = today - timedelta(days=back)
        path = layout.clock_samples(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(
                json.dumps(
                    {
                        "time": f"{day.isoformat()}T{hour:02d}:05:00+03:00",
                        "server": "172.20.10.1",
                        "ok": True,
                        "offset_us": 1200,
                        "delay_us": 800,
                        "stratum": 2,
                    }
                )
                + "\n"
                for hour in range(24)
            ),
            encoding="utf-8",
        )
    for back in range(1, 31):
        day = today - timedelta(days=back)
        write_json(layout.seal_state(day), {"day": day.isoformat(), "status": "sealed"})
    verify = layout.state / "verify"
    for name in (verify_name(2), verify_name(30), "last-full.json"):
        write_json(verify / name, {"at": stamp, "days": 30, "ok": True, "checks": []})
    write_json(backup_state_path(layout), {"ok": True, "finished": stamp})


def test_health_within_one_second(
    client: TestClient,
    login_as: Callable[..., LoggedIn],
    db_engine: Engine,
    layout: Layout,
    api_config: Config,
) -> None:
    jobs = [job.name for job in default_jobs(api_config.worker.interval_seconds)]
    began = time.perf_counter()
    load_job_runs(db_engine, jobs)
    write_state(layout, jobs)
    print(f"\nloaded {DAYS * RUNS_PER_DAY:,} job runs in {time.perf_counter() - began:.0f} s")

    with db_engine.connect() as connection:
        plan = "\n".join(
            row[0]
            for row in connection.execute(
                text(
                    "EXPLAIN SELECT status, started_at, finished_at FROM job_runs "
                    "WHERE job = :job AND status <> 'running' AND started_at >= :since "
                    "ORDER BY started_at DESC LIMIT 1"
                ),
                {"job": "partitions.ensure", "since": utc_now() - timedelta(days=7)},
            )
        )
    assert "ix_job_runs_job_started_at" in plan, plan
    assert "Seq Scan" not in plan, plan

    login_as(client, "admin")
    began = time.perf_counter()
    response = client.get(API)
    cold = time.perf_counter() - began
    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["jobs"]) == len(jobs)
    assert all(job["duration_ms"] == 50 for job in body["jobs"])
    assert len(body["time"]["samples"]) >= 23
    best = float("inf")
    for _ in range(3):
        began = time.perf_counter()
        assert client.get(API).status_code == 200
        best = min(best, time.perf_counter() - began)
    print(f"GET /system/health: cold {cold * 1000:.0f} ms, best {best * 1000:.0f} ms")
    assert best < BUDGET_SECONDS
