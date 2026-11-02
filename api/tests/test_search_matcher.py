"""The matcher (contract 2.14.3, 3.1): every field and value form on hand-built records, the
typed and the ``messages`` driver, NULL-safe negation, ASCII-only case folding, the site's
VLANs, departments and network sources, and real parser output."""

from __future__ import annotations

import ipaddress
from datetime import date, datetime, timedelta, timezone
from typing import Any

import pytest

from arcilogger_api.search import Record, SearchQueryError, SiteNetwork, compile_matcher
from arcilogger_api.search import parse_query as parse
from arcilogger_api.search.fields import DepartmentEntry
from arcilogger_core.rawlog import format_line
from arcilogger_db.kinds import CLIENT_IP, TYPED_TABLES
from arcilogger_ingest.prepare import StreamInfo, prepare_line

SITE = SiteNetwork(
    vlans={
        41: ipaddress.ip_network("172.20.41.0/24"),
        42: ipaddress.ip_network("172.20.42.0/24"),
        43: ipaddress.ip_network("172.20.43.0/24"),
        60: ipaddress.ip_network("172.20.60.0/24"),
    },
    departments=(
        DepartmentEntry(1, "Finans", (41,)),
        DepartmentEntry(2, "Planlama", (42,)),
        DepartmentEntry(3, "İdari İşler", (43,)),
        DepartmentEntry(4, "Depo", ()),
    ),
    network_sources=frozenset({"ap"}),
)


def _message(**values: Any) -> dict[str, Any]:
    message = {
        "type": "traffic",
        "subtype": "forward",
        "source": "fortigate",
        "sender": "172.31.255.1",
        "severity": 5,
        "host": "FW-01",
        "program": None,
        "text": 'date=2026-10-26 devname="FW-01" logid="0000000013" type="traffic"',
        "parse_error": None,
    }
    message.update(values)
    return message


def _record(table: str | None = None, row: dict[str, Any] | None = None, **message: Any) -> Record:
    return Record(_message(**message), table, row)


WEB = _record(
    "fg_web",
    {
        "srcip": "172.20.41.105",
        "dstip": "203.0.113.10",
        "srcport": 50123,
        "dstport": 443,
        "hostname": "www.Example.com",
        "url": "/Login?next=1",
        "catdesc": "Social Networking",
        "action": "blocked",
        "sessionid": 77,
        "logid": "0316013056",
    },
    type="utm",
    subtype="webfilter",
)
TRAFFIC = _record(
    "fg_traffic",
    {
        "srcip": "172.20.42.76",
        "dstip": "198.51.100.77",
        "transip": "203.0.113.2",
        "transport": 40123,
        "srcport": 52790,
        "dstport": 3389,
        "proto": 6,
        "action": "deny",
        "policyid": 12,
        "policyname": "Internet-Out",
        "app": "RDP",
        "appcat": "Remote.Access",
        "service": "RDP",
        "dstcountry": "Reserved",
        "srcintf": "port2",
        "dstintf": "wan1",
        "sessionid": 9001,
    },
)
NO_ACTION = _record("fg_traffic", {"srcip": "172.20.43.9", "action": None, "proto": 17})
DNS = _record(
    "fg_dns",
    {"srcip": "172.20.60.163", "qname": "mail.example.com", "qtype": "AAAA", "action": None},
    type="utm",
    subtype="dns",
)
APP = _record(
    "fg_app",
    {"srcip": "fe80::1", "hostname": "my-example.com", "app": "YouTube", "proto": 6},
    type="utm",
    subtype="app-ctrl",
)
EVENT = _record(
    "fg_event",
    {"srcip": None, "user_name": "admin", "level": "notice", "action": "Edit"},
    type="event",
    subtype="system",
)
DHCP = _record(
    "mt_dhcp",
    {"ip": "172.20.41.142", "mac": "00:00:5e:00:53:48", "action": "assigned"},
    type="dhcp",
    subtype="assigned",
    source="core-r1",
    host="CORE-R1",
    program="dhcp,info",
    severity=6,
)
POOL = _record(
    "mt_event",
    {"event": "dhcp_pool_empty", "mac": None, "ip": None, "level": "warning"},
    type="dhcp",
    subtype="pool_empty",
    source="core-r1",
)
WIFI = _record(
    "mt_event",
    {"event": "wifi_connect", "mac": "02:00:5e:00:53:5b", "interface": "wlan1", "ip": None},
    type="wifi",
    subtype="connected",
    source="core-r1",
)
FW = _record(
    "mt_fw",
    {
        "src": "172.20.61.118",
        "dst": "172.20.20.44",
        "sport": 41822,
        "dport": 443,
        "proto": "TCP",
        "src_mac": "02:00:5e:00:53:5e",
        "in_if": "vlan61",
        "out_if": "vlan20",
    },
    type="fw",
    source="core-r1",
)
ACCOUNT = _record(
    "mt_account",
    {"action": "login_failure", "user_name": "NetOps", "ip": "172.20.10.37"},
    type="account",
    subtype="login_failure",
    source="core-r1",
)
SYSLOG = _record(
    type="syslog",
    subtype=None,
    source="printer",
    sender="172.20.10.40",
    host="printer-2",
    severity=3,
    text="job 17 printed in İstanbul office",
)
MEMBER = _record(type="syslog", subtype=None, source="ap-172.20.10.5", sender="172.20.10.5")
FAILED = _record(type="unknown", subtype=None, parse_error="fortigate.kv", severity=None)
DROPPED = _record(type="traffic")  # its typed row was dropped by the row checks

RECORDS = {
    "WEB": WEB,
    "TRAFFIC": TRAFFIC,
    "NO_ACTION": NO_ACTION,
    "DNS": DNS,
    "APP": APP,
    "EVENT": EVENT,
    "DHCP": DHCP,
    "POOL": POOL,
    "WIFI": WIFI,
    "FW": FW,
    "ACCOUNT": ACCOUNT,
    "SYSLOG": SYSLOG,
    "MEMBER": MEMBER,
    "FAILED": FAILED,
    "DROPPED": DROPPED,
}


@pytest.mark.parametrize(
    ("query", "expected"),
    [
        ("", set(RECORDS)),
        # the fields of every table and the kinds
        (
            "source:fortigate",
            {"WEB", "TRAFFIC", "NO_ACTION", "DNS", "APP", "EVENT", "FAILED", "DROPPED"},
        ),
        ("source:core-*", {"DHCP", "POOL", "WIFI", "FW", "ACCOUNT"}),
        ("-source:fortigate,core-r1", {"SYSLOG", "MEMBER"}),
        ("source:ap", {"MEMBER"}),  # a network source and its members
        ("source:printer*", {"SYSLOG"}),
        ("sender:172.20.10.0/24", {"SYSLOG", "MEMBER"}),
        ("type:web", {"WEB"}),
        ("type:dhcp", {"DHCP", "POOL"}),
        ("type:traffic", {"TRAFFIC", "NO_ACTION", "DROPPED"}),
        ("-type:traffic,web,dns,app,event,dhcp,wifi,fw,account", {"SYSLOG", "MEMBER", "FAILED"}),
        ("type:dhcp mac:*", {"DHCP"}),
        ("type:dhcp,wifi mac:*", {"DHCP", "WIFI"}),
        ("-type:dhcp mac:*", {"WIFI", "FW"}),
        # messages-only fields
        ("host:core-r1", {"DHCP"}),
        ("host:FW-*", set(RECORDS) - {"SYSLOG", "DHCP"}),
        ("program:dhcp*", {"DHCP"}),
        ("subtype:pool_empty", {"POOL"}),
        ("severity:<=err", {"SYSLOG"}),
        ("severity:info", {"DHCP"}),
        ("-severity:*", {"FAILED"}),
        ("error:fortigate.*", {"FAILED"}),
        ("error:*", {"FAILED"}),
        ("-error:*", set(RECORDS) - {"FAILED"}),
        # free text: ASCII letters only fold (İ is matched as typed)
        ("printed", {"SYSLOG"}),
        ('"JOB 17 PRINTED"', {"SYSLOG"}),
        ("istanbul", set()),
        ("İstanbul", {"SYSLOG"}),
        ("-printed", set(RECORDS) - {"SYSLOG"}),
        ("text:printed,office", {"SYSLOG"}),
        ('srcip:172.20.41.105 "type=\\"traffic\\""', {"WEB"}),  # typed query, messages text
        # addresses
        ("srcip:172.20.41.105", {"WEB"}),
        ("srcip:172.20.41.0/24", {"WEB", "DHCP"}),
        ("srcip:172.20.*", {"WEB", "TRAFFIC", "NO_ACTION", "DNS", "DHCP", "FW", "ACCOUNT"}),
        ("srcip:fe80::/64", {"APP"}),
        ("-srcip:172.20.*", {"APP", "EVENT", "POOL", "WIFI"}),
        ("dstip:198.51.100.77", {"TRAFFIC"}),
        ("dstip:172.20.20.44", {"FW"}),
        ("natip:203.0.113.2", {"TRAFFIC"}),
        ("ip:203.0.113.2", {"TRAFFIC"}),
        ("ip:203.0.113.10", {"WEB"}),
        ("ip:172.20.10.37", {"ACCOUNT"}),
        # ports, protocols, numbers
        ("dstport:3389", {"TRAFFIC"}),
        ("dstport:443 type:fw", {"FW"}),
        ("dstport:400-500", {"WEB", "FW"}),
        ("srcport:>50000", {"WEB", "TRAFFIC"}),
        ("natport:40123", {"TRAFFIC"}),
        ("port:41822", {"FW"}),
        ("port:<=443", {"WEB", "FW"}),
        ("proto:tcp", {"TRAFFIC", "APP", "FW"}),
        ("proto:udp", {"NO_ACTION"}),
        ("proto:6 type:fw", {"FW"}),
        ("sessionid:9001", {"TRAFFIC"}),
        ("sessionid:>=1000", {"TRAFFIC"}),
        ("policy:12", {"TRAFFIC"}),
        ("policy:internet*", {"TRAFFIC"}),
        ("-policy:13", {"TRAFFIC", "NO_ACTION"}),
        # tokens: lower-cased on both sides
        ("action:deny,blocked", {"TRAFFIC", "WEB"}),
        ("action:EDIT", {"EVENT"}),
        ("-action:deny", {"WEB", "NO_ACTION", "DNS", "APP", "EVENT", "DHCP", "ACCOUNT"}),
        ("qtype:aaaa", {"DNS"}),
        ("logid:0316013056", {"WEB"}),
        ("level:warning", {"POOL"}),
        # names and text
        ("domain:example.com", {"WEB", "DNS"}),  # not my-example.com
        ("domain:*.example.com", {"WEB", "DNS"}),
        ("domain:www.example.com", {"WEB"}),
        ("-domain:example.com", {"APP"}),
        ("qname:mail.example.com", {"DNS"}),
        ("qname:MAIL.example.com", {"DNS"}),  # the input is lower-cased …
        ("url:login", {"WEB"}),
        ("url:*?next*", {"WEB"}),
        ("app:youtube", {"APP"}),
        ("app:you*", {"APP"}),
        ("app:*tube", {"APP"}),
        ("app:*OUT*", {"APP"}),
        ('app:"you*"', set()),  # quoted: literal
        ("appcat:remote.access", {"TRAFFIC"}),
        ("category:*social*", {"WEB"}),
        ("service:rdp", {"TRAFFIC"}),
        ("country:reserved", {"TRAFFIC"}),
        ("user:netops", {"ACCOUNT"}),
        ("user:adm*", {"EVENT"}),
        ("interface:wan1", {"TRAFFIC"}),
        ("interface:vlan2*", {"FW"}),
        ("interface:wlan1", {"WIFI"}),
        # MAC addresses
        ("mac:00-00-5E-00-53-48", {"DHCP"}),
        ("mac:02:00:5e:*", {"WIFI", "FW"}),
        ("mac:00:00:5e:00:53:*", {"DHCP"}),
        ("-mac:*", {"POOL"}),
        # VLANs and departments (by the client address)
        ("vlan:41", {"WEB", "DHCP"}),
        ("vlan:42,60", {"TRAFFIC", "DNS"}),
        (
            "-vlan:41",
            {"TRAFFIC", "NO_ACTION", "DNS", "APP", "EVENT", "POOL", "WIFI", "FW", "ACCOUNT"},
        ),
        ("department:finans", {"WEB", "DHCP"}),
        ('department:"İDARİ İŞLER"', {"NO_ACTION"}),
        ("department:depo", set()),
        ("department:2", {"TRAFFIC"}),
        # NULL-safe negation: a typed table without the value matches, no typed row never does
        (
            "-action:accept",
            {"WEB", "TRAFFIC", "NO_ACTION", "DNS", "APP", "EVENT", "DHCP", "ACCOUNT"},
        ),
        ("action:*", {"WEB", "TRAFFIC", "EVENT", "DHCP", "ACCOUNT"}),
    ],
)
def test_fields_and_value_forms(query: str, expected: set[str]) -> None:
    matcher = compile_matcher(parse(query), SITE)
    assert {name for name, record in RECORDS.items() if matcher(record)} == expected


def test_qname_compares_as_stored() -> None:
    upper = _record("fg_dns", {"qname": "MAIL.example.com"}, type="utm", subtype="dns")
    assert not compile_matcher(parse("qname:mail.example.com"))(upper)
    assert compile_matcher(parse("domain:mail.example.com"))(upper)  # domain folds


def test_the_driver() -> None:
    typed = compile_matcher(parse("srcip:172.20.41.105 printed"))
    assert typed.typed
    assert typed.tables == tuple(table for table in TYPED_TABLES if table in CLIENT_IP)
    messages = compile_matcher(parse("type:web printed"))
    assert not messages.typed
    assert messages.tables is None
    assert messages.kinds == {"web"}
    # a typed query never matches a record without a typed row, or one of another table
    assert not compile_matcher(parse("-action:accept"))(DROPPED)
    other_table = Record(WEB.message, "mt_interface", WEB.row)
    assert not compile_matcher(parse("srcip:172.20.41.105"))(other_table)
    assert not compile_matcher(parse("srcip:*"))(Record(WEB.message, "fg_web", None))
    unknown_event = _record("mt_event", {"event": "new_thing", "ip": "172.20.41.7"}, type="log")
    assert not compile_matcher(parse("srcip:172.20.41.7"))(unknown_event)


def test_compile_errors() -> None:
    with pytest.raises(SearchQueryError) as caught:
        compile_matcher(parse("vlan:99"), SITE)
    assert (caught.value.code, caught.value.start, caught.value.end) == (
        "search_vlan_unknown",
        0,
        7,
    )
    with pytest.raises(SearchQueryError) as caught:
        compile_matcher(parse("srcip:* department:Muhasebe"), SITE)
    assert caught.value.code == "search_department_unknown"
    with pytest.raises(SearchQueryError) as caught:
        compile_matcher(parse("type:syslog mac:*"), SITE)
    assert caught.value.code == "search_fields_incompatible"
    with pytest.raises(SearchQueryError):
        compile_matcher(parse("vlan:41"))  # no site data: no VLAN is known


def test_the_live_tail_helpers() -> None:
    plain = compile_matcher(parse("source:fortigate,core-* -source:core-r2 sender:172.31.255.1"))
    assert not plain.needs_parse
    assert plain.wants_source("fortigate")
    assert plain.wants_source("core-r1")
    assert not plain.wants_source("core-r2")
    assert not plain.wants_source("printer")
    assert compile_matcher(parse("source:ap"), SITE).wants_source("ap-172.20.10.5")
    assert compile_matcher(parse("srcip:*")).wants_source("anything")
    assert compile_matcher(parse("printed")).needs_parse
    assert compile_matcher(parse('-foo "bar baz" text:a1b,c2d')).terms == ("bar baz",)


# -- real parser output ---------------------------------------------------------------------------

RECEIVED = datetime(2026, 9, 23, 9, 44, 57, 123456, tzinfo=timezone(timedelta(hours=3)))
LINES = {
    "dhcp": (
        "mikrotik",
        b"<30>2026-09-23T09:38:26.380+03:00 CORE-R1 dhcp,info dhcp41 assigned 172.20.41.142 "
        b"for 00:00:5E:00:53:48 FIN-PC03",
    ),
    "web": (
        "fortigate",
        b'<189>date=2026-09-23 time=09:44:50 devname="FW-01" devid="FGT60F0000000000" '
        b'eventtime=1790138690000000000 tz="+0300" logid="0316013056" type="utm" '
        b'subtype="webfilter" eventtype="ftgd_blk" level="warning" vd="root" policyid=12 '
        b'sessionid=77 srcip=172.20.41.105 srcport=50123 srcintf="port2" dstip=203.0.113.10 '
        b'dstport=443 dstintf="wan1" proto=6 service="HTTPS" hostname="www.example.com" '
        b'action="blocked" url="https://www.example.com/" catdesc="Social Networking"',
    ),
}


def _prepared(kind: str) -> Record:
    source_type, message = LINES[kind]
    raw = format_line(RECEIVED, "172.20.10.1", message).rstrip(b"\n")
    prepared = prepare_line(StreamInfo("example", source_type, date(2026, 9, 23)), 1, 0, raw)  # type: ignore[arg-type]
    rows = dict(prepared.rows)
    typed = [(table, values) for table, values in prepared.rows if table != "messages"]
    table, row = typed[0] if typed else (None, None)
    return Record(rows["messages"], table, row)


@pytest.mark.parametrize(
    ("kind", "query", "expected"),
    [
        ("dhcp", "type:dhcp mac:00:00:5e:00:53:48", True),
        ("dhcp", "srcip:172.20.41.0/24 action:assigned", True),
        ("dhcp", "vlan:41 host:core-r1", True),
        ("dhcp", "type:dhcp -action:assigned", False),
        ("dhcp", "fin-pc03", True),
        ("web", "srcip:172.20.41.105 type:web action:blocked", True),
        ("web", "domain:example.com -action:passthrough", True),
        ("web", "department:Finans category:social*", True),
        ("web", "type:traffic", False),
        ("web", "logid:0316013056 dstport:443", True),
    ],
)
def test_lines_as_ingest_prepares_them(kind: str, query: str, expected: bool) -> None:
    assert compile_matcher(parse(query), SITE)(_prepared(kind)) is expected
