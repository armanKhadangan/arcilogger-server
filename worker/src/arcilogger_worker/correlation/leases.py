"""Lease timeline jobs: load evidence from the database, run :mod:`.timeline`, write ``leases``.

Storage rule: a ``leases`` row belongs to the **site day of its start** (Europe/Istanbul).

* :func:`rebuild_day` — idempotent full rebuild of one site day in one transaction: recompute
  the day's intervals from the DHCP tables ``mt_dhcp`` / ``fg_dhcp`` (:mod:`.dhcp_sources`),
  ``mt_arp`` and ``fg_traffic`` and write the difference
  (unchanged rows keep their ``id``; rows that no longer follow from the evidence are deleted).
  Intervals that started earlier and were **alive at midnight** are the **seeds** (see
  :func:`_seeds`): their state at midnight is reconstructed from their stored evidence before
  midnight plus the raw evidence of the last lease-timeout window, and their tail (end, last
  evidence, evidence) is rewritten by the rebuild. Intervals still open at the end of the day are
  followed into the next days (**look-ahead**, only for their IPs) until they close, so an
  interval that crosses midnight is one row with its real end. :func:`rebuild_days` rebuilds a
  range in order and only looks ahead after the last day (the next day's seeds carry the state).
* :func:`run_incremental` — every minute: feeds the evidence between the watermark (setting
  ``worker.leases_watermark``) and ``now - worker.leases_lag_seconds`` into the open intervals and
  advances the watermark. Without a watermark, or more than a day behind, it catches up with
  :func:`rebuild_days` from the first day that has DHCP/ARP data.
* :func:`run_late_evidence` — rebuilds the days whose evidence arrived after their rebuild
  (ingest backlog; :mod:`.late`).

The rebuilds never process evidence beyond the watermark (the incremental job owns that part),
so the two modes never apply the same evidence twice. Every job takes a transaction-level
advisory lock, so the service and a manual ``arcilogger-worker rebuild`` never interleave. Live
incremental operation, :func:`rebuild_days` and single-day rebuilds in any order give the same
rows (tested).

Traffic volume: ``fg_traffic`` holds about a million rows a day, but the timeline only needs, per
source IP, the first and last row of every :data:`TRAFFIC_BUCKET_SECONDS` bucket. This is exact
as long as the bucket is shorter than the shortest lease timeout (checked): an interval that is
alive at a bucket's first row is still alive at its last. Traffic of a sender that is not a
configured FortiGate (``trust.untrusted_sources``, ADR-0016 D24) never keeps a lease alive.

**FortiGate DHCP (ADR-0016).** An ``fg_dhcp`` interval is not extended by traffic unless
``dhcp.fortigate_traffic_extends`` (D26), so its seed's state at midnight takes its last
evidence from its DHCP records only, and its lease time from the latest ACK of its pair before
midnight (or before the event that ends it). A FortiGate ACK of a MAC for another address ends
that MAC's lease (D33), so the seeds' raw DHCP rows and the look-ahead are also read by the MACs
of the DHCP intervals. The job report counts the FortiGate rows read and refused
(:mod:`.dhcp_sources`), and the timeline's ``fg_renewal_ack``, ``fg_mac_moved`` and
``superseded_across_sources``.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import delete, func, insert, select, text, tuple_, update
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, site_day, utc_now
from arcilogger_db.models import Device, FgDhcp, FgTraffic, Lease, MtArp
from arcilogger_db.network import find_vlan
from arcilogger_db.settings import get_setting, get_settings
from arcilogger_worker.correlation import dhcp_sources
from arcilogger_worker.correlation.conflicts import (
    find_conflicts,
    raise_conflict_alerts,
    resolve_unsupported,
)
from arcilogger_worker.correlation.devices import DeviceSighting, upsert_devices
from arcilogger_worker.correlation.dhcp_sources import DhcpRow, load_rows
from arcilogger_worker.correlation.late import (
    find_late_days,
    get_seen,
    ingest_lines,
    record_seen,
    retention_start,
)
from arcilogger_worker.correlation.oui import OuiTable, load_oui
from arcilogger_worker.correlation.timeline import (
    DEFAULT_RELEASE_GRACE,
    ArpSighting,
    DhcpEvent,
    EndReason,
    Evidence,
    LeaseInterval,
    LeaseTimeline,
    Origin,
    TimelineRules,
    TrafficSeen,
    bounded,
    sort_evidence,
    with_evidence,
)
from arcilogger_worker.correlation.watermark import (
    collector_received,
    get_watermark,
    lease_horizon,
    rebuild_horizon,
    set_watermark,
)
from arcilogger_worker.lookup.refs import (
    ARP_TABLE,
    DHCP_TABLES,
    FG_DHCP_TABLE,
    TABLE_RANK,
    TRAFFIC_TABLE,
    arp_ref,
    dhcp_action,
    traffic_ref,
)
from arcilogger_worker.lookup.trust import untrusted_sources
from arcilogger_worker.lookup.types import EvidenceRef, LookupSettings

#: pg_advisory_xact_lock key ("ARCILEAS" as a signed 64-bit integer).
LEASE_LOCK_KEY: Final = int.from_bytes(b"ARCILEAS", "big") - 2**63
TRAFFIC_BUCKET_SECONDS: Final = 300
LOOKAHEAD_CHUNK: Final = timedelta(days=1)
#: The incremental job catches up with full day rebuilds when it is further behind than this.
CATCH_UP_AFTER: Final = timedelta(days=1)
#: Setting: ARP sightings this soon after a release of the pair are stale entries (seconds).
RELEASE_GRACE_KEY: Final = "arp.ignore_after_release_seconds"


# -- report ---------------------------------------------------------------------------------------


@dataclass
class LeaseJobReport:
    mode: str
    window_start: datetime | None = None
    window_end: datetime | None = None
    days: list[str] = field(default_factory=list)
    events: Counter[str] = field(default_factory=Counter)
    stats: Counter[str] = field(default_factory=Counter)
    deleted: int = 0
    inserted: int = 0
    updated: int = 0
    devices: int = 0
    alerts: int = 0
    #: Open conflict alerts a rebuild no longer supports (set to ``resolved``).
    resolved_alerts: int = 0
    lookahead_chunks: int = 0
    #: Raw days with evidence that arrived after their rebuild (:func:`run_late_evidence`).
    late_days: list[str] = field(default_factory=list)

    def merge(self, other: LeaseJobReport) -> None:
        self.days.extend(other.days)
        self.events.update(other.events)
        self.stats.update(other.stats)
        self.deleted += other.deleted
        self.inserted += other.inserted
        self.updated += other.updated
        self.devices += other.devices
        self.alerts += other.alerts
        self.resolved_alerts += other.resolved_alerts
        self.lookahead_chunks += other.lookahead_chunks
        self.late_days.extend(other.late_days)
        self.window_start = _min_time(self.window_start, other.window_start)
        self.window_end = _max_time(self.window_end, other.window_end)

    def details(self) -> dict[str, Any]:
        """JSON-safe summary for ``job_runs.details`` (integers and strings only)."""
        return {
            "mode": self.mode,
            "window_start": None if self.window_start is None else self.window_start.isoformat(),
            "window_end": None if self.window_end is None else self.window_end.isoformat(),
            "days": list(self.days),
            "events": dict(sorted(self.events.items())),
            "stats": dict(sorted(self.stats.items())),
            "deleted": self.deleted,
            "inserted": self.inserted,
            "updated": self.updated,
            "devices": self.devices,
            "alerts": self.alerts,
            "resolved_alerts": self.resolved_alerts,
            "lookahead_chunks": self.lookahead_chunks,
            "late_days": list(self.late_days),
        }


def _min_time(a: datetime | None, b: datetime | None) -> datetime | None:
    return b if a is None else a if b is None else min(a, b)


def _max_time(a: datetime | None, b: datetime | None) -> datetime | None:
    return b if a is None else a if b is None else max(a, b)


# -- settings and context -------------------------------------------------------------------------


def lock(session: Session) -> None:
    """Serialise lease jobs (released at commit/rollback)."""
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LEASE_LOCK_KEY})


@dataclass
class Context:
    """Settings and caches of one job run."""

    session: Session
    settings: LookupSettings
    rules: TimelineRules
    server_vlan: re.Pattern[str] | None
    infrastructure: frozenset[str]
    oui: OuiTable | None
    #: Sources whose ``fg_*`` rows are no evidence (ADR-0016 D24), loaded once per run.
    untrusted: frozenset[str] = frozenset()
    #: Rows read and refused (:mod:`.dhcp_sources`), for the job report.
    stats: Counter[str] = field(default_factory=Counter)
    #: Per IP: the VLAN number and the subnet of the configured network holding it.
    _networks: dict[str, tuple[int | None, str | None]] = field(default_factory=dict)

    @classmethod
    def load(cls, session: Session) -> Context:
        """Settings of the run; ``ValueError`` for settings the timeline cannot work with.

        ``nat.egresses`` is kept as stored and never parsed here (``LookupSettings``), so an
        exit value that cannot be read never stops the lease timeline."""
        settings = LookupSettings.from_settings(get_settings(session))
        release_grace = int(get_setting(session, RELEASE_GRACE_KEY, DEFAULT_RELEASE_GRACE))
        rules = TimelineRules.from_settings(settings, release_grace=release_grace)
        if rules.shortest_timeout < TRAFFIC_BUCKET_SECONDS:
            raise ValueError("lease timeouts must be longer than the traffic bucket (300 s)")
        pattern = str(get_setting(session, "dhcp.server_vlan_regex", "") or "")
        try:
            server_vlan = re.compile(pattern) if pattern else None
        except re.error:
            server_vlan = None
        flagged = session.scalars(select(Device.mac).where(Device.infrastructure.is_(True)))
        return cls(
            session=session,
            settings=settings,
            rules=rules,
            server_vlan=server_vlan,
            infrastructure=frozenset([*settings.infrastructure_macs, *flagged]),
            oui=load_oui(get_setting(session, "devices.oui_file", "")),
            untrusted=untrusted_sources(session),
        )

    def vlan_for(self, ip: str, server: str | None = None, vlan: int | None = None) -> int | None:
        """The row's VLAN, else the DHCP server name's (``dhcp60``), else the subnet's (a
        FortiGate row passes no server: ``dhcp.server_vlan_regex`` names MikroTik servers)."""
        if vlan is not None:
            return vlan
        if server and self.server_vlan is not None:
            match = self.server_vlan.match(server)
            if match and match.groups() and match.group(1).isdigit():
                number = int(match.group(1))
                if 1 <= number <= 4094:
                    return number
        return self._network(ip)[0]

    def network_for(self, ip: str) -> str | None:
        """The subnet of the configured network holding ``ip`` (the most specific ``vlans`` row,
        ``find_vlan``), else ``None``: the key of ADR-0016 D33, whether or not the network has a
        VLAN number."""
        return self._network(ip)[1]

    def _network(self, ip: str) -> tuple[int | None, str | None]:
        if ip not in self._networks:
            row = find_vlan(self.session, ip)
            self._networks[ip] = (None, None) if row is None else (row.vlan_id, str(row.subnet))
        return self._networks[ip]


# -- evidence from the database -------------------------------------------------------------------


_ARP_COLUMNS = (
    MtArp.id,
    MtArp.time,
    MtArp.ip,
    MtArp.mac,
    MtArp.interface,
    MtArp.source,
    MtArp.raw_day,
    MtArp.raw_line,
    MtArp.raw_offset,
)
TRAFFIC_REF_COLUMNS: Final = (
    FgTraffic.id,
    FgTraffic.time,
    FgTraffic.srcip,
    FgTraffic.srcport,
    FgTraffic.dstip,
    FgTraffic.dstport,
    FgTraffic.proto,
    FgTraffic.transip,
    FgTraffic.transport,
    FgTraffic.trandisp,
    FgTraffic.is_interim,
    FgTraffic.source,
    FgTraffic.raw_day,
    FgTraffic.raw_line,
    FgTraffic.raw_offset,
)


def load_dhcp(
    ctx: Context,
    lo: datetime,
    hi: datetime,
    ips: Collection[str] | None = None,
    macs: Collection[str] | None = None,
    *,
    count: bool = False,
) -> list[DhcpEvent]:
    """The DHCP events of both tables (:func:`.dhcp_sources.load_rows`)."""
    return dhcp_sources.events(load_rows(ctx, lo, hi, ips, macs, count=count))


def load_arp(
    ctx: Context, lo: datetime, hi: datetime, ips: Collection[str] | None = None
) -> list[ArpSighting]:
    query = select(*_ARP_COLUMNS).where(MtArp.time >= lo, MtArp.time < hi)
    if ips is not None:
        if not ips:
            return []
        query = query.where(MtArp.ip.in_(sorted(ips)))
    return [
        ArpSighting(
            time=row.time, ip=row.ip, mac=row.mac, vlan=ctx.vlan_for(row.ip), ref=arp_ref(row)
        )
        for row in ctx.session.execute(query.order_by(MtArp.time, MtArp.id))
    ]


def load_traffic(
    ctx: Context, lo: datetime, hi: datetime, ips: Collection[str]
) -> list[TrafficSeen]:
    """First and last traffic time per source IP and bucket (see the module docstring)."""
    if not ips:
        return []
    bucket = func.floor(func.extract("epoch", FgTraffic.time) / TRAFFIC_BUCKET_SECONDS)
    query = (
        select(FgTraffic.srcip, func.min(FgTraffic.time), func.max(FgTraffic.time))
        .where(FgTraffic.time >= lo, FgTraffic.time < hi, FgTraffic.srcip.in_(sorted(ips)))
        .group_by(FgTraffic.srcip, bucket)
    )
    if ctx.untrusted:
        query = query.where(FgTraffic.source.not_in(sorted(ctx.untrusted)))
    events: list[TrafficSeen] = []
    for ip, first, last in ctx.session.execute(query):
        if ip is None:  # pragma: no cover - the IN filter excludes NULL
            continue
        events.append(TrafficSeen(time=first, ip=ip))
        if last != first:
            events.append(TrafficSeen(time=last, ip=ip))
    return events


def first_evidence_time(session: Session) -> datetime | None:
    """Earliest DHCP or ARP evidence (start of a catch-up; :func:`.dhcp_sources.first_time`)."""
    times = [
        dhcp_sources.first_time(session),
        session.scalar(select(func.min(MtArp.time))),
    ]
    found = [t for t in times if t is not None]
    return min(found) if found else None


# -- rows <-> intervals ---------------------------------------------------------------------------


def interval_from_row(row: Lease) -> LeaseInterval:
    refs = sort_evidence(EvidenceRef.from_dict(item) for item in row.evidence or ())
    traffic = [r.time for r in refs if r.table == TRAFFIC_TABLE]
    return LeaseInterval(
        ip=row.ip,
        mac=row.mac,
        vlan=row.vlan,
        server=row.server,
        hostname=row.hostname,
        start=row.start,
        end=row.end,
        end_reason=row.end_reason,  # type: ignore[arg-type]
        confidence=row.confidence,  # type: ignore[arg-type]
        source_table=row.source_table,
        last_evidence=row.last_evidence or row.start,
        evidence=refs,
        row_id=row.id,
        last_traffic=max(traffic) if traffic else None,
        lease_seconds=row.lease_seconds,
    )


def _values(interval: LeaseInterval) -> dict[str, Any]:
    return {
        "ip": interval.ip,
        "mac": interval.mac,
        "vlan": interval.vlan,
        "server": interval.server,
        "hostname": interval.hostname,
        "start": interval.start,
        "end": interval.end,
        "end_reason": interval.end_reason,
        "confidence": interval.confidence,
        "source_table": interval.source_table,
        "last_evidence": interval.last_evidence,
        "lease_seconds": interval.lease_seconds,
        "evidence": [ref.to_dict() for ref in interval.evidence],
    }


def _changed(before: LeaseInterval, after: LeaseInterval) -> bool:
    return _values(before) != _values(after)


def _attach_traffic_refs(ctx: Context, intervals: list[LeaseInterval]) -> list[LeaseInterval]:
    """Add the row of each interval's latest traffic when only its time was known."""
    wanted = {
        (i.ip, i.last_traffic)
        for i in intervals
        if i.last_traffic is not None
        and not any(r.table == TRAFFIC_TABLE and r.time == i.last_traffic for r in i.evidence)
    }
    if not wanted:
        return intervals
    found: dict[tuple[str, datetime], EvidenceRef] = {}
    pairs = sorted(wanted)
    for start in range(0, len(pairs), 500):
        chunk = pairs[start : start + 500]
        query = (
            select(*TRAFFIC_REF_COLUMNS)
            .where(tuple_(FgTraffic.srcip, FgTraffic.time).in_(chunk))
            .order_by(FgTraffic.time, FgTraffic.id)
        )
        for row in ctx.session.execute(query):
            found.setdefault((row.srcip, row.time), traffic_ref(row))
    result = []
    for interval in intervals:
        ref = found.get((interval.ip, interval.last_traffic))  # type: ignore[arg-type]
        if ref is not None:
            kept = [r for r in interval.evidence if r.table != TRAFFIC_TABLE]
            interval = with_evidence(replace(interval, evidence=tuple(kept)), [ref])
        result.append(interval)
    return result


def _write(
    ctx: Context, intervals: Sequence[LeaseInterval], stored: Mapping[int | None, LeaseInterval]
) -> tuple[int, int]:
    """Insert the new intervals and update the stored rows that changed (``(inserted, updated)``).

    The traffic rows are attached first, so an unchanged interval compares equal to its row and
    is not written again.
    """
    attached = _attach_traffic_refs(ctx, list(intervals))
    inserts = [i for i in attached if i.row_id is None]
    updates = [
        i
        for i in attached
        if i.row_id is not None and (i.row_id not in stored or _changed(stored[i.row_id], i))
    ]
    if inserts:
        ctx.session.execute(insert(Lease), [_values(i) for i in inserts])
    if updates:
        ctx.session.execute(
            update(Lease),
            [{"id": i.row_id, **_values(i), "updated_at": utc_now()} for i in updates],
        )
    return len(inserts), len(updates)


def _match_stored(
    computed: Iterable[LeaseInterval], stored: Mapping[int | None, LeaseInterval]
) -> tuple[list[LeaseInterval], list[int]]:
    """Give recomputed intervals the ``id`` of the stored row with the same (ip, mac, start).

    Lease ids stay stable across rebuilds (``LeaseInfo.lease_id`` in stored lookups and the lease
    ids in alert details keep pointing at their row). Returns the intervals and the ids of the
    stored rows that no longer follow from the evidence.
    """
    pool: dict[tuple[str, str, datetime], list[LeaseInterval]] = defaultdict(list)
    for row in sorted(stored.values(), key=lambda i: (i.start, i.row_id or 0)):
        pool[(row.ip, row.mac, row.start)].append(row)
    matched = []
    for interval in computed:
        rows = pool.get((interval.ip, interval.mac, interval.start))
        if rows:
            interval = replace(interval, row_id=rows.pop(0).row_id)
        matched.append(interval)
    gone = sorted(row.row_id for rows in pool.values() for row in rows if row.row_id is not None)
    return matched, gone


def _devices(ctx: Context, events: Iterable[Evidence]) -> int:
    sightings = [
        DeviceSighting(
            mac=e.mac,
            time=e.time,
            ip=e.ip,
            vlan=e.vlan,
            hostname=e.hostname if isinstance(e, DhcpEvent) and e.action == "assigned" else None,
        )
        for e in events
        if isinstance(e, DhcpEvent | ArpSighting)
    ]
    return upsert_devices(
        ctx.session, sightings, oui=ctx.oui, infrastructure_macs=ctx.settings.infrastructure_macs
    )


def _overlapping(
    ctx: Context, lo: datetime, hi: datetime, ips: Collection[str] | None
) -> list[LeaseInterval]:
    query = select(Lease).where(Lease.start < hi, (Lease.end.is_(None)) | (Lease.end > lo))
    if ips is not None:
        if not ips:
            return []
        query = query.where(Lease.ip.in_(sorted(ips)))
    return [interval_from_row(row) for row in ctx.session.scalars(query)]


def _conflicts(ctx: Context, lo: datetime, hi: datetime, ips: Collection[str] | None) -> int:
    rows = _overlapping(ctx, lo, hi, ips)
    return raise_conflict_alerts(ctx.session, find_conflicts(rows, ctx.infrastructure))


def _day_conflicts(ctx: Context, day: date, lo: datetime, hi: datetime) -> tuple[int, int]:
    """Alerts for the conflicts among the leases of a rebuilt day; resolves the day's open alerts
    the rebuild no longer supports. ``(raised or re-opened, resolved)``."""
    conflicts = find_conflicts(_overlapping(ctx, lo, hi, None), ctx.infrastructure)
    raised = raise_conflict_alerts(ctx.session, conflicts)
    resolved = resolve_unsupported(ctx.session, day, {c.dedup_key for c in conflicts})
    return raised, resolved


def _count(report: LeaseJobReport, events: Iterable[Evidence]) -> None:
    for event in events:
        if isinstance(event, DhcpEvent):
            report.events[f"dhcp_{event.action}"] += 1
        elif isinstance(event, ArpSighting):
            report.events["arp"] += 1
        else:
            report.events["traffic_points"] += 1


def _releases(ctx: Context, before: datetime) -> list[DhcpEvent]:
    """``deassigned`` events in the release grace before ``before`` (timeline rule 6)."""
    if ctx.rules.release_grace <= 0:
        return []
    lo = before - timedelta(seconds=ctx.rules.release_grace)
    return [e for e in load_dhcp(ctx, lo, before) if e.action == "deassigned"]


# -- seeds of a rebuild ---------------------------------------------------------------------------


@dataclass
class _Seeds:
    open: list[LeaseInterval] = field(default_factory=list)
    #: Stored as alive at midnight, but the raw evidence before midnight ends them earlier.
    closed: list[LeaseInterval] = field(default_factory=list)
    #: The stored rows of all candidates, by id (only changed rows are written back).
    stored: dict[int | None, LeaseInterval] = field(default_factory=dict)
    stats: Counter[str] = field(default_factory=Counter)


#: Order of evidence at equal times (:func:`.timeline.evidence_key`): traffic, ARP, release,
#: assignment; then the DHCP table (``TABLE_RANK``) and the row id.
_RANK: Final = {TRAFFIC_TABLE: 0, ARP_TABLE: 1, "deassigned": 2, "assigned": 3}
Key = tuple[datetime, int, int, int]
#: The lease a FortiGate DHCP ref's summary cites (``…, lease 86400)``, :func:`.refs.fg_dhcp_ref`).
_REF_LEASE: Final = re.compile(r", lease (\d+)\)$")


def _ref_key(ref: EvidenceRef) -> Key:
    if ref.table in DHCP_TABLES:
        rank = _RANK["deassigned"] if dhcp_action(ref) == "deassigned" else _RANK["assigned"]
    else:
        rank = _RANK.get(ref.table, 0)
    return (ref.time, rank, TABLE_RANK.get(ref.table, 0), ref.row_id or 0)


def _arp_key(row: Any) -> Key:
    return (row.time, _RANK[ARP_TABLE], 0, int(row.id))


def _ref_lease(ctx: Context, ref: EvidenceRef) -> int | None:
    """The usable lease time a FortiGate ``assigned`` ref cites (``None`` for any other ref)."""
    if ref.table != FG_DHCP_TABLE or dhcp_action(ref) != "assigned":
        return None
    match = _REF_LEASE.search(ref.summary)
    return dhcp_sources.usable_lease(ctx.settings, int(match.group(1)) if match else None)


def _seed_candidates(ctx: Context, day_start: datetime) -> list[LeaseInterval]:
    """Stored intervals of earlier days that were alive at ``day_start``.

    A row is alive at midnight when it is open or ends at/after midnight (its stored chain of
    evidence spans midnight: a DHCP timeout ends exactly at ``last + timeout``, a release or a
    new assignment is an event at/after midnight). One more case: an ARP interval ends at
    ``last + interval`` but is only *decided* over when no sighting comes within the grace
    (``last + grace``), which can be after midnight — it is a candidate too, and :func:`_seeds`
    checks it against the raw evidence. Rows that ended before midnight are final: their end
    rests only on evidence before midnight, so a later sighting of the same pair (or traffic of
    another client on the IP) never re-opens them.
    """
    rules = ctx.rules
    slack = timedelta(seconds=rules.arp_grace - rules.arp_interval)
    grace = timedelta(seconds=rules.arp_grace)
    rows = ctx.session.scalars(
        select(Lease)
        .where(Lease.start < day_start, Lease.end.is_(None) | (Lease.end >= day_start - slack))
        .order_by(Lease.start, Lease.id)
    ).all()
    candidates = []
    for row in rows:
        seed = interval_from_row(row)
        spans_midnight = seed.end is None or seed.end >= day_start
        undecided_arp = (
            seed.source_table == ARP_TABLE
            and seed.end_reason == "arp"
            and seed.last_evidence + grace >= day_start
        )
        if spans_midnight or undecided_arp:
            candidates.append(seed)
    return _annotated(ctx, candidates)


def _annotated(ctx: Context, seeds: Sequence[LeaseInterval]) -> list[LeaseInterval]:
    """``seeds`` with what the stored rows do not hold (ADR-0016 D27, D33): the network of
    their IP and their DHCP origin. A FortiGate seed's VDOM is read from the ``fg_dhcp`` row of
    its newest own ref (by primary key; never the line that superseded it)."""
    newest: dict[int | None, EvidenceRef] = {}
    for seed in seeds:
        if seed.source_table != FG_DHCP_TABLE:
            continue
        own = [
            ref
            for ref in seed.evidence
            if ref.table == FG_DHCP_TABLE
            and ref.row_id is not None
            and not (seed.end_reason == "superseded" and seed.end and ref.time >= seed.end)
        ]
        if own:
            newest[seed.row_id] = max(own, key=lambda ref: (ref.time, ref.row_id or 0))
    keys = sorted({(ref.time, ref.row_id) for ref in newest.values()})
    vds: dict[tuple[datetime, int | None], str | None] = {}
    for first in range(0, len(keys), 500):
        query = select(FgDhcp.time, FgDhcp.id, FgDhcp.extra["vd"].astext).where(
            tuple_(FgDhcp.time, FgDhcp.id).in_(keys[first : first + 500])
        )
        for time, row_id, vd in ctx.session.execute(query):
            vds[(time, row_id)] = vd
    result = []
    for seed in seeds:
        origin: Origin | None = None
        if seed.source_table == FG_DHCP_TABLE:
            ref = newest.get(seed.row_id)
            origin = (FG_DHCP_TABLE, None if ref is None else vds.get((ref.time, ref.row_id)))
        elif seed.source_table in DHCP_TABLES:
            origin = (seed.source_table, None)
        result.append(replace(seed, origin=origin, network=ctx.network_for(seed.ip)))
    return result


@dataclass(frozen=True)
class _Held:
    """A stored DHCP interval (to tell a continuation ``assigned`` from a new assignment)."""

    mac: str
    table: str
    start: datetime
    end: datetime | None

    def open_at(self, moment: datetime) -> bool:
        return self.start < moment and (self.end is None or self.end > moment)


def _closes(seed: LeaseInterval, row: DhcpRow, held: Sequence[_Held]) -> bool:
    """Whether DHCP ``row`` ends ``seed`` (timeline rules 1 and 2).

    A ``deassigned`` ends the interval of its own pair. An ``assigned`` ends every other
    interval of the IP — unless it continues a DHCP lease its MAC already held from the same
    table (``held``: the stored DHCP intervals of the IP), which changes nothing for the others
    (an IP conflict stays a conflict). An ``assigned`` of the seed's own pair ends an ARP seed
    and a seed of the other DHCP table (ADR-0016 D5). A row of another IP ends the seed only as
    a FortiGate ACK of its MAC (:func:`_moves`, D33).
    """
    if row.ip != seed.ip:
        return _moves(seed, row)
    if row.action == "deassigned":
        return bool(row.mac == seed.mac)
    if row.mac == seed.mac:
        return seed.source_table != row.table  # an ARP interval, or the other DHCP table's
    return not any(h.mac == row.mac and h.table == row.table and h.open_at(row.time) for h in held)


def _moves(seed: LeaseInterval, row: DhcpRow) -> bool:
    """ADR-0016 D33 for a seed: a FortiGate ``assigned`` of its MAC for another IP of its
    configured network ends a FortiGate seed of the same origin and interface, and a MikroTik
    seed (the migration case); never for an address in no configured network."""
    if row.table != FG_DHCP_TABLE or row.action != "assigned" or row.mac != seed.mac:
        return False
    if row.network is None or row.network != seed.network:
        return False
    if seed.source_table == FG_DHCP_TABLE:
        origin = seed.origin or (FG_DHCP_TABLE, None)
        return origin == row.origin and seed.server == row.server
    return seed.source_table in DHCP_TABLES


def _bound_window(ctx: Context, seed: LeaseInterval) -> int:
    """How far before midnight a seed's raw evidence is read: its alive window. A FortiGate
    seed's lease at midnight is only known from those rows, so its window is the longest it can
    have: the configured time, its stored lease and every lease its stored refs cite."""
    window = ctx.rules.alive_window(seed.source_table, seed.server)
    if seed.source_table == FG_DHCP_TABLE:
        leases = [seed.lease_seconds, *(_ref_lease(ctx, ref) for ref in seed.evidence)]
        window = max([window, *(lease for lease in leases if lease)])
    return window


def _midnight_lease(
    ctx: Context, seed: LeaseInterval, refs: Sequence[EvidenceRef], own: Sequence[DhcpRow]
) -> int | None:
    """A FortiGate seed's lease time at midnight (or at the event that ends it): the usable
    lease of the latest ``assigned`` of its pair that logs one, as the timeline's continuations
    keep it (a renewal without a usable lease keeps the one before), from its stored refs and
    the raw rows before that point; the stored value only without either. A later day's rebuild
    may have stored a newer ACK's lease, so the stored value is never the first choice."""
    if seed.source_table != FG_DHCP_TABLE:
        return seed.lease_seconds
    assigned = [(_ref_key(ref), ref) for ref in refs if _ref_key(ref)[1] == _RANK["assigned"]]
    seen = [(key, _ref_lease(ctx, ref)) for key, ref in assigned if ref.table == FG_DHCP_TABLE]
    seen += [(row.key, row.lease_seconds) for row in own]
    if not seen:
        return seed.lease_seconds
    usable = [(key, lease) for key, lease in seen if lease is not None]
    return max(usable)[1] if usable else None


def _seeds(ctx: Context, day_start: datetime) -> _Seeds:
    """The state at ``day_start`` of the intervals alive then (see the module docstring).

    For a seed that was continuously open from its start to midnight, every ARP sighting and
    ``assigned`` of its pair (of its own DHCP table) and (DHCP) every traffic row of its IP
    before midnight belongs to it, and nothing closed it — so its state at midnight is its
    stored evidence before midnight plus the raw evidence of the last ``alive_window`` before
    midnight. The raw DHCP events of that window are also checked for a release or a new
    assignment that ended it (an ARP interval decided after midnight, or stored rows that late
    evidence made stale), and for a FortiGate ACK of its MAC for another address (D33): such a
    seed is closed exactly as the timeline would close it, never re-opened. A FortiGate seed's
    last evidence counts its DHCP records only unless ``dhcp.fortigate_traffic_extends`` (D26);
    its lease time is the one at midnight, and it is ``high`` again (re-derived from its rows,
    which undoes the rollback step's ``low``, ``db.lease_rollback``).
    """
    rules = ctx.rules
    candidates = _seed_candidates(ctx, day_start)
    if not candidates:
        return _Seeds()

    def lower(seed: LeaseInterval) -> datetime:
        window = _bound_window(ctx, seed)
        return max(seed.start, day_start - timedelta(seconds=window))

    bounds = {seed.row_id: lower(seed) for seed in candidates}
    ips = sorted({seed.ip for seed in candidates})
    macs = sorted({seed.mac for seed in candidates if seed.source_table in DHCP_TABLES})
    low = min(bounds.values())
    arp = ctx.session.execute(
        select(*_ARP_COLUMNS)
        .where(MtArp.time >= low, MtArp.time < day_start, MtArp.ip.in_(ips))
        .order_by(MtArp.time, MtArp.id)
    ).all()
    dhcp_by_ip: dict[str, list[DhcpRow]] = defaultdict(list)
    dhcp_by_mac: dict[str, list[DhcpRow]] = defaultdict(list)
    for dhcp_row in load_rows(ctx, low, day_start, ips, macs):
        dhcp_by_ip[dhcp_row.ip].append(dhcp_row)
        if dhcp_row.table == FG_DHCP_TABLE:
            dhcp_by_mac[dhcp_row.mac].append(dhcp_row)
    held: dict[str, list[_Held]] = defaultdict(list)
    for lease_ip, lease_mac, lease_table, lease_start, lease_end in ctx.session.execute(
        select(Lease.ip, Lease.mac, Lease.source_table, Lease.start, Lease.end).where(
            Lease.ip.in_(ips),
            Lease.source_table.in_(DHCP_TABLES),
            Lease.start < day_start,
            Lease.end.is_(None) | (Lease.end > low),
        )
    ):
        held[lease_ip].append(_Held(lease_mac, lease_table, lease_start, lease_end))
    arp_by_pair: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for arp_row in arp:
        arp_by_pair[(arp_row.ip, arp_row.mac)].append(arp_row)

    def rows_for(seed: LeaseInterval) -> list[DhcpRow]:
        """The DHCP rows that can end ``seed``: its IP's, and its MAC's for other IPs."""
        rows = dhcp_by_ip.get(seed.ip, [])
        if seed.source_table not in DHCP_TABLES:
            return rows
        moved = [row for row in dhcp_by_mac.get(seed.mac, ()) if row.ip != seed.ip]
        return sorted([*rows, *moved], key=lambda row: row.key) if moved else rows

    # The first event before midnight that ends each seed (normally none).
    closing: dict[int | None, DhcpRow] = {}
    for seed in candidates:
        opening = min(
            (_ref_key(r) for r in seed.evidence if r.time == seed.start),
            default=(seed.start, _RANK.get(seed.source_table, 0), 0, 0),
        )
        for row in rows_for(seed):
            if (
                row.time >= bounds[seed.row_id]
                and row.key > opening
                and _closes(seed, row, held.get(seed.ip, ()))
            ):
                closing[seed.row_id] = row
                break

    def limit(seed: LeaseInterval) -> Key:
        row = closing.get(seed.row_id)
        return (day_start, -1, 0, 0) if row is None else row.key

    traffic = _latest_traffic(
        ctx,
        [s for s in candidates if s.source_table in DHCP_TABLES],
        bounds,
        {s.row_id: closing[s.row_id].time for s in candidates if s.row_id in closing},
        day_start,
    )

    result = _Seeds(stored={seed.row_id: seed for seed in candidates})
    for seed in candidates:
        lo, end_key = bounds[seed.row_id], limit(seed)
        refs = [r for r in seed.evidence if _ref_key(r) < end_key]
        hostname = seed.hostname
        found = [
            arp_ref(r)
            for r in arp_by_pair.get((seed.ip, seed.mac), ())
            if r.time >= lo and _arp_key(r) < end_key
        ]
        own: list[DhcpRow] = []
        if seed.source_table in DHCP_TABLES:
            for r in dhcp_by_ip.get(seed.ip, ()):
                if (
                    r.action == "assigned"
                    and r.mac == seed.mac
                    and r.table == seed.source_table
                    and r.time >= lo
                    and r.key < end_key
                ):
                    own.append(r)
                    hostname = (r.hostname or "").strip() or hostname
        found.extend(r.ref for r in own)
        last_traffic = max((r.time for r in refs if r.table == TRAFFIC_TABLE), default=None)
        latest = traffic.get(seed.row_id)
        if latest is not None:
            found.append(traffic_ref(latest))
            last_traffic = latest.time if last_traffic is None else max(last_traffic, latest.time)
        fortigate = seed.source_table == FG_DHCP_TABLE
        if fortigate and not rules.fortigate_traffic_extends:
            # D26: traffic and ARP stay evidence of a FortiGate lease but never move its end.
            dhcp_times = [r.time for r in refs if r.table in DHCP_TABLES]
            last = max([seed.start, *dhcp_times, *(r.time for r in own)])
        else:
            last = max([seed.start, *(r.time for r in refs), *(r.time for r in found)])
        lease = _midnight_lease(ctx, seed, refs, own)
        state = replace(
            seed,
            end=None,
            end_reason="open",
            hostname=hostname,
            last_evidence=last,
            evidence=sort_evidence([*refs, *found]),
            last_traffic=last_traffic,
            lease_seconds=lease,
            origin=own[-1].origin if own else seed.origin,
            confidence="high" if fortigate else seed.confidence,
        )
        window = timedelta(seconds=rules.alive_window(seed.source_table, seed.server, lease))
        ended_by = closing.get(seed.row_id)
        if ended_by is not None and ended_by.time <= last + window:
            result.closed.append(_closed_by(ctx, state, ended_by))
        elif ended_by is None and day_start <= last + window:
            result.open.append(state)
        elif seed.source_table == ARP_TABLE:
            end = last + timedelta(seconds=rules.arp_interval)
            result.closed.append(replace(state, end=end, end_reason="arp", evidence=bounded(state)))
        else:
            result.closed.append(
                replace(state, end=last + window, end_reason="timeout", evidence=bounded(state))
            )
    result.open = _one_per_pair(result)
    return result


def _closed_by(ctx: Context, state: LeaseInterval, row: DhcpRow) -> LeaseInterval:
    """``state`` closed by DHCP ``row`` as :meth:`.timeline.LeaseTimeline._close` would."""
    if state.source_table == ARP_TABLE:
        claim = state.last_evidence + timedelta(seconds=ctx.rules.arp_interval)
        if row.time > claim:
            return replace(state, end=claim, end_reason="arp", evidence=bounded(state))
    reason: EndReason = "dhcp" if row.action == "deassigned" else "superseded"
    last = state.last_evidence
    if reason == "dhcp" and state.source_table in DHCP_TABLES:
        last = max(last, row.time)  # the release is a DHCP lease's last evidence
    return replace(
        state,
        end=max(row.time, state.start),
        end_reason=reason,
        last_evidence=last,
        evidence=bounded(state, row.ref),
    )


def _one_per_pair(seeds: _Seeds) -> list[LeaseInterval]:
    """At most one open seed per (ip, mac): two would mean inconsistent stored rows (the timeline
    never holds two intervals of one pair); the newer one is kept, the older closed at its start
    (counted as ``duplicate_seed``)."""
    newest: dict[tuple[str, str], LeaseInterval] = {}
    for seed in sorted(seeds.open, key=lambda s: (s.start, s.row_id or 0)):
        older = newest.get((seed.ip, seed.mac))
        if older is not None:
            seeds.closed.append(replace(older, end=seed.start, end_reason="superseded"))
            seeds.stats["duplicate_seed"] += 1
        newest[(seed.ip, seed.mac)] = seed
    return sorted(newest.values(), key=lambda s: (s.start, s.row_id or 0))


def _latest_traffic(
    ctx: Context,
    seeds: Sequence[LeaseInterval],
    bounds: Mapping[int | None, datetime],
    ends: Mapping[int | None, datetime],
    day_start: datetime,
) -> dict[int | None, Any]:
    """Per DHCP seed the latest traffic row of a trusted sender from its IP in ``[bound, end]``
    (end: the closing event, which sorts after traffic at the same instant; else midnight,
    exclusive)."""
    if not seeds:
        return {}
    statement = text(
        "SELECT s.row_id, t.* FROM unnest(CAST(:ids AS bigint[]), CAST(:ips AS inet[]), "
        "CAST(:los AS timestamptz[]), CAST(:his AS timestamptz[])) AS s(row_id, ip, lo, hi) "
        "CROSS JOIN LATERAL ("
        "SELECT f.id, f.time, host(f.srcip) AS srcip, f.srcport, host(f.dstip) AS dstip, "
        "f.dstport, f.proto, host(f.transip) AS transip, f.transport, f.trandisp, "
        "f.is_interim, f.source, f.raw_day, f.raw_line, f.raw_offset FROM fg_traffic f "
        "WHERE f.srcip = s.ip AND f.time >= s.lo AND f.time < s.hi "
        "AND f.source <> ALL(CAST(:untrusted AS text[])) "
        "ORDER BY f.time DESC, f.id DESC LIMIT 1) AS t"
    )
    tick = timedelta(microseconds=1)
    found: dict[int | None, Any] = {}
    for row in ctx.session.execute(
        statement,
        {
            "ids": [s.row_id for s in seeds],
            "ips": [s.ip for s in seeds],
            "los": [bounds[s.row_id] for s in seeds],
            "his": [ends[s.row_id] + tick if s.row_id in ends else day_start for s in seeds],
            "untrusted": sorted(ctx.untrusted),
        },
    ):
        found[row.row_id] = row
    return found


# -- jobs -----------------------------------------------------------------------------------------


def rebuild_day(
    session: Session,
    day: date,
    *,
    horizon: datetime | None = None,
    lookahead: bool = True,
    now: datetime | None = None,
) -> LeaseJobReport:
    """Recompute the leases that started on site day ``day`` (one transaction; caller commits).

    ``horizon``: evidence before it is complete (default: :func:`rebuild_horizon`).
    ``lookahead=False`` leaves intervals open at midnight open — the next day's rebuild
    continues them (used by :func:`rebuild_days` for every day but the last). A complete day
    (``end <= horizon``) also records its ingest line count (:mod:`.late`).
    """
    lock(session)
    ctx = Context.load(session)
    horizon = horizon or rebuild_horizon(session, now)
    start, end = (t.astimezone(UTC) for t in day_window(day))
    report = LeaseJobReport(mode="rebuild", window_start=start, days=[day.isoformat()])
    lines = ingest_lines(session, [day]).get(day, 0)
    stored: dict[int | None, LeaseInterval] = {
        row.id: interval_from_row(row)
        for row in session.scalars(select(Lease).where(Lease.start >= start, Lease.start < end))
    }
    limit = min(end, horizon)
    report.window_end = limit
    if start >= limit:
        report.deleted = _delete(session, stored)
        return report
    seeds = _seeds(ctx, start)
    timeline = LeaseTimeline(ctx.rules, seeds.open, releases=_releases(ctx, start))
    dhcp = load_dhcp(ctx, start, limit, count=True)
    arp = load_arp(ctx, start, limit)
    traffic_ips = {s.ip for s in seeds.open if s.source_table in DHCP_TABLES}
    traffic_ips |= {e.ip for e in dhcp if e.action == "assigned"}
    traffic = load_traffic(ctx, start, limit, traffic_ips)
    events: list[Evidence] = [*dhcp, *arp, *traffic]
    _count(report, events)
    timeline.feed(events)
    timeline.expire(limit)
    cursor = limit
    while lookahead and cursor < horizon:
        pending = timeline.open_ips(started_before=end)
        if not pending:
            break
        chunk_end = min(cursor + LOOKAHEAD_CHUNK, horizon)
        open_dhcp = [i for i in timeline.open_intervals() if i.source_table in DHCP_TABLES]
        dhcp_ips = {i.ip for i in open_dhcp}
        # A FortiGate ACK of a pending interval's MAC for another address ends it (D33).
        macs = {i.mac for i in open_dhcp if i.start < end}
        later: list[Evidence] = [
            *load_dhcp(ctx, cursor, chunk_end, pending, macs),
            *load_arp(ctx, cursor, chunk_end, pending),
            *load_traffic(ctx, cursor, chunk_end, pending & dhcp_ips),
        ]
        timeline.feed(later)
        timeline.expire(chunk_end)
        cursor = chunk_end
        report.lookahead_chunks += 1
    report.window_end = cursor
    result = timeline.finish(cursor)
    report.stats.update(timeline.stats)
    report.stats.update(seeds.stats)
    report.stats.update(ctx.stats)
    report.stats["seeds"] += len(seeds.open)
    report.stats["seeds_closed_before_midnight"] += len(seeds.closed)
    seed_ids = {s.row_id for s in seeds.open}
    days_rows, gone = _match_stored(
        (i for i in result if i.row_id is None and start <= i.start < end), stored
    )
    tails = [*(i for i in result if i.row_id in seed_ids), *seeds.closed]
    report.deleted = _delete(session, {i: stored[i] for i in gone})
    report.inserted, report.updated = _write(ctx, [*days_rows, *tails], {**stored, **seeds.stored})
    report.devices = _devices(ctx, [*dhcp, *arp])
    session.flush()
    report.alerts, report.resolved_alerts = _day_conflicts(ctx, day, start, end)
    if end <= horizon:
        record_seen(session, {day: lines}, keep_from=retention_start(session, site_day(horizon)))
    return report


def _delete(session: Session, rows: Mapping[int | None, LeaseInterval]) -> int:
    ids = sorted(i for i in rows if i is not None)
    if ids:
        session.execute(delete(Lease).where(Lease.id.in_(ids)))
    return len(ids)


def rebuild_days(
    session: Session, first: date, last: date, *, now: datetime | None = None, commit: bool = True
) -> LeaseJobReport:
    """Rebuild ``first`` … ``last`` in order (one transaction per day when ``commit``)."""
    if last < first:
        raise ValueError("last day is before the first day")
    total = LeaseJobReport(mode="rebuild")
    day = first
    while day <= last:
        report = rebuild_day(session, day, lookahead=day == last, now=now)
        if commit:
            session.commit()
        total.merge(report)
        day += timedelta(days=1)
    return total


def run_incremental(
    session: Session, *, now: datetime | None = None, layout: Layout | None = None
) -> LeaseJobReport:
    """Feed new evidence into the open intervals and advance the watermark (caller commits).

    Catches up with day rebuilds (committing each day) when there is no watermark yet or it is
    more than :data:`CATCH_UP_AFTER` behind. ``layout`` (the server's data directory) lets the
    horizon read what the collector has received (:func:`.watermark.collector_received`).
    """
    now = now or utc_now()
    lock(session)
    # Never past what ingest has loaded (an outage, a backlog, a device clock behind).
    horizon = lease_horizon(session, now, received=collector_received(layout, now))
    watermark = get_watermark(session)
    if watermark is None or horizon - watermark > CATCH_UP_AFTER:
        return _catch_up(session, watermark, horizon)
    report = LeaseJobReport(mode="incremental", window_start=watermark, window_end=watermark)
    if horizon <= watermark:
        return report
    report.window_end = horizon
    ctx = Context.load(session)
    # Stored rows as they are (their confidence included), with their network and origin.
    seeds = _annotated(
        ctx,
        [
            interval_from_row(row)
            for row in session.scalars(select(Lease).where(Lease.end.is_(None)))
        ],
    )
    by_id: dict[int | None, LeaseInterval] = {s.row_id: s for s in seeds}
    timeline = LeaseTimeline(ctx.rules, seeds, releases=_releases(ctx, watermark))
    dhcp = load_dhcp(ctx, watermark, horizon, count=True)
    arp = load_arp(ctx, watermark, horizon)
    traffic_ips = {s.ip for s in seeds if s.source_table in DHCP_TABLES}
    traffic_ips |= {e.ip for e in dhcp if e.action == "assigned"}
    traffic = load_traffic(ctx, watermark, horizon, traffic_ips)
    events: list[Evidence] = [*dhcp, *arp, *traffic]
    _count(report, events)
    timeline.feed(events)
    result = timeline.finish(horizon)
    report.stats.update(timeline.stats)
    report.stats.update(ctx.stats)
    report.inserted, report.updated = _write(ctx, result, by_id)
    report.devices = _devices(ctx, [*dhcp, *arp])
    session.flush()
    touched = {i.ip for i in result if i.row_id is None or _changed(by_id[i.row_id], i)}
    report.alerts = _conflicts(ctx, watermark, horizon, touched)
    set_watermark(session, horizon)
    return report


def _catch_up(session: Session, watermark: datetime | None, horizon: datetime) -> LeaseJobReport:
    """Rebuild every day from the watermark's (or the first evidence's) day up to ``horizon``."""
    start = watermark if watermark is not None else first_evidence_time(session)
    report = LeaseJobReport(mode="catch_up", window_start=start, window_end=horizon)
    if start is not None and start < horizon:
        day, last = site_day(start), site_day(horizon)
        while day <= last:
            # The next day's seeds continue what is open at midnight: no look-ahead needed.
            report.merge(rebuild_day(session, day, horizon=horizon, lookahead=False))
            session.commit()
            day += timedelta(days=1)
        lock(session)
    set_watermark(session, horizon)
    return report


def run_late_evidence(session: Session, *, now: datetime | None = None) -> LeaseJobReport:
    """Rebuild the days whose evidence arrived after their last rebuild (:mod:`.late`).

    Rebuilds from the day before the first late raw day up to the watermark's day, in order,
    committing each day. The first run on a database without recorded line counts only records
    them.
    """
    lock(session)
    report = LeaseJobReport(mode="late_evidence")
    watermark = get_watermark(session)
    if watermark is None:
        return report  # the incremental job has not caught up yet; it rebuilds everything
    today = site_day(watermark)
    earliest = retention_start(session, today)
    current = ingest_lines(session)
    seen = get_seen(session)
    if seen is None:
        record_seen(session, {d: n for d, n in current.items() if d < today}, keep_from=earliest)
        report.stats["initialised"] = 1
        return report
    late = find_late_days(current, seen, today, earliest=earliest)
    if not late:
        return report
    report.late_days = [day.isoformat() for day in late]
    session.commit()  # rebuild_days takes the lock again per day
    report.merge(rebuild_days(session, late[0] - timedelta(days=1), today, now=now))
    return report
