"""``/api/v1/imports`` — CSV içe aktarma: persons and device assignments (spec 9.2).

* ``POST /imports/{kind}?dry_run=true|false`` — ``kind`` ``persons`` or ``assignments``; the file
  in the multipart field ``file`` (UTF-8, ``;`` or ``,``, at most 5 MB / 10,000 rows). The default
  is a **dry run**: nothing is written, the answer shows per-line errors and what would change.
  ``dry_run=false`` applies — all lines in one transaction, or none when any line has an error.
  Rules per kind: :mod:`arcilogger_api.csvimport`. ``devices.manage`` and the KVKK notice (the
  preview names persons).
* ``GET /imports/{kind}/template.csv`` — the header and one example row (``devices.read``).

**Audit:** an applied import (``dry_run=false`` and no errors) as ``import.persons`` /
``import.assignments`` with the file's SHA-256 and size and the counts — never the persons or
the lines. Dry runs and refused applies change nothing and are not audited (like reading).
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Path, Request, Response
from sqlalchemy.exc import IntegrityError

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.csvfiles import MAX_BYTES, csv_response, write_csv
from arcilogger_api.csvimport import (
    TEMPLATES,
    ImportKind,
    ImportResult,
    import_assignments,
    import_persons,
)
from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.licensing import require_module
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_api.uploads import OVERHEAD, multipart_file, read_body
from arcilogger_core.timeutil import utc_now
from arcilogger_db.assignments import apply, is_overlap_error

#: Paid module ``bulk_import`` (licensing contract 1.3): both routes answer 403
#: ``module_not_licensed`` while the module is off. Manual entry (``POST /persons``,
#: ``POST /devices/assign``) is 5651 Temel and always works.
router = APIRouter(
    prefix="/imports", tags=["devices"], dependencies=[require_module("bulk_import")]
)

ManagerDep = Annotated[CurrentUser, Depends(require_privacy_notice("devices.manage"))]
ReaderDep = Annotated[CurrentUser, Depends(require("devices.read"))]
KindPath = Annotated[ImportKind, Path(description="persons or assignments")]

MESSAGES: Final = {
    "import_conflict": (
        "Kayıtlar içe aktarma sırasında başka bir işlemle değişti; önizlemeyi yeniden çalıştırın.",
        "The records changed during the import; run the preview again.",
    ),
}
UPLOAD_BODY: Final = {
    "requestBody": {
        "required": True,
        "content": {
            "multipart/form-data": {
                "schema": {
                    "type": "object",
                    "required": ["file"],
                    "properties": {"file": {"type": "string", "format": "binary"}},
                }
            }
        },
    }
}


async def uploaded_file(request: Request) -> bytes:
    """The bytes of the multipart field ``file`` (bounded while the body streams in)."""
    body = await read_body(request, MAX_BYTES + OVERHEAD)
    return multipart_file(request.headers.get("content-type", ""), body)


@router.post("/{kind}", openapi_extra=UPLOAD_BODY)
def run_import(
    kind: KindPath,
    request: Request,
    db: DbSession,
    user: ManagerDep,
    data: Annotated[bytes, Depends(uploaded_file)],
    dry_run: bool = True,
) -> ImportResult:
    """Check a CSV file and (``dry_run=false``) apply it, all lines or none."""
    apply_now = not dry_run
    try:
        with db.begin_nested():
            if kind == "persons":
                outcome = import_persons(db, data, apply=apply_now)
            else:
                outcome, changes = import_assignments(db, data, apply=apply_now, now=utc_now())
                if apply_now and not outcome.errors and changes:
                    apply(db, changes, assigned_by=user.username)
    except IntegrityError as exc:
        if kind == "assignments" and not is_overlap_error(exc):
            raise
        text_tr, text_en = MESSAGES["import_conflict"]
        raise ApiError(409, "import_conflict", text_tr, text_en) from None
    result = outcome.result(dry_run=dry_run)
    if apply_now and not outcome.errors:
        record_audit(
            db,
            actor=user,
            action=f"import.{kind}",
            details={
                "file_sha256": hashlib.sha256(data).hexdigest(),
                "size": len(data),
                "rows": result.rows,
                "created": result.created,
                "updated": result.updated,
                "unchanged": result.unchanged,
            },
            ip=client_ip(request),
        )
    return result


@router.get(
    "/{kind}/template.csv",
    response_class=Response,
    responses={200: {"content": {"text/csv": {}}}},
)
def import_template(kind: KindPath, user: ReaderDep) -> Response:
    """Header and one example row (UTF-8 with BOM, ``;``)."""
    header, example = TEMPLATES[kind]
    name = "kisiler" if kind == "persons" else "atamalar"
    return csv_response(write_csv(header, [example]), f"arcilogger-{name}-sablon.csv")
