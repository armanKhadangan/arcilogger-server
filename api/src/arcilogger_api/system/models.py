"""JSON of ``GET /system``, ``GET /system/disk`` (Wave 4 contract 2.5.1) and
``GET /system/database`` (Wave 5 addendum C, C7).

The web mirror is ``web/src/lib/system-types.ts``: the same names, fields and optionality. Times
are timezone-aware, dates plain days; integers are bytes, counts or whole percents.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel

DriveRole = Literal["data", "system"]
#: ``missing``: no status file; ``stale``: not rewritten within 2 minutes (the service is
#: stopped or hangs); ``running``: fresh.
StatusFileState = Literal["running", "stale", "missing"]


class HostInfo(BaseModel):
    platform: Literal["windows", "linux", "other"]
    #: Busy share of the processors since the previous reading of this API process; ``null`` at
    #: the first reading or when the platform cannot be read.
    cpu_percent: int | None
    memory_total_bytes: int | None
    memory_used_bytes: int | None
    uptime_seconds: int | None


class DiskForecast(BaseModel):
    state: Literal["estimated", "insufficient", "not_growing"]
    #: Daily samples the forecast used (at most the newest 30).
    samples: int
    growth_bytes_per_day: int | None
    #: First day the trend line reaches ``warning_percent``. ``null``: at or above the mark, or
    #: beyond the horizon (100 years); whether the drive is already above the mark is read from
    #: ``used_bytes``/``used_percent`` against ``warning_percent``.
    warning_on: date | None
    #: First day the trend line reaches the drive's size (``null``: beyond the horizon).
    full_on: date | None


class DriveInfo(BaseModel):
    role: DriveRole
    #: The drive label (``D:``; the mount point elsewhere).
    path: str
    total_bytes: int
    used_bytes: int
    free_bytes: int
    used_percent: int
    #: ``alerts.disk_used_percent`` (R20-disk opens above it).
    warning_percent: int
    #: R20-disk is ``critical`` from this percent on.
    critical_percent: int
    forecast: DiskForecast


class TableSize(BaseModel):
    name: str
    bytes: int


class DatabaseInfo(BaseModel):
    storage: Literal["postgresql", "timescaledb"]
    #: ``pg_database_size``; ``null`` when the sizes could not be read.
    total_bytes: int | None
    #: The 10 largest tables, partitions and chunks included, largest first.
    tables: list[TableSize]
    #: When the sizes were measured (they are kept 5 minutes).
    measured_at: datetime | None


class CollectorSource(BaseModel):
    name: str
    messages: int
    bytes: int
    last_received: datetime | None


class UnknownSenders(BaseModel):
    """Status-file entries that are not a configured source, never listed by name."""

    count: int
    messages: int
    bytes: int


class CollectorInfo(BaseModel):
    state: StatusFileState
    updated_at: datetime | None
    started_at: datetime | None
    sources: list[CollectorSource]
    unknown_senders: UnknownSenders
    dropped_unknown: int
    dropped_transport: int
    write_failures: int
    write_error_since: datetime | None
    torn_lines: int
    tcp_connections: int
    tcp_framing_errors: int


class IngestSource(BaseModel):
    name: str
    lag_bytes: int
    lag_seconds: float | None
    lines_per_second: float | None
    unfinished_days: int
    #: The service recorded an error for the source (the text is not returned).
    has_error: bool


class IngestInfo(BaseModel):
    state: StatusFileState
    #: The service's own state (``running``, ``database_unavailable``, …).
    service_state: str | None
    updated_at: datetime | None
    database_ok: bool | None
    workers_configured: int | None
    workers_active: int | None
    lines_per_second: float | None
    sources: list[IngestSource]
    unknown_sources: int


class VersionInfo(BaseModel):
    product: str
    version: str
    core_version: str
    release_date: date


class SystemSummary(BaseModel):
    checked_at: datetime
    host: HostInfo
    drives: list[DriveInfo]
    database: DatabaseInfo
    collector: CollectorInfo
    ingest: IngestInfo
    version: VersionInfo


class SystemDisk(BaseModel):
    checked_at: datetime
    drives: list[DriveInfo]


class DatabaseGrowth(BaseModel):
    """The database's growth a day, fitted like a drive's forecast to the daily samples."""

    state: Literal["estimated", "insufficient", "not_growing"]
    samples: int
    bytes_per_day: int | None


class DatabaseSample(BaseModel):
    day: date
    bytes: int


class LastBackup(BaseModel):
    """``state/backup/last.json`` of the Backup task (only what the page shows)."""

    finished: datetime | None
    ok: bool
    #: The dump's file name (no folder) and size; ``null`` after a failed attempt.
    file: str | None
    bytes: int | None


class DatabaseDetails(BaseModel):
    """The Veritabanı page: what database ARCILOGGER uses, how big it is and how it grows."""

    checked_at: datetime
    storage: Literal["postgresql", "timescaledb"]
    #: ``SHOW server_version`` (``16.4``) and the TimescaleDB extension's version, if installed.
    server_version: str | None
    timescaledb_version: str | None
    #: The database's name and where the API reaches it (``[database]`` host and port).
    name: str | None
    host: str
    port: int
    total_bytes: int | None
    measured_at: datetime | None
    tables: list[TableSize]
    #: Connections to this database now, and the server's limit.
    connections: int | None
    max_connections: int | None
    #: ``db.retention_days``: days of logs the database keeps (older days are archived).
    window_days: int | None
    growth: DatabaseGrowth
    #: The daily samples of the last 30 days, oldest first.
    history: list[DatabaseSample]
    #: The data folder's drive with its forecast (PostgreSQL's own folder is usually on it).
    data_drive: DriveInfo | None
    last_backup: LastBackup | None


class BackupDump(BaseModel):
    """A dump of the backup folder (``state/backup/list.json`` of the Backup task)."""

    name: str
    taken: datetime | None
    bytes: int
    #: The SHA-256 the dump's sidecar records (``null`` without one).
    sha256: str | None


class BackupMirrorState(BaseModel):
    """The last copy to the backup copy's target (``state/backup/mirror.json``)."""

    finished: datetime | None
    ok: bool
    copied: int
    present: int
    pruned: int
    prune_refused: int
    #: ``{dump name: why}`` of the dumps that were not copied.
    failed: dict[str, str]
    error: str | None


class BackupsInfo(BaseModel):
    """``GET /system/backups``: Ayarlar → Yedekleme's list (Wave 5 addendum C, C5-C6)."""

    checked_at: datetime
    enabled: bool
    #: ``[backup] dir`` as a full path on the server.
    folder: str
    #: When the Backup task last wrote the list (``null``: no successful run yet).
    list_updated: datetime | None
    dumps: list[BackupDump]
    #: ``[backup] mirror_target`` (``null``: no copy) and its last run.
    mirror_target: str | None
    mirror: BackupMirrorState | None
