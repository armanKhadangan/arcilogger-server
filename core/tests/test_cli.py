"""The arcilogger-core command line, end to end with the mock time-stamp authority."""

from __future__ import annotations

import io
import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core import __version__
from arcilogger_core.alerts import read_alerts
from arcilogger_core.cli import main
from arcilogger_core.immutability import unlock
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.seal import config_alert
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_core.tsa.base import TsaConfigurationError
from arcilogger_core.tsa.mock import MockTsa

CONFIG = """
data_dir = "{data}"
site_name = "Örnek A.Ş."

[[sources]]
name = "fortigate"
type = "fortigate"
address = "172.31.255.1"

[seal]
quiet_seconds = 0

[tsa]
provider = "mock"

[replica]
enabled = {replica}
target = "{target}"
"""


@pytest.fixture
def setup(tmp_path: Path, one_site_day: date) -> dict[str, Any]:
    data = tmp_path / "data"
    config = tmp_path / "arcilogger.toml"
    config.write_text(
        CONFIG.format(data=data.as_posix(), replica="false", target=(tmp_path / "nas").as_posix()),
        encoding="utf-8",
    )
    # The three days before today, which stays today for the CLI the whole test long.
    days = [one_site_day - timedelta(days=n) for n in (3, 2, 1)]
    layout = Layout(data)
    for day in days:
        path = layout.spool_file("fortigate", day)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("wb") as handle:
            for hour in range(0, 24, 2):
                received = datetime.combine(day, time(hour, 0), tzinfo=SITE_TZ)
                handle.write(format_line(received, "172.31.255.1", b"<189>logid=0000000013 x=1"))
    return {"config": str(config), "days": days, "layout": layout, "tmp": tmp_path}


def run(setup: dict[str, Any], *args: str) -> int:
    return main(["--config", setup["config"], *args])


def test_version_and_info(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == f"ARCILOGGER {__version__}"
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    capsys.readouterr()
    assert main(["info"]) == 0
    out = capsys.readouterr().out
    assert "Europe/Istanbul" in out
    assert "+03:00" in out


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 2
    assert "usage:" in capsys.readouterr().out


def test_bad_config_is_a_usage_error(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.toml"
    bad.write_text('data_dir = "x"\n[retention]\ndays = 10\n', encoding="utf-8")
    assert main(["--config", str(bad), "seal"]) == 2
    assert "730" in capsys.readouterr().err
    assert main(["--config", str(tmp_path / "missing.toml"), "seal"]) == 2


def test_seal_verify_and_status(setup: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert run(setup, "seal") == 0
    out = capsys.readouterr().out
    assert out.count("sealed") == 3
    assert "mühürlendi ve doğrulandı" in out
    assert read_alerts(setup["layout"].alerts) == []  # a working configuration: no alert
    assert run(setup, "seal") == 0
    assert "kapanmış gün yok" in capsys.readouterr().out

    assert run(setup, "verify", "--all", "--deep") == 0
    assert capsys.readouterr().out.count("OK") == 3
    layout: Layout = setup["layout"]
    assert (layout.state / "verify" / "pending-chain-check.json").exists()
    assert run(setup, "verify", "--last", "2", "--json") == 0
    assert len(json.loads(capsys.readouterr().out)) == 2

    assert run(setup, "status") == 0
    status = json.loads(capsys.readouterr().out)
    assert status["yesterday_sealed"] is True
    assert status["pending_days"] == []
    assert status["legal_grade"] is False


def test_verify_detects_tampering(
    setup: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(setup, "seal") == 0
    day = setup["days"][1]
    path = setup["layout"].vault_day(day) / "fortigate.log.gz"
    unlock(path)
    path.write_bytes(b"x")
    capsys.readouterr()
    assert run(setup, "verify", "--day", day.isoformat()) == 1
    assert "HATA" in capsys.readouterr().out
    alerts = setup["layout"].alerts.read_text(encoding="utf-8")
    assert "R20-verify" in alerts


def test_nothing_to_verify(setup: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert run(setup, "verify") == 0
    assert "Doğrulanacak" in capsys.readouterr().out


def test_credit_tsa_test_and_retention(
    setup: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(setup, "credit") == 0
    assert "Kalan kontör" in capsys.readouterr().out
    assert run(setup, "tsa-test") == 0
    result = json.loads(capsys.readouterr().out)
    assert result["verified"] is True
    assert result["legal_grade"] is False
    assert list(result) == [  # the console output the Requests task shares (run_tsa_test)
        "provider",
        "legal_grade_claimed",
        "legal_grade",
        "root_sha256",
        "root",
        "verified",
        "errors",
        "warnings",
        "gen_time",
        "serial",
        "tsa",
        "tsa_cert_not_after",
        "container",
        "token",
    ]
    assert Path(result["token"]).is_file()
    MockTsa(setup["layout"].state / "mock-tsa").credits = 0
    assert run(setup, "tsa-test") == 1
    assert capsys.readouterr().out.startswith("Zaman damgası alınamadı: ")
    assert run(setup, "retention", "--dry-run") == 0
    assert "kapalı" in capsys.readouterr().out


def test_the_requests_task_command(
    setup: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    """``python -I -m arcilogger_core requests run`` (task ``\\ARCILOGGER\\Requests``, every 5
    minutes and on Event 4111): nothing to do is success, and it does nothing."""
    assert run(setup, "requests", "run") == 0
    assert capsys.readouterr().out == ""
    assert not setup["layout"].requests.exists()
    with pytest.raises(SystemExit) as caught:
        main(["--config", setup["config"], "requests"])  # the action is required
    assert caught.value.code == 2


def test_replica_command(setup: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    assert run(setup, "replica") == 0
    assert "kapalı" in capsys.readouterr().out
    config = Path(setup["config"])
    config.write_text(
        config.read_text(encoding="utf-8").replace("enabled = false", "enabled = true"),
        encoding="utf-8",
    )
    assert run(setup, "seal") == 0
    capsys.readouterr()
    assert run(setup, "replica") == 0
    assert capsys.readouterr().out.count("copied") == 3
    assert (setup["tmp"] / "nas").is_dir()


def test_secrets_are_stored_but_never_printed(
    setup: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    assert run(setup, "secret", "init") == 0
    assert run(setup, "secret", "init") == 0  # idempotent
    monkeypatch.setattr("sys.stdin", io.StringIO("Gizli-Parola-1\n"))
    assert run(setup, "secret", "set", "kamusm.password") == 0
    assert run(setup, "secret", "list") == 0
    out = capsys.readouterr().out
    assert "kamusm.password  (kayıtlı ✓)" in out
    assert "Gizli-Parola-1" not in out
    assert run(setup, "secret", "set", "something.else") == 2
    assert run(setup, "secret", "delete", "kamusm.password") == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("Veritabanı-Parolası-2\n"))
    assert run(setup, "secret", "set", "database.password") == 0
    capsys.readouterr()
    assert run(setup, "secret", "list") == 0
    out = capsys.readouterr().out
    assert "database.password  (kayıtlı ✓)" in out
    assert "Veritabanı-Parolası-2" not in out
    # Every change is recorded for the audit log (spec 6.10, 8.5) — never the value.
    log = setup["layout"].secret_changes
    raw = log.read_text(encoding="utf-8")
    assert "Gizli-Parola-1" not in raw
    assert "Veritabanı-Parolası-2" not in raw
    changes = [json.loads(line) for line in raw.splitlines()]
    assert [(c["name"], c["action"], c["result"]) for c in changes] == [
        ("kamusm.password", "set", "stored"),
        ("kamusm.password", "delete", "deleted"),
        ("database.password", "set", "stored"),
    ]
    assert all(c["operator"].startswith("cli") and len(c["id"]) == 32 for c in changes)
    assert run(setup, "secret", "delete", "kamusm.password") == 0  # nothing to delete
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[-1])["result"] == "absent"
    # A change that cannot be recorded is reported (exit code 1), not silently accepted.
    log.unlink()
    log.mkdir()  # the log path is unusable
    monkeypatch.setattr("sys.stdin", io.StringIO("Üçüncü-Parola-3\n"))
    assert run(setup, "secret", "set", "kamusm.password") == 1
    assert "denetim kaydı için yazılamadı" in capsys.readouterr().err


def test_clock_check_command(
    setup: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "arcilogger_core.cli.check_clock",
        lambda config: {"ok": True, "offset_us": 3200, "server": "172.20.10.1"},
    )
    assert run(setup, "clock-check") == 0
    assert json.loads(capsys.readouterr().out)["offset_us"] == 3200
    monkeypatch.setattr(
        "arcilogger_core.cli.check_clock", lambda config: {"ok": True, "offset_us": 2_000_000}
    )
    assert run(setup, "clock-check") == 1
    capsys.readouterr()
    monkeypatch.setattr(
        "arcilogger_core.cli.check_clock",
        lambda config: {"ok": True, "offset_us": 100, "warning": "no local time source"},
    )
    assert run(setup, "clock-check") == 0  # only the fallback answered: a warning, still 0
    assert json.loads(capsys.readouterr().out)["warning"] == "no local time source"
    log = (setup["layout"].logs / "core.log").read_text(encoding="utf-8")
    assert "clock-check: no local time source" in log


def test_config_check_command(
    setup: dict[str, Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The pre-flight check of a production configuration: the organisation name is required,
    a missing local time source and a configuration without log sources (a new installation)
    are warnings."""
    assert run(setup, "config-check") == 0  # the setup names the organisation
    out = capsys.readouterr().out
    assert "UYARI [WARNING]  clock.ntp_server" in out
    assert "sources" not in out, "the setup has a log source"
    assert "HATA" not in out
    config = tmp_path / "bare.toml"
    config.write_text(f'data_dir = "{(tmp_path / "bare").as_posix()}"\n', encoding="utf-8")
    assert main(["--config", str(config), "config-check"]) == 1
    out = capsys.readouterr().out
    assert "HATA [ERROR]  site_name" in out
    assert "-SiteName" in out
    assert "UYARI [WARNING]  sources" in out
    assert "docs/DEVICE-SETUP.md" in out
    assert main(["--config", str(config), "config-check", "--json"]) == 1
    report = json.loads(capsys.readouterr().out)
    assert report["ok"] is False
    assert [(p["level"], p["key"]) for p in report["problems"]] == [
        ("error", "site_name"),
        ("warning", "clock.ntp_server"),
        ("warning", "sources"),
    ]
    config.write_text(
        f'data_dir = "{(tmp_path / "bare").as_posix()}"\nsite_name = "Örnek A.Ş."\n\n'
        '[clock]\nntp_server = "172.20.10.1"\n',
        encoding="utf-8",
    )
    assert main(["--config", str(config), "config-check"]) == 0, "warnings alone keep 0"
    assert "UYARI [WARNING]  sources" in capsys.readouterr().out
    config.write_text(
        f'data_dir = "{(tmp_path / "bare").as_posix()}"\nsite_name = "Örnek A.Ş."\n\n'
        '[[sources]]\nname = "fortigate"\ntype = "fortigate"\naddress = "172.31.255.1"\n\n'
        '[clock]\nntp_server = "172.20.10.1"\n',
        encoding="utf-8",
    )
    assert main(["--config", str(config), "config-check"]) == 0
    assert "Yapılandırma tamam" in capsys.readouterr().out


def test_zamane_without_credentials_reports_an_error(
    setup: dict[str, Any], capsys: pytest.CaptureFixture[str]
) -> None:
    config = Path(setup["config"])
    jar = setup["tmp"] / "tss.jar"
    jar.write_bytes(b"PK")
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            'provider = "mock"', f'provider = "zamane"\nzamane_jar = "{jar.as_posix()}"'
        ),
        encoding="utf-8",
    )
    assert run(setup, "credit") == 1
    assert "customer" in capsys.readouterr().err


def test_the_smtp_password_is_a_stored_secret(
    setup: dict[str, Any], capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """``arcilogger-core secret set smtp.password`` (docs/ALERTS.md): stored, never printed."""
    assert run(setup, "secret", "init") == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("Smtp-Deneme-Parolasi-4\n"))  # gitleaks:allow
    assert run(setup, "secret", "set", "smtp.password") == 0
    assert run(setup, "secret", "list") == 0
    out = capsys.readouterr().out
    assert "smtp.password  (kayıtlı ✓)" in out
    assert "Smtp-Deneme-Parolasi-4" not in out


def test_seal_configuration_alert_once_per_day_and_cause(tmp_path: Path) -> None:
    """The Seal task runs every 5 minutes: ``R20-seal-config`` once a site day per cause."""
    layout = Layout(tmp_path)
    first = datetime(2026, 10, 19, 1, 0, tzinfo=SITE_TZ)
    error = TsaConfigurationError("Kamu SM customer number / password not stored")
    assert config_alert(layout, "credentials", error, clock=lambda: first)
    assert not config_alert(layout, "credentials", error, clock=lambda: first + timedelta(hours=4))
    assert config_alert(layout, "roots", error, clock=lambda: first + timedelta(hours=4))
    assert config_alert(layout, "credentials", error, clock=lambda: first + timedelta(days=1))
    assert config_alert(layout, "something-else", error, clock=lambda: first)
    alerts = read_alerts(layout.alerts)
    assert [(a["rule"], a["details"]["cause"]) for a in alerts] == [
        ("R20-seal-config", "credentials"),
        ("R20-seal-config", "roots"),
        ("R20-seal-config", "credentials"),
        ("R20-seal-config", "provider"),
    ]
    assert alerts[0]["message"].startswith("Mühürleme başlatılamadı: Kamu SM müşteri numarası")
    assert "[Sealing cannot start: the Kamu SM customer number" in alerts[0]["message"]
    # The marker is not a day state (state/seal/*.json is read by the sealer and the worker).
    assert list((layout.state / "seal").glob("*.json")) == []


def test_seal_with_an_unusable_secret_store(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Secrets stored, key file missing: the cause is the secret store, not the credentials."""
    data = tmp_path / "data"
    config = tmp_path / "arcilogger.toml"
    jar = (tmp_path / "tss-client-console.jar").as_posix()
    config.write_text(
        f'data_dir = "{data.as_posix()}"\n[tsa]\nprovider = "zamane"\nzamane_jar = "{jar}"\n',
        encoding="utf-8",
    )
    (tmp_path / "secrets.json").write_text('{"kamusm.customer": "x"}', encoding="utf-8")
    assert main(["--config", str(config), "seal"]) == 1
    assert "secret key not found" in capsys.readouterr().err
    (alert,) = read_alerts(Layout(data).alerts)
    assert alert["details"]["cause"] == "secret-store"
