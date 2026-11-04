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
``R20-lease:<cause>``            ADR-0016 lease evidence (:func:`check_lease_evidence`): a DHCP or
                                 ARP-LOG feed that stopped (``none:<table>``) or never ran
                                 (``absent``), FortiGate DHCP rows refused or not converted,
                                 renewals not seen, a static device in a DHCP pool, two DHCP
                                 sources in one network, unusable lease times, rows outside every
                                 network, the unverified format, a source's clock, a MAC on many
                                 addresses, a shared srcmac (docs/UYARILAR.md); counted on usable
                                 rows (``trust.kept``)
``R20-nat:<cause>``              ADR-0016 internet exits (:func:`check_nat_exits`): the legacy
                                 conversion failed, ``nat.egresses`` cannot be parsed, legacy rows
                                 changed during a rollback, an unclaimed NAT address in use
``R20-admin:heartbeat``          the agent ArciAdmin's heartbeat (``admin\\status\\agent.json``)
                                 older than 2 minutes or missing, from 5 minutes after the
                                 worker's start (ADR-0014; the agent starts after the other
                                 services) — high. Not judged while ``admin\\policy.json``
                                 switches the browser administration off (or is missing or
                                 invalid), nor, when the worker may not read that file, before
                                 the agent ever wrote a heartbeat. Resolved by a fresh
                                 heartbeat, or when the browser administration is switched off
``R20-filter:none``              no web or DNS filter record (``fg_web``, ``fg_dns``) of the
                                 enabled FortiGate sources for ``alerts.filter_quiet_hours`` (24)
                                 hours while every site day of that window is expected
                                 (``alerts.filter_days``, every day; not the days of
                                 ``usage.holidays`` while ``alerts.filter_skip_holidays``;
                                 :mod:`~arcilogger_worker.compliance_filter`, card 7 of Uyum
                                 Durumu) — high; resolved once a record arrives or the window is
                                 no longer expected
``R20-renewal:none``             ``state/renewal.json`` has a token due for re-stamping or one
                                 whose certificate expired without it — high; resolved by a
                                 readable report without such a token (Wave 5 contract 2.3.1,
                                 :data:`LEGAL_CHECKS`)
``R20-cert:none``                a time-stamp certificate on a revocation list at or before a
                                 stamp — critical; a token due for re-stamping whose certificate
                                 expires within 30 days (:data:`CERT_EXPIRY_WARNING`), or one
                                 whose certificate expired uncovered — high
                                 (:data:`LEGAL_CHECKS`)
``R20-crl:none``                 no stored revocation list fetched in the last
                                 ``tsa.crl_max_age_days`` (7) days and not past its own
                                 ``nextUpdate`` (C22) for an issuer of the newest sealed day's
                                 chain, judged from ``state/crl`` only — medium
                                 (:data:`LEGAL_CHECKS`)
===============================  ==============================================================

A check that fails (an unreadable folder, a database error) is reported in the job's details and
makes the run ``failed`` (so ``R20-job`` sees it); the other checks still run and commit.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sys
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from ipaddress import IPv4Network, IPv6Network, ip_address, ip_network
from pathlib import Path
from typing import Any, Final, Literal, TypeGuard, get_args

from cryptography import x509
from sqlalchemy import (
    ColumnElement,
    Text,
    and_,
    cast,
    exists,
    func,
    not_,
    or_,
    select,
    true,
    tuple_,
)
from sqlalchemy.dialects.postgresql import CIDR, array
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core import crl
from arcilogger_core.admin.contract import (
    AdminLayout,
    AdminPolicy,
    AgentStatus,
    DocumentError,
    heartbeat_state,
    parse_document,
)
from arcilogger_core.chain import sealed_days
from arcilogger_core.config import Config
from arcilogger_core.crl import Crl, CrlError, CrlOptions, StoredLists, crl_options, load_stored
from arcilogger_core.fmt import format_date
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import SITE_TZ, day_window, isoformat_site, site_day
from arcilogger_core.tsa.base import StampResult, TsaError
from arcilogger_core.tsa.verify import (
    check_revocation,
    fingerprint,
    inspect_token,
    load_certificates,
    load_roots_dir,
    token_chain,
)
from arcilogger_db import clocksamples
from arcilogger_db.egress import (
    LEGACY_KEYS,
    MIGRATION_KEY,
    NOT_PUBLIC,
    Egress,
    EgressValueError,
    parse_egresses,
)
from arcilogger_db.models import (
    LEASE_SECONDS_RANGE,
    Alert,
    FgDhcp,
    FgEvent,
    FgTraffic,
    JobRun,
    Lease,
    Message,
    MtArp,
    MtDhcp,
    ParseFailure,
    SealVerification,
    SearchCount,
    Setting,
    Source,
    Vlan,
)
from arcilogger_db.settings import MIGRATION_AUTHOR, get_setting, get_settings
from arcilogger_worker.alerts.store import (
    active_alerts,
    last_occurrence,
    last_seen,
    open_alert,
    resolve,
)
from arcilogger_worker.alerts.texts import (
    EVIDENCE_TABLES,
    LEASE_TEXTS,
    NAT_TEXTS,
    REFUSAL_REASONS,
    REFUSED_HIGH_TITLE,
)
from arcilogger_worker.compliance_filter import filter_evidence
from arcilogger_worker.lookup import trust
from arcilogger_worker.lookup.refs import (
    ARP_TABLE,
    DHCP_TABLE,
    FG_DHCP_TABLE,
    last_dhcp_evidence,
)
from arcilogger_worker.lookup.types import EvidenceRef, LookupSettings

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
    """The JSON of ``path``, ``None`` when it is missing. Raises ``OSError``, or ``ValueError``
    also for a document nested too deeply to parse (a damaged file never fails a check)."""
    try:
        return json.loads(path.read_bytes())
    except FileNotFoundError:
        return None
    except RecursionError as exc:
        raise ValueError(f"{path.name} is nested too deeply") from exc


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
        except (OSError, ValueError, RecursionError):
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


class NoStamps:
    """A provider for a :class:`~arcilogger_core.seal.Sealer` that only answers which days are
    pending (the alert checks, the API); it never stamps."""

    name = "none"
    legal_grade = False

    def stamp(self, file_path: Path) -> StampResult:  # pragma: no cover - never called
        raise TsaError("this reader does not stamp")

    def credits(self) -> int | None:  # pragma: no cover - never called
        return None


def _seal_day(key: str) -> date | None:
    try:
        return date.fromisoformat(key.removeprefix("R20-seal:"))
    except ValueError:
        return None


def check_seal(ctx: CheckContext) -> Outcome:
    hour = ctx.setting("alerts.seal_overdue_hour", 9)
    sealer = Sealer(ctx.config, provider=NoStamps(), roots=[], clock=lambda: ctx.now)
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


@dataclass(frozen=True)
class ScheduledCheck:
    """A day's result in a scheduled verification (``state/verify/last-*.json``)."""

    at: datetime
    ok: bool
    errors: tuple[str, ...]


def scheduled_verifications(layout: Layout) -> dict[date, ScheduledCheck]:
    """Newest scheduled verification per day (``state/verify/last-*.json``, files only)."""
    return read_scheduled_verifications(layout)[0]


def read_scheduled_verifications(layout: Layout) -> tuple[dict[date, ScheduledCheck], bool]:
    """:func:`scheduled_verifications`, and whether every ``last-*.json`` could be read: a file
    that cannot be read, or holds no verification, is left out and makes it ``False``."""
    newest: dict[date, ScheduledCheck] = {}
    readable = True
    folder = layout.state / "verify"
    for path in sorted(folder.glob("last-*.json")) if folder.is_dir() else []:
        try:
            data = _read_json(path)
        except (OSError, ValueError):
            readable = False
            continue
        at = _time(data.get("at")) if isinstance(data, dict) else None
        checks = data.get("checks") if isinstance(data, dict) else None
        if at is None or not isinstance(checks, list):
            readable = False
            continue
        for check in checks:
            try:
                day = date.fromisoformat(str(check["day"]))
            except (KeyError, TypeError, ValueError):
                continue
            if day not in newest or newest[day].at < at:
                errors = check.get("errors")
                newest[day] = ScheduledCheck(
                    at=at,
                    ok=bool(check.get("ok")),
                    errors=tuple(str(e) for e in errors) if isinstance(errors, list) else (),
                )
    return newest, readable


def _newest_verifications(ctx: CheckContext) -> dict[date, tuple[datetime, bool]]:
    """Newest verification per day: scheduled (``state/verify/last-*.json``) or on demand."""
    newest: dict[date, tuple[datetime, bool]] = {}

    def offer(day: date, at: datetime, ok: bool) -> None:
        if day not in newest or newest[day][0] < at:
            newest[day] = (at, ok)

    for day, check in scheduled_verifications(ctx.layout).items():
        offer(day, check.at, check.ok)
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


# -- lease evidence (ADR-0016: R20-lease) ---------------------------------------------------------

LEASE_RULE: Final = "R20-lease"
NAT_RULE: Final = "R20-nat"
#: Most problems are judged on this window, and resolved this long after they were last found.
LEASE_WINDOW: Final = timedelta(hours=24)
#: Evidence that arrived in this period before a table's window was a working feed.
EVIDENCE_HISTORY: Final = timedelta(days=30)
TRAFFIC_WINDOW: Final = timedelta(hours=1)
#: How far back the first day to rebuild from is looked for (``first_day``).
REBUILD_HISTORY: Final = timedelta(days=100)
RENEWALS_RECENT: Final = timedelta(days=7)
RENEWALS_BEFORE: Final = timedelta(days=30)
#: Slack around a lease's half and end for the sessions that show its address in use.
SESSION_SLACK: Final = timedelta(minutes=15)
#: A lease ended by its MAC's next ACK this soon after its own (D33) is a quick move.
DECLINE_SECONDS: Final = 300
#: A MAC that got more distinct addresses than this in one origin, interface and network.
DHCP_MAC_IPS: Final = 3
#: ``source-clock`` (D28, #73): the newest rows per source by receive time, of which this many
#: must have been received in the last hour, and the median offset beyond which the clock is off.
#: A source with fewer rows in its recent minutes than :data:`CLOCK_ROWS` is judged on those
#: minutes once they hold :data:`CLOCK_MIN_ROWS` (a quiet source's jump opens the cause within
#: about two checks, not after half an hour of jumped rows).
CLOCK_ROWS: Final = 200
CLOCK_MIN_ROWS: Final = 10
CLOCK_LIMIT_SECONDS: Final = 120
CLOCK_RECENT: Final = timedelta(minutes=10)
#: Tables lookups read: a source whose files hold none of them has its clock left alone.
LOOKUP_TABLES: Final = ("fg_traffic", "fg_dhcp", "mt_dhcp", "mt_arp")
#: ``R20-nat:unmapped-transip``: sessions an unclaimed NAT address carried in the last hour.
UNMAPPED_SESSIONS: Final = 50
#: Most values of one kind (addresses, names, log values) listed in an alert's details.
LISTED: Final = 5
#: Log-supplied texts in details are cut to this length (the setting values' own limit).
TEXT_CUT: Final = 32
#: The private networks (RFC 1918) whose traffic shows internal clients at work.
PRIVATE_NETWORKS: Final = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
_MAC_LIKE: Final = re.compile(
    r"[0-9A-Fa-f]{2}(?:[:-][0-9A-Fa-f]{2}){5}|[0-9A-Fa-f]{4}(?:\.[0-9A-Fa-f]{4}){2}"
)
_IPV4_LIKE: Final = re.compile(r"\d{1,3}(?:\.\d{1,3}){3}")
_LOGID: Final = re.compile(r"^\d{10}$")
#: The ``details.reasons`` names of the refusal codes of ``trust.refusal`` (rules 1, 4-7).
REFUSAL_NAMES: Final[Mapping[str, str]] = {
    trust.UNTRUSTED_SOURCE: "untrusted",
    trust.UNMAPPED: "unmapped",
    trust.UNEXPECTED_MSG: "unexpected_msg",
    trust.RECEIVE_TIME: "receive_time",
    trust.BAD_MAC: "bad_mac",
}


def masked(value: str | None) -> str:
    """A log-supplied text as an alert's details may show it: MAC- and IPv4-looking parts
    replaced by "…", cut to :data:`TEXT_CUT` characters."""
    text = _IPV4_LIKE.sub("…", _MAC_LIKE.sub("…", value or ""))
    return text[:TEXT_CUT]


def _private(column: Any) -> ColumnElement[bool]:
    return or_(*(column.op("<<")(cast(network, CIDR)) for network in PRIVATE_NETWORKS))


@dataclass(frozen=True)
class Network:
    """A configured network (``vlans`` row): its key in a dedup key and its name in a text."""

    subnet: IPv4Network | IPv6Network
    vlan: int | None
    name: str

    @property
    def key(self) -> str:
        """``42``, else the subnet with ``_`` for ``/`` (``172.20.42.0_24``)."""
        return str(self.vlan) if self.vlan is not None else str(self.subnet).replace("/", "_")

    @property
    def label(self) -> str:
        """``VLAN 42``, else the network's name and subnet (site configuration, never client
        data); the same in both languages."""
        return f"VLAN {self.vlan}" if self.vlan is not None else f"{self.name} ({self.subnet})"


class Networks:
    """The configured networks, most specific first (``find_vlan``'s order), read once."""

    def __init__(self, session: Session) -> None:
        rows = session.execute(select(cast(Vlan.subnet, Text), Vlan.vlan_id, Vlan.name_tr)).all()
        self.all = sorted(
            (Network(ip_network(subnet), vlan, name) for subnet, vlan, name in rows),
            key=lambda n: -n.subnet.prefixlen,
        )

    def of(self, ip: str) -> Network | None:
        try:
            address = ip_address(ip)
        except ValueError:
            return None
        return next((n for n in self.all if address in n.subnet), None)


@dataclass
class _Check:
    """One run of the lease evidence or the exit check: what it found, and per cause how an
    active alert of it that was not found again is resolved (default: 24 hours clean)."""

    ctx: CheckContext
    rule: str
    findings: list[Finding] = field(default_factory=list)
    clears: dict[str, Callable[[Alert], str | None]] = field(default_factory=dict)

    @property
    def now(self) -> datetime:
        return self.ctx.now

    @property
    def session(self) -> Session:
        return self.ctx.session

    def add(
        self,
        cause: str,
        severity: str,
        params: Mapping[str, Any],
        details: Mapping[str, Any],
        *,
        key: str | None = None,
        params_en: Mapping[str, Any] | None = None,
        title: tuple[str, str] | None = None,
    ) -> None:
        """A finding of ``cause``: its title and what to do from the catalogue (``title``: another
        title), formatted with ``params`` (``params_en`` for the English texts, when a value is
        worded); ``details`` holds what the Security page and the describer may show."""
        catalogue = LEASE_TEXTS if self.rule == LEASE_RULE else NAT_TEXTS
        title_tr, title_en, action_tr, action_en = catalogue[cause]
        if title is not None:
            title_tr, title_en = title
        english = params if params_en is None else params_en
        self.findings.append(
            Finding(
                key=key or f"{self.rule}:{cause}",
                rule=self.rule,
                severity=severity,
                title=title_tr.format(**params),
                details={
                    "cause": cause,
                    **details,
                    "title_en": title_en.format(**english),
                    "action_tr": action_tr.format(**params),
                    "action_en": action_en.format(**english),
                },
            )
        )

    def clean_for_a_day(self, alert: Alert) -> str | None:
        """The default resolution: 24 hours without the problem."""
        return "clean_24h" if self.now - last_seen(alert) >= LEASE_WINDOW else None

    def outcome(self) -> Outcome:
        prefix = f"{self.rule}:"

        def clear(alert: Alert) -> str | None:
            cause = (alert.dedup_key or "").removeprefix(prefix).split(":", 1)[0]
            return self.clears.get(cause, self.clean_for_a_day)(alert)

        return Outcome(self.findings, lambda key: key.startswith(prefix), clear)


@dataclass
class _LeaseCheck(_Check):
    """The lease evidence check: the settings, the trust rules and the networks of the run."""

    settings: LookupSettings = field(default_factory=LookupSettings)
    untrusted: frozenset[str] = frozenset()
    networks: Networks | None = None

    @property
    def network(self) -> Networks:
        if self.networks is None:  # pragma: no cover - check_lease_evidence always sets it
            raise RuntimeError("the networks are not loaded")
        return self.networks

    def kept(self) -> ColumnElement[bool]:
        return trust.kept(self.settings, self.untrusted)

    def any_row(self, model: Any, predicate: ColumnElement[bool], lo: datetime) -> bool:
        query = select(model.id).where(model.time >= lo, model.time <= self.now, predicate)
        return self.session.scalar(query.limit(1)) is not None


def check_lease_evidence(ctx: CheckContext) -> Outcome:
    """The ``R20-lease:*`` causes (ADR-0016 D9, D30; docs/UYARILAR.md). Never parses
    ``nat.egresses`` (:func:`check_nat_exits` has its own transaction)."""
    session = ctx.session
    check = _LeaseCheck(
        ctx,
        LEASE_RULE,
        settings=LookupSettings.from_settings(get_settings(session)),
        untrusted=trust.untrusted_sources(session),
        networks=Networks(session),
    )
    _evidence_stopped(check)
    _refused(check)
    _unconverted(check)
    _renewals(check)
    _declines(check)
    _overlaps(check)
    _lease_values(check)
    _outside_networks(check)
    _unverified(check)
    _source_clocks(check)
    _shared_macs(check)
    _srcmac_shared(check)
    return check.outcome()


def _evidence_stopped(check: _LeaseCheck) -> None:
    """``none:<table>`` (one per evidence table) and ``absent`` (D30)."""
    now, session = check.now, check.session
    traffic = check.any_row(
        FgTraffic,
        and_(_private(FgTraffic.srcip), FgTraffic.source.not_in(sorted(check.untrusted)))
        if check.untrusted
        else _private(FgTraffic.srcip),
        now - TRAFFIC_WINDOW,
    )
    longest = session.scalar(
        select(func.max(Lease.lease_seconds)).where(
            Lease.source_table == FG_DHCP_TABLE, Lease.end.is_(None)
        )
    )
    usable: dict[str, tuple[Any, ColumnElement[bool]]] = {
        FG_DHCP_TABLE: (FgDhcp, check.kept()),
        ARP_TABLE: (MtArp, true()),
        DHCP_TABLE: (MtDhcp, MtDhcp.action.in_(("assigned", "deassigned"))),
    }
    windows = dict.fromkeys(usable, LEASE_WINDOW)
    if longest:  # a FortiGate client renews at half its lease
        windows[FG_DHCP_TABLE] = LEASE_WINDOW + timedelta(seconds=longest // 2)
    recent = {
        table: check.any_row(model, predicate, now - windows[table])
        for table, (model, predicate) in usable.items()
    }

    def before(table: str) -> bool:
        model, predicate = usable[table]
        lo = now - windows[table] - EVIDENCE_HISTORY
        query = select(model.id).where(
            model.time >= lo, model.time < now - windows[table], predicate
        )
        return session.scalar(query.limit(1)) is not None

    earlier = {table: not recent[table] and before(table) for table in usable}
    ever = {table: recent[table] or earlier[table] for table in usable}
    if traffic and not any(ever.values()):
        check.add("absent", "info", {}, {})
    for table, (severity, quiet) in {
        FG_DHCP_TABLE: ("high", False),
        ARP_TABLE: ("high", False),
        # RouterOS logs no renewals: only while ARP-LOG is quiet too (O20).
        DHCP_TABLE: ("medium", recent[ARP_TABLE]),
    }.items():
        if traffic and earlier[table] and not quiet:
            hours = int(windows[table] / timedelta(hours=1))
            check.add(
                "none",
                severity,
                {"evidence": EVIDENCE_TABLES[table], "hours": hours},
                {"table": table, "hours": hours},
                key=f"{LEASE_RULE}:none:{table}",
            )

    def none_cleared(alert: Alert) -> str | None:
        table = (alert.details or {}).get("table")
        return "evidence_arrived" if recent.get(str(table)) else None

    check.clears["none"] = none_cleared
    check.clears["absent"] = lambda alert: "evidence_arrived" if any(ever.values()) else None


def _refused(check: _LeaseCheck) -> None:
    """``fortigate-refused`` (D30): the rows of the last 24 hours ``trust.refusal`` refuses,
    re-read with today's settings and sources."""
    if not check.settings.fortigate_logids:  # FortiGate DHCP evidence switched off
        check.clears["fortigate-refused"] = lambda alert: "fortigate_dhcp_off"
        return
    now, session = check.now, check.session
    code = trust.refusal(check.settings, check.untrusted)
    window = (FgDhcp.time >= now - LEASE_WINDOW, FgDhcp.time <= now)
    rows = select(code.label("code")).where(*window).subquery()
    counts = {
        found: int(n)
        for found, n in session.execute(select(rows.c.code, func.count()).group_by(rows.c.code))
    }
    excluded = sum(n for found, n in counts.items() if found in trust.CONFIGURATION)
    refused = {REFUSAL_NAMES[found]: n for found, n in counts.items() if found in REFUSAL_NAMES}
    considered = sum(counts.values()) - excluded
    total = sum(refused.values())
    kept = considered - total
    check.clears["fortigate-refused"] = lambda alert: "no_refused_rows"
    if not total:
        return

    def values(column: Any, wanted: str) -> list[str]:
        query = select(column).distinct().where(*window, code == wanted).order_by(column)
        return [str(value) for value in session.scalars(query.limit(50)) if value is not None]

    logids = [v for v in values(FgDhcp.logid, trust.UNMAPPED) if _LOGID.match(v)][:LISTED]
    messages = sorted({masked(v) for v in values(FgDhcp.dhcp_msg, trust.UNEXPECTED_MSG)})
    senders = values(FgDhcp.source, trust.UNTRUSTED_SOURCE)[:LISTED]
    first = session.scalar(
        select(func.min(FgDhcp.time)).where(
            FgDhcp.time >= now - REBUILD_HISTORY,
            FgDhcp.time <= now,
            code.is_not(None),
            code.not_in(sorted(trust.CONFIGURATION)),
        )
    )
    first_day = site_day(first if first is not None else now)
    shown = {"unmapped": logids, "unexpected_msg": messages[:LISTED], "untrusted": senders}
    reasons = []
    for language in (0, 1):
        parts = []
        for name, n in sorted(refused.items()):
            seen = shown.get(name) or []
            tail = f" ({', '.join(seen)})" if seen else ""
            parts.append(f"{REFUSAL_REASONS[name][language]} {n}{tail}")
        reasons.append("; ".join(parts))
    high = considered >= 10 and kept * 10 < considered
    params = {
        "rows": considered,
        "refused": total,
        "kept": kept,
        "excluded": excluded,
        "first_day": format_date(first_day),
    }
    check.add(
        "fortigate-refused",
        "high" if high else "medium",
        {**params, "reasons": reasons[0]},
        {
            "count": total,
            "rows": considered,
            "kept": kept,
            "excluded": excluded,
            "reasons": refused,
            "logids": logids,
            "dhcp_msgs": messages[:LISTED],
            "sources": senders,
            "first_day": first_day.isoformat(),
        },
        params_en={**params, "reasons": reasons[1]},
        title=REFUSED_HIGH_TITLE if high else None,
    )


def _unconverted(check: _LeaseCheck) -> None:
    """``fortigate-unconverted``: a mapped logid that kept the ``fg_event`` shape, or
    ``fortigate.dhcp`` parse failures (V1)."""
    now, session = check.now, check.session
    rows = 0
    if check.settings.fortigate_logids:
        rows = int(
            session.scalar(
                select(func.count())
                .select_from(FgEvent)
                .where(
                    FgEvent.subtype == "system",
                    FgEvent.time >= now - LEASE_WINDOW,
                    FgEvent.time <= now,
                    FgEvent.logid.in_(sorted(check.settings.fortigate_logids)),
                    not_(FgEvent.extra.has_all(array(["ip", "mac", "dhcp_msg"]))),
                )
            )
            or 0
        )
    failures = int(
        session.scalar(
            select(func.coalesce(func.sum(ParseFailure.count), 0)).where(
                ParseFailure.pattern == "fortigate.dhcp",
                ParseFailure.day >= site_day(now) - timedelta(days=1),
            )
        )
        or 0
    )
    if rows or failures:
        check.add(
            "fortigate-unconverted",
            "high",
            {"rows": rows, "failures": failures},
            {"count": rows + failures, "rows": rows, "failures": failures},
        )


def _renewals(check: _LeaseCheck) -> None:
    """``fortigate-renewals-missing`` / ``fortigate-renewals-not-logged`` (D26): FortiGate
    leases with a logged lease time that ended ``timeout`` in the last 24 hours while their
    address was in use in the second half of the lease and right after its end."""
    now, session, settings = check.now, check.session, check.settings
    leases = session.scalars(
        select(Lease).where(
            Lease.source_table == FG_DHCP_TABLE,
            Lease.end_reason == "timeout",
            Lease.end >= now - LEASE_WINDOW,
            Lease.end <= now,
            Lease.lease_seconds.is_not(None),
        )
    ).all()
    longest = timedelta(seconds=settings.max_session_seconds)
    begin = FgTraffic.time - func.make_interval(
        0, 0, 0, 0, 0, 0, func.coalesce(FgTraffic.duration, 0)
    )

    def started(ip: str, lo: datetime, hi: datetime) -> bool:
        if hi < lo:
            return False
        query = select(FgTraffic.id).where(
            FgTraffic.srcip == ip,
            FgTraffic.time >= lo,
            FgTraffic.time <= hi + longest,
            begin >= lo,
            begin <= hi,
        )
        if check.untrusted:
            query = query.where(FgTraffic.source.not_in(sorted(check.untrusted)))
        return session.scalar(query.limit(1)) is not None

    counted = 0
    for lease in leases:
        refs = [EvidenceRef.from_dict(item) for item in lease.evidence or ()]
        sources = {r.raw.source for r in refs if r.table == FG_DHCP_TABLE and r.raw is not None}
        if lease.end is None or lease.lease_seconds is None or sources & check.untrusted:
            continue
        last = last_dhcp_evidence(refs, lease.end, lease.end_reason) or lease.start
        half = last + timedelta(seconds=lease.lease_seconds // 2) + SESSION_SLACK
        if started(lease.ip, half, lease.end) and started(
            lease.ip, lease.end, lease.end + SESSION_SLACK
        ):
            counted += 1
    total = int(
        session.scalar(
            select(func.count())
            .select_from(Lease)
            .where(
                Lease.source_table == FG_DHCP_TABLE,
                Lease.start <= now,
                Lease.end.is_(None) | (Lease.end >= now - LEASE_WINDOW),
            )
        )
        or 0
    )

    def renewals(lo: datetime, hi: datetime) -> bool:
        query = select(JobRun.id).where(
            JobRun.job.startswith("leases."),
            JobRun.started_at >= lo,
            JobRun.started_at < hi,
            JobRun.details["stats"]["fg_renewal_ack"].as_integer() > 0,
        )
        return session.scalar(query.limit(1)) is not None

    recent = renewals(now - RENEWALS_RECENT, now + timedelta(seconds=1))
    earlier = renewals(now - RENEWALS_RECENT - RENEWALS_BEFORE, now - RENEWALS_RECENT)
    check.clears["fortigate-renewals-not-logged"] = lambda alert: (
        "renewal_seen" if recent else check.clean_for_a_day(alert)
    )
    if counted < 3 or counted * 20 < total or recent:
        return
    verified = settings.fortigate_format_verified
    if not verified or earlier:
        check.add("fortigate-renewals-missing", "high", {"count": counted}, {"count": counted})
    else:
        check.add("fortigate-renewals-not-logged", "info", {"count": counted}, {"count": counted})


def _declines(check: _LeaseCheck) -> None:
    """``fortigate-decline:<net>`` (D33): an address left within minutes of its ACK, for the
    same MAC's lease on another address of the same network, at least twice in 24 hours."""
    now, session = check.now, check.session
    quick = session.execute(
        select(Lease.ip, Lease.mac, Lease.end).where(
            Lease.source_table == FG_DHCP_TABLE,
            Lease.end_reason == "superseded",
            Lease.end >= now - LEASE_WINDOW,
            Lease.end <= now,
            Lease.end - Lease.start <= timedelta(seconds=DECLINE_SECONDS),
        )
    ).all()
    if not quick:
        return
    pairs = sorted({(mac, end) for _ip, mac, end in quick})
    moved: dict[tuple[str, datetime], set[str]] = defaultdict(set)
    for first in range(0, len(pairs), 500):
        query = select(Lease.mac, Lease.start, Lease.ip).where(
            Lease.source_table == FG_DHCP_TABLE,
            tuple_(Lease.mac, Lease.start).in_(pairs[first : first + 500]),
        )
        for mac, start, ip in session.execute(query):
            moved[(mac, start)].add(ip)
    per_address: Counter[str] = Counter()
    for ip, mac, end in quick:
        network = check.network.of(ip)
        if network is None or end is None:
            continue
        if any(other != ip and check.network.of(other) == network for other in moved[(mac, end)]):
            per_address[ip] += 1
    by_network: dict[Network, list[str]] = defaultdict(list)
    for ip, n in per_address.items():
        network = check.network.of(ip)
        if n >= 2 and network is not None:
            by_network[network].append(ip)
    for network, addresses in sorted(by_network.items(), key=lambda item: item[0].key):
        count = sum(per_address[ip] for ip in addresses)
        check.add(
            "fortigate-decline",
            "high",
            {"network": network.label, "count": count},
            {
                "count": count,
                **({"vlan": network.vlan} if network.vlan is not None else {}),
                "network": network.label,
                "addresses": sorted(addresses, key=ip_address)[:LISTED],
            },
            key=f"{LEASE_RULE}:fortigate-decline:{network.key}",
        )


def _origin_name(origin: tuple[str, str | None]) -> str:
    table, vd = origin
    if table == DHCP_TABLE:
        return "MikroTik DHCP"
    return "FortiGate DHCP" if vd is None else f"FortiGate DHCP (VDOM {masked(vd)})"


def _overlaps(check: _LeaseCheck) -> None:
    """``dhcp-sources-overlap:<net>`` (D27): usable DHCP rows of two origins in one network."""
    subnets = {str(network.subnet): network for network in check.network.all}
    origins = trust.usable_dhcp_origins(
        check.session,
        check.settings,
        check.untrusted,
        sorted(subnets),
        check.now - LEASE_WINDOW,
        check.now,
    )
    for subnet, found in sorted(origins.items()):
        if len(found) < 2:
            continue
        network = subnets[subnet]
        names = sorted(_origin_name(origin) for origin in found)
        check.add(
            "dhcp-sources-overlap",
            "high",
            {"network": network.label, "sources": ", ".join(names)},
            {
                **({"vlan": network.vlan} if network.vlan is not None else {}),
                "network": network.label,
                "origins": names,
            },
            key=f"{LEASE_RULE}:dhcp-sources-overlap:{network.key}",
        )


def _lease_values(check: _LeaseCheck) -> None:
    """``fortigate-lease-unlimited`` (``lease=0``) and ``fortigate-lease`` (out of range)."""
    now, session = check.now, check.session
    low, high = LEASE_SECONDS_RANGE

    def rows(predicate: ColumnElement[bool]) -> int:
        query = (
            select(func.count())
            .select_from(FgDhcp)
            .where(FgDhcp.time >= now - LEASE_WINDOW, FgDhcp.time <= now, check.kept(), predicate)
        )
        return int(session.scalar(query) or 0)

    unlimited = rows(FgDhcp.lease == 0)
    if unlimited:
        check.add("fortigate-lease-unlimited", "high", {"rows": unlimited}, {"count": unlimited})
    odd = rows((FgDhcp.lease != 0) & ((FgDhcp.lease < low) | (FgDhcp.lease > high)))
    if odd:
        check.add("fortigate-lease", "low", {"rows": odd}, {"count": odd})


def _outside_networks(check: _LeaseCheck) -> None:
    """``fortigate-dhcp-subnet`` (D28, D33): usable rows whose address lies in no configured
    network, with the day to rebuild from once it is entered (round 7)."""
    now, session = check.now, check.session
    outside = and_(
        check.kept(), ~exists(select(Vlan.subnet).where(Vlan.subnet.op(">>=")(FgDhcp.ip)))
    )
    window = (FgDhcp.time >= now - LEASE_WINDOW, FgDhcp.time <= now, outside)
    rows = int(session.scalar(select(func.count()).select_from(FgDhcp).where(*window)) or 0)
    if not rows:
        return
    names = session.scalars(
        select(FgDhcp.interface).distinct().where(*window).order_by(FgDhcp.interface).limit(50)
    )
    interfaces = sorted({masked(name) for name in names if name})[:LISTED]
    first = session.scalar(
        select(func.min(FgDhcp.time)).where(
            FgDhcp.time >= now - REBUILD_HISTORY, FgDhcp.time <= now, outside
        )
    )
    first_day = site_day(first if first is not None else now)
    check.add(
        "fortigate-dhcp-subnet",
        "low",
        {
            "rows": rows,
            "interfaces": ", ".join(interfaces) or "-",
            "first_day": format_date(first_day),
        },
        {"count": rows, "interfaces": interfaces, "first_day": first_day.isoformat()},
    )


def _unverified(check: _LeaseCheck) -> None:
    """``fortigate-unverified`` (D29): FortiGate DHCP rows arrive while the site check of
    DEVICE-SETUP 2.4 is not marked done; resolved only by the setting."""
    verified = check.settings.fortigate_format_verified
    check.clears["fortigate-unverified"] = lambda alert: "format_verified" if verified else None
    if verified:
        return
    if check.any_row(FgDhcp, true(), check.now - RENEWALS_RECENT):
        check.add("fortigate-unverified", "info", {}, {})


def _source_clocks(check: _LeaseCheck) -> None:
    """``source-clock`` (D28): a configured source whose files of today or yesterday hold rows
    lookups read, and whose latest records' device times differ from their receive times by a
    median beyond :data:`CLOCK_LIMIT_SECONDS`.

    The latest records are the newest received in the last hour, from ``source_clock_samples``
    (whole received minutes, newest first, until :data:`CLOCK_ROWS` rows, or until
    :data:`CLOCK_MIN_ROWS` rows once the minutes reach :data:`CLOCK_RECENT` back; #73): ranked
    by device time, the records a source sent on time before its clock jumped back outranked
    every record after it, and the alert opened only about as long after the jump as the jump
    itself."""
    now, session = check.now, check.session
    today = site_day(now)
    checked = session.scalars(
        select(SearchCount.source)
        .distinct()
        .where(
            SearchCount.raw_day.in_([today - timedelta(days=1), today]),
            SearchCount.tbl.in_(LOOKUP_TABLES),
            SearchCount.source.in_(
                select(Source.name).where(Source.enabled.is_(True), Source.unknown.is_(False))
            ),
        )
        .order_by(SearchCount.source)
    ).all()
    offsets: dict[str, int] = {}
    for source in checked:
        latest = clocksamples.newest(
            session,
            source,
            now - TRAFFIC_WINDOW,
            now,
            CLOCK_ROWS,
            least=CLOCK_MIN_ROWS,
            recent=now - CLOCK_RECENT,
        )
        offset, counted = clocksamples.median(latest)
        if offset is None or counted < CLOCK_MIN_ROWS:
            continue
        if abs(offset) > CLOCK_LIMIT_SECONDS:
            offsets[source] = int(offset)
    check.clears["source-clock"] = lambda alert: "clock_ok"
    if not offsets:
        return
    listed = sorted(offsets, key=lambda name: (-abs(offsets[name]), name))[:LISTED]
    worst = offsets[listed[0]]
    check.add(
        "source-clock",
        "high",
        {"seconds": worst, "sources": ", ".join(f"{n} ({offsets[n]:+d} sn)" for n in listed)},
        {
            "seconds": worst,
            "sources": listed,
            "offsets": {name: offsets[name] for name in listed},
        },
        params_en={
            "seconds": worst,
            "sources": ", ".join(f"{n} ({offsets[n]:+d} s)" for n in listed),
        },
    )


def _shared_macs(check: _LeaseCheck) -> None:
    """``fortigate-dhcp-mac`` (D28): a MAC given more than :data:`DHCP_MAC_IPS` distinct
    addresses within one origin, interface and network in 24 hours (counted on the rows: D33
    closes the previous lease at every ACK)."""
    assigned = sorted(
        logid for logid, meaning in check.settings.fortigate_logids.items() if meaning == "assigned"
    )
    if not assigned:
        return
    rows = check.session.execute(
        select(FgDhcp.mac, FgDhcp.interface, FgDhcp.extra["vd"].astext, FgDhcp.ip)
        .distinct()
        .where(
            FgDhcp.time >= check.now - LEASE_WINDOW,
            FgDhcp.time <= check.now,
            check.kept(),
            FgDhcp.logid.in_(assigned),
        )
    ).all()
    addresses: dict[tuple[str, str | None, str | None, Network], set[str]] = defaultdict(set)
    for mac, interface, vd, ip in rows:
        network = check.network.of(ip)
        if network is not None:
            addresses[(mac, interface, vd, network)].add(ip)
    shared = len({key[0] for key, ips in addresses.items() if len(ips) > DHCP_MAC_IPS})
    if shared:
        check.add("fortigate-dhcp-mac", "medium", {"shared": shared}, {"count": shared})


def _srcmac_shared(check: _LeaseCheck) -> None:
    """``srcmac-shared:<interface>`` (D10): a listed interface where a srcmac came from more
    IPv4 addresses than ``lookup.srcmac_max_ips`` in the last hour."""
    interfaces = check.settings.srcmac_interfaces
    if not interfaces:
        return
    limit = check.settings.srcmac_max_ips
    counts = trust.srcmac_ip_counts(
        check.session, check.now - TRAFFIC_WINDOW, check.now, interfaces, check.untrusted
    )
    for interface in sorted(set(interfaces)):
        over = [n for (name, _mac), n in counts.items() if name == interface and n > limit]
        if not over:
            continue
        check.add(
            "srcmac-shared",
            "medium",
            {"interface": interface, "macs": len(over), "count": max(over), "limit": limit},
            {"count": len(over), "interface": interface, "most": max(over), "limit": limit},
            key=f"{LEASE_RULE}:srcmac-shared:{interface}",
        )


# -- internet exits (ADR-0016: R20-nat) -----------------------------------------------------------


def check_nat_exits(ctx: CheckContext) -> Outcome:
    """The ``R20-nat:*`` causes (D18, D20). A stored ``nat.egresses`` that cannot be parsed is
    the finding ``invalid``, never an error of the check (nor of the lease check)."""
    session, now = ctx.session, ctx.now
    check = _Check(ctx, NAT_RULE)
    row = session.get(Setting, "nat.egresses")
    exits: tuple[Egress, ...] | None
    try:
        exits = parse_egresses(row.value if row is not None else {})
    except ValueError as exc:
        exits = None
        code = exc.code if isinstance(exc, EgressValueError) else "setting_type"
        check.add("invalid", "high", {"code": code}, {"code": code})
    check.clears["invalid"] = lambda alert: "parsed" if exits is not None else None

    status = session.get(Setting, MIGRATION_KEY)
    failed = (
        status is not None
        and isinstance(status.value, Mapping)
        and status.value.get("conversion_failed") is True
        and row is not None
        and row.updated_by == MIGRATION_AUTHOR
    )
    if failed:
        check.add("conversion-failed", "high", {}, {})
    check.clears["conversion-failed"] = lambda alert: "exits_written"

    legacy = session.scalars(select(Setting).where(Setting.key.in_(LEGACY_KEYS))).all()
    changed = sorted(
        item.key
        for item in legacy
        if not item.updated_by.startswith(":") and (row is None or item.updated_at > row.updated_at)
    )
    if changed:
        check.add(
            "legacy-changed",
            "medium",
            {"keys": ", ".join(changed)},
            {"keys": changed},
        )
    check.clears["legacy-changed"] = lambda alert: "exits_written"

    unclaimed: list[tuple[str, int]] = []
    if exits:
        untrusted = trust.untrusted_sources(session)
        listed = get_setting(session, "nat.snat_trandisp", ["snat"])
        trandisp = [str(v) for v in listed] if isinstance(listed, list) else ["snat"]
        query = (
            select(FgTraffic.transip, func.count())
            .where(
                FgTraffic.time >= now - TRAFFIC_WINDOW,
                FgTraffic.time <= now,
                FgTraffic.transip.is_not(None),
                func.family(FgTraffic.transip) == 4,
                FgTraffic.trandisp.in_(trandisp),
            )
            .group_by(FgTraffic.transip)
        )
        if untrusted:
            query = query.where(FgTraffic.source.not_in(sorted(untrusted)))
        for address, n in session.execute(query):
            ip = str(ip_address(str(address)))
            if n >= UNMAPPED_SESSIONS and not _claimed(exits, ip):
                unclaimed.append((ip, int(n)))
    if unclaimed:
        unclaimed.sort(key=lambda item: (-item[1], ip_address(item[0])))
        shown = [
            {"address": ip, "kind": "private" if _not_public(ip) else "public"}
            for ip, _n in unclaimed[:LISTED]
        ]
        count = sum(n for _ip, n in unclaimed)
        words = {"private": ("özel/paylaşılan", "private/shared"), "public": ("genel", "public")}

        def listed_as(language: int) -> str:
            return ", ".join(f"{a['address']} ({words[a['kind']][language]})" for a in shown)

        check.add(
            "unmapped-transip",
            "medium",
            {"count": count, "addresses": listed_as(0)},
            {"count": count, "addresses": shown},
            params_en={"count": count, "addresses": listed_as(1)},
        )

    def unmapped_cleared(alert: Alert) -> str | None:
        listed = [
            item.get("address")
            for item in (alert.details or {}).get("addresses") or []
            if isinstance(item, Mapping)
        ]
        if exits and listed and all(isinstance(a, str) and _claimed(exits, a) for a in listed):
            return "claimed"
        return check.clean_for_a_day(alert)

    check.clears["unmapped-transip"] = unmapped_cleared
    return check.outcome()


def _claimed(exits: tuple[Egress, ...], ip: str) -> bool:
    """An address some exit claims as a public or NAT address (``internal`` exits included)."""
    try:
        return any(e.claims_public(ip) or e.claims_transip(ip) for e in exits)
    except ValueError:
        return False


def _not_public(ip: str) -> bool:
    address = ip_address(ip)
    return any(address in network for network in NOT_PUBLIC)


# -- content-filter evidence (Wave 4 contract 2.4: R20-filter) -----------------------------------

FILTER_RULE: Final = "R20-filter"
FILTER_KEY: Final = f"{FILTER_RULE}:none"


def _filter_key(key: str) -> bool:
    return key.startswith(FILTER_RULE)


def check_filter(ctx: CheckContext) -> Outcome:
    """``R20-filter:none``: no web or DNS filter record of the enabled FortiGate sources for
    ``alerts.filter_quiet_hours`` while every site day of that window is expected
    (:func:`~arcilogger_worker.compliance_filter.filter_evidence`, shared with card 7 of Uyum
    Durumu). Resolved as soon as that no longer holds: a record arrived, the window touches a day
    that is not expected, or no FortiGate source is left."""
    found = filter_evidence(ctx.session, now=ctx.now)
    if not found.alert_due:
        return Outcome([], _filter_key)
    hours = found.window_hours
    finding = Finding(
        key=FILTER_KEY,
        rule=FILTER_RULE,
        severity="high",
        title=f"İçerik filtresi kaydı gelmiyor: son {hours} saat",
        details={
            "hours": hours,
            "last_at": isoformat_site(found.last_at, "seconds") if found.last_at else None,
        },
    )
    return Outcome([finding], _filter_key)


# -- legal-validity state (Wave 5 contract 2.3; read by /legal-status and the R20 checks) --------
#
# One reader per state file, so the banner, the Panel, the Mühürleme page and the alerts judge
# from the same values (D11). The API imports these; the worker never imports the API.

#: What ``state/renewal.json`` says of a token (Wave 5 contract 2.1.2).
StampRenewalStatus = Literal["not_due", "due", "covered", "late", "uncovered"]
STAMP_RENEWAL_STATUSES: Final[frozenset[str]] = frozenset(get_args(StampRenewalStatus))
CRL_INDEX_SCHEMA: Final = crl.INDEX_SCHEMA
#: The day-check error of a stamp whose certificate is revoked at or before the stamp (the
#: English text of ``certificate_revoked``, Wave 5 contract 2.6).
REVOKED_DAY_TEXT: Final = re.compile(
    r"^A time-stamp certificate is on a revocation list: (?P<name>.+)\. Contact the provider; "
    r"legal review is recommended\.$"
)


@dataclass(frozen=True)
class StampRenewal:
    """One token of the re-stamping report."""

    #: Vault-relative POSIX path, e.g. ``2026/10/01/manifest-2026-10-01.json.zd``.
    path: str
    kind: str
    cert_not_after: datetime
    status: StampRenewalStatus
    #: Vault-relative path of the renewal record that counts, or ``None``.
    covered_by: str | None
    #: When that renewal was stamped, if the report says so.
    renewed_at: datetime | None


def stamp_renewal_path(layout: Layout) -> Path:
    return layout.state / "renewal.json"


def read_stamp_renewals(layout: Layout) -> tuple[StampRenewal, ...] | None:
    """The tokens of ``state/renewal.json``: empty when the Renew task has not written it yet,
    ``None`` (unknown) when it cannot be read. The statuses are counted from the tokens, never
    from the report's totals; an entry this reader does not understand is left out."""
    path = stamp_renewal_path(layout)
    try:
        data = _read_json(path)
    except (OSError, ValueError) as exc:
        log.info("re-stamping report %s unknown: %s", path, exc)
        return None
    if data is None:
        return ()
    entries = data.get("tokens") if isinstance(data, dict) else data
    if not isinstance(entries, list):
        log.info("re-stamping report %s unknown: no token list", path)
        return None
    found = []
    for entry in entries:
        renewal = _stamp_renewal(entry)
        if renewal is None:
            log.info("re-stamping report %s: entry left out: %r", path, entry)
            continue
        found.append(renewal)
    return tuple(found)


def _renewal_status(value: Any) -> TypeGuard[StampRenewalStatus]:
    # a list or an object is no status (and cannot be looked up in a set)
    return isinstance(value, str) and value in STAMP_RENEWAL_STATUSES


#: The last moment the site calendar can show (+03:00): a later UTC time cannot be converted.
_LAST_SITE_MOMENT: Final = datetime.max.replace(tzinfo=SITE_TZ).astimezone(UTC)


def _calendar_time(value: Any) -> datetime | None:
    """:func:`_time`, ``None`` for a time at the edge of the calendar that cannot be shown in
    UTC or in site time (from 9999-12-31T21:00Z on, RFC 5280's "no well-defined expiration"
    ``99991231235959Z`` included): the readers leave it out and never fail on it."""
    moment = _time(value)
    try:
        return moment if moment is None or moment.astimezone(UTC) <= _LAST_SITE_MOMENT else None
    except OverflowError:
        return None


def _stamp_renewal(entry: Any) -> StampRenewal | None:
    if not isinstance(entry, dict):
        return None
    path, status, not_after = (
        entry.get("path"),
        entry.get("status"),
        _calendar_time(entry.get("cert_not_after")),
    )
    if not isinstance(path, str) or not path or not _renewal_status(status) or not_after is None:
        return None
    covered_by = entry.get("covered_by")
    return StampRenewal(
        path=path,
        kind=str(entry.get("kind") or ""),
        cert_not_after=not_after.astimezone(UTC),
        status=status,
        covered_by=covered_by if isinstance(covered_by, str) and covered_by else None,
        renewed_at=_calendar_time(entry.get("renewed_at")),
    )


@dataclass(frozen=True)
class RenewRuns:
    """The runs of the ``Renew`` task as ``state/renewal.json`` keeps them (``last_run``,
    ``failures_in_a_row``; W5-A's A4). A dry run changes neither."""

    #: When the last run started (None: no run yet, or a time this reader leaves out).
    last_run_at: datetime | None = None
    #: How it ended: ``renewed``, ``not_needed``, ``failed`` or ``refused`` (None: no run yet,
    #: or not a text).
    outcome: str | None = None
    #: Failed and refused runs in a row (None: unknown).
    failures_in_a_row: int | None = 0


def read_renew_runs(layout: Layout) -> RenewRuns | None:
    """The ``Renew`` task's runs from ``state/renewal.json``: no run and no failure when the task
    has not written it yet, ``None`` (unknown) when it cannot be read. Judged from this file,
    never from the task's exit code (C12)."""
    path = stamp_renewal_path(layout)
    try:
        data = _read_json(path)
    except (OSError, ValueError) as exc:
        log.info("re-stamping runs %s unknown: %s", path, exc)
        return None
    if data is None:
        return RenewRuns()
    if not isinstance(data, dict):
        log.info("re-stamping runs %s unknown: not an object", path)
        return None
    last_run = data.get("last_run")
    last_run = last_run if isinstance(last_run, dict) else {}
    outcome = last_run.get("outcome")
    failures = data.get("failures_in_a_row")
    return RenewRuns(
        last_run_at=_calendar_time(last_run.get("at")),
        outcome=outcome if isinstance(outcome, str) else None,
        # bool is an int to Python, not a count
        failures_in_a_row=(
            failures
            if isinstance(failures, int) and not isinstance(failures, bool) and failures >= 0
            else None
        ),
    )


@dataclass(frozen=True)
class CrlListEntry:
    """A stored revocation list as ``state/crl/index.json`` lists it."""

    #: The list issuer's name (RFC 4514 text).
    issuer: str
    #: SHA-256 fingerprint of the certificate that verified the list.
    issuer_sha256: str
    this_update: datetime
    next_update: datetime | None
    file: str
    fetched_at: datetime


@dataclass(frozen=True)
class CrlFailureEntry:
    """A list URL the latest fetch could not store."""

    url: str
    at: datetime
    error: str


@dataclass(frozen=True)
class CrlIndexState:
    lists: tuple[CrlListEntry, ...] = ()
    failures: tuple[CrlFailureEntry, ...] = ()
    #: The last successful fetch of any list.
    newest_fetched_at: datetime | None = None
    #: ``False``: the index exists but is not an index this reader knows (nothing is decided
    #: from it, and nothing fails).
    readable: bool = True


def crl_index_path(layout: Layout) -> Path:
    return layout.crl_dir / crl.INDEX_NAME


def read_crl_index(layout: Layout) -> CrlIndexState:
    """``state/crl/index.json`` as the CrlFetch task writes it (Wave 5 contract 2.2): an empty
    index before the first fetch. What the fetch did (its ``failures`` and newest
    ``fetched_at``) is read from this file only, never from the task's exit code (C12); whether
    a list counts is :func:`stored_lists`'s question."""
    path = crl_index_path(layout)
    try:
        data = _read_json(path)
    except (OSError, ValueError) as exc:
        log.info("revocation list index %s unreadable: %s", path, exc)
        return CrlIndexState(readable=False)
    if data is None:
        return CrlIndexState()
    if (
        not isinstance(data, dict)
        or data.get("schema") != CRL_INDEX_SCHEMA
        or not isinstance(data.get("lists"), list)
        or not isinstance(data.get("failures"), list)
    ):
        log.info("revocation list index %s is not an %s index", path, CRL_INDEX_SCHEMA)
        return CrlIndexState(readable=False)
    lists = tuple(entry for item in data["lists"] if (entry := _crl_list(item)) is not None)
    failures = tuple(failure for item in data["failures"] if (failure := _crl_failure(item)))
    return CrlIndexState(
        lists=lists,
        failures=failures,
        newest_fetched_at=max((entry.fetched_at for entry in lists), default=None),
    )


def _crl_list(item: Any) -> CrlListEntry | None:
    if not isinstance(item, dict):
        return None
    # fetched_at is shown in site time; the list's own times are only compared
    this_update, fetched_at = _time(item.get("this_update")), _calendar_time(item.get("fetched_at"))
    next_update = _time(item.get("next_update"))
    issuer_sha256 = item.get("issuer_sha256")
    if this_update is None or fetched_at is None or not isinstance(issuer_sha256, str):
        return None
    if item.get("next_update") is not None and next_update is None:
        return None
    return CrlListEntry(
        issuer=str(item.get("issuer") or ""),
        issuer_sha256=issuer_sha256.lower(),
        this_update=this_update,
        next_update=next_update,
        file=str(item.get("file") or ""),
        fetched_at=fetched_at,
    )


def _crl_failure(item: Any) -> CrlFailureEntry | None:
    at = _calendar_time(item.get("at")) if isinstance(item, dict) else None
    if at is None:
        return None
    return CrlFailureEntry(
        url=str(item.get("url") or ""), at=at, error=str(item.get("error") or "")
    )


def pinned_roots(config: Config, layout: Layout) -> list[x509.Certificate]:
    """The roots a day token may chain to, read only (the rule of
    :func:`arcilogger_core.tsa.trusted_roots`): ``tsa.roots_dir``, and the mock TSA's test root
    only while ``tsa.provider`` is ``mock``. Empty when none can be read."""
    roots: list[x509.Certificate] = []
    try:
        if config.tsa.roots_dir is not None and config.tsa.roots_dir.is_dir():
            roots += load_roots_dir(config.tsa.roots_dir)
        mock_root = layout.state / "mock-tsa" / "root.pem"
        if config.tsa.provider == "mock" and mock_root.is_file():
            roots += load_certificates([mock_root])
    except (OSError, ValueError) as exc:
        log.info("pinned roots unreadable: %s", exc)
    return roots


@dataclass(frozen=True)
class StampInUse:
    """The time stamp of the newest sealed day: the certificates in use now."""

    day: date
    #: Its TSA certificate first, the pinned root it reaches last (``token_chain``).
    chain: tuple[x509.Certificate, ...]
    gen_time: datetime


def stamp_in_use(layout: Layout, roots: Sequence[x509.Certificate]) -> StampInUse | None:
    """The newest sealed day's stamp and its verified chain; ``None`` before the first sealed
    day, or when that day's token cannot be read or reaches no pinned root (the day check says
    why)."""
    return _newest_stamp(layout, roots)[0]


def _newest_stamp(
    layout: Layout, roots: Sequence[x509.Certificate]
) -> tuple[StampInUse | None, bool]:
    """:func:`stamp_in_use`, and whether it is known: ``False`` when the newest sealed day's
    token cannot be read or reaches no pinned root (``True`` before the first sealed day)."""
    for day in reversed(sealed_days(layout)):
        try:
            data = layout.token(day).read_bytes()
        except FileNotFoundError:
            continue  # a failed attempt leaves a manifest without a token: never sealed
        except OSError as exc:
            log.info("token of %s unreadable: %s", day, exc)
            return None, False
        try:
            chain = token_chain(data, roots)
            gen_time = inspect_token(data).gen_time
        except (TsaError, ValueError, TypeError, KeyError) as exc:
            log.info("token of %s unreadable: %s", day, exc)
            return None, False
        return (StampInUse(day, tuple(chain), gen_time), True) if chain else (None, False)
    return None, True


def issuers_in_use(stamp: StampInUse | None) -> frozenset[str]:
    """SHA-256 fingerprints of the issuers whose lists must stay fresh: the certificates of the
    newest sealed day's chain that sign another one of it (its CA certificates and its root)."""
    if stamp is None:
        return frozenset()
    return frozenset(fingerprint(certificate) for certificate in stamp.chain[1:])


def stored_lists(layout: Layout) -> StoredLists | None:
    """The stored lists that count (:func:`arcilogger_core.crl.load_stored`): a list whose file
    is gone or changed is left out (C14, C20). ``None``: ``state/crl`` cannot be read."""
    try:
        return load_stored(layout)
    except (CrlError, RecursionError) as exc:
        log.info("stored revocation lists unreadable: %s", exc)
        return None


def crl_fetch_times(index: CrlIndexState) -> dict[str, datetime]:
    """The last successful fetch of each stored list (its index ``fetched_at``), by its stored
    name."""
    fetched: dict[str, datetime] = {}
    for entry in index.lists:
        if entry.file not in fetched or fetched[entry.file] < entry.fetched_at:
            fetched[entry.file] = entry.fetched_at
    return fetched


def crl_stale_issuers(
    lists: Sequence[Crl],
    fetched: Mapping[str, datetime],
    in_use: Iterable[str],
    now: datetime,
    max_age_days: int,
) -> list[str]:
    """The issuers in use without a fresh stored list (C22), an issuer without any list
    included. ``fetched`` holds the lists' fetch times by stored name (:func:`crl_fetch_times`)."""
    fresh = _fresh_until(lists, fetched, now, max_age_days)
    return [issuer for issuer in sorted(set(in_use)) if issuer not in fresh or fresh[issuer] <= now]


def _fresh_until(
    lists: Sequence[Crl], fetched: Mapping[str, datetime], now: datetime, max_age_days: int
) -> dict[str, datetime]:
    """Until when each issuer's stored lists keep it fresh, by the issuer's fingerprint (C22): a
    list until its last fetch is ``max_age_days`` old or its own ``nextUpdate``, whichever comes
    first, and the issuer's best list counts. ``thisUpdate`` is not a measure: a root CA may
    publish only every few months. A list the index gives no fetch time counts for nothing, and
    so does one fetched later than ``now``: a skewed clock must not keep a list fresh (the clock
    check reports the skew itself)."""
    until: dict[str, datetime] = {}
    for found in lists:
        fetched_at = fetched.get(found.path.name if found.path is not None else found.file_name)
        if fetched_at is None or fetched_at > now:
            continue
        fresh = shifted(fetched_at, timedelta(days=max_age_days))
        if fresh is None:
            continue
        if found.next_update is not None:
            fresh = min(fresh, found.next_update)
        issuer = found.issuer_sha256.lower()
        if issuer not in until or until[issuer] < fresh:
            until[issuer] = fresh
    return until


@dataclass(frozen=True)
class CrlStatus:
    """The revocation lists of the issuers in use (``crl_stale``, ``R20-crl``)."""

    in_use: frozenset[str] = frozenset()
    #: The issuers in use without a fresh list (C22): all of them when the stored lists cannot
    #: be read.
    stale: tuple[str, ...] = ()
    #: When the first stale issuer that has a list went stale; ``None`` when none has one.
    since: datetime | None = None
    #: ``False``: ``state/crl`` cannot be read (nothing is resolved from it).
    readable: bool = True
    #: Index entries whose file is gone or changed (C14): the lists are evidence.
    mismatched: tuple[str, ...] = ()


def crl_status(
    lists: StoredLists | None,
    index: CrlIndexState,
    stamp: StampInUse | None,
    now: datetime,
    max_age_days: int,
) -> CrlStatus:
    """Whether the issuers of the stamp in use have a fresh verified list (Wave 5 contract
    2.3.1, C22): judged from the stored lists and their fetch times in ``index``
    (:func:`read_crl_index`) only, never from the CrlFetch task's exit code (C12)."""
    in_use = issuers_in_use(stamp)
    usable: Sequence[Crl] = lists if lists is not None else ()
    fetched = crl_fetch_times(index)
    stale = crl_stale_issuers(usable, fetched, in_use, now, max_age_days)
    until = _fresh_until(usable, fetched, now, max_age_days)
    return CrlStatus(
        in_use=in_use,
        stale=tuple(stale),
        since=min((until[issuer] for issuer in stale if issuer in until), default=None),
        readable=lists is not None,
        mismatched=lists.mismatched if lists is not None else (),
    )


def shifted(moment: datetime, delta: timedelta) -> datetime | None:
    """``moment + delta``, ``None`` beyond the calendar or the last moment site time can show
    (a time no reader should fail on)."""
    try:
        moved = moment + delta
        return moved if moved.astimezone(UTC) <= _LAST_SITE_MOMENT else None
    except OverflowError:
        return None


@dataclass(frozen=True)
class RevokedCertificate:
    #: The certificate's subject (RFC 4514 text).
    name: str
    #: When it was revoked, if known.
    since: datetime | None


def revoked_certificates(
    layout: Layout,
    stamp: StampInUse | None = None,
    lists: Sequence[Crl] | None = None,
    verifications: Mapping[date, ScheduledCheck] | None = None,
) -> list[RevokedCertificate]:
    """The time-stamp certificates revoked at or before a stamp (``crl-revoked``), each once:
    those of the newest scheduled verification of each day that is not deleted, oldest day
    first (``verifications``, read from ``state/verify`` when not given), then those of the
    newest sealed day's chain against the stored lists (``stamp`` and ``lists``), which also
    sees a revocation before the next verification runs."""
    states = _seal_states(layout)
    found: dict[str, RevokedCertificate] = {}
    if verifications is None:
        verifications = scheduled_verifications(layout)
    for day, check in sorted(verifications.items()):
        if states.get(day, {}).get("status") == "deleted":
            continue
        for error in check.errors:
            if (match := REVOKED_DAY_TEXT.match(error)) is not None:
                name = match["name"]
                found.setdefault(name, RevokedCertificate(name=name, since=None))
    if stamp is not None and lists is not None:
        for finding in check_revocation(list(stamp.chain), lists, stamp.gen_time):
            if finding.code == "crl-revoked":
                known = found.get(finding.subject)
                if known is None or known.since is None:
                    found[finding.subject] = RevokedCertificate(finding.subject, finding.at)
    return list(found.values())


def crl_max_age_days(config: Config) -> int:
    """``tsa.crl_max_age_days`` (Wave 5 contract 2.5, default 7), read where every CRL setting
    is read (:func:`arcilogger_core.crl.crl_options`). A proxy setting the fetch refuses gives
    the default here: the status is never a server error."""
    try:
        return crl_options(config).max_age_days
    except CrlError as exc:
        log.warning("CRL settings refused (%s); tsa.crl_max_age_days taken as its default", exc)
        return CrlOptions.max_age_days


# -- re-stamping, certificates and revocation lists (Wave 5 contract 2.3.1) ------------------------
#
# Switched on by W5-E (D14): the codes, their catalogue texts (alerts.texts) and their rows in the
# first-steps guide came together, and LEGAL_CHECKS is part of CHECKS. The findings carry counts
# only: no certificate name, no person, no log line.

RENEWAL_RULE: Final = "R20-renewal"
CERT_RULE: Final = "R20-cert"
CRL_RULE: Final = "R20-crl"
RENEWAL_KEY: Final = f"{RENEWAL_RULE}:none"
CERT_KEY: Final = f"{CERT_RULE}:none"
CRL_KEY: Final = f"{CRL_RULE}:none"
#: A token due for re-stamping is ``high`` for R20-cert when its certificate expires this soon
#: (at most 30 days left; an ``uncovered`` one always is). Manager answer on #117, W5-C-12.
CERT_EXPIRY_WARNING: Final = timedelta(days=30)


def _rule_of(key: str) -> str:
    """The alert code of a dedup key ``<code>:<what>``."""
    return key.partition(":")[0]


def _owned(key: str) -> Callable[[str], bool]:
    return lambda candidate: candidate == key


def _keep(alert: Alert) -> str | None:
    return None


def check_stamp_renewal(ctx: CheckContext) -> Outcome:
    """``R20-renewal:none``: a token of the re-stamping report is ``due``, or its certificate
    expired ``uncovered``. Resolved by a readable report without such a token; an unreadable one
    decides nothing."""
    renewals = read_stamp_renewals(ctx.layout)
    if renewals is None:
        return Outcome([], _owned(RENEWAL_KEY), _keep)
    counts = Counter(renewal.status for renewal in renewals)
    due, uncovered = counts["due"], counts["uncovered"]
    findings = []
    if due or uncovered:
        findings.append(
            Finding(
                key=RENEWAL_KEY,
                rule=RENEWAL_RULE,
                severity="high",
                title=(
                    f"Yeniden damgalama gerekiyor: {due} damganın zamanı geldi, {uncovered} damga "
                    "yeniden damgalanmadan sona erdi"
                ),
                details={
                    "due": due,
                    "uncovered": uncovered,
                    "title_en": (
                        f"Re-stamping needed: {due} stamp(s) due, {uncovered} expired without "
                        "re-stamping"
                    ),
                },
            )
        )
    return Outcome(findings, _owned(RENEWAL_KEY), lambda alert: "renewal_ok")


def check_stamp_cert(ctx: CheckContext) -> Outcome:
    """``R20-cert:none``: a time-stamp certificate is on a revocation list at or before a stamp
    (``critical``); or a token due for re-stamping, not covered yet, has a certificate that
    expires within :data:`CERT_EXPIRY_WARNING`, or a token's certificate expired with no renewal
    that counts (``uncovered``) (``high``). Resolved when none holds and every input can be
    read: the re-stamping report, the scheduled verifications, the newest sealed day's stamp
    (reaching a pinned root) and, once a day is sealed, every stored list (none changed or
    gone, C14). An unreadable one decides nothing: a revocation it raised stays open."""
    stamp, stamp_known = _newest_stamp(ctx.layout, pinned_roots(ctx.config, ctx.layout))
    lists = stored_lists(ctx.layout)
    verifications, verifications_known = read_scheduled_verifications(ctx.layout)
    revoked = revoked_certificates(ctx.layout, stamp, lists, verifications)
    renewals = read_stamp_renewals(ctx.layout)
    expiring = sum(
        1
        for renewal in renewals or ()
        if renewal.status == "due" and renewal.cert_not_after - ctx.now <= CERT_EXPIRY_WARNING
    )
    uncovered = sum(1 for renewal in renewals or () if renewal.status == "uncovered")
    findings = []
    if revoked:
        findings.append(
            Finding(
                key=CERT_KEY,
                rule=CERT_RULE,
                severity="critical",
                title=f"Zaman damgası sertifikası iptal listesinde: {len(revoked)} sertifika",
                details={
                    "revoked": len(revoked),
                    "expiring": expiring,
                    "uncovered": uncovered,
                    "title_en": (
                        f"Time-stamp certificate on a revocation list: {len(revoked)} "
                        "certificate(s)"
                    ),
                },
            )
        )
    elif expiring or uncovered:
        days = CERT_EXPIRY_WARNING.days
        findings.append(
            Finding(
                key=CERT_KEY,
                rule=CERT_RULE,
                severity="high",
                title=(
                    f"Zaman damgası sertifikası: {expiring} damganın sertifikası {days} gün "
                    f"içinde sona eriyor, {uncovered} damganınki yeniden damgalanmadan sona erdi"
                ),
                details={
                    "revoked": 0,
                    "expiring": expiring,
                    "uncovered": uncovered,
                    "title_en": (
                        f"Time-stamp certificate: {expiring} stamp(s) expire within {days} "
                        f"days, {uncovered} expired without re-stamping"
                    ),
                },
            )
        )

    # nothing sealed yet: no chain to check the lists against, and no list is needed
    lists_known = stamp is None or lists is not None
    lists_intact = stamp is None or (lists is not None and not lists.mismatched)
    readable = renewals is not None and verifications_known and stamp_known and lists_known

    def clear(alert: Alert) -> str | None:
        if not readable:
            return None
        # A changed or deleted list (C14/C20) keeps its index entry for good, so it holds open
        # only an alert that carries a revocation; an expiry alert resolves on renewal.json
        # (the damage itself is reported by CrlFetch's stored_file_changed and the day page).
        if not lists_intact and _carries_revocation(alert):
            return None
        return "certificate_ok"

    return Outcome(findings, _owned(CERT_KEY), clear)


def _carries_revocation(alert: Alert) -> bool:
    """Whether an open ``R20-cert`` alert was raised for a revocation: its ``revoked`` count,
    or its severity (the store keeps the highest severity an alert reached, while a later
    expiry-only finding writes ``revoked`` 0 into its details)."""
    revoked = (alert.details or {}).get("revoked")
    return alert.severity == "critical" or (isinstance(revoked, int) and revoked > 0)


def check_stamp_crl(ctx: CheckContext) -> Outcome:
    """``R20-crl:none``: an issuer of the newest sealed day's chain has no fresh stored
    revocation list (C22: fetched in the last ``tsa.crl_max_age_days`` days, not past its
    ``nextUpdate``, still matching its index entry), or ``state/crl`` cannot be read while one
    is in use. Judged from the stored lists and ``state/crl/index.json``, never from the
    CrlFetch task's exit code (C12); lists that cannot be read resolve nothing."""
    max_age = crl_max_age_days(ctx.config)
    stamp = stamp_in_use(ctx.layout, pinned_roots(ctx.config, ctx.layout))
    index = read_crl_index(ctx.layout)
    status = crl_status(stored_lists(ctx.layout), index, stamp, ctx.now, max_age)
    findings = []
    if status.stale:
        newest = index.newest_fetched_at
        findings.append(
            Finding(
                key=CRL_KEY,
                rule=CRL_RULE,
                severity="medium",
                title=f"{max_age} günden yeni doğrulanmış bir iptal listesi (SİL) yok",
                details={
                    "stale_issuers": len(status.stale),
                    "failures": len(index.failures),
                    # C14: stored lists left out because their file is gone or changed
                    "changed_files": len(status.mismatched),
                    "newest_fetched_at": isoformat_site(newest, "seconds") if newest else None,
                    "max_age_days": max_age,
                    "title_en": f"No verified revocation list newer than {max_age} days",
                },
            )
        )

    def clear(alert: Alert) -> str | None:
        return "crl_fresh" if status.readable else None

    return Outcome(findings, _owned(CRL_KEY), clear)


#: The checks of Wave 5 contract 2.3.1, the last ones of :data:`CHECKS`.
LEGAL_CHECKS: Final[tuple[tuple[str, Callable[[CheckContext], Outcome]], ...]] = (
    ("stamp_renewal", check_stamp_renewal),
    ("stamp_cert", check_stamp_cert),
    ("stamp_crl", check_stamp_crl),
)


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
    # ADR-0016: separate checks (transactions), so an exit value that cannot be read never
    # stops a lease evidence alert.
    ("lease_evidence", check_lease_evidence),
    ("nat_exits", check_nat_exits),
    ("filter", check_filter),
    # Wave 5 contract 2.3.1 (D14): re-stamping, the time-stamp certificate, revocation lists.
    *LEGAL_CHECKS,
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
