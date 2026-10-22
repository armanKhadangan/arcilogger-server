"""``reports.run_due`` and ``reports.purge`` against the database and a real SMTP server on
127.0.0.1: the shipped schedules, due runs (once after downtime), rendering and storing with
SHA-256, one e-mail per recipient with attachments or a download link, retries and giving up
(R20-report), the alerts and the seal of the report, retention."""

from __future__ import annotations

import hashlib
import json
import socket
from datetime import date, datetime, timedelta
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config, SmtpConfig
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStore
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, ReportRun, ReportSchedule
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts.notify import BACKOFF, MAX_ATTEMPTS
from arcilogger_worker.alerts.smtptesting import FakeSmtp
from arcilogger_worker.alerts.store import open_alert
from arcilogger_worker.alerts.texts import describe
from arcilogger_worker.reports import mail as report_mail
from arcilogger_worker.reports import runner, store
from arcilogger_worker.reports.content import ReportMeta, build_document, seal_status
from arcilogger_worker.reports.runner import REPORT_RULE, RunDueReport, queue_run, run_due
from arcilogger_worker.reports.testing import SITE_NAME, report_sample
from arcilogger_worker.usage import report_data

NOW = datetime(2026, 10, 20, 7, 0, 30, tzinfo=SITE_TZ)
TO = "yonetim@example.com"
OTHER = "ik@example.com"
SENDER = "arcilogger@example.com"
WEB = "https://172.20.20.15"
#: A raw FortiGate line and a person in an alert's title: neither may reach a report.
RAW = '<189>date=2026-10-19 time=11:58:01 devname="FIREWALL-01" srcip=172.20.42.57 action="deny"'


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


class Engine_:  # noqa: N801 - a stand-in for the usage engine
    """``build_report_data`` of the usage engine: the fixed sample, and the calls it got."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, connection: Any, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        data = report_sample()
        data["kind"] = kwargs["kind"]
        data["period"] = {"from": kwargs["start"].isoformat(), "to": kwargs["end"].isoformat()}
        return data


@pytest.fixture
def engine() -> Engine_:
    return Engine_()


def smtp_config(layout: Layout, fake: FakeSmtp | None, **smtp: Any) -> Config:
    settings: dict[str, Any] = {"enabled": False}
    if fake is not None:
        settings = {
            "enabled": True,
            "host": fake.host,
            "port": fake.port,
            "security": "none",
            "from_address": SENDER,
            "timeout_seconds": 2,
        }
    return Config(
        data_dir=layout.root,
        secrets_dir=layout.root,
        site_name=SITE_NAME,
        collector={"bind": "172.20.20.15"},
        smtp=SmtpConfig(**{**settings, **smtp}),
    )


def schedule(session: Session, kind: str = "full_day", **fields: Any) -> ReportSchedule:
    row = session.scalars(select(ReportSchedule).where(ReportSchedule.kind == kind)).one()
    values: dict[str, Any] = {
        "enabled": True,
        "recipients": [TO, OTHER],
        "next_run_at": datetime(2026, 10, 20, 7, 0, tzinfo=SITE_TZ),
        **fields,
    }
    for key, value in values.items():
        setattr(row, key, value)
    session.commit()
    return row


def tick(
    sessions: sessionmaker[Session],
    layout: Layout,
    config: Config,
    engine: Engine_,
    now: datetime = NOW,
) -> RunDueReport:
    return run_due(sessions, layout, config, now=now, builder=engine)


def runs(session: Session) -> list[ReportRun]:
    session.expire_all()
    return list(session.scalars(select(ReportRun).order_by(ReportRun.id)))


def report_alerts(session: Session) -> list[Alert]:
    session.expire_all()
    return list(session.scalars(select(Alert).where(Alert.rule == REPORT_RULE)))


def attachments(message: EmailMessage) -> dict[str, tuple[str, bytes]]:
    found = {}
    for part in message.iter_attachments():
        payload = part.get_payload(decode=True)
        assert isinstance(payload, bytes)
        found[str(part.get_filename())] = (part.get_content_type(), payload)
    return found


# -- the shipped schedules ------------------------------------------------------------------------


def test_the_four_default_schedules_are_shipped_disabled_and_person_level(
    db_session: Session,
) -> None:
    rows = db_session.scalars(select(ReportSchedule).order_by(ReportSchedule.id)).all()
    assert [(r.name, r.kind, r.time, r.weekday, r.monthday) for r in rows] == [
        ("Gün sonu raporu", "end_of_day", "18:30", None, None),
        ("Tam gün raporu", "full_day", "07:00", None, None),
        ("Haftalık rapor", "weekly", "07:15", 1, None),
        ("Aylık rapor", "monthly", "07:30", None, 1),
    ]
    for row in rows:
        assert (row.enabled, row.recipients, row.next_run_at) == (False, [], None)
        assert (row.person_level, row.pseudonymize) == (True, False)  # owner decision
        assert row.formats == {"email_html": True, "pdf": True, "xlsx": True}
        assert row.updated_by == ":seed"


# -- a scheduled run end to end -------------------------------------------------------------------


def test_a_due_report_is_rendered_stored_and_mailed_to_each_recipient(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    row = schedule(db_session)
    report = tick(sessions, layout, config, engine)
    assert (report.scheduled, report.rendered, report.emails, report.finished) == (1, 1, 2, 1)
    # The engine was asked for the previous day, person-level with names.
    assert engine.calls == [
        {
            "start": datetime(2026, 10, 19, tzinfo=SITE_TZ),
            "end": datetime(2026, 10, 20, tzinfo=SITE_TZ),
            "kind": "full_day",
            "person_level": True,
            "pseudonymize": False,
        }
    ]
    (run,) = runs(db_session)
    assert (run.status, run.triggered_by, run.catch_up, run.schedule_id) == (
        "sent",
        "schedule",
        False,
        row.id,
    )
    assert run.due_at == datetime(2026, 10, 20, 7, 0, tzinfo=SITE_TZ)
    assert run.finished_at == NOW
    stem = f"ARCILOGGER-kullanim-tam-gun-20261019-r{run.id}"
    assert [(f["name"], f["format"]) for f in run.files] == [
        (f"{stem}.html", "html"),
        (f"{stem}.pdf", "pdf"),
        (f"{stem}.xlsx", "xlsx"),
    ]
    folder = layout.exports / "usage-reports" / str(run.id)
    for entry in run.files:
        data = (folder / entry["name"]).read_bytes()
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["size"] == len(data)
    assert not (layout.vault).exists()  # never in the vault
    assert [d["recipient"] for d in run.deliveries] == [TO, OTHER]
    assert {d["status"] for d in run.deliveries} == {"sent"}
    # One e-mail per recipient, nobody sees the other.
    assert [mail.recipients for mail in smtp_server.received] == [[TO], [OTHER]]
    message = smtp_server.received[0].message
    assert message["To"] == TO
    assert message["Cc"] is None
    assert message["Bcc"] is None
    assert message["From"] == SENDER
    assert message["Auto-Submitted"] == "auto-generated"
    assert message["Subject"] == "[ARCILOGGER] Tam gün raporu — 19.10.2026 (Örnek A.Ş.)"
    # A long Message-ID line is folded on the wire; the parsed value then starts with the
    # folding space, which is not part of the id.
    assert run.deliveries[0]["message_id"] == message["Message-ID"].strip()
    files = attachments(message)
    assert sorted(files) == [f"{stem}.pdf", f"{stem}.xlsx"]
    assert files[f"{stem}.pdf"][0] == "application/pdf"
    assert files[f"{stem}.pdf"][1] == (folder / f"{stem}.pdf").read_bytes()
    html = smtp_server.received[0].part("html")
    assert "<b>Elif Kaya</b>" in html  # names, owner decision 2026-10-20
    assert f"{WEB}/reports?run={run.id}" in html
    text = smtp_server.received[0].part("plain")
    assert text.index("aktif kullanıcı") < text.index("active users")  # Turkish first
    db_session.expire_all()
    assert db_session.get(ReportSchedule, row.id).next_run_at == datetime(  # type: ignore[union-attr]
        2026, 10, 21, 7, 0, tzinfo=SITE_TZ
    )
    # Nothing more to do a minute later.
    again = tick(sessions, layout, config, engine, NOW + timedelta(minutes=1))
    assert (again.scheduled, again.rendered, again.emails) == (0, 0, 0)
    assert len(smtp_server.received) == 2


def test_after_downtime_a_schedule_runs_once_or_is_skipped(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session, next_run_at=datetime(2026, 10, 15, 7, 0, tzinfo=SITE_TZ))
    later = datetime(2026, 10, 20, 9, 0, tzinfo=SITE_TZ)
    report = tick(sessions, layout, config, engine, later)
    assert (report.scheduled, report.skipped) == (1, 5)
    (run,) = runs(db_session)
    assert run.catch_up
    assert run.due_at == datetime(2026, 10, 20, 7, 0, tzinfo=SITE_TZ)
    assert run.period_from == datetime(2026, 10, 19, tzinfo=SITE_TZ)
    # Catching up switched off: a late occurrence is skipped, the next one waits.
    set_setting(db_session, "reports.catch_up", False, updated_by="tolga")
    row = schedule(db_session, next_run_at=datetime(2026, 10, 21, 7, 0, tzinfo=SITE_TZ))
    report = tick(sessions, layout, config, engine, datetime(2026, 10, 21, 8, 0, tzinfo=SITE_TZ))
    assert (report.scheduled, report.skipped) == (0, 1)
    db_session.expire_all()
    assert row.next_run_at == datetime(2026, 10, 22, 7, 0, tzinfo=SITE_TZ)
    # On time it still runs.
    report = tick(sessions, layout, config, engine, datetime(2026, 10, 22, 7, 1, tzinfo=SITE_TZ))
    assert report.scheduled == 1


def test_disabled_schedules_and_schedules_without_recipients_never_run(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session, enabled=False)
    schedule(db_session, "weekly", recipients=[])
    report = tick(sessions, layout, config, engine, datetime(2026, 10, 27, 9, 0, tzinfo=SITE_TZ))
    assert report.scheduled == 0
    assert runs(db_session) == []


def test_a_schedule_that_cannot_run_does_not_stop_the_others(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    """An end-of-day report at 00:00 stored before the API refused it (it would cover nothing):
    it is left out with an error, and the full-day report due at the same minute still runs."""
    config = smtp_config(layout, smtp_server)
    midnight = datetime(2026, 10, 21, tzinfo=SITE_TZ)
    broken = schedule(db_session, "end_of_day", time="00:00", next_run_at=midnight)
    schedule(db_session, time="00:00", recipients=[TO], next_run_at=midnight)
    report = tick(sessions, layout, config, engine, midnight + timedelta(seconds=30))
    assert (report.scheduled, report.rendered, report.emails) == (1, 1, 1)
    assert report.errors == [
        f"schedule {broken.id}: an end_of_day report at 00:00 would cover nothing; "
        "choose 00:01 … 23:59 (or full_day for the whole previous day)"
    ]
    (run,) = runs(db_session)
    assert (run.kind, run.status) == ("full_day", "sent")
    db_session.expire_all()
    assert broken.next_run_at is None  # waits for its time to be corrected
    assert tick(sessions, layout, config, engine, midnight + timedelta(minutes=1)).errors == []


# -- attachments, size guard, formats -------------------------------------------------------------


def test_too_large_attachments_are_replaced_by_the_download_link(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = smtp_config(layout, smtp_server)
    set_setting(db_session, "reports.max_attachment_mb", 1, updated_by="tolga")
    schedule(db_session, recipients=[TO])
    monkeypatch.setattr(report_mail, "MIB", 10_000)  # 1 "MB" = 10 KB: the sample is larger
    tick(sessions, layout, config, engine)
    (run,) = runs(db_session)
    assert run.summary["attachments_omitted"] is True
    assert run.summary["attachments"] == []
    assert len(run.files) == 3  # still stored and downloadable
    (mail,) = smtp_server.received
    assert attachments(mail.message) == {}
    text = mail.part("plain")
    assert "Ekler e-postaya eklenmedi" in text
    assert f"{WEB}/reports?run={run.id}" in text
    assert "Elif Kaya" in mail.part("html")  # the report itself is still the body


def test_without_an_organisation_name_the_mail_leaves_it_out(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    """``[site] site_name`` is empty until the installer sets it: the report still goes out,
    with no empty brackets in the subject or the summary."""
    config = smtp_config(layout, smtp_server).model_copy(update={"site_name": ""})
    schedule(db_session, recipients=[TO], formats={"email_html": False, "pdf": True, "xlsx": False})
    report = tick(sessions, layout, config, engine)
    assert (report.rendered, report.emails, report.finished) == (1, 1, 1)
    (mail,) = smtp_server.received
    assert mail.message["Subject"] == "[ARCILOGGER] Tam gün raporu — 19.10.2026"
    for part in (mail.part("plain"), mail.part("html")):
        assert "Tam gün raporu 19.10.2026: 18 aktif kullanıcı" in part
        assert "()" not in part


def test_only_the_chosen_formats_are_made(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session, recipients=[TO], formats={"email_html": False, "pdf": True, "xlsx": False})
    tick(sessions, layout, config, engine)
    (run,) = runs(db_session)
    assert [f["format"] for f in run.files] == ["pdf"]
    (mail,) = smtp_server.received
    assert list(attachments(mail.message)) == [run.files[0]["name"]]
    html = mail.part("html")
    assert "Elif Kaya" not in html  # a short summary, not the report
    assert "aktif kullanıcı" in html


# -- failures -------------------------------------------------------------------------------------


def test_a_temporary_failure_is_retried_then_given_up_with_r20_report(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session, recipients=[TO])
    smtp_server.defer.add(TO)
    now = NOW
    tick(sessions, layout, config, engine, now)
    (run,) = runs(db_session)
    delivery = run.deliveries[0]
    assert (run.status, delivery["status"], delivery["attempts"]) == ("running", "queued", 1)
    assert "451" in delivery["error"]
    assert report_alerts(db_session) == []
    # Not before the backoff.
    assert tick(sessions, layout, config, engine, now + timedelta(seconds=30)).errors == []
    for wait in BACKOFF[: MAX_ATTEMPTS - 1]:
        now += wait
        tick(sessions, layout, config, engine, now)
    (run,) = runs(db_session)
    delivery = run.deliveries[0]
    assert (delivery["status"], delivery["attempts"]) == ("failed", MAX_ATTEMPTS)
    assert run.status == "failed"
    assert run.finished_at == now
    (alert,) = report_alerts(db_session)
    assert (alert.state, alert.severity, alert.details["recipients"]) == ("open", "medium", [TO])
    assert describe(REPORT_RULE, alert.details)[0] == (
        f"Kullanım raporu 1 alıcıya gönderilemedi (çalıştırma {run.id})."
    )
    # The address works again: the next report reaches it and the alert is resolved.
    smtp_server.defer.clear()
    with sessions() as session:
        row = session.scalars(select(ReportSchedule).where(ReportSchedule.kind == "full_day"))
        queue_run(
            session,
            row.one(),
            start=run.period_from,
            end=run.period_to,
            kind="custom",
            triggered_by="manual",
            requested_by="tolga",
            now=now,
        )
        session.commit()
    tick(sessions, layout, config, engine, now + timedelta(minutes=1))
    (alert,) = report_alerts(db_session)
    assert alert.state == "resolved"
    assert alert.details["auto_resolved"]["reason"] == "report_sent"


def test_a_refused_recipient_fails_alone(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session)
    smtp_server.refuse.add(OTHER)
    tick(sessions, layout, config, engine)
    (run,) = runs(db_session)
    assert run.status == "partial"
    by_recipient = {d["recipient"]: d for d in run.deliveries}
    assert by_recipient[TO]["status"] == "sent"
    assert (by_recipient[OTHER]["status"], by_recipient[OTHER]["attempts"]) == ("failed", 1)
    assert "550" in by_recipient[OTHER]["error"]
    (alert,) = report_alerts(db_session)
    assert alert.details["recipients"] == [OTHER]
    # The administrator removes the address from every schedule: the alert is resolved.
    schedule(db_session, recipients=[TO])
    tick(sessions, layout, config, engine, NOW + timedelta(minutes=1))
    (alert,) = report_alerts(db_session)
    assert alert.state == "resolved"


def _closed_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
    return port


def test_when_the_server_is_down_the_other_recipients_wait(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server, port=_closed_port())
    schedule(db_session)
    report = tick(sessions, layout, config, engine)
    assert report.emails == 0
    (run,) = runs(db_session)
    assert [d["attempts"] for d in run.deliveries] == [1, 0]
    assert run.status == "running"


def test_without_smtp_the_deliveries_fail_at_once(
    sessions: sessionmaker[Session],
    layout: Layout,
    db_session: Session,
    engine: Engine_,
) -> None:
    schedule(db_session, recipients=[TO])
    tick(sessions, layout, smtp_config(layout, None), engine)
    (run,) = runs(db_session)
    assert run.status == "failed"
    assert run.deliveries[0]["error"] == runner.SMTP_DISABLED
    assert len(run.files) == 3  # the report was made all the same
    (alert,) = report_alerts(db_session)
    assert alert.details["recipients"] == [TO]


def test_a_report_that_cannot_be_made_fails_visibly(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session, recipients=[TO])

    def missing(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("no usage engine")

    # A usage engine that cannot run makes the run fail visibly, with an alert.
    with monkeypatch.context() as patch:
        patch.setattr(report_data, "build_report_data", missing)
        report = run_due(sessions, layout, config, now=NOW)
    assert report.render_failed == 1
    (run,) = runs(db_session)
    assert run.status == "failed"
    assert run.error is not None
    assert "usage engine not installed" in run.error
    assert run.files == []
    assert smtp_server.received == []
    (alert,) = report_alerts(db_session)
    assert alert.details["render_failed"] is True
    assert describe(REPORT_RULE, alert.details)[0] == (
        f"Kullanım raporu oluşturulamadı (çalıştırma {run.id})."
    )
    # The next report that renders resolves it.
    schedule(db_session, recipients=[TO], next_run_at=datetime(2026, 10, 21, 7, 0, tzinfo=SITE_TZ))
    tick(sessions, layout, config, engine, datetime(2026, 10, 21, 7, 0, 5, tzinfo=SITE_TZ))
    (alert,) = report_alerts(db_session)
    assert alert.state == "resolved"


def test_pseudonyms_use_the_key_of_the_workers_own_secret_store(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session, recipients=[TO], pseudonymize=True)
    tick(sessions, layout, config, engine)
    (run,) = runs(db_session)
    assert run.status == "failed"
    assert run.error == "PseudonymKeyMissingError: usage.pseudonym_key is not in the secret store"
    assert engine.calls == []
    SecretStore.create_key(config.secrets_dir / "secret.key")
    SecretStore(config.secrets_dir / "secret.key", config.secrets_dir / "secrets.json").set(
        "usage.pseudonym_key",
        "report-runner-test-key-0123456789",  # gitleaks:allow
    )
    schedule(db_session, recipients=[TO], pseudonymize=True)  # due again
    tick(sessions, layout, config, engine)
    assert engine.calls[-1]["pseudonym_key"] == b"report-runner-test-key-0123456789"
    assert runs(db_session)[-1].status == "sent"


def test_an_interrupted_rendering_is_done_again(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    row = schedule(db_session, recipients=[TO], enabled=False)
    run = queue_run(
        db_session,
        row,
        start=datetime(2026, 10, 19, tzinfo=SITE_TZ),
        end=datetime(2026, 10, 20, tzinfo=SITE_TZ),
        kind="full_day",
        triggered_by="manual",
        now=NOW,
    )
    run.status, run.picked_at, run.render_attempts = "running", NOW - timedelta(hours=1), 1
    db_session.commit()
    tick(sessions, layout, config, engine)
    (run,) = runs(db_session)
    assert (run.status, run.render_attempts) == ("sent", 2)


def test_files_only_when_send_is_false(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    row = schedule(db_session, enabled=False)
    queue_run(
        db_session,
        row,
        start=datetime(2026, 10, 1, tzinfo=SITE_TZ),
        end=datetime(2026, 10, 8, tzinfo=SITE_TZ),
        kind="custom",
        triggered_by="manual",
        requested_by="tolga",
        send=False,
        now=NOW,
    )
    db_session.commit()
    tick(sessions, layout, config, engine)
    (run,) = runs(db_session)
    assert run.status == "sent"
    assert {d["status"] for d in run.deliveries} == {"skipped"}
    assert smtp_server.received == []
    assert run.files[0]["name"] == f"ARCILOGGER-kullanim-ozel-20261001-20261007-r{run.id}.html"
    assert engine.calls[0]["kind"] == "custom"


# -- the document: alerts and the seal ------------------------------------------------------------


def test_the_report_lists_the_alerts_of_the_period_from_the_catalogue(
    sessions: sessionmaker[Session], layout: Layout, db_session: Session, engine: Engine_
) -> None:
    open_alert(
        db_session,
        rule="R15",
        severity="high",
        title=f"Log kaynağı sessiz: Ayşe Yılmaztürk {RAW}",
        details={"source": "fortigate", "threshold_minutes": 10, "raw": RAW},
        key="R15:fortigate",
        time=datetime(2026, 10, 19, 11, 58, tzinfo=SITE_TZ),
    )
    open_alert(
        db_session,
        rule="R20-disk",
        severity="critical",
        title="Disk",
        details={"drive": "D:", "used_percent": 91, "threshold_percent": 80},
        key="R20-disk:D:",
        time=datetime(2026, 10, 19, 3, 0, tzinfo=SITE_TZ),
    )
    open_alert(  # outside the period
        db_session,
        rule="R15",
        severity="high",
        title="x",
        details={},
        key="R15:sw",
        time=datetime(2026, 10, 20, 0, 0, tzinfo=SITE_TZ),
    )
    db_session.commit()
    seal = layout.seal_state(seal_day())
    seal.parent.mkdir(parents=True)
    seal.write_text(
        json.dumps(
            {"status": "sealed", "sealed_at": "2026-10-20T00:31:12+03:00", "serial": "4F2A19"}
        ),
        encoding="utf-8",
    )
    for path in (layout.manifest(seal_day()), layout.token(seal_day())):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x")
    document = build_document(
        db_session,
        layout,
        meta=ReportMeta("Tam gün raporu", "full_day", SITE_NAME, WEB, NOW),
        start=datetime(2026, 10, 19, tzinfo=SITE_TZ),
        end=datetime(2026, 10, 20, tzinfo=SITE_TZ),
        person_level=True,
        pseudonymize=False,
        builder=engine,
    )
    alerts = document["alerts"]
    assert alerts["total"] == 2
    assert [item["rule"] for item in alerts["listed"]] == ["R20-disk", "R15"]  # most severe first
    assert alerts["listed"][1]["text_tr"] == (
        "fortigate kaynağından 10 dakikadan uzun süredir mesaj gelmedi."
    )
    whole = json.dumps(document, ensure_ascii=False)
    for secret in ("Yılmaztürk", "srcip=", "<189>"):
        assert secret not in whole
    assert document["seal"] == {
        "day": "2026-10-19",
        "status": "sealed",
        "sealed_at": "2026-10-20T00:31:12+03:00",
        "serial": "4F2A19",
        "provider": None,
        "test_stamp": False,
    }


def seal_day() -> date:
    return date(2026, 10, 19)


@pytest.mark.parametrize(
    ("state", "vault", "expected"),
    [
        (None, False, "pending"),
        ({"status": "pending", "attempts": 0}, False, "pending"),
        ({"status": "pending", "last_error": "TSA unreachable"}, False, "failed"),
        ({"status": "skipped"}, False, "skipped"),
        ({"status": "sealed", "provider": "mock"}, True, "sealed"),
        (None, True, "sealed"),
    ],
)
def test_seal_status_of_the_previous_day(
    layout: Layout, state: dict[str, Any] | None, vault: bool, expected: str
) -> None:
    day = seal_day()
    if state is not None:
        layout.seal_state(day).parent.mkdir(parents=True)
        layout.seal_state(day).write_text(json.dumps(state), encoding="utf-8")
    if vault:
        layout.manifest(day).parent.mkdir(parents=True)
        layout.manifest(day).write_bytes(b"{}")
        layout.token(day).write_bytes(b"x")
    found = seal_status(layout, day)
    assert found["status"] == expected
    assert found["test_stamp"] is (state is not None and state.get("provider") == "mock")


# -- retention ------------------------------------------------------------------------------------


def test_old_report_files_are_purged_but_the_run_stays(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session, recipients=[TO])
    tick(sessions, layout, config, engine)
    (run,) = runs(db_session)
    folder = store.run_folder(layout, run.id)
    assert folder.is_dir()
    report = runner.purge_reports(sessions, layout, now=NOW + timedelta(days=399))
    assert report.deleted == 0
    report = runner.purge_reports(sessions, layout, now=NOW + timedelta(days=401))
    assert report.deleted == 1
    assert report.bytes > 0
    assert not folder.exists()
    (run,) = runs(db_session)
    assert run.expired_at == NOW + timedelta(days=401)
    assert len(run.files) == 3  # the hashes stay
    with pytest.raises(store.StoredFileError) as raised:
        store.read_file(layout, run, run.files[0]["name"])
    assert raised.value.code == "report_expired"


def test_a_changed_file_is_refused(
    sessions: sessionmaker[Session],
    layout: Layout,
    smtp_server: FakeSmtp,
    db_session: Session,
    engine: Engine_,
) -> None:
    config = smtp_config(layout, smtp_server)
    schedule(db_session, recipients=[TO])
    tick(sessions, layout, config, engine)
    (run,) = runs(db_session)
    entry = run.files[1]
    path: Path = store.run_folder(layout, run.id) / entry["name"]
    path.write_bytes(path.read_bytes() + b"x")
    with pytest.raises(store.StoredFileError) as raised:
        store.read_file(layout, run, entry["name"])
    assert raised.value.code == "report_damaged"
    path.unlink()
    with pytest.raises(store.StoredFileError) as raised:
        store.read_file(layout, run, entry["name"])
    assert raised.value.code == "report_missing"
