"""What the Sistem page shows about the server's resources (Wave 4 contract 2.5.2).

Everything is read, nothing is asked or changed: the host's processor, memory and uptime
(:mod:`arcilogger_core.hostinfo`), the data and system drives with their forecast (the samples
of the worker job ``system.sample``), the database's size, the collector's and ingest's status
files, and the versions.

**No sender address leaves this module.** Both status files key their sources by spool name,
which carries the sender's address for an unknown sender (``unknown-<ip>``) and for each member
of a network source (``<name>-<ip>``). So only configured sources are listed, under their
configured name: an exact source's entry as it is, a network source's members summed under the
network's name. Every other entry is only counted (``unknown_senders``, ``unknown_sources``);
the Kaynaklar page is where unknown senders are looked at. Error texts of the files (which can
name a path or an address) are not returned either, only whether there is one.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import shutil
import sys
import threading
import time
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Final, Literal

from sqlalchemy import bindparam, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from arcilogger_api import __version__
from arcilogger_api.system.forecast import drive_samples, forecast
from arcilogger_api.system.models import (
    CollectorInfo,
    CollectorSource,
    DatabaseInfo,
    DriveInfo,
    DriveRole,
    HostInfo,
    IngestInfo,
    IngestSource,
    StatusFileState,
    TableSize,
    UnknownSenders,
    VersionInfo,
)
from arcilogger_core import PRODUCT_NAME, RELEASE_DATE, hostinfo
from arcilogger_core import __version__ as core_version
from arcilogger_core.admin.contract import SERVICE_FRESH
from arcilogger_core.config import Config, SourceConfig
from arcilogger_core.layout import Layout
from arcilogger_db.models import Base
from arcilogger_db.settings import get_setting
from arcilogger_worker.alerts.system import DISK_CRITICAL_PERCENT, drives
from arcilogger_worker.system_jobs import read_history

log = logging.getLogger("arcilogger_api.system")

#: Readings of the processor counters closer together than this keep the previous percentage.
CPU_MIN_INTERVAL: Final = 1.0
#: Database sizes are measured at most this often (seconds).
DATABASE_TTL: Final = 300.0
#: Largest tables listed.
TABLES_SHOWN: Final = 10
#: ``alerts.disk_used_percent`` when the setting is missing or not a number.
DEFAULT_WARNING_PERCENT: Final = 80


# -- host -----------------------------------------------------------------------------------------


@dataclass
class CpuMeter:
    """The previous processor reading of this API process (contract 2.5.2: "a lock-guarded
    module value"). The first reading answers ``None``; a later one the busy percentage since
    the previous reading, which is replaced once it is :data:`CPU_MIN_INTERVAL` old (closer
    readings answer the last percentage)."""

    clock: Callable[[], float] = time.monotonic
    _previous: tuple[hostinfo.CpuTimes, float] | None = None
    _percent: int | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def read(self, times: hostinfo.CpuTimes | None) -> int | None:
        if times is None:
            return None
        now = self.clock()
        with self._lock:
            if self._previous is None:
                self._previous = (times, now)
                return None
            before, at = self._previous
            if now - at >= CPU_MIN_INTERVAL:
                self._percent = hostinfo.cpu_percent(before, times)
                self._previous = (times, now)
            return self._percent


#: The meter of this process.
CPU: Final = CpuMeter()


Platform = Literal["windows", "linux", "other"]
Storage = Literal["postgresql", "timescaledb"]


def _platform() -> Platform:
    name: Platform
    if sys.platform == "win32":
        name = "windows"
    else:
        name = "linux" if sys.platform.startswith("linux") else "other"
    return name


def host_info(meter: CpuMeter | None = None) -> HostInfo:
    memory = hostinfo.memory()
    return HostInfo(
        platform=_platform(),
        cpu_percent=(meter or CPU).read(hostinfo.cpu_times()),
        memory_total_bytes=memory.total_bytes if memory else None,
        memory_used_bytes=memory.used_bytes if memory else None,
        uptime_seconds=hostinfo.uptime_seconds(),
    )


# -- drives ---------------------------------------------------------------------------------------


def warning_percent(session: Session) -> int:
    value = get_setting(session, "alerts.disk_used_percent")
    return value if type(value) is int else DEFAULT_WARNING_PERCENT


def drive_infos(layout: Layout, data_dir: Path, *, warning: int, today: date) -> list[DriveInfo]:
    """The data drive and (when it is another device) the system drive, as R20-disk sees them,
    each with the forecast of its samples."""
    history = read_history(layout.system_history)
    found: list[DriveInfo] = []
    roles: tuple[DriveRole, ...] = ("data", "system")
    for role, (label, path) in zip(roles, drives(data_dir), strict=False):
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            log.warning("disk usage of %s: %s", path, exc)
            continue
        if usage.total <= 0:
            continue
        used = usage.total - usage.free
        found.append(
            DriveInfo(
                role=role,
                path=label,
                total_bytes=usage.total,
                used_bytes=used,
                free_bytes=usage.free,
                used_percent=used * 100 // usage.total,
                warning_percent=warning,
                critical_percent=DISK_CRITICAL_PERCENT,
                forecast=forecast(
                    drive_samples(history, label),
                    total=usage.total,
                    warning_percent=warning,
                    today=today,
                    used_now=used,
                ),
            )
        )
    return found


# -- database -------------------------------------------------------------------------------------

#: Size of each named table with every descendant (partitions, a hypertable's chunks).
_TABLE_SIZES: Final = text(
    """
    WITH RECURSIVE tree AS (
        SELECT t.name, c.oid AS rel
        FROM unnest(CAST(:names AS text[])) AS t(name)
        JOIN pg_class c ON c.oid = to_regclass(quote_ident(t.name))
        UNION ALL
        SELECT tree.name, i.inhrelid
        FROM tree JOIN pg_inherits i ON i.inhparent = tree.rel
    )
    SELECT name, CAST(sum(pg_total_relation_size(rel)) AS bigint) AS bytes
    FROM tree GROUP BY name
    """
).bindparams(bindparam("names"))
#: A hypertable's own size function, which knows its compressed chunks.
_HYPERTABLE_SIZES: Final = text(
    """
    SELECT hypertable_name AS name,
           CAST(hypertable_size(
               CAST(format('%I.%I', hypertable_schema, hypertable_name) AS regclass)
           ) AS bigint) AS bytes
    FROM timescaledb_information.hypertables
    WHERE hypertable_schema = current_schema()
    """
)


def storage_of(session: Session) -> Storage:
    """``timescaledb`` when the upgrade made hypertables (the ``db.timescale`` status row)."""
    status = get_setting(session, "db.timescale") or {}
    storage = status.get("storage") if isinstance(status, dict) else None
    return "timescaledb" if storage == "hypertables" else "postgresql"


def measure_database(session: Session, now: datetime) -> DatabaseInfo:
    """The database's size and its :data:`TABLES_SHOWN` largest tables (inside a savepoint: a
    failure leaves the request's transaction usable, answers ``null`` and is logged)."""
    storage = storage_of(session)
    names = sorted(Base.metadata.tables)
    try:
        with session.begin_nested():
            total = session.scalar(text("SELECT pg_database_size(current_database())"))
            sizes = {
                str(row.name): int(row.bytes or 0)
                for row in session.execute(_TABLE_SIZES, {"names": names})
            }
            if storage == "timescaledb":
                sizes |= {
                    str(row.name): int(row.bytes or 0)
                    for row in session.execute(_HYPERTABLE_SIZES)
                    if str(row.name) in sizes
                }
    except SQLAlchemyError as exc:
        log.warning("database sizes: %s", exc)
        return DatabaseInfo(storage=storage, total_bytes=None, tables=[], measured_at=None)
    largest = sorted(sizes.items(), key=lambda item: (-item[1], item[0]))[:TABLES_SHOWN]
    return DatabaseInfo(
        storage=storage,
        total_bytes=int(total) if total is not None else None,
        tables=[TableSize(name=name, bytes=size) for name, size in largest],
        measured_at=now,
    )


# -- status files ---------------------------------------------------------------------------------


def _number(value: Any) -> int:
    return value if type(value) is int else 0


def _float(value: Any) -> float | None:
    return float(value) if type(value) in (int, float) else None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _total(value: Any) -> int:
    """A count, or the sum of a ``{key: count}`` object (the collector counts drops per
    sender or transport)."""
    if isinstance(value, dict):
        return sum(_number(item) for item in value.values())
    return _number(value)


def read_status(path: Path, now: datetime) -> tuple[StatusFileState, dict[str, Any]]:
    """The file's state and its content (``{}`` when missing or unreadable)."""
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return "missing", {}
    except (OSError, ValueError) as exc:
        log.warning("%s cannot be read: %s", path, exc)
        return "missing", {}
    if not isinstance(data, dict):
        return "missing", {}
    updated = _time(data.get("updated_at"))
    if updated is None or now - updated > SERVICE_FRESH:
        return "stale", data
    return "running", data


def _member_of(entry: str, source: SourceConfig) -> bool:
    """Whether the spool name ``entry`` is a member ``<name>-<ip>`` of the network source.

    The collector writes the member's address with ``:`` and a zone's ``%`` as ``-``
    (:meth:`~arcilogger_core.config.Config.source_name_for`); an address is taken only when it
    gives back exactly this name (a zone id may hold ``-`` itself)."""
    prefix = f"{source.name}-"
    if not entry.startswith(prefix):
        return False
    parts = entry[len(prefix) :].split("-")
    for end in range(len(parts), 0, -1):
        head, zone = "-".join(parts[:end]), "-".join(parts[end:])
        for text_ in dict.fromkeys((head, head.replace("-", ":"))):
            try:
                address = ipaddress.ip_address(f"{text_}%{zone}" if zone else text_)
            except ValueError:
                continue
            if prefix + str(address).replace(":", "-").replace("%", "-") == entry:
                return address in ipaddress.ip_network(source.address, strict=False)
    return False


def configured_name(entry: str, config: Config) -> str | None:
    """The configured source a status-file entry belongs to, or ``None`` (an unknown sender,
    a source removed since, anything else)."""
    for source in config.sources:
        if not source.is_network and entry == source.name:
            return source.name
    for source in config.sources:
        if source.is_network and _member_of(entry, source):
            return source.name
    return None


def _grouped(
    entries: Any, config: Config
) -> tuple[dict[str, list[Mapping[str, Any]]], list[Mapping[str, Any]]]:
    """The entries of a ``sources`` object by configured name (in the configuration's order),
    and the others."""
    order = {source.name: index for index, source in enumerate(config.sources)}
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    others: list[Mapping[str, Any]] = []
    if not isinstance(entries, dict):
        return grouped, others
    for entry, value in entries.items():
        if not isinstance(value, dict):
            continue
        name = configured_name(str(entry), config)
        if name is None:
            others.append(value)
        else:
            grouped.setdefault(name, []).append(value)
    return dict(sorted(grouped.items(), key=lambda item: order[item[0]])), others


def _newest(values: Iterable[datetime | None]) -> datetime | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


def collector_info(layout: Layout, config: Config, now: datetime) -> CollectorInfo:
    state, data = read_status(layout.collector_status, now)
    grouped, others = _grouped(data.get("sources"), config)
    write_error = data.get("spool_write_error")
    return CollectorInfo(
        state=state,
        updated_at=_time(data.get("updated_at")),
        started_at=_time(data.get("started_at")),
        sources=[
            CollectorSource(
                name=name,
                messages=sum(_number(member.get("messages")) for member in members),
                bytes=sum(_number(member.get("bytes")) for member in members),
                last_received=_newest(_time(member.get("last_received")) for member in members),
            )
            for name, members in grouped.items()
        ],
        unknown_senders=UnknownSenders(
            count=len(others),
            messages=sum(_number(other.get("messages")) for other in others),
            bytes=sum(_number(other.get("bytes")) for other in others),
        ),
        dropped_unknown=_total(data.get("dropped_unknown")),
        dropped_transport=_total(data.get("dropped_transport")),
        write_failures=_total(data.get("write_failures")),
        write_error_since=_time(write_error.get("since"))
        if isinstance(write_error, dict)
        else None,
        torn_lines=_number(data.get("torn_lines_total")),
        tcp_connections=_number(data.get("tcp_connections")),
        tcp_framing_errors=_number(data.get("tcp_framing_errors")),
    )


def _largest(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return max(known) if known else None


def _sum(values: Iterable[float | None]) -> float | None:
    known = [value for value in values if value is not None]
    return sum(known) if known else None


def ingest_info(layout: Layout, config: Config, now: datetime) -> IngestInfo:
    state, data = read_status(layout.ingest_status, now)
    grouped, others = _grouped(data.get("sources"), config)
    database = data.get("database")
    workers = data.get("parse_workers")
    totals = data.get("totals")
    service_state = data.get("state")
    return IngestInfo(
        state=state,
        service_state=service_state if isinstance(service_state, str) else None,
        updated_at=_time(data.get("updated_at")),
        database_ok=database.get("ok")
        if isinstance(database, dict) and isinstance(database.get("ok"), bool)
        else None,
        workers_configured=_count(workers, "configured"),
        workers_active=_count(workers, "active"),
        lines_per_second=_float(totals.get("lines_per_second"))
        if isinstance(totals, dict)
        else None,
        sources=[
            IngestSource(
                name=name,
                lag_bytes=sum(_number(member.get("lag_bytes")) for member in members),
                lag_seconds=_largest(_float(member.get("lag_seconds")) for member in members),
                lines_per_second=_sum(_float(member.get("lines_per_second")) for member in members),
                # Days, not streams: every member of a network lists the same day.
                unfinished_days=len(
                    {
                        day
                        for member in members
                        if isinstance(days := member.get("unfinished_days"), list)
                        for day in days
                        if isinstance(day, str)
                    }
                ),
                has_error=any(member.get("last_error") is not None for member in members),
            )
            for name, members in grouped.items()
        ],
        unknown_sources=len(others),
    )


def _count(block: Any, key: str) -> int | None:
    if not isinstance(block, dict):
        return None
    value = block.get(key)
    return value if type(value) is int else None


# -- versions -------------------------------------------------------------------------------------


def version_info() -> VersionInfo:
    return VersionInfo(
        product=PRODUCT_NAME,
        version=__version__,
        core_version=core_version,
        release_date=date.fromisoformat(RELEASE_DATE),
    )
