"""The logon task of the desktop status window and its place in the installer (Wave 5 contract
§5, decisions I3 and I4; ``deploy/windows/desktop/StatusWindow.xml``)."""

from __future__ import annotations

import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from arcilogger_core.desktop.__main__ import build_parser

WINDOWS = Path(__file__).parents[1] / "deploy" / "windows"
TASK = WINDOWS / "desktop" / "StatusWindow.xml"
NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
ADMINISTRATORS = "S-1-5-32-544"


def _text(root: ET.Element, path: str) -> str:
    found = root.find(path, NS)
    assert found is not None, path
    return (found.text or "").strip()


def test_the_only_desktop_definition() -> None:
    assert sorted(p.name for p in TASK.parent.iterdir()) == ["StatusWindow.xml"]


def test_status_window_task() -> None:
    """Members of Administrators, elevated (it reads state\\ without a UAC prompt), at every
    logon, as long as the person keeps the window open, one per logon."""
    root = ET.parse(TASK).getroot()  # noqa: S314 - our own repository file
    assert _text(root, "t:RegistrationInfo/t:URI") == "\\ARCILOGGER\\Desktop\\StatusWindow"
    assert root.find("t:Triggers/t:LogonTrigger", NS) is not None
    assert len(root.find("t:Triggers", NS) or []) == 1
    assert root.find("t:Triggers/t:LogonTrigger/t:UserId", NS) is None  # anyone, see GroupId
    assert _text(root, "t:Principals/t:Principal/t:GroupId") == ADMINISTRATORS
    assert root.find("t:Principals/t:Principal/t:UserId", NS) is None
    assert _text(root, "t:Principals/t:Principal/t:RunLevel") == "HighestAvailable"
    assert _text(root, "t:Settings/t:ExecutionTimeLimit") == "PT0S"
    assert _text(root, "t:Settings/t:MultipleInstancesPolicy") == "Parallel"
    assert _text(root, "t:Settings/t:Enabled") == "true"
    assert _text(root, "t:Actions/t:Exec/t:Command") == "{{PYTHONW}}"
    arguments = _text(root, "t:Actions/t:Exec/t:Arguments").split()
    assert arguments[:3] == ["-I", "-m", "arcilogger_core.desktop"]
    build_parser().parse_args(arguments[3:])  # the window takes its arguments


def test_register_script_registers_the_desktop_folder() -> None:
    script = (WINDOWS / "scripts" / "Register-ArciTasks.ps1").read_text(encoding="utf-8-sig")
    default = (
        'if (-not $DesktopDirectory) { $DesktopDirectory = Join-Path $PSScriptRoot "..\\desktop" }'
    )
    assert default in script
    assert '$PythonW = Join-Path (Split-Path -Parent $Python) "pythonw.exe"' in script
    assert '.Replace("{{PYTHONW}}", $PythonW)' in script
    assert '-TaskPath "\\ARCILOGGER\\Desktop\\"' in script
    # A program without the folder (an older version) registers no desktop task.
    assert "if (Test-Path -LiteralPath $DesktopDirectory) {" in script


def test_bundle_and_program_carry_the_desktop_folder() -> None:
    build = (WINDOWS / "installer" / "Build-ArciBundle.ps1").read_text(encoding="utf-8-sig")
    assert "Copy-Tree (Join-Path $Windows 'desktop') (Join-Path $stage 'desktop') '*.xml'" in build
    install = (WINDOWS / "installer" / "Install-Arcilogger.ps1").read_text(encoding="utf-8-sig")
    assert "@('installer', 'scripts', 'tasks', 'desktop', 'config', 'docs', 'licenses')" in install


def _function(script: str, name: str) -> str:
    start = script.index(f"function {name}")
    end = script.index("\nfunction ", start + 1)
    return script[start:end]


def test_installer_closes_and_removes_the_window() -> None:
    """The window runs Python from the program folder until someone closes it: an upgrade or a
    removal ends it before it waits for the evidence jobs (which never include it)."""
    install = (WINDOWS / "installer" / "Install-Arcilogger.ps1").read_text(encoding="utf-8-sig")
    disable = _function(install, "Disable-ArciTasks")
    assert disable.index("Stop-ArciStatusWindow") < disable.index("$deadline")
    assert "-TaskPath '\\ARCILOGGER\\' " in disable  # the wait never sees the desktop folder
    stop = _function(install, "Stop-ArciStatusWindow")
    assert "*arcilogger_core.desktop*" in stop
    assert "StartsWith($program, [System.StringComparison]::OrdinalIgnoreCase)" in stop
    assert "Stop-Process -Id $_.ProcessId -Force" in stop
    register = _function(install, "Register-ArciTaskSet")
    assert (
        "Get-ScheduledTask -TaskPath '\\ARCILOGGER\\Desktop\\' -ErrorAction SilentlyContinue "
        "| Unregister-ScheduledTask -Confirm:$false"
    ) in register
    assert "Set-ArciStatusWindowShortcut -Root $Root" in register
    # Without ProgramData (the installer's simulation on Linux pwsh) there is no Start menu, and
    # Join-Path would refuse the missing drive C:.
    folder = _function(install, "Get-StatusWindowFolder")
    assert "if (-not $env:ProgramData) { return $null }" in folder
    shortcut = _function(install, "Set-ArciStatusWindowShortcut")
    assert "$link.Arguments = '-I -m arcilogger_core.desktop'" in shortcut
    assert "Remove-ArciStatusWindowShortcut" in shortcut
    removal = install[install.index("Invoke-Step 'tasks.unregister'") :]
    removal = removal[: removal.index("Invoke-Step 'eventlog.remove'")]
    assert "Where-Object { $_.TaskPath -eq '\\ARCILOGGER\\Desktop\\' }" in removal
    assert "Remove-ArciStatusWindowShortcut" in removal


def test_the_window_runs_isolated(tmp_path: Path) -> None:
    """``python -I -m arcilogger_core.desktop`` as the task and the shortcut start it."""
    config = tmp_path / "arcilogger.toml"
    config.write_text(f'data_dir = "{(tmp_path / "data").as_posix()}"\n[api]\nport = 9\n')
    result = subprocess.run(
        [sys.executable, "-I", "-m", "arcilogger_core.desktop", "--print", "--config", str(config)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "API · ArciApi / API: " in result.stdout


@pytest.mark.skipif(sys.platform != "win32", reason="Task Scheduler is Windows-only")
def test_task_scheduler_accepts_the_definition(tmp_path: Path) -> None:
    """Register the definition in a throw-away folder (needs an administrator, e.g. CI)."""
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
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    xml_file = tmp_path / TASK.name
    xml_file.write_text(
        TASK.read_text(encoding="utf-8").replace("{{PYTHONW}}", str(pythonw)), encoding="utf-8"
    )
    script = (
        f"$xml = Get-Content -LiteralPath '{xml_file}' -Raw -Encoding UTF8; "
        "Register-ScheduledTask -TaskName 'StatusWindow' -TaskPath '\\ARCILOGGER-CI\\Desktop\\' "
        "-Xml $xml -Force | Out-Null; "
        "Unregister-ScheduledTask -TaskName 'StatusWindow' -TaskPath '\\ARCILOGGER-CI\\Desktop\\' "
        "-Confirm:$false"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
