"""The ``ArciWorker`` service loop: a small scheduler without third-party dependencies.

Jobs (``DEFAULT_JOBS``):

==========================  =============================================================
``leases.incremental``      every ``[worker] interval_seconds`` (60 s): new DHCP/ARP/traffic
                            evidence into the lease timeline (catches up after downtime)
``leases.recheck_today``    hourly: full rebuild of today (late-arriving evidence)
``leases.late_evidence``    every 10 minutes: rebuild older days whose evidence arrived
                            after their rebuild (ingest backlog; ``correlation.late``)
``leases.nightly``          daily after 00:05 (site time): rebuild yesterday and today
``partitions.ensure``       daily after 00:15 and at start-up: monthly partitions ahead
                            (``arcilogger_db.partitions``; no-op with TimescaleDB)
``license.check``           every 5 minutes: the licence state recomputed; state changes and
                            limit crossings audited once, the ``R20-license`` alerts opened
                            and resolved (:mod:`.alerts.license`),
                            ``state/license-status.json`` rewritten (:mod:`.licensing`)
``evidence.purge``          daily after 03:00: expired evidence packages are deleted from
                            ``exports/`` and their rows marked (``evidence.keep_days``),
                            stale builds failed; audited (:mod:`.evidence_jobs`)
``audit.import``            every 15 minutes: scheduled sealing and retention runs
                            (``state/seal``, ``vault/deletions``, ``state/alerts.jsonl``)
                            into the audit log, each event once (``seal.run``,
                            ``retention.run``)
``alerts.import``           every minute: new ``state/alerts.jsonl`` lines into ``alerts``,
                            each once (:mod:`.alerts.importer`)
``alerts.r15``              every minute: R15 log source silent (:mod:`.alerts.r15`)
``alerts.system``           every 5 minutes: the R20 system checks (:mod:`.alerts.system`)
``alerts.notify``           every minute: alert e-mail through ``[smtp]``
                            (:mod:`.alerts.notify`)
``usage.today``             every 5 minutes: today's usage rollup (:mod:`.usage.jobs`)
``usage.daily``             daily after 00:45: yesterday's usage rollup, final
``usage.reattribute``       every 10 minutes: recent days whose inputs or device owners
                            changed are rebuilt (``usage.auto_rebuild_days``)
``usage.requests``          every 30 seconds: rebuild requests of the web interface
``usage.retention``         daily after 03:30: rollups older than ``usage.retention_days``
``reports.run_due``         every minute: due report schedules queued (one run per
                            schedule after downtime), queued runs rendered (HTML, PDF,
                            Excel) and e-mailed per recipient (:mod:`.reports.runner`)
``reports.purge``           daily after 03:20: files of report runs older than
                            ``reports.keep_days`` deleted from ``exports/usage-reports``
==========================  =============================================================

A daily job runs once per site day (Europe/Istanbul) as soon as its time has passed — also
right after a start-up if that day's run is missing (the last successful run is read from
``job_runs``). Every run is a ``job_runs`` row (``running`` → ``ok`` / ``failed`` /
``skipped`` with JSON details) and the loop writes ``state/worker-status.json`` for the
dashboard. A failing job never stops the loop; it is retried with a growing delay
(:func:`retry_delay`: daily jobs after 15 min, 30 min, … at most 4 h; interval jobs after
their interval, doubling up to 30 min), so a job that fails every time does not run back to
back. While the database is unreachable a job is retried after :data:`DATABASE_RETRY`.
:meth:`Worker.run` returns when the stop event is set (Ctrl+C / service stop / SIGTERM).

**Licence gates** (licensing contract 7): a job of a paid module (``Job.module``: the ``usage.*``
rollups → ``usage_analytics``, ``reports.run_due`` → ``usage_reports``) asks
:func:`.licensing.module_gate` before its ``running`` row is written. While the module is off
the job does not run: its first skip of a site day is one ``skipped`` row with the gate's details
(for a daily job the run that counts as done), later ones only move ``next_due``; one INFO line
per job when its gate changes (the first evaluation after the start counts as a change). When
``reports.run_due`` is locked an active ``R20-report`` is resolved. Every other job — 5651 Temel
and the data-minimisation jobs ``usage.retention`` / ``reports.purge`` — never asks.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Engine, func, select, update
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ, site_day, to_site, utc_now
from arcilogger_db.engine import session_factory
from arcilogger_db.models import JobRun
from arcilogger_db.partitions import ensure_partitions
from arcilogger_worker import __version__
from arcilogger_worker.alerts.importer import import_alerts
from arcilogger_worker.alerts.notify import run_notify
from arcilogger_worker.alerts.r15 import check_silent_sources
from arcilogger_worker.alerts.system import run_system_checks
from arcilogger_worker.correlation.leases import (
    rebuild_days,
    run_incremental,
    run_late_evidence,
)
from arcilogger_worker.evidence_jobs import import_job_events, purge_evidence
from arcilogger_worker.licensing import StatusCache, job_license_check, module_gate
from arcilogger_worker.reports.runner import purge_reports, resolve_while_unlicensed, run_due
from arcilogger_worker.usage import jobs as usage_jobs

log = logging.getLogger("arcilogger_worker")

STATUS_FILE: Final = "worker-status.json"
#: The loop checks for due jobs and stop requests this often.
SLEEP_SECONDS: Final = 1.0
#: The status file is rewritten at least this often (and after every job run).
STATUS_EVERY: Final = timedelta(seconds=15)
#: First retry of a failed daily job; doubles per failure up to :data:`DAILY_RETRY_MAX`.
DAILY_RETRY_FIRST: Final = timedelta(minutes=15)
DAILY_RETRY_MAX: Final = timedelta(hours=4)
#: Longest retry delay of a failing interval job (its interval doubles per failure up to this).
INTERVAL_RETRY_MAX: Final = timedelta(minutes=30)
#: Retry delay while the database is unreachable (no ``job_runs`` row can be written).
DATABASE_RETRY: Final = timedelta(minutes=1)


@dataclass(frozen=True)
class JobContext:
    engine: Engine
    sessions: sessionmaker[Session]
    now: datetime
    #: The data directory (``exports/``, ``state/``, read-only ``vault/``).
    layout: Layout
    #: ``arcilogger.toml`` (``None`` in tests that build a worker without one: the defaults).
    config: Config | None = None
    #: When the worker loop started (the R15 start-up grace period).
    started_at: datetime | None = None
    #: The worker's licence status for the gates (kept 60 s; ``None``: computed every time).
    license_cache: StatusCache | None = None


JobFunc = Callable[[JobContext], Mapping[str, Any]]


@dataclass(frozen=True)
class Job:
    name: str
    func: JobFunc
    #: Interval job: run every ``every``.
    every: timedelta | None = None
    #: Daily job: once per site day, after this local (Europe/Istanbul) time.
    daily_after: time | None = None
    #: Paid module the job belongs to (licensing contract 1.4): skipped while it is off.
    module: str | None = None

    def __post_init__(self) -> None:
        if (self.every is None) == (self.daily_after is None):
            raise ValueError(f"job {self.name}: give exactly one of every / daily_after")

    @property
    def daily_time(self) -> time:
        if self.daily_after is None:
            raise ValueError(f"job {self.name} is not a daily job")
        return self.daily_after


def retry_delay(job: Job, failures: int) -> timedelta:
    """Delay before the next attempt after ``failures`` failed runs in a row (``>= 1``)."""
    exponent = min(max(failures, 1) - 1, 16)
    if job.every is None:
        return min(DAILY_RETRY_FIRST * (1 << exponent), DAILY_RETRY_MAX)
    return min(job.every * (1 << exponent), max(job.every, INTERVAL_RETRY_MAX))


#: Details key a job may set to record its run as ``skipped`` instead of ``ok``.
SKIPPED: Final = "_skipped"
#: How often ``leases.late_evidence`` compares the ingest progress with the rebuilt days.
LATE_EVIDENCE_EVERY: Final = timedelta(minutes=10)
#: How often ``audit.import`` looks for new sealing and retention events.
AUDIT_IMPORT_EVERY: Final = timedelta(minutes=15)
#: ``alerts.import``, ``alerts.r15`` and ``alerts.notify`` run every minute, the R20 checks
#: every 5 minutes (they read more files).
ALERTS_EVERY: Final = timedelta(minutes=1)
ALERTS_SYSTEM_EVERY: Final = timedelta(minutes=5)
#: Area U (spec 10.4): today's usage every 5 minutes, re-attribution every 10, requests every 30 s.
USAGE_TODAY_EVERY: Final = timedelta(minutes=5)
USAGE_REATTRIBUTE_EVERY: Final = timedelta(minutes=10)
USAGE_REQUESTS_EVERY: Final = timedelta(seconds=30)
#: ``reports.run_due`` looks for due schedules and queued runs every minute.
REPORTS_EVERY: Final = timedelta(minutes=1)
#: ``license.check`` recomputes the licence state every 5 minutes (contract 7).
LICENSE_EVERY: Final = timedelta(minutes=5)
#: The modules of the paid jobs (licensing contract 1.4).
USAGE_MODULE: Final = "usage_analytics"
REPORTS_MODULE: Final = "usage_reports"


# -- the jobs -------------------------------------------------------------------------------------


def job_leases_incremental(ctx: JobContext) -> Mapping[str, Any]:
    with ctx.sessions() as session:
        report = run_incremental(session, now=ctx.now, layout=ctx.layout)
        session.commit()
        return report.details()


def job_leases_recheck_today(ctx: JobContext) -> Mapping[str, Any]:
    today = site_day(ctx.now)
    with ctx.sessions() as session:
        return rebuild_days(session, today, today, now=ctx.now).details()


def job_leases_late_evidence(ctx: JobContext) -> Mapping[str, Any]:
    with ctx.sessions() as session:
        report = run_late_evidence(session, now=ctx.now)
        session.commit()
        return report.details()


def job_leases_nightly(ctx: JobContext) -> Mapping[str, Any]:
    today = site_day(ctx.now)
    with ctx.sessions() as session:
        return rebuild_days(session, today - timedelta(days=1), today, now=ctx.now).details()


def job_partitions(ctx: JobContext) -> Mapping[str, Any]:
    with ctx.engine.begin() as connection:
        report = ensure_partitions(connection, today=site_day(ctx.now))
    return {"tables": len(report.tables), "created": report.created, "moved": report.moved}


def job_evidence_purge(ctx: JobContext) -> Mapping[str, Any]:
    """Expired evidence packages (``evidence.keep_days``) out of ``exports/``; rows reconciled."""
    with ctx.sessions() as session:
        return purge_evidence(session, ctx.layout, now=ctx.now).details()


def job_audit_import(ctx: JobContext) -> Mapping[str, Any]:
    """Scheduled sealing and retention runs into the audit log (each event once)."""
    return import_job_events(ctx.sessions, ctx.layout, now=ctx.now).details()


def job_config(ctx: JobContext) -> Config:
    """The worker's configuration (the defaults for the data directory when it has none)."""
    if ctx.config is not None:
        return ctx.config
    return Config(data_dir=ctx.layout.root, secrets_dir=ctx.layout.root)


def job_alerts_import(ctx: JobContext) -> Mapping[str, Any]:
    """New ``state/alerts.jsonl`` lines into ``alerts`` (each line once)."""
    return import_alerts(ctx.sessions, ctx.layout, now=ctx.now).details()


def job_alerts_r15(ctx: JobContext) -> Mapping[str, Any]:
    """R15: open or resolve the "log source silent" alerts."""
    with ctx.sessions() as session:
        report = check_silent_sources(
            session, ctx.layout, now=ctx.now, started_at=ctx.started_at or ctx.now
        )
        session.commit()
        return report.details()


def job_alerts_system(ctx: JobContext) -> Mapping[str, Any]:
    """R20: disk, sealing, verification, credit, replica, parsing, jobs, backup, clock."""
    return run_system_checks(
        ctx.sessions, ctx.layout, job_config(ctx), now=ctx.now, started_at=ctx.started_at
    ).details()


def job_alerts_notify(ctx: JobContext) -> Mapping[str, Any]:
    """Alert e-mail (skipped while ``[smtp] enabled`` is false)."""
    return run_notify(ctx.sessions, job_config(ctx), now=ctx.now).details()


def job_usage_today(ctx: JobContext) -> Mapping[str, Any]:
    """Today's usage rollup (partial) from the logs loaded so far."""
    return usage_jobs.refresh_today(ctx.sessions, now=ctx.now)


def job_usage_daily(ctx: JobContext) -> Mapping[str, Any]:
    """Yesterday's usage rollup, final."""
    return usage_jobs.finalize_yesterday(ctx.sessions, now=ctx.now)


def job_usage_reattribute(ctx: JobContext) -> Mapping[str, Any]:
    """Recent days whose device owners, leases, late logs or settings changed."""
    report = usage_jobs.reattribute(ctx.sessions, now=ctx.now)
    details = report.details()
    if not report.days and not report.more:
        details[SKIPPED] = True
    return details


def job_usage_requests(ctx: JobContext) -> Mapping[str, Any]:
    """Rebuild requests from the web interface (``usage_rebuilds``)."""
    report = usage_jobs.process_requests(ctx.sessions, now=ctx.now)
    details = report.details()
    if not report.days and not report.more:
        details[SKIPPED] = True
    return details


def job_usage_retention(ctx: JobContext) -> Mapping[str, Any]:
    """Usage rollups older than ``usage.retention_days``."""
    return usage_jobs.apply_retention(ctx.sessions, now=ctx.now)


def job_reports_run_due(ctx: JobContext) -> Mapping[str, Any]:
    """Usage reports: queue due schedules, render queued runs, e-mail them (ADR-0012)."""
    return run_due(ctx.sessions, ctx.layout, job_config(ctx), now=ctx.now).details()


def job_reports_purge(ctx: JobContext) -> Mapping[str, Any]:
    """Files of report runs older than ``reports.keep_days`` out of ``exports/``."""
    return purge_reports(ctx.sessions, ctx.layout, now=ctx.now).details()


def default_jobs(interval_seconds: int) -> tuple[Job, ...]:
    # Order matters when several are due at once (start-up): partitions first, then the
    # incremental job (which catches up and sets the watermark the rebuilds stop at).
    return (
        Job("partitions.ensure", job_partitions, daily_after=time(0, 15)),
        Job("license.check", job_license_check, every=LICENSE_EVERY),
        Job(
            "leases.incremental",
            job_leases_incremental,
            every=timedelta(seconds=interval_seconds),
        ),
        Job("leases.nightly", job_leases_nightly, daily_after=time(0, 5)),
        Job("leases.recheck_today", job_leases_recheck_today, every=timedelta(hours=1)),
        Job("leases.late_evidence", job_leases_late_evidence, every=LATE_EVIDENCE_EVERY),
        Job("evidence.purge", job_evidence_purge, daily_after=time(3, 0)),
        Job("audit.import", job_audit_import, every=AUDIT_IMPORT_EVERY),
        # Alerts: import and checks first, the e-mail last (it sends what they raised).
        Job("alerts.import", job_alerts_import, every=ALERTS_EVERY),
        Job("alerts.r15", job_alerts_r15, every=ALERTS_EVERY),
        Job("alerts.system", job_alerts_system, every=ALERTS_SYSTEM_EVERY),
        Job("alerts.notify", job_alerts_notify, every=ALERTS_EVERY),
        # Usage (spec 10.4) after the lease timeline: it reads who held an address when.
        Job("usage.requests", job_usage_requests, every=USAGE_REQUESTS_EVERY, module=USAGE_MODULE),
        Job("usage.daily", job_usage_daily, daily_after=time(0, 45), module=USAGE_MODULE),
        Job("usage.today", job_usage_today, every=USAGE_TODAY_EVERY, module=USAGE_MODULE),
        Job(
            "usage.reattribute",
            job_usage_reattribute,
            every=USAGE_REATTRIBUTE_EVERY,
            module=USAGE_MODULE,
        ),
        # Data minimisation: personal data must still be deleted on time, licensed or not.
        Job("usage.retention", job_usage_retention, daily_after=time(3, 30)),
        # Reports after the alerts: a report lists the alerts imported just before it.
        Job("reports.run_due", job_reports_run_due, every=REPORTS_EVERY, module=REPORTS_MODULE),
        Job("reports.purge", job_reports_purge, daily_after=time(3, 20)),
    )


# -- the scheduler --------------------------------------------------------------------------------


@dataclass
class JobState:
    last_started: datetime | None = None
    last_finished: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    #: Site day of the last successful run (daily jobs).
    done_day: date | None = None
    next_due: datetime | None = None
    runs: int = 0
    #: Failed runs in a row (reset by a successful run).
    failures: int = 0
    #: After a failure: not before this moment (:func:`retry_delay`).
    retry_at: datetime | None = None
    #: Site day of the last ``skipped`` row written by the licence gate (one per day).
    skip_day: date | None = None

    def as_json(self) -> dict[str, Any]:
        def iso(value: datetime | None) -> str | None:
            return None if value is None else value.isoformat()

        return {
            "last_started": iso(self.last_started),
            "last_finished": iso(self.last_finished),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "done_day": None if self.done_day is None else self.done_day.isoformat(),
            "next_due": iso(self.next_due),
            "runs": self.runs,
            "failures": self.failures,
            "retry_at": iso(self.retry_at),
        }


def _json_safe(value: Any) -> Any:
    """Job details as plain JSON (anything unexpected becomes its text)."""
    return json.loads(json.dumps(value, default=str))


@dataclass
class Worker:
    engine: Engine
    layout: Layout
    jobs: Sequence[Job]
    clock: Callable[[], datetime] = utc_now
    states: dict[str, JobState] = field(default_factory=dict)
    database_ok: bool | None = None
    started_at: datetime | None = None
    state: str = "starting"
    #: Passed to every job (``JobContext.config``).
    config: Config | None = None

    def __post_init__(self) -> None:
        self.sessions = session_factory(self.engine)
        self.states = {job.name: JobState() for job in self.jobs}
        self._loaded_history = False
        #: The licence status of the gates, kept 60 s (contract 4.3).
        self.license_cache = StatusCache()
        #: Per gated job: locked at the last evaluation (absent before the first one).
        self.gates: dict[str, bool] = {}

    @property
    def status_path(self) -> Path:
        return self.layout.state / STATUS_FILE

    # -- scheduling -------------------------------------------------------------------------------

    def _load_history(self) -> None:
        """Daily jobs: the site day of their last successful run (survives restarts)."""
        names = [job.name for job in self.jobs if job.daily_after is not None]
        with self.sessions() as session:
            rows = session.execute(
                select(JobRun.job, func.max(JobRun.started_at))
                .where(JobRun.job.in_(names), JobRun.status.in_(("ok", "skipped")))
                .group_by(JobRun.job)
            ).all()
        for name, started in rows:
            self.states[name].done_day = site_day(started)
        self._loaded_history = True

    def due(self, job: Job, now: datetime) -> bool:
        state = self.states[job.name]
        if state.retry_at is not None and now < state.retry_at:
            return False
        if job.every is not None:
            return state.last_started is None or now >= state.last_started + job.every
        local = to_site(now)
        return state.done_day != local.date() and local.time() >= job.daily_time

    def next_due(self, job: Job, now: datetime) -> datetime:
        state = self.states[job.name]
        if job.every is not None:
            due = now if state.last_started is None else state.last_started + job.every
        else:
            local = to_site(now)
            today = datetime.combine(local.date(), job.daily_time, tzinfo=SITE_TZ)
            if state.done_day == local.date() or local < today:
                due = today if local < today else today + timedelta(days=1)
            else:
                due = now
        if state.retry_at is not None and due < state.retry_at:
            return state.retry_at
        return due

    def tick(self) -> list[tuple[str, str]]:
        """Run every job that is due now (in declaration order); ``[(job, status)]``."""
        done: list[tuple[str, str]] = []
        if self.started_at is None:  # run() sets it; a test may tick without run()
            self.started_at = self.clock()
        try:
            if not self._loaded_history:
                self._load_history()
        except SQLAlchemyError as exc:
            self._database_down(exc)
            return done
        for job in self.jobs:
            now = self.clock()
            if not self.due(job, now):
                continue
            gate = self.gate(job, now) if job.module is not None else None
            if gate is not None:
                done.append((job.name, self.skip_job(job, now, gate)))
            else:
                done.append((job.name, self.run_job(job, now)))
        now = self.clock()
        for job in self.jobs:
            self.states[job.name].next_due = self.next_due(job, now)
        return done

    def context(self, now: datetime) -> JobContext:
        return JobContext(
            self.engine,
            self.sessions,
            now,
            self.layout,
            self.config,
            self.started_at,
            license_cache=self.license_cache,
        )

    # -- licence gates ----------------------------------------------------------------------------

    def gate(self, job: Job, now: datetime) -> dict[str, Any] | None:
        """``None`` when ``job`` may run; else the details of its ``skipped`` run (logged and,
        for ``reports.run_due``, the ``R20-report`` resolved when the gate closes)."""
        if job.module is None:
            return None
        gate = module_gate(self.context(now), job.module)
        locked = gate is not None
        if self.gates.get(job.name) != locked:
            self.gates[job.name] = locked
            info = (gate or {}).get("license", {})
            if locked:
                log.info(
                    "job %s skipped: module %s not licensed (%s)",
                    job.name,
                    job.module,
                    info.get("status"),
                )
                if job.module == REPORTS_MODULE:
                    self._resolve_report_alert(now)
            else:
                log.info("job %s runs: module %s licensed", job.name, job.module)
        return gate

    def _resolve_report_alert(self, now: datetime) -> None:
        try:
            with self.sessions() as session:
                if resolve_while_unlicensed(session, now=now):
                    log.info("R20-report resolved: scheduled reports are off in the licence")
                session.commit()
        except SQLAlchemyError as exc:
            self._database_down(exc)

    def skip_job(self, job: Job, now: datetime, gate: Mapping[str, Any]) -> str:
        """A due job of a locked module: one ``skipped`` row per site day, else only the time."""
        state = self.states[job.name]
        state.last_started = state.last_finished = now
        state.last_status, state.last_error = "skipped", None
        state.failures, state.retry_at = 0, None
        day = site_day(now)
        if job.daily_after is not None:
            state.done_day = day
        if state.skip_day == day:
            return "skipped"
        details = {key: value for key, value in gate.items() if key != SKIPPED}
        try:
            with self.sessions() as session:
                session.add(
                    JobRun(
                        job=job.name,
                        started_at=now,
                        finished_at=now,
                        status="skipped",
                        details=_json_safe(details),
                    )
                )
                session.commit()
        except SQLAlchemyError as exc:
            self._database_down(exc)
            return "skipped"
        state.skip_day = day
        return "skipped"

    # -- running ----------------------------------------------------------------------------------

    def run_job(self, job: Job, now: datetime) -> str:
        state = self.states[job.name]
        state.last_started, state.runs = now, state.runs + 1
        try:
            with self.sessions() as session:
                run = JobRun(job=job.name, started_at=now, status="running", details={})
                session.add(run)
                session.commit()
                run_id = run.id
        except SQLAlchemyError as exc:
            self._database_down(exc)
            state.last_status, state.last_error = "failed", "database unreachable"
            state.retry_at = now + DATABASE_RETRY
            return "failed"
        self.database_ok = True
        status = "ok"
        try:
            details = dict(job.func(self.context(now)))
            if details.pop(SKIPPED, False):
                status = "skipped"
            state.last_error = None
        except Exception as exc:  # a failing job must never stop the service
            log.exception("job %s failed", job.name)
            status = "failed"
            details = {"error": type(exc).__name__, "message": str(exc)[:500]}
            state.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
        finished = self.clock()
        state.last_finished, state.last_status = finished, status
        if status == "failed":
            state.failures += 1
            state.retry_at = now + retry_delay(job, state.failures)
            details["failures"] = state.failures
            details["retry_at"] = state.retry_at.isoformat()
        else:
            state.failures, state.retry_at = 0, None
            if job.daily_after is not None:
                state.done_day = site_day(now)
        details["duration_ms"] = (finished - now) // timedelta(milliseconds=1)
        try:
            with self.sessions() as session:
                session.execute(
                    update(JobRun)
                    .where(JobRun.id == run_id)
                    .values(finished_at=finished, status=status, details=_json_safe(details))
                )
                session.commit()
        except SQLAlchemyError as exc:
            self._database_down(exc)
        log.info("job %s: %s (%s ms)", job.name, status, details["duration_ms"])
        return status

    def _database_down(self, exc: SQLAlchemyError) -> None:
        if self.database_ok is not False:
            log.warning("database unreachable: %s", type(exc).__name__)
        self.database_ok = False

    # -- status and loop --------------------------------------------------------------------------

    def write_status(self) -> None:
        data = {
            "service": "arcilogger-worker",
            "version": __version__,
            "pid": os.getpid(),
            "state": self.state,
            "started_at": None if self.started_at is None else self.started_at.isoformat(),
            "updated_at": self.clock().isoformat(),
            "database": {None: "unknown", True: "ok", False: "unreachable"}[self.database_ok],
            "jobs": {name: state.as_json() for name, state in self.states.items()},
        }
        try:
            atomic_write_text(
                self.status_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n"
            )
        except OSError:
            log.exception("cannot write %s", self.status_path)

    def run(self, stop: threading.Event) -> None:
        self.started_at = self.clock()
        self.state = "running"
        log.info("ARCILOGGER worker %s started", __version__)
        written: datetime | None = None
        try:
            while not stop.is_set():
                ran = self.tick()
                now = self.clock()
                if ran or written is None or now - written >= STATUS_EVERY:
                    self.write_status()
                    written = now
                # Short waits: on Windows a blocked wait is not interrupted by Ctrl+C, the signal
                # handler only runs between them.
                stop.wait(SLEEP_SECONDS)
        finally:
            self.state = "stopped"
            self.write_status()
            log.info("ARCILOGGER worker stopped")
