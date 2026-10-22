"""``/api/v1/audit`` — Denetim kaydı, zincir doğrulaması (spec 8.5, 12.3 page 14).

Read-only (``audit.read``: administrators and auditors). The entries carry person-level data
(spec 3.3): lookup queries with IP and MAC addresses, result ids of leases and persons, raw-line
references, user names with IP addresses — so ``GET /audit`` also needs the accepted KVKK notice
(:func:`arcilogger_api.auth.privacy.require_privacy_notice`), like every other person-level
endpoint; ``GET /audit/verify`` returns only counts and hashes. The "Denetim Kaydı" page lists the
entries newest first and calls ``/audit/verify`` when it loads (spec 8.5: "verifies the chain on
load"); the verification recomputes every hash from the database
(:func:`arcilogger_api.audit.verify_audit_chain`). Reading the audit log is not itself audited
(it would add an entry on every page load); every action it shows is.

Pagination is by entry id (ids follow commit order): ``cursor`` = the ``next_cursor`` of the
previous page returns the older entries.

**Pseudonymous readers:** the target of ``usage.view`` and of a one-subject ``usage.export`` is
the real subject key (``usage:device:<mac>``). A reader who sees pseudonyms on the Kullanım pages
(the auditor while ``usage.pseudonymize_anonymous_role`` is on) gets it as the subject's
pseudonym token instead (``usage:d:<token>``, ``target_masked: true``;
:func:`arcilogger_api.routers.usage.audit_target_mask`), so its own entries do not tie a pseudonym
to a MAC, an IP or a person id; the ``target`` filter compares with what it sees. The stored entry
is unchanged and its hash covers the real key: with the hash before it, a list of candidates
(person ids, the MACs and IPs that search shows) could be tried until one gives the stored hash.
So this reader gets no hashes: ``prev_hash`` and ``hash`` are ``null`` on every entry (every later
hash depends on the masked one) and ``/audit/verify`` answers ``last_hash: null``; the chain is
still verified on the server. Administrators and readers who see names get the stored key and the
hashes.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from pydantic import AwareDatetime, BaseModel
from sqlalchemy import select

from arcilogger_api.audit import verify_audit_chain
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.deps import ConfigDep, DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.routers.usage import AUDIT_TARGET, audit_target_mask, sees_pseudonyms
from arcilogger_api.security import CurrentUser, require
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import AuditLog

router = APIRouter(prefix="/audit", tags=["audit"])

ReaderDep = Annotated[CurrentUser, Depends(require("audit.read"))]
#: The entries themselves are person-level data (module docstring): the KVKK notice is required.
EntryReaderDep = Annotated[CurrentUser, Depends(require_privacy_notice("audit.read"))]
#: Largest entry id (PostgreSQL ``bigint``): a larger cursor answers 422, not a database error.
MAX_ID = 2**63 - 1
#: Messages returned by /audit/verify (the full list can be long after heavy tampering).
MAX_ERRORS = 20


class AuditEntry(BaseModel):
    id: int
    time: datetime
    actor_id: int | None
    actor: str | None
    action: str
    target: str | None
    #: ``target`` is a usage subject's pseudonym token, not the stored key (module docstring).
    target_masked: bool
    details: dict[str, Any]
    ip: str | None
    #: ``null`` for a reader who gets masked targets (module docstring), on every entry.
    prev_hash: str | None
    hash: str | None


class AuditPage(BaseModel):
    items: list[AuditEntry]
    next_cursor: int | None


@router.get("")
def list_entries(
    db: DbSession,
    user: EntryReaderDep,
    config: ConfigDep,
    time_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    time_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    actor: Annotated[str | None, Query(max_length=64)] = None,
    action: Annotated[str | None, Query(max_length=100, description="prefix, e.g. auth.")] = None,
    target: Annotated[str | None, Query(max_length=200)] = None,
    cursor: Annotated[int | None, Query(ge=1, le=MAX_ID)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> AuditPage:
    """Entries newest first; ``from`` inclusive, ``to`` exclusive (times with offset)."""
    if time_from is not None and time_to is not None and time_from >= time_to:
        raise ApiError(422, "validation_error", details=[{"loc": ["query", "to"]}])
    query = select(AuditLog).order_by(AuditLog.id.desc()).limit(limit + 1)
    if time_from is not None:
        query = query.where(AuditLog.time >= time_from)
    if time_to is not None:
        query = query.where(AuditLog.time < time_to)
    if actor:
        query = query.where(AuditLog.actor_username == actor)
    if action:
        query = query.where(AuditLog.action.startswith(action, autoescape=True))
    mask = audit_target_mask(db, user, config)
    if target and mask is not None and target.startswith(AUDIT_TARGET):
        # Compared with the masked targets: a real subject key finds nothing for this reader.
        stored = db.scalars(
            select(AuditLog.target)
            .where(AuditLog.target.startswith(AUDIT_TARGET, autoescape=True))
            .distinct()
        )
        query = query.where(AuditLog.target.in_([t for t in stored if t and mask(t) == target]))
    elif target:
        query = query.where(AuditLog.target == target)
    if cursor is not None:
        query = query.where(AuditLog.id < cursor)
    rows = list(db.scalars(query))
    more = len(rows) > limit
    rows = rows[:limit]
    return AuditPage(
        items=[_entry(row, mask) for row in rows],
        next_cursor=rows[-1].id if more else None,
    )


def _entry(row: AuditLog, mask: Callable[[str], str] | None) -> AuditEntry:
    target, masked = row.target, False
    if mask is not None and target is not None and target.startswith(AUDIT_TARGET):
        target, masked = mask(target), True
    # A hash would let this reader test guesses of a masked target (module docstring).
    hidden = mask is not None
    return AuditEntry(
        id=row.id,
        time=row.time,
        actor_id=row.actor_id,
        actor=row.actor_username,
        action=row.action,
        target=target,
        target_masked=masked,
        details=row.details,
        ip=row.ip,
        prev_hash=None if hidden else row.prev_hash,
        hash=None if hidden else row.hash,
    )


class ChainStatus(BaseModel):
    ok: bool
    #: Number of entries checked (the whole log).
    entries: int
    #: First entry whose hash or link does not match (``null`` when intact).
    first_bad_id: int | None
    #: ``null`` also for a reader who sees usage pseudonyms (module docstring).
    last_hash: str | None
    errors: list[str]
    verified_at: datetime


@router.get("/verify")
def verify_chain(db: DbSession, user: ReaderDep) -> ChainStatus:
    """Recompute the hash chain of the whole audit log."""
    result = verify_audit_chain(db)
    return ChainStatus(
        ok=result.ok,
        entries=result.checked,
        first_bad_id=result.first_bad_id,
        # Two answers around one usage view would let a guess of its target be tested.
        last_hash=None if sees_pseudonyms(db, user) else result.last_hash,
        errors=result.errors[:MAX_ERRORS],
        verified_at=utc_now(),
    )
