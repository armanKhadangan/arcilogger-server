"""``GET /api/v1/system/health`` — services, tasks, jobs and the clock (Wave 4 contract 2.6).

=============  ==========================================================================
Block          Read from
=============  ==========================================================================
services       ``updated_at`` of the collector's, the ingest's and the worker's status file
               (``running`` while younger than ``SERVICE_FRESH``, 2 minutes); ArciApi is
               ``running`` (it answers)
agent          the heartbeat of ArciAdmin (``admin\\status\\agent.json``, ``Channel``);
               ``disabled`` while ``policy.json`` switches the browser administration off
apply_system   ApplySystem's ``admin\\system\\status.json`` (``Channel.system``)
tasks          the evidence each scheduled task leaves (:mod:`.tasks`)
jobs           ``default_jobs``, ``state/worker-status.json``, ``job_runs`` (:mod:`.jobs`)
time           ``[clock]``, the peers ApplySystem applied, the samples of the last 24 hours
               (``state/clock/<day>.jsonl`` of today and yesterday), newest first
=============  ==========================================================================

``system.read`` (administrators); not licence-gated (5651 Temel), not audited (no data about
log subjects: service, task and job names, times, configured time servers), not cached. The
router is mounted by ``routers/system_health.py`` (package W4-F).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends

from arcilogger_api.admin_channel import SERVICES, Channel, ChannelDep
from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep
from arcilogger_api.security import CurrentUser, require
from arcilogger_api.system_health.jobs import job_health
from arcilogger_api.system_health.models import (
    AgentHealth,
    ApplySystemHealth,
    ClockSampleOut,
    ServiceHealth,
    ServiceName,
    SystemHealth,
    TimeHealth,
)
from arcilogger_api.system_health.tasks import Evidence, aware_time, clock_entries, task_health
from arcilogger_core.admin.contract import SERVICE_FRESH
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import to_site, utc_now

router = APIRouter(prefix="/system/health", tags=["system"])

SystemReadDep = Annotated[CurrentUser, Depends(require("system.read"))]

#: The services that write a status file (``state/<service>-status.json``), in the page's order.
STATUS_FILE_SERVICES: Final[tuple[ServiceName, ...]] = ("ArciCollector", "ArciIngest", "ArciWorker")
#: Clock samples shown: the hourly probe of the last 24 hours.
SAMPLE_WINDOW: Final = timedelta(hours=24)


def _site(value: datetime | None) -> datetime | None:
    return None if value is None else to_site(value)


def service_health(layout: Layout, now: datetime) -> list[ServiceHealth]:
    """The three services with a status file, then ArciApi. A file without a readable
    ``updated_at`` is judged by its modification time (``Channel.service`` judges every file
    that way)."""
    found = []
    for name in STATUS_FILE_SERVICES:
        path = SERVICES[name](layout)
        try:
            modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
            data = json.loads(path.read_bytes())
        except OSError:
            found.append(ServiceHealth(name=name, state="missing", updated_at=None))
            continue
        except ValueError:
            data = None
        updated = (
            aware_time(data.get("updated_at")) if isinstance(data, dict) else None
        ) or modified
        found.append(
            ServiceHealth(
                name=name,
                state="running" if now - updated <= SERVICE_FRESH else "stale",
                updated_at=to_site(updated),
            )
        )
    found.append(ServiceHealth(name="ArciApi", state="running", updated_at=to_site(now)))
    return found


def apply_system_health(channel: Channel) -> ApplySystemHealth:
    system = channel.system
    at = None if system is None else _site(system.at)
    code = None if system is None else system.code
    if not channel.policy.enabled:
        return ApplySystemHealth(state="disabled", at=at, code=code)
    if system is None:
        return ApplySystemHealth(state="never", at=None, code=None)
    return ApplySystemHealth(state="ok" if system.ok else "failed", at=at, code=code)


def _ms(value: Any) -> float | None:
    return value / 1000 if type(value) is int else None


def time_health(
    config: Config, channel: Channel, samples: list[tuple[datetime, dict[str, Any]]], now: datetime
) -> TimeHealth:
    """``[clock]``, ApplySystem's peers and the samples of the last 24 hours, newest first."""
    system = channel.system
    shown = []
    for at, sample in reversed(samples):
        if now - at > SAMPLE_WINDOW:
            break
        ok = sample.get("ok") is True
        server = sample.get("server")
        stratum = sample.get("stratum")
        shown.append(
            ClockSampleOut(
                time=to_site(at),
                server=server if isinstance(server, str) and server else None,
                ok=ok,
                offset_ms=_ms(sample.get("offset_us")) if ok else None,
                delay_ms=_ms(sample.get("delay_us")) if ok else None,
                stratum=stratum if type(stratum) is int else None,
            )
        )
    return TimeHealth(
        ntp_server=config.clock.ntp_server or None,
        fallback_server=config.clock.fallback_server or None,
        alert_offset_ms=config.clock.alert_offset_ms,
        applied_peers=system.time.peers if system is not None else None,
        applied_at=_site(system.time.changed_at) if system is not None else None,
        samples=shown,
    )


@router.get("")
def system_health(
    db: DbSession, _: SystemReadDep, layout: LayoutDep, config: ConfigDep, channel: ChannelDep
) -> SystemHealth:
    """The health of the services, the agent, ApplySystem, the scheduled tasks, the worker
    jobs and the clock (see the module text)."""
    now = utc_now()
    samples = clock_entries(layout, now)
    agent = channel.agent
    evidence = Evidence(
        layout=layout,
        config=config,
        now=now,
        clock_times=tuple(at for at, _ in samples),
        system_at=None if channel.system is None else channel.system.at,
        administration=channel.policy.enabled,
    )
    return SystemHealth(
        checked_at=to_site(now),
        services=service_health(layout, now),
        agent=AgentHealth(
            state=channel.agent_state,
            heartbeat_at=None if agent is None else to_site(agent.heartbeat_at),
        ),
        apply_system=apply_system_health(channel),
        tasks=task_health(evidence),
        jobs=job_health(db, layout, config.worker.interval_seconds, now),
        time=time_health(config, channel, samples, now),
    )
