"""The database settings through the browser (ADR-0014; contract 2.13): "Uygulama ayarları".

Every visible setting with its label, help, bounds and flags; ``PATCH /settings`` all or nothing
with its audit entry (the user's id and address), conflicts, refusals in both languages without
the submitted value, the re-authentication of ``REAUTH_KEYS`` and the console-only keys; the
reset to the shipped default.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.security import hash_token
from arcilogger_api.testing import LoggedIn
from arcilogger_db.models import AuditLog, UserSession
from arcilogger_db.settings import DEFAULT_SETTINGS, get_setting
from arcilogger_db.settings_meta import GROUPS, SETTINGS

Login = Callable[..., LoggedIn]
URL = "/api/v1/settings"


def recent(factory: sessionmaker[Session], who: LoggedIn) -> None:
    with factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(who.session_token))
            .values(reauthenticated_at=datetime.now(UTC))
        )
        session.commit()


def changes(factory: sessionmaker[Session]) -> list[AuditLog]:
    with factory() as session:
        return list(
            session.scalars(
                select(AuditLog).where(AuditLog.action == "settings.change").order_by(AuditLog.id)
            )
        )


def value(factory: sessionmaker[Session], key: str) -> Any:
    with factory() as session:
        return get_setting(session, key)


def test_every_visible_setting_is_listed_with_its_texts(
    client: TestClient, login_as: Login
) -> None:
    login_as(client, "admin")
    response = client.get(URL)
    assert response.status_code == 200, response.text
    groups = response.json()["groups"]
    assert [group["id"] for group in groups] == [group.id for group in GROUPS]
    listed = {item["key"]: item for group in groups for item in group["settings"]}
    assert set(listed) == set(SETTINGS)
    tolerance = listed["lookup.tolerance_seconds"]
    assert tolerance["value"] == DEFAULT_SETTINGS["lookup.tolerance_seconds"]
    assert (tolerance["type"], tolerance["min"], tolerance["max"]) == ("integer", 0, 3600)
    assert tolerance["is_default"] is True
    assert tolerance["updated_by"] is None
    assert listed["auth.idle_minutes"]["reauth"] is True
    assert listed["devices.oui_file"]["locked"] is True
    for item in listed.values():
        assert all(item[name] for name in ("label_tr", "label_en", "help_tr", "help_en"))


def test_a_change_is_all_or_nothing_and_audited(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    who = login_as(client, "admin")
    body = {
        "changes": [
            {"key": "lookup.tolerance_seconds", "old": 60, "value": 90},
            {"key": "lookup.max_candidates", "old": 100, "value": -5},
        ]
    }
    refused = client.patch(URL, json=body)
    assert refused.status_code == 422
    error = refused.json()["error"]
    assert error["code"] == "setting_invalid"
    assert error["message_en"] == "Some setting values are invalid; correct the marked settings."
    (problem,) = error["details"]
    assert problem["key"] == "lookup.max_candidates"
    assert problem["code"] == "setting_out_of_range"
    assert problem["message_tr"] == "1 ile 10000 arasında olmalı."
    assert "-5" not in refused.text
    assert value(app_session_factory, "lookup.tolerance_seconds") == 60  # nothing was written

    body["changes"][1]["value"] = 100
    done = client.patch(URL, json=body)
    assert done.status_code == 200, done.text
    assert done.json() == {
        "changed": [{"key": "lookup.tolerance_seconds", "old": 60, "new": 90}],
        "unchanged": ["lookup.max_candidates"],
    }
    assert value(app_session_factory, "lookup.tolerance_seconds") == 90
    (entry,) = changes(app_session_factory)
    assert (entry.actor_id, entry.actor_username) == (who.user_id, who.username)
    assert entry.target == "setting:lookup.tolerance_seconds"
    assert entry.details == {"old": 60, "new": 90}
    listed = {
        item["key"]: item
        for group in client.get(URL).json()["groups"]
        for item in group["settings"]
    }
    assert listed["lookup.tolerance_seconds"]["updated_by"] == who.username


def test_a_value_changed_meanwhile_is_a_conflict(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "admin")
    response = client.patch(
        URL, json={"changes": [{"key": "lookup.tolerance_seconds", "old": 30, "value": 90}]}
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "setting_conflict"
    assert error["details"] == [{"key": "lookup.tolerance_seconds", "current": 60}]
    assert not changes(app_session_factory)


def test_unknown_locked_and_sensitive_keys(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    who = login_as(client, "admin")
    unknown = client.patch(URL, json={"changes": [{"key": "no.such", "old": 1, "value": 2}]})
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (422, "setting_unknown")
    assert "no.such" not in unknown.text
    locked = client.patch(
        URL, json={"changes": [{"key": "devices.oui_file", "old": "", "value": "C:/x.csv"}]}
    )
    assert (locked.status_code, locked.json()["error"]["code"]) == (422, "setting_locked")
    twice = client.patch(
        URL,
        json={
            "changes": [
                {"key": "lookup.tolerance_seconds", "old": 60, "value": 61},
                {"key": "lookup.tolerance_seconds", "old": 60, "value": 62},
            ]
        },
    )
    assert (twice.status_code, twice.json()["error"]["code"]) == (422, "validation_error")
    idle = {"changes": [{"key": "auth.idle_minutes", "old": 15, "value": 20}]}
    needs = client.patch(URL, json=idle)
    assert (needs.status_code, needs.json()["error"]["code"]) == (403, "reauth_required")
    recent(app_session_factory, who)
    assert client.patch(URL, json=idle).status_code == 200
    assert value(app_session_factory, "auth.idle_minutes") == 20


def test_reset_puts_the_shipped_default_back(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    who = login_as(client, "admin")
    client.patch(
        URL, json={"changes": [{"key": "lookup.tolerance_seconds", "old": 60, "value": 90}]}
    )
    reset = client.post(f"{URL}/lookup.tolerance_seconds/reset")
    assert reset.status_code == 200, reset.text
    assert reset.json()["changed"] == [{"key": "lookup.tolerance_seconds", "old": 90, "new": 60}]
    assert changes(app_session_factory)[-1].details == {"reset": True, "old": 90, "new": 60}
    again = client.post(f"{URL}/lookup.tolerance_seconds/reset")
    assert again.json() == {"changed": [], "unchanged": ["lookup.tolerance_seconds"]}
    sensitive = client.post(f"{URL}/auth.idle_minutes/reset")
    assert (sensitive.status_code, sensitive.json()["error"]["code"]) == (403, "reauth_required")
    recent(app_session_factory, who)
    assert client.post(f"{URL}/auth.idle_minutes/reset").status_code == 200
    unknown = client.post(f"{URL}/no.such/reset")
    assert (unknown.status_code, unknown.json()["error"]["code"]) == (422, "setting_unknown")


def test_only_administrators_change_settings(client: TestClient, login_as: Login) -> None:
    login_as(client, "auditor")
    assert client.get(URL).status_code == 403
    response = client.patch(
        URL, json={"changes": [{"key": "lookup.tolerance_seconds", "old": 60, "value": 90}]}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden")
    assert client.post(f"{URL}/lookup.tolerance_seconds/reset").status_code == 403


def test_the_fortigate_and_nat_settings_are_accepted_and_refused(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    """ADR-0016: the FortiGate DHCP, srcmac and NAT keys are listed in the lookup group, take
    their normalised values without a re-authentication and are refused with their codes."""
    login_as(client, "admin")
    listed = {
        item["key"]: item
        for group in client.get(URL).json()["groups"]
        for item in group["settings"]
        if group["id"] == "lookup"
    }
    assert listed["dhcp.fortigate_logids"]["type"] == "object"
    assert listed["dhcp.fortigate_logids"]["value"] == {
        "0100026001": "assigned",
        "0100026002": "deassigned",
    }
    assert listed["nat.egresses"]["type"] == "object"
    assert listed["nat.snat_trandisp"]["type"] == "string_list"
    assert listed["lookup.srcmac_interfaces"]["type"] == "string_list"
    max_ips = listed["lookup.srcmac_max_ips"]
    assert (max_ips["type"], max_ips["min"], max_ips["max"]) == ("integer", 1, 16)
    new_keys = [key for key in listed if key.startswith(("dhcp.fortigate_", "lookup.srcmac_"))]
    new_keys += ["nat.egresses", "nat.snat_trandisp"]
    assert len(new_keys) == 10
    assert not any(listed[key]["reauth"] or listed[key]["locked"] for key in new_keys)

    refused = client.patch(
        URL,
        json={
            "changes": [
                {"key": "dhcp.fortigate_logids", "old": None, "value": {"26001": "assigned"}},
                {"key": "dhcp.fortigate_dhcp_msgs", "old": None, "value": {"0100026001": []}},
                {"key": "dhcp.fortigate_interfaces_excluded", "old": [], "value": ["ipsec "]},
                {
                    "key": "lookup.srcmac_interfaces",
                    "old": [],
                    "value": [f"v{n}" for n in range(40)],
                },
                {"key": "lookup.srcmac_max_ips", "old": 1, "value": 17},
                {"key": "nat.snat_trandisp", "old": ["snat"], "value": ["SNAT"]},
                {"key": "nat.egresses", "old": {}, "value": ["hat-1"]},
                {"key": "dhcp.fortigate_format_verified", "old": False, "value": "yes"},
            ]
        },
    )
    assert refused.status_code == 422
    codes = {problem["key"]: problem["code"] for problem in refused.json()["error"]["details"]}
    assert codes == {
        "dhcp.fortigate_logids": "setting_logid_invalid",
        "dhcp.fortigate_dhcp_msgs": "setting_dhcp_msg_invalid",
        "dhcp.fortigate_interfaces_excluded": "setting_interface_invalid",
        "lookup.srcmac_interfaces": "setting_too_many",
        "lookup.srcmac_max_ips": "setting_out_of_range",
        "nat.snat_trandisp": "setting_trandisp_invalid",
        "nat.egresses": "setting_type",
        "dhcp.fortigate_format_verified": "setting_type",
    }
    assert "ipsec " not in refused.text
    assert "SNAT" not in refused.text
    assert not changes(app_session_factory)

    defaults = {
        key: DEFAULT_SETTINGS[key]
        for key in (
            "dhcp.fortigate_dhcp_msgs",
            "dhcp.fortigate_format_verified",
            "lookup.srcmac_interfaces",
            "lookup.srcmac_max_ips",
            "nat.snat_trandisp",
        )
    }
    accepted = client.patch(
        URL,
        json={
            "changes": [
                {
                    "key": "dhcp.fortigate_dhcp_msgs",
                    "old": defaults["dhcp.fortigate_dhcp_msgs"],
                    "value": {"0100026001": ["Ack", "ACK", "Inform"]},
                },
                {"key": "dhcp.fortigate_format_verified", "old": False, "value": True},
                {"key": "lookup.srcmac_interfaces", "old": [], "value": ["vlan42", "vlan42"]},
                {"key": "lookup.srcmac_max_ips", "old": 1, "value": 2},
                {"key": "nat.snat_trandisp", "old": ["snat"], "value": ["snat", "dnat+snat"]},
            ]
        },
    )
    assert accepted.status_code == 200, accepted.text
    stored = {"0100026001": ["Ack", "Inform"]}  # repeats dropped case-insensitively
    assert value(app_session_factory, "dhcp.fortigate_dhcp_msgs") == stored
    assert value(app_session_factory, "dhcp.fortigate_format_verified") is True
    assert value(app_session_factory, "lookup.srcmac_interfaces") == ["vlan42"]
    assert value(app_session_factory, "lookup.srcmac_max_ips") == 2
    assert value(app_session_factory, "nat.snat_trandisp") == ["snat", "dnat+snat"]
    assert sorted(str(entry.target) for entry in changes(app_session_factory)) == [
        f"setting:{key}" for key in sorted(defaults)
    ]
