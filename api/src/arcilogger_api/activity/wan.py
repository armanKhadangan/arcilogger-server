"""``GET /api/v1/activity/wan`` — the internet line's speed (Wave 5 contract §6, N6).

The worker's ``snmp.poll`` job reads the WAN interface counters of the SNMP-enabled log sources
and keeps the rates in ``state/wan-rates.json`` (:mod:`arcilogger_worker.snmp.state`); this
answers from that file only, never by asking a device. Per source and interface: the newest
download and upload rate, the configured capacity, the series of the range (``60m``: every read
of the last 60 minutes; ``24h``: the 1-minute averages of the last 24 hours, oldest first), the
status of the last poll and the times of the last good read and the last failure.

It holds no person data, so it is not audited, and it is not licence-gated (QB1). Who may read
it: ``dashboard.view`` or ``system.read``.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Final, get_args

from fastapi import Depends, Query

from arcilogger_api.activity.models import (
    OperStatus,
    WanCapacity,
    WanLine,
    WanPoint,
    WanRange,
    WanRates,
)
from arcilogger_api.deps import LayoutDep
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, require
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now
from arcilogger_worker.snmp.state import Line, read_state

SignedInDep = Annotated[CurrentUser, Depends(require())]

#: The permissions that may read the line's speed (N6): either one.
READERS: Final = ("dashboard.view", "system.read")
RANGES: Final[dict[str, timedelta]] = {"60m": timedelta(minutes=60), "24h": timedelta(hours=24)}
OPER_STATUSES: Final = frozenset(get_args(OperStatus))


def _line(source: str, line: Line, scope: WanRange, now: datetime) -> WanLine:
    since = now - RANGES[scope]
    if scope == "60m":
        points = [(at, down, up) for at, down, up in line.samples if at >= since]
    else:
        points = [(at, down, up) for at, down, up, _ in line.minutes if at >= since]
    recent = [sample for sample in line.samples if sample[0] >= now - RANGES["60m"]]
    newest = recent[-1] if recent else None
    return WanLine(
        source=source,
        name=line.name,
        oper_status=line.oper_status if line.oper_status in OPER_STATUSES else None,
        download_bps=newest[1] if newest else None,
        upload_bps=newest[2] if newest else None,
        capacity_mbps=(
            WanCapacity(down=line.capacity[0], up=line.capacity[1]) if line.capacity else None
        ),
        series=[WanPoint(time=at, download_bps=down, upload_bps=up) for at, down, up in points],
        status=line.status,
        updated_at=line.updated_at,
        last_error_at=line.last_error_at,
    )


def wan_rates(layout: Layout, scope: WanRange, now: datetime) -> WanRates:
    """The answer of ``GET /activity/wan`` from ``state/wan-rates.json`` at ``now``."""
    state = read_state(layout.wan_rates)
    lines = [
        _line(name, line, scope, now)
        for name, source in sorted(state.sources.items())
        for line in source.lines.values()
    ]
    return WanRates(range=scope, lines=lines, generated_at=now)


def get_wan(
    layout: LayoutDep,
    user: SignedInDep,
    range: Annotated[
        WanRange, Query(description="60m: every read; 24h: 1-minute averages")
    ] = "60m",
) -> WanRates:
    """The internet line's speed per SNMP source and WAN interface (not audited: no person
    data; not licence-gated). ``GET /activity/wan``, mounted by ``routers/activity.py``."""
    if not any(user.has(permission) for permission in READERS):
        raise ApiError(403, "forbidden")
    return wan_rates(layout, range, utc_now())
