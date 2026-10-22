"""A package made from a 5651 lookup contains the lookup's official report (spec 9.6 "and the PDF
report"): the stored bytes — the same the report download serves — with its time stamp when it
has one, never a second rendering; one SHA-256 in ``paket.json``, ``lookup_reports`` and the
audit log."""

from __future__ import annotations

import hashlib
import json
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import evidence_jobs, evidence_limits
from arcilogger_api.errors import ApiError
from arcilogger_api.evidence_jobs import (
    NOTE_STAMP_FAILED,
    NOTE_STAMP_ROOTS,
    NOTE_STAMP_UNVERIFIED,
    EvidenceBuilder,
    ReportFiles,
)
from arcilogger_api.lookup_testing import AT, DAY, PC, accept_privacy, build_site, iso, seal_day
from arcilogger_api.reports import store
from arcilogger_api.routers import evidence as evidence_router
from arcilogger_api.security import CurrentUser, hash_token
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.evidence import run_verifier
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.tsa import mock_tsa
from arcilogger_core.tsa.base import TimestampProvider, VerifyResult
from arcilogger_core.tsa.mock import MockFailure, MockTsaProvider
from arcilogger_db.models import AuditLog, EvidencePackage, Lookup, LookupReport, UserSession
from arcilogger_db.settings import set_setting

Login = Callable[..., LoggedIn]
API = "/api/v1/evidence"


class Usage(NamedTuple):
    """What ``shutil.disk_usage`` returns."""

    total: int
    used: int
    free: int


@pytest.fixture(autouse=True)
def _roomy_disk_and_unlock(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """A large, mostly empty data disk (the test machine may be nearly full)."""
    total = 1000 * 1024**3
    monkeypatch.setattr(evidence_limits, "disk_usage", lambda path: Usage(total, 0, total))
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


@pytest.fixture
def sealed_site(
    app_session_factory: sessionmaker[Session], layout: Layout, api_config: Config
) -> None:
    build_site(app_session_factory, layout)
    seal_day(api_config, layout, DAY)


def admin(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str = "admin"
) -> LoggedIn:
    """An administrator (or ``role``) with the KVKK notice accepted and a recent
    re-authentication."""
    who = login_as(client, role, display_name="Tolga Yönetici")
    accept_privacy(factory, who.user_id)
    with factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(who.session_token))
            .values(reauthenticated_at=datetime.now(UTC))
        )
        session.commit()
    return who


def run_lookup(client: TestClient) -> int:
    body = {
        "request": {
            "authority": "İstanbul Cumhuriyet Başsavcılığı",
            "file_no": "2026/77",
            "request_date": "2026-10-13",
            "reason": "Kanıt paketi testi",
        },
        "query": {"at": iso(AT), "internal_ip": PC},
    }
    response = client.post("/api/v1/lookup/5651", json=body)
    assert response.status_code == 201, response.text
    lookup_id: int = response.json()["id"]
    return lookup_id


def build(client: TestClient, app: FastAPI, **body: Any) -> dict[str, Any]:
    response = client.post(API, json=body)
    assert response.status_code == 202, response.text
    builder = getattr(app.state, "arci_evidence_builder", None)
    assert isinstance(builder, EvidenceBuilder)
    assert builder.wait(timeout=120)
    detail: dict[str, Any] = client.get(f"{API}/{response.json()['package']['id']}").json()
    assert detail["status"] == "ready", detail["error"]
    return detail


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def stored_report(factory: sessionmaker[Session], lookup_id: int) -> LookupReport:
    with factory() as session:
        row = session.scalars(select(LookupReport).where(LookupReport.lookup_id == lookup_id)).one()
        session.expunge(row)
        return row


def unpack(layout: Layout, detail: dict[str, Any], target: Path) -> Path:
    with zipfile.ZipFile(layout.exports / detail["file"]) as archive:
        archive.extractall(target)
    return target


@pytest.mark.usefixtures("sealed_site")
def test_the_package_renders_the_report_once_and_carries_the_stored_bytes(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    tmp_path: Path,
) -> None:
    who = admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    detail = build(client, app, lookup_id=lookup_id)
    # Nobody had downloaded the report: the package request rendered and stored it, once.
    report = stored_report(app_session_factory, lookup_id)
    (created,) = audits(app_session_factory, "lookup.report")
    assert created.actor_username == who.username
    assert created.details["sha256"] == report.sha256
    assert created.details["file"] == report.path
    stored_bytes = (layout.exports / report.path).read_bytes()
    assert hashlib.sha256(stored_bytes).hexdigest() == report.sha256

    folder = unpack(layout, detail, tmp_path / "pkg")
    name = f"ARCILOGGER-5651-rapor-{lookup_id}.pdf"
    assert (folder / "rapor" / name).read_bytes() == stored_bytes
    assert not (folder / "rapor" / f"{name}.zd").exists()  # not time-stamped (default)
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["report"] == f"rapor/{name}"
    assert index["report_token"] is None
    listed = {entry["path"]: entry for entry in index["files"]}
    assert listed[f"rapor/{name}"]["sha256"] == report.sha256
    assert listed[f"rapor/{name}"]["bytes"] == report.size

    # One hash everywhere: lookup_reports, lookup.report, evidence.request, evidence.create.
    (requested,) = audits(app_session_factory, "evidence.request")
    (finished,) = audits(app_session_factory, "evidence.create")
    expected = {
        "lookup_id": lookup_id,
        "file": f"rapor/{name}",
        "sha256": report.sha256,
        "size": report.size,
        "token_file": None,
        "token_sha256": None,
        "token_note": None,
    }
    assert requested.details["report"] == expected
    assert finished.details["report"] == expected
    assert finished.details["sha256"] == detail["sha256"]

    # The download afterwards serves the same bytes and renders nothing new.
    response = client.get(f"/api/v1/lookup/{lookup_id}/report.pdf")
    assert response.status_code == 200
    assert response.content == stored_bytes
    assert len(audits(app_session_factory, "lookup.report")) == 1

    # The standalone verifier checks the PDF against paket.json and shows its hash.
    result = run_verifier(folder, "--json", timeout=300)
    verdict = json.loads(result.stdout)
    assert result.returncode == 0, [f for f in verdict["findings"] if f["level"] == "fail"]
    (hash_line,) = [f["tr"] for f in verdict["findings"] if f["code"] == "report-hash"]
    assert report.sha256 in hash_line

    # A second package of the same lookup carries the same bytes.
    second = build(client, app, lookup_id=lookup_id, content="extract")
    again = unpack(layout, second, tmp_path / "pkg2")
    assert (again / "rapor" / name).read_bytes() == stored_bytes
    assert len(audits(app_session_factory, "lookup.report")) == 1


@pytest.mark.usefixtures("sealed_site")
def test_a_stamped_report_goes_in_with_its_token(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    tmp_path: Path,
) -> None:
    with app_session_factory() as session:
        set_setting(session, "lookup.report_timestamp", True, updated_by="admin")
        session.commit()
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    downloaded = client.get(f"/api/v1/lookup/{lookup_id}/report.pdf").content  # stored + stamped
    report = stored_report(app_session_factory, lookup_id)
    assert report.timestamp_status == "stamped"
    stamp = report.timestamp or {}
    detail = build(client, app, lookup_id=lookup_id)
    folder = unpack(layout, detail, tmp_path / "pkg")
    name = f"ARCILOGGER-5651-rapor-{lookup_id}.pdf"
    assert (folder / "rapor" / name).read_bytes() == downloaded
    token = (folder / "rapor" / f"{name}.zd").read_bytes()
    assert hashlib.sha256(token).hexdigest() == stamp["token_sha256"]
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["report_token"] == f"rapor/{name}.zd"
    (finished,) = audits(app_session_factory, "evidence.create")
    assert finished.details["report"]["token_sha256"] == stamp["token_sha256"]
    assert len(audits(app_session_factory, "lookup.report")) == 1  # the download made it
    result = run_verifier(folder, "--json", timeout=300)
    verdict = json.loads(result.stdout)
    assert result.returncode == 0, [f for f in verdict["findings"] if f["level"] == "fail"]
    assert "report-ok" in {f["code"] for f in verdict["findings"] if f["level"] == "ok"}


@pytest.mark.usefixtures("sealed_site")
def test_a_damaged_stored_report_blocks_the_package(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
) -> None:
    """The report is never rendered a second time: a changed stored file refuses the package."""
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    assert client.get(f"/api/v1/lookup/{lookup_id}/report.pdf").status_code == 200
    report = stored_report(app_session_factory, lookup_id)
    path = layout.exports / report.path
    path.write_bytes(path.read_bytes() + b"%changed\n")
    response = client.post(API, json={"lookup_id": lookup_id})
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == "report_damaged"
    with app_session_factory() as session:
        assert session.scalars(select(EvidencePackage)).all() == []
    assert audits(app_session_factory, "evidence.request") == []


@pytest.mark.usefixtures("sealed_site")
def test_a_report_changed_while_the_package_waited_fails_the_build(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    real = EvidenceBuilder._report_bytes

    def changed_meanwhile(self: EvidenceBuilder, ref: Any, roots: Any, *rule: Any) -> ReportFiles:
        report = stored_report(app_session_factory, lookup_id)
        path = layout.exports / report.path
        path.write_bytes(path.read_bytes() + b"%changed\n")
        return real(self, ref, roots, *rule)

    monkeypatch.setattr(EvidenceBuilder, "_report_bytes", changed_meanwhile)
    response = client.post(API, json={"lookup_id": lookup_id})
    assert response.status_code == 202, response.text
    builder = getattr(app.state, "arci_evidence_builder", None)
    assert isinstance(builder, EvidenceBuilder)
    assert builder.wait(timeout=120)
    detail = client.get(f"{API}/{response.json()['package']['id']}").json()
    assert detail["status"] == "failed"
    assert "report_damaged" in detail["error"]
    (finished,) = audits(app_session_factory, "evidence.create")
    assert finished.details["status"] == "failed"


def failing_tsa(monkeypatch: pytest.MonkeyPatch, failure: MockFailure) -> None:
    """The report store stamps with a mock TSA that fails in ``failure``'s way."""

    def provider(config: Config, layout: Layout, secrets: Any) -> TimestampProvider:
        tsa = mock_tsa(layout)
        tsa.failure = failure
        return MockTsaProvider(tsa)

    monkeypatch.setattr(store, "make_provider", provider)


def package_without_token(
    client: TestClient,
    app: FastAPI,
    factory: sessionmaker[Session],
    layout: Layout,
    lookup_id: int,
    target: Path,
    note: str,
) -> dict[str, Any]:
    """Build a package of ``lookup_id``; the report must be in it without a token, with ``note``
    in paket.json, the audit entries and as the verifier's warning (which does not fail)."""
    detail = build(client, app, lookup_id=lookup_id)
    report = stored_report(factory, lookup_id)
    folder = unpack(layout, detail, target)
    name = f"ARCILOGGER-5651-rapor-{lookup_id}.pdf"
    assert (folder / "rapor" / name).read_bytes() == (layout.exports / report.path).read_bytes()
    assert not (folder / "rapor" / f"{name}.zd").exists()
    index = json.loads((folder / "paket.json").read_bytes())
    assert index["report"] == f"rapor/{name}"
    assert index["report_token"] is None
    assert index["report_note"] == note
    guide = (folder / "DOGRULAMA.txt").read_text(encoding="utf-8-sig")
    assert "Raporun zaman damgası pakette yok" in guide
    (finished,) = audits(factory, "evidence.create")
    assert finished.details["status"] == "ready"
    assert finished.details["report"]["sha256"] == report.sha256
    assert finished.details["report"]["token_sha256"] is None
    assert finished.details["report"]["token_note"] == note
    result = run_verifier(folder, "--json", timeout=300)
    verdict = json.loads(result.stdout)
    assert result.returncode == 0, [f for f in verdict["findings"] if f["level"] == "fail"]
    warnings = {f["code"]: f for f in verdict["findings"] if f["level"] == "warn"}
    assert note in warnings["report-token-omitted"]["tr"]
    return detail


@pytest.mark.parametrize("failure", [MockFailure.BAD_SIGNATURE, MockFailure.EXPIRED_CERT])
@pytest.mark.usefixtures("sealed_site")
def test_a_stored_time_stamp_that_did_not_verify_stays_out_of_the_package(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: MockFailure,
) -> None:
    """The stored report is never rendered again: a token that did not verify when it was stored
    must not fail every package of the lookup. The report goes in, its token stays out, and the
    package says why."""
    with app_session_factory() as session:
        set_setting(session, "lookup.report_timestamp", True, updated_by="admin")
        session.commit()
    failing_tsa(monkeypatch, failure)
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    assert client.get(f"/api/v1/lookup/{lookup_id}/report.pdf").status_code == 200
    report = stored_report(app_session_factory, lookup_id)
    assert report.timestamp_status == "stamped"
    assert (report.timestamp or {})["verified"] is False
    package_without_token(
        client, app, app_session_factory, layout, lookup_id, tmp_path / "pkg", NOTE_STAMP_UNVERIFIED
    )
    (requested,) = audits(app_session_factory, "evidence.request")
    assert requested.details["report"]["token_note"] == NOTE_STAMP_UNVERIFIED


@pytest.mark.usefixtures("sealed_site")
def test_a_report_whose_time_stamp_failed_goes_in_with_a_note(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with app_session_factory() as session:
        set_setting(session, "lookup.report_timestamp", True, updated_by="admin")
        session.commit()
    failing_tsa(monkeypatch, MockFailure.UNREACHABLE)
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    # The package request renders the report itself (nobody downloaded it); the stamp fails.
    package_without_token(
        client, app, app_session_factory, layout, lookup_id, tmp_path / "pkg", NOTE_STAMP_FAILED
    )
    assert stored_report(app_session_factory, lookup_id).timestamp_status == "failed"
    (created,) = audits(app_session_factory, "lookup.report")
    assert created.details["timestamp"]["status"] == "failed"


@pytest.mark.usefixtures("sealed_site")
def test_a_token_that_no_longer_verifies_against_the_package_roots_stays_out(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verified when stored, but the roots changed since: checked again when the package is
    built, against the roots the package carries."""
    with app_session_factory() as session:
        set_setting(session, "lookup.report_timestamp", True, updated_by="admin")
        session.commit()
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    assert client.get(f"/api/v1/lookup/{lookup_id}/report.pdf").status_code == 200
    assert (stored_report(app_session_factory, lookup_id).timestamp or {})["verified"] is True
    checked: list[Path | None] = []

    def other_roots(token: bytes, **kwargs: Any) -> VerifyResult:
        checked.append(kwargs.get("file_path"))
        return VerifyResult(ok=False, errors=("no trusted root",))

    monkeypatch.setattr(evidence_jobs, "verify_token", other_roots)
    package_without_token(
        client, app, app_session_factory, layout, lookup_id, tmp_path / "pkg", NOTE_STAMP_ROOTS
    )
    report = stored_report(app_session_factory, lookup_id)
    assert checked == [layout.exports / report.path]
    # The request still named the token (it had verified); the build left it out.
    (requested,) = audits(app_session_factory, "evidence.request")
    assert requested.details["report"]["token_sha256"] == (report.timestamp or {})["token_sha256"]


@pytest.mark.usefixtures("sealed_site")
def test_the_report_is_stored_before_the_evidence_lock_is_taken(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rendering and time-stamping a new report can take seconds: it happens (and is committed,
    like a download) before the request takes the lock every other package request waits on. A
    request that a limit then refuses keeps the stored report, and queues nothing."""
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    stored_at_lock: list[bool] = []

    def lock_seen_from_outside(db: Session) -> None:
        with app_session_factory() as other:  # another connection: sees committed rows only
            found = other.scalar(select(LookupReport.id).where(LookupReport.lookup_id == lookup_id))
            stored_at_lock.append(found is not None)

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise ApiError(429, "too_many_requests", "Çok fazla istek.", "Too many requests.")

    monkeypatch.setattr(evidence_router, "lock", lock_seen_from_outside)
    monkeypatch.setattr(evidence_router, "check_request", refuse)
    response = client.post(API, json={"lookup_id": lookup_id})
    assert response.status_code == 429, response.text
    assert stored_at_lock == [True]
    report = stored_report(app_session_factory, lookup_id)
    (created,) = audits(app_session_factory, "lookup.report")
    assert created.details["sha256"] == report.sha256
    with app_session_factory() as session:
        assert session.scalars(select(EvidencePackage)).all() == []
    assert audits(app_session_factory, "evidence.request") == []
    # The download afterwards serves the report that was stored then.
    pdf = client.get(f"/api/v1/lookup/{lookup_id}/report.pdf")
    assert hashlib.sha256(pdf.content).hexdigest() == report.sha256


@pytest.mark.usefixtures("sealed_site")
def test_packages_follow_the_lookup_visibility(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    """A package of a lookup carries its report (reason, result, person chain): whoever may not
    see the lookup (non-admins: only their own) does not see or download its packages either."""
    with TestClient(app) as boss, TestClient(app) as reviewer:
        admin(boss, login_as, app_session_factory)
        auditor = admin(reviewer, login_as, app_session_factory, role="auditor")
        theirs_lookup = run_lookup(boss)
        mine_lookup = run_lookup(reviewer)
        theirs = build(boss, app, lookup_id=theirs_lookup)
        mine = build(boss, app, lookup_id=mine_lookup)  # admins may package any lookup
        manual = build(boss, app, **{"from": DAY.isoformat(), "to": DAY.isoformat()})

        # Not listed, also not when asked for by lookup number.
        listed = [item["id"] for item in reviewer.get(API).json()["items"]]
        assert listed == [manual["id"], mine["id"]]
        assert reviewer.get(API, params={"lookup_id": theirs_lookup}).json()["items"] == []
        own = reviewer.get(API, params={"lookup_id": mine_lookup}).json()["items"]
        assert [item["id"] for item in own] == [mine["id"]]
        # Detail and download answer 404 - like /lookup/{id} - and nothing is counted.
        assert reviewer.get(f"/api/v1/lookup/{theirs_lookup}").status_code == 404
        for path in (f"{API}/{theirs['id']}", f"{API}/{theirs['id']}/download"):
            response = reviewer.get(path)
            assert response.status_code == 404, path
            assert response.json()["error"]["code"] == "not_found"
        assert audits(app_session_factory, "evidence.download") == []
        with app_session_factory() as session:
            assert session.get_one(EvidencePackage, theirs["id"]).download_count == 0
        # The own lookup's package and the package chosen by days stay readable.
        for detail in (mine, manual):
            assert reviewer.get(f"{API}/{detail['id']}").status_code == 200
            assert reviewer.get(f"{API}/{detail['id']}/download").status_code == 200
        downloads = audits(app_session_factory, "evidence.download")
        assert [entry.actor_username for entry in downloads] == [auditor.username] * 2

        # Administrators see and download every package.
        everything = [item["id"] for item in boss.get(API).json()["items"]]
        assert everything == [manual["id"], mine["id"], theirs["id"]]
        assert boss.get(f"{API}/{theirs['id']}/download").status_code == 200

        # A deleted lookup (retention) sets the column to NULL; the package still is no manual
        # one and stays hidden from everybody but administrators.
        with app_session_factory() as session:
            session.execute(delete(Lookup).where(Lookup.id == theirs_lookup))
            session.commit()
            assert session.get_one(EvidencePackage, theirs["id"]).lookup_id is None
        listed = [item["id"] for item in reviewer.get(API).json()["items"]]
        assert listed == [manual["id"], mine["id"]]
        assert reviewer.get(f"{API}/{theirs['id']}").status_code == 404
        assert reviewer.get(f"{API}/{theirs['id']}/download").status_code == 404
        assert boss.get(f"{API}/{theirs['id']}").status_code == 200


@pytest.mark.usefixtures("sealed_site")
def test_packages_with_the_audit_log_are_hidden_from_readers_who_see_pseudonyms(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    """A package with the audit log holds the stored entries: the real usage targets
    (``usage:device:<mac>``) and their hashes, which ``GET /audit`` withholds from a reader who
    sees pseudonyms. While ``usage.pseudonymize_anonymous_role`` is on, the auditor neither sees
    nor downloads such a package (404, like a package of another user's lookup)."""
    with TestClient(app) as boss, TestClient(app) as reviewer:
        admin(boss, login_as, app_session_factory)
        admin(reviewer, login_as, app_session_factory, role="auditor")
        own_lookup = run_lookup(reviewer)
        days = {"from": DAY.isoformat(), "to": DAY.isoformat()}
        plain = build(boss, app, **days)
        manual = build(boss, app, **days, include_audit_log=True)
        own = build(boss, app, lookup_id=own_lookup, include_audit_log=True)
        every = [own["id"], manual["id"], plain["id"]]
        # Names (the default): the auditor sees and downloads all three, as before.
        assert [item["id"] for item in reviewer.get(API).json()["items"]] == every
        assert reviewer.get(f"{API}/{manual['id']}").status_code == 200

        with app_session_factory() as session:
            set_setting(session, "usage.pseudonymize_anonymous_role", True, updated_by="test")
            session.commit()
        assert [item["id"] for item in reviewer.get(API).json()["items"]] == [plain["id"]]
        assert reviewer.get(API, params={"lookup_id": own_lookup}).json()["items"] == []
        for detail in (manual, own):
            for path in (f"{API}/{detail['id']}", f"{API}/{detail['id']}/download"):
                response = reviewer.get(path)
                assert response.status_code == 404, path
                assert response.json()["error"]["code"] == "not_found"
        assert audits(app_session_factory, "evidence.download") == []
        assert reviewer.get(f"{API}/{plain['id']}/download").status_code == 200
        # Administrators see names, and every package.
        assert [item["id"] for item in boss.get(API).json()["items"]] == every
        assert boss.get(f"{API}/{manual['id']}/download").status_code == 200


@pytest.mark.usefixtures("sealed_site")
def test_a_package_is_never_requested_from_a_lookup_the_requester_may_not_see(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    """Creating packages is admin-only today; should a role that does not see every lookup get
    ``evidence.create`` (roles.md open question), the lookup rule still holds."""
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    with app_session_factory() as session:
        lookup = session.get_one(Lookup, lookup_id)
        assert lookup.user_id is not None
        other = CurrentUser(
            id=lookup.user_id + 1000,
            username="someone",
            display_name="Someone",
            role="auditor",
            permissions=frozenset({"evidence.create"}),
            must_change_password=False,
            mfa_passed=True,
            session_token_hash="-",
            reauthenticated_at=None,
        )
        body = evidence_router.EvidenceCreate(lookup_id=lookup_id)
        with pytest.raises(ApiError) as refused:
            evidence_router._plan_from_lookup(session, other, body)
        assert (refused.value.status_code, refused.value.code) == (404, "not_found")
        owner = replace(other, id=lookup.user_id)
        assert evidence_router._plan_from_lookup(session, owner, body).lookup is not None


@pytest.mark.usefixtures("sealed_site")
def test_the_report_token_is_checked_with_the_packages_eku_rule(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stored report's token is checked under the EKU rule the package's sealed manifests
    record (``tsa.require_critical_eku``, finding 7) — the rule its verifier applies."""
    with app_session_factory() as session:
        set_setting(session, "lookup.report_timestamp", True, updated_by="admin")
        session.commit()
    admin(client, login_as, app_session_factory)
    lookup_id = run_lookup(client)
    assert client.get(f"/api/v1/lookup/{lookup_id}/report.pdf").status_code == 200
    from arcilogger_core.tsa.verify import verify_token as real

    rules: list[Any] = []

    def recording(token: bytes, **kwargs: Any) -> VerifyResult:
        rules.append(kwargs.get("require_critical_eku"))
        return real(token, **kwargs)

    monkeypatch.setattr(evidence_jobs, "verify_token", recording)
    monkeypatch.setattr(evidence_jobs, "requires_critical_eku", lambda layout, days: False)
    build(client, app, lookup_id=lookup_id)
    assert rules == [False]
