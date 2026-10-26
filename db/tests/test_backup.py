"""``arcilogger-db backup``: scheduled dumps, state file, alert, retention and restore (ADR-0009).

The failure paths run with fake ``pg_dump`` / ``pg_restore`` programs (small Python scripts) and
need no database; the full path runs the real PostgreSQL tools against a throwaway database:
dump, check, restore into a new database, compare the rows.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import URL, Engine, make_url, select, text
from sqlalchemy.orm import Session

from arcilogger_core.admin.contract import (
    AdminLayout,
    AdminPolicy,
    TrustedExecutable,
    dump_document,
)
from arcilogger_core.alerts import read_alerts
from arcilogger_core.config import BackupConfig, Config, load_config
from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db import backup as backup_module
from arcilogger_db import cli as cli_module
from arcilogger_db.audit import append_audit, verify_audit_chain
from arcilogger_db.backup import (
    BACKUP_NAME,
    BackupError,
    BackupFile,
    RestoreRefusedError,
    ServerInfo,
    Tool,
    backup_dir,
    backup_name,
    check_expected_sha256,
    check_restore_target,
    default_versions_root,
    file_sha256,
    find_tool,
    kept_by_retention,
    list_backups,
    password_file,
    prune_backups,
    restore_backup,
    run_backup,
    service_bin_dirs,
    taken_at,
    tool_version,
    versioned_bin_dirs,
)
from arcilogger_db.cli import main
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import Device, Message, Person
from arcilogger_db.testing import TestServer

#: A fake password with the two characters a libpq password file must escape.
PASSWORD = "fake:pass\\word-1"  # gitleaks:allow
NOW = datetime(2026, 10, 18, 22, 40, 2, tzinfo=UTC)  # 2026-10-19 01:40:02 site time

FAKE_TOOL = r"""
import json, os, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
settings = json.loads((HERE / "fake.json").read_text(encoding="utf-8"))
name = Path(__file__).stem
args = sys.argv[1:]
if args == ["--version"]:
    print(f"{name} (PostgreSQL) {settings['version']}")
    raise SystemExit(0)
record = {"tool": name, "args": args, "env_password": "PGPASSWORD" in os.environ}
passfile = os.environ.get("PGPASSFILE")
if passfile:
    record["passfile"] = passfile
    record["passfile_content"] = Path(passfile).read_text(encoding="utf-8")
    record["passfile_mode"] = oct(os.stat(passfile).st_mode & 0o777)
with (HERE / "calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(record) + "\n")
GOOD = b"PGDMP" + b"fake archive block\n" * 64 + b"END"
if name == "pg_dump":
    target = Path(next(a for a in args if a.startswith("--file="))[len("--file="):])
    mode = settings["mode"]
    if mode == "fail":
        print('pg_dump: error: connection to server failed: FATAL:  password authentication '
              'failed for user "arcilogger"', file=sys.stderr)
        raise SystemExit(1)
    if mode == "diskfull":
        target.write_bytes(GOOD[:40])
        print("pg_dump: error: could not write to output file: No space left on device",
              file=sys.stderr)
        raise SystemExit(1)
    target.write_bytes(GOOD[:50] if mode == "truncated" else GOOD)
    raise SystemExit(0)
# pg_restore --list FILE / --file=- FILE / --dbname=NAME FILE
data = Path(args[-1]).read_bytes()
if not (data.startswith(b"PGDMP") and data.endswith(b"END")):
    print("pg_restore: error: could not read from input file: end of file", file=sys.stderr)
    raise SystemExit(1)
if settings["mode"] == "restore-fails" and any(a.startswith("--dbname=") for a in args):
    print("pg_restore: error: could not execute query: ERROR:  out of shared memory",
          file=sys.stderr)
    raise SystemExit(1)
if "--list" in args:
    print(";\n; Archive created at 2026-10-19 01:40:03 +03\n;\n"
          "215; 1259 16390 TABLE public settings arcilogger\n"
          "3001; 0 16390 TABLE DATA public settings arcilogger")
"""


# -- helpers --------------------------------------------------------------------------------------


def _fake_tools(folder: Path, *, mode: str = "ok", version: str = "16.4") -> tuple[Path, Path]:
    """Fake ``pg_dump`` and ``pg_restore`` (Python scripts; a ``.cmd`` launcher on Windows)."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "fake.json").write_text(json.dumps({"mode": mode, "version": version}))
    tools = []
    for name in ("pg_dump", "pg_restore"):
        if os.name == "nt":
            script = folder / f"{name}.py"
            script.write_text(FAKE_TOOL, encoding="utf-8")
            launcher = folder / f"{name}.cmd"
            launcher.write_text(f'@"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
        else:
            launcher = folder / name
            launcher.write_text(f"#!{sys.executable}\n" + FAKE_TOOL, encoding="utf-8")
            launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR)
        tools.append(launcher)
    return tools[0], tools[1]


def _calls(folder: Path) -> list[dict[str, Any]]:
    path = folder / "calls.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _config(
    tmp_path: Path, extra: str = "", *, password: str | None = PASSWORD, database: str = ""
) -> Path:
    tools = tmp_path / "tools"
    pg_dump, pg_restore = (tools / n for n in ("pg_dump", "pg_restore"))
    if os.name == "nt":
        pg_dump, pg_restore = pg_dump.with_suffix(".cmd"), pg_restore.with_suffix(".cmd")
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        "[database]\n"
        'name = "arcilogger"\n'
        'user = "arcilogger"\n' + database + "[backup]\n"
        f'pg_dump = "{pg_dump.as_posix()}"\n'
        f'pg_restore = "{pg_restore.as_posix()}"\n' + extra,
        encoding="utf-8",
    )
    if password is not None:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, password
        )
    return path


def _probe(major: int = 16) -> Any:
    seen: list[str | None] = []

    def probe(config: Config, password: str | None) -> ServerInfo:
        seen.append(password)
        return ServerInfo(f"{major}.4", major, "0007_evidence_seals", None)

    probe.seen = seen  # type: ignore[attr-defined]
    return probe


def _scheduled(tmp_path: Path) -> Path:
    return tmp_path / "data" / "backups" / "scheduled"


def _last(tmp_path: Path) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (tmp_path / "data" / "state" / "backup" / "last.json").read_text(encoding="utf-8")
    )
    return data


def _backup_alerts(tmp_path: Path) -> list[dict[str, Any]]:
    alerts = read_alerts(tmp_path / "data" / "state" / "alerts.jsonl")
    return [alert for alert in alerts if alert["rule"] == "R20-backup"]


def _trust(installation: Path, *roots: Path, recorded: tuple[Path, ...] = ()) -> None:
    """The installation's ``admin\\policy.json``: programs below ``roots`` may run, and those
    ``recorded`` by the installer with their SHA-256 (ADR-0014, ``execpolicy``)."""
    policy = AdminPolicy(
        enabled=True,
        executable_roots=tuple(str(root) for root in roots),
        trusted_executables=tuple(
            TrustedExecutable(path=str(path), sha256=file_sha256(path)[0]) for path in recorded
        ),
        written_at=NOW,
        written_by="installer",
    )
    target = AdminLayout(installation / "admin").policy
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(dump_document(policy))


@pytest.fixture(autouse=True)
def installation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Every test's installation folder is ``tmp_path`` (``default_config_path()``), whose policy
    trusts the programs below it: a configured ``pg_dump`` runs only from a trusted place."""
    monkeypatch.setenv("ARCILOGGER_CONFIG", str(tmp_path / "arcilogger.toml"))
    _trust(tmp_path, tmp_path)
    if os.name == "nt":  # the fake tools are .cmd launchers (_fake_tools)
        monkeypatch.setattr(
            backup_module, "_tool_names", lambda name: (f"{name}.exe", f"{name}.cmd")
        )
    return tmp_path


# -- names, folders, tools ------------------------------------------------------------------------


def test_names_use_site_time_and_round_trip() -> None:
    name = backup_name(NOW)
    assert name == "arcilogger-20261019-014002.dump"
    assert taken_at(name) == datetime(2026, 10, 19, 1, 40, 2, tzinfo=SITE_TZ)
    # The installer's upgrade dumps, sidecars and other files are never "ours".
    for other in (
        "arcilogger-20261019-014002Z-before-0.1.0.dump",
        "arcilogger-20261019-014002.dump.json",
        "arcilogger-20261319-014002.dump",
        "ARCILOGGER-20261019-014002.dump",
        "notes.txt",
    ):
        assert taken_at(other) is None, other


def test_backup_folder_stays_out_of_the_evidence(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path, password=None))
    assert backup_dir(config) == config.data_dir / "backups" / "scheduled"
    for refused in ("vault", "spool/x", "state", "exports", "logs", "."):
        bad = config.model_copy(update={"backup": BackupConfig(dir=Path(refused))})
        with pytest.raises(BackupError, match="must not be"):
            backup_dir(bad)
    outside = config.model_copy(update={"backup": BackupConfig(dir=tmp_path / "elsewhere")})
    assert backup_dir(outside) == tmp_path / "elsewhere"


def test_service_bin_dirs_follow_the_installer() -> None:
    services = {
        "postgresql-x64-15": r'"C:\Program Files\PostgreSQL\15\bin\pg_ctl.exe" runservice '
        r'-N "postgresql-x64-15" -D "C:\Program Files\PostgreSQL\15\data" -w',
        "postgresql-x64-16": r'"C:\Program Files\PostgreSQL\16\bin\pg_ctl.exe" runservice '
        r'-N "postgresql-x64-16" -D "D:\ARCILOGGER\db" -w',
        "postgresqlOther": "C:\\pg\\bin\\pg_ctl.exe runservice -N x",
        "MSSQLSERVER": r'"C:\Program Files\Microsoft SQL Server\sqlservr.exe"',
        "postgresql-broken": "not a pg_ctl command line",
    }
    found = service_bin_dirs(services)
    assert [name for name, _ in found] == [
        "postgresql-x64-16",
        "postgresql-x64-15",
        "postgresqlOther",
    ]
    assert str(found[0][1]) == r"C:\Program Files\PostgreSQL\16\bin"
    assert str(found[2][1]) == r"C:\pg\bin"


def test_find_tool_order(tmp_path: Path) -> None:
    exe = "pg_dump.exe" if os.name == "nt" else "pg_dump"
    service_bin = tmp_path / "service" / "bin"
    versions = tmp_path / "PostgreSQL"
    for folder in (service_bin, versions / "9.6" / "bin", versions / "16" / "bin"):
        folder.mkdir(parents=True)
        (folder / exe).write_text("")
        (folder / exe).chmod(0o755)  # shutil.which wants an executable
    (versions / "17").mkdir()  # no bin folder: skipped
    (versions / "notes").mkdir()
    assert versioned_bin_dirs(versions)[0] == versions / "17" / "bin"
    services = {"postgresql-x64-16": f'"{service_bin / "pg_ctl.exe"}" runservice -N x'}
    configured = tmp_path / "custom" / exe
    configured.parent.mkdir()
    configured.write_text("")
    assert find_tool("pg_dump", str(configured), services=services) == Tool(configured, "config")
    with pytest.raises(BackupError, match="file not found"):
        find_tool("pg_dump", str(tmp_path / "missing"), services=services)
    if os.name == "nt":  # a Windows ImagePath parses only on Windows
        found = find_tool("pg_dump", services=services, versions_root=versions)
        assert found.found_by == "service postgresql-x64-16"
    found = find_tool("pg_dump", services={}, versions_root=versions, search_path="")
    assert found == Tool(versions / "16" / "bin" / exe, "versions folder")
    found = find_tool(
        "pg_dump", services={}, versions_root=tmp_path / "none", search_path=str(service_bin)
    )
    assert found.found_by == "PATH"
    with pytest.raises(BackupError, match=r"set \[backup\] pg_dump"):
        find_tool("pg_dump", services={}, versions_root=tmp_path / "none", search_path="")


def test_a_configured_tool_runs_only_from_a_trusted_place(tmp_path: Path) -> None:
    """ADR-0014 (contract 2.8.4): the administration agent may write ``[backup] pg_dump``, and
    the Backup task runs it as LOCAL SERVICE: only a program below ``executable_roots`` or the
    one the installer recorded at its path runs."""
    trusted, _ = _fake_tools(tmp_path / "tools")
    other, _ = _fake_tools(tmp_path / "other")
    _trust(tmp_path, tmp_path / "tools", recorded=(other,))
    assert find_tool("pg_dump", str(trusted), services={}).path == trusted
    assert find_tool("pg_dump", str(other), services={}).path == other  # recorded with its hash
    copy = tmp_path / "copy" / other.name
    copy.parent.mkdir()
    copy.write_bytes(other.read_bytes())
    with pytest.raises(BackupError, match=r"\[backup\] pg_dump: .*executable_refused"):
        find_tool("pg_dump", str(copy), services={})  # the same bytes elsewhere: refused
    if os.name != "nt":  # a link inside the trusted folder to a program outside it
        link = tmp_path / "tools" / "linked" / copy.name
        link.parent.mkdir()
        link.symlink_to(copy)
        with pytest.raises(BackupError, match="executable_refused"):
            find_tool("pg_dump", str(link), services={})
    # A tool found without configuration (service, versions folder, PATH) is not a free value.
    folder = tmp_path / "versions" / "16" / "bin"
    folder.mkdir(parents=True)
    exe = folder / ("pg_dump.exe" if os.name == "nt" else "pg_dump")
    exe.write_text("")
    assert find_tool("pg_dump", services={}, versions_root=tmp_path / "versions").path == exe


def test_a_configured_tool_must_be_the_tool_itself(tmp_path: Path) -> None:
    """A trusted folder holds other programs too (``C:\\Program Files\\PowerShell\\7\\pwsh.exe``):
    ``[backup] pg_dump`` must name a ``pg_dump`` (execpolicy ``names``), and ``pg_restore`` a
    ``pg_restore``, or nothing runs."""
    pg_dump, pg_restore = _fake_tools(tmp_path / "tools")
    other = pg_dump.with_name(pg_dump.name.replace("pg_dump", "pwsh"))
    other.write_bytes(pg_dump.read_bytes())
    for name, configured in (("pg_dump", other), ("pg_restore", pg_dump), ("pg_dump", pg_restore)):
        with pytest.raises(BackupError, match=rf"\[backup\] {name}: .*executable_refused"):
            find_tool(name, str(configured), services={})
    assert find_tool("pg_restore", str(pg_restore), services={}).path == pg_restore


def test_a_refused_tool_fails_the_backup_with_its_alert(tmp_path: Path) -> None:
    _fake_tools(tmp_path / "tools")
    config = load_config(_config(tmp_path))
    _trust(tmp_path, tmp_path / "elsewhere")  # the configured tools are not trusted
    outcome = run_backup(config, clock=lambda: NOW, probe=_probe())
    assert not outcome.ok
    assert outcome.error is not None
    assert "executable_refused" in outcome.error
    assert _calls(tmp_path / "tools") == []  # nothing ran
    last = _last(tmp_path)
    assert (last["ok"], last["file"]) == (False, None)
    assert "executable_refused" in last["error"]
    (alert,) = _backup_alerts(tmp_path)
    assert alert["severity"] == "high"
    assert list(_scheduled(tmp_path).glob("*.dump")) == []


def test_tool_version(tmp_path: Path) -> None:
    pg_dump, _ = _fake_tools(tmp_path, version="16.4")
    assert tool_version(Tool(pg_dump, "config")) == ("16.4", 16)


def test_password_file_is_private_escaped_and_removed() -> None:
    with password_file({"PATH": "x"}, "arci:user", PASSWORD) as env:
        path = Path(env["PGPASSFILE"])
        assert path.read_text(encoding="utf-8") == "*:*:*:arci\\:user:fake\\:pass\\\\word-1\n"
        if os.name != "nt":
            assert stat.S_IMODE(path.stat().st_mode) == 0o600
            assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
        assert env["PATH"] == "x"
    assert not path.exists()
    assert not path.parent.exists()
    with password_file({}, "arcilogger", None) as env:
        assert "PGPASSFILE" not in env  # trust / SSPI: no file at all
    with pytest.raises(BackupError, match="line break"), password_file({}, "u", "a\nb"):
        pass


# -- retention ------------------------------------------------------------------------------------


def _backup(taken: datetime) -> BackupFile:
    return BackupFile(Path(backup_name(taken)), taken.astimezone(SITE_TZ), 1, None)


def test_retention_keeps_daily_weekly_and_monthly() -> None:
    # One dump a day at 01:40 for 400 days, plus a manual one today.
    start = datetime(2025, 9, 15, 22, 40, tzinfo=UTC)
    backups = [_backup(start + timedelta(days=n)) for n in range(400)]
    newest = backups[-1].taken
    manual = _backup(newest + timedelta(hours=10))
    backups.append(manual)
    kept = {b.taken for b in backups if b.path in kept_by_retention(backups, 14, 8, 12)}
    days = sorted({t.date() for t in kept}, reverse=True)
    # Daily: the 14 newest days (today only once: the newest dump of the day).
    assert manual.taken in kept
    assert newest not in kept
    assert days[:14] == [manual.taken.date() - timedelta(days=n) for n in range(14)]
    # Weekly: the newest of each of the last 8 ISO weeks; monthly: of the last 12 months.
    weeks = {t.isocalendar()[:2] for t in kept}
    months = {(t.year, t.month) for t in kept}
    assert len(weeks) >= 8
    assert len(months) == 12
    assert min(months) == (2025, 11)
    for taken in kept - {t for t in kept if t.date() in days[:14]}:
        same_week = [
            b.taken for b in backups if b.taken.isocalendar()[:2] == taken.isocalendar()[:2]
        ]
        same_month = [
            b.taken for b in backups if (b.taken.year, b.taken.month) == (taken.year, taken.month)
        ]
        assert taken in (max(same_week), max(same_month))
    assert len(kept) == len(set(kept)) <= 14 + 8 + 12
    # Never empty, never the newest: also with nothing but the daily rule.
    assert kept_by_retention(backups, 1, 0, 0) == {manual.path}
    assert kept_by_retention([], 14, 8, 12) == set()


def test_retention_counts_periods_not_the_clock() -> None:
    """A dump from a clock that jumped years ahead deletes nothing early."""
    regular = [_backup(NOW - timedelta(days=n)) for n in range(5)]
    future = _backup(NOW + timedelta(days=3650))
    kept = kept_by_retention([*regular, future], 3, 0, 0)
    assert kept == {future.path, regular[0].path, regular[1].path}


@pytest.mark.parametrize("back", [timedelta(days=1, seconds=5), timedelta(days=600)])
def test_retention_keeps_the_dump_just_made_after_the_clock_went_back(back: timedelta) -> None:
    """A clock set back names the new dump older than the ones already there: every period of
    its name is taken by a newer-named dump, but it is the dump last.json points at."""
    start = NOW - timedelta(days=399)
    existing = [_backup(start + timedelta(days=n)) for n in range(400)]
    new = _backup(NOW - back)
    backups = [*existing, new]
    assert new.path not in kept_by_retention(backups, 14, 8, 12)  # without protection: deleted
    kept = kept_by_retention(backups, 14, 8, 12, protect={new.path})
    assert new.path in kept
    assert kept - {new.path} == kept_by_retention(backups, 14, 8, 12)  # nothing else changes
    # A path that is not one of the listed dumps is not "kept".
    assert kept_by_retention(existing, 1, 0, 0, protect={Path("elsewhere.dump")}) == {
        existing[-1].path
    }


def test_prune_deletes_only_scheduled_dumps(tmp_path: Path) -> None:
    folder = tmp_path / "scheduled"
    folder.mkdir()
    names = [backup_name(NOW - timedelta(days=n)) for n in range(4)]
    for name in names:
        (folder / name).write_bytes(b"dump")
        (folder / f"{name}.json").write_text("{}")
    keep_out = [
        "arcilogger-20261001-000000Z-before-0.1.0.dump",  # an installer dump copied here
        "notes.txt",
        "arcilogger-20200101-000000.dump.bak",
    ]
    for name in keep_out:
        (folder / name).write_text("x")
    (folder / "arcilogger-20200101-000000.dump").mkdir()  # a folder with our name
    sub = folder / "db"
    sub.mkdir()
    (sub / names[0]).write_text("x")  # subfolders are never looked into
    orphan = folder / "arcilogger-20200101-000000.dump.json"
    orphan.write_text("{}")
    stale = folder / f".{backup_name(NOW - timedelta(days=9))}.partial"
    stale.write_bytes(b"half")
    old = (NOW - timedelta(days=2)).timestamp()
    os.utime(stale, (old, old))
    fresh = folder / f".{backup_name(NOW)}.partial"  # maybe a run in progress
    fresh.write_bytes(b"half")
    settings = BackupConfig(keep_daily=2, keep_weekly=0, keep_monthly=0)

    preview = prune_backups(folder, settings, now=NOW, dry_run=True)
    assert [p.name for p in preview.deleted] == names[2:]
    assert sorted(p.name for p in preview.cleaned) == sorted([orphan.name, stale.name])
    assert all((folder / n).exists() for n in names)  # a dry run changes nothing

    result = prune_backups(folder, settings, now=NOW)
    assert [p.name for p in result.kept] == names[:2]
    assert sorted(p.name for p in folder.iterdir()) == sorted(
        [
            *names[:2],
            *(f"{n}.json" for n in names[:2]),
            *keep_out,
            "arcilogger-20200101-000000.dump",
            "db",
            fresh.name,
        ]
    )
    assert (sub / names[0]).exists()


# -- run with fake tools --------------------------------------------------------------------------


def test_run_writes_dump_sidecar_and_state(tmp_path: Path) -> None:
    _fake_tools(tmp_path / "tools")
    config = load_config(_config(tmp_path))
    probe = _probe()
    outcome = run_backup(config, clock=lambda: NOW, probe=probe)
    assert outcome.ok, outcome.error
    dump = _scheduled(tmp_path) / "arcilogger-20261019-014002.dump"
    assert outcome.file == dump
    assert probe.seen == [PASSWORD]  # the stored database password
    # last.json: exactly the contract of MVP-CONTRACTS section 9.
    last = _last(tmp_path)
    assert last == {
        "schema": 1,
        "finished": "2026-10-19T01:40:02+03:00",
        "ok": True,
        "file": "backups/scheduled/arcilogger-20261019-014002.dump",
        "size": dump.stat().st_size,
        "sha256": outcome.sha256,
        "error": None,
    }
    assert list(last) == ["schema", "finished", "ok", "file", "size", "sha256", "error"]
    sidecar = json.loads((dump.parent / f"{dump.name}.json").read_text(encoding="utf-8"))
    assert sidecar["sha256"] == outcome.sha256
    assert sidecar["size"] == dump.stat().st_size
    assert sidecar["pg_dump_version"] == "16.4"
    assert sidecar["server_version"] == "16.4"
    assert sidecar["alembic_revision"] == "0007_evidence_seals"
    assert sidecar["timescaledb_version"] is None
    assert sidecar["toc_entries"] == 2
    assert {"started", "finished", "schema"} <= set(sidecar)
    assert _backup_alerts(tmp_path) == []
    # Only the final dump and its sidecar are left; nothing temporary.
    assert sorted(p.name for p in dump.parent.iterdir()) == [dump.name, f"{dump.name}.json"]
    # The password reached pg_dump only through the password file, which is gone again.
    (call,) = [c for c in _calls(tmp_path / "tools") if c["tool"] == "pg_dump"]
    assert all(PASSWORD not in argument for argument in call["args"])
    assert call["env_password"] is False
    assert call["passfile_content"] == "*:*:*:arcilogger:fake\\:pass\\\\word-1\n"
    if os.name != "nt":
        assert call["passfile_mode"] == "0o600"
    assert not Path(call["passfile"]).exists()
    assert call["args"][-1] == "--dbname=arcilogger"
    assert "--format=custom" in call["args"]
    assert "--no-password" in call["args"]
    assert PASSWORD not in (tmp_path / "data" / "state" / "backup" / "last.json").read_text()


@pytest.mark.parametrize(
    ("mode", "error"),
    [
        ("fail", "pg_dump failed (exit code 1): pg_dump: error: connection to server failed"),
        ("truncated", "pg_restore --list failed (exit code 1)"),
        ("diskfull", "No space left on device"),
    ],
)
def test_failed_run_records_state_and_alert(tmp_path: Path, mode: str, error: str) -> None:
    _fake_tools(tmp_path / "tools", mode=mode)
    config = load_config(_config(tmp_path))
    earlier = _scheduled(tmp_path) / backup_name(NOW - timedelta(days=1))
    earlier.parent.mkdir(parents=True)
    earlier.write_bytes(b"an older dump")
    outcome = run_backup(config, clock=lambda: NOW, probe=_probe())
    assert outcome.ok is False
    assert error in (outcome.error or "")
    last = _last(tmp_path)
    assert last["ok"] is False
    assert last["file"] is None
    assert last["size"] is None
    assert last["sha256"] is None
    assert error in last["error"]
    (alert,) = _backup_alerts(tmp_path)
    assert alert["severity"] == "high"
    assert alert["message"].startswith("Veritabanı yedeği alınamadı [database backup failed]:")
    assert error in alert["details"]["error"]
    # No partial or unchecked dump is left; the earlier dump is untouched (no retention run).
    assert sorted(p.name for p in earlier.parent.iterdir()) == [earlier.name]
    assert PASSWORD not in json.dumps(last) + json.dumps(alert)


def test_a_state_file_that_cannot_be_written_keeps_the_dump(tmp_path: Path) -> None:
    _fake_tools(tmp_path / "tools")
    config = load_config(_config(tmp_path))
    blocked = tmp_path / "data" / "state" / "backup"
    blocked.parent.mkdir(parents=True)
    blocked.write_text("not a folder")
    outcome = run_backup(config, clock=lambda: NOW, probe=_probe())
    assert not outcome.ok
    assert "last.json was not written" in (outcome.error or "")
    assert outcome.file is not None
    assert outcome.file.is_file()  # a checked dump is never thrown away for the state file
    (alert,) = _backup_alerts(tmp_path)
    assert alert["severity"] == "high"
    assert "durumu yazılamadı" in alert["message"]
    # Under R20-backup itself (no key): the worker resolves it only after a later good backup.
    assert "key" not in alert["details"]


def test_refused_without_free_space(tmp_path: Path) -> None:
    _fake_tools(tmp_path / "tools")
    config = load_config(_config(tmp_path, "min_free_gb = 2\n"))
    folder = _scheduled(tmp_path)
    folder.mkdir(parents=True)
    (folder / backup_name(NOW - timedelta(days=1))).write_bytes(b"x" * 1000)

    class Usage:
        free = 2 * 1024**3 + 999  # less than 2 GiB plus the newest dump (1000 bytes)

    outcome = run_backup(config, clock=lambda: NOW, probe=_probe(), disk_usage=lambda _: Usage)
    assert not outcome.ok
    assert "not enough free space" in (outcome.error or "")
    assert "min_free_gb = 2" in _last(tmp_path)["error"]
    assert len(_backup_alerts(tmp_path)) == 1
    assert _calls(tmp_path / "tools") == []  # pg_dump never started


def test_refuses_a_pg_dump_older_than_the_server(tmp_path: Path) -> None:
    _fake_tools(tmp_path / "tools", version="14.12")
    config = load_config(_config(tmp_path))
    outcome = run_backup(config, clock=lambda: NOW, probe=_probe(16))
    assert not outcome.ok
    assert "older than the server" in (outcome.error or "")
    assert _calls(tmp_path / "tools") == []


def test_run_prunes_after_a_successful_backup(tmp_path: Path) -> None:
    _fake_tools(tmp_path / "tools")
    config = load_config(_config(tmp_path, "keep_daily = 2\nkeep_weekly = 0\nkeep_monthly = 0\n"))
    folder = _scheduled(tmp_path)
    folder.mkdir(parents=True)
    old = [folder / backup_name(NOW - timedelta(days=n)) for n in (1, 2, 3)]
    for path in old:
        path.write_bytes(b"older")
    outcome = run_backup(config, clock=lambda: NOW, probe=_probe())
    assert outcome.ok
    assert outcome.pruned is not None
    assert [p.name for p in outcome.pruned.deleted] == [old[1].name, old[2].name]
    assert [b.path for b in list_backups(folder)] == [outcome.file, old[0]]
    assert _backup_alerts(tmp_path) == []  # an ordinary run: no clock alert


def test_run_keeps_its_dump_when_the_clock_was_set_back(tmp_path: Path) -> None:
    """The dumps already there are named later than the new one (the clock went back a day):
    the new dump, which last.json points at, is kept, and R20-backup:clock says why."""
    _fake_tools(tmp_path / "tools")
    config = load_config(_config(tmp_path, "keep_daily = 2\nkeep_weekly = 0\nkeep_monthly = 0\n"))
    folder = _scheduled(tmp_path)
    folder.mkdir(parents=True)
    later = [folder / backup_name(NOW + timedelta(days=n, seconds=5)) for n in (0, 1, 2)]
    for path in later:
        path.write_bytes(b"made before the clock went back")
    outcome = run_backup(config, clock=lambda: NOW, probe=_probe())
    assert outcome.ok
    assert outcome.file is not None
    assert outcome.file.is_file()
    assert (config.data_dir / _last(tmp_path)["file"]) == outcome.file
    assert outcome.pruned is not None
    assert outcome.file not in outcome.pruned.deleted
    assert [p.name for p in outcome.pruned.deleted] == [later[0].name]
    (alert,) = _backup_alerts(tmp_path)
    assert alert["severity"] == "medium"
    assert alert["details"]["key"] == "clock"
    assert alert["details"]["later"] == later[2].name
    assert alert["details"]["file"] == _last(tmp_path)["file"]


def test_a_failed_retention_keeps_the_dump_and_has_its_own_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Old dumps that cannot be deleted: exit code 1, but last.json says ok and the new dump
    exists; the alert has its own key (R20-backup:prune), which a good last.json does not
    resolve."""
    _fake_tools(tmp_path / "tools")
    config_path = _config(tmp_path)

    def refuse(*args: Any, **kwargs: Any) -> Any:
        raise OSError(13, "Access is denied")

    monkeypatch.setattr(backup_module, "prune_backups", refuse)
    monkeypatch.setattr(
        cli_module,
        "run_backup",
        lambda config, **kwargs: run_backup(config, clock=lambda: NOW, probe=_probe(), **kwargs),
    )
    assert main(["backup", "run", "--config", str(config_path)]) == 1
    captured = capsys.readouterr()
    assert "Yedek [backup]:" in captured.out
    assert "retention failed: Access is denied" in captured.err
    last = _last(tmp_path)
    assert last["ok"] is True
    assert (tmp_path / "data" / last["file"]).is_file()
    (alert,) = _backup_alerts(tmp_path)
    assert alert["severity"] == "medium"
    assert alert["details"]["key"] == "prune"
    assert alert["details"]["error"] == "retention failed: Access is denied"


def test_a_retention_failure_raises_its_own_medium_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backup itself is fine (last.json ok): the alert has its own key, ``prune``, so the
    worker never resolves it because of that fine backup."""
    _fake_tools(tmp_path / "tools")
    config = load_config(_config(tmp_path))

    def refuse(*args: Any, **kwargs: Any) -> None:
        raise PermissionError(13, "Access is denied", "arcilogger-20261001-014002.dump")

    monkeypatch.setattr("arcilogger_db.backup.prune_backups", refuse)
    outcome = run_backup(config, clock=lambda: NOW, probe=_probe())
    assert outcome.ok
    assert outcome.prune_error == "retention failed: Access is denied"
    assert _last(tmp_path)["ok"] is True
    (alert,) = _backup_alerts(tmp_path)
    assert (alert["severity"], alert["details"]["key"]) == ("medium", "prune")
    assert alert["details"]["error"] == outcome.prune_error


def test_cli_run_disabled(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    _fake_tools(tmp_path / "tools")
    config = _config(tmp_path, "enabled = false\n")
    assert main(["backup", "run", "--config", str(config)]) == 0
    assert "kapalı" in capsys.readouterr().out
    assert not (tmp_path / "data" / "state" / "backup" / "last.json").exists()
    assert _calls(tmp_path / "tools") == []


def test_cli_run_refuses_a_configuration_that_moves_its_folders(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0014 (contract Day 0 notes D0-2, WP2-1): the administration agent may write
    ``arcilogger.toml``. The Backup task runs only with ``secrets_dir`` in the folder of its
    ``--config`` file (it chooses the database password) and ``data_dir`` at the data root the
    installer recorded; nothing runs and nothing is written otherwise."""
    _fake_tools(tmp_path / "tools")
    config = _config(
        tmp_path, database='host = "127.0.0.1"\nport = 1\nconnect_timeout_seconds = 1\n'
    )
    text = config.read_text(encoding="utf-8")
    config.write_text(f'secrets_dir = "{(tmp_path / "elsewhere").as_posix()}"\n' + text)
    assert main(["backup", "run", "--config", str(config)]) == 2
    assert "secrets_dir" in capsys.readouterr().err
    config.write_text(text, encoding="utf-8")
    record = tmp_path / "data-root.txt"
    data = (tmp_path / "data").as_posix()
    for content, named in (
        (f"{(tmp_path / 'other').as_posix()}\n".encode(), "data_dir"),
        (b"relative/data\n", "data-root.txt"),
        (data.encode("utf-16"), "data-root.txt"),  # a record that cannot be used fails closed
    ):
        record.write_bytes(content)
        assert main(["backup", "run", "--config", str(config)]) == 2
        assert named in capsys.readouterr().err
    assert _calls(tmp_path / "tools") == []
    assert not (tmp_path / "data").exists()  # no log, no last.json, no alert
    record.write_text(f"\ufeff{data}\n", encoding="utf-8")
    assert main(["backup", "run", "--config", str(config)]) == 1  # runs (no database here)
    assert _last(tmp_path)["ok"] is False


def test_cli_run_without_a_database(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """Nothing listens on the port: exit code 1, recorded in last.json, alerted, logged."""
    _fake_tools(tmp_path / "tools")
    database = 'host = "127.0.0.1"\nport = 1\nconnect_timeout_seconds = 1\n'
    config = _config(tmp_path, database=database)
    assert main(["backup", "run", "--config", str(config)]) == 1
    assert "Veritabanı yedeği alınamadı [database backup failed]" in capsys.readouterr().err
    last = _last(tmp_path)
    assert last["ok"] is False
    assert last["error"]
    assert PASSWORD not in last["error"]
    assert len(_backup_alerts(tmp_path)) == 1
    log = (tmp_path / "data" / "logs" / "backup.log").read_text(encoding="utf-8")
    assert "backup failed" in log
    assert PASSWORD not in log


def test_cli_list_verify_and_prune_without_a_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _fake_tools(tmp_path / "tools")
    config = _config(tmp_path, "keep_daily = 1\nkeep_weekly = 0\nkeep_monthly = 0\n")
    assert main(["backup", "verify", "--config", str(config)]) == 1  # nothing to verify yet
    assert "Doğrulanacak yedek yok" in capsys.readouterr().err
    assert run_backup(load_config(config), clock=lambda: NOW, probe=_probe()).ok
    older = _scheduled(tmp_path) / backup_name(NOW - timedelta(days=1))
    older.write_bytes(b"PGDMP" + b"x" * 10 + b"END")
    assert main(["backup", "list", "--json", "--config", str(config)]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [row["sidecar"] for row in listing["backups"]] == [True, False]
    assert listing["last"]["ok"] is True
    assert main(["backup", "verify", "--all", "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert out.count("OK  ") == 2
    assert "no sidecar" in out
    assert main(["backup", "prune", "--dry-run", "--config", str(config)]) == 0
    assert older.exists()
    assert main(["backup", "prune", "--config", str(config)]) == 0
    assert f"silindi [deleted]: {older.name}" in capsys.readouterr().out
    assert not older.exists()
    bad = _config(tmp_path, 'dir = "vault/backups"\n', password=None)
    assert main(["backup", "list", "--config", str(bad)]) == 1
    assert "must not be inside vault/" in capsys.readouterr().err


def test_cli_prune_keeps_the_dump_last_json_points_at(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A dump named later than the last good run's (made before the clock was set back) is
    the newest by name; a manual prune still keeps the dump last.json points at."""
    _fake_tools(tmp_path / "tools")
    config = _config(tmp_path, "keep_daily = 1\nkeep_weekly = 0\nkeep_monthly = 0\n")
    assert run_backup(load_config(config), clock=lambda: NOW, probe=_probe()).ok
    made = _scheduled(tmp_path) / backup_name(NOW)
    later = _scheduled(tmp_path) / backup_name(NOW + timedelta(days=1))
    earlier = _scheduled(tmp_path) / backup_name(NOW - timedelta(days=1))
    for path in (later, earlier):
        path.write_bytes(b"PGDMP" + b"x" * 10 + b"END")
    assert main(["backup", "prune", "--config", str(config)]) == 0
    assert f"silindi [deleted]: {earlier.name}" in capsys.readouterr().out
    assert made.exists()
    assert later.exists()
    assert not earlier.exists()
    # Without a good last.json only the retention's own rule applies.
    (tmp_path / "data" / "state" / "backup" / "last.json").write_text(
        json.dumps({"schema": 1, "ok": False, "file": None}), encoding="utf-8"
    )
    assert main(["backup", "prune", "--config", str(config)]) == 0
    assert not made.exists()
    assert later.exists()


def test_restore_refuses_the_live_and_invalid_names(tmp_path: Path) -> None:
    config = load_config(_config(tmp_path, password=None))
    for name in ("arcilogger", "postgres", "template1", "Arci", "a-b", "x" * 64, ""):
        with pytest.raises(RestoreRefusedError):
            check_restore_target(config, name)
    assert check_restore_target(config, "arcilogger_restore") == "arcilogger_restore"


def test_expected_sha256_is_64_hex_digits() -> None:
    digest = "ab" * 32
    assert check_expected_sha256(None) is None
    assert check_expected_sha256(digest.upper()) == digest  # as Get-FileHash prints it
    assert check_expected_sha256(f" {digest}\n") == digest
    for bad in ("", "  ", digest[:-1], digest + "0", "g" * 64, f"sha256:{digest}"):
        with pytest.raises(RestoreRefusedError, match="64 hexadecimal"):
            check_expected_sha256(bad)


def test_restore_refuses_a_dump_that_differs_from_sha256(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A dump replaced together with its sidecar passes the sidecar check; ``--sha256`` (the
    hash recorded off the server) refuses it before the tool or the database is touched."""
    _fake_tools(tmp_path / "tools")
    config = load_config(_config(tmp_path, password=None))
    assert run_backup(config, clock=lambda: NOW, probe=_probe()).ok
    dump = _scheduled(tmp_path) / backup_name(NOW)
    recorded, _ = file_sha256(dump)
    forged = b"PGDMP" + b"forged entry\n" * 8 + b"END"
    dump.write_bytes(forged)
    sidecar = dump.with_name(dump.name + ".json")
    meta = json.loads(sidecar.read_text(encoding="utf-8"))
    meta.update(sha256=file_sha256(dump)[0], size=len(forged))
    sidecar.write_text(json.dumps(meta), encoding="utf-8")
    calls = len(_calls(tmp_path / "tools"))
    with pytest.raises(RestoreRefusedError, match="differs from --sha256"):
        restore_backup(
            config, dump, "arci_new", superuser="postgres", password=None, sha256=recorded
        )
    assert len(_calls(tmp_path / "tools")) == calls  # pg_restore never started
    # The command line: refused (exit 2) before the password is asked for, also for a value
    # that is not a SHA-256 at all.
    unread = io.StringIO("never read\n")
    monkeypatch.setattr(sys, "stdin", unread)
    command = ["backup", "restore", "--file", str(dump), "--target-db", "arci_new"]
    config_arg = ["--config", str(tmp_path / "arcilogger.toml")]
    assert main([*command, "--sha256", "not-a-hash", *config_arg]) == 2
    assert "64 hexadecimal" in capsys.readouterr().err
    assert unread.tell() == 0
    assert main([*command, "--sha256", recorded.upper(), *config_arg]) == 2
    err = capsys.readouterr().err
    assert "hiçbir şey değiştirilmedi" in err
    assert "differs from --sha256" in err


@pytest.mark.parametrize(
    ("given", "sidecar", "said"),
    [
        (True, True, "(--sha256 ile aynı [matches --sha256])"),
        (False, True, "[no --sha256: checked against the sidecar only, for accidental damage]"),
        (False, False, "[no --sha256, no sidecar: not checked]"),
    ],
)
def test_restore_output_says_what_the_dump_was_checked_against(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    given: bool,
    sidecar: bool,
    said: str,
) -> None:
    config = _config(tmp_path, password=None)
    digest = "ab" * 32

    def restored(*args: Any, **kwargs: Any) -> backup_module.RestoreOutcome:
        assert kwargs["sha256"] == (digest if given else None)
        return backup_module.RestoreOutcome(
            database="arci_new",
            tables=40,
            revision=None,
            expected_revision=None,
            timescaledb=False,
            sha256=digest,
            sha256_given=given,
            sidecar=sidecar,
        )

    monkeypatch.setattr(cli_module, "restore_backup", restored)
    monkeypatch.setattr(sys, "stdin", io.StringIO("\n"))
    extra = ["--sha256", digest.upper()] if given else []
    command = ["backup", "restore", "--file", str(tmp_path / "x.dump"), "--target-db", "arci_new"]
    assert main([*command, *extra, "--config", str(config)]) == 0
    out = capsys.readouterr().out
    assert f"SHA-256 {digest} " in out
    assert said in out


# -- the real tools and a real database -----------------------------------------------------------


def _real_tool(name: str, server_major: int) -> Path:
    """A real PostgreSQL tool, the server's major version preferred, else a newer one: ``PGBIN``
    (Windows runner image), ``<versions>/<n>/bin`` (``/usr/lib/postgresql``,
    ``C:\\Program Files\\PostgreSQL``), ``PATH``."""
    exe = f"{name}.exe" if os.name == "nt" else name
    candidates = [Path(os.environ["PGBIN"]) / exe] if os.environ.get("PGBIN") else []
    candidates += [folder / exe for folder in versioned_bin_dirs(default_versions_root())]
    with contextlib.suppress(BackupError):
        candidates.append(find_tool(name, services={}, versions_root=Path("-")).path)
    usable = []
    for path in candidates:
        if path.is_file():
            _, major = tool_version(Tool(path, "test"))
            if major == server_major:
                return path
            if major > server_major:
                usable.append(path)
    if not usable:
        pytest.skip(f"no {name} >= {server_major} on this machine")
    return usable[0]


@dataclass(frozen=True)
class Live:
    config: Path
    engine: Engine
    url: URL

    def restore(
        self, dump: Path, target: str, *extra: str, config: Path | None = None
    ) -> list[str]:
        return [
            "backup",
            "restore",
            "--file",
            str(dump),
            "--target-db",
            target,
            "--superuser",
            str(self.url.username),
            *extra,
            "--config",
            str(config or self.config),
        ]

    def password_on_stdin(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO(f"{self.url.password or ''}\n"))


@pytest.fixture
def live(pg_dsn: str, tmp_path: Path) -> Iterator[Live]:
    """A migrated database with rows, its configuration (the real tools) and an engine."""
    url = make_url(pg_dsn)
    probe = engine_from_url(pg_dsn)
    try:
        with probe.connect() as connection:
            number = connection.execute(text("SHOW server_version_num")).scalar_one()
    finally:
        probe.dispose()
    pg_dump, pg_restore = (_real_tool(name, int(number) // 10000) for name in TOOLS)
    _trust(tmp_path, tmp_path, pg_dump.parent, pg_restore.parent)  # the real tools may run
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n'
        "[database]\n"
        f'host = "{url.host or "127.0.0.1"}"\n'
        f"port = {url.port or 5432}\n"
        f'name = "{url.database}"\n'
        f'user = "{url.username}"\n'
        "[backup]\n"
        f'pg_dump = "{pg_dump.as_posix()}"\n'
        f'pg_restore = "{pg_restore.as_posix()}"\n',
        encoding="utf-8",
    )
    if url.password:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, str(url.password)
        )
    engine = engine_from_url(pg_dsn)
    with Session(engine) as session, session.begin():
        people = [Person(name=f"Kişi {n}", employee_no=f"E{n}") for n in range(3)]
        session.add_all(people)
        session.flush()
        session.add_all(
            [
                Device(mac="aa:bb:cc:00:00:01", name="Kişi 0 PC", person_id=people[0].id),
                Device(mac="aa:bb:cc:00:00:02", hostnames=["yazici"]),
            ]
        )
        now = datetime.now(UTC)
        session.add_all(
            Message(
                time=now - timedelta(minutes=n),
                time_received=now,
                source="fortigate",
                sender="172.31.255.1",
                type="traffic",
                text=f"satır {n}",
                raw_day=now.astimezone(SITE_TZ).date(),
                raw_line=n + 1,
                raw_offset=n * 100,
            )
            for n in range(50)
        )
        for n in range(3):
            append_audit(session, action="test.backup", target=f"row:{n}")
    yield Live(path, engine, url)
    engine.dispose()


TOOLS = ("pg_dump", "pg_restore")
COUNTED = ("persons", "devices", "messages", "audit_log", "settings", "vlans", "departments")


def _counts(engine: Engine) -> dict[str, int]:
    with engine.connect() as connection:
        return {
            table: int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608
            for table in COUNTED
        }


def test_backup_and_restore_with_postgresql(
    live: Live,
    pg_server: TestServer,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = load_config(live.config)
    assert main(["backup", "run", "--config", str(live.config)]) == 0
    assert "Yedek [backup]:" in capsys.readouterr().out
    last = json.loads((config.data_dir / "state" / "backup" / "last.json").read_text())
    assert last["ok"] is True
    assert last["error"] is None
    dump = config.data_dir / last["file"]
    assert BACKUP_NAME.fullmatch(dump.name)
    sidecar = json.loads(dump.with_name(dump.name + ".json").read_text(encoding="utf-8"))
    assert sidecar["alembic_revision"]
    assert sidecar["sha256"] == last["sha256"]
    assert sidecar["size"] == last["size"] == dump.stat().st_size

    assert main(["backup", "list", "--json", "--config", str(live.config)]) == 0
    listing = json.loads(capsys.readouterr().out)
    assert [row["file"] for row in listing["backups"]] == [dump.name]
    assert listing["last"] == last
    assert main(["backup", "verify", "--deep", "--config", str(live.config)]) == 0
    assert "all data read" in capsys.readouterr().out

    source = _counts(live.engine)
    assert source["persons"] == 3
    assert source["messages"] == 50
    assert source["audit_log"] >= 3
    target = pg_server.new_name("t")
    try:
        # Refused before anything changes: the live database and a system database.
        for refused in (str(live.url.database), "postgres"):
            live.password_on_stdin(monkeypatch)
            assert main(live.restore(dump, refused)) == 2, refused
        capsys.readouterr()
        live.password_on_stdin(monkeypatch)
        # The hash as Get-FileHash prints it (upper case), recorded off the server.
        code = main(live.restore(dump, target, "--sha256", last["sha256"].upper()))
        out = capsys.readouterr().out
        assert code == 0, out
        assert f"SHA-256 {last['sha256']} (--sha256 ile aynı [matches --sha256])" in out
        assert f'[database] name = "{target}"' in out
        assert "Stop-Service ArciWorker, ArciApi, ArciIngest" in out
        if live.url.password:
            assert live.url.password not in out
        restored = engine_from_url(live.url.set(database=target))
        try:
            assert _counts(restored) == source
            with Session(restored) as session:
                # The C locale like the installer's database, not the cluster's default
                # (C.UTF-8 on the CI cluster, a Windows locale on some servers).
                locale = session.execute(
                    text(
                        "SELECT datcollate, datctype FROM pg_database "
                        "WHERE datname = current_database()"
                    )
                ).one()
                assert tuple(locale) == ("C", "C")
                chain = verify_audit_chain(session)
                assert chain.ok
                assert chain.checked == source["audit_log"]
                names = session.scalars(select(Person.name).order_by(Person.id)).all()
                assert names == ["Kişi 0", "Kişi 1", "Kişi 2"]
                owner = session.execute(
                    text(
                        "SELECT pg_get_userbyid(datdba) FROM pg_database "
                        "WHERE datname = current_database()"
                    )
                ).scalar_one()
                assert owner == live.url.username  # [database] user owns the new database
        finally:
            restored.dispose()
        # A second restore into the same name is refused: it exists now.
        live.password_on_stdin(monkeypatch)
        assert main(live.restore(dump, target)) == 2
        assert "already exists" in capsys.readouterr().err
    finally:
        pg_server.drop_database(target)
    assert _counts(live.engine) == source  # the live database is untouched


def test_restore_refuses_a_changed_dump(
    live: Live, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main(["backup", "run", "--config", str(live.config)]) == 0
    dump = next((load_config(live.config).data_dir / "backups" / "scheduled").glob("*.dump"))
    with dump.open("ab") as handle:
        handle.write(b"tampered")
    capsys.readouterr()
    assert main(["backup", "verify", "--config", str(live.config)]) == 1
    assert "SHA-256 differs" in capsys.readouterr().out
    live.password_on_stdin(monkeypatch)
    assert main(live.restore(dump, "arci_never_created")) == 2
    err = capsys.readouterr().err
    assert "differs from its sidecar (damaged or incomplete)" in err
    assert "changed" not in err.split("]:", 1)[1]  # no claim that it detects deliberate changes
    with live.engine.connect() as connection:
        created = connection.execute(
            text("SELECT 1 FROM pg_database WHERE datname = 'arci_never_created'")
        ).scalar_one_or_none()
    assert created is None


def test_a_failed_restore_stops_and_says_how_to_drop_the_new_database(
    live: Live,
    pg_server: TestServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``pg_restore`` runs one transaction per object (``--exit-on-error``), not one for the
    whole archive, whose locks a TimescaleDB database outgrows; a failure leaves the new
    database incomplete, and the error names the DROP DATABASE."""
    tools = tmp_path / "fake"
    _, fake_restore = _fake_tools(tools, mode="restore-fails")
    config = tmp_path / "fake-restore.toml"
    text_ = live.config.read_text(encoding="utf-8")
    config.write_text(
        "\n".join(
            f'pg_restore = "{fake_restore.as_posix()}"' if line.startswith("pg_restore =") else line
            for line in text_.splitlines()
        )
        + "\n",
        encoding="utf-8",
    )
    dump = tmp_path / "manual.dump"  # no sidecar, like a pre-upgrade dump
    dump.write_bytes(b"PGDMP" + b"fake archive block\n" * 4 + b"END")
    target = pg_server.new_name("f")
    try:
        live.password_on_stdin(monkeypatch)
        assert main(live.restore(dump, target, config=config)) == 1
        err = capsys.readouterr().err
        assert "out of shared memory" in err
        assert (
            f"the new database {target} is incomplete: drop it with DROP DATABASE {target}" in err
        )
        with live.engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": target}
            ).scalar_one_or_none()
        assert exists == 1
        (call,) = [c for c in _calls(tools) if any(a.startswith("--dbname=") for a in c["args"])]
        assert "--exit-on-error" in call["args"]
        assert "--single-transaction" not in call["args"]
        assert call["args"][-1] == str(dump)
    finally:
        pg_server.drop_database(target)
