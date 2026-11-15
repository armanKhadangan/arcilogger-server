"""``alerts.webhooks`` (Wave 6 contract §2.4.2, tests T-H1 and T-H2; decisions D10 and D11).

The queue (no backfill, ``min_severity``, ``rules``, ``actions.webhook``), the retry schedule
(1, 5, 15, 60 minutes, then hourly, ``failed`` after 24 h), a permanent 4xx, ``R20-webhook`` and
its resolution, removed webhooks, and what a request and ``last_error`` never hold. Most tests
send through a recording fake; some go through a local HTTPS receiver with its own test CA. The
last part is "Test gönder" (§2.4.4): the worker-channel request ``worker.webhook_test``, its
result and its ``webhook.test`` audit entry. Every value is fictional.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.admin.contract import new_id
from arcilogger_core.admin.messages import MESSAGES
from arcilogger_core.config import Config, WebhookConfig
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStore
from arcilogger_core.webhook import SIGNATURE_HEADER, Target, WebhookError, signature
from arcilogger_core.webhooktesting import WebhookReceiver
from arcilogger_db.models import AdminRequest, Alert, AlertDelivery, AuditLog, User
from arcilogger_db.settings import set_setting
from arcilogger_worker import admin_jobs
from arcilogger_worker.admin_jobs import WEBHOOK_TEST, WEBHOOK_TEST_ACTION, process_requests
from arcilogger_worker.alerts import notify
from arcilogger_worker.alerts.store import open_alert
from arcilogger_worker.alerts.webhooks import (
    RETIRED,
    WEBHOOK_RULE,
    load_starts,
    run_webhooks,
)

NOW = datetime(2026, 11, 14, 9, 0, tzinfo=UTC)
SECRET = "Örnek-HMAC-Anahtarı-0123456789abcdef"
SIEM = WebhookConfig(name="siem", url="https://siem.ornek.example/arcilogger?kanal=guvenlik")
WEB = "https://arcilogger.ornek.example"


@dataclass
class Fake:
    """Records every request and answers with the next outcome (``None``: 200)."""

    outcomes: list[WebhookError | None] = field(default_factory=list)
    sent: list[tuple[Target, dict[str, Any]]] = field(default_factory=list)

    def __call__(self, target: Target, body: bytes) -> int:
        self.sent.append((target, json.loads(body)))
        outcome = (
            self.outcomes[min(len(self.sent), len(self.outcomes)) - 1] if self.outcomes else None
        )
        if outcome is not None:
            raise outcome
        return 200


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(db_engine, expire_on_commit=False)


def config_of(tmp_path: Path, *webhooks: WebhookConfig, secret: str | None = SECRET) -> Config:
    secrets = tmp_path / "secrets"
    secrets.mkdir(exist_ok=True)
    if not (secrets / "secret.key").exists():
        SecretStore.create_key(secrets / "secret.key")
    if secret is not None:
        store = SecretStore(secrets / "secret.key", secrets / "secrets.json")
        for webhook in webhooks:
            store.set(webhook.secret_name, secret)
    return Config(data_dir=tmp_path / "data", secrets_dir=secrets, webhooks=webhooks)


def alert(
    session: Session,
    at: datetime,
    *,
    rule: str = "R01",
    severity: str = "high",
    key: str | None = None,
    **details: Any,
) -> int:
    opened = open_alert(
        session,
        rule=rule,
        severity=severity,
        title="İç adres 172.20.41.17 47 farklı porta / 1 adrese engellendi",
        details={
            "title_en": "Internal address 172.20.41.17 was blocked on 47 ports / 1 addresses",
            "count": 47,
            "actions": {"email": True, "webhook": True},
            **details,
        },
        key=key or f"{rule}:172.20.41.{at.minute}",
        time=at,
        now=at,
    )
    session.commit()
    return int(opened.alert.id)


def run(
    sessions: sessionmaker[Session], config: Config, layout: Layout, at: datetime, fake: Fake
) -> dict[str, Any]:
    return run_webhooks(sessions, config, layout, now=at, sender=fake).details()


def deliveries(session: Session) -> list[AlertDelivery]:
    session.expire_all()
    return list(session.scalars(select(AlertDelivery).order_by(AlertDelivery.id)))


@pytest.fixture(autouse=True)
def web_url(db_session: Session) -> None:
    set_setting(db_session, "alerts.web_base_url", WEB, updated_by="test")
    db_session.commit()


# -- queue ----------------------------------------------------------------------------------------


def test_no_backfill_then_each_new_alert_once(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    config, fake = config_of(tmp_path, SIEM), Fake()
    alert(db_session, NOW - timedelta(minutes=5))  # before the webhook's first run
    first = run(sessions, config, layout, NOW, fake)
    assert (first["started"], first["queued"], fake.sent) == (["siem"], 0, [])
    assert load_starts(layout) == {"siem": NOW}
    new = alert(db_session, NOW + timedelta(seconds=30))
    second = run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    assert (second["queued"], second["sent"]) == (1, 1)
    run(sessions, config, layout, NOW + timedelta(minutes=2), fake)
    assert len(fake.sent) == 1  # queued once, whatever happens
    (row,) = deliveries(db_session)
    assert (row.alert_id, row.kind, row.recipient, row.status) == (new, "webhook", "siem", "sent")
    target, document = fake.sent[0]
    assert target.secret == SECRET
    assert target.url == SIEM.url
    assert document["alert"]["id"] == new
    assert document["alert"]["url"] == f"{WEB}/security?alert={new}"
    assert document["site"] == "arcilogger.ornek.example"


def test_what_a_webhook_takes(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    picky = WebhookConfig(name="picky", url=SIEM.url, min_severity="high", rules=("R01", "R15"))
    config, fake = config_of(tmp_path, picky), Fake()
    run(sessions, config, layout, NOW, fake)
    at = NOW + timedelta(seconds=10)
    taken = alert(db_session, at, rule="R01", severity="critical")
    temel = alert(db_session, at, rule="R15", severity="high", key="R15:fortigate", actions=None)
    alert(db_session, at, rule="R01", severity="medium", key="R01:low")  # below min_severity
    alert(db_session, at, rule="R02", severity="high")  # not listed
    alert(db_session, at, rule="R01", key="R01:off", actions={"email": True, "webhook": False})
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    assert sorted(document["alert"]["id"] for _, document in fake.sent) == [taken, temel]


def test_a_webhook_never_gets_its_own_failure(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    other = WebhookConfig(name="other", url="https://other.ornek.example/hook")
    config, fake = config_of(tmp_path, SIEM, other), Fake()
    run(sessions, config, layout, NOW, fake)
    at = NOW + timedelta(seconds=10)
    alert(db_session, at, rule=WEBHOOK_RULE, key=f"{WEBHOOK_RULE}:siem", actions=None)
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    assert [target.url for target, _ in fake.sent] == [other.url]


# -- retry, failure, R20-webhook ------------------------------------------------------------------


def test_the_retry_schedule_then_failed_after_24_hours(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    config, fake = config_of(tmp_path, SIEM), Fake([WebhookError("HTTP 503", status=503)])
    run(sessions, config, layout, NOW, fake)
    alert(db_session, NOW + timedelta(seconds=10))
    at = NOW + timedelta(minutes=1)
    waits = []
    while True:
        run(sessions, config, layout, at, fake)
        (row,) = deliveries(db_session)
        if row.status != "queued":
            break
        assert row.next_attempt_at is not None
        waits.append(row.next_attempt_at - at)
        assert not run(sessions, config, layout, row.next_attempt_at - timedelta(seconds=1), fake)[
            "retried"
        ]  # not before its time
        at = row.next_attempt_at
    minutes = [int(wait.total_seconds() // 60) for wait in waits]
    assert minutes[:5] == [1, 5, 15, 60, 60]
    assert set(minutes[4:]) == {60}
    assert row.status == "failed"
    assert at - row.created_at >= timedelta(hours=24)
    assert at - row.created_at < timedelta(hours=25)
    (failure,) = db_session.scalars(select(Alert).where(Alert.rule == WEBHOOK_RULE))
    assert (failure.dedup_key, failure.severity, failure.state) == (
        "R20-webhook:siem",
        "high",
        "open",
    )
    assert failure.title == "Webhook teslim edilemiyor: siem"
    assert failure.details["title_en"] == "Webhook cannot deliver: siem"


def test_a_permanent_refusal_fails_at_once_and_the_next_success_resolves(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    gone = WebhookError("HTTP 404: no such hook", status=404, permanent=True)
    config, fake = config_of(tmp_path, SIEM), Fake([gone, None])
    run(sessions, config, layout, NOW, fake)
    alert(db_session, NOW + timedelta(seconds=10))
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    (row,) = deliveries(db_session)
    assert (row.status, row.attempts, row.last_error) == ("failed", 1, "HTTP 404: no such hook")
    (failure,) = db_session.scalars(select(Alert).where(Alert.rule == WEBHOOK_RULE))
    assert failure.state == "open"
    assert failure.details["permanent"] is True
    alert(db_session, NOW + timedelta(minutes=2))
    run(sessions, config, layout, NOW + timedelta(minutes=3), fake)
    db_session.expire_all()
    assert failure.state == "resolved"
    assert [row.status for row in deliveries(db_session)] == ["failed", "sent"]


@pytest.mark.parametrize("status", [408, 429, 500])
def test_these_refusals_are_tried_again(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    tmp_path: Path,
    status: int,
) -> None:
    error = WebhookError(f"HTTP {status}", status=status, permanent=False)
    config, fake = config_of(tmp_path, SIEM), Fake([error])
    run(sessions, config, layout, NOW, fake)
    alert(db_session, NOW + timedelta(seconds=10))
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    (row,) = deliveries(db_session)
    assert (row.status, row.attempts) == ("queued", 1)
    assert not db_session.scalars(select(Alert).where(Alert.rule == WEBHOOK_RULE)).all()


def test_an_unreachable_target_holds_the_other_rows(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    config, fake = config_of(tmp_path, SIEM), Fake([WebhookError("connection refused")])
    run(sessions, config, layout, NOW, fake)
    for second in (10, 20, 30):
        alert(db_session, NOW + timedelta(seconds=second), key=f"R01:172.20.41.{second}")
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    assert len(fake.sent) == 1
    assert [row.attempts for row in deliveries(db_session)] == [1, 0, 0]


def test_a_missing_secret_is_a_failed_attempt_that_sends_nothing(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    config, fake = config_of(tmp_path, SIEM, secret=None), Fake()
    run(sessions, config, layout, NOW, fake)
    alert(db_session, NOW + timedelta(seconds=10))
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    assert fake.sent == []
    (row,) = deliveries(db_session)
    assert (row.status, row.attempts) == ("queued", 1)
    assert row.last_error is not None
    assert "webhook.siem.secret" in row.last_error


@pytest.mark.parametrize("kind", ["teams", "slack"])
def test_teams_and_slack_read_their_url_from_the_secret_store(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    tmp_path: Path,
    kind: str,
) -> None:
    hook = WebhookConfig(name="kanal", format=kind)
    config, fake = config_of(tmp_path, hook, secret=None), Fake()
    url = "https://hooks.ornek.example/services/T000/B000/Gizli"
    SecretStore(config.secrets_dir / "secret.key", config.secrets_dir / "secrets.json").set(
        hook.url_name, url
    )
    run(sessions, config, layout, NOW, fake)
    alert(db_session, NOW + timedelta(seconds=10))
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    ((target, document),) = fake.sent
    assert (target.url, target.secret, target.format) == (url, None, kind)
    assert ("text" in document) is (kind == "slack")


def test_a_removed_webhook_retires_its_rows_and_its_failure(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    config = config_of(tmp_path, SIEM)
    fake = Fake([WebhookError("HTTP 404", status=404, permanent=True), WebhookError("down")])
    run(sessions, config, layout, NOW, fake)
    alert(db_session, NOW + timedelta(seconds=10), key="R01:172.20.41.10")
    alert(db_session, NOW + timedelta(seconds=20), key="R01:172.20.41.20")
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    assert [row.status for row in deliveries(db_session)] == ["failed", "queued"]
    removed = config.model_copy(update={"webhooks": ()})
    report = run(sessions, removed, layout, NOW + timedelta(minutes=2), fake)
    assert report["retired"] == 1
    assert [(row.status, row.last_error) for row in deliveries(db_session)][1] == (
        "failed",
        RETIRED,
    )
    (failure,) = db_session.scalars(select(Alert).where(Alert.rule == WEBHOOK_RULE))
    db_session.refresh(failure)
    assert failure.state == "resolved"
    assert load_starts(layout) == {}  # switched on again later: a new start, no backfill


# -- D10 and D11: the e-mail leaves webhook rows alone --------------------------------------------


def test_the_email_job_leaves_webhook_rows_alone(
    sessions: sessionmaker[Session], db_session: Session, layout: Layout, tmp_path: Path
) -> None:
    config, fake = config_of(tmp_path, SIEM), Fake([WebhookError("down")])
    run(sessions, config, layout, NOW, fake)
    alert(db_session, NOW + timedelta(seconds=10))
    run(sessions, config, layout, NOW + timedelta(minutes=1), fake)
    policy = notify.Policy(
        recipients=("guvenlik@ornek.example",),
        min_severity="high",
        max_per_hour=20,
        digest=timedelta(0),
        resolved=False,
        web_url=WEB,
    )
    with sessions() as session:
        notify.enqueue(session, policy, now=NOW + timedelta(days=2))
        session.commit()
    webhook_row = next(row for row in deliveries(db_session) if row.kind == "webhook")
    assert webhook_row.status == "queued"  # not retired as an unknown e-mail recipient


def test_an_alert_whose_rule_does_not_email_is_not_emailed(
    sessions: sessionmaker[Session], db_session: Session
) -> None:
    mailed = alert(db_session, NOW, key="R01:a")
    alert(db_session, NOW, key="R01:b", actions={"email": False, "webhook": True})
    plain = alert(db_session, NOW, rule="R15", key="R15:fortigate", actions=None)
    policy = notify.Policy(
        recipients=("guvenlik@ornek.example",),
        min_severity="high",
        max_per_hour=20,
        digest=timedelta(0),
        resolved=False,
        web_url=WEB,
    )
    with sessions() as session:
        notify.enqueue(session, policy, now=NOW + timedelta(minutes=1))
        session.commit()
    assert sorted(row.alert_id for row in deliveries(db_session)) == sorted([mailed, plain])


# -- through HTTPS, and what is never sent or kept -------------------------------------------------


@pytest.fixture
def receiver(tmp_path: Path) -> Iterator[WebhookReceiver]:
    with WebhookReceiver(tmp_path / "tls") as found:
        yield found


def test_a_signed_request_through_https_without_names_or_log_lines(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    tmp_path: Path,
    receiver: WebhookReceiver,
) -> None:
    hook = WebhookConfig(name="siem", url=receiver.url, ca_file=receiver.ca_file)
    config = config_of(tmp_path, hook)
    run_webhooks(sessions, config, layout, now=NOW)
    person_alert = alert(
        db_session,
        NOW + timedelta(seconds=10),
        rule="R13",
        key="R13:ip:172.20.41.17",
        person_id=7,
        raw={"line": 12, "source": "fortigate"},
    )
    report = run_webhooks(sessions, config, layout, now=NOW + timedelta(minutes=1))
    assert report.sent == 1
    (request,) = receiver.received
    timestamp = int(request.headers["X-Arcilogger-Timestamp"])
    assert request.headers[SIGNATURE_HEADER] == signature(SECRET, timestamp, request.body)
    document = json.loads(request.body)
    assert document["alert"]["id"] == person_alert
    assert set(document["alert"]) == {
        "id", "rule", "severity", "state", "title_tr", "title_en", "time", "count", "url"
    }  # fmt: skip
    text = request.body.decode()
    for never in ("person", "raw", "fortigate", SECRET):
        assert never not in text


def test_last_error_holds_no_secret_and_no_query(
    sessions: sessionmaker[Session],
    db_session: Session,
    layout: Layout,
    tmp_path: Path,
    receiver: WebhookReceiver,
) -> None:
    hook = WebhookConfig(name="siem", url=receiver.url, ca_file=receiver.ca_file)
    config = config_of(tmp_path, hook)
    receiver.statuses = [400]
    receiver.reply = f"bad request to {receiver.url} signed with {SECRET}".encode()
    run_webhooks(sessions, config, layout, now=NOW)
    alert(db_session, NOW + timedelta(seconds=10))
    run_webhooks(sessions, config, layout, now=NOW + timedelta(minutes=1))
    (row,) = deliveries(db_session)
    assert row.status == "failed"
    assert row.last_error is not None
    assert row.last_error.startswith("HTTP 400: bad request to ***")
    for never in (SECRET, "kanal=guvenlik", "/arcilogger"):
        assert never not in row.last_error


# -- "Test gönder": the worker-channel request worker.webhook_test (§2.4.4) -----------------------


def queue_test(session: Session, name: object, *, expires_at: datetime | None = None) -> str:
    """A ``worker.webhook_test`` row as the API queues it (``params.name``)."""
    user = session.scalars(select(User).where(User.username == "ayse")).first()
    if user is None:
        user = User(username="ayse", display_name="Ayşe", role="admin", password_hash="!")
        session.add(user)
        session.flush()
    row = AdminRequest(
        id=new_id(),
        kind=WEBHOOK_TEST,
        channel="worker",
        state="queued",
        requested_by_id=user.id,
        requested_by=user.username,
        created_at=NOW - timedelta(seconds=5),
        expires_at=expires_at,
        params={"name": name},
    )
    session.add(row)
    session.commit()
    return row.id


def outcome_of(session: Session, request_id: str) -> tuple[AdminRequest, AuditLog]:
    session.expire_all()
    row = session.get(AdminRequest, request_id)
    assert row is not None
    (entry,) = session.scalars(select(AuditLog).where(AuditLog.action == WEBHOOK_TEST_ACTION))
    assert entry.details["request"] == request_id
    return row, entry


def test_the_test_notification_is_signed_and_sent_by_the_worker(
    sessions: sessionmaker[Session], db_session: Session, tmp_path: Path, receiver: WebhookReceiver
) -> None:
    hook = WebhookConfig(name="siem", url=receiver.url, ca_file=receiver.ca_file)
    set_setting(db_session, "alerts.web_base_url", WEB, updated_by="test")
    db_session.commit()
    request_id = queue_test(db_session, "siem")
    report = process_requests(sessions, config_of(tmp_path, hook), now=NOW)
    assert report.steps == [f"{request_id} {WEBHOOK_TEST} succeeded"]
    row, entry = outcome_of(db_session, request_id)
    assert (row.state, row.terminal, row.error_code) == ("succeeded", True, None)
    assert row.result == {"ok": True, "status": 200, "error_text": None}
    assert entry.details == {
        "name": "siem",
        "ok": True,
        "status": 200,
        "request": request_id,
        "requested_by": {"user_id": row.requested_by_id, "username": "ayse"},
    }
    (sent,) = receiver.received
    timestamp = int(sent.headers["X-Arcilogger-Timestamp"])
    assert timestamp == int(NOW.timestamp())
    assert sent.headers[SIGNATURE_HEADER] == signature(SECRET, timestamp, sent.body)
    document = json.loads(sent.body)
    assert document["site"] == "arcilogger.ornek.example"
    assert document["alert"] == {
        "id": 0,
        "rule": "TEST",
        "severity": "info",
        "state": "open",
        "title_tr": "ARCILOGGER deneme bildirimi",
        "title_en": "ARCILOGGER test notification",
        "time": "2026-11-14T12:00:00+03:00",
        "count": 1,
        "url": WEB,
    }


def test_a_refused_test_keeps_the_status_and_the_cleaned_reply(
    sessions: sessionmaker[Session], db_session: Session, tmp_path: Path, receiver: WebhookReceiver
) -> None:
    hook = WebhookConfig(name="siem", url=receiver.url, ca_file=receiver.ca_file)
    receiver.statuses = [404]
    receiver.reply = f"no channel at {receiver.url} for {SECRET}".encode()
    request_id = queue_test(db_session, "siem")
    process_requests(sessions, config_of(tmp_path, hook), now=NOW)
    row, entry = outcome_of(db_session, request_id)
    assert (row.state, row.error_code) == ("failed", "webhook_test_failed")
    assert row.result is not None
    assert (row.result["ok"], row.result["status"]) == (False, 404)
    assert row.result["error_text"].startswith("HTTP 404: no channel at ***")
    assert (entry.details["ok"], entry.details["status"], entry.details["code"]) == (
        False,
        404,
        "webhook_test_failed",
    )
    stored = json.dumps([row.result, row.params, entry.details])
    for never in (SECRET, "kanal=guvenlik", "/arcilogger"):
        assert never not in stored
    assert MESSAGES["webhook_test_failed"] == (
        "Deneme bildirimi gönderilemedi.",
        "The test notification could not be sent.",
    )


@pytest.mark.parametrize("stored", ["", None], ids=["teams-url-missing", "secret-missing"])
def test_a_missing_secret_or_url_fails_the_test_without_a_request(
    sessions: sessionmaker[Session],
    db_session: Session,
    tmp_path: Path,
    stored: str | None,
) -> None:
    """``secret-missing``: a ``generic`` webhook without its HMAC secret; ``teams-url-missing``:
    a Teams webhook whose URL is not in the secret store. The text names the stored secret and
    the command that sets it, never a value."""
    hook = SIEM if stored is None else WebhookConfig(name="teams-bt", format="teams")
    request_id = queue_test(db_session, hook.name)
    process_requests(sessions, config_of(tmp_path, hook, secret=None), now=NOW)
    row, _ = outcome_of(db_session, request_id)
    assert (row.state, row.error_code) == ("failed", "webhook_test_failed")
    assert row.result is not None
    name = hook.secret_name if stored is None else hook.url_name
    assert row.result["error_text"].startswith(f"{name}: ")
    assert f"arcilogger-core secret set {name}" in row.result["error_text"]


@pytest.mark.parametrize("name", ["eski", 7], ids=["removed", "not-a-name"])
def test_a_webhook_no_longer_in_the_file_fails_the_test(
    sessions: sessionmaker[Session], db_session: Session, tmp_path: Path, name: object
) -> None:
    """Removed from ``[[webhooks]]`` after the API queued the test (a row written past the API:
    not a name at all): the test fails, with the one failure code of the request."""
    request_id = queue_test(db_session, name)
    process_requests(sessions, config_of(tmp_path, SIEM), now=NOW)
    row, entry = outcome_of(db_session, request_id)
    assert (row.state, row.error_code) == ("failed", "webhook_test_failed")
    assert row.result == {
        "ok": False,
        "status": None,
        "error_text": "the webhook is no longer in [[webhooks]]",
    }
    found = name if isinstance(name, str) else None
    assert (entry.details["name"], entry.details["code"]) == (found, "webhook_test_failed")


def test_an_expired_test_sends_nothing(
    sessions: sessionmaker[Session], db_session: Session, tmp_path: Path, receiver: WebhookReceiver
) -> None:
    hook = WebhookConfig(name="siem", url=receiver.url, ca_file=receiver.ca_file)
    request_id = queue_test(db_session, "siem", expires_at=NOW - timedelta(seconds=1))
    process_requests(sessions, config_of(tmp_path, hook), now=NOW)
    row, entry = outcome_of(db_session, request_id)
    assert (row.state, row.error_code) == ("expired", "request_expired")
    assert (entry.details["name"], entry.details["ok"]) == ("siem", False)
    assert receiver.received == []


def test_an_unexpected_error_fails_only_that_test(
    sessions: sessionmaker[Session],
    db_session: Session,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def broken(*args: object, **kwargs: object) -> int:
        raise RuntimeError("beklenmedik")

    monkeypatch.setattr(admin_jobs, "send", broken)
    request_id = queue_test(db_session, "siem")
    process_requests(sessions, config_of(tmp_path, SIEM), now=NOW)
    row, entry = outcome_of(db_session, request_id)
    assert (row.state, row.error_code) == ("failed", "webhook_test_failed")
    assert row.result is not None
    assert row.result["error_text"] == "beklenmeyen hata / unexpected error: RuntimeError"
    assert entry.details["code"] == "webhook_test_failed"
