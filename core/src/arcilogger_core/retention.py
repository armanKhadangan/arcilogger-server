"""Retention and deletion ("imha") of sealed days (build specification 8.4).

Disabled by default (``retention.enabled = false``); the retention period can never be set below
730 days. When enabled, days older than ``retention.days`` are:

1. verified once more (a day that does not verify is never deleted — it raises a critical alert);
2. described in a **deletion record** (canonical JSON: days, manifest/token hashes, file hashes,
   reason, time) that is time-stamped and verified like a manifest;
3. unlocked and deleted, oldest first; for each day its data files go first, then the **chain
   anchor** moves to that day (hashes of its manifest and token), then its manifest and token —
   so the chain stays verifiable from the oldest remaining day, also when a deletion stops
   half-way (see :func:`_finish`).

The next day's manifest lists the deletion record, so the deletion itself is sealed into the chain.

**A record is only ever under its final name when its time stamp verified.** The record and its
token are written as ``<date>-imha.json.part`` / ``….part.zd``; only after the token verified
against the pinned roots are they renamed — the token first, then the record — and only then is
anything deleted. A failed stamp (time-stamp service unreachable, no credit, a rejection) or a
token that does not verify removes both files and raises the alert ``R20-retention``; nothing
is deleted and the anchor is untouched. So everything that reads
``vault/deletions/*-imha.json`` (the next manifest, the sealing calendar, the worker's audit
import, evidence packages) never sees a record that looks valid but is not. Leftovers of an
interrupted run (power loss while stamping or renaming) are tidied up by the next run
(:func:`recover`): a ``.part`` whose token is complete and verifies is promoted, anything else
is removed.

A run that stopped **after** the record was promoted (the anchor not written yet, or a day that
could not be unlocked/deleted) is finished by the next run before it plans anything new
(:func:`unfinished_record`): the anchor is set from that record and the days it lists that are
still in the vault are deleted — after checking that their manifests and tokens are still the
ones the record describes. Until then the worker reports the record as ``incomplete`` (never as
a deletion).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from cryptography import x509

from arcilogger_core.alerts import Severity, raise_alert
from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import sealed_days, verify_day
from arcilogger_core.config import Config
from arcilogger_core.fsutil import atomic_write_bytes, atomic_write_text, fsync_dir
from arcilogger_core.immutability import is_locked, lock, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.seal import sha256_file
from arcilogger_core.timeutil import isoformat_site, site_day, utc_now
from arcilogger_core.tsa.base import TimestampProvider, TsaError
from arcilogger_core.tsa.client import token_path_for
from arcilogger_core.tsa.verify import verify_token

log = logging.getLogger("arcilogger.retention")

DELETION_SCHEMA = "arcilogger.deletion/1"
REASON = (
    "Saklama süresi doldu (İnternet Toplu Kullanım Sağlayıcıları Hakkında Yönetmelik md. 4/1-b: "
    "en az 2 yıl)."
)
ALERT_RULE: Final = "R20-retention"
RECORD_SUFFIX: Final = "-imha.json"
#: Suffix of a record (and its token) that is not complete yet.
PART_SUFFIX: Final = ".part"


@dataclass
class RetentionResult:
    enabled: bool
    cutoff: date | None = None
    candidates: list[date] = field(default_factory=list)
    deleted: list[date] = field(default_factory=list)
    record: str | None = None
    error: str | None = None
    #: Leftovers of an interrupted run that were removed (``.part`` files, orphaned tokens).
    discarded: list[str] = field(default_factory=list)
    #: A record of an interrupted run that this run finished (its days deleted now).
    resumed: str | None = None


def plan(layout: Layout, config: Config, today: date) -> tuple[date, list[date]]:
    cutoff = today - timedelta(days=config.retention.days)
    return cutoff, [day for day in sealed_days(layout) if day < cutoff]


def records(layout: Layout) -> list[Path]:
    """Deletion records under their final name, oldest first (since the ``.part`` naming only
    complete ones: stamped and verified; an earlier version may have left an unstamped one)."""
    folder = layout.deletions
    return sorted(folder.glob(f"*{RECORD_SUFFIX}")) if folder.is_dir() else []


# -- leftovers of an interrupted run --------------------------------------------------------------


def _remove(path: Path, removed: list[str]) -> None:
    if not path.exists():
        return
    if is_locked(path):
        unlock(path)
    path.unlink()
    removed.append(path.name)


def recover(
    layout: Layout, roots: Sequence[x509.Certificate], *, require_critical_eku: bool = True
) -> list[str]:
    """Tidy up what an interrupted run left in ``vault/deletions``; returns the removed names.

    * ``X-imha.json.part`` with its final token ``X-imha.json.zd`` (the run stopped between the
      two renames): promoted when that token verifies against the part, else both removed;
    * any other ``….part`` and ``….part.zd``: removed (never stamped, or not verified);
    * a final token without its record and without a part: removed.
    """
    folder = layout.deletions
    removed: list[str] = []
    if not folder.is_dir():
        return removed
    for part in sorted(folder.glob(f"*{RECORD_SUFFIX}{PART_SUFFIX}")):
        final = part.with_name(part.name.removesuffix(PART_SUFFIX))
        token = token_path_for(final)
        if not final.exists() and token.is_file():
            check = verify_token(
                token.read_bytes(),
                roots=roots,
                file_path=part,
                require_critical_eku=require_critical_eku,
            )
            if check.ok:
                _replace(part, final)
                for path in (final, token):
                    if not is_locked(path):
                        lock(path)
                log.warning("retention: completed the promotion of %s (interrupted run)", final)
                continue
            _remove(token, removed)
        _remove(part, removed)
    for token in sorted(folder.glob(f"*{RECORD_SUFFIX}{PART_SUFFIX}.zd")):
        _remove(token, removed)
    for token in sorted(folder.glob(f"*{RECORD_SUFFIX}.zd")):
        if not token.with_name(token.name.removesuffix(".zd")).exists():
            _remove(token, removed)
    if removed:
        log.warning("retention: removed leftovers of an interrupted run: %s", ", ".join(removed))
    return removed


def _replace(source: Path, target: Path) -> None:
    source.replace(target)
    fsync_dir(target.parent)


def _listed_days(record: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(record.read_bytes())
    except (OSError, ValueError):
        return []
    entries = data.get("days") if isinstance(data, dict) else None
    return [entry for entry in entries or [] if isinstance(entry, dict)]


def unfinished_record(
    layout: Layout, roots: Sequence[x509.Certificate], *, require_critical_eku: bool = True
) -> Path | None:
    """The newest record whose time stamp verifies and that still lists a day in the vault.

    A record without a verifying token (left by a version before the ``.part`` naming) is
    never finished: it never authorised a deletion.
    """
    for record in reversed(records(layout)):
        listed = []
        for entry in _listed_days(record):
            try:
                listed.append(date.fromisoformat(str(entry.get("day"))))
            except ValueError:
                continue
        if not any(layout.manifest(day).exists() for day in listed):
            continue
        token = token_path_for(record)
        if (
            token.is_file()
            and verify_token(
                token.read_bytes(),
                roots=roots,
                file_path=record,
                require_critical_eku=require_critical_eku,
            ).ok
        ):
            return record
        log.warning("retention: %s is not stamped validly; its days are kept", record.name)
    return None


# -- the run --------------------------------------------------------------------------------------


def _record_path(layout: Layout, today: date) -> Path:
    """``<today>-imha.json``; a second record on the same day gets ``-2``, ``-3`` …"""
    path = layout.deletions / f"{today.isoformat()}{RECORD_SUFFIX}"
    number = 2
    while path.exists() or token_path_for(path).exists():
        path = layout.deletions / f"{today.isoformat()}-{number}{RECORD_SUFFIX}"
        number += 1
    return path


def _fail(
    layout: Layout,
    result: RetentionResult,
    message: str,
    *,
    severity: Severity = "critical",
    facts: dict[str, Any] | None = None,
) -> RetentionResult:
    result.error = message
    raise_alert(layout.alerts, ALERT_RULE, severity, message, facts or {})
    log.error("retention: %s", message)
    return result


def _write_anchor(layout: Layout, record: Path, last: dict[str, Any]) -> None:
    anchor = {
        "day": last["day"],
        "manifest_sha256": last["manifest_sha256"],
        "token_sha256": last["token_sha256"],
        "record": record.name,
    }
    data = canonical_json(anchor)
    if layout.chain_anchor.exists():
        if layout.chain_anchor.read_bytes() == data:
            return
        if is_locked(layout.chain_anchor):
            unlock(layout.chain_anchor)
    atomic_write_bytes(layout.chain_anchor, data)
    lock(layout.chain_anchor)


def _unlink(path: Path) -> None:
    if is_locked(path):
        unlock(path)
    path.unlink()


def _delete_data(layout: Layout, day: date) -> None:
    """Delete the day's sealed files (logs, audit file) — not yet its manifest and token."""
    folder = layout.vault_day(day)
    if not folder.is_dir():
        return
    seal = {layout.manifest(day).name, layout.token(day).name}
    for path in sorted(folder.iterdir()):
        if path.name not in seal:
            _unlink(path)


def _delete_day(layout: Layout, day: date, record: Path) -> None:
    """Delete what is left of the day (its manifest and token) and mark its seal state."""
    folder = layout.vault_day(day)
    if folder.is_dir():
        for path in sorted(folder.iterdir()):
            _unlink(path)
        folder.rmdir()
        for parent in (folder.parent, folder.parent.parent):
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()
    state_path = layout.seal_state(day)
    if state_path.exists():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state.update(status="deleted", deleted_by=record.name)
        atomic_write_text(state_path, json.dumps(state, ensure_ascii=False, indent=2) + "\n")


def _finish(layout: Layout, record: Path, result: RetentionResult) -> bool:
    """Delete every day ``record`` lists (oldest first), moving the chain anchor along.

    Per day: its data files, then the anchor (set to that day), then its manifest and token.
    The day after it links to the day's manifest while that exists and to the anchor once it
    is gone, so the remaining chain verifies at every step: a day that could not be deleted at
    all (e.g. a file in use on Windows) leaves the anchor on the day before and verifies as it
    is; a day whose deletion stopped in the middle is incomplete (and reported so) until the
    next run finishes it. A day whose manifest or token is no longer the one the record
    describes is not deleted (critical alert). Returns ``False`` after a failure
    (``result.error`` set).
    """
    entries = _listed_days(record)
    if not entries:
        _fail(
            layout,
            result,
            f"{record.name}: imha tutanağında hiç gün yok; hiçbir şey silinmedi "
            "[the deletion record lists no day; nothing deleted]",
            facts={"record": record.name},
        )
        return False
    for entry in entries:
        day = date.fromisoformat(str(entry["day"]))
        manifest, token = layout.manifest(day), layout.token(day)
        present = manifest.exists()
        if present and (
            not token.exists()
            or sha256_file(manifest) != entry.get("manifest_sha256")
            or sha256_file(token) != entry.get("token_sha256")
        ):
            _fail(
                layout,
                result,
                f"{day} günü, {record.name} yazıldıktan sonra değişmiş; silinmedi "
                f"[{day} changed after {record.name} was written; not deleted]",
                facts={"day": day.isoformat(), "record": record.name},
            )
            return False
        try:
            if present:
                _delete_data(layout, day)
                _write_anchor(layout, record, entry)
            # Also marks the state of a day an interrupted run already deleted.
            _delete_day(layout, day, record)
        except OSError as exc:
            _fail(
                layout,
                result,
                f"{day} gününün silinmesi yarıda kaldı ({exc}); bir sonraki çalıştırma "
                f"{record.name} tutanağını tamamlar [deleting {day} stopped; the next run "
                f"finishes {record.name}]",
                facts={"day": day.isoformat(), "record": record.name, "error": str(exc)},
            )
            return False
        if present:
            result.deleted.append(day)
    return True


def run_retention(
    config: Config,
    *,
    provider: TimestampProvider,
    roots: Sequence[x509.Certificate],
    clock: Callable[[], datetime] = utc_now,
    dry_run: bool = False,
) -> RetentionResult:
    layout = Layout(config.data_dir)
    now = clock()
    cutoff, days = plan(layout, config, site_day(now))
    result = RetentionResult(enabled=config.retention.enabled, cutoff=cutoff, candidates=days)
    if not config.retention.enabled or dry_run:
        return result

    eku = config.tsa.require_critical_eku
    result.discarded = recover(layout, roots, require_critical_eku=eku)
    pending = unfinished_record(layout, roots, require_critical_eku=eku)
    if pending is not None:
        result.resumed = pending.name
        result.record = pending.name
        if not _finish(layout, pending, result):
            return result
        _deleted_alert(layout, result.deleted, pending)
        cutoff, days = plan(layout, config, site_day(now))
        result.candidates = days
    if not days:
        return result

    remaining = [day for day in sealed_days(layout) if day >= cutoff]
    entries: list[dict[str, Any]] = []
    for day in days:
        check = verify_day(layout, day, roots)
        if not check.ok:
            message = (
                f"{day} günü doğrulanamıyor; hiçbir şey silinmedi: {'; '.join(check.errors)} "
                f"[{day} does not verify; nothing deleted]"
            )
            return _fail(layout, result, message, facts={"day": day.isoformat()})
        manifest = json.loads(layout.manifest(day).read_bytes())
        entries.append(
            {
                "day": day.isoformat(),
                "manifest_sha256": sha256_file(layout.manifest(day)),
                "token_sha256": sha256_file(layout.token(day)),
                "serial": check.serial,
                "files": [
                    {"path": f["path"], "sha256": f["sha256"]}
                    for f in [*manifest["files"], manifest["audit_log"]]
                ],
            }
        )
    if not remaining or not _links_to(layout, remaining[0], days[-1]):
        return _fail(
            layout,
            result,
            "Silinecek günlerden sonraki gün mühürlü değil; hiçbir şey silinmedi "
            "[the day after the deleted range is not sealed; nothing deleted]",
        )

    record = {
        "schema": DELETION_SCHEMA,
        "created_at": isoformat_site(now, "milliseconds"),
        "retention_days": config.retention.days,
        "cutoff": cutoff.isoformat(),
        "days": entries,
        "operator": "arcilogger-core retention (scheduled task)",
        "reason": REASON,
    }
    layout.deletions.mkdir(parents=True, exist_ok=True)
    record_path = _record_path(layout, site_day(now))
    part = record_path.with_name(record_path.name + PART_SUFFIX)
    atomic_write_bytes(part, canonical_json(record))
    discarded = result.discarded
    try:
        stamp = provider.stamp(part)
    except (TsaError, OSError) as exc:
        _remove(token_path_for(part), discarded)
        _remove(part, discarded)
        retryable = getattr(exc, "retryable", True)
        return _fail(
            layout,
            result,
            f"İmha tutanağına zaman damgası alınamadı ({exc}); hiçbir şey silinmedi, bir sonraki "
            "çalıştırma yeniden dener [the deletion record could not be time-stamped; nothing "
            "deleted — the next run tries again]",
            severity="high" if retryable else "critical",
            facts={
                "stage": "stamp",
                "provider": provider.name,
                "retryable": retryable,
                "error": str(exc),
            },
        )
    stamp_check = verify_token(
        stamp.token_path.read_bytes(), roots=roots, file_path=part, require_critical_eku=eku
    )
    if not stamp_check.ok:
        _remove(stamp.token_path, discarded)
        _remove(part, discarded)
        return _fail(
            layout,
            result,
            "İmha tutanağının zaman damgası doğrulanamadı; hiçbir şey silinmedi: "
            + "; ".join(stamp_check.errors)
            + " [the deletion record's time stamp did not verify; nothing deleted]",
            facts={
                "stage": "verify",
                "provider": provider.name,
                "errors": list(stamp_check.errors),
            },
        )

    # Verified: promote (token first, so a final record never exists without its token).
    token_path = token_path_for(record_path)
    _replace(stamp.token_path, token_path)
    _replace(part, record_path)
    for path in (record_path, token_path):
        lock(path)
    result.record = record_path.name
    already = len(result.deleted)
    if not _finish(layout, record_path, result):
        return result
    _deleted_alert(layout, result.deleted[already:], record_path)
    return result


def _links_to(layout: Layout, day: date, previous: date) -> bool:
    """Whether ``day``'s manifest links to ``previous`` — the day before, or the last sealed
    day before a run of empty days that were not stamped one by one."""
    if day == previous + timedelta(days=1):
        return True
    try:
        manifest = json.loads(layout.manifest(day).read_bytes())
    except (OSError, ValueError):
        return False
    link = manifest.get("previous") if isinstance(manifest, dict) else None
    return isinstance(link, dict) and link.get("day") == previous.isoformat()


def _deleted_alert(layout: Layout, days: Sequence[date], record: Path) -> None:
    """The ``info`` alert of a completed deletion (the record itself is the evidence)."""
    raise_alert(
        layout.alerts,
        ALERT_RULE,
        "info",
        f"{len(days)} gün saklama süresi dolduğu için silindi; imha tutanağı {record.name}.",
        {"days": [d.isoformat() for d in days]},
    )
