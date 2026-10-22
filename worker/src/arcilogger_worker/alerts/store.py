"""Writing alerts: open one (or bring the active one with the same key up to date), resolve.

An alert is **active** while it is ``open`` or ``acknowledged`` (a person saw it, the problem
may still be there). A check never opens a second alert for a key that has an active one —
the partial unique index ``uq_alerts_open_dedup_key`` guarantees it for ``open`` — and it
resolves the active alert itself once the condition is gone: ``state = resolved``,
``resolved_at`` and ``details.auto_resolved`` (when and why). An alert a person resolved or
dismissed is never re-opened by a check; the next occurrence is a new alert.

**Every change of state is an ``alert_events`` row** (ADR-0010 amendment): :func:`resolve` and
:func:`reopen` write one by ``system`` with the reason code, and a person's change or note goes
through :func:`change_state` / :func:`add_note` — the only writers for the API — which also
write the audit entry through the caller's hook (``alert.state`` with from/to, ``alert.note``
with the note's **length** only: a note may name people, the sealed audit log never holds it).

A person's transitions (:data:`TRANSITIONS`; the web names in brackets): ``open`` (Yeni) →
``acknowledged`` (İnceleniyor), ``resolved`` (Kapandı) or ``dismissed`` (Yanlış alarm);
``acknowledged`` → any other; ``resolved`` ↔ ``dismissed``; ``resolved`` / ``dismissed`` →
``open`` (re-open) only while no **other** active alert holds the same dedup key — a check that
still saw the problem has opened that one meanwhile (:class:`WorkflowError` ``key_active``).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, object_session

from arcilogger_db.models import (
    ALERT_NOTE_MAX,
    ALERT_REASON_MAX,
    ALERT_SEVERITIES,
    Alert,
    AlertEvent,
)

#: Lowest first: ``info`` < ``low`` < ``medium`` < ``high`` < ``critical``.
SEVERITY_RANK: Final = {name: rank for rank, name in enumerate(ALERT_SEVERITIES)}
#: States of an alert whose problem is not known to be over.
ACTIVE_STATES: Final = ("open", "acknowledged")
#: States of a closed alert (resolved, or a false alarm).
CLOSED_STATES: Final = ("resolved", "dismissed")
#: ``details`` key of an alert a check resolved (``{"at", "reason", …}``).
AUTO_RESOLVED: Final = "auto_resolved"
#: Longest ``alerts.title`` written here.
TITLE_MAX: Final = 500
#: ``alert_events.actor`` of the automatic changes.
SYSTEM: Final = "system"
#: The states a person may set, per current state (see the module text).
TRANSITIONS: Final[Mapping[str, frozenset[str]]] = {
    "open": frozenset({"acknowledged", "resolved", "dismissed"}),
    "acknowledged": frozenset({"open", "resolved", "dismissed"}),
    "resolved": frozenset({"open", "dismissed"}),
    "dismissed": frozenset({"open", "resolved"}),
}
#: Writes one audit entry: ``audit(action, details)`` (the API passes ``record_audit``).
AuditHook = Callable[[str, Mapping[str, Any]], object]
#: ``details`` key of when a check last found the problem again — not ``last_seen``, which R15's
#: facts use for the source's last message.
SEEN_AT: Final = "seen_at"
#: A check that finds its problem again records it (``details.seen_at``) at most this often.
SEEN_EVERY: Final = timedelta(hours=1)


def higher(first: str, second: str) -> str:
    """The more severe of two severities."""
    return first if SEVERITY_RANK.get(first, 0) >= SEVERITY_RANK.get(second, 0) else second


def at_least(severity: str, minimum: str) -> bool:
    return SEVERITY_RANK.get(severity, 0) >= SEVERITY_RANK.get(minimum, 0)


def active_alerts(session: Session, keys: Iterable[str] | None = None) -> list[Alert]:
    """Active alerts with a dedup key (of ``keys``, or all), oldest first — locked, skipping
    the ones a person is changing right now: the check decides on them in its next run instead
    of overwriting the person's change (and never waits for the API)."""
    query = select(Alert).where(Alert.state.in_(ACTIVE_STATES), Alert.dedup_key.is_not(None))
    if keys is not None:
        query = query.where(Alert.dedup_key.in_(sorted(set(keys))))
    locked = query.order_by(Alert.id).with_for_update(skip_locked=True)
    return list(session.scalars(locked.execution_options(populate_existing=True)))


def active_alert(session: Session, key: str) -> Alert | None:
    """The newest active alert with ``key`` (locked for the update)."""
    return session.scalars(
        select(Alert)
        .where(Alert.dedup_key == key, Alert.state.in_(ACTIVE_STATES))
        .order_by(Alert.id.desc())
        .limit(1)
        .with_for_update()
    ).first()


def last_occurrence(alert: Alert) -> datetime:
    """When the problem of ``alert`` was last reported (an imported repeat, else its time)."""
    value = (alert.details or {}).get("last_time")
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return alert.time
        if parsed.tzinfo is not None:
            return parsed
    return alert.time


def last_seen(alert: Alert) -> datetime:
    """When the problem of ``alert`` was last known to be there: its time, an imported repeat,
    or a check that found it again (``details.seen_at``, at most every :data:`SEEN_EVERY`)."""
    seen = max(alert.time, last_occurrence(alert))
    value = (alert.details or {}).get(SEEN_AT)
    try:
        parsed = datetime.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        parsed = None
    return max(seen, parsed) if parsed is not None and parsed.tzinfo is not None else seen


@dataclass(frozen=True)
class Opened:
    alert: Alert
    #: A new row (``False``: the active alert with the same key was brought up to date).
    created: bool


def open_alert(
    session: Session,
    *,
    rule: str,
    severity: str,
    title: str,
    details: Mapping[str, Any],
    key: str | None,
    time: datetime,
    repeat: bool = False,
    now: datetime | None = None,
) -> Opened:
    """A new open alert, or the active one with ``key`` brought up to date.

    ``repeat``: the same event happened again (an imported ``alerts.jsonl`` line): the active
    alert counts it (``details.repeats``, ``last_time``, ``last_title``) and keeps its first
    title. Otherwise a check found its problem again: the details and the title are replaced,
    but only when they changed, and ``seen_at`` at most every :data:`SEEN_EVERY`, so a check
    that runs every minute does not rewrite the row. The severity only ever goes up. ``now`` is
    the row's ``created_at`` (default: the database time; ``time`` is when the problem
    happened).
    """
    if key is not None:
        existing = active_alert(session, key)
        if existing is not None:
            _update(existing, severity, title, details, time, repeat)
            return Opened(existing, False)
    alert = Alert(
        time=time,
        rule=rule,
        severity=severity,
        state="open",
        title=title[:TITLE_MAX],
        details=dict(details),
        dedup_key=key,
    )
    if now is not None:
        alert.created_at = now
    try:
        with session.begin_nested():
            session.add(alert)
            session.flush()
    except IntegrityError:
        # Another writer opened the same key meanwhile (the partial unique index).
        existing = active_alert(session, key) if key is not None else None
        if existing is None:
            raise
        _update(existing, severity, title, details, time, repeat)
        return Opened(existing, False)
    return Opened(alert, True)


def _update(
    alert: Alert,
    severity: str,
    title: str,
    details: Mapping[str, Any],
    time: datetime,
    repeat: bool,
) -> None:
    current = dict(alert.details or {})
    if repeat:
        facts = {k: v for k, v in details.items() if k != "origin"}
        merged = {
            **current,
            **facts,
            "repeats": int(current.get("repeats") or 0) + 1,
            "last_time": time.isoformat(),
            "last_title": title[:TITLE_MAX],
        }
        if "origin" in details:
            merged["last_origin"] = details["origin"]
    else:
        merged = {**current, **details}
        if time - last_seen(alert) >= SEEN_EVERY:  # still there: the e-mail treats it as current
            merged[SEEN_AT] = time.isoformat()
        if title[:TITLE_MAX] != alert.title:
            alert.title = title[:TITLE_MAX]
    if merged != current:
        alert.details = merged
    raised = higher(alert.severity, severity)
    if raised != alert.severity:
        alert.severity = raised


def resolve(
    alert: Alert, *, reason: str, now: datetime, facts: Mapping[str, Any] | None = None
) -> None:
    """Resolve an active alert because its condition is gone (event by ``system``)."""
    previous = alert.state
    alert.state = "resolved"
    alert.resolved_at = now
    alert.details = {
        **(alert.details or {}),
        AUTO_RESOLVED: {"at": now.isoformat(), "reason": reason, **(facts or {})},
    }
    if previous != "resolved":
        _system_event(alert, previous, "resolved", reason, now)


def reopen(alert: Alert, *, reason: str, now: datetime) -> None:
    """Re-open an alert a check had resolved, because the check finds the problem again (event
    by ``system``; ``details.auto_resolved`` gives way to ``details.reopened_at``)."""
    previous = alert.state
    details = {key: value for key, value in (alert.details or {}).items() if key != AUTO_RESOLVED}
    details["reopened_at"] = now.isoformat()
    alert.state = "open"
    alert.resolved_at = None
    alert.details = details
    if previous != "open":
        _system_event(alert, previous, "open", reason, now)


def _system_event(alert: Alert, previous: str, state: str, reason: str, now: datetime) -> None:
    session = object_session(alert)
    if session is None:  # pragma: no cover - the callers always hold a loaded alert
        raise RuntimeError("the alert is not in a session")
    record_event(
        session,
        alert,
        kind="state",
        from_state=previous,
        to_state=state,
        text=reason[:ALERT_REASON_MAX] or None,
        by=SYSTEM,
        user_id=None,
        at=now,
    )


def record_event(
    session: Session,
    alert: Alert,
    *,
    kind: str,
    by: str,
    user_id: int | None,
    at: datetime,
    from_state: str | None = None,
    to_state: str | None = None,
    text: str | None = None,
) -> AlertEvent:
    """Add one ``alert_events`` row (in the caller's transaction)."""
    if not inspect(alert).has_identity:  # a new alert gets its id first
        session.flush()
    event = AlertEvent(
        alert_id=alert.id,
        kind=kind,
        from_state=from_state,
        to_state=to_state,
        text=text,
        actor=by,
        actor_id=user_id,
        at=at,
    )
    session.add(event)
    return event


# -- a person's changes (the API) -----------------------------------------------------------------


class WorkflowError(Exception):
    """A change a person may not make: ``unchanged`` (already in that state), ``not_allowed``
    (no such transition) or ``key_active`` (re-open while ``other`` holds the dedup key)."""

    def __init__(self, code: str, *, other: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.other = other


def clean_note(text: str) -> str:
    """A note as stored: trimmed, 1-:data:`ALERT_NOTE_MAX` characters, no control characters
    other than tab and line breaks (``ValueError`` otherwise)."""
    value = text.strip()
    if not 1 <= len(value) <= ALERT_NOTE_MAX:
        raise ValueError(f"a note has 1-{ALERT_NOTE_MAX} characters")
    if any((ord(char) < 0x20 and char not in "\t\n\r") or char == "\x7f" for char in value):
        raise ValueError("a note must not contain control characters")
    return value


def other_active(session: Session, alert: Alert) -> Alert | None:
    """Another active alert with ``alert``'s dedup key (``None`` without a key)."""
    if alert.dedup_key is None:
        return None
    return session.scalars(
        select(Alert)
        .where(
            Alert.dedup_key == alert.dedup_key,
            Alert.state.in_(ACTIVE_STATES),
            Alert.id != alert.id,
        )
        .order_by(Alert.id.desc())
        .limit(1)
    ).first()


def change_state(
    session: Session,
    alert: Alert,
    state: str,
    *,
    by: str,
    user_id: int | None,
    now: datetime,
    audit: AuditHook,
    note: str | None = None,
) -> list[AlertEvent]:
    """A person sets ``alert`` (locked by the caller) to ``state``, with an optional note.

    Writes the state event (and a note event), ``updated_at``, ``resolved_at`` (set for
    ``resolved``, cleared otherwise; ``details.auto_resolved`` is dropped — the alert is now a
    person's) and the audit entries. Raises :class:`WorkflowError` or ``ValueError`` (the note)
    before anything is changed.
    """
    previous = alert.state
    if state == previous:
        raise WorkflowError("unchanged")
    if state not in TRANSITIONS.get(previous, frozenset()):
        raise WorkflowError("not_allowed")
    text = clean_note(note) if note is not None else None
    if state in ACTIVE_STATES:
        holder = other_active(session, alert)
        if holder is not None:
            raise WorkflowError("key_active", other=holder.id)
    try:
        with session.begin_nested():
            alert.state = state
            alert.updated_at = now
            alert.resolved_at = now if state == "resolved" else None
            if AUTO_RESOLVED in (alert.details or {}):
                alert.details = {k: v for k, v in alert.details.items() if k != AUTO_RESOLVED}
            session.flush()
    except IntegrityError:
        # A check opened an alert with the same key meanwhile (``uq_alerts_open_dedup_key``).
        holder = other_active(session, alert)
        raise WorkflowError("key_active", other=holder.id if holder else None) from None
    events = [
        record_event(
            session,
            alert,
            kind="state",
            from_state=previous,
            to_state=state,
            by=by,
            user_id=user_id,
            at=now,
        )
    ]
    audit("alert.state", {"from": previous, "to": state, "rule": alert.rule})
    if text is not None:
        events.append(
            record_event(session, alert, kind="note", text=text, by=by, user_id=user_id, at=now)
        )
        audit("alert.note", {"length": len(text)})
    session.flush()
    return events


def add_note(
    session: Session,
    alert: Alert,
    text: str,
    *,
    by: str,
    user_id: int | None,
    now: datetime,
    audit: AuditHook,
) -> AlertEvent:
    """A person's note on ``alert`` (any state); the audit entry holds only its length."""
    value = clean_note(text)
    alert.updated_at = now
    event = record_event(session, alert, kind="note", text=value, by=by, user_id=user_id, at=now)
    audit("alert.note", {"length": len(value)})
    session.flush()
    return event
