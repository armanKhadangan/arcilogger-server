"""The console commands of ``python -I -m arcilogger_core.admin`` (ADR-0014; contract 2.7.2,
2.8.3, Day 0 note D0-16)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from arcilogger_core.admin import __main__ as console
from arcilogger_core.admin.contract import (
    JarsFile,
    StagedJar,
    SystemPlan,
    dump_document,
    parse_document,
)
from arcilogger_core.admin.store import Installation, read_txn
from arcilogger_core.admin.testing import FakeAgent, Installed, make_installation
from arcilogger_core.fsutil import atomic_write_bytes
from arcilogger_core.timeutil import SITE_TZ, utc_now

SITE = {"op": "set", "path": "site_name", "expect": "Örnek A.Ş.", "value": "Örnek Holding"}


@pytest.fixture
def installed(tmp_path: Path) -> Installed:
    return make_installation(tmp_path / "ProgramData")


def run(installed: Installed, *args: str) -> int:
    return console.main([*args[:1], "--config", str(installed.config_path), *args[1:]])


def test_the_module_runs_isolated(installed: Installed) -> None:
    completed = subprocess.run(
        [sys.executable, "-I", "-m", "arcilogger_core.admin", "keys", "show", "--config",
         str(installed.config_path)],
        capture_output=True, check=False, timeout=120,
    )  # fmt: skip
    assert completed.returncode == 0, completed.stderr
    assert installed.instance in completed.stdout.decode()


def test_keys_init_is_idempotent_and_show_lists_public_ids(
    installed: Installed, capsys: pytest.CaptureFixture[str]
) -> None:
    before = (installed.layout.keys / "keys.json").read_bytes()
    assert run(installed, "keys", "init") == 0
    assert (installed.layout.keys / "keys.json").read_bytes() == before
    assert run(installed, "keys", "show") == 0
    out = capsys.readouterr().out
    assert installed.api_key.key_id in out
    assert installed.agent_key.key_id in out
    assert "PRIVATE" not in out.upper()


def test_status_reports_the_agent(installed: Installed, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(installed, "status") == 1  # no heartbeat yet
    FakeAgent(installed).process()
    capsys.readouterr()
    assert run(installed, "status", "--json") == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["agent"]["state"] == "ok"
    assert summary["policy"]["enabled"] is True
    assert summary["transaction_in_progress"] is False


def test_versions_list_and_restore(
    installed: Installed, capsys: pytest.CaptureFixture[str]
) -> None:
    agent = FakeAgent(installed)
    installed.submit(installed.request(section="general", ops=[SITE]))
    agent.process()
    assert installed.config().site_name == "Örnek Holding"
    capsys.readouterr()
    assert run(installed, "versions", "list", "--json") == 0
    versions = json.loads(capsys.readouterr().out)
    assert [v["n"] for v in versions] == [1]
    assert versions[0]["paths"] == ["site_name"]
    assert run(installed, "versions", "restore", "1") == 0
    assert installed.config().site_name == "Örnek A.Ş."
    assert run(installed, "versions", "list") == 0
    out = capsys.readouterr().out
    assert "console" in out
    assert run(installed, "versions", "restore", "9") == 2
    assert run(installed, "versions", "restore") == 2


def test_a_restore_waits_for_a_recovery(installed: Installed) -> None:
    class Crash(BaseException):
        pass

    def crash(step: str) -> None:
        if step == "verify":
            raise Crash

    installed.submit(installed.request(section="general", ops=[SITE]))
    with pytest.raises(Crash):
        FakeAgent(installed, checkpoint=crash).process()
    assert read_txn(installed.layout) is not None
    assert run(installed, "versions", "restore", "1") == 1
    assert run(installed, "recover") == 0
    assert read_txn(installed.layout) is None
    assert installed.config().site_name == "Örnek Holding"
    assert run(installed, "recover") == 0  # nothing left


def test_system_plan_prints_the_plan(
    installed: Installed, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(installed, "system-plan") == 0
    plan = parse_document(SystemPlan, capsys.readouterr().out.encode())
    assert plan.config_sha256 == hashlib.sha256(installed.config_path.read_bytes()).hexdigest()
    assert plan.firewall.managed is False
    assert plan.time.primary == "172.20.10.1"


def test_system_plan_refuses_a_moved_secret_store(
    installed: Installed, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    text = installed.config_path.read_text(encoding="utf-8")
    elsewhere = (tmp_path / "elsewhere").as_posix()
    installed.config_path.write_text(
        text.replace("data_dir =", f'secrets_dir = "{elsewhere}"\ndata_dir ='), encoding="utf-8"
    )
    assert run(installed, "system-plan") == 1
    assert "system_plan_failed" in capsys.readouterr().err


def test_system_plan_refuses_a_data_root_the_installer_did_not_record(
    installed: Installed, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (installed.root / "data-root.txt").write_text(str(tmp_path / "other"), encoding="utf-8")
    assert run(installed, "system-plan") == 1
    assert "data_dir" in capsys.readouterr().err
    (installed.root / "data-root.txt").write_text("relative/data\n", encoding="utf-8")
    assert run(installed, "system-plan") == 1  # an unusable record fails closed
    assert "data-root.txt" in capsys.readouterr().err


# -- approve-jar -----------------------------------------------------------------------------------


def stage(installed: Installed, data: bytes, first_seen: datetime) -> str:
    digest = hashlib.sha256(data).hexdigest()
    staging = installed.root / "zamane" / "web"
    staging.mkdir(parents=True, exist_ok=True)
    (staging / f"{digest}.jar").write_bytes(data)
    jars = JarsFile(staged=(StagedJar(sha256=digest, first_seen=first_seen),))
    atomic_write_bytes(installed.layout.jars, dump_document(jars))
    return digest


def test_approve_jar_waits_for_the_hold_and_records_the_administrator(installed: Installed) -> None:
    now = datetime(2026, 10, 25, 10, 0, tzinfo=SITE_TZ)
    digest = stage(installed, b"PK client bytes", now - timedelta(hours=23))
    location = Installation.of(installed.config_path)
    with pytest.raises(console.ConsoleError, match=r"bekletme|hold"):
        console.approve_jar(location, digest, user="ayse", now=now)
    later = now + timedelta(hours=2)
    path = console.approve_jar(location, digest, user="ayse", now=later)
    assert path == installed.root / "zamane" / "approved" / f"{digest}.jar"
    assert path.read_bytes() == b"PK client bytes"
    jars = parse_document(JarsFile, installed.layout.jars.read_bytes())
    assert [(j.sha256, j.approved_by) for j in jars.approved] == [(digest, "console:ayse")]
    assert jars.staged[0].sha256 == digest  # ApplySystem's entries are kept
    journal = installed.layout.system_journal.read_text(encoding="utf-8").splitlines()
    assert json.loads(journal[-1])["jar"] == {"sha256": digest, "approved_by": "console:ayse"}
    assert console.approve_jar(location, digest, user="ayse", now=later) == path  # idempotent


def test_approve_jar_refuses_what_it_cannot_check(installed: Installed) -> None:
    location = Installation.of(installed.config_path)
    now = utc_now() + timedelta(days=2)
    with pytest.raises(console.ConsoleError):
        console.approve_jar(location, "not-a-hash", user="ayse", now=now)
    with pytest.raises(console.ConsoleError, match="ApplySystem"):
        console.approve_jar(location, "c3" * 32, user="ayse", now=now)
    digest = stage(installed, b"PK original", utc_now() - timedelta(days=3))
    (installed.root / "zamane" / "web" / f"{digest}.jar").write_bytes(b"PK swapped")
    with pytest.raises(console.ConsoleError, match="SHA-256"):
        console.approve_jar(location, digest, user="ayse", now=now)
    assert not (installed.root / "zamane" / "approved" / f"{digest}.jar").exists()
    assert run(installed, "approve-jar", digest, "--user", "ayse") == 1
