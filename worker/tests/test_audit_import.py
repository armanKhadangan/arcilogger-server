"""``audit.import``: scheduled sealing and retention runs reach the audit log exactly once."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import date, timedelta
from pathlib import Path
from typing import cast

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core import retention
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import record_change
from arcilogger_core.tsa.mock import MockFailure
from arcilogger_db.audit import GENESIS_HASH, entry_hash
from arcilogger_db.engine import session_factory
from arcilogger_db.models import AuditImport, AuditLog
from arcilogger_worker.evidence_jobs import (
    AuditEvent,
    ImportReport,
    alert_events,
    import_events,
    import_job_events,
    secret_events,
)
from arcilogger_worker.service import Worker, default_jobs
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

DAYS = [date(2024, 9, 10) + timedelta(days=n) for n in range(4)]


@pytest.fixture
def vault(layout: Layout) -> Iterator[SealedVault]:
    sealed = make_vault(layout.root, tsa_now=at_site(DAYS[0] - timedelta(days=30)))
    yield sealed
    sealed.unlock_all()


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


def entries(session: Session) -> list[AuditLog]:
    session.expire_all()
    return list(session.scalars(select(AuditLog).order_by(AuditLog.id)))


def chain_ok(rows: list[AuditLog]) -> bool:
    previous = GENESIS_HASH
    for row in rows:
        entry = {
            "v": 1,
            "id": row.id,
            "time": row.time.isoformat(timespec="microseconds"),
            "actor_id": row.actor_id,
            "actor": row.actor_username,
            "action": row.action,
            "target": row.target,
            "details": row.details,
            "ip": row.ip,
        }
        if row.prev_hash != previous or entry_hash(row.prev_hash, entry) != row.hash:
            return False
        previous = row.hash
    return True


def test_sealing_failures_and_retention_are_imported_once(
    vault: SealedVault, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    vault.seal_days(DAYS[:3])
    vault.write_day(DAYS[3])
    vault.fail_seal(DAYS[3])
    report = import_job_events(sessions, layout)
    assert report.imported == [
        "seal:2024-09-10:sealed",
        "seal:2024-09-11:sealed",
        "seal:2024-09-12:sealed",
        "seal:2024-09-13:attempt:1",
    ]
    rows = entries(db_session)
    assert [(r.action, r.target, r.actor_username, r.actor_id) for r in rows] == [
        ("seal.run", f"seal:{day.isoformat()}", "system", None) for day in DAYS
    ]
    sealed = rows[0].details
    state = json.loads(layout.seal_state(DAYS[0]).read_text(encoding="utf-8"))
    assert sealed["outcome"] == "sealed"
    assert sealed["source"] == "state/seal/2024-09-10.json"
    for key in ("sealed_at", "serial", "manifest_sha256", "token_sha256", "files", "lines"):
        assert sealed[key] == state[key]
    assert sealed["legal_grade"] is False
    failed = rows[3].details
    assert failed["outcome"] == "failed"
    assert failed["attempt"] == 1
    assert failed["error"] == "mock TSA unreachable (injected)"
    assert failed["next_attempt_at"]

    # Nothing new: nothing written. A second failed attempt is a new event.
    assert import_job_events(sessions, layout).imported == []
    vault.fail_seal(DAYS[3])
    assert import_job_events(sessions, layout).imported == ["seal:2024-09-13:attempt:2"]
    # The day is sealed at last.
    assert vault.seal(DAYS[3]).status == "sealed"
    assert import_job_events(sessions, layout).imported == ["seal:2024-09-13:sealed"]
    assert entries(db_session)[-1].details["attempts"] == 3

    # Retention deletes the two oldest days: one retention.run entry with the sealed record.
    result = vault.retention(at_site(date(2026, 9, 12), 1, 0))
    assert result.deleted == DAYS[:2], result.error
    report = import_job_events(sessions, layout)
    assert report.imported == [f"retention:{result.record}"]
    retention = entries(db_session)[-1]
    assert retention.action == "retention.run"
    assert retention.target == f"retention:{result.record}"
    record = layout.deletions / str(result.record)
    assert retention.details["record"] == f"vault/deletions/{result.record}"
    assert retention.details["record_sha256"] == hashlib.sha256(record.read_bytes()).hexdigest()
    token = record.with_name(record.name + ".zd")
    assert retention.details["token_sha256"] == hashlib.sha256(token.read_bytes()).hexdigest()
    assert retention.details["days"] == ["2024-09-10", "2024-09-11"]
    assert retention.details["day_count"] == 2
    assert retention.details["retention_days"] == 730
    # The deleted days' "sealed" entries were imported before; their state now says deleted.
    assert import_job_events(sessions, layout).imported == []

    rows = entries(db_session)
    assert len(rows) == 7
    assert chain_ok(rows)
    with sessions() as session:
        keys = {row.key: row.audit_id for row in session.scalars(select(AuditImport))}
    assert len(keys) == 7
    assert sorted(keys.values()) == [row.id for row in rows]


def test_a_day_sealed_and_deleted_before_the_first_import_is_still_recorded(
    vault: SealedVault, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    vault.seal_days(DAYS[:3])
    vault.retention(at_site(date(2026, 9, 11), 1, 0))  # deletes 2024-09-10
    report = import_job_events(sessions, layout)
    assert report.imported[0] == "seal:2024-09-10:sealed"
    assert report.imported[-1].startswith("retention:")
    assert len(report.imported) == 4


def test_damaged_files_are_reported_not_imported(
    vault: SealedVault, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    folder = layout.state / "seal"
    folder.mkdir(parents=True)
    (folder / "2024-09-10.json").write_bytes(b"{not json")
    (folder / "2024-09-11.json").write_text(json.dumps({"day": "yesterday"}), encoding="utf-8")
    (folder / "2024-09-12.json").write_text(json.dumps({"day": "2024-09-01"}), encoding="utf-8")
    (folder / "2024-09-13.json").write_text(
        json.dumps({"day": "2024-09-13", "sealed_at": "x", "lines": 1.5}), encoding="utf-8"
    )
    layout.deletions.mkdir(parents=True)
    (layout.deletions / "2026-09-01-imha.json").write_text("[]", encoding="utf-8")
    report = import_job_events(sessions, layout)
    assert report.imported == []
    assert report.problems == [
        "2024-09-10.json: JSONDecodeError",
        "2024-09-11.json: not a seal state",
        "2024-09-12.json: state of another day",
        "seal:2024-09-13:sealed: audit details cannot be stored exactly: floating-point "
        "numbers are not allowed in canonical documents",
        "2026-09-01-imha.json: not a deletion record",
    ]
    assert entries(db_session) == []
    assert report.details()["problems"] == report.problems


def test_an_event_imported_meanwhile_by_another_worker_is_skipped(
    sessions: sessionmaker[Session], db_session: Session
) -> None:
    event = AuditEvent("seal:2024-09-10:sealed", "seal.run", "seal:2024-09-10", {"day": "x"})
    calls = 0

    def racing() -> Session:
        """After this importer read the known keys, another worker imports the same event."""
        nonlocal calls
        calls += 1
        if calls == 2:
            assert import_events(sessions, [event], ImportReport()).imported == [event.key]
        return sessions()

    report = import_events(cast(sessionmaker[Session], racing), [event], ImportReport())
    assert report.imported == []  # seen under the chain lock, not imported twice
    assert len(entries(db_session)) == 1


def test_the_worker_runs_the_import_every_15_minutes(
    vault: SealedVault, layout: Layout, db_engine: Engine, db_session: Session
) -> None:
    vault.seal_days(DAYS[:1])
    (job,) = [job for job in default_jobs(60) if job.name == "audit.import"]
    assert job.every == timedelta(minutes=15)
    now = at_site(date(2026, 10, 13), 12)
    worker = Worker(engine=db_engine, layout=layout, jobs=[job], clock=lambda: now)
    assert worker.tick() == [("audit.import", "ok")]
    assert [row.action for row in entries(db_session)] == ["seal.run"]


def retention_keys(report: ImportReport) -> list[str]:
    return [key for key in report.imported if key.startswith("retention:")]


def test_failed_retention_runs_are_never_reported_as_deletions(
    vault: SealedVault, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    """A retention run whose time stamp failed (service unreachable) or did not verify leaves no
    record (``arcilogger_core.retention`` writes it as ``.part`` and removes it) and deleted
    nothing: only its ``R20-retention`` alert is imported, as ``refused``; leftovers that are
    not complete records are never imported at all."""
    vault.seal_days(DAYS)
    first_run = at_site(date(2026, 9, 12), 1, 0)
    vault.tsa.failure = MockFailure.UNREACHABLE
    unreachable = vault.retention(first_run)
    assert unreachable.error is not None
    assert "could not be time-stamped" in unreachable.error
    # What an interrupted run may leave behind: never a deletion record.
    (layout.deletions / "2026-09-12-imha.json.part").write_bytes(b'{"days": []}')
    (layout.deletions / "2026-09-12-imha.json.part.zd").write_bytes(b"token of a part")
    report = import_job_events(sessions, layout, now=first_run + timedelta(hours=1))
    (refused_first,) = retention_keys(report)
    assert refused_first.startswith("retention:alert:")
    assert report.waiting == []
    assert report.problems == []

    second_run = at_site(date(2026, 9, 13), 1, 0)
    vault.tsa.failure = MockFailure.BAD_SIGNATURE
    result = vault.retention(second_run)
    assert result.error is not None
    assert result.error.startswith("İmha tutanağının zaman damgası doğrulanamadı")
    assert "time stamp did not verify; nothing deleted" in result.error
    assert sorted(p.name for p in layout.deletions.iterdir()) == []  # leftovers tidied up too
    vault.tsa.failure = MockFailure.NONE
    report = import_job_events(sessions, layout, now=second_run + timedelta(hours=1))
    (refused,) = retention_keys(report)
    assert refused.startswith("retention:alert:")
    assert report.waiting == []
    rows = [row for row in entries(db_session) if row.action == "retention.run"]
    assert [row.details["outcome"] for row in rows] == ["refused", "refused"]
    assert [row.details["severity"] for row in rows] == ["high", "critical"]
    assert rows[0].details["message"] == unreachable.error
    assert rows[1].details["message"] == result.error
    assert all(row.details["source"].startswith("state/alerts.jsonl line ") for row in rows)

    # The same day's run is repeated and succeeds: its record is complete.
    result = vault.retention(second_run + timedelta(hours=2))
    assert result.deleted == DAYS[:3], result.error
    report = import_job_events(sessions, layout, now=second_run + timedelta(hours=3))
    assert retention_keys(report) == ["retention:2026-09-13-imha.json"]
    deleted = entries(db_session)[-1].details
    assert deleted["outcome"] == "deleted"
    assert deleted["days"] == ["2024-09-10", "2024-09-11", "2024-09-12"]
    assert report.waiting == []
    # The successful run's own (info) alert is not a second entry; nothing is imported twice.
    later = import_job_events(sessions, layout, now=second_run + timedelta(days=3))
    assert later.imported == []
    assert chain_ok(entries(db_session))


def test_an_unstamped_record_of_an_earlier_version_is_incomplete(
    vault: SealedVault, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    """A final-named record without a time stamp (written before the ``.part`` naming) deleted
    nothing: imported as ``incomplete`` once it is a day old, never as a deletion."""
    vault.seal_days(DAYS)
    layout.deletions.mkdir(parents=True, exist_ok=True)
    record = layout.deletions / "2026-09-12-imha.json"
    facts = {"created_at": "2026-09-12T01:00:00.000+03:00", "days": [{"day": "2024-09-10"}]}
    record.write_text(json.dumps(facts), encoding="utf-8")
    run = at_site(date(2026, 9, 12), 1, 0)
    report = import_job_events(sessions, layout, now=run + timedelta(hours=1))
    assert retention_keys(report) == []
    assert report.waiting == ["2026-09-12-imha.json"]
    report = import_job_events(sessions, layout, now=run + timedelta(days=2))
    assert retention_keys(report) == ["retention:2026-09-12-imha.json:incomplete"]
    (row,) = [r for r in entries(db_session) if r.action == "retention.run"]
    assert row.details["outcome"] == "incomplete"
    assert row.details["token_sha256"] is None
    assert row.details["still_in_vault"] == ["2024-09-10"]
    assert row.details["reason"].startswith("the record has no time stamp")


def test_a_record_whose_days_are_still_sealed_is_incomplete(
    vault: SealedVault,
    layout: Layout,
    sessions: sessionmaker[Session],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A time-stamped record listing days still in the vault (the run stopped while deleting,
    e.g. a file in use): ``incomplete`` after a day; once the next run finished the deletion,
    the record is imported as ``deleted`` too."""
    vault.seal_days(DAYS)
    run = at_site(date(2026, 9, 13), 1, 0)
    real = retention._delete_day

    def busy(target: Layout, day: date, record: Path) -> None:
        if day == DAYS[1]:
            raise PermissionError(13, "in use", "fortigate.log.gz")
        real(target, day, record)

    monkeypatch.setattr(retention, "_delete_day", busy)
    stopped = vault.retention(run)
    assert stopped.error is not None
    assert stopped.deleted == [DAYS[0]]
    monkeypatch.setattr(retention, "_delete_day", real)
    report = import_job_events(sessions, layout, now=run + timedelta(days=2))
    assert "retention:2026-09-13-imha.json:incomplete" in report.imported
    (row,) = [r for r in entries(db_session) if r.target == "retention:2026-09-13-imha.json"]
    assert row.details["outcome"] == "incomplete"
    assert row.details["token_sha256"]
    assert row.details["still_in_vault"] == ["2024-09-11", "2024-09-12"]
    assert row.details["reason"] == "not every listed day was deleted by this record"

    finished = vault.retention(run + timedelta(hours=2))
    assert finished.error is None, finished.error
    assert finished.resumed == "2026-09-13-imha.json"
    report = import_job_events(sessions, layout, now=run + timedelta(days=3))
    assert retention_keys(report) == ["retention:2026-09-13-imha.json"]
    assert entries(db_session)[-1].details["outcome"] == "deleted"
    assert chain_ok(entries(db_session))


def test_alerts_record_every_failed_attempt_and_nothing_else(
    vault: SealedVault, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    """Two attempts failed before the import ran: the state file shows only the second."""
    vault.write_day(DAYS[0])
    vault.fail_seal(DAYS[0])
    vault.fail_seal(DAYS[0], MockFailure.NO_CREDIT)
    with layout.alerts.open("ab") as handle:
        handle.write(b'{"rule": "R20-credit", "message": "R20-seal mentioned", "x": [\n')
        handle.write(b'{"rule": "R20-seal", "time": "x", "details": {"day": "soon"}}\n')
        handle.write(b'{"rule": "R15", "severity": "high", "message": "source silent"}\n')
    report = import_job_events(sessions, layout)
    assert report.imported == ["seal:2024-09-10:attempt:2", "seal:2024-09-10:attempt:1"]
    assert report.problems == [
        "alerts.jsonl line 3: unreadable",
        "alerts.jsonl line 4: not a seal alert",
    ]
    second, first = entries(db_session)
    assert second.details["source"] == "state/seal/2024-09-10.json"
    assert second.details["error"]
    assert first.target == "seal:2024-09-10"
    assert first.details["outcome"] == "failed"
    assert first.details["attempt"] == 1
    assert first.details["source"] == "state/alerts.jsonl line 1"
    assert first.details["message"].startswith("Mühürleme başarısız (10.09.2024): mock TSA")
    assert first.details["retryable"] is True
    assert first.details["at"]
    assert import_job_events(sessions, layout).imported == []
    # Without an alert file there is nothing to read.
    layout.alerts.unlink()
    assert alert_events(layout, ImportReport()) == []


def test_credential_changes_are_imported_once_without_values(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    """``arcilogger-core secret set|delete`` leaves a line; the import makes it an audit entry."""
    for name, action, result in (
        ("kamusm.password", "set", "stored"),
        ("database.password", "delete", "deleted"),
    ):
        record_change(
            layout.secret_changes, name=name, action=action, result=result, operator="cli:tolga"
        )
    with layout.secret_changes.open("ab") as handle:
        handle.write(b'{"id": "not-an-id", "name": "x"}\n\n')
    report = import_job_events(sessions, layout)
    assert len(report.imported) == 2
    assert report.problems == ["secret-changes.jsonl line 3: unreadable"]
    rows = entries(db_session)
    assert [(r.action, r.target) for r in rows] == [
        ("settings.secret_change", "secret:kamusm.password"),
        ("settings.secret_change", "secret:database.password"),
    ]
    assert rows[0].details["operator"] == "cli:tolga"
    assert rows[0].details["action"] == "set"
    assert rows[1].details["result"] == "deleted"
    assert rows[0].details["source"] == "state/seal/secret-changes.jsonl line 1"
    assert set(rows[0].details) == {"name", "action", "result", "operator", "at", "source"}
    assert chain_ok(rows)
    assert import_job_events(sessions, layout).imported == []  # once
    layout.secret_changes.unlink()
    assert secret_events(layout, ImportReport()) == []
