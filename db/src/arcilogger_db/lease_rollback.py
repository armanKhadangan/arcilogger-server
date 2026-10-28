"""The lease step of a site rollback, and its undoing at the re-upgrade (ADR-0016, round 6).

``Install-Arcilogger.ps1 -Rollback`` without ``-RestoreDatabase`` runs no schema downgrade: the
previous version runs on this schema. It reads every ``leases.source_table`` but ``mt_arp`` as
DHCP and takes a lease's stored confidence into its chains, but it cannot read a FortiGate DHCP
line (its parser files them under ``fg_event``), never ends a FortiGate lease at a RELEASE or
the next client's ACK, and keeps it open for its own configured time. Left ``high``, its
lookups would name a former holder with high confidence.

* :func:`prepare_rollback` (``arcilogger-db leases prepare-rollback``, run by the installer
  after the services stop and before the program is swapped, while this version's program is
  in place): one transaction on derived rows only — every ``fg_dhcp`` lease, ended ones too
  (the previous version may rebuild any day), is set to ``low`` with **one** ``updated_at``,
  and the status row :data:`ROLLBACK_KEY` records that time and the count. Raw and sealed data
  are not touched; the previous version keeps a stored confidence through all its jobs, so its
  chains on these leases are ``low``.
* :func:`restore_after_rollback` (the seed, which ``arcilogger-db upgrade`` runs after alembic):
  the ``fg_dhcp`` leases still ``low`` with exactly that ``updated_at`` are this version's rows
  of the rollback moment (the previous version did not rewrite them): they are ``high`` again,
  the only confidence an ``fg_dhcp`` interval opens with. The rows it rewrote (closed early by
  its timeout, re-derived by a rebuild) stay ``low`` until a lease rebuild re-derives them from
  the ``fg_dhcp`` rows; the report names the rebuild range of the upgrade notes (KURULUM §13):
  from the rollback day minus the longest FortiGate lease to today. The status row is deleted,
  so a second run changes nothing.

The status row is never in ``DEFAULT_SETTINGS`` and never editable (like ``db.timescale``); its
time is kept as an ISO text with microseconds and time zone and compared exactly.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Final

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import require_aware, site_day, utc_now
from arcilogger_db.models import Lease, Setting
from arcilogger_db.settings import SYSTEM_AUTHOR

#: The status row of a rollback step not yet undone: ``{"at": <ISO time>, "count": <rows>}``.
ROLLBACK_KEY: Final = "leases.rollback_lowered"
FORTIGATE_DHCP: Final = "fg_dhcp"
#: The FortiOS default ``lease-time`` (seconds): the rebuild range without a known lease.
DEFAULT_LEASE_SECONDS: Final = 604800
#: The step runs while the services are stopped: it neither waits long for a lock nor runs
#: unbounded (round-7 note).
LOCK_TIMEOUT: Final = "30s"
STATEMENT_TIMEOUT: Final = "10min"


@dataclass(frozen=True)
class RollbackReport:
    """What :func:`prepare_rollback` lowered."""

    at: datetime
    count: int


@dataclass(frozen=True)
class RestoreReport:
    """What :func:`restore_after_rollback` did: leases restored to ``high``, FortiGate leases
    still ``low`` (rewritten by the previous version), and the days to rebuild."""

    restored: int
    still_low: int
    rebuild_from: date
    rebuild_to: date


def prepare_rollback(session: Session, now: datetime | None = None) -> RollbackReport:
    """Lower every FortiGate DHCP lease to ``low`` before a rollback (the caller commits)."""
    at = require_aware(now or utc_now())
    session.execute(text(f"SET LOCAL lock_timeout = '{LOCK_TIMEOUT}'"))
    session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT}'"))
    result = session.execute(
        update(Lease)
        .where(Lease.source_table == FORTIGATE_DHCP, Lease.confidence != "low")
        .values(confidence="low", updated_at=at)
        .execution_options(synchronize_session=False)
    )
    count = int(result.rowcount or 0)  # type: ignore[attr-defined]
    value = {"at": at.isoformat(timespec="microseconds"), "count": count}
    session.execute(
        insert(Setting)
        .values(key=ROLLBACK_KEY, value=value, updated_by=SYSTEM_AUTHOR)
        .on_conflict_do_update(
            index_elements=["key"],
            set_={"value": value, "updated_by": SYSTEM_AUTHOR, "updated_at": func.now()},
        )
    )
    return RollbackReport(at=at, count=count)


def restore_after_rollback(session: Session, now: datetime | None = None) -> RestoreReport | None:
    """Undo :func:`prepare_rollback` for the leases the previous version left as they were
    (the caller commits); ``None`` when no rollback step is recorded."""
    row = session.get(Setting, ROLLBACK_KEY)
    if row is None:
        return None
    value = row.value if isinstance(row.value, dict) else {}
    try:
        at = datetime.fromisoformat(str(value.get("at")))
    except ValueError:
        at = None
    restored = 0
    if at is not None and at.tzinfo is not None:
        result = session.execute(
            update(Lease)
            .where(
                Lease.source_table == FORTIGATE_DHCP,
                Lease.confidence == "low",
                Lease.updated_at == at,
            )
            .values(confidence="high", updated_at=utc_now())
            .execution_options(synchronize_session=False)
        )
        restored = int(result.rowcount or 0)  # type: ignore[attr-defined]
    still_low = int(
        session.scalar(
            select(func.count())
            .select_from(Lease)
            .where(Lease.source_table == FORTIGATE_DHCP, Lease.confidence == "low")
        )
        or 0
    )
    longest = session.scalar(
        select(func.max(Lease.lease_seconds)).where(Lease.source_table == FORTIGATE_DHCP)
    )
    today = site_day(now or utc_now())
    since = (at if at is not None and at.tzinfo is not None else now or utc_now()) - timedelta(
        seconds=longest or DEFAULT_LEASE_SECONDS
    )
    session.delete(row)
    session.flush()
    return RestoreReport(
        restored=restored, still_low=still_low, rebuild_from=site_day(since), rebuild_to=today
    )
