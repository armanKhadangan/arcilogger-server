"""``alerts.system``: every R20 check with a positive and a negative case, its dedup key and
its automatic resolution."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from sqlalchemy import Engine, insert, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import BackupConfig, Config, ReplicaConfig
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, site_day
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, JobRun, Message, SealVerification
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts import system
from arcilogger_worker.alerts.importer import import_alerts
from arcilogger_worker.alerts.system import CheckError, drives, run_system_checks
from arcilogger_worker.service import Worker, default_jobs
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

DAYS = [date(2026, 10, 10) + timedelta(days=n) for n in range(3)]
#: 13 October 2026, 12:00 site time.
NOW = at_site(date(2026, 10, 13), 12, 0)
GIB = 1024**3


@pytest.fixture
def vault(layout: Layout) -> Iterator[SealedVault]:
    sealed = make_vault(layout.root, tsa_now=at_site(DAYS[0] - timedelta(days=30)))
    yield sealed
    sealed.unlock_all()


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


@pytest.fixture
def config(layout: Layout) -> Config:
    return Config(data_dir=layout.root, secrets_dir=layout.root)


class Usage(NamedTuple):
    total: int
    used: int
    free: int


@pytest.fixture(autouse=True)
def roomy_disks(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    """Every drive 50 % used unless a test says otherwise (the real disks never decide)."""
    used: dict[str, int] = {}

    def usage(path: Path) -> Usage:
        percent = used.get(str(path), 50)
        total = 1000 * GIB
        return Usage(total, total * percent // 100, total - total * percent // 100)

    monkeypatch.setattr("arcilogger_worker.alerts.system.shutil.disk_usage", usage)
    return used


def run(
    sessions: sessionmaker[Session], layout: Layout, config: Config, now: datetime = NOW
) -> system.SystemReport:
    return run_system_checks(sessions, layout, config, now=now)


def active(session: Session) -> dict[str, Alert]:
    session.expire_all()
    rows = session.scalars(select(Alert).where(Alert.state.in_(("open", "acknowledged"))))
    return {row.dedup_key or "": row for row in rows}


def resolved(session: Session, key: str) -> Alert:
    session.expire_all()
    return session.scalars(
        select(Alert).where(Alert.dedup_key == key, Alert.state == "resolved")
    ).one()


def jsonl(layout: Layout, at: datetime, rule: str, severity: str, **details: Any) -> None:
    entry = {
        "time": isoformat_site(at, "seconds"),
        "rule": rule,
        "severity": severity,
        "message": f"{rule} from a task",
        "details": details,
    }
    layout.alerts.parent.mkdir(parents=True, exist_ok=True)
    with layout.alerts.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


# -- disk -----------------------------------------------------------------------------------------


def test_disk_above_the_threshold(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    db_session: Session,
    roomy_disks: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data, os_drive = layout.root, Path("/")
    monkeypatch.setattr(system, "drives", lambda _: [("D:", data), ("C:", os_drive)])
    run(sessions, layout, config)
    assert not [key for key in active(db_session) if key.startswith("R20-disk")]  # negative

    roomy_disks[str(data)] = 85
    report = run(sessions, layout, config)
    assert report.checks["disk"]["opened"] == ["R20-disk:D:"]
    alert = active(db_session)["R20-disk:D:"]
    assert (alert.rule, alert.severity) == ("R20-disk", "high")
    assert alert.details["used_percent"] == 85
    assert alert.details["threshold_percent"] == 80
    roomy_disks[str(os_drive)] = 81  # the system drive has its own alert
    roomy_disks[str(data)] = 96
    run(sessions, layout, config)
    alerts = active(db_session)
    assert alerts["R20-disk:D:"].severity == "critical"
    assert alerts["R20-disk:C:"].severity == "high"
    roomy_disks[str(data)] = 79  # within the 2-point margin: still open
    roomy_disks[str(os_drive)] = 60
    run(sessions, layout, config)
    assert "R20-disk:D:" in active(db_session)
    assert resolved(db_session, "R20-disk:C:").details["auto_resolved"]["reason"] == (
        "below_threshold"
    )
    roomy_disks[str(data)] = 78
    run(sessions, layout, config)
    assert "R20-disk:D:" not in active(db_session)


def test_disk_threshold_is_a_setting(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    db_session: Session,
    roomy_disks: dict[str, int],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(system, "drives", lambda _: [("D:", layout.root)])
    set_setting(db_session, "alerts.disk_used_percent", 90, updated_by="tolga")
    db_session.commit()
    roomy_disks[str(layout.root)] = 85
    run(sessions, layout, config)
    assert "R20-disk:D:" not in active(db_session)


def test_drives_are_the_data_drive_and_the_system_drive(tmp_path: Path) -> None:
    found = drives(tmp_path / "not-created-yet")
    assert found[0][1] == tmp_path  # the nearest existing folder
    assert len({label for label, _ in found}) == len(found) <= 2


# -- sealing --------------------------------------------------------------------------------------


def test_a_day_still_unsealed_in_the_morning(
    vault: SealedVault,
    sessions: sessionmaker[Session],
    layout: Layout,
    db_session: Session,
) -> None:
    vault.seal_days(DAYS[:2])
    vault.write_day(DAYS[2])
    vault.fail_seal(DAYS[2])  # 13 Oct 00:30: Kamu SM unreachable (R20-seal in alerts.jsonl)
    import_alerts(sessions, layout, now=at_site(DAYS[2] + timedelta(days=1), 0, 31))
    key = f"R20-seal:{DAYS[2].isoformat()}"
    assert key in active(db_session)
    early = at_site(DAYS[2] + timedelta(days=1), 8, 59)
    report = run(sessions, layout, vault.config, early)
    assert report.checks["seal"]["found"] == []  # not overdue before 09:00
    assert key in active(db_session)  # and the failed attempt is still open
    late = at_site(DAYS[2] + timedelta(days=1), 9, 0)
    report = run(sessions, layout, vault.config, late)
    assert report.checks["seal"]["found"] == [key]
    assert report.checks["seal"]["opened"] == []  # the same alert as the failed attempt
    alert = active(db_session)[key]
    assert alert.details["overdue"] is True
    assert alert.details["attempts"] == 1
    vault.seal(DAYS[2], now=late + timedelta(minutes=5))
    run(sessions, layout, vault.config, late + timedelta(minutes=10))
    assert resolved(db_session, key).details["auto_resolved"]["reason"] == "sealed"


def test_no_seal_alert_when_every_closed_day_is_sealed(
    vault: SealedVault, sessions: sessionmaker[Session], layout: Layout, db_session: Session
) -> None:
    vault.seal_days(DAYS)
    report = run(sessions, layout, vault.config, at_site(DAYS[2] + timedelta(days=1), 10))
    assert report.checks["seal"]["found"] == []
    assert active(db_session) == {}


def test_seal_overdue_hour_is_a_setting(
    vault: SealedVault, sessions: sessionmaker[Session], layout: Layout, db_session: Session
) -> None:
    vault.seal_days(DAYS[:2])
    vault.write_day(DAYS[2])
    set_setting(db_session, "alerts.seal_overdue_hour", 6, updated_by="tolga")
    db_session.commit()
    report = run(sessions, layout, vault.config, at_site(DAYS[2] + timedelta(days=1), 6, 1))
    assert report.checks["seal"]["opened"] == [f"R20-seal:{DAYS[2].isoformat()}"]


def test_a_seal_configuration_alert_clears_once_a_day_is_sealed(
    vault: SealedVault, sessions: sessionmaker[Session], layout: Layout, db_session: Session
) -> None:
    vault.seal_days(DAYS[:2])
    vault.write_day(DAYS[2])
    raised = at_site(DAYS[2] + timedelta(days=1), 0, 10)
    jsonl(layout, raised, "R20-seal-config", "high", cause="credentials", day="2026-10-13")
    import_alerts(sessions, layout, now=raised)
    run(sessions, layout, vault.config, raised + timedelta(minutes=5))
    assert "R20-seal-config:credentials" in active(db_session)  # nothing sealed since
    vault.seal(DAYS[2], now=raised + timedelta(minutes=20))
    run(sessions, layout, vault.config, raised + timedelta(minutes=25))
    alert = resolved(db_session, "R20-seal-config:credentials")
    assert alert.details["auto_resolved"]["reason"] == "sealed_again"


# -- verification ---------------------------------------------------------------------------------


def verification(session: Session, day: date, at: datetime, outcome: str) -> None:
    session.add(
        SealVerification(
            day=day,
            started_at=at - timedelta(seconds=5),
            finished_at=at,
            username="tolga",
            deep=False,
            outcome=outcome,
        )
    )
    session.commit()


def scheduled(layout: Layout, kind: str, at: datetime, checks: dict[date, bool]) -> None:
    write_json(
        layout.state / "verify" / f"last-{kind}.json",
        {
            "at": isoformat_site(at, "seconds"),
            "ok": all(checks.values()),
            "checks": [{"day": d.isoformat(), "ok": ok} for d, ok in checks.items()],
        },
    )


def test_the_newest_verification_of_a_day_failed(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    scheduled(layout, "2", NOW - timedelta(hours=10), {DAYS[1]: True, DAYS[2]: True})
    run(sessions, layout, config)
    assert "R20-verify" not in active(db_session)  # negative
    scheduled(layout, "90", NOW - timedelta(hours=2), {DAYS[0]: False, DAYS[1]: True})
    verification(db_session, DAYS[2], NOW - timedelta(hours=1), "failed")
    verification(db_session, DAYS[1], NOW - timedelta(hours=1), "timeout")  # inconclusive
    run(sessions, layout, config)
    alert = active(db_session)["R20-verify"]
    assert alert.severity == "critical"
    assert alert.details["failed_days"] == [DAYS[0].isoformat(), DAYS[2].isoformat()]
    # Verified again (on the sealing page, and by the next weekly run): resolved.
    verification(db_session, DAYS[2], NOW - timedelta(minutes=10), "ok")
    scheduled(layout, "90", NOW - timedelta(minutes=5), {DAYS[0]: True, DAYS[1]: True})
    run(sessions, layout, config)
    assert resolved(db_session, "R20-verify").details["auto_resolved"]["reason"] == (
        "verified_again"
    )


def test_days_deleted_by_retention_do_not_count(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    scheduled(layout, "full", NOW - timedelta(days=3), {DAYS[0]: False})
    write_json(layout.seal_state(DAYS[0]), {"day": DAYS[0].isoformat(), "status": "deleted"})
    run(sessions, layout, config)
    assert "R20-verify" not in active(db_session)


# -- Kamu SM credit -------------------------------------------------------------------------------


def credit_line(layout: Layout, credits: int | None) -> None:
    layout.credit_log.parent.mkdir(parents=True, exist_ok=True)
    entry = {"time": isoformat_site(NOW, "seconds"), "provider": "zamane", "credits": credits}
    with layout.credit_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry) + "\n")


def test_credit_below_the_threshold(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    credit_line(layout, 2000)
    run(sessions, layout, config)
    assert "R20-credit" not in active(db_session)  # negative
    credit_line(layout, 120)
    run(sessions, layout, config)
    alert = active(db_session)["R20-credit"]
    assert (alert.severity, alert.details["credits"], alert.details["threshold"]) == (
        "high",
        120,
        500,
    )
    credit_line(layout, None)  # unreadable: tells nothing, the alert stays
    run(sessions, layout, config)
    assert "R20-credit" in active(db_session)
    credit_line(layout, 5000)  # credit bought
    run(sessions, layout, config)
    assert resolved(db_session, "R20-credit").details["auto_resolved"]["reason"] == "credit_ok"


# -- replica --------------------------------------------------------------------------------------


def replica_ok(layout: Layout, day: date, at: datetime) -> None:
    write_json(
        layout.replica_state(day),
        {"day": day.isoformat(), "status": "ok", "verified_at": isoformat_site(at, "seconds")},
    )


def replica_enabled(config: Config, layout: Layout) -> Config:
    return config.model_copy(
        update={"replica": ReplicaConfig(enabled=True, target=layout.root / "nas")}
    )


def test_replica_stale(
    vault: SealedVault, sessions: sessionmaker[Session], layout: Layout, db_session: Session
) -> None:
    """A sealed day without a replica for more than 48 hours, however fresh the newest copy."""
    vault.seal_days(DAYS)  # sealed on 11, 12 and 13 October at 00:30
    enabled = replica_enabled(vault.config, layout)
    replica_ok(layout, DAYS[0], at_site(DAYS[1], 1, 0))
    replica_ok(layout, DAYS[2], at_site(DAYS[2] + timedelta(days=1), 1, 0))  # DAYS[1] fails
    run(sessions, layout, vault.config, at_site(DAYS[2] + timedelta(days=2), 1, 0))
    assert "R20-replica:stale" not in active(db_session)  # replica disabled: nothing to check
    run(sessions, layout, enabled, at_site(DAYS[1] + timedelta(days=3), 0, 0))
    assert "R20-replica:stale" not in active(db_session)  # DAYS[1] waits 47.5 hours
    later = at_site(DAYS[1] + timedelta(days=3), 1, 0)
    run(sessions, layout, enabled, later)
    alert = active(db_session)["R20-replica:stale"]
    assert (alert.rule, alert.severity, alert.details["threshold_hours"]) == (
        "R20-replica",
        "high",
        48,
    )
    assert (alert.details["missing_days"], alert.details["count"]) == ([DAYS[1].isoformat()], 1)
    assert alert.details["last_success"] == isoformat_site(
        at_site(DAYS[2] + timedelta(days=1), 1, 0), "seconds"
    )
    replica_ok(layout, DAYS[1], later + timedelta(minutes=30))
    run(sessions, layout, enabled, later + timedelta(hours=1))
    assert resolved(db_session, "R20-replica:stale").details["auto_resolved"]["reason"] == (
        "replica_ok"
    )


def test_a_copy_failure_stays_while_its_day_has_no_replica(
    vault: SealedVault, sessions: sessionmaker[Session], layout: Layout, db_session: Session
) -> None:
    """Day D fails, the next day copies fine 30 s later: the Replica task reports D's failure
    only once, so the next day's success must not resolve it."""
    vault.seal_days(DAYS)
    enabled = replica_enabled(vault.config, layout)
    failed = at_site(DAYS[2], 1, 0)  # 12 Oct: DAYS[0] fails, DAYS[1] (sealed 00:30) copies
    jsonl(layout, failed, "R20-replica", "high", day=DAYS[0].isoformat(), file="x.log.gz")
    import_alerts(sessions, layout, now=failed)
    replica_ok(layout, DAYS[1], failed + timedelta(seconds=30))
    run(sessions, layout, enabled, failed + timedelta(minutes=5))
    assert "R20-replica" in active(db_session)
    # DAYS[0] is copied at last; DAYS[2], sealed after the failure, waits for its first try.
    copied = at_site(DAYS[2] + timedelta(days=1), 1, 0)
    replica_ok(layout, DAYS[0], copied)
    run(sessions, layout, enabled, copied + timedelta(minutes=5))
    assert resolved(db_session, "R20-replica").details["auto_resolved"]["reason"] == "replica_ok"
    assert "R20-replica:stale" not in active(db_session)


def test_a_replica_switched_on_later_counts_from_the_worker_start(
    vault: SealedVault,
    sessions: sessionmaker[Session],
    layout: Layout,
    db_engine: Engine,
    db_session: Session,
) -> None:
    """Old sealed days and no replica yet: the first check after the restart opens nothing."""
    vault.seal_days(DAYS)  # sealed 59.5 hours and more before NOW
    enabled = replica_enabled(vault.config, layout)
    (job,) = [job for job in default_jobs(60) if job.name == "alerts.system"]
    worker = Worker(engine=db_engine, layout=layout, jobs=[job], clock=lambda: NOW, config=enabled)
    assert worker.tick() == [("alerts.system", "ok")]  # the worker started at NOW
    assert "R20-replica:stale" not in active(db_session)
    started = NOW

    def checks(now: datetime, start: datetime) -> None:
        run_system_checks(sessions, layout, enabled, now=now, started_at=start)

    checks(started + timedelta(hours=47), started)
    assert "R20-replica:stale" not in active(db_session)
    checks(started + timedelta(hours=49), started)
    alert = active(db_session)["R20-replica:stale"]
    assert alert.details["missing_days"] == [day.isoformat() for day in DAYS]
    assert alert.details["last_success"] is None
    # A restart alone never resolves it: the days still wait.
    restart = started + timedelta(hours=50)
    checks(restart + timedelta(minutes=5), restart)
    assert "R20-replica:stale" in active(db_session)
    for day in DAYS:
        replica_ok(layout, day, restart + timedelta(minutes=10))
    checks(restart + timedelta(minutes=15), restart)
    assert resolved(db_session, "R20-replica:stale").details["auto_resolved"]["reason"] == (
        "replica_ok"
    )


def test_replica_never_successful_and_copy_failures(
    vault: SealedVault, sessions: sessionmaker[Session], layout: Layout, db_session: Session
) -> None:
    vault.seal_days(DAYS[:1])  # sealed on 11 Oct 00:30
    enabled = vault.config.model_copy(
        update={"replica": ReplicaConfig(enabled=True, target=layout.root / "nas")}
    )
    run(sessions, layout, enabled, at_site(DAYS[0] + timedelta(days=2), 0, 0))
    assert "R20-replica:stale" not in active(db_session)  # 47.5 hours
    run(sessions, layout, enabled, NOW)
    assert active(db_session)["R20-replica:stale"].details["last_success"] is None
    # Copy failures from the Replica task: a later success resolves a high one, never a
    # critical one (the replica differs: a person looks at it).
    jsonl(layout, NOW, "R20-replica", "high", day=DAYS[0].isoformat())
    import_alerts(sessions, layout, now=NOW)
    replica_ok(layout, DAYS[0], NOW + timedelta(minutes=30))
    run(sessions, layout, enabled, NOW + timedelta(hours=1))
    assert resolved(db_session, "R20-replica").details["auto_resolved"]["reason"] == "replica_ok"
    assert "R20-replica:stale" not in active(db_session)
    jsonl(layout, NOW + timedelta(hours=2), "R20-replica", "critical", file="x.log.gz")
    import_alerts(sessions, layout, now=NOW + timedelta(hours=2))
    replica_ok(layout, DAYS[0], NOW + timedelta(hours=3))
    run(sessions, layout, enabled, NOW + timedelta(hours=4))
    assert active(db_session)["R20-replica"].severity == "critical"


# -- parse failures -------------------------------------------------------------------------------


def messages(session: Session, source: str, total: int, failed: int) -> None:
    rows = []
    for number in range(total):
        moment = NOW - timedelta(minutes=30, seconds=number % 1000)
        rows.append(
            {
                "time": moment,
                "time_received": moment,
                "source": source,
                "sender": "172.31.255.1",
                "type": "unknown" if number < failed else "traffic",
                "text": "",
                "raw_day": date(2026, 10, 13),
                "raw_line": number + 1,
                "raw_offset": number * 100,
                "parse_error": "fortigate.kv" if number < failed else None,
            }
        )
    session.execute(insert(Message), rows)
    session.commit()


def test_parse_failure_rate(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    messages(db_session, "fortigate", 1000, 10)  # exactly 1 %: not above it
    messages(db_session, "core-r1", 500, 400)  # too few messages to judge
    run(sessions, layout, config)
    assert not [key for key in active(db_session) if key.startswith("R20-parse")]
    messages(db_session, "fortigate", 1000, 30)  # 40 of 2000: 2 %
    run(sessions, layout, config)
    alert = active(db_session)["R20-parse:fortigate"]
    assert (alert.severity, alert.details["failed"], alert.details["total"]) == (
        "medium",
        40,
        2000,
    )
    later = NOW + timedelta(hours=2)  # the failures are out of the hour
    run(sessions, layout, config, later)
    assert "R20-parse:fortigate" not in active(db_session)


# -- failing jobs ---------------------------------------------------------------------------------


def job_run(session: Session, job: str, status: str, at: datetime, **details: Any) -> None:
    session.add(JobRun(job=job, started_at=at, finished_at=at, status=status, details=details))
    session.commit()


def test_a_job_that_failed_three_times_in_a_row(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    for minutes, status in ((40, "failed"), (30, "ok"), (20, "failed"), (10, "failed")):
        job_run(db_session, "leases.incremental", status, NOW - timedelta(minutes=minutes))
    for minutes in (30, 20, 10):  # a repair started by hand
        job_run(
            db_session, "leases.rebuild", "failed", NOW - timedelta(minutes=minutes), manual=True
        )
    run(sessions, layout, config)
    assert not [key for key in active(db_session) if key.startswith("R20-job")]  # negative
    job_run(db_session, "leases.incremental", "failed", NOW - timedelta(minutes=5), error="X")
    job_run(db_session, "leases.incremental", "running", NOW - timedelta(minutes=1))
    run(sessions, layout, config)
    alert = active(db_session)["R20-job:leases.incremental"]
    assert (alert.severity, alert.details["failures"], alert.details["last_error"]) == (
        "high",
        3,
        "X",
    )
    job_run(db_session, "leases.incremental", "ok", NOW)
    run(sessions, layout, config, NOW + timedelta(minutes=1))
    assert (
        resolved(db_session, "R20-job:leases.incremental").details["auto_resolved"]["reason"]
        == "job_ok"
    )


# -- database backup (state/backup/last.json, contract 4.1) ---------------------------------------


def backup(layout: Layout, *, ok: bool, finished: datetime, error: str | None = None) -> None:
    write_json(
        layout.state / "backup" / "last.json",
        {
            "schema": 1,
            "finished": isoformat_site(finished, "seconds"),
            "ok": ok,
            "file": "backups/scheduled/arcilogger-20261013-014002.dump" if ok else None,
            "size": 123456 if ok else None,
            "sha256": "0" * 64 if ok else None,
            "error": error,
        },
    )


def test_backup_missing_failed_stale_and_fine(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    run(sessions, layout, config)  # no state file and a fresh installation: nothing
    job_run(db_session, "partitions.ensure", "ok", NOW - timedelta(hours=40))
    run(sessions, layout, config)
    assert not [key for key in active(db_session) if key.startswith("R20-backup")]
    run(sessions, layout, config, NOW + timedelta(hours=9))  # installed 49 hours ago
    alert = active(db_session)["R20-backup:missing"]
    assert (alert.severity, alert.details["case"]) == ("medium", "missing")

    backup(layout, ok=False, finished=NOW, error="pg_dump: connection refused")
    run(sessions, layout, config, NOW + timedelta(hours=10))
    alerts = active(db_session)
    assert "R20-backup:missing" not in alerts
    assert (alerts["R20-backup"].severity, alerts["R20-backup"].details["case"]) == (
        "high",
        "failed",
    )
    backup(layout, ok=True, finished=NOW + timedelta(hours=11))
    run(sessions, layout, config, NOW + timedelta(hours=12))
    assert resolved(db_session, "R20-backup").details["auto_resolved"]["reason"] == "backup_ok"
    run(sessions, layout, config, NOW + timedelta(hours=48))  # 37 hours without a new one
    assert active(db_session)["R20-backup"].details["case"] == "stale"
    (layout.state / "backup" / "last.json").write_text("{not json", encoding="utf-8")
    run(sessions, layout, config, NOW + timedelta(hours=49))
    assert active(db_session)["R20-backup"].details["case"] == "unreadable"


def test_backup_stale_hours_is_a_setting(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    backup(layout, ok=True, finished=NOW - timedelta(hours=30))
    set_setting(db_session, "alerts.backup_stale_hours", 24, updated_by="tolga")
    db_session.commit()
    run(sessions, layout, config)
    assert active(db_session)["R20-backup"].details["case"] == "stale"


def test_a_backup_failure_raised_by_the_backup_task_is_one_alert(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    """The backup task writes last.json and raises R20-backup (alerts.jsonl): one alert."""
    backup(layout, ok=False, finished=NOW, error="disk full")
    jsonl(layout, NOW, "R20-backup", "high", error="disk full")
    import_alerts(sessions, layout, now=NOW)
    report = run(sessions, layout, config)
    assert report.checks["backup"]["opened"] == []
    assert len([a for a in active(db_session) if a.startswith("R20-backup")]) == 1


def test_a_dump_whose_state_file_was_not_written_stays_until_a_later_backup(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    """last.json still shows yesterday's good backup, fresh enough: that does not end it."""
    backup(layout, ok=True, finished=NOW - timedelta(hours=24))
    error = "the dump was made but last.json was not written: Access is denied"
    jsonl(layout, NOW - timedelta(hours=1), "R20-backup", "high", error=error, file="x.dump")
    import_alerts(sessions, layout, now=NOW - timedelta(minutes=59))
    run(sessions, layout, config)
    assert active(db_session)["R20-backup"].severity == "high"
    tomorrow = NOW + timedelta(hours=23)
    backup(layout, ok=True, finished=tomorrow)
    run(sessions, layout, config, tomorrow + timedelta(minutes=5))
    assert resolved(db_session, "R20-backup").details["auto_resolved"]["reason"] == "backup_ok"


def test_a_retention_failure_stays_until_a_later_backup_raised_it_no_more(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    """Old dumps that cannot be deleted: the dump itself is fine every night."""

    def night(at: datetime, *, prune_fails: bool) -> None:
        backup(layout, ok=True, finished=at)
        if prune_fails:
            jsonl(
                layout,
                at + timedelta(seconds=5),
                "R20-backup",
                "medium",
                key="prune",
                error="retention failed: Access is denied",
                dir="backups/scheduled",
            )

    tonight = NOW - timedelta(hours=2)
    night(tonight, prune_fails=True)
    import_alerts(sessions, layout, now=tonight + timedelta(minutes=1))
    run(sessions, layout, config)
    alert = active(db_session)["R20-backup:prune"]  # not resolved by the good backup
    assert (alert.rule, alert.severity) == ("R20-backup", "medium")
    assert "R20-backup" not in active(db_session)
    tomorrow = tonight + timedelta(days=1)
    night(tomorrow, prune_fails=True)
    run(sessions, layout, config, tomorrow + timedelta(seconds=30))  # its failure not imported
    assert "R20-backup:prune" in active(db_session)
    import_alerts(sessions, layout, now=tomorrow + timedelta(minutes=1))
    run(sessions, layout, config, tomorrow + timedelta(hours=2))
    assert active(db_session)["R20-backup:prune"].details["repeats"] == 1
    after = tomorrow + timedelta(days=1)
    night(after, prune_fails=False)
    run(sessions, layout, config, after + timedelta(minutes=30))
    assert "R20-backup:prune" in active(db_session)  # its retention may still be reported
    run(sessions, layout, config, after + timedelta(hours=1))
    assert resolved(db_session, "R20-backup:prune").details["auto_resolved"]["reason"] == "pruned"


def test_no_backup_alert_while_backups_are_disabled(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    backup(layout, ok=True, finished=NOW - timedelta(hours=40))
    run(sessions, layout, config)
    assert active(db_session)["R20-backup"].details["case"] == "stale"  # negative
    disabled = config.model_copy(update={"backup": BackupConfig(enabled=False)})
    run(sessions, layout, disabled, NOW + timedelta(minutes=5))
    alert = resolved(db_session, "R20-backup")
    assert alert.details["auto_resolved"]["reason"] == "backup_disabled"
    (layout.state / "backup" / "last.json").unlink()
    job_run(db_session, "partitions.ensure", "ok", NOW - timedelta(days=30))
    run(sessions, layout, disabled, NOW + timedelta(days=5))
    assert not [key for key in active(db_session) if key.startswith("R20-backup")]


def test_a_dump_named_before_an_older_one_is_resolved_by_a_later_backup(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    """A clock set back names the new dump earlier than one already there (the dump is kept)."""
    backup(layout, ok=True, finished=NOW - timedelta(minutes=10))
    jsonl(layout, NOW - timedelta(minutes=9), "R20-backup", "medium", later="x.dump", key="clock")
    import_alerts(sessions, layout, now=NOW - timedelta(minutes=8))
    run(sessions, layout, config)
    assert active(db_session)["R20-backup:clock"].severity == "medium"  # the same night: stays
    tomorrow = NOW + timedelta(hours=23)
    backup(layout, ok=True, finished=tomorrow)
    run(sessions, layout, config, tomorrow + timedelta(minutes=5))
    alert = resolved(db_session, "R20-backup:clock")
    assert alert.details["auto_resolved"]["reason"] == "backup_ok"


# -- clock ----------------------------------------------------------------------------------------


def clock_sample(layout: Layout, at: datetime, offset_us: int, ok: bool = True) -> None:
    path = layout.clock_samples(site_day(at))
    path.parent.mkdir(parents=True, exist_ok=True)
    sample = {"time": isoformat_site(at, "seconds"), "ok": ok, "offset_us": offset_us}
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(sample) + "\n")


def test_a_clock_alert_clears_with_a_good_sample(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    jsonl(layout, NOW - timedelta(hours=1), "R20-clock", "high", offset_us=1_500_000)
    import_alerts(sessions, layout, now=NOW)
    clock_sample(layout, NOW - timedelta(minutes=30), 2_000_000)  # still off
    run(sessions, layout, config)
    assert "R20-clock" in active(db_session)
    clock_sample(layout, NOW - timedelta(minutes=10), 3_000)
    run(sessions, layout, config)
    assert resolved(db_session, "R20-clock").details["auto_resolved"]["reason"] == "clock_ok"


def test_a_clock_step_seen_by_sealing_is_left_to_a_person(
    sessions: sessionmaker[Session], layout: Layout, config: Config, db_session: Session
) -> None:
    """Sealing's clock alerts carry a key (a stray spool file, a clock behind the seals): a good
    clock sample says nothing about that day's files."""
    key = "clock-behind:spool:2026-10-13"
    jsonl(layout, NOW - timedelta(hours=1), "R20-clock", "critical", key=key)
    jsonl(layout, NOW - timedelta(hours=1), "R20-clock", "high", offset_us=1_500_000)
    import_alerts(sessions, layout, now=NOW)
    clock_sample(layout, NOW - timedelta(minutes=10), 3_000)
    run(sessions, layout, config)
    assert resolved(db_session, "R20-clock").details["auto_resolved"]["reason"] == "clock_ok"
    assert active(db_session)[f"R20-clock:{key}"].severity == "critical"


# -- the job --------------------------------------------------------------------------------------


def test_a_failing_check_does_not_stop_the_others(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(ctx: system.CheckContext) -> system.Outcome:
        raise OSError("state folder unreadable")

    checks = tuple((name, broken if name == "verify" else check) for name, check in system.CHECKS)
    monkeypatch.setattr(system, "CHECKS", checks)
    credit_line(layout, 10)
    with pytest.raises(CheckError, match="verify: OSError: state folder unreadable") as caught:
        run(sessions, layout, config)
    assert "credit" in caught.value.report.checks
    assert "R20-credit" in active(db_session)


def test_the_worker_runs_the_checks_every_5_minutes(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    (job,) = [job for job in default_jobs(60) if job.name == "alerts.system"]
    assert job.every == timedelta(minutes=5)
    credit_line(layout, 10)
    worker = Worker(engine=db_engine, layout=layout, jobs=[job], clock=lambda: NOW)
    assert worker.tick() == [("alerts.system", "ok")]
    assert "R20-credit" in active(db_session)
