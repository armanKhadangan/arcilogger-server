"""Evidence packages through the API (spec 9.6, 12.3 page 10, 13).

Days are sealed by the real sealing code with the mock TSA; every package the API builds is
then checked by the standalone verifier the way an auditor runs it (``python -I -S verify.py``
in a separate process).
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import threading
import zipfile
from collections.abc import Callable, Iterator
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import evidence_jobs, evidence_limits, security
from arcilogger_api.auth.accounts import accept_privacy
from arcilogger_api.evidence_jobs import INTERRUPTED, EvidenceBuilder
from arcilogger_api.routers.evidence import lookup_refs
from arcilogger_api.security import hash_token
from arcilogger_api.testing import EXAMPLE_SITE_NAME, LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.evidence import (
    EvidenceError,
    RawRef,
    extract_archive,
    is_encrypted,
    run_verifier,
    verifier_sha256,
)
from arcilogger_core.layout import Layout
from arcilogger_db.models import AuditLog, EvidenceDownload, EvidencePackage, Lookup, UserSession
from arcilogger_db.settings import set_setting
from arcilogger_worker.lookup.types import (
    AttributionChain,
    DeviceInfo,
    EvidenceRef,
    LeaseInfo,
    LookupOutcome,
    LookupRequest,
    NatMatch,
    TrafficInfo,
)
from arcilogger_worker.lookup.types import RawRef as LookupRawRef
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

DAYS = [date(2026, 9, 10), date(2026, 9, 11), date(2026, 9, 12)]
API = "/api/v1/evidence"
Login = Callable[..., LoggedIn]
GIB = 1024**3


# -- fixtures and helpers -------------------------------------------------------------------------


class Usage(NamedTuple):
    """What ``shutil.disk_usage`` returns."""

    total: int
    used: int
    free: int


def disk(free_gib: float, total_gib: float = 1000) -> Usage:
    total, free = int(total_gib * GIB), int(free_gib * GIB)
    return Usage(total, total - free, free)


@pytest.fixture(autouse=True)
def roomy_disk(monkeypatch: pytest.MonkeyPatch) -> None:
    """A large, mostly empty data disk: the machine running the tests may be nearly full."""
    monkeypatch.setattr(evidence_limits, "disk_usage", lambda path: disk(900))


def setting(factory: sessionmaker[Session], key: str, value: Any) -> None:
    with factory() as session:
        set_setting(session, key, value, updated_by="test")
        session.commit()


@pytest.fixture
def vault(api_config: Config) -> Iterator[SealedVault]:
    sealed = make_vault(api_config.data_dir, tsa_now=at_site(DAYS[0] - timedelta(days=30)))
    sealed.seal_days(DAYS)
    yield sealed
    sealed.unlock_all()


def recent(factory: sessionmaker[Session], who: LoggedIn, *, minutes_ago: int = 0) -> None:
    """The session re-authenticated ``minutes_ago`` (``POST /auth/reauth``)."""
    with factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(who.session_token))
            .values(reauthenticated_at=datetime.now(UTC) - timedelta(minutes=minutes_ago))
        )
        session.commit()


def notice(factory: sessionmaker[Session], who: LoggedIn) -> None:
    with factory() as session:
        accept_privacy(session, who.user_id, "1", ip=None)
        session.commit()


def signed_in(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str = "admin"
) -> LoggedIn:
    """A user with a recent re-authentication and the accepted KVKK notice."""
    who = login_as(client, role)
    recent(factory, who)
    notice(factory, who)
    return who


def builder(app: FastAPI) -> EvidenceBuilder:
    found = getattr(app.state, "arci_evidence_builder", None)
    assert isinstance(found, EvidenceBuilder)
    return found


def finish(app: FastAPI) -> None:
    assert builder(app).wait(timeout=120)


def package(factory: sessionmaker[Session], package_id: int) -> EvidencePackage:
    with factory() as session:
        row = session.get(EvidencePackage, package_id)
        assert row is not None
        session.expunge(row)
        return row


def audits(factory: sessionmaker[Session], prefix: str = "evidence.") -> list[AuditLog]:
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.action.startswith(prefix)).order_by(AuditLog.id)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def create(client: TestClient, **body: Any) -> dict[str, Any]:
    response = client.post(API, json=body)
    assert response.status_code == 202, response.text
    data: dict[str, Any] = response.json()
    return data


def built(client: TestClient, app: FastAPI, **body: Any) -> dict[str, Any]:
    created = create(client, **body)
    finish(app)
    detail = client.get(f"{API}/{created['package']['id']}")
    assert detail.status_code == 200
    result: dict[str, Any] = detail.json()
    assert result["status"] == "ready", result["error"]
    return {**result, "password": created["password"]}


def error_code(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


# -- creating packages ----------------------------------------------------------------------------


def test_full_days_package_is_built_in_the_background_and_verifies(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    tmp_path: Path,
) -> None:
    who = signed_in(client, login_as, app_session_factory)
    created = create(client, **{"from": "2026-09-10", "to": "2026-09-12"})
    assert created["password"] is None
    assert created["password_notice_tr"] is None
    assert created["package"]["status"] in ("queued", "building", "ready")
    assert created["package"]["created_by"] == who.username
    finish(app)
    detail = client.get(f"{API}/{created['package']['id']}").json()
    assert detail["status"] == "ready"
    assert detail["status_label_tr"] == "Hazır"
    # The size estimate made with the request is just above the real size (spec 14 disk).
    estimated = detail["scope"].pop("estimated_bytes")
    assert detail["size"] <= estimated <= detail["size"] + evidence_limits.PACKAGE_OVERHEAD
    assert detail["scope"] == {
        "content": "full_days",
        "days": ["2026-09-10", "2026-09-11", "2026-09-12"],
        "sources": None,
        "extract_lines": 0,
        "lookup_id": None,
        "reference": None,
        "include_audit_log": False,
    }
    path = layout.exports / detail["file"]
    assert path.parent == layout.exports
    assert path.is_file()
    assert detail["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert detail["size"] == path.stat().st_size
    assert detail["verifier_sha256"] == verifier_sha256()
    assert detail["package_name"].startswith("KP-")
    assert detail["legal_grade"] is False  # mock TSA days are never legally valid
    assert detail["downloadable"] is True
    assert detail["download_count"] == 0
    assert detail["finished_at"] is not None
    keep = datetime.fromisoformat(detail["expires_at"]) - datetime.fromisoformat(
        detail["finished_at"]
    )
    assert keep == timedelta(days=30)  # evidence.keep_days

    # The auditor's check: the standalone verifier in an isolated Python process.
    result = run_verifier(path, "--json", timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr
    report = json.loads(result.stdout)
    assert report["passed"] is True

    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        index = json.loads(archive.read("paket.json"))
    for day in DAYS:
        folder = f"vault/{day.year}/{day.month:02d}/{day.day:02d}"
        assert f"{folder}/fortigate.log.gz" in names
        assert f"{folder}/core-r1.log.gz" in names
        assert f"{folder}/manifest-{day.isoformat()}.json.zd" in names
    assert index["package_id"] == detail["package_name"]
    assert index["created_by"] == who.username
    assert index["site"] == EXAMPLE_SITE_NAME  # site_name of the configuration
    assert hashlib.sha256(index_bytes(path)).hexdigest() == detail["index_sha256"]

    request_entry, create_entry = audits(app_session_factory)
    assert (request_entry.action, create_entry.action) == ("evidence.request", "evidence.create")
    assert request_entry.actor_username == create_entry.actor_username == who.username
    assert request_entry.target == create_entry.target == f"evidence:{detail['id']}"
    assert request_entry.details["days"] == detail["scope"]["days"]
    assert request_entry.details["password_protected"] is False
    assert create_entry.details["status"] == "ready"
    assert create_entry.details["sha256"] == detail["sha256"]
    assert create_entry.details["verifier_sha256"] == verifier_sha256()
    assert create_entry.details["package_id"] == detail["package_name"]
    assert create_entry.ip is None  # the test client has no IP address


def test_a_package_is_built_while_no_organisation_name_is_set(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    api_config: Config,
) -> None:
    app.state.arci.config = Config.model_validate({**api_config.model_dump(), "site_name": ""})
    signed_in(client, login_as, app_session_factory)
    created = create(client, **{"from": "2026-09-10", "to": "2026-09-10"})
    finish(app)
    detail = client.get(f"{API}/{created['package']['id']}").json()
    assert detail["status"] == "ready", detail
    assert json.loads(index_bytes(layout.exports / detail["file"]))["site"] == ""


def index_bytes(path: Path) -> bytes:
    with zipfile.ZipFile(path) as archive:
        return archive.read("paket.json")


def audit_files(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as archive:
        return sorted(name for name in archive.namelist() if name.endswith("/audit.jsonl.gz"))


def test_the_audit_log_goes_in_only_when_an_administrator_asks_for_it(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    """Orchestrator decision 1: the days' ARCILOGGER audit log (other lookups, staff) is left out
    by default (KVKK); an administrator may add it for an internal audit."""
    signed_in(client, login_as, app_session_factory)
    days = {"from": "2026-09-10", "to": "2026-09-11"}
    default = built(client, app, **days)
    path = layout.exports / default["file"]
    assert audit_files(path) == []
    assert default["scope"]["include_audit_log"] is False
    index = json.loads(index_bytes(path))
    assert index["scope"]["audit_log"] is False
    for entry in index["days"]:
        (audit,) = [item for item in entry["excluded"] if item.get("kind") == "audit_log"]
        manifest = json.loads(layout.manifest(date.fromisoformat(entry["day"])).read_bytes())
        assert audit["sha256"] == manifest["audit_log"]["sha256"]  # listed, with its seal
    assert run_verifier(path, timeout=300).returncode == 0

    included = built(client, app, **days, include_audit_log=True)
    path = layout.exports / included["file"]
    assert audit_files(path) == [
        "vault/2026/09/10/audit.jsonl.gz",
        "vault/2026/09/11/audit.jsonl.gz",
    ]
    assert included["scope"]["include_audit_log"] is True
    assert json.loads(index_bytes(path))["scope"]["audit_log"] is True
    assert run_verifier(path, timeout=300).returncode == 0
    # The choice is in both audit entries of each package.
    entries = audits(app_session_factory)
    by_package: dict[str, list[bool]] = {}
    for entry in entries:
        scope = entry.details if entry.action == "evidence.request" else entry.details["scope"]
        by_package.setdefault(entry.target or "", []).append(scope["include_audit_log"])
    assert by_package == {
        f"evidence:{default['id']}": [False, False],
        f"evidence:{included['id']}": [True, True],
    }
    # The size estimate counts the audit log only when it goes in.
    assert included["scope"]["estimated_bytes"] > default["scope"]["estimated_bytes"]


def test_only_an_administrator_may_add_the_audit_log(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even a role that may create packages (should the owner grant the auditor
    ``evidence.create``) may not add the audit log; nothing is queued or audited."""
    monkeypatch.setitem(
        security.ROLE_PERMISSIONS,
        "auditor",
        security.ROLE_PERMISSIONS["auditor"] | {"evidence.create"},
    )
    body = {"from": "2026-09-10", "to": "2026-09-10"}
    with TestClient(app) as auditor:
        signed_in(auditor, login_as, app_session_factory, "auditor")
        response = auditor.post(API, json={**body, "include_audit_log": True})
        assert error_code(response) == (403, "audit_log_admin_only")
        assert "yönetici" in response.json()["error"]["message_tr"]
        assert audits(app_session_factory) == []
        # Without the option the same request is accepted.
        detail = built(auditor, app, **body)
        assert detail["scope"]["include_audit_log"] is False
        response = auditor.post(API, json={**body, "include_audit_log": "maybe"})
        assert error_code(response) == (422, "validation_error")


def test_password_is_returned_once_and_never_stored(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    signed_in(client, login_as, app_session_factory)
    created = create(client, **{"from": "2026-09-11", "to": "2026-09-11", "password": True})
    password = created["password"]
    assert isinstance(password, str)
    assert len(password) >= 24
    assert "yalnızca şimdi" in created["password_notice_tr"]
    finish(app)
    detail = client.get(f"{API}/{created['package']['id']}")
    assert password not in detail.text
    assert detail.json()["password_protected"] is True
    listing = client.get(API)
    assert password not in listing.text
    path = layout.exports / detail.json()["file"]
    assert is_encrypted(path)

    # Nowhere in the database, the audit spool, the logs or the package index.
    with app_session_factory() as session:
        for row in session.scalars(select(AuditLog)):
            assert password not in json.dumps(row.details)
        stored = session.get_one(EvidencePackage, created["package"]["id"])
        assert password not in json.dumps(stored.scope)
    for spool in (layout.spool / "_audit").glob("*.jsonl"):
        assert password.encode() not in spool.read_bytes()
    assert password not in caplog.text

    # The recipient opens it with the password and verifies it.
    with pytest.raises(EvidenceError, match="cannot decrypt"):
        extract_archive(path, tmp_path / "wrong", "wrong-password-123")
    extract_archive(path, tmp_path / "opened", password)
    result = run_verifier(tmp_path / "opened", timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr


def lookup_outcome() -> LookupOutcome:
    """A double-NAT candidate match citing a FortiGate line of 11.09 and a DHCP line of 10.09."""
    at = at_site(DAYS[1], 10, 0)
    traffic_ref = EvidenceRef(
        table="fg_traffic",
        row_id=7,
        time=at,
        raw=LookupRawRef(source="fortigate", day=DAYS[1], line=21, offset=None),
        summary="172.20.42.170:40000 -> 203.0.113.10:443",
    )
    dhcp_ref = EvidenceRef(
        table="mt_dhcp",
        row_id=3,
        time=at_site(DAYS[0], 22, 5),
        raw=LookupRawRef(source="core-r1", day=DAYS[0], line=23, offset=None),
        summary="dhcp42 assigned",
    )
    chain = AttributionChain(
        internal_ip="172.20.42.170",
        at=at,
        vlan=42,
        vlan_name="Planlama",
        lease=LeaseInfo(
            lease_id=1,
            ip="172.20.42.170",
            mac="00:00:5e:00:53:16",
            vlan=42,
            hostname="PC",
            start=at_site(DAYS[0], 22, 5),
            end=None,
            end_reason="open",
            confidence="high",
            evidence=(dhcp_ref,),
        ),
        device=DeviceInfo(
            mac="00:00:5e:00:53:16",
            name=None,
            hostnames=(),
            vendor=None,
            randomized=False,
            type=None,
            infrastructure=False,
            confidence="high",
            evidence=(dhcp_ref, EvidenceRef(table="devices", row_id=None, time=at, raw=None)),
        ),
        person=None,
        department="Planlama",
        department_source="vlan",
        confidence="high",
    )
    match = NatMatch(
        kind="candidate",
        score=80,
        time_distance_ms=0,
        destination_match="ip_port",
        traffic=TrafficInfo(
            sessionid=1,
            proto=6,
            srcip="172.20.42.170",
            srcport=40000,
            dstip="203.0.113.10",
            dstport=443,
            transip="100.64.12.9",
            transport=50000,
            start=at - timedelta(seconds=5),
            end=at,
            evidence=(traffic_ref,),
        ),
        chain=chain,
    )
    return LookupOutcome(
        request=LookupRequest(at=at, public_ip="192.0.2.46", public_port=50000),
        mode="candidates",
        matches=(match,),
        double_nat=True,
        attribution=None,
        explanations=(),
        data_window=None,
        tolerance_seconds=60,
        generated_at=at,
    )


def unconverted_outcome() -> LookupOutcome:
    """An exact match whose result also cites a FortiGate DHCP line of 11.09 that never became a
    lease (``unconverted_dhcp``, D8): shown in the report's section 6, so in its package too."""
    outcome = lookup_outcome()
    unconverted = EvidenceRef(
        table="fg_event",
        row_id=9,
        time=at_site(DAYS[1], 9, 58),
        raw=LookupRawRef(source="fortigate", day=DAYS[1], line=22, offset=None),
        summary="FortiGate DHCP Ack 172.20.42.170",
    )
    return replace(
        outcome,
        mode="exact",
        matches=tuple(replace(match, kind="exact") for match in outcome.matches),
        unconverted_dhcp=(unconverted,),
    )


def add_lookup(factory: sessionmaker[Session], user_id: int, outcome: LookupOutcome | None) -> int:
    with factory() as session:
        row = Lookup(
            user_id=user_id,
            username="tester",
            authority="İstanbul Cumhuriyet Başsavcılığı",
            file_no="2026/123",
            request_date=date(2026, 9, 20),
            reason="test",
            query=(outcome or lookup_outcome()).request.to_dict(),
            outcome=None if outcome is None else outcome.to_dict(),
            mode=None if outcome is None else outcome.mode,
        )
        session.add(row)
        session.commit()
        return row.id


def test_lookup_refs_cover_matches_chains_and_alternatives() -> None:
    assert lookup_refs(lookup_outcome()) == {
        RawRef(day=DAYS[1], source="fortigate", line=21),
        RawRef(day=DAYS[0], source="core-r1", line=23),
    }


def test_lookup_refs_include_the_unconverted_dhcp_lines() -> None:
    assert lookup_refs(unconverted_outcome()) == {
        RawRef(day=DAYS[1], source="fortigate", line=21),
        RawRef(day=DAYS[0], source="core-r1", line=23),
        RawRef(day=DAYS[1], source="fortigate", line=22),
    }


def test_extract_package_from_a_lookup_holds_its_unconverted_dhcp_lines(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    """The PDF of a lookup cites the unconverted lines in section 6; its package extracts them."""
    who = signed_in(client, login_as, app_session_factory)
    lookup_id = add_lookup(app_session_factory, who.user_id, unconverted_outcome())
    detail = built(client, app, lookup_id=lookup_id, content="extract")
    assert {"day": "2026-09-11", "source": "fortigate", "line": 22} in detail["extract"]
    with zipfile.ZipFile(layout.exports / detail["file"]) as archive:
        fortigate = archive.read("extract/2026-09-11/fortigate.txt").splitlines()
    numbers = [row.split(b"\t", 1)[0] for row in fortigate]
    assert numbers == [b"21", b"22"]
    assert vault.sent[(DAYS[1], "fortigate")][21] in fortigate[1]


def test_extract_package_from_a_lookup(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    tmp_path: Path,
) -> None:
    who = signed_in(client, login_as, app_session_factory)
    lookup_id = add_lookup(app_session_factory, who.user_id, lookup_outcome())
    detail = built(client, app, lookup_id=lookup_id, content="extract")
    assert detail["lookup_id"] == lookup_id
    assert detail["scope"]["content"] == "extract"
    assert detail["scope"]["days"] == ["2026-09-10", "2026-09-11"]
    assert detail["scope"]["sources"] == ["core-r1", "fortigate"]
    assert detail["scope"]["reference"] == "İstanbul Cumhuriyet Başsavcılığı — 2026/123"
    assert detail["extract"] == [
        {"day": "2026-09-10", "source": "core-r1", "line": 23},
        {"day": "2026-09-11", "source": "fortigate", "line": 21},
    ]
    path = layout.exports / detail["file"]
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        fortigate = archive.read("extract/2026-09-11/fortigate.txt")
        index = json.loads(archive.read("paket.json"))
    # the extract + the complete sealed files of the cited sources on the cited days (9.6)
    for folder in ("vault/2026/09/10", "vault/2026/09/11"):
        assert {f"{folder}/core-r1.log.gz", f"{folder}/fortigate.log.gz"} <= names
    assert not any(name.startswith("vault/2026/09/12/") for name in names)
    line = fortigate.split(b"\t", 1)
    assert line[0] == b"21"
    assert vault.sent[(DAYS[1], "fortigate")][20] in line[1]
    assert index["scope"]["lookup_id"] == f"L-{lookup_id}"
    result = run_verifier(path, timeout=300)
    assert result.returncode == 0, result.stdout + result.stderr

    full = built(client, app, lookup_id=lookup_id, sources=["fortigate"])
    assert full["scope"]["days"] == ["2026-09-10", "2026-09-11"]  # cited days + request day
    assert full["scope"]["sources"] == ["core-r1", "fortigate"]  # + the cited lines' sources


def test_full_days_from_a_lookup_include_the_cited_lines(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
) -> None:
    who = signed_in(client, login_as, app_session_factory)
    lookup_id = add_lookup(app_session_factory, who.user_id, lookup_outcome())
    detail = built(client, app, lookup_id=lookup_id, reference="Dosya 2026/9")
    assert detail["scope"]["sources"] is None
    assert detail["scope"]["extract_lines"] == 2
    assert detail["scope"]["reference"] == "Dosya 2026/9"
    listed = client.get(API, params={"lookup_id": lookup_id}).json()["items"]
    assert [item["id"] for item in listed] == [detail["id"]]


def test_explicit_extract_package(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    signed_in(client, login_as, app_session_factory)
    lines = [{"day": "2026-09-12", "source": "core-r1", "line": 1}]
    detail = built(
        client, app, **{"from": "2026-09-12", "to": "2026-09-12"}, content="extract", extract=lines
    )
    assert detail["scope"]["sources"] == ["core-r1"]
    with zipfile.ZipFile(layout.exports / detail["file"]) as archive:
        assert archive.read("extract/2026-09-12/core-r1.txt").startswith(b"1\t")
    full = built(
        client,
        app,
        **{"from": "2026-09-12", "to": "2026-09-12"},
        sources=["core-r1"],
        extract=lines,
    )
    assert full["scope"]["sources"] == ["core-r1"]
    assert full["scope"]["extract_lines"] == 1


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, (422, "scope_invalid")),
        ({"lookup_id": 1, "from": "2026-09-10", "to": "2026-09-10"}, (422, "scope_invalid")),
        ({"from": "2026-09-12", "to": "2026-09-10"}, (422, "scope_invalid")),
        ({"from": "2026-08-01", "to": "2026-09-12"}, (422, "scope_invalid")),  # > 31 days
        ({"from": "2000-01-01", "to": "2999-12-31"}, (422, "scope_invalid")),  # refused at once
        ({"from": "2026-09-10", "to": "2026-09-13"}, (409, "days_not_sealed")),
        ({"from": "2026-09-10", "to": "2026-09-10", "sources": ["ap-99"]}, (422, "scope_invalid")),
        ({"from": "2026-09-10", "to": "2026-09-10", "content": "extract"}, (422, "scope_invalid")),
        (
            {
                "from": "2026-09-10",
                "to": "2026-09-10",
                "extract": [{"day": "2026-09-11", "source": "fortigate", "line": 1}],
            },
            (422, "scope_invalid"),
        ),
        (
            {
                "from": "2026-09-10",
                "to": "2026-09-10",
                "sources": ["fortigate"],
                "extract": [{"day": "2026-09-10", "source": "core-r1", "line": 1}],
            },
            (422, "scope_invalid"),
        ),
        (
            {
                "from": "2026-09-10",
                "to": "2026-09-10",
                "content": "extract",
                "sources": ["fortigate"],
                "extract": [{"day": "2026-09-10", "source": "fortigate", "line": 1}],
            },
            (422, "scope_invalid"),
        ),
        (
            {
                "from": "2026-09-10",
                "to": "2026-09-10",
                "extract": [{"day": "2026-09-10", "source": "switch-99", "line": 1}],
            },
            (409, "lines_not_sealed"),
        ),
        (
            {"from": "2026-09-10", "to": "2026-09-10", "sources": ["../x"]},
            (422, "validation_error"),
        ),
        ({"from": "2026-09-10", "to": "2026-09-10", "unknown": 1}, (422, "validation_error")),
        ({"lookup_id": 999999}, (404, "not_found")),
    ],
)
def test_invalid_scopes_are_refused_before_any_work(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    body: dict[str, Any],
    expected: tuple[int, str],
) -> None:
    signed_in(client, login_as, app_session_factory)
    response = client.post(API, json=body)
    assert error_code(response) == expected
    assert response.json()["error"]["message_tr"]
    with app_session_factory() as session:
        assert session.scalars(select(EvidencePackage)).all() == []
    assert not layout.exports.exists() or not any(layout.exports.iterdir())


def test_lookup_scope_errors(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
) -> None:
    who = signed_in(client, login_as, app_session_factory)
    unfinished = add_lookup(app_session_factory, who.user_id, None)
    assert error_code(client.post(API, json={"lookup_id": unfinished})) == (
        409,
        "lookup_not_finished",
    )
    empty = lookup_outcome()
    nothing = LookupOutcome(
        request=empty.request,
        mode="none",
        matches=(),
        double_nat=True,
        attribution=None,
        explanations=(),
        data_window=None,
        tolerance_seconds=60,
        generated_at=empty.generated_at,
    )
    none_id = add_lookup(app_session_factory, who.user_id, nothing)
    with_sources = {"lookup_id": none_id, "content": "extract", "sources": ["fortigate"]}
    assert error_code(client.post(API, json=with_sources)) == (422, "scope_invalid")
    assert error_code(client.post(API, json={"lookup_id": none_id, "content": "extract"})) == (
        409,
        "no_raw_references",
    )
    with app_session_factory() as session:
        broken = session.get_one(Lookup, none_id)
        broken.outcome = {"mode": "none"}
        session.commit()
    assert error_code(client.post(API, json={"lookup_id": none_id})) == (409, "lookup_unreadable")


def test_no_trusted_roots(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    signed_in(client, login_as, app_session_factory)
    root = layout.state / "mock-tsa" / "root.pem"
    root.rename(root.with_suffix(".bak"))
    response = client.post(API, json={"from": "2026-09-10", "to": "2026-09-10"})
    assert error_code(response) == (409, "no_trusted_roots")


# -- background status ----------------------------------------------------------------------------


def test_status_moves_from_queued_to_building_to_ready(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_in(client, login_as, app_session_factory)
    started, release = threading.Event(), threading.Event()
    real_build = evidence_jobs.build_package  # type: ignore[attr-defined]

    def slow_build(*args: Any, **kwargs: Any) -> Any:
        started.set()
        assert release.wait(60)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(evidence_jobs, "build_package", slow_build)
    first = create(client, **{"from": "2026-09-10", "to": "2026-09-10"})["package"]["id"]
    # The same days for another request (a duplicate of the first is refused, see below).
    body = {"from": "2026-09-10", "to": "2026-09-10", "reference": "Savcılık 2026/2"}
    second = create(client, **body)["package"]["id"]
    assert started.wait(30)
    assert client.get(f"{API}/{first}").json()["status"] == "building"
    assert client.get(f"{API}/{second}").json()["status"] == "queued"  # one build at a time
    blocked = client.get(f"{API}/{first}/download")
    assert error_code(blocked) == (409, "evidence_not_ready")
    release.set()
    finish(app)
    one, two = (client.get(f"{API}/{n}").json() for n in (first, second))
    assert one["status"] == two["status"] == "ready"
    assert one["package_name"] != two["package_name"]
    page = client.get(API, params={"limit": 1}).json()
    assert [item["id"] for item in page["items"]] == [second]
    rest = client.get(API, params={"limit": 1, "cursor": page["next_cursor"]}).json()
    assert [item["id"] for item in rest["items"]] == [first]
    assert rest["next_cursor"] is None
    assert client.get(API, params={"status": "ready"}).json()["items"][0]["id"] == second
    assert client.get(API, params={"status": "failed"}).json()["items"] == []


def test_two_packages_are_never_created_in_the_same_second(app: FastAPI) -> None:
    """The package id is the creation second plus a hash of the scope (same scope twice)."""
    clock = EvidenceBuilder(app.state.arci)._clock
    first, second = clock(), clock()
    assert int(second.timestamp()) > int(first.timestamp())


def test_a_changed_vault_file_fails_the_build(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    signed_in(client, login_as, app_session_factory)
    vault.tamper(layout.vault_day(DAYS[1]) / "fortigate.log.gz")
    created = create(client, **{"from": "2026-09-10", "to": "2026-09-12"})
    finish(app)
    detail = client.get(f"{API}/{created['package']['id']}").json()
    assert detail["status"] == "failed"
    assert "fortigate.log.gz" in detail["error"]
    assert detail["file"] is None
    assert detail["downloadable"] is False
    assert error_code(client.get(f"{API}/{detail['id']}/download")) == (409, "evidence_failed")
    entry = audits(app_session_factory)[-1]
    assert entry.action == "evidence.create"
    assert entry.details["status"] == "failed"
    assert not list(layout.exports.glob("*.zip"))


def test_an_unexpected_error_is_recorded_without_details(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_in(client, login_as, app_session_factory)

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("secret internal detail")

    monkeypatch.setattr(evidence_jobs, "build_package", broken)
    created = create(client, **{"from": "2026-09-10", "to": "2026-09-10"})
    finish(app)
    detail = client.get(f"{API}/{created['package']['id']}").json()
    assert detail["status"] == "failed"
    assert "secret internal detail" not in detail["error"]
    assert detail["error"] == evidence_jobs.UNEXPECTED


def test_builds_interrupted_by_a_restart_are_marked_failed(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
) -> None:
    who = signed_in(client, login_as, app_session_factory)
    with app_session_factory() as session:
        rows = [
            EvidencePackage(
                user_id=who.user_id,
                username=who.username,
                scope={"content": "full_days", "days": ["2026-09-10"]},
                path="",
                status=state,
                expires_at=datetime.now(UTC) + timedelta(days=30),
            )
            for state in ("queued", "building", "ready")
        ]
        session.add_all(rows)
        session.commit()
        ids = [row.id for row in rows]
    listing = client.get(API).json()["items"]
    states = {item["id"]: (item["status"], item["error"]) for item in listing}
    assert states[ids[0]] == ("failed", INTERRUPTED)
    assert states[ids[1]] == ("failed", INTERRUPTED)
    assert states[ids[2]][0] == "ready"
    entries = audits(app_session_factory, "evidence.create")
    assert [(e.target, e.actor_username, e.details["status"]) for e in entries] == [
        (f"evidence:{ids[0]}", "system", "failed"),
        (f"evidence:{ids[1]}", "system", "failed"),
    ]
    client.get(API)  # once per process: nothing more happens
    assert len(audits(app_session_factory, "evidence.create")) == 2


def test_a_build_marked_failed_meanwhile_stays_failed(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A hung build that the restart recovery or the worker already failed (and audited) does
    not turn the row back to ready, nor write a second, contradicting entry, when it ends."""
    signed_in(client, login_as, app_session_factory)
    started, release = threading.Event(), threading.Event()
    real_build = evidence_jobs.build_package  # type: ignore[attr-defined]
    outcomes = iter(["finishes", "breaks"])

    def hung_build(*args: Any, **kwargs: Any) -> Any:
        started.set()
        assert release.wait(60)
        if next(outcomes) == "breaks":
            raise EvidenceError("the vault went away")
        return real_build(*args, **kwargs)

    monkeypatch.setattr(evidence_jobs, "build_package", hung_build)
    for day in ("2026-09-10", "2026-09-11"):
        started.clear()
        release.clear()
        package_id = create(client, **{"from": day, "to": day})["package"]["id"]
        assert started.wait(30)
        marked = evidence_jobs.mark_interrupted(
            app_session_factory, before=datetime.now(UTC) + timedelta(seconds=1)
        )
        assert marked == [package_id]
        release.set()
        finish(app)
        row = package(app_session_factory, package_id)
        assert (row.status, row.error, row.path) == ("failed", INTERRUPTED, "")
        entries = [
            e
            for e in audits(app_session_factory, "evidence.create")
            if e.target == f"evidence:{package_id}"
        ]
        assert [(e.actor_username, e.details["status"]) for e in entries] == [("system", "failed")]
    assert not list(layout.exports.glob("*.zip"))  # the late ZIP is not left without a row


# -- limits (spec 13 "rate limits on exports", spec 14 disk) --------------------------------------


def test_duplicates_and_unfinished_builds_are_limited(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    release = threading.Event()
    real_build = evidence_jobs.build_package  # type: ignore[attr-defined]

    def held_build(*args: Any, **kwargs: Any) -> Any:
        assert release.wait(60)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(evidence_jobs, "build_package", held_build)
    setting(app_session_factory, "evidence.max_unfinished_total", 3)
    with TestClient(app) as first, TestClient(app) as second:
        signed_in(first, login_as, app_session_factory)
        signed_in(second, login_as, app_session_factory)
        body = {"from": "2026-09-10", "to": "2026-09-10"}
        package_id = create(first, **body)["package"]["id"]
        # A double click: the same scope again while the first is not finished.
        duplicate = first.post(API, json=body)
        assert error_code(duplicate) == (409, "evidence_duplicate")
        assert duplicate.json()["error"]["details"] == {"package_id": package_id}
        # The same days with a password is another package.
        create(first, **body, password=True)
        # Two unfinished builds per user (evidence.max_unfinished_per_user).
        third = first.post(API, json={"from": "2026-09-11", "to": "2026-09-11"})
        assert error_code(third) == (429, "rate_limited")
        assert third.json()["error"]["details"] == {
            "reason": "unfinished_per_user",
            "unfinished": 2,
            "limit": 2,
        }
        assert third.headers["retry-after"] == "60"
        assert "Hazırlanmakta olan 2 paketiniz" in third.json()["error"]["message_tr"]
        # Three in total (the setting): the other user gets one more, then no more.
        create(second, **body)
        total = second.post(API, json={"from": "2026-09-12", "to": "2026-09-12"})
        assert error_code(total) == (429, "rate_limited")
        assert total.json()["error"]["details"]["reason"] == "unfinished_total"
        assert len(audits(app_session_factory, "evidence.request")) == 3
        release.set()
        finish(app)
        # Finished builds no longer count.
        assert first.post(API, json={"from": "2026-09-11", "to": "2026-09-11"}).status_code == 202
        finish(app)
    with app_session_factory() as session:
        rows = session.scalars(select(EvidencePackage)).all()
        assert sorted(row.status for row in rows) == ["ready"] * 4


def test_requests_per_hour_are_limited(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
) -> None:
    signed_in(client, login_as, app_session_factory)
    setting(app_session_factory, "evidence.max_requests_per_hour", 2)
    for day in ("2026-09-10", "2026-09-11"):
        create(client, **{"from": day, "to": day})
        finish(app)
    response = client.post(API, json={"from": "2026-09-12", "to": "2026-09-12"})
    assert error_code(response) == (429, "rate_limited")
    assert response.json()["error"]["details"] == {
        "reason": "requests_per_hour",
        "requests": 2,
        "limit": 2,
    }
    assert 3500 < int(response.headers["retry-after"]) <= 3600
    # A bad setting falls back to the shipped default (10 per hour).
    setting(app_session_factory, "evidence.max_requests_per_hour", "many")
    assert client.post(API, json={"from": "2026-09-12", "to": "2026-09-12"}).status_code == 202
    finish(app)


def test_free_disk_space_and_the_exports_limit_are_kept(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signed_in(client, login_as, app_session_factory)
    body = {"from": "2026-09-10", "to": "2026-09-12"}
    # 100 GiB disk: the reserve is max(10 %, 20 GiB) = 20 GiB.
    monkeypatch.setattr(evidence_limits, "disk_usage", lambda path: disk(19.9, 100))
    low = client.post(API, json=body)
    assert error_code(low) == (507, "disk_space_low")
    details = low.json()["error"]["details"]
    assert details["reserve_bytes"] == 20 * GIB
    assert details["free_bytes"] == int(19.9 * GIB)
    assert details["pending_bytes"] == 0
    assert details["estimated_bytes"] > evidence_limits.PACKAGE_OVERHEAD
    assert "en az 20 GB boş kalmalıdır" in low.json()["error"]["message_tr"]
    # 1 TB disk: the reserve is 10 % of it.
    monkeypatch.setattr(evidence_limits, "disk_usage", lambda path: disk(99.9, 1000))
    assert error_code(client.post(API, json=body)) == (507, "disk_space_low")
    with app_session_factory() as session:
        assert session.scalars(select(EvidencePackage)).all() == []
    assert audits(app_session_factory) == []

    # Unfinished builds count with their estimate: room for one package, not for two.
    release = threading.Event()
    real_build = evidence_jobs.build_package  # type: ignore[attr-defined]

    def held_build(*args: Any, **kwargs: Any) -> Any:
        assert release.wait(60)
        return real_build(*args, **kwargs)

    monkeypatch.setattr(evidence_jobs, "build_package", held_build)
    setting(app_session_factory, "evidence.min_free_percent", 0)
    setting(app_session_factory, "evidence.min_free_gb", 0)
    estimate = details["estimated_bytes"]
    room = Usage(GIB, GIB - estimate * 3 // 2, estimate * 3 // 2)
    monkeypatch.setattr(evidence_limits, "disk_usage", lambda path: room)
    first = create(client, **body)["package"]
    assert first["scope"]["estimated_bytes"] == estimate
    second = client.post(API, json={**body, "reference": "ikinci"})
    assert error_code(second) == (507, "disk_space_low")
    assert second.json()["error"]["details"]["pending_bytes"] == estimate
    release.set()
    finish(app)
    assert package(app_session_factory, first["id"]).status == "ready"

    # exports/ has its own limit (evidence.exports_max_gb, whole GB; 0 allows nothing more).
    monkeypatch.setattr(evidence_limits, "disk_usage", lambda path: disk(900))
    setting(app_session_factory, "evidence.exports_max_gb", 0)
    full = client.post(API, json={**body, "reference": "üçüncü"})
    assert error_code(full) == (507, "exports_quota_exceeded")
    full_details = full.json()["error"]["details"]
    zip_size = (layout.exports / package(app_session_factory, first["id"]).path).stat().st_size
    assert full_details["exports_bytes"] == zip_size
    assert full_details["limit_bytes"] == 0


def test_the_builder_checks_the_free_space_again(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The disk filled up while the package waited: the build fails instead of writing."""
    signed_in(client, login_as, app_session_factory)
    answers = iter([disk(900), disk(50)])  # the request, then the builder (reserve 100 GiB)
    monkeypatch.setattr(evidence_limits, "disk_usage", lambda path: next(answers))
    created = create(client, **{"from": "2026-09-10", "to": "2026-09-10"})
    finish(app)
    row = package(app_session_factory, created["package"]["id"])
    assert row.status == "failed"
    assert row.error is not None
    assert row.error.startswith("Veri diskinde yer yetersiz")
    assert "Not enough space on the data disk" in row.error
    entry = audits(app_session_factory, "evidence.create")[-1]
    assert entry.details["status"] == "failed"
    assert not layout.exports.exists() or not list(layout.exports.iterdir())


def test_bad_limit_settings_and_unreadable_manifests(
    app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    setting(app_session_factory, "evidence.max_unfinished_per_user", 0)
    setting(app_session_factory, "evidence.min_free_gb", -5)
    setting(app_session_factory, "evidence.exports_max_gb", [1])
    with app_session_factory() as session:
        limits = evidence_limits.Limits.load(session)
    assert limits.max_unfinished_per_user == 2  # below the floor: the shipped default
    assert limits.min_free_bytes == 20 * GIB
    assert limits.exports_max_bytes == 100 * GIB
    assert limits.reserve(1000 * GIB) == 100 * GIB
    # Days the build would refuse add only what is on disk.
    day, other = date(2026, 1, 5), date(2026, 1, 6)
    assert (
        evidence_limits.estimate_bytes(layout, [day], None, 0) == evidence_limits.PACKAGE_OVERHEAD
    )
    for when, content in ((day, b"{broken"), (other, b"[]")):
        layout.manifest(when).parent.mkdir(parents=True)
        layout.manifest(when).write_bytes(content)
        layout.token(when).write_bytes(b"token")
    estimate = evidence_limits.estimate_bytes(layout, [day, other], ["fortigate"], 3)
    allowance = evidence_limits.PACKAGE_OVERHEAD + 3 * evidence_limits.EXTRACT_LINE_BYTES
    assert estimate == allowance + 7 + 5 + 2 + 5
    # The audit log (its manifest entry has no size) is measured, and only counted when asked for.
    third, fourth = date(2026, 1, 7), date(2026, 1, 8)
    audit = layout.vault / "2026/01/07/audit.jsonl.gz"
    for when, relative in ((third, "2026/01/07/audit.jsonl.gz"), (fourth, "../../outside")):
        layout.manifest(when).parent.mkdir(parents=True, exist_ok=True)
        layout.manifest(when).write_text(
            json.dumps({"files": [], "audit_log": {"path": relative}}), encoding="utf-8"
        )
        layout.token(when).write_bytes(b"")
    audit.write_bytes(b"x" * 1000)
    base = evidence_limits.estimate_bytes(layout, [third, fourth], None, 0)
    with_audit = evidence_limits.estimate_bytes(layout, [third, fourth], None, 0, audit_log=True)
    assert with_audit - base == 1000  # the path leaving the vault counts nothing
    audit.unlink()
    assert evidence_limits.estimate_bytes(layout, [third], None, 0, audit_log=True) == (
        evidence_limits.estimate_bytes(layout, [third], None, 0)
    )


def test_a_day_whose_sealing_did_not_finish_cannot_be_packaged(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    """The token is in the vault, but the Seal task failed after it (e.g. locking the files)."""
    signed_in(client, login_as, app_session_factory)
    path = layout.seal_state(DAYS[1])
    state = json.loads(path.read_text(encoding="utf-8"))
    state.update(status="pending", last_error="[WinError 5] Access is denied (lock)")
    path.write_text(json.dumps(state), encoding="utf-8")
    response = client.post(API, json={"from": "2026-09-10", "to": "2026-09-12"})
    assert error_code(response) == (409, "days_not_sealed")
    assert response.json()["error"]["details"] == {"days": ["2026-09-11"]}


# -- downloads ------------------------------------------------------------------------------------


def test_download_is_streamed_recorded_audited_and_counted(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    who = signed_in(client, login_as, app_session_factory)
    detail = built(client, app, **{"from": "2026-09-10", "to": "2026-09-10"})
    response = client.get(f"{API}/{detail['id']}/download")
    assert response.status_code == 200
    assert response.content == (layout.exports / detail["file"]).read_bytes()
    assert hashlib.sha256(response.content).hexdigest() == detail["sha256"]
    assert response.headers["content-type"] == "application/zip"
    assert response.headers["content-disposition"] == f'attachment; filename="{detail["file"]}"'
    assert detail["file"].isascii()
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["cache-control"] == "no-store"
    digest = base64.b64encode(bytes.fromhex(detail["sha256"])).decode()
    assert response.headers["repr-digest"] == f"sha-256=:{digest}:"
    assert client.get(f"{API}/{detail['id']}/download").status_code == 200

    after = client.get(f"{API}/{detail['id']}").json()
    assert after["download_count"] == 2
    assert [d["username"] for d in after["downloads"]] == [who.username, who.username]
    with app_session_factory() as session:
        rows = session.scalars(select(EvidenceDownload)).all()
        assert [(r.package_id, r.user_id, r.username) for r in rows] == [
            (detail["id"], who.user_id, who.username)
        ] * 2
    entries = audits(app_session_factory, "evidence.download")
    assert [e.details["download"] for e in entries] == [1, 2]
    assert entries[0].details["sha256"] == detail["sha256"]
    assert entries[0].target == f"evidence:{detail['id']}"


def test_range_requests_get_the_whole_file(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    """Every recorded download is one complete copy: byte ranges are not served."""
    signed_in(client, login_as, app_session_factory)
    detail = built(client, app, **{"from": "2026-09-10", "to": "2026-09-10"})
    url = f"{API}/{detail['id']}/download"
    whole = (layout.exports / detail["file"]).read_bytes()
    for headers in (
        {"Range": "bytes=0-0"},
        {"Range": "bytes=1-1", "If-Range": "whatever"},
        {"Range": "bytes=0-9, 20-29"},
        {"Range": "bytes=999999999-"},
    ):
        response = client.get(url, headers=headers)
        assert response.status_code == 200, headers
        assert response.content == whole
        assert response.headers["accept-ranges"] == "none"
        assert "content-range" not in response.headers
    after = client.get(f"{API}/{detail['id']}").json()
    assert after["download_count"] == 4
    entries = audits(app_session_factory, "evidence.download")
    assert [e.details["size"] for e in entries] == [len(whole)] * 4


def test_expired_or_missing_packages_are_gone(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    signed_in(client, login_as, app_session_factory)
    detail = built(client, app, **{"from": "2026-09-10", "to": "2026-09-10"})
    url = f"{API}/{detail['id']}/download"

    def set_row(**values: Any) -> None:
        with app_session_factory() as session:
            session.execute(
                update(EvidencePackage).where(EvidencePackage.id == detail["id"]).values(**values)
            )
            session.commit()

    set_row(expires_at=datetime.now(UTC) - timedelta(seconds=1))
    gone = client.get(url)
    assert error_code(gone) == (410, "evidence_gone")
    assert "süresi doldu" in gone.json()["error"]["message_tr"]
    assert client.get(f"{API}/{detail['id']}").json()["downloadable"] is False
    set_row(expires_at=datetime.now(UTC) + timedelta(days=1), status="expired")
    assert error_code(client.get(url)) == (410, "evidence_gone")
    set_row(status="ready", path="../../vault/escape.zip")
    assert error_code(client.get(url)) == (410, "evidence_gone")
    set_row(path=detail["file"])
    assert client.get(url).status_code == 200
    (layout.exports / detail["file"]).unlink()
    assert error_code(client.get(url)) == (410, "evidence_gone")
    with app_session_factory() as session:
        assert session.get_one(EvidencePackage, detail["id"]).download_count == 1


def test_ids_are_validated(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    signed_in(client, login_as, app_session_factory)
    assert error_code(client.get(f"{API}/0")) == (422, "validation_error")
    assert error_code(client.get(f"{API}/abc")) == (422, "validation_error")
    assert error_code(client.get(f"{API}/{2**63}")) == (422, "validation_error")
    assert error_code(client.get(f"{API}/12345")) == (404, "not_found")
    assert error_code(client.get(f"{API}/12345/download")) == (404, "not_found")
    assert error_code(client.get(API, params={"status": "lost"})) == (422, "validation_error")


# -- permissions and re-authentication ----------------------------------------------------------


def test_creating_and_downloading_need_a_recent_reauthentication(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
) -> None:
    who = login_as(client, "admin")
    notice(app_session_factory, who)
    body = {"from": "2026-09-10", "to": "2026-09-10"}
    assert error_code(client.post(API, json=body)) == (403, "reauth_required")
    recent(app_session_factory, who, minutes_ago=6)
    assert error_code(client.post(API, json=body)) == (403, "reauth_required")
    recent(app_session_factory, who)
    detail = built(client, app, **body)
    recent(app_session_factory, who, minutes_ago=6)
    assert error_code(client.get(f"{API}/{detail['id']}/download")) == (403, "reauth_required")
    assert client.get(f"{API}/{detail['id']}").status_code == 200  # listing needs none


def test_download_needs_the_privacy_notice(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
) -> None:
    who = login_as(client, "admin")
    recent(app_session_factory, who)
    detail = built(client, app, **{"from": "2026-09-10", "to": "2026-09-10"})
    response = client.get(f"{API}/{detail['id']}/download")
    assert error_code(response) == (403, "privacy_notice_required")
    notice(app_session_factory, who)
    assert client.get(f"{API}/{detail['id']}/download").status_code == 200


def test_permissions_per_role(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    vault: SealedVault,
) -> None:
    with TestClient(app) as admin, TestClient(app) as auditor:
        signed_in(admin, login_as, app_session_factory)
        detail = built(admin, app, **{"from": "2026-09-10", "to": "2026-09-10"})
        signed_in(auditor, login_as, app_session_factory, "auditor")
        # The auditor lists and downloads, but may not create (owner decision, spec 13).
        assert auditor.get(API).status_code == 200
        assert auditor.get(f"{API}/{detail['id']}").status_code == 200
        assert auditor.get(f"{API}/{detail['id']}/download").status_code == 200
        body = {"from": "2026-09-10", "to": "2026-09-10"}
        assert error_code(auditor.post(API, json=body)) == (403, "forbidden")
    for role in ("hr", "viewer"):
        with TestClient(app) as other:
            signed_in(other, login_as, app_session_factory, role)
            assert error_code(other.get(API)) == (403, "forbidden")
            assert error_code(other.get(f"{API}/{detail['id']}")) == (403, "forbidden")
            assert error_code(other.get(f"{API}/{detail['id']}/download")) == (403, "forbidden")
            assert error_code(other.post(API, json=body)) == (403, "forbidden")
    with TestClient(app) as anonymous:
        assert error_code(anonymous.get(API)) == (401, "not_authenticated")


def test_a_claimed_production_package_is_never_recorded_as_a_test_package(tmp_path: Path) -> None:
    """Decision 5: legal grade only from the verified chain — and a Kamu SM production package
    whose root is not (yet) in tsa.legal_root_fingerprints is not a test package either: its
    row says NULL, which the web shows without the "test/mock" warning."""
    from dataclasses import replace

    from arcilogger_core.evidence.model import PackageInfo

    info = PackageInfo(
        package_id="KP-20261013-000000-0000",
        path=tmp_path / "package.zip",
        sha256="0" * 64,
        size=1,
        file_count=1,
        password_protected=False,
        days=(date(2026, 10, 12),),
        sources=None,
        extract_lines=0,
        index_sha256="1" * 64,
        created_at=datetime(2026, 10, 13, tzinfo=UTC),
        created_by="admin",
        lookup_id=None,
        legal_grade=False,
        verifier_sha256="2" * 64,
    )
    assert evidence_jobs.package_legal_grade(info) is False  # a test / mock stamped day
    claimed = replace(info, legal_grade_claimed=True)
    assert evidence_jobs.package_legal_grade(claimed) is None
    assert evidence_jobs.package_details(claimed)["legal_grade_claimed"] is True
    assert evidence_jobs.package_legal_grade(replace(claimed, legal_grade=True)) is True


def test_a_range_across_skipped_empty_days_can_be_packaged(
    client: TestClient,
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
) -> None:
    """Days skipped as empty (seal.max_consecutive_empty_days) go into a package with the sealed
    day whose manifest lists them; alone they are refused with a clear message."""
    from arcilogger_worker.vaulttesting import make_vault as vault_of

    first, empty, last = date(2026, 9, 1), date(2026, 9, 2), date(2026, 9, 3)
    sealed = vault_of(api_config.data_dir, tsa_now=at_site(first - timedelta(days=30)))
    sealed.config = sealed.config.model_copy(
        update={"seal": sealed.config.seal.model_copy(update={"max_consecutive_empty_days": 0})}
    )
    sealed.write_day(first)
    sealed.write_day(last)
    try:
        outcomes = sealed.sealer(at_site(last + timedelta(days=1))).seal_due()
        assert [o.status for o in outcomes] == ["sealed", "skipped", "sealed"]
        assert evidence_jobs.days_not_sealed(layout, [first, empty, last]) == []
        assert evidence_jobs.days_not_sealed(layout, [first, empty]) == [empty]
        signed_in(client, login_as, app_session_factory)
        refused = client.post(API, json={"from": "2026-09-01", "to": "2026-09-02"})
        assert error_code(refused) == (409, "days_not_sealed")
        assert refused.json()["error"]["details"]["covered_by"] == {"2026-09-02": "2026-09-03"}
        assert "onu listeleyen mühürlü günle" in refused.json()["error"]["message_tr"]
        response = client.post(API, json={"from": "2026-09-01", "to": "2026-09-03"})
        assert response.status_code == 202, response.text
        finish(app)
        detail = client.get(f"{API}/{response.json()['package']['id']}").json()
        assert detail["status"] == "ready", detail["error"]
    finally:
        sealed.unlock_all()
