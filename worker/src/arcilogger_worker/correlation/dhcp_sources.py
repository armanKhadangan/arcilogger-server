"""The DHCP sources of the lease timeline: MikroTik ``mt_dhcp`` and FortiGate ``fg_dhcp``.

One loader for both tables (ADR-0016), so live, range and single-day rebuilds read DHCP evidence
the same way whichever server logged it. :func:`load_rows` runs one query per table and merges the
rows in the timeline's order ``(time, TABLE_RANK[table], id)``; each row is already interpreted
(:class:`DhcpRow`) and becomes a :class:`~.timeline.DhcpEvent` through :func:`events`.

* **MikroTik** rows keep their rules: the actions ``assigned`` / ``deassigned`` (other actions are
  not lease evidence), the VLAN of the row, else of the server name (``dhcp.server_vlan_regex``),
  else of the subnet; no logged lease time.
* **FortiGate** rows mean what ``dhcp.fortigate_logids`` says their logid means (D4). Whether a
  row is lease evidence at all is decided by :func:`arcilogger_worker.lookup.trust.refusal`, the
  one implementation of the seven rules that the lease alerts and the "Lease evidence" card
  count with as well; it is selected as a column and every refused row is counted under its rule.
  Rules 2 and 3 (an excluded interface, an IPv6 address) are configuration, the others refusals.
  A kept row's server is its ``interface`` (``lease.server_seconds`` keys on it, D7), its VLAN
  the subnet's (the regex is not applied to FortiGate interface names), its lease time the logged
  ``lease`` when ``dhcp.fortigate_use_logged_lease`` is on and it lies in the FortiOS range
  (D6; otherwise the configured times apply, counted ``lease_fallback``, and ``lease=0``
  ``lease_unlimited`` too), its origin ``(fg_dhcp, vd)`` (D27). A row whose address lies in no
  configured network is used and counted (``fortigate_dhcp_no_network``).

Both tables give each row the subnet of the configured network holding its address
(``network``, :meth:`~.leases.Context.network_for`), which the timeline compares for D33: a
VLAN number may come from a MikroTik row or the regex, and a network may have none.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Collection, Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Literal

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from arcilogger_db.models import LEASE_SECONDS_RANGE, FgDhcp, MtDhcp
from arcilogger_db.settings import get_settings
from arcilogger_worker.correlation.timeline import DhcpEvent, Origin
from arcilogger_worker.lookup.refs import (
    DHCP_TABLE,
    FG_DHCP_TABLE,
    TABLE_RANK,
    dhcp_ref,
    fg_dhcp_ref,
)
from arcilogger_worker.lookup.trust import CONFIGURATION, kept, refusal, untrusted_sources
from arcilogger_worker.lookup.types import EvidenceRef, LookupSettings

if TYPE_CHECKING:
    from arcilogger_worker.correlation.leases import Context

ACTIONS = ("assigned", "deassigned")
#: Job report counters of the rows read (besides the refusal codes of ``trust``).
KEPT = "kept_fg_dhcp"
REFUSED = "refused_fg_dhcp"
EXCLUDED = "excluded_fg_dhcp"
NO_NETWORK = "fortigate_dhcp_no_network"
LEASE_FALLBACK = "lease_fallback"
LEASE_UNLIMITED = "lease_unlimited"


@dataclass(frozen=True, slots=True)
class DhcpRow:
    """One row of either DHCP table, already interpreted."""

    table: str
    id: int
    time: datetime
    action: Literal["assigned", "deassigned"]
    ip: str
    mac: str
    server: str | None
    vlan: int | None
    hostname: str | None
    lease_seconds: int | None
    ref: EvidenceRef
    #: ``(table, vd)``; ``vd`` ``None`` for MikroTik (D27).
    origin: Origin
    #: Subnet of the configured network holding ``ip``, else ``None`` (D33).
    network: str | None

    @property
    def key(self) -> tuple[datetime, int, int, int]:
        """The timeline's order (:func:`~.timeline.evidence_key`): time, release before
        assignment, table rank, id."""
        return (self.time, 2 if self.action == "deassigned" else 3, TABLE_RANK[self.table], self.id)


_MT_COLUMNS = (
    MtDhcp.id,
    MtDhcp.time,
    MtDhcp.server,
    MtDhcp.vlan,
    MtDhcp.action,
    MtDhcp.ip,
    MtDhcp.mac,
    MtDhcp.hostname,
    MtDhcp.source,
    MtDhcp.raw_day,
    MtDhcp.raw_line,
    MtDhcp.raw_offset,
)
_FG_COLUMNS = (
    FgDhcp.id,
    FgDhcp.time,
    FgDhcp.logid,
    FgDhcp.dhcp_msg,
    FgDhcp.interface,
    FgDhcp.ip,
    FgDhcp.mac,
    FgDhcp.hostname,
    FgDhcp.lease,
    FgDhcp.device_time,
    FgDhcp.extra["vd"].astext.label("vd"),
    FgDhcp.source,
    FgDhcp.raw_day,
    FgDhcp.raw_line,
    FgDhcp.raw_offset,
)


def usable_lease(settings: LookupSettings, lease: int | None) -> int | None:
    """The logged lease time a FortiGate lease runs on (D6): within the FortiOS range and with
    ``dhcp.fortigate_use_logged_lease``; ``None``: the configured times apply."""
    low, high = LEASE_SECONDS_RANGE
    if not settings.use_logged_lease or lease is None or not low <= lease <= high:
        return None
    return lease


def load_rows(
    ctx: Context,
    lo: datetime,
    hi: datetime,
    ips: Collection[str] | None = None,
    macs: Collection[str] | None = None,
    *,
    count: bool = False,
) -> list[DhcpRow]:
    """The lease evidence of both tables with ``lo <= time < hi``, in the timeline's order.

    ``ips``: only rows of these addresses (``None``: every row); ``macs``: also the FortiGate
    rows of these MACs, whatever their address (D33: a client's ACK for another address ends
    its lease; MikroTik rows are read by address only). ``count``: add what was read to the
    run's counters (``ctx.stats``) — only the primary window of a run, so that the seeds and the
    look-ahead, which read rows of other windows again, count nothing twice.
    """
    if ips is not None and not ips and not macs:
        return []
    stats: Counter[str] = ctx.stats if count else Counter()
    rows = [*_mikrotik(ctx, lo, hi, ips), *_fortigate(ctx, lo, hi, ips, macs, stats)]
    return sorted(rows, key=lambda row: row.key)


def _mikrotik(
    ctx: Context, lo: datetime, hi: datetime, ips: Collection[str] | None
) -> list[DhcpRow]:
    query = select(*_MT_COLUMNS).where(
        MtDhcp.time >= lo, MtDhcp.time < hi, MtDhcp.action.in_(ACTIONS)
    )
    if ips is not None:
        if not ips:
            return []
        query = query.where(MtDhcp.ip.in_(sorted(ips)))
    return [
        DhcpRow(
            table=DHCP_TABLE,
            id=int(row.id),
            time=row.time,
            action=row.action,
            ip=row.ip,
            mac=row.mac,
            server=row.server,
            vlan=ctx.vlan_for(row.ip, row.server, row.vlan),
            hostname=row.hostname,
            lease_seconds=None,
            ref=dhcp_ref(row),
            origin=(DHCP_TABLE, None),
            network=ctx.network_for(row.ip),
        )
        for row in ctx.session.execute(query.order_by(MtDhcp.time, MtDhcp.id))
    ]


def _fortigate(
    ctx: Context,
    lo: datetime,
    hi: datetime,
    ips: Collection[str] | None,
    macs: Collection[str] | None,
    stats: Counter[str],
) -> list[DhcpRow]:
    settings = ctx.settings
    code = refusal(settings, ctx.untrusted).label("refusal")
    query = select(*_FG_COLUMNS, code).where(FgDhcp.time >= lo, FgDhcp.time < hi)
    if ips is not None:
        wanted = []
        if ips:
            wanted.append(FgDhcp.ip.in_(sorted(ips)))
        if macs:
            wanted.append(FgDhcp.mac.in_(sorted(macs)))
        query = query.where(or_(*wanted))
    found = []
    for row in ctx.session.execute(query.order_by(FgDhcp.time, FgDhcp.id)):
        if row.refusal is not None:
            stats[row.refusal] += 1
            stats[EXCLUDED if row.refusal in CONFIGURATION else REFUSED] += 1
            continue
        stats[KEPT] += 1
        action = settings.fortigate_logids[row.logid]
        lease = usable_lease(settings, row.lease)
        if row.lease is not None and lease is None:
            stats[LEASE_FALLBACK] += 1
            if row.lease == 0:
                stats[LEASE_UNLIMITED] += 1
        network = ctx.network_for(row.ip)
        if network is None:
            stats[NO_NETWORK] += 1
        found.append(
            DhcpRow(
                table=FG_DHCP_TABLE,
                id=int(row.id),
                time=row.time,
                action=action,  # type: ignore[arg-type]
                ip=row.ip,
                mac=row.mac,
                server=row.interface,
                vlan=ctx.vlan_for(row.ip),
                hostname=row.hostname,
                lease_seconds=lease,
                ref=fg_dhcp_ref(row, action),
                origin=(FG_DHCP_TABLE, row.vd),
                network=network,
            )
        )
    return found


def first_time(session: Session) -> datetime | None:
    """The earliest DHCP evidence of both tables: a MikroTik row, or a FortiGate row of a
    trusted source that the current settings make lease evidence (where a catch-up starts)."""
    settings = LookupSettings.from_settings(get_settings(session))
    times = [
        session.scalar(select(func.min(MtDhcp.time))),
        session.scalar(
            select(func.min(FgDhcp.time)).where(kept(settings, untrusted_sources(session)))
        ),
    ]
    found = [t for t in times if t is not None]
    return min(found) if found else None


def events(rows: Iterable[DhcpRow]) -> list[DhcpEvent]:
    """The timeline's events of ``rows``."""
    return [
        DhcpEvent(
            time=row.time,
            action=row.action,
            ip=row.ip,
            mac=row.mac,
            server=row.server,
            vlan=row.vlan,
            hostname=row.hostname,
            ref=row.ref,
            table=row.table,
            lease_seconds=row.lease_seconds,
            origin=row.origin,
            network=row.network,
        )
        for row in rows
    ]
