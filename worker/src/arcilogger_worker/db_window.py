"""``db.window``: the database keeps the last ``db.retention_days`` days of log rows (Wave 5
contract G3).

A daily job after 04:00 (site time), and at start-up when that day's run is missing. Each step is
one transaction:

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
reload that stays ``queued`` or ``loading`` for more than a day holds it too; the run names it in
``details.reloads_waiting`` (nothing is dropped because of its age).

Every step first takes the partition maintenance lock
(:data:`~arcilogger_db.partitions.PARTITION_LOCK_KEY`) and only then reads the reloads: ArciIngest
takes the same lock in the transaction that starts loading a reload (its ``loading``), so a
reload is either seen here as active or starts loading after the step's drop, never dropped half
loaded. The API's lookups and searches take it shared while they check and read the days.

A failed step (the database, a sealing state that cannot be read) drops nothing — its
transaction is rolled back; earlier steps of the run stay, each complete with its recorded
cutoff — and opens the alert ``R20-db-window`` (medium, key :data:`WINDOW_KEY`); the worker
retries the job (15 min, 30 min, … at most 4 h). The next successful run resolves it.

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

from sqlalchemy import Connection, func, select, text, update

from arcilogger_core.config import Config
from arcilogger_core.fmt import format_date
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import site_day
from arcilogger_core.tsa.base import StampResult, TsaError
from arcilogger_db.models import ARCHIVE_RELOAD_ACTIVE, ArchiveReload
from arcilogger_db.partitions import (
    PARTITION_LOCK_KEY,
    RAW_DAY_MARGIN,
    dropped_before,
    ensure_partitions,
    maintain_window,
    retention_days,
)
from arcilogger_worker.alerts.store import active_alert, open_alert, resolve

if TYPE_CHECKING:
    from arcilogger_worker.service import JobContext

log = logging.getLogger("arcilogger_worker.db_window")

#: Dedup key of the alert of a failed run (Wave 5 D14 form ``<code>:none``). The rule is the part
#: before the colon: only the switch-on package adds the whole-string constant (D14, Wave 4 D7).
WINDOW_KEY: Final = "R20-db-window:none"
WINDOW_SEVERITY: Final = "medium"
#: ``details.auto_resolved.reason`` once a run succeeds again.
RESOLVED_REASON: Final = "window_maintained"
#: Steps one run takes at most (64 x 16 days: a first run after the upgrade from 400 days ends
#: in one run; the next run goes on otherwise).
MAX_STEPS: Final = 64
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
    connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY})
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
        report = ensure_partitions(connection, today=today)  # the partitions, no drop
        done = report.remaining == 0
    else:
        report = maintain_window(connection, cutoff.day, today=today)
        done = report.remaining == 0 and report.applied == cutoff.day
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
    while True:
        with ctx.engine.begin() as connection:
            details = maintain(connection, config, now=ctx.now, layout=ctx.layout)
        steps += 1
        for key in _COUNTS:
            total[key] += int(details[key])
        if details["done"] or steps >= MAX_STEPS:
            break
    details.update(total, steps=steps)
    return details


def job_db_window(ctx: JobContext) -> Mapping[str, Any]:
    """The database window (module text); a failure opens ``R20-db-window`` and fails the run."""
    config = ctx.config or Config(data_dir=ctx.layout.root, secrets_dir=ctx.layout.root)
    try:
        details = run_steps(ctx, config)
    except Exception as exc:
        _report_failure(ctx, exc)
        raise
    with ctx.sessions() as session:
        existing = active_alert(session, WINDOW_KEY)
        if existing is not None:
            resolve(existing, reason=RESOLVED_REASON, now=ctx.now)
            details["resolved"] = existing.id
        session.commit()
    return details


def _report_failure(ctx: JobContext, exc: Exception) -> None:
    """Open (or bring up to date) the one ``R20-db-window`` alert; an alert that cannot be
    written is only logged (the run fails either way)."""
    today = site_day(ctx.now)
    try:
        with ctx.sessions() as session:
            open_alert(
                session,
                rule=WINDOW_KEY.partition(":")[0],
                severity=WINDOW_SEVERITY,
                title=(
                    f"Veritabanı penceresi işi (db.window) {format_date(today)} günü çalışamadı: "
                    "pencereden eski günler silinmedi"
                ),
                details={
                    "day": today.isoformat(),
                    "job": "db.window",
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
