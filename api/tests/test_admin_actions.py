"""The evidence actions of the browser administration (ADR-0014; contract 2.9, 2.11.3).

"Şimdi mühürle" (``POST /seals/{day}/seal``), "Kontör sorgula" (``POST /credits/query``) and the
replica test go through the real agent (``FakeAgent``), which signs a task request that the Seal
or Requests task answers (``FakeTasks``); the API follows the request to ``succeeded`` with the
task's result. The 409s of seal now come from the calendar state; the credit query and the
replica test are limited to one a minute. The test e-mail and the lease rebuild are rows of the
worker channel that the worker's own job works (``admin_jobs.process_requests``, a real SMTP
server of the test): five e-mails an hour, only to the alert recipients or one's own address,
the address masked in the audit log; a rebuild of at most 31 days that are not in the future and
not before the first DHCP record. Users with ``seal.manage`` follow their own action; nobody else
without ``settings.manage``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.testing import LoggedIn
from arcilogger_core.admin.contract import CreditResult, TaskRequest, TaskResult
from arcilogger_core.admin.testing import FakeAgent, Installed, SmtpTestServer
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day, utc_now
from arcilogger_db.models import AdminRequest, AuditLog
from arcilogger_db.settings import set_setting
from arcilogger_worker.admin_jobs import process_requests
from arcilogger_worker.testing import Evidence
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

pytestmark = pytest.mark.usefixtures("installed")

Login = Callable[..., LoggedIn]
V = "/api/v1"
TODAY = site_day(utc_now())
#: Four sealed days, a failed one (the oldest due) and one never tried, before today.
SEALED = [TODAY - timedelta(days=offset) for offset in (7, 6, 5, 4)]
FAILED, WAITING = TODAY - timedelta(days=3), TODAY - timedelta(days=2)


@pytest.fixture
def vault(api_config: Config) -> Iterator[SealedVault]:
    sealed = make_vault(api_config.data_dir, tsa_now=at_site(SEALED[0] - timedelta(days=30)))
    sealed.seal_days(SEALED)
    sealed.write_day(FAILED)
    assert sealed.fail_seal(FAILED).status == "pending"
    sealed.write_day(WAITING)
    yield sealed
    sealed.unlock_all()


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def test_seal_now_reaches_the_seal_task_once_for_the_oldest_due_day(
    client: TestClient,
    login_as: Login,
    agent: FakeAgent,
    vault: SealedVault,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "admin")
    response = client.post(f"{V}/seals/{FAILED.isoformat()}/seal")
    assert response.status_code == 202, response.text
    accepted = response.json()
    assert accepted["state"] == "queued"
    again = client.post(f"{V}/seals/{FAILED.isoformat()}/seal")
    assert error(again) == (409, "seal_request_pending")

    agent.process()
    (task,) = agent.tasks.taken if agent.tasks else []
    assert (task.kind, task.params.day, task.origin) == ("seal", FAILED, accepted["request_id"])
    done = client.get(accepted["status_url"]).json()
    assert done["state"] == "succeeded", done
    assert done["kind"] == "action.seal"
    assert done["result"]["day"] == FAILED.isoformat()
    assert done["result"]["status"] == "sealed"
    with app_session_factory() as session:
        row = session.get_one(AdminRequest, accepted["request_id"])
        assert (row.state, row.terminal) == ("succeeded", True)
        assert row.params["day"] == FAILED.isoformat()
        entry = session.scalars(select(AuditLog).where(AuditLog.action == "admin.request")).one()
        assert entry.details["kind"] == "action.seal"
        assert entry.details["day"] == FAILED.isoformat()


@pytest.mark.parametrize(
    ("day", "code"),
    [
        (SEALED[1], "seal_day_sealed"),
        (WAITING, "seal_not_due"),
        (TODAY, "seal_not_due"),
    ],
)
def test_seal_now_refuses_a_day_that_is_not_next(
    client: TestClient, login_as: Login, agent: FakeAgent, vault: SealedVault, day: date, code: str
) -> None:
    login_as(client, "admin")
    response = client.post(f"{V}/seals/{day.isoformat()}/seal")
    assert error(response) == (409, code)
    assert response.json()["error"]["message_en"]


def test_seal_now_refuses_a_day_without_data(
    client: TestClient, login_as: Login, agent: FakeAgent, api_config: Config
) -> None:
    sealed = make_vault(api_config.data_dir, tsa_now=at_site(SEALED[0] - timedelta(days=30)))
    sealed.write_day(WAITING)
    login_as(client, "admin")
    assert error(client.post(f"{V}/seals/{FAILED.isoformat()}/seal")) == (
        409,
        "seal_not_applicable",
    )
    sealed.unlock_all()


def test_seal_now_needs_the_administration_service(
    client: TestClient, login_as: Login, vault: SealedVault, installed: Installed
) -> None:
    login_as(client, "admin")  # no agent: no heartbeat
    response = client.post(f"{V}/seals/{FAILED.isoformat()}/seal")
    assert error(response) == (503, "admin_agent_unavailable")
    assert "ArciAdmin" in response.json()["error"]["message_en"]


def test_credit_query_once_a_minute_with_the_task_result(
    client: TestClient, login_as: Login, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    response = client.post(f"{V}/credits/query")
    assert response.status_code == 202, response.text
    accepted = response.json()
    limited = client.post(f"{V}/credits/query")
    assert error(limited) == (429, "admin_rate_limited")
    assert 1 <= limited.json()["error"]["details"]["retry_after"] <= 60
    assert limited.headers["retry-after"] == str(limited.json()["error"]["details"]["retry_after"])
    agent.process()
    done = client.get(accepted["status_url"]).json()
    assert done["state"] == "succeeded"
    assert done["result"] == {"credits": 1000, "threshold": 500, "below_threshold": False}


def test_a_failed_task_and_a_task_that_never_answers(
    client: TestClient, login_as: Login, installed: Installed, agent: FakeAgent
) -> None:
    def failed(request: TaskRequest, now: Any) -> TaskResult:
        return TaskResult(
            id=request.id,
            kind="credit",
            ok=False,
            code="credit_query_failed",
            started_at=now,
            finished_at=now,
        )

    assert agent.tasks is not None
    agent.tasks.outcomes["credit"] = failed
    login_as(client, "admin")
    accepted = client.post(f"{V}/credits/query").json()
    agent.process()
    done = client.get(accepted["status_url"]).json()
    assert done["state"] == "failed"
    assert done["error"]["code"] == "credit_query_failed"
    assert done["error"]["message_tr"].startswith("Kamu SM kontör sorgusu başarısız")

    agent.tasks.outcomes["credit"] = lambda request, now: None  # the task does not run
    backdate(client, timedelta(minutes=2))
    stuck = client.post(f"{V}/credits/query")
    assert stuck.status_code == 202, stuck.text
    agent.process()
    agent.advance(timedelta(minutes=16))
    agent.process()
    timed_out = client.get(stuck.json()["status_url"]).json()
    assert (timed_out["state"], timed_out["error"]["code"]) == ("timed_out", "task_timeout")


def test_seal_managers_follow_their_own_actions_only(
    app: FastAPI, client: TestClient, login_as: Login, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    accepted = client.post(f"{V}/credits/query").json()
    with TestClient(app) as other:
        login_as(other, "admin")
        assert other.get(accepted["status_url"]).status_code == 200
    with TestClient(app) as auditor:
        login_as(auditor, "auditor")
        assert error(auditor.get(accepted["status_url"])) == (403, "forbidden")
        assert error(auditor.post(f"{V}/credits/query")) == (403, "forbidden")
        assert error(auditor.post(f"{V}/seals/{FAILED.isoformat()}/seal")) == (403, "forbidden")
    missing = client.get(f"{V}/admin/requests/{'0' * 32}")
    assert error(missing) == (404, "admin_request_not_found")


def test_the_request_list_pages_newest_first(
    client: TestClient, login_as: Login, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    ids = []
    for _ in range(3):
        ids.append(client.post(f"{V}/credits/query").json()["request_id"])
        backdate(client, timedelta(minutes=2))
    first = client.get(f"{V}/admin/requests", params={"limit": 2}).json()
    assert [item["id"] for item in first["items"]] == [ids[2], ids[1]]
    second = client.get(
        f"{V}/admin/requests", params={"limit": 2, "cursor": first["next_cursor"]}
    ).json()
    assert [item["id"] for item in second["items"]] == [ids[0]]
    assert second["next_cursor"] is None
    only = client.get(f"{V}/admin/requests", params={"kind": "action.seal"}).json()
    assert only["items"] == []
    assert error(client.get(f"{V}/admin/requests", params={"kind": "nope"})) == (
        422,
        "validation_error",
    )


def backdate(client: TestClient, by: timedelta) -> None:
    """Every request made ``by`` earlier (the rate limits are judged on the API's clock)."""
    state = client.app.state.arci  # type: ignore[attr-defined]
    with state.session_factory()() as session:
        for row in session.scalars(select(AdminRequest)):
            row.created_at -= by
        session.commit()


def test_credit_result_model_is_the_contract_one() -> None:
    assert CreditResult(credits=1, threshold=0, below_threshold=False).model_dump() == {
        "credits": 1,
        "threshold": 0,
        "below_threshold": False,
    }


def test_the_replica_test_runs_in_the_requests_task_once_a_minute(
    client: TestClient, login_as: Login, agent: FakeAgent
) -> None:
    login_as(client, "admin")
    response = client.post(f"{V}/admin/actions/replica-test", json={})
    assert response.status_code == 202, response.text
    assert error(client.post(f"{V}/admin/actions/replica-test", json={})) == (
        429,
        "admin_rate_limited",
    )
    agent.process()
    done = client.get(response.json()["status_url"]).json()
    assert (done["kind"], done["state"]) == ("action.replica_test", "succeeded")
    assert done["result"]["ok"] is True


def mail_server(installed: Installed, port: int) -> None:
    """``[smtp]`` of the installation: the test's SMTP server (a hand edit the API reloads)."""
    text = installed.config_path.read_text(encoding="utf-8").replace(
        'port = 587\nsecurity = "starttls"',
        f'host = "127.0.0.1"\nport = {port}\nsecurity = "none"\n'
        'from_address = "arcilogger@ornek.example"',
    )
    installed.config_path.write_text(text, encoding="utf-8")


def test_a_test_email_is_sent_by_the_worker_to_an_allowed_address(
    client: TestClient,
    login_as: Login,
    installed: Installed,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "admin", email="tolga@ornek.example")
    url = f"{V}/admin/actions/test-email"
    stranger = client.post(url, json={"to": "yabanci@baska.example"})
    assert error(stranger) == (422, "test_email_recipient_not_allowed")
    assert "yabanci@baska.example" not in stranger.text
    unset = client.post(url, json={"to": "tolga@ornek.example"})
    assert error(unset) == (409, "smtp_not_configured")
    with app_session_factory() as session:
        set_setting(
            session, "alerts.email_recipients", ["bilgi-islem@ornek.example"], updated_by="test"
        )
        session.commit()

    with SmtpTestServer() as smtp:
        mail_server(installed, smtp.port)
        response = client.post(url, json={"to": "bilgi-islem@ornek.example"})
        assert response.status_code == 202, response.text
        accepted = response.json()
        assert client.get(accepted["status_url"]).json()["state"] == "queued"
        process_requests(app_session_factory, installed.config(), now=utc_now())
        done = client.get(accepted["status_url"]).json()
        assert (done["kind"], done["state"]) == ("worker.test_email", "succeeded"), done
        assert done["result"]["to"] == "b***@ornek.example"
        (mail,) = smtp.received
        assert mail.recipients == ["bilgi-islem@ornek.example"]
    with app_session_factory() as session:
        (entry,) = session.scalars(select(AuditLog).where(AuditLog.action == "admin.request"))
        assert entry.details["to"] == "b***@ornek.example"
        assert entry.details["kind"] == "worker.test_email"

    backdate(client, timedelta(minutes=1))
    for _ in range(4):
        assert client.post(url, json={"to": "tolga@ornek.example"}).status_code == 202
    limited = client.post(url, json={"to": "tolga@ornek.example"})
    assert error(limited) == (429, "admin_rate_limited")
    assert 3500 <= limited.json()["error"]["details"]["retry_after"] <= 3600


def test_a_test_email_the_worker_is_sending_is_not_cancelled(
    client: TestClient,
    login_as: Login,
    installed: Installed,
    app_session_factory: sessionmaker[Session],
) -> None:
    """The job holds the row (``FOR UPDATE SKIP LOCKED``) while it sends: a cancel then is 409
    and never overwrites what the worker sets; a row nobody holds is cancelled."""
    login_as(client, "admin", email="tolga@ornek.example")
    mail_server(installed, 2525)
    accepted = client.post(f"{V}/admin/actions/test-email", json={"to": "tolga@ornek.example"})
    assert accepted.status_code == 202, accepted.text
    request_id = accepted.json()["request_id"]
    with app_session_factory() as worker:
        held = worker.scalar(
            select(AdminRequest).where(AdminRequest.id == request_id).with_for_update()
        )
        assert held is not None
        assert error(client.delete(f"{V}/admin/requests/{request_id}")) == (
            409,
            "request_not_cancellable",
        )
        held.state, held.terminal = "succeeded", True
        worker.commit()
    assert client.get(accepted.json()["status_url"]).json()["state"] == "succeeded"
    second = client.post(f"{V}/admin/actions/test-email", json={"to": "tolga@ornek.example"})
    cancelled = client.delete(f"{V}/admin/requests/{second.json()['request_id']}")
    assert cancelled.status_code == 202, cancelled.text
    assert cancelled.json()["state"] == "cancelled"


def test_a_lease_rebuild_is_worked_day_by_day_within_its_range(
    client: TestClient,
    login_as: Login,
    api_config: Config,
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "admin")
    url = f"{V}/admin/actions/lease-rebuild"
    span = {"from": (TODAY - timedelta(days=2)).isoformat(), "to": TODAY.isoformat()}
    assert error(client.post(url, json=span)) == (422, "lease_rebuild_range")  # no log yet
    with app_session_factory() as session:
        evidence = Evidence(session, Layout(api_config.data_dir))
        evidence.dhcp(
            at_site(TODAY - timedelta(days=2)), "assigned", "172.20.60.21", "02:00:00:00:00:21"
        )
        session.commit()
    for refused in (
        {"from": (TODAY - timedelta(days=3)).isoformat(), "to": TODAY.isoformat()},
        {"from": TODAY.isoformat(), "to": (TODAY + timedelta(days=1)).isoformat()},
        {"from": TODAY.isoformat(), "to": (TODAY - timedelta(days=1)).isoformat()},
    ):
        assert error(client.post(url, json=refused)) == (422, "lease_rebuild_range"), refused
    response = client.post(url, json=span)
    assert response.status_code == 202, response.text
    for _ in range(5):
        process_requests(app_session_factory, api_config, now=utc_now())
    done = client.get(response.json()["status_url"]).json()
    assert (done["kind"], done["state"]) == ("worker.lease_rebuild", "succeeded"), done
    assert done["result"] == {"done_days": 3, "total_days": 3, "failed_days": []}
