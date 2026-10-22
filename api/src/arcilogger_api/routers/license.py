"""``/api/v1/license`` — Lisans (offline licensing, licensing contract 6.1-6.3).

* ``GET /license`` — the state, the modules, the trial, maintenance and limits; any signed-in
  user (no person data, no KVKK notice, not audited). ``details`` (licence and server ids, the
  request code, the time source, the history) only for ``settings.manage``.
* ``POST /license`` — upload a ``.lic`` file (multipart field ``file``); recent
  re-authentication and ``settings.manage``. 200: the ``GET`` document of the new state. Every
  refusal (also a body too large to read) is a ``refuse`` licence event and a
  ``license.refuse`` audit entry, committed although the request fails.
* ``DELETE /license`` — remove the installed licence (204; 404 ``license_not_installed``).

These routes are 5651 Temel: never gated by a module. The licence code is imported inside the
handlers only (contract 5.2): without it ``GET /license`` answers 503 ``license_unavailable``
and every other route keeps working.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from types import ModuleType
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep, get_state
from arcilogger_api.errors import ApiError
from arcilogger_api.licensing import (
    LicenseDep,
    invalidate_license,
    license_unavailable,
)
from arcilogger_api.routers.imports import UPLOAD_BODY
from arcilogger_api.security import CurrentUser, client_ip, require, require_recent_auth
from arcilogger_api.uploads import OVERHEAD, BodyTooLargeError, multipart_file, read_body
from arcilogger_db.license_guard import GuardedStatus

router = APIRouter(prefix="/license", tags=["license"])

#: Largest licence file (contract 2.1); the body may carry the multipart framing on top.
MAX_LICENSE_BYTES = 65_536

ReaderDep = Annotated[CurrentUser, Depends(require())]
AdminDep = Annotated[CurrentUser, Depends(require_recent_auth("settings.manage"))]

LicenseStateName = Literal[
    "trial",
    "trial_expired",
    "licensed",
    "invalid",
    "wrong_server",
    "expired",
    "version_not_covered",
]
TimeSourceName = Literal["system", "tsa", "vault_day", "release_date", "license_issued"]


# -- the document (contract 6.1) ------------------------------------------------------------------


class LicenseModuleOut(BaseModel):
    key: str
    name_tr: str
    name_en: str
    description_tr: str
    description_en: str
    #: ``LicenseStatus.module_enabled``: what this installation may use now.
    enabled: bool
    #: In every licence and every state (``core_5651``).
    always: bool
    #: Listed in the verified licence document (false without one).
    licensed: bool


class LicenseTrialOut(BaseModel):
    started_at: datetime
    ends_at: datetime
    days_left: int


class LicenseMaintenanceOut(BaseModel):
    until: date
    days_left: int
    expired: bool
    ends_soon: bool
    #: This version's release date is within the maintenance period.
    covers_version: bool


class LicenseLimitOut(BaseModel):
    #: ``null``: unlimited.
    max: int | None
    used: int
    #: Log sources only: counted senders that are not in the configuration.
    unconfigured: int
    exceeded: bool
    full: bool


class LicenseLimitsOut(BaseModel):
    log_sources: LicenseLimitOut
    users: LicenseLimitOut


class LicenseProductOut(BaseModel):
    name: str
    version: str
    release_date: date


class LicensorOut(BaseModel):
    name: str
    email: str


class LicenseTimeOut(BaseModel):
    now: datetime
    source: TimeSourceName
    tsa_time: datetime | None
    vault_day_end: datetime | None
    system_time: datetime
    clock_behind: bool


class LicenseEventOut(BaseModel):
    at: datetime
    kind: Literal["install", "remove", "refuse", "limit", "state"]
    source: Literal["web", "cli", "worker", "seed"]
    actor: str
    license_id: str | None
    details: dict[str, Any]


class LicenseDetailsOut(BaseModel):
    license_id: str | None
    key_id: str | None
    issued_at: datetime | None
    server_ids: list[str] | None
    installed_at: datetime | None
    installed_by: str | None
    server_id: str | None
    server_id_error: str | None
    installation_id: str
    organization: str
    request_code: str | None
    time: LicenseTimeOut
    #: The newest 20 licence events, newest first.
    history: list[LicenseEventOut]


class LicenseOut(BaseModel):
    status: LicenseStateName
    problem: str | None
    modules: list[LicenseModuleOut]
    trial: LicenseTrialOut | None
    licensee: str | None
    maintenance: LicenseMaintenanceOut | None
    valid_until: date | None
    limits: LicenseLimitsOut
    product: LicenseProductOut
    licensor: LicensorOut
    checked_at: datetime
    #: Only for ``settings.manage``.
    details: LicenseDetailsOut | None


# -- helpers --------------------------------------------------------------------------------------


def _licensing() -> tuple[ModuleType, type[Exception]]:
    """The licence database layer and ``LicenseError``; 503 when they cannot be loaded."""
    try:
        from arcilogger_core.license import LicenseError
        from arcilogger_db import licensing
    except ImportError:
        raise license_unavailable() from None
    return licensing, LicenseError


def _writer(db: Session, user: CurrentUser, request: Request) -> Any:
    """``AuditWriter`` of the signed-in user (``record_audit``, with the request's address)."""
    ip = client_ip(request)

    def write(action: str, target: str | None, details: Any) -> None:
        record_audit(db, actor=user, action=action, target=target, details=details, ip=ip)

    return write


def _api_error(error: Any) -> ApiError:
    details = dict(error.details) or None
    return ApiError(
        error.http_status, error.code, error.message_tr, error.message_en, details=details
    )


@dataclass(frozen=True)
class Upload:
    """The request body, or the refusal of a body too large to read."""

    body: bytes | None
    too_large: BodyTooLargeError | None = None


async def license_upload(request: Request) -> Upload:
    """The body, bounded while it streams in (``license_too_large`` above the limit)."""
    try:
        body = await read_body(
            request, MAX_LICENSE_BYTES + OVERHEAD, too_large_code="license_too_large"
        )
    except BodyTooLargeError as exc:
        return Upload(body=None, too_large=exc)
    return Upload(body=body)


UploadDep = Annotated[Upload, Depends(license_upload)]


# -- routes ---------------------------------------------------------------------------------------


@router.get("")
def get_license(
    db: DbSession, config: ConfigDep, user: ReaderDep, current: LicenseDep
) -> LicenseOut:
    """The licence state for the banner and the licence page (any signed-in user)."""
    licensing, _ = _licensing()
    document = licensing.status_json(
        current, details=user.has("settings.manage"), session=db, config=config
    )
    return LicenseOut.model_validate(document)


@router.post("", openapi_extra=UPLOAD_BODY)
def upload_license(
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    user: AdminDep,
    upload: UploadDep,
) -> LicenseOut:
    """Install a licence file (``.lic``) after its signature and this server's checks."""
    licensing, license_error = _licensing()
    if upload.too_large is not None:
        refused = license_error("license_too_large")
        licensing.record_refusal(
            db,
            refused,
            None,
            actor=user.username,
            source="web",
            audit=_writer(db, user, request),
            size=upload.too_large.size,
        )
        db.commit()
        raise upload.too_large
    data = multipart_file(request.headers.get("content-type", ""), upload.body or b"")
    try:
        current = licensing.install_license(
            db,
            data,
            config=config,
            layout=layout,
            actor=user.username,
            source="web",
            audit=_writer(db, user, request),
        )
    except license_error as exc:
        db.rollback()  # nothing of the install is kept; the refusal is its own transaction
        licensing.record_refusal(
            db, exc, data, actor=user.username, source="web", audit=_writer(db, user, request)
        )
        db.commit()
        raise _api_error(exc) from None
    db.commit()
    cache = get_state(request).license_cache
    cache.invalidate()
    cache.put(GuardedStatus(status=current))
    document = licensing.status_json(current, details=True, session=db, config=config)
    return LicenseOut.model_validate(document)


@router.delete("", status_code=status.HTTP_204_NO_CONTENT)
def remove_license(request: Request, db: DbSession, user: AdminDep) -> Response:
    """Remove the installed licence: the state becomes ``trial`` or ``trial_expired``."""
    licensing, license_error = _licensing()
    try:
        licensing.remove_license(
            db, actor=user.username, source="web", audit=_writer(db, user, request)
        )
    except license_error as exc:
        raise _api_error(exc) from None
    db.commit()
    invalidate_license(request)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
