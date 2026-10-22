"""``alerts.r15`` — R15 "Log kaynağı sessiz" (spec 11, compliance-critical, severity high).

Every minute, for each **enabled, expected, configured** source of the ``sources`` table
(``unknown-<ip>`` senders and the members of a configured network are not expected; ingest
writes ``expected`` and ``heartbeat_minutes`` from ``[[sources]]`` — 15 minutes unless the
source sets another value):

* the source is **silent** when its newest message is older than ``heartbeat_minutes``. The
  newest message is the later of ``sources.last_seen`` (what ingest loaded) and the collector's
  last receive time from ``state/collector-status.json`` (when that file is current), so an
  ingest backlog alone is never reported as a silent device;
* a source that never sent anything is silent ``alerts.r15_never_seen_minutes`` after its row
  was created (ingest creates the rows of the configured sources when it starts);
* no alert in the first ``alerts.r15_startup_grace_minutes`` after the worker started, after
  the source's row was created or after its first message: after a reboot ingest and the
  collector need a moment, and an outage before it is a gap in the day's manifest, not a silent
  device now.

One alert per source (``R15:<name>``); it is resolved automatically as soon as the source sends
again — and also when the source is disabled, no longer expected or removed.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_db.models import Source
from arcilogger_db.settings import get_settings
from arcilogger_worker.alerts.store import active_alerts, open_alert, resolve
from arcilogger_worker.correlation.watermark import collector_received

RULE: Final = "R15"
SEVERITY: Final = "high"
GRACE_KEY: Final = "alerts.r15_startup_grace_minutes"
NEVER_SEEN_KEY: Final = "alerts.r15_never_seen_minutes"


def dedup_key(source: str) -> str:
    return f"{RULE}:{source}"


@dataclass
class R15Report:
    checked: int = 0
    silent: list[str] = field(default_factory=list)
    opened: list[str] = field(default_factory=list)
    resolved: list[str] = field(default_factory=list)
    #: Silent, but still within the start-up grace period (no alert yet).
    grace: list[str] = field(default_factory=list)

    def details(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "silent": self.silent,
            "opened": self.opened,
            "resolved": self.resolved,
            "grace": self.grace,
        }


def _minutes(value: Any, default: int) -> int:
    return value if type(value) is int and value >= 0 else default


def watched_sources(session: Session) -> list[Source]:
    """The sources R15 watches (enabled, expected, configured), by name — also the dashboard's
    "Log alımı" box."""
    return list(
        session.scalars(
            select(Source)
            .where(Source.enabled.is_(True), Source.expected.is_(True), Source.unknown.is_(False))
            .order_by(Source.name)
        )
    )


def newest_message(source: Source, received: Mapping[str, datetime]) -> datetime | None:
    """The later of what ingest loaded and what the collector received (``None``: never)."""
    seen = [moment for moment in (source.last_seen, received.get(source.name)) if moment]
    return max(seen) if seen else None


def check_silent_sources(
    session: Session, layout: Layout, *, now: datetime, started_at: datetime
) -> R15Report:
    """Open or resolve the R15 alerts (the caller commits)."""
    settings = get_settings(session, keys=[GRACE_KEY, NEVER_SEEN_KEY])
    grace = timedelta(minutes=_minutes(settings[GRACE_KEY], 15))
    never_seen = timedelta(minutes=max(1, _minutes(settings[NEVER_SEEN_KEY], 60)))
    received = collector_received(layout, now) or {}
    report = R15Report()
    #: Dedup key → newest message of every watched source (``None``: never).
    watched: dict[str, datetime | None] = {}
    silent_now: set[str] = set()
    for source in watched_sources(session):
        report.checked += 1
        key = dedup_key(source.name)
        last = watched[key] = newest_message(source, received)
        threshold = timedelta(minutes=source.heartbeat_minutes)
        silent = (
            now - last > threshold if last is not None else now - source.created_at > never_seen
        )
        if not silent:
            continue
        silent_now.add(key)
        report.silent.append(source.name)
        starts = (started_at, source.created_at, source.first_seen)
        if any(start is not None and now - start < grace for start in starts):
            report.grace.append(source.name)
            continue
        if last is None:
            title = (
                f"Log kaynağı sessiz: {source.name} kaynağından hiç mesaj gelmedi "
                f"({int(never_seen.total_seconds() // 60)} dakikadan uzun süredir bekleniyor)"
            )
            title_en = f"Log source silent: no message from {source.name} ever"
        else:
            title = (
                f"Log kaynağı sessiz: {source.name} {source.heartbeat_minutes} dakikadan uzun "
                f"süredir mesaj göndermedi (son mesaj {isoformat_site(last, 'seconds')})"
            )
            title_en = (
                f"Log source silent: no message from {source.name} for more than "
                f"{source.heartbeat_minutes} minutes"
            )
        opened = open_alert(
            session,
            rule=RULE,
            severity=SEVERITY,
            title=title,
            details={
                "source": source.name,
                "last_seen": isoformat_site(last, "seconds") if last is not None else None,
                "threshold_minutes": source.heartbeat_minutes,
                "never_seen": last is None,
                "title_en": title_en,
            },
            key=key,
            time=now,
            now=now,
        )
        if opened.created:
            report.opened.append(source.name)
    for alert in active_alerts(session):
        key = alert.dedup_key or ""
        if not key.startswith(f"{RULE}:") or key in silent_now:
            continue
        if key in watched:
            last = watched[key]
            reason = "messages_again"
            facts = {"last_seen": isoformat_site(last, "seconds") if last else None}
        else:
            reason, facts = "not_monitored", {}  # disabled, not expected or removed
        resolve(alert, reason=reason, now=now, facts=facts)
        report.resolved.append(key.removeprefix(f"{RULE}:"))
    return report
