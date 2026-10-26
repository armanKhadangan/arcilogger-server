"""No credential typed in the browser is ever stored or shown in the clear (ADR-0014; contract
2.5.1, test plan 3: secret leaks).

Distinct canary values for the Kamu SM customer number and password, the SMTP password and the
replica password go through dry runs, refusals and applied changes with the real agent. None of
them may then appear in any API response, any ``audit_log`` row, any ``admin_requests`` row, any
log record, or any file of the installation (the configuration, the agent's journal, status
files, versions and inbox: ``secrets.json`` and the requests hold Fernet tokens only).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.testing import FakeAgent, Installed
from arcilogger_db.models import AdminRequest, AuditLog

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
Reauth = Callable[[LoggedIn], None]
V = "/api/v1"
CONFIG = f"{V}/admin/config"
OWN = "tolga@ornek.example"
CANARIES = {
    "kamusm.customer": "771100229933",
    "kamusm.password": "Kanarya-Kamusm-Parola-91",
    "smtp.password": "Kanarya-Smtp-Parola-92",
    "replica.password": "Kanarya-Replika-Parola-93",
}


def rows_text(factory: sessionmaker[Session]) -> list[str]:
    """Every column of every audit and request row, as JSON."""
    found = []
    with factory() as session:
        for model in (AuditLog, AdminRequest):
            for row in session.scalars(select(model)):
                values = {
                    column.key: getattr(row, column.key) for column in model.__mapper__.columns
                }
                found.append(json.dumps(values, ensure_ascii=False, default=str))
    return found


def files_with(root: Path, secret: str) -> list[str]:
    needle = secret.encode("utf-8")
    return [
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file() and needle in path.read_bytes()
    ]


def test_typed_credentials_leak_nowhere(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    answers: list[str] = []

    def call(method: str, url: str, **kwargs: Any) -> Any:
        response = client.request(method, url, **kwargs)
        answers.append(response.text)
        return response

    def tag(section: str) -> str:
        found: str = call("GET", CONFIG).json()["etags"][section]
        return found

    def apply(method: str, url: str, body: dict[str, Any]) -> None:
        response = call(method, url, json=body)
        assert response.status_code == 202, response.text
        agent.process()
        done = call("GET", response.json()["status_url"]).json()
        assert done["state"] == "active", done

    who = login_as(client, "admin", email=OWN)
    tsa = {
        "etag": tag("tsa"),
        "customer": CANARIES["kamusm.customer"],
        "password": CANARIES["kamusm.password"],
    }
    assert call("POST", f"{CONFIG}/tsa/check", json=tsa).status_code == 200
    assert call("PUT", f"{CONFIG}/tsa", json=tsa).status_code == 403  # re-authentication first
    reauthenticate(who)
    apply("PUT", f"{CONFIG}/tsa", tsa)

    smtp = {
        "etag": tag("smtp"),
        "enabled": True,
        "host": "smtp.ornek.example",
        "port": 587,
        "security": "none",
        "username": "arcilogger",
        "from_address": "arcilogger@ornek.example",
        "password": CANARIES["smtp.password"],
        "test_to": OWN,
    }
    refused = call("PUT", f"{CONFIG}/smtp", json=smtp)  # a password in the clear: refused
    assert refused.json()["error"]["code"] == "smtp_plaintext_auth_refused"
    assert (
        call("POST", f"{CONFIG}/smtp/check", json={**smtp, "security": "starttls"}).status_code
        == 200
    )
    apply("PUT", f"{CONFIG}/smtp", {**smtp, "security": "starttls"})

    replica = {
        "etag": tag("replica"),
        "enabled": True,
        "target": "\\\\NAS-01\\arcilogger-worm",
        "user": "NAS-01\\arcireplica",
        "password": CANARIES["replica.password"],
    }
    apply("PUT", f"{CONFIG}/replica", replica)
    assert {call.kind for call in agent.prober.calls} >= {"smtp", "replica"}
    for path in ("/admin/config", "/admin/status", "/admin/requests"):
        assert call("GET", f"{V}{path}").status_code == 200

    # The credentials arrived: the agent stored them (as Fernet tokens of secret.key) …
    for name, secret in CANARIES.items():
        assert installed.store.get(name) == secret
    # … and none of them is anywhere else.
    stored = rows_text(app_session_factory)
    for name, secret in CANARIES.items():
        for text in answers:
            assert secret not in text, name
        for text in stored:
            assert secret not in text, name
        assert secret not in caplog.text, name
        assert files_with(installed.root, secret) == [], name
