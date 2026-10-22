"""VLAN management (spec 2.2, 9.2): add, edit and delete VLANs/subnets — strict networks, unique
subnet, VLAN id and DHCP server, partial edits that keep the old ``{subnet, department_id}`` body,
a moved subnet, nested subnets — and the consumers that follow at once (device list, department
list), with the audit entries (numbers and field names, never the texts)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.directory_testing import add_device, department_id
from arcilogger_api.lookup_testing import accept_privacy
from arcilogger_api.testing import LoggedIn
from arcilogger_db.models import AuditLog, Vlan

Login = Callable[..., LoggedIn]
API = "/api/v1"
#: Devices in the networks the tests add (outside the example plan).
LAB_PC, LAB_PC2, NAS = "f0:1f:af:00:02:01", "f0:1f:af:00:02:02", "f0:1f:af:00:02:03"
LAB = "10.20.0.0/24"
#: A full new VLAN (the example plan has VLAN ids up to 61 and 172.20.x.0/24 networks).
FULL: dict[str, Any] = {
    "subnet": LAB,
    "vlan_id": 70,
    "name_tr": "Laboratuvar",
    "purpose": "Test bench PCs",
    "dhcp_server": "dhcp70",
    "guest": False,
    "notes": "Kat 2\nOda 5",
}


@pytest.fixture
def factory(app_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return app_session_factory


@pytest.fixture
def admin(client: TestClient, login_as: Login, factory: sessionmaker[Session]) -> LoggedIn:
    who = login_as(client, "admin", username="yonetici")
    accept_privacy(factory, who.user_id)
    return who


def _audit(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
        for row in rows:
            session.expunge(row)
    return rows


def _dept(factory: sessionmaker[Session], name: str) -> int:
    with factory() as session:
        return department_id(session, name)


def _count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(Vlan)) or 0


def _devices(factory: sessionmaker[Session], *devices: tuple[str, str]) -> None:
    with factory() as session:
        for mac, ip in devices:
            add_device(session, mac, last_ip=ip)
        session.commit()


def _vlan_of(client: TestClient, mac: str) -> tuple[Any, Any, Any]:
    device = client.get(f"{API}/devices/{mac}").json()
    return device["vlan_name"], device["department"], device["department_source"]


def _error(response: Any) -> dict[str, Any]:
    error: dict[str, Any] = response.json()["error"]
    return error


# -- add ------------------------------------------------------------------------------------------


def test_add_a_vlan_and_its_devices_follow(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    office = _dept(factory, "Planlama")
    _devices(factory, (LAB_PC, "10.20.0.15"))
    assert _vlan_of(client, LAB_PC) == (None, None, None)
    body = {**FULL, "subnet": f" {LAB} ", "name_tr": " Laboratuvar ", "department_id": office}
    response = client.post(f"{API}/vlans", json=body)
    assert response.status_code == 201, response.text
    created = {**FULL, "department": {"id": office, "name": "Planlama"}}
    assert response.json() == created
    listed = client.get(f"{API}/vlans").json()
    assert len(listed) == 9
    # VLAN id order; the transit network without an id stays last.
    assert [v["vlan_id"] for v in listed][-3:] == [61, 70, None]
    assert listed[-2] == created
    assert _vlan_of(client, LAB_PC) == (
        "Laboratuvar",
        {"id": office, "name": "Planlama"},
        "vlan",
    )
    departments = {d["name"]: d for d in client.get(f"{API}/departments").json()}
    assert departments["Planlama"]["vlan_ids"] == [42, 70]

    # Only the subnet and the name are required.
    bare = client.post(f"{API}/vlans", json={"subnet": "10.30.0.0/30", "name_tr": "Hat 2"})
    assert bare.status_code == 201, bare.text
    assert bare.json() == {
        "subnet": "10.30.0.0/30",
        "vlan_id": None,
        "name_tr": "Hat 2",
        "purpose": None,
        "dhcp_server": None,
        "department": None,
        "guest": False,
        "notes": None,
    }
    # Empty optional texts are "no value"; IPv6 networks are written in canonical form.
    six = client.post(
        f"{API}/vlans",
        json={"subnet": "FD00:0:0:20::/64", "name_tr": "IPv6 ofis", "purpose": " ", "notes": ""},
    )
    assert six.status_code == 201, six.text
    assert (six.json()["subnet"], six.json()["purpose"], six.json()["notes"]) == (
        "fd00:0:0:20::/64",
        None,
        None,
    )

    # The sealed audit log gets the network, numbers and field names, never the texts.
    entries = _audit(factory, "vlan.create")
    assert [e.target for e in entries] == [
        f"vlan:{LAB}",
        "vlan:10.30.0.0/30",
        "vlan:fd00:0:0:20::/64",
    ]
    assert entries[0].details == {
        "subnet": LAB,
        "vlan_id": 70,
        "department_id": office,
        "guest": False,
        "fields": [
            "subnet",
            "vlan_id",
            "name_tr",
            "purpose",
            "dhcp_server",
            "department_id",
            "guest",
            "notes",
        ],
    }
    # "fields" lists what the request sent with a value: not the default guest flag (FULL sends
    # it), not the empty texts.
    assert entries[1].details == {
        "subnet": "10.30.0.0/30",
        "vlan_id": None,
        "department_id": None,
        "guest": False,
        "fields": ["subnet", "name_tr"],
    }
    assert entries[2].details["fields"] == ["subnet", "name_tr"]
    for text in ("Laboratuvar", "Test bench", "dhcp70", "Kat 2", "Hat 2"):
        assert text not in str([e.details for e in entries])


def test_refusals_say_what_is_taken_and_write_nothing(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    before = _count(factory)
    taken = (
        ({"subnet": "172.20.43.0/24"}, "vlan_exists", "172.20.43.0/24 ağı zaten tanımlı."),
        (
            {"vlan_id": 43},
            "vlan_id_taken",
            "VLAN numarası 43 başka bir ağda kullanılıyor (172.20.43.0/24).",
        ),
        (
            {"dhcp_server": "dhcp43"},
            "dhcp_server_taken",
            "dhcp43 DHCP sunucusu başka bir ağa bağlı (172.20.43.0/24).",
        ),
    )
    for change, code, text_tr in taken:
        response = client.post(f"{API}/vlans", json={**FULL, **change})
        assert response.status_code == 409, (change, response.text)
        error = _error(response)
        assert (error["code"], error["message_tr"]) == (code, text_tr)
        assert error["message_en"]
        assert error["message_en"] != text_tr
    assert _error(client.post(f"{API}/vlans", json={**FULL, "vlan_id": 43}))["message_en"] == (
        "VLAN id 43 is used by another network (172.20.43.0/24)."
    )
    unknown = client.post(f"{API}/vlans", json={**FULL, "department_id": 999999})
    assert unknown.status_code == 422
    assert _error(unknown)["details"][0]["loc"] == ["body", "department_id"]
    invalid: list[tuple[dict[str, Any], str]] = [
        ({"subnet": "10.20.0.1/24"}, "subnet"),  # host bits set
        ({"subnet": "10.20.0.0"}, "subnet"),  # no prefix length
        ({"subnet": "10.20.0.0/33"}, "subnet"),
        ({"subnet": "fe80::%1/64"}, "subnet"),  # zone id
        ({"subnet": "::ffff:10.20.0.0/120"}, "subnet"),  # IPv4 written as IPv6
        ({"subnet": "::ffff:0:0/96"}, "subnet"),
        ({"subnet": "nope"}, "subnet"),
        ({"subnet": ""}, "subnet"),
        ({"vlan_id": 0}, "vlan_id"),
        ({"vlan_id": 4095}, "vlan_id"),
        ({"vlan_id": "x"}, "vlan_id"),
        ({"name_tr": "   "}, "name_tr"),
        ({"name_tr": "a" * 201}, "name_tr"),
        ({"name_tr": "Satır\nkırık"}, "name_tr"),
        ({"purpose": "a" * 501}, "purpose"),
        ({"dhcp_server": "dhcp 70"}, "dhcp_server"),
        ({"dhcp_server": "dhcp:70"}, "dhcp_server"),
        ({"guest": "maybe"}, "guest"),
        ({"notes": "a" * 2001}, "notes"),
        ({"color": "red"}, "color"),
    ]
    for change, field in invalid:
        response = client.post(f"{API}/vlans", json={**FULL, **change})
        assert response.status_code == 422, (change, response.text)
        assert _error(response)["details"][0]["loc"][:2] == ["body", field], change
    host_bits = client.post(f"{API}/vlans", json={**FULL, "subnet": "10.20.0.1/24"})
    assert "10.20.0.0/24" in _error(host_bits)["details"][0]["msg"]
    # No IPv4 address of a lease, a device or a log ever falls into an IPv4-mapped network.
    mapped = client.post(f"{API}/vlans", json={**FULL, "subnet": "::FFFF:a14:0/112"})
    assert "enter the IPv4 network 10.20.0.0/16" in _error(mapped)["details"][0]["msg"]
    missing = client.post(f"{API}/vlans", json={"subnet": LAB})
    assert _error(missing)["details"][0]["loc"] == ["body", "name_tr"]
    assert _count(factory) == before
    assert _audit(factory, "vlan.create") == []


# -- edit -----------------------------------------------------------------------------------------


def test_edit_changes_only_the_fields_sent(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    office = _dept(factory, "Planlama")
    assert client.post(f"{API}/vlans", json={**FULL, "department_id": office}).status_code == 201
    renamed = client.patch(
        f"{API}/vlans", json={"subnet": LAB, "name_tr": "Ar-Ge Laboratuvarı", "guest": True}
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json() == {
        **FULL,
        "name_tr": "Ar-Ge Laboratuvarı",
        "guest": True,
        "department": {"id": office, "name": "Planlama"},
    }
    # null (or an empty text) clears an optional field; the old web body still works.
    cleared = client.patch(
        f"{API}/vlans",
        json={"subnet": LAB, "purpose": None, "notes": "", "dhcp_server": None, "vlan_id": None},
    )
    assert cleared.status_code == 200, cleared.text
    out = cleared.json()
    assert (out["purpose"], out["notes"], out["dhcp_server"], out["vlan_id"]) == (
        None,
        None,
        None,
        None,
    )
    old_body = client.patch(f"{API}/vlans", json={"subnet": LAB, "department_id": None})
    assert old_body.json()["department"] is None
    assert old_body.json()["name_tr"] == "Ar-Ge Laboratuvarı"
    renumbered = client.patch(
        f"{API}/vlans",
        json={"subnet": LAB, "vlan_id": 71, "dhcp_server": "dhcp71", "department_id": office},
    )
    assert (renumbered.json()["vlan_id"], renumbered.json()["dhcp_server"]) == (71, "dhcp71")
    departments = {d["name"]: d for d in client.get(f"{API}/departments").json()}
    assert departments["Planlama"]["vlan_ids"] == [42, 71]
    # Nothing sent, or the same values again: no change, no audit entry.
    same = {"subnet": LAB, "name_tr": "Ar-Ge Laboratuvarı", "guest": True, "vlan_id": 71}
    for body in ({"subnet": LAB}, same, {"subnet": LAB, "new_subnet": f" {LAB} "}):
        response = client.patch(f"{API}/vlans", json=body)
        assert response.status_code == 200, (body, response.text)
        assert response.json()["vlan_id"] == 71

    updates = [e.details for e in _audit(factory, "vlan.update")]
    assert updates == [
        {
            "subnet": LAB,
            "vlan_id": 70,
            "changed": ["name_tr", "guest"],
            "guest": {"old": False, "new": True},
        },
        {
            "subnet": LAB,
            "vlan_id": None,
            "changed": ["vlan_id", "purpose", "dhcp_server", "notes"],
            "previous_vlan_id": 70,
        },
        {
            "subnet": LAB,
            "vlan_id": None,
            "changed": ["department_id"],
            "department_id": {"old": office, "new": None},
        },
        {
            "subnet": LAB,
            "vlan_id": 71,
            "changed": ["vlan_id", "dhcp_server", "department_id"],
            "previous_vlan_id": None,
            "department_id": {"old": None, "new": office},
        },
    ]
    for text in ("Laboratuvar", "Test bench", "dhcp7", "Kat 2"):
        assert text not in str(updates)


def test_a_corrected_subnet_keeps_the_vlan_and_moves_its_devices(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    office = _dept(factory, "Planlama")
    _devices(factory, (LAB_PC, "10.20.0.15"), (LAB_PC2, "10.20.1.15"))
    assert client.post(f"{API}/vlans", json={**FULL, "department_id": office}).status_code == 201
    assert _vlan_of(client, LAB_PC)[0] == "Laboratuvar"
    assert _vlan_of(client, LAB_PC2)[0] is None
    moved = client.patch(f"{API}/vlans", json={"subnet": LAB, "new_subnet": "10.20.1.0/24"})
    assert moved.status_code == 200, moved.text
    assert moved.json() == {
        **FULL,
        "subnet": "10.20.1.0/24",
        "department": {"id": office, "name": "Planlama"},
    }
    assert _vlan_of(client, LAB_PC) == (None, None, None)
    assert _vlan_of(client, LAB_PC2) == (
        "Laboratuvar",
        {"id": office, "name": "Planlama"},
        "vlan",
    )
    # The old subnet is free again; the new one names the VLAN now.
    assert client.patch(f"{API}/vlans", json={"subnet": LAB, "name_tr": "X"}).status_code == 404
    (entry,) = _audit(factory, "vlan.update")
    assert entry.target == "vlan:10.20.1.0/24"
    assert entry.details == {
        "subnet": "10.20.1.0/24",
        "vlan_id": 70,
        "changed": ["subnet"],
        "previous_subnet": LAB,
    }


def test_edit_refusals(client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]) -> None:
    assert client.post(f"{API}/vlans", json=FULL).status_code == 201
    conflicts = (
        ({"new_subnet": "172.20.43.0/24"}, "vlan_exists"),
        ({"vlan_id": 43}, "vlan_id_taken"),
        ({"dhcp_server": "dhcp43"}, "dhcp_server_taken"),
    )
    for change, code in conflicts:
        response = client.patch(f"{API}/vlans", json={"subnet": LAB, **change})
        assert response.status_code == 409, (change, response.text)
        assert _error(response)["code"] == code
    invalid: list[tuple[dict[str, Any], str]] = [
        ({"name_tr": None}, "name_tr"),
        ({"name_tr": " "}, "name_tr"),
        ({"guest": None}, "guest"),
        ({"new_subnet": None}, "new_subnet"),
        ({"new_subnet": "10.20.1.1/24"}, "new_subnet"),
        ({"new_subnet": "::ffff:10.20.0.0/120"}, "new_subnet"),
        ({"department_id": 999999}, "department_id"),
        ({"vlan_id": 5000}, "vlan_id"),
        ({"subnet": "10.20.0.0"}, "subnet"),
        ({"id": 1}, "id"),
    ]
    for change, field in invalid:
        response = client.patch(f"{API}/vlans", json={"subnet": LAB, **change})
        assert response.status_code == 422, (change, response.text)
        assert _error(response)["details"][0]["loc"][:2] == ["body", field], change
    missing = client.patch(f"{API}/vlans", json={"subnet": "10.99.0.0/24", "name_tr": "X"})
    assert (missing.status_code, _error(missing)["code"]) == (404, "not_found")
    assert client.patch(f"{API}/vlans", json={"name_tr": "X"}).status_code == 422
    assert client.get(f"{API}/vlans").json()[-2] == {**FULL, "department": None}
    assert _audit(factory, "vlan.update") == []


# -- delete ---------------------------------------------------------------------------------------


def test_delete_a_vlan(client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]) -> None:
    office = _dept(factory, "İdari İşler")
    wifi = "172.20.60.0/24"
    with factory() as session:
        add_device(session, NAS, last_ip="172.20.60.183", last_vlan=60)
        session.commit()
    client.patch(f"{API}/vlans", json={"subnet": wifi, "department_id": office})
    assert _vlan_of(client, NAS) == (
        "Wi-Fi Personel",
        {"id": office, "name": "İdari İşler"},
        "vlan",
    )
    response = client.delete(f"{API}/vlans", params={"subnet": wifi})
    assert response.status_code == 204, response.text
    assert response.content == b""
    assert wifi not in [v["subnet"] for v in client.get(f"{API}/vlans").json()]
    assert _vlan_of(client, NAS) == (None, None, None)
    departments = {d["name"]: d for d in client.get(f"{API}/departments").json()}
    assert departments["İdari İşler"]["vlan_ids"] == [43]
    (entry,) = _audit(factory, "vlan.delete")
    assert entry.target == f"vlan:{wifi}"
    assert entry.details == {
        "subnet": wifi,
        "vlan_id": 60,
        "department_id": office,
        "guest": False,
    }
    again = client.delete(f"{API}/vlans", params={"subnet": wifi})
    assert (again.status_code, _error(again)["code"]) == (404, "not_found")
    for params in ({"subnet": "172.20.60.1/24"}, {"subnet": "x"}, {}):
        bad = client.delete(f"{API}/vlans", params=params)
        assert bad.status_code == 422, params
        assert _error(bad)["details"][0]["loc"] == ["query", "subnet"]
    # A deleted VLAN can be entered again (its VLAN id and DHCP server are free).
    recreated = client.post(
        f"{API}/vlans",
        json={"subnet": wifi, "vlan_id": 60, "name_tr": "Wi-Fi Personel", "dhcp_server": "dhcp60"},
    )
    assert recreated.status_code == 201, recreated.text
    assert _vlan_of(client, NAS)[0] == "Wi-Fi Personel"


def test_a_stored_ipv4_mapped_network_can_be_corrected_or_deleted(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    """``POST`` and ``new_subnet`` refuse ``::ffff:10.20.0.0/120``; a row entered by SQL before
    is still named by it, so it can be moved to its IPv4 network or deleted."""
    mapped = "::ffff:10.20.0.0/120"
    _devices(factory, (LAB_PC, "10.20.0.15"))
    with factory() as session:
        session.add(Vlan(subnet=mapped, vlan_id=70, name_tr="Laboratuvar"))
        session.commit()
    assert _vlan_of(client, LAB_PC)[0] is None
    named = client.patch(f"{API}/vlans", json={"subnet": mapped, "notes": "SQL"})
    assert named.status_code == 200, named.text
    moved = client.patch(f"{API}/vlans", json={"subnet": mapped, "new_subnet": LAB})
    assert moved.status_code == 200, moved.text
    assert (moved.json()["subnet"], moved.json()["vlan_id"]) == (LAB, 70)
    assert _vlan_of(client, LAB_PC)[0] == "Laboratuvar"
    with factory() as session:
        session.add(Vlan(subnet="::ffff:10.21.0.0/120", name_tr="Eski"))
        session.commit()
    deleted = client.delete(f"{API}/vlans", params={"subnet": "::ffff:10.21.0.0/120"})
    assert deleted.status_code == 204, deleted.text


# -- nested subnets -------------------------------------------------------------------------------


def test_a_subnet_inside_another_is_allowed_and_the_most_specific_wins(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    _devices(factory, (LAB_PC, "172.20.60.37"), (LAB_PC2, "172.20.60.200"))
    nested = client.post(
        f"{API}/vlans", json={"subnet": "172.20.60.0/25", "vlan_id": 70, "name_tr": "Depo"}
    )
    assert nested.status_code == 201, nested.text
    assert _vlan_of(client, LAB_PC)[0] == "Depo"
    assert _vlan_of(client, LAB_PC2)[0] == "Wi-Fi Personel"
    # A network around existing ones is allowed too (it covers only what no other one does).
    wide = client.post(f"{API}/vlans", json={"subnet": "172.20.0.0/16", "name_tr": "Kampüs"})
    assert wide.status_code == 201, wide.text
    assert _vlan_of(client, LAB_PC)[0] == "Depo"
