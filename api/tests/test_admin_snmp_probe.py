"""The ``snmp`` probe's interface list in a request's answer (W5-N-9, #224).

A source switched on for SNMP with an interface the device does not have: the agent's probe
refuses it, and ``GET /admin/requests/{id}`` gives what the device reports in
``error.interfaces``, so the browser can offer those names. A refusal without a list (no
answer) gives none.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.contract import ProbeInterface
from arcilogger_core.admin.probe import ProbeResult
from arcilogger_core.admin.testing import FakeAgent

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
Reauth = Callable[[LoggedIn], None]
V = "/api/v1"
URL = f"{V}/admin/config/sources"
SNMP = {"enabled": True, "user": "arcilogger", "interfaces": ["wan1"]}
DEVICE = {"name": "fw-sube", "type": "fortigate", "address": "172.31.255.2", "snmp": SNMP}
REPORTED = (
    ProbeInterface(name="wan2", oper_status="up", speed_mbps=1000),
    ProbeInterface(name="lan", oper_status="down", speed_mbps=None),
)


def refused(
    client: TestClient, agent: FakeAgent, who: LoggedIn, reauth: Reauth, failure: ProbeResult
) -> dict[str, Any]:
    agent.prober.results["snmp"] = failure
    reauth(who)
    tag = client.get(f"{V}/admin/config").json()["etags"]["sources"]
    keys = {"snmp_auth_key": "Örnek-SNMP-Kimlik-2", "snmp_priv_key": "Örnek-SNMP-Gizlilik-2"}
    accepted = client.post(URL, json={"etag": tag, "source": DEVICE, **keys})
    assert accepted.status_code == 202, accepted.text
    agent.process()
    found: dict[str, Any] = client.get(accepted.json()["status_url"]).json()
    assert found["state"] == "probe_failed", found
    return found


@pytest.mark.parametrize("code", ["snmp_no_such_interface", "snmp_no_hc_counters"])
def test_the_answer_lists_what_the_device_reports(
    client: TestClient, login_as: Login, reauthenticate: Reauth, agent: FakeAgent, code: str
) -> None:
    who = login_as(client, "admin")
    found = refused(
        client, agent, who, reauthenticate, ProbeResult(False, code, "not found", REPORTED)
    )
    assert found["error"]["code"] == code
    assert found["error"]["interfaces"] == [
        {"name": "wan2", "oper_status": "up", "speed_mbps": 1000},
        {"name": "lan", "oper_status": "down", "speed_mbps": None},
    ]
    assert found["error"]["message_tr"]
    assert found["error"]["message_en"]
    # only the trial e-mail carries the server's answer and its explanation
    assert (found["error"]["detail"], found["error"]["hint_tr"]) == (None, None)
    (probe,) = [step for step in found["steps"] if step["step"] == "probe"]
    assert (probe["state"], probe["code"]) == ("failed", code)  # the step stays a code
    assert "fw-sube" not in {source["name"] for source in sources(client)}


def test_no_answer_has_no_list(
    client: TestClient, login_as: Login, reauthenticate: Reauth, agent: FakeAgent
) -> None:
    who = login_as(client, "admin")
    failure = ProbeResult(False, "snmp_timeout", "no answer", REPORTED)
    found = refused(client, agent, who, reauthenticate, failure)
    assert (found["error"]["code"], found["error"]["interfaces"]) == ("snmp_timeout", [])


def sources(client: TestClient) -> list[dict[str, Any]]:
    listed: list[dict[str, Any]] = client.get(f"{V}/admin/config").json()["sections"]["sources"]
    return listed


# -- "Bağlantıyı dene": POST /admin/config/sources/snmp-test (Wave 6 C11, #217) -------------------

TEST_URL = f"{URL}/snmp-test"
KEYS = {"snmp_auth_key": "Örnek-SNMP-Kimlik-3", "snmp_priv_key": "Örnek-SNMP-Gizlilik-3"}
UNCHOSEN = {**DEVICE, "snmp": {"enabled": True, "user": "arcilogger", "interfaces": []}}


def try_it(client: TestClient, **extra: Any) -> Any:
    tag = client.get(f"{V}/admin/config").json()["etags"]["sources"]
    body = {"etag": tag, "operation": "add", "source": UNCHOSEN, **KEYS, **extra}
    return client.post(TEST_URL, json=body)


def test_the_test_lists_the_interfaces_before_anything_is_saved(
    client: TestClient, login_as: Login, reauthenticate: Reauth, agent: FakeAgent
) -> None:
    who = login_as(client, "admin")
    reauthenticate(who)
    agent.prober.results["snmp"] = ProbeResult(True, None, "ok", REPORTED)
    before = client.get(f"{V}/admin/config").json()["etags"]["sources"]
    accepted = try_it(client)
    assert accepted.status_code == 202, accepted.text
    agent.process()
    found = client.get(accepted.json()["status_url"]).json()
    # the request's own state, never turned into active or partially_active
    assert (found["kind"], found["state"]) == ("config.apply", "succeeded"), found
    assert found["result"] == {
        "probe_only": True,
        "interfaces": [
            {"name": "wan2", "oper_status": "up", "speed_mbps": 1000},
            {"name": "lan", "oper_status": "down", "speed_mbps": None},
        ],
    }
    assert "fw-sube" not in {source["name"] for source in sources(client)}
    assert client.get(f"{V}/admin/config").json()["etags"]["sources"] == before
    (call,) = agent.prober.calls
    assert call.params["interfaces"] == []  # nothing chosen yet: the device lists them all
    assert "Örnek-SNMP-Kimlik-3" not in accepted.text + str(found)


def test_a_failed_test_is_probe_failed(
    client: TestClient, login_as: Login, reauthenticate: Reauth, agent: FakeAgent
) -> None:
    who = login_as(client, "admin")
    reauthenticate(who)
    agent.prober.results["snmp"] = ProbeResult(False, "snmp_auth_failed", "wrong keys", ())
    accepted = try_it(client)
    agent.process()
    found = client.get(accepted.json()["status_url"]).json()
    assert (found["state"], found["error"]["code"]) == ("probe_failed", "snmp_auth_failed")


def test_the_test_needs_settings_reauthentication_keys_and_waits_a_minute(
    client: TestClient, login_as: Login, reauthenticate: Reauth, agent: FakeAgent
) -> None:
    login_as(client, "viewer")
    body = {"etag": "x", "operation": "add", "source": UNCHOSEN, **KEYS}
    assert client.post(TEST_URL, json=body).status_code == 403
    who = login_as(client, "admin")
    stale = try_it(client)
    assert (stale.status_code, stale.json()["error"]["code"]) == (403, "reauth_required")
    reauthenticate(who)
    no_keys = try_it(client, snmp_auth_key=None, snmp_priv_key="")
    assert no_keys.status_code == 422, no_keys.text
    assert no_keys.json()["error"]["code"] == "snmp_secret_missing"
    assert try_it(client).status_code == 202
    again = try_it(client)
    assert (again.status_code, again.json()["error"]["code"]) == (429, "admin_rate_limited")
    assert 1 <= again.json()["error"]["details"]["retry_after"] <= 60
