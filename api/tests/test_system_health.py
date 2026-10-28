"""``GET /api/v1/system/health`` (Wave 4 contract 2.6): services, agent, ApplySystem, the
scheduled tasks, the worker jobs and the clock, each from the files and rows it is judged by.

The router is not mounted before package W4-F: :func:`app` mounts it here unless the app already
has the route (contract Implementation note 3), so these tests keep passing once it is mounted.
The verification results are named with the CLI's own expression (:func:`verify_name`, and one
test runs the real ``arcilogger-core verify``), never by hand (contract Implementation note 7).
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from pathlib import Path
from typing import Any, get_args

import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, func, select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.app import API_PREFIX
from arcilogger_api.system_health import routes
from arcilogger_api.system_health.jobs import from_time_tr, schedule_texts
from arcilogger_api.system_health.models import TaskName
from arcilogger_api.system_health.tasks import TASKS, Evidence, task_health, verify_state
from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.contract import SystemStatus, dump_document, parse_document
from arcilogger_core.admin.testing import FakeAgent, FakeSystem, Installed
from arcilogger_core.cli import main as core_main
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site, site_day, to_site, utc_now
from arcilogger_db.models import AuditLog, JobRun
from arcilogger_worker.alerts.system import backup_state_path
from arcilogger_worker.service import Job, JobState, default_jobs
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

Login = Callable[..., LoggedIn]
API = f"{API_PREFIX}/system/health"
TASK_FILES = Path(__file__).resolve().parents[2] / "deploy" / "windows" / "tasks"


@pytest.fixture
def app(app: FastAPI) -> FastAPI:
    """The app with the health router (mounted by ``routers/system_health.py`` from W4-F on)."""
    if not any(isinstance(route, APIRoute) and route.path == API for route in app.routes):
        app.include_router(routes.router, prefix=API_PREFIX)
    return app


def verify_name(days: int) -> str:
    """The file ``arcilogger-core verify --last N`` writes after verifying ``days`` days, with
    the expression of ``cmd_verify`` (``core/src/arcilogger_core/cli.py``)."""
    kind = f"last-{days}"
    return f"last-{kind}.json"


def get(client: TestClient) -> dict[str, Any]:
    response = client.get(API)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


def moment(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def touch(path: Path, at: datetime) -> None:
    os.utime(path, (at.timestamp(), at.timestamp()))


def by(items: list[dict[str, Any]], key: str) -> dict[str, dict[str, Any]]:
    return {item[key]: item for item in items}


# -- services, agent, ApplySystem -----------------------------------------------------------------


def test_services_from_status_file_age(client: TestClient, login_as: Login, layout: Layout) -> None:
    now = utc_now()
    fresh = now - timedelta(seconds=30)
    write_json(
        layout.collector_status,
        {"updated_at": isoformat_site(fresh, "seconds"), "sources": {"fortigate": {}}},
    )
    write_json(layout.ingest_status, {"updated_at": isoformat_site(now - timedelta(minutes=5))})
    login_as(client, "admin")
    services = get(client)["services"]
    assert [service["name"] for service in services] == [
        "ArciCollector",
        "ArciIngest",
        "ArciWorker",
        "ArciApi",
    ]
    named = by(services, "name")
    assert named["ArciCollector"]["state"] == "running"
    assert moment(named["ArciCollector"]["updated_at"]) == fresh.replace(microsecond=0)
    assert named["ArciIngest"]["state"] == "stale"
    assert named["ArciWorker"] == {"name": "ArciWorker", "state": "missing", "updated_at": None}
    assert named["ArciApi"]["state"] == "running"  # it answers

    # A status file without a readable time is judged by its modification time.
    layout.worker_status.write_text("{not json", encoding="utf-8")
    touch(layout.worker_status, now - timedelta(minutes=3))
    assert by(get(client)["services"], "name")["ArciWorker"]["state"] == "stale"
    touch(layout.worker_status, utc_now())
    assert by(get(client)["services"], "name")["ArciWorker"]["state"] == "running"


def test_agent_and_apply_system_states(
    client: TestClient, login_as: Login, installed: Installed
) -> None:
    login_as(client, "admin")
    body = get(client)
    # Browser administration on, but the agent never wrote a heartbeat nor ApplySystem a report.
    assert body["agent"] == {"state": "missing", "heartbeat_at": None}
    assert body["apply_system"] == {"state": "never", "at": None, "code": None}
    assert body["time"]["applied_peers"] is None
    assert by(body["tasks"], "task")["ApplySystem"]["state"] == "missing"

    agent = FakeAgent(installed, system=FakeSystem(installed))
    agent.process()
    body = get(client)
    assert body["agent"]["state"] == "ok"
    assert moment(body["agent"]["heartbeat_at"]) is not None
    status = parse_document(SystemStatus, installed.layout.system_status.read_bytes())
    assert body["apply_system"] == {
        "state": "ok",
        "at": isoformat_site(status.at),
        "code": None,
    }
    assert body["time"]["applied_peers"] == status.time.peers
    assert body["time"]["applied_peers"]  # the configured servers were applied
    assert moment(body["time"]["applied_at"]) == status.time.changed_at
    assert by(body["tasks"], "task")["ApplySystem"]["state"] == "ok"

    failed = status.model_copy(
        update={"ok": False, "code": "system_sync_failed", "at": utc_now() - timedelta(minutes=20)}
    )
    installed.layout.system_status.write_bytes(dump_document(failed))
    body = get(client)
    assert body["apply_system"]["state"] == "failed"
    assert body["apply_system"]["code"] == "system_sync_failed"
    assert by(body["tasks"], "task")["ApplySystem"]["state"] == "late"  # 15 minutes

    installed.write_policy(enabled=False)  # browser administration switched off
    body = get(client)
    assert body["agent"]["state"] == "disabled"
    assert body["apply_system"]["state"] == "disabled"
    assert by(body["tasks"], "task")["ApplySystem"]["state"] == "not_used"


def test_agent_heartbeat_goes_stale_and_missing(
    client: TestClient, login_as: Login, installed: Installed
) -> None:
    agent = FakeAgent(installed, now=utc_now() - timedelta(seconds=60))
    agent.start()
    login_as(client, "admin")
    assert get(client)["agent"]["state"] == "stale"
    agent.advance(timedelta(minutes=-5))
    agent.agent.heartbeat()
    assert get(client)["agent"]["state"] == "missing"


def test_without_an_installation_administration_is_off(client: TestClient, login_as: Login) -> None:
    """A configuration given in memory (no configuration file): no browser administration."""
    login_as(client, "admin")
    body = get(client)
    assert body["agent"] == {"state": "disabled", "heartbeat_at": None}
    assert body["apply_system"] == {"state": "disabled", "at": None, "code": None}


# -- scheduled tasks ------------------------------------------------------------------------------


def test_task_table_matches_the_task_files() -> None:
    """One row per file of ``deploy/windows/tasks/``, and every time of a row's texts is in
    its task file: adding a task file or moving a task's time fails here."""
    files = {path.stem: path.read_text(encoding="utf-8") for path in TASK_FILES.glob("*.xml")}
    rows = [row.task for row in TASKS]
    assert len(rows) == len(set(rows))
    assert sorted(rows) == sorted(files)
    assert sorted(get_args(TaskName)) == sorted(files)
    for row in TASKS:
        xml = files[row.task]
        times = re.findall(r"\b\d{2}:\d{2}\b", f"{row.schedule_tr} {row.schedule_en}")
        for found in times:
            assert f"T{found}:00" in xml, (row.task, found)
        assert sorted(set(re.findall(r"\d{2}:\d{2}", row.schedule_tr))) == sorted(
            set(re.findall(r"\d{2}:\d{2}", row.schedule_en))
        ), row.task
        # The cadence of the texts is the trigger's.
        if "5 dakikada" in row.schedule_tr:
            assert "<Interval>PT5M</Interval>" in xml, row.task
        if row.schedule_tr.startswith("Saatte bir"):
            assert "<Interval>PT1H</Interval>" in xml, row.task
        if row.schedule_tr.startswith("Olayla"):
            assert "<EventTrigger>" in xml, row.task
        if row.schedule_tr.startswith("Pazar"):
            assert "<Sunday />" in xml, row.task
        if row.schedule_tr.startswith("Ayın 1'i"):
            assert "<Day>1</Day>" in xml, row.task
        if row.schedule_tr.startswith("Her gün") and "dakikada" not in row.schedule_tr:
            assert "<DaysInterval>1</DaysInterval>" in xml, row.task
            assert "<Repetition>" not in xml, row.task
    # The verification tasks write the results their rows read.
    assert "verify --last 2" in files["VerifyDaily"]
    assert "verify --last 90" in files["VerifyWeekly"]
    assert "verify --all" in files["VerifyMonthly"]


def test_task_times_carry_the_turkish_suffix_of_how_they_are_read() -> None:
    for row in TASKS:
        for clock, suffix in re.findall(r"(\d{2}:\d{2})'(\w+)", row.schedule_tr):
            assert from_time_tr(time.fromisoformat(clock)) == f"{clock}'{suffix}", row.task


def test_task_states_from_evidence_files(
    client: TestClient, login_as: Login, layout: Layout
) -> None:
    """The defaults of the test configuration: replica and retention off, the mock TSA, the
    backup on, no browser administration."""
    login_as(client, "admin")
    tasks = by(get(client)["tasks"], "task")
    assert [task["task"] for task in get(client)["tasks"]] == [row.task for row in TASKS]
    assert {name: task["state"] for name, task in tasks.items()} == {
        "Seal": "missing",
        "VerifyDaily": "missing",
        "VerifyWeekly": "missing",
        "VerifyMonthly": "missing",
        "Replica": "not_used",
        "Retention": "not_used",
        "Credit": "not_used",
        "ClockCheck": "missing",
        "Backup": "missing",
        "Requests": "not_observable",
        "ApplySystem": "not_used",
    }
    assert tasks["Seal"]["schedule_tr"] == "Her gün 00:10'dan sonra 5 dakikada bir"
    assert tasks["Seal"]["schedule_en"] == "Every 5 minutes from 00:10"
    assert tasks["Backup"]["evidence"] == "state\\backup\\last.json"

    now = utc_now()
    yesterday = site_day(now) - timedelta(days=1)
    write_json(layout.seal_state(yesterday), {"day": yesterday.isoformat(), "status": "sealed"})
    touch(layout.seal_state(yesterday), now - timedelta(hours=2))
    verify = layout.state / "verify"
    write_json(verify / verify_name(1), {"at": isoformat_site(now - timedelta(hours=30))})
    write_json(verify / verify_name(2), {"at": isoformat_site(now - timedelta(hours=27))})
    # Names the CLI never writes (``--last 2`` and ``--last 90`` by hand) are no results.
    write_json(verify / "last-2.json", {"at": isoformat_site(now)})
    write_json(verify / "last-90.json", {"at": isoformat_site(now)})
    write_json(verify / verify_name(90), {"at": isoformat_site(now - timedelta(days=2))})
    write_json(verify / "last-full.json", {"at": isoformat_site(now - timedelta(days=33))})
    write_json(verify / "last-day.json", {"at": isoformat_site(now)})  # "Doğrula" of one day
    write_json(backup_state_path(layout), {"ok": False, "finished": isoformat_site(now)})
    tasks = by(get(client)["tasks"], "task")
    assert tasks["Seal"]["state"] == "ok"
    sealed_at = moment(tasks["Seal"]["last_evidence_at"])  # the file's modification time
    assert sealed_at is not None
    assert abs(sealed_at - (now - timedelta(hours=2))) < timedelta(seconds=1)
    assert tasks["VerifyDaily"]["state"] == "late"
    assert moment(tasks["VerifyDaily"]["last_evidence_at"]) == now - timedelta(hours=27)
    assert tasks["VerifyWeekly"]["state"] == "ok"
    assert moment(tasks["VerifyWeekly"]["last_evidence_at"]) == now - timedelta(days=2)
    assert tasks["VerifyMonthly"]["state"] == "late"
    # The task ran; whether the dump succeeded is R20-backup's (Uyarılar).
    assert tasks["Backup"]["state"] == "ok"

    touch(layout.seal_state(yesterday), now - timedelta(hours=27))
    write_json(verify / verify_name(2), {"at": isoformat_site(now - timedelta(hours=1))})
    write_json(verify / verify_name(3), {"at": isoformat_site(now - timedelta(days=9))})
    (verify / verify_name(90)).unlink()
    tasks = by(get(client)["tasks"], "task")
    assert tasks["Seal"]["state"] == "late"
    assert tasks["VerifyDaily"]["state"] == "ok"
    assert tasks["VerifyWeekly"]["state"] == "late"  # a young chain: --last 90 verified 3 days

    # Retention rewrites the state of each day it deletes (``retention._delete_day``): it is
    # not Seal's evidence, nor is an old day's state touched by anything else.
    old = site_day(now) - timedelta(days=800)
    write_json(
        layout.seal_state(old),
        {"day": old.isoformat(), "status": "deleted", "deleted_by": "retention-1.json"},
    )
    assert by(get(client)["tasks"], "task")["Seal"]["state"] == "late"
    layout.seal_state(yesterday).unlink()
    assert by(get(client)["tasks"], "task")["Seal"]["state"] == "missing"


def test_unreadable_evidence_is_no_evidence(
    client: TestClient, login_as: Login, layout: Layout, api_config: Config
) -> None:
    """A file that cannot be read never fails the page (Channel's rule): a credit log that is
    a folder, a verification result whose number is not ASCII."""
    layout.credit_log.mkdir(parents=True)
    verify = layout.state / "verify"
    write_json(verify / "last-last-\u00b2.json", {"at": isoformat_site(utc_now())})
    login_as(client, "admin")
    tasks = by(get(client)["tasks"], "task")
    assert (tasks["Credit"]["state"], tasks["VerifyWeekly"]["state"]) == ("not_used", "missing")
    zamane = api_config.model_copy(
        update={"tsa": api_config.tsa.model_copy(update={"provider": "zamane"})}
    )
    assert states(evidence(zamane, utc_now()))["Credit"] == "missing"


def evidence(config: Config, now: datetime, **values: Any) -> Evidence:
    return Evidence(
        layout=Layout(config.data_dir),
        config=config,
        now=now,
        clock_times=values.pop("clock_times", ()),
        system_at=values.pop("system_at", None),
        administration=values.pop("administration", True),
    )


def states(found: Evidence) -> dict[str, str]:
    return {task.task: task.state for task in task_health(found)}


def test_task_states_follow_the_configuration(api_config: Config, tmp_path: Path) -> None:
    now = utc_now()
    on = api_config.model_copy(
        update={
            "replica": api_config.replica.model_copy(
                update={"enabled": True, "target": tmp_path / "nas"}
            ),
            "retention": api_config.retention.model_copy(update={"enabled": True}),
            "tsa": api_config.tsa.model_copy(update={"provider": "zamane"}),
            "backup": api_config.backup.model_copy(update={"enabled": False}),
        }
    )
    layout = Layout(on.data_dir)
    found = states(evidence(on, now))
    assert (found["Replica"], found["Retention"], found["Credit"], found["Backup"]) == (
        "missing",
        "not_observable",
        "missing",
        "not_used",
    )
    today = site_day(now)
    write_json(
        layout.replica_state(today - timedelta(days=1)),
        {"status": "ok", "verified_at": isoformat_site(now - timedelta(hours=3))},
    )
    write_json(  # older than the 3 days read
        layout.replica_state(today - timedelta(days=3)),
        {"status": "ok", "verified_at": isoformat_site(now)},
    )
    layout.credit_log.write_text(
        "\n".join(
            json.dumps({"time": isoformat_site(at), "provider": "zamane", "credits": credits})
            for at, credits in ((now - timedelta(hours=50), 900), (now - timedelta(hours=27), 899))
        )
        + "\n",
        encoding="utf-8",
    )
    rows = by([task.model_dump(mode="json") for task in task_health(evidence(on, now))], "task")
    assert rows["Replica"]["state"] == "ok"
    assert moment(rows["Replica"]["last_evidence_at"]) == now - timedelta(hours=3)
    assert rows["Credit"]["state"] == "late"
    assert moment(rows["Credit"]["last_evidence_at"]) == now - timedelta(hours=27)
    assert rows["Retention"]["last_evidence_at"] is None

    clocked = evidence(
        api_config,
        now,
        clock_times=(now - timedelta(hours=3), now - timedelta(minutes=70)),
        system_at=now - timedelta(minutes=14),
    )
    found = states(clocked)
    assert (found["ClockCheck"], found["ApplySystem"]) == ("ok", "ok")
    assert states(evidence(api_config, now + timedelta(minutes=60)))["ClockCheck"] == "missing"
    late = evidence(api_config, now, clock_times=(now - timedelta(hours=2, seconds=1),))
    assert states(late)["ClockCheck"] == "late"
    assert states(evidence(api_config, now, administration=False))["ApplySystem"] == "not_used"
    assert states(evidence(api_config, now))["Requests"] == "not_observable"


@pytest.fixture
def sealed(api_config: Config) -> Iterator[SealedVault]:
    """The three days before today sealed with the mock TSA in the API's data folder."""
    today = site_day(utc_now())
    vault = make_vault(api_config.data_dir, tsa_now=at_site(today - timedelta(days=30)))
    vault.seal_days([today - timedelta(days=n) for n in (3, 2, 1)])
    yield vault
    vault.unlock_all()


def test_verify_tasks_read_the_names_the_cli_writes(
    sealed: SealedVault, api_config: Config
) -> None:
    """The real ``arcilogger-core verify`` of the three verification tasks (their arguments
    from the task files) on a chain of three sealed days."""
    config = sealed.write_config(sealed.root / "verify-test.toml")
    for task in ("VerifyDaily", "VerifyWeekly", "VerifyMonthly"):
        xml = (TASK_FILES / f"{task}.xml").read_text(encoding="utf-8")
        arguments = re.search(r"arcilogger_core (verify[^<]*)</Arguments>", xml)
        assert arguments is not None, task
        core_main(["--config", str(config), *arguments[1].split()])
    layout = Layout(api_config.data_dir)
    written = sorted(path.name for path in (layout.state / "verify").glob("last-*.json"))
    # --last 2 verified 2 days, --last 90 the 3 there are, --all the whole chain.
    assert written == sorted([verify_name(2), verify_name(3), "last-full.json"])
    assert verify_state(layout, 2).name == verify_name(2)
    found = states(evidence(api_config, utc_now()))
    assert (found["VerifyDaily"], found["VerifyWeekly"], found["VerifyMonthly"]) == (
        "ok",
        "ok",
        "ok",
    )
    assert found["Seal"] == "ok"


# -- worker jobs ----------------------------------------------------------------------------------


def write_worker_status(
    layout: Layout, jobs: dict[str, JobState], started_at: datetime | None = None
) -> None:
    """The worker's status file as it writes it between its ticks (started an hour ago)."""
    now = utc_now()
    write_json(
        layout.worker_status,
        {
            "service": "arcilogger-worker",
            "state": "running",
            "started_at": (started_at or now - timedelta(hours=1)).isoformat(),
            "updated_at": isoformat_site(now),
            "jobs": {name: state.as_json() for name, state in jobs.items()},
        },
    )


def add_runs(factory: sessionmaker[Session], *runs: tuple[str, datetime, int | None, str]) -> None:
    """``(job, started_at, duration in ms or None while running, status)``."""
    with factory() as session:
        for job, started, duration, status in runs:
            session.add(
                JobRun(
                    job=job,
                    started_at=started,
                    finished_at=None
                    if duration is None
                    else started + timedelta(milliseconds=duration),
                    status=status,
                    details={"duration_ms": duration} if duration is not None else {},
                )
            )
        session.commit()


def test_jobs_from_worker_status_and_job_runs(
    client: TestClient,
    login_as: Login,
    layout: Layout,
    api_config: Config,
    app_session_factory: sessionmaker[Session],
) -> None:
    now = utc_now()
    write_worker_status(
        layout,
        {
            "license.check": JobState(
                last_started=now - timedelta(minutes=2),
                last_finished=now - timedelta(minutes=2) + timedelta(milliseconds=1234),
                last_status="ok",
                next_due=now + timedelta(minutes=3),
                runs=12,
            ),
            "alerts.system": JobState(
                last_started=now - timedelta(minutes=1),
                last_finished=now - timedelta(seconds=50),
                last_status="failed",
                last_error="OperationalError: x",
                failures=2,
            ),
            "usage.today": JobState(
                last_started=now - timedelta(minutes=4),
                last_finished=now - timedelta(minutes=4),
                last_status="skipped",
            ),
            # The worker writes its status between ticks: the run it is in is in job_runs only.
            "leases.incremental": JobState(
                last_started=now - timedelta(seconds=65),
                last_finished=now - timedelta(seconds=64),
                last_status="ok",
            ),
            "partitions.ensure": JobState(),  # never ran since the worker started
            # The database refused the run's row: started, never finished, not counted.
            "alerts.import": JobState(
                last_started=now - timedelta(minutes=1),
                last_finished=now - timedelta(minutes=2),
                last_status="failed",
                last_error="database unreachable",
            ),
        },
    )
    add_runs(
        app_session_factory,
        ("license.check", now - timedelta(days=8), 99, "ok"),
        ("license.check", now - timedelta(minutes=7), 2000, "failed"),
        ("license.check", now - timedelta(minutes=2), 1234, "ok"),
        ("leases.incremental", now - timedelta(seconds=65), 1000, "ok"),
        ("leases.incremental", now - timedelta(seconds=5), None, "running"),
        # The run the status file names as done, whose end the database did not take.
        ("alerts.system", now - timedelta(minutes=1), None, "running"),
        ("evidence.purge", now - timedelta(hours=5), 321, "ok"),
        # Left behind by a worker that stopped in the middle of the run (before the restart).
        ("evidence.purge", now - timedelta(hours=3), None, "running"),
        ("reports.purge", now - timedelta(days=9), 50, "ok"),
    )
    login_as(client, "admin")
    jobs = get(client)["jobs"]
    listed = default_jobs(api_config.worker.interval_seconds)
    assert [job["job"] for job in jobs] == [job.name for job in listed]
    named = by(jobs, "job")
    check = named["license.check"]
    assert check["state"] == "ok"
    assert (check["last_status"], check["failures"], check["duration_ms"]) == ("ok", 0, 1234)
    assert moment(check["last_started"]) == now - timedelta(minutes=2)
    assert moment(check["next_due"]) == now + timedelta(minutes=3)
    assert (check["schedule_tr"], check["schedule_en"]) == ("Her 5 dakikada bir", "Every 5 minutes")
    assert check["module"] is None

    assert named["alerts.system"]["state"] == "failing"
    assert named["alerts.system"]["failures"] == 2
    assert named["alerts.import"]["state"] == "failing"
    assert named["usage.today"]["state"] == "skipped"
    assert named["usage.today"]["module"] == next(
        j.module for j in listed if j.name == "usage.today"
    )
    running = named["leases.incremental"]
    assert running["state"] == "running"
    assert moment(running["last_started"]) == now - timedelta(seconds=5)
    assert moment(running["last_finished"]) == now - timedelta(seconds=64)
    # The status and duration of the run before (the running row has none).
    assert (running["last_status"], running["duration_ms"]) == ("ok", 1000)
    assert (
        named["leases.incremental"]["schedule_tr"],
        named["leases.incremental"]["schedule_en"],
    ) == (
        "Her 1 dakikada bir",
        "Every 1 minute",
    )
    never = named["partitions.ensure"]
    assert never["state"] == "never"
    assert (never["last_started"], never["duration_ms"], never["next_due"]) == (None, None, None)
    assert (never["schedule_tr"], never["schedule_en"]) == (
        "Her gün 00:15'ten sonra",
        "Daily after 00:15",
    )
    # Not in the worker's state (it is not running): the newest run of the last 7 days.
    purge = named["evidence.purge"]
    assert (purge["state"], purge["last_status"], purge["duration_ms"]) == ("ok", "ok", 321)
    assert moment(purge["last_started"]) == now - timedelta(hours=5)
    # Only a run older than 7 days: not looked at.
    assert named["reports.purge"]["state"] == "never"
    assert named["reports.purge"]["duration_ms"] is None


def test_schedule_texts_of_every_unit() -> None:
    def texts(**schedule: Any) -> tuple[str, str]:
        return schedule_texts(Job("x", lambda ctx: {}, **schedule))

    assert texts(every=timedelta(seconds=15)) == ("Her 15 saniyede bir", "Every 15 seconds")
    assert texts(every=timedelta(seconds=90)) == ("Her 90 saniyede bir", "Every 90 seconds")
    assert texts(every=timedelta(minutes=10)) == ("Her 10 dakikada bir", "Every 10 minutes")
    assert texts(every=timedelta(hours=1)) == ("Her 1 saatte bir", "Every 1 hour")
    assert texts(every=timedelta(hours=2)) == ("Her 2 saatte bir", "Every 2 hours")
    assert texts(daily_after=time(0, 5)) == ("Her gün 00:05'ten sonra", "Daily after 00:05")
    assert texts(daily_after=time(0, 45)) == ("Her gün 00:45'ten sonra", "Daily after 00:45")
    assert texts(daily_after=time(3, 0)) == ("Her gün 03:00'ten sonra", "Daily after 03:00")
    assert texts(daily_after=time(3, 20)) == ("Her gün 03:20'den sonra", "Daily after 03:20")
    assert texts(daily_after=time(3, 30)) == ("Her gün 03:30'dan sonra", "Daily after 03:30")
    assert texts(daily_after=time(0, 40)) == ("Her gün 00:40'tan sonra", "Daily after 00:40")
    assert texts(daily_after=time(0, 0)) == ("Her gün 00:00'dan sonra", "Daily after 00:00")
    assert texts(daily_after=time(6, 0)) == ("Her gün 06:00'dan sonra", "Daily after 06:00")


def test_health_reads_bounded_probes_per_job(
    client: TestClient, login_as: Login, db_engine: Engine, api_config: Config
) -> None:
    """``job_runs`` is not purged (owner question Q21): per job one ``LIMIT 1`` probe of the
    last 7 days for the last finished run and one past it for the run in progress, never a
    ``DISTINCT ON`` over the table."""
    login_as(client, "admin")
    statements: list[str] = []

    def record(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        if "job_runs" in statement:
            statements.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        get(client)
    finally:
        event.remove(db_engine, "before_cursor_execute", record)
    jobs = len(default_jobs(api_config.worker.interval_seconds))
    assert len(statements) == 2 * jobs
    for statement in statements:
        assert "DISTINCT" not in statement.upper()
        assert "LIMIT" in statement.upper()
        assert "job_runs.job =" in statement
    finished = [s for s in statements if "job_runs.status !=" in s]
    running = [s for s in statements if "job_runs.status =" in s]
    assert (len(finished), len(running)) == (jobs, jobs)
    assert all("job_runs.started_at >=" in statement for statement in finished)
    assert all("job_runs.started_at >" in statement for statement in running)


def test_health_returns_no_job_error_text(
    client: TestClient,
    login_as: Login,
    layout: Layout,
    app_session_factory: sessionmaker[Session],
) -> None:
    """A job's error text may name anything (a host, a file, an address): never in the answer."""
    secret = "Hata metni 172.20.10.55 00:00:5E:00:53:01 D:\\ARCILOGGER\\gizli"
    now = utc_now()
    write_worker_status(
        layout,
        {
            "alerts.system": JobState(
                last_started=now - timedelta(minutes=1),
                last_finished=now - timedelta(seconds=55),
                last_status="failed",
                last_error=secret,
                failures=1,
            )
        },
    )
    with app_session_factory() as session:
        session.add(
            JobRun(
                job="alerts.system",
                started_at=now - timedelta(minutes=1),
                finished_at=now - timedelta(seconds=55),
                status="failed",
                details={"error": "RuntimeError", "message": secret},
            )
        )
        session.commit()
    login_as(client, "admin")
    response = client.get(API)
    assert response.status_code == 200, response.text
    assert "172.20.10.55" not in response.text
    assert "gizli" not in response.text
    assert "RuntimeError" not in response.text
    assert "last_error" not in response.text
    job = by(response.json()["jobs"], "job")["alerts.system"]
    assert job["state"] == "failing"
    assert set(job) == {
        "job",
        "schedule_tr",
        "schedule_en",
        "module",
        "state",
        "last_status",
        "last_started",
        "last_finished",
        "duration_ms",
        "failures",
        "next_due",
    }


# -- the clock ------------------------------------------------------------------------------------


def test_time_samples_of_the_last_24_hours(
    client: TestClient, login_as: Login, layout: Layout, api_config: Config
) -> None:
    now = utc_now().replace(microsecond=0)
    samples: list[tuple[datetime, dict[str, Any]]] = [
        (now - timedelta(hours=25), {"ok": True, "server": "172.20.10.1", "offset_us": 1}),
        (
            now - timedelta(hours=23),
            {
                "ok": True,
                "server": "172.20.10.1",
                "offset_us": -1500,
                "delay_us": 2250,
                "stratum": 2,
            },
        ),
        (
            now - timedelta(hours=2),
            {"ok": False, "server": None, "offset_us": None, "error": "zaman aşımı"},
        ),
        (
            now - timedelta(minutes=10),
            {
                "ok": True,
                "server": "192.0.2.123",
                "offset_us": 250,
                "delay_us": 1000,
                "stratum": 3,
            },
        ),
    ]
    for at, sample in samples:
        path = layout.clock_samples(site_day(at))
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"time": isoformat_site(at, "seconds"), **sample}) + "\n")
    path.write_text(path.read_text(encoding="utf-8") + "{broken\n", encoding="utf-8")
    login_as(client, "admin")
    body = get(client)
    block = body["time"]
    assert block["ntp_server"] is None  # not set in the test configuration
    assert block["fallback_server"] == api_config.clock.fallback_server
    assert block["alert_offset_ms"] == api_config.clock.alert_offset_ms
    assert (block["applied_peers"], block["applied_at"]) == (None, None)
    assert [moment(sample["time"]) for sample in block["samples"]] == [
        now - timedelta(minutes=10),
        now - timedelta(hours=2),
        now - timedelta(hours=23),
    ]
    newest, failed, oldest = block["samples"]
    assert newest == {
        "time": isoformat_site(now - timedelta(minutes=10), "seconds"),
        "server": "192.0.2.123",
        "ok": True,
        "offset_ms": 0.25,
        "delay_ms": 1.0,
        "stratum": 3,
    }
    assert failed == {
        "time": isoformat_site(now - timedelta(hours=2), "seconds"),
        "server": None,
        "ok": False,
        "offset_ms": None,
        "delay_ms": None,
        "stratum": None,
    }
    assert (oldest["offset_ms"], oldest["delay_ms"], oldest["stratum"]) == (-1.5, 2.25, 2)
    assert by(body["tasks"], "task")["ClockCheck"]["state"] == "ok"
    assert moment(by(body["tasks"], "task")["ClockCheck"]["last_evidence_at"]) == to_site(
        now - timedelta(minutes=10)
    )


# -- permissions, audit ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "status"), [("admin", 200), ("auditor", 403), ("hr", 403), ("viewer", 403)]
)
def test_health_is_refused_without_system_read(
    client: TestClient, login_as: Login, role: str, status: int
) -> None:
    assert client.get(API).status_code == 401
    login_as(client, role)
    response = client.get(API)
    assert response.status_code == status, response.text
    if status == 403:
        assert response.json()["error"]["code"] == "forbidden"


def test_health_is_not_audited(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    login_as(client, "admin")

    def entries() -> int:
        with app_session_factory() as session:
            return int(session.scalar(select(func.count()).select_from(AuditLog)) or 0)

    before = entries()
    get(client)
    get(client)
    assert entries() == before


def test_checked_at_is_site_time(client: TestClient, login_as: Login) -> None:
    login_as(client, "admin")
    checked = moment(get(client)["checked_at"])
    assert checked is not None
    assert checked.utcoffset() == timedelta(hours=3)
    assert abs(checked - datetime.now(UTC)) < timedelta(minutes=1)
