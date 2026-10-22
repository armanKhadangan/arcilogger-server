"""The one implementation of the hash-chained ARCILOGGER audit log (build specification 8.5).

Every writer goes through :func:`append_entry`: the API (``arcilogger_api.audit.record_audit``,
which adds the signed-in user, the request's address and the after-commit spool flush) and the
worker's jobs (:func:`append_audit`, actor ``system``: ``evidence.purge``, the import of the
scheduled sealing and retention runs). The worker may not import the API (the API depends on the
worker), so the chain step lives here, in the package both import. The spool projection
(:func:`flush_audit_spool`) is here too; only the API calls it (ADR-0007: the worker's service
account may not write ``spool/_audit``), and it copies the worker's entries with its own.

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

**Concurrency:** writers are serialised with a PostgreSQL transaction-level advisory lock
(:data:`AUDIT_LOCK_KEY`), so ids and the chain follow commit order, whichever process writes.
The caller's transaction must be **READ COMMITTED** (the PostgreSQL default): a REPEATABLE READ
snapshot taken before the lock would read a stale ``prev_hash``. ``audit_log.prev_hash`` is
unique, so a fork can never be stored.

**Spool:** :func:`flush_audit_spool` appends every committed entry not yet in the spool — under
a second advisory lock, idempotently, in id order, catching up anything an earlier failed flush
missed (watermark setting ``audit.spool_watermark``). Lines go to the file of the site day on
which they are written, so a day's file is never appended to after midnight, when the sealer
takes it. An incomplete last line (a write interrupted by a crash or a full disk) is cut off
before anything else happens (:func:`repair_torn_tail`).
"""

from __future__ import annotations

import gzip
import hashlib
import ipaddress
import json
import logging
import os
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path, PurePath
from typing import IO, Any, Final
from uuid import UUID

from sqlalchemy import Engine, func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from arcilogger_core.canonical import CanonicalJsonError, canonical_json
from arcilogger_core.iputil import canonical_ip
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.models import AuditLog, Setting
from arcilogger_db.settings import SYSTEM_AUTHOR

log = logging.getLogger("arcilogger.audit")

ENTRY_VERSION: Final = 1
GENESIS_HASH: Final = "0" * 64
#: pg_advisory_xact_lock keys ("ARCIAUDT" / "ARCISPOL" as signed 64-bit integers).
AUDIT_LOCK_KEY: Final = int.from_bytes(b"ARCIAUDT", "big") - 2**63
SPOOL_LOCK_KEY: Final = int.from_bytes(b"ARCISPOL", "big") - 2**63
WATERMARK_KEY: Final = "audit.spool_watermark"
#: ``actor`` of entries written by scheduled jobs (``actor_id`` NULL).
SYSTEM_ACTOR: Final = "system"
#: How many earlier days the duplicate guard looks at when today's file is still empty.
LOOKBACK_DAYS: Final = 7
_CHUNK: Final = 65536

Clock = Callable[[], datetime]
Appender = Callable[[Path, bytes], None]


# -- canonical form -------------------------------------------------------------------------------


def format_time(value: datetime) -> str:
    """UTC with microseconds — stable forever (no time-zone rules involved)."""
    if value.utcoffset() is None:
        raise ValueError("audit time must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def normalize_ip(value: str | None) -> str | None:
    """Canonical text of an IP address; ``None`` for anything else (never fails an entry)."""
    if value is None:
        return None
    try:
        return canonical_ip(value)
    except ValueError:
        log.warning("audit: %r is not an IP address; stored without address", value[:100])
        return None


def jsonable(value: Any) -> Any:
    """Convert common Python values for ``details`` (times → UTC ISO, UUID/IP/paths → str)."""
    if isinstance(value, datetime):
        return format_time(value)
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, ipaddress.IPv4Address | ipaddress.IPv6Address):
        return canonical_ip(str(value))
    if isinstance(value, UUID | PurePath):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = sorted(value, key=str) if isinstance(value, set | frozenset) else value
        return [jsonable(item) for item in items]
    return value


def has_nul(value: Any) -> bool:
    if isinstance(value, str):
        return "\x00" in value
    if isinstance(value, Mapping):
        return any(has_nul(k) or has_nul(v) for k, v in value.items())
    if isinstance(value, list):
        return any(has_nul(item) for item in value)
    return False


def canonical_entry(
    *,
    entry_id: int,
    time: datetime,
    actor_id: int | None,
    actor: str | None,
    action: str,
    target: str | None,
    details: Mapping[str, Any],
    ip: str | None,
) -> dict[str, Any]:
    return {
        "v": ENTRY_VERSION,
        "id": entry_id,
        "time": format_time(time),
        "actor_id": actor_id,
        "actor": actor,
        "action": action,
        "target": target,
        "details": dict(details),
        "ip": ip,
    }


def entry_hash(prev_hash: str, entry: Mapping[str, Any]) -> str:
    """``sha256(prev_hash || canonical_json(entry))`` — the chain rule of spec 8.5."""
    return hashlib.sha256(prev_hash.encode("ascii") + canonical_json(entry)).hexdigest()


def entry_of(row: AuditLog) -> dict[str, Any]:
    """The canonical entry of a stored row."""
    return canonical_entry(
        entry_id=row.id,
        time=row.time,
        actor_id=row.actor_id,
        actor=row.actor_username,
        action=row.action,
        target=row.target,
        details=row.details,
        ip=row.ip,
    )


def spool_line(row: AuditLog) -> bytes:
    return canonical_json({**entry_of(row), "prev_hash": row.prev_hash, "hash": row.hash}) + b"\n"


# -- the chain step -------------------------------------------------------------------------------


def append_entry(
    session: Session,
    *,
    actor_id: int | None,
    actor: str | None,
    action: str,
    target: str | None = None,
    details: Mapping[str, Any] | None = None,
    ip: str | None = None,
    clock: Clock = utc_now,
) -> AuditLog:
    """Append one entry to the chain in the caller's transaction (the lock is held until it
    ends; keep the transaction short after the call).

    Raises ``ValueError`` for an entry that cannot be stored exactly (no action, floats, NUL
    characters, non-string keys, huge integers) and ``RuntimeError`` outside READ COMMITTED.
    ``ip`` that is not an address is stored as ``null`` (logged), never failing the entry.
    """
    if not action:
        raise ValueError("action is required")
    clean = jsonable(dict(details or {}))
    try:
        canonical_json(clean)
    except CanonicalJsonError as exc:
        raise ValueError(f"audit details cannot be stored exactly: {exc}") from exc
    if has_nul(clean) or has_nul([action, target, actor]):
        raise ValueError("audit entries must not contain NUL characters")
    address = normalize_ip(ip)
    isolation = session.execute(text("SHOW transaction_isolation")).scalar_one()
    if isolation != "read committed":
        raise RuntimeError(
            f"the audit log needs a READ COMMITTED transaction (got {isolation}): a snapshot "
            "taken before the chain lock would fork the audit chain"
        )
    session.execute(select(func.pg_advisory_xact_lock(AUDIT_LOCK_KEY)))
    prev_hash = (
        session.scalar(select(AuditLog.hash).order_by(AuditLog.id.desc()).limit(1)) or GENESIS_HASH
    )
    entry_id = int(
        session.execute(
            text("SELECT nextval(pg_get_serial_sequence('audit_log', 'id'))")
        ).scalar_one()
    )
    at = clock()
    entry = canonical_entry(
        entry_id=entry_id,
        time=at,
        actor_id=actor_id,
        actor=actor,
        action=action,
        target=target,
        details=clean,
        ip=address,
    )
    row = AuditLog(
        id=entry_id,
        time=at,
        actor_id=actor_id,
        actor_username=actor,
        action=action,
        target=target,
        details=clean,
        ip=address,
        prev_hash=prev_hash,
        hash=entry_hash(prev_hash, entry),
    )
    session.add(row)
    session.flush()
    return row


def append_audit(
    session: Session,
    *,
    action: str,
    actor: str = SYSTEM_ACTOR,
    actor_id: int | None = None,
    target: str | None = None,
    details: Mapping[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    """An entry of a scheduled job (actor ``system`` unless given) — :func:`append_entry`.

    Used by the worker, which cannot write the spool: the API's periodic flush copies the entry
    into the day's spool file, so it is sealed like every other entry.
    """
    return append_entry(
        session,
        actor_id=actor_id,
        actor=actor,
        action=action,
        target=target,
        details=details,
        ip=ip,
    )


# -- the spool projection -------------------------------------------------------------------------


def _line_id(line: bytes) -> int | None:
    """``id`` of one spool line, ``None`` when it is not an audit entry."""
    if not line.strip():
        return None
    try:
        value = json.loads(line).get("id")
    except (ValueError, AttributeError):
        return None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _complete_length(handle: IO[bytes], size: int) -> int:
    """Length of the file up to and including its last ``\\n`` (0 when there is none)."""
    position = size
    while position > 0:
        start = max(0, position - _CHUNK)
        handle.seek(start)
        index = handle.read(position - start).rfind(b"\n")
        if index >= 0:
            return start + index + 1
        position = start
    return 0


def repair_torn_tail(path: Path) -> int:
    """Cut an incomplete last line (no ``\\n``) off a spool file; returns the bytes removed.

    Such a fragment is what a crash or a full disk leaves in the middle of an append. It was
    never a complete entry — the entry is in the database and the flush writes it again — and
    left in place it would merge with the next line into one corrupt line.
    """
    if not path.exists():
        return 0
    with path.open("r+b") as handle:
        size = handle.seek(0, os.SEEK_END)
        if size == 0:
            return 0
        handle.seek(size - 1)
        if handle.read(1) == b"\n":
            return 0
        keep = _complete_length(handle, size)
        handle.truncate(keep)
        handle.flush()
        os.fsync(handle.fileno())
    log.warning(
        "audit spool %s: removed an incomplete last line (%d bytes) left by an interrupted "
        "write; the entry is written again from the database",
        path.name,
        size - keep,
    )
    return size - keep


def last_line_id(path: Path) -> int | None:
    """``id`` of the last complete, valid line of a spool file (``None`` when there is none).

    An incomplete tail is ignored; the file is read backwards in chunks, so long lines and
    large files are handled without reading everything.
    """
    if not path.exists():
        return None
    with path.open("rb") as handle:
        position = _complete_length(handle, handle.seek(0, os.SEEK_END))
        carry = b""
        while position > 0:
            start = max(0, position - _CHUNK)
            handle.seek(start)
            lines = (handle.read(position - start) + carry).split(b"\n")
            # lines[0] may be the end of a line that starts before this chunk.
            carry = lines.pop(0) if start > 0 else b""
            for line in reversed(lines):
                if (entry_id := _line_id(line)) is not None:
                    return entry_id
            position = start
    return None


def last_gz_line_id(path: Path) -> int | None:
    """``id`` of the last valid line of a sealed ``audit.jsonl.gz`` (small files: read whole)."""
    for line in reversed(gzip.decompress(path.read_bytes()).split(b"\n")):
        if (entry_id := _line_id(line)) is not None:
            return entry_id
    return None


def last_spooled_id(layout: Layout, today: date) -> int | None:
    """Highest id already written: today's file, else the latest earlier spool or sealed file."""
    for back in range(LOOKBACK_DAYS + 1):
        day = today - timedelta(days=back)
        last = last_line_id(layout.audit_spool(day))
        if last is not None:
            return last
        sealed = layout.vault_day(day) / "audit.jsonl.gz"
        if sealed.exists() and (last := last_gz_line_id(sealed)) is not None:
            return last
    return None


def append_bytes(path: Path, data: bytes) -> None:
    """Append ``data`` and fsync (the spool file is the copy the sealer takes)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def flush_audit_spool(
    engine: Engine,
    layout: Layout,
    *,
    append: Appender = append_bytes,
    clock: Clock = utc_now,
) -> int:
    """Append every committed entry not yet in the spool, in id order; returns lines written.

    Lines go to the file of the site day on which they are **written** — in practice the day
    of the entry, except for entries committed in the last moments before midnight or caught up
    after a failed flush. A day's file is therefore never touched after that day ended, so the
    sealer (which reads it after midnight) can never race with a writer.
    """
    with engine.begin() as connection, Session(bind=connection) as session:
        session.execute(select(func.pg_advisory_xact_lock(SPOOL_LOCK_KEY)))
        today = site_day(clock())
        # Unsealed files of the last days: a torn tail must go before anything is appended
        # (today) or the sealer takes the file (earlier days; it refuses a torn file).
        for back in range(LOOKBACK_DAYS + 1):
            repair_torn_tail(layout.audit_spool(today - timedelta(days=back)))
        watermark = int(
            session.scalar(select(Setting.value).where(Setting.key == WATERMARK_KEY)) or 0
        )
        rows = session.scalars(
            select(AuditLog).where(AuditLog.id > watermark).order_by(AuditLog.id)
        ).all()
        if not rows:
            return 0
        # Guard against a crash after appending but before the watermark was saved.
        last = last_spooled_id(layout, today)
        pending = [row for row in rows if last is None or row.id > last]
        if pending:
            append(layout.audit_spool(today), b"".join(spool_line(row) for row in pending))
        statement = insert(Setting).values(
            key=WATERMARK_KEY, value=rows[-1].id, updated_by=SYSTEM_AUTHOR
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[Setting.key],
                set_={"value": statement.excluded.value, "updated_at": func.now()},
            )
        )
        session.flush()
    return len(pending)


# -- verification ---------------------------------------------------------------------------------


@dataclass
class AuditVerification:
    ok: bool = True
    checked: int = 0
    last_hash: str | None = None
    first_bad_id: int | None = None
    errors: list[str] = field(default_factory=list)

    def _fail(self, entry_id: int | None, message: str) -> None:
        self.ok = False
        if self.first_bad_id is None:
            self.first_bad_id = entry_id
        if len(self.errors) < 100:
            self.errors.append(message)


def verify_audit_chain(session: Session) -> AuditVerification:
    """Recompute the whole chain from the database (spec 8.5: verified when the page loads)."""
    result = AuditVerification()
    previous = GENESIS_HASH
    for row in session.scalars(select(AuditLog).order_by(AuditLog.id)).yield_per(1000):
        result.checked += 1
        if row.prev_hash != previous:
            result._fail(row.id, f"#{row.id}: prev_hash does not match the previous entry")
        if entry_hash(row.prev_hash, entry_of(row)) != row.hash:
            result._fail(row.id, f"#{row.id}: hash does not match the entry (modified?)")
        previous = row.hash
    result.last_hash = previous if result.checked else None
    return result


def verify_audit_lines(lines: Iterable[bytes], prev_hash: str | None = None) -> AuditVerification:
    """Verify spool/vault lines; ``prev_hash=None`` starts from the first line's ``prev_hash``."""
    result = AuditVerification()
    previous = prev_hash
    for number, raw in enumerate(lines, start=1):
        if not raw.strip():
            continue
        try:
            data = json.loads(raw)
            stored_prev, stored_hash = data.pop("prev_hash"), data.pop("hash")
        except (ValueError, KeyError, AttributeError):
            result._fail(None, f"line {number}: not an audit entry")
            continue
        result.checked += 1
        entry_id = data.get("id")
        if previous is not None and stored_prev != previous:
            result._fail(entry_id, f"line {number}: prev_hash does not match the previous line")
        try:
            recomputed = entry_hash(str(stored_prev), data)
        except (CanonicalJsonError, UnicodeEncodeError):
            recomputed = ""
        if recomputed != stored_hash:
            result._fail(entry_id, f"line {number}: hash does not match the entry (modified?)")
        if canonical_json({**data, "prev_hash": stored_prev, "hash": stored_hash}) + b"\n" != (
            raw if raw.endswith(b"\n") else raw + b"\n"
        ):
            result._fail(entry_id, f"line {number}: not in canonical form")
        previous = stored_hash
    result.last_hash = previous if result.checked else None
    return result
