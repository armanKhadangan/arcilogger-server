"""The ingest service: spool files → parsers → PostgreSQL, exactly once, until stopped.

One loop in one thread (plus optional parse worker processes, :mod:`arcilogger_ingest.pool`):

1. **Discover** streams — one per ``(source, day)``: every ``spool/<source>/<day>.log`` (cheap
   directory listing every poll), every unfinished ``ingest_offsets`` row, and every source of
   a sealed day within the database retention (``db.retention_days``) that is not finished yet
   (vault scan once a minute; this is how a day whose spool file the sealer already deleted is
   still loaded, e.g. after the service was down over a weekend).
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

import logging
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Final, Literal

from sqlalchemy import Engine, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_core.config import Config
from arcilogger_core.configwatch import INGEST_HOT, INGEST_RESTART, ConfigWatch, ConfigWatcher
from arcilogger_core.layout import AUDIT_SOURCE, SPOOL_SUFFIX, Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.models import IngestOffset, Source
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
                read = self._fill()
                if self._flush_due(read, once=once) and not self._flush(retry=not once):
                    if once:
                        return EXIT_LOCKED if self._locked_out else EXIT_DB_UNAVAILABLE
                    continue  # stop was requested while the database was down
                if read == 0 and self._batch_empty():
                    if once:
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
        dashboard and the lease watermark stop waiting for it."""
        with self.engine.begin() as connection:
            for source in self.config.sources:
                if source.is_network:
                    continue
                values = {
                    "type": source.type,
                    "address": source.address,
                    "enabled": source.enabled,
                    "expected": source.expected,
                    "heartbeat_minutes": source.heartbeat_minutes,
                    "unknown": False,
                }
                statement = insert(Source).values(name=source.name, **values)
                connection.execute(
                    statement.on_conflict_do_update(index_elements=["name"], set_=values)
                )
            watched = connection.scalars(
                select(Source.name).where(
                    Source.unknown.is_(False), Source.enabled.is_(True) | Source.expected.is_(True)
                )
            ).all()
            removed = [name for name in watched if not self._configured(name)]
            if removed:
                connection.execute(
                    update(Source)
                    .where(Source.name.in_(removed))
                    .values(enabled=False, expected=False)
                )
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
        """Sources of sealed days (within the database retention) that are not finished."""
        self._next_vault_scan = time.monotonic() + self._vault_scan_seconds
        horizon = self._today() - timedelta(days=self._retention_days)
        for day in self.seals.sealed_days(horizon):
            if day in self._vault_done:
                continue
            files = self.seals.files(day) or {}
            pending = [source for source in files if (source, day) not in self.finished]
            if not pending:
                self._vault_done.add(day)
            for source in pending:
                self._add_stream(source, day)

    def _today(self) -> date:
        return site_day(self._clock())

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
        stream.error = message

    @staticmethod
    def _clear_error(stream: Stream) -> None:
        stream.error = None

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
