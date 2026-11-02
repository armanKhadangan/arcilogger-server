"""Ayarlar → Geçmiş: cancel, revert, versions and the diagnostics package (ADR-0014; contract
2.7.2, 2.11.1, 2.11.2).

The real API and agent: a request the agent has not taken is removed from the inbox by the API
itself; a held change is cancelled by a signed ``config.cancel`` (re-authenticated) and nothing
changes; an applied change is reverted by a signed ``config.revert`` that the agent builds from
the version it saved; the versions come from the agent's journal. The diagnostics ZIP holds the
status files, the service logs' last lines and the configuration without any secret and without
a raw log line, and is audited.
"""

from __future__ import annotations

import io
import zipfile
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.admin_channel import manual_steps
from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.testing import FakeAgent, Installed
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.models import AuditLog

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
Reauth = Callable[[LoggedIn], None]
V = "/api/v1"
CONFIG = f"{V}/admin/config"
REQUESTS = f"{V}/admin/requests"


def etag(client: TestClient, section: str) -> str:
    tag: str = client.get(CONFIG).json()["etags"][section]
    return tag


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def site_name(client: TestClient, name: str) -> dict[str, Any]:
    response = client.put(
        f"{CONFIG}/general", json={"etag": etag(client, "general"), "site_name": name}
    )
    assert response.status_code == 202, response.text
    accepted: dict[str, Any] = response.json()
    return accepted


def cancels(factory: sessionmaker[Session]) -> list[AuditLog]:
    with factory() as session:
        return list(
            session.scalars(select(AuditLog).where(AuditLog.action == "admin.request_cancel"))
        )


def test_a_queued_request_is_removed_from_the_inbox(
    client: TestClient,
    login_as: Login,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "admin")
    accepted = site_name(client, "Örnek Lojistik A.Ş.")
    request_file = installed.layout.request_file(accepted["request_id"])
    assert request_file.is_file()
    response = client.delete(f"{REQUESTS}/{accepted['request_id']}")
    assert response.status_code == 202, response.text
    assert response.json()["state"] == "cancelled"
    assert not request_file.exists()
    agent.process()
    assert client.get(accepted["status_url"]).json()["state"] == "cancelled"
    assert installed.config().site_name == "Örnek A.Ş."
    (entry,) = cancels(app_session_factory)
    assert entry.details == {"state_before": "queued"}
    assert error(client.delete(f"{REQUESTS}/{accepted['request_id']}")) == (
        409,
        "request_not_cancellable",
    )
    assert error(client.delete(f"{REQUESTS}/{'0' * 32}")) == (404, "admin_request_not_found")


def test_a_held_change_is_cancelled_by_the_agent(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
) -> None:
    who = login_as(client, "admin")
    reauthenticate(who)
    installed.write_policy(hold_hours=24)
    body = {
        "etag": etag(client, "retention"),
        "enabled": True,
        "days": 760,
        "confirm_delete_days": 0,
    }
    held = client.put(f"{CONFIG}/retention", json=body)
    assert held.status_code == 202, held.text
    agent.process()
    target = held.json()
    assert client.get(target["status_url"]).json()["state"] == "pending"

    cancel = client.delete(f"{REQUESTS}/{target['request_id']}")
    assert cancel.status_code == 202, cancel.text
    assert cancel.json()["state"] == "queued"  # the agent's config.cancel
    agent.process()
    done = client.get(cancel.json()["status_url"]).json()
    assert (done["kind"], done["state"]) == ("config.cancel", "succeeded"), done
    assert client.get(target["status_url"]).json()["state"] == "cancelled"
    assert installed.config().retention.enabled is False
    assert [entry.details for entry in cancels(app_session_factory)] == [
        {"state_before": "pending"}
    ]
    assert client.get(CONFIG).json()["pending"] == []


def test_an_applied_change_is_reverted_from_its_version(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    agent: FakeAgent,
) -> None:
    who = login_as(client, "admin")
    accepted = site_name(client, "Örnek Lojistik A.Ş.")
    agent.process()
    assert client.get(accepted["status_url"]).json()["state"] == "active"
    assert installed.config().site_name == "Örnek Lojistik A.Ş."
    url = f"{REQUESTS}/{accepted['request_id']}/revert"
    assert error(client.post(url)) == (403, "reauth_required")
    reauthenticate(who)
    revert = client.post(url)
    assert revert.status_code == 202, revert.text
    assert revert.json()["changes"] == {
        "site_name": {"old": "Örnek Lojistik A.Ş.", "new": "Örnek A.Ş."}
    }
    agent.process()
    done = client.get(revert.json()["status_url"]).json()
    assert (done["kind"], done["state"]) == ("config.revert", "active"), done
    assert installed.config().site_name == "Örnek A.Ş."

    versions = client.get(f"{CONFIG}/versions").json()
    assert [(item["request_id"], item["paths"]) for item in versions] == [
        (revert.json()["request_id"], ["site_name"]),
        (accepted["request_id"], ["site_name"]),
    ]
    assert versions[0]["n"] > versions[1]["n"]
    assert versions[1]["actor"] == who.username
    assert versions[1]["has_secrets"] is False

    queued = site_name(client, "Örnek Holding A.Ş.")
    assert error(client.post(f"{REQUESTS}/{queued['request_id']}/revert")) == (
        409,
        "request_not_revertible",
    )


def test_the_diagnostics_hold_no_secret_and_no_raw_log_line(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
) -> None:
    secret = "Kanarya-Tani-Parola-77"
    installed.store.set("smtp.password", secret)
    logs = installed.data.logs
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "worker.log").write_text(
        "".join(f"satır {n}\n" for n in range(500))
        + f"smtp login failed password={secret} user=arcilogger\n"
        + f"retry with {secret}\n",
        encoding="utf-8",
    )
    raw = "date=2026-10-12 srcip=172.20.60.21 dstip=203.0.113.9 action=accept"
    spool = installed.data.spool_file("fortigate", site_day(utc_now()))
    spool.parent.mkdir(parents=True, exist_ok=True)
    spool.write_bytes(format_line(utc_now(), "172.31.255.1", raw.encode()))

    who = login_as(client, "admin")
    assert error(client.get(f"{V}/admin/diagnostics")) == (403, "reauth_required")
    reauthenticate(who)
    response = client.get(f"{V}/admin/diagnostics")
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "application/zip"
    assert "attachment" in response.headers["content-disposition"]
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        names = archive.namelist()
        content = {name: archive.read(name).decode("utf-8") for name in names}
    assert {"README.txt", "config/arcilogger.toml", "config/config-check.json"} <= set(names)
    assert "status/agent.json" in names
    assert "logs/worker.log" in names
    log = content["logs/worker.log"]
    assert len(log.splitlines()) == 200
    assert "password=***" in log
    for text in content.values():
        assert secret not in text
        assert raw not in text
    with app_session_factory() as session:
        entry = session.scalars(
            select(AuditLog).where(AuditLog.action == "admin.diagnostics")
        ).one()
    assert entry.details == {"files": sorted(names)}


def test_only_administrators_see_the_history_tools(client: TestClient, login_as: Login) -> None:
    login_as(client, "auditor")
    for method, path in (
        ("GET", f"{CONFIG}/versions"),
        ("GET", f"{V}/admin/diagnostics"),
        ("DELETE", f"{REQUESTS}/{'0' * 32}"),
        ("POST", f"{REQUESTS}/{'0' * 32}/revert"),
    ):
        assert error(client.request(method, path)) == (403, "forbidden"), path


def test_the_manual_steps_run_when_pasted_on_the_server() -> None:
    """The ARCILOGGER programs are not on PATH: the steps name the installation's interpreter.
    For ``acl_drift`` the change is in force, so the rights are repaired instead of putting the
    previous version back."""
    python = (
        '& "C:\\Program Files\\ARCILOGGER\\venv\\Scripts\\python.exe" -I -m arcilogger_core.admin'
    )
    for text in manual_steps(4, "rollback_failed"):
        assert text is not None
        assert f"{python} versions restore 4" in text
        assert f"{python} status" in text
        assert "…" not in text
    for text in manual_steps(4, "acl_drift"):
        assert text is not None
        assert '& "C:\\Program Files\\ARCILOGGER\\scripts\\Set-ArciVaultAcl.ps1"' in text
        assert "versions restore" not in text
    assert manual_steps(None, "rollback_failed") == (None, None)
