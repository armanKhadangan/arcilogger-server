"""Usage rollup jobs (spec 10.4) and the rebuild of a range of days.

==========================  =====================================================================
``usage.today``             every 5 minutes: today's rollup (``partial``) — the hourly and daily
                            tables of the current day from what has been loaded so far
``usage.daily``             daily after 00:45: yesterday rebuilt as ``final`` (idempotent)
``usage.reattribute``       every 10 minutes: the last ``usage.auto_rebuild_days`` days are
                            rebuilt when what they were computed from changed — a device was
                            (re)assigned or the lease timeline changed (who an address belonged
                            to), late logs were loaded (also during the computation: the
                            fingerprint is taken before the logs are read), the ``usage.*``
                            settings or the engine changed — or when a day is missing or still
                            ``partial``
``usage.requests``          every 30 seconds: rebuild requests of the web interface
                            (``POST /usage/rebuild``, table ``usage_rebuilds``)
``usage.retention``         daily after 03:30: rollups older than ``usage.retention_days``
==========================  =====================================================================

Each day is computed and written in its own transaction (:mod:`.store`). The worker runs its jobs
one after the other, so the jobs that may take long (re-attribution, requests) stop starting new
days after :data:`TIME_BUDGET` and continue with the next run. A rebuild of a day older than the
window (``arcilogger-worker usage rebuild --from --to`` or the web interface) is the way to apply a
change to older days.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, exists, select, text, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.timeutil import day_window, site_day
from arcilogger_db.models import (
    USAGE_DAY_TABLES,
    DomainCategory,
    FgTraffic,
    UsageDay,
    UsageRebuild,
)
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.domains import DomainNamer
from arcilogger_worker.usage.engine import compute_day
from arcilogger_worker.usage.store import final_after, inputs_hash, lock_day, write_day
from arcilogger_worker.usage.subjects import build_timelines, timelines_hash

log = logging.getLogger("arcilogger.worker.usage")

#: A job that may run long starts no new day after this many seconds.
TIME_BUDGET: Final = timedelta(seconds=120)
#: Finished rebuild requests are kept this long.
REQUESTS_KEEP: Final = timedelta(days=90)


@dataclass
class RunReport:
    days: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    reasons: dict[str, str] = field(default_factory=dict)
    more: bool = False

    def details(self) -> dict[str, Any]:
        found: dict[str, Any] = {"days": self.days}
        if self.reasons:
            found["reasons"] = self.reasons
        if self.skipped:
            found["skipped"] = self.skipped
        if self.more:
            found["more"] = True
        return found


def rebuild_day(
    session: Session,
    day: date,
    *,
    now: datetime,
    settings: UsageSettings | None = None,
    namer: DomainNamer | None = None,
) -> dict[str, Any]:
    """Compute ``day`` and replace its rollup; commits. ``final`` once its late window passed."""
    settings = settings or UsageSettings.load(session)
    namer = namer or DomainNamer(settings.psl_file, settings.psl_private_suffixes)
    started = time.perf_counter()
    lock_day(session, day)
    # Before the logs are read: a log the ingest commits during the computation then changes
    # the fingerprint, and the re-attribution job rebuilds the day with it.
    inputs = inputs_hash(session, day, settings, namer)
    result = compute_day(session, day, settings=settings, namer=namer)
    status = "final" if now >= final_after(result.hi, settings) else "partial"
    report = write_day(
        session,
        result,
        status=status,
        inputs=inputs,
        now=now,
        duration_ms=int((time.perf_counter() - started) * 1000),
    )
    session.commit()
    details = report.details()
    details["duration_ms"] = int((time.perf_counter() - started) * 1000)
    log.info("usage %s: %s in %s ms", day, status, details["duration_ms"])
    return details


def rebuild_range(
    sessions: sessionmaker[Session],
    first: date,
    last: date,
    *,
    now: datetime,
    progress: Callable[[date, dict[str, Any]], None] | None = None,
) -> list[dict[str, Any]]:
    """Rebuild ``first`` … ``last`` (each day its own transaction), in order."""
    done = []
    day = first
    with sessions() as session:
        settings = UsageSettings.load(session)
    namer = DomainNamer(settings.psl_file, settings.psl_private_suffixes)
    while day <= last:
        with sessions() as session:
            details = rebuild_day(session, day, now=now, settings=settings, namer=namer)
        done.append(details)
        if progress is not None:
            progress(day, details)
        day += timedelta(days=1)
    return done


# -- the scheduled jobs ---------------------------------------------------------------------------


def refresh_today(sessions: sessionmaker[Session], *, now: datetime) -> dict[str, Any]:
    """``usage.today``: today's rollup from the logs loaded so far."""
    with sessions() as session:
        return rebuild_day(session, site_day(now), now=now)


def finalize_yesterday(sessions: sessionmaker[Session], *, now: datetime) -> dict[str, Any]:
    """``usage.daily``: yesterday, final (also when the five-minute job saw all of it)."""
    with sessions() as session:
        return rebuild_day(session, site_day(now) - timedelta(days=1), now=now)


def _has_traffic(session: Session, day: date) -> bool:
    lo, hi = day_window(day)
    return bool(session.scalar(select(exists().where(FgTraffic.time >= lo, FgTraffic.time < hi))))


def stale_reason(
    session: Session,
    day: date,
    *,
    now: datetime,
    settings: UsageSettings,
    namer: DomainNamer,
) -> str | None:
    """Why ``day`` must be rebuilt (``missing``, ``partial``, ``inputs``, ``attribution``)."""
    row = session.get(UsageDay, day)
    if row is None:
        return "missing" if _has_traffic(session, day) else None
    lo, hi = day_window(day)
    if row.status == "partial" and now >= final_after(hi, settings):
        return "partial"
    if row.inputs_hash != inputs_hash(session, day, settings, namer):
        return "inputs"
    if row.ips:
        current = timelines_hash(build_timelines(session, row.ips, lo, hi))
        if current != row.attribution_hash:
            return "attribution"
    return None


def reattribute(
    sessions: sessionmaker[Session], *, now: datetime, budget: timedelta = TIME_BUDGET
) -> RunReport:
    """``usage.reattribute``: rebuild the recent days whose inputs or owners changed."""
    report = RunReport()
    started = time.monotonic()
    today = site_day(now)
    with sessions() as session:
        settings = UsageSettings.load(session)
        namer = DomainNamer(settings.psl_file, settings.psl_private_suffixes)
        window = settings.auto_rebuild_days
        candidates = [today - timedelta(days=n) for n in range(window, 0, -1)]
        todo: list[tuple[date, str]] = []
        for day in candidates:
            reason = stale_reason(session, day, now=now, settings=settings, namer=namer)
            if reason is not None:
                todo.append((day, reason))
        session.rollback()
    for day, reason in todo:
        if time.monotonic() - started > budget.total_seconds():
            report.more = True
            report.skipped.append(day.isoformat())
            continue
        with sessions() as session:
            report.days.append(rebuild_day(session, day, now=now, settings=settings, namer=namer))
        report.reasons[day.isoformat()] = reason
    return report


def process_requests(
    sessions: sessionmaker[Session], *, now: datetime, budget: timedelta = TIME_BUDGET
) -> RunReport:
    """``usage.requests``: work through queued rebuild requests, oldest first."""
    report = RunReport()
    started = time.monotonic()
    while time.monotonic() - started <= budget.total_seconds():
        with sessions() as session:
            request = session.scalars(
                select(UsageRebuild)
                .where(UsageRebuild.status.in_(("queued", "running")))
                .order_by(UsageRebuild.id)
                .limit(1)
                .with_for_update(skip_locked=True)
            ).first()
            if request is None:
                return report
            if request.status == "queued":
                request.status = "running"
                request.started_at = now
            request_id = request.id
            day = request.from_day + timedelta(days=request.done_days)
            finished = day > request.to_day
            if finished:
                request.status = "done"
                request.finished_at = now
            session.commit()
        if finished:
            continue
        try:
            with sessions() as session:
                report.days.append(rebuild_day(session, day, now=now))
        except Exception as exc:  # recorded on the request; the job itself goes on
            log.exception("usage rebuild request %s failed on %s", request_id, day)
            with sessions() as session:
                session.execute(
                    update(UsageRebuild)
                    .where(UsageRebuild.id == request_id)
                    .values(
                        status="failed",
                        error=f"{day}: {type(exc).__name__}: {str(exc)[:300]}",
                        finished_at=now,
                    )
                )
                session.commit()
            continue
        with sessions() as session:
            session.execute(
                update(UsageRebuild)
                .where(UsageRebuild.id == request_id)
                .values(done_days=UsageRebuild.done_days + 1)
            )
            session.commit()
    report.more = True
    return report


def apply_retention(sessions: sessionmaker[Session], *, now: datetime) -> dict[str, Any]:
    """``usage.retention``: delete rollups older than ``usage.retention_days``."""
    with sessions() as session:
        settings = UsageSettings.load(session)
        cutoff = site_day(now) - timedelta(days=settings.retention_days)
        deleted: dict[str, int] = {}
        for table in (*USAGE_DAY_TABLES, "usage_days"):
            result = session.execute(
                text(f"DELETE FROM {table} WHERE day < :cutoff"),  # noqa: S608 - fixed names
                {"cutoff": cutoff},
            )
            deleted[table] = int(getattr(result, "rowcount", 0) or 0)
        result = session.execute(delete(DomainCategory).where(DomainCategory.last_day < cutoff))
        deleted["domain_categories"] = int(getattr(result, "rowcount", 0) or 0)
        result = session.execute(
            delete(UsageRebuild).where(
                UsageRebuild.status.in_(("done", "failed")),
                UsageRebuild.finished_at < now - REQUESTS_KEEP,
            )
        )
        deleted["usage_rebuilds"] = int(getattr(result, "rowcount", 0) or 0)
        session.commit()
    return {"cutoff": cutoff.isoformat(), "deleted": deleted}
