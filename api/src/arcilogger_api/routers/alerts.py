"""``/api/v1/alerts`` — Güvenlik: alert list, detail, state workflow, notes, summary (spec 11,
12.3 page 8; docs/MVP-CONTRACTS.md section 14, ADR-0010 amendment).

``alerts.read`` for reads, ``alerts.manage`` for changes (both administrators only today,
docs/roles.md). Alerts are written by the worker (:mod:`arcilogger_worker.alerts`); this router
only reads them and changes their state through :mod:`arcilogger_worker.alerts.store` — the one
writer of ``alert_events`` and of the audit entries ``alert.state`` (from/to) and ``alert.note``
(the note's **length** only: a note may name people, the sealed audit log never holds it).

* ``GET /alerts`` — filters ``state``, ``severity``, ``rule`` (each repeatable or comma
  separated), ``q`` (part of the title, the rule id or the source name; Turkish letters
  folded), ``from`` / ``to`` (``time``, from inclusive, to exclusive); ``sort`` ``time``
  (default) | ``created_at`` | ``updated_at`` | ``severity`` | ``state`` | ``rule``, ``order``
  ``desc`` (default) | ``asc``; ``limit`` 1-200, ``offset``.
* ``GET /alerts/summary`` — open alerts per severity, the number acknowledged, the 5 newest
  active ones and the mean time to close of the last 30 days (from the closing events).
* ``GET /alerts/{id}`` — the alert with its sanitised details (:func:`sanitize`), its events
  (oldest first), the runbook (:func:`arcilogger_worker.alerts.texts.runbook`) and its e-mail
  deliveries with masked recipients.
* ``POST /alerts/{id}/state`` ``{state, note?}`` — the workflow of
  :data:`arcilogger_worker.alerts.store.TRANSITIONS`; 409 ``alert_state_unchanged``,
  ``alert_transition_not_allowed`` or ``alert_key_active`` (re-open while another active alert
  holds the same dedup key; ``details.alert_id`` names it).
* ``POST /alerts/{id}/notes`` ``{text}`` (1-2000 characters) — 201 with the new event.
* Wave 6 (contract §2.6.2, §2.6.3): ``assignee=me|none|<user id>`` filter; every alert carries
  ``assignee`` and ``evidence_total``; ``POST /alerts/{id}/assign`` ``{user_id}`` (only users
  with ``alerts.read``; audit ``alert.assign``; 422 ``alert_assignee_invalid``);
  ``GET /alerts/{id}/evidence`` (the security rule's evidence rows with their raw references,
  never line content); and the summary's ``by_day``, ``top_rules`` and ``top_keys``.

Rule names, summaries and runbooks come from the catalogue
(:mod:`arcilogger_worker.alerts.texts`), never from the database. E-mail addresses are masked
in every title, summary and detail text (:func:`mask_addresses`). Reads are not audited (like
the lookup list).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from contextlib import suppress
from datetime import date, datetime, timedelta
from typing import Annotated, Any, Final, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import AfterValidator, AwareDatetime, BaseModel, Field, StringConstraints
from sqlalchemy import ColumnElement, Select, case, extract, func, or_, select
from sqlalchemy.dialects.postgresql import distinct_on
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.schemas import StrictModel
from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip, permissions_for, require
from arcilogger_api.textfold import fold, folded
from arcilogger_core.timeutil import SITE_TZ, site_day, utc_now
from arcilogger_db.models import (
    ALERT_NOTE_MAX,
    ALERT_SEVERITIES,
    ALERT_STATES,
    Alert,
    AlertDelivery,
    AlertEvidence,
    User,
)
from arcilogger_db.models import AlertEvent as AlertEventRow
from arcilogger_worker.alerts.store import (
    ACTIVE_STATES,
    AUTO_RESOLVED,
    CLOSED_STATES,
    TRANSITIONS,
    AuditHook,
    WorkflowError,
    add_note,
    assign,
    change_state,
    clean_note,
)
from arcilogger_worker.alerts.texts import facts, rule_text, runbook

router = APIRouter(prefix="/alerts", tags=["alerts"])

ReaderDep = Annotated[CurrentUser, Depends(require("alerts.read"))]
ManagerDep = Annotated[CurrentUser, Depends(require("alerts.manage"))]
MAX_ID: Final = 2**63 - 1
#: Deepest page (``offset``); larger values answer 422, never a slow scan.
MAX_OFFSET: Final = 1_000_000
#: Values of one repeatable filter.
MAX_FILTER_VALUES: Final = 20
#: Newest active alerts in the summary and on the dashboard.
LATEST: Final = 5
#: Window of the mean time to close.
CLOSE_WINDOW: Final = timedelta(days=30)
RULE_PATTERN: Final = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")
#: The summary's day counts (site days) and its top lists (days, entries).
BY_DAY_DAYS: Final = 30
TOP_DAYS: Final = 7
TOP_MAX: Final = 10

Severity = Literal["info", "low", "medium", "high", "critical"]
State = Literal["open", "acknowledged", "resolved", "dismissed"]
SortKey = Literal["time", "created_at", "updated_at", "severity", "state", "rule"]
SortOrder = Literal["asc", "desc"]

#: The web's names of the states (spec 11), for the error messages.
STATE_NAMES: Final[Mapping[str, tuple[str, str]]] = {
    "open": ("Yeni", "New"),
    "acknowledged": ("İnceleniyor", "Investigating"),
    "resolved": ("Kapandı", "Closed"),
    "dismissed": ("Yanlış alarm", "False alarm"),
}

MESSAGES: Final = {
    "alert_not_found": ("Uyarı bulunamadı.", "Alert not found."),
    "alert_state_unchanged": (
        "Uyarı zaten '{state_tr}' durumunda; sayfayı yenileyin.",
        "The alert is already '{state_en}'; reload the page.",
    ),
    "alert_transition_not_allowed": (
        "'{from_tr}' durumundaki bir uyarı '{to_tr}' yapılamaz.",
        "An alert that is '{from_en}' cannot be set to '{to_en}'.",
    ),
    "alert_assignee_invalid": (
        "Bu kullanıcıya uyarı atanamaz.",
        "Alerts cannot be assigned to this user.",
    ),
    "alert_key_active": (
        "Aynı sorun için etkin bir uyarı var (#{other}): sorun sürdüğü için denetim yeni bir "
        "uyarı açtı. Bu uyarı yeniden açılamaz; etkin uyarıyı kullanın.",
        "An active alert covers the same problem (#{other}): the check opened a new one because "
        "the problem persists. This alert cannot be re-opened; use the active one.",
    ),
}


def _error(status: int, code: str, *, details: Any = None, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(
        status, code, text_tr.format(**params), text_en.format(**params), details=details
    )


# -- response and request models ------------------------------------------------------------------


class AssigneeOut(BaseModel):
    id: int
    username: str


class AlertOut(BaseModel):
    id: int
    #: When the problem happened (an imported alert keeps the time of its line).
    time: datetime
    #: When ARCILOGGER recorded it.
    created_at: datetime
    updated_at: datetime
    resolved_at: datetime | None
    rule: str
    rule_name_tr: str
    rule_name_en: str
    severity: Severity
    state: State
    title: str
    #: One sentence from the alert's checked facts (else the title / its English twin).
    summary_tr: str
    summary_en: str
    #: Resolved by its check (not by a person).
    auto_resolved: bool
    notes_count: int
    #: The user the alert is given to (Wave 6 §2.6.2), or null.
    assignee: AssigneeOut | None
    #: A security rule's alert: the rows behind it (``details.evidence_total``), else null.
    evidence_total: int | None


class AlertEvent(BaseModel):
    id: int
    #: ``assign``: ``text`` is the new assignee's user name, null for nobody.
    kind: Literal["state", "note", "assign"]
    from_state: State | None
    to_state: State | None
    #: The note, or the reason code of an automatic change (``by`` = ``system``).
    text: str | None
    #: User name, or ``system``.
    by: str
    at: datetime


class AlertDeliveryOut(BaseModel):
    kind: Literal["new", "resolved"]
    status: Literal["queued", "sent", "failed", "suppressed"]
    attempts: int
    sent_at: datetime | None
    #: ``a***@firma.com.tr``: the first letter and the domain only.
    recipient_masked: str


class AlertDetail(AlertOut):
    #: The alert's details without secrets and without very long values (:func:`sanitize`).
    details: dict[str, Any]
    #: Oldest first.
    events: list[AlertEvent]
    runbook_tr: str
    runbook_en: str
    deliveries: list[AlertDeliveryOut]


class AlertPage(BaseModel):
    items: list[AlertOut]
    total: int
    limit: int
    offset: int


class OpenBySeverity(BaseModel):
    critical: int
    high: int
    medium: int
    low: int
    info: int


class DayCounts(BaseModel):
    #: A site day (Europe/Istanbul).
    day: date
    critical: int
    high: int
    medium: int
    low: int
    info: int


class RuleCount(BaseModel):
    rule: str
    count: int


class KeyCount(BaseModel):
    rule: str
    #: The alert's dedup key (``R01:172.20.41.17``).
    key: str
    count: int


class AlertSummary(BaseModel):
    #: Alerts in the state ``open`` (Yeni), per severity.
    open_by_severity: OpenBySeverity
    acknowledged: int
    #: The newest open or acknowledged alerts (at most 5).
    latest: list[AlertOut]
    #: Mean seconds from ``created_at`` to closing (resolved or dismissed, by a person or a
    #: check) of the alerts closed within the last 30 days; ``null`` when there is none.
    mean_time_to_close_seconds_30d: int | None
    #: Alerts per site day of the last 30, per severity (by ``time``), oldest first.
    by_day: list[DayCounts]
    #: The rules and keys with the most alerts of the last 7 days (at most 10 each).
    top_rules: list[RuleCount]
    top_keys: list[KeyCount]


#: Trimmed, 1-2000 characters, no control characters but tab and line breaks.
NoteText = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=ALERT_NOTE_MAX),
    AfterValidator(clean_note),
]


class AlertStateRequest(StrictModel):
    """Set the alert to ``state``; ``note`` is stored as a note event with it."""

    state: State
    note: NoteText | None = None


class AlertAssignRequest(StrictModel):
    """Give the alert to a user (``null``: to nobody)."""

    user_id: Annotated[int, Field(ge=1, le=MAX_ID)] | None


class AlertEvidenceRaw(BaseModel):
    """What ``GET /api/v1/raw`` opens (with its own permission and audit)."""

    source: str
    day: date
    line: int
    offset: int


class AlertEvidenceOut(BaseModel):
    n: int
    table: str
    row_id: int | None
    time: datetime | None
    time_received: datetime | None
    #: Null for a ``devices`` row (no raw line).
    raw: AlertEvidenceRaw | None


class AlertEvidencePage(BaseModel):
    alert_id: int
    #: Rows behind the alert in all; ``items`` holds at most ``rules.evidence_max``.
    evidence_total: int | None
    items: list[AlertEvidenceOut]


class AlertNoteRequest(StrictModel):
    """A note: 1-2000 characters (trimmed), line breaks allowed."""

    text: NoteText


# -- presentation ---------------------------------------------------------------------------------


def _summary(alert: Alert) -> tuple[str, str]:
    details = alert.details or {}
    found = facts(alert.rule, details)
    if found is not None:
        return found
    english = details.get("title_en")
    name_en = rule_text(alert.rule).name_en
    return alert.title, english if isinstance(english, str) and english else name_en


def _evidence_total(alert: Alert) -> int | None:
    value = (alert.details or {}).get("evidence_total")
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def alert_out(alert: Alert, notes: int, assignee: AssigneeOut | None) -> AlertOut:
    """The alert as the web shows it; e-mail addresses in the title and the summary are masked
    like the details' (``R20-email`` names the refused recipient)."""
    text = rule_text(alert.rule)
    summary_tr, summary_en = (mask_addresses(part) for part in _summary(alert))
    return AlertOut(
        id=alert.id,
        time=alert.time,
        created_at=alert.created_at,
        updated_at=alert.updated_at,
        resolved_at=alert.resolved_at,
        rule=alert.rule,
        rule_name_tr=text.name_tr,
        rule_name_en=text.name_en,
        severity=alert.severity,
        state=alert.state,
        title=mask_addresses(alert.title),
        summary_tr=summary_tr,
        summary_en=summary_en,
        auto_resolved=alert.state == "resolved" and AUTO_RESOLVED in (alert.details or {}),
        notes_count=notes,
        assignee=assignee,
        evidence_total=_evidence_total(alert),
    )


def assignees(db: Session, alerts: Iterable[Alert]) -> dict[int, AssigneeOut]:
    """The assignees of ``alerts`` by user id."""
    wanted = sorted({alert.assignee_id for alert in alerts if alert.assignee_id is not None})
    if not wanted:
        return {}
    rows = db.execute(select(User.id, User.username).where(User.id.in_(wanted)))
    return {user_id: AssigneeOut(id=user_id, username=name) for user_id, name in rows}


def notes_counts(db: Session, ids: Iterable[int]) -> dict[int, int]:
    wanted = sorted(set(ids))
    if not wanted:
        return {}
    rows = db.execute(
        select(AlertEventRow.alert_id, func.count())
        .where(AlertEventRow.alert_id.in_(wanted), AlertEventRow.kind == "note")
        .group_by(AlertEventRow.alert_id)
    ).all()
    return {alert_id: int(count) for alert_id, count in rows}


def alert_outs(db: Session, alerts: list[Alert]) -> list[AlertOut]:
    counts = notes_counts(db, (alert.id for alert in alerts))
    users = assignees(db, alerts)
    return [
        alert_out(
            alert,
            counts.get(alert.id, 0),
            users.get(alert.assignee_id) if alert.assignee_id is not None else None,
        )
        for alert in alerts
    ]


def event_out(row: AlertEventRow) -> AlertEvent:
    return AlertEvent(
        id=row.id,
        kind=row.kind,
        from_state=row.from_state,
        to_state=row.to_state,
        text=row.text,
        by=row.actor,
        at=row.at,
    )


def mask_recipient(address: str) -> str:
    """``tolga@firma.com.tr`` → ``t***@firma.com.tr`` (``***`` when it is not an address)."""
    local, at, domain = address.strip().rpartition("@")
    if not at or not local or not domain:
        return "***"
    return f"{local[0]}***@{domain}"


#: Keys whose values are never shown (a check never stores such a value; this is the guard
#: should one ever arrive through ``alerts.jsonl``).
_SECRET_KEY: Final = re.compile(
    r"pass(word|wd|phrase)?$|^pwd$|secret|credential|private.?key|api.?key|access.?key|"
    r"^auth(orization)?$|cookie|csrf|bearer|(^|_)token$|session.?(id|token)",
    re.IGNORECASE,
)
#: ``password=…`` and the like inside a text value ("token: …" is left alone: the sealing's
#: error texts speak of the time-stamp token).
_SECRET_TEXT: Final = re.compile(
    r"(?i)\b(password|passwd|pwd|secret|api[_-]?key)\b(\s*[=:]\s*)(\S+)"
)
#: A text value that is an e-mail address (``R20-email`` lists its recipients): masked like
#: the deliveries when its domain (the group) holds a dot between two characters.
_EMAIL: Final = re.compile(r"[^@\s]+@([^@\s]+)")
#: A character of an e-mail address inside a text: not a space, bracket, quote or separator.
_ADDRESS_CHAR: Final = r"[^@\s<>()\[\]{}'\",;:=]"
#: An e-mail address inside a text (a title naming the recipient, an SMTP reply echoing it). It
#: starts where a word starts, so a long text without one is scanned once.
_EMAIL_IN_TEXT: Final = re.compile(
    rf"(?<!{_ADDRESS_CHAR}){_ADDRESS_CHAR}+@{_ADDRESS_CHAR}+\.{_ADDRESS_CHAR}+"
)
REDACTED: Final = "[gizlendi / redacted]"
#: Longest text value shown, longest list, deepest nesting.
DETAIL_TEXT_MAX: Final = 1000
DETAIL_LIST_MAX: Final = 100
DETAIL_DEPTH_MAX: Final = 6


def mask_addresses(text: str) -> str:
    """Every e-mail address in ``text`` masked (:func:`mask_recipient`)."""
    whole = _EMAIL.fullmatch(text.strip())
    if whole is not None and "." in whole.group(1)[1:-1]:
        return mask_recipient(text)
    return _EMAIL_IN_TEXT.sub(lambda match: mask_recipient(match.group(0)), text)


def sanitize(value: Any, depth: int = 0) -> Any:
    """``details`` as the web may show them: secret-looking keys and ``key=value`` pairs
    redacted, e-mail addresses masked also inside texts (:func:`mask_addresses`), texts cut at
    :data:`DETAIL_TEXT_MAX` characters, lists at :data:`DETAIL_LIST_MAX` items, nesting at
    :data:`DETAIL_DEPTH_MAX` levels."""
    if depth > DETAIL_DEPTH_MAX:
        return "…"
    if isinstance(value, dict):
        return {
            str(key): REDACTED if _SECRET_KEY.search(str(key)) else sanitize(item, depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        items = [sanitize(item, depth + 1) for item in value[:DETAIL_LIST_MAX]]
        if len(value) > DETAIL_LIST_MAX:
            items.append(f"… (+{len(value) - DETAIL_LIST_MAX})")
        return items
    if isinstance(value, str):
        text = _SECRET_TEXT.sub(lambda m: f"{m.group(1)}{m.group(2)}***", mask_addresses(value))
        if len(text) > DETAIL_TEXT_MAX:
            return f"{text[:DETAIL_TEXT_MAX]}… (+{len(text) - DETAIL_TEXT_MAX})"
        return text
    return value


def alert_detail(db: Session, alert: Alert) -> AlertDetail:
    events = list(
        db.scalars(
            select(AlertEventRow)
            .where(AlertEventRow.alert_id == alert.id)
            .order_by(AlertEventRow.at, AlertEventRow.id)
        )
    )
    deliveries = db.scalars(
        select(AlertDelivery)
        .where(AlertDelivery.alert_id == alert.id)
        .order_by(AlertDelivery.created_at, AlertDelivery.id)
    )
    steps_tr, steps_en = runbook(alert.rule)
    users = assignees(db, [alert])
    assignee = users.get(alert.assignee_id) if alert.assignee_id is not None else None
    base = alert_out(alert, sum(1 for event in events if event.kind == "note"), assignee)
    return AlertDetail(
        **base.model_dump(),
        details=sanitize(dict(alert.details or {})),
        events=[event_out(event) for event in events],
        runbook_tr=steps_tr,
        runbook_en=steps_en,
        deliveries=[
            AlertDeliveryOut(
                kind=row.kind,
                status=row.status,
                attempts=row.attempts,
                sent_at=row.sent_at,
                recipient_masked=mask_recipient(row.recipient),
            )
            for row in deliveries
        ],
    )


# -- summary (also the dashboard's alert box) -----------------------------------------------------


def open_by_severity(db: Session) -> OpenBySeverity:
    counts = dict.fromkeys(ALERT_SEVERITIES, 0)
    for severity, count in db.execute(
        select(Alert.severity, func.count()).where(Alert.state == "open").group_by(Alert.severity)
    ):
        counts[severity] = int(count)
    return OpenBySeverity(**counts)


def latest_active(db: Session, limit: int = LATEST) -> list[AlertOut]:
    rows = list(
        db.scalars(
            select(Alert)
            .where(Alert.state.in_(ACTIVE_STATES))
            .order_by(Alert.time.desc(), Alert.id.desc())
            .limit(limit)
        )
    )
    return alert_outs(db, rows)


def mean_time_to_close(db: Session, now: datetime) -> int | None:
    """Mean seconds from ``created_at`` to the newest closing event (a change from an active to
    a closed state — a later "Kapandı" ↔ "Yanlış alarm" is no closing), over the alerts that
    are closed now and whose newest closing event lies within :data:`CLOSE_WINDOW`."""
    closes = (
        select(AlertEventRow.alert_id, AlertEventRow.at)
        .where(
            AlertEventRow.kind == "state",
            AlertEventRow.from_state.in_(ACTIVE_STATES),
            AlertEventRow.to_state.in_(CLOSED_STATES),
            AlertEventRow.at >= now - CLOSE_WINDOW,
            AlertEventRow.at <= now,
        )
        .order_by(AlertEventRow.alert_id, AlertEventRow.at.desc(), AlertEventRow.id.desc())
        .ext(distinct_on(AlertEventRow.alert_id))
        .subquery()
    )
    seconds = func.greatest(extract("epoch", closes.c.at - Alert.created_at), 0)
    mean = db.scalar(
        select(func.avg(seconds))
        .select_from(closes)
        .join(Alert, Alert.id == closes.c.alert_id)
        .where(Alert.state.in_(CLOSED_STATES))
    )
    return None if mean is None else round(float(mean))


# -- list -----------------------------------------------------------------------------------------


def _values(raw: list[str] | None, name: str, allowed: Iterable[str] | None = None) -> list[str]:
    """A repeatable filter: ``?state=open&state=acknowledged`` or ``?state=open,acknowledged``."""
    parts = (part.strip() for item in raw or [] for part in item.split(","))
    values = sorted({part for part in parts if part})
    known = set(allowed) if allowed is not None else None
    bad = (
        len(values) > MAX_FILTER_VALUES
        or (known is not None and any(value not in known for value in values))
        or (known is None and any(not RULE_PATTERN.match(value) for value in values))
    )
    if bad:
        raise ApiError(
            422,
            "validation_error",
            details=[{"loc": ["query", name], "type": "value_error", "msg": "unknown value"}],
        )
    return values


def _search(text: str) -> ColumnElement[bool]:
    needle = fold(text)
    source = func.coalesce(func.jsonb_extract_path_text(Alert.details, "source"), "")
    return or_(
        folded(Alert.title).contains(needle, autoescape=True),
        folded(Alert.rule).contains(needle, autoescape=True),
        folded(source).contains(needle, autoescape=True),
    )


def _rank(column: Any, order: Iterable[str]) -> ColumnElement[int]:
    return case({value: rank for rank, value in enumerate(order)}, value=column, else_=-1)


def _ordered(query: Select[Alert], sort: SortKey, order: SortOrder) -> Select[Alert]:
    """Sorted by ``sort`` (severity and state in their natural order), then by time and id in
    the same direction."""
    column: ColumnElement[Any]
    if sort == "severity":
        column = _rank(Alert.severity, ALERT_SEVERITIES)
    elif sort == "state":
        column = _rank(Alert.state, ALERT_STATES)
    else:
        column = getattr(Alert, sort)
    keys = (column, Alert.time, Alert.id)
    return query.order_by(*(key.desc() if order == "desc" else key.asc() for key in keys))


@router.get("")
def list_alerts(
    db: DbSession,
    user: ReaderDep,
    state: Annotated[list[str] | None, Query()] = None,
    severity: Annotated[list[str] | None, Query()] = None,
    rule: Annotated[list[str] | None, Query()] = None,
    assignee: Annotated[str | None, Query(pattern=r"^(me|none|[1-9][0-9]{0,18})$")] = None,
    q: Annotated[str | None, Query(max_length=200)] = None,
    time_from: Annotated[AwareDatetime | None, Query(alias="from")] = None,
    time_to: Annotated[AwareDatetime | None, Query(alias="to")] = None,
    sort: SortKey = "time",
    order: SortOrder = "desc",
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0, le=MAX_OFFSET)] = 0,
) -> AlertPage:
    """One page of alerts (``total`` = all alerts matching the filters)."""
    if time_from is not None and time_to is not None and time_from >= time_to:
        raise ApiError(422, "validation_error", details=[{"loc": ["query", "to"]}])
    query = select(Alert)
    states = _values(state, "state", ALERT_STATES)
    severities = _values(severity, "severity", ALERT_SEVERITIES)
    rules = _values(rule, "rule")
    if states:
        query = query.where(Alert.state.in_(states))
    if severities:
        query = query.where(Alert.severity.in_(severities))
    if rules:
        query = query.where(Alert.rule.in_(rules))
    if assignee == "me":
        query = query.where(Alert.assignee_id == user.id)
    elif assignee == "none":
        query = query.where(Alert.assignee_id.is_(None))
    elif assignee is not None:
        query = query.where(Alert.assignee_id == int(assignee))
    if q and q.strip():
        query = query.where(_search(q.strip()))
    if time_from is not None:
        query = query.where(Alert.time >= time_from)
    if time_to is not None:
        query = query.where(Alert.time < time_to)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = list(db.scalars(_ordered(query, sort, order).limit(limit).offset(offset)))
    return AlertPage(items=alert_outs(db, rows), total=total, limit=limit, offset=offset)


@router.get("/summary")
def summary(db: DbSession, user: ReaderDep) -> AlertSummary:
    """Counts for the Security page's header and the dashboard."""
    acknowledged = db.scalar(
        select(func.count()).select_from(Alert).where(Alert.state == "acknowledged")
    )
    now = utc_now()
    return AlertSummary(
        open_by_severity=open_by_severity(db),
        acknowledged=int(acknowledged or 0),
        latest=latest_active(db),
        mean_time_to_close_seconds_30d=mean_time_to_close(db, now),
        by_day=by_day(db, now),
        top_rules=top_rules(db, now),
        top_keys=top_keys(db, now),
    )


def by_day(db: Session, now: datetime, days: int = BY_DAY_DAYS) -> list[DayCounts]:
    """Alerts per site day of the last ``days`` (today included), per severity, by ``time``."""
    today = site_day(now)
    first = today - timedelta(days=days - 1)
    start = datetime.combine(first, datetime.min.time(), tzinfo=SITE_TZ)
    local_day = func.date(func.timezone(SITE_TZ.key, Alert.time))
    counts: dict[date, dict[str, int]] = {
        first + timedelta(days=n): dict.fromkeys(ALERT_SEVERITIES, 0) for n in range(days)
    }
    for day, severity, count in db.execute(
        select(local_day, Alert.severity, func.count())
        .where(Alert.time >= start, Alert.time <= now)
        .group_by(local_day, Alert.severity)
    ):
        if day in counts:
            counts[day][severity] = int(count)
    return [DayCounts(day=day, **values) for day, values in sorted(counts.items())]


def top_rules(db: Session, now: datetime) -> list[RuleCount]:
    """The rules with the most alerts of the last 7 days (by ``time``)."""
    count = func.count().label("count")
    rows = db.execute(
        select(Alert.rule, count)
        .where(Alert.time >= now - timedelta(days=TOP_DAYS), Alert.time <= now)
        .group_by(Alert.rule)
        .order_by(count.desc(), Alert.rule)
        .limit(TOP_MAX)
    )
    return [RuleCount(rule=rule, count=int(number)) for rule, number in rows]


def top_keys(db: Session, now: datetime) -> list[KeyCount]:
    """The dedup keys with the most alerts of the last 7 days (alerts without a key are not
    counted)."""
    count = func.count().label("count")
    rows = db.execute(
        select(Alert.rule, Alert.dedup_key, count)
        .where(
            Alert.time >= now - timedelta(days=TOP_DAYS),
            Alert.time <= now,
            Alert.dedup_key.is_not(None),
        )
        .group_by(Alert.rule, Alert.dedup_key)
        .order_by(count.desc(), Alert.dedup_key)
        .limit(TOP_MAX)
    )
    return [
        KeyCount(rule=rule, key=mask_addresses(key or ""), count=int(number))
        for rule, key, number in rows
    ]


# -- one alert ------------------------------------------------------------------------------------

AlertId = Annotated[int, Path(ge=1, le=MAX_ID)]


def _alert(db: Session, alert_id: int, *, lock: bool = False) -> Alert:
    row = db.get(Alert, alert_id, with_for_update=lock)
    if row is None:
        raise _error(404, "alert_not_found")
    return row


@router.get("/{alert_id}")
def get_alert(alert_id: AlertId, db: DbSession, user: ReaderDep) -> AlertDetail:
    """The alert with its details, events, runbook and e-mail deliveries."""
    return alert_detail(db, _alert(db, alert_id))


@router.get("/{alert_id}/evidence")
def get_evidence(alert_id: AlertId, db: DbSession, user: ReaderDep) -> AlertEvidencePage:
    """The rows behind a security rule's alert, oldest first, with what ``/raw`` opens (no line
    content; an alert without evidence answers an empty list)."""
    alert = _alert(db, alert_id)
    rows = db.scalars(
        select(AlertEvidence).where(AlertEvidence.alert_id == alert.id).order_by(AlertEvidence.n)
    )
    items = []
    for row in rows:
        raw = None
        if (
            row.source is not None
            and row.raw_day is not None
            and row.raw_line is not None
            and row.raw_offset is not None
        ):
            raw = AlertEvidenceRaw(
                source=row.source, day=row.raw_day, line=row.raw_line, offset=row.raw_offset
            )
        items.append(
            AlertEvidenceOut(
                n=row.n,
                table=row.tbl,
                row_id=row.row_id,
                time=row.time,
                time_received=row.time_received,
                raw=raw,
            )
        )
    return AlertEvidencePage(alert_id=alert.id, evidence_total=_evidence_total(alert), items=items)


def _workflow_error(exc: WorkflowError, previous: str, wanted: str) -> ApiError:
    names = {
        "state_tr": STATE_NAMES[wanted][0],
        "state_en": STATE_NAMES[wanted][1],
        "from_tr": STATE_NAMES[previous][0],
        "from_en": STATE_NAMES[previous][1],
        "to_tr": STATE_NAMES[wanted][0],
        "to_en": STATE_NAMES[wanted][1],
    }
    if exc.code == "unchanged":
        return _error(409, "alert_state_unchanged", details={"state": wanted}, **names)
    if exc.code == "not_allowed":
        return _error(
            409,
            "alert_transition_not_allowed",
            details={"from": previous, "to": wanted, "allowed": sorted(TRANSITIONS[previous])},
            **names,
        )
    return _error(409, "alert_key_active", details={"alert_id": exc.other}, other=exc.other or "?")


def _auditor(db: Session, request: Request, user: CurrentUser, alert: Alert) -> AuditHook:
    ip = client_ip(request)

    def audit(action: str, details: Mapping[str, Any]) -> None:
        record_audit(
            db, actor=user, action=action, target=f"alert:{alert.id}", details=details, ip=ip
        )

    return audit


@router.post("/{alert_id}/state")
def set_state(
    alert_id: AlertId, body: AlertStateRequest, request: Request, db: DbSession, user: ManagerDep
) -> AlertDetail:
    """Change the state (Yeni, İnceleniyor, Kapandı, Yanlış alarm), optionally with a note."""
    alert = _alert(db, alert_id, lock=True)
    previous = alert.state
    try:
        change_state(
            db,
            alert,
            body.state,
            by=user.username,
            user_id=user.id,
            now=utc_now(),
            audit=_auditor(db, request, user, alert),
            note=body.note,
        )
    except WorkflowError as exc:
        raise _workflow_error(exc, previous, body.state) from None
    return alert_detail(db, alert)


@router.post("/{alert_id}/notes", status_code=201)
def create_note(
    alert_id: AlertId, body: AlertNoteRequest, request: Request, db: DbSession, user: ManagerDep
) -> AlertEvent:
    """Add a note (any state); the audit entry records only its length."""
    alert = _alert(db, alert_id, lock=True)
    event = add_note(
        db,
        alert,
        body.text,
        by=user.username,
        user_id=user.id,
        now=utc_now(),
        audit=_auditor(db, request, user, alert),
    )
    return event_out(event)


def _assignee(db: Session, user_id: int | None) -> User | None:
    """The user an alert may be given to: active and allowed to read alerts."""
    if user_id is None:
        return None
    target = db.get(User, user_id)
    if target is None or not target.active or "alerts.read" not in permissions_for(target.role):
        raise _error(422, "alert_assignee_invalid", details={"user_id": user_id})
    return target


@router.post("/{alert_id}/assign")
def assign_alert(
    alert_id: AlertId, body: AlertAssignRequest, request: Request, db: DbSession, user: ManagerDep
) -> AlertDetail:
    """Give the alert to a user who may read alerts, or to nobody (audited ``alert.assign``);
    the same assignee again changes nothing."""
    alert = _alert(db, alert_id, lock=True)
    target = _assignee(db, body.user_id)
    # The same assignee again: nothing to record.
    with suppress(WorkflowError):
        assign(
            db,
            alert,
            target,
            by=user.username,
            user_id=user.id,
            now=utc_now(),
            audit=_auditor(db, request, user, alert),
        )
    return alert_detail(db, alert)
