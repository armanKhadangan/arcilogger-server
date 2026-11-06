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
