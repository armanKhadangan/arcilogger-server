"""One site day of the lease timeline rebuilt on request: by an administrator with
``arcilogger-worker rebuild`` (the manual repair) and from the browser's "Kira zaman çizelgesini
yeniden oluştur" (worker job ``admin.requests``, contract 2.9).

Each day is its own transaction, recorded as a ``leases.rebuild`` job run (``manual: true``; the
request's id when the browser asked). A failed day is a ``failed`` run with the error, never
left ``running``; the caller decides whether the next day is tried. A :class:`ValueError` is a
lease, ARP or NAT setting the timeline cannot work with (``Context.load``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any, Final

from sqlalchemy import update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import JobRun
from arcilogger_worker.correlation.leases import LeaseJobReport, rebuild_day

JOB: Final = "leases.rebuild"


@dataclass(frozen=True)
class DayRebuild:
    """The outcome of one day: its report, or (``report`` is ``None``) the error that rolled
    it back."""

    day: date
    report: LeaseJobReport | None = None
    error: Exception | None = None

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def error_text(self) -> str | None:
        """``Type: message`` of the error (``None`` for a rebuilt day)."""
        return None if self.error is None else f"{type(self.error).__name__}: {self.error}"

    @property
    def settings_error(self) -> bool:
        """The settings cannot be worked with (every other day would fail the same way)."""
        return isinstance(self.error, ValueError)


def rebuild_lease_day(
    sessions: sessionmaker[Session],
    day: date,
    *,
    lookahead: bool,
    details: Mapping[str, Any] | None = None,
) -> DayRebuild:
    """Rebuild ``day`` in its own transaction, recorded as a ``leases.rebuild`` job run whose
    details carry ``details`` too (e.g. ``request``). ``lookahead`` only for the last day of a
    range (:func:`.leases.rebuild_day`)."""
    extra = dict(details or {})
    with sessions() as session:
        run = JobRun(
            job=JOB,
            started_at=utc_now(),
            status="running",
            details={"day": day.isoformat(), "manual": True, **extra},
        )
        session.add(run)
        session.commit()
        run_id = run.id
    with sessions() as session:
        try:
            report = rebuild_day(session, day, lookahead=lookahead)
            session.commit()
        except Exception as exc:  # recorded and returned; the job run never stays "running"
            session.rollback()
            outcome = DayRebuild(day, error=exc)
            final: dict[str, Any] = {
                "day": day.isoformat(),
                "manual": True,
                **extra,
                "error": type(exc).__name__,
                "message": str(exc)[:500],
            }
        else:
            outcome = DayRebuild(day, report=report)
            final = {**report.details(), "manual": True, **extra}
    with sessions() as session:
        session.execute(
            update(JobRun)
            .where(JobRun.id == run_id)
            .values(status="ok" if outcome.ok else "failed", finished_at=utc_now(), details=final)
        )
        session.commit()
    return outcome
