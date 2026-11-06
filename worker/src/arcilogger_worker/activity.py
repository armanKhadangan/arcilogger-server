"""Canlı Durum's rolling snapshot (Wave 5 contract §6.2 M2; the M4 decision on #201, 2026-11-06):
who sent and received how much in each of the last 60 minutes, kept up to date by the worker
job ``activity.tick`` and written to ``state/activity.json``, which ``GET /activity/devices``
answers from.

**The counting is the usage engine's** (:mod:`arcilogger_worker.usage.engine`): every byte goes
through :meth:`~arcilogger_worker.usage.engine.DayBuilder.add_ip` over the snapshot's minutes
(``DayBuilder(..., window=...)``), so the increment rule (an interim log's deltas at its own time,
the final log only the rest), the spreading over minutes, attribution, destinations and the
background rules are Kullanım's. Only the selection of the rows is the job's own:

* **New rows** of ``fg_traffic`` (forward sessions from internal addresses, as the engine reads
  them) are read by ``id``: from the highest id seen :data:`ID_MARGIN` ago, so a batch committed
  late with lower ids than a row already read is still read, and the ``id`` already counted is
  skipped (ids are not committed in order).
* **A session** (``srcip``, ``sessionid``, ``dstip``, ``dstport``) whose newest log is its final
  log is counted once, with all of its logs, and folded into the closed minutes. A session that
  can still log (its newest log an interim one, less than ``usage.session_overlap_seconds`` old)
  keeps its logs, and is counted again at every tick with the ones that came, so its bytes follow
  the engine's rule however its logs are spread over ticks.
* **Warm-up:** after a start the last 60 minutes and the overlap before them are read in slices of
  :data:`WARMUP_SLICE`, one a tick, and the snapshot says ``warming_up`` until they are covered.

**The file** holds subject keys (``person:12``, ``device:<mac>``, ``ip:<addr>``), their VLAN,
MAC and address, and per minute and subject the bytes per destination (a registrable domain, a
service name or an address) and application, and the sessions started: never a name, a user, a
URL or a raw line. It is sparse (only minutes with traffic), covers the last 60 minutes, and is
written atomically, only when it changed. The API applies access, the KVKK notice, pseudonyms and
``usage_excluded`` when it answers.
"""

from __future__ import annotations

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

from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.timeutil import site_day
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.domains import BackgroundRules, DomainNamer
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
#: Rows committed late: each tick reads again from the highest id it had seen this long ago and
#: skips the ``(table, id)`` pairs already counted. Ingest commits a batch within its
#: ``batch_seconds`` (0.5 s by default); this is far more than twice that.
ID_MARGIN: Final = timedelta(seconds=30)
#: The part of the start-up's read one tick takes (about 2 s or less at 2,000 lines/s).
WARMUP_SLICE: Final = timedelta(minutes=2)
#: The most new rows one tick reads; the rest come in the next ticks.
MAX_ROWS: Final = 40_000
SNAPSHOT_VERSION: Final = 1
#: Destinations and applications kept per subject and minute (the answer shows 3 per window).
TOP_PER_MINUTE: Final = 10

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
    f"WHERE id > :after AND time >= :lo AND {_FORWARD} ORDER BY id LIMIT :limit"
)
_SLICE_ROWS = text(
    f"SELECT id, host(srcip), {_TRAFFIC_COLUMNS} FROM fg_traffic "  # noqa: S608 - fixed text
    f"WHERE id <= :upto AND time >= :lo AND time < :hi AND {_FORWARD} ORDER BY time, id"
)
_MAX_ID = text("SELECT coalesce(max(id), 0) FROM fg_traffic")
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
#: Only the answers that can name a session (one of its addresses, before its start): the
#: engine names a session by the newest such answer (``DayBuilder._destination``).
_DNS = text(
    "SELECT id, host(srcip), time, qname, answers, cat, catdesc, action FROM fg_dns "
    "WHERE srcip = ANY(CAST(:ips AS inet[])) AND answers && CAST(:addresses AS inet[]) "
    "AND time >= :lo AND time <= :hi ORDER BY srcip, time, id"
)

Key = tuple[Any, ...]
#: minute (epoch seconds) → subject key → (domain, kind, app, background) → [down, up]
Cells = dict[int, dict[str, dict[tuple[str, str, str | None, bool], list[float]]]]


@dataclass
class OpenSession:
    """The logs so far of a session that can still log, and what they count for."""

    ip: str
    rows: list[tuple[Any, ...]] = field(default_factory=list)
    #: The newest log's time (epoch seconds).
    newest: float = 0.0
    #: The engine's count of ``rows``; ``None`` when logs came (or dropped out) since.
    counted: Counted | None = None


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


class Snapshot:
    """The worker's rolling snapshot: kept in memory by the process, written to ``path``."""

    def __init__(
        self, path: Path, *, minutes: int = MINUTES, warmup_slice: timedelta = WARMUP_SLICE
    ) -> None:
        self.path = path
        #: Minutes kept, and the part of the start-up's read one tick takes (the tests set them).
        self.minutes = minutes
        self.warmup_slice = warmup_slice
        self.closed = Counted()
        self.open: dict[Key, OpenSession] = {}
        #: Ids read in the last :data:`ID_MARGIN`, and the highest id seen at each tick.
        self.seen: set[int] = set()
        self.marks: list[tuple[float, int]] = []
        self.highest = 0
        #: Warm-up: the next slice's start, up to the id seen at start (``None``: done).
        self.warm_from: datetime | None = None
        self.warm_upto = 0
        self.started = False
        self.written: str | None = None

    # -- the tick ---------------------------------------------------------------------------------

    def tick(self, session: Session, now: datetime) -> dict[str, Any]:
        """Read what came since the last tick (or the next warm-up slice), count it, write the
        snapshot when it changed. Returns the job's details."""
        began = time.monotonic()
        settings = UsageSettings.load(session)
        overlap = timedelta(seconds=settings.session_overlap_seconds)
        end = now.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(seconds=BUCKET)
        lo = end - timedelta(minutes=self.minutes)
        refused = list(REFUSED_ACTIONS)
        if not self.started:
            self.started = True
            self.warm_upto = int(session.execute(_MAX_ID).scalar_one())
            self.highest = self.warm_upto
            self.marks = [(time.monotonic(), self.warm_upto)]
            self.warm_from = lo - overlap
        until = end
        if self.warm_from is not None:
            until = min(self.warm_from + self.warmup_slice, end)
            rows = session.execute(
                _SLICE_ROWS,
                {"upto": self.warm_upto, "lo": self.warm_from, "hi": until, "refused": refused},
            ).all()
            self.warm_from = None if until >= end else until
        else:
            after = self._after()
            rows = session.execute(
                _NEW_ROWS,
                {"after": after, "lo": lo - overlap, "limit": MAX_ROWS, "refused": refused},
            ).all()
        fresh = [row for row in rows if row[0] not in self.seen]
        for row in fresh:
            self.seen.add(row[0])
            self.highest = max(self.highest, row[0])
        self._mark()
        details = self._count(session, settings, fresh, lo, end, overlap, until)
        counted = self.current(session, settings, lo, end)
        writing = time.monotonic()
        changed = self._write(counted, now, lo, end)
        details.update(
            {
                "rows": len(fresh),
                "open_sessions": len(self.open),
                "warming_up": self.warm_from is not None,
                "written": changed,
                "write_seconds": round(time.monotonic() - writing, 3),
                "seconds": round(time.monotonic() - began, 3),
            }
        )
        return details

    def _after(self) -> int:
        """The id to read after: the highest one seen :data:`ID_MARGIN` ago."""
        horizon = time.monotonic() - ID_MARGIN.total_seconds()
        earlier = [mark for at, mark in self.marks if at <= horizon]
        return earlier[-1] if earlier else (self.marks[0][1] if self.marks else 0)

    def _mark(self) -> None:
        now = time.monotonic()
        self.marks.append((now, self.highest))
        horizon = now - ID_MARGIN.total_seconds()
        # Keep the newest mark at or before the horizon (the next tick reads after it).
        while len(self.marks) > 1 and self.marks[1][0] <= horizon:
            self.marks.pop(0)
        low = self.marks[0][1]
        self.seen = {row_id for row_id in self.seen if row_id > low}

    # -- counting ---------------------------------------------------------------------------------

    def _count(
        self,
        session: Session,
        settings: UsageSettings,
        rows: Sequence[Sequence[Any]],
        lo: datetime,
        end: datetime,
        overlap: timedelta,
        until: datetime,
    ) -> dict[str, Any]:
        """Sort ``rows`` into the open sessions; count the sessions that ended (their final log,
        or no log for the overlap before ``until``: the end of the rows read) into the closed
        minutes."""
        closed: dict[Key, OpenSession] = {}
        for row in rows:
            ip, values = str(row[1]), tuple(row[2:])
            key: Key = (
                (ip, values[0], values[1], values[2]) if values[0] is not None else (ip, row[0])
            )
            known = self.open.pop(key, None) or OpenSession(ip)
            known.rows.append(values)
            known.newest = max(known.newest, values[3].timestamp())
            known.counted = None
            if values[10] and values[0] is not None:  # an interim log: the session goes on
                self.open[key] = known
            else:
                closed[key] = known
        horizon = until.timestamp() - overlap.total_seconds()
        for key in [key for key, known in self.open.items() if known.newest < horizon]:
            closed[key] = self.open.pop(key)  # no log for the overlap: it is over
        if closed:
            (done,) = self._counts(session, settings, [list(closed.values())], lo, end, overlap)
            _add(self.closed, done)
        self._prune(lo, overlap)
        return {"closed_sessions": len(closed)}

    def _counts(
        self,
        session: Session,
        settings: UsageSettings,
        groups: Sequence[Sequence[OpenSession]],
        lo: datetime,
        end: datetime,
        overlap: timedelta,
    ) -> list[Counted]:
        """The engine's count of each group of sessions over the minutes ``[lo, end)``: one
        builder and one read of the web filter, application and DNS logs for all of them."""
        results = [Counted() for _ in groups]
        everyone = [known for group in groups for known in group]
        if not everyone:
            return results
        namer = DomainNamer(settings.psl_file, settings.psl_private_suffixes)
        rules = BackgroundRules.create(
            settings.background_domains, settings.background_appcats, settings.background_ports
        )
        builder = DayBuilder(
            site_day(end - timedelta(seconds=BUCKET)), settings, namer, rules, window=(lo, end)
        )
        ips = sorted({known.ip for known in everyone})
        timelines = build_timelines(session, ips, lo, end)
        rows = [row for known in everyone for row in known.rows]
        sessionids = sorted({row[0] for row in rows if row[0] is not None})
        addresses = sorted({str(row[1]) for row in rows if row[1] is not None})
        starts = [row[3].timestamp() - (row[4] or 0) for row in rows]
        since = lo - overlap
        web = _by_session(session.execute(_WEB, {"ips": ips, "sessions": sessionids, "lo": since}))
        app = _by_session(session.execute(_APP, {"ips": ips, "sessions": sessionids, "lo": since}))
        dns = _by_answer(
            session.execute(
                _DNS,
                {
                    "ips": ips,
                    "addresses": addresses,
                    "lo": datetime.fromtimestamp(min(starts) - settings.dns_window_seconds, UTC),
                    "hi": datetime.fromtimestamp(max(starts) + DNS_TOLERANCE, UTC),
                },
            ),
            set(addresses),
        )
        for group, counted in zip(groups, results, strict=True):
            by_ip: dict[str, list[tuple[Any, ...]]] = defaultdict(list)
            for known in group:
                by_ip[known.ip].extend(known.rows)
            for ip in sorted(by_ip):
                # The engine's order (``_TRAFFIC``): by session, then time; a row without a
                # session id stands alone, last.
                traffic = sorted(
                    by_ip[ip],
                    key=lambda r: (r[0] is None, r[0] or 0, str(r[1]), r[2] or 0, r[3], r[4] or 0),
                )
                ids = sorted({row[0] for row in traffic if row[0] is not None})
                answers = {
                    found
                    for address in {str(row[1]) for row in traffic if row[1] is not None}
                    for found in dns.get((ip, address), ())
                }
                builder.add_ip(
                    ip,
                    timelines[ip],
                    traffic,
                    [row for sessionid in ids for row in web.get((ip, sessionid), ())],
                    [row for sessionid in ids for row in app.get((ip, sessionid), ())],
                    [row[1:] for row in sorted(answers, key=lambda r: (r[1], r[0]))],
                )
                _started(builder, timelines[ip], traffic, counted)
            _harvest(builder, counted)
        return results

    def _prune(self, lo: datetime, overlap: timedelta) -> None:
        """Drop the minutes before ``lo``, and the logs of open sessions older than the
        engine reads before its range (``lo`` less the overlap)."""
        first = int(lo.timestamp())
        for table in (self.closed.cells, self.closed.sessions):
            for minute in [minute for minute in table if minute < first]:
                del table[minute]
        oldest = lo - overlap
        for known in self.open.values():
            if known.rows[0][3] < oldest:
                known.rows = [row for row in known.rows if row[3] >= oldest]
                known.counted = None

    # -- the file ---------------------------------------------------------------------------------

    def current(
        self, session: Session, settings: UsageSettings, lo: datetime, end: datetime
    ) -> Counted:
        """The closed minutes plus the open sessions counted as they stand: a session is
        counted again only when logs came for it (its destination's name and whether it is
        background follow its logs; it is counted whole once more when it ends)."""
        overlap = timedelta(seconds=settings.session_overlap_seconds)
        stale = [known for known in self.open.values() if known.counted is None]
        counts = self._counts(session, settings, [[known] for known in stale], lo, end, overlap)
        for known, counted in zip(stale, counts, strict=True):
            known.counted = counted
        result = Counted()
        _add(result, self.closed)
        for known in self.open.values():
            if known.counted is not None:
                _add(result, known.counted)
        return result

    def _write(self, counted: Counted, now: datetime, lo: datetime, end: datetime) -> bool:
        """Write the snapshot when its content changed (atomically); whether it was written."""
        first, last = int(lo.timestamp()), int(end.timestamp())
        minutes = []
        used: set[str] = set()
        for minute in sorted(counted.cells.keys() | counted.sessions.keys()):
            if not first <= minute < last:
                continue
            flows = counted.cells.get(minute, {})
            started = counted.sessions.get(minute, {})
            subjects = {}
            for key in sorted(flows.keys() | started.keys()):
                cell = flows.get(key, {})
                down = sum(values[0] for values in cell.values())
                up = sum(values[1] for values in cell.values())
                domains: dict[tuple[str, str], float] = defaultdict(float)
                apps: dict[str, float] = defaultdict(float)
                for (domain, kind, app, background), (received, sent) in cell.items():
                    if not background:
                        domains[(domain, kind)] += received + sent
                    if app:
                        apps[app] += received + sent
                subjects[key] = {
                    "down": down,
                    "up": up,
                    "sessions": started.get(key, 0),
                    "domains": [
                        [domain, kind, volume]
                        for (domain, kind), volume in _top(domains, TOP_PER_MINUTE)
                    ],
                    "apps": [[app, volume] for app, volume in _top(apps, TOP_PER_MINUTE)],
                }
                used.add(key)
            minutes.append({"minute": _iso(minute), "subjects": subjects})
        body = {
            "version": SNAPSHOT_VERSION,
            "from": _iso(first),
            "until": _iso(last),
            "warming_up": self.warm_from is not None,
            "data_until": None if counted.newest is None else _iso(counted.newest),
            "subjects": {
                key: counted.subjects[key] for key in sorted(used) if key in counted.subjects
            },
            "minutes": minutes,
        }
        content = json.dumps(body, sort_keys=True, separators=(",", ":"))
        if content == self.written:
            return False
        document = {**body, "generated_at": now.astimezone(UTC).isoformat()}
        atomic_write_text(self.path, json.dumps(document, sort_keys=True, separators=(",", ":")))
        self.written = content
        return True


#: The snapshot of each data directory, kept by the worker process between its ticks.
_SNAPSHOTS: dict[Path, Snapshot] = {}


def tick_job(sessions: sessionmaker[Session], path: Path, now: datetime) -> dict[str, Any]:
    """The job ``activity.tick``: one tick of the snapshot written to ``path``."""
    snapshot = _SNAPSHOTS.setdefault(path, Snapshot(path))
    with sessions() as session:
        return snapshot.tick(session, now)


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


def _by_answer(
    rows: Iterable[Sequence[Any]], addresses: set[str]
) -> dict[tuple[str, str], list[tuple[Any, ...]]]:
    """DNS rows ``(id, time, qname, answers, …)`` by (address, answered address)."""
    found: dict[tuple[str, str], list[tuple[Any, ...]]] = defaultdict(list)
    for row in rows:
        values = (row[0], row[2], row[3], tuple(row[4] or ()), *row[5:])
        for answer in row[4] or ():
            if str(answer) in addresses:
                found[(str(row[1]), str(answer))].append(values)
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
        for index, cell in buckets.items():
            minute = int(builder.lo_ts + index * BUCKET)
            target = counted.cells.setdefault(minute, {}).setdefault(key, {})
            for flow_id, (_, received, sent) in cell.items():
                flow = builder.flow_list[flow_id]
                name = (flow.domain, flow.kind, flow.app, flow.background)
                entry = target.setdefault(name, [0.0, 0.0])
                entry[0] += received
                entry[1] += sent
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


def _add(total: Counted, other: Counted) -> None:
    for minute, subjects in other.cells.items():
        target_minute = total.cells.setdefault(minute, {})
        for key, flows in subjects.items():
            target = target_minute.setdefault(key, {})
            for name, (down, up) in flows.items():
                entry = target.setdefault(name, [0.0, 0.0])
                entry[0] += down
                entry[1] += up
    for minute, started in other.sessions.items():
        target_sessions = total.sessions.setdefault(minute, {})
        for key, count in started.items():
            target_sessions[key] = target_sessions.get(key, 0) + count
    total.subjects.update(other.subjects)
    if other.newest is not None and (total.newest is None or other.newest > total.newest):
        total.newest = other.newest
