"""The parser contract: every emitted row fits the database schema (B1 and B2 rely on this)."""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta, timezone
from datetime import time as dt_time
from pathlib import Path
from typing import Any, ClassVar

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_core.rawlog import RawRecord, parse_line
from arcilogger_db.models import COMMON_COLUMNS, TIME_SERIES_TABLES, Base
from arcilogger_db.rowcheck import check_row
from arcilogger_ingest.parsers import (
    PARSERS,
    SOURCE_TYPES,
    LinePosition,
    Parsed,
    ParsedRow,
    Parser,
    build_parsers,
    detect_type,
    get_parser,
)
from arcilogger_ingest.parsers import parse_line as parse_raw
from arcilogger_ingest.parsers.base import MESSAGE_TEXT_MAX, decode_message
from arcilogger_ingest.parsers.fortigate import FortiGateParser
from arcilogger_ingest.parsers.generic import GenericParser
from arcilogger_ingest.parsers.mikrotik import MikroTikParser
from arcilogger_ingest.parsers.syslog import plausible_time

FORMAT = Path(__file__).parents[2] / "tests" / "fixtures" / "format"
FORTIGATE = (FORMAT / "fortigate-sample.log").read_bytes().splitlines()
RECEIVED = datetime(2026, 9, 23, 9, 44, 57, 123456, tzinfo=timezone(timedelta(hours=3)))


def _mikrotik_wire_lines() -> list[bytes]:
    """SYNTHETIC wire lines built from the RouterOS `/log print` sample (verify on site)."""
    lines = []
    for row in (FORMAT / "mikrotik-log-print-sample.txt").read_text("utf-8").splitlines():
        topics, _, message = row.partition("|")
        topics_text = ",".join(topics.strip().split(";"))
        lines.append(
            f"<30>2026-09-23T09:38:26.380+03:00 CORE-R1 {topics_text} {message.strip()}".encode()
        )
    return lines


MIKROTIK = _mikrotik_wire_lines()
SYNTHETIC = Path(__file__).parents[2] / "tests" / "fixtures" / "synthetic"


def _synthetic(name: str) -> list[bytes]:
    """Hand-written lines (synthetic, verify on site); ``#`` lines are comments."""
    lines = (SYNTHETIC / name).read_bytes().splitlines()
    return [line for line in lines if not line.startswith(b"#")]


FORTIGATE_SYNTHETIC = _synthetic("fortigate-synthetic.log")
MIKROTIK_SYNTHETIC = _synthetic("mikrotik-wire-synthetic.log")
JUNK = [
    b"",
    b"<999>not a pri",
    b"\x00\xff\xfe binary \x00",
    b"<13>Sep 23 09:44:57 host app[12]: hello",
    b"<14>1 2026-09-23T06:44:57.5Z host app 1 ID47 - message",
    "Türkçe ığüşöç İ".encode(),
    b"x" * 5000,
]


def record(
    message: bytes, received: datetime = RECEIVED, sender: str = "172.31.255.1"
) -> RawRecord:
    return RawRecord(received=received, sender=sender, message=message)


def position(source_type: Any = "fortigate", line: int = 7, offset: int = 1234) -> LinePosition:
    return LinePosition(
        source="fortigate",
        source_type=source_type,
        raw_day=date(2026, 9, 23),
        raw_line=line,
        raw_offset=offset,
    )


def assert_loadable(parser: Parser, outcome_rows: tuple[ParsedRow, ...]) -> None:
    for row in outcome_rows:
        if row.table != "messages":
            assert row.table in parser.tables, (parser.name, row.table)
        assert check_row(row.table, row.values, complete=True) == [], row


def test_registry_has_one_parser_per_source_type() -> None:
    assert set(PARSERS) == set(SOURCE_TYPES) == {"fortigate", "mikrotik", "generic"}
    for name, parser in PARSERS.items():
        assert parser.name == name
        assert get_parser(name) is parser


def test_declared_tables_exist_with_raw_reference_columns() -> None:
    """Every table a parser may emit is a time-series table of arcilogger_db.models."""
    for parser in PARSERS.values():
        for table in parser.tables:
            assert table in Base.metadata.tables, (parser.name, table)
            assert table in TIME_SERIES_TABLES, (parser.name, table)
            columns = set(Base.metadata.tables[table].columns.keys())
            assert set(COMMON_COLUMNS) <= columns, table
    assert PARSERS["fortigate"].tables == {
        "fg_traffic",
        "fg_web",
        "fg_dns",
        "fg_app",
        "fg_ips",
        "fg_virus",
        "fg_event",
    }
    assert {t for t in TIME_SERIES_TABLES if t.startswith("mt_")} == PARSERS["mikrotik"].tables


@pytest.mark.parametrize("source_type", ["fortigate", "mikrotik", "generic", None])
@pytest.mark.parametrize(
    "message", FORTIGATE + MIKROTIK + FORTIGATE_SYNTHETIC + MIKROTIK_SYNTHETIC + JUNK
)
def test_every_outcome_is_loadable(source_type: Any, message: bytes) -> None:
    outcome = parse_raw(record(message), position(source_type))
    parser = PARSERS[detect_type(record(message), source_type)]
    assert outcome.parser == parser.name
    assert_loadable(parser, outcome.all_rows())
    assert outcome.message["raw_line"] == 7
    assert outcome.message["raw_offset"] == 1234
    assert outcome.message["source"] == "fortigate"
    assert len(outcome.message["text"]) <= MESSAGE_TEXT_MAX


@pytest.mark.parametrize("source_type", ["fortigate", None])
def test_sample_fortigate_lines_give_one_typed_row(source_type: Any) -> None:
    """Configured FortiGate and unknown sender (detected by content) parse the same."""
    tables = []
    for message in FORTIGATE:
        outcome = parse_raw(record(message), position(source_type))
        assert outcome.parser == "fortigate"
        assert outcome.error is None
        (row,) = outcome.rows
        tables.append(row.table)
        assert row.values["time"] == outcome.message["time"]
    assert tables == [
        "fg_event",
        "fg_app",
        "fg_traffic",
        "fg_traffic",
        "fg_traffic",
        "fg_app",
        "fg_traffic",
    ]


def test_mikrotik_sample_lines_give_their_typed_rows() -> None:
    tables = []
    for message in MIKROTIK:
        outcome = parse_raw(record(message), position("mikrotik"))
        assert outcome.error is None
        (row,) = outcome.rows
        tables.append(row.table)
    assert tables == [
        "mt_dhcp",
        "mt_arp",
        "mt_account",
        "mt_dhcp",
        "mt_fw",
        "mt_dhcp",
        "mt_arp",
        "mt_fw",
        "mt_dhcp",
        "mt_arp",
        "mt_account",
    ]


def test_synthetic_lines_cover_every_typed_table() -> None:
    emitted = set()
    for message in FORTIGATE_SYNTHETIC + FORTIGATE:
        emitted |= {row.table for row in parse_raw(record(message), position("fortigate")).rows}
    for message in MIKROTIK_SYNTHETIC:
        emitted |= {row.table for row in parse_raw(record(message), position("mikrotik")).rows}
    assert emitted == PARSERS["fortigate"].tables | PARSERS["mikrotik"].tables


def test_rows_of_one_table_always_have_the_same_columns() -> None:
    """``arcilogger_db.bulk.copy_rows`` takes its column list from the first row of a batch:
    a later row with more keys would lose values, so every parser emits every column."""
    keys: dict[str, set[frozenset[str]]] = {}
    lines = [
        (message, kind)
        for kind, messages in (
            ("fortigate", FORTIGATE + FORTIGATE_SYNTHETIC),
            ("mikrotik", MIKROTIK + MIKROTIK_SYNTHETIC),
            ("generic", MIKROTIK + JUNK),
        )
        for message in messages
    ]
    for message, kind in lines:
        for row in parse_raw(record(message), position(kind)).all_rows():
            keys.setdefault(row.table, set()).add(frozenset(row.values))
    for table, variants in keys.items():
        assert len(variants) == 1, table
        (columns,) = variants
        expected = set(Base.metadata.tables[table].columns.keys()) - {"id"}
        assert columns == expected, (table, expected ^ columns)


def test_sample_fortigate_line_through_the_spool_format() -> None:
    """A line as the collector writes it, read back exactly as ingest will."""
    from arcilogger_core.rawlog import format_line

    line = format_line(RECEIVED, "172.31.255.1", FORTIGATE[6])  # the session end
    outcome = parse_raw(parse_line(line), position("fortigate"))
    assert outcome.message["time_received"] == RECEIVED.astimezone(UTC)
    assert outcome.message["sender"] == "172.31.255.1"
    assert outcome.message["facility"] == 23  # <189> = local7.notice
    assert outcome.message["severity"] == 5
    assert outcome.message["text"].startswith('date=2026-09-23 time=09:44:57 devname="FIREWALL-01"')


def test_detection_configured_type_wins_content_only_for_unknown_senders() -> None:
    fortigate_like = record(b'<189>date=2026-09-23 devname="FIREWALL-01" logid="0000000013"')
    plain = record(b"<30>hello")
    assert detect_type(fortigate_like, None) == "fortigate"
    assert detect_type(record(b"<189>logid=0100040704 x=1"), None) == "fortigate"
    assert detect_type(plain, None) == "generic"
    assert detect_type(fortigate_like, "mikrotik") == "mikrotik"
    assert detect_type(fortigate_like, "generic") == "generic"


def test_device_time_with_offset_is_used_in_utc() -> None:
    outcome = parse_raw(record(MIKROTIK[0]), position("mikrotik"))
    assert outcome.message["time"] == datetime(2026, 9, 23, 6, 38, 26, 380000, tzinfo=UTC)
    assert outcome.message["host"] == "CORE-R1"
    assert outcome.message["time_received"] == RECEIVED.astimezone(UTC)


def test_time_without_offset_is_not_guessed() -> None:
    outcome = parse_raw(record(b"<13>Sep 23 09:44:57 host app: hi"), position("generic"))
    assert outcome.message["time"] == RECEIVED.astimezone(UTC)
    assert outcome.message["extra"] == {"device_time": "Sep 23 09:44:57"}
    assert outcome.message["program"] == "app"
    assert outcome.message["text"] == "hi"


class _Exploding(Parser):
    name: ClassVar[str] = "exploding"
    tables: ClassVar[frozenset[str]] = frozenset({"mt_arp"})

    def parse_record(self, record: RawRecord, position: LinePosition) -> Parsed:
        raise RuntimeError("bug " * 300)


class _Rows(Parser):
    name: ClassVar[str] = "rows"
    tables: ClassVar[frozenset[str]] = frozenset({"mt_arp"})

    def parse_record(self, record: RawRecord, position: LinePosition) -> Parsed:
        return Parsed(
            message={"type": "arp", "text": "ARP-LOG 172.20.10.23 00:00:5E:00:53:43 vlan10"},
            rows=(
                ParsedRow(
                    "mt_arp",
                    {
                        "ip": "172.20.10.23",
                        "mac": "00:00:5E:00:53:43",
                        "interface": "vlan10",
                        "raw_line": 999,  # a parser can never move the raw reference
                        "source": "elsewhere",
                    },
                ),
            ),
        )


def test_parser_exception_becomes_a_parse_failure() -> None:
    outcome = _Exploding().parse(record(b"<30>anything"), position("generic"))
    assert outcome.failed
    assert outcome.error == "exploding.exception"
    assert outcome.detail is not None
    assert outcome.detail.startswith("RuntimeError: bug")
    assert len(outcome.detail) <= 500
    assert outcome.rows == ()
    assert outcome.message["parse_error"] == "exploding.exception"
    assert check_row("messages", outcome.message, complete=True) == []


def test_common_columns_are_added_and_raw_reference_cannot_be_overridden() -> None:
    outcome = _Rows().parse(record(b"<30>x"), position("mikrotik", line=3, offset=88))
    (row,) = outcome.rows
    assert row.values["raw_line"] == 3
    assert row.values["raw_offset"] == 88
    assert row.values["source"] == "fortigate"
    assert row.values["extra"] == {}
    assert row.values["time"] == RECEIVED.astimezone(UTC)
    assert outcome.all_rows()[0].table == "messages"
    assert_loadable(_Rows(), outcome.all_rows())


def test_decode_message_never_returns_nul() -> None:
    assert decode_message(b"a\x00b\xffc") == "a�b�c"
    assert decode_message("ığüşöç".encode()) == "ığüşöç"


def test_position_validation() -> None:
    with pytest.raises(ValueError, match="1-based"):
        LinePosition("s", None, date(2026, 10, 12), 0, 0)
    with pytest.raises(ValueError, match="negative"):
        LinePosition("s", None, date(2026, 10, 12), 1, -1)


@settings(max_examples=300, deadline=None)
@given(message=st.binary(max_size=3000), source_type=st.sampled_from([*SOURCE_TYPES, None]))
def test_fuzz_any_bytes_give_loadable_rows(message: bytes, source_type: Any) -> None:
    outcome = parse_raw(record(message), position(source_type))
    parser = PARSERS[detect_type(record(message), source_type)]
    assert_loadable(parser, outcome.all_rows())


# -- device times (review of B1: an out-of-range time used to escape parse_line) -----------------


class _TimeParser(Parser):
    """Returns a fixed device time: what ``complete`` must survive for any parser."""

    name: ClassVar[str] = "timed"
    tables: ClassVar[frozenset[str]] = frozenset({"mt_arp"})

    def __init__(self, time: datetime, error: str | None = None) -> None:
        self.time = time
        self.error = error

    def parse_record(self, record: RawRecord, position: LinePosition) -> Parsed:
        row = ParsedRow("mt_arp", {"ip": "172.20.10.23", "mac": "00:00:5e:00:53:43"})
        return Parsed(
            message={"type": "arp", "extra": {"note": "kept"}},
            rows=(row,),
            time=self.time,
            error=self.error,
            detail="parser detail" if self.error else None,
        )


@pytest.mark.parametrize(
    "device",
    [
        datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=3))),
        datetime(9999, 12, 31, 23, 59, tzinfo=timezone(-timedelta(hours=23, minutes=59))),
        datetime(2026, 9, 23, 9, 44, 57),  # noqa: DTZ001 - a naive time is a parser bug
    ],
    ids=["year1+0300", "year9999-2359", "naive"],
)
def test_complete_survives_a_device_time_it_cannot_convert(device: datetime) -> None:
    outcome = _TimeParser(device).parse(record(b"<30>x"), position("mikrotik"))
    assert outcome.error == "timed.time"
    assert outcome.detail == "device time not representable in UTC"
    assert outcome.message["time"] == RECEIVED.astimezone(UTC)
    assert outcome.message["extra"] == {"note": "kept", "device_time": device.isoformat()}
    assert outcome.message["parse_error"] == "timed.time"
    (row,) = outcome.rows
    assert row.values["time"] == RECEIVED.astimezone(UTC)
    assert_loadable(_TimeParser(device), outcome.all_rows())
    with_error = _TimeParser(device, error="timed.other").parse(record(b"x"), position(None))
    assert (with_error.error, with_error.detail) == ("timed.other", "parser detail")
    assert with_error.message["extra"]["device_time"] == device.isoformat()


def test_build_parsers_configures_every_parser_and_parse_line_uses_them() -> None:
    parsers = build_parsers(
        interim_logids={"0000000013"},
        config_patterns=[r"^config saved by (?P<user>\S+)$"],
        server_vlan_regex=r"^vlan(\d+)-dhcp$",
        host_names=["CORE-R1"],
        max_clock_skew=None,
    )
    assert set(parsers) == set(SOURCE_TYPES)
    fortigate, mikrotik, generic = parsers["fortigate"], parsers["mikrotik"], parsers["generic"]
    assert isinstance(fortigate, FortiGateParser)
    assert isinstance(mikrotik, MikroTikParser)
    assert isinstance(generic, GenericParser)
    assert fortigate.interim_logids == {"0000000013"}
    assert mikrotik.host_names == {"core-r1"}
    assert mikrotik.vlan_of("vlan42-dhcp") == 42
    assert all(parser.max_clock_skew is None for parser in (fortigate, mikrotik, generic))
    assert get_parser("mikrotik", parsers) is mikrotik
    configured = parse_raw(record(FORTIGATE[6]), position("fortigate"), parsers)
    assert configured.rows[0].values["is_interim"] is True  # 0000000013 is interim here
    default = parse_raw(record(FORTIGATE[6]), position("fortigate"))
    assert default.rows[0].values["is_interim"] is False
    old = b"<30>2020-01-01T00:00:00Z host app: hi"
    assert parse_raw(record(old), position("generic"), parsers).error is None
    assert parse_raw(record(old), position("generic")).error == "generic.time"
    with pytest.raises(re.error):
        build_parsers(config_patterns=["(unclosed"])


@pytest.mark.parametrize(
    ("stamp", "error"),
    [
        ("2026-09-23T06:44:57Z", None),
        ("0001-01-01T00:00:00+03:00", "generic.time"),
        ("9999-12-31T23:59:59-23:59", "generic.time"),
        ("2020-01-01T00:00:00Z", "generic.time"),
    ],
)
def test_generic_device_time_rules(stamp: str, error: str | None) -> None:
    for header in (f"<30>1 {stamp} host app - - - hi", f"<30>{stamp} host app: hi"):
        outcome = parse_raw(record(header.encode()), position("generic"))
        assert outcome.error == error
        if error is None:
            assert outcome.message["time"] == datetime(2026, 9, 23, 6, 44, 57, tzinfo=UTC)
            assert outcome.message["extra"] == {}
        else:
            assert outcome.message["time"] == RECEIVED.astimezone(UTC)
            assert outcome.message["extra"] == {"device_time": stamp}
        assert outcome.message["text"] == "hi"


def _offset_text(minutes: int, colon: bool) -> str:
    sign = "-" if minutes < 0 else "+"
    hours, rest = divmod(abs(minutes), 60)
    return f"{sign}{hours:02d}{':' if colon else ''}{rest:02d}"


@settings(max_examples=300, deadline=None)
@given(
    day=st.dates() | st.sampled_from([date.min, date.max]),
    clock=st.times().map(lambda t: t.replace(microsecond=0)),
    minutes=st.integers(min_value=-(23 * 60 + 59), max_value=23 * 60 + 59),
    source_type=st.sampled_from([*SOURCE_TYPES, None]),
)
def test_any_device_time_in_any_parser_is_loadable(
    day: date, clock: dt_time, minutes: int, source_type: Any
) -> None:
    """Year 1 to 9999 with any offset, through every parser and the content detection."""
    iso = f"{day.isoformat()}T{clock.isoformat()}{_offset_text(minutes, colon=True)}"
    fortigate = (
        f'<189>date={day.isoformat()} time={clock.isoformat()} devname="X" '
        f'tz="{_offset_text(minutes, colon=False)}" logid="0000000013" type="traffic" '
        'subtype="forward" srcip=172.20.10.37'
    )
    for message in (
        fortigate,
        f"<30>{iso} CORE-R1 dhcp,info dhcp41 assigned 172.20.41.142 for 00:00:5E:00:53:48",
        f"<30>1 {iso} host app - - - hi",
    ):
        outcome = parse_raw(record(message.encode()), position(source_type))
        parser = PARSERS[detect_type(record(message.encode()), source_type)]
        assert outcome.error != f"{parser.name}.exception", outcome.detail
        assert_loadable(parser, outcome.all_rows())
        assert abs(outcome.message["time"] - RECEIVED) <= timedelta(days=1)


def test_plausible_time_rules() -> None:
    received = RECEIVED  # 2026-09-23T09:44:57.123456+03:00
    day = timedelta(days=1)
    assert plausible_time(received + day, received) == (received + day).astimezone(UTC)
    assert plausible_time(received - day, received) == (received - day).astimezone(UTC)
    assert plausible_time(received + day + timedelta(microseconds=1), received) is None
    assert plausible_time(received - day - timedelta(microseconds=1), received) is None
    naive = datetime(2026, 9, 23, 6, 44, 57)  # noqa: DTZ001 - must be refused, not guessed
    assert plausible_time(naive, received) is None
    assert plausible_time(naive, received, None) is None
    old = datetime(2020, 1, 1, tzinfo=UTC)
    assert plausible_time(old, received) is None
    assert plausible_time(old, received, None) == old
    year_one = datetime(1, 1, 1, tzinfo=timezone(timedelta(hours=3)))
    assert plausible_time(year_one, received, None) is None  # not representable in UTC
    assert plausible_time(year_one, received) is None
