"""Stored official reports: rendered **once**, kept in ``exports/reports/``, served unchanged.

The first request for a lookup's report collects the inputs, renders the PDF, writes it
atomically to ``exports/reports/ARCILOGGER-5651-rapor-<id>.pdf`` and records path, SHA-256, size,
page count and inputs in ``lookup_reports`` — under a row lock on the lookup, so two first
downloads at the same moment produce one report. With the setting ``lookup.report_timestamp``
the PDF's hash is then time-stamped with the configured ``[tsa]`` provider (1 kontör with Kamu
SM); the token goes next to the PDF as ``….pdf.zd`` and is verified against the pinned roots.

Nothing is stored when the report cannot be made: a cited file that exists but cannot be read
now (``report_access_denied`` / ``report_evidence_unreadable``: Windows folder rights, I/O error)
or a layout failure (``report_render_failed``), or a cited day that is being sealed right now
and did not settle while the report was collected (``report_seal_in_progress``) — the next request
tries again.

Every later request reads the stored file and checks it against the recorded SHA-256: a missing
or changed file is reported (``report_missing`` / ``report_damaged``), never silently rendered
again — the hash handed out with the first download must stay valid, and a new rendering would
read the vault as it is *now*. Files are written by the API service only (ADR-0007: it may write
``exports``); nothing here touches the vault or the spool.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

from reportlab.platypus.doctemplate import LayoutError  # type: ignore[import-untyped]
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_api import __version__
from arcilogger_api.reports.inputs import (
    EvidenceReadError,
    Person,
    SealInProgressError,
    TimestampPlan,
    build_inputs,
)
from arcilogger_api.reports.render import render
from arcilogger_core.config import Config
from arcilogger_core.fsutil import atomic_write_bytes
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_core.timeutil import utc_now
from arcilogger_core.tsa import make_provider, trusted_roots
from arcilogger_core.tsa.base import TsaError
from arcilogger_core.tsa.verify import verify_token
from arcilogger_db.engine import secret_store
from arcilogger_db.models import Lookup, LookupReport, User
from arcilogger_db.settings import get_setting
from arcilogger_worker.lookup import LookupOutcome

log = logging.getLogger("arcilogger.api.reports")

REPORT_FOLDER: Final = "reports"
TIMESTAMP_SETTING: Final = "lookup.report_timestamp"


class ReportError(RuntimeError):
    """The stored report cannot be served; ``code`` is the API error code."""

    def __init__(self, code: str, message: str, *, file: str | None = None) -> None:
        super().__init__(message)
        self.code = code
        #: Data-directory relative path of the file concerned, when there is one.
        self.file = file


@dataclass(frozen=True)
class StoredReport:
    row: LookupReport
    pdf: bytes
    #: True when this call rendered (and stored) the report.
    created: bool


def report_name(lookup_id: int) -> str:
    return f"ARCILOGGER-5651-rapor-{lookup_id}.pdf"


def export_path(layout: Layout, relative: str) -> Path:
    """A path recorded relative to ``exports``; it may not leave that folder."""
    parts = PurePosixPath(relative).parts
    if not parts or relative.startswith("/") or any(p in ("..", ".") or ":" in p for p in parts):
        raise ReportError("report_damaged", f"unsafe report path {relative!r}")
    return layout.exports.joinpath(*parts)


def read_stored(layout: Layout, row: LookupReport) -> bytes:
    """The stored PDF, checked against its recorded SHA-256."""
    path = export_path(layout, row.path)
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        log.error("lookup report %s: file %s is missing", row.lookup_id, row.path)
        raise ReportError("report_missing", f"{row.path} is missing") from exc
    if hashlib.sha256(data).hexdigest() != row.sha256:
        log.error("lookup report %s: %s does not match its SHA-256", row.lookup_id, row.path)
        raise ReportError("report_damaged", f"{row.path} does not match its SHA-256")
    return data


def read_token(layout: Layout, row: LookupReport) -> bytes:
    """The stored time-stamp token of a report (``report_missing`` when there is none)."""
    info = row.timestamp or {}
    if row.timestamp_status != "stamped" or not info.get("token_file"):
        raise ReportError("report_missing", "the report has no time stamp")
    path = export_path(layout, str(info["token_file"]))
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise ReportError("report_missing", f"{info['token_file']} is missing") from exc
    if hashlib.sha256(data).hexdigest() != info.get("token_sha256"):
        raise ReportError("report_damaged", f"{info['token_file']} does not match its SHA-256")
    return data


def timestamp_plan(db: Session, config: Config) -> TimestampPlan:
    if not get_setting(db, TIMESTAMP_SETTING, False):
        return TimestampPlan(requested=False)
    return TimestampPlan(
        requested=True,
        provider=config.tsa.provider,
        legal_grade=config.tsa.legal_grade,
        server=config.tsa.host or None,
    )


def _person(db: Session, user_id: int | None, username: str) -> Person:
    user = db.get(User, user_id) if user_id is not None else None
    return Person(username=username, display_name=user.display_name if user else username)


def _lookup_facts(row: Lookup) -> dict[str, Any]:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat(),
        "finished_at": row.finished_at.isoformat() if row.finished_at else None,
        "authority": row.authority,
        "file_no": row.file_no,
        "request_date": row.request_date.isoformat(),
        "reason": row.reason,
        "time_input": (row.query or {}).get("time_input"),
    }


def _stamp(
    config: Config, layout: Layout, pdf_path: Path, plan: TimestampPlan
) -> tuple[str, dict[str, Any]]:
    """Time-stamp the stored PDF; ``("stamped" | "failed", facts)``. Never raises."""
    facts: dict[str, Any] = {
        "provider": plan.provider,
        "legal_grade": plan.legal_grade,
        "server": plan.server,
    }
    try:
        provider = make_provider(config, layout, secret_store(config))
        if provider.legal_grade != plan.legal_grade:  # pragma: no cover - same rule, defensive
            raise TsaError("the provider's legal grade differs from the one shown in the PDF")
        result = provider.stamp(pdf_path)
        token_file = result.token_path.relative_to(layout.exports).as_posix()
        token = result.token_path.read_bytes()
        check = verify_token(
            token,
            roots=trusted_roots(config, layout),
            file_path=pdf_path,
            # The site's EKU rule (K6), as for the days it seals.
            require_critical_eku=config.tsa.require_critical_eku,
        )
    except (TsaError, SecretStoreError, OSError, ValueError) as exc:
        log.warning("time stamp of %s failed: %s", pdf_path.name, exc)
        facts["error"] = str(exc)[:500]
        return "failed", facts
    info = result.info
    facts.update(
        serial=info.serial,
        gen_time=info.gen_time.isoformat(),
        tsa_name=info.tsa_name or None,
        policy=info.policy,
        token_file=token_file,
        token_sha256=hashlib.sha256(token).hexdigest(),
        verified=check.ok,
        errors=list(check.errors),
        root=check.details.get("root") or None,
    )
    return "stamped", facts


def create_report(
    db: Session,
    *,
    lookup: Lookup,
    generated_by: Person,
    generated_by_id: int | None,
    layout: Layout,
    config: Config,
    now: datetime | None = None,
) -> StoredReport:
    """Render, store and (optionally) time-stamp the report of ``lookup`` (caller holds the
    lookup row lock and commits)."""
    if lookup.outcome is None:
        raise ReportError("lookup_unfinished", f"lookup {lookup.id} has no outcome")
    outcome = LookupOutcome.from_dict(lookup.outcome)
    plan = timestamp_plan(db, config)
    name = report_name(lookup.id)
    relative = f"{REPORT_FOLDER}/{name}"
    try:
        inputs = build_inputs(
            lookup=_lookup_facts(lookup),
            outcome=outcome,
            operator=_person(db, lookup.user_id, lookup.username),
            generated_by=generated_by,
            generated_at=now or utc_now(),
            layout=layout,
            config=config,
            timestamp=plan,
            report_file=name,
        )
    except EvidenceReadError as exc:  # a passing server problem is not recorded for good
        log.error("lookup report %s: cited file cannot be read: %s", lookup.id, exc)
        code = "report_access_denied" if exc.access_denied else "report_evidence_unreadable"
        raise ReportError(code, str(exc), file=exc.file) from exc
    except SealInProgressError as exc:  # the nightly seal is moving a cited day: try again
        log.warning("lookup report %s: %s", lookup.id, exc)
        raise ReportError("report_seal_in_progress", str(exc), file=exc.file) from exc
    try:
        rendered = render(inputs)
    except LayoutError as exc:  # a value the layout cannot place: no report, nothing stored
        log.error("lookup report %s cannot be laid out: %s", lookup.id, exc)
        raise ReportError("report_render_failed", str(exc)[:500]) from exc
    path = export_path(layout, relative)
    atomic_write_bytes(path, rendered.pdf)
    status, stamp = ("not_requested", None)
    if plan.requested:
        status, stamp = _stamp(config, layout, path, plan)
    row = LookupReport(
        lookup_id=lookup.id,
        user_id=generated_by_id,
        username=generated_by.username,
        path=relative,
        sha256=rendered.sha256,
        size=len(rendered.pdf),
        pages=rendered.pages,
        version=__version__,
        inputs=inputs,
        timestamp_status=status,
        timestamp=stamp,
    )
    db.add(row)
    db.flush()
    return StoredReport(row=row, pdf=rendered.pdf, created=True)


def load_or_create(
    db: Session,
    *,
    lookup: Lookup,
    generated_by: Person,
    generated_by_id: int | None,
    layout: Layout,
    config: Config,
) -> StoredReport:
    """The stored report of ``lookup``; rendered and stored on the first call."""
    db.execute(select(Lookup.id).where(Lookup.id == lookup.id).with_for_update())
    existing = db.scalar(select(LookupReport).where(LookupReport.lookup_id == lookup.id))
    if existing is not None:
        return StoredReport(row=existing, pdf=read_stored(layout, existing), created=False)
    return create_report(
        db,
        lookup=lookup,
        generated_by=generated_by,
        generated_by_id=generated_by_id,
        layout=layout,
        config=config,
    )
