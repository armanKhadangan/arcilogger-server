"""Windows Task Scheduler definitions of the evidence jobs (deploy/windows/tasks)."""

from __future__ import annotations

import argparse
import re
import shlex
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from arcilogger_core.admin.contract import EVENT_SOURCE, Event
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
    # What the web interface asks for (credit query, time-stamp and replica test; ADR-0014).
    "Requests": "requests run",
}
#: Task → command of ``python -I -m arcilogger_db`` (the scheduled database backup, ADR-0009).
EXPECTED_DB = {"Backup": "backup run"}
#: The one task that is neither LOCAL SERVICE nor Python: ApplySystem (SYSTEM) runs
#: Sync-ArciSystem.ps1 of the program folder with the system's Windows PowerShell (ADR-0014).
SYSTEM_TASK = "ApplySystem"
WINDOWS_POWERSHELL = r"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
SYNC_ARGUMENTS = (
    '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{{SCRIPTS}}\\Sync-ArciSystem.ps1"'
)
#: Tasks woken through the Application log (source ARCILOGGER-Admin): the event only wakes the
#: task, the signed request file is the authority.
WAKE_EVENTS = {
    "Seal": Event.SEAL_REQUESTED,
    "Requests": Event.TASK_REQUESTED,
    "ApplySystem": Event.SYSTEM_SYNC_REQUESTED,
}
#: A wake-up during a run queues one more run instead of being dropped (IgnoreNew elsewhere).
QUEUED = {"Requests", "ApplySystem"}
PARSERS = {"arcilogger_core": core_parser, "arcilogger_db": db_parser}


def _has_command(parser: argparse.ArgumentParser, name: str) -> bool:
    actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
    return bool(actions) and name in actions[0].choices


def _tasks() -> list[Path]:
    return sorted(TASKS.glob("*.xml"))


def test_every_job_has_a_definition() -> None:
    assert {p.stem for p in _tasks()} == set(EXPECTED) | set(EXPECTED_DB) | {SYSTEM_TASK}


@pytest.mark.parametrize("path", _tasks(), ids=lambda p: p.stem)
def test_definition(path: Path) -> None:
    root = ET.parse(path).getroot()  # noqa: S314 - our own repository file
    principal = root.find("t:Principals/t:Principal", NS)
    assert principal is not None
    settings = root.find("t:Settings", NS)
    assert settings is not None
    assert settings.findtext("t:StartWhenAvailable", namespaces=NS) == "true"
    policy = "Queue" if path.stem in QUEUED else "IgnoreNew"
    assert settings.findtext("t:MultipleInstancesPolicy", namespaces=NS) == policy
    assert root.findtext("t:RegistrationInfo/t:URI", namespaces=NS) == f"\\ARCILOGGER\\{path.stem}"
    command = root.findtext("t:Actions/t:Exec/t:Command", namespaces=NS)
    arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=NS) or ""
    if path.stem == SYSTEM_TASK:
        assert principal.findtext("t:UserId", namespaces=NS) == "S-1-5-18"
        assert principal.findtext("t:RunLevel", namespaces=NS) == "HighestAvailable"
        assert command == WINDOWS_POWERSHELL
        assert arguments == SYNC_ARGUMENTS
        return
    assert principal.findtext("t:UserId", namespaces=NS) == "S-1-5-19"
    assert principal.findtext("t:RunLevel", namespaces=NS) == "LeastPrivilege"
    assert command == "{{PYTHON}}"
    # Python isolated (-I before -m, contract 9.6): no PYTHON* variable can put a module on the
    # path of an evidence job.
    isolated, flag, module, command = arguments.split(" ", 3)
    assert isolated == "-I"
    assert flag == "-m"
    if path.stem in EXPECTED_DB:
        assert (module, command) == ("arcilogger_db", EXPECTED_DB[path.stem])
    else:
        assert (module, command) == ("arcilogger_core", EXPECTED[path.stem])
    parser = PARSERS[module]()
    if path.stem == "Requests" and not _has_command(parser, "requests"):
        pytest.skip("arcilogger-core requests run arrives with WP2 (contract 4.3)")
    parser.parse_args(shlex.split(command))  # the CLI accepts it


@pytest.mark.parametrize("name", sorted(WAKE_EVENTS))
def test_wake_up_through_the_application_log(name: str) -> None:
    """Seal now, the task requests and the system sync wake their task with an Event Log entry
    of ARCILOGGER-Admin (contract 2.14, 2.16); the schedule stays as the safety net."""
    root = ET.parse(TASKS / f"{name}.xml").getroot()  # noqa: S314
    triggers = root.find("t:Triggers", NS)
    assert triggers is not None
    events = triggers.findall("t:EventTrigger", NS)
    assert len(events) == 1
    assert events[0].findtext("t:Enabled", namespaces=NS) == "true"
    query = ET.fromstring(events[0].findtext("t:Subscription", namespaces=NS) or "")  # noqa: S314
    select = query.find("Query/Select")
    assert query.tag == "QueryList"
    assert select is not None
    assert select.get("Path") == "Application"
    expected = f"*[System[Provider[@Name='{EVENT_SOURCE}'] and EventID={int(WAKE_EVENTS[name])}]]"
    assert select.text == expected
    calendar = triggers.find("t:CalendarTrigger", NS)
    assert calendar is not None
    assert calendar.findtext("t:Repetition/t:Interval", namespaces=NS) == "PT5M"
    assert calendar.findtext("t:ScheduleByDay/t:DaysInterval", namespaces=NS) == "1"


def test_tasks_woken_by_requests_end_in_time() -> None:
    """The agent reports a task request without a result after 15 minutes (contract 2.9);
    ApplySystem has 10 minutes, the sealing run keeps its two hours."""
    limits = {}
    for name in WAKE_EVENTS:
        root = ET.parse(TASKS / f"{name}.xml").getroot()  # noqa: S314
        limits[name] = root.findtext("t:Settings/t:ExecutionTimeLimit", namespaces=NS)
    assert limits == {"Seal": "PT2H", "Requests": "PT15M", "ApplySystem": "PT10M"}


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
    """Register-ArciTasks.ps1 takes every XML file of the folder, so an upgrade adds Backup,
    Requests and ApplySystem. It fills in the venv's Python and the program's script folder
    (-ScriptDirectory, its default set in the body: ADR-0007 section 7) and refuses a definition
    whose script is not in that folder."""
    script = (TASKS.parent / "scripts" / "Register-ArciTasks.ps1").read_text(encoding="utf-8-sig")
    assert "Get-ChildItem -LiteralPath $TaskDirectory -Filter *.xml" in script
    assert "Register-ScheduledTask -TaskName $_.BaseName" in script
    assert '.Replace("{{PYTHON}}", $Python).Replace("{{SCRIPTS}}", $ScriptDirectory)' in script
    default = (
        'if (-not $ScriptDirectory) { $ScriptDirectory = Join-Path $PSScriptRoot "..\\scripts" }'
    )
    assert default in script
    assert "Betik bulunamadı [script not found]" in script
    used = {
        name
        for path in _tasks()
        for name in re.findall(r"\{\{SCRIPTS\}\}\\([\w.-]+\.ps1)", path.read_text("utf-8"))
    }
    assert used == {"Sync-ArciSystem.ps1"}
    assert all((TASKS.parent / "scripts" / name).is_file() for name in used)


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
        definition = path.read_text(encoding="utf-8").replace("{{PYTHON}}", sys.executable)
        scripts = str(TASKS.parent / "scripts")
        xml_file.write_text(definition.replace("{{SCRIPTS}}", scripts), encoding="utf-8")
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
