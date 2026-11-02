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
* ``admin.system`` — one per line of ApplySystem's ``admin\\system\\journal.jsonl``
  (``SystemJournalLine``: a firewall, Windows Time or Zamane client change it applied as
  SYSTEM), target ``system``, key ``admin-system:<line>``: the number of syslog addresses and
  outbound rules, whether the time peers changed, the promoted client's SHA-256 and who
  approved it.

``audit_imports`` remembers the keys under the audit chain lock, so a restart or a second
worker never imports a line twice (:func:`.evidence_jobs.import_events`). A line that is not a
valid journal line is skipped and reported; it never blocks the lines after it. The folder is
the ``admin`` folder next to the worker's fixed ``--config`` path, never one the configuration
names (Day 0 notes D0-1, D0-2).

The worker also works the **worker channel** (contract 2.9): requests the API stores as
``admin_requests`` rows with ``channel = 'worker'`` because they need no privilege and no secret
(job ``admin.requests``, every 15 seconds):

* ``worker.test_email`` (``params.to``) — the test e-mail of ``alerts test-email`` through
  ``[smtp]``; the result is ``{ok, to, smtp_code, error_text}`` with every address masked
  (``b***@ornek.example``) and the audit entry ``smtp.test``. ``smtp_not_configured`` when the
  server or the sender is not set, ``smtp_test_failed`` when the e-mail was not accepted or the
  password cannot be read (``error_text`` is then a fixed sentence: the error itself may name a
  file of the server and stays in the worker's log).
* ``worker.lease_rebuild`` (``params.from``, ``params.to``) — the lease timeline of each day
  rebuilt in order (:func:`.correlation.rebuild.rebuild_lease_day`, a ``leases.rebuild`` job
  run per day with the request's id). The row is ``running`` with ``{done_days, total_days,
  failed_days}`` after every day; the last day finishes it (``succeeded``, or ``failed`` with
  ``lease_rebuild_failed`` when a day failed) with the audit entry ``leases.rebuild``.

A row is claimed with ``FOR UPDATE SKIP LOCKED`` and stays locked while its step runs, so two
workers never work on one request; the final state and its audit entry are one transaction. A
step that raises (an error of the SMTP library, say) is rolled back and recorded in a
transaction of its own, with the error's type only: the test e-mail fails
(``smtp_test_failed``), the lease rebuild counts the day as failed and goes on. One request
therefore never holds up the ones queued after it.

The API checks every request before it queues it (contract 2.9); the worker checks again what
decides where an e-mail goes and how much is rebuilt, so a row written past the API never lets
the organisation's SMTP server mail an address of its choosing: the address must be one
address, listed in ``alerts.email_recipients`` or the requester's own e-mail; the range at most
:data:`LEASE_REBUILD_DAYS` days, in order and not in the future. Anything else fails with
``validation_failed``. The rate limits and "not before the first log" stay the API's. A request
still queued at its ``expires_at`` is ``expired`` (``request_expired``) and nothing is done.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.admin.contract import (
    TERMINAL_STATES,
    ActionResult,
    AdminLayout,
    CreditResult,
    DocumentError,
    JournalLine,
    SealResult,
    SystemJournalLine,
    TsaTestResult,
    parse_document,
)
from arcilogger_core.config import EMAIL_ADDRESS, Config
from arcilogger_core.timeutil import site_day
from arcilogger_db.audit import append_audit
from arcilogger_db.models import AdminRequest, User
from arcilogger_db.settings import get_setting
from arcilogger_worker.alerts.commands import TrialEmailRefusedError, trial_email
from arcilogger_worker.alerts.notify import web_base_url
from arcilogger_worker.correlation.rebuild import rebuild_lease_day
from arcilogger_worker.evidence_jobs import SECRET_ACTION, AuditEvent, ImportReport, import_events

log = logging.getLogger("arcilogger_worker.admin")

#: Where the imported lines come from (``details.source``), relative to ``ProgramData``.
JOURNAL_SOURCE: Final = "admin/journal"
SYSTEM_JOURNAL_SOURCE: Final = "admin/system/journal.jsonl"
SYSTEM_ACTION: Final = "admin.system"

#: The worker channel's kinds (``WorkerKind`` of the contract) and their audit actions.
TEST_EMAIL: Final = "worker.test_email"
LEASE_REBUILD: Final = "worker.lease_rebuild"
WORKER_KINDS: Final = (TEST_EMAIL, LEASE_REBUILD)
SMTP_TEST_ACTION: Final = "smtp.test"
LEASES_REBUILD_ACTION: Final = "leases.rebuild"
#: A worker request not done yet.
OPEN_STATES: Final = ("queued", "running")
#: Longest lease rebuild (the API refuses more with ``lease_rebuild_range``).
LEASE_REBUILD_DAYS: Final = 31
#: ``admin.requests`` starts no further step after this long (a rebuild goes on next run).
REQUESTS_BUDGET: Final = timedelta(seconds=30)
_ADDRESS: Final = re.compile(EMAIL_ADDRESS)
_ADDRESS_IN_TEXT: Final = re.compile(
    r"[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"
)


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


def system_events(admin: AdminLayout, report: ImportReport) -> list[AuditEvent]:
    """Every line of ApplySystem's journal, in file order."""
    events: list[AuditEvent] = []
    path = admin.system_journal
    for number, raw in enumerate(_lines(path, report), 1):
        if not raw.strip():
            continue
        try:
            line = parse_document(SystemJournalLine, raw)
        except DocumentError:
            report.problems.append(f"{path.name} line {number}: not a system journal line")
            continue
        block = {"firewall": line.firewall, "time": line.time, "jar": line.jar}[line.change]
        if block is None:  # never: a line has exactly the block its ``change`` names
            continue
        details = {
            "change": line.change,
            line.change: block.model_dump(mode="json"),
            "line": line.line,
            "at": line.at.isoformat(),
            "source": f"{SYSTEM_JOURNAL_SOURCE} line {number}",
        }
        events.append(AuditEvent(f"admin-system:{line.line}", SYSTEM_ACTION, "system", details))
    return events


def import_admin_events(
    sessions: sessionmaker[Session], admin: AdminLayout, report: ImportReport | None = None
) -> ImportReport:
    """The administration's journal lines into the audit log, each once (``audit.import``)."""
    report = report if report is not None else ImportReport()
    known = len(report.problems)
    import_events(sessions, journal_events(admin, report), report)
    import_events(sessions, system_events(admin, report), report)
    if len(report.problems) > known:
        log.warning("admin journal import: %s", "; ".join(report.problems[known:][:10]))
    return report


# -- the worker channel (contract 2.9) ------------------------------------------------------------


def mask_address(address: str) -> str:
    """``bora@ornek.example`` → ``b***@ornek.example`` (ADR-0010 A4; ``***`` when it is not an
    address)."""
    local, at, domain = address.strip().rpartition("@")
    if not at or not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


def mask_addresses(text: str) -> str:
    """Every e-mail address in ``text`` masked (an SMTP reply may quote the recipient)."""
    return _ADDRESS_IN_TEXT.sub(lambda match: mask_address(match.group()), text)


@dataclass
class RequestsReport:
    """What one ``admin.requests`` run did: ``<id> <kind> <state>`` per step."""

    steps: list[str] = field(default_factory=list)
    #: Requests are left for the next run (the time budget was used up).
    more: bool = False

    def details(self) -> dict[str, Any]:
        return {"steps": self.steps, "more": self.more}


def _requester(row: AdminRequest) -> dict[str, Any]:
    return {"user_id": row.requested_by_id, "username": row.requested_by}


def _claim(session: Session) -> AdminRequest | None:
    """The next open worker request no other worker holds (``FOR UPDATE SKIP LOCKED``): test
    e-mails first (a person waits for them), then the oldest."""
    return session.scalars(
        select(AdminRequest)
        .where(
            AdminRequest.channel == "worker",
            AdminRequest.kind.in_(WORKER_KINDS),
            AdminRequest.state.in_(OPEN_STATES),
        )
        .order_by(AdminRequest.kind != TEST_EMAIL, AdminRequest.created_at, AdminRequest.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    ).first()


def _finish(
    session: Session,
    row: AdminRequest,
    state: str,
    *,
    action: str,
    details: Mapping[str, Any],
    result: dict[str, Any] | None = None,
    code: str | None = None,
) -> None:
    """The request's final state and its audit entry, in the caller's transaction."""
    row.state = state
    row.terminal = state in TERMINAL_STATES
    row.result = result
    row.error_code = code
    audit = {**details, "request": row.id, "requested_by": _requester(row)}
    if code is not None:
        audit["code"] = code
    append_audit(session, action=action, details=audit)


def _masked(to: Any) -> str:
    return mask_address(to) if isinstance(to, str) else "***"


def _recipient_allowed(session: Session, row: AdminRequest, to: str) -> bool:
    """``to`` is one of ``alerts.email_recipients`` or the requester's own e-mail (contract 2.9;
    compared without regard to case)."""
    listed = get_setting(session, "alerts.email_recipients")
    allowed = {str(address).lower() for address in listed} if isinstance(listed, list) else set()
    if row.requested_by_id is not None:
        own = session.scalar(select(User.email).where(User.id == row.requested_by_id))
        if own:
            allowed.add(own.strip().lower())
    return to.lower() in allowed


def _test_email(session: Session, row: AdminRequest, config: Config) -> None:
    """Send the test e-mail of ``row`` and record the outcome with its ``smtp.test`` entry."""
    to = row.params.get("to")
    if (
        not isinstance(to, str)
        or not _ADDRESS.fullmatch(to)  # ``$`` alone would let a final newline through
        or not _recipient_allowed(session, row, to)
    ):
        masked = _masked(to)
        _finish(
            session,
            row,
            "failed",
            action=SMTP_TEST_ACTION,
            details={"to": masked, "ok": False, "smtp_code": None},
            result={"ok": False, "to": masked, "smtp_code": None, "error_text": None},
            code="validation_failed",
        )
        return
    masked = mask_address(to)
    web_url = web_base_url(get_setting(session, "alerts.web_base_url"), config)
    text: str | None
    code: str | None
    try:
        outcome = trial_email(config, to, web_url=web_url).send()
    except TrialEmailRefusedError as exc:
        log.warning("test e-mail request %s: %s", row.id, exc)  # the detail stays in this log
        ok, smtp_code, text = False, None, exc.text
        code = "smtp_not_configured" if exc.not_configured else "smtp_test_failed"
    else:
        ok, smtp_code, text = outcome.ok, outcome.smtp_code, outcome.error_text
        code = None if ok else "smtp_test_failed"
    _finish(
        session,
        row,
        "succeeded" if ok else "failed",
        action=SMTP_TEST_ACTION,
        details={"to": masked, "ok": ok, "smtp_code": smtp_code},
        result={
            "ok": ok,
            "to": masked,
            "smtp_code": smtp_code,
            "error_text": None if text is None else mask_addresses(text),
        },
        code=code,
    )


def lease_rebuild_range(params: Mapping[str, Any], *, now: datetime) -> tuple[date, date] | None:
    """``from`` … ``to`` of a lease rebuild request; ``None`` for a range the API does not
    accept either (more than :data:`LEASE_REBUILD_DAYS` days, reversed, in the future)."""
    try:
        first = date.fromisoformat(str(params["from"]))
        last = date.fromisoformat(str(params["to"]))
    except (KeyError, ValueError):
        return None
    if last < first or (last - first).days >= LEASE_REBUILD_DAYS or last > site_day(now):
        return None
    return first, last


def _lease_refused(session: Session, row: AdminRequest) -> None:
    _finish(
        session,
        row,
        "failed",
        action=LEASES_REBUILD_ACTION,
        details={"from": row.params.get("from"), "to": row.params.get("to")},
        result={"done_days": 0, "total_days": 0, "failed_days": []},
        code="validation_failed",
    )


def _progress(row: AdminRequest) -> tuple[int, list[str]]:
    """The days rebuilt and the days failed so far (``result`` of a running rebuild)."""
    progress = row.result or {}
    done = int(progress.get("done_days", 0))
    return done, [str(day) for day in progress.get("failed_days", [])]


def _lease_progress(
    session: Session, row: AdminRequest, first: date, last: date, done: int, failed: list[str]
) -> None:
    """Record the days worked on so far; after the last day the request ends (``succeeded``, or
    ``failed`` with ``lease_rebuild_failed``) with its ``leases.rebuild`` entry."""
    total = (last - first).days + 1
    result = {"done_days": done, "total_days": total, "failed_days": failed}
    if done + len(failed) < total:
        row.state = "running"
        row.result = result
        return
    _finish(
        session,
        row,
        "failed" if failed else "succeeded",
        action=LEASES_REBUILD_ACTION,
        details={
            "from": first.isoformat(),
            "to": last.isoformat(),
            "done_days": done,
            "failed_days": failed,
        },
        result=result,
        code="lease_rebuild_failed" if failed else None,
    )


def _lease_rebuild(
    sessions: sessionmaker[Session], session: Session, row: AdminRequest, *, now: datetime
) -> None:
    """One step of the lease rebuild of ``row``: start it, or rebuild its next day in that
    day's own transaction while the row stays locked. The last day finishes the request with
    its ``leases.rebuild`` entry."""
    span = lease_rebuild_range(row.params, now=now)
    if span is None:
        _lease_refused(session, row)
        return
    first, last = span
    if row.state == "queued":  # committed at once: "running" shows before the first day is done
        row.state = "running"
        row.result = {"done_days": 0, "total_days": (last - first).days + 1, "failed_days": []}
        return
    done, failed = _progress(row)
    day = first + timedelta(days=done + len(failed))
    if day <= last:
        outcome = rebuild_lease_day(
            sessions, day, lookahead=day == last, details={"request": row.id}
        )
        if outcome.ok:
            done += 1
        else:
            failed.append(day.isoformat())
            log.warning("lease rebuild request %s: %s %s", row.id, day, outcome.error_text)
    _lease_progress(session, row, first, last, done, failed)


def _expire(session: Session, row: AdminRequest) -> None:
    """A queued request whose time ran out: nothing was done (``request_expired``)."""
    details: dict[str, Any]
    if row.kind == TEST_EMAIL:
        details = {"to": _masked(row.params.get("to")), "ok": False}
        action = SMTP_TEST_ACTION
    else:
        details = {"from": row.params.get("from"), "to": row.params.get("to")}
        action = LEASES_REBUILD_ACTION
    _finish(session, row, "expired", action=action, details=details, code="request_expired")


def _work(
    sessions: sessionmaker[Session],
    session: Session,
    row: AdminRequest,
    config: Config,
    *,
    now: datetime,
) -> None:
    """One step of the claimed ``row``, in ``session``'s transaction."""
    if row.state == "queued" and row.expires_at is not None and now >= row.expires_at:
        _expire(session, row)
    elif row.kind == TEST_EMAIL:
        _test_email(session, row, config)
    else:
        _lease_rebuild(sessions, session, row, now=now)


def _record_error(
    sessions: sessionmaker[Session], request_id: str, error: str, *, now: datetime
) -> str:
    """After a step of ``request_id`` raised ``error`` (the exception's type; its transaction
    was rolled back), in a transaction of its own: a test e-mail fails with
    ``smtp_test_failed``, a lease rebuild counts the day it was at as failed and goes on with
    the next. The row is claimed again (``SKIP LOCKED``); one another worker holds by now, or
    one that has ended, is left alone. Returns the state, ``skipped`` for a row left alone."""
    with sessions() as session:
        row = session.scalars(
            select(AdminRequest)
            .where(AdminRequest.id == request_id, AdminRequest.state.in_(OPEN_STATES))
            .with_for_update(skip_locked=True)
        ).first()
        if row is None:
            return "skipped"
        if row.kind == TEST_EMAIL:
            masked = _masked(row.params.get("to"))
            _finish(
                session,
                row,
                "failed",
                action=SMTP_TEST_ACTION,
                details={"to": masked, "ok": False, "smtp_code": None},
                result={
                    "ok": False,
                    "to": masked,
                    "smtp_code": None,
                    "error_text": f"beklenmeyen hata / unexpected error: {error}",
                },
                code="smtp_test_failed",
            )
        elif (span := lease_rebuild_range(row.params, now=now)) is None:
            _lease_refused(session, row)
        else:
            first, last = span
            done, failed = _progress(row)
            failed.append((first + timedelta(days=done + len(failed))).isoformat())
            _lease_progress(session, row, first, last, done, failed)
        state = row.state
        session.commit()
    return state


def process_requests(
    sessions: sessionmaker[Session],
    config: Config,
    *,
    now: datetime,
    budget: timedelta = REQUESTS_BUDGET,
) -> RequestsReport:
    """``admin.requests``: the browser's test e-mails and lease rebuilds (``admin_requests``,
    ``channel = 'worker'``), one step per claimed row and transaction, until none is left or
    ``budget`` is used up. Several workers share the queue: a row another one holds is
    skipped, so no request is worked on twice at the same time. A step that raises is recorded
    as the request's failure (:func:`_record_error`) and the run goes on with the next row; a
    database that is gone stops the run (the job is retried)."""
    report = RequestsReport()
    started = time.monotonic()
    while time.monotonic() - started <= budget.total_seconds():
        error: str | None = None
        with sessions() as session:
            row = _claim(session)
            if row is None:
                return report
            request_id, kind = row.id, row.kind
            try:
                _work(sessions, session, row, config, now=now)
                state = row.state
                session.commit()
            except Exception as exc:  # recorded below: one request never holds up the others
                session.rollback()
                error = type(exc).__name__
        if error is not None:
            log.warning("admin request %s (%s) failed: %s", request_id, kind, error)
            state = _record_error(sessions, request_id, error, now=now)
        report.steps.append(f"{request_id} {kind} {state}")
    report.more = True
    return report
