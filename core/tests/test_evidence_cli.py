"""``arcilogger-core evidence build | verify | purge`` end to end with the mock TSA."""

from __future__ import annotations

import io
import json
import os
import zipfile
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Any

import pytest

from arcilogger_core.cli import main
from arcilogger_core.layout import Layout
from arcilogger_core.rawlog import format_line
from arcilogger_core.timeutil import SITE_TZ, site_day, utc_now

CONFIG = """
data_dir = "{data}"
site_name = "Örnek A.Ş."

[[sources]]
name = "fortigate"
type = "fortigate"
address = "172.31.255.1"

[[sources]]
name = "core-r1"
type = "mikrotik"
address = "172.20.10.1"

[seal]
quiet_seconds = 0

[tsa]
provider = "mock"
"""


@pytest.fixture
def sealed(tmp_path: Path, capfd: pytest.CaptureFixture[str]) -> dict[str, Any]:
    data = tmp_path / "data"
    config = tmp_path / "arcilogger.toml"
    config.write_text(CONFIG.format(data=data.as_posix()), encoding="utf-8")
    today = site_day(utc_now())
    days = [today - timedelta(days=n) for n in (3, 2, 1)]
    layout = Layout(data)
    for day in days:
        for source, sender in (("fortigate", "172.31.255.1"), ("core-r1", "172.20.10.1")):
            path = layout.spool_file(source, day)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("wb") as handle:
                for hour in range(0, 24, 3):
                    received = datetime.combine(day, time(hour, 0), tzinfo=SITE_TZ)
                    handle.write(format_line(received, sender, b"<189>logid=13 hour=%d" % hour))
    assert main(["--config", str(config), "seal"]) == 0
    capfd.readouterr()
    return {"config": str(config), "days": days, "layout": layout, "tmp": tmp_path}


def run(sealed: dict[str, Any], *args: str) -> int:
    return main(["--config", sealed["config"], "evidence", *args])


def day_range(sealed: dict[str, Any]) -> list[str]:
    return ["--from", sealed["days"][0].isoformat(), "--to", sealed["days"][-1].isoformat()]


def test_a_claimed_but_unlisted_root_is_not_called_a_test_stamp(
    sealed: dict[str, Any], capfd: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Kamu SM production days whose root is not (yet) in tsa.legal_root_fingerprints."""
    import dataclasses

    import arcilogger_core.cli as cli
    from arcilogger_core.evidence import build_package

    def production_like(*args: Any, **kwargs: Any) -> Any:
        info = build_package(*args, **kwargs)
        return dataclasses.replace(info, legal_grade=False, legal_grade_claimed=True)

    monkeypatch.setattr(cli, "build_package", production_like)
    assert run(sealed, "build", *day_range(sealed)) == 0
    out = capfd.readouterr().out
    assert "UYARI" not in out
    assert "test damgalı" not in out
    assert "tsa.legal_root_fingerprints listesinde olmadığı için" in out


def test_build_and_verify(sealed: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    assert run(sealed, "build", *day_range(sealed)) == 0
    out = capfd.readouterr().out
    assert "Kanıt paketi oluşturuldu:" in out
    assert "UYARI" in out  # mock-stamped days
    assert "Parola" not in out
    (package,) = sealed["layout"].exports.glob("ARCILOGGER-kanit-KP-*.zip")
    assert str(package) in out
    with zipfile.ZipFile(package) as archive:
        index = json.loads(archive.read("paket.json"))
    assert index["created_by"].startswith("cli")
    assert index["site"] == "Örnek A.Ş."
    assert len(index["days"]) == 3
    assert index["scope"]["audit_log"] is False  # left out unless asked for (KVKK)
    with zipfile.ZipFile(package) as archive:
        assert not any(name.endswith("audit.jsonl.gz") for name in archive.namelist())
    # verification needs no configuration at all: an auditor has no arcilogger.toml
    os.environ.pop("ARCILOGGER_CONFIG", None)
    capfd.readouterr()
    assert main(["evidence", "verify", str(package)]) == 0
    assert "SONUÇ: DOĞRULANDI" in capfd.readouterr().out
    assert main(["evidence", "verify", str(package), "--json", "--quick"]) == 0
    assert json.loads(capfd.readouterr().out)["passed"] is True


def test_build_with_every_option(sealed: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    tmp: Path = sealed["tmp"]
    day = sealed["days"][1]
    refs = tmp / "refs.json"
    refs.write_text(
        json.dumps([{"day": day.isoformat(), "source": "fortigate", "line": 3}]), encoding="utf-8"
    )
    report = tmp / "rapor-5651.pdf"
    report.write_bytes(b"%PDF-1.7 test report")
    out_dir = tmp / "out"
    args = [
        "build",
        "--from", day.isoformat(),
        "--to", day.isoformat(),
        "--source", "fortigate",
        "--extract", str(refs),
        "--pdf", str(report),
        "--out", str(out_dir),
        "--creator", "tolga",
        "--lookup-id", "L-7",
        "--reference", "Savcılık 2026/123",
        "--include-audit-log",
        "--json",
    ]  # fmt: skip
    assert run(sealed, *args) == 0
    info = json.loads(capfd.readouterr().out)
    assert info["created_by"] == "tolga"
    assert info["lookup_id"] == "L-7"
    assert info["sources"] == ["fortigate"]
    assert info["extract_lines"] == 1
    assert "password" not in info
    with zipfile.ZipFile(out_dir / info["file"]) as archive:
        names = archive.namelist()
        index = json.loads(archive.read("paket.json"))
        assert archive.read("rapor/rapor-5651.pdf") == b"%PDF-1.7 test report"
    assert f"extract/{day.isoformat()}/fortigate.txt" in names
    assert not any(name.endswith("core-r1.log.gz") for name in names)
    assert index["scope"]["reference"] == "Savcılık 2026/123"
    assert index["scope"]["audit_log"] is True  # --include-audit-log
    assert f"vault/{day:%Y/%m/%d}/audit.jsonl.gz" in names


def test_encrypted_build_and_verify_with_the_password(
    sealed: dict[str, Any],
    capfd: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert run(sealed, "build", *day_range(sealed), "--password") == 0
    out = capfd.readouterr().out
    password = next(
        line.split(":", 1)[1].strip() for line in out.splitlines() if "Parola  " in line
    )
    assert "yalnızca şimdi gösteriliyor" in out
    (package,) = sealed["layout"].exports.glob("*.zip")
    capfd.readouterr()
    monkeypatch.setattr("sys.stdin", io.StringIO(password + "\n"))
    assert main(["evidence", "verify", str(package)]) == 0
    assert "SONUÇ: DOĞRULANDI" in capfd.readouterr().out
    monkeypatch.setattr("sys.stdin", io.StringIO("wrong password\n"))
    assert main(["evidence", "verify", str(package)]) == 2
    assert "Paket açılamadı" in capfd.readouterr().err
    # --json returns the generated password once, for programs that build packages
    assert (
        run(
            sealed,
            "build",
            *day_range(sealed),
            "--password",
            "--json",
            "--out",
            str(sealed["tmp"] / "o"),
        )
        == 0
    )
    data = json.loads(capfd.readouterr().out)
    assert len(data["password"]) == 29
    assert data["password_protected"] is True


def test_build_errors(sealed: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    today = site_day(utc_now())
    assert (
        run(sealed, "build", "--from", sealed["days"][0].isoformat(), "--to", today.isoformat())
        == 1
    )
    assert "not sealed" in capfd.readouterr().err
    assert run(sealed, "build", "--from", "2026-10-12", "--to", "2026-10-10") == 1
    assert "before" in capfd.readouterr().err
    assert (
        run(sealed, "build", *day_range(sealed), "--extract", str(sealed["tmp"] / "missing.json"))
        == 1
    )
    assert "cannot read" in capfd.readouterr().err


def test_verify_reports_tampering(
    sealed: dict[str, Any], capfd: pytest.CaptureFixture[str]
) -> None:
    assert run(sealed, "build", *day_range(sealed)) == 0
    capfd.readouterr()
    (package,) = sealed["layout"].exports.glob("*.zip")
    folder = sealed["tmp"] / "unpacked"
    with zipfile.ZipFile(package) as archive:
        archive.extractall(folder)
    victim = next(folder.glob("vault/*/*/*/fortigate.log.gz"))
    data = bytearray(victim.read_bytes())
    data[len(data) // 2] ^= 0xFF
    victim.write_bytes(bytes(data))
    capfd.readouterr()
    assert main(["evidence", "verify", str(folder)]) == 1
    out = capfd.readouterr().out
    assert "[HATA]" in out
    assert "DOĞRULANAMADI" in out
    assert main(["evidence", "verify", str(folder / "nothing-here")]) == 2


def test_purge(sealed: dict[str, Any], capfd: pytest.CaptureFixture[str]) -> None:
    exports = sealed["layout"].exports
    exports.mkdir(parents=True)
    old = exports / "ARCILOGGER-kanit-KP-old.zip"
    old.write_bytes(b"x")
    moment = (utc_now() - timedelta(days=40)).timestamp()
    os.utime(old, (moment, moment))
    assert run(sealed, "purge") == 0
    assert "1 eski kanıt paketi silindi" in capfd.readouterr().out
    assert not old.exists()
    assert run(sealed, "purge", "--keep-days", "0") == 0
