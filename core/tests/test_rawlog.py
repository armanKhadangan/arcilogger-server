"""Raw spool line format: reversible escaping, one message per line."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arcilogger_core.rawlog import (
    RawLineError,
    escape,
    format_line,
    parse_line,
    received_at,
    unescape,
)

RECEIVED = datetime(2026, 9, 23, 6, 44, 57, 123456, tzinfo=UTC)
FORTIGATE = b'<189>date=2026-09-23 time=09:44:57 devname="FIREWALL-01" logid="0000000013"'


def test_line_layout_matches_the_specification() -> None:
    line = format_line(RECEIVED, "172.31.255.1", FORTIGATE)
    assert line == b"2026-09-23T09:44:57.123456+03:00 172.31.255.1 " + FORTIGATE + b"\n"


def test_control_characters_and_hash_are_escaped() -> None:
    assert escape(b"a\nb\tc\x00d\x7fe#f") == b"a#012b#011c#000d#177e#043f"
    assert unescape(b"a#012b#011c#000d#177e#043f") == b"a\nb\tc\x00d\x7fe#f"


def test_a_message_never_spans_two_lines() -> None:
    line = format_line(RECEIVED, "172.31.255.1", b"evil\nline\r\n")
    assert line.count(b"\n") == 1
    assert parse_line(line).message == b"evil\nline\r\n"


def test_non_utf8_bytes_are_kept() -> None:
    message = b"\xff\xfe user=\xc5\x9f"
    assert parse_line(format_line(RECEIVED, "10.0.0.1", message)).message == message


@given(st.binary(max_size=2048))
def test_escaping_is_reversible(message: bytes) -> None:
    line = format_line(RECEIVED, "172.20.10.1", message)
    record = parse_line(line)
    assert record.message == message
    assert record.received == RECEIVED
    assert record.sender == "172.20.10.1"
    assert line.count(b"\n") == 1


def test_received_at_fast_path() -> None:
    line = format_line(RECEIVED, "172.31.255.1", FORTIGATE)
    assert received_at(line) == RECEIVED


@pytest.mark.parametrize(
    "line",
    [
        b"",
        b"no-spaces",
        b"2026-09-23T09:44:57 172.31.255.1 msg",  # naive time
        b"yesterday 172.31.255.1 msg",
        b"2026-09-23T09:44:57.123456+03:00 172.31.255.1 bad#99",
        b"2026-09-23T09:44:57.123456+03:00 172.31.255.1 raw\tcontrol",
    ],
)
def test_malformed_lines_are_rejected(line: bytes) -> None:
    with pytest.raises(RawLineError):
        parse_line(line)


def test_received_at_rejects_garbage() -> None:
    with pytest.raises(RawLineError):
        received_at(b"garbage")
    with pytest.raises(RawLineError):
        received_at(b"notatime 1.2.3.4 x")


@pytest.mark.parametrize("sender", ["", "1.2.3.4 x"])
def test_invalid_sender_is_rejected(sender: str) -> None:
    with pytest.raises(ValueError, match="sender"):
        format_line(RECEIVED, sender, b"x")


def test_non_ascii_sender_is_a_format_error() -> None:
    with pytest.raises(RawLineError, match="bad sender"):
        parse_line("2026-09-23T09:44:57.123456+03:00 172.31.255.ı hello\n".encode())
