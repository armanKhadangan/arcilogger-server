"""MikroTik parser: header variants, every sample line field by field, families, configuration.

The wire format is not verified yet (spec 2.5). The ``/log print`` format sample is turned into
wire lines in the approximate format of the specification; ``tests/fixtures/synthetic`` holds
more (synthetic, verify on site).
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.rawlog import RawRecord
from arcilogger_db.rowcheck import check_row
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_ingest.parsers import LinePosition, ParseOutcome
from arcilogger_ingest.parsers.mikrotik import (
    DEFAULT_SERVER_VLAN_REGEX,
    Header,
    MikroTikParser,
    split_header,
    topic_level,
)

FIXTURES = Path(__file__).parents[2] / "tests" / "fixtures"
SAMPLE = (FIXTURES / "format" / "mikrotik-log-print-sample.txt").read_text("utf-8").splitlines()
SYNTHETIC = [
    line
    for line in (FIXTURES / "synthetic" / "mikrotik-wire-synthetic.log").read_bytes().splitlines()
    if not line.startswith(b"#")
]
RECEIVED = datetime(2026, 9, 23, 6, 38, 27, tzinfo=UTC)
AT = datetime(2026, 9, 23, 6, 38, 26, 380000, tzinfo=UTC)
PARSER = MikroTikParser()
FRAMEWORK = {"time", "time_received", "source", "sender", "raw_day", "raw_line", "raw_offset"}


def wire(row: str) -> bytes:
    """``topics | message`` of ``/log print`` → the approximate wire format (PARSERS.md §3.1)."""
    topics, _, message = row.partition("|")
    topics_text = ",".join(topics.strip().split(";"))
    return f"<30>2026-09-23T09:38:26.380+03:00 CORE-R1 {topics_text} {message.strip()}".encode()


def parse(message: bytes, parser: MikroTikParser = PARSER) -> ParseOutcome:
    record = RawRecord(received=RECEIVED, sender="172.20.10.1", message=message)
    outcome = parser.parse(record, LinePosition("core-r1", "mikrotik", date(2026, 9, 23), 1, 0))
    for row in outcome.all_rows():
        assert check_row(row.table, row.values, complete=True) == [], row
    return outcome


def typed(outcome: ParseOutcome) -> tuple[str, dict[str, Any]]:
    (row,) = outcome.rows
    return row.table, {k: v for k, v in row.values.items() if k not in FRAMEWORK}


def event(**values: Any) -> dict[str, Any]:
    return {
        "event": None,
        "topics": None,
        "level": None,
        "identity": None,
        "interface": None,
        "mac": None,
        "ip": None,
        "text": None,
        "extra": {},
        **values,
    }


def dhcp(server: str, vlan: int, action: str, ip: str, mac: str, host: str | None) -> Any:
    return (
        "mt_dhcp",
        {
            "server": server,
            "vlan": vlan,
            "action": action,
            "ip": ip,
            "mac": mac,
            "hostname": host,
            "extra": {},
        },
    )


def arp(ip: str, mac: str, interface: str) -> Any:
    return ("mt_arp", {"ip": ip, "mac": mac, "interface": interface, "extra": {}})


def account(action: str) -> Any:
    return (
        "mt_account",
        {
            "action": action,
            "user_name": "netops",
            "ip": "172.20.10.37",
            "via": "winbox",
            "extra": {},
        },
    )


def forward_drop(in_if: str, out_if: str, mac: str, src: str, sport: int, dst: str) -> Any:
    """A ``DROP-FWD`` line of the format sample: a new TCP connection to port 443 (SYN)."""
    return (
        "mt_fw",
        {
            "prefix": "DROP-FWD",
            "chain": "forward",
            "in_if": in_if,
            "out_if": out_if,
            "state": "new",
            "src_mac": mac,
            "proto": "TCP",
            "src": src,
            "sport": sport,
            "dst": dst,
            "dport": 443,
            "len": 60,
            "extra": {"proto_info": "SYN"},
        },
    )


#: All 11 lines of the format sample: (topics, messages.type, subtype, typed row).
GOLDEN: list[tuple[str, str, str | None, Any]] = [
    ("dhcp,info", "dhcp", "assigned",
     dhcp("dhcp41", 41, "assigned", "172.20.41.142", "00:00:5e:00:53:48", "FIN-PC03")),
    ("script,info", "arp", None, arp("172.20.10.23", "00:00:5e:00:53:43", "vlan10")),
    ("system,info,account", "account", "login", account("login")),
    ("dhcp,info", "dhcp", "deassigned",
     dhcp("dhcp61", 61, "deassigned", "172.20.61.204", "02:00:5e:00:53:53", None)),
    ("firewall,info", "fw", "forward", forward_drop(
        "vlan61", "vlan20", "02:00:5e:00:53:5e", "172.20.61.118", 41822, "172.20.20.44"
    )),
    ("dhcp,info", "dhcp", "assigned",
     dhcp("dhcp60", 60, "assigned", "172.20.60.163", "02:00:5e:00:53:5b", "NB-17")),
    ("script,info", "arp", None, arp("172.20.10.31", "00:00:5e:00:53:4a", "vlan10")),
    ("firewall,info", "fw", "forward", forward_drop(
        "vlan42", "ether2", "00:00:5e:00:53:4c", "172.20.42.76", 52790, "198.51.100.77"
    )),
    ("dhcp,info", "dhcp", "assigned",
     dhcp("dhcp43", 43, "assigned", "172.20.43.159", "00:00:5e:00:53:52", "IDR-PC05")),
    ("script,info", "arp", None, arp("172.20.20.44", "00:00:5e:00:53:61", "vlan20")),
    ("system,info,account", "account", "logout", account("logout")),
]  # fmt: skip


@pytest.mark.parametrize("index", range(11))
def test_sample_line_golden(index: int) -> None:
    topics, message_type, subtype, row = GOLDEN[index]
    raw = wire(SAMPLE[index])
    outcome = parse(raw)
    assert outcome.error is None
    message = outcome.message
    assert (message["type"], message["subtype"]) == (message_type, subtype)
    assert (message["facility"], message["severity"]) == (3, 6)  # <30> daemon.info
    assert message["host"] == "CORE-R1"
    assert message["program"] == topics
    assert message["text"] == SAMPLE[index].partition("|")[2].strip()
    assert message["extra"] == {}
    assert message["time"] == AT
    assert message["time_received"] == RECEIVED
    assert outcome.rows[0].values["time"] == AT
    assert typed(outcome) == row


def test_sample_has_eleven_lines() -> None:
    assert len(SAMPLE) == 11


# -- header ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (
            "<30>2026-09-23T09:38:26.380+03:00 CORE-R1 dhcp,info dhcp41 assigned x",
            Header(3, 6, "2026-09-23T09:38:26.380+03:00", "CORE-R1", True, "dhcp,info",
                   "dhcp41 assigned x"),
        ),
        (
            "<30>2026-09-23T09:38:26+03:00 dhcp,info dhcp41 assigned x",
            Header(
                3, 6, "2026-09-23T09:38:26+03:00", None, False, "dhcp,info", "dhcp41 assigned x"
            ),
        ),
        (
            "<30>dhcp,info dhcp41 assigned x",
            Header(3, 6, None, None, False, "dhcp,info", "dhcp41 assigned x"),
        ),
        (
            "<30>CORE-R1 system,info,account user a logged in",
            Header(3, 6, None, "CORE-R1", True, "system,info,account", "user a logged in"),
        ),
        (
            "<30>Sep 23 09:38:26 CORE-R1 dhcp;info x",
            Header(3, 6, "Sep 23 09:38:26", "CORE-R1", True, "dhcp;info", "x"),
        ),
        (
            "<30>2026-09-23T09:38:26+03:00 CORE-R1 dhcp41 assigned x",
            Header(3, 6, "2026-09-23T09:38:26+03:00", "CORE-R1", False, None, "dhcp41 assigned x"),
        ),
        (
            "<30>no header at all",
            Header(3, 6, None, None, False, None, "no header at all"),
        ),
        (
            "<30>1 2026-09-23T06:38:26Z CORE-R1 dhcp,info - - - dhcp41 assigned x",
            Header(3, 6, "2026-09-23T06:38:26Z", "CORE-R1", True, "dhcp,info", "dhcp41 assigned x"),
        ),
        (
            "<30>1 2026-09-23T06:38:26Z CORE-R1 routeros - - - firewall,info x  ",
            Header(3, 6, "2026-09-23T06:38:26Z", "CORE-R1", True, "firewall,info", "x"),
        ),
        (
            "<30>2026-09-23T09:38:26+03:00  CORE-R1   dhcp,info  x  y",
            Header(3, 6, "2026-09-23T09:38:26+03:00", "CORE-R1", True, "dhcp,info", "x  y"),
        ),
        ("", Header(None, None, None, None, False, None, "")),
        (" \r\n", Header(None, None, None, None, False, None, "")),
    ],
)  # fmt: skip
def test_split_header(text: str, expected: Header) -> None:
    assert split_header(text) == expected


@pytest.mark.parametrize(
    "token",
    ["DHCP,INFO", "dhcp", "dhcp:", "dhcp,", ",info", "dhcp,,info", "dhcp,info::", "dhcp, info"],
)
def test_tokens_that_are_not_topics(token: str) -> None:
    assert split_header(f"<30>{token} rest").topics is None


@pytest.mark.parametrize(
    ("text", "host", "topics"),
    [
        ("<30>dhcp,info: rest", None, "dhcp,info"),
        ("<30>2026-09-23T09:38:26+03:00 CORE-R1 script,info: rest", "CORE-R1", "script,info"),
        ("<30>1 2026-09-23T06:38:26Z CORE-R1 dhcp,info: - - - rest", "CORE-R1", "dhcp,info"),
    ],
)
def test_topics_with_a_trailing_colon(text: str, host: str | None, topics: str) -> None:
    header = split_header(text)
    assert (header.host, header.topics, header.message) == (host, topics, "rest")


def test_a_word_ending_in_a_colon_is_never_the_host_name() -> None:
    """A tag or a firewall chain (``forward:``) after the timestamp is not a host name."""
    header = split_header("<30>2026-09-23T09:38:26+03:00 forward: in:ether1 out:ether2")
    assert (header.host, header.message) == (None, "forward: in:ether1 out:ether2")
    outcome = parse(b"<30>2026-09-23T09:38:26+03:00 forward: in:ether1 out:ether2, len 60")
    assert outcome.message["host"] is None
    assert (typed(outcome)[1]["prefix"], typed(outcome)[1]["chain"]) == (None, "forward")


def test_timestamp_without_host_and_topics_retries_the_first_word() -> None:
    outcome = parse(
        b"<30>2026-09-23T09:38:31.250+03:00 dhcp43 assigned 172.20.43.120 for 3C:52:82:00:00:11 PC"
    )
    assert outcome.message["host"] is None
    assert outcome.message["text"] == "dhcp43 assigned 172.20.43.120 for 3C:52:82:00:00:11 PC"
    assert typed(outcome) == dhcp(
        "dhcp43", 43, "assigned", "172.20.43.120", "3c:52:82:00:00:11", "PC"
    )
    only_word = parse(b"<30>2026-09-23T09:38:31+03:00 ARCI-HEARTBEAT")
    assert only_word.message["type"] == "heartbeat"
    assert only_word.message["host"] is None
    unknown = parse(b"<30>2026-09-23T09:38:31+03:00 CORE-R1 something else")
    assert (unknown.message["host"], unknown.message["text"]) == ("CORE-R1", "something else")


def test_bare_header_retries_with_the_first_word_as_host() -> None:
    """``<PRI>HOST message`` without timestamp and topics (found by a property test)."""
    outcome = parse(b"<30>CORE-R1 dhcp41 assigned 172.20.41.142 for 00:00:5E:00:53:48")
    assert (outcome.message["host"], outcome.message["text"]) == (
        "CORE-R1",
        "dhcp41 assigned 172.20.41.142 for 00:00:5E:00:53:48",
    )
    assert typed(outcome)[0] == "mt_dhcp"
    unknown = parse(b"<30>CORE-R1 something else")
    assert (unknown.message["host"], unknown.message["text"]) == (None, "CORE-R1 something else")
    with_topics = parse(
        b"<30>dhcp,info CORE-R1 dhcp41 assigned 172.20.41.142 for 00:00:5E:00:53:48"
    )
    assert with_topics.message["host"] is None  # topics first: there is no host name
    table, values = typed(with_topics)  # the lease is still found; the residue is kept
    assert (table, values["ip"], values["extra"]) == (
        "mt_dhcp",
        "172.20.41.142",
        {"prefix": "CORE-R1"},
    )


@pytest.mark.parametrize(
    ("raw", "time", "extra"),
    [
        (b"<30>2026-09-23T09:38:26+03:00 R x", datetime(2026, 9, 23, 6, 38, 26, tzinfo=UTC), {}),
        (b"<30>2026-09-23T06:38:26.5Z R x", datetime(2026, 9, 23, 6, 38, 26, 5 * 10**5, UTC), {}),
        (b"<30>2026-09-23T09:38:26 R x", RECEIVED, {"device_time": "2026-09-23T09:38:26"}),
        (b"<30>Sep 23 09:38:26 R x", RECEIVED, {"device_time": "Sep 23 09:38:26"}),
        (b"<30>R dhcp,info x", RECEIVED, {}),
    ],
)  # fmt: skip
def test_time_rules(raw: bytes, time: datetime, extra: dict[str, str]) -> None:
    outcome = parse(raw)
    assert outcome.message["time"] == time
    assert outcome.message["extra"] == extra


@pytest.mark.parametrize(
    ("topics", "severity", "level"),
    [
        (["system", "error", "critical"], 6, "critical"),
        (["dhcp", "warning"], 6, "warning"),
        (["dhcp", "info"], 3, "info"),
        (["script"], 2, "critical"),
        ([], 3, "error"),
        ([], 4, "warning"),
        ([], 5, "info"),
        ([], 7, "debug"),
        ([], None, None),
    ],
)
def test_topic_level(topics: list[str], severity: int | None, level: str | None) -> None:
    assert topic_level(topics, severity) == level


# -- families ------------------------------------------------------------------------------------


def synthetic(fragment: str) -> ParseOutcome:
    (line,) = [line for line in SYNTHETIC if fragment.encode() in line]
    return parse(line)


def test_dhcp_hostname_is_the_rest_of_the_line() -> None:
    outcome = synthetic("Ayşe'nin iPhone'u")
    assert typed(outcome)[1]["hostname"] == "Ayşe'nin iPhone'u"
    assert outcome.message["program"] is None  # no topics token
    trailing = parse(b"<30>R dhcp,info dhcp60 assigned 172.20.60.9 for 02:00:5E:00:53:5B   ")
    assert typed(trailing)[1]["hostname"] is None


def test_dhcp_tolerates_the_older_to_wording() -> None:
    outcome = parse(b"<30>R dhcp,info dhcp41 assigned 172.20.41.9 to 00:00:5E:00:53:48")
    assert typed(outcome) == dhcp(
        "dhcp41", 41, "assigned", "172.20.41.9", "00:00:5e:00:53:48", None
    )


@pytest.mark.parametrize(
    "message",
    [
        b"dhcp41 assigned 172.20.41.999 for 00:00:5E:00:53:48",
        b"dhcp41 assigned 172.20.41.9 for 00:00:5E:00:53",
        b"dhcp41 assigned 172.20.41.9 by somebody",
    ],
)
def test_bad_dhcp_lines_are_failures_without_rows(message: bytes) -> None:
    outcome = parse(b"<30>CORE-R1 dhcp,info " + message)
    assert outcome.error == "mikrotik.dhcp"
    assert outcome.rows == ()
    assert outcome.message["type"] == "dhcp"


def test_assigned_outside_the_dhcp_topic_is_not_a_failure() -> None:
    outcome = parse(b"<30>CORE-R1 system,info address assigned to bridge")
    assert outcome.error is None
    assert outcome.message["type"] == "log"


def test_dhcp_pool_empty_and_offering() -> None:
    table, values = typed(synthetic("is empty"))
    assert (table, values) == (
        "mt_event",
        event(
            event="dhcp_pool_empty",
            topics="dhcp,warning",
            level="warning",
            text="dhcp60 failed to give out IP address: pool <pool60> is empty",
            extra={"server": "dhcp60", "vlan": 60, "pool": "pool60"},
        ),
    )
    failed = parse(b"<28>R dhcp,warning dhcp-x failed to give out IP address: no reason given")
    assert typed(failed)[1]["event"] == "dhcp_failed"
    assert typed(failed)[1]["extra"] == {"server": "dhcp-x"}  # no VLAN in that name
    _, offer = typed(synthetic("offering lease"))
    assert offer == event(
        event="dhcp_offering",
        topics="dhcp,info",
        level="info",
        ip="172.20.60.120",
        mac="02:00:5e:00:53:5d",
        text="dhcp60 offering lease 172.20.60.120 for 02:00:5E:00:53:5D without success",
        extra={"server": "dhcp60", "vlan": 60, "tail": "without success"},
    )
    bad = parse(b"<30>R dhcp,info dhcp60 offering lease 1.2.3 for zz without success")
    assert typed(bad)[1]["extra"]["ip"] == "1.2.3"
    assert typed(bad)[1]["extra"]["mac"] == "zz"


def test_arp_log_and_failures() -> None:
    outcome = parse(b"<30>R script,info ARP-LOG 172.20.10.23 00:00:5E:00:53:43 vlan10 extra words")
    assert typed(outcome)[1]["extra"] == {"tail": "extra words"}
    no_interface = parse(b"<30>R script,info ARP-LOG 172.20.10.23 00:00:5E:00:53:43")
    assert typed(no_interface)[1]["interface"] is None
    for bad in (b"ARP-LOG", b"ARP-LOG 172.20.10.23", b"ARP-LOG 300.1.1.1 00:00:5E:00:53:43 x"):
        failure = parse(b"<30>R script,info " + bad)
        assert failure.error == "mikrotik.arp"
        assert failure.rows == ()


def test_firewall_icmp_tcp_flags_and_nat() -> None:
    _, icmp = typed(synthetic("DROP-WAN"))
    assert icmp == {
        "prefix": "DROP-WAN",
        "chain": "input",
        "in_if": "ether2",
        "out_if": "(unknown 0)",
        "state": "new",
        "src_mac": None,
        "proto": "ICMP",
        "src": "198.51.100.9",
        "sport": None,
        "dst": "100.64.12.1",
        "dport": None,
        "len": 84,
        "extra": {"proto_info": "type 8, code 0"},
    }
    _, tcp = typed(synthetic("DROP-MGMT"))
    assert (tcp["proto"], tcp["extra"], tcp["dport"]) == ("TCP", {"proto_info": "SYN"}, 8291)
    _, nat = typed(synthetic("DROP-GUEST"))
    assert nat["extra"] == {"nat": "(172.20.61.150:40000->172.31.255.2:40000)->203.0.113.53:53"}
    assert (nat["src"], nat["sport"], nat["dst"], nat["dport"]) == (
        "172.20.61.150",
        40000,
        "203.0.113.53",
        53,
    )


def test_firewall_without_prefix_with_tail_and_odd_values() -> None:
    outcome = parse(
        b"<30>R firewall,info forward: in:ether1 out:ether2, connection-state:established,snat"
        b" proto TCP (ACK,PSH), [2001:db8::1]:443->10.0.0.1:99999, len 40, something new"
    )
    assert outcome.error == "mikrotik.firewall"
    assert outcome.detail == "invalid dport"
    _, values = typed(outcome)
    assert values["prefix"] is None
    assert values["state"] == "established,snat"
    assert (values["src"], values["sport"], values["dst"], values["dport"]) == (
        "2001:db8::1",
        443,
        "10.0.0.1",
        None,
    )
    assert values["extra"] == {"dport": "99999", "proto_info": "ACK,PSH", "tail": "something new"}
    bare_ipv6 = parse(
        b"<30>R firewall,info input: in:e1 out:(unknown 0), proto ICMPv6, fe80::1->ff02::1"
    )
    assert typed(bare_ipv6)[1]["src"] == "fe80::1"
    huge_len = parse(b"<30>R firewall,info input: in:e1 out:e2, len 99999999999")
    assert typed(huge_len)[1]["extra"] == {"len": "99999999999"}


def test_firewall_topic_without_known_layout_is_a_failure() -> None:
    outcome = parse(b"<30>R firewall,info rule 7 matched in:ether1 out:ether2 somehow")
    assert outcome.error == "mikrotik.firewall"
    assert outcome.rows == ()
    elsewhere = parse(b"<30>R system,info rule 7 matched in:ether1 out:ether2 somehow")
    assert elsewhere.error is None


def test_account_family() -> None:
    table, failure = typed(synthetic("from 172.20.41.196 via winbox"))
    assert table == "mt_account"
    assert failure == {
        "action": "login_failure",
        "user_name": "admin",
        "ip": "172.20.41.196",
        "via": "winbox",
        "extra": {},
    }
    _, by_mac = typed(synthetic("from 00:00:5E:00:53:4E via winbox"))
    assert (by_mac["ip"], by_mac["extra"]) == (None, {"from": "00:00:5E:00:53:4E"})
    local = parse(b"<30>R system,info,account user admin logged in via local")
    assert typed(local)[1] == {
        "action": "login",
        "user_name": "admin",
        "ip": None,
        "via": "local",
        "extra": {},
    }
    assert parse(b"<30>R system,info user admin did something").rows == ()
    assert parse(b"<30>R system,info login failure for user ").rows == ()


def test_interface_family() -> None:
    _, up = typed(synthetic("link up"))
    assert up == {
        "interface": "ether5",
        "state": "up",
        "detail": "speed 1G, full duplex",
        "extra": {},
    }
    _, down = typed(synthetic("link down"))
    assert down == {"interface": "ether5", "state": "down", "detail": None, "extra": {}}
    assert parse(b"<30>R interface,info ether5 link flapping").rows == ()


def test_configuration_changes_default_patterns() -> None:
    expectations = {
        "filter rule changed by admin": ("changed", "filter rule"),
        "user operator added by admin": ("added", "user operator"),
        "ip address removed by admin": ("removed", "ip address"),
    }
    for fragment, (action, target) in expectations.items():
        outcome = synthetic(fragment)
        assert outcome.message["type"] == "config"
        assert typed(outcome) == (
            "mt_config",
            {"action": action, "user_name": "admin", "target": target, "via": None, "extra": {}},
        )
    detail = parse(
        b"<30>R system,info ip address added by admin (/ip address add address=1.2.3.4/24)"
    )
    assert typed(detail)[1]["extra"] == {"detail": "(/ip address add address=1.2.3.4/24)"}
    assert synthetic("device changed name").message["type"] == "log"  # no "by <user>"


def test_configuration_patterns_are_configurable() -> None:
    parser = MikroTikParser(
        config_patterns=[
            r"^(?P<target>.+?) (?P<action>changed) by (?P<via>[a-z]+):(?P<user>\w+)@(?P<ip>\S+)$",
            re.compile(r"^config saved by (?P<user>\S+)$"),
        ]
    )
    outcome = parse(b"<30>R system,info filter rule changed by winbox:admin@172.20.10.37", parser)
    assert typed(outcome) == (
        "mt_config",
        {
            "action": "changed",
            "user_name": "admin",
            "target": "filter rule",
            "via": "winbox",
            "extra": {"ip": "172.20.10.37"},
        },
    )
    saved = parse(b"<30>R system,info config saved by netops", parser)
    assert typed(saved)[1] == {
        "action": None,
        "user_name": "netops",
        "target": None,
        "via": None,
        "extra": {},
    }
    assert parse(b"<30>R system,info ip address added by admin", parser).rows == ()
    with pytest.raises(re.error):
        MikroTikParser(config_patterns=["(unclosed"])


def test_heartbeat() -> None:
    _, beat = typed(synthetic("ARCI-HEARTBEAT"))
    assert beat == event(
        event="heartbeat",
        topics="script,info",
        level="info",
        identity="CORE-R1",
        text="ARCI-HEARTBEAT CORE-R1",
    )
    spaced = parse(b"<30>R script,info ARCI-HEARTBEAT  Core Router 1 ")
    assert typed(spaced)[1]["identity"] == "Core Router 1"
    bare = parse(b"<30>R script,info ARCI-HEARTBEAT")
    assert typed(bare)[1]["identity"] is None
    assert parse(b"<30>R script,info ARCI-HEARTBEATS x").message["type"] == "log"


def test_wifi() -> None:
    _, connect = typed(synthetic("@cap-wifi1 connected"))
    assert connect == event(
        event="wifi_connect",
        topics="wireless,info",
        level="info",
        interface="cap-wifi1",
        mac="02:00:5e:00:53:5b",
        text="02:00:5E:00:53:5B@cap-wifi1 connected, signal strength -52",
        extra={"detail": "signal strength -52"},
    )
    _, disconnect = typed(synthetic("@cap-wifi1 disconnected"))
    assert disconnect["event"] == "wifi_disconnect"
    assert disconnect["extra"] == {"detail": "connection lost, signal strength -80"}
    legacy = parse(b"<30>AP wireless,info 02:00:5E:00:53:5B@wlan1: disconnected")
    assert (typed(legacy)[1]["interface"], typed(legacy)[1]["extra"]) == ("wlan1", {})
    assert parse(b"<30>AP wireless,info someone@example connected").rows == ()


def test_other_messages() -> None:
    critical = synthetic("rebooted without proper shutdown")
    assert (critical.message["type"], critical.message["subtype"]) == ("log", "critical")
    assert typed(critical)[1] == event(
        event="log",
        topics="system,error,critical",
        level="critical",
        text="router was rebooted without proper shutdown",
    )
    warning = synthetic("ntp change time")
    assert typed(warning)[1]["level"] == "warning"
    info = synthetic("device changed name")
    assert info.rows == ()
    assert info.error is None
    no_topics_error = parse(b"<27>2026-09-23T09:38:26+03:00 R disk failure")  # PRI says error
    assert typed(no_topics_error)[1]["level"] == "error"


# -- configuration --------------------------------------------------------------------------------


def test_vlan_rule_is_the_database_setting() -> None:
    assert DEFAULT_SETTINGS["dhcp.server_vlan_regex"] == DEFAULT_SERVER_VLAN_REGEX
    names = ("dhcp60", "dhcp43", "dhcp1", "dhcp", "defconf")
    assert [PARSER.vlan_of(name) for name in names] == [60, 43, 1, None, None]
    assert PARSER.vlan_of("dhcp0") is None  # VLAN ids are 1-4094
    assert PARSER.vlan_of("dhcp4095") is None
    assert PARSER.vlan_of("dhcp99999999999999999999999") is None
    custom = MikroTikParser(server_vlan_regex=r"^vlan(\d+)-dhcp$")
    assert custom.vlan_of("vlan42-dhcp") == 42
    assert custom.vlan_of("dhcp42") is None
    no_group = MikroTikParser(server_vlan_regex=r"^dhcp\d+$")
    assert no_group.vlan_of("dhcp60") is None
    outcome = parse(b"<30>R dhcp,info guest assigned 172.20.61.9 for 02:00:5E:00:53:5E x")
    assert typed(outcome)[1]["vlan"] is None


def test_every_synthetic_line_parses_without_unexpected_failures() -> None:
    for raw in SYNTHETIC:
        outcome = parse(raw)
        assert outcome.error is None, raw


# -- header variants must not hide evidence (review of B1) ---------------------------------------

STAMP = "<30>2026-09-23T09:38:26.380+03:00 CORE-R1 "


@pytest.mark.parametrize(
    ("rest", "program", "table", "values"),
    [
        (
            "dhcp,info: dhcp60 assigned 172.20.60.163 for 02:00:5E:00:53:5B NB-17",
            "dhcp,info",
            "mt_dhcp",
            {"server": "dhcp60", "vlan": 60, "hostname": "NB-17", "extra": {}},
        ),
        (
            "dhcp,info dhcp60: assigned 172.20.60.163 for 02:00:5E:00:53:5B NB-17",
            "dhcp,info",
            "mt_dhcp",
            {"server": "dhcp60", "vlan": 60, "hostname": "NB-17", "extra": {}},
        ),
        (
            "dhcp dhcp60 assigned 172.20.60.163 for 02:00:5E:00:53:5B NB-17",
            None,
            "mt_dhcp",
            {"server": "dhcp60", "vlan": 60, "extra": {"prefix": "dhcp"}},
        ),
        (
            "script,info: ARP-LOG 172.20.10.23 00:00:5E:00:53:43 vlan10",
            "script,info",
            "mt_arp",
            {"ip": "172.20.10.23", "interface": "vlan10", "extra": {}},
        ),
        (
            "script: ARP-LOG 172.20.10.23 00:00:5E:00:53:43 vlan10",
            None,
            "mt_arp",
            {"ip": "172.20.10.23", "interface": "vlan10", "extra": {"prefix": "script:"}},
        ),
        (
            "script ARP-LOG 172.20.10.23 00:00:5E:00:53:43 vlan10",
            None,
            "mt_arp",
            {"mac": "00:00:5e:00:53:43", "extra": {"prefix": "script"}},
        ),
        (
            "script: ARCI-HEARTBEAT CORE-R1",
            None,
            "mt_event",
            {"event": "heartbeat", "identity": "CORE-R1", "extra": {"prefix": "script:"}},
        ),
        (
            "firewall,info: DROP-FWD forward: in:vlan42 out:ether2, len 60",
            "firewall,info",
            "mt_fw",
            {"prefix": "DROP-FWD", "chain": "forward", "len": 60},
        ),
    ],
)
def test_header_variants_keep_the_evidence(
    rest: str, program: str | None, table: str, values: dict[str, Any]
) -> None:
    outcome = parse((STAMP + rest).encode())
    assert outcome.error is None
    assert outcome.message["host"] == "CORE-R1"
    assert outcome.message["program"] == program
    found, row = typed(outcome)
    assert found == table
    assert {key: row[key] for key in values} == values


@pytest.mark.parametrize(
    "rest",
    [
        "dhcp60 assigned 172.20.60.999 for 02:00:5E:00:53:5B",  # bad IP, MAC present
        "dhcp60 assigned to 02:00:5E:00:53:5B",  # unknown layout, MAC present
        "dhcp dhcp60 assigned 172.20.60.163 for 0200.5E00.535B",  # dotted MAC, no topic
    ],
)
def test_dhcp_evidence_that_does_not_parse_is_counted(rest: str) -> None:
    outcome = parse((STAMP + rest).encode())
    if "0200." in rest:  # no MAC-shaped token and no dhcp topic: not recognisable as DHCP
        assert (outcome.error, outcome.message["type"]) == (None, "log")
        dhcp_topic = parse((STAMP + "dhcp,info " + rest).encode())
        assert dhcp_topic.error == "mikrotik.dhcp"
        return
    assert outcome.error == "mikrotik.dhcp"
    assert outcome.rows == ()
    assert outcome.message["type"] == "dhcp"


def test_assigned_outside_dhcp_is_not_a_dhcp_failure() -> None:
    """Review finding: ``<x> assigned <y> to <z>`` outside the dhcp topic is not DHCP."""
    outcome = parse(STAMP.encode() + b"system,info ether1 assigned address to bridge")
    assert (outcome.error, outcome.message["type"], outcome.rows) == (None, "log", ())


def test_firewall_shape_without_the_firewall_topic_is_counted() -> None:
    outcome = parse(STAMP.encode() + b"rule 7 in:ether1 out:ether2 proto TCP 1.2.3.4->5.6.7.8")
    assert outcome.error == "mikrotik.firewall"
    assert parse(STAMP.encode() + b"system,info in:ether1 out:ether2 somehow").error is None


def test_known_host_names_make_the_header_deterministic() -> None:
    """Without topics, ``<ts> DROP-FWD forward: …`` and ``<ts> CORE-R1 forward: …`` have the
    same shape; the device identities decide (review finding, minor)."""
    named = MikroTikParser(host_names=["core-r1", "SWITCH-1"])
    stamp = b"<30>2026-09-23T09:38:26+03:00 "
    firewall = b"input: in:ether2 out:(unknown 0), proto TCP (SYN), 1.2.3.4:5->100.64.12.9:22"
    ambiguous = parse(stamp + b"DROP-WAN " + firewall)  # default: BSD convention
    assert (ambiguous.message["host"], typed(ambiguous)[1]["prefix"]) == ("DROP-WAN", None)
    no_host = parse(stamp + b"DROP-WAN " + firewall, named)
    assert (no_host.message["host"], typed(no_host)[1]["prefix"]) == (None, "DROP-WAN")
    host = parse(stamp + b"CORE-R1 " + firewall, named)
    assert (host.message["host"], typed(host)[1]["prefix"]) == ("CORE-R1", None)
    bare = parse(b"<30>CORE-R1 DROP-FWD " + firewall, named)  # no timestamp, no topics
    assert (bare.message["host"], typed(bare)[1]["prefix"]) == ("CORE-R1", "DROP-FWD")
    assert typed(parse(b"<30>CORE-R1 DROP-FWD " + firewall))[1]["prefix"] == "CORE-R1 DROP-FWD"
    config = parse(stamp + b"filter rule changed by admin", named)
    assert (config.message["host"], typed(config)[1]["target"]) == (None, "filter rule")
    assert typed(parse(stamp + b"filter rule changed by admin"))[1]["target"] == "rule"
    lease = parse(stamp + b"switch-1 dhcp41 assigned 172.20.41.142 for 00:00:5E:00:53:48", named)
    assert (lease.message["host"], typed(lease)[1]["extra"]) == ("switch-1", {})
    topics = parse(stamp + b"OTHER dhcp,info dhcp41 assigned 172.20.41.142 for 00:00:5E:00:53:48")
    assert topics.message["host"] == "OTHER"  # confirmed by the topics token anyway


def test_glued_messages_are_cut_and_counted() -> None:
    first = STAMP + "dhcp,info dhcp60 assigned 172.20.60.163 for 02:00:5E:00:53:5B NB-17"
    second = (
        "<30>2026-09-23T09:38:27.380+03:00 CORE-R1 dhcp,info dhcp41 assigned 172.20.41.142 for "
        "00:00:5E:00:53:48"
    )
    outcome = parse((first + second).encode())
    assert outcome.error == "mikrotik.glued"
    assert outcome.message["extra"] == {"glued": second}
    assert outcome.message["text"].endswith("NB-17")
    _, lease = typed(outcome)
    assert (lease["ip"], lease["hostname"]) == ("172.20.60.163", "NB-17")
    bsd = parse((first + " <30>Sep 23 09:38:27 CORE-R1 dhcp,info x").encode())
    assert bsd.error == "mikrotik.glued"
    assert typed(bsd)[1]["hostname"] == "NB-17"
    pool = synthetic("is empty")  # "<pool60>" is not a syslog header
    assert pool.error is None


@pytest.mark.parametrize(
    ("stamp", "used"),
    [
        ("2026-09-23T09:38:26+03:00", AT.replace(microsecond=0)),
        ("2026-09-22T09:38:28+03:00", datetime(2026, 9, 22, 6, 38, 28, tzinfo=UTC)),  # < 1 day
        ("2026-09-22T09:38:26+03:00", None),  # just over one day before the receive time
        ("2020-01-01T00:00:00Z", None),
        ("1970-01-02T00:00:00Z", None),  # a router without RTC after a reboot
        ("0001-01-01T00:00:00+03:00", None),  # not representable in UTC: OverflowError before
        ("9999-12-31T23:59:59-23:59", None),
    ],
)
def test_implausible_device_times_use_the_receive_time(stamp: str, used: datetime | None) -> None:
    raw = f"<30>{stamp} CORE-R1 dhcp,info dhcp41 assigned 172.20.41.142 for 00:00:5E:00:53:48"
    outcome = parse(raw.encode())
    assert typed(outcome)[0] == "mt_dhcp"  # the lease is kept either way
    if used is not None:
        assert (outcome.message["time"], outcome.error) == (used, None)
        return
    assert outcome.message["time"] == RECEIVED
    assert outcome.rows[0].values["time"] == RECEIVED
    assert outcome.error == "mikrotik.time"
    assert outcome.message["extra"] == {"device_time": stamp}


def test_clock_skew_is_configurable() -> None:
    raw = (
        b"<30>2020-01-01T00:00:00Z CORE-R1 dhcp,info dhcp41 assigned 172.20.41.142 for "
        b"00:00:5E:00:53:48"
    )
    unlimited = parse(raw, MikroTikParser(max_clock_skew=None))
    assert (unlimited.message["time"], unlimited.error) == (datetime(2020, 1, 1, tzinfo=UTC), None)
    strict = MikroTikParser(max_clock_skew=timedelta(seconds=0.5))
    assert parse(wire(SAMPLE[0]), strict).error == "mikrotik.time"  # 0.620 s early
    overflow = parse(raw.replace(b"2020-01-01T00:00:00Z", b"0001-01-01T00:00:00+03:00"), strict)
    assert overflow.error == "mikrotik.time"
    unlimited_overflow = parse(
        raw.replace(b"2020-01-01T00:00:00Z", b"0001-01-01T00:00:00+03:00"),
        MikroTikParser(max_clock_skew=None),
    )
    assert (unlimited_overflow.message["time"], unlimited_overflow.error) == (
        RECEIVED,
        "mikrotik.time",
    )


def test_script_keywords_only_count_as_whole_words() -> None:
    for message in (b"xARP-LOG 172.20.10.23 00:00:5E:00:53:43 e1", b"ARP-LOGS rotated"):
        outcome = parse(STAMP.encode() + b"script,info " + message)
        assert (outcome.error, outcome.message["type"], outcome.rows) == (None, "log", ())
    beat = parse(STAMP.encode() + b"script,info my-ARCI-HEARTBEAT x")
    assert beat.message["type"] == "log"
