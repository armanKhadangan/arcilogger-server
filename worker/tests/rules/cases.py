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


# -- W6-C: access and devices (R03, R04, R11, R12, R16) -------------------------------------------
#
# The common columns are the writer's: these cases assume a MikroTik row's ``source`` is
# ``core-r1`` and a FortiGate row's ``fortigate`` (the sources of ``arcilogger_worker.testing``),
# and that a ``devices`` row gets ``created_at`` = its receive time and ``first_seen`` = its
# device time (R11 reads the insert time, deviation in the contract). SLICE_END is a Thursday,
# 12:45 site time: within the default work hours.

_FAILED_LOGIN: Final = {
    "logid": "0100032002",
    "subtype": "system",
    "logdesc": "Admin login failed",
    "user_name": "admin",
    "srcip": "172.20.41.196",
    "action": "login",
    "status": "failed",
}
_ROUTER_LOGIN: Final = {"action": "login", "user_name": "netops", "via": "winbox"}

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
                    {"action": "login_failure", "user_name": "admin", "ip": "172.20.41.196"},
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
                    {"ip": "172.20.43.50", "mac": mac, "interface": "vlan43"},
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
                    {"ip": "172.20.43.50", "mac": mac, "interface": "vlan43"},
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
                    {"action": "changed", "user_name": "admin", "target": "filter rule"},
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
                        "logid": "9900000010",
                        "logdesc": "Configuration changed",
                        "user_name": "admin",
                    },
                ),
            ),
        ),
    ]
)
