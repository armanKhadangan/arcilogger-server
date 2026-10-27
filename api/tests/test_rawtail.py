"""The live tail's reader (``arcilogger_api.rawtail``) without the database: the end of a file
against a naive split for any anchor, window and block size, unterminated last lines, empty
files and one line of 1 MiB; line numbers and offsets equal what ``rawlines.read_line`` finds;
the follower's new lines, its jump over a file far ahead, a deleted and a replaced file."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_api import rawlines, rawtail
from arcilogger_api.rawlines import read_line
from arcilogger_api.rawtail import Follower, line_anchor, read_tail, tail_lines
from arcilogger_core.layout import Layout

DAY = date(2026, 10, 12)
FOLDER = "fortigate"
LINE = st.binary(max_size=30).map(lambda b: b.replace(b"\n", b"#"))


def _write(layout: Layout, data: bytes, folder: str = FOLDER) -> Path:
    path = layout.spool_file(folder, DAY)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def _naive(data: bytes, n: int, max_bytes: int) -> list[tuple[int, int, bytes]]:
    """``(line, offset, bytes)`` of the last ``n`` complete lines starting in the window."""
    window = max(0, len(data) - max_bytes)
    found, offset = [], 0
    for number, raw in enumerate(data.split(b"\n")[:-1], start=1):
        if offset >= window:
            found.append((number, offset, raw))
        offset += len(raw) + 1
    return found[-n:] if n else []


def _anchors(data: bytes) -> list[tuple[int, int]]:
    """Every true anchor of ``data``: each line start and the complete lines before it."""
    points, offset = [(0, 0)], 0
    for number, raw in enumerate(data.split(b"\n")[:-1], start=1):
        offset += len(raw) + 1
        points.append((offset, number))
    return points


@settings(max_examples=300, deadline=None)
@given(
    lines=st.lists(LINE, max_size=40),
    tail=st.binary(max_size=8).map(lambda b: b.replace(b"\n", b"")),
    n=st.integers(min_value=0, max_value=45),
    max_bytes=st.integers(min_value=1, max_value=600),
    block=st.integers(min_value=1, max_value=64),
    pick=st.integers(min_value=0, max_value=1000),
)
def test_read_tail_matches_a_naive_split(
    lines: list[bytes], tail: bytes, n: int, max_bytes: int, block: int, pick: int
) -> None:
    data = b"".join(line + b"\n" for line in lines) + tail  # tail: a line still being written
    with tempfile.TemporaryDirectory() as folder:
        layout = Layout(Path(folder))
        _write(layout, data)
        rawlines.SPOOL_INDEX.clear()
        anchors = _anchors(data)
        anchor = anchors[pick % len(anchors)]
        found = read_tail(layout, FOLDER, DAY, n, anchor, max_bytes=max_bytes, block=block)
        expected = _naive(data, n, max_bytes)
        assert [(line.line, line.offset, line.data) for line in found.lines] == expected
        complete = len(data.split(b"\n")) - 1
        assert (found.end_offset, found.end_line) == (len(data) - len(tail), complete)
        for line in found.lines:
            assert (line.source, line.day, line.origin) == (FOLDER, DAY, "spool")
            assert line.file == f"spool/{FOLDER}/2026-10-12.log"


@settings(max_examples=100, deadline=None)
@given(
    lines=st.lists(LINE, min_size=1, max_size=30),
    n=st.integers(min_value=1, max_value=10),
    offset_shift=st.integers(min_value=1, max_value=5),
)
def test_a_wrong_anchor_is_not_trusted(lines: list[bytes], n: int, offset_shift: int) -> None:
    """An anchor that is no line start (a replaced file, a stale resume point) or lies beyond
    the end of the file is ignored: the lines are counted from the start instead."""
    data = b"".join(line + b"\n" for line in lines)
    with tempfile.TemporaryDirectory() as folder:
        layout = Layout(Path(folder))
        _write(layout, data)
        rawlines.SPOOL_INDEX.clear()
        for anchor in ((len(data) + offset_shift, 7), (1, 99)):
            if anchor[0] <= len(data) and data[anchor[0] - 1 : anchor[0]] == b"\n":
                continue  # by chance a real line start
            found = read_tail(layout, FOLDER, DAY, n, anchor)
            assert [(line.line, line.offset) for line in found.lines] == [
                (number, offset) for number, offset, _ in _naive(data, n, len(data) + 1)
            ]


def test_line_numbers_equal_the_raw_line_view(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    data = b"".join(
        b"2026-10-12T10:00:00.000000+03:00 198.51.100.7 satir %d\n" % i for i in range(500)
    )
    _write(layout, data + b"2026-10-12T10:00:01.000000+03:00 198.51.100.7 yari")
    for anchor in ((0, 0), (len(data), 500), _anchors(data)[250]):
        found = tail_lines(layout, FOLDER, DAY, 20, anchor)
        assert len(found) == 20
        for line in found:
            viewed = read_line(layout, FOLDER, DAY, line.line, line.offset)
            assert (viewed.data, viewed.sha256) == (line.data, line.sha256)
    assert found[-1].line == 500


def test_empty_files_and_files_without_a_complete_line(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    _write(layout, b"")
    found = read_tail(layout, FOLDER, DAY, 10)
    assert (found.lines, found.end_offset, found.end_line) == ([], 0, 0)
    _write(layout, b"half a line")
    found = read_tail(layout, FOLDER, DAY, 10)
    assert (found.lines, found.end_offset, found.end_line) == ([], 0, 0)
    with pytest.raises(FileNotFoundError):
        read_tail(layout, "missing", DAY, 10)


def test_a_single_line_of_one_mebibyte(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    big = b"x" * (1024 * 1024)
    _write(layout, b"first\n" + big + b"\n")
    found = tail_lines(layout, FOLDER, DAY, 5, max_bytes=len(big) + 1)
    assert [(line.line, line.offset, len(line.data)) for line in found] == [(2, 6, len(big))]
    # a window one byte shorter cuts the line's start off: it is not returned at all
    assert tail_lines(layout, FOLDER, DAY, 5, max_bytes=len(big)) == []
    beyond = read_tail(layout, FOLDER, DAY, 5, max_bytes=1024)
    assert (beyond.lines, beyond.end_offset, beyond.end_line) == ([], len(big) + 7, 2)


def test_counting_leaves_checkpoints_for_the_next_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rawtail, "CHECKPOINT_BYTES", 64)
    monkeypatch.setattr(rawlines, "CHECKPOINT_BYTES", 64)
    monkeypatch.setattr(rawtail, "COUNT_CHUNK", 50)
    rawlines.SPOOL_INDEX.clear()
    layout = Layout(tmp_path)
    data = b"".join(b"line %04d\n" % i for i in range(300))  # 10 bytes a line
    path = _write(layout, data)
    found = tail_lines(layout, FOLDER, DAY, 3, (0, 0), max_bytes=40)
    assert [line.line for line in found] == [298, 299, 300]
    stat = path.stat()
    offset, lines = rawlines.spool_anchor(path, (stat.st_dev, stat.st_ino), stat.st_size)
    assert offset > 0
    assert data[:offset].count(b"\n") == lines
    assert line_anchor(None, layout, FOLDER, DAY) == (offset, lines)
    assert line_anchor(None, layout, "missing", DAY) == (0, 0)
    rawlines.SPOOL_INDEX.clear()


def test_the_follower_reads_appended_complete_lines(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    path = _write(layout, b"a\nb\n")
    found = read_tail(layout, FOLDER, DAY, 10)
    follower = Follower(path, found.end_offset, found.end_line, identity=found.identity)
    assert follower.poll() == []
    with path.open("ab") as handle:
        handle.write(b"c\nd\nhalf")
    new = follower.poll()
    assert [(line.line, line.offset, line.data) for line in new] == [(3, 4, b"c"), (4, 6, b"d")]
    assert (new[0].source, new[0].day, new[0].file) == (
        FOLDER,
        DAY,
        "spool/fortigate/2026-10-12.log",
    )
    with path.open("ab") as handle:
        handle.write(b" done\n")
    assert [(line.line, line.data) for line in follower.poll()] == [(5, b"half done")]
    for line in new:
        assert read_line(layout, FOLDER, DAY, line.line, line.offset).data == line.data
    path.unlink()  # the sealer deleted the file
    assert follower.poll() == []
    assert follower.ended == "gone"


def test_the_follower_reads_at_most_its_share_per_poll(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    path = _write(layout, b"")
    follower = Follower(path, 0, 0)
    with path.open("ab") as handle:
        handle.write(b"".join(b"%05d\n" % i for i in range(1000)))
    first = follower.poll(max_bytes=600)
    assert 90 <= len(first) <= 100
    assert first[-1].line == len(first)
    rest: list[rawlines.RawLine] = []
    while chunk := follower.poll(max_bytes=600):
        rest.extend(chunk)
    assert [line.line for line in first + rest] == list(range(1, 1001))


def test_a_file_far_ahead_is_skipped_with_its_lines_counted(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    path = _write(layout, b"old\n")
    follower = Follower(path, 4, 1)
    with path.open("ab") as handle:
        handle.write(b"".join(b"%05d\n" % i for i in range(2000)) + b"half")
    assert follower.poll(skip_beyond=1000) == []
    assert (follower.skipped, follower.line, follower.offset) == (2000, 2001, 4 + 2000 * 6)
    with path.open("ab") as handle:
        handle.write(b"\nnext\n")
    assert [(line.line, line.data) for line in follower.poll(skip_beyond=1000)] == [
        (2002, b"half"),
        (2003, b"next"),
    ]
    assert follower.skipped == 0


def test_a_replaced_or_shrunk_file_ends_the_follower(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    path = _write(layout, b"a\nb\n")
    follower = Follower(path, 4, 2)
    assert follower.poll() == []
    path.write_bytes(b"a\n")  # smaller than the position
    assert follower.poll() == []
    assert follower.ended == "file_replaced"
    assert follower.poll() == []
    other = Follower(path, 2, 1)
    assert other.poll() == []
    replacement = path.with_suffix(".new")
    replacement.write_bytes(b"a\nb\nc\n")
    replacement.replace(path)  # another inode under the same name
    assert other.poll() == []
    assert other.ended == "file_replaced"
