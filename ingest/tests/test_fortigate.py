"""FortiGate parser: the 7 sample lines field by field, synthetic types, time rules, failures.

Golden values were read off the raw lines in ``tests/fixtures/format`` by hand; a change in
any mapping shows up here as a precise diff.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.rawlog import RawRecord
from arcilogger_db.rowcheck import check_row
from arcilogger_ingest.parsers import LinePosition, ParseOutcome
from arcilogger_ingest.parsers.base import URL_MAX
from arcilogger_ingest.parsers.fortigate import (
    FG_TRAFFIC,
    SPECS,
    FortiGateParser,
    eventtime_utc,
    local_time,
    offset_of,
    table_for,
)
from arcilogger_ingest.parsers.kv import tokenize
from arcilogger_ingest.parsers.values import to_mac

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures"
SAMPLES = (FIXTURES / "format" / "fortigate-sample.log").read_bytes().splitlines()
#: The format sample lines (file order = time order), by what each one shows.
PERF, APP, INTERIM, FAILED, LOCAL, SSL, CLOSE = SAMPLES
SYNTHETIC = [
    line
    for line in (FIXTURES / "synthetic" / "fortigate-synthetic.log").read_bytes().splitlines()
    if not line.startswith(b"#")
]
RECEIVED = datetime(2026, 9, 23, 6, 44, 58, 500000, tzinfo=UTC)
PARSER = FortiGateParser()
POLUUID = "00000000-0000-0000-0000-000000000012"
#: The raw-reference columns ``Parser.parse`` adds (tested in test_parser_contract.py).
FRAMEWORK = {"time", "time_received", "source", "sender", "raw_day", "raw_line", "raw_offset"}


def parse(message: bytes, parser: FortiGateParser = PARSER) -> ParseOutcome:
    record = RawRecord(received=RECEIVED, sender="172.31.255.1", message=message)
    position = LinePosition("fortigate", "fortigate", date(2026, 9, 23), 1, 0)
    outcome = parser.parse(record, position)
    for row in outcome.all_rows():
        assert check_row(row.table, row.values, complete=True) == [], row
    return outcome


def typed(outcome: ParseOutcome) -> tuple[str, dict[str, Any]]:
    """The one typed row without the framework columns."""
    (row,) = outcome.rows
    return row.table, {k: v for k, v in row.values.items() if k not in FRAMEWORK}


def utc(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0, micro: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, micro, tzinfo=UTC)


def columns(table: str, **values: Any) -> dict[str, Any]:
    """Every column of ``table`` (``None``) overridden by ``values``."""
    base: dict[str, Any] = dict.fromkeys(column for column, _, _ in SPECS[table])
    if table == "fg_traffic":
        base["is_interim"] = False
    if table == "fg_dns":
        base["answers"] = None
    if table == "fg_dhcp":
        base["device_time"] = False
    unknown = set(values) - set(base) - {"extra"}
    assert not unknown, unknown
    return {**base, **values}


COMMON_EXTRA = {"devid": "FGT0000000000000", "vd": "root"}
ROLES = {"srcintfrole": "undefined", "dstintfrole": "wan"}

GOLDEN: list[tuple[int, int, datetime, str, dict[str, Any]]] = [
    (
        23 * 8 + 5,
        5,
        utc(2026, 9, 23, 6, 41, 0, 218047),
        "fg_event",
        columns(
            "fg_event",
            logid="0100040704",
            subtype="system",
            level="notice",
            logdesc="System performance statistics",
            action="perf-stats",
            msg=(
                "Performance statistics: average CPU: 3, memory:  31, concurrent sessions:  74, "
                "setup-rate: 2"
            ),
            extra={
                **COMMON_EXTRA,
                "cpu": "3",
                "mem": "31",
                "totalsession": "74",
                "disk": "0",
                "bandwidth": "118/245",
                "setuprate": "2",
                "disklograte": "0",
                "fazlograte": "0",
                "freediskstorage": "0",
                "sysuptime": "58210",
                "waninfo": (
                    "name=wan1,bytes=7340032000/912680550,packets=6120400/2890310;"
                    "name=wan2,bytes=0/0,packets=0/0;"
                ),
            },
        ),
    ),
    (
        23 * 8 + 6,
        6,
        utc(2026, 9, 23, 6, 42, 3, 604381),
        "fg_app",
        columns(
            "fg_app",
            logid="1059028704",
            sessionid=5304127,
            proto=6,
            srcip="172.20.42.76",
            srcport=52814,
            dstip="203.0.113.75",
            dstport=443,
            app="Example.Docs",
            appid=99101,
            appcat="General.Interest",
            apprisk="elevated",
            action="pass",
            hostname="docs.example.org",
            url="/",
            extra={
                **COMMON_EXTRA,
                **ROLES,
                "subtype": "app-ctrl",
                "eventtype": "signature",
                "level": "information",
                "srccountry": "Reserved",
                "dstcountry": "Ireland",
                "srcintf": "port2",
                "dstintf": "wan1",
                "service": "SSL",
                "direction": "incoming",
                "policyid": "12",
                "poluuid": POLUUID,
                "policytype": "policy",
                "applist": "APP-PROFILE",
                "incidentserialno": "100000417",
                "msg": "General.Interest: Example.Docs",
            },
        ),
    ),
    (
        23 * 8 + 5,
        5,
        utc(2026, 9, 23, 6, 43, 30, 71596),
        "fg_traffic",
        columns(
            "fg_traffic",
            logid="0000000020",
            subtype="forward",
            action="accept",
            policyid=12,
            policyname="internet-out",
            sessionid=5302210,
            proto=6,
            srcip="172.20.60.163",
            srcport=60311,
            srcintf="port2",
            dstip="203.0.113.91",
            dstport=443,
            dstintf="wan1",
            transip="100.64.12.9",
            transport=60311,
            trandisp="snat",
            service="Example-Storage",
            dstinetsvc="Example-Storage",
            app="unknown-99102",
            appid=99102,
            appcat="unknown",
            dstcountry="Sweden",
            duration=600,
            sentbyte=48230,
            rcvdbyte=1203877,
            sentpkt=412,
            rcvdpkt=905,
            sentdelta=3120,
            rcvddelta=96544,
            durationdelta=120,
            is_interim=True,
            extra={
                **COMMON_EXTRA,
                **ROLES,
                "level": "notice",
                "srccountry": "Reserved",
                "dstregion": "Undefined",
                "dstcity": "Undefined",
                "dstreputation": "5",
                "policytype": "policy",
                "poluuid": POLUUID,
                "applist": "APP-PROFILE",
                "appact": "detected",
                "sentpktdelta": "31",
                "rcvdpktdelta": "74",
            },
        ),
    ),
    (
        23 * 8 + 4,
        4,
        utc(2026, 9, 23, 6, 43, 52, 937210),
        "fg_traffic",
        columns(
            "fg_traffic",
            logid="0000000011",
            subtype="forward",
            action="ip-conn",
            policyid=12,
            policyname="internet-out",
            sessionid=5304190,
            proto=6,
            srcip="172.20.43.159",
            srcport=58040,
            srcintf="port2",
            dstip="198.51.100.64",
            dstport=443,
            dstintf="wan1",
            service="Example-Hosting",
            dstinetsvc="Example-Hosting",
            dstcountry="Poland",
            crscore=5,
            extra={
                **COMMON_EXTRA,
                **ROLES,
                "level": "warning",
                "srccountry": "Reserved",
                "dstregion": "Undefined",
                "dstcity": "Undefined",
                "policytype": "policy",
                "poluuid": POLUUID,
                "craction": "262144",
                "crlevel": "low",
                "msg": "Connection Failed",
            },
        ),
    ),
    (
        23 * 8 + 5,
        5,
        utc(2026, 9, 23, 6, 44, 20, 455128),
        "fg_traffic",
        columns(
            "fg_traffic",
            logid="0001000014",
            subtype="local",
            action="close",
            policyid=0,
            sessionid=5304201,
            proto=6,
            srcip="100.64.12.9",
            srcport=16502,
            srcintf="root",
            dstip="203.0.113.140",
            dstport=443,
            dstintf="wan1",
            trandisp="noop",
            service="HTTPS",
            app="HTTPS",
            dstcountry="United States",
            duration=3,
            sentbyte=2260,
            rcvdbyte=5718,
            sentpkt=9,
            rcvdpkt=8,
            extra={**COMMON_EXTRA, **ROLES, "level": "notice", "srccountry": "Reserved"},
        ),
    ),
    (
        23 * 8 + 5,
        5,
        utc(2026, 9, 23, 6, 44, 41, 312774),
        "fg_app",
        columns(
            "fg_app",
            logid="1700062306",
            sessionid=5304215,
            proto=6,
            srcip="172.20.60.163",
            srcport=60388,
            dstip="203.0.113.92",
            dstport=443,
            action="bypass",
            hostname="sync.example.net",
            extra={
                **COMMON_EXTRA,
                **ROLES,
                "subtype": "ssl",
                "eventtype": "ssl-anomaly",
                "level": "notice",
                "policyid": "12",
                "poluuid": POLUUID,
                "policytype": "policy",
                "service": "SSL",
                "profile": "certificate-inspection",
                "srccountry": "Reserved",
                "dstcountry": "Sweden",
                "srcintf": "port2",
                "dstintf": "wan1",
                "srcuuid": "00000000-0000-0000-0000-0000000000aa",
                "dstuuid": "00000000-0000-0000-0000-0000000000bb",
                "sni": "sync.example.net",
                "eventsubtype": "certificate-probe-failed",
                "msg": "SSL connection is bypassed due to unable to retrieve server's certificate",
            },
        ),
    ),
    (
        23 * 8 + 5,
        5,
        utc(2026, 9, 23, 6, 44, 57, 482913),
        "fg_traffic",
        columns(
            "fg_traffic",
            logid="0000000013",
            subtype="forward",
            action="close",
            policyid=12,
            policyname="internet-out",
            sessionid=5304127,
            proto=6,
            srcip="172.20.42.76",
            srcport=52814,
            srcintf="port2",
            dstip="203.0.113.75",
            dstport=443,
            dstintf="wan1",
            transip="100.64.12.9",
            transport=52814,
            trandisp="snat",
            service="Example-Docs",
            dstinetsvc="Example-Docs",
            app="Example.Docs",
            appid=99101,
            appcat="General.Interest",
            apprisk="elevated",
            dstcountry="Ireland",
            duration=174,
            sentbyte=6083,
            rcvdbyte=48210,
            sentpkt=41,
            rcvdpkt=52,
            utmaction="allow",
            extra={
                **COMMON_EXTRA,
                **ROLES,
                "level": "notice",
                "srccountry": "Reserved",
                "dstregion": "Undefined",
                "dstcity": "Undefined",
                "dstreputation": "4",
                "policytype": "policy",
                "poluuid": POLUUID,
                "applist": "APP-PROFILE",
                "appact": "detected",
                "countapp": "1",
            },
        ),
    ),
]
MESSAGE_TYPES = [
    ("event", "system"),
    ("utm", "app-ctrl"),
    ("traffic", "forward"),
    ("traffic", "forward"),
    ("traffic", "local"),
    ("utm", "ssl"),
    ("traffic", "forward"),
]


@pytest.mark.parametrize("index", range(7))
def test_sample_line_golden(index: int) -> None:
    pri, severity, time, table, expected = GOLDEN[index]
    raw = SAMPLES[index]
    outcome = parse(raw)
    assert outcome.error is None
    assert raw.startswith(f"<{pri}>".encode())
    message = outcome.message
    assert (message["type"], message["subtype"]) == MESSAGE_TYPES[index]
    assert message["severity"] == severity
    assert message["facility"] == 23  # local7
    assert message["host"] == "FIREWALL-01"
    assert message["program"] is None
    assert message["text"] == raw.decode().split(">", 1)[1]
    assert message["extra"] == {}
    assert message["parse_error"] is None
    assert message["time"] == time
    assert message["time_received"] == RECEIVED
    assert outcome.rows[0].values["time"] == time
    assert typed(outcome) == (table, expected)


@pytest.mark.parametrize("raw", SAMPLES, ids=[f"line{i + 1}" for i in range(7)])
def test_eventtime_agrees_with_local_date_time_and_tz(raw: bytes) -> None:
    """Spec 2.5: eventtime is UTC nanoseconds. Fails loudly if a format sample disagrees."""
    fields = tokenize(raw.decode().split(">", 1)[1]).fields
    from_eventtime = eventtime_utc(fields["eventtime"])
    from_local = local_time(fields["date"], fields["time"], fields["tz"])
    assert from_eventtime is not None
    assert from_local is not None
    assert len(fields["eventtime"]) == 19  # nanoseconds on FortiOS 7.6
    assert abs(from_eventtime - from_local) < timedelta(seconds=1)


@pytest.mark.parametrize("raw", SAMPLES + SYNTHETIC)
def test_no_field_is_lost(raw: bytes) -> None:
    """Every key of the line is a column, in extra, or represented by messages/time."""
    outcome = parse(raw)
    body = raw.decode().split(">", 1)[1]
    fields = tokenize(body[body.index("date=") :]).fields
    envelope = {"type", "devname", "subtype"}
    if outcome.message["time"] != RECEIVED:
        envelope |= {"eventtime", "date", "time", "tz"}
    if not outcome.rows:
        stored = outcome.message["extra"]["fields"]
        assert {k: v for k, v in fields.items() if k not in envelope} == stored
        return
    table, values = typed(outcome)
    mapped = {key: column for column, key, _ in SPECS[table]}
    macs = {key for _, key, kind in SPECS[table] if kind == "mac"}
    for key, value in fields.items():
        if key in values["extra"]:
            assert values["extra"][key] == value
        elif key in macs:  # normalised: lower case with colons (values.to_mac)
            assert values[mapped[key]] == to_mac(value), key
        elif key in mapped:
            column_value = values[mapped[key]]
            assert str(column_value) == value or column_value == value, key
        elif key == "ipaddr":
            assert values["answers"] is not None
        else:
            assert key in envelope, key


def test_interim_logids_are_configurable() -> None:
    assert typed(parse(INTERIM))[1]["is_interim"] is True
    assert typed(parse(CLOSE))[1]["is_interim"] is False
    custom = FortiGateParser(interim_logids={"0000000013"})
    assert typed(parse(CLOSE, custom))[1]["is_interim"] is True
    assert typed(parse(INTERIM, custom))[1]["is_interim"] is False


# -- time ----------------------------------------------------------------------------------------

EXPECTED = datetime(2026, 9, 23, 6, 44, 57, 482913, tzinfo=UTC)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1790145897482913706", EXPECTED),  # ns (FortiOS 7.6)
        ("1790145897123456789", EXPECTED.replace(microsecond=123456)),  # ns: truncated
        ("1790145897482913", EXPECTED),  # µs
        ("1790145897123456", EXPECTED.replace(microsecond=123456)),  # µs
        ("1790145897123", EXPECTED.replace(microsecond=123000)),  # ms
        ("1790145897", EXPECTED.replace(microsecond=0)),  # s
        ("1000000000", utc(2001, 9, 9, 1, 46, 40)),  # smallest accepted
        ("99999999999999999999", utc(5138, 11, 16, 9, 46, 39, 999999)),  # largest ns
        ("999999999", None),  # before 2001: not a plausible clock
        ("100000000000000000000", None),  # 21 digits
        ("0", None),
        ("-1790145897", None),
        ("1790145897.5", None),
        ("", None),
        ("\uff11\uff17\uff19\uff10\uff11\uff14\uff15\uff18\uff19\uff17", None),  # full-width
    ],
)
def test_eventtime_magnitudes(text: str, expected: datetime | None) -> None:
    assert eventtime_utc(text) == expected


@pytest.mark.parametrize(
    ("text", "minutes"),
    [("+0300", 180), ("+03:00", 180), ("-0130", -90), ("Z", 0), ("+0000", 0), ("-0000", 0)],
)
def test_offsets(text: str, minutes: int) -> None:
    assert offset_of(text) == timezone(timedelta(minutes=minutes))


@pytest.mark.parametrize("text", ["", "+3", "+2400", "+0360", "UTC", "+03:00:00", "+٠٣٠٠"])
def test_bad_offsets(text: str) -> None:
    assert offset_of(text) is None


def test_local_time_rules() -> None:
    assert local_time("2026-09-23", "09:44:57", "+0300") == utc(2026, 9, 23, 6, 44, 57)
    assert local_time("2026-09-23", "09:44:57", None) is None
    assert local_time("2026-13-23", "09:44:57", "+0300") is None
    assert local_time("2026-09-23", "09:44:57+01:00", "+0300") is None  # do not pick one


def test_time_falls_back_to_date_time_tz_when_eventtime_is_unusable() -> None:
    line = CLOSE.replace(b"eventtime=1790145897482913706", b"eventtime=garbage")
    outcome = parse(line)
    assert outcome.message["time"] == utc(2026, 9, 23, 6, 44, 57)
    _, values = typed(outcome)
    assert values["extra"]["eventtime"] == "garbage"  # kept: not used
    assert "date" not in values["extra"]  # used
    # An eventtime that is not a time is counted (it used to fall back silently).
    assert (outcome.error, outcome.detail) == ("fortigate.value", "invalid value: eventtime")
    empty = parse(CLOSE.replace(b"eventtime=1790145897482913706", b"eventtime="))
    assert empty.error is None  # empty = absent
    assert empty.message["time"] == utc(2026, 9, 23, 6, 44, 57)


def test_implausible_eventtime_falls_back_to_date_time_tz() -> None:
    """11 digits read as seconds = year 5138: a wrong unit or a broken clock, not an event."""
    line = CLOSE.replace(b"eventtime=1790145897482913706", b"eventtime=99999999999")
    outcome = parse(line)
    assert outcome.message["time"] == utc(2026, 9, 23, 6, 44, 57)  # from date/time/tz
    assert outcome.error == "fortigate.time"
    assert outcome.detail == "implausible device time: eventtime=99999999999"
    _, values = typed(outcome)
    assert values["extra"]["eventtime"] == "99999999999"  # not used: kept
    assert "date" not in values["extra"]
    assert outcome.message["extra"] == {}  # a device time was used


def test_implausible_device_time_uses_the_receive_time() -> None:
    line = CLOSE.replace(b"eventtime=1790145897482913706", b"eventtime=1000000000").replace(  # 2001
        b"date=2026-09-23", b"date=2020-01-01"
    )
    outcome = parse(line)
    assert outcome.message["time"] == RECEIVED
    assert outcome.rows[0].values["time"] == RECEIVED
    assert outcome.error == "fortigate.time"
    assert outcome.message["extra"] == {"device_time": "2020-01-01 09:44:57 +0300"}
    _, values = typed(outcome)
    assert (values["extra"]["eventtime"], values["extra"]["date"]) == ("1000000000", "2020-01-01")
    assert (values["extra"]["time"], values["extra"]["tz"]) == ("09:44:57", "+0300")
    only_eventtime = parse(
        CLOSE.replace(b"eventtime=1790145897482913706", b"eventtime=1000000000").replace(
            b"date=2026-09-23 time=09:44:57 ", b""
        )
    )
    assert only_eventtime.message["time"] == RECEIVED
    assert only_eventtime.message["extra"] == {"device_time": "1000000000"}
    assert only_eventtime.error == "fortigate.time"


@pytest.mark.parametrize(
    ("date_text", "time_text", "tz", "in_utc"),
    [
        ("0001-01-01", "00:00:00", "+0300", None),  # UTC would be year 0: OverflowError before
        ("0001-01-01", "00:00:00", "+2359", None),
        ("0001-01-01", "00:00:00", "-0300", utc(1, 1, 1, 3)),
        ("9999-12-31", "23:59:59", "-0300", None),  # UTC would be year 10000
        ("9999-12-31", "23:59:59", "-2359", None),
        ("9999-12-31", "23:59:59", "+0000", utc(9999, 12, 31, 23, 59, 59)),
    ],
)
def test_extreme_device_times_never_raise(
    date_text: str, time_text: str, tz: str, in_utc: datetime | None
) -> None:
    line = (
        f'<189>date={date_text} time={time_text} devname="X" tz="{tz}" logid="0000000013" '
        'type="traffic" subtype="forward" srcip=172.20.10.37'
    ).encode()
    outcome = parse(line)
    assert outcome.message["time"] == RECEIVED
    assert outcome.error == "fortigate.time"
    assert outcome.message["extra"] == {"device_time": f"{date_text} {time_text} {tz}"}
    assert typed(outcome)[1]["srcip"] == "172.20.10.37"
    # Without a skew limit only what UTC can represent is used.
    unlimited = parse(line, FortiGateParser(max_clock_skew=None))
    assert unlimited.message["time"] == (in_utc or RECEIVED)
    assert unlimited.error == (None if in_utc else "fortigate.time")


def test_clock_skew_is_configurable() -> None:
    hour_late = CLOSE.replace(b"eventtime=1790145897482913706 ", b"").replace(
        b"time=09:44:57", b"time=10:44:57"
    )
    assert parse(hour_late).message["time"] == utc(2026, 9, 23, 7, 44, 57)  # default: 1 day
    strict = FortiGateParser(max_clock_skew=timedelta(minutes=5))
    assert parse(hour_late, strict).error == "fortigate.time"
    assert parse(hour_late, strict).message["time"] == RECEIVED
    old = CLOSE.replace(b"eventtime=1790145897482913706 ", b"").replace(
        b"date=2026-09-23", b"date=2020-01-01"
    )
    anything = FortiGateParser(max_clock_skew=None)
    assert parse(old, anything).message["time"] == utc(2020, 1, 1, 6, 44, 57)
    assert parse(old, anything).error is None


def test_invalid_date_with_an_offset_is_a_value_failure() -> None:
    outcome = parse(
        CLOSE.replace(b"eventtime=1790145897482913706 ", b"").replace(
            b"date=2026-09-23", b"date=2026-13-45"
        )
    )
    assert (outcome.error, outcome.detail) == ("fortigate.value", "invalid value: date/time")
    assert outcome.message["time"] == RECEIVED
    assert outcome.message["extra"] == {"device_time": "2026-13-45 09:44:57"}


def test_time_without_offset_is_not_guessed() -> None:
    line = CLOSE.replace(b"eventtime=1790145897482913706 ", b"").replace(b' tz="+0300"', b"")
    outcome = parse(line)
    assert outcome.message["time"] == RECEIVED
    assert outcome.message["extra"] == {"device_time": "2026-09-23 09:44:57"}
    _, values = typed(outcome)
    assert values["extra"]["date"] == "2026-09-23"
    assert values["extra"]["time"] == "09:44:57"
    assert outcome.error is None


def test_missing_date_uses_eventtime_and_missing_both_uses_receive_time() -> None:
    no_date = CLOSE.replace(b"date=2026-09-23 time=09:44:57 ", b"")
    assert parse(no_date).message["time"] == EXPECTED
    neither = no_date.replace(b"eventtime=1790145897482913706 ", b"")
    outcome = parse(neither)
    assert outcome.message["time"] == RECEIVED
    assert outcome.message["extra"] == {}
    assert typed(outcome)[1]["extra"]["tz"] == "+0300"


# -- synthetic types (verify on site) ------------------------------------------------------------


def synthetic(logid: str) -> ParseOutcome:
    (line,) = [line for line in SYNTHETIC if f'logid="{logid}"'.encode() in line]
    return parse(line)


def test_webfilter_blocked() -> None:
    outcome = synthetic("9900000001")
    table, values = typed(outcome)
    assert table == "fg_web"
    assert values == columns(
        "fg_web",
        logid="9900000001",
        sessionid=3100101,
        srcip="172.20.42.120",
        srcport=51544,
        dstip="203.0.113.10",
        dstport=443,
        hostname="games.example.com",
        url="/play?id=7&lang=tr",
        cat=901,
        catdesc="Synthetic Category A",
        action="blocked",
        eventtype="ftgd_blk",
        profile="WEB-PROFILE",
        extra={
            **COMMON_EXTRA,
            "level": "warning",
            "policyid": "12",
            "srcintf": "port2",
            "dstintf": "wan1",
            "proto": "6",
            "service": "HTTPS",
            "msg": "URL belongs to a denied category in policy",
        },
    )
    assert (outcome.message["type"], outcome.message["subtype"]) == ("utm", "webfilter")
    assert outcome.message["severity"] == 4


def test_dns_query_and_response_answers() -> None:
    _, query = typed(synthetic("9900000003"))
    assert (query["qname"], query["qtype"], query["eventtype"]) == (
        "www.example.com",
        "A",
        "dns-query",
    )
    assert query["answers"] is None
    table, response = typed(synthetic("9900000004"))
    assert table == "fg_dns"
    assert response["answers"] == ["203.0.113.20", "203.0.113.21"]
    assert "ipaddr" not in response["extra"]  # fully represented by answers
    assert (response["cat"], response["catdesc"], response["action"]) == (
        902,
        "Synthetic Category B",
        "pass",
    )
    _, blocked = typed(synthetic("9900000005"))
    assert (blocked["action"], blocked["qtype"]) == ("block", "AAAA")


def test_dns_answers_with_a_bad_token_keep_the_raw_list() -> None:
    line = SYNTHETIC[3].replace(
        b'"203.0.113.20, 203.0.113.21"', b'"203.0.113.20;bogus 2001:db8::1"'
    )
    outcome = parse(line)
    _, values = typed(outcome)
    assert values["answers"] == ["203.0.113.20", "2001:db8::1"]
    assert values["extra"]["ipaddr"] == "203.0.113.20;bogus 2001:db8::1"
    assert outcome.error == "fortigate.value"
    empty = parse(SYNTHETIC[3].replace(b'"203.0.113.20, 203.0.113.21"', b'""'))
    assert typed(empty)[1]["answers"] is None
    assert typed(empty)[1]["extra"]["ipaddr"] == ""
    assert empty.error is None


def test_ips_and_virus() -> None:
    table, ips = typed(synthetic("9900000006"))
    assert table == "fg_ips"
    assert ips == columns(
        "fg_ips",
        logid="9900000006",
        eventtype="signature",
        proto=6,
        sessionid=3100106,
        srcip="198.51.100.7",
        srcport=40123,
        dstip="100.64.12.9",
        dstport=443,
        service="HTTPS",
        attack="Synthetic.Signature.Remote.Code.Execution",
        attackid=99001,
        severity="high",
        action="dropped",
        msg="web_server: Synthetic.Signature.Remote.Code.Execution",
        extra={
            **COMMON_EXTRA,
            "level": "alert",
            "srcintf": "wan1",
            "dstintf": "root",
            "policyid": "0",
        },
    )
    table, virus = typed(synthetic("9900000007"))
    assert table == "fg_virus"
    assert (virus["virus"], virus["virusid"], virus["dtype"], virus["filename"]) == (
        "Synthetic.Test.File",
        99002,
        "Virus",
        "eicar.com",
    )
    assert (virus["url"], virus["hostname"], virus["action"]) == (
        "http://downloads.example.net/eicar.com",
        "downloads.example.net",
        "blocked",
    )
    assert (virus["srcip"], virus["dstip"]) == ("203.0.113.30", "172.20.41.110")


def test_admin_logins_and_configuration_change() -> None:
    table, ok = typed(synthetic("9900000008"))
    assert table == "fg_event"
    assert ok == columns(
        "fg_event",
        logid="9900000008",
        subtype="system",
        level="information",
        logdesc="Admin login successful",
        action="login",
        status="success",
        user_name="admin",
        ui="https(172.20.10.37)",
        srcip="172.20.10.37",
        dstip="172.31.255.1",
        msg="Administrator admin logged in successfully from https(172.20.10.37)",
        extra=COMMON_EXTRA,
    )
    _, failed = typed(synthetic("9900000009"))
    assert (failed["logdesc"], failed["status"], failed["srcip"], failed["user_name"]) == (
        "Admin login failed",
        "failed",
        "172.20.41.196",
        "admin",
    )
    assert failed["extra"]["reason"] == "passwd_invalid"
    _, change = typed(synthetic("9900000010"))
    assert (change["cfgpath"], change["cfgobj"], change["cfgattr"], change["action"]) == (
        "firewall.policy",
        "12",
        "action[accept->deny]",
        "Edit",
    )
    assert change["logdesc"] == "Configuration changed"


def test_vpn_and_user_events_keep_unmapped_fields() -> None:
    _, vpn = typed(synthetic("9900000011"))
    assert (vpn["subtype"], vpn["user_name"], vpn["action"]) == ("vpn", "remote01", "tunnel-up")
    assert vpn["extra"]["remip"] == "198.51.100.50"
    assert vpn["extra"]["tunnelip"] == "172.16.99.10"
    _, user = typed(synthetic("9900000012"))
    assert (user["subtype"], user["status"], user["srcip"]) == ("user", "failure", "198.51.100.50")
    assert user["extra"]["authproto"] == "SSL-VPN"


def test_synthetic_eventtime_units_and_fallbacks() -> None:
    microseconds, seconds, no_eventtime, no_tz, prefixed = (
        parse(line) for line in SYNTHETIC[12:17]
    )
    assert microseconds.message["time"] == utc(2026, 9, 23, 6, 46, 13, 500000)
    assert seconds.message["time"] == utc(2026, 9, 23, 6, 46, 14)
    assert no_eventtime.message["time"] == utc(2026, 9, 23, 6, 46, 15)
    assert no_tz.message["time"] == RECEIVED
    assert no_tz.message["extra"] == {"device_time": "2026-09-23 09:46:16"}
    assert prefixed.message["extra"] == {"prefix": "Sep 23 09:46:17 172.31.255.1"}
    assert prefixed.message["time"] == utc(2026, 9, 23, 6, 46, 17)
    assert prefixed.error is None


def test_forward_traffic_with_srcmac() -> None:
    """ADR-0016: ``srcmac`` is a column (lower case with colons); ``mastersrcmac`` and
    ``srcmacvendor`` stay in ``extra`` (FortiGate DHCP lines: test_fortigate_dhcp.py)."""
    (line,) = [line for line in SYNTHETIC if b"sessionid=3100119 " in line]
    outcome = parse(line)
    assert outcome.error is None
    assert (outcome.message["type"], outcome.message["subtype"]) == ("traffic", "forward")
    assert outcome.message["time"] == utc(2026, 9, 23, 6, 46, 25)
    assert typed(outcome) == (
        "fg_traffic",
        columns(
            "fg_traffic",
            logid="0000000013",
            subtype="forward",
            action="close",
            policyid=12,
            policyname="internet-out",
            proto=6,
            sessionid=3100119,
            srcip="172.20.42.57",
            srcport=50005,
            dstip="203.0.113.44",
            dstport=443,
            srcintf="vlan42",
            srcmac="00:00:5e:00:53:57",
            dstintf="wan1",
            transip="100.64.12.9",
            transport=50005,
            trandisp="snat",
            service="HTTPS",
            duration=30,
            sentbyte=1200,
            rcvdbyte=34000,
            sentpkt=12,
            rcvdpkt=30,
            extra={
                **COMMON_EXTRA,
                "level": "notice",
                "mastersrcmac": "00:00:5e:00:53:57",
                "srcmacvendor": "sentetik",
            },
        ),
    )


def test_unmapped_subtype_keeps_every_field_in_messages_extra() -> None:
    outcome = synthetic("9900000018")
    assert outcome.rows == ()
    assert outcome.error is None
    assert (outcome.message["type"], outcome.message["subtype"]) == ("utm", "dlp")
    fields = outcome.message["extra"]["fields"]
    assert fields["filename"] == "teklif.xlsx"
    assert fields["sessionid"] == "3100118"
    assert "eventtime" not in fields  # used for time
    assert "subtype" not in fields  # in messages.subtype


@pytest.mark.parametrize(
    ("log_type", "subtype", "table"),
    [
        ("traffic", "forward", "fg_traffic"),
        ("traffic", "sniffer", "fg_traffic"),
        ("event", "vpn", "fg_event"),
        ("event", "system", "fg_event"),  # a DHCP server line is routed by its shape later
        ("utm", "webfilter", "fg_web"),
        ("utm", "dns", "fg_dns"),
        ("utm", "app-ctrl", "fg_app"),
        ("utm", "ssl", "fg_app"),
        ("utm", "ips", "fg_ips"),
        ("utm", "virus", "fg_virus"),
        ("utm", "dlp", None),
        ("utm", None, None),
        ("anomaly", "anomaly", None),
        (None, "forward", None),
    ],
)
def test_table_for(log_type: str | None, subtype: str | None, table: str | None) -> None:
    assert table_for(log_type, subtype) == table


# -- failures and tolerance ----------------------------------------------------------------------


def test_invalid_column_value_stays_in_extra() -> None:
    line = CLOSE.replace(b"srcip=172.20.42.76", b'srcip="n/a"').replace(b"proto=6", b"proto=99999")
    outcome = parse(line)
    assert outcome.error == "fortigate.value"
    assert outcome.detail == "invalid value: proto, srcip"
    _, values = typed(outcome)
    assert (values["srcip"], values["proto"]) == (None, None)
    assert (values["extra"]["srcip"], values["extra"]["proto"]) == ("n/a", "99999")
    assert outcome.message["parse_error"] == "fortigate.value"


def test_empty_values_are_null_but_kept() -> None:
    outcome = parse(
        CLOSE.replace(b"srcip=172.20.42.76", b"srcip=").replace(b"duration=174", b'duration=""')
    )
    assert outcome.error is None
    _, values = typed(outcome)
    assert (values["srcip"], values["duration"]) == (None, None)
    assert (values["extra"]["srcip"], values["extra"]["duration"]) == ("", "")


def test_empty_text_values_are_null_but_kept() -> None:
    """An empty hostname must not win the usage join (spec 10.2) over the DNS fallback."""
    outcome = parse(
        APP.replace(b'hostname="docs.example.org"', b'hostname=""')
        .replace(b'url="/"', b"url=")
        .replace(b'logid="1059028704"', b'logid=""')
    )
    assert outcome.error is None
    _, values = typed(outcome)
    assert (values["hostname"], values["url"], values["logid"]) == (None, None, None)
    assert (values["extra"]["hostname"], values["extra"]["url"]) == ("", "")
    assert values["extra"]["logid"] == ""
    dns = parse(SYNTHETIC[2].replace(b'qname="www.example.com"', b'qname=""'))
    assert typed(dns)[1]["qname"] is None
    assert typed(dns)[1]["extra"]["qname"] == ""
    event = parse(PERF.replace(b'devname="FIREWALL-01"', b'devname=""'))
    assert event.message["host"] is None
    assert typed(event)[1]["extra"]["devname"] == ""
    no_subtype = parse(PERF.replace(b'subtype="system"', b'subtype=""'))
    assert no_subtype.message["subtype"] is None
    assert typed(no_subtype)[1]["subtype"] is None
    assert typed(no_subtype)[1]["extra"]["subtype"] == ""
    dlp = parse(SYNTHETIC[17].replace(b'subtype="dlp"', b'subtype=""'))
    assert dlp.message["subtype"] is None
    assert dlp.message["extra"]["fields"]["subtype"] == ""


def test_duplicate_keys_first_in_column_all_in_extra() -> None:
    outcome = parse(CLOSE + b' srcport=1 policyname="second"')
    assert outcome.error is None
    _, values = typed(outcome)
    assert values["srcport"] == 52814
    assert values["policyname"] == "internet-out"
    assert values["extra"]["srcport"] == ["52814", "1"]
    assert values["extra"]["policyname"] == ["internet-out", "second"]


def test_trailing_garbage_and_truncated_line() -> None:
    garbage = parse(CLOSE + b" #012garbage tokens")
    assert garbage.error == "fortigate.unparsed"
    assert garbage.message["extra"] == {"unparsed": ["#012garbage", "tokens"]}
    assert typed(garbage)[1]["srcip"] == "172.20.42.76"
    cut = parse(PERF[:-20])
    assert cut.error == "fortigate.unterminated"
    assert cut.message["extra"] == {"unterminated": "msg"}
    assert typed(cut)[1]["msg"].startswith("Performance statistics")


def test_line_without_pairs_is_a_parse_failure_with_the_generic_view() -> None:
    outcome = parse(b"<189>Sep 23 09:44:57 fw hello world")
    assert outcome.error == "fortigate.kv"
    assert outcome.rows == ()
    assert outcome.message["type"] == "syslog"
    assert outcome.message["text"] == "hello world"


def test_line_without_type() -> None:
    outcome = parse(b'<189>logid="0000000013" srcip=1.2.3.4 devname="X"')
    assert outcome.error == "fortigate.type"
    assert outcome.rows == ()
    assert outcome.message["type"] == "unknown"
    assert outcome.message["host"] == "X"
    assert outcome.message["extra"] == {"fields": {"logid": "0000000013", "srcip": "1.2.3.4"}}
    empty = parse(b'<189>type="" subtype="forward" srcip=1.2.3.4')
    assert empty.error == "fortigate.type"
    assert empty.message["type"] == "unknown"
    assert empty.message["extra"] == {"fields": {"type": "", "srcip": "1.2.3.4"}}


def test_no_pri_uses_level_for_severity() -> None:
    outcome = parse(FAILED.split(b">", 1)[1])
    assert outcome.message["severity"] == 4  # level="warning"
    assert outcome.message["facility"] is None
    assert parse(APP.split(b">", 1)[1]).message["severity"] == 6  # information


def test_long_url_is_truncated() -> None:
    url = "/" + "a" * (URL_MAX + 500)
    line = SYNTHETIC[0].replace(b'url="/play?id=7&lang=tr"', f'url="{url}"'.encode())
    outcome = parse(line)
    _, values = typed(outcome)
    assert values["url"] == url[:URL_MAX]
    assert outcome.message["extra"] == {"truncated": ["url"]}
    assert outcome.message["text"] == line.decode().split(">", 1)[1][:2000]


def test_ssl_log_without_hostname_uses_sni() -> None:
    line = SSL.replace(b' hostname="sync.example.net"', b"")
    _, values = typed(parse(line))
    assert values["hostname"] == "sync.example.net"
    assert values["extra"]["sni"] == "sync.example.net"
    empty = SSL.replace(b'hostname="sync.example.net"', b'hostname=""')
    _, values = typed(parse(empty))
    assert values["hostname"] == "sync.example.net"  # an empty hostname is absent
    assert values["extra"]["hostname"] == ""
    assert values["extra"]["sni"] == "sync.example.net"


def test_glued_messages_keep_the_first_and_are_counted() -> None:
    outcome = parse(CLOSE + INTERIM)  # a framing error upstream: two datagrams in one line
    assert outcome.error == "fortigate.glued"
    assert outcome.detail is not None
    assert outcome.detail.startswith("two messages in one line? repeated keys: ")
    assert {"devname", "logid", "time"} <= set(outcome.detail.split(": ", 1)[1].split(", "))
    _, values = typed(outcome)
    assert (values["sessionid"], values["transport"]) == (5304127, 52814)  # the first message
    assert values["extra"]["sessionid"] == ["5304127", "5302210"]  # the second one is kept
    assert values["is_interim"] is False
    with_space = parse(CLOSE + b" " + INTERIM)
    assert with_space.error == "fortigate.glued"
    one_repeated_key = parse(CLOSE + b' logid="0000000013"')
    assert one_repeated_key.error is None  # a single duplicate is FortiOS's business


def test_backslash_before_closing_quote_is_read_literally_when_that_closes_the_line() -> None:
    """Whether FortiOS escapes backslashes is unverified: a Windows directory ending in a
    backslash must not swallow the rest of the line."""
    line = SYNTHETIC[6].replace(b'filename="eicar.com"', rb'filename="C:\Temp\"')
    outcome = parse(line)
    assert outcome.error is None
    assert outcome.message["extra"] == {"backslashes": "literal"}
    _, values = typed(outcome)
    assert values["filename"] == r"C:\Temp" + "\\"
    assert values["virus"] == "Synthetic.Test.File"  # fields after it survive
    escaped = parse(SYNTHETIC[6].replace(b'filename="eicar.com"', rb'filename="a \"b\" c"'))
    assert escaped.error is None
    assert escaped.message["extra"] == {}
    assert typed(escaped)[1]["filename"] == 'a "b" c'
    cut = parse(PERF[:-20] + b"\\")  # truncated: neither reading closes the quote
    assert cut.error == "fortigate.unterminated"
    assert "backslashes" not in cut.message["extra"]


def test_ipv6_addresses_are_canonical() -> None:
    line = CLOSE.replace(b"dstip=203.0.113.75", b"dstip=2001:DB8:0:0::1")
    assert typed(parse(line))[1]["dstip"] == "2001:db8::1"
    zone = parse(CLOSE.replace(b"dstip=203.0.113.75", b"dstip=fe80::1%port2"))
    assert zone.error == "fortigate.value"


def test_every_spec_column_exists_in_the_model() -> None:
    from arcilogger_db.models import Base

    for table, spec in SPECS.items():
        model_columns = set(Base.metadata.tables[table].columns.keys())
        assert {column for column, _, _ in spec} <= model_columns, table
    # Spec 7.3 fg_traffic list and srcmac (ADR-0016), all present as columns (the rest of the
    # line goes to extra).
    assert {column for column, _, _ in FG_TRAFFIC} | {"is_interim"} == {
        "logid", "subtype", "action", "policyid", "policyname", "sessionid", "proto", "srcip",
        "srcport", "srcintf", "srcmac", "dstip", "dstport", "dstintf", "transip", "transport",
        "trandisp", "service", "dstinetsvc", "app", "appid", "appcat", "apprisk", "dstcountry",
        "duration", "sentbyte", "rcvdbyte", "sentpkt", "rcvdpkt", "sentdelta", "rcvddelta",
        "durationdelta", "is_interim", "utmaction", "crscore",
    }  # fmt: skip
