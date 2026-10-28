"""Performance guard of ``GET /api/v1/system`` (Wave 4 contract 2.5.2, 3 W4-B; ``poe test-perf``,
both CI jobs).

**Data** (the perf data of contract 3, the parts the Sistem resources read): ``fg_web`` 300,000
and ``fg_dns`` 500,000 rows on the newest site day and 20,000 / 35,000 on each of the 7 days
before, from two FortiGate sources; ``job_runs`` 17,000 rows a day for 180 days; status files
with 20 configured sources (and their network members); a disk history of 120 days. Written
with ``INSERT … SELECT generate_series`` (the technique of ``test_dashboard_api.py``), then
``VACUUM ANALYZE``; on TimescaleDB the compression policies are paused first.

**Timing:** through the API (``TestClient``), best of three after one warm-up, each timed call
with the database sizes measured again (an empty cache: the worst case).
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from arcilogger_api import testing as api_testing
from arcilogger_api.app import API_PREFIX
from arcilogger_api.deps import TtlCache
from arcilogger_api.system import routes
from arcilogger_api.system.models import DatabaseInfo
from arcilogger_api.system.resources import DATABASE_TTL
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, site_day, utc_now
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_worker.alerts.system import drives

pytestmark = pytest.mark.perf

API = f"{API_PREFIX}/system"
#: ``GET /system`` must answer within this (contract 2.5.2).
BUDGET_SECONDS = 1.0
SOURCES = 20

WEB = text(
    "INSERT INTO fg_web (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "hostname, profile, action) SELECT t, t, :source, :sender, :day, g, 0, "
    "'www' || (g % 500) || '.example.com', 'ornek-web', 'passthrough' "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT CAST(:start AS timestamptz) + g * CAST(:step AS interval)) AS x(t)"
)
DNS = text(
    "INSERT INTO fg_dns (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "qname, action) SELECT t, t, :source, :sender, :day, g, 0, "
    "'host' || (g % 700) || '.example.com', 'pass' "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT CAST(:start AS timestamptz) + g * CAST(:step AS interval)) AS x(t)"
)
JOB_RUNS = text(
    "INSERT INTO job_runs (job, started_at, finished_at, status, details) "
    "SELECT 'job-' || (g % 30), t, t + interval '1 second', 'ok', '{}'::jsonb "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT CAST(:start AS timestamptz) + g * CAST(:step AS interval)) AS x(t)"
)


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    sources = [
        {"name": f"fortigate-{i}", "type": "fortigate", "address": f"172.20.0.{i + 1}"}
        for i in range(SOURCES - 2)
    ]
    sources += [
        {"name": "ap", "type": "generic", "address": "172.20.30.0/24"},
        {"name": "switches", "type": "generic", "address": "172.20.40.0/24"},
    ]
    return Config.model_validate(
        {
            "site_name": api_testing.EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": sources,
        }
    )


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    if not any(getattr(route, "path", None) == API for route in app.routes):
        app.include_router(routes.router, prefix=API_PREFIX)
    return app


def _day_start(day: date) -> datetime:
    return datetime.combine(day, datetime.min.time(), tzinfo=SITE_TZ)


def _load(engine: Engine, today: date) -> None:
    with engine.begin() as connection:
        ensure_partitions(connection, today=today, months_back=7)
    for back in range(8):
        day = today - timedelta(days=back)
        web, dns = (300_000, 500_000) if back == 0 else (20_000, 35_000)
        for statement, rows in ((WEB, web), (DNS, dns)):
            for index, sender in enumerate(("172.20.0.1", "172.20.0.2")):
                with engine.begin() as connection:
                    connection.execute(
                        statement,
                        {
                            "source": f"fortigate-{index}",
                            "sender": sender,
                            "day": day,
                            "rows": rows // 2,
                            "start": _day_start(day),
                            "step": f"{86_400_000 / (rows // 2 + 1)} milliseconds",
                        },
                    )
    with engine.begin() as connection:
        connection.execute(
            JOB_RUNS,
            {
                "rows": 17_000 * 180,
                "start": _day_start(today - timedelta(days=180)),
                "step": f"{86_400_000 / 17_000} milliseconds",
            },
        )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE"))


def _status_files(layout: Layout) -> None:
    now = isoformat_site(utc_now(), "seconds")
    entries = {f"fortigate-{i}": i for i in range(SOURCES - 2)}
    entries |= {f"ap-172.20.30.{i}": i for i in range(1, 41)}
    entries |= {f"switches-172.20.40.{i}": i for i in range(1, 21)}
    entries |= {f"unknown-172.20.50.{i}": i for i in range(1, 11)}
    layout.state.mkdir(parents=True, exist_ok=True)
    layout.collector_status.write_text(
        json.dumps(
            {
                "started_at": now,
                "updated_at": now,
                "sources": {
                    name: {"messages": n * 1000, "bytes": n * 90_000, "last_received": now}
                    for name, n in entries.items()
                },
                "dropped_unknown": {},
                "dropped_transport": {},
                "write_failures": {},
                "spool_write_error": None,
                "torn_lines_total": 0,
                "tcp_connections": 2,
                "tcp_framing_errors": 0,
            }
        ),
        encoding="utf-8",
    )
    layout.ingest_status.write_text(
        json.dumps(
            {
                "state": "running",
                "updated_at": now,
                "database": {"ok": True},
                "parse_workers": {"configured": 2, "active": 2},
                "totals": {"lines_per_second": 1500.0},
                "sources": {
                    name: {
                        "lag_bytes": n,
                        "lag_seconds": 0.5,
                        "lines_per_second": 20.0,
                        "unfinished_days": ["2026-10-28"],
                        "last_error": None,
                    }
                    for name, n in entries.items()
                },
            }
        ),
        encoding="utf-8",
    )


def _history(layout: Layout, config: Config, today: date) -> None:
    label = drives(config.data_dir)[0][0]
    days = [
        {
            "day": (today - timedelta(days=119 - i)).isoformat(),
            "drives": [{"path": label, "total": 10**12, "used": 3 * 10**11 + i * 10**9}],
        }
        for i in range(120)
    ]
    layout.system_history.write_text(json.dumps({"schema": 1, "days": days}), encoding="utf-8")


def test_system_within_one_second(
    client: TestClient,
    app: FastAPI,
    db_engine: Engine,
    layout: Layout,
    api_config: Config,
    login_as: Callable[..., LoggedIn],
) -> None:
    today = site_day(utc_now())
    if _timescale(db_engine):
        pause_compression_jobs(db_engine)
    _load(db_engine, today)
    _status_files(layout)
    _history(layout, api_config, today)
    login_as(client, "admin")
    warm = client.get(API)
    assert warm.status_code == 200, warm.text
    body = warm.json()
    assert len(body["collector"]["sources"]) == SOURCES
    assert body["collector"]["unknown_senders"]["count"] == 10
    assert body["drives"][0]["forecast"]["samples"] == 30
    assert {"fg_web", "fg_dns", "job_runs"} <= {t["name"] for t in body["database"]["tables"]}
    timings = []
    for _ in range(3):
        app.state.system_database = TtlCache[DatabaseInfo](ttl=DATABASE_TTL)
        began = time.perf_counter()
        response = client.get(API)
        timings.append(time.perf_counter() - began)
        assert response.status_code == 200
    print(f"\nGET /system: {', '.join(f'{t * 1000:.0f} ms' for t in timings)}")
    assert min(timings) < BUDGET_SECONDS, timings


def _timescale(engine: Engine) -> bool:
    with engine.connect() as connection:
        found = connection.scalar(
            text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
        )
    return bool(found == 1)
