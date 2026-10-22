"""``/api/v1/devices`` — Cihazlar: list, detail, edit, assign to persons, CSV export (spec 9.2).

Contract: docs/MVP-CONTRACTS.md section 11 (group contract 4.3). Reads need ``devices.read``,
changes ``devices.manage``; every endpoint here returns person data (the current owner), so all
of them also need the accepted KVKK notice (``require_privacy_notice``). Today only
administrators hold ``devices.*`` (docs/roles.md).

* ``GET /devices`` — filters ``q`` (part of the MAC — also without separators —, a host name,
  the last IP or the device name; case-insensitive, Turkish letters folded), ``unknown``
  ("Bilinmeyen cihazlar": no current owner and not infrastructure), ``type``, ``person_id``,
  ``vlan``, ``randomized``, ``infrastructure``; ``sort`` ``last_seen`` (default) | ``first_seen`` |
  ``mac`` | ``last_ip`` | ``name``, ``order`` ``desc`` (default) | ``asc`` (empty values last);
  ``limit`` 1-500, ``offset``.
* ``GET /devices/{mac}`` — the device with its assignment history and its 20 newest leases.
* ``PATCH /devices/{mac}`` — ``name``, ``type``, ``infrastructure``, ``notes``, ``tags`` (only the
  fields sent). The correlation worker never overwrites them.
* ``POST /devices/assign`` — assigns 1-500 devices to a person (``person_id: null`` unassigns)
  from ``valid_from`` (default now): each device's current assignment is closed at that moment
  and a new one opened (:mod:`arcilogger_db.assignments`). ``valid_from`` must not be in the
  future (up to two minutes ahead of the server clock is taken as now) and not before the
  current assignment's start — the history is appended to, never rewritten (409
  ``assignment_in_future`` / ``assignment_overlap``, ``details`` per MAC). All or nothing. A
  device already held by that person is left unchanged (not counted in ``updated``). Inactive
  persons get no devices (409 ``person_inactive``). ``note`` is kept on the new assignment; an
  unassign refuses one (422), as there is nothing to keep it on.
* ``GET /devices/export.csv`` — the filtered list as CSV (``;``, UTF-8 with BOM, cells
  neutralised against formula injection, :mod:`arcilogger_api.csvfiles`).

**Audit** (never free text or person names: the audit log is sealed and kept): ``device.update``
(fields changed; old/new of ``type`` and ``infrastructure``), ``device.assign`` (person id,
``valid_from``, per changed device the MAC and the old and new person id), ``device.export``
(row count and which filters were used). Reads are not audited (like the lookup list).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field
from sqlalchemy import (
    ColumnElement,
    Select,
    Text,
    and_,
    cast,
    func,
    not_,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.auth.schemas import StrictModel
from arcilogger_api.csvfiles import csv_response, write_csv
from arcilogger_api.deps import DbSession
from arcilogger_api.directory import (
    MAX_TAGS,
    DeviceDetail,
    DeviceOut,
    DevicePage,
    DeviceType,
    Directory,
    OptionalName,
    OptionalNote,
    OptionalNotes,
    Tag,
    clean_tags,
)
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip
from arcilogger_api.textfold import fold, folded
from arcilogger_core.fmt import format_datetime
from arcilogger_core.timeutil import utc_now
from arcilogger_db.assignments import (
    AssignmentError,
    Planner,
    apply,
    is_overlap_error,
    load_history,
    lock_devices,
)
from arcilogger_db.models import Device, Person
from arcilogger_worker.lookup import normalize_mac

router = APIRouter(prefix="/devices", tags=["devices"])

ReaderDep = Annotated[CurrentUser, Depends(require_privacy_notice("devices.read"))]
ManagerDep = Annotated[CurrentUser, Depends(require_privacy_notice("devices.manage"))]
MAX_ID: Final = 2**63 - 1
#: Deepest page (``offset``); larger values answer 422, never a slow scan.
MAX_OFFSET: Final = 1_000_000
#: Devices of one ``POST /devices/assign``.
MAX_ASSIGN: Final = 500
SortKey = Literal["last_seen", "first_seen", "mac", "last_ip", "name"]
SortOrder = Literal["asc", "desc"]

MESSAGES: Final = {
    "device_not_found": (
        "Cihaz bulunamadı: {macs}.",
        "Device not found: {macs}.",
    ),
    "person_inactive": (
        "Kişi pasif; pasif bir kişiye cihaz atanamaz. Önce kişiyi etkinleştirin.",
        "The person is inactive; devices cannot be assigned to an inactive person. Activate the "
        "person first.",
    ),
    "assignment_in_future": (
        "Atama başlangıcı gelecekte olamaz.",
        "The start of an assignment must not be in the future.",
    ),
    "assignment_overlap": (
        "Atama başlangıcı, {mac} cihazının önceki atamasından ({limit}) önce olamaz: atama "
        "geçmişi değiştirilemez, yalnızca ileriye doğru eklenir.",
        "The start of the assignment must not be before {mac}'s previous assignment ({limit}): "
        "the assignment history cannot be rewritten, only extended.",
    ),
}


def _error(status: int, code: str, *, details: Any = None, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(
        status, code, text_tr.format(**params), text_en.format(**params), details=details
    )


def _mac_text(value: str) -> str:
    try:
        return normalize_mac(value)
    except ValueError:
        raise ValueError("not a MAC address (aa:bb:cc:dd:ee:ff)") from None


MacText = Annotated[str, Field(min_length=1, max_length=32), AfterValidator(_mac_text)]
MacPath = Annotated[str, Path(max_length=32, description="MAC address (aa:bb:cc:dd:ee:ff)")]


def path_mac(value: str) -> str:
    """A MAC from the URL, normalised; 422 when it is none."""
    try:
        return normalize_mac(value)
    except ValueError:
        raise ApiError(
            422,
            "validation_error",
            details=[{"loc": ["path", "mac"], "type": "value_error", "msg": "not a MAC address"}],
        ) from None


# -- filters --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DeviceFilters:
    q: str | None
    unknown: bool | None
    type: str | None
    person_id: int | None
    vlan: int | None
    randomized: bool | None
    infrastructure: bool | None

    def used(self) -> dict[str, Any]:
        """For the audit entry: the filters given (the search text only as "given")."""
        values: dict[str, Any] = {
            "unknown": self.unknown,
            "type": self.type,
            "person_id": self.person_id,
            "vlan": self.vlan,
            "randomized": self.randomized,
            "infrastructure": self.infrastructure,
        }
        used = {key: value for key, value in values.items() if value is not None}
        if self.q:
            used["q"] = True
        return used


def device_filters(
    q: Annotated[str | None, Query(max_length=200)] = None,
    unknown: bool | None = None,
    type_: Annotated[DeviceType | None, Query(alias="type")] = None,
    person_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    vlan: Annotated[int | None, Query(ge=1, le=4094)] = None,
    randomized: bool | None = None,
    infrastructure: bool | None = None,
) -> DeviceFilters:
    return DeviceFilters(
        q=q.strip() if q and q.strip() else None,
        unknown=unknown,
        type=type_,
        person_id=person_id,
        vlan=vlan,
        randomized=randomized,
        infrastructure=infrastructure,
    )


FiltersDep = Annotated[DeviceFilters, Depends(device_filters)]


def _search(text: str) -> ColumnElement[bool]:
    needle = fold(text)
    mac = cast(Device.mac, Text)
    conditions: list[ColumnElement[bool]] = [
        folded(func.coalesce(Device.name, "")).contains(needle, autoescape=True),
        folded(func.array_to_string(Device.hostnames, " ")).contains(needle, autoescape=True),
        func.host(Device.last_ip).contains(text.lower(), autoescape=True),
        mac.contains(text.lower().replace("-", ":"), autoescape=True),
    ]
    digits = re.sub(r"[:.\-\s]", "", text.lower())
    if digits and re.fullmatch(r"[0-9a-f]+", digits):
        conditions.append(func.replace(mac, ":", "").contains(digits, autoescape=True))
    return or_(*conditions)


def filtered(filters: DeviceFilters) -> Select[Device]:
    query = select(Device)
    if filters.q:
        query = query.where(_search(filters.q))
    unknown = and_(Device.person_id.is_(None), Device.infrastructure.is_(False))
    if filters.unknown is True:
        query = query.where(unknown)
    elif filters.unknown is False:
        query = query.where(not_(unknown))
    if filters.type is not None:
        query = query.where(Device.type == filters.type)
    if filters.person_id is not None:
        query = query.where(Device.person_id == filters.person_id)
    if filters.vlan is not None:
        query = query.where(Device.last_vlan == filters.vlan)
    if filters.randomized is not None:
        query = query.where(Device.randomized.is_(filters.randomized))
    if filters.infrastructure is not None:
        query = query.where(Device.infrastructure.is_(filters.infrastructure))
    return query


def ordered(query: Select[Device], sort: SortKey, order: SortOrder) -> Select[Device]:
    """Sorted by ``sort`` (empty values last in both directions), then by MAC."""
    table = Device.__table__.c
    column: ColumnElement[Any] = folded(table.name) if sort == "name" else table[sort]
    key = column.desc() if order == "desc" else column.asc()
    return query.order_by(key.nulls_last(), Device.mac)


def _device(db: Session, mac: str, *, lock: bool = False) -> Device:
    row = db.get(Device, mac, with_for_update=lock)
    if row is None:
        raise _error(404, "device_not_found", macs=mac)
    return row


# -- endpoints ------------------------------------------------------------------------------------


@router.get("")
def list_devices(
    db: DbSession,
    user: ReaderDep,
    filters: FiltersDep,
    sort: SortKey = "last_seen",
    order: SortOrder = "desc",
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> DevicePage:
    """One page of devices (``total`` = all devices matching the filters)."""
    query = filtered(filters)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(ordered(query, sort, order).limit(limit).offset(offset)))
    return DevicePage(items=Directory(db).devices(rows), total=total, limit=limit, offset=offset)


DEVICE_COLUMNS: Final = (
    "mac",
    "name",
    "type",
    "vendor",
    "randomized",
    "infrastructure",
    "hostnames",
    "last_ip",
    "last_vlan",
    "vlan_name",
    "person_id",
    "person",
    "employee_no",
    "assigned_since",
    "department",
    "department_source",
    "first_seen",
    "last_seen",
    "tags",
    "notes",
)


@router.get(
    "/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_devices(
    request: Request,
    db: DbSession,
    user: ReaderDep,
    filters: FiltersDep,
    sort: SortKey = "last_seen",
    order: SortOrder = "desc",
) -> Response:
    """Every device matching the filters as CSV (Turkish Excel: ``;``, UTF-8 with BOM)."""
    rows = list(db.scalars(ordered(filtered(filters), sort, order)))
    directory = Directory(db)
    outs = directory.devices(rows)
    persons = directory.persons(row.person_id for row in rows)
    lines = []
    for out in outs:
        owner = persons.get(out.person.id) if out.person is not None else None
        lines.append(
            (
                out.mac,
                out.name,
                out.type,
                out.vendor,
                out.randomized,
                out.infrastructure,
                out.hostnames,
                out.last_ip,
                out.last_vlan,
                out.vlan_name,
                None if out.person is None else out.person.id,
                None if out.person is None else out.person.name,
                None if owner is None else owner.employee_no,
                out.assigned_since,
                None if out.department is None else out.department.name,
                out.department_source,
                out.first_seen,
                out.last_seen,
                out.tags,
                out.notes,
            )
        )
    record_audit(
        db,
        actor=user,
        action="device.export",
        details={"rows": len(lines), "filters": filters.used(), "sort": sort, "order": order},
        ip=client_ip(request),
    )
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    return csv_response(write_csv(DEVICE_COLUMNS, lines), f"arcilogger-cihazlar-{stamp}.csv")


class AssignRequest(StrictModel):
    """Assign ``macs`` to ``person_id`` (``null``: unassign) from ``valid_from``."""

    macs: list[MacText] = Field(min_length=1, max_length=MAX_ASSIGN)
    person_id: Annotated[int | None, Field(ge=1, le=MAX_ID)]
    #: Start of the assignment (default: now); not in the future, not before the current one.
    valid_from: AwareDatetime | None = None
    #: Kept on the assignment opened; an unassign opens none, so it refuses a note (422).
    note: OptionalNote = None


class AssignResult(BaseModel):
    #: Devices whose assignment changed.
    updated: int
    #: Every requested device as it is now, in request order.
    devices: list[DeviceOut]


def assignment_conflict(problems: list[AssignmentError]) -> ApiError:
    """409 for refused changes: ``assignment_overlap`` if any overlaps, else in the future."""
    details = [
        {
            "mac": problem.mac,
            "reason": problem.code,
            "earliest": None if problem.limit is None else problem.limit.isoformat(),
        }
        for problem in problems
    ]
    overlap = next((problem for problem in problems if problem.code == "overlap"), None)
    if overlap is not None and overlap.limit is not None:
        return _error(
            409,
            "assignment_overlap",
            details=details,
            mac=overlap.mac,
            limit=format_datetime(overlap.limit),
        )
    return _error(409, "assignment_in_future", details=details)


def owner_for_assignment(db: Session, person_id: int | None) -> Person | None:
    """The person to assign to (locked against a concurrent deactivation); 422 when unknown,
    409 ``person_inactive`` when inactive."""
    if person_id is None:
        return None
    person = db.get(Person, person_id, with_for_update={"read": True})
    if person is None:
        raise ApiError(
            422,
            "validation_error",
            details=[
                {"loc": ["body", "person_id"], "type": "value_error", "msg": "unknown person"}
            ],
        )
    if not person.active:
        raise _error(409, "person_inactive")
    return person


@router.post("/assign")
def assign_devices(
    body: AssignRequest, request: Request, db: DbSession, user: ManagerDep
) -> AssignResult:
    """Close each device's current assignment at ``valid_from`` and open the new one."""
    if body.person_id is None and body.note is not None:
        # Nowhere to keep it: the closed row keeps its own note, the audit takes no free text.
        raise ApiError(
            422,
            "validation_error",
            details=[
                {
                    "loc": ["body", "note"],
                    "type": "value_error",
                    "msg": "an unassign (person_id null) takes no note",
                }
            ],
        )
    macs = list(dict.fromkeys(body.macs))
    owner_for_assignment(db, body.person_id)
    found = lock_devices(db, macs)
    missing = [mac for mac in macs if mac not in found]
    if missing:
        raise _error(404, "device_not_found", details={"macs": missing}, macs=", ".join(missing))
    planner = Planner(load_history(db, macs), now=utc_now(), owners=found)
    problems: list[AssignmentError] = []
    for mac in macs:
        try:
            planner.assign(mac, body.person_id, body.valid_from, note=body.note)
        except AssignmentError as exc:
            problems.append(exc)
    if problems:
        raise assignment_conflict(problems)
    if planner.changes:
        try:
            with db.begin_nested():
                apply(db, planner.changes, assigned_by=user.username)
        except IntegrityError as exc:
            if not is_overlap_error(exc):
                raise
            raise _error(409, "assignment_overlap", mac=", ".join(macs), limit="—") from None
        record_audit(
            db,
            actor=user,
            action="device.assign",
            target=f"device:{macs[0]}" if len(macs) == 1 else None,
            details={
                "person_id": body.person_id,
                # The effective start (the request may leave it to "now").
                "valid_from": planner.changes[0].valid_from.isoformat(),
                "requested": len(macs),
                "updated": len(planner.changes),
                "changes": [
                    {"mac": change.mac, "from": change.previous, "to": change.person_id}
                    for change in planner.changes
                ],
            },
            ip=client_ip(request),
        )
    current = select(Device).where(Device.mac.in_(macs)).execution_options(populate_existing=True)
    rows = {row.mac: row for row in db.scalars(current)}
    return AssignResult(
        updated=len(planner.changes),
        devices=Directory(db).devices([rows[mac] for mac in macs]),
    )


@router.get("/{mac}")
def get_device(mac: MacPath, db: DbSession, user: ReaderDep) -> DeviceDetail:
    """The device with its assignment history (newest first) and its 20 newest leases."""
    return Directory(db).device_detail(_device(db, path_mac(mac)))


class DeviceUpdate(StrictModel):
    """Only the fields sent are changed; ``name``/``type``/``notes``: ``null`` clears."""

    name: OptionalName = None
    type: DeviceType | None = None
    infrastructure: bool | None = None
    notes: OptionalNotes = None
    tags: list[Tag] | None = Field(default=None, max_length=MAX_TAGS)


@router.patch("/{mac}")
def update_device(
    mac: MacPath, body: DeviceUpdate, request: Request, db: DbSession, user: ManagerDep
) -> DeviceOut:
    """Edit what an administrator maintains (the worker never overwrites these fields)."""
    device = _device(db, path_mac(mac), lock=True)
    sent = body.model_fields_set
    for field in ("infrastructure", "tags"):
        if field in sent and getattr(body, field) is None:
            raise ApiError(422, "validation_error", details=[{"loc": ["body", field]}])
    new: dict[str, Any] = {
        field: getattr(body, field) for field in ("name", "type", "infrastructure", "notes")
    }
    if body.tags is not None:
        new["tags"] = clean_tags(body.tags)
    changed = [
        field
        for field in DeviceUpdate.model_fields
        if field in sent and getattr(device, field) != new[field]
    ]
    if changed:
        details: dict[str, Any] = {"changed": changed}
        for field in ("type", "infrastructure"):
            if field in changed:
                details[field] = {"old": getattr(device, field), "new": new[field]}
        for field in changed:
            setattr(device, field, new[field])
        db.flush()
        record_audit(
            db,
            actor=user,
            action="device.update",
            target=f"device:{device.mac}",
            details=details,
            ip=client_ip(request),
        )
    (out,) = Directory(db).devices([device])
    return out
