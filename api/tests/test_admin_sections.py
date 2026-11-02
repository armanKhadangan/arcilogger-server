"""E-mail, replica, retention and backups through the browser (ADR-0014; contract 2.6,
2.11.1).

The real API and agent on a real installation: each change needs a re-authentication, is tested
first where it reaches a server (the agent's probe gets the typed password, the API never sees
it again), and is refused with the rules' field codes otherwise. The SMTP CA file is uploaded,
trusted and cleared; a retention change shows the sealed days it deletes and must be confirmed
with their number; a change the installer's policy holds waits as ``pending``.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from arcilogger_api import admin_channel
from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin import agent as agent_module
from arcilogger_core.admin.testing import FakeAgent, Installed, windows_replica_target
from arcilogger_core.config import Config, parse_config

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
Reauth = Callable[[LoggedIn], None]
Pem = Callable[..., bytes]
V = "/api/v1"
CONFIG = f"{V}/admin/config"
#: The administrator's own address: the probe's test e-mail may go there.
OWN = "tolga@ornek.example"
SMTP = {
    "enabled": True,
    "host": "smtp.ornek.example",
    "port": 587,
    "security": "starttls",
    "username": "arcilogger",
    "from_address": "arcilogger@ornek.example",
    "timeout_seconds": 30,
    "test_to": OWN,
}


def etag(client: TestClient, section: str) -> str:
    tag: str = client.get(CONFIG).json()["etags"][section]
    return tag


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def fields(response: Any) -> list[tuple[str, str]]:
    return [(item["field"], item["code"]) for item in response.json()["error"]["details"]]


def settle(client: TestClient, agent: FakeAgent, response: Any) -> dict[str, Any]:
    assert response.status_code == 202, response.text
    agent.process()
    found: dict[str, Any] = client.get(response.json()["status_url"]).json()
    return found


@pytest.fixture
def admin(client: TestClient, login_as: Login, reauthenticate: Reauth) -> LoggedIn:
    """An administrator who re-authenticated just now."""
    who = login_as(client, "admin", email=OWN)
    reauthenticate(who)
    return who


def test_the_mail_server_is_probed_with_the_typed_password(
    client: TestClient, login_as: Login, reauthenticate: Reauth, agent: FakeAgent
) -> None:
    who = login_as(client, "admin", email=OWN)
    body = {"etag": etag(client, "smtp"), **SMTP, "password": "Örnek-Posta-Parola-3"}
    checked = client.post(f"{CONFIG}/smtp/check", json=body)
    assert checked.status_code == 200, checked.text
    assert checked.json()["reauth_required"] is True
    assert checked.json()["changes"]["smtp.password"]["new"] == "set"
    assert "reload.worker" in checked.json()["effects"]
    assert error(client.put(f"{CONFIG}/smtp", json=body)) == (403, "reauth_required")

    reauthenticate(who)
    done = settle(client, agent, client.put(f"{CONFIG}/smtp", json=body))
    assert done["state"] == "active", done
    steps = {step["step"]: step["state"] for step in done["steps"]}
    assert steps["probe"] == "done"
    (call,) = agent.prober.calls
    assert call.kind == "smtp"
    assert call.secrets == {"smtp.password": "Örnek-Posta-Parola-3"}
    assert call.params["to"] == OWN
    smtp = client.get(CONFIG).json()["sections"]["smtp"]
    assert (smtp["enabled"], smtp["host"], smtp["username"]) == (
        True,
        "smtp.ornek.example",
        "arcilogger",
    )
    assert smtp["secrets"]["smtp.password"]["set"] is True


def test_a_mail_server_change_is_reverted_without_a_trial_recipient(
    client: TestClient, admin: LoggedIn, agent: FakeAgent
) -> None:
    """The revert ("Geri al") takes no body: it puts back values that were in force before
    and is not probed, so it needs no recipient while the server is enabled."""
    body = {"etag": etag(client, "smtp"), **SMTP, "password": "Örnek-Posta-Parola-3"}
    assert settle(client, agent, client.put(f"{CONFIG}/smtp", json=body))["state"] == "active"
    moved = {
        "etag": etag(client, "smtp"),
        "host": "posta.ornek.example",
        "password": "Örnek-Posta-Parola-4",
        "test_to": OWN,
    }
    changed = client.put(f"{CONFIG}/smtp", json=moved)
    assert settle(client, agent, changed)["state"] == "active"
    tested = len(agent.prober.calls)
    revert = client.post(f"{V}/admin/requests/{changed.json()['request_id']}/revert")
    done = settle(client, agent, revert)
    assert (done["kind"], done["state"]) == ("config.revert", "active"), done
    steps = {step["step"]: (step["state"], step["code"]) for step in done["steps"]}
    assert steps["probe"] == ("skipped", "probe_skipped")
    assert len(agent.prober.calls) == tested
    assert client.get(CONFIG).json()["sections"]["smtp"]["host"] == "smtp.ornek.example"


def test_mail_server_refusals_name_their_fields(
    client: TestClient, admin: LoggedIn, agent: FakeAgent
) -> None:
    tag = etag(client, "smtp")
    no_password = client.put(f"{CONFIG}/smtp", json={"etag": tag, **SMTP})
    assert error(no_password) == (422, "smtp_password_required")
    assert fields(no_password) == [("smtp.password", "smtp_password_required")]
    plain = {**SMTP, "security": "none", "password": "x-Parola-1"}
    refused = client.put(f"{CONFIG}/smtp", json={"etag": tag, **plain})
    assert error(refused) == (422, "smtp_plaintext_auth_refused")
    stranger = {"etag": tag, **SMTP, "password": "x-Parola-1", "test_to": "yabanci@baska.example"}
    response = client.put(f"{CONFIG}/smtp", json=stranger)
    assert error(response) == (422, "test_email_recipient_not_allowed")
    assert fields(response) == [("test_to", "test_email_recipient_not_allowed")]
    assert "yabanci@baska.example" not in response.text


def test_the_smtp_ca_file_is_trusted_and_cleared(
    client: TestClient, admin: LoggedIn, installed: Installed, agent: FakeAgent, ca_pem: Pem
) -> None:
    pem = ca_pem("Örnek Posta Kökü")
    staged = client.post(
        f"{V}/admin/uploads",
        params={"kind": "smtp_ca"},
        files={"file": ("posta-ca.pem", pem, "application/x-pem-file")},
    )
    assert staged.status_code == 201, staged.text
    preview = staged.json()
    assert preview["certificate"]["subject"].endswith("Örnek Posta Kökü")
    body = {"etag": etag(client, "smtp"), "upload_id": preview["upload_id"]}
    done = settle(client, agent, client.post(f"{CONFIG}/smtp/ca", json={**body, "test_to": OWN}))
    assert done["state"] == "active", done
    assert client.get(CONFIG).json()["sections"]["smtp"]["ca"]["sha256"] == preview["sha256"]
    assert installed.config().smtp.ca_file is not None

    params = {"etag": etag(client, "smtp")}
    clear = client.request("DELETE", f"{CONFIG}/smtp/ca", params=params, json={"test_to": OWN})
    cleared = settle(client, agent, clear)
    assert cleared["state"] == "active", cleared
    assert client.get(CONFIG).json()["sections"]["smtp"]["ca"]["sha256"] is None
    again = client.delete(f"{CONFIG}/smtp/ca", params={"etag": etag(client, "smtp")})
    assert error(again) == (422, "config_unchanged")


def test_a_new_smtp_ca_file_needs_the_password_again(
    client: TestClient, admin: LoggedIn, agent: FakeAgent, ca_pem: Pem
) -> None:
    """With a user name the CA file is part of the server's identity: the stored password is
    never sent to a server trusted differently, so the CA change takes the password again."""
    body = {"etag": etag(client, "smtp"), **SMTP, "password": "Örnek-Posta-Parola-3"}
    assert settle(client, agent, client.put(f"{CONFIG}/smtp", json=body))["state"] == "active"
    staged = client.post(
        f"{V}/admin/uploads",
        params={"kind": "smtp_ca"},
        files={"file": ("posta-ca.pem", ca_pem("Örnek Posta Kökü"), "application/x-pem-file")},
    )
    ca = {"etag": etag(client, "smtp"), "upload_id": staged.json()["upload_id"], "test_to": OWN}
    refused = client.post(f"{CONFIG}/smtp/ca", json=ca)
    assert error(refused) == (422, "secret_rebind_required")
    assert fields(refused) == [("smtp.password", "secret_rebind_required")]

    response = client.post(f"{CONFIG}/smtp/ca", json={**ca, "password": "Örnek-Posta-Parola-4"})
    assert "Örnek-Posta-Parola-4" not in response.text
    done = settle(client, agent, response)
    assert done["state"] == "active", done
    assert done["changes"]["smtp.password"]["new"] == "set"
    assert agent.prober.calls[-1].secrets == {"smtp.password": "Örnek-Posta-Parola-4"}

    params = {"etag": etag(client, "smtp")}
    again = client.request("DELETE", f"{CONFIG}/smtp/ca", params=params, json={"test_to": OWN})
    assert error(again) == (422, "secret_rebind_required")
    clear = {"test_to": OWN, "password": "Örnek-Posta-Parola-5"}
    cleared = settle(
        client, agent, client.request("DELETE", f"{CONFIG}/smtp/ca", params=params, json=clear)
    )
    assert cleared["state"] == "active", cleared
    assert agent.prober.calls[-1].secrets == {"smtp.password": "Örnek-Posta-Parola-5"}


def test_the_replica_share_is_probed_and_a_switch_off_is_held(
    client: TestClient, admin: LoggedIn, installed: Installed, agent: FakeAgent
) -> None:
    body = {
        "etag": etag(client, "replica"),
        "enabled": True,
        "target": "\\\\NAS-01\\arcilogger-worm",
        "user": "NAS-01\\arcireplica",
        "password": "Örnek-Paylaşım-Parola-4",
    }
    done = settle(client, agent, client.put(f"{CONFIG}/replica", json=body))
    assert done["state"] == "active", done
    assert [call.kind for call in agent.prober.calls] == ["replica"]
    replica = client.get(CONFIG).json()["sections"]["replica"]
    assert replica["target"] == "\\\\NAS-01\\arcilogger-worm"
    bad = client.put(
        f"{CONFIG}/replica", json={"etag": etag(client, "replica"), "target": "NAS-01/paylasim"}
    )
    assert error(bad) == (422, "replica_target_invalid")

    installed.write_policy(hold_hours=12)
    off = client.put(f"{CONFIG}/replica", json={"etag": etag(client, "replica"), "enabled": False})
    assert off.status_code == 202, off.text
    assert off.json()["guarded"] is True
    assert off.json()["effective_at"] is not None
    held = settle(client, agent, off)
    assert held["state"] == "pending", held
    assert installed.config().replica.enabled is True  # nothing changed yet
    assert [item["section"] for item in client.get(CONFIG).json()["pending"]] == ["replica"]


def test_the_share_reads_back_as_saved_where_its_root_keeps_a_separator(
    client: TestClient, admin: LoggedIn, agent: FakeAgent, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows keeps a share root's separator in ``str()`` of its path (simulated here for the
    API and the agent): the page shows the share in the one form it is saved in, and the next
    change of the section expects it."""

    def as_on_windows(data: bytes, path: Path) -> Config:
        return windows_replica_target(parse_config(data, path))

    monkeypatch.setattr(admin_channel, "parse_config", as_on_windows)
    monkeypatch.setattr(agent_module, "parse_config", as_on_windows)
    body = {
        "etag": etag(client, "replica"),
        "enabled": True,
        "target": "//NAS-01/arcilogger-worm/",
        "user": "NAS-01\\arcireplica",
        "password": "Örnek-Paylaşım-Parola-6",
    }
    done = settle(client, agent, client.put(f"{CONFIG}/replica", json=body))
    assert done["state"] == "active", done
    saved = "\\\\NAS-01\\arcilogger-worm"
    assert client.get(CONFIG).json()["sections"]["replica"]["target"] == saved
    unchanged = {"etag": etag(client, "replica"), "target": saved}
    checked = client.post(f"{CONFIG}/replica/check", json=unchanged)
    assert checked.status_code == 200, checked.text
    assert checked.json()["changes"] == {}
    folder = {"etag": etag(client, "replica"), "target": f"{saved}\\yeni"}  # the same host
    moved = settle(client, agent, client.put(f"{CONFIG}/replica", json=folder))
    assert moved["state"] == "active", moved
    assert client.get(CONFIG).json()["sections"]["replica"]["target"] == f"{saved}\\yeni"


def test_retention_shows_and_confirms_the_days_it_deletes(
    client: TestClient, admin: LoggedIn, installed: Installed, agent: FakeAgent
) -> None:
    body = {"etag": etag(client, "retention"), "enabled": True, "days": 760}
    checked = client.post(f"{CONFIG}/retention/check", json=body)
    assert checked.status_code == 200, checked.text
    preview = checked.json()["retention_preview"]
    assert preview["days_to_delete"] == 0
    assert preview["first_run_at"] is not None
    unconfirmed = client.put(f"{CONFIG}/retention", json=body)
    assert error(unconfirmed) == (422, "retention_confirm_mismatch")
    short = client.put(f"{CONFIG}/retention", json={**body, "days": 365, "confirm_delete_days": 0})
    assert error(short) == (422, "retention_below_minimum")
    done = settle(
        client, agent, client.put(f"{CONFIG}/retention", json={**body, "confirm_delete_days": 0})
    )
    assert done["state"] == "active", done
    assert installed.config().retention.enabled is True


def test_backups_are_kept_as_set(client: TestClient, admin: LoggedIn, agent: FakeAgent) -> None:
    tag = etag(client, "backup")
    refused = client.put(f"{CONFIG}/backup", json={"etag": tag, "keep_daily": 0})
    assert fields(refused) == [("backup.keep_daily", "config_invalid")]
    done = settle(
        client, agent, client.put(f"{CONFIG}/backup", json={"etag": tag, "keep_daily": 21})
    )
    assert done["state"] == "active", done
    assert client.get(CONFIG).json()["sections"]["backup"]["keep_daily"] == 21


def test_only_administrators_change_the_sections(client: TestClient, login_as: Login) -> None:
    login_as(client, "auditor")
    for section in ("smtp", "replica", "retention", "backup"):
        response = client.put(f"{CONFIG}/{section}", json={"etag": f"{section}:0"})
        assert error(response) == (403, "forbidden")
