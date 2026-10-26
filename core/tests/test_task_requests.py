"""The evidence tasks in request mode (ADR-0014; contract 2.4.5, 2.4.6, 2.9): "Şimdi mühürle"
in the Seal task, the credit query, the TSA test and the replica test in the Requests task, and
the check of the installation's own folders every task makes first (Day 0 note D0-2)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from arcilogger_core.admin.contract import (
    TaskKind,
    TaskParams,
    TaskRequest,
    TaskResult,
    key_id_for,
    new_id,
    parse_document,
    task_queue,
)
from arcilogger_core.admin.envelope import TASK_REQUEST, sign_payload, sign_task_request
from arcilogger_core.admin.keys import SigningKey, init_keys, load_signing_key
from arcilogger_core.alerts import read_alerts
from arcilogger_core.canonical import canonical_json
from arcilogger_core.cli import main
from arcilogger_core.config import (
    DATA_ROOT_RECORD,
    ConfigLocationError,
    check_fixed_location,
    load_config,
    recorded_data_root,
)
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.taskrequests import clean, take
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, site_day, utc_now
from arcilogger_core.tsa.mock import MockTsa

CONFIG = """data_dir = "{data}"
site_name = "Örnek A.Ş."

[[sources]]
name = "fortigate"
type = "fortigate"
address = "172.31.255.1"

[seal]
quiet_seconds = 0

[tsa]
provider = "mock"
"""
FORTIGATE = b'<189>date=2026-10-24 time=09:44:57 devname="FIREWALL-01" logid="0000000013" x=1'


@dataclass
class Site:
    """An installation: ``ProgramData\\ARCILOGGER`` with the configuration and the channel's
    keys, a data root with three closed days in the spool, and the agent's signing key."""

    programdata: Path
    data: Path
    agent: SigningKey
    days: list[date]

    @property
    def config(self) -> Path:
        return self.programdata / "arcilogger.toml"

    @property
    def layout(self) -> Layout:
        return Layout(self.data)

    def run(self, *args: str) -> int:
        return main(["--config", str(self.config), *args])

    def request(
        self,
        kind: TaskKind,
        *,
        day: date | None = None,
        issued: datetime | None = None,
        validity: timedelta | None = None,
        key: SigningKey | None = None,
    ) -> TaskRequest:
        """Write a signed task request into its queue, as the agent does."""
        request = task_request(kind, day=day, issued=issued, validity=validity)
        self.write(request, sign_task_request(request, key or self.agent))
        return request

    def write(self, request: TaskRequest, data: bytes, *, name: str | None = None) -> Path:
        folder = self.layout.request_dir(task_queue(request.kind))
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name or f"{request.id}.json")
        path.write_bytes(data)
        return path

    def result(self, request: TaskRequest) -> TaskResult:
        path = self.layout.request_results / f"{request.id}.json"
        return parse_document(TaskResult, path.read_bytes())

    def state(self, day: date) -> dict[str, Any]:
        data: dict[str, Any] = json.loads(self.layout.seal_state(day).read_bytes())
        return data

    def failed_before(self, day: date, *, retry_in: timedelta = timedelta(hours=3)) -> None:
        """The day's last attempt failed and its next one is not due yet."""
        state = {
            "day": day.isoformat(),
            "status": "pending",
            "attempts": 4,
            "last_attempt_at": isoformat_site(utc_now() - timedelta(minutes=5), "seconds"),
            "last_error": "Kamu SM did not answer",
            "last_error_code": "tsa_unavailable",
            "next_attempt_at": isoformat_site(utc_now() + retry_in, "seconds"),
        }
        path = self.layout.seal_state(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state), encoding="utf-8")


def task_request(
    kind: TaskKind,
    *,
    day: date | None = None,
    issued: datetime | None = None,
    validity: timedelta | None = None,
) -> TaskRequest:
    """A task request as the agent makes it (neither signed nor written)."""
    issued = issued or utc_now()
    if validity is None:
        validity = timedelta(hours=6) if kind == "seal" else timedelta(minutes=30)
    return TaskRequest(
        id=new_id(),
        origin=new_id(),
        kind=kind,
        params=TaskParams(day=day if kind == "seal" else None),
        issued_at=issued,
        expires_at=issued + validity,
    )


def foreign_agent_key() -> SigningKey:
    """An agent key the installation does not list (another installation's, or forged)."""
    private_key = Ed25519PrivateKey.from_private_bytes(bytes([7]) * 32)
    raw = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return SigningKey("agent", key_id_for("agent", raw), private_key)


@pytest.fixture
def site(tmp_path: Path) -> Site:
    programdata = tmp_path / "ProgramData" / "ARCILOGGER"
    data = tmp_path / "ARCILOGGER"
    programdata.mkdir(parents=True)
    (programdata / "arcilogger.toml").write_text(
        CONFIG.format(data=data.as_posix()), encoding="utf-8"
    )
    keys_dir = programdata / "admin" / "keys"
    init_keys(keys_dir)
    today = site_day(utc_now())
    days = [today - timedelta(days=n) for n in (3, 2, 1)]
    layout = Layout(data)
    for day in days:
        path = layout.spool_file("fortigate", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            for hour in range(0, 24, 3):
                received = datetime.combine(day, time(hour, 0), tzinfo=SITE_TZ)
                handle.write(format_line(received, "172.31.255.1", FORTIGATE))
    return Site(programdata, data, load_signing_key(keys_dir, "agent"), days)


def mock_tsa(site: Site) -> MockTsa:
    return MockTsa(site.layout.state / "mock-tsa")


# -- Şimdi mühürle (Seal task) ----------------------------------------------------------------


def test_a_seal_request_seals_a_day_that_waits_for_its_retry(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    oldest = site.days[0]
    site.failed_before(oldest)
    assert site.run("seal") == 1  # without a request the day waits for its retry time
    assert "next attempt" in capsys.readouterr().out
    assert site.state(oldest)["attempts"] == 4

    request = site.request("seal", day=oldest)
    assert site.run("seal") == 0
    assert capsys.readouterr().out.count("sealed") == 3
    state = site.state(oldest)
    assert state["status"] == "sealed"
    assert state["attempts"] == 5  # one attempt, recorded with the request it served
    assert state["requested"] == request.id
    assert "requested" not in site.state(site.days[1])  # the later days came in the normal way
    result = site.result(request)
    assert result.ok
    assert result.code is None
    assert result.kind == "seal"
    assert result.result is not None
    assert result.result.model_dump(mode="json")["status"] == "sealed"
    assert result.result.model_dump(mode="json")["serial"] == state["serial"]
    assert result.message_tr is not None
    assert result.message_tr.endswith("günü mühürlendi ve doğrulandı.")
    assert result.message_en == f"{oldest.isoformat()} was sealed and verified."
    # The request is gone, its id stays in the ledger; nothing in the result names a path.
    assert list(site.layout.request_dir("seal").iterdir()) == []
    assert (site.layout.request_seen / request.id).is_file()
    raw = (site.layout.request_results / f"{request.id}.json").read_text(encoding="utf-8")
    assert str(site.data) not in raw
    assert site.data.as_posix() not in raw


def test_the_request_is_honoured_once(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    """The retry time is ignored for the run that takes the request, not for the next one."""
    oldest = site.days[0]
    site.request("seal", day=oldest)
    tsa = mock_tsa(site)
    tsa.credits = 0  # every stamp is refused now
    assert site.run("seal") == 1
    state = site.state(oldest)
    assert state["status"] == "pending"
    assert state["attempts"] == 1
    assert "requested" in state
    assert site.run("seal") == 1  # the next run waits for the retry time again
    assert "next attempt" in capsys.readouterr().out.splitlines()[-1]
    assert site.state(oldest)["attempts"] == 1


def test_a_failed_requested_attempt_reports_the_failure(site: Site) -> None:
    oldest = site.days[0]
    request = site.request("seal", day=oldest)
    mock_tsa(site).credits = 0
    assert site.run("seal") == 1
    result = site.result(request)
    assert not result.ok
    assert result.code == "seal_failed"
    assert result.message_tr == "Mühürleme denemesi başarısız; hata gün ayrıntısında."
    assert result.result is not None
    facts = result.result.model_dump(mode="json")
    assert facts["status"] == "pending"
    assert "credit" in facts["message"]


def test_a_request_never_jumps_the_queue(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    """Oldest first: a request for a later day does not pass an older day that still waits."""
    site.failed_before(site.days[0])
    request = site.request("seal", day=site.days[1])
    assert site.run("seal") == 1
    assert capsys.readouterr().out.split()[1] == "waiting"
    assert not site.layout.seal_state(site.days[1]).exists()
    result = site.result(request)
    assert result.code == "seal_failed"
    assert result.result is not None
    assert result.result.model_dump(mode="json")["status"] == "waiting"


def test_a_request_for_a_sealed_day_says_so(site: Site) -> None:
    assert site.run("seal") == 0
    request = site.request("seal", day=site.days[0])
    assert site.run("seal") == 0
    result = site.result(request)
    assert result.ok
    assert result.result is not None
    assert result.result.model_dump(mode="json")["status"] == "sealed"


def test_a_skipped_day_stays_skipped(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    """The empty-run rule still decides: a day without any log is skipped, request or not."""
    site.config.write_text(
        site.config.read_text(encoding="utf-8").replace(
            "quiet_seconds = 0", "quiet_seconds = 0\nmax_consecutive_empty_days = 0"
        ),
        encoding="utf-8",
    )
    empty = site.days[1]
    site.layout.spool_file("fortigate", empty).unlink()
    request = site.request("seal", day=empty)
    assert site.run("seal") == 1
    assert [line.split()[1] for line in capsys.readouterr().out.splitlines()] == [
        "sealed",
        "skipped",
        "sealed",
    ]
    assert site.state(empty)["status"] == "skipped"
    assert "requested" not in site.state(empty)
    result = site.result(request)
    assert not result.ok
    assert result.result is not None
    assert result.result.model_dump(mode="json")["status"] == "skipped"


def test_a_seal_request_waits_for_a_run_of_all_days(site: Site) -> None:
    """``seal --day`` (a person on the console) leaves the requests to the Seal task."""
    request = site.request("seal", day=site.days[0])
    assert site.run("seal", "--day", site.days[0].isoformat()) == 0
    assert (site.layout.request_dir("seal") / f"{request.id}.json").is_file()
    assert not (site.layout.request_seen / request.id).exists()


def test_a_sealer_that_cannot_start_answers_the_request(site: Site) -> None:
    site.config.write_text(
        site.config.read_text(encoding="utf-8").replace('provider = "mock"', 'provider = "zamane"'),
        encoding="utf-8",
    )
    request = site.request("seal", day=site.days[0])
    assert site.run("seal") == 1
    result = site.result(request)
    assert result.code == "seal_failed"
    assert result.result is None
    (alert,) = read_alerts(site.layout.alerts)
    assert alert["details"]["cause"] == "zamane-jar"


# -- forged, damaged, expired and replayed requests ---------------------------------------------


def _waits_as_without_a_request(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    assert site.run("seal") == 1
    assert "next attempt" in capsys.readouterr().out
    assert site.state(site.days[0])["attempts"] == 4
    assert "requested" not in site.state(site.days[0])
    assert list(site.layout.request_dir("seal").iterdir()) == []  # deleted, never run
    assert not site.layout.request_results.exists()  # nothing of the file is trusted


def test_a_request_signed_with_another_key_is_ignored(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    site.failed_before(site.days[0])
    site.request("seal", day=site.days[0], key=foreign_agent_key())
    _waits_as_without_a_request(site, capsys)


def test_an_unsigned_or_damaged_file_is_ignored(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    site.failed_before(site.days[0])
    request = task_request("seal", day=site.days[0])
    site.write(request, request.model_dump_json().encode("utf-8"))  # the payload, unsigned
    changed = bytearray(sign_task_request(request, site.agent))
    changed[40] ^= 0x01
    site.write(request, bytes(changed), name=f"{new_id()}.json")
    site.write(request, b"\x00" * 100, name="not-an-id.json")
    _waits_as_without_a_request(site, capsys)


def test_an_expired_request_is_ignored(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    site.failed_before(site.days[0])
    site.request(
        "seal", day=site.days[0], issued=utc_now() - timedelta(hours=7), validity=timedelta(hours=6)
    )
    _waits_as_without_a_request(site, capsys)


def test_a_request_under_another_name_or_queue_is_ignored(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    site.failed_before(site.days[0])
    request = task_request("seal", day=site.days[0])
    site.write(request, sign_task_request(request, site.agent), name=f"{new_id()}.json")
    credit = site.request("credit")  # a valid request of the other queue, put into this one
    data = (site.layout.request_dir("tasks") / f"{credit.id}.json").read_bytes()
    (site.layout.request_dir("tasks") / f"{credit.id}.json").unlink()
    site.write(request, data, name=f"{credit.id}.json")
    _waits_as_without_a_request(site, capsys)


def test_a_replayed_request_is_ignored(site: Site, capsys: pytest.CaptureFixture[str]) -> None:
    oldest = site.days[0]
    request = site.request("seal", day=oldest)
    data = (site.layout.request_dir("seal") / f"{request.id}.json").read_bytes()
    mock_tsa(site).credits = 0
    assert site.run("seal") == 1  # taken: one requested attempt, which fails
    first = site.result(request)
    site.write(request, data)  # the same signed file again, within its 6 hours
    assert site.run("seal") == 1
    assert "next attempt" in capsys.readouterr().out.splitlines()[-1]
    assert site.state(oldest)["attempts"] == 1
    assert site.result(request) == first
    assert list(site.layout.request_dir("seal").iterdir()) == []


def test_without_the_channel_keys_requests_are_dropped_and_sealing_goes_on(site: Site) -> None:
    site.request("seal", day=site.days[0])
    (site.programdata / "admin" / "keys" / "keys.json").unlink()
    assert site.run("seal") == 0  # the normal run seals all three days
    assert list(site.layout.request_dir("seal").iterdir()) == []
    assert not site.layout.request_results.exists()
    log = (site.layout.logs / "core.log").read_text(encoding="utf-8")
    assert "task requests cannot be verified, 1 deleted" in log


def test_the_keys_come_from_the_installation_folder_not_from_secrets_dir(tmp_path: Path) -> None:
    """The agent can write secrets_dir; keys there would let it sign for itself (D0-2): the
    tasks read ``admin\\keys`` next to their fixed configuration path."""
    layout = Layout(tmp_path / "data")
    own = tmp_path / "own" / "admin" / "keys"
    other = tmp_path / "other" / "admin" / "keys"
    init_keys(own)
    init_keys(other)
    request = task_request("credit")
    folder = layout.request_dir("tasks")
    folder.mkdir(parents=True)
    (folder / f"{request.id}.json").write_bytes(
        sign_task_request(request, load_signing_key(other, "agent"))
    )
    assert take(layout, "tasks", own) == []
    (folder / f"{request.id}.json").write_bytes(
        sign_task_request(request, load_signing_key(own, "agent"))
    )
    assert take(layout, "tasks", own) == [request]


def test_an_oversize_file_is_not_read(site: Site) -> None:
    request = task_request("credit")
    data = sign_task_request(request, site.agent)
    site.write(request, data + b" " * (64 * 1024))
    assert take(site.layout, "tasks", site.programdata / "admin" / "keys") == []


def test_take_returns_the_oldest_first(site: Site) -> None:
    now = utc_now()
    later = site.request("credit", issued=now - timedelta(minutes=1))
    earlier = site.request("credit", issued=now - timedelta(minutes=2))
    taken = take(site.layout, "tasks", site.programdata / "admin" / "keys")
    assert [request.id for request in taken] == [earlier.id, later.id]


def test_a_payload_signed_but_not_a_task_request_is_ignored(site: Site) -> None:
    payload = {"schema": "arcilogger.task-request/1", "id": new_id()}
    data = sign_payload(canonical_json(payload), site.agent, TASK_REQUEST)
    folder = site.layout.request_dir("tasks")
    folder.mkdir(parents=True)
    (folder / f"{payload['id']}.json").write_bytes(data)
    assert take(site.layout, "tasks", site.programdata / "admin" / "keys") == []
    assert list(folder.iterdir()) == []


def test_clean_removes_week_old_ledger_entries_and_results(site: Site) -> None:
    layout = site.layout
    request = site.request("credit")
    assert site.run("requests", "run") == 0
    old = utc_now() - timedelta(days=8)
    stale = layout.request_dir("seal") / f"{new_id()}.json"  # never taken: no Seal run
    stale.parent.mkdir(parents=True)
    stale.write_bytes(b"{}")
    for path in (layout.request_seen / request.id, layout.request_results / f"{request.id}.json"):
        os.utime(path, (old.timestamp(), old.timestamp()))
    os.utime(stale, (old.timestamp(), old.timestamp()))
    fresh = site.request("credit")
    assert site.run("requests", "run") == 0
    assert not (layout.request_seen / request.id).exists()
    assert not (layout.request_results / f"{request.id}.json").exists()
    assert not stale.exists()
    assert (layout.request_seen / fresh.id).exists()
    assert clean(layout) == 0


# -- the Requests task ----------------------------------------------------------------------------


def test_requests_run_without_a_request_does_nothing(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    assert site.run("requests", "run") == 0
    assert capsys.readouterr().out == ""
    assert not (site.layout.state / "tsa-test").exists()  # no stamp, no credit spent
    assert not site.layout.credit_log.exists()
    assert not site.layout.requests.exists()


def test_a_credit_request_queries_the_credit(site: Site) -> None:
    mock_tsa(site).credits = 420
    request = site.request("credit")
    assert site.run("requests", "run") == 0
    result = site.result(request)
    assert result.ok
    assert result.result is not None
    assert result.result.model_dump(mode="json") == {
        "credits": 420,
        "threshold": 500,
        "below_threshold": True,
    }
    assert result.message_tr == "Kalan kontör: 420 (uyarı eşiği 500)."
    assert result.message_en == "Credits left: 420 (alert threshold 500)."
    # As the daily Credit task: the credit log and the threshold alert.
    entry = json.loads(site.layout.credit_log.read_text(encoding="utf-8").splitlines()[-1])
    assert entry["credits"] == 420
    assert [alert["rule"] for alert in read_alerts(site.layout.alerts)] == ["R20-credit"]
    assert list(site.layout.request_dir("tasks").iterdir()) == []


def test_a_credit_request_that_fails_says_why(site: Site) -> None:
    site.config.write_text(
        site.config.read_text(encoding="utf-8").replace(
            'provider = "mock"', 'provider = "zamane"\nzamane_jar = "C:/x/tss.jar"'
        ),
        encoding="utf-8",
    )
    request = site.request("credit")
    assert site.run("requests", "run") == 1
    result = site.result(request)
    assert not result.ok
    assert result.code == "credit_query_failed"
    assert result.message_tr is not None
    assert result.message_tr.startswith("Kamu SM kontör sorgusu başarısız")
    assert result.result is None


# -- the installation's own folders (D0-2) --------------------------------------------------------


def test_a_configuration_that_moves_secrets_dir_is_refused(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    elsewhere = site.programdata.parent / "agent-made"
    site.config.write_text(
        site.config.read_text(encoding="utf-8").replace(
            "[[sources]]", f'secrets_dir = "{elsewhere.as_posix()}"\n\n[[sources]]', 1
        ),
        encoding="utf-8",
    )
    for command in (("seal",), ("requests", "run"), ("credit",), ("verify",), ("status",)):
        assert site.run(*command) == 2, command
        assert "secrets_dir" in capsys.readouterr().err
    assert not site.layout.vault.exists()  # nothing was sealed
    (alert,) = read_alerts(site.layout.alerts)  # once a day, by the Seal task
    assert alert["rule"] == "R20-seal-config"
    assert alert["details"]["cause"] == "location"
    assert "possible tampering" in alert["message"]


def test_a_configuration_that_moves_data_dir_is_refused(
    site: Site, capsys: pytest.CaptureFixture[str]
) -> None:
    (site.programdata / DATA_ROOT_RECORD).write_text(f"{site.data}\r\n", encoding="utf-8-sig")
    assert recorded_data_root(site.config) == site.data
    assert site.run("seal") == 0  # the recorded data root: sealing as before
    elsewhere = site.programdata.parent / "agent-data"
    site.config.write_text(
        site.config.read_text(encoding="utf-8").replace(site.data.as_posix(), elsewhere.as_posix()),
        encoding="utf-8",
    )
    assert site.run("seal") == 2
    assert "data_dir" in capsys.readouterr().err
    assert not elsewhere.exists()
    (alert,) = read_alerts(site.layout.alerts)  # into the recorded data root
    assert alert["details"]["cause"] == "location"


def test_check_fixed_location(tmp_path: Path) -> None:
    config_path = tmp_path / "pd" / "arcilogger.toml"
    config_path.parent.mkdir()
    config_path.write_text(f'data_dir = "{(tmp_path / "data").as_posix()}"\n', encoding="utf-8")
    config = load_config(config_path)
    check_fixed_location(config, config_path)  # the defaults: no record, secrets_dir its folder
    explicit = config.model_copy(update={"secrets_dir": tmp_path / "pd" / "sub" / ".."})
    check_fixed_location(explicit, config_path)  # the same folder, written another way
    moved = config.model_copy(update={"secrets_dir": tmp_path / "other"})
    with pytest.raises(ConfigLocationError) as caught:
        check_fixed_location(moved, config_path)
    assert caught.value.key == "secrets_dir"
    assert caught.value.data_root == tmp_path / "data"
    assert "kurulumun klasörü" in str(caught.value)
    assert "is not the installation's folder" in str(caught.value)
    record = config_path.parent / DATA_ROOT_RECORD
    for unusable in ("", "relative/path\n", f"{tmp_path}\n{tmp_path}\n"):
        record.write_text(unusable, encoding="utf-8")
        assert recorded_data_root(config_path) is None
    record.write_text(str(tmp_path / "recorded"), encoding="utf-8")
    with pytest.raises(ConfigLocationError) as caught:
        check_fixed_location(config, config_path)
    assert caught.value.key == "data_dir"
    assert caught.value.data_root == tmp_path / "recorded"
