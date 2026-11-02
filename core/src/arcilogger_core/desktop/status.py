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
log flow       ``state/collector-status.json``: the newest message, the sources silent for
               longer than ``[seal] gap_minutes``, a spool write error
database       ``state/ingest-status.json``: the database connection and the largest lag
sealing        the newest :data:`SEAL_STATES` files ``state/seal/<day>.json``
alerts         ``state/alerts.jsonl``, only its last :data:`ALERT_TAIL` bytes: the evidence-path
               alerts of the last 24 hours (the web interface's Uyarılar page has all alerts)
disk           the free space of the data folder's drive
=============  =========================================================================

Nothing is written and nothing leaves the machine: the health request goes to the loopback
address and ignores any proxy, and there is no telemetry. A file that cannot be read gives the
level ``unknown`` and the text "bilinmiyor", never an exception. The data folder is readable
only by administrators, so a window started without administrator rights (UAC declined) still
shows the services, the API and the disk.

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
class Site:
    """What the window needs from the configuration."""

    #: The data folder (``data_dir``); None when neither the configuration nor the installer's
    #: record names it.
    data_root: Path | None
    health_url: str
    gap_minutes: int = 60
    late_after_hours: int = 48
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
        return Site(
            data_root=root, health_url=health_url("127.0.0.1", 8000), problem=_first_line(exc)
        )
    return Site(
        data_root=config.data_dir,
        health_url=health_url(config.api.host, config.api.port),
        gap_minutes=config.seal.gap_minutes,
        late_after_hours=config.seal.late_after_hours,
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


def read_json(path: Path) -> tuple[dict[str, Any], datetime] | None:
    """A status file and its modification time; None when it does not exist."""
    try:
        modified = datetime.fromtimestamp(path.stat().st_mtime, UTC)
        data = json.loads(path.read_bytes())
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


def flow_row(layout: Layout, site: Site, now: datetime) -> Row:
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
    sources = data.get("sources")
    if not isinstance(sources, dict) or not sources:
        return Row(
            key,
            "warn",
            title_tr,
            title_en,
            "log kaynağı tanımlı değil ([[sources]])",
            "no log source is configured ([[sources]])",
        )
    gap = timedelta(minutes=site.gap_minutes)
    last: dict[str, datetime | None] = {
        str(name): _time(entry.get("last_received")) if isinstance(entry, dict) else None
        for name, entry in sources.items()
    }
    silent = sorted(name for name, at in last.items() if at is None or now - at > gap)
    received = [at for at in last.values() if at is not None]
    if received:
        newest = max(received)
        tr, en = ago(now - newest)
        text_tr = f"son kayıt {clock(newest)} ({tr} önce); {len(sources)} kaynak"
        text_en = f"last message {clock(newest)} ({en} ago); {len(sources)} sources"
    else:
        text_tr = f"henüz kayıt gelmedi; {len(sources)} kaynak"
        text_en = f"no message yet; {len(sources)} sources"
    level: Level = "ok"
    if silent:
        level = "warn"
        names = _short(", ".join(silent), 80)
        text_tr += f", {len(silent)} sessiz: {names}"
        text_en += f", {len(silent)} silent: {names}"
    age = now - _updated(data, modified)
    if age > SERVICE_FRESH:
        tr, en = ago(age)
        text_tr += f" (bilgi {tr} eski)"
        text_en += f" (information {en} old)"
    return Row(key, level, title_tr, title_en, text_tr, text_en)


def load_row(layout: Layout) -> Row:
    key, title_tr, title_en = "database", "Veritabanına aktarım", "Database loading"
    try:
        found = read_json(layout.ingest_status)
    except UnreadableError as exc:
        return _unreadable_row(key, title_tr, title_en, exc)
    if found is None:
        return Row(key, "unknown", title_tr, title_en, "henüz bilgi yok", "no information yet")
    data, _ = found
    database = data.get("database")
    if isinstance(database, dict) and database.get("ok") is False:
        reason = _short(database.get("last_error") or "?", 80)
        return Row(
            key,
            "critical",
            title_tr,
            title_en,
            f"veritabanına yazılamıyor: {reason}",
            "the database cannot be written",
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
            key, "warn", title_tr, title_en, f"{tr} geride{speed_tr}", f"{en} behind{speed_en}"
        )
    return Row(
        key,
        "ok",
        title_tr,
        title_en,
        f"güncel (gecikme {tr}){speed_tr}",
        f"up to date (lag {en}){speed_en}",
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


def seal_row(layout: Layout, site: Site, now: datetime) -> Row:
    key, title_tr, title_en = "seal", "Mühürleme", "Sealing"
    try:
        states = _seal_states(layout)
    except UnreadableError as exc:
        return _unreadable_row(key, title_tr, title_en, exc)
    sealed = next(((day, data) for day, data in states if data.get("status") == "sealed"), None)
    failed = next(
        (
            (day, data)
            for day, data in states
            if data.get("status") == "failed" and (sealed is None or day > sealed[0])
        ),
        None,
    )
    yesterday = site_day(now) - timedelta(days=1)
    if sealed is None and failed is None:
        return Row(
            key,
            "ok",
            title_tr,
            title_en,
            "henüz mühürlenmiş gün yok (ilk mühür ilk tam günün gecesi)",
            "no day sealed yet (the first seal follows the first whole day)",
        )
    if sealed is not None and sealed[0] >= yesterday:
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
    first_open = sealed[0] + timedelta(days=1) if sealed else (failed[0] if failed else yesterday)
    overdue = now - day_window(first_open)[1]
    last_tr = sealed[0].isoformat() if sealed else "yok"
    last_en = sealed[0].isoformat() if sealed else "none"
    if overdue > timedelta(hours=site.late_after_hours):
        days = (yesterday - first_open).days + 1
        return Row(
            key,
            "critical",
            title_tr,
            title_en,
            f"{days} kapanmış gün mühürlenmedi (son mühür: {last_tr})",
            f"{days} closed day(s) not sealed (last seal: {last_en})",
        )
    if failed is not None:
        message = _short(failed[1].get("message") or failed[1].get("error") or "?", 100)
        return Row(
            key,
            "warn",
            title_tr,
            title_en,
            f"{failed[0].isoformat()} mühürlenemedi, yeniden denenecek: {message}",
            f"{failed[0].isoformat()} could not be sealed and will be retried",
        )
    if to_site(now).hour >= SEAL_EXPECTED_HOUR:
        return Row(
            key,
            "warn",
            title_tr,
            title_en,
            f"dün ({yesterday.isoformat()}) henüz mühürlenmedi (son mühür: {last_tr})",
            f"yesterday ({yesterday.isoformat()}) is not sealed yet (last seal: {last_en})",
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
                load_row(layout),
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
