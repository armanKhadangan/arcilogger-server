"""The worker and the licence (licensing contract 1.4, 7; tests B-T14, B-T15).

* The scheduler asks the gate before the ``running`` row of a paid job: while its module is off
  the job does not run, one ``skipped`` row per job per site day records it, one log line per
  gate change, ``R20-report`` is resolved, and every 5651 job runs in every locked state.
* ``license.check`` records the first state, each state change once, limit crossings once
  (only where a limit exists; a state change creates no limit event), and writes the
  installer's status file.
* ``arcilogger-worker usage rebuild`` is refused while ``usage_analytics`` is off;
  ``arcilogger-worker rebuild`` (the lease timeline) never is.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import Engine, make_url, select, update
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.license import fingerprint, keys, load_license
from arcilogger_core.license.testing import (
    all_modules_status,
    base_only_status,
    fixed_identity,
    make_license,
    override_status,
    trusted_test_keys,
)
from arcilogger_core.license.trusted_time import clear_cache
from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import (
    Alert,
    AlertEvent,
    AuditLog,
    JobRun,
    LicenseEvent,
    LicenseFile,
    LicenseInstallation,
    Source,
)
from arcilogger_worker.cli import EXIT_FAILED, EXIT_OK, main
from arcilogger_worker.licensing import job_license_check
from arcilogger_worker.reports.runner import REPORT_RULE, UNLICENSED_NOTE
from arcilogger_worker.service import SKIPPED, Job, JobContext, Worker, default_jobs

pytestmark = pytest.mark.license_live

GATED = {
    "usage.requests": "usage_analytics",
    "usage.daily": "usage_analytics",
    "usage.today": "usage_analytics",
    "usage.reattribute": "usage_analytics",
    "reports.run_due": "usage_reports",
}


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now


def recorder(calls: list[str], name: str) -> Callable[[JobContext], Mapping[str, Any]]:
    def job(ctx: JobContext) -> Mapping[str, Any]:
        calls.append(name)
        return {}

    return job


def runs(session: Session, job: str | None = None) -> list[JobRun]:
    session.expire_all()
    query = select(JobRun).order_by(JobRun.id)
    if job is not None:
        query = query.where(JobRun.job == job)
    return list(session.scalars(query))


@pytest.fixture(autouse=True)
def server_id() -> Iterator[str]:
    clear_cache()
    with fixed_identity() as server_id, trusted_test_keys():
        yield server_id
    clear_cache()


def store(session: Session, data: bytes) -> None:
    """The active licence, as an install stored it."""
    document = load_license(data)
    row = LicenseFile(
        sha256=document.sha256,
        payload_sha256=document.payload_sha256,
        content=data,
        license_id=document.payload.license_id,
        key_id=document.key_id,
        licensee=document.payload.licensee,
        issued_at=document.payload.issued_at,
        first_installed_at=utc_now(),
        first_installed_by="admin",
    )
    session.add(row)
    session.flush()
    session.add(
        LicenseEvent(
            at=utc_now(), kind="install", source="web", actor="admin", license_file_id=row.id
        )
    )
    session.commit()


def trial_over(session: Session) -> None:
    session.add(
        LicenseInstallation(
            id=1, installation_id=uuid4(), trial_started_at=utc_now() - timedelta(days=30)
        )
    )
    session.commit()


LOCKED = ["trial_expired", "invalid", "wrong_server", "expired", "version_not_covered"]


def lock(session: Session, state: str, server_id: str, monkeypatch: pytest.MonkeyPatch) -> None:
    trial_over(session)
    if state == "trial_expired":
        return
    options: dict[str, Any] = {
        "server_ids": ["AAAA-BBBB-CCCC-DDDD" if state == "wrong_server" else server_id]
    }
    if state == "expired":
        options["valid_until"] = utc_now().date() - timedelta(days=3)
    if state == "version_not_covered":
        options["maintenance_until"] = date(2026, 1, 1)
    store(session, make_license(**options))
    if state == "invalid":
        monkeypatch.setattr(keys, "TRUSTED_KEYS", {})


# -- B-T14 the scheduler's gate -------------------------------------------------------------------


def test_default_jobs_name_their_modules() -> None:
    jobs = {job.name: job.module for job in default_jobs(60)}
    assert {name: module for name, module in jobs.items() if module} == GATED
    names = list(jobs)
    assert names[:2] == ["partitions.ensure", "license.check"]
    assert {"usage.retention", "reports.purge", "alerts.notify", "evidence.purge"} <= {
        name for name, module in jobs.items() if module is None
    }


def test_a_locked_job_is_skipped_before_its_running_row(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    calls: list[str] = []
    clock = Clock(utc_now())
    jobs = [
        Job(
            "gated", recorder(calls, "gated"), every=timedelta(minutes=1), module="usage_analytics"
        ),
        Job("free", recorder(calls, "free"), every=timedelta(minutes=1)),
    ]
    with override_status(base_only_status()):
        worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=clock)
        assert worker.tick() == [("gated", "skipped"), ("free", "ok")]
    assert calls == ["free"]
    (row,) = runs(db_session, "gated")
    assert row.status == "skipped"
    assert row.details == {"license": {"module": "usage_analytics", "status": "trial_expired"}}
    assert row.finished_at is not None
    with override_status(all_modules_status()):
        worker.license_cache.invalidate()
        clock.now += timedelta(minutes=1)
        assert worker.tick() == [("gated", "ok"), ("free", "ok")]
    assert calls == ["free", "gated", "free"]


def test_one_skipped_row_per_job_and_site_day(
    db_engine: Engine, db_session: Session, layout: Layout, caplog: pytest.LogCaptureFixture
) -> None:
    calls: list[str] = []
    start = datetime.combine(utc_now().date(), time(0, 1), tzinfo=UTC) - timedelta(hours=3)
    clock = Clock(start)  # 00:01 site time
    jobs = [
        Job(
            "usage.today",
            recorder(calls, "today"),
            every=timedelta(minutes=5),
            module="usage_analytics",
        ),
        Job(
            "usage.daily",
            recorder(calls, "daily"),
            daily_after=time(0, 45),
            module="usage_analytics",
        ),
    ]
    worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=clock)
    with override_status(base_only_status()), caplog.at_level(logging.INFO, "arcilogger_worker"):
        for _ in range(24 * 12):  # a whole site day, a tick every 5 minutes
            worker.tick()
            clock.now += timedelta(minutes=5)
    assert calls == []
    assert [row.job for row in runs(db_session)] == ["usage.today", "usage.daily"]
    skipped = [r.getMessage() for r in caplog.records if "not licensed" in r.getMessage()]
    assert skipped == [
        "job usage.today skipped: module usage_analytics not licensed (trial_expired)",
        "job usage.daily skipped: module usage_analytics not licensed (trial_expired)",
    ]
    assert worker.states["usage.daily"].done_day is not None  # the skip counts as done
    # The next site day: one more row each.
    with override_status(base_only_status()):
        clock.now += timedelta(hours=1)
        worker.tick()
    assert len(runs(db_session, "usage.today")) == 2
    assert len(runs(db_session, "usage.daily")) == 2
    # Licensed again: one line, the job runs.
    caplog.clear()
    with override_status(all_modules_status()), caplog.at_level(logging.INFO, "arcilogger_worker"):
        worker.license_cache.invalidate()
        clock.now += timedelta(minutes=5)
        worker.tick()
    assert "today" in calls
    assert any("job usage.today runs" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("state", LOCKED)
def test_every_5651_job_runs_in_every_locked_state(
    db_engine: Engine,
    db_session: Session,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    server_id: str,
    state: str,
) -> None:
    lock(db_session, state, server_id, monkeypatch)
    clock = Clock(utc_now())
    worker = Worker(engine=db_engine, layout=layout, jobs=default_jobs(60), clock=clock)
    done = dict(worker.tick())
    assert {"usage.requests", "usage.today", "usage.reattribute", "reports.run_due"} <= set(done)
    for name, module in GATED.items():
        if name not in done:
            continue  # usage.daily before 00:45 site time
        assert done[name] == "skipped", name
        (row,) = runs(db_session, name)
        assert row.details["license"] == {"module": module, "status": state}
    for name, status in done.items():
        if name not in GATED:
            assert status in ("ok", "skipped"), (name, status)  # never failed by the licence
            assert not (runs(db_session, name)[-1].details or {}).get("license"), name
    assert done["license.check"] == "ok"
    check = runs(db_session, "license.check")[-1]
    assert check.details["status"] == state
    assert check.details["enabled"] == ["core_5651"]


def test_skips_never_raise_r20_job(db_engine: Engine, db_session: Session, layout: Layout) -> None:
    clock = Clock(utc_now())
    jobs = [j for j in default_jobs(60) if j.name in ("usage.today", "alerts.system")]
    with override_status(base_only_status()):
        worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=clock)
        for _ in range(4):
            worker.tick()
            clock.now += timedelta(days=1)
    alerts = db_session.scalars(select(Alert).where(Alert.dedup_key.like("R20-job:%"))).all()
    assert alerts == []


def test_r20_report_is_resolved_when_reports_go_off(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    now = utc_now()
    alert = Alert(
        time=now,
        rule=REPORT_RULE,
        severity="medium",
        title="Kullanım raporu gönderilemedi",
        state="open",
        dedup_key=REPORT_RULE,
        details={"recipients": ["ik@example.com"], "runs": [1], "render_failed": False},
    )
    db_session.add(alert)
    db_session.commit()
    jobs = [j for j in default_jobs(60) if j.name == "reports.run_due"]
    with override_status(base_only_status()):
        worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=Clock(now))
        assert worker.tick() == [("reports.run_due", "skipped")]
    db_session.expire_all()
    resolved = db_session.get_one(Alert, alert.id)
    assert resolved.state == "resolved"
    assert resolved.details["auto_resolved"]["reason"] == "module_not_licensed"
    events = db_session.scalars(
        select(AlertEvent).where(AlertEvent.alert_id == alert.id).order_by(AlertEvent.id)
    ).all()
    assert [(e.kind, e.text) for e in events] == [
        ("state", "module_not_licensed"),
        ("note", UNLICENSED_NOTE),
    ]
    assert all(e.actor == "system" for e in events)


# -- the CLI --------------------------------------------------------------------------------------


def write_config(tmp_path: Path, dsn: str) -> Path:
    url = make_url(dsn)
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n\n'
        "[database]\n"
        f'host = "{url.host}"\nport = {url.port or 5432}\n'
        f'name = "{url.database}"\nuser = "{url.username}"\nconnect_timeout_seconds = 2\n',
        encoding="utf-8",
    )
    if url.password:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, str(url.password)
        )
    return path


def test_usage_rebuild_is_refused_and_lease_rebuild_works_when_locked(
    pg_dsn: str, tmp_path: Path, db_session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(write_config(tmp_path, pg_dsn))
    day = (utc_now() - timedelta(days=2)).date().isoformat()
    with override_status(base_only_status()):
        assert main(["usage", "rebuild", "--from", day, "--config", config]) == EXIT_FAILED
        err = capsys.readouterr().err
        assert "Bu özellik lisansınızda yok: Kullanım analizi." in err
        assert "[This feature is not part of your licence: Usage analytics." in err
        assert runs(db_session, "usage.rebuild") == []
        assert main(["rebuild", "--day", day, "--config", config]) == EXIT_OK
    with override_status(all_modules_status()):
        assert main(["usage", "rebuild", "--from", day, "--config", config]) == EXIT_OK


# -- B-T15 license.check --------------------------------------------------------------------------


def check(worker: Worker, at: datetime) -> Mapping[str, Any]:
    return job_license_check(worker.context(at))


def events(session: Session, kind: str) -> list[LicenseEvent]:
    session.expire_all()
    return list(
        session.scalars(
            select(LicenseEvent).where(LicenseEvent.kind == kind).order_by(LicenseEvent.id)
        )
    )


def test_license_check_records_state_changes_once(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    now = utc_now()
    worker = Worker(engine=db_engine, layout=layout, jobs=[], clock=Clock(now))
    first = check(worker, now)
    assert first == {
        "status": "trial",
        "enabled": ["core_5651", "usage_analytics", "usage_reports", "bulk_import"],
        "recorded": ["state"],
    }
    (state,) = events(db_session, "state")
    assert (state.details["from"], state.details["to"]) == (None, "trial")
    assert (state.source, state.actor) == ("worker", "system")
    assert check(worker, now + timedelta(minutes=5))["recorded"] == []
    later = now + timedelta(days=15)
    assert check(worker, later)["recorded"] == ["state"]  # trial -> trial_expired, once
    assert check(worker, later + timedelta(minutes=5))["recorded"] == []
    second = events(db_session, "state")[-1]
    assert second.details == {
        "from": "trial",
        "to": "trial_expired",
        "enabled_from": ["core_5651", "usage_analytics", "usage_reports", "bulk_import"],
        "enabled_to": ["core_5651"],
        "problem": None,
    }
    audit = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "license.state").order_by(AuditLog.id)
    ).all()
    assert [entry.details["to"] for entry in audit] == ["trial", "trial_expired"]
    assert all(entry.actor_username == "system" for entry in audit)
    status = json.loads((layout.state / "license-status.json").read_text(encoding="utf-8"))
    assert status["schema"] == "arcilogger.license-status/1"
    assert status["status"] == "trial_expired"
    assert status["modules"] == ["core_5651"]
    assert status["license_id"] is None


def test_license_check_source_limit_crossings(
    db_engine: Engine, db_session: Session, layout: Layout, server_id: str
) -> None:
    now = utc_now()
    store(
        db_session,
        make_license(
            server_ids=[server_id], log_sources=2, valid_until=(now + timedelta(days=5)).date()
        ),
    )
    db_session.add_all(
        [
            # an unconfigured sender with data, a disabled source with data: both count
            Source(name="unknown-198.51.100.7", type="generic", unknown=True, last_seen=now),
            Source(
                name="switch-2", type="generic", enabled=False, last_seen=now - timedelta(days=2)
            ),
            # silent for 29 days: still counts for one more day
            Source(name="old-ap", type="generic", last_seen=now - timedelta(days=29)),
        ]
    )
    db_session.commit()
    worker = Worker(engine=db_engine, layout=layout, jobs=[], clock=Clock(now))
    assert check(worker, now)["recorded"] == ["state"]  # the first state, no limit event
    assert check(worker, now)["recorded"] == ["limit"]  # 3 > 2
    assert check(worker, now)["recorded"] == []
    (exceeded,) = events(db_session, "limit")
    assert exceeded.details == {"limit": "log_sources", "exceeded": True, "max": 2, "used": 3}
    entry = db_session.scalars(
        select(AuditLog).where(AuditLog.action == "license.limit_exceeded")
    ).one()
    assert entry.details == {"limit": "log_sources", "max": 2, "used": 3}
    # Two days later the old access point no longer counts: back under the limit, once.
    later = now + timedelta(days=2)
    assert check(worker, later)["recorded"] == ["limit"]
    assert check(worker, later)["recorded"] == []
    assert [e.details["exceeded"] for e in events(db_session, "limit")] == [True, False]
    assert db_session.scalars(select(AuditLog).where(AuditLog.action == "license.limit_ok")).one()
    # Over again, then the licence expires: a state change, no limit event.
    db_session.add(
        Source(name="unknown-198.51.100.8", type="generic", unknown=True, last_seen=later)
    )
    db_session.commit()
    assert check(worker, later)["recorded"] == ["limit"]
    expired = now + timedelta(days=10)
    db_session.execute(update(Source).values(last_seen=expired))
    db_session.commit()
    assert check(worker, expired)["recorded"] == ["state"]
    assert check(worker, expired)["recorded"] == []  # the document's limit stays, still exceeded
    assert events(db_session, "state")[-1].details["to"] == "expired"
    assert len(events(db_session, "limit")) == 3


def test_license_check_fails_like_any_job_without_the_licence_code(
    db_engine: Engine, db_session: Session, layout: Layout, monkeypatch: pytest.MonkeyPatch
) -> None:
    import sys

    import arcilogger_db

    monkeypatch.setitem(sys.modules, "arcilogger_db.licensing", None)
    monkeypatch.delattr(arcilogger_db, "licensing", raising=False)
    for name in [n for n in sys.modules if n.startswith("arcilogger_core.license")]:
        monkeypatch.setitem(sys.modules, name, None)
    jobs = [
        j
        for j in default_jobs(60)
        if j.name in ("license.check", "leases.incremental", "usage.today")
    ]
    worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=Clock(utc_now()))
    assert dict(worker.tick()) == {
        "license.check": "failed",
        "leases.incremental": "ok",
        "usage.today": "skipped",  # the gate locks the paid job, nothing else
    }
    (skipped,) = runs(db_session, "usage.today")
    assert skipped.details["license"]["status"] == "unavailable"
    assert SKIPPED not in skipped.details
    assert fingerprint is not None
