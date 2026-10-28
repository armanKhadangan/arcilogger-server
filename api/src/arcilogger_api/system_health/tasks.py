"""The scheduled tasks of ``deploy/windows/tasks/`` on the Sistem page (Wave 4 contract 2.6.2).

ArciApi may not ask the Task Scheduler (contract D9, owner question Q19), so each task is judged
by the newest evidence it leaves where ArciApi may read:

=============  ========================================================  ==========  ==========
Task           Evidence (below the data folder)                          Late after  Not used
=============  ========================================================  ==========  ==========
Seal           newest modification of ``state/seal/<day>.json`` (the     26 h        —
               last 3 days)
VerifyDaily    ``at`` of ``state/verify/last-last-1.json`` or ``-2``     26 h        —
VerifyWeekly   newest ``at`` of ``state/verify/last-last-<N>.json``,     8 days      —
               N ≥ 3
VerifyMonthly  ``at`` of ``state/verify/last-full.json``                 32 days     —
Replica        newest ``verified_at`` of ``state/replica/<day>.json``    26 h        replica off
               (the last 3 days)
Retention      none ArciApi can read: ``not_observable``                 —           retention
                                                                                     off
Credit         newest ``time`` of ``state/credit.jsonl``                 26 h        not Zamane
ClockCheck     newest ``time`` of ``state/clock/<day>.jsonl`` (today,    2 h         —
               yesterday)
Backup         ``finished`` of ``state/backup/last.json``                26 h        backup off
Requests       ``state/requests`` (not readable): ``not_observable``     —           —
ApplySystem    ``at`` of ``admin/system/status.json``                    15 min      browser
                                                                                     admin off
=============  ========================================================  ==========  ==========

``missing``: no evidence yet; ``late``: the evidence is older than the limit — each task's cadence
(the task's XML) plus a grace, not a setting. The names of the verification results are the ones
``arcilogger-core verify`` really writes (contract Implementation note 7): it puts ``last-``
before its kind, and the kind of ``--last N`` is ``last-<days verified>`` (:func:`verify_state`).
Seal is judged by the states of the last days only: Retention rewrites the state of each day it
deletes (``status = "deleted"``, ``retention._delete_day``), which says nothing about Seal.
A file that cannot be read is no evidence; it never fails the request.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from arcilogger_api.sealinfo import credit_history
from arcilogger_api.system_health.models import TaskHealth, TaskName
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, to_site
from arcilogger_worker.alerts.system import backup_state_path

#: ``verify --last 2`` (VerifyDaily) checks 1 day on a chain of one sealed day, else 2; more
#: days are the weekly ``--last 90`` (on a young chain: every sealed day there is).
DAILY_VERIFY_DAYS: Final = (1, 2)
#: Days of ``state/seal`` read, ending today (Seal writes yesterday's state after 00:10; a day
#: this young is never one Retention deletes).
SEAL_DAYS: Final = 3
#: Days of ``state/replica`` read, ending today (a day is copied after it was sealed).
REPLICA_DAYS: Final = 3


@dataclass(frozen=True)
class Evidence:
    """What the readers of one request share: the files are read once per request."""

    layout: Layout
    config: Config
    now: datetime
    #: Times of the clock samples of today and yesterday (:func:`clock_entries`).
    clock_times: tuple[datetime, ...]
    #: ``at`` of ApplySystem's ``admin\\system\\status.json`` (None: no report).
    system_at: datetime | None
    #: Browser administration switched on (``policy.json``).
    administration: bool


@dataclass(frozen=True)
class TaskRow:
    task: TaskName
    schedule_tr: str
    schedule_en: str
    #: Shown as the evidence, relative to the data folder (empty: none).
    evidence: str
    #: Newest evidence (None: none yet); None for a task whose evidence ArciApi cannot read.
    read: Callable[[Evidence], datetime | None] | None
    late_after: timedelta | None
    #: The task is switched off by the configuration (it runs and does nothing).
    not_used: Callable[[Evidence], bool] | None = None


def aware_time(value: Any) -> datetime | None:
    """An RFC 3339 text with its offset as a datetime (None: anything else)."""
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _read_json(path: Path) -> Any:
    """A JSON file, None when it is missing or cannot be read or parsed."""
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def _field_time(path: Path, key: str) -> datetime | None:
    data = _read_json(path)
    return aware_time(data.get(key)) if isinstance(data, dict) else None


def _newest(times: Any) -> datetime | None:
    found = [moment for moment in times if moment is not None]
    return max(found) if found else None


def verify_state(layout: Layout, days: int) -> Path:
    """The result ``arcilogger-core verify --last N`` writes when it verified ``days`` days:
    ``state/verify/last-last-<days>.json`` (``cmd_verify``: kind ``last-<days>``, file
    ``last-<kind>.json``)."""
    kind = f"last-{days}"
    return layout.state / "verify" / f"last-{kind}.json"


def _verified_days(layout: Layout, path: Path) -> int | None:
    """The ``days`` of a ``--last`` result's name (None: another kind, or a name the CLI never
    writes such as ``last-2.json``)."""
    count = path.stem.rpartition("-")[2]
    if not (count.isascii() and count.isdigit()):
        return None
    return int(count) if verify_state(layout, int(count)).name == path.name else None


def _modified(path: Path) -> datetime | None:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime, UTC)
    except OSError:
        return None


def _seal(evidence: Evidence) -> datetime | None:
    today = site_day(evidence.now)
    return _newest(
        _modified(evidence.layout.seal_state(today - timedelta(days=back)))
        for back in range(SEAL_DAYS)
    )


def _verify_daily(evidence: Evidence) -> datetime | None:
    return _newest(
        _field_time(verify_state(evidence.layout, days), "at") for days in DAILY_VERIFY_DAYS
    )


def _verify_weekly(evidence: Evidence) -> datetime | None:
    folder = evidence.layout.state / "verify"
    try:
        paths = list(folder.glob("*.json"))
    except OSError:
        return None
    return _newest(
        _field_time(path, "at")
        for path in paths
        if (days := _verified_days(evidence.layout, path)) is not None
        and days > max(DAILY_VERIFY_DAYS)
    )


def _verify_monthly(evidence: Evidence) -> datetime | None:
    return _field_time(evidence.layout.state / "verify" / "last-full.json", "at")


def _replica(evidence: Evidence) -> datetime | None:
    today = site_day(evidence.now)
    return _newest(
        _field_time(evidence.layout.replica_state(today - timedelta(days=back)), "verified_at")
        for back in range(REPLICA_DAYS)
    )


def _credit(evidence: Evidence) -> datetime | None:
    try:
        entries, _ = credit_history(evidence.layout)
    except OSError:  # not a file, or not readable (credit_history passes only a missing one)
        return None
    return _newest(entry["time"] for entry in entries)


def _clock(evidence: Evidence) -> datetime | None:
    return _newest(evidence.clock_times)


def _backup(evidence: Evidence) -> datetime | None:
    return _field_time(backup_state_path(evidence.layout), "finished")


def _apply_system(evidence: Evidence) -> datetime | None:
    return evidence.system_at


#: One row per file of ``deploy/windows/tasks/``, in the order of the contract's table (a test
#: compares the rows with the files and the times of the texts with their triggers).
TASKS: Final[tuple[TaskRow, ...]] = (
    TaskRow(
        "Seal",
        "Her gün 00:10'dan sonra 5 dakikada bir",
        "Every 5 minutes from 00:10",
        "state\\seal\\<YYYY-MM-DD>.json",
        _seal,
        timedelta(hours=26),
    ),
    TaskRow(
        "VerifyDaily",
        "Her gün 01:00",
        "Daily at 01:00",
        "state\\verify\\last-last-1.json, last-last-2.json",
        _verify_daily,
        timedelta(hours=26),
    ),
    TaskRow(
        "VerifyWeekly",
        "Pazar 03:00",
        "Sundays at 03:00",
        "state\\verify\\last-last-<N>.json (N ≥ 3)",
        _verify_weekly,
        timedelta(days=8),
    ),
    TaskRow(
        "VerifyMonthly",
        "Ayın 1'i 04:00",
        "1st of the month at 04:00",
        "state\\verify\\last-full.json",
        _verify_monthly,
        timedelta(days=32),
    ),
    TaskRow(
        "Replica",
        "Saatte bir, 00:40'tan",
        "Hourly from 00:40",
        "state\\replica\\<YYYY-MM-DD>.json",
        _replica,
        timedelta(hours=26),
        lambda evidence: not evidence.config.replica.enabled,
    ),
    TaskRow(
        "Retention",
        "Her gün 02:00",
        "Daily at 02:00",
        "",
        None,
        None,
        lambda evidence: not evidence.config.retention.enabled,
    ),
    TaskRow(
        "Credit",
        "Her gün 06:00",
        "Daily at 06:00",
        "state\\credit.jsonl",
        _credit,
        timedelta(hours=26),
        lambda evidence: evidence.config.tsa.provider != "zamane",
    ),
    TaskRow(
        "ClockCheck",
        "Saatte bir, 00:05'ten",
        "Hourly from 00:05",
        "state\\clock\\<YYYY-MM-DD>.jsonl",
        _clock,
        timedelta(hours=2),
    ),
    TaskRow(
        "Backup",
        "Her gün 01:40",
        "Daily at 01:40",
        "state\\backup\\last.json",
        _backup,
        timedelta(hours=26),
        lambda evidence: not evidence.config.backup.enabled,
    ),
    TaskRow(
        "Requests",
        "Olayla ve 5 dakikada bir",
        "On event and every 5 minutes",
        "state\\requests",
        None,
        None,
    ),
    TaskRow(
        "ApplySystem",
        "Olayla ve 5 dakikada bir",
        "On event and every 5 minutes",
        "admin\\system\\status.json",
        _apply_system,
        timedelta(minutes=15),
        lambda evidence: not evidence.administration,
    ),
)
TASK_ROWS: Final[Mapping[str, TaskRow]] = {row.task: row for row in TASKS}


def judge(row: TaskRow, evidence: Evidence) -> TaskHealth:
    """One task's row: its state from its newest evidence and the configuration."""
    last = row.read(evidence) if row.read is not None else None
    if row.not_used is not None and row.not_used(evidence):
        state = "not_used"
    elif row.read is None or row.late_after is None:
        state = "not_observable"
    elif last is None:
        state = "missing"
    else:
        state = "late" if evidence.now - last > row.late_after else "ok"
    return TaskHealth(
        task=row.task,
        schedule_tr=row.schedule_tr,
        schedule_en=row.schedule_en,
        state=state,
        last_evidence_at=None if last is None else to_site(last),
        evidence=row.evidence,
    )


def task_health(evidence: Evidence) -> list[TaskHealth]:
    return [judge(row, evidence) for row in TASKS]


def clock_entries(layout: Layout, now: datetime) -> list[tuple[datetime, dict[str, Any]]]:
    """The clock samples of today's and yesterday's ``state/clock/<day>.jsonl`` (24 a day),
    oldest first; a line that cannot be read is left out."""
    today = site_day(now)
    found: list[tuple[datetime, dict[str, Any]]] = []
    for day in (today - timedelta(days=1), today):
        try:
            lines = layout.clock_samples(day).read_bytes().splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                sample = json.loads(line)
            except ValueError:
                continue
            at = aware_time(sample.get("time")) if isinstance(sample, dict) else None
            if at is not None:
                found.append((at, sample))
    found.sort(key=lambda entry: entry[0])
    return found
