"""Devices, persons, departments and VLANs as the API shows them (spec 9.2, ADR-0008).

Shared by the routers ``devices``, ``persons``, ``departments``, ``vlans`` and ``imports``
(contract: docs/MVP-CONTRACTS.md section 11). The response models carry the names of the group
contract (``DeviceOut``, ``PersonOut`` …), which the web app's types follow.

* **Current owner** of a device: ``devices.person_id`` (the open ``device_assignments`` row,
  :mod:`arcilogger_db.assignments`); ``assigned_since`` is that row's ``valid_from``.
* **Effective department** (spec 9.2 "person's department if linked, else the VLAN's"): the
  current owner's department (``department_source = "person"``), else the department linked to
  the device's VLAN — the VLAN of ``last_vlan``, else the one whose subnet holds ``last_ip``
  (``"vlan"``); ``null`` / ``null`` when neither is known. A VLAN without a linked department
  (e.g. a staff Wi-Fi) has none here: the 5651 chain then shows the VLAN's name
  (:mod:`arcilogger_db.network`), the page shows ``vlan_name``.

Input types: free text is trimmed, bounded and free of control and format characters (line breaks
only in notes); an empty string means "no value" (``null``).
"""

from __future__ import annotations

import ipaddress
import unicodedata
from collections.abc import Iterable, Sequence
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, BeforeValidator, StringConstraints
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_api.auth.schemas import Email
from arcilogger_db.models import Department, Device, DeviceAssignment, Lease, Person, Vlan

DeviceType = Literal["pc", "phone", "printer", "ip_phone", "ap", "server", "camera", "other"]
DepartmentSource = Literal["person", "vlan"]
#: Newest leases shown on a device's detail.
RECENT_LEASES: Final = 20
#: Most assignments shown on a device's or person's detail (newest first).
ASSIGNMENTS_SHOWN: Final = 200
MAX_TAGS: Final = 20

_FORBIDDEN: Final = frozenset({"Cc", "Cf", "Cs"})


# -- input types ----------------------------------------------------------------------------------


def blank_to_none(value: object) -> object:
    """``""`` / blank → ``None`` (an empty form field means "no value")."""
    if isinstance(value, str) and not value.strip():
        return None
    return value


def _single_line(value: str) -> str:
    if any(unicodedata.category(char) in _FORBIDDEN for char in value):
        raise ValueError("control characters are not allowed")
    return value


def _multi_line(value: str) -> str:
    value = value.replace("\r\n", "\n")
    if any(unicodedata.category(char) in _FORBIDDEN and char not in "\n\t" for char in value):
        raise ValueError("control characters are not allowed")
    return value


#: Name of a person, a device or a department (also a title).
Name = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_single_line),
]
EmployeeNo = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
    AfterValidator(_single_line),
]
Tag = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=50),
    AfterValidator(_single_line),
]
#: An assignment's note (line breaks allowed).
Note = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(_multi_line),
]
#: Notes of a device, a person or a department (line breaks allowed).
Notes = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=2000),
    AfterValidator(_multi_line),
]
#: Purpose of a VLAN (one line).
Purpose = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=500),
    AfterValidator(_single_line),
]
#: Name of a DHCP server on the router as its log lines carry it (``dhcp20``): no spaces or
#: colons (the MikroTik parser reads ``<server> assigned …``).
DhcpServer = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64, pattern=r"^[^\s:]+$"),
    AfterValidator(_single_line),
]
#: Optional variants: an empty or blank string is ``null``.
OptionalName = Annotated[Name | None, BeforeValidator(blank_to_none)]
OptionalEmployeeNo = Annotated[EmployeeNo | None, BeforeValidator(blank_to_none)]
OptionalNote = Annotated[Note | None, BeforeValidator(blank_to_none)]
OptionalNotes = Annotated[Notes | None, BeforeValidator(blank_to_none)]
OptionalEmail = Annotated[Email | None, BeforeValidator(blank_to_none)]
OptionalPurpose = Annotated[Purpose | None, BeforeValidator(blank_to_none)]
OptionalDhcpServer = Annotated[DhcpServer | None, BeforeValidator(blank_to_none)]


def clean_tags(tags: Iterable[str]) -> list[str]:
    """Tags in their first order, without duplicates (compared case-insensitively)."""
    seen: set[str] = set()
    result: list[str] = []
    for tag in tags:
        key = tag.casefold()
        if key not in seen:
            seen.add(key)
            result.append(tag)
    return result


# -- response models (contract names) -------------------------------------------------------------


class PersonRef(BaseModel):
    id: int
    name: str


class DepartmentRef(BaseModel):
    id: int
    name: str


class DeviceOut(BaseModel):
    mac: str
    name: str | None
    vendor: str | None
    randomized: bool
    hostnames: list[str]
    last_ip: str | None
    last_vlan: int | None
    vlan_name: str | None
    #: One of ``pc``, ``phone``, ``printer``, ``ip_phone``, ``ap``, ``server``, ``camera``,
    #: ``other``, or ``null`` (unknown).
    type: str | None
    infrastructure: bool
    notes: str | None
    tags: list[str]
    first_seen: datetime | None
    last_seen: datetime | None
    #: Current owner.
    person: PersonRef | None
    #: ``valid_from`` of the current assignment.
    assigned_since: datetime | None
    #: Effective department: the owner's, else the VLAN's.
    department: DepartmentRef | None
    department_source: DepartmentSource | None


class AssignmentOut(BaseModel):
    id: int
    mac: str
    person: PersonRef | None
    valid_from: datetime
    #: ``null`` while it is the device's current assignment.
    valid_to: datetime | None
    assigned_by: str
    note: str | None
    #: Made by the migration from a link that existed before the history was kept.
    backfilled: bool


class LeaseBrief(BaseModel):
    ip: str
    vlan: int | None
    start: datetime
    end: datetime | None
    end_reason: str | None
    confidence: str


class DeviceDetail(DeviceOut):
    #: Newest first.
    assignments: list[AssignmentOut]
    #: Newest first, at most 20.
    recent_leases: list[LeaseBrief]


class PersonOut(BaseModel):
    id: int
    name: str
    department: DepartmentRef | None
    title: str | None
    email: str | None
    employee_no: str | None
    active: bool
    notes: str | None
    #: Left out of every person-level usage view and report (the company and department totals
    #: still count the traffic); 5651 lookups never read it.
    usage_excluded: bool
    #: Devices the person holds now.
    device_count: int
    created_at: datetime
    updated_at: datetime


class PersonDetail(PersonOut):
    #: Devices the person holds now.
    devices: list[DeviceOut]
    #: Every assignment of the person, newest first.
    assignments: list[AssignmentOut]


class DepartmentOut(BaseModel):
    id: int
    name: str
    notes: str | None
    #: Persons in the department (active and inactive).
    person_count: int
    #: VLAN ids linked to the department.
    vlan_ids: list[int]


class VlanOut(BaseModel):
    subnet: str
    vlan_id: int | None
    name_tr: str
    purpose: str | None
    #: The DHCP server's name on the router (``dhcp20``), or ``null``.
    dhcp_server: str | None
    department: DepartmentRef | None
    guest: bool
    notes: str | None


class DevicePage(BaseModel):
    items: list[DeviceOut]
    total: int
    limit: int
    offset: int


class PersonPage(BaseModel):
    items: list[PersonOut]
    total: int
    limit: int
    offset: int


# -- building -------------------------------------------------------------------------------------


class Directory:
    """Turns rows into response models for one request; departments and VLANs (small tables)
    are read once, persons and open assignments per batch of rows."""

    def __init__(self, db: Session) -> None:
        self.db = db
        self.departments = {row.id: row for row in db.scalars(select(Department))}
        self.vlans = list(db.scalars(select(Vlan).order_by(Vlan.vlan_id.nulls_last(), Vlan.subnet)))
        self._by_id = {vlan.vlan_id: vlan for vlan in self.vlans if vlan.vlan_id is not None}
        networks = [(ipaddress.ip_network(vlan.subnet, strict=False), vlan) for vlan in self.vlans]
        self._networks = sorted(networks, key=lambda item: -item[0].prefixlen)
        self._persons: dict[int, Person] = {}

    # -- references -------------------------------------------------------------------------------

    def department_ref(self, department_id: int | None) -> DepartmentRef | None:
        row = self.departments.get(department_id) if department_id is not None else None
        return None if row is None else DepartmentRef(id=row.id, name=row.name)

    def person_ref(self, person_id: int | None) -> PersonRef | None:
        row = self.persons([person_id]).get(person_id) if person_id is not None else None
        return None if row is None else PersonRef(id=row.id, name=row.name)

    def persons(self, ids: Iterable[int | None]) -> dict[int, Person]:
        missing = sorted({i for i in ids if i is not None and i not in self._persons})
        if missing:
            for row in self.db.scalars(select(Person).where(Person.id.in_(missing))):
                self._persons[row.id] = row
        return self._persons

    def vlan_of(self, device: Device) -> Vlan | None:
        """The VLAN of ``last_vlan``, else the most specific subnet holding ``last_ip``."""
        if device.last_vlan is not None and device.last_vlan in self._by_id:
            return self._by_id[device.last_vlan]
        if device.last_ip is None:
            return None
        try:
            address = ipaddress.ip_address(device.last_ip)
        except ValueError:
            return None
        return next(
            (
                vlan
                for network, vlan in self._networks
                if network.version == address.version and address in network
            ),
            None,
        )

    # -- devices ----------------------------------------------------------------------------------

    def devices(self, rows: Sequence[Device]) -> list[DeviceOut]:
        macs = [row.mac for row in rows]
        since: dict[str, datetime] = {}
        if macs:
            query = select(DeviceAssignment.mac, DeviceAssignment.valid_from).where(
                DeviceAssignment.mac.in_(macs), DeviceAssignment.valid_to.is_(None)
            )
            since = dict(self.db.execute(query).all())
        persons = self.persons(row.person_id for row in rows)
        return [
            self._device(row, persons.get(row.person_id or 0), since.get(row.mac)) for row in rows
        ]

    def _device(self, row: Device, owner: Person | None, since: datetime | None) -> DeviceOut:
        vlan = self.vlan_of(row)
        department: DepartmentRef | None = None
        source: DepartmentSource | None = None
        if owner is not None and owner.department_id is not None:
            department, source = self.department_ref(owner.department_id), "person"
        if department is None and vlan is not None and vlan.department_id is not None:
            department, source = self.department_ref(vlan.department_id), "vlan"
        if department is None:
            source = None
        return DeviceOut(
            mac=row.mac,
            name=row.name,
            vendor=row.vendor,
            randomized=row.randomized,
            hostnames=list(row.hostnames or ()),
            last_ip=row.last_ip,
            last_vlan=row.last_vlan,
            vlan_name=None if vlan is None else vlan.name_tr,
            type=row.type,
            infrastructure=row.infrastructure,
            notes=row.notes,
            tags=list(row.tags or ()),
            first_seen=row.first_seen,
            last_seen=row.last_seen,
            person=None if owner is None else PersonRef(id=owner.id, name=owner.name),
            assigned_since=since if owner is not None else None,
            department=department,
            department_source=source,
        )

    def device_detail(self, row: Device) -> DeviceDetail:
        (base,) = self.devices([row])
        history = list(
            self.db.scalars(
                select(DeviceAssignment)
                .where(DeviceAssignment.mac == row.mac)
                .order_by(DeviceAssignment.valid_from.desc(), DeviceAssignment.id.desc())
                .limit(ASSIGNMENTS_SHOWN)
            )
        )
        leases = self.db.scalars(
            select(Lease)
            .where(Lease.mac == row.mac)
            .order_by(Lease.start.desc(), Lease.id.desc())
            .limit(RECENT_LEASES)
        )
        return DeviceDetail(
            **base.model_dump(),
            assignments=self.assignments(history),
            recent_leases=[
                LeaseBrief(
                    ip=lease.ip,
                    vlan=lease.vlan,
                    start=lease.start,
                    end=lease.end,
                    end_reason=lease.end_reason,
                    confidence=lease.confidence,
                )
                for lease in leases
            ],
        )

    def assignments(self, rows: Sequence[DeviceAssignment]) -> list[AssignmentOut]:
        persons = self.persons(row.person_id for row in rows)
        return [
            AssignmentOut(
                id=row.id,
                mac=row.mac,
                person=(
                    PersonRef(id=row.person_id, name=persons[row.person_id].name)
                    if row.person_id in persons
                    else None
                ),
                valid_from=row.valid_from,
                valid_to=row.valid_to,
                assigned_by=row.assigned_by,
                note=row.note,
                backfilled=row.backfilled,
            )
            for row in rows
        ]

    # -- persons ----------------------------------------------------------------------------------

    def person_outs(self, rows: Sequence[Person]) -> list[PersonOut]:
        ids = [row.id for row in rows]
        counts: dict[int, int] = {}
        if ids:
            query = (
                select(Device.person_id, func.count())
                .where(Device.person_id.in_(ids))
                .group_by(Device.person_id)
            )
            counts = {key: count for key, count in self.db.execute(query) if key is not None}
        for row in rows:
            self._persons[row.id] = row
        return [
            PersonOut(
                id=row.id,
                name=row.name,
                department=self.department_ref(row.department_id),
                title=row.title,
                email=row.email,
                employee_no=row.employee_no,
                active=row.active,
                notes=row.notes,
                usage_excluded=row.usage_excluded,
                device_count=counts.get(row.id, 0),
                created_at=row.created_at,
                updated_at=row.updated_at,
            )
            for row in rows
        ]

    def person_detail(self, row: Person) -> PersonDetail:
        (base,) = self.person_outs([row])
        devices = list(
            self.db.scalars(
                select(Device)
                .where(Device.person_id == row.id)
                .order_by(Device.last_seen.desc().nulls_last(), Device.mac)
            )
        )
        history = list(
            self.db.scalars(
                select(DeviceAssignment)
                .where(DeviceAssignment.person_id == row.id)
                .order_by(DeviceAssignment.valid_from.desc(), DeviceAssignment.id.desc())
                .limit(ASSIGNMENTS_SHOWN)
            )
        )
        return PersonDetail(
            **base.model_dump(),
            devices=self.devices(devices),
            assignments=self.assignments(history),
        )

    # -- departments and VLANs --------------------------------------------------------------------

    def department_outs(self, rows: Sequence[Department]) -> list[DepartmentOut]:
        ids = [row.id for row in rows]
        counts: dict[int, int] = {}
        if ids:
            query = (
                select(Person.department_id, func.count())
                .where(Person.department_id.in_(ids))
                .group_by(Person.department_id)
            )
            counts = {key: count for key, count in self.db.execute(query) if key is not None}
        vlan_ids: dict[int, list[int]] = {}
        for vlan in self.vlans:
            if vlan.department_id is not None and vlan.vlan_id is not None:
                vlan_ids.setdefault(vlan.department_id, []).append(vlan.vlan_id)
        return [
            DepartmentOut(
                id=row.id,
                name=row.name,
                notes=row.notes,
                person_count=counts.get(row.id, 0),
                vlan_ids=sorted(vlan_ids.get(row.id, [])),
            )
            for row in rows
        ]

    def vlan_out(self, vlan: Vlan) -> VlanOut:
        return VlanOut(
            subnet=vlan.subnet,
            vlan_id=vlan.vlan_id,
            name_tr=vlan.name_tr,
            purpose=vlan.purpose,
            dhcp_server=vlan.dhcp_server,
            department=self.department_ref(vlan.department_id),
            guest=vlan.guest,
            notes=vlan.notes,
        )
