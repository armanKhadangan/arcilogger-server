"""Canlı Durum's rolling snapshot (Wave 5 contract §6.2 M2; the M4 decision on #201, 2026-11-06):
who sent and received how much in each of the last 60 minutes, kept up to date by the worker
job ``activity.tick`` and written to ``state/activity.json``, which ``GET /activity/devices``
answers from (deviation W5-M-4).

**The counting is the usage engine's** (:mod:`arcilogger_worker.usage.engine`): every byte goes
through :meth:`~arcilogger_worker.usage.engine.DayBuilder.add_ip` over the snapshot's minutes
(``DayBuilder(..., window=...)``), so the increment rule (an interim log's deltas at its own time,
the final log only the rest), the spreading over minutes, attribution, destinations and the
background rules are Kullanım's. Only the selection of the rows is the job's own:

* **New rows** of ``fg_traffic`` (forward sessions from internal addresses, as the engine reads
  them) are read by ``id``: above the highest id the table had :data:`ID_MARGIN` ago (its
  sequence), in pages, so a batch committed late, with lower ids than a row already read, is
  still read; the ids already counted are skipped.
* **A session** (``srcip``, ``sessionid``, ``dstip``, ``dstport``) whose newest log is its final
  log is counted once, with all of its logs, into the closed minutes. A session that can still
  log (its newest log an interim one, less than ``usage.session_overlap_seconds`` old) keeps its
  logs and its count, and is counted again when logs come for it (or drop out of the range the
  engine reads), so its bytes follow the engine's rule however its logs are spread over ticks;
  it is counted whole once more when it ends. Its logs older than the snapshot's first minute
  less the overlap are dropped, as the engine's read leaves them out.
* **Names:** a session's web filter and application logs (by session id) are read when it is
  first counted, and the DNS answers that can name it (for its address, at most
  ``usage.dns_window_seconds`` before its start; the engine takes the newest) only when no web
  filter or application log names it (the engine takes those names first). Both are kept while
  the session is open: they come when it starts.
* **One budget per tick** (:data:`TICK_BUDGET`, the file's write set aside): a tick reads and
  sorts the new rows, then counts in chunks, first the sessions that ended (so they never wait
  long), then the next warm-up slices, then the open sessions whose logs changed (those that
  changed longest ago first). What does not fit waits for the next tick; the details say how
  many sessions wait.
* **Warm-up:** after a start the last 60 minutes and the overlap before them are read in slices
  of :data:`WARMUP_SLICE` within that budget. The snapshot says ``warming_up`` until they are
  read and every session is counted.

**The file** holds subject keys (``person:12``, ``device:<mac>``, ``ip:<addr>``), their VLAN,
MAC and address, and per minute and subject the bytes, the sessions started and the bytes of the
:data:`TOP_PER_MINUTE` largest destinations (a registrable domain, a service name or an address)
and applications, named through the tables ``destinations`` and ``apps``: never a name, a user,
a URL or a raw line. It is sparse (only the subject-minutes with traffic), covers the last 60
minutes, bytes are whole numbers, and it is replaced whole (a temporary file renamed), only
when it changed. The API
applies access, the KVKK notice, pseudonyms and ``usage_excluded`` when it answers.
"""

from __future__ import annotations

import itertools
import json
import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Engine, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.engine import session_factory
from arcilogger_db.models import JobRun
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.domains import BackgroundRules, DomainNamer, normalize_hostname
from arcilogger_worker.usage.engine import (
    BUCKET,
    DNS_TOLERANCE,
    PRIVATE,
    REFUSED_ACTIONS,
    DayBuilder,
)
from arcilogger_worker.usage.subjects import IpTimeline, build_timelines

#: The job's name in ``job_runs`` and the worker's status file.
JOB_NAME: Final = "activity.tick"
#: The thread ticks this often, records its ticks in ``job_runs`` at most this often, and
#: after failures waits up to this long.
ACTIVITY_EVERY: Final = timedelta(seconds=10)
RECORD_EVERY: Final = timedelta(minutes=1)
BACKOFF_MAX: Final = timedelta(minutes=5)
#: The seconds one tick of the thread may count, of its 10 (the job loop does not wait on it).
THREAD_BUDGET: Final = 7.0
#: Minutes of the snapshot.
MINUTES: Final = 60
#: Rows committed late: each tick reads the ids above the table's highest id of this long ago,
#: skipping those already counted. Ingest commits a batch within its ``batch_seconds`` (0.5 s
#: by default); this is ten times that.
ID_MARGIN: Final = timedelta(seconds=5)
#: The seconds one tick may take (M4 guard (b) and (c): 2 s), its file write included: the
#: start-up's slices and the open sessions counted again fill what the new rows leave.
TICK_BUDGET: Final = 1.5
#: One slice of the start-up's read.
WARMUP_SLICE: Final = timedelta(seconds=5)
#: Sessions that ended, and open sessions whose logs changed, counted in one go.
ENDED_CHUNK: Final = 500
RECOUNT_CHUNK: Final = 500
#: New rows are read in pages of this many.
MAX_ROWS: Final = 20_000
#: A row committed this long after its time is not read by id (the warm-up's slices read it
#: when its time is not covered yet): a live view, the day's rollup counts it. Each tick's read
#: covers only these minutes, not the whole range.
LATE_ROWS: Final = timedelta(minutes=5)
#: A new session whose first row says it ran this long (or ``usage.session_overlap_seconds``,
#: when shorter) may have logged before: its earlier logs are looked up, and if they were
#: counted, taken back and counted with it.
EARLIER_SECONDS: Final = 60
#: The most ended sessions waiting to be counted; beyond it the oldest are dropped (logged).
ENDED_LIMIT: Final = 200_000
SNAPSHOT_VERSION: Final = 1
#: Destinations and applications kept per subject and minute (the answer shows 3 per window).
TOP_PER_MINUTE: Final = 5
#: The DNS answers read per session: the newest; more only when none of them has a usable name.
DNS_CANDIDATES: Final = 4
#: Bytes below this after a session's count was taken back are rounding: the cell is removed.
EPSILON: Final = 1e-6
#: The file's tables of names start again when they hold this many more names than the
#: snapshot uses (names leave the window).
NAMES_SPARE: Final = 5_000

#: Columns as the engine reads them (``engine._TRAFFIC`` without ``srcip``).
_TRAFFIC_COLUMNS: Final = (
    "sessionid, dstip, dstport, time, duration, sentbyte, rcvdbyte, sentdelta, rcvddelta, "
    "durationdelta, is_interim, app, appcat, dstinetsvc"
)
_FORWARD: Final = (
    f"subtype = 'forward' AND (action IS NULL OR action <> ALL(:refused)) AND {PRIVATE}"
)
_NEW_ROWS = text(
    f"SELECT id, host(srcip), {_TRAFFIC_COLUMNS} FROM fg_traffic "  # noqa: S608 - fixed text
    f"WHERE id > :after AND time >= :lo AND time < :hi AND {_FORWARD} ORDER BY id LIMIT :limit"
)
_SLICE_ROWS = text(
    f"SELECT id, host(srcip), {_TRAFFIC_COLUMNS} FROM fg_traffic "  # noqa: S608 - fixed text
    f"WHERE time >= :lo AND time < :hi AND {_FORWARD} ORDER BY time, id"
)
#: The earlier logs of sessions (by address and session id) that a new row continues.
_EARLIER_ROWS = text(
    f"SELECT id, host(srcip), {_TRAFFIC_COLUMNS} FROM fg_traffic "  # noqa: S608 - fixed text
    "WHERE sessionid = ANY(:sessions) AND srcip = ANY(CAST(:ips AS inet[])) AND time >= :lo "
    f"AND {_FORWARD}"
)
#: The highest id handed out (committed or not): the sequence, not ``max(id)`` (no index).
_LAST_ID = text(
    "SELECT coalesce(pg_sequence_last_value(pg_get_serial_sequence('fg_traffic', 'id')), 0)"
)
_WEB = text(
    "SELECT host(srcip), sessionid, time, hostname, url, cat, catdesc, action FROM fg_web "
    "WHERE srcip = ANY(CAST(:ips AS inet[])) AND sessionid = ANY(:sessions) AND time >= :lo "
    "ORDER BY srcip, time, id"
)
_APP = text(
    "SELECT host(srcip), sessionid, time, hostname, app, appcat, action, extra ->> 'subtype' "
    "FROM fg_app WHERE srcip = ANY(CAST(:ips AS inet[])) AND sessionid = ANY(:sessions) "
    "AND time >= :lo ORDER BY srcip, time, id"
)
#: Per session the newest DNS answers for its address that can name it (the engine takes the
#: newest one at most ``DNS_TOLERANCE`` after the start and ``dns_window_seconds`` before it).
_DNS = text(
    "SELECT k.n, d.id, d.time, d.qname, d.answers, d.cat, d.catdesc, d.action "
    "FROM unnest(CAST(:ips AS inet[]), CAST(:addresses AS inet[]), "
    "CAST(:los AS timestamptz[]), CAST(:his AS timestamptz[])) WITH ORDINALITY "
    "AS k(ip, address, lo, hi, n) "
    "CROSS JOIN LATERAL (SELECT id, time, qname, answers, cat, catdesc, action FROM fg_dns "
    "WHERE srcip = k.ip AND time >= k.lo AND time <= k.hi AND answers @> ARRAY[k.address] "
    "ORDER BY time DESC, id DESC LIMIT :limit) AS d"
)

log = logging.getLogger(__name__)

Key = tuple[Any, ...]
#: [download, upload, (destination, kind) → foreground bytes, application → bytes]
Cell = list[Any]
#: minute (epoch seconds) → subject key → cell
Cells = dict[int, dict[str, Cell]]


@dataclass
class Counted:
    """What a set of sessions adds to the snapshot."""

    cells: Cells = field(default_factory=dict)
    #: minute → subject key → sessions started
    sessions: dict[int, dict[str, int]] = field(default_factory=dict)
    #: person or device subject key → {"ip": its main address}
    subjects: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The newest traffic log counted (epoch seconds).
    newest: float | None = None


@dataclass
class OpenSession:
    """The logs so far of a session that can still log, and what they count for."""

    ip: str
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    #: The ids of ``rows`` in ``fg_traffic``.
    ids: set[int] = field(default_factory=set)
    #: The newest log's time (epoch seconds).
    newest: float = 0.0
    #: The engine's count of ``rows`` as last taken (in the open sum until it is taken again).
    counted: Counted | None = None
    #: Logs came (or dropped out) since: to be counted again.
    stale: bool = True
    #: Its final log came (a late interim log does not open it again).
    final: bool = False
    #: The DNS answers that can name it (read once: they come before the session starts).
    dns: list[tuple[Any, ...]] | None = None
    #: Its web filter and application logs as last read (an open session counted again uses
    #: them; its count when it ends reads them again).
    names: tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]] | None = None

    def start(self) -> float:
        return float(min((row[3].timestamp() - (row[4] or 0) for row in self.rows), default=0.0))


class Snapshot:
    """The worker's rolling snapshot: kept in memory by the process, written to ``path``."""

    def __init__(
        self,
        path: Path,
        *,
        minutes: int = MINUTES,
        warmup_slice: timedelta = WARMUP_SLICE,
        budget: float = TICK_BUDGET,
    ) -> None:
        self.path = path
        #: Minutes kept, one slice of the start-up's read and the seconds of a tick (at least
        #: one slice and one chunk of open sessions; the tests set them).
        self.minutes = minutes
        self.warmup_slice = warmup_slice
        self.budget = budget
        #: What the last writes and counts took (seconds), to keep a tick within its budget.
        self._write_cost = 0.0
        self._piece_cost = 0.0
        self._pieces_seconds = 0.0
        #: What one step of counting took lately (a chunk or a slice; a running average).
        self._step_cost = 0.0
        #: The sessions that ended, and the sum of the open sessions' counts.
        self.closed = Counted()
        self.opened = Counted()
        self.open: dict[Key, OpenSession] = {}
        #: The sessions that ended, waiting to be counted (in the order they ended).
        self.ended: dict[Key, OpenSession] = {}
        #: Ids read above the oldest mark, and the table's highest id at each tick.
        self.seen: set[int] = set()
        self.marks: list[tuple[float, int]] = []
        #: Warm-up: the next slice's start (``None``: read), then the open sessions counted.
        self.warm_from: datetime | None = None
        self.settled = False
        self.started = False
        self.written: str | None = None
        self._names: tuple[Any, DomainNamer, BackgroundRules] | None = None
        #: The addresses' attribution over this tick's minutes (read once a tick).
        self._timelines: tuple[tuple[datetime, datetime], dict[str, IpTimeline]] | None = None
        #: The first minute the last prune kept.
        self._pruned = 0
        #: The file: per minute its JSON and its subjects, the minutes whose totals changed
        #: since, and the tables of destinations and applications the minutes refer to.
        #: minute → subject → (its JSON, the destination and application ids it names)
        self._pieces: dict[int, dict[str, tuple[str, frozenset[int], frozenset[int]]]] = {}
        self._fragments: dict[int, str] = {}
        self._dirty: set[tuple[int, str]] = set()
        self._destinations: dict[tuple[str, str], int] = {}
        self._apps: dict[str, int] = {}

    # -- the tick ---------------------------------------------------------------------------------

    def tick(
        self, session: Session, now: datetime, stop: threading.Event | None = None
    ) -> dict[str, Any]:
        """Read what came since the last tick and sort it into sessions, then count, as long as
        the tick's budget lasts: the sessions that ended, the next warm-up slices, the open
        sessions whose logs changed. Write the snapshot when it changed. Returns the job's
        details."""
        began = time.monotonic()
        settings = UsageSettings.load(session)
        overlap = timedelta(seconds=settings.session_overlap_seconds)
        now_utc = now.astimezone(UTC)
        end = now_utc.replace(second=0, microsecond=0) + timedelta(seconds=BUCKET)
        lo = end - timedelta(minutes=self.minutes)
        last_id = int(session.execute(_LAST_ID).scalar_one())
        if not self.started:
            self.started = True
            self.marks = [(began, last_id)]
            self.warm_from = lo - overlap
        counts = dict.fromkeys(
            (
                "rows",
                "ended",
                "closed_sessions",
                "slices",
                "recounted",
                "reopened",
                "dropped",
                "ended_dropped",
            ),
            0,
        )
        # The rows committed since (their time at most LATE_ROWS back): up to the time the
        # warm-up has read, all of it after.
        covered = end if self.warm_from is None else self.warm_from
        after = mark = self._after(began)
        new: dict[Key, OpenSession] = {}
        while stop is None or not stop.is_set():
            rows = session.execute(
                _NEW_ROWS,
                {
                    "after": after,
                    "lo": max(lo - overlap, end - LATE_ROWS),
                    "hi": covered if self.warm_from is not None else end + overlap,
                    "limit": MAX_ROWS,
                    "refused": list(REFUSED_ACTIONS),
                },
            ).all()
            # A session is over after the overlap without a log, counted back from now (or from
            # the time the warm-up has read).
            self._sort(rows, overlap, now_utc if self.warm_from is None else covered, counts, new)
            if len(rows) < MAX_ROWS:
                break
            after = rows[-1][0]
        front = covered if self.warm_from is not None else end + overlap
        self._reopen(session, settings, lo, end, overlap, new, mark, front, counts)
        self._mark(began, last_id)
        self._prune(lo, overlap, counts)
        read = time.monotonic()
        while True:
            step = time.monotonic()
            if self.ended:
                counts["closed_sessions"] += self._close(session, settings, lo, end, overlap)
            elif self.warm_from is not None:
                until = min(self.warm_from + self.warmup_slice, end)
                rows = session.execute(
                    _SLICE_ROWS,
                    {"lo": self.warm_from, "hi": until, "refused": list(REFUSED_ACTIONS)},
                ).all()
                self.warm_from = None if until >= end else until
                new = {}
                self._sort(rows, overlap, until, counts, new)
                self._reopen(session, settings, lo, end, overlap, new, mark, until, counts)
                counts["slices"] += 1
            elif any(known.stale for known in self.open.values()):
                counts["recounted"] += self._recount(session, settings, lo, end, overlap)
            else:
                break
            # What a step takes lately (a chunk of sessions or a slice): the next one starts only
            # when it fits in what the tick has left.
            self._step_cost = 0.5 * self._step_cost + 0.5 * (time.monotonic() - step)
            if self._left(began) < self._step_cost or (stop is not None and stop.is_set()):
                break
        stale = sum(1 for known in self.open.values() if known.stale)
        if self.warm_from is None and not self.ended and not stale:
            self.settled = True
        writing = time.monotonic()
        changed = self._write(now, lo, end)
        done = time.monotonic()
        if changed:
            self._write_cost = done - writing - self._pieces_seconds
        return {
            **counts,
            "ended_waiting": len(self.ended),
            "stale_sessions": stale,
            "open_sessions": len(self.open),
            "warming_up": not self.settled,
            "written": changed,
            "read_seconds": round(read - began, 3),
            "count_seconds": round(writing - read, 3),
            "write_seconds": round(done - writing, 3),
            "seconds": round(done - began, 3),
        }

    def _left(self, began: float) -> float:
        """Seconds this tick still has for counting, the file's write set aside."""
        writing = self._write_cost + self._piece_cost * len(self._dirty)
        return self.budget - (time.monotonic() - began) - writing

    def _after(self, now: float) -> int:
        """The id to read after: the table's highest id :data:`ID_MARGIN` ago."""
        horizon = now - ID_MARGIN.total_seconds()
        earlier = [mark for at, mark in self.marks if at <= horizon]
        return earlier[-1] if earlier else self.marks[0][1]

    def _mark(self, now: float, last_id: int) -> None:
        self.marks.append((now, last_id))
        horizon = now - ID_MARGIN.total_seconds()
        # Keep the newest mark at or before the horizon (the next tick reads after it).
        while len(self.marks) > 1 and self.marks[1][0] <= horizon:
            self.marks.pop(0)
        low = self.marks[0][1]
        self.seen = {row_id for row_id in self.seen if row_id > low}

    # -- counting ---------------------------------------------------------------------------------

    def _sort(
        self,
        rows: Sequence[Sequence[Any]],
        overlap: timedelta,
        until: datetime,
        counts: dict[str, int],
        new: dict[Key, OpenSession],
    ) -> None:
        """Sort the rows not read before into the sessions: an interim log keeps a session open
        unless its final log came, a final log ends it, and so does no log for the overlap
        before ``until`` (the end of the rows read). The sessions that ended wait in ``ended``
        to be counted. A session first seen here whose row says it ran for a while goes into
        ``new``, to look for its earlier logs."""
        for row in rows:
            if row[0] in self.seen:
                continue
            self.seen.add(row[0])
            counts["rows"] += 1
            ip, values = str(row[1]), tuple(row[2:])
            key: Key = (
                (ip, values[0], values[1], values[2]) if values[0] is not None else (ip, row[0])
            )
            known = self.open.pop(key, None) or self.ended.pop(key, None)
            if known is None:
                known = OpenSession(ip)
                # A session ends after the overlap without a log, so one that ran at least that
                # long (EARLIER_SECONDS at most) may have logged before.
                earlier = min(EARLIER_SECONDS, overlap.total_seconds())
                if values[0] is not None and (values[4] or 0) >= earlier:
                    new[key] = known
            known.stale = True
            known.rows.append(values)
            known.ids.add(int(row[0]))
            known.newest = max(known.newest, values[3].timestamp())
            known.final = known.final or not values[10] or values[0] is None
            if known.final:
                self.ended[key] = known
                counts["ended"] += 1
            else:  # an interim log: the session goes on
                self.open[key] = known
        horizon = until.timestamp() - overlap.total_seconds()
        for key in [key for key, known in self.open.items() if known.newest < horizon]:
            self.ended[key] = self.open.pop(key)  # no log for the overlap: it is over
            counts["ended"] += 1
        excess = len(self.ended) - ENDED_LIMIT
        if excess > 0:
            # Overload only. A dropped session that logs again later has a count taken back
            # that never went into the closed minutes; a piece never shows less than 0.
            for key in list(itertools.islice(self.ended, excess)):
                self._forget(self.ended.pop(key))
            counts["ended_dropped"] += excess
            log.warning(
                "activity.tick falls behind: %s ended sessions dropped uncounted (more than %s "
                "waiting)",
                excess,
                ENDED_LIMIT,
            )

    def _forget(self, known: OpenSession) -> None:
        """Take a session's open count out of the open sum."""
        if known.counted is not None:
            self._dirty |= _pairs(known.counted)
            _add(self.opened, known.counted, sign=-1)
            known.counted = None

    def _reopen(
        self,
        session: Session,
        settings: UsageSettings,
        lo: datetime,
        end: datetime,
        overlap: timedelta,
        new: dict[Key, OpenSession],
        mark: int,
        front: datetime,
        counts: dict[str, int],
    ) -> None:
        """Sessions first seen this tick that logged before: a pause longer than the overlap
        ended them, or a log committed late came after their final log was counted. Their
        earlier logs (read already: before ``front``, the end of the times read, and at most
        ``mark`` or seen) were counted as an ended session; that count is taken back out of the
        closed minutes, and the session goes on with all of its logs, so nothing is counted
        twice."""
        if not new:
            return
        found = session.execute(
            _EARLIER_ROWS,
            {
                "sessions": sorted({key[1] for key in new}),
                "ips": sorted({key[0] for key in new}),
                "lo": lo - overlap,
                "refused": list(REFUSED_ACTIONS),
            },
        )
        earlier: dict[Key, OpenSession] = {}
        for row in found:
            ip, values = str(row[1]), tuple(row[2:])
            key = (ip, values[0], values[1], values[2])
            known = new.get(key)
            if known is None or row[0] in known.ids:
                continue
            if row[5] >= front or (row[0] > mark and row[0] not in self.seen):
                continue  # not read yet: it comes as a new row
            old = earlier.setdefault(key, OpenSession(ip))
            old.rows.append(values)
            old.ids.add(int(row[0]))
        if not earlier:
            return
        olds = list(earlier.values())
        for counted in self._counts(session, settings, [[old] for old in olds], lo, end, overlap):
            self._dirty |= _pairs(counted)
            _add(self.closed, counted, sign=-1)
        for key, old in earlier.items():
            known = new[key]
            known.rows = old.rows + known.rows
            known.ids |= old.ids
            known.newest = max(known.newest, *(row[3].timestamp() for row in old.rows))
            known.final = known.final or any(not row[10] for row in old.rows)
            known.dns = known.names = None
            if known.final and self.open.pop(key, None) is not None:
                self.ended[key] = known
            counts["reopened"] += 1

    def _close(
        self,
        session: Session,
        settings: UsageSettings,
        lo: datetime,
        end: datetime,
        overlap: timedelta,
    ) -> int:
        """Count :data:`ENDED_CHUNK` of the sessions that ended into the closed minutes, their
        open count taken out of the open sum. Returns how many."""
        keys = list(itertools.islice(self.ended, ENDED_CHUNK))
        chunk = [self.ended[key] for key in keys]
        # A session never counted while open adds only new subject-minutes; one that was is
        # counted whole again, and only the difference from its open count changes the file.
        new = [known for known in chunk if known.counted is None]
        known_before = [known for known in chunk if known.counted is not None]
        fresh, again = self._counts(
            session, settings, [new, known_before], lo, end, overlap, cached=True
        )
        before = [known.counted for known in known_before if known.counted is not None]
        self._dirty |= _pairs(fresh)
        self._dirty |= _pairs(_difference([again], before))
        for counted in before:
            _add(self.opened, counted, sign=-1)
        _add(self.closed, fresh)
        _add(self.closed, again)
        for key in keys:  # only now: a failed count leaves them waiting
            self.ended.pop(key, None)
        return len(chunk)

    def _recount(
        self,
        session: Session,
        settings: UsageSettings,
        lo: datetime,
        end: datetime,
        overlap: timedelta,
    ) -> int:
        """Count :data:`RECOUNT_CHUNK` of the open sessions whose logs changed (those that
        changed longest ago first); the others wait. Returns how many."""
        chunk = list(
            itertools.islice((known for known in self.open.values() if known.stale), RECOUNT_CHUNK)
        )
        counts = self._counts(
            session, settings, [[known] for known in chunk], lo, end, overlap, cached=True
        )
        difference = _difference(
            counts, [known.counted for known in chunk if known.counted is not None]
        )
        self._dirty |= _pairs(difference)
        _add(self.opened, difference)
        for known, counted in zip(chunk, counts, strict=True):
            known.counted, known.stale = counted, False
        return len(chunk)

    def _engine(self, settings: UsageSettings) -> tuple[DomainNamer, BackgroundRules]:
        """The settings' public suffix list and background rules (kept while they hold)."""
        key = (
            settings.psl_file,
            repr(settings.psl_private_suffixes),
            repr(settings.background_domains),
            repr(settings.background_appcats),
            repr(settings.background_ports),
        )
        if self._names is None or self._names[0] != key:
            namer = DomainNamer(settings.psl_file, settings.psl_private_suffixes)
            rules = BackgroundRules.create(
                settings.background_domains,
                settings.background_appcats,
                settings.background_ports,
            )
            self._names = (key, namer, rules)
        return self._names[1], self._names[2]

    def _counts(
        self,
        session: Session,
        settings: UsageSettings,
        groups: Sequence[Sequence[OpenSession]],
        lo: datetime,
        end: datetime,
        overlap: timedelta,
        *,
        cached: bool = False,
    ) -> list[Counted]:
        """The engine's count of each group of sessions over the minutes ``[lo, end)``: one
        builder and one read of the web filter, application and DNS logs for all of them
        (``cached``: the web filter and application logs already read for a session are used)."""
        results = [Counted() for _ in groups]
        everyone = [known for group in groups for known in group]
        if not everyone:
            return results
        namer, rules = self._engine(settings)
        builder = DayBuilder(
            site_day(end - timedelta(seconds=BUCKET)), settings, namer, rules, window=(lo, end)
        )
        ips = sorted({known.ip for known in everyone})
        timelines = self._timelines_of(session, ips, lo, end)
        unread = [known for known in everyone if not cached or known.names is None]
        sessionids = sorted({row[0] for known in unread for row in known.rows if row[0]})
        since = lo - overlap
        web: dict[tuple[str, int], list[tuple[Any, ...]]] = {}
        app: dict[tuple[str, int], list[tuple[Any, ...]]] = {}
        if sessionids:
            unread_ips = sorted({known.ip for known in unread})
            bounds = {"ips": unread_ips, "sessions": sessionids, "lo": since}
            web = _by_session(session.execute(_WEB, bounds))
            app = _by_session(session.execute(_APP, bounds))
        for known in unread:
            sessionid = known.rows[0][0]
            known.names = (
                list(web.get((known.ip, sessionid), ())),
                list(app.get((known.ip, sessionid), ())),
            )
        # The engine names a session by its web filter or application host name first: only
        # the sessions without one need their DNS answers.
        _answers(
            session,
            settings,
            [known for known in everyone if known.dns is None and not _host(known)],
        )
        for group, counted in zip(groups, results, strict=True):
            by_ip: dict[str, list[OpenSession]] = defaultdict(list)
            for known in group:
                by_ip[known.ip].append(known)
            for ip in sorted(by_ip):
                # The engine's order (``_TRAFFIC``): by session, then time; a row without a
                # session id stands alone, last.
                traffic = sorted(
                    (row for known in by_ip[ip] for row in known.rows),
                    key=lambda r: (r[0] is None, r[0] or 0, str(r[1]), r[2] or 0, r[3], r[4] or 0),
                )
                answers = {row[0]: row for known in by_ip[ip] for row in known.dns or ()}
                web_rows, app_rows = _names_of(by_ip[ip])
                builder.add_ip(
                    ip,
                    timelines[ip],
                    traffic,
                    web_rows,
                    app_rows,
                    [row[1:] for row in sorted(answers.values(), key=lambda r: (r[1], r[0]))],
                )
                _started(builder, timelines[ip], traffic, counted)
            _harvest(builder, counted)
        return results

    def _timelines_of(
        self, session: Session, ips: Sequence[str], lo: datetime, end: datetime
    ) -> dict[str, IpTimeline]:
        if self._timelines is None or self._timelines[0] != (lo, end):
            self._timelines = ((lo, end), {})
        known = self._timelines[1]
        missing = [ip for ip in ips if ip not in known]
        if missing:
            known.update(build_timelines(session, missing, lo, end))
        return known

    def _prune(self, lo: datetime, overlap: timedelta, counts: dict[str, int]) -> None:
        """Drop the minutes before ``lo`` and the subjects no minute holds any more, and the logs
        of open sessions older than the engine reads before its range (``lo`` less the
        overlap): a session left without logs is over (its open count taken out)."""
        first = int(lo.timestamp())
        if first != self._pruned:
            self._pruned = first
            for counted in (self.closed, self.opened):
                for table in (counted.cells, counted.sessions):
                    for minute in [minute for minute in table if minute < first]:
                        del table[minute]
            used = {
                key
                for counted in (self.closed, self.opened)
                for table in (counted.cells, counted.sessions)
                for found in table.values()
                for key in found
            }
            for counted in (self.closed, self.opened):
                counted.subjects = {k: v for k, v in counted.subjects.items() if k in used}
        oldest = lo - overlap
        for key, known in list(self.open.items()):
            if any(row[3] < oldest for row in known.rows):
                known.rows = [row for row in known.rows if row[3] >= oldest]
                known.stale = True
                if not known.rows:
                    del self.open[key]
                    self._forget(known)
                    counts["dropped"] += 1

    # -- the file ---------------------------------------------------------------------------------

    def merged(self) -> Counted:
        """The closed minutes plus the open sessions as last counted."""
        result = Counted()
        _add(result, self.closed)
        _add(result, self.opened)
        return result

    def _write(self, now: datetime, lo: datetime, end: datetime) -> bool:
        """Write the snapshot when its content changed; whether it was written. Only the
        subject-minutes whose totals changed are put together again, and only the names the
        minutes use are in the file."""
        first, last = int(lo.timestamp()), int(end.timestamp())
        for minute in [minute for minute in self._pieces if not first <= minute < last]:
            del self._pieces[minute]
            self._fragments.pop(minute, None)
        building = time.monotonic()
        dirty = len(self._dirty)
        touched = self._build(first, last)
        used_destinations = {i for p in self._pieces.values() for _, d, _ in p.values() for i in d}
        used_apps = {i for p in self._pieces.values() for _, _, a in p.values() for i in a}
        spare = len(self._destinations) + len(self._apps) - len(used_destinations) - len(used_apps)
        if spare > NAMES_SPARE:  # the tables start again: every piece is built again
            self._destinations.clear()
            self._apps.clear()
            self._dirty = {(m, key) for m, pieces in self._pieces.items() for key in pieces}
            self._pieces.clear()
            touched |= self._build(first, last)
            used_destinations = set(self._destinations.values())
            used_apps = set(self._apps.values())
        for minute in touched:
            entries = self._pieces.get(minute)
            if not entries:
                self._pieces.pop(minute, None)
                self._fragments.pop(minute, None)
                continue
            joined = ",".join(entries[key][0] for key in sorted(entries))
            self._fragments[minute] = f'{{"minute":"{_iso(minute)}","subjects":{{{joined}}}}}'
        self._pieces_seconds = time.monotonic() - building
        if dirty:
            # The minutes' joins included: what one changed subject-minute costs the write.
            self._piece_cost = self._pieces_seconds / dirty
        used = {key for pieces in self._pieces.values() for key in pieces}
        newest = max(
            (c.newest for c in (self.closed, self.opened) if c.newest is not None), default=None
        )
        info = {**self.opened.subjects, **self.closed.subjects}
        header = json.dumps(
            {
                "version": SNAPSHOT_VERSION,
                "from": _iso(first),
                "until": _iso(last),
                "warming_up": not self.settled,
                "data_until": None if newest is None else _iso(newest),
                "subjects": {key: info[key] for key in sorted(used) if info.get(key)},
                "destinations": {
                    str(i): list(name)
                    for name, i in self._destinations.items()
                    if i in used_destinations
                },
                "apps": {str(i): name for name, i in self._apps.items() if i in used_apps},
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        minutes = ",".join(self._fragments[minute] for minute in sorted(self._fragments))
        content = f'{header[:-1]},"minutes":[{minutes}]}}'
        if content == self.written:
            return False
        stamp = json.dumps(now.astimezone(UTC).isoformat())
        if not _replace(self.path, f'{{"generated_at":{stamp},{content[1:]}'):
            return False  # the API has it open (Windows): the next tick writes it
        self.written = content
        return True

    def _build(self, first: int, last: int) -> set[int]:
        """Build the pieces of the changed subject-minutes; the minutes touched."""
        touched: set[int] = set()
        for minute, key in self._dirty:
            if not first <= minute < last:
                continue
            touched.add(minute)
            piece = self._piece(minute, key)
            pieces = self._pieces.setdefault(minute, {})
            if piece is None:
                pieces.pop(key, None)
            else:
                pieces[key] = piece
        self._dirty.clear()
        return touched

    def _piece(self, minute: int, key: str) -> tuple[str, frozenset[int], frozenset[int]] | None:
        """One subject's entry of a minute (``None`` without traffic): ``"key":[download,
        upload, sessions, [[destination, bytes]], [[application, bytes]]]``, whole bytes, with
        the destination and application ids it names."""
        down = up = 0.0
        volumes: dict[tuple[str, str], float] = defaultdict(float)
        by_app: dict[str, float] = defaultdict(float)
        sessions = 0
        for counted in (self.closed, self.opened):
            cell = counted.cells.get(minute, {}).get(key)
            sessions += counted.sessions.get(minute, {}).get(key, 0)
            if cell is None:
                continue
            down += cell[0]
            up += cell[1]
            for name, volume in cell[2].items():
                volumes[name] += volume
            for name, volume in cell[3].items():
                by_app[name] += volume
        if round(down) <= 0 and round(up) <= 0 and sessions <= 0:
            return None
        destinations = [
            [self._destinations.setdefault(name, len(self._destinations)), round(volume)]
            for name, volume in _top(volumes, TOP_PER_MINUTE)
            if round(volume) > 0
        ]
        apps = [
            [self._apps.setdefault(name, len(self._apps)), round(volume)]
            for name, volume in _top(by_app, TOP_PER_MINUTE)
            if round(volume) > 0
        ]
        entry = [max(round(down), 0), max(round(up), 0), max(sessions, 0), destinations, apps]
        return (
            f"{json.dumps(key)}:{json.dumps(entry, separators=(',', ':'))}",
            frozenset(i for i, _ in destinations),
            frozenset(i for i, _ in apps),
        )


# -- the thread -----------------------------------------------------------------------------------


@dataclass
class Window:
    """What the thread's ticks did since its last ``job_runs`` row."""

    started: datetime
    ticks: int = 0
    failed: int = 0
    rows: int = 0
    #: Ended sessions dropped uncounted (more than ``ENDED_LIMIT`` waiting).
    ended_dropped: int = 0
    max_seconds: float = 0.0
    last: dict[str, Any] = field(default_factory=dict)


class Runner:
    """``activity.tick`` in its own thread of the worker (the owner's decision on #201,
    2026-11-06 07:08Z): a tick every :data:`ACTIVITY_EVERY`, with its own database engine, so the
    job loop never waits on it.

    * **Licence:** while ``usage_analytics`` is off (``gate``, given this thread's engine,
      returns the job loop's skip details) it idles and asks again at the next tick; one
      ``skipped`` row per site day. A failing gate is a failed tick.
    * **Visible like a job:** a ``job_runs`` row ``activity.tick`` at most every
      :data:`RECORD_EVERY` (``ok``, or ``failed`` when its last tick failed) with the ticks, the
      newest tick's seconds and the sessions waiting, and :meth:`status` for the worker's status
      file; an exception is logged and the next tick waits longer (up to :data:`BACKOFF_MAX`).
    * **Stop:** the service's stop event ends the tick under way after its current chunk (the
      snapshot is still written whole) and then the thread.
    """

    def __init__(
        self,
        engine: Engine,
        path: Path,
        *,
        gate: Callable[[datetime, Engine], Mapping[str, Any] | None],
        clock: Callable[[], datetime] = utc_now,
        every: timedelta = ACTIVITY_EVERY,
        budget: float = THREAD_BUDGET,
    ) -> None:
        self.engine = engine
        self.sessions = session_factory(engine)
        self.snapshot = Snapshot(path, budget=budget)
        self.gate = gate
        self.clock = clock
        self.every = every
        self.failures = 0
        self.runs = 0
        self.last_started: datetime | None = None
        self.last_finished: datetime | None = None
        self.last_status: str | None = None
        self.last_error: str | None = None
        #: The newest tick's details.
        self.details: dict[str, Any] = {}
        self._window: Window | None = None
        self._skip_day: date | None = None

    def run(self, stop: threading.Event) -> None:
        """Tick until ``stop`` is set."""
        while not stop.is_set():
            began = time.monotonic()
            self.once(stop)
            if self.failures:
                wait = min(self.every * 2**self.failures, BACKOFF_MAX).total_seconds()
            else:
                wait = self.every.total_seconds() - (time.monotonic() - began)
            stop.wait(max(wait, 0.0))
        self._record(self.clock(), final=True)

    def once(self, stop: threading.Event | None = None) -> str:
        """One tick (or its skip while the module is off); its status."""
        now = self.clock()
        self.last_started, self.runs = now, self.runs + 1
        try:
            gate = self.gate(now, self.engine)  # the licence, read through this thread's engine
            if gate is not None:
                self._skipped(now, gate)
                return "skipped"
            with self.sessions() as session:
                details = self.snapshot.tick(session, now, stop)
            status, self.failures, self.last_error = "ok", 0, None
        except Exception as exc:  # a failing tick must never end the thread
            log.exception("job activity.tick failed")
            status, self.failures = "failed", self.failures + 1
            self.last_error = f"{type(exc).__name__}: {str(exc)[:200]}"
            details = {"error": type(exc).__name__, "message": str(exc)[:500]}
        self.last_finished, self.last_status, self.details = self.clock(), status, details
        window = self._window or Window(started=now)
        self._window = window
        window.ticks += 1
        window.failed += status == "failed"
        window.rows += int(details.get("rows", 0))
        window.ended_dropped += int(details.get("ended_dropped", 0))
        window.max_seconds = max(window.max_seconds, float(details.get("seconds", 0.0)))
        window.last = details
        if self.last_finished - window.started >= RECORD_EVERY:
            self._record(self.last_finished)
        return status

    def status(self) -> dict[str, Any]:
        """The thread's state for the worker's status file (as a job's)."""

        def iso(value: datetime | None) -> str | None:
            return None if value is None else value.isoformat()

        return {
            "last_started": iso(self.last_started),
            "last_finished": iso(self.last_finished),
            "last_status": self.last_status,
            "last_error": self.last_error,
            "done_day": None,
            "next_due": None,
            "runs": self.runs,
            "failures": self.failures,
            "retry_at": None,
            "thread": True,
        }

    def _skipped(self, now: datetime, gate: Mapping[str, Any]) -> None:
        self.last_finished, self.last_status, self.last_error = now, "skipped", None
        self.failures = 0
        day = site_day(now)
        if self._skip_day == day:
            return
        details = {key: value for key, value in gate.items() if key != "_skipped"}
        if self._insert(now, now, "skipped", details):
            self._skip_day = day

    def _record(self, now: datetime, *, final: bool = False) -> None:
        """The ticks since the last row as one ``job_runs`` row."""
        window, self._window = self._window, None
        if window is None or (final and not window.ticks):
            return
        last = window.last
        details: dict[str, Any] = {
            "ticks": window.ticks,
            "failed": window.failed,
            "rows": window.rows,
            "ended_dropped": window.ended_dropped,
            "max_seconds": round(window.max_seconds, 3),
            **{
                key: last[key]
                for key in (
                    "seconds",
                    "ended_waiting",
                    "stale_sessions",
                    "open_sessions",
                    "warming_up",
                    "error",
                    "message",
                )
                if key in last
            },
        }
        status = "failed" if "error" in last else "ok"
        self._insert(window.started, now, status, details)

    def _insert(
        self, started: datetime, finished: datetime, status: str, details: Mapping[str, Any]
    ) -> bool:
        try:
            with self.sessions() as session:
                session.add(
                    JobRun(
                        job=JOB_NAME,
                        started_at=started,
                        finished_at=finished,
                        status=status,
                        details=json.loads(json.dumps(details, default=str)),
                    )
                )
                session.commit()
        except SQLAlchemyError as exc:
            log.warning("job activity.tick: its run is not recorded (%s)", type(exc).__name__)
            return False
        return True


def _replace(path: Path, content: str) -> bool:
    """Write ``path`` through a temporary file and a rename, so a reader sees the old file or
    the new one. Not flushed to the disk: the snapshot is rewritten every tick and rebuilt from
    the logs after a restart. ``False`` when the rename was refused (a reader holds the file
    open on Windows)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8", newline="\n")
    try:
        temporary.replace(path)
    except PermissionError:
        return False
    return True


def _iso(moment: float) -> str:
    return datetime.fromtimestamp(moment, UTC).isoformat()


def _top[T](values: Mapping[T, float], count: int) -> list[tuple[T, float]]:
    return sorted(values.items(), key=lambda item: (-item[1], str(item[0])))[:count]


def _by_session(rows: Iterable[Sequence[Any]]) -> dict[tuple[str, int], list[tuple[Any, ...]]]:
    """Web filter or application rows by (address, session id), in their time order."""
    found: dict[tuple[str, int], list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        found[(str(row[0]), row[1])].append(tuple(row[1:]))
    return found


def _host(known: OpenSession) -> bool:
    """Whether a web filter or application log names the session (rows ``(sessionid, time,
    hostname, …)``), as the engine reads them."""
    web, app = known.names or ([], [])
    return any(normalize_hostname(row[2]) for row in (*web, *app))


def _names_of(
    sessions: Sequence[OpenSession],
) -> tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]]:
    """The sessions' web filter and application logs, each in time order (by session)."""
    ordered = sorted(sessions, key=lambda known: known.rows[0][0] or 0)
    web = [row for known in ordered for row in (known.names or ([], []))[0]]
    app = [row for known in ordered for row in (known.names or ([], []))[1]]
    return web, app


def _answers(session: Session, settings: UsageSettings, sessions: Sequence[OpenSession]) -> None:
    """Read each session's DNS answers (``known.dns``: rows ``(id, time, qname, answers, cat,
    catdesc, action)``): the newest :data:`DNS_CANDIDATES` for its address in the engine's
    window before its start, all of them when none of those has a usable name."""
    lookups: list[tuple[OpenSession, str, datetime, datetime]] = []
    for known in sessions:
        known.dns = []
        address = next((str(row[1]) for row in known.rows if row[1] is not None), None)
        if address is None:
            continue
        start = known.start()
        lookups.append(
            (
                known,
                address,
                datetime.fromtimestamp(start - settings.dns_window_seconds, UTC),
                datetime.fromtimestamp(start + DNS_TOLERANCE, UTC),
            )
        )
    found = _lookup(session, lookups, DNS_CANDIDATES)
    unusable = [
        number
        for number, rows in found.items()
        if len(rows) == DNS_CANDIDATES and all(normalize_hostname(row[2]) is None for row in rows)
    ]
    if unusable:
        again = _lookup(session, [lookups[number] for number in unusable], None)
        for index, number in enumerate(unusable):
            found[number] = again.get(index, [])
    for number, rows in found.items():
        lookups[number][0].dns = rows


def _lookup(
    session: Session,
    lookups: Sequence[tuple[OpenSession, str, datetime, datetime]],
    limit: int | None,
) -> dict[int, list[tuple[Any, ...]]]:
    """DNS rows per lookup (by its index in ``lookups``)."""
    found: dict[int, list[tuple[Any, ...]]] = defaultdict(list)
    if not lookups:
        return found
    rows = session.execute(
        _DNS,
        {
            "ips": [known.ip for known, _, _, _ in lookups],
            "addresses": [address for _, address, _, _ in lookups],
            "los": [lo for _, _, lo, _ in lookups],
            "his": [hi for _, _, _, hi in lookups],
            "limit": limit,
        },
    )
    for row in rows:
        found[int(row[0]) - 1].append((row[1], row[2], row[3], row[4], row[5], row[6], row[7]))
    return found


def _harvest(builder: DayBuilder, counted: Counted) -> None:
    """Move the builder's cells into ``counted`` (the builder goes on with the next group)."""
    for key, buckets in builder.cells.items():
        if builder.subjects[key].kind in ("person", "device"):
            # The one hint the answer needs: the address its row shows (``Directory.resolve``
            # takes the kind and MAC from the key, and the row has no department).
            counted.subjects[key] = {"ip": _most(builder.subject_ips.get(key))}
        for index, flows in buckets.items():
            minute = int(builder.lo_ts + index * BUCKET)
            cell = counted.cells.setdefault(minute, {}).get(key)
            if cell is None:
                cell = counted.cells[minute][key] = [0.0, 0.0, {}, {}]
            for flow_id, (_, received, sent) in flows.items():
                flow = builder.flow_list[flow_id]
                volume = received + sent
                cell[0] += received
                cell[1] += sent
                if not flow.background:
                    name = (flow.domain, flow.kind)
                    cell[2][name] = cell[2].get(name, 0.0) + volume
                if flow.app:
                    cell[3][flow.app] = cell[3].get(flow.app, 0.0) + volume
    counted.newest = builder.newest
    builder.cells.clear()
    builder.newest = None


def _most(counter: Mapping[str, int] | None) -> str | None:
    if not counter:
        return None
    return max(counter.items(), key=lambda item: (item[1], item[0]))[0]


def _started(
    builder: DayBuilder, timeline: IpTimeline, rows: Sequence[tuple[Any, ...]], counted: Counted
) -> None:
    """Each session counts once, in the minute it started and for that minute's subject."""
    sessions: dict[Key, float] = {}
    for row in rows:
        key: Key = (row[0], row[1], row[2]) if row[0] is not None else (id(row),)
        start = row[3].timestamp() - (row[4] or 0)
        sessions[key] = min(sessions.get(key, start), start)
    for start in sessions.values():
        if not builder.lo_ts <= start < builder.hi_ts:
            continue  # started before the snapshot's minutes: not a session started in them
        minute = int(builder.lo_ts + ((start - builder.lo_ts) // BUCKET) * BUCKET)
        subject = timeline.at(minute).key
        bucket = counted.sessions.setdefault(minute, {})
        bucket[subject] = bucket.get(subject, 0) + 1


def _difference(added: Iterable[Counted], removed: Iterable[Counted]) -> Counted:
    """``added`` less ``removed`` (what is left at zero dropped): a session counted again
    changes only the minutes its new logs reach."""
    difference = Counted()
    for counted in added:
        _add(difference, counted)
    for counted in removed:
        _add(difference, counted, sign=-1)
    return difference


def _pairs(difference: Counted) -> set[tuple[int, str]]:
    """The subject-minutes a difference touches."""
    changed = {(minute, key) for minute, cells in difference.cells.items() for key in cells}
    changed.update(
        (minute, key) for minute, started in difference.sessions.items() for key in started
    )
    return changed


def _add(total: Counted, other: Counted, *, sign: int = 1) -> None:
    """Add ``other`` into ``total`` (``sign=-1``: take it back out, dropping what is left at
    zero)."""
    for minute, subjects in other.cells.items():
        target_minute = total.cells.setdefault(minute, {})
        for key, (down, up, volumes, by_app) in subjects.items():
            cell = target_minute.get(key)
            if cell is None:
                cell = target_minute[key] = [0.0, 0.0, {}, {}]
            cell[0] += sign * down
            cell[1] += sign * up
            for table, values in ((cell[2], volumes), (cell[3], by_app)):
                for name, volume in values.items():
                    left = table.get(name, 0.0) + sign * volume
                    if abs(left) < EPSILON:
                        table.pop(name, None)
                    else:
                        table[name] = left
            if max(abs(cell[0]), abs(cell[1])) < EPSILON and not (cell[2] or cell[3]):
                del target_minute[key]
        if not target_minute:
            del total.cells[minute]
    for minute, started in other.sessions.items():
        target_sessions = total.sessions.setdefault(minute, {})
        for key, count in started.items():
            left = target_sessions.get(key, 0) + sign * count
            if left:
                target_sessions[key] = left
            else:
                target_sessions.pop(key, None)
        if not target_sessions:
            del total.sessions[minute]
    if sign > 0:
        total.subjects.update(other.subjects)
        if other.newest is not None and (total.newest is None or other.newest > total.newest):
            total.newest = other.newest
