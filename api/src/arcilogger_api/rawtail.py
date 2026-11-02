"""The end of a spool file, with exact raw references — the reader behind Canlı Akış.

The live tail (:mod:`arcilogger_api.live`) shows the lines the collector appends to
``spool/<folder>/<day>.log`` and gives every line its raw reference ``(folder, day, line,
offset)`` — the one ``GET /raw`` opens and verifies (:mod:`arcilogger_api.rawlines`). A line
number is the count of line feeds before the line, so it needs a known position to count from:

* :func:`line_anchor` — the furthest known ``(offset of a line start, complete lines before
  it)`` of a file: what ingest committed (``ingest_offsets``) while that lies within the file,
  or the nearest checkpoint the raw-line index keeps (:data:`rawlines.SPOOL_INDEX`, through
  :func:`rawlines.spool_anchor`), else ``(0, 0)``. The database is optional: without it (or
  while ingest is stopped) the index or the start of the file serves.
* :func:`tail_lines` / :func:`read_tail` — the last lines of a file, read **backwards** in
  64 KiB blocks (at most 1 MiB) until enough line feeds are found; their numbers come from
  counting on from the anchor (``bytes.count`` on 1 MiB chunks, the caller runs it in a thread)
  or back from an anchor that lies inside the tail. Checkpoints found on the way are kept in
  the index for the next caller.
* :class:`Follower` — the new complete lines of one file, poll after poll.

The rules of ingest and the raw-line view hold here too: every read opens the file with
:func:`arcilogger_ingest.files.open_shared` and closes it before returning (never in the
sealer's way, never a file kept open between polls), nothing is written, and an unterminated
last line (still being written) is not a line yet. A file that disappears (sealed and deleted)
ends a follower cleanly; a file that shrank or was replaced (another inode) ends it with
``file_replaced`` — the lines in between cannot be told apart from new ones.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import BinaryIO, Final, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api import rawlines
from arcilogger_api.rawlines import CHECKPOINT_BYTES, SPOOL_INDEX, RawLine
from arcilogger_core.layout import Layout
from arcilogger_db.models import IngestOffset
from arcilogger_ingest.files import open_shared, read_complete

#: Size of one backwards read of :func:`read_tail`.
TAIL_BLOCK: Final = 64 * 1024
#: :func:`tail_lines` returns only lines that start within this many bytes before the end.
TAIL_MAX_BYTES: Final = 1024 * 1024
#: Bytes one :meth:`Follower.poll` reads at most (a longer single line is read whole).
POLL_MAX_BYTES: Final = 1024 * 1024
#: Chunk size of counting line feeds.
COUNT_CHUNK: Final = rawlines.CHUNK

#: ``(byte offset of a line start, complete lines before it)``.
Anchor = tuple[int, int]
Ended = Literal["gone", "file_replaced"]


def _identity(stat: os.stat_result) -> tuple[int, int]:
    return stat.st_dev, stat.st_ino


def _file_of(path: Path) -> str:
    """``spool/<folder>/<day>.log``: the data-directory relative path of a spool file."""
    return f"spool/{path.parent.name}/{path.name}"


def line_anchor(db: Session | None, layout: Layout, folder: str, day: date) -> Anchor:
    """The furthest known line start of ``spool/<folder>/<day>.log``: ingest's committed resume
    point while it lies within the file, or the raw-line index's nearest checkpoint, else
    ``(0, 0)`` (also for a file that does not exist)."""
    path = layout.spool_file(folder, day)
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (0, 0)
    best = rawlines.spool_anchor(path, _identity(stat), stat.st_size)
    if db is not None:
        row = db.execute(
            select(IngestOffset.byte_offset, IngestOffset.line_number).where(
                IngestOffset.source == folder, IngestOffset.day == day
            )
        ).one_or_none()
        if row is not None and best[0] < int(row.byte_offset) <= stat.st_size:
            best = (int(row.byte_offset), int(row.line_number))
    return best


def _is_line_start(handle: BinaryIO, offset: int, size: int) -> bool:
    if offset == 0:
        return True
    if offset > size:
        return False
    handle.seek(offset - 1)
    return handle.read(1) == b"\n"


def _count_on(
    handle: BinaryIO, path: Path, identity: tuple[int, int], start: Anchor, end: int
) -> tuple[int, int]:
    """Count line feeds from the line start ``start`` up to byte ``end``: ``(complete lines
    before end, offset of the last line start reached)``. Checkpoints about every
    :data:`~arcilogger_api.rawlines.CHECKPOINT_BYTES` go into the raw-line index."""
    position, lines = start
    last_start = last_point = position
    points: list[tuple[int, int]] = []
    handle.seek(position)
    while position < end:
        chunk = handle.read(min(COUNT_CHUNK, end - position))
        if not chunk:
            break
        found = chunk.count(b"\n")
        if found:
            lines += found
            last_start = position + chunk.rfind(b"\n") + 1
            if last_start >= last_point + CHECKPOINT_BYTES:
                points.append((last_start, lines + 1))
                last_point = last_start
        position += len(chunk)
    SPOOL_INDEX.record(path, identity, points)
    return lines, last_start


@dataclass(frozen=True)
class TailRead:
    """What :func:`read_tail` found: the lines and where the next line starts."""

    lines: list[RawLine]
    #: Offset after the last complete line and its line number (= complete lines before it).
    end_offset: int
    end_line: int
    #: ``(st_dev, st_ino)`` of the file read (a :class:`Follower` notices a replaced file).
    identity: tuple[int, int]


def read_tail(
    layout: Layout,
    folder: str,
    day: date,
    n: int,
    anchor: Anchor = (0, 0),
    *,
    max_bytes: int = TAIL_MAX_BYTES,
    block: int = TAIL_BLOCK,
) -> TailRead:
    """The last ``n`` complete lines of ``spool/<folder>/<day>.log`` that start within its last
    ``max_bytes`` bytes (oldest first, exact line numbers) and the position after them.

    ``anchor`` is used when it is a line start within the file; a checkpoint of the raw-line
    index nearer to the lines is used instead. Raises ``FileNotFoundError`` / ``OSError`` like
    ``open()``.
    """
    path = layout.spool_file(folder, day)
    file = _file_of(path)
    with open_shared(path) as handle:
        stat = os.fstat(handle.fileno())
        size, identity = stat.st_size, _identity(stat)
        window = max(0, size - max_bytes)
        low = max(0, window - 1)  # one byte more: is the window's first byte a line start?
        position, parts, feeds = size, [], 0
        while position > low and feeds < n + 1:
            step = min(block, position - low)
            position -= step
            handle.seek(position)
            chunk = handle.read(step)
            parts.append(chunk)
            feeds += chunk.count(b"\n")
        data = b"".join(reversed(parts))
        last = data.rfind(b"\n")
        spool = rawlines.spool_anchor(path, identity, size)
        if anchor[0] > size or not _is_line_start(handle, anchor[0], size):
            anchor = (0, 0)
        if last < 0:  # no line ends in the window: count up to the end of the file
            end_line, end_offset = _count_on(handle, path, identity, max(anchor, spool), size)
            return TailRead([], end_offset, end_line, identity)
        end_offset = position + last + 1
        starts: list[int] = []
        cursor = last
        while len(starts) < n:
            previous = data.rfind(b"\n", 0, cursor)
            if previous < 0 and position > 0:
                break  # the line began before the bytes read
            line_start = position + previous + 1
            if line_start < window:
                break
            starts.append(line_start)
            if previous < 0:
                break
            cursor = previous
        starts.reverse()
        first = starts[0] if starts else end_offset
        if first <= anchor[0] <= end_offset:  # count back from an anchor inside the tail
            before = anchor[1] - data[first - position : anchor[0] - position].count(b"\n")
        else:
            known = max((point for point in (anchor, spool) if point[0] <= first), default=(0, 0))
            before, _ = _count_on(handle, path, identity, known, first)
    ends = [*starts[1:], end_offset] if starts else []
    lines = [
        RawLine(
            source=folder,
            day=day,
            line=before + index + 1,
            offset=start,
            data=data[start - position : stop - position - 1],
            origin="spool",
            file=file,
        )
        for index, (start, stop) in enumerate(zip(starts, ends, strict=True))
    ]
    return TailRead(lines, end_offset, before + len(lines), identity)


def tail_lines(
    layout: Layout,
    folder: str,
    day: date,
    n: int,
    anchor: Anchor = (0, 0),
    *,
    max_bytes: int = TAIL_MAX_BYTES,
) -> list[RawLine]:
    """The last ``n`` complete lines of ``spool/<folder>/<day>.log`` (see :func:`read_tail`)."""
    return read_tail(layout, folder, day, n, anchor, max_bytes=max_bytes).lines


@dataclass
class Follower:
    """New complete lines of one spool file, from the line start ``offset`` (``line`` complete
    lines before it).

    :meth:`poll` opens the file, reads what was appended and closes it again. When the file is
    gone (the sealer deleted it), ``ended`` becomes ``"gone"``; when it is smaller than the
    position or another file (inode) took its name, ``"file_replaced"``. An ended follower
    returns nothing.
    """

    path: Path
    offset: int
    line: int
    #: ``(st_dev, st_ino)`` of the file followed; taken at the first poll when not given.
    identity: tuple[int, int] | None = None
    ended: Ended | None = None
    #: File size seen by the last poll.
    size: int = field(default=0, init=False)
    #: Lines the last poll skipped (``skip_beyond``), counted, not returned.
    skipped: int = field(default=0, init=False)

    @property
    def ahead(self) -> int:
        """Bytes appended after the position (as of the last poll)."""
        return max(0, self.size - self.offset)

    def poll(
        self, max_bytes: int = POLL_MAX_BYTES, *, skip_beyond: int | None = None
    ) -> list[RawLine]:
        """The complete lines appended since the last poll (about ``max_bytes`` at most).

        With ``skip_beyond``: when more than that many bytes wait, the follower jumps to the
        last complete line instead and counts the lines it passed (:attr:`skipped`).
        """
        self.skipped = 0
        if self.ended is not None:
            return []
        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self.ended = "gone"
            return []
        identity = _identity(stat)
        if self.identity is None:
            self.identity = identity
        elif identity != self.identity or stat.st_size < self.offset:
            self.ended = "file_replaced"
            return []
        self.size = stat.st_size
        if self.size == self.offset:
            return []
        try:
            if skip_beyond is not None and self.ahead > skip_beyond:
                self._skip()
                return []
            data = read_complete(self.path, self.offset, max_bytes)
        except FileNotFoundError:
            self.ended = "gone"
            return []
        lines: list[RawLine] = []
        position, file = self.offset, _file_of(self.path)
        source, day = self.path.parent.name, date.fromisoformat(self.path.stem)
        for raw in data.split(b"\n")[:-1]:
            self.line += 1
            lines.append(RawLine(source, day, self.line, position, raw, "spool", file))
            position += len(raw) + 1
        self.offset = position
        return lines

    def _skip(self) -> None:
        with open_shared(self.path) as handle:
            identity = self.identity or (0, 0)
            lines, last_start = _count_on(
                handle, self.path, identity, (self.offset, self.line), self.size
            )
        self.skipped = lines - self.line
        self.offset, self.line = last_start, lines
