"""The worker's part of the browser administration (ADR-0014; contract 2.9, 2.14).

The administration agent ArciAdmin has no database (contract 2.2): it records the outcome of every
request in its journal ``admin\\journal\\<YYYY-MM-DD>.jsonl`` (``JournalLine`` of
:mod:`arcilogger_core.admin.contract`), which only the agent writes and the worker may read. The
job ``audit.import`` (with :mod:`.evidence_jobs`) copies every line into the hash-chained audit
log **once**, actor ``system``:

* ``admin.<state>`` — ``admin.applied``, ``admin.failed``, ``admin.rejected``, ``admin.action``,
  … for the line's state, target ``admin-request:<id>``, key ``admin:<request>:<state>``: the
  requester, the version, the configuration's SHA-256 before and after, the masked changes (a
  secret only as ``set``/``unset``), the code, whether it was rolled back and, for an action,
  what its result says (credits, the stamp test's serial and legal grade, the seal outcome, the
  replica test's outcome). A request rejected before its signature was verified names no
  requester (contract Day 0 note D0-8): nothing of an unverified file reaches the chain but its
  id and code.
* ``settings.secret_change`` — one per stored credential the line changed (``secrets``), target
  ``secret:<name>``, key ``admin-secret:<line>:<name>``, with ``operator: "web:<username>"``: the
  same action as a change made with ``arcilogger-core secret set`` on the console.

``audit_imports`` remembers the keys under the audit chain lock, so a restart or a second
worker never imports a line twice (:func:`.evidence_jobs.import_events`). A line that is not a
valid journal line is skipped and reported; it never blocks the lines after it. The folder is
the ``admin`` folder next to the worker's fixed ``--config`` path, never one the configuration
names (Day 0 notes D0-1, D0-2).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Final

from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.admin.contract import (
    ActionResult,
    AdminLayout,
    CreditResult,
    DocumentError,
    JournalLine,
    SealResult,
    TsaTestResult,
    parse_document,
)
from arcilogger_worker.evidence_jobs import SECRET_ACTION, AuditEvent, ImportReport, import_events

log = logging.getLogger("arcilogger_worker.admin")

#: Where the imported lines come from (``details.source``), relative to ``ProgramData``.
JOURNAL_SOURCE: Final = "admin/journal"


def _lines(path: Path, report: ImportReport) -> list[bytes]:
    try:
        return path.read_bytes().splitlines()
    except FileNotFoundError:
        return []
    except OSError as exc:
        report.problems.append(f"{path.name}: {type(exc).__name__}")
        return []


def action_result(result: ActionResult) -> dict[str, Any]:
    """What ``admin.action`` keeps of a task's result (contract 2.14)."""
    if isinstance(result, CreditResult):
        return result.model_dump(mode="json")
    if isinstance(result, TsaTestResult):
        return {
            "provider": result.provider,
            "verified": result.verified,
            "legal_grade": result.legal_grade,
            "serial": result.serial,
            "gen_time": None if result.gen_time is None else result.gen_time.isoformat(),
        }
    if isinstance(result, SealResult):
        return {
            "day": result.day.isoformat(),
            "status": result.status,
            "serial": result.serial,
            "gen_time": None if result.gen_time is None else result.gen_time.isoformat(),
        }
    return {"ok": result.ok}  # the replica test (its message stays in the request's status)


def request_event(line: JournalLine, source: str) -> AuditEvent:
    """``admin.<state>`` of one journal line."""
    details: dict[str, Any] = {
        "request": line.request,
        "line": line.line,
        "at": line.at.isoformat(),
        "kind": line.kind,
        "section": line.section,
        "version": line.version,
        "config_sha256": (
            None if line.config_sha256 is None else line.config_sha256.model_dump(mode="json")
        ),
        "changes": {path: change.model_dump(mode="json") for path, change in line.changes.items()},
        "code": line.code,
        "rolled_back": line.rolled_back,
        "source": source,
    }
    if line.requested_by is not None:
        details["requested_by"] = line.requested_by.model_dump(mode="json")
    if line.result is not None:
        details["result"] = action_result(line.result)
    return AuditEvent(
        f"admin:{line.request}:{line.state}",
        f"admin.{line.state}",
        f"admin-request:{line.request}",
        details,
    )


def secret_change_events(line: JournalLine, source: str) -> list[AuditEvent]:
    """``settings.secret_change`` for every stored credential a journal line changed."""
    if line.requested_by is None:  # only a verified request changes a secret
        return []
    operator = f"web:{line.requested_by.username}"
    return [
        AuditEvent(
            f"admin-secret:{line.line}:{record.name}",
            SECRET_ACTION,
            f"secret:{record.name}",
            {
                "name": record.name,
                "action": record.action,
                "result": record.result,
                "operator": operator,
                "at": line.at.isoformat(),
                "request": line.request,
                "source": source,
            },
        )
        for record in line.secrets
    ]


def journal_events(admin: AdminLayout, report: ImportReport) -> list[AuditEvent]:
    """Every line of the agent's journal, oldest day first, in file order."""
    folder = admin.journal
    events: list[AuditEvent] = []
    if not folder.is_dir():
        return events
    for path in sorted(folder.glob("*.jsonl")):
        for number, raw in enumerate(_lines(path, report), 1):
            if not raw.strip():
                continue
            try:
                line = parse_document(JournalLine, raw)
            except DocumentError:
                report.problems.append(f"{path.name} line {number}: not a journal line")
                continue
            source = f"{JOURNAL_SOURCE}/{path.name} line {number}"
            events.append(request_event(line, source))
            events.extend(secret_change_events(line, source))
    return events


def import_admin_events(
    sessions: sessionmaker[Session], admin: AdminLayout, report: ImportReport | None = None
) -> ImportReport:
    """The administration's journal lines into the audit log, each once (``audit.import``)."""
    report = report if report is not None else ImportReport()
    known = len(report.problems)
    import_events(sessions, journal_events(admin, report), report)
    if len(report.problems) > known:
        log.warning("admin journal import: %s", "; ".join(report.problems[known:][:10]))
    return report
