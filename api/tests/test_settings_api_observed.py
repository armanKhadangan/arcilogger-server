"""What the FortiGate's records show, for the exit editor (contract 2.12, 3.5):
``GET /settings/fortigate/observed``.

Administrators only and never audited; counts and the firewall's own addresses, never a client's
address or MAC; the NAT groups of trusted rows in the window with the exit that claims each by
the **current** exits (the one on the row's ``dstintf`` first), the unmapped addresses and the
unknown ``trandisp`` values; ``exits_invalid`` instead of a 500 for an unreadable exit value; the
srcmac part of the last hour as the srcmac guard counts it; ``hours`` 1-72; the counts kept five
minutes per ``hours`` while the claims follow a saved exit at once; 504 past the deadline.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.lookup_testing import (
    PC,
    PC_MAC,
    PHONE,
    PHONE_MAC,
    WAN,
    WAN2,
    accept_privacy,
    enter_exits,
    unknown_sender_traffic,
)
from arcilogger_api.routers import settings as settings_router
from arcilogger_api.routers.settings import FortigateObserved, observed_state
from arcilogger_api.testing import LoggedIn
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, site_day, to_site
from arcilogger_db.models import AuditLog, Source
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.settings import set_setting
from arcilogger_worker.testing import Evidence

Login = Callable[..., LoggedIn]
URL = "/api/v1/settings/fortigate/observed"
#: A public address of a direct exit (RFC 5737).
PUBLIC = "198.51.100.7"
#: An IPv6 NAT address (RFC 3849): exits refuse IPv6, so the observed part leaves it out.
NAT6 = "2001:db8:0:12::9"
#: Two exits share WAN2 on separate interfaces: the row's dstintf picks one.
SHARED = {
    "a-hat": {"mode": "isp_nat", "transips": [WAN2], "interfaces": ["wan3"]},
    "hat-1": {"mode": "isp_nat", "transips": [WAN], "interfaces": ["wan1"]},
    "hat-2": {"mode": "isp_nat", "transips": [WAN2], "interfaces": ["wan2"]},
}


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Any:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture
def now(monkeypatch: pytest.MonkeyPatch, app_session_factory: sessionmaker[Session]) -> datetime:
    """The latest site noon (never more than a day old, so no chunk of the TimescaleDB job is
    compressed yet), the moment the router reads as now."""
    real = datetime.now(UTC)
    noon = day_window(site_day(real))[0] + timedelta(hours=12)
    moment = (noon if noon <= real else noon - timedelta(days=1)).astimezone(UTC)
    monkeypatch.setattr("arcilogger_api.routers.settings.utc_now", lambda: moment)
    with app_session_factory() as session:
        ensure_partitions(session.connection(), today=site_day(moment))
        session.commit()
    return moment


def admin(client: TestClient, login_as: Login, factory: sessionmaker[Session]) -> LoggedIn:
    who = login_as(client, "admin")
    accept_privacy(factory, who.user_id)
    return who


def setting(factory: sessionmaker[Session], key: str, value: Any) -> None:
    with factory() as session:
        set_setting(session, key, value, updated_by="admin")
        session.commit()


def audit_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(AuditLog)) or 0)


def observed(client: TestClient, **params: Any) -> dict[str, Any]:
    response = client.get(URL, params=params)
    assert response.status_code == 200, response.text
    data: dict[str, Any] = response.json()
    FortigateObserved.model_validate(data)
    return data


def sessions(
    factory: sessionmaker[Session], layout: Layout, rows: list[tuple[datetime, dict[str, Any]]]
) -> None:
    """Session records of the PC (``fields``: ``transip``, ``dstintf``, ``trandisp``, ...)."""
    with factory() as session:
        evidence = Evidence(session, layout)
        for port, (moment, fields) in enumerate(rows, start=40001):
            evidence.traffic(moment, fields.pop("srcip", PC), port, **fields)
        session.commit()


@pytest.fixture
def nat_site(app_session_factory: sessionmaker[Session], layout: Layout, now: datetime) -> datetime:
    """NAT groups of the last 24 hours, and rows that must not count."""
    rows: list[tuple[datetime, dict[str, Any]]] = [
        (now - timedelta(hours=20), {"transip": WAN, "dstintf": "wan1"}),
        (now - timedelta(hours=2), {"transip": WAN, "dstintf": "wan1"}),
        (now - timedelta(minutes=10), {"transip": WAN, "dstintf": "wan1", "srcip": PHONE}),
        (now - timedelta(hours=3), {"transip": WAN2, "dstintf": "wan2"}),
        (now - timedelta(hours=1), {"transip": WAN2, "dstintf": "wan2"}),
        (now - timedelta(hours=5), {"transip": WAN2, "dstintf": "wan9"}),
        (now - timedelta(hours=4), {"transip": WAN2, "dstintf": "wan2", "trandisp": "snat-pool"}),
        (now - timedelta(hours=6), {"transip": PUBLIC, "dstintf": "wan1"}),
        # not counted: older than the window, no NAT address, later than now
        (now - timedelta(hours=30), {"transip": "100.64.14.9", "dstintf": "wan1"}),
        (now - timedelta(hours=1), {"transip": None}),
        (now + timedelta(minutes=5), {"transip": "100.64.16.9", "dstintf": "wan1"}),
    ]
    sessions(app_session_factory, layout, rows)
    with app_session_factory() as session:  # an unconfigured sender's session is not counted
        unknown_sender_traffic(
            Evidence(session, layout), now - timedelta(hours=1), PC, 41999, transip="100.64.15.9"
        )
        session.commit()
    return now


# -- permissions ----------------------------------------------------------------------------------


def test_the_observed_endpoint_is_for_administrators(
    app: FastAPI, login_as: Login, app_session_factory: sessionmaker[Session], now: datetime
) -> None:
    with TestClient(app, base_url="http://testserver") as anonymous:
        response = anonymous.get(URL)
        assert (response.status_code, response.json()["error"]["code"]) == (
            401,
            "not_authenticated",
        )
    for role in ("auditor", "hr", "viewer"):
        with TestClient(app, base_url="http://testserver") as client:
            login_as(client, role)
            response = client.get(URL)
            assert (response.status_code, response.json()["error"]["code"]) == (
                403,
                "forbidden",
            ), role
    with TestClient(app, base_url="http://testserver") as client:
        admin(client, login_as, app_session_factory)
        before = audit_count(app_session_factory)
        data = observed(client)
        assert audit_count(app_session_factory) == before  # counts only: not audited
    assert data["nat"] == {
        "hours": 24,
        "truncated": False,
        "rows": [],
        "unmapped_transips": [],
        "unknown_trandisp": [],
        "exits_invalid": False,
    }
    assert data["srcmac"] == {"hours": 1, "truncated": False, "interfaces": []}


# -- the NAT part ---------------------------------------------------------------------------------


def test_the_observed_endpoint_counts_only(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    nat_site: datetime,
) -> None:
    now = nat_site
    enter_exits(app_session_factory, SHARED, at=now - timedelta(days=30), by="yonetici")
    admin(client, login_as, app_session_factory)
    before = audit_count(app_session_factory)
    response = client.get(URL, params={"hours": 24})
    assert response.status_code == 200, response.text
    data = response.json()
    rows = [
        (r["transip"], r["dstintf"], r["trandisp"], r["sessions"], r["egress"], r["trandisp_known"])
        for r in data["nat"]["rows"]
    ]
    assert rows == [  # busiest first, then by address, interface and trandisp
        (WAN, "wan1", "snat", 3, "hat-1", True),
        (WAN2, "wan2", "snat", 2, "hat-2", True),
        (WAN2, "wan2", "snat-pool", 1, "hat-2", False),
        # claimed by two exits on separate interfaces: neither holds wan9, the first by name
        (WAN2, "wan9", "snat", 1, "a-hat", True),
        (PUBLIC, "wan1", "snat", 1, None, True),
    ]
    first = data["nat"]["rows"][0]
    assert (first["first_seen"], first["last_seen"]) == (
        to_site(now - timedelta(hours=20)).isoformat(),
        to_site(now - timedelta(minutes=10)).isoformat(),
    )
    assert data["nat"]["unmapped_transips"] == [PUBLIC]
    assert data["nat"]["unknown_trandisp"] == ["snat-pool"]
    assert (data["nat"]["hours"], data["nat"]["truncated"], data["nat"]["exits_invalid"]) == (
        24,
        False,
        False,
    )
    assert data["generated_at"] == to_site(now).isoformat()
    # counts and the firewall's own addresses only
    for personal in (PC, PHONE, PC_MAC, PHONE_MAC, "172.20."):
        assert personal not in response.text
    assert audit_count(app_session_factory) == before

    # nat.snat_trandisp names the pool: it is known now (read from the current setting)
    setting(app_session_factory, "nat.snat_trandisp", ["snat", "snat-pool"])
    again = observed(client)
    assert again["nat"]["unknown_trandisp"] == []
    assert all(row["trandisp_known"] for row in again["nat"]["rows"])


def test_a_shorter_window_counts_fewer_rows(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    nat_site: datetime,
) -> None:
    admin(client, login_as, app_session_factory)
    data = observed(client, hours=1)
    assert [(r["transip"], r["dstintf"], r["sessions"]) for r in data["nat"]["rows"]] == [
        (WAN, "wan1", 1),
        (WAN2, "wan2", 1),
    ]
    assert data["nat"]["hours"] == 1
    # no exits configured: no row is claimed
    assert data["nat"]["unmapped_transips"] == [r["transip"] for r in data["nat"]["rows"]]
    wide = observed(client, hours=72)
    assert ("100.64.14.9", "wan1", 1) in [
        (r["transip"], r["dstintf"], r["sessions"]) for r in wide["nat"]["rows"]
    ]


@pytest.mark.parametrize(("hours", "status"), [(0, 422), (73, 422), (1, 200), (72, 200)])
def test_hours_bounds(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    now: datetime,
    hours: int,
    status: int,
) -> None:
    admin(client, login_as, app_session_factory)
    response = client.get(URL, params={"hours": hours})
    assert response.status_code == status, response.text
    if status == 422:
        assert response.json()["error"]["code"] == "validation_error"
    else:
        assert response.json()["nat"]["hours"] == hours


@pytest.mark.parametrize(
    "stored",
    [["hat-1"], {"hat-1": {"mode": "direct", "public": ["x"]}}],
    ids=["not-an-object", "bad-address"],
)
def test_an_unreadable_exit_value_gives_exits_invalid(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    nat_site: datetime,
    stored: Any,
) -> None:
    """A stored nat.egresses the rules refuse (written around the validator): a 200 that says
    so, every row unclaimed, never a 500 (contract 2.8.1, 2.12)."""
    setting(app_session_factory, "nat.egresses", stored)
    admin(client, login_as, app_session_factory)
    data = observed(client)
    assert data["nat"]["exits_invalid"] is True
    assert data["nat"]["rows"]
    assert all(row["egress"] is None for row in data["nat"]["rows"])
    assert data["nat"]["unmapped_transips"] == [WAN, WAN2, PUBLIC]


def test_ipv6_nat_addresses_are_not_listed(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    now: datetime,
) -> None:
    """Only IPv4 NAT addresses are listed (``family(transip) = 4``): exits refuse IPv6, so an
    IPv6 row would stay unmapped for ever; the ``R20-nat:unmapped-transip`` check leaves it out
    the same way, and its ``trandisp`` is not reported either (review Q9)."""
    rows: list[tuple[datetime, dict[str, Any]]] = [
        (now - timedelta(hours=1), {"transip": WAN, "dstintf": "wan1"}),
        (now - timedelta(minutes=30), {"transip": NAT6, "dstintf": "wan1"}),
        (now - timedelta(minutes=20), {"transip": NAT6, "dstintf": "wan1", "trandisp": "snat6"}),
    ]
    sessions(app_session_factory, layout, rows)
    admin(client, login_as, app_session_factory)
    response = client.get(URL)
    assert response.status_code == 200, response.text
    nat = response.json()["nat"]
    assert [(r["transip"], r["dstintf"], r["sessions"]) for r in nat["rows"]] == [
        (WAN, "wan1", 1)
    ]
    assert (nat["unmapped_transips"], nat["unknown_trandisp"]) == ([WAN], [])
    assert "2001:db8" not in response.text


def test_a_truncated_list_keeps_the_busiest_groups(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    now: datetime,
) -> None:
    rows = [
        (now - timedelta(minutes=30), {"transip": f"100.64.20.{n}", "dstintf": "wan1"})
        for n in range(1, settings_router.OBSERVED_ROWS + 2)
    ]
    rows += [(now - timedelta(minutes=20), {"transip": "100.64.20.200", "dstintf": "wan1"})] * 2
    sessions(app_session_factory, layout, [(moment, dict(fields)) for moment, fields in rows])
    admin(client, login_as, app_session_factory)
    data = observed(client)
    assert data["nat"]["truncated"] is True
    assert len(data["nat"]["rows"]) == settings_router.OBSERVED_ROWS
    assert (data["nat"]["rows"][0]["transip"], data["nat"]["rows"][0]["sessions"]) == (
        "100.64.20.200",
        2,
    )


# -- the srcmac part ------------------------------------------------------------------------------


def test_the_observed_srcmac_part(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    now: datetime,
) -> None:
    other_mac = "00:00:5e:00:53:58"
    vlan42 = {"srcintf": "vlan42", "transip": WAN, "dstintf": "wan1"}
    rows: list[tuple[datetime, dict[str, Any]]] = [
        # vlan42: PC_MAC on two IPv4 addresses, another MAC on one
        (now - timedelta(minutes=50), {**vlan42, "srcmac": PC_MAC, "srcip": PC}),
        (now - timedelta(minutes=40), {**vlan42, "srcmac": PC_MAC, "srcip": "172.20.42.58"}),
        (now - timedelta(minutes=30), {**vlan42, "srcmac": other_mac, "srcip": "172.20.42.59"}),
        # an IPv6 source of the same MAC does not raise its count, nor the rows
        (now - timedelta(minutes=20), {**vlan42, "srcmac": PC_MAC, "srcip": "fd00:0:0:42::57"}),
        # older than the hour
        (now - timedelta(minutes=90), {**vlan42, "srcmac": PC_MAC, "srcip": "172.20.42.60"}),
        # vlan99: one MAC, not listed in lookup.srcmac_interfaces
        (
            now - timedelta(minutes=10),
            {"srcintf": "vlan99", "srcmac": PHONE_MAC, "srcip": PHONE, "transip": WAN},
        ),
        # without srcmac: not a srcmac row
        (now - timedelta(minutes=10), {"srcintf": "vlan42", "srcip": PC, "transip": WAN}),
    ]
    sessions(app_session_factory, layout, rows)
    with app_session_factory() as session:  # an unconfigured sender's rows never count
        session.add(
            Source(name="unknown-192.0.2.7", type="fortigate", address="192.0.2.7", unknown=True)
        )
        evidence = Evidence(session, layout)
        row = evidence.traffic(
            now - timedelta(minutes=5), "172.20.42.61", 41998, srcintf="vlan42", srcmac=PC_MAC
        )
        session.execute(
            text("UPDATE fg_traffic SET source = 'unknown-192.0.2.7' WHERE id = :id"), {"id": row}
        )
        session.commit()
    setting(app_session_factory, "lookup.srcmac_interfaces", ["vlan42"])
    admin(client, login_as, app_session_factory)
    response = client.get(URL)
    assert response.status_code == 200, response.text
    srcmac = response.json()["srcmac"]
    assert (srcmac["hours"], srcmac["truncated"]) == (1, False)
    assert srcmac["interfaces"] == [
        {"srcintf": "vlan42", "rows": 3, "macs": 2, "max_ips_per_mac": 2, "configured": True},
        {"srcintf": "vlan99", "rows": 1, "macs": 1, "max_ips_per_mac": 1, "configured": False},
    ]
    for personal in (PC_MAC, PHONE_MAC, other_mac, "00:00:5E", "172.20.42."):
        assert personal not in response.text


# -- cache and deadline ---------------------------------------------------------------------------


def test_the_observed_answer_is_cached(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    nat_site: datetime,
) -> None:
    """The counts are kept 5 minutes per ``hours``; the claims follow the exits saved since."""
    now = nat_site
    admin(client, login_as, app_session_factory)
    first = observed(client)
    assert observed_state(_request(app)).cache(24).ttl == settings_router.OBSERVED_CACHE_SECONDS
    assert first["nat"]["unmapped_transips"] == [WAN, WAN2, PUBLIC]
    sessions(
        app_session_factory,
        layout,
        [(now - timedelta(minutes=1), {"transip": WAN, "dstintf": "wan1"})],
    )

    kept = observed(client)
    assert kept["nat"]["rows"] == first["nat"]["rows"]  # the new session is not counted yet
    enter_exits(
        app_session_factory, {"hat-1": SHARED["hat-1"]}, at=now - timedelta(minutes=1), by="y"
    )
    claimed = observed(client)
    assert [r["sessions"] for r in claimed["nat"]["rows"]] == [
        r["sessions"] for r in first["nat"]["rows"]
    ]
    assert claimed["nat"]["rows"][0]["egress"] == "hat-1"  # at once, from the saved exit
    assert claimed["nat"]["unmapped_transips"] == [WAN2, PUBLIC]

    other = observed(client, hours=23)  # another hours value is counted on its own
    assert other["nat"]["rows"][0]["sessions"] == 4
    observed_state(_request(app)).cache(24).invalidate()
    fresh = observed(client)
    assert fresh["nat"]["rows"][0]["sessions"] == 4


def _request(app: FastAPI) -> Any:
    class _Request:
        def __init__(self, app: FastAPI) -> None:
            self.app = app

    return _Request(app)


def test_the_observed_endpoint_stops_at_its_deadline(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    nat_site: datetime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def slow(session: Session, hours: int, now: datetime) -> dict[str, Any]:
        session.execute(text("SELECT pg_sleep(30)"))
        raise AssertionError("the statement deadline did not stop the count")

    monkeypatch.setattr(settings_router, "OBSERVED_DEADLINE_SECONDS", 1)
    monkeypatch.setattr(settings_router, "observed_counts", slow)
    admin(client, login_as, app_session_factory)
    started = time.monotonic()
    response = client.get(URL)
    assert time.monotonic() - started < 15  # never the 30-s sleep
    assert response.status_code == 504, response.text
    error = response.json()["error"]
    assert (error["code"], error["message_tr"], error["message_en"]) == (
        "fortigate_observed_timeout",
        "FortiGate kayıtları 1 saniyede özetlenemedi; biraz sonra yeniden deneyin.",
        "The FortiGate records could not be summarised within 1 s; try again a little later.",
    )
    assert settings_router.MESSAGES["fortigate_observed_timeout"] == (
        "FortiGate kayıtları {seconds} saniyede özetlenemedi; biraz sonra yeniden deneyin.",
        "The FortiGate records could not be summarised within {seconds} s; try again a little "
        "later.",
    )
    # a 504 is not kept: the next request counts, on the same pooled connection
    monkeypatch.undo()
    monkeypatch.setattr("arcilogger_api.routers.settings.utc_now", lambda: nat_site)
    assert observed(client)["nat"]["rows"]
