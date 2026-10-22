"""``reports.run_due`` (every minute) and ``reports.purge`` (daily) — ADR-0012.

One run of ``reports.run_due``:

1. **Schedule.** Every enabled schedule with recipients whose ``next_run_at`` has passed gets
   **one** queued run for its newest occurrence (:func:`.schedule.due_run`) — after downtime the
   missed occurrences are not run one by one; with ``reports.catch_up`` false an occurrence more
   than 30 minutes late is skipped. ``next_run_at`` moves to the next occurrence after now. A
   schedule whose timing is not valid (stored before a rule refused it) is left out with an
   error and waits for an edit; it never stops the others.
2. **Render.** Queued runs (scheduled ones and "run now" from the API), oldest first, at most
   :data:`RENDERS_PER_TICK` per minute: the engine's report data for the period
   (``build_report_data``, read-only), the alerts of the period, the previous day's sealing
   status → the files of the schedule's formats and the e-mail, written to
   ``exports/usage-reports/<run>/`` with their SHA-256. A run whose rendering was interrupted (a
   crash) is rendered again, at most :data:`MAX_RENDER_ATTEMPTS` times.
3. **Deliver.** One e-mail per recipient through ``[smtp]``, with the alert e-mail's semantics
   (ADR-0010): the delivery is leased (attempt + retry time, committed) before sending and
   recorded after, so a crash in between repeats that e-mail once, never more; temporary
   failures are retried after 1, 5, 15, 60, 180 minutes; after 6 attempts, or at once for a 5xx
   refusal, the delivery is ``failed``. When the server itself fails, the other recipients wait
   for the next minute.
4. **Finish.** A run whose deliveries are all decided is ``sent`` (every e-mail went out, or
   none was due: ``send`` false), ``partial`` or ``failed`` (rendering failed, or no e-mail went
   out). A failure opens the local alert **R20-report** (medium; resolved once the recipients
   it names received a report again, and a later run rendered).

``reports.run_due`` belongs to the paid module ``usage_reports`` (licensing contract 1.4): the
scheduler skips it while the module is off, and when it goes off it resolves an active
**R20-report** with a note (:func:`resolve_while_unlicensed`), since no report can fail or be
repaired while the module is off.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_db.models import ReportRun, ReportSchedule
from arcilogger_db.settings import get_settings
from arcilogger_worker.alerts.mail import SmtpSender, SmtpSendError
from arcilogger_worker.alerts.notify import (
    MAX_ATTEMPTS,
    Sender,
    backoff,
    smtp_password,
    web_base_url,
)
from arcilogger_worker.alerts.store import SYSTEM, active_alert, open_alert, record_event
from arcilogger_worker.alerts.store import resolve as resolve_alert
from arcilogger_worker.reports import mail, store
from arcilogger_worker.reports.content import ReportBuilder, ReportMeta, build_document
from arcilogger_worker.reports.html import render_html
from arcilogger_worker.reports.pdf import render_pdf
from arcilogger_worker.reports.schedule import (
    Timing,
    TimingError,
    due_run,
    next_occurrence,
    period_for,
)
from arcilogger_worker.reports.xlsx import render_xlsx
from arcilogger_worker.usage.pseudonyms import PseudonymKeyMissingError, load_key

log = logging.getLogger("arcilogger_worker.reports")

REPORT_RULE: Final = "R20-report"
#: The note on an ``R20-report`` resolved because ``usage_reports`` is off (contract 7).
UNLICENSED_NOTE: Final = (
    "Zamanlanmış raporlar lisansta kapalı / Scheduled reports are off in the licence"
)
#: The reason code of that resolution (``alert_events.text`` of the state change).
UNLICENSED_REASON: Final = "module_not_licensed"
RENDERS_PER_TICK: Final = 2
MAX_RENDER_ATTEMPTS: Final = 3
#: A run still "rendering" this long after it was picked was interrupted (worker stopped).
RENDER_STALE: Final = timedelta(minutes=30)
ERROR_MAX: Final = 300
SMTP_DISABLED: Final = "[smtp] enabled = false: e-posta gönderilemiyor / cannot send e-mail"


@dataclass
class RunDueReport:
    scheduled: int = 0
    skipped: int = 0
    rendered: int = 0
    render_failed: int = 0
    emails: int = 0
    delivery_failed: int = 0
    finished: int = 0
    errors: list[str] = field(default_factory=list)

    def details(self) -> dict[str, Any]:
        return {
            "scheduled": self.scheduled,
            "skipped": self.skipped,
            "rendered": self.rendered,
            "render_failed": self.render_failed,
            "emails": self.emails,
            "delivery_failed": self.delivery_failed,
            "finished": self.finished,
            "errors": self.errors[:20],
        }


@dataclass(frozen=True)
class Policy:
    catch_up: bool
    max_attachment_mb: int
    web_url: str

    @classmethod
    def load(cls, session: Session, config: Config) -> Policy:
        values = get_settings(session, prefix="reports.")
        limit = values.get("reports.max_attachment_mb")
        alerts = get_settings(session, keys=["alerts.web_base_url"])
        return cls(
            catch_up=values.get("reports.catch_up") is not False,
            max_attachment_mb=limit if type(limit) is int and limit >= 1 else 15,
            web_url=web_base_url(alerts.get("alerts.web_base_url"), config),
        )


def timing_of(schedule: ReportSchedule) -> Timing:
    return Timing(schedule.kind, schedule.time, schedule.weekday, schedule.monthday)


def next_run(schedule: ReportSchedule, now: datetime) -> datetime | None:
    """``next_run_at`` of a schedule changed now (``None`` while it cannot run)."""
    if not schedule.enabled or not schedule.recipients:
        return None
    return next_occurrence(timing_of(schedule), now)


def queue_run(
    session: Session,
    schedule: ReportSchedule,
    *,
    start: datetime,
    end: datetime,
    kind: str,
    triggered_by: str,
    now: datetime,
    send: bool = True,
    requested_by: str | None = None,
    due_at: datetime | None = None,
    catch_up: bool = False,
) -> ReportRun:
    """A queued run with the schedule's settings as they are now (the caller commits)."""
    run = ReportRun(
        schedule_id=schedule.id,
        name=schedule.name,
        kind=kind,
        period_from=start,
        period_to=end,
        person_level=schedule.person_level,
        pseudonymize=schedule.pseudonymize,
        formats=dict(schedule.formats or {}),
        recipients=list(schedule.recipients or []),
        send=send,
        status="queued",
        triggered_by=triggered_by,
        requested_by=requested_by,
        due_at=due_at,
        catch_up=catch_up,
        started_at=now,
    )
    session.add(run)
    session.flush()
    return run


# -- 1. schedule ----------------------------------------------------------------------------------


def schedule_due(session: Session, policy: Policy, report: RunDueReport, *, now: datetime) -> None:
    schedules = session.scalars(
        select(ReportSchedule)
        .where(
            ReportSchedule.enabled.is_(True),
            ReportSchedule.next_run_at.is_not(None),
            ReportSchedule.next_run_at <= now,
        )
        .order_by(ReportSchedule.next_run_at, ReportSchedule.id)
        .with_for_update(skip_locked=True)
    ).all()
    for schedule in schedules:
        if schedule.next_run_at is None:  # pragma: no cover - selected above
            continue
        try:
            timing = timing_of(schedule)
        except TimingError as exc:
            # Stored before a rule refused it (e.g. end_of_day at 00:00, an empty period): it
            # waits for an edit (which must correct it); the other schedules go on.
            log.error("report schedule %s cannot run: %s", schedule.id, exc)
            report.errors.append(f"schedule {schedule.id}: {exc}"[:ERROR_MAX])
            schedule.next_run_at = None
            continue
        due = due_run(timing, schedule.next_run_at, now)
        if due is not None and schedule.recipients and (policy.catch_up or not due.catch_up):
            start, end = period_for(schedule.kind, due.occurrence)
            queue_run(
                session,
                schedule,
                start=start,
                end=end,
                kind=schedule.kind,
                triggered_by="schedule",
                now=now,
                due_at=due.occurrence,
                catch_up=due.catch_up,
            )
            report.scheduled += 1
            report.skipped += due.missed
        elif due is not None:
            report.skipped += due.missed + 1
            log.info("report schedule %s: occurrence %s skipped", schedule.id, due.occurrence)
        schedule.next_run_at = next_run(schedule, now)


# -- 2. render ------------------------------------------------------------------------------------


def _error_text(exc: BaseException) -> str:
    if isinstance(exc, NotImplementedError):
        return f"kullanım motoru yok / usage engine not installed: {exc}"[:ERROR_MAX]
    return f"{type(exc).__name__}: {exc}"[:ERROR_MAX]


def render_outputs(
    document: Mapping[str, Any], formats: Mapping[str, Any], stem: str
) -> list[store.Output]:
    """The files of the schedule's formats (the HTML file is the report without e-mail notes)."""
    outputs = []
    if formats.get("email_html"):
        outputs.append(store.Output(f"{stem}.html", "html", render_html(document).encode()))
    if formats.get("pdf"):
        outputs.append(store.Output(f"{stem}.pdf", "pdf", render_pdf(document).pdf))
    if formats.get("xlsx"):
        outputs.append(store.Output(f"{stem}.xlsx", "xlsx", render_xlsx(document)))
    return outputs


def _pick(sessions: sessionmaker[Session], now: datetime) -> int | None:
    """Lease the oldest queued run for rendering (committed), or ``None``."""
    with sessions() as session:
        stale = session.scalars(
            select(ReportRun)
            .where(
                ReportRun.status == "running",
                ReportRun.rendered_at.is_(None),
                ReportRun.picked_at < now - RENDER_STALE,
            )
            .with_for_update(skip_locked=True)
        ).all()
        for run in stale:  # interrupted while rendering: queue it again
            run.status = "queued"
        session.flush()
        queued = session.scalars(
            select(ReportRun)
            .where(ReportRun.status == "queued")
            .order_by(ReportRun.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        ).first()
        if queued is None:
            session.commit()
            return None
        queued.status = "running"
        queued.picked_at = now
        queued.render_attempts += 1
        session.commit()
        return queued.id


@dataclass(frozen=True)
class _Job:
    """What a leased run needs once its transaction is closed."""

    document: dict[str, Any]
    formats: dict[str, Any]
    stem: str


def _document(
    session: Session,
    layout: Layout,
    config: Config,
    policy: Policy,
    run: ReportRun,
    *,
    now: datetime,
    builder: ReportBuilder | None,
) -> _Job:
    """The run's document, read from the database (read-only)."""
    meta = ReportMeta(
        name=run.name,
        kind=run.kind,
        site_name=config.site_name,
        web_url=policy.web_url,
        generated_at=now,
        run_id=run.id,
        schedule_id=run.schedule_id,
    )
    key = None
    if run.pseudonymize:  # the worker's own secret store (its --config), as the API's
        key = load_key(config.secrets_dir)
        if key is None:
            raise PseudonymKeyMissingError("usage.pseudonym_key is not in the secret store")
    document = build_document(
        session,
        layout,
        meta=meta,
        start=run.period_from,
        end=run.period_to,
        person_level=run.person_level,
        pseudonymize=run.pseudonymize,
        builder=builder,
        pseudonym_key=key,
    )
    stem = store.file_stem(run.kind, run.period_from, run.period_to, run.id)
    return _Job(document, dict(run.formats or {}), stem)


def render_run(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    policy: Policy,
    run_id: int,
    report: RunDueReport,
    *,
    now: datetime,
    builder: ReportBuilder | None = None,
) -> None:
    """Render a leased run, store its files and e-mail, queue its deliveries."""
    job: _Job | None = None
    outputs: list[store.Output] = []
    error: str | None = None
    with sessions() as session:
        run = session.get(ReportRun, run_id)
        if run is None:  # pragma: no cover - deleted meanwhile
            return
        if run.render_attempts > MAX_RENDER_ATTEMPTS:
            error = f"rendering interrupted {MAX_RENDER_ATTEMPTS} times"
        else:
            try:
                job = _document(session, layout, config, policy, run, now=now, builder=builder)
            except Exception as exc:  # a broken report must not stop the other runs
                log.exception("report run %s: no report data", run_id)
                error = _error_text(exc)
        session.rollback()  # nothing was written; no transaction stays open while rendering
    if job is not None:
        try:
            outputs = render_outputs(job.document, job.formats, job.stem)
        except Exception as exc:  # a layout failure must not stop the other runs
            log.exception("report run %s could not be rendered", run_id)
            error, job = _error_text(exc), None
    summary: dict[str, Any] = {}
    files: list[dict[str, Any]] = []
    if job is not None:
        attachments = [o for o in outputs if o.format in ("pdf", "xlsx")]
        total = sum(len(o.data) for o in attachments)
        omitted = total > policy.max_attachment_mb * mail.MIB
        html, text = mail.render_mail(
            job.document,
            email_html=bool(job.formats.get("email_html")),
            attachments=[o.entry for o in attachments],
            omitted=omitted,
            limit_mb=policy.max_attachment_mb,
            run_id=run_id,
        )
        data = job.document["data"]
        summary = {
            "subject": mail.subject(job.document),
            "attachments": [] if omitted else [o.name for o in attachments],
            "attachments_omitted": omitted,
            "attachments_size": total,
            "persons": len(data.get("persons") or []),
            "departments": len(data.get("departments") or []),
            "alerts": int(job.document["alerts"].get("total") or 0),
            "seal": job.document["seal"].get("status"),
        }
        try:
            files = store.write_run(
                layout, run_id, outputs, {store.MAIL_HTML: html, store.MAIL_TEXT: text}
            )
        except OSError as exc:
            error = f"exports/{store.FOLDER}: {type(exc).__name__}: {exc}"[:ERROR_MAX]
    with sessions() as session:
        run = session.get(ReportRun, run_id, with_for_update=True)
        if run is None:  # pragma: no cover
            return
        if error is not None:
            run.status, run.error, run.finished_at = "failed", error, now
            report.render_failed += 1
            report.errors.append(f"run {run_id}: {error}")
            report_failed(session, run, recipient=None, error=error, now=now)
            session.commit()
            return
        run.files, run.summary, run.rendered_at, run.error = files, summary, now, None
        status = "queued" if run.send else "skipped"
        run.deliveries = [
            {
                "recipient": recipient,
                "status": status,
                "attempts": 0,
                "error": None,
                "sent_at": None,
                "next_attempt_at": None,
                "message_id": None,
            }
            for recipient in run.recipients or []
        ]
        report.rendered += 1
        render_works(session, now=now)
        finish(session, run, report, now=now)
        session.commit()


# -- 3. deliver -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Lease:
    run_id: int
    recipient: str
    message: Any


def _due(delivery: Mapping[str, Any], now: datetime) -> bool:
    if delivery.get("status") != "queued":
        return False
    retry = delivery.get("next_attempt_at")
    return retry is None or datetime.fromisoformat(str(retry)) <= now


def _attachments(layout: Layout, run: ReportRun) -> list[tuple[str, str, bytes]]:
    """``(name, format, bytes)`` of the files the run's e-mail carries (hash-checked)."""
    names = set((run.summary or {}).get("attachments") or [])
    found = []
    for entry in run.files or []:
        if entry.get("name") in names:
            _, data = store.read_file(layout, run, str(entry["name"]))
            found.append((str(entry["name"]), str(entry["format"]), data))
    return found


def _lease(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    report: RunDueReport,
    now: datetime,
) -> _Lease | None:
    """The next due delivery, leased (attempt + retry time committed), with its e-mail."""
    with sessions() as session:
        while True:
            runs = session.scalars(
                select(ReportRun)
                .where(ReportRun.status == "running", ReportRun.rendered_at.is_not(None))
                .order_by(ReportRun.id)
                .with_for_update(skip_locked=True)
            ).all()
            found = next(
                (
                    (run, index)
                    for run in runs
                    for index, item in enumerate(run.deliveries or [])
                    if _due(item, now)
                ),
                None,
            )
            if found is None:
                session.commit()
                return None
            run, index = found
            deliveries = [dict(item) for item in run.deliveries or []]
            delivery = deliveries[index]
            recipient = str(delivery["recipient"])
            delivery["attempts"] = int(delivery.get("attempts") or 0) + 1
            delivery["next_attempt_at"] = (now + backoff(delivery["attempts"])).isoformat()
            try:
                html, text = store.read_mail(layout, run.id)
                attachments = _attachments(layout, run)
            except (OSError, store.StoredFileError) as exc:
                # The files of the run are gone or changed: nothing to send, ever.
                error = f"report files: {exc}"[:ERROR_MAX]
                delivery.update(status="failed", error=error, next_attempt_at=None)
                run.deliveries = deliveries
                report.delivery_failed += 1
                report_failed(session, run, recipient=recipient, error=error, now=now)
                finish(session, run, report, now=now)
                session.commit()
                continue
            message = mail.build_message(
                subject_line=str((run.summary or {}).get("subject") or run.name),
                sender=str(config.smtp.from_address),
                recipient=recipient,
                html=html,
                text=text,
                attachments=attachments,
                now=now,
            )
            delivery["message_id"] = str(message["Message-ID"])
            run.deliveries = deliveries
            session.commit()
            return _Lease(run.id, recipient, message)


def _record(
    sessions: sessionmaker[Session],
    lease: _Lease,
    error: SmtpSendError | None,
    report: RunDueReport,
    now: datetime,
) -> None:
    with sessions() as session:
        run = session.get(ReportRun, lease.run_id, with_for_update=True)
        if run is None:  # pragma: no cover
            return
        deliveries = [dict(item) for item in run.deliveries or []]
        for delivery in deliveries:
            if delivery.get("recipient") != lease.recipient or delivery.get("status") != "queued":
                continue
            if error is None:
                delivery.update(status="sent", sent_at=now.isoformat(), error=None)
                delivery["next_attempt_at"] = None
                report.emails += 1
                recipient_works(session, lease.recipient, now=now)
            else:
                delivery["error"] = str(error)
                if error.permanent or int(delivery.get("attempts") or 0) >= MAX_ATTEMPTS:
                    delivery.update(status="failed", next_attempt_at=None)
                    report.delivery_failed += 1
                    report_failed(
                        session, run, recipient=lease.recipient, error=str(error), now=now
                    )
                report.errors.append(f"run {run.id} {lease.recipient}: {error}"[:ERROR_MAX])
        run.deliveries = deliveries
        finish(session, run, report, now=now)
        session.commit()


def deliver(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    sender: Sender,
    report: RunDueReport,
    *,
    now: datetime,
) -> None:
    while True:
        lease = _lease(sessions, layout, config, report, now)
        if lease is None:
            return
        error: SmtpSendError | None = None
        try:
            sender.send(lease.message, lease.recipient)
        except SmtpSendError as exc:
            error = exc
            log.warning("report e-mail to %s failed: %s", lease.recipient, exc)
        _record(sessions, lease, error, report, now)
        if error is not None and not error.recipient:
            # The server itself failed: the other recipients wait for the next run instead of
            # holding the worker loop for one timeout each.
            return


def fail_undeliverable(
    sessions: sessionmaker[Session], report: RunDueReport, error: str, *, now: datetime
) -> None:
    """``[smtp]`` is off: every waiting delivery fails at once (a configuration problem)."""
    with sessions() as session:
        runs = session.scalars(
            select(ReportRun)
            .where(ReportRun.status == "running", ReportRun.rendered_at.is_not(None))
            .with_for_update(skip_locked=True)
        ).all()
        for run in runs:
            deliveries = [dict(item) for item in run.deliveries or []]
            for delivery in deliveries:
                if delivery.get("status") == "queued":
                    delivery.update(status="failed", error=error, next_attempt_at=None)
                    report.delivery_failed += 1
                    report_failed(
                        session, run, recipient=str(delivery["recipient"]), error=error, now=now
                    )
            run.deliveries = deliveries
            finish(session, run, report, now=now)
        session.commit()


# -- 4. finish and the R20-report alert -----------------------------------------------------------


def finish(session: Session, run: ReportRun, report: RunDueReport, *, now: datetime) -> None:
    """Final status once no delivery waits any more (the caller commits)."""
    deliveries = run.deliveries or []
    if any(item.get("status") == "queued" for item in deliveries):
        return
    sent = sum(1 for item in deliveries if item.get("status") == "sent")
    failed = sum(1 for item in deliveries if item.get("status") == "failed")
    if failed and not sent:
        run.status = "failed"
        run.error = run.error or "e-posta gönderilemedi / no e-mail could be sent"
    elif failed:
        run.status = "partial"
    else:
        run.status = "sent"
    run.finished_at = now
    report.finished += 1


def report_failed(
    session: Session, run: ReportRun, *, recipient: str | None, error: str, now: datetime
) -> None:
    """Open or extend the one local ``R20-report`` alert."""
    existing = active_alert(session, REPORT_RULE)
    details = dict(existing.details or {}) if existing is not None else {}
    recipients = set(details.get("recipients") or [])
    if recipient is not None:
        recipients.add(recipient)
    runs = [int(item) for item in details.get("runs") or [] if isinstance(item, int)]
    runs = sorted({*runs, run.id})[-20:]
    render_failed = bool(details.get("render_failed")) or recipient is None
    what = f"alıcı {recipient}" if recipient else "rapor oluşturulamadı"
    open_alert(
        session,
        rule=REPORT_RULE,
        severity="medium",
        title=f"Kullanım raporu gönderilemedi (çalıştırma {run.id}, {what}): {error}"[:500],
        details={
            "recipients": sorted(recipients),
            "runs": runs,
            "render_failed": render_failed,
            "error": error[:ERROR_MAX],
            "title_en": f"Usage report could not be delivered (run {run.id})",
        },
        key=REPORT_RULE,
        time=now,
        now=now,
    )


def _resolve_if_clear(session: Session, *, reason: str, now: datetime, **changes: Any) -> None:
    existing = active_alert(session, REPORT_RULE)
    if existing is None:
        return
    details = {**(existing.details or {}), **changes}
    if details.get("recipients") or details.get("render_failed"):
        if details != (existing.details or {}):
            existing.details = details
        return
    existing.details = details
    resolve_alert(existing, reason=reason, now=now)


def resolve_while_unlicensed(session: Session, *, now: datetime) -> bool:
    """Resolve the active ``R20-report`` (with :data:`UNLICENSED_NOTE`) because the module
    ``usage_reports`` is off; ``True`` when there was one."""
    existing = active_alert(session, REPORT_RULE)
    if existing is None:
        return False
    resolve_alert(existing, reason=UNLICENSED_REASON, now=now, facts={"note": UNLICENSED_NOTE})
    record_event(
        session, existing, kind="note", by=SYSTEM, user_id=None, at=now, text=UNLICENSED_NOTE
    )
    return True


def recipient_works(session: Session, recipient: str, *, now: datetime) -> None:
    """A report e-mail reached ``recipient``: it no longer counts as failing."""
    existing = active_alert(session, REPORT_RULE)
    if existing is None:
        return
    left = sorted(set((existing.details or {}).get("recipients") or []) - {recipient})
    _resolve_if_clear(session, reason="report_sent", now=now, recipients=left)


def render_works(session: Session, *, now: datetime) -> None:
    """A run rendered: a rendering failure no longer counts."""
    if active_alert(session, REPORT_RULE) is not None:
        _resolve_if_clear(session, reason="report_rendered", now=now, render_failed=False)


def prune_alert(session: Session, *, now: datetime) -> None:
    """Recipients no schedule lists any more no longer count as failing."""
    existing = active_alert(session, REPORT_RULE)
    if existing is None:
        return
    listed: set[str] = set()
    for recipients in session.scalars(select(ReportSchedule.recipients)):
        listed |= set(recipients or [])
    failing = set((existing.details or {}).get("recipients") or [])
    if failing - listed:
        _resolve_if_clear(
            session, reason="recipients_removed", now=now, recipients=sorted(failing & listed)
        )


# -- the job --------------------------------------------------------------------------------------


@dataclass(frozen=True)
class _Failing:
    error: SmtpSendError

    def send(self, message: Any, recipient: str) -> None:
        raise self.error


def run_due(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    *,
    now: datetime,
    sender_factory: Callable[[Config], Sender] | None = None,
    builder: ReportBuilder | None = None,
) -> RunDueReport:
    """One run of ``reports.run_due`` (see the module docstring)."""
    report = RunDueReport()
    with sessions() as session:
        policy = Policy.load(session, config)
        schedule_due(session, policy, report, now=now)
        prune_alert(session, now=now)
        session.commit()
    for _ in range(RENDERS_PER_TICK):
        run_id = _pick(sessions, now)
        if run_id is None:
            break
        render_run(sessions, layout, config, policy, run_id, report, now=now, builder=builder)
    with sessions() as session:
        waiting = session.scalar(
            select(func.count())
            .select_from(ReportRun)
            .where(ReportRun.status == "running", ReportRun.rendered_at.is_not(None))
        )
    if not waiting:
        return report
    if not config.smtp.enabled:
        fail_undeliverable(sessions, report, SMTP_DISABLED, now=now)
        return report
    if sender_factory is not None:
        sender: Sender = sender_factory(config)
    else:
        try:
            sender = SmtpSender(config.smtp, smtp_password(config))
        except (SecretStoreError, OSError, ValueError) as exc:
            # Counted like a failed attempt of every due e-mail (retried, then R20-report).
            sender = _Failing(SmtpSendError(f"smtp.password: {exc}", permanent=False))
    deliver(sessions, layout, config, sender, report, now=now)
    return report


def purge_reports(
    sessions: sessionmaker[Session], layout: Layout, *, now: datetime
) -> store.PurgeReport:
    """``reports.purge``: the files of runs older than ``reports.keep_days``."""
    with sessions() as session:
        return store.purge(session, layout, now=now)
