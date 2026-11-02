"""Kamu SM through the browser (ADR-0014; contract 2.4.4, 2.6 tsa, 2.11.1, 2.11.3).

The real API on a real installation and the real agent (``FakeAgent``): the customer number and
password reach the agent only as Fernet tokens of the installation's key inside a signed request
(never in a response, the audit log, ``admin_requests`` or the API's log), the server switch
that would stop sealing, the uploads (a root certificate previewed, installed and marked legal;
a Zamane client that is not a known release held for its approval; files that are not what they
claim refused and removed) and the stamp test with its confirmation and its limits.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.contract import UPLOAD_LIMITS, SecretSetOp
from arcilogger_core.admin.envelope import open_admin_request
from arcilogger_core.admin.keys import load_keys
from arcilogger_core.admin.testing import FakeAgent, Installed
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import AdminRequest, AuditLog

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
Reauth = Callable[[LoggedIn], None]
Pem = Callable[..., bytes]
V = "/api/v1"
CUSTOMER = "998877"
PASSWORD = "Örnek-Kontör-Parola-7"


def etag(client: TestClient, section: str = "tsa") -> str:
    tag: str = client.get(f"{V}/admin/config").json()["etags"][section]
    return tag


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def client_jar(content: bytes) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("META-INF/MANIFEST.MF", "Manifest-Version: 1.0\n")
        archive.writestr("ornek/Client.class", content)
    return buffer.getvalue()


def upload(client: TestClient, kind: str, data: bytes, name: str = "dosya") -> Any:
    return client.post(
        f"{V}/admin/uploads",
        params={"kind": kind},
        files={"file": (name, data, "application/octet-stream")},
    )


def test_kamusm_credentials_reach_the_agent_only_as_fernet_tokens(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    who = login_as(client, "admin")
    body = {"etag": etag(client), "customer": CUSTOMER, "password": PASSWORD}
    checked = client.post(f"{V}/admin/config/tsa/check", json=body)
    assert checked.status_code == 200, checked.text
    assert checked.json()["reauth_required"] is True
    assert checked.json()["changes"]["kamusm.password"] == {
        "old": "unset",
        "new": "set",
        "secret": True,
        "changed": True,
    }
    assert error(client.put(f"{V}/admin/config/tsa", json=body)) == (403, "reauth_required")

    reauthenticate(who)
    response = client.put(f"{V}/admin/config/tsa", json=body)
    assert response.status_code == 202, response.text
    accepted = response.json()

    # The inbox file is the signed request; the credentials are Fernet tokens of secret.key.
    data = installed.layout.request_file(accepted["request_id"]).read_bytes()
    signed = open_admin_request(data, load_keys(installed.layout.keys), now=utc_now())
    tokens = {op.name: op.token for op in signed.ops if isinstance(op, SecretSetOp)}
    assert set(tokens) == {"kamusm.customer", "kamusm.password"}
    assert installed.store.decrypt_value(tokens["kamusm.customer"]) == CUSTOMER
    assert installed.store.decrypt_value(tokens["kamusm.password"]) == PASSWORD
    for token in tokens.values():
        assert token.startswith("gAAAAA")
    for secret in (CUSTOMER, PASSWORD, json.dumps(PASSWORD)[1:-1]):
        assert secret.encode() not in data
        assert secret not in checked.text
        assert secret not in response.text

    agent.process()
    done = client.get(accepted["status_url"])
    assert done.json()["state"] == "active", done.text
    assert installed.store.get("kamusm.password") == PASSWORD
    secrets = client.get(f"{V}/admin/config").json()["sections"]["tsa"]["secrets"]
    assert secrets["kamusm.password"]["set"] is True
    assert secrets["kamusm.password"]["changed_by"] == f"web:{who.username}"

    with app_session_factory() as session:
        stored = [
            json.dumps(row.params, ensure_ascii=False)
            for row in session.scalars(select(AdminRequest))
        ]
        stored += [
            json.dumps(entry.details, ensure_ascii=False)
            for entry in session.scalars(select(AuditLog))
        ]
    for text in [*stored, caplog.text, done.text]:
        assert CUSTOMER not in text
        assert PASSWORD not in text


def test_the_threshold_needs_no_reauthentication(
    client: TestClient, login_as: Login, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    body = {"etag": etag(client), "credit_alert_threshold": 250, "probe": False}
    response = client.put(f"{V}/admin/config/tsa", json=body)
    assert response.status_code == 202, response.text
    assert response.json()["changes"] == {"tsa.credit_alert_threshold": {"old": 500, "new": 250}}
    agent.process()
    assert client.get(response.json()["status_url"]).json()["state"] == "active"
    tsa = client.get(f"{V}/admin/config").json()["sections"]["tsa"]
    assert tsa["credit_alert_threshold"] == 250


def test_a_switch_that_would_stop_sealing_is_refused(
    client: TestClient, login_as: Login, reauthenticate: Reauth, agent: FakeAgent
) -> None:
    reauthenticate(login_as(client, "admin"))
    mock = client.put(f"{V}/admin/config/tsa", json={"etag": etag(client), "server": "mock"})
    assert error(mock) == (422, "tsa_mock_refused")
    # Kamu SM's test server without Java, a client and credentials: the Seal task would stop.
    response = client.put(f"{V}/admin/config/tsa", json={"etag": etag(client), "server": "test"})
    assert error(response) == (409, "tsa_would_break")
    details = response.json()["error"]["details"]
    assert details["causes"]
    tsa = client.get(f"{V}/admin/config").json()["sections"]["tsa"]
    assert (tsa["provider"], tsa["server"]) == ("mock", None)
    assert tsa["readiness"]["production"] is False


def test_a_root_certificate_is_previewed_installed_and_made_legal(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
    ca_pem: Pem,
) -> None:
    reauthenticate(login_as(client, "admin"))
    pem = ca_pem()
    staged = upload(client, "kamusm_root", pem, "kok.pem")
    assert staged.status_code == 201, staged.text
    preview = staged.json()
    assert preview["kind"] == "kamusm_root"
    assert preview["size"] == len(pem)
    assert preview["sha256"] == hashlib.sha256(pem).hexdigest()
    assert preview["certificate"]["subject"].endswith("Örnek Test Kök")
    assert preview["certificate"]["is_ca"] is True
    assert preview["certificate"]["installed"] is False
    with app_session_factory() as session:
        entry = session.scalars(select(AuditLog).where(AuditLog.action == "admin.upload")).one()
    assert entry.details == {"kind": "kamusm_root", "sha256": preview["sha256"], "size": len(pem)}

    body = {"etag": etag(client), "upload_id": preview["upload_id"], "legal": True}
    unconfirmed = client.post(f"{V}/admin/config/tsa/roots", json=body)
    assert error(unconfirmed) == (422, "legal_root_confirm_required")
    response = client.post(f"{V}/admin/config/tsa/roots", json={**body, "confirmed": True})
    assert response.status_code == 202, response.text
    with app_session_factory() as session:
        row = session.get_one(AdminRequest, response.json()["request_id"])
        (item,) = row.params["files"]
    assert (item["kind"], item["sha256"], item["size"]) == (
        "kamusm_root",
        preview["sha256"],
        len(pem),
    )

    agent.process()
    assert client.get(response.json()["status_url"]).json()["state"] == "active"
    (root,) = client.get(f"{V}/admin/config").json()["sections"]["tsa"]["roots"]
    assert (root["legal"], root["source"]) == (True, "web")
    assert installed.config().tsa.legal_root_fingerprints == (root["sha256"],)
    again = upload(client, "kamusm_root", pem, "kok.pem").json()
    assert again["certificate"]["installed"] is True


def test_uploads_that_are_not_what_they_claim(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    installed: Installed,
    agent: FakeAgent,
    ca_pem: Pem,
) -> None:
    login_as(client, "admin")
    assert error(upload(client, "kamusm_root", b"bir sertifika degil")) == (
        422,
        "root_certificate_invalid",
    )
    assert error(upload(client, "zamane_client", b"PK\x03\x04 kirik")) == (
        422,
        "zamane_archive_invalid",
    )
    assert not [path for path in installed.layout.uploads.iterdir() if path.is_file()]
    assert error(upload(client, "program", b"x")) == (422, "upload_kind_invalid")
    large = b"-----BEGIN CERTIFICATE-----\n" + b"A" * UPLOAD_LIMITS["kamusm_root"]
    assert error(upload(client, "kamusm_root", large)) == (413, "upload_too_large")

    # Someone else's upload cannot be installed.
    staged = upload(client, "kamusm_root", ca_pem()).json()
    with TestClient(app) as other:
        login_as(other, "admin", username="mehmet")
        body = {"etag": etag(other), "upload_id": staged["upload_id"]}
        assert error(other.post(f"{V}/admin/config/tsa/roots", json=body)) == (
            404,
            "upload_not_found",
        )


def test_an_unknown_zamane_client_is_held_for_its_approval(
    client: TestClient,
    login_as: Login,
    reauthenticate: Reauth,
    installed: Installed,
    agent: FakeAgent,
) -> None:
    reauthenticate(login_as(client, "admin"))
    jar = client_jar(b"yeni surum")
    digest = hashlib.sha256(jar).hexdigest()
    preview = upload(client, "zamane_client", jar, "tss-client.jar").json()
    assert preview["zamane"]["kind"] == "jar"
    assert preview["zamane"]["jar_sha256"] == digest
    assert (preview["zamane"]["known"], preview["zamane"]["reason"]) == (False, "unknown_jar_held")

    url = f"{V}/admin/config/tsa/zamane"
    body = {"etag": etag(client), "upload_id": preview["upload_id"]}
    assert error(client.post(url, json=body)) == (422, "zamane_sha256_required")
    wrong = {**body, "expected_sha256": "0" * 64}
    assert error(client.post(url, json=wrong)) == (422, "zamane_sha256_mismatch")
    response = client.post(url, json={**body, "expected_sha256": digest.upper()})
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["guarded"] is True
    held_until = datetime.fromisoformat(accepted["effective_at"])
    assert timedelta(hours=23) < held_until - utc_now() <= timedelta(hours=24)

    agent.process()
    request = client.get(accepted["status_url"]).json()
    assert request["state"] == "pending", request
    assert "unknown_jar_held" in [warning["code"] for warning in request["warnings"]]
    document = client.get(f"{V}/admin/config").json()
    assert [jar["sha256"] for jar in document["sections"]["tsa"]["zamane"]["staged"]] == [digest]
    assert [item["request_id"] for item in document["pending"]] == [accepted["request_id"]]
    assert (installed.root / "zamane" / "web" / f"{digest}.jar").is_file()

    # The held client may wait for days: the other steps of the wizard are not kept busy …
    threshold = client.put(
        f"{V}/admin/config/tsa", json={"etag": etag(client), "credit_alert_threshold": 750}
    )
    assert threshold.status_code == 202, threshold.text
    agent.process()
    assert client.get(threshold.json()["status_url"]).json()["state"] == "active"
    assert installed.config().tsa.credit_alert_threshold == 750
    assert client.get(accepted["status_url"]).json()["state"] == "pending"
    # … but a second client waits until the first is approved or cancelled.
    second = upload(client, "zamane_client", client_jar(b"bir sonraki"), "tss-client.jar").json()
    again = {"etag": etag(client), "upload_id": second["upload_id"],
             "expected_sha256": second["zamane"]["jar_sha256"]}  # fmt: skip
    assert error(client.post(url, json=again)) == (409, "admin_section_busy")


def test_the_stamp_test_is_confirmed_limited_and_reports_the_stamp(
    client: TestClient,
    login_as: Login,
    installed: Installed,
    agent: FakeAgent,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "admin")
    url = f"{V}/admin/actions/tsa-test"
    assert error(client.post(url, json={})) == (422, "tsa_test_confirm_required")
    response = client.post(url, json={"confirm_credit": True})
    assert response.status_code == 202, response.text
    assert error(client.post(url, json={"confirm_credit": True})) == (429, "admin_rate_limited")
    agent.process()
    (task,) = agent.tasks.taken if agent.tasks else []
    assert task.kind == "tsa_test"
    done = client.get(response.json()["status_url"]).json()
    assert (done["kind"], done["state"]) == ("action.tsa_test", "succeeded")
    assert done["result"]["verified"] is True
    assert "token" not in done["result"]

    with app_session_factory() as session:  # the next one is 5 minutes later …
        for row in session.scalars(select(AdminRequest)):
            row.created_at -= timedelta(minutes=6)
        session.commit()
    installed.write_policy(tsa_test_per_day=0)  # … but no test is left for today
    limited = client.post(url, json={"confirm_credit": True})
    assert error(limited) == (429, "tsa_test_limit")
    assert int(limited.headers["retry-after"]) >= 1
