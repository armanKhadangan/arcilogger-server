"""Hash-chained ARCILOGGER audit log (build specification 8.5) — the API's writer.

The chain itself (canonical entry, hash, advisory lock, READ COMMITTED check, spool projection
and verification) has **one** implementation, :mod:`arcilogger_db.audit`, shared with the
worker's jobs; this module re-exports it under the names the API has always used and adds what
only the API does: the signed-in user as actor, the request's session and the after-commit
spool flush.

Every entry is one row of ``audit_log`` **and** one line of the day's audit spool file
``spool/_audit/<YYYY-MM-DD>.jsonl``, which the nightly sealer compresses into
``vault/<Y>/<M>/<D>/audit.jsonl.gz`` and covers by the manifest (it records the last line's
``hash``).

**Canonical entry** (the hashed object; RFC 8785, ``arcilogger_core.canonical.canonical_json``)::

    {"v": 1, "id": 42, "time": "2026-10-12T15:19:07.123456+00:00", "actor_id": 3,
     "actor": "tolga", "action": "lookup.run", "target": "lookup:17",
     "details": {...}, "ip": "172.20.10.37"}

* ``time`` is UTC, ISO-8601 with microseconds; ``actor_id``, ``actor``, ``target``, ``ip`` may be
  ``null``; ``details`` is a JSON object without floats (canonical JSON forbids them) and without
  NUL characters.
* ``hash = sha256(prev_hash || canonical_json(entry))`` as lower-case hex, where ``prev_hash`` is
  the 64 hex characters (ASCII) of the previous entry's hash, ``"0" * 64`` for the first entry.
* A spool line is ``canonical_json({**entry, "prev_hash": …, "hash": …})`` + ``"\\n"``; to verify a
  line, drop ``prev_hash`` and ``hash`` from the object and recompute.

**Concurrency:** all writers (this API and the worker) are serialised with a PostgreSQL
transaction-level advisory lock, so ids and the chain follow commit order. :func:`record_audit`
joins the caller's transaction (keep it short after the call: the lock is held until commit)
and requires **READ COMMITTED** (the PostgreSQL default): a REPEATABLE READ snapshot taken before
the lock would read a stale ``prev_hash``. ``audit_log.prev_hash`` is unique, so a fork can
never be stored.

**Spool:** after the commit — once the session has returned its connection to the pool, so a
request never holds two pooled connections — the new entries are appended by
:func:`flush_audit_spool` — under a second advisory lock, idempotently, in id order, catching
up anything an earlier failed flush missed (watermark setting ``audit.spool_watermark``) and
the entries the worker wrote (it may not write the spool, ADR-0007). The API also flushes at
start-up and every minute (:func:`run_periodic_flush`), so an entry never waits for the next
audited action. Lines go to the file of the day on which they are written, so a day's file is
never appended to after midnight, when the sealer takes it. An incomplete last line (a write
interrupted by a crash or a full disk) is cut off before anything else happens
(:func:`repair_torn_tail`): it never was a complete entry, and that entry is still in the
database and is written again in full. The sealer refuses a file whose last line is incomplete
and retries later.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final, Protocol

from sqlalchemy import Connection, Engine, event
from sqlalchemy.orm import Session, SessionTransaction

from arcilogger_api.deps import LAYOUT_INFO_KEY
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now
from arcilogger_db import audit as chain
from arcilogger_db.audit import (
    AUDIT_LOCK_KEY,
    ENTRY_VERSION,
    GENESIS_HASH,
    LOOKBACK_DAYS,
    SPOOL_LOCK_KEY,
    WATERMARK_KEY,
    AuditVerification,
    canonical_entry,
    entry_hash,
    entry_of,
    format_time,
    jsonable,
    normalize_ip,
    repair_torn_tail,
    spool_line,
    verify_audit_chain,
    verify_audit_lines,
)
from arcilogger_db.models import AuditLog

__all__ = [
    "AUDIT_LOCK_KEY",
    "ENTRY_VERSION",
    "FLUSH_INTERVAL_SECONDS",
    "GENESIS_HASH",
    "LOOKBACK_DAYS",
    "SPOOL_LOCK_KEY",
    "WATERMARK_KEY",
    "Actor",
    "AuditVerification",
    "SystemActor",
    "canonical_entry",
    "entry_hash",
    "entry_of",
    "flush_audit_spool",
    "format_time",
    "jsonable",
    "normalize_ip",
    "record_audit",
    "repair_torn_tail",
    "run_periodic_flush",
    "spool_line",
    "verify_audit_chain",
    "verify_audit_lines",
]

log = logging.getLogger("arcilogger.audit")

#: Seconds between two background flushes of the API (catch-up after a failed write).
FLUSH_INTERVAL_SECONDS: Final = 60.0
_PENDING_FLUSH: Final = "arci.audit.pending_flush"
_COMMITTED_FLUSH: Final = "arci.audit.committed_flush"

#: The spool writer and readers of :mod:`arcilogger_db.audit`, under the names this module has
#: always had. :func:`flush_audit_spool` and :func:`record_audit` look ``_append`` and
#: ``utc_now`` up here at call time, so a test can simulate a full disk or another clock.
_append = chain.append_bytes
_last_line_id = chain.last_line_id
_last_gz_line_id = chain.last_gz_line_id
_last_spooled_id = chain.last_spooled_id


class Actor(Protocol):
    """Who did it: :class:`~arcilogger_api.security.CurrentUser` or :class:`SystemActor`."""

    @property
    def id(self) -> int | None: ...

    @property
    def username(self) -> str: ...


@dataclass(frozen=True)
class SystemActor:
    """Actions without a signed-in user (failed logins, jobs); ``id`` stays ``None``."""

    username: str = chain.SYSTEM_ACTOR
    id: int | None = None


# -- writing --------------------------------------------------------------------------------------


def record_audit(
    session: Session,
    *,
    actor: Actor | None,
    action: str,
    target: str | None = None,
    details: Mapping[str, Any] | None = None,
    ip: str | None = None,
    layout: Layout | None = None,
) -> AuditLog:
    """Append an entry in the caller's transaction; it reaches the spool after the commit.

    ``layout`` defaults to the API's (``Session.info``). Raises ``ValueError`` for details that
    cannot be stored exactly (floats, NUL characters, non-string keys, huge integers) and
    ``RuntimeError`` outside READ COMMITTED (:func:`arcilogger_db.audit.append_entry`).
    """
    if not action:
        raise ValueError("action is required")
    spool = layout or session.info.get(LAYOUT_INFO_KEY)
    if not isinstance(spool, Layout):
        raise RuntimeError("no audit spool: pass layout= or use the API session factory")
    who = actor or SystemActor()
    row = chain.append_entry(
        session,
        actor_id=who.id,
        actor=who.username,
        action=action,
        target=target,
        details=details,
        ip=ip,
        clock=utc_now,
    )
    session.info[_PENDING_FLUSH] = spool
    return row


@event.listens_for(Session, "after_commit")
def _mark_committed(session: Session) -> None:
    """The entries are committed: flush them once the connection is back in the pool.

    ``after_commit`` runs while the session still holds its connection (SQLAlchemy releases it
    afterwards, in ``SessionTransaction.close``). Flushing here took a **second** pooled
    connection per audited request: with a pool of one the request waited the whole pool
    timeout and the flush failed (B5 report). The flush therefore runs in
    :func:`_flush_after_release`. A savepoint commit is not a commit of the entries.
    """
    if session.in_nested_transaction():
        return
    spool = session.info.pop(_PENDING_FLUSH, None)
    if spool is not None:
        session.info[_COMMITTED_FLUSH] = spool


@event.listens_for(Session, "after_transaction_end")
def _flush_after_release(session: Session, transaction: SessionTransaction) -> None:
    """Append committed entries to the spool after the outermost transaction ended.

    At this point the session has returned its connection to the pool (unless it is bound to
    an external connection), so the flush's own connection is the only one this thread uses.
    """
    if transaction.parent is not None:
        return
    spool = session.info.pop(_COMMITTED_FLUSH, None)
    if spool is None:
        return
    bind = session.get_bind()
    engine = bind.engine if isinstance(bind, Connection) else bind
    try:
        flush_audit_spool(engine, spool)
    except Exception:  # the next flush catches up; the database row is the source of truth
        log.exception("audit spool flush failed; it will be retried with the next entry")


@event.listens_for(Session, "after_rollback")
def _forget_after_rollback(session: Session) -> None:
    # A savepoint rollback keeps the outer transaction's entries pending (an entry written
    # inside the savepoint is gone; the flush after the outer commit then finds nothing new).
    if not session.in_nested_transaction():
        session.info.pop(_PENDING_FLUSH, None)


def _write_lines(path: Path, data: bytes) -> None:
    _append(path, data)


def flush_audit_spool(engine: Engine, layout: Layout) -> int:
    """Append every committed entry not yet in the spool, in id order; returns lines written.

    :func:`arcilogger_db.audit.flush_audit_spool` with this module's writer and clock: lines go
    to the file of the site day on which they are **written**, so a day's file is never touched
    after that day ended and the sealer can never race with a writer.
    """
    return chain.flush_audit_spool(engine, layout, append=_write_lines, clock=_now)


def _now() -> datetime:
    return utc_now()


async def run_periodic_flush(
    engine: Callable[[], Engine],
    layout: Layout,
    stop: asyncio.Event,
    *,
    interval: float = FLUSH_INTERVAL_SECONDS,
) -> None:
    """Flush at once and then every ``interval`` seconds until ``stop`` is set (API lifespan).

    Catches up entries whose after-commit flush failed even when no further audited action
    follows (e.g. overnight), so they reach that day's spool file before the sealer runs. A
    flush in progress always finishes; errors are logged and retried at the next interval.
    """
    while not stop.is_set():
        try:
            written = await asyncio.to_thread(flush_audit_spool, engine(), layout)
            if written:
                log.info("audit spool: caught up %d entries", written)
        except Exception:  # the next round retries; the database row is the source of truth
            log.exception("periodic audit spool flush failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except TimeoutError:
            continue
