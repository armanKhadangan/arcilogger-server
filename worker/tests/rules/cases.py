"""The positive and negative cases of the security rules (Wave 6 contract §3, T-A1 and T-R).

Every rule of the registry has at least one positive case (it raises the alerts listed in
:attr:`RuleCase.expected`) and one negative case (just below the threshold: no alert). A case is
data only: the rows it writes into the parsed tables (:class:`CaseRow`), the overrides of its
rule's settings (``rules.<ID>`` shape) and what the rule must answer. ``test_rules_cases.py``
writes the rows, runs the rule through the engine on the slice that ends at :data:`SLICE_END` and
compares the alerts. The writer fills in what a row does not name:

- a parsed row gets ``source`` (``fortigate`` for an ``fg_*`` table, ``core-r1`` otherwise) unless
  its values name one, and the sender and its raw reference (``raw_day``, ``raw_line``,
  ``raw_offset``) always; ``extra`` is ``{}`` unless named;
- a ``devices`` row gets ``created_at`` = its receive time and ``first_seen`` = ``last_seen`` = its
  device time (receive time plus :attr:`CaseRow.device_offset`).

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
    #: The cooldown case: a closed alert of every expected key, this long before
    #: :data:`SLICE_END` (its ``time``). Inside the rule's cooldown the hits are counted on that
    #: alert's ``after_close`` and no alert opens; past it a new alert opens as in a positive case.
    #: The closed alert's time plus the cooldown must not fall inside the slice.
    closed_before: timedelta | None = None

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
