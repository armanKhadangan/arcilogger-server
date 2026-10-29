"""``arcilogger-ingest`` command line: exit codes, status, and the real process stopped by a
console event / signal."""

from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, text

from arcilogger_core.testing import refused_port
from arcilogger_ingest import __version__
from arcilogger_ingest.cli import EXIT_CONFIG, EXIT_DB_UNAVAILABLE, EXIT_OK, main
from arcilogger_ingest.status import read_status
from arcilogger_ingest.testing import (
    DAY,
    World,
    check_exactly_once,
    fortigate_message,
    loaded,
    write_config_file,
)


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"ARCILOGGER ingest {__version__}"
    assert main([]) == 2  # no command: help


def test_configuration_errors_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["run", "--config", str(tmp_path / "missing.toml")]) == EXIT_CONFIG
    assert "Yapılandırma okunamadı" in capsys.readouterr().err
    bad = tmp_path / "bad.toml"
    bad.write_text('data_dir = "x"\nunknown_key = 1\n', encoding="utf-8")
    assert main(["--config", str(bad), "run"]) == EXIT_CONFIG
    assert main(["status", "--config", str(bad)]) == EXIT_CONFIG


@pytest.mark.parametrize(
    "key",
    [
        Fernet.generate_key(),  # a valid key, but not the one the password was encrypted with
        b"x" * 44,  # not a key at all
    ],
    # Stable test ids: parallel workers (pytest-xdist) must collect identical tests.
    ids=["other-key", "not-a-key"],
)
def test_an_unusable_secret_store_exits_2(world: World, pg_dsn: str, key: bytes) -> None:
    config = write_config_file(world, pg_dsn)
    (world.root / "secrets" / "secret.key").write_bytes(key)
    assert main(["run", "--config", str(config), "--once"]) == EXIT_CONFIG


def test_run_once_in_process(world: World, pg_dsn: str, db_engine: Engine) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(12)))
    config = write_config_file(world, pg_dsn)
    assert main(["run", "--config", str(config), "--once"]) == EXIT_OK
    assert check_exactly_once(db_engine, world) == []


def test_run_once_with_the_database_unreachable_exits_3(world: World, tmp_path: Path) -> None:
    with refused_port() as port:
        config = write_config_file(world, f"postgresql://nobody:pw@127.0.0.1:{port}/nothing")
        assert main(["run", "--config", str(config), "--once"]) == EXIT_DB_UNAVAILABLE


def test_status_command(world: World, pg_dsn: str, capsys: pytest.CaptureFixture[str]) -> None:
    config = write_config_file(world, pg_dsn)
    assert main(["status", "--config", str(config)]) == 1
    assert "not found" in capsys.readouterr().err
    world.append("fortigate", DAY, [fortigate_message(0)])
    assert main(["run", "--config", str(config), "--once"]) == EXIT_OK
    capsys.readouterr()
    assert main(["status", "--config", str(config)]) == 0
    status = json.loads(capsys.readouterr().out)
    assert status["state"] == "stopped"
    assert status["sources"]["fortigate"]["line_number"] == 1


def test_python_dash_m_entry_point() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "arcilogger_ingest", "version"],
        capture_output=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0
    assert result.stdout.decode().strip() == f"ARCILOGGER ingest {__version__}"


def _stop_request(process: subprocess.Popen[bytes]) -> None:
    """What the service wrapper does: Ctrl+Break on Windows (process group), SIGINT on POSIX."""
    if sys.platform == "win32":
        process.send_signal(signal.CTRL_BREAK_EVENT)
    else:
        process.send_signal(signal.SIGINT)


@pytest.mark.parametrize("workers", [0, 2])
def test_the_service_process_stops_gracefully(
    world: World, pg_dsn: str, db_engine: Engine, workers: int
) -> None:
    world.append("fortigate", DAY, (fortigate_message(i) for i in range(40)))
    config = write_config_file(world, pg_dsn)
    command = [sys.executable, "-m", "arcilogger_ingest", "run", "--config", str(config)]
    if workers:
        command += ["--workers", str(workers)]
    if sys.platform == "win32":  # Ctrl+Break goes to a process group
        flags = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        flags = 0
    process = subprocess.Popen(command, stderr=subprocess.PIPE, creationflags=flags)
    try:
        deadline = time.monotonic() + 60
        while len(loaded(db_engine)) < 40 and time.monotonic() < deadline:
            time.sleep(0.05)
        _stop_request(process)
        _, stderr = process.communicate(timeout=30)
    finally:
        if process.poll() is None:
            process.kill()
    log = stderr.decode("utf-8", "replace")
    assert process.returncode == EXIT_OK, log
    assert "ingest stopped" in log
    assert check_exactly_once(db_engine, world) == []
    status = read_status(world.layout.state / "ingest-status.json")
    assert status is not None
    assert status["state"] == "stopped"
    with db_engine.connect() as connection:
        sources = connection.execute(text("SELECT count(*) FROM sources")).scalar_one()
    assert sources == 2  # the configured exact sources
