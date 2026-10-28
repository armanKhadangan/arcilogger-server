"""Attribution chain: internal IP + moment → lease → device → person / department (spec 9.4).

:class:`Attributor` builds chains for one lookup and caches what several matches share
(devices, persons, VLANs); :meth:`Attributor.prefetch` loads the leases of many IPs at once, so
a candidate list of 100 sessions costs a handful of queries.

Which leases cover a moment ``at`` (``[start, end)``):

* a closed lease covers ``start <= at < end``;
* an open lease (``end`` NULL) covers ``at`` up to the moment it would time out without further
  evidence: ``last_evidence + lease timeout`` (DHCP) or ``last_evidence + ARP interval`` (ARP).

Several MACs on one IP at ``at`` is an **IP conflict** (spec 9.1): the most likely chain (non-
infrastructure first, then DHCP before ARP, then the newest assignment) carries the others in
``alternatives``; that chain and every alternative have confidence ``low`` and the
``ip_conflict`` explanation. Infrastructure MACs
(setting ``devices.infrastructure_macs`` or the device flag) are excluded from conflicts, as in
the conflict alerts. The department is the linked person's, else the VLAN's
(:mod:`arcilogger_db.network`, the one rule shared with the usage reports).

**Person step** (ADR-0008): the assignment of the device valid at the moment
(``device_assignments``, ``valid_from <= at < valid_to``; :mod:`arcilogger_db.assignments`):

* a row covers ``at`` → that person, ``link="history"`` with ``assigned_from`` / ``assigned_to``
  (a row the migration made from an older link, ``backfilled``, adds
  ``person_link_backfilled``: its start is only the device's first sighting);
* the device has assignment rows but none covers ``at`` → **no person**
  (``person_unassigned_at_time``) — never the owner of another time;
* the device has no assignment rows at all, only ``devices.person_id`` (data written before the
  history existed) → that current owner, ``link="current"``; the engine then adds
  ``person_link_at_lookup_time`` to the outcome.

The department of a person is always the person's **current** department (no department history
is kept); the engine says so for history links (``person_department_current``).

**Lease facts at the moment** (ADR-0016, D6, D29, D33, D24; Wave 3 contract 2.7). A chain's
``LeaseInfo`` carries the lease time and ``last_dhcp_evidence``, the lease's own newest DHCP
record at or before ``at`` (never the line that ended a ``superseded`` lease, never a renewal
logged after ``at``). For a lookup (``lookup=True``) an ``fg_dhcp`` lease reads that record from
the rows, one ``LIMIT 1`` query per chain on ``ix_fg_dhcp_ip_time`` over the rows
:func:`.trust.kept` accepts; the stored refs (the opening ACK and the last continuations only)
are its fallback and the only source for other tables and for the usage rollups, which ignore
confidence. A lease whose newest such row is a mapped RELEASE does not cover ``at`` (a logid
mapped after the build). An ``fg_dhcp`` chain carries the format note (D29), is ``low`` (lease
and chain) when ``at`` lies more than half the lease after that record
(``lease_renewal_not_seen``) or when its IP lies in no configured network
(``fortigate_dhcp_no_network``), and names cited rows of an ``unknown-<ip>`` sender that is
trusted now (``source_configured_later``).

**Checks across the chains of a lookup** (:meth:`Attributor.checked`, after the lease steps;
contract 2.7), one query each for every chain of the lookup: a chain whose configured network
shows usable DHCP rows of two origins within W of the moment is ``low`` with
``dhcp_sources_overlap`` (D27); a DHCP chain still ``high`` or ``medium`` is ``low`` (lease and
chain, its alternatives too) with ``fortigate_dhcp_not_converted_since_lease`` when, between its
lease's last DHCP record and the moment, a mapped FortiGate DHCP line in ``fg_event`` names its
IP or its MAC (D8), or a converted ACK moves its MAC to another address in a way D33 would have
closed the lease on with today's networks (round 7). A MAC on another address counts unless
both addresses lie in configured networks and those differ (D33's key). Nothing of those rows
is cited or copied into the outcome: they may be another client's (KVKK).
"""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Final

from sqlalchemy import ColumnElement, case, func, or_, select
from sqlalchemy.orm import Session

from arcilogger_db.assignments import Slot, covering, load_history
from arcilogger_db.models import (
    LEASE_SECONDS_RANGE,
    Department,
    Device,
    FgDhcp,
    FgEvent,
    Lease,
    Person,
    Vlan,
)
from arcilogger_db.network import VlanDepartment, find_vlan, vlan_department
from arcilogger_worker.lookup.refs import (
    ARP_TABLE,
    DHCP_TABLES,
    FG_DHCP_TABLE,
    dhcp_action,
    last_dhcp_evidence,
)
from arcilogger_worker.lookup.texts import explain
from arcilogger_worker.lookup.trust import UNKNOWN_PREFIX, kept, usable_dhcp_origins
from arcilogger_worker.lookup.types import (
    CONFIDENCE_ORDER,
    AttributionChain,
    Confidence,
    DeviceInfo,
    EvidenceRef,
    Explanation,
    LeaseInfo,
    LookupSettings,
    PersonInfo,
    weakest,
)
from arcilogger_worker.macaddr import is_randomized

#: The logged lease of an ``fg_dhcp`` ref summary (``…, lease 86400)``; ``lease -)`` without).
_REF_LEASE: Final = re.compile(r", lease (\d+)\)$")
#: The D8 probe's window (2.8.3, FortiGate lease times): the post-lease check of a MikroTik
#: lease, which traffic keeps open, reads no further back than this (2.7, round 7).
UNCONVERTED_WINDOW: Final = timedelta(seconds=8_640_000)
#: The chains the post-lease check looks at (a ``low`` chain is warned already).
_CHECKED: Final = ("high", "medium")
#: How 2.6.3 renders a log-supplied VDOM name: MAC- and IPv4-looking parts masked, cut.
_MAC_LIKE: Final = re.compile(
    r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{4}(?:\.[0-9A-Fa-f]{4}){2}"
)
_IPV4_LIKE: Final = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_VD_CUT: Final = 32


@dataclass(frozen=True)
class StoredLease:
    """A ``leases`` row as the engine uses it."""

    id: int
    ip: str
    mac: str
    vlan: int | None
    server: str | None
    hostname: str | None
    start: datetime
    end: datetime | None
    end_reason: str
    confidence: Confidence
    source_table: str
    last_evidence: datetime | None
    evidence: tuple[EvidenceRef, ...]
    #: The usable lease time the FortiGate logged (``fg_dhcp`` only; D6).
    lease_seconds: int | None = None

    @classmethod
    def from_row(cls, row: Lease) -> StoredLease:
        return cls(
            id=row.id,
            ip=row.ip,
            mac=row.mac,
            vlan=row.vlan,
            server=row.server,
            hostname=row.hostname,
            start=row.start,
            end=row.end,
            end_reason=row.end_reason,
            confidence=row.confidence,  # type: ignore[arg-type]
            source_table=row.source_table,
            last_evidence=row.last_evidence,
            evidence=tuple(EvidenceRef.from_dict(item) for item in row.evidence or ()),
            lease_seconds=row.lease_seconds,
        )

    def info(
        self,
        *,
        confidence: Confidence | None = None,
        lease_seconds: int | None = None,
        last_dhcp_evidence: datetime | None = None,
    ) -> LeaseInfo:
        return LeaseInfo(
            lease_id=self.id,
            ip=self.ip,
            mac=self.mac,
            vlan=self.vlan,
            hostname=self.hostname,
            start=self.start,
            end=self.end,
            end_reason=self.end_reason,
            confidence=confidence or self.confidence,
            evidence=self.evidence,
            source_table=self.source_table,
            lease_seconds=lease_seconds if lease_seconds is not None else self.lease_seconds,
            last_dhcp_evidence=last_dhcp_evidence,
        )


@dataclass(frozen=True)
class _Record:
    """A lease's own newest DHCP record at or before a moment (2.7): its time, its meaning
    (``assigned``/``deassigned``) and the usable lease it logged (``None``: none, or out of
    range); ``row``: read from the ``fg_dhcp`` row itself, which also gives its VDOM ``vd``
    (not from the stored refs)."""

    time: datetime
    action: str | None
    lease: int | None
    row: bool = False
    vd: str | None = None


@dataclass(frozen=True)
class _PersonRow:
    id: int
    name: str
    title: str | None
    department: str | None


class Attributor:
    """Chains of one lookup or one usage run.

    ``untrusted``: :func:`.trust.untrusted_sources` (loaded once by the caller). ``lookup``: read
    each ``fg_dhcp`` chain's record at the moment from the rows (one query per chain); the usage
    rollups leave it off and use the stored refs. ``format_verified``: the time and author of
    the ``dhcp.fortigate_format_verified`` settings row, which :func:`.engine.run_lookup` reads.
    """

    def __init__(
        self,
        session: Session,
        settings: LookupSettings,
        untrusted: Collection[str] = frozenset(),
        *,
        lookup: bool = False,
        format_verified: tuple[datetime | None, str | None] = (None, None),
    ) -> None:
        self.session = session
        self.settings = settings
        self.untrusted = frozenset(untrusted)
        self.lookup = lookup
        self.format_verified = format_verified
        self._kept: ColumnElement[bool] | None = None
        #: Per ``(lease id, moment)`` the lease's own newest DHCP record (``None``: none).
        self._records: dict[tuple[int, datetime], _Record | None] = {}
        #: The stored lease of every chain built, by lease id (the lookup's checks read them).
        self._stored: dict[int, StoredLease] = {}
        self._leases: dict[str, list[StoredLease]] = {}
        #: Per IP the time range already loaded into ``_leases``.
        self._loaded: dict[str, tuple[datetime, datetime]] = {}
        self._devices: dict[str, Device | None] = {}
        #: Assignment history per MAC (loaded with the device; MACs without rows are missing).
        self._history: dict[str, list[Slot]] = {}
        self._persons: dict[int, _PersonRow | None] = {}
        self._vlans: dict[str, tuple[Vlan | None, VlanDepartment | None]] = {}

    # -- loading ----------------------------------------------------------------------------------

    def prefetch(self, items: Iterable[tuple[str, datetime]]) -> None:
        """Load the leases of all ``(ip, at)`` pairs, then their devices and persons."""
        wanted: dict[str, list[datetime]] = defaultdict(list)
        for ip, at in items:
            wanted[ip].append(at)
        if not wanted:
            return
        low = min(min(times) for times in wanted.values())
        high = max(max(times) for times in wanted.values())
        ips = sorted(wanted)
        for start in range(0, len(ips), 500):
            self._load(ips[start : start + 500], low, high)
        self._load_devices({lease.mac for ip in ips for lease in self._leases.get(ip, ())})

    def _load(self, ips: list[str], low: datetime, high: datetime) -> None:
        query = select(Lease).where(
            Lease.ip.in_(ips),
            Lease.start <= high,
            or_(Lease.end.is_(None), Lease.end > low),
        )
        found: dict[str, list[StoredLease]] = defaultdict(list)
        for row in self.session.scalars(query.order_by(Lease.start, Lease.id)):
            found[row.ip].append(StoredLease.from_row(row))
        for ip in ips:
            self._leases[ip] = found.get(ip, [])
            self._loaded[ip] = (low, high)

    def _load_devices(self, macs: set[str]) -> None:
        missing = sorted(mac for mac in macs if mac not in self._devices)
        if not missing:
            return
        rows = {
            d.mac: d for d in self.session.scalars(select(Device).where(Device.mac.in_(missing)))
        }
        for mac in missing:
            self._devices[mac] = rows.get(mac)
        history = load_history(self.session, rows)
        self._history.update(history)
        person_ids = {d.person_id for d in rows.values() if d.person_id is not None}
        person_ids |= {slot.person_id for slots in history.values() for slot in slots}
        self._load_persons(person_ids)

    def _load_persons(self, ids: set[int]) -> None:
        missing = sorted(i for i in ids if i not in self._persons)
        if not missing:
            return
        query = (
            select(Person.id, Person.name, Person.title, Department.name)
            .outerjoin(Department, Person.department_id == Department.id)
            .where(Person.id.in_(missing))
        )
        found = {
            row[0]: _PersonRow(row[0], row[1], row[2], row[3])
            for row in self.session.execute(query)
        }
        for person_id in missing:
            self._persons[person_id] = found.get(person_id)

    def _device(self, mac: str) -> Device | None:
        if mac not in self._devices:
            self._load_devices({mac})
        return self._devices.get(mac)

    def _vlan(self, ip: str) -> tuple[Vlan | None, VlanDepartment | None]:
        if ip not in self._vlans:
            vlan = find_vlan(self.session, ip)
            self._vlans[ip] = (vlan, None if vlan is None else vlan_department(self.session, vlan))
        return self._vlans[ip]

    # -- leases at a moment -----------------------------------------------------------------------

    def _open_limit(self, lease: StoredLease) -> datetime:
        last = lease.last_evidence or lease.start
        if lease.source_table == ARP_TABLE:
            return last + timedelta(seconds=self.settings.arp_interval_seconds)
        timeout = self.settings.lease_timeout_seconds(lease.server, lease.lease_seconds)
        return last + timedelta(seconds=timeout)

    def covered_until(self, lease: StoredLease) -> datetime:
        """End of the time ``lease`` covers: its end, or for an open lease the moment it times
        out without further evidence (the usage rollups split a day at these moments)."""
        return lease.end if lease.end is not None else self._open_limit(lease)

    def leases_overlapping(self, ip: str, low: datetime, high: datetime) -> list[StoredLease]:
        """Every lease of ``ip`` that covers some moment of ``[low, high]`` (loaded once)."""
        loaded = self._loaded.get(ip)
        if loaded is None or not (loaded[0] <= low and high <= loaded[1]):
            self._load([ip], low, high)
        return [
            lease
            for lease in self._leases.get(ip, ())
            if lease.start <= high and self.covered_until(lease) >= low
        ]

    def assignments(self, mac: str) -> list[Slot]:
        """The assignment history of ``mac``, oldest first (empty without one)."""
        self._device(mac)
        return list(self._history.get(mac, ()))

    def covers(self, lease: StoredLease, at: datetime) -> bool:
        if lease.start > at:
            return False
        if lease.end is not None:
            inside = at < lease.end
        elif lease.source_table == ARP_TABLE:
            return at < self._open_limit(lease)
        else:
            inside = at <= self._open_limit(lease)
        # A lease built without its client's RELEASE (a logid mapped after the build) ended at
        # that RELEASE under today's settings (2.7, round 6): it no longer covers ``at``.
        return inside and not self._released(lease, at)

    # -- the lease's own record at the moment (2.7) -----------------------------------------------

    def record(self, lease: StoredLease, at: datetime) -> _Record | None:
        """The lease's own newest DHCP record at or before ``at``: for a lookup's ``fg_dhcp``
        lease the newest kept row of its pair in ``[start, at]``, else (and when no row is
        found) the newest of its stored refs (:func:`.refs.last_dhcp_evidence`)."""
        key = (lease.id, at)
        if key not in self._records:
            found = None
            if self.lookup and lease.source_table == FG_DHCP_TABLE:
                found = self._row_record(lease, at)
            self._records[key] = found or self._ref_record(lease, at)
        return self._records[key]

    def _row_record(self, lease: StoredLease, at: datetime) -> _Record | None:
        if self._kept is None:
            self._kept = kept(self.settings, self.untrusted)
        row = self.session.execute(
            select(FgDhcp.time, FgDhcp.logid, FgDhcp.lease, FgDhcp.extra["vd"].astext)
            .where(
                FgDhcp.ip == lease.ip,
                FgDhcp.mac == lease.mac,
                FgDhcp.time >= lease.start,
                FgDhcp.time <= at,
                self._kept,
            )
            .order_by(FgDhcp.time.desc(), self._assigned_first, FgDhcp.id.desc())
            .limit(1)
        ).first()
        if row is None:
            return None
        action = self.settings.fortigate_logids.get(str(row.logid))
        return _Record(row.time, action, self._usable_lease(row.lease), row=True, vd=row[3])

    @property
    def _assigned_first(self) -> ColumnElement[int]:
        """At equal times an assignment is newer than a release, as in the timeline's feed
        order (``deassigned`` before ``assigned``): a RELEASE that closed the previous interval
        in the second this lease began is not this lease's record."""
        released = sorted(
            logid
            for logid, meaning in self.settings.fortigate_logids.items()
            if meaning == "deassigned"
        )
        return case((FgDhcp.logid.in_(released), 1), else_=0)

    def _ref_record(self, lease: StoredLease, at: datetime) -> _Record | None:
        last = last_dhcp_evidence(lease.evidence, lease.end, lease.end_reason, at)
        if last is None:
            return None
        ref = next(
            ref for ref in reversed(lease.evidence) if ref.table in DHCP_TABLES and ref.time == last
        )
        logged = _REF_LEASE.search(ref.summary) if ref.table == FG_DHCP_TABLE else None
        return _Record(
            last, dhcp_action(ref), self._usable_lease(int(logged.group(1)) if logged else None)
        )

    def _usable_lease(self, lease: int | None) -> int | None:
        low, high = LEASE_SECONDS_RANGE
        if not self.settings.use_logged_lease or lease is None or not low <= lease <= high:
            return None
        return lease

    def _released(self, lease: StoredLease, at: datetime) -> bool:
        if not (self.lookup and lease.source_table == FG_DHCP_TABLE):
            return False
        record = self.record(lease, at)
        return record is not None and record.action == "deassigned"

    def leases_at(self, ip: str, at: datetime) -> list[StoredLease]:
        """Leases of ``ip`` covering ``at``, one per MAC, most likely first."""
        loaded = self._loaded.get(ip)
        if loaded is None or not loaded[0] <= at <= loaded[1]:
            self._load([ip], at, at)
        covering = [lease for lease in self._leases.get(ip, ()) if self.covers(lease, at)]
        best: dict[str, StoredLease] = {}
        for lease in sorted(covering, key=self._rank):
            best.setdefault(lease.mac, lease)
        return sorted(best.values(), key=self._rank)

    def _rank(self, lease: StoredLease) -> tuple[bool, int, float, str, int]:
        return (
            self.is_infrastructure(lease.mac),
            -CONFIDENCE_ORDER[lease.confidence],
            -lease.start.timestamp(),
            lease.mac,
            lease.id,
        )

    def is_infrastructure(self, mac: str) -> bool:
        if mac in self.settings.infrastructure_macs:
            return True
        device = self._device(mac)
        return bool(device is not None and device.infrastructure)

    def nearest(self, ip: str, at: datetime) -> tuple[datetime | None, datetime | None]:
        """End of the latest lease before ``at`` and start of the first one after it."""
        before = self.session.scalar(
            select(Lease.end)
            .where(Lease.ip == ip, Lease.end <= at)
            .order_by(Lease.end.desc())
            .limit(1)
        )
        after = self.session.scalar(
            select(Lease.start)
            .where(Lease.ip == ip, Lease.start > at)
            .order_by(Lease.start)
            .limit(1)
        )
        return before, after

    def leases_of_mac(self, mac: str, low: datetime, high: datetime) -> list[StoredLease]:
        """Leases of ``mac`` that overlap ``[low, high]`` (MAC lookups)."""
        query = select(Lease).where(
            Lease.mac == mac, Lease.start <= high, or_(Lease.end.is_(None), Lease.end > low)
        )
        return [
            StoredLease.from_row(row) for row in self.session.scalars(query.order_by(Lease.start))
        ]

    # -- chains -----------------------------------------------------------------------------------

    def chain(
        self, ip: str, at: datetime, *, prefer_mac: str | None = None
    ) -> AttributionChain | None:
        """The chain of ``ip`` at ``at`` (``None`` when no lease covers it)."""
        leases = self.leases_at(ip, at)
        if not leases:
            return None
        regular = [lease for lease in leases if not self.is_infrastructure(lease.mac)]
        considered = regular or leases[:1]
        if prefer_mac is not None:
            considered.sort(key=lambda lease: lease.mac != prefer_mac)
        primary = self._single(ip, at, considered[0])
        if len(considered) == 1:
            return primary

        def conflicted(single: AttributionChain) -> AttributionChain:
            # Every chain of a conflict is equally uncertain: none may look more reliable than
            # another (the step confidences — lease, device, person — stay as they are).
            return replace(
                single,
                confidence="low",
                explanations=(explain("ip_conflict"), *single.explanations),
            )

        others = tuple(conflicted(self._single(ip, at, lease)) for lease in considered[1:])
        return replace(conflicted(primary), alternatives=others)

    def _person(
        self, device: Device | None, at: datetime
    ) -> tuple[PersonInfo | None, list[Explanation]]:
        """The person of ``device`` at ``at`` and the notes that qualify it (module text)."""
        if device is None:
            return None, []
        slots = self._history.get(device.mac)
        if slots:
            slot = covering(slots, at)
            if slot is None:
                return None, [explain("person_unassigned_at_time", at=at.isoformat())]
            row = self._person_row(slot.person_id)
            if row is None:  # pragma: no cover - the foreign key keeps the person
                return None, []
            notes = []
            if slot.backfilled:
                notes.append(
                    explain("person_link_backfilled", assigned_from=slot.valid_from.isoformat())
                )
            return (
                PersonInfo(
                    person_id=row.id,
                    name=row.name,
                    department=row.department,
                    title=row.title,
                    confidence="medium",
                    assigned_from=slot.valid_from,
                    assigned_to=slot.valid_to,
                    link="history",
                ),
                notes,
            )
        if device.person_id is None:
            return None, []
        row = self._person_row(device.person_id)
        if row is None:
            return None, []
        return (
            PersonInfo(
                person_id=row.id,
                name=row.name,
                department=row.department,
                title=row.title,
                confidence="medium",
                link="current",
            ),
            [],
        )

    def _person_row(self, person_id: int) -> _PersonRow | None:
        self._load_persons({person_id})
        return self._persons.get(person_id)

    def _single(self, ip: str, at: datetime, lease: StoredLease) -> AttributionChain:
        self._stored[lease.id] = lease
        vlan, department = self._vlan(ip)
        device = self._device(lease.mac)
        randomized = device.randomized if device is not None else is_randomized(lease.mac)
        infrastructure = self.is_infrastructure(lease.mac)
        hostnames = tuple(device.hostnames) if device is not None and device.hostnames else ()
        if not hostnames and lease.hostname:
            hostnames = (lease.hostname,)
        device_info = DeviceInfo(
            mac=lease.mac,
            name=None if device is None else device.name,
            hostnames=hostnames,
            vendor=None if device is None else device.vendor,
            randomized=randomized,
            type=None if device is None else device.type,
            infrastructure=infrastructure,
            confidence="medium" if randomized else "high",
        )
        person, person_notes = self._person(device, at)
        explanations: list[Explanation] = []
        if vlan is not None and vlan.guest:
            explanations.append(explain("guest_vlan"))
        if lease.source_table == ARP_TABLE:
            explanations.append(explain("arp_only"))
        if randomized:
            explanations.append(explain("randomized_mac"))
        if infrastructure:
            explanations.append(explain("infrastructure_device"))
        explanations += person_notes
        lease_confidence, lease_notes = self._lease_notes(ip, at, lease, vlan)
        explanations += lease_notes
        if person is not None and person.department:
            dept, source = person.department, "person"
        elif department is not None:
            dept, source = department.department, "vlan"
            if person is None and not person_notes:
                explanations.append(explain("no_person"))
        else:
            dept, source = None, "none"
        levels: list[Confidence] = [lease_confidence, device_info.confidence]
        if person is not None:
            levels.append(person.confidence)
        record = self.record(lease, at)
        return AttributionChain(
            internal_ip=ip,
            at=at,
            vlan=vlan.vlan_id if vlan is not None else lease.vlan,
            vlan_name=None if vlan is None else vlan.name_tr,
            lease=lease.info(
                confidence=lease_confidence,
                lease_seconds=None if record is None else record.lease,
                last_dhcp_evidence=None if record is None else record.time,
            ),
            device=device_info,
            person=person,
            department=dept,
            department_source=source,  # type: ignore[arg-type]
            confidence=weakest(levels),
            explanations=tuple(explanations),
        )

    def _lease_notes(
        self, ip: str, at: datetime, lease: StoredLease, vlan: Vlan | None
    ) -> tuple[Confidence, list[Explanation]]:
        """The lease step's confidence and the notes of 2.7 on it: the FortiGate notes of an
        ``fg_dhcp`` lease (D29, D6, D33) and a sender configured later (D24)."""
        confidence = lease.confidence
        notes: list[Explanation] = []
        if lease.source_table == FG_DHCP_TABLE:
            if self.settings.fortigate_format_verified:
                verified_at, verified_by = self.format_verified
                notes.append(
                    explain(
                        "fortigate_format_verified",
                        at=None if verified_at is None else verified_at.isoformat(),
                        by=verified_by,
                    )
                )
            else:
                notes.append(explain("fortigate_format_unverified"))
            record = self.record(lease, at)
            if record is not None:
                length = (
                    record.lease
                    or lease.lease_seconds
                    or self.settings.server_lease_seconds(lease.server)
                )
                if at > record.time + timedelta(seconds=length / 2):
                    confidence = "low"
                    notes.append(
                        explain(
                            "lease_renewal_not_seen", last=record.time.isoformat(), lease=length
                        )
                    )
            if vlan is None:
                confidence = "low"
                notes.append(explain("fortigate_dhcp_no_network", ip=ip))
        later = sorted(
            {
                ref.raw.source
                for ref in lease.evidence
                if ref.table in DHCP_TABLES
                and ref.raw is not None
                and ref.raw.source.startswith(UNKNOWN_PREFIX)
                and ref.raw.source not in self.untrusted
            }
        )
        if later:
            notes.append(explain("source_configured_later", sources=", ".join(later)))
        return confidence, notes

    # -- checks across the chains of a lookup (2.7) -----------------------------------------------

    def checked(self, chains: Sequence[AttributionChain]) -> list[AttributionChain]:
        """``chains`` (every chain of one lookup, in order) after the checks of 2.7 that read
        across them, in the contract's order: two DHCP origins (D27), then FortiGate DHCP lines
        in ``fg_event`` after the lease (D8), then converted ACKs of a MAC move the lease build
        missed (D33, round 7). Lookups only: the usage rollups ignore confidence."""
        if not self.lookup or not chains:
            return list(chains)
        groups = [[chain, *chain.alternatives] for chain in chains]
        self._two_origins(groups)
        self._unconverted_lines(groups)
        self._missed_moves(groups)
        return [replace(group[0], alternatives=tuple(group[1:])) for group in groups]

    def _stored_lease(self, chain: AttributionChain) -> StoredLease | None:
        lease = chain.lease
        return None if lease is None or lease.lease_id is None else self._stored.get(lease.lease_id)

    def _alive(self, lease: StoredLease) -> int:
        """How long after its last evidence ``lease`` stays alive (``_open_limit``'s window)."""
        if lease.source_table == ARP_TABLE:
            return self.settings.arp_interval_seconds
        return self.settings.lease_timeout_seconds(lease.server, lease.lease_seconds)

    def _two_origins(self, groups: list[list[AttributionChain]]) -> None:
        """D27: one :func:`.trust.usable_dhcp_origins` query for the configured networks of all
        chains over ``[T - W, T + W]``, W the largest alive window of their leases and at least
        the longest configured DHCP window; a chain whose network shows two origins is ``low``
        with ``dhcp_sources_overlap`` (the network and the origins, never a client's value)."""
        every = [chain for group in groups for chain in group]
        networks = {
            chain.internal_ip: vlan
            for chain in every
            if (vlan := self._vlan(chain.internal_ip)[0]) is not None
        }
        if not networks:
            return
        servers = [None, *self.settings.lease_server_seconds]
        windows = [self.settings.lease_timeout_seconds(server) for server in servers]
        windows += [self._alive(lease) for c in every if (lease := self._stored_lease(c))]
        width = timedelta(seconds=max(windows))
        moments = [chain.at for chain in every]
        origins = usable_dhcp_origins(
            self.session,
            self.settings,
            self.untrusted,
            sorted({str(vlan.subnet) for vlan in networks.values()}),
            min(moments) - width,
            max(moments) + width,
        )
        for group in groups:
            for index, chain in enumerate(group):
                vlan = networks.get(chain.internal_ip)
                found = set() if vlan is None else origins[str(vlan.subnet)]
                if vlan is None or len(found) < 2:
                    continue
                overlap = explain(
                    "dhcp_sources_overlap",
                    network=_network_name(vlan),
                    at=chain.at.isoformat(),
                    sources=", ".join(sorted(_origin_name(origin) for origin in found)),
                )
                group[index] = replace(
                    chain, confidence="low", explanations=(*chain.explanations, overlap)
                )

    def _targets(
        self, groups: list[list[AttributionChain]]
    ) -> list[tuple[list[AttributionChain], AttributionChain, StoredLease, datetime]]:
        """The chains the post-lease check looks at: a DHCP lease, still ``high`` or ``medium``;
        each with its group and the lower end of its window: the lease's last DHCP record at
        or before the moment (its start without one), for a MikroTik lease no earlier than the
        D8 probe window before the moment."""
        targets = []
        for group in groups:
            for chain in group:
                lease = self._stored_lease(chain)
                if lease is None or lease.source_table not in DHCP_TABLES:
                    continue
                if chain.confidence not in _CHECKED:
                    continue
                since = self._last_record(lease, chain.at)
                if lease.source_table != FG_DHCP_TABLE:
                    since = max(since, chain.at - UNCONVERTED_WINDOW)
                targets.append((group, chain, lease, since))
        return targets

    def _last_record(self, lease: StoredLease, at: datetime) -> datetime:
        record = self.record(lease, at)
        return lease.start if record is None else record.time

    def _unconverted_lines(self, groups: list[list[AttributionChain]]) -> None:
        """D8 after the lease: one query on ``fg_event`` (``ix_fg_event_subtype_time``) for the
        mapped FortiGate DHCP lines of trusted senders that name a target's IP or MAC, matched to
        each target's own window ``(since, at]`` in memory."""
        targets = self._targets(groups)
        logids = sorted(self.settings.fortigate_logids)
        if not targets or not logids:
            return
        ip = FgEvent.extra["ip"].astext
        mac = func.lower(func.replace(FgEvent.extra["mac"].astext, "-", ":"))
        query = select(ip, mac, FgEvent.time).where(
            FgEvent.subtype == "system",
            FgEvent.logid.in_(logids),
            FgEvent.time > min(since for _, _, _, since in targets),
            FgEvent.time <= max(chain.at for _, chain, _, _ in targets),
            or_(
                ip.in_(sorted({chain.internal_ip for _, chain, _, _ in targets})),
                mac.in_(sorted({lease.mac for _, _, lease, _ in targets})),
            ),
        )
        if self.untrusted:
            query = query.where(FgEvent.source.not_in(sorted(self.untrusted)))
        rows = self.session.execute(query).all()
        if not rows:
            return
        for group, chain, lease, since in targets:
            if any(
                since < time <= chain.at
                and (
                    address == chain.internal_ip
                    or (moved == lease.mac and self._move_counts(chain.internal_ip, address))
                )
                for address, moved, time in rows
            ):
                self._lower(group)

    def _missed_moves(self, groups: list[list[AttributionChain]]) -> None:
        """Round 7: one query on ``fg_dhcp`` (``ix_fg_dhcp_mac_time``) for kept ACKs of a
        target's MAC, matched in memory: another IP inside ``(since, at)``, for an ``fg_dhcp``
        lease on its interface and (when its record is a row) its VDOM, under D33's key."""
        targets = self._targets(groups)
        assigned = sorted(
            logid
            for logid, meaning in self.settings.fortigate_logids.items()
            if meaning == "assigned"
        )
        if not targets or not assigned:
            return
        if self._kept is None:
            self._kept = kept(self.settings, self.untrusted)
        query = select(
            FgDhcp.ip, FgDhcp.mac, FgDhcp.interface, FgDhcp.extra["vd"].astext, FgDhcp.time
        ).where(
            FgDhcp.mac.in_(sorted({lease.mac for _, _, lease, _ in targets})),
            FgDhcp.time > min(since for _, _, _, since in targets),
            FgDhcp.time < max(chain.at for _, chain, _, _ in targets),
            FgDhcp.logid.in_(assigned),
            self._kept,
        )
        rows = self.session.execute(query).all()
        if not rows:
            return
        for group, chain, lease, since in targets:
            record = self.record(lease, chain.at)
            for address, mac, interface, vd, time in rows:
                if mac != lease.mac or address == chain.internal_ip:
                    continue
                if not since < time < chain.at:
                    continue
                if lease.source_table == FG_DHCP_TABLE:
                    if interface != lease.server:
                        continue
                    if record is not None and record.row and vd != record.vd:
                        continue
                if self._move_counts(chain.internal_ip, address):
                    self._lower(group)
                    break

    def _move_counts(self, ip: str, other: str | None) -> bool:
        """Whether a row of the chain's MAC for address ``other`` bears on the chain of ``ip``:
        not when both lie in configured networks and those differ (D33's key: a laptop or a NAS
        on two configured networks); an unreadable address, or one outside every configured
        network on either side, counts."""
        try:
            ipaddress.ip_address(other or "")
        except ValueError:
            return True
        own, theirs = self._vlan(ip)[0], self._vlan(str(other))[0]
        return own is None or theirs is None or str(own.subnet) == str(theirs.subnet)

    def _lower(self, group: list[AttributionChain]) -> None:
        """The chain and its alternatives ``low`` (lease and chain) with
        ``fortigate_dhcp_not_converted_since_lease``, citing nothing (round 6: the lease row
        must not show ``high`` beside a ``low`` chain)."""
        for index, chain in enumerate(group):
            if any(e.key == "fortigate_dhcp_not_converted_since_lease" for e in chain.explanations):
                continue
            lease = self._stored_lease(chain)
            notes = chain.explanations
            if lease is not None:
                last = self._last_record(lease, chain.at)
                notes = (
                    *notes,
                    explain(
                        "fortigate_dhcp_not_converted_since_lease",
                        last=last.isoformat(),
                        ip=chain.internal_ip,
                    ),
                )
            group[index] = replace(
                chain,
                confidence="low",
                lease=None if chain.lease is None else replace(chain.lease, confidence="low"),
                explanations=notes,
            )


def _network_name(vlan: Vlan) -> str:
    """A configured network as 2.6.3 names it: ``VLAN 42``, else its name and subnet (site
    configuration, never client data)."""
    if vlan.vlan_id is not None:
        return f"VLAN {vlan.vlan_id}"
    return f"{vlan.name_tr} ({vlan.subnet})"


def _origin_name(origin: tuple[str, str | None]) -> str:
    """A DHCP origin ``(table, vd)`` as 2.6.3 renders it; the log-supplied VDOM name masked and
    cut like every log value there."""
    table, vd = origin
    if table != FG_DHCP_TABLE:
        return "MikroTik DHCP"
    if vd is None:
        return "FortiGate DHCP"
    shown = _IPV4_LIKE.sub("…", _MAC_LIKE.sub("…", vd))[:_VD_CUT]
    return f"FortiGate DHCP (VDOM {shown})"
