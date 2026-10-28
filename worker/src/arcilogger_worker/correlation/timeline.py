"""The lease timeline of spec 9.1 as a pure computation over ordered evidence.

Input is evidence that some MAC address held, or some host used, an internal IP address:

* :class:`DhcpEvent` — an ``assigned`` / ``deassigned`` of a DHCP server: MikroTik
  (``mt_dhcp``) or, ADR-0016, a FortiGate DHCP ACK/RELEASE whose logid the settings map to that
  meaning (``fg_dhcp``, :mod:`.dhcp_sources`); its ``table`` says which;
* :class:`ArpSighting` — the hourly ``ARP-LOG <ip> <mac> <interface>`` lines (``mt_arp``);
* :class:`TrafficSeen` — FortiGate traffic **from** the IP (``fg_traffic.srcip``). It proves that
  the address was in use, not which MAC used it.

Output is a list of :class:`LeaseInterval` — "MAC m held IP x from start to end" — with the
evidence rows behind each interval. The database job (:mod:`.leases`) only loads evidence, feeds
it in and writes the result; every rule lives here so it can be tested without a database.

Rules (docs/CORRELATION.md has the full table):

1. ``assigned`` opens an interval (ip, mac, hostname, vlan, server) with confidence ``high``. An
   ``assigned`` of an IP whose interval for the same MAC **from the same DHCP table** is already
   open is a continuation (the evidence is kept, the hostname and a logged lease time updated).
   Every *other* open interval of that IP is closed: ``superseded`` — an interval of the same
   pair from the other DHCP table too (ADR-0016 D5: after a move from MikroTik to FortiGate DHCP
   the FortiGate's ACK starts a lease of its own). A supersede across DHCP origins
   (``(table, vd)``) is counted (``superseded_across_sources``). **ADR-0016 D33:** a FortiGate
   ``assigned`` of MAC m also closes m's open FortiGate interval on another IP of the same
   origin, interface (``server``) and configured network, and m's open MikroTik interval on
   another IP of the same network (a migration), each after that IP's own deadline is applied
   (``fg_mac_moved``, ``superseded_across_sources``): a client holds one address per server
   interface and network, and a DHCPDECLINE is not logged. Nothing is closed this way for an
   address in no configured network, nor by a MikroTik ``assigned``.
2. ``deassigned`` with the same ip + mac closes the open interval: ``dhcp``. A ``deassigned``
   without an open interval is counted (``orphan_deassign``) and ignored.
3. RouterOS logs no renewals, so an open MikroTik interval stays open while evidence keeps
   coming: an ``ARP-LOG`` of the same ip + mac or traffic from the IP extends it. Without
   evidence it times out ``lease time x lease.timeout_percent / 100`` after the last evidence,
   per DHCP server (``LookupSettings.lease_timeout_seconds``): ``timeout``, ending at that
   moment. A FortiGate interval with a usable logged lease time (``lease_seconds``) times out
   exactly that long after its last DHCP evidence, the FortiGate's own end (ADR-0016 D6), and
   FortiOS logs renewals: traffic and ARP sightings are kept as its evidence but move its
   deadline only with ``dhcp.fortigate_traffic_extends`` (D26). A FortiGate continuation at least
   40 % of the lease after the interval's previous DHCP evidence is a renewal
   (``fg_renewal_ack``).
4. Traffic never creates an interval and never extends an ARP interval (it cannot name the MAC).
5. An ``ARP-LOG`` of an ip + mac without an open interval creates an ARP interval with
   confidence ``medium``: static devices (IPs never in DHCP), and DHCP clients whose assignment
   was logged before the data starts. Sightings at most ``arp_grace`` apart are merged; the
   interval ends ``arp_interval`` after the last sighting: ``[first_seen, last_seen + interval]``,
   ``arp``. A later ``assigned`` or ``deassigned`` closes it (``superseded`` / ``dhcp``;
   zero-length remainders are dropped), but never after ``last_seen + interval`` — an ARP
   interval claims no more than its last sighting allows.
6. An ``ARP-LOG`` of a pair within ``release_grace`` after a ``deassigned`` of that pair opens
   nothing: the router's ARP table still holds the released client for a while (a stale entry,
   not a sighting; *verify on site*). Counted as ``ignored_arp_after_release``.
7. An ``ARP-LOG`` of an IP that another MAC holds creates an overlapping ARP interval: that is
   an IP conflict, reported by :mod:`.conflicts` (never silently resolved here).

Time ranges are half-open: an event at ``t`` keeps an interval alive when ``t <= last + timeout``;
:meth:`LeaseTimeline.finish` with horizon ``h`` (all evidence with ``time < h`` was fed) closes
intervals with ``last + timeout < h`` and returns the others as open (``end = None``).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Final, Literal

from arcilogger_core.timeutil import require_aware
from arcilogger_worker.lookup.refs import (
    ARP_TABLE,
    DHCP_TABLE,
    DHCP_TABLES,
    FG_DHCP_TABLE,
    TABLE_RANK,
    TRAFFIC_TABLE,
)
from arcilogger_worker.lookup.types import Confidence, EvidenceRef, LookupSettings

#: ``assigned`` continuations kept per interval (the opening and closing rows are always kept).
CONTINUATIONS_KEPT: Final = 6
#: Default of :attr:`TimelineRules.release_grace` (setting ``arp.ignore_after_release_seconds``).
DEFAULT_RELEASE_GRACE: Final = 300

EndReason = Literal["dhcp", "timeout", "superseded", "arp", "open"]
#: The DHCP authority that wrote a row: ``(table, vd)`` (``vd`` of a FortiGate row's ``extra``,
#: ``None`` for MikroTik; ADR-0016 D27).
Origin = tuple[str, str | None]
#: A FortiGate continuation this share of its lease (or more) after the previous DHCP evidence
#: is a renewal (``fg_renewal_ack``; a client renews at half the lease, RFC 2131 T1).
RENEWAL_SHARE: Final = (2, 5)


# -- evidence -------------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DhcpEvent:
    time: datetime
    action: Literal["assigned", "deassigned"]
    ip: str
    mac: str
    server: str | None = None
    vlan: int | None = None
    hostname: str | None = None
    ref: EvidenceRef | None = None
    #: The DHCP table (``mt_dhcp`` or ``fg_dhcp``), its usable logged lease time (``fg_dhcp``
    #: only), its origin and the subnet of the configured network holding ``ip`` (ADR-0016).
    table: str = DHCP_TABLE
    lease_seconds: int | None = None
    origin: Origin | None = None
    network: str | None = None


@dataclass(frozen=True, slots=True)
class ArpSighting:
    time: datetime
    ip: str
    mac: str
    vlan: int | None = None
    ref: EvidenceRef | None = None


@dataclass(frozen=True, slots=True)
class TrafficSeen:
    """Traffic from ``ip`` at ``time`` (the database job passes per-bucket first/last times)."""

    time: datetime
    ip: str
    ref: EvidenceRef | None = None


Evidence = DhcpEvent | ArpSighting | TrafficSeen


def _rank(event: Evidence) -> int:
    """Order at equal times: activity first, then releases, then assignments.

    Traffic and ARP before DHCP keep an interval alive up to a ``deassigned``/``assigned`` at
    the same instant; ``deassigned`` before ``assigned`` turns release + re-assignment in the
    same second into ``dhcp`` + a new interval instead of ``superseded``.
    """
    if isinstance(event, TrafficSeen):
        return 0
    if isinstance(event, ArpSighting):
        return 1
    return 2 if event.action == "deassigned" else 3


def evidence_key(event: Evidence) -> tuple[datetime, int, int, int, str, str]:
    """Feed order: time, :func:`_rank`, the DHCP table's rank (rows of two tables may share an
    id, ADR-0016), row id, IP, MAC."""
    row_id = event.ref.row_id if event.ref is not None and event.ref.row_id is not None else 0
    if event.ref is not None:
        table = event.ref.table
    else:
        table = event.table if isinstance(event, DhcpEvent) else ""
    mac = "" if isinstance(event, TrafficSeen) else event.mac
    return (event.time, _rank(event), TABLE_RANK.get(table, 0), row_id, event.ip, mac)


# -- rules and result -----------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TimelineRules:
    #: Timeout after the last evidence, per DHCP server (seconds).
    server_timeouts: Mapping[str, int] = field(default_factory=dict)
    default_timeout: int = 95040
    #: Interval of the ARP-LOG script; an ARP interval ends this long after the last sighting.
    arp_interval: int = 3600
    #: Sightings at most this far apart belong to one ARP interval (interval + scheduler jitter).
    arp_grace: int = 3960
    #: An ARP sighting of a pair this soon after its ``deassigned`` is a stale ARP table entry
    #: (setting ``arp.ignore_after_release_seconds``; 0 switches the rule off).
    release_grace: int = DEFAULT_RELEASE_GRACE
    #: Traffic (and ARP) extends a FortiGate interval as it does a MikroTik one (setting
    #: ``dhcp.fortigate_traffic_extends``, ADR-0016 D26; off: only DHCP records move its end).
    fortigate_traffic_extends: bool = False

    def __post_init__(self) -> None:
        if self.arp_grace < self.arp_interval:
            raise ValueError("arp_grace must not be shorter than arp_interval")
        if min([self.default_timeout, *self.server_timeouts.values()]) <= 0:
            raise ValueError("lease timeouts must be positive")
        if not 0 <= self.release_grace <= self.arp_interval:
            raise ValueError("release_grace must be between 0 and arp_interval")

    @classmethod
    def from_settings(
        cls, settings: LookupSettings, *, release_grace: int = DEFAULT_RELEASE_GRACE
    ) -> TimelineRules:
        percent = settings.lease_timeout_percent
        return cls(
            server_timeouts={
                server: settings.lease_timeout_seconds(server)
                for server in settings.lease_server_seconds
            },
            default_timeout=settings.lease_timeout_seconds(None),
            arp_interval=settings.arp_interval_seconds,
            arp_grace=max(
                settings.arp_interval_seconds, settings.arp_interval_seconds * percent // 100
            ),
            release_grace=release_grace,
            fortigate_traffic_extends=settings.fortigate_traffic_extends,
        )

    def timeout(self, server: str | None) -> int:
        return self.server_timeouts.get(server or "", self.default_timeout)

    @property
    def shortest_timeout(self) -> int:
        return min([self.default_timeout, *self.server_timeouts.values()])

    @property
    def longest_window(self) -> int:
        """Longest time evidence can keep an interval alive (seed and bucket bounds) without a
        logged lease time (:meth:`longest_window_with` takes those into account)."""
        return max([self.default_timeout, self.arp_grace, *self.server_timeouts.values()])

    def longest_window_with(self, lease_seconds: Iterable[int | None]) -> int:
        """:attr:`longest_window`, or the largest logged lease time of ``lease_seconds`` (the
        seeds' FortiGate leases) when that is longer."""
        return max([self.longest_window, *(lease for lease in lease_seconds if lease)])

    def alive_window(
        self, source_table: str, server: str | None, lease_seconds: int | None = None
    ) -> int:
        """How long after its last evidence an interval stays alive: an ARP interval the grace;
        a DHCP interval with a logged lease time (only ``fg_dhcp`` rows carry one) exactly that
        lease, the FortiGate's own end (ADR-0016 D6); any other the server's configured time x
        ``lease.timeout_percent`` (``LookupSettings.lease_timeout_seconds``, the same formula)."""
        if source_table == ARP_TABLE:
            return self.arp_grace
        if lease_seconds is not None:
            return lease_seconds
        return self.timeout(server)


@dataclass(frozen=True)
class LeaseInterval:
    """One "MAC held IP" interval; ``end`` is ``None`` while it is open."""

    ip: str
    mac: str
    vlan: int | None
    server: str | None
    hostname: str | None
    start: datetime
    end: datetime | None
    end_reason: EndReason
    confidence: Confidence
    source_table: str
    last_evidence: datetime
    evidence: tuple[EvidenceRef, ...] = ()
    #: ``leases.id`` of the stored row this interval continues (seeds), else ``None``.
    row_id: int | None = None
    #: Time of the latest traffic that extended it (its row is looked up afterwards when the
    #: traffic evidence was aggregated without row references).
    last_traffic: datetime | None = None
    #: ADR-0016: the logged lease time of a FortiGate interval (``leases.lease_seconds``); its
    #: origin ``(table, vd)`` and the subnet of the configured network holding ``ip`` (D27, D33;
    #: not stored: the lease job sets both for a seed).
    lease_seconds: int | None = None
    origin: Origin | None = None
    network: str | None = None

    @property
    def is_open(self) -> bool:
        return self.end is None


def sort_evidence(refs: Iterable[EvidenceRef]) -> tuple[EvidenceRef, ...]:
    """Unique refs in time order (stable, so applying the rule twice changes nothing)."""
    unique: dict[tuple[str, int | None, datetime, str], EvidenceRef] = {}
    for ref in refs:
        unique.setdefault((ref.table, ref.row_id, ref.time, ref.summary), ref)
    return tuple(sorted(unique.values(), key=lambda r: (r.time, r.table, r.row_id or 0, r.summary)))


# -- the engine -----------------------------------------------------------------------------------


@dataclass(slots=True)
class _Open:
    ip: str
    mac: str
    vlan: int | None
    server: str | None
    hostname: str | None
    start: datetime
    last: datetime
    source_table: str
    confidence: Confidence
    opening: EvidenceRef | None
    continuations: list[EvidenceRef]
    latest: dict[str, EvidenceRef]
    last_traffic: datetime | None
    row_id: int | None
    lease_seconds: int | None = None
    origin: Origin | None = None
    network: str | None = None
    #: Time of the newest DHCP evidence (the opening, a continuation, its release): what a
    #: renewal is measured from.
    last_dhcp: datetime | None = None

    @classmethod
    def from_interval(cls, seed: LeaseInterval) -> _Open:
        refs = sort_evidence(seed.evidence)
        opening = next((r for r in refs if r.time == seed.start), refs[0] if refs else None)
        continuations = [r for r in refs if r is not opening and r.table in DHCP_TABLES]
        latest: dict[str, EvidenceRef] = {}
        for ref in refs:
            if ref is not opening and ref.table in (ARP_TABLE, TRAFFIC_TABLE):
                latest[ref.table] = ref  # time order: the last one wins
        dhcp_times = [r.time for r in refs if r.table in DHCP_TABLES]
        return cls(
            ip=seed.ip,
            mac=seed.mac,
            vlan=seed.vlan,
            server=seed.server,
            hostname=seed.hostname,
            start=seed.start,
            last=seed.last_evidence,
            source_table=seed.source_table,
            confidence=seed.confidence,
            opening=opening,
            continuations=continuations[-CONTINUATIONS_KEPT:],
            latest=latest,
            last_traffic=seed.last_traffic,
            row_id=seed.row_id,
            lease_seconds=seed.lease_seconds,
            origin=seed.origin,
            network=seed.network,
            last_dhcp=max(dhcp_times, default=seed.start),
        )

    def touch(
        self, time: datetime, ref: EvidenceRef | None, table: str, *, extends: bool = True
    ) -> None:
        """Add evidence at ``time``; ``extends=False`` keeps it without moving the deadline
        (traffic and ARP of a FortiGate interval, ADR-0016 D26)."""
        if extends and time >= self.last:
            self.last = time
        if ref is not None:
            current = self.latest.get(table)
            if current is None or ref.time >= current.time:
                self.latest[table] = ref
        if table == TRAFFIC_TABLE and (self.last_traffic is None or time >= self.last_traffic):
            self.last_traffic = time
        if table in DHCP_TABLES and (self.last_dhcp is None or time >= self.last_dhcp):
            self.last_dhcp = time

    def evidence(self, closing: EvidenceRef | None = None) -> tuple[EvidenceRef, ...]:
        refs = [*self.continuations, *self.latest.values()]
        if self.opening is not None:
            refs.append(self.opening)
        if closing is not None:
            refs.append(closing)
        return sort_evidence(refs)

    def interval(
        self, end: datetime | None, reason: EndReason, closing: EvidenceRef | None = None
    ) -> LeaseInterval:
        return LeaseInterval(
            ip=self.ip,
            mac=self.mac,
            vlan=self.vlan,
            server=self.server,
            hostname=self.hostname,
            start=self.start,
            end=end,
            end_reason=reason,
            confidence=self.confidence,
            source_table=self.source_table,
            last_evidence=self.last,
            evidence=self.evidence(closing),
            row_id=self.row_id,
            last_traffic=self.last_traffic,
            lease_seconds=self.lease_seconds,
            origin=self.origin,
            network=self.network,
        )


class LeaseTimeline:
    """Incremental form of :func:`build_timeline` (the database job feeds it in chunks).

    Batches passed to :meth:`feed` must not go back in time; every IP is independent of every
    other IP, so a caller may feed later evidence for some IPs only (look-ahead).
    """

    def __init__(
        self,
        rules: TimelineRules,
        seeds: Iterable[LeaseInterval] = (),
        *,
        releases: Iterable[DhcpEvent] = (),
    ) -> None:
        """``seeds``: intervals open where the feed starts; ``releases``: ``deassigned`` events
        shortly before it (rule 6 must see them to treat the following ARP sightings alike)."""
        self.rules = rules
        self._open: dict[str, list[_Open]] = {}
        self._closed: list[LeaseInterval] = []
        self._clock: datetime | None = None
        #: Latest ``deassigned`` per (ip, mac) (rule 6).
        self._released: dict[tuple[str, str], datetime] = {}
        #: The IPs of each MAC's open DHCP intervals of both tables (rule 1, D33); may hold IPs
        #: whose interval has closed since, which :meth:`_moved` skips and drops.
        self._dhcp_by_mac: dict[str, set[str]] = {}
        self.stats: Counter[str] = Counter()
        for seed in seeds:
            if not seed.is_open:
                raise ValueError("seeds must be open intervals")
            item = _Open.from_interval(seed)
            self._open.setdefault(seed.ip, []).append(item)
            if item.source_table in DHCP_TABLES:
                self._dhcp_by_mac.setdefault(item.mac, set()).add(item.ip)
        for event in releases:
            if event.action == "deassigned":
                self._note_release(event)

    # -- feeding ----------------------------------------------------------------------------------

    def feed(self, events: Iterable[Evidence]) -> None:
        for event in sorted(events, key=evidence_key):
            require_aware(event.time)
            if self._clock is not None and event.time < self._clock:
                raise ValueError("evidence must be fed in time order")
            self._clock = event.time
            self._expire_ip(event.ip, event.time)
            if isinstance(event, TrafficSeen):
                self._traffic(event)
            elif isinstance(event, ArpSighting):
                self._arp(event)
            elif event.action == "assigned":
                self._assigned(event)
            else:
                self._deassigned(event)

    def expire(self, until: datetime) -> None:
        """Close what timed out before ``until`` (all evidence with ``time < until`` was fed)."""
        for ip in list(self._open):
            self._expire_ip(ip, until)

    def finish(self, horizon: datetime) -> list[LeaseInterval]:
        """Closed intervals plus the ones still open at ``horizon``, in a stable order."""
        self.expire(horizon)
        result = [*self._closed, *self.open_intervals()]
        return sorted(result, key=lambda i: (i.start, i.ip, i.mac, i.end is None))

    # -- inspection -------------------------------------------------------------------------------

    def open_intervals(self) -> list[LeaseInterval]:
        return [o.interval(None, "open") for items in self._open.values() for o in items]

    def closed_intervals(self) -> list[LeaseInterval]:
        return list(self._closed)

    def open_ips(self, *, started_before: datetime | None = None) -> set[str]:
        return {
            ip
            for ip, items in self._open.items()
            if any(started_before is None or o.start < started_before for o in items)
        }

    # -- rules ------------------------------------------------------------------------------------

    def _deadline(self, item: _Open) -> datetime:
        return item.last + timedelta(
            seconds=self.rules.alive_window(item.source_table, item.server, item.lease_seconds)
        )

    def _extends(self, item: _Open) -> bool:
        """Does activity (traffic, an ARP sighting) move this DHCP interval's deadline? Always
        for MikroTik (RouterOS logs no renewals), for FortiGate only with
        ``fortigate_traffic_extends`` (ADR-0016 D26)."""
        return item.source_table != FG_DHCP_TABLE or self.rules.fortigate_traffic_extends

    def _expire_ip(self, ip: str, now: datetime) -> None:
        """An interval is alive at ``now`` while ``now <= last + window``."""
        items = self._open.get(ip)
        if not items:
            return
        keep = []
        for item in items:
            if self._deadline(item) < now:
                self._timeout(item)
            else:
                keep.append(item)
        self._set(ip, keep)

    def _timeout(self, item: _Open) -> None:
        if item.source_table == ARP_TABLE:
            end = item.last + timedelta(seconds=self.rules.arp_interval)
            self._closed.append(item.interval(end, "arp"))
            self.stats["closed_arp"] += 1
        else:
            self._closed.append(item.interval(self._deadline(item), "timeout"))
            self.stats["closed_timeout"] += 1

    def _close(
        self, item: _Open, time: datetime, reason: EndReason, ref: EvidenceRef | None
    ) -> None:
        """Close at ``time``; an ARP interval never claims more than its last sighting allows
        (a DHCP event inside the grace window but after ``last + interval`` ends it there)."""
        if item.source_table == ARP_TABLE:
            claim = item.last + timedelta(seconds=self.rules.arp_interval)
            if time > claim:
                self._closed.append(item.interval(claim, "arp"))
                self.stats["closed_arp"] += 1
                return
            if reason == "superseded" and time <= item.start:
                self.stats["dropped_zero_length_arp"] += 1
                return
        self._closed.append(item.interval(time, reason, ref))
        self.stats[f"closed_{reason}"] += 1

    def _note_release(self, event: DhcpEvent) -> None:
        key = (event.ip, event.mac)
        previous = self._released.get(key)
        if previous is None or event.time > previous:
            self._released[key] = event.time

    def _stale_after_release(self, event: ArpSighting) -> bool:
        released = self._released.get((event.ip, event.mac))
        return (
            released is not None
            and self.rules.release_grace > 0
            and event.time - released <= timedelta(seconds=self.rules.release_grace)
        )

    def _set(self, ip: str, items: list[_Open]) -> None:
        if items:
            self._open[ip] = items
        else:
            self._open.pop(ip, None)

    def _find(self, ip: str, mac: str) -> _Open | None:
        return next((o for o in self._open.get(ip, ()) if o.mac == mac), None)

    def _traffic(self, event: TrafficSeen) -> None:
        self.stats["traffic"] += 1
        for item in self._open.get(event.ip, ()):
            if item.source_table in DHCP_TABLES:
                item.touch(event.time, event.ref, TRAFFIC_TABLE, extends=self._extends(item))

    def _arp(self, event: ArpSighting) -> None:
        self.stats["arp"] += 1
        item = self._find(event.ip, event.mac)
        if item is not None:
            extends = item.source_table == ARP_TABLE or self._extends(item)
            item.touch(event.time, event.ref, ARP_TABLE, extends=extends)
            return
        if self._stale_after_release(event):
            self.stats["ignored_arp_after_release"] += 1
            return
        self._open.setdefault(event.ip, []).append(
            _Open(
                ip=event.ip,
                mac=event.mac,
                vlan=event.vlan,
                server=None,
                hostname=None,
                start=event.time,
                last=event.time,
                source_table=ARP_TABLE,
                confidence="medium",
                opening=event.ref,
                continuations=[],
                latest={},
                last_traffic=None,
                row_id=None,
            )
        )
        self.stats["opened_arp"] += 1

    def _deassigned(self, event: DhcpEvent) -> None:
        self.stats["deassigned"] += 1
        self._note_release(event)
        item = self._find(event.ip, event.mac)
        if item is None:
            self.stats["orphan_deassign"] += 1
            return
        if item.source_table in DHCP_TABLES:
            # The release is the lease's last evidence; an ARP interval keeps its last
            # sighting (its claim is measured from it).
            item.touch(event.time, None, item.source_table)
        self._close(item, event.time, "dhcp", event.ref)
        self._set(event.ip, [o for o in self._open.get(event.ip, ()) if o is not item])

    def _assigned(self, event: DhcpEvent) -> None:
        self.stats["assigned"] += 1
        hostname = (event.hostname or "").strip() or None
        if event.table == FG_DHCP_TABLE:
            self._moved(event)
        current = self._find(event.ip, event.mac)
        if current is not None and current.source_table == event.table:
            if event.table == FG_DHCP_TABLE and self._renewal(current, event):
                self.stats["fg_renewal_ack"] += 1
            current.touch(event.time, None, event.table)
            if event.ref is not None:
                current.continuations = [*current.continuations, event.ref][-CONTINUATIONS_KEPT:]
            current.hostname = hostname or current.hostname
            current.lease_seconds = event.lease_seconds or current.lease_seconds
            self.stats["continued"] += 1
            return
        for other in self._open.get(event.ip, ()):
            self._supersede(other, event)
        self._open[event.ip] = [
            _Open(
                ip=event.ip,
                mac=event.mac,
                vlan=event.vlan,
                server=event.server,
                hostname=hostname,
                start=event.time,
                last=event.time,
                source_table=event.table,
                confidence="high",
                opening=event.ref,
                continuations=[],
                latest={},
                last_traffic=None,
                row_id=None,
                lease_seconds=event.lease_seconds,
                origin=event.origin,
                network=event.network,
                last_dhcp=event.time,
            )
        ]
        self._dhcp_by_mac.setdefault(event.mac, set()).add(event.ip)
        self.stats["opened_dhcp"] += 1

    def _supersede(self, item: _Open, event: DhcpEvent) -> None:
        """Close ``item`` at ``event`` as ``superseded``; a DHCP interval of another origin
        counts as a supersede across sources (ADR-0016 D27)."""
        if item.source_table in DHCP_TABLES and _origin(item) != _origin(event):
            self.stats["superseded_across_sources"] += 1
        self._close(item, event.time, "superseded", event.ref)

    def _renewal(self, item: _Open, event: DhcpEvent) -> bool:
        """A continuation ACK at least :data:`RENEWAL_SHARE` of the lease after the interval's
        previous DHCP evidence (a renewal at half the lease; ADR-0016 V3)."""
        lease = item.lease_seconds or event.lease_seconds
        if lease is None or item.last_dhcp is None:
            return False
        part, whole = RENEWAL_SHARE
        return (event.time - item.last_dhcp).total_seconds() * whole >= lease * part

    def _moved(self, event: DhcpEvent) -> None:
        """ADR-0016 D33: a FortiGate ``assigned`` of MAC m for IP y ends m's open FortiGate
        interval on another IP of the same origin, interface and configured network
        (``fg_mac_moved``), and m's open MikroTik interval on another IP of the same network
        (``superseded_across_sources``); each IP's own deadline is applied first, so an
        interval already past its end times out there, as a run that expires every minute
        would have closed it. Nothing is closed for an address in no configured network."""
        if event.network is None:
            return
        ips = self._dhcp_by_mac.get(event.mac)
        if not ips:
            return
        for ip in sorted(ips - {event.ip}):
            self._expire_ip(ip, event.time)
            items = self._open.get(ip, [])
            ended = []
            for item in items:
                if item.mac != event.mac or item.network != event.network:
                    continue
                if item.source_table == FG_DHCP_TABLE:
                    if _origin(item) != _origin(event) or item.server != event.server:
                        continue
                    self.stats["fg_mac_moved"] += 1
                elif item.source_table in DHCP_TABLES:  # MikroTik: the migration case
                    self.stats["superseded_across_sources"] += 1
                else:
                    continue
                self._close(item, event.time, "superseded", event.ref)
                ended.append(item)
            if ended:
                self._set(ip, [o for o in items if all(o is not e for e in ended)])
        still = {
            ip
            for ip in ips
            if any(
                o.mac == event.mac and o.source_table in DHCP_TABLES for o in self._open.get(ip, ())
            )
        }
        if still:
            self._dhcp_by_mac[event.mac] = still
        else:
            self._dhcp_by_mac.pop(event.mac, None)


def _origin(item: _Open | DhcpEvent) -> Origin:
    """The origin of a DHCP interval or event; without one known, its table's without a VDOM."""
    if item.origin is not None:
        return item.origin
    return (item.source_table if isinstance(item, _Open) else item.table, None)


def build_timeline(
    events: Iterable[Evidence],
    rules: TimelineRules,
    *,
    horizon: datetime,
    seeds: Iterable[LeaseInterval] = (),
    releases: Iterable[DhcpEvent] = (),
) -> list[LeaseInterval]:
    """Lease intervals from ``events`` (any order) and the ``seeds`` open at their start.

    ``horizon``: all evidence before it is in ``events``; intervals whose timeout falls before
    it are closed, the rest stay open. Pure: same input, same output.
    """
    timeline = LeaseTimeline(rules, seeds, releases=releases)
    timeline.feed(e for e in events if e.time < horizon)
    return timeline.finish(horizon)


def bounded(interval: LeaseInterval, closing: EvidenceRef | None = None) -> tuple[EvidenceRef, ...]:
    """The evidence the timeline keeps for ``interval`` (opening, the last
    :data:`CONTINUATIONS_KEPT` continuations, latest ARP and traffic row, ``closing``)."""
    return _Open.from_interval(interval).evidence(closing)


def with_evidence(interval: LeaseInterval, refs: Iterable[EvidenceRef]) -> LeaseInterval:
    """``interval`` with extra evidence merged in (e.g. a looked-up traffic row)."""
    return replace(interval, evidence=sort_evidence([*interval.evidence, *refs]))
