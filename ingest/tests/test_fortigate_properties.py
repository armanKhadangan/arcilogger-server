"""FortiGate parser properties (hypothesis) and throughput.

* Never raises (a parser exception would surface as ``fortigate.exception``).
* Always exactly one complete ``messages`` row; every row passes ``check_row``.
* No field is lost: every key of the line is a column, in ``extra`` or represented by the
  ``messages`` row / ``time`` (see the module docstring of ``parsers.fortigate``).
* Any device time, year 1 to 9999 with any offset: never raises; the event time is the device
  time when representable and within a day of the receive time, else the receive time.
* FortiGate DHCP lines (ADR-0016): any order and quoting of their keys gives the same row, and
  the bounded MAC cache (``_MACS``) never changes a result, whether it is empty, full or warm.
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_core.rawlog import RawRecord
from arcilogger_db.rowcheck import check_row
from arcilogger_ingest.parsers import LinePosition, ParseOutcome, decode_message, fortigate
from arcilogger_ingest.parsers.fortigate import SPECS, TABLES, FortiGateParser
from arcilogger_ingest.parsers.kv import format_pairs, quote, tokenize
from arcilogger_ingest.parsers.values import to_mac

SAMPLES = (
    (Path(__file__).parents[2] / "tests" / "fixtures" / "format" / "fortigate-sample.log")
    .read_bytes()
    .splitlines()
)
_SYNTHETIC = (
    (Path(__file__).parents[2] / "tests" / "fixtures" / "synthetic" / "fortigate-synthetic.log")
    .read_bytes()
    .splitlines()
)
#: The FortiGate DHCP server lines of the synthetic fixture (ADR-0016), each with its PRI.
DHCP_LINES = tuple(line for line in _SYNTHETIC if b'logid="01000260' in line)
#: A forward traffic line with ``srcmac`` (synthetic, ADR-0016).
SRCMAC_LINE = next(line for line in _SYNTHETIC if b' srcmac="00:00:5e:00:53:57"' in line)
RECEIVED = datetime(2026, 9, 23, 6, 44, 58, tzinfo=UTC)
PARSER = FortiGateParser()
KNOWN_KEYS = sorted(
    {key for spec in SPECS.values() for _, key, _ in spec}
    | {"date", "time", "tz", "eventtime", "devname", "ipaddr", "sni", "level"}
)
TYPES = sorted(
    {(log_type, subtype or "forward") for log_type, subtype in TABLES} | {("utm", "dlp")}
)
ENVELOPE = {"type", "devname", "subtype"}
TIME_KEYS = {"eventtime", "date", "time", "tz"}


def parse(message: bytes) -> ParseOutcome:
    record = RawRecord(received=RECEIVED, sender="172.31.255.1", message=message)
    return PARSER.parse(record, LinePosition("fortigate", "fortigate", date(2026, 9, 23), 1, 0))


def check_outcome(outcome: ParseOutcome) -> None:
    assert outcome.error != "fortigate.exception", outcome.detail
    rows = outcome.all_rows()
    assert rows[0].table == "messages"
    assert [row.table for row in rows].count("messages") == 1
    assert len(outcome.rows) <= 1
    for row in rows:
        assert check_row(row.table, row.values, complete=True) == [], row


def check_accounted(outcome: ParseOutcome, fields: dict[str, str]) -> None:
    """Every field is somewhere (``fields`` = first values of a line without duplicates)."""
    envelope = ENVELOPE | (TIME_KEYS if outcome.message["time"] != RECEIVED else set())
    if not outcome.rows:
        stored = outcome.message["extra"].get("fields", {})
        for key, value in fields.items():
            assert key in envelope or stored[key] == value, key
        return
    (row,) = outcome.rows
    values = row.values
    mapped = {key: column for column, key, _ in SPECS[row.table]}
    macs = {key for _, key, kind in SPECS[row.table] if kind == "mac"}
    for key, value in fields.items():
        if key in values["extra"]:
            assert values["extra"][key] == value, key
        elif key in macs:  # normalised: lower case with colons
            assert values[mapped[key]] == to_mac(value), key
        elif key in mapped:
            assert str(values[mapped[key]]) == value or values[mapped[key]] == value, key
        elif key == "ipaddr":
            assert values["answers"], key
        else:
            assert key in envelope, key


KEY = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=10)
#: MAC addresses as a FortiGate may print them, and near misses (mixed separators).
MAC_TEXT = st.builds(
    lambda octets, upper, separators: "".join(
        (octet.upper() if upper else octet) + separator
        for octet, separator in zip(octets, [*separators, ""], strict=True)
    ),
    st.lists(st.integers(0, 255).map("{:02x}".format), min_size=6, max_size=6),
    st.booleans(),
    st.sampled_from([[":"] * 5, ["-"] * 5, [":", ":", "-", ":", ":"], ["."] * 5]),
)
VALUE = st.one_of(
    st.text(max_size=30),
    st.integers(min_value=-(2**70), max_value=2**70).map(str),
    st.ip_addresses().map(str),
    MAC_TEXT,
    st.sampled_from(["", "1790145897482913706", "1790145897", "+0300", "2026-09-23", "09:44:57"]),
)


@settings(max_examples=400, deadline=None)
@given(
    kind=st.sampled_from(TYPES),
    known=st.dictionaries(st.sampled_from(KNOWN_KEYS), VALUE, max_size=25),
    unknown=st.dictionaries(KEY, VALUE, max_size=10),
    pri=st.integers(min_value=0, max_value=191) | st.none(),
)
def test_random_lines_parse_completely(
    kind: tuple[str, str], known: dict[str, str], unknown: dict[str, str], pri: int | None
) -> None:
    pairs: dict[str, str] = {"type": kind[0], "subtype": kind[1], **unknown, **known}
    pairs["type"], pairs["subtype"] = kind
    text = format_pairs(pairs.items())
    outcome = parse((f"<{pri}>" if pri is not None else "").encode() + text.encode())
    check_outcome(outcome)
    # The parser sees decoded text (NUL → U+FFFD, contract): compare with that.
    check_accounted(outcome, tokenize(decode_message(text.encode())).fields)


@settings(max_examples=300, deadline=None)
@given(
    index=st.integers(min_value=0, max_value=len(SAMPLES) - 1),
    unknown=st.dictionaries(KEY.filter(lambda k: k not in KNOWN_KEYS), st.text(max_size=40)),
)
def test_unknown_keys_are_preserved(index: int, unknown: dict[str, str]) -> None:
    body = SAMPLES[index].decode().split(">", 1)[1]
    existing = tokenize(body).fields
    added = {key: value for key, value in unknown.items() if key not in existing}
    outcome = parse(SAMPLES[index] + b" " + format_pairs(added.items()).encode())
    check_outcome(outcome)
    assert outcome.error is None
    extra: dict[str, Any] = outcome.rows[0].values["extra"]
    for key, value in added.items():
        assert extra[key] == decode_message(value.encode())  # NUL → U+FFFD, never lost


DATES = st.dates() | st.sampled_from([date.min, date.max])
TIMES = st.times().map(lambda t: t.replace(microsecond=0))
#: Offsets in minutes, ±23:59 (the largest a ``+HHMM`` offset can express).
OFFSETS = st.integers(min_value=-(23 * 60 + 59), max_value=23 * 60 + 59)
DAY = timedelta(days=1)


def device_utc(day: date, clock: dt_time, minutes: int) -> datetime | None:
    """Independent oracle: local wall time minus its offset, ``None`` outside 1..9999."""
    try:
        return datetime.combine(day, clock, tzinfo=UTC) - timedelta(minutes=minutes)
    except OverflowError:
        return None


@settings(max_examples=400, deadline=None)
@given(
    day=DATES,
    clock=TIMES,
    minutes=OFFSETS | st.sampled_from([23 * 60 + 59, -(23 * 60 + 59)]),
    eventtime=st.none() | st.integers(min_value=0, max_value=10**20 - 1).map(str),
)
def test_any_device_time(day: date, clock: dt_time, minutes: int, eventtime: str | None) -> None:
    sign = "-" if minutes < 0 else "+"
    tz = f"{sign}{abs(minutes) // 60:02d}{abs(minutes) % 60:02d}"
    pairs = [
        ("date", day.isoformat()),
        ("time", clock.isoformat()),
        ("tz", tz),
        ("logid", "0000000013"),
        ("type", "traffic"),
        ("subtype", "forward"),
        ("srcip", "172.20.10.37"),
    ]
    if eventtime is not None:
        pairs.append(("eventtime", eventtime))
    outcome = parse(b"<189>" + format_pairs(pairs).encode())
    check_outcome(outcome)
    used = outcome.message["time"]
    assert abs(used - RECEIVED) <= DAY  # whatever the line says
    assert outcome.rows[0].values["srcip"] == "172.20.10.37"
    if eventtime is None:
        device = device_utc(day, clock, minutes)
        plausible = device is not None and abs(device - RECEIVED) <= DAY
        assert used == (device if plausible else RECEIVED)
        assert outcome.error == (None if plausible else "fortigate.time")
        if not plausible:
            assert outcome.message["extra"]["device_time"] == f"{pairs[0][1]} {pairs[1][1]} {tz}"
    elif used == RECEIVED:
        assert outcome.error in ("fortigate.time", "fortigate.value")


@settings(max_examples=300, deadline=None)
@given(data=st.binary(max_size=2000) | st.text(max_size=2000).map(str.encode))
def test_any_bytes(data: bytes) -> None:
    check_outcome(parse(data))
    check_outcome(parse(b"<189>" + data))
    check_outcome(parse(SAMPLES[6][:200] + data))  # a traffic line cut short


def _typed(outcome: ParseOutcome) -> tuple[str, Mapping[str, Any]] | None:
    return (outcome.rows[0].table, outcome.rows[0].values) if outcome.rows else None


def _requoted(fields: list[tuple[str, str]], quoted: list[bool]) -> bytes:
    """``key=value`` pairs; a value is written unquoted only where the grammar allows it."""
    parts = []
    for (key, value), keep in zip(fields, quoted, strict=True):
        bare = value and not keep and not any(c.isspace() or c in '"\\' for c in value)
        parts.append(f"{key}={value}" if bare else f"{key}={quote(value)}")
    return " ".join(parts).encode()


@settings(max_examples=200, deadline=None)
@given(data=st.data(), index=st.integers(min_value=0, max_value=len(DHCP_LINES) - 1))
def test_any_order_and_quoting_of_a_dhcp_line_gives_the_same_row(
    data: st.DataObject, index: int
) -> None:
    line = DHCP_LINES[index]
    pri, body = line.split(b">", 1)
    pairs = list(tokenize(body.decode()).fields.items())
    order = data.draw(st.permutations(pairs))
    quoted = data.draw(st.lists(st.booleans(), min_size=len(order), max_size=len(order)))
    original, moved = parse(line), parse(pri + b">" + _requoted(order, quoted))
    check_outcome(moved)
    assert _typed(moved) == _typed(original)
    assert (moved.error, moved.detail) == (original.error, original.detail)
    assert moved.message["time"] == original.message["time"]


#: As many entries as the MAC cache holds: the next new MAC clears it.
_FULL_CACHE = {f"cache-filler-{i}": "00:00:5e:00:53:00" for i in range(fortigate._MACS_MAX)}


def _with_cache(state: str, message: bytes) -> ParseOutcome:
    fortigate._MACS.clear()
    if state == "full":
        fortigate._MACS.update(_FULL_CACHE)
    elif state == "warm":
        parse(message)  # the same MAC text parsed once before
    try:
        return parse(message)
    finally:
        fortigate._MACS.clear()


@settings(max_examples=150, deadline=None)
@given(mac=MAC_TEXT | st.text(max_size=20).filter(lambda t: '"' not in t and "\\" not in t))
def test_the_mac_cache_never_changes_a_result(mac: str) -> None:
    traffic = SRCMAC_LINE.replace(b' srcmac="00:00:5e:00:53:57"', f" srcmac={quote(mac)}".encode())
    ack = DHCP_LINES[0].replace(b'mac="00:00:5E:00:53:57"', f"mac={quote(mac)}".encode())
    for message in (traffic, ack):
        outcomes = [_with_cache(state, message) for state in ("empty", "full", "warm")]
        for outcome in outcomes:
            check_outcome(outcome)
        rows = {repr(_typed(outcome)) for outcome in outcomes}
        assert len(rows) == 1, rows
        assert len({(outcome.error, outcome.detail) for outcome in outcomes}) == 1
    srcmac = _typed(_with_cache("full", traffic))
    assert srcmac is not None
    assert srcmac[1]["srcmac"] == to_mac(mac)
    assert len(fortigate._MACS) <= fortigate._MACS_MAX


def lines_per_second(message: bytes, seconds: float = 0.4) -> float:
    record = RawRecord(received=RECEIVED, sender="172.31.255.1", message=message)
    position = LinePosition("fortigate", "fortigate", date(2026, 9, 23), 1, 0)
    count = 0
    start = time.perf_counter()
    while (elapsed := time.perf_counter() - start) < seconds:
        for _ in range(200):
            PARSER.parse(record, position)
        count += 200
    return count / elapsed


@pytest.mark.perf
def test_throughput_floor() -> None:
    """Spec 7.2/14: ≥ 10,000 messages/s for the whole ingest on 4 cores; the parser alone was
    measured at ~30,000 traffic lines/s per core (docs/PARSERS.md). The floor here only catches
    a pathological regression (e.g. a backtracking regex) without being flaky on CI runners.
    A throughput guard: it runs without coverage tracing (poe test-perf), which slows the parser
    several times (a Windows runner measured 2,859 lines/s under coverage)."""
    traffic = lines_per_second(SAMPLES[6])  # the session end (logid 0000000013)
    perf = lines_per_second(SAMPLES[0])  # performance statistics
    print(f"fortigate traffic {traffic:,.0f} lines/s, perf-stats {perf:,.0f} lines/s")
    assert traffic > 3_000
    assert perf > 3_000
