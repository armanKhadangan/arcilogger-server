"""The sealed files of a vault day: their names, their two formats and the one reader for both.

A day's raw log of one source is sealed into ``vault/<Y>/<M>/<D>/<source>.log`` plus the suffix of
its format, and the day's audit log into ``audit.jsonl`` plus the same suffix:

- ``.gz``: gzip without a name and with mtime 0 (``gzip -n``), the format of every day sealed
  before Wave 5 and of ``[seal] compression = "gzip"``;
- ``.xz``: *seekable xz* (Wave 5 contract H2), the default. The file is a concatenation of
  independent ``.xz`` streams (``FORMAT_XZ``, ``CHECK_CRC64``, no stream padding); each holds
  exactly :data:`STREAM_BYTES` of input, the last one less. The streams are compressed on
  ``[seal] xz_threads`` threads and written in order, so the bytes depend on the input, the
  preset and liblzma only, never on the thread count. Any xz tool (``xz -d``, 7-Zip) reads the
  concatenation as one file; a reader that wants one line finds its stream from the xz index in
  the stream footers (:func:`xz_streams`), so there is no sidecar file.

A sealed day is never re-encoded: old ``.gz`` days stay as they are, and both formats are read
forever. The manifest's ``path`` tells the format (its suffix); the schema stays
``arcilogger.manifest/1``. Code that needs a day's file without the manifest uses
:func:`find_log` / :func:`find_audit`; no other module writes the suffixes (a test enforces it).
"""

from __future__ import annotations

import gzip
import hashlib
import io
import lzma
import os
import shutil
import struct
import zlib
from collections import deque
from collections.abc import Generator, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import BinaryIO, Final, Literal

from arcilogger_core.fsutil import fsync_dir
from arcilogger_core.layout import Layout

Compression = Literal["gzip", "xz"]

#: Suffix of each format, the only place the suffixes are written.
SUFFIXES: Final[dict[str, str]] = {"gzip": ".gz", "xz": ".xz"}
#: Uncompressed bytes per xz stream (H2). Tests patch it to see several streams in a small file.
STREAM_BYTES: int = 16 * 1024 * 1024
#: Read size for sequential work.
CHUNK: Final = 1024 * 1024
#: What the decompressors raise on damaged data (gzip's ``BadGzipFile`` is an ``OSError``).
DAMAGE_ERRORS: Final = (EOFError, zlib.error, lzma.LZMAError, gzip.BadGzipFile)

_XZ_MAGIC: Final = b"\xfd7zXZ\x00"
_XZ_FOOTER_MAGIC: Final = b"YZ"
_XZ_HEADER_SIZE: Final = 12


class SealedFileError(OSError):
    """A sealed file that cannot be used: ``code`` is ``sealed_file_ambiguous`` or ``damaged``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


# -- names -------------------------------------------------------------------------------------


def log_name(source: str, compression: Compression) -> str:
    """File name of a source's sealed day in the given format (``<source>.log.<suffix>``)."""
    return f"{source}.log{SUFFIXES[compression]}"


def audit_name(compression: Compression) -> str:
    """File name of a day's sealed audit log in the given format."""
    return f"audit.jsonl{SUFFIXES[compression]}"


def format_of(path: Path | str) -> Compression:
    """The format of a sealed file, from its suffix (a manifest ``path`` or a file name)."""
    name = str(path)
    for compression, suffix in SUFFIXES.items():
        if name.endswith(suffix):
            return compression  # type: ignore[return-value]
    raise SealedFileError("damaged", f"{Path(name).name}: not a sealed file (.gz or .xz)")


def _find(folder: Path, stem: str) -> Path | None:
    found = [
        folder / f"{stem}{suffix}"
        for suffix in SUFFIXES.values()
        if (folder / f"{stem}{suffix}").is_file()
    ]
    if len(found) > 1:
        raise SealedFileError(
            "sealed_file_ambiguous",
            f"{folder.name}/{stem}: sealed in both formats ({', '.join(p.name for p in found)})",
        )
    return found[0] if found else None


def find_log(layout: Layout, day: date, source: str) -> Path | None:
    """The sealed file of ``source`` on ``day`` in whichever format it has, or ``None``.

    Both a ``.gz`` and an ``.xz`` for one source raise ``sealed_file_ambiguous``: the sealer never
    leaves both (it removes the other format of a failed attempt), so a pair means a file was put
    there by hand.
    """
    return _find(layout.vault_day(day), f"{source}.log")


def find_audit(layout: Layout, day: date) -> Path | None:
    """The sealed audit log of ``day`` in whichever format it has, or ``None``."""
    return _find(layout.vault_day(day), "audit.jsonl")


def other_formats(target: Path) -> list[Path]:
    """The same sealed file in the other format(s), which a sealer removes before it writes."""
    current = format_of(target)
    stem = target.name[: -len(SUFFIXES[current])]
    return [target.with_name(stem + suffix) for c, suffix in SUFFIXES.items() if c != current]


# -- writing -----------------------------------------------------------------------------------


def _xz_stream(chunk: bytes, preset: int) -> bytes:
    return lzma.compress(chunk, format=lzma.FORMAT_XZ, check=lzma.CHECK_CRC64, preset=preset)


def _write_xz(raw: BinaryIO, out: BinaryIO, preset: int, threads: int) -> None:
    """Independent streams of :data:`STREAM_BYTES` each, compressed on ``threads`` threads and
    written in order; at most ``threads`` streams are in memory at once."""
    first = raw.read(STREAM_BYTES)
    if threads <= 1:
        chunk = first
        while True:
            out.write(_xz_stream(chunk, preset))
            chunk = raw.read(STREAM_BYTES)
            if not chunk:
                return
    with ThreadPoolExecutor(max_workers=threads, thread_name_prefix="seal-xz") as pool:
        pending: deque[Future[bytes]] = deque([pool.submit(_xz_stream, first, preset)])
        while pending:
            while len(pending) < threads:
                chunk = raw.read(STREAM_BYTES)
                if not chunk:
                    break
                pending.append(pool.submit(_xz_stream, chunk, preset))
            out.write(pending.popleft().result())


def write(
    source: Path,
    target: Path,
    *,
    gzip_level: int = 9,
    xz_preset: int = 6,
    xz_threads: int = 2,
) -> None:
    """Compress ``source`` into ``target`` in the format of ``target``'s suffix, durably.

    The same input gives the same bytes: gzip without a name and with mtime 0 (``gzip -n``), xz
    as independent streams in order. The file is written as ``<target>.part``, flushed, fsync'ed
    and renamed; an empty input still gives a valid file (one empty member or stream).
    """
    compression = format_of(target)
    part = target.with_name(target.name + ".part")
    with source.open("rb") as raw, part.open("wb") as out:
        if compression == "gzip":
            with gzip.GzipFile(
                filename="", mode="wb", compresslevel=gzip_level, fileobj=out, mtime=0
            ) as gz:
                shutil.copyfileobj(raw, gz, CHUNK)
        else:
            _write_xz(raw, out, xz_preset, xz_threads)
        out.flush()
        os.fsync(out.fileno())
    part.replace(target)
    fsync_dir(target.parent)


# -- reading -----------------------------------------------------------------------------------


class _ChunkReader(io.RawIOBase):
    """A readable raw stream over :func:`chunks_from`, for ``readline`` and ``read(n)``."""

    def __init__(self, chunks: Generator[bytes, None, None], on_close: BinaryIO | None) -> None:
        self._chunks = chunks
        self._pending = b""
        self._on_close = on_close

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: memoryview) -> int:  # type: ignore[override]
        while not self._pending:
            try:
                self._pending = next(self._chunks)
            except StopIteration:
                return 0
        size = min(len(buffer), len(self._pending))
        buffer[:size] = self._pending[:size]
        self._pending = self._pending[size:]
        return size

    def close(self) -> None:
        if not self.closed:
            self._chunks.close()
            if self._on_close is not None:
                self._on_close.close()
        super().close()


def open_sequential(path: Path, raw: BinaryIO | None = None, offset: int = 0) -> BinaryIO:
    """A buffered binary reader of the uncompressed content of a sealed file, from ``offset``.

    It reads every gzip member and every xz stream. xz is read stream by stream from the index in
    the footers (:func:`xz_streams`), so damage in any stream is an error and never a silently
    shorter file (``lzma.LZMAFile`` stops quietly at a damaged later stream, treating it as
    trailing garbage). Damaged data raises :class:`SealedFileError` or one of
    :data:`DAMAGE_ERRORS` from ``read``; callers name it as damage, not as a missing file. A
    ``raw`` handle stays open when the reader is closed; a file opened here is closed with it.
    """
    handle = path.open("rb") if raw is None else raw
    try:
        chunks = chunks_from(path, offset, handle)
    except BaseException:
        if raw is None:
            handle.close()
        raise
    return io.BufferedReader(_ChunkReader(chunks, handle if raw is None else None), CHUNK)


def digest(path: Path) -> tuple[str, int]:
    """SHA-256 and line count of the uncompressed content of a sealed file of either format.

    Lines are counted by the one rule ARCILOGGER uses everywhere (sealing, ingest, extracts,
    the standalone verifier): every ``\\n`` ends a line, and a non-empty last line without
    ``\\n`` (a write torn by a crash or a full disk) counts as a line too. Damaged data raises
    :class:`SealedFileError` (``damaged``).
    """
    hasher = hashlib.sha256()
    lines = 0
    last = b""
    try:
        for chunk in chunks_from(path, 0):
            hasher.update(chunk)
            lines += chunk.count(b"\n")
            last = chunk[-1:]
    except SealedFileError:
        raise
    except DAMAGE_ERRORS as exc:
        raise SealedFileError("damaged", f"{path.name}: damaged compressed data ({exc})") from exc
    if last not in (b"", b"\n"):
        lines += 1
    return hasher.hexdigest(), lines


@dataclass(frozen=True)
class XzStream:
    """One stream of a seekable xz file: where it is and which uncompressed bytes it holds."""

    compressed_start: int
    compressed_size: int
    uncompressed_start: int
    uncompressed_size: int


def _varint(data: bytes, pos: int) -> tuple[int, int]:
    value = 0
    for shift in range(0, 63, 7):
        if pos >= len(data):
            break
        byte = data[pos]
        pos += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, pos
    raise SealedFileError("damaged", "xz index: bad number")


def _read_at(handle: BinaryIO, offset: int, size: int) -> bytes:
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise SealedFileError("damaged", "xz: file ends early")
    return data


def xz_streams(path: Path, handle: BinaryIO | None = None) -> list[XzStream]:
    """The streams of an xz file, read backwards from the stream footers and indexes.

    Nothing is decompressed: each footer gives the size of its index, each index the sizes of the
    stream's blocks. A file that is not a clean concatenation of streams without padding raises
    :class:`SealedFileError` (``damaged``).
    """
    own = handle is None
    raw = path.open("rb") if handle is None else handle
    try:
        raw.seek(0, io.SEEK_END)
        end = raw.tell()
        found: list[tuple[int, int, int]] = []  # (compressed start, compressed size, uncompressed)
        while end > 0:
            if end < 2 * _XZ_HEADER_SIZE:
                raise SealedFileError("damaged", f"{path.name}: not an xz file")
            footer = _read_at(raw, end - _XZ_HEADER_SIZE, _XZ_HEADER_SIZE)
            if footer[10:12] != _XZ_FOOTER_MAGIC:
                raise SealedFileError("damaged", f"{path.name}: xz stream footer not found")
            if zlib.crc32(footer[4:10]) != struct.unpack("<I", footer[0:4])[0]:
                raise SealedFileError("damaged", f"{path.name}: xz stream footer damaged")
            index_size = (struct.unpack("<I", footer[4:8])[0] + 1) * 4
            index_start = end - _XZ_HEADER_SIZE - index_size
            if index_start < _XZ_HEADER_SIZE:
                raise SealedFileError("damaged", f"{path.name}: xz index out of range")
            index = _read_at(raw, index_start, index_size)
            if index[0] != 0 or zlib.crc32(index[:-4]) != struct.unpack("<I", index[-4:])[0]:
                raise SealedFileError("damaged", f"{path.name}: xz index damaged")
            records, pos = _varint(index, 1)
            blocks = 0
            uncompressed = 0
            for _ in range(records):
                unpadded, pos = _varint(index, pos)
                size, pos = _varint(index, pos)
                blocks += (unpadded + 3) & ~3
                uncompressed += size
            start = index_start - blocks - _XZ_HEADER_SIZE
            if start < 0 or _read_at(raw, start, 6) != _XZ_MAGIC:
                raise SealedFileError("damaged", f"{path.name}: xz stream header not found")
            found.append((start, end - start, uncompressed))
            end = start
    finally:
        if own:
            raw.close()
    streams: list[XzStream] = []
    position = 0
    for start, size, uncompressed in reversed(found):
        streams.append(XzStream(start, size, position, uncompressed))
        position += uncompressed
    return streams


def stream_of(streams: list[XzStream], offset: int) -> int:
    """Index of the stream that holds uncompressed ``offset`` (the last one past the end)."""
    for number, stream in enumerate(streams):
        if offset < stream.uncompressed_start + stream.uncompressed_size:
            return number
    return max(0, len(streams) - 1)


def xz_stream_chunks(raw: BinaryIO, stream: XzStream) -> Iterator[bytes]:
    """The uncompressed content of one stream, in pieces of at most :data:`CHUNK` bytes."""
    raw.seek(stream.compressed_start)
    left = stream.compressed_size
    decompressor = lzma.LZMADecompressor(format=lzma.FORMAT_XZ)
    while left > 0:
        data = raw.read(min(CHUNK, left))
        if not data:
            raise SealedFileError("damaged", "xz: file ends early")
        left -= len(data)
        while data:
            out = decompressor.decompress(data, CHUNK)
            data = b""
            if out:
                yield out
            while not decompressor.needs_input and not decompressor.eof:
                out = decompressor.decompress(b"", CHUNK)
                if not out:
                    break
                yield out
    if not decompressor.eof:
        raise SealedFileError("damaged", "xz: stream ends early")


def chunks_from(
    path: Path, offset: int, raw: BinaryIO | None = None
) -> Generator[bytes, None, None]:
    """The uncompressed content of a sealed file from ``offset`` on, in pieces.

    xz starts at the stream that holds ``offset`` and decompresses only what is read from there
    on; gzip decompresses from the start and drops the bytes before ``offset``. Damaged data
    raises :class:`SealedFileError` (``damaged``) or one of :data:`DAMAGE_ERRORS`.
    """
    own = raw is None
    handle = path.open("rb") if raw is None else raw
    try:
        if format_of(path) == "xz":
            streams = xz_streams(path, handle)
            skip = offset
            first = stream_of(streams, offset)
            if streams:
                skip = offset - streams[first].uncompressed_start
            for stream in streams[first:]:
                for data in xz_stream_chunks(handle, stream):
                    if skip >= len(data):
                        skip -= len(data)
                        continue
                    yield data[skip:]
                    skip = 0
            return
        handle.seek(0)
        skip = offset
        with gzip.GzipFile(fileobj=handle, mode="rb") as unzipped:
            for data in iter(lambda: unzipped.read(CHUNK), b""):
                if skip >= len(data):
                    skip -= len(data)
                    continue
                yield data[skip:]
                skip = 0
    finally:
        if own:
            handle.close()
