"""``alerts.system`` — the R20 "ARCILOGGER sistem" checks (spec 11), every 5 minutes.

Each check reads what the evidence tasks and services already record (files under ``state/``,
``job_runs``, ``messages``, ``seal_verifications``), never asks a device or Kamu SM, and owns its
dedup keys: it opens an alert per problem found and resolves its active alerts whose problem is
gone (``details.auto_resolved``). Thresholds are database settings (``alerts.*``) except the
Kamu SM credit threshold, which is ``[tsa] credit_alert_threshold`` like the Credit task's.

===============================  ==============================================================
Key                              Problem (setting)
===============================  ==============================================================
``R20-disk:<drive>``             data drive or system drive more than
                                 ``alerts.disk_used_percent`` (80) % used; ``critical`` from
                                 95 %; resolved 2 points below the threshold (no flapping)
``R20-seal:<day>``               a closed day still not sealed at ``alerts.seal_overdue_hour``
                                 (09:00) of the next day — the sealing page's state, from
                                 ``state/seal`` and the vault (:class:`~arcilogger_core.seal.
                                 Sealer`); a failed attempt imported from ``alerts.jsonl``
                                 shares the key. Resolved once the day is sealed (or skipped,
                                 deleted)
``R20-seal-config:<cause>``      (raised by the Seal task) resolved once a day is sealed after it
``R20-verify``                   the newest verification of a sealed day failed (scheduled
                                 ``state/verify`` or "Doğrula" on the sealing page)
``R20-credit``                   Kamu SM credit (newest ``state/credit.jsonl`` entry) below
                                 ``[tsa] credit_alert_threshold``
``R20-replica:stale``            replica enabled and a sealed day without a replica
                                 (``state/replica/<day>.json``) for more than
                                 ``alerts.replica_stale_hours`` (48), counted from its seal or
                                 from when the worker took the ``[replica]`` in use (its start
                                 or a live reload) when that is later (a replica switched on
                                 later first copies the old days); the imported
                                 ``R20-replica`` (a copy failed) is resolved once a copy
                                 succeeded after it and every day sealed by then has its
                                 replica, unless it is ``critical`` (replica differs)
``R20-parse:<source>``           more than ``alerts.parse_failure_percent`` (1) % of a source's
                                 messages of the last hour unparsed, from
                                 ``alerts.parse_min_messages`` (1000) messages on
``R20-job:<job>``                the last ``alerts.job_failures`` (3) runs of a worker job failed
``R20-backup``                   ``state/backup/last.json``: the last backup failed, or the last
                                 one finished more than ``alerts.backup_stale_hours`` (36) ago;
                                 resolved by a backup that succeeded after the alert's last
                                 report
``R20-backup:missing``           no backup state at all ``alerts.backup_missing_hours`` (48)
                                 after the installation (the first ``job_runs`` row) — medium
``R20-backup:prune``             (raised by the Backup task) old dumps could not be deleted;
                                 resolved one hour after a later successful backup that raised
                                 it no more
``R20-backup:clock``             (raised by the Backup task) a new dump named earlier than one
                                 already there (clock set back); resolved by a later successful
                                 backup. ``[backup] enabled = false``: no backup alert, the
                                 active ones are resolved
``R20-clock``                    (imported from ``clock-check``: offset beyond the limit, time
                                 server unreachable) resolved by a later clock sample within the
                                 limit. A clock step or stray spool file seen by sealing
                                 (``R20-clock:<key>``) is left to a person
``R20-admin:heartbeat``          the agent ArciAdmin's heartbeat (``admin\\status\\agent.json``)
                                 older than 2 minutes or missing, from 5 minutes after the
                                 worker's start (ADR-0014; the agent starts after the other
                                 services) — high. Not judged while ``admin\\policy.json``
                                 switches the browser administration off (or is missing or
                                 invalid), nor, when the worker may not read that file, before
                                 the agent ever wrote a heartbeat. Resolved by a fresh
                                 heartbeat, or when the browser administration is switched off
===============================  ==============================================================

A check that fails (an unreadable folder, a database error) is reported in the job's details and
makes the run ``failed`` (so ``R20-job`` sees it); the other checks still run and commit.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.admin.contract import (
    AdminLayout,
    AdminPolicy,
    AgentStatus,
    DocumentError,
    heartbeat_state,
    parse_document,
)
from arcilogger_core.config import Config
from arcilogger_core.fmt import format_date
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ, day_window, isoformat_site, site_day
from arcilogger_core.tsa.base import StampResult, TsaError
from arcilogger_db.models import Alert, JobRun, Message, SealVerification
from arcilogger_db.settings import get_settings
from arcilogger_worker.alerts.store import (
    active_alerts,
    last_occurrence,
    open_alert,
    resolve,
)

log = logging.getLogger("arcilogger_worker.alerts")

#: The data drive at this percentage is ``critical`` (raw logs are lost when it is full).
DISK_CRITICAL_PERCENT: Final = 95
#: A disk alert is resolved this many points below the threshold.
DISK_HYSTERESIS: Final = 2
PARSE_WINDOW: Final = timedelta(hours=1)
JOB_WINDOW: Final = timedelta(days=7)
#: Most days listed in an ``R20-verify`` alert.
DAYS_LISTED: Final = 31


@dataclass(frozen=True)
class Finding:
    key: str
    rule: str
    severity: str
    title: str
    details: Mapping[str, Any]


def _clear_always(alert: Alert) -> str | None:
    return "condition_cleared"


@dataclass
class Outcome:
    """What a check found. ``owns(key)``: the active alerts it decides on; ``clear(alert)``
    for an owned active alert not among the findings: why it is resolved, or ``None`` to keep
    it (the check cannot tell yet)."""

    findings: list[Finding]
    owns: Callable[[str], bool]
    clear: Callable[[Alert], str | None] = _clear_always


@dataclass
class CheckContext:
    session: Session
    layout: Layout
    config: Config
    now: datetime
    settings: Mapping[str, Any]
    #: When the worker started; ``None``: unknown.
    started_at: datetime | None = None
    #: The installation's ``admin`` folder (next to the fixed ``--config`` path); ``None``:
    #: none known, the administration check is left out.
    admin: AdminLayout | None = None
    #: When the worker took the ``[replica]`` in use (its start, or a later change it reloaded);
    #: ``None``: ``started_at``.
    replica_since: datetime | None = None

    def setting(self, key: str, default: int) -> int:
        value = self.settings.get(key)
        return value if type(value) is int else default


@dataclass
class SystemReport:
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)

    def details(self) -> dict[str, Any]:
        return {"checks": self.checks, "errors": self.errors}


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _seal_states(layout: Layout) -> dict[date, dict[str, Any]]:
    """Every ``state/seal/<YYYY-MM-DD>.json`` that can be read."""
    states: dict[date, dict[str, Any]] = {}
    folder = layout.state / "seal"
    if not folder.is_dir():
        return states
    for path in folder.glob("*.json"):
        try:
            day = date.fromisoformat(path.stem)
            data = json.loads(path.read_bytes())
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            states[day] = data
    return states


def _last_line(path: Path) -> dict[str, Any] | None:
    """The last JSON object of a JSONL file (read from its end)."""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 65536))
            lines = handle.read().splitlines()
    except FileNotFoundError:
        return None
    for line in reversed(lines):
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            return entry
    return None


# -- disk -----------------------------------------------------------------------------------------


def _existing(path: Path) -> Path:
    while not path.exists() and path.parent != path:
        path = path.parent
    return path


def drives(data_dir: Path) -> list[tuple[str, Path]]:
    """``(label, path)`` of the data drive and the system drive (once if they are the same)."""
    if sys.platform == "win32":
        system = Path(os.environ.get("SYSTEMDRIVE", "C:") + "\\")
    else:
        system = Path("/")
    found: list[tuple[str, Path]] = []
    devices: set[int] = set()
    for path in (_existing(data_dir), system):
        try:
            device = path.stat().st_dev
        except OSError:
            device = -len(found) - 1
        if device in devices:
            continue
        devices.add(device)
        label = path.drive if sys.platform == "win32" and path.drive else str(path)
        found.append((label, path))
    return found


def check_disk(ctx: CheckContext) -> Outcome:
    threshold = ctx.setting("alerts.disk_used_percent", 80)
    findings: list[Finding] = []
    usage_by_label: dict[str, int] = {}
    checked = drives(ctx.config.data_dir)
    for label, path in checked:
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            log.warning("disk usage of %s: %s", path, exc)
            continue
        if usage.total <= 0:
            continue
        used = (usage.total - usage.free) * 100 // usage.total
        usage_by_label[label] = used
        if used <= threshold:
            continue
        severity = "critical" if used >= DISK_CRITICAL_PERCENT else "high"
        findings.append(
            Finding(
                key=f"R20-disk:{label}",
                rule="R20-disk",
                severity=severity,
                title=f"Disk dolmak üzere: {label} %{used} dolu (eşik %{threshold})",
                details={
                    "drive": label,
                    "used_percent": used,
                    "threshold_percent": threshold,
                    "free_bytes": usage.free,
                    "total_bytes": usage.total,
                    "title_en": f"Disk almost full: {label} {used}% used",
                },
            )
        )

    def clear(alert: Alert) -> str | None:
        label = (alert.dedup_key or "").removeprefix("R20-disk:")
        used = usage_by_label.get(label)
        if used is None:  # not measured now: unreadable (keep) or no longer checked
            return None if label in {name for name, _ in checked} else "not_checked"
        return "below_threshold" if used <= threshold - DISK_HYSTERESIS else None

    return Outcome(findings, lambda key: key.startswith("R20-disk:"), clear)


# -- sealing --------------------------------------------------------------------------------------


class _NoStamps:
    """The sealer only answers which days are pending here; it never stamps."""

    name = "none"
    legal_grade = False

    def stamp(self, file_path: Path) -> StampResult:  # pragma: no cover - never called
        raise TsaError("the alert check does not stamp")

    def credits(self) -> int | None:  # pragma: no cover - never called
        return None


def _seal_day(key: str) -> date | None:
    try:
        return date.fromisoformat(key.removeprefix("R20-seal:"))
    except ValueError:
        return None


def check_seal(ctx: CheckContext) -> Outcome:
    hour = ctx.setting("alerts.seal_overdue_hour", 9)
    sealer = Sealer(ctx.config, provider=_NoStamps(), roots=[], clock=lambda: ctx.now)
    try:
        pending = set(sealer.closed_unsealed_days())
    except OSError as exc:
        # Before the first seal the chain starts at the oldest spool day, and on the server the
        # worker may not list the spool (ACL plan, ADR-0007): the Seal task's own R20-seal and
        # R20-seal-config alerts cover that time. Nothing is decided now.
        log.info("pending days unknown (%s): %s", type(exc).__name__, exc)
        return Outcome([], lambda key: key.startswith("R20-seal"), lambda alert: None)
    states = _seal_states(ctx.layout)
    findings = []
    for day in sorted(pending):
        due = datetime.combine(day + timedelta(days=1), time(hour), tzinfo=SITE_TZ)
        if ctx.now < due:
            continue
        state = states.get(day, {})
        shown = format_date(day)
        late = ctx.now > day_window(day)[1] + timedelta(hours=ctx.config.seal.late_after_hours)
        findings.append(
            Finding(
                key=f"R20-seal:{day.isoformat()}",
                rule="R20-seal",
                severity="critical" if late else "high",  # as the Seal task's own alert
                title=(
                    f"Mühürleme gecikti: {shown} günü ertesi gün saat {hour:02d}:00'e kadar "
                    "mühürlenmedi"
                ),
                details={
                    "day": day.isoformat(),
                    "overdue": True,
                    "attempts": int(state.get("attempts") or 0),
                    "last_error_code": state.get("last_error_code"),
                    "title_en": f"Sealing overdue: {day.isoformat()} not sealed by {hour:02d}:00",
                },
            )
        )
    sealed_times = [moment for s in states.values() if (moment := _time(s.get("sealed_at")))]
    newest_seal = max(sealed_times) if sealed_times else None

    def owns(key: str) -> bool:
        return key.startswith("R20-seal-config") or (
            key.startswith("R20-seal:") and _seal_day(key) is not None
        )

    def clear(alert: Alert) -> str | None:
        key = alert.dedup_key or ""
        if key.startswith("R20-seal-config"):
            if newest_seal is not None and newest_seal > last_occurrence(alert):
                return "sealed_again"
            return None
        day = _seal_day(key)
        if day is None or day in pending:
            return None
        status = states.get(day, {}).get("status")
        return str(status) if status in ("sealed", "skipped", "deleted") else "not_pending"

    return Outcome(findings, owns, clear)


# -- verification ---------------------------------------------------------------------------------


def _newest_verifications(ctx: CheckContext) -> dict[date, tuple[datetime, bool]]:
    """Newest verification per day: scheduled (``state/verify/last-*.json``) or on demand."""
    newest: dict[date, tuple[datetime, bool]] = {}

    def offer(day: date, at: datetime, ok: bool) -> None:
        if day not in newest or newest[day][0] < at:
            newest[day] = (at, ok)

    folder = ctx.layout.state / "verify"
    for path in sorted(folder.glob("last-*.json")) if folder.is_dir() else []:
        try:
            data = _read_json(path)
        except (OSError, ValueError):
            continue
        at = _time(data.get("at")) if isinstance(data, dict) else None
        if at is None:
            continue
        for check in data.get("checks") or []:
            try:
                offer(date.fromisoformat(str(check["day"])), at, bool(check.get("ok")))
            except (KeyError, TypeError, ValueError):
                continue
    rows = ctx.session.execute(
        select(SealVerification.day, SealVerification.finished_at, SealVerification.outcome).where(
            SealVerification.outcome.in_(("ok", "failed"))
        )
    ).all()
    for day, finished, outcome in rows:
        offer(day, finished, outcome == "ok")
    return newest


def check_verify(ctx: CheckContext) -> Outcome:
    states = _seal_states(ctx.layout)
    failed = sorted(
        day
        for day, (_, ok) in _newest_verifications(ctx).items()
        if not ok and states.get(day, {}).get("status") != "deleted"
    )
    findings = []
    if failed:
        listed = [day.isoformat() for day in failed[:DAYS_LISTED]]
        findings.append(
            Finding(
                key="R20-verify",
                rule="R20-verify",
                severity="critical",
                title=f"Mühür doğrulaması başarısız: {', '.join(listed)}",
                details={
                    "failed_days": listed,
                    "count": len(failed),
                    "title_en": f"Seal verification failed: {', '.join(listed)}",
                },
            )
        )
    return Outcome(findings, lambda key: key == "R20-verify", lambda alert: "verified_again")


# -- Kamu SM credit -------------------------------------------------------------------------------


def check_credit(ctx: CheckContext) -> Outcome:
    threshold = ctx.config.tsa.credit_alert_threshold
    entry = _last_line(ctx.layout.credit_log)
    credits = entry.get("credits") if entry else None
    findings = []
    if type(credits) is int and credits < threshold:
        findings.append(
            Finding(
                key="R20-credit",
                rule="R20-credit",
                severity="high",
                title=f"Kamu SM kontörü azaldı: {credits} kaldı (eşik {threshold})",
                details={
                    "credits": credits,
                    "threshold": threshold,
                    "checked_at": entry.get("time") if entry else None,
                    "title_en": f"Kamu SM credit low: {credits} left (threshold {threshold})",
                },
            )
        )

    def clear(alert: Alert) -> str | None:
        # Only a reading at or above the threshold clears it (an unreadable one tells nothing).
        return "credit_ok" if type(credits) is int and credits >= threshold else None

    return Outcome(findings, lambda key: key == "R20-credit", clear)


# -- replica --------------------------------------------------------------------------------------


def _replica_successes(layout: Layout) -> dict[date, datetime]:
    """When each day's replica was last copied and verified (``state/replica/<day>.json``)."""
    successes: dict[date, datetime] = {}
    folder = layout.state / "replica"
    for path in folder.glob("*.json") if folder.is_dir() else []:
        try:
            day = date.fromisoformat(path.stem)
            data = _read_json(path)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("status") == "ok":
            moment = _time(data.get("verified_at"))
            if moment is not None:
                successes[day] = moment
    return successes


def check_replica(ctx: CheckContext) -> Outcome:
    hours = ctx.setting("alerts.replica_stale_hours", 48)
    threshold = timedelta(hours=hours)
    successes = _replica_successes(ctx.layout)
    last_success = max(successes.values()) if successes else None
    # Sealed days (not deleted, not skipped) without a replica yet, and when they were sealed.
    missing = {
        day: moment
        for day, state in _seal_states(ctx.layout).items()
        if state.get("status") == "sealed"
        and day not in successes
        and (moment := _time(state.get("sealed_at"))) is not None
    }
    findings = []
    enabled = ctx.config.replica.enabled
    if enabled:
        # A day waits from its seal, but at most from when the worker took the [replica] in
        # use (its start or a reload): a replica switched on later has old days to copy first.
        floor = ctx.replica_since or ctx.started_at
        overdue = sorted(
            day
            for day, sealed_at in missing.items()
            if ctx.now - (max(sealed_at, floor) if floor else sealed_at) > threshold
        )
        if overdue:
            listed = [day.isoformat() for day in overdue[:DAYS_LISTED]]
            findings.append(
                Finding(
                    key="R20-replica:stale",
                    rule="R20-replica",
                    severity="high",
                    title=(
                        f"Replika eksik: {len(overdue)} mühürlü gün {hours} saatten uzun süredir "
                        f"replikada yok (en eskisi {format_date(overdue[0])})"
                    ),
                    details={
                        "stale": True,
                        "threshold_hours": hours,
                        "missing_days": listed,
                        "count": len(overdue),
                        "last_success": isoformat_site(last_success, "seconds")
                        if last_success
                        else None,
                        "title_en": (
                            f"Replica missing: {len(overdue)} sealed day(s) without a replica "
                            f"copy for more than {hours} hours (oldest {listed[0]})"
                        ),
                    },
                )
            )

    def clear(alert: Alert) -> str | None:
        if alert.dedup_key == "R20-replica:stale":
            if not enabled:
                return "replica_disabled"
            # Counted from the seal alone: a worker restart alone never resolves it.
            waiting = any(ctx.now - sealed_at > threshold for sealed_at in missing.values())
            return None if waiting else "replica_ok"
        if alert.severity == "critical":
            return None  # a replica that differs is looked at by a person
        # A copy failure (imported): over once a copy succeeded after it and every day sealed
        # by then has its replica — a later day that copies fine says nothing about the
        # failing one, whose failure the Replica task reports only once.
        reported = last_occurrence(alert)
        if last_success is None or last_success <= reported:
            return None
        if any(sealed_at <= reported for sealed_at in missing.values()):
            return None
        return "replica_ok"

    return Outcome(findings, lambda key: key in ("R20-replica", "R20-replica:stale"), clear)


# -- parse failures -------------------------------------------------------------------------------


def check_parse(ctx: CheckContext) -> Outcome:
    percent = ctx.setting("alerts.parse_failure_percent", 1)
    minimum = ctx.setting("alerts.parse_min_messages", 1000)
    rows = ctx.session.execute(
        select(Message.source, func.count(), func.count(Message.parse_error))
        .where(Message.time >= ctx.now - PARSE_WINDOW, Message.time <= ctx.now)
        .group_by(Message.source)
    ).all()
    findings = []
    for source, total, failed in rows:
        if total < minimum or failed * 100 <= percent * total:
            continue
        findings.append(
            Finding(
                key=f"R20-parse:{source}",
                rule="R20-parse",
                severity="medium",
                title=(
                    f"Ayrıştırılamayan satır oranı yüksek: {source} son bir saatte {total} "
                    f"satırın {failed} tanesi (eşik %{percent})"
                ),
                details={
                    "source": source,
                    "total": int(total),
                    "failed": int(failed),
                    "threshold_percent": percent,
                    "title_en": f"Many unparsed lines: {source} {failed} of {total}",
                },
            )
        )
    return Outcome(findings, lambda key: key.startswith("R20-parse:"))


# -- failing worker jobs ---------------------------------------------------------------------------


def check_jobs(ctx: CheckContext) -> Outcome:
    count = max(1, ctx.setting("alerts.job_failures", 3))
    rank = (
        func.row_number()
        .over(partition_by=JobRun.job, order_by=(JobRun.started_at.desc(), JobRun.id.desc()))
        .label("rank")
    )
    ranked = (
        select(JobRun.job, JobRun.status, JobRun.started_at, JobRun.details, rank)
        .where(JobRun.status != "running", JobRun.started_at >= ctx.now - JOB_WINDOW)
        .subquery()
    )
    runs: dict[str, list[tuple[str, datetime, Mapping[str, Any]]]] = {}
    for job, status, started, details, _ in ctx.session.execute(
        select(ranked).where(ranked.c.rank <= count).order_by(ranked.c.job, ranked.c.rank)
    ):
        runs.setdefault(job, []).append((status, started, details or {}))
    findings = []
    for job, last in sorted(runs.items()):
        if any(details.get("manual") for _, _, details in last):
            continue  # a repair started by a person, who sees the result
        if len(last) < count or any(status != "failed" for status, _, _ in last):
            continue
        newest = last[0]
        findings.append(
            Finding(
                key=f"R20-job:{job}",
                rule="R20-job",
                severity="high",
                title=f"Arka plan işi art arda başarısız: {job} ({count} kez)",
                details={
                    "job": job,
                    "failures": count,
                    "last_started": isoformat_site(newest[1], "seconds"),
                    "last_error": str(newest[2].get("error") or "")[:100],
                    "title_en": f"Background job failing repeatedly: {job} ({count} times)",
                },
            )
        )

    def clear(alert: Alert) -> str | None:
        job = (alert.dedup_key or "").removeprefix("R20-job:")
        last = runs.get(job)
        if not last:
            return "no_recent_runs"
        return "job_ok" if last[0][0] != "failed" else None

    return Outcome(findings, lambda key: key.startswith("R20-job:"), clear)


# -- database backup (contract 4.1 of group 1: state/backup/last.json) -----------------------------


def backup_state_path(layout: Layout) -> Path:
    return layout.state / "backup" / "last.json"


#: Keys of the backup check: the backup state, no state at all, old dumps not deleted (the Backup
#: task's retention, ``details.key = "prune"``).
BACKUP_KEYS: Final = ("R20-backup", "R20-backup:missing", "R20-backup:prune", "R20-backup:clock")
#: A retention failure is over once a later backup ran this long ago without a new one (the
#: failure of that run's own retention follows the backup by seconds and is imported minutes
#: later).
PRUNE_SETTLE: Final = timedelta(hours=1)


def check_backup(ctx: CheckContext) -> Outcome:
    if not ctx.config.backup.enabled:  # no backup is run, and none is expected
        return Outcome([], lambda key: key in BACKUP_KEYS, lambda alert: "backup_disabled")
    stale_hours = ctx.setting("alerts.backup_stale_hours", 36)
    missing_hours = ctx.setting("alerts.backup_missing_hours", 48)
    findings: list[Finding] = []
    try:
        data = _read_json(backup_state_path(ctx.layout))
    except (OSError, ValueError):
        data = ""  # there, but not readable
    if data is None:  # no backup state at all
        installed = ctx.session.scalar(select(func.min(JobRun.started_at)))
        if installed is not None and ctx.now - installed > timedelta(hours=missing_hours):
            findings.append(
                Finding(
                    key="R20-backup:missing",
                    rule="R20-backup",
                    severity="medium",
                    title="Henüz veritabanı yedeği yok (state\\backup\\last.json yok)",
                    details={
                        "case": "missing",
                        "threshold_hours": missing_hours,
                        "title_en": "No database backup yet",
                    },
                )
            )
        # Nothing tells whether an earlier failure is over: R20-backup stays as it is.
        return Outcome(
            findings,
            lambda key: key == "R20-backup:missing",
            lambda alert: None,
        )
    case = ""
    finished: datetime | None = None
    if not isinstance(data, dict):
        case = "unreadable"
    elif data.get("ok") is not True:
        case = "failed"
    elif (finished := _time(data.get("finished"))) is None:
        case = "unreadable"
    elif ctx.now - finished > timedelta(hours=stale_hours):
        case = "stale"
    if case:
        error = str(data.get("error") or "?")[:200] if isinstance(data, dict) else ""
        titles = {
            "failed": (f"Veritabanı yedeği başarısız: {error}", "Database backup failed"),
            "stale": (
                f"Son başarılı veritabanı yedeği {stale_hours} saatten eski",
                f"Last database backup older than {stale_hours} hours",
            ),
            "unreadable": (
                "Yedek durum dosyası okunamıyor (state\\backup\\last.json)",
                "Backup state file unreadable",
            ),
        }
        findings.append(
            Finding(
                key="R20-backup",
                rule="R20-backup",
                severity="high",
                title=titles[case][0],
                details={
                    "case": case,
                    "threshold_hours": stale_hours,
                    "finished": data.get("finished") if isinstance(data, dict) else None,
                    "title_en": titles[case][1],
                },
            )
        )

    def clear(alert: Alert) -> str | None:
        if alert.dedup_key == "R20-backup:missing":
            return "backup_found"
        # Only a backup that succeeded after the alert's last report ends it: the Backup task
        # also raises R20-backup for a dump whose last.json could not be written (the file still
        # shows the backup before it).
        if case or finished is None or finished <= last_occurrence(alert):
            return None
        if alert.dedup_key == "R20-backup:prune":
            return "pruned" if ctx.now - finished >= PRUNE_SETTLE else None
        return "backup_ok"

    return Outcome(findings, lambda key: key in BACKUP_KEYS, clear)


# -- clock (raised by the core clock check, resolved here) ----------------------------------------


def check_clock(ctx: CheckContext) -> Outcome:
    limit_us = ctx.config.clock.alert_offset_ms * 1000
    today = site_day(ctx.now)
    sample = _last_line(ctx.layout.clock_samples(today)) or _last_line(
        ctx.layout.clock_samples(today - timedelta(days=1))
    )
    at = _time(sample.get("time")) if sample else None
    offset = sample.get("offset_us") if sample else None
    good = bool(sample and sample.get("ok") and type(offset) is int and abs(offset) <= limit_us)

    def clear(alert: Alert) -> str | None:
        return "clock_ok" if good and at is not None and at > last_occurrence(alert) else None

    return Outcome([], lambda key: key == "R20-clock", clear)


# -- the administration agent's heartbeat (ADR-0014) ----------------------------------------------

#: The heartbeat of the agent ArciAdmin is judged only this long after the worker's start: the
#: agent starts after the other services, and its file may still be the one of before a restart.
AGENT_GRACE: Final = timedelta(minutes=5)
ADMIN_HEARTBEAT_KEY: Final = "R20-admin:heartbeat"


def agent_heartbeat(admin: AdminLayout) -> datetime | None:
    """``heartbeat_at`` of ``admin\\status\\agent.json``; ``None`` for a missing, unreadable or
    invalid file (an agent that never wrote one counts as missing, contract 2.5.1)."""
    try:
        return parse_document(AgentStatus, admin.agent_status.read_bytes()).heartbeat_at
    except (OSError, DocumentError):
        return None


def admin_enabled(admin: AdminLayout) -> bool | None:
    """Whether ``admin\\policy.json`` switches the browser administration on: its ``enabled``,
    ``False`` for a missing or invalid file (as :func:`~arcilogger_core.admin.contract.load_policy`
    reads it, contract D0-13), ``None`` when the worker may not read it (contract 2.2 gave the
    worker no right on it before Day 0 note WP3-5)."""
    try:
        data = admin.policy.read_bytes()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    try:
        return parse_document(AdminPolicy, data).enabled
    except DocumentError:
        return False


def _agent_seen(admin: AdminLayout) -> bool:
    """The agent has written its heartbeat file at least once (it never deletes it)."""
    try:
        return admin.agent_status.is_file()
    except OSError:
        return True  # there, but it cannot be looked at: judged like a missing heartbeat


def _heartbeat_key(key: str) -> bool:
    return key == ADMIN_HEARTBEAT_KEY


def check_admin(ctx: CheckContext) -> Outcome:
    owns = _heartbeat_key
    if ctx.admin is None:
        return Outcome([], owns, lambda alert: None)
    enabled = admin_enabled(ctx.admin)
    if enabled is False:  # off (or no valid policy): no agent expected
        return Outcome([], owns, lambda alert: "admin_disabled")
    heartbeat = agent_heartbeat(ctx.admin)
    if heartbeat_state(heartbeat, ctx.now) != "missing":
        return Outcome([], owns, lambda alert: "agent_ok")
    if ctx.started_at is not None and ctx.now - ctx.started_at < AGENT_GRACE:
        return Outcome([], owns, lambda alert: None)
    if enabled is None and not _agent_seen(ctx.admin):  # policy unknown, agent never ran
        return Outcome([], owns, lambda alert: None)
    minutes = None if heartbeat is None else int((ctx.now - heartbeat) // timedelta(minutes=1))
    since_tr = "hiç yanıt vermedi" if minutes is None else f"{minutes} dakikadır yanıt vermiyor"
    since_en = "never answered" if minutes is None else f"not answering for {minutes} minutes"
    finding = Finding(
        key=ADMIN_HEARTBEAT_KEY,
        rule="R20-admin",
        severity="high",
        title=f"Yönetim hizmeti (ArciAdmin) {since_tr}",
        details={
            "case": "heartbeat",
            "minutes": minutes,
            "heartbeat_at": isoformat_site(heartbeat, "seconds") if heartbeat else None,
            "title_en": f"Administration service (ArciAdmin) {since_en}",
        },
    )
    return Outcome([finding], owns, lambda alert: None)


CHECKS: Final[tuple[tuple[str, Callable[[CheckContext], Outcome]], ...]] = (
    ("disk", check_disk),
    ("seal", check_seal),
    ("verify", check_verify),
    ("credit", check_credit),
    ("replica", check_replica),
    ("parse", check_parse),
    ("job", check_jobs),
    ("backup", check_backup),
    ("clock", check_clock),
    ("admin", check_admin),
)


def apply(session: Session, outcome: Outcome, *, now: datetime) -> dict[str, Any]:
    """Open the findings, resolve the owned active alerts that are cleared."""
    opened, resolved = [], []
    found = {finding.key for finding in outcome.findings}
    for finding in outcome.findings:
        result = open_alert(
            session,
            rule=finding.rule,
            severity=finding.severity,
            title=finding.title,
            details=finding.details,
            key=finding.key,
            time=now,
            now=now,
        )
        if result.created:
            opened.append(finding.key)
    for alert in active_alerts(session):
        key = alert.dedup_key or ""
        if key in found or not outcome.owns(key):
            continue
        reason = outcome.clear(alert)
        if reason is not None:
            resolve(alert, reason=reason, now=now)
            resolved.append(key)
    return {"found": sorted(found), "opened": opened, "resolved": resolved}


def run_system_checks(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    *,
    now: datetime,
    started_at: datetime | None = None,
    admin: AdminLayout | None = None,
    replica_since: datetime | None = None,
) -> SystemReport:
    """Every check in its own transaction; raises after the others ran if one failed."""
    report = SystemReport()
    for name, check in CHECKS:
        with sessions() as session:
            try:
                settings = get_settings(session, prefix="alerts.")
                ctx = CheckContext(
                    session, layout, config, now, settings, started_at, admin, replica_since
                )
                report.checks[name] = apply(session, check(ctx), now=now)
                session.commit()
            except Exception as exc:  # one broken check must not stop the others
                session.rollback()
                log.exception("alert check %s failed", name)
                report.errors[name] = f"{type(exc).__name__}: {str(exc)[:200]}"
    if report.errors:
        raise CheckError(report)
    return report


class CheckError(RuntimeError):
    def __init__(self, report: SystemReport) -> None:
        super().__init__(
            "alert checks failed: "
            + "; ".join(f"{name}: {error}" for name, error in sorted(report.errors.items()))
        )
        self.report = report
