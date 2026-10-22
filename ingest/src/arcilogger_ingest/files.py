"""Reading spool and vault files without getting in the sealer's way.

The collector appends to ``spool/<source>/<day>.log``; the nightly sealer compresses a closed
day into ``vault/<Y>/<M>/<D>/<source>.log.gz`` and then **deletes** the spool file. Two rules
follow for the ingest service, which tails those files:

* **Never keep a spool file open between polls.** :func:`read_complete` opens the file, reads
  the new bytes and closes it again. A file that is closed cannot block the sealer.
* **On Windows, open with ``FILE_SHARE_DELETE``.** Python's ``open()`` asks only for shared
  read/write access, so while ingest is *inside* a read, ``DeleteFileW`` in the sealer would fail
  with "access denied" and the day would be retried minutes later with a "sealing failed"
  alert. :func:`open_shared` calls ``CreateFileW`` with read, write *and* delete sharing, so the
  sealer can delete the file even during a read (the open handle keeps working until it is
  closed). Elsewhere a plain ``open()`` already allows that.

Only complete lines (``\\n``-terminated) are returned from a spool file: the collector writes a
line with one ``write()``, but a reader can still see a line that is only partly written, and
that line is left for the next poll. The vault copy is final, so its last line counts even
without ``\\n`` (:class:`VaultReader`).

Byte offsets and line numbers of the decompressed vault file are identical to the spool file's
(the sealer compresses byte for byte), so a stream can switch from the spool to the vault at any
complete-line offset.
"""

from __future__ import annotations

import gzip
import os
import sys
import zlib
from pathlib import Path
from typing import BinaryIO, Final

#: Growth step when one line is longer than the requested size; smallest vault read.
MIN_READ: Final = 64 * 1024


class VaultReadError(OSError):
    """A sealed file is shorter than the resume offset or its gzip data is damaged."""


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _GENERIC_READ: Final = 0x80000000
    _FILE_SHARE_READ_WRITE_DELETE: Final = 0x00000001 | 0x00000002 | 0x00000004
    _OPEN_EXISTING: Final = 3
    _FILE_ATTRIBUTE_NORMAL: Final = 0x00000080
    _FILE_FLAG_SEQUENTIAL_SCAN: Final = 0x08000000
    _INVALID_HANDLE_VALUE: Final = ctypes.c_void_p(-1).value

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel32.CreateFileW
    _create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL

    def open_shared(path: Path) -> BinaryIO:
        """Open ``path`` for reading with read, write **and delete** sharing (Windows)."""
        handle = _create_file(
            str(path),
            _GENERIC_READ,
            _FILE_SHARE_READ_WRITE_DELETE,
            None,
            _OPEN_EXISTING,
            _FILE_ATTRIBUTE_NORMAL | _FILE_FLAG_SEQUENTIAL_SCAN,
            None,
        )
        if handle is None or handle == _INVALID_HANDLE_VALUE:
            code = ctypes.get_last_error()
            # OSError maps the Windows error code to FileNotFoundError, PermissionError, …
            raise OSError(None, ctypes.FormatError(code).strip(), str(path), code)
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            _close_handle(handle)
            raise
        return os.fdopen(descriptor, "rb")

else:

    def open_shared(path: Path) -> BinaryIO:
        """Open ``path`` for reading (POSIX: an open file never blocks ``unlink``)."""
        return path.open("rb")


def read_complete(path: Path, offset: int, max_bytes: int) -> bytes:
    """Complete lines of ``path`` from ``offset``: about ``max_bytes``, always ending in ``\\n``.

    Returns ``b""`` when no complete line follows ``offset`` (nothing new, or only a line that is
    still being written). A single line longer than ``max_bytes`` is returned whole. The file is
    closed before returning. Raises ``FileNotFoundError`` when the file is gone.
    """
    want = max(max_bytes, 1)
    with open_shared(path) as handle:
        handle.seek(offset)
        data = handle.read(want)
        end = data.rfind(b"\n")
        while end < 0 and len(data) >= want:  # one very long line: keep reading
            more = handle.read(max(want, MIN_READ))
            if not more:
                break
            end = more.rfind(b"\n")
            if end >= 0:
                end += len(data)
            data += more
    return data[: end + 1] if end >= 0 else b""


class VaultReader:
    """Sequential reader of a sealed ``<source>.log.gz`` from an uncompressed offset.

    Kept open while a stream is read from the vault (decompressing from the start for every poll
    would be quadratic). The vault file is never deleted by the sealer and only by the retention
    job years later; it is opened with delete sharing all the same. :attr:`position` is the
    uncompressed offset of the next byte :meth:`read` returns.
    """

    def __init__(self, path: Path, offset: int) -> None:
        self.path = path
        self._raw = open_shared(path)
        try:
            self._gzip = gzip.GzipFile(fileobj=self._raw, mode="rb")
            self.position = 0
            self._buffer = b""
            self._eof = False
            self._skip(offset)
        except BaseException:
            self._raw.close()
            raise

    def _read_raw(self, size: int) -> bytes:
        try:
            return self._gzip.read(size)
        except (EOFError, zlib.error, gzip.BadGzipFile) as exc:
            raise VaultReadError(f"{self.path.name}: damaged gzip data ({exc})") from exc

    def _skip(self, count: int) -> None:
        remaining = count
        while remaining > 0:
            chunk = self._read_raw(min(remaining, 1024 * 1024))
            if not chunk:
                raise VaultReadError(
                    f"{self.path.name}: has {self.position} bytes, resume offset is {count}"
                )
            remaining -= len(chunk)
            self.position += len(chunk)

    def read(self, max_bytes: int) -> bytes:
        """Complete lines (about ``max_bytes``); at the end also the last line without ``\\n``.

        Returns ``b""`` once the whole file was returned.
        """
        want = max(max_bytes, MIN_READ)
        data = self._buffer
        while not self._eof and (len(data) < want or data.rfind(b"\n") < 0):
            more = self._read_raw(want)
            if not more:
                self._eof = True
            data += more
        if self._eof:
            complete, self._buffer = data, b""
        else:
            end = data.rfind(b"\n")
            complete, self._buffer = data[: end + 1], data[end + 1 :]
        self.position += len(complete)
        return complete

    def unread(self, data: bytes) -> None:
        """Give back the end of what :meth:`read` returned (the caller took fewer lines)."""
        if not data:
            return
        self._buffer = data + self._buffer
        self.position -= len(data)

    def close(self) -> None:
        self._gzip.close()
        self._raw.close()
