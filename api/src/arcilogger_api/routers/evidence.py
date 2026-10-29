"""``/api/v1/evidence`` — Kanıt paketleri: oluştur, listele, indir (spec 9.6, 12.3 page 10).

* ``POST /evidence`` (``evidence.create`` + re-authentication within 5 minutes) — scope either
  from a stored 5651 lookup (``lookup_id``: the days and raw lines its result cites) or explicit
  (``from``/``to``, optional ``sources`` and raw lines). ``content``: ``full_days`` (the sealed
  files of every chosen source, plus the cited lines as an extract) or ``extract`` (the cited
  lines plus only the complete sealed files they come from, spec 9.6 "filtered extract plus the
  full original files"). ``password: true`` encrypts the ZIP (AES-256) with a generated password
  that is in **this response only** — never stored, logged or audited. A package of a lookup
  also contains the lookup's official report (``rapor/``, spec 9.6): the stored PDF — rendered
  and stored now through the normal report path (audited ``lookup.report``, committed before the
  limits are checked, like a download) when nobody downloaded it yet — and its time stamp when
  that verifies (otherwise ``paket.json`` says why it is missing). The days' ARCILOGGER audit
  log (``audit.jsonl.gz``: every other lookup of the day, staff names and addresses) is left out
  by default (KVKK data minimisation; ``paket.json`` lists it as excluded with its sealed
  SHA-256); ``include_audit_log: true`` adds it for an internal audit and is accepted from
  **administrators only** (403 ``audit_log_admin_only`` for any other role, checked before any
  work). The choice is kept in the scope (``include_audit_log``) and in both audit entries.
  Answers ``202`` at once;
  the ZIP is built in the background (:mod:`arcilogger_api.evidence_jobs`). Requests over a
  limit (duplicates, unfinished builds, requests per hour, free disk space, size of
  ``exports/``) are refused before anything is queued (:mod:`arcilogger_api.evidence_limits`).
* ``GET /evidence``, ``GET /evidence/{id}`` (``evidence.read``) — who, when, scope, status, size,
  hashes, expiry, downloads.
* ``GET /evidence/{id}/download`` (``evidence.download`` + re-authentication + the accepted KVKK
  notice) — streams the ZIP; every download is recorded in ``evidence_downloads``, audited
  (``evidence.download``) and counted. Expired or missing packages answer ``410``. Range
  requests are not served: every download is the whole file (``Accept-Ranges: none``), so the
  count and the audit entry always mean one complete copy.

**Who sees which package** — the lookup rule (:mod:`arcilogger_api.routers.lookup`, docs/roles.md)
applies here too, because a package of a lookup carries that lookup's official report (request,
reason, result, person chain): administrators see every package; everybody else sees packages
without a lookup (chosen by days) and packages of **their own** lookups. A package of another
user's lookup is left out of the list and answers ``404`` on detail and download (never ``403``:
no enumeration), and a package cannot be requested from a lookup the requester may not see. A
package whose lookup was deleted (``lookup_id`` set to NULL by the database) keeps the lookup
number in its scope and stays hidden from everybody but administrators. A package with the audit
log (``include_audit_log``) holds the stored entries with their real usage targets and hashes, so
it is hidden in the same way from a reader who sees usage pseudonyms
(:func:`arcilogger_api.routers.usage.sees_pseudonyms`; ``GET /audit`` masks those targets for it).

Packages are only ever read from and written to ``exports/``; the vault is only read.
"""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Path, Query, Request, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from sqlalchemy import BigInteger, ColumnElement, and_, cast, func, or_, select, true, update
from sqlalchemy.orm import Session
from starlette.types import Receive, Send
from starlette.types import Scope as AsgiScope

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.auth.schemas import DisplayName, StrictModel
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.errors import ApiError
from arcilogger_api.evidence_jobs import (
    BuildOrder,
    EvidenceBuilder,
    ReportRef,
    Requester,
    builder_for,
    days_not_sealed,
    keep_days,
)
from arcilogger_api.evidence_limits import ESTIMATE_KEY, check_request, estimate_bytes, lock
from arcilogger_api.reports import Person, ReportError, load_or_create
from arcilogger_api.routers.lookup import report_created_details, report_error, sees_all_lookups
from arcilogger_api.routers.usage import sees_pseudonyms
from arcilogger_api.sealcheck import read_roots
from arcilogger_api.sealinfo import skipped_day_cover
from arcilogger_api.security import CurrentUser, client_ip, require, require_recent_auth
from arcilogger_core.canonical import canonical_json
from arcilogger_core.config import Config
from arcilogger_core.evidence import EvidenceError, RawRef, generate_password, package_days
from arcilogger_core.evidence.builder import PACKAGE_PREFIX
from arcilogger_core.evidence.model import SOURCE_NAME
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.models import EvidenceDownload, EvidencePackage, Lookup, LookupReport
from arcilogger_worker.lookup.types import AttributionChain, EvidenceRef, LookupOutcome

router = APIRouter(prefix="/evidence", tags=["evidence"])

#: Most days in one package (a ZIP of whole sealed days can reach gigabytes per day).
MAX_DAYS: Final = 31
#: Most raw lines in one extract.
MAX_EXTRACT_LINES: Final = 10_000
#: Downloads listed on the detail page.
DOWNLOADS_SHOWN: Final = 100
PACKAGE_FILE: Final = re.compile(r"^" + re.escape(PACKAGE_PREFIX) + r"[A-Za-z0-9._-]{1,100}\.zip$")
#: Owner decision (spec 13, docs/roles.md): the auditor lists and downloads, admins create.
CreatorDep = Annotated[CurrentUser, Depends(require_recent_auth("evidence.create"))]
ReaderDep = Annotated[CurrentUser, Depends(require("evidence.read"))]
DownloaderDep = Annotated[
    CurrentUser, Depends(require_privacy_notice("evidence.download", recent=True))
]
PackageId = Annotated[int, Path(ge=1, le=2**63 - 1)]
#: Key in ``evidence_packages.scope``: the days' ARCILOGGER audit log is in the package.
AUDIT_LOG_KEY: Final = "include_audit_log"

STATUS_LABELS: Final[dict[str, tuple[str, str]]] = {
    "queued": ("Sırada", "Queued"),
    "building": ("Hazırlanıyor", "Building"),
    "ready": ("Hazır", "Ready"),
    "failed": ("Oluşturulamadı", "Failed"),
    "expired": ("Süresi doldu", "Expired"),
    "deleted": ("Dosya yok", "File missing"),
}
PASSWORD_NOTICE: Final = (
    "Parola yalnızca şimdi gösteriliyor ve hiçbir yerde saklanmıyor. Not alın ve paketi alan "
    "kişiye paketten ayrı bir yoldan iletin; kaybolursa paket açılamaz.",
    "The password is shown only now and is not stored anywhere. Write it down and give it to the "
    "recipient apart from the package; without it the package cannot be opened.",
)


# -- request and response models ------------------------------------------------------------------

SourceName = Annotated[str, StringConstraints(pattern=SOURCE_NAME.pattern)]


class RawLine(StrictModel):
    """One raw line of a sealed day (line numbers start at 1)."""

    day: date
    source: SourceName
    line: int = Field(ge=1, le=2**31 - 1)


class EvidenceCreate(StrictModel):
    """Scope: ``lookup_id`` **or** ``from``/``to`` (inclusive)."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    lookup_id: int | None = Field(default=None, ge=1, le=2**63 - 1)
    first: date | None = Field(default=None, alias="from")
    last: date | None = Field(default=None, alias="to")
    sources: list[SourceName] | None = Field(default=None, min_length=1, max_length=200)
    extract: list[RawLine] = Field(default_factory=list, max_length=MAX_EXTRACT_LINES)
    content: Literal["full_days", "extract"] = "full_days"
    password: bool = False
    #: Request reference written into the package, e.g. "Savcılık 2026/123".
    reference: DisplayName | None = None
    #: Add the days' ARCILOGGER audit log (internal audits; administrators only). Off by default:
    #: it names every other lookup of the day and staff members (KVKK data minimisation).
    include_audit_log: bool = False


class Scope(BaseModel):
    content: str
    days: list[date]
    sources: list[str] | None
    extract_lines: int
    lookup_id: int | None
    reference: str | None
    #: Size estimate made when the package was requested (``evidence_limits``).
    estimated_bytes: int | None = None
    #: The days' ARCILOGGER audit log is in the package (``include_audit_log`` of the request).
    include_audit_log: bool = False


class EvidenceOut(BaseModel):
    id: int
    created_at: datetime
    created_by: str
    user_id: int | None
    lookup_id: int | None
    status: str
    status_label_tr: str
    status_label_en: str
    error: str | None
    scope: Scope
    package_name: str | None
    file: str | None
    size: int | None
    sha256: str | None
    index_sha256: str | None
    #: SHA-256 of the package's verify.py: give it to the recipient on a separate channel.
    verifier_sha256: str | None
    legal_grade: bool | None
    password_protected: bool
    expires_at: datetime
    finished_at: datetime | None
    download_count: int
    downloadable: bool


class DownloadOut(BaseModel):
    time: datetime
    username: str
    ip: str | None


class EvidenceDetail(EvidenceOut):
    extract: list[RawLine]
    downloads: list[DownloadOut]


class EvidencePage(BaseModel):
    items: list[EvidenceOut]
    next_cursor: int | None


class EvidenceCreated(BaseModel):
    package: EvidenceOut
    #: Only for ``password: true`` — shown once, never stored.
    password: str | None
    password_notice_tr: str | None
    password_notice_en: str | None


# -- helpers --------------------------------------------------------------------------------------


def _scope(row: EvidencePackage) -> Scope:
    scope = row.scope or {}
    return Scope(
        content=str(scope.get("content", "full_days")),
        days=[date.fromisoformat(day) for day in scope.get("days") or []],
        sources=scope.get("sources"),
        extract_lines=len(scope.get("extract") or []),
        lookup_id=row.lookup_id,
        reference=scope.get("reference"),
        estimated_bytes=scope.get(ESTIMATE_KEY),
        include_audit_log=scope.get(AUDIT_LOG_KEY) is True,
    )


def _downloadable(row: EvidencePackage, now: datetime) -> bool:
    return row.status == "ready" and row.expires_at > now


def _out(row: EvidencePackage, now: datetime) -> dict[str, Any]:
    label_tr, label_en = STATUS_LABELS.get(row.status, (row.status, row.status))
    return {
        "id": row.id,
        "created_at": row.created_at,
        "created_by": row.username,
        "user_id": row.user_id,
        "lookup_id": row.lookup_id,
        "status": row.status,
        "status_label_tr": label_tr,
        "status_label_en": label_en,
        "error": row.error,
        "scope": _scope(row),
        "package_name": row.package_name,
        "file": row.path or None,
        "size": row.size,
        "sha256": row.sha256,
        "index_sha256": row.index_sha256,
        "verifier_sha256": row.verifier_sha256,
        "legal_grade": row.legal_grade,
        "password_protected": row.password_protected,
        "expires_at": row.expires_at,
        "finished_at": row.finished_at,
        "download_count": row.download_count,
        "downloadable": _downloadable(row, now),
    }


def may_include_audit_log(user: CurrentUser) -> bool:
    """Orchestrator decision 1: only administrators may put the ARCILOGGER audit log into a
    package. It names every other lookup of the day and staff members, so even a role that may
    create packages (should the owner grant ``evidence.create`` more widely) may not add it."""
    return user.role == "admin"


def _audit_log_refused() -> ApiError:
    return ApiError(
        403,
        "audit_log_admin_only",
        "ARCILOGGER denetim kaydını pakete yalnızca yönetici ekleyebilir.",
        "Only an administrator can add ARCILOGGER's audit log to a package.",
    )


def visible_packages(db: Session, user: CurrentUser) -> ColumnElement[bool]:
    """Packages ``user`` may see (module docstring): all for administrators, otherwise those
    without a lookup and those of the user's own lookups — without the audit log for a user who
    sees usage pseudonyms.

    The lookup is taken from the column or, once retention deleted the lookup (the column is
    then NULL), from the number kept in the scope — such a package is no manual one."""
    if sees_all_lookups(user):
        return true()
    lookup_id = func.coalesce(
        EvidencePackage.lookup_id, cast(EvidencePackage.scope["lookup_id"].astext, BigInteger)
    )
    own = select(Lookup.id).where(Lookup.user_id == user.id)
    visible = or_(lookup_id.is_(None), lookup_id.in_(own))
    if sees_pseudonyms(db, user):
        audit_log = EvidencePackage.scope[AUDIT_LOG_KEY].astext
        visible = and_(visible, audit_log.is_distinct_from("true"))
    return visible


def _visible_package(db: Session, user: CurrentUser, package_id: int) -> EvidencePackage:
    """The package if ``user`` may see it, else 404 (also for a package of another user's
    lookup: the same answer as for a number that does not exist)."""
    row = db.scalars(
        select(EvidencePackage).where(EvidencePackage.id == package_id, visible_packages(db, user))
    ).one_or_none()
    if row is None:
        raise ApiError(404, "not_found")
    return row


def _builder(request: Request) -> EvidenceBuilder:
    builder = builder_for(request)
    builder.recover()
    return builder


BuilderDep = Annotated[EvidenceBuilder, Depends(_builder)]


def _chain_refs(chain: AttributionChain | None) -> list[EvidenceRef]:
    if chain is None:
        return []
    found = [
        *(chain.lease.evidence if chain.lease else ()),
        *(chain.device.evidence if chain.device else ()),
        *(chain.person.evidence if chain.person else ()),
    ]
    for other in chain.alternatives:
        found += _chain_refs(other)
    return found


def lookup_refs(outcome: LookupOutcome) -> set[RawRef]:
    """Every raw line a lookup result cites (sessions, leases, devices, alternatives, and the
    unconverted FortiGate DHCP lines its report shows)."""
    refs: list[EvidenceRef] = []
    for match in outcome.matches:
        refs += match.traffic.evidence
        refs += _chain_refs(match.chain)
    refs += _chain_refs(outcome.attribution)
    refs += outcome.unconverted_dhcp  # shown in section 6 of the report, as no attribution (D8)
    return {
        RawRef(day=ref.raw.day, source=ref.raw.source, line=ref.raw.line)
        for ref in refs
        if ref.raw is not None
    }


def _sealed_file(layout: Layout, day: date, source: str) -> bool:
    return (layout.vault_day(day) / f"{source}.log.gz").is_file()


class _Plan:
    """The validated scope of a new package."""

    def __init__(
        self,
        days: list[date],
        sources: list[str] | None,
        extract: list[RawRef],
        lookup: Lookup | None,
        reference: str | None,
    ) -> None:
        self.days, self.sources, self.extract = days, sources, extract
        self.lookup, self.reference = lookup, reference


def _bad_scope(message_tr: str, message_en: str, **details: Any) -> ApiError:
    return ApiError(422, "scope_invalid", message_tr, message_en, details=details or None)


def _no_sources_with_extract(body: EvidenceCreate) -> None:
    if body.content == "extract" and body.sources:
        raise _bad_scope(
            "Alıntı paketinde kaynaklar satırlardan belirlenir; 'sources' göndermeyin.",
            "An extract package takes its sources from the lines; do not send 'sources'.",
        )


def _too_many_days() -> ApiError:
    return _bad_scope(
        f"Bir pakette en fazla {MAX_DAYS} gün olabilir.",
        f"A package can hold at most {MAX_DAYS} days.",
    )


def _plan_from_lookup(db: Session, user: CurrentUser, body: EvidenceCreate) -> _Plan:
    _no_sources_with_extract(body)
    lookup = db.get(Lookup, body.lookup_id)
    # Only a lookup the requester may see (the package carries its report): 404 like /lookup.
    if lookup is None or not (sees_all_lookups(user) or lookup.user_id == user.id):
        raise ApiError(404, "not_found", "5651 sorgusu bulunamadı.", "5651 lookup not found.")
    if lookup.outcome is None:
        raise ApiError(
            409,
            "lookup_not_finished",
            "Bu sorgunun sonucu henüz yok.",
            "This lookup has no result yet.",
        )
    try:
        outcome = LookupOutcome.from_dict(lookup.outcome)
        refs = lookup_refs(outcome)
    except (EvidenceError, KeyError, TypeError, ValueError) as exc:
        raise ApiError(
            409,
            "lookup_unreadable",
            "Sorgu sonucu okunamadı; paket kapsamını elle seçin.",
            "The lookup result cannot be read; choose the package scope by hand.",
        ) from exc
    refs |= {RawRef(day=line.day, source=line.source, line=line.line) for line in body.extract}
    if body.content == "extract":
        if not refs:
            raise ApiError(
                409,
                "no_raw_references",
                "Sorgu sonucu hiçbir ham kayıt satırına dayanmıyor; tam gün paketi seçin.",
                "The lookup result cites no raw log line; choose a full-day package.",
            )
        days = sorted({ref.day for ref in refs})
        sources: list[str] | None = sorted({ref.source for ref in refs})
    else:
        days = sorted({ref.day for ref in refs} | {site_day(outcome.request.at)})
        # Chosen sources plus those of the cited lines: the package must hold the files its
        # extract is checked against.
        sources = sorted(set(body.sources) | {ref.source for ref in refs}) if body.sources else None
    reference = body.reference or f"{lookup.authority} — {lookup.file_no}"
    return _Plan(days, sources, sorted(refs), lookup, reference)


def _plan_explicit(body: EvidenceCreate) -> _Plan:
    if body.first is None or body.last is None:
        raise _bad_scope(
            "Paket kapsamı için bir 5651 sorgusu ya da başlangıç ve bitiş günü seçin.",
            "Choose a 5651 lookup or a first and last day for the package.",
        )
    if body.last < body.first:
        raise _bad_scope("Bitiş günü başlangıçtan önce.", "The last day is before the first.")
    if (body.last - body.first).days >= MAX_DAYS:
        raise _too_many_days()
    _no_sources_with_extract(body)
    days = package_days(body.first, body.last)
    refs = sorted(
        {RawRef(day=line.day, source=line.source, line=line.line) for line in body.extract}
    )
    outside = sorted({ref.day.isoformat() for ref in refs if ref.day not in days})
    if outside:
        raise _bad_scope(
            "Alıntılanan satırlar seçilen günlerin dışında.",
            "Cited lines lie outside the chosen days.",
            days=outside,
        )
    if body.content == "extract":
        if not refs:
            raise _bad_scope(
                "Alıntı paketi için en az bir ham kayıt satırı seçin.",
                "An extract package needs at least one raw log line.",
            )
        sources: list[str] | None = sorted({ref.source for ref in refs})
    else:
        sources = sorted(set(body.sources)) if body.sources else None
        missing = sorted({ref.source for ref in refs} - set(sources or ()))
        if sources is not None and missing:
            raise _bad_scope(
                "Alıntılanan satırların kaynakları pakete dahil değil.",
                "The sources of the cited lines are not in the package.",
                sources=missing,
            )
    return _Plan(days, sources, refs, None, body.reference)


def _lookup_report(
    db: Session,
    user: CurrentUser,
    lookup: Lookup,
    layout: Layout,
    config: Config,
    ip: str | None,
) -> ReportRef:
    """The official report that goes into a package of ``lookup`` (spec 9.6).

    The stored report when there is one (the same bytes every download got), otherwise it is
    rendered and stored now through the normal report path — once; later downloads and
    packages get these bytes. Errors are the report's own (409 ``report_missing`` /
    ``report_damaged``, 503 ``report_access_denied`` …): no package without its report.
    """
    try:
        stored = load_or_create(
            db,
            lookup=lookup,
            generated_by=Person(username=user.username, display_name=user.display_name),
            generated_by_id=user.id,
            layout=layout,
            config=config,
        )
    except ReportError as exc:
        raise report_error(exc) from None
    if stored.created:
        record_audit(
            db,
            actor=user,
            action="lookup.report",
            target=f"lookup:{lookup.id}",
            details=report_created_details(lookup.id, stored.row),
            ip=ip,
        )
    return ReportRef.of(stored.row)


def _still_stored(db: Session, report: ReportRef, lookup_id: int) -> ReportRef:
    """The report row once more after the commit: the package request goes on with the row as
    it is now (the build checks the bytes again)."""
    row = db.get(LookupReport, report.report_id)
    same = row is not None and row.lookup_id == lookup_id and row.sha256 == report.sha256
    if row is None or not same:  # pragma: no cover - ARCILOGGER never changes a stored report
        raise report_error(ReportError("report_missing", "the stored report changed"))
    return ReportRef.of(row)


def _check_vault(layout: Layout, plan: _Plan) -> None:
    """Refuse at once what the build would refuse later (cheap file checks only)."""
    if len(plan.days) > MAX_DAYS:
        raise _too_many_days()
    unsealed = days_not_sealed(layout, plan.days)
    if unsealed:
        empty = {
            day.isoformat(): cover.isoformat()
            for day in unsealed
            if (cover := skipped_day_cover(layout, day)) is not None
        }
        raise ApiError(
            409,
            "days_not_sealed",
            "Seçilen günlerin bazıları henüz mühürlenmedi (ya da silindi); yalnızca mühürlü "
            "günler pakete alınabilir."
            + (
                " Hiç log olmadığı için boş geçilen bir gün, onu listeleyen mühürlü günle "
                "birlikte seçilmelidir."
                if empty
                else ""
            ),
            "Some of the chosen days are not sealed (or were deleted); only sealed days can be "
            "packaged."
            + (
                " A day skipped for having no log at all must be chosen together with the sealed "
                "day that lists it."
                if empty
                else ""
            ),
            details={"days": [day.isoformat() for day in unsealed]}
            | ({"covered_by": empty} if empty else {}),
        )
    for source in plan.sources or ():
        if not any(_sealed_file(layout, day, source) for day in plan.days):
            raise _bad_scope(
                "Bu kaynağın seçilen günlerde mühürlü dosyası yok.",
                "This source has no sealed file on the chosen days.",
                sources=[source],
            )
    files = {(ref.day, ref.source) for ref in plan.extract}
    missing = sorted(
        f"{day.isoformat()} {source}"
        for day, source in files
        if not _sealed_file(layout, day, source)
    )
    if missing:
        raise ApiError(
            409,
            "lines_not_sealed",
            "Alıntılanan satırların bazılarının mühürlü dosyası yok.",
            "Some cited lines have no sealed file.",
            details={"files": missing},
        )


# -- endpoints ------------------------------------------------------------------------------------


@router.post("", status_code=status.HTTP_202_ACCEPTED)
def create_package(
    body: EvidenceCreate,
    request: Request,
    db: DbSession,
    user: CreatorDep,
    layout: LayoutDep,
    config: ConfigDep,
    builder: BuilderDep,
) -> EvidenceCreated:
    """Accept a package request; the ZIP is built in the background (poll ``GET /{id}``)."""
    if body.include_audit_log and not may_include_audit_log(user):
        raise _audit_log_refused()
    if body.lookup_id is not None and (body.first is not None or body.last is not None):
        raise _bad_scope(
            "Ya bir 5651 sorgusu ya da bir gün aralığı seçin, ikisini birden değil.",
            "Choose either a 5651 lookup or a range of days, not both.",
        )
    try:
        plan = _plan_explicit(body) if body.lookup_id is None else _plan_from_lookup(db, user, body)
    except EvidenceError as exc:  # pragma: no cover - names and numbers are validated above
        raise _bad_scope("Kapsam geçersiz.", "The scope is invalid.") from exc
    _check_vault(layout, plan)
    if not read_roots(layout, config.tsa.roots_dir, provider=config.tsa.provider):
        raise ApiError(
            409,
            "no_trusted_roots",
            "Güvenilen kök sertifika yok (tsa.roots_dir); paket doğrulanamaz.",
            "No trusted root certificates (tsa.roots_dir); the package cannot be verified.",
        )
    now = utc_now()
    extract = [ref.as_json() for ref in plan.extract]
    estimate = estimate_bytes(
        layout, plan.days, plan.sources, len(extract), audit_log=body.include_audit_log
    )
    scope = {
        "content": body.content,
        "days": [day.isoformat() for day in plan.days],
        "sources": plan.sources,
        "extract": extract,
        "lookup_id": plan.lookup.id if plan.lookup else None,
        "reference": plan.reference,
        AUDIT_LOG_KEY: body.include_audit_log,
        ESTIMATE_KEY: estimate,
    }
    ip = client_ip(request)
    report = None
    if plan.lookup is not None:
        lookup_id = plan.lookup.id
        report = _lookup_report(db, user, plan.lookup, layout, config, ip)
        # Rendering (and time-stamping) a new report can take seconds: it is stored and
        # committed like a download *before* the evidence lock, which other requests wait on.
        db.commit()
        report = _still_stored(db, report, lookup_id)
    lock(db)  # the limits and the insert below are one step for concurrent requests
    check_request(
        db,
        layout,
        user_id=user.id,
        scope=scope,
        password_protected=body.password,
        estimate=estimate,
        now=now,
    )
    password = generate_password() if body.password else None
    row = EvidencePackage(
        user_id=user.id,
        username=user.username,
        lookup_id=scope["lookup_id"],
        scope=scope,
        path="",
        password_protected=password is not None,
        status="queued",
        expires_at=now + timedelta(days=keep_days(db)),
    )
    db.add(row)
    db.flush()
    audited_scope: dict[str, Any] = {
        **{key: value for key, value in scope.items() if key != "extract"},
        "extract_lines": len(extract),
        # binds the exact cited lines without copying thousands of them into the audit log
        "extract_sha256": hashlib.sha256(canonical_json(extract)).hexdigest(),
    }
    if report is not None:
        audited_scope["report"] = report.details()
    record_audit(
        db,
        actor=user,
        action="evidence.request",
        target=f"evidence:{row.id}",
        details={**audited_scope, "password_protected": password is not None},
        ip=ip,
    )
    db.commit()  # the background build needs the committed row
    builder.submit(
        BuildOrder(
            package_id=row.id,
            days=tuple(plan.days),
            sources=tuple(plan.sources) if plan.sources is not None else None,
            extract=tuple(plan.extract),
            requester=Requester(id=user.id, username=user.username),
            ip=ip,
            lookup_ref=f"L-{scope['lookup_id']}" if scope["lookup_id"] is not None else None,
            reference=plan.reference,
            scope=audited_scope,
            encrypt=password is not None,
            password=password,
            estimated_bytes=estimate,
            report=report,
            include_audit_log=body.include_audit_log,
        )
    )
    return EvidenceCreated(
        package=EvidenceOut.model_validate(_out(row, now)),
        password=password,
        password_notice_tr=PASSWORD_NOTICE[0] if password else None,
        password_notice_en=PASSWORD_NOTICE[1] if password else None,
    )


@router.get("")
def list_packages(
    db: DbSession,
    user: ReaderDep,
    builder: BuilderDep,
    status_filter: Annotated[
        Literal["queued", "building", "ready", "failed", "expired", "deleted"] | None,
        Query(alias="status"),
    ] = None,
    lookup_id: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    cursor: Annotated[int | None, Query(ge=1, le=2**63 - 1)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> EvidencePage:
    """Packages ``user`` may see, newest first (``cursor`` = ``next_cursor`` of the previous
    page)."""
    query = (
        select(EvidencePackage)
        .where(visible_packages(db, user))
        .order_by(EvidencePackage.id.desc())
        .limit(limit + 1)
    )
    if status_filter is not None:
        query = query.where(EvidencePackage.status == status_filter)
    if lookup_id is not None:
        query = query.where(EvidencePackage.lookup_id == lookup_id)
    if cursor is not None:
        query = query.where(EvidencePackage.id < cursor)
    rows = list(db.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    now = utc_now()
    return EvidencePage(
        items=[EvidenceOut.model_validate(_out(row, now)) for row in rows],
        next_cursor=rows[-1].id if more else None,
    )


@router.get("/{package_id}")
def get_package(
    package_id: PackageId, db: DbSession, user: ReaderDep, builder: BuilderDep
) -> EvidenceDetail:
    row = _visible_package(db, user, package_id)
    downloads = db.scalars(
        select(EvidenceDownload)
        .where(EvidenceDownload.package_id == package_id)
        .order_by(EvidenceDownload.id.desc())
        .limit(DOWNLOADS_SHOWN)
    ).all()
    return EvidenceDetail.model_validate(
        {
            **_out(row, utc_now()),
            "extract": (row.scope or {}).get("extract") or [],
            "downloads": [{"time": d.time, "username": d.username, "ip": d.ip} for d in downloads],
        }
    )


def _gone(message_tr: str, message_en: str) -> ApiError:
    return ApiError(410, "evidence_gone", message_tr, message_en)


class WholeFileResponse(FileResponse):
    """A file response that ignores ``Range``/``If-Range`` (RFC 9110 allows a server to).

    Starlette serves byte ranges (``206``); for an evidence package every response must be the
    complete file, because each one is recorded, audited and counted as a download.
    """

    async def __call__(self, scope: AsgiScope, receive: Receive, send: Send) -> None:
        headers = [
            (name, value)
            for name, value in scope.get("headers", [])
            if name.lower() not in (b"range", b"if-range")
        ]
        await super().__call__({**scope, "headers": headers}, receive, send)


@router.get("/{package_id}/download")
def download_package(
    package_id: PackageId,
    request: Request,
    db: DbSession,
    user: DownloaderDep,
    layout: LayoutDep,
    builder: BuilderDep,
) -> WholeFileResponse:
    """The ZIP as an attachment; every download is recorded, audited and counted."""
    row = _visible_package(db, user, package_id)  # before anything is counted or audited
    now = utc_now()
    if row.status in ("queued", "building"):
        raise ApiError(
            409,
            "evidence_not_ready",
            "Paket henüz hazırlanıyor; biraz sonra tekrar deneyin.",
            "The package is still being built; try again shortly.",
        )
    if row.status == "failed":
        raise ApiError(
            409,
            "evidence_failed",
            "Bu paket oluşturulamadı; ayrıntı paket bilgisinde.",
            "This package could not be built; see the package details.",
        )
    if row.status != "ready" or row.expires_at <= now:
        raise _gone(
            "Paketin saklama süresi doldu ve silindi; gerekirse yeniden oluşturun.",
            "The package expired and was deleted; create it again if needed.",
        )
    if not PACKAGE_FILE.match(row.path):
        raise _gone("Paket dosyası bulunamadı.", "The package file is missing.")
    exports = layout.exports.resolve()
    path = (exports / row.path).resolve()
    if path.parent != exports or not path.is_file() or path.stat().st_size != row.size:
        raise _gone(
            "Paket dosyası bulunamadı ya da değişmiş; yeniden oluşturun.",
            "The package file is missing or changed; create it again.",
        )
    counted = db.execute(
        update(EvidencePackage)
        .where(EvidencePackage.id == package_id, EvidencePackage.status == "ready")
        .values(download_count=EvidencePackage.download_count + 1)
        .returning(EvidencePackage.download_count)
    ).scalar_one_or_none()
    if counted is None:  # pragma: no cover - purged between the checks above and this update
        raise _gone("Paket artık yok.", "The package is gone.")
    ip = client_ip(request)
    db.add(EvidenceDownload(package_id=package_id, user_id=user.id, username=user.username, ip=ip))
    record_audit(
        db,
        actor=user,
        action="evidence.download",
        target=f"evidence:{package_id}",
        details={
            "package_id": row.package_name,
            "file": row.path,
            "sha256": row.sha256,
            "size": row.size,
            "download": counted,
        },
        ip=ip,
    )
    digest = base64.b64encode(bytes.fromhex(row.sha256 or "")).decode("ascii")
    return WholeFileResponse(
        path,
        media_type="application/zip",
        filename=row.path,
        content_disposition_type="attachment",
        headers={
            "Accept-Ranges": "none",
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            # RFC 9530: the client can check the bytes it received against the recorded hash.
            "Repr-Digest": f"sha-256=:{digest}:",
        },
    )
