"""The answer of ``GET /system/health`` (Wave 4 contract 2.6.1).

Mirrored verbatim in ``web/src/lib/system-health-types.ts``: the same names, fields and
optionality, every field required. Times are site time with offset (``to_site``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel

ServiceName = Literal["ArciCollector", "ArciIngest", "ArciWorker", "ArciApi"]
TaskName = Literal[
    "Seal",
    "VerifyDaily",
    "VerifyWeekly",
    "VerifyMonthly",
    "Replica",
    "Retention",
    "Credit",
    "ClockCheck",
    "Backup",
    "Requests",
    "ApplySystem",
]
TaskHealthState = Literal["ok", "late", "missing", "not_used", "not_observable"]
JobHealthState = Literal["ok", "failing", "skipped", "running", "never"]


class ServiceHealth(BaseModel):
    name: ServiceName
    state: Literal["running", "stale", "missing"]
    #: ``updated_at`` of the service's status file (ArciApi: now, it answers).
    updated_at: AwareDatetime | None


class AgentHealth(BaseModel):
    state: Literal["ok", "stale", "missing", "disabled"]
    heartbeat_at: AwareDatetime | None


class ApplySystemHealth(BaseModel):
    state: Literal["ok", "failed", "never", "disabled"]
    at: AwareDatetime | None
    code: str | None


class TaskHealth(BaseModel):
    task: TaskName
    schedule_tr: str
    schedule_en: str
    state: TaskHealthState
    last_evidence_at: AwareDatetime | None
    #: The file the state is judged by, relative to the data folder (empty: none).
    evidence: str


class JobHealth(BaseModel):
    job: str
    schedule_tr: str
    schedule_en: str
    #: Paid module the job belongs to (skipped while it is off).
    module: str | None
    state: JobHealthState
    last_status: str | None
    last_started: AwareDatetime | None
    last_finished: AwareDatetime | None
    #: Of the newest finished run of the last 7 days (``job_runs``).
    duration_ms: int | None
    #: Failed runs in a row.
    failures: int
    next_due: AwareDatetime | None


class ClockSampleOut(BaseModel):
    time: AwareDatetime
    server: str | None
    ok: bool
    offset_ms: float | None
    delay_ms: float | None
    stratum: int | None


class TimeHealth(BaseModel):
    ntp_server: str | None
    fallback_server: str | None
    alert_offset_ms: int
    #: The Windows Time peer list ApplySystem applied last (``null`` without its report).
    applied_peers: str | None
    applied_at: AwareDatetime | None
    #: The clock samples of the last 24 hours, newest first.
    samples: list[ClockSampleOut]


class SystemHealth(BaseModel):
    checked_at: AwareDatetime
    services: list[ServiceHealth]
    agent: AgentHealth
    apply_system: ApplySystemHealth
    tasks: list[TaskHealth]
    jobs: list[JobHealth]
    time: TimeHealth
