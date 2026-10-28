"""``/api/v1/compliance`` (Wave 4 contract 2.2, 2.3.4, 2.3.5): who may read the cards and the
report, that neither is licence-gated, that reading is not audited, the 30-second answer, the
report's audit entry and the configuration backup confirmation that turns card 10.

The router is not mounted before W4-F (contract D2): this module mounts it on the test app
unless the path already exists (Implementation note 3), so the tests pass before and after.
The auditor holds ``compliance.read`` from W4-F on (D15); until then the auditor tests grant it
with ``monkeypatch`` (as ``test_evidence_api.py`` does), which changes nothing once it is there.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import security
from arcilogger_api.app import API_PREFIX
from arcilogger_api.compliance import routes
from arcilogger_api.compliance.cards import CONFIG_BACKUP_CONFIRM
from arcilogger_api.compliance.models import ComplianceStatus
from arcilogger_api.deps import TtlCache
from arcilogger_api.licensing import MODULE_MARKER
from arcilogger_api.testing import EXAMPLE_SITE_NAME, LoggedIn
from arcilogger_core.admin.testing import Installed
from arcilogger_core.layout import Layout
from arcilogger_core.license.testing import base_only_status, override_status
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, utc_now
from arcilogger_db.models import AuditLog, Source
from arcilogger_worker.alerts.system import backup_state_path

Login = Callable[..., LoggedIn]
API = f"{API_PREFIX}/compliance"
REPORT = f"{API}/report.pdf"
CONFIRM = f"{API}/config-backup"
ORDER = [
    "sources",
    "seal_yesterday",
    "chain",
    "retention",
    "tsa",
    "clock",
    "content_filter",
    "lease_evidence",
    "replica",
    "backup",
]
#: Wednesday 28 October 2026, 10:05:30 site time.
NOW = datetime(2026, 10, 28, 10, 5, 30, tzinfo=SITE_TZ).astimezone(UTC)
NEVER_CONFIRMED = (
    "Yapılandırma dosyalarının çevrimdışı kopyası hiç onaylanmadı (YEDEKLEME.md bölüm 3)."
)


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The test app with the compliance routes (W4-F mounts them; until then this module)."""
    if not any(getattr(route, "path", None) == API for route in app.routes):
        app.include_router(routes.router, prefix=API_PREFIX)
    return app


@pytest.fixture
def computed(monkeypatch: pytest.MonkeyPatch) -> list[datetime]:
    """The ``now`` of every computation of the cards by a route."""
    calls: list[datetime] = []
    real = routes.compute_status

    def counting(*args: Any) -> ComplianceStatus:
        calls.append(args[-1])
        return real(*args)

    monkeypatch.setattr(routes, "compute_status", counting)
    return calls


def grant_auditor(monkeypatch: pytest.MonkeyPatch) -> None:
    """The auditor's ``compliance.read`` of D15 (W4-F adds it to ``ROLE_PERMISSIONS``)."""
    monkeypatch.setitem(
        security.ROLE_PERMISSIONS,
        "auditor",
        security.ROLE_PERMISSIONS["auditor"] | {"compliance.read"},
    )


def audits(factory: sessionmaker[Session], action: str | None = None) -> list[AuditLog]:
    with factory() as session:
        query = select(AuditLog).order_by(AuditLog.id)
        if action is not None:
            query = query.where(AuditLog.action == action)
        rows = list(session.scalars(query))
        session.expunge_all()
        return rows


def audit_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return session.scalar(select(func.count()).select_from(AuditLog)) or 0


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def card(status: dict[str, Any], card_id: str) -> dict[str, Any]:
    found: dict[str, Any] = next(item for item in status["cards"] if item["id"] == card_id)
    return found


def untimed(item: dict[str, Any]) -> dict[str, Any]:
    """A card without its ``checked_at`` (cards computed at two moments)."""
    return {key: value for key, value in item.items() if key != "checked_at"}


def fresh_backup(layout: Layout) -> None:
    """``state/backup/last.json`` of a successful backup an hour ago (card 10's first rows)."""
    path = backup_state_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema": 1,
                "finished": isoformat_site(utc_now() - timedelta(hours=1), "seconds"),
                "ok": True,
                "file": "arcilogger-20261028-0140.dump",
                "size": 1_288_490_189,
                "sha256": "0" * 64,
                "error": None,
            }
        ),
        encoding="utf-8",
    )


# -- the cards -----------------------------------------------------------------------------------


def test_compliance_answers_the_ten_cards(
    client: TestClient, login_as: Login, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(routes, "utc_now", lambda: NOW)
    login_as(client, "admin")
    response = client.get(API)
    assert response.status_code == 200
    body = response.json()
    status = ComplianceStatus.model_validate(body)
    assert [item.id for item in status.cards] == ORDER
    assert [item.number for item in status.cards] == list(range(1, 11))
    tones = [item.tone for item in status.cards]
    assert status.counts.model_dump() == {
        tone: tones.count(tone) for tone in ("ok", "warning", "error", "neutral")
    }
    assert sum(status.counts.model_dump().values()) == 10
    assert status.site_name == EXAMPLE_SITE_NAME
    assert body["generated_at"] == "2026-10-28T10:05:30+03:00"
    assert {item["checked_at"] for item in body["cards"]} == {"2026-10-28T10:05:30+03:00"}
    assert status.notice_tr.startswith(
        "ARCILOGGER, 5651 yükümlülüklerini desteklemek için tasarlanmıştır."
    )
    assert status.notice_en.startswith("ARCILOGGER is designed to support 5651 obligations.")
    # every field of contract 2.3.1 is in the answer, nothing else
    assert set(body) == {"generated_at", "site_name", "counts", "cards", "notice_tr", "notice_en"}
    assert set(body["cards"][0]) == {
        "id",
        "number",
        "tone",
        "title_tr",
        "title_en",
        "reason_tr",
        "reason_en",
        "facts",
        "alerts",
        "page",
        "doc_tr",
        "doc_en",
        "verify_on_site_tr",
        "verify_on_site_en",
        "checked_at",
    }
    # a new site without sources: card 1 asks for them
    assert card(body, "sources")["tone"] == "warning"
    assert card(body, "sources")["reason_en"] == (
        "No log source is watched. Define the sources in Settings → Sources."
    )


def test_compliance_is_refused_without_compliance_read(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    computed: list[datetime],
) -> None:
    with TestClient(app) as anonymous:
        assert error(anonymous.get(API)) == (401, "not_authenticated")
        assert error(anonymous.get(REPORT)) == (401, "not_authenticated")
    for role in ("hr", "viewer"):
        assert "compliance.read" not in security.ROLE_PERMISSIONS[role]
        with TestClient(app) as client:
            login_as(client, role)
            assert error(client.get(API)) == (403, "forbidden")
            response = client.get(REPORT)
            assert error(response) == (403, "forbidden")
            assert not response.content.startswith(b"%PDF")
    assert computed == []  # nothing computed for a refused request
    assert audits(app_session_factory, routes.REPORT_EXPORT) == []


def test_auditor_reads_the_cards_and_the_report(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant_auditor(monkeypatch)
    with TestClient(app) as client:
        auditor = login_as(client, "auditor", display_name="Deniz Örnek")
        response = client.get(API)
        assert response.status_code == 200
        assert [item["id"] for item in response.json()["cards"]] == ORDER
        report = client.get(REPORT)
        assert report.status_code == 200
        assert report.headers["content-type"] == "application/pdf"
        assert report.content.startswith(b"%PDF")
    [entry] = audits(app_session_factory, routes.REPORT_EXPORT)
    assert entry.actor_id == auditor.user_id
    assert entry.actor_username == auditor.username
    assert entry.details["sha256"] == hashlib.sha256(report.content).hexdigest()


def test_compliance_is_not_licence_gated(
    app: FastAPI, client: TestClient, login_as: Login
) -> None:
    for route in routes.router.routes:
        assert isinstance(route, APIRoute)
        assert not [
            dependency
            for dependency in route.dependant.dependencies
            if hasattr(dependency.call, MODULE_MARKER)
        ], route.path
    login_as(client, "admin")
    with override_status(base_only_status()):  # 5651 Temel only: every paid module locked
        response = client.get(API)
        assert response.status_code == 200
        assert len(response.json()["cards"]) == 10
        report = client.get(REPORT)
        assert report.status_code == 200
        assert report.content.startswith(b"%PDF")
        # the confirmation is refused for its own reason (no configuration file), not licence
        assert error(client.post(CONFIRM)) == (409, "config_backup_unreadable")


def test_compliance_reads_are_not_audited(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "admin")
    before = audit_count(app_session_factory)
    for _ in range(3):
        assert client.get(API).status_code == 200
    app_state_cache(client.app).invalidate()  # a computed answer is not audited either
    assert client.get(API).status_code == 200
    assert audit_count(app_session_factory) == before


def app_state_cache(app: Any) -> TtlCache[ComplianceStatus]:
    cache: TtlCache[ComplianceStatus] = app.state.arci_compliance
    return cache


def test_compliance_answer_is_cached_for_30_seconds(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    computed: list[datetime],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant_auditor(monkeypatch)
    with TestClient(app) as admin, TestClient(app) as auditor:
        login_as(admin, "admin")
        login_as(auditor, "auditor")
        first = admin.get(API).json()
        assert len(computed) == 1
        cache = app_state_cache(app)
        assert cache.ttl == routes.CACHE_SECONDS == 30.0
        clock = [1000.0]
        cache.invalidate()
        cache.clock = lambda: clock[0]
        second = admin.get(API).json()
        assert [untimed(item) for item in second["cards"]] == [
            untimed(item) for item in first["cards"]
        ]
        assert len(computed) == 2
        # a watched source appears: the kept answer does not know it for 30 seconds
        with app_session_factory() as session:
            session.add(Source(name="core-r1", type="mikrotik", last_seen=utc_now()))
            session.commit()
        clock[0] = 1029.9
        kept = auditor.get(API).json()  # one answer for every user of the process
        assert kept == second
        assert card(kept, "sources")["tone"] == "warning"
        assert admin.get(API).json() == kept
        assert len(computed) == 2
        # the report is never served from the kept answer
        assert admin.get(REPORT).status_code == 200
        assert len(computed) == 3
        clock[0] = 1030.0
        fresh = admin.get(API).json()
        assert len(computed) == 4
        assert card(fresh, "sources")["tone"] == "ok"
        assert card(fresh, "sources")["reason_tr"] != card(first, "sources")["reason_tr"]
        assert auditor.get(API).json() == fresh
        assert len(computed) == 4


# -- the report -----------------------------------------------------------------------------------


def test_report_pdf_is_audited_with_its_sha256(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(routes, "utc_now", lambda: NOW)
    admin = login_as(client, "admin")
    counts = client.get(API).json()["counts"]
    response = client.get(REPORT)
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["content-disposition"] == (
        'attachment; filename="ARCILOGGER-uyum-durumu-20261028-1005.pdf"'
    )
    sha256 = hashlib.sha256(response.content).hexdigest()
    assert response.headers["x-arci-sha256"] == sha256
    assert response.content.startswith(b"%PDF")
    # the entry is committed when the response arrives
    [entry] = audits(app_session_factory, routes.REPORT_EXPORT)
    assert entry.action == "compliance.report_export"
    assert entry.target is None
    assert entry.actor_id == admin.user_id
    assert entry.details == {"sha256": sha256, **counts}
    assert all(type(value) is int for key, value in entry.details.items() if key != "sha256")
    assert sum(entry.details[tone] for tone in ("ok", "warning", "error", "neutral")) == 10
    # every export is a new entry
    again = client.get(REPORT)
    assert len(audits(app_session_factory, routes.REPORT_EXPORT)) == 2
    assert again.headers["x-arci-sha256"] == sha256  # same cards, same moment: same bytes


def test_report_pdf_is_not_sent_without_its_audit_entry(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit spool not writable")

    monkeypatch.setattr(routes, "record_audit", failing)
    with TestClient(app, raise_server_exceptions=False) as client:
        login_as(client, "admin")
        response = client.get(REPORT)
    assert response.status_code == 500
    assert not response.content.startswith(b"%PDF")
    assert "x-arci-sha256" not in response.headers
    assert audits(app_session_factory, routes.REPORT_EXPORT) == []


# -- the configuration backup confirmation --------------------------------------------------------


def test_config_backup_turns_card_10(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    installed: Installed,
    layout: Layout,
) -> None:
    fresh_backup(layout)
    admin = login_as(client, "admin")
    before = card(client.get(API).json(), "backup")
    assert before["tone"] == "warning"
    assert before["reason_tr"] == NEVER_CONFIRMED
    current = hashlib.sha256(installed.config_path.read_bytes()).hexdigest()

    response = client.post(CONFIRM)
    assert response.status_code == 200
    body = response.json()
    assert body["config_sha256"] == current
    confirmed_at = datetime.fromisoformat(body["confirmed_at"])
    assert confirmed_at.utcoffset() == timedelta(hours=3)  # site time
    [entry] = audits(app_session_factory, CONFIG_BACKUP_CONFIRM)
    assert entry.action == "compliance.config_backup_confirm"
    assert entry.details == {"config_sha256": current}
    assert entry.target is None
    assert entry.actor_id == admin.user_id
    assert entry.time == confirmed_at

    # the kept answer was dropped: card 10 turns at once, within the 30 seconds
    after = card(client.get(API).json(), "backup")
    assert after["tone"] == "ok"
    assert after["reason_en"].endswith(
        f"configuration copy confirmed on {confirmed_at.strftime('%d.%m.%Y %H:%M:%S')}."
    )
    assert admin.username not in json.dumps(after)  # never who confirmed

    # a repeated confirmation is a new entry
    assert client.post(CONFIRM).status_code == 200
    assert len(audits(app_session_factory, CONFIG_BACKUP_CONFIRM)) == 2


def test_config_backup_needs_settings_manage(
    app: FastAPI, login_as: Login, app_session_factory: sessionmaker[Session], installed: Installed
) -> None:
    with TestClient(app) as anonymous:
        anonymous.get(f"{API_PREFIX}/health")
        anonymous.headers["X-CSRF-Token"] = anonymous.cookies["arci_csrf"]
        assert error(anonymous.post(CONFIRM)) == (401, "not_authenticated")
    for role in ("hr", "viewer"):
        assert "settings.manage" not in security.ROLE_PERMISSIONS[role]
        with TestClient(app) as client:
            login_as(client, role)
            assert error(client.post(CONFIRM)) == (403, "forbidden")
    assert audits(app_session_factory, CONFIG_BACKUP_CONFIRM) == []


def test_config_backup_is_refused_for_the_auditor(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    installed: Installed,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    grant_auditor(monkeypatch)
    fresh_backup(layout)
    with TestClient(app) as client:
        login_as(client, "auditor")
        before = card(client.get(API).json(), "backup")
        assert before["reason_tr"] == NEVER_CONFIRMED
        assert error(client.post(CONFIRM)) == (403, "forbidden")
        app_state_cache(app).invalidate()  # computed again: still not confirmed
        assert untimed(card(client.get(API).json(), "backup")) == untimed(before)
    assert audits(app_session_factory, CONFIG_BACKUP_CONFIRM) == []


def test_config_backup_refused_when_the_file_cannot_be_read(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    installed: Installed,
    layout: Layout,
) -> None:
    fresh_backup(layout)
    login_as(client, "admin")
    before = card(client.get(API).json(), "backup")
    assert before["reason_tr"] == NEVER_CONFIRMED
    data = installed.config_path.read_bytes()
    installed.config_path.unlink()
    installed.config_path.mkdir()  # still there, but its bytes cannot be read
    try:
        response = client.post(CONFIRM)
    finally:
        installed.config_path.rmdir()
        installed.config_path.write_bytes(data)
    assert response.status_code == 409
    assert response.json()["error"] == {
        "code": "config_backup_unreadable",
        "message_tr": "Yapılandırma dosyası okunamadı; yedek onayı kaydedilmedi.",
        "message_en": (
            "The configuration file could not be read; the backup confirmation was not recorded."
        ),
    }
    assert audits(app_session_factory, CONFIG_BACKUP_CONFIRM) == []
    app_state_cache(client.app).invalidate()  # computed again: still not confirmed
    assert untimed(card(client.get(API).json(), "backup")) == untimed(before)
