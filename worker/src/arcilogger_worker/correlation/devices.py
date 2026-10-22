"""Device inventory from DHCP and ARP evidence (spec 9.2).

:func:`upsert_devices` keeps ``devices`` up to date from what the lease jobs saw: first/last
seen, last IP and VLAN, DHCP host names, the randomised-MAC flag and — only when the optional
OUI file is configured — the vendor. It is idempotent and order-independent (``LEAST`` /
``GREATEST``; the last IP follows the latest sighting), so rebuilding an old day never moves a
device back in time.

Fields an administrator maintains are **never** overwritten: ``name``, ``type``, ``person_id``,
``notes``, ``tags``, ``infrastructure`` (the latter is only *initialised* for a new device from
the setting ``devices.infrastructure_macs``) and ``vendor`` once it has a value. ``person_id`` is
the current owner of the assignment history (``device_assignments``) and is written only by
:mod:`arcilogger_db.assignments`, together with that history; the upsert writes rows in MAC
order, the order in which the assignment writers lock them, so the two never deadlock.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from sqlalchemy import case, func, literal_column
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import ColumnClause

from arcilogger_db.models import Device
from arcilogger_worker.correlation.oui import OuiTable
from arcilogger_worker.lookup.types import normalize_mac
from arcilogger_worker.macaddr import is_randomized

#: Host names kept per device (a device rarely has more than one or two).
HOSTNAMES_KEPT = 16
#: Known host names first, then new ones, without duplicates (order of first appearance).
#: A constant SQL fragment (no input), hence the noqa.
_MERGED_HOSTNAMES: ColumnClause[Any] = literal_column(
    "ARRAY(SELECT h FROM unnest(devices.hostnames || excluded.hostnames) WITH ORDINALITY "  # noqa: S608
    f"AS t(h, n) GROUP BY h ORDER BY min(n) LIMIT {HOSTNAMES_KEPT})"
)


@dataclass(frozen=True, slots=True)
class DeviceSighting:
    mac: str
    time: datetime
    ip: str | None = None
    vlan: int | None = None
    hostname: str | None = None


@dataclass
class _Seen:
    mac: str
    first: datetime
    last: datetime
    ip: str | None
    vlan: int | None
    hostnames: list[str] = field(default_factory=list)


def summarize(sightings: Iterable[DeviceSighting]) -> list[_Seen]:
    """One entry per MAC: earliest and latest sighting, the latest IP/VLAN, host names."""
    seen: dict[str, _Seen] = {}
    for item in sorted(sightings, key=lambda s: (s.time, s.mac)):
        mac = normalize_mac(item.mac)
        entry = seen.get(mac)
        if entry is None:
            entry = seen[mac] = _Seen(mac, item.time, item.time, item.ip, item.vlan)
        entry.first = min(entry.first, item.time)
        if item.time >= entry.last:
            entry.last = item.time
            entry.ip = item.ip or entry.ip
            entry.vlan = item.vlan if item.vlan is not None else entry.vlan
        hostname = (item.hostname or "").strip()
        if hostname and hostname not in entry.hostnames:
            entry.hostnames.append(hostname)
    return [seen[mac] for mac in sorted(seen)]


def upsert_devices(
    session: Session,
    sightings: Iterable[DeviceSighting],
    *,
    oui: OuiTable | None = None,
    infrastructure_macs: Collection[str] = (),
) -> int:
    """Insert or update one row per MAC; returns the number of MACs written."""
    entries = summarize(sightings)
    if not entries:
        return 0
    infrastructure = {normalize_mac(m) for m in infrastructure_macs}
    rows = []
    for entry in entries:
        randomized = is_randomized(entry.mac)
        rows.append(
            {
                "mac": entry.mac,
                "first_seen": entry.first,
                "last_seen": entry.last,
                "last_ip": entry.ip,
                "last_vlan": entry.vlan,
                "hostnames": entry.hostnames[:HOSTNAMES_KEPT],
                "vendor": None if randomized or oui is None else oui.lookup(entry.mac),
                "randomized": randomized,
                "infrastructure": entry.mac in infrastructure,
            }
        )
    statement = insert(Device)
    new = statement.excluded
    newer = Device.last_seen.is_(None) | (new.last_seen >= Device.last_seen)
    statement = statement.on_conflict_do_update(
        index_elements=[Device.mac],
        set_={
            "first_seen": func.least(Device.first_seen, new.first_seen),
            "last_seen": func.greatest(Device.last_seen, new.last_seen),
            "last_ip": case((newer & new.last_ip.is_not(None), new.last_ip), else_=Device.last_ip),
            "last_vlan": case(
                (newer & new.last_vlan.is_not(None), new.last_vlan), else_=Device.last_vlan
            ),
            "hostnames": _MERGED_HOSTNAMES,
            "vendor": func.coalesce(Device.vendor, new.vendor),
            "randomized": new.randomized,
            "updated_at": func.now(),
        },
    )
    session.execute(statement, rows)
    return len(rows)
