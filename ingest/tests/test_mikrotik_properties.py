"""MikroTik parser properties (hypothesis) and throughput.

Whatever the (unverified) header layout — with or without timestamp, host name or topics,
topics with a trailing ``:`` or a single word, any device time: never raises, exactly one
complete ``messages`` row, every row loadable, and a DHCP lease, ``ARP-LOG``, firewall or
configuration-change line always gives the row it describes.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime, timedelta
from datetime import time as dt_time

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_core.rawlog import RawRecord
from arcilogger_db.rowcheck import check_row
from arcilogger_ingest.parsers import LinePosition, ParseOutcome
from arcilogger_ingest.parsers.mikrotik import MikroTikParser

RECEIVED = datetime(2026, 9, 23, 6, 38, 27, tzinfo=UTC)
PARSER = MikroTikParser()
POSITION = LinePosition("core-r1", "mikrotik", date(2026, 9, 23), 1, 0)


def parse(message: bytes, parser: MikroTikParser = PARSER) -> ParseOutcome:
    record = RawRecord(received=RECEIVED, sender="172.20.10.1", message=message)
    return parser.parse(record, POSITION)


def check_outcome(outcome: ParseOutcome) -> None:
    assert outcome.error != "mikrotik.exception", outcome.detail
    rows = outcome.all_rows()
    assert rows[0].table == "messages"
    assert [row.table for row in rows].count("messages") == 1
    for row in rows:
        assert check_row(row.table, row.values, complete=True) == [], row


TIMESTAMPS = st.sampled_from(
    [
        "",
        "2026-09-23T09:38:26.380+03:00 ",
        "2026-09-23T09:38:26+03:00 ",
        "2026-09-23T06:38:26Z ",
        "2026-09-23T09:38:26 ",
        "Sep 23 09:38:26 ",
        # implausible or not representable in UTC: the receive time is used, the rows stay
        "0001-01-01T00:00:00+03:00 ",
        "9999-12-31T23:59:59-23:59 ",
        "1970-01-02T00:00:00Z ",
    ]
)
HOST_NAMES = ("CORE-R1", "SWITCH-1", "ap-172.20.10.23")
HOSTS = st.sampled_from(["", *(f"{name} " for name in HOST_NAMES)])
#: Multi-word topics (recognised, also with a trailing ":") and single words (not recognisable
#: as topics: header residue in front of the message — the wire format is unverified).
RECOGNISED = [
    "",
    "dhcp,info ",
    "dhcp,info: ",
    "script,info ",
    "script,info: ",
    "firewall,info ",
    "system,error,critical ",
    "x;y ",
]
SINGLE = ["dhcp ", "dhcp: ", "script ", "script: "]
TOPICS = st.sampled_from(RECOGNISED + SINGLE)
#: Parser that knows the device identities: the host name is then never ambiguous.
NAMED = MikroTikParser(host_names=HOST_NAMES)
OCTET = st.integers(min_value=0, max_value=255)
IPV4 = st.tuples(OCTET, OCTET, OCTET, OCTET).map(lambda o: ".".join(map(str, o)))
MAC = st.lists(st.integers(min_value=0, max_value=255), min_size=6, max_size=6).map(
    lambda b: ":".join(f"{x:02X}" for x in b)
)
HOSTNAME = st.text(
    alphabet=st.characters(blacklist_categories=("Cc", "Cs"), blacklist_characters="#"),
    max_size=30,
)
WORDS = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789-:,.()@<>= ", max_size=80)


def check_header(outcome: ParseOutcome, host: str, topics: str, named: bool) -> None:
    """Host and topics are exact when the topics token is recognisable or absent; with known
    host names the host is exact in every layout."""
    recognised = topics in RECOGNISED
    if recognised or named:
        assert outcome.message["host"] == (host.strip() or None)
    expected_topics = topics.strip().rstrip(":") or None
    assert outcome.message["program"] == (expected_topics if recognised else None)


def check_time(outcome: ParseOutcome, stamp: str) -> None:
    if stamp.startswith(("0001", "9999", "1970")):
        assert outcome.message["time"] == RECEIVED
        assert outcome.error == "mikrotik.time"
        assert outcome.message["extra"]["device_time"] == stamp.strip()
    else:
        assert outcome.error is None


@settings(max_examples=500, deadline=None)
@given(
    pri=st.integers(min_value=0, max_value=191),
    stamp=TIMESTAMPS,
    host=HOSTS,
    topics=TOPICS,
    named=st.booleans(),
    vlan=st.integers(min_value=1, max_value=4094),
    action=st.sampled_from(["assigned", "deassigned"]),
    ip=IPV4,
    mac=MAC,
    hostname=HOSTNAME,
)
def test_dhcp_lines_in_any_header_layout(
    pri: int,
    stamp: str,
    host: str,
    topics: str,
    named: bool,
    vlan: int,
    action: str,
    ip: str,
    mac: str,
    hostname: str,
) -> None:
    message = f"dhcp{vlan} {action} {ip} for {mac}" + (f" {hostname}" if hostname else "")
    raw = f"<{pri}>{stamp}{host}{topics}{message}".encode()
    outcome = parse(raw, NAMED if named else PARSER)
    check_outcome(outcome)
    check_time(outcome, stamp)
    (row,) = outcome.rows
    assert row.table == "mt_dhcp"
    values = row.values
    assert (values["server"], values["vlan"], values["action"]) == (f"dhcp{vlan}", vlan, action)
    assert values["ip"] == ip
    assert values["mac"] == mac.lower()
    assert values["hostname"] == (hostname.strip() or None)
    check_header(outcome, host, topics, named)


@settings(max_examples=300, deadline=None)
@given(
    pri=st.integers(min_value=0, max_value=191),
    stamp=TIMESTAMPS,
    host=HOSTS,
    topics=TOPICS,
    named=st.booleans(),
    ip=IPV4,
    mac=MAC,
    interface=st.sampled_from(["ether1", "vlan10", "bridge"]),
)
def test_arp_lines_in_any_header_layout(
    pri: int, stamp: str, host: str, topics: str, named: bool, ip: str, mac: str, interface: str
) -> None:
    raw = f"<{pri}>{stamp}{host}{topics}ARP-LOG {ip} {mac} {interface}".encode()
    outcome = parse(raw, NAMED if named else PARSER)
    check_outcome(outcome)
    check_time(outcome, stamp)
    (row,) = outcome.rows
    assert row.table == "mt_arp"
    assert (row.values["ip"], row.values["mac"], row.values["interface"]) == (
        ip,
        mac.lower(),
        interface,
    )
    check_header(outcome, host, topics, named)


@settings(max_examples=300, deadline=None)
@given(
    pri=st.integers(min_value=0, max_value=191),
    stamp=TIMESTAMPS,
    host=HOSTS,
    topics=st.sampled_from(["", "firewall,info ", "firewall,info: ", "firewall "]),
    prefix=st.sampled_from(["", "DROP-FWD ", "DROP-WAN "]),
    chain=st.sampled_from(["forward", "input"]),
    src=IPV4,
    dst=IPV4,
    ports=st.tuples(st.integers(0, 65535), st.integers(0, 65535)),
)
def test_firewall_lines_in_any_header_layout(
    pri: int,
    stamp: str,
    host: str,
    topics: str,
    prefix: str,
    chain: str,
    src: str,
    dst: str,
    ports: tuple[int, int],
) -> None:
    """With known host names the prefix is exact whenever the topics token is recognisable or
    absent; without them (or after a single-word topic) the row is still complete and the
    log prefix ends the prefix column or became the host name (the ambiguity documented in
    docs/PARSERS.md §3.1)."""
    message = (
        f"{prefix}{chain}: in:vlan42 out:ether2, connection-state:new, proto TCP (SYN), "
        f"{src}:{ports[0]}->{dst}:{ports[1]}, len 60"
    )
    line = f"<{pri}>{stamp}{host}{topics}{message}".encode()
    for parser, named in ((PARSER, False), (NAMED, True)):
        outcome = parse(line, parser)
        check_outcome(outcome)
        check_time(outcome, stamp)
        (row,) = outcome.rows
        values = row.values
        assert row.table == "mt_fw"
        assert (values["chain"], values["src"], values["dst"]) == (chain, src, dst)
        assert (values["sport"], values["dport"], values["len"]) == (*ports, 60)
        assert values["extra"] == {"proto_info": "SYN"}
        expected = prefix.strip() or None
        if named and topics != "firewall ":
            assert values["prefix"] == expected
            assert outcome.message["host"] == (host.strip() or None)
        elif expected is not None:  # ambiguous: "<ts> DROP-FWD forward:" reads DROP-FWD as host
            seen = values["prefix"] or outcome.message["host"] or ""
            assert seen.endswith(expected)


@settings(max_examples=200, deadline=None)
@given(
    stamp=TIMESTAMPS,
    host=HOSTS,
    topics=st.sampled_from(["", "system,info ", "system,info: "]),
    target=st.sampled_from(["filter rule", "user operator", "ip address", "x"]),
    action=st.sampled_from(["changed", "added", "removed"]),
)
def test_configuration_changes_in_any_header_layout(
    stamp: str, host: str, topics: str, target: str, action: str
) -> None:
    outcome = parse(f"<30>{stamp}{host}{topics}{target} {action} by admin".encode(), NAMED)
    check_outcome(outcome)
    check_time(outcome, stamp)
    (row,) = outcome.rows
    assert row.table == "mt_config"
    assert (row.values["target"], row.values["action"], row.values["user_name"]) == (
        target,
        action,
        "admin",
    )
    assert outcome.message["host"] == (host.strip() or None)


@settings(max_examples=300, deadline=None)
@given(
    day=st.dates() | st.sampled_from([date.min, date.max]),
    clock=st.times(),
    minutes=st.integers(min_value=-(23 * 60 + 59), max_value=23 * 60 + 59),
    fraction=st.booleans(),
)
def test_any_header_time(day: date, clock: dt_time, minutes: int, fraction: bool) -> None:
    """Year 1 to 9999, any offset: never raises; used only when within a day of receipt."""
    sign = "-" if minutes < 0 else "+"
    offset = f"{sign}{abs(minutes) // 60:02d}:{abs(minutes) % 60:02d}"
    clock = clock if fraction else clock.replace(microsecond=0)
    stamp = f"{day.isoformat()}T{clock.isoformat()}{offset}"
    outcome = parse(
        f"<30>{stamp} CORE-R1 dhcp,info dhcp41 assigned 172.20.41.142 for "
        "00:00:5E:00:53:48".encode()
    )
    check_outcome(outcome)
    assert outcome.rows[0].table == "mt_dhcp"
    try:
        device: datetime | None = datetime.combine(day, clock, tzinfo=UTC) - timedelta(
            minutes=minutes
        )
    except OverflowError:
        device = None
    plausible = device is not None and abs(device - RECEIVED) <= timedelta(days=1)
    assert outcome.message["time"] == (device if plausible else RECEIVED)
    assert outcome.error == (None if plausible else "mikrotik.time")


@settings(max_examples=400, deadline=None)
@given(
    pri=st.integers(min_value=0, max_value=191) | st.none(),
    stamp=TIMESTAMPS,
    host=HOSTS,
    topics=TOPICS,
    head=st.sampled_from(
        [
            "",
            "ARP-LOG ",
            "ARCI-HEARTBEAT ",
            "user ",
            "login failure for user ",
            "DROP-FWD forward: in:",
            "dhcp60 assigned ",
            "dhcp60 offering lease ",
            "dhcp60 failed to give out IP address",
            "ether1 link ",
            "02:00:5E:00:53:5B@wifi1 ",
        ]
    ),
    tail=WORDS,
)
def test_family_prefixes_with_random_tails(
    pri: int | None, stamp: str, host: str, topics: str, head: str, tail: str
) -> None:
    prefix = f"<{pri}>" if pri is not None else ""
    check_outcome(parse(f"{prefix}{stamp}{host}{topics}{head}{tail}".encode()))


@settings(max_examples=300, deadline=None)
@given(data=st.binary(max_size=2000) | st.text(max_size=2000).map(str.encode))
def test_any_bytes(data: bytes) -> None:
    check_outcome(parse(data))
    check_outcome(parse(b"<30>2026-09-23T09:38:26+03:00 CORE-R1 dhcp,info " + data))


@pytest.mark.perf
def test_throughput_floor() -> None:
    """The floor only catches a pathological regression; numbers are in docs/PARSERS.md.
    A throughput guard: it runs without coverage tracing (poe test-perf), which slows the parser
    several times (a Windows runner measured 2,424 firewall lines/s under coverage)."""
    lines = {
        "dhcp": b"<30>2026-09-23T09:38:26.380+03:00 CORE-R1 dhcp,info dhcp60 assigned "
        b"172.20.60.163 for 02:00:5E:00:53:5B NB-17",
        "firewall": b"<30>2026-09-23T09:38:26.380+03:00 CORE-R1 firewall,info DROP-FWD forward: "
        b"in:vlan42 out:ether2, connection-state:new src-mac 00:00:5E:00:53:4C, "
        b"proto TCP (SYN), 172.20.42.76:52790->198.51.100.77:443, len 60",
    }
    for name, message in lines.items():
        record = RawRecord(received=RECEIVED, sender="172.20.10.1", message=message)
        count = 0
        start = time.perf_counter()
        while (elapsed := time.perf_counter() - start) < 0.4:
            for _ in range(200):
                PARSER.parse(record, POSITION)
            count += 200
        rate = count / elapsed
        print(f"mikrotik {name} {rate:,.0f} lines/s")
        assert rate > 3_000, name
