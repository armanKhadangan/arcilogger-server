"""``/api/v1/reports`` — Raporlar: scheduled usage reports (spec 10.5, 12.3 page 12, ADR-0012).

Contract: group 2 contract section 4 / docs/MVP-CONTRACTS.md section 13. Permission
``reports.manage`` (administrators); endpoints that hand out person-level report content —
``POST …/run``, a file download, the preview — also need the accepted KVKK notice.

* ``GET/POST /reports/schedules``, ``PATCH/DELETE /reports/schedules/{id}`` — the schedules.
  Recipients are checked and normalised like ``alerts.email_recipients``; ``time`` is ``HH:MM``
  (site time, not 00:00 for ``end_of_day``: that period would be empty); ``weekday`` (1 = Monday
  … 7) only for ``weekly``, ``monthday`` (1-28) only for ``monthly``; a schedule needs at least
  one format, and recipients before it can be enabled. ``next_run_at`` is recomputed when
  ``kind``, ``time``, ``weekday``, ``monthday`` or ``enabled`` change or the recipients become
  (no longer) empty; other edits keep a passed occurrence the worker has not run yet. Deleting
  keeps the runs and their files.
* ``POST /reports/schedules/{id}/run`` — "run now": queues a run (202) that the worker job
  ``reports.run_due`` renders and sends within a minute. Without ``from``/``to`` it covers the
  period the schedule would cover now; with them it is a ``custom`` run (at most
  :data:`MAX_PERIOD_DAYS` days, the end not after now). ``send: false`` only makes the files.
* ``GET /reports/runs``, ``GET /reports/runs/{id}``, ``GET /reports/runs/{id}/files/{name}`` —
  the run history with per-recipient delivery state, and the stored files (checked against their
  SHA-256, ``X-Arci-Sha256``).
* ``GET /reports/preview`` — renders a report now (PDF or HTML), stores and sends nothing.

Pseudonyms need the secret ``usage.pseudonym_key`` (optional, not set by default): a preview with
``pseudonymize``, switching a schedule's ``pseudonymize`` on and "run now" of such a schedule
answer 409 ``pseudonym_key_missing`` while it is not stored (the usage pages' error).

**Audit** (never the recipients' addresses, only their number): ``report.schedule.create``,
``report.schedule.update`` (fields changed; old/new of ``enabled``, ``person_level``,
``pseudonymize``), ``report.schedule.delete``, ``report.run``, ``report.download`` (file,
SHA-256), ``report.preview``.

A delivery waiting for its next attempt is shown as ``failed`` with the last error while the run
is still ``running``; a delivery not tried yet is not listed (the contract has no "queued").
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.auth.schemas import StrictModel
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.directory import Name
from arcilogger_api.errors import ApiError
from arcilogger_api.licensing import require_module
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_core.config import Config
from arcilogger_core.timeutil import SITE_TZ, utc_now
from arcilogger_db.models import ReportRun, ReportSchedule
from arcilogger_db.settings import email_addresses, get_setting
from arcilogger_worker.alerts.notify import web_base_url
from arcilogger_worker.reports import store
from arcilogger_worker.reports.content import ReportMeta, build_document
from arcilogger_worker.reports.html import render_html
from arcilogger_worker.reports.pdf import render_pdf
from arcilogger_worker.reports.runner import next_run, queue_run
from arcilogger_worker.reports.schedule import TIME_PATTERN, Timing, TimingError, period_for
from arcilogger_worker.reports.texts import KIND_NAMES
from arcilogger_worker.usage.pseudonyms import load_key

#: Paid module ``usage_reports`` (licensing contract 1.3): every route answers 403
#: ``module_not_licensed`` while the module is off; nothing of 5651 Temel is here.
router = APIRouter(
    prefix="/reports", tags=["reports"], dependencies=[require_module("usage_reports")]
)

ManagerDep = Annotated[CurrentUser, Depends(require("reports.manage"))]
PersonDataDep = Annotated[CurrentUser, Depends(require_privacy_notice("reports.manage"))]
MAX_ID: Final = 2**63 - 1
MAX_OFFSET: Final = 1_000_000
#: Most recipients of one schedule.
MAX_RECIPIENTS: Final = 50
#: Longest period of a custom run or a preview.
MAX_PERIOD_DAYS: Final = 92
ScheduleId = Annotated[int, Path(ge=1, le=MAX_ID, description="Schedule number")]
RunId = Annotated[int, Path(ge=1, le=MAX_ID, description="Run number")]
FileName = Annotated[str, Path(pattern=r"^[A-Za-z0-9._-]{1,200}$", description="File name")]

ScheduleKind = Literal["end_of_day", "full_day", "weekly", "monthly"]
RunKind = Literal["end_of_day", "full_day", "weekly", "monthly", "custom"]
RunStatus = Literal["queued", "running", "sent", "partial", "failed"]
FileFormat = Literal["pdf", "xlsx", "html"]
DeliveryStatus = Literal["sent", "failed", "skipped"]
Clock = Annotated[str, Field(pattern=TIME_PATTERN.pattern, description="HH:MM, site time")]
Weekday = Annotated[int, Field(ge=1, le=7, description="1 = Monday … 7 = Sunday")]
Monthday = Annotated[int, Field(ge=1, le=28)]

MESSAGES: Final = {
    "report_recipients_required": (
        "Bu işlem için en az bir alıcı gerekiyor (zamanlamayı açmak ya da raporu göndermek "
        "için). Yalnız dosya oluşturmak için send=false kullanın.",
        "At least one recipient is needed (to enable the schedule or to send the report). Use "
        "send=false to make the files only.",
    ),
    "report_smtp_disabled": (
        "E-posta gönderimi kapalı ([smtp] enabled = false). Raporu yalnız dosya olarak "
        "oluşturmak için send=false kullanın.",
        "E-mail is switched off ([smtp] enabled = false). Use send=false to make the files only.",
    ),
    "report_expired": (
        "Bu raporun dosyaları saklama süresi (reports.keep_days) dolduğu için silindi.",
        "The files of this report were deleted after reports.keep_days.",
    ),
    "report_missing": (
        "Rapor dosyası bulunamadı (exports/usage-reports). Raporu yeniden çalıştırın.",
        "The report file is missing (exports/usage-reports). Run the report again.",
    ),
    "report_damaged": (
        "Rapor dosyası kayıtlı SHA-256 özetiyle uyuşmuyor (değiştirilmiş olabilir).",
        "The report file does not match its recorded SHA-256 (it may have been changed).",
    ),
    "usage_engine_unavailable": (
        "Kullanım verileri henüz hesaplanamıyor (kullanım motoru yok).",
        "Usage data cannot be computed yet (the usage engine is not installed).",
    ),
    # The text of the usage pages (arcilogger_api.routers.usage).
    "pseudonym_key_missing": (
        "Takma adlar için anahtar tanımlı değil: sunucuda `arcilogger-core secret set "
        "usage.pseudonym_key` ile uzun, rastgele bir metin kaydedin.",
        "No key for the pseudonyms: store a long random text on the server with "
        "`arcilogger-core secret set usage.pseudonym_key`.",
    ),
}
FILE_ERRORS: Final = {
    "not_found": 404,
    "report_expired": 410,
    "report_missing": 409,
    "report_damaged": 409,
}


def _error(status_code: int, code: str) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(status_code, code, text_tr, text_en)


def _pseudonym_key(config: Config) -> bytes:
    """``usage.pseudonym_key`` for a report with pseudonyms; 409 while it is not stored (the
    report would fail, now in the preview or later in the worker)."""
    key = load_key(config.secrets_dir)
    if key is None:
        raise _error(409, "pseudonym_key_missing")
    return key


def _invalid(field: str, message: str, *, where: str = "body") -> ApiError:
    return ApiError(
        422,
        "validation_error",
        details=[{"loc": [where, field], "type": "value_error", "msg": message}],
    )


# -- schemas --------------------------------------------------------------------------------------


class ReportFormats(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email_html: bool
    pdf: bool
    xlsx: bool


class ReportPeriod(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    from_: datetime = Field(alias="from")
    to: datetime


class ReportFile(BaseModel):
    name: str
    format: FileFormat
    size: int
    sha256: str


class ReportDelivery(BaseModel):
    recipient: str
    status: DeliveryStatus
    error: str | None
    sent_at: datetime | None


class RunOut(BaseModel):
    id: int
    schedule_id: int | None
    kind: RunKind
    period: ReportPeriod
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None
    error: str | None
    files: list[ReportFile]
    deliveries: list[ReportDelivery]
    triggered_by: Literal["schedule", "manual"]
    requested_by: str | None


class RunPage(BaseModel):
    items: list[RunOut]
    total: int
    limit: int
    offset: int


class ScheduleOut(BaseModel):
    id: int
    name: str
    kind: ScheduleKind
    enabled: bool
    time: str
    weekday: int | None
    monthday: int | None
    recipients: list[str]
    formats: ReportFormats
    person_level: bool
    pseudonymize: bool
    created_at: datetime
    updated_at: datetime
    updated_by: str
    next_run_at: datetime | None
    last_run: RunOut | None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _deliveries(run: ReportRun) -> list[ReportDelivery]:
    shown = []
    for item in run.deliveries or []:
        state = item.get("status")
        if state == "queued" and not item.get("attempts"):
            continue  # not tried yet
        shown.append(
            ReportDelivery(
                recipient=str(item.get("recipient")),
                status=state if state in ("sent", "skipped") else "failed",
                error=item.get("error"),
                sent_at=_moment(item.get("sent_at")),
            )
        )
    return shown


def run_out(run: ReportRun) -> RunOut:
    return RunOut(
        id=run.id,
        schedule_id=run.schedule_id,
        kind=run.kind,
        period=ReportPeriod(from_=run.period_from, to=run.period_to),
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        error=run.error,
        files=[
            ReportFile(**{k: item[k] for k in ("name", "format", "size", "sha256")})
            for item in run.files or []
        ],
        deliveries=_deliveries(run),
        triggered_by=run.triggered_by,
        requested_by=run.requested_by,
    )


def _last_runs(db: Session, schedule_ids: list[int]) -> dict[int, ReportRun]:
    if not schedule_ids:
        return {}
    rows = db.scalars(
        select(ReportRun)
        .where(ReportRun.schedule_id.in_(schedule_ids))
        .order_by(ReportRun.schedule_id, ReportRun.id.desc())
        .ext(distinct_on(ReportRun.schedule_id))
    )
    return {row.schedule_id: row for row in rows if row.schedule_id is not None}


def schedule_outs(db: Session, rows: list[ReportSchedule]) -> list[ScheduleOut]:
    last = _last_runs(db, [row.id for row in rows])
    return [
        ScheduleOut(
            id=row.id,
            name=row.name,
            kind=row.kind,
            enabled=row.enabled,
            time=row.time,
            weekday=row.weekday,
            monthday=row.monthday,
            recipients=list(row.recipients or []),
            formats=ReportFormats(
                email_html=bool((row.formats or {}).get("email_html")),
                pdf=bool((row.formats or {}).get("pdf")),
                xlsx=bool((row.formats or {}).get("xlsx")),
            ),
            person_level=row.person_level,
            pseudonymize=row.pseudonymize,
            created_at=row.created_at,
            updated_at=row.updated_at,
            updated_by=row.updated_by,
            next_run_at=row.next_run_at,
            last_run=run_out(last[row.id]) if row.id in last else None,
        )
        for row in rows
    ]


# -- schedules ------------------------------------------------------------------------------------


class ScheduleCreate(StrictModel):
    name: Name
    kind: ScheduleKind
    enabled: bool = False
    time: Clock
    weekday: Weekday | None = None
    monthday: Monthday | None = None
    recipients: list[str] = Field(max_length=MAX_RECIPIENTS)
    formats: ReportFormats
    person_level: bool = True
    pseudonymize: bool = False


class ScheduleUpdate(StrictModel):
    """Only the fields sent are changed. When ``kind`` changes, a ``weekday``/``monthday`` the
    new kind does not use is cleared."""

    name: Name | None = None
    kind: ScheduleKind | None = None
    enabled: bool | None = None
    time: Clock | None = None
    weekday: Weekday | None = None
    monthday: Monthday | None = None
    recipients: list[str] | None = Field(default=None, max_length=MAX_RECIPIENTS)
    formats: ReportFormats | None = None
    person_level: bool | None = None
    pseudonymize: bool | None = None


#: Fields of a schedule in the order of the API and the audit entries.
FIELDS: Final = (
    "name",
    "kind",
    "enabled",
    "time",
    "weekday",
    "monthday",
    "recipients",
    "formats",
    "person_level",
    "pseudonymize",
)


#: Fields a PATCH may not set to ``null`` (only ``weekday`` / ``monthday`` may be cleared).
REQUIRED: Final = tuple(field for field in FIELDS if field not in ("weekday", "monthday"))
#: Fields that decide when a schedule runs: only a change of these moves ``next_run_at``.
TIMING_FIELDS: Final = ("kind", "enabled", "time", "weekday", "monthday")


def _checked(values: dict[str, Any]) -> dict[str, Any]:
    """The complete field values of a schedule, checked together (422 otherwise)."""
    try:
        values["recipients"] = email_addresses(
            "recipients", values["recipients"], limit=MAX_RECIPIENTS
        )
    except ValueError as exc:
        raise _invalid("recipients", str(exc)) from None
    try:
        Timing(values["kind"], values["time"], values["weekday"], values["monthday"])
    except TimingError as exc:
        raise _invalid(exc.field, str(exc)) from None
    if not any(values["formats"].values()):
        raise _invalid("formats", "at least one format (email_html, pdf, xlsx) is required")
    if values["enabled"] and not values["recipients"]:
        raise _error(422, "report_recipients_required")
    return values


def _schedule(db: Session, schedule_id: int, *, lock: bool = False) -> ReportSchedule:
    row = db.get(ReportSchedule, schedule_id, with_for_update=lock)
    if row is None:
        raise ApiError(404, "not_found")
    return row


def _audit_fields(values: dict[str, Any]) -> dict[str, Any]:
    """What the audit log may keep of a schedule (addresses only as a count)."""
    return {
        "name": values["name"],
        "kind": values["kind"],
        "enabled": values["enabled"],
        "time": values["time"],
        "weekday": values["weekday"],
        "monthday": values["monthday"],
        "recipients": len(values["recipients"]),
        "formats": dict(values["formats"]),
        "person_level": values["person_level"],
        "pseudonymize": values["pseudonymize"],
    }


@router.get("/schedules")
def list_schedules(db: DbSession, user: ManagerDep) -> list[ScheduleOut]:
    """Every schedule (the shipped ones first), with its last run."""
    rows = list(db.scalars(select(ReportSchedule).order_by(ReportSchedule.id)))
    return schedule_outs(db, rows)


@router.post("/schedules", status_code=status.HTTP_201_CREATED)
def create_schedule(
    body: ScheduleCreate, request: Request, db: DbSession, config: ConfigDep, user: ManagerDep
) -> ScheduleOut:
    values = _checked(body.model_dump())
    if values["pseudonymize"]:
        _pseudonym_key(config)
    row = ReportSchedule(**values, updated_by=user.username)
    row.next_run_at = next_run(row, utc_now())
    db.add(row)
    db.flush()
    db.refresh(row)
    record_audit(
        db,
        actor=user,
        action="report.schedule.create",
        target=f"report_schedule:{row.id}",
        details={"schedule_id": row.id, **_audit_fields(values)},
        ip=client_ip(request),
    )
    (out,) = schedule_outs(db, [row])
    return out


@router.patch("/schedules/{schedule_id}")
def update_schedule(
    schedule_id: ScheduleId,
    body: ScheduleUpdate,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    user: ManagerDep,
) -> ScheduleOut:
    row = _schedule(db, schedule_id, lock=True)
    sent = body.model_fields_set
    for field in REQUIRED:
        if field in sent and getattr(body, field) is None:
            raise _invalid(field, "must not be null")
    old = {field: getattr(row, field) for field in FIELDS}
    old["formats"] = dict(old["formats"] or {})
    old["recipients"] = list(old["recipients"] or [])
    new = dict(old)
    for field in FIELDS:
        if field in sent:
            value = getattr(body, field)
            new[field] = value.model_dump() if isinstance(value, ReportFormats) else value
    if "kind" in sent:
        if new["kind"] != "weekly" and "weekday" not in sent:
            new["weekday"] = None
        if new["kind"] != "monthly" and "monthday" not in sent:
            new["monthday"] = None
    new = _checked(new)
    changed = [field for field in FIELDS if new[field] != old[field]]
    if "pseudonymize" in changed and new["pseudonymize"]:
        _pseudonym_key(config)
    if changed:
        for field in changed:
            setattr(row, field, new[field])
        row.updated_by = user.username
        if (
            any(field in changed for field in TIMING_FIELDS)
            or bool(old["recipients"]) != bool(new["recipients"])
            or row.next_run_at is None
        ):
            row.next_run_at = next_run(row, utc_now())
        # Otherwise an occurrence that passed while the worker was not running stays due: it
        # still runs once (``reports.catch_up``), a name or recipient edit does not drop it.
        db.flush()
        db.refresh(row)
        details: dict[str, Any] = {"schedule_id": row.id, "name": row.name, "changed": changed}
        for field in ("enabled", "person_level", "pseudonymize"):
            if field in changed:
                details[field] = {"old": old[field], "new": new[field]}
        if "recipients" in changed:
            details["recipients"] = {"old": len(old["recipients"]), "new": len(new["recipients"])}
        record_audit(
            db,
            actor=user,
            action="report.schedule.update",
            target=f"report_schedule:{row.id}",
            details=details,
            ip=client_ip(request),
        )
    (out,) = schedule_outs(db, [row])
    return out


@router.delete("/schedules/{schedule_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_schedule(
    schedule_id: ScheduleId, request: Request, db: DbSession, user: ManagerDep
) -> Response:
    """Delete a schedule; its runs and their files are kept (``schedule_id`` becomes null)."""
    row = _schedule(db, schedule_id, lock=True)
    runs = db.scalar(
        select(func.count()).select_from(ReportRun).where(ReportRun.schedule_id == row.id)
    )
    details = {"schedule_id": row.id, "name": row.name, "kind": row.kind, "runs": runs or 0}
    db.delete(row)
    db.flush()
    record_audit(
        db,
        actor=user,
        action="report.schedule.delete",
        target=f"report_schedule:{schedule_id}",
        details=details,
        ip=client_ip(request),
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# -- runs -----------------------------------------------------------------------------------------


class RunRequest(StrictModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: datetime | None = Field(default=None, alias="from")
    to: datetime | None = None
    send: bool = True


def _site(value: datetime) -> datetime:
    """A time without an offset is site time (Europe/Istanbul)."""
    return value if value.tzinfo is not None else value.replace(tzinfo=SITE_TZ)


def _period(
    kind: str, start: datetime | None, end: datetime | None, now: datetime, *, where: str
) -> tuple[str, datetime, datetime]:
    """``(run kind, start, end)``: the kind's own period now, or ``custom`` from/to."""
    if start is None and end is None:
        if kind == "custom":
            raise _invalid("from", "a custom report needs from and to", where=where)
        first, last = period_for(kind, now)
        return kind, first, last
    if start is None or end is None:
        missing = "from" if start is None else "to"
        raise _invalid(missing, "give both from and to, or neither", where=where)
    first, last = _site(start), min(_site(end), now)
    if last <= first:
        raise _invalid("to", "to must be after from (and not after now)", where=where)
    if last - first > timedelta(days=MAX_PERIOD_DAYS):
        raise _invalid("from", f"the period may cover at most {MAX_PERIOD_DAYS} days", where=where)
    return "custom", first, last


@router.post("/schedules/{schedule_id}/run", status_code=status.HTTP_202_ACCEPTED)
def run_schedule(
    schedule_id: ScheduleId,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    user: PersonDataDep,
    body: RunRequest | None = None,
) -> RunOut:
    """ "Run now": a queued run the worker renders (and sends) within a minute."""
    order = body or RunRequest()
    row = _schedule(db, schedule_id)
    now = utc_now()
    kind, start, end = _period(row.kind, order.from_, order.to, now, where="body")
    if order.send and not row.recipients:
        raise _error(422, "report_recipients_required")
    if order.send and not config.smtp.enabled:
        raise _error(409, "report_smtp_disabled")
    if row.pseudonymize:
        _pseudonym_key(config)
    run = queue_run(
        db,
        row,
        start=start,
        end=end,
        kind=kind,
        triggered_by="manual",
        requested_by=user.username,
        send=order.send,
        now=now,
    )
    db.refresh(run)
    record_audit(
        db,
        actor=user,
        action="report.run",
        target=f"report_run:{run.id}",
        details={
            "run_id": run.id,
            "schedule_id": row.id,
            "kind": kind,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "send": order.send,
            "recipients": len(row.recipients or []) if order.send else 0,
            "person_level": row.person_level,
            "pseudonymize": row.pseudonymize,
        },
        ip=client_ip(request),
    )
    return run_out(run)


@router.get("/runs")
def list_runs(
    db: DbSession,
    user: ManagerDep,
    schedule_id: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    status_: Annotated[RunStatus | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> RunPage:
    """Runs, newest first."""
    query = select(ReportRun)
    if schedule_id is not None:
        query = query.where(ReportRun.schedule_id == schedule_id)
    if status_ is not None:
        query = query.where(ReportRun.status == status_)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(ReportRun.id.desc()).limit(limit).offset(offset))
    return RunPage(items=[run_out(row) for row in rows], total=total, limit=limit, offset=offset)


def _run(db: Session, run_id: int) -> ReportRun:
    row = db.get(ReportRun, run_id)
    if row is None:
        raise ApiError(404, "not_found")
    return row


@router.get("/runs/{run_id}")
def get_run(run_id: RunId, db: DbSession, user: ManagerDep) -> RunOut:
    return run_out(_run(db, run_id))


@router.get(
    "/runs/{run_id}/files/{name}",
    response_class=Response,
    responses={
        200: {
            "content": {
                "application/pdf": {},
                store.MEDIA_TYPES["xlsx"]: {},
                "text/html": {},
            }
        }
    },
)
def download_file(
    run_id: RunId,
    name: FileName,
    request: Request,
    db: DbSession,
    layout: LayoutDep,
    user: PersonDataDep,
) -> Response:
    """A stored file of a run, the bytes checked against its SHA-256."""
    run = _run(db, run_id)
    try:
        entry, data = store.read_file(layout, run, name)
    except store.StoredFileError as exc:
        if exc.code == "not_found":
            raise ApiError(404, "not_found") from None
        raise _error(FILE_ERRORS[exc.code], exc.code) from None
    record_audit(
        db,
        actor=user,
        action="report.download",
        target=f"report_run:{run.id}",
        details={
            "run_id": run.id,
            "file": entry["name"],
            "format": entry["format"],
            "sha256": entry["sha256"],
            "size": entry["size"],
        },
        ip=client_ip(request),
    )
    return Response(
        content=data,
        media_type=store.MEDIA_TYPES[str(entry["format"])],
        headers={
            "Content-Disposition": f'attachment; filename="{entry["name"]}"',
            "X-Arci-Sha256": str(entry["sha256"]),
            "Cache-Control": "no-store",
        },
    )


# -- preview --------------------------------------------------------------------------------------


@router.get(
    "/preview",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}, "text/html": {}}}},
)
def preview(
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    user: PersonDataDep,
    kind: RunKind,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: datetime | None = None,
    format_: Annotated[Literal["pdf", "html"], Query(alias="format")] = "html",
    person_level: bool = True,
    pseudonymize: bool = False,
) -> Response:
    """The report as it would be made now: rendered, not stored, not sent."""
    now = utc_now()
    run_kind, start, end = _period(kind, from_, to, now, where="query")
    key = _pseudonym_key(config) if pseudonymize else None
    meta = ReportMeta(
        name=KIND_NAMES[kind][0],
        kind=run_kind,
        site_name=config.site_name,
        web_url=web_base_url(get_setting(db, "alerts.web_base_url"), config),
        generated_at=now,
        preview=True,
    )
    try:
        document = build_document(
            db,
            layout,
            meta=meta,
            start=start,
            end=end,
            person_level=person_level,
            pseudonymize=pseudonymize,
            pseudonym_key=key,
        )
    except NotImplementedError:
        raise _error(503, "usage_engine_unavailable") from None
    record_audit(
        db,
        actor=user,
        action="report.preview",
        details={
            "kind": run_kind,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "format": format_,
            "person_level": person_level,
            "pseudonymize": pseudonymize,
        },
        ip=client_ip(request),
    )
    if format_ == "pdf":
        return Response(
            content=render_pdf(document).pdf,
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'inline; filename="ARCILOGGER-kullanim-onizleme.pdf"',
                "Cache-Control": "no-store",
            },
        )
    return Response(
        content=render_html(document),
        media_type="text/html; charset=utf-8",
        headers={
            "Cache-Control": "no-store",
            # A report, not a page of the app: shown in a sandbox, nothing loaded or run.
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; sandbox",
        },
    )
