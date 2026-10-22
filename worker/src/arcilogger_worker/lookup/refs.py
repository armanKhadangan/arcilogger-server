"""Evidence references for database rows (``EvidenceRef`` with the raw line, neutral summary).

Used by the lease jobs (``leases.evidence``) and the lookup engine (traffic rows of a match), so a
row is always described the same way. Rows are SQLAlchemy result rows that carry the raw
reference columns (``source``, ``raw_day``, ``raw_line``, ``raw_offset``) and ``id``/``time``.
"""

from __future__ import annotations

from typing import Any, Final

from arcilogger_worker.lookup.types import EvidenceRef, RawRef

DHCP_TABLE: Final = "mt_dhcp"
ARP_TABLE: Final = "mt_arp"
TRAFFIC_TABLE: Final = "fg_traffic"


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
