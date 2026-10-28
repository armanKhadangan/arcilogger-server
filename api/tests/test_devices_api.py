"""Devices API (spec 9.2, ADR-0008): list with filters/sort/pagination, detail with history and
leases, editing, (bulk) assignment with its history rules, CSV export, permissions per role,
the KVKK notice and the audit entries."""

from __future__ import annotations

import csv
import io
import itertools
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.directory_testing import add_device, add_person
from arcilogger_api.lookup_testing import PC_MAC, accept_privacy, build_site
from arcilogger_api.testing import LoggedIn
from arcilogger_core.layout import Layout
from arcilogger_db.models import AuditLog, Device, DeviceAssignment, Person

Login = Callable[..., LoggedIn]
API = "/api/v1"
NOW = datetime.now(UTC)
PC1, PC2, PHONE, AP, PRINTER = (
    "f0:1f:af:00:00:01",
    "f0:1f:af:00:00:02",
    "02:00:5e:00:53:46",
    "00:0c:29:00:00:aa",
    "a4:83:e7:00:00:60",
)


@pytest.fixture
def factory(app_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return app_session_factory


@pytest.fixture
def admin(client: TestClient, login_as: Login, factory: sessionmaker[Session]) -> LoggedIn:
    who = login_as(client, "admin", username="yonetici")
    accept_privacy(factory, who.user_id)
    return who


@pytest.fixture
def people(factory: sessionmaker[Session]) -> dict[str, int]:
    """Two persons in two departments, one inactive; five devices, none assigned."""
    with factory() as session:
        ayse = add_person(session, "Ayşe Işık", department="Finans", employee_no="1001")
        mehmet = add_person(session, "Mehmet Öztürk", department="Planlama", employee_no="1002")
        gone = add_person(session, "Eski Çalışan", employee_no="0999", active=False)
        add_device(
            session,
            PC1,
            last_ip="172.20.41.21",
            last_vlan=41,
            hostnames=["MUHASEBE-PC1"],
            name="Ayşe'nin masaüstü",
            first_seen=NOW - timedelta(days=30),
            last_seen=NOW - timedelta(hours=1),
            device_type="pc",
        )
        add_device(
            session,
            PC2,
            last_ip="172.20.43.30",
            last_vlan=43,
            hostnames=["IDARI-ŞEF"],
            first_seen=NOW - timedelta(days=20),
            last_seen=NOW - timedelta(hours=3),
        )
        add_device(
            session,
            PHONE,
            last_ip="172.20.60.183",
            last_vlan=60,
            hostnames=["Telefon-12"],
            randomized=True,
            first_seen=NOW - timedelta(days=2),
            last_seen=NOW - timedelta(minutes=5),
            device_type="phone",
        )
        add_device(
            session,
            AP,
            last_ip="172.20.10.211",
            infrastructure=True,
            first_seen=NOW - timedelta(days=60),
            last_seen=NOW - timedelta(hours=2),
            device_type="ap",
        )
        add_device(session, PRINTER, last_ip="172.20.10.8")  # never seen times
        session.commit()
        return {"ayse": ayse.id, "mehmet": mehmet.id, "gone": gone.id}


def _audit(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
        for row in rows:
            session.expunge(row)
    return rows


def _assign(client: TestClient, macs: list[str], person_id: int | None, **extra: Any) -> Any:
    return client.post(
        f"{API}/devices/assign", json={"macs": macs, "person_id": person_id, **extra}
    )


def _macs(page: dict[str, Any]) -> list[str]:
    return [item["mac"] for item in page["items"]]


# -- routes, roles, KVKK notice -------------------------------------------------------------------

#: Every route of the devices/persons area with what it needs: (permission, KVKK notice).
RULES: dict[tuple[str, str], tuple[str, bool]] = {
    ("GET", "/api/v1/devices"): ("devices.read", True),
    ("GET", "/api/v1/devices/export.csv"): ("devices.read", True),
    ("POST", "/api/v1/devices/assign"): ("devices.manage", True),
    ("GET", "/api/v1/devices/{mac}"): ("devices.read", True),
    ("PATCH", "/api/v1/devices/{mac}"): ("devices.manage", True),
    ("GET", "/api/v1/persons"): ("devices.read", True),
    ("POST", "/api/v1/persons"): ("devices.manage", True),
    ("GET", "/api/v1/persons/export.csv"): ("devices.read", True),
    ("GET", "/api/v1/persons/{person_id}"): ("devices.read", True),
    ("PATCH", "/api/v1/persons/{person_id}"): ("devices.manage", True),
    ("GET", "/api/v1/departments"): ("devices.read", False),
    ("POST", "/api/v1/departments"): ("devices.manage", False),
    ("PATCH", "/api/v1/departments/{department_id}"): ("devices.manage", False),
    ("GET", "/api/v1/vlans"): ("devices.read", False),
    ("POST", "/api/v1/vlans"): ("devices.manage", False),
    ("PATCH", "/api/v1/vlans"): ("devices.manage", False),
    ("DELETE", "/api/v1/vlans"): ("devices.manage", False),
    ("POST", "/api/v1/imports/{kind}"): ("devices.manage", True),
    ("GET", "/api/v1/imports/{kind}/template.csv"): ("devices.read", False),
}
PREFIXES = ("/api/v1/devices", "/api/v1/persons", "/api/v1/departments", "/api/v1/vlans")


def _url(path: str) -> str:
    return (
        path.replace("{mac}", PC1)
        .replace("{person_id}", "1")
        .replace("{department_id}", "1")
        .replace("{kind}", "persons")
    )


def _call(client: TestClient, method: str, path: str) -> Any:
    url = _url(path)
    if method == "GET":
        return client.get(url)
    if path.endswith("/imports/{kind}"):
        return client.post(url, files={"file": ("p.csv", b"name\r\nX\r\n", "text/csv")})
    return client.request(method, url, json={})


def test_every_route_is_classified(app: FastAPI) -> None:
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    found = sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith((*PREFIXES, "/api/v1/imports"))
        for method in operations
    )
    assert found == sorted(RULES)


@pytest.mark.parametrize("role", ["auditor", "hr", "viewer"])
def test_other_roles_are_refused_everywhere(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str
) -> None:
    who = login_as(client, role)
    accept_privacy(factory, who.user_id)
    for method, path in RULES:
        response = _call(client, method, path)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"]["code"] == "forbidden", (method, path)


def test_no_session_is_401(client: TestClient) -> None:
    for method, path in RULES:
        if method == "GET":
            assert _call(client, method, path).status_code == 401, path


def test_person_data_needs_the_privacy_notice(
    client: TestClient, login_as: Login, people: dict[str, int]
) -> None:
    login_as(client, "admin")  # notice not accepted
    for (method, path), (_, notice) in RULES.items():
        response = _call(client, method, path)
        if notice:
            assert response.status_code == 403, (method, path)
            assert response.json()["error"]["code"] == "privacy_notice_required", (method, path)
        else:
            assert response.status_code != 403, (method, path)


# -- list -----------------------------------------------------------------------------------------


def test_list_sorts_pages_and_counts(
    client: TestClient, admin: LoggedIn, people: dict[str, int]
) -> None:
    page = client.get(f"{API}/devices").json()
    assert page["total"] == 5
    assert (page["limit"], page["offset"]) == (50, 0)
    # last_seen desc, never-seen last
    assert _macs(page) == [PHONE, PC1, AP, PC2, PRINTER]
    asc = client.get(f"{API}/devices", params={"order": "asc"}).json()
    assert _macs(asc) == [PC2, AP, PC1, PHONE, PRINTER]  # empty values last in both directions
    by_mac = client.get(f"{API}/devices", params={"sort": "mac", "order": "asc"}).json()
    assert _macs(by_mac) == sorted(_macs(by_mac))
    by_ip = client.get(f"{API}/devices", params={"sort": "last_ip", "order": "asc"}).json()
    assert [i["last_ip"] for i in by_ip["items"]] == [
        "172.20.10.8",
        "172.20.10.211",
        "172.20.41.21",
        "172.20.43.30",
        "172.20.60.183",
    ]
    second = client.get(f"{API}/devices", params={"limit": 2, "offset": 2}).json()
    assert _macs(second) == [AP, PC2]
    assert second["total"] == 5
    assert (
        client.get(f"{API}/devices", params={"sort": "first_seen"}).json()["items"][0]["mac"]
        == PHONE
    )
    for bad in ({"limit": 0}, {"limit": 501}, {"sort": "vendor"}, {"order": "up"}, {"type": "tv"}):
        response = client.get(f"{API}/devices", params=bad)
        assert response.status_code == 422, bad
        assert response.json()["error"]["code"] == "validation_error"


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ({"q": "f01faf000001"}, [PC1]),  # MAC without separators
        ({"q": "F0-1F-AF"}, [PC1, PC2]),  # MAC with dashes, upper case
        ({"q": "idari-şef"}, [PC2]),  # host name, case folded
        ({"q": "İDARİ"}, [PC2]),  # dotted capital İ folds to i
        ({"q": "ayşe'nin"}, [PC1]),  # device name
        ({"q": "172.20.60"}, [PHONE]),  # IP
        ({"q": "100%"}, []),  # LIKE wildcards are escaped
        ({"type": "phone"}, [PHONE]),
        ({"vlan": 43}, [PC2]),
        ({"randomized": "true"}, [PHONE]),
        ({"infrastructure": "true"}, [AP]),
        ({"unknown": "true"}, [PHONE, PC1, PC2, PRINTER]),
        ({"unknown": "false"}, [AP]),
    ],
)
def test_list_filters(
    client: TestClient,
    admin: LoggedIn,
    people: dict[str, int],
    query: dict[str, Any],
    expected: list[str],
) -> None:
    page = client.get(f"{API}/devices", params=query).json()
    assert _macs(page) == expected
    assert page["total"] == len(expected)


def test_owner_filter_and_effective_department(
    client: TestClient, admin: LoggedIn, people: dict[str, int], factory: sessionmaker[Session]
) -> None:
    assert _assign(client, [PC1, PHONE], people["ayse"]).status_code == 200
    page = client.get(f"{API}/devices", params={"person_id": people["ayse"]}).json()
    assert _macs(page) == [PHONE, PC1]
    by_mac = {item["mac"]: item for item in client.get(f"{API}/devices").json()["items"]}
    # Owner's department wins over the VLAN's (phone on staff Wi-Fi 60, no linked dept.).
    assert by_mac[PHONE]["person"] == {"id": people["ayse"], "name": "Ayşe Işık"}
    assert by_mac[PHONE]["department"]["name"] == "Finans"
    assert by_mac[PHONE]["department_source"] == "person"
    assert by_mac[PHONE]["vlan_name"] == "Wi-Fi Personel"
    assert by_mac[PHONE]["assigned_since"] is not None
    # No owner: the VLAN's department (VLAN 43 İdari İşler), found by last_vlan.
    assert (by_mac[PC2]["department"]["name"], by_mac[PC2]["department_source"]) == (
        "İdari İşler",
        "vlan",
    )
    assert by_mac[PC2]["person"] is None
    assert by_mac[PC2]["assigned_since"] is None
    # VLAN 10 (network devices) has no department; the printer's VLAN is found by its IP.
    assert (by_mac[AP]["department"], by_mac[AP]["department_source"]) == (None, None)
    assert by_mac[PRINTER]["vlan_name"] == "Ağ Cihazları"
    unknown = client.get(f"{API}/devices", params={"unknown": "true"}).json()
    assert _macs(unknown) == [PC2, PRINTER]


# -- detail and edit ------------------------------------------------------------------------------


def test_detail_has_history_and_the_newest_leases(
    client: TestClient,
    admin: LoggedIn,
    factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    build_site(factory, layout)
    response = client.get(f"{API}/devices/00-00-5E-00-53-57")  # any MAC notation
    assert response.status_code == 200, response.text
    detail = response.json()
    assert detail["mac"] == PC_MAC
    assert detail["assignments"] == []  # the site links the PC without history (legacy)
    assert detail["person"]["name"] == "Selin Kaya"
    assert detail["assigned_since"] is None
    leases = detail["recent_leases"]
    assert leases
    assert set(leases[0]) == {"ip", "vlan", "start", "end", "end_reason", "confidence"}
    starts = [lease["start"] for lease in leases]
    assert starts == sorted(starts, reverse=True)
    assert client.get(f"{API}/devices/00:00:00:00:00:01").status_code == 404
    bad = client.get(f"{API}/devices/not-a-mac")
    assert bad.status_code == 422
    assert bad.json()["error"]["details"][0]["loc"] == ["path", "mac"]


def test_detail_lists_at_most_twenty_leases(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session], people: dict[str, int]
) -> None:
    from arcilogger_db.models import Lease

    with factory() as session:
        for hour in range(25):
            start = NOW - timedelta(hours=50 - hour)
            session.add(
                Lease(
                    ip="172.20.41.21",
                    mac=PC1,
                    start=start,
                    end=start + timedelta(minutes=30),
                    end_reason="dhcp",
                    confidence="high",
                    source_table="mt_dhcp",
                )
            )
        session.commit()
    leases = client.get(f"{API}/devices/{PC1}").json()["recent_leases"]
    assert len(leases) == 20
    assert leases[0]["start"] > leases[-1]["start"]


def test_edit_device(
    client: TestClient, admin: LoggedIn, people: dict[str, int], factory: sessionmaker[Session]
) -> None:
    response = client.patch(
        f"{API}/devices/{PC2}",
        json={
            "name": "  Ofis şefi PC ",
            "type": "pc",
            "notes": "Masa 3\nkat 2",
            "tags": ["ödeme", "Ödeme", "kat-2"],
        },
    )
    assert response.status_code == 200, response.text
    out = response.json()
    assert (out["name"], out["type"], out["notes"]) == ("Ofis şefi PC", "pc", "Masa 3\nkat 2")
    assert out["tags"] == ["ödeme", "kat-2"]  # duplicates (case-insensitive) dropped
    (entry,) = _audit(factory, "device.update")
    assert entry.target == f"device:{PC2}"
    assert entry.details == {
        "changed": ["name", "type", "notes", "tags"],
        "type": {"old": None, "new": "pc"},
    }
    # Unchanged values: no new entry; null clears; infrastructure/tags may not be null.
    client.patch(f"{API}/devices/{PC2}", json={"type": "pc"})
    assert len(_audit(factory, "device.update")) == 1
    cleared = client.patch(f"{API}/devices/{PC2}", json={"name": None, "infrastructure": True})
    assert (cleared.json()["name"], cleared.json()["infrastructure"]) == (None, True)
    assert _audit(factory, "device.update")[-1].details["infrastructure"] == {
        "old": False,
        "new": True,
    }
    for body in (
        {"infrastructure": None},
        {"tags": None},
        {"type": "tv"},
        {"name": "x" * 201},
        {"notes": "a‮b"},
        {"tags": ["x"] * 21},
        {"person_id": 1},
    ):
        bad = client.patch(f"{API}/devices/{PC2}", json=body)
        assert bad.status_code == 422, body
    assert client.patch(f"{API}/devices/00:00:00:00:00:01", json={}).status_code == 404


# -- assignment -----------------------------------------------------------------------------------


def _history(factory: sessionmaker[Session], mac: str) -> list[DeviceAssignment]:
    with factory() as session:
        rows = list(
            session.scalars(
                select(DeviceAssignment)
                .where(DeviceAssignment.mac == mac)
                .order_by(DeviceAssignment.valid_from, DeviceAssignment.id)
            )
        )
        for row in rows:
            session.expunge(row)
    return rows


def _owner(factory: sessionmaker[Session], mac: str) -> int | None:
    with factory() as session:
        return session.get_one(Device, mac).person_id


def test_assign_reassign_and_unassign(
    client: TestClient, admin: LoggedIn, people: dict[str, int], factory: sessionmaker[Session]
) -> None:
    start = NOW - timedelta(days=10)
    response = _assign(client, [PC1], people["ayse"], valid_from=start.isoformat(), note="Zimmet")
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["updated"] == 1
    (device,) = result["devices"]
    assert device["person"]["id"] == people["ayse"]
    assert datetime.fromisoformat(device["assigned_since"]) == start
    # Same person again: nothing changes.
    again = _assign(client, [PC1], people["ayse"]).json()
    assert again["updated"] == 0
    moved = NOW - timedelta(days=2)
    assert (
        _assign(client, [PC1], people["mehmet"], valid_from=moved.isoformat()).json()["updated"]
        == 1
    )
    assert _owner(factory, PC1) == people["mehmet"]
    unassigned = _assign(client, [PC1], None).json()
    assert unassigned["updated"] == 1
    assert unassigned["devices"][0]["person"] is None
    assert _owner(factory, PC1) is None
    rows = _history(factory, PC1)
    assert [(r.person_id, r.valid_from, r.valid_to is None) for r in rows] == [
        (people["ayse"], start, False),
        (people["mehmet"], moved, False),
    ]
    assert rows[0].valid_to == moved
    assert rows[0].note == "Zimmet"
    assert rows[0].assigned_by == "yonetici"
    assert not rows[0].backfilled
    # Unassigning an unassigned device changes nothing.
    assert _assign(client, [PC1], None).json()["updated"] == 0
    entries = _audit(factory, "device.assign")
    assert len(entries) == 3
    assert entries[1].details["changes"] == [
        {"mac": PC1, "from": people["ayse"], "to": people["mehmet"]}
    ]
    assert entries[1].target == f"device:{PC1}"
    assert "Ayşe" not in str(entries[1].details)
    # The detail shows the history newest first.
    history = client.get(f"{API}/devices/{PC1}").json()["assignments"]
    assert [h["person"]["name"] for h in history] == ["Mehmet Öztürk", "Ayşe Işık"]
    assert history[0]["valid_to"] is not None


def test_bulk_assignment_is_atomic(
    client: TestClient, admin: LoggedIn, people: dict[str, int], factory: sessionmaker[Session]
) -> None:
    early = NOW - timedelta(days=5)
    assert _assign(client, [PC2], people["mehmet"], valid_from=early.isoformat()).status_code == 200
    # PC2's assignment started 5 days ago: a bulk change from 7 days ago is refused for PC2 and
    # therefore for all three.
    response = _assign(
        client, [PC1, PHONE, PC2], people["ayse"], valid_from=(NOW - timedelta(days=7)).isoformat()
    )
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "assignment_overlap"
    assert error["details"] == [{"mac": PC2, "reason": "overlap", "earliest": early.isoformat()}]
    assert "geçmişi" in error["message_tr"]
    assert (_owner(factory, PC1), _owner(factory, PHONE), _owner(factory, PC2)) == (
        None,
        None,
        people["mehmet"],
    )
    ok = _assign(client, [PC1, PHONE, PC2, PC1], people["ayse"])  # a repeated MAC counts once
    assert ok.status_code == 200
    assert ok.json()["updated"] == 3
    assert [d["mac"] for d in ok.json()["devices"]] == [PC1, PHONE, PC2]
    (entry,) = [e for e in _audit(factory, "device.assign") if e.details["requested"] == 3]
    assert entry.target is None
    assert entry.details["updated"] == 3


def test_backdating_and_future_assignments_are_refused(
    client: TestClient,
    admin: LoggedIn,
    people: dict[str, int],
    factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The router decides "in the future" by its own clock, so the test hands it its moment, taken
    # when the test runs and one second later at each read: the module's NOW is taken at
    # collection and, on a run that reaches this test 58 minutes later, NOW + 1 h is no longer
    # more than FUTURE_SLACK ahead of the server's clock (200 instead of 409).
    now = datetime.now(UTC)
    reads = itertools.count()
    monkeypatch.setattr(
        "arcilogger_api.routers.devices.utc_now", lambda: now + timedelta(seconds=next(reads))
    )
    future = _assign(
        client, [PC1], people["ayse"], valid_from=(now + timedelta(hours=1)).isoformat()
    )
    assert future.status_code == 409
    assert future.json()["error"]["code"] == "assignment_in_future"
    # A browser clock a little ahead is accepted.
    assert (
        _assign(
            client, [PC1], people["ayse"], valid_from=(now + timedelta(seconds=30)).isoformat()
        ).status_code
        == 200
    )
    # Before the current assignment's start: 409.
    before = _assign(
        client, [PC1], people["mehmet"], valid_from=(now - timedelta(days=1)).isoformat()
    )
    assert before.status_code == 409
    assert before.json()["error"]["code"] == "assignment_overlap"
    # Unassigned again: a new assignment may not start inside the closed one either.
    assert (
        _assign(
            client, [PC1], None, valid_from=(now + timedelta(seconds=40)).isoformat()
        ).status_code
        == 200
    )
    inside = _assign(client, [PC1], people["mehmet"], valid_from=now.isoformat())
    assert inside.status_code == 409
    assert len(_history(factory, PC1)) == 1
    naive = _assign(client, [PC1], people["mehmet"], valid_from="2026-10-01T09:00:00")
    assert naive.status_code == 422


def test_a_start_a_little_ahead_is_stored_as_now_and_can_be_corrected_at_once(
    client: TestClient, admin: LoggedIn, people: dict[str, int], factory: sessionmaker[Session]
) -> None:
    """A browser clock up to two minutes ahead: the assignment starts at the server's now, so
    reassigning right away ("Şimdi") is not refused as before a still-future start."""
    before = datetime.now(UTC)
    ahead = _assign(
        client, [PC1], people["ayse"], valid_from=(before + timedelta(seconds=90)).isoformat()
    )
    assert ahead.status_code == 200, ahead.text
    after = datetime.now(UTC)
    since = datetime.fromisoformat(ahead.json()["devices"][0]["assigned_since"])
    assert before <= since <= after
    fixed = _assign(client, [PC1], people["mehmet"])
    assert fixed.status_code == 200, fixed.text
    first, second = _history(factory, PC1)
    assert (first.person_id, first.valid_from) == (people["ayse"], since)
    assert first.valid_to == second.valid_from
    assert second.valid_from <= datetime.now(UTC)
    (entry, _) = _audit(factory, "device.assign")
    assert datetime.fromisoformat(entry.details["valid_from"]) == since


def test_an_unassign_refuses_a_note(
    client: TestClient, admin: LoggedIn, people: dict[str, int], factory: sessionmaker[Session]
) -> None:
    """A note is kept on the assignment opened; an unassign opens none, so a note is refused
    (422) instead of being accepted and dropped."""
    assert _assign(client, [PC1], people["ayse"], note="Zimmet").status_code == 200
    refused = _assign(client, [PC1], None, note="İade formu 42")
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "note"]
    assert _owner(factory, PC1) == people["ayse"]
    assert len(_audit(factory, "device.assign")) == 1
    # Without a note (an empty field is no note) the unassign goes through.
    removed = _assign(client, [PC1], None, note="  ")
    assert removed.status_code == 200, removed.text
    assert removed.json()["updated"] == 1
    (row,) = _history(factory, PC1)
    assert (row.note, row.valid_to is not None) == ("Zimmet", True)


def test_a_link_without_history_can_be_removed(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session], layout: Layout
) -> None:
    build_site(factory, layout)  # links the PC directly, without an assignment row
    response = _assign(client, [PC_MAC], None)
    assert response.json()["updated"] == 1
    assert response.json()["devices"][0]["person"] is None
    assert _owner(factory, PC_MAC) is None
    assert _history(factory, PC_MAC) == []
    (entry,) = _audit(factory, "device.assign")
    assert entry.details["changes"][0]["to"] is None


def test_assignment_refusals(client: TestClient, admin: LoggedIn, people: dict[str, int]) -> None:
    missing = _assign(client, [PC1, "00:00:00:00:00:01"], people["ayse"])
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "device_not_found"
    assert missing.json()["error"]["details"] == {"macs": ["00:00:00:00:00:01"]}
    inactive = _assign(client, [PC1], people["gone"])
    assert inactive.status_code == 409
    assert inactive.json()["error"]["code"] == "person_inactive"
    unknown = _assign(client, [PC1], 999_999)
    assert unknown.status_code == 422
    assert unknown.json()["error"]["details"][0]["loc"] == ["body", "person_id"]
    for body in (
        {"macs": [], "person_id": 1},
        {"macs": ["zz"], "person_id": 1},
        {"macs": [PC1] * 501, "person_id": 1},
        {"macs": [PC1]},  # person_id is required (null unassigns)
        {"macs": [PC1], "person_id": 1, "extra": 1},
    ):
        response = client.post(f"{API}/devices/assign", json=body)
        assert response.status_code == 422, body
    assert client.get(f"{API}/devices/{PC1}").json()["assignments"] == []


# -- export ---------------------------------------------------------------------------------------


def _csv(response: Any) -> list[dict[str, str]]:
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/csv")
    assert "attachment" in response.headers["content-disposition"]
    data: bytes = response.content
    assert data.startswith("﻿".encode())
    text = data.decode("utf-8").removeprefix("﻿")
    assert "\r\n" in text
    return list(csv.DictReader(io.StringIO(text), delimiter=";"))


def test_device_export_neutralises_formulas_and_keeps_turkish_letters(
    client: TestClient, admin: LoggedIn, people: dict[str, int], factory: sessionmaker[Session]
) -> None:
    with factory() as session:
        add_device(
            session, "02:00:00:00:00:66", hostnames=["=cmd|'/c calc'!A1", "+SUM(1)"], name="@ışık"
        )
        session.commit()
    _assign(client, [PC1], people["ayse"])
    rows = _csv(client.get(f"{API}/devices/export.csv", params={"sort": "mac", "order": "asc"}))
    by_mac = {row["mac"]: row for row in rows}
    assert len(rows) == 6
    evil = by_mac["02:00:00:00:00:66"]
    assert evil["hostnames"] == "'=cmd|'/c calc'!A1, +SUM(1)"
    assert evil["name"] == "'@ışık"
    pc = by_mac[PC1]
    assert (pc["person"], pc["employee_no"], pc["department"]) == (
        "Ayşe Işık",
        "1001",
        "Finans",
    )
    assert pc["randomized"] == "hayır"
    assert pc["first_seen"].endswith("+03:00")
    filtered = _csv(client.get(f"{API}/devices/export.csv", params={"q": "telefon"}))
    assert [row["mac"] for row in filtered] == [PHONE]
    entries = _audit(factory, "device.export")
    assert [e.details["rows"] for e in entries] == [6, 1]
    assert entries[1].details["filters"] == {"q": True}  # never the search text


def test_cells_starting_with_a_formula_character_are_neutralised() -> None:
    from arcilogger_api.csvfiles import neutralise

    for value in ("=1+1", "+1", "-1", "@SUM", "\tx", "\rx"):
        assert neutralise(value) == "'" + value
    assert neutralise("Işık") == "Işık"
    assert neutralise("") == ""


def test_lookup_api_chain_exposes_the_assignment(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session], layout: Layout
) -> None:
    """The person step reads the history: the lookup's chain JSON carries assigned_from,
    assigned_to and link (and the report prints the interval)."""
    from arcilogger_api.lookup_testing import AT, PC, iso
    from arcilogger_api.pdftext import pdf_text

    site = build_site(factory, layout)
    with factory() as session:  # make the PC a device with history
        session.get_one(Device, PC_MAC).person_id = None
        session.commit()
    since = AT - timedelta(days=3)
    assert (
        _assign(client, [PC_MAC], site.person_id, valid_from=since.isoformat()).status_code == 200
    )
    body = {
        "request": {
            "authority": "Test Makamı",
            "file_no": "2026/1",
            "request_date": AT.date().isoformat(),
            "reason": "Deneme",
        },
        "query": {"at": iso(AT), "internal_ip": PC},
    }
    response = client.post(f"{API}/lookup/5651", json=body)
    assert response.status_code == 201, response.text
    outcome = response.json()["outcome"]
    person = outcome["attribution"]["person"]
    assert person["link"] == "history"
    assert datetime.fromisoformat(person["assigned_from"]) == since
    assert person["assigned_to"] is None
    keys = [e["key"] for e in outcome["explanations"]]
    assert "person_link_at_lookup_time" not in keys
    assert "person_department_current" in keys
    pdf = client.get(f"{API}/lookup/{response.json()['id']}/report.pdf")
    assert pdf.status_code == 200
    text = " ".join(pdf_text(pdf.content).split())
    assert "tarihinden itibaren" in text
    with factory() as session:
        assert session.scalars(select(Person).where(Person.id == site.person_id)).one()


def test_report_person_line_prints_the_assignment_interval() -> None:
    from arcilogger_api.reports.render import _assignment
    from arcilogger_worker.lookup import PersonInfo

    start = datetime(2026, 3, 12, 6, 0, tzinfo=UTC)

    def person(**fields: Any) -> PersonInfo:
        return PersonInfo(
            person_id=1, name="Ayşe", department=None, title=None, confidence="medium", **fields
        )

    assert _assignment(person(assigned_from=start, link="history")) == (
        " · atama: 12.03.2026 09:00:00 tarihinden itibaren"
    )
    closed = person(assigned_from=start, assigned_to=start + timedelta(days=30), link="history")
    assert _assignment(closed) == " · atama: 12.03.2026 09:00:00 - 11.04.2026 09:00:00"
    # Outcomes stored before the history (link "current") render exactly as before.
    assert _assignment(person()) == ""
