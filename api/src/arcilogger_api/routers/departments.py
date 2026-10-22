"""``/api/v1/departments`` — Departmanlar: list, create, rename (spec 9.2).

``devices.read`` / ``devices.manage``. A department carries no person data (only a count), so no
KVKK notice is needed. Names are unique, compared with Turkish letters folded ("İDARİ İŞLER" =
"İdari İşler", 409 ``department_exists``). No DELETE: persons and VLANs point at departments, and a
renamed department keeps its persons. Audited with ids and field names, never the name or the
notes (the sealed audit log is kept, and a department may be named after a person):
``department.create`` (``department_id``, the fields given), ``department.update``
(``department_id``, the fields changed).
"""

from __future__ import annotations

from typing import Annotated, Final

from fastapi import APIRouter, Depends, Path, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.schemas import StrictModel
from arcilogger_api.deps import DbSession
from arcilogger_api.directory import DepartmentOut, Directory, Name, OptionalName, OptionalNotes
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_api.textfold import fold, folded
from arcilogger_db.models import Department

router = APIRouter(prefix="/departments", tags=["devices"])

ReaderDep = Annotated[CurrentUser, Depends(require("devices.read"))]
ManagerDep = Annotated[CurrentUser, Depends(require("devices.manage"))]
DepartmentId = Annotated[int, Path(ge=1, le=2**63 - 1, description="Department number")]

MESSAGES: Final = {
    "department_exists": (
        "Bu adda bir departman zaten var ({name}).",
        "A department with this name already exists ({name}).",
    ),
}


def _exists(name: str) -> ApiError:
    text_tr, text_en = MESSAGES["department_exists"]
    return ApiError(409, "department_exists", text_tr.format(name=name), text_en.format(name=name))


def _taken(db: Session, name: str, *, department_id: int | None = None) -> bool:
    found = db.scalar(select(Department.id).where(folded(Department.name) == fold(name)))
    return found is not None and found != department_id


def _flush(db: Session, name: str) -> None:
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        raise _exists(name) from None


@router.get("")
def list_departments(db: DbSession, user: ReaderDep) -> list[DepartmentOut]:
    """Every department, by name."""
    rows = list(db.scalars(select(Department).order_by(folded(Department.name), Department.id)))
    return Directory(db).department_outs(rows)


class DepartmentCreate(StrictModel):
    name: Name
    notes: OptionalNotes = None


@router.post("", status_code=status.HTTP_201_CREATED)
def create_department(
    body: DepartmentCreate, request: Request, db: DbSession, user: ManagerDep
) -> DepartmentOut:
    if _taken(db, body.name):
        raise _exists(body.name)
    row = Department(name=body.name, notes=body.notes)
    db.add(row)
    _flush(db, body.name)
    record_audit(
        db,
        actor=user,
        action="department.create",
        target=f"department:{row.id}",
        details={
            "department_id": row.id,
            "fields": [field for field in ("name", "notes") if getattr(row, field) is not None],
        },
        ip=client_ip(request),
    )
    (out,) = Directory(db).department_outs([row])
    return out


class DepartmentUpdate(StrictModel):
    """Only the fields sent are changed; ``notes: null`` clears the notes."""

    name: OptionalName = None
    notes: OptionalNotes = None


@router.patch("/{department_id}")
def update_department(
    department_id: DepartmentId,
    body: DepartmentUpdate,
    request: Request,
    db: DbSession,
    user: ManagerDep,
) -> DepartmentOut:
    row = db.get(Department, department_id, with_for_update=True)
    if row is None:
        raise ApiError(404, "not_found")
    sent = body.model_fields_set
    if "name" in sent and body.name is None:
        raise ApiError(
            422,
            "validation_error",
            details=[{"loc": ["body", "name"], "type": "value_error", "msg": "must not be null"}],
        )
    changed: list[str] = []
    if "name" in sent and body.name is not None and body.name != row.name:
        if _taken(db, body.name, department_id=row.id):
            raise _exists(body.name)
        changed.append("name")
        row.name = body.name
    if "notes" in sent and body.notes != row.notes:
        changed.append("notes")
        row.notes = body.notes
    if changed:
        _flush(db, row.name)
        record_audit(
            db,
            actor=user,
            action="department.update",
            target=f"department:{row.id}",
            details={"department_id": row.id, "changed": changed},
            ip=client_ip(request),
        )
    (out,) = Directory(db).department_outs([row])
    return out
