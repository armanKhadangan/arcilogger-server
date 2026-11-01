"""The Sistem samples (``web/e2e/samples/system.json``, ``system-disk.json``,
``system-health.json``), captured from the real API.

``capture_api_samples.py`` calls :func:`capture` on its main site after every other page's
samples (Wave 4 contract 2.2, 2.5, 2.6), with the capture (the app, its database and data
directory, the request helpers ``json`` and ``error``) and three signed-in clients: an
administrator, an auditor and an administrator who has not accepted the KVKK notice. It returns
``{sample file name: data}``.

The product runs on Windows, the capture on any machine, so what the endpoints read from the host
is fixed for the block: the platform is ``windows``, the drives are ``D:`` (data, 2 TB) and ``C:``
(system, 256 GB) of fixed sizes, the processor, memory and uptime readings are fixed, and the
database's size is the real measurement's answer with fixed byte counts (they differ with every
PostgreSQL build and every capture). The rest is read from files and rows the module writes at
the capture's "now" (13.10.2026 07:00), with modification times set from that clock (the file
system's clock is the machine's):

* ``state/system-history.json`` as the worker job ``system.sample`` writes it: 30 days of the
  data drive growing about 10 GB a day (``estimated``) and the system drive's first three samples
  (``insufficient``); for ``disk_not_growing`` 30 days of a data drive whose use went down (the
  retention deletes more than arrives);
* the collector's and ingest's status files of a running site (the two watched sources, one
  unknown sender, which only counts), with a fractional ingest lag and fractional lines per
  second;
* the worker's status file and ``job_runs`` rows: every job ``ok`` but ``alerts.system``
  (``failing``), ``alerts.notify`` (``skipped``: SMTP is off), ``leases.incremental``
  (``running``) and ``reports.purge`` (``never``);
* the evidence of the scheduled tasks: the seal states' modification times (the night after each
  sealed day; minutes ago for the day Seal retries), the daily and the weekly (Sunday 11.10)
  verification results (the monthly full check has not run yet: ``missing``), yesterday's backup
  (today's has not run: ``late``), ApplySystem's report and the agent's heartbeat (fresh), today's
  hourly clock samples (a 3.2 ms offset, one probe no server answered).

What the files hold: ``system.json`` ``summary`` (``GET /system``, the processor measured) and
``summary_first_reading`` (the first reading of a process: ``cpu_percent`` null),
``system-disk.json`` ``disk`` and ``disk_not_growing`` (``GET /system/disk``),
``system-health.json`` ``health``
(``GET /system/health``); each with ``errors.forbidden`` (the auditor: ``system.read`` is the
administrators' only). Everything the module writes is put back afterwards: the files as they
were (or removed), their modification times, the ``job_runs`` rows, the measured database sizes
kept by the app.
"""

from __future__ import annotations

import json
import os
import shutil
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, NamedTuple
from unittest import mock

from sqlalchemy import delete
from sqlalchemy.orm import Session

from arcilogger_api import __version__
from arcilogger_api.system import resources
from arcilogger_api.system import routes as system_routes
from arcilogger_api.system.models import DatabaseInfo, TableSize
from arcilogger_api.system_health.tasks import verify_state
from arcilogger_core import hostinfo
from arcilogger_core.admin.contract import SystemStatus, dump_document, parse_document
from arcilogger_core.seal import SEALED
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, site_day, utc_now
from arcilogger_db.models import Base, JobRun
from arcilogger_worker.alerts.system import backup_state_path
from arcilogger_worker.service import JobState, default_jobs
from arcilogger_worker.system_jobs import HISTORY_SCHEMA

if TYPE_CHECKING:
    from capture_api_samples import Capture

#: Keys this module's files compare differently, added to the script's defaults:
#: ``{file: (data-map keys, enum keys)}`` (``capture_api_samples.MAP_KEYS``, ``ENUM_KEYS``).
#: The page maps the platform, the drive roles and the storage to labels, the services, tasks and
#: a job's last status too.
SHAPE_KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "system": (frozenset(), frozenset({"platform", "role", "storage"})),
    "system-disk": (frozenset(), frozenset({"role"})),
    "system-health": (frozenset(), frozenset({"name", "task", "last_status"})),
}

API = "/api/v1"
SUMMARY = f"{API}/system"
DISK = f"{API}/system/disk"
HEALTH = f"{API}/system/health"

GB = 1_000_000_000


class Usage(NamedTuple):
    """What ``shutil.disk_usage`` answers."""

    total: int
    used: int
    free: int


@dataclass(frozen=True)
class Drive:
    label: str
    total: int
    #: Used bytes of the newest daily sample (today's, after 00:30).
    sampled: int
    #: Used bytes now (the morning's logs since the sample).
    now: int

    @property
    def path(self) -> Path:
        return Path(f"{self.label}\\")

    @property
    def usage(self) -> Usage:
        return Usage(self.total, self.now, self.total - self.now)


#: The data drive: a 2 TB disk, 59 % used, growing about 10 GB a day.
DATA = Drive("D:", 2_000_381_014_016, 1_180_412_345_344, 1_182_533_120_000)
DATA_GROWTH = 10_312_345_678
#: The data drive of ``disk_not_growing``: the retention deletes a little more than arrives.
DATA_FLAT = Drive("D:", 2_000_381_014_016, 1_101_204_684_800, 1_101_396_557_824)
DATA_SHRINK = 150_000_000
#: The system drive: 256 GB, 39 % used; three samples (too few for a forecast).
SYSTEM = Drive("C:", 255_953_203_200, 101_276_049_408, 101_298_786_304)
SYSTEM_SAMPLES = 3
#: Days of samples of the data drive (the forecast fits the newest 30).
HISTORY = 30

#: The host's readings: 32 GB of memory, 14 days up, two processor readings 30 s apart.
MEMORY = hostinfo.Memory(total_bytes=34_359_738_368, available_bytes=13_958_643_712)
UPTIME = 1_209_843
CPU_READINGS = (
    hostinfo.CpuTimes(busy=81_234_567, idle=402_345_678),
    hostinfo.CpuTimes(busy=81_924_567, idle=404_655_678),  # 23 % busy in between
)

#: The database: its size and its largest tables (real table names), in bytes.
DATABASE_TOTAL = 318_472_110_080
DATABASE_TABLES = (
    ("messages", 196_534_018_048),
    ("fg_traffic", 52_018_347_008),
    ("fg_web", 21_473_632_256),
    ("fg_dns", 13_902_413_824),
    ("search_counts", 6_871_646_208),
    ("mt_dhcp", 3_221_225_472),
    ("leases", 2_147_483_648),
    ("mt_arp", 1_610_612_736),
    ("job_runs", 912_261_120),
    ("audit_log", 402_653_184),
)

#: An unknown sender in the status files (counted only: its address never leaves the API).
UNKNOWN = "192.0.2.77"
#: ``(minutes before "now", offset µs, delay µs)`` of today's hourly clock samples; ``None``:
#: no server answered.
CLOCK = (
    (55, 3_200, 912),
    (115, 2_800, 1_046),
    (175, None, None),
    (235, -1_450, 887),
    (295, 2_150, 903),
    (355, 1_975, 921),
)

#: Jobs whose state is not ``ok`` and what it is (every other job ran and succeeded).
JOB_STATES = {
    "alerts.system": "failing",
    "alerts.notify": "skipped",
    "leases.incremental": "running",
    "reports.purge": "never",
}
#: The scheduled tasks' states the evidence gives (the capture's configuration: replica on,
#: retention on, the mock TSA, the backup on, browser administration on; the replica copied
#: only 04.-07.10).
TASK_STATES = {
    "Seal": "ok",
    "VerifyDaily": "ok",
    "VerifyWeekly": "ok",
    "VerifyMonthly": "missing",
    "Replica": "missing",
    "Retention": "not_observable",
    "Credit": "not_used",
    "ClockCheck": "ok",
    "Backup": "late",
    "Requests": "not_observable",
    "ApplySystem": "ok",
}


# -- putting back what the module writes ----------------------------------------------------------


@contextmanager
def kept(*paths: Path) -> Iterator[None]:
    """The files as they are now (content and modification time, or absence) and the folders
    the block creates for them, put back afterwards."""
    saved: list[tuple[Path, bytes | None, tuple[float, float] | None]] = []
    folders: list[Path] = []
    for path in paths:
        if path.exists():
            info = path.stat()
            saved.append((path, path.read_bytes(), (info.st_atime, info.st_mtime)))
        else:
            saved.append((path, None, None))
            folder = path.parent
            while not folder.exists():
                folders.append(folder)
                folder = folder.parent
    try:
        yield
    finally:
        for path, content, times in saved:
            if content is None:
                path.unlink(missing_ok=True)
                continue
            path.write_bytes(content)
            os.utime(path, times)
        for folder in folders:  # deepest first
            if folder.exists() and not any(folder.iterdir()):
                folder.rmdir()


@contextmanager
def kept_times(paths: list[Path]) -> Iterator[None]:
    """The modification times of ``paths``, put back afterwards (the content is not touched)."""
    saved = [(path, path.stat()) for path in paths]
    try:
        yield
    finally:
        for path, info in saved:
            os.utime(path, (info.st_atime, info.st_mtime))


def touch(path: Path, at: datetime) -> None:
    os.utime(path, (at.timestamp(), at.timestamp()))


def write_json(path: Path, data: Any, at: datetime) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    touch(path, at)


def site_at(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=SITE_TZ)


# -- the host -------------------------------------------------------------------------------------


@contextmanager
def windows_host(capture: Capture) -> Iterator[dict[str, Usage]]:
    """The platform, drives, processor, memory, uptime and database sizes of the block (module
    text); the drives' use is the returned map, by label, which the caller may change."""
    usage = {DATA.label: DATA.usage, SYSTEM.label: SYSTEM.usage}
    real_usage = shutil.disk_usage
    real_measure = resources.measure_database
    known = set(Base.metadata.tables)
    if not {name for name, _ in DATABASE_TABLES} <= known:
        raise SystemExit(f"system: DATABASE_TABLES names a table that does not exist: {known}")

    def drives(_data_dir: Path) -> list[tuple[str, Path]]:
        return [(DATA.label, DATA.path), (SYSTEM.label, SYSTEM.path)]

    def disk_usage(path: Any) -> Any:
        for label, found in usage.items():
            if path == Path(f"{label}\\"):
                return found
        return real_usage(path)

    def measure_database(session: Session, now: datetime) -> DatabaseInfo:
        measured = real_measure(session, now)
        if measured.total_bytes is None or not measured.tables:
            raise SystemExit(f"system: the database could not be measured: {measured}")
        tables = [TableSize(name=name, bytes=size) for name, size in DATABASE_TABLES]
        return measured.model_copy(update={"total_bytes": DATABASE_TOTAL, "tables": tables})

    with ExitStack() as stack:
        stack.enter_context(mock.patch.object(resources, "_platform", return_value="windows"))
        stack.enter_context(mock.patch.object(resources, "drives", drives))
        stack.enter_context(mock.patch.object(shutil, "disk_usage", disk_usage))
        stack.enter_context(mock.patch.object(hostinfo, "memory", return_value=MEMORY))
        stack.enter_context(mock.patch.object(hostinfo, "uptime_seconds", return_value=UPTIME))
        stack.enter_context(mock.patch.object(system_routes, "measure_database", measure_database))
        capture.app.state.system_database = None  # measured again, through the mock
        try:
            yield usage
        finally:
            capture.app.state.system_database = None


@contextmanager
def processor() -> Iterator[Callable[[], None]]:
    """A new processor meter (the first reading of a process); the returned function moves to
    the next reading, 30 seconds later."""
    step = [0]

    def cpu_times() -> hostinfo.CpuTimes:
        return CPU_READINGS[step[0]]

    meter = resources.CpuMeter(clock=lambda: 30.0 * step[0])

    def advance() -> None:
        step[0] += 1

    with (
        mock.patch.object(resources, "CPU", meter),
        mock.patch.object(hostinfo, "cpu_times", cpu_times),
    ):
        yield advance


# -- the files and rows ---------------------------------------------------------------------------


def history(data: Drive, slope: int, today: date) -> dict[str, Any]:
    """``state/system-history.json`` of the job ``system.sample``: :data:`HISTORY` days of the
    data drive (the newest ``data.sampled``, ``slope`` bytes a day and a little noise) and the
    system drive's newest :data:`SYSTEM_SAMPLES` days."""
    days = []
    for back in range(HISTORY - 1, -1, -1):
        noise = ((back * 7919) % 13 - 6) * 250_000_000 if back else 0
        drives = [
            {"path": data.label, "total": data.total, "used": data.sampled - back * slope + noise}
        ]
        if back < SYSTEM_SAMPLES:
            used = SYSTEM.sampled - back * 41_943_040
            drives.append({"path": SYSTEM.label, "total": SYSTEM.total, "used": used})
        days.append({"day": (today - timedelta(days=back)).isoformat(), "drives": drives})
    return {"schema": HISTORY_SCHEMA, "days": days}


def write_status_files(capture: Capture, now: datetime) -> None:
    """The collector's and ingest's status files of a running site (module text)."""
    layout = capture.layout
    today = site_day(now).isoformat()
    updated = now - timedelta(seconds=20)
    write_json(
        layout.collector_status,
        {
            "started_at": isoformat_site(now - timedelta(days=2, hours=5), "seconds"),
            "updated_at": isoformat_site(updated, "seconds"),
            "version": __version__,
            "sources": {
                "core-r1": {
                    "messages": 183_644,
                    "bytes": 31_219_480,
                    "last_received": isoformat_site(now - timedelta(seconds=50)),
                },
                "fortigate": {
                    "messages": 1_652_310,
                    "bytes": 1_102_092_770,
                    "last_received": isoformat_site(now - timedelta(seconds=4)),
                },
                f"unknown-{UNKNOWN}": {
                    "messages": 14,
                    "bytes": 2_114,
                    "last_received": isoformat_site(now - timedelta(hours=3)),
                },
            },
            "dropped_unknown": {},
            "dropped_transport": {},
            "write_failures": {},
            "spool_write_error": None,
            "spool_write_errors": {},
            "torn_lines_total": 0,
            "torn_lines": [],
            "tcp_connections": 0,
            "tcp_framing_errors": 0,
        },
        updated,
    )

    def source(lag_bytes: int, lag: float, rate: float, received: int) -> dict[str, Any]:
        return {
            "day": today,
            "byte_offset": 48_211_967,
            "line_number": 162_844,
            "read_from": "spool",
            "lag_bytes": lag_bytes,
            "lag_seconds": lag,
            "lines_per_second": rate,
            "last_received": isoformat_site(now - timedelta(seconds=received)),
            "unfinished_days": [today],
            "last_error": None,
        }

    updated = now - timedelta(seconds=3)
    write_json(
        layout.ingest_status,
        {
            "version": __version__,
            "pid": 4242,
            "state": "running",
            "started_at": isoformat_site(now - timedelta(days=2, hours=5), "seconds"),
            "updated_at": isoformat_site(updated, "seconds"),
            "database": {"ok": True, "last_error": None},
            "parse_workers": {"configured": 2, "active": 2, "restarts": 0, "last_error": None},
            "totals": {
                "lines": 1_835_954,
                "rows": 1_902_114,
                "parse_failures": 12,
                "batches": 3_120,
                "lines_per_second": 21.4,
            },
            "sources": {
                "core-r1": source(18_342, 2.6, 2.3, 53),
                "fortigate": source(0, 0.0, 19.1, 4),
                f"unknown-{UNKNOWN}": source(0, 0.0, 0.0, 10_800),
            },
        },
        updated,
    )


def job_states(capture: Capture, now: datetime) -> tuple[dict[str, JobState], list[JobRun]]:
    """The worker's job states (the worker started two days ago) and the ``job_runs`` rows of
    their last runs (module text)."""
    today = site_day(now)
    jobs = default_jobs(capture.config.worker.interval_seconds)
    unknown = set(JOB_STATES) - {job.name for job in jobs}
    if unknown:
        raise SystemExit(f"system: JOB_STATES names jobs the worker does not run: {unknown}")
    states: dict[str, JobState] = {}
    runs: list[JobRun] = []
    for index, job in enumerate(jobs):
        if JOB_STATES.get(job.name) == "never":
            continue  # no state, no run in the last 7 days
        duration = timedelta(milliseconds=180 + 37 * index)
        if job.every is None:
            started = site_at(today, job.daily_time) + timedelta(seconds=7)
            next_due = site_at(today + timedelta(days=1), job.daily_time)
        else:
            started = now - job.every / 2
            next_due = started + job.every
        state = JobState(
            last_started=started,
            last_finished=started + duration,
            last_status="ok",
            done_day=today if job.every is None else None,
            next_due=next_due,
            runs=40 + index,
        )
        expected = JOB_STATES.get(job.name, "ok")
        if expected == "failing":
            state.last_status = "failed"
            state.last_error = "OperationalError: server closed the connection unexpectedly"
            state.failures = 2
        elif expected == "skipped":
            state.last_status = "skipped"
        states[job.name] = state
        runs.append(
            JobRun(
                job=job.name,
                started_at=started,
                finished_at=started + duration,
                status=state.last_status,
                details={},
            )
        )
        if expected == "running":  # the worker is in its next run (job_runs only)
            runs.append(
                JobRun(
                    job=job.name,
                    started_at=now - timedelta(seconds=5),
                    finished_at=None,
                    status="running",
                    details={},
                )
            )
    return states, runs


def write_worker(capture: Capture, now: datetime) -> list[int]:
    """The worker's status file and its ``job_runs`` rows; the rows' ids."""
    states, runs = job_states(capture, now)
    updated = now - timedelta(seconds=10)
    write_json(
        capture.layout.worker_status,
        {
            "service": "arcilogger-worker",
            "state": "running",
            "started_at": (now - timedelta(days=2, hours=5)).isoformat(),
            "updated_at": isoformat_site(updated),
            "jobs": {name: state.as_json() for name, state in states.items()},
        },
        updated,
    )
    with capture.factory() as session:
        session.add_all(runs)
        session.commit()
        return [run.id for run in runs]


@dataclass(frozen=True)
class Verified:
    """A verification result ``arcilogger-core verify`` writes (``cmd_verify``)."""

    path: Path
    at: datetime
    first: date
    last: date

    def write(self) -> None:
        summary = {
            "at": isoformat_site(self.at, "seconds"),
            "from": self.first.isoformat(),
            "to": self.last.isoformat(),
            "days": (self.last - self.first).days + 1,
            "deep": False,
            "ok": True,
            "failed_days": [],
            "checks": [],
        }
        write_json(self.path, summary, self.at)


def seal_states(capture: Capture) -> list[Path]:
    seals = sorted((capture.layout.state / "seal").glob("*.json"))
    if not seals:
        raise SystemExit("system: the site has no seal states")
    return seals


def verifications(capture: Capture, today: date) -> list[Verified]:
    """The results of VerifyDaily (``--last 2`` at 01:00 today) and VerifyWeekly (``--last 90``
    on Sunday at 03:00: every day sealed by then, the chain is young); the monthly full check
    has not run yet (the chain started in October)."""
    sealed = sorted(
        date.fromisoformat(path.stem)
        for path in seal_states(capture)
        if json.loads(path.read_bytes()).get("status") == SEALED
    )
    sunday = today - timedelta(days=(today.weekday() + 1) % 7)
    weekly = [day for day in sealed if day < sunday]
    daily = [day for day in sealed if day < today - timedelta(days=1)][-2:]
    if len(weekly) < 3 or len(daily) < 2:
        raise SystemExit(f"system: too few sealed days to verify: {sealed}")
    layout = capture.layout
    return [
        Verified(verify_state(layout, 2), site_at(today, time(1, 0, 41)), daily[0], daily[-1]),
        Verified(
            verify_state(layout, len(weekly)),
            site_at(sunday, time(3, 0, 52)),
            weekly[0],
            weekly[-1],
        ),
    ]


def task_evidence(capture: Capture, now: datetime, verified: list[Verified]) -> None:
    """The evidence of the scheduled tasks (module text)."""
    layout = capture.layout
    today = site_day(now)
    for path in seal_states(capture):
        if json.loads(path.read_bytes()).get("status") == SEALED:
            touch(path, site_at(date.fromisoformat(path.stem) + timedelta(days=1), time(0, 12, 30)))
        else:  # Seal retries a day it could not seal on every run (every 5 minutes)
            touch(path, now - timedelta(minutes=3))
    for result in verified:
        result.write()
    # Today's backup (01:40) has not run: the newest is yesterday's.
    finished = site_at(today - timedelta(days=1), time(1, 52, 10))
    write_json(
        backup_state_path(layout),
        {
            "schema": 1,
            "finished": isoformat_site(finished, "seconds"),
            "ok": True,
            "file": "D:\\ARCILOGGER\\backups\\scheduled\\arcilogger-20261012-0140.dump",
            "size": 1_281_734_656,
            "sha256": "0" * 64,
            "error": None,
        },
        finished,
    )

    status = capture.installed.layout.system_status
    system = parse_document(SystemStatus, status.read_bytes())
    status.write_bytes(dump_document(system.model_copy(update={"at": now - timedelta(minutes=4)})))

    servers = capture.config.clock.servers  # the first one that answers is sampled
    if not servers:
        raise SystemExit("system: the capture's site configures no time server")
    lines = []
    for minutes, offset, delay in sorted(CLOCK, reverse=True):
        at = isoformat_site(now - timedelta(minutes=minutes), "seconds")
        if offset is None:  # as ``check_clock`` writes a probe no server answered
            errors = "; ".join(f"{server}: timed out" for server in servers)
            sample: dict[str, Any] = {
                "time": at,
                "server": None,
                "offset_us": None,
                "ok": False,
                "error": errors,
            }
        else:
            sample = {
                "time": at,
                "server": servers[0],
                "offset_us": offset,
                "delay_us": delay,
                "stratum": 2,
                "ok": True,
            }
        lines.append(json.dumps(sample, sort_keys=True) + "\n")
    clock = layout.clock_samples(today)
    clock.parent.mkdir(parents=True, exist_ok=True)
    clock.write_text("".join(lines), encoding="utf-8")
    touch(clock, now - timedelta(minutes=CLOCK[0][0]))


# -- the samples ----------------------------------------------------------------------------------


def capture(capture: Capture, admin: Any, auditor: Any, newcomer: Any) -> dict[str, Any]:
    """The Sistem samples (see the module text)."""
    now = utc_now()
    today = site_day(now)
    layout = capture.layout
    if not capture.installed.layout.system_status.exists():
        raise SystemExit("system: ApplySystem has not reported on the capture's site")
    verified = verifications(capture, today)
    written = (
        layout.collector_status,
        layout.ingest_status,
        layout.worker_status,
        layout.system_history,
        layout.clock_samples(today),
        backup_state_path(layout),
        capture.installed.layout.system_status,
        *(result.path for result in verified),
    )
    capture.agent()  # a fresh heartbeat
    with kept(*written), kept_times(seal_states(capture)), windows_host(capture) as usage:
        write_status_files(capture, now)
        task_evidence(capture, now, verified)
        runs = write_worker(capture, now)
        try:
            return _capture(capture, admin, auditor, usage, today)
        finally:
            with capture.factory() as session:
                session.execute(delete(JobRun).where(JobRun.id.in_(runs)))
                session.commit()


def _capture(
    capture: Capture, admin: Any, auditor: Any, usage: dict[str, Usage], today: date
) -> dict[str, Any]:
    layout = capture.layout
    layout.system_history.write_text(json.dumps(history(DATA, DATA_GROWTH, today), indent=1))

    system: dict[str, Any] = {}
    with processor() as advance:
        system["summary_first_reading"] = capture.json(admin.get(SUMMARY), 200)
        advance()
        system["summary"] = capture.json(admin.get(SUMMARY), 200)
    system["errors"] = {"forbidden": capture.error(auditor.get(SUMMARY), 403, "forbidden")}
    first, summary = system["summary_first_reading"], system["summary"]
    if first["host"]["cpu_percent"] is not None or summary["host"]["cpu_percent"] is None:
        raise SystemExit(f"system: the processor readings: {first['host']} {summary['host']}")
    if UNKNOWN in json.dumps(summary):
        raise SystemExit("system: an unknown sender's address left the API (contract 2.5.2)")
    ingest = summary["ingest"]
    fractions = [ingest["lines_per_second"]] + [
        value
        for source in ingest["sources"]
        for value in (source["lag_seconds"], source["lines_per_second"])
    ]
    if not any(isinstance(value, float) and not value.is_integer() for value in fractions):
        raise SystemExit(f"system: no fractional ingest value: {ingest}")

    disk: dict[str, Any] = {"disk": capture.json(admin.get(DISK), 200)}
    expect_forecasts(disk["disk"], "estimated", "insufficient")
    layout.system_history.write_text(json.dumps(history(DATA_FLAT, -DATA_SHRINK, today), indent=1))
    usage[DATA.label] = DATA_FLAT.usage
    disk["disk_not_growing"] = capture.json(admin.get(DISK), 200)
    expect_forecasts(disk["disk_not_growing"], "not_growing", "insufficient")
    disk["errors"] = {"forbidden": capture.error(auditor.get(DISK), 403, "forbidden")}

    health: dict[str, Any] = {"health": capture.json(admin.get(HEALTH), 200)}
    health["errors"] = {"forbidden": capture.error(auditor.get(HEALTH), 403, "forbidden")}
    expect_health(health["health"])
    return {"system": system, "system-disk": disk, "system-health": health}


def expect_forecasts(answer: dict[str, Any], data: str, system: str) -> None:
    found = [
        (drive["role"], drive["path"], drive["forecast"]["state"]) for drive in answer["drives"]
    ]
    wanted = [("data", DATA.label, data), ("system", SYSTEM.label, system)]
    if found != wanted:
        raise SystemExit(f"system: drives {found}, expected {wanted}")
    if data == "estimated" and None in (
        answer["drives"][0]["forecast"]["warning_on"],
        answer["drives"][0]["forecast"]["full_on"],
    ):
        raise SystemExit(f"system: the data drive's forecast has no dates: {answer['drives'][0]}")


def expect_health(health: dict[str, Any]) -> None:
    services = {service["name"]: service["state"] for service in health["services"]}
    if set(services.values()) != {"running"}:
        raise SystemExit(f"system: services {services}")
    if (health["agent"]["state"], health["apply_system"]["state"]) != ("ok", "ok"):
        raise SystemExit(f"system: agent {health['agent']}, ApplySystem {health['apply_system']}")
    tasks = {task["task"]: task["state"] for task in health["tasks"]}
    if tasks != TASK_STATES:
        raise SystemExit(f"system: task states {tasks}, expected {TASK_STATES}")
    jobs = {job["job"]: job["state"] for job in health["jobs"]}
    wanted = {name: JOB_STATES.get(name, "ok") for name in jobs}
    if jobs != wanted:
        raise SystemExit(f"system: job states {jobs}, expected {wanted}")
    offsets = [sample["offset_ms"] for sample in health["time"]["samples"]]
    if len(offsets) != len(CLOCK) or 3.2 not in offsets or None not in offsets:
        raise SystemExit(f"system: clock samples {health['time']['samples']}")
