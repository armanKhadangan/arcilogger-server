"""The apply transaction: write-ahead record, versions, rollback and crash recovery (ADR-0014;
contract 2.7.1, 2.7.2)."""

from __future__ import annotations

import dataclasses
import hashlib
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import pytest

from arcilogger_core.admin.apply import CHECKPOINTS, Applier, ChangePlan
from arcilogger_core.admin.contract import KEEP_CONFIG_VERSIONS, KEEP_SECRETS_VERSIONS, new_id
from arcilogger_core.admin.rules import Install
from arcilogger_core.admin.store import Installation, read_txn
from arcilogger_core.admin.testing import Installed, make_installation
from arcilogger_core.admin.tomledit import SetValue, edit
from arcilogger_core.admin.winapi import SharingViolationError
from arcilogger_core.timeutil import SITE_TZ

NOW = datetime(2026, 10, 25, 10, 15, tzinfo=SITE_TZ)
CANARY = "kanarya-parola-7f3a"


class Crash(BaseException):
    """Stops the process at a checkpoint as a power cut would (nothing is rolled back)."""


@pytest.fixture
def installed(tmp_path: Path) -> Installed:
    return make_installation(tmp_path / "ProgramData")


def pair(installed: Installed) -> tuple[bytes, bytes]:
    return installed.config_path.read_bytes(), (installed.root / "secrets.json").read_bytes()


def make_plan(installed: Installed, *, install: bool = False) -> ChangePlan:
    old_toml, old_secrets = pair(installed)
    store = installed.store
    root = installed.root / "kamusm-roots" / "web.pem"
    return ChangePlan(
        id=new_id(),
        request=new_id(),
        actor="ayse",
        paths=("site_name", "smtp.password"),
        old_toml=old_toml,
        old_secrets=old_secrets,
        new_toml=edit(
            old_toml, [SetValue("site_name", "Örnek Holding")], path=installed.config_path
        ),
        new_secrets=store.render_with({"smtp.password": store.encrypt_value(CANARY)}),
        installs=(Install(root, b"-----BEGIN CERTIFICATE-----\n"),) if install else (),
        secret_names=("smtp.password",),
        journal={"kind": "config.apply"},
    )


def applier(installed: Installed, **options: object) -> Applier:
    location = Installation.of(installed.config_path)
    return Applier(location, now=lambda: NOW, **options)  # type: ignore[arg-type]


def no_temporary_files(installed: Installed) -> bool:
    return not [p for p in installed.root.iterdir() if p.name.endswith(".tmp")]


def test_an_apply_writes_both_files_and_keeps_the_old_pair_as_a_version(
    installed: Installed,
) -> None:
    plan = make_plan(installed, install=True)
    tool = applier(installed)
    outcome = tool.apply(plan)
    assert outcome.state == "applied"
    assert pair(installed) == (plan.new_toml, plan.new_secrets)
    assert outcome.version is not None
    assert outcome.version.n == 1
    assert tool.versions.read(1) == (plan.old_toml, plan.old_secrets)
    assert outcome.from_sha256 == hashlib.sha256(plan.old_toml).hexdigest()
    assert read_txn(installed.layout) is not None  # until the journal line is written
    tool.commit()
    assert read_txn(installed.layout) is None
    assert (installed.root / "kamusm-roots" / "web.pem").exists()
    assert installed.store.get("smtp.password") == CANARY
    assert no_temporary_files(installed)


@pytest.mark.parametrize("step", [c for c in CHECKPOINTS if c != "restore"])
def test_a_failure_at_any_step_leaves_the_old_pair(installed: Installed, step: str) -> None:
    plan = make_plan(installed, install=True)

    def fail(at: str) -> None:
        if at == step:
            raise OSError(f"injected at {at}")

    outcome = applier(installed, checkpoint=fail).apply(plan)
    assert outcome.state == "failed"
    assert outcome.code == "write_failed"
    assert pair(installed) == (plan.old_toml, plan.old_secrets)
    assert outcome.rolled_back == (step in ("replace_secrets", "replace_config", "verify"))
    assert not (installed.root / "kamusm-roots" / "web.pem").exists()
    assert read_txn(installed.layout) is None
    assert no_temporary_files(installed)


@pytest.mark.parametrize("when", ["before", "during"])
def test_a_file_edited_meanwhile_is_never_overwritten(installed: Installed, when: str) -> None:
    """The agent read the pair before its probes: an edit made on the console since then is a
    conflict, found before the version is saved or again right before the replace."""
    plan = make_plan(installed, install=True)
    edited = plan.old_toml + b"# edited on the console\n"

    def edit(at: str) -> None:
        if at == "install_files":
            installed.config_path.write_bytes(edited)

    if when == "before":
        installed.config_path.write_bytes(edited)
    tool = applier(installed, checkpoint=edit)
    outcome = tool.apply(plan)
    assert (outcome.state, outcome.code, outcome.rolled_back) == ("conflict", "conflict", False)
    assert pair(installed) == (edited, plan.old_secrets)
    assert (outcome.version is None) == (when == "before")
    assert not (installed.root / "kamusm-roots" / "web.pem").exists()
    assert read_txn(installed.layout) is None
    assert no_temporary_files(installed)


def two_roots(installed: Installed) -> tuple[ChangePlan, Path, Path]:
    roots = installed.root / "kamusm-roots"
    first, second = roots / "a.pem", roots / "b.pem"
    installs = (Install(first, b"-----BEGIN CERTIFICATE-----\na\n"),
                Install(second, b"-----BEGIN CERTIFICATE-----\nb\n"))  # fmt: skip
    return dataclasses.replace(make_plan(installed), installs=installs), first, second


def test_a_failed_install_removes_the_files_installed_before_it(installed: Installed) -> None:
    plan, first, second = two_roots(installed)
    second.parent.mkdir()
    second.write_bytes(b"another root\n")
    outcome = applier(installed).apply(plan)
    assert (outcome.state, outcome.code) == ("failed", "write_failed")
    assert not first.exists()
    assert second.read_bytes() == b"another root\n"  # never one this apply installed
    assert pair(installed) == (plan.old_toml, plan.old_secrets)
    assert not [p for p in first.parent.iterdir() if p.name.endswith(".tmp")]


def test_a_crash_between_two_installs_removes_the_first_at_recovery(
    installed: Installed, monkeypatch: pytest.MonkeyPatch
) -> None:
    plan, first, second = two_roots(installed)
    write_new = Applier._write_new

    def crash_at_the_second(self: Applier, path: Path, data: bytes) -> None:
        if path.name.startswith(f".{second.name}."):
            raise Crash(path.name)
        write_new(self, path, data)

    monkeypatch.setattr(Applier, "_write_new", crash_at_the_second)
    with pytest.raises(Crash):
        applier(installed).apply(plan)
    monkeypatch.undo()
    assert first.exists()
    outcome = applier(installed).recover()
    assert outcome is not None
    assert (outcome.state, outcome.code) == ("failed", "interrupted")
    assert not first.exists()
    assert not second.exists()


def test_a_file_that_does_not_verify_is_rolled_back(installed: Installed) -> None:
    plan = make_plan(installed)
    corrupted: list[Path] = []

    def corrupt(source: Path, target: Path) -> None:
        source.replace(target)
        if target.name == "arcilogger.toml" and not corrupted:  # the restore works
            corrupted.append(target)
            target.write_bytes(target.read_bytes() + b"# changed by someone else\n")

    outcome = applier(installed, replace=corrupt).apply(plan)
    assert (outcome.state, outcome.code, outcome.rolled_back) == ("failed", "verify_failed", True)
    assert pair(installed) == (plan.old_toml, plan.old_secrets)


def test_a_restore_that_fails_needs_attention_and_keeps_the_version(installed: Installed) -> None:
    plan = make_plan(installed)

    def fail(at: str) -> None:
        if at in ("verify", "restore"):
            raise OSError(f"injected at {at}")

    tool = applier(installed, checkpoint=fail)
    outcome = tool.apply(plan)
    assert (outcome.state, outcome.code) == ("needs_attention", "rollback_failed")
    assert outcome.version is not None
    assert tool.versions.read(outcome.version.n) == (plan.old_toml, plan.old_secrets)
    assert pair(installed) == (plan.new_toml, plan.new_secrets)  # never a mix
    assert read_txn(installed.layout) is not None  # until the caller journaled it
    tool.commit()
    assert read_txn(installed.layout) is None


def test_lost_write_access_is_acl_drift(installed: Installed) -> None:
    """The files were replaced and verified: the change is in force, with the code."""
    plan = make_plan(installed)
    outcome = applier(installed, writable=lambda path: False).apply(plan)
    assert (outcome.state, outcome.code) == ("applied", "acl_drift")
    assert pair(installed) == (plan.new_toml, plan.new_secrets)


def test_a_sharing_violation_is_retried_for_ten_seconds(installed: Installed) -> None:
    clock = [0.0]
    sleeps: list[float] = []
    calls = [0]

    def busy_three_times(source: Path, target: Path) -> None:
        calls[0] += 1
        if calls[0] <= 3:
            raise SharingViolationError(None, "in use", str(target), 32)
        source.replace(target)

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock[0] += seconds

    plan = make_plan(installed)
    outcome = applier(
        installed, replace=busy_three_times, sleep=sleep, monotonic=lambda: clock[0]
    ).apply(plan)
    assert outcome.state == "applied"
    assert sleeps == [0.05, 0.1, 0.2]

    def always_busy(source: Path, target: Path) -> None:
        raise SharingViolationError(None, "in use", str(target), 32)

    clock[0] = 0.0
    sleeps.clear()
    again = make_plan(installed)
    outcome = applier(
        installed, replace=always_busy, sleep=sleep, monotonic=lambda: clock[0]
    ).apply(again)
    assert (outcome.state, outcome.code) == ("failed", "write_failed")
    assert sum(sleeps) >= 10
    assert pair(installed) == (again.old_toml, again.old_secrets)


def test_versions_keep_twenty_configurations_and_five_secret_stores(installed: Installed) -> None:
    tool = applier(installed)
    for number in range(25):
        old_toml, old_secrets = pair(installed)
        new_toml = edit(
            old_toml, [SetValue("site_name", f"Örnek {number}")], path=installed.config_path
        )
        plan = ChangePlan(
            id=new_id(),
            request=new_id(),
            actor="ayse",
            paths=("site_name",),
            old_toml=old_toml,
            old_secrets=old_secrets,
            new_toml=new_toml,
        )
        assert tool.apply(plan).state == "applied"
        tool.commit()
    versions = tool.versions.index().versions
    assert [v.n for v in versions] == list(range(6, 26))
    assert len(versions) == KEEP_CONFIG_VERSIONS
    assert [v.n for v in versions if v.has_secrets] == list(range(21, 26))
    assert sum(v.has_secrets for v in versions) == KEEP_SECRETS_VERSIONS
    assert not installed.layout.version_dir(5).exists()
    assert not (installed.layout.version_dir(20) / "secrets.json").exists()
    assert tool.versions.read(25)[1] is not None


# -- crash recovery --------------------------------------------------------------------------------


def crash_at(step: str) -> Callable[[str], None]:
    def checkpoint(at: str) -> None:
        if at == step:
            raise Crash(at)

    return checkpoint


@pytest.mark.parametrize(
    ("step", "state", "code", "new"),
    [
        ("versions", "failed", "interrupted", False),
        ("write_temp", "failed", "interrupted", False),
        ("install_files", "failed", "interrupted", False),
        ("replace_secrets", "failed", "interrupted", False),
        ("replace_config", "failed", "interrupted", False),  # secrets.json was replaced already
        ("verify", "applied", None, True),
    ],
)
def test_recovery_after_a_crash_at_each_stage(
    installed: Installed, step: str, state: str, code: str | None, new: bool
) -> None:
    plan = make_plan(installed, install=True)
    with pytest.raises(Crash):
        applier(installed, checkpoint=crash_at(step)).apply(plan)
    assert read_txn(installed.layout) is not None
    if step == "replace_config":
        assert pair(installed) == (plan.old_toml, plan.new_secrets)  # the mix a crash leaves
    outcome = applier(installed).recover()
    assert outcome is not None
    assert (outcome.state, outcome.code) == (state, code)
    expected = (plan.new_toml, plan.new_secrets) if new else (plan.old_toml, plan.old_secrets)
    assert pair(installed) == expected
    if new:
        assert outcome.recovered
        applier(installed).commit()
    else:
        assert not (installed.root / "kamusm-roots" / "web.pem").exists()
    assert read_txn(installed.layout) is None
    assert no_temporary_files(installed)
    assert applier(installed).recover() is None


def test_a_file_that_is_neither_old_nor_new_after_a_crash_needs_attention(
    installed: Installed,
) -> None:
    plan = make_plan(installed)
    with pytest.raises(Crash):
        applier(installed, checkpoint=crash_at("replace_config")).apply(plan)
    installed.config_path.write_bytes(plan.old_toml + b"# edited by hand meanwhile\n")
    outcome = applier(installed).recover()
    assert outcome is not None
    assert (outcome.state, outcome.code) == ("needs_attention", "rollback_failed")
    assert read_txn(installed.layout) is None


def test_a_console_restore_needs_no_request(installed: Installed) -> None:
    plan = make_plan(installed)
    tool = applier(installed)
    assert tool.apply(plan).state == "applied"
    tool.commit()
    toml, secrets = tool.versions.read(1)
    restore = ChangePlan(
        id=new_id(),
        request=None,
        actor="console",
        paths=("site_name",),
        old_toml=plan.new_toml,
        old_secrets=plan.new_secrets,
        new_toml=toml,
        new_secrets=secrets,
    )
    outcome = tool.apply(restore)
    assert outcome.state == "applied"
    tool.commit()
    assert pair(installed) == (plan.old_toml, plan.old_secrets)
    entry = tool.versions.entry(2)
    assert entry is not None
    assert (entry.request, entry.actor) == (None, "console")
