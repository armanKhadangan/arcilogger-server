"""Archive reload API and the database window of the lookup and the search (Wave 5 contract §5,
G4 to G9).

* ``POST /archive-reloads``: 202 and the ``queued`` request; the audit entry ``archive.reload``
  (who, the days, the reason, the lookup) is written **before** the row (statement order), and
  no request is queued without it; the refusals of G5 (more than 14 days, a day not sealed in the
  vault, too little room on the database drive — no disk sample, no refusal; only the files not
  loaded yet count) and the validation;
  the permission of the asking function per reason (G6), the KVKK notice, another user's lookup;
  no licence gate (5651 Temel).
* ``GET /archive-reloads/{id}`` and ``GET /archive-reloads``: active first, own requests only for
  non-administrators.
* The window (:func:`arcilogger_api.archive.service.check_window`): a day before the cutoff the
  window job applied is gone (also inside a window raised since), a day older than the window
  without rows too (a lost database); a ``ready`` reload holds it, a ``queued`` or ``loading``
  one brings it back (waiting); a ``loading`` one makes the days its files have rows in gone
  (four days either side, inside the window too, a ``ready`` reload of them notwithstanding);
  a day the job has not dropped yet is read as it is; a gone day the vault does not have is no
  reload. The check holds the partition lock shared.
* A request of complete days of the window is refused; a second request of the same days is
  answered with the first (one load); a day the sealer skipped has nothing to load. Another
  user's request of days already loaded is a record and an audit entry of its own, and ingest
  makes it ``ready`` without a second load (W5-G answer 6); an administrator's request of
  another user's days as well.
* A sealed file that becomes unreadable while a reload loads it fails the reload and leaves its
  days gone — also under another user's ``ready`` reload: the lookup answers 409, never a
  partial answer (W5-G answer 9).
* ``POST /lookup/5651`` answers 409 ``archive_reload_required`` (audited ``lookup.run_failed``);
  the search answers ``archived`` or 409. Both name the current ``db.reload_keep_days`` (C23).
* **The G7 equality test**: a lookup over days that were dropped and then reloaded from the vault
  by the real ingest gives the same answer and the same raw-line references as before the drop;
  the same for a search over a day with a line of the next raw day in it (a session logged
  across midnight).

"Now" of the window is :data:`NOW`-style fixed moments (``archive.service.utc_now``); the site's
day is :data:`~arcilogger_api.lookup_testing.DAY` (12.10.2026).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import security
from arcilogger_api.archive import service as archive_service
from arcilogger_api.archive.models import ArchiveReloadFailureCode
from arcilogger_api.archive.service import (
    DB_BYTES_PER_RAW_BYTE,
    MAX_DAYS,
    MESSAGES,
    check_window,
    estimate,
)
from arcilogger_api.lookup_testing import (
    AT,
    DAY,
    PC,
    PC_MAC,
    WAN,
    Site,
    accept_privacy,
    build_site,
    iso,
    seal_day,
)
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.license.testing import base_only_status, override_status
from arcilogger_core.timeutil import day_window
from arcilogger_db.egress import legacy_egresses
from arcilogger_db.models import ArchiveReload, AuditLog, Lookup
from arcilogger_db.partitions import DROPPED_KEY, PARTITION_LOCK_KEY, drop_before
from arcilogger_db.settings import set_setting
from arcilogger_ingest import service as ingest_service
from arcilogger_ingest.files import VaultReader, VaultReadError
from arcilogger_ingest.service import IngestService
from arcilogger_worker.alerts.system import drives
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.testing import FIREWALL, ROUTER, Evidence, tr

Login = Callable[..., LoggedIn]
API = "/api/v1/archive-reloads"
LOOKUP = "/api/v1/lookup/5651"
SEARCH = "/api/v1/search"
#: Inside the window: the site's day and the lookup's days are 5 days old.
SOON = tr(DAY + timedelta(days=5), 12)
#: Outside: 60 days later (window 30 days).
LATER = tr(DAY + timedelta(days=60), 12)
#: The cutoff the window job applied (it dropped the site's day and the next).
CUTOFF = DAY + timedelta(days=2)
WAN_EXIT = legacy_egresses([], WAN, False)[0]


@pytest.fixture(autouse=True)
def _unlock_sealed_files(tmp_path: Path) -> Iterator[None]:
    yield
    for path in tmp_path.rglob("*"):
        if path.is_file() and is_locked(path):
            unlock(path)


class Clock:
    """The window's "now" (``archive.service.utc_now``)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.now = LATER
        monkeypatch.setattr(archive_service, "utc_now", lambda: self.now)


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """:data:`LATER` unless a test moves it: the site's day is outside the window."""
    return Clock(monkeypatch)


@pytest.fixture
def site(app_session_factory: sessionmaker[Session], layout: Layout) -> Site:
    return build_site(app_session_factory, layout)


@pytest.fixture
def sealed(site: Site, api_config: Config, layout: Layout) -> Site:
    seal_day(api_config, layout, DAY)
    return site


def signed_in(
    client: TestClient, login_as: Login, factory: sessionmaker[Session], role: str = "admin"
) -> LoggedIn:
    who = login_as(client, role)
    accept_privacy(factory, who.user_id)
    return who


def reload_body(first: date = DAY, last: date | None = None, **extra: Any) -> dict[str, Any]:
    return {
        "reason": "search",
        "first_day": first.isoformat(),
        "last_day": (last or first).isoformat(),
        **extra,
    }


def lookup_body(**query: Any) -> dict[str, Any]:
    return {
        "request": {
            "authority": "İstanbul Cumhuriyet Başsavcılığı",
            "file_no": "2026/88 Arşiv",
            "request_date": "2026-10-13",
            "reason": "Arşivden yeniden yükleme testi",
        },
        "query": {"at": iso(AT), **query},
    }


def sealed_bytes(layout: Layout, day: date) -> int:
    manifest = json.loads(layout.manifest(day).read_bytes())
    return sum(int(entry["bytes_uncompressed"]) for entry in manifest["files"])


def reload_rows(factory: sessionmaker[Session]) -> list[ArchiveReload]:
    with factory() as session:
        return list(session.scalars(select(ArchiveReload).order_by(ArchiveReload.id)))


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        return list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )


def drop(engine: Engine, cutoff: date = CUTOFF, *, lost: bool = False) -> None:
    """What the window job does: expire the ready reloads, drop the days before ``cutoff``
    (``drop_before`` records the cutoff). ``lost``: the rows are gone without the record (a
    database restored or lost, ingest loading only the window)."""
    with engine.begin() as connection:
        connection.execute(
            text("UPDATE archive_reloads SET status = 'expired' WHERE status = 'ready'")
        )
        drop_before(connection, cutoff)
        if lost:
            connection.execute(text("DELETE FROM settings WHERE key = :key"), {"key": DROPPED_KEY})


def set_status(engine: Engine, reload_id: int, status: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "UPDATE archive_reloads SET status = :status, ready_at = now(), "
                "expires_at = now() + interval '7 days' WHERE id = :id"
            ),
            {"status": status, "id": reload_id},
        )


def disk_sample(layout: Layout, data_dir: Path, *, total: int, used: int) -> None:
    """The worker's ``system.sample`` of the data drive (the PostgreSQL drive)."""
    label = drives(data_dir)[0][0]
    layout.system_history.parent.mkdir(parents=True, exist_ok=True)
    days = [
        {"day": "2026-12-10", "drives": [{"path": label, "total": total, "used": 1}]},
        {"day": "2026-12-11", "drives": [{"path": label, "total": total, "used": used}]},
    ]
    layout.system_history.write_text(json.dumps({"schema": 1, "days": days}), encoding="utf-8")


def ingest_config(api_config: Config, *, batch_rows: int = 500) -> Config:
    """ArciIngest of the site: its two sources, the API's data folder."""
    return Config.model_validate(
        {
            **api_config.model_dump(),
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": FIREWALL},
                {"name": "core-r1", "type": "mikrotik", "address": ROUTER},
            ],
            "ingest": {"batch_rows": batch_rows, "batch_seconds": 0.05, "poll_seconds": 0.02},
        }
    )


def run_ingest(
    api_config: Config,
    engine: Engine,
    *,
    batch_rows: int = 500,
    archive_error_seconds: float = ingest_service.ARCHIVE_ERROR_SECONDS,
) -> None:
    """ArciIngest carries out the queued reloads (its clock 60 days on: no vault day of the
    site is in its own window, so only the reload loads it)."""
    service = IngestService(
        ingest_config(api_config, batch_rows=batch_rows),
        engine,
        clock=lambda: LATER,
        status_interval=0.05,
        backoff_max=0.2,
        archive_seconds=0.0,
        archive_error_seconds=archive_error_seconds,
    )
    assert service.run(once=True) == 0


def message_ids(engine: Engine) -> list[int]:
    with engine.connect() as connection:
        return list(connection.execute(text("SELECT id FROM messages ORDER BY id")).scalars())


def skip_day(layout: Layout, day: date) -> None:
    """The sealer skipped ``day`` (no line): a request may name it, nothing to load."""
    layout.seal_state(day).write_text(
        json.dumps({"day": day.isoformat(), "status": "skipped"}), encoding="utf-8"
    )


def sealed_file(layout: Layout, day: date, source: str) -> tuple[Path, int]:
    """The sealed file of ``(source, day)`` and its ``bytes_uncompressed`` (the manifest)."""
    manifest = json.loads(layout.manifest(day).read_bytes())
    entry = next(entry for entry in manifest["files"] if entry["source"] == source)
    return layout.vault / str(entry["path"]), int(entry["bytes_uncompressed"])


def unreadable_mid_load(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    """``path`` becomes unreadable while ArciIngest loads it: at the loader's second read of it
    the file is damaged in place, and that read and every later one fail (the reset's reading of
    its receive times, :func:`~arcilogger_ingest.service.received_outside`, comes before the load
    and is not counted). (A reader meets such damage where its buffer ends; a test file fits in
    the buffer, so the reader is told.)"""
    reads = 0
    resetting = False
    outside = ingest_service.received_outside

    def reset_read(file: Path, day: date) -> list[datetime]:
        nonlocal resetting
        resetting = True
        try:
            return outside(file, day)
        finally:
            resetting = False

    class Damaged(VaultReader):
        def read(self, max_bytes: int) -> bytes:
            nonlocal reads
            if self.path.name == path.name and not resetting:
                reads += 1
                if reads == 2:
                    unlock(path)
                    path.write_bytes(path.read_bytes()[:64])
                if reads >= 2:
                    raise VaultReadError(f"{path.name}: damaged gzip data")
            return super().read(max_bytes)

    monkeypatch.setattr(ingest_service, "VaultReader", Damaged)
    monkeypatch.setattr(ingest_service, "received_outside", reset_read)


# -- POST /archive-reloads ------------------------------------------------------------------------


def test_a_request_is_queued_after_its_audit_entry(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    sealed: Site,
) -> None:
    who = signed_in(client, login_as, app_session_factory)
    statements: list[str] = []

    def seen(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        for table in ("audit_log", "archive_reloads"):
            if statement.lstrip().upper().startswith("INSERT INTO " + table.upper()):
                statements.append(table)

    event.listen(db_engine, "before_cursor_execute", seen)
    try:
        response = client.post(API, json=reload_body(reason="search"))
    finally:
        event.remove(db_engine, "before_cursor_execute", seen)
    assert response.status_code == 202, response.text
    assert statements == ["audit_log", "archive_reloads"]  # G6: audited before it is queued
    data = response.json()
    total = sealed_bytes(layout, DAY)
    assert total > 0
    assert {key: data[key] for key in ("requested_by", "reason", "status", "lookup_id")} == {
        "requested_by": who.username,
        "reason": "search",
        "status": "queued",
        "lookup_id": None,
    }
    assert (data["first_day"], data["last_day"]) == (DAY.isoformat(), DAY.isoformat())
    assert (data["bytes_total"], data["bytes_done"], data["error_code"]) == (total, 0, None)
    assert (data["ready_at"], data["expires_at"]) == (None, None)
    (row,) = reload_rows(app_session_factory)
    assert (row.id, row.status) == (data["id"], "queued")
    (entry,) = audits(app_session_factory, "archive.reload")
    assert (entry.actor_id, entry.target) == (who.user_id, f"archive_reload:{row.id}")
    assert entry.details == {
        "archive_reload_id": row.id,
        "reason": "search",
        "lookup_id": None,
        "first_day": DAY.isoformat(),
        "last_day": DAY.isoformat(),
        "days": 1,
        "bytes_total": total,
        "estimated_bytes": estimate(total),
    }


def test_no_request_is_queued_without_its_audit_entry(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    sealed: Site,
) -> None:
    signed_in(client, login_as, app_session_factory)

    def failing(*args: Any, **kwargs: Any) -> None:
        raise RuntimeError("audit spool unavailable")

    monkeypatch.setattr(archive_service, "record_audit", failing)
    with pytest.raises(RuntimeError, match="audit spool"):
        client.post(API, json=reload_body())
    assert reload_rows(app_session_factory) == []


def test_more_than_fourteen_days_is_refused(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], sealed: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    assert MAX_DAYS == 14
    response = client.post(API, json=reload_body(DAY, DAY + timedelta(days=14)))
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "archive_reload_too_large"
    assert error["details"] == {"days": 15, "max_days": 14}
    assert "14" in error["message_tr"]
    assert "15" in error["message_en"]
    assert reload_rows(app_session_factory) == []
    assert audits(app_session_factory, "archive.reload") == []


def test_a_day_without_a_sealed_manifest_is_refused(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], sealed: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    response = client.post(API, json=reload_body(DAY - timedelta(days=1), DAY + timedelta(days=1)))
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "archive_day_not_in_vault"
    assert error["details"] == {
        "days": [(DAY - timedelta(days=1)).isoformat(), (DAY + timedelta(days=1)).isoformat()]
    }
    assert "11.10.2026, 13.10.2026" in error["message_tr"]
    assert "2026-10-11, 2026-10-13" in error["message_en"]
    assert reload_rows(app_session_factory) == []


def test_too_little_room_on_the_database_drive_is_refused(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
    sealed: Site,
) -> None:
    signed_in(client, login_as, app_session_factory)
    estimated = estimate(sealed_bytes(layout, DAY))
    total = 100 * estimated
    # 15 % free before, 14 % after the load: refused (the newest sample counts).
    disk_sample(layout, api_config.data_dir, total=total, used=total * 85 // 100)
    response = client.post(API, json=reload_body())
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "archive_reload_disk_low"
    assert error["details"]["estimated_bytes"] == estimated
    assert error["details"]["free_bytes"] == total - total * 85 // 100
    assert "%15" in error["message_tr"]
    assert "15 %" in error["message_en"]
    assert reload_rows(app_session_factory) == []
    # 17 % free: 16 % after the load, accepted.
    disk_sample(layout, api_config.data_dir, total=total, used=total * 83 // 100)
    assert client.post(API, json=reload_body()).status_code == 202


def test_no_disk_sample_no_refusal(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    sealed: Site,
) -> None:
    signed_in(client, login_as, app_session_factory)
    assert not layout.system_history.exists()
    assert client.post(API, json=reload_body()).status_code == 202


def test_the_estimate_is_the_measured_factor() -> None:
    assert DB_BYTES_PER_RAW_BYTE == 2.6
    assert estimate(1_000_000) == 2_600_000
    assert estimate(3) == 8  # rounded up


@pytest.mark.parametrize(
    ("body", "loc"),
    [
        (reload_body(DAY, DAY - timedelta(days=1)), ["body", "last_day"]),
        (reload_body(lookup_id=1), ["body", "lookup_id"]),
        ({"reason": "export", "first_day": "2026-10-12", "last_day": "2026-10-12"}, None),
    ],
)
def test_invalid_requests(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    body: dict[str, Any],
    loc: list[str] | None,
) -> None:
    signed_in(client, login_as, app_session_factory)
    response = client.post(API, json=body)
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    if loc is not None:
        assert error["details"][0]["loc"] == loc


REASONS = ("lookup", "search")


def test_the_permission_of_the_asking_function(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    sealed: Site,
) -> None:
    # No role holds only one of the two today: viewers get search.read for this test.
    viewer = security.ROLE_PERMISSIONS["viewer"] | {"search.read"}
    monkeypatch.setitem(security.ROLE_PERMISSIONS, "viewer", viewer)
    with TestClient(app, base_url="http://testserver") as client:
        signed_in(client, login_as, app_session_factory, "viewer")
        refused = client.post(API, json=reload_body(reason="lookup"))
        assert refused.status_code == 403, refused.text
        assert refused.json()["error"]["code"] == "forbidden"
        assert client.post(API, json=reload_body(reason="search")).status_code == 202
        assert client.get(API).status_code == 200
    with TestClient(app, base_url="http://testserver") as client:
        signed_in(client, login_as, app_session_factory, "hr")  # neither permission
        for reason in ("lookup", "search"):
            response = client.post(API, json=reload_body(reason=reason))
            assert response.status_code == 403, response.text
        assert client.get(API).status_code == 403
    with TestClient(app, base_url="http://testserver") as client:
        signed_in(client, login_as, app_session_factory, "auditor")  # both
        answers = [client.post(API, json=reload_body(reason=reason)) for reason in REASONS]
        assert [answer.status_code for answer in answers] == [202, 202]
        # The same days again: the auditor's own active request is the answer, one load.
        assert answers[0].json()["id"] == answers[1].json()["id"]
    assert [row.reason for row in reload_rows(app_session_factory)] == ["search", "lookup"]


def test_the_kvkk_notice_is_needed(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], sealed: Site
) -> None:
    login_as(client, "auditor")  # notice not accepted
    response = client.post(API, json=reload_body())
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "privacy_notice_required"
    assert reload_rows(app_session_factory) == []


def test_no_licence_gate(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], sealed: Site
) -> None:
    signed_in(client, login_as, app_session_factory)
    with override_status(base_only_status()):  # 5651 Temel only
        assert client.post(API, json=reload_body()).status_code == 202
        assert client.get(API).status_code == 200


def test_a_lookup_reload_names_the_lookup(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    sealed: Site,
) -> None:
    with TestClient(app, base_url="http://testserver") as client:
        signed_in(client, login_as, app_session_factory, "auditor")
        created = client.post(LOOKUP, json=lookup_body(internal_ip=PC))
        assert created.status_code == 201, created.text
        own = created.json()["id"]
        response = client.post(API, json=reload_body(reason="lookup", lookup_id=own))
        assert response.status_code == 202, response.text
        assert response.json()["lookup_id"] == own
        (entry,) = audits(app_session_factory, "archive.reload")
        assert (entry.details["reason"], entry.details["lookup_id"]) == ("lookup", own)
    with TestClient(app, base_url="http://testserver") as client:
        signed_in(client, login_as, app_session_factory, "auditor")  # another auditor
        response = client.post(API, json=reload_body(reason="lookup", lookup_id=own))
        assert response.status_code == 404, response.text
        missing = client.post(API, json=reload_body(reason="lookup", lookup_id=own + 1000))
        assert missing.status_code == 404
    assert len(reload_rows(app_session_factory)) == 1


# -- GET ------------------------------------------------------------------------------------------


def test_get_and_list(
    app: FastAPI,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    sealed: Site,
) -> None:
    with TestClient(app, base_url="http://testserver") as admin:
        manager = signed_in(admin, login_as, app_session_factory)
    with app_session_factory() as session:  # four of the administrator's requests
        rows = [
            ArchiveReload(
                requested_by=manager.username,
                reason="search",
                first_day=DAY,
                last_day=DAY,
                status="queued",
            )
            for _ in range(4)
        ]
        session.add_all(rows)
        session.commit()
        ids = [row.id for row in rows]
    with TestClient(app, base_url="http://testserver") as auditor:
        signed_in(auditor, login_as, app_session_factory, "auditor")
        own = auditor.post(API, json=reload_body(reason="lookup")).json()["id"]
        set_status(db_engine, ids[0], "expired")
        set_status(db_engine, ids[1], "ready")
        with db_engine.begin() as connection:
            connection.execute(
                text(
                    "UPDATE archive_reloads SET status = 'loading', bytes_done = 7 WHERE id = :id"
                ),
                {"id": ids[2]},
            )
        assert auditor.get(f"{API}/{ids[2]}").status_code == 404  # another user's
        mine = auditor.get(f"{API}/{own}")
        assert mine.status_code == 200
        assert (mine.json()["id"], mine.json()["status"]) == (own, "queued")
        assert [item["id"] for item in auditor.get(API).json()["items"]] == [own]
        assert auditor.get(f"{API}/{2**63}").status_code == 422
    with TestClient(app, base_url="http://testserver") as admin:
        signed_in(admin, login_as, app_session_factory)
        loading = admin.get(f"{API}/{ids[2]}").json()
        assert (loading["status"], loading["bytes_done"]) == ("loading", 7)
        items = admin.get(API).json()["items"]
        # Active first (loading, queued in order, ready), then the others, newest first.
        assert [(item["id"], item["status"]) for item in items] == [
            (ids[2], "loading"),
            (ids[3], "queued"),
            (own, "queued"),
            (ids[1], "ready"),
            (ids[0], "expired"),
        ]
        assert admin.get(f"{API}/{own + 100}").status_code == 404


# -- the window -----------------------------------------------------------------------------------


def window_of(factory: sessionmaker[Session], layout: Layout, first: date, last: date) -> Any:
    with factory() as session:
        return check_window(session, layout, first, last)


def test_a_day_not_dropped_yet_is_read_as_it_is(
    app_session_factory: sessionmaker[Session], layout: Layout, clock: Clock, sealed: Site
) -> None:
    # 60 days on, but the window job has not dropped the day: its rows are there.
    found = window_of(app_session_factory, layout, DAY - timedelta(days=1), DAY + timedelta(days=1))
    assert (found.missing, found.all_gone) == ((), False)
    assert found.required() is None
    assert found.archived() is None


def test_a_day_before_the_applied_cutoff_is_gone(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    clock: Clock,
    sealed: Site,
) -> None:
    drop(db_engine)
    found = window_of(app_session_factory, layout, DAY - timedelta(days=1), DAY + timedelta(days=1))
    # Only the sealed day can come back; the days beside it are gone and not in the vault.
    assert (found.missing, found.all_gone) == ((DAY,), True)
    required = found.required()
    total = sealed_bytes(layout, DAY)
    assert required is not None
    assert required.model_dump(mode="json") == {
        "first_day": DAY.isoformat(),
        "last_day": DAY.isoformat(),
        "bytes_total": total,
        "estimated_bytes": estimate(total),
        "keep_days": 7,
    }
    # A larger window since (the day is inside it again): what the job dropped is still gone.
    clock.now = SOON
    assert window_of(app_session_factory, layout, DAY, DAY).missing == (DAY,)


def test_a_lost_database_has_gone_days_without_a_job_run(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    clock: Clock,
    sealed: Site,
) -> None:
    drop(db_engine, lost=True)  # no cutoff on record, no rows: gone all the same
    assert window_of(app_session_factory, layout, DAY, DAY).missing == (DAY,)


def test_a_ready_reload_holds_its_days_a_pending_one_does_not(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    clock: Clock,
    sealed: Site,
) -> None:
    signed_in(client, login_as, app_session_factory)
    drop(db_engine)
    reload_id = client.post(API, json=reload_body()).json()["id"]
    for status in ("queued", "loading"):
        with db_engine.begin() as connection:
            connection.execute(
                text("UPDATE archive_reloads SET status = :s WHERE id = :id"),
                {"s": status, "id": reload_id},
            )
        found = window_of(app_session_factory, layout, DAY, DAY)
        # Gone, and coming back with that request: no new one is needed.
        assert (found.gone, found.missing, found.waiting) == ((DAY,), (), (DAY,))
        assert found.required() is not None
    set_status(db_engine, reload_id, "ready")
    assert window_of(app_session_factory, layout, DAY, DAY).missing == ()
    set_status(db_engine, reload_id, "expired")
    assert window_of(app_session_factory, layout, DAY, DAY).missing == (DAY,)


def test_a_gone_day_the_vault_lacks_is_no_reload(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    clock: Clock,
    site: Site,
) -> None:
    drop(db_engine)  # the day was never sealed (or retention removed it from the vault)
    found = window_of(app_session_factory, layout, DAY, DAY)
    assert (found.missing, found.all_gone) == ((), True)
    assert found.required() is None


def test_a_loading_reload_makes_the_days_of_its_files_gone(
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    clock: Clock,
    sealed: Site,
) -> None:
    """A ``loading`` reload of ``F`` … ``L`` replaces the raw days ``F - 2`` … ``L + 2``, whose
    rows lie in ``F - 4`` … ``L + 4``: those days are gone while it loads — inside the window
    too, and although a ready reload holds them — and come back with it (waiting). A queued
    reload changes nothing yet (its load starts under the partition lock, after the check)."""
    clock.now = SOON  # the site's day is in the window, nothing dropped
    first, last = DAY - timedelta(days=5), DAY + timedelta(days=5)
    with app_session_factory() as session:
        for status in ("ready", "queued"):
            session.add(
                ArchiveReload(
                    requested_by="admin",
                    reason="search",
                    first_day=DAY,
                    last_day=DAY,
                    status=status,
                    ready_at=SOON if status == "ready" else None,
                    expires_at=SOON + timedelta(days=7) if status == "ready" else None,
                )
            )
        session.commit()
    found = window_of(app_session_factory, layout, first, last)
    assert (found.gone, found.all_gone, found.required()) == ((), False, None)
    with db_engine.begin() as connection:
        connection.execute(
            text("UPDATE archive_reloads SET status = 'loading' WHERE status = 'queued'")
        )
    found = window_of(app_session_factory, layout, first, last)
    replaced = tuple(DAY + timedelta(days=n) for n in range(-4, 5))
    assert (found.gone, found.waiting, found.missing) == (replaced, replaced, ())
    required = found.required()
    assert required is not None
    assert (required.first_day, required.last_day) == (replaced[0], replaced[-1])
    assert required.bytes_total == sealed_bytes(layout, DAY)  # the sealed raw days of it


def test_the_check_holds_the_partition_lock_shared(
    app_session_factory: sessionmaker[Session], db_engine: Engine, layout: Layout, sealed: Site
) -> None:
    """Until the caller's transaction ends (the lookup's, the search's reads), neither the
    window job nor a reload's start (both take the lock exclusively) runs."""
    try_lock = text("SELECT pg_try_advisory_xact_lock(:key)")
    with app_session_factory() as session:
        check_window(session, layout, DAY, DAY)
        with db_engine.connect() as other:
            assert other.execute(try_lock, {"key": PARTITION_LOCK_KEY}).scalar_one() is False
            other.rollback()
        session.rollback()
        with db_engine.connect() as other:
            assert other.execute(try_lock, {"key": PARTITION_LOCK_KEY}).scalar_one() is True


def test_complete_days_of_the_window_are_no_reload(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    clock: Clock,
    sealed: Site,
) -> None:
    """Reloading a day the database holds whole would only take it away while it loads."""
    signed_in(client, login_as, app_session_factory)
    clock.now = SOON
    response = client.post(API, json=reload_body())
    assert response.status_code == 422, response.text
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert error["details"][0]["loc"] == ["body", "first_day"]
    assert reload_rows(app_session_factory) == []
    assert audits(app_session_factory, "archive.reload") == []


def test_a_second_request_of_the_same_days_is_answered_with_the_first(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    sealed: Site,
) -> None:
    """Two clicks, or the web app asking again: one load (audited twice)."""
    signed_in(client, login_as, app_session_factory)
    drop(db_engine)
    first = client.post(API, json=reload_body())
    second = client.post(API, json=reload_body(reason="lookup"))
    assert (first.status_code, second.status_code) == (202, 202)
    assert first.json() == second.json()
    assert len(reload_rows(app_session_factory)) == 1
    entries = audits(app_session_factory, "archive.reload")
    assert [entry.details.get("existing") for entry in entries] == [None, True]
    assert {entry.target for entry in entries} == {f"archive_reload:{first.json()['id']}"}


def test_another_users_request_of_loaded_days_is_its_own_record_and_no_second_load(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    api_config: Config,
    layout: Layout,
    sealed: Site,
) -> None:
    """Two users, two records, one load (W5-G answer 6): each person's request is a record and
    an audit entry of its own (each access is traceable); ArciIngest finds the days loaded by the
    first one and makes the second ``ready`` without deleting or loading a row again."""
    first_user = signed_in(client, login_as, app_session_factory)
    drop(db_engine)
    first = client.post(API, json=reload_body())
    assert first.status_code == 202, first.text
    run_ingest(api_config, db_engine)
    loaded = message_ids(db_engine)
    assert loaded
    second_user = signed_in(client, login_as, app_session_factory, "auditor")
    second = client.post(API, json=reload_body(reason="lookup"))
    assert second.status_code == 202, second.text
    assert second.json()["id"] != first.json()["id"]
    assert (second.json()["requested_by"], second.json()["status"]) == (
        second_user.username,
        "queued",
    )
    run_ingest(api_config, db_engine)
    ready = client.get(f"{API}/{second.json()['id']}").json()
    assert ready["status"] == "ready", ready
    assert ready["bytes_done"] == ready["bytes_total"] == sealed_bytes(layout, DAY)
    assert message_ids(db_engine) == loaded  # nothing deleted, nothing loaded again
    entries = audits(app_session_factory, "archive.reload")
    assert [(entry.actor_id, entry.target) for entry in entries] == [
        (first_user.user_id, f"archive_reload:{first.json()['id']}"),
        (second_user.user_id, f"archive_reload:{second.json()['id']}"),
    ]
    assert [entry.details.get("existing") for entry in entries] == [None, None]


def test_an_administrators_request_of_another_users_days_is_its_own_record(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    sealed: Site,
) -> None:
    """W5-G answer 6 holds for an administrator too: another user's active request of the days
    is not the answer (no ``existing`` entry pointing at it), the administrator's request is a
    record and an audit entry of its own, and the other user's record stays as it was. The
    administrator still sees both (``GET /archive-reloads``: everyone's)."""
    other = signed_in(client, login_as, app_session_factory, "auditor")
    drop(db_engine)
    first = client.post(API, json=reload_body(reason="lookup"))
    assert first.status_code == 202, first.text
    manager = signed_in(client, login_as, app_session_factory)
    second = client.post(API, json=reload_body())
    assert second.status_code == 202, second.text
    assert second.json()["id"] != first.json()["id"]
    assert (second.json()["requested_by"], second.json()["status"], second.json()["reason"]) == (
        manager.username,
        "queued",
        "search",
    )
    assert second.json()["bytes_total"] == sealed_bytes(layout, DAY)
    assert client.get(f"{API}/{first.json()['id']}").json() == first.json()  # unchanged
    assert [item["id"] for item in client.get(API).json()["items"]] == [
        first.json()["id"],
        second.json()["id"],
    ]
    rows = reload_rows(app_session_factory)
    assert [(row.id, row.requested_by) for row in rows] == [
        (first.json()["id"], other.username),
        (second.json()["id"], manager.username),
    ]
    entries = audits(app_session_factory, "archive.reload")
    assert [(entry.actor_id, entry.target, entry.details.get("existing")) for entry in entries] == [
        (other.user_id, f"archive_reload:{first.json()['id']}", None),
        (manager.user_id, f"archive_reload:{second.json()['id']}", None),
    ]
    # A second click of the administrator is answered with the administrator's own request.
    again = client.post(API, json=reload_body())
    assert again.status_code == 202, again.text
    assert again.json()["id"] == second.json()["id"]
    last = audits(app_session_factory, "archive.reload")[-1]
    assert (last.actor_id, last.target, last.details.get("existing")) == (
        manager.user_id,
        f"archive_reload:{second.json()['id']}",
        True,
    )


def test_the_disk_check_counts_only_the_files_not_loaded_yet(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    api_config: Config,
    layout: Layout,
    sealed: Site,
) -> None:
    """A file ArciIngest would find loaded (another user's ``ready`` reload holds its days) is
    neither reset nor loaded again, so it takes no room: a request of such days is accepted
    on a drive too full for a full load, and becomes ``ready`` without a load. While the files
    are not loaded yet (the other request still ``queued``), the full load counts."""
    signed_in(client, login_as, app_session_factory)
    drop(db_engine)
    first = client.post(API, json=reload_body())  # no disk sample yet
    assert first.status_code == 202, first.text
    full = estimate(sealed_bytes(layout, DAY))
    total = 100 * full
    # 15 % free: 14 % after a full load (refused), 15 % after none.
    disk_sample(layout, api_config.data_dir, total=total, used=total * 85 // 100)
    second_user = signed_in(client, login_as, app_session_factory, "auditor")
    refused = client.post(API, json=reload_body(reason="lookup"))
    assert refused.status_code == 422, refused.text
    error = refused.json()["error"]
    assert (error["code"], error["details"]["estimated_bytes"]) == ("archive_reload_disk_low", full)
    assert len(reload_rows(app_session_factory)) == 1

    run_ingest(api_config, db_engine)
    assert [row.status for row in reload_rows(app_session_factory)] == ["ready"]
    loaded = message_ids(db_engine)
    assert loaded
    accepted = client.post(API, json=reload_body(reason="lookup"))
    assert accepted.status_code == 202, accepted.text
    assert (accepted.json()["requested_by"], accepted.json()["status"]) == (
        second_user.username,
        "queued",
    )
    # The progress runs over every file: ArciIngest counts the loaded ones as done.
    assert accepted.json()["bytes_total"] == sealed_bytes(layout, DAY)
    entry = audits(app_session_factory, "archive.reload")[-1]
    assert entry.target == f"archive_reload:{accepted.json()['id']}"
    assert (entry.details["bytes_total"], entry.details["estimated_bytes"]) == (
        sealed_bytes(layout, DAY),
        0,
    )
    run_ingest(api_config, db_engine)
    ready = client.get(f"{API}/{accepted.json()['id']}").json()
    assert ready["status"] == "ready", ready
    assert ready["bytes_done"] == ready["bytes_total"] == sealed_bytes(layout, DAY)
    assert message_ids(db_engine) == loaded  # nothing deleted, nothing loaded again


def test_a_day_the_sealer_skipped_has_nothing_to_load(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    sealed: Site,
) -> None:
    """A day without a line is ``skipped``, with no manifest: a range across it loads the others
    (a 409 range may hold such a day)."""
    signed_in(client, login_as, app_session_factory)
    skipped = DAY + timedelta(days=1)
    layout.seal_state(skipped).write_text(
        json.dumps({"day": skipped.isoformat(), "status": "skipped"}), encoding="utf-8"
    )
    response = client.post(API, json=reload_body(DAY, skipped))
    assert response.status_code == 202, response.text
    assert response.json()["bytes_total"] == sealed_bytes(layout, DAY)


# -- the lookup (G7) ------------------------------------------------------------------------------


def test_a_lookup_over_gone_days_answers_409(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    layout: Layout,
    clock: Clock,
    sealed: Site,
) -> None:
    who = signed_in(client, login_as, app_session_factory, "auditor")
    drop(db_engine)
    response = client.post(LOOKUP, json=lookup_body(internal_ip=PC))
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    total = sealed_bytes(layout, DAY)
    assert error["code"] == "archive_reload_required"
    assert error["details"] == {
        "first_day": DAY.isoformat(),
        "last_day": DAY.isoformat(),
        "bytes_total": total,
        "estimated_bytes": estimate(total),
        "keep_days": 7,
    }
    assert "12.10.2026 - 12.10.2026" in error["message_tr"]
    assert "2026-10-12 - 2026-10-12" in error["message_en"]
    assert "30" in error["message_tr"]
    with app_session_factory() as session:
        assert session.scalars(select(Lookup)).all() == []  # never a "not found" answer
    (entry,) = audits(app_session_factory, "lookup.run_failed")
    assert entry.actor_id == who.user_id
    assert entry.details["reason"] == "archive_reload_required"
    assert entry.details["days"] == {
        "first": (DAY - timedelta(days=12)).isoformat(),
        "last": (DAY + timedelta(days=1)).isoformat(),
    }
    assert entry.details["missing"] == {"first": DAY.isoformat(), "last": DAY.isoformat()}
    assert entry.details["query"]["internal_ip"] == PC


def test_a_lookup_with_its_days_in_the_window_runs(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    clock: Clock,
    sealed: Site,
) -> None:
    signed_in(client, login_as, app_session_factory)
    clock.now = SOON  # every day of the lookup is in the window, none was dropped
    assert client.post(LOOKUP, json=lookup_body(internal_ip=PC)).status_code == 201


#: What differs between two runs of one lookup over the same rows: the row numbers (a reload
#: renumbers the rows it loads) and the time the outcome was made.
VARYING = frozenset({"row_id", "generated_at"})


def _without_row_ids(value: Any) -> Any:
    """The outcome without :data:`VARYING`."""
    if isinstance(value, dict):
        return {key: _without_row_ids(item) for key, item in value.items() if key not in VARYING}
    if isinstance(value, list):
        return [_without_row_ids(item) for item in value]
    return value


def _raw_refs(value: Any) -> list[tuple[Any, ...]]:
    """Every raw-line reference of the outcome, in order."""
    found: list[tuple[Any, ...]] = []
    if isinstance(value, dict):
        raw = value.get("raw")
        if isinstance(raw, dict) and {"source", "day", "line", "offset"} <= raw.keys():
            found.append((raw["source"], raw["day"], raw["line"], raw["offset"]))
        for item in value.values():
            found += _raw_refs(item)
    elif isinstance(value, list):
        for item in value:
            found += _raw_refs(item)
    return found


def day_rows(engine: Engine) -> dict[str, int]:
    """Rows of the site's day in the tables the lookup reads."""
    start, end = day_window(DAY)
    with engine.connect() as connection:
        return {
            table: connection.execute(
                text(f"SELECT count(*) FROM {table} WHERE time >= :start AND time < :end"),  # noqa: S608 - fixed names
                {"start": start, "end": end},
            ).scalar_one()
            for table in ("messages", "fg_traffic", "mt_dhcp", "mt_arp")
        }


def test_g7_a_lookup_over_reloaded_days_equals_the_lookup_before_the_drop(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    api_config: Config,
    layout: Layout,
    clock: Clock,
    sealed: Site,
) -> None:
    """The G7 equality test: the same answer and the same raw-line references from the rows
    the real ingest loaded from the sealed vault, before the drop and after the reload."""
    with app_session_factory() as session:
        set_setting(session, "nat.egresses", WAN_EXIT, updated_by="admin")
        session.commit()
    signed_in(client, login_as, app_session_factory)
    # The rows as ArciIngest loads them from the vault (the site wrote its own), the leases built
    # from them, as on a site.
    first = client.post(API, json=reload_body())
    assert first.status_code == 202, first.text
    run_ingest(api_config, db_engine)
    with app_session_factory() as session:
        rebuild_day(session, DAY, horizon=tr(DAY + timedelta(days=1), 9))
        session.commit()
    queries: list[dict[str, Any]] = [
        {"public_ip": WAN, "public_port": 51001, "protocol": "tcp"},
        {"internal_ip": PC},
        {"mac": PC_MAC},
    ]
    clock.now = SOON
    before = []
    for query in queries:
        response = client.post(LOOKUP, json=lookup_body(**query))
        assert response.status_code == 201, response.text
        before.append(response.json())
    assert (before[0]["mode"], before[0]["result_count"]) == ("exact", 1)
    assert before[1]["mode"] == "internal"
    assert before[1]["outcome"]["attribution"] is not None

    loaded = day_rows(db_engine)
    assert loaded["messages"] > 0
    assert loaded["fg_traffic"] > 0
    clock.now = LATER
    drop(db_engine)
    assert set(day_rows(db_engine).values()) == {0}
    for query in queries:
        response = client.post(LOOKUP, json=lookup_body(**query))
        assert response.status_code == 409, response.text
    required = response.json()["error"]["details"]
    second = client.post(
        API,
        json=reload_body(
            date.fromisoformat(required["first_day"]),
            date.fromisoformat(required["last_day"]),
            reason="lookup",
        ),
    )
    assert second.status_code == 202, second.text
    run_ingest(api_config, db_engine)
    reloaded = client.get(f"{API}/{second.json()['id']}").json()
    assert reloaded["status"] == "ready", reloaded
    assert reloaded["bytes_done"] == reloaded["bytes_total"] == sealed_bytes(layout, DAY)
    assert day_rows(db_engine) == loaded

    for query, earlier in zip(queries, before, strict=True):
        response = client.post(LOOKUP, json=lookup_body(**query))
        assert response.status_code == 201, response.text
        now = response.json()
        assert (now["mode"], now["result_count"]) == (earlier["mode"], earlier["result_count"])
        assert _without_row_ids(now["outcome"]) == _without_row_ids(earlier["outcome"])
        refs = _raw_refs(now["outcome"])
        assert refs == _raw_refs(earlier["outcome"])
        assert refs, query


def test_a_file_unreadable_mid_load_keeps_the_lookup_at_409(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    api_config: Config,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    sealed: Site,
) -> None:
    """W5-G answer 9, end to end: a sealed file that becomes unreadable while a reload loads it
    fails the reload (``archive_file_unreadable``); the day never counts as loaded (the file's
    offset stays short of ``bytes_uncompressed``) and the lookup answers 409 for it — although
    another user's ``ready`` reload of the day holds it, never a partial answer, and a request of
    the held day alone loads the file again (and fails again) instead of taking it as loaded.
    Once the file is whole again, a new reload brings the same answer back."""
    with app_session_factory() as session:
        set_setting(session, "nat.egresses", WAN_EXIT, updated_by="admin")
        session.commit()
    signed_in(client, login_as, app_session_factory)
    # An administrator's reload holds the day (the rows as ArciIngest loads them from the vault,
    # the leases built from them); the window job dropped what lies before it.
    assert client.post(API, json=reload_body()).status_code == 202
    run_ingest(api_config, db_engine)
    with app_session_factory() as session:
        rebuild_day(session, DAY, horizon=tr(DAY + timedelta(days=1), 9))
        session.commit()
    with db_engine.begin() as connection:
        drop_before(connection, DAY)
    signed_in(client, login_as, app_session_factory, "auditor")
    held = client.post(LOOKUP, json=lookup_body(internal_ip=PC))
    assert held.status_code == 201, held.text

    # Another user's reload from the day before (the sealer skipped it): the day's files are
    # loaded again, and the firewall's file becomes unreadable on the way.
    skip_day(layout, DAY - timedelta(days=1))
    vault_file, size = sealed_file(layout, DAY, "fortigate")
    good = vault_file.read_bytes()
    unreadable_mid_load(monkeypatch, vault_file)
    failing = client.post(API, json=reload_body(DAY - timedelta(days=1), DAY, reason="lookup"))
    assert failing.status_code == 202, failing.text
    run_ingest(api_config, db_engine, batch_rows=2, archive_error_seconds=0.0)
    failed = client.get(f"{API}/{failing.json()['id']}").json()
    assert (failed["status"], failed["error_code"]) == ("failed", "archive_file_unreadable")
    with db_engine.connect() as connection:
        offset, finished_at = connection.execute(
            text(
                "SELECT byte_offset, finished_at FROM ingest_offsets "
                "WHERE source = 'fortigate' AND day = :day"
            ),
            {"day": DAY},
        ).one()
    assert (0 < offset < size, finished_at) == (True, None)

    response = client.post(LOOKUP, json=lookup_body(internal_ip=PC))
    assert response.status_code == 409, response.text
    error = response.json()["error"]
    assert error["code"] == "archive_reload_required"
    assert (error["details"]["first_day"], error["details"]["last_day"]) == (
        DAY.isoformat(),
        DAY.isoformat(),
    )
    # Asked for the day alone, which the administrator's reload holds: the file left short does
    # not count as loaded, so it is loaded again, and fails again while it stays unreadable.
    still = client.post(API, json=reload_body(reason="lookup"))
    assert still.status_code == 202, still.text
    run_ingest(api_config, db_engine, archive_error_seconds=0.0)
    retried = client.get(f"{API}/{still.json()['id']}").json()
    assert (retried["status"], retried["error_code"]) == ("failed", "archive_file_unreadable")
    assert client.post(LOOKUP, json=lookup_body(internal_ip=PC)).status_code == 409

    monkeypatch.setattr(ingest_service, "VaultReader", VaultReader)
    vault_file.write_bytes(good)  # restored from the replica
    again = client.post(API, json=reload_body(DAY - timedelta(days=1), DAY, reason="lookup"))
    assert again.status_code == 202, again.text
    assert again.json()["id"] != failing.json()["id"]
    run_ingest(api_config, db_engine)
    assert client.get(f"{API}/{again.json()['id']}").json()["status"] == "ready"
    response = client.post(LOOKUP, json=lookup_body(internal_ip=PC))
    assert response.status_code == 201, response.text
    assert _without_row_ids(response.json()["outcome"]) == _without_row_ids(held.json()["outcome"])


# -- the search (G8) ------------------------------------------------------------------------------


def search(client: TestClient, start: date, end: date, **body: Any) -> Any:
    return client.post(
        SEARCH,
        json={
            "q": "",
            "from": day_window(start)[0].isoformat(),
            "to": day_window(end)[0].isoformat(),
            **body,
        },
    )


def test_the_search_answers_archived_or_409(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    api_config: Config,
    layout: Layout,
    clock: Clock,
    sealed: Site,
) -> None:
    signed_in(client, login_as, app_session_factory)
    # The day's ``messages`` rows as ArciIngest loads them from the vault (the site wrote the
    # typed rows only).
    assert client.post(API, json=reload_body()).status_code == 202
    run_ingest(api_config, db_engine)
    with app_session_factory() as session:
        set_setting(session, "db.retention_days", 7, updated_by="admin")
        session.commit()
    clock.now = tr(DAY + timedelta(days=20), 12)  # the window starts on DAY + 13

    # Before the drop: the rows are there, nothing archived (also on the next page).
    found = search(client, DAY - timedelta(days=1), DAY + timedelta(days=2), limit=1)
    assert found.status_code == 200, found.text
    page = found.json()
    assert page["archived"] is None
    assert len(page["items"]) == 1
    assert page["next_cursor"] is not None
    following = client.post(
        f"{SEARCH}/page", json={"search_id": page["search_id"], "cursor": page["next_cursor"]}
    )
    assert following.status_code == 200, following.text
    assert following.json()["archived"] is None

    drop(db_engine)
    # The whole range gone: 409 with what a reload would bring back.
    refused = search(client, DAY - timedelta(days=1), DAY + timedelta(days=2))
    assert refused.status_code == 409, refused.text
    error = refused.json()["error"]
    total = sealed_bytes(layout, DAY)
    assert error["code"] == "archive_reload_required"
    assert error["details"] == {
        "first_day": DAY.isoformat(),
        "last_day": DAY.isoformat(),
        "bytes_total": total,
        "estimated_bytes": estimate(total),
        "keep_days": 7,
    }
    assert audits(app_session_factory, "search.run")[-1].details["search_id"] == page["search_id"]

    # A range reaching into the window: its rows, and the archived part.
    partly = search(client, DAY, DAY + timedelta(days=16))
    assert partly.status_code == 200, partly.text
    assert partly.json()["archived"] == {"first_day": DAY.isoformat(), "last_day": DAY.isoformat()}
    assert partly.json()["items"] == []


def test_the_409_names_the_keep_days_of_the_setting(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    clock: Clock,
    sealed: Site,
) -> None:
    # C23: the current db.reload_keep_days, not the default, for users who cannot read /settings.
    signed_in(client, login_as, app_session_factory, "auditor")
    with app_session_factory() as session:
        set_setting(session, "db.reload_keep_days", 12, updated_by="admin")
        session.commit()
    drop(db_engine)
    looked_up = client.post(LOOKUP, json=lookup_body(internal_ip=PC))
    assert looked_up.status_code == 409, looked_up.text
    searched = search(client, DAY - timedelta(days=1), DAY + timedelta(days=2))
    assert searched.status_code == 409, searched.text
    for response in (looked_up, searched):
        error = response.json()["error"]
        assert error["code"] == "archive_reload_required"
        assert error["details"]["keep_days"] == 12


@pytest.mark.parametrize("stored", ["0", "-3", "true", '"12"', "null"])
def test_keep_days_falls_back_to_the_shipped_default(
    app_session_factory: sessionmaker[Session], stored: str
) -> None:
    # A stored value ArciIngest would not use for expires_at: the dialog names the default too.
    with app_session_factory() as session:
        changed = session.execute(
            text("UPDATE settings SET value = CAST(:value AS jsonb) WHERE key = :key"),
            {"value": stored, "key": "db.reload_keep_days"},
        )
        assert changed.rowcount == 1  # type: ignore[attr-defined]
        assert archive_service.reload_keep_days(session) == 7
        session.rollback()


def test_the_texts_are_turkish_first_with_english_beside() -> None:
    for code, (text_tr, text_en) in MESSAGES.items():
        assert code.startswith("archive_")
        assert text_tr != text_en
        assert any(char in text_tr for char in "çğıöşüÇĞİÖŞÜ")


def test_g8_a_search_over_a_reloaded_day_equals_the_search_before_the_drop(
    client: TestClient,
    login_as: Login,
    app_session_factory: sessionmaker[Session],
    db_engine: Engine,
    api_config: Config,
    layout: Layout,
    sealed: Site,
) -> None:
    """A session logged across midnight (device time 23:59:59.999, received after midnight) is
    in the next raw day's file but in the day: the reload of the day loads the raw days two days
    either side again, so the search finds the same rows, raw references included."""
    with app_session_factory() as session:
        Evidence(session, layout).traffic(
            day_window(DAY + timedelta(days=1))[0] - timedelta(milliseconds=1), PC, 51009
        )
        session.commit()
    seal_day(api_config, layout, DAY + timedelta(days=1))
    signed_in(client, login_as, app_session_factory)
    assert client.post(API, json=reload_body()).status_code == 202  # the rows ingest loads
    run_ingest(api_config, db_engine)

    def items() -> list[dict[str, Any]]:
        found = search(client, DAY, DAY + timedelta(days=1), limit=500)
        assert found.status_code == 200, found.text
        assert found.json()["archived"] is None
        return [{k: v for k, v in item.items() if k != "key"} for item in found.json()["items"]]

    before = items()
    late = [item for item in before if item["raw"]["day"] == (DAY + timedelta(days=1)).isoformat()]
    assert len(late) == 1  # the session across midnight
    drop(db_engine)
    refused = search(client, DAY, DAY + timedelta(days=1))
    assert refused.status_code == 409, refused.text
    required = refused.json()["error"]["details"]
    assert (required["first_day"], required["last_day"]) == (DAY.isoformat(), DAY.isoformat())
    assert required["bytes_total"] == sealed_bytes(layout, DAY) + sealed_bytes(
        layout, DAY + timedelta(days=1)
    )
    again = client.post(API, json=reload_body())
    assert again.status_code == 202, again.text
    run_ingest(api_config, db_engine)
    assert client.get(f"{API}/{again.json()['id']}").json()["status"] == "ready"
    assert items() == before


def test_every_failure_code_has_its_two_texts() -> None:
    """The ``error_code`` values ArciIngest sets are the model's failure codes, and each has a
    Turkish and an English text for the web (W5-J), as every code of the area does."""
    codes = set(get_args(ArchiveReloadFailureCode))
    assert codes == {ingest_service.ARCHIVE_NOT_IN_VAULT, ingest_service.ARCHIVE_UNREADABLE}
    assert set(archive_service.FAILURE_MESSAGES) == codes
    for turkish, english in archive_service.FAILURE_MESSAGES.values():
        assert turkish
        assert english
        assert turkish != english
