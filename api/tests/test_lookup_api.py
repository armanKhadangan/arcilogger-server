"""5651 lookup API end to end (spec 9.5, 12.3 page 6, 13, 14, 17): real spool lines and rows, the
lease timeline rebuilt by the worker job, then ``POST /lookup/5651`` — exact, double-NAT
candidates, internal, none; request details, time zones, roles, the KVKK notice, the audit
entries, visibility of other users' lookups, the list and the statement timeout."""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Iterator
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.lookup_testing import (
    AT,
    PC,
    PC_MAC,
    PERSON_NAME,
    PHONE,
    PHONE_MAC,
    WAN,
    Site,
    accept_privacy,
    build_site,
    iso,
)
from arcilogger_api.routers import lookup as lookup_router
from arcilogger_api.routers.lookup import LookupTimeoutError, parse_moment, statement_deadline
from arcilogger_api.testing import LoggedIn
from arcilogger_core.canonical import canonical_json
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_db.models import AuditLog, Department, Lease, Lookup
from arcilogger_db.settings import set_setting
from arcilogger_worker.lookup import EXPLANATIONS

Login = Callable[..., LoggedIn]
AUTHORITY = "İstanbul Cumhuriyet Başsavcılığı"
FILE_NO = "2026/1234 Soruşturma"
REASON = "Bilgi talebi yazısı (12.10.2026, ek: 1)\nİlgili IP: genel adres"


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture
def site(app_session_factory: sessionmaker[Session], layout: Layout) -> Site:
    return build_site(app_session_factory, layout)


def body(**query: Any) -> dict[str, Any]:
    return {
        "request": {
            "authority": AUTHORITY,
            "file_no": FILE_NO,
            "request_date": "2026-10-12",
            "reason": REASON,
        },
        "query": {"at": iso(AT), **query},
    }


def signed_in(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str = "admin"
) -> LoggedIn:
    who = login_as(client, role)
    accept_privacy(factory, who.user_id)
    return who


def setting(factory: sessionmaker[Session], key: str, value: Any) -> None:
    with factory() as session:
        set_setting(session, key, value, updated_by="admin")
        session.commit()


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        return list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )


def lease_id(factory: sessionmaker[Session], mac: str) -> int:
    with factory() as session:
        return session.scalars(select(Lease.id).where(Lease.mac == mac)).one()


def lookup_rows(factory: sessionmaker[Session]) -> list[Lookup]:
    with factory() as session:
        return list(session.scalars(select(Lookup).order_by(Lookup.id)))


def keys(outcome: dict[str, Any]) -> list[str]:
    return [item["key"] for item in outcome["explanations"]]


# -- modes ----------------------------------------------------------------------------------------


def test_exact_lookup_with_a_configured_public_ip(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    setting(app_session_factory, "nat.public_ips", [WAN])  # ISP router in bridge mode
    admin = signed_in(client, login_as, app_session_factory)
    started = time.monotonic()
    response = client.post(
        "/api/v1/lookup/5651", json=body(public_ip=WAN, public_port=51001, protocol="tcp")
    )
    assert response.status_code == 201, response.text
    assert time.monotonic() - started < 5  # spec 14: < 5 s for ±60 s
    data = response.json()
    outcome = data["outcome"]
    assert (data["mode"], data["result_count"], outcome["double_nat"]) == ("exact", 1, False)
    assert 0 <= data["elapsed_ms"] < 5000
    (match,) = outcome["matches"]
    assert (match["kind"], match["score"], match["time_distance_ms"]) == ("exact", 100, 0)
    traffic = match["traffic"]
    assert (traffic["srcip"], traffic["srcport"], traffic["transport"]) == (PC, 51001, 51001)
    chain = match["chain"]
    assert (chain["lease"]["mac"], chain["person"]["name"]) == (PC_MAC, PERSON_NAME)
    assert (chain["department"], chain["department_source"]) == ("Planlama", "person")
    assert data["request"] == {
        "authority": AUTHORITY,
        "file_no": FILE_NO,
        "request_date": "2026-10-12",
        "reason": REASON,
    }
    assert data["query"]["time_input"] == {
        "text": iso(AT),
        "offset_given": True,
        "time_zone": None,
    }
    assert data["report"] is None
    (row,) = lookup_rows(app_session_factory)
    assert (row.id, row.user_id, row.mode, row.result_count) == (
        data["id"],
        admin.user_id,
        "exact",
        1,
    )
    assert row.outcome == outcome
    assert row.finished_at is not None

    (entry,) = audits(app_session_factory, "lookup.run")
    assert (entry.actor_id, entry.target) == (admin.user_id, f"lookup:{row.id}")
    details = entry.details
    assert details["lookup_id"] == row.id
    assert details["request"] == {
        "authority": AUTHORITY,
        "file_no": FILE_NO,
        "request_date": "2026-10-12",
        "reason_sha256": hashlib.sha256(REASON.encode()).hexdigest(),
    }
    assert details["query"]["public_ip"] == WAN
    assert details["query"]["at"] == "2026-10-12T08:23:41+00:00"
    assert (details["mode"], details["result_count"], details["truncated"]) == ("exact", 1, False)
    assert details["results"] == [
        {
            "kind": "exact",
            "score": 100,
            "traffic": [site.s1],
            "lease": lease_id(app_session_factory, PC_MAC),
            "person": site.person_id,
        }
    ]
    stored = canonical_json(details).decode()
    for personal in (PERSON_NAME, PC_MAC, "PLN-PC14", REASON.splitlines()[0]):
        assert personal not in stored  # the sealed audit log keeps ids, not names or text


def test_a_person_left_out_of_usage_reports_is_still_named(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    """``usage_excluded`` belongs to the usage reports (area U): the 5651 lookup never reads it."""
    setting(app_session_factory, "nat.public_ips", [WAN])
    signed_in(client, login_as, app_session_factory)
    excluded = client.patch(f"/api/v1/persons/{site.person_id}", json={"usage_excluded": True})
    assert excluded.status_code == 200, excluded.text
    response = client.post(
        "/api/v1/lookup/5651", json=body(public_ip=WAN, public_port=51001, protocol="tcp")
    )
    assert response.status_code == 201, response.text
    (match,) = response.json()["outcome"]["matches"]
    assert (match["chain"]["lease"]["mac"], match["chain"]["person"]["name"]) == (
        PC_MAC,
        PERSON_NAME,
    )


def test_double_nat_answers_ranked_candidates(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory, "auditor")
    response = client.post(
        "/api/v1/lookup/5651",
        json=body(public_ip="192.0.2.44", public_port=41234, dest_ip="203.0.113.10", dest_port=443),
    )
    assert response.status_code == 201, response.text
    outcome = response.json()["outcome"]
    assert (outcome["mode"], outcome["double_nat"]) == ("candidates", True)
    assert "double_nat" in keys(outcome)
    text_tr = next(e["text_tr"] for e in outcome["explanations"] if e["key"] == "double_nat")
    assert text_tr == EXPLANATIONS["double_nat"][0]
    assert text_tr.startswith("Kesin eşleşme için ISS modeminin NAT kaydı gerekir")
    first = outcome["matches"][0]
    assert (first["kind"], first["traffic"]["srcip"], first["destination_match"]) == (
        "candidate",
        PC,
        "ip_port",
    )
    assert first["chain"]["person"]["name"] == PERSON_NAME
    (entry,) = audits(app_session_factory, "lookup.run")
    assert entry.details["results"][0]["kind"] == "candidate"
    assert entry.details["results"][0]["traffic"] == [site.s1]


def test_unknown_destination_lists_nobody(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    response = client.post(
        "/api/v1/lookup/5651",
        json=body(public_ip="192.0.2.44", public_port=41234, dest_ip="203.0.113.9"),
    )
    assert response.status_code == 201, response.text
    data = response.json()
    assert (data["mode"], data["result_count"]) == ("none", 0)
    found = keys(data["outcome"])
    assert {"double_nat_no_candidates", "destination_not_found", "no_match"} <= set(found)
    assert "double_nat" not in found
    (entry,) = audits(app_session_factory, "lookup.run")
    assert entry.details["results"] == []


def test_internal_lookup_returns_the_chain(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PHONE))
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["mode"] == "internal"
    chain = data["outcome"]["attribution"]
    assert (chain["lease"]["mac"], chain["lease"]["hostname"], chain["person"]) == (
        PHONE_MAC,
        "Telefon-12",
        None,
    )
    assert "no_person" in [e["key"] for e in chain["explanations"]]
    (entry,) = audits(app_session_factory, "lookup.run")
    assert entry.details["results"] == [
        {"kind": "internal", "lease": lease_id(app_session_factory, PHONE_MAC), "person": None}
    ]


def test_vlan_changes_reach_the_next_lookup_and_keep_stored_ones(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    """A VLAN edited or deleted on the devices page is what the next lookup uses (the chain reads
    the table when it runs, like a department change); a stored lookup keeps its result."""
    signed_in(client, login_as, app_session_factory)

    def chain() -> tuple[int, dict[str, Any]]:
        response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PHONE))
        assert response.status_code == 201, response.text
        data = response.json()
        return data["id"], data["outcome"]["attribution"]

    first_id, first = chain()
    assert (first["vlan"], first["vlan_name"]) == (60, "Wi-Fi Personel")
    assert (first["department"], first["department_source"]) == ("Wi-Fi Personel", "vlan")
    with app_session_factory() as session:
        office = session.scalars(select(Department.id).where(Department.name == "İdari İşler"))
        office_id = office.one()
    edited = client.patch(
        "/api/v1/vlans",
        json={
            "subnet": "172.20.60.0/24",
            "name_tr": "Ofis kablosuz",
            "guest": True,
            "department_id": office_id,
        },
    )
    assert edited.status_code == 200, edited.text
    _, second = chain()
    assert (second["vlan_name"], second["department"]) == ("Ofis kablosuz", "İdari İşler")
    assert "guest_vlan" in [e["key"] for e in second["explanations"]]
    deleted = client.delete("/api/v1/vlans", params={"subnet": "172.20.60.0/24"})
    assert deleted.status_code == 204, deleted.text
    _, third = chain()
    # Without the VLAN only the lease's own VLAN number (from the DHCP server's name) is left.
    assert (third["vlan"], third["vlan_name"], third["department"]) == (60, None, None)
    stored = client.get(f"/api/v1/lookup/{first_id}").json()["outcome"]["attribution"]
    assert (stored["vlan_name"], stored["department"]) == ("Wi-Fi Personel", "Wi-Fi Personel")


# -- time zones -----------------------------------------------------------------------------------


def test_time_without_offset_is_europe_istanbul_unless_a_zone_is_given(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    local = body(internal_ip=PC)
    local["query"]["at"] = "2026-10-12T11:23:41"
    first = client.post("/api/v1/lookup/5651", json=local).json()
    assert first["query"]["at"] == "2026-10-12T08:23:41+00:00"
    assert first["query"]["time_input"] == {
        "text": "2026-10-12T11:23:41",
        "offset_given": False,
        "time_zone": "Europe/Istanbul",
    }
    utc = body(internal_ip=PC)
    utc["query"].update(at="2026-10-12 08:23:41", time_zone="UTC")
    second = client.post("/api/v1/lookup/5651", json=utc).json()
    assert second["query"]["at"] == first["query"]["at"]
    assert second["query"]["time_input"]["time_zone"] == "UTC"
    zulu = body(internal_ip=PC)
    zulu["query"].update(at="2026-10-12T08:23:41Z", time_zone="Asia/Tokyo")  # offset wins
    third = client.post("/api/v1/lookup/5651", json=zulu).json()
    assert third["query"]["at"] == first["query"]["at"]
    assert third["query"]["time_input"] == {
        "text": "2026-10-12T08:23:41Z",
        "offset_given": True,
        "time_zone": None,
    }


def test_parse_moment() -> None:
    moment, given = parse_moment("2026-10-12T11:23:41.250+03:00", "UTC")
    assert (moment.isoformat(), given) == ("2026-10-12T11:23:41.250000+03:00", True)
    moment, given = parse_moment("2026-10-12T11:23", "Europe/Istanbul")
    assert (moment.utcoffset(), given) == (AT.utcoffset(), False)
    with pytest.raises(ValueError, match="ISO-8601"):
        parse_moment("12.10.2026 11:23:41", "UTC")
    # zones with clock changes: ordinary local times are converted, the hour that occurs twice
    # (clocks back) and the hour that does not exist (clocks forward) are refused
    moment, given = parse_moment("2026-10-12T11:23:41", "Europe/Berlin")
    assert (moment.utcoffset(), given) == (timedelta(hours=2), False)
    moment, _ = parse_moment("2026-10-25T03:30:00", "Europe/Berlin")
    assert moment.utcoffset() == timedelta(hours=1)
    with pytest.raises(ValueError, match="occurs twice in Europe/Berlin"):
        parse_moment("2026-10-25T02:30:00", "Europe/Berlin")
    with pytest.raises(ValueError, match="does not exist in Europe/Berlin"):
        parse_moment("2026-03-29T02:30:00", "Europe/Berlin")
    # with an offset the same wall-clock times are unambiguous
    moment, given = parse_moment("2026-10-25T02:30:00+01:00", "Europe/Berlin")
    assert (moment.isoformat(), given) == ("2026-10-25T02:30:00+01:00", True)


@pytest.mark.parametrize(
    ("at", "message"),
    [
        ("2026-10-25T02:30:00", "occurs twice in Europe/Berlin"),
        ("2026-03-29T02:30:00", "does not exist in Europe/Berlin"),
    ],
)
def test_ambiguous_local_times_are_refused(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    at: str,
    message: str,
) -> None:
    signed_in(client, login_as, app_session_factory)
    request = body(internal_ip=PC)
    request["query"].update(at=at, time_zone="Europe/Berlin")
    response = client.post("/api/v1/lookup/5651", json=request)
    assert (response.status_code, response.json()["error"]["code"]) == (422, "validation_error")
    (detail,) = response.json()["error"]["details"]
    assert detail["loc"] == ["body", "query", "at"]
    assert message in detail["msg"]
    assert lookup_rows(app_session_factory) == []


def test_numbers_beyond_bigint_are_refused_not_database_errors(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    huge = 2**63
    for path in (
        f"/api/v1/lookup/{huge}",
        f"/api/v1/lookup/{10**20}",
        f"/api/v1/lookup/{huge}/report.pdf",
        f"/api/v1/lookup/{huge}/report.pdf.zd",
        f"/api/v1/lookups?cursor={huge}",
        f"/api/v1/lookups?cursor={10**20}",
    ):
        response = client.get(path)
        assert (response.status_code, response.json()["error"]["code"]) == (
            422,
            "validation_error",
        ), path
    largest = 2**63 - 1
    assert client.get(f"/api/v1/lookup/{largest}").status_code == 404
    assert client.get(f"/api/v1/lookups?cursor={largest}").status_code == 200


# -- validation -----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("change", "field"),
    [
        ({"authority": "   "}, "authority"),
        ({"file_no": ""}, "file_no"),
        ({"reason": " \n\t "}, "reason"),
        ({"authority": "Savcılık \u202e"}, "authority"),
        ({"file_no": "a\x00b"}, "file_no"),
        ({"request_date": "2999-01-01"}, "request_date"),
        ({"request_date": "12.10.2026"}, "request_date"),
        ({"reason": None}, "reason"),
    ],
)
def test_request_details_are_required_and_never_blank(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    change: dict[str, Any],
    field: str,
) -> None:
    signed_in(client, login_as, app_session_factory)
    payload = body(internal_ip=PC)
    for key, value in change.items():
        if value is None:
            del payload["request"][key]
        else:
            payload["request"][key] = value
    response = client.post("/api/v1/lookup/5651", json=payload)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(field in detail["loc"] for detail in error["details"])
    assert "Savcılık" not in response.text  # submitted values are never echoed
    assert lookup_rows(app_session_factory) == []
    assert audits(app_session_factory, "lookup.run") == []


@pytest.mark.parametrize(
    ("query", "field"),
    [
        ({"at": "yesterday evening"}, "at"),
        ({"at": "2026-10-12"}, "at"),
        ({"at": "2026-10-12T25:00:00"}, "at"),
        ({"internal_ip": "999.1.1.1"}, "internal_ip"),
        ({"public_ip": "192.0.2.44", "public_port": 70000}, "public_port"),
        ({"mac": "zz:zz"}, "mac"),
        ({"internal_ip": PC, "protocol": "sctp"}, "protocol"),
        ({"internal_ip": PC, "protocol": 300}, "protocol"),
        ({"internal_ip": PC, "tolerance_seconds": 3601}, "tolerance_seconds"),
        ({"internal_ip": PC, "time_zone": "Mars/Olympus_Mons"}, "time_zone"),
        ({"dest_domain": "bad domain.com"}, "dest_domain"),
        ({"internal_ip": PC, "unknown": 1}, "unknown"),
        ({"protocol": "tcp"}, "query"),  # nothing to look for
    ],
)
def test_invalid_queries_are_refused(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    query: dict[str, Any],
    field: str,
) -> None:
    signed_in(client, login_as, app_session_factory)
    payload = body()
    payload["query"].update(query)
    response = client.post("/api/v1/lookup/5651", json=payload)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert any(field in detail["loc"] for detail in error["details"]), error["details"]
    assert "999.1.1.1" not in response.text
    assert lookup_rows(app_session_factory) == []


def test_invalid_nat_settings_are_reported(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    setting(app_session_factory, "nat.public_ips", ["not an address"])
    signed_in(client, login_as, app_session_factory)
    response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PC))
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "lookup_settings_invalid"


# -- permissions ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "allowed"), [("admin", True), ("auditor", True), ("hr", False), ("viewer", False)]
)
def test_roles(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    role: str,
    allowed: bool,
) -> None:
    signed_in(client, login_as, app_session_factory, role)
    response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PC))
    if allowed:
        assert response.status_code == 201, response.text
    else:
        assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden")
        assert client.get("/api/v1/lookups").status_code == 403
        assert lookup_rows(app_session_factory) == []


def test_the_privacy_notice_is_required(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    auditor = login_as(client, "auditor")
    for method, url in (
        ("POST", "/api/v1/lookup/5651"),
        ("GET", "/api/v1/lookups"),
        ("GET", "/api/v1/lookup/1"),
        ("GET", "/api/v1/lookup/1/report.pdf"),
    ):
        response = client.request(method, url, json=body(internal_ip=PC))
        assert (response.status_code, response.json()["error"]["code"]) == (
            403,
            "privacy_notice_required",
        )
    assert lookup_rows(app_session_factory) == []
    accept_privacy(app_session_factory, auditor.user_id)
    assert client.post("/api/v1/lookup/5651", json=body(internal_ip=PC)).status_code == 201


def test_without_session_and_without_csrf(client: TestClient, site: Site) -> None:
    assert client.get("/api/v1/lookups").status_code == 401
    assert client.get("/api/v1/lookup/1").status_code == 401
    response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PC))
    assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_failed")


# -- reading lookups ------------------------------------------------------------------------------


def test_who_sees_which_lookup(
    app: Any, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    with (
        TestClient(app, base_url="http://testserver") as admin_client,
        TestClient(app, base_url="http://testserver") as auditor_client,
        TestClient(app, base_url="http://testserver") as other_client,
    ):
        admin = signed_in(admin_client, login_as, app_session_factory, "admin")
        auditor = signed_in(auditor_client, login_as, app_session_factory, "auditor")
        signed_in(other_client, login_as, app_session_factory, "auditor")
        own = auditor_client.post("/api/v1/lookup/5651", json=body(internal_ip=PC)).json()
        admins = [
            admin_client.post("/api/v1/lookup/5651", json=body(internal_ip=ip)).json()
            for ip in (PC, PHONE)
        ]

        response = auditor_client.get(f"/api/v1/lookup/{own['id']}")
        assert response.status_code == 200
        assert response.json()["outcome"] == own["outcome"]
        # another user's lookup is "not found", never "forbidden" (no enumeration)
        for client, lookup_id in (
            (auditor_client, admins[0]["id"]),
            (other_client, own["id"]),
            (auditor_client, 999_999),
        ):
            response = client.get(f"/api/v1/lookup/{lookup_id}")
            assert (response.status_code, response.json()["error"]["code"]) == (404, "not_found")
            assert client.get(f"/api/v1/lookup/{lookup_id}/report.pdf").status_code == 404
        assert admin_client.get(f"/api/v1/lookup/{own['id']}").status_code == 200

        views = audits(app_session_factory, "lookup.view")
        assert [(v.actor_id, v.details) for v in views] == [
            (auditor.user_id, {"lookup_id": own["id"], "own": True}),
            (admin.user_id, {"lookup_id": own["id"], "own": False}),
        ]

        mine = auditor_client.get("/api/v1/lookups").json()
        assert ([item["id"] for item in mine["items"]], mine["all_users"]) == ([own["id"]], False)
        assert other_client.get("/api/v1/lookups").json()["items"] == []
        everything = admin_client.get("/api/v1/lookups").json()
        assert everything["all_users"] is True
        assert [item["id"] for item in everything["items"]] == [
            admins[1]["id"],
            admins[0]["id"],
            own["id"],
        ]
        item = everything["items"][-1]
        assert (item["username"], item["authority"], item["file_no"], item["mode"]) == (
            auditor.username,
            AUTHORITY,
            FILE_NO,
            "internal",
        )
        assert "outcome" not in item
        assert item["report_sha256"] is None
        only_admin = admin_client.get("/api/v1/lookups", params={"mine": "true"}).json()
        assert [item["id"] for item in only_admin["items"]] == [admins[1]["id"], admins[0]["id"]]
        by_user = admin_client.get("/api/v1/lookups", params={"username": auditor.username})
        assert [item["id"] for item in by_user.json()["items"]] == [own["id"]]
        assert admin_client.get("/api/v1/lookups", params={"mode": "exact"}).json()["items"] == []

        page = admin_client.get("/api/v1/lookups", params={"limit": 2}).json()
        assert len(page["items"]) == 2
        assert page["next_cursor"] == admins[0]["id"]
        rest = admin_client.get(
            "/api/v1/lookups", params={"limit": 2, "cursor": page["next_cursor"]}
        ).json()
        assert ([item["id"] for item in rest["items"]], rest["next_cursor"]) == ([own["id"]], None)

        window = admin_client.get(
            "/api/v1/lookups",
            params={"from": "2000-01-01T00:00:00+00:00", "to": "2100-01-01T00:00:00+00:00"},
        )
        assert len(window.json()["items"]) == 3
        empty = admin_client.get("/api/v1/lookups", params={"from": "2100-01-01T00:00:00Z"})
        assert empty.json()["items"] == []
        backwards = admin_client.get(
            "/api/v1/lookups", params={"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"}
        )
        assert backwards.status_code == 422


# -- time budget ----------------------------------------------------------------------------------


def _slow_sql(session: Session, *args: Any, **kwargs: Any) -> Any:
    session.execute(text("SELECT pg_sleep(5)"))
    raise AssertionError("the statement timeout did not stop the query")


def _slow_python(session: Session, *args: Any, **kwargs: Any) -> Any:
    session.execute(text("SELECT 1"))
    time.sleep(1.2)
    session.execute(text("SELECT 2"))  # never sent: the budget is used up
    raise AssertionError("the deadline did not stop the next statement")


@pytest.mark.parametrize("slow", [_slow_sql, _slow_python])
def test_a_slow_lookup_is_stopped_with_a_clear_error(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    slow: Callable[..., Any],
) -> None:
    setting(app_session_factory, "lookup.timeout_seconds", 1)
    monkeypatch.setattr(lookup_router, "run_lookup", slow)
    who = signed_in(client, login_as, app_session_factory)
    started = time.monotonic()
    response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PC))
    assert time.monotonic() - started < 3
    assert response.status_code == 504, response.text
    error = response.json()["error"]
    assert error["code"] == "lookup_timeout"
    assert "1 saniye" in error["message_tr"]
    assert lookup_rows(app_session_factory) == []
    (failed,) = audits(app_session_factory, "lookup.run_failed")
    assert failed.actor_id == who.user_id
    assert (failed.details["reason"], failed.details["limit_seconds"]) == ("timeout", 1)
    assert failed.details["query"]["internal_ip"] == PC
    assert failed.details["request"]["file_no"] == FILE_NO
    assert audits(app_session_factory, "lookup.run") == []
    # the next request works normally on the same pooled connection
    monkeypatch.undo()
    assert client.post("/api/v1/lookup/5651", json=body(internal_ip=PC)).status_code == 201


def test_other_database_errors_are_not_reported_as_timeouts(
    app: Any,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(session: Session, *args: Any, **kwargs: Any) -> Any:
        session.execute(text("SELECT * FROM no_such_table"))

    monkeypatch.setattr(lookup_router, "run_lookup", broken)
    client = TestClient(app, base_url="http://testserver", raise_server_exceptions=False)
    signed_in(client, login_as, app_session_factory)
    response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PC))
    assert (response.status_code, response.json()["error"]["code"]) == (500, "internal_error")
    assert audits(app_session_factory, "lookup.run_failed") == []


def test_statement_deadline_limits_each_statement_to_the_remaining_time(
    app_session_factory: sessionmaker[Session],
) -> None:
    with app_session_factory() as session:
        started = time.monotonic()
        with pytest.raises(DBAPIError) as raised, statement_deadline(session, 0.6):
            session.execute(text("SELECT pg_sleep(3)"))
        assert time.monotonic() - started < 2
        assert getattr(raised.value.orig, "sqlstate", None) == "57014"
        session.rollback()
        with pytest.raises(LookupTimeoutError), statement_deadline(session, 0):
            session.execute(text("SELECT 1"))
        session.rollback()
        # outside the block statements are not limited (the listener is gone)
        assert session.execute(text("SELECT 3")).scalar_one() == 3
        assert session.execute(text("SHOW statement_timeout")).scalar_one() == "0"


def test_lookup_output_matches_the_stored_json(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    data = client.post(
        "/api/v1/lookup/5651", json=body(public_ip="192.0.2.44", public_port=41234)
    ).json()
    fetched = client.get(f"/api/v1/lookup/{data['id']}").json()
    assert fetched["outcome"] == data["outcome"]
    assert fetched["elapsed_ms"] is None
    assert canonical_json(fetched["outcome"])  # no floats anywhere
    assert json.loads(json.dumps(fetched["query"])) == data["query"]


# -- every route of these routers is guarded ------------------------------------------------------

GUARDED_PREFIXES = ("/api/v1/lookup", "/api/v1/raw")
EXPECTED_ROUTES = {
    ("POST", "/api/v1/lookup/5651"),
    ("GET", "/api/v1/lookup/{lookup_id}"),
    ("GET", "/api/v1/lookup/{lookup_id}/report.pdf"),
    ("GET", "/api/v1/lookup/{lookup_id}/report.pdf.zd"),
    ("GET", "/api/v1/lookups"),
    ("GET", "/api/v1/raw"),
}


def test_every_route_needs_a_session_and_lookup_or_search_permission(
    app: Any, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    """A new route under these prefixes fails here until it is classified (and guarded)."""
    routes = {
        (method, path)
        for path, item in app.openapi()["paths"].items()
        for method in (m.upper() for m in item)
        if path.startswith(GUARDED_PREFIXES)
    }
    assert routes == EXPECTED_ROUTES
    params: dict[str, str | int] = {"source": "fortigate", "day": "2026-10-12", "line": 1}
    with TestClient(app, base_url="http://testserver") as anonymous:
        anonymous.get("/api/v1/health")  # CSRF cookie for the POST
        anonymous.headers["X-CSRF-Token"] = anonymous.cookies["arci_csrf"]
        for method, path in sorted(routes):
            url = path.replace("{lookup_id}", "1")
            response = anonymous.request(method, url, params=params, json=body(internal_ip=PC))
            assert response.status_code == 401, (method, path)
    for role in ("hr", "viewer"):
        with TestClient(app, base_url="http://testserver") as client:
            signed_in(client, login_as, app_session_factory, role)
            for method, path in sorted(routes):
                url = path.replace("{lookup_id}", "1")
                response = client.request(method, url, params=params, json=body(internal_ip=PC))
                assert (response.status_code, response.json()["error"]["code"]) == (
                    403,
                    "forbidden",
                ), (role, method, path)
