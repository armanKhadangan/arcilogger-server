"""The worker service loop: due jobs, job_runs rows, daily jobs once per site day (also across
restarts), failures and database outages never stop the loop, status file, stop event."""

from __future__ import annotations

import itertools
import json
import threading
from collections.abc import Callable, Mapping
from datetime import date, datetime, time, timedelta
from time import monotonic
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import JobRun, Lease
from arcilogger_worker.service import (
    MIN_SLEEP_SECONDS,
    SKIPPED,
    SLEEP_SECONDS,
    Job,
    JobContext,
    Worker,
    default_jobs,
    retry_delay,
)
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)


class Clock:
    def __init__(self, start: datetime) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs: float) -> None:
        self.now += timedelta(**kwargs)


def recorder(
    calls: list[str], name: str, result: Mapping[str, Any] | None = None
) -> Callable[[JobContext], Mapping[str, Any]]:
    def job(ctx: JobContext) -> Mapping[str, Any]:
        calls.append(name)
        return result or {"at": ctx.now.isoformat()}

    return job


def runs(session: Session) -> list[tuple[str, str]]:
    session.expire_all()
    return [(r.job, r.status) for r in session.scalars(select(JobRun).order_by(JobRun.id)).all()]


def test_interval_and_daily_jobs(db_engine: Engine, db_session: Session, layout: Layout) -> None:
    calls: list[str] = []

    def broken(ctx: JobContext) -> Mapping[str, Any]:
        raise RuntimeError("boom")

    clock = Clock(tr(DAY, 0, 1))
    jobs = [
        Job("every-minute", recorder(calls, "every-minute"), every=timedelta(minutes=1)),
        Job("daily", recorder(calls, "daily"), daily_after=time(0, 5)),
        Job("broken", broken, every=timedelta(minutes=10)),
        Job(
            "hook", recorder(calls, "hook", {SKIPPED: True, "reason": "later"}), daily_after=time(0)
        ),
    ]
    worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=clock)
    assert worker.tick() == [("every-minute", "ok"), ("broken", "failed"), ("hook", "skipped")]
    assert worker.states["daily"].next_due == tr(DAY, 0, 5)  # not before 00:05 site time
    clock.advance(seconds=30)
    assert worker.tick() == []  # nothing due yet
    clock.advance(minutes=5)
    assert worker.tick() == [("every-minute", "ok"), ("daily", "ok")]
    clock.advance(minutes=1)
    assert worker.tick() == [("every-minute", "ok")]  # the daily job ran today already
    assert calls.count("daily") == 1

    rows = runs(db_session)
    assert ("broken", "failed") in rows
    assert ("hook", "skipped") in rows
    failed = db_session.scalars(select(JobRun).where(JobRun.job == "broken")).one()
    assert failed.details["error"] == "RuntimeError"
    assert failed.details["message"] == "boom"
    assert failed.finished_at is not None
    hook = db_session.scalars(select(JobRun).where(JobRun.job == "hook")).one()
    assert hook.details == {"reason": "later", "duration_ms": 0}
    assert worker.states["broken"].last_error == "RuntimeError: boom"

    # A restart the same day: the daily jobs are not repeated (history from job_runs) …
    again = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=clock)
    assert again.tick() == [("every-minute", "ok"), ("broken", "failed")]
    # … but the next site day they are due again after their time.
    clock.now = tr(DAY + timedelta(days=1), 0, 4)
    assert [name for name, _ in again.tick()] == ["every-minute", "broken", "hook"]
    clock.now = tr(DAY + timedelta(days=1), 0, 6)
    assert [name for name, _ in again.tick()] == ["every-minute", "daily"]


def test_status_file_and_stop(db_engine: Engine, layout: Layout) -> None:
    calls: list[str] = []
    clock = Clock(tr(DAY, 12))
    worker = Worker(
        engine=db_engine,
        layout=layout,
        jobs=[Job("job", recorder(calls, "job"), every=timedelta(hours=1))],
        clock=clock,
    )
    stop = threading.Event()
    thread = threading.Thread(target=worker.run, args=(stop,))
    thread.start()
    for _ in range(200):
        if calls:
            break
        threading.Event().wait(0.02)
    stop.set()
    thread.join(timeout=10)
    assert not thread.is_alive()
    status = json.loads(worker.status_path.read_text(encoding="utf-8"))
    assert status["state"] == "stopped"
    assert status["database"] == "ok"
    assert status["jobs"]["job"]["last_status"] == "ok"
    assert status["jobs"]["job"]["next_due"] == (tr(DAY, 13)).isoformat()
    assert worker.status_path == layout.state / "worker-status.json"
    # Canlı Durum's snapshot ran beside the loop, in its own thread, and stopped with it.
    assert not any(t.name == "activity.tick" for t in threading.enumerate())
    tick = status["jobs"]["activity.tick"]
    assert tick["thread"] is True
    assert tick["runs"] >= 1
    assert tick["last_status"] in {"ok", "skipped"}


def test_the_loop_waits_until_the_next_job_is_due(db_engine: Engine, layout: Layout) -> None:
    clock = Clock(tr(DAY, 12))
    worker = Worker(
        engine=db_engine,
        layout=layout,
        jobs=[
            Job("often", recorder([], "often"), every=timedelta(seconds=5)),
            Job("hourly", recorder([], "hourly"), every=timedelta(hours=1)),
        ],
        clock=clock,
        activity=False,
    )
    assert worker.pause(clock()) == SLEEP_SECONDS  # nothing has ticked yet
    assert [name for name, _ in worker.tick()] == ["often", "hourly"]
    assert worker.pause(clock()) == SLEEP_SECONDS  # "often" is due in 5 s: at most a full wait
    assert worker.pause(tr(DAY, 12) + timedelta(seconds=4.7)) == pytest.approx(0.3)
    assert worker.pause(tr(DAY, 12) + timedelta(seconds=5)) == MIN_SLEEP_SECONDS  # due now
    assert worker.pause(tr(DAY, 12) + timedelta(seconds=9)) == MIN_SLEEP_SECONDS  # overdue
    # While the database is unreachable the history is not read and next_due is stale.
    worker.database_ok = False
    assert worker.pause(tr(DAY, 12) + timedelta(seconds=4.7)) == SLEEP_SECONDS


def test_a_job_starts_when_it_is_due_not_a_whole_wait_later(
    db_engine: Engine, layout: Layout
) -> None:
    """A job due every 0.4 s runs about every 0.4 s: the loop waits until the job is due, not
    :data:`SLEEP_SECONDS` after every tick, whose own time then added to the delay (the probe
    of the activity guard, #201, was more than 2 s late on a busy Windows runner)."""
    starts: list[float] = []

    def probe(ctx: JobContext) -> Mapping[str, Any]:
        starts.append(monotonic())
        return {}

    worker = Worker(
        engine=db_engine,
        layout=layout,
        jobs=[Job("probe", probe, every=timedelta(seconds=0.4))],
        activity=False,
    )
    stop = threading.Event()
    thread = threading.Thread(target=worker.run, args=(stop,))
    thread.start()
    try:
        for _ in range(500):
            if len(starts) >= 9:
                break
            threading.Event().wait(0.02)
    finally:
        stop.set()
        thread.join(timeout=10)
    assert not thread.is_alive()
    intervals = sorted(b - a for a, b in itertools.pairwise(starts))
    assert len(intervals) >= 8
    # Each interval was at least a whole wait before (> 1 s); now the median is near 0.4 s.
    assert intervals[len(intervals) // 2] < 0.8 < SLEEP_SECONDS


def test_database_outage_does_not_stop_the_loop(layout: Layout) -> None:
    engine = engine_from_url("postgresql://nobody:nothing@127.0.0.1:1/none")
    calls: list[str] = []
    worker = Worker(
        engine=engine,
        layout=layout,
        jobs=[Job("job", recorder(calls, "job"), every=timedelta(minutes=1))],
        clock=Clock(tr(DAY, 12)),
    )
    assert worker.tick() == []
    assert worker.database_ok is False
    worker.write_status()
    status = json.loads(worker.status_path.read_text(encoding="utf-8"))
    assert status["database"] == "unreachable"
    assert calls == []
    engine.dispose()


def test_job_definitions_are_checked() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        Job("bad", recorder([], "bad"))
    with pytest.raises(ValueError, match="exactly one"):
        Job("bad", recorder([], "bad"), every=timedelta(1), daily_after=time(1))
    with pytest.raises(ValueError, match="interval job"):
        Job("bad", recorder([], "bad"), daily_after=time(1), idle=lambda ctx: True)


def test_an_idle_job_neither_runs_nor_writes_a_row(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    """A due job whose ``idle`` check is true does nothing and is due again ``every`` later;
    a failing check runs the job."""
    calls: list[str] = []
    state: dict[str, bool | None] = {"idle": True}

    def idle(ctx: JobContext) -> bool:
        if state["idle"] is None:
            raise RuntimeError("cannot tell")
        return bool(state["idle"])

    clock = Clock(tr(DAY, 9))
    jobs = [Job("poll", recorder(calls, "poll"), every=timedelta(seconds=5), idle=idle)]
    worker = Worker(engine=db_engine, layout=layout, jobs=jobs, clock=clock)
    assert worker.tick() == []
    assert worker.states["poll"].next_due == tr(DAY, 9) + timedelta(seconds=5)
    clock.advance(seconds=2)
    state["idle"] = False
    assert worker.tick() == []  # not due before its interval
    clock.advance(seconds=3)
    assert worker.tick() == [("poll", "ok")]
    clock.advance(seconds=5)
    state["idle"] = None
    assert worker.tick() == [("poll", "ok")]
    assert calls == ["poll", "poll"]
    assert runs(db_session) == [("poll", "ok"), ("poll", "ok")]
    assert worker.states["poll"].last_started == clock.now


def test_default_jobs_run_against_the_database(
    db_engine: Engine, db_session: Session, layout: Layout, evidence: Evidence
) -> None:
    evidence.dhcp(tr(DAY, 8), "assigned", "172.20.43.120", "f0:1f:af:00:00:01")
    db_session.commit()
    clock = Clock(tr(DAY, 3, 30))
    worker = Worker(engine=db_engine, layout=layout, jobs=default_jobs(60), clock=clock)
    assert worker.tick() == [
        ("partitions.ensure", "ok"),
        ("license.check", "ok"),
        ("leases.incremental", "ok"),
        ("leases.nightly", "ok"),
        ("leases.recheck_today", "ok"),
        ("leases.late_evidence", "ok"),
        ("evidence.purge", "ok"),
        ("audit.import", "ok"),
        ("admin.requests", "skipped"),  # no test e-mail or lease rebuild requested
        ("alerts.import", "ok"),
        ("alerts.r15", "ok"),
        ("alerts.system", "ok"),
        ("rules.run", "ok"),  # the registered rules start at the horizon (D12)
        ("alerts.notify", "skipped"),  # [smtp] enabled = false by default
        ("alerts.webhooks", "skipped"),  # no [[webhooks]] by default
        ("usage.requests", "skipped"),  # no rebuild request
        ("usage.daily", "ok"),
        ("usage.today", "ok"),
        ("usage.reattribute", "skipped"),  # nothing changed
        ("usage.retention", "ok"),
        ("db.window", "ok"),  # after 01:00; nothing sealed yet, so only the partitions
        ("reports.run_due", "ok"),
        ("reports.purge", "ok"),
        ("system.sample", "ok"),
        # snmp.poll is idle: no source has SNMP on, so it neither runs nor writes a row
        ("search.counts_backfill", "skipped"),  # no file loaded before the summary table
    ]
    clock.now = tr(DAY, 9)
    assert worker.tick() == [
        ("license.check", "ok"),
        ("leases.incremental", "ok"),
        ("leases.recheck_today", "ok"),
        ("leases.late_evidence", "ok"),
        ("audit.import", "ok"),
        ("admin.requests", "skipped"),
        ("alerts.import", "ok"),
        ("alerts.r15", "ok"),
        ("alerts.system", "ok"),
        ("rules.run", "ok"),
        ("alerts.notify", "skipped"),
        ("alerts.webhooks", "skipped"),
        ("usage.requests", "skipped"),
        ("usage.today", "ok"),
        ("usage.reattribute", "skipped"),
        ("reports.run_due", "ok"),
        ("search.counts_backfill", "skipped"),
    ]
    assert not any(job == "snmp.poll" for job, _ in runs(db_session))
    lease = db_session.scalars(select(Lease)).one()
    assert lease.start == tr(DAY, 8)
    incremental = db_session.scalars(
        select(JobRun).where(JobRun.job == "leases.incremental").order_by(JobRun.id.desc())
    ).first()
    assert incremental is not None
    assert incremental.details["mode"] == "incremental"
    assert incremental.details["inserted"] == 1
    partitions = db_session.scalars(select(JobRun).where(JobRun.job == "partitions.ensure")).one()
    assert set(partitions.details) == {"tables", "created", "moved", "duration_ms"}
    sample = db_session.scalars(select(JobRun).where(JobRun.job == "system.sample")).one()
    assert sample.details["day"] == DAY.isoformat()
    history = json.loads(layout.system_history.read_text(encoding="utf-8"))
    assert [entry["day"] for entry in history["days"]] == [DAY.isoformat()]


def test_a_failing_daily_job_is_retried_with_a_growing_delay(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    """Not on every 1-second tick: 15 min, 30 min, 1 h … after each failure; success resets."""
    calls: list[datetime] = []
    failing = [True]

    def nightly(ctx: JobContext) -> Mapping[str, Any]:
        calls.append(ctx.now)
        if failing[0]:
            raise ValueError("lease timeouts must be positive")
        return {}

    clock = Clock(tr(DAY, 0, 6))
    job = Job("leases.nightly", nightly, daily_after=time(0, 5))
    worker = Worker(engine=db_engine, layout=layout, jobs=[job], clock=clock)
    for _ in range(60):  # one minute of the service loop
        worker.tick()
        clock.advance(seconds=1)
    assert calls == [tr(DAY, 0, 6)]
    state = worker.states["leases.nightly"]
    assert (state.failures, state.retry_at) == (1, tr(DAY, 0, 21))
    assert state.next_due == tr(DAY, 0, 21)

    clock.now = tr(DAY, 0, 21)
    assert worker.tick() == [("leases.nightly", "failed")]
    assert worker.states["leases.nightly"].retry_at == tr(DAY, 0, 51)  # 30 min
    rows = db_session.scalars(select(JobRun).where(JobRun.job == "leases.nightly")).all()
    assert len(rows) == 2
    assert rows[-1].details["failures"] == 2
    worker.write_status()
    status = json.loads(worker.status_path.read_text(encoding="utf-8"))
    assert status["jobs"]["leases.nightly"]["failures"] == 2
    assert status["jobs"]["leases.nightly"]["retry_at"] == tr(DAY, 0, 51).isoformat()

    failing[0] = False  # e.g. the administrator fixed the setting
    clock.now = tr(DAY, 0, 51)
    assert worker.tick() == [("leases.nightly", "ok")]
    state = worker.states["leases.nightly"]
    assert (state.failures, state.retry_at, state.done_day) == (0, None, DAY)
    clock.advance(minutes=30)
    assert worker.tick() == []  # done for today


def test_retry_delays() -> None:
    daily = Job("d", recorder([], "d"), daily_after=time(0, 5))
    minute = Job("m", recorder([], "m"), every=timedelta(minutes=1))
    hourly = Job("h", recorder([], "h"), every=timedelta(hours=1))
    assert [retry_delay(daily, n) for n in (1, 2, 3, 4, 5, 6, 50)] == [
        timedelta(minutes=15),
        timedelta(minutes=30),
        timedelta(hours=1),
        timedelta(hours=2),
        timedelta(hours=4),
        timedelta(hours=4),
        timedelta(hours=4),
    ]
    assert [retry_delay(minute, n) for n in (1, 2, 3, 6, 50)] == [
        timedelta(minutes=1),
        timedelta(minutes=2),
        timedelta(minutes=4),
        timedelta(minutes=30),
        timedelta(minutes=30),
    ]
    assert retry_delay(hourly, 5) == timedelta(hours=1)  # never shorter than the interval
