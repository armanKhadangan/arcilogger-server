"""``/api/v1/lookup`` — 5651 sorgusu, sonuçlar, resmi rapor PDF (spec 9.5, 12.3 page 6, 16).

* ``POST /lookup/5651`` runs a lookup. The request details (makam, dosya/evrak no, talep tarihi,
  gerekçe) are required and must not be blank (spec 12.3 page 6: entered before results are
  shown). The query is :class:`arcilogger_worker.lookup.LookupRequest`; ``at`` is ISO-8601 —
  **with** an offset (``2026-10-12T11:23:41+03:00``, ``…Z``) it is taken as given, **without**
  one it is a local time in ``time_zone`` (default Europe/Istanbul) and the answer says so; a
  local time that is ambiguous or skipped by a clock change in that zone is refused (422).
  A public or internal **IPv6** address is refused before the lookup runs (422
  ``ipv6_not_supported``, audited as ``lookup.run_failed``): ARCILOGGER keeps no evidence that
  ties an IPv6 address to a device (no NDP, no DHCPv6), so such a request must be answered by
  other means — never with IPv4 sessions as candidates.
  The lookup must finish within ``lookup.timeout_seconds`` (spec 14: < 5 s for ±60 s): each
  statement gets the remaining time as PostgreSQL ``statement_timeout``; past the limit the
  answer is 504 ``lookup_timeout`` (audited as ``lookup.run_failed``) instead of a hanging
  request. The lookup is stored (``lookups``) and audited (``lookup.run``).
* ``GET /lookup/{id}`` — one lookup with its outcome (audited ``lookup.view``).
* ``GET /lookup/{id}/report.pdf`` — the official report: rendered and stored on the first call
  (``lookup.report``), the same bytes on every call (``lookup.report_download``);
  ``GET /lookup/{id}/report.pdf.zd`` — its time-stamp token, when ``lookup.report_timestamp``
  was on.

**Who sees which lookup** (spec 13 + KVKK, docs/MVP-CONTRACTS.md section 6): everything needs
``lookup.run`` and the accepted KVKK notice. Administrators see every lookup; everybody else
(the auditor) only their own — spec 9.5 shows results only after the user entered a reason, so
reading another person's results without one is not offered; the auditor still sees in the
audit log that the lookup happened, with its inputs. Another user's lookup answers 404, never
403 (no enumeration of lookup numbers). The same rule covers the evidence packages built from a
lookup, which carry its report (:func:`arcilogger_api.routers.evidence.visible_packages`).

**Audit details are minimal** (the audit log is sealed and kept): inputs in machine form, the
request's authority, file number and date, the SHA-256 of the reason (the text itself stays in
``lookups``, which retention can delete) and result **ids** (traffic rows, leases, persons) —
never names, MAC addresses or raw lines.
"""

from __future__ import annotations

import hashlib
import ipaddress
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from time import monotonic
from typing import Annotated, Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Path, Request, Response, status
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
)
from sqlalchemy import Connection, event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.errors import ApiError
from arcilogger_api.reports import Person, ReportError, load_or_create, read_token
from arcilogger_api.security import CurrentUser, client_ip
from arcilogger_core.iputil import canonical_ip
from arcilogger_core.timeutil import SITE_TZ_NAME, site_day, utc_now
from arcilogger_db.models import Lookup, LookupReport
from arcilogger_db.settings import get_settings
from arcilogger_worker.lookup import (
    AttributionChain,
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    normalize_mac,
    protocol_number,
    run_lookup,
)
from arcilogger_worker.lookup.types import MAX_TOLERANCE_SECONDS

router = APIRouter(prefix="/lookup", tags=["lookup"])

RunnerDep = Annotated[CurrentUser, Depends(require_privacy_notice("lookup.run"))]

TIMEOUT_SETTING: Final = "lookup.timeout_seconds"
DEFAULT_TIMEOUT_SECONDS: Final = 5
#: PostgreSQL ``query_canceled`` (statement timeout).
QUERY_CANCELED: Final = "57014"
#: Results listed per lookup in its audit entry (the outcome keeps all of them).
AUDIT_RESULTS: Final = 100
#: Largest lookup number (PostgreSQL ``bigint``): larger path values answer 422, not a database
#: error.
MAX_ID: Final = 2**63 - 1
LookupId = Annotated[int, Path(ge=1, le=MAX_ID, description="Lookup number")]

MESSAGES: Final = {
    "lookup_timeout": (
        "Sorgu {seconds} saniye içinde tamamlanamadı ve durduruldu. Toleransı daraltın veya "
        "hedef bilgisi ekleyin; sorun sürerse sistem yöneticisine bildirin.",
        "The lookup did not finish within {seconds} seconds and was stopped. Narrow the "
        "tolerance or add destination details; tell the administrator if it persists.",
    ),
    "lookup_settings_invalid": (
        "5651 sorgu ayarları geçersiz (Ayarlar → NAT); sistem yöneticisi düzeltmelidir.",
        "The 5651 lookup settings are invalid (Settings → NAT); an administrator must fix them.",
    ),
    "ipv6_not_supported": (
        "Bu sürümde IPv6 adresleriyle 5651 sorgusu yapılamaz: ARCILOGGER, IPv6 adreslerini "
        "cihazlara bağlayan kayıtları (NDP, DHCPv6) toplamaz. Bu talep başka yollarla "
        "yanıtlanmalıdır. IPv6 biçiminde yazılmış bir IPv4 adresini (::ffff:…) IPv4 olarak girin.",
        "This version does not support 5651 lookups for IPv6 addresses: ARCILOGGER does not "
        "collect the records that tie IPv6 addresses to devices (NDP, DHCPv6). The request must be "
        "answered by other means. Enter an IPv4 address written as IPv6 (::ffff:…) as IPv4.",
    ),
    "lookup_unfinished": (
        "Bu sorgunun sonucu yok; rapor oluşturulamaz.",
        "This lookup has no result; no report can be generated.",
    ),
    "report_missing": (
        "Kayıtlı rapor dosyası bulunamadı. Rapor yeniden üretilmez (özeti değişirdi); sistem "
        "yöneticisine bildirin.",
        "The stored report file is missing. It is not generated again (its hash would change); "
        "tell the administrator.",
    ),
    "report_damaged": (
        "Kayıtlı rapor dosyası, kaydedilen SHA-256 özetiyle eşleşmiyor (değiştirilmiş olabilir); "
        "sistem yöneticisine bildirin.",
        "The stored report file does not match its recorded SHA-256 (it may have been changed); "
        "tell the administrator.",
    ),
    "report_access_denied": (
        "Rapor oluşturulmadı: ARCILOGGER API hizmetinin kanıt dosyalarından birini ({file}) okuma "
        "izni yok (kurulumdaki klasör izinleri). Rapor eksik kanıtla üretilmez; izin "
        "düzeltildikten sonra tekrar deneyin. Sistem yöneticisine bildirin.",
        "No report was made: the ARCILOGGER API service may not read a cited file ({file}; "
        "folder permissions of the installation). A report is never made with missing evidence; "
        "try again once the permission is fixed. Tell the administrator.",
    ),
    "report_evidence_unreadable": (
        "Rapor oluşturulmadı: kanıt dosyalarından biri ({file}) şu anda okunamıyor (disk veya "
        "dosya sistemi hatası). Biraz sonra tekrar deneyin; sürerse sistem yöneticisine "
        "bildirin.",
        "No report was made: a cited file ({file}) cannot be read right now (disk or file system "
        "error). Try again later; tell the administrator if it persists.",
    ),
    "report_seal_in_progress": (
        "Rapor oluşturulmadı: dayandığı günlerden biri ({file}) şu anda mühürleniyor. Birkaç "
        "dakika sonra tekrar deneyin.",
        "No report was made: one of its days ({file}) is being sealed right now. Try again in a "
        "few minutes.",
    ),
    "report_render_failed": (
        "Rapor PDF olarak dizilemedi; hiçbir şey kaydedilmedi. Sistem yöneticisine bildirin "
        "(ayrıntılar sistem kaydında).",
        "The report could not be laid out as a PDF; nothing was stored. Tell the administrator "
        "(details are in the system log).",
    ),
}
#: HTTP status of each report error (``report_missing``/``report_damaged``: the stored file is
#: gone or changed; the others: the report could not be made now and nothing was stored).
REPORT_STATUS: Final = {
    "lookup_unfinished": 409,
    "report_missing": 409,
    "report_damaged": 409,
    "report_access_denied": 503,
    "report_evidence_unreadable": 503,
    "report_seal_in_progress": 503,
    "report_render_failed": 500,
}


def _error(status_code: int, code: str, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(status_code, code, text_tr.format(**params), text_en.format(**params))


# -- input ----------------------------------------------------------------------------------------


def _printable(value: str, *, multiline: bool = False) -> str:
    allowed = {"\n", "\t"} if multiline else set()
    if any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs"} and char not in allowed for char in value
    ):
        raise ValueError("control characters are not allowed")
    return value


def _single_line(value: str) -> str:
    return _printable(value)


def _multi_line(value: str) -> str:
    return _printable(value.replace("\r\n", "\n"), multiline=True)


def _ip(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return canonical_ip(value.strip())
    except ValueError:
        raise ValueError("not an IP address") from None


def _mac(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    try:
        return normalize_mac(value)
    except ValueError:
        raise ValueError("not a MAC address (aa:bb:cc:dd:ee:ff)") from None


def _protocol(value: object) -> int | None:
    if value is not None and not isinstance(value, str | int):
        raise ValueError("protocol must be a number 0-255 or tcp, udp, icmp")
    try:
        return protocol_number(value)
    except ValueError:
        raise ValueError("protocol must be a number 0-255 or tcp, udp, icmp") from None


def _domain(value: str | None) -> str | None:
    if value is None or not value.strip():
        return None
    domain = value.strip()
    if any(char.isspace() or char in "/\\@:" for char in domain):
        raise ValueError("not a domain name")
    return _printable(domain)


def _time_zone(value: str) -> str:
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError):
        raise ValueError("unknown time zone (IANA name, e.g. Europe/Istanbul or UTC)") from None
    return value


SingleLine = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=300),
    AfterValidator(_single_line),
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequestDetails(StrictModel):
    """Talep bilgileri (spec 12.3 page 6) — all required, never blank."""

    authority: Annotated[SingleLine, Field(description="Makam (requesting authority)")]
    file_no: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
        AfterValidator(_single_line),
        Field(description="Dosya / evrak no"),
    ]
    request_date: Annotated[date, Field(description="Talep tarihi (not in the future)")]
    reason: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=1, max_length=4000),
        AfterValidator(_multi_line),
        Field(description="Gerekçe / reference (line breaks allowed)"),
    ]


class QueryIn(StrictModel):
    """What the official request asks (any combination; ``at`` is required)."""

    at: Annotated[
        str,
        StringConstraints(strip_whitespace=True, min_length=16, max_length=40),
        Field(
            description=(
                "ISO-8601 date and time. With an offset (+03:00, Z) it is used as given; "
                "without one it is a local time in `time_zone` (refused when that local time "
                "is ambiguous or does not exist because of a clock change)."
            ),
            examples=["2026-10-12T11:23:41+03:00", "2026-10-12T11:23:41"],
        ),
    ]
    time_zone: Annotated[
        str,
        StringConstraints(max_length=64),
        AfterValidator(_time_zone),
        Field(description="IANA time zone for an `at` without offset"),
    ] = SITE_TZ_NAME
    public_ip: Annotated[str | None, Field(max_length=64), AfterValidator(_ip)] = None
    public_port: Annotated[int | None, Field(ge=0, le=65535)] = None
    protocol: Annotated[
        int | None,
        BeforeValidator(_protocol),
        Field(description="6, 17, 1 or tcp, udp, icmp"),
    ] = None
    dest_ip: Annotated[str | None, Field(max_length=64), AfterValidator(_ip)] = None
    dest_port: Annotated[int | None, Field(ge=0, le=65535)] = None
    dest_domain: Annotated[str | None, Field(max_length=253), AfterValidator(_domain)] = None
    tolerance_seconds: Annotated[
        int | None,
        Field(ge=0, le=MAX_TOLERANCE_SECONDS, description="± seconds (default: setting)"),
    ] = None
    internal_ip: Annotated[str | None, Field(max_length=64), AfterValidator(_ip)] = None
    mac: Annotated[str | None, Field(max_length=32), AfterValidator(_mac)] = None


class LookupIn(StrictModel):
    request: RequestDetails
    query: QueryIn


def parse_moment(value: str, time_zone: str) -> tuple[datetime, bool]:
    """``(aware moment, offset_given)``; a value without offset is local time in ``time_zone``.

    A local time that is **ambiguous** (it occurs twice when the clocks go back) or **does not
    exist** (skipped when they go forward) in ``time_zone`` is refused: an official lookup must
    not pick one of two hours silently — the request then has to give the UTC offset.
    Europe/Istanbul has had no clock changes since 2016, so this concerns other zones.
    """
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError("not an ISO-8601 date and time") from None
    if moment.tzinfo is not None and moment.utcoffset() is not None:
        return moment, True
    zone = ZoneInfo(time_zone)
    first, second = moment.replace(tzinfo=zone, fold=0), moment.replace(tzinfo=zone, fold=1)
    if first.utcoffset() != second.utcoffset():
        round_trip = first.astimezone(UTC).astimezone(zone).replace(tzinfo=None, fold=0)
        if round_trip != moment.replace(fold=0):
            raise ValueError(
                f"this local time does not exist in {time_zone} (clocks went forward); "
                "give the time with its UTC offset, e.g. +01:00"
            )
        raise ValueError(
            f"this local time occurs twice in {time_zone} (clocks went back); give the time "
            "with its UTC offset, e.g. +01:00 or +02:00"
        )
    return first, False


def _invalid(field: str, message: str) -> ApiError:
    return ApiError(
        422,
        "validation_error",
        details=[{"loc": ["body", "query", field], "type": "value_error", "msg": message}],
    )


def _to_request(query: QueryIn) -> tuple[LookupRequest, dict[str, Any]]:
    try:
        at, offset_given = parse_moment(query.at, query.time_zone)
    except ValueError as exc:
        raise _invalid("at", str(exc)) from None
    try:
        request = LookupRequest(
            at=at,
            public_ip=query.public_ip,
            public_port=query.public_port,
            protocol=query.protocol,
            dest_ip=query.dest_ip,
            dest_port=query.dest_port,
            dest_domain=query.dest_domain,
            tolerance_seconds=query.tolerance_seconds,
            internal_ip=query.internal_ip,
            mac=query.mac,
        )
    except ValueError as exc:
        raise ApiError(
            422,
            "validation_error",
            details=[{"loc": ["body", "query"], "type": "value_error", "msg": str(exc)}],
        ) from None
    time_input = {
        "text": query.at,
        "offset_given": offset_given,
        "time_zone": None if offset_given else query.time_zone,
    }
    return request, time_input


def _ipv6_fields(request: LookupRequest) -> list[str]:
    """The public / internal address fields of ``request`` that hold an IPv6 address.

    Such a lookup is refused (``ipv6_not_supported``): no IPv6 identity evidence (NDP, DHCPv6)
    is collected. An IPv4 address written as IPv6 (``::ffff:192.0.2.44``) counts as IPv6 — it
    never equals the IPv4 ``inet`` values of the logs; the message asks for the IPv4 form.
    """
    return [
        field
        for field in ("public_ip", "internal_ip")
        if (address := getattr(request, field)) is not None
        and ipaddress.ip_address(address).version == 6
    ]


# -- statement deadline ---------------------------------------------------------------------------


class LookupTimeoutError(RuntimeError):
    """The lookup used up its time budget."""


@contextmanager
def statement_deadline(session: Session, seconds: float) -> Iterator[None]:
    """Every statement of ``session`` inside the block gets the **remaining** time as its
    PostgreSQL ``statement_timeout`` (transaction-local); once the time is up the next statement
    is not even sent. The whole block therefore ends within ``seconds`` (plus one round trip).
    """
    connection = session.connection()
    deadline = monotonic() + seconds

    def before_cursor_execute(
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        remaining = int((deadline - monotonic()) * 1000)
        if remaining <= 0:
            raise LookupTimeoutError(f"time budget of {seconds} s used up")
        # Straight on the DB-API cursor (not through SQLAlchemy, which would call us again).
        cursor.execute(f"SELECT set_config('statement_timeout', '{remaining}', true)")

    event.listen(connection, "before_cursor_execute", before_cursor_execute)
    try:
        yield
    finally:
        event.remove(connection, "before_cursor_execute", before_cursor_execute)


def _is_timeout(exc: DBAPIError) -> bool:
    original = exc.orig
    return QUERY_CANCELED in (
        getattr(original, "sqlstate", None),
        getattr(original, "pgcode", None),
    )


# -- output ---------------------------------------------------------------------------------------


class RequestOut(BaseModel):
    authority: str
    file_no: str
    request_date: date
    reason: str


class ReportOut(BaseModel):
    sha256: str
    size: int
    pages: int
    created_at: datetime
    username: str
    version: str
    timestamp_status: str
    timestamp: dict[str, Any] | None
    download_count: int

    @classmethod
    def of(cls, row: LookupReport) -> ReportOut:
        return cls(
            sha256=row.sha256,
            size=row.size,
            pages=row.pages,
            created_at=row.created_at,
            username=row.username,
            version=row.version,
            timestamp_status=row.timestamp_status,
            timestamp=row.timestamp,
            download_count=row.download_count,
        )


class LookupOut(BaseModel):
    id: int
    created_at: datetime
    finished_at: datetime | None
    user_id: int | None
    username: str
    request: RequestOut
    #: ``LookupRequest.to_dict()`` plus ``time_input`` (how ``at`` was entered).
    query: dict[str, Any]
    mode: str | None
    result_count: int
    #: ``LookupOutcome.to_dict()`` (docs/MVP-CONTRACTS.md section 5).
    outcome: dict[str, Any] | None
    report: ReportOut | None
    #: Time the lookup took (only in the answer to ``POST /lookup/5651``).
    elapsed_ms: int | None = None

    @classmethod
    def of(
        cls, row: Lookup, report: LookupReport | None, *, elapsed_ms: int | None = None
    ) -> LookupOut:
        return cls(
            id=row.id,
            created_at=row.created_at,
            finished_at=row.finished_at,
            user_id=row.user_id,
            username=row.username,
            request=RequestOut(
                authority=row.authority,
                file_no=row.file_no,
                request_date=row.request_date,
                reason=row.reason,
            ),
            query=row.query,
            mode=row.mode,
            result_count=row.result_count,
            outcome=row.outcome,
            report=None if report is None else ReportOut.of(report),
            elapsed_ms=elapsed_ms,
        )


# -- visibility and audit -------------------------------------------------------------------------


def sees_all_lookups(user: CurrentUser) -> bool:
    """Administrators see every lookup; other roles with ``lookup.run`` only their own."""
    return user.role == "admin"


def visible_lookup(db: Session, user: CurrentUser, lookup_id: int) -> Lookup:
    """The lookup if ``user`` may see it, else 404 (also for another user's lookup)."""
    row = db.get(Lookup, lookup_id)
    if row is None or not (sees_all_lookups(user) or row.user_id == user.id):
        raise ApiError(404, "not_found")
    return row


def _report_of(db: Session, lookup_id: int) -> LookupReport | None:
    return db.scalar(select(LookupReport).where(LookupReport.lookup_id == lookup_id))


def _chain_ids(chain: AttributionChain) -> dict[str, Any]:
    ids: dict[str, Any] = {
        "lease": chain.lease.lease_id if chain.lease else None,
        "person": chain.person.person_id if chain.person else None,
    }
    if chain.alternatives:
        ids["alternatives"] = [_chain_ids(other) for other in chain.alternatives]
    return ids


def result_ids(outcome: LookupOutcome) -> list[dict[str, Any]]:
    """Ids behind each result (for the audit entry): traffic rows, lease and person ids."""
    results: list[dict[str, Any]] = []
    for match in outcome.matches:
        item: dict[str, Any] = {
            "kind": match.kind,
            "score": match.score,
            "traffic": sorted({e.row_id for e in match.traffic.evidence if e.row_id is not None}),
        }
        if match.chain is not None:
            item.update(_chain_ids(match.chain))
        results.append(item)
    if outcome.attribution is not None:
        results.append({"kind": "internal", **_chain_ids(outcome.attribution)})
    return results


def _request_details(row: Lookup | RequestDetails) -> dict[str, Any]:
    return {
        "authority": row.authority,
        "file_no": row.file_no,
        "request_date": row.request_date.isoformat(),
        "reason_sha256": hashlib.sha256(row.reason.encode("utf-8")).hexdigest(),
    }


# -- endpoints ------------------------------------------------------------------------------------


@router.post("/5651", status_code=status.HTTP_201_CREATED)
def run_5651(
    body: LookupIn,
    request: Request,
    db: DbSession,
    layout: LayoutDep,
    user: RunnerDep,
) -> LookupOut:
    """Run a 5651 lookup, store it and return its id and outcome."""
    details = body.request
    if details.request_date > site_day(utc_now()):
        raise ApiError(
            422,
            "validation_error",
            details=[
                {
                    "loc": ["body", "request", "request_date"],
                    "type": "value_error",
                    "msg": "the request date is in the future",
                }
            ],
        )
    lookup_request, time_input = _to_request(body.query)
    query = {**lookup_request.to_dict(), "time_input": time_input}
    ipv6 = _ipv6_fields(lookup_request)
    if ipv6:
        # An official request this version cannot answer: refused, the attempt is audited.
        record_audit(
            db,
            actor=user,
            action="lookup.run_failed",
            details={
                "reason": "ipv6_not_supported",
                "query": query,
                "request": _request_details(details),
            },
            ip=client_ip(request),
        )
        db.commit()
        text_tr, text_en = MESSAGES["ipv6_not_supported"]
        raise ApiError(
            422,
            "ipv6_not_supported",
            text_tr,
            text_en,
            details=[
                {
                    "loc": ["body", "query", field],
                    "type": "value_error",
                    "msg": "IPv6 lookups are not supported",
                }
                for field in ipv6
            ],
        )
    values = get_settings(db)
    try:
        settings = LookupSettings.from_settings(values)
        limit = min(max(int(values.get(TIMEOUT_SETTING, DEFAULT_TIMEOUT_SECONDS)), 1), 300)
    except (TypeError, ValueError) as exc:
        raise _error(503, "lookup_settings_invalid") from exc
    started = monotonic()
    try:
        with statement_deadline(db, limit):
            outcome = run_lookup(db, lookup_request, settings, layout=layout)
    except (LookupTimeoutError, DBAPIError) as exc:
        if isinstance(exc, DBAPIError) and not _is_timeout(exc):
            raise
        db.rollback()
        record_audit(
            db,
            actor=user,
            action="lookup.run_failed",
            details={
                "reason": "timeout",
                "limit_seconds": limit,
                "elapsed_ms": int((monotonic() - started) * 1000),
                "query": query,
                "request": _request_details(details),
            },
            ip=client_ip(request),
        )
        db.commit()
        raise _error(504, "lookup_timeout", seconds=limit) from None
    elapsed = int((monotonic() - started) * 1000)
    db.execute(text("SET LOCAL statement_timeout TO DEFAULT"))
    data = outcome.to_dict()
    row = Lookup(
        user_id=user.id,
        username=user.username,
        authority=details.authority,
        file_no=details.file_no,
        request_date=details.request_date,
        reason=details.reason,
        query=query,
        outcome=data,
        mode=outcome.mode,
        result_count=outcome.result_count,
        finished_at=utc_now(),
    )
    db.add(row)
    db.flush()
    results = result_ids(outcome)
    record_audit(
        db,
        actor=user,
        action="lookup.run",
        target=f"lookup:{row.id}",
        details={
            "lookup_id": row.id,
            "request": _request_details(row),
            "query": query,
            "mode": outcome.mode,
            "result_count": outcome.result_count,
            "truncated": outcome.truncated,
            "results": results[:AUDIT_RESULTS],
            "results_listed": min(len(results), AUDIT_RESULTS),
        },
        ip=client_ip(request),
    )
    return LookupOut.of(row, None, elapsed_ms=elapsed)


@router.get("/{lookup_id}")
def get_lookup(lookup_id: LookupId, request: Request, db: DbSession, user: RunnerDep) -> LookupOut:
    """One lookup with its outcome (administrators: any; others: their own)."""
    row = visible_lookup(db, user, lookup_id)
    record_audit(
        db,
        actor=user,
        action="lookup.view",
        target=f"lookup:{row.id}",
        details={"lookup_id": row.id, "own": row.user_id == user.id},
        ip=client_ip(request),
    )
    return LookupOut.of(row, _report_of(db, row.id))


def report_error(exc: ReportError) -> ApiError:
    """The API error of a report that cannot be served or made now (also for evidence
    packages, which take the stored report)."""
    return _error(REPORT_STATUS.get(exc.code, 409), exc.code, file=exc.file or "—")


def _timestamp_summary(report: LookupReport) -> dict[str, Any]:
    info = report.timestamp or {}
    keys = ("provider", "legal_grade", "serial", "gen_time", "verified", "error")
    return {"status": report.timestamp_status, **{k: info[k] for k in keys if k in info}}


def report_created_details(lookup_id: int, report: LookupReport) -> dict[str, Any]:
    """Details of the ``lookup.report`` entry written when a report is rendered and stored
    (here on the first download, or when an evidence package of the lookup needs it)."""
    return {
        "lookup_id": lookup_id,
        "sha256": report.sha256,
        "size": report.size,
        "pages": report.pages,
        "version": report.version,
        "file": report.path,
        "timestamp": _timestamp_summary(report),
    }


@router.get(
    "/{lookup_id}/report.pdf",
    response_class=Response,
    responses={200: {"content": {"application/pdf": {}}}},
)
def report_pdf(
    lookup_id: LookupId,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    layout: LayoutDep,
    user: RunnerDep,
) -> Response:
    """The official report ("Resmi Rapor"): generated once, the same bytes on every call."""
    row = visible_lookup(db, user, lookup_id)
    if row.outcome is None:
        raise _error(409, "lookup_unfinished")
    try:
        stored = load_or_create(
            db,
            lookup=row,
            generated_by=Person(username=user.username, display_name=user.display_name),
            generated_by_id=user.id,
            layout=layout,
            config=config,
        )
    except ReportError as exc:
        raise report_error(exc) from None
    report = stored.row
    ip = client_ip(request)
    if stored.created:
        record_audit(
            db,
            actor=user,
            action="lookup.report",
            target=f"lookup:{row.id}",
            details=report_created_details(row.id, report),
            ip=ip,
        )
    report.download_count += 1
    record_audit(
        db,
        actor=user,
        action="lookup.report_download",
        target=f"lookup:{row.id}",
        details={
            "lookup_id": row.id,
            "file": "pdf",
            "sha256": report.sha256,
            "download": report.download_count,
        },
        ip=ip,
    )
    return Response(
        content=stored.pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{report.path.rsplit("/", 1)[-1]}"',
            "X-Arci-Sha256": report.sha256,
        },
    )


@router.get(
    "/{lookup_id}/report.pdf.zd",
    response_class=Response,
    responses={200: {"content": {"application/timestamp-reply": {}}}},
)
def report_token(
    lookup_id: LookupId,
    request: Request,
    db: DbSession,
    layout: LayoutDep,
    user: RunnerDep,
) -> Response:
    """The RFC 3161 time-stamp token of the report's hash (only when it was stamped)."""
    row = visible_lookup(db, user, lookup_id)
    report = _report_of(db, row.id)
    if report is None or report.timestamp_status != "stamped":
        raise ApiError(404, "not_found")
    try:
        token = read_token(layout, report)
    except ReportError as exc:
        raise report_error(exc) from None
    record_audit(
        db,
        actor=user,
        action="lookup.report_download",
        target=f"lookup:{row.id}",
        details={
            "lookup_id": row.id,
            "file": "zd",
            "sha256": (report.timestamp or {}).get("token_sha256"),
        },
        ip=client_ip(request),
    )
    name = report.path.rsplit("/", 1)[-1] + ".zd"
    return Response(
        content=token,
        media_type="application/timestamp-reply",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
