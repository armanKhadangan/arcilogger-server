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
from arcilogger_core.admin.messages import MESSAGES
from arcilogger_core.config import Config, SmtpConfig
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SMTP_PASSWORD, SecretStore
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
from arcilogger_worker.alerts.commands import trial_email
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
    """The administrator who asks; a test e-mail may go to her own address (contract 2.9)."""
    user = User(username="ayse", display_name="Ayşe", role="admin", password_hash="!", email=TO)
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


def test_a_test_email_goes_only_to_a_listed_recipient_or_the_requester(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
) -> None:
    """The worker checks the address again (contract 2.9): a row written past the API never
    makes the SMTP server mail an address of its choosing, nor one with a broken header."""
    config = smtp_config(layout, smtp_server)
    duty = "nobet@ornek.example"
    unlisted = queue(db_session, requester, TEST_EMAIL, {"to": duty})
    newline = queue(db_session, requester, TEST_EMAIL, {"to": f"{TO}\n"})
    process_requests(sessions, config, now=NOW)
    assert smtp_server.received == []
    assert {request(db_session, key).error_code for key in (unlisted, newline)} == {
        "validation_failed"
    }
    assert request(db_session, unlisted).result == {
        "ok": False,
        "to": "n***@ornek.example",
        "smtp_code": None,
        "error_text": None,
    }
    set_setting(db_session, "alerts.email_recipients", [duty], updated_by="tolga")
    db_session.commit()
    listed = queue(db_session, requester, TEST_EMAIL, {"to": duty})
    own = queue(db_session, requester, TEST_EMAIL, {"to": "Bora@Ornek.Example"})
    process_requests(sessions, config, now=NOW)
    assert {request(db_session, key).state for key in (listed, own)} == {"succeeded"}
    assert sorted(to for r in smtp_server.received for to in r.recipients) == [
        "Bora@Ornek.Example",
        duty,
    ]


def test_an_unreadable_password_keeps_the_server_paths_out_of_the_result(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The secret store's error names its files: it stays in the worker's log, the result the
    browser sees has a fixed sentence (contract 2.4.6)."""
    layout.root.mkdir(parents=True, exist_ok=True)
    key = layout.root / "secret.key"
    SecretStore.create_key(key)
    SecretStore(key, layout.root / "secrets.json").set(SMTP_PASSWORD, "Gizli-1")
    key.unlink()
    base = smtp_config(layout, smtp_server)
    config = base.model_copy(update={"smtp": base.smtp.model_copy(update={"username": "arci"})})
    request_id = queue(db_session, requester, TEST_EMAIL, {"to": TO})
    with caplog.at_level("WARNING", logger="arcilogger_worker.admin"):
        process_requests(sessions, config, now=NOW)
    row = request(db_session, request_id)
    assert (row.state, row.error_code) == ("failed", "smtp_test_failed")
    assert row.result is not None
    assert row.result["error_text"] == "smtp.password okunamadı / cannot read smtp.password"
    (entry,) = audit(db_session, "smtp.test")
    assert str(layout.root) not in json.dumps([row.result, entry.details])
    assert str(key) in caplog.text  # the detail is in the log, for the administrator
    assert smtp_server.received == []


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


def test_an_unexpected_error_fails_only_its_own_request(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A step that raises is rolled back and recorded as that request's failure, in a
    transaction of its own; the requests queued after it still run in the same run (it would
    otherwise stay queued at the head of the queue for ever)."""
    poisoned = "kirik@ornek.example"
    set_setting(db_session, "alerts.email_recipients", [poisoned], updated_by="tolga")
    db_session.commit()
    first = queue(
        db_session, requester, TEST_EMAIL, {"to": poisoned}, created_at=NOW - timedelta(minutes=1)
    )
    rebuild_id = queue(
        db_session, requester, LEASE_REBUILD, {"from": "2026-10-12", "to": "2026-10-13"}
    )
    second = queue(db_session, requester, TEST_EMAIL, {"to": TO})
    trial = trial_email
    rebuild = rebuild_lease_day

    def broken_mail(config: Config, recipient: str, **kwargs: Any) -> Any:
        if recipient == poisoned:
            raise RuntimeError("Gizli-1 is not for the log")
        return trial(config, recipient, **kwargs)

    def broken_day(sessions: sessionmaker[Session], day: date, **kwargs: Any) -> DayRebuild:
        if day == DAY:
            raise RuntimeError("not recorded either")
        return rebuild(sessions, day, **kwargs)

    monkeypatch.setattr(admin_jobs, "trial_email", broken_mail)
    monkeypatch.setattr(admin_jobs, "rebuild_lease_day", broken_day)
    report = process_requests(sessions, smtp_config(layout, smtp_server), now=NOW)
    assert report.steps == [
        f"{first} {TEST_EMAIL} failed",
        f"{second} {TEST_EMAIL} succeeded",
        f"{rebuild_id} {LEASE_REBUILD} running",
        f"{rebuild_id} {LEASE_REBUILD} running",  # its first day raised: counted as failed
        f"{rebuild_id} {LEASE_REBUILD} failed",
    ]
    row = request(db_session, first)
    assert (row.state, row.terminal, row.error_code) == ("failed", True, "smtp_test_failed")
    assert row.result == {
        "ok": False,
        "to": "k***@ornek.example",
        "smtp_code": None,
        "error_text": "beklenmeyen hata / unexpected error: RuntimeError",
    }
    assert [r.recipients for r in smtp_server.received] == [[TO]]
    rebuilt = request(db_session, rebuild_id)
    assert (rebuilt.state, rebuilt.error_code) == ("failed", "lease_rebuild_failed")
    assert rebuilt.result == {"done_days": 1, "total_days": 2, "failed_days": ["2026-10-12"]}
    codes = [e.details.get("code") for e in audit(db_session, "smtp.test")]
    assert codes == ["smtp_test_failed", None]
    assert audit(db_session, "leases.rebuild")[0].details["failed_days"] == ["2026-10-12"]
    stored = json.dumps([row.result, *(e.details for e in audit(db_session, "smtp.test"))])
    assert "Gizli-1" not in stored
    assert verify_audit_chain(db_session).ok


def test_a_password_smtplib_cannot_send_fails_the_test_email(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
) -> None:
    """smtplib's AUTH PLAIN sends ASCII only: a Turkish password is a failed test e-mail
    (contract Day 0 note WP1-9), not an error that leaves the request queued."""
    layout.root.mkdir(parents=True, exist_ok=True)
    store = SecretStore(layout.root / "secret.key", layout.root / "secrets.json")
    SecretStore.create_key(layout.root / "secret.key")
    store.set(SMTP_PASSWORD, "Gizli-şifre-1")
    base = smtp_config(layout, smtp_server)
    config = base.model_copy(update={"smtp": base.smtp.model_copy(update={"username": "arci"})})
    request_id = queue(db_session, requester, TEST_EMAIL, {"to": TO})
    rebuild_id = queue(
        db_session, requester, LEASE_REBUILD, {"from": "2026-10-12", "to": "2026-10-12"}
    )
    process_requests(sessions, config, now=NOW)
    row = request(db_session, request_id)
    assert (row.state, row.error_code) == ("failed", "smtp_test_failed")
    assert row.result is not None
    assert "ASCII" in row.result["error_text"]
    assert "Gizli" not in json.dumps(row.result)
    assert request(db_session, rebuild_id).state == "succeeded"


def test_every_code_a_worker_request_ends_with_has_its_texts(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    smtp_server: FakeSmtp,
    requester: User,
) -> None:
    """The API shows ``error_code`` with the texts of ``arcilogger_core.admin.messages``
    (contract Day 0 note D0-9): every code the worker writes must have them."""
    set_setting(db_session, "lease.timeout_percent", 1, updated_by="tolga")  # every day fails
    db_session.commit()
    queue(db_session, requester, TEST_EMAIL, {"to": "bora at ornek"})
    queue(db_session, requester, TEST_EMAIL, {"to": TO}, expires_at=NOW)
    queue(db_session, requester, TEST_EMAIL, {"to": TO})
    queue(db_session, requester, LEASE_REBUILD, {"from": "2026-10-12", "to": "2026-10-12"})
    queue(db_session, requester, LEASE_REBUILD, {"from": "2026-10-13", "to": "2026-10-12"})
    process_requests(sessions, smtp_config(layout, None), now=NOW)  # no SMTP server set
    smtp_server.refuse.add(TO)
    queue(db_session, requester, TEST_EMAIL, {"to": TO})
    process_requests(sessions, smtp_config(layout, smtp_server), now=NOW)
    db_session.expire_all()
    codes = {row.error_code for row in db_session.scalars(select(AdminRequest))}
    assert codes == {
        "validation_failed",
        "request_expired",
        "smtp_not_configured",
        "lease_rebuild_failed",
        "smtp_test_failed",
    }
    assert codes <= set(MESSAGES)


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
    assert (row.state, row.terminal, row.error_code) == ("failed", True, "lease_rebuild_failed")
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
    assert (entry.details["done_days"], entry.details["failed_days"], entry.details["code"]) == (
        0,
        ["2026-10-12", "2026-10-13"],
        "lease_rebuild_failed",
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
    set_setting(db_session, "alerts.email_recipients", recipients, updated_by="tolga")
    db_session.commit()
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
