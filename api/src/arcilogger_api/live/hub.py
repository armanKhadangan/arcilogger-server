"""The live tail's reader: one per API process (contract 2.14.3, decisions 8, R9-R13).

Canlı Akış reads the **raw spool**, not the database: the collector writes every line there
first, so the live tail works while ingest lags or is stopped, shows a new ``unknown-<ip>``
folder the moment the collector creates it and gives every line its exact raw reference. The
database is needed only for sign-in, the 30 s session check and the optional line-number anchor
(:func:`arcilogger_api.rawtail.line_anchor`).

**One hub** (:class:`Hub`, kept on ``app.state``, created by the first stream) serves every
stream of the process with one asyncio task:

* every :attr:`Timing.poll` (0.5 s) it reads what was appended to today's file of every folder
  some stream wants — at most 1 MiB a file a poll; a file more than 8 MiB ahead is skipped to
  its end with the lines counted exactly (``dropped {behind}``); every :attr:`Timing.listing`
  (5 s) it lists ``spool/`` (valid folder names only, never ``_audit``), so network members
  (``<name>-<ip>``) and new unknown senders join at once (R10);
* after midnight (site time) it reads yesterday's file to its end, then follows today's; the
  sealer may delete yesterday's file at any time, which ends that file cleanly. A file that
  shrank or was replaced is announced as ``gap {file_replaced}``;
* when a stream needs a file the hub did not follow yet, it seeds its buffer with the lines of
  the file's last 256 KiB (they serve as the stream's ``backfill``), parsed newest first;
* lines are parsed with ingest's own ``prepare_line`` (the rows pass ingest's row checks) within
  a hub-wide budget of ``live.parse_lines_per_second``; a line beyond it is kept unparsed and
  goes out with ``parse_skipped: true`` to streams that filter on the folder and the sender
  only, while streams that need parsed fields count it as ``dropped {parse_budget}`` (R9). When
  every open stream has free text, a line that contains none of a stream's terms (ASCII case
  folded, a byte search) is not parsed at all: no open stream could match it;
* the newest ``live.buffer_lines`` lines stay in one buffer (R11) with a sequence number; its
  ``epoch`` is chosen when the hub starts. A stream that pauses (its connection closes) lingers
  120 s and keeps its slot; a resume replays the matching lines it missed from the buffer, or
  announces a ``gap``.

All file reading and parsing runs in a worker thread, one cycle at a time, and only that thread
touches the followed files; everything else (streams, buffer, dispatch) lives on the event loop.
Every read opens the file with ``open_shared`` and closes it before the cycle ends — the hub
never writes anything and never keeps a file open between polls.

**Per stream** (:class:`Stream`): the folders its ``source:`` clauses allow, the matcher of its
query (:func:`arcilogger_api.search.compile_matcher`, the semantics of the search SQL), a token
bucket of ``live.max_lines_per_second`` (excess: ``rate_limited``) and a queue of
:data:`QUEUE_LINES` lines (full: ``slow_client``; full for 30 s: the stream ends). A stream ends
with ``time_limit`` after ``live.max_minutes``. Closing — ``DELETE``, an ``end``, lingering out
or the server stopping — writes ``live.close`` (the delivered lines' ranges, counts by reason).
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import secrets
import time
from collections import deque
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.deps import AppState
from arcilogger_api.live.events import RAW_KEEP_BYTES, Entry, frame
from arcilogger_api.live.messages import GAP_MESSAGE, dropped_texts, end_texts
from arcilogger_api.rawlines import RawLine, regular_file, valid_source_name
from arcilogger_api.rawtail import POLL_MAX_BYTES, Follower, line_anchor, read_tail
from arcilogger_api.search.fields import SiteNetwork
from arcilogger_api.search.matcher import Matcher, Record
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, to_site, utc_now
from arcilogger_db.kinds import kind_of
from arcilogger_db.settings import get_settings
from arcilogger_ingest.prepare import PATTERN_RAW, StreamInfo, prepare_line
from arcilogger_ingest.sources import resolve_source

log = logging.getLogger("arcilogger.api.live")

#: A file further ahead than this is skipped to its end (its lines counted, ``behind``).
BEHIND_BYTES: Final = 8 * 1024 * 1024
#: The end of a newly followed file read into the buffer (the new stream's backfill).
SEED_BYTES: Final = 256 * 1024
#: Lines waiting for one stream's connection; more are dropped as ``slow_client``.
QUEUE_LINES: Final = 500
#: Reasons a line is counted instead of shown (``dropped`` event, ``live.close``).
DROP_REASONS: Final = ("rate_limited", "slow_client", "parse_budget", "behind")
#: Seconds of hub statistics behind ``lines_per_second`` / ``parsed_per_second``.
RATE_WINDOW: Final = 10.0

Verdict = Literal["match", "no", "parse_budget"]


@dataclass(frozen=True)
class Timing:
    """The hub's intervals in seconds (tests shorten them)."""

    poll: float = 0.5
    listing: float = 5.0
    #: The hub task stops this long after the last stream ended.
    idle_stop: float = 30.0
    #: A paused (disconnected) stream keeps its slot this long.
    linger: float = 120.0
    #: A stream whose queue stays full this long ends with ``slow_client``.
    slow_client: float = 30.0
    keepalive: float = 15.0
    session_check: float = 30.0
    #: ``dropped`` events at most this often.
    dropped: float = 1.0


@dataclass(frozen=True)
class LiveSettings:
    """The ``live.*`` settings (contract 2.19), read when a stream opens."""

    max_streams_total: int = 4
    max_streams_per_user: int = 2
    max_lines_per_second: int = 100
    parse_lines_per_second: int = 1000
    buffer_lines: int = 5000
    max_minutes: int = 60
    backfill_lines: int = 50

    @classmethod
    def load(cls, db: Session) -> LiveSettings:
        values = get_settings(db, prefix="live.")
        return cls(
            **{item.name: int(values[f"live.{item.name}"]) for item in dataclasses.fields(cls)}
        )


class TokenBucket:
    """``rate`` tokens a second, at most one second's worth saved up."""

    def __init__(self, rate: float, clock: Callable[[], float]) -> None:
        self.rate = rate
        self.clock = clock
        self.tokens = rate
        self.at = clock()

    def set_rate(self, rate: float) -> None:
        self.rate = rate
        self.tokens = min(self.tokens, rate)

    def take(self) -> bool:
        now = self.clock()
        self.tokens = min(self.rate, self.tokens + (now - self.at) * self.rate)
        self.at = now
        if self.tokens >= 1:
            self.tokens -= 1
            return True
        return False


class LiveUnavailableError(OSError):
    """``spool/`` cannot be listed: no stream can start."""


class StreamLimitError(Exception):
    """``live.max_streams_total`` or ``live.max_streams_per_user`` streams are open already."""


class Connection:
    """One ``GET /live/streams/{id}``: the lines queued for it and its control frames."""

    def __init__(self) -> None:
        #: Lines to send; ``None`` only wakes the writer (a control frame or a stop waits).
        self.queue: asyncio.Queue[Entry | None] = asyncio.Queue(maxsize=QUEUE_LINES)
        self.controls: deque[bytes] = deque()
        #: Since when the queue has been full (``slow_client``).
        self.full_since: float | None = None
        #: End the response after the control frames (an ``end`` was queued).
        self.ending = False
        #: End the response now, without an event (closed, or another connection took over).
        self.stopped = False

    def wake(self) -> None:
        # A full queue: the writer is busy and looks at the control frames before the next line.
        with contextlib.suppress(asyncio.QueueFull):
            self.queue.put_nowait(None)

    def control(self, data: bytes, *, ending: bool = False) -> None:
        self.controls.append(data)
        self.ending = self.ending or ending
        self.wake()

    def stop(self) -> None:
        self.stopped = True
        self.wake()


def _usable_term(term: str) -> bytes | None:
    """A free-text term as the byte search sees it (``None``: the spool escapes it, so the raw
    bytes cannot be searched for it)."""
    data = term.encode("utf-8").lower()
    if b"#" in data or any(byte < 0x20 or byte == 0x7F for byte in data):
        return None
    return data


@dataclass(eq=False)
class Stream:
    """One opened live tail (``POST /live/streams``), connected or paused."""

    id: str
    user_id: int
    username: str
    #: The session the stream belongs to (``user_sessions.token_hash``).
    token_hash: str
    ip: str | None
    #: The normalised query (``live.open``).
    query: str
    matcher: Matcher
    backfill: int
    settings: LiveSettings
    notice_version: str
    opened_at: datetime
    opened_clock: float
    clock: Callable[[], float]
    folders: frozenset[str] = frozenset()
    #: ``folder → (day, start_line)`` when the stream opened.
    start: dict[str, tuple[date, int]] = field(default_factory=dict)
    #: The hub's sequence number when the stream opened (``None`` while it is opening).
    open_seq: int | None = None
    #: The oldest sequence number the stream may be sent: its oldest backfill line, else the
    #: first line after it opened (set on its first connection; a resume never goes further back).
    first_seq: int = 0
    connection: Connection | None = None
    connected_once: bool = False
    #: When the connection went away (lingering), and the hub's sequence number then.
    detached_clock: float | None = None
    detach_seq: int = 0
    #: Sequence numbers that matched while paused (replayed on resume while still buffered).
    backlog: deque[int] = field(default_factory=deque)
    backlog_lost: int = 0
    #: A ``gap {file_replaced}`` to send when the paused stream resumes.
    pending_gap: bool = False
    delivered: int = 0
    #: Counts by reason since the last ``dropped`` event, and since the stream opened.
    pending: dict[str, int] = field(default_factory=dict)
    pending_since: datetime | None = None
    dropped: dict[str, int] = field(default_factory=dict)
    #: ``(folder, day) → [first, last]`` line numbers delivered (``live.close``).
    ranges: dict[tuple[str, date], list[int]] = field(default_factory=dict)
    #: Why the stream ended (``None`` while it is open).
    closed: str | None = None
    #: ``live.close`` is written (or being written).
    audited: bool = False
    bucket: TokenBucket = field(init=False)
    #: The free-text terms the byte pre-filter may search for (``None``: it may not).
    terms: tuple[bytes, ...] | None = field(init=False)

    def __post_init__(self) -> None:
        self.bucket = TokenBucket(self.settings.max_lines_per_second, self.clock)
        terms = [_usable_term(term) for term in self.matcher.terms]
        self.terms = tuple(t for t in terms if t is not None) if terms and all(terms) else None
        self.detached_clock = self.opened_clock

    @property
    def expires_at(self) -> datetime:
        return self.opened_at + timedelta(minutes=self.settings.max_minutes)

    @property
    def needs_parse(self) -> bool:
        return self.matcher.needs_parse

    def judge(self, entry: Entry) -> Verdict:
        """Whether ``entry`` matches the stream's query (``parse_budget``: cannot tell)."""
        if entry.folder not in self.folders:
            return "no"
        if entry.state == "parsed" and entry.record is not None:
            return "match" if self.matcher(entry.record) else "no"
        if self.needs_parse:
            if entry.state != "skipped":
                return "no"  # not in spool format or no ``messages`` row: like Arama
            if self.terms and not all(term in entry.folded for term in self.terms):
                return "no"
            return "parse_budget"
        unparsed = Record({"source": entry.folder, "sender": entry.sender})
        return "match" if self.matcher(unparsed) else "no"

    def count(self, reason: str, n: int = 1, *, at: datetime) -> None:
        if n <= 0:
            return
        if not self.pending:
            self.pending_since = at
        self.pending[reason] = self.pending.get(reason, 0) + n
        self.dropped[reason] = self.dropped.get(reason, 0) + n

    def take_dropped(self) -> dict[str, Any] | None:
        """The ``dropped`` event data of the counts since the last one (``None``: none)."""
        if not self.pending:
            return None
        counts = {reason: self.pending.get(reason, 0) for reason in DROP_REASONS}
        message_tr, message_en = dropped_texts(counts, self.settings.max_lines_per_second)
        since = self.pending_since or utc_now()
        self.pending, self.pending_since = {}, None
        return {
            **counts,
            "since": to_site(since).isoformat(),
            "message_tr": message_tr,
            "message_en": message_en,
        }

    def sent(self, entry: Entry) -> None:
        """``entry`` was written to the connection."""
        self.delivered += 1
        span = self.ranges.get((entry.folder, entry.day))
        if span is None:
            self.ranges[(entry.folder, entry.day)] = [entry.line, entry.line]
        else:
            span[0], span[1] = min(span[0], entry.line), max(span[1], entry.line)

    def close_details(self, reason: str, now: datetime) -> dict[str, Any]:
        """The ``live.close`` audit details (counts and line ranges, never content)."""
        return {
            "stream_id": self.id,
            "seconds": max(0, int((now - self.opened_at).total_seconds())),
            "delivered": self.delivered,
            "dropped": {reason_: self.dropped.get(reason_, 0) for reason_ in DROP_REASONS},
            "reason": reason,
            "ranges": [
                {"folder": folder, "day": day.isoformat(), "from_line": span[0], "to_line": span[1]}
                for (folder, day), span in sorted(self.ranges.items())
            ],
        }


@dataclass(frozen=True)
class ActorRef:
    """The stream's user as the audit writer's actor."""

    id: int
    username: str


@dataclass
class FileState:
    """A followed folder: today's file (``None`` until it exists) and, after midnight,
    yesterday's until read to its end. Only the hub's read thread touches it."""

    folder: str
    day: date
    follower: Follower | None = None
    previous: Follower | None = None


@dataclass
class Plan:
    """What one read cycle does (built on the event loop, carried out in the thread)."""

    today: date
    list_spool: bool
    #: Every open stream (``True``: opening in this cycle) — only its matcher is read.
    streams: list[tuple[Stream, bool]]
    buffer_lines: int
    #: The byte pre-filter's term sets, one per stream (``None``: no pre-filter).
    terms: tuple[tuple[bytes, ...], ...] | None


@dataclass
class ReadResult:
    listing: frozenset[str] | None = None
    #: ``listing`` failed and no previous listing exists (``live_unavailable``).
    error: OSError | None = None
    folders: dict[Stream, frozenset[str]] = field(default_factory=dict)
    seeded: list[Entry] = field(default_factory=list)
    entries: list[Entry] = field(default_factory=list)
    behind: dict[str, int] = field(default_factory=dict)
    gaps: list[str] = field(default_factory=list)
    starts: dict[str, tuple[date, int]] = field(default_factory=dict)
    parsed: int = 0


def list_spool(layout: Layout) -> frozenset[str]:
    """The folder names of ``spool/`` that can be raw-log sources (``_audit`` is not one).

    A missing ``spool/`` (no line received yet) is an empty set; any other error is raised.
    """
    try:
        children = list(layout.spool.iterdir())
    except FileNotFoundError:
        return frozenset()
    return frozenset(
        child.name for child in children if valid_source_name(child.name) and child.is_dir()
    )


def _light_parse(raw: bytes) -> tuple[datetime | None, str | None]:
    """Receive time and sender of a spool line without unescaping its message."""
    parts = raw.split(b" ", 2)
    if len(parts) != 3:
        return None, None
    try:
        received = datetime.fromisoformat(parts[0].decode("ascii"))
        sender = parts[1].decode("ascii")
    except (UnicodeDecodeError, ValueError):
        return None, None
    return (received, sender) if received.tzinfo is not None else (None, None)


def make_entry(info: StreamInfo, line: RawLine, *, parse: bool, seeded: bool = False) -> Entry:
    """A buffer entry of ``line`` (parsed with ``prepare_line`` when ``parse``)."""
    raw = line.data
    entry = Entry(
        seq=0,
        folder=line.source,
        day=line.day,
        line=line.line,
        offset=line.offset,
        raw=raw[:RAW_KEEP_BYTES],
        cut=len(raw) > RAW_KEEP_BYTES,
        received=None,
        sender=None,
        state="skipped",
        seeded=seeded,
    )
    if not parse:
        entry.received, entry.sender = _light_parse(raw)
        return entry
    prepared = prepare_line(info, line.line, line.offset, raw)
    if any(failure.pattern == PATTERN_RAW for failure in prepared.failures):
        entry.state, entry.parse_error = "invalid", PATTERN_RAW
        return entry
    entry.state = "parsed"
    entry.received, entry.sender = prepared.received, prepared.sender
    rows = prepared.rows
    if rows and rows[0][0] == "messages":
        message = rows[0][1]
        table, row = rows[1] if len(rows) > 1 else (None, None)
        entry.record = Record(message, table, row)
        entry.kind = kind_of(str(message.get("type")), message.get("subtype"))
        entry.parse_error = message.get("parse_error")
    if entry.parse_error is None and prepared.failures:
        entry.parse_error = prepared.failures[0].pattern
    return entry


class Hub:
    """The process's live-tail reader (see the module documentation)."""

    def __init__(
        self,
        state: AppState,
        *,
        timing: Timing | None = None,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = utc_now,
    ) -> None:
        self.state = state
        self.timing = timing or Timing()
        self.clock = clock
        self.now = now
        self.epoch = secrets.token_urlsafe(9)
        self.settings = LiveSettings()
        self.site = SiteNetwork()
        self.streams: dict[str, Stream] = {}
        self.buffer: deque[Entry] = deque(maxlen=self.settings.buffer_lines)
        self.seq = 0
        self.listing: frozenset[str] = frozenset()
        self.skipped_total = 0
        self._listed_at: float | None = None
        self._files: dict[str, FileState] = {}
        self._parse = TokenBucket(self.settings.parse_lines_per_second, clock)
        self._opens: list[tuple[Stream, asyncio.Future[list[dict[str, Any]]]]] = []
        self._task: asyncio.Task[None] | None = None
        self._wake: asyncio.Event | None = None
        self._rates: deque[tuple[float, int, int]] = deque()
        self._background: set[asyncio.Task[None]] = set()
        self._warned: set[str] = set()

    # -- streams ----------------------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def count_streams(self, user_id: int) -> tuple[int, int]:
        """Open streams in all and of ``user_id`` (paused ones keep their slot)."""
        open_ = [stream for stream in self.streams.values() if stream.closed is None]
        return len(open_), sum(1 for stream in open_ if stream.user_id == user_id)

    def get(self, stream_id: str) -> Stream | None:
        stream = self.streams.get(stream_id)
        return stream if stream is not None and stream.closed is None else None

    async def open(
        self, stream: Stream, settings: LiveSettings, site: SiteNetwork
    ) -> list[dict[str, Any]]:
        """Register ``stream``; returns its folders once the hub follows them.

        Raises :class:`StreamLimitError` when a limit is reached (checked and registered with no
        ``await`` between, so concurrent opens cannot overshoot it) and
        :class:`LiveUnavailableError` when ``spool/`` cannot be listed.
        """
        total, mine = self.count_streams(stream.user_id)
        if total >= settings.max_streams_total or mine >= settings.max_streams_per_user:
            raise StreamLimitError
        self.settings, self.site = settings, site
        if self.buffer.maxlen != settings.buffer_lines:
            self.buffer = deque(self.buffer, maxlen=settings.buffer_lines)
        self._parse.set_rate(settings.parse_lines_per_second)
        self.streams[stream.id] = stream
        future: asyncio.Future[list[dict[str, Any]]] = asyncio.get_running_loop().create_future()
        self._opens.append((stream, future))
        self._start()
        try:
            return await future
        except BaseException:
            if stream.open_seq is None or not future.done():
                self.streams.pop(stream.id, None)
            raise

    def folders_of(self, stream: Stream) -> list[dict[str, Any]]:
        return [
            {"folder": folder, "day": day, "start_line": line}
            for folder, (day, line) in sorted(stream.start.items())
        ]

    def attach(
        self, stream: Stream, connection: Connection, last: tuple[str, int] | None
    ) -> list[Entry | bytes]:
        """Connect ``connection`` to ``stream``: the lines and frames to send before the live
        ones — the backfill on the first connection, else what it missed while paused
        (``last``: the ``Last-Event-ID`` epoch and sequence number). A resume never replays a
        line older than :attr:`Stream.first_seq`: the backfill bound holds, and only the stream's
        own lines are counted."""
        previous = stream.connection
        if previous is not None and previous is not connection:
            previous.stop()  # another tab or a reconnect took over
            self._detached(stream)
        items: list[Entry | bytes] = []
        if stream.pending_gap:
            items.append(self._gap("file_replaced", None))
            stream.pending_gap = False
        oldest = self.buffer[0].seq if self.buffer else self.seq + 1
        open_seq = stream.open_seq or 0
        if not stream.connected_once:
            # No ``Last-Event-ID`` can name a line of this stream yet: nothing was sent.
            backfill = self._backfill(stream, open_seq)
            items.extend(backfill)
            stream.first_seq = backfill[0].seq if backfill else open_seq + 1
            after = open_seq
        elif last is None:
            after = stream.detach_seq
        elif last[0] != self.epoch:
            items.append(self._gap("hub_restarted", None))
            after = self.seq
            stream.backlog.clear()
        else:
            after = max(min(last[1], self.seq), stream.first_seq - 1)
        backlog = set(stream.backlog)
        lost_unknown = after < oldest - 1 and after < stream.detach_seq
        missed = stream.backlog_lost + sum(1 for seq in backlog if after < seq < oldest)
        replay = [
            entry
            for entry in self.buffer
            if entry.seq > after
            and (
                entry.seq in backlog
                if entry.seq > stream.detach_seq
                else stream.judge(entry) == "match"
            )
        ]
        if lost_unknown or missed:
            items.append(self._gap("resume_too_late", None if lost_unknown else missed))
        now = self.now()
        for entry in replay:
            if stream.bucket.take():
                items.append(entry)
            else:
                stream.count("rate_limited", at=now)
        stream.connection = connection
        stream.connected_once = True
        stream.detached_clock = None
        stream.backlog.clear()
        stream.backlog_lost = 0
        return items

    def _backfill(self, stream: Stream, open_seq: int) -> list[Entry]:
        """The newest ``stream.backfill`` lines from before the stream opened that match it,
        oldest first. Lines the parse budget left unparsed that could be among them are counted
        as ``parse_budget`` (R9: nothing is hidden silently)."""
        found: list[Entry] = []
        unknown = 0
        for entry in reversed(self.buffer):
            if len(found) >= stream.backfill:
                break
            if entry.seq > open_seq:
                continue
            verdict = stream.judge(entry)
            if verdict == "match":
                found.append(entry)
            elif verdict == "parse_budget":
                unknown += 1
        stream.count("parse_budget", unknown, at=self.now())
        return found[::-1]

    def detach(self, stream: Stream, connection: Connection) -> None:
        """``connection`` ended (paused, gone, or the stream ended)."""
        if stream.connection is connection:
            self._detached(stream)
        if stream.closed is not None:
            self._finish(stream)

    def _detached(self, stream: Stream) -> None:
        stream.connection = None
        stream.detached_clock = self.clock()
        stream.detach_seq = self.seq

    def end(self, stream: Stream, code: str, **params: Any) -> None:
        """End ``stream`` with an ``end`` event (sent when it is connected)."""
        if stream.closed is not None:
            return
        stream.closed = code
        connection = stream.connection
        if connection is None:
            self._finish(stream)
            return
        message_tr, message_en = end_texts(code, **params)
        data = {"code": code, "message_tr": message_tr, "message_en": message_en}
        connection.control(frame("end", data), ending=True)

    async def close(self, stream: Stream, reason: str) -> None:
        """Close ``stream`` without an event (``DELETE``) and audit it before returning."""
        if stream.closed is not None:
            return
        stream.closed = reason
        self.streams.pop(stream.id, None)
        if stream.connection is not None:
            stream.connection.stop()
        stream.audited = True
        await asyncio.to_thread(self._audit_close, stream, reason)

    def _finish(self, stream: Stream) -> None:
        """Forget the ended ``stream`` and write ``live.close`` in the background."""
        self.streams.pop(stream.id, None)
        if stream.audited:
            return
        stream.audited = True
        reason = stream.closed or "closed"
        task = asyncio.get_running_loop().create_task(
            asyncio.to_thread(self._audit_close, stream, reason)
        )
        self._background.add(task)
        task.add_done_callback(self._background.discard)

    def _audit_close(self, stream: Stream, reason: str) -> None:
        """Write ``live.close`` in a short session of its own (never raises)."""
        try:
            with self.state.session_factory()() as session:
                record_audit(
                    session,
                    actor=ActorRef(stream.user_id, stream.username),
                    action="live.close",
                    target=f"live:{stream.id}",
                    details=stream.close_details(reason, self.now()),
                    ip=stream.ip,
                )
                session.commit()
        except (SQLAlchemyError, OSError, RuntimeError, ValueError):
            log.exception("live tail %s: live.close not recorded", stream.id)

    def _gap(self, reason: str, missed: int | None) -> bytes:
        message_tr, message_en = GAP_MESSAGE
        return frame(
            "gap",
            {
                "reason": reason,
                "missed": missed,
                "message_tr": message_tr,
                "message_en": message_en,
            },
        )

    def status(self) -> dict[str, Any]:
        now = self.clock()
        while self._rates and self._rates[0][0] < now - RATE_WINDOW:
            self._rates.popleft()
        lines = sum(n for _, n, _ in self._rates)
        parsed = sum(p for _, _, p in self._rates)
        return {
            "streams": sum(1 for stream in self.streams.values() if stream.closed is None),
            "max_streams": self.settings.max_streams_total,
            "hub": {
                "running": self.running,
                "files": len(self._files),
                "lines_per_second": round(lines / RATE_WINDOW, 1),
                "parsed_per_second": round(parsed / RATE_WINDOW, 1),
                "skipped_total": self.skipped_total,
            },
        }

    # -- the task ---------------------------------------------------------------------------------

    def _start(self) -> None:
        if self._wake is None:
            self._wake = asyncio.Event()
        self._wake.set()
        if not self.running:
            self._task = asyncio.get_running_loop().create_task(self._run(), name="live-hub")

    async def stop(self) -> None:
        """Stop the task (tests; the server stopping cancels it)."""
        task = self._task
        if task is not None:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
        for background in list(self._background):
            await background

    async def _run(self) -> None:
        wake = self._wake = self._wake or asyncio.Event()
        idle_since: float | None = None
        try:
            while True:
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(wake.wait(), self.timing.poll)
                wake.clear()
                await self._cycle()
                if self.streams or self._opens:
                    idle_since = None
                elif idle_since is None:
                    idle_since = self.clock()
                elif self.clock() - idle_since >= self.timing.idle_stop:
                    self._files.clear()
                    self.buffer.clear()
                    return
        except asyncio.CancelledError:
            self._shutdown()
            raise
        except Exception:  # pragma: no cover - a bug: never leave a stream waiting for ever
            log.exception("live tail hub stopped")
            for stream in list(self.streams.values()):
                self.end(stream, "server_stopping")
            raise

    def _shutdown(self) -> None:
        """The server stops: every stream ends (``server_stopping``, best effort)."""
        for stream in list(self.streams.values()):
            if stream.closed is None:
                self.end(stream, "server_stopping")
            self.streams.pop(stream.id, None)
            if not stream.audited:
                stream.audited = True
                self._audit_close(stream, stream.closed or "server_stopping")
        for _, future in self._opens:
            if not future.done():
                future.cancel()
        self._opens.clear()

    async def _cycle(self) -> None:
        opens, self._opens = self._opens, []
        now = self.clock()
        due = self._listed_at is None or now - self._listed_at >= self.timing.listing
        active = [s for s in self.streams.values() if s.closed is None and s.open_seq is not None]
        everyone = active + [stream for stream, _ in opens]
        plan = Plan(
            today=site_day(self.now()),
            list_spool=bool(opens) or due,
            streams=[(stream, False) for stream in active]
            + [(stream, True) for stream, _ in opens],
            buffer_lines=self.settings.buffer_lines,
            terms=self._prefilter(everyone),
        )
        try:
            result = await asyncio.to_thread(self._read, plan)
        except Exception as exc:  # pragma: no cover - a bug in reading: the opens must not hang
            log.exception("live tail: read cycle failed")
            for _, future in opens:
                if not future.done():
                    future.set_exception(LiveUnavailableError(str(exc)))
            return
        if plan.list_spool:
            self._listed_at = now
        if result.listing is not None:
            self.listing = result.listing
        for stream, folders in result.folders.items():
            if stream in self.streams.values():
                stream.folders = folders
        for entry in result.seeded:
            self._append(entry)
        for stream, future in opens:
            if future.done():  # the request went away while waiting
                self.streams.pop(stream.id, None)
            elif result.error is not None:
                self.streams.pop(stream.id, None)
                future.set_exception(LiveUnavailableError(str(result.error)))
            else:
                stream.open_seq = stream.detach_seq = self.seq
                stream.start = {folder: result.starts[folder] for folder in stream.folders}
                future.set_result(self.folders_of(stream))
        at = self.now()
        for folder, skipped in result.behind.items():
            for stream in self._wanting(folder):
                stream.count("behind", skipped, at=at)
        for folder in result.gaps:
            for stream in self._wanting(folder):
                if stream.connection is not None:
                    stream.connection.control(self._gap("file_replaced", None))
                else:
                    stream.pending_gap = True
        for entry in result.entries:
            self._append(entry)
            self._dispatch(entry)
        self._rates.append((now, len(result.entries), result.parsed))
        self._housekeeping()

    def _prefilter(self, streams: Iterable[Stream]) -> tuple[tuple[bytes, ...], ...] | None:
        sets = [stream.terms for stream in streams]
        if not sets or any(not terms for terms in sets):
            return None
        return tuple(terms for terms in sets if terms)

    def _wanting(self, folder: str) -> list[Stream]:
        return [
            stream
            for stream in self.streams.values()
            if stream.closed is None and stream.open_seq is not None and folder in stream.folders
        ]

    def _append(self, entry: Entry) -> None:
        self.seq += 1
        entry.seq = self.seq
        self.buffer.append(entry)

    def _dispatch(self, entry: Entry) -> None:
        at: datetime | None = None
        for stream in self.streams.values():
            if stream.closed is not None or stream.open_seq is None:
                continue
            if entry.folder not in stream.folders:
                continue
            verdict = stream.judge(entry)
            if verdict == "no":
                continue
            at = at or self.now()
            if verdict == "parse_budget":
                stream.count("parse_budget", at=at)
                continue
            connection = stream.connection
            if connection is None:
                if len(stream.backlog) >= self.settings.buffer_lines:
                    stream.backlog.popleft()
                    stream.backlog_lost += 1
                stream.backlog.append(entry.seq)
                continue
            if not stream.bucket.take():
                stream.count("rate_limited", at=at)
                continue
            try:
                connection.queue.put_nowait(entry)
            except asyncio.QueueFull:
                stream.count("slow_client", at=at)
                if connection.full_since is None:
                    connection.full_since = self.clock()
                continue
            connection.full_since = None

    def _housekeeping(self) -> None:
        now = self.clock()
        wall = self.now()
        for stream in list(self.streams.values()):
            if stream.closed is not None or stream.open_seq is None:
                continue
            if wall >= stream.expires_at:
                self.end(stream, "time_limit", minutes=stream.settings.max_minutes)
                continue
            connection = stream.connection
            if connection is None:
                lingering = stream.detached_clock is not None
                if lingering and now - (stream.detached_clock or now) >= self.timing.linger:
                    stream.closed = "lingered"
                    self._finish(stream)
            elif (
                connection.full_since is not None
                and now - connection.full_since >= self.timing.slow_client
            ):
                self.end(stream, "slow_client")

    # -- the read thread --------------------------------------------------------------------------

    def _read(self, plan: Plan) -> ReadResult:
        """List, attach, follow and parse (runs in a worker thread, one cycle at a time)."""
        result = ReadResult()
        layout = self.state.layout
        config = self.state.require_config()
        listing = self.listing
        if plan.list_spool:
            try:
                listing = result.listing = list_spool(layout)
            except OSError as exc:
                log.error("live tail: spool folder cannot be listed: %s", exc)
                result.error = exc
                return result
        wanted: set[str] = set()
        seeds: set[str] = set()
        for stream, new in plan.streams:
            folders = frozenset(f for f in listing if stream.matcher.wants_source(f))
            result.folders[stream] = folders
            wanted |= folders
            if new:
                seeds |= {f for f in folders if f not in self._files}
        for folder in set(self._files) - wanted:
            del self._files[folder]
        anchors = self._anchors(layout, plan.today, seeds)
        news = {folder for stream, new in plan.streams if new for folder in result.folders[stream]}
        for folder in sorted(wanted):
            state = self._files.get(folder)
            if folder in news and state is not None:  # followed already: new from here on
                line = state.follower.line if state.follower is not None else 0
                result.starts[folder] = (plan.today, line)
            try:
                self._follow(result, layout, config, plan, folder, folder in seeds, anchors)
            except OSError as exc:
                if folder not in self._warned:
                    self._warned.add(folder)
                    log.warning("live tail: %s cannot be read: %s", folder, exc)
            if folder in news:
                result.starts.setdefault(folder, (plan.today, 0))
        return result

    def _anchors(self, layout: Layout, day: date, folders: set[str]) -> dict[str, tuple[int, int]]:
        """Line-number anchors of the files to seed (one short session; none without one)."""
        if not folders:
            return {}
        try:
            with self.state.session_factory()() as session:
                return {folder: line_anchor(session, layout, folder, day) for folder in folders}
        except (SQLAlchemyError, OSError) as exc:
            log.warning("live tail: no line anchor from the database: %s", exc)
            return {folder: line_anchor(None, layout, folder, day) for folder in folders}

    def _follow(
        self,
        result: ReadResult,
        layout: Layout,
        config: Config,
        plan: Plan,
        folder: str,
        seed: bool,
        anchors: Mapping[str, tuple[int, int]],
    ) -> None:
        today = plan.today
        state = self._files.get(folder)
        info = StreamInfo(folder, resolve_source(config, folder).configured_type, today)
        if state is None:
            state = self._files[folder] = FileState(folder, today)
            path = layout.spool_file(folder, today)
            if seed:
                try:
                    tail = read_tail(
                        layout,
                        folder,
                        today,
                        plan.buffer_lines,
                        anchors.get(folder, (0, 0)),
                        max_bytes=SEED_BYTES,
                    )
                except FileNotFoundError:
                    tail = None
                if tail is not None:
                    state.follower = Follower(
                        path, tail.end_offset, tail.end_line, identity=tail.identity
                    )
                    result.starts[folder] = (today, tail.end_line)
                    # Newest first: the parse budget goes to the lines a backfill shows.
                    seeded = [
                        self._entry(result, info, line, plan, seeded=True)
                        for line in reversed(tail.lines)
                    ]
                    result.seeded.extend(reversed(seeded))
                else:
                    result.starts[folder] = (today, 0)
            elif regular_file(path):
                state.follower = Follower(path, 0, 0)  # a folder that just appeared: all new
        if state.day != today:  # midnight: read yesterday's file to its end first
            state.previous, state.follower, state.day = state.follower, None, today
        if state.previous is not None:
            previous = state.previous
            lines = previous.poll(POLL_MAX_BYTES, skip_beyond=BEHIND_BYTES)
            self._take(
                result,
                StreamInfo(folder, info.source_type, previous_day(previous)),
                lines,
                plan,
                previous.skipped,
                folder,
            )
            if previous.ended is not None or (not lines and not previous.skipped):
                state.previous = None
        if state.follower is None:
            path = layout.spool_file(folder, today)
            if regular_file(path):
                state.follower = Follower(path, 0, 0)
        follower = state.follower
        if follower is None:
            return
        lines = follower.poll(POLL_MAX_BYTES, skip_beyond=BEHIND_BYTES)
        self._take(result, info, lines, plan, follower.skipped, folder)
        if follower.ended is not None:
            result.gaps.append(folder)
            state.follower = None
            path = layout.spool_file(folder, today)
            if regular_file(path):  # follow the new file from its end
                tail = read_tail(layout, folder, today, 0)
                state.follower = Follower(
                    path, tail.end_offset, tail.end_line, identity=tail.identity
                )

    def _take(
        self,
        result: ReadResult,
        info: StreamInfo,
        lines: list[RawLine],
        plan: Plan,
        skipped: int,
        folder: str,
    ) -> None:
        if skipped:
            result.behind[folder] = result.behind.get(folder, 0) + skipped
        for line in lines:
            result.entries.append(self._entry(result, info, line, plan))

    def _entry(
        self,
        result: ReadResult,
        info: StreamInfo,
        line: RawLine,
        plan: Plan,
        *,
        seeded: bool = False,
    ) -> Entry:
        parse = True
        if plan.terms is not None:
            folded = line.data[:RAW_KEEP_BYTES].lower()
            parse = any(all(term in folded for term in terms) for terms in plan.terms)
        if parse and not self._parse.take():
            parse = False
            self.skipped_total += 1
        entry = make_entry(info, line, parse=parse, seeded=seeded)
        if parse:
            result.parsed += 1
        return entry


def previous_day(follower: Follower) -> date:
    """The day of the file ``follower`` reads (``<day>.log``)."""
    return date.fromisoformat(follower.path.stem)


def hub_of(app: Any) -> Hub:
    """The application's hub (created on first use; no change to ``app.py``)."""
    hub: Hub | None = getattr(app.state, "arci_live", None)
    if hub is None:
        hub = Hub(app.state.arci)
        app.state.arci_live = hub
    return hub
