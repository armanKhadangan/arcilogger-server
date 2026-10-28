"""Where the data of a requested moment lives (spec 7.3: the database keeps less than the vault).

The database keeps ``db.retention_days`` (default 400); the sealed vault keeps at least 730
days. A lookup for a day that is no longer (or not) in the database says so instead of
returning an empty result that looks like "nobody": the day may be only in the sealed vault
(re-ingest from the vault is a later feature, "arşivden yeniden yükle").
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import exists, func, select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.models import FgDhcp, FgTraffic, Message, MtArp, MtDhcp
from arcilogger_db.settings import get_setting
from arcilogger_worker.lookup.texts import explain
from arcilogger_worker.lookup.types import DataWindow, Explanation

#: Tables whose presence means "this day is in the database" (every ingested line has a
#: ``messages`` row; the typed tables are what a lookup actually reads).
_TABLES = (Message, FgTraffic, MtDhcp, MtArp, FgDhcp)


def data_window(
    session: Session, at: datetime, *, layout: Layout | None = None, now: datetime | None = None
) -> tuple[DataWindow, list[Explanation]]:
    now = now or utc_now()
    day = site_day(at)
    start, end = (t.astimezone(UTC) for t in day_window(day))
    firsts = [session.scalar(select(func.min(model.time))) for model in _TABLES]
    lasts = [session.scalar(select(func.max(model.time))) for model in _TABLES]
    first = min((t for t in firsts if t is not None), default=None)
    last = max((t for t in lasts if t is not None), default=None)
    in_database = any(
        session.scalar(select(exists().where(model.time >= start, model.time < end)))
        for model in _TABLES
    )
    in_vault = layout is not None and layout.manifest(day).is_file()
    window = DataWindow(
        requested_day=day,
        db_first_day=None if first is None else site_day(first),
        db_last_day=None if last is None else site_day(last),
        in_database=in_database,
        in_vault=in_vault,
    )
    notes: list[Explanation] = []
    if at > now:
        notes.append(explain("future_time", at=at.isoformat()))
    retention = int(get_setting(session, "db.retention_days", 400))
    if day < site_day(now - timedelta(days=retention)):
        notes.append(explain("older_than_db_retention", day=day.isoformat(), days=retention))
    if not in_database:
        if in_vault:
            notes.append(explain("only_in_vault", day=day.isoformat()))
        elif layout is not None:
            notes.append(explain("no_data_for_day", day=day.isoformat()))
        else:
            notes.append(explain("not_in_database", day=day.isoformat()))
    return window, notes
