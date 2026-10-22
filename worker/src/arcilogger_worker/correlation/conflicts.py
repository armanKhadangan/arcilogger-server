"""IP conflicts (spec 9.1): the same IP held by two MAC addresses in overlapping intervals.

Detection is pure (:func:`find_conflicts`); :func:`raise_conflict_alerts` writes the alerts.
Infrastructure MAC addresses (setting ``devices.infrastructure_macs`` and devices flagged
``infrastructure``) are excluded: a router answering for several addresses (proxy ARP, the same
MAC on many VLAN interfaces of the main router) is normal, not a conflict.

Alerts are deduplicated per IP, MAC pair and site day of the overlap start
(``ip_conflict:<ip>:<mac>,<mac>:<day>``): rebuilding a day never raises the same conflict twice,
also not after an administrator dismissed it.

A rebuild of a day sees all of that day's evidence, so an **active** alert (open, or
acknowledged by a person looking into it) of that day whose conflict the rebuild no longer finds
(late evidence closed one of the leases earlier) is set to ``resolved`` with
``details.auto_resolved`` (when and why) — :func:`resolve_unsupported`, like every check that
finds its problem gone. If a later rebuild finds the conflict again, that alert is re-opened
instead of raising a new one. Alerts an administrator resolved or dismissed are never changed.
Both automatic changes go through :mod:`arcilogger_worker.alerts.store` (an ``alert_events`` row
by ``system``, ``resolved_at``).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import date, datetime
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import isoformat_site, site_day, utc_now
from arcilogger_db.models import Alert
from arcilogger_worker.alerts.store import ACTIVE_STATES, reopen, resolve
from arcilogger_worker.correlation.timeline import LeaseInterval

RULE: Final = "ip_conflict"
SEVERITY: Final = "medium"
#: ``details`` key of an alert closed by a rebuild (only such alerts are ever re-opened).
AUTO_RESOLVED: Final = "auto_resolved"


@dataclass(frozen=True)
class Conflict:
    ip: str
    #: The two MAC addresses, sorted.
    macs: tuple[str, str]
    #: Overlap of the two intervals (``end`` ``None``: both still open).
    start: datetime
    end: datetime | None
    lease_ids: tuple[int | None, int | None]
    vlan: int | None

    @property
    def dedup_key(self) -> str:
        return f"{RULE}:{self.ip}:{self.macs[0]},{self.macs[1]}:{site_day(self.start).isoformat()}"


def _overlap(a: LeaseInterval, b: LeaseInterval) -> tuple[datetime, datetime | None] | None:
    start = max(a.start, b.start)
    ends = [end for end in (a.end, b.end) if end is not None]
    end = min(ends) if ends else None
    if end is not None and end <= start:
        return None
    return start, end


def find_conflicts(
    intervals: Iterable[LeaseInterval], infrastructure: Collection[str] = ()
) -> list[Conflict]:
    """Every pair of intervals of one IP with different, non-infrastructure MACs that overlap
    for a positive time (``[start, end)``; an interval that ends when the other starts does not
    overlap)."""
    by_ip: dict[str, list[LeaseInterval]] = defaultdict(list)
    for interval in intervals:
        if interval.mac not in infrastructure:
            by_ip[interval.ip].append(interval)
    found: list[Conflict] = []
    for ip, items in by_ip.items():
        items.sort(key=lambda i: (i.start, i.mac))
        for index, a in enumerate(items):
            for b in items[index + 1 :]:
                if a.mac == b.mac:
                    continue
                overlap = _overlap(a, b)
                if overlap is None:
                    continue
                first, second = sorted((a, b), key=lambda i: i.mac)
                found.append(
                    Conflict(
                        ip=ip,
                        macs=(first.mac, second.mac),
                        start=overlap[0],
                        end=overlap[1],
                        lease_ids=(first.row_id, second.row_id),
                        vlan=a.vlan if a.vlan is not None else b.vlan,
                    )
                )
    return sorted(found, key=lambda c: (c.start, c.ip, c.macs))


def raise_conflict_alerts(session: Session, conflicts: Iterable[Conflict]) -> int:
    """One alert per IP, MAC pair and day (see the module docstring); returns how many were new
    or re-opened."""
    by_key: dict[str, Conflict] = {}
    for conflict in conflicts:
        by_key.setdefault(conflict.dedup_key, conflict)
    if not by_key:
        return 0
    existing: dict[str, list[Alert]] = defaultdict(list)
    # Locked: a person changing one of them right now finishes first, and the rebuild then sees
    # the person's state (it never re-opens an alert a person closed).
    for alert in session.scalars(
        select(Alert)
        .where(Alert.dedup_key.in_(sorted(by_key)))
        .order_by(Alert.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ):
        existing[alert.dedup_key or ""].append(alert)
    created = 0
    for key, conflict in sorted(by_key.items()):
        alerts = existing.get(key)
        if alerts:
            created += _reopen(alerts)
            continue
        details = {
            "ip": conflict.ip,
            "macs": list(conflict.macs),
            "vlan": conflict.vlan,
            "start": conflict.start.isoformat(),
            "end": None if conflict.end is None else conflict.end.isoformat(),
            "lease_ids": [i for i in conflict.lease_ids if i is not None],
        }
        title = (
            f"IP çakışması: {conflict.ip} aynı anda iki MAC adresinde "
            f"({conflict.macs[0]}, {conflict.macs[1]}; {isoformat_site(conflict.start, 'seconds')})"
        )
        statement = (
            insert(Alert)
            .values(
                time=conflict.start,
                rule=RULE,
                severity=SEVERITY,
                title=title,
                details=details,
                dedup_key=key,
            )
            .on_conflict_do_nothing(
                index_elements=[Alert.dedup_key],
                index_where=(Alert.state == "open") & Alert.dedup_key.is_not(None),
            )
            .returning(Alert.id)
        )
        # RETURNING, not rowcount: an ORM-enabled INSERT reports rowcount -1.
        created += len(session.execute(statement).all())
    return created


def _reopen(alerts: list[Alert]) -> int:
    """Re-open the newest alert a rebuild had auto-resolved (never one a person closed)."""
    if any(alert.state == "open" for alert in alerts):
        return 0
    newest = alerts[-1]
    if newest.state != "resolved" or AUTO_RESOLVED not in (newest.details or {}):
        return 0
    reopen(newest, reason="conflict_found_again", now=utc_now())
    return 1


def resolve_unsupported(session: Session, day: date, found: Collection[str]) -> int:
    """Resolve the active conflict alerts of site ``day`` whose key is not in ``found`` (the
    conflicts a rebuild of that day still sees); returns how many."""
    suffix = f":{day.isoformat()}"
    query = select(Alert).where(
        Alert.rule == RULE,
        Alert.state.in_(ACTIVE_STATES),
        Alert.dedup_key.startswith(f"{RULE}:", autoescape=True),
        Alert.dedup_key.endswith(suffix, autoescape=True),
    )
    resolved = 0
    # Locked like the alerts above: one a person closes right now is no longer active.
    locked = query.with_for_update().execution_options(populate_existing=True)
    for alert in session.scalars(locked):
        if alert.dedup_key in found:
            continue
        resolve(alert, reason="lease_rebuild", now=utc_now())
        resolved += 1
    return resolved
