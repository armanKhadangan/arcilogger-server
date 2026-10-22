"""How far the incremental lease job has processed evidence (setting ``worker.leases_watermark``).

Kept apart from :mod:`.leases` because the lookup engine reads it too ("the lease timeline is
computed up to …") and must not import the jobs (import cycle through
:mod:`arcilogger_worker.lookup`).

**The watermark never passes evidence that is not loaded yet.** The incremental job only reads
evidence with a device time in ``[watermark, horizon)``; a row that reaches the database after
the watermark passed its device time would be missed until the hourly re-check of today (or the
late-evidence job, for earlier days). So the horizon is bounded by :func:`ingest_progress` — the
device time up to which ingest has loaded the sources that feed the lease timeline — minus the
lag: during an ingest outage or backlog the watermark waits, and a device clock that runs behind
moves the horizon back by the same amount.

* The sources are those that fed the timeline within :data:`FEED_LOOKBACK` of the watermark (or
  of ``now``), so an ingest outage longer than that still holds the watermark at what was loaded.
* A source holds the others back while it is **active** or has **received lines not loaded
  yet**. Both are decided by receive time (the collector's clock), never by device time, so a
  device clock that runs behind is not mistaken for silence: with the collector's status file
  (``state/collector-status.json``, current within :data:`COLLECTOR_STATUS_FRESH`) a source is
  active when the collector received from it within :data:`SILENT_AFTER`, and has a backlog when
  the collector received later than the newest loaded line; without it, a source whose newest
  loaded line was received :data:`SILENT_AFTER` before the newest loaded line of any other
  source counts as silent (device off; "log source silent" is its own alert). Until then,
  lookups of the newest moments say "the timeline is computed up to …".
* Ingest loads the newest day first after a multi-day backlog: the horizon may then pass days
  that are still being loaded; the late-evidence job rebuilds them once they are (:mod:`.late`).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import Message, MtArp, MtDhcp, Source
from arcilogger_db.settings import SYSTEM_AUTHOR, get_setting, set_setting

WATERMARK_KEY: Final = "worker.leases_watermark"
LAG_KEY: Final = "worker.leases_lag_seconds"
#: Evidence younger than this (behind the ingest progress) is left for the next run: ingest
#: batches every 0.5 s and loads sources independently, the margin covers that.
DEFAULT_LAG_SECONDS: Final = 120
log = logging.getLogger("arcilogger.worker")

#: Sources that fed the lease timeline within this period before the watermark (or ``now``,
#: whichever is earlier) are watched by :func:`ingest_progress`.
FEED_LOOKBACK: Final = timedelta(days=1)
#: A feeding source that sent nothing for this long (receive time) is silent, not "not loaded
#: yet" (the MikroTik heartbeat comes every 5 minutes, DEVICE-SETUP; the FortiGate logs traffic
#: continuously).
SILENT_AFTER: Final = timedelta(minutes=30)
#: Lines dated this far after ``now`` (a device clock ahead) do not count as progress.
FUTURE_SLACK: Final = timedelta(minutes=5)
#: The collector's status file (written every ``status_interval_seconds``, 10 s) counts as
#: current when it was written within this period.
COLLECTOR_STATUS_FRESH: Final = timedelta(minutes=5)
#: Receive times of the collector status and of the loaded lines are compared with this slack
#: (the status file shows microseconds; a line is "not loaded" only beyond it).
RECEIVE_SLACK: Final = timedelta(seconds=1)


@dataclass(frozen=True)
class Loaded:
    """The newest loaded line of a source: device time and collector receive time."""

    time: datetime
    received: datetime


def get_watermark(session: Session) -> datetime | None:
    """Evidence before this moment has been fed into the lease timeline (``None``: never)."""
    value = get_setting(session, WATERMARK_KEY, None)
    if not value:
        return None
    return datetime.fromisoformat(str(value)).astimezone(UTC)


def set_watermark(session: Session, value: datetime) -> None:
    set_setting(session, WATERMARK_KEY, value.astimezone(UTC).isoformat(), updated_by=SYSTEM_AUTHOR)


def lag_seconds(session: Session) -> int:
    return max(0, int(get_setting(session, LAG_KEY, DEFAULT_LAG_SECONDS)))


def feeding_sources(session: Session, now: datetime, since: datetime | None = None) -> set[str]:
    """Sources whose lines feed the lease timeline: enabled FortiGate sources (session traffic)
    and every source that sent DHCP or ARP lines since ``since`` (default: ``now`` minus
    :data:`FEED_LOOKBACK`)."""
    since = now - FEED_LOOKBACK if since is None else since
    names = set(
        session.scalars(
            select(Source.name).where(
                Source.type == "fortigate", Source.enabled.is_(True), Source.unknown.is_(False)
            )
        )
    )
    for model in (MtDhcp, MtArp):
        names |= set(
            session.scalars(
                select(model.source).where(model.time >= since, model.time <= now).distinct()
            )
        )
    return names


def collector_received(layout: Layout | None, now: datetime) -> dict[str, datetime] | None:
    """Per source the collector's last receive time (``state/collector-status.json``).

    ``None`` when there is no layout, or the file is missing, unreadable or older than
    :data:`COLLECTOR_STATUS_FRESH` (the collector is down: :func:`ingest_progress` then judges
    by the loaded lines alone). The collector counts since its own start, so a source missing
    here is judged by the loaded lines as well.
    """
    if layout is None:
        return None
    try:
        status = json.loads(layout.collector_status.read_text(encoding="utf-8"))
        updated = datetime.fromisoformat(str(status["updated_at"]))
        if updated.tzinfo is None or now - updated > COLLECTOR_STATUS_FRESH:
            return None
        received: dict[str, datetime] = {}
        for name, stats in dict(status.get("sources") or {}).items():
            last = stats.get("last_received") if isinstance(stats, dict) else None
            if last:
                moment = datetime.fromisoformat(str(last))
                if moment.tzinfo is not None:
                    received[str(name)] = moment.astimezone(UTC)
        return received
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        log.debug("collector status not usable for the lease horizon: %s", exc)
        return None


def _holds_back(
    name: str,
    loaded: Loaded,
    freshest: datetime,
    received: Mapping[str, datetime] | None,
    now: datetime,
) -> bool:
    """Does this source still hold the horizon at its newest loaded device time?"""
    collected = None if received is None else received.get(name)
    if collected is not None:
        active = now - collected < SILENT_AFTER
        backlog = collected > loaded.received + RECEIVE_SLACK
        return active or backlog
    return loaded.received >= freshest - SILENT_AFTER


def ingest_progress(
    session: Session,
    now: datetime | None = None,
    *,
    watermark: datetime | None = None,
    received: Mapping[str, datetime] | None = None,
) -> datetime | None:
    """Device time up to which the lease timeline's sources are loaded (``None``: no source
    holds the horizon back — no lines, or every source silent and fully loaded).

    The oldest "newest line" (``messages.time``, index ``(source, time)``) over
    :func:`feeding_sources` that are active or have a backlog (``received``: the collector's
    last receive time per source, :func:`collector_received`; see the module docstring).
    """
    now = now or utc_now()
    since = now - FEED_LOOKBACK
    if watermark is not None:
        since = min(since, watermark - FEED_LOOKBACK)
    newest: dict[str, Loaded] = {}
    for name in sorted(feeding_sources(session, now, since)):
        row = session.execute(
            select(Message.time, Message.time_received)
            .where(
                Message.source == name, Message.time >= since, Message.time <= now + FUTURE_SLACK
            )
            .order_by(Message.time.desc())
            .limit(1)
        ).first()
        if row is not None:
            newest[name] = Loaded(time=row.time, received=row.time_received)
    if not newest:
        return None
    freshest = max(item.received for item in newest.values())
    holding = [
        item.time
        for name, item in newest.items()
        if _holds_back(name, item, freshest, received, now)
    ]
    return min(holding) if holding else None


def lease_horizon(
    session: Session,
    now: datetime | None = None,
    *,
    received: Mapping[str, datetime] | None = None,
) -> datetime:
    """Evidence before this moment is complete enough to be applied: the ingest progress (or
    ``now``), whichever is earlier, minus the lag."""
    now = now or utc_now()
    progress = ingest_progress(session, now, watermark=get_watermark(session), received=received)
    reached = now if progress is None else min(now, progress)
    return reached - timedelta(seconds=lag_seconds(session))


def rebuild_horizon(session: Session, now: datetime | None = None) -> datetime:
    """How far rebuilds may process evidence: the watermark, else :func:`lease_horizon`.

    Rebuilds never go past the watermark, so they never apply evidence the incremental job
    will apply again.
    """
    watermark = get_watermark(session)
    if watermark is not None:
        return watermark
    return lease_horizon(session, now)
