"""Reading spool and vault files: complete lines only, exact offsets, files never kept open."""

from __future__ import annotations

import gzip
import sys
from pathlib import Path

import pytest

from arcilogger_ingest.files import (
    MIN_READ,
    VaultReader,
    VaultReadError,
    open_shared,
    read_complete,
)

LINES = [b"first line\n", b"second\n", b"third line is longer\n"]


def spool(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "2026-10-12.log"
    path.write_bytes(data)
    return path


def vault(tmp_path: Path, data: bytes) -> Path:
    path = tmp_path / "fortigate.log.gz"
    with path.open("wb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as gz:
        gz.write(data)
    return path


def test_read_complete_returns_only_complete_lines(tmp_path: Path) -> None:
    path = spool(tmp_path, b"".join(LINES) + b"partial without newl")
    assert read_complete(path, 0, 1024) == b"".join(LINES)
    # resume exactly at a line start
    start = len(LINES[0])
    assert read_complete(path, start, 1024) == b"".join(LINES[1:])
    # only the line being written follows: nothing to return yet
    assert read_complete(path, len(b"".join(LINES)), 1024) == b""
    # at the very end
    assert read_complete(path, path.stat().st_size, 1024) == b""


def test_read_complete_stops_at_the_last_newline_within_the_limit(tmp_path: Path) -> None:
    line = b"x" * 100 + b"\n"
    path = spool(tmp_path, line * 2000)  # 202,000 bytes
    data = read_complete(path, 0, MIN_READ)
    assert data.endswith(b"\n")
    assert len(data) == (MIN_READ // len(line)) * len(line)
    rest = read_complete(path, len(data), 10**9)
    assert data + rest == line * 2000


def test_a_line_longer_than_the_limit_is_returned_whole(tmp_path: Path) -> None:
    long_line = b"y" * (3 * MIN_READ + 17) + b"\n"
    path = spool(tmp_path, long_line + b"next\n")
    # the read that finds the long line's end also holds the next line: both are complete
    assert read_complete(path, 0, 10) == long_line + b"next\n"
    # and an over-long line still being written is not returned
    path.write_bytes(b"z" * (2 * MIN_READ + 5))
    assert read_complete(path, 0, 10) == b""


def test_read_complete_on_a_missing_file_raises_file_not_found(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        read_complete(tmp_path / "gone.log", 0, 1024)


@pytest.mark.skipif(not Path("/proc/self/fd").is_dir(), reason="needs /proc (Linux)")
def test_the_spool_file_is_closed_after_every_read(tmp_path: Path) -> None:
    """Never keep a spool file open between polls (the sealer deletes it on Windows)."""
    path = spool(tmp_path, b"".join(LINES))

    def open_paths() -> set[str]:
        found = set()
        for fd in Path("/proc/self/fd").iterdir():
            try:
                found.add(str(fd.readlink()))
            except OSError:
                continue
        return found

    read_complete(path, 0, 1024)
    assert str(path) not in open_paths()


def test_a_shared_handle_does_not_prevent_deleting_the_file(tmp_path: Path) -> None:
    """The sealer can delete a spool file while ingest is in the middle of reading it.

    On Windows this needs FILE_SHARE_DELETE (``open_shared``); a plain ``open()`` would make
    ``unlink`` fail with PermissionError there. POSIX allows it anyway.
    """
    path = spool(tmp_path, b"".join(LINES))
    with open_shared(path) as handle:
        assert handle.read(6) == b"first "
        path.unlink()
        assert not path.exists()
        assert handle.read() == b"".join(LINES)[6:]  # the open handle keeps working


@pytest.mark.skipif(sys.platform != "win32", reason="Windows sharing semantics")
def test_windows_plain_open_would_block_the_sealer(tmp_path: Path) -> None:
    """Documents why open_shared exists: Python's open() does not share delete access."""
    path = spool(tmp_path, b"".join(LINES))
    with path.open("rb"), pytest.raises(PermissionError):
        path.unlink()


def test_open_shared_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        open_shared(tmp_path / "missing.log")


def test_vault_reader_resumes_at_an_uncompressed_offset(tmp_path: Path) -> None:
    data = b"".join(LINES)
    path = vault(tmp_path, data)
    reader = VaultReader(path, len(LINES[0]))
    try:
        assert reader.position == len(LINES[0])
        assert reader.read(1) == b"".join(LINES[1:])
        assert reader.position == len(data)
        assert reader.read(1) == b""
    finally:
        reader.close()


def test_vault_reader_returns_the_final_line_without_newline(tmp_path: Path) -> None:
    """A sealed file is final: its last line counts even without '\\n' (cut by a power loss)."""
    path = vault(tmp_path, b"".join(LINES) + b"cut off")
    reader = VaultReader(path, 0)
    try:
        assert reader.read(10**6) == b"".join(LINES) + b"cut off"
        assert reader.read(10**6) == b""
    finally:
        reader.close()


def test_vault_reader_reads_in_chunks_of_complete_lines(tmp_path: Path) -> None:
    line = b"0123456789" * 30 + b"\n"
    data = line * 1000
    path = vault(tmp_path, data)
    reader = VaultReader(path, 0)
    parts = []
    try:
        while chunk := reader.read(MIN_READ):
            assert chunk.endswith(b"\n")
            assert len(chunk) <= 2 * MIN_READ
            parts.append(chunk)
    finally:
        reader.close()
    assert b"".join(parts) == data
    assert len(parts) > 2


def test_vault_reader_refuses_an_offset_beyond_the_file(tmp_path: Path) -> None:
    path = vault(tmp_path, b"".join(LINES))
    with pytest.raises(VaultReadError, match="resume offset"):
        VaultReader(path, 10_000)


def test_vault_reader_reports_damaged_gzip_data(tmp_path: Path) -> None:
    path = vault(tmp_path, b"".join(LINES) * 200)
    damaged = path.read_bytes()
    path.write_bytes(damaged[: len(damaged) // 2])  # truncated: no end-of-stream marker
    reader = VaultReader(path, 0)

    def read_all() -> None:
        while reader.read(MIN_READ):
            pass

    try:
        with pytest.raises(VaultReadError, match="damaged"):
            read_all()
    finally:
        reader.close()
