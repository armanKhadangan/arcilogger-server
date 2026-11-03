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
   or, once the sealer has deleted it, from the sealed ``.log.gz`` at the same offset.
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
For each such ``(source, day)`` it first checks the sealed file against the manifest (size and
SHA-256, the manifest read afresh): a file that is missing or changed fails the request before
anything of it is deleted. Then it deletes in one transaction that file's rows (all
:data:`~arcilogger_db.models.TIME_SERIES_MODELS`), its parse failures, search counts and clock
samples, and resets its ``ingest_offsets`` row to 0 — in the loader's step order (offsets, rows,
failures, counts), one file per round so live data is not held up. The file is then an ordinary
stream: it is read from the sealed file by the reader above, after every newer day (live data
first), and loaded exactly once like any other. ``bytes_done`` is Σ committed offset; the
request is ``ready`` when every file has finished (``expires_at`` = ``ready_at`` +
``db.reload_keep_days``) and ``failed`` with an ``error_code`` when a day is no longer sealed in
the vault or a file stays unreadable.

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
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from sqlalchemy import Connection, Engine, delete, func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_core.config import Config
from arcilogger_core.configwatch import INGEST_HOT, INGEST_RESTART, ConfigWatch, ConfigWatcher
from arcilogger_core.layout import AUDIT_SOURCE, SPOOL_SUFFIX, Layout
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.models import (
    TIME_SERIES_TABLES,
    ArchiveReload,
    Base,
    IngestOffset,
    ParseFailure,
    SearchCount,
    Source,
    SourceClockSample,
)
from arcilogger_db.partitions import (
    PARTITION_LOCK_KEY,
    RAW_DAY_MARGIN,
    default_partition_name,
    ensure_days,
    partitioned_tables,
)
from arcilogger_db.searchcounts import stream_window
from arcilogger_db.settings import DEFAULT_SETTINGS, get_setting
from arcilogger_ingest.files import VaultReader, VaultReadError, read_complete
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
#: Creating partitions waits at most this long for a table lock (a long search holds one);
#: ingest tries again at the next round instead of holding up live data.
PARTITION_LOCK_TIMEOUT: Final = "5s"
LOCK_NOT_AVAILABLE: Final = "55P03"
#: The raw days around a request's days whose files are loaded with it (module text).
RELOAD_MARGIN: Final = timedelta(days=RAW_DAY_MARGIN)
#: Bytes read at a time when a sealed file is checked against its manifest's SHA-256.
HASH_CHUNK: Final = 1 << 20


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
    #: Set when a file changed after the claim checked it: no further file is reset, the
    #: request ends ``failed`` with it once the files already reset have loaded.
    error_code: str | None = None
    #: ``bytes_done`` as last written.
    written: int = 0
    next_progress: float = 0.0

    @property
    def bytes_total(self) -> int:
        return sum(self.files.values())


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
        self._archive_seconds = archive_seconds
        self._archive_error_seconds = archive_error_seconds
        self._next_archive = 0.0
        self._archive_job: ArchiveJob | None = None
        #: The last claim failed a request (the next one is taken at once).
        self._claim_failed = False
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
                    if once and self._archive_job is None:
                        break
                    self._write_status_if_due()
                    self.stop.wait(settings.poll_seconds)
                elif read == 0:
                    self.stop.wait(self._batch_wait())
                self._write_status_if_due()
            self._shutdown()
            return EXIT_OK
        except BaseException:
            self.state = "failed"  # the CLI logs the traceback and exits 1
            raise
        finally:
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
        """Positions from ``ingest_offsets`` (the only source of truth after a restart)."""
        with self.engine.connect() as connection:
            rows = connection.execute(
                select(
                    IngestOffset.source,
                    IngestOffset.day,
                    IngestOffset.byte_offset,
                    IngestOffset.line_number,
                    IngestOffset.finished_at,
                )
            ).all()
        for stream in self.streams.values():
            stream.close()
        self.streams.clear()
        self.finished.clear()
        self._vault_done.clear()
        for source, day, offset, line, finished_at in rows:
            key = (source, day)
            if finished_at is not None:
                self.finished.add(key)
                continue
            stream = self._new_stream(source, day)
            stream.offset = stream.read_offset = offset
            stream.line = stream.read_line = line
            stream.in_db = True
            self.streams[key] = stream

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
        if key not in self.finished and key not in self.streams:
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
        nothing to do with TimescaleDB), one transaction a day (its device times lie in two UTC
        days, at most 32 partitions: the lock table of PostgreSQL's defaults holds about 256).
        ``False`` when a table lock was not free within :data:`PARTITION_LOCK_TIMEOUT` (tried
        again later); other database errors are raised."""
        day = first_day
        while day <= last_day:
            if day not in self._partitioned:
                try:
                    with self.engine.begin() as connection:
                        connection.execute(
                            text(f"SET LOCAL lock_timeout = '{PARTITION_LOCK_TIMEOUT}'")
                        )
                        report = ensure_days(connection, day, day)
                except DBAPIError as exc:
                    if not _lock_not_available(exc):
                        raise
                    log.info("partitions of %s: a table is locked, trying again later", day)
                    return False
                if report.created:
                    log.info("partitions of %s created (%d)", day, len(report.created))
                self._partitioned.add(day)
            day += timedelta(days=1)
        return True

    def _today(self) -> date:
        return site_day(self._clock())

    # -- archive reloads (Wave 5 contract G4) -------------------------------------------------

    def _archive(self) -> bool:
        """Between two batches: reset the next file of the loading archive reload, write its
        progress or end it, and take the next queued request (at once after one ended).
        ``False`` when the database was unavailable (everything is tried again next round)."""
        try:
            job = self._archive_job
            if job is not None:
                if job.pending:
                    self._reset_next(job)
                self._archive_progress(job)
            if self._archive_job is None and time.monotonic() >= self._next_archive:
                self._next_archive = time.monotonic() + self._archive_seconds
                # A request that fails at once (not in the vault, a file changed) is followed by
                # the next one in the same round.
                self._claim_failed = True
                while self._archive_job is None and self._claim_failed and self._take_lock():
                    self._claim_failed = False
                    self._archive_job = self._claim_archive()
        except DBAPIError as exc:
            self._database_down(exc)
            return False
        return True

    def _claim_archive(self) -> ArchiveJob | None:
        """The ``loading`` request (this service stopped while loading it: every file is reset
        again, which loads the same rows) or else the oldest ``queued`` one, now ``loading``.

        Every file it would load is checked against its manifest first
        (:meth:`_sealed_file_intact`, outside any transaction: it reads them whole): a day of the
        request that is not sealed (``archive_day_not_in_vault``) or a file missing or changed
        (``archive_file_unreadable``) fails the request before anything is deleted. Then one
        transaction under the partition maintenance lock (the window job holds it while it reads
        the reloads and drops: it either saw this request as active, or its drop is done before
        a file is reset) moves it on. ``None`` when there is none, when it failed, when it
        changed meanwhile, or when the lock is busy (tried again at the next poll)."""
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
        error = None
        if files is None:
            error = ARCHIVE_NOT_IN_VAULT
        elif not all(self._sealed_file_intact(source, day) for source, day in sorted(files)):
            error = ARCHIVE_UNREADABLE
        try:
            with self.engine.begin() as connection:
                connection.execute(text(f"SET LOCAL lock_timeout = '{PARTITION_LOCK_TIMEOUT}'"))
                connection.execute(
                    text("SELECT pg_advisory_xact_lock(:key)"), {"key": PARTITION_LOCK_KEY}
                )
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
                job = ArchiveJob(reload_id, first_day, last_day, files, pending=sorted(files))
                connection.execute(
                    update(r)
                    .where(r.id == reload_id)
                    .values(status="loading", bytes_total=job.bytes_total, bytes_done=0)
                )
        except DBAPIError as exc:
            if not _lock_not_available(exc):
                raise
            log.info("archive reloads: the window job holds the partitions, trying again later")
            return None
        # The window job may have dropped them since this run made sure of them.
        first, last = self._reload_partition_days(job)
        self._partitioned.difference_update(
            first + timedelta(days=n) for n in range((last - first).days + 1)
        )
        log.info(
            "archive reload %d: %s … %s, %d file(s), %d bytes",
            job.id,
            job.first_day,
            job.last_day,
            len(job.files),
            job.bytes_total,
        )
        return job

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

    def _sealed_file_intact(self, source: str, day: date, *, digest: bool = True) -> bool:
        """Whether the sealed file of ``(source, day)`` is in the vault as its manifest — read
        afresh, not from :class:`SealIndex`'s cache — describes it: its size and (``digest``)
        SHA-256. Checked before the file's rows are deleted, so a reload never deletes rows it
        could not load again."""
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
            with path.open("rb") as handle:
                while chunk := handle.read(HASH_CHUNK):
                    sha256.update(chunk)
            return sha256.hexdigest() == str(entry["sha256"])
        except (OSError, ValueError, KeyError, TypeError, AttributeError, StopIteration):
            return False

    def _reset_next(self, job: ArchiveJob) -> None:
        """Reset the request's next file so that it is loaded again from the vault: its sealed
        file still there (the claim checked it whole), the partitions its rows can land in
        first, then one transaction, then a fresh stream at offset 0. A file gone or changed
        since stops the resets: the request stays ``loading`` until the files already reset
        have loaded, then fails (:meth:`_archive_progress`)."""
        source, day = key = job.pending[0]
        if not self._sealed_file_intact(source, day, digest=False):
            log.warning(
                "archive reload %d: %s %s: sealed file missing or changed", job.id, source, day
            )
            job.error_code = ARCHIVE_UNREADABLE
            for unset in job.pending:
                job.files.pop(unset, None)
            job.pending.clear()
            return
        if not self._ensure_days(*self._reload_partition_days(job)):
            return
        if not self._take_lock():
            return
        if any(key in building.batch.offsets for building in self._queue):
            self._discard_batches()  # read from what is being replaced: read again afterwards
        with self.engine.begin() as connection:
            self._reset_file(connection, source, day, defaults=self._defaults(connection))
        job.pending.pop(0)
        old = self.streams.pop(key, None)
        if old is not None:
            old.close()
        self.finished.discard(key)
        self._exceptions_logged.discard(key)
        stream = self._new_stream(source, day)
        stream.in_db = True
        self.streams[key] = stream

    @staticmethod
    def _defaults(connection: Connection) -> list[str]:
        """The default partitions (plain PostgreSQL; none with TimescaleDB)."""
        return [default_partition_name(table) for table in partitioned_tables(connection)]

    @staticmethod
    def _reset_file(
        connection: Connection, source: str, day: date, *, defaults: list[str] | None = None
    ) -> None:
        """Delete everything the file ``(source, day)`` loaded and set its offset back to 0, in
        the loader's step order: offset, rows (``messages`` first), failures, counts. Its rows
        lie within ``stream_window`` (partition pruning), its receive times within its site
        day — so its clock samples are the source's minutes of that day. A row outside that
        window (a damaged or hand-restored spool line with a receive time of another day) can
        only be in a default partition (``defaults``, plain PostgreSQL): those are deleted too.
        On TimescaleDB such a row is not looked for (it would mean reading every chunk); a
        reload run twice before ``db.window`` drops it then leaves it twice (a known limit)."""
        connection.execute(
            insert(IngestOffset)
            .values(source=source, day=day, byte_offset=0, line_number=0)
            .on_conflict_do_update(
                index_elements=["source", "day"],
                set_={
                    "byte_offset": 0,
                    "line_number": 0,
                    "finished_at": None,
                    "counts_from_line": None,
                    "updated_at": func.now(),
                },
            )
        )
        lo, hi = stream_window(day)
        for name in sorted(TIME_SERIES_TABLES, key=lambda table: (table != "messages", table)):
            table = Base.metadata.tables[name]
            connection.execute(
                delete(table).where(
                    table.c.source == source,
                    table.c.raw_day == day,
                    table.c.time >= lo,
                    table.c.time < hi,
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
        connection.execute(
            delete(SearchCount).where(SearchCount.source == source, SearchCount.raw_day == day)
        )
        start, end = day_window(day)
        connection.execute(
            delete(SourceClockSample).where(
                SourceClockSample.source == source,
                SourceClockSample.minute >= start,
                SourceClockSample.minute < end,
            )
        )

    def _archive_progress(self, job: ArchiveJob) -> None:
        """``bytes_done`` (Σ committed offset; a finished file counts whole), at most every
        :data:`ARCHIVE_PROGRESS_SECONDS`; ``ready`` once every file has finished, ``failed``
        once one has not been readable for :attr:`_archive_error_seconds`."""
        now = time.monotonic()
        pending = set(job.pending)
        done, loading = 0, bool(pending)
        for key, size in job.files.items():
            if key in pending:
                continue
            if key in self.finished:
                done += size
                continue
            loading = True
            stream = self.streams.get(key)
            if stream is None:  # pragma: no cover - a reset file is a stream until it finishes
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
        self._archive_job = None
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
            path = self.layout.vault_day(day) / f"{source}.log.gz"
            if not path.is_file():
                self._report(stream, "spool file missing and the day is not sealed yet")
                return b""
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
        if time.monotonic() >= self._next_status:
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
