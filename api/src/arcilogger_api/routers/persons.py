"""``/api/v1/persons`` — Kişiler: list, detail, create, edit, CSV export (spec 9.2).

Contract: docs/MVP-CONTRACTS.md section 11. Person data: ``devices.read`` / ``devices.manage``
**and** the accepted KVKK notice on every endpoint. There is no DELETE: a person who leaves is
deactivated (``active: false``); the assignment history keeps naming them for the moments they
held a device, and inactive persons get no new devices. Deactivating does not unassign the
person's current devices — that is a separate, deliberate step (``POST /devices/assign``).

* ``GET /persons`` — ``q`` (part of the name, e-mail, employee number or title;
  case-insensitive, Turkish letters folded), ``active``, ``department_id``; sorted by name;
  ``limit`` 1-500, ``offset``.
* ``POST /persons`` / ``PATCH /persons/{id}`` — ``employee_no`` is unique (409
  ``employee_no_taken``), the e-mail must look like one, ``department_id`` must exist (422).
* ``GET /persons/{id}`` — with the devices held now and every assignment (newest first).
* ``GET /persons/export.csv`` — the filtered list as CSV; it imports again unchanged
  (``POST /imports/persons``).

``usage_excluded`` (area U, ADR-0011) leaves the person out of every person-level usage view and
report; their traffic still counts in the company and department totals. 5651 lookups, their
official reports and evidence packages never read it.

**Audit** (the sealed audit log gets ids and field names, never names, e-mails or notes):
``person.create`` (``person_id``, ``department_id``, ``active``, ``usage_excluded``, the fields
given), ``person.update`` (fields changed; old/new of ``department_id``, ``active`` and
``usage_excluded``),
``person.export`` (row count, which filters).
"""

from __future__ import annotations

from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import Field
from sqlalchemy import ColumnElement, Select, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.auth.schemas import StrictModel
from arcilogger_api.csvfiles import csv_response, write_csv
from arcilogger_api.deps import DbSession
from arcilogger_api.directory import (
    Directory,
    Name,
    OptionalEmail,
    OptionalEmployeeNo,
    OptionalName,
    OptionalNotes,
    PersonDetail,
    PersonOut,
    PersonPage,
)
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip
from arcilogger_api.textfold import fold, folded
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import Department, Person

router = APIRouter(prefix="/persons", tags=["devices"])

ReaderDep = Annotated[CurrentUser, Depends(require_privacy_notice("devices.read"))]
ManagerDep = Annotated[CurrentUser, Depends(require_privacy_notice("devices.manage"))]
MAX_ID: Final = 2**63 - 1
MAX_OFFSET: Final = 1_000_000
PersonId = Annotated[int, Path(ge=1, le=MAX_ID, description="Person number")]
#: Fields of a person in the order of the API, the CSV and the audit entries.
FIELDS: Final = (
    "name",
    "department_id",
    "title",
    "email",
    "employee_no",
    "active",
    "notes",
    "usage_excluded",
)

MESSAGES: Final = {
    "employee_no_taken": (
        "Bu sicil numarası ({employee_no}) başka bir kişiye ait.",
        "This employee number ({employee_no}) belongs to another person.",
    ),
}


def _error(status_code: int, code: str, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(status_code, code, text_tr.format(**params), text_en.format(**params))


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        details=[{"loc": ["body", field], "type": "value_error", "msg": message}],
    )


def person_search(text: str) -> ColumnElement[bool]:
    needle = fold(text)
    return or_(
        *(
            folded(func.coalesce(column, "")).contains(needle, autoescape=True)
            for column in (Person.name, Person.email, Person.employee_no, Person.title)
        )
    )


def filtered(q: str | None, active: bool | None, department_id: int | None) -> Select[Person]:
    query = select(Person)
    if q and q.strip():
        query = query.where(person_search(q.strip()))
    if active is not None:
        query = query.where(Person.active.is_(active))
    if department_id is not None:
        query = query.where(Person.department_id == department_id)
    return query.order_by(folded(Person.name), Person.id)


def _person(db: Session, person_id: int, *, lock: bool = False) -> Person:
    row = db.get(Person, person_id, with_for_update=lock)
    if row is None:
        raise ApiError(404, "not_found")
    return row


def _check(db: Session, values: dict[str, Any], *, person_id: int | None = None) -> None:
    """``department_id`` exists (422) and ``employee_no`` is free (409)."""
    department_id = values.get("department_id")
    if department_id is not None and db.get(Department, department_id) is None:
        raise _invalid("department_id", "unknown department")
    employee_no = values.get("employee_no")
    if employee_no is not None:
        taken = db.scalar(select(Person.id).where(Person.employee_no == employee_no))
        if taken is not None and taken != person_id:
            raise _error(409, "employee_no_taken", employee_no=employee_no)


def _flush(db: Session, employee_no: str | None) -> None:
    """Flush; a concurrent insert of the same employee number answers 409, not 500."""
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise _error(409, "employee_no_taken", employee_no=employee_no or "—") from None


# -- endpoints ------------------------------------------------------------------------------------


@router.get("")
def list_persons(
    db: DbSession,
    user: ReaderDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    active: bool | None = None,
    department_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> PersonPage:
    """One page of persons sorted by name (``total`` = all matching)."""
    query = filtered(q, active, department_id)
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery())) or 0
    rows = list(db.scalars(query.limit(limit).offset(offset)))
    return PersonPage(
        items=Directory(db).person_outs(rows), total=total, limit=limit, offset=offset
    )


class PersonCreate(StrictModel):
    name: Name
    department_id: Annotated[int | None, Field(ge=1, le=MAX_ID)] = None
    title: OptionalName = None
    email: OptionalEmail = None
    employee_no: OptionalEmployeeNo = None
    active: bool = True
    notes: OptionalNotes = None
    usage_excluded: bool = False


@router.post("", status_code=status.HTTP_201_CREATED)
def create_person(
    body: PersonCreate, request: Request, db: DbSession, user: ManagerDep
) -> PersonOut:
    values = body.model_dump()
    _check(db, values)
    person = Person(**values)
    db.add(person)
    _flush(db, body.employee_no)
    db.refresh(person)
    record_audit(
        db,
        actor=user,
        action="person.create",
        target=f"person:{person.id}",
        details={
            "person_id": person.id,
            "department_id": person.department_id,
            "active": person.active,
            "usage_excluded": person.usage_excluded,
            "fields": [field for field in FIELDS if values.get(field) is not None],
        },
        ip=client_ip(request),
    )
    (out,) = Directory(db).person_outs([person])
    return out


PERSON_COLUMNS: Final = (
    "id",
    "name",
    "department",
    "title",
    "email",
    "employee_no",
    "active",
    "notes",
    "usage_excluded",
    "device_count",
    "created_at",
    "updated_at",
)


@router.get(
    "/export.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def export_persons(
    request: Request,
    db: DbSession,
    user: ReaderDep,
    q: Annotated[str | None, Query(max_length=200)] = None,
    active: bool | None = None,
    department_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
) -> Response:
    """Every person matching the filters as CSV (re-importable with ``/imports/persons``)."""
    rows = list(db.scalars(filtered(q, active, department_id)))
    outs = Directory(db).person_outs(rows)
    lines = [
        (
            out.id,
            out.name,
            None if out.department is None else out.department.name,
            out.title,
            out.email,
            out.employee_no,
            out.active,
            out.notes,
            out.usage_excluded,
            out.device_count,
            out.created_at,
            out.updated_at,
        )
        for out in outs
    ]
    used: dict[str, Any] = {"active": active, "department_id": department_id}
    filters = {key: value for key, value in used.items() if value is not None}
    if q and q.strip():
        filters["q"] = True
    record_audit(
        db,
        actor=user,
        action="person.export",
        details={"rows": len(lines), "filters": filters},
        ip=client_ip(request),
    )
    stamp = utc_now().strftime("%Y%m%d-%H%M%S")
    return csv_response(write_csv(PERSON_COLUMNS, lines), f"arcilogger-kisiler-{stamp}.csv")


@router.get("/{person_id}")
def get_person(person_id: PersonId, db: DbSession, user: ReaderDep) -> PersonDetail:
    """The person with the devices held now and every assignment (newest first)."""
    return Directory(db).person_detail(_person(db, person_id))


class PersonUpdate(StrictModel):
    """Only the fields sent are changed; ``null`` clears an optional field (not ``name``,
    ``active`` or ``usage_excluded``)."""

    name: OptionalName = None
    department_id: Annotated[int | None, Field(ge=1, le=MAX_ID)] = None
    title: OptionalName = None
    email: OptionalEmail = None
    employee_no: OptionalEmployeeNo = None
    active: bool | None = None
    notes: OptionalNotes = None
    usage_excluded: bool | None = None


@router.patch("/{person_id}")
def update_person(
    person_id: PersonId, body: PersonUpdate, request: Request, db: DbSession, user: ManagerDep
) -> PersonOut:
    person = _person(db, person_id, lock=True)
    sent = body.model_fields_set
    for field in ("name", "active", "usage_excluded"):
        if field in sent and getattr(body, field) is None:
            raise _invalid(field, "must not be null")
    values = {field: getattr(body, field) for field in FIELDS if field in sent}
    changed = [field for field, value in values.items() if getattr(person, field) != value]
    if changed:
        _check(db, {field: values[field] for field in changed}, person_id=person.id)
        details: dict[str, Any] = {"changed": changed}
        for field in ("department_id", "active", "usage_excluded"):
            if field in changed:
                details[field] = {"old": getattr(person, field), "new": values[field]}
        for field in changed:
            setattr(person, field, values[field])
        _flush(db, values.get("employee_no"))
        db.refresh(person)
        record_audit(
            db,
            actor=user,
            action="person.update",
            target=f"person:{person.id}",
            details=details,
            ip=client_ip(request),
        )
    (out,) = Directory(db).person_outs([person])
    return out
