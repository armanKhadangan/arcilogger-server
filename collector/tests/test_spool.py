"""Spool writer: append-only, unbuffered, one file per source and receive day."""

from __future__ import annotations

import errno
from datetime import UTC, date, datetime
from pathlib import Path
from typing import BinaryIO, cast

import pytest

from arcilogger_collector.spool import SpoolWriter, TornLine, write_all
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line, parse_line

BEFORE_MIDNIGHT = datetime(2026, 10, 12, 20, 59, 59, 999999, tzinfo=UTC)  # 23:59:59.999999 +03
AFTER_MIDNIGHT = datetime(2026, 10, 12, 21, 0, 0, tzinfo=UTC)  # 00:00:00 +03


def test_lines_are_visible_immediately(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    writer = SpoolWriter(layout)
    writer.write("fortigate", BEFORE_MIDNIGHT, "172.31.255.1", b"<189>one")
    data = layout.spool_file("fortigate", date(2026, 10, 12)).read_bytes()
    assert parse_line(data).message == b"<189>one"
    writer.close()


def test_receive_day_decides_the_file(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    writer = SpoolWriter(layout)
    writer.write("fortigate", BEFORE_MIDNIGHT, "172.31.255.1", b"late")
    writer.write("fortigate", AFTER_MIDNIGHT, "172.31.255.1", b"early")
    writer.flush()
    assert writer.open_files == [
        ("fortigate", date(2026, 10, 12)),
        ("fortigate", date(2026, 10, 13)),
    ]
    assert writer.close_stale(date(2026, 10, 13)) == [("fortigate", date(2026, 10, 12))]
    assert writer.open_files == [("fortigate", date(2026, 10, 13))]
    day12 = layout.spool_file("fortigate", date(2026, 10, 12)).read_bytes().splitlines()
    day13 = layout.spool_file("fortigate", date(2026, 10, 13)).read_bytes().splitlines()
    assert [parse_line(x).message for x in day12] == [b"late"]
    assert [parse_line(x).message for x in day13] == [b"early"]
    writer.close()


def test_appends_to_an_existing_file_after_restart(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    first = SpoolWriter(layout)
    first.write("core-r1", BEFORE_MIDNIGHT, "172.20.10.1", b"a")
    first.close()
    second = SpoolWriter(layout)
    second.write("core-r1", BEFORE_MIDNIGHT, "172.20.10.1", b"b")
    second.close()
    lines = layout.spool_file("core-r1", date(2026, 10, 12)).read_bytes().splitlines()
    assert [parse_line(x).message for x in lines] == [b"a", b"b"]


# -- torn lines: one line is one message, also after a crash or a full disk -----------------------

T0 = datetime(2026, 10, 12, 9, 0, tzinfo=UTC)
T1 = datetime(2026, 10, 12, 9, 1, tzinfo=UTC)
DAY = date(2026, 10, 12)
DHCP = b"<30>2026-10-12T12:01:00+03:00 CORE-R1 dhcp,info dhcp43 assigned 172.20.43.121"


def _torn_file(layout: Layout, tail: bytes) -> bytes:
    path = layout.spool_file("core-r1", DAY)
    path.parent.mkdir(parents=True)
    before = format_line(T0, "172.20.10.1", b"<30>complete") + tail
    path.write_bytes(before)
    return before


@pytest.mark.parametrize(
    "tail",
    [
        format_line(T0, "172.20.10.1", DHCP)[:60],  # power cut in the middle of a line
        b"\x00" * 300,  # NTFS: size committed, data not
    ],
    ids=["partial", "nul"],
)
def test_a_torn_last_line_is_terminated_before_the_next_message(
    tmp_path: Path, tail: bytes
) -> None:
    layout = Layout(tmp_path)
    before = _torn_file(layout, tail)
    seen: list[TornLine] = []
    writer = SpoolWriter(layout, on_torn=seen.append)
    writer.write("core-r1", T1, "172.20.10.1", DHCP)
    writer.close()
    data = layout.spool_file("core-r1", DAY).read_bytes()
    assert data.startswith(before)  # existing bytes unchanged, only appended to
    assert data == before + b"\n" + format_line(T1, "172.20.10.1", DHCP)
    lines = data.splitlines(keepends=True)
    assert len(lines) == 3
    assert parse_line(lines[-1]).received == T1
    assert parse_line(lines[-1]).message == DHCP
    assert seen == [TornLine("core-r1", DAY, len(before))]
    assert writer.torn_lines == seen


def test_a_line_without_only_its_newline_stays_intact(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    before = _torn_file(layout, format_line(T0, "172.20.10.1", b"<30>whole")[:-1])
    writer = SpoolWriter(layout)
    writer.write("core-r1", T1, "172.20.10.1", DHCP)
    writer.close()
    lines = layout.spool_file("core-r1", DAY).read_bytes().splitlines()
    assert [parse_line(x).message for x in lines] == [b"<30>complete", b"<30>whole", DHCP]
    assert len(writer.torn_lines) == 1
    assert before + b"\n" == layout.spool_file("core-r1", DAY).read_bytes()[: len(before) + 1]


def test_clean_and_empty_files_get_no_extra_newline(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    clean = layout.spool_file("core-r1", DAY)
    empty = layout.spool_file("fortigate", DAY)
    clean.parent.mkdir(parents=True)
    empty.parent.mkdir(parents=True)
    clean.write_bytes(format_line(T0, "172.20.10.1", b"a"))
    empty.write_bytes(b"")
    writer = SpoolWriter(layout)
    writer.terminate_torn_lines(DAY)
    writer.write("core-r1", T1, "172.20.10.1", b"b")
    writer.write("fortigate", T1, "172.31.255.1", b"c")
    writer.close()
    assert clean.read_bytes() == format_line(T0, "172.20.10.1", b"a") + format_line(
        T1, "172.20.10.1", b"b"
    )
    assert empty.read_bytes() == format_line(T1, "172.31.255.1", b"c")
    assert writer.torn_lines == []


def test_terminate_on_start_handles_only_that_day_and_never_the_audit_spool(
    tmp_path: Path,
) -> None:
    layout = Layout(tmp_path)
    before = _torn_file(layout, b"2026-10-12T12:00:00.000000+03:00 172.20.10.1 <30>cut")
    other_day = layout.spool_file("core-r1", date(2026, 10, 11))
    other_day.write_bytes(b"torn yesterday")
    audit = layout.audit_spool(DAY)
    audit.parent.mkdir(parents=True)
    audit.write_bytes(b'{"id": 1')
    writer = SpoolWriter(layout)
    writer.terminate_torn_lines(DAY)
    writer.terminate_torn_lines(DAY)  # idempotent
    writer.close()
    assert layout.spool_file("core-r1", DAY).read_bytes() == before + b"\n"
    assert other_day.read_bytes() == b"torn yesterday"  # the sealer's business
    assert audit.read_bytes() == b'{"id": 1'  # the API cuts audit fragments itself
    assert writer.torn_lines == [TornLine("core-r1", DAY, len(before))]


class _DiskFull:
    """A spool handle that writes half of the next line and then reports a full disk."""

    def __init__(self, inner: BinaryIO, failures: int) -> None:
        self.inner = inner
        self.failures = failures
        self.short_done = False

    def write(self, data: bytes | memoryview) -> int:
        if not self.short_done:
            self.short_done = True
            half = bytes(data)[: len(data) // 2]
            return self.inner.write(half) or 0
        if self.failures > 0:
            self.failures -= 1
            raise OSError(errno.ENOSPC, "No space left on device")
        return self.inner.write(data) or 0

    def fileno(self) -> int:
        return self.inner.fileno()

    def close(self) -> None:
        self.inner.close()


def test_a_full_disk_never_glues_two_messages_into_one_line(tmp_path: Path) -> None:
    layout = Layout(tmp_path)
    writer = SpoolWriter(layout)
    writer.write("fortigate", T0, "172.31.255.1", b"<189>first")
    key = ("fortigate", DAY)
    writer._handles[key] = cast(BinaryIO, _DiskFull(writer._handles[key], failures=3))
    with pytest.raises(OSError, match="No space"):
        writer.write("fortigate", T0, "172.31.255.1", b"<189>cut by the full disk")
    for _ in range(2):  # the newline cannot be written either while the disk is full
        with pytest.raises(OSError, match="No space"):
            writer.write("fortigate", T0, "172.31.255.1", b"<189>lost")
    writer.write("fortigate", T1, "172.31.255.1", b"<189>after recovery")
    writer.close()
    lines = layout.spool_file("fortigate", DAY).read_bytes().splitlines(keepends=True)
    assert len(lines) == 3
    assert parse_line(lines[0]).message == b"<189>first"
    assert format_line(T0, "172.31.255.1", b"<189>cut by the full disk").startswith(lines[1][:-1])
    assert parse_line(lines[2]).message == b"<189>after recovery"
    assert parse_line(lines[2]).received == T1
    assert len(writer.torn_lines) == 1


def test_a_zero_byte_write_is_a_failure() -> None:
    class Stuck:
        def write(self, data: bytes | memoryview) -> int:
            return 0

    with pytest.raises(OSError, match="short write"):
        write_all(cast(BinaryIO, Stuck()), b"x")
