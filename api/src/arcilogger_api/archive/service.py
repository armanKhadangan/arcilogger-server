"""The database window as the API sees it, and the archive reload requests (Wave 5 contract §5,
G4 to G9).

**Days.** The days here are site days of device time (``time``): the days a lookup or search
reads, and the partitions the window job drops. A raw day's file has rows up to 25 hours either
side of its day, in the partitions of :data:`~arcilogger_db.partitions.RAW_DAY_MARGIN` (2) days
around it; so a reload of days ``F`` … ``L`` loads the raw days ``F - 2`` … ``L + 2`` again
(ArciIngest), and while it loads, the days ``F - 4`` … ``L + 4`` those files have rows in are
incomplete.

**Which days are in the database** (:func:`check_window`). A day is **gone** — a lookup or search
over it would miss rows — when

* it is before the newest cutoff the window job ``db.window`` applied (the status row
  ``db.window_dropped_before``, :func:`arcilogger_db.partitions.dropped_before`, written with the
  drop: everything before that midnight went, whatever the window is now), or before the window
  (:func:`window_start`: today - ``db.retention_days``) with no row at all (a database restored or
  lost: ingest loads only the window back), unless a ``ready`` reload holds it;
* or a ``loading`` reload is replacing the files it has rows of (``F - 4`` … ``L + 4``), even
  inside the window and even when a ``ready`` reload holds it too;
* or a sealed file whose rows lie in it (its raw day within two days) was reset by a ``failed``
  reload and has not finished loading since: it became unreadable or changed while it loaded
  (W5-G answer 9), so its offset is short of the manifest's ``bytes_uncompressed`` — inside the
  window and under a ``ready`` reload too, until a later load finishes it. Never a partial
  answer.

A day before the window that still has rows and is not before the applied cutoff has not been
dropped yet (the job's run of today is to come, the day is unsealed, a monthly partition of the
change-over holds it) and is read as it is. A ``queued`` reload changes nothing yet: its load
starts in a transaction that takes the partition maintenance lock
(:data:`~arcilogger_db.partitions.PARTITION_LOCK_KEY`) exclusively, while :func:`check_window`
takes it shared for the rest of the caller's transaction — the lookup's reads included —, so
neither a load nor the window job's drop starts between the check and the reads. The search,
whose REPEATABLE READ snapshot is taken by its transaction's first statement, holds the lock
shared at session level from before that transaction instead (:func:`hold_window`,
``check_window(held=True)``), so its snapshot has every drop it waited for.

A gone day comes back when the vault has it: a day with a sealed manifest (``state/seal`` says
``sealed``, :class:`arcilogger_ingest.sealed.SealIndex`). A gone day a ``queued`` or ``loading``
reload brings back is **waiting**; one that needs a new reload is **missing**; a gone day without
a manifest (removed by the vault's retention, before the chain's first day) cannot be reloaded,
and the lookup's and the search's own notices name it (``older_than_db_retention``,
``vault_only_before``). The 409 ``archive_reload_required`` names the missing and waiting days,
and ``db.reload_keep_days`` at the time of the answer (C23: the confirm dialog names it to a user
who cannot read ``/settings``).

**Requests** (``POST /archive-reloads``, :func:`create_reload`): at most :data:`MAX_DAYS` days;
days of the window that are complete are refused (422 ``validation_error``: nothing to reload);
when no day of the request is missing and an active request of the same user covers it, that
request is the answer (a second click, the web's re-run) instead of a second load — another
user's request never is, for an administrator either (W5-G answer 6: each person's request keeps
its own record); every day sealed (a day the sealer skipped as empty has nothing to load),
the margin days loaded when they are sealed; and the estimated growth of the database must leave
:data:`MIN_FREE_PERCENT` of the PostgreSQL data drive free. The estimate counts only the files
ArciIngest would load (:func:`bytes_to_load`): a file that counts as loaded already (finished,
and nothing of the request's days dropped since: another user's ``ready`` reload holds them) is
neither reset nor loaded again, so it takes no room. Each refusal is a 422 with ``code``,
``message_tr`` and ``message_en``. The audit entry ``archive.reload`` is written first, the
``queued`` row after it in the same transaction (G6); an answered existing request is audited as
well (``existing``). ArciIngest loads the request (:mod:`arcilogger_ingest.service`).

The vault and the spool are only read (the manifests); nothing here writes a log row.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Connection, case, exists, func, select, text
from sqlalchemy.orm import Session

from arcilogger_api.archive.models import ArchivedRange, ArchiveReloadRequest, ArchiveReloadRequired
from arcilogger_api.archive.models import ArchiveReload as ArchiveReloadOut
from arcilogger_api.audit import record_audit
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser
from arcilogger_core.fmt import format_bytes, format_date
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.models import (
    ARCHIVE_RELOAD_ACTIVE,
    ArchiveReload,
    FgDhcp,
    FgTraffic,
    IngestOffset,
    Message,
    MtArp,
    MtDhcp,
)
from arcilogger_db.partitions import (
    PARTITION_LOCK_KEY,
    RAW_DAY_MARGIN,
    dropped_before,
    retention_days,
)
from arcilogger_db.settings import DEFAULT_SETTINGS, get_setting
from arcilogger_ingest.sealed import SealIndex
from arcilogger_ingest.service import IngestService
from arcilogger_worker.alerts.system import drives
from arcilogger_worker.system_jobs import read_history

#: Database bytes per raw byte (G5): the growth of every table a loaded line writes to (the 16
#: time-series tables, ``search_counts``, ``source_clock_samples``, ``parse_failures``: rows,
#: indexes and TOAST, after ``VACUUM ANALYZE``) divided by the raw bytes loaded (the spool files,
#: which are the sealed files' ``bytes_uncompressed``). Measured 2026-11-02 on plain PostgreSQL 16
#: with daily partitions: ``tools/loadgen`` days through the real ingest
#: (``IngestService.run(once=True)``, ``batch_rows`` 5,000) into a fresh test database each:
#:
#: * default site, 2026-10-27: 218,170 lines, 139,102,963 raw bytes → 349,200,384 bytes, **2.51**
#:   (rows alone 1.97);
#: * default site, 2026-10-26 and 27: 426,905 lines, 272,266,266 → 682,049,536 bytes, **2.51**;
#: * FortiGate-only site with ``srcmac``, 2026-10-27: 214,836 lines, 140,590,432 → 337,584,128
#:   bytes, **2.40**.
#:
#: Rounded up, so a reload is not under-estimated (a reload's deletes leave dead rows until
#: autovacuum). On TimescaleDB the rows of a reloaded day are written uncompressed first.
DB_BYTES_PER_RAW_BYTE: Final = 2.6
#: The largest request (``archive_reload_too_large``).
MAX_DAYS: Final = 14
#: Free space the PostgreSQL data drive must keep after a reload (``archive_reload_disk_low``).
MIN_FREE_PERCENT: Final = 15
#: The raw days around a request's days whose files it loads (module text).
MARGIN: Final = timedelta(days=RAW_DAY_MARGIN)
#: Requests ``GET /archive-reloads`` lists besides the active ones (the newest first).
RECENT: Final = 20
#: Days a refusal names at most.
NAMED_DAYS: Final = 5
#: A day with a row in one of these tables is in the database (every loaded line has a
#: ``messages`` row; the typed tables are what a lookup reads).
PRESENCE_TABLES: Final = (Message, FgTraffic, MtDhcp, MtArp, FgDhcp)

MESSAGES: Final = {
    "archive_reload_required": (
        "İstenen kayıtlar için gereken günler ({first_day} - {last_day}) veritabanında değil: "
        "veritabanı son {window} günün kayıtlarını tutar. Bu günler mühürlü arşivden yeniden "
        "yüklenebilir (yaklaşık {size}); yükleme denetim kaydına yazılır. Bu günler okunmadan "
        "sonuç verilmez.",
        "The days these records need ({first_day_en} - {last_day_en}) are not in the database: "
        "it keeps the records of the last {window} days. They can be reloaded from the sealed "
        "archive (about {size_en}); the reload is written to the audit log. No answer is given "
        "without them.",
    ),
    "archive_reload_too_large": (
        "Bir yüklemede en fazla {max_days} gün istenebilir; bu istek {days} gün. Aralığı "
        "daraltın veya birkaç yüklemeye bölün.",
        "One reload may ask for at most {max_days} days; this request has {days}. Narrow the "
        "range or split it into several reloads.",
    ),
    "archive_day_not_in_vault": (
        "Şu günlerin mühürlü arşivde kaydı yok (henüz mühürlenmedi veya saklama süresi doldu): "
        "{days}. Bu günler yeniden yüklenemez.",
        "The sealed archive holds no record of these days (not sealed yet, or removed by "
        "retention): {days_en}. They cannot be reloaded.",
    ),
    "archive_reload_disk_low": (
        "Yükleme veritabanı diskinde (yaklaşık {size}) yer kaplar ve {drive} sürücüsünde boş "
        "alan %{percent} altına iner. Daha kısa bir aralık seçin veya sistem yöneticisine "
        "bildirin.",
        "The reload would take about {size_en} on the database drive and leave less than "
        "{percent} % free on {drive}. Choose a shorter range or tell the administrator.",
    ),
}
#: The texts of a ``failed`` reload's ``error_code`` (``models.ArchiveReloadFailureCode``),
#: without parameters: the reload names its days itself.
FAILURE_MESSAGES: Final = {
    "archive_day_not_in_vault": (
        "Yükleme yapılamadı: günlerden biri artık mühürlü arşivde yok (istek beklerken saklama "
        "süresi doldu). Bu gün yeniden yüklenemez.",
        "The reload failed: one of the days is no longer in the sealed archive (removed by "
        "retention while the request waited). That day cannot be reloaded.",
    ),
    "archive_file_unreadable": (
        "Yükleme yapılamadı: bir günün mühürlü dosyası okunamadı veya mühür kaydıyla eşleşmiyor. "
        "Sistem yöneticisine bildirin; arşiv doğrulamasını çalıştırın.",
        "The reload failed: a sealed file of one of the days could not be read or does not "
        "match its seal record. Tell the administrator and run the archive verification.",
    ),
}


def archive_error(status_code: int, code: str, details: Any = None, **params: object) -> ApiError:
    """An error of the area with its two texts (``MESSAGES``)."""
    text_tr, text_en = MESSAGES[code]
    return ApiError(
        status_code,
        code,
        text_tr.format(**params),
        text_en.format(**params),
        details=details,
    )


# -- the window -----------------------------------------------------------------------------------


def days_of(first_day: date, last_day: date) -> list[date]:
    """``first_day`` … ``last_day``, both included."""
    return [first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)]


def window_start(db: Session, today: date) -> date:
    """The first day of the window: today - ``db.retention_days`` (G1)."""
    return today - timedelta(days=retention_days(db.connection()))


def reload_keep_days(db: Session) -> int:
    """Setting ``db.reload_keep_days`` (G4): how long a ``ready`` reload holds its days; the
    shipped default when unset or not a positive number, as ArciIngest reads it for
    ``expires_at``."""
    keep = get_setting(db, "db.reload_keep_days")
    if isinstance(keep, int) and not isinstance(keep, bool) and keep >= 1:
        return keep
    return int(DEFAULT_SETTINGS["db.reload_keep_days"])


def _reloads(db: Session, statuses: Iterable[str]) -> list[tuple[date, date]]:
    rows = db.execute(
        select(ArchiveReload.first_day, ArchiveReload.last_day).where(
            ArchiveReload.status.in_(list(statuses))
        )
    )
    return [(first_day, last_day) for first_day, last_day in rows]


def _spans(reloads: Iterable[tuple[date, date]], margin: timedelta) -> set[date]:
    return {day for first, last in reloads for day in days_of(first - margin, last + margin)}


def _left_short(db: Session, seals: SealIndex, first_day: date, last_day: date) -> set[date]:
    """The days of ``first_day`` … ``last_day`` with rows of a sealed file a ``failed`` reload
    reset and that has not finished loading since (module text): its raw day is one of the
    failed reload's (its days and :data:`MARGIN` either side), its offset is not finished."""
    low, high = first_day - MARGIN, last_day + MARGIN  # the raw days with rows in the range
    failed = db.execute(
        select(ArchiveReload.first_day, ArchiveReload.last_day).where(
            ArchiveReload.status == "failed",
            ArchiveReload.first_day <= high + MARGIN,
            ArchiveReload.last_day >= low - MARGIN,
        )
    )
    spans = _spans(((first, last) for first, last in failed), MARGIN)
    raw_days = sorted(day for day in spans if low <= day <= high)
    if not raw_days:
        return set()
    short = db.execute(
        select(IngestOffset.source, IngestOffset.day).where(
            IngestOffset.day.in_(raw_days), IngestOffset.finished_at.is_(None)
        )
    )
    days: set[date] = set()
    for source, raw_day in short:
        if source in (seals.files(raw_day) or {}):
            days.update(days_of(raw_day - MARGIN, raw_day + MARGIN))
    return days


def _has_rows(db: Session, day: date) -> bool:
    """Any row of the day in ``messages`` or a table the lookup reads (as the lookup's data
    window, :func:`arcilogger_worker.lookup.window.data_window`, asks)."""
    start, end = day_window(day)
    return any(
        db.scalar(select(exists().where(model.time >= start, model.time < end)))
        for model in PRESENCE_TABLES
    )


def vault_sizes(layout: Layout, first_day: date, last_day: date) -> dict[date, int]:
    """Σ ``bytes_uncompressed`` of each sealed day's files, for the sealed days of ``first_day``
    … ``last_day`` (a day without a sealed manifest is left out)."""
    seals = SealIndex(layout)
    sizes: dict[date, int] = {}
    for day in days_of(first_day, last_day):
        files = seals.files(day)
        if files is not None:
            sizes[day] = sum(entry.bytes_uncompressed for entry in files.values())
    return sizes


def reload_bytes(layout: Layout, first_day: date, last_day: date) -> int:
    """Σ ``bytes_uncompressed`` of the files a reload of ``first_day`` … ``last_day`` loads:
    the sealed raw days of the request and its margin (module text)."""
    return sum(reload_files(layout, first_day, last_day).values())


def reload_files(layout: Layout, first_day: date, last_day: date) -> dict[tuple[str, date], int]:
    """``bytes_uncompressed`` by ``(source, raw day)`` of the files a reload of ``first_day`` …
    ``last_day`` loads, as ArciIngest lists them (``IngestService._request_files``): every file
    of the sealed raw days of the request and its margin."""
    seals = SealIndex(layout)
    files: dict[tuple[str, date], int] = {}
    for day in days_of(first_day - MARGIN, last_day + MARGIN):
        sealed = seals.files(day)
        if sealed is not None:
            files.update({(source, day): f.bytes_uncompressed for source, f in sealed.items()})
    return files


@dataclass(frozen=True)
class WindowCheck:
    """The days of one range outside the database (module text)."""

    #: The first day of the window.
    window_start: date
    #: Every day of the range a lookup or search would not see completely.
    gone: tuple[date, ...]
    #: Gone days the vault has and no ``queued`` or ``loading`` reload brings back: what a new
    #: reload would bring back.
    missing: tuple[date, ...]
    #: Gone days a ``queued`` or ``loading`` reload brings back.
    waiting: tuple[date, ...]
    #: Every day of the range is gone (missing, waiting or not in the vault either).
    all_gone: bool
    #: The reload's raw bytes (:func:`reload_bytes`) of :meth:`reloadable`'s range.
    bytes_total: int = 0
    #: ``db.reload_keep_days`` (:func:`reload_keep_days`), read with ``bytes_total`` (C23).
    keep_days: int = int(DEFAULT_SETTINGS["db.reload_keep_days"])

    def reloadable(self) -> tuple[date, ...]:
        """The missing and waiting days, oldest first."""
        return tuple(sorted({*self.missing, *self.waiting}))

    def required(self) -> ArchiveReloadRequired | None:
        """The ``details`` of 409 ``archive_reload_required``; ``None`` when nothing is
        missing or waiting."""
        days = self.reloadable()
        if not days:
            return None
        return ArchiveReloadRequired(
            first_day=days[0],
            last_day=days[-1],
            bytes_total=self.bytes_total,
            estimated_bytes=estimate(self.bytes_total),
            keep_days=self.keep_days,
        )

    def archived(self) -> ArchivedRange | None:
        """The search answer's ``archived`` (G8): the missing and waiting part of the range."""
        days = self.reloadable()
        if not days:
            return None
        return ArchivedRange(first_day=days[0], last_day=days[-1])


#: :func:`hold_window`'s statement: the lock, waited for at most ``lock_timeout`` (the subquery
#: sets it first, for this statement's own transaction only).
_HOLD: Final = (
    "SELECT pg_advisory_lock_shared(%s) "
    "FROM (SELECT set_config('lock_timeout', %s, true)) AS deadline"
)
_RELEASE: Final = "SELECT pg_advisory_unlock_shared(%s)"
#: PostgreSQL ``lock_not_available``: ``lock_timeout`` ran out.
_LOCK_NOT_AVAILABLE: Final = "55P03"


def hold_window(db: Session, seconds: float) -> Connection:
    """Hold the partition maintenance lock shared for the session, before the caller's next
    transaction begins (the search, G8), waiting at most ``seconds`` (:class:`TimeoutError`).

    A REPEATABLE READ transaction takes its snapshot with its first statement: a lock taken in
    that transaction is granted after the snapshot, which then still has the cutoff and the
    reloads of before a drop the lock waited for, while the dropped partitions are gone (a partly
    dropped day read without ``archived``). So the lock is taken at session level, in a statement
    of its own (autocommit), on the connection of ``db``'s transaction, which the session keeps
    until its commit or rollback. :func:`release_window` must follow on every path, before the
    session gives the connection back to the pool."""
    connection = db.connection()
    raw = connection.connection.driver_connection
    if raw is None:  # pragma: no cover - a pooled connection always has one
        raise RuntimeError("no DBAPI connection")
    raw.autocommit = True
    try:
        raw.execute(_HOLD, (PARTITION_LOCK_KEY, str(max(1, int(seconds * 1000)))))
    except Exception as exc:
        if getattr(exc, "sqlstate", None) == _LOCK_NOT_AVAILABLE:
            raise TimeoutError(f"partition lock not granted within {seconds} s") from exc
        raise
    finally:
        if not raw.closed:  # a lost connection keeps the original error
            raw.autocommit = False
    return connection


def release_window(connection: Connection) -> None:
    """Release :func:`hold_window`'s lock, in the caller's transaction on ``connection`` before
    its rollback (a lock of the session is no part of a transaction; the session's rollback then
    ends the transaction and its savepoints as it knows them). When that fails — the transaction
    is aborted, the connection lost — the connection is invalidated: closing it releases the
    lock, and it never goes back to the pool holding it."""
    try:
        raw = connection.connection.driver_connection
        if raw is None:  # pragma: no cover - a pooled connection always has one
            raise RuntimeError("no DBAPI connection")
        raw.execute(_RELEASE, (PARTITION_LOCK_KEY,))
    except Exception:
        connection.invalidate()


def check_window(
    db: Session, layout: Layout, first_day: date, last_day: date, *, held: bool = False
) -> WindowCheck:
    """Which days of ``first_day`` … ``last_day`` are gone, and which of those the vault has.

    Takes the partition maintenance lock shared for the caller's transaction first (module text):
    the caller reads the days in the same transaction. ``held``: the caller holds it already,
    from before its transaction (:func:`hold_window`)."""
    if not held:
        db.execute(text("SELECT pg_advisory_xact_lock_shared(:key)"), {"key": PARTITION_LOCK_KEY})
    start = window_start(db, site_day(utc_now()))
    cutoff = dropped_before(db.connection())
    ready = _spans(_reloads(db, ("ready",)), timedelta(0))
    coming = _spans(_reloads(db, ("queued", "loading")), timedelta(0))
    replacing = _spans(_reloads(db, ("loading",)), 2 * MARGIN)
    seals = SealIndex(layout)
    short = _left_short(db, seals, first_day, last_day)
    days = days_of(first_day, last_day)
    gone: list[date] = []
    missing: list[date] = []
    waiting: list[date] = []
    for day in days:
        dropped = day in short or (
            day not in ready
            and ((cutoff is not None and day < cutoff) or (day < start and not _has_rows(db, day)))
        )
        if not dropped and day not in replacing:
            continue
        gone.append(day)
        if day in coming or not dropped:
            waiting.append(day)  # a load in progress or to come brings it back
        elif seals.files(day) is not None:
            missing.append(day)
    check = WindowCheck(start, tuple(gone), tuple(missing), tuple(waiting), len(gone) == len(days))
    reloadable = check.reloadable()
    if not reloadable:
        return check
    total = reload_bytes(layout, reloadable[0], reloadable[-1])
    return WindowCheck(
        start,
        check.gone,
        check.missing,
        check.waiting,
        check.all_gone,
        bytes_total=total,
        keep_days=reload_keep_days(db),
    )


def estimate(bytes_total: int) -> int:
    """The estimated database growth of loading ``bytes_total`` raw bytes (G5)."""
    return math.ceil(bytes_total * DB_BYTES_PER_RAW_BYTE)


def required_error(db: Session, check: WindowCheck) -> ApiError:
    """409 ``archive_reload_required`` with :class:`ArchiveReloadRequired` as ``details``."""
    required = check.required()
    if required is None:  # pragma: no cover - callers ask only when something is missing
        raise ValueError("nothing to reload")
    return archive_error(
        409,
        "archive_reload_required",
        details=required.model_dump(mode="json"),
        first_day=format_date(required.first_day),
        last_day=format_date(required.last_day),
        first_day_en=required.first_day.isoformat(),
        last_day_en=required.last_day.isoformat(),
        window=retention_days(db.connection()),
        size=format_bytes(required.estimated_bytes),
        size_en=format_bytes(required.estimated_bytes, "en"),
    )


# -- requests -------------------------------------------------------------------------------------


def to_model(row: ArchiveReload) -> ArchiveReloadOut:
    return ArchiveReloadOut(
        id=row.id,
        requested_by=row.requested_by,
        requested_at=row.requested_at,
        reason=row.reason,
        lookup_id=row.lookup_id,
        first_day=row.first_day,
        last_day=row.last_day,
        status=row.status,
        bytes_total=row.bytes_total,
        bytes_done=row.bytes_done,
        error_code=row.error_code,
        ready_at=row.ready_at,
        expires_at=row.expires_at,
    )


def _named(days: Iterable[date], *, english: bool) -> str:
    found = sorted(days)
    shown = [day.isoformat() if english else format_date(day) for day in found[:NAMED_DAYS]]
    more = len(found) - len(shown)
    if more:
        shown.append(f"+{more}")
    return ", ".join(shown)


def _seal_status(layout: Layout, day: date) -> str | None:
    """``status`` of ``state/seal/<day>.json`` (``None``: none, or it cannot be read)."""
    try:
        state = json.loads(layout.seal_state(day).read_bytes())
    except (OSError, ValueError):
        return None
    status = state.get("status") if isinstance(state, dict) else None
    return status if isinstance(status, str) else None


def check_in_vault(layout: Layout, first_day: date, last_day: date) -> None:
    """422 ``archive_day_not_in_vault`` naming the days of the request without a sealed
    manifest; a day the sealer skipped (no line that day) has nothing to load and passes."""
    sealed = vault_sizes(layout, first_day, last_day)
    unsealed = [
        day
        for day in days_of(first_day, last_day)
        if day not in sealed and _seal_status(layout, day) != "skipped"
    ]
    if unsealed:
        raise archive_error(
            422,
            "archive_day_not_in_vault",
            details={"days": [day.isoformat() for day in unsealed]},
            days=_named(unsealed, english=False),
            days_en=_named(unsealed, english=True),
        )


def database_drive(layout: Layout, data_dir: Path) -> tuple[str, int, int] | None:
    """``(label, total, used)`` of the newest disk sample of the PostgreSQL data drive
    (``state/system-history.json``, the worker's ``system.sample``); ``None`` without one.

    The installation keeps the PostgreSQL data folder under the data root (docs/KURULUM.md
    section 2: ``D:\\ARCILOGGER\\db``), so it is the data drive of the R20 disk check."""
    found = drives(data_dir)
    if not found:
        return None
    label = found[0][0]
    for entry in reversed(read_history(layout.system_history)):
        for drive in entry["drives"]:
            if drive["path"] == label and drive["total"] > 0:
                return label, int(drive["total"]), int(drive["used"])
    return None


def check_disk(layout: Layout, data_dir: Path, estimated: int) -> None:
    """422 ``archive_reload_disk_low`` when the load would leave less than
    :data:`MIN_FREE_PERCENT` of the database drive free; no sample, no refusal (G5)."""
    sample = database_drive(layout, data_dir)
    if sample is None:
        return
    label, total, used = sample
    free_after = total - used - estimated
    if free_after * 100 < total * MIN_FREE_PERCENT:
        raise archive_error(
            422,
            "archive_reload_disk_low",
            details={
                "drive": label,
                "total_bytes": total,
                "free_bytes": total - used,
                "estimated_bytes": estimated,
            },
            size=format_bytes(estimated),
            size_en=format_bytes(estimated, "en"),
            drive=label,
            percent=MIN_FREE_PERCENT,
        )


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        details=[{"loc": ["body", field], "type": "value_error", "msg": message}],
    )


def bytes_to_load(db: Session, first_day: date, files: dict[tuple[str, date], int]) -> int:
    """Σ ``bytes_uncompressed`` of the ``files`` of a new request (from ``first_day``) that
    ArciIngest would load: the files that do not count as loaded yet, by ingest's own rule
    (:meth:`arcilogger_ingest.service.IngestService.loaded_files`, read-only: a finished offset
    and no drop of the request's days since). It is checked again when the request is claimed;
    one loaded now and dropped by then is loaded after all, as with any estimate."""
    # No request has the number 0 (the serial starts at 1): no ``ready`` reload is left out.
    loaded = IngestService.loaded_files(db.connection(), 0, first_day, files)
    return sum(size for key, size in files.items() if key not in loaded)


def _existing(
    db: Session, user: CurrentUser, first_day: date, last_day: date
) -> ArchiveReload | None:
    """The active request (``loading``, then ``queued``, then ``ready``; the newest) of the user
    whose days overlap ``first_day`` … ``last_day``. Of an administrator as well: another user's
    request is never the answer (each person's request keeps its own record and audit entry,
    W5-G answer 6), although an administrator sees it (:func:`visible_reloads`)."""
    return db.scalars(
        select(ArchiveReload)
        .where(
            ArchiveReload.status.in_(ARCHIVE_RELOAD_ACTIVE),
            ArchiveReload.requested_by == user.username,
            ArchiveReload.first_day <= last_day,
            ArchiveReload.last_day >= first_day,
        )
        .order_by(
            case({"loading": 0, "queued": 1}, value=ArchiveReload.status, else_=2),
            ArchiveReload.id.desc(),
        )
        .limit(1)
    ).first()


def create_reload(
    db: Session,
    *,
    user: CurrentUser,
    body: ArchiveReloadRequest,
    layout: Layout,
    data_dir: Path,
    ip: str | None,
) -> ArchiveReload:
    """Check, audit and queue one request (module text); the caller commits."""
    if body.last_day < body.first_day:
        raise _invalid("last_day", "last_day is before first_day")
    if body.lookup_id is not None and body.reason != "lookup":
        raise _invalid("lookup_id", "only a lookup's reload names a lookup")
    days = (body.last_day - body.first_day).days + 1
    if days > MAX_DAYS:
        raise archive_error(
            422,
            "archive_reload_too_large",
            details={"days": days, "max_days": MAX_DAYS},
            days=days,
            max_days=MAX_DAYS,
        )
    check = check_window(db, layout, body.first_day, body.last_day)
    complete = [
        day
        for day in days_of(body.first_day, body.last_day)
        if day >= check.window_start and day not in check.gone
    ]
    if complete:
        raise _invalid(
            "first_day",
            f"in the database window, nothing to reload: {_named(complete, english=True)}",
        )
    existing = None if check.missing else _existing(db, user, body.first_day, body.last_day)
    if existing is not None:
        _audit(
            db,
            user,
            body,
            existing.id,
            existing.bytes_total,
            estimate(existing.bytes_total),
            ip=ip,
            existing=True,
        )
        return existing
    check_in_vault(layout, body.first_day, body.last_day)
    files = reload_files(layout, body.first_day, body.last_day)
    # ``bytes_total`` stays every file: ArciIngest counts the loaded ones as done at the claim
    # (``bytes_done`` from their sizes), so the progress runs from them to the whole.
    bytes_total = sum(files.values())
    estimated = estimate(bytes_to_load(db, body.first_day, files))
    check_disk(layout, data_dir, estimated)
    # The number first, so the audit entry names the request it precedes (G6).
    sequence = func.pg_get_serial_sequence(ArchiveReload.__tablename__, "id")
    reload_id = int(db.execute(select(func.nextval(sequence))).scalar_one())
    _audit(db, user, body, reload_id, bytes_total, estimated, ip=ip)
    db.flush()
    row = ArchiveReload(
        id=reload_id,
        requested_by=user.username,
        reason=body.reason,
        lookup_id=body.lookup_id,
        first_day=body.first_day,
        last_day=body.last_day,
        status="queued",
        bytes_total=bytes_total,
        bytes_done=0,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    return row


def _audit(
    db: Session,
    user: CurrentUser,
    body: ArchiveReloadRequest,
    reload_id: int,
    bytes_total: int,
    estimated: int,
    *,
    ip: str | None,
    existing: bool = False,
) -> None:
    """The audit entry ``archive.reload`` of a request (G6); ``estimated``: the database growth
    the disk check counted (:func:`bytes_to_load`)."""
    details: dict[str, Any] = {
        "archive_reload_id": reload_id,
        "reason": body.reason,
        "lookup_id": body.lookup_id,
        "first_day": body.first_day.isoformat(),
        "last_day": body.last_day.isoformat(),
        "days": (body.last_day - body.first_day).days + 1,
        "bytes_total": bytes_total,
        "estimated_bytes": estimated,
    }
    if existing:
        details["existing"] = True  # answered with the active request, nothing queued
    record_audit(
        db,
        actor=user,
        action="archive.reload",
        target=f"archive_reload:{reload_id}",
        details=details,
        ip=ip,
    )


def visible_reloads(db: Session, user: CurrentUser) -> list[ArchiveReload]:
    """The active requests (oldest first), then the :data:`RECENT` newest others."""
    query = select(ArchiveReload)
    if user.role != "admin":
        query = query.where(ArchiveReload.requested_by == user.username)
    active = ArchiveReload.status.in_(ARCHIVE_RELOAD_ACTIVE)
    rows = list(
        db.scalars(
            query.where(active).order_by(
                case({"loading": 0, "ready": 2}, value=ArchiveReload.status, else_=1),
                ArchiveReload.id,
            )
        )
    )
    rows += db.scalars(query.where(~active).order_by(ArchiveReload.id.desc()).limit(RECENT))
    return rows
