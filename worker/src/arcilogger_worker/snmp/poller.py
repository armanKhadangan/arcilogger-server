"""Polling one source and the rates it gives (Wave 5 contract §6, N2 and N4).

One poll of a source is at most one walk of ``ifName`` (when an interface has no ``ifIndex``
yet: at start, after a name went missing or after ``sysUpTime`` went back) and one GET of
``sysUpTime.0`` and, per interface, ``ifHCInOctets``, ``ifHCOutOctets``, ``ifOperStatus`` and
``ifHighSpeed``. A rate is Δoctets * 8 / Δt between two good reads of the same interface; on a
WAN interface In is the download and Out the upload ("sahada doğrula" X-B2). A read after a
counter or ``sysUpTime`` decrease (the device restarted or the counters were cleared) gives no
rate: it only starts the next one. Only the 64-bit counters are read (``snmp_no_hc_counters``).

The state of every source is kept in ``state/wan-rates.json`` (:mod:`~.state`), so a restarted
worker goes on from its last reads.
"""

from __future__ import annotations

from datetime import datetime

from arcilogger_worker.snmp.client import (
    IF_HC_IN_OCTETS,
    IF_HC_OUT_OCTETS,
    IF_HIGH_SPEED,
    IF_NAME,
    IF_OPER_STATUS,
    MISSING,
    OPER_STATUS,
    SYS_UPTIME,
    SnmpClient,
    SnmpError,
    Status,
    Target,
    Value,
)
from arcilogger_worker.snmp.settings import SnmpSource
from arcilogger_worker.snmp.state import Counters, Line, SourceState


def _fail(line: Line, status: Status, now: datetime) -> None:
    line.status = status
    line.last_error_at = now


def _oids(index: int) -> tuple[str, str, str, str]:
    return (
        f"{IF_HC_IN_OCTETS}.{index}",
        f"{IF_HC_OUT_OCTETS}.{index}",
        f"{IF_OPER_STATUS}.{index}",
        f"{IF_HIGH_SPEED}.{index}",
    )


def _int(value: Value) -> int | None:
    return value if isinstance(value, int) else None


def prepare(source: SnmpSource, state: SourceState) -> None:
    """The lines of ``state`` follow the configured interfaces and capacities."""
    state.lines = {name: state.lines.get(name) or Line(name=name) for name in source.interfaces}
    for name, line in state.lines.items():
        line.capacity = source.capacity.get(name)


async def walk_names(client: SnmpClient, target: Target) -> dict[str, int]:
    """``ifName`` → ``ifIndex`` from a walk of ``ifName`` (the first index of a repeated name)."""
    names: dict[str, int] = {}
    for oid, name in (await client.walk(target, IF_NAME)).items():
        index = oid.rsplit(".", 1)[-1]
        if index.isdigit():
            names.setdefault(name, int(index))
    return names


async def poll_source(
    source: SnmpSource,
    target: Target | None,
    client: SnmpClient,
    state: SourceState,
    now: datetime,
) -> None:
    """Poll ``source`` once and update ``state`` (never raises for an SNMP failure)."""
    prepare(source, state)
    state.polled_at = now
    if state.watched_since is None:
        state.watched_since = now
    lines = list(state.lines.values())
    if target is None:  # a key is missing from the secret store (refused when configured)
        for line in lines:
            _fail(line, "auth_failed", now)
        return
    try:
        if any(line.if_index is None for line in lines):
            names = await walk_names(client, target)
            for line in lines:
                index = names.get(line.name)
                if index != line.if_index:
                    line.if_index, line.counters = index, None
        oids = [SYS_UPTIME]
        for line in lines:
            if line.if_index is not None:
                oids.extend(_oids(line.if_index))
        values = await client.get(target, oids)
    except SnmpError as exc:
        for line in lines:
            _fail(line, exc.status, now)
        return
    uptime = _int(values.get(SYS_UPTIME))
    went_back = uptime is not None and state.uptime is not None and uptime < state.uptime
    state.uptime = uptime
    if went_back:  # restarted: the indexes may have moved; walk again, start the rates again
        for line in lines:
            line.if_index, line.counters = None, None
        return
    for line in lines:
        if line.if_index is None:
            _fail(line, "no_such_interface", now)
            continue
        in_oid, out_oid, oper_oid, speed_oid = _oids(line.if_index)
        octets_in, octets_out = _int(values.get(in_oid)), _int(values.get(out_oid))
        oper = _int(values.get(oper_oid))
        if values.get(oper_oid) is MISSING and octets_in is None and octets_out is None:
            line.if_index, line.counters = None, None  # gone: walk again at the next poll
            _fail(line, "no_such_interface", now)
            continue
        line.oper_status = OPER_STATUS.get(oper) if oper is not None else None
        line.high_speed_mbps = _int(values.get(speed_oid))
        if octets_in is None or octets_out is None:
            _fail(line, "snmp_no_hc_counters", now)
            continue
        previous = line.counters
        line.counters = Counters(octets_in, octets_out, now)
        line.status = "ok"
        line.updated_at = now
        if previous is None or octets_in < previous.octets_in or octets_out < previous.octets_out:
            continue  # the first read, or the counters were reset: the next read gives a rate
        seconds = (now - previous.at).total_seconds()
        if seconds <= 0:
            continue
        line.add(
            now,
            (octets_in - previous.octets_in) * 8 / seconds,
            (octets_out - previous.octets_out) * 8 / seconds,
        )
