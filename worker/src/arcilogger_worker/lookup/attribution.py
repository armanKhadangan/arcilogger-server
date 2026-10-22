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
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from arcilogger_db.assignments import Slot, covering, load_history
from arcilogger_db.models import Department, Device, Lease, Person, Vlan
from arcilogger_db.network import VlanDepartment, find_vlan, vlan_department
from arcilogger_worker.lookup.refs import ARP_TABLE
from arcilogger_worker.lookup.texts import explain
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
        )

    def info(self) -> LeaseInfo:
        return LeaseInfo(
            lease_id=self.id,
            ip=self.ip,
            mac=self.mac,
            vlan=self.vlan,
            hostname=self.hostname,
            start=self.start,
            end=self.end,
            end_reason=self.end_reason,
            confidence=self.confidence,
            evidence=self.evidence,
        )


@dataclass(frozen=True)
class _PersonRow:
    id: int
    name: str
    title: str | None
    department: str | None


class Attributor:
    def __init__(self, session: Session, settings: LookupSettings) -> None:
        self.session = session
        self.settings = settings
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
        return last + timedelta(seconds=self.settings.lease_timeout_seconds(lease.server))

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
            return at < lease.end
        if lease.source_table == ARP_TABLE:
            return at < self._open_limit(lease)
        return at <= self._open_limit(lease)

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
        if person is not None and person.department:
            dept, source = person.department, "person"
        elif department is not None:
            dept, source = department.department, "vlan"
            if person is None and not person_notes:
                explanations.append(explain("no_person"))
        else:
            dept, source = None, "none"
        levels: list[Confidence] = [lease.confidence, device_info.confidence]
        if person is not None:
            levels.append(person.confidence)
        return AttributionChain(
            internal_ip=ip,
            at=at,
            vlan=vlan.vlan_id if vlan is not None else lease.vlan,
            vlan_name=None if vlan is None else vlan.name_tr,
            lease=lease.info(),
            device=device_info,
            person=person,
            department=dept,
            department_source=source,  # type: ignore[arg-type]
            confidence=weakest(levels),
            explanations=tuple(explanations),
        )
