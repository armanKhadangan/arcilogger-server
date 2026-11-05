"""``db.window``: the database keeps the last ``db.retention_days`` days of log rows (Wave 5
contract G3).

A daily job after 01:00 (site time, before the Backup task's dump: W5-G-27), and at start-up
when that day's run is missing. Each step is one transaction:

1. Archive reloads whose ``expires_at`` has passed (``ready_at`` + ``db.reload_keep_days``)
   become ``expired``, so their days can go again (G4).
2. The **cutoff** is the earliest of
   * ``today - db.retention_days`` (the window, :func:`arcilogger_db.partitions.retention_days`),
   * the oldest day that is not sealed yet (:func:`oldest_unsealed_day`) less
     :data:`~arcilogger_db.partitions.RAW_DAY_MARGIN` (2) days: a raw day's rows reach into the
     partitions of the two days before it (device clocks behind, lines received after
     midnight), and a day that could not come back from the vault is never dropped, not even in
     part. A day before the chain's start (``seal.first_day`` or the first sealed day) counts as
     sealed, a skipped (empty) day and a day retention removed from the vault too; today is
     never sealed. A day whose state says ``sealed`` but whose manifest (or a file it names) is
     missing or cannot be read counts as not sealed (:func:`unreadable_manifests`): it could not
     come back either;
   * the oldest day of an active archive reload (``queued``, ``loading``, ``ready``:
     :data:`~arcilogger_db.models.ARCHIVE_RELOAD_ACTIVE`). Its days are device-time days, and
     ArciIngest loads the raw days around them (G4), so every row from that day's midnight on
     is kept.
3. :func:`arcilogger_db.partitions.maintain_window`: the daily partitions of the window and the
   days ahead, then everything older than the next step towards the cutoff dropped — whole
   partitions (TimescaleDB: chunks), the older rows of the default partitions, their
   ``search_counts`` and ``source_clock_samples`` — and the applied cutoff recorded
   (``db.window_dropped_before``, which the API reads). It refuses a cutoff less than a week
   before today. A long backlog (the first run after the upgrade from the 400-day window) goes
   in steps of :data:`~arcilogger_db.partitions.DAYS_PER_RUN` days, each its own transaction,
   up to :data:`MAX_STEPS` a run; the limits are read again in every step.

While nothing is sealed yet and ``seal.first_day`` is not set (a new installation before its first
seal), no day can come back from the vault: nothing is dropped, only the partitions are made
(``limited_by`` ``nothing_sealed``). A day that stays unsealed (sealing stopped) holds the cutoff
until it is sealed: the database then grows beyond the window, which ``R20-seal`` reports. A
reload that stays ``queued`` or ``loading`` holds it too, however long (a day being loaded is
never dropped, and nothing fails it because of its age): the run names one waiting for more than
a day in ``details.reloads_waiting``. A reload makes no progress when it has the same status and
``bytes_done`` as the previous successful run's ``details.reload_progress``; a ``loading`` one
without progress opens the alert ``R20-db-window`` (medium, key ``R20-db-window:reload-<id>``,
naming the reload and its days), and so does a ``queued`` one, but only while nothing is
``loading`` and no reload moved on or ended since that run (a queue that moves is no incident;
manager answer 2 on #121). The alert is resolved by the first run that no longer finds the
reload so (it moved on or ended, or the queue before it moves).

**Audit** (actor ``system``; the record of the deletion, manager answer 4 on #121):

* Every step that dropped something (a partition or chunk, rows of a default partition, summary
  counts or clock samples) writes one ``db.window`` entry in its own transaction, so no drop is
  ever committed without it: the step's cutoff, the names of the partitions and chunks dropped
  (at most :data:`LIST_MAX`, the count complete) and the exact row counts the drop returns (the
  rows of the default partitions, ``search_counts``, clock samples). The rows of a dropped
  partition or chunk are not counted (a count would read the whole day). A run is one step
  unless it works off a backlog (the first run after the upgrade: one entry per step).
* The status row :data:`CHANGE_KEY` that revision ``0021_db_window`` leaves when it changed a
  stored ``db.retention_days`` (400 → 30, below 7 → 7; G1) becomes one ``settings.change`` entry
  with the old and the new value, in a transaction before the first step (no revision writes
  audit entries).

Every step first takes the partition maintenance lock
(:data:`~arcilogger_db.partitions.PARTITION_LOCK_KEY`) and only then reads the reloads: ArciIngest
takes the same lock in the transaction that starts loading a reload (its ``loading``), so a
reload is either seen here as active or starts loading after the step's drop, never dropped half
loaded. The API's lookups, searches and exports take it shared while they check and read the
days.

A step waits for that lock at most half of ``search.request_budget_ms``
(:func:`~arcilogger_db.partitions.step_lock_timeout_ms`, 2 s by default; W5-G-24): an export
holds it shared for up to ``search.export_budget_seconds`` (W5-G-23), and every search that comes
while the step waits queues behind the step's request. The step's table locks after it (its
partitions, the partitions and chunks it drops) share that wait
(:class:`~arcilogger_db.partitions.LockBudget`): a reader that takes no partition lock (a plain
``SELECT``, a backup) holding a table would otherwise keep the step, and the searches queued
behind its partition lock, waiting. A step that does not get them in time does
nothing (its transaction is rolled back; earlier steps of the run stay) and fails the run with
``PartitionLockTimeoutError`` and a warning line; the worker tries again (15 min, 30 min, …).
Only the ``alerts.job_failures``-th (3) failed run in a row opens ``R20-db-window`` (the threshold
at which ``R20-job`` reports any job), so one long export is no incident.

Any other failed step (the database, a sealing state that cannot be read) drops nothing — its
transaction is rolled back; earlier steps of the run stay, each complete with its recorded
cutoff — and opens the alert ``R20-db-window`` (medium, key :data:`WINDOW_KEY`); the worker
retries the job (15 min, 30 min, … at most 4 h). The next successful run resolves it. A failure
after the steps (resolving that alert, the report of the reloads without progress) fails the run
too, and the worker retries it the same way, but opens no alert: the steps' drops are done and
audited, and the failure shows in the job's run (``job_runs``); the next run compares the
reloads' progress with the last successful run's.

The vault, the spool and every sealed file are only read (``state/seal``, the manifests). The job
belongs to 5651 Temel and never asks the licence.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import Connection, func, select, update
from sqlalchemy.orm import Session

from arcilogger_core.config import Config
from arcilogger_core.fmt import format_date
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import site_day
from arcilogger_core.tsa.base import StampResult, TsaError
from arcilogger_db.audit import append_audit
from arcilogger_db.models import ARCHIVE_RELOAD_ACTIVE, ArchiveReload, JobRun, Setting
from arcilogger_db.partitions import (
    RAW_DAY_MARGIN,
    LockBudget,
    PartitionLockTimeoutError,
    PartitionReport,
    dropped_before,
    ensure_partitions,
    lock_partitions,
    maintain_window,
    retention_days,
    step_lock_timeout_ms,
)
from arcilogger_db.settings import INTEGER_RANGES, get_setting
from arcilogger_worker.alerts.store import active_alert, active_alerts, open_alert, resolve
from arcilogger_worker.alerts.system import JOB_WINDOW

if TYPE_CHECKING:
    from arcilogger_worker.service import JobContext

log = logging.getLogger("arcilogger_worker.db_window")

#: The job's name (worker service) and the audit action of a step that dropped something.
JOB: Final = "db.window"
#: The rule of the job's alerts (``alerts.texts.RULES``; ILK-ADIMLAR / FIRST-STEPS section 9).
WINDOW_RULE: Final = "R20-db-window"
#: Dedup key of the alert of a failed run (Wave 5 D14 form ``<code>:none``).
WINDOW_KEY: Final = f"{WINDOW_RULE}:none"
WINDOW_SEVERITY: Final = "medium"
#: ``details.auto_resolved.reason`` once a run succeeds again.
RESOLVED_REASON: Final = "window_maintained"
#: Dedup key prefix of the alert of a reload without progress (``R20-db-window:reload-<id>``).
STUCK_KEY: Final = f"{WINDOW_RULE}:reload-"
#: ``details.auto_resolved.reason`` once the reload moved on or ended.
PROGRESSED_REASON: Final = "reload_progressed"
#: ``details`` key of the progress of the reloads ``queued`` or ``loading`` (the next run reads it).
PROGRESS: Final = "reload_progress"
#: Longest list of partition or chunk names put into one audit entry (the count is complete).
LIST_MAX: Final = 100
#: The status row of revision ``0021_db_window``'s change of ``db.retention_days`` (G1; its
#: frozen copy is the revision's ``CHANGE_KEY``).
CHANGE_KEY: Final = "db.retention_days_migration"
#: Audit target of that change (as ``settings.change`` names a setting).
CHANGE_TARGET: Final = "setting:db.retention_days"
#: Steps one run takes at most (64 x 16 days: a first run after the upgrade from 400 days ends
#: in one run; the next run goes on otherwise).
MAX_STEPS: Final = 64
#: Most runs :func:`failed_in_a_row` reads (the upper bound of ``alerts.job_failures``).
ALERT_LIMIT: Final = INTEGER_RANGES["alerts.job_failures"][1]
#: A reload ``queued`` or ``loading`` for longer is named in ``details.reloads_waiting``.
RELOAD_WAITING: Final = timedelta(days=1)
_MARGIN: Final = timedelta(days=RAW_DAY_MARGIN)


class _NoStamps:
    """The sealer only answers which days are sealed here; it never stamps."""

    name = "none"
    legal_grade = False

    def stamp(self, file_path: Path) -> StampResult:  # pragma: no cover - never called
        raise TsaError("the database window does not stamp")

    def credits(self) -> int | None:  # pragma: no cover - never called
        return None


def oldest_unsealed_day(config: Config, now: datetime) -> date | None:
    """The oldest site day not sealed yet (today at the latest); ``None`` while nothing is sealed
    and ``seal.first_day`` is not set: then no day can come back from the vault.

    Read from the sealing records only (``state/seal``, the vault's manifests, the chain anchor),
    never from the spool. Days before the chain start count as sealed (``seal.first_day`` can only
    move it forward), and so do the skipped days and the days retention removed from the vault."""
    sealer = Sealer(config, provider=_NoStamps(), roots=[], clock=lambda: now)
    if sealer.established_start() is None and config.seal.first_day is None:
        return None
    return min([*sealer.closed_unsealed_days(), site_day(now)])


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None


def _manifest_readable(layout: Layout, day: date) -> bool:
    """Whether the day's manifest parses (as ArciIngest's ``SealIndex`` reads it) and every file
    it names is in the vault: what an archive reload of the day would read."""
    manifest = _read_json(layout.manifest(day))
    files = manifest.get("files") if isinstance(manifest, dict) else None
    if not isinstance(files, list):
        return False
    for entry in files:
        try:
            if not isinstance(entry["source"], str):
                return False
            int(entry["bytes_uncompressed"])
            int(entry["lines"])
            if not (layout.vault / str(entry["path"])).is_file():
                return False
        except (KeyError, TypeError, ValueError, OSError):
            return False
    return True


def unreadable_manifests(layout: Layout, first: date | None, end: date) -> list[date]:
    """Days in ``[first, end)`` (``first`` ``None``: from the oldest) whose seal state says
    ``sealed`` but whose manifest, or a file it names, is missing or cannot be read, oldest
    first. Only the state files of those days are read."""
    folder = layout.state / "seal"
    if not folder.is_dir():
        return []
    found = []
    for state_file in folder.glob("*.json"):
        try:
            day = date.fromisoformat(state_file.stem)
        except ValueError:
            continue
        if day >= end or (first is not None and day < first):
            continue
        state = _read_json(state_file)
        sealed = isinstance(state, dict) and state.get("status") == "sealed"
        if sealed and not _manifest_readable(layout, day):
            found.append(day)
    return sorted(found)


def expire_reloads(connection: Connection, now: datetime) -> int:
    """``ready`` reloads whose ``expires_at`` has passed become ``expired``; how many."""
    result = connection.execute(
        update(ArchiveReload)
        .where(ArchiveReload.status == "ready", ArchiveReload.expires_at <= now)
        .values(status="expired")
    )
    return int(result.rowcount or 0)


def oldest_reload_day(connection: Connection) -> date | None:
    """The oldest first day of an active archive reload (queued, loading or ready)."""
    value: date | None = connection.execute(
        select(func.min(ArchiveReload.first_day)).where(
            ArchiveReload.status.in_(ARCHIVE_RELOAD_ACTIVE)
        )
    ).scalar_one_or_none()
    return value


def waiting_reloads(connection: Connection, now: datetime) -> list[int]:
    """Reloads ``queued`` or ``loading`` for longer than :data:`RELOAD_WAITING` (module text)."""
    rows = connection.execute(
        select(ArchiveReload.id)
        .where(
            ArchiveReload.status.in_(("queued", "loading")),
            ArchiveReload.requested_at <= now - RELOAD_WAITING,
        )
        .order_by(ArchiveReload.id)
    ).scalars()
    return [int(value) for value in rows]


@dataclass(frozen=True)
class Cutoff:
    """The limits of one run (module text); :attr:`day` is the cutoff."""

    #: ``today - db.retention_days``.
    window: date
    #: :func:`oldest_unsealed_day` (``None``: nothing is sealed).
    unsealed: date | None
    #: :func:`oldest_reload_day` (``None``: no active reload).
    reload: date | None
    #: The oldest sealed day whose manifest cannot be read (:func:`unreadable_manifests`).
    unreadable: date | None = None

    @property
    def day(self) -> date | None:
        """The earliest of the limits, the unsealed and unreadable days less the raw-day
        margin; ``None`` while nothing is sealed (nothing may go)."""
        if self.unsealed is None:
            return None
        return min(self._limits().values())

    def _limits(self) -> dict[str, date]:
        limits = {"window": self.window}
        if self.unsealed is not None:
            limits["unsealed"] = self.unsealed - _MARGIN
        if self.unreadable is not None:
            limits["manifest"] = self.unreadable - _MARGIN
        if self.reload is not None:
            limits["reload"] = self.reload
        return limits

    @property
    def limited_by(self) -> str:
        """Which limit set the cutoff: ``window`` (also on a tie), ``unsealed``, ``manifest``,
        ``reload`` or ``nothing_sealed``."""
        day = self.day
        if day is None:
            return "nothing_sealed"
        return next(name for name, limit in self._limits().items() if limit == day)


def cutoff_of(connection: Connection, config: Config, layout: Layout, now: datetime) -> Cutoff:
    """The limits of a step (module text, step 2) in the caller's transaction."""
    today = site_day(now)
    limits = Cutoff(
        window=today - timedelta(days=retention_days(connection)),
        unsealed=oldest_unsealed_day(config, now),
        reload=oldest_reload_day(connection),
    )
    day = limits.day
    if day is None:
        return limits
    # The raw days whose rows this cutoff would drop for the first time.
    applied = dropped_before(connection)
    first = None if applied is None else applied - _MARGIN
    unreadable = unreadable_manifests(layout, first, day + _MARGIN)
    if not unreadable:
        return limits
    return Cutoff(limits.window, limits.unsealed, limits.reload, unreadable=unreadable[0])


def maintain(
    connection: Connection, config: Config, *, now: datetime, layout: Layout | None = None
) -> dict[str, Any]:
    """Steps 1-3 of the module text, one step, in the caller's transaction; the step's details
    (``cutoff``: the limit, ``applied``: the cutoff the step reached, ``done``: nothing is left
    to create or drop)."""
    today = site_day(now)
    budget = LockBudget(step_lock_timeout_ms(connection))
    lock_partitions(connection, budget=budget)
    expired = expire_reloads(connection, now)
    cutoff = cutoff_of(connection, config, layout or Layout(config.data_dir), now)
    details: dict[str, Any] = {
        "today": today.isoformat(),
        "cutoff": None if cutoff.day is None else cutoff.day.isoformat(),
        "limited_by": cutoff.limited_by,
        "window_from": cutoff.window.isoformat(),
        "unsealed_from": None if cutoff.unsealed is None else cutoff.unsealed.isoformat(),
        "reload_from": None if cutoff.reload is None else cutoff.reload.isoformat(),
        "unreadable_manifest": (
            None if cutoff.unreadable is None else cutoff.unreadable.isoformat()
        ),
        "reloads_waiting": waiting_reloads(connection, now),
        "expired": expired,
    }
    if cutoff.day is None:
        report = ensure_partitions(connection, today=today, budget=budget)  # no drop
        done = report.remaining == 0
    else:
        report = maintain_window(connection, cutoff.day, today=today, budget=budget)
        done = report.remaining == 0 and report.applied == cutoff.day
        if dropped_anything(report):
            details["audit_id"] = audit_drop(connection, cutoff, report, expired=expired)
    details.update(
        applied=None if report.applied is None else report.applied.isoformat(),
        done=done,
        created=len(report.created),
        remaining=report.remaining,
        dropped=len(report.dropped),
        purged=report.purged,
        counts_deleted=report.counts_deleted,
        clock_samples_deleted=report.clock_samples_deleted,
    )
    return details


def dropped_anything(report: PartitionReport) -> bool:
    """Whether a step dropped a partition or chunk, or deleted any row."""
    return bool(
        report.dropped or report.purged or report.counts_deleted or report.clock_samples_deleted
    )


def audit_drop(
    connection: Connection, cutoff: Cutoff, report: PartitionReport, *, expired: int
) -> int:
    """The ``db.window`` audit entry of a step that dropped something (module text), in the
    step's transaction; its id."""
    details = {
        "cutoff": None if report.applied is None else report.applied.isoformat(),
        "target_cutoff": None if cutoff.day is None else cutoff.day.isoformat(),
        "limited_by": cutoff.limited_by,
        "storage": "hypertables" if report.hypertables else "partitions",
        "dropped": report.dropped[:LIST_MAX],
        "dropped_count": len(report.dropped),
        "default_rows_deleted": report.purged,
        "counts_deleted": report.counts_deleted,
        "clock_samples_deleted": report.clock_samples_deleted,
        "reloads_expired": expired,
    }
    with Session(bind=connection) as session:
        entry = append_audit(session, action=JOB, target="database", details=details)
        return entry.id


#: Counts of the steps that the run's details add up.
_COUNTS: Final = (
    "expired",
    "created",
    "dropped",
    "purged",
    "counts_deleted",
    "clock_samples_deleted",
)


def run_steps(ctx: JobContext, config: Config) -> dict[str, Any]:
    """The steps of one run (module text), each in its own transaction, until nothing is left
    or :data:`MAX_STEPS`; the details of the last step with the counts of all of them."""
    total = dict.fromkeys(_COUNTS, 0)
    steps = 0
    audited: list[int] = []
    while True:
        with ctx.engine.begin() as connection:
            details = maintain(connection, config, now=ctx.now, layout=ctx.layout)
        steps += 1
        for key in _COUNTS:
            total[key] += int(details[key])
        if "audit_id" in details:
            audited.append(int(details.pop("audit_id")))
        if details["done"] or steps >= MAX_STEPS:
            break
    details.update(total, steps=steps, audit_ids=audited)
    return details


def audit_window_change(session: Session) -> int | None:
    """The one ``settings.change`` entry of revision ``0021_db_window``'s change of
    ``db.retention_days`` from its status row :data:`CHANGE_KEY` (module text), which is then
    marked ``audited``; the entry's id, ``None`` when there is nothing to audit."""
    row = session.get(Setting, CHANGE_KEY, with_for_update=True)
    if row is None or not isinstance(row.value, dict) or row.value.get("audited") is not False:
        return None
    change = row.value
    entry = append_audit(
        session,
        action="settings.change",
        target=CHANGE_TARGET,
        details={
            "old": change.get("old"),
            "new": change.get("new"),
            "was_set_by": change.get("was_set_by"),
            "migration": change.get("migration"),
            "changed_at": row.updated_at,
        },
    )
    row.value = {**change, "audited": True}
    session.flush()
    return entry.id


def reload_progress(session: Session) -> dict[str, dict[str, Any]]:
    """The reloads ``queued`` or ``loading`` now: id (text, as JSON keeps it) → status and
    ``bytes_done``."""
    rows = session.execute(
        select(ArchiveReload.id, ArchiveReload.status, ArchiveReload.bytes_done)
        .where(ArchiveReload.status.in_(("queued", "loading")))
        .order_by(ArchiveReload.id)
    ).all()
    return {str(id_): {"status": status, "bytes_done": int(done)} for id_, status, done in rows}


def previous_progress(session: Session) -> dict[str, Any]:
    """``details.reload_progress`` of the previous successful run (empty when there is none)."""
    details = session.scalars(
        select(JobRun.details)
        .where(JobRun.job == JOB, JobRun.status == "ok")
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .limit(1)
    ).first()
    progress = details.get(PROGRESS) if isinstance(details, dict) else None
    return progress if isinstance(progress, dict) else {}


def report_stuck_reloads(session: Session, now: datetime) -> tuple[dict[str, Any], list[int]]:
    """The progress of this run (kept in its details), and the reloads without progress since
    the previous successful run: one ``R20-db-window`` alert each (module text); the other
    alerts of reloads (moved on, ended, or queued behind a queue that moves) are resolved."""
    progress = reload_progress(session)
    before = previous_progress(session)
    # A queue that moves is no incident (manager answer 2 on #121): a queued reload counts only
    # when nothing loads and no reload moved on or ended since the previous run.
    moved = any(progress.get(key) != seen for key, seen in before.items())
    loading = any(seen["status"] == "loading" for seen in progress.values())
    stuck = sorted(
        int(key)
        for key, seen in progress.items()
        if before.get(key) == seen and (seen["status"] == "loading" or not (moved or loading))
    )
    keys = {f"{STUCK_KEY}{reload_id}" for reload_id in stuck}
    for reload_id in stuck:
        reload = session.get(ArchiveReload, reload_id)
        if reload is not None:
            _open_stuck(session, reload, now)
    for alert in active_alerts(session):
        key = alert.dedup_key or ""
        if key.startswith(STUCK_KEY) and key not in keys:
            resolve(alert, reason=PROGRESSED_REASON, now=now)
    return progress, stuck


def _open_stuck(session: Session, reload: ArchiveReload, now: datetime) -> None:
    first, last = reload.first_day, reload.last_day
    days_tr = format_date(first) if first == last else f"{format_date(first)} - {format_date(last)}"
    days_en = first.isoformat() if first == last else f"{first.isoformat()} - {last.isoformat()}"
    open_alert(
        session,
        rule=WINDOW_RULE,
        severity=WINDOW_SEVERITY,
        title=(
            f"Arşivden yeniden yükleme #{reload.id} ({days_tr}) önceki veritabanı penceresi "
            "çalıştırmasından beri ilerlemedi: o günden itibaren hiçbir kayıt silinmiyor"
        ),
        details={
            "reload_id": reload.id,
            "first_day": first.isoformat(),
            "last_day": last.isoformat(),
            "status": reload.status,
            "bytes_done": reload.bytes_done,
            "bytes_total": reload.bytes_total,
            "job": JOB,
            "title_en": (
                f"Archive reload #{reload.id} ({days_en}) has made no progress since the "
                "previous database window run: nothing from that day on is dropped"
            ),
        },
        key=f"{STUCK_KEY}{reload.id}",
        time=now,
        now=now,
    )


def job_db_window(ctx: JobContext) -> Mapping[str, Any]:
    """The database window (module text); a failure opens ``R20-db-window`` and fails the run."""
    config = ctx.config or Config(data_dir=ctx.layout.root, secrets_dir=ctx.layout.root)
    try:
        with ctx.sessions() as session:
            changed = audit_window_change(session)
            session.commit()
        details = run_steps(ctx, config)
    except PartitionLockTimeoutError as exc:
        _back_off(ctx, exc)
        raise
    except Exception as exc:
        _report_failure(ctx, exc)
        raise
    if changed is not None:
        details["window_change_audited"] = changed
    with ctx.sessions() as session:
        existing = active_alert(session, WINDOW_KEY)
        if existing is not None:
            resolve(existing, reason=RESOLVED_REASON, now=ctx.now)
            details["resolved"] = existing.id
        details[PROGRESS], details["reloads_stuck"] = report_stuck_reloads(session, ctx.now)
        session.commit()
    return details


def failed_in_a_row(session: Session, now: datetime) -> int:
    """Finished runs of the job that failed, counted back from the newest until one did not, as
    ``R20-job`` counts them (:func:`~arcilogger_worker.alerts.system.check_jobs`): runs started
    within :data:`~arcilogger_worker.alerts.system.JOB_WINDOW` before ``now``, and a run a
    person started (``details.manual``) ends the count."""
    runs = session.execute(
        select(JobRun.status, JobRun.details)
        .where(
            JobRun.job == JOB,
            JobRun.status != "running",
            JobRun.started_at >= now - JOB_WINDOW,
        )
        .order_by(JobRun.started_at.desc(), JobRun.id.desc())
        .limit(ALERT_LIMIT)
    )
    count = 0
    for status, details in runs:
        if status != "failed" or (details or {}).get("manual"):
            break
        count += 1
    return count


def _back_off(ctx: JobContext, exc: PartitionLockTimeoutError) -> None:
    """A step did not get the partition lock in time (module text): logged, and the
    ``R20-db-window`` alert only when this run is the ``alerts.job_failures``-th failed run in a
    row; an alert that cannot be written is only logged (the run fails either way)."""
    try:
        with ctx.sessions() as session:
            failures = failed_in_a_row(session, ctx.now) + 1
            needed = max(1, int(get_setting(session, "alerts.job_failures", 3)))
    except Exception:  # the database may be what failed: alert as for any failure
        log.exception("the failed runs of db.window could not be read")
        failures = needed = 1
    log.warning(
        "db.window: %s (a search export holds the partition lock, or a reader a table); "
        "nothing done, tried again at the next run (failed runs in a row: %d, alert from %d)",
        exc,
        failures,
        needed,
    )
    if failures >= needed:
        _report_failure(ctx, exc)


def _report_failure(ctx: JobContext, exc: Exception) -> None:
    """Open (or bring up to date) the one ``R20-db-window`` alert; an alert that cannot be
    written is only logged (the run fails either way)."""
    today = site_day(ctx.now)
    try:
        with ctx.sessions() as session:
            open_alert(
                session,
                rule=WINDOW_RULE,
                severity=WINDOW_SEVERITY,
                title=(
                    f"Veritabanı penceresi işi (db.window) {format_date(today)} günü çalışamadı: "
                    "pencereden eski günler silinmedi"
                ),
                details={
                    "day": today.isoformat(),
                    "job": JOB,
                    "error": type(exc).__name__,
                    "title_en": (
                        f"Database window job (db.window) failed on {today.isoformat()}: days "
                        "older than the window were not dropped"
                    ),
                },
                key=WINDOW_KEY,
                time=ctx.now,
                now=ctx.now,
            )
            session.commit()
    except Exception:  # the database may be what failed
        log.exception("the R20-db-window alert could not be written")
