"""The machine configuration through the browser (ADR-0014; contract 2.5.1, 2.10, 2.11.1).

The real API on a real installation folder and the real administration agent (``FakeAgent``,
run synchronously): ``GET /admin/config`` and ``/admin/status``, a site name change from the
202 to ``active`` (the API's own reload counts), the dry run, ``etag`` conflicts, field
refusals with both texts and no echoed value, the agent missing, stale or switched off, the
channel key missing, the inbox quota and the one open request per section. The inbox file is
the signed request the agent verifies, and ``admin.request`` is in the same transaction as the
row.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.admin_channel import TEXTS
from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.contract import AgentStatus, dump_document, parse_document
from arcilogger_core.admin.envelope import open_admin_request
from arcilogger_core.admin.keys import load_keys
from arcilogger_core.admin.messages import MESSAGES
from arcilogger_core.admin.testing import FakeAgent, Installed
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import AdminRequest, AuditLog

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
V = "/api/v1"


def etag(client: TestClient, section: str = "general") -> str:
    tag: str = client.get(f"{V}/admin/config").json()["etags"][section]
    return tag


def follow(client: TestClient, agent: FakeAgent, accepted: dict[str, Any]) -> dict[str, Any]:
    """Let the agent take the inbox, then read the request back."""
    agent.process()
    response = client.get(accepted["status_url"])
    assert response.status_code == 200, response.text
    found: dict[str, Any] = response.json()
    return found


def rows(factory: sessionmaker[Session]) -> list[AdminRequest]:
    with factory() as session:
        return list(session.scalars(select(AdminRequest).order_by(AdminRequest.created_at)))


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        return list(session.scalars(select(AuditLog).where(AuditLog.action == action)))


def test_the_configuration_is_shown_without_any_secret(
    client: TestClient, login_as: Login, installed: Installed, agent: FakeAgent
) -> None:
    installed.store.set("smtp.password", "Gizli-SMTP-Parola-1")
    login_as(client, "admin")
    response = client.get(f"{V}/admin/config")
    assert response.status_code == 200, response.text
    document = response.json()
    assert "Gizli-SMTP-Parola-1" not in response.text
    general = document["sections"]["general"]
    assert general == {
        "site_name": "Örnek A.Ş.",
        "clock": {
            "ntp_server": "172.20.10.1",
            "fallback_server": "time.ume.tubitak.gov.tr",
            "alert_offset_ms": 1000,
        },
    }
    assert document["sections"]["smtp"]["secrets"]["smtp.password"]["set"] is True
    assert document["sections"]["tsa"]["provider"] == "mock"
    assert document["sections"]["tsa"]["server"] is None
    assert document["sections"]["retention"]["minimum_days"] == 730
    assert set(document["etags"]) == {
        "general",
        "tsa",
        "smtp",
        "replica",
        "retention",
        "backup",
        "sources",
    }
    locked = {item["path"]: item["reason"] for item in document["locked"]}
    assert locked["collector.unknown_senders"] == "collector"
    assert locked["data_dir"] == "security"
    assert locked["database.password"] == "security"
    assert document["policy"]["enabled"] is True
    assert document["restart_required"] is False
    assert len(document["config_sha256"]) == 64


def test_the_status_names_the_agent_and_the_services(
    client: TestClient, login_as: Login, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    document = client.get(f"{V}/admin/status").json()
    assert document["agent"]["state"] == "ok"
    assert document["system"]["state"] == "never"
    assert set(document["services"]) == {"ArciCollector", "ArciIngest", "ArciWorker", "ArciApi"}
    assert document["services"]["ArciApi"]["running"] is True
    assert document["services"]["ArciApi"]["sha256"] == document["config_sha256"]
    assert document["services"]["ArciCollector"]["running"] is False
    assert document["config_origin"] == "outside"  # nothing was changed through the browser


def test_a_site_name_change_goes_from_queued_to_active(
    client: TestClient,
    login_as: Login,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
) -> None:
    who = login_as(client, "admin")
    body = {"etag": etag(client), "site_name": "Örnek Lojistik A.Ş.", "probe": True}
    checked = client.post(f"{V}/admin/config/general/check", json=body)
    assert checked.status_code == 200, checked.text
    assert checked.json()["changes"] == {
        "site_name": {"old": "Örnek A.Ş.", "new": "Örnek Lojistik A.Ş."}
    }
    assert checked.json()["reauth_required"] is False
    assert not list(installed.layout.inbox.glob("*.req"))  # a dry run queues nothing

    response = client.put(f"{V}/admin/config/general", json=body)
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["state"] == "queued"
    assert accepted["short_id"] == accepted["request_id"][:8]
    assert accepted["effects"] == ["reload.api", "reload.worker"]

    # The file in the inbox is the signed request the agent verifies.
    request_file = installed.layout.request_file(accepted["request_id"])
    signed = open_admin_request(
        request_file.read_bytes(), load_keys(installed.layout.keys), now=utc_now()
    )
    assert signed.kind == "config.apply"
    assert signed.section == "general"
    assert (signed.actor.user_id, signed.actor.username) == (who.user_id, who.username)
    # The row and admin.request were committed together, before the file existed.
    (row,) = rows(app_session_factory)
    (entry,) = audits(app_session_factory, "admin.request")
    assert row.audit_id == entry.id == signed.audit_id
    assert entry.target == f"admin-request:{row.id}"
    assert entry.details["changes"] == {
        "site_name": {"old": "Örnek A.Ş.", "new": "Örnek Lojistik A.Ş."}
    }

    queued = client.get(accepted["status_url"]).json()
    assert queued["state"] == "queued"
    assert queued["label_tr"] == "Sırada"

    final = follow(client, agent, accepted)
    assert final["state"] == "active", final
    assert final["terminal"] is True
    assert final["label_tr"] == "Uygulandı"
    steps = {step["step"]: step["state"] for step in final["steps"]}
    assert steps["write_config"] == "done"
    assert steps["reload.api"] == "done"
    assert steps["reload.worker"] == "skipped"  # no worker runs here
    assert installed.config().site_name == "Örnek Lojistik A.Ş."
    assert client.get(f"{V}/auth/me").json()["site_name"] == "Örnek Lojistik A.Ş."
    (row,) = rows(app_session_factory)
    assert (row.state, row.terminal) == ("active", True)
    assert client.get(f"{V}/admin/status").json()["config_origin"] == "browser"


def test_a_stale_etag_is_a_conflict(client: TestClient, login_as: Login, agent: FakeAgent) -> None:
    login_as(client, "admin")
    response = client.put(
        f"{V}/admin/config/general", json={"etag": "general:0", "site_name": "Başka A.Ş."}
    )
    assert response.status_code == 409
    error = response.json()["error"]
    assert error["code"] == "config_conflict"
    assert error["details"]["current_etag"] == etag(client)
    assert (error["message_tr"], error["message_en"]) == TEXTS["config_conflict"]


def test_a_refused_field_names_its_code_in_both_languages_and_never_the_value(
    client: TestClient,
    login_as: Login,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "admin")
    response = client.put(
        f"{V}/admin/config/general", json={"etag": etag(client), "site_name": "   "}
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "site_name_required"
    assert error["details"] == [
        {
            "field": "site_name",
            "code": "site_name_required",
            "message_tr": MESSAGES["site_name_required"][0],
            "message_en": MESSAGES["site_name_required"][1],
        }
    ]
    bad = client.put(
        f"{V}/admin/config/general",
        json={"etag": etag(client), "clock": {"ntp_server": "ntp sunucusu yok!"}},
    )
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "host_invalid"
    assert "ntp sunucusu yok!" not in bad.text
    (refused, *_) = audits(app_session_factory, "admin.refused")
    assert refused.details == {
        "kind": "config.apply",
        "section": "general",
        "codes": ["site_name_required"],
    }
    assert not rows(app_session_factory)


def test_an_unchanged_form_is_not_queued(
    client: TestClient, login_as: Login, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    response = client.put(
        f"{V}/admin/config/general", json={"etag": etag(client), "site_name": "Örnek A.Ş."}
    )
    assert (response.status_code, response.json()["error"]["code"]) == (422, "config_unchanged")


def test_a_clock_change_needs_a_recent_reauthentication(
    client: TestClient, login_as: Login, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    body = {"etag": etag(client), "clock": {"ntp_server": "172.20.10.2"}, "probe": False}
    assert client.post(f"{V}/admin/config/general/check", json=body).json()["reauth_required"]
    response = client.put(f"{V}/admin/config/general", json=body)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "reauth_required")


def write_heartbeat(installed: Installed, age: timedelta) -> None:
    path = installed.layout.agent_status
    status = parse_document(AgentStatus, path.read_bytes())
    path.write_bytes(dump_document(status.model_copy(update={"heartbeat_at": utc_now() - age})))


def test_without_the_agent_nothing_is_queued(
    client: TestClient,
    login_as: Login,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "admin")
    body = {"etag": etag(client), "site_name": "Örnek Lojistik A.Ş."}
    write_heartbeat(installed, timedelta(seconds=45))
    stale = client.put(f"{V}/admin/config/general", json=body)
    assert stale.status_code == 202
    assert [w["code"] for w in stale.json()["warnings"]] == ["agent_stale"]
    agent.process()
    write_heartbeat(installed, timedelta(minutes=3))
    body["etag"] = etag(client)
    body["site_name"] = "Örnek Holding A.Ş."
    missing = client.put(f"{V}/admin/config/general", json=body)
    assert missing.status_code == 503
    error = missing.json()["error"]
    assert error["code"] == "admin_agent_unavailable"
    assert (error["message_tr"], error["message_en"]) == TEXTS["admin_agent_unavailable"]
    assert client.get(f"{V}/admin/status").json()["agent"]["state"] == "missing"
    assert len(rows(app_session_factory)) == 1


def test_a_switched_off_administration_and_a_missing_key(
    client: TestClient, login_as: Login, installed: Installed, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    body = {"etag": etag(client), "site_name": "Örnek Lojistik A.Ş."}
    installed.write_policy(enabled=False)
    off = client.put(f"{V}/admin/config/general", json=body)
    assert (off.status_code, off.json()["error"]["code"]) == (503, "admin_disabled")
    assert off.json()["error"]["message_tr"] == MESSAGES["admin_disabled"][0]
    assert client.get(f"{V}/admin/status").json()["agent"]["state"] == "disabled"
    assert client.get(f"{V}/admin/config").json()["policy"]["enabled"] is False
    installed.write_policy()
    (installed.layout.keys / "api-ed25519.key").unlink()
    missing = client.put(f"{V}/admin/config/general", json=body)
    assert (missing.status_code, missing.json()["error"]["code"]) == (
        503,
        "admin_channel_key_missing",
    )


def test_one_open_change_per_section_and_the_inbox_quota(
    client: TestClient, login_as: Login, installed: Installed, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    body = {"etag": etag(client), "site_name": "Örnek Lojistik A.Ş."}
    assert client.put(f"{V}/admin/config/general", json=body).status_code == 202
    busy = client.put(f"{V}/admin/config/general", json={**body, "site_name": "Örnek Holding A.Ş."})
    assert (busy.status_code, busy.json()["error"]["code"]) == (409, "admin_section_busy")
    agent.process()
    for number in range(20):
        (installed.layout.inbox / f"{number:032x}.req").write_bytes(b"{}")
    body = {"etag": etag(client), "site_name": "Örnek Holding A.Ş."}
    full = client.put(f"{V}/admin/config/general", json=body)
    assert (full.status_code, full.json()["error"]["code"]) == (429, "admin_queue_full")


def test_a_request_the_agent_never_takes_waits_then_expires(
    client: TestClient,
    login_as: Login,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "admin")
    body = {"etag": etag(client), "site_name": "Örnek Lojistik A.Ş."}
    accepted = client.put(f"{V}/admin/config/general", json=body).json()
    with app_session_factory() as session:
        row = session.get_one(AdminRequest, accepted["request_id"])
        row.created_at -= timedelta(minutes=2)
        session.commit()
    assert client.get(accepted["status_url"]).json()["state"] == "waiting_for_agent"
    with app_session_factory() as session:
        row = session.get_one(AdminRequest, accepted["request_id"])
        row.expires_at = utc_now() - timedelta(minutes=3)
        session.commit()
    expired = client.get(accepted["status_url"]).json()
    assert expired["state"] == "expired"
    assert expired["terminal"] is True
    assert not installed.layout.request_file(accepted["request_id"]).exists()
    assert client.get(f"{V}/admin/requests").json()["items"][0]["state"] == "expired"


def test_the_api_follows_a_hand_edit_and_refuses_moved_folders(
    client: TestClient, login_as: Login, installed: Installed, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    text = installed.config_path.read_text(encoding="utf-8")
    installed.config_path.write_text(
        text.replace('site_name = "Örnek A.Ş."', 'site_name = "Elle Değişti A.Ş."'),
        encoding="utf-8",
    )
    assert client.get(f"{V}/auth/me").json()["site_name"] == "Elle Değişti A.Ş."
    status = client.get(f"{V}/admin/status").json()
    assert status["services"]["ArciApi"]["sha256"] == status["config_sha256"]
    assert status["config_origin"] == "outside"
    moved = installed.config_path.read_text(encoding="utf-8").replace(
        'site_name = "Elle Değişti A.Ş."',
        f'site_name = "Taşındı A.Ş."\nsecrets_dir = "{(installed.root / "baska").as_posix()}"',
    )
    installed.config_path.write_text(moved, encoding="utf-8")
    assert client.get(f"{V}/auth/me").json()["site_name"] == "Elle Değişti A.Ş."
    api = client.get(f"{V}/admin/status").json()["services"]["ArciApi"]
    assert "secrets_dir" in api["error"]
    refused = client.put(
        f"{V}/admin/config/general", json={"etag": "general:x", "site_name": "Yeni A.Ş."}
    )
    assert (refused.status_code, refused.json()["error"]["code"]) == (409, "config_unreadable")
    problems = client.get(f"{V}/admin/config").json()["problems"]
    assert problems[0]["key"] == "arcilogger.toml"


@pytest.mark.parametrize("role", ["auditor", "hr", "viewer"])
def test_only_administrators_see_or_change_the_machine_configuration(
    client: TestClient, login_as: Login, agent: FakeAgent, role: str
) -> None:
    login_as(client, role)
    for method, path in (
        ("GET", "/admin/config"),
        ("GET", "/admin/status"),
        ("POST", "/admin/config/general/check"),
        ("PUT", "/admin/config/general"),
        ("GET", "/admin/requests"),
    ):
        response = client.request(method, f"{V}{path}", json={"etag": "general:x"})
        assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden"), (
            method,
            path,
        )


def test_every_rule_code_has_its_two_texts() -> None:
    from arcilogger_api.admin_channel import STATUS, texts

    for code in STATUS:
        message_tr, message_en = texts(code)
        assert message_tr, code
        assert message_en, code
        assert message_tr != message_en, code
    # No text claims legal compliance ("5651 yükümlülüklerini desteklemek için tasarlanmıştır").
    every = json.dumps(TEXTS, ensure_ascii=False).lower()
    assert not any(word in every for word in ("uyumlu", "uyum ", "complian", "yasal olarak"))
