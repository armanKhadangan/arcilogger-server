"""What the desktop status window shows: the state of ARCILOGGER on this server, read-only
(Wave 5 contract §5, decisions I1-I5; issue #113).

The window (:mod:`arcilogger_core.desktop.window`) is a thin view of one :class:`Snapshot`, which
:func:`collect` builds from what a person at the server may read without the database and
without signing in:

=============  =========================================================================
Row            Read from
=============  =========================================================================
services       the Service Control Manager (query only, :mod:`arcilogger_core.desktop.winapi`)
               for the six services; for ArciCollector, ArciIngest and ArciWorker also the
               age of their status file (the Sistem page's rule, ``SERVICE_FRESH``); for
               ArciApi ``GET /api/v1/health`` on the loopback address (no sign-in, no
               database)
log flow       ``state/collector-status.json``: the newest message, a spool write error, and
               every enabled, expected ``[[sources]]`` entry silent for longer than
               ``[seal] gap_minutes`` (also one that sent nothing since the collector started)
database       ``state/ingest-status.json``: the database connection and the largest lag
sealing        the newest :data:`SEAL_STATES` files ``state/seal/<day>.json`` (a failed attempt
               is ``pending`` with ``last_error``); without a sealed day, the oldest open day
               or the oldest spool day from ``[seal] first_day`` on
alerts         ``state/alerts.jsonl``, only its last :data:`ALERT_TAIL` bytes: the evidence-path
               alerts of the last 24 hours (the web interface's Uyarılar page has all alerts)
disk           the free space of the data folder's drive
=============  =========================================================================

Nothing is written and nothing leaves the machine: the health request goes to the loopback
address and ignores any proxy, and there is no telemetry. A file that cannot be read gives the
level ``unknown`` and the text "bilinmiyor", never an exception. The configuration and the data
folder are readable only by administrators, so a window started without administrator rights
(UAC declined) shows the services and asks the API on its default port; every other row says
why it is unknown. Files the services replace are read with delete sharing on Windows
(:func:`read_shared`), so a look never makes a service's replace fail.

Levels, worst first:

- ``critical``: evidence or lookups are stopped. A core service is stopped, raw logs are not
  written, the database cannot be written, sealing is later than ``[seal] late_after_hours``,
  a critical alert was raised, or less than 5 % of the disk is free.
- ``warn``: something needs a look.
- ``unknown``: could not be read.
- ``ok``.

The window's level is the worst row's; an ``unknown`` row makes it at least ``warn``.
"""

from __future__ import annotations

import contextlib
import http.client
import ipaddress
import json
import os
import shutil
import sys
import urllib.request
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final, Literal

from arcilogger_core.admin.contract import SERVICE_FRESH
from arcilogger_core.config import (
    check_fixed_location,
    default_config_path,
    load_config,
    recorded_data_root,
)
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import day_window, site_day, to_site

Level = Literal["ok", "unknown", "warn", "critical"]
#: What the Service Control Manager says about a service (``unknown``: it could not be asked,
#: e.g. on another platform; ``missing``: no such service is installed).
ServiceState = Literal["running", "stopped", "starting", "stopping", "paused", "missing", "unknown"]

RANK: Final[Mapping[Level, int]] = {"ok": 0, "unknown": 1, "warn": 2, "critical": 3}

#: The six services in the installer's order (``ArciInstall.psm1`` ``$ArciServiceNames``):
#: name, Turkish and English label, and whether a stop is critical. ArciAdmin only carries the
#: browser administration; without it logs are still received, loaded and sealed.
SERVICES: Final[tuple[tuple[str, str, str, bool], ...]] = (
    ("ArciCollector", "Toplayıcı (syslog)", "Collector (syslog)", True),
    ("ArciIngest", "Veritabanı aktarımı", "Database loader", True),
    ("ArciApi", "API", "API", True),
    ("ArciWorker", "Zamanlanmış işler", "Background jobs", True),
    ("ArciWeb", "Web sunucusu", "Web server", True),
    ("ArciAdmin", "Yönetim aracısı", "Administration agent", False),
)

#: Newest seal states read (one file per day; older days do not change the picture).
SEAL_STATES: Final = 14
#: Bytes read from the end of ``state/alerts.jsonl`` (the file only grows).
ALERT_TAIL: Final = 1024 * 1024
ALERT_WINDOW: Final = timedelta(hours=24)
#: A database loader further behind than this needs a look.
LAG_WARN_SECONDS: Final = 300
#: Free share of the data drive below which the disk row warns, and is critical.
DISK_WARN_PERCENT: Final = 15
DISK_CRITICAL_PERCENT: Final = 5
#: Hour of the site day after which an unsealed yesterday is reported (Seal runs at 00:10
#: and retries; a large day may take a while).
SEAL_EXPECTED_HOUR: Final = 3
HEALTH_TIMEOUT_SECONDS: Final = 2.0
#: Shown for every file the window may not read.
NO_ACCESS_TR: Final = "okunamadı: pencere yönetici yetkisiyle çalışmıyor"
NO_ACCESS_EN: Final = "cannot be read: the window does not run with administrator rights"


@dataclass(frozen=True)
class Row:
    """One line of the window: a dot in the level's colour, a title and a text, each in
    Turkish with English beside it. ``key`` is stable across refreshes."""

    key: str
    level: Level
    title_tr: str
    title_en: str
    text_tr: str
    text_en: str


@dataclass(frozen=True)
class Snapshot:
    at: datetime
    services: tuple[Row, ...]
    checks: tuple[Row, ...]

    @property
    def level(self) -> Level:
        return overall(row.level for row in (*self.services, *self.checks))


def overall(levels: Iterable[Level]) -> Level:
    """The worst level; ``unknown`` counts as ``warn`` (the window cannot vouch for it)."""
    worst: Level = "ok"
    for level in levels:
        if RANK[level] > RANK[worst]:
            worst = level
    return "warn" if worst == "unknown" else worst


@dataclass(frozen=True)
class Watched:
    """An enabled ``[[sources]]`` entry that is expected to send every day. A network source
    (``172.20.10.0/24``) is recorded per sender as ``<name>-<ip>``."""

    name: str
    network: bool = False

    def matches(self, recorded: str) -> bool:
        return recorded == self.name or (self.network and recorded.startswith(f"{self.name}-"))


@dataclass(frozen=True)
class Site:
    """What the window needs from the configuration."""

    #: The data folder (``data_dir``); None when neither the configuration nor the installer's
    #: record names it.
    data_root: Path | None
    health_url: str
    gap_minutes: int = 60
    late_after_hours: int = 48
    #: ``[seal] first_day``: spool days before it are never sealed.
    first_day: date | None = None
    #: The sources that must send; meaningful only when ``configured``.
    sources: tuple[Watched, ...] = ()
    #: The configuration was read, so ``sources`` is the site's list (else the status file's
    #: names are all the window knows).
    configured: bool = False
    #: Why the configuration could not be used (None: it was).
    problem: str | None = None


def health_url(host: str, port: int) -> str:
    """``GET /api/v1/health`` of the API on this machine: an API listening on every address is
    asked on the loopback address."""
    if host in ("", "0.0.0.0", "::"):  # noqa: S104 - compared, never bound
        host = "127.0.0.1"
    with contextlib.suppress(ValueError):
        if ipaddress.ip_address(host).version == 6:
            host = f"[{host}]"
    return f"http://{host}:{port}/api/v1/health"


def _first_line(exc: BaseException) -> str:
    text = str(exc).strip().splitlines()
    return (text[0] if text else type(exc).__name__)[:200]


def load_site(config_path: Path | None = None) -> Site:
    """The configuration as every ARCILOGGER runner reads it (fixed path, ``data_dir`` checked
    against the installer's record). Without a usable configuration the window still shows the
    services, the API on its default port and the recorded data folder."""
    path = config_path or default_config_path()
    try:
        config = load_config(path)
        check_fixed_location(config, path)
    except (OSError, ValueError) as exc:
        root = None
        with contextlib.suppress(OSError, ValueError):
            root = recorded_data_root(path)
        problem = NO_ACCESS_TR if isinstance(exc, PermissionError) else _first_line(exc)
        return Site(data_root=root, health_url=health_url("127.0.0.1", 8000), problem=problem)
    return Site(
        data_root=config.data_dir,
        health_url=health_url(config.api.host, config.api.port),
        gap_minutes=config.seal.gap_minutes,
        late_after_hours=config.seal.late_after_hours,
        first_day=config.seal.first_day,
        sources=tuple(
            Watched(source.name, source.is_network)
            for source in config.sources
            if source.enabled and source.expected
        ),
        configured=True,
    )


# -- probes (replaced in tests) -------------------------------------------------------------------


def http_health(url: str) -> str | None:
    """The API's version when it answers ``status: ok``, else None. No proxy: the address is
    this machine's own."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(url, timeout=HEALTH_TIMEOUT_SECONDS) as reply:
            data = json.loads(reply.read(64 * 1024))
    except (OSError, ValueError, http.client.HTTPException):
        return None
    if not isinstance(data, dict) or data.get("status") != "ok":
        return None
    return str(data.get("version") or "?")


def disk_space(path: Path) -> tuple[int, int] | None:
    """Free and total bytes of the drive holding ``path``."""
    try:
        usage = shutil.disk_usage(path)
    except OSError:
        return None
    return usage.free, usage.total


@dataclass(frozen=True)
class Probe:
    """Everything :func:`collect` asks outside the data folder."""

    services: Callable[[Sequence[str]], Mapping[str, ServiceState]]
    health: Callable[[str], str | None] = http_health
    disk: Callable[[Path], tuple[int, int] | None] = disk_space
    now: Callable[[], datetime] = lambda: datetime.now(UTC)


# -- formatting -----------------------------------------------------------------------------------


def ago(delta: timedelta) -> tuple[str, str]:
    """``(Turkish, English)`` for a past duration: seconds, minutes, hours, then days."""
    seconds = max(int(delta.total_seconds()), 0)
    if seconds < 90:
        return f"{seconds} sn", f"{seconds} s"
    if seconds < 90 * 60:
        return f"{seconds // 60} dk", f"{seconds // 60} min"
    if seconds < 48 * 3600:
        return f"{seconds // 3600} sa", f"{seconds // 3600} h"
    days = seconds // 86400
    return f"{days} gün", f"{days} day{'s' if days != 1 else ''}"


def size(value: int) -> str:
    """Bytes in the units Windows Explorer shows (1 GB = 1024³ bytes)."""
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024:
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} TB"


def clock(value: datetime) -> str:
    return to_site(value).strftime("%H:%M:%S")


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _short(text: Any, limit: int = 120) -> str:
    value = " ".join(str(text).split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


# -- reading the data folder ----------------------------------------------------------------------


class UnreadableError(Exception):
    """A status file that exists (or may exist) but cannot be used; ``denied`` when the reason
    is the missing administrator right."""

    def __init__(self, denied: bool) -> None:
        super().__init__("permission denied" if denied else "unreadable")
        self.denied = denied


if sys.platform == "win32":
    import ctypes
    import msvcrt
    from ctypes import wintypes

    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _create_file = _kernel32.CreateFileW
    _create_file.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _create_file.restype = wintypes.HANDLE
    _close_handle = _kernel32.CloseHandle
    _close_handle.argtypes = [wintypes.HANDLE]
    _close_handle.restype = wintypes.BOOL
    _GENERIC_READ: Final = 0x80000000
    _SHARE_READ_WRITE_DELETE: Final = 0x1 | 0x2 | 0x4
    _OPEN_EXISTING: Final = 3
    _INVALID_HANDLE_VALUE: Final = wintypes.HANDLE(-1).value

    def read_shared(path: Path) -> bytes:
        """The file's bytes, opened with read, write **and delete** sharing: the services
        replace their status files atomically, and Python's ``open()`` would make that replace
        fail while the window reads (the pattern of ``arcilogger_ingest.files.open_shared``)."""
        handle = _create_file(
            str(path), _GENERIC_READ, _SHARE_READ_WRITE_DELETE, None, _OPEN_EXISTING, 0x80, None
        )
        if handle is None or handle == _INVALID_HANDLE_VALUE:
            code = ctypes.get_last_error()
            # OSError maps the Windows code to FileNotFoundError, PermissionError, …
            raise OSError(None, ctypes.FormatError(code).strip(), str(path), code)
        try:
            descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
        except OSError:
            _close_handle(handle)
            raise
        with os.fdopen(descriptor, "rb") as handle_file:
            return handle_file.read()

else:

    def read_shared(path: Path) -> bytes:
        """The file's bytes (POSIX: an open file never blocks a rename over it)."""
        return path.read_bytes()


def read_json(path: Path) -> tuple[dict[str, Any], datetime] | None:
    """A status file and its modification time; None when it does not exist."""
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        data = json.loads(read_shared(path))
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        raise UnreadableError(denied=True) from exc
    except (OSError, ValueError) as exc:
        raise UnreadableError(denied=False) from exc
    if not isinstance(data, dict):
        raise UnreadableError(denied=False)
    return data, modified


def _unreadable_row(key: str, title_tr: str, title_en: str, exc: UnreadableError) -> Row:
    if exc.denied:
        return Row(key, "unknown", title_tr, title_en, NO_ACCESS_TR, NO_ACCESS_EN)
    return Row(
        key,
        "unknown",
        title_tr,
        title_en,
        "bilinmiyor: dosya okunamadı",
        "unknown: unreadable file",
    )


def _updated(data: dict[str, Any], modified: datetime) -> datetime:
    """When a service wrote its status file: ``updated_at``, else the file's time (the Sistem
    page's rule)."""
    return _time(data.get("updated_at")) or modified


# -- services -------------------------------------------------------------------------------------

_STATE_TEXTS: Final[Mapping[ServiceState, tuple[str, str]]] = {
    "running": ("çalışıyor", "running"),
    "stopped": ("durdu", "stopped"),
    "starting": ("başlatılıyor", "starting"),
    "stopping": ("durduruluyor", "stopping"),
    "paused": ("duraklatıldı", "paused"),
    "missing": ("kurulu değil", "not installed"),
    "unknown": ("bilinmiyor", "unknown"),
}


def _status_files(layout: Layout | None) -> Mapping[str, Path]:
    if layout is None:
        return {}
    return {
        "ArciCollector": layout.collector_status,
        "ArciIngest": layout.ingest_status,
        "ArciWorker": layout.worker_status,
    }


def _file_evidence(path: Path, now: datetime) -> tuple[Level, str, str] | None:
    """What a service's status file says: ``ok`` while it is younger than ``SERVICE_FRESH``,
    ``warn`` when it is older or missing; None when it cannot be read (the check rows say why)."""
    try:
        found = read_json(path)
    except UnreadableError:
        return None
    if found is None:
        return "warn", "durum dosyası yok", "no status file"
    data, modified = found
    age = now - _updated(data, modified)
    if age <= SERVICE_FRESH:
        return "ok", "durum dosyası güncel", "status file up to date"
    tr, en = ago(age)
    return "warn", f"durum dosyası {tr} eski", f"status file {en} old"


def service_row(
    name: str, state: ServiceState, evidence: tuple[Level, str, str] | None, essential: bool
) -> tuple[Level, str, str]:
    """Level and text of one service: the Service Control Manager's word, made worse (never
    better) by its status file or the API's answer. When the SCM could not be asked (another
    platform), the evidence alone decides."""
    text_tr, text_en = _STATE_TEXTS[state]
    if state == "unknown":
        if evidence is None:
            return "unknown", text_tr, text_en
        level, extra_tr, extra_en = evidence
        return level, extra_tr, extra_en
    if state in ("stopped", "missing"):
        return ("critical" if essential else "warn"), text_tr, text_en
    if state != "running":
        return "warn", text_tr, text_en
    if evidence is None or (evidence[0] == "ok" and name != "ArciApi"):
        return "ok", text_tr, text_en
    level, extra_tr, extra_en = evidence
    return level, f"{text_tr}; {extra_tr}", f"{text_en}; {extra_en}"


def service_rows(
    layout: Layout | None, states: Mapping[str, ServiceState], version: str | None, now: datetime
) -> tuple[Row, ...]:
    files = _status_files(layout)
    rows = []
    for name, title_tr, title_en, essential in SERVICES:
        evidence: tuple[Level, str, str] | None = None
        if name == "ArciApi":
            evidence = (
                ("warn", "yanıt vermiyor", "not answering")
                if version is None
                else ("ok", f"yanıt veriyor (sürüm {version})", f"answering (version {version})")
            )
        elif name in files:
            evidence = _file_evidence(files[name], now)
        level, text_tr, text_en = service_row(
            name, states.get(name, "unknown"), evidence, essential
        )
        rows.append(
            Row(f"service:{name}", level, f"{title_tr} · {name}", title_en, text_tr, text_en)
        )
    return tuple(rows)


# -- checks ---------------------------------------------------------------------------------------


def _received(entry: Any) -> datetime | None:
    return _time(entry.get("last_received")) if isinstance(entry, dict) else None


def flow_row(layout: Layout, site: Site, now: datetime) -> Row:
    """The newest message and the silent sources. ``sources`` of the collector's file lists only
    what arrived since it started, so with the configuration known every watched source is
    judged: one missing from the file is silent once the collector has run for the gap."""
    key, title_tr, title_en = "flow", "Log akışı", "Log flow"
    try:
        found = read_json(layout.collector_status)
    except UnreadableError as exc:
        return _unreadable_row(key, title_tr, title_en, exc)
    if found is None:
        return Row(key, "unknown", title_tr, title_en, "henüz bilgi yok", "no information yet")
    data, modified = found
    error = data.get("spool_write_error")
    if isinstance(error, dict):
        failing = ", ".join(str(s) for s in error.get("sources") or []) or "?"
        return Row(
            key,
            "critical",
            title_tr,
            title_en,
            f"ham kayıtlar diske yazılamıyor ({failing}): {_short(error.get('error'), 80)}",
            f"raw logs cannot be written to disk ({failing})",
        )
    recorded = data.get("sources")
    last = (
        {str(name): _received(entry) for name, entry in recorded.items()}
        if isinstance(recorded, dict)
        else {}
    )
    gap = timedelta(minutes=site.gap_minutes)
    if site.configured:
        if not site.sources:
            return Row(
                key,
                "warn",
                title_tr,
                title_en,
                "beklenen log kaynağı tanımlı değil ([[sources]])",
                "no expected log source is configured ([[sources]])",
            )
        running_since = _time(data.get("started_at")) or _updated(data, modified)
        silent = []
        for watched in site.sources:
            times = [at for name, at in last.items() if watched.matches(name) and at is not None]
            newest = max(times, default=None)
            quiet_since = newest if newest is not None else running_since
            if now - quiet_since > gap:
                silent.append(watched.name)
        count = len(site.sources)
    else:
        silent = [name for name, at in last.items() if at is None or now - at > gap]
        count = len(last)
    received = [at for at in last.values() if at is not None]
    if received:
        newest_any = max(received)
        tr, en = ago(now - newest_any)
        text_tr = f"son kayıt {clock(newest_any)} ({tr} önce); {count} kaynak"
        text_en = f"last message {clock(newest_any)} ({en} ago); {count} sources"
    else:
        text_tr = f"henüz kayıt gelmedi; {count} kaynak"
        text_en = f"no message yet; {count} sources"
    level: Level = "ok"
    if silent:
        level = "warn"
        names = _short(", ".join(sorted(silent)), 80)
        text_tr += f", {len(silent)} sessiz: {names}"
        text_en += f", {len(silent)} silent: {names}"
    tr_old, en_old = _stale(data, modified, now)
    return Row(key, level, title_tr, title_en, text_tr + tr_old, text_en + en_old)


def _stale(data: dict[str, Any], modified: datetime, now: datetime) -> tuple[str, str]:
    """The suffix of a row read from a status file older than ``SERVICE_FRESH``: its numbers
    are what the service last said, not what it does now."""
    age = now - _updated(data, modified)
    if age <= SERVICE_FRESH:
        return "", ""
    tr, en = ago(age)
    return f" (bilgi {tr} eski)", f" (information {en} old)"


def load_row(layout: Layout, now: datetime) -> Row:
    key, title_tr, title_en = "database", "Veritabanına aktarım", "Database loading"
    try:
        found = read_json(layout.ingest_status)
    except UnreadableError as exc:
        return _unreadable_row(key, title_tr, title_en, exc)
    if found is None:
        return Row(key, "unknown", title_tr, title_en, "henüz bilgi yok", "no information yet")
    data, modified = found
    old_tr, old_en = _stale(data, modified, now)
    database = data.get("database")
    if isinstance(database, dict) and database.get("ok") is False:
        reason = _short(database.get("last_error") or "?", 80)
        return Row(
            key,
            "critical",
            title_tr,
            title_en,
            f"veritabanına yazılamıyor: {reason}{old_tr}",
            f"the database cannot be written{old_en}",
        )
    lags = [
        float(entry["lag_seconds"])
        for entry in (data.get("sources") or {}).values()
        if isinstance(entry, dict) and isinstance(entry.get("lag_seconds"), int | float)
    ]
    lag = max(lags, default=0.0)
    totals = data.get("totals")
    rate = totals.get("lines_per_second") if isinstance(totals, dict) else None
    speed_tr = f", {rate} satır/sn" if isinstance(rate, int | float) else ""
    speed_en = f", {rate} lines/s" if isinstance(rate, int | float) else ""
    tr, en = ago(timedelta(seconds=lag))
    if lag > LAG_WARN_SECONDS:
        return Row(
            key,
            "warn",
            title_tr,
            title_en,
            f"{tr} geride{speed_tr}{old_tr}",
            f"{en} behind{speed_en}{old_en}",
        )
    return Row(
        key,
        "ok",
        title_tr,
        title_en,
        f"güncel (gecikme {tr}){speed_tr}{old_tr}",
        f"up to date (lag {en}){speed_en}{old_en}",
    )


def _seal_states(layout: Layout) -> list[tuple[date, dict[str, Any]]]:
    folder = layout.state / "seal"
    try:
        names = [entry.name for entry in os.scandir(folder) if entry.name.endswith(".json")]
    except FileNotFoundError:
        return []
    except PermissionError as exc:
        raise UnreadableError(denied=True) from exc
    except OSError as exc:
        raise UnreadableError(denied=False) from exc
    days = []
    for name in names:
        with contextlib.suppress(ValueError):
            days.append(date.fromisoformat(name.removesuffix(".json")))
    states = []
    for day in sorted(days, reverse=True)[:SEAL_STATES]:
        try:
            found = read_json(layout.seal_state(day))
        except UnreadableError as exc:
            if exc.denied:
                raise
            continue
        if found is not None:
            states.append((day, found[0]))
    return states


def _oldest_spool_day(layout: Layout, first_day: date | None) -> date | None:
    """The oldest ``spool/<source>/<day>.log`` from ``first_day`` on: a day the collector wrote
    that Seal has not recorded anything about (it never ran)."""
    days = []
    try:
        folders = [entry.path for entry in os.scandir(layout.spool) if entry.is_dir()]
        for folder in folders:
            for entry in os.scandir(folder):
                if entry.name.endswith(".log"):
                    with contextlib.suppress(ValueError):
                        days.append(date.fromisoformat(entry.name.removesuffix(".log")))
    except FileNotFoundError:
        return None
    except PermissionError as exc:
        raise UnreadableError(denied=True) from exc
    except OSError as exc:
        raise UnreadableError(denied=False) from exc
    days = [day for day in days if first_day is None or day >= first_day]
    return min(days, default=None)


def seal_row(layout: Layout, site: Site, now: datetime) -> Row:
    """The newest sealed day, or how late sealing is. The sealer records a failed attempt as
    ``pending`` with ``last_error`` (``Sealer._failed``); ``skipped`` and ``deleted`` days need
    no seal."""
    key, title_tr, title_en = "seal", "Mühürleme", "Sealing"
    try:
        states = _seal_states(layout)
        sealed = next(((day, data) for day, data in states if data.get("status") == "sealed"), None)
        yesterday = site_day(now) - timedelta(days=1)
        first_open: date | None
        if sealed is not None:
            first_open = sealed[0] + timedelta(days=1)
        else:
            pending = [day for day, data in states if data.get("status") == "pending"]
            first_open = min(pending, default=None)
            if first_open is None and not states:
                first_open = _oldest_spool_day(layout, site.first_day)
    except UnreadableError as exc:
        return _unreadable_row(key, title_tr, title_en, exc)
    failing = next(
        (
            (day, data)
            for day, data in states
            if data.get("status") == "pending"
            and data.get("last_error")
            and (sealed is None or day > sealed[0])
        ),
        None,
    )
    if first_open is None or first_open > yesterday:
        if sealed is None:
            return Row(
                key,
                "ok",
                title_tr,
                title_en,
                "henüz mühürlenecek kapanmış gün yok",
                "no closed day to seal yet",
            )
        day, data = sealed
        at = _time(data.get("sealed_at"))
        provider = str(data.get("provider") or "?")
        when_tr = f", {clock(at)}" if at else ""
        text_tr = f"son mühürlenen gün {day.isoformat()}{when_tr} ({provider})"
        text_en = f"last sealed day {day.isoformat()} ({provider})"
        if data.get("legal_grade") is False:
            return Row(
                key,
                "warn",
                title_tr,
                title_en,
                f"{text_tr}; damga yasal nitelikli değil (deneme ya da test sağlayıcısı)",
                f"{text_en}; the stamp is not legal grade (trial or test provider)",
            )
        return Row(key, "ok", title_tr, title_en, text_tr, text_en)
    # The first closed day without a seal ended at the midnight after it.
    overdue = now - day_window(first_open)[1]
    days = (yesterday - first_open).days + 1
    last_tr = sealed[0].isoformat() if sealed else "yok"
    last_en = sealed[0].isoformat() if sealed else "none"
    if overdue > timedelta(hours=site.late_after_hours):
        return Row(
            key,
            "critical",
            title_tr,
            title_en,
            f"{days} kapanmış gün mühürlenmedi (son mühür: {last_tr})",
            f"{days} closed day(s) not sealed (last seal: {last_en})",
        )
    if failing is not None:
        message = _short(failing[1].get("last_error"), 100)
        return Row(
            key,
            "warn",
            title_tr,
            title_en,
            f"{failing[0].isoformat()} mühürlenemedi, yeniden denenecek: {message}",
            f"{failing[0].isoformat()} could not be sealed and will be retried",
        )
    if days > 1 or to_site(now).hour >= SEAL_EXPECTED_HOUR:
        what_tr = "dün" if days == 1 else f"{days} kapanmış gün"
        what_en = "yesterday" if days == 1 else f"{days} closed days"
        return Row(
            key,
            "warn",
            title_tr,
            title_en,
            f"{what_tr} henüz mühürlenmedi (son mühür: {last_tr})",
            f"{what_en} not sealed yet (last seal: {last_en})",
        )
    return Row(
        key,
        "ok",
        title_tr,
        title_en,
        f"gece mühürlemesi bekleniyor (son mühür: {last_tr})",
        f"waiting for the nightly seal (last seal: {last_en})",
    )


def alert_lines(path: Path, tail: int = ALERT_TAIL) -> list[dict[str, Any]]:
    """The alerts of the last ``tail`` bytes of ``state/alerts.jsonl``; a line cut by the start
    of the tail, a torn line and a line that is not an alert are skipped."""
    try:
        with path.open("rb") as handle:
            end = handle.seek(0, os.SEEK_END)
            start = max(end - tail, 0)
            handle.seek(start)
            data = handle.read(end - start)
    except FileNotFoundError:
        return []
    except PermissionError as exc:
        raise UnreadableError(denied=True) from exc
    except OSError as exc:
        raise UnreadableError(denied=False) from exc
    lines = data.split(b"\n")
    if start > 0:
        lines = lines[1:]  # begins inside a line
    alerts = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            alerts.append(entry)
    return alerts


_SEVERITIES: Final = (
    ("critical", "kritik", "critical"),
    ("high", "yüksek", "high"),
    ("medium", "orta", "medium"),
    ("low", "düşük", "low"),
    ("info", "bilgi", "info"),
)


def alert_row(layout: Layout, now: datetime) -> Row:
    key, title_tr, title_en = (
        "alerts",
        "Uyarılar (kanıt yolu, 24 saat)",
        "Alerts (evidence path, 24 h)",
    )
    try:
        entries = alert_lines(layout.alerts)
    except UnreadableError as exc:
        return _unreadable_row(key, title_tr, title_en, exc)
    recent = []
    for entry in entries:
        at = _time(entry.get("time"))
        if at is not None and now - at <= ALERT_WINDOW:
            recent.append((at, entry))
    if not recent:
        return Row(key, "ok", title_tr, title_en, "son 24 saatte uyarı yok", "no alert in 24 hours")
    counts = {name: 0 for name, _, _ in _SEVERITIES}
    for _, entry in recent:
        severity = entry.get("severity")
        if severity in counts:
            counts[str(severity)] += 1
    parts_tr = [f"{counts[name]} {tr}" for name, tr, _ in _SEVERITIES if counts[name]]
    parts_en = [f"{counts[name]} {en}" for name, _, en in _SEVERITIES if counts[name]]
    rank = {name: index for index, (name, _, _) in enumerate(_SEVERITIES)}
    top = min(
        recent, key=lambda item: (rank.get(str(item[1].get("severity")), 99), -item[0].timestamp())
    )
    message = _short(top[1].get("message") or top[1].get("rule") or "?", 100)
    level: Level = "critical" if counts["critical"] else "warn" if counts["high"] else "ok"
    return Row(
        key,
        level,
        title_tr,
        title_en,
        f"{', '.join(parts_tr) or len(recent)}; en önemlisi ({clock(top[0])}): {message}",
        f"{', '.join(parts_en) or len(recent)}; the most important at {clock(top[0])}",
    )


def disk_row(root: Path, probe: Probe) -> Row:
    key, title_tr, title_en = "disk", "Veri diski", "Data drive"
    space = probe.disk(root)
    if space is None or space[1] <= 0:
        return Row(key, "unknown", title_tr, title_en, "okunamadı", "cannot be read")
    free, total = space
    percent = 100 * free / total
    level: Level = (
        "critical"
        if percent < DISK_CRITICAL_PERCENT
        else "warn"
        if percent < DISK_WARN_PERCENT
        else "ok"
    )
    drive = root.anchor or str(root)
    return Row(
        key,
        level,
        title_tr,
        title_en,
        f"{drive} {size(free)} boş / {size(total)} (%{percent:.0f})",
        f"{drive} {size(free)} free of {size(total)} ({percent:.0f} %)",
    )


def collect(site: Site, probe: Probe) -> Snapshot:
    """One look at the server. Never raises for anything the server's state can cause."""
    now = probe.now()
    names = [name for name, _, _, _ in SERVICES]
    try:
        states = dict(probe.services(names))
    except OSError:
        states = {}
    version = probe.health(site.health_url)
    layout = Layout(site.data_root) if site.data_root is not None else None
    checks: list[Row] = []
    if site.problem is not None:
        checks.append(
            Row(
                "config",
                "warn",
                "Yapılandırma",
                "Configuration",
                f"okunamadı: {site.problem}",
                "cannot be used (see the Turkish text)",
            )
        )
    if layout is None:
        checks.append(
            Row(
                "data",
                "unknown",
                "Veri klasörü",
                "Data folder",
                "bilinmiyor: yapılandırma ve kurulum kaydı yok",
                "unknown: no configuration and no installation record",
            )
        )
    else:
        checks.extend(
            (
                flow_row(layout, site, now),
                load_row(layout, now),
                seal_row(layout, site, now),
                alert_row(layout, now),
                disk_row(layout.root, probe),
            )
        )
    return Snapshot(
        at=now,
        services=service_rows(layout, states, version, now),
        checks=tuple(checks),
    )


#: The window's title starts with this (a second start finds the first window by it).
TITLE: Final = "ARCILOGGER Durum"
#: Dot colours (the Lisans Yöneticisi's green, orange, red and grey).
COLOURS: Final[Mapping[Level, str]] = {
    "ok": "#1a7f37",
    "warn": "#b35900",
    "unknown": "#6e6e6e",
    "critical": "#c62828",
}

LEVEL_TEXTS: Final[Mapping[Level, tuple[str, str]]] = {
    "ok": ("Çalışıyor", "Running"),
    "warn": ("Dikkat", "Attention"),
    "unknown": ("Bilinmiyor", "Unknown"),
    "critical": ("Sorun var", "Problem"),
}


def render_text(snapshot: Snapshot) -> str:
    """The snapshot as plain text (``--print``: for a console and for support)."""
    tr, en = LEVEL_TEXTS[snapshot.level]
    lines = [f"ARCILOGGER {clock(snapshot.at)}: {tr} / {en}"]
    for row in (*snapshot.services, *snapshot.checks):
        lines.append(
            f"[{row.level}] {row.title_tr} / {row.title_en}: {row.text_tr} / {row.text_en}"
        )
    return "\n".join(lines)


def window_title(level: Level) -> str:
    tr, en = LEVEL_TEXTS[level]
    return f"{TITLE} — {tr} / {en}"


def flash_due(before: Level | None, after: Level) -> bool:
    """The taskbar button flashes when the state becomes critical, once per rise."""
    return after == "critical" and before != "critical"
