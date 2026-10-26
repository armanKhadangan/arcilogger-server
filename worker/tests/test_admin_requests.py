"""The worker channel of the browser administration (contract 2.9): ``admin.requests`` sends the
test e-mail through a real SMTP server on 127.0.0.1 and rebuilds lease days from
``admin_requests`` rows, with progress, results and the audit entry in one transaction, every
address masked, and ``FOR UPDATE SKIP LOCKED`` so two workers never work on one request."""

from __future__ import annotations

import json
import re
import threading
from datetime import date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.admin.contract import TERMINAL_STATES, new_id
from arcilogger_core.config import Config, SmtpConfig
from arcilogger_core.layout import Layout
from arcilogger_db.audit import verify_audit_chain
from arcilogger_db.engine import session_factory
from arcilogger_db.models import AdminRequest, AuditLog, JobRun, Lease, User
from arcilogger_db.settings import set_setting
from arcilogger_worker import admin_jobs
from arcilogger_worker.admin_jobs import (
    LEASE_REBUILD,
    TEST_EMAIL,
    RequestsReport,
    lease_rebuild_range,
    mask_address,
    mask_addresses,
    process_requests,
)
from arcilogger_worker.alerts.smtptesting import FakeSmtp
from arcilogger_worker.correlation.rebuild import DayRebuild, rebuild_lease_day
from arcilogger_worker.correlation.watermark import set_watermark
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
NOW = tr(DAY + timedelta(days=3), 10)
TO = "bora@ornek.example"
MASKED = "b***@ornek.example"
SENDER = "arcilogger@ornek.example"
SECRET_KEY = re.compile("password|customer|token", re.IGNORECASE)


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


@pytest.fixture
def requester(db_session: Session) -> User:
    user = User(username="ayse", display_name="Ayşe", role="admin", password_hash="!")
    db_session.add(user)
    db_session.commit()
    return user


def smtp_config(layout: Layout, fake: FakeSmtp | None) -> Config:
    smtp = (
        SmtpConfig()
        if fake is None
        else SmtpConfig(
            # The test e-mail works before the alert e-mail is switched on.
            enabled=False,
            host=fake.host,
            port=fake.port,
            security="none",
            from_address=SENDER,
            timeout_seconds=2,
        )
    )
    return Config(data_dir=layout.root, secrets_dir=layout.root, smtp=smtp)


def queue(
    session: Session,
    user: User,
    kind: str,
    params: dict[str, Any],
    *,
    created_at: datetime = NOW - timedelta(seconds=5),
    expires_at: datetime | None = None,
) -> str:
    row = AdminRequest(
        id=new_id(),
        kind=kind,
        channel="worker",
        state="queued",
        requested_by_id=user.id,
        requested_by=user.username,
        created_at=created_at,
        expires_at=expires_at,
        params=params,
    )
    session.add(row)
    session.commit()
    return row.id


def request(session: Session, request_id: str) -> AdminRequest:
    session.expire_all()
    row = session.get(AdminRequest, request_id)
    assert row is not None
    return row


def audit(session: Session, action: str) -> list[AuditLog]:
    session.expire_all()
    return list(
        session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
    )


def no_secret_keys(value: Any) -> bool:
    if isinstance(value, dict):
        return all(not SECRET_KEY.search(k) and no_secret_keys(v) for k, v in value.items())
    if isinstance(value, list):
        return all(no_secret_keys(item) for item in value)
    return True


# -- the test e-mail ------------------------------------------------------------------------------


def test_a_test_email_is_sent_once_with_its_result_and_audit_entry(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
) -> None:
    request_id = queue(db_session, requester, TEST_EMAIL, {"to": TO})
    config = smtp_config(layout, smtp_server)
    report = process_requests(sessions, config, now=NOW)
    assert report.steps == [f"{request_id} {TEST_EMAIL} succeeded"]
    assert [r.recipients for r in smtp_server.received] == [[TO]]
    assert "Deneme e-postası" in str(smtp_server.received[0].message["Subject"])
    row = request(db_session, request_id)
    assert (row.state, row.terminal, row.error_code) == ("succeeded", True, None)
    assert row.result == {"ok": True, "to": MASKED, "smtp_code": None, "error_text": None}
    (entry,) = audit(db_session, "smtp.test")
    assert entry.actor_username == "system"
    assert entry.target is None
    assert entry.details == {
        "to": MASKED,
        "ok": True,
        "smtp_code": None,
        "request": request_id,
        "requested_by": {"user_id": requester.id, "username": "ayse"},
    }
    assert TO not in json.dumps([row.result, entry.details])
    assert verify_audit_chain(db_session).ok
    # Done is done: the next run finds nothing (and sends nothing again).
    assert process_requests(sessions, config, now=NOW).steps == []
    assert len(smtp_server.received) == 1


def test_a_refused_test_email_keeps_the_smtp_code(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
) -> None:
    smtp_server.refuse.add(TO)
    request_id = queue(db_session, requester, TEST_EMAIL, {"to": TO})
    process_requests(sessions, smtp_config(layout, smtp_server), now=NOW)
    row = request(db_session, request_id)
    assert (row.state, row.terminal, row.error_code) == ("failed", True, "smtp_test_failed")
    assert row.result is not None
    assert (row.result["ok"], row.result["to"], row.result["smtp_code"]) == (False, MASKED, 550)
    assert "550" in row.result["error_text"]
    (entry,) = audit(db_session, "smtp.test")
    assert (entry.details["ok"], entry.details["smtp_code"], entry.details["code"]) == (
        False,
        550,
        "smtp_test_failed",
    )


def test_a_test_email_without_an_smtp_server_is_not_configured(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, requester: User
) -> None:
    request_id = queue(db_session, requester, TEST_EMAIL, {"to": TO})
    process_requests(sessions, smtp_config(layout, None), now=NOW)
    row = request(db_session, request_id)
    assert (row.state, row.error_code) == ("failed", "smtp_not_configured")
    assert row.result is not None
    assert "host ve from_address ayarlı değil" in row.result["error_text"]
    assert "host and from_address are not set" in row.result["error_text"]
    assert audit(db_session, "smtp.test")[0].details["code"] == "smtp_not_configured"


def test_addresses_are_masked_also_inside_smtp_replies() -> None:
    assert mask_address(TO) == MASKED
    assert mask_address("not an address") == "***"
    reply = "recipient refused: 550 5.1.1 <bora@ornek.example>: unknown; bilgi@ornek.example"
    assert mask_addresses(reply) == (
        "recipient refused: 550 5.1.1 <b***@ornek.example>: unknown; b***@ornek.example"
    )


# -- refused and expired requests -----------------------------------------------------------------


def test_an_invalid_or_expired_request_changes_nothing(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
) -> None:
    bad_address = queue(db_session, requester, TEST_EMAIL, {"to": "bora at ornek"})
    reversed_range = queue(
        db_session, requester, LEASE_REBUILD, {"from": "2026-10-13", "to": "2026-10-12"}
    )
    expired = queue(db_session, requester, TEST_EMAIL, {"to": TO}, expires_at=NOW)
    process_requests(sessions, smtp_config(layout, smtp_server), now=NOW)
    assert smtp_server.received == []
    assert db_session.scalars(select(JobRun)).all() == []
    rows = {key: request(db_session, key) for key in (bad_address, reversed_range, expired)}
    states = {key: (row.state, row.error_code) for key, row in rows.items()}
    assert states == {
        bad_address: ("failed", "validation_failed"),
        reversed_range: ("failed", "validation_failed"),
        expired: ("expired", "request_expired"),
    }
    assert all(row.terminal for row in rows.values())
    codes = [e.details["code"] for e in audit(db_session, "smtp.test")]
    assert sorted(codes) == ["request_expired", "validation_failed"]
    (rebuild,) = audit(db_session, "leases.rebuild")
    assert rebuild.details["code"] == "validation_failed"
    assert verify_audit_chain(db_session).ok


@pytest.mark.parametrize(
    ("params", "accepted"),
    [
        ({"from": "2026-10-12", "to": "2026-10-12"}, True),
        ({"from": "2026-09-15", "to": "2026-10-15"}, True),  # 31 days
        ({"from": "2026-09-14", "to": "2026-10-15"}, False),  # 32 days
        ({"from": "2026-10-15", "to": "2026-10-16"}, False),  # tomorrow is in the future
        ({"from": "2026-10-12"}, False),
        ({"from": "12.10.2026", "to": "2026-10-12"}, False),
    ],
)
def test_the_lease_rebuild_range_is_checked_again(params: dict[str, str], accepted: bool) -> None:
    assert (lease_rebuild_range(params, now=NOW) is not None) is accepted


# -- the lease rebuild ----------------------------------------------------------------------------


def test_a_lease_rebuild_reports_progress_and_finishes_with_its_audit_entry(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    evidence: Evidence,
    requester: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    evidence.dhcp(tr(DAY, 22), "assigned", "172.20.43.120", "f0:1f:af:00:00:01")
    evidence.dhcp(
        tr(DAY + timedelta(days=1), 2), "deassigned", "172.20.43.120", "f0:1f:af:00:00:01"
    )
    set_watermark(db_session, tr(DAY + timedelta(days=2), 0))
    db_session.commit()
    request_id = queue(
        db_session, requester, LEASE_REBUILD, {"from": "2026-10-12", "to": "2026-10-13"}
    )
    seen: list[tuple[date, str, dict[str, Any] | None]] = []
    rebuild = rebuild_lease_day

    def watched(sessions: sessionmaker[Session], day: date, **kwargs: Any) -> DayRebuild:
        # What the API sees while the day is rebuilt: the progress committed so far.
        with sessions() as other:
            row = other.get(AdminRequest, request_id)
            assert row is not None
            seen.append((day, row.state, row.result))
        return rebuild(sessions, day, **kwargs)

    monkeypatch.setattr(admin_jobs, "rebuild_lease_day", watched)
    report = process_requests(sessions, smtp_config(layout, None), now=NOW)
    assert report.steps == [
        f"{request_id} {LEASE_REBUILD} running",
        f"{request_id} {LEASE_REBUILD} running",
        f"{request_id} {LEASE_REBUILD} succeeded",
    ]
    assert seen == [
        (DAY, "running", {"done_days": 0, "total_days": 2, "failed_days": []}),
        (DAY + timedelta(days=1), "running", {"done_days": 1, "total_days": 2, "failed_days": []}),
    ]
    row = request(db_session, request_id)
    assert (row.state, row.terminal, row.error_code) == ("succeeded", True, None)
    assert row.result == {"done_days": 2, "total_days": 2, "failed_days": []}
    lease = db_session.scalars(select(Lease)).one()
    assert (lease.start, lease.end) == (tr(DAY, 22), tr(DAY + timedelta(days=1), 2))
    runs = db_session.scalars(select(JobRun).order_by(JobRun.id)).all()
    assert [(r.job, r.status, r.details["manual"], r.details["request"]) for r in runs] == [
        ("leases.rebuild", "ok", True, request_id),
        ("leases.rebuild", "ok", True, request_id),
    ]
    (entry,) = audit(db_session, "leases.rebuild")
    assert entry.details == {
        "from": "2026-10-12",
        "to": "2026-10-13",
        "done_days": 2,
        "failed_days": [],
        "request": request_id,
        "requested_by": {"user_id": requester.id, "username": "ayse"},
    }


def test_a_failed_day_is_listed_and_fails_the_request(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, requester: User
) -> None:
    # 3600 s x 1 % = 36 s guest timeout: shorter than the 300 s traffic bucket.
    set_setting(db_session, "lease.timeout_percent", 1, updated_by="tolga")
    db_session.commit()
    request_id = queue(
        db_session, requester, LEASE_REBUILD, {"from": "2026-10-12", "to": "2026-10-13"}
    )
    process_requests(sessions, smtp_config(layout, None), now=NOW)
    row = request(db_session, request_id)
    assert (row.state, row.terminal) == ("failed", True)
    assert row.result == {
        "done_days": 0,
        "total_days": 2,
        "failed_days": ["2026-10-12", "2026-10-13"],
    }
    runs = db_session.scalars(select(JobRun).order_by(JobRun.id)).all()
    assert [(r.status, r.details["error"], r.details["request"]) for r in runs] == [
        ("failed", "ValueError", request_id),
        ("failed", "ValueError", request_id),
    ]
    assert all(r.finished_at is not None for r in runs)
    (entry,) = audit(db_session, "leases.rebuild")
    assert (entry.details["done_days"], entry.details["failed_days"]) == (
        0,
        ["2026-10-12", "2026-10-13"],
    )


def test_the_time_budget_leaves_the_rest_for_the_next_run(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, requester: User
) -> None:
    request_id = queue(
        db_session, requester, LEASE_REBUILD, {"from": "2026-10-12", "to": "2026-10-12"}
    )
    report = process_requests(
        sessions, smtp_config(layout, None), now=NOW, budget=timedelta(seconds=-1)
    )
    assert report == RequestsReport(steps=[], more=True)
    assert request(db_session, request_id).state == "queued"


# -- several workers ------------------------------------------------------------------------------


def test_a_request_held_by_one_worker_is_skipped_by_another(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """While worker A rebuilds a day, worker B sends a test e-mail queued meanwhile and leaves
    the rebuild alone (``SKIP LOCKED``); A finishes it."""
    config = smtp_config(layout, smtp_server)
    rebuild_id = queue(
        db_session, requester, LEASE_REBUILD, {"from": "2026-10-12", "to": "2026-10-12"}
    )
    other: list[RequestsReport] = []
    rebuild = rebuild_lease_day

    def with_second_worker(sessions: sessionmaker[Session], day: date, **kwargs: Any) -> DayRebuild:
        with sessions() as session:
            mail_id = queue(session, requester, TEST_EMAIL, {"to": TO}, created_at=NOW)
        other.append(process_requests(sessions, config, now=NOW))
        assert other[0].steps == [f"{mail_id} {TEST_EMAIL} succeeded"]
        return rebuild(sessions, day, **kwargs)

    monkeypatch.setattr(admin_jobs, "rebuild_lease_day", with_second_worker)
    report = process_requests(sessions, config, now=NOW)
    assert len(other) == 1
    assert report.steps == [
        f"{rebuild_id} {LEASE_REBUILD} running",
        f"{rebuild_id} {LEASE_REBUILD} succeeded",
    ]
    assert len(smtp_server.messages_to(TO)) == 1
    assert len(audit(db_session, "leases.rebuild")) == 1


def test_two_workers_send_each_test_email_once(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
) -> None:
    smtp_server.delay = 0.2  # every e-mail takes a while: the workers overlap
    recipients = [f"nobet{n}@ornek.example" for n in range(6)]
    ids = [queue(db_session, requester, TEST_EMAIL, {"to": to}) for to in recipients]
    config = smtp_config(layout, smtp_server)
    reports: list[RequestsReport] = []
    start = threading.Barrier(2)

    def worker() -> None:
        start.wait()
        reports.append(process_requests(sessions, config, now=NOW))

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert sorted(len(r.steps) for r in reports) != [0, 6]  # both workers took a share
    assert sorted(step.split()[0] for r in reports for step in r.steps) == sorted(ids)
    assert sorted(to for r in smtp_server.received for to in r.recipients) == sorted(recipients)
    assert len(audit(db_session, "smtp.test")) == len(recipients)
    assert verify_audit_chain(db_session).ok


# -- the rows -------------------------------------------------------------------------------------


def test_no_request_or_result_holds_a_secret_named_key(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
) -> None:
    queue(db_session, requester, TEST_EMAIL, {"to": TO})
    queue(db_session, requester, LEASE_REBUILD, {"from": "2026-10-12", "to": "2026-10-12"})
    smtp_server.refuse.add(TO)
    process_requests(sessions, smtp_config(layout, smtp_server), now=NOW)
    db_session.expire_all()
    rows = db_session.scalars(select(AdminRequest)).all()
    assert {row.state for row in rows} <= set(TERMINAL_STATES)
    for row in rows:
        assert no_secret_keys(row.params)
        assert no_secret_keys(row.result)
    for entry in [*audit(db_session, "smtp.test"), *audit(db_session, "leases.rebuild")]:
        assert no_secret_keys(entry.details)
        assert TO not in json.dumps(entry.details)
