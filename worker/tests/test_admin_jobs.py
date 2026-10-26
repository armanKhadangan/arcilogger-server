"""The worker's part of the browser administration (ADR-0014): the agent's journal reaches the
audit chain exactly once, the agent's and ApplySystem's alert files are imported, a stale agent
heartbeat raises ``R20-admin``, and the alert texts carry only whitelisted facts."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, NamedTuple

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.admin.contract import (
    AdminLayout,
    AdminPolicy,
    AgentStatus,
    CreditResult,
    FirewallChange,
    JarChange,
    JournalLine,
    Requester,
    SealReady,
    SecretChange,
    SecretRecord,
    SystemJournalLine,
    SystemTaskCheck,
    TimeChange,
    Transition,
    ValueChange,
    dump_document,
    dump_line,
    new_id,
)
from arcilogger_core.admin.messages import MESSAGES
from arcilogger_core.alerts import raise_alert
from arcilogger_core.config import Config, ReplicaConfig, SmtpConfig
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ, isoformat_site
from arcilogger_db.audit import verify_audit_chain
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, AuditImport, AuditLog, Setting
from arcilogger_db.settings import set_setting
from arcilogger_worker.admin_jobs import import_admin_events, journal_events
from arcilogger_worker.alerts.importer import ADMIN_FILES, OFFSET_KEY, import_alerts
from arcilogger_worker.alerts.notify import run_notify
from arcilogger_worker.alerts.smtptesting import FakeSmtp
from arcilogger_worker.alerts.system import ADMIN_HEARTBEAT_KEY, AGENT_GRACE, run_system_checks
from arcilogger_worker.alerts.texts import CONFIG_SECTIONS, describe, rule_text, runbook
from arcilogger_worker.evidence_jobs import ImportReport, import_job_events
from arcilogger_worker.service import Job, Worker, job_audit_import

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
SHA_A = "a1" * 32
SHA_B = "b2" * 32
GIB = 1024**3


class Usage(NamedTuple):
    total: int
    used: int
    free: int


@pytest.fixture(autouse=True)
def roomy_disks(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every drive half used: the real disks never decide a check here."""

    def usage(path: Path) -> Usage:
        return Usage(1000 * GIB, 500 * GIB, 500 * GIB)

    monkeypatch.setattr("arcilogger_worker.alerts.system.shutil.disk_usage", usage)


@pytest.fixture
def sessions(db_engine: Engine) -> sessionmaker[Session]:
    return session_factory(db_engine)


@pytest.fixture
def admin(tmp_path: Path) -> AdminLayout:
    """The ``admin`` folder next to the configuration file ``tmp_path/arcilogger.toml``."""
    return AdminLayout(tmp_path / "admin")


def applied(**changes: Any) -> JournalLine:
    """The example line of contract 2.4.9: an SMTP change with a new password."""
    values: dict[str, Any] = {
        "line": new_id(),
        "request": new_id(),
        "state": "applied",
        "at": NOW,
        "kind": "config.apply",
        "section": "smtp",
        "requested_by": Requester(user_id=3, username="ayse"),
        "version": 17,
        "config_sha256": Transition.model_validate({"from": SHA_A, "to": SHA_B}),
        "changes": {
            "smtp.host": ValueChange(old="mail.ornek.example", new="smtp.ornek.example"),
            "smtp.password": SecretChange(secret=True, old="set", new="set", changed=True),
        },
        "secrets": (SecretRecord(name="smtp.password", action="set", result="stored"),),
    }
    values.update(changes)
    return JournalLine(**values)


def write_journal(admin: AdminLayout, day: date, *lines: JournalLine | bytes) -> None:
    path = admin.journal_file(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("ab") as handle:
        for line in lines:
            handle.write(line if isinstance(line, bytes) else dump_line(line))


def entries(session: Session, prefix: str = "") -> list[AuditLog]:
    session.expire_all()
    rows = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
    return [row for row in rows if row.action.startswith(prefix)]


# -- the agent's journal --------------------------------------------------------------------------


def test_the_journal_reaches_the_audit_chain_exactly_once(
    admin: AdminLayout,
    sessions: sessionmaker[Session],
    db_engine: Engine,
    db_session: Session,
) -> None:
    change = applied()
    credit = JournalLine(
        line=new_id(),
        request=new_id(),
        state="action",
        at=NOW + timedelta(minutes=1),
        kind="action.credit",
        requested_by=Requester(user_id=3, username="ayse"),
        result=CreditResult(credits=1250, threshold=500, below_threshold=False),
    )
    forged = JournalLine(  # rejected before verification: only the file name's id is known
        line=new_id(),
        request=new_id(),
        state="rejected",
        at=NOW + timedelta(minutes=2),
        code="signature_invalid",
    )
    write_journal(admin, date(2026, 10, 24), change)
    write_journal(admin, date(2026, 10, 25), credit, b"\n", b'{"v": 1, "line": "x"}\n', forged)
    report = import_admin_events(sessions, admin)
    assert report.problems == ["2026-10-25.jsonl line 3: not a journal line"]
    assert len(report.imported) == 4
    rows = entries(db_session)
    assert [(row.action, row.target) for row in rows] == [
        ("admin.applied", f"admin-request:{change.request}"),
        ("settings.secret_change", "secret:smtp.password"),
        ("admin.action", f"admin-request:{credit.request}"),
        ("admin.rejected", f"admin-request:{forged.request}"),
    ]
    assert all((row.actor_username, row.actor_id) == ("system", None) for row in rows)
    first = rows[0].details
    assert first["requested_by"] == {"user_id": 3, "username": "ayse"}
    assert (first["kind"], first["section"], first["version"]) == ("config.apply", "smtp", 17)
    assert first["config_sha256"] == {"from": SHA_A, "to": SHA_B}
    assert first["changes"]["smtp.password"] == {
        "secret": True,
        "old": "set",
        "new": "set",
        "changed": True,
    }
    assert first["source"] == "admin/journal/2026-10-24.jsonl line 1"
    assert rows[1].details == {
        "name": "smtp.password",
        "action": "set",
        "result": "stored",
        "operator": "web:ayse",
        "at": NOW.isoformat(),
        "request": change.request,
        "source": "admin/journal/2026-10-24.jsonl line 1",
    }
    assert rows[2].details["result"] == {
        "credits": 1250,
        "threshold": 500,
        "below_threshold": False,
    }
    rejected = rows[3].details
    assert "requested_by" not in rejected  # nobody is named for an unverified file
    assert (rejected["kind"], rejected["code"]) == (None, "signature_invalid")
    keys = set(db_session.scalars(select(AuditImport.key)))
    assert {
        f"admin:{change.request}:applied",
        f"admin-secret:{change.line}:smtp.password",
        f"admin:{credit.request}:action",
        f"admin:{forged.request}:rejected",
    } <= keys
    # Nothing twice: again, and after a restart of the worker (the job of a new Worker).
    assert import_admin_events(sessions, admin).imported == []
    worker = Worker(
        engine=db_engine,
        layout=Layout(admin.root.parent / "data"),
        jobs=[Job("audit.import", job_audit_import, every=timedelta(minutes=15))],
        clock=lambda: NOW,
        config_path=admin.root.parent / "arcilogger.toml",
    )
    assert worker.admin == admin
    assert worker.tick() == [("audit.import", "ok")]
    assert len(entries(db_session)) == 4
    assert verify_audit_chain(db_session).ok


def test_a_held_change_and_its_outcome_are_two_entries(
    admin: AdminLayout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    held = applied(state="pending", secrets=(), version=None, config_sha256=None)
    later = applied(line=new_id(), request=held.request, at=NOW + timedelta(hours=1))
    write_journal(admin, date(2026, 10, 25), held, later)
    report = ImportReport()
    events = journal_events(admin, report)
    assert [event.action for event in events] == [
        "admin.pending",
        "admin.applied",
        "settings.secret_change",
    ]
    import_admin_events(sessions, admin)
    assert [row.action for row in entries(db_session, "admin.")] == [
        "admin.pending",
        "admin.applied",
    ]


def test_an_unreadable_journal_is_reported_not_imported(
    admin: AdminLayout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    assert import_admin_events(sessions, admin).imported == []  # no journal yet
    leaking = json.loads(dump_line(applied()))
    leaking["changes"]["smtp.password"] = {"old": "eski-parola", "new": "yeni-parola"}
    write_journal(admin, date(2026, 10, 25), json.dumps(leaking).encode() + b"\n")
    report = import_admin_events(sessions, admin)
    assert report.problems == ["2026-10-25.jsonl line 1: not a journal line"]
    assert entries(db_session) == []
    assert "parola" not in json.dumps([row.details for row in entries(db_session)])


def test_applysystem_changes_are_imported_once(
    admin: AdminLayout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    lines = [
        SystemJournalLine(
            line=new_id(),
            at=NOW,
            change="firewall",
            firewall=FirewallChange(syslog_addresses_count=2, outbound_rules=0),
        ),
        SystemJournalLine(
            line=new_id(),
            at=NOW,
            change="time",
            time=TimeChange(changed=True, peers="172.20.10.1,0x8 time.ume.tubitak.gov.tr,0xa"),
        ),
        SystemJournalLine(
            line=new_id(),
            at=NOW,
            change="jar",
            jar=JarChange(sha256="c3" * 32, approved_by="console:yonetici"),
        ),
    ]
    admin.system.mkdir(parents=True)
    admin.system_journal.write_bytes(
        b"".join(dump_line(line) for line in lines) + b'{"v": 1, "change": "firewall"}\n'
    )
    report = import_admin_events(sessions, admin)
    assert report.problems == ["journal.jsonl line 4: not a system journal line"]
    rows = entries(db_session, "admin.system")
    assert [(row.target, row.details["change"]) for row in rows] == [
        ("system", "firewall"),
        ("system", "time"),
        ("system", "jar"),
    ]
    assert rows[0].details["firewall"] == {"syslog_addresses_count": 2, "outbound_rules": 0}
    assert rows[1].details["time"]["changed"] is True
    assert rows[2].details["jar"] == {"sha256": "c3" * 32, "approved_by": "console:yonetici"}
    assert rows[2].details["source"] == "admin/system/journal.jsonl line 3"
    keys = set(db_session.scalars(select(AuditImport.key)))
    assert {f"admin-system:{line.line}" for line in lines} <= keys
    assert import_admin_events(sessions, admin).imported == []
    assert verify_audit_chain(db_session).ok


def test_a_seal_now_attempt_names_its_request(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    request, day = new_id(), date(2026, 10, 24)
    folder = layout.state / "seal"
    folder.mkdir(parents=True)
    (folder / f"{day}.json").write_text(
        json.dumps(
            {
                "day": day.isoformat(),
                "attempts": 2,
                "last_attempt_at": isoformat_site(NOW, "seconds"),
                "last_error": "Kamu SM yanıt vermedi",
                "next_attempt_at": None,
                "requested": request,
            }
        ),
        encoding="utf-8",
    )
    import_job_events(sessions, layout, now=NOW)
    (row,) = entries(db_session, "seal.run")
    assert (row.details["outcome"], row.details["requested"]) == ("failed", request)
    (folder / f"{day}.json").write_text(
        json.dumps({"day": day.isoformat(), "attempts": 3, "sealed_at": NOW.isoformat()}),
        encoding="utf-8",
    )
    import_job_events(sessions, layout, now=NOW)
    sealed = entries(db_session, "seal.run")[-1]
    assert sealed.details["outcome"] == "sealed"
    assert "requested" not in sealed.details  # the scheduled attempt honoured no request


# -- the administration's alert files -------------------------------------------------------------


def test_the_agent_and_applysystem_alert_files_are_imported_once(
    admin: AdminLayout, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    request = new_id()
    raise_alert(
        admin.alerts,
        "R20-admin",
        "high",
        "İstek imzası doğrulanamadı",
        {"code": "signature_invalid", "request": request},
    )
    raise_alert(
        admin.alerts,
        "R20-config-change",
        "high",
        "Saklama süresi değişti",
        {"section": "retention", "paths": ["retention.enabled", "retention.days"]},
    )
    raise_alert(admin.system_alerts, "R20-admin", "high", "Güvenlik duvarı", {"code": "x"})
    raise_alert(layout.alerts, "R20-credit", "high", "Kontör az", {"credits": 3})
    report = import_alerts(sessions, layout, now=NOW, admin=admin)
    assert report.lines == 1  # state/alerts.jsonl
    assert sorted(report.files) == ["admin/status/alerts.jsonl", "admin/system/alerts.jsonl"]
    assert report.files["admin/status/alerts.jsonl"].lines == 2
    assert report.files["admin/system/alerts.jsonl"].lines == 1
    rows = db_session.scalars(select(Alert).order_by(Alert.id)).all()
    origins = sorted(row.details["origin"]["file"] for row in rows)
    assert origins == [
        "admin/status/alerts.jsonl",
        "admin/status/alerts.jsonl",
        "admin/system/alerts.jsonl",
        "state/alerts.jsonl",
    ]
    again = import_alerts(sessions, layout, now=NOW, admin=admin)
    assert again.lines == 0
    assert all(file.lines == 0 for file in again.files.values())
    stored = {row.key for row in db_session.scalars(select(Setting))}
    assert {OFFSET_KEY, *(key for _, key in ADMIN_FILES)} <= stored
    # Without the admin folder (a test worker): only state/alerts.jsonl.
    assert import_alerts(sessions, layout, now=NOW).files == {}


# -- the agent's heartbeat ------------------------------------------------------------------------


def _policy(admin: AdminLayout, *, enabled: bool) -> None:
    admin.policy.parent.mkdir(parents=True, exist_ok=True)
    policy = AdminPolicy(enabled=enabled, written_at=NOW, written_by="installer")
    admin.policy.write_bytes(dump_document(policy))


def _heartbeat(admin: AdminLayout, at: datetime) -> None:
    admin.agent_status.parent.mkdir(parents=True, exist_ok=True)
    status = AgentStatus(
        version="0.1.0",
        pid=4242,
        started_at=at - timedelta(hours=1),
        heartbeat_at=at,
        state="idle",
        policy_sha256=None,
        key_ids=(),
        seal_ready=SealReady(ok=True),
        system_task=SystemTaskCheck(ok=True),
    )
    admin.agent_status.write_bytes(dump_document(status))


def _admin_alerts(session: Session) -> list[Alert]:
    session.expire_all()
    return list(
        session.scalars(
            select(Alert).where(Alert.dedup_key == ADMIN_HEARTBEAT_KEY).order_by(Alert.id)
        )
    )


def test_a_stale_agent_heartbeat_raises_r20_admin(
    admin: AdminLayout, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    config = Config(data_dir=layout.root, secrets_dir=layout.root)
    started = NOW - timedelta(hours=2)

    def check(now: datetime, started_at: datetime = started) -> None:
        run_system_checks(sessions, layout, config, now=now, started_at=started_at, admin=admin)

    _policy(admin, enabled=True)
    _heartbeat(admin, NOW - timedelta(seconds=90))  # stale, not missing
    check(NOW)
    assert _admin_alerts(db_session) == []
    _heartbeat(admin, NOW - timedelta(minutes=7))
    check(NOW, started_at=NOW - AGENT_GRACE + timedelta(seconds=1))  # the worker just started
    assert _admin_alerts(db_session) == []
    check(NOW)
    (alert,) = _admin_alerts(db_session)
    assert (alert.rule, alert.severity, alert.state) == ("R20-admin", "high", "open")
    assert (alert.details["case"], alert.details["minutes"]) == ("heartbeat", 7)
    assert describe("R20-admin", alert.details)[0].startswith(
        "Yönetim hizmeti (ArciAdmin) 7 dakikadır yanıt vermiyor"
    )
    _heartbeat(admin, NOW + timedelta(minutes=5))
    check(NOW + timedelta(minutes=5))
    (alert,) = _admin_alerts(db_session)
    assert (alert.state, alert.details["auto_resolved"]["reason"]) == ("resolved", "agent_ok")
    # No heartbeat at all: the agent counts as missing.
    admin.agent_status.unlink()
    check(NOW + timedelta(minutes=10))
    newest = _admin_alerts(db_session)[-1]
    assert (newest.state, newest.details["minutes"]) == ("open", None)
    assert "hiç yanıt vermedi" in describe("R20-admin", newest.details)[0]
    # Browser administration switched off: no agent is expected.
    _policy(admin, enabled=False)
    check(NOW + timedelta(minutes=15))
    assert _admin_alerts(db_session)[-1].details["auto_resolved"]["reason"] == "admin_disabled"


def test_without_a_policy_no_agent_is_expected(
    admin: AdminLayout, layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    """Before the installer writes ``policy.json`` (an upgrade, Linux CI): browser
    administration is off (contract D0-13), so a missing agent is no problem."""
    config = Config(data_dir=layout.root, secrets_dir=layout.root)
    run_system_checks(
        sessions, layout, config, now=NOW, started_at=NOW - timedelta(hours=1), admin=admin
    )
    assert _admin_alerts(db_session) == []


def test_a_policy_the_worker_may_not_read_leaves_the_heartbeat_to_decide(
    admin: AdminLayout,
    layout: Layout,
    sessions: sessionmaker[Session],
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Until the installer grants ArciWorker Read on ``admin\\policy.json`` (contract Day 0 note
    WP3-5), the file is "access denied" for the worker. That is no "switched off": once the
    agent has written a heartbeat, a stale one raises R20-admin as with an enabled policy; an
    agent that never ran is not judged."""
    _policy(admin, enabled=True)
    policy = admin.policy
    read_bytes = Path.read_bytes

    def denied(path: Path) -> bytes:
        if path == policy:
            raise PermissionError(13, "Access is denied", str(path))
        return read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", denied)
    config = Config(data_dir=layout.root, secrets_dir=layout.root)
    started = NOW - timedelta(hours=1)
    run_system_checks(sessions, layout, config, now=NOW, started_at=started, admin=admin)
    assert _admin_alerts(db_session) == []  # no heartbeat ever written: nothing to judge
    _heartbeat(admin, NOW - timedelta(minutes=4))
    run_system_checks(sessions, layout, config, now=NOW, started_at=started, admin=admin)
    (alert,) = _admin_alerts(db_session)
    assert (alert.rule, alert.state, alert.details["minutes"]) == ("R20-admin", "open", 4)
    _heartbeat(admin, NOW)
    run_system_checks(sessions, layout, config, now=NOW, started_at=started, admin=admin)
    assert _admin_alerts(db_session)[0].details["auto_resolved"]["reason"] == "agent_ok"


def test_a_replica_switched_on_by_a_reload_first_copies_the_old_days(
    layout: Layout, sessions: sessionmaker[Session], db_session: Session
) -> None:
    folder = layout.state / "seal"
    folder.mkdir(parents=True)
    old = NOW - timedelta(days=10)
    (folder / "2026-10-15.json").write_text(
        json.dumps({"day": "2026-10-15", "status": "sealed", "sealed_at": old.isoformat()}),
        encoding="utf-8",
    )
    config = Config(
        data_dir=layout.root,
        secrets_dir=layout.root,
        replica=ReplicaConfig(enabled=True, target=layout.root / "nas"),
    )
    started = NOW - timedelta(days=5)
    run_system_checks(
        sessions,
        layout,
        config,
        now=NOW,
        started_at=started,
        replica_since=NOW - timedelta(hours=1),
    )
    db_session.expire_all()
    assert db_session.scalars(select(Alert).where(Alert.rule == "R20-replica")).all() == []
    run_system_checks(sessions, layout, config, now=NOW, started_at=started)
    assert db_session.scalars(select(Alert.dedup_key).where(Alert.rule == "R20-replica")).all() == [
        "R20-replica:stale"
    ]


# -- what the alert e-mails may say ---------------------------------------------------------------


def test_a_configuration_change_names_the_section_and_fields_only() -> None:
    assert set(CONFIG_SECTIONS) == {
        "general",
        "tsa",
        "smtp",
        "replica",
        "retention",
        "backup",
        "sources",
    }
    details = {
        "section": "retention",
        "paths": ["retention.enabled", "retention.days"],
        "old": 760,
        "new": 730,
        "requested_by": {"user_id": 3, "username": "ayse"},
    }
    text_tr, text_en = describe("R20-config-change", details)
    assert text_tr == "Yapılandırma değişti: Saklama ve imha (retention.enabled, retention.days)."
    assert text_en == "Configuration changed: Retention (retention.enabled, retention.days)."
    for leak in ("760", "730", "ayse"):
        assert leak not in text_tr + text_en
    # A value or anything else in place of a field name is left out; too many are counted.
    odd = {"section": "smtp", "paths": ["smtp.host", "smtp.ornek.example ", "x" * 300, 5]}
    assert (
        describe("R20-config-change", odd)[1] == "Configuration changed: E-mail (SMTP) (smtp.host)."
    )
    many = {"section": "sources", "paths": [f"sources.s{n}.enabled" for n in range(12)]}
    assert describe("R20-config-change", many)[0].endswith(", sources.s9.enabled, …).")
    assert describe("R20-config-change", {"section": "database"}) == describe("R20-x", {})


def test_the_configuration_change_email_carries_only_whitelisted_facts(
    admin: AdminLayout,
    layout: Layout,
    sessions: sessionmaker[Session],
    db_session: Session,
    smtp_server: FakeSmtp,
) -> None:
    """The agent's alert goes through ``alerts.import`` and ``alerts.notify``: the e-mail names
    the section and the field, never the values or who changed them."""
    raise_alert(
        admin.alerts,
        "R20-config-change",
        "high",
        "SMTP sunucusu eski-sunucu.ornek.example oldu",
        {
            "section": "smtp",
            "paths": ["smtp.host"],
            "old": "eski-sunucu.ornek.example",
            "new": "yeni-sunucu.ornek.example",
            "requested_by": {"user_id": 3, "username": "ayse"},
        },
    )
    import_alerts(sessions, layout, now=NOW, admin=admin)
    for key, value in {
        "alerts.email_recipients": ["nobet@ornek.example"],
        "alerts.email_digest_seconds": 0,
    }.items():
        set_setting(db_session, key, value, updated_by="tolga")
    db_session.commit()
    smtp = SmtpConfig(
        enabled=True,
        host=smtp_server.host,
        port=smtp_server.port,
        security="none",
        from_address="arcilogger@ornek.example",
        timeout_seconds=2,
    )
    config = Config(data_dir=layout.root, secrets_dir=layout.root, smtp=smtp)
    run_notify(sessions, config, now=NOW)
    (mail,) = smtp_server.messages_to("nobet@ornek.example")
    text, page = mail.part("plain"), mail.part("html")
    assert "Yapılandırma değişti: E-posta" in text
    assert "Configuration changed: E-mail (SMTP) (smtp.host)." in text
    for leak in ("eski-sunucu", "yeni-sunucu", "ayse"):
        assert leak not in text + page + str(mail.message["Subject"])


def test_an_administration_problem_is_told_by_its_code() -> None:
    assert describe("R20-admin", {"code": "signature_invalid"}) == MESSAGES["signature_invalid"]
    assert describe("R20-admin", {"code": "acl_drift", "path": "C:\\x"}) == MESSAGES["acl_drift"]
    assert describe("R20-admin", {"code": "no-such-code"}) == describe("R20-x", {})
    for rule in ("R20-admin", "R20-config-change"):
        text = rule_text(rule)
        assert text.name_tr != text.name_en
        steps_tr, steps_en = runbook(rule)
        assert steps_tr.startswith("1. ")
        assert steps_en.startswith("1. ")
