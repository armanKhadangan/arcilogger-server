"""The positive and negative cases of the security rules (Wave 6 contract §3, T-A1 and T-R).

Every rule of the registry has at least one positive case (it raises the alerts listed in
:attr:`RuleCase.expected`) and one negative case (just below the threshold: no alert). A case is
data only: the rows it writes into the parsed tables (:class:`CaseRow`), the overrides of its
rule's settings (``rules.<ID>`` shape) and what the rule must answer. The engine tests write the
rows with the common columns filled in (``source``, ``sender``, the raw reference), evaluate the
rule on the slice that ends at :data:`SLICE_END` and compare the hits.

Each rule package appends **one block** of cases for its rules (append-only, contract §4.1):
W6-B network, W6-C access, W6-D threats, W6-E people. Every value comes from the fictional world
(Örnek A.Ş., ``172.20.x``, ``00:00:5E:00:53:xx``, RFC 5737 addresses, ``*.example`` names).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Final

#: The end of the slice every case is evaluated on (receive time); the slice is
#: ``rules.max_slice_seconds`` long unless the case says otherwise.
SLICE_END: Final = datetime(2026, 11, 12, 9, 45, tzinfo=UTC)


@dataclass(frozen=True)
class CaseRow:
    """One row of a parsed table (or ``devices``) a case writes."""

    table: str
    #: Receive time relative to :data:`SLICE_END` (negative: before it).
    received: timedelta
    #: The table's own columns (``srcip``, ``dstport``, ``action`` …).
    values: Mapping[str, object]
    #: Device time minus receive time (a device clock that is off, decision D8).
    device_offset: timedelta = timedelta(0)
    #: The same row this many times, one second apart in receive time (bursts).
    repeat: int = 1


@dataclass(frozen=True)
class ExpectedHit:
    """An alert a positive case must raise."""

    key: str
    count: int
    #: Values the hit must carry (a subset of ``Hit.values``).
    values: Mapping[str, object] = field(default_factory=dict)
    #: Evidence rows the hit must reference (``rules.evidence_max`` caps them).
    evidence: int | None = None


@dataclass(frozen=True)
class RuleCase:
    rule: str
    #: Short name, unique within the rule (the test id).
    name: str
    rows: tuple[CaseRow, ...]
    #: Empty for a negative case.
    expected: tuple[ExpectedHit, ...] = ()
    #: ``rules.<ID>`` overrides for this case (``{"params": {"threshold": 3}}`` …).
    overrides: Mapping[str, object] = field(default_factory=dict)
    #: Other settings the case needs (``rules.internal_networks``, ``usage.work_hours`` …).
    settings: Mapping[str, object] = field(default_factory=dict)
    #: Length of the evaluated slice; ``None``: ``rules.max_slice_seconds``.
    slice_seconds: int | None = None

    @property
    def positive(self) -> bool:
        return bool(self.expected)


#: Every case, one block per rule package, in the order the packages merge.
CASES: Final[list[RuleCase]] = []

# -------------------------------------------------------------------------------------------------
# W6-D — threats and health (R05, R06, R07, R17, R18; contract §2.2.3)
# -------------------------------------------------------------------------------------------------
#: ``source`` in a row's values names the source (the keys of R17 and R18 contain it).

_IPS: Final[Mapping[str, object]] = {
    "logid": "9900000006",
    "eventtype": "signature",
    "srcip": "198.51.100.7",
    "srcport": 40123,
    "dstip": "172.20.41.17",
    "dstport": 443,
    "proto": 6,
    "service": "HTTPS",
    "attack": "Synthetic.Signature.Remote.Code.Execution",
    "attackid": 99001,
    "severity": "high",
    "action": "dropped",
}
_VIRUS: Final[Mapping[str, object]] = {
    "logid": "9900000007",
    "eventtype": "infected",
    "srcip": "203.0.113.30",
    "dstip": "172.20.41.110",
    "virus": "Synthetic.Test.File",
    "virusid": 99002,
    "dtype": "Virus",
    "filename": "eicar.com",
    "url": "http://downloads.example/eicar.com",
    "hostname": "downloads.example",
    "action": "blocked",
}


def _perf(cpu: int, mem: int) -> Mapping[str, object]:
    return {
        "source": "fortigate",
        "logid": "0100040704",
        "subtype": "system",
        "level": "notice",
        "logdesc": "System performance statistics",
        "action": "perf-stats",
        "extra": {"cpu": str(cpu), "mem": str(mem)},
    }


def _samples(*values: tuple[int, int]) -> tuple[CaseRow, ...]:
    """Performance samples one minute apart, the newest 10 s before the slice end."""
    newest = timedelta(seconds=-10)
    return tuple(
        CaseRow("fg_event", newest - timedelta(minutes=len(values) - 1 - index), _perf(cpu, mem))
        for index, (cpu, mem) in enumerate(values)
    )


_POOL: Final[Mapping[str, object]] = {
    "source": "core-r1",
    "event": "dhcp_pool_empty",
    "topics": "dhcp,warning",
    "level": "warning",
    "text": "dhcp60 failed to give out IP address: pool <pool60> is empty",
    "extra": {"server": "dhcp60", "vlan": 60, "pool": "pool60"},
}

CASES.extend(
    [
        RuleCase(
            "R05",
            "high-severity-each",
            (CaseRow("fg_ips", timedelta(seconds=-30), _IPS),),
            (
                ExpectedHit(
                    "198.51.100.7:99001",
                    1,
                    {"srcip": "198.51.100.7", "attack": _IPS["attack"]},
                    evidence=1,
                ),
            ),
        ),
        RuleCase(
            "R05",
            "medium-at-threshold",
            (CaseRow("fg_ips", timedelta(seconds=-20), {**_IPS, "severity": "medium"}, repeat=10),),
            (ExpectedHit("198.51.100.7:99001", 10, evidence=10),),
        ),
        RuleCase(
            "R05",
            "medium-below-threshold",
            (CaseRow("fg_ips", timedelta(seconds=-20), {**_IPS, "severity": "medium"}, repeat=9),),
        ),
        RuleCase(
            "R06",
            "botnet",
            (CaseRow("fg_ips", timedelta(seconds=-30), {**_IPS, "eventtype": "botnet"}),),
            (ExpectedHit("198.51.100.7", 1, {"srcip": "198.51.100.7"}, evidence=1),),
        ),
        RuleCase(
            "R06",
            "signature-is-not-botnet",
            (CaseRow("fg_ips", timedelta(seconds=-30), _IPS),),
        ),
        RuleCase(
            "R07",
            "malware",
            (CaseRow("fg_virus", timedelta(seconds=-30), _VIRUS),),
            (
                ExpectedHit(
                    "203.0.113.30:99002",
                    1,
                    {"virus": "Synthetic.Test.File", "srcip": "203.0.113.30"},
                    evidence=1,
                ),
            ),
        ),
        RuleCase(
            "R07",
            "ignored-action",
            (CaseRow("fg_virus", timedelta(seconds=-30), _VIRUS),),
            overrides={"params": {"ignore_actions": ["blocked"]}},
        ),
        RuleCase(
            "R17",
            "cpu-sustained",
            _samples((20, 30), *[(95, 30)] * 11),
            (ExpectedHit("fortigate", 11, {"cpu": 95, "conserve": False}, evidence=11),),
        ),
        RuleCase(
            "R17",
            "cpu-not-yet-sustained",
            _samples((20, 30), *[(95, 30)] * 10),
        ),
        RuleCase(
            "R17",
            "conserve-mode",
            (
                CaseRow(
                    "fg_event",
                    timedelta(seconds=-30),
                    {
                        "source": "fortigate",
                        "logid": "9900000060",
                        "subtype": "system",
                        "level": "critical",
                        "logdesc": "Kernel enters memory conserve mode",
                    },
                ),
            ),
            (ExpectedHit("fortigate", 1, {"conserve": True}, evidence=1),),
        ),
        RuleCase(
            "R18",
            "mikrotik-pool-empty",
            (CaseRow("mt_event", timedelta(seconds=-30), _POOL),),
            (ExpectedHit("core-r1:dhcp60", 1, {"pool": "pool60"}, evidence=1),),
        ),
        RuleCase(
            "R18",
            "other-dhcp-failure",
            (CaseRow("mt_event", timedelta(seconds=-30), {**_POOL, "event": "dhcp_failed"}),),
        ),
    ]
)

# -------------------------------------------------------------------------------------------------
# W6-E — people (R09, R13, R14; contract §2.2.4). The case rows have no leases, so the key is the
# address (``ip:<srcip>``); the person keys are tested with leases in ``test_people.py``.
# -------------------------------------------------------------------------------------------------

_MIB = 1024 * 1024
_PC = "172.20.43.37"


def _blocked_web(count: int) -> CaseRow:
    return CaseRow(
        "fg_web",
        timedelta(seconds=-300),
        {
            "srcip": _PC,
            "dstip": "198.51.100.20",
            "dstport": 443,
            "hostname": "proxy.example",
            "catdesc": "Proxy Avoidance",
            "action": "blocked",
        },
        repeat=count,
    )


def _sent(received: timedelta, sessionid: int, sent: int, rcvd: int = 2000) -> CaseRow:
    return CaseRow(
        "fg_traffic",
        received,
        {
            "subtype": "forward",
            "action": "accept",
            "sessionid": sessionid,
            "srcip": _PC,
            "srcport": 50000 + sessionid % 1000,
            "dstip": "203.0.113.10",
            "dstport": 443,
            "sentbyte": sent,
            "rcvdbyte": rcvd,
            "is_interim": False,
        },
    )


#: R14 cases: a "night" of 12:00-13:00 site time, which holds the slice (12:35-12:45).
_NOON = {
    "params": {"user_networks": ["172.20.43.0/24"], "night_start": 43_200, "night_end": 46_800}
}

CASES.extend(
    [
        RuleCase(
            "R09",
            "blocked-20",
            (_blocked_web(20),),
            (ExpectedHit(f"ip:{_PC}", 20, {"srcip": _PC, "count": 20}, evidence=20),),
        ),
        RuleCase("R09", "blocked-19", (_blocked_web(19),)),
        RuleCase(
            "R13",
            "sent-1100-mib",
            (_sent(timedelta(seconds=-60), 1001, 1100 * _MIB),),
            (ExpectedHit(f"ip:{_PC}", 1, {"bytes": 1100 * _MIB, "period": "hour"}, evidence=1),),
        ),
        RuleCase("R13", "sent-1-gib", (_sent(timedelta(seconds=-60), 1001, 1024 * _MIB),)),
        RuleCase(
            "R14",
            "night-10-mib",
            (
                _sent(timedelta(seconds=-1800), 1002, 4 * _MIB, 2 * _MIB),
                _sent(timedelta(seconds=-60), 1003, 2 * _MIB, 2 * _MIB),
            ),
            (ExpectedHit(f"ip:{_PC}", 2, {"bytes": 10 * _MIB, "night": "2026-11-12"}, evidence=2),),
            overrides=_NOON,
        ),
        RuleCase(
            "R14",
            "night-below-10-mib",
            (
                _sent(timedelta(seconds=-1800), 1002, 4 * _MIB, 2 * _MIB),
                _sent(timedelta(seconds=-60), 1003, 2 * _MIB, 2 * _MIB - 1),
            ),
            overrides=_NOON,
        ),
    ]
)


# -- W6-B network: R01, R02, R08, R10, R19 (contract §2.2.1) --------------------------------------

_SCANNER: Final = "172.20.41.17"
_OUTSIDE: Final = "203.0.113.50"


def _deny(srcip: str, seconds_before: int, **values: object) -> CaseRow:
    """A blocked FortiGate session from ``srcip``, received ``seconds_before`` the slice end."""
    columns = {"srcip": srcip, "dstip": "198.51.100.10", "dstport": 443, "action": "deny"}
    return CaseRow("fg_traffic", -timedelta(seconds=seconds_before), {**columns, **values})


def _dns_bypass(repeat: int) -> CaseRow:
    columns = {"srcip": _SCANNER, "dstip": "198.51.100.53", "dstport": 53, "action": "accept"}
    return CaseRow("fg_traffic", -timedelta(seconds=60), columns, repeat=repeat)


def _nxdomain(repeat: int) -> CaseRow:
    """DNS answers "no such name"; the response code in ``extra`` is V6 (sahada doğrula)."""
    columns = {"srcip": _SCANNER, "qname": "x7k2q9.example", "extra": {"rcode": "NXDOMAIN"}}
    return CaseRow("fg_dns", -timedelta(seconds=300), columns, repeat=repeat)


_DNS_SERVERS: Final = {"params": {"allowed_dns_servers": ["172.20.10.1"]}}

CASES.extend(
    [
        RuleCase(
            "R01",
            "ports",
            tuple(_deny(_SCANNER, 59 - n, dstport=1000 + n) for n in range(30)),
            (ExpectedHit(_SCANNER, 30, {"ports": 30, "hosts": 1}, evidence=20),),
        ),
        RuleCase(
            "R01",
            "hosts",
            tuple(_deny(_SCANNER, 59 - n, dstip=f"172.20.50.{n + 1}") for n in range(50)),
            (ExpectedHit(_SCANNER, 50, {"ports": 1, "hosts": 50}, evidence=20),),
        ),
        RuleCase(
            "R01",
            "below",
            tuple(_deny(_SCANNER, 59 - n, dstport=1000 + n) for n in range(29)),
        ),
        RuleCase(
            "R02",
            "scan",
            (
                CaseRow(
                    "fg_traffic", -timedelta(seconds=120), _deny(_OUTSIDE, 0).values, repeat=100
                ),
            ),
            (ExpectedHit(_OUTSIDE, 100, evidence=20),),
        ),
        RuleCase(
            "R02",
            "below",
            (CaseRow("fg_traffic", -timedelta(seconds=120), _deny(_OUTSIDE, 0).values, repeat=99),),
        ),
        RuleCase(
            "R08",
            "bypass",
            (_dns_bypass(20),),
            (ExpectedHit(_SCANNER, 20, {"servers": 1}, evidence=20),),
            overrides=_DNS_SERVERS,
        ),
        RuleCase("R08", "below", (_dns_bypass(19),), overrides=_DNS_SERVERS),
        RuleCase(
            "R10",
            "proxy",
            (
                CaseRow(
                    "fg_web",
                    -timedelta(seconds=30),
                    {"srcip": _SCANNER, "catdesc": "Proxy Avoidance"},
                ),
            ),
            (ExpectedHit(_SCANNER, 1, {"what": "Proxy Avoidance"}, evidence=1),),
        ),
        RuleCase(
            "R10",
            "other",
            (CaseRow("fg_web", -timedelta(seconds=30), {"srcip": _SCANNER, "catdesc": "News"}),),
        ),
        RuleCase(
            "R19",
            "dga",
            (_nxdomain(201),),
            (ExpectedHit(_SCANNER, 201, evidence=20),),
        ),
        RuleCase("R19", "at-threshold", (_nxdomain(200),)),
    ]
)


# -- W6-C: access and devices (R03, R04, R11, R12, R16) -------------------------------------------
#
# Every parsed row names its ``source`` (``core-r1`` for the router, ``fortigate``): R16's key
# holds it. A ``devices`` row is assumed to get ``created_at`` = its receive time and
# ``first_seen`` = its device time (R11 reads the insert time, deviation in the contract).
# SLICE_END is a Thursday, 12:45 site time: within the default work hours.

_FAILED_LOGIN: Final = {
    "source": "fortigate",
    "logid": "0100032002",
    "subtype": "system",
    "logdesc": "Admin login failed",
    "user_name": "admin",
    "srcip": "172.20.41.196",
    "action": "login",
    "status": "failed",
}
_ROUTER_LOGIN: Final = {
    "source": "core-r1",
    "action": "login",
    "user_name": "netops",
    "via": "winbox",
}

CASES.extend(
    [
        RuleCase(
            "R03",
            "five-failed-logins",
            (CaseRow("fg_event", timedelta(seconds=-120), _FAILED_LOGIN, repeat=5),),
            expected=(
                ExpectedHit("user:admin", 5, {"failures": 5}, evidence=5),
                ExpectedHit("ip:172.20.41.196", 5, {"failures": 5}, evidence=5),
            ),
        ),
        RuleCase(
            "R03",
            "four-failed-logins",
            (CaseRow("fg_event", timedelta(seconds=-120), _FAILED_LOGIN, repeat=4),),
        ),
        RuleCase(
            "R03",
            "router-and-firewall-together",
            (
                CaseRow("fg_event", timedelta(seconds=-90), _FAILED_LOGIN, repeat=3),
                CaseRow(
                    "mt_account",
                    timedelta(seconds=-60),
                    {
                        "source": "core-r1",
                        "action": "login_failure",
                        "user_name": "admin",
                        "ip": "172.20.41.196",
                    },
                    repeat=2,
                ),
            ),
            expected=(
                ExpectedHit("user:admin", 5),
                ExpectedHit("ip:172.20.41.196", 5),
            ),
        ),
        RuleCase(
            "R04",
            "login-from-an-unexpected-address",
            (
                CaseRow(
                    "mt_account", timedelta(seconds=-60), {**_ROUTER_LOGIN, "ip": "172.20.41.196"}
                ),
            ),
            expected=(
                ExpectedHit(
                    "user:netops",
                    1,
                    {"srcip": "172.20.41.196", "reason_en": "unexpected address"},
                    evidence=1,
                ),
            ),
            overrides={"params": {"trusted_admin_networks": ["172.20.10.0/24"]}},
        ),
        RuleCase(
            "R04",
            "login-after-work-hours",
            (
                CaseRow(
                    "mt_account", timedelta(seconds=-60), {**_ROUTER_LOGIN, "ip": "172.20.10.37"}
                ),
            ),
            expected=(ExpectedHit("user:netops", 1, {"reason_en": "off hours"}),),
            settings={
                "usage.work_hours": {"days": [1, 2, 3, 4, 5], "start": "08:00", "end": "12:00"}
            },
        ),
        RuleCase(
            "R04",
            "login-at-work-from-the-admin-network",
            (
                CaseRow(
                    "mt_account", timedelta(seconds=-60), {**_ROUTER_LOGIN, "ip": "172.20.10.37"}
                ),
            ),
            overrides={"params": {"trusted_admin_networks": ["172.20.10.0/24"]}},
        ),
        RuleCase(
            "R11",
            "a-new-device",
            (
                CaseRow(
                    "devices",
                    timedelta(seconds=-30),
                    {"mac": "00:00:5e:00:53:f0", "last_ip": "172.20.43.240", "last_vlan": 43},
                ),
            ),
            expected=(ExpectedHit("00:00:5e:00:53:f0", 1, {"ip": "172.20.43.240", "vlan": 43}),),
        ),
        RuleCase(
            "R11",
            "an-infrastructure-device",
            (
                CaseRow(
                    "devices",
                    timedelta(seconds=-30),
                    {"mac": "00:00:5e:00:53:f1", "last_vlan": 10, "infrastructure": True},
                ),
            ),
        ),
        RuleCase(
            "R12",
            "three-mac-changes",
            tuple(
                CaseRow(
                    "mt_arp",
                    timedelta(minutes=minutes),
                    {"source": "core-r1", "ip": "172.20.43.50", "mac": mac, "interface": "vlan43"},
                )
                for minutes, mac in (
                    (-30, "00:00:5e:00:53:e1"),
                    (-20, "00:00:5e:00:53:e2"),
                    (-10, "00:00:5e:00:53:e1"),
                    (-1, "00:00:5e:00:53:e2"),
                )
            ),
            expected=(
                ExpectedHit(
                    "172.20.43.50",
                    3,
                    {"changes": 3, "macs": ["00:00:5e:00:53:e1", "00:00:5e:00:53:e2"]},
                    evidence=3,
                ),
            ),
        ),
        RuleCase(
            "R12",
            "two-mac-changes",
            tuple(
                CaseRow(
                    "mt_arp",
                    timedelta(minutes=minutes),
                    {"source": "core-r1", "ip": "172.20.43.50", "mac": mac, "interface": "vlan43"},
                )
                for minutes, mac in (
                    (-20, "00:00:5e:00:53:e1"),
                    (-10, "00:00:5e:00:53:e2"),
                    (-1, "00:00:5e:00:53:e1"),
                )
            ),
        ),
        RuleCase(
            "R16",
            "router-configuration-changes",
            (
                CaseRow(
                    "mt_config",
                    timedelta(seconds=-60),
                    {
                        "source": "core-r1",
                        "action": "changed",
                        "user_name": "admin",
                        "target": "filter rule",
                    },
                    repeat=2,
                ),
            ),
            expected=(ExpectedHit("core-r1:admin", 2, {"changes": 2}, evidence=2),),
        ),
        RuleCase(
            "R16",
            "an-event-of-another-logid",
            (
                CaseRow(
                    "fg_event",
                    timedelta(seconds=-60),
                    {
                        "source": "fortigate",
                        "logid": "9900000010",
                        "logdesc": "Configuration changed",
                        "user_name": "admin",
                    },
                ),
            ),
        ),
    ]
)
