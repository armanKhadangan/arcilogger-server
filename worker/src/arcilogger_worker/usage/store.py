"""Writing a computed day into the rollup tables, and the fingerprint of its inputs.

A day is replaced as a whole in one transaction (``DELETE … WHERE day = :day`` in every rollup
table, then the new rows): readers see the old day or the new one, never a mix. A
transaction-level advisory lock per day serialises the worker's jobs, the API's rebuild requests
and ``arcilogger-worker usage rebuild`` (another process) on the same day.

``usage_days.inputs_hash`` fingerprints what a rollup was computed from apart from the
attribution: the engine version, the ``usage.*`` settings it depends on, the Public Suffix List
in use and the ingest progress of the day's raw files (``ingest_offsets``: lines loaded per
receive day, as the lease timeline's late-evidence check uses it), taken before the logs are read
(:func:`arcilogger_worker.usage.jobs.rebuild_day`), so a log loaded meanwhile changes it.
``attribution_hash`` (:func:`arcilogger_worker.usage.subjects.timelines_hash`) covers leases and
assignments. The re-attribution job compares both with the current state (:mod:`.jobs`).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, cast

from sqlalchemy import Table, delete, insert, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from arcilogger_db.models import (
    DomainCategory,
    UsageAppDaily,
    UsageBlocked,
    UsageDaily,
    UsageDay,
    UsageDeviceDaily,
    UsageDomainDaily,
    UsageHourly,
    UsageUrl,
)
from arcilogger_worker.correlation.late import ingest_lines
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.domains import DomainNamer
from arcilogger_worker.usage.engine import ENGINE_VERSION, DayResult

#: ``pg_advisory_xact_lock(USAGE_LOCK, day)``: "ARCU" as a 32-bit integer.
USAGE_LOCK: Final = int.from_bytes(b"ARCU", "big") - 2**31
#: Rows per INSERT statement batch.
BATCH: Final = 5000
_TABLES: Final = (
    (UsageDaily, "daily"),
    (UsageDeviceDaily, "devices"),
    (UsageDomainDaily, "domains"),
    (UsageAppDaily, "apps"),
    (UsageHourly, "hourly"),
    (UsageBlocked, "blocked"),
    (UsageUrl, "urls"),
)


def lock_day(session: Session, day: date) -> None:
    session.execute(
        text("SELECT pg_advisory_xact_lock(:key, :day)"),
        {"key": USAGE_LOCK, "day": day.toordinal()},
    )


def inputs_hash(session: Session, day: date, settings: UsageSettings, namer: DomainNamer) -> str:
    """SHA-256 of the engine version, the settings, the suffix list and the ingest progress."""
    lines = ingest_lines(session, [day]).get(day, 0)
    data = {
        "engine": ENGINE_VERSION,
        "settings": settings.fingerprint(),
        "psl": namer.source,
        "ingest_lines": lines,
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


@dataclass(frozen=True)
class WriteReport:
    day: date
    status: str
    rows: dict[str, int]
    computed_until: datetime | None

    def details(self) -> dict[str, Any]:
        return {
            "day": self.day.isoformat(),
            "status": self.status,
            "rows": self.rows,
            "computed_until": None
            if self.computed_until is None
            else self.computed_until.isoformat(),
        }


def write_day(
    session: Session,
    result: DayResult,
    *,
    status: str,
    inputs: str,
    now: datetime,
    duration_ms: int | None = None,
) -> WriteReport:
    """Replace the rollup of ``result.day`` (the caller holds :func:`lock_day` and commits)."""
    day = result.day
    rows: dict[str, int] = {}
    for model, name in _TABLES:
        session.execute(delete(model).where(model.day == day))
        values: list[dict[str, Any]] = getattr(result, name)
        table = cast(Table, model.__table__)
        for start in range(0, len(values), BATCH):
            session.execute(insert(table), values[start : start + BATCH])
        rows[name] = len(values)
    details = {**result.stats, "rows": rows}
    if duration_ms is not None:
        details["duration_ms"] = duration_ms
    # Final: the whole day. Partial (today): what was loaded up to now.
    computed_until = result.hi if status == "final" else min(now, result.hi)
    statement = pg_insert(UsageDay).values(
        day=day,
        status=status,
        computed_at=now,
        computed_until=computed_until,
        inputs_hash=inputs,
        attribution_hash=result.attribution_hash,
        ips=result.ips,
        details=json.loads(json.dumps(details, default=str)),
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[UsageDay.day],
            set_={
                column: statement.excluded[column]
                for column in (
                    "status",
                    "computed_at",
                    "computed_until",
                    "inputs_hash",
                    "attribution_hash",
                    "ips",
                    "details",
                )
            },
        )
    )
    _categories(session, result)
    return WriteReport(day, status, rows, computed_until)


def _categories(session: Session, result: DayResult) -> None:
    """Cache the day's categories; an older day never overwrites a newer day's category."""
    values = [
        {
            "domain": domain,
            "category": category[:200],
            "cat": cat,
            "source": source,
            "hits": hits,
            "last_day": result.day,
        }
        for domain, (category, cat, source, hits) in sorted(result.categories.items())
    ]
    for start in range(0, len(values), BATCH):
        statement = pg_insert(DomainCategory).values(values[start : start + BATCH])
        excluded = statement.excluded
        # A web filter category always replaces a DNS filter one of the same or an older day;
        # a DNS one fills only domains the web filter never categorised on a newer day.
        newer = (DomainCategory.last_day < excluded.last_day) | (
            (DomainCategory.last_day == excluded.last_day)
            & ((excluded.source == "web") | (DomainCategory.source == "dns"))
        )
        session.execute(
            statement.on_conflict_do_update(
                index_elements=[DomainCategory.domain],
                set_={
                    "category": excluded.category,
                    "cat": excluded.cat,
                    "source": excluded.source,
                    "hits": excluded.hits,
                    "last_day": excluded.last_day,
                    "updated_at": text("now()"),
                },
                where=newer,
            )
        )


def final_after(result_hi: datetime, settings: UsageSettings) -> datetime:
    """From this moment the day's rollup is final (its late window has passed)."""
    return result_hi + timedelta(seconds=settings.session_overlap_seconds)
