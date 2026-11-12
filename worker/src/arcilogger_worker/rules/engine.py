"""The rule engine: the worker job ``rules.run`` (Wave 6 contract §2.1.3, §2.1.5).

Every minute, for every registered rule that is switched on and configured, up to the horizon
``H = now - rules.lag_seconds``:

1. The watermark ``W`` is ``rule_state.watermark``. A rule without one (its first run, or it was
   off since the last run) starts at ``H`` and evaluates nothing this run (decision D12: no
   alert for data older than the switch-on).
2. A backlog older than ``rules.max_catch_up_hours`` is skipped and recorded
   (``rule_state.skipped_until``).
3. ``(W, H]`` is evaluated in slices of at most ``rules.max_slice_seconds`` (and at most the
   rule's window, deviation W6-A-5), each in its own
   transaction with ``statement_timeout = rules.statement_timeout_seconds``: the rule's hits open
   or update alerts with their evidence, the watermark moves to the slice end, commit.
4. A failing slice (an exception, a timeout) is rolled back and evaluated again next run; the
   failure is recorded (``last_error``: the error's class, never its text, which may quote
   parameter values) and the job goes on with the next rule. ``rules.failures_alert`` failures
   in a row open the system alert ``R20-rules:<ID>`` (high); the next successful slice resolves
   it.

Afterwards the newest input row of each rule is recorded (``rule_state.last_input_at``, the
editor's data status), and a condition rule resolves the active alert of every key without a hit
for ``clear_seconds`` (reason ``condition_cleared``).

**Alerts** (§2.1.5): ``rule`` = the id, ``dedup_key`` = ``<ID>:<key>``. An active alert of the
key is updated (counts add up, ``last_time`` moves, ``values`` show the latest window, evidence is
appended up to ``rules.evidence_max``). Within ``cooldown_seconds`` after the ``time`` of the
newest closed alert of the key no new alert opens: the hit is counted on that alert's
``details.after_close``. The engine writes only ``alerts``, ``alert_evidence``, ``rule_state``
and, through the store, ``alert_events`` (contract §0.1 rule 2). The log names rules, slices and
counts, never a parameter or an evidence value.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db.models import Alert, AlertEvidence, Base, RuleState
from arcilogger_worker.alerts.store import (
    ACTIVE_STATES,
    CLOSED_STATES,
    active_alert,
    open_alert,
    resolve,
)
from arcilogger_worker.rules import registry
from arcilogger_worker.rules.model import EvidenceRef, Hit, RuleContext, RuleDef, RuleSettings
from arcilogger_worker.rules.settings import (
    EngineSettings,
    effective,
    engine_settings,
    runs,
    stored_overrides,
)
from arcilogger_worker.rules.texts import title

log = logging.getLogger(__name__)

Sessions = Callable[[], AbstractContextManager[Session]]

#: The system alert of a rule that keeps failing (contract §2.1.3), one per rule.
FAILING_RULE: Final = "R20-rules"
FAILING_TITLE: Final = ("Güvenlik kuralı çalışamıyor: {rule}", "Security rule cannot run: {rule}")
#: Reason codes of the automatic resolutions.
CONDITION_CLEARED: Final = "condition_cleared"
RULE_RUNS_AGAIN: Final = "rule_runs_again"
#: ``details`` key of the hits counted on a closed alert within its cooldown.
AFTER_CLOSE: Final = "after_close"
#: The column that tells how new a table's data is (``devices`` has no receive time).
_FRESHNESS: Final = {"devices": "last_seen"}


def failing_key(rule_id: str) -> str:
    return f"{FAILING_RULE}:{rule_id}"


@dataclass
class RuleRun:
    """What one rule did in one run of the job."""

    rule: str
    slices: int = 0
    hits: int = 0
    opened: int = 0
    updated: int = 0
    cooled: int = 0
    resolved: int = 0
    failed: bool = False
    started: bool = False
    skipped_until: datetime | None = None


@dataclass
class EngineReport:
    rules: list[RuleRun] = field(default_factory=list)

    def details(self) -> dict[str, Any]:
        ran = [item for item in self.rules if item.slices or item.started or item.failed]
        return {
            "rules": len(self.rules),
            "slices": sum(item.slices for item in self.rules),
            "hits": sum(item.hits for item in self.rules),
            "opened": sum(item.opened for item in self.rules),
            "updated": sum(item.updated for item in self.rules),
            "cooled": sum(item.cooled for item in self.rules),
            "resolved": sum(item.resolved for item in self.rules),
            "started": sorted(item.rule for item in self.rules if item.started),
            "failed": sorted(item.rule for item in self.rules if item.failed),
            "skipped_backlog": sorted(item.rule for item in self.rules if item.skipped_until),
            "_skipped": not ran,
        }


# -- one hit ---------------------------------------------------------------------------------------


def _json_value(value: object) -> object:
    """A hit value as JSON: numbers, strings, booleans and None as they are, anything else
    (an address object) as text."""
    if value is None or isinstance(value, bool | int | str):
        return value
    return str(value)


def _evidence_key(ref: EvidenceRef | AlertEvidence) -> tuple[object, ...]:
    table = ref.table if isinstance(ref, EvidenceRef) else ref.tbl
    return (table, ref.row_id, ref.time) if ref.row_id is None else (table, ref.row_id)


def _add_evidence(
    session: Session, alert: Alert, evidence: Sequence[EvidenceRef], limit: int
) -> int:
    """Append the new rows of ``evidence`` (oldest first) to ``alert``'s, up to ``limit``;
    the number added."""
    stored = list(session.scalars(select(AlertEvidence).where(AlertEvidence.alert_id == alert.id)))
    seen = {_evidence_key(row) for row in stored}
    position = max((row.n for row in stored), default=0)
    added = 0
    for ref in sorted(evidence, key=lambda item: (item.time_received or item.time, item.time)):
        if position >= limit:
            break
        if _evidence_key(ref) in seen:
            continue
        position += 1
        added += 1
        seen.add(_evidence_key(ref))
        session.add(
            AlertEvidence(
                alert_id=alert.id,
                n=position,
                tbl=ref.table,
                row_id=ref.row_id,
                time=ref.time,
                time_received=ref.time_received,
                source=ref.source,
                raw_day=ref.raw_day,
                raw_line=ref.raw_line,
                raw_offset=ref.raw_offset,
            )
        )
    return added


def _newest_closed(session: Session, key: str) -> Alert | None:
    return session.scalars(
        select(Alert)
        .where(Alert.dedup_key == key, Alert.state.in_(CLOSED_STATES))
        .order_by(Alert.time.desc(), Alert.id.desc())
        .limit(1)
    ).first()


def apply_hit(
    session: Session,
    rule: RuleDef,
    settings: RuleSettings,
    hit: Hit,
    *,
    evidence_max: int,
    now: datetime,
) -> str:
    """Record ``hit``: ``opened`` (a new alert), ``updated`` (the active alert of its key) or
    ``cooled`` (counted on the closed alert whose cooldown it falls in)."""
    key = f"{rule.id}:{hit.key}"
    first_time = min((ref.time_received or ref.time for ref in hit.evidence), default=hit.time)
    values = {name: _json_value(value) for name, value in hit.values.items()}
    title_tr, title_en = title(rule.id, hit.key, hit.count, hit.values)
    current = active_alert(session, key)
    if current is not None:
        details = dict(current.details or {})
        details.update(
            title_en=title_en,
            count=int(details.get("count") or 0) + hit.count,
            evidence_total=int(details.get("evidence_total") or 0) + hit.count,
            values=values,
            last_time=max(hit.time.isoformat(), str(details.get("last_time") or "")),
        )
        opened = open_alert(
            session,
            rule=rule.id,
            severity=settings.severity,
            title=title_tr,
            details=details,
            key=key,
            time=hit.time,
            now=now,
        )
        _add_evidence(session, opened.alert, hit.evidence, evidence_max)
        return "updated"
    closed = _newest_closed(session, key)
    if closed is not None and hit.time < closed.time + timedelta(seconds=settings.cooldown_seconds):
        details = dict(closed.details or {})
        after = dict(details.get(AFTER_CLOSE) or {})
        after["count"] = int(after.get("count") or 0) + hit.count
        after["last_time"] = max(hit.time.isoformat(), str(after.get("last_time") or ""))
        details[AFTER_CLOSE] = after
        closed.details = details
        return "cooled"
    details = {
        "title_en": title_en,
        "rule_kind": rule.kind,
        "key": hit.key,
        "count": hit.count,
        "values": values,
        "first_time": first_time.isoformat(),
        "last_time": hit.time.isoformat(),
        "actions": {"email": settings.actions.email, "webhook": settings.actions.webhook},
        "person_id": hit.person_id,
        "format_verified": rule.format_verified,
        "evidence_total": hit.count,
    }
    opened = open_alert(
        session,
        rule=rule.id,
        severity=settings.severity,
        title=title_tr,
        details=details,
        key=key,
        time=hit.time,
        now=now,
    )
    _add_evidence(session, opened.alert, hit.evidence, evidence_max)
    return "opened" if opened.created else "updated"


# -- one rule --------------------------------------------------------------------------------------


def _state(session: Session, rule_id: str) -> RuleState:
    state = session.get(RuleState, rule_id, with_for_update=True)
    if state is None:
        state = RuleState(rule=rule_id, failures=0, alerts_opened=0)
        session.add(state)
        session.flush()
    return state


def _statement_timeout(session: Session, seconds: int) -> None:
    session.execute(
        text("SELECT set_config('statement_timeout', :value, true)"),
        {"value": f"{seconds * 1000}"},
    )


def _error_name(exc: BaseException) -> str:
    """What ``rule_state.last_error`` keeps of a failure: the classes only (a database error's
    text may quote the statement and its parameters)."""
    if isinstance(exc, DBAPIError) and exc.orig is not None:
        return f"{type(exc).__name__}: {type(exc.orig).__name__}"
    return type(exc).__name__


def context(
    settings: RuleSettings,
    engine: EngineSettings,
    start: datetime,
    end: datetime,
    zone: ZoneInfo = SITE_TZ,
) -> RuleContext:
    return RuleContext(
        settings=settings,
        start=start,
        end=end,
        max_device_skew=engine.max_device_skew,
        internal_networks=engine.internal_networks,
        zone=zone,
    )


def step(engine: EngineSettings, settings: RuleSettings) -> timedelta:
    """The length of a rule's slices: ``rules.max_slice_seconds``, but never longer than the
    rule's window, so a backlog or a dry run puts every receive moment into the window of some
    slice end (deviation W6-A-5; a 600-s slice would show R01's 60-s window only its last
    minute)."""
    return min(engine.max_slice, timedelta(seconds=settings.window_seconds))


def slices(start: datetime, end: datetime, size: timedelta) -> list[tuple[datetime, datetime]]:
    """``(start, end]`` cut into consecutive slices of at most ``size``."""
    bounds = []
    while start < end:
        stop = min(start + size, end)
        bounds.append((start, stop))
        start = stop
    return bounds


def _evaluate_slice(
    sessions: Sessions,
    rule: RuleDef,
    settings: RuleSettings,
    engine: EngineSettings,
    bounds: tuple[datetime, datetime],
    now: datetime,
    run: RuleRun,
) -> None:
    start, end = bounds
    with sessions() as session:
        _statement_timeout(session, engine.statement_timeout_seconds)
        state = _state(session, rule.id)
        if state.watermark != start:  # another run moved it meanwhile
            session.rollback()
            return
        hits = list(rule.evaluate(session, context(settings, engine, start, end)))
        outcomes = [
            apply_hit(session, rule, settings, hit, evidence_max=engine.evidence_max, now=now)
            for hit in hits
        ]
        state.watermark = end
        state.failures = 0
        state.last_error = None
        state.alerts_opened += outcomes.count("opened")
        state.updated_at = now
        failing = active_alert(session, failing_key(rule.id))
        if failing is not None:
            resolve(failing, reason=RULE_RUNS_AGAIN, now=now)
            run.resolved += 1
        session.commit()
    run.slices += 1
    run.hits += len(hits)
    run.opened += outcomes.count("opened")
    run.updated += outcomes.count("updated")
    run.cooled += outcomes.count("cooled")
    log.info(
        "rule %s: slice %s..%s, %d hits (%d opened, %d updated, %d in cooldown)",
        rule.id,
        start.isoformat(),
        end.isoformat(),
        len(hits),
        outcomes.count("opened"),
        outcomes.count("updated"),
        outcomes.count("cooled"),
    )


def _record_failure(
    sessions: Sessions, rule: RuleDef, engine: EngineSettings, error: str, now: datetime
) -> None:
    with sessions() as session:
        state = _state(session, rule.id)
        state.failures += 1
        state.last_error = error
        state.updated_at = now
        if state.failures >= engine.failures_alert:
            title_tr, title_en = (text.format(rule=rule.id) for text in FAILING_TITLE)
            open_alert(
                session,
                rule=FAILING_RULE,
                severity="high",
                title=title_tr,
                details={
                    "title_en": title_en,
                    "rule_id": rule.id,
                    "failures": state.failures,
                    "error": error,
                },
                key=failing_key(rule.id),
                time=now,
                now=now,
            )
        session.commit()


def _resolve_cleared(session: Session, rule: RuleDef, horizon: datetime, now: datetime) -> int:
    """Condition rules: resolve each active alert whose key had no hit for ``clear_seconds``
    before the horizon."""
    limit = horizon - timedelta(seconds=rule.clear_seconds or 0)
    resolved = 0
    for alert in session.scalars(
        select(Alert).where(Alert.rule == rule.id, Alert.state.in_(ACTIVE_STATES))
    ):
        last = (alert.details or {}).get("last_time")
        try:
            last_time = datetime.fromisoformat(last) if isinstance(last, str) else alert.time
        except ValueError:
            last_time = alert.time
        if last_time <= limit:
            resolve(alert, reason=CONDITION_CLEARED, now=now)
            resolved += 1
    return resolved


def newest_input(
    session: Session, tables: Iterable[str], since: datetime, cache: dict[str, datetime | None]
) -> datetime | None:
    """The newest receive time among ``tables`` after ``since`` (one ``max`` per table and run,
    bounded by device time for partition pruning; ``devices``: the newest sighting)."""
    newest: datetime | None = None
    for name in tables:
        if name not in cache:
            table = Base.metadata.tables[name]
            column = table.c[_FRESHNESS.get(name, "time_received")]
            bound = table.c["last_seen" if name == "devices" else "time"]
            cache[name] = session.scalar(select(func.max(column)).where(bound > since))
        value = cache[name]
        if value is not None and (newest is None or value > newest):
            newest = value
    return newest


def run_rule(
    sessions: Sessions,
    rule: RuleDef,
    settings: RuleSettings,
    engine: EngineSettings,
    *,
    now: datetime,
    inputs: dict[str, datetime | None],
) -> RuleRun:
    """One rule, one run of the job (the steps of the module text)."""
    run = RuleRun(rule.id)
    horizon = now - engine.lag
    if not runs(rule, settings):
        with sessions() as session:
            state = _state(session, rule.id)
            state.watermark = None  # off: it starts afresh when switched on again (D12)
            state.updated_at = now
            session.commit()
        return run
    with sessions() as session:
        state = _state(session, rule.id)
        if state.watermark is None:
            state.watermark = horizon
            state.updated_at = now
            run.started = True
        elif horizon - state.watermark > engine.max_catch_up:
            state.watermark = horizon - engine.max_catch_up
            state.skipped_until = state.watermark
            run.skipped_until = state.watermark
            log.warning("rule %s: backlog before %s skipped", rule.id, state.watermark.isoformat())
        watermark = state.watermark
        session.commit()
    for bounds in slices(watermark, horizon, step(engine, settings)):
        try:
            _evaluate_slice(sessions, rule, settings, engine, bounds, now, run)
        except Exception as exc:
            error = _error_name(exc)
            log.warning("rule %s: slice from %s failed (%s)", rule.id, bounds[0], error)
            _record_failure(sessions, rule, engine, error, now)
            run.failed = True
            break
    with sessions() as session:
        state = _state(session, rule.id)
        state.last_run_at = now
        newest = newest_input(
            session, rule.inputs, now - engine.no_data - engine.max_device_skew, inputs
        )
        if newest is not None and (state.last_input_at is None or newest > state.last_input_at):
            state.last_input_at = newest
        if rule.kind == "condition" and not run.failed and state.watermark == horizon:
            run.resolved += _resolve_cleared(session, rule, horizon, now)
        session.commit()
    return run


def run_rules(
    sessions: Sessions, *, now: datetime, rules: Sequence[RuleDef] | None = None
) -> EngineReport:
    """The job ``rules.run``: every registered rule (or ``rules``), one after the other."""
    selected = tuple(registry.RULES if rules is None else rules)
    with sessions() as session:
        engine = engine_settings(session)
        stored = stored_overrides(session, selected)
    report = EngineReport()
    inputs: dict[str, datetime | None] = {}
    for rule in selected:
        settings = effective(rule, stored[rule.id])
        report.rules.append(run_rule(sessions, rule, settings, engine, now=now, inputs=inputs))
    return report


def data_status(last_input_at: datetime | None, now: datetime, no_data: timedelta) -> str:
    """``ok`` (data within ``rules.no_data_hours``), ``no_data`` or ``never``."""
    if last_input_at is None:
        return "never"
    return "ok" if now - last_input_at <= no_data else "no_data"
