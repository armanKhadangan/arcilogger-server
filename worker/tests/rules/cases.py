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
