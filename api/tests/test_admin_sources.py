"""The log sources through the browser (ADR-0014; contract 2.6 sources, 2.11.1; WP5-1).

A source added, changed, switched off and removed by the real agent with ApplySystem's real
plan (``FakeSystem``): the check of an addition and of a change (``operation``), the reloads and
the firewall rule each change leads to, the re-authentication of a switch-off and a removal,
the removal's typed confirmation, and the addresses the browser may not add (outside the
installer's networks, IPv6, taken). Only users with ``sources.manage`` change sources.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.testing import FakeAgent, Installed

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
Reauth = Callable[[LoggedIn], None]
V = "/api/v1"
URL = f"{V}/admin/config/sources"
FIREWALL = {
    "name": "fortigate",
    "type": "fortigate",
    "address": "172.31.255.1",
    "heartbeat_minutes": 15,
    "expected": True,
    "transport": "any",
    "enabled": True,
}


@pytest.fixture
def system_agent(installed: Installed) -> FakeAgent:
    """The agent with ApplySystem (a managed firewall)."""
    fake = FakeAgent(installed, system=True)
    fake.start()
    return fake


def etag(client: TestClient) -> str:
    tag: str = client.get(f"{V}/admin/config").json()["etags"]["sources"]
    return tag


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def sources(client: TestClient) -> dict[str, dict[str, Any]]:
    listed = client.get(f"{V}/admin/config").json()["sections"]["sources"]
    return {source["name"]: source for source in listed}


def settle(client: TestClient, agent: FakeAgent, accepted: dict[str, Any]) -> dict[str, Any]:
    agent.process()
    agent.process()  # ApplySystem runs after the agent wrote the file
    found: dict[str, Any] = client.get(accepted["status_url"]).json()
    return found


def test_a_source_is_added_reloaded_and_let_through_the_firewall(
    client: TestClient, login_as: Login, installed: Installed, system_agent: FakeAgent
) -> None:
    login_as(client, "admin")
    body = {"etag": etag(client), "source": FIREWALL}
    checked = client.post(f"{URL}/check", json={**body, "operation": "add"})
    assert checked.status_code == 200, checked.text
    assert set(checked.json()["effects"]) == {
        "reload.collector",
        "reload.ingest",
        "reload.api",
        "system.firewall",
    }
    assert checked.json()["changes"]["sources.fortigate"]["old"] is None
    assert checked.json()["reauth_required"] is False

    response = client.post(URL, json=body)
    assert response.status_code == 202, response.text
    done = settle(client, system_agent, response.json())
    assert done["state"] == "active", done
    steps = {step["step"]: step["state"] for step in done["steps"]}
    assert steps["system.firewall"] == "done"
    assert [source.name for source in installed.config().sources] == ["fortigate"]
    added = sources(client)["fortigate"]
    assert (added["address"], added["firewall"], added["last_seen"]) == (
        "172.31.255.1",
        "open",
        None,
    )


def test_a_change_is_checked_as_a_change_and_a_switch_off_needs_a_reauthentication(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    system_agent: FakeAgent,
) -> None:
    who = login_as(client, "admin")
    settle(
        client,
        system_agent,
        client.post(URL, json={"etag": etag(client), "source": FIREWALL}).json(),
    )

    changed = {**FIREWALL, "heartbeat_minutes": 30}
    body = {"etag": etag(client), "source": changed}
    as_addition = client.post(f"{URL}/check", json={**body, "operation": "add"})
    assert error(as_addition) == (409, "source_name_taken")
    as_change = client.post(f"{URL}/check", json={**body, "operation": "update"})
    assert as_change.status_code == 200, as_change.text
    assert as_change.json()["changes"]["sources.fortigate"]["new"]["heartbeat_minutes"] == 30
    edited = client.put(f"{URL}/fortigate", json=body)
    assert edited.status_code == 202, edited.text
    assert settle(client, system_agent, edited.json())["state"] == "active"
    assert installed.config().sources[0].heartbeat_minutes == 30

    off = {"etag": etag(client), "source": {**changed, "enabled": False}}
    checked = client.post(f"{URL}/check", json={**off, "operation": "update"}).json()
    assert checked["reauth_required"] is True
    assert [warning["code"] for warning in checked["warnings"]] == ["source_disable_gaps"]
    assert error(client.put(f"{URL}/fortigate", json=off)) == (403, "reauth_required")
    reauthenticate(who)
    response = client.put(f"{URL}/fortigate", json=off)
    assert response.status_code == 202, response.text
    assert settle(client, system_agent, response.json())["state"] == "active"
    assert sources(client)["fortigate"]["firewall"] == "closed"
    renamed = {"etag": etag(client), "source": {**changed, "name": "fortigate-2"}}
    assert error(client.put(f"{URL}/fortigate", json=renamed)) == (
        422,
        "source_rename_not_allowed",
    )
    assert error(client.put(f"{URL}/yok", json=renamed)) == (404, "source_not_found")


def test_a_removal_needs_the_typed_name_and_a_reauthentication(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    system_agent: FakeAgent,
) -> None:
    who = login_as(client, "admin")
    settle(
        client,
        system_agent,
        client.post(URL, json={"etag": etag(client), "source": FIREWALL}).json(),
    )
    wrong = client.delete(f"{URL}/fortigate", params={"etag": etag(client), "confirm": "forti"})
    assert error(wrong) == (422, "source_confirm_mismatch")
    params = {"etag": etag(client), "confirm": "fortigate"}
    assert error(client.delete(f"{URL}/fortigate", params=params)) == (403, "reauth_required")
    reauthenticate(who)
    response = client.delete(f"{URL}/fortigate", params=params)
    assert response.status_code == 202, response.text
    assert response.json()["changes"]["sources.fortigate"]["new"] is None
    assert settle(client, system_agent, response.json())["state"] == "active"
    assert installed.config().sources == ()
    assert sources(client) == {}


@pytest.mark.parametrize(
    ("address", "code", "status"),
    [
        ("203.0.113.7", "source_network_not_allowed", 422),
        ("10.0.0.0/8", "source_prefix_too_short", 422),
        ("127.0.0.1", "source_reserved_address", 422),
        ("172.20.10.5", "source_own_address", 422),
        ("fd00::1", "source_ipv6_unsupported", 422),
        ("bir adres", "source_invalid", 422),
    ],
)
def test_addresses_the_browser_may_not_add(
    client: TestClient,
    login_as: Login,
    system_agent: FakeAgent,
    address: str,
    code: str,
    status: int,
) -> None:
    login_as(client, "admin")
    body = {"etag": etag(client), "source": {**FIREWALL, "address": address}}
    response = client.post(URL, json=body)
    assert error(response) == (status, code)
    (problem,) = response.json()["error"]["details"]
    assert problem["field"] in ("sources.fortigate.address", "sources")
    assert problem["message_tr"]
    assert problem["message_en"]
    assert address not in response.text


def test_a_taken_address_is_a_conflict(
    client: TestClient, login_as: Login, system_agent: FakeAgent
) -> None:
    login_as(client, "admin")
    settle(
        client,
        system_agent,
        client.post(URL, json={"etag": etag(client), "source": FIREWALL}).json(),
    )
    twin = {**FIREWALL, "name": "fortigate-yedek"}
    response = client.post(URL, json={"etag": etag(client), "source": twin})
    assert error(response) == (409, "source_address_taken")


def test_only_users_who_edit_sources_change_them(
    client: TestClient, login_as: Login, system_agent: FakeAgent
) -> None:
    login_as(client, "auditor")
    assert error(client.post(URL, json={"etag": "sources:0", "source": FIREWALL})) == (
        403,
        "forbidden",
    )
    assert error(client.delete(f"{URL}/fortigate", params={"etag": "x"})) == (403, "forbidden")
