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
