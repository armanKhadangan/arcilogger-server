"""The alert workflow in the store (ADR-0010 amendment): a person's changes and notes with their
audit hook, the ``system`` events of every automatic resolution and re-opening, how a person's
closing meets a problem that persists, and the runbook catalogue."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, AlertEvent, Source
from arcilogger_worker.alerts.r15 import check_silent_sources
from arcilogger_worker.alerts.store import (
    TRANSITIONS,
    WorkflowError,
    add_note,
    change_state,
    clean_note,
    open_alert,
    reopen,
    resolve,
)
from arcilogger_worker.alerts.system import Finding, Outcome, apply
from arcilogger_worker.alerts.texts import RULES, UNKNOWN_RUNBOOK, runbook
from arcilogger_worker.correlation.conflicts import _reopen, resolve_unsupported

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)


class Audit:
    def __init__(self) -> None:
        self.entries: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, action: str, details: Mapping[str, Any]) -> None:
        self.entries.append((action, dict(details)))


def alert(session: Session, key: str = "R15:fortigate", **fields: Any) -> Alert:
    row = open_alert(
        session,
        rule=fields.pop("rule", "R15"),
        severity="high",
        title="Log kaynağı sessiz: fortigate",
        details=fields.pop("details", {"source": "fortigate"}),
        key=key,
        time=NOW,
        now=NOW,
    ).alert
    for name, value in fields.items():
        setattr(row, name, value)
    session.commit()
    return row


def events(session: Session, alert_id: int) -> list[tuple[str, str | None, str | None, str]]:
    session.expire_all()
    rows = session.scalars(
        select(AlertEvent).where(AlertEvent.alert_id == alert_id).order_by(AlertEvent.id)
    )
    return [(row.kind, row.from_state, row.to_state, row.actor) for row in rows]


def state_of(row: Alert) -> tuple[str, datetime | None]:
    return row.state, row.resolved_at


def change(session: Session, row: Alert, state: str, **kwargs: Any) -> list[AlertEvent]:
    audit = kwargs.pop("audit", Audit())
    result = change_state(
        session, row, state, by="tolga", user_id=None, now=NOW, audit=audit, **kwargs
    )
    session.commit()
    return result


# -- a person's changes ---------------------------------------------------------------------------


def test_change_state_writes_event_state_times_and_audit(db_session: Session) -> None:
    row = alert(db_session)
    audit = Audit()
    written = change(db_session, row, "resolved", note="  Kablo takıldı. ", audit=audit)
    assert [(e.kind, e.from_state, e.to_state, e.text) for e in written] == [
        ("state", "open", "resolved", None),
        ("note", None, None, "Kablo takıldı."),
    ]
    assert (row.state, row.resolved_at, row.updated_at) == ("resolved", NOW, NOW)
    assert audit.entries == [
        ("alert.state", {"from": "open", "to": "resolved", "rule": "R15"}),
        ("alert.note", {"length": len("Kablo takıldı.")}),
    ]
    change(db_session, row, "dismissed")
    assert (row.state, row.resolved_at) == ("dismissed", None)
    assert events(db_session, row.id)[-1] == ("state", "resolved", "dismissed", "tolga")


def test_refusals_change_nothing(db_session: Session) -> None:
    row = alert(db_session, state="dismissed")
    audit = Audit()
    with pytest.raises(WorkflowError) as same:
        change(db_session, row, "dismissed", audit=audit)
    assert same.value.code == "unchanged"
    with pytest.raises(WorkflowError) as refused:
        change(db_session, row, "acknowledged", audit=audit)
    assert refused.value.code == "not_allowed"
    with pytest.raises(ValueError, match="1-2000"):
        change(db_session, row, "open", note=" ", audit=audit)
    db_session.rollback()
    assert (row.state, audit.entries, events(db_session, row.id)) == ("dismissed", [], [])


def test_reopen_waits_for_the_key(db_session: Session) -> None:
    closed = alert(db_session, state="resolved")
    holder = alert(db_session)  # a check opened a new alert for the same problem
    assert holder.id != closed.id
    with pytest.raises(WorkflowError) as conflict:
        change(db_session, closed, "open")
    assert (conflict.value.code, conflict.value.other) == ("key_active", holder.id)
    db_session.rollback()
    change(db_session, holder, "resolved")
    change(db_session, closed, "open")
    assert closed.state == "open"


def test_notes(db_session: Session) -> None:
    row = alert(db_session)
    audit = Audit()
    event = add_note(
        db_session, row, "Bakılıyor\n- adım 1", by="tolga", user_id=None, now=NOW, audit=audit
    )
    db_session.commit()
    assert (event.kind, event.text, event.actor, event.at) == (
        "note",
        "Bakılıyor\n- adım 1",
        "tolga",
        NOW,
    )
    assert audit.entries == [("alert.note", {"length": len("Bakılıyor\n- adım 1")})]
    assert row.state == "open"


@pytest.mark.parametrize(
    ("text", "ok"),
    [
        ("a", True),
        ("ş" * 2000, True),
        ("  satır\r\n\tikinci  ", True),
        ("", False),
        ("   ", False),
        ("ş" * 2001, False),
        ("zil\x07", False),
        ("sil\x7f", False),
        ("nul\x00", False),
    ],
)
def test_clean_note(text: str, ok: bool) -> None:
    if ok:
        assert clean_note(text) == text.strip()
    else:
        with pytest.raises(ValueError, match="note"):
            clean_note(text)


def test_transition_table_reaches_every_state() -> None:
    for state, targets in TRANSITIONS.items():
        assert state not in targets
        assert "open" in targets or state == "open"


# -- system events --------------------------------------------------------------------------------


def test_resolve_and_reopen_write_system_events(db_session: Session) -> None:
    row = alert(db_session, state="acknowledged")
    resolve(row, reason="condition_cleared", now=NOW)
    db_session.commit()
    assert state_of(row) == ("resolved", NOW)
    reopen(row, reason="conflict_found_again", now=NOW + timedelta(minutes=1))
    db_session.commit()
    assert state_of(row) == ("open", None)
    assert "auto_resolved" not in row.details
    assert row.details["reopened_at"] == (NOW + timedelta(minutes=1)).isoformat()
    rows = db_session.scalars(
        select(AlertEvent).where(AlertEvent.alert_id == row.id).order_by(AlertEvent.id)
    ).all()
    assert [(e.from_state, e.to_state, e.actor, e.actor_id, e.text) for e in rows] == [
        ("acknowledged", "resolved", "system", None, "condition_cleared"),
        ("resolved", "open", "system", None, "conflict_found_again"),
    ]


def silent_source(session: Session, name: str = "fortigate") -> Source:
    row = Source(
        name=name,
        type="fortigate",
        heartbeat_minutes=10,
        last_seen=NOW - timedelta(minutes=30),
        first_seen=NOW - timedelta(days=30),
        created_at=NOW - timedelta(days=30),
    )
    session.add(row)
    session.commit()
    return row


def r15(session: Session, layout: Layout, now: datetime) -> None:
    check_silent_sources(session, layout, now=now, started_at=NOW - timedelta(hours=1))
    session.commit()


def r15_alerts(session: Session) -> list[Alert]:
    session.expire_all()
    return list(session.scalars(select(Alert).where(Alert.rule == "R15").order_by(Alert.id)))


def test_r15_resolution_is_a_system_event(db_session: Session, layout: Layout) -> None:
    source = silent_source(db_session)
    r15(db_session, layout, NOW)
    (row,) = r15_alerts(db_session)
    change(db_session, row, "acknowledged")  # a person looks into it: the check keeps it
    r15(db_session, layout, NOW + timedelta(minutes=1))
    assert [a.id for a in r15_alerts(db_session)] == [row.id]
    source.last_seen = NOW + timedelta(minutes=2)
    db_session.commit()
    r15(db_session, layout, NOW + timedelta(minutes=3))
    assert events(db_session, row.id) == [
        ("state", "open", "acknowledged", "tolga"),
        ("state", "acknowledged", "resolved", "system"),
    ]
    event = db_session.scalars(select(AlertEvent).order_by(AlertEvent.id.desc())).first()
    assert event is not None
    assert (event.text, event.at) == ("messages_again", NOW + timedelta(minutes=3))


def test_a_check_never_overwrites_a_change_a_person_is_making(
    db_engine: Engine, layout: Layout
) -> None:
    """The person's transaction holds the alert: the check skips it at once (no waiting) and
    decides on it in its next run, so the person's state is never overwritten."""
    sessions = session_factory(db_engine)
    with sessions() as setup:
        source = silent_source(setup)
        r15(setup, layout, NOW)
        (row,) = r15_alerts(setup)
        alert_id = row.id
        source.last_seen = NOW + timedelta(minutes=1)  # the problem is gone
        setup.commit()
    with sessions() as person:
        held = person.get_one(Alert, alert_id, with_for_update=True)
        with sessions() as check:
            check.execute(text("SET LOCAL lock_timeout = '5s'"))  # waiting would fail
            r15(check, layout, NOW + timedelta(minutes=2))
        change_state(person, held, "dismissed", by="tolga", user_id=None, now=NOW, audit=Audit())
        person.commit()
    with sessions() as after:
        (row,) = r15_alerts(after)
        assert row.state == "dismissed"
        assert events(after, alert_id) == [("state", "open", "dismissed", "tolga")]
        r15(after, layout, NOW + timedelta(minutes=3))  # no longer active: left alone
        assert [a.state for a in r15_alerts(after)] == ["dismissed"]


def test_closing_by_hand_while_the_problem_persists_opens_a_new_alert(
    db_session: Session, layout: Layout
) -> None:
    """The dedup key is unique among active alerts only: the next check opens a new one."""
    silent_source(db_session)
    r15(db_session, layout, NOW)
    (first,) = r15_alerts(db_session)
    change(db_session, first, "resolved")
    r15(db_session, layout, NOW + timedelta(minutes=1))
    first_again, second = r15_alerts(db_session)
    assert (first_again.id, first_again.state) == (first.id, "resolved")
    assert second.state == "open"
    assert events(db_session, second.id) == []  # created, not changed
    # The first one cannot be re-opened while the second is active.
    with pytest.raises(WorkflowError):
        change(db_session, first_again, "open")
    db_session.rollback()
    # A false alarm (dismissed) is the same: the problem is still there.
    change(db_session, second, "dismissed")
    r15(db_session, layout, NOW + timedelta(minutes=2))
    assert [a.state for a in r15_alerts(db_session)] == ["resolved", "dismissed", "open"]


def test_system_check_resolution_is_a_system_event(db_session: Session) -> None:
    row = alert(db_session, key="R20-disk:D:", rule="R20-disk", details={"drive": "D:"})
    outcome = Outcome([], lambda key: key.startswith("R20-disk:"), lambda _: "below_threshold")
    result = apply(db_session, outcome, now=NOW)
    db_session.commit()
    assert result["resolved"] == ["R20-disk:D:"]
    assert events(db_session, row.id) == [("state", "open", "resolved", "system")]
    # Found again: a new alert (the resolved one stays resolved).
    finding = Finding("R20-disk:D:", "R20-disk", "high", "Disk dolmak üzere", {"drive": "D:"})
    assert apply(db_session, Outcome([finding], lambda key: False), now=NOW)["opened"] == [
        "R20-disk:D:"
    ]


def test_ip_conflict_rebuild_events(db_session: Session) -> None:
    day = NOW.date()
    key = f"ip_conflict:172.20.43.120:aa:bb:cc:00:00:01,aa:bb:cc:00:00:02:{day.isoformat()}"
    row = alert(db_session, key=key, rule="ip_conflict", details={"ip": "172.20.43.120"})
    assert resolve_unsupported(db_session, day, found=()) == 1
    db_session.commit()
    assert row.state == "resolved"
    assert row.resolved_at is not None
    assert _reopen([row]) == 1
    db_session.commit()
    rows = db_session.scalars(
        select(AlertEvent).where(AlertEvent.alert_id == row.id).order_by(AlertEvent.id)
    ).all()
    assert [(e.to_state, e.actor, e.text) for e in rows] == [
        ("resolved", "system", "lease_rebuild"),
        ("open", "system", "conflict_found_again"),
    ]
    # Closed by a person: never re-opened by a rebuild.
    change(db_session, row, "dismissed")
    assert _reopen([row]) == 0


def test_a_rebuild_resolves_an_acknowledged_conflict_it_no_longer_finds(
    db_session: Session,
) -> None:
    """İnceleniyor while a person looks into it: the rebuild still closes it once the day's
    evidence shows no conflict, like every other check (UYARILAR.md section 8)."""
    day = NOW.date()
    key = f"ip_conflict:172.20.43.23:aa:bb:cc:00:00:03,aa:bb:cc:00:00:04:{day.isoformat()}"
    row = alert(db_session, key=key, rule="ip_conflict", details={"ip": "172.20.43.23"})
    change(db_session, row, "acknowledged")
    assert resolve_unsupported(db_session, day, found=(key,)) == 0  # still there: kept
    assert row.state == "acknowledged"
    assert resolve_unsupported(db_session, day, found=()) == 1
    db_session.commit()
    assert row.state == "resolved"
    assert row.details["auto_resolved"]["reason"] == "lease_rebuild"
    assert events(db_session, row.id) == [
        ("state", "open", "acknowledged", "tolga"),
        ("state", "acknowledged", "resolved", "system"),
    ]
    # A person's closing stays a person's.
    other = alert(
        db_session,
        key=key.replace(":03,", ":05,"),
        rule="ip_conflict",
        details={"ip": "172.20.43.23"},
    )
    change(db_session, other, "dismissed")
    assert resolve_unsupported(db_session, day, found=()) == 0
    assert other.state == "dismissed"


# -- runbooks -------------------------------------------------------------------------------------


def test_every_rule_of_the_catalogue_has_a_runbook() -> None:
    for rule in RULES:
        steps_tr, steps_en = runbook(rule)
        assert steps_tr.startswith("1. "), rule
        assert steps_en.startswith("1. "), rule
        assert steps_tr.count("\n") == steps_en.count("\n"), rule  # the same steps
    assert runbook("custom-rule") == UNKNOWN_RUNBOOK
    assert runbook("new_source") == runbook("new-log-source")
