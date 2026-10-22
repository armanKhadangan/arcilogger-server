"""Windows Task Scheduler definitions of the evidence jobs (deploy/windows/tasks)."""

from __future__ import annotations

import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from arcilogger_core.cli import _build_parser as core_parser
from arcilogger_db.backup import DUMP_TIMEOUT_SECONDS
from arcilogger_db.cli import _build_parser as db_parser

TASKS = Path(__file__).parents[1] / "deploy" / "windows" / "tasks"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
#: Task → command of ``python -I -m arcilogger_core``.
EXPECTED = {
    "Seal": "seal",
    "VerifyDaily": "verify --last 2",
    "VerifyWeekly": "verify --last 90",
    "VerifyMonthly": "verify --all --deep",
    "Replica": "replica",
    "Retention": "retention",
    "Credit": "credit",
    "ClockCheck": "clock-check",
}
#: Task → command of ``python -I -m arcilogger_db`` (the scheduled database backup, ADR-0009).
EXPECTED_DB = {"Backup": "backup run"}
PARSERS = {"arcilogger_core": core_parser, "arcilogger_db": db_parser}


def _tasks() -> list[Path]:
    return sorted(TASKS.glob("*.xml"))


def test_every_job_has_a_definition() -> None:
    assert {p.stem for p in _tasks()} == set(EXPECTED) | set(EXPECTED_DB)


@pytest.mark.parametrize("path", _tasks(), ids=lambda p: p.stem)
def test_definition(path: Path) -> None:
    root = ET.parse(path).getroot()  # noqa: S314 - our own repository file
    assert root.find("t:Principals/t:Principal/t:UserId", NS).text == "S-1-5-19"  # type: ignore[union-attr]
    settings = root.find("t:Settings", NS)
    assert settings is not None
    assert settings.findtext("t:StartWhenAvailable", namespaces=NS) == "true"
    assert settings.findtext("t:MultipleInstancesPolicy", namespaces=NS) == "IgnoreNew"
    assert root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS) == "{{PYTHON}}"
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) or ""
    # Python isolated (-I before -m, contract 9.6): no PYTHON* variable can put a module on the
    # path of an evidence job.
    isolated, flag, module, command = arguments.split(" ", 3)
    assert isolated == "-I"
    assert flag == "-m"
    if path.stem in EXPECTED_DB:
        assert (module, command) == ("arcilogger_db", EXPECTED_DB[path.stem])
    else:
        assert (module, command) == ("arcilogger_core", EXPECTED[path.stem])
    PARSERS[module]().parse_args(shlex.split(command))  # the CLI accepts it
    assert root.findtext("t:RegistrationInfo/t:URI", namespaces=NS) == f"\\ARCILOGGER\\{path.stem}"


def test_seal_runs_after_midnight_and_retries_every_five_minutes() -> None:
    root = ET.parse(TASKS / "Seal.xml").getroot()  # noqa: S314
    trigger = root.find("t:Triggers/t:CalendarTrigger", NS)
    assert trigger is not None
    assert trigger.findtext("t:StartBoundary", namespaces=NS) == "2026-01-01T00:10:00"
    assert trigger.findtext("t:Repetition/t:Interval", namespaces=NS) == "PT5M"


def test_backup_runs_daily_after_sealing() -> None:
    """01:40 local time (ASSUMPTIONS B7): after the seal (00:10) and the nightly lease rebuild,
    and long enough before the 4-hour limit ends that pg_dump's own timeout comes first."""
    root = ET.parse(TASKS / "Backup.xml").getroot()  # noqa: S314
    trigger = root.find("t:Triggers/t:CalendarTrigger", NS)
    assert trigger is not None
    assert trigger.findtext("t:StartBoundary", namespaces=NS) == "2026-01-01T01:40:00"
    assert trigger.findtext("t:ScheduleByDay/t:DaysInterval", namespaces=NS) == "1"
    assert trigger.find("t:Repetition", NS) is None
    settings = root.find("t:Settings", NS)
    assert settings is not None
    assert settings.findtext("t:ExecutionTimeLimit", namespaces=NS) == "PT4H"
    assert DUMP_TIMEOUT_SECONDS < 4 * 3600


def test_register_script_registers_every_definition() -> None:
    """Register-ArciTasks.ps1 takes every XML file of the folder, so an upgrade adds Backup."""
    script = (TASKS.parent / "scripts" / "Register-ArciTasks.ps1").read_text(encoding="utf-8-sig")
    assert "Get-ChildItem -LiteralPath $TaskDirectory -Filter *.xml" in script
    assert "Register-ScheduledTask -TaskName $_.BaseName" in script


@pytest.mark.skipif(sys.platform != "win32", reason="Task Scheduler is Windows-only")
def test_task_scheduler_accepts_the_definitions(tmp_path: Path) -> None:
    """Register every definition in a throw-away folder (needs an administrator, e.g. CI)."""
    is_admin = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent())"
            ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)",
        ],
        capture_output=True,
        text=True,
        check=False,
    ).stdout.strip()
    if is_admin != "True":
        pytest.skip("not running as administrator")
    for path in _tasks():
        xml_file = tmp_path / path.name
        xml_file.write_text(
            path.read_text(encoding="utf-8").replace("{{PYTHON}}", sys.executable), encoding="utf-8"
        )
        script = (
            f"$xml = Get-Content -LiteralPath '{xml_file}' -Raw -Encoding UTF8; "
            f"Register-ScheduledTask -TaskName '{path.stem}' -TaskPath '\\ARCILOGGER-CI\\' "
            "-Xml $xml -Force | Out-Null; "
            f"Unregister-ScheduledTask -TaskName '{path.stem}' -TaskPath '\\ARCILOGGER-CI\\' "
            "-Confirm:$false"
        )
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, f"{path.stem}: {result.stderr}"
