"""Licence gates of the worker and the ``license.check`` job (licensing contract 7).

* :func:`module_gate` — asked by the scheduler before a job of a paid module (``Job.module``:
  ``usage.*`` → ``usage_analytics``, ``reports.run_due`` → ``usage_reports``) would run:
  ``None`` while the module is enabled, else the details of the ``skipped`` run. The status
  comes from :func:`arcilogger_db.license_guard.safe_status` (a broken licence subsystem locks
  only the paid modules) and is kept :data:`STATUS_SECONDS` per worker process.
* :func:`job_license_check` — every 5 minutes, always recomputed: state changes and limit
  crossings become licence events and audit entries (each change once), the licence alerts
  (``R20-license``, :mod:`.alerts.license`) are opened and resolved in their own transaction —
  a failure there is logged and reported in the details, it never fails the job —, and the
  installer's status file ``state/license-status.json`` is rewritten. It imports the licence code
  inside the function: when that fails the job fails like any job (retries, ``R20-job``), the
  others run on.

Every job of 5651 Temel (leases, partitions, evidence, audit and alert imports, the checks and
the alert e-mail) and the data-minimisation jobs (``usage.retention``, ``reports.purge``) never
ask: they run in every licence state. This module imports no licence code at module level.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy.exc import SQLAlchemyError

from arcilogger_db.audit import append_audit
from arcilogger_db.license_guard import GuardedStatus, safe_status
from arcilogger_worker.alerts.license import check_license

if TYPE_CHECKING:
    from arcilogger_core.license.state import LicenseStatus
    from arcilogger_worker.service import JobContext

log = logging.getLogger("arcilogger_worker")

__all__ = [
    "STATUS_SECONDS",
    "StatusCache",
    "guarded_status",
    "job_license_check",
    "module_gate",
]

#: How long a worker process keeps the status for its gates (contract 4.3).
STATUS_SECONDS: Final = 60.0


class StatusCache:
    """The last guarded status of this worker, kept :data:`STATUS_SECONDS` (monotonic)."""

    def __init__(
        self, ttl: float = STATUS_SECONDS, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.ttl = ttl
        self.clock = clock
        self._value: GuardedStatus | None = None
        self._expires = 0.0
        self._lock = threading.Lock()

    def get(self) -> GuardedStatus | None:
        with self._lock:
            if self._value is not None and self.clock() < self._expires:
                return self._value
            return None

    def put(self, value: GuardedStatus) -> None:
        with self._lock:
            self._value, self._expires = value, self.clock() + self.ttl

    def invalidate(self) -> None:
        with self._lock:
            self._value, self._expires = None, 0.0


def _compute(ctx: JobContext) -> GuardedStatus:
    """A fresh guarded status; never raises (a database outage locks only the paid jobs)."""
    with ctx.sessions() as session:
        guarded = safe_status(session, config=ctx.config, layout=ctx.layout, clock=lambda: ctx.now)
        try:
            session.commit()  # the installation row, when it was missing
        except SQLAlchemyError:
            session.rollback()
    return guarded


def guarded_status(ctx: JobContext) -> GuardedStatus:
    """The cached guarded status of this worker (computed when absent or older than 60 s)."""
    cache = ctx.license_cache
    if cache is not None:
        cached = cache.get()
        if cached is not None:
            return cached
    guarded = _compute(ctx)
    if cache is not None and guarded.status is not None:  # a failure is computed again
        cache.put(guarded)
    return guarded


def module_gate(ctx: JobContext, key: str) -> dict[str, Any] | None:
    """None when enabled (safe_status); else
    {"_skipped": True, "license": {"module": key, "status": state}}."""
    guarded = guarded_status(ctx)
    if guarded.module_enabled(key):
        return None
    return {"_skipped": True, "license": {"module": key, "status": guarded.state}}


def job_license_check(ctx: JobContext) -> Mapping[str, Any]:
    """``license.check``: recompute the status, record changes, write the status file."""
    from arcilogger_core.license import module_keys  # a failure here fails the job (contract 7)
    from arcilogger_db import licensing

    with ctx.sessions() as session:

        def audit(action: str, target: str | None, details: Mapping[str, Any]) -> None:
            append_audit(session, action=action, target=target, details=details)

        status = licensing.load_status(
            session, config=ctx.config, layout=ctx.layout, clock=lambda: ctx.now
        )
        recorded = licensing.check_and_record(session, status, audit=audit, clock=lambda: ctx.now)
        session.commit()
    if ctx.license_cache is not None:
        ctx.license_cache.put(GuardedStatus(status=status))
    alerts = _alerts(ctx, status)
    licensing.write_status_file(ctx.layout, status, clock=lambda: ctx.now)
    return {
        "status": status.state,
        "enabled": [key for key in module_keys() if status.module_enabled(key)],
        "recorded": recorded,
        "alerts": alerts,
    }


def _alerts(ctx: JobContext, status: LicenseStatus) -> Mapping[str, Any]:
    """The licence alerts for ``status``; a failure is only logged and reported (the state is
    recorded already, and an alert never makes the licence check fail)."""
    try:
        with ctx.sessions() as session:
            report = check_license(session, status, now=ctx.now)
            session.commit()
    except Exception as exc:  # whatever went wrong: the job and the 5651 functions go on
        log.exception("licence alerts failed")
        return {"error": f"{type(exc).__name__}: {str(exc)[:200]}"}
    return report
