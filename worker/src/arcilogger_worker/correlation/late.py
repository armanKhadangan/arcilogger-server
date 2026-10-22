"""Late evidence: rows that reach the database after the lease timeline has passed their time.

The incremental lease job reads only evidence newer than its watermark, and the scheduled
rebuilds cover today (hourly) and yesterday (nightly). Rows loaded later than that — the ingest
service was stopped over a weekend, or caught up on a backlog — would never become leases, and a
5651 lookup for that time would wrongly answer "no lease" although the day is in the database.

Detection uses the ingest progress counters instead of scanning the evidence tables:
``ingest_offsets.line_number`` (per source and **raw day**, the receive day of the spool file) is
updated in the same transaction as the rows it loads (docs/MVP-CONTRACTS.md section 4), so the
sum per raw day only grows when rows received on that day were loaded. Every rebuild of a
complete day records the sum it started from in the internal setting
``worker.leases_ingest_seen`` (``{"YYYY-MM-DD": lines}``). The job ``leases.late_evidence``
compares the current sums with the recorded ones; a raw day that grew since its last rebuild is
rebuilt again, from the day before it (device clocks around midnight) up to today, in order.

Days not recorded yet are left to the nightly rebuild while they are yesterday; older ones
count as late when they hold any line. The first run on a database without the setting only
records the current sums (the existing leases are taken as built from them). Days older than
``db.retention_days`` are ignored. A future "re-ingest from the vault" must update
``ingest_offsets`` (or rebuild the days itself) to be noticed here.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import date, timedelta
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_db.models import IngestOffset
from arcilogger_db.settings import SYSTEM_AUTHOR, get_setting, set_setting

SEEN_KEY: Final = "worker.leases_ingest_seen"


def ingest_lines(session: Session, days: Collection[date] | None = None) -> dict[date, int]:
    """Lines loaded per raw day (all sources together)."""
    query = select(IngestOffset.day, func.sum(IngestOffset.line_number)).group_by(IngestOffset.day)
    if days is not None:
        if not days:
            return {}
        query = query.where(IngestOffset.day.in_(sorted(days)))
    return {day: int(lines or 0) for day, lines in session.execute(query)}


def get_seen(session: Session) -> dict[date, int] | None:
    """Recorded line sums per raw day; ``None`` when nothing was ever recorded."""
    value = get_setting(session, SEEN_KEY, None)
    if not isinstance(value, dict):
        return None
    seen: dict[date, int] = {}
    for key, lines in value.items():
        try:
            seen[date.fromisoformat(str(key))] = int(lines)
        except (TypeError, ValueError):
            continue  # a hand-edited or damaged entry counts as not recorded
    return seen


def record_seen(session: Session, values: Mapping[date, int], *, keep_from: date) -> None:
    """Merge ``values`` into the recorded sums and drop days before ``keep_from``."""
    seen = get_seen(session) or {}
    seen.update(values)
    data = {day.isoformat(): lines for day, lines in sorted(seen.items()) if day >= keep_from}
    set_setting(session, SEEN_KEY, data, updated_by=SYSTEM_AUTHOR)


def retention_start(session: Session, today: date) -> date:
    """First raw day still considered (``db.retention_days`` before ``today``)."""
    days = int(get_setting(session, "db.retention_days", 400))
    return today - timedelta(days=max(1, days))


def find_late_days(
    current: Mapping[date, int], seen: Mapping[date, int], today: date, *, earliest: date
) -> list[date]:
    """Raw days before ``today`` with more lines than at their last rebuild (pure)."""
    yesterday = today - timedelta(days=1)
    late = []
    for day, lines in current.items():
        if not earliest <= day < today:
            continue
        recorded = seen.get(day)
        if recorded is None:
            if day < yesterday and lines > 0:
                late.append(day)
        elif lines > recorded:
            late.append(day)
    return sorted(late)
