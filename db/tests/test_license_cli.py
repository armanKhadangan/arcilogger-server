"""``arcilogger-db license …`` (licensing contract 6.8, 4.4; test B-T5).

Each command with its text and ``--json`` output, exit codes 0/1/2/3, the status file, the
licence events and the audit entries with ``os_user``; ``license_unavailable`` (exit 1) when the
licence code cannot be imported, while ``upgrade`` and every other command never load it.
"""

from __future__ import annotations

import getpass
import io
import json
import subprocess
import sys
from collections.abc import Iterator
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import arcilogger_db
from arcilogger_core.admin.contract import AdminLayout, AdminPolicy, dump_document
from arcilogger_core.config import load_config
from arcilogger_core.license.request import parse_request_code
from arcilogger_core.license.testing import fixed_identity, make_license, tamper, trusted_test_keys
from arcilogger_core.license.trusted_time import clear_cache
from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_core.timeutil import utc_now
from arcilogger_db.backup import BackupError, find_tool
from arcilogger_db.cli import main
from arcilogger_db.engine import make_engine
from arcilogger_db.models import AuditLog, LicenseEvent
from arcilogger_db.testing import TestServer

pytestmark = pytest.mark.license_live

CONFIG = """
data_dir = "{data}"
site_name = "{site}"

[database]
host = "{host}"
port = {port}
name = "{name}"
user = "{user}"
connect_timeout_seconds = 5
"""


def _write_config(
    tmp_path: Path, url: Any, name: str, *, port: int | None = None, site: str = "Örnek A.Ş."
) -> Path:
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        CONFIG.format(
            data=(tmp_path / "data").as_posix(),
            site=site,
            host=url.host or "127.0.0.1",
            port=port or url.port or 5432,
            name=name,
            user=url.username,
        ),
        encoding="utf-8",
    )
    if url.password:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, str(url.password)
        )
    return path


@pytest.fixture
def setup(pg_server: TestServer, tmp_path: Path) -> Iterator[Path]:
    """Config file, secret store and a migrated database."""
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    path = _write_config(tmp_path, pg_server.admin_url, name)
    try:
        assert main(["upgrade", "--config", str(path)]) == 0
        yield path
    finally:
        pg_server.drop_database(name)


@pytest.fixture
def server_id() -> Iterator[str]:
    clear_cache()
    with fixed_identity() as value, trusted_test_keys():
        yield value
    clear_cache()


def _session(path: Path) -> Any:
    engine = make_engine(load_config(path))
    return engine, Session(engine)


def _events(path: Path) -> list[LicenseEvent]:
    engine, session = _session(path)
    try:
        return list(session.scalars(select(LicenseEvent).order_by(LicenseEvent.id)))
    finally:
        session.close()
        engine.dispose()


def _audits(path: Path, action: str) -> list[AuditLog]:
    engine, session = _session(path)
    try:
        return list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
    finally:
        session.close()
        engine.dispose()


def _status_file(tmp_path: Path) -> dict[str, Any]:
    document: dict[str, Any] = json.loads(
        (tmp_path / "data" / "state" / "license-status.json").read_text(encoding="utf-8")
    )
    return document


def test_status_and_request_code(
    setup: Path, server_id: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup)
    assert main(["license", "status", "--config", config]) == 0
    out = capsys.readouterr().out
    assert "Lisans durumu [licence state]: Deneme sürümü [trial]" in out
    assert server_id in out
    assert _status_file(tmp_path)["status"] == "trial"
    assert main(["license", "status", "--json", "--config", config]) == 0
    document = json.loads(capsys.readouterr().out)
    assert document["status"] == "trial"
    assert document["details"]["server_id"] == server_id
    assert main(["license", "request-code", "--config", config]) == 0
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1  # exactly the code
    request = parse_request_code(lines[0])
    assert request.server_id == server_id
    assert request.organization == "Örnek A.Ş."
    assert request.installation_id == document["details"]["installation_id"]
    assert main(["license", "request-code", "--json", "--config", config]) == 0
    info = json.loads(capsys.readouterr().out)
    assert set(info) == {
        "request_code",
        "server_id",
        "installation_id",
        "organization",
        "product_version",
    }
    assert info["request_code"] == lines[0]  # the same code all day


def test_request_code_without_an_organisation_warns(
    pg_server: TestServer, tmp_path: Path, server_id: str, capsys: pytest.CaptureFixture[str]
) -> None:
    name = pg_server.new_name("t")
    pg_server.create_database(name)
    try:
        path = _write_config(tmp_path, pg_server.admin_url, name, site="")
        assert main(["upgrade", "--config", str(path)]) == 0
        capsys.readouterr()
        assert main(["license", "request-code", "--config", str(path)]) == 0
        captured = capsys.readouterr()
        assert captured.out.startswith("ARCIREQ1-")
        assert "Kuruluş adı ayarlanmamış" in captured.err
    finally:
        pg_server.drop_database(name)


def test_verify_install_remove(
    setup: Path, server_id: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup)
    licence = tmp_path / "lisans.lic"
    licence.write_bytes(
        make_license(server_ids=[server_id], modules=("core_5651", "usage_analytics"))
    )
    assert main(["license", "verify", str(licence), "--config", config]) == 0
    assert "ARCI-L-20261001-TEST0001" in capsys.readouterr().out
    assert _events(setup) == []  # verify writes nothing
    assert main(["license", "verify", str(licence), "--json", "--config", config]) == 0
    assert json.loads(capsys.readouterr().out)["ok"] is True

    assert main(["license", "install", str(licence), "--json", "--config", config]) == 0
    answer = json.loads(capsys.readouterr().out)
    assert answer["ok"] is True
    assert answer["status"]["status"] == "licensed"
    assert answer["status"]["licensee"] == "Örnek A.Ş."
    written = _status_file(tmp_path)
    assert (written["status"], written["license_id"]) == ("licensed", "ARCI-L-20261001-TEST0001")
    assert written["modules"] == ["core_5651", "usage_analytics"]
    (event,) = _events(setup)
    user = getpass.getuser()
    assert (event.kind, event.source, event.actor) == ("install", "cli", f"os:{user}")
    (entry,) = _audits(setup, "license.install")
    assert entry.actor_username == f"os:{user}"
    assert entry.details["os_user"] == user
    assert "licensee" not in entry.details

    assert main(["license", "remove", "--yes", "--config", config]) == 0
    assert "Lisans kaldırıldı [licence removed]" in capsys.readouterr().out
    assert _status_file(tmp_path)["status"] == "trial"
    (removed,) = _audits(setup, "license.remove")
    assert removed.details["os_user"] == user
    assert main(["license", "remove", "--yes", "--config", config]) == 1
    assert "Kurulu bir lisans yok." in capsys.readouterr().err


def test_refusals(
    setup: Path, server_id: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = str(setup)
    good = make_license(server_ids=[server_id])
    bad = tmp_path / "bozuk.lic"
    bad.write_bytes(tamper(good, "licensee", "Başka A.Ş."))
    assert main(["license", "verify", str(bad), "--config", config]) == 1
    assert "imzası geçersiz" in capsys.readouterr().err
    assert _events(setup) == []  # verify records nothing
    assert main(["license", "install", str(bad), "--json", "--config", config]) == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer == {
        "ok": False,
        "code": "license_signature_invalid",
        "message_tr": answer["message_tr"],
        "message_en": answer["message_en"],
        "details": None,
    }
    other = tmp_path / "baska.lic"
    other.write_bytes(make_license(server_ids=["AAAA-BBBB-CCCC-DDDD"]))
    assert main(["license", "install", str(other), "--json", "--config", config]) == 1
    answer = json.loads(capsys.readouterr().out)
    assert answer["code"] == "license_other_server"
    assert answer["details"] == {"server_id": server_id, "allowed": ["AAAA-BBBB-CCCC-DDDD"]}
    old = tmp_path / "eski.lic"
    old.write_bytes(make_license(server_ids=[server_id], maintenance_until=date(2026, 1, 1)))
    assert main(["license", "install", str(old), "--config", config]) == 1
    assert "bakım süresinden" in capsys.readouterr().err
    refusals = [event for event in _events(setup) if event.kind == "refuse"]
    assert [event.details["code"] for event in refusals] == [
        "license_signature_invalid",
        "license_other_server",
        "license_version_not_covered",
    ]
    assert len(_audits(setup, "license.refuse")) == 3
    assert all(entry.details["os_user"] for entry in _audits(setup, "license.refuse"))


def test_usage_errors_and_unreachable_database(
    setup: Path,
    pg_server: TestServer,
    tmp_path: Path,
    server_id: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = str(setup)
    assert main(["license", "install", str(tmp_path / "yok.lic"), "--config", config]) == 2
    assert "okunamadı" in capsys.readouterr().err
    assert main(["license", "status", "--config", str(tmp_path / "missing.toml")]) == 2
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))
    assert main(["license", "remove", "--config", config]) == 2  # no --yes, no console
    down = tmp_path / "down"
    down.mkdir()
    path = _write_config(down, pg_server.admin_url, "nowhere", port=1)
    assert main(["license", "status", "--config", str(path)]) == 3


def test_without_the_licence_code(
    setup: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in [n for n in sys.modules if n.startswith("arcilogger_core.license")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "arcilogger_db.licensing", None)
    monkeypatch.delattr(arcilogger_db, "licensing", raising=False)
    config = str(setup)
    assert main(["license", "status", "--config", config]) == 1
    err = capsys.readouterr().err
    assert "Lisans bileşeni yüklenemedi" in err
    assert "[The licence component could not be loaded" in err
    assert main(["license", "install", "x.lic", "--json", "--config", config]) == 1
    assert json.loads(capsys.readouterr().out)["code"] == "license_unavailable"
    # The rest of the command never needs it.
    assert main(["upgrade", "--config", config]) == 0
    assert main(["current", "--config", config]) == 0


def test_backup_and_upgrade_run_without_the_licence_code(setup: Path) -> None:
    """B-T31: the Backup task and ``upgrade`` (the same command line) never need it."""
    try:  # the programs themselves (a configured link, e.g. to pg_wrapper, is refused)
        pg_dump, pg_restore = (
            find_tool(name, services={}).path for name in ("pg_dump", "pg_restore")
        )
    except BackupError:
        pytest.skip("pg_dump/pg_restore not installed")
    with setup.open("a", encoding="utf-8") as handle:
        handle.write(
            f'\n[backup]\npg_dump = "{pg_dump.as_posix()}"\n'
            f'pg_restore = "{pg_restore.as_posix()}"\n'
        )
    # A configured tool runs only from a folder the installation's policy trusts (execpolicy).
    policy = AdminPolicy(
        enabled=True,
        executable_roots=tuple(sorted({str(pg_dump.parent), str(pg_restore.parent)})),
        written_at=utc_now(),
        written_by="installer",
    )
    target = AdminLayout(setup.parent / "admin").policy
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(dump_document(policy))
    script = (
        "import sys\n"
        "sys.modules['arcilogger_core.license'] = None\n"
        "sys.modules['arcilogger_db.licensing'] = None\n"
        "from arcilogger_db.cli import main\n"
        "codes = [main(['upgrade', '--config', sys.argv[1]]),\n"
        "         main(['backup', 'run', '--config', sys.argv[1]])]\n"
        "print(codes)\n"
        "print(sorted(n for n in sys.modules if 'licens' in n and sys.modules[n] is not None))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script, str(setup)], capture_output=True, timeout=300, check=False
    )
    out = completed.stdout.decode("utf-8")
    assert completed.returncode == 0, completed.stderr.decode("utf-8", "replace")
    lines = out.strip().splitlines()
    assert lines[-2] == "[0, 0]", out
    assert lines[-1] == "[]", out  # nothing of the licence code was loaded
    assert "Yedek [backup]:" in out
