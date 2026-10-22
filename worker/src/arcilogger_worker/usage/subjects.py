"""Who an internal address belonged to during a day: the subject timeline of every source IP.

The usage reports name the same owner as a 5651 lookup would for that moment, so this module
does not have its own rules: it asks :class:`arcilogger_worker.lookup.attribution.Attributor`
(lease → device → person at T through the assignment history, IP conflicts, infrastructure
devices) — the very code of the lookup engine — at the moments where the answer can change.

**Subject** (spec 10.1): the person the device was assigned to at that moment, else the device
(the MAC of the lease), else the bare IP address (no lease covers it). Keys:
``person:<id>``, ``device:<mac>``, ``ip:<address>``.

The answer for an IP changes only where a lease of it starts or ends (an open lease: where it
would time out without further evidence) or where an assignment of one of its MACs starts or
ends. :func:`build_timelines` splits the day at exactly these moments and evaluates the chain
once per piece (at its midpoint, so no boundary rule of the chain can tip the result), then
joins neighbouring pieces with the same owner. A minute bucket belongs to the subject of its
first instant (:meth:`IpTimeline.at`).

:func:`timelines_hash` fingerprints the result: the re-attribution check (:mod:`.jobs`) compares
it with the one stored for the day and rebuilds the day when a device was (re)assigned or the
lease timeline changed.
"""

from __future__ import annotations

import bisect
import hashlib
import ipaddress
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_db.models import Vlan
from arcilogger_db.settings import get_settings
from arcilogger_worker.lookup.attribution import Attributor
from arcilogger_worker.lookup.types import LookupSettings


@dataclass(frozen=True)
class Subject:
    key: str
    kind: str
    person_id: int | None
    mac: str | None
    #: The VLAN of the address (lease or ``vlans`` table).
    vlan: int | None


@dataclass(frozen=True)
class IpTimeline:
    ip: str
    #: Epoch seconds where each piece starts (the first is the start of the day).
    starts: tuple[float, ...]
    subjects: tuple[Subject, ...]

    def at(self, moment: float) -> Subject:
        if len(self.starts) == 1:
            return self.subjects[0]
        index = bisect.bisect_right(self.starts, moment) - 1
        return self.subjects[max(index, 0)]


def lookup_settings(session: Session) -> LookupSettings:
    return LookupSettings.from_settings(get_settings(session))


def build_timelines(
    session: Session,
    ips: Iterable[str],
    low: datetime,
    high: datetime,
    *,
    settings: LookupSettings | None = None,
) -> dict[str, IpTimeline]:
    """The subject timeline of every address in ``ips`` over ``[low, high)``."""
    wanted = sorted(set(ips))
    attributor = Attributor(session, settings or lookup_settings(session))
    attributor.prefetch([(ip, low) for ip in wanted] + [(ip, high) for ip in wanted])
    vlans = VlanIndex(session)
    result: dict[str, IpTimeline] = {}
    for ip in wanted:
        cuts = {low}
        leases = attributor.leases_overlapping(ip, low, high)
        for lease in leases:
            cuts.add(lease.start)
            cuts.add(attributor.covered_until(lease))
            for slot in attributor.assignments(lease.mac):
                cuts.add(slot.valid_from)
                if slot.valid_to is not None:
                    cuts.add(slot.valid_to)
        points = sorted(cut for cut in cuts if low <= cut < high)
        starts: list[float] = []
        subjects: list[Subject] = []
        for index, start in enumerate(points):
            end = points[index + 1] if index + 1 < len(points) else high
            middle = start + (end - start) / 2
            subject = _subject(attributor, ip, middle, vlans) if leases else None
            if subject is None:
                subject = Subject(f"ip:{ip}", "ip", None, None, vlans(ip))
            if subjects and subjects[-1] == subject:
                continue
            starts.append(start.timestamp())
            subjects.append(subject)
        result[ip] = IpTimeline(ip, tuple(starts), tuple(subjects))
    return result


class VlanIndex:
    """VLAN id of the most specific ``vlans`` subnet containing an address (the rule of
    :func:`arcilogger_db.network.find_vlan`), with the table read once per run."""

    def __init__(self, session: Session) -> None:
        rows = session.execute(select(Vlan.subnet, Vlan.vlan_id))
        self.networks = sorted(
            ((ipaddress.ip_network(str(subnet)), vlan_id) for subnet, vlan_id in rows),
            key=lambda item: -item[0].prefixlen,
        )
        self._cache: dict[str, int | None] = {}

    def __call__(self, ip: str) -> int | None:
        if ip not in self._cache:
            address = ipaddress.ip_address(ip)
            self._cache[ip] = next(
                (
                    vlan_id
                    for network, vlan_id in self.networks
                    if address.version == network.version and address in network
                ),
                None,
            )
        return self._cache[ip]


def _subject(attributor: Attributor, ip: str, at: datetime, vlans: VlanIndex) -> Subject | None:
    chain = attributor.chain(ip, at)
    if chain is None or chain.device is None:
        return None
    mac = chain.device.mac
    vlan = chain.vlan if chain.vlan is not None else vlans(ip)
    if chain.person is not None:
        person_id = chain.person.person_id
        return Subject(f"person:{person_id}", "person", person_id, mac, vlan)
    return Subject(f"device:{mac}", "device", None, mac, vlan)


def timelines_hash(timelines: Mapping[str, IpTimeline]) -> str:
    """SHA-256 of who every address belonged to when (order independent)."""
    data = [
        [ip, [[start, s.key, s.mac, s.vlan] for start, s in zip(t.starts, t.subjects, strict=True)]]
        for ip, t in sorted(timelines.items())
    ]
    return hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest()
