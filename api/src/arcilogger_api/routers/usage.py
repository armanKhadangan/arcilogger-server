"""``/api/v1/usage`` — Kullanım: company, subjects, sites, categories, exports, rebuilds (spec 10).

Contract: group 2 contract section 2, docs/MVP-CONTRACTS.md section 12. All numbers come from the
rollups of :mod:`arcilogger_worker.usage` and are **estimates** (``estimated: true``).

Access (owner decision 2026-10-20: names for every role that may see usage):

* ``GET /usage/company`` — ``dashboard.usage`` or ``usage.person`` or ``usage.anonymous``
  (company and department totals, no person);
* every other read and the exports — ``usage.person`` or ``usage.anonymous`` and the accepted
  KVKK notice (``require_privacy_notice`` semantics; both permissions require it, so every role
  that sees names accepts it); ``export.*?view=company`` like the company view;
* ``POST /usage/rebuild``, ``GET /usage/rebuild/{job_id}`` — ``settings.manage``; a rebuild may
  not start before the oldest log in the database (422 ``usage_rebuild_no_logs``);
* ``GET /usage/pseudonyms`` — ``users.manage`` (administrators) and the notice.

Pseudonyms only when the setting ``usage.pseudonymize_anonymous_role`` is on, for viewers with
``usage.anonymous`` but without ``usage.person`` (409 ``pseudonym_key_missing`` while the secret
``usage.pseudonym_key`` is not stored).

**Audit** (ids and counts, never names or the search text): ``usage.view`` (the detail of one
subject: spec 8.5 "every person-level usage report viewed"), ``usage.export`` (view, format,
period, rows, filters used), ``usage.rebuild`` (the requested range) and ``usage.pseudonyms``
(the mapping was shown). The target of ``usage.view`` and of a one-subject export is the real
subject key (``usage:person:12``), also for a pseudonymous viewer. The stored entry is never
rewritten; ``GET /audit`` shows such a target to a reader who sees pseudonyms here by the
subject's pseudonym token instead (:func:`audit_target_mask`), or its own views would tie each
pseudonym to a MAC, an IP or a person id.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import date
from typing import Annotated, Final, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, Response
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import ensure_privacy_notice
from arcilogger_api.deps import ConfigDep, DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.licensing import require_module
from arcilogger_api.security import CurrentUser, client_ip, require
from arcilogger_api.usage_exports import (
    Sheet,
    categories_sheets,
    category_sheets,
    company_sheets,
    csv_file,
    site_sheets,
    sites_sheets,
    subject_sheets,
    subjects_sheets,
    xlsx_file,
)
from arcilogger_api.usage_models import (
    CategoryDetail,
    CategoryList,
    CompanyUsage,
    DepartmentPage,
    PseudonymRow,
    RebuildJob,
    RebuildRequest,
    SiteDetail,
    SitePage,
    SubjectDetail,
    SubjectPage,
)
from arcilogger_core.config import Config
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.models import FgTraffic, UsageRebuild
from arcilogger_worker.usage import query
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.pseudonyms import load_key, pseudonym
from arcilogger_worker.usage.query import Context, Period, Viewer

#: Paid module ``usage_analytics`` (licensing contract 1.3): every route answers 403
#: ``module_not_licensed`` while the module is off; nothing of 5651 Temel is here.
router = APIRouter(
    prefix="/usage", tags=["usage"], dependencies=[require_module("usage_analytics")]
)

#: Longest period of one request (days; the database keeps 400).
MAX_DAYS: Final = 400
MAX_OFFSET: Final = 1_000_000
#: Largest page of ``/subjects`` and ``/sites``: the web reads a whole list in one request, so
#: the period is aggregated once, not once per page.
MAX_LIMIT: Final = 100_000
COMPANY_PERMISSIONS: Final = ("dashboard.usage", "usage.person", "usage.anonymous")
PERSON_PERMISSIONS: Final = ("usage.person", "usage.anonymous")
#: Prefix of the audit targets that name a subject (``usage.view``, one-subject ``usage.export``).
AUDIT_TARGET: Final = "usage:"
#: Such a target for a pseudonymous reader of the audit log while the pseudonym key is missing.
MASKED_TARGET: Final = "usage:masked"
SubjectKind = Literal["person", "device", "department"]
SubjectSort = Literal[
    "active_seconds", "online_seconds", "download_bytes", "upload_bytes", "blocked", "label"
]
SiteSort = Literal[
    "seconds", "raw_seconds", "download_bytes", "upload_bytes", "sessions", "users", "domain"
]
Order = Literal["asc", "desc"]
View = Literal["company", "subjects", "subject", "sites", "site", "categories", "category"]

MESSAGES: Final = {
    "usage_period": (
        "Dönem geçersiz: başlangıç bitişten sonra olamaz ve dönem en çok {days} gün olabilir.",
        "Invalid period: the start must not be after the end and a period has at most {days} days.",
    ),
    "usage_subject_not_found": (
        "Bu dönemde bu kişi ya da cihaz için kullanım verisi yok.",
        "There is no usage data for this person or device in this period.",
    ),
    "usage_site_not_found": (
        "Bu dönemde bu site için kullanım verisi yok.",
        "There is no usage data for this site in this period.",
    ),
    "usage_category_not_found": (
        "Bu dönemde bu kategoride kullanım verisi yok.",
        "There is no usage data in this category in this period.",
    ),
    "usage_rebuild_range": (
        "Yeniden hesaplama aralığı geçersiz: bitiş başlangıçtan önce ya da gelecekte olamaz, "
        "aralık en çok {days} gün olabilir.",
        "Invalid rebuild range: the end must not be before the start or in the future, and the "
        "range has at most {days} days.",
    ),
    "usage_rebuild_no_logs": (
        "Veritabanında {first} gününden önceki log yok: yeniden hesaplama en erken bu günden "
        "başlayabilir (daha eski günler boş hesaplanırdı; özetleri olduğu gibi korunur).",
        "The database holds no logs before {first}: a rebuild can start on that day at the "
        "earliest (older days would be computed empty; their summaries are kept as they are).",
    ),
    "usage_rebuild_not_found": ("Yeniden hesaplama işi bulunamadı.", "Rebuild job not found."),
    "pseudonym_key_missing": (
        "Takma adlar için anahtar tanımlı değil: sunucuda `arcilogger-core secret set "
        "usage.pseudonym_key` ile uzun, rastgele bir metin kaydedin.",
        "No key for the pseudonyms: store a long random text on the server with "
        "`arcilogger-core secret set usage.pseudonym_key`.",
    ),
    "usage_export_view": (
        "Bu dışa aktarma görünümü için gereken parametre eksik: {parameter}.",
        "A parameter this export view needs is missing: {parameter}.",
    ),
}


def _error(status: int, code: str, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(status, code, text_tr.format(**params), text_en.format(**params))


# -- access ---------------------------------------------------------------------------------------


def _any_of(*permissions: str, notice: bool) -> Callable[..., CurrentUser]:
    """Dependency: at least one of ``permissions`` (and the KVKK notice with ``notice``)."""
    allowed = require()

    def dependency(db: DbSession, user: CurrentUser = Depends(allowed)) -> CurrentUser:  # noqa: B008
        if not any(user.has(permission) for permission in permissions):
            raise ApiError(403, "forbidden")
        if notice:
            ensure_privacy_notice(db, user)
        return user

    return dependency


CompanyReader = Annotated[CurrentUser, Depends(_any_of(*COMPANY_PERMISSIONS, notice=False))]
UsageReader = Annotated[CurrentUser, Depends(_any_of(*PERSON_PERMISSIONS, notice=True))]
SettingsManager = Annotated[CurrentUser, Depends(require("settings.manage"))]
Administrator = Annotated[CurrentUser, Depends(_any_of("users.manage", notice=True))]


def sees_pseudonyms(db: Session, user: CurrentUser) -> bool:
    """``usage.anonymous`` without ``usage.person`` while ``usage.pseudonymize_anonymous_role``
    is on: the user sees pseudonyms instead of names."""
    if user.has("usage.person") or not user.has("usage.anonymous"):
        return False
    return UsageSettings.load(db).pseudonymize_anonymous_role


def viewer_for(db: Session, user: CurrentUser, config: Config) -> Viewer:
    """Names, or pseudonyms for ``usage.anonymous``-only viewers when the setting asks for it."""
    if not sees_pseudonyms(db, user):
        return Viewer()
    key = load_key(config.secrets_dir)
    if key is None:
        raise _error(409, "pseudonym_key_missing")
    return Viewer(pseudonymize=True, key=key)


def audit_target_mask(
    db: Session, user: CurrentUser, config: Config
) -> Callable[[str], str] | None:
    """How ``GET /audit`` shows ``user`` a target ``usage:<real key>`` (:data:`AUDIT_TARGET`).

    ``None`` (the stored key) for readers who see names; for a reader who sees pseudonyms, the
    subject's pseudonym token as in these pages (``usage:p:3f2a…``), or :data:`MASKED_TARGET`
    while the secret ``usage.pseudonym_key`` is missing. Only the answer changes, never the entry.
    """
    if not sees_pseudonyms(db, user):
        return None
    key = load_key(config.secrets_dir)

    def mask(target: str) -> str:
        if key is None:
            return MASKED_TARGET
        return AUDIT_TARGET + pseudonym(key, target.removeprefix(AUDIT_TARGET))[0]

    return mask


# -- parameters -----------------------------------------------------------------------------------


def period_of(
    from_: Annotated[date | None, Query(alias="from", description="first site day")] = None,
    to: Annotated[date | None, Query(description="last site day (inclusive)")] = None,
    hours: query.Hours = "all",
) -> Period:
    last = to or site_day(utc_now())
    first = from_ or last
    if last < first or (last - first).days >= MAX_DAYS:
        raise _error(422, "usage_period", days=MAX_DAYS)
    return Period(first, last, hours)


PeriodDep = Annotated[Period, Depends(period_of)]
Department = Annotated[
    str | None,
    Query(max_length=200, description="DepartmentRow.department; restricts to its subjects"),
]
Search = Annotated[str | None, Query(max_length=200)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]
Offset = Annotated[int, Query(ge=0, le=MAX_OFFSET)]


def _context(
    db: Session, user: CurrentUser, config: Config, period: Period, department: str | None
) -> Context:
    return Context(db, period, viewer_for(db, user, config), department=department or None)


# -- reads ----------------------------------------------------------------------------------------


@router.get("/company")
def company(db: DbSession, user: CompanyReader, period: PeriodDep) -> CompanyUsage:
    """Company totals, hourly curve, top sites/categories/applications, departments."""
    return CompanyUsage.model_validate(query.company(Context(db, period)))


@router.get("/subjects")
def subjects(
    db: DbSession,
    user: UsageReader,
    config: ConfigDep,
    period: PeriodDep,
    kind: SubjectKind = "person",
    q: Search = None,
    sort: SubjectSort = "active_seconds",
    order: Order = "desc",
    limit: Limit = 50,
    offset: Offset = 0,
    department: Department = None,
) -> SubjectPage | DepartmentPage:
    """Persons, devices (with bare addresses) or departments of the period, one page."""
    ctx = _context(db, user, config, period, department)
    data = query.subjects_page(
        ctx, kind=kind, q=q, sort=sort, order=order, limit=limit, offset=offset
    )
    if kind == "department":
        return DepartmentPage.model_validate(data)
    return SubjectPage.model_validate(data)


@router.get("/subjects/{key}")
def subject(
    request: Request,
    db: DbSession,
    user: UsageReader,
    config: ConfigDep,
    period: PeriodDep,
    key: Annotated[str, Path(max_length=120)],
) -> SubjectDetail:
    """Everything about one person, device or address in the period."""
    ctx = _context(db, user, config, period, None)
    try:
        real = query.find_subject(ctx, key)
    except query.NotFoundError:
        raise _error(404, "usage_subject_not_found") from None
    data = query.subject_detail(ctx, real)
    record_audit(
        db,
        actor=user,
        action="usage.view",
        target=f"{AUDIT_TARGET}{real}",
        details={**period.to_json(), "pseudonymous": ctx.viewer.pseudonymize},
        ip=client_ip(request),
    )
    return SubjectDetail.model_validate(data)


@router.get("/sites")
def sites(
    db: DbSession,
    user: UsageReader,
    config: ConfigDep,
    period: PeriodDep,
    q: Search = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    sort: SiteSort = "seconds",
    order: Order = "desc",
    limit: Limit = 50,
    offset: Offset = 0,
    department: Department = None,
) -> SitePage:
    """Sites (registrable domains, services, addresses), one page."""
    ctx = _context(db, user, config, period, department)
    return SitePage.model_validate(
        query.sites_page(
            ctx, q=q, category=category, sort=sort, order=order, limit=limit, offset=offset
        )
    )


@router.get("/sites/{domain}")
def site(
    db: DbSession,
    user: UsageReader,
    config: ConfigDep,
    period: PeriodDep,
    domain: Annotated[str, Path(max_length=255)],
    department: Department = None,
) -> SiteDetail:
    """One site: totals, host names, who used it (their time on this site)."""
    ctx = _context(db, user, config, period, department)
    data = query.site_detail(ctx, domain)
    if data is None:
        raise _error(404, "usage_site_not_found")
    return SiteDetail.model_validate(data)


@router.get("/categories")
def categories(
    db: DbSession,
    user: UsageReader,
    config: ConfigDep,
    period: PeriodDep,
    department: Department = None,
) -> CategoryList:
    """Every category of the period (``Bilinmiyor``: domains without a FortiGuard category)."""
    ctx = _context(db, user, config, period, department)
    return CategoryList.model_validate(query.categories_list(ctx))


@router.get("/categories/{category:path}")
def category(
    db: DbSession,
    user: UsageReader,
    config: ConfigDep,
    period: PeriodDep,
    category: Annotated[str, Path(max_length=200)],
    department: Department = None,
) -> CategoryDetail:
    """One category: totals, its sites, who used it (their time in this category)."""
    ctx = _context(db, user, config, period, department)
    data = query.category_detail(ctx, category)
    if data is None:
        raise _error(404, "usage_category_not_found")
    return CategoryDetail.model_validate(data)


# -- exports --------------------------------------------------------------------------------------


def _export(
    request: Request,
    db: Session,
    user: CurrentUser,
    config: Config,
    period: Period,
    *,
    view: View,
    kind: SubjectKind,
    key: str | None,
    domain: str | None,
    category: str | None,
    q: str | None,
    sort: str,
    order: Order,
    department: str | None,
    file_format: Literal["csv", "xlsx"],
) -> Response:
    if view != "company":
        if not any(user.has(p) for p in PERSON_PERMISSIONS):
            raise ApiError(403, "forbidden")
        ensure_privacy_notice(db, user)
    # The company view names nobody: no pseudonym key is needed for it.
    ctx = (
        Context(db, period) if view == "company" else _context(db, user, config, period, department)
    )
    sheets: list[Sheet]
    target = None
    if view == "company":
        sheets = company_sheets(query.company(Context(db, period)))
    elif view == "subjects":
        subject_sort = sort if sort in query.SUBJECT_SORTS else "active_seconds"
        data = query.subjects_page(
            ctx, kind=kind, q=q, sort=subject_sort, order=order, limit=MAX_OFFSET, offset=0
        )
        sheets = subjects_sheets(data["items"], departments=kind == "department")
    elif view == "subject":
        if not key:
            raise _error(422, "usage_export_view", parameter="key")
        try:
            real = query.find_subject(ctx, key)
        except query.NotFoundError:
            raise _error(404, "usage_subject_not_found") from None
        data = query.subject_detail(ctx, real)
        target = f"{AUDIT_TARGET}{real}"
        sheets = subject_sheets(data)
    elif view == "sites":
        site_sort = sort if sort in query.SITE_SORTS else "seconds"
        data = query.sites_page(
            ctx, q=q, category=category, sort=site_sort, order=order, limit=MAX_OFFSET, offset=0
        )
        sheets = sites_sheets(data["items"])
    elif view == "site":
        if not domain:
            raise _error(422, "usage_export_view", parameter="domain")
        found = query.site_detail(ctx, domain, capped=False)
        if found is None:
            raise _error(404, "usage_site_not_found")
        sheets = site_sheets(found)
    elif view == "categories":
        sheets = categories_sheets(query.categories_list(ctx)["items"])
    else:
        if not category:
            raise _error(422, "usage_export_view", parameter="category")
        found = query.category_detail(ctx, category, capped=False)
        if found is None:
            raise _error(404, "usage_category_not_found")
        sheets = category_sheets(found)
    filters = {
        name: True
        for name, value in (
            ("q", q),
            ("department", department),
            ("domain", domain),
            ("category", category),
        )
        if value
    }
    record_audit(
        db,
        actor=user,
        action="usage.export",
        target=target,
        details={
            "view": view,
            "format": file_format,
            **period.to_json(),
            "rows": sum(len(sheet.rows) for sheet in sheets),
            "filters": filters,
            "pseudonymous": ctx.viewer.pseudonymize,
            **({"kind": kind} if view == "subjects" else {}),
        },
        ip=client_ip(request),
    )
    name = f"arcilogger-kullanim-{view}-{period.first:%Y%m%d}-{period.last:%Y%m%d}.{file_format}"
    return csv_file(sheets, name) if file_format == "csv" else xlsx_file(sheets, name)


ExportView = Annotated[View, Query(description="the view whose rows are exported")]


@router.get("/export.csv", response_class=Response, responses={200: {"content": {"text/csv": {}}}})
def export_csv(
    request: Request,
    db: DbSession,
    user: CompanyReader,
    config: ConfigDep,
    period: PeriodDep,
    view: ExportView,
    kind: SubjectKind = "person",
    key: Annotated[str | None, Query(max_length=120)] = None,
    domain: Annotated[str | None, Query(max_length=255)] = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    q: Search = None,
    sort: Annotated[str, Query(max_length=40)] = "active_seconds",
    order: Order = "desc",
    department: Department = None,
) -> Response:
    """The rows of ``view`` as CSV (the main table; audited ``usage.export``)."""
    return _export(
        request, db, user, config, period, view=view, kind=kind, key=key, domain=domain,
        category=category, q=q, sort=sort, order=order, department=department,
        file_format="csv",
    )  # fmt: skip


@router.get(
    "/export.xlsx",
    response_class=Response,
    responses={200: {"content": {"application/vnd.openxmlformats-officedocument."
                                 "spreadsheetml.sheet": {}}}},
)  # fmt: skip
def export_xlsx(
    request: Request,
    db: DbSession,
    user: CompanyReader,
    config: ConfigDep,
    period: PeriodDep,
    view: ExportView,
    kind: SubjectKind = "person",
    key: Annotated[str | None, Query(max_length=120)] = None,
    domain: Annotated[str | None, Query(max_length=255)] = None,
    category: Annotated[str | None, Query(max_length=200)] = None,
    q: Search = None,
    sort: Annotated[str, Query(max_length=40)] = "active_seconds",
    order: Order = "desc",
    department: Department = None,
) -> Response:
    """The view as an Excel workbook (one sheet per section; audited ``usage.export``)."""
    return _export(
        request, db, user, config, period, view=view, kind=kind, key=key, domain=domain,
        category=category, q=q, sort=sort, order=order, department=department,
        file_format="xlsx",
    )  # fmt: skip


# -- rebuilds -------------------------------------------------------------------------------------


def _job(row: UsageRebuild) -> RebuildJob:
    return RebuildJob.model_validate(
        {
            "job_id": row.id,
            "from": row.from_day,
            "to": row.to_day,
            "status": row.status,
            "progress": round(row.done_days / row.total_days, 4) if row.total_days else 1.0,
            "error": row.error,
            "requested_by": row.requested_by,
            "created_at": row.created_at,
            "started_at": row.started_at,
            "finished_at": row.finished_at,
        }
    )


@router.post("/rebuild", status_code=202)
def request_rebuild(
    body: RebuildRequest, request: Request, db: DbSession, user: SettingsManager
) -> RebuildJob:
    """Queue a rebuild of the rollups of ``from`` … ``to`` (the worker runs it)."""
    today = site_day(utc_now())
    if body.to < body.from_ or body.to > today or (body.to - body.from_).days >= MAX_DAYS:
        raise _error(422, "usage_rebuild_range", days=MAX_DAYS)
    # A rebuild replaces a day's rollups: without the day's logs it would replace them with an
    # empty day (rollups and logs are kept independently: usage.retention_days, db.retention_days).
    # One index-only step per monthly partition.
    oldest = db.scalar(select(func.min(FgTraffic.time)))
    first = site_day(oldest) if oldest is not None else today
    if body.from_ < first:
        raise _error(422, "usage_rebuild_no_logs", first=f"{first:%d.%m.%Y}")
    row = UsageRebuild(
        from_day=body.from_,
        to_day=body.to,
        status="queued",
        total_days=(body.to - body.from_).days + 1,
        done_days=0,
        requested_by=user.username,
        created_at=utc_now(),
    )
    db.add(row)
    db.flush()
    record_audit(
        db,
        actor=user,
        action="usage.rebuild",
        target=f"usage_rebuild:{row.id}",
        details={"from": body.from_.isoformat(), "to": body.to.isoformat()},
        ip=client_ip(request),
    )
    return _job(row)


@router.get("/rebuild/{job_id}")
def rebuild_status(
    db: DbSession, user: SettingsManager, job_id: Annotated[int, Path(ge=1, le=2**63 - 1)]
) -> RebuildJob:
    row = db.get(UsageRebuild, job_id)
    if row is None:
        raise _error(404, "usage_rebuild_not_found")
    return _job(row)


# -- pseudonyms -----------------------------------------------------------------------------------


@router.get("/pseudonyms")
def pseudonyms(
    request: Request, db: DbSession, user: Administrator, config: ConfigDep, q: Search = None
) -> list[PseudonymRow]:
    """Pseudonym → real subject (administrators; audited ``usage.pseudonyms``)."""
    key = load_key(config.secrets_dir)
    if key is None:
        raise _error(409, "pseudonym_key_missing")
    rows = db.execute(
        text(
            "SELECT subject, min(kind), mode() WITHIN GROUP (ORDER BY vlan), "
            "mode() WITHIN GROUP (ORDER BY host(ip)), max(mac::text) FROM usage_daily "
            "GROUP BY subject"
        )
    )
    hints = {
        subject: {"kind": kind, "vlan": vlan, "ip": ip, "mac": mac}
        for subject, kind, vlan, ip, mac in rows
    }
    infos = query.Directory(db).resolve(hints)
    needle = query.fold(q.strip()) if q and q.strip() else None
    found = []
    for subject_key, info in sorted(infos.items()):
        if info.excluded:  # never shown anywhere, so it has no pseudonym to explain
            continue
        token, label = pseudonym(key, subject_key)
        ref = query.subject_ref(info, Viewer())
        texts = [label, token, info.label, info.mac or "", info.ip or ""]
        if needle is not None and not any(needle in query.fold(t) for t in texts):
            continue
        found.append(PseudonymRow(pseudonym=label, kind=info.kind, subject=ref))
    found.sort(key=lambda row: (row.pseudonym, row.subject.key))
    record_audit(
        db,
        actor=user,
        action="usage.pseudonyms",
        details={"rows": len(found), "q": needle is not None},
        ip=client_ip(request),
    )
    return found
