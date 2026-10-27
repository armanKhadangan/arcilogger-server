"""Kaynaklar (``/api/v1/sources``, contract 2.16, test plan 3.4): every state of a source
(active, silent, never, disabled, unknown, promoted, network member), the suggestion for an unknown
sender, today's lines, ingest lag and failure ratio, the sparkline, a detail's hours (summary table
or direct count, equal), days, gaps from a sealed vault and today's open one, parse failures with
their detail only for raw-line readers, and the audited tail: permissions, KVKK, rate limit, the
no-file case and a ``range_sha256`` an auditor recomputes from the file. The sparkline query and
the suggestion for a busy unknown sender are guarded at 300,000 rows an hour (``poe test-perf``).
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
from sqlalchemy import Engine, delete, select, text
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
from arcilogger_db.models import AuditLog, IngestOffset, Message, ParseFailure, Source
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.searchcounts import rebuild_stream
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
def now() -> datetime:
    return datetime.now(UTC)


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
    without = security.ALL_PERMISSIONS - {"search.read", "lookup.run"}
    monkeypatch.setitem(security.ROLE_PERMISSIONS, "admin", without)
    reader(client, login_as, app_session_factory)
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
    # a short backward scan, whichever index the planner takes (here every row is the sender's)
    assert "Limit" in plan
    assert "Seq Scan" not in plan
    assert re.search(r"Index (Only )?Scan Backward using \S*(source_time|pkey)", plan), plan
    assert min(timings) < SUGGESTION_BUDGET_SECONDS
