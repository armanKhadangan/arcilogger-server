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
import time
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.timeutil import site_day
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
SNAPSHOT_VERSION: Final = 1
#: Destinations and applications kept per subject and minute (the answer shows 3 per window).
TOP_PER_MINUTE: Final = 5
#: The DNS answers read per session: the newest; more only when none of them has a usable name.
DNS_CANDIDATES: Final = 4
#: Bytes below this after a session's count was taken back are rounding: the cell is removed.
EPSILON: Final = 1e-6
#: The file's tables of names start again when they grow past this (names leave the window).
NAMES_LIMIT: Final = 20_000

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
    #: subject key → {"kind", "vlan", "mac", "ip"}
    subjects: dict[str, dict[str, Any]] = field(default_factory=dict)
    #: The newest traffic log counted (epoch seconds).
    newest: float | None = None


@dataclass
class OpenSession:
    """The logs so far of a session that can still log, and what they count for."""

    ip: str
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    #: The newest log's time (epoch seconds).
    newest: float = 0.0
    #: The engine's count of ``rows`` as last taken (in the open sum until it is taken again).
    counted: Counted | None = None
    #: Logs came (or dropped out) since: to be counted again.
    stale: bool = True
    #: The DNS answers that can name it (read once: they come before the session starts).
    dns: list[tuple[Any, ...]] | None = None
    #: Its web filter and application logs as last read (an open session counted again uses
    #: them; its count when it ends reads them again).
    names: tuple[list[tuple[Any, ...]], list[tuple[Any, ...]]] | None = None

    def start(self) -> float:
        return float(min(row[3].timestamp() - (row[4] or 0) for row in self.rows))


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
        #: What one step of counting took lately (a chunk or a slice; it decays).
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
        #: The file: per minute its JSON and its subjects, the minutes whose totals changed
        #: since, and the tables of destinations and applications the minutes refer to.
        self._pieces: dict[int, dict[str, str]] = {}
        self._fragments: dict[int, str] = {}
        self._dirty: set[tuple[int, str]] = set()
        self._destinations: dict[tuple[str, str], int] = {}
        self._apps: dict[str, int] = {}

    # -- the tick ---------------------------------------------------------------------------------

    def tick(self, session: Session, now: datetime) -> dict[str, Any]:
        """Read what came since the last tick and sort it into sessions, then count, as long as
        the tick's budget lasts: the sessions that ended, the next warm-up slices, the open
        sessions whose logs changed. Write the snapshot when it changed. Returns the job's
        details."""
        began = time.monotonic()
        settings = UsageSettings.load(session)
        overlap = timedelta(seconds=settings.session_overlap_seconds)
        end = now.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(seconds=BUCKET)
        lo = end - timedelta(minutes=self.minutes)
        last_id = int(session.execute(_LAST_ID).scalar_one())
        if not self.started:
            self.started = True
            self.marks = [(began, last_id)]
            self.warm_from = lo - overlap
        counts = {"rows": 0, "ended": 0, "closed_sessions": 0, "slices": 0, "recounted": 0}
        # The rows committed since: up to the time the warm-up has read, all of it after.
        covered = end if self.warm_from is None else self.warm_from
        after = self._after(began)
        while True:
            rows = session.execute(
                _NEW_ROWS,
                {
                    "after": after,
                    "lo": lo - overlap,
                    "hi": covered if self.warm_from is not None else end + overlap,
                    "limit": MAX_ROWS,
                    "refused": list(REFUSED_ACTIONS),
                },
            ).all()
            self._sort(rows, overlap, covered, counts)
            if len(rows) < MAX_ROWS:
                break
            after = rows[-1][0]
        self._mark(began, last_id)
        self._prune(lo, overlap)
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
                self._sort(rows, overlap, until, counts)
                counts["slices"] += 1
            elif any(known.stale for known in self.open.values()):
                counts["recounted"] += self._recount(session, settings, lo, end, overlap)
            else:
                break
            self._step_cost = max(self._step_cost * 0.8, time.monotonic() - step)
            if self._left(began) < self._step_cost:
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
    ) -> None:
        """Sort the rows not read before into the sessions: an interim log keeps a session open,
        a final log ends it, and so does no log for the overlap before ``until`` (the end of
        the rows read). The sessions that ended wait in ``ended`` to be counted."""
        for row in rows:
            if row[0] in self.seen:
                continue
            self.seen.add(row[0])
            counts["rows"] += 1
            ip, values = str(row[1]), tuple(row[2:])
            key: Key = (
                (ip, values[0], values[1], values[2]) if values[0] is not None else (ip, row[0])
            )
            known = self.open.pop(key, None) or self.ended.pop(key, None) or OpenSession(ip)
            known.stale = True
            known.rows.append(values)
            known.newest = max(known.newest, values[3].timestamp())
            if values[10] and values[0] is not None:  # an interim log: the session goes on
                self.open[key] = known
            else:
                self.ended[key] = known
                counts["ended"] += 1
        horizon = until.timestamp() - overlap.total_seconds()
        for key in [key for key, known in self.open.items() if known.newest < horizon]:
            self.ended[key] = self.open.pop(key)  # no log for the overlap: it is over
            counts["ended"] += 1

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
        chunk = [self.ended.pop(key) for key in keys]
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

    def _prune(self, lo: datetime, overlap: timedelta) -> None:
        """Drop the minutes before ``lo``, and the logs of open sessions older than the
        engine reads before its range (``lo`` less the overlap)."""
        first = int(lo.timestamp())
        for counted in (self.closed, self.opened):
            for table in (counted.cells, counted.sessions):
                for minute in [minute for minute in table if minute < first]:
                    del table[minute]
        oldest = lo - overlap
        for known in self.open.values():
            if known.rows[0][3] < oldest:
                known.stale = True
                known.rows = [row for row in known.rows if row[3] >= oldest]

    # -- the file ---------------------------------------------------------------------------------

    def merged(self) -> Counted:
        """The closed minutes plus the open sessions as last counted."""
        result = Counted()
        _add(result, self.closed)
        _add(result, self.opened)
        return result

    def _write(self, now: datetime, lo: datetime, end: datetime) -> bool:
        """Write the snapshot when its content changed; whether it was written. Only the
        subject-minutes whose totals changed are put together again."""
        first, last = int(lo.timestamp()), int(end.timestamp())
        if len(self._destinations) + len(self._apps) > NAMES_LIMIT:
            self._destinations.clear()
            self._apps.clear()
            self._dirty |= {(m, key) for m, pieces in self._pieces.items() for key in pieces}
            self._pieces.clear()
            self._fragments.clear()
        for minute in [minute for minute in self._pieces if not first <= minute < last]:
            del self._pieces[minute]
            self._fragments.pop(minute, None)
        building = time.monotonic()
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
        dirty = len(self._dirty)
        self._dirty.clear()
        for minute in touched:
            entries = self._pieces.get(minute)
            if not entries:
                self._pieces.pop(minute, None)
                self._fragments.pop(minute, None)
                continue
            joined = ",".join(entries[key] for key in sorted(entries))
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
                "subjects": {key: info[key] for key in sorted(used) if key in info},
                "destinations": [list(name) for name in self._destinations],
                "apps": list(self._apps),
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

    def _piece(self, minute: int, key: str) -> str | None:
        """One subject's entry of a minute (``None`` without traffic): ``"key":[download,
        upload, sessions, [[destination, bytes]], [[application, bytes]]]``, whole bytes."""
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
        if round(down) == 0 and round(up) == 0 and not sessions:
            return None
        entry = [
            round(down),
            round(up),
            sessions,
            [
                [self._destinations.setdefault(name, len(self._destinations)), round(volume)]
                for name, volume in _top(volumes, TOP_PER_MINUTE)
                if round(volume)
            ],
            [
                [self._apps.setdefault(name, len(self._apps)), round(volume)]
                for name, volume in _top(by_app, TOP_PER_MINUTE)
                if round(volume)
            ],
        ]
        return f"{json.dumps(key)}:{json.dumps(entry, separators=(',', ':'))}"


#: The snapshot of each data directory, kept by the worker process between its ticks.
_SNAPSHOTS: dict[Path, Snapshot] = {}


def tick_job(sessions: sessionmaker[Session], path: Path, now: datetime) -> dict[str, Any]:
    """The job ``activity.tick``: one tick of the snapshot written to ``path``."""
    snapshot = _SNAPSHOTS.setdefault(path, Snapshot(path))
    with sessions() as session:
        return snapshot.tick(session, now)


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
        subject = builder.subjects[key]
        counted.subjects[key] = {
            "kind": subject.kind,
            "vlan": subject.vlan,
            "mac": subject.mac,
            "ip": _most(builder.subject_ips.get(key)),
        }
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
