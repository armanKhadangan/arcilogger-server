"""The ingest service: spool files → parsers → PostgreSQL, exactly once, until stopped.

One loop in one thread (plus optional parse worker processes, :mod:`arcilogger_ingest.pool`):

1. **Discover** streams — one per ``(source, day)``: every ``spool/<source>/<day>.log`` (cheap
   directory listing every poll), every unfinished ``ingest_offsets`` row, and every source of
   a sealed day within the database window (``db.retention_days``, Wave 5 contract G1) that is
   not finished yet (vault scan once a minute; this is how a day whose spool file the sealer
   already deleted is still loaded, e.g. after the service was down over a weekend, and how a
   lost database gets the window back — only the window and the two raw days before it, whose
   rows reach into its first day (:data:`~arcilogger_db.partitions.RAW_DAY_MARGIN`): older days
   come back on request).
   The daily partitions of such a day are created before it is read
   (:func:`~arcilogger_db.partitions.ensure_days`), so its rows do not land in the default
   partition.
2. **Read** new complete lines, newest day first (live data before backlog); the sources of
   one day share each batch in equal parts, so one source's backlog does not hold the others
   back. From the spool file (opened and closed for every read, :mod:`arcilogger_ingest.files`)
   or, once the sealer has deleted it, from the sealed file (xz or gzip) at the same offset.
3. **Prepare** every line (:mod:`arcilogger_ingest.prepare`), in this process or in the pool.
4. **Load** the batch when it has ``[ingest] batch_rows`` lines or is ``batch_seconds`` old:
   rows, parse failures, sources and the new offsets in one transaction
   (:mod:`arcilogger_ingest.loader`). Only after ``COMMIT`` do the in-memory offsets move.
5. **Finish** a ``(source, day)`` when its day is sealed and the committed offset equals the
   sealed file's size (``bytes_uncompressed`` in the manifest).

**Archive reloads** (``archive_reloads``, Wave 5 contract G4: "arşivden yeniden yükle") are
carried out here, because this service is the only writer of log rows. Between two batches it
takes the oldest ``queued`` request (or resumes the ``loading`` one after a restart; one at a
time, the others stay ``queued``), in a transaction that holds the partition maintenance lock
(:data:`~arcilogger_db.partitions.PARTITION_LOCK_KEY`), so the window job ``db.window`` either
sees it as active or has finished its drop before anything is loaded. A request names
device-time days ``first_day`` … ``last_day`` (the days a lookup or search reads); their rows
come from the raw days ``first_day - 2`` … ``last_day + 2``
(:data:`~arcilogger_db.partitions.RAW_DAY_MARGIN`: a line received after midnight with a device
time before it, a device clock behind), so those files are loaded again: every day of the
request must be sealed (or skipped as empty), a day of the margin is loaded when it is sealed.
A file whose rows in the request's days are in the database already counts as loaded and is
left alone (no reset, no second load): its offset is finished and no drop has reached those
rows since — the window job never dropped from the request's first day or the file's margin
day on (``db.window_dropped_before``), or another ``ready`` reload whose files it is holds them
(the job keeps every day from an active reload's first day on). So another user's request of
days already loaded becomes ``ready`` with its own record (and audit entry) and no load.
For each other ``(source, day)`` it first checks the sealed file against the manifest (size and
SHA-256, the manifest read afresh): a file that is missing or changed fails the request before
anything of it is deleted. Then it resets that file, one file at a time (W5-G-25): one
transaction sets its ``ingest_offsets`` row to 0 and marks it :data:`RESETTING`, and deletes its
parse failures and clock samples (:meth:`IngestService._begin_reset`); then its rows (all
:data:`~arcilogger_db.models.TIME_SERIES_MODELS`, by ``(source, raw_day)``, wherever their
device times lie) are deleted in steps of device time, each a transaction of at most
:data:`RESET_STEP_LINES` lines between rounds of live data (about 0.1 s each instead of 5 to 8 s for
every 1 M lines whose rows are still there), each with the search counts of the hours it
finishes; the last one takes the mark off and deletes the counts left
(:meth:`IngestService._reset_step`). Each in the loader's step order (offsets, rows, failures,
counts), and each a compare-and-set on the offset: the file is no stream from before the first
one on, so no batch of it is read or committed under the mark. A marked file is never a stream
and never counts as loaded (its offset is 0): a start that finds one resets it again from its
first step, for its request or on its own, so a stop between two steps loads it exactly once all
the same. The checks read whole files
(the SHA-256 of every file at the claim, a file's receive times before its reset: about 0.5 s and
25 s for a 5 M line day), so they run in a thread beside the loop
(:meth:`IngestService._beside`), which goes on loading live data and acts on a check's result in
a later round, in the same order: nothing is deleted before its check has passed, and the size
check is made again on the loop right before the file's first transaction. The partitions of a
request's days are made one day a round, also while a check or a reset's steps run. What still
holds the loop up is one round of a reset's steps (:data:`RESET_ROUND_SECONDS` and the step
that ends it: 0.1 s, up to about 1 s on a slow disk; a lock waited for up to
:data:`RESET_LOCK_TIMEOUT`, as by its first transaction), one day's partitions (up to 0.4 s on
Linux; not measured on Windows; one table a transaction, so that a stop does not wait for the
day) and a wait for the partition lock while the window job or a search export holds it (half of
``search.request_budget_ms``, at most :data:`PARTITION_LOCK_TIMEOUT_MS`, for the lock and the
tables together; then none for ``archive_seconds``). A stop is looked at between these: none
is begun once it has come (W5-G-25). After its last step the file is an ordinary
stream: it is read from the sealed file by the reader above, after every newer day (live data
first), and loaded exactly once like any other. ``bytes_done`` is Σ committed offset; the
request is ``ready`` when every file has finished (``expires_at`` = ``ready_at`` +
``db.reload_keep_days``) and ``failed`` with an ``error_code`` when a day is no longer sealed in
the vault or a file stays unreadable. A file that becomes unreadable while it loads (changed in
place, a failing disk) stays short of its ``bytes_uncompressed``, so its day never counts as
loaded: the API answers 409 for the days its rows lie in until a later load finishes it.

If the database is unavailable the same batch is retried with capped exponential backoff
(0.5 s … 30 s); nothing more is read meanwhile, offsets do not move, and the spool files keep
everything (they are the durable queue). ``state/ingest-status.json`` is rewritten every few
seconds for the dashboard; a write that fails (on Windows the replace is refused while another
process reads the file) is tried again at the next round until it succeeds.

A changed ``[[sources]]`` in ``arcilogger.toml`` is taken without a restart (ADR-0014): between
two batches the service drops what it read but did not load (it is read again), re-resolves
every open stream for the new sources (offsets stay) and writes the sources, ``enabled``
included, to the ``sources`` table (a removed source keeps its row, disabled and no longer
expected). While the database is unavailable the file is looked at
before every retry, so a change is taken (and its SHA-256 reported) within one retry interval;
the batch waiting for the database is dropped and read again for the new sources, and the
``sources`` table follows once the database is back. A file that does not load keeps the
running sources; a change of a key ingest reads only at start-up is reported in the status
file's ``config`` block, which also names the SHA-256 of the file in use.

Only one instance works on a database at a time (:mod:`arcilogger_ingest.instance`): a second
one waits — and writes no status file — until the first stops (``--once``: exits with code 4).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterable
from concurrent.futures import Future
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from sqlalchemy import (
    Connection,
    Engine,
    and_,
    delete,
    func,
    not_,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_core import sealedfile
from arcilogger_core.config import Config
from arcilogger_core.configwatch import INGEST_HOT, INGEST_RESTART, ConfigWatch, ConfigWatcher
from arcilogger_core.layout import AUDIT_SOURCE, SPOOL_SUFFIX, Layout
from arcilogger_core.rawlog import RawLineError, received_at
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.clocksamples import ClockKey, clock_key, minute_of
from arcilogger_db.models import (
    TIME_SERIES_TABLES,
    ArchiveReload,
    Base,
    IngestOffset,
    Message,
    ParseFailure,
    SearchCount,
    Source,
    SourceClockSample,
)
from arcilogger_db.partitions import (
    RAW_DAY_MARGIN,
    LockBudget,
    PartitionLockTimeoutError,
    default_partition_name,
    dropped_before,
    ensure_days,
    lock_partitions,
    partitioned_tables,
    step_lock_timeout_ms,
)
from arcilogger_db.searchcounts import COUNTS_TIME_MARGIN, stream_window
from arcilogger_db.settings import DEFAULT_SETTINGS, get_setting
from arcilogger_ingest.files import VaultReader, VaultReadError, open_shared, read_complete
from arcilogger_ingest.instance import InstanceLock
from arcilogger_ingest.loader import (
    Batch,
    Loader,
    LoadResult,
    OffsetConflictError,
    OffsetUpdate,
    SourceSeen,
    StepHook,
)
from arcilogger_ingest.pool import PendingChunk, Preparer
from arcilogger_ingest.prepare import PATTERN_EXCEPTION, PreparedLine, StreamInfo
from arcilogger_ingest.sealed import SealIndex
from arcilogger_ingest.sources import SourceInfo, resolve_source
from arcilogger_ingest.status import StatusWriter

log = logging.getLogger("arcilogger.ingest")

Clock = Callable[[], datetime]
Key = tuple[str, date]

EXIT_OK: Final = 0
EXIT_ERROR: Final = 1
EXIT_CONFIG: Final = 2
EXIT_DB_UNAVAILABLE: Final = 3
EXIT_LOCKED: Final = 4

#: Line length assumed before a stream's own average is known (FortiGate lines are ~0.8 KB).
INITIAL_LINE_BYTES: Final = 1024
MIN_READ_BYTES: Final = 4096
MAX_READ_BYTES: Final = 8 * 1024 * 1024
#: Smallest chunk handed to a parse worker (smaller chunks cost more in process round trips).
MIN_WORKER_CHUNK_LINES: Final = 250
BACKOFF_FIRST: Final = 0.5
BACKOFF_MAX: Final = 30.0
#: How often a second instance checks whether the first one has stopped.
LOCK_RETRY_SECONDS: Final = 5.0
#: ``arcilogger.toml`` is looked at at most this often (a stat; it is read when it changed), so
#: new sources are taken within this plus one batch (contract: 5 s).
RELOAD_SECONDS: Final = 2.0
#: How often ``archive_reloads`` is looked at for a ``queued`` request (the web polls every 2 s).
ARCHIVE_POLL_SECONDS: Final = 5.0
#: ``bytes_done`` of the loading request is written at most this often.
ARCHIVE_PROGRESS_SECONDS: Final = 1.0
#: A file of the loading request whose reading keeps failing this long fails the request (a
#: shorter error, e.g. an antivirus scan holding the file, is retried like any vault read).
ARCHIVE_ERROR_SECONDS: Final = 60.0
#: ``error_code`` of a failed request: a day without a sealed manifest (the API's refusal of
#: the same name: it checks before queueing, but vault retention may remove the day meanwhile),
#: or a sealed file that cannot be read.
ARCHIVE_NOT_IN_VAULT: Final = "archive_day_not_in_vault"
ARCHIVE_UNREADABLE: Final = "archive_file_unreadable"
#: The longest wait of the loop for the partition maintenance lock and the tables it then locks
#: (a reload's claim, its partitions), in all: half of ``search.request_budget_ms`` as the
#: worker's steps (W5-G-24: a search queued behind the request keeps half its budget), but never
#: longer than this, the default's half, whatever the budget: a stop that comes during the wait
#: stays well within the 4.5 s of a Windows console stop
#: (:data:`~arcilogger_ingest.stopping.WINDOWS_GRACE_SECONDS`, W5-G-25). Ingest tries again
#: later instead of holding up live data (:meth:`IngestService._lock_budget`).
PARTITION_LOCK_TIMEOUT_MS: Final = 2000
LOCK_NOT_AVAILABLE: Final = "55P03"
#: The raw days around a request's days whose files are loaded with it (module text).
RELOAD_MARGIN: Final = timedelta(days=RAW_DAY_MARGIN)
#: Bytes read at a time when a sealed file is checked against its manifest's SHA-256 (and when
#: it is read for its receive times, :func:`received_outside`): a check beside the loop gives up
#: within one such slice once the service stops (:class:`CheckStoppedError`).
HASH_CHUNK: Final = 1 << 20
#: The name of the thread an archive reload's file check runs in (:meth:`IngestService._beside`).
CHECK_THREAD: Final = "arcilogger-archive-check"
#: The bytes of a spool line that name the minute it was received in, when its receive time is
#: as the collector writes it (``2026-09-23T09:38:26.380000+03:00 ``): ``line[:17] +
#: line[19:20] + line[26:33]``, the minute, the decimal point and the UTC offset.
STAMP_KEY: Final = re.compile(rb"\d{4}-\d\d-\d\dT\d\d:\d\d:\.[+-]\d\d:\d\d ")
#: The unit of a ``source_clock_samples`` key.
MINUTE: Final = timedelta(minutes=1)
#: The unit of ``search_counts``.
HOUR: Final = timedelta(hours=1)
#: ``ingest_offsets.counts_from_line`` of a file whose reset has begun and not ended (W5-G-25):
#: its offset is 0 and its rows are being deleted in steps (:meth:`IngestService._begin_reset`).
#: No unfinished file has it otherwise: a file loaded before ``0016_search`` is marked from line
#: 2 on. And it says what a reset needs said: the file's lines are not all in ``search_counts``
#: (a search counts their hours from the rows, ``searchcounts.verify`` leaves the file out) and
#: it is not finished (``search.counts_backfill`` waits for it). ``arcilogger-db search-counts
#: rebuild`` takes it off (it recounts the rows still there): a file at offset 0 whose counts are
#: not empty is a reset all the same (:meth:`IngestService._recounted`), and a step puts the mark
#: back.
RESETTING: Final = 0
#: Lines a step of a reset deletes at most, in a transaction of its own (W5-G-25), counted by the
#: ``search_counts`` of every file in its hours (what the step's deletes read: the ``fg_*`` tables
#: have no index by source): about 75 ms on plain PostgreSQL, 100 to 150 ms on compressed chunks.
RESET_STEP_LINES: Final = 10_000
#: The longest step in a file's time window when its counts were not complete at its reset
#: (``counts_from_line`` set: they do not tell its lines).
RESET_CAPPED_STEP: Final = timedelta(minutes=5)
#: A round of the loop makes steps of a reset for this long (at least one), then loads live data.
RESET_ROUND_SECONDS: Final = 0.25
#: A step waits at most this long for a lock (the compression policy recompressing a chunk it
#: deletes from, a recount holding the file's offsets row): it is made again in the next round,
#: live data loaded between.
RESET_LOCK_TIMEOUT: Final = "1s"
#: The order a reset deletes rows in: the loader's (``messages`` first).
RESET_TABLES: Final = sorted(TIME_SERIES_TABLES, key=lambda table: (table != "messages", table))


class CheckStoppedError(Exception):
    """An archive reload's file check gave up because the service is stopping (nothing is
    decided, the request is checked again at the next start) or its request is no longer loaded
    by this service."""


#: The stops of the archive reload's file check running in this thread
#: (:meth:`IngestService._beside`): the service's and its request's; none, as in a direct call,
#: a check never gives up.
_CHECK_STOP: ContextVar[tuple[threading.Event, ...]] = ContextVar("check_stop", default=())


def _check_stopped() -> None:
    """Between two slices of a file check: raise :class:`CheckStoppedError` once the service
    that runs it stops or the request it is for has ended."""
    if any(stop.is_set() for stop in _CHECK_STOP.get()):
        raise CheckStoppedError


def _lock_not_available(exc: DBAPIError) -> bool:
    """Whether a database error is a lock wait that ran past ``lock_timeout`` (55P03)."""
    original = exc.orig
    code = getattr(original, "sqlstate", None) or getattr(original, "pgcode", None)
    return code == LOCK_NOT_AVAILABLE


def count_lines(data: bytes) -> int:
    """Lines in a chunk (a final line without ``\\n`` counts)."""
    if not data:
        return 0
    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def first_lines(data: bytes, limit: int) -> int:
    """Length of the first ``limit`` lines of ``data`` (all of it when it has fewer)."""
    end = -1
    for _ in range(limit):
        end = data.find(b"\n", end + 1)
        if end < 0:
            return len(data)
    return end + 1


def _received_minute(line: bytes) -> datetime | None:
    """The UTC minute a spool line was received in; ``None`` when it has no usable receive time
    (it has no row either, :mod:`arcilogger_ingest.prepare`)."""
    try:
        return minute_of(received_at(line))
    except (RawLineError, ValueError, OverflowError):
        return None


def received_outside(path: Path, day: date) -> list[datetime]:
    """The minutes (UTC, oldest first) in which lines of the sealed file ``path`` of the site day
    ``day`` were received outside that day: a damaged or hand-restored spool line can carry a
    receive time of any other day, and its rows then lie within
    :data:`~arcilogger_db.searchcounts.COUNTS_TIME_MARGIN` of it instead of in
    ``stream_window`` (:meth:`IngestService._begin_reset`). The file is read whole, decompressed
    by :class:`VaultReader` like every sealed file (a receive time as the collector writes it is
    parsed once a minute, by :data:`STAMP_KEY`, any other line on its own); a line without a
    usable receive time has no row and is left out.
    Raises :class:`VaultReadError` or ``OSError`` when it cannot be read; beside the loop
    (:meth:`IngestService._beside`) :class:`CheckStoppedError` within one slice of
    :data:`HASH_CHUNK` once the service stops."""
    start, end = day_window(day)
    known: dict[bytes, datetime | None] = {}
    odd: set[bytes] = set()
    minutes: set[datetime | None] = set()

    def take(lines: list[bytes]) -> None:
        keys = {line[:17] + line[19:20] + line[26:33] for line in lines}
        for key in keys - known.keys() - odd:
            if STAMP_KEY.fullmatch(key):
                known[key] = _received_minute(key[:16] + b":00" + key[18:])
            else:
                odd.add(key)
        minutes.update(known[key] for key in keys if key in known)
        if not keys.isdisjoint(odd):
            minutes.update(
                _received_minute(line)
                for line in lines
                if line[:17] + line[19:20] + line[26:33] in odd
            )

    reader = VaultReader(path, 0)  # the one reader of sealed files, whatever their format
    try:
        while data := reader.read(HASH_CHUNK):
            take(data.removesuffix(b"\n").split(b"\n"))
            _check_stopped()
    finally:
        reader.close()
    return sorted(m for m in minutes if m is not None and not start <= m < end)


def _reach(minutes: Iterable[datetime]) -> list[tuple[datetime, datetime]]:
    """The device times the rows of lines received in ``minutes`` can have (within
    :data:`~arcilogger_db.searchcounts.COUNTS_TIME_MARGIN`), overlapping ranges joined."""
    ranges: list[tuple[datetime, datetime]] = []
    for minute in sorted(minutes):
        start, end = minute - COUNTS_TIME_MARGIN, minute + MINUTE + COUNTS_TIME_MARGIN
        if ranges and start <= ranges[-1][1]:
            ranges[-1] = (ranges[-1][0], end)
        else:
            ranges.append((start, end))
    return ranges


def _outside(
    ranges: Iterable[tuple[datetime, datetime]], lo: datetime, hi: datetime
) -> list[tuple[datetime, datetime]]:
    """The parts of ``ranges`` outside ``[lo, hi)``."""
    parts = []
    for start, end in ranges:
        parts += [
            part for part in ((start, min(end, lo)), (max(start, hi), end)) if part[0] < part[1]
        ]
    return parts


def reset_steps(
    start: datetime,
    end: datetime,
    lines: dict[datetime, int],
    *,
    budget: int,
    longest: timedelta | None = None,
) -> list[tuple[datetime, datetime]]:
    """The steps in which a reset deletes the device times ``[start, end)`` (W5-G-25), in order:
    each of at most ``budget`` lines by ``lines`` (by UTC hour; a step over part of an hour counts
    the hour's lines whole), an hour of more lines split evenly, and none longer than ``longest``
    (when given)."""
    parts: list[tuple[datetime, datetime, float]] = []
    hour = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    while hour < end:
        lo, hi = max(hour, start), min(hour + HOUR, end)
        here = lines.get(hour, 0)
        count = max(1, -(-here // budget), 0 if longest is None else -(-(hi - lo) // longest))
        width = (hi - lo) / count
        parts += [
            (lo + width * k, hi if k == count - 1 else lo + width * (k + 1), here / count)
            for k in range(count)
        ]
        hour += HOUR
    steps: list[tuple[datetime, datetime, float]] = []
    for lo, hi, share in parts:
        if steps:
            first, _, taken = steps[-1]
            if taken + share <= budget and (longest is None or hi - first <= longest):
                steps[-1] = (first, hi, taken + share)
                continue
        steps.append((lo, hi, share))
    return [(lo, hi) for lo, hi, _ in steps]


@dataclass
class Stream:
    """Progress of one ``(source, day)``: committed (in the database) and read (in the batch)."""

    info: StreamInfo
    source: SourceInfo
    offset: int = 0
    line: int = 0
    read_offset: int = 0
    read_line: int = 0
    in_db: bool = False
    origin: Literal["spool", "vault"] = "spool"
    vault: VaultReader | None = None
    #: Size of the file being read (spool) or of the sealed file (vault), for the lag.
    size: int | None = None
    #: Receive time of the last committed line (status: lag in seconds).
    last_received: datetime | None = None
    #: Average line length seen so far (sizes the reads so a batch gets ``batch_rows`` lines).
    line_bytes: float = INITIAL_LINE_BYTES
    finishing: bool = False
    error: str | None = None
    #: ``monotonic`` time since which the stream has had an error (an archive reload fails
    #: after :data:`ARCHIVE_ERROR_SECONDS`).
    error_since: float | None = None

    @property
    def key(self) -> Key:
        return (self.info.source, self.info.day)

    def rewind(self) -> None:
        """Forget what was read but not committed."""
        self.read_offset, self.read_line = self.offset, self.line
        self.finishing = False
        self.close()

    def close(self) -> None:
        if self.vault is not None:
            self.vault.close()
            self.vault = None


@dataclass
class Building:
    """A batch being read: its chunks (maybe still in a worker) and its offset updates."""

    batch: Batch = field(default_factory=Batch)
    pending: list[PendingChunk] = field(default_factory=list)
    lines: int = 0
    started: float | None = None

    @property
    def empty(self) -> bool:
        return not self.pending and self.batch.empty


@dataclass
class ArchiveJob:
    """The archive reload being loaded (``archive_reloads.status = 'loading'``)."""

    id: int
    first_day: date
    last_day: date
    #: Every file of the request's sealed days: ``bytes_uncompressed`` by ``(source, day)``.
    files: dict[Key, int]
    #: Files not reset yet, in order (one per round).
    pending: list[Key]
    #: Files that counted as loaded when the request was claimed (never reset).
    done: frozenset[Key] = frozenset()
    #: Set when a file changed after the claim checked it: no further file is reset, the
    #: request ends ``failed`` with it once the files already reset have loaded.
    error_code: str | None = None
    #: ``bytes_done`` as last written.
    written: int = 0
    next_progress: float = 0.0
    #: The check of the next file to reset, running beside the loop
    #: (:meth:`IngestService._reset_next`): that file, and the minutes its lines were received
    #: in outside its day (``None``: missing, changed or unreadable).
    scan: tuple[Key, Future[list[datetime] | None]] | None = None
    #: Set when the job ends (:meth:`IngestService._drop_job`): a scan of it still running gives
    #: up at its next slice.
    ended: threading.Event = field(default_factory=threading.Event)

    @property
    def bytes_total(self) -> int:
        return sum(self.files.values())


@dataclass
class PendingClaim:
    """A request being claimed (:meth:`IngestService._claim_archive`) while its files are checked
    beside the loop."""

    id: int
    first_day: date
    last_day: date
    #: ``status`` when it was read (``queued``, or ``loading`` after a restart).
    status: str
    #: Every file of the request (as :attr:`ArchiveJob.files`); ``None``: a day not in the vault.
    files: dict[Key, int] | None
    #: The files that count as loaded already (never checked, never reset).
    done: frozenset[Key]
    #: Whether every other file is intact (:meth:`IngestService._sealed_file_intact`); ``None``:
    #: nothing to check.
    check: Future[bool] | None
    #: Set when the request changed while its files were checked (:meth:`IngestService._watch`):
    #: the check gives up at its next slice.
    ended: threading.Event = field(default_factory=threading.Event)
    #: When the request's ``status`` is read again while the check runs (``monotonic``).
    next_look: float = 0.0
    #: Set when its files were checked but the partition lock was not free in time
    #: (:meth:`IngestService._finish_claim`): finished again from then on (``monotonic``), its
    #: files not checked again.
    retry_at: float | None = None


@dataclass(frozen=True)
class ResetStep:
    """A transaction of a reset (:meth:`IngestService._reset_step`): the file's rows of the
    device times ``[start, end)`` and its ``search_counts`` of the hours ``[hours[0], hours[1])``
    (those no later step of the reset reaches: their rows are gone with it)."""

    start: datetime
    end: datetime
    hours: tuple[datetime, datetime]


def count_hours(steps: list[tuple[datetime, datetime]]) -> list[ResetStep]:
    """``steps`` (:func:`reset_steps` of each range) with the hours whose counts each one
    deletes: the hours it reaches that no later step reaches (only a first or last hour can be
    shared, with a neighbouring range's step)."""
    hours: list[list[datetime]] = []
    last: dict[datetime, int] = {}
    for index, (start, end) in enumerate(steps):
        reached = []
        hour = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
        while hour < end:
            reached.append(hour)
            last[hour] = index
            hour += HOUR
        hours.append(reached)
    planned = []
    for index, (start, end) in enumerate(steps):
        own = [hour for hour in hours[index] if last[hour] == index]
        span = (own[0], own[-1] + HOUR) if own else (start, start)
        planned.append(ResetStep(start, end, span))
    return planned


@dataclass
class Reset:
    """A file whose rows are being deleted in steps (W5-G-25,
    :meth:`IngestService._reset_round`): its offset is 0 and its row marked
    :data:`RESETTING` until the last step, after which it is a stream."""

    key: Key
    #: The minutes its lines were received in outside its day (:func:`received_outside`): a
    #: reset begun again (:meth:`IngestService._reset_round`) reaches the same rows.
    strays: list[datetime]
    #: The transactions still to make, in order; ``None`` before its first transaction
    #: (:meth:`IngestService._begin_reset`), made again when its answer did not come.
    steps: list[ResetStep] | None = None


@dataclass
class SourceCounters:
    lines: int = 0
    rate: float = 0.0
    window_lines: int = 0


@dataclass
class Counters:
    lines: int = 0
    rows: int = 0
    failures: int = 0
    batches: int = 0
    rate: float = 0.0
    window_lines: int = 0
    window_started: float = field(default_factory=time.monotonic)
    per_source: dict[str, SourceCounters] = field(default_factory=dict)


class IngestService:
    def __init__(
        self,
        config: Config,
        engine: Engine,
        *,
        stop: threading.Event | None = None,
        clock: Clock = utc_now,
        workers: int = 0,
        status_interval: float = 5.0,
        vault_scan_seconds: float = 60.0,
        backoff_max: float = BACKOFF_MAX,
        step_hook: StepHook | None = None,
        single_instance: bool = True,
        config_path: Path | None = None,
        reload_seconds: float = RELOAD_SECONDS,
        archive_seconds: float = ARCHIVE_POLL_SECONDS,
        archive_error_seconds: float = ARCHIVE_ERROR_SECONDS,
    ) -> None:
        """``config_path``: the file ``config`` was loaded from, watched for new
        ``[[sources]]`` (None: no live reload, e.g. a test with a configuration of its own)."""
        self.config = config
        self.layout = Layout(config.data_dir)
        self.engine = engine
        self.loader = Loader(engine, step_hook=step_hook)
        self.stop = stop if stop is not None else threading.Event()
        #: Set when :meth:`run` has returned (the Windows console handler waits for it).
        self.stopped = threading.Event()
        self.workers = workers
        self._clock = clock
        self._status_interval = status_interval
        self._vault_scan_seconds = vault_scan_seconds
        self._backoff_max = backoff_max
        #: ``None`` only in tests of the compare-and-set safety net (two instances at once).
        self._lock = InstanceLock(engine) if single_instance else None
        #: Another instance holds the lock: this one reads nothing and writes no status file.
        self._locked_out = False
        self._lock_retry = min(backoff_max, LOCK_RETRY_SECONDS)
        self.seals = SealIndex(self.layout)
        self.streams: dict[Key, Stream] = {}
        self.finished: set[Key] = set()
        #: Streams whose ``prepare.exception`` was logged (once per stream, every line counted).
        self._exceptions_logged: set[Key] = set()
        self._vault_done: set[date] = set()
        self._unlistable: set[str] = set()
        self._next_vault_scan = 0.0
        self._retention_days = int(DEFAULT_SETTINGS["db.retention_days"])
        #: Days whose daily partitions this run has made sure of (``ensure_days``).
        self._partitioned: set[date] = set()
        #: No partitions are made before this (``monotonic``): the lock was not free in time.
        self._partitions_due = 0.0
        self._archive_seconds = archive_seconds
        self._archive_error_seconds = archive_error_seconds
        self._next_archive = 0.0
        self._archive_job: ArchiveJob | None = None
        #: The request whose files are being checked before it is claimed (no job meanwhile).
        self._claim: PendingClaim | None = None
        #: The last claim failed a request (the next one is taken at once).
        self._claim_failed = False
        #: The files whose rows are marked :data:`RESETTING` (offset 0, rows being deleted): never
        #: a stream until their reset ends.
        self.resetting: set[Key] = set()
        #: The reset whose steps are being made (one file at a time, whatever request it is for).
        self._reset: Reset | None = None
        #: The check of a file marked resetting that no request holds (its reset is finished on
        #: its own, :meth:`_resume_reset`), and when a file that could not be read is tried
        #: again (``monotonic``, by file: the others are not held up by it).
        self._orphan: tuple[Key, Future[list[datetime] | None]] | None = None
        self._orphan_due: dict[Key, float] = {}
        #: This round made a step of a reset: the loop does not wait before the next one.
        self._stepped = False
        #: Set when :meth:`run` ends: a file check still running beside it gives up (a fresh
        #: event for every run).
        self._halt = threading.Event()
        self._preparer: Preparer | None = None
        #: Batches being built, oldest first; only the first is loaded. With parse workers a
        #: second one is read ahead, so its lines are prepared while the first is loading.
        self._queue: list[Building] = [Building()]
        self.counters = Counters()
        self.state = "starting"
        self.db_ok = False
        self.db_error: str | None = None
        self.started_at = utc_now()
        self.status = StatusWriter(self.layout.ingest_status)
        self._next_status = 0.0
        self._watcher = (
            None
            if config_path is None
            else ConfigWatcher(
                config_path,
                config,
                hot=INGEST_HOT,
                restart=INGEST_RESTART,
                min_interval=reload_seconds,
            )
        )
        #: What the configuration watch last found (the status file's ``config`` block).
        self.config_watch: ConfigWatch | None = None
        if self._watcher is not None:
            self.config_watch = self._watcher.poll()  # the file as it is now
            self.config = self.config_watch.config
        #: New ``[[sources]]`` were taken but are not in the ``sources`` table yet.
        self._sources_unsynced = False

    # -- entry point --------------------------------------------------------------------------

    def run(self, *, once: bool = False) -> int:
        """Run until :attr:`stop` is set (``once``: until everything readable is loaded)."""
        settings = self.config.ingest
        try:
            log.info(
                "ingest started (pid %d, data %s, parse workers %d)",
                os.getpid(),
                self.layout.root,
                self.workers,
            )
            self._halt = threading.Event()
            # No status file yet: until the lock is ours it may belong to another instance.
            failed_start = self._start(once=once)
            if failed_start is not None:
                return failed_start
            self._preparer = Preparer(self.workers)
            self.state = "running"
            while not self.stop.is_set():
                self._reload()
                self._discover()
                if not self._archive() and once:
                    return EXIT_DB_UNAVAILABLE
                read = self._fill()
                if self._flush_due(read, once=once) and not self._flush(retry=not once):
                    if once:
                        return EXIT_LOCKED if self._locked_out else EXIT_DB_UNAVAILABLE
                    continue  # stop was requested while the database was down
                if read == 0 and self._batch_empty():
                    if once and not self._archive_busy():
                        break
                    self._write_status_if_due()
                    self._idle(settings.poll_seconds)
                elif read == 0:
                    self._idle(self._batch_wait())
                self._write_status_if_due()
            self._shutdown()
            return EXIT_OK
        except BaseException:
            self.state = "failed"  # the CLI logs the traceback and exits 1
            raise
        finally:
            self._halt.set()  # a file check beside the loop gives up; it is not waited for
            for stream in self.streams.values():
                stream.close()
            if self._preparer is not None:
                self._preparer.close()
            if self.state != "failed":
                self.state = "stopped"
            self._write_status()
            if self._lock is not None:
                self._lock.release()
            self.stopped.set()
            log.info("ingest stopped")

    def _archive_busy(self) -> bool:
        """An archive reload or a reset is under way (``--once`` runs until it is not). A claim
        that waits for the partition lock is not: ``--once`` ends, the next start claims it."""
        claim = self._claim if self._claim is not None and self._claim.retry_at is None else None
        return any(
            item is not None for item in (self._archive_job, claim, self._reset, self._orphan)
        )

    def _idle(self, seconds: float) -> None:
        """Wait before the next round, unless this round made a step of a reset: its next step
        comes in the next round (live data still between them)."""
        if not self._stepped:
            self.stop.wait(seconds)

    def parse_workers(self) -> dict[str, int | str | None]:
        """Configured and active parse workers, pool restarts, last pool error (status file)."""
        if self._preparer is None:
            return {"configured": self.workers, "active": 0, "restarts": 0, "last_error": None}
        return self._preparer.status()

    # -- start-up -----------------------------------------------------------------------------

    def _start(self, *, once: bool) -> int | None:
        """Lock, offsets, settings; ``None`` once started, otherwise the exit code."""
        attempt = 0
        while not self.stop.is_set():
            try:
                if not self._take_lock():
                    if once:
                        return EXIT_LOCKED
                    self.stop.wait(self._lock_retry)
                    continue
                self._load_offsets()
                self._load_retention()
                self._sync_configured_sources()
            except DBAPIError as exc:
                self._database_down(exc)
                if once:
                    return EXIT_DB_UNAVAILABLE
                self._backoff(attempt)
                attempt += 1
                self._reload()  # new sources are written to the table by the next attempt
                continue
            self._database_up()
            self._scan_vault()
            return None
        return EXIT_OK

    def _take_lock(self) -> bool:
        """Hold the single-instance lock; ``False`` while another instance holds it.

        Raises :class:`~sqlalchemy.exc.DBAPIError` when the database is unavailable.
        """
        if self._lock is None:
            return True
        if self._lock.acquire():
            if self._locked_out:
                log.info("the other ingest instance has stopped; this one takes over")
                self._locked_out = False
            return True
        if not self._locked_out:
            log.warning(
                "another ingest instance is running on this database (it holds the ingest "
                "lock); this one loads nothing while it runs"
            )
            self._locked_out = True
        return False

    def _lost_lock(self, *, retry: bool) -> bool:
        """Another instance took over while our lock connection was down.

        Everything read is dropped; with ``retry`` this waits until the lock is ours again and
        continues from the offsets in the database (the other instance moved them). Returns
        ``True`` when ingest can go on.
        """
        self._discard_batches()
        while retry and not self.stop.is_set():
            self.stop.wait(self._lock_retry)
            try:
                if self._take_lock():
                    self._load_offsets()
                    self._database_up()
                    return True
            except DBAPIError as exc:
                self._database_down(exc)
        return False

    def _load_offsets(self) -> None:
        """Positions from ``ingest_offsets`` (the only source of truth after a restart). A file
        marked :data:`RESETTING`, or whose mark a recount took off (:meth:`_recounted`), is not a
        stream: its reset is finished first (:meth:`_archive`)."""
        o = IngestOffset
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    o.source, o.day, o.byte_offset, o.line_number, o.finished_at, o.counts_from_line
                )
            ).all()
            recounted = self._recounted(
                connection,
                [
                    (source, day)
                    for source, day, offset, line, finished_at, counts_from_line in rows
                    if (offset, line, finished_at, counts_from_line) == (0, 0, None, None)
                ],
            )
        for stream in self.streams.values():
            stream.close()
        self.streams.clear()
        self.finished.clear()
        self.resetting.clear()
        self._vault_done.clear()
        # A reset whose first transaction may not have committed (its answer lost) is made again.
        begun = None if self._reset is None or self._reset.steps is not None else self._reset.key
        if begun is not None:
            self.resetting.add(begun)
        for source, day, offset, line, finished_at, counts_from_line in rows:
            key = (source, day)
            if key == begun:
                continue
            if finished_at is not None:
                self.finished.add(key)
                continue
            if counts_from_line == RESETTING or key in recounted:
                self.resetting.add(key)
                continue
            stream = self._new_stream(source, day)
            stream.offset = stream.read_offset = offset
            stream.line = stream.read_line = line
            stream.in_db = True
            self.streams[key] = stream
        if self._reset is not None and self._reset.key not in self.resetting:
            self._reset = None  # ended meanwhile, by another instance (the lock was lost)
        if self._orphan is not None and self._orphan[0] not in self.resetting:
            self._orphan = None

    @staticmethod
    def _recounted(connection: Connection, keys: list[Key]) -> set[Key]:
        """The files of ``keys`` (unfinished, at offset 0, not marked :data:`RESETTING`) that
        still have ``search_counts``: rows of theirs are still there, so a reset of theirs has not
        ended (W5-G-25). A file loads its rows and their counts in one transaction with its
        offset, and its reset's last step deletes the counts with the mark, so only a recount
        (``arcilogger-db search-counts rebuild``, which counts the rows still there in
        ``stream_window`` and takes the mark off) leaves such a file. A reset deletes that
        window last, after the rows around its stray receive times: a recount that finds none
        of its rows there finds no row of it left (but for a file whose every line was received
        outside its day)."""
        if not keys:
            return set()
        sc = SearchCount
        found = connection.execute(
            select(sc.source, sc.raw_day).where(tuple_(sc.source, sc.raw_day).in_(keys)).distinct()
        )
        return {(source, day) for source, day in found}

    def _load_retention(self) -> None:
        with Session(self.engine) as session:
            value = get_setting(session, "db.retention_days")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            self._retention_days = value

    def _sync_configured_sources(self) -> None:
        """Every configured exact source has a ``sources`` row, even before its first line, with
        its configured values (``enabled = false``: R15 and the dashboard leave it out).

        A row whose source is no longer configured (removed in the browser or by hand; neither
        an ``unknown-<ip>`` sender nor a member of a configured network) is kept for its
        history with ``enabled = false, expected = false``, in the same transaction: R15, the
        dashboard and the lease watermark stop waiting for it.

        The rows are written one after the other in name order, the order of the loader's step
        ``sources``: a second instance starting beside a running load (lock switched off, or
        the moment the lock cannot cover) waits for it instead of deadlocking with it."""
        configured = {
            source.name: source for source in self.config.sources if not source.is_network
        }
        with self.engine.begin() as connection:
            watched = connection.scalars(
                select(Source.name).where(
                    Source.unknown.is_(False), Source.enabled.is_(True) | Source.expected.is_(True)
                )
            ).all()
            removed = [name for name in watched if not self._configured(name)]
            for name in sorted(configured.keys() | set(removed)):  # fixed lock order
                source = configured.get(name)
                if source is None:
                    connection.execute(
                        update(Source)
                        .where(Source.name == name)
                        .values(enabled=False, expected=False)
                    )
                    continue
                values = {
                    "type": source.type,
                    "address": source.address,
                    "enabled": source.enabled,
                    "expected": source.expected,
                    "heartbeat_minutes": source.heartbeat_minutes,
                    "unknown": False,
                }
                statement = insert(Source).values(name=name, **values)
                connection.execute(
                    statement.on_conflict_do_update(index_elements=["name"], set_=values)
                )
            if removed:
                log.info("sources no longer configured: %s", ", ".join(sorted(removed)))

    def _configured(self, name: str) -> bool:
        """A configured exact source, a member of a configured network or an unknown sender."""
        info = resolve_source(self.config, name)
        return info.config is not None or info.unknown or info.configured_type is not None

    # -- live reload of [[sources]] (ADR-0014) --------------------------------------------------

    def _reload(self) -> bool:
        """Between two batches, and before each retry while the database is unavailable: take
        a changed ``[[sources]]`` of the configuration file; True when new sources were taken.

        What was read but not loaded is dropped and read again (the spool is the queue), so
        every line is prepared with the sources in use when it is loaded; the open streams are
        resolved again with their committed offsets, and the ``sources`` table follows (retried
        before the next batch while the database is unavailable)."""
        if self._watcher is None:
            return False
        watch = self._watcher.poll()
        before, self.config_watch = self.config_watch, watch
        if before is None or before.status_block() != watch.status_block():
            self._next_status = 0.0  # the browser administration waits for the new SHA-256
        if watch.changed:
            self._discard_batches()
            self.config = watch.config
            self._resolve_streams()
            self._sources_unsynced = True
            log.info(
                "sources reloaded (%d configured, configuration %s)",
                len(self.config.sources),
                (watch.sha256 or "?")[:12],
            )
        if self._sources_unsynced:
            try:
                self._sync_configured_sources()
            except DBAPIError as exc:
                self._database_down(exc)
                return watch.changed
            self._sources_unsynced = False
        return watch.changed

    def _resolve_streams(self) -> None:
        """Every open stream as the configuration now describes its folder (parser type,
        expected, heartbeat); the offsets do not move."""
        for stream in self.streams.values():
            source = resolve_source(self.config, stream.info.source)
            stream.source = source
            stream.info = StreamInfo(stream.info.source, source.configured_type, stream.info.day)

    # -- discovery ----------------------------------------------------------------------------

    def _new_stream(self, source: str, day: date) -> Stream:
        info = resolve_source(self.config, source)
        return Stream(info=StreamInfo(source, info.configured_type, day), source=info)

    def _add_stream(self, source: str, day: date) -> None:
        key = (source, day)
        if key not in self.finished and key not in self.streams and key not in self.resetting:
            self.streams[key] = self._new_stream(source, day)

    def _discover(self) -> None:
        for folder in self._list(self.layout.spool):
            if not folder.is_dir() or folder.name == AUDIT_SOURCE or folder.name.startswith("."):
                continue
            for file in self._list(Path(folder.path)):
                if not file.name.endswith(SPOOL_SUFFIX):
                    continue
                try:
                    day = date.fromisoformat(file.name.removesuffix(SPOOL_SUFFIX))
                except ValueError:
                    continue
                self._add_stream(folder.name, day)
        if time.monotonic() >= self._next_vault_scan:
            self._refresh_retention()
            self._scan_vault()

    def _list(self, folder: Path) -> list[os.DirEntry[str]]:
        """Directory entries; a folder that cannot be listed now is skipped (and logged once).

        On Windows an antivirus scan or a wrong ACL can make a listing fail; the service keeps
        loading everything else and tries again at the next poll.
        """
        try:
            entries = list(os.scandir(folder))
        except FileNotFoundError:
            return []
        except OSError as exc:
            if str(folder) not in self._unlistable:
                log.warning("cannot list %s: %s", folder, exc)
                self._unlistable.add(str(folder))
            return []
        self._unlistable.discard(str(folder))
        return entries

    def _refresh_retention(self) -> None:
        """An administrator may change ``db.retention_days`` while the service runs."""
        try:
            self._load_retention()
        except DBAPIError as exc:  # the loader reports database problems; keep the last value
            log.debug("db.retention_days not refreshed: %s", exc)

    def _scan_vault(self) -> None:
        """Sources of sealed days (within the database window) that are not finished.

        The window and its two raw days before, not more: after a database loss the days
        before it stay in the vault until someone asks for them (archive reload). The
        partitions of a day to load are created first; when that cannot be done now (table lock
        busy, database away) its rows go to the default partition, and the worker's partition
        run moves them later."""
        self._next_vault_scan = time.monotonic() + self._vault_scan_seconds
        # The raw days whose rows reach into the window's first day count too (module text).
        horizon = self._today() - timedelta(days=self._retention_days) - RELOAD_MARGIN
        for day in self.seals.sealed_days(horizon):
            if day in self._vault_done:
                continue
            files = self.seals.files(day) or {}
            pending = [source for source in files if (source, day) not in self.finished]
            if not pending:
                self._vault_done.add(day)
                continue
            try:
                self._ensure_days(day, day)
            except DBAPIError as exc:  # the loader reports database problems
                log.debug("partitions of %s not created: %s", day, exc)
            for source in pending:
                self._add_stream(source, day)

    def _ensure_days(self, first_day: date, last_day: date) -> bool:
        """The daily partitions of the site days ``first_day`` … ``last_day`` (plain PostgreSQL;
        nothing to do with TimescaleDB), one day a call: up to 0.4 s each, so between two batches
        of live data the loop makes sure of one day, not of the 22 of a long archive reload
        (4.7 s). One table a transaction (its device times lie in two UTC days: two partitions,
        about 30 ms), the stop looked at before each: a stop waits for one table's, not for the
        day's (several seconds on the Windows CI runners). ``False`` until every day is done,
        when the service stops, or when the partition lock and the tables were not free within
        :meth:`_lock_budget` for the call (a search export holds the lock): then none is made for
        the next ``archive_seconds``, live data loaded meanwhile (while the loop waits for the
        lock, searches queue behind it). :attr:`_partitioned` keeps the days done; other database
        errors are raised."""
        days = [first_day + timedelta(days=n) for n in range((last_day - first_day).days + 1)]
        missing = [day for day in days if day not in self._partitioned]
        if not missing:
            return True
        if time.monotonic() < self._partitions_due:
            return False
        day = missing[0]
        with self.engine.connect() as connection:
            tables = partitioned_tables(connection)
            budget = self._lock_budget(connection)
        created = 0
        for table in tables:
            if self.stop.is_set():
                return False
            try:
                with self.engine.begin() as connection:
                    report = ensure_days(connection, day, day, tables=[table], budget=budget)
            except PartitionLockTimeoutError as exc:
                log.info("partitions of %s: %s, trying again later", day, exc)
                self._partitions_due = time.monotonic() + self._archive_seconds
                return False
            created += len(report.created)
        if created:
            log.info("partitions of %s created (%d)", day, created)
        self._partitioned.add(day)
        return len(missing) == 1

    @staticmethod
    def _lock_budget(connection: Connection) -> LockBudget:
        """How long the loop waits for the partition maintenance lock and then for the tables, in
        all (W5-G-24): half of ``search.request_budget_ms``, as a step of the worker (a search
        that queues behind the request keeps half its budget), at most
        :data:`PARTITION_LOCK_TIMEOUT_MS`."""
        return LockBudget(min(PARTITION_LOCK_TIMEOUT_MS, step_lock_timeout_ms(connection)))

    def _today(self) -> date:
        return site_day(self._clock())

    # -- archive reloads (Wave 5 contract G4) -------------------------------------------------

    def _archive(self) -> bool:
        """Between two batches: make the next steps of the reset under way
        (:meth:`_reset_round`), reset the next file of the loading archive reload, write its
        progress or end it, finish the claim of a request whose files have been checked (or give
        it up when the request changed meanwhile, :meth:`_watch`), take the next queued request
        (at once after one ended), and else finish the reset of a file no request holds
        (:meth:`_resume_reset`). It never waits for a file check (:meth:`_beside`): the loop
        loads live data meanwhile and looks at the check again in the next round. Once the
        service stops, nothing that waits for a lock is begun (a claim, a reset's first
        transaction): the next start carries it on. ``False`` when the database was unavailable
        (everything is tried again next round)."""
        self._stepped = False
        try:
            if self._reset is not None:
                self._reset_round()
            job = self._archive_job
            if job is not None:
                if job.pending:
                    self._reset_next(job)
                self._archive_progress(job)
            claim = self._claim
            if claim is not None and claim.check is not None and not claim.check.done():
                self._watch(claim)
            claim = self._claim
            if (
                self._archive_job is None
                and claim is not None
                and (claim.check is None or claim.check.done())
                and (claim.retry_at is None or claim.retry_at <= time.monotonic())
                and not self.stop.is_set()
                and self._take_lock()
            ):
                self._claim_failed = False
                self._archive_job = self._finish_claim(claim)
                if self._claim_failed:  # the next request at once
                    self._next_archive = 0.0
            if (
                self._archive_job is None
                and self._claim is None
                and self._orphan is None
                and time.monotonic() >= self._next_archive
                and not self.stop.is_set()
            ):
                self._next_archive = time.monotonic() + self._archive_seconds
                # A request that fails at once (not in the vault, a file changed and found so
                # by a quick check) is followed by the next one in the same round.
                self._claim_failed = True
                while (
                    self._archive_job is None
                    and self._claim is None
                    and self._claim_failed
                    and self._take_lock()
                ):
                    self._claim_failed = False
                    self._archive_job = self._claim_archive()
            if self._archive_job is None and self._claim is None and self._reset is None:
                self._resume_reset()
        except DBAPIError as exc:
            self._database_down(exc)
            return False
        return True

    def _watch(self, claim: PendingClaim) -> None:
        """While a claim's files are checked: its request's ``status``, read every
        :data:`ARCHIVE_PROGRESS_SECONDS`; once it has changed (by hand), the check gives up at its
        next slice and the next request is taken at once. A change after the check has ended is
        found by :meth:`_finish_claim`, which reads the request again under the lock."""
        now = time.monotonic()
        if now < claim.next_look:
            return
        claim.next_look = now + ARCHIVE_PROGRESS_SECONDS
        r = ArchiveReload
        with self.engine.connect() as connection:
            status = connection.execute(select(r.status).where(r.id == claim.id)).scalar()
        if status != claim.status:
            log.warning(
                "archive reload %d is no longer %s: its check stops", claim.id, claim.status
            )
            claim.ended.set()
            self._claim = None
            self._next_archive = 0.0

    def _claim_archive(self) -> ArchiveJob | None:
        """Start claiming the ``loading`` request (this service stopped while loading it: every
        file not loaded already is reset again, which loads the same rows) or else the oldest
        ``queued`` one.

        The files that count as loaded already (:meth:`loaded_files`) are left alone; every
        other file it would load is checked against its manifest first
        (:meth:`_sealed_file_intact`, outside any transaction and beside the loop,
        :meth:`_beside`: it reads them whole, about 0.5 s for a 5 M line day), then
        :meth:`_finish_claim` moves the request on, in this round when there is nothing to check
        (or the check was quick), else in a later one. ``None`` while the files are checked, or
        as :meth:`_finish_claim`."""
        r = ArchiveReload
        with self.engine.connect() as connection:
            row = connection.execute(
                select(r.id, r.first_day, r.last_day, r.status).where(r.status == "loading")
            ).first()
            if row is None:
                row = connection.execute(
                    select(r.id, r.first_day, r.last_day, r.status)
                    .where(r.status == "queued")
                    .order_by(r.id)
                    .limit(1)
                ).first()
        if row is None:
            return None
        reload_id, first_day, last_day, status = row
        files = self._request_files(reload_id, first_day, last_day)
        done: frozenset[Key] = frozenset()
        check: Future[bool] | None = None
        ended = threading.Event()
        if files is not None:
            with self.engine.connect() as connection:
                done = self.loaded_files(connection, reload_id, first_day, files)
            unchecked = sorted(files.keys() - done)
            if unchecked:  # the first file missing or changed ends the check
                check = self._beside(lambda: self._files_intact(unchecked), ended=ended)
        self._claim = PendingClaim(
            reload_id, first_day, last_day, status, files, done, check, ended=ended
        )
        return self._finish_claim(self._claim)

    def _finish_claim(self, claim: PendingClaim) -> ArchiveJob | None:
        """The claim of a request once its files are checked (:meth:`_claim_archive`): a day of
        the request that is not sealed (``archive_day_not_in_vault``) or a file missing or
        changed (``archive_file_unreadable``) fails the request before anything is deleted.
        Otherwise one transaction under the partition maintenance lock (the window job holds it
        while it reads the reloads and drops: it either saw this request as active, or its drop
        is done before a file is reset) finds the files loaded at the claim still loaded and
        moves it on; a file loaded since (a stream that finished meanwhile) is left alone too,
        not checked again. ``None`` while the check runs, when it failed, when the request or
        one of its loaded files changed meanwhile, when the check gave up (the service stops) or
        when the lock or the request's row is not free within :meth:`_lock_budget` (a search
        export or the window job holds it): the request is not failed, the claim is kept with its
        check and finished again after ``archive_seconds`` (:attr:`PendingClaim.retry_at`; the
        status and the loaded files are read again under the lock then), so its files are not
        read again while an export runs."""
        if claim.check is not None and not claim.check.done():
            return None
        self._claim = None
        reload_id, first_day, last_day = claim.id, claim.first_day, claim.last_day
        status, files, done = claim.status, claim.files, claim.done
        error = ARCHIVE_NOT_IN_VAULT if files is None else None
        if claim.check is not None:
            try:
                intact = claim.check.result()
            except CheckStoppedError:  # a stop of an earlier run: claimed afresh at once
                self._next_archive = 0.0
                return None
            if not intact:
                error = ARCHIVE_UNREADABLE
        r = ArchiveReload
        try:
            with self.engine.begin() as connection:
                budget = self._lock_budget(connection)
                lock_partitions(connection, budget=budget)
                budget.bound(connection)  # the request's row as well
                current = connection.execute(
                    select(r.status).where(r.id == reload_id).with_for_update()
                ).scalar_one_or_none()
                if current != status:  # changed meanwhile (by hand): looked at again next poll
                    return None
                if files is None or error is not None:
                    log.warning("archive reload %d failed: %s", reload_id, error)
                    self._claim_failed = True
                    values: dict[str, Any] = {"status": "failed", "error_code": error}
                    if files is not None:
                        values["bytes_total"] = sum(files.values())
                    connection.execute(update(r).where(r.id == reload_id).values(**values))
                    return None
                now = self.loaded_files(connection, reload_id, first_day, files)
                if not done <= now:
                    return None  # a drop since the check: looked at again next poll
                # A file loaded meanwhile (a stream that finished during the check) is left
                # alone as well; every other one was checked.
                done = now
                job = ArchiveJob(
                    reload_id,
                    first_day,
                    last_day,
                    files,
                    pending=sorted(files.keys() - done),
                    done=done,
                    written=sum(files[key] for key in done),
                )
                connection.execute(
                    update(r)
                    .where(r.id == reload_id)
                    .values(status="loading", bytes_total=job.bytes_total, bytes_done=job.written)
                )
        except (DBAPIError, PartitionLockTimeoutError) as exc:
            if isinstance(exc, DBAPIError) and not _lock_not_available(exc):
                raise
            log.info(
                "archive reloads: the partition lock is held (the window job, a search export), "
                "trying again later"
            )
            claim.retry_at = time.monotonic() + self._archive_seconds
            self._claim = claim
            return None
        # The window job may have dropped them since this run made sure of them.
        first, last = self._reload_partition_days(job)
        self._partitioned.difference_update(
            first + timedelta(days=n) for n in range((last - first).days + 1)
        )
        log.info(
            "archive reload %d: %s … %s, %d file(s) (%d loaded already), %d bytes",
            job.id,
            job.first_day,
            job.last_day,
            len(job.files),
            len(job.done),
            job.bytes_total,
        )
        return job

    @staticmethod
    def loaded_files(
        connection: Connection, reload_id: int, first_day: date, files: dict[Key, int]
    ) -> frozenset[Key]:
        """The files of a request (``reload_id``, from ``first_day``) that count as loaded:
        a second load would bring back no row of the request's days (W5-G answer 6).

        A file is loaded when its offset is finished at its ``bytes_uncompressed`` (a file a
        failed reload left short is not) and no drop has reached its rows of the request's days
        since: they lie from the site day ``max(day - RAW_DAY_MARGIN, first_day)`` on (its own
        rows of earlier days are not the request's to keep: the window job may drop them, as it
        may those of a file loaded for it). That holds when the newest cutoff ever applied
        (``db.window_dropped_before``) is not after that day — nothing from it on was ever
        dropped; without that record (nothing dropped yet, or a database restored without it)
        this is not known — or when another ``ready`` reload starts on or before it and the file
        is one of its raw days: that reload loaded it (or found it loaded) and kept every day
        from its first day on since, as the window job keeps the days of an active reload
        (``expired`` is set by the job before it drops). A row of the file far outside its days
        (a spool line with a stray receive time) is not looked at here. The API's disk estimate
        of a new request calls it too, read-only, with ``reload_id`` 0 (no request has it, so no
        ``ready`` reload is left out; ``arcilogger_api.archive.service.bytes_to_load``)."""
        r, o = ArchiveReload, IngestOffset
        days = sorted({day for _, day in files})
        finished = [
            (source, day)
            for source, day, offset in connection.execute(
                select(o.source, o.day, o.byte_offset).where(
                    o.day.in_(days), o.finished_at.is_not(None)
                )
            )
            if files.get((source, day)) == offset
        ]
        if not finished:
            return frozenset()
        cutoff = dropped_before(connection)
        holders = connection.execute(
            select(r.first_day, r.last_day).where(r.status == "ready", r.id != reload_id)
        ).all()
        loaded = set()
        for source, day in finished:
            kept_from = max(day - RELOAD_MARGIN, first_day)
            never_dropped = cutoff is not None and cutoff <= kept_from
            if never_dropped or any(
                held <= kept_from and held - RELOAD_MARGIN <= day <= last + RELOAD_MARGIN
                for held, last in holders
            ):
                loaded.add((source, day))
        return frozenset(loaded)

    def _request_files(
        self, reload_id: int, first_day: date, last_day: date
    ) -> dict[Key, int] | None:
        """``bytes_uncompressed`` of every file a request loads: the sealed raw days of its days
        and of the margin around them (module text); ``None`` when a day of the request itself
        is neither sealed nor skipped as empty. A margin day without a seal is left as it is (not
        sealed yet: the window job keeps its rows; not in the vault: nothing to load)."""
        files: dict[Key, int] = {}
        day = first_day - RELOAD_MARGIN
        while day <= last_day + RELOAD_MARGIN:
            sealed = self.seals.files(day)
            if sealed is not None:
                files.update({(source, day): f.bytes_uncompressed for source, f in sealed.items()})
            elif first_day <= day <= last_day and self._seal_status(day) != "skipped":
                log.warning("archive reload %d: %s is not sealed in the vault", reload_id, day)
                return None
            day += timedelta(days=1)
        return files

    def _seal_status(self, day: date) -> str | None:
        """``status`` of ``state/seal/<day>.json`` (``None``: none, or it cannot be read)."""
        try:
            state = json.loads(self.layout.seal_state(day).read_bytes())
        except (OSError, ValueError):
            return None
        status = state.get("status") if isinstance(state, dict) else None
        return status if isinstance(status, str) else None

    @staticmethod
    def _reload_partition_days(job: ArchiveJob) -> tuple[date, date]:
        """The days whose partitions the request's files can write to: their raw days and a
        margin more (module text)."""
        return job.first_day - 2 * RELOAD_MARGIN, job.last_day + 2 * RELOAD_MARGIN

    def _files_intact(self, keys: list[Key]) -> bool:
        """The claim's check (beside the loop): whether every file of ``keys`` is
        :meth:`_sealed_file_intact`, the first that is not ending it. The stop is looked at before
        each file too, not only between two slices of one: a check whose service has stopped
        opens no further file, so that it ends at once and never outlives its service by a
        file's read."""
        for key in keys:
            _check_stopped()
            if not self._sealed_file_intact(*key):
                return False
        return True

    def _sealed_file_intact(self, source: str, day: date, *, digest: bool = True) -> bool:
        """Whether the sealed file of ``(source, day)`` is in the vault as its manifest — read
        afresh, not from :class:`SealIndex`'s cache — describes it: its size and (``digest``)
        SHA-256. Checked before the file's rows are deleted, so a reload never deletes rows it
        could not load again. Run beside the loop (:meth:`_beside`), it raises
        :class:`CheckStoppedError` within one slice of :data:`HASH_CHUNK` once the service
        stops."""
        try:
            state = json.loads(self.layout.seal_state(day).read_bytes())
            manifest = json.loads(self.layout.manifest(day).read_bytes())
            if state.get("status") != "sealed":
                return False
            entry = next(e for e in manifest["files"] if e["source"] == source)
            path = self.layout.vault / str(entry["path"])
            if path.stat().st_size != int(entry["bytes"]):
                return False
            if not digest:
                return True
            sha256 = hashlib.sha256()
            with open_shared(path) as handle:  # the file as stored: compressed bytes
                while chunk := handle.read(HASH_CHUNK):
                    sha256.update(chunk)
                    _check_stopped()
            return sha256.hexdigest() == str(entry["sha256"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
            return False

    def _beside[T](
        self, check: Callable[[], T], *, ended: threading.Event | None = None
    ) -> Future[T]:
        """Run ``check``, an archive reload's reading of sealed files (the claim's SHA-256, the
        reset's :func:`received_outside`: 0.5 s and 25 s for a 5 M line day), in a daemon thread
        of its own, so that the loop goes on loading live data (Wave 5 contract G4: live data first)
        and looks at the result in a later round. The thread only reads files: every decision
        and every transaction stays with the loop, in W5-G's order.

        A daemon thread and a plain :class:`~concurrent.futures.Future`, not an executor, whose
        threads are joined when the interpreter exits: that would hold up a stop on Windows
        (:data:`~arcilogger_ingest.stopping.WINDOWS_GRACE_SECONDS`) by a whole file's read. The
        check gives up within one slice of :data:`HASH_CHUNK` once :meth:`run` ends or ``ended``
        is set (its request is no longer this service's: :meth:`_drop_job`), raising
        :class:`CheckStoppedError`, and is never waited for."""
        future: Future[T] = Future()
        future.set_running_or_notify_cancel()
        stops = (self._halt,) if ended is None else (self._halt, ended)

        def run() -> None:
            _CHECK_STOP.set(stops)
            try:
                future.set_result(check())
            except BaseException as exc:  # raised in the loop, which looks at the result
                future.set_exception(exc)

        threading.Thread(target=run, name=CHECK_THREAD, daemon=True).start()
        return future

    def _file_strays(
        self, what: str, source: str, day: date, path: Path | None
    ) -> list[datetime] | None:
        """The check of a file before its reset (beside the loop): the minutes its lines were
        received in outside its day (:func:`received_outside`) when its sealed file ``path`` is
        still there as its manifest describes it (by size: the claim read it whole); ``None``
        when it is missing, changed or cannot be read. ``what`` names the reset in the log. A
        check that starts after its service stopped reads nothing (:func:`_check_stopped`)."""
        _check_stopped()
        if path is None or not self._sealed_file_intact(source, day, digest=False):
            return None
        try:
            return received_outside(path, day)
        except (VaultReadError, OSError) as exc:
            log.warning("%s: %s %s: %s", what, source, day, exc)
            return None

    def _scan(
        self, what: str, key: Key, ended: threading.Event | None
    ) -> Future[list[datetime] | None]:
        """:meth:`_file_strays` of ``key`` beside the loop (:meth:`_beside`)."""
        source, day = key
        entry = self.seals.entry(day, source)
        path = None if entry is None else entry.path
        return self._beside(lambda: self._file_strays(what, source, day, path), ended=ended)

    def _reset_next(self, job: ArchiveJob) -> None:
        """Reset the request's next file so that it is loaded again from the vault, over several
        rounds (each loads a batch of live data too): its sealed file still there (the claim
        checked it whole) and read for the minutes its lines were received in outside its day
        (:meth:`_file_strays`, beside the loop and before any transaction), then the partitions
        its rows can land in (a day a round, :meth:`_ensure_days`, while the scan runs and after
        it), then the steps of the file before it, if they are not done yet (:meth:`_reset_round`),
        then its size checked again on the loop (the file may have gone during the rounds since
        the scan), then the reset's first transaction (:meth:`_start_reset`): its rows are
        deleted in steps from the next round on, and it is a stream at offset 0 after the last.
        A file gone or changed since stops the resets: the request stays ``loading`` until the
        files already reset have loaded, then fails (:meth:`_archive_progress`)."""
        source, day = key = job.pending[0]
        if job.scan is None or job.scan[0] != key:
            job.scan = (key, self._scan(f"archive reload {job.id}", key, job.ended))
            return
        scan = job.scan[1]
        if not scan.done():
            self._ensure_days(*self._reload_partition_days(job))  # meanwhile, a day a round
            return
        try:
            strays = scan.result()
        except CheckStoppedError:
            job.scan = None
            return
        if strays is None:
            self._stop_resets(job, source, day)
            return
        if not self._ensure_days(*self._reload_partition_days(job)):
            return  # the check's result is kept for the next round
        if self._reset is not None or self.stop.is_set() or not self._take_lock():
            return  # as well: the steps of the file before it first; none begun at a stop
        if not self._sealed_file_intact(source, day, digest=False):  # gone since its scan
            self._stop_resets(job, source, day)
            return
        job.scan = None
        job.pending.pop(0)
        self._start_reset(key, strays)

    def _resume_reset(self) -> None:
        """A file marked :data:`RESETTING` that no request holds (found at the start: the service
        stopped between two steps of its reset and its request has ended since, failed or
        changed by hand) is reset on its own, as a request's file is, from its first step: its
        sealed file read for its receive times (beside the loop), its partitions, its size
        checked again, then its steps; it is a stream after the last. None is begun while a
        request is ``loading`` (its claim resets its own files again). A file that cannot be
        read stays marked (never a stream, never loaded) and is tried again after
        ``vault_scan_seconds``, the others in the meantime. The loop claims no request while one
        is checked."""
        if self._orphan is None:
            now = time.monotonic()
            key = next(
                (key for key in sorted(self.resetting) if self._orphan_due.get(key, 0.0) <= now),
                None,
            )
            if key is None:
                return
            r = ArchiveReload
            with self.engine.connect() as connection:
                if connection.execute(select(r.id).where(r.status == "loading")).first():
                    return
            log.info("%s %s: its reset was not finished, finishing it", *key)
            first, last = key[1] - RELOAD_MARGIN, key[1] + RELOAD_MARGIN
            # The window job may have dropped them since this run made sure of them.
            self._partitioned.difference_update(
                first + timedelta(days=n) for n in range((last - first).days + 1)
            )
            self._orphan = (key, self._scan("reset", key, None))
            return
        key, scan = self._orphan
        source, day = key
        if not scan.done():
            return
        try:
            strays = scan.result()
        except CheckStoppedError:  # pragma: no cover - only a stop of the run gives up
            self._orphan = None
            return
        if strays is not None and not self._ensure_days(day - RELOAD_MARGIN, day + RELOAD_MARGIN):
            return
        if self.stop.is_set() or not self._take_lock():
            return  # none begun at a stop: the next start finds the mark
        self._orphan = None
        if strays is None or not self._sealed_file_intact(source, day, digest=False):
            log.warning("%s %s: sealed file missing or changed; its reset waits for it", *key)
            self._orphan_due[key] = time.monotonic() + self._vault_scan_seconds
            self._resume_reset()  # the next one meanwhile
            return
        self._orphan_due.pop(key, None)
        self._start_reset(key, strays)

    def _start_reset(self, key: Key, strays: list[datetime]) -> None:
        """The first transaction of the file's reset (:meth:`_begin_reset`); its steps follow
        from the next round on (:meth:`_reset_round`). The file is no stream from before it on
        (no batch of it is read or committed under the mark: the batches read from it are
        dropped, they are read again after the reset), until its last step."""
        if any(key in building.batch.offsets for building in self._queue):
            self._discard_batches()  # read from what is being replaced: read again afterwards
        old = self.streams.pop(key, None)
        if old is not None:
            old.close()
        self.finished.discard(key)
        self._exceptions_logged.discard(key)
        self.resetting.add(key)
        self._reset = reset = Reset(key, strays)
        self._begin(reset)

    def _begin(self, reset: Reset) -> None:
        """The first transaction of ``reset``; when it raises (its answer lost, the database
        gone), :meth:`_reset_round` makes it again: it finds the mark set, or not. It waits for a
        lock at most :data:`RESET_LOCK_TIMEOUT` as a step (the window job's step holding a
        default partition it sweeps, a recount the file's offsets row), then is made again in
        the next round as well."""
        source, day = reset.key
        try:
            with self.engine.begin() as connection:
                connection.execute(text(f"SET LOCAL lock_timeout = '{RESET_LOCK_TIMEOUT}'"))
                reset.steps = self._begin_reset(
                    connection,
                    source,
                    day,
                    strays=reset.strays,
                    defaults=self._defaults(connection),
                )
        except DBAPIError as exc:
            if not _lock_not_available(exc):
                raise
            log.info("%s %s: its reset waits for a lock, again later", source, day)

    def _reset_round(self) -> None:
        """The steps of the reset under way, for :data:`RESET_ROUND_SECONDS` (at least one), each
        a transaction of its own (:meth:`_reset_step`), live batches between the rounds; after
        the last one (which takes the mark off) a fresh stream at offset 0. A step that waits
        for a lock longer than :data:`RESET_LOCK_TIMEOUT` is made again in the next round; a stop
        ends the round after the step under way (the next start goes on from the mark). A
        file whose offset has moved meanwhile (another instance loaded it while this one's lock
        connection was down) is not touched any further: the offsets are read again, and while
        it is still marked its reset begins again (its rows deleted whole, then it loads from
        offset 0)."""
        reset = self._reset
        if reset is None or not self._take_lock():
            return
        source, day = key = reset.key
        if reset.steps is None:
            self._begin(reset)
            return
        started = time.monotonic()
        while reset.steps:
            try:
                with self.engine.begin() as connection:
                    marked = self._reset_step(
                        connection, source, day, reset.steps[0], last=len(reset.steps) == 1
                    )
            except DBAPIError as exc:
                if not _lock_not_available(exc):
                    raise
                log.info("%s %s: a step of its reset waits for a lock, again later", source, day)
                return
            if not marked:
                log.warning("%s %s: its offset moved during its reset; read again", source, day)
                self._reset = None
                self._discard_batches()
                self._load_offsets()
                if key in self.resetting:  # loaded under the mark: from the start again
                    self._start_reset(key, reset.strays)
                return
            reset.steps.pop(0)
            self._stepped = True
            if time.monotonic() - started >= RESET_ROUND_SECONDS or self.stop.is_set():
                return
        self._reset = None
        self.resetting.discard(key)
        stream = self._new_stream(source, day)
        stream.in_db = True
        self.streams[key] = stream

    @staticmethod
    def _stop_resets(job: ArchiveJob, source: str, day: date) -> None:
        """The sealed file of the next file to reset is missing, changed or unreadable: no file
        is reset any more (:meth:`_reset_next`)."""
        log.warning("archive reload %d: %s %s: sealed file missing or changed", job.id, source, day)
        job.scan = None
        job.error_code = ARCHIVE_UNREADABLE
        for unset in job.pending:
            job.files.pop(unset, None)
        job.pending.clear()

    @staticmethod
    def _defaults(connection: Connection) -> list[str]:
        """The default partitions (plain PostgreSQL; none with TimescaleDB)."""
        return [default_partition_name(table) for table in partitioned_tables(connection)]

    @staticmethod
    def _begin_reset(
        connection: Connection,
        source: str,
        day: date,
        *,
        strays: Iterable[datetime] = (),
        defaults: list[str] | None = None,
    ) -> list[ResetStep]:
        """The first transaction of a reset of the file ``(source, day)`` (W5-G-25), in the
        loader's step order: its offset set back to 0 and marked :data:`RESETTING`, the rows it
        has in the default partitions outside its time window, its failures and clock samples,
        and its counts of the hours no step reaches. Returns the steps its other rows are
        deleted in, each a transaction of its own (:meth:`_reset_step`); the last one takes the
        mark off and deletes its other counts. Until then it is never a stream and never counts
        as loaded (offset 0), and a start finds the mark and resets it again from its first
        step.

        Its rows are those of ``(source, raw_day)``, wherever their device times lie (W5-G
        answer 10: a reload run twice leaves every row once, on both back ends). They are
        deleted in the device times they can have, so that partitions and chunks are pruned
        (no index leads with ``raw_day``):

        * ``stream_window``: every line received on the file's day (a device time further than
          a day from the receive time is not used, ``ingest.parsers.syslog``);
        * around the minutes ``strays`` (:func:`received_outside`, read from the sealed file
          that the claim checked against its manifest: the bytes that were loaded) in which a
          line of the file was received outside its day — a damaged or hand-restored spool line
          can carry a receive time of any other day, and its rows lie within
          :data:`~arcilogger_db.searchcounts.COUNTS_TIME_MARGIN` of it;
        * on plain PostgreSQL the default partitions (``defaults``) outside the window too, a
          cheap sweep, here.

        The steps hold at most :data:`RESET_STEP_LINES` lines each by the ``search_counts`` of
        their hours (of every file: what their deletes read). A file whose counts were not
        complete (``counts_from_line`` set before its reset) is stepped at most
        :data:`RESET_CAPPED_STEP` in its time window: its counts do not tell its lines. A reset
        taken up again (the file marked, or its mark taken off by a recount: :meth:`_recounted`)
        is planned by the counts as they are: those of the hours its steps finished went with
        them, the others still tell its rows (a recount counted the rows still there).

        Its clock samples are the source's minutes of its site day, and what the lines received
        outside it added to the minutes they were received in (those keys are shared with that
        day's own lines: the file's part is taken off, here only, in the transaction that sets
        the mark: a reset taken up again leaves them). A sample of another file's line received
        in this file's day goes with the day's minutes (derived data; ``clocksamples.rebuild``
        recounts it)."""
        o = IngestOffset
        row = connection.execute(
            select(o.byte_offset, o.line_number, o.finished_at, o.counts_from_line)
            .where(o.source == source, o.day == day)
            .with_for_update()
        ).first()
        resumed = row is not None and (
            row[3] == RESETTING
            or (
                tuple(row) == (0, 0, None, None)
                and bool(IngestService._recounted(connection, [(source, day)]))
            )
        )
        capped = row is not None and row[3] is not None and not resumed  # counts not complete
        lo, hi = stream_window(day)
        reach = _reach(strays)
        received: Counter[ClockKey] = Counter()
        if not resumed:
            received = IngestService._stray_samples(connection, source, day, reach)
        connection.execute(
            insert(o)
            .values(
                source=source, day=day, byte_offset=0, line_number=0, counts_from_line=RESETTING
            )
            .on_conflict_do_update(
                index_elements=["source", "day"],
                set_={
                    "byte_offset": 0,
                    "line_number": 0,
                    "finished_at": None,
                    "counts_from_line": RESETTING,
                    "updated_at": func.now(),
                },
            )
        )
        outside = {"source": source, "day": day, "lo": lo, "hi": hi}
        for default in sorted(defaults or [], key=lambda name: (name != "messages_default", name)):
            connection.execute(
                text(
                    f'DELETE FROM "{default}" WHERE source = :source AND raw_day = :day '  # noqa: S608
                    "AND (time < :lo OR time >= :hi)"
                ),
                outside,
            )
        connection.execute(
            delete(ParseFailure).where(ParseFailure.source == source, ParseFailure.day == day)
        )
        start, end = day_window(day)
        c = SourceClockSample
        connection.execute(delete(c).where(c.source == source, c.minute >= start, c.minute < end))
        for key, n in sorted(received.items()):
            same = (c.source == key.source, c.minute == key.minute, c.seconds == key.seconds)
            connection.execute(delete(c).where(*same, c.n <= n))
            connection.execute(update(c).where(*same).values(n=c.n - n))
        ranges = [*_outside(reach, lo, hi), (lo, hi)]  # the window last: :meth:`_recounted`
        sc = SearchCount
        connection.execute(
            delete(sc).where(
                sc.source == source,
                sc.raw_day == day,
                not_(or_(*(and_(sc.hour > low - HOUR, sc.hour < high) for low, high in ranges))),
            )
        )
        steps: list[tuple[datetime, datetime]] = []
        for low, high in ranges:
            lines = {
                hour: int(n)
                for hour, n in connection.execute(
                    select(sc.hour, func.sum(sc.n))
                    .where(sc.hour > low - HOUR, sc.hour < high)
                    .group_by(sc.hour)
                )
            }
            longest = RESET_CAPPED_STEP if capped and (low, high) == (lo, hi) else None
            steps += reset_steps(low, high, lines, budget=RESET_STEP_LINES, longest=longest)
        return count_hours(steps)

    @staticmethod
    def _reset_step(
        connection: Connection, source: str, day: date, step: ResetStep, *, last: bool
    ) -> bool:
        """A step of the file's reset (:meth:`_begin_reset`): its rows of the device times of
        ``step`` (``messages`` first) and its counts of the hours ``step`` finishes; the
        ``last`` takes the mark off first (the offsets row first, the loader's order) and
        deletes all its counts left. A compare-and-set as the loader's: ``False`` (and nothing
        deleted) when the file's offset is no longer 0 or it has finished (another instance
        loaded it while this one's lock connection was down). A mark taken off meanwhile with
        the offset still 0 (``arcilogger-db search-counts rebuild``, which recounts the rows
        still there) is put back: no batch has loaded the file since its reset began."""
        o = IngestOffset
        at = (o.source == source, o.day == day)
        connection.execute(text(f"SET LOCAL lock_timeout = '{RESET_LOCK_TIMEOUT}'"))
        row = connection.execute(
            select(o.byte_offset, o.line_number, o.finished_at, o.counts_from_line)
            .where(*at)
            .with_for_update()
        ).first()
        if row is None or tuple(row[:3]) != (0, 0, None):
            return False
        if last or row[3] != RESETTING:
            if row[3] != RESETTING:
                log.warning("%s %s: its reset's mark was taken off (a recount?)", source, day)
            mark = None if last else RESETTING
            connection.execute(
                update(o).where(*at).values(counts_from_line=mark, updated_at=func.now())
            )
        for name in RESET_TABLES:
            table = Base.metadata.tables[name]
            connection.execute(
                delete(table).where(
                    table.c.source == source,
                    table.c.raw_day == day,
                    table.c.time >= step.start,
                    table.c.time < step.end,
                )
            )
        sc = SearchCount
        file_counts = (sc.source == source, sc.raw_day == day)
        if last:
            connection.execute(delete(sc).where(*file_counts))
        elif step.hours[0] < step.hours[1]:
            hours = (sc.hour >= step.hours[0], sc.hour < step.hours[1])
            connection.execute(delete(sc).where(*file_counts, *hours))
        return True

    @staticmethod
    def _stray_samples(
        connection: Connection, source: str, day: date, reach: list[tuple[datetime, datetime]]
    ) -> Counter[ClockKey]:
        """The ``source_clock_samples`` keys the file's rows received outside its site day added
        (:func:`~arcilogger_db.clocksamples.clock_key`), read before they are deleted; they lie
        in ``reach``."""
        start, end = day_window(day)
        m = Message.__table__
        samples: Counter[ClockKey] = Counter()
        for low, high in reach:
            rows = connection.execute(
                select(m.c.time, m.c.time_received).where(
                    m.c.source == source,
                    m.c.raw_day == day,
                    m.c.time >= low,
                    m.c.time < high,
                    or_(m.c.time_received < start, m.c.time_received >= end),
                )
            )
            for time_, received in rows:
                key = clock_key({"source": source, "time": time_, "time_received": received})
                if key is not None:
                    samples[key] += 1
        return samples

    def _archive_progress(self, job: ArchiveJob) -> None:
        """``bytes_done`` (Σ committed offset; a finished file and one loaded already count
        whole), at most every :data:`ARCHIVE_PROGRESS_SECONDS`; ``ready`` once every file has
        finished, ``failed`` once one has not been readable for :attr:`_archive_error_seconds`."""
        now = time.monotonic()
        pending = set(job.pending)
        done, loading = 0, bool(pending)
        for key, size in job.files.items():
            if key in pending:
                continue
            if key in job.done or key in self.finished:
                done += size
                continue
            loading = True
            stream = self.streams.get(key)
            if stream is None:  # its reset's steps are not done yet: a stream after them
                continue
            done += stream.offset
            if (
                stream.error_since is not None
                and now - stream.error_since >= self._archive_error_seconds
            ):
                log.warning("archive reload %d: %s %s: %s", job.id, key[0], key[1], stream.error)
                self._archive_end(job, "failed", error_code=ARCHIVE_UNREADABLE)
                return
        if not loading:
            if job.error_code is not None:
                self._archive_end(job, "failed", error_code=job.error_code)
            else:
                self._archive_end(job, "ready")
            return
        if done == job.written or now < job.next_progress:
            return
        r = ArchiveReload
        with self.engine.begin() as connection:
            changed = connection.execute(
                update(r).where(r.id == job.id, r.status == "loading").values(bytes_done=done)
            ).rowcount
        job.written, job.next_progress = done, now + ARCHIVE_PROGRESS_SECONDS
        if changed != 1:  # changed by hand: its files go on as ordinary streams
            log.warning("archive reload %d is no longer loading", job.id)
            self._drop_job(job)

    def _drop_job(self, job: ArchiveJob) -> None:
        """The loop is done with ``job``: a scan of it still running beside the loop
        (:meth:`_reset_next`) gives up at its next slice instead of reading on."""
        job.ended.set()
        self._archive_job = None

    def _archive_end(self, job: ArchiveJob, status: str, *, error_code: str | None = None) -> None:
        """``ready`` (``ready_at`` now, ``expires_at`` ``db.reload_keep_days`` later) or
        ``failed`` with its ``error_code``; the next queued request is taken at once."""
        r = ArchiveReload
        values: dict[str, Any] = {"status": status, "error_code": error_code}
        with Session(self.engine) as session, session.begin():
            if status == "ready":
                keep = get_setting(session, "db.reload_keep_days")
                if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
                    keep = int(DEFAULT_SETTINGS["db.reload_keep_days"])
                ready_at = self._clock()
                values.update(
                    bytes_done=job.bytes_total,
                    ready_at=ready_at,
                    expires_at=ready_at + timedelta(days=keep),
                )
            session.execute(
                update(r)
                .where(r.id == job.id, r.status == "loading")
                .values(**values)
                .execution_options(synchronize_session=False)
            )
        self._drop_job(job)
        self._next_archive = 0.0
        if status == "ready":
            log.info("archive reload %d ready (%d bytes)", job.id, job.bytes_total)
        else:
            log.warning("archive reload %d failed: %s", job.id, error_code)

    # -- reading ------------------------------------------------------------------------------

    def _by_day(self) -> list[list[Stream]]:
        """Streams grouped by day, newest day first (live data before backlog), by source name."""
        days: dict[date, list[Stream]] = {}
        ordered = sorted(
            self.streams.values(), key=lambda s: (-s.info.day.toordinal(), s.info.source)
        )
        for stream in ordered:
            days.setdefault(stream.info.day, []).append(stream)
        return list(days.values())

    def _chunk_lines(self) -> int:
        """Lines per read: the whole batch inline; with workers, enough chunks to share."""
        rows = self.config.ingest.batch_rows
        if self.workers <= 0:
            return rows
        return max(MIN_WORKER_CHUNK_LINES, rows // (2 * self.workers))

    def _fill(self) -> int:
        """Read new lines until the batch is full (with workers: one batch ahead as well).

        Newest day first. The streams of one day share the room left in the batch in rounds:
        each gets an equal share, and what one cannot use goes to the others in the next round.
        Read one after the other, a source with a large backlog (DHCP or access points after
        an outage) would hold back every source sorted after it — ``fortigate``, which 5651
        lookups need most, sorts after ``ap-*`` and ``core-r1``. Returns the number of lines read.
        """
        total = 0
        limit = 2 if self.workers > 0 else 1
        rows = self.config.ingest.batch_rows
        for active in self._by_day():
            while active:
                room = rows - self._queue[-1].lines
                share = -(-(room if room > 0 else rows) // len(active))  # ceiling
                still: list[Stream] = []
                for stream in active:
                    tail = self._queue[-1]
                    if tail.lines >= rows:
                        if len(self._queue) >= limit:
                            return total
                        tail = Building()
                        self._queue.append(tail)
                    budget = min(rows - tail.lines, share, self._chunk_lines())
                    added = self._read_stream(stream, tail, budget)
                    if added:
                        total += added
                        still.append(stream)
                active = still
        return total

    @property
    def _head(self) -> Building:
        return self._queue[0]

    def _batch_full(self) -> bool:
        return self._head.lines >= self.config.ingest.batch_rows

    def _batch_empty(self) -> bool:
        return all(building.empty for building in self._queue)

    def _read_stream(self, stream: Stream, into: Building, budget: int) -> int:
        """Read at most ``budget`` new lines of ``stream`` into ``into``; returns how many."""
        if stream.finishing:
            return 0
        wanted = int(budget * stream.line_bytes * 1.25)
        max_bytes = min(max(wanted, MIN_READ_BYTES), MAX_READ_BYTES)
        data: bytes | None = None
        if stream.vault is None:
            data = self._read_spool(stream, max_bytes)
        if data is None:  # the spool file is gone (or we are already on the vault copy)
            data = self._read_vault(stream, into, max_bytes)
        if not data:
            return 0
        keep = first_lines(data, budget)
        if keep < len(data):  # more lines than the batch takes: the rest is read again later
            if stream.vault is not None:
                stream.vault.unread(data[keep:])
            data = data[:keep]
        added = self._add_chunk(stream, into, data)
        stream.line_bytes = 0.8 * stream.line_bytes + 0.2 * (len(data) / added)
        return added

    def _read_spool(self, stream: Stream, max_bytes: int) -> bytes | None:
        """New complete lines from the spool file; ``None`` when the file does not exist."""
        path = self.layout.spool_file(stream.info.source, stream.info.day)
        try:
            size = path.stat().st_size
            stream.origin, stream.size = "spool", size
            if size < stream.read_offset:
                self._report(
                    stream,
                    f"spool file has {size} bytes but {stream.read_offset} were already read; "
                    "the raw file was changed (evidence!) — not reading it",
                )
                return b""
            if size == stream.read_offset:
                return b""
            data = read_complete(path, stream.read_offset, max_bytes)
        except FileNotFoundError:
            return None
        except OSError as exc:  # e.g. Windows: the file is being deleted by the sealer
            self._report(stream, f"spool file not readable: {exc}")
            return b""
        self._clear_error(stream)
        return data

    def _read_vault(self, stream: Stream, into: Building, max_bytes: int) -> bytes:
        """Continue from the sealed copy; finish the stream once it has been read completely."""
        day, source = stream.info.day, stream.info.source
        entry = self.seals.entry(day, source)
        if entry is not None:
            stream.size = entry.bytes_uncompressed
            if stream.read_offset == entry.bytes_uncompressed:
                self._queue_finish(stream, into)
                return b""
            if stream.read_offset > entry.bytes_uncompressed:
                self._report(
                    stream,
                    f"{stream.read_offset} bytes loaded but the sealed file has "
                    f"{entry.bytes_uncompressed}; not finishing this day",
                )
                return b""
            path = entry.path
        elif self.seals.is_sealed(day):
            self._report(stream, "spool file missing and the source is not in the day's manifest")
            return b""
        else:
            # Between the sealer's spool deletion and its "sealed" state (or after a crash
            # there): the vault file was verified before the spool was deleted.
            try:
                found = sealedfile.find_log(self.layout, day, source)
            except sealedfile.SealedFileError as exc:
                self._report(stream, f"sealed file not readable: {exc}")
                return b""
            if found is None:
                self._report(stream, "spool file missing and the day is not sealed yet")
                return b""
            path = found
        try:
            if stream.vault is None:
                stream.vault = VaultReader(path, stream.read_offset)
                stream.origin = "vault"
                log.info(
                    "%s %s: spool file gone, continuing from %s at byte %d",
                    source,
                    day,
                    path.name,
                    stream.read_offset,
                )
            data = stream.vault.read(max_bytes)
        except (VaultReadError, OSError) as exc:
            stream.close()
            self._report(stream, f"sealed file not readable: {exc}")
            return b""
        self._clear_error(stream)
        return data

    def _add_chunk(self, stream: Stream, into: Building, data: bytes) -> int:
        """Hand the chunk to the preparer and move the stream's read position past it."""
        if self._preparer is None:  # pragma: no cover - created before the loop starts
            raise RuntimeError("service not started")
        lines = count_lines(data)
        if into.started is None:
            into.started = time.monotonic()
        into.pending.append(
            self._preparer.submit(stream.info, stream.read_offset, stream.read_line, data)
        )
        start = (stream.read_offset, stream.read_line)
        stream.read_offset += len(data)
        stream.read_line += lines
        into.lines += lines
        self._set_offset(stream, into, start)
        return lines

    def _set_offset(
        self, stream: Stream, into: Building, start: tuple[int, int], *, finish: bool = False
    ) -> None:
        """The batch moves the stream from where it started in *this* batch to its read position.

        With a batch read ahead, that start is the previous batch's end, which is committed
        first; the compare-and-set in the loader checks exactly that.
        """
        existing = into.batch.offsets.get(stream.key)
        old = (existing.old_offset, existing.old_line) if existing is not None else start
        into.batch.offsets[stream.key] = OffsetUpdate(
            source=stream.info.source,
            day=stream.info.day,
            old_offset=old[0],
            old_line=old[1],
            new_offset=stream.read_offset,
            new_line=stream.read_line,
            exists=stream.in_db,
            finish=finish,
        )

    def _resolve(self, building: Building) -> None:
        """Collect the prepared chunks (in read order) into the batch."""
        batch = building.batch
        for pending in building.pending:
            chunk = pending.result()
            change = batch.offsets.get((chunk.stream.source, chunk.stream.day))
            if change is None or chunk.end_offset > change.new_offset:  # pragma: no cover
                raise RuntimeError("prepared chunk does not match what was read")
            batch.lines.extend(chunk.lines)
            source = chunk.stream.source
            seen = batch.sources.get(source)
            for line in chunk.lines:
                if seen is None:
                    stream = self.streams[(source, chunk.stream.day)]
                    seen = self._source_seen(stream.source, line.parser)
                    batch.sources[source] = seen
                seen.see(line.received, line.sender)
                if line.failures:
                    self._log_exception(line)
        building.pending = []

    def _log_exception(self, line: PreparedLine) -> None:
        """A parser or row-check bug: ERROR once per stream; every line is in parse_failures."""
        key = (line.stream.source, line.stream.day)
        if key in self._exceptions_logged:
            return
        for failure in line.failures:
            if failure.pattern == PATTERN_EXCEPTION:
                self._exceptions_logged.add(key)
                log.error(
                    "%s %s line %d (byte %d): %s; counted as %s (the generic message is kept), "
                    "further lines of this day are only counted",
                    key[0],
                    key[1],
                    line.line,
                    line.offset,
                    failure.detail,
                    PATTERN_EXCEPTION,
                )
                return

    @staticmethod
    def _source_seen(source: SourceInfo, parser: str | None) -> SourceSeen:
        return SourceSeen(
            name=source.name,
            type=source.configured_type or parser or "generic",
            unknown=source.unknown,
            configured=source.config is not None,
            expected=source.expected,
            heartbeat_minutes=source.heartbeat_minutes,
        )

    def _queue_finish(self, stream: Stream, into: Building) -> None:
        stream.finishing = True
        self._set_offset(stream, into, (stream.read_offset, stream.read_line), finish=True)

    def _report(self, stream: Stream, message: str) -> None:
        if stream.error != message:
            log.warning("%s %s: %s", stream.info.source, stream.info.day, message)
        if stream.error is None:
            stream.error_since = time.monotonic()
        stream.error = message

    @staticmethod
    def _clear_error(stream: Stream) -> None:
        stream.error = None
        stream.error_since = None

    # -- loading ------------------------------------------------------------------------------

    def _flush_due(self, read: int, *, once: bool) -> bool:
        head = self._head
        if head.empty:
            return False
        if head.lines == 0 or self._batch_full():
            return True  # only finishing streams, or a full batch
        if read == 0 and once:
            return True
        started = head.started or time.monotonic()
        return time.monotonic() - started >= self.config.ingest.batch_seconds

    def _batch_wait(self) -> float:
        started = self._head.started or time.monotonic()
        left = self.config.ingest.batch_seconds - (time.monotonic() - started)
        return max(0.0, min(left, self.config.ingest.poll_seconds))

    def _flush(self, *, retry: bool) -> bool:
        """Load the first batch; with ``retry`` until it is loaded or stop is requested."""
        head = self._head
        self._resolve(head)
        attempt = 0
        while True:
            try:
                if not self._take_lock():
                    return self._lost_lock(retry=retry)
                result = self.loader.load(head.batch)
            except OffsetConflictError as exc:
                # A COMMIT whose answer was lost (it did commit), or a second instance
                # without the lock: either way the database is right, continue from it.
                log.warning(
                    "%s (moved by another instance, or a lost COMMIT answer): reloading", exc
                )
                self._discard_batches()
                try:
                    self._load_offsets()
                except DBAPIError as reload_error:
                    self._database_down(reload_error)
                    return False
                return True
            except DBAPIError as exc:  # unavailable, not ready, or a schema problem: retry
                self._database_down(exc)
                self.engine.dispose()  # fresh connections after the database comes back
                if not retry or self.stop.is_set():
                    return False
                self._backoff(attempt)
                attempt += 1
                if self.stop.is_set():
                    return False
                if self._reload():  # the batch was read for the old sources: dropped, it is
                    return True  # read again for the new ones
                continue
            self._committed(head, result)
            self._database_up()
            return True

    def _committed(self, head: Building, result: LoadResult) -> None:
        batch = head.batch
        per_source: dict[str, int] = {}
        last_received: dict[Key, datetime] = {}
        for line in batch.lines:
            key = (line.stream.source, line.stream.day)
            per_source[key[0]] = per_source.get(key[0], 0) + 1
            if line.received is not None:
                last_received[key] = line.received
        for key, change in batch.offsets.items():
            stream = self.streams.get(key)
            if stream is None:  # pragma: no cover - streams are only removed here
                continue
            stream.offset, stream.line = change.new_offset, change.new_line
            stream.in_db = True
            stream.last_received = last_received.get(key, stream.last_received)
            if change.finish:
                stream.close()
                del self.streams[key]
                self.finished.add(key)
                log.info("%s %s finished (%d lines)", key[0], key[1], change.new_line)
        counters = self.counters
        counters.lines += len(batch.lines)
        counters.window_lines += len(batch.lines)
        counters.rows += result.total_rows
        counters.failures += result.failures
        counters.batches += 1
        for source, count in per_source.items():
            entry = counters.per_source.setdefault(source, SourceCounters())
            entry.lines += count
            entry.window_lines += count
        for name in result.new_sources:
            log.info("new source recorded: %s", name)
        self._queue.pop(0)
        if not self._queue:
            self._queue.append(Building())

    def _discard_batches(self) -> None:
        """Drop every batch not loaded yet; the streams go back to their committed offsets."""
        for building in self._queue:
            for key in building.batch.offsets:
                stream = self.streams.get(key)
                if stream is not None:
                    stream.rewind()
        self._queue = [Building()]

    def _shutdown(self) -> None:
        """Stop requested: load the current batch once if the database is up, else drop it.

        A batch read ahead (parse workers) is dropped; it is read again at the next start.
        """
        self.state = "stopping"
        if not self._head.empty and self.db_ok and self._flush(retry=False):
            log.info("last batch loaded before stopping")
        lines = sum(building.lines for building in self._queue)
        if lines:
            log.info("%d lines read but not loaded; they are read again at the next start", lines)
        self._discard_batches()

    def _backoff(self, attempt: int) -> None:
        delay = min(self._backoff_max, BACKOFF_FIRST * (2**attempt))
        log.info("retrying in %.1f s", delay)
        self._write_status()
        self.stop.wait(delay)

    def _database_down(self, exc: BaseException) -> None:
        text = str(getattr(exc, "orig", None) or exc).strip()
        message = (text.splitlines() or [type(exc).__name__])[0][:500]
        if self.db_ok or self.db_error != message:
            log.warning("database unavailable, nothing is lost (spool is the queue): %s", message)
        self.db_ok = False
        self.db_error = message
        if self.state == "running":
            self.state = "database_unavailable"

    def _database_up(self) -> None:
        if not self.db_ok and self.db_error is not None:
            log.info("database available again")
        self.db_ok = True
        self.db_error = None
        if self.state == "database_unavailable":
            self.state = "running"

    # -- status -------------------------------------------------------------------------------

    def _write_status_if_due(self) -> None:
        # Once the service stops, the one written last (``stopped``, :meth:`run`) is enough: a
        # stop waits for at most the write under way and that one (#124 stage 2).
        if not self.stop.is_set() and time.monotonic() >= self._next_status:
            self._write_status()

    def _write_status(self) -> None:
        now = time.monotonic()
        self._next_status = now + self._status_interval
        if self._locked_out:  # the file belongs to the instance holding the lock
            return
        counters = self.counters
        elapsed = now - counters.window_started
        if elapsed >= 1.0:
            counters.rate = counters.window_lines / elapsed
            for entry in counters.per_source.values():
                entry.rate = entry.window_lines / elapsed
                entry.window_lines = 0
            counters.window_lines = 0
            counters.window_started = now
        self.status.write(self)
        if self.status._failing:
            # Refused (on Windows a reader holding the file refuses the replace; logged once
            # per streak): still due, so the next round tries again, not a status interval
            # later — a new configuration report must not wait for it.
            self._next_status = now
