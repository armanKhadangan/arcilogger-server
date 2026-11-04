"""The scheduled tasks of ``deploy/windows/tasks/`` on the Sistem page (Wave 4 contract 2.6.2).

ArciApi may not ask the Task Scheduler (contract D9, owner question Q19), so each task is judged
by the newest evidence it leaves where ArciApi may read:

=============  ========================================================  ==========  ==========
Task           Evidence (below the data folder)                          Late after  Not used
=============  ========================================================  ==========  ==========
Seal           newest modification of a ``state/seal/<day>.json`` that   26 h        —
               Retention did not write (status not ``deleted``)
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
Backup         ``finished`` of ``state/backup/last.json``; ``failed``    26 h        backup off
               when its ``ok`` is not ``true``
Requests       ``state/requests`` (not readable): ``not_observable``     —           —
ApplySystem    ``at`` of ``admin/system/status.json``                    15 min      browser
                                                                                     admin off
Renew          ``last_run.at`` of ``state/renewal/renewal.json``         26 h        —
               (not ``at``, which a run without pinned roots keeps)
CrlFetch       newest ``fetched_at`` of the ``lists`` or ``at`` of the   26 h        —
               ``failures`` of ``state/crl/index.json``; with no
               failure, also when the index was written; ``failed``
               with its ``run_failure``, or when ``crl fetch`` cannot
               read the index
=============  ========================================================  ==========  ==========

``missing``: no evidence yet; ``late``: the evidence is older than the limit — each task's cadence
(the task's XML) plus a grace, not a setting; ``failed``: the task's latest run recorded that it
failed, however long ago, with a ``code`` (:data:`TaskFailure`) and the failure's time as the
evidence (#171). The names of the verification results are the ones
``arcilogger-core verify`` really writes (contract Implementation note 7): it puts ``last-``
before its kind, and the kind of ``--last N`` is ``last-<days verified>`` (:func:`verify_state`).
Seal is judged by every day's state except those Retention rewrote: it marks each day it deletes
(``status = "deleted"``, ``retention._delete_day``), which says nothing about Seal, while Seal
itself may rewrite any day's state, also an old one it retries after a time-stamp outage.
Backup, Renew and CrlFetch are judged by their state files, never by an exit code (Wave 5
contract C12, A4). Backup's row is ``failed`` while ``last.json`` records a failed attempt, as
R20-backup reads it; its ``error`` text is never read (KVKK). Renew's row says that the task ran,
and whether it succeeded is R20-renewal's; the per-URL ``failures`` of CrlFetch's index leave its
row ``ok`` too, they are R20-crl's (W5-E-2). A dry run of ``renew`` keeps ``last_run``, and a
``crl fetch`` that finds the lock held writes nothing, so neither is evidence. From an upgrade to
the first counted Renew run the Renew row reads the earlier release's ``state/renewal.json`` and
names it as its evidence (Wave 5 contract W5-E-4). Every ``crl fetch`` that gets past its lock and
its reads rewrites the index with the failures of that run only, so an index that records no
failure and no run failure was written by a completed run without one, and its modification time
is that run: also on a site that knows no list address any more, whose stored lists keep their
old ``fetched_at`` (W5-E-2, answer to Q8 on #158). A run that finds the lock held writes nothing,
but the run holding it writes the index, so the row stays fresh. A run that cannot read a stored
list exits 1 (answer to Q9) and writes the index as it read it with a ``run_failure``, its time
and code and never the error text: the row is ``failed`` until the next completed run writes the
index without one. A run that cannot read the index itself writes nothing: the row is ``failed``
with no time when the reader of ``crl fetch`` rejects what the index holds
(``arcilogger_core.crl.read_index``, stricter than R20-crl's: a byte order mark, for one), and
``missing`` while ArciApi cannot read the file at all.
A time later than now is no evidence of Renew or CrlFetch, nor a failed Backup's time: a clock set
back never keeps them fresh.
A file that cannot be read is no evidence; it never fails the request.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from arcilogger_api.sealinfo import credit_history
from arcilogger_api.system_health.models import TaskHealth, TaskName
from arcilogger_core.config import Config
from arcilogger_core.crl import CrlError
from arcilogger_core.crl import read_index as read_crl_document
from arcilogger_core.layout import Layout
from arcilogger_core.renewal import state_path as renewal_state_path
from arcilogger_core.renewal import state_to_read as renewal_state_to_read
from arcilogger_core.seal import DELETED
from arcilogger_core.timeutil import site_day, to_site
from arcilogger_worker.alerts.system import (
    backup_state_path,
    crl_index_path,
    read_crl_index,
    read_renew_runs,
)

#: ``verify --last 2`` (VerifyDaily) checks 1 day on a chain of one sealed day, else 2; more
#: days are the weekly ``--last 90`` (on a young chain: every sealed day there is).
DAILY_VERIFY_DAYS: Final = (1, 2)
#: Days of ``state/replica`` read, ending today (a day is copied after it was sealed).
REPLICA_DAYS: Final = 3

#: The ``code`` of a ``failed`` row: never the error text a task wrote, which can hold paths,
#: URLs, host and user names (KVKK).
TaskFailure = Literal["backup_failed", "index_unreadable", "stored_file_unreadable"]
#: The ``run_failure.error`` values a ``crl fetch`` records in its index; a record with another
#: one is no evidence.
CRL_RUN_FAILURES: Final[tuple[TaskFailure, ...]] = ("stored_file_unreadable",)


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
class Reading:
    """The evidence of a task whose latest run can record that it failed."""

    #: Newest evidence (None: none yet); when ``failure`` is set, the failure's time (None:
    #: unknown).
    at: datetime | None
    #: Why the latest run failed (None: it did not).
    failure: TaskFailure | None = None


@dataclass(frozen=True)
class TaskRow:
    task: TaskName
    schedule_tr: str
    schedule_en: str
    #: Shown as the evidence, relative to the data folder (empty: none).
    evidence: str
    #: Newest evidence (None: none yet), or a :class:`Reading` with the failure of the latest
    #: run; None for a task whose evidence ArciApi cannot read.
    read: Callable[[Evidence], Reading | datetime | None] | None
    late_after: timedelta | None
    #: The task is switched off by the configuration (it runs and does nothing).
    not_used: Callable[[Evidence], bool] | None = None
    #: The evidence shown, when it depends on which file is there (None: ``evidence``).
    shown: Callable[[Evidence], str] | None = None


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
    """The newest state Seal wrote: the day states newest first (a stat each), read until one is
    not a day Retention deleted. During a time-stamp outage Seal retries its oldest unsealed day
    only (``seal_due`` stops at the first failure), so any day's state may be the newest."""
    states: list[tuple[datetime, Path]] = []
    try:
        for path in (evidence.layout.state / "seal").glob("*.json"):
            try:
                date.fromisoformat(path.stem)
            except ValueError:
                continue
            modified = _modified(path)
            if modified is not None:
                states.append((modified, path))
    except OSError:
        return None
    for modified, path in sorted(states, reverse=True):
        data = _read_json(path)
        if isinstance(data, dict) and data.get("status") != DELETED:
            return modified
    return None


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


def _backup(evidence: Evidence) -> Reading | datetime | None:
    """``finished`` of ``state/backup/last.json``; a failed attempt (``ok`` not ``true``, as
    R20-backup reads it) writes the time it failed there. ``error`` is never read (KVKK)."""
    data = _read_json(backup_state_path(evidence.layout))
    if not isinstance(data, dict):
        return None
    finished = aware_time(data.get("finished"))
    if data.get("ok") is not True:
        return Reading(_until_now(evidence, (finished,)), "backup_failed")
    return finished


def _apply_system(evidence: Evidence) -> datetime | None:
    return evidence.system_at


def _until_now(evidence: Evidence, moments: Iterable[datetime | None]) -> datetime | None:
    """The newest of ``moments`` that is not later than now."""
    return _newest(moment for moment in moments if moment is not None and moment <= evidence.now)


def _renew(evidence: Evidence) -> datetime | None:
    runs = read_renew_runs(evidence.layout)
    return None if runs is None else _until_now(evidence, (runs.last_run_at,))


def _renew_shown(evidence: Evidence) -> str:
    """The file :func:`_renew` reads: ``state\\renewal\\renewal.json``, or the earlier release's
    ``state\\renewal.json`` while it is the only one (W5-E-4, answer to Q2 on #158). Never
    raises: a file that cannot be looked at names the file the task writes."""
    written = renewal_state_path(evidence.layout)
    path = renewal_state_to_read(evidence.layout)
    try:
        earlier = path != written and path.exists()
    except OSError:  # Path.exists() raises PermissionError, for one
        earlier = False
    shown = path if earlier else written  # neither file yet: the task writes the new one
    return "\\".join(shown.relative_to(evidence.layout.root).parts)


def _crl_fetch(evidence: Evidence) -> Reading | datetime | None:
    try:
        # The reader of crl fetch itself: what it cannot get past, the task cannot either. It is
        # stricter than R20-crl's (a byte order mark, for one).
        document = read_crl_document(evidence.layout)
    except CrlError as exc:
        # An index ArciApi cannot read is no evidence; one it reads and crl fetch rejects is.
        return None if isinstance(exc.__cause__, OSError) else Reading(None, "index_unreadable")
    except RecursionError:  # nested too deeply to parse: crl fetch stops on it as well
        return Reading(None, "index_unreadable")
    completed = "run_failure" not in document
    if not completed:
        failed = _crl_run_failure(evidence, document["run_failure"])
        if failed is not None:
            return failed
    index = read_crl_index(evidence.layout)
    moments: list[datetime | None] = [entry.fetched_at for entry in index.lists]
    moments += [failure.at for failure in index.failures]
    if completed and index.readable and not index.failures:
        # The run that wrote the index completed and failed nowhere: its lists' fetched_at can
        # be older, when it knew no list address (#151, answer to Q8 on #158). state/crl is
        # protected.
        moments.append(_modified(crl_index_path(evidence.layout)))
    return _until_now(evidence, moments)


def _crl_run_failure(evidence: Evidence, record: Any) -> Reading | None:
    """The ``run_failure`` a ``crl fetch`` that could not complete wrote into the index as it
    read it; the next completed run writes an index without one. None for a record with an
    unknown ``error`` or a time that is missing or later than now: the row is then judged by the
    index's lists and failures, but never by its modification time, which is no completed run."""
    if not isinstance(record, dict):
        return None
    at = aware_time(record.get("at"))
    code = next((known for known in CRL_RUN_FAILURES if known == record.get("error")), None)
    if at is None or at > evidence.now or code is None:
        return None
    return Reading(at, code)


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
    TaskRow(
        "Renew",
        "Her gün 02:30",
        "Daily at 02:30",
        "state\\renewal\\renewal.json",
        _renew,
        timedelta(hours=26),
        shown=_renew_shown,
    ),
    TaskRow(
        "CrlFetch",
        "Her gün 00:30",
        "Daily at 00:30",
        "state\\crl\\index.json",
        _crl_fetch,
        timedelta(hours=26),
    ),
)
TASK_ROWS: Final[Mapping[str, TaskRow]] = {row.task: row for row in TASKS}


def judge(row: TaskRow, evidence: Evidence) -> TaskHealth:
    """One task's row: its state from its newest evidence and the configuration, in this order:
    ``not_used``, ``not_observable``, ``failed`` (however old, until the next completed run),
    ``missing``, ``late`` or ``ok``."""
    found = row.read(evidence) if row.read is not None else None
    reading = found if isinstance(found, Reading) else Reading(found)
    last = reading.at
    if row.not_used is not None and row.not_used(evidence):
        state = "not_used"
    elif row.read is None or row.late_after is None:
        state = "not_observable"
    elif reading.failure is not None:
        state = "failed"
    elif last is None:
        state = "missing"
    else:
        state = "late" if evidence.now - last > row.late_after else "ok"
    return TaskHealth(
        task=row.task,
        schedule_tr=row.schedule_tr,
        schedule_en=row.schedule_en,
        state=state,
        code=reading.failure if state == "failed" else None,
        last_evidence_at=None if last is None else to_site(last),
        evidence=row.evidence if row.shown is None else row.shown(evidence),
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
