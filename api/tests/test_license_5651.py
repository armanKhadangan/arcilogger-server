"""5651 obligations are never blocked by a licence, and evidence never carries a licence mark
(licensing contract 1.7, the brief's hard constraints; tests B-T10, B-T30, B-T32).

With real evaluation, in every state that locks the paid modules — ``trial_expired``, ``invalid``
(the key was removed), ``wrong_server`` (another server, and an unreadable server id),
``expired`` and ``version_not_covered`` — the 5651 path works end to end: a lookup with its PDF
report and time stamp, the day sealed and verified, an evidence package built and downloaded, a
raw line, the audit log and its chain, the dashboard, the alerts, users and sign-in, and log
sources over the licence's limit. The dashboard summary is the same in every state.
"""

from __future__ import annotations

import gzip
import json
import zipfile
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import audit as api_audit
from arcilogger_api import evidence_limits
from arcilogger_api.evidence_jobs import EvidenceBuilder
from arcilogger_api.lookup_testing import AT, DAY, PC, build_site, iso, seal_day
from arcilogger_api.pdftext import pdf_text
from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.testing import FakeAgent, Installed
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.license import fingerprint, keys
from arcilogger_core.license.testing import all_modules_status, make_license, override_status
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import Source
from arcilogger_db.settings import set_setting

pytestmark = pytest.mark.license_live

Login = Callable[..., LoggedIn]
V = "/api/v1"
PASSWORD = "Sarı limon ağacı 1923"
LOCKED = [
    "trial_expired",
    "invalid",
    "wrong_server",
    "wrong_server_unreadable",
    "expired",
    "version_not_covered",
]
OTHER_SERVER = "AAAA-BBBB-CCCC-DDDD"
MARK = "LISANS-İŞARETİ-TEST"
GIB = 1024**3


class Usage:
    def __init__(self, free: int) -> None:
        self.total, self.used, self.free = 1000 * GIB, 1000 * GIB - free, free


@pytest.fixture(autouse=True)
def _roomy_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(evidence_limits, "disk_usage", lambda path: Usage(900 * GIB))


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


def lookup_body() -> dict[str, Any]:
    return {
        "request": {
            "authority": "İzmir Cumhuriyet Başsavcılığı",
            "file_no": "2026/5651-1",
            "request_date": "2026-10-13",
            "reason": "Soruşturma kapsamında IP kullanıcısının tespiti.",
        },
        "query": {"at": iso(AT), "internal_ip": PC},
    }


def lock(state: str, licence: Any, monkeypatch: pytest.MonkeyPatch) -> str:
    """Bring the installation into ``state`` (a licence limited to one log source); returns
    the state ``GET /license`` must report."""
    licence.trial_over()
    if state == "trial_expired":
        return state
    server = OTHER_SERVER if state == "wrong_server" else licence.server_id
    options: dict[str, Any] = {"server_ids": [server], "log_sources": 1, "users": 10}
    if state == "expired":
        options["valid_until"] = utc_now().date() - timedelta(days=3)
    if state == "version_not_covered":
        options["maintenance_until"] = date(2026, 1, 1)
    licence.store(make_license(**options))
    if state == "invalid":
        monkeypatch.setattr(keys, "TRUSTED_KEYS", {})
    if state == "wrong_server_unreadable":

        def unreadable() -> Any:
            raise fingerprint.FingerprintError("GetSystemFirmwareTable failed")

        monkeypatch.setattr(fingerprint, "IDENTITY_PROVIDER", unreadable)
        fingerprint.clear_cache()
    licence.invalidate()
    return "wrong_server" if state == "wrong_server_unreadable" else state


def finish(app: FastAPI) -> None:
    builder = getattr(app.state, "arci_evidence_builder", None)
    assert isinstance(builder, EvidenceBuilder)
    assert builder.wait(timeout=120)


def build_package(client: TestClient, app: FastAPI, **body: Any) -> dict[str, Any]:
    response = client.post(
        f"{V}/evidence", json={"from": DAY.isoformat(), "to": DAY.isoformat(), **body}
    )
    assert response.status_code == 202, response.text
    finish(app)
    package_id = response.json()["package"]["id"]
    detail = client.get(f"{V}/evidence/{package_id}").json()
    assert detail["status"] == "ready", detail
    return dict(detail)


def recent_sources(factory: sessionmaker[Session]) -> None:
    """The two senders of the site delivered logs just now (two log sources counted)."""
    with factory() as session:
        session.execute(update(Source).values(last_seen=datetime.now(UTC)))
        session.commit()


def dashboard(client: TestClient) -> dict[str, Any]:
    response = client.get(f"{V}/dashboard/summary")
    assert response.status_code == 200, response.text
    summary: dict[str, Any] = response.json()
    summary.pop("generated_at")
    return summary


def same_dashboard(client: TestClient, licence: Any) -> None:
    """B-T32: the summary (health boxes) is the same as with every module on."""
    licence.invalidate()
    # One clock for both calls: the 60-minute sparkline must not move between them.
    with patch("arcilogger_api.routers.dashboard.utc_now", return_value=utc_now()):
        locked = dashboard(client)
        with override_status(all_modules_status()):
            licence.invalidate()
            assert dashboard(client) == locked
    licence.invalidate()


@pytest.mark.parametrize("state", LOCKED)
def test_the_5651_path_works_in_every_locked_state(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    make_user: Callable[..., Any],
    licence: Any,
    api_config: Config,
    layout: Layout,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    build_site(app_session_factory, layout)
    recent_sources(app_session_factory)
    with app_session_factory() as session:
        set_setting(session, "lookup.report_timestamp", True, updated_by="admin")
        session.commit()
    licence.admin(client, login_as, password=PASSWORD)
    expected = lock(state, licence, monkeypatch)
    document = client.get(f"{V}/license").json()
    assert document["status"] == expected
    assert [m["key"] for m in document["modules"] if m["enabled"]] == ["core_5651"]
    assert client.get(f"{V}/usage/company").json()["error"]["code"] == "module_not_licensed"

    same_dashboard(client, licence)

    # Lookup, the official PDF report and its time stamp.
    created = client.post(f"{V}/lookup/5651", json=lookup_body())
    assert created.status_code == 201, created.text
    lookup_id = created.json()["id"]
    assert client.get(f"{V}/lookup/{lookup_id}").status_code == 200
    assert client.get(f"{V}/lookups").status_code == 200
    report = client.get(f"{V}/lookup/{lookup_id}/report.pdf")
    assert report.status_code == 200, report.text
    assert report.headers["content-type"] == "application/pdf"
    assert client.get(f"{V}/lookup/{lookup_id}/report.pdf.zd").status_code == 200

    # The day sealed (the nightly task), its seal shown and verified.
    seal_day(api_config, layout, DAY)
    assert client.get(f"{V}/seals/{DAY.isoformat()}").status_code == 200
    assert client.get(f"{V}/seals/calendar").status_code == 200
    verified = client.post(f"{V}/seals/{DAY.isoformat()}/verify")
    assert verified.status_code == 200, verified.text
    assert verified.json()["ok"] is True

    # An evidence package of the day, built and downloaded.
    package = build_package(client, app)
    download = client.get(f"{V}/evidence/{package['id']}/download")
    assert download.status_code == 200, download.text

    # A raw line, the audit log and its chain.
    raw = client.get(f"{V}/raw", params={"source": "fortigate", "day": DAY.isoformat(), "line": 1})
    assert raw.status_code == 200, raw.text
    assert client.get(f"{V}/audit").status_code == 200
    chain = client.get(f"{V}/audit/verify")
    assert chain.status_code == 200
    assert chain.json()["ok"] is True

    # Alerts, users within the limit, sign-in.
    assert client.get(f"{V}/alerts").status_code == 200
    assert client.get(f"{V}/alerts/summary").status_code == 200
    assert client.get(f"{V}/users").status_code == 200
    created_user = client.post(
        f"{V}/users", json={"username": "yeni", "display_name": "Yeni", "role": "viewer"}
    )
    assert created_user.status_code == 201, created_user.text
    make_user("auditor", username="denetci", password=PASSWORD)
    other = TestClient(app, base_url="http://testserver")
    other.get(f"{V}/health")
    other.headers["X-CSRF-Token"] = other.cookies["arci_csrf"]
    signed_in = other.post(f"{V}/auth/login", json={"username": "denetci", "password": PASSWORD})
    assert signed_in.status_code == 200, signed_in.text

    # Log sources over the licence's limit: nothing is refused, the banner data says so.
    licence.invalidate()
    limits = client.get(f"{V}/license").json()["limits"]["log_sources"]
    assert limits["used"] == 2
    if state != "trial_expired" and state != "invalid":
        assert (limits["max"], limits["exceeded"]) == (1, True)
    assert client.get(f"{V}/devices").status_code == 200
    assert client.get(f"{V}/persons").status_code == 200
    same_dashboard(client, licence)


@pytest.mark.parametrize("state", ["trial_expired", "invalid", "expired"])
def test_browser_administration_works_in_every_locked_state(
    client: TestClient,
    login_as: Login,
    licence: Any,
    installed: Installed,
    agent: FakeAgent,
    monkeypatch: pytest.MonkeyPatch,
    state: str,
) -> None:
    """No router of the browser administration uses ``require_module`` (ADR-0014): a change of
    the machine configuration, "Kontör sorgula" and the database settings work whatever the
    licence says."""
    licence.admin(client, login_as, password=PASSWORD)
    lock(state, licence, monkeypatch)
    assert client.get(f"{V}/admin/config").status_code == 200
    assert client.get(f"{V}/admin/status").json()["agent"]["state"] == "ok"
    tag = client.get(f"{V}/admin/config").json()["etags"]["general"]
    changed = client.put(
        f"{V}/admin/config/general", json={"etag": tag, "site_name": "Örnek Lojistik A.Ş."}
    )
    assert changed.status_code == 202, changed.text
    credit = client.post(f"{V}/credits/query")
    assert credit.status_code == 202, credit.text
    agent.process()
    assert client.get(changed.json()["status_url"]).json()["state"] == "active"
    assert client.get(credit.json()["status_url"]).json()["state"] == "succeeded"
    settings = client.patch(
        f"{V}/settings",
        json={"changes": [{"key": "lookup.tolerance_seconds", "old": 60, "value": 90}]},
    )
    assert settings.status_code == 200, settings.text
    assert client.get(f"{V}/settings").status_code == 200


# -- B-T30 never alter evidence -------------------------------------------------------------------


def _texts_of(path: Path) -> dict[str, bytes]:
    """Every member of a package, decompressed when it is gzip."""
    found: dict[str, bytes] = {}
    with zipfile.ZipFile(path) as archive:
        for name in archive.namelist():
            data = archive.read(name)
            if name.endswith(".gz"):
                data = gzip.decompress(data)
            found[name] = data
    return found


def test_no_licence_mark_in_evidence(
    app: FastAPI,
    client: TestClient,
    login_as: Login,
    licence: Any,
    api_config: Config,
    layout: Layout,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_site(app_session_factory, layout)
    with app_session_factory() as session:
        set_setting(session, "lookup.report_timestamp", True, updated_by="admin")
        session.commit()
    licence.admin(client, login_as)
    # The licence is installed "on the day": its audit entries go into that day's audit spool.
    on_the_day = datetime.combine(DAY, datetime.min.time(), tzinfo=UTC) + timedelta(hours=12)
    data = make_license(
        server_ids=[licence.server_id], licensee=MARK, modules=("core_5651", "usage_analytics")
    )
    with monkeypatch.context() as clock:
        clock.setattr(api_audit, "utc_now", lambda: on_the_day)
        response = licence.upload(client, data)
    assert response.status_code == 200, response.text
    assert response.json()["licensee"] == MARK
    marks = {
        "licensee": MARK.encode(),
        "license_id": b"ARCI-L-20261001-TEST0001",
        "server_id": licence.server_id.encode(),
    }
    spooled = (layout.spool / "_audit" / f"{DAY.isoformat()}.jsonl").read_bytes()
    assert marks["license_id"] in spooled  # the install is an audit entry of the day …
    assert marks["licensee"] not in spooled  # … without the licensee or the server id
    assert marks["server_id"] not in spooled

    lookup_id = client.post(f"{V}/lookup/5651", json=lookup_body()).json()["id"]
    pdf = client.get(f"{V}/lookup/{lookup_id}/report.pdf").content
    token = client.get(f"{V}/lookup/{lookup_id}/report.pdf.zd").content
    text = pdf_text(pdf)
    for value in marks.values():
        assert value not in pdf
        assert value not in token
        assert value.decode() not in text
    seal_day(api_config, layout, DAY)
    manifest = layout.manifest(DAY).read_bytes()
    for value in marks.values():
        assert value not in manifest

    plain = build_package(client, app)
    for name, content in _texts_of(layout.exports / plain["file"]).items():
        for value in marks.values():
            assert value not in content, (name, value)
    with_audit = build_package(client, app, include_audit_log=True)
    members = _texts_of(layout.exports / with_audit["file"])
    audit_log = f"vault/{DAY.year}/{DAY.month:02d}/{DAY.day:02d}/audit.jsonl.gz"
    assert marks["license_id"] in members[audit_log]  # only the audit log names the licence
    for name, content in members.items():
        assert marks["licensee"] not in content, name
        assert marks["server_id"] not in content, name
        if name != audit_log:
            assert marks["license_id"] not in content, name
    assert json.loads(members["paket.json"])["site"] != MARK
