"""``evidence.purge``: expired packages leave ``exports/``, rows follow the files, audited."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from arcilogger_core.evidence import build_package
from arcilogger_core.layout import Layout
from arcilogger_db.models import AuditLog, EvidencePackage
from arcilogger_db.settings import set_setting
from arcilogger_worker.evidence_jobs import INTERRUPTED, MISSING, package_path, purge_evidence
from arcilogger_worker.service import Worker, default_jobs, job_evidence_purge
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

NOW = datetime(2026, 10, 13, 0, 30, tzinfo=UTC)
DAY = date(2026, 9, 10)


def add_row(session: Session, *, path: str, status: str = "ready", **values: object) -> int:
    values.setdefault("expires_at", NOW + timedelta(days=10))
    row = EvidencePackage(
        username="admin1",
        scope={"content": "full_days", "days": [DAY.isoformat()]},
        path=path,
        status=status,
        **values,
    )
    session.add(row)
    session.commit()
    return row.id


def package_file(layout: Layout, name: str, *, age_days: float = 0) -> Path:
    path = layout.exports / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"PK zip")
    stamp = NOW.timestamp() - age_days * 86400
    os.utime(path, (stamp, stamp))
    return path


def statuses(session: Session) -> dict[int, tuple[str, str | None]]:
    session.expire_all()
    return {row.id: (row.status, row.error) for row in session.scalars(select(EvidencePackage))}


def test_purge_reconciles_rows_and_files(db_session: Session, layout: Layout) -> None:
    name = "ARCILOGGER-kanit-KP-{}.zip".format
    expired = add_row(db_session, path=name("1"), expires_at=NOW - timedelta(seconds=1))
    package_file(layout, name("1"), age_days=5)
    current = add_row(db_session, path=name("2"))
    package_file(layout, name("2"), age_days=5)
    vanished = add_row(db_session, path=name("3"))
    old = add_row(db_session, path=name("4"))  # keep_days was lowered after it was built
    package_file(layout, name("4"), age_days=40)
    orphan = package_file(layout, name("cli-built"), age_days=31)
    part = package_file(layout, name("5") + ".part", age_days=2)
    other = package_file(layout, "notes.txt", age_days=400)
    stuck = add_row(db_session, path="", status="building")
    db_session.get_one(EvidencePackage, stuck).created_at = NOW - timedelta(days=2)
    fresh = add_row(db_session, path="", status="queued")
    db_session.get_one(EvidencePackage, fresh).created_at = NOW - timedelta(hours=1)
    db_session.commit()

    report = purge_evidence(db_session, layout, now=NOW)
    assert statuses(db_session) == {
        expired: ("expired", None),
        current: ("ready", None),
        vanished: ("deleted", MISSING),
        old: ("expired", None),
        stuck: ("failed", INTERRUPTED),
        fresh: ("queued", None),
    }
    assert sorted(report.files_deleted) == sorted(
        [name("4"), name("cli-built"), name("5") + ".part", name("1")]
    )
    assert not orphan.exists()
    assert not part.exists()
    assert other.exists()  # only ARCILOGGER packages are ever deleted
    assert (layout.exports / name("2")).exists()
    (entry,) = db_session.scalars(select(AuditLog)).all()
    assert (entry.action, entry.actor_username, entry.target) == (
        "evidence.purge",
        "system",
        "exports",
    )
    assert entry.details["keep_days"] == 30
    assert entry.details["expired"] == [expired, old]
    assert entry.details["missing"] == [vanished]
    assert entry.details["interrupted"] == [stuck]
    assert entry.details["files_deleted_count"] == 4

    # Nothing left to do: no second audit entry.
    again = purge_evidence(db_session, layout, now=NOW)
    assert not again.changed
    assert len(db_session.scalars(select(AuditLog)).all()) == 1


def test_keep_days_setting_and_bad_names(db_session: Session, layout: Layout) -> None:
    set_setting(db_session, "evidence.keep_days", 7, updated_by="admin1")
    db_session.commit()
    package_file(layout, "ARCILOGGER-kanit-KP-x.zip", age_days=8)
    escape = add_row(db_session, path="../vault/x.zip")
    report = purge_evidence(db_session, layout, now=NOW)
    assert report.keep_days == 7
    assert report.files_deleted == ["ARCILOGGER-kanit-KP-x.zip"]
    assert statuses(db_session)[escape] == ("deleted", MISSING)
    assert package_path(layout, "../vault/x.zip") is None
    assert package_path(layout, "ARCILOGGER-kanit-KP-1.zip") == (
        layout.exports / "ARCILOGGER-kanit-KP-1.zip"
    )


def test_a_file_that_cannot_be_deleted_now_is_retried(
    db_session: Session, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    name = "ARCILOGGER-kanit-KP-busy.zip"
    row = add_row(db_session, path=name, expires_at=NOW - timedelta(days=1))
    path = package_file(layout, name)
    real_unlink = Path.unlink

    def unlink(self: Path, missing_ok: bool = False) -> None:
        if self.name == name:  # like a package being downloaded on Windows
            raise PermissionError(13, "in use", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink)
    report = purge_evidence(db_session, layout, now=NOW)
    assert report.busy == [name]
    assert statuses(db_session)[row] == ("ready", None)
    assert path.exists()
    monkeypatch.undo()
    assert purge_evidence(db_session, layout, now=NOW).expired == [row]


def test_purge_survives_a_file_it_cannot_delete(
    db_session: Session, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``purge_expired`` stops at a locked file; rows are still reconciled, by their age."""
    old_name = "ARCILOGGER-kanit-KP-old.zip"
    package_file(layout, old_name, age_days=40)
    gone = add_row(
        db_session, path="ARCILOGGER-kanit-KP-gone.zip", finished_at=NOW - timedelta(days=31)
    )

    def locked(*args: object, **kwargs: object) -> list[Path]:
        raise PermissionError(13, "in use", str(layout.exports / old_name))

    monkeypatch.setattr("arcilogger_worker.evidence_jobs.purge_expired", locked)
    report = purge_evidence(db_session, layout, now=NOW)
    assert report.busy == [old_name]
    assert statuses(db_session)[gone] == ("expired", None)  # older than keep_days: expired


def test_the_worker_job_purges_a_real_package(
    db_engine: Engine, db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    vault: SealedVault = make_vault(layout.root, tsa_now=at_site(DAY - timedelta(days=30)))
    try:
        vault.seal_days([DAY])
        info = build_package(layout, [DAY], roots=[vault.tsa.root_certificate])
        row = add_row(db_session, path=info.path.name, expires_at=NOW - timedelta(minutes=1))
        jobs = [job for job in default_jobs(60) if job.name == "evidence.purge"]
        assert jobs[0].daily_after == time(3, 0)
        assert jobs[0].func is job_evidence_purge
        worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=lambda: NOW)
        assert worker.tick() == [("evidence.purge", "ok")]
        assert not info.path.exists()
        assert statuses(db_session)[row] == ("expired", None)
        assert vault.layout.manifest(DAY).exists()  # the vault is never touched
    finally:
        vault.unlock_all()
