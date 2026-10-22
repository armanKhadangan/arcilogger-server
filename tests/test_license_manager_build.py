"""The Windows zip of the Lisans Yöneticisi: ``tools/license-issuer/Build-LicenseManager.ps1`` and
``.github/workflows/license-manager.yml``.

The build itself needs Windows and the network (the workflow runs it). Here: PowerShell parses the
script, its pure helpers (dot-sourced, which runs no build) give the expected launchers, names and
texts, and the script and the workflow use the verified Python of ``components.json``, the
pinned wheels, the same pinned actions as ``ci.yml`` and no secret. ``ARCILOGGER_PWSH`` may point
at a pwsh binary; without PowerShell the PowerShell tests are skipped (``ARCILOGGER_REQUIRE_PWSH=1``
makes that a failure).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import arcilogger_core

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools" / "license-issuer"
SCRIPT = TOOLS / "Build-LicenseManager.ps1"
WORKFLOW = ROOT / ".github" / "workflows" / "license-manager.yml"
COMPONENTS = ROOT / "deploy" / "windows" / "installer" / "components.json"
GUI_LAUNCHER = "ARCILOGGER Lisans Yöneticisi.cmd"
CLI_LAUNCHER = "Lisans Aracı (komut satırı).cmd"


def _pwsh() -> str | None:
    override = os.environ.get("ARCILOGGER_PWSH")
    if override and Path(override).is_file():
        return override
    return shutil.which("pwsh") or shutil.which("powershell")


@pytest.fixture
def pwsh() -> str:
    found = _pwsh()
    if found is None:
        if os.environ.get("ARCILOGGER_REQUIRE_PWSH") == "1":
            pytest.fail("PowerShell is required (ARCILOGGER_REQUIRE_PWSH=1) but none was found")
        pytest.skip("no PowerShell available")
    return found


def run_powershell(engine: str, script: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "POWERSHELL_TELEMETRY_OPTOUT": "1", "POWERSHELL_UPDATECHECK": "Off"}
    return subprocess.run(
        [engine, "-NoProfile", "-NonInteractive", "-File", str(script), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
        env=env,
    )


def powershell_json(engine: str, tmp_path: Path, body: str) -> Any:
    """Dot-source the build script in a fresh PowerShell, run ``body`` and read its JSON."""
    probe = tmp_path / "probe.ps1"
    probe.write_text(
        "param([string]$Script)\n$ErrorActionPreference = 'Stop'\n. $Script\n" + body,
        encoding="utf-8-sig",
    )
    completed = run_powershell(engine, probe, "-Script", str(SCRIPT))
    assert completed.returncode == 0, completed.stdout + completed.stderr
    return json.loads(completed.stdout)


def test_powershell_parses_the_build_script(pwsh: str, tmp_path: Path) -> None:
    probe = tmp_path / "parse.ps1"
    probe.write_text(
        "param([string]$Script)\n"
        "$errors = $null; $tokens = $null\n"
        "[void][System.Management.Automation.Language.Parser]::ParseFile($Script, [ref]$tokens, "
        "[ref]$errors)\n"
        "$errors | ForEach-Object { $_.ToString() }\n"
        "exit $errors.Count\n",
        encoding="utf-8-sig",
    )
    completed = run_powershell(pwsh, probe, "-Script", str(SCRIPT))
    assert completed.returncode == 0, completed.stdout + completed.stderr


def test_the_pure_helpers(pwsh: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init = (ROOT / "core" / "src" / "arcilogger_core" / "__init__.py").read_text(encoding="utf-8")
    body = (
        "$init = [System.IO.File]::ReadAllText($env:ARCI_INIT)\n"
        "$version = Get-LmProductVersion -InitText $init\n"
        "$bad = @()\n"
        "foreach ($call in @({ Get-LmProductVersion -InitText 'no version' },"
        " { Get-LmPackageName -Version '1 0' },"
        " { Format-LmSha256Line -Hash 'abc' -FileName x })) {\n"
        "  try { & $call | Out-Null; $bad += 'accepted' } catch { $bad += 'refused' }\n"
        "}\n"
        "[pscustomobject]@{\n"
        "  version = $version\n"
        "  name = Get-LmPackageName -Version $version\n"
        "  gui = Get-LmGuiLauncher\n"
        "  cli = Get-LmCliLauncher\n"
        "  sha = Format-LmSha256Line -Hash ('AB' * 32) -FileName 'x.zip'\n"
        "  readme = Get-LmReadme -Version $version\n"
        "  files = $script:LmToolFiles\n"
        "  gui_name = $script:LmGuiLauncherName\n"
        "  cli_name = $script:LmCliLauncherName\n"
        "  refusals = $bad\n"
        "} | ConvertTo-Json -Depth 3\n"
    )
    monkeypatch.setenv("ARCI_INIT", str(ROOT / "core" / "src" / "arcilogger_core" / "__init__.py"))
    result = powershell_json(pwsh, tmp_path, body)
    assert result["version"] == arcilogger_core.__version__
    assert f'__version__ = "{result["version"]}"' in init
    assert result["name"] == f"ARCILOGGER-Lisans-Yoneticisi-{arcilogger_core.__version__}"
    gui = result["gui"]
    assert gui.isascii()
    assert gui.endswith('start "" "%~dp0python\\pythonw.exe" -I -m arcilogger_license_gui\r\n')
    assert gui.startswith("@echo off\r\n")
    cli = result["cli"]
    assert cli.isascii()
    assert cli.endswith('"%~dp0python\\python.exe" -I -m arcilogger_license_issuer %*\r\n')
    assert result["sha"] == "ab" * 32 + "  x.zip\n"
    readme = result["readme"]
    assert "\r\n" in readme
    assert "\n" not in readme.replace("\r\n", "")
    for needle in ("TÜRKÇE", "ENGLISH", GUI_LAUNCHER, CLI_LAUNCHER, "kilavuz\\README.md",
                   "--self-test", "keys.py", result["name"],
                   "adres çubuğuna cmd", "address bar"):  # fmt: skip
        assert needle in readme, needle
    assert result["files"] == [
        "arcilogger_license_issuer.py", "arcilogger_license_gui.py", "license_manager_model.py"
    ]  # fmt: skip
    for name in result["files"]:
        assert (TOOLS / name).is_file(), name
    assert (result["gui_name"], result["cli_name"]) == (GUI_LAUNCHER, CLI_LAUNCHER)
    assert result["refusals"] == ["refused", "refused", "refused"]


def test_dot_sourcing_runs_no_build(pwsh: str, tmp_path: Path) -> None:
    """Dot-sourced, the script only defines functions (a build would stop off Windows)."""
    body = "[pscustomobject]@{ build = [bool](Get-Command Invoke-LmBuild) } | ConvertTo-Json\n"
    assert powershell_json(pwsh, tmp_path, body) == {"build": True}


def test_the_script_builds_from_the_verified_python_and_the_pinned_wheels() -> None:
    raw = SCRIPT.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf"), "Windows PowerShell 5.1 needs the BOM (Turkish text)"
    text = raw.decode("utf-8-sig")
    components = json.loads(COMPONENTS.read_text(encoding="utf-8"))["components"]
    python = next(item for item in components if item["name"] == "python")
    assert python["sha256"] not in text, "the checksum comes from components.json"
    assert "components.json" in text
    assert "Resolve-ArciComponentSource" in text  # the bundle builder's verification
    assert "[hashtable]$ComponentSha256" in text
    assert "[string]$OutDir" in text
    for needle in (
        "'build', '--package', 'arcilogger-core', '--wheel'",
        "'export', '--frozen', '--no-dev', '--package', 'arcilogger-core', '--no-emit-workspace'",
        "'--only-binary=:all:', '--platform', 'win_amd64'",
        "'--require-hashes'",
        "'install', '--no-index'",
        "Get-ArciWheelTestHelpers",
        "import tkinter",
        "CreateFromDirectory",
        "Format-LmSha256Line",
        "'BENIOKU.txt'",
        "'kilavuz'",
    ):
        assert needle in text, needle
    assert "Invoke-WebRequest" in text
    assert not re.search(r"https?://(?!docs\.astral\.sh/|\.\.\.)", text), "URLs: components.json"


def test_the_workflow_builds_unzips_self_tests_and_keeps_the_artifact() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    trigger = text[text.index("\non:\n") : text.index("\npermissions:")]
    paths = [
        "tools/license-issuer/**",
        "core/src/arcilogger_core/license/**",
        ".github/workflows/license-manager.yml",
        "deploy/windows/installer/components.json",
        "deploy/windows/installer/ArciInstall.psm1",
        "uv.lock",
        "core/pyproject.toml",
    ]
    for event in ("push", "pull_request"):
        block = re.search(rf"(?m)^  {event}:\n    paths:\n((?:      - .*\n)+)", trigger)
        assert block, event
        listed = re.findall(r"(?m)^      - '([^']+)'$", block.group(1))
        assert listed == paths, (event, listed)
    assert re.search(r"(?m)^  workflow_dispatch:$", trigger)
    assert re.search(r"(?m)^permissions:\n  contents: read$", text)
    assert "runs-on: windows-latest" in text
    assert "secrets." not in text
    assert "Build-LicenseManager.ps1" in text
    assert "Expand-Archive" in text
    assert "python\\python.exe -I -m arcilogger_license_gui --self-test" in text
    assert "python\\python.exe -I -m arcilogger_license_issuer --help" in text
    assert "name: ARCILOGGER-Lisans-Yoneticisi\n" in text
    assert ".zip.sha256" in text
    pinned = set(re.findall(r"uses:\s*(\S+@[0-9a-f]{40})", text))
    known = set()
    for other in ("ci.yml", "installer.yml"):
        known |= set(
            re.findall(r"uses:\s*(\S+@[0-9a-f]{40})", (WORKFLOW.parent / other).read_text())
        )
    assert pinned
    assert pinned <= known, pinned - known


def test_the_product_bundle_never_ships_the_licence_manager() -> None:
    installer = ROOT / "deploy" / "windows" / "installer"
    for path in (installer / "Build-ArciBundle.ps1", installer / "ARCILOGGER.iss",
                 installer / "Install-Arcilogger.ps1"):  # fmt: skip
        text = path.read_bytes().decode("utf-8-sig").lower()
        for name in ("license_gui", "license_manager", "build-licensemanager", "lisans-yoneticisi"):
            assert name not in text, (path.name, name)
