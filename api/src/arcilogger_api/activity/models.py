"""Response models of ``/activity`` (Wave 5 contract §6.2, M2 and N6).

``web/src/lib/activity-types.ts`` mirrors them: the same names, fields and optionality. Rates
are bits per second (bytes * 8 / seconds), volumes bytes. A subject is the usage engine's
(:class:`~arcilogger_api.usage_models.SubjectRef`: a person, a device or an address, by the
same attribution, ``usage_excluded`` and pseudonym rules as ``/usage``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from arcilogger_api.usage_models import SubjectRef

#: ``window`` of ``GET /activity/devices``, in minutes (default 5; any other value is 422
#: ``activity_window_invalid``).
ActivityWindow = Literal[1, 5, 15]
#: ``range`` of ``GET /activity/wan``.
WanRange = Literal["60m", "24h"]
#: A line's state in ``state/wan-rates.json`` (N4); each has TR/EN text (N5).
WanStatus = Literal[
    "ok",
    "timeout",
    "auth_failed",
    "no_such_interface",
    "snmp_no_hc_counters",
    "disabled",
]
#: ``ifOperStatus`` (RFC 2863), by name.
OperStatus = Literal[
    "up", "down", "testing", "unknown", "dormant", "not_present", "lower_layer_down"
]


class ActivityDomain(BaseModel):
    #: As in ``/usage``: a registrable domain, a FortiGuard service name or an address.
    domain: str
    kind: Literal["domain", "service", "ip"]
    #: Download and upload in the window.
    bytes: int


class ActivityApp(BaseModel):
    app: str
    #: Download and upload in the window.
    bytes: int


class ActivityRow(BaseModel):
    subject: SubjectRef
    download_bytes: int
    upload_bytes: int
    download_bps: float
    upload_bps: float
    #: Traffic log rows (sessions) of the subject in the window.
    sessions: int
    #: At most 3, by bytes; background traffic excluded.
    top_domains: list[ActivityDomain]
    #: At most 3, by bytes.
    top_apps: list[ActivityApp]
    #: The newest traffic row of the subject.
    last_seen: datetime


class ActivityTotals(BaseModel):
    """The company in the window: every subject, also those beyond the 200 rows."""

    download_bytes: int
    upload_bytes: int
    download_bps: float
    upload_bps: float
    sessions: int
    #: Subjects with traffic in the window.
    subjects: int


class ActivityMinute(BaseModel):
    #: The minute's start.
    minute: datetime
    download_bytes: int
    upload_bytes: int


class ActivityDevices(BaseModel):
    """``GET /activity/devices`` (M2)."""

    window: ActivityWindow
    #: Sorted by download, at most 200.
    rows: list[ActivityRow]
    #: More subjects had traffic than ``rows`` holds.
    more: bool
    totals: ActivityTotals
    #: Each of the last 60 minutes, oldest first.
    per_minute: list[ActivityMinute]
    #: The newest traffic row (null with none).
    data_until: datetime | None
    generated_at: datetime


class WanCapacity(BaseModel):
    """The line's capacity as configured (``capacity_mbps``), in Mbit/s."""

    down: float
    up: float


class WanPoint(BaseModel):
    time: datetime
    download_bps: float
    upload_bps: float


class WanLine(BaseModel):
    """One WAN interface of an SNMP source (N6)."""

    #: The log source (``[[sources]]`` name) the line is polled on.
    source: str
    #: ``ifName``.
    name: str
    oper_status: OperStatus | None
    #: The newest rate (null before the first good sample).
    download_bps: float | None
    upload_bps: float | None
    capacity_mbps: WanCapacity | None
    #: ``range=60m``: the samples at full resolution; ``24h``: 1-minute averages. Oldest first.
    series: list[WanPoint]
    status: WanStatus
    #: The newest good sample (null with none).
    updated_at: datetime | None
    last_error_at: datetime | None


class WanRates(BaseModel):
    """``GET /activity/wan`` (N6)."""

    range: WanRange
    lines: list[WanLine]
    generated_at: datetime
