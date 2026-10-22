"""Raw spool lines by reference — byte-exact, for the raw-line view and the official report.

A reference ``(source, day, line[, offset])`` names line ``line`` (1-based) of the raw file of
``source`` and receive day ``day``, which starts at byte ``offset`` (docs/DATA-MODEL.md):

* ``spool/<source>/<day>.log`` while the day is not sealed (today, and yesterday until the
  nightly seal), then
* ``vault/<Y>/<M>/<D>/<source>.log.gz`` — the same bytes, gzip-compressed by the sealer.

**The line number is always verified**: the line feeds before the line are counted (C-speed
``bytes.count`` on 1 MiB chunks); a given ``offset`` must match the offset found, otherwise the
reference is refused — a client cannot have a line shown (and audited) under another number.
Costs:

* **spool** (the day is not sealed): the first lookup in a file reads it up to the line (disk or
  page-cache speed, roughly 1-2 s per GB on the server) and leaves line-start checkpoints about
  every 4 MiB in a small in-memory index (:data:`SPOOL_INDEX`, 32 files, least recently used
  dropped); every later lookup in that file seeks to the nearest checkpoint and reads at most a
  few MiB. Spool files only grow while their day is open, so checkpoints stay valid; a file that
  was replaced (other device/inode) or shrank starts a new index.
* **vault** (sealed ``.log.gz``): gzip cannot seek, so the first view in a file decompresses it
  from its start up to the line (measured: about 2 s per 500 MB of uncompressed log, so up to
  ~20 s for a late line of a 5 GB day) and leaves a copy of the decompressor state about every
  64 MiB in :data:`VAULT_INDEX` (8 files, least recently used dropped, ~40 KB per checkpoint);
  every later view in that file resumes at the nearest checkpoint and decompresses at most
  64 MiB (~0.3 s). Views of one file wait for each other (so the index is built once), and at
  most :data:`VAULT_SCANS` sealed files are decompressed at the same time.

Several lines of one file are found in one pass (:func:`scan_file`), so a report that cites
hundreds of lines reads each file once.

A file that exists but cannot be read (Windows: the service account has no right on the
folder) raises ``raw_access_denied``, other read errors ``raw_io_error`` — never "not found" and
never "damaged" (that is ``raw_unreadable``, for damaged compressed data).

Nothing here modifies a raw file: files are opened read-only, and the bytes are returned exactly
as stored (without the line feed). :func:`display_text` makes them safe to show.

**Never in the sealer's way** (docs/INGEST.md "Windows: file sharing and the sealer"): every raw
file is opened like ingest opens it — :func:`arcilogger_ingest.files.open_shared`, on Windows
with ``FILE_SHARE_DELETE`` — so a raw-line view or a report hashing a large spool file at
night never makes the Seal task's deletion of that spool file fail (it would retry 5 minutes
later with an alert); the open handle keeps reading the file until it is closed. A spool file
that the sealer removed between finding it and opening it is looked for again, in the vault
(:func:`read_line`).
"""

from __future__ import annotations

import bisect
import hashlib
import stat
import threading
import unicodedata
import zlib
from collections import OrderedDict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, BinaryIO, Final, Literal

from arcilogger_core.evidence.model import SOURCE_NAME
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import RawLineError, RawRecord, parse_line
from arcilogger_ingest.files import open_shared

CHUNK: Final = 1024 * 1024
#: A raw line is at most ``collector.max_message_bytes`` (1 MiB) escaped + its prefix; anything
#: longer is not a spool line.
MAX_LINE_BYTES: Final = 5 * 1024 * 1024
#: Distance between two line-start checkpoints of the spool index.
CHECKPOINT_BYTES: Final = 4 * 1024 * 1024
#: Spool files the index keeps checkpoints for.
INDEXED_FILES: Final = 32
#: Uncompressed distance between two decompressor checkpoints of a sealed file (resuming
#: decompresses at most this much: about 0.2-0.3 s).
VAULT_CHECKPOINT_BYTES: Final = 64 * 1024 * 1024
#: Sealed files the vault index keeps checkpoints for (a 5 GB day: about 80 checkpoints, 3 MB).
VAULT_INDEXED_FILES: Final = 8
#: Sealed files decompressed at the same time; further views wait for a free slot.
VAULT_SCANS: Final = 2
#: A vault checkpoint keeps the decompressed start of the line it falls into; where that is
#: longer, the checkpoint moves on to a later piece (bounds the index's memory).
CHECKPOINT_TAIL_BYTES: Final = 64 * 1024
#: Characters shown as ``\\uXXXX`` (control, format such as bidi overrides, surrogates, line and
#: paragraph separators): they could hide or reorder text on the page.
_UNSAFE_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp", "Co", "Cn"})

Origin = Literal["spool", "vault"]


class RawReferenceError(ValueError):
    """A reference cannot be resolved; ``code`` is the API error code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def display_text(data: bytes) -> str:
    """Text for people: UTF-8, undecodable bytes as ``\\xNN``, unsafe characters as ``\\uXXXX``.

    The spool's own escapes (``#012`` for a line feed, ``#043`` for ``#``) stay visible as they
    are in the file. Exact bytes are available separately (base64 in the API).
    """
    text = data.decode("utf-8", "backslashreplace")
    if all(unicodedata.category(char) not in _UNSAFE_CATEGORIES for char in text):
        return text
    return "".join(
        f"\\u{ord(char):04x}" if unicodedata.category(char) in _UNSAFE_CATEGORIES else char
        for char in text
    )


@dataclass(frozen=True)
class RawLine:
    source: str
    day: date
    line: int
    offset: int
    #: The line exactly as stored, without its ``\\n``.
    data: bytes
    origin: Origin
    #: Data-directory relative path of the file it was read from (``/`` separators).
    file: str

    @property
    def sha256(self) -> str:
        """SHA-256 of :attr:`data` (the line without its line feed)."""
        return hashlib.sha256(self.data).hexdigest()

    @property
    def text(self) -> str:
        return display_text(self.data)

    def record(self) -> RawRecord | None:
        """Receive time, sender and message of the line (``None`` if it is not in spool format)."""
        try:
            return parse_line(self.data)
        except RawLineError:
            return None


def valid_source_name(source: str) -> bool:
    """Spool folder names: configured names, ``<network source>-<ip>``, ``unknown-<ip>``.

    The pattern admits no path separator, no leading dot and no ``_audit`` (the audit spool is
    not a raw log), so a name can never leave ``spool/`` or ``vault/<day>/``.
    """
    return bool(SOURCE_NAME.fullmatch(source))


def _inside(path: Path, folder: Path) -> bool:
    try:
        return path.resolve().is_relative_to(folder.resolve())
    except OSError:  # pragma: no cover - resolve() failing means the path is unusable anyway
        return False


def regular_file(path: Path) -> bool:
    """``True`` for a regular file, ``False`` when there is none.

    Any other error is **raised** — above all "access denied" (Windows: the service account has
    no right on the folder). ``Path.is_file`` raises it on Python 3.12/3.13 but returns ``False``
    from 3.14 on, which would turn "cannot read" into "does not exist".
    """
    try:
        return stat.S_ISREG(path.stat().st_mode)
    except (FileNotFoundError, NotADirectoryError):
        return False


def os_error(path: Path, exc: OSError) -> RawReferenceError:
    """``raw_access_denied`` (the service may not read the file) or ``raw_io_error``."""
    reason = exc.strerror or exc.__class__.__name__
    if isinstance(exc, PermissionError):
        return RawReferenceError("raw_access_denied", f"{path.name}: access denied ({reason})")
    return RawReferenceError("raw_io_error", f"{path.name}: {reason}")


def locate(layout: Layout, source: str, day: date) -> tuple[Origin, Path] | None:
    """Where the raw file of ``source`` and ``day`` is: the spool first, then the vault.

    A file that exists but cannot be examined (access denied) raises ``raw_access_denied`` /
    ``raw_io_error`` — unless the other place has the file (a sealed day whose spool folder the
    API may not read).
    """
    if not valid_source_name(source):
        raise RawReferenceError("raw_source_invalid", f"invalid source name {source!r}")
    failure: RawReferenceError | None = None
    spool = layout.spool_file(source, day)
    try:
        if regular_file(spool) and _inside(spool, layout.spool):
            return "spool", spool
    except OSError as exc:
        failure = os_error(spool, exc)
    vault = layout.vault_day(day) / f"{source}.log.gz"
    try:
        if regular_file(vault) and _inside(vault, layout.vault):
            return "vault", vault
    except OSError as exc:
        failure = failure or os_error(vault, exc)
    if failure is not None:
        raise failure
    return None


@dataclass
class LineFinder:
    """Feed a file's (decompressed) bytes in order; collects the wanted lines and counts lines.

    ``found[line] = (offset, bytes without \\n)``. Only complete lines (ending in ``\\n``) count:
    the last line of a spool file that is still being written is not a line yet.

    ``start_offset`` / ``start_line``: the bytes fed begin at that offset, which is the start of
    that line (a checkpoint). ``checkpoints`` collects ``(offset, line)`` of line starts about
    every ``checkpoint_bytes`` passed on the way.
    """

    wanted: Iterable[int]
    start_offset: int = 0
    start_line: int = 1
    checkpoint_bytes: int = CHECKPOINT_BYTES
    found: dict[int, tuple[int, bytes]] = field(default_factory=dict)
    #: Complete lines before the current position (= line feeds, counted from the file start).
    lines: int = field(init=False, default=0)
    checkpoints: list[tuple[int, int]] = field(init=False, default_factory=list)
    _targets: list[int] = field(init=False)
    _next: int = field(init=False, default=0)
    _line_start: int = field(init=False, default=0)
    _position: int = field(init=False, default=0)
    _pending: bytearray | None = field(init=False, default=None)
    _last_checkpoint: int = field(init=False, default=0)

    def __post_init__(self) -> None:
        self._targets = sorted(n for n in set(self.wanted) if n >= self.start_line)
        self.lines = self.start_line - 1
        self._line_start = self._position = self._last_checkpoint = self.start_offset

    @property
    def done(self) -> bool:
        return self._next >= len(self._targets)

    @property
    def line_start(self) -> int | None:
        """Offset where line ``lines + 1`` starts (the bytes fed so far end inside it);
        ``None`` while a wanted line is half read or after the last wanted line was found."""
        return None if self._pending is not None or self.done else self._line_start

    def feed(self, chunk: bytes) -> None:
        start = 0
        size = len(chunk)
        while start < size and not self.done:
            line_number = self.lines + 1
            if self._pending is not None or line_number == self._targets[self._next]:
                end = chunk.find(b"\n", start)
                pending = self._pending if self._pending is not None else bytearray()
                pending += chunk[start : size if end < 0 else end]
                if len(pending) > MAX_LINE_BYTES:
                    raise RawReferenceError(
                        "raw_not_found", f"line {line_number} is longer than a raw line can be"
                    )
                if end < 0:
                    self._pending = pending
                    start = size
                    break
                self.found[line_number] = (self._line_start, bytes(pending))
                self._pending = None
                self._next += 1
                self.lines += 1
                self._line_start = self._position + end + 1
                start = end + 1
                continue
            skip = self._targets[self._next] - line_number  # line feeds before the target
            available = chunk.count(b"\n", start)
            if available < skip:
                if available:
                    self._line_start = self._position + chunk.rfind(b"\n", start) + 1
                self.lines += available
                start = size
                break
            index = start - 1
            for _ in range(skip):
                index = chunk.find(b"\n", index + 1)
            self.lines += skip
            self._line_start = self._position + index + 1
            start = index + 1
        # Here ``_line_start`` is the start of line ``lines + 1`` (unless a line is half read).
        if self._pending is None and self._line_start >= self._last_checkpoint + max(
            self.checkpoint_bytes, 1
        ):
            self.checkpoints.append((self._line_start, self.lines + 1))
            self._last_checkpoint = self._line_start
        if start < size:  # targets done: only count the rest
            self.lines += chunk.count(b"\n", start)
        self._position += size


@dataclass
class _Checkpoints:
    identity: tuple[int, int]
    #: ``(offset of a line start, its 1-based number)``, ascending in both; first ``(0, 1)``.
    points: list[tuple[int, int]] = field(default_factory=lambda: [(0, 1)])

    def floor(self, line: int) -> tuple[int, int]:
        """The last checkpoint at or before ``line``."""
        index = bisect.bisect_right(self.points, line, key=lambda point: point[1]) - 1
        return self.points[max(index, 0)]

    def add(self, offset: int, line: int) -> None:
        last_offset, last_line = self.points[-1]
        if offset >= last_offset + CHECKPOINT_BYTES and line > last_line:
            self.points.append((offset, line))


class SpoolIndex:
    """Line-start checkpoints of recently read spool files (thread-safe, least recently used
    files dropped). Only positions are kept, never content."""

    def __init__(self, files: int = INDEXED_FILES) -> None:
        self._files = files
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _Checkpoints] = OrderedDict()

    def start(self, path: Path, identity: tuple[int, int], size: int, line: int) -> tuple[int, int]:
        """Where to start looking for ``line``: ``(offset, line number)`` of a line start."""
        key = str(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.identity != identity or entry.points[-1][0] > size:
                entry = self._entries[key] = _Checkpoints(identity)
            self._entries.move_to_end(key)
            while len(self._entries) > self._files:
                self._entries.popitem(last=False)
            return entry.floor(line)

    def record(
        self, path: Path, identity: tuple[int, int], points: Iterable[tuple[int, int]]
    ) -> None:
        with self._lock:
            entry = self._entries.get(str(path))
            if entry is not None and entry.identity == identity:
                for offset, line in points:
                    entry.add(offset, line)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


SPOOL_INDEX: Final = SpoolIndex()


@dataclass(frozen=True)
class GzPoint:
    """Where decompressing a sealed file can resume: after ``compressed`` bytes of the file, with
    the decompressor as it was there (``state``, only ever copied); ``produced`` bytes came out,
    line ``line`` starts at ``line_start`` and ``tail`` is its part decompressed so far."""

    compressed: int
    state: Any
    produced: int
    line_start: int
    line: int
    tail: bytes


@dataclass
class _GzEntry:
    identity: tuple[int, ...]
    #: Ascending in every field; appended to only under ``lock``.
    points: list[GzPoint] = field(default_factory=list)
    #: Held while the file is scanned: views of one file wait for each other (and then resume
    #: from the checkpoints the first one left) instead of all decompressing from the start.
    lock: threading.Lock = field(default_factory=threading.Lock)

    def floor(self, line: int) -> GzPoint | None:
        index = bisect.bisect_right(self.points, line, key=lambda point: point.line) - 1
        return self.points[index] if index >= 0 else None


class VaultIndex:
    """Decompressor checkpoints of recently read sealed files (``.log.gz``), least recently used
    files dropped. A checkpoint holds a copy of the zlib state (about 40 KB with its 32 KB
    window) and the start of the line it falls into — positions and a few KB of data already
    shown to the viewer, never a decompressed file. ``scans`` bounds how many sealed files are
    decompressed at the same time (the CPU one user can occupy with ``/raw``)."""

    def __init__(self, files: int = VAULT_INDEXED_FILES, scans: int = VAULT_SCANS) -> None:
        self._files = files
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, _GzEntry] = OrderedDict()
        self.scans = threading.BoundedSemaphore(scans)

    def entry(self, path: Path, identity: tuple[int, ...]) -> _GzEntry:
        key = str(path)
        with self._lock:
            entry = self._entries.get(key)
            if entry is None or entry.identity != identity:
                entry = self._entries[key] = _GzEntry(identity)
            self._entries.move_to_end(key)
            while len(self._entries) > self._files:
                self._entries.popitem(last=False)
            return entry

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


VAULT_INDEX: Final = VaultIndex()


def _gunzip() -> Any:
    return zlib.decompressobj(wbits=zlib.MAX_WBITS | 16)


def _plain_chunks(handle: BinaryIO) -> Iterator[bytes]:
    while chunk := handle.read(CHUNK):
        yield chunk


def gzip_chunks(raw_chunks: Iterable[bytes]) -> Iterator[bytes]:
    """Decompress a gzip stream (one or more members) in pieces of at most :data:`CHUNK` bytes
    (well-compressed logs would otherwise come out ~100 MB per call)."""
    decompressor = _gunzip()
    for raw in raw_chunks:
        data = raw
        while data:
            out = decompressor.decompress(data, CHUNK)
            if out:
                yield out
            if decompressor.eof:
                data = decompressor.unused_data
                decompressor = _gunzip()
            else:
                data = decompressor.unconsumed_tail
    tail = decompressor.flush()
    if tail:
        yield tail


@dataclass
class FileScan:
    """What one pass over a raw file found (see :func:`scan_file`)."""

    origin: Origin
    path: Path
    found: dict[int, tuple[int, bytes]]
    #: SHA-256 of the file's bytes as stored (the compressed bytes for a sealed file), when the
    #: whole file was read (``hash_file=True``).
    sha256: str | None = None
    #: Bytes as stored, read in this pass.
    size: int | None = None
    #: Complete lines (plain files, ``hash_file=True`` only).
    lines: int | None = None
    #: Why the (compressed) content could not be read; lines after the damage are not found.
    error: str | None = None


def scan_file(
    origin: Origin, path: Path, wanted: Iterable[int], *, hash_file: bool = False
) -> FileScan:
    """Find the ``wanted`` lines of one raw file in a single pass.

    ``hash_file`` also hashes the whole file as stored (and counts the lines of a plain spool
    file): used by the report for "the file at report time" / "the sealed file still matches its
    manifest". Without it reading stops after the last wanted line.
    """
    finder = LineFinder(wanted)
    hasher = hashlib.sha256() if hash_file else None
    size = 0
    error: str | None = None
    with open_shared(path) as handle:
        if origin == "spool":
            for chunk in _plain_chunks(handle):
                size += len(chunk)
                if hasher is not None:
                    hasher.update(chunk)
                    finder.feed(chunk)
                elif not finder.done:
                    finder.feed(chunk)
                else:
                    break
        else:

            def compressed() -> Iterator[bytes]:
                nonlocal size
                for chunk in _plain_chunks(handle):
                    size += len(chunk)
                    if hasher is not None:
                        hasher.update(chunk)
                    yield chunk

            raw = compressed()
            try:
                for data in gzip_chunks(raw):
                    finder.feed(data)
                    if finder.done:
                        break
            except zlib.error as exc:
                error = f"{path.name}: damaged gzip data ({exc})"
            if hasher is not None:
                for _ in raw:  # hash the rest without decompressing it
                    pass
    return FileScan(
        origin=origin,
        path=path,
        found=finder.found,
        sha256=hasher.hexdigest() if hasher is not None else None,
        size=size if hash_file else None,
        lines=finder.lines if hash_file and origin == "spool" else None,
        error=error,
    )


def _find_in_spool(path: Path, line: int) -> tuple[int, bytes] | None:
    """Line ``line`` of a spool file, starting at the nearest checkpoint of :data:`SPOOL_INDEX`."""
    stat = path.stat()
    identity = (stat.st_dev, stat.st_ino)
    start_offset, start_line = SPOOL_INDEX.start(path, identity, stat.st_size, line)
    finder = LineFinder(
        [line],
        start_offset=start_offset,
        start_line=start_line,
        checkpoint_bytes=CHECKPOINT_BYTES,
    )
    with open_shared(path) as handle:
        handle.seek(start_offset)
        for chunk in _plain_chunks(handle):
            finder.feed(chunk)
            if finder.done:
                break
    SPOOL_INDEX.record(path, identity, finder.checkpoints)
    return finder.found.get(line)


def _find_in_vault(path: Path, line: int) -> tuple[tuple[int, bytes] | None, str | None]:
    """Line ``line`` of a sealed file, resuming at the nearest checkpoint of :data:`VAULT_INDEX`
    (``(found, damage)``: ``damage`` names damaged compressed data before the line).

    Output comes in pieces of at most :data:`CHUNK` bytes; after each piece the position is
    ``compressed - len(data)`` (``data``: input the current decompressor has not consumed — or,
    after the end of a gzip member, the next member for a fresh one), which is where a
    checkpoint resumes."""
    stat = path.stat()
    entry = VAULT_INDEX.entry(path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    with VAULT_INDEX.scans, entry.lock:
        start = entry.floor(line)
        if start is None:
            decompressor, compressed, produced = _gunzip(), 0, 0
            finder = LineFinder([line])
        else:
            decompressor = start.state.copy()
            compressed, produced = start.compressed, start.produced
            finder = LineFinder([line], start_offset=start.line_start, start_line=start.line)
            finder.feed(start.tail)
        last = entry.points[-1].produced if entry.points else 0
        damage: str | None = None
        with open_shared(path) as handle:
            handle.seek(compressed)
            try:
                for raw in _plain_chunks(handle):
                    compressed += len(raw)
                    data = raw
                    while data and not finder.done:
                        out = decompressor.decompress(data, CHUNK)
                        if out:
                            finder.feed(out)
                            produced += len(out)
                        if decompressor.eof:
                            data = decompressor.unused_data
                            decompressor = _gunzip()
                        else:
                            data = decompressor.unconsumed_tail
                        line_start = finder.line_start
                        if (
                            line_start is not None
                            and produced >= last + VAULT_CHECKPOINT_BYTES
                            # the line began in ``out`` and its start is short enough to keep
                            and produced - line_start <= min(len(out), CHECKPOINT_TAIL_BYTES)
                        ):
                            entry.points.append(
                                GzPoint(
                                    compressed=compressed - len(data),
                                    state=decompressor.copy(),
                                    produced=produced,
                                    line_start=line_start,
                                    line=finder.lines + 1,
                                    tail=out[len(out) - (produced - line_start) :],
                                )
                            )
                            last = produced
                    if finder.done:
                        break
                else:
                    finder.feed(decompressor.flush())
            except zlib.error as exc:
                damage = f"{path.name}: damaged gzip data ({exc})"
    return finder.found.get(line), damage


def read_line(
    layout: Layout, source: str, day: date, line: int, offset: int | None = None
) -> RawLine:
    """One raw line; :class:`RawReferenceError` when it does not exist or ``offset`` is wrong."""
    if line < 1:
        raise RawReferenceError("raw_not_found", "line numbers start at 1")
    for attempt in (1, 2):
        where = locate(layout, source, day)
        if where is None:
            raise RawReferenceError("raw_not_found", f"no raw file of {source} for {day}")
        origin, path = where
        try:
            if origin == "spool":
                found = _find_in_spool(path, line)
            else:
                found, damage = _find_in_vault(path, line)
                if found is None and damage is not None:
                    raise RawReferenceError("raw_unreadable", damage)
        except FileNotFoundError as exc:
            if origin == "spool" and attempt == 1:
                continue  # sealed just now: the day is in the vault
            raise os_error(path, exc) from exc
        except OSError as exc:
            raise os_error(path, exc) from exc
        break
    if found is None:
        raise RawReferenceError("raw_not_found", f"{path.name} has fewer than {line} lines")
    found_offset, data = found
    if offset is not None and offset != found_offset:
        raise RawReferenceError(
            "raw_reference_mismatch",
            f"line {line} starts at byte {found_offset}, not at byte {offset}",
        )
    return RawLine(
        source=source,
        day=day,
        line=line,
        offset=found_offset,
        data=data,
        origin=origin,
        file=path.relative_to(layout.root).as_posix(),
    )
