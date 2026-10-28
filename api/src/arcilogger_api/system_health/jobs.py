"""The worker jobs on the Sistem page (Wave 4 contract 2.6.2).

The list is :func:`arcilogger_worker.service.default_jobs` as the worker builds it
(``default_jobs(config.worker.interval_seconds)``), joined with the job state the worker writes
into ``state/worker-status.json`` and, for the duration, with the newest finished ``job_runs``
row of the last 7 days of each job: one probe of the index ``(job, started_at)`` per job, never a
``DISTINCT ON`` over the table, which is not purged and grows about 17,000 rows a day (owner
question Q21). A job's error text (``last_error``, the details of a failed run) is never read
into the answer: the page names the job, the alert ``R20-job`` and the worker's log tell more.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api.system_health.models import JobHealth, JobHealthState
from arcilogger_api.system_health.tasks import aware_time
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import to_site
from arcilogger_db.models import JobRun
from arcilogger_worker.alerts.system import JOB_WINDOW
from arcilogger_worker.service import Job, default_jobs

#: Turkish and English names of the interval units, largest first (singular, plural in English).
UNITS: Final = (
    (3600, "saatte", ("hour", "hours")),
    (60, "dakikada", ("minute", "minutes")),
    (1, "saniyede", ("second", "seconds")),
)
#: The last word of a number as it is spoken in Turkish (0-9, then the tens).
_ONES: Final = ("sıfır", "bir", "iki", "üç", "dört", "beş", "altı", "yedi", "sekiz", "dokuz")
_TENS: Final = ("", "on", "yirmi", "otuz", "kırk", "elli")


def _ablative(number: int) -> str:
    """The ablative suffix after a number as it is read ("05'ten", "10'dan", "15'ten")."""
    word = _ONES[number % 10] if number % 10 or number == 0 else _TENS[number // 10]
    vowel = next(letter for letter in reversed(word) if letter in "aeıioöuü")
    consonant = "t" if word[-1] in "çfhkpsşt" else "d"
    return consonant + ("a" if vowel in "aıou" else "e") + "n"


def from_time_tr(at: time) -> str:
    """``HH:MM`` with the Turkish "from" suffix of how it is read: by the minutes, or by the hour
    on the full hour ("00:15'ten", "01:00'den", "03:30'dan")."""
    spoken = at.minute if at.minute else at.hour
    return f"{at.strftime('%H:%M')}'{_ablative(spoken)}"


def schedule_texts(job: Job) -> tuple[str, str]:
    """When a job runs, Turkish and English: "Her 5 dakikada bir" / "Every 5 minutes", "Her gün
    00:15'ten sonra" / "Daily after 00:15"."""
    if job.every is None:
        at = job.daily_time
        return f"Her gün {from_time_tr(at)} sonra", f"Daily after {at.strftime('%H:%M')}"
    seconds = int(job.every.total_seconds())
    size, unit_tr, (one, many) = next(unit for unit in UNITS if seconds % unit[0] == 0)
    count = seconds // size
    return f"Her {count} {unit_tr} bir", f"Every {count} {one if count == 1 else many}"


@dataclass(frozen=True)
class LastRun:
    """The newest finished ``job_runs`` row of a job (no details, no error text)."""

    status: str
    started_at: datetime
    finished_at: datetime | None

    @property
    def duration_ms(self) -> int | None:
        if self.finished_at is None:
            return None
        return (self.finished_at - self.started_at) // timedelta(milliseconds=1)


def last_run(db: Session, job: str, since: datetime) -> LastRun | None:
    """One probe of ``ix_job_runs_job_started_at``: the newest run of ``job`` since ``since``
    that is not running any more."""
    row = db.execute(
        select(JobRun.status, JobRun.started_at, JobRun.finished_at)
        .where(JobRun.job == job, JobRun.status != "running", JobRun.started_at >= since)
        .order_by(JobRun.started_at.desc())
        .limit(1)
    ).first()
    return None if row is None else LastRun(row.status, row.started_at, row.finished_at)


def worker_jobs(layout: Layout) -> Mapping[str, Mapping[str, Any]]:
    """``jobs`` of ``state/worker-status.json`` (empty when it is missing or unreadable)."""
    try:
        data = json.loads(layout.worker_status.read_bytes())
    except (OSError, ValueError):
        return {}
    jobs = data.get("jobs") if isinstance(data, dict) else None
    if not isinstance(jobs, dict):
        return {}
    return {name: state for name, state in jobs.items() if isinstance(state, dict)}


def _site(value: datetime | None) -> datetime | None:
    return None if value is None else to_site(value)


def job_row(job: Job, state: Mapping[str, Any], run: LastRun | None) -> JobHealth:
    """A job from its worker state (``JobState.as_json``, ``last_error`` left out) and its last
    finished run; without a state (the worker never wrote one) the run alone."""
    started = aware_time(state.get("last_started"))
    finished = aware_time(state.get("last_finished"))
    status = state.get("last_status")
    last_status = status if isinstance(status, str) else None
    count = state.get("failures")
    failures = count if type(count) is int and count > 0 else 0
    if started is None and run is not None:
        started, finished, last_status = run.started_at, run.finished_at, run.status
    if started is None:
        health: JobHealthState = "never"
    elif finished is None or started > finished:
        health = "running"
    elif failures or last_status == "failed":
        health = "failing"
    elif last_status == "skipped":
        health = "skipped"
    else:
        health = "ok"
    schedule_tr, schedule_en = schedule_texts(job)
    return JobHealth(
        job=job.name,
        schedule_tr=schedule_tr,
        schedule_en=schedule_en,
        module=job.module,
        state=health,
        last_status=last_status,
        last_started=_site(started),
        last_finished=_site(finished),
        duration_ms=None if run is None else run.duration_ms,
        failures=failures,
        next_due=_site(aware_time(state.get("next_due"))),
    )


def job_health(
    db: Session, layout: Layout, interval_seconds: int, now: datetime
) -> list[JobHealth]:
    """Every job of the worker, in its order (``default_jobs``)."""
    states = worker_jobs(layout)
    since = now - JOB_WINDOW
    return [
        job_row(job, states.get(job.name, {}), last_run(db, job.name, since))
        for job in default_jobs(interval_seconds)
    ]
