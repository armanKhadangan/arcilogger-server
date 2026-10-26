"""``/api/v1/seals`` — Mühür takvimi, gün ayrıntısı, doğrula (spec 6.6-6.9, 12.3 page 9).

Permission ``seal.read`` (administrators, auditors) unless noted:

* ``GET /seals/calendar?month=YYYY-MM`` — the state of every day of the month: ``verified``
  (sealed and verified within the last 35 days), ``sealed`` (not verified recently), ``late``
  (stamped after the allowed time), ``pending``, ``failed`` (with the message), ``open`` (today),
  ``future``, ``no_data``, ``deleted`` (retention). Read from the vault and the job state files
  (:mod:`arcilogger_api.sealinfo`), not from the database.
* ``GET /seals/{day}`` — the day: files, sizes, lines and hashes exactly as in the manifest,
  the manifest's own hash, the link to the previous day (and whether it still matches the
  vault), gaps, clock data, time-stamp token (genTime, serial, TSA, policy, certificate
  validity, provider, ``legal_grade``, a fresh token check), the newest verification and the
  replica status.
* ``GET /seals/{day}/manifest`` and ``/token`` — the sealed files (audited ``seal.download``).
* ``POST /seals/{day}/verify?deep=true`` (``seal.verify``) — the core day check in a child
  process with a time limit (:mod:`arcilogger_api.sealcheck`); the result is stored in
  ``seal_verifications`` (the API may not write ``state/verify``) and audited ``seal.verify``.
* ``POST /seals/{day}/seal`` (``seal.manage``) — "Şimdi mühürle" (ADR-0014; contract 2.9): the
  API asks the administration agent for one sealing attempt of the day; the agent signs a seal
  request for the Seal task (LOCAL SERVICE), which takes it at its next start (woken at once) and
  ignores the day's retry wait once — the quiet window after midnight, the order of the days and
  the empty-day rules stay as they are. Only the oldest closed unsealed day in state ``failed`` or
  ``pending`` can be asked for (409 ``seal_not_due``; ``seal_day_sealed``,
  ``seal_not_applicable``, ``seal_request_pending``). Answer 202 with the request to follow
  (``GET /admin/requests/{id}``); its result is the day's outcome.
"""

from __future__ import annotations

import logging
import threading
from datetime import date, datetime, timedelta
from pathlib import Path as FsPath
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from arcilogger_api.admin_channel import AdminAccepted, Channel, ChannelDep, admin_error
from arcilogger_api.audit import record_audit
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.errors import ApiError
from arcilogger_api.issues import issues_tr
from arcilogger_api.sealcheck import VERIFY_TIMEOUT_SECONDS, read_roots, run_check
from arcilogger_api.sealinfo import (
    RECENT_VERIFY_DAYS,
    STATES,
    VaultView,
    month_days,
    on_demand_verifications,
    read_seal_state,
)
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer, sha256_file
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_core.tsa.base import StampResult, TsaError
from arcilogger_db.models import SealVerification

log = logging.getLogger("arcilogger.api.seals")

router = APIRouter(prefix="/seals", tags=["seals"])

ReaderDep = Annotated[CurrentUser, Depends(require("seal.read"))]
VerifierDep = Annotated[CurrentUser, Depends(require("seal.verify"))]
ManagerDep = Annotated[CurrentUser, Depends(require("seal.manage"))]
DayParam = Annotated[date, Path(description="YYYY-MM-DD")]
MonthParam = Annotated[str | None, Query(pattern=r"^2\d{3}-(0[1-9]|1[0-2])$")]
#: One on-demand verification at a time per API process (a deep check reads gigabytes).
_verify_slot = threading.Semaphore(1)
#: The earliest day that can be asked for (well before any ARCILOGGER installation).
FIRST_DAY: Final = date(2000, 1, 1)


class CalendarDay(BaseModel):
    day: date
    state: str
    label_tr: str
    label_en: str
    tone: str
    message_tr: str | None
    message_en: str | None
    #: The technical (English) text behind the message (shown apart from it, as a detail).
    error_detail: str | None
    overdue: bool
    files: int | None
    lines: int | None
    stamped_late: bool
    sealed_at: str | None
    serial: str | None
    gen_time: str | None
    verification: dict[str, Any] | None
    replica: str | None


class StateLegend(BaseModel):
    state: str
    label_tr: str
    label_en: str
    tone: str


class Calendar(BaseModel):
    month: str
    today: date
    days: list[CalendarDay]
    counts: dict[str, int]
    legend: list[StateLegend]
    recent_verify_days: int
    replica_enabled: bool
    #: State files that could not be read (shown as a warning).
    problems: list[str]


class VerifyResult(BaseModel):
    day: date
    outcome: str
    ok: bool
    deep: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: int
    errors: list[str]
    warnings: list[str]
    #: ``errors`` / ``warnings`` in Turkish (:func:`arcilogger_api.issues.issue_tr`).
    errors_tr: list[str]
    warnings_tr: list[str]
    details: dict[str, Any]
    message_tr: str
    message_en: str


def _check_day(day: date) -> None:
    if day < FIRST_DAY or day > site_day(utc_now()):
        raise ApiError(
            404, "not_found", "Bu gün için kayıt yok.", "There is no record of this day."
        )


@router.get("/calendar")
def calendar(
    db: DbSession,
    user: ReaderDep,
    layout: LayoutDep,
    config: ConfigDep,
    month: MonthParam = None,
) -> Calendar:
    """The state of every day of ``month`` (default: the current month, site time)."""
    view = VaultView(layout, config)
    first = date.fromisoformat(f"{month}-01") if month else view.today.replace(day=1)
    days = month_days(first)
    on_demand = on_demand_verifications(db, days[0], days[-1])
    cells = [view.summary(day, on_demand.get(day)) for day in days]
    counts: dict[str, int] = {}
    for cell in cells:
        counts[cell["state"]] = counts.get(cell["state"], 0) + 1
    return Calendar(
        month=first.strftime("%Y-%m"),
        today=view.today,
        days=[CalendarDay.model_validate(cell) for cell in cells],
        counts=counts,
        legend=[
            StateLegend(state=name, label_tr=tr, label_en=en, tone=tone)
            for name, (tr, en, tone) in STATES.items()
        ],
        recent_verify_days=RECENT_VERIFY_DAYS,
        replica_enabled=config.replica.enabled,
        problems=view.problems,
    )


@router.get("/{day}")
def day_detail(
    day: DayParam, db: DbSession, user: ReaderDep, layout: LayoutDep, config: ConfigDep
) -> dict[str, Any]:
    """Everything about one day; file values are exactly those sealed in the manifest."""
    _check_day(day)
    view = VaultView(layout, config)
    conclusive = on_demand_verifications(db, day, day).get(day)
    last_attempt = on_demand_verifications(db, day, day, conclusive=False).get(day)
    roots = read_roots(layout, config.tsa.roots_dir, provider=config.tsa.provider)
    detail = view.detail(day, roots, conclusive, last_attempt)
    detail["problems"] = view.problems
    return detail


def _sealed_file(
    request: Request, db: Session, user: CurrentUser, layout: Layout, day: date, kind: str
) -> FileResponse:
    _check_day(day)
    path = layout.manifest(day) if kind == "manifest" else layout.token(day)
    # A failed attempt leaves a manifest without a token: that manifest was never sealed.
    if not (layout.manifest(day).is_file() and layout.token(day).is_file()):
        raise ApiError(404, "not_found", "Bu gün mühürlü değil.", "This day is not sealed.")
    record_audit(
        db,
        actor=user,
        action="seal.download",
        target=f"seal:{day.isoformat()}",
        details={"file": path.name, "kind": kind, "sha256": sha256_file(path)},
        ip=client_ip(request),
    )
    return FileResponse(
        path,
        media_type="application/json" if kind == "manifest" else "application/octet-stream",
        filename=path.name,
        content_disposition_type="attachment",
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.get("/{day}/manifest")
def download_manifest(
    day: DayParam, request: Request, db: DbSession, user: ReaderDep, layout: LayoutDep
) -> FileResponse:
    """``manifest-<day>.json`` as sealed (canonical JSON)."""
    return _sealed_file(request, db, user, layout, day, "manifest")


@router.get("/{day}/token")
def download_token(
    day: DayParam, request: Request, db: DbSession, user: ReaderDep, layout: LayoutDep
) -> FileResponse:
    """``manifest-<day>.json.zd``: the RFC 3161 time-stamp token of the manifest."""
    return _sealed_file(request, db, user, layout, day, "token")


@router.post("/{day}/verify")
def verify_day(
    day: DayParam,
    request: Request,
    db: DbSession,
    user: VerifierDep,
    layout: LayoutDep,
    config: ConfigDep,
    deep: bool = True,
) -> VerifyResult:
    """Verify the day now (deep: also decompress and re-hash the raw data)."""
    _check_day(day)
    if not (layout.manifest(day).is_file() and layout.token(day).is_file()):
        raise ApiError(404, "not_found", "Bu gün mühürlü değil.", "This day is not sealed.")
    if not read_roots(layout, config.tsa.roots_dir, provider=config.tsa.provider):
        raise ApiError(
            409,
            "no_trusted_roots",
            "Güvenilen kök sertifika yok (tsa.roots_dir); doğrulama yapılamaz.",
            "No trusted root certificates (tsa.roots_dir); nothing can be verified.",
        )
    if not _verify_slot.acquire(blocking=False):
        raise ApiError(
            409,
            "verification_running",
            "Başka bir doğrulama sürüyor; bitince tekrar deneyin.",
            "Another verification is running; try again when it has finished.",
        )
    started = utc_now()
    try:
        result = run_check(
            layout, config.tsa.roots_dir, day, deep=deep, provider=config.tsa.provider
        )
    finally:
        _verify_slot.release()
    finished = utc_now()
    row = SealVerification(
        day=day,
        started_at=started,
        finished_at=finished,
        user_id=user.id,
        username=user.username,
        deep=deep,
        outcome=result.outcome,
        errors=result.errors,
        warnings=result.warnings,
        details={**result.details, "duration_ms": result.duration_ms},
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        actor=user,
        action="seal.verify",
        target=f"seal:{day.isoformat()}",
        details={
            "outcome": result.outcome,
            "deep": deep,
            "errors": result.errors[:20],
            "warnings": len(result.warnings),
            "duration_ms": result.duration_ms,
        },
        ip=client_ip(request),
    )
    when = day.strftime("%d.%m.%Y")
    messages = {
        "ok": (f"{when} günü doğrulandı.", f"{when} verified."),
        "failed": (
            f"{when} günü doğrulanamadı: {'; '.join(issues_tr(result.errors[:3]))}",
            f"{when} does not verify: {'; '.join(result.errors[:3])}",
        ),
        "timeout": (
            f"Doğrulama {int(VERIFY_TIMEOUT_SECONDS)} sn içinde bitmedi ve durduruldu; sonuç yok. "
            "Zamanlanmış doğrulama görevini bekleyin ya da hızlı doğrulamayı deneyin.",
            f"The verification did not finish within {int(VERIFY_TIMEOUT_SECONDS)} s and was "
            "stopped; no result. Wait for the scheduled verification or try a quick check.",
        ),
        "error": (
            "Doğrulama çalıştırılamadı; ayrıntılar sistem kaydında.",
            "The verification could not run; details are in the system log.",
        ),
    }
    if result.outcome == "error":
        log.error("verification of %s could not run: %s", day, result.errors)
    message_tr, message_en = messages[result.outcome]
    return VerifyResult(
        day=day,
        outcome=result.outcome,
        ok=result.outcome == "ok",
        deep=deep,
        started_at=started,
        finished_at=finished,
        duration_ms=result.duration_ms,
        errors=result.errors,
        warnings=result.warnings,
        errors_tr=issues_tr(result.errors),
        warnings_tr=issues_tr(result.warnings),
        details=row.details,
        message_tr=message_tr,
        message_en=message_en,
    )


class _NoStamps:
    """The sealer only answers which day is due here; it never stamps."""

    name = "none"
    legal_grade = False

    def stamp(self, file_path: FsPath) -> StampResult:  # pragma: no cover - never called
        raise TsaError("the API does not stamp")

    def credits(self) -> int | None:  # pragma: no cover - never called
        return None


#: Calendar states of a day that is sealed (a failed verification still is).
_SEALED_STATES: Final = ("verified", "sealed", "late")


def _check_seal_now(
    db: Session, channel: Channel, config: Config, layout: Layout, day: date
) -> None:
    """The 409s of "Şimdi mühürle" (contract 2.9); the Seal task checks again."""
    state, _ = read_seal_state(layout, day)
    status = state.get("status") if state else None
    shown = VaultView(layout, config).summary(day)["state"]
    if status == "sealed" or shown in _SEALED_STATES:
        raise admin_error("seal_day_sealed")
    if status in ("skipped", "deleted") or shown in ("no_data", "deleted"):
        raise admin_error("seal_not_applicable")
    due = Sealer(config, provider=_NoStamps(), roots=[]).closed_unsealed_days()
    quiet = timedelta(seconds=config.seal.quiet_seconds)
    if (
        not due
        or due[0] != day
        or shown not in ("failed", "pending")
        or channel.now < day_window(day)[1] + quiet
    ):
        raise admin_error("seal_not_due")
    for row in channel.open_rows(db, kind="action.seal"):
        if (row.params or {}).get("day") == day.isoformat():
            raise admin_error("seal_request_pending")


@router.post("/{day}/seal", status_code=status.HTTP_202_ACCEPTED)
def seal_now(
    day: DayParam,
    request: Request,
    db: DbSession,
    user: ManagerDep,
    layout: LayoutDep,
    config: ConfigDep,
    channel: ChannelDep,
) -> AdminAccepted:
    """ "Şimdi mühürle": one sealing attempt of the oldest due day through the Seal task."""
    _check_day(day)
    _check_seal_now(db, channel, config, layout, day)
    return channel.submit(
        db,
        user,
        request,
        kind="action.seal",
        options={"day": day.isoformat()},
        params={"day": day.isoformat()},
    )
