"""Durable file writes.

Evidence files are written to a temporary name, flushed and fsync'ed, then atomically renamed.
On POSIX the directory is fsync'ed as well so the rename survives a power cut; Windows has no
directory fsync (NTFS journals metadata), so that step is skipped there.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path


def fsync_file(path: Path) -> None:
    """Flush a file's data to stable storage."""
    with path.open("rb+") as handle:
        os.fsync(handle.fileno())


def fsync_dir(path: Path) -> None:
    """Flush a directory entry (POSIX only; no-op on Windows)."""
    if sys.platform != "win32":
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` atomically and durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    temp.replace(path)
    fsync_dir(path.parent)


def atomic_write_text(path: Path, text: str) -> None:
    """UTF-8 text variant of :func:`atomic_write_bytes` (LF line endings, never CRLF)."""
    atomic_write_bytes(path, text.encode("utf-8"))
