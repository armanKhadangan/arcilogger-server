"""``GET /api/v1/system`` and ``/system/disk`` (Wave 4 contract 2.5, 3 W4-B).

The router is not mounted until the Wave 4 switch-on (W4-F): this module mounts it on the test
application unless a route with its path already exists (Implementation note 3), so the tests
keep passing after W4-F.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import __version__ as api_version
from arcilogger_api import testing as api_testing
from arcilogger_api.app import API_PREFIX
from arcilogger_api.deps import TtlCache
from arcilogger_api.system import resources, routes
from arcilogger_api.system.models import DatabaseInfo, SystemDisk, SystemSummary
from arcilogger_api.system.resources import CpuMeter, configured_name, measure_database
from arcilogger_api.testing import LoggedIn
from arcilogger_core import RELEASE_DATE, hostinfo
from arcilogger_core import __version__ as core_version
from arcilogger_core.config import Config, SourceConfig
from arcilogger_core.hostinfo import CpuTimes
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, site_day, utc_now
from arcilogger_db.settings import get_setting, set_setting
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_worker.alerts.system import drives

Login = Callable[..., LoggedIn]
API = f"{API_PREFIX}/system"
DISK = f"{API}/disk"
GIB = 1024**3


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    """An exact source and a network source, as the status files' entries need them."""
    return Config.model_validate(
        {
            "site_name": api_testing.EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.20.0.1"},
                {"name": "ap", "type": "generic", "address": "172.20.30.0/24"},
                {"name": "lab6", "type": "generic", "address": "2001:db8:6::/64"},
            ],
        }
    )


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The application with the system router, as ``routers/system.py`` will mount it."""
    if not any(getattr(route, "path", None) == API for route in app.routes):
        app.include_router(routes.router, prefix=API_PREFIX)
    return app


@pytest.fixture(autouse=True)
def cpu(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """A fresh processor meter per test, on a clock the test moves."""
    clock = [1000.0]
    monkeypatch.setattr(resources, "CPU", CpuMeter(clock=lambda: clock[0]))
    return clock


def now_site() -> str:
    return isoformat_site(utc_now(), "seconds")


def write_status(layout: Layout, *, updated_at: str | None = None) -> None:
    """Status files as the collector and ingest write them: a configured source, two members of
    the network source, an unknown sender and an entry of a source removed since."""
    updated = updated_at or now_site()
    layout.state.mkdir(parents=True, exist_ok=True)
    collector = {
        "started_at": "2026-10-27T08:00:00+03:00",
        "updated_at": updated,
        "version": "0.1.0",
        "sources": {
            "ap-172.20.30.11": {
                "messages": 10,
                "bytes": 1000,
                "last_received": "2026-10-28T09:00:00+03:00",
            },
            "ap-172.20.30.12": {
                "messages": 5,
                "bytes": 400,
                "last_received": "2026-10-28T09:30:00+03:00",
            },
            "fortigate": {
                "messages": 700,
                "bytes": 90000,
                "last_received": "2026-10-28T09:45:00+03:00",
            },
            "lab6-2001-db8-6--11": {"messages": 2, "bytes": 200, "last_received": None},
            "old-switch": {"messages": 3, "bytes": 300, "last_received": None},
            "unknown-172.20.10.40": {
                "messages": 4,
                "bytes": 512,
                "last_received": "2026-10-28T09:40:00+03:00",
            },
        },
        "dropped_unknown": {"172.20.10.41": 7},
        "dropped_transport": {"172.20.0.1": 2},
        "write_failures": {"fortigate": 1},
        "spool_write_error": {
            "since": "2026-10-28T09:10:00+03:00",
            "last": "2026-10-28T09:12:00+03:00",
            "messages": 3,
            "error": "D:\\ARCILOGGER\\spool\\unknown-172.20.10.40: disk full",
            "sources": ["fortigate", "unknown-172.20.10.40"],
        },
        "spool_write_errors": {"unknown-172.20.10.40": {"error": "disk full"}},
        "torn_lines_total": 2,
        "torn_lines": [{"source": "unknown-172.20.10.40", "sender": "172.20.10.40"}],
        "tcp_connections": 3,
        "tcp_framing_errors": 1,
    }
    ingest = {
        "version": "0.1.0",
        "pid": 4242,
        "state": "running",
        "started_at": "2026-10-27T08:00:00+03:00",
        "updated_at": updated,
        "database": {"ok": True, "last_error": None},
        "parse_workers": {"configured": 2, "active": 2, "restarts": 0, "last_error": None},
        "totals": {"lines": 1000, "rows": 1200, "parse_failures": 1, "lines_per_second": 51.5},
        "sources": {
            "fortigate": {
                "day": "2026-10-28",
                "lag_bytes": 100,
                "lag_seconds": 0.5,
                "lines_per_second": 40.0,
                "unfinished_days": ["2026-10-27", "2026-10-28"],
                "last_error": None,
            },
            "ap-172.20.30.11": {
                "lag_bytes": 10,
                "lag_seconds": 2.0,
                "lines_per_second": 1.5,
                "unfinished_days": ["2026-10-28"],
                "last_error": None,
            },
            "ap-172.20.30.12": {
                "lag_bytes": 20,
                "lag_seconds": None,
                "lines_per_second": None,
                "unfinished_days": ["2026-10-28"],
                "last_error": "line 4 of ap-172.20.30.12 could not be read",
            },
            "unknown-172.20.10.40": {
                "lag_bytes": 5,
                "lag_seconds": 1.0,
                "lines_per_second": 0.1,
                "unfinished_days": ["2026-10-28"],
                "last_error": "connection from 172.20.10.40",
            },
        },
    }
    layout.collector_status.write_text(json.dumps(collector), encoding="utf-8")
    layout.ingest_status.write_text(json.dumps(ingest), encoding="utf-8")


def get(client: TestClient, path: str = API) -> dict[str, Any]:
    response = client.get(path)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def test_system_summary_shape(
    client: TestClient, login_as: Login, layout: Layout, api_config: Config
) -> None:
    write_status(layout)
    login_as(client, "admin")
    body = get(client)
    assert set(body) == set(SystemSummary.model_fields)
    assert SystemSummary.model_validate(body)
    assert datetime.fromisoformat(body["checked_at"]).utcoffset() == timedelta(hours=3)

    host = body["host"]
    assert host["platform"] == ("linux" if os.name == "posix" else "windows")
    assert host["cpu_percent"] is None  # the first reading of this process
    assert host["memory_total_bytes"] > 0
    assert 0 < host["memory_used_bytes"] <= host["memory_total_bytes"]
    assert host["uptime_seconds"] > 0

    data_label = drives(api_config.data_dir)[0][0]
    drive = body["drives"][0]
    assert drive["role"] == "data"
    assert drive["path"] == data_label
    assert drive["total_bytes"] == drive["used_bytes"] + drive["free_bytes"]
    assert drive["used_percent"] == drive["used_bytes"] * 100 // drive["total_bytes"]
    assert (drive["warning_percent"], drive["critical_percent"]) == (80, 95)
    assert drive["forecast"] == {
        "state": "insufficient",
        "samples": 0,
        "growth_bytes_per_day": None,
        "warning_on": None,
        "full_on": None,
    }
    assert [d["role"] for d in body["drives"]] in (["data"], ["data", "system"])

    database = body["database"]
    assert database["storage"] in ("postgresql", "timescaledb")
    assert database["total_bytes"] > 0
    assert 0 < len(database["tables"]) <= 10
    sizes = [table["bytes"] for table in database["tables"]]
    assert sizes == sorted(sizes, reverse=True)
    assert database["measured_at"] is not None

    collector = body["collector"]
    assert collector["state"] == "running"
    assert collector["started_at"] == "2026-10-27T08:00:00+03:00"
    assert collector["sources"] == [
        {
            "name": "fortigate",
            "messages": 700,
            "bytes": 90000,
            "last_received": "2026-10-28T09:45:00+03:00",
        },
        {"name": "ap", "messages": 15, "bytes": 1400, "last_received": "2026-10-28T09:30:00+03:00"},
        {"name": "lab6", "messages": 2, "bytes": 200, "last_received": None},
    ]
    # The unknown sender and the removed source are only counted.
    assert collector["unknown_senders"] == {"count": 2, "messages": 7, "bytes": 812}
    assert (collector["dropped_unknown"], collector["dropped_transport"]) == (7, 2)
    assert collector["write_failures"] == 1
    assert collector["write_error_since"] == "2026-10-28T09:10:00+03:00"
    assert (collector["torn_lines"], collector["tcp_connections"]) == (2, 3)
    assert collector["tcp_framing_errors"] == 1

    ingest = body["ingest"]
    assert (ingest["state"], ingest["service_state"], ingest["database_ok"]) == (
        "running",
        "running",
        True,
    )
    assert (ingest["workers_configured"], ingest["workers_active"]) == (2, 2)
    assert ingest["lines_per_second"] == 51.5
    assert ingest["sources"] == [
        {
            "name": "fortigate",
            "lag_bytes": 100,
            "lag_seconds": 0.5,
            "lines_per_second": 40.0,
            "unfinished_days": 2,
            "has_error": False,
        },
        {
            "name": "ap",
            "lag_bytes": 30,
            "lag_seconds": 2.0,
            "lines_per_second": 1.5,
            "unfinished_days": 2,
            "has_error": True,
        },
    ]
    assert ingest["unknown_sources"] == 1

    assert body["version"] == {
        "product": "ARCILOGGER",
        "version": api_version,
        "core_version": core_version,
        "release_date": RELEASE_DATE,
    }


def test_status_files_missing_or_stale(client: TestClient, login_as: Login, layout: Layout) -> None:
    login_as(client, "admin")
    body = get(client)
    assert body["collector"] == {
        "state": "missing",
        "updated_at": None,
        "started_at": None,
        "sources": [],
        "unknown_senders": {"count": 0, "messages": 0, "bytes": 0},
        "dropped_unknown": 0,
        "dropped_transport": 0,
        "write_failures": 0,
        "write_error_since": None,
        "torn_lines": 0,
        "tcp_connections": 0,
        "tcp_framing_errors": 0,
    }
    assert body["ingest"]["state"] == "missing"
    assert body["ingest"]["sources"] == []
    three_minutes_ago = isoformat_site(utc_now() - timedelta(minutes=3), "seconds")
    write_status(layout, updated_at=three_minutes_ago)
    body = get(client)
    assert (body["collector"]["state"], body["ingest"]["state"]) == ("stale", "stale")
    assert body["collector"]["sources"][0]["name"] == "fortigate"  # still shown
    layout.collector_status.write_text("{half a file", encoding="utf-8")
    assert get(client)["collector"]["state"] == "missing"


def test_system_disk_shape(
    client: TestClient,
    login_as: Login,
    layout: Layout,
    api_config: Config,
    app_session_factory: sessionmaker[Session],
) -> None:
    """``/system/disk`` is the drives alone, with the forecast of the history file and the
    warning mark of ``alerts.disk_used_percent``."""
    layout.state.mkdir(parents=True)  # the data directory exists, as on a server
    label, path = drives(api_config.data_dir)[0]
    total = shutil.disk_usage(path).total
    today = site_day(utc_now())
    layout.system_history.write_text(
        json.dumps(
            {
                "schema": 1,
                "days": [
                    {
                        "day": (today - timedelta(days=9 - i)).isoformat(),
                        "drives": [{"path": label, "total": total, "used": total // 10 + i * GIB}],
                    }
                    for i in range(10)
                ],
            }
        ),
        encoding="utf-8",
    )
    with app_session_factory() as session:
        set_setting(session, "alerts.disk_used_percent", 70, updated_by="tolga")
        session.commit()
    login_as(client, "admin")
    body = get(client, DISK)
    assert set(body) == set(SystemDisk.model_fields)
    drive = body["drives"][0]
    assert drive["warning_percent"] == 70
    forecast = drive["forecast"]
    assert forecast["state"] == "estimated"
    assert forecast["samples"] == 10
    assert forecast["growth_bytes_per_day"] == GIB
    assert date.fromisoformat(forecast["full_on"]) > today
    assert forecast == get(client)["drives"][0]["forecast"]


@pytest.mark.parametrize("role", ["auditor", "hr", "viewer"])
def test_system_is_refused_without_system_read(
    client: TestClient, login_as: Login, role: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    measured: list[int] = []
    monkeypatch.setattr(routes, "measure_database", lambda *_: measured.append(1))
    login_as(client, role)
    for path in (API, DISK):
        response = client.get(path)
        assert response.status_code == 403, (role, path)
        assert response.json()["error"]["code"] == "forbidden"
    assert measured == []  # nothing computed


def test_system_needs_a_session(client: TestClient) -> None:
    for path in (API, DISK):
        assert client.get(path).status_code == 401


@pytest.mark.license_live
def test_system_is_not_licence_gated(client: TestClient, login_as: Login, licence: Any) -> None:
    licence.trial_over()
    login_as(client, "admin")
    assert client.get(f"{API_PREFIX}/license").json()["status"] == "trial_expired"
    assert get(client)["version"]["product"] == "ARCILOGGER"
    assert get(client, DISK)["drives"]


def test_system_returns_no_sender_address(
    client: TestClient, login_as: Login, layout: Layout
) -> None:
    write_status(layout)
    login_as(client, "admin")
    for path in (API, DISK):
        response = client.get(path)
        assert response.status_code == 200, (path, response.text)
        answer = response.text
        assert "172.20." not in answer, path  # no sender, member or configured address
        assert "2001:db8" not in answer, path
        assert "2001-db8" not in answer, path
        assert "lab6-" not in answer, path
        assert "unknown-" not in answer, path
        assert "old-switch" not in answer, path  # a name not configured any more
        assert "disk full" not in answer, path  # error texts are not returned
        assert "could not be read" not in answer, path


def test_configured_names_of_status_entries(api_config: Config) -> None:
    assert configured_name("fortigate", api_config) == "fortigate"
    assert configured_name("ap-172.20.30.11", api_config) == "ap"
    assert configured_name("lab6-2001-db8-6--11", api_config) == "lab6"
    assert configured_name("ap-172.20.31.11", api_config) is None  # outside the network
    assert configured_name("ap-gateway", api_config) is None
    assert configured_name("ap", api_config) is None  # the network's own name has no entry
    assert configured_name("unknown-172.20.10.40", api_config) is None
    # A link-local member keeps its zone id in the spool name ('%' written as '-').
    lan = api_config.model_copy(
        update={
            "sources": [
                *api_config.sources,
                SourceConfig(name="lan", type="generic", address="fe80::/64"),
            ]
        }
    )
    for sender in ("fe80::1%eth0", "fe80::1%12", "fe80::abcd%br-lan", "fe80::2"):
        spool_name = lan.source_name_for(sender)
        assert spool_name is not None
        assert configured_name(spool_name, lan) == "lan", spool_name
    assert configured_name("lan-fe80--1-", lan) is None


def test_database_sizes_are_cached(
    client: TestClient, login_as: Login, app: FastAPI, monkeypatch: pytest.MonkeyPatch
) -> None:
    clock = [0.0]
    app.state.system_database = TtlCache[DatabaseInfo](ttl=300, clock=lambda: clock[0])
    calls: list[datetime] = []
    failing = [True]

    def measured(session: Session, now: datetime) -> DatabaseInfo:
        calls.append(now)
        if failing[0]:  # a failed measurement is not kept
            return DatabaseInfo(storage="postgresql", total_bytes=None, tables=[], measured_at=None)
        return measure_database(session, now)

    monkeypatch.setattr(routes, "measure_database", measured)
    login_as(client, "admin")
    assert get(client)["database"]["total_bytes"] is None
    failing[0] = False
    first = get(client)["database"]
    assert first["total_bytes"] > 0
    clock[0] = 299
    assert get(client)["database"] == first
    assert len(calls) == 2
    clock[0] = 301
    later = get(client)["database"]
    assert len(calls) == 3
    assert later["measured_at"] > first["measured_at"]


def test_a_failed_size_query_answers_null_and_keeps_the_session(
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(resources, "_TABLE_SIZES", text("SELECT no_such_function()"))
    with app_session_factory() as session:
        info = measure_database(session, datetime.now(UTC))
        assert (info.total_bytes, info.tables, info.measured_at) == (None, [], None)
        assert session.scalar(text("SELECT 1")) == 1  # the savepoint was rolled back
    assert "database sizes" in caplog.text


def test_database_sizes_on_timescaledb(
    app_session_factory: sessionmaker[Session], db_engine: Engine
) -> None:
    """On TimescaleDB a hypertable's size is ``hypertable_size()``, compressed chunks included
    (Implementation note 9); the Linux CI job runs it."""
    with app_session_factory() as session:
        status = get_setting(session, "db.timescale") or {}
    if status.get("storage") != "hypertables":
        assert os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") != "1", status
        pytest.skip("TimescaleDB is not installed (the Linux CI job runs this)")
    pause_compression_jobs(db_engine)
    old = datetime.now(UTC) - timedelta(days=20)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO messages (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, type, text) SELECT t, t, 'fortigate', '172.20.0.1', :day, g, 0, "
                "'traffic', repeat('x', 200) FROM generate_series(1, 5000) AS g, "
                "LATERAL (SELECT CAST(:start AS timestamptz) + g * interval '1 second') AS x(t)"
            ),
            {"day": site_day(old), "start": old},
        )
    with db_engine.begin() as connection:
        before = connection.scalar(text("SELECT hypertable_size('messages')"))
        connection.execute(
            text(
                "SELECT compress_chunk(c, if_not_compressed => true) "
                "FROM show_chunks('messages', older_than => CAST(:before AS timestamptz)) AS c"
            ),
            {"before": old + timedelta(days=5)},
        )
        compressed = connection.scalar(
            text(
                "SELECT sum(after_compression_total_bytes) "
                "FROM chunk_compression_stats('messages') WHERE compression_status = 'Compressed'"
            )
        )
        after = connection.scalar(text("SELECT hypertable_size('messages')"))
    assert before > 0
    assert compressed is not None
    assert compressed > 0
    with app_session_factory() as session:
        info = measure_database(session, datetime.now(UTC))
    assert info.storage == "timescaledb"
    sizes = {table.name: table.bytes for table in info.tables}
    assert sizes.get("messages") == after
    assert after >= compressed  # the compressed chunk is counted


def test_first_cpu_reading_is_null(
    client: TestClient, login_as: Login, cpu: list[float], monkeypatch: pytest.MonkeyPatch
) -> None:
    readings = iter(
        [
            CpuTimes(busy=1000, idle=3000),
            CpuTimes(busy=1010, idle=3010),  # 0.5 s later
            CpuTimes(busy=1100, idle=3300),  # 1.5 s after the first
            CpuTimes(busy=1110, idle=3310),  # 0.2 s later
            CpuTimes(busy=1400, idle=3410),  # 1.2 s after the third
        ]
    )
    monkeypatch.setattr(hostinfo, "cpu_times", lambda: next(readings))
    login_as(client, "admin")
    assert get(client)["host"]["cpu_percent"] is None  # the first reading of the process
    cpu[0] += 0.5  # closer than a second: no percentage yet, the first reading is kept
    assert get(client)["host"]["cpu_percent"] is None
    cpu[0] += 1.0
    assert get(client)["host"]["cpu_percent"] == 25  # 100 busy of 400 since the first
    cpu[0] += 0.2
    assert get(client)["host"]["cpu_percent"] == 25  # the last percentage
    cpu[0] += 1.0
    assert get(client)["host"]["cpu_percent"] == 73  # 300 busy of 410 since the third
