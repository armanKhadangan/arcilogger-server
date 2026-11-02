"""``arcilogger-worker`` command line: version, usage errors, ``run`` (starts, writes its status,
stops on request) and ``rebuild --day`` (manual repair, job_runs row, exit codes)."""

from __future__ import annotations

import json
import threading
from datetime import date, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, make_url, select
from sqlalchemy.orm import Session

from arcilogger_core.secretstore import DATABASE_PASSWORD, SecretStore
from arcilogger_db.models import Alert, JobRun, Lease
from arcilogger_db.settings import set_setting
from arcilogger_worker import __version__
from arcilogger_worker.alerts.smtptesting import FakeSmtp
from arcilogger_worker.cli import EXIT_CONFIG, EXIT_DATABASE, EXIT_OK, main, run
from arcilogger_worker.correlation.watermark import set_watermark
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)


def write_config(tmp_path: Path, dsn: str | None, *, port: int | None = None) -> Path:
    url = make_url(dsn or "postgresql://nobody@127.0.0.1:1/none")
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        f'data_dir = "{(tmp_path / "data").as_posix()}"\n\n'
        "[database]\n"
        f'host = "{url.host}"\nport = {port or url.port or 5432}\n'
        f'name = "{url.database}"\nuser = "{url.username}"\nconnect_timeout_seconds = 2\n\n'
        "[worker]\ninterval_seconds = 5\n",
        encoding="utf-8",
    )
    if url.password:
        SecretStore.create_key(tmp_path / "secret.key")
        SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json").set(
            DATABASE_PASSWORD, str(url.password)
        )
    return path


def test_version_and_usage(capsys: pytest.CaptureFixture[str], tmp_path: Path) -> None:
    assert main(["version"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == f"ARCILOGGER worker {__version__}"
    assert main([]) == EXIT_CONFIG
    assert main(["rebuild"]) == EXIT_CONFIG  # --day is required
    assert main(["rebuild", "--day", "12.10.2026"]) == EXIT_CONFIG
    assert "YYYY-MM-DD" in capsys.readouterr().err
    config = write_config(tmp_path, None)
    assert (
        main(["rebuild", "--day", "2026-10-12", "--to", "2026-10-11", "--config", str(config)]) == 2
    )
    assert main(["run", "--config", str(tmp_path / "missing.toml")]) == EXIT_CONFIG
    assert "Yapılandırma okunamadı" in capsys.readouterr().err


def test_rebuild_reports_an_unreachable_database(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = write_config(tmp_path, None)
    assert main(["rebuild", "--day", "2026-10-12", "--config", str(config)]) == EXIT_DATABASE
    assert "database unreachable" in capsys.readouterr().err


def test_rebuild_a_range(
    pg_dsn: str,
    tmp_path: Path,
    db_session: Session,
    evidence: Evidence,
    capsys: pytest.CaptureFixture[str],
) -> None:
    evidence.dhcp(tr(DAY, 22), "assigned", "172.20.43.120", "f0:1f:af:00:00:01")
    evidence.dhcp(
        tr(DAY + timedelta(days=1), 2), "deassigned", "172.20.43.120", "f0:1f:af:00:00:01"
    )
    # Rebuilds stop at the incremental job's watermark (here: two days later).
    set_watermark(db_session, tr(DAY + timedelta(days=2), 0))
    db_session.commit()
    config = write_config(tmp_path, pg_dsn)
    args = ["rebuild", "--day", "2026-10-12", "--to", "2026-10-13", "--config", str(config)]
    assert main(args) == EXIT_OK
    out = capsys.readouterr().out
    assert "2026-10-12: 1 kira yazıldı" in out
    assert "2026-10-13: 0 kira yazıldı, 1 güncellendi" in out
    lease = db_session.scalars(select(Lease)).one()
    assert (lease.start, lease.end, lease.end_reason) == (
        tr(DAY, 22),
        tr(DAY + timedelta(days=1), 2),
        "dhcp",
    )
    runs = db_session.scalars(select(JobRun).order_by(JobRun.id)).all()
    assert [(r.job, r.status, r.details["manual"]) for r in runs] == [
        ("leases.rebuild", "ok", True),
        ("leases.rebuild", "ok", True),
    ]
    assert runs[0].details["days"] == ["2026-10-12"]


def test_run_starts_writes_status_and_stops(pg_dsn: str, tmp_path: Path, db_engine: Engine) -> None:
    config = write_config(tmp_path, pg_dsn)
    # The installer's record of the data root (with the BOM Windows PowerShell 5.1 may write).
    (tmp_path / "data-root.txt").write_text(f"\ufeff{tmp_path / 'data'}\n", encoding="utf-8")
    stop = threading.Event()
    result: list[int] = []
    thread = threading.Thread(target=lambda: result.append(run(config, stop=stop)))
    thread.start()
    status_path = tmp_path / "data" / "state" / "worker-status.json"
    for _ in range(300):
        if status_path.exists():
            status = json.loads(status_path.read_text(encoding="utf-8"))
            if status["jobs"]["leases.incremental"]["last_status"]:
                break
        threading.Event().wait(0.05)
    stop.set()
    thread.join(timeout=20)
    assert result == [EXIT_OK]
    status = json.loads(status_path.read_text(encoding="utf-8"))
    assert status["state"] == "stopped"
    assert status["jobs"]["leases.incremental"]["last_status"] == "ok"
    assert (tmp_path / "data" / "logs" / "worker.log").exists()


def test_run_refuses_a_configuration_that_moves_its_folders(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0014 (contract Day 0 notes D0-2, WP2-1): the administration agent may write
    ``arcilogger.toml``. ``secrets_dir`` must stay the folder of the configuration file and
    ``data_dir`` the data root the installer recorded, or the worker does not start (it would
    read a password store, and import evidence and alert files, of the writer's choosing)."""
    config = write_config(tmp_path, None)
    text = config.read_text(encoding="utf-8")
    config.write_text(f'secrets_dir = "{(tmp_path / "elsewhere").as_posix()}"\n' + text)
    assert run(config) == EXIT_CONFIG
    assert "secrets_dir" in capsys.readouterr().err
    config.write_text(text, encoding="utf-8")
    record = tmp_path / "data-root.txt"
    record.write_text((tmp_path / "other").as_posix() + "\n", encoding="utf-8")
    assert run(config) == EXIT_CONFIG
    assert "data_dir" in capsys.readouterr().err
    data = (tmp_path / "data").as_posix()
    # A record that exists but cannot be used fails closed, even when it names the right folder
    # (UTF-16, as Notepad may save it).
    for unusable in (b"relative/data\n", f"{data}\n{data}\n".encode(), data.encode("utf-16")):
        record.write_bytes(unusable)
        assert run(config) == EXIT_CONFIG
        assert "data-root.txt" in capsys.readouterr().err
    assert not (tmp_path / "data").exists()  # no log, no status file below a refused data_dir


def test_rebuild_with_unusable_settings_fails_cleanly(
    pg_dsn: str,
    tmp_path: Path,
    db_session: Session,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A settings error is exit code 2 with a message, and the job run is closed as failed
    (never left "running")."""
    # 3600 s x 1 % = 36 s guest timeout: shorter than the 300 s traffic bucket.
    set_setting(db_session, "lease.timeout_percent", 1, updated_by="tolga")
    db_session.commit()
    config = write_config(tmp_path, pg_dsn)
    assert main(["rebuild", "--day", "2026-10-12", "--config", str(config)]) == EXIT_CONFIG
    err = capsys.readouterr().err
    assert "settings error" in err
    assert "traffic bucket" in err
    (job,) = db_session.scalars(select(JobRun)).all()
    assert (job.job, job.status, job.details["error"]) == ("leases.rebuild", "failed", "ValueError")
    assert "traffic bucket" in job.details["message"]
    assert job.finished_at is not None


def test_alerts_commands(
    pg_dsn: str,
    tmp_path: Path,
    db_session: Session,
    smtp_server: FakeSmtp,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = write_config(tmp_path, pg_dsn)
    with config.open("a", encoding="utf-8") as handle:
        handle.write(
            f'\n[smtp]\nhost = "127.0.0.1"\nport = {smtp_server.port}\nsecurity = "none"\n'
            'from_address = "arcilogger@example.com"\n'
        )
    assert main(["alerts", "list", "--config", str(config)]) == EXIT_OK
    assert "Uyarı yok" in capsys.readouterr().out
    db_session.add(Alert(rule="R15", severity="high", title="Log kaynağı sessiz: fortigate"))
    db_session.commit()
    assert main(["alerts", "list", "--open", "--config", str(config)]) == EXIT_OK
    assert "Log kaynağı sessiz: fortigate" in capsys.readouterr().out
    to = "bilgi-islem@example.com"
    assert main(["alerts", "test-email", "--to", to, "--config", str(config)]) == EXIT_OK
    assert f"Gönderildi / sent: {to}" in capsys.readouterr().out
    assert [m.recipients for m in smtp_server.received] == [[to]]
    assert main(["alerts"]) == EXIT_CONFIG  # a sub-command is required
    assert main(["alerts", "test-email", "--config", str(config)]) == EXIT_CONFIG  # --to
    (tmp_path / "other").mkdir()
    unreachable = write_config(tmp_path / "other", None)
    assert main(["alerts", "list", "--config", str(unreachable)]) == EXIT_DATABASE
