"""5651 lookup API end to end (spec 9.5, 12.3 page 6, 13, 14, 17): real spool lines and rows, the
lease timeline rebuilt by the worker job, then ``POST /lookup/5651`` — exact, double-NAT
candidates, internal, none; request details, time zones, roles, the KVKK notice, the audit
entries (with the exit, contract 2.12), visibility of other users' lookups, the list and the
statement timeout; the Wave 3 fields (the egress block, a FortiGate lease, srcmac and the
interfaces, a second exit, a stored Wave 2 outcome) and the 503 of unreadable exits."""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.lookup_testing import (
    AT,
    DAY,
    FORTIGATE_LEASE,
    PC,
    PC_MAC,
    PERSON_NAME,
    PHONE,
    PHONE_MAC,
    WAN,
    WAN2,
    Site,
    accept_privacy,
    build_site,
    enter_exits,
    iso,
)
from arcilogger_api.routers import lookup as lookup_router
from arcilogger_api.routers.lookup import LookupTimeoutError, parse_moment, statement_deadline
from arcilogger_api.testing import LoggedIn
from arcilogger_core.canonical import canonical_json
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.iputil import canonical_ip
from arcilogger_core.layout import Layout
from arcilogger_db.egress import legacy_egresses
from arcilogger_db.models import AuditLog, Department, Lease, Lookup
from arcilogger_db.settings import set_setting
from arcilogger_worker.lookup import EXPLANATIONS, run_lookup
from arcilogger_worker.testing import tr

#: The FortiGate's WAN address behind the ISP router as revision 0018 converts Wave 2's
#: nat.public_ips = [WAN] or nat.wan_transip = WAN: the NAT address of the isp_nat exit wan
#: (contract 2.8.2 cases (c), (h)); an exact lookup on it keeps double_nat true.
WAN_EXIT = legacy_egresses([], WAN, False)[0]

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
    setting(app_session_factory, "nat.egresses", WAN_EXIT)
    admin = signed_in(client, login_as, app_session_factory)
    started = time.monotonic()
    response = client.post(
        "/api/v1/lookup/5651", json=body(public_ip=WAN, public_port=51001, protocol="tcp")
    )
    assert response.status_code == 201, response.text
    assert time.monotonic() - started < 5  # spec 14: < 5 s for ±60 s
    data = response.json()
    outcome = data["outcome"]
    assert (data["mode"], data["result_count"], outcome["double_nat"]) == ("exact", 1, True)
    assert outcome["egress"]["resolution"] == "transip"
    assert [e["name"] for e in outcome["egress"]["egresses"]] == ["wan"]
    assert outcome["matches"][0]["egress"] == "wan"
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
    # The exit in machine form (contract 2.12): exit names are firewall configuration.
    assert details["egress"] == {"resolution": "transip", "names": ["wan"]}
    stored = canonical_json(details).decode()
    for personal in (PERSON_NAME, PC_MAC, "PLN-PC14", REASON.splitlines()[0]):
        assert personal not in stored  # the sealed audit log keeps ids, not names or text


def test_a_person_left_out_of_usage_reports_is_still_named(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    """``usage_excluded`` belongs to the usage reports (area U): the 5651 lookup never reads it."""
    setting(app_session_factory, "nat.egresses", WAN_EXIT)
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
    # No exits configured: the implicit exit, kept with its empty name.
    assert entry.details["egress"] == {"resolution": "unconfigured", "names": [""]}


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
    assert entry.details["egress"] is None  # no public IP, no exit


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


@pytest.mark.parametrize(
    ("query", "fields"),
    [
        ({"public_ip": "2001:db8::44", "public_port": 41234}, ["public_ip"]),
        ({"internal_ip": "FD00:0:0:42::57"}, ["internal_ip"]),
        # An IPv4 address written as IPv6 never equals the logs' IPv4 values: refused as well.
        (
            {"public_ip": "::ffff:192.0.2.44", "internal_ip": "fd00::57"},
            ["public_ip", "internal_ip"],
        ),
    ],
)
def test_ipv6_lookups_are_refused_and_audited(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    query: dict[str, Any],
    fields: list[str],
) -> None:
    """No IPv6 identity evidence (NDP, DHCPv6): a clear refusal instead of a result — never the
    IPv4 sessions through the FortiGate's WAN address as candidates; the attempt is audited."""
    setting(app_session_factory, "nat.egresses", WAN_EXIT)
    who = signed_in(client, login_as, app_session_factory, "auditor")
    response = client.post("/api/v1/lookup/5651", json=body(**query))
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    text_tr, text_en = lookup_router.MESSAGES["ipv6_not_supported"]
    assert (error["code"], error["message_tr"], error["message_en"]) == (
        "ipv6_not_supported",
        text_tr,
        text_en,
    )
    assert "IPv6 adresleriyle 5651 sorgusu yapılamaz" in text_tr
    assert "başka yollarla yanıtlanmalıdır" in text_tr
    assert "must be answered by other means" in text_en
    assert [detail["loc"] for detail in error["details"]] == [
        ["body", "query", field] for field in fields
    ]
    assert lookup_rows(app_session_factory) == []
    assert audits(app_session_factory, "lookup.run") == []
    (failed,) = audits(app_session_factory, "lookup.run_failed")
    assert (failed.actor_id, failed.details["reason"]) == (who.user_id, "ipv6_not_supported")
    for field in fields:  # canonical, as stored for any lookup
        assert failed.details["query"][field] == canonical_ip(query[field])
    assert failed.details["request"] == {
        "authority": AUTHORITY,
        "file_no": FILE_NO,
        "request_date": "2026-10-12",
        "reason_sha256": hashlib.sha256(REASON.encode()).hexdigest(),
    }
    # The same request with IPv4 addresses runs normally.
    assert client.post("/api/v1/lookup/5651", json=body(internal_ip=PC)).status_code == 201


#: The 503 of a stored exit value the router cannot read (contract 2.12), Turkish first.
SETTINGS_INVALID_TR = (
    "5651 sorgu ayarları geçersiz: İnternet çıkışları (nat.egresses) değeri okunamadı (Ayarlar → "
    "Uygulama ayarları → 5651 sorgu ve kira zaman çizelgesi → İnternet çıkışları (NAT)); sistem "
    "yöneticisi düzeltmelidir."
)
SETTINGS_INVALID_EN = (
    "The 5651 lookup settings are invalid: the internet exits value (nat.egresses) cannot be read "
    "(Settings → Application settings → 5651 lookup and lease timeline → Internet exits (NAT)); "
    "an administrator must fix it."
)
SETTINGS_UNREADABLE_TR = (
    "5651 sorgu ayarları geçersiz: kayıtlı bir sorgu ya da kira ayarı okunamadı (Ayarlar → "
    "Uygulama ayarları); sistem yöneticisi düzeltmelidir."
)
SETTINGS_UNREADABLE_EN = (
    "The 5651 lookup settings are invalid: a stored lookup or lease setting cannot be read "
    "(Settings → Application settings); an administrator must fix it."
)


@pytest.mark.parametrize(
    "stored",
    [
        {"hat-1": {"mode": "direct", "public": ["x"]}},  # an address the rules refuse
        ["hat-1"],  # not even an object
        {"hat-1": {"mode": "bridge", "public": ["198.51.100.7"]}},  # an unknown mode
    ],
    ids=["bad-address", "not-an-object", "bad-mode"],
)
def test_invalid_nat_settings_are_reported(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    stored: Any,
) -> None:
    """A stored nat.egresses the exit rules refuse (written around the validator) stops the
    public-IP lookups with 503 lookup_settings_invalid, never 500 (the router parses the exits
    inside its try, C1's line), with the text naming the setting and where it is edited;
    lookups that need no exit (an internal IP or MAC) still answer (contract 2.8.1, 2.12,
    implementation note round 5)."""
    setting(app_session_factory, "nat.egresses", stored)
    signed_in(client, login_as, app_session_factory)
    for query in ({"public_ip": WAN, "public_port": 51001}, {"public_ip": WAN}):
        response = client.post("/api/v1/lookup/5651", json=body(**query))
        assert response.status_code == 503, response.text
        error = response.json()["error"]
        assert (error["code"], error["message_tr"], error["message_en"]) == (
            "lookup_settings_invalid",
            SETTINGS_INVALID_TR,
            SETTINGS_INVALID_EN,
        )
        assert "Ayarlar → NAT" not in response.text  # the Wave 2 page that does not exist
        assert "Settings → NAT" not in response.text
    assert lookup_router.MESSAGES["lookup_settings_invalid"] == (
        SETTINGS_INVALID_TR,
        SETTINGS_INVALID_EN,
    )
    assert client.post("/api/v1/lookup/5651", json=body(internal_ip=PC)).status_code == 201
    assert client.post("/api/v1/lookup/5651", json=body(mac=PC_MAC)).status_code == 201
    assert audits(app_session_factory, "lookup.run_failed") == []


@pytest.mark.parametrize(
    ("key", "stored"),
    [
        ("dhcp.fortigate_logids", ["0100026001"]),  # not an object
        ("lookup.timeout_seconds", "beş"),  # not a number
    ],
    ids=["fortigate-logids", "timeout"],
)
def test_another_unreadable_lookup_setting_answers_the_same_503(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    site: Site,
    caplog: pytest.LogCaptureFixture,
    key: str,
    stored: Any,
) -> None:
    """``LookupSettings.from_settings`` is strict for the ``dhcp.fortigate_*`` values, and
    ``lookup.timeout_seconds`` must be a number: one written around the validator stops every
    lookup with the same code, never 500, but with the second text, which names no key (the
    key is in the WARNING of the system log; review Q1)."""
    setting(app_session_factory, key, stored)
    signed_in(client, login_as, app_session_factory)
    caplog.set_level(logging.WARNING, logger="arcilogger.api.lookup")
    for query in ({"internal_ip": PC}, {"public_ip": WAN, "public_port": 51001}):
        caplog.clear()
        response = client.post("/api/v1/lookup/5651", json=body(**query))
        assert response.status_code == 503, response.text
        error = response.json()["error"]
        assert (error["code"], error["message_tr"], error["message_en"]) == (
            "lookup_settings_invalid",
            SETTINGS_UNREADABLE_TR,
            SETTINGS_UNREADABLE_EN,
        )
        assert key not in response.text
        assert "nat.egresses" not in response.text
        (record,) = [r for r in caplog.records if r.name == "arcilogger.api.lookup"]
        assert record.levelno == logging.WARNING
    assert lookup_router.SETTINGS_UNREADABLE == (SETTINGS_UNREADABLE_TR, SETTINGS_UNREADABLE_EN)
    assert audits(app_session_factory, "lookup.run_failed") == []


# -- the Wave 3 fields of the outcome (contract 2.10, 2.12) ---------------------------------------

#: The exits of the dual-WAN site: line 1 through WAN, line 2 through WAN2 on wan2.
EXITS = {
    "hat-1": {"mode": "isp_nat", "transips": [WAN], "interfaces": ["wan1"]},
    "hat-2": {"mode": "isp_nat", "transips": [WAN2], "interfaces": ["wan2"]},
}


def test_the_egress_block_of_a_public_ip_lookup(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    enter_exits(app_session_factory, WAN_EXIT, at=AT - timedelta(days=30), by="yonetici")
    signed_in(client, login_as, app_session_factory)
    response = client.post("/api/v1/lookup/5651", json=body(public_ip=WAN, public_port=51001))
    assert response.status_code == 201, response.text
    outcome = response.json()["outcome"]
    egress = outcome["egress"]
    assert set(egress) == {"resolution", "egresses", "searched", "changes", "changed_after_event"}
    (entry,) = egress["egresses"]
    assert set(entry) == {
        "name",
        "label",
        "mode",
        "public",
        "public_dynamic",
        "transips",
        "interfaces",
        "port_preservation",
    }
    assert (entry["name"], entry["mode"], entry["transips"]) == ("wan", "isp_nat", [WAN])
    assert set(egress["searched"]) == {
        "transips",
        "unclaimed",
        "unclaimed_seen",
        "outside_seen",
        "interfaces",
        "interface_filter",
    }
    assert egress["searched"]["transips"] == [WAN]
    assert egress["changed_after_event"] is False
    assert [change["name"] for change in egress["changes"]] == ["wan"]
    assert outcome["matches"]
    for match in outcome["matches"]:
        assert (match["egress"], match["untrusted_sources"]) == ("wan", [])
    assert outcome["unconverted_dhcp"] == []


def test_a_fortigate_lease_in_the_response(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    site = build_site(app_session_factory, layout, dhcp="fortigate")
    signed_in(client, login_as, app_session_factory)
    response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PC))
    assert response.status_code == 201, response.text
    outcome = response.json()["outcome"]
    assert outcome["mode"] == "internal"
    chain = outcome["attribution"]
    lease = chain["lease"]
    assert (lease["source_table"], lease["lease_seconds"]) == ("fg_dhcp", FORTIGATE_LEASE)
    assert lease["last_dhcp_evidence"] == tr(DAY, 10, 42, 18).astimezone(UTC).isoformat()
    assert next(ref["row_id"] for ref in lease["evidence"] if ref["table"] == "fg_dhcp") == (
        site.dhcp_pc
    )
    assert (chain["mac_source"], chain["person"]["name"]) == ("lease", PERSON_NAME)
    assert outcome["unconverted_dhcp"] == []
    assert outcome["egress"] is None


def test_srcmac_and_interfaces_in_the_response(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """The session's interfaces are always in the response; its srcmac only while its
    interface is listed in lookup.srcmac_interfaces (contract 2.9)."""
    build_site(app_session_factory, layout, srcmac=True)
    enter_exits(app_session_factory, WAN_EXIT, at=AT - timedelta(days=30), by="yonetici")
    signed_in(client, login_as, app_session_factory)

    def traffic() -> dict[str, Any]:
        response = client.post("/api/v1/lookup/5651", json=body(public_ip=WAN, public_port=51001))
        assert response.status_code == 201, response.text
        (match,) = response.json()["outcome"]["matches"]
        found: dict[str, Any] = match["traffic"]
        return found

    off = traffic()
    assert (off["srcintf"], off["dstintf"], off["srcmac"]) == ("vlan42", None, None)
    setting(app_session_factory, "lookup.srcmac_interfaces", ["vlan42"])
    on = traffic()
    assert (on["srcintf"], on["srcmac"]) == ("vlan42", PC_MAC)


def test_a_second_exit(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """A lookup by the second exit's NAT address lists only the session that left through it,
    with its exit on the match."""
    site = build_site(app_session_factory, layout, srcmac=True, wan2=True)
    enter_exits(app_session_factory, EXITS, at=AT - timedelta(days=30), by="yonetici")
    signed_in(client, login_as, app_session_factory)
    response = client.post("/api/v1/lookup/5651", json=body(public_ip=WAN2))
    assert response.status_code == 201, response.text
    outcome = response.json()["outcome"]
    assert outcome["egress"]["resolution"] == "transip"
    assert [entry["name"] for entry in outcome["egress"]["egresses"]] == ["hat-2"]
    (match,) = outcome["matches"]
    assert match["egress"] == "hat-2"
    assert (match["traffic"]["srcip"], match["traffic"]["dstintf"]) == (PHONE, "wan2")
    assert [ref["row_id"] for ref in match["traffic"]["evidence"]] == [site.wan2]
    (entry,) = audits(app_session_factory, "lookup.run")
    assert entry.details["egress"] == {"resolution": "transip", "names": ["hat-2"]}


def test_a_stored_wave_2_outcome_is_returned_as_stored(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], site: Site
) -> None:
    """A lookup stored before ADR-0016 keeps its outcome as it was stored: no egress block, no
    FortiGate fields are added on the way out (contract 2.12, day-0 deviation)."""
    signed_in(client, login_as, app_session_factory)
    created = client.post("/api/v1/lookup/5651", json=body(internal_ip=PC)).json()
    stored = dict(created["outcome"])
    for key in ("egress", "unconverted_dhcp"):
        stored.pop(key)
    with app_session_factory() as session:
        session.get_one(Lookup, created["id"]).outcome = stored
        session.commit()
    fetched = client.get(f"/api/v1/lookup/{created['id']}")
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["outcome"] == stored
    assert "egress" not in fetched.json()["outcome"]


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
    session.execute(text("SELECT pg_sleep(60)"))
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
    # No hanging request: an uncancelled pg_sleep(60) would hold it for 60 s. The request also
    # does work outside the lookup's budget (sign-in, rollback, the lookup.run_failed audit write
    # and spool flush, the 504), which takes well over a second on a loaded CI runner, so the
    # wall clock only gets this loose bound; the budget itself is checked on elapsed_ms below.
    assert time.monotonic() - started < 20
    assert response.status_code == 504, response.text
    error = response.json()["error"]
    assert error["code"] == "lookup_timeout"
    assert "1 saniye" in error["message_tr"]
    assert lookup_rows(app_session_factory) == []
    (failed,) = audits(app_session_factory, "lookup.run_failed")
    assert failed.actor_id == who.user_id
    assert (failed.details["reason"], failed.details["limit_seconds"]) == ("timeout", 1)
    # The 1 s budget stopped the lookup (the router times only run_lookup, ~1.0 s for _slow_sql,
    # ~1.2 s for _slow_python), not a later timeout: a budget of 4 s would give ~4000 here.
    assert failed.details["elapsed_ms"] < 3000
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


def test_the_custom_plans_last_as_long_as_the_lookup_transaction(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    site: Site,
) -> None:
    """The lookup's statements are planned with their values; the statements after it on its
    pooled connection are planned as before."""
    show = select(func.current_setting("plan_cache_mode"))
    seen: list[tuple[Any, Any]] = []

    def watched(session: Session, *args: Any, **kwargs: Any) -> Any:
        seen.append((session.scalar(show), session.connection().connection.dbapi_connection))
        return run_lookup(session, *args, **kwargs)

    monkeypatch.setattr(lookup_router, "run_lookup", watched)
    signed_in(client, login_as, app_session_factory)
    response = client.post("/api/v1/lookup/5651", json=body(internal_ip=PC))
    assert response.status_code == 201, response.text
    assert response.json()["outcome"]["attribution"]["person"]["name"] == PERSON_NAME
    ((inside, pooled),) = seen
    assert pooled is not None
    after = pooled.execute("SELECT current_setting('plan_cache_mode')").fetchone()
    pooled.rollback()  # back to the pool as it was
    assert inside == "force_custom_plan"
    assert after == ("auto",)


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
