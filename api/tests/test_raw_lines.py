"""Raw-line reading (``arcilogger_api.rawlines``) without the database: the streaming line finder
against a naive split for any chunking, hashes and line counts, gzip members, display text, the
checkpoint indexes of spool files and of sealed files (correct for any lookup order, growing and
replaced files), and files that exist but cannot be read (access denied is never "missing")."""

from __future__ import annotations

import gzip
import hashlib
import itertools
import tempfile
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_api import rawlines
from arcilogger_api.rawlines import (
    LineFinder,
    RawReferenceError,
    display_text,
    gzip_chunks,
    locate,
    read_line,
    regular_file,
    scan_file,
    valid_source_name,
)
from arcilogger_core.layout import Layout
from arcilogger_ingest.files import open_shared

DAY = date(2026, 10, 12)
LINE = st.binary(max_size=40).map(lambda b: b.replace(b"\n", b"#"))


def _expected(lines: list[bytes], wanted: list[int]) -> dict[int, tuple[int, bytes]]:
    offsets, position = [], 0
    for line in lines:
        offsets.append(position)
        position += len(line) + 1
    return {n: (offsets[n - 1], lines[n - 1]) for n in wanted if 1 <= n <= len(lines)}


@settings(max_examples=300, deadline=None)
@given(
    lines=st.lists(LINE, max_size=40),
    tail=st.binary(max_size=10).map(lambda b: b.replace(b"\n", b"")),
    wanted=st.lists(st.integers(min_value=1, max_value=45), max_size=6),
    chunk=st.integers(min_value=1, max_value=64),
)
def test_line_finder_matches_a_naive_split(
    lines: list[bytes], tail: bytes, wanted: list[int], chunk: int
) -> None:
    data = b"".join(line + b"\n" for line in lines) + tail  # tail: a line still being written
    finder = LineFinder(wanted)
    for start in range(0, len(data), chunk):
        finder.feed(data[start : start + chunk])
    assert finder.found == _expected(lines, wanted)
    assert finder.lines == len(lines)
    assert finder.done == all(n <= len(lines) for n in wanted)


def test_scan_file_hashes_counts_and_stops_early(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rawlines, "CHUNK", 7)
    lines = [f"line {n} ğüşıöç".encode() for n in range(1, 30)]
    data = b"".join(line + b"\n" for line in lines)
    plain = tmp_path / "x.log"
    plain.write_bytes(data + b"partial")
    scan = scan_file("spool", plain, [3, 29], hash_file=True)
    assert scan.found == _expected(lines, [3, 29])
    assert (scan.sha256, scan.size, scan.lines) == (
        hashlib.sha256(data + b"partial").hexdigest(),
        len(data) + 7,
        29,
    )
    quick = scan_file("spool", plain, [2])
    assert quick.found == _expected(lines, [2])
    assert (quick.sha256, quick.size, quick.lines) == (None, None, None)

    packed = tmp_path / "x.log.gz"
    compressed = gzip.compress(data[:50]) + gzip.compress(data[50:])  # two members
    packed.write_bytes(compressed)
    scan = scan_file("vault", packed, [1, 28], hash_file=True)
    assert scan.found == _expected(lines, [1, 28])
    assert (scan.sha256, scan.size, scan.error) == (
        hashlib.sha256(compressed).hexdigest(),
        len(compressed),
        None,
    )


def test_gzip_chunks_handles_members_and_reports_damage() -> None:
    joined = gzip.compress(b"a\n") + gzip.compress(b"b\n") + gzip.compress(b"")
    assert b"".join(gzip_chunks([joined[:5], joined[5:]])) == b"a\nb\n"
    with pytest.raises(Exception, match=r"(?i)invalid|incorrect|error"):
        list(gzip_chunks([b"\x1f\x8b\x08\x00" + b"\xff" * 40]))


def test_damaged_gzip_is_reported_with_its_hash(tmp_path: Path) -> None:
    damaged = tmp_path / "d.log.gz"
    data = bytearray(gzip.compress(b"".join(b"line %d\n" % n for n in range(2000))))
    data[len(data) // 2] ^= 0xFF
    damaged.write_bytes(bytes(data))
    scan = scan_file("vault", damaged, [1999], hash_file=True)
    assert scan.error is not None
    assert scan.error.startswith("d.log.gz: damaged gzip data")
    assert scan.sha256 == hashlib.sha256(bytes(data)).hexdigest()  # still hashed completely
    assert 1999 not in scan.found


def test_read_line_and_locate(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    spool = layout.spool_file("fortigate", DAY)
    spool.parent.mkdir(parents=True)
    spool.write_bytes(b"first\nsecond\n")
    found = read_line(layout, "fortigate", DAY, 2, 6)
    assert (found.data, found.offset, found.origin, found.file) == (
        b"second",
        6,
        "spool",
        "spool/fortigate/2026-10-12.log",
    )
    assert found.record() is None  # not in spool format
    assert found.text == "second"
    for line, offset, code in (
        (0, None, "raw_not_found"),
        (3, None, "raw_not_found"),
        (2, 5, "raw_reference_mismatch"),
    ):
        with pytest.raises(RawReferenceError) as raised:
            read_line(layout, "fortigate", DAY, line, offset)
        assert raised.value.code == code
    with pytest.raises(RawReferenceError) as raised:
        read_line(layout, "fortigate", date(2026, 10, 11), 1)
    assert raised.value.code == "raw_not_found"
    with pytest.raises(RawReferenceError) as raised:
        locate(layout, "../x", DAY)
    assert raised.value.code == "raw_source_invalid"
    assert locate(layout, "core-r1", DAY) is None


def test_overlong_lines_are_not_raw_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rawlines, "MAX_LINE_BYTES", 100)
    path = tmp_path / "x.log"
    path.write_bytes(b"short\n" + b"x" * 500 + b"\n")
    with pytest.raises(RawReferenceError, match="longer"):
        scan_file("spool", path, [2])


@pytest.mark.parametrize(
    ("name", "valid"),
    [
        ("fortigate", True),
        ("core-r1", True),
        ("ap-172.20.10.211", True),
        ("unknown-10.9.9.9", True),
        ("_audit", False),
        ("..", False),
        (".x", False),
        ("a/b", False),
        ("a\\b", False),
        ("Fortigate", False),
        ("", False),
        ("a" * 82, False),
    ],
)
def test_valid_source_names(name: str, valid: bool) -> None:
    assert valid_source_name(name) is valid


def test_display_text() -> None:
    assert display_text("çağrı #012 ok".encode()) == "çağrı #012 ok"
    assert display_text(b"bad \xff\xfe") == "bad \\xff\\xfe"
    assert display_text("a\u202eb\u200bc\u0085d".encode()) == "a\\u202eb\\u200bc\\u0085d"
    assert display_text("x\u2028y".encode()) == "x\\u2028y"


# -- spool checkpoint index -----------------------------------------------------------------------


def _spool(layout: Layout, lines: list[bytes], *, append: bool = False) -> Path:
    path = layout.spool_file("fortigate", DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(line + b"\n" for line in lines)
    if append:
        with path.open("ab") as handle:
            handle.write(data)
    else:
        path.write_bytes(data)
    return path


@settings(max_examples=150, deadline=None)
@given(
    lines=st.lists(LINE, min_size=1, max_size=60),
    reads=st.lists(st.integers(min_value=1, max_value=65), min_size=1, max_size=8),
    chunk=st.integers(min_value=1, max_value=32),
    every=st.integers(min_value=1, max_value=48),
)
def test_spool_lookups_with_checkpoints_match_a_naive_split(
    lines: list[bytes], reads: list[int], chunk: int, every: int
) -> None:
    with tempfile.TemporaryDirectory() as folder, pytest.MonkeyPatch.context() as patch:
        patch.setattr(rawlines, "CHUNK", chunk)
        patch.setattr(rawlines, "CHECKPOINT_BYTES", every)
        patch.setattr(rawlines, "SPOOL_INDEX", rawlines.SpoolIndex())
        layout = Layout(Path(folder))
        _spool(layout, lines)
        expected = _expected(lines, list(range(1, len(lines) + 1)))
        for number in reads:
            if number > len(lines):
                with pytest.raises(RawReferenceError):
                    read_line(layout, "fortigate", DAY, number)
                continue
            found = read_line(layout, "fortigate", DAY, number, expected[number][0])
            assert (found.offset, found.data) == expected[number]


def test_checkpoints_shorten_later_lookups_and_follow_the_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rawlines, "CHUNK", 64)
    monkeypatch.setattr(rawlines, "CHECKPOINT_BYTES", 256)
    index = rawlines.SpoolIndex(files=2)
    monkeypatch.setattr(rawlines, "SPOOL_INDEX", index)
    layout = Layout(tmp_path)
    lines = [b"line %05d" % n for n in range(1, 401)]
    path = _spool(layout, lines)
    assert read_line(layout, "fortigate", DAY, 400).data == b"line 00400"
    stat = path.stat()
    start = index.start(path, (stat.st_dev, stat.st_ino), stat.st_size, 350)
    assert start[1] > 300  # the next lookup starts close to line 350, not at the file start
    assert path.read_bytes()[start[0] :].startswith(b"line %05d" % start[1])

    read: list[int] = []
    real_chunks = rawlines._plain_chunks

    def counting(handle: Any) -> Iterator[bytes]:
        for chunk in real_chunks(handle):
            read.append(len(chunk))
            yield chunk

    monkeypatch.setattr(rawlines, "_plain_chunks", counting)
    assert read_line(layout, "fortigate", DAY, 399).data == b"line 00399"
    assert sum(read) <= 2 * 256  # not the 4,400 bytes before line 399

    # the file grows: new lines are found, old checkpoints stay valid
    _spool(layout, [b"later 1", b"later 2"], append=True)
    assert read_line(layout, "fortigate", DAY, 402).data == b"later 2"
    # the file is replaced (another inode): a new index
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"other\ncontent\n")
    replacement.replace(path)
    assert read_line(layout, "fortigate", DAY, 2).data == b"content"
    with pytest.raises(RawReferenceError):
        read_line(layout, "fortigate", DAY, 300)
    # least recently used files are dropped
    for source in ("core-r1", "switch-1"):
        other = layout.spool_file(source, DAY)
        other.parent.mkdir(parents=True)
        other.write_bytes(b"x\n")
        read_line(layout, source, DAY, 1)
    assert len(index) == 2
    index.clear()
    assert len(index) == 0


def test_checkpoints_of_a_file_that_shrank_are_dropped(tmp_path: Path) -> None:
    index = rawlines.SpoolIndex()
    path = tmp_path / "x.log"
    identity = (1, 2)
    assert index.start(path, identity, 10_000_000, 5) == (0, 1)
    index.record(path, identity, [(5_000_000, 1000), (6_000_000, 1100), (9_500_000, 1800)])
    assert index.start(path, identity, 10_000_000, 1500) == (5_000_000, 1000)  # 6 MB: too close
    index.record(path, (9, 9), [(9_900_000, 1900)])  # another file: ignored
    assert index.start(path, identity, 10_000_000, 2000) == (9_500_000, 1800)
    assert index.start(path, identity, 100, 2000) == (0, 1)  # shrank: start over


# -- vault checkpoint index -----------------------------------------------------------------------


def _vault(layout: Layout, lines: list[bytes], members: int = 1, source: str = "fortigate") -> Path:
    """The sealed file of ``lines`` as ``members`` gzip members (the sealer writes one; the
    reader must accept several)."""
    path = layout.vault_day(DAY) / f"{source}.log.gz"
    path.parent.mkdir(parents=True, exist_ok=True)
    data = b"".join(line + b"\n" for line in lines)
    cut = [len(data) * n // members for n in range(members + 1)]
    path.write_bytes(b"".join(gzip.compress(data[a:b]) for a, b in itertools.pairwise(cut)))
    return path


@settings(max_examples=150, deadline=None)
@given(
    lines=st.lists(LINE, min_size=1, max_size=60),
    members=st.integers(min_value=1, max_value=3),
    reads=st.lists(st.integers(min_value=1, max_value=65), min_size=1, max_size=8),
    chunk=st.integers(min_value=1, max_value=32),
    every=st.integers(min_value=1, max_value=48),
    tail=st.integers(min_value=0, max_value=50),
)
def test_vault_lookups_with_checkpoints_match_a_naive_split(
    lines: list[bytes], members: int, reads: list[int], chunk: int, every: int, tail: int
) -> None:
    with tempfile.TemporaryDirectory() as folder, pytest.MonkeyPatch.context() as patch:
        patch.setattr(rawlines, "CHUNK", chunk)
        patch.setattr(rawlines, "VAULT_CHECKPOINT_BYTES", every)
        patch.setattr(rawlines, "CHECKPOINT_TAIL_BYTES", tail)
        patch.setattr(rawlines, "VAULT_INDEX", rawlines.VaultIndex())
        layout = Layout(Path(folder))
        _vault(layout, lines, members)
        expected = _expected(lines, list(range(1, len(lines) + 1)))
        for number in reads:
            if number > len(lines):
                with pytest.raises(RawReferenceError):
                    read_line(layout, "fortigate", DAY, number)
                continue
            found = read_line(layout, "fortigate", DAY, number, expected[number][0])
            assert (found.offset, found.data, found.origin) == (*expected[number], "vault")


def test_vault_checkpoints_shorten_later_lookups(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The first view of a late line decompresses the file up to it and leaves checkpoints;
    a later view resumes at the nearest one. A replaced file starts a new index, damage after a
    checkpoint is still reported, least recently used files are dropped."""
    monkeypatch.setattr(rawlines, "CHUNK", 64)
    monkeypatch.setattr(rawlines, "VAULT_CHECKPOINT_BYTES", 512)
    index = rawlines.VaultIndex(files=2)
    monkeypatch.setattr(rawlines, "VAULT_INDEX", index)
    layout = Layout(tmp_path)
    lines = [
        b"line %05d %s" % (n, hashlib.sha256(b"%d" % n).hexdigest().encode()) for n in range(1, 801)
    ]
    path = _vault(layout, lines, members=2)
    assert read_line(layout, "fortigate", DAY, 800).data == lines[799]
    stat = path.stat()
    entry = index.entry(path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    assert len(entry.points) > 10
    point = entry.floor(790)
    assert point is not None
    assert 700 < point.line <= 790

    read: list[int] = []
    real_chunks = rawlines._plain_chunks

    def counting(handle: Any) -> Iterator[bytes]:
        for chunk in real_chunks(handle):
            read.append(len(chunk))
            yield chunk

    monkeypatch.setattr(rawlines, "_plain_chunks", counting)
    assert read_line(layout, "fortigate", DAY, 790).data == lines[789]
    assert sum(read) < path.stat().st_size // 4  # resumed near the end, not at the start
    read.clear()
    assert read_line(layout, "fortigate", DAY, 3).data == lines[2]
    assert sum(read) <= 3 * 64  # early lines: no checkpoint needed, reading stops early

    # damage after the checkpoints: lines before it still read, the line after it is refused
    data = bytearray(path.read_bytes())
    data[-40] ^= 0xFF
    damaged = tmp_path / "damaged.gz"
    damaged.write_bytes(bytes(data))
    damaged.replace(path)  # another file: a new index
    with pytest.raises(RawReferenceError) as raised:
        read_line(layout, "fortigate", DAY, 800)
    assert raised.value.code == "raw_unreadable"
    assert read_line(layout, "fortigate", DAY, 10).data == lines[9]

    # the kept start of a line is bounded: no checkpoint inside lines longer than that
    monkeypatch.setattr(rawlines, "_plain_chunks", real_chunks)
    monkeypatch.setattr(rawlines, "CHECKPOINT_TAIL_BYTES", 8)
    long_lines = [b"%04d " % n + b"y" * 100 for n in range(1, 201)]
    long_path = _vault(layout, long_lines, source="long")
    assert read_line(layout, "long", DAY, 200).data == long_lines[199]
    stat = long_path.stat()
    long_entry = index.entry(long_path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
    assert long_entry.points
    assert all(len(point.tail) <= 8 for point in long_entry.points)
    assert read_line(layout, "long", DAY, 150).data == long_lines[149]

    for source in ("core-r1", "switch-1"):
        _vault(layout, [b"x"], source=source)
        read_line(layout, source, DAY, 1)
    assert len(index) == 2
    index.clear()
    assert len(index) == 0


# -- files that exist but cannot be read ----------------------------------------------------------


def _deny(monkeypatch: pytest.MonkeyPatch, folder: Path, error: OSError) -> None:
    """``stat`` and ``open`` below ``folder`` fail as Windows fails them for a service account
    without rights on the folder."""
    real_stat, real_open = Path.stat, Path.open

    def stat(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.is_relative_to(folder):
            raise error
        return real_stat(self, *args, **kwargs)

    def open_(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self.is_relative_to(folder):
            raise error
        return real_open(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", stat)
    monkeypatch.setattr(Path, "open", open_)


def test_access_denied_is_not_reported_as_missing_or_damaged(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = Layout(tmp_path)
    spool = _spool(layout, [b"one", b"two"])
    _deny(monkeypatch, layout.spool, PermissionError(13, "Access is denied", str(spool)))
    for call in (
        lambda: locate(layout, "fortigate", DAY),
        lambda: read_line(layout, "fortigate", DAY, 2),
    ):
        with pytest.raises(RawReferenceError) as raised:
            call()
        assert raised.value.code == "raw_access_denied"
        assert "access denied" in str(raised.value)
    # a sealed day is still read from the vault although the spool folder is closed
    _vault(layout, [b"one", b"two"])
    assert read_line(layout, "fortigate", DAY, 2).origin == "vault"
    with pytest.raises(RawReferenceError) as raised:
        read_line(layout, "fortigate", DAY, 3)
    assert raised.value.code == "raw_not_found"


def test_other_read_errors_are_io_errors(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    layout = Layout(tmp_path)
    _vault(layout, [b"one"])
    _deny(monkeypatch, layout.vault, OSError(5, "Input/output error"))
    with pytest.raises(RawReferenceError) as raised:
        read_line(layout, "fortigate", DAY, 1)
    assert (raised.value.code, str(raised.value)) == (
        "raw_io_error",
        "fortigate.log.gz: Input/output error",
    )
    assert regular_file(tmp_path / "nothing" / "here") is False


# -- never in the sealer's way ---------------------------------------------------------------------


def test_raw_files_are_opened_with_delete_sharing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Like ingest (``arcilogger_ingest.files.open_shared``: Windows ``FILE_SHARE_DELETE``), so
    the Seal task can delete a spool file while a raw view or a report reads it."""
    opened: list[Path] = []
    real = open_shared

    def recording(path: Path) -> Any:
        opened.append(path)
        return real(path)

    monkeypatch.setattr(rawlines, "open_shared", recording)
    layout = Layout(tmp_path)
    spool = _spool(layout, [b"one", b"two"])
    scan_file("spool", spool, [1], hash_file=True)
    assert read_line(layout, "fortigate", DAY, 2).origin == "spool"
    spool.unlink()
    vault = _vault(layout, [b"one", b"two"])
    assert read_line(layout, "fortigate", DAY, 2).origin == "vault"
    assert scan_file("vault", vault, [1]).found[1] == (0, b"one")
    assert opened == [spool, spool, vault, vault]


def test_the_sealer_can_delete_a_spool_file_while_it_is_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The spool file is deleted after the first chunk (the sealer finishing the day): the delete
    succeeds (on Windows only thanks to delete sharing — CI job python-windows) and the reader
    still returns the bytes it opened."""
    monkeypatch.setattr(rawlines, "CHUNK", 8)
    layout = Layout(tmp_path)
    lines = [f"line {n}".encode() for n in range(1, 20)]
    spool = _spool(layout, lines)
    original = spool.read_bytes()
    real_chunks = rawlines._plain_chunks

    def chunks(handle: Any) -> Iterator[bytes]:
        for index, chunk in enumerate(real_chunks(handle)):
            if index == 0:
                spool.unlink()  # Sealer._finish
            yield chunk

    monkeypatch.setattr(rawlines, "_plain_chunks", chunks)
    scan = scan_file("spool", spool, [19], hash_file=True)
    assert not spool.exists()
    assert scan.sha256 == hashlib.sha256(original).hexdigest()
    assert scan.found[19][1] == b"line 19"


def test_a_spool_file_sealed_just_before_it_is_opened_is_read_from_the_vault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Found in the spool, gone when opened (the sealer moved the day): read from the vault
    instead of answering 503 ``raw_io_error``."""
    layout = Layout(tmp_path)
    spool = _spool(layout, [b"one", b"two"])
    real = rawlines._find_in_spool

    def sealed_meanwhile(path: Path, line: int) -> Any:
        _vault(layout, [b"one", b"two"])
        spool.unlink()
        return real(path, line)

    monkeypatch.setattr(rawlines, "_find_in_spool", sealed_meanwhile)
    found = read_line(layout, "fortigate", DAY, 2)
    assert (found.origin, found.data, found.file) == (
        "vault",
        b"two",
        "vault/2026/10/12/fortigate.log.gz",
    )
