"""Kaynaklar (``/api/v1/sources``, contract 2.16, test plan 3.4): every state of a source
(active, silent, never, disabled, unknown, promoted, network member), the suggestion for an unknown
sender, today's lines, ingest lag and failure ratio, the sparkline, a detail's hours (summary table
or direct count, equal), days, gaps from a sealed vault and today's open one, parse failures with
their detail only for raw-line readers, and the audited tail: permissions, KVKK, rate limit, the
no-file case and a ``range_sha256`` an auditor recomputes from the file. The sparkline query and
the suggestion for a busy unknown sender are guarded at 300,000 rows an hour (``poe test-perf``).

The lease evidence card (``lease_evidence``, contract 2.12, 3.5): usable rows only, refused and
excluded rows apart, the empty logid map, the verified format, current leases, ``none`` read from
the active alerts, ``refused`` live and winning over ``none``, unreadable DHCP settings, and the
srcmac count only with interfaces, kept 60 s, null past its 2-s deadline; counts only, never a
client's address, never audited. Its perf guards are in ``test_observed_perf.py``.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, delete, func, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import security, sourcehealth
from arcilogger_api.lookup_testing import accept_privacy
from arcilogger_api.routers.sources import SourceDetail, SourcesOverview, SourceTail, state_of
from arcilogger_api.testing import EXAMPLE_SITE_NAME, LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import day_window, site_day, to_site
from arcilogger_db.engine import session_factory
from arcilogger_db.models import (
    Alert,
    AuditLog,
    FgTraffic,
    IngestOffset,
    Lease,
    Message,
    ParseFailure,
    Source,
)
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts import system
from arcilogger_worker.alerts.system import run_system_checks
from arcilogger_worker.testing import Evidence
from arcilogger_worker.vaulttesting import at_site, make_vault

Login = Callable[..., LoggedIn]
FIREWALL = "172.31.255.1"
ROUTER = "172.20.10.1"
#: The sparkline query on 300,000 messages in the hour must answer within this (perf guard).
SPARKLINE_BUDGET_SECONDS = 1.0
#: The suggestion for an unknown sender of 300,000 messages an hour (perf guard): it reads only
#: the newest 1,000 of them.
SUGGESTION_BUDGET_SECONDS = 0.05


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "site_name": EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": FIREWALL},
                {"name": "core-r1", "type": "mikrotik", "address": ROUTER},
                # the access points: a block of the network-device VLAN 10
                {"name": "ap", "type": "mikrotik", "address": "172.20.10.192/26"},
                {
                    "name": "lab-switch",
                    "type": "generic",
                    "address": "172.20.10.45",
                    "enabled": False,
                },
                {
                    "name": "printer",
                    "type": "generic",
                    "address": "172.20.10.46",
                    "expected": False,
                    "heartbeat_minutes": 60,
                },
            ],
        }
    )


@pytest.fixture(autouse=True)
def _fresh_caches(app: FastAPI) -> None:
    state_of_app(app).overview.ttl = 0.0
    state_of_app(app).detail_seconds = 0.0


def state_of_app(app: FastAPI) -> Any:
    class _Request:
        def __init__(self, app: FastAPI) -> None:
            self.app = app

    return state_of(_Request(app))  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch) -> datetime:
    """The moment the test data is written around: the latest site noon, not the real now. The
    router reads the same moment, so a request that runs past a minute boundary still sees the
    data where it was put.

    At the real now the firewall's minute three minutes before it was in yesterday's file from
    00:00 to 00:03 site time, and the tests count today's file only (``0 == 12``). Noon is 12
    hours from either midnight. It is never more than a day old: a fixed day would age past
    the 7-day compression policy of the TimescaleDB job."""
    real = datetime.now(UTC)
    noon = day_window(site_day(real))[0] + timedelta(hours=12)
    moment = (noon if noon <= real else noon - timedelta(days=1)).astimezone(UTC)
    monkeypatch.setattr("arcilogger_api.routers.sources.utc_now", lambda: moment)
    return moment


@pytest.fixture(autouse=True)
def _partitions(app_session_factory: sessionmaker[Session], now: datetime) -> None:
    with app_session_factory() as session:
        ensure_partitions(session.connection(), today=site_day(now))
        session.commit()


def reader(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str = "admin"
) -> LoggedIn:
    who = login_as(client, role)
    accept_privacy(factory, who.user_id)
    return who


def message(
    source: str,
    at: datetime,
    *,
    line: int = 1,
    type_: str = "traffic",
    subtype: str | None = None,
    host: str | None = None,
) -> Message:
    return Message(
        time=at,
        time_received=at,
        source=source,
        sender=FIREWALL,
        raw_day=site_day(at),
        raw_line=line,
        raw_offset=0,
        type=type_,
        subtype=subtype,
        host=host,
        text="x",
    )


def write_collector_status(layout: Layout, now: datetime, received: dict[str, datetime]) -> None:
    layout.collector_status.parent.mkdir(parents=True, exist_ok=True)
    layout.collector_status.write_text(
        json.dumps(
            {
                "updated_at": now.isoformat(),
                "sources": {
                    name: {"last_received": at.isoformat()} for name, at in received.items()
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def site(app_session_factory: sessionmaker[Session], layout: Layout, now: datetime) -> None:
    """Sources in every state, today's offsets, failures and a minute of messages."""
    with app_session_factory() as session:
        rows = [
            Source(
                name="fortigate",
                type="fortigate",
                address=FIREWALL,
                first_seen=now - timedelta(days=3),
                last_seen=now - timedelta(minutes=50),
            ),
            Source(
                name="core-r1",
                type="mikrotik",
                address=ROUTER,
                first_seen=now - timedelta(days=3),
                last_seen=now - timedelta(minutes=40),
            ),
            Source(
                name="ap-172.20.10.200",
                type="mikrotik",
                address="172.20.10.200",
                first_seen=now - timedelta(hours=2),
                last_seen=now - timedelta(minutes=2),
            ),
            Source(
                name="unknown-198.51.100.23",
                type="fortigate",
                address="198.51.100.23",
                unknown=True,
                first_seen=now - timedelta(hours=1),
                last_seen=now - timedelta(minutes=1),
            ),
            Source(
                name="unknown-172.20.10.211",
                type="generic",
                address="172.20.10.211",
                unknown=True,
                first_seen=now - timedelta(days=2),
                last_seen=now - timedelta(days=2),
            ),
            Source(
                name="lab-switch",
                type="generic",
                address="172.20.10.45",
                enabled=False,
                last_seen=now - timedelta(minutes=1),
            ),
            Source(
                name="printer",
                type="generic",
                address="172.20.10.46",
                expected=False,
                heartbeat_minutes=60,
            ),
            Source(
                name="old-router",
                type="mikrotik",
                address="172.20.10.2",
                enabled=False,
                expected=False,
                last_seen=now - timedelta(days=9),
            ),
        ]
        session.add_all(rows)
        today = site_day(now)
        session.add_all(
            [
                IngestOffset(source="fortigate", day=today, byte_offset=1000, line_number=2000),
                IngestOffset(source="core-r1", day=today, byte_offset=0, line_number=0),
            ]
        )
        session.add(
            ParseFailure(
                source="fortigate",
                day=today,
                pattern="fortigate.kv",
                count=5,
                first_seen=now,
                last_seen=now,
                example_line=7,
                example_offset=700,
                error="bad value srcip=ornek",
            )
        )
        minute = now.replace(second=0, microsecond=0) - timedelta(minutes=3)
        session.add_all(
            message("fortigate", minute + timedelta(seconds=n), line=n + 1) for n in range(12)
        )
        session.add_all(
            message(
                "unknown-198.51.100.23",
                now - timedelta(minutes=30 + n),
                line=n + 1,
                type_="traffic",
                host="Yeni Cihaz_01",
            )
            for n in range(3)
        )
        session.add(
            message(
                "unknown-198.51.100.23",
                now - timedelta(minutes=29),
                line=4,
                type_="utm",
                subtype="webfilter",
                host="Yeni Cihaz_01",
            )
        )
        session.commit()
    path = layout.spool_file("fortigate", site_day(now))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * 1500)
    # the collector received from the firewall just now (ingest's newest row is older)
    write_collector_status(layout, now, {"fortigate": now - timedelta(seconds=30)})


def overview(client: TestClient) -> dict[str, dict[str, Any]]:
    response = client.get("/api/v1/sources")
    assert response.status_code == 200, response.text
    data = SourcesOverview.model_validate(response.json())
    assert data.collector.fresh is True
    return {item["name"]: item for item in response.json()["items"]}


def test_every_state_of_a_source(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: None,
    now: datetime,
) -> None:
    reader(client, login_as, app_session_factory)
    items = overview(client)
    states = {name: item["state"] for name, item in items.items()}
    assert states == {
        "fortigate": "active",  # by the collector's receive time
        "core-r1": "silent",
        "ap": "active",  # a network: active when a member is
        "ap-172.20.10.200": "active",
        "unknown-198.51.100.23": "unknown",
        "unknown-172.20.10.211": "promoted",
        "lab-switch": "disabled",
        "printer": "never",
        "old-router": "disabled",
    }
    assert list(items).index("ap-172.20.10.200") == list(items).index("ap") + 1  # members follow
    fortigate, core = items["fortigate"], items["core-r1"]
    assert fortigate["last_message_origin"] == "collector"
    assert core["last_message_origin"] == "database"
    assert core["silent_minutes"] == 40
    assert fortigate["silent_minutes"] is None
    assert items["ap-172.20.10.200"]["network_of"] == "ap"
    assert items["ap-172.20.10.200"]["configured"] is True
    assert items["ap"]["configured"] is True
    assert items["ap"]["address"] == "172.20.10.192/26"
    assert items["unknown-172.20.10.211"]["promoted_to"] == "ap-172.20.10.211"
    assert items["unknown-198.51.100.23"]["promoted_to"] is None
    assert items["unknown-198.51.100.23"]["configured"] is False
    assert items["printer"]["expected"] is False
    assert items["old-router"]["configured"] is False
    assert items["lab-switch"]["enabled"] is False


def test_suggestions_for_unknown_senders(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: None
) -> None:
    reader(client, login_as, app_session_factory)
    items = overview(client)
    assert items["unknown-198.51.100.23"]["suggested"] == {
        "name": "yeni-cihaz-01",
        "type": "fortigate",
    }
    assert items["unknown-172.20.10.211"]["suggested"] is None  # promoted already
    assert items["fortigate"]["suggested"] is None
    assert sourcehealth.slug("FIREWALL-01") == "firewall-01"
    assert sourcehealth.slug("---") is None
    assert sourcehealth.slug("unknown-host") is None


def test_suggestion_falls_back_to_the_address(
    app_session_factory: sessionmaker[Session], api_config: Config, now: datetime
) -> None:
    with app_session_factory() as session:
        session.add(message("unknown-198.51.100.77", now - timedelta(minutes=5), type_="syslog"))
        session.commit()
        found = sourcehealth.suggestions(
            session, ["unknown-198.51.100.77", "unknown-2001-db8--9"], api_config, now
        )
    assert found == {
        "unknown-198.51.100.77": {"name": "kaynak-77", "type": "generic"},
        "unknown-2001-db8--9": {"name": "kaynak-9", "type": "generic"},
    }


def test_lines_lag_failures_and_sparkline(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: None,
    now: datetime,
) -> None:
    reader(client, login_as, app_session_factory)
    fortigate = overview(client)["fortigate"]
    assert fortigate["lines_today"] == 2000
    assert fortigate["pending_bytes"] == 500  # 1,500 bytes in the file, 1,000 loaded
    assert fortigate["parse_failures_today"] == 5
    assert fortigate["parse_failure_permille"] == 2.5
    assert len(fortigate["sparkline"]) == 60
    assert sum(fortigate["sparkline"]) == 12
    assert fortigate["sparkline"][-3] == 12  # the minute three minutes ago
    assert fortigate["messages_per_minute"] == 0.2
    assert overview(client)["core-r1"]["parse_failure_permille"] is None  # no lines today


def test_the_ingest_lag_counts_yesterdays_unfinished_file_after_midnight(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: None,
    layout: Layout,
    now: datetime,
) -> None:
    today = site_day(now)
    yesterday, older = today - timedelta(days=1), today - timedelta(days=3)
    with app_session_factory() as session:
        session.add_all(
            [
                # ingest is 3,000 bytes behind on yesterday's file and never finished an older one
                IngestOffset(source="fortigate", day=yesterday, byte_offset=1000, line_number=10),
                IngestOffset(source="fortigate", day=older, byte_offset=100, line_number=1),
                # finished: nothing left, whatever the file's size
                IngestOffset(
                    source="core-r1",
                    day=yesterday,
                    byte_offset=10,
                    line_number=1,
                    finished_at=now - timedelta(hours=1),
                ),
            ]
        )
        session.commit()
    for name, day, size in (
        ("fortigate", yesterday, 4000),
        ("fortigate", older, 300),
        ("core-r1", yesterday, 900),
        ("unknown-198.51.100.23", yesterday, 700),  # no row: ingest has not started it
    ):
        path = layout.spool_file(name, day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * size)
    reader(client, login_as, app_session_factory)
    items = overview(client)
    assert items["fortigate"]["pending_bytes"] == 500 + 3000 + 200
    assert items["fortigate"]["lines_today"] == 2000  # today's only
    assert items["core-r1"]["pending_bytes"] == 0
    assert items["unknown-198.51.100.23"]["pending_bytes"] == 700


def test_detail_hours_days_gaps_and_failures(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    site: None,
    now: datetime,
) -> None:
    reader(client, login_as, app_session_factory)
    with app_session_factory() as session:  # what ingest's counts step keeps up to date
        rebuild_stream(session.connection(), "fortigate", site_day(now))
        session.commit()
    yesterday = site_day(now) - timedelta(days=1)
    vault = make_vault(Path(api_config.data_dir), tsa_now=at_site(yesterday + timedelta(days=1)))
    vault.write_day(yesterday, sources=("core-r1",))  # nothing from the firewall that day
    assert vault.seal(yesterday).status == "sealed"
    response = client.get("/api/v1/sources/fortigate")
    assert response.status_code == 200, response.text
    detail = SourceDetail.model_validate(response.json())
    assert detail.state == "active"
    assert len(detail.hourly) == 48
    assert sum(point.count for point in detail.hourly) == 12
    assert detail.daily[0].day == site_day(now)
    assert (detail.daily[0].lines, detail.daily[0].failures, detail.daily[0].sealed) == (
        2000,
        5,
        False,
    )
    assert detail.daily[1].day == yesterday
    assert detail.daily[1].sealed is True
    manifest_gaps = [gap for gap in detail.gaps if gap.origin == "manifest"]
    start, end = day_window(yesterday)
    assert [(gap.from_, gap.to, gap.reason) for gap in manifest_gaps] == [
        (start, end, "no messages")
    ]
    assert manifest_gaps[0].minutes == 24 * 60
    assert [gap.origin for gap in detail.gaps] == ["manifest"]  # the collector heard it just now
    failure = detail.parse_failures[0]
    assert (failure.pattern, failure.count, failure.error) == (
        "fortigate.kv",
        5,
        "bad value srcip=ornek",
    )
    assert failure.example.model_dump() == {
        "source": "fortigate",
        "day": site_day(now),
        "line": 7,
        "offset": 700,
    }
    assert response.json()["gaps"][0]["from"] == to_site(start).isoformat()
    # core-r1 is silent for 40 minutes: no gap yet (seal.gap_minutes 60); 70 minutes: one
    with app_session_factory() as session:
        session.get_one(Source, "core-r1").last_seen = now - timedelta(minutes=70)
        session.commit()
    gaps = client.get("/api/v1/sources/core-r1").json()["gaps"]
    assert [(gap["origin"], gap["minutes"], gap["reason"]) for gap in gaps] == [
        ("live", 70, "no messages")
    ]
    network = client.get("/api/v1/sources/ap").json()
    assert network["state"] == "active"
    assert client.get("/api/v1/sources/printer").json()["gaps"] == []  # not expected


def test_hours_from_the_summary_table_equal_direct_counting(
    app_session_factory: sessionmaker[Session], site: None, now: datetime
) -> None:
    """While a file of the range is not in ``search_counts`` yet, the hours are counted from
    ``messages``; once it is, they come from the summary table, with the same numbers."""
    yesterday = site_day(now) - timedelta(days=1)
    with app_session_factory() as session:
        session.add(IngestOffset(source="fortigate", day=yesterday, counts_from_line=1))
        session.commit()
        direct = sourcehealth.hourly(session, ["fortigate"], now)
        session.execute(
            delete(IngestOffset).where(
                IngestOffset.source == "fortigate", IngestOffset.day == yesterday
            )
        )
        rebuild_stream(session.connection(), "fortigate", site_day(now))
        session.commit()
        from_table = sourcehealth.hourly(session, ["fortigate"], now)
    assert sum(point["count"] for point in direct) == 12
    assert from_table == direct


def test_unknown_names_and_permissions(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: None
) -> None:
    reader(client, login_as, app_session_factory)
    for name in ("nobody", "unknown-198.51.100.99", "_audit", "..%2f.."):
        response = client.get(f"/api/v1/sources/{name}")
        assert response.status_code == 404, name
        if response.headers.get("content-type", "").startswith("application/json"):
            assert response.json()["error"]["code"] in ("source_not_found", "not_found")
    assert client.get("/api/v1/sources/nobody").json()["error"]["message_tr"].startswith("Bu adla")
    for role in ("auditor", "hr", "viewer"):
        other = TestClient(client.app, base_url="http://testserver")
        reader(other, login_as, app_session_factory, role)
        for path in (
            "/api/v1/sources",
            "/api/v1/sources/fortigate",
            "/api/v1/sources/fortigate/tail",
        ):
            response = other.get(path)
            assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden"), (
                role,
                path,
            )


@pytest.mark.parametrize(
    "path", ["/api/v1/sources", "/api/v1/sources/fortigate", "/api/v1/sources/fortigate/tail"]
)
def test_a_password_change_comes_first(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: None,
    path: str,
) -> None:
    who = login_as(client, "admin", must_change_password=True)
    accept_privacy(app_session_factory, who.user_id)
    response = client.get(path)
    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "password_change_required",
    )


def test_failure_details_and_the_tail_need_raw_access(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the detail can echo a value of the line: shown under the raw-line rule, so only once the
    # KVKK notice is accepted
    who = login_as(client, "admin")
    detail = client.get("/api/v1/sources/fortigate").json()
    assert detail["parse_failures"][0]["error"] is None
    accept_privacy(app_session_factory, who.user_id)
    detail = client.get("/api/v1/sources/fortigate").json()
    assert detail["parse_failures"][0]["error"] == "bad value srcip=ornek"
    without = security.ALL_PERMISSIONS - {"search.read", "lookup.run"}
    monkeypatch.setitem(security.ROLE_PERMISSIONS, "admin", without)
    detail = client.get("/api/v1/sources/fortigate").json()
    assert detail["parse_failures"][0]["error"] is None
    response = client.get("/api/v1/sources/fortigate/tail")
    assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden")


def spool(layout: Layout, folder: str, day: date, count: int, sender: str = FIREWALL) -> Path:
    path = layout.spool_file(folder, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    start = day_window(day)[0]
    with path.open("wb") as handle:
        for n in range(count):
            handle.write(
                format_line(
                    start + timedelta(seconds=n), sender, f'logid="0000000013" n={n} #'.encode()
                )
            )
        handle.write(b"2026-10-26T10:00:00.000000+03:00 172.31.255.1 yar")  # still being written
    return path


def test_the_tail_is_exact_and_audited(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: None,
    now: datetime,
) -> None:
    who = reader(client, login_as, app_session_factory)
    path = spool(layout, "core-r1", site_day(now), 300, ROUTER)
    response = client.get("/api/v1/sources/core-r1/tail", params={"limit": 20})
    assert response.status_code == 200, response.text
    tail = SourceTail.model_validate(response.json())
    assert (tail.day, tail.origin, tail.from_line, tail.to_line) == (
        site_day(now),
        "spool",
        281,
        300,
    )
    assert [line.line for line in tail.lines] == list(range(281, 301))
    data = path.read_bytes()
    assert tail.from_offset is not None
    assert tail.to_offset is not None
    assert hashlib.sha256(data[tail.from_offset : tail.to_offset]).hexdigest() == tail.range_sha256
    last = tail.lines[-1]
    stored = data[last.offset : data.index(b"\n", last.offset)]
    assert last.sha256 == hashlib.sha256(stored).hexdigest()
    assert last.sender == ROUTER
    assert last.text == 'logid="0000000013" n=299 #043'  # the spool's escape stays visible
    raw = client.get(
        "/api/v1/raw",
        params={
            "source": "core-r1",
            "day": str(tail.day),
            "line": last.line,
            "offset": last.offset,
        },
    )
    assert raw.json()["sha256"] == last.sha256
    with app_session_factory() as session:
        entry = session.scalars(select(AuditLog).where(AuditLog.action == "raw.tail")).one()
    assert entry.actor_id == who.user_id
    assert entry.details == {
        "source": "core-r1",
        "day": site_day(now).isoformat(),
        "origin": "spool",
        "from_line": 281,
        "to_line": 300,
        "from_offset": tail.from_offset,
        "to_offset": tail.to_offset,
        "count": 20,
        "range_sha256": tail.range_sha256,
    }
    assert "n=299" not in json.dumps(entry.details)  # never the content


def test_the_tail_falls_back_to_yesterday_and_says_when_there_is_no_file(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: None,
    now: datetime,
) -> None:
    reader(client, login_as, app_session_factory)
    spool(layout, "core-r1", site_day(now) - timedelta(days=1), 5, ROUTER)
    tail = client.get("/api/v1/sources/core-r1/tail").json()
    assert (tail["day"], tail["to_line"], len(tail["lines"])) == (
        (site_day(now) - timedelta(days=1)).isoformat(),
        5,
        5,
    )
    empty = client.get("/api/v1/sources/printer/tail").json()
    assert empty == {
        "source": "printer",
        "day": None,
        "origin": None,
        "from_line": None,
        "to_line": None,
        "from_offset": None,
        "to_offset": None,
        "range_sha256": None,
        "lines": [],
        "reason": "no_file_today",
    }
    # A configured network has no folder of its own: its lines are under its members.
    spool(layout, "ap-172.20.10.200", site_day(now), 3, "172.20.10.200")
    network = client.get("/api/v1/sources/ap/tail").json()
    assert network == {**empty, "source": "ap", "reason": "network_source"}
    with app_session_factory() as session:
        audited = session.scalars(
            select(AuditLog.details).where(AuditLog.action == "raw.tail")
        ).all()
    assert [entry["source"] for entry in audited] == ["core-r1"]  # nothing read, nothing audited


def test_the_tail_needs_the_kvkk_notice_and_is_rate_limited(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: None,
    now: datetime,
) -> None:
    login_as(client, "admin")
    response = client.get("/api/v1/sources/core-r1/tail")
    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "privacy_notice_required",
    )
    other = TestClient(app, base_url="http://testserver")
    reader(other, login_as, app_session_factory)
    spool(layout, "core-r1", site_day(now), 3, ROUTER)
    for _ in range(30):
        assert other.get("/api/v1/sources/core-r1/tail", params={"limit": 1}).status_code == 200
    refused = other.get("/api/v1/sources/core-r1/tail")
    assert (refused.status_code, refused.json()["error"]["code"]) == (429, "rate_limited")
    assert int(refused.headers["Retry-After"]) >= 1


def test_an_unreadable_spool_file_is_503(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    site: None,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader(client, login_as, app_session_factory)
    spool(layout, "core-r1", site_day(now), 3, ROUTER)

    def denied(*args: Any, **kwargs: Any) -> Any:
        raise PermissionError(13, "Access is denied")

    monkeypatch.setattr("arcilogger_api.routers.sources.read_tail", denied)
    response = client.get("/api/v1/sources/core-r1/tail")
    assert (response.status_code, response.json()["error"]["code"]) == (503, "raw_access_denied")


def test_the_overview_is_cached(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: None,
    now: datetime,
) -> None:
    reader(client, login_as, app_session_factory)
    state_of_app(app).overview.ttl = 15.0
    first = overview(client)
    with app_session_factory() as session:
        session.add(Source(name="new-device", type="generic", address="172.20.10.47"))
        session.commit()
    assert overview(client).keys() == first.keys()  # within 15 s: the same answer


# -- the lease evidence card (contract 2.12, 3.5) -------------------------------------------------

PC, PC_MAC = "172.20.42.57", "00:00:5e:00:53:57"
#: An unconfigured sender (RFC 5737) and a logid the default map does not name.
UNKNOWN = "unknown-192.0.2.7"
UNMAPPED_LOGID = "0100026099"


def audit_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(AuditLog)) or 0)


def lease_card(client: TestClient) -> dict[str, Any]:
    response = client.get("/api/v1/sources")
    assert response.status_code == 200, response.text
    data = SourcesOverview.model_validate(response.json())
    evidence: dict[str, Any] = response.json()["lease_evidence"]
    assert data.lease_evidence.model_dump(mode="json") == evidence
    return evidence


def set_value(factory: sessionmaker[Session], key: str, value: Any) -> None:
    with factory() as session:
        set_setting(session, key, value, updated_by="admin")
        session.commit()


def add_alert(factory: sessionmaker[Session], key: str, state: str = "open") -> None:
    with factory() as session:
        session.add(
            Alert(
                rule="R20-lease", severity="high", state=state, title="Kira kanıtı", dedup_key=key
            )
        )
        session.commit()


@pytest.fixture
def lease_checks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the lease evidence check of ``alerts.system`` runs (in its own transaction)."""
    checks = tuple((name, check) for name, check in system.CHECKS if name == "lease_evidence")
    monkeypatch.setattr(system, "CHECKS", checks)


@pytest.fixture
def evidence_rows(
    app_session_factory: sessionmaker[Session], layout: Layout, now: datetime
) -> None:
    """DHCP and ARP rows of the last 24 hours, usable and not, and leases."""
    with app_session_factory() as session:
        session.add(Source(name=UNKNOWN, type="fortigate", address="192.0.2.7", unknown=True))
        evidence = Evidence(session, layout)
        evidence.fortigate_dhcp(now - timedelta(hours=3), PC, PC_MAC)
        evidence.fortigate_dhcp(now - timedelta(minutes=30), PC, PC_MAC)
        evidence.fortigate_dhcp(now - timedelta(hours=30), PC, PC_MAC)  # outside 24 h
        # refused: an unconfigured sender, the receive time, an unmapped logid
        evidence.fortigate_dhcp(now - timedelta(hours=2), PC, PC_MAC, source=UNKNOWN)
        evidence.fortigate_dhcp(now - timedelta(minutes=20), PC, PC_MAC, device_time=False)
        evidence.fortigate_dhcp(now - timedelta(minutes=10), PC, PC_MAC, logid=UNMAPPED_LOGID)
        # excluded (configuration): an IPv6 address
        evidence.fortigate_dhcp(now - timedelta(hours=4), "fd00:0:0:42::57", PC_MAC)
        evidence.dhcp(now - timedelta(hours=5), "assigned", "172.20.43.20", "00:00:5e:00:53:20")
        evidence.dhcp(
            now - timedelta(minutes=45), "deassigned", "172.20.43.20", "00:00:5e:00:53:20"
        )
        evidence.arp(now - timedelta(hours=2), "172.20.43.21", "00:00:5e:00:53:21")
        evidence.arp(now - timedelta(minutes=90), "172.20.43.22", "00:00:5e:00:53:22")
        session.add_all(
            [
                lease("fg_dhcp", now - timedelta(hours=3), None),  # open
                lease("mt_dhcp", now - timedelta(hours=5), now - timedelta(hours=2)),
                lease("mt_dhcp", now - timedelta(days=3), now - timedelta(days=2)),  # not current
            ]
        )
        session.commit()


def lease(table: str, start: datetime, end: datetime | None) -> Lease:
    """A lease of ``table`` (open without ``end``); fictional addresses."""
    return Lease(
        ip="172.20.43.30",
        mac="00:00:5e:00:53:30",
        start=start,
        end=end,
        end_reason="open" if end is None else "dhcp" if table != "mt_arp" else "arp",
        confidence="high",
        source_table=table,
    )


def test_lease_evidence_counts_usable_rows_only(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    evidence_rows: None,
    now: datetime,
) -> None:
    """``rows`` are the rows the lease job uses: the unknown sender's, the receive-time and the
    unmapped-logid rows are refused, the IPv6 row is excluded; none of them is in ``rows``."""
    reader(client, login_as, app_session_factory)
    before = audit_count(app_session_factory)
    response = client.get("/api/v1/sources")
    card = lease_card(client)
    assert card == {
        "hours": 24,
        "mt_dhcp": {
            "rows": 2,
            "last_hour": to_site(now - timedelta(minutes=45)).isoformat(),
            "leases_current": 1,
        },
        "fg_dhcp": {
            "rows": 2,
            "last_hour": to_site(now - timedelta(minutes=30)).isoformat(),
            "leases_current": 1,
            "refused_rows": 3,
            "excluded_rows": 1,
            "format_verified": False,
        },
        "mt_arp": {"rows": 2, "last_hour": None, "leases_current": 0},
        "srcmac": {"interfaces": [], "rows_last_hour": None},
        "state": "refused",
        "none_tables": [],
    }
    # counts only: no client address or MAC, and nothing audited (sources.read, admin only)
    text_of = json.dumps(response.json()["lease_evidence"])
    for personal in (PC, PC_MAC, "172.20.43.", "00:00:5e", "192.0.2.7"):
        assert personal not in text_of
    assert audit_count(app_session_factory) == before


def test_last_hour_is_the_newest_usable_row(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    now: datetime,
) -> None:
    reader(client, login_as, app_session_factory)
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        evidence.fortigate_dhcp(now - timedelta(hours=2), PC, PC_MAC)
        # refused rows of the last hour do not make it
        evidence.fortigate_dhcp(now - timedelta(minutes=5), PC, PC_MAC, logid=UNMAPPED_LOGID)
        session.commit()
    card = lease_card(client)
    assert (card["fg_dhcp"]["rows"], card["fg_dhcp"]["last_hour"]) == (1, None)
    with app_session_factory() as session:
        Evidence(session, layout).fortigate_dhcp(now - timedelta(minutes=30), PC, PC_MAC)
        Evidence(session, layout).fortigate_dhcp(now - timedelta(minutes=50), PC, PC_MAC)
        session.commit()
    card = lease_card(client)
    assert card["fg_dhcp"]["rows"] == 3
    assert card["fg_dhcp"]["last_hour"] == to_site(now - timedelta(minutes=30)).isoformat()


def test_excluded_interfaces_are_not_refused(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    now: datetime,
) -> None:
    set_value(app_session_factory, "dhcp.fortigate_interfaces_excluded", ["ipsec1"])
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        evidence.fortigate_dhcp(now - timedelta(hours=1), PC, PC_MAC)
        for minutes in (10, 20):
            evidence.fortigate_dhcp(
                now - timedelta(minutes=minutes), "172.20.90.5", PC_MAC, interface="ipsec1"
            )
        session.commit()
    reader(client, login_as, app_session_factory)
    card = lease_card(client)
    fortigate = card["fg_dhcp"]
    assert (fortigate["rows"], fortigate["refused_rows"], fortigate["excluded_rows"]) == (1, 0, 2)
    assert card["state"] == "ok"


def test_an_empty_logid_map_refuses_nothing_on_the_card(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    evidence_rows: None,
) -> None:
    """FortiGate DHCP evidence switched off (``dhcp.fortigate_logids = {}``): no row is usable
    and none counts as refused, as the alert reads it (round-5 note)."""
    set_value(app_session_factory, "dhcp.fortigate_logids", {})
    reader(client, login_as, app_session_factory)
    card = lease_card(client)
    assert (card["fg_dhcp"]["rows"], card["fg_dhcp"]["refused_rows"]) == (0, 0)
    assert card["fg_dhcp"]["excluded_rows"] == 1  # configuration stays configuration
    assert card["state"] == "ok"


def test_format_verified_follows_the_setting(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], now: datetime
) -> None:
    reader(client, login_as, app_session_factory)
    assert lease_card(client)["fg_dhcp"]["format_verified"] is False
    set_value(app_session_factory, "dhcp.fortigate_format_verified", True)
    assert lease_card(client)["fg_dhcp"]["format_verified"] is True


def test_leases_current_per_table(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    evidence_rows: None,
    now: datetime,
) -> None:
    """Open or ended in the last 24 hours: the open fg_dhcp lease and the mt_dhcp lease that
    ended 2 hours ago; not the one that ended 2 days ago."""
    with app_session_factory() as session:
        session.add(lease("mt_arp", now - timedelta(hours=30), now - timedelta(hours=23)))
        session.commit()
    reader(client, login_as, app_session_factory)
    card = lease_card(client)
    assert [card[table]["leases_current"] for table in ("mt_dhcp", "fg_dhcp", "mt_arp")] == [
        1,
        1,
        1,
    ]


def test_lease_evidence_none_follows_the_active_alerts(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    api_config: Config,
    now: datetime,
    lease_checks: None,
) -> None:
    """``none`` is read from the alerts the check opened, never recomputed: an old ACK and
    office traffic open ``R20-lease:none:fg_dhcp``; an acknowledged alert still counts; once a
    usable ACK arrives and the check resolves it, the card is ``ok``."""
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        evidence.fortigate_dhcp(now - timedelta(days=5), PC, PC_MAC)
        evidence.traffic(now - timedelta(minutes=10), "172.20.43.20", 50000)
        session.commit()
    reader(client, login_as, app_session_factory)
    assert lease_card(client)["state"] == "ok"  # nothing read until the check ran
    run_system_checks(app_session_factory, layout, api_config, now=now)
    card = lease_card(client)
    assert (card["state"], card["none_tables"]) == ("none", ["fg_dhcp"])
    with app_session_factory() as session:
        alert = session.scalars(
            select(Alert).where(Alert.dedup_key == "R20-lease:none:fg_dhcp")
        ).one()
        alert.state = "acknowledged"
        session.commit()
    assert (lease_card(client)["state"], lease_card(client)["none_tables"]) == ("none", ["fg_dhcp"])
    with app_session_factory() as session:
        Evidence(session, layout).fortigate_dhcp(now - timedelta(minutes=5), PC, PC_MAC)
        session.commit()
    assert lease_card(client)["state"] == "none"  # until the check resolves it
    run_system_checks(app_session_factory, layout, api_config, now=now)
    card = lease_card(client)
    assert (card["state"], card["none_tables"]) == ("ok", [])


def test_absent_gives_none_without_tables(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], now: datetime
) -> None:
    add_alert(app_session_factory, "R20-lease:absent")
    add_alert(app_session_factory, "R20-lease:none:mt_arp", state="resolved")  # not active
    add_alert(app_session_factory, "R20-lease:renewals-missing")  # another cause
    reader(client, login_as, app_session_factory)
    card = lease_card(client)
    assert (card["state"], card["none_tables"]) == ("none", [])
    add_alert(app_session_factory, "R20-lease:none:mt_arp")
    add_alert(app_session_factory, "R20-lease:none:mt_dhcp")
    card = lease_card(client)
    assert (card["state"], card["none_tables"]) == ("none", ["mt_dhcp", "mt_arp"])


def test_refused_wins_over_none(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    now: datetime,
) -> None:
    """Every row of the feed refused while its ``none`` alert is active: the records arrive, so
    the card says ``refused``."""
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        for minutes in (10, 70, 130):
            evidence.fortigate_dhcp(
                now - timedelta(minutes=minutes), PC, PC_MAC, logid=UNMAPPED_LOGID
            )
        session.commit()
    add_alert(app_session_factory, "R20-lease:none:fg_dhcp")
    reader(client, login_as, app_session_factory)
    card = lease_card(client)
    assert (card["fg_dhcp"]["rows"], card["fg_dhcp"]["refused_rows"]) == (0, 3)
    assert (card["state"], card["none_tables"]) == ("refused", ["fg_dhcp"])


def test_a_corrected_setting_leaves_refused_at_once(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    api_config: Config,
    now: datetime,
    lease_checks: None,
) -> None:
    """The refused count is live: once the logid is mapped the card leaves ``refused`` without
    waiting for the alert, which is still open until its check runs."""
    with app_session_factory() as session:
        evidence = Evidence(session, layout)
        for minutes in (10, 70):
            evidence.fortigate_dhcp(
                now - timedelta(minutes=minutes), PC, PC_MAC, logid=UNMAPPED_LOGID
            )
        evidence.traffic(now - timedelta(minutes=10), "172.20.43.20", 50000)
        session.commit()
    run_system_checks(app_session_factory, layout, api_config, now=now)
    reader(client, login_as, app_session_factory)
    card = lease_card(client)
    assert (card["state"], card["fg_dhcp"]["refused_rows"]) == ("refused", 2)
    logids = {"0100026001": "assigned", "0100026002": "deassigned", UNMAPPED_LOGID: "assigned"}
    set_value(app_session_factory, "dhcp.fortigate_logids", logids)
    card = lease_card(client)
    assert (card["fg_dhcp"]["rows"], card["fg_dhcp"]["refused_rows"]) == (2, 0)
    # no longer refused; the absent cause the check opened (no usable row then) is read as it
    # stands until the check runs again
    assert (card["state"], card["none_tables"]) == ("none", [])
    with app_session_factory() as session:
        active = dict(
            session.execute(
                select(Alert.dedup_key, Alert.state).where(
                    Alert.state.in_(("open", "acknowledged"))
                )
            ).all()
        )
    assert active.get("R20-lease:fortigate-refused") == "open"
    assert active.get("R20-lease:absent") == "open"
    run_system_checks(app_session_factory, layout, api_config, now=now)
    assert lease_card(client)["state"] == "ok"


def test_unreadable_dhcp_settings_keep_the_card(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    evidence_rows: None,
) -> None:
    """A ``dhcp.fortigate_*`` value written around the validator: never a 500 and never a card
    without lease evidence; the lease job cannot use any ``fg_dhcp`` row then (``Context.load``
    raises), so no row counts as usable, refused or excluded, ``format_verified`` is only a
    stored JSON ``true`` and the state comes from the alerts (decision X8, review 3, Q10)."""
    set_value(app_session_factory, "dhcp.fortigate_logids", ["0100026001"])
    set_value(app_session_factory, "dhcp.fortigate_format_verified", "true")
    reader(client, login_as, app_session_factory)
    card = lease_card(client)
    fortigate = card["fg_dhcp"]
    # the IPv6 row and the receive-time row of the fixture are not counted as usable either
    assert fortigate == {
        "rows": 0,
        "last_hour": None,
        "leases_current": 1,
        "refused_rows": 0,
        "excluded_rows": 0,
        "format_verified": False,
    }
    assert (card["mt_dhcp"]["rows"], card["state"]) == (2, "ok")
    set_value(app_session_factory, "dhcp.fortigate_format_verified", True)
    assert lease_card(client)["fg_dhcp"]["format_verified"] is True
    add_alert(app_session_factory, "R20-lease:none:mt_arp")
    assert lease_card(client)["state"] == "none"


# -- the srcmac count of the card -----------------------------------------------------------------


def test_srcmac_rows_only_with_interfaces(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with app_session_factory() as session:
        session.add(Source(name=UNKNOWN, type="fortigate", address="192.0.2.7", unknown=True))
        evidence = Evidence(session, layout)
        for minutes, srcintf, srcmac in (
            (30, "vlan42", PC_MAC),  # counted
            (40, "vlan42", PC_MAC),  # counted
            (90, "vlan42", PC_MAC),  # older than the hour
            (20, "vlan99", PC_MAC),  # another interface
            (10, "vlan42", None),  # without srcmac
        ):
            evidence.traffic(
                now - timedelta(minutes=minutes),
                PC,
                50000 + minutes,
                srcintf=srcintf,
                srcmac=srcmac,
            )
        row = evidence.traffic(
            now - timedelta(minutes=5), PC, 50100, srcintf="vlan42", srcmac=PC_MAC
        )
        session.execute(update(FgTraffic).where(FgTraffic.id == row).values(source=UNKNOWN))
        session.commit()
    reader(client, login_as, app_session_factory)

    def never(*args: Any) -> Any:
        raise AssertionError("no fg_traffic count without configured interfaces")

    monkeypatch.setattr(sourcehealth, "srcmac_query", never)
    assert lease_card(client)["srcmac"] == {"interfaces": [], "rows_last_hour": None}
    monkeypatch.undo()
    monkeypatch.setattr("arcilogger_api.routers.sources.utc_now", lambda: now)
    set_value(app_session_factory, "lookup.srcmac_interfaces", ["vlan42"])
    assert lease_card(client)["srcmac"] == {"interfaces": ["vlan42"], "rows_last_hour": 2}
    # kept 60 s for the same interfaces; other interfaces are counted at once
    with app_session_factory() as session:
        Evidence(session, layout).traffic(
            now - timedelta(minutes=1), PC, 50200, srcintf="vlan42", srcmac=PC_MAC
        )
        session.commit()
    assert state_of_app(app).srcmac.ttl == 60.0
    assert lease_card(client)["srcmac"]["rows_last_hour"] == 2
    set_value(app_session_factory, "lookup.srcmac_interfaces", ["vlan42", "vlan99"])
    assert lease_card(client)["srcmac"] == {
        "interfaces": ["vlan42", "vlan99"],
        "rows_last_hour": 4,
    }


def test_srcmac_count_times_out_to_null(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    now: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Past its 2-s deadline the count is null and the rest of the overview is answered; the
    request's transaction stays usable (a savepoint), and a null is not kept."""
    set_value(app_session_factory, "lookup.srcmac_interfaces", ["vlan42"])
    monkeypatch.setattr(
        sourcehealth, "srcmac_query", lambda *args: text("SELECT count(*) FROM pg_sleep(30)")
    )
    reader(client, login_as, app_session_factory)
    started = time.monotonic()
    card = lease_card(client)
    assert time.monotonic() - started < 15  # never the 30-s sleep
    assert card["srcmac"] == {"interfaces": ["vlan42"], "rows_last_hour": None}
    assert card["state"] == "ok"
    assert state_of_app(app).srcmac.get() is None
    # the request's own statement timeout is kept, and its transaction stays usable
    with app_session_factory() as session:
        session.execute(text("SET LOCAL statement_timeout TO '7s'"))
        assert sourcehealth.srcmac_rows_last_hour(session, ["vlan42"], [], now) is None
        assert session.execute(text("SHOW statement_timeout")).scalar_one() == "7s"
        session.rollback()
    monkeypatch.undo()
    monkeypatch.setattr("arcilogger_api.routers.sources.utc_now", lambda: now)
    assert lease_card(client)["srcmac"]["rows_last_hour"] == 0
    with app_session_factory() as session:
        session.execute(text("SET LOCAL statement_timeout TO '7s'"))
        assert sourcehealth.srcmac_rows_last_hour(session, ["vlan42"], [], now) == 0
        assert session.execute(text("SHOW statement_timeout")).scalar_one() == "7s"


# -- performance ----------------------------------------------------------------------------------

LOAD = text(
    "INSERT INTO messages (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "type, text) SELECT t, t, 'kaynak-' || (g % 10), '172.31.255.1', :day, g, 0, 'traffic', 'x' "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT CAST(:start AS timestamptz) + g * CAST(:step AS interval) AS t) AS x"
)


@pytest.mark.perf
def test_the_sparkline_query_stays_within_its_budget(db_engine: Engine) -> None:
    """300,000 messages of ten sources in the hour before now (5,000 a minute), after 600,000
    in the 23 hours before (the same day's partition, as on a busy day)."""
    now = datetime(2026, 10, 14, 15, 30, 20, tzinfo=UTC)
    end = now.replace(second=0)
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=site_day(now))
        for start, rows, step in (
            (end - timedelta(hours=24), 600_000, "138 milliseconds"),
            (end - timedelta(hours=1), 300_000, "11.9 milliseconds"),
        ):
            connection.execute(
                LOAD, {"day": site_day(now), "rows": rows, "start": start, "step": step}
            )
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE messages"))
    names = [f"kaynak-{n}" for n in range(10)]
    timings = []
    for _ in range(3):
        with session_factory(db_engine)() as session:
            began = time.perf_counter()
            lines = sourcehealth.sparklines(session, names, now)
            timings.append(time.perf_counter() - began)
    with db_engine.connect() as connection:
        plan = "\n".join(
            connection.execute(
                text(
                    "EXPLAIN SELECT source, date_trunc('minute', time), count(*) FROM messages "
                    "WHERE source = ANY(:names) AND time >= :s AND time < :e GROUP BY 1, 2"
                ),
                {"names": names, "s": end - timedelta(hours=1), "e": end},
            ).scalars()
        )
    print(f"\nsources sparkline, 300,000 of 900,000 rows: {min(timings) * 1000:.1f} ms\n{plan}")
    assert sum(sum(values) for values in lines.values()) >= 299_000
    assert re.search(r"Index (Only )?Scan using \S*source_time", plan), plan
    assert min(timings) < SPARKLINE_BUDGET_SECONDS


UNKNOWN_LOAD = text(
    "INSERT INTO messages (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "type, host, text) SELECT t, t, 'unknown-198.51.100.23', '198.51.100.23', :day, g, 0, "
    "'traffic', 'FIREWALL-02', 'x' FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT CAST(:start AS timestamptz) + g * CAST(:step AS interval) AS t) AS x"
)


@pytest.mark.perf
def test_the_suggestion_reads_only_the_newest_messages(db_engine: Engine, tmp_path: Path) -> None:
    """A firewall whose address is not configured yet: 300,000 messages of one unknown sender in
    the hour before now, after 600,000 in the 23 hours before. The suggestion reads only its
    newest messages, so every overview costs one short backward index scan per sender."""
    now = datetime(2026, 10, 14, 15, 30, 20, tzinfo=UTC)
    end = now.replace(second=0)
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=site_day(now))
        for start, rows, step in (
            (end - timedelta(hours=24), 600_000, "138 milliseconds"),
            (end - timedelta(hours=1), 300_000, "11.9 milliseconds"),
        ):
            connection.execute(
                UNKNOWN_LOAD, {"day": site_day(now), "rows": rows, "start": start, "step": step}
            )
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE messages"))
    config = Config.model_validate(
        {"site_name": EXAMPLE_SITE_NAME, "data_dir": str(tmp_path), "secrets_dir": str(tmp_path)}
    )
    timings = []
    for _ in range(3):
        with session_factory(db_engine)() as session:
            began = time.perf_counter()
            found = sourcehealth.suggestions(session, ["unknown-198.51.100.23"], config, now)
            timings.append(time.perf_counter() - began)
    print(f"\nsources suggestion, 900,000 rows of one sender: {min(timings) * 1000:.1f} ms")
    statement = sourcehealth.newest_messages("unknown-198.51.100.23", now)
    with db_engine.connect() as connection:
        compiled = statement.compile(connection)
        plan = "\n".join(
            row[0] for row in connection.exec_driver_sql(f"EXPLAIN {compiled}", compiled.params)
        )
    print(plan)
    assert found == {"unknown-198.51.100.23": {"name": "firewall-02", "type": "fortigate"}}
    # a short backward scan, whichever index the planner takes (here every row is the sender's);
    # a TimescaleDB chunk's index is named after the hypertable's (``2_pk_messages``)
    assert "Limit" in plan
    assert "Seq Scan" not in plan
    assert re.search(
        r"Index (Only )?Scan Backward using \S*(source_time|pkey|pk_messages)", plan
    ), plan
    assert min(timings) < SUGGESTION_BUDGET_SECONDS
