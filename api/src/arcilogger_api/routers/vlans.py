"""``/api/v1/vlans`` — VLAN'lar: list, add, edit, delete (spec 2.2, 9.2).

``devices.read`` / ``devices.manage`` (no person data, no KVKK notice). ARCILOGGER ships no VLAN
plan (:mod:`arcilogger_db.seed`): the administrator enters the site's VLANs and subnets here. A
VLAN is named by its **subnet** (the primary key: a transit network may have no VLAN id).

* ``POST /vlans`` — a network address with its prefix length (host bits zero, ``10.0.20.0/24``),
  ``name_tr`` and optionally ``vlan_id`` (1-4094), ``purpose``, ``dhcp_server`` (the DHCP
  server's name on the router, ``dhcp20``), ``department_id``, ``guest``, ``notes``. An IPv4
  network written as IPv6 (``::ffff:10.0.20.0/120``) is 422: no IPv4 address ever falls into
  it, the error names the IPv4 network meant. The subnet, the VLAN id and the DHCP server are
  unique (409 ``vlan_exists``, ``vlan_id_taken``, ``dhcp_server_taken``); an unknown
  ``department_id`` is 422.
* ``PATCH /vlans`` — ``subnet`` names the VLAN; only the other fields sent change, so
  ``{subnet, department_id}`` changes only the department (the default department of devices
  in that VLAN when no person is linked, spec 9.2). ``new_subnet`` moves the VLAN to another
  network: a corrected subnet keeps the name, the department and the flags.
* ``DELETE /vlans?subnet=`` — 204.

A subnet inside another one (a /25 of a /24) is allowed: an address belongs to the most specific
one (:func:`arcilogger_db.network.find_vlan`, the rule of every consumer). Nothing refers to a
``vlans`` row — leases, devices and the usage rollups keep VLAN *numbers*, raw logs and stored
5651 results are never touched —, and every consumer reads the table when it uses it, like a
department change: the device and department lists per request, a 5651 lookup when it runs,
the usage pages when they join the VLAN's department (by VLAN number, at read time). The VLAN
number of an address is the table's (the most specific subnet holding it), else, in the hours
with a lease, the lease's (the number it was built with). An address whose number changes — a
renumbered VLAN, a new one or one with a new network; for an address leaving a deleted or moved
VLAN only its hours without a lease — changes the fingerprint of its day, so the usage job
``usage.reattribute`` rebuilds the recent days by itself; older days need
``arcilogger-worker usage rebuild``.

**Audit** (subnets, VLAN ids, department ids and flags; the texts — name, purpose, DHCP server,
notes — only as field names, like a department's name): ``vlan.create`` (``subnet``, ``vlan_id``,
``department_id``, ``guest``, the fields the request sent with a value), ``vlan.update``
(``subnet`` and ``vlan_id`` after the change, ``changed`` fields, ``previous_subnet`` /
``previous_vlan_id`` when they changed, old/new of ``department_id`` and ``guest``),
``vlan.delete`` (``subnet``, ``vlan_id``, ``department_id``, ``guest``).
"""

from __future__ import annotations

import ipaddress
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Query, Request, Response, status
from pydantic import AfterValidator, Field, StringConstraints
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.schemas import StrictModel
from arcilogger_api.deps import DbSession
from arcilogger_api.directory import (
    Directory,
    Name,
    OptionalDhcpServer,
    OptionalName,
    OptionalNotes,
    OptionalPurpose,
    VlanOut,
)
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_db.models import Department, Vlan

router = APIRouter(prefix="/vlans", tags=["devices"])

ReaderDep = Annotated[CurrentUser, Depends(require("devices.read"))]
ManagerDep = Annotated[CurrentUser, Depends(require("devices.manage"))]
MAX_ID: Final = 2**63 - 1
#: IPv4-mapped IPv6 addresses (RFC 4291 2.5.5.2).
IPV4_MAPPED: Final = ipaddress.IPv6Network("::ffff:0:0/96")
#: Fields of a VLAN in the order of the API and the audit entries.
FIELDS: Final = (
    "subnet",
    "vlan_id",
    "name_tr",
    "purpose",
    "dhcp_server",
    "department_id",
    "guest",
    "notes",
)

MESSAGES: Final = {
    "vlan_exists": (
        "{subnet} ağı zaten tanımlı.",
        "The network {subnet} is already defined.",
    ),
    "vlan_id_taken": (
        "VLAN numarası {vlan_id} başka bir ağda kullanılıyor ({subnet}).",
        "VLAN id {vlan_id} is used by another network ({subnet}).",
    ),
    "dhcp_server_taken": (
        "{dhcp_server} DHCP sunucusu başka bir ağa bağlı ({subnet}).",
        "The DHCP server {dhcp_server} belongs to another network ({subnet}).",
    ),
}
#: Unique constraints of ``vlans`` → error code (a concurrent change answers 409, not 500).
_CONSTRAINTS: Final = (
    ("uq_vlans_vlan_id", "vlan_id_taken"),
    ("uq_vlans_dhcp_server", "dhcp_server_taken"),
    ("pk_vlans", "vlan_exists"),
)


def _subnet(value: str) -> str:
    """A network address with its prefix length, host bits zero, in canonical form."""
    value = value.strip()
    if "%" in value:
        raise ValueError("IPv6 zone ids are not allowed")
    if "/" not in value:
        raise ValueError("give the prefix length, e.g. 10.0.20.0/24")
    try:
        return str(ipaddress.ip_network(value, strict=True))
    except ValueError:
        pass
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        raise ValueError("not a network address, e.g. 10.0.20.0/24") from None
    raise ValueError(f"host bits are set; the network address is {network}")


def _new_subnet(value: str) -> str:
    """:func:`_subnet` of a network to write, not an IPv4 network written as IPv6
    (``::ffff:10.20.0.0/120``): the addresses of leases, devices and logs are IPv4 and never
    fall into it (PostgreSQL's ``>>=`` and :class:`ipaddress` do not mix the families)."""
    subnet = _subnet(value)
    network = ipaddress.ip_network(subnet)
    if isinstance(network, ipaddress.IPv6Network) and network.subnet_of(IPV4_MAPPED):
        mapped = network.network_address.ipv4_mapped
        meant = ipaddress.ip_network(f"{mapped}/{network.prefixlen - 96}")
        raise ValueError(f"an IPv4 network written as IPv6; enter the IPv4 network {meant}")
    return subnet


#: Names a stored VLAN (also one entered by SQL in a notation :func:`_new_subnet` refuses).
Subnet = Annotated[str, StringConstraints(min_length=1, max_length=64), AfterValidator(_subnet)]
#: The network of a new or moved VLAN.
NewSubnet = Annotated[
    str, StringConstraints(min_length=1, max_length=64), AfterValidator(_new_subnet)
]
VlanId = Annotated[int | None, Field(ge=1, le=4094)]
DepartmentId = Annotated[int | None, Field(ge=1, le=MAX_ID)]


def _error(code: str, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(409, code, text_tr.format(**params), text_en.format(**params))


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        details=[{"loc": ["body", field], "type": "value_error", "msg": message}],
    )


def _same_network(left: str, right: str) -> bool:
    return ipaddress.ip_network(left) == ipaddress.ip_network(right)


def _vlan(db: Session, subnet: str, *, lock: bool = False) -> Vlan:
    query = select(Vlan).where(Vlan.subnet == subnet)
    row = db.scalars(query.with_for_update() if lock else query).first()
    if row is None:
        raise ApiError(404, "not_found")
    return row


def _check(db: Session, values: dict[str, Any]) -> None:
    """The values to write (only new ones): ``department_id`` exists (422), the subnet, the VLAN
    id and the DHCP server are free (409)."""
    department_id = values.get("department_id")
    if department_id is not None and db.get(Department, department_id) is None:
        raise _invalid("department_id", "unknown department")
    subnet = values.get("subnet")
    if subnet is not None and db.scalar(select(Vlan.subnet).where(Vlan.subnet == subnet)):
        raise _error("vlan_exists", subnet=subnet)
    vlan_id = values.get("vlan_id")
    if vlan_id is not None:
        holder = db.scalar(select(Vlan.subnet).where(Vlan.vlan_id == vlan_id))
        if holder is not None:
            raise _error("vlan_id_taken", vlan_id=vlan_id, subnet=holder)
    server = values.get("dhcp_server")
    if server is not None:
        holder = db.scalar(select(Vlan.subnet).where(Vlan.dhcp_server == server))
        if holder is not None:
            raise _error("dhcp_server_taken", dhcp_server=server, subnet=holder)


def _flush(db: Session, values: dict[str, Any]) -> None:
    """Flush; the same subnet, VLAN id or DHCP server written concurrently answers 409."""
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError as exc:
        text = str(exc.orig)
        code = next((code for name, code in _CONSTRAINTS if name in text), None)
        if code is None:
            raise
        params = {"subnet": "—", "vlan_id": "—", "dhcp_server": "—"}
        params.update({key: value for key, value in values.items() if value is not None})
        raise _error(code, **params) from None


# -- endpoints ------------------------------------------------------------------------------------


@router.get("")
def list_vlans(db: DbSession, user: ReaderDep) -> list[VlanOut]:
    """Every VLAN/subnet (VLAN id order; networks without an id, e.g. a transit network, last)."""
    directory = Directory(db)
    return [directory.vlan_out(vlan) for vlan in directory.vlans]


class VlanCreate(StrictModel):
    subnet: NewSubnet
    vlan_id: VlanId = None
    name_tr: Name
    purpose: OptionalPurpose = None
    dhcp_server: OptionalDhcpServer = None
    #: The default department of the VLAN's devices without a person (``null``: the VLAN's name
    #: is the department of the 5651 chain).
    department_id: DepartmentId = None
    #: A guest network (e.g. a guest Wi-Fi that does not leave through the FortiGate).
    guest: bool = False
    notes: OptionalNotes = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_vlan(body: VlanCreate, request: Request, db: DbSession, user: ManagerDep) -> VlanOut:
    values = body.model_dump()
    _check(db, values)
    vlan = Vlan(**values)
    db.add(vlan)
    _flush(db, values)
    db.refresh(vlan)
    record_audit(
        db,
        actor=user,
        action="vlan.create",
        target=f"vlan:{vlan.subnet}",
        details={
            "subnet": vlan.subnet,
            "vlan_id": vlan.vlan_id,
            "department_id": vlan.department_id,
            "guest": vlan.guest,
            "fields": [
                field
                for field in FIELDS
                if field in body.model_fields_set and values[field] is not None
            ],
        },
        ip=client_ip(request),
    )
    return Directory(db).vlan_out(vlan)


class VlanUpdate(StrictModel):
    """``subnet`` names the VLAN; only the other fields sent are changed. ``null`` clears an
    optional field (not ``name_tr``, ``guest`` or ``new_subnet``)."""

    subnet: Subnet
    #: The VLAN's new network (a corrected subnet; everything else stays).
    new_subnet: NewSubnet | None = None
    vlan_id: VlanId = None
    name_tr: OptionalName = None
    purpose: OptionalPurpose = None
    dhcp_server: OptionalDhcpServer = None
    #: ``null`` removes the link (the VLAN's name is then the department of the 5651 chain).
    department_id: DepartmentId = None
    guest: bool | None = None
    notes: OptionalNotes = None


@router.patch("")
def update_vlan(body: VlanUpdate, request: Request, db: DbSession, user: ManagerDep) -> VlanOut:
    vlan = _vlan(db, body.subnet, lock=True)
    sent = body.model_fields_set
    for field in ("new_subnet", "name_tr", "guest"):
        if field in sent and getattr(body, field) is None:
            raise _invalid(field, "must not be null")
    values: dict[str, Any] = {field: getattr(body, field) for field in FIELDS[1:] if field in sent}
    if body.new_subnet is not None and not _same_network(body.new_subnet, vlan.subnet):
        values["subnet"] = body.new_subnet
    changed = [
        field for field in FIELDS if field in values and getattr(vlan, field) != values[field]
    ]
    if changed:
        _check(db, {field: values[field] for field in changed})
        details: dict[str, Any] = {"changed": changed}
        if "subnet" in changed:
            details["previous_subnet"] = vlan.subnet
        if "vlan_id" in changed:
            details["previous_vlan_id"] = vlan.vlan_id
        for field in ("department_id", "guest"):
            if field in changed:
                details[field] = {"old": getattr(vlan, field), "new": values[field]}
        for field in changed:
            setattr(vlan, field, values[field])
        _flush(db, {field: values[field] for field in changed})
        db.refresh(vlan)
        record_audit(
            db,
            actor=user,
            action="vlan.update",
            target=f"vlan:{vlan.subnet}",
            details={"subnet": vlan.subnet, "vlan_id": vlan.vlan_id, **details},
            ip=client_ip(request),
        )
    return Directory(db).vlan_out(vlan)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def delete_vlan(
    request: Request,
    db: DbSession,
    user: ManagerDep,
    subnet: Annotated[
        str,
        Query(min_length=1, max_length=64, description="The VLAN's subnet, e.g. 10.0.20.0/24"),
        AfterValidator(_subnet),
    ],
) -> Response:
    """Delete a VLAN. Its devices then have no VLAN name and no VLAN department; leases, raw
    logs and stored 5651 results keep what they hold."""
    vlan = _vlan(db, subnet, lock=True)
    details = {
        "subnet": vlan.subnet,
        "vlan_id": vlan.vlan_id,
        "department_id": vlan.department_id,
        "guest": vlan.guest,
    }
    db.delete(vlan)
    db.flush()
    record_audit(
        db,
        actor=user,
        action="vlan.delete",
        target=f"vlan:{details['subnet']}",
        details=details,
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)
