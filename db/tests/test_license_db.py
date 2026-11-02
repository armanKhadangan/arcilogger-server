"""The database layer of the licensing (licensing contract 4, 5.4; tests B-T1 … B-T4, B-T15 parts).

Migration ``0014_license`` up and down, the append-only triggers, the installation row and its
backdating, the seed's guarded licence step, ``load_status`` on real inputs (it never raises for
a missing vault, roots or fingerprint), the usage counts of 3.1, install / refuse / remove, the
state and limit events of ``license.check``, the ``GET /license`` document and the status file.
Real evaluation everywhere (``license_live``), with the deterministic test key and server id.
"""

from __future__ import annotations

import json
import logging
import sys
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import Engine, func, inspect, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

import arcilogger_db
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.license import LicenseError, fingerprint
from arcilogger_core.license.testing import (
    fixed_identity,
    make_license,
    tamper,
    trusted_test_keys,
)
from arcilogger_core.license.trusted_time import clear_cache
from arcilogger_core.timeutil import day_window, utc_now
from arcilogger_db import licensing
from arcilogger_db.audit import append_entry, verify_audit_chain
from arcilogger_db.engine import engine_from_url
from arcilogger_db.migrate import alembic_config, head_revisions, upgrade
from arcilogger_db.models import (
    LICENSE_TABLES,
    AuditLog,
    LicenseEvent,
    LicenseFile,
    LicenseInstallation,
    Source,
    User,
)
from arcilogger_db.testing import TestServer

pytestmark = pytest.mark.license_live

PAID = ("core_5651", "usage_analytics", "usage_reports", "bulk_import")


@pytest.fixture(autouse=True)
def _fresh_caches() -> Iterator[None]:
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def server_id() -> Iterator[str]:
    with fixed_identity() as value, trusted_test_keys():
        yield value


@pytest.fixture
def config(tmp_path: Path) -> Config:
    return Config.model_validate(
        {
            "site_name": "Örnek A.Ş.",
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "192.0.2.1"},
                {"name": "aps", "type": "generic", "address": "192.0.2.128/25"},
            ],
        }
    )


@pytest.fixture
def layout(config: Config) -> Layout:
    return Layout(config.data_dir)


@pytest.fixture
def empty_engine(pg_server: TestServer) -> Iterator[Engine]:
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
    try:
        yield engine
    finally:
        engine.dispose()
        pg_server.drop_database(name)


class Recorder:
    """An ``AuditWriter`` that remembers the entries."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str | None, dict[str, Any]]] = []

    def __call__(self, action: str, target: str | None, details: Mapping[str, Any]) -> None:
        self.entries.append((action, target, dict(details)))

    def actions(self) -> list[str]:
        return [action for action, _, _ in self.entries]


def events(session: Session, kind: str | None = None) -> list[LicenseEvent]:
    query = select(LicenseEvent).order_by(LicenseEvent.id)
    if kind is not None:
        query = query.where(LicenseEvent.kind == kind)
    return list(session.scalars(query))


def audit_rows(session: Session, action: str) -> list[AuditLog]:
    return list(
        session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
    )


# -- B-T1 migration -------------------------------------------------------------------------------


def _triggers(engine: Engine) -> set[str]:
    with engine.connect() as connection:
        return set(
            connection.execute(
                text("SELECT tgname FROM pg_trigger WHERE tgname LIKE 'license_%'")
            ).scalars()
        )


def test_migration_0014_up_and_down(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision="0013_site_data")
    assert not set(LICENSE_TABLES) & set(inspect(empty_engine).get_table_names())
    result = upgrade(empty_engine, revision="0014_license")
    assert result.after == ("0014_license",)
    assert set(LICENSE_TABLES) <= set(inspect(empty_engine).get_table_names())
    expected = {
        f"{table}_{suffix}"
        for table in LICENSE_TABLES
        for suffix in ("no_update_delete", "no_truncate")
    }
    assert _triggers(empty_engine) == expected
    columns = {c["name"] for c in inspect(empty_engine).get_columns("license_files")}
    assert {"sha256", "payload_sha256", "content", "license_id", "key_id", "licensee"} <= columns
    with empty_engine.begin() as connection:
        command.downgrade(alembic_config(connection), "0013_site_data")
    assert not set(LICENSE_TABLES) & set(inspect(empty_engine).get_table_names())
    assert _triggers(empty_engine) == set()
    with empty_engine.connect() as connection:
        assert (
            connection.execute(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'arci_license_immutable'")
            ).scalar_one()
            == 0
        )
    assert upgrade(empty_engine, revision="0014_license").after == ("0014_license",)  # and up


# -- B-T2 append-only -----------------------------------------------------------------------------


def _fill(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO license_installation (id, installation_id, trial_started_at) "
                "VALUES (1, :id, now())"
            ),
            {"id": str(uuid4())},
        )
        file_id = connection.execute(
            text(
                "INSERT INTO license_files (sha256, payload_sha256, content, license_id, key_id, "
                "licensee, issued_at, first_installed_at, first_installed_by) VALUES "
                "(:h, :h, '\\x7b7d', 'ARCI-L-20261001-TEST0001', 'test-key-1', 'X', now(), "
                "now(), 'admin') RETURNING id"
            ),
            {"h": "a" * 64},
        ).scalar_one()
        connection.execute(
            text(
                "INSERT INTO license_events (at, kind, source, actor, license_file_id) "
                "VALUES (now(), 'install', 'web', 'admin', :f)"
            ),
            {"f": file_id},
        )


@pytest.mark.parametrize("table", LICENSE_TABLES)
@pytest.mark.parametrize(
    "statement", ["UPDATE {t} SET id = id", "DELETE FROM {t}", "TRUNCATE {t} CASCADE"]
)
def test_licence_tables_are_append_only(db_engine: Engine, table: str, statement: str) -> None:
    _fill(db_engine)
    with (
        pytest.raises(DBAPIError, match="append-only"),
        db_engine.begin() as connection,
    ):
        connection.execute(text(statement.format(t=table)))
    with db_engine.connect() as connection:
        count = text(f"SELECT count(*) FROM {table}")  # noqa: S608 - a fixed table name
        assert connection.execute(count).scalar_one() == 1


def test_one_installation_row_only(db_engine: Engine) -> None:
    _fill(db_engine)
    for row_id in (1, 2):
        with pytest.raises(IntegrityError), db_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO license_installation (id, installation_id, trial_started_at) "
                    "VALUES (:n, :id, now())"
                ),
                {"n": row_id, "id": str(uuid4())},
            )


def test_install_and_remove_events_need_a_file(db_engine: Engine) -> None:
    for kind in ("install", "remove"):
        with pytest.raises(IntegrityError, match="ck_license_events_file"), db_engine.begin() as c:
            c.execute(
                text(
                    "INSERT INTO license_events (at, kind, source, actor) "
                    "VALUES (now(), :k, 'cli', 'os:x')"
                ),
                {"k": kind},
            )


# -- B-T3 the seed --------------------------------------------------------------------------------


def _installation(engine: Engine) -> list[Any]:
    with engine.connect() as connection:
        return list(connection.execute(text("SELECT * FROM license_installation")).mappings())


def _trial_starts(engine: Engine) -> list[dict[str, Any]]:
    with Session(engine) as session:
        return [row.details for row in audit_rows(session, "license.trial_start")]


def test_the_seed_creates_the_installation_once(empty_engine: Engine) -> None:
    before = utc_now()
    first = upgrade(empty_engine)
    assert first.seed.license_installation_created is True
    (row,) = _installation(empty_engine)
    assert row["id"] == 1
    assert before <= row["trial_started_at"] <= utc_now()
    (details,) = _trial_starts(empty_engine)
    assert details == {
        "installation_id": str(row["installation_id"]),
        "trial_started_at": details["trial_started_at"],
        "backdated": False,
        "recreated": False,
    }
    assert datetime.fromisoformat(details["trial_started_at"]) == row["trial_started_at"]
    second = upgrade(empty_engine)
    assert second.seed.license_installation_created is False
    assert not second.seed.changed
    assert _installation(empty_engine) == [row]
    assert len(_trial_starts(empty_engine)) == 1
    with Session(empty_engine) as session:
        assert verify_audit_chain(session).ok


def test_the_seed_backdates_to_the_oldest_audit_entry(empty_engine: Engine) -> None:
    upgrade(empty_engine, revision="0013_site_data")  # the seed's licence step fails: no table
    old = utc_now() - timedelta(days=30)
    with Session(empty_engine) as session:
        append_entry(session, actor_id=None, actor="system", action="seal.run", clock=lambda: old)
        session.commit()
    assert "license_installation" not in inspect(empty_engine).get_table_names()
    upgrade(empty_engine)
    (row,) = _installation(empty_engine)
    assert row["trial_started_at"] == old
    (details,) = _trial_starts(empty_engine)
    assert (details["backdated"], details["recreated"]) == (True, False)


def test_a_broken_licence_package_never_stops_the_upgrade(
    empty_engine: Engine, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setitem(sys.modules, "arcilogger_db.licensing", None)
    monkeypatch.delattr(arcilogger_db, "licensing", raising=False)
    with caplog.at_level(logging.WARNING, logger="arcilogger.db"):
        result = upgrade(empty_engine)
    assert result.after == head_revisions()
    assert result.seed.settings_inserted  # the rest of the seed ran
    assert result.seed.license_installation_created is False
    assert "license: installation row not created" in caplog.text
    assert _installation(empty_engine) == []


# -- installation row -----------------------------------------------------------------------------


def _delete_installation(session: Session) -> None:
    session.execute(text("ALTER TABLE license_installation DISABLE TRIGGER USER"))
    session.execute(text("DELETE FROM license_installation"))
    session.execute(text("ALTER TABLE license_installation ENABLE TRIGGER USER"))


def test_a_deleted_installation_row_is_recreated_backdated(db_session: Session) -> None:
    start = utc_now() - timedelta(days=20)
    first = licensing.ensure_installation(db_session, now=start)
    db_session.commit()
    _delete_installation(db_session)
    db_session.commit()
    recorder = Recorder()
    again = licensing.ensure_installation(db_session, now=utc_now(), audit=recorder)
    assert again.trial_started_at == start  # the trial-start audit entry keeps it
    assert again.installation_id != first.installation_id
    ((action, target, details),) = recorder.entries
    assert (action, target) == ("license.trial_start", "license")
    assert (details["backdated"], details["recreated"]) == (True, True)


def test_db_lower_bounds(db_session: Session) -> None:
    assert licensing.db_lower_bounds(db_session) == {}
    old = datetime(2026, 9, 1, tzinfo=UTC)
    append_entry(db_session, actor_id=None, actor="system", action="x", clock=lambda: old)
    licensing.ensure_installation(db_session, now=utc_now())
    db_session.add(
        LicenseEvent(at=old + timedelta(days=1), kind="state", source="worker", actor="system")
    )
    db_session.flush()
    bounds = licensing.db_lower_bounds(db_session)
    assert bounds == {
        "audit": old,
        "trial_start_audit": old,
        "license_events": old + timedelta(days=1),
    }


# -- B-T4 load_status -----------------------------------------------------------------------------


def _vault_day(layout: Layout, day: date) -> None:
    manifest = layout.manifest(day)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    manifest.write_text("{}", encoding="utf-8")


def test_load_status_on_a_fresh_installation_is_the_trial(
    db_session: Session, config: Config, layout: Layout, server_id: str
) -> None:
    status = licensing.load_status(db_session, config=config, layout=layout)
    assert status.state == "trial"
    assert status.trial_days_left == 14
    assert status.server_id == server_id
    assert all(status.module_enabled(key) for key in PAID)
    assert status.limits["users"].max is None
    assert db_session.scalar(select(func.count()).select_from(LicenseInstallation)) == 1


def test_load_status_with_an_old_vault_is_over_and_never_raises(
    db_session: Session, config: Config, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = utc_now().date()
    for offset in (40, 39):  # junk manifests: no token, no roots — only the day names count
        _vault_day(layout, today - timedelta(days=offset))

    def broken() -> Any:
        raise OSError("no MachineGuid")

    monkeypatch.setattr(fingerprint, "IDENTITY_PROVIDER", broken)
    fingerprint.clear_cache()
    try:
        status = licensing.load_status(db_session, config=config, layout=layout)
    finally:
        fingerprint.clear_cache()
    assert status.state == "trial_expired"
    assert status.problem is None
    assert status.trial_started_at == day_window(today - timedelta(days=40))[1]
    assert status.server_id is None
    assert status.server_id_error is not None
    assert "MachineGuid" in status.server_id_error
    assert status.enabled == frozenset({"core_5651"})
    assert status.limits["users"].max == 3


def test_load_status_without_a_data_folder(db_session: Session, tmp_path: Path) -> None:
    layout = Layout(tmp_path / "missing")
    with fixed_identity():
        status = licensing.load_status(db_session, config=None, layout=layout)
    assert status.state == "trial"
    assert status.trusted.tsa is None
    assert status.trusted.vault_day_end is None


def test_usage_counts_follow_section_3_1(db_session: Session, config: Config) -> None:
    now = utc_now()
    rows = [
        # configured, never seen yet: counts
        Source(name="fortigate", type="fortigate", unknown=False),
        # a sender of the configured network, seen 5 days ago: counts
        Source(
            name="aps-192.0.2.130", type="generic", unknown=False, last_seen=now - timedelta(days=5)
        ),
        # an unconfigured sender with data: counts, unconfigured
        Source(
            name="unknown-198.51.100.7",
            type="generic",
            unknown=True,
            last_seen=now - timedelta(days=1),
        ),
        # disabled but sending: counts (enabled is never read by collection)
        Source(
            name="old-switch", type="generic", enabled=False, last_seen=now - timedelta(days=29)
        ),
        # removed device / old sender IP: silent for more than 30 days, no longer counts
        Source(
            name="unknown-198.51.100.8",
            type="generic",
            unknown=True,
            last_seen=now - timedelta(days=31),
        ),
        Source(name="retired", type="generic", last_seen=now - timedelta(days=40)),
    ]
    db_session.add_all(rows)
    db_session.add_all(
        [
            User(username="a1", display_name="A", role="admin", password_hash="!"),
            User(username="v1", display_name="V", role="viewer", password_hash="!"),
            User(username="v2", display_name="V", role="viewer", password_hash="!", active=False),
        ]
    )
    db_session.flush()
    assert licensing.usage_counts(db_session, config=config, now=now) == (2, 4, 1)
    # Without a configuration only the recent senders count.
    assert licensing.usage_counts(db_session, config=None, now=now) == (2, 3, 1)


# -- install, refuse, remove ----------------------------------------------------------------------


def test_install_reinstall_and_remove(
    db_session: Session, config: Config, layout: Layout, server_id: str
) -> None:
    recorder = Recorder()
    data = make_license(server_ids=[server_id], modules=("core_5651", "usage_analytics"), users=25)
    status = licensing.install_license(
        db_session, data, config=config, layout=layout, actor="admin", source="web", audit=recorder
    )
    assert status.state == "licensed"
    assert status.module_enabled("usage_analytics")
    assert not status.module_enabled("usage_reports")
    (event,) = events(db_session, "install")
    assert event.details["unchanged"] is False
    assert event.details["replaced_license_id"] is None
    assert event.details["licensee"] == "Örnek A.Ş."
    assert event.details["server_ids"] == [server_id]
    (action, target, details) = recorder.entries[-1]
    assert (action, target) == ("license.install", "license:ARCI-L-20261001-TEST0001")
    assert set(details) == {
        "license_id",
        "sha256",
        "payload_sha256",
        "key_id",
        "modules",
        "limits",
        "maintenance_until",
        "valid_until",
        "unchanged",
        "replaced_license_id",
    }
    assert details["limits"] == {"log_sources": None, "users": 25}
    # The same licence reformatted: a new file row, an unchanged install.
    reformatted = json.dumps(json.loads(data), indent=4, sort_keys=True).encode()
    licensing.install_license(
        db_session,
        reformatted,
        config=config,
        layout=layout,
        actor="admin",
        source="cli",
        audit=recorder,
    )
    # The same bytes again: no new file row.
    licensing.install_license(
        db_session, data, config=config, layout=layout, actor="admin", source="web", audit=recorder
    )
    installs = events(db_session, "install")
    assert [e.details["unchanged"] for e in installs] == [False, True, True]
    assert installs[1].details["replaced_license_id"] == "ARCI-L-20261001-TEST0001"
    assert db_session.scalar(select(func.count()).select_from(LicenseFile)) == 2
    assert licensing.active_license(db_session) is not None
    removed = licensing.remove_license(db_session, actor="admin", source="web", audit=recorder)
    assert removed.sha256 == installs[-1].details["sha256"]
    assert licensing.active_license(db_session) is None
    assert recorder.entries[-1][0:2] == ("license.remove", "license:ARCI-L-20261001-TEST0001")
    with pytest.raises(LicenseError) as missing:
        licensing.remove_license(db_session, actor="admin", source="web", audit=recorder)
    assert missing.value.code == "license_not_installed"
    after = licensing.load_status(db_session, config=config, layout=layout)
    assert after.state == "trial"


def test_refusals_are_recorded(
    db_session: Session, config: Config, layout: Layout, server_id: str
) -> None:
    recorder = Recorder()
    data = make_license(server_ids=["AAAA-BBBB-CCCC-DDDD"])
    with pytest.raises(LicenseError) as refused:
        licensing.install_license(
            db_session,
            data,
            config=config,
            layout=layout,
            actor="admin",
            source="web",
            audit=recorder,
        )
    assert refused.value.code == "license_other_server"
    assert events(db_session) == []  # nothing written before the refusal
    licensing.record_refusal(
        db_session, refused.value, data, actor="admin", source="web", audit=recorder
    )
    licensing.record_refusal(
        db_session,
        LicenseError("license_too_large"),
        None,
        actor="admin",
        source="web",
        audit=recorder,
        size=200_000,
    )
    first, second = events(db_session, "refuse")
    assert first.details["code"] == "license_other_server"
    assert first.details["license_id"] == "ARCI-L-20261001-TEST0001"
    assert first.details["key_id"] == "test-key-1"
    assert first.details["size"] == len(data)
    assert second.details == {
        "code": "license_too_large",
        "sha256": None,
        "size": 200_000,
        "license_id": None,
        "key_id": None,
    }
    assert recorder.actions() == ["license.refuse", "license.refuse"]
    assert recorder.entries[0][1] == "license"
    assert recorder.entries[0][2] == first.details


def test_verify_license_file_writes_nothing(
    db_session: Session, config: Config, layout: Layout, server_id: str
) -> None:
    data = make_license(server_ids=[server_id])
    document = licensing.verify_license_file(db_session, data, config=config, layout=layout)
    assert document.payload.license_id == "ARCI-L-20261001-TEST0001"
    assert events(db_session) == []
    with pytest.raises(LicenseError) as tampered:
        licensing.verify_license_file(
            db_session, tamper(data, "users", 99), config=config, layout=layout
        )
    assert tampered.value.code == "license_signature_invalid"


# -- B-T15 parts: state and limit events ----------------------------------------------------------


def test_check_and_record_state_and_limits(
    db_session: Session, config: Config, layout: Layout, server_id: str
) -> None:
    recorder = Recorder()

    def check() -> list[str]:
        status = licensing.load_status(db_session, config=config, layout=layout)
        return licensing.check_and_record(db_session, status, audit=recorder)

    assert check() == ["state"]  # the first run records the state (from null)
    (state,) = events(db_session, "state")
    assert state.details == {
        "from": None,
        "to": "trial",
        "enabled_from": None,
        "enabled_to": list(PAID),
        "problem": None,
    }
    assert check() == []  # unchanged: nothing
    licensing.install_license(
        db_session,
        make_license(server_ids=[server_id], log_sources=1),
        config=config,
        layout=layout,
        actor="admin",
        source="web",
        audit=recorder,
    )
    assert check() == ["state"]  # trial -> licensed; no limit event with a state change
    now = utc_now()
    db_session.add_all(
        [
            Source(name="fortigate", type="fortigate"),
            Source(name="unknown-198.51.100.7", type="generic", unknown=True, last_seen=now),
        ]
    )
    db_session.flush()
    assert check() == ["limit"]  # 2 > 1: exceeded, once
    assert check() == []
    (limit,) = events(db_session, "limit")
    assert limit.details == {"limit": "log_sources", "exceeded": True, "max": 1, "used": 2}
    db_session.execute(text("DELETE FROM sources WHERE name = 'unknown-198.51.100.7'"))
    assert check() == ["limit"]  # back under: ok, once
    assert check() == []
    assert [e.details["exceeded"] for e in events(db_session, "limit")] == [True, False]
    assert recorder.actions()[-2:] == ["license.limit_exceeded", "license.limit_ok"]
    assert recorder.entries[-1][2] == {"limit": "log_sources", "max": 1, "used": 1}


# -- the document and the status file -------------------------------------------------------------


def test_status_json_and_status_file(
    db_session: Session, config: Config, layout: Layout, server_id: str
) -> None:
    status = licensing.install_license(
        db_session,
        make_license(server_ids=[server_id], modules=("core_5651", "usage_analytics", "future_x")),
        config=config,
        layout=layout,
        actor="admin",
        source="web",
        audit=Recorder(),
    )
    plain = licensing.status_json(status, details=False)
    assert plain["details"] is None
    assert plain["status"] == "licensed"
    assert plain["licensee"] == "Örnek A.Ş."
    assert plain["trial"] is None
    assert [m["key"] for m in plain["modules"]] == list(PAID)
    licensed = {m["key"]: (m["enabled"], m["licensed"]) for m in plain["modules"]}
    assert licensed == {
        "core_5651": (True, True),
        "usage_analytics": (True, True),
        "usage_reports": (False, False),
        "bulk_import": (False, False),
    }
    assert plain["maintenance"]["until"] == "2099-12-31"
    assert plain["maintenance"]["covers_version"] is True
    assert plain["licensor"] == {"name": "Arman Khadangan", "email": "arman@khadangan.com"}
    full = licensing.status_json(status, details=True, session=db_session, config=config)
    details = full["details"]
    assert details["license_id"] == "ARCI-L-20261001-TEST0001"
    assert details["server_ids"] == [server_id]
    assert details["installed_by"] == "admin"
    assert details["organization"] == "Örnek A.Ş."
    assert details["request_code"].startswith("ARCIREQ1-")
    assert details["time"]["source"] in (
        "system",
        "tsa",
        "vault_day",
        "release_date",
        "license_issued",
    )
    assert details["history"][0]["kind"] == "install"
    licensing.write_status_file(
        layout, status, clock=lambda: datetime(2026, 10, 20, 10, 5, tzinfo=UTC)
    )
    written = json.loads((layout.state / "license-status.json").read_text(encoding="utf-8"))
    assert written == {
        "schema": "arcilogger.license-status/1",
        "written_at": "2026-10-20T10:05:00Z",
        "status": "licensed",
        "license_id": "ARCI-L-20261001-TEST0001",
        "licensee": "Örnek A.Ş.",
        "server_id": server_id,
        "maintenance_until": "2099-12-31",
        "valid_until": None,
        "modules": ["core_5651", "usage_analytics"],
        "version": "0.1.0",
        "release_date": status.release_date.isoformat(),
    }


# -- license_guard ----------------------------------------------------------------------------


def test_safe_status_on_a_broken_subsystem(
    db_session: Session,
    config: Config,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from arcilogger_db import license_guard

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise OSError("ctypes call failed")

    monkeypatch.setattr(licensing, "load_status", broken)
    monkeypatch.setattr(license_guard, "_logged", set())
    with caplog.at_level(logging.ERROR, logger="arcilogger.license"):
        first = license_guard.safe_status(db_session, config=config, layout=layout)
        second = license_guard.safe_status(db_session, config=config, layout=layout)
    for guarded in (first, second):
        assert guarded.status is None
        assert guarded.error == "OSError"
        assert guarded.state == "unavailable"
        assert guarded.users_limit is None  # never refuses a user
        assert guarded.module_enabled("core_5651")
        assert not guarded.module_enabled("usage_analytics")
    errors = [r.getMessage() for r in caplog.records if "unavailable" in r.getMessage()]
    assert errors == ["licence subsystem unavailable: OSError: ctypes call failed"]  # once
    # The session is still usable: only the SAVEPOINT was rolled back.
    assert db_session.scalar(select(func.count()).select_from(User)) is not None


def test_safe_status_returns_the_status(
    db_session: Session, config: Config, layout: Layout, server_id: str
) -> None:
    from arcilogger_db.license_guard import safe_status

    guarded = safe_status(db_session, config=config, layout=layout)
    assert guarded.status is not None
    assert guarded.error is None
    assert guarded.state == "trial"
    assert guarded.users_limit == (None, 0)
    assert guarded.module_enabled("bulk_import")
