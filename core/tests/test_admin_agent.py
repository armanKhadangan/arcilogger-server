"""The administration agent ArciAdmin (ADR-0014; contract 2.5.2, 2.5.3, 2.7, 2.9, 2.10)."""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.admin.contract import (
    AgentStatus,
    Event,
    TaskRequest,
    TaskResult,
    new_id,
    parse_document,
)
from arcilogger_core.admin.envelope import ADMIN_REQUEST, sign_payload
from arcilogger_core.admin.probe import ProbeResult
from arcilogger_core.admin.store import read_pending, read_txn, read_waits
from arcilogger_core.admin.testing import (
    FakeAgent,
    FakeProber,
    FakeTasks,
    Installed,
    make_installation,
)
from arcilogger_core.alerts import read_alerts
from arcilogger_core.canonical import canonical_json
from arcilogger_core.timeutil import SITE_TZ, utc_now

SITE = {"op": "set", "path": "site_name", "expect": "Örnek A.Ş.", "value": "Örnek Holding"}


@pytest.fixture
def installed(tmp_path: Path) -> Installed:
    return make_installation(tmp_path / "ProgramData")


@pytest.fixture
def agent(installed: Installed) -> FakeAgent:
    return FakeAgent(installed)


def site_request(installed: Installed, **options: Any) -> Any:
    return installed.request(section="general", ops=[SITE], **options)


def alerts(installed: Installed) -> list[dict[str, Any]]:
    return read_alerts(installed.layout.alerts)


# -- the happy path --------------------------------------------------------------------------------


def test_a_site_name_change_is_applied_journaled_and_reported(
    installed: Installed, agent: FakeAgent
) -> None:
    request = site_request(installed, reauth=False)  # the site name needs no re-authentication
    installed.submit(request)
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert status.state == "applied"
    assert status.version is not None
    assert status.version.number == 1
    steps = {step.step: step.state for step in status.steps}
    assert steps["validate"] == "done"
    assert steps["write_config"] == "done"
    assert steps["write_secrets"] == "skipped"
    assert installed.config().site_name == "Örnek Holding"
    lines = [line for line in installed.journal() if line.request == request.id]
    assert [line.state for line in lines] == ["applied"]
    assert lines[0].changes["site_name"].model_dump() == {
        "old": "Örnek A.Ş.",
        "new": "Örnek Holding",
    }
    assert lines[0].requested_by is not None
    assert lines[0].requested_by.username == "ayse"
    assert int(Event.CHANGE_APPLIED) in agent.events.ids()
    assert not installed.layout.request_file(request.id).exists()
    assert not installed.layout.work_dir(request.id).exists()
    assert read_txn(installed.layout) is None
    assert request.id in agent.agent.ledger


def test_the_heartbeat_reports_the_agent(installed: Installed, agent: FakeAgent) -> None:
    agent.start()
    installed.submit(site_request(installed))
    agent.agent.heartbeat()
    status = parse_document(AgentStatus, installed.layout.agent_status.read_bytes())
    assert status.queue == 1
    assert status.key_ids == (installed.api_key.key_id,)
    assert status.policy_sha256 is not None
    assert status.local_addresses == ("172.20.10.5",)
    assert status.seal_ready.ok  # the mock provider seals
    assert status.system_task.ok
    assert status.system_task.checked_at is None
    assert status.heartbeat_at.utcoffset() == timedelta(hours=3)  # site time
    agent.time = utc_now() + timedelta(minutes=6)
    late = agent.agent.heartbeat()
    assert not late.system_task.ok
    assert late.system_task.error == "system_sync_timeout"
    assert [int(e) for e in (Event.AGENT_STARTED, Event.SYSTEM_SYNC_REQUESTED)] == (
        agent.events.ids()[:2]
    )


# -- claim, verify, ledger -------------------------------------------------------------------------


def test_a_file_changed_after_the_claim_is_irrelevant(
    installed: Installed, agent: FakeAgent
) -> None:
    agent.start()
    request = site_request(installed)
    inbox = installed.submit(request)
    claimed = agent.agent._claim()
    assert claimed == [(request.id, installed.layout.work_dir(request.id) / "request.req")]
    assert not inbox.exists()
    tampered = installed.request(
        section="general",
        ops=[{**SITE, "value": "Başka A.Ş."}],
        request_id=request.id,
    )
    installed.submit(tampered)  # the API's folder: a new file under the same name
    agent.process()
    assert installed.config().site_name == "Örnek Holding"
    assert installed.status(request.id).state == "applied"  # type: ignore[union-attr]
    rejected = [line for line in installed.journal() if line.state == "rejected"]
    assert [line.code for line in rejected] == ["request_replayed"]


def test_a_tampered_request_is_rejected_before_its_payload_is_read(
    installed: Installed, agent: FakeAgent
) -> None:
    request = site_request(installed)
    data = bytearray(json.dumps(json.loads(_signed(installed, request))).encode())
    envelope = json.loads(bytes(data))
    signature = envelope["signature"]
    envelope["signature"] = ("A" if signature[0] != "A" else "B") + signature[1:]
    installed.submit(request, data=canonical_json(envelope) + b"\n")
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert (status.state, status.kind, status.error.code) == (  # type: ignore[union-attr]
        "rejected",
        None,
        "signature_invalid",
    )
    line = installed.journal()[-1]
    assert (line.state, line.kind, line.requested_by, line.code) == (
        "rejected",
        None,
        None,
        "signature_invalid",
    )
    assert alerts(installed)[-1]["rule"] == "R20-admin"
    assert alerts(installed)[-1]["severity"] == "high"
    assert int(Event.REQUEST_REJECTED) in agent.events.ids()
    assert installed.config().site_name == "Örnek A.Ş."
    assert not installed.layout.work_dir(request.id).exists()


def _signed(installed: Installed, request: Any) -> bytes:
    from arcilogger_core.admin.envelope import sign_admin_request

    return sign_admin_request(request, installed.api_key)


def test_a_request_for_another_installation_names_its_requester(
    installed: Installed, agent: FakeAgent
) -> None:
    request = site_request(installed).model_copy(update={"instance": new_id()})
    installed.submit(request)
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert status.error is not None
    assert (status.state, status.kind, status.error.code) == (
        "rejected",
        "config.apply",
        "installation_mismatch",
    )
    assert installed.journal()[-1].requested_by is not None
    assert alerts(installed)[-1]["details"]["code"] == "installation_mismatch"


def test_an_expired_request_changes_nothing(installed: Installed, agent: FakeAgent) -> None:
    request = site_request(installed, now=utc_now() - timedelta(minutes=11))
    installed.submit(request)
    agent.process()
    assert installed.status(request.id).error.code == "request_expired"  # type: ignore[union-attr]
    assert installed.config().site_name == "Örnek A.Ş."
    assert not [a for a in alerts(installed) if a["rule"] == "R20-admin"]


def test_a_payload_that_is_not_canonical_is_schema_invalid(
    installed: Installed, agent: FakeAgent
) -> None:
    request = site_request(installed)
    payload = json.dumps(request.model_dump(mode="json"), indent=1).encode()
    installed.submit(request, data=sign_payload(payload, installed.api_key, ADMIN_REQUEST))
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert status.kind is None
    assert status.error is not None
    assert status.error.code == "schema_invalid"


def test_a_replay_is_rejected_and_keeps_the_first_status(
    installed: Installed, agent: FakeAgent
) -> None:
    request = site_request(installed)
    data = _signed(installed, request)
    installed.submit(request, data=data)
    agent.process()
    first = installed.status(request.id)
    installed.config_path.write_bytes(
        installed.config_path.read_bytes().replace("Örnek Holding".encode(), "Örnek A.Ş.".encode())
    )
    installed.submit(request, data=data)
    agent.process()
    assert installed.status(request.id) == first
    assert installed.config().site_name == "Örnek A.Ş."  # not applied again
    assert installed.journal()[-1].code == "request_replayed"


def test_the_ledger_is_written_before_any_effect(installed: Installed) -> None:
    def crash(step: str) -> None:
        if step == "versions":
            raise OSError("disk full")

    agent = FakeAgent(installed, checkpoint=crash)
    request = site_request(installed)
    installed.submit(request)
    agent.process()
    assert request.id in agent.agent.ledger
    status = installed.status(request.id)
    assert status is not None
    assert status.state == "failed"
    assert status.error is not None
    assert status.error.code == "write_failed"


def test_browser_administration_switched_off_refuses_everything(
    installed: Installed, agent: FakeAgent
) -> None:
    installed.write_policy(enabled=False)
    request = site_request(installed)
    installed.submit(request)
    agent.process()
    assert installed.status(request.id).error.code == "admin_disabled"  # type: ignore[union-attr]
    assert installed.config().site_name == "Örnek A.Ş."


def test_a_missing_policy_means_switched_off(installed: Installed, agent: FakeAgent) -> None:
    installed.layout.policy.unlink()
    request = site_request(installed)
    installed.submit(request)
    agent.process()
    assert installed.status(request.id).error.code == "admin_disabled"  # type: ignore[union-attr]


def test_an_upload_that_differs_from_its_digest_is_rejected(
    installed: Installed, agent: FakeAgent
) -> None:
    upload = installed.upload("kamusm_root", b"-----BEGIN CERTIFICATE-----\nabc\n")
    installed.layout.upload_data(upload["upload"]).write_bytes(b"something else")
    request = installed.request(section="tsa", ops=[{"op": "root_add", **upload, "legal": False}])
    installed.submit(request)
    agent.process()
    assert installed.status(request.id).error.code == "payload_mismatch"  # type: ignore[union-attr]


# -- validation ------------------------------------------------------------------------------------


def test_a_clock_change_without_a_recent_reauthentication_is_rejected(
    installed: Installed, agent: FakeAgent
) -> None:
    op = {"op": "set", "path": "clock.ntp_server", "expect": "172.20.10.1", "value": "172.20.10.2"}
    request = installed.request(section="general", ops=[op], reauth=False)
    installed.submit(request)
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert status.error is not None
    assert (status.state, status.error.code) == ("rejected", "reauth_missing")
    assert installed.config().clock.ntp_server == "172.20.10.1"


def test_a_change_made_elsewhere_meanwhile_is_a_conflict(
    installed: Installed, agent: FakeAgent
) -> None:
    request = installed.request(section="general", ops=[{**SITE, "expect": "Eski A.Ş."}])
    installed.submit(request)
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert status.error is not None
    assert (status.state, status.error.code, status.error.field) == (
        "conflict",
        "conflict",
        "site_name",
    )
    assert installed.journal()[-1].state == "failed"


@pytest.mark.parametrize(
    ("op", "code", "field"),
    [
        ({**SITE, "value": "  "}, "site_name_required", "site_name"),
        ({**SITE, "value": "satır\nsonu"}, "config_invalid", "site_name"),
        ({"op": "set", "path": "data_dir", "expect": None, "value": "E:/x"}, "config_key_locked",
         "data_dir"),
        ({"op": "set", "path": "clock.ntp_server", "expect": "172.20.10.1", "value": "10.0.3"},
         "host_invalid", "clock.ntp_server"),
    ],
)  # fmt: skip
def test_the_agent_checks_the_rules_again(
    installed: Installed, agent: FakeAgent, op: dict[str, Any], code: str, field: str
) -> None:
    request = installed.request(section="general", ops=[op])
    installed.submit(request)
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert status.error is not None
    assert (status.state, status.error.code, status.error.field) == ("failed", code, field)
    assert installed.journal()[-1].code == code
    assert installed.config().site_name == "Örnek A.Ş."


def test_a_configuration_that_moves_the_secret_store_is_not_worked_on(
    installed: Installed, agent: FakeAgent, tmp_path: Path
) -> None:
    text = installed.config_path.read_text(encoding="utf-8")
    elsewhere = (tmp_path / "elsewhere").as_posix()
    installed.config_path.write_text(
        text.replace("data_dir =", f'secrets_dir = "{elsewhere}"\ndata_dir ='), encoding="utf-8"
    )
    request = site_request(installed)
    installed.submit(request)
    agent.process()
    assert installed.status(request.id).error.code == "validation_failed"  # type: ignore[union-attr]


def test_the_data_root_must_be_the_one_the_installer_recorded(
    installed: Installed, agent: FakeAgent, tmp_path: Path
) -> None:
    record = installed.root / "data-root.txt"
    record.write_text(str(tmp_path / "other-data"), encoding="utf-8")
    refused = site_request(installed)
    installed.submit(refused)
    agent.process()
    assert installed.status(refused.id).error.code == "validation_failed"  # type: ignore[union-attr]
    record.write_bytes(b"\xef\xbb\xbf" + str(installed.data_dir).encode() + b"\r\n")
    accepted = site_request(installed)
    installed.submit(accepted)
    agent.process()
    assert installed.status(accepted.id).state == "applied"  # type: ignore[union-attr]


def test_a_failed_probe_changes_nothing(installed: Installed) -> None:
    prober = FakeProber({"sntp": ProbeResult(False, "ntp_unreachable", "timeout")})
    agent = FakeAgent(installed, prober=prober)
    op = {"op": "set", "path": "clock.ntp_server", "expect": "172.20.10.1", "value": "172.20.10.2"}
    request = installed.request(section="general", ops=[op])
    installed.submit(request)
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert status.error is not None
    assert (status.state, status.error.code) == ("probe_failed", "ntp_unreachable")
    assert prober.calls[0].params == {"server": "172.20.10.2", "port": 123}
    assert installed.config().clock.ntp_server == "172.20.10.1"


def test_a_clock_change_wakes_applysystem(installed: Installed, agent: FakeAgent) -> None:
    op = {"op": "set", "path": "clock.ntp_server", "expect": "172.20.10.1", "value": "172.20.10.2"}
    installed.submit(installed.request(section="general", ops=[op]))
    agent.process()
    assert installed.config().clock.ntp_server == "172.20.10.2"
    assert agent.events.ids().count(int(Event.SYSTEM_SYNC_REQUESTED)) == 2  # start, change


# -- holds -----------------------------------------------------------------------------------------

RETENTION = [{"op": "set", "path": "retention.enabled", "expect": False, "value": True}]


def test_a_guarded_change_is_held_and_then_applied(installed: Installed) -> None:
    installed.write_policy(hold_hours=1)
    agent = FakeAgent(installed, now=datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ))
    request = installed.request(section="retention", ops=RETENTION, now=agent.now())
    installed.submit(request)
    agent.process()
    status = installed.status(request.id)
    assert status is not None
    assert status.state == "pending"
    assert status.effective_at == agent.now() + timedelta(hours=1)
    assert [w.code for w in status.warnings] == ["change_held"]
    assert [p.id for p in read_pending(installed.layout)] == [request.id]
    assert int(Event.CHANGE_HELD) in agent.events.ids()
    assert alerts(installed)[-1]["rule"] == "R20-config-change"
    assert not installed.config().retention.enabled
    agent.advance(timedelta(minutes=61))
    agent.process()
    assert installed.status(request.id).state == "applied"  # type: ignore[union-attr]
    assert installed.config().retention.enabled
    assert read_pending(installed.layout) == []
    assert [line.state for line in installed.journal(date(2026, 10, 25))] == ["pending", "applied"]


def test_a_held_change_whose_value_changed_meanwhile_is_a_conflict(installed: Installed) -> None:
    installed.write_policy(hold_hours=1)
    agent = FakeAgent(installed, now=datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ))
    request = installed.request(section="retention", ops=RETENTION, now=agent.now())
    installed.submit(request)
    agent.process()
    installed.config_path.write_text(
        installed.config_path.read_text(encoding="utf-8").replace(
            "[retention]\nenabled = false", "[retention]\nenabled = true"
        ),
        encoding="utf-8",
    )
    agent.advance(timedelta(hours=2))
    agent.process()
    assert installed.status(request.id).state == "conflict"  # type: ignore[union-attr]


def test_a_held_change_can_be_cancelled(installed: Installed) -> None:
    installed.write_policy(hold_hours=1)
    agent = FakeAgent(installed, now=datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ))
    held = installed.request(section="retention", ops=RETENTION, now=agent.now())
    installed.submit(held)
    agent.process()
    cancel = installed.request("config.cancel", options={"target": held.id}, now=agent.now())
    installed.submit(cancel)
    agent.process()
    assert installed.status(held.id).state == "cancelled"  # type: ignore[union-attr]
    assert installed.status(cancel.id).state == "succeeded"  # type: ignore[union-attr]
    assert int(Event.HOLD_CANCELLED) in agent.events.ids()
    agent.advance(timedelta(hours=2))
    agent.process()
    assert not installed.config().retention.enabled
    again = installed.request("config.cancel", options={"target": held.id}, now=agent.now())
    installed.submit(again)
    agent.process()
    assert installed.status(again.id).error.code == "request_not_cancellable"  # type: ignore[union-attr]


# -- actions ---------------------------------------------------------------------------------------


def test_seal_now_and_credit_query_run_in_their_tasks(
    installed: Installed, agent: FakeAgent
) -> None:
    seal = installed.request("action.seal", options={"day": date(2026, 10, 24)})
    credit = installed.request("action.credit")
    installed.submit(seal)
    installed.submit(credit)
    agent.process()
    seal_status, credit_status = installed.status(seal.id), installed.status(credit.id)
    assert seal_status is not None
    assert credit_status is not None
    assert seal_status.state == "succeeded"
    assert seal_status.result is not None
    assert seal_status.result.model_dump()["day"] == date(2026, 10, 24)
    assert credit_status.state == "succeeded"
    assert credit_status.result is not None
    assert credit_status.result.model_dump()["credits"] == 1000
    tasks = agent.tasks
    assert tasks is not None
    assert sorted(t.kind for t in tasks.taken) == ["credit", "seal"]
    assert {t.origin for t in tasks.taken} == {seal.id, credit.id}
    ids = agent.events.ids()
    assert int(Event.SEAL_REQUESTED) in ids
    assert int(Event.TASK_REQUESTED) in ids
    actions = [line for line in installed.journal() if line.state == "action"]
    assert {line.request for line in actions} == {seal.id, credit.id}
    assert all(line.requested_by is not None for line in actions)


def test_a_failed_task_and_a_silent_task(installed: Installed) -> None:
    def failed(request: TaskRequest, now: datetime) -> TaskResult:
        return TaskResult(
            id=request.id, kind=request.kind, ok=False, code="seal_failed", started_at=now,
            finished_at=now,
        )  # fmt: skip

    agent = FakeAgent(installed)
    assert agent.tasks is not None
    agent.tasks.outcomes = {"seal": failed, "credit": lambda request, now: None}
    seal = installed.request("action.seal", options={"day": date(2026, 10, 24)})
    credit = installed.request("action.credit")
    installed.submit(seal)
    installed.submit(credit)
    agent.process()
    assert installed.status(seal.id).error.code == "seal_failed"  # type: ignore[union-attr]
    assert installed.status(credit.id).state == "dispatched"  # type: ignore[union-attr]
    agent.advance(timedelta(minutes=16))
    agent.process()
    status = installed.status(credit.id)
    assert status is not None
    assert status.error is not None
    assert (status.state, status.error.code) == ("timed_out", "task_timeout")
    assert read_waits(installed.layout).tasks == ()


def test_waits_never_block_other_requests(installed: Installed) -> None:
    agent = FakeAgent(installed, tasks=False)
    seal = installed.request("action.seal", options={"day": date(2026, 10, 24)})
    installed.submit(seal)
    agent.process()
    assert installed.status(seal.id).state == "dispatched"  # type: ignore[union-attr]
    change = site_request(installed)
    installed.submit(change)
    agent.process()
    assert installed.status(change.id).state == "applied"  # type: ignore[union-attr]
    assert installed.status(seal.id).state == "dispatched"  # type: ignore[union-attr]
    FakeTasks(installed).run()
    agent.process()
    assert installed.status(seal.id).state == "succeeded"  # type: ignore[union-attr]


def test_a_forged_result_for_another_request_is_ignored(installed: Installed) -> None:
    agent = FakeAgent(installed, tasks=False)
    seal = installed.request("action.seal", options={"day": date(2026, 10, 24)})
    installed.submit(seal)
    agent.process()
    wait = read_waits(installed.layout).tasks[0]
    (installed.data.request_results).mkdir(parents=True, exist_ok=True)
    (installed.data.request_results / f"{wait.task}.json").write_text("{}", encoding="utf-8")
    agent.process()
    assert installed.status(seal.id).state == "dispatched"  # type: ignore[union-attr]


# -- recovery and cleanup --------------------------------------------------------------------------


class _Crash(BaseException):
    pass


def test_an_interrupted_apply_is_finished_at_the_next_start(installed: Installed) -> None:
    def crash(step: str) -> None:
        if step == "verify":
            raise _Crash

    first = FakeAgent(installed, checkpoint=crash)
    request = site_request(installed)
    installed.submit(request)
    with pytest.raises(_Crash):
        first.process()
    assert installed.status(request.id).state == "applying"  # type: ignore[union-attr]
    second = FakeAgent(installed)
    second.start()
    status = installed.status(request.id)
    assert status is not None
    assert status.state == "applied"
    assert [line.state for line in installed.journal()] == ["recovered"]
    assert not installed.layout.work_dir(request.id).exists()


def test_an_interrupted_probe_fails_at_the_next_start(installed: Installed) -> None:
    class Stop(FakeProber):
        def run(self, *args: Any, **kwargs: Any) -> ProbeResult:
            raise _Crash

    op = {"op": "set", "path": "clock.ntp_server", "expect": "172.20.10.1", "value": "172.20.10.2"}
    request = installed.request(section="general", ops=[op])
    installed.submit(request)
    with pytest.raises(_Crash):
        FakeAgent(installed, prober=Stop()).process()
    FakeAgent(installed).start()
    status = installed.status(request.id)
    assert status is not None
    assert status.error is not None
    assert (status.state, status.error.code) == ("failed", "interrupted")


def test_cleanup_removes_old_temporary_files_and_uploads(
    installed: Installed, agent: FakeAgent
) -> None:
    agent.start()
    old = (utc_now() - timedelta(hours=1)).timestamp()
    temp = installed.layout.inbox / f"{new_id()}.req.tmp"
    temp.write_bytes(b"partial")
    fresh = installed.layout.inbox / f"{new_id()}.req.tmp"
    fresh.write_bytes(b"partial")
    upload = installed.upload("smtp_ca", b"-----BEGIN CERTIFICATE-----\n")
    stale_status = installed.layout.status / f"{new_id()}.json"
    stale_status.write_text("{}", encoding="utf-8")
    for path in (temp, installed.layout.upload_data(upload["upload"]), stale_status):
        os.utime(path, (old, old))
    os.utime(stale_status, (old - 31 * 86400, old - 31 * 86400))
    agent.agent._last_cleanup = None
    agent.process()
    assert not temp.exists()
    assert fresh.exists()
    assert not installed.layout.upload_data(upload["upload"]).exists()
    assert installed.layout.upload_meta(upload["upload"]).exists()  # younger than 30 minutes
    assert not stale_status.exists()
    assert installed.layout.agent_status.exists()


def test_a_request_file_not_named_by_an_id_is_removed(
    installed: Installed, agent: FakeAgent
) -> None:
    stray = installed.layout.inbox / "not-an-id.req"
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_bytes(b"{}")
    agent.process()
    assert not stray.exists()
