"""The schema downgrade of a site rollback (``arcilogger-db schema downgrade``, issue #122).

``Install-Arcilogger.ps1 -Rollback`` without ``-RestoreDatabase``, back to a version that does
not have this version's head revision (issue #164; ``Get-SchemaRollbackPlan``), runs it after the
services stop and before the program is swapped, with this version's program still in place:
down to the previous version's head, never below ``0020_fg_traffic_transip_time`` (the revision
``0021_db_window`` revises), and only through the revisions the installer lists as lossless
(``$LosslessDowngrades``: ``0022_lease_moves``, ``0021_db_window``). A version from before
``0021_db_window`` creates and drops monthly partitions only, and ``0021_db_window``'s downgrade
merges the daily partitions back into monthly ones; one that has ``0021`` but not this head would
fail its own ``arcilogger-db upgrade`` (its repair run) on a revision it does not ship.
:func:`downgrade_schema`

* takes the migrations' advisory lock (:data:`~arcilogger_db.migrate.MIGRATION_LOCK_KEY`, which
  ``arcilogger-db upgrade`` takes for its transaction) at **session** level: ``0021``'s
  downgrade commits a step per table and month (W5-G-15), which would release a
  transaction-level lock with the first step;
* sets the session's ``lock_timeout`` (:data:`LOCK_TIMEOUT`, as the lease step of the rollback):
  the services are stopped, so a lock that does not come soon is a scheduled backup or another
  installer, and the command ends with exit 1 rather than waiting without end; no statement
  timeout, a month of a large table takes as long as it takes;
* runs alembic's downgrade to the target (several revisions at once if need be). A run that
  fails or refuses leaves the steps committed so far (``0021``'s merged months) and the version
  where it was; the next run goes on from there;
* writes, when the downgrade changed the stored ``db.retention_days`` (``0021``'s downgrade puts
  a 30 of the seed or of that revision back to 400; an administrator's value stays), the one
  ``settings.change`` audit entry (actor ``system``; manager answer 4 on #121, issue #122: no
  revision writes audit entries) with the old and the new value, in the transaction in which
  alembic moves its version (the caller's, where ``0021``'s downgrade changes the value): the
  value, the version and the entry commit together, so neither a failed downgrade nor a failed
  entry leaves a change without its entry, and the run after a failure writes it once. The audit
  table has not changed since ``0001_baseline``: this version's writer works on every schema;
* writes first, in the same transaction, the entry of the upgrade's own change of the value when
  it was still pending (the status row :data:`CHANGE_KEY` with ``audited: false``, which the
  window job ``db.window`` turns into its entry on its first run, W5-G-10) and the downgrade
  dropped that row: a rollback before the job's first run would otherwise lose that change's
  entry for good.

Nothing else runs: no seed and no partitions (this version's would make daily partitions again).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from functools import cache
from typing import Any, Final

from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from arcilogger_db.audit import append_audit
from arcilogger_db.migrate import (
    MIGRATION_LOCK_KEY,
    TimescaleMode,
    alembic_config,
    current_revisions,
)

#: The setting ``0021_db_window``'s downgrade changes (G1 the other way round).
WINDOW_KEY: Final = "db.retention_days"
#: Audit target of its change (as ``settings.change`` names a setting).
WINDOW_TARGET: Final = f"setting:{WINDOW_KEY}"
AUDIT_ACTION: Final = "settings.change"
#: The status row of the upgrade's change of the value (frozen copy in ``0021_db_window``, and
#: ``arcilogger_worker.db_window.CHANGE_KEY``, whose job audits it).
CHANGE_KEY: Final = "db.retention_days_migration"
#: The services are stopped during a rollback: no lock waits long (the lease step's 30 s).
LOCK_TIMEOUT: Final = "30s"


class DowngradeUsageError(ValueError):
    """The target is not a revision this version knows, or not below the database's (exit 2)."""


class DowngradeRefusedError(RuntimeError):
    """The database's state does not allow a downgrade; nothing was changed (exit 1)."""


@dataclass(frozen=True)
class DowngradeResult:
    """Alembic's revision(s) before and after, the stored ``db.retention_days`` before and after
    (``None`` for a missing row), the id of the ``settings.change`` entry written for the
    downgrade's change, and that of the upgrade's pending change written before it."""

    before: tuple[str, ...]
    after: tuple[str, ...]
    retention_before: Any = None
    retention_after: Any = None
    audit_id: int | None = None
    upgrade_audit_id: int | None = None

    @property
    def downgraded(self) -> bool:
        return self.before != self.after


@cache
def _script() -> ScriptDirectory:
    """This version's revisions (read once per process)."""
    return ScriptDirectory.from_config(alembic_config())


def known_revisions() -> frozenset[str]:
    """Every revision id shipped with this version (no ``base``, ``heads``, prefixes)."""
    return frozenset(item.revision for item in _script().walk_revisions())


def check_target(revision: str) -> str:
    """``revision`` when this version ships it (before anything connects)."""
    if revision not in known_revisions():
        raise DowngradeUsageError(
            f"bilinmeyen şema sürümü [unknown schema revision]: {revision!r} (bu programın "
            "sürümlerinden biri olmalı [must be one of this program's revisions])"
        )
    return revision


def _below(current: tuple[str, ...], revision: str) -> bool:
    """Whether ``revision`` is the database's revision or one before it (its ancestor)."""
    (head,) = current
    if head not in known_revisions():
        raise DowngradeRefusedError(
            f"veritabanı bu programın bilmediği bir şema sürümünde [the database is at a "
            f"revision this program does not know]: {head}; hiçbir şey değiştirilmedi "
            "[nothing changed]"
        )
    return revision in {item.revision for item in _script().iterate_revisions(head, "base")}


def _window(connection: Connection) -> tuple[Any, str | None]:
    row = connection.execute(
        text("SELECT value, updated_by FROM settings WHERE key = :key"), {"key": WINDOW_KEY}
    ).one_or_none()
    return (None, None) if row is None else (row[0], row[1])


def _pending_change(connection: Connection) -> tuple[dict[str, Any], datetime] | None:
    """The upgrade's change of the value, while no entry was written for it (module text)."""
    row = connection.execute(
        text("SELECT value, updated_at FROM settings WHERE key = :key"), {"key": CHANGE_KEY}
    ).one_or_none()
    if row is None or not isinstance(row[0], dict) or row[0].get("audited") is not False:
        return None
    return row[0], row[1]


def downgrade_schema(
    connection: Connection,
    revision: str,
    *,
    timescale: TimescaleMode = "auto",
    details: dict[str, Any] | None = None,
) -> DowngradeResult:
    """Downgrade the database on ``connection`` (no transaction open) to ``revision`` (module
    text); ``details`` go into the audit entry beside the change. Raises
    :class:`DowngradeUsageError`, :class:`DowngradeRefusedError`, or whatever the downgrade
    raised (its committed steps stay)."""
    check_target(revision)
    connection.execute(text(f"SET lock_timeout = '{LOCK_TIMEOUT}'"))
    connection.execute(text("SELECT pg_advisory_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
    connection.commit()
    try:
        with connection.begin():
            before = current_revisions(connection)
            if len(before) != 1:
                raise DowngradeRefusedError(
                    "veritabanında tek bir şema sürümü yok [the database is not at one schema "
                    f"revision]: {', '.join(before) or '-'}; hiçbir şey değiştirilmedi "
                    "[nothing changed]"
                )
            if not _below(before, revision):
                raise DowngradeUsageError(
                    f"{revision} veritabanının şema sürümünden ({before[0]}) önce değil "
                    f"[is not below the database's schema revision ({before[0]})]"
                )
            if before == (revision,):
                return DowngradeResult(before=before, after=before)
            old, was_set_by = _window(connection)
            pending = _pending_change(connection)
            command.downgrade(alembic_config(connection, timescale=timescale), revision)
            after = current_revisions(connection)
            new, _author = _window(connection)
            downgrade = {"from": before[0], "to": ",".join(after)}
            audit_id = upgrade_audit_id = None
            with Session(bind=connection) as session:
                if pending is not None and _pending_change(connection) is None:
                    # The upgrade's change first, as the window job would have written it.
                    change, changed_at = pending
                    entry = append_audit(
                        session,
                        action=AUDIT_ACTION,
                        target=WINDOW_TARGET,
                        details={
                            **(details or {}),
                            "old": change.get("old"),
                            "new": change.get("new"),
                            "was_set_by": change.get("was_set_by"),
                            "migration": change.get("migration"),
                            "changed_at": changed_at,
                            "audited_at_downgrade": downgrade,
                        },
                    )
                    upgrade_audit_id = entry.id
                if new != old:
                    entry = append_audit(
                        session,
                        action=AUDIT_ACTION,
                        target=WINDOW_TARGET,
                        details={
                            **(details or {}),
                            "old": old,
                            "new": new,
                            "was_set_by": was_set_by,
                            "downgrade": downgrade,
                        },
                    )
                    audit_id = entry.id
            return DowngradeResult(
                before=before,
                after=after,
                retention_before=old,
                retention_after=new,
                audit_id=audit_id,
                upgrade_audit_id=upgrade_audit_id,
            )
    finally:
        _unlock(connection)


def _unlock(connection: Connection) -> None:
    """Release the session lock (a broken connection releases it as it closes)."""
    try:
        connection.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": MIGRATION_LOCK_KEY})
        connection.commit()
    except Exception:  # the connection is gone or unusable: closing it ends the session's lock
        connection.invalidate()
