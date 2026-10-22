"""The replica (spec 8.3): interrupted copies, tampering, and SMB credentials.

* A copy goes to ``<target>/.arcilogger-staging/`` first and is renamed only when its hash
  matches: an interrupted copy never leaves a half file in a day folder.
* A half file left by an earlier version (a strict prefix of the source) is an interrupted copy
  (high alert, replaced), not tampering; any other difference stays critical and untouched.
* One bad file does not stop the others (the manifest and its token still reach the replica).
* An SMB target with ``replica.user`` is connected with the stored ``replica.password``
  (never printed, never in an alert) and disconnected afterwards.
"""

from __future__ import annotations

import io
import shutil
from datetime import date, timedelta
from pathlib import Path
from typing import IO, Any

import pytest

from arcilogger_core.alerts import read_alerts
from arcilogger_core.cli import main
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.replica import STAGING, replicate_day, replicate_due
from arcilogger_core.seal import sha256_file
from arcilogger_core.smbshare import ShareError, connected, unc_root


def _seal(world: Any, days: list[date]) -> None:
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    assert all(o.status == "sealed" for o in world.sealer().seal_due())


def _replica_alerts(world: Any) -> list[dict[str, Any]]:
    return [a for a in read_alerts(world.layout.alerts) if a["rule"] == "R20-replica"]


def test_an_interrupted_copy_never_leaves_a_half_file(
    world: Any, days: list[date], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _seal(world, days[:1])
    target = tmp_path / "nas"
    calls = {"n": 0}
    original = shutil.copyfileobj

    def drop_on_the_second_file(source: IO[bytes], out: IO[bytes], length: int = 0) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            out.write(source.read(100))
            raise OSError("the network went away")
        original(source, out, length)

    monkeypatch.setattr("arcilogger_core.replica.shutil.copyfileobj", drop_on_the_second_file)
    first = replicate_day(world.layout, days[0], target)
    assert first.status == "failed"
    assert first.copied == 4  # the other files were still copied
    day_dir = target / "2026" / "10" / "10"
    sources = sorted(p.name for p in world.layout.vault_day(days[0]).iterdir())
    present = sorted(p.name for p in day_dir.iterdir())
    assert len(present) == len(sources) - 1  # the interrupted one is not there at all
    assert not list((target / STAGING).rglob("*.part"))
    assert [a["severity"] for a in _replica_alerts(world)] == ["high"]
    assert not world.layout.replica_state(days[0]).exists()

    monkeypatch.undo()
    second = replicate_due(world.layout, target)
    assert [(o.status, o.copied) for o in second] == [("copied", 1)]
    for name in sources:
        assert sha256_file(day_dir / name) == sha256_file(world.layout.vault_day(days[0]) / name)
    assert world.layout.replica_state(days[0]).exists()
    assert not any(a["severity"] == "critical" for a in _replica_alerts(world))
    # The replica verifies on its own (the staging folder is outside the day folders).
    roots = tmp_path / "roots"
    roots.mkdir()
    (roots / "root.pem").write_bytes((world.tsa.state_dir / "root.pem").read_bytes())
    with sv.open_package(target) as package:
        report = sv.verify_package(package, deep=False, roots_dir=roots)
    assert report.passed, [(f.code, f.en) for f in report.findings if f.level == "fail"]


def test_a_half_file_of_an_earlier_version_is_completed_not_called_tampering(
    world: Any, days: list[date], tmp_path: Path
) -> None:
    _seal(world, days[:1])
    target = tmp_path / "nas"
    source = world.layout.vault_day(days[0]) / "fortigate.log.gz"
    half = target / "2026" / "10" / "10" / "fortigate.log.gz"
    half.parent.mkdir(parents=True)
    half.write_bytes(source.read_bytes()[:50])
    outcome = replicate_day(world.layout, days[0], target)
    assert outcome.status == "copied", outcome.message
    assert half.read_bytes() == source.read_bytes()
    assert [(a["severity"], "yarım kalmış" in a["message"]) for a in _replica_alerts(world)] == [
        ("high", True)
    ]


def test_a_different_file_is_never_overwritten_and_the_rest_is_copied(
    world: Any, days: list[date], tmp_path: Path
) -> None:
    _seal(world, days[:1])
    target = tmp_path / "nas"
    victim = target / "2026" / "10" / "10" / "core-r1.log.gz"
    victim.parent.mkdir(parents=True)
    victim.write_bytes(b"tampered, longer than nothing")
    outcome = replicate_day(world.layout, days[0], target)
    assert outcome.status == "failed"
    assert "differs" in outcome.message
    assert victim.read_bytes() == b"tampered, longer than nothing"
    names = {p.name for p in victim.parent.iterdir()}
    assert {world.layout.manifest(days[0]).name, world.layout.token(days[0]).name} <= names
    assert [a["severity"] for a in _replica_alerts(world)] == ["critical"]
    replicate_day(world.layout, days[0], target)  # the hourly task runs again
    assert len(_replica_alerts(world)) == 1  # one alert per problem


# -- SMB credentials -----------------------------------------------------------------------------


class FakeShare:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, ...]] = []
        self.fail = fail

    def add(self, remote: str, user: str, password: str) -> None:
        self.calls.append(("add", remote, user, password))
        if self.fail:
            raise ShareError(f"{remote} paylaşımına bağlanılamadı: parola yanlış")

    def cancel(self, remote: str) -> None:
        self.calls.append(("cancel", remote))


def test_unc_roots() -> None:
    assert unc_root("\\\\NAS-01\\arcilogger-worm") == "\\\\NAS-01\\arcilogger-worm"
    assert unc_root("\\\\NAS-01\\arcilogger-worm\\sub") == "\\\\NAS-01\\arcilogger-worm"
    assert unc_root("//NAS-01/arcilogger-worm") == "\\\\NAS-01\\arcilogger-worm"
    assert unc_root("D:\\replica") is None
    assert unc_root("/mnt/nas") is None


def test_a_share_is_connected_with_the_stored_password_and_always_released() -> None:
    share = FakeShare()
    target = Path("\\\\NAS-01\\arcilogger-worm")
    with pytest.raises(RuntimeError), connected(target, "NAS-01\\arci", "Gizli-1", api=share):
        raise RuntimeError("the copy failed")
    assert share.calls == [
        ("add", "\\\\NAS-01\\arcilogger-worm", "NAS-01\\arci", "Gizli-1"),
        ("cancel", "\\\\NAS-01\\arcilogger-worm"),
    ]
    local = FakeShare()
    with connected(Path("/mnt/nas"), "x", "y", api=local):
        pass
    with connected(target, None, None, api=local):
        pass
    assert local.calls == []  # a local target, or no user: no connection
    with pytest.raises(ShareError, match=r"replica\.password"), connected(target, "u", None):
        pass


CONFIG = """
data_dir = "{data}"
[seal]
quiet_seconds = 0
[tsa]
provider = "mock"
[replica]
enabled = true
target = '\\\\NAS-01\\arcilogger-worm'
user = 'NAS-01\\arcireplica'
"""


def test_the_replica_command_uses_the_stored_password_and_never_shows_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    config = tmp_path / "arcilogger.toml"
    config.write_text(CONFIG.format(data=(tmp_path / "data").as_posix()), encoding="utf-8")
    assert main(["--config", str(config), "secret", "init"]) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("Replika-Parolası-9\n"))
    assert main(["--config", str(config), "secret", "set", "replica.password"]) == 0
    share = FakeShare(fail=True)
    monkeypatch.setattr("arcilogger_core.smbshare.default_api", lambda: share)
    capsys.readouterr()
    assert main(["--config", str(config), "replica", "--test"]) == 1
    captured = capsys.readouterr()
    assert "parola yanlış" in captured.err
    assert share.calls[0][1:] == (
        "\\\\NAS-01\\arcilogger-worm",
        "NAS-01\\arcireplica",
        "Replika-Parolası-9",
    )
    alerts = (tmp_path / "data" / "state" / "alerts.jsonl").read_text(encoding="utf-8")
    assert "Replika hedefine bağlanılamadı" in alerts
    assert "Replika-Parolası-9" not in alerts + captured.out + captured.err


def test_the_replica_connection_test_on_a_local_target(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    target = tmp_path / "nas"
    config = tmp_path / "arcilogger.toml"
    config.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        f'[replica]\nenabled = true\ntarget = "{target.as_posix()}"\n',
        encoding="utf-8",
    )
    assert main(["--config", str(config), "replica", "--test"]) == 0
    assert "çalışıyor" in capsys.readouterr().out
    assert not list((target / STAGING).iterdir())  # the probe file is gone
