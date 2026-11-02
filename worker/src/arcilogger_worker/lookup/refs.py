"""Evidence references for database rows (``EvidenceRef`` with the raw line, neutral summary).

Used by the lease jobs (``leases.evidence``) and the lookup engine (traffic rows of a match), so a
row is always described the same way. Rows are SQLAlchemy result rows that carry the raw
reference columns (``source``, ``raw_day``, ``raw_line``, ``raw_offset``) and ``id``/``time``.

The two DHCP evidence tables (ADR-0016) are defined here once, for the lease timeline, the DHCP
source registry, the trust rules and the lookup: :data:`DHCP_TABLES`, whose order is the
tie-break rank of rows with equal times (:data:`TABLE_RANK`). A DHCP summary is
``<server or interface> <action> <ip> for <mac>[ <host>]…`` for both tables; its action and IP
are read from that fixed part (:func:`dhcp_action`, :func:`dhcp_ip`), never by position, because
a RouterOS server name may hold a space.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from arcilogger_worker.lookup.types import EvidenceRef, RawRef

DHCP_TABLE: Final = "mt_dhcp"
FG_DHCP_TABLE: Final = "fg_dhcp"
ARP_TABLE: Final = "mt_arp"
TRAFFIC_TABLE: Final = "fg_traffic"
#: The DHCP evidence tables, MikroTik first: the index is the tie-break rank of equal times.
DHCP_TABLES: Final = (DHCP_TABLE, FG_DHCP_TABLE)
TABLE_RANK: Final[Mapping[str, int]] = {table: rank for rank, table in enumerate(DHCP_TABLES)}

#: ``<action> <ip> for `` of a DHCP summary: the first match is the row's own (the server or
#: interface before it is a log-supplied name; the host name after it can be anything).
_DHCP_SUMMARY = re.compile(r" (assigned|deassigned) (\S+) for ")
_SPACE = re.compile(r"\s")


def raw_ref(row: Any) -> RawRef:
    return RawRef(source=row.source, day=row.raw_day, line=row.raw_line, offset=row.raw_offset)


def dhcp_ref(row: Any) -> EvidenceRef:
    """``dhcp42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14`` (as RouterOS logs it)."""
    host = f" {row.hostname}" if row.hostname else ""
    return EvidenceRef(
        table=DHCP_TABLE,
        row_id=row.id,
        time=row.time,
        raw=raw_ref(row),
        summary=f"{row.server} {row.action} {row.ip} for {row.mac}{host}",
    )


def fg_dhcp_ref(row: Any, action: str) -> EvidenceRef:
    """``vlan42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14 (FortiGate DHCP, logid
    0100026001, lease 604800)``.

    ``action`` is the meaning ``dhcp.fortigate_logids`` gives the row's logid; the lease is the
    one the FortiGate logged (``-`` without one), so a stored interval keeps the lease of each
    ACK it cites. A space in the interface name becomes ``_`` (sahada doğrula: FortiOS interface
    names are free text of up to 32 characters). "FortiGate DHCP," stands between the device
    and the logid on purpose: the docs quote summaries, and a logid right after the device name
    reads like a model number.
    """
    interface = _SPACE.sub("_", row.interface) if row.interface else "-"
    host = f" {row.hostname}" if row.hostname else ""
    lease = "-" if row.lease is None else row.lease
    return EvidenceRef(
        table=FG_DHCP_TABLE,
        row_id=row.id,
        time=row.time,
        raw=raw_ref(row),
        summary=(
            f"{interface} {action} {row.ip} for {row.mac}{host} "
            f"(FortiGate DHCP, logid {row.logid}, lease {lease})"
        ),
    )


def _dhcp_part(ref: EvidenceRef) -> re.Match[str] | None:
    if ref.table not in DHCP_TABLES:
        return None
    return _DHCP_SUMMARY.search(ref.summary)


def dhcp_action(ref: EvidenceRef) -> str | None:
    """``assigned`` / ``deassigned`` of a DHCP table's ref; ``None`` for any other ref."""
    match = _dhcp_part(ref)
    return None if match is None else match.group(1)


def dhcp_ip(ref: EvidenceRef) -> str | None:
    """The IP of a DHCP table's ref (the report cross-checks the line that ended a lease with
    it); ``None`` for any other ref."""
    match = _dhcp_part(ref)
    return None if match is None else match.group(2)


def last_dhcp_evidence(
    refs: Sequence[EvidenceRef],
    end: datetime | None,
    end_reason: str | None,
    at: datetime | None = None,
) -> datetime | None:
    """The time of a lease's own newest DHCP record at or before ``at`` (no bound without it).

    ``refs``, ``end`` and ``end_reason`` are the lease's evidence, end and end reason (a stored
    lease or a timeline interval). The line that ended a ``superseded`` lease (a ref at or after
    its end) is another device's ACK, or this MAC's ACK for another address, so it is left out,
    as the report cross-check leaves its MAC claim out; a lease's own RELEASE (end reason
    ``dhcp``) counts. ARP and traffic refs are never DHCP evidence.
    """
    times = [
        ref.time
        for ref in refs
        if ref.table in DHCP_TABLES
        and (at is None or ref.time <= at)
        and not (end_reason == "superseded" and end is not None and ref.time >= end)
    ]
    return max(times, default=None)


def arp_ref(row: Any) -> EvidenceRef:
    """``ARP-LOG 172.20.42.57 00:00:5e:00:53:57 vlan42``."""
    interface = f" {row.interface}" if row.interface else ""
    return EvidenceRef(
        table=ARP_TABLE,
        row_id=row.id,
        time=row.time,
        raw=raw_ref(row),
        summary=f"ARP-LOG {row.ip} {row.mac}{interface}",
    )


def _endpoint(ip: str | None, port: int | None) -> str:
    return f"{ip or '?'}:{port}" if port is not None else str(ip or "?")


def traffic_summary(row: Any) -> str:
    """``172.20.60.183:51001 -> 203.0.113.10:443 proto 6 snat 100.64.12.9:51001``."""
    parts = [f"{_endpoint(row.srcip, row.srcport)} -> {_endpoint(row.dstip, row.dstport)}"]
    if row.proto is not None:
        parts.append(f"proto {row.proto}")
    if row.transip is not None:
        parts.append(f"{row.trandisp or 'nat'} {_endpoint(row.transip, row.transport)}")
    if row.is_interim:
        parts.append("(interim)")
    return " ".join(parts)


def traffic_ref(row: Any) -> EvidenceRef:
    return EvidenceRef(
        table=TRAFFIC_TABLE,
        row_id=row.id,
        time=row.time,
        raw=raw_ref(row),
        summary=traffic_summary(row),
    )
