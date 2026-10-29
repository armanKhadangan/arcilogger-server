"""``alerts.notify``: alert e-mail through a real SMTP server on 127.0.0.1 — queueing, digest,
rate limit with its summary, retries and failures (connection refused, 5xx, 4xx, timeout), the
single ``R20-email`` alert, crash safety and the content rule (no log line, no person name)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config, SmtpConfig
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SMTP_PASSWORD, SecretStore
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, AlertDelivery
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts import notify
from arcilogger_worker.alerts.commands import list_alerts, send_test_email
from arcilogger_worker.alerts.mail import MailItem, build_message
from arcilogger_worker.alerts.notify import (
    BACKOFF,
    MAX_ATTEMPTS,
    NotifyReport,
    run_notify,
    web_base_url,
)
from arcilogger_worker.alerts.smtptesting import FakeSmtp
from arcilogger_worker.alerts.store import open_alert, resolve
from arcilogger_worker.alerts.texts import describe

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
TO = "bilgi-islem@example.com"
OTHER = "nobet@example.com"
SENDER = "arcilogger@example.com"
#: A person, a device owner and a raw FortiGate line: none of it may reach an e-mail.
PERSON = "Ayşe Yılmaztürk"
RAW = (
    '<189>date=2026-10-19 time=11:58:01 devname="FIREWALL-01" srcip=172.20.42.57 '
    'user="ayse.yilmazturk" dstip=203.0.113.10 action="deny"'
)
SMTP_SECRET = "test-only-smtp-secret-7Qx"  # gitleaks:allow


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


def smtp_config(layout: Layout, fake: FakeSmtp, **smtp: Any) -> Config:
    settings: dict[str, Any] = {
        "enabled": True,
        "host": fake.host,
        "port": fake.port,
        "security": "none",
        "from_address": SENDER,
        "timeout_seconds": 2,
        **smtp,
    }
    return Config(
        data_dir=layout.root,
        secrets_dir=layout.root,
        collector={"bind": "172.20.20.15"},
        smtp=SmtpConfig(**settings),
    )


def policy(session: Session, **values: Any) -> None:
    defaults: dict[str, Any] = {
        "alerts.email_recipients": [TO],
        "alerts.email_digest_seconds": 0,
    }
    for key, value in {**defaults, **values}.items():
        set_setting(session, key, value, updated_by="tolga")
    session.commit()


def raise_alert(
    session: Session,
    *,
    rule: str = "R15",
    severity: str = "high",
    key: str | None = "R15:fortigate",
    details: dict[str, Any] | None = None,
    title: str = "Log kaynağı sessiz: fortigate",
    at: datetime = NOW,
    created: datetime | None = None,
) -> Alert:
    facts = {"source": "fortigate", "threshold_minutes": 10, "last_seen": None}
    opened = open_alert(
        session,
        rule=rule,
        severity=severity,
        title=title,
        details=details if details is not None else facts,
        key=key,
        time=at,
        now=created or at,
    )
    session.commit()
    return opened.alert


def notify_at(sessions: sessionmaker[Session], config: Config, now: datetime = NOW) -> NotifyReport:
    return run_notify(sessions, config, now=now)


def deliveries(session: Session) -> list[AlertDelivery]:
    session.expire_all()
    return list(session.scalars(select(AlertDelivery).order_by(AlertDelivery.id)))


def email_alerts(session: Session) -> list[Alert]:
    session.expire_all()
    return list(session.scalars(select(Alert).where(Alert.rule == "R20-email")))


# -- one alert, the content -----------------------------------------------------------------------


def test_an_alert_is_mailed_once_to_each_recipient(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server)
    policy(db_session, **{"alerts.email_recipients": [TO, OTHER]})
    alert = raise_alert(db_session)
    report = notify_at(sessions, config)
    assert (report.queued, report.emails, report.sent) == (2, 2, 2)
    assert notify_at(sessions, config, NOW + timedelta(minutes=1)).emails == 0
    assert [r.recipients for r in smtp_server.received] == [[TO], [OTHER]]
    mail = smtp_server.received[0]
    assert mail.sender == SENDER
    message = mail.message
    assert message["Subject"] == "[ARCILOGGER] Yüksek: Log kaynağı sessiz — fortigate"
    assert message["From"] == SENDER
    assert message["To"] == TO
    text = mail.part("plain")
    assert text.index("ARCILOGGER uyarısı") < text.index("ARCILOGGER alert")  # Turkish first
    for expected in (
        "[Yüksek] R15 — Log kaynağı sessiz",
        "Zaman: 19.10.2026 12:00:00 (Europe/Istanbul)",
        "Kaynak: fortigate",
        "Durum: Açık",
        "fortigate kaynağından 10 dakikadan uzun süredir mesaj gelmedi.",
        "Yapılacak: Cihazın açık",
        "[High] R15 — Log source silent",
        "What to do: Check that the device",
        "Web arayüzü: https://172.20.20.15",
    ):
        assert expected in text, expected
    page = mail.part("html")
    assert "https://172.20.20.15" in page
    assert "<img" not in page
    assert "src=" not in page
    assert sorted(set(_links(page))) == ["https://172.20.20.15"]
    rows = deliveries(db_session)
    assert [(r.alert_id, r.kind, r.recipient, r.status, r.attempts) for r in rows] == [
        (alert.id, "new", TO, "sent", 1),
        (alert.id, "new", OTHER, "sent", 1),
    ]
    # A long Message-ID line is folded on the wire; the parsed value then starts with the
    # folding space, which is not part of the id.
    assert rows[0].message_id == message["Message-ID"].strip()


def _links(page: str) -> list[str]:
    return [part.split('"', 1)[0] for part in page.split('href="')[1:]]


def test_the_content_never_carries_log_lines_or_names(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    """Spec 0: no log content leaves the server. The alerts below carry a raw log line, a
    person and a device owner in their titles and details; none of it reaches the e-mail."""
    config = smtp_config(layout, smtp_server)
    policy(db_session)
    tainted = {
        "source": "fortigate",
        "threshold_minutes": 10,
        "person": PERSON,
        "owner": PERSON,
        "raw": RAW,
        "message": RAW,
        "error": f"line 7: {RAW}",
        "mac": "f0:1f:af:00:00:01",
    }
    raise_alert(db_session, title=f"Log kaynağı sessiz: {PERSON} {RAW}", details=tainted)
    raise_alert(
        db_session,
        rule="R20-seal",
        key="R20-seal:2026-10-18",
        title=f"Mühürleme başarısız: {RAW}",
        details={"day": "2026-10-18", "attempts": 2, "error": RAW, "person": PERSON},
    )
    raise_alert(
        db_session,
        rule="custom-rule",
        severity="critical",
        key=None,
        title=f"{PERSON}: {RAW}",
        details={"source": PERSON, "raw": RAW},
    )
    raise_alert(
        db_session,
        rule="R20-disk",
        key="R20-disk:D:",
        title=RAW,
        details={"drive": f"D: {PERSON}", "used_percent": 91, "threshold_percent": 80},
    )
    assert notify_at(sessions, config).emails == 1
    (mail,) = smtp_server.received
    whole = mail.data.decode("ascii", "replace")
    for part in (mail.message["Subject"], mail.part("plain"), mail.part("html"), whole):
        for secret in (PERSON, "Ayşe", "Yılmaztürk", "ayse.yilmazturk", RAW, "srcip=", "<189>"):
            assert secret not in part, secret
        assert "f0:1f:af" not in part
    text = mail.part("plain")
    assert "custom-rule — ARCILOGGER uyarısı" in text  # an unknown rule: the generic text
    assert "18.10.2026 günü mühürlenemedi (2. deneme)." in text
    assert "Ayrıntılar ARCILOGGER'da" in text  # the disk alert's drive did not pass the check


def test_low_severity_old_and_email_alerts_are_not_mailed(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server)
    policy(db_session)
    raise_alert(db_session, rule="new_source", severity="medium", key="new_source:x")
    raise_alert(db_session, rule="R20-email", key="R20-email")  # never mailed: no loop
    # An old alerts.jsonl line imported now (the first import after an upgrade).
    raise_alert(db_session, key="R15:switch-1", at=NOW - timedelta(days=2), created=NOW)
    resolved = raise_alert(db_session, key="R15:switch-2")
    resolve(resolved, reason="messages_again", now=NOW)
    db_session.commit()
    assert notify_at(sessions, config).queued == 0
    assert smtp_server.received == []
    policy(db_session, **{"alerts.email_min_severity": "medium"})
    assert notify_at(sessions, config).queued == 1


def test_a_problem_still_going_on_is_mailed_however_long_ago_it_began(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    """After a worker or database outage the import opens the alert with its first line's old
    time; the check that finds the problem again now makes it current."""
    config = smtp_config(layout, smtp_server)
    policy(db_session)
    facts = {"credits": 120, "threshold": 500}

    def credit(at: datetime, *, repeat: bool) -> Alert:
        opened = open_alert(
            db_session,
            rule="R20-credit",
            severity="high",
            title="Kamu SM kontörü azaldı",
            details=facts,
            key="R20-credit",
            time=at,
            repeat=repeat,
            now=NOW,
        )
        db_session.commit()
        return opened.alert

    alert = credit(NOW - timedelta(hours=30), repeat=True)  # imported: the first line
    assert notify_at(sessions, config).queued == 0  # history only
    credit(NOW, repeat=False)  # the credit check still finds it
    assert alert.details["seen_at"] == NOW.isoformat()
    assert notify_at(sessions, config).queued == 1
    assert [r.alert_id for r in deliveries(db_session)] == [alert.id]
    # A check that runs every few minutes records it at most once an hour.
    credit(NOW + timedelta(minutes=55), repeat=False)
    assert alert.details["seen_at"] == NOW.isoformat()
    credit(NOW + timedelta(minutes=60), repeat=False)
    assert alert.details["seen_at"] == (NOW + timedelta(minutes=60)).isoformat()


def test_an_alert_older_than_a_day_is_mailed_on_a_new_repeat(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    """E-mail set up on day 3 of a problem the Seal task reports once a day."""
    config = smtp_config(layout, smtp_server)
    began = NOW - timedelta(days=3)
    row = raise_alert(
        db_session,
        rule="R20-seal-config",
        key="R20-seal-config:credentials",
        details={"cause": "credentials"},
        at=began,
        created=began,
    )
    policy(db_session)
    assert notify_at(sessions, config).queued == 0  # last reported 3 days ago
    open_alert(
        db_session,
        rule="R20-seal-config",
        severity="high",
        title="again",
        details={"cause": "credentials"},
        key="R20-seal-config:credentials",
        time=NOW - timedelta(hours=1),
        repeat=True,
        now=NOW,
    )
    db_session.commit()
    assert notify_at(sessions, config).queued == 1
    assert deliveries(db_session)[0].alert_id == row.id


def test_disabled_or_without_recipients_nothing_is_queued(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    raise_alert(db_session)
    disabled = smtp_config(layout, smtp_server, enabled=False)
    assert notify_at(sessions, disabled).details() == {
        **NotifyReport().details(),
        "_skipped": True,
        "reason": "[smtp] enabled = false",
    }
    assert notify_at(sessions, smtp_config(layout, smtp_server)).skipped == (
        "alerts.email_recipients is empty"
    )
    assert deliveries(db_session) == []


# -- digest, resolved, rate limit -----------------------------------------------------------------


def test_alerts_within_the_digest_window_go_into_one_email(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server)
    policy(db_session, **{"alerts.email_digest_seconds": 300})
    raise_alert(db_session)
    assert notify_at(sessions, config).waiting == [TO]
    later = NOW + timedelta(minutes=2)
    raise_alert(
        db_session,
        rule="R20-disk",
        severity="critical",
        key="R20-disk:D:",
        details={"drive": "D:", "used_percent": 96, "threshold_percent": 80},
        at=later,
    )
    assert notify_at(sessions, config, later).emails == 0
    assert notify_at(sessions, config, NOW + timedelta(minutes=4, seconds=59)).emails == 0
    report = notify_at(sessions, config, NOW + timedelta(minutes=5))
    assert (report.emails, report.sent) == (1, 2)
    (mail,) = smtp_server.received
    assert mail.message["Subject"] == "[ARCILOGGER] Kritik: 2 uyarı"
    text = mail.part("plain")
    assert text.index("R20-disk") < text.index("R15")  # most severe first
    assert "D: diski %96 dolu (eşik %80)." in text


def test_a_resolved_alert_gets_a_resolved_email(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server)
    policy(db_session)
    alert = raise_alert(db_session)
    notify_at(sessions, config)
    resolve(alert, reason="messages_again", now=NOW + timedelta(minutes=3))
    db_session.commit()
    report = notify_at(sessions, config, NOW + timedelta(minutes=4))
    assert (report.queued_resolved, report.emails) == (1, 1)
    message = smtp_server.received[1].message
    assert message["Subject"] == "[ARCILOGGER] Çözüldü: Log kaynağı sessiz — fortigate"
    text = smtp_server.received[1].part("plain")
    assert "Durum: Çözüldü" in text
    assert "Çözüldü: 19.10.2026 12:03:00" in text
    assert "Yapılacak" not in text
    # Switched off: no resolved e-mail.
    policy(db_session, **{"alerts.email_resolved": False})
    other = raise_alert(db_session, key="R15:core-r1", at=NOW + timedelta(minutes=5))
    notify_at(sessions, config, NOW + timedelta(minutes=5))
    resolve(other, reason="messages_again", now=NOW + timedelta(minutes=6))
    db_session.commit()
    assert notify_at(sessions, config, NOW + timedelta(minutes=7)).queued_resolved == 0


def test_the_hourly_limit_and_its_summary(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server)
    policy(db_session, **{"alerts.email_max_per_hour": 2})
    # Five silent sources (three switches, the router, the firewall): two e-mails, three held.
    for minute, name in enumerate(("switch-1", "switch-2", "switch-3", "core-r1", "fortigate")):
        moment = NOW + timedelta(minutes=minute)
        raise_alert(db_session, key=f"R15:{name}", at=moment)
        notify_at(sessions, config, moment)
    assert len(smtp_server.received) == 2
    rows = deliveries(db_session)
    assert [r.status for r in rows] == ["sent", "sent", "suppressed", "suppressed", "suppressed"]
    # An hour after the first e-mail the limit allows one again: the summary goes out.
    report = notify_at(sessions, config, NOW + timedelta(minutes=60))
    assert report.emails == 1
    summary = smtp_server.received[2]
    assert summary.message["Subject"] == "[ARCILOGGER] E-posta sınırı: 3 uyarı gönderilmedi"
    text = summary.part("plain")
    assert (
        "Saatlik e-posta sınırı (2) aşıldığı için 3 uyarı e-postayla gönderilmedi (R15: 3)" in text
    )
    assert "3 alert(s) were not e-mailed because the hourly e-mail limit (2)" in text
    rows = deliveries(db_session)
    assert {r.message_id for r in rows[2:]} == {summary.message["Message-ID"].strip()}
    assert notify_at(sessions, config, NOW + timedelta(minutes=61)).emails == 0  # reported once


# -- failures and retries -------------------------------------------------------------------------


def test_connection_refused_is_retried_with_backoff_then_one_r20_email(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    closed_port: int,
) -> None:
    config = smtp_config(layout, smtp_server, port=closed_port)
    policy(db_session)
    raise_alert(db_session)
    raise_alert(db_session, key="R15:core-r1")
    moment = NOW
    for attempt in range(1, MAX_ATTEMPTS + 1):
        report = notify_at(sessions, config, moment)
        assert report.emails == 0
        # Windows retries a refused loopback connection for about 2 s, as long as the test's
        # timeout, so there the error reads "timeout": either way it is a connection failure.
        assert any(text in report.errors[0] for text in ("ConnectionRefusedError", "timeout"))
        row = deliveries(db_session)[0]
        assert row.attempts == attempt
        assert row.last_error is not None
        if attempt < MAX_ATTEMPTS:
            assert row.status == "queued"
            assert row.next_attempt_at == moment + BACKOFF[min(attempt, len(BACKOFF)) - 1]
            assert notify_at(sessions, config, moment + timedelta(seconds=30)).errors == []
            moment = row.next_attempt_at
    assert [r.status for r in deliveries(db_session)] == ["failed", "failed"]
    (email_alert,) = email_alerts(db_session)
    assert (email_alert.state, email_alert.severity) == ("open", "high")
    assert email_alert.details["alerts"] == 2
    # The R20-email alert itself is never e-mailed; the server comes back for a new alert.
    working = smtp_config(layout, smtp_server)
    raise_alert(db_session, key="R15:switch-1", at=moment)
    assert notify_at(sessions, working, moment).sent == 1
    (email_alert,) = email_alerts(db_session)
    assert email_alert.state == "resolved"
    assert [r.recipients for r in smtp_server.received] == [[TO]]


def test_a_server_failure_stops_the_run_for_the_other_recipients(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    closed_port: int,
) -> None:
    """One timeout per run, not one per recipient: the worker loop is not held up."""
    config = smtp_config(layout, smtp_server, port=closed_port)
    policy(db_session, **{"alerts.email_recipients": [TO, OTHER]})
    raise_alert(db_session)
    report = notify_at(sessions, config)
    assert len(report.errors) == 1
    rows = {r.recipient: r for r in deliveries(db_session)}
    assert (rows[TO].attempts, rows[OTHER].attempts) == (1, 0)
    working = smtp_config(layout, smtp_server)
    assert notify_at(sessions, working, NOW + timedelta(minutes=1)).sent == 2


def test_5xx_on_rcpt_fails_at_once_and_other_recipients_still_get_it(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server)
    policy(db_session, **{"alerts.email_recipients": [TO, OTHER]})
    smtp_server.refuse.add(TO)
    raise_alert(db_session)
    report = notify_at(sessions, config)
    assert (report.emails, report.failed) == (1, 1)
    rows = {r.recipient: r for r in deliveries(db_session)}
    assert rows[TO].status == "failed"
    assert rows[TO].last_error == "recipient refused: 550 5.1.1 mailbox unavailable"
    assert rows[OTHER].status == "sent"
    assert [m.recipients for m in smtp_server.received] == [[OTHER]]
    (email_alert,) = email_alerts(db_session)
    assert email_alert.details["permanent"] is True
    # A second permanent failure counts on the same R20-email alert.
    raise_alert(db_session, key="R15:switch-1")
    notify_at(sessions, config, NOW + timedelta(minutes=1))
    (email_alert,) = email_alerts(db_session)
    assert email_alert.details["repeats"] == 1


def test_r20_email_is_resolved_once_its_failing_recipients_are_removed(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    """A wrong address is fixed by taking it out of the list: nothing is sent to it again."""
    third = "yanlis@example.com"
    config = smtp_config(layout, smtp_server)
    policy(db_session, **{"alerts.email_recipients": [TO, OTHER, third]})
    smtp_server.refuse.update({TO, third})
    raise_alert(db_session)
    notify_at(sessions, config)
    (email_alert,) = email_alerts(db_session)
    assert email_alert.details["recipients"] == sorted([TO, third])
    raise_alert(db_session, key="R15:switch-1")  # OTHER gets it; the others still fail
    notify_at(sessions, config, NOW + timedelta(minutes=1))
    (email_alert,) = email_alerts(db_session)
    assert (email_alert.state, email_alert.details["recipients"]) == ("open", sorted([TO, third]))
    policy(db_session, **{"alerts.email_recipients": [TO, OTHER]})
    notify_at(sessions, config, NOW + timedelta(minutes=2))
    (email_alert,) = email_alerts(db_session)
    assert (email_alert.state, email_alert.details["recipients"]) == ("open", [TO])
    policy(db_session, **{"alerts.email_recipients": [OTHER]})
    notify_at(sessions, config, NOW + timedelta(minutes=3))
    (email_alert,) = email_alerts(db_session)
    assert email_alert.state == "resolved"
    assert email_alert.details["auto_resolved"]["reason"] == "recipient_removed"


def test_4xx_on_rcpt_is_retried(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server)
    policy(db_session)
    smtp_server.defer.add(TO)
    raise_alert(db_session)
    report = notify_at(sessions, config)
    assert report.errors == [f"{TO}: recipient refused: 451 4.3.0 try again later"]
    assert deliveries(db_session)[0].status == "queued"
    smtp_server.defer.clear()
    assert notify_at(sessions, config, NOW + timedelta(minutes=1)).sent == 1


def test_a_server_that_does_not_answer_times_out(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server, timeout_seconds=1)
    policy(db_session)
    smtp_server.delay = 2.5
    raise_alert(db_session)
    report = notify_at(sessions, config)
    assert report.emails == 0
    assert "timed out" in report.errors[0] or "timeout" in report.errors[0]
    row = deliveries(db_session)[0]
    assert (row.status, row.attempts) == ("queued", 1)


def test_a_crash_between_sending_and_recording_repeats_the_email_at_most_once(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = smtp_config(layout, smtp_server)
    policy(db_session)
    raise_alert(db_session)
    record: Callable[..., None] = notify._record

    def crash(*args: Any, **kwargs: Any) -> None:
        raise SystemExit("power cut")

    monkeypatch.setattr(notify, "_record", crash)
    with pytest.raises(SystemExit):
        notify_at(sessions, config)
    assert len(smtp_server.received) == 1  # sent, but not recorded
    monkeypatch.setattr(notify, "_record", record)
    assert notify_at(sessions, config, NOW + timedelta(seconds=30)).emails == 0  # leased
    assert notify_at(sessions, config, NOW + timedelta(minutes=1)).emails == 1  # the repeat
    assert notify_at(sessions, config, NOW + timedelta(minutes=10)).emails == 0
    assert len(smtp_server.received) == 2
    # Queueing again never adds a second row for the same alert, kind and recipient.
    total = db_session.scalar(select(func.count()).select_from(AlertDelivery))
    assert total == 1


# -- authentication and the password --------------------------------------------------------------


def store_password(layout: Layout, value: str) -> None:
    key = layout.root / "secret.key"
    if not key.exists():
        SecretStore.create_key(key)
    SecretStore(key, layout.root / "secrets.json").set(SMTP_PASSWORD, value)


def test_auth_plain_with_the_stored_password_which_never_shows_in_errors(
    sessions: sessionmaker[Session], layout: Layout, smtp_server: FakeSmtp, db_session: Session
) -> None:
    config = smtp_config(layout, smtp_server, username="arcilogger")
    policy(db_session)
    smtp_server.credentials = ("arcilogger", SMTP_SECRET)
    raise_alert(db_session)
    report = notify_at(sessions, config)  # no password stored yet
    assert report.errors == [
        f"{TO}: smtp.password is not stored (arcilogger-core secret set smtp.password)"
    ]
    store_password(layout, SMTP_SECRET + "-wrong")
    report = notify_at(sessions, config, NOW + timedelta(minutes=1))
    assert "535" in report.errors[0]
    for row in deliveries(db_session):
        assert SMTP_SECRET not in (row.last_error or "")
    store_password(layout, SMTP_SECRET)
    assert notify_at(sessions, config, NOW + timedelta(minutes=10)).sent == 1
    assert smtp_server.received[0].login == ("arcilogger", SMTP_SECRET)


# -- the command line -----------------------------------------------------------------------------


def test_test_email_command(
    layout: Layout,
    smtp_server: FakeSmtp,
    db_engine: Engine,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = smtp_config(layout, smtp_server, enabled=False, username="arcilogger")
    smtp_server.credentials = ("arcilogger", SMTP_SECRET)
    store_password(layout, SMTP_SECRET)
    assert send_test_email(config, TO, engine=db_engine) == 0
    out = capsys.readouterr()
    assert f"Gönderildi / sent: {TO}" in out.out
    assert "[smtp] enabled = false" in out.out
    assert SMTP_SECRET not in out.out + out.err
    (mail,) = smtp_server.received
    assert mail.message["Subject"] == "[ARCILOGGER] Deneme e-postası / Test e-mail"
    assert "https://172.20.20.15" in mail.part("plain")
    smtp_server.refuse.add(OTHER)
    assert send_test_email(config, OTHER) == 1
    out = capsys.readouterr()
    assert "550 5.1.1 mailbox unavailable" in out.err
    assert SMTP_SECRET not in out.out + out.err
    assert send_test_email(config, "not an address") == 2
    unset = Config(data_dir=layout.root, secrets_dir=layout.root)
    assert send_test_email(unset, TO) == 2


def test_list_command(
    db_engine: Engine, db_session: Session, capsys: pytest.CaptureFixture[str]
) -> None:
    assert list_alerts(db_engine) == 0
    assert "Uyarı yok" in capsys.readouterr().out
    alert = raise_alert(db_session)
    other = raise_alert(db_session, key="R15:core-r1", title="Log kaynağı sessiz: core-r1")
    resolve(other, reason="messages_again", now=NOW)
    db_session.commit()
    assert list_alerts(db_engine) == 0
    assert capsys.readouterr().out.count("R15") == 2
    assert list_alerts(db_engine, only_open=True) == 0
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1
    assert lines[0].split()[0] == str(alert.id)
    assert "19.10.2026 12:00:00" in lines[0]


# -- small parts ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("setting", "bind", "url"),
    [
        ("https://logs.ornek.example/", "172.20.20.15", "https://logs.ornek.example"),
        ("", "172.20.20.15", "https://172.20.20.15"),
        ("", "10.1.2.3", "https://10.1.2.3"),
        # No address of this server: the installer's default server name, no network's address.
        ("", "0.0.0.0", "https://arcilogger.internal"),  # noqa: S104 - a test value
        ("", "127.0.0.1", "https://arcilogger.internal"),
        ("", "fd00::20", "https://[fd00::20]"),
    ],
)
def test_web_base_url(tmp_path: Path, setting: str, bind: str, url: str) -> None:
    config = Config(data_dir=tmp_path, secrets_dir=tmp_path, collector={"bind": bind})
    assert web_base_url(setting, config) == url


def test_a_long_digest_lists_fifty_and_counts_the_rest() -> None:
    items = [
        MailItem(n, "new", "R15", "high", "open", NOW, None, {"source": f"source-{n:02d}"})
        for n in range(60)
    ]
    message = build_message(
        items, sender=SENDER, recipient=TO, web_url="https://172.20.20.15", now=NOW
    )
    assert message["Subject"] == "[ARCILOGGER] Yüksek: 60 uyarı"
    body = message.get_body(preferencelist=("plain",))
    assert body is not None
    text = str(body.get_content())
    assert "… 10 uyarı daha." in text
    assert "… 10 more alert(s)." in text
    assert "source-49" in text
    assert "source-50" not in text


def test_replica_and_retention_sentences() -> None:
    stale = {"stale": True, "threshold_hours": 48, "missing_days": ["2026-10-11", "x"]}
    assert describe("R20-replica", stale) == (
        "48 saatten uzun süredir replikada olmayan mühürlü gün(ler): 11.10.2026.",
        "Sealed day(s) without a replica copy for more than 48 hours: 11.10.2026.",
    )
    pruned = {"key": "prune", "error": RAW, "dir": "backups/scheduled"}  # the error never shows
    assert describe("R20-backup", pruned) == (
        "Eski veritabanı yedekleri silinemedi.",
        "Old database backups could not be deleted.",
    )
