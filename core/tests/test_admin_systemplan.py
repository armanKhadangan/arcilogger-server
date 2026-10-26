"""The system plan ApplySystem applies (ADR-0014; contract 2.8.3, Day 0 notes D0-15, D0-16).

:func:`~arcilogger_core.admin.systemplan.plan` is a pure function: the narrowing rule, the
outbound rules, the time plan and the jar promotion are tested on :class:`PlanInputs`; the last
tests read a real installation (the agent's versions, the collector's status file).
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.admin import systemplan
from arcilogger_core.admin.contract import FALLBACK_POLICY, SystemPlan, dump_document
from arcilogger_core.admin.systemplan import (
    PlanError,
    PlanInputs,
    enabled_senders,
    peer_list,
    plan,
    read_inputs,
    staged_clients,
)
from arcilogger_core.admin.testing import FakeAgent, FakeSystem, Installed, make_installation
from arcilogger_core.config import Config, parse_config
from arcilogger_core.timeutil import SITE_TZ, isoformat_site
from arcilogger_core.tsa import zamane_releases
from arcilogger_core.tsa.zamane_releases import ZamaneRelease

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
POLICY = FALLBACK_POLICY.model_copy(update={"enabled": True})
WHERE = Path("/srv/arci/arcilogger.toml")
CURRENT = "c" * 64
OLDER = "b" * 64


def config_of(*sources: str | tuple[str, bool], extra: str = "") -> Config:
    """A configuration with these source addresses (``(address, False)``: disabled)."""
    text = 'data_dir = "/srv/data"\n' + extra + "\n"
    for index, source in enumerate(sources):
        address, enabled = source if isinstance(source, tuple) else (source, True)
        text += (
            f'[[sources]]\nname = "s{index}"\ntype = "generic"\naddress = "{address}"\n'
            f"enabled = {str(enabled).lower()}\n"
        )
    return parse_config(text.encode("utf-8"), WHERE)


def inputs(config: Config, **values: Any) -> PlanInputs:
    return PlanInputs(config=config, config_sha256=CURRENT, policy=POLICY, **values)


def senders(planned: SystemPlan) -> list[str]:
    return list(planned.firewall.syslog_addresses)


# -- the syslog senders ----------------------------------------------------------------------------


def test_the_first_run_opens_the_current_senders() -> None:
    current = config_of("172.20.10.1", "172.20.20.0/24")
    assert senders(plan(inputs(current))) == ["172.20.10.1", "172.20.20.0/24"]
    planned = plan(inputs(current, collector_ack=None, applied_addresses=None))
    assert planned.collector_ack_sha256 is None
    assert senders(planned) == ["172.20.10.1", "172.20.20.0/24"]


def test_a_new_sender_opens_only_after_the_collector_knows_it() -> None:
    older = config_of("172.20.10.1")
    current = config_of("172.20.10.1", "172.20.10.22")
    waiting = plan(
        inputs(
            current,
            collector_ack=OLDER,
            acknowledged=older,
            applied_addresses=("172.20.10.1",),
        )
    )
    assert senders(waiting) == ["172.20.10.1"]
    assert waiting.collector_ack_sha256 == OLDER
    known = plan(inputs(current, collector_ack=CURRENT, applied_addresses=("172.20.10.1",)))
    assert senders(known) == ["172.20.10.1", "172.20.10.22"]


def test_a_removed_sender_is_closed_at_once() -> None:
    older = config_of("172.20.10.1", "172.20.10.22")
    current = config_of("172.20.10.1")
    planned = plan(
        inputs(
            current,
            collector_ack=OLDER,
            acknowledged=older,
            applied_addresses=("172.20.10.1", "172.20.10.22"),
        )
    )
    assert senders(planned) == ["172.20.10.1"]


def test_a_stopped_collector_counts_as_acknowledged() -> None:
    older = config_of("172.20.10.1")
    current = config_of("172.20.10.1", "172.20.10.22")
    planned = plan(inputs(current, collector_ack=OLDER, acknowledged=older, collector_fresh=False))
    assert senders(planned) == ["172.20.10.1", "172.20.10.22"]


def test_an_unknown_acknowledged_version_never_widens_the_rule() -> None:
    current = config_of("172.20.10.1", "172.20.10.22")
    unknown = plan(
        inputs(current, collector_ack=OLDER, acknowledged=None, applied_addresses=("172.20.10.1",))
    )
    assert senders(unknown) == ["172.20.10.1"]
    silent = plan(inputs(current, collector_ack=None, applied_addresses=("172.20.10.1",)))
    assert senders(silent) == ["172.20.10.1"]
    # An address the acknowledged configuration has but the current one does not stays closed.
    acknowledged = config_of("172.20.10.1", "172.20.10.99")
    narrowed = plan(
        inputs(config_of("172.20.10.1"), collector_ack=OLDER, acknowledged=acknowledged)
    )
    assert senders(narrowed) == ["172.20.10.1"]


def test_disabled_and_out_of_policy_sources_stay_closed() -> None:
    current = config_of(
        "172.20.10.1",
        ("172.20.10.2", False),
        "8.8.8.8",
        "172.16.0.0/15",
        "172.21.0.0/16",
        "172.20.10.30/32",
    )
    found, problems = enabled_senders(current, POLICY)
    assert found == ["172.20.10.1", "172.21.0.0/16", "172.20.10.30"]
    assert [(p.code, p.value) for p in problems] == [
        ("source_outside_policy", "8.8.8.8"),
        ("source_outside_policy", "172.16.0.0/15"),
    ]
    planned = plan(inputs(current))
    assert senders(planned) == found
    assert planned.problems == tuple(problems)
    tight = POLICY.model_copy(update={"source_networks": ("172.20.10.0/24",)})
    assert enabled_senders(current, tight)[0] == ["172.20.10.1", "172.20.10.30"]


def test_a_plan_without_sources_removes_the_syslog_rules() -> None:
    planned = plan(inputs(config_of(), applied_addresses=("172.20.10.1",)))
    assert senders(planned) == []
    assert planned.firewall.managed is False


# -- outbound rules and the clock ------------------------------------------------------------------


SMTP_AND_REPLICA = """\
[clock]
ntp_server = "172.20.10.1"
fallback_server = "time.ume.tubitak.gov.tr"
[smtp]
enabled = true
host = "smtp.ornek.example"
port = 587
from_address = "arcilogger@ornek.example"
[replica]
enabled = true
target = '\\\\NAS-01\\worm'
"""


def test_outbound_rules_are_planned_in_strict_mode_only() -> None:
    current = config_of("172.20.10.1", extra=SMTP_AND_REPLICA)
    assert plan(inputs(current, firewall_managed=True)).firewall.outbound == ()
    planned = plan(
        inputs(
            current,
            firewall_managed=True,
            strict_outbound=True,
            smtp_server="relay.ornek.example:25",
        )
    )
    rules = [(rule.rule, rule.host, rule.port) for rule in planned.firewall.outbound]
    assert rules == [
        ("ARCILOGGER Out NTP", "172.20.10.1", 123),
        ("ARCILOGGER Out NTP", "time.ume.tubitak.gov.tr", 123),
        ("ARCILOGGER Out Kamu SM", "zd.kamusm.gov.tr", 80),
        ("ARCILOGGER Out Kamu SM", "tzd.kamusm.gov.tr", 80),
        ("ARCILOGGER Out SMTP", "smtp.ornek.example", 587),
        ("ARCILOGGER Out SMTP 2", "relay.ornek.example", 25),
        ("ARCILOGGER Out Replica", "NAS-01", 445),
    ]
    assert planned.firewall.strict_outbound is True
    assert planned.problems == ()


def test_an_invalid_remembered_host_is_left_out_and_named() -> None:
    planned = plan(
        inputs(config_of(), strict_outbound=True, smtp_server="kötü host:25", firewall_managed=True)
    )
    assert "kötü host" not in [rule.host for rule in planned.firewall.outbound]
    assert [(p.code, p.value) for p in planned.problems] == [("outbound_host_invalid", "kötü host")]


def test_the_time_plan_and_the_peer_list() -> None:
    current = config_of(extra=SMTP_AND_REPLICA)
    first = plan(inputs(current)).time
    assert (first.primary, first.fallback, first.changed) == (
        "172.20.10.1",
        "time.ume.tubitak.gov.tr",
        True,
    )
    peers = peer_list("172.20.10.1", "time.ume.tubitak.gov.tr")
    assert peers == "172.20.10.1,0x8 time.ume.tubitak.gov.tr,0xA"
    assert plan(inputs(current, applied_peers=peers)).time.changed is False
    assert plan(inputs(current, applied_peers="172.20.10.1,0x8")).time.changed is True
    assert peer_list("", "time.ume.tubitak.gov.tr") == "time.ume.tubitak.gov.tr,0x8"
    assert peer_list("172.20.10.1", "172.20.10.1") == "172.20.10.1,0x8"
    assert peer_list(None, None) == ""


# -- Zamane clients (D0-16) ------------------------------------------------------------------------


def stage(folder: Path, data: bytes) -> str:
    digest = hashlib.sha256(data).hexdigest()
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"{digest}.jar").write_bytes(data)
    return digest


def test_only_a_known_release_is_promoted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    web = tmp_path / "zamane" / "web"
    known = stage(web, b"PK known client")
    unknown = stage(web, b"PK some other client")
    approved = stage(web, b"PK approved before")
    monkeypatch.setattr(
        zamane_releases,
        "KNOWN_RELEASES",
        {known: ZamaneRelease("3.1.33"), approved: ZamaneRelease("3.1.32")},
    )
    staged = staged_clients(web)
    assert set(staged) == {known, unknown, approved}
    planned = plan(inputs(config_of(), staged=staged, approved=frozenset({approved})))
    assert [(p.sha256, p.reason) for p in planned.jars.promote] == [(known, "known")]
    assert planned.jars.promote[0].staged == str(web / f"{known}.jar")
    monkeypatch.setattr(zamane_releases, "KNOWN_RELEASES", {})
    assert plan(inputs(config_of(), staged=staged)).jars.promote == ()  # time alone: never


def test_a_staged_file_must_have_the_hash_it_is_named_by(tmp_path: Path) -> None:
    web = tmp_path / "web"
    good = stage(web, b"PK good")
    (web / f"{'d' * 64}.jar").write_bytes(b"PK renamed")
    (web / "notes.txt").write_bytes(b"x")
    (web / f"{'e' * 64}.jar").mkdir()
    if os.name != "nt":
        (web / f"{hashlib.sha256(b'PK target').hexdigest()}.jar").symlink_to(web / "notes.txt")
    assert list(staged_clients(web)) == [good]
    assert staged_clients(tmp_path / "missing") == {}


# -- reading a real installation -------------------------------------------------------------------


@pytest.fixture
def installed(tmp_path: Path) -> Installed:
    return make_installation(tmp_path / "ProgramData")


def collector_reports(installed: Installed, sha256: str | None, *, age: float = 0) -> None:
    status = installed.data.collector_status
    status.parent.mkdir(parents=True, exist_ok=True)
    block: dict[str, Any] = {
        "sha256": sha256,
        "loaded_at": isoformat_site(NOW, "seconds"),
        "error": None,
        "restart_keys": [],
    }
    status.write_text(json.dumps({"config": block}), encoding="utf-8")
    stamp = NOW.timestamp() - age
    os.utime(status, (stamp, stamp))


def add_source(installed: Installed, name: str, address: str) -> None:
    source = {"name": name, "type": "fortigate", "address": address}
    request = installed.request(section="sources", ops=[{"op": "source_add", "source": source}])
    installed.submit(request)
    agent = FakeAgent(installed, tasks=False)
    agent.process()
    state = installed.status(request.id)
    assert state is not None
    assert state.state in ("applied", "active"), state


def sha256_of(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_plan_of_an_installation_follows_the_collector(installed: Installed) -> None:
    add_source(installed, "fw-01", "172.20.10.22")
    first = systemplan.system_plan(installed.config_path, now=NOW)
    assert senders(first) == ["172.20.10.22"]  # nothing applied yet: the installer's rule
    system = FakeSystem(installed, now=lambda: NOW)
    system.run()
    before = sha256_of(installed.config_path)
    collector_reports(installed, before)
    add_source(installed, "fw-02", "172.20.10.23")
    waiting = systemplan.system_plan(installed.config_path, now=NOW)
    assert waiting.config_sha256 == sha256_of(installed.config_path)
    assert waiting.collector_ack_sha256 == before
    assert senders(waiting) == ["172.20.10.22"]  # the collector has not loaded fw-02 yet
    collector_reports(installed, waiting.config_sha256)
    assert senders(systemplan.system_plan(installed.config_path, now=NOW)) == [
        "172.20.10.22",
        "172.20.10.23",
    ]
    collector_reports(installed, before, age=120)  # stopped: its rule follows the file
    assert len(senders(systemplan.system_plan(installed.config_path, now=NOW))) == 2


def test_read_inputs_takes_the_firewall_state_and_the_approved_clients(
    installed: Installed,
) -> None:
    state = installed.root / "install" / "firewall-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(
        '{"schema": 1, "strict_outbound": true, "smtp_server": "relay.ornek.example:25"}',
        encoding="utf-8-sig",
    )
    read = read_inputs(installed.config_path, now=NOW)
    assert (read.firewall_managed, read.strict_outbound) == (True, True)
    assert read.smtp_server == "relay.ornek.example:25"
    assert read.collector_fresh is False  # no status file
    assert read.applied_addresses is None
    assert read.policy.enabled is True


def test_a_configuration_that_does_not_load_gives_no_plan(installed: Installed) -> None:
    installed.config_path.write_text("data_dir = \n", encoding="utf-8")
    with pytest.raises(PlanError):
        read_inputs(installed.config_path, now=NOW)


def test_the_printed_plan_parses_as_the_contract_document(installed: Installed) -> None:
    planned = systemplan.system_plan(installed.config_path, now=NOW)
    assert SystemPlan.model_validate_json(dump_document(planned)) == planned
