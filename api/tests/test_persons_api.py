"""Persons, departments and VLANs API (spec 9.2): validation, uniqueness, Turkish search, detail
with devices and history, deactivation, CSV export and the audit entries (never names)."""

from __future__ import annotations

import csv
import io
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.directory_testing import add_device, add_person, department_id
from arcilogger_api.lookup_testing import accept_privacy
from arcilogger_api.testing import LoggedIn
from arcilogger_db.models import AuditLog, Department, Person, Vlan

Login = Callable[..., LoggedIn]
API = "/api/v1"
PC, PHONE = "f0:1f:af:00:00:01", "02:00:5e:00:53:46"


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


def _create(client: TestClient, **body: Any) -> Any:
    return client.post(f"{API}/persons", json=body)


# -- persons --------------------------------------------------------------------------------------


def test_create_and_read_a_person(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    finance = _dept(factory, "Finans")
    response = _create(
        client,
        name="  Şükrü Çağlayan ",
        department_id=finance,
        title="Muhasebe Uzmanı",
        email="sukru@example.com",
        employee_no="2001",
        notes="İşe giriş: 2024\nOda 3",
    )
    assert response.status_code == 201, response.text
    person = response.json()
    assert person["name"] == "Şükrü Çağlayan"
    assert person["department"] == {"id": finance, "name": "Finans"}
    assert (person["active"], person["device_count"]) == (True, 0)
    assert set(person) == {
        "id",
        "name",
        "department",
        "title",
        "email",
        "employee_no",
        "active",
        "notes",
        "usage_excluded",
        "device_count",
        "created_at",
        "updated_at",
    }
    assert person["usage_excluded"] is False  # in the usage reports unless excluded
    (entry,) = _audit(factory, "person.create")
    assert entry.target == f"person:{person['id']}"
    assert entry.details == {
        "person_id": person["id"],
        "department_id": finance,
        "active": True,
        "usage_excluded": False,
        "fields": [
            "name",
            "department_id",
            "title",
            "email",
            "employee_no",
            "active",
            "notes",
            "usage_excluded",
        ],
    }
    assert "Şükrü" not in str(entry.details)
    assert "example.com" not in str(entry.details)
    # Empty optional fields are "no value".
    blank = _create(client, name="Boş Alanlar", title="", email=" ", employee_no="", notes="")
    assert blank.status_code == 201, blank.text
    assert (blank.json()["title"], blank.json()["email"], blank.json()["employee_no"]) == (
        None,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("body", "status", "code"),
    [
        ({"name": ""}, 422, "validation_error"),
        ({"name": "A", "email": "not-an-address"}, 422, "validation_error"),
        ({"name": "A", "department_id": 999999}, 422, "validation_error"),
        ({"name": "A\u0000"}, 422, "validation_error"),
        ({"name": "A", "employee_no": "x" * 65}, 422, "validation_error"),
        ({"name": "A", "unknown": 1}, 422, "validation_error"),
        ({"name": "A", "employee_no": "1001"}, 409, "employee_no_taken"),
    ],
)
def test_person_validation(
    client: TestClient,
    admin: LoggedIn,
    factory: sessionmaker[Session],
    body: dict[str, Any],
    status: int,
    code: str,
) -> None:
    with factory() as session:
        add_person(session, "Var Olan", employee_no="1001")
        session.commit()
    response = _create(client, **body)
    assert response.status_code == status, response.text
    assert response.json()["error"]["code"] == code


def test_update_and_deactivate_keep_the_history(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    with factory() as session:
        person = add_person(session, "Ayşe Işık", department="Finans", employee_no="1001")
        other = add_person(session, "Başka Biri", employee_no="1002")
        add_device(session, PC, last_ip="172.20.41.21", last_vlan=41)
        add_device(session, PHONE, last_ip="172.20.60.183", last_vlan=60, randomized=True)
        session.commit()
        person_id, other_id = person.id, other.id
    assert (
        client.post(
            f"{API}/devices/assign", json={"macs": [PC, PHONE], "person_id": person_id}
        ).status_code
        == 200
    )
    planning = _dept(factory, "Planlama")
    response = client.patch(
        f"{API}/persons/{person_id}",
        json={"department_id": planning, "title": "Şef", "email": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["department"]["name"] == "Planlama"
    assert response.json()["device_count"] == 2
    (entry,) = _audit(factory, "person.update")
    assert entry.details == {
        "changed": ["department_id", "title"],
        "department_id": {"old": _dept(factory, "Finans"), "new": planning},
    }
    # The device's effective department follows the owner's current department.
    device = client.get(f"{API}/devices/{PC}").json()
    assert (device["department"]["name"], device["department_source"]) == ("Planlama", "person")
    # Deactivating keeps devices and history; no new devices for an inactive person.
    inactive = client.patch(f"{API}/persons/{person_id}", json={"active": False})
    assert inactive.json()["active"] is False
    assert _audit(factory, "person.update")[-1].details["active"] == {"old": True, "new": False}
    detail = client.get(f"{API}/persons/{person_id}").json()
    assert {d["mac"] for d in detail["devices"]} == {PC, PHONE}
    assert {a["mac"] for a in detail["assignments"]} == {PC, PHONE}
    assert all(a["valid_to"] is None for a in detail["assignments"])
    refused = client.post(f"{API}/devices/assign", json={"macs": [PC], "person_id": person_id})
    assert refused.status_code == 409  # checked before anything else, also for the owner
    assert refused.json()["error"]["code"] == "person_inactive"
    other_pc = client.post(f"{API}/devices/assign", json={"macs": [PC], "person_id": other_id})
    assert other_pc.status_code == 200
    again = client.post(f"{API}/devices/assign", json={"macs": [PC], "person_id": person_id})
    assert again.status_code == 409
    assert again.json()["error"]["code"] == "person_inactive"
    # Unchanged values write no entry; null name/active are refused; employee_no stays unique.
    client.patch(f"{API}/persons/{person_id}", json={"title": "Şef"})
    assert len(_audit(factory, "person.update")) == 2
    for body, code in (
        ({"name": None}, "validation_error"),
        ({"active": None}, "validation_error"),
        ({"employee_no": "1002"}, "employee_no_taken"),
        ({"department_id": 999999}, "validation_error"),
    ):
        bad = client.patch(f"{API}/persons/{person_id}", json=body)
        assert bad.json()["error"]["code"] == code, body
    assert client.get(f"{API}/persons/999999").status_code == 404
    assert client.patch(f"{API}/persons/999999", json={}).status_code == 404
    assert client.get(f"{API}/persons/0").status_code == 422


def test_usage_exclusion_is_set_audited_and_exported(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    """``usage_excluded`` (area U) is set like any other person field: on create or edit, the
    old and new value in the audit entry, in every answer and in the export (evet/hayır)."""
    created = _create(client, name="Gizli Kişi", employee_no="4001", usage_excluded=True)
    assert created.status_code == 201, created.text
    person = created.json()
    assert person["usage_excluded"] is True
    (entry,) = _audit(factory, "person.create")
    assert entry.details["usage_excluded"] is True
    plain = _create(client, name="Görünen Kişi").json()
    assert plain["usage_excluded"] is False
    page = client.get(f"{API}/persons").json()
    assert [(p["name"], p["usage_excluded"]) for p in page["items"]] == [
        ("Gizli Kişi", True),
        ("Görünen Kişi", False),
    ]
    assert client.get(f"{API}/persons/{person['id']}").json()["usage_excluded"] is True
    response = client.patch(f"{API}/persons/{person['id']}", json={"usage_excluded": False})
    assert response.status_code == 200, response.text
    assert response.json()["usage_excluded"] is False
    client.patch(f"{API}/persons/{person['id']}", json={"usage_excluded": False})  # no change
    both = {"usage_excluded": True, "title": "Uzman"}
    assert client.patch(f"{API}/persons/{plain['id']}", json=both).status_code == 200
    assert [e.details for e in _audit(factory, "person.update")] == [
        {"changed": ["usage_excluded"], "usage_excluded": {"old": True, "new": False}},
        {"changed": ["title", "usage_excluded"], "usage_excluded": {"old": False, "new": True}},
    ]
    for value in (None, "belki"):
        refused = client.patch(f"{API}/persons/{plain['id']}", json={"usage_excluded": value})
        assert refused.status_code == 422, value
        assert refused.json()["error"]["code"] == "validation_error"
    text = client.get(f"{API}/persons/export.csv").content.decode("utf-8").removeprefix("\ufeff")
    rows = list(csv.DictReader(io.StringIO(text), delimiter=";"))
    assert list(rows[0])[6:9] == ["active", "notes", "usage_excluded"]
    assert [(r["name"], r["usage_excluded"]) for r in rows] == [
        ("Gizli Kişi", "hayır"),
        ("Görünen Kişi", "evet"),
    ]


def test_person_list_search_filters_and_pages(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    with factory() as session:
        add_person(session, "Işıl Ilgaz", department="İdari İşler", email="isil@example.com")
        add_person(session, "İsmail Şahin", department="Planlama", employee_no="İ-77")
        add_person(session, "Ömer Çelik", department="Planlama", active=False, title="Usta")
        add_person(session, "Zeynep Ak")
        session.commit()

    def names(**params: Any) -> list[str]:
        page = client.get(f"{API}/persons", params=params).json()
        return [item["name"] for item in page["items"]]

    assert names(q="ışıl") == ["Işıl Ilgaz"]
    assert names(q="ISIL") == ["Işıl Ilgaz"]  # I, ı and i fold together
    assert names(q="ismail") == ["İsmail Şahin"]
    assert names(q="ŞAHİN") == ["İsmail Şahin"]
    assert names(q="example.com") == ["Işıl Ilgaz"]
    assert names(q="i-77") == ["İsmail Şahin"]
    assert names(q="usta") == ["Ömer Çelik"]
    assert names(active=False) == ["Ömer Çelik"]
    planning = _dept(factory, "Planlama")
    assert sorted(names(department_id=planning)) == ["Ömer Çelik", "İsmail Şahin"]
    page = client.get(f"{API}/persons", params={"limit": 2, "offset": 1}).json()
    assert (page["total"], page["limit"], page["offset"], len(page["items"])) == (4, 2, 1, 2)
    assert client.get(f"{API}/persons", params={"limit": 501}).status_code == 422


def test_person_export(client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]) -> None:
    with factory() as session:
        add_person(session, "Gülşen Ünlü", department="Finans", employee_no="3001")
        add_person(session, '=HYPERLINK("x")', employee_no="3002")
        session.commit()
    response = client.get(f"{API}/persons/export.csv", params={"q": "ünlü"})
    assert response.status_code == 200
    text = response.content.decode("utf-8")
    assert text.startswith("﻿")
    rows = list(csv.DictReader(io.StringIO(text.removeprefix("﻿")), delimiter=";"))
    assert [(r["name"], r["department"], r["active"]) for r in rows] == [
        ("Gülşen Ünlü", "Finans", "evet")
    ]
    every = client.get(f"{API}/persons/export.csv").content.decode("utf-8")
    assert "'=HYPERLINK" in every
    entries = _audit(factory, "person.export")
    assert [e.details for e in entries] == [
        {"rows": 1, "filters": {"q": True}},
        {"rows": 2, "filters": {}},
    ]


# -- departments ----------------------------------------------------------------------------------


def test_departments(client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]) -> None:
    with factory() as session:
        add_person(session, "Bir", department="İdari İşler")
        add_person(session, "İki", department="İdari İşler")
        session.commit()
    listed = {d["name"]: d for d in client.get(f"{API}/departments").json()}
    assert listed["İdari İşler"]["person_count"] == 2
    assert listed["İdari İşler"]["vlan_ids"] == [43]
    assert listed["Planlama"]["vlan_ids"] == [42]
    created = client.post(f"{API}/departments", json={"name": "Ar-Ge", "notes": "Yeni"})
    assert created.status_code == 201, created.text
    new = created.json()
    assert new == {
        "id": new["id"],
        "name": "Ar-Ge",
        "notes": "Yeni",
        "person_count": 0,
        "vlan_ids": [],
    }
    duplicate = client.post(f"{API}/departments", json={"name": "İDARİ İŞLER"})
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "department_exists"
    renamed = client.patch(f"{API}/departments/{new['id']}", json={"name": "Araştırma"})
    assert renamed.json()["name"] == "Araştırma"
    assert (
        client.patch(f"{API}/departments/{new['id']}", json={"name": "idari işler"}).status_code
        == 409
    )
    assert client.patch(f"{API}/departments/{new['id']}", json={"name": None}).status_code == 422
    assert client.patch(f"{API}/departments/999999", json={"name": "X"}).status_code == 404
    client.patch(f"{API}/departments/{new['id']}", json={"notes": None})
    client.patch(f"{API}/departments/{new['id']}", json={"name": "Araştırma"})  # no change
    both = {"name": "Ayşe Kaya ekibi", "notes": "Kat 2"}
    assert client.patch(f"{API}/departments/{new['id']}", json=both).status_code == 200
    bare = client.post(f"{API}/departments", json={"name": "Depo"}).json()
    # The sealed audit log gets ids and field names, never a department's name or notes.
    creations = [e.details for e in _audit(factory, "department.create")]
    assert creations == [
        {"department_id": new["id"], "fields": ["name", "notes"]},
        {"department_id": bare["id"], "fields": ["name"]},
    ]
    updates = [e.details for e in _audit(factory, "department.update")]
    assert updates == [
        {"department_id": new["id"], "changed": ["name"]},
        {"department_id": new["id"], "changed": ["notes"]},
        {"department_id": new["id"], "changed": ["name", "notes"]},
    ]
    for text in ("Ar-Ge", "Araştırma", "Ayşe", "Kat 2", "Yeni", "Depo"):
        assert text not in str(creations + updates)


# -- VLANs ----------------------------------------------------------------------------------------


def test_vlans_and_their_department(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    vlans = client.get(f"{API}/vlans").json()
    assert len(vlans) == 8
    assert vlans[0]["vlan_id"] == 10
    assert vlans[-1]["vlan_id"] is None  # the transit network has no id: last
    wifi = next(v for v in vlans if v["vlan_id"] == 60)
    assert (wifi["department"], wifi["guest"]) == (None, False)
    with factory() as session:
        add_device(session, PHONE, last_ip="172.20.60.183", last_vlan=60)
        session.commit()
    assert client.get(f"{API}/devices/{PHONE}").json()["department"] is None
    office = _dept(factory, "İdari İşler")
    response = client.patch(
        f"{API}/vlans", json={"subnet": "172.20.60.0/24", "department_id": office}
    )
    assert response.status_code == 200, response.text
    assert response.json()["department"] == {"id": office, "name": "İdari İşler"}
    device = client.get(f"{API}/devices/{PHONE}").json()
    assert (device["department"]["name"], device["department_source"]) == ("İdari İşler", "vlan")
    (entry,) = _audit(factory, "vlan.update")
    assert entry.target == "vlan:172.20.60.0/24"
    assert entry.details == {
        "subnet": "172.20.60.0/24",
        "vlan_id": 60,
        "changed": ["department_id"],
        "department_id": {"old": None, "new": office},
    }
    # The same value again writes nothing; null unlinks.
    client.patch(f"{API}/vlans", json={"subnet": "172.20.60.0/24", "department_id": office})
    assert len(_audit(factory, "vlan.update")) == 1
    unlinked = client.patch(
        f"{API}/vlans", json={"subnet": "172.20.60.0/24", "department_id": None}
    )
    assert unlinked.json()["department"] is None
    with factory() as session:
        assert session.get_one(Vlan, "172.20.60.0/24").department_id is None
    for body, status in (
        ({"subnet": "172.20.70.0/24", "department_id": None}, 404),
        ({"subnet": "172.20.60.8/24", "department_id": None}, 422),  # host bits set
        ({"subnet": "nope", "department_id": None}, 422),
        ({"subnet": "172.20.60.0/24", "department_id": 999999}, 422),
        ({"subnet": "172.20.60.0/24"}, 200),  # nothing sent, nothing changed
    ):
        assert client.patch(f"{API}/vlans", json=body).status_code == status, body
    assert client.get(f"{API}/devices/{PHONE}").json()["department"] is None
    assert len(_audit(factory, "vlan.update")) == 2


def test_a_new_installation_has_no_network_and_still_works(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    """ARCILOGGER ships no VLANs or departments (arcilogger_db.seed): empty lists, devices
    without a VLAN, and the administrator enters the departments and the VLANs."""
    with factory() as session:
        session.execute(delete(Vlan))
        session.execute(delete(Department))
        add_device(session, PHONE, last_ip="172.20.60.183", last_vlan=60)
        session.commit()
    assert client.get(f"{API}/vlans").json() == []
    assert client.get(f"{API}/departments").json() == []
    device = client.get(f"{API}/devices/{PHONE}").json()
    assert (device["vlan_name"], device["department"], device["department_source"]) == (
        None,
        None,
        None,
    )
    created = client.post(f"{API}/departments", json={"name": "Muhasebe"})
    assert created.status_code == 201, created.text
    assert created.json()["vlan_ids"] == []
    person = _create(client, name="İlk Kişi", department_id=created.json()["id"])
    assert person.status_code == 201, person.text
    response = client.patch(
        f"{API}/vlans", json={"subnet": "172.20.60.0/24", "department_id": None}
    )
    assert response.status_code == 404
    accounting = created.json()["id"]
    vlan = client.post(
        f"{API}/vlans",
        json={
            "subnet": "172.20.60.0/24",
            "vlan_id": 60,
            "name_tr": "Muhasebe Wi-Fi",
            "department_id": accounting,
        },
    )
    assert vlan.status_code == 201, vlan.text
    device = client.get(f"{API}/devices/{PHONE}").json()
    assert (device["vlan_name"], device["department"], device["department_source"]) == (
        "Muhasebe Wi-Fi",
        {"id": accounting, "name": "Muhasebe"},
        "vlan",
    )
    assert client.get(f"{API}/departments").json()[0]["vlan_ids"] == [60]


def test_persons_are_never_deleted(client: TestClient, admin: LoggedIn) -> None:
    created = _create(client, name="Kalıcı").json()
    assert client.delete(f"{API}/persons/{created['id']}").status_code == 405


def test_person_detail_lists_devices_and_history(
    client: TestClient, admin: LoggedIn, factory: sessionmaker[Session]
) -> None:
    with factory() as session:
        person = add_person(session, "Tek Kişi")
        add_device(session, PC)
        session.commit()
        person_id = person.id
    client.post(f"{API}/devices/assign", json={"macs": [PC], "person_id": person_id})
    client.post(f"{API}/devices/assign", json={"macs": [PC], "person_id": None})
    detail = client.get(f"{API}/persons/{person_id}").json()
    assert detail["devices"] == []
    assert detail["device_count"] == 0
    (assignment,) = detail["assignments"]
    assert assignment["mac"] == PC
    assert assignment["valid_to"] is not None
    assert assignment["person"] == {"id": person_id, "name": "Tek Kişi"}
    with factory() as session:
        assert session.get_one(Person, person_id).active
