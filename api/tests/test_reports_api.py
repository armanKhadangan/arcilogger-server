"""``/api/v1/reports`` (group 2 contract section 4): schedules with validation and audit, "run
now", the run history, stored files with their SHA-256, the preview, permissions and the KVKK
notice."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.app import create_app
from arcilogger_api.deps import AppState
from arcilogger_api.lookup_testing import accept_privacy
from arcilogger_api.testing import EXAMPLE_SITE_NAME, LoggedIn, login, make_user
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStore
from arcilogger_core.timeutil import SITE_TZ, utc_now
from arcilogger_db.models import AuditLog, ReportRun, ReportSchedule
from arcilogger_worker.reports import runner
from arcilogger_worker.reports.schedule import Timing, next_occurrence, period_for
from arcilogger_worker.reports.testing import report_sample
from arcilogger_worker.usage import report_data

Login = Callable[..., LoggedIn]
API = "/api/v1/reports"
TO = "yonetim@example.com"


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    """SMTP switched on (nothing is sent here: the worker sends)."""
    return Config.model_validate(
        {
            "site_name": EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "collector": {"bind": "172.20.20.15"},
            "smtp": {"enabled": True, "host": "mail.example.com", "from_address": "a@example.com"},
        }
    )


def sample_engine(connection: Any, **kwargs: Any) -> dict[str, Any]:
    data = report_sample()
    data["kind"] = kwargs["kind"]
    data["period"] = {"from": kwargs["start"].isoformat(), "to": kwargs["end"].isoformat()}
    data["person_level"] = kwargs["person_level"]
    if not kwargs["person_level"]:
        data["persons"] = []
    return data


@pytest.fixture
def admin(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> LoggedIn:
    who = login_as(client, "admin", display_name="Tolga Yılmaz")
    accept_privacy(app_session_factory, who.user_id)
    return who


def audits(factory: sessionmaker[Session], action: str) -> list[dict[str, Any]]:
    with factory() as session:
        rows = session.scalars(
            select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id)
        )
        return [dict(row.details) for row in rows]


def schedule_id(factory: sessionmaker[Session], kind: str = "full_day") -> int:
    with factory() as session:
        return (
            session.scalars(
                select(ReportSchedule.id)
                .where(ReportSchedule.kind == kind)
                .order_by(ReportSchedule.id)
            ).first()
            or 0
        )


def body(**changes: Any) -> dict[str, Any]:
    return {
        "name": "Yönetim haftalık",
        "kind": "weekly",
        "time": "07:45",
        "weekday": 5,
        "recipients": [TO],
        "formats": {"email_html": True, "pdf": True, "xlsx": False},
        **changes,
    }


def error_of(response: Any, status: int) -> dict[str, Any]:
    assert response.status_code == status, response.text
    error: dict[str, Any] = response.json()["error"]
    return error


# -- schedules ------------------------------------------------------------------------------------


@pytest.mark.usefixtures("admin")
def test_the_shipped_schedules_are_listed(client: TestClient) -> None:
    response = client.get(f"{API}/schedules")
    assert response.status_code == 200
    items = response.json()
    assert [(s["name"], s["kind"], s["time"], s["enabled"]) for s in items] == [
        ("Gün sonu raporu", "end_of_day", "18:30", False),
        ("Tam gün raporu", "full_day", "07:00", False),
        ("Haftalık rapor", "weekly", "07:15", False),
        ("Aylık rapor", "monthly", "07:30", False),
    ]
    for item in items:
        assert item["recipients"] == []
        assert item["person_level"] is True
        assert item["pseudonymize"] is False
        assert item["formats"] == {"email_html": True, "pdf": True, "xlsx": True}
        assert (item["next_run_at"], item["last_run"], item["updated_by"]) == (None, None, ":seed")


@pytest.mark.usefixtures("admin")
def test_a_schedule_is_created_checked_changed_and_deleted(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    # Validation: weekday per kind, e-mail addresses, HH:MM, formats, recipients to enable.
    error = error_of(client.post(f"{API}/schedules", json=body(weekday=None)), 422)
    assert error["details"][0]["loc"] == ["body", "weekday"]
    error = error_of(client.post(f"{API}/schedules", json=body(monthday=3)), 422)
    assert error["details"][0]["loc"] == ["body", "monthday"]
    error = error_of(client.post(f"{API}/schedules", json=body(recipients=["yonetim"])), 422)
    assert error["details"][0]["loc"] == ["body", "recipients"]
    assert "geçersiz e-posta adresi" in error["details"][0]["msg"]
    for bad in ("7:45", "24:00", "07:60"):
        error_of(client.post(f"{API}/schedules", json=body(time=bad)), 422)
    # An end-of-day report at 00:00 would cover nothing (00:00 → 00:00).
    midnight = body(kind="end_of_day", time="00:00", weekday=None)
    error = error_of(client.post(f"{API}/schedules", json=midnight), 422)
    assert error["details"][0]["loc"] == ["body", "time"]
    assert "end_of_day" in error["details"][0]["msg"]
    error = error_of(
        client.post(
            f"{API}/schedules",
            json=body(formats={"email_html": False, "pdf": False, "xlsx": False}),
        ),
        422,
    )
    assert error["details"][0]["loc"] == ["body", "formats"]
    error = error_of(client.post(f"{API}/schedules", json=body(enabled=True, recipients=[])), 422)
    assert error["code"] == "report_recipients_required"
    error_of(client.post(f"{API}/schedules", json=body(name="  ")), 422)
    error_of(client.post(f"{API}/schedules", json=body(extra=1)), 422)
    # Created: addresses normalised (domain lower case, duplicates dropped), next run computed.
    before = utc_now()
    response = client.post(
        f"{API}/schedules",
        json=body(enabled=True, recipients=[" Yonetim@Example.COM ", "Yonetim@example.com"]),
    )
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["recipients"] == ["Yonetim@example.com"]
    assert created["updated_by"] == response.request.headers.get("x-user", created["updated_by"])
    expected = next_occurrence(Timing("weekly", "07:45", weekday=5), before)
    assert datetime.fromisoformat(created["next_run_at"]) == expected
    (entry,) = audits(app_session_factory, "report.schedule.create")
    assert entry["schedule_id"] == created["id"]
    assert entry["recipients"] == 1
    assert "example" not in json.dumps(entry).lower()  # never an address in the audit log
    # Changed: a new kind clears the weekday it no longer uses; disabling clears the next run.
    url = f"{API}/schedules/{created['id']}"
    error = error_of(client.patch(url, json={"kind": "monthly"}), 422)
    assert error["details"][0]["loc"] == ["body", "monthday"]
    error = error_of(client.patch(url, json={"kind": "end_of_day", "time": "00:00"}), 422)
    assert error["details"][0]["loc"] == ["body", "time"]
    changed = client.patch(url, json={"kind": "full_day", "time": "06:30"}).json()
    assert (changed["kind"], changed["weekday"], changed["time"]) == ("full_day", None, "06:30")
    changed = client.patch(url, json={"enabled": False, "person_level": False}).json()
    assert (changed["enabled"], changed["next_run_at"], changed["person_level"]) == (
        False,
        None,
        False,
    )
    error_of(client.patch(url, json={"name": None}), 422)
    error = error_of(client.patch(url, json={"enabled": True, "recipients": []}), 422)
    assert error["code"] == "report_recipients_required"
    updates = audits(app_session_factory, "report.schedule.update")
    assert [u["changed"] for u in updates] == [
        ["kind", "time", "weekday"],
        ["enabled", "person_level"],
    ]
    assert updates[1]["enabled"] == {"old": True, "new": False}
    assert client.patch(url, json={"time": "06:30"}).status_code == 200  # no change, no entry
    assert len(audits(app_session_factory, "report.schedule.update")) == 2
    # Deleted: its runs stay.
    run = client.post(f"{url}/run", json={"send": False})
    assert run.status_code == 202, run.text
    assert client.delete(url).status_code == 204
    assert client.delete(url).status_code == 404
    with app_session_factory() as session:
        kept = session.get(ReportRun, run.json()["id"])
        assert kept is not None
        assert kept.schedule_id is None
    (deleted,) = audits(app_session_factory, "report.schedule.delete")
    assert (deleted["schedule_id"], deleted["runs"]) == (created["id"], 1)


@pytest.mark.usefixtures("admin")
def test_an_edit_keeps_an_occurrence_the_worker_has_not_run_yet(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    """The worker was stopped at 18:30: renaming the schedule or adding a recipient must not
    drop today's report (it still runs once when the worker starts again); a new time does."""
    created = client.post(
        f"{API}/schedules", json=body(kind="end_of_day", time="18:30", weekday=None, enabled=True)
    ).json()
    url = f"{API}/schedules/{created['id']}"
    passed = utc_now() - timedelta(minutes=10)
    with app_session_factory() as session:
        row = session.get(ReportSchedule, created["id"])
        assert row is not None
        row.next_run_at = passed
        session.commit()
    for change in (
        {"name": "Gün sonu (yönetim)"},
        {"recipients": [TO, "ik@example.com"]},
        {"formats": {"email_html": True, "pdf": False, "xlsx": True}, "person_level": False},
    ):
        response = client.patch(url, json=change)
        assert response.status_code == 200, response.text
        assert datetime.fromisoformat(response.json()["next_run_at"]) == passed, change
    changed = client.patch(url, json={"time": "19:00"}).json()
    assert datetime.fromisoformat(changed["next_run_at"]) > utc_now()
    assert client.patch(url, json={"enabled": False}).json()["next_run_at"] is None
    assert client.patch(url, json={"enabled": True}).json()["next_run_at"] is not None


# -- run now --------------------------------------------------------------------------------------


@pytest.mark.usefixtures("admin")
def test_run_now_queues_a_run_for_the_worker(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    sid = schedule_id(app_session_factory)
    url = f"{API}/schedules/{sid}/run"
    # The shipped schedule has no recipients yet: sending is refused, files only are fine.
    assert error_of(client.post(url), 422)["code"] == "report_recipients_required"
    client.patch(f"{API}/schedules/{sid}", json={"recipients": [TO]})
    before = utc_now()
    response = client.post(url)
    assert response.status_code == 202, response.text
    run = response.json()
    start, end = period_for("full_day", before)
    assert run["kind"] == "full_day"
    assert datetime.fromisoformat(run["period"]["from"]) == start
    assert datetime.fromisoformat(run["period"]["to"]) == end
    assert (run["status"], run["triggered_by"], run["schedule_id"]) == ("queued", "manual", sid)
    assert run["requested_by"]
    assert (run["files"], run["deliveries"], run["finished_at"]) == ([], [], None)
    # A period given by hand is a custom run; a time without offset is site time.
    custom = client.post(url, json={"from": "2026-10-01T00:00:00", "to": "2026-10-08T00:00:00"})
    assert custom.status_code == 202, custom.text
    assert custom.json()["kind"] == "custom"
    assert datetime.fromisoformat(custom.json()["period"]["from"]) == datetime(
        2026, 10, 1, tzinfo=SITE_TZ
    )
    for wrong in (
        {"from": "2026-10-01T00:00:00"},
        {"from": "2026-10-08T00:00:00+03:00", "to": "2026-10-01T00:00:00+03:00"},
        {"from": "2026-01-01T00:00:00+03:00", "to": "2026-10-01T00:00:00+03:00"},
        {"send": "maybe"},
    ):
        error_of(client.post(url, json=wrong), 422)
    entries = audits(app_session_factory, "report.run")
    assert [(e["kind"], e["send"], e["recipients"]) for e in entries] == [
        ("full_day", True, 1),
        ("custom", True, 1),
    ]
    assert client.post(f"{API}/schedules/999999/run").status_code == 404


def test_sending_is_refused_while_smtp_is_off(api_config: Config, db_engine: Engine) -> None:
    config = api_config.model_copy(
        update={"smtp": api_config.smtp.model_copy(update={"enabled": False})}
    )
    app = create_app(config, db_engine)
    state: AppState = app.state.arci
    factory = state.session_factory()
    with TestClient(app, base_url="http://testserver") as client:
        who = login(client, factory, make_user(factory, "admin"))
        accept_privacy(factory, who.user_id)
        sid = schedule_id(factory)
        client.patch(f"{API}/schedules/{sid}", json={"recipients": [TO]})
        assert error_of(client.post(f"{API}/schedules/{sid}/run"), 409)["code"] == (
            "report_smtp_disabled"
        )
        assert client.post(f"{API}/schedules/{sid}/run", json={"send": False}).status_code == 202


# -- runs and files -------------------------------------------------------------------------------


def render(factory: sessionmaker[Session], layout: Layout, config: Config) -> RunDueReport_:
    """What the worker does within a minute (the sample instead of the usage engine)."""
    return runner.run_due(
        factory,
        layout,
        config.model_copy(update={"smtp": config.smtp.model_copy(update={"enabled": False})}),
        now=utc_now(),
        builder=sample_engine,
    )


RunDueReport_ = runner.RunDueReport


@pytest.mark.usefixtures("admin")
def test_runs_files_and_downloads(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    api_config: Config,
) -> None:
    sid = schedule_id(app_session_factory)
    queued = client.post(f"{API}/schedules/{sid}/run", json={"send": False}).json()
    other = client.post(f"{API}/schedules/{schedule_id(app_session_factory, 'weekly')}/run",
                        json={"send": False}).json()  # fmt: skip
    render(app_session_factory, layout, api_config)
    page = client.get(f"{API}/runs").json()
    assert [item["id"] for item in page["items"]] == [other["id"], queued["id"]]
    assert (page["total"], page["limit"], page["offset"]) == (2, 50, 0)
    page = client.get(f"{API}/runs", params={"schedule_id": sid, "status": "sent"}).json()
    assert [item["id"] for item in page["items"]] == [queued["id"]]
    assert (
        client.get(f"{API}/runs", params={"limit": 1, "offset": 1}).json()["items"][0]["id"]
        == queued["id"]
    )
    error_of(client.get(f"{API}/runs", params={"status": "done"}), 422)
    run = client.get(f"{API}/runs/{queued['id']}").json()
    assert run["status"] == "sent"
    assert [f["format"] for f in run["files"]] == ["html", "pdf", "xlsx"]
    assert client.get(f"{API}/schedules").json()[1]["last_run"]["id"] == queued["id"]
    assert client.get(f"{API}/runs/999999").status_code == 404
    # Download: the stored bytes, their hash, audited.
    pdf = run["files"][1]
    response = client.get(f"{API}/runs/{run['id']}/files/{pdf['name']}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.headers["x-arci-sha256"] == pdf["sha256"]
    assert hashlib.sha256(response.content).hexdigest() == pdf["sha256"]
    assert pdf["name"] in response.headers["content-disposition"]
    (entry,) = audits(app_session_factory, "report.download")
    assert entry == {
        "run_id": run["id"],
        "file": pdf["name"],
        "format": "pdf",
        "sha256": pdf["sha256"],
        "size": pdf["size"],
    }
    xlsx = client.get(f"{API}/runs/{run['id']}/files/{run['files'][2]['name']}")
    assert xlsx.headers["content-type"].startswith("application/vnd.openxmlformats")
    assert client.get(f"{API}/runs/{run['id']}/files/nothing.pdf").status_code == 404
    assert client.get(f"{API}/runs/{run['id']}/files/..%2Fsecret").status_code in (404, 422)
    # A changed file is refused, a deleted one too; expired files say so.
    path = layout.exports / "usage-reports" / str(run["id"]) / pdf["name"]
    path.write_bytes(b"%PDF-changed")
    error = error_of(client.get(f"{API}/runs/{run['id']}/files/{pdf['name']}"), 409)
    assert error["code"] == "report_damaged"
    path.unlink()
    error = error_of(client.get(f"{API}/runs/{run['id']}/files/{pdf['name']}"), 409)
    assert error["code"] == "report_missing"
    runner.purge_reports(app_session_factory, layout, now=utc_now() + timedelta(days=401))
    error = error_of(client.get(f"{API}/runs/{run['id']}/files/{run['files'][0]['name']}"), 410)
    assert error["code"] == "report_expired"


@pytest.mark.usefixtures("admin")
def test_a_delivery_waiting_for_its_retry_shows_as_failed(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        row = session.scalars(select(ReportSchedule).where(ReportSchedule.kind == "full_day"))
        run = runner.queue_run(
            session,
            row.one(),
            start=datetime(2026, 10, 19, tzinfo=SITE_TZ),
            end=datetime(2026, 10, 20, tzinfo=SITE_TZ),
            kind="full_day",
            triggered_by="schedule",
            now=utc_now(),
        )
        run.status = "running"
        run.deliveries = [
            {"recipient": "a@example.com", "status": "queued", "attempts": 0},
            {"recipient": "b@example.com", "status": "queued", "attempts": 2, "error": "451 x"},
            {
                "recipient": "c@example.com",
                "status": "sent",
                "attempts": 1,
                "error": None,
                "sent_at": "2026-10-20T07:01:00+03:00",
            },
        ]
        session.commit()
        run_id = run.id
    shown = client.get(f"{API}/runs/{run_id}").json()["deliveries"]
    assert shown == [
        {"recipient": "b@example.com", "status": "failed", "error": "451 x", "sent_at": None},
        {
            "recipient": "c@example.com",
            "status": "sent",
            "error": None,
            "sent_at": "2026-10-20T07:01:00+03:00",
        },
    ]


# -- preview --------------------------------------------------------------------------------------


@pytest.fixture
def engine_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The usage engine (area U) answers with the fixed sample."""
    monkeypatch.setattr(report_data, "build_report_data", sample_engine)


@pytest.mark.usefixtures("admin", "engine_installed")
def test_preview_renders_without_storing_or_sending(
    client: TestClient, app_session_factory: sessionmaker[Session], layout: Layout
) -> None:
    response = client.get(f"{API}/preview", params={"kind": "full_day"})
    assert response.status_code == 200, response.text
    assert response.headers["content-type"] == "text/html; charset=utf-8"
    assert "sandbox" in response.headers["content-security-policy"]
    assert "Önizleme: kaydedilmedi, gönderilmedi." in response.text
    assert "<b>Elif Kaya</b>" in response.text
    assert EXAMPLE_SITE_NAME in response.text  # site_name of the configuration
    pdf = client.get(f"{API}/preview", params={"kind": "weekly", "format": "pdf"})
    assert pdf.status_code == 200
    assert pdf.content.startswith(b"%PDF-")
    anonymous = client.get(
        f"{API}/preview",
        params={
            "kind": "custom",
            "from": "2026-10-01T00:00:00+03:00",
            "to": "2026-10-02T00:00:00+03:00",
            "person_level": "false",
        },
    )
    assert anonymous.status_code == 200
    assert "Elif Kaya" not in anonymous.text
    error_of(client.get(f"{API}/preview", params={"kind": "custom"}), 422)
    error_of(client.get(f"{API}/preview", params={"kind": "yearly"}), 422)
    error_of(client.get(f"{API}/preview", params={"kind": "full_day", "format": "xlsx"}), 422)
    entries = audits(app_session_factory, "report.preview")
    assert [(e["kind"], e["format"], e["person_level"]) for e in entries] == [
        ("full_day", "html", True),
        ("weekly", "pdf", True),
        ("custom", "html", False),
    ]
    with app_session_factory() as session:
        assert session.scalars(select(ReportRun)).all() == []
    assert not (layout.exports / "usage-reports").exists()


@pytest.mark.usefixtures("admin")
def test_preview_without_the_usage_engine(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    def missing(*args: Any, **kwargs: Any) -> dict[str, Any]:
        raise NotImplementedError("no usage engine")

    monkeypatch.setattr(report_data, "build_report_data", missing)
    error = error_of(client.get(f"{API}/preview", params={"kind": "full_day"}), 503)
    assert error["code"] == "usage_engine_unavailable"


def store_pseudonym_key(config: Config) -> SecretStore:
    key_path = config.secrets_dir / "secret.key"
    if not key_path.exists():
        SecretStore.create_key(key_path)
    store = SecretStore(key_path, config.secrets_dir / "secrets.json")
    store.set("usage.pseudonym_key", "report-api-test-key-0123456789")  # gitleaks:allow
    return store


@pytest.mark.usefixtures("admin")
def test_reports_with_pseudonyms_need_the_pseudonym_key(
    client: TestClient, app_session_factory: sessionmaker[Session], api_config: Config
) -> None:
    """Pseudonyms are optional and their key is not set by default: the API says how to set it
    (409, as the usage pages) instead of failing — a preview now, a scheduled run later."""
    preview = {"kind": "full_day", "pseudonymize": "true"}
    error = error_of(client.get(f"{API}/preview", params=preview), 409)
    assert error["code"] == "pseudonym_key_missing"
    assert "arcilogger-core secret set usage.pseudonym_key" in error["message_tr"]
    error = error_of(client.post(f"{API}/schedules", json=body(pseudonymize=True)), 409)
    assert error["code"] == "pseudonym_key_missing"
    sid = schedule_id(app_session_factory)
    url = f"{API}/schedules/{sid}"
    error = error_of(client.patch(url, json={"pseudonymize": True}), 409)
    assert error["code"] == "pseudonym_key_missing"
    store = store_pseudonym_key(api_config)
    response = client.get(f"{API}/preview", params=preview)
    assert response.status_code == 200, response.text
    assert "takma adlarla / pseudonymised" in response.text
    assert client.patch(url, json={"pseudonymize": True, "recipients": [TO]}).status_code == 200
    # The key removed again: "run now" would only make a failed run.
    store.delete("usage.pseudonym_key")
    error = error_of(client.post(f"{url}/run", json={"send": False}), 409)
    assert error["code"] == "pseudonym_key_missing"
    assert client.patch(url, json={"name": "Tam gün (takma adlarla)"}).status_code == 200
    with app_session_factory() as session:
        assert session.scalars(select(ReportRun)).all() == []


# -- permissions ----------------------------------------------------------------------------------


@pytest.mark.parametrize("role", ["auditor", "hr", "viewer"])
def test_only_report_managers_may_use_the_reports(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session], role: str
) -> None:
    who = login_as(client, role)
    accept_privacy(app_session_factory, who.user_id)
    for method, path in (
        ("get", "/schedules"),
        ("post", "/schedules"),
        ("patch", "/schedules/1"),
        ("delete", "/schedules/1"),
        ("post", "/schedules/1/run"),
        ("get", "/runs"),
        ("get", "/runs/1"),
        ("get", "/runs/1/files/a.pdf"),
        ("get", "/preview?kind=full_day"),
    ):
        response = client.request(method, f"{API}{path}", json={} if method != "get" else None)
        assert error_of(response, 403)["code"] == "forbidden", (method, path)


def test_person_level_content_needs_the_privacy_notice(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "admin")
    assert client.get(f"{API}/schedules").status_code == 200
    assert client.get(f"{API}/runs").status_code == 200
    sid = schedule_id(app_session_factory)
    for method, path in (
        ("post", f"/schedules/{sid}/run"),
        ("get", "/runs/1/files/a.pdf"),
        ("get", "/preview?kind=full_day"),
    ):
        response = client.request(method, f"{API}{path}")
        assert error_of(response, 403)["code"] == "privacy_notice_required", path
