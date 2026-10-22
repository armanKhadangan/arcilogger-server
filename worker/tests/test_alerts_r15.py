"""R15 "Log kaynağı sessiz": positive and negative cases, the grace periods, the collector's
receive times, automatic resolution."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site
from arcilogger_db.models import Alert, Source
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts.r15 import R15Report, check_silent_sources
from arcilogger_worker.alerts.texts import facts
from arcilogger_worker.service import Worker, default_jobs

NOW = datetime(2026, 10, 19, 12, 0, tzinfo=UTC)
#: The worker has been running for an hour (past the start-up grace period).
STARTED = NOW - timedelta(hours=1)
LONG_AGO = NOW - timedelta(days=30)


def source(
    session: Session,
    name: str,
    *,
    last_seen: datetime | None,
    heartbeat: int = 15,
    created: datetime = LONG_AGO,
    first_seen: datetime | None = LONG_AGO,
    **columns: object,
) -> Source:
    row = Source(
        name=name,
        type="fortigate" if name == "fortigate" else "mikrotik",
        heartbeat_minutes=heartbeat,
        first_seen=first_seen if last_seen is not None else None,
        last_seen=last_seen,
        created_at=created,
        **columns,
    )
    session.add(row)
    session.commit()
    return row


def r15_alerts(session: Session) -> list[Alert]:
    session.expire_all()
    return list(session.scalars(select(Alert).where(Alert.rule == "R15").order_by(Alert.id)))


def check(
    session: Session, layout: Layout, now: datetime = NOW, started: datetime = STARTED
) -> R15Report:
    report = check_silent_sources(session, layout, now=now, started_at=started)
    session.commit()
    return report


def test_a_source_silent_longer_than_its_heartbeat_alerts_once(
    db_session: Session, layout: Layout
) -> None:
    source(db_session, "fortigate", last_seen=NOW - timedelta(minutes=11), heartbeat=10)
    source(db_session, "core-r1", last_seen=NOW - timedelta(minutes=14), heartbeat=15)
    report = check(db_session, layout)
    assert report.silent == ["fortigate"]
    assert report.opened == ["fortigate"]
    (alert,) = r15_alerts(db_session)
    assert (alert.severity, alert.state, alert.dedup_key) == ("high", "open", "R15:fortigate")
    assert alert.details["source"] == "fortigate"
    assert alert.details["threshold_minutes"] == 10
    assert alert.details["last_seen"] == isoformat_site(NOW - timedelta(minutes=11), "seconds")
    assert "10 dakikadan uzun" in alert.title
    # The next minutes: still one alert, not rewritten.
    updated = alert.updated_at
    assert check(db_session, layout, NOW + timedelta(minutes=1)).opened == []
    (same,) = r15_alerts(db_session)
    assert same.updated_at == updated


def test_a_long_silence_keeps_the_last_message(db_session: Session, layout: Layout) -> None:
    """The check notes that it still finds the problem (hourly) apart from the source's last
    message: the summary names the true last message, and the row is not rewritten every
    minute."""
    source(db_session, "fortigate", last_seen=NOW - timedelta(minutes=30), heartbeat=10)
    check(db_session, layout)
    last = isoformat_site(NOW - timedelta(minutes=30), "seconds")
    written = set()
    for minute in range(1, 130):
        check(db_session, layout, NOW + timedelta(minutes=minute))
        (alert,) = r15_alerts(db_session)
        assert alert.details["last_seen"] == last, minute
        summary = facts("R15", alert.details)
        assert summary is not None
        assert summary[0].endswith("Son mesaj: 19.10.2026 14:30:00."), minute
        written.add(alert.updated_at)
    # Created, then still found after one and after two hours.
    assert len(written) == 3
    assert alert.details["seen_at"] == (NOW + timedelta(minutes=120)).isoformat()


def test_messages_again_resolve_the_alert(db_session: Session, layout: Layout) -> None:
    row = source(db_session, "core-r1", last_seen=NOW - timedelta(minutes=20))
    check(db_session, layout)
    row.last_seen = NOW + timedelta(minutes=1)
    db_session.commit()
    report = check(db_session, layout, NOW + timedelta(minutes=2))
    assert report.resolved == ["core-r1"]
    (alert,) = r15_alerts(db_session)
    assert alert.state == "resolved"
    assert alert.resolved_at == NOW + timedelta(minutes=2)
    assert alert.details["auto_resolved"]["reason"] == "messages_again"
    # Silent again later: a new alert.
    check(db_session, layout, NOW + timedelta(minutes=30))
    assert [a.state for a in r15_alerts(db_session)] == ["resolved", "open"]


def test_disabled_unexpected_and_unknown_sources_are_not_watched(
    db_session: Session, layout: Layout
) -> None:
    old = NOW - timedelta(hours=5)
    source(db_session, "switch-1", last_seen=old, enabled=False)
    source(db_session, "ap-172.20.10.23", last_seen=old, expected=False)
    source(db_session, "unknown-172.20.19.9", last_seen=old, unknown=True, expected=False)
    assert check(db_session, layout).checked == 0
    assert r15_alerts(db_session) == []


def test_disabling_a_silent_source_resolves_its_alert(db_session: Session, layout: Layout) -> None:
    row = source(db_session, "switch-2", last_seen=NOW - timedelta(hours=1))
    check(db_session, layout)
    row.enabled = False
    db_session.commit()
    assert check(db_session, layout).resolved == ["switch-2"]
    (alert,) = r15_alerts(db_session)
    assert alert.details["auto_resolved"]["reason"] == "not_monitored"


def test_no_alert_right_after_the_worker_started(db_session: Session, layout: Layout) -> None:
    """After a reboot every source looks silent until ingest and the collector caught up."""
    source(db_session, "fortigate", last_seen=NOW - timedelta(hours=3), heartbeat=10)
    report = check(db_session, layout, started=NOW - timedelta(minutes=5))
    assert report.grace == ["fortigate"]
    assert r15_alerts(db_session) == []
    report = check(db_session, layout, NOW + timedelta(minutes=11), NOW - timedelta(minutes=5))
    assert report.opened == ["fortigate"]  # still silent after the 15 minutes


def test_a_new_source_and_one_that_never_sent(db_session: Session, layout: Layout) -> None:
    """A configured source gets its row when ingest starts; it alerts only after
    ``alerts.r15_never_seen_minutes`` (default 60) without any message."""
    source(db_session, "switch-1", last_seen=None, created=NOW - timedelta(minutes=30))
    assert check(db_session, layout).silent == []
    report = check(db_session, layout, NOW + timedelta(minutes=31))
    assert report.opened == ["switch-1"]
    (alert,) = r15_alerts(db_session)
    assert alert.details["never_seen"] is True
    assert alert.details["last_seen"] is None
    # A source created a moment ago that already sent once is in its grace period too.
    source(db_session, "switch-2", last_seen=NOW - timedelta(minutes=20), created=NOW)
    assert check(db_session, layout, NOW + timedelta(minutes=5)).grace == ["switch-2"]
    # And one whose first message ever came a few minutes ago.
    source(
        db_session,
        "switch-3",
        last_seen=NOW - timedelta(minutes=3),
        first_seen=NOW - timedelta(minutes=5),
        heartbeat=1,
    )
    assert "switch-3" in check(db_session, layout).grace


def test_the_never_seen_delay_and_grace_are_settings(db_session: Session, layout: Layout) -> None:
    set_setting(db_session, "alerts.r15_never_seen_minutes", 10, updated_by="tolga")
    set_setting(db_session, "alerts.r15_startup_grace_minutes", 0, updated_by="tolga")
    db_session.commit()
    source(db_session, "switch-1", last_seen=None, created=NOW - timedelta(minutes=11))
    assert check(db_session, layout, started=NOW).opened == ["switch-1"]


def test_the_collectors_receive_time_counts_too(db_session: Session, layout: Layout) -> None:
    """Ingest behind (its last_seen old) while the collector still receives: not silent."""
    source(db_session, "fortigate", last_seen=NOW - timedelta(minutes=40), heartbeat=10)
    status = {
        "updated_at": (NOW - timedelta(seconds=10)).isoformat(),
        "sources": {"fortigate": {"last_received": (NOW - timedelta(minutes=1)).isoformat()}},
    }
    layout.collector_status.parent.mkdir(parents=True, exist_ok=True)
    layout.collector_status.write_text(json.dumps(status), encoding="utf-8")
    assert check(db_session, layout).silent == []
    # A stale status file (collector down) is not trusted.
    status["updated_at"] = (NOW - timedelta(minutes=30)).isoformat()
    layout.collector_status.write_text(json.dumps(status), encoding="utf-8")
    assert check(db_session, layout).opened == ["fortigate"]


def test_the_shipped_heartbeats(db_session: Session, layout: Layout) -> None:
    """FortiGate 10 minutes, CORE-R1 15 (the commented example sources of
    deploy/windows/arcilogger.example.toml)."""
    source(db_session, "fortigate", last_seen=NOW - timedelta(minutes=9), heartbeat=10)
    source(db_session, "core-r1", last_seen=NOW - timedelta(minutes=16), heartbeat=15)
    assert check(db_session, layout).opened == ["core-r1"]
    assert check(db_session, layout, NOW + timedelta(minutes=2)).opened == ["fortigate"]


def test_the_worker_job_uses_its_start_time(
    db_engine: Engine, db_session: Session, layout: Layout
) -> None:
    (job,) = [job for job in default_jobs(60) if job.name == "alerts.r15"]
    source(db_session, "fortigate", last_seen=NOW - timedelta(hours=2), heartbeat=10)
    clock = [NOW]
    worker = Worker(engine=db_engine, layout=layout, jobs=[job], clock=lambda: clock[0])
    assert worker.tick() == [("alerts.r15", "ok")]
    assert r15_alerts(db_session) == []  # just started
    clock[0] = NOW + timedelta(minutes=16)
    assert worker.tick() == [("alerts.r15", "ok")]
    assert [a.dedup_key for a in r15_alerts(db_session)] == ["R15:fortigate"]
