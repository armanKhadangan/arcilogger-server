"""Worker jobs around the evidence path (spec 8.1, 8.4, 8.5, 9.6).

``evidence.purge`` (daily)
    Evidence packages are copies handed out for a request; the evidence itself stays in the
    vault. A ``ready`` package whose ``expires_at`` passed is deleted from ``exports/`` and its
    row becomes ``expired``; :func:`arcilogger_core.evidence.purge_expired` removes packages
    older than ``evidence.keep_days`` that have no row (built with the command line) and
    unfinished ``*.zip.part`` files. A ``ready`` row whose file vanished becomes ``deleted``;
    a build left ``queued``/``building`` for more than a day (the API stopped) becomes
    ``failed``. Anything that changed is audited (``evidence.purge``, actor ``system``).

``audit.import`` (every 15 minutes)
    Sealing and retention run as scheduled tasks without the database (``arcilogger-core``),
    so they cannot write the audit log themselves. Their results are in files only they may
    write (ADR-0007). This job copies every event into the audit log **once**:

    * ``state/seal/<day>.json`` — ``seal.run`` ``sealed`` for a sealed day, ``failed`` for the
      failed attempt the file shows, with ``requested`` (the task request's id) when the attempt
      was one of "Şimdi mühürle" (ADR-0014);
    * ``vault/deletions/<date>-imha.json`` — ``retention.run`` ``deleted`` when the record is
      time-stamped (``.zd``) and every day it lists is really gone from the vault (and no state
      file names another record as the one that deleted it). A record that does not meet this
      (the run failed at the stamp, or stopped while deleting) is **not** reported as a
      deletion: after :data:`RETENTION_SETTLE` it is imported as ``retention.run``
      ``incomplete`` with the days still in the vault; before that it is left for the next run;
    * ``state/alerts.jsonl`` — every ``R20-seal`` alert (one per failed attempt, so attempts
      that a later attempt overwrote in the state file before this job ran are still recorded:
      ``seal.run`` ``failed``; same key as the state file's attempt, so each attempt is one
      entry) and every refused retention run (``R20-retention`` other than the ``info`` of a
      successful run, which the deletion record covers: ``retention.run`` ``refused``).

    * ``state/seal/secret-changes.jsonl`` — ``settings.secret_change`` for every change of a
      stored credential (Kamu SM, database, replica) made with ``arcilogger-core secret
      set|delete`` (name, action, operator and time; never a value);
    * the browser administration's journals (``admin.*``, ``settings.secret_change`` with
      ``operator: "web:<user>"``): :mod:`.admin_jobs`.

    ``audit_imports`` remembers the imported event keys in the same transaction as the entry,
    under the audit chain lock, so a second worker or a restart never imports twice.

The worker may not import the API (the API depends on the worker) and may not write the audit
spool; it writes the chain with :func:`arcilogger_db.audit.append_audit`, and the API's
periodic flush copies the entries into the spool, which is sealed nightly.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.evidence import purge_expired
from arcilogger_core.evidence.builder import PACKAGE_PREFIX
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now
from arcilogger_db.audit import AUDIT_LOCK_KEY, append_audit
from arcilogger_db.models import AuditImport, EvidencePackage
from arcilogger_db.settings import get_setting

log = logging.getLogger("arcilogger_worker.evidence")

#: A build that has not finished after this long was interrupted (the API stopped).
STALE_BUILD: Final = timedelta(days=1)
#: Longest list of ids or names put into one audit entry (the counts are always complete).
LIST_MAX: Final = 100
PACKAGE_FILE: Final = re.compile(r"^" + re.escape(PACKAGE_PREFIX) + r"[A-Za-z0-9._-]{1,100}\.zip$")
INTERRUPTED: Final = (
    "Oluşturma yarıda kaldı (bir günden uzun sürdü; API durmuş olabilir); paketi yeniden "
    "oluşturun. / The build was interrupted (unfinished after a day; the API may have stopped); "
    "create the package again."
)
MISSING: Final = (
    "Paket dosyası exports klasöründe yok (silinmiş). / The package file is missing from exports."
)
#: Text of the error of an unreadable state file or record (never the file content).
SEAL_STATE_SOURCE: Final = "state/seal"
ALERTS_SOURCE: Final = "state/alerts.jsonl"
SEAL_ACTION: Final = "seal.run"
SECRET_ACTION: Final = "settings.secret_change"  # noqa: S105 - an action name
SECRET_SOURCE: Final = "state/seal/secret-changes.jsonl"  # noqa: S105 - a file name
SECRET_ID: Final = re.compile(r"^[0-9a-f]{32}$")
#: A seal state's ``requested``: the id of the task request of "Şimdi mühürle".
REQUEST_ID: Final = SECRET_ID
RETENTION_ACTION: Final = "retention.run"
SEAL_ALERT: Final = "R20-seal"
RETENTION_ALERT: Final = "R20-retention"
#: A deletion record that is still incomplete after this long belongs to a failed run.
RETENTION_SETTLE: Final = timedelta(days=1)
#: Longest error or alert text copied into an audit entry.
TEXT_MAX: Final = 500
SEAL_FIELDS: Final = (
    "sealed_at",
    "attempts",
    "serial",
    "gen_time",
    "tsa_name",
    "tsa_cert_not_after",
    "provider",
    "legal_grade",
    "manifest_sha256",
    "token_sha256",
    "files",
    "lines",
    "stamped_late",
)


# -- evidence.purge -------------------------------------------------------------------------------


def keep_days(session: Session) -> int:
    try:
        return max(1, int(get_setting(session, "evidence.keep_days")))
    except (TypeError, ValueError):
        return 30


def package_path(layout: Layout, name: str) -> Path | None:
    """``exports/<name>`` for a valid package file name, else ``None`` (never outside)."""
    return layout.exports / name if PACKAGE_FILE.match(name) else None


@dataclass
class PurgeReport:
    keep_days: int
    expired: list[int] = field(default_factory=list)
    missing: list[int] = field(default_factory=list)
    interrupted: list[int] = field(default_factory=list)
    files_deleted: list[str] = field(default_factory=list)
    #: Files that could not be deleted now (e.g. being downloaded on Windows); retried tomorrow.
    busy: list[str] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        return bool(self.expired or self.missing or self.interrupted or self.files_deleted)

    def details(self) -> dict[str, Any]:
        result: dict[str, Any] = {"keep_days": self.keep_days}
        for name in ("expired", "missing", "interrupted", "files_deleted", "busy"):
            values = getattr(self, name)
            result[name] = values[:LIST_MAX]
            result[f"{name}_count"] = len(values)
        return result


def purge_evidence(session: Session, layout: Layout, *, now: datetime) -> PurgeReport:
    """Delete expired packages, reconcile the rows with ``exports/``; audited, committed."""
    report = PurgeReport(keep_days=keep_days(session))
    try:
        report.files_deleted += [p.name for p in purge_expired(layout, report.keep_days, now=now)]
    except OSError as exc:  # e.g. a package open for download on Windows: retried tomorrow
        log.warning("evidence purge stopped at %s: %s", exc.filename, exc)
        report.busy.append(Path(str(exc.filename or "?")).name)
    purged_by_age = set(report.files_deleted)
    kept = timedelta(days=report.keep_days)
    rows = session.scalars(
        select(EvidencePackage)
        .where(EvidencePackage.status == "ready")
        .order_by(EvidencePackage.id)
        .with_for_update(skip_locked=True)
    ).all()
    for row in rows:
        path = package_path(layout, row.path)
        if row.expires_at <= now:
            if path is not None and path.exists():
                try:
                    path.unlink()
                except OSError as exc:
                    log.warning("cannot delete %s now: %s", row.path, exc)
                    report.busy.append(row.path)
                    continue
                report.files_deleted.append(row.path)
            row.status = "expired"
            report.expired.append(row.id)
        elif path is None or not path.is_file():
            aged = row.finished_at is not None and row.finished_at + kept <= now
            if row.path in purged_by_age or aged:
                row.status = "expired"
                report.expired.append(row.id)
            else:
                row.status, row.error = "deleted", MISSING
                report.missing.append(row.id)
    stale = session.scalars(
        select(EvidencePackage)
        .where(
            EvidencePackage.status.in_(("queued", "building")),
            EvidencePackage.created_at < now - STALE_BUILD,
        )
        .order_by(EvidencePackage.id)
        .with_for_update(skip_locked=True)
    ).all()
    for row in stale:
        row.status, row.error, row.finished_at = "failed", INTERRUPTED, now
        report.interrupted.append(row.id)
    if report.changed:
        append_audit(session, action="evidence.purge", target="exports", details=report.details())
    session.commit()
    return report


# -- audit.import ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditEvent:
    """One event of a scheduled job, as it goes into the audit log."""

    key: str
    action: str
    target: str
    details: Mapping[str, Any]


@dataclass
class ImportReport:
    imported: list[str] = field(default_factory=list)
    #: Unreadable state files or records, and events the audit log refused (with the reason).
    problems: list[str] = field(default_factory=list)
    #: Deletion records not complete yet (a retention run in progress, or failed recently).
    waiting: list[str] = field(default_factory=list)
    seen: int = 0

    def details(self) -> dict[str, Any]:
        return {
            "events": self.seen,
            "imported": len(self.imported),
            "keys": self.imported[:LIST_MAX],
            "problems": self.problems[:LIST_MAX],
            "waiting": self.waiting[:LIST_MAX],
        }


def _read(path: Path, report: ImportReport) -> Any:
    try:
        return json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        report.problems.append(f"{path.name}: {type(exc).__name__}")
        return None


def seal_events(layout: Layout, report: ImportReport) -> list[AuditEvent]:
    """``seal.run`` events from ``state/seal/<day>.json``: sealed days and failed attempts."""
    folder = layout.state / "seal"
    events: list[AuditEvent] = []
    if not folder.is_dir():
        return events
    for path in sorted(folder.glob("*.json")):
        data = _read(path, report)
        if not isinstance(data, dict):
            continue
        try:
            day = date.fromisoformat(str(data.get("day")))
            attempts = int(data.get("attempts") or 0)
        except (TypeError, ValueError):
            report.problems.append(f"{path.name}: not a seal state")
            continue
        if path.stem != day.isoformat():
            report.problems.append(f"{path.name}: state of another day")
            continue
        base: dict[str, Any] = {
            "day": day.isoformat(),
            "source": f"{SEAL_STATE_SOURCE}/{path.name}",
        }
        # "Şimdi mühürle" (ADR-0014): the task request the attempt honoured, by its id.
        requested = data.get("requested")
        if isinstance(requested, str) and REQUEST_ID.match(requested):
            base["requested"] = requested
        target = f"seal:{day.isoformat()}"
        if data.get("sealed_at"):  # sealed (also kept when retention later deleted the day)
            details: dict[str, Any] = {**base, "outcome": "sealed"}
            details.update({name: data.get(name) for name in SEAL_FIELDS})
            events.append(
                AuditEvent(f"seal:{day.isoformat()}:sealed", SEAL_ACTION, target, details)
            )
        elif data.get("last_error") and attempts > 0:
            details = {
                **base,
                "outcome": "failed",
                "attempt": attempts,
                "at": data.get("last_attempt_at"),
                "error": str(data["last_error"])[:TEXT_MAX],
                "next_attempt_at": data.get("next_attempt_at"),
            }
            key = f"seal:{day.isoformat()}:attempt:{attempts}"
            events.append(AuditEvent(key, SEAL_ACTION, target, details))
    return events


def _deleted_by(layout: Layout, day: date) -> str | None:
    """The deletion record ``state/seal/<day>.json`` names (``None``: none or unreadable)."""
    try:
        state = json.loads(layout.seal_state(day).read_bytes())
    except (OSError, ValueError):
        return None
    value = state.get("deleted_by") if isinstance(state, dict) else None
    return str(value) if value else None


def _record_time(path: Path, data: Mapping[str, Any]) -> datetime:
    """When a deletion record was written: its ``created_at``, else the file time."""
    try:
        created = datetime.fromisoformat(str(data.get("created_at")))
        if created.tzinfo is not None:
            return created
    except ValueError:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime).astimezone()


def retention_events(
    layout: Layout, report: ImportReport, *, now: datetime | None = None
) -> list[AuditEvent]:
    """``retention.run`` events: one per deletion record that really deleted its days."""
    folder = layout.deletions
    events: list[AuditEvent] = []
    if not folder.is_dir():
        return events
    now = now or utc_now()
    for path in sorted(folder.glob("*-imha.json")):
        data = _read(path, report)
        if not isinstance(data, dict) or not isinstance(data.get("days"), list):
            if data is not None:
                report.problems.append(f"{path.name}: not a deletion record")
            continue
        days = [str(entry.get("day")) for entry in data["days"] if isinstance(entry, dict)]
        try:
            listed = [date.fromisoformat(day) for day in days]
        except ValueError:
            report.problems.append(f"{path.name}: not a deletion record")
            continue
        token = path.with_name(path.name + ".zd")
        stamped = token.is_file()
        still_sealed = [day.isoformat() for day in listed if layout.manifest(day).exists()]
        other_record = [
            day.isoformat() for day in listed if _deleted_by(layout, day) not in (None, path.name)
        ]
        complete = bool(stamped and listed and not still_sealed and not other_record)
        relative = "vault/" + path.relative_to(layout.vault).as_posix()
        details: dict[str, Any] = {
            "outcome": "deleted" if complete else "incomplete",
            "record": relative,
            "record_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "token_sha256": hashlib.sha256(token.read_bytes()).hexdigest() if stamped else None,
            "created_at": data.get("created_at"),
            "cutoff": data.get("cutoff"),
            "retention_days": data.get("retention_days"),
            "operator": data.get("operator"),
            "days": days,
            "day_count": len(days),
        }
        key = f"retention:{path.name}"
        if not complete:
            if now - _record_time(path, data) < RETENTION_SETTLE:
                report.waiting.append(path.name)
                continue
            details.update(
                still_in_vault=still_sealed,
                deleted_by_another_record=other_record,
                reason=(
                    "the record has no time stamp: the run failed before deleting anything"
                    if not stamped
                    else "not every listed day was deleted by this record"
                ),
            )
            key = f"{key}:incomplete"
        events.append(AuditEvent(key, RETENTION_ACTION, f"retention:{path.name}", details))
    return events


def alert_events(layout: Layout, report: ImportReport) -> list[AuditEvent]:
    """Failed sealing attempts and refused retention runs from ``state/alerts.jsonl``."""
    try:
        lines = layout.alerts.read_bytes().splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        report.problems.append(f"{layout.alerts.name}: {type(exc).__name__}")
        return []
    events: list[AuditEvent] = []
    for number, line in enumerate(lines, 1):
        if SEAL_ALERT.encode() not in line and RETENTION_ALERT.encode() not in line:
            continue  # other alerts are not job runs
        try:
            alert = json.loads(line)
            rule, severity = alert["rule"], alert.get("severity")
            raw = alert.get("details")
            facts: dict[str, Any] = raw if isinstance(raw, dict) else {}
        except (ValueError, TypeError, KeyError):
            report.problems.append(f"{layout.alerts.name} line {number}: unreadable")
            continue
        digest = hashlib.sha256(line).hexdigest()
        base = {
            "source": f"{ALERTS_SOURCE} line {number}",
            "alert_sha256": digest,
            "at": alert.get("time"),
            "severity": severity,
            "message": str(alert.get("message") or "")[:TEXT_MAX],
        }
        if rule == SEAL_ALERT:
            try:
                day = date.fromisoformat(str(facts.get("day")))
                attempt = int(facts["attempts"])
            except (KeyError, TypeError, ValueError):
                report.problems.append(f"{layout.alerts.name} line {number}: not a seal alert")
                continue
            details = {
                **base,
                "day": day.isoformat(),
                "outcome": "failed",
                "attempt": attempt,
                "retryable": facts.get("retryable"),
            }
            key = f"seal:{day.isoformat()}:attempt:{attempt}"
            events.append(AuditEvent(key, SEAL_ACTION, f"seal:{day.isoformat()}", details))
        elif rule == RETENTION_ALERT and severity != "info":
            details = {**base, "outcome": "refused", "day": facts.get("day")}
            events.append(
                AuditEvent(f"retention:alert:{digest}", RETENTION_ACTION, "retention", details)
            )
    return events


def secret_events(layout: Layout, report: ImportReport) -> list[AuditEvent]:
    """Credential changes (spec 6.10, 8.5) from the change log the core command line writes."""
    path = layout.secret_changes
    try:
        lines = path.read_bytes().splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        report.problems.append(f"{path.name}: {type(exc).__name__}")
        return []
    events: list[AuditEvent] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
            change_id, name = str(entry["id"]), str(entry["name"])
            if not SECRET_ID.match(change_id):
                raise ValueError("bad id")
        except (ValueError, TypeError, KeyError):
            report.problems.append(f"{path.name} line {number}: unreadable")
            continue
        details = {
            "name": name[:TEXT_MAX],
            "action": str(entry.get("action") or "")[:TEXT_MAX],
            "result": str(entry.get("result") or "")[:TEXT_MAX],
            "operator": str(entry.get("operator") or "")[:TEXT_MAX],
            "at": entry.get("time"),
            "source": f"{SECRET_SOURCE} line {number}",
        }
        events.append(AuditEvent(f"secret:{change_id}", SECRET_ACTION, f"secret:{name}", details))
    return events


def import_events(
    sessions: sessionmaker[Session], events: Iterable[AuditEvent], report: ImportReport
) -> ImportReport:
    """Write every event not imported yet: one transaction per event, under the chain lock."""
    events = list(events)
    report.seen += len(events)
    with sessions() as session:
        known = set(session.scalars(select(AuditImport.key)))
    for event in events:
        if event.key in known:
            continue
        try:
            with sessions() as session:
                session.execute(select(func.pg_advisory_xact_lock(AUDIT_LOCK_KEY)))
                if session.get(AuditImport, event.key) is not None:
                    continue  # imported by another worker meanwhile
                entry = append_audit(
                    session, action=event.action, target=event.target, details=event.details
                )
                session.add(AuditImport(key=event.key, audit_id=entry.id))
                session.commit()
        except ValueError as exc:  # details the chain cannot store exactly (a damaged file)
            report.problems.append(f"{event.key}: {exc}")
            continue
        report.imported.append(event.key)
    return report


def import_job_events(
    sessions: sessionmaker[Session], layout: Layout, *, now: datetime | None = None
) -> ImportReport:
    """The state files first (the latest attempt with its next retry), then the deletion
    records, then the alerts (earlier attempts and refused retention runs)."""
    report = ImportReport()
    import_events(sessions, seal_events(layout, report), report)
    import_events(sessions, retention_events(layout, report, now=now), report)
    import_events(sessions, alert_events(layout, report), report)
    import_events(sessions, secret_events(layout, report), report)
    if report.problems:
        log.warning("audit import: %s", "; ".join(report.problems[:10]))
    return report
