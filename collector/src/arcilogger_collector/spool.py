"""Appends raw lines to ``spool/<source>/<YYYY-MM-DD>.log``.

* Files are opened unbuffered in append mode, so every line reaches the operating system as soon
  as it is written (a crash of the collector loses nothing already received).
* ``flush`` fsyncs the files written since the last call; the collector calls it every
  ``fsync_interval_seconds`` (default 1 s) so a power cut loses at most that much.
* The day of a line is the **receive day in Europe/Istanbul**. A new day opens a new file;
  files of earlier days are fsync'ed and closed by ``close_stale`` shortly after midnight, which
  is what lets the sealing job start on a closed, quiet file.

**One line is one message, also after a crash or a full disk** (ADR-0002). A write is repeated
until every byte of the line is written; when it fails part-way (disk full, I/O error) the
fragment stays in the file — raw bytes are evidence and are never rewritten or removed — and
the file is marked. Before the next line goes into a marked file, and whenever a file is
(re)opened, e.g. after a power cut left a partial last line, its last byte is checked: if it is
not ``\\n`` a single ``\\n`` is **appended** to terminate the fragment, so the next message
always starts its own line. The fragment then reads as one damaged line (a parse failure in
ingest) instead of swallowing the next, intact message. Every such termination is reported
(:class:`TornLine`, ``on_torn``): the collector raises an alert and lists it in its status file.
"""

from __future__ import annotations

import errno
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from typing import BinaryIO

from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import site_day

log = logging.getLogger("arcilogger.collector")

Key = tuple[str, date]


@dataclass(frozen=True)
class TornLine:
    """A spool file ended in an unterminated line; ``\\n`` was appended at ``offset``."""

    source: str
    day: date
    #: Size of the file before the ``\\n`` was appended (= byte offset of that ``\\n``).
    offset: int


def write_all(handle: BinaryIO, data: bytes) -> None:
    """Write every byte of ``data`` (repeating short writes); ``OSError`` when that fails."""
    view = memoryview(data)
    written = 0
    while written < len(data):
        count = handle.write(view[written:])
        if not count:  # 0 or None: nothing more can be written (e.g. the disk is full)
            raise OSError(errno.ENOSPC, "short write to the spool file")
        written += count


class SpoolWriter:
    def __init__(
        self, layout: Layout, *, on_torn: Callable[[TornLine], None] | None = None
    ) -> None:
        self._layout = layout
        self._handles: dict[Key, BinaryIO] = {}
        self._dirty: set[Key] = set()
        #: Files whose last line may be unterminated (a write failed part-way).
        self._suspect: set[Key] = set()
        self._on_torn = on_torn
        self.torn_lines: list[TornLine] = []

    def write(self, source: str, received: datetime, sender: str, message: bytes) -> None:
        """Append one line. ``OSError`` when it could not be written completely (the file is
        then marked, and its last line is terminated before the next line goes in)."""
        line = format_line(received, sender, message)
        key = (source, site_day(received))
        handle = self._open(key)
        try:
            if key in self._suspect:
                self._terminate(key, handle)
            write_all(handle, line)
        except OSError:
            self._suspect.add(key)
            raise
        finally:
            self._dirty.add(key)

    def terminate_torn_lines(self, day: date) -> None:
        """Open every existing spool file of ``day`` and terminate an unterminated last line
        (the collector does this for today when it starts, e.g. after a power cut)."""
        for source, _path in self._layout.spool_files_for(day):
            key = (source, day)
            if key in self._handles and key not in self._suspect:
                continue
            try:
                handle = self._open(key)
                if key in self._suspect:
                    self._terminate(key, handle)
            except OSError as exc:
                self._suspect.add(key)
                log.warning("could not check the last line of %s %s: %s", source, day, exc)
            self._dirty.add(key)

    def _open(self, key: Key) -> BinaryIO:
        handle = self._handles.get(key)
        if handle is None:
            path = self._layout.spool_file(*key)
            path.parent.mkdir(parents=True, exist_ok=True)
            handle = path.open("ab", buffering=0)
            self._handles[key] = handle
            # A file written before this process started may end in a torn line.
            self._suspect.add(key)
        return handle

    def _terminate(self, key: Key, handle: BinaryIO) -> None:
        """Append ``\\n`` when the file does not end with one (never changes existing bytes)."""
        size = os.fstat(handle.fileno()).st_size
        if size > 0:
            # A second, read-only handle: the append handle cannot read. CPython opens files
            # with full sharing on Windows, as ingest does, so this never blocks a writer.
            with self._layout.spool_file(*key).open("rb") as reader:
                reader.seek(size - 1)
                last = reader.read(1)
            if last not in (b"\n", b""):
                write_all(handle, b"\n")
                torn = TornLine(source=key[0], day=key[1], offset=size)
                self.torn_lines.append(torn)
                log.warning(
                    "spool %s/%s: the last line was incomplete (crash, power cut or full disk); "
                    "terminated it with a newline at byte %d, existing bytes unchanged",
                    key[0],
                    key[1],
                    size,
                )
                if self._on_torn is not None:
                    self._on_torn(torn)
        self._suspect.discard(key)

    def flush(self) -> None:
        for key in list(self._dirty):
            handle = self._handles.get(key)
            if handle is not None:
                os.fsync(handle.fileno())
            self._dirty.discard(key)

    def close_stale(self, today: date) -> list[tuple[str, date]]:
        """Fsync and close files of days before ``today``; returns what was closed."""
        stale = [key for key in self._handles if key[1] < today]
        for key in stale:
            handle = self._handles.pop(key)
            self._suspect.discard(key)
            self._dirty.discard(key)
            try:
                os.fsync(handle.fileno())
            finally:
                handle.close()
        return stale

    def close(self) -> None:
        handles = list(self._handles.values())
        self._handles.clear()
        self._dirty.clear()
        self._suspect.clear()
        errors: list[OSError] = []
        for handle in handles:
            try:
                os.fsync(handle.fileno())
            except OSError as exc:
                errors.append(exc)
            finally:
                handle.close()
        if errors:
            raise errors[0]

    @property
    def open_files(self) -> list[tuple[str, date]]:
        return sorted(self._handles)
