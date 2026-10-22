"""Raw lines → rows: exact raw references, check_row on every row, and the failure paths."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_core.rawlog import RawRecord, format_line
from arcilogger_db.rowcheck import check_row
from arcilogger_ingest import prepare
from arcilogger_ingest.parsers import (
    LinePosition,
    Parsed,
    ParsedRow,
    ParseOutcome,
    Parser,
    get_parser,
)
from arcilogger_ingest.prepare import (
    PATTERN_EXCEPTION,
    PATTERN_RAW,
    StreamInfo,
    prepare_chunk,
    prepare_line,
    split_lines,
)
from arcilogger_ingest.testing import dhcp_message, fortigate_message

RECEIVED = datetime(2026, 9, 23, 9, 44, 57, 123456, tzinfo=timezone(timedelta(hours=3)))
STREAM = StreamInfo("core-r1", "mikrotik", date(2026, 9, 23))


def line(message: bytes, sender: str = "172.20.10.1", at: int = 0) -> bytes:
    return format_line(RECEIVED + timedelta(seconds=at), sender, message)


def test_split_lines() -> None:
    assert split_lines(b"") == []
    assert split_lines(b"a\n") == [b"a"]
    assert split_lines(b"a\nb\n") == [b"a", b"b"]
    assert split_lines(b"a\nb") == [b"a", b"b"]  # final line of a sealed file
    assert split_lines(b"\n\n") == [b"", b""]


def test_chunk_positions_are_the_line_starts_and_numbers() -> None:
    lines = [line(dhcp_message(i), at=i) for i in range(5)]
    data = b"".join(lines)
    chunk = prepare_chunk(STREAM, 1000, 41, data)
    assert [p.line for p in chunk.lines] == [42, 43, 44, 45, 46]
    starts = [1000 + sum(len(x) for x in lines[:i]) for i in range(5)]
    assert [p.offset for p in chunk.lines] == starts
    assert (chunk.end_offset, chunk.end_line) == (1000 + len(data), 46)
    for prepared, raw in zip(chunk.lines, lines, strict=True):
        message = prepared.rows[0][1]
        assert prepared.rows[0][0] == "messages"
        assert message["raw_line"] == prepared.line
        assert message["raw_offset"] == prepared.offset
        assert message["source"] == "core-r1"
        assert message["raw_day"] == date(2026, 9, 23)
        assert message["sender"] == "172.20.10.1"
        assert prepared.received is not None
        assert raw.startswith(prepared.received.isoformat().encode() + b" 172.20.10.1 ")


def test_final_line_without_newline_ends_at_the_end_of_the_data() -> None:
    data = line(dhcp_message(1)) + line(dhcp_message(2))[:-1]
    chunk = prepare_chunk(STREAM, 0, 0, data)
    assert len(chunk.lines) == 2
    assert chunk.end_offset == len(data)
    assert chunk.end_line == 2


def test_a_line_not_in_the_spool_format_is_counted_but_not_invented() -> None:
    """``time_received``/``sender`` are NOT NULL: no messages row is made up for such a line."""
    prepared = prepare_line(STREAM, 7, 300, b"\x00\x00\x00 zeros after a power cut")
    assert prepared.rows == []
    assert [(f.pattern, f.line, f.offset, f.seen) for f in prepared.failures] == [
        (PATTERN_RAW, 7, 300, None)
    ]
    assert prepared.failures[0].detail


@pytest.mark.parametrize(
    ("raw", "detail"),
    [
        # file-system damage in the sender field: core rawlog refuses a non-ASCII sender
        (b"2026-09-23T09:44:57.123456+03:00 \xff\xfe <13>damaged", "bad sender"),
        (b"2026-09-23T09:44:57.123456+03:00 172.20.\xc0.1 <13>damaged", "bad sender"),
        # receive times without a UTC datetime in Python (year 0 / year 10000 in UTC)
        (b"0001-01-01T00:00:00+03:00 172.20.10.1 <13>x", "OverflowError"),
        (b"9999-12-31T23:59:59-03:00 172.20.10.1 <13>x", "OverflowError"),
        (b"2026-13-45T99:00:00+03:00 172.20.10.1 <13>x", "bad receive time"),
        (b"2026-09-23T09:44:57 172.20.10.1 <13>naive time", "bad receive time"),
        (b"", "expected"),
    ],
)
def test_damaged_lines_are_counted_never_raised(raw: bytes, detail: str) -> None:
    """One damaged line used to crash the service on every restart (stuck for every source)."""
    prepared = prepare_line(STREAM, 11, 2048, raw)
    assert prepared.rows == []
    assert [(f.pattern, f.line, f.offset, f.seen) for f in prepared.failures] == [
        (PATTERN_RAW, 11, 2048, None)
    ]
    assert detail in (prepared.failures[0].detail or "")
    assert (prepared.received, prepared.sender) == (None, None)


def test_an_exception_from_the_parser_costs_only_its_details(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(record: RawRecord, position: LinePosition) -> ParseOutcome:
        raise OverflowError("date value out of range")

    monkeypatch.setattr(prepare, "parse_line", broken)
    prepared = prepare_line(STREAM, 5, 640, line(dhcp_message(0)))
    assert [(f.pattern, f.line, f.offset, f.seen) for f in prepared.failures] == [
        (PATTERN_EXCEPTION, 5, 640, RECEIVED)
    ]
    assert prepared.failures[0].detail == "OverflowError: date value out of range"
    [(table, message)] = prepared.rows
    assert table == "messages"
    assert message["parse_error"] == PATTERN_EXCEPTION
    assert (message["raw_line"], message["raw_offset"]) == (5, 640)
    assert check_row("messages", message, complete=True) == []


def test_an_exception_from_the_row_checks_drops_partial_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(record: RawRecord, position: LinePosition) -> ParseOutcome:
        return _outcome(record, position, error="mikrotik.topic", detail="x")

    def check_raising_except_on_the_fallback(
        table: str, values: Any, *, complete: bool = False
    ) -> Any:
        if values.get("parse_error") != PATTERN_EXCEPTION:
            raise KeyError("bug in a row check")
        return check_row(table, values, complete=complete)

    monkeypatch.setattr(prepare, "parse_line", fake)
    monkeypatch.setattr(prepare, "check_row", check_raising_except_on_the_fallback)
    prepared = prepare_line(STREAM, 1, 0, line(dhcp_message(0)))
    # the parser's own failure, recorded before the exception, is replaced (one line, one cause)
    assert [f.pattern for f in prepared.failures] == [PATTERN_EXCEPTION]
    assert [table for table, _ in prepared.rows] == ["messages"]
    assert prepared.rows[0][1]["type"] == "syslog"


def test_no_messages_row_when_even_the_generic_parser_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Exploding:
        def parse(self, record: RawRecord, position: LinePosition) -> ParseOutcome:
            raise RuntimeError("generic parser bug")

    def broken(record: RawRecord, position: LinePosition) -> ParseOutcome:
        raise ValueError("parser bug")

    monkeypatch.setattr(prepare, "parse_line", broken)
    monkeypatch.setattr(prepare, "get_parser", lambda name: Exploding())
    prepared = prepare_line(STREAM, 2, 10, line(dhcp_message(0)))
    assert prepared.rows == []
    assert [(f.pattern, f.detail) for f in prepared.failures] == [
        (PATTERN_EXCEPTION, "ValueError: parser bug")
    ]


def test_a_device_time_out_of_range_never_costs_the_message(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A parser that returns year 1 at +03:00 as the device time (no UTC datetime exists)."""

    class YearOne(Parser):
        name = "mikrotik"

        def parse_record(self, record: RawRecord, position: LinePosition) -> Parsed:
            year_one = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=3)))
            return Parsed(message={"type": "syslog", "text": "x"}, time=year_one)

    monkeypatch.setattr(prepare, "parse_line", lambda r, p: YearOne().parse(r, p))
    prepared = prepare_line(STREAM, 1, 0, line(dhcp_message(0)))
    assert len(prepared.failures) == 1  # prepare.exception today, or the parser's own pattern
    [(table, message)] = prepared.rows
    assert table == "messages"
    assert check_row("messages", message, complete=True) == []


def _outcome(record: RawRecord, position: LinePosition, **changes: Any) -> ParseOutcome:
    outcome = get_parser("generic").parse(record, position)
    return replace(outcome, parser="mikrotik", **changes)


def test_a_typed_row_failing_check_row_is_dropped_but_the_message_is_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    good = {"server": "dhcp60", "action": "assigned", "ip": "172.20.60.8"}
    bad = {"server": "dhcp60", "action": "assigned", "ip": "not-an-ip"}

    def fake(record: RawRecord, position: LinePosition) -> ParseOutcome:
        common = {
            "time": record.received,
            "time_received": record.received,
            "source": position.source,
            "sender": record.sender,
            "raw_day": position.raw_day,
            "raw_line": position.raw_line,
            "raw_offset": position.raw_offset,
            "extra": {},
            "mac": "02:00:5e:00:53:5b",
        }
        return _outcome(
            record,
            position,
            rows=(
                ParsedRow("mt_dhcp", {**common, **bad}),
                ParsedRow("mt_dhcp", {**common, **good}),
                ParsedRow("no_such_table", common),
            ),
        )

    monkeypatch.setattr(prepare, "parse_line", fake)
    prepared = prepare_line(STREAM, 3, 99, line(dhcp_message(0)))
    tables = [table for table, _ in prepared.rows]
    assert tables == ["messages", "mt_dhcp"]
    assert prepared.rows[1][1]["ip"] == "172.20.60.8"
    assert prepared.rows[0][1]["parse_error"] == "rowcheck.mt_dhcp"
    patterns = [f.pattern for f in prepared.failures]
    assert patterns == ["rowcheck.mt_dhcp", "rowcheck.no_such_table"]
    assert "ip" in (prepared.failures[0].detail or "")
    assert all(f.line == 3 and f.offset == 99 for f in prepared.failures)
    for table, values in prepared.rows:
        assert check_row(table, values, complete=True) == []


def test_parser_error_is_counted_and_the_message_still_loads(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(record: RawRecord, position: LinePosition) -> ParseOutcome:
        return _outcome(record, position, error="mikrotik.unknown_topic", detail="topic xyz")

    monkeypatch.setattr(prepare, "parse_line", fake)
    prepared = prepare_line(STREAM, 1, 0, line(dhcp_message(0)))
    assert [(f.pattern, f.detail) for f in prepared.failures] == [
        ("mikrotik.unknown_topic", "topic xyz")
    ]
    assert [table for table, _ in prepared.rows] == ["messages"]


def test_an_invalid_messages_row_falls_back_to_the_generic_view(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake(record: RawRecord, position: LinePosition) -> ParseOutcome:
        outcome = _outcome(record, position)
        broken = {**outcome.message, "severity": 99999, "type": None}  # parser bug
        return replace(outcome, message=broken)

    monkeypatch.setattr(prepare, "parse_line", fake)
    prepared = prepare_line(STREAM, 1, 0, line(dhcp_message(0)))
    assert [f.pattern for f in prepared.failures] == ["rowcheck.messages"]
    table, message = prepared.rows[0]
    assert table == "messages"
    assert message["type"] == "syslog"
    assert message["parse_error"] == "rowcheck.messages"
    assert check_row("messages", message, complete=True) == []


def test_no_messages_row_when_even_the_generic_view_cannot_be_stored() -> None:
    """A sender PostgreSQL's INET refuses (IPv6 zone id): counted, nothing invented."""
    prepared = prepare_line(STREAM, 1, 0, line(b"<13>hello", sender="fe80::1%eth0"))
    assert prepared.rows == []
    assert [f.pattern for f in prepared.failures] == ["rowcheck.messages"]


def test_unknown_sender_is_detected_by_content() -> None:
    unknown = StreamInfo("unknown-10.9.9.9", None, date(2026, 10, 12))
    fortigate = prepare_line(unknown, 1, 0, line(fortigate_message(0), sender="10.9.9.9"))
    generic = prepare_line(unknown, 2, 0, line(b"<13>plain syslog", sender="10.9.9.9"))
    assert fortigate.parser == "fortigate"
    assert generic.parser == "generic"


@settings(max_examples=150, deadline=None)
@given(st.lists(st.binary(max_size=300), min_size=1, max_size=8))
def test_every_emitted_row_is_loadable(messages: list[bytes]) -> None:
    """Whatever arrives, the rows handed to COPY pass check_row and point at their line."""
    data = b"".join(line(message, at=i) for i, message in enumerate(messages))
    chunk = prepare_chunk(STREAM, 0, 0, data)
    assert len(chunk.lines) == len(messages)
    assert chunk.end_offset == len(data)
    for prepared in chunk.lines:
        assert prepared.rows, "every valid spool line becomes a messages row"
        for table, values in prepared.rows:
            assert check_row(table, values, complete=True) == []
            assert (values["raw_line"], values["raw_offset"]) == (prepared.line, prepared.offset)


_STAMPS = st.sampled_from(
    [
        b"2026-09-23T09:44:57.123456+03:00",
        b"0001-01-01T00:00:00+03:00",
        b"9999-12-31T23:59:59-14:00",
        b"2026-09-23T09:44:57",
        b"\x00\x00",
        b"",
    ]
)
_SENDERS = st.sampled_from(
    [b"172.20.10.1", b"fe80::1%12", b"::1", b"\xff\xfe", b"not-an-ip", b"", b"10.9.9.9"]
)


@settings(max_examples=300, deadline=None)
@given(st.lists(st.tuples(_STAMPS, _SENDERS, st.binary(max_size=200)), min_size=1, max_size=6))
def test_no_line_of_any_bytes_raises(parts: list[tuple[bytes, bytes, bytes]]) -> None:
    """Spool lines as file-system damage could leave them: every line is rows or a failure."""
    raw_lines = [b" ".join(p).replace(b"\n", b" ") for p in parts]
    chunk = prepare_chunk(STREAM, 0, 0, b"".join(r + b"\n" for r in raw_lines))
    assert len(chunk.lines) == len(raw_lines)
    for prepared in chunk.lines:
        assert prepared.rows or prepared.failures
        for table, values in prepared.rows:
            assert check_row(table, values, complete=True) == []
