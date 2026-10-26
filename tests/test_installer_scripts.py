"""Windows installer: component manifest, PowerShell scripts, service definitions, Caddy.

The PowerShell checks run with every PowerShell found: Windows PowerShell 5.1 (``powershell``, the
production engine on Windows Server) and PowerShell 7 (``pwsh``, also on Linux CI runners).
``ARCILOGGER_PWSH`` may point at a pwsh binary. Without any PowerShell those tests are skipped,
except with ``ARCILOGGER_REQUIRE_PWSH=1`` (the installer workflow), where that is a failure.

``ARCILOGGER_CADDY`` pointing at a Caddy binary enables the Caddyfile tests against the real server
(the installer workflow downloads the pinned version and checks its SHA-256 first).
``ARCILOGGER_ISCC`` may point at Inno Setup's ``ISCC.exe`` (Windows) for the compile check of the
setup script.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import http.server
import importlib.util
import inspect
import json
import os
import re
import shutil
import socket
import ssl
import struct
import subprocess
import sys
import threading
import time
import tomllib
import urllib.error
import urllib.parse
import urllib.request
import uuid
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from io import StringIO
from pathlib import Path
from typing import Any, NoReturn

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from uvicorn.middleware.proxy_headers import _TrustedHosts  # what ArciApi trusts (cli.py)

from arcilogger_core import cli, timeutil
from arcilogger_core.admin.contract import (
    AdminPolicy,
    DocumentError,
    Event,
    JarsFile,
    SystemJournalLine,
    SystemPlan,
    SystemStatus,
    parse_document,
)
from arcilogger_core.config import ApiConfig
from arcilogger_core.rawlog import format_line
from arcilogger_core.secretstore import KAMUSM_CUSTOMER, KAMUSM_PASSWORD, SecretStore

REPO = Path(__file__).parents[1]
WINDOWS = REPO / "deploy" / "windows"
INSTALLER = WINDOWS / "installer"
SERVICES = WINDOWS / "services"
CADDYFILE = WINDOWS / "caddy" / "Caddyfile"
COMPONENTS = INSTALLER / "components.json"
MODULE = INSTALLER / "ArciInstall.psm1"
INSTALL_SCRIPT = INSTALLER / "Install-Arcilogger.ps1"

#: The interpreter flags of every Python service (contract 9.6): isolated (-I: no PYTHON*
#: variable, no user site-packages), with -X utf8 -u for the PYTHONUTF8 / PYTHONUNBUFFERED that
#: -I ignores.
ISOLATED_SERVICE_FLAGS = "-I -X utf8 -u -m "
#: Python service -> (package run with -m, command line of its console-script launcher).
SERVICE_MODULES = {
    "ArciCollector": ("arcilogger_collector", 'run --config "{{CONFIG}}"'),
    "ArciIngest": ("arcilogger_ingest", 'run --config "{{CONFIG}}"'),
    "ArciWorker": ("arcilogger_worker", 'run --config "{{CONFIG}}"'),
    "ArciApi": ("arcilogger_api", 'serve --config "{{CONFIG}}"'),
}
#: The administration agent of the browser administration (ADR-0014): a module of the core
#: package, run like the other Python services.
ADMIN_SERVICE = ("arcilogger_core.admin", 'serve --config "{{CONFIG}}"')
SERVICE_ENTRY_POINTS = {
    **{
        name: ("python.exe", f"{ISOLATED_SERVICE_FLAGS}{module} {arguments}")
        for name, (module, arguments) in {**SERVICE_MODULES, "ArciAdmin": ADMIN_SERVICE}.items()
    },
    "ArciWeb": (
        "caddy.exe",
        'run --config "{{INSTALL_DIR}}\\caddy\\Caddyfile" --adapter caddyfile',
    ),
}
# Every element documented for WinSW v2.12.0 (doc/xmlConfigFile.md and
# doc/loggingAndErrorReporting.md of that release).
WINSW_ELEMENTS = {
    "id", "name", "description", "executable", "arguments", "startarguments", "stopexecutable",
    "stoparguments", "startmode", "delayedAutoStart", "depend", "logpath", "log", "onfailure",
    "resetfailure", "stoptimeout", "env", "interactive", "beeponshutdown", "download",
    "securityDescriptor", "serviceaccount", "workingdirectory", "priority",
    "stopparentprocessfirst", "waithint", "sleeptime", "extensions",
}  # fmt: skip
WELL_KNOWN = {"system": "S-1-5-18", "local_service": "S-1-5-19", "admins": "S-1-5-32-544"}
USERS_SID = "S-1-5-32-545"
TRUSTED_INSTALLER_SID = "S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464"
BOM = b"\xef\xbb\xbf"
#: The organisation name and the admin PC of the fictional example network: the installer has
#: no default for either (product decisions D1, D3), so the dry runs pass them unless a test
#: checks what happens without them.
SITE_NAME = "Örnek A.Ş."
ADMIN_ADDRESS = "172.20.10.37"


def with_example_sources(text: str) -> str:
    """The example configuration with its commented-out [[sources]] (the fictional example
    network) switched on, as docs/DEVICE-SETUP.md tells an administrator: the "#" in front of
    those lines removed ("# " lines stay comments). The example itself configures none."""
    start = text.index("# --- Log sources")
    end = text.index("\n[collector]\n")
    return text[:start] + re.sub(r"(?m)^#(?=[^ \n]|$)", "", text[start:end]) + text[end:]


# --- helpers -------------------------------------------------------------------------------------


def _powershells() -> list[str]:
    found: list[str] = []
    if sys.platform == "win32" and (windows_ps := shutil.which("powershell")):
        found.append(windows_ps)
    override = os.environ.get("ARCILOGGER_PWSH")
    pwsh = override if override and Path(override).is_file() else shutil.which("pwsh")
    if pwsh:
        found.append(pwsh)
    return found


ENGINES = _powershells()


def _engine_id(engine: str | None) -> str:
    return Path(engine).stem.lower() if engine else "none"


ENGINE_PARAMS: list[str | None] = [*ENGINES] if ENGINES else [None]


@pytest.fixture(params=ENGINE_PARAMS, ids=_engine_id)
def engine(request: pytest.FixtureRequest) -> str:
    value: str | None = request.param
    if value is None:
        if os.environ.get("ARCILOGGER_REQUIRE_PWSH") == "1":
            pytest.fail("PowerShell is required (ARCILOGGER_REQUIRE_PWSH=1) but none was found")
        pytest.skip("no PowerShell available")
    return value


def is_windows_powershell(engine: str) -> bool:
    """Windows PowerShell 5.1 (powershell.exe), as opposed to PowerShell 7 (pwsh)."""
    return Path(engine).stem.lower() == "powershell"


def windows_powershell_module_path(value: str) -> str:
    """PSModulePath without PowerShell 7's module folders, as pwsh gives it to powershell.exe.

    pwsh removes its user, system and ``$PSHOME`` module folders only from a powershell.exe it
    starts itself; a program it started (pytest in a GitHub Actions step with ``shell: pwsh``)
    passes its PSModulePath on unchanged (about_PSModulePath, "Starting Windows PowerShell from
    PowerShell 7"). Windows PowerShell 5.1 then finds the PowerShell 7 copies of its own modules
    first and cannot load them ("The 'ConvertTo-SecureString' command was found in the module
    'Microsoft.PowerShell.Security', but the module could not be loaded"; Get-Acl too). Here every
    ``...\\PowerShell\\Modules`` folder goes (the tests need none of them) and the ``Modules``
    folder of every PowerShell 7 installation (the folder next to pwsh.dll).
    Initialize-ArciModulePath removes them in the installer scripts as well; the tests that
    start Windows PowerShell with them on purpose check that.
    """
    kept = []
    for entry in value.split(os.pathsep):
        folder = entry.strip().rstrip("\\/")
        if not folder:
            continue
        path = Path(folder)
        if path.name.lower() == "modules" and path.parent.name.lower() == "powershell":
            continue
        if (path.parent / "pwsh.dll").is_file():
            continue
        kept.append(entry.strip())
    return os.pathsep.join(kept)


def _set_variable(env: dict[str, str], name: str, value: str | None) -> None:
    """Environment names are case-insensitive on Windows (os.environ holds them upper-case)."""
    if sys.platform == "win32":
        for key in [k for k in env if k.upper() == name.upper()]:
            del env[key]
    if value is None:
        env.pop(name, None)
    else:
        env[name] = value


def powershell_environment(engine: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    _set_variable(env, "POWERSHELL_TELEMETRY_OPTOUT", "1")
    _set_variable(env, "POWERSHELL_UPDATECHECK", "Off")
    if is_windows_powershell(engine):
        inherited = next((v for k, v in env.items() if k.upper() == "PSMODULEPATH"), "")
        # Nothing left: Windows PowerShell builds its default module path itself.
        _set_variable(env, "PSModulePath", windows_powershell_module_path(inherited) or None)
    for name, value in (extra or {}).items():
        _set_variable(env, name, value)
    return env


def run_powershell(
    engine: str,
    script: Path,
    *args: str,
    timeout: int = 600,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    command = [engine, "-NoProfile", "-NonInteractive"]
    if sys.platform == "win32":
        command += ["-ExecutionPolicy", "Bypass"]
    command += ["-File", str(script), *args]
    env = powershell_environment(engine, env_extra)
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        env=env,
    )


def write_script(path: Path, text: str) -> Path:
    path.write_text(text, encoding="utf-8-sig")
    return path


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def write_sums(root: Path) -> None:
    lines = []
    for file in sorted(p for p in root.rglob("*") if p.is_file() and p.name != "SHA256SUMS"):
        lines.append(f"{sha256_bytes(file.read_bytes())}  {file.relative_to(root).as_posix()}")
    (root / "SHA256SUMS").write_bytes(("\n".join(lines) + "\n").encode())


def service_sid(name: str) -> str:
    digest = hashlib.sha1(name.upper().encode("utf-16-le")).digest()  # noqa: S324 - Windows' own derivation
    return "S-1-5-80-" + "-".join(str(part) for part in struct.unpack("<5I", digest))


def scram_verifier(password: str, salt: bytes, iterations: int = 4096) -> str:
    """Reference implementation (RFC 5802 / RFC 7677) for the PowerShell one."""
    salted = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.new(salted, b"Server Key", "sha256").digest()

    def b64(data: bytes) -> str:
        return base64.b64encode(data).decode()

    return f"SCRAM-SHA-256${iterations}:{b64(salt)}${b64(stored_key)}:{b64(server_key)}"


# --- components.json -----------------------------------------------------------------------------


def _components() -> list[dict[str, Any]]:
    data: dict[str, Any] = json.loads(COMPONENTS.read_text(encoding="utf-8"))
    components: list[dict[str, Any]] = data["components"]
    return components


def _is_placeholder(value: str) -> bool:
    return len(set(value)) <= 2 or value in "0123456789abcdef" * 4 or value == value[:16] * 4


def test_components_schema() -> None:
    data = json.loads(COMPONENTS.read_text(encoding="utf-8"))
    roles = set(data["roles"])
    assert roles == {"bundled", "operator"}
    names = [c["name"] for c in data["components"]]
    assert len(names) == len(set(names))
    checksums = [c["sha256"] for c in data["components"] if c["sha256"]]
    assert len(checksums) == len(set(checksums)), "two components share one checksum"
    for component in data["components"]:
        name = component["name"]
        assert component["role"] in roles, name
        for field in ("title", "version", "licence", "sha256_source"):
            assert isinstance(component[field], str), (name, field)
            assert component[field].strip(), (name, field)
        for field in ("licence_url", "download_page"):
            assert component[field].startswith("https://"), (name, field)
        url = component["url"]
        assert url is None or url.startswith("https://"), name
        sha256 = component["sha256"]
        if sha256 is None:
            assert component["verified_on"] is None, name
            assert component["sha256_source"].startswith("Not "), f"{name}: say why not"
        else:
            assert re.fullmatch(r"[0-9a-f]{64}", sha256), name
            assert not _is_placeholder(sha256), name
            assert url is not None, f"{name}: a checksum without a download URL"
            date.fromisoformat(component["verified_on"])
            assert component["file"] in url.replace("%2B", "+"), name
        sha512 = component.get("vendor_sha512")
        assert sha512 is None or re.fullmatch(r"[0-9a-f]{128}", sha512), name
        vendor_sums = component.get("vendor_checksum_url")
        assert vendor_sums is None or vendor_sums.startswith("https://"), name
        licence_sha256 = component["licence_sha256"]
        if licence_sha256 is not None:
            assert re.fullmatch(r"[0-9a-f]{64}", licence_sha256), name
            assert not _is_placeholder(licence_sha256), name
            # A checksum only makes sense for a file that cannot change: a tag, never a branch.
            assert not re.search(r"/(main|master|[A-Z0-9_]+_STABLE)/", component["licence_url"]), (
                name
            )


def test_bundled_components_carry_verified_licence_texts() -> None:
    """MIT and Apache-2.0 require the licence text with every copy (WinSW is installed 5 times)."""
    for component in _components():
        if component["role"] == "bundled":
            assert component["licence_sha256"], component["name"]
    build = (INSTALLER / "Build-ArciBundle.ps1").read_text(encoding="utf-8-sig")
    assert "function Get-VerifiedLicence" in build
    assert "licence_sha256" in build
    assert "'licenses/' + $licence.FileName" in build
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert "'installer', 'scripts', 'tasks', 'config', 'docs', 'licenses'" in script
    assert "THIRD-PARTY-NOTICES.txt" in script


def test_timescaledb_is_opt_in_until_the_licence_decision() -> None:
    build = (INSTALLER / "Build-ArciBundle.ps1").read_text(encoding="utf-8-sig")
    assert "[switch]$IncludeTimescaleDB" in build
    assert "$component.name -eq 'timescaledb' -and -not $IncludeTimescaleDB" in build
    timescale = next(c for c in _components() if c["name"] == "timescaledb")
    assert "Timescale License" in timescale["licence"]


def test_bundled_components_are_pinned_verified_release_downloads() -> None:
    bundled = {c["name"]: c for c in _components() if c["role"] == "bundled"}
    assert set(bundled) == {"python", "winsw", "caddy"}
    for component in bundled.values():
        assert component["required"] is True
        assert component["sha256"]
        assert component["url"]
        # Fixed release assets only: never "latest" or a moving branch.
        assert "/releases/download/" in component["url"]
        assert "latest" not in component["url"]
    assert bundled["python"]["version"].startswith("3.12.")
    assert "x86_64-pc-windows-msvc-install_only" in bundled["python"]["url"]
    assert bundled["winsw"]["version"] == "2.12.0"
    assert bundled["winsw"]["url"].endswith("/v2.12.0/WinSW.NET461.exe")
    caddy = bundled["caddy"]
    assert f"/v{caddy['version']}/caddy_{caddy['version']}_windows_amd64.zip" in caddy["url"]
    assert caddy["licence"] == "Apache-2.0"
    assert bundled["winsw"]["licence"] == "MIT"


def test_unverified_components_are_operator_installed() -> None:
    components = {c["name"]: c for c in _components()}
    for component in components.values():
        if component["sha256"] is None:
            assert component["role"] == "operator", component["name"]
    # Could not be verified from the build environment: must stay null until someone does.
    assert components["postgresql"]["sha256"] is None
    assert components["zamane"]["sha256"] is None
    assert components["zamane"]["url"] is None
    assert components["timescaledb"]["required"] is False  # ARCILOGGER runs on plain PostgreSQL


# --- script files --------------------------------------------------------------------------------


def _powershell_files() -> list[Path]:
    return sorted([*WINDOWS.rglob("*.ps1"), *WINDOWS.rglob("*.psm1")])


@pytest.mark.parametrize("path", _powershell_files(), ids=lambda p: p.name)
def test_powershell_file_is_utf8_bom_crlf(path: Path) -> None:
    data = path.read_bytes()
    assert data.startswith(BOM), "Windows PowerShell 5.1 reads files without BOM as ANSI"
    body = data[len(BOM) :]
    body.decode("utf-8")
    assert b"\n" in body
    assert body.count(b"\n") == body.count(b"\r\n"), "every line must end with CRLF"


def test_inno_script_is_utf8_with_bom() -> None:
    data = (INSTALLER / "ARCILOGGER.iss").read_bytes()
    assert data.startswith(BOM), "Inno Setup reads non-ASCII text correctly only with a BOM"
    text = data[len(BOM) :].decode("utf-8")
    assert 'MessagesFile: "compiler:Languages\\Turkish.isl"' in text
    assert 'MessagesFile: "compiler:Default.isl"' in text
    assert "Install-Arcilogger.ps1" in text
    assert "-Uninstall" in text
    assert not re.search(r"(?im)^\s*Source:.*\.(exe|jar|msi|zip)\"", text), "no binaries"


def test_setup_counts_exit_0_or_1_only_when_this_bundle_is_recorded() -> None:
    """PowerShell also ends with 1 when a script stops before its first line (ADR-0007, section 7).

    After exit code 0 or 1 the setup .exe looks for its own bundle's fingerprint in VERSION.json,
    computed as the installer computes it: the SHA-256 of the bundle's SHA256SUMS.
    """
    text = (INSTALLER / "ARCILOGGER.iss").read_bytes()[len(BOM) :].decode("utf-8")
    assert "GetSHA256OfFile(ExpandConstant('{tmp}\\bundle\\SHA256SUMS'))" in text
    assert "ExpandConstant('{commonpf64}\\ARCILOGGER\\VERSION.json')" in text
    run = text[text.index("procedure RunArciInstaller") :]
    run = run[: run.index("\nend;")]
    assert run.index("Fingerprint := BundleFingerprint()") < run.index("Exec(")
    assert run.index("(ResultCode <> 0) and (ResultCode <> 1)") < run.index(
        "not BundleIsInstalled(Fingerprint)"
    )
    assert run.index("not BundleIsInstalled(Fingerprint)") < run.index("ResultCode = 1")
    module = MODULE.read_text(encoding="utf-8-sig")
    assert "$sumsPath = Join-Path $BundleRoot 'SHA256SUMS'" in module
    assert "Fingerprint = (Get-ArciFileSha256 -Path $sumsPath)" in module
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    record = installer[installer.index("Invoke-Step 'version.write'") :]
    record = record[: record.index("'VERSION.json'")]
    assert re.search(r"fingerprint\s*=\s*\$script:BundleInfo\.Fingerprint", record), record


def _iss() -> str:
    return (INSTALLER / "ARCILOGGER.iss").read_bytes()[len(BOM) :].decode("utf-8")


def _pascal(text: str, header: str) -> str:
    """One routine of the [Code] section, from its header to its closing "end;" at column 0."""
    begin = text.index(header + "(")
    return text[begin : text.index("\nend;", begin)].replace("\r\n", "\n")


#: The first statement of InstallerParameters: what the setup always ran (-Auto only).
SETUP_DEFAULT_PARAMETERS = (
    "Result := '-NoProfile -ExecutionPolicy Bypass -File \"' + "
    "ExpandConstant('{tmp}\\bundle\\installer\\Install-Arcilogger.ps1') + '\" -Auto';"
)


def setup_command_line(script: str, license_file: str | None, allow_uncovered: bool) -> str:
    """The PowerShell command line InstallerParameters builds (its statements are pinned by
    test_setup_passes_its_licence_switches_to_the_script)."""
    line = f'-NoProfile -ExecutionPolicy Bypass -File "{script}" -Auto'
    if license_file:
        line += f' -LicenseFile "{license_file}"'
    if allow_uncovered:
        line += " -AllowUncoveredUpgrade"
    return line


def windows_argv(command_line: str) -> list[str]:
    """Split a command line as the Microsoft C runtime does (powershell.exe's own argv):
    2n backslashes before a quote give n and toggle quoting, 2n+1 give n and a literal quote,
    other backslashes are literal; "" inside quotes is one literal quote."""
    args: list[str] = []
    current: list[str] = []
    quoted = started = False
    i = 0
    while i < len(command_line):
        char = command_line[i]
        if char == "\\":
            end = i
            while end < len(command_line) and command_line[end] == "\\":
                end += 1
            count = end - i
            started = True
            if end < len(command_line) and command_line[end] == '"':
                current.append("\\" * (count // 2))
                if count % 2:
                    current.append('"')
                    end += 1
            else:
                current.append("\\" * count)
            i = end
        elif char == '"':
            started = True
            if quoted and command_line[i + 1 : i + 2] == '"':
                current.append('"')
                i += 2
            else:
                quoted = not quoted
                i += 1
        elif char in " \t" and not quoted:
            if started:
                args.append("".join(current))
                current, started = [], False
            i += 1
        else:
            current.append(char)
            started = True
            i += 1
    if started:
        args.append("".join(current))
    return args


def test_windows_argv_follows_the_c_runtime_rules() -> None:
    assert windows_argv(r'-File "C:\a b\c.ps1" -Auto') == ["-File", r"C:\a b\c.ps1", "-Auto"]
    assert windows_argv(r'"C:\dir\\" x') == ["C:\\dir\\", "x"]
    assert windows_argv(r'"a\"b" "c""d"') == ['a"b', 'c"d']
    assert windows_argv(r'C:\a\b "" end') == [r"C:\a\b", "", "end"]


def test_setup_passes_its_licence_switches_to_the_script() -> None:
    """C-6: a customer with only the setup .exe can pass -LicenseFile and -AllowUncoveredUpgrade:
    /LICENSEFILE="<file>" and /ALLOWUNCOVEREDUPGRADE (any case, / or -) are read from the
    command line in InitializeSetup and appended to -Auto; without them the command line is the
    old one. A /LICENSEFILE that names no file stops the setup before anything is copied."""
    text = _iss()
    parameters = _pascal(text, "function InstallerParameters")
    statements = [line.strip() for line in parameters.splitlines()[2:]]
    assert statements == [
        SETUP_DEFAULT_PARAMETERS,
        "if LicenseFileArgument <> '' then",
        "Result := Result + ' -LicenseFile \"' + LicenseFileArgument + '\"';",
        "if AllowUncoveredUpgrade then",
        "Result := Result + ' -AllowUncoveredUpgrade';",
    ]
    run = _pascal(text, "procedure RunArciInstaller")
    assert "Parameters := InstallerParameters();" in run
    assert run.index("Parameters := InstallerParameters();") < run.index("Exec(")
    assert "-Auto" not in run, "one place builds the command line"
    # Read from the command line: every parameter, case-insensitive, "/" or "-".
    prefix = _pascal(text, "function HasSwitchPrefix")
    assert "(Copy(Param, 1, 1) = '/') or (Copy(Param, 1, 1) = '-')" in prefix
    for name in ("function SwitchGiven", "function SwitchValue"):
        routine = _pascal(text, name)
        assert "for I := 1 to ParamCount do" in routine, name
        assert "ParamStr(I)" in routine, name
    assert "Lowercase(Copy(Param, 2, Length(Param))) = Lowercase(Name)" in _pascal(
        text, "function IsSwitch"
    )
    value = _pascal(text, "function SwitchValue")
    assert "Head := Lowercase(Name) + '=';" in value
    assert "Result := Trim(Copy(Param, Length(Head) + 2, Length(Param)));" in value
    initialize = _pascal(text, "function InitializeSetup")
    assert "AllowUncoveredUpgrade := SwitchGiven('ALLOWUNCOVEREDUPGRADE');" in initialize
    assert "Given := SwitchValue('LICENSEFILE', Found);" in initialize
    assert "LicenseFileArgument := ResolveLicenseFile(Given);" in initialize
    refused = initialize[initialize.index("if LicenseFileArgument = '' then") :]
    assert "CustomMessage('LicenseFileMissing'), [Given]" in refused
    assert "Result := False;" in refused
    # Quoted safely: a full path to an existing file, never one with a double quote (Windows
    # file names cannot hold one), so '"' + path + '"' is always exactly one argument.
    resolve = _pascal(text, "function ResolveLicenseFile")
    assert "if (Value = '') or (Pos('\"', Value) > 0) then\n    Exit;" in resolve
    assert resolve.count("FileExists(Candidate)") == 2
    assert "Candidate := ExpandFileName(Value);" in resolve
    assert "ExpandFileName(AddBackslash(ExpandConstant('{src}')) + Value)" in resolve
    for language in ("turkish", "english"):
        message = re.search(rf"(?m)^{language}\.LicenseFileMissing=(.+)\r?$", text)
        assert message is not None, language
        assert '"%1" (/LICENSEFILE)' in message.group(1)
        assert '/LICENSEFILE="C:\\Kurulum\\ARCI-L-....lic"' in message.group(1)
    # Every message the code uses exists in both languages.
    for name in sorted(set(re.findall(r"CustomMessage\('(\w+)'\)", text))):
        for language in ("turkish", "english"):
            assert re.search(rf"(?m)^{language}\.{name}=", text), (language, name)
    # The script takes exactly these parameters (a misspelt one stops it before its first line).
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    params = script[script.index("param(") : script.index("$ErrorActionPreference")]
    assert "[string]$LicenseFile," in params
    assert "[switch]$AllowUncoveredUpgrade," in params
    assert "[switch]$Auto," in params


def _iscc() -> str | None:
    """Inno Setup's compiler: ARCILOGGER_ISCC, or where Build-ArciBundle.ps1 looks for it."""
    override = os.environ.get("ARCILOGGER_ISCC")
    if override:
        return override
    for variable in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(variable)
        if base and (Path(base) / "Inno Setup 6" / "ISCC.exe").is_file():
            return str(Path(base) / "Inno Setup 6" / "ISCC.exe")
    return shutil.which("iscc")


@pytest.mark.skipif(sys.platform != "win32", reason="Inno Setup runs on Windows only")
def test_inno_script_compiles(tmp_path: Path) -> None:
    """ISCC compiles the setup and its [Code] section (an unknown function fails the compile).

    Needs Inno Setup 6.3 or newer, as Build-ArciBundle.ps1 does; skipped where it is not installed
    (the CI bundle build uses -NoInno).
    """
    iscc = _iscc()
    if iscc is None:
        pytest.skip(
            "Inno Setup 6 (ISCC.exe) not found; ARCILOGGER_ISCC=<ISCC.exe> enables this test"
        )
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "SHA256SUMS").write_text("", encoding="utf-8")
    out = tmp_path / "out"
    command = [
        iscc,
        "/Q",
        f"/DBundleDir={bundle}",
        "/DAppVersion=0.0.0",
        f"/O{out}",
        str(INSTALLER / "ARCILOGGER.iss"),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, errors="replace", timeout=600, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert (out / "ARCILOGGER-Setup-0.0.0.exe").is_file()


_PARSE_SCRIPT = r"""
param([string]$ListPath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
# Windows PowerShell 5.1 would otherwise write arrays as {"value": [...], "Count": n}.
if ($PSVersionTable.PSVersion.Major -lt 6) {
    Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue
}
$parser = [System.Management.Automation.Language.Parser]
$results = @()
foreach ($path in [System.IO.File]::ReadAllLines($ListPath)) {
    if (-not $path) { continue }
    $tokens = $null
    $errors = $null
    $ast = $parser::ParseFile($path, [ref]$tokens, [ref]$errors)
    $newer = @()
    if ($PSVersionTable.PSVersion.Major -ge 7) {
        # Syntax that Windows PowerShell 5.1 (the server's PowerShell) cannot parse.
        $found = $ast.FindAll({
                param($node)
                $kind = $node.GetType().Name
                $operator = ''
                if ($node.PSObject.Properties['Operator']) { $operator = [string]$node.Operator }
                $nullConditional = $false
                if ($node.PSObject.Properties['NullConditional']) {
                    $nullConditional = [bool]$node.NullConditional
                }
                ($kind -eq 'TernaryExpressionAst') -or ($kind -eq 'PipelineChainAst') -or
                ($operator -eq 'QuestionQuestion') -or ($operator -eq 'QuestionQuestionEquals') -or
                $nullConditional
            }, $true)
        $newer = @($found | ForEach-Object {
                '{0}: {1}' -f $_.Extent.StartLineNumber, $_.Extent.Text
            })
    }
    # Windows PowerShell 5.1 leaves $PSScriptRoot empty while it evaluates the parameter defaults
    # of a script started with "powershell -File" (as the setup and the tests start the installer).
    # $PSCommandPath and $MyInvocation stay out of parameter defaults for the same reason.
    $rootDefaults = @()
    $variableAst = [System.Management.Automation.Language.VariableExpressionAst]
    $scriptPathVariables = @('PSScriptRoot', 'PSCommandPath', 'MyInvocation')
    if ($null -ne $ast.ParamBlock) {
        foreach ($parameter in $ast.ParamBlock.Parameters) {
            if ($null -eq $parameter.DefaultValue) { continue }
            $uses = @($parameter.DefaultValue.FindAll({
                        param($node)
                        ($node -is $variableAst) -and
                        ($scriptPathVariables -contains $node.VariablePath.UserPath)
                    }, $true))
            if ($uses.Count -gt 0) {
                $line = $parameter.Extent.StartLineNumber
                $rootDefaults += ('{0}: {1}' -f $line, $parameter.Extent.Text)
            }
        }
    }
    $results += [pscustomobject]@{
        path   = $path
        errors = @($errors | ForEach-Object { '{0}: {1}' -f $_.Extent.StartLineNumber, $_.Message })
        newer  = $newer
        script_root_defaults = $rootDefaults
    }
}
[pscustomobject]@{ version = $PSVersionTable.PSVersion.ToString(); results = $results } |
    ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
"""


def test_powershell_scripts_parse(engine: str, tmp_path: Path) -> None:
    files = _powershell_files()
    listing = tmp_path / "files.txt"
    listing.write_text("\n".join(str(p) for p in files) + "\n", encoding="utf-8")
    output = tmp_path / "parse.json"
    script = write_script(tmp_path / "parse.ps1", _PARSE_SCRIPT)
    result = run_powershell(engine, script, "-ListPath", str(listing), "-OutputPath", str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output)
    checked = {Path(item["path"]).name for item in report["results"]}
    assert checked == {p.name for p in files}
    for item in report["results"]:
        assert not item["errors"], (item["path"], item["errors"])
        assert not item["newer"], ("PowerShell 7-only syntax", item["path"], item["newer"])
        assert not item["script_root_defaults"], (
            "a parameter default uses $PSScriptRoot/$PSCommandPath/$MyInvocation ($PSScriptRoot is "
            "empty there under 'powershell -File' in Windows PowerShell 5.1): set it in the body",
            item["path"],
            item["script_root_defaults"],
        )


# --- service definitions -------------------------------------------------------------------------


@pytest.mark.parametrize("name", sorted(SERVICE_ENTRY_POINTS))
def test_service_definition(name: str) -> None:
    path = SERVICES / f"{name}.xml"
    root = ET.parse(path).getroot()  # noqa: S314 - our own repository file
    assert root.tag == "service"
    assert {child.tag for child in root} <= WINSW_ELEMENTS, "only documented WinSW 2.12 options"
    assert root.findtext("id") == name
    executable, arguments = SERVICE_ENTRY_POINTS[name]
    command = root.findtext("executable") or ""
    assert command.startswith("{{INSTALL_DIR}}\\")
    assert command.endswith("\\" + executable)
    if name != "ArciWeb":
        assert command == f"{{{{INSTALL_DIR}}}}\\venv\\Scripts\\{executable}"
    assert root.findtext("arguments") == arguments
    assert root.findtext("startmode") == "Automatic"
    # The virtual account is set with sc.exe: WinSW 2.12 documents no option for it.
    assert root.find("serviceaccount") is None
    failures = root.findall("onfailure")
    assert failures
    assert all(f.get("action") == "restart" for f in failures)
    assert root.findtext("logpath") == "{{LOG_DIR}}"
    log = root.find("log")
    assert log is not None
    assert log.get("mode") == "roll-by-size"
    assert int(log.findtext("sizeThreshold") or 0) > 0
    assert int(log.findtext("keepFiles") or 0) > 0
    stop = root.findtext("stoptimeout") or ""
    assert re.fullmatch(r"\d+ sec", stop)


def test_collector_starts_immediately_others_delayed() -> None:
    for name in SERVICE_ENTRY_POINTS:
        root = ET.parse(SERVICES / f"{name}.xml").getroot()  # noqa: S314
        delayed = root.find("delayedAutoStart") is not None
        assert delayed is (name != "ArciCollector"), name


def test_service_set_matches_installer_and_placeholders_are_filled() -> None:
    module = MODULE.read_text(encoding="utf-8-sig")
    declared = re.search(r"\$script:ArciServiceNames = @\(([^)]*)\)", module)
    assert declared is not None
    names = re.findall(r"'([A-Za-z]+)'", declared.group(1))
    assert names[0] == "ArciCollector", "the collector starts first"
    # ArciAdmin writes the configuration the others load: it starts last and stops first.
    assert names[-1] == "ArciAdmin"
    assert set(names) == {p.stem for p in SERVICES.glob("*.xml")} == set(SERVICE_ENTRY_POINTS)

    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    block = re.search(r"function Get-TemplateValues \{(.*?)\n\}", script, re.S)
    assert block is not None
    keys = set(re.findall(r"(?m)^\s+([A-Z_]+)\s+=", block.group(1)))
    for xml_file in SERVICES.glob("*.xml"):
        used = set(re.findall(r"\{\{([A-Z_]+)\}\}", xml_file.read_text(encoding="utf-8")))
        assert used <= keys, (xml_file.name, used - keys)


def test_python_entry_points_match_the_contract() -> None:
    declared: dict[str, str] = {}
    for package in ("core", "collector", "ingest", "api", "worker"):
        project = tomllib.loads((REPO / package / "pyproject.toml").read_text(encoding="utf-8"))
        declared.update(project["project"].get("scripts", {}))
    assert declared["arcilogger-core"] == "arcilogger_core.cli:main"
    assert declared["arcilogger-collector"] == "arcilogger_collector.cli:main"
    # Every Python service runs "python -m <package>" (isolated, contract 9.6) instead of its
    # console-script launcher: the package's __main__.py must run exactly the launcher's main.
    for module, _ in SERVICE_MODULES.values():
        package = module.removeprefix("arcilogger_")
        command = module.replace("_", "-")
        assert declared[command] == f"{module}.cli:main", command
        main = (REPO / package / "src" / module / "__main__.py").read_text(encoding="utf-8")
        assert f"from {module}.cli import main" in main, module
        assert "raise SystemExit(main())" in main, module
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert "'upgrade', '--config', $ConfigPath" in script  # arcilogger-db upgrade --config
    assert "'secret', 'set', 'database.password'" in script


TASKS = WINDOWS / "tasks"
TASK_NS = {"t": "http://schemas.microsoft.com/windows/2004/02/mit/task"}
#: A console-script launcher of a venv (arcilogger-api.exe, ...): it starts Python without -I.
LAUNCHER = re.compile(r"arcilogger-[a-z]+\.exe", re.IGNORECASE)


def test_python_services_and_tasks_run_isolated() -> None:
    """D-T10 (contract 9.6): every Python service and scheduled task runs the venv's python.exe
    isolated, so no environment variable (machine-wide or per service) can put a module, e.g. a
    sitecustomize.py that swaps the trusted licence keys, on its path; no launcher is used."""
    services = sorted(SERVICES.glob("*.xml"))
    assert {p.stem for p in services} == set(SERVICE_ENTRY_POINTS)
    for path in services:
        assert not LAUNCHER.search(path.read_text(encoding="utf-8")), path.name
        if path.stem == "ArciWeb":
            continue
        root = ET.parse(path).getroot()  # noqa: S314 - our own repository file
        executable = root.findtext("executable") or ""
        assert executable.endswith("venv\\Scripts\\python.exe"), (path.name, executable)
        arguments = root.findtext("arguments") or ""
        assert arguments.startswith("-I -X utf8 -u -m arcilogger_"), (path.name, arguments)
        names = [env.get("name") or "" for env in root.findall("env")]
        # Not even PYTHONUTF8 / PYTHONUNBUFFERED (ignored under -I: -X utf8 -u instead) or
        # PYTHONPATH: no PYTHON* variable at all.
        assert [n for n in names if n.upper().startswith("PYTHON")] == [], (path.name, names)
        assert "ARCILOGGER_CONFIG" in names, path.name
    tasks = sorted(TASKS.glob("*.xml"))
    assert len(tasks) >= 11
    for path in tasks:
        assert not LAUNCHER.search(path.read_text(encoding="utf-8")), path.name
        root = ET.parse(path).getroot()  # noqa: S314 - our own repository file
        command = root.findtext("t:Actions/t:Exec/t:Command", namespaces=TASK_NS)
        arguments = root.findtext("t:Actions/t:Exec/t:Arguments", namespaces=TASK_NS) or ""
        if path.stem == "ApplySystem":
            # The one SYSTEM task runs no Python of its own: the system's Windows PowerShell runs
            # Sync-ArciSystem.ps1 of the program folder, which runs the venv's python.exe -I.
            assert command == "%SystemRoot%\\System32\\WindowsPowerShell\\v1.0\\powershell.exe"
            assert arguments.startswith("-NoProfile -NonInteractive "), arguments
            sync = (WINDOWS / "scripts" / "Sync-ArciSystem.ps1").read_text(encoding="utf-8-sig")
            assert (
                "@('-I', '-m', 'arcilogger_core.admin', 'system-plan', '--config', $ConfigPath)"
                in sync
            )
            # The venv of the program folder the script belongs to (any -InstallDir).
            assert (
                "$Python = Join-Path (Split-Path -Parent $PSScriptRoot) 'venv\\Scripts\\python.exe'"
                in sync
            )
            continue
        assert command == "{{PYTHON}}"
        assert arguments.startswith("-I -m arcilogger_"), (path.name, arguments)
    # Register-ArciTasks.ps1 registers the definitions as they are: only {{PYTHON}} is filled in.
    register = (WINDOWS / "scripts" / "Register-ArciTasks.ps1").read_text(encoding="utf-8-sig")
    assert '.Replace("{{PYTHON}}", $Python)' in register
    assert "-Xml $xml -Force" in register


@pytest.mark.parametrize("name", sorted([*SERVICE_MODULES, "ArciAdmin"]))
def test_isolated_service_command_ignores_python_variables(name: str, tmp_path: Path) -> None:
    """The service's own interpreter flags (here with --help) start the package from the
    environment, and a sitecustomize.py on PYTHONPATH is not loaded; without -I it would be."""
    module, _ = {**SERVICE_MODULES, "ArciAdmin": ADMIN_SERVICE}[name]
    if importlib.util.find_spec(f"{module}.__main__") is None:
        pytest.skip(f"{module} has no entry point yet (WP1 delivers it, contract 4.3)")
    inject = tmp_path / "inject"
    inject.mkdir()
    marker = tmp_path / "loaded.txt"
    (inject / "sitecustomize.py").write_text(
        f"open({str(marker)!r}, 'a', encoding='utf-8').write('loaded')\n", encoding="utf-8"
    )
    env = {**os.environ, "PYTHONPATH": str(inject), "PYTHONSTARTUP": str(inject / "x.py")}
    control = subprocess.run(
        [sys.executable, "-c", "pass"], env=env, cwd=tmp_path, check=False, timeout=60
    )
    assert control.returncode == 0
    assert marker.is_file(), "the injection works without -I"
    marker.unlink()
    flags = ISOLATED_SERVICE_FLAGS.split()
    result = subprocess.run(
        [sys.executable, *flags, module, "--help"],
        env=env,
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists(), "sitecustomize.py from PYTHONPATH was loaded"


DATABASE_SECRET = "database.password"


def test_core_secret_set_reads_the_database_password_from_stdin(tmp_path: Path) -> None:
    """The exact call the installer makes: the generated password piped into ``secret set``."""
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert f"'secret', 'set', '{DATABASE_SECRET}'" in script
    config = tmp_path / "arcilogger.toml"
    config.write_text(f'data_dir = "{(tmp_path / "data").as_posix()}"\n', encoding="utf-8")
    assert cli.main(["--config", str(config), "secret", "init"]) == 0
    stdin = sys.stdin
    # A UTF-8 BOM in front (Windows PowerShell 5.1 pipes) must not become part of the password.
    sys.stdin = StringIO("\ufeffAbc123piped\r\n")
    try:
        assert cli.main(["--config", str(config), "secret", "set", DATABASE_SECRET]) == 0
    finally:
        sys.stdin = stdin
    store = SecretStore(tmp_path / "secret.key", tmp_path / "secrets.json")
    assert store.get(DATABASE_SECRET) == "Abc123piped"


# --- Caddyfile -----------------------------------------------------------------------------------


def _csp() -> dict[str, list[str]]:
    text = CADDYFILE.read_text(encoding="utf-8")
    match = re.search(r'Content-Security-Policy "([^"]+)"', text)
    assert match is not None
    directives: dict[str, list[str]] = {}
    for part in match.group(1).split(";"):
        words = part.split()
        if words:
            directives[words[0]] = words[1:]
    return directives


def test_caddyfile_security_headers_and_proxy() -> None:
    text = CADDYFILE.read_text(encoding="utf-8")
    assert re.search(r"(?m)^\s*tls internal\b", text)
    assert re.search(r"(?m)^\s*admin off$", text)
    assert re.search(r"(?m)^\s*skip_install_trust$", text)
    assert re.search(r"(?m)^\s*Strict-Transport-Security \"max-age=\d+", text)
    assert re.search(r'(?m)^\s*X-Frame-Options "DENY"$', text)
    assert re.search(r'(?m)^\s*Referrer-Policy "no-referrer"$', text)
    assert re.search(r'(?m)^\s*X-Content-Type-Options "nosniff"$', text)
    assert re.search(r"(?m)^\s*-Server$", text)
    proxy = re.search(r"handle /api/\* \{\s*reverse_proxy (\S+)\s*\}", text)
    assert proxy is not None
    assert proxy.group(1) == "{$ARCILOGGER_API_UPSTREAM:127.0.0.1:8000}"
    assert "try_files {path} /index.html" in text
    csp = _csp()
    assert csp["default-src"] == ["'self'"]
    assert csp["script-src"] == ["'self'"], "no inline or external scripts"
    assert csp["frame-ancestors"] == ["'none'"]
    assert csp["object-src"] == ["'none'"]
    for sources in csp.values():
        assert not any(s.startswith(("http:", "https:", "*")) for s in sources), "no CDNs"


CADDY = os.environ.get("ARCILOGGER_CADDY")
needs_caddy = pytest.mark.skipif(
    not CADDY or not Path(CADDY).is_file(), reason="ARCILOGGER_CADDY not set"
)


def _caddy_env(tmp_path: Path, extra: dict[str, str]) -> dict[str, str]:
    env = {
        **os.environ,
        "HOME": str(tmp_path / "home"),
        "APPDATA": str(tmp_path / "appdata"),
        "XDG_DATA_HOME": str(tmp_path / "data"),
        "XDG_CONFIG_HOME": str(tmp_path / "config"),
        "ARCILOGGER_LOG_DIR": (tmp_path / "logs").as_posix(),
    }
    env.update(extra)
    return env


@needs_caddy
def test_caddyfile_adapts_to_https_only(tmp_path: Path) -> None:
    assert CADDY is not None
    result = subprocess.run(
        [CADDY, "adapt", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
        capture_output=True,
        text=True,
        check=False,
        env=_caddy_env(tmp_path, {}),
    )
    assert result.returncode == 0, result.stderr
    config = json.loads(result.stdout)
    assert config["admin"]["disabled"] is True
    assert config["apps"]["pki"]["certificate_authorities"]["local"]["install_trust"] is False
    servers = config["apps"]["http"]["servers"]
    assert [server["listen"] for server in servers.values()] == [[":443"]], "no port 80"
    policies = config["apps"]["tls"]["automation"]["policies"]
    assert all(issuer["module"] == "internal" for p in policies for issuer in p["issuers"])


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class _ApiStub(http.server.BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = json.dumps({"status": "ok", "path": self.path}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Server", "uvicorn")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture
def api_stub() -> Iterator[int]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ApiStub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


@needs_caddy
def test_caddy_serves_spa_and_proxies_api(tmp_path: Path, api_stub: int) -> None:
    assert CADDY is not None
    web = tmp_path / "web"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>ARCILOGGER</title>", encoding="utf-8")
    (web / "assets" / "app-1a2b3c.js").write_text("console.log(1)", encoding="utf-8")
    port = _free_port()
    env = _caddy_env(
        tmp_path,
        {
            "ARCILOGGER_SITE_ADDRESSES": f"https://localhost:{port}",
            "ARCILOGGER_WEB_ROOT": web.as_posix(),
            "ARCILOGGER_API_UPSTREAM": f"127.0.0.1:{api_stub}",
        },
    )
    process = subprocess.Popen(
        [CADDY, "run", "--config", str(CADDYFILE), "--adapter", "caddyfile"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        root = tmp_path / "data" / "caddy" / "pki" / "authorities" / "local" / "root.crt"
        context: ssl.SSLContext | None = None
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if root.is_file():
                context = ssl.create_default_context(cafile=str(root))
                try:
                    with (
                        socket.create_connection(("127.0.0.1", port), timeout=2) as raw,
                        context.wrap_socket(raw, server_hostname="localhost"),
                    ):
                        break
                except OSError:
                    pass
            assert process.poll() is None, process.stderr.read() if process.stderr else ""
            time.sleep(0.3)
        assert context is not None, "Caddy did not create its local CA"

        def get(path: str) -> tuple[int, dict[str, str], bytes]:
            with urllib.request.urlopen(
                f"https://localhost:{port}{path}", context=context, timeout=10
            ) as response:
                return response.status, dict(response.headers.items()), response.read()

        status, headers, body = get("/")
        assert status == 200
        assert b"ARCILOGGER" in body
        assert headers["Cache-Control"] == "no-cache"
        for name, value in (
            ("X-Frame-Options", "DENY"),
            ("Referrer-Policy", "no-referrer"),
            ("X-Content-Type-Options", "nosniff"),
        ):
            assert headers[name] == value
        assert headers["Strict-Transport-Security"].startswith("max-age=")
        assert "script-src 'self';" in headers["Content-Security-Policy"]
        assert "Server" not in headers

        status, _, body = get("/sorgu/5651")  # deep link of the single-page app
        assert status == 200
        assert b"ARCILOGGER" in body

        status, headers, _ = get("/assets/app-1a2b3c.js")
        assert status == 200
        assert "immutable" in headers["Cache-Control"]

        status, headers, body = get("/api/v1/health")
        assert status == 200
        assert json.loads(body) == {"status": "ok", "path": "/api/v1/health"}
        assert "Server" not in headers, "the API's own Server header is removed"
        assert headers["X-Frame-Options"] == "DENY"
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


# --- PowerShell helper module --------------------------------------------------------------------

_PROBE_SCRIPT = r"""
param([string]$ModulePath, [string]$InputPath, [string]$OutputPath, [string]$Python)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
$in = [System.IO.File]::ReadAllText($InputPath) | ConvertFrom-Json
$out = [ordered]@{}

function Get-Field($Object, [string]$Name) {
    $property = $Object.PSObject.Properties[$Name]
    if ($property) { return $property.Value }
    $null
}

$out['sids'] = @($in.service_names | ForEach-Object { Get-ArciServiceSid -ServiceName $_ })

$salt = [Convert]::FromBase64String($in.scram.salt)
$out['scram'] = ConvertTo-ArciScramVerifier -Password $in.scram.password -Salt $salt `
    -Iterations $in.scram.iterations
$out['scram_random_salt'] = @(
    (ConvertTo-ArciScramVerifier -Password 'abc'),
    (ConvertTo-ArciScramVerifier -Password 'abc'))
try {
    [void](ConvertTo-ArciScramVerifier -Password "kötü'parola")
    $out['scram_rejects'] = $false
}
catch { $out['scram_rejects'] = $true }

$out['passwords'] = @(
    (New-ArciRandomPassword), (New-ArciRandomPassword), (New-ArciRandomPassword -Length 16))

$quoted = @()
foreach ($case in $in.quote_cases) {
    $quoted += , (ConvertTo-ArciArgumentString -ArgumentList @($case))
}
$out['quoted'] = $quoted
$out['quoted_list'] = ConvertTo-ArciArgumentString -ArgumentList @('a b', 'c')

$argv = @('-I', '-c', 'import json,sys; print(json.dumps(sys.argv[1:]))') + @($in.argv)
$out['roundtrip'] = (Invoke-ArciProcess -FilePath $Python -ArgumentList $argv).StdOut.Trim()

$echo = Invoke-ArciProcess -FilePath $Python -ArgumentList @('-I', '-c', $in.echo_code) `
    -StdinText $in.stdin -Environment @{ ARCI_TEST_SECRET = $in.secret } -Redact @($in.secret)
$out['echo'] = $echo.StdOut.Trim()
$failed = Invoke-ArciProcess -FilePath $Python -AllowFailure `
    -ArgumentList @('-I', '-c', 'import sys; sys.exit(3)')
$out['failure_code'] = $failed.ExitCode
$out['failure_message'] = $null
try {
    $code = 'import sys; sys.stderr.write(''boom''); sys.exit(4)'
    [void](Invoke-ArciProcess -FilePath $Python -ArgumentList @('-I', '-c', $code))
}
catch { $out['failure_message'] = $_.Exception.Message }

$out['template'] = Expand-ArciTemplate -Text '<a v="{{X}}">{{Y}}</a>' -Xml `
    -Values @{ X = 'C:\A & B'; Y = '<tag>' }
$out['template_leftover'] = $null
try { [void](Expand-ArciTemplate -Text '{{X}} {{MISSING}}' -Values @{ X = '1' }) }
catch { $out['template_leftover'] = $_.Exception.Message }

$bundles = [ordered]@{}
foreach ($property in $in.bundles.PSObject.Properties) {
    try {
        $check = Test-ArciBundle -BundleRoot $property.Value
        $bundles[$property.Name] = [ordered]@{
            ok = $check.Ok; errors = @($check.Errors); warnings = @($check.Warnings)
            files = $check.Files; fingerprint = $check.Fingerprint
        }
    }
    catch {
        $bundles[$property.Name] = [ordered]@{
            ok = $false; errors = @($_.Exception.Message); warnings = @()
            files = 0; fingerprint = $null
        }
    }
}
$out['bundles'] = $bundles

$sources = [ordered]@{}
foreach ($case in $in.components) {
    $urls = @{}
    $sums = @{}
    $name = $case.component.name
    if (Get-Field $case 'url_override') { $urls[$name] = Get-Field $case 'url_override' }
    if (Get-Field $case 'sha_override') { $sums[$name] = Get-Field $case 'sha_override' }
    try {
        $resolved = Resolve-ArciComponentSource -Component $case.component `
            -UrlOverride $urls -Sha256Override $sums
        $sources[$case.id] = [ordered]@{
            ok = $true; url = $resolved.Url; sha256 = $resolved.Sha256
            source = $resolved.Source; file = $resolved.FileName
        }
    }
    catch { $sources[$case.id] = [ordered]@{ ok = $false; error = $_.Exception.Message } }
}
$out['sources'] = $sources

$config = Read-ArciTomlFile -Python $Python -ConfigPath $in.config
$out['sources_from_config'] = @(Get-ArciSourceAddresses -Config $config)
$template = Read-ArciTomlFile -Python $Python -ConfigPath $in.template
$out['sources_from_template'] = @(Get-ArciSourceAddresses -Config $template)
$out['config_value'] = Get-ArciConfigValue -Config $config -Path 'clock.fallback_server'
$out['config_empty'] = Get-ArciConfigValue -Config $config -Path 'clock.ntp_server' -Default 'x'
$out['config_default'] = Get-ArciConfigValue -Config $config -Path 'no.such' -Default 'fallback'
$out['api_from_example'] = (Get-ArciApiEndpoint -Config $config).Address
$out['api_endpoints'] = @(foreach ($case in $in.api_endpoints) {
        $apiConfig = $null
        if ($null -ne $case) { $apiConfig = [pscustomobject]@{ api = $case } }
        try { (Get-ArciApiEndpoint -Config $apiConfig).Address } catch { 'error' }
    })
$out['api_trust'] = @(foreach ($case in $in.api_endpoints) {
        $apiConfig = $null
        if ($null -ne $case) { $apiConfig = [pscustomobject]@{ api = $case } }
        try { (Get-ArciApiEndpoint -Config $apiConfig).ProxyTrust } catch { 'error' }
    })
$out['proxy_trust'] = @(foreach ($case in $in.proxy_trust) {
        Test-ArciProxyTrust -HostName $case[0] -Allowed $case[1]
    })

# A secret on stdin must reach the child without a byte order mark, also on a UTF-8 console
# (Windows PowerShell 5.1 would otherwise write one first).
$out['console_utf8_set'] = $false
try {
    [Console]::InputEncoding = [System.Text.Encoding]::UTF8
    $out['console_utf8_set'] = $true
}
catch { $out['console_utf8_set'] = $false }
$hexCode = 'import sys; print(sys.stdin.buffer.read().hex())'
$out['stdin_hex'] = (Invoke-ArciProcess -FilePath $Python -ArgumentList @('-I', '-c', $hexCode) `
        -StdinText "Abc123`n").StdOut.Trim()
$out['console_preamble_after'] = [Console]::InputEncoding.GetPreamble().Length

$out['address_specs'] = @(foreach ($value in $in.address_specs) {
        [bool](Test-ArciAddressSpec $value)
    })
$out['address_in'] = @(foreach ($case in $in.address_in) {
        [bool](Test-ArciAddressInNetwork -Address $case[0] -Network $case[1])
    })
$states = [ordered]@{}
foreach ($property in $in.folders.PSObject.Properties) {
    $states[$property.Name] = Get-ArciProgramFolderState -Path $property.Value
}
$out['folder_states'] = $states
$out['rdp_foreign'] = @(Get-ArciForeignRdpSession -AdminAddress @('172.20.10.37'))

$pathCase = $in.module_path
$out['module_path'] = Get-ArciWindowsPowerShellModulePath -ModulePath $pathCase.value `
    -ExcludeFolder @($pathCase.exclude)
$out['module_path_expected'] = $pathCase.expected
$out['module_path_empty'] = Get-ArciWindowsPowerShellModulePath -ModulePath ''

$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 8), $utf8)
"""

QUOTE_CASES = [
    ["simple"],
    ["with space"],
    [""],
    ['quote"inside'],
    ["C:\\dir\\"],
    ["C:\\Program Files\\x\\"],
    ['a\\"b'],
    ["tab\there"],
]
QUOTE_EXPECTED = [
    "simple",
    '"with space"',
    '""',
    '"quote\\"inside"',
    "C:\\dir\\",
    '"C:\\Program Files\\x\\\\"',
    '"a\\\\\\"b"',
    '"tab\there"',
]
ROUNDTRIP_ARGV = ["plain", "two words", "", 'say "hi"', "C:\\Program Files\\ARCILOGGER\\", "ü ı ş"]
RFC7677_SALT = "W22ZaJ0SNY7soEsUEjb6gQ=="
RFC7677_VERIFIER = (
    "SCRAM-SHA-256$4096:W22ZaJ0SNY7soEsUEjb6gQ==$"
    "WG5d8oPm3OtcPnkdi4Uo7BkeZkBFzpcXkuLmtbsT4qY=:wfPLwcE6nTWhTAmQ7tl2KeoiWGPlZqQxSrmfPwDl2dU="
)


ADDRESS_SPECS = {
    "172.20.10.37": True,
    "172.20.10.192/26": True,
    "0.0.0.0/0": True,
    "2001:db8::10": True,
    "2001:db8::/32": True,
    "172.20.10": False,  # [IPAddress]::TryParse reads it as 172.20.0.10
    "10": False,  # ... as 0.0.0.10
    "127.1": False,  # ... as 127.0.0.1
    "172.20.010.1": False,
    "172.20.10.256": False,
    "10.0.0.0/33": False,
    "10.0.0.0/": False,
    "10.0.0.0/08": False,
    "2001:db8::/129": False,
    "fe80::1%3": False,
    "172.20.10.1-172.20.10.9": False,
    "LocalSubnet": False,
    "example.com": False,
    "": False,
}
ADDRESS_IN = [
    ("172.20.10.37", "172.20.10.37", True),
    ("172.20.10.81", "172.20.10.37", False),
    ("172.20.10.37", "172.20.10.0/24", True),
    ("172.20.20.37", "172.20.10.0/24", False),
    ("::ffff:172.20.10.37", "172.20.10.0/24", True),
    ("172.20.10.129", "172.20.10.128/25", True),
    ("172.20.10.127", "172.20.10.128/25", False),
    ("10.1.255.255", "10.0.0.0/15", True),
    ("10.2.0.1", "10.0.0.0/15", False),
    ("203.0.113.9", "0.0.0.0/0", True),
    ("2001:db8::5", "2001:db8::/32", True),
    ("2001:db9::5", "2001:db8::/32", False),
    ("172.20.10.37", "2001:db8::/32", False),
    ("172.20.10.37", "172.20.10", False),
    ("not an address", "172.20.10.0/24", False),
]


# [api] tables of arcilogger.toml (None: no configuration) and where ArciApi is then reached.
API_ENDPOINTS: list[tuple[dict[str, Any] | None, str]] = [
    (None, "127.0.0.1:8000"),
    ({}, "127.0.0.1:8000"),
    ({"host": "127.0.0.1", "port": 8000}, "127.0.0.1:8000"),
    ({"host": "0.0.0.0", "port": 18641}, "127.0.0.1:18641"),  # noqa: S104 - wildcard -> loopback
    ({"host": "::", "port": 8000}, "[::1]:8000"),
    ({"host": "::1", "port": 9000}, "[::1]:9000"),
    ({"host": "[2001:db8::1]", "port": 8443}, "[2001:db8::1]:8443"),
    ({"host": "localhost"}, "localhost:8000"),
    ({"port": 70000}, "error"),
    ({"port": "x"}, "error"),
    ({"host": "evil host;", "port": 1}, "error"),
]
# Whether the API then believes the client address Caddy forwards (default forwarded_allow_ips
# 127.0.0.1): Caddy connecting over ::1 would make every request come from the server itself.
API_TRUST = [
    "trusted",
    "trusted",
    "trusted",
    "trusted",
    "untrusted",
    "untrusted",
    "untrusted",
    "unknown",
    "error",
    "error",
    "error",
]

# (address Caddy connects from = the API host after Get-ArciApiEndpoint, [api]
# forwarded_allow_ips) -> Test-ArciProxyTrust. For an address the verdict must be uvicorn's own
# (checked against uvicorn.middleware.proxy_headers._TrustedHosts below).
PROXY_TRUST: list[tuple[str, str, str]] = [
    ("127.0.0.1", "127.0.0.1", "trusted"),
    ("::1", "127.0.0.1", "untrusted"),  # IPv6 loopback with the default list
    ("::1", "127.0.0.1, ::1", "trusted"),
    ("::1", "::1/128", "trusted"),
    ("::1", "0:0:0:0:0:0:0:1", "trusted"),  # another spelling of the same address
    ("127.0.0.1", "127.0.0.0/8", "trusted"),
    ("127.0.0.1", "127.0.0.1/8", "untrusted"),  # host bits set: uvicorn keeps it as text
    ("172.20.20.15", "127.0.0.1", "untrusted"),
    ("172.20.20.15", "172.20.20.0/24,127.0.0.1", "trusted"),
    ("2001:db8::1", "2001:db8::/32", "trusted"),
    ("2001:db8::1", "2001:db8::/129", "untrusted"),
    ("127.0.0.1", "*", "trusted"),
    ("10.0.0.1", "127.0.0.1,*", "untrusted"),  # "*" inside a list is only text for uvicorn
    ("127.0.0.1", "", "untrusted"),
    ("127.0.0.1", "localhost", "untrusted"),
    ("127.0.0.1", "127.000.0.1", "untrusted"),
    ("::ffff:127.0.0.1", "127.0.0.1", "untrusted"),
    ("127.0.0.1", "::ffff:127.0.0.1", "untrusted"),
    ("localhost", "127.0.0.1", "unknown"),
    ("localhost", "*", "trusted"),
]


def _program_folders(root: Path) -> dict[str, str]:
    """One folder per state Get-ArciProgramFolderState knows."""
    folders = {name: root / name for name in ("missing", "empty", "installed", "partial")}
    folders |= {name: root / name for name in ("foreign", "foreign_version", "file")}
    for name in ("empty", "installed", "partial", "foreign", "foreign_version"):
        folders[name].mkdir(parents=True)
    product = {"product": "ARCILOGGER", "version": "1.0.0", "fingerprint": "a" * 64}
    (folders["installed"] / "VERSION.json").write_text(json.dumps(product), encoding="utf-8")
    (folders["partial"] / "INSTALL-IN-PROGRESS.json").write_text(
        json.dumps(product), encoding="utf-8"
    )
    (folders["foreign"] / "important.txt").write_text("not ours", encoding="utf-8")
    (folders["foreign_version"] / "VERSION.json").write_text(
        json.dumps({"version": "5.0"}), encoding="utf-8"
    )
    folders["file"].write_text("a file, not a folder", encoding="utf-8")
    return {name: str(path) for name, path in folders.items()}


def _module_path_case(root: Path) -> dict[str, Any]:
    """An inherited PSModulePath as PowerShell 7 hands it on, and what Windows PowerShell keeps."""
    user = root / "Documents" / "PowerShell" / "Modules"
    all_users = root / "Program Files" / "PowerShell" / "Modules"
    pwsh_home = root / "PowerShell" / "7"
    pwsh_home.mkdir(parents=True)
    (pwsh_home / "pwsh.dll").write_bytes(b"")
    windows_powershell = root / "WindowsPowerShell" / "v1.0" / "Modules"
    other = root / "Some Vendor" / "Modules"
    entries = [
        str(user),
        f"{all_users}{os.sep}",
        str(pwsh_home / "Modules"),
        "",
        str(windows_powershell),
        str(user).upper() if sys.platform == "win32" else str(user),
        str(other),
    ]
    return {
        "value": os.pathsep.join(entries),
        "exclude": [str(user), str(all_users)],
        "expected": os.pathsep.join([str(windows_powershell), str(other)]),
    }


def test_reference_scram_matches_rfc7677() -> None:
    """The golden verifier reproduces the RFC 7677 exchange (ServerSignature and ClientProof)."""
    verifier = scram_verifier("pencil", base64.b64decode(RFC7677_SALT))
    assert verifier == RFC7677_VERIFIER
    stored_key = base64.b64decode(verifier.split("$")[2].split(":")[0])
    server_key = base64.b64decode(verifier.split("$")[2].split(":")[1])
    nonce = "rOprNGfwEbeRWgbNEkqO%hvYDpWUa2RaTCAfuxFIlj)hNlF$k0"
    auth = (
        f"n=user,r=rOprNGfwEbeRWgbNEkqO,r={nonce},s={RFC7677_SALT},i=4096,c=biws,r={nonce}"
    ).encode()
    signature = hmac.new(server_key, auth, "sha256").digest()
    assert base64.b64encode(signature).decode() == "6rriTRBi23WpRR/wtup+mMhUZUn/dB5nLTJRsjl95G4="
    salted = hashlib.pbkdf2_hmac("sha256", b"pencil", base64.b64decode(RFC7677_SALT), 4096)
    client_key = hmac.new(salted, b"Client Key", "sha256").digest()
    assert hashlib.sha256(client_key).digest() == stored_key
    proof = bytes(
        a ^ b
        for a, b in zip(client_key, hmac.new(stored_key, auth, "sha256").digest(), strict=True)
    )
    assert base64.b64encode(proof).decode() == "dHzbZapWIk4jUhN+Ute9ytag9zjfMHgsqmmiz7AndVQ="


def test_reference_service_sid_is_windows_derivation() -> None:
    assert service_sid("TrustedInstaller") == TRUSTED_INSTALLER_SID


def _bundle_cases(root: Path) -> dict[str, str]:
    cases: dict[str, str] = {}
    for name in ("ok", "mismatch", "missing", "unlisted", "unsafe", "empty"):
        folder = root / name
        (folder / "sub").mkdir(parents=True)
        (folder / "a.txt").write_bytes(b"alpha\n")
        (folder / "sub" / "b.bin").write_bytes(bytes(range(256)))
        if name != "empty":
            write_sums(folder)
        cases[name] = str(folder)
    (root / "mismatch" / "a.txt").write_bytes(b"alpha changed\n")
    (root / "missing" / "sub" / "b.bin").unlink()
    (root / "unlisted" / "extra.txt").write_bytes(b"not in the list")
    with (root / "unsafe" / "SHA256SUMS").open("ab") as handle:
        handle.write(f"{'0' * 64}  ../outside.txt\n".encode())
    (root / "empty" / "SHA256SUMS").write_bytes(b"")
    return cases


@pytest.fixture
def probe(engine: str, tmp_path: Path) -> dict[str, Any]:
    config = tmp_path / "arcilogger.toml"
    example = (WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8")
    # The example's devices switched on, plus a source given as a network (devices with DHCP
    # addresses, e.g. access points: docs/DEVICE-SETUP.md section 3).
    network = '\n[[sources]]\nname = "ap"\ntype = "mikrotik"\naddress = "172.20.10.192/26"\n'
    switched_on = with_example_sources(example).replace(
        "\n[collector]\n", network + "\n[collector]\n", 1
    )
    config.write_text(switched_on, encoding="utf-8")
    postgres = next(c for c in _components() if c["name"] == "postgresql")
    caddy = next(c for c in _components() if c["name"] == "caddy")
    data = {
        "service_names": ["TrustedInstaller", "ArciCollector", "ArciWeb", "arcicollector"],
        "scram": {"password": "pencil", "salt": RFC7677_SALT, "iterations": 4096},
        "quote_cases": QUOTE_CASES,
        "argv": ROUNDTRIP_ARGV,
        "echo_code": (
            "import os,sys; data=sys.stdin.read(); "
            "print(repr(data) + '|' + os.environ['ARCI_TEST_SECRET'])"
        ),
        "stdin": "line one\nSECRETVALUE\n",
        "secret": "SECRETVALUE",
        "bundles": _bundle_cases(tmp_path / "bundles"),
        "components": [
            {"id": "caddy", "component": caddy},
            {"id": "postgres_unverified", "component": postgres},
            {
                "id": "postgres_override",
                "component": postgres,
                "url_override": "https://example.invalid/postgresql-16-windows-x64.exe",
                "sha_override": "AB" * 32,
            },
            {
                "id": "bad_override",
                "component": postgres,
                "url_override": "https://example.invalid/pg.exe",
                "sha_override": "abc",
            },
            {
                "id": "plain_http",
                "component": {**caddy, "url": "http://example.invalid/caddy.zip"},
            },
        ],
        "config": str(config),
        "template": str(WINDOWS / "arcilogger.example.toml"),
        "api_endpoints": [case for case, _ in API_ENDPOINTS],
        "proxy_trust": [[address, allowed] for address, allowed, _ in PROXY_TRUST],
        "address_specs": list(ADDRESS_SPECS),
        "address_in": [[address, network] for address, network, _ in ADDRESS_IN],
        "folders": _program_folders(tmp_path / "folders"),
        "module_path": _module_path_case(tmp_path / "modulepath"),
    }
    input_path = tmp_path / "probe-in.json"
    input_path.write_text(json.dumps(data), encoding="utf-8")
    output = tmp_path / "probe-out.json"
    script = write_script(tmp_path / "probe.ps1", _PROBE_SCRIPT)
    result = run_powershell(
        engine,
        script,
        "-ModulePath",
        str(MODULE),
        "-InputPath",
        str(input_path),
        "-OutputPath",
        str(output),
        "-Python",
        sys.executable,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    probed: dict[str, Any] = read_json(output)
    return probed


def test_module_service_sids(probe: dict[str, Any]) -> None:
    trusted_installer, collector, web, lower_case = probe["sids"]
    assert trusted_installer == TRUSTED_INSTALLER_SID
    assert collector == service_sid("ArciCollector")
    assert web == service_sid("ArciWeb")
    assert lower_case == collector, "service names are case-insensitive"


def test_module_api_endpoint_follows_the_configuration(probe: dict[str, Any]) -> None:
    """Caddy's upstream, the health check and the port probe: [api] host/port, not 8000 fixed."""
    assert probe["api_endpoints"] == [expected for _, expected in API_ENDPOINTS]
    # The installer's default (no configuration yet) is what the example configuration says.
    assert probe["api_from_example"] == "127.0.0.1:8000"
    example = tomllib.loads((WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8"))
    assert (example["api"]["host"], example["api"]["port"]) == ("127.0.0.1", 8000)
    assert probe["api_trust"] == API_TRUST


def test_module_proxy_trust_follows_uvicorn(probe: dict[str, Any]) -> None:
    """[api] forwarded_allow_ips must cover the address Caddy connects from, by uvicorn's rules:
    the preflight refuses a configuration where the API would record the server's own address
    for every user (audit log, sessions, per-IP limits)."""
    assert probe["proxy_trust"] == [expected for _, _, expected in PROXY_TRUST]
    for address, allowed, expected in PROXY_TRUST:
        if expected == "unknown":
            continue  # a host name: the address is decided by name resolution
        assert (address in _TrustedHosts(allowed)) == (expected == "trusted"), (address, allowed)
    # The defaults agree: the example configuration's API host is trusted by its own list.
    example = tomllib.loads((WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8"))
    assert example["api"]["host"] in _TrustedHosts(example["api"]["forwarded_allow_ips"])
    assert ApiConfig().host in _TrustedHosts(ApiConfig().forwarded_allow_ips)


def test_module_scram_verifier(probe: dict[str, Any]) -> None:
    assert probe["scram"] == RFC7677_VERIFIER
    first, second = probe["scram_random_salt"]
    assert first != second, "a fresh random salt each time"
    for verifier in (first, second):
        salt = base64.b64decode(verifier.split("$")[1].split(":")[1])
        assert len(salt) == 16
        assert verifier == scram_verifier("abc", salt)
    assert probe["scram_rejects"] is True


def test_module_random_password(probe: dict[str, Any]) -> None:
    first, second, short = probe["passwords"]
    assert len(first) == len(second) == 40
    assert len(short) == 16
    assert first != second
    for password in (first, second, short):
        assert re.fullmatch(r"[A-Za-z0-9]+", password)


def test_module_argument_quoting(probe: dict[str, Any]) -> None:
    assert probe["quoted"] == QUOTE_EXPECTED
    assert probe["quoted_list"] == '"a b" c'
    assert json.loads(probe["roundtrip"]) == ROUNDTRIP_ARGV


def test_module_process_stdin_environment_and_redaction(probe: dict[str, Any]) -> None:
    # The secret reached the child on stdin and in its environment, but never the output.
    assert probe["echo"] == "'line one\\n***\\n'|***"
    assert probe["failure_code"] == 3
    assert "boom" in probe["failure_message"]
    assert "4" in probe["failure_message"]


def test_module_template_expansion(probe: dict[str, Any]) -> None:
    assert probe["template"] == '<a v="C:\\A &amp; B">&lt;tag&gt;</a>'
    assert "{{MISSING}}" in probe["template_leftover"]


def test_module_bundle_verification(probe: dict[str, Any], tmp_path: Path) -> None:
    bundles = probe["bundles"]
    ok = bundles["ok"]
    assert ok["ok"] is True
    assert ok["files"] == 2
    assert not ok["warnings"]
    sums = tmp_path / "bundles" / "ok" / "SHA256SUMS"
    assert ok["fingerprint"] == sha256_bytes(sums.read_bytes()), "fingerprint = SHA-256 of the list"
    assert bundles["mismatch"]["ok"] is False
    assert any("a.txt" in e for e in bundles["mismatch"]["errors"])
    assert bundles["missing"]["ok"] is False
    assert any("sub/b.bin" in e for e in bundles["missing"]["errors"])
    assert bundles["unlisted"]["ok"] is True
    assert any("extra.txt" in w for w in bundles["unlisted"]["warnings"])
    assert bundles["unsafe"]["ok"] is False
    assert any("../outside.txt" in e for e in bundles["unsafe"]["errors"])
    assert bundles["empty"]["ok"] is False, "an empty list verifies nothing"


def test_module_component_sources(probe: dict[str, Any]) -> None:
    sources = probe["sources"]
    caddy = next(c for c in _components() if c["name"] == "caddy")
    assert sources["caddy"] == {
        "ok": True,
        "url": caddy["url"],
        "sha256": caddy["sha256"],
        "source": "components.json",
        "file": caddy["file"],
    }
    assert sources["postgres_unverified"]["ok"] is False
    assert "-ComponentSha256" in sources["postgres_unverified"]["error"]
    override = sources["postgres_override"]
    assert override["ok"] is True
    assert override["sha256"] == "ab" * 32
    assert override["source"].startswith("operator-supplied")
    assert override["file"] == "postgresql-16-windows-x64.exe"
    assert sources["bad_override"]["ok"] is False
    assert sources["plain_http"]["ok"] is False
    assert "https" in sources["plain_http"]["error"]


def test_module_reads_config_with_tomllib(probe: dict[str, Any]) -> None:
    assert probe["sources_from_config"] == [
        "172.31.255.1",
        "172.20.10.1",
        "172.20.10.21",
        "172.20.10.192/26",
    ]
    # The example itself (the template of a new arcilogger.toml) configures no sender.
    assert probe["sources_from_template"] == []
    assert probe["config_value"] == "time.ume.tubitak.gov.tr"
    # The example has no local time source: an empty value is a value, not a missing key.
    assert probe["config_empty"] == ""
    assert probe["config_default"] == "fallback"


def test_module_stdin_has_no_byte_order_mark(probe: dict[str, Any]) -> None:
    """The database password is piped: a BOM in front would be stored as part of it."""
    assert probe["stdin_hex"] == b"Abc123\n".hex()
    if probe["console_utf8_set"]:
        # The console setting the operator chose is back after the child has started.
        assert probe["console_preamble_after"] == len(BOM)


def test_module_address_validation_is_strict(probe: dict[str, Any]) -> None:
    assert dict(zip(ADDRESS_SPECS, probe["address_specs"], strict=True)) == ADDRESS_SPECS


def test_module_address_containment(probe: dict[str, Any]) -> None:
    expected = [inside for _, _, inside in ADDRESS_IN]
    got = probe["address_in"]
    assert got == expected, [
        case for case, ok in zip(ADDRESS_IN, got, strict=True) if ok != case[2]
    ]
    # Runs without the NetTCPIP module too (Linux CI): an empty list, never an error.
    assert isinstance(probe["rdp_foreign"], list)
    assert all(isinstance(address, str) for address in probe["rdp_foreign"])


def test_module_drops_powershell_7_module_folders(probe: dict[str, Any], tmp_path: Path) -> None:
    """What Windows PowerShell 5.1 may load: never the PowerShell 7 copies of its modules."""
    assert probe["module_path"] == probe["module_path_expected"]
    assert probe["module_path_empty"] == ""
    # The test harness gives powershell.exe the same module path.
    case = _module_path_case(tmp_path / "harness")
    assert windows_powershell_module_path(case["value"]) == case["expected"]


def test_module_program_folder_states(probe: dict[str, Any]) -> None:
    assert probe["folder_states"] == {
        "missing": "missing",
        "empty": "empty",
        "installed": "installed",
        "partial": "partial",
        "foreign": "foreign",
        "foreign_version": "foreign",  # a VERSION.json of some other product
        "file": "foreign",
    }


# --- HTTPS health check against a pinned root ----------------------------------------------------


# --- desktop and easier-install helpers of the module (pure functions, synthetic inputs) ----------

_SUPPORT_SCRIPT = r"""
param([string]$ModulePath, [string]$InputPath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
$in = [System.IO.File]::ReadAllText($InputPath) | ConvertFrom-Json
$out = [ordered]@{}

function ConvertTo-Checks($Checks) {
    , @(foreach ($check in $Checks) {
            [ordered]@{ id = $check.Id; status = $check.Status; text = $check.Text }
        })
}

$out['server'] = @(foreach ($case in $in.server) {
        $result = Select-ArciServerAddress -Interfaces @($case.interfaces)
        [ordered]@{
            address = $result.Address; dhcp = $result.Dhcp; candidates = @($result.Candidates)
        }
    })
$out['local'] = @(foreach ($case in $in.server) {
        $addresses = Get-ArciLocalAddresses -Interfaces @($case.interfaces)
        , @($addresses)
    })
$out['bind'] = @(foreach ($case in $in.bind) {
        (Test-ArciBindAddress -Bind $case.bind -LocalAddress @($case.local)).Kind
    })
$out['bind_text'] = @(foreach ($text in $in.bind_text) {
        $value = Get-ArciCollectorBind -Text $text
        if ($null -eq $value) { 'NULL' } else { $value }
    })
$out['source_tables'] = @(foreach ($text in $in.source_tables) {
        $tables = Get-ArciSourceTables -Text $text
        [ordered]@{ tables = $tables.Tables; addresses = @($tables.Addresses) }
    })
$out['data_root'] = @(foreach ($case in $in.data_root) {
        $choice = Select-ArciDataRoot -DataDrive $case.drive -SystemDrive $case.system
        [ordered]@{ path = $choice.Path; fallback = $choice.Fallback; reason = $choice.Reason }
    })
$out['drive_checks'] = @(foreach ($case in $in.drive_checks) {
        $checks = Get-ArciDataDriveChecks -Root $case.root -Drive $case.drive -BusType $case.bus `
            -SystemDrive $case.system -MinFreeGB $case.min -Mode $case.mode
        ConvertTo-Checks $checks
    })
$out['os'] = @(foreach ($case in $in.os) {
        $checks = Get-ArciOsSupport -ProductType $case.product -Build $case.build `
            -EditionId $case.edition -Architecture $case.arch -Caption 'Windows' `
            -AllowUnsupportedWindows:([bool]$case.allow)
        ConvertTo-Checks $checks
    })
$out['powercfg'] = @(foreach ($text in $in.powercfg) {
        $parsed = ConvertFrom-ArciPowercfgQuery -Text $text
        if ($null -eq $parsed) { 'NULL' } else { [ordered]@{ ac = $parsed.Ac; dc = $parsed.Dc } }
    })
$out['scheme'] = Get-ArciPowerSchemeGuid -Text $in.scheme
$out['interactive'] = @(foreach ($line in $in.command_lines) {
        [bool](Test-ArciInteractiveConsole -CommandLine @($line))
    })
$out['interactive_here'] = [bool](Test-ArciInteractiveConsole)
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 8), $utf8)
"""


def _nic(
    name: str,
    *addresses: str,
    gateways: tuple[str, ...] = (),
    status: str = "Up",
    loopback: bool = False,
    dhcp: bool | None = False,
) -> dict[str, Any]:
    entries = [
        {"Address": a, "Family": "InterNetworkV6" if ":" in a else "InterNetwork", "Dhcp": dhcp}
        for a in addresses
    ]
    return {
        "Name": name, "Status": status, "Loopback": loopback,
        "Addresses": entries, "Gateways": list(gateways),
    }  # fmt: skip


LOOPBACK_NIC = _nic("Loopback Pseudo-Interface 1", "127.0.0.1", "::1", loopback=True)
SERVER_CASES = [
    # The only adapter with a default gateway: its single IPv4 address (DHCP flag carried).
    [LOOPBACK_NIC, _nic("Ethernet", "172.20.20.15", "fe80::1", gateways=("172.20.20.1",))],
    [LOOPBACK_NIC, _nic("Ethernet", "10.0.1.34", gateways=("10.0.1.1",), dhcp=True)],
    # Two adapters with a gateway (a second NIC, a VPN, a virtual switch): not guessed.
    [
        _nic("Ethernet", "172.20.20.15", gateways=("172.20.20.1",)),
        _nic("vEthernet (Default Switch)", "172.20.0.1", gateways=("172.20.0.254",)),
    ],
    # Two IPv4 addresses on the one adapter: not guessed either.
    [_nic("Ethernet", "172.20.20.15", "172.20.20.21", gateways=("172.20.20.1",))],
    # No gateway at all; a disconnected adapter and a link-local address do not count.
    [
        LOOPBACK_NIC,
        _nic("Ethernet", "172.20.20.15"),
        _nic("Wi-Fi", "10.0.0.5", gateways=("10.0.0.1",), status="Down"),
    ],
    [_nic("Ethernet", "169.254.10.20", "172.20.20.15", gateways=("172.20.20.1",))],
]
#: The collector's wildcard address, as arcilogger.toml writes it (data here, nothing binds).
WILDCARD = "0.0.0.0"  # noqa: S104
BIND_CASES = [
    (WILDCARD, [], "wildcard"),
    ("::", [], "wildcard"),
    ("127.0.0.1", [], "loopback"),
    ("::1", [], "loopback"),
    ("172.20.20.15", ["127.0.0.1", "172.20.20.15"], "local"),
    ("[fe80::1]", ["fe80::1"], "local"),
    ("172.20.20.15", ["127.0.0.1", "10.0.1.34"], "foreign"),
    ("arcilogger.internal", ["127.0.0.1"], "invalid"),
    ("172.20.20", ["172.20.0.20"], "invalid"),  # shorthand is never an address here
    ("172.20.20.0/24", ["172.20.20.0"], "invalid"),
    ("", [], "invalid"),
]
BIND_TEXTS = [
    (
        'data_dir = "D:/ARCILOGGER"\r\n\r\n[collector]\r\nbind = "172.20.20.15"\r\n',
        "172.20.20.15",
    ),
    ("[collector]  # syslog\nudp_port = 514\nbind = '0.0.0.0'\n", WILDCARD),
    ('[collector]\nudp_port = 514\n\n[tsa]\nbind = "10.0.0.1"\n', "NULL"),  # not in [collector]
    ('[[sources]]\nname = "x"\n[collector]\n  bind = "::"\n[api]\nhost = "127.0.0.1"\n', "::"),
    ('data_dir = "D:/ARCILOGGER"\n', "NULL"),
]
#: arcilogger.toml as text -> the [[sources]] tables Get-ArciSourceTables counts and the addresses
#: it reads, without Python (a new installation over the configuration a removal kept).
SOURCE_TABLE_TEXTS: list[tuple[str, tuple[int, list[str]]]] = [
    (
        'data_dir = "D:/ARCILOGGER"\r\n\r\n[[sources]]\r\nname = "fw"\r\ntype = "fortigate"\r\n'
        'address = "172.31.255.1"\r\n\r\n[[ sources ]]  # erişim noktaları\r\nname = "ap"\r\n'
        "address = '172.20.10.192/26'  # VLAN 10\r\n[collector]\r\nbind = \"0.0.0.0\"\r\n",
        (2, ["172.31.255.1", "172.20.10.192/26"]),
    ),
    # Commented out, as the example configuration writes them: no table.
    ('#[[sources]]\n#name = "fw"\n#address = "172.31.255.1"\n\n[collector]\n', (0, [])),
    # The address of another table is not the source's.
    ('[[sources]]\nname = "x"\n[api]\naddress = "10.0.0.9"\n', (1, [])),
    ('[collector]\naddress = "10.0.0.9"\n', (0, [])),
    # Read as written: the preflight refuses it as Set-ArciFirewall.ps1 would.
    ('[[sources]]\naddress = "172.31.255"\n', (1, ["172.31.255"])),
    # Not a plain one-line string, or empty: not seen (tomllib checks it during the installation).
    ('[[sources]]\naddress = """10.0.0.1"""\n[[sources]]\naddress = ""\n', (2, [])),
    ("", (0, [])),
]
GB = 1024**3


def _drive(
    kind: str = "Fixed", fmt: str = "NTFS", free_gb: int = 500, ready: bool = True
) -> dict[str, Any]:
    return {
        "Root": "D:\\",
        "DriveType": kind,
        "IsReady": ready,
        "DriveFormat": fmt,
        "FreeBytes": free_gb * GB,
    }


DATA_ROOT_CASES = [
    ({"drive": _drive(), "system": "C:"}, ("D:\\ARCILOGGER", False)),
    ({"drive": None, "system": "C:"}, ("C:\\ARCILOGGER", True)),
    ({"drive": _drive("CDRom", ready=False), "system": "C:"}, ("C:\\ARCILOGGER", True)),
    ({"drive": _drive("Removable", "FAT32"), "system": "C:"}, ("C:\\ARCILOGGER", True)),
    ({"drive": _drive(fmt="exFAT"), "system": "E:"}, ("E:\\ARCILOGGER", True)),
    ({"drive": _drive(ready=False), "system": ""}, ("C:\\ARCILOGGER", True)),
]


def _checks(
    root: str, drive: dict[str, Any] | None, bus: str | None = "SATA", **extra: Any
) -> dict[str, Any]:
    case = {"root": root, "drive": drive, "bus": bus, "system": "C:", "min": 100, "mode": "install"}
    return {**case, **extra}


DRIVE_CASES = [
    _checks("D:\\", _drive()),
    _checks("D:\\", _drive("Removable", "NTFS")),
    _checks("D:\\", _drive("CDRom", ready=False)),
    _checks("Z:\\", _drive("Network")),
    _checks("\\\\NAS-01\\arcilogger", None),
    _checks("R:\\", _drive("Ram")),
    _checks("D:\\", None),
    _checks("D:\\", _drive(fmt="exFAT")),
    _checks("C:\\", _drive(free_gb=60)),
    _checks("C:\\", _drive(free_gb=60), mode="upgrade"),
    _checks("D:\\", _drive(), bus="USB"),
    _checks("D:\\", _drive(), bus=None),
    _checks("C:\\", _drive()),
]
OS_CASES = [
    {"product": 3, "build": 20348, "edition": "ServerStandard", "arch": "x64", "allow": False},
    {"product": 3, "build": 26100, "edition": "ServerDatacenter", "arch": "x64", "allow": False},
    {"product": 3, "build": 17763, "edition": "ServerStandard", "arch": "x64", "allow": False},
    {"product": 1, "build": 26200, "edition": "Professional", "arch": "x64", "allow": False},
    {"product": 1, "build": 26100, "edition": "Professional", "arch": "x64", "allow": False},
    {"product": 1, "build": 22631, "edition": "Enterprise", "arch": "x64", "allow": False},
    {"product": 1, "build": 26200, "edition": "Education", "arch": "ARM64", "allow": False},
    {"product": 1, "build": 26200, "edition": "Core", "arch": "x64", "allow": False},
    {"product": 1, "build": 26200, "edition": "CoreSingleLanguage", "arch": "x64", "allow": True},
    {"product": 1, "build": 19045, "edition": "Professional", "arch": "x64", "allow": False},
    {"product": 1, "build": 19045, "edition": "Professional", "arch": "x64", "allow": True},
    {"product": 1, "build": 26200, "edition": "CloudEdition", "arch": "x64", "allow": False},
    {"product": 1, "build": 26200, "edition": "Professional", "arch": "x86", "allow": False},
    {"product": 1, "build": 19044, "edition": "EnterpriseS", "arch": "x64", "allow": False},
]
POWERCFG_STANDBY = (
    "Power Scheme GUID: 381b4222-f694-41f0-9685-ff5bb260df2e  (Balanced)\n"
    "  GUID Alias: SCHEME_BALANCED\n"
    "  Subgroup GUID: 238c9fa8-0aad-41ed-83f4-97be242c8f20  (Sleep)\n"
    "    GUID Alias: SUB_SLEEP\n"
    "    Power Setting GUID: 29f6c1db-86da-48c5-9fdb-f2b67b1f44da  (Sleep after)\n"
    "      GUID Alias: STANDBYIDLE\n"
    "      Minimum Possible Setting: 0x00000000\n"
    "      Maximum Possible Setting: 0xffffffff\n"
    "      Possible Settings increment: 0x00000001\n"
    "      Possible Settings units: Seconds\n"
    "    Current AC Power Setting Index: 0x00000708\n"
    "    Current DC Power Setting Index: 0x00000384\n"
)


@pytest.fixture
def support(engine: str, tmp_path: Path) -> dict[str, Any]:
    cases = {
        "server": [{"interfaces": interfaces} for interfaces in SERVER_CASES],
        "bind": [{"bind": bind, "local": local} for bind, local, _ in BIND_CASES],
        "bind_text": [text for text, _ in BIND_TEXTS],
        # The last one: the example configuration with its devices switched on.
        "source_tables": [text for text, _ in SOURCE_TABLE_TEXTS]
        + [with_example_sources((WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8"))],
        "data_root": [case for case, _ in DATA_ROOT_CASES],
        "drive_checks": DRIVE_CASES,
        "os": OS_CASES,
        "powercfg": [
            POWERCFG_STANDBY,
            # Localised labels change nothing; a different shape is not guessed at.
            POWERCFG_STANDBY.replace(
                "Current AC Power Setting Index", "Geçerli AC Güç Ayarı Dizini"
            ),
            "\n".join(POWERCFG_STANDBY.splitlines()[:-1]),
            "",
        ],
        "scheme": "Power Scheme GUID: 8C5E7FDA-E8BF-4A96-9A85-A6E23A8C635C  (High performance)",
        "command_lines": [
            ["pwsh", "-NonInteractive", "-File", "x.ps1"],
            ["powershell.exe", "-noni", "-File", "x.ps1"],
            [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                "x.ps1",
                "-Auto",
            ],
        ],
    }
    path = tmp_path / "support-in.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    output = tmp_path / "support-out.json"
    script = write_script(tmp_path / "support.ps1", _SUPPORT_SCRIPT)
    result = run_powershell(
        engine,
        script,
        "-ModulePath",
        str(MODULE),
        "-InputPath",
        str(path),
        "-OutputPath",
        str(output),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = read_json(output)
    return report


def test_module_detects_the_server_address_only_when_it_is_clear(support: dict[str, Any]) -> None:
    server = support["server"]
    assert [case["address"] for case in server] == [
        "172.20.20.15", "10.0.1.34", None, None, None, "172.20.20.15",
    ]  # fmt: skip
    assert server[0]["dhcp"] is False
    assert server[1]["dhcp"] is True, "a DHCP address is reported, so the preflight can warn"
    # The message lists every adapter with a gateway, so the operator can pick the right one.
    assert len(server[2]["candidates"]) == 2
    assert "vEthernet (Default Switch): 172.20.0.1" in server[2]["candidates"][1]
    assert server[3]["candidates"] == [
        "Ethernet: 172.20.20.15, 172.20.20.21 (ağ geçidi [gateway] 172.20.20.1)"
    ]
    assert server[4]["candidates"] == []
    # What the collector could listen on: up adapters and loopback, never a disconnected one.
    local = support["local"]
    assert set(local[4]) == {"127.0.0.1", "::1", "172.20.20.15"}
    assert set(local[0]) == {"127.0.0.1", "::1", "172.20.20.15", "fe80::1"}


def test_module_bind_address_must_be_one_of_this_computer(support: dict[str, Any]) -> None:
    assert support["bind"] == [kind for _, _, kind in BIND_CASES]


def test_module_reads_the_collector_bind_without_python(support: dict[str, Any]) -> None:
    assert support["bind_text"] == [value for _, value in BIND_TEXTS]


def test_module_reads_the_source_tables_without_python(support: dict[str, Any]) -> None:
    """What the sources preflight reads before Python is installed (a new installation over the
    configuration a removal kept): the [[sources]] tables and their plain addresses."""
    *cases, example = support["source_tables"]
    assert [(case["tables"], case["addresses"]) for case in cases] == [
        expected for _, expected in SOURCE_TABLE_TEXTS
    ]
    # The example with its devices switched on reads as tomllib reads it.
    text = with_example_sources((WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8"))
    sources = tomllib.loads(text)["sources"]
    assert example == {"tables": len(sources), "addresses": [s["address"] for s in sources]}


def test_module_chooses_the_data_root_of_a_new_installation(support: dict[str, Any]) -> None:
    chosen = [(case["path"], case["fallback"]) for case in support["data_root"]]
    assert chosen == [expected for _, expected in DATA_ROOT_CASES]
    reasons = [case["reason"] for case in support["data_root"]]
    assert reasons[0] is None
    assert "[no drive D:]" in reasons[1]
    assert "CDRom" in reasons[2]
    assert "exFAT" in reasons[4]


def _by_id(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {check["id"]: check for check in checks}


def test_module_data_drive_checks(support: dict[str, Any]) -> None:
    cases = [_by_id(checks) for checks in support["drive_checks"]]
    good, removable, optical, network, share, ram, missing, exfat = cases[:8]
    small_install, small_upgrade, usb, unknown_bus, system = cases[8:]
    assert {k: v["status"] for k, v in good.items()} == {
        "ntfs": "ok",
        "space": "ok",
        "disk-bus": "ok",
    }
    for refused, word in (
        (removable, "[removable drive]"), (optical, "[optical drive]"),
        (network, "[network drive or share]"), (share, "[network drive or share]"),
        (ram, "[RAM disk]"), (missing, "[data drive missing]"),
    ):  # fmt: skip
        assert set(refused) == {"drive"}, refused
        assert refused["drive"]["status"] == "fail"
        assert word in refused["drive"]["text"]
        assert "-DataRoot" in refused["drive"]["text"], "says how to fix it"
    assert exfat["ntfs"]["status"] == "fail"
    # 100 GB because the evidence is kept for two years: the reason is in the message.
    assert small_install["space"]["status"] == "fail"
    assert "730" in small_install["space"]["text"]
    assert "-MinFreeGB" in small_install["space"]["text"]
    assert small_upgrade["space"]["status"] == "warn"
    assert usb["disk-bus"]["status"] == "warn"
    assert "[USB-attached disk]" in usb["disk-bus"]["text"]
    assert unknown_bus["disk-bus"]["status"] == "unknown", "no Storage module: no claim"
    assert system["system-disk"]["status"] == "warn"
    assert "[evidence shares the system disk]" in system["system-disk"]["text"]
    assert "[replica]" in system["system-disk"]["text"], "points to the second copy"
    assert "system-disk" not in good


def test_module_os_support_policy(support: dict[str, Any]) -> None:
    cases = [_by_id(checks) for checks in support["os"]]
    statuses = [case["os"]["status"] for case in cases]
    assert statuses == [
        "ok", "ok", "warn",                     # Server 2022, 2025; 2019 only warns
        "ok", "warn", "warn", "ok",             # Pro 25H2; 24H2 and 23H2 may be out of servicing
        "fail", "warn",                         # Home; Home with -AllowUnsupportedWindows
        "fail", "warn",                         # Windows 10; with -AllowUnsupportedWindows
        "warn", "ok",                           # an unknown edition; x86 is the architecture check
        "fail",                                 # Windows 10 Enterprise LTSC 2021 as well
    ]  # fmt: skip
    assert "Microsoft'un destek tarihlerini kontrol edin" in cases[4]["os"]["text"]
    assert "-AllowUnsupportedWindows" in cases[7]["os"]["text"]
    assert "-AllowUnsupportedWindows" in cases[9]["os"]["text"]
    # Windows 10 is refused by product policy. The reason given is no Microsoft end-of-support
    # date: that differs by edition (LTSC 2021, build 19044, is serviced on a fixed lifecycle).
    for case in (cases[9], cases[10], cases[13]):
        text = case["os"]["text"]
        assert "Windows 10 desteklenmiyor [Windows 10 is not supported]" in text
        assert "Windows 11 Pro, Enterprise, Education ya da Windows Server 2022/2025" in text
        assert not re.search(r"\d{1,2}\.\d{1,2}\.\d{4}|sona erdi|end of support", text), text
    assert cases[6]["os-arch"]["status"] == "warn", "ARM64: x64 programs under emulation"
    assert cases[12]["os-arch"]["status"] == "fail"
    licence = "Windows lisans koşullarını bir Microsoft iş ortağıyla doğrulayın"
    for case in cases[3:]:
        assert case["os-licence"]["status"] == "info"
        assert licence in case["os-licence"]["text"]
    for case in cases[:3]:
        assert "os-licence" not in case, "the client licence note is for client Windows only"


def test_module_os_policy_cut_off_is_a_table() -> None:
    """The serviced-build cut-off is data to review with every release, not logic."""
    module = MODULE.read_text(encoding="utf-8-sig")
    table = re.search(r"\$script:ArciClientServicedBuild = @\{([^}]*)\}", module)
    assert table is not None
    assert dict(re.findall(r"(\w+) = (\d+)", table.group(1))) == {
        "Pro": "26200", "Enterprise": "26200", "Education": "26200",
    }  # fmt: skip
    assert "[hashtable]$ServicedBuild" in module


def test_module_parses_powercfg_output_by_numbers_only(support: dict[str, Any]) -> None:
    assert support["powercfg"] == [{"ac": 1800, "dc": 900}, {"ac": 1800, "dc": 900}, "NULL", "NULL"]
    assert support["scheme"] == "8c5e7fda-e8bf-4a96-9a85-a6e23a8c635c"


def test_module_knows_when_nobody_sits_at_the_console(support: dict[str, Any]) -> None:
    """-NonInteractive (and its abbreviation) or redirected output: no question, no token."""
    assert support["interactive"][:2] == [False, False]
    # The tests run PowerShell with -NonInteractive and captured output.
    assert support["interactive_here"] is False


# --- setup values: arcilogger.toml strings, names, time servers, the admin PC --------------------

_SETUP_HELPERS_SCRIPT = r"""
param([string]$ModulePath, [string]$InputPath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
$in = [System.IO.File]::ReadAllText($InputPath, [System.Text.Encoding]::UTF8) | ConvertFrom-Json
$out = [ordered]@{}

function Invoke-Case([scriptblock]$Block) {
    try { [ordered]@{ value = (& $Block); error = $null } }
    catch { [ordered]@{ value = $null; error = $_.Exception.Message } }
}
function ConvertTo-Text($Value) { if ($null -eq $Value) { 'NULL' } else { [string]$Value } }

$out['get'] = @(foreach ($case in $in.get) {
        Invoke-Case { Get-ArciTomlString -Text $case.text -Section $case.section -Key $case.key }
    })
$out['set'] = @(foreach ($case in $in.set) {
        Invoke-Case {
            Set-ArciTomlString -Text $case.text -Section $case.section -Key $case.key `
                -Value $case.value
        }
    })
$out['quote'] = @(foreach ($value in $in.quote) { ConvertTo-ArciTomlString -Value $value })
$out['site'] = @(foreach ($value in $in.site) {
        ConvertTo-Text (Get-ArciSiteNameProblem -Value $value)
    })
$out['time'] = @(foreach ($value in $in.time) {
        ConvertTo-Text (Get-ArciTimeServerProblem -Value $value)
    })
$out['peers'] = @(foreach ($case in $in.peers) {
        Invoke-Case { Get-ArciTimePeerList -Primary $case.primary -Fallback $case.fallback }
    })
# Answers typed at the console: a global Read-Host is what the module's code calls.
function global:Read-Host {
    param([Parameter(Position = 0)][string]$Prompt)
    $global:ArciPrompts.Add($Prompt)
    if ($global:ArciAnswers.Count -eq 0) { throw 'no console input' }
    $global:ArciAnswers.Dequeue()
}
$out['admin'] = @(foreach ($case in $in.admin) {
        $global:ArciAnswers = New-Object 'System.Collections.Generic.Queue[string]'
        $global:ArciPrompts = New-Object 'System.Collections.Generic.List[string]'
        foreach ($answer in @($case)) { $global:ArciAnswers.Enqueue([string]$answer) }
        $values = @(Read-ArciAdminAddress)
        [ordered]@{ values = $values; prompts = $global:ArciPrompts.ToArray() }
    })
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 6), $utf8)
"""

EXAMPLE_TEXT = (WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8")
QUOTED_NAME = 'Örnek "Kalite" \\ A.Ş.'
TOML_GETS = [
    # (text, section, key, value or None, error fragment or None)
    ('site_name = "Örnek \\"Kalite\\" A.Ş."\ndata_dir = "x"\n', "", "site_name",
     'Örnek "Kalite" A.Ş.', None),
    ("site_name = 'C:\\x'   # literal\n", "", "site_name", "C:\\x", None),
    ('site_name = "a\\tb\\u00e7\\\\"\n', "", "site_name", "a\tbç\\", None),
    ('data_dir = "x"\n[clock]\nsite_name = "not top level"\n', "", "site_name", None, None),
    ('[clock]\r\nntp_server = "10.0.0.1"\r\nfallback_server = "a"\r\n', "clock", "ntp_server",
     "10.0.0.1", None),
    ('[clock.extra]\na = 1\n[clock]\nntp_server = "b"\n', "clock", "ntp_server", "b", None),
    ('data_dir = "x"\n', "clock", "ntp_server", None, None),
    ('clock.ntp_server = "x"\n', "clock", "ntp_server", None, "[written as dotted keys"),
    ('clock = { ntp_server = "x" }\n', "clock", "ntp_server", None, "[written as dotted keys"),
    ('[clock]\nx = 1\n[clock]\nntp_server = "a"\n', "clock", "ntp_server", None, "[the table"),
    ('site_name = "a"\nsite_name = "b"\n', "", "site_name", None, "[the key appears twice]"),
    ('site_name = """a"""\n', "", "site_name", None, "[not a one-line string"),
    ("site_name = 42\n", "", "site_name", None, "[not a one-line string"),
]  # fmt: skip
TOML_SETS = [
    # (text, section, key, value, error fragment or None)
    (EXAMPLE_TEXT, "", "site_name", QUOTED_NAME, None),
    (EXAMPLE_TEXT, "clock", "ntp_server", "10.0.0.1", None),
    ('# head\n\n[collector]\nbind = "0.0.0.0"\n', "", "site_name", "Kurum", None),
    ('[clock]\nfallback_server = "a"\n\n[api]\nport = 1\n', "clock", "ntp_server", "b", None),
    ('data_dir = "x"\n[api]\nport = 1', "clock", "ntp_server", "c", None),
    ('[clock]\r\nntp_server = "old"   # keep\r\n', "clock", "ntp_server", "new", None),
    ('clock.ntp_server = "x"\n', "clock", "ntp_server", "y", "[written as dotted keys"),
]  # fmt: skip
SITE_PROBLEMS = [
    (SITE_NAME, None), ("  Kurum  ", None), (QUOTED_NAME, None), ("", "[empty]"),
    ("   ", "[empty]"), ("x" * 201, "[characters, at most 200]"), ("x" * 200, None),
    ("a\u0007b", "[contains a control character]"), ("a\u200bb", "[contains a control character]"),
    ("a\u2028b", "[contains a control character]"), ("a\nb", "[contains a control character]"),
]  # fmt: skip
TIME_PROBLEMS = [
    ("", None), ("10.0.0.1", None), ("ntp.example.com", None), ("ntp.example.com.", None),
    ("core-r1", None), ("fe80::1", None), ("172.20.10", "[invalid IPv4 address]"),
    ("10.0.0.256", "[invalid IPv4 address]"), ("10.0.0.0/24", "[neither an IP address"),
    ("two words", "[contains a space]"), ("a_b.example", "[neither an IP address"),
    ("-bad.example", "[neither an IP address"), ("fe80::1/64", "[invalid IPv6 address]"),
]  # fmt: skip
TIME_PEERS = [
    ("10.0.0.1", "time.ume.tubitak.gov.tr", "10.0.0.1,0x8 time.ume.tubitak.gov.tr,0xA"),
    ("", "time.ume.tubitak.gov.tr", "time.ume.tubitak.gov.tr,0x8"),
    ("10.0.0.1", "", "10.0.0.1,0x8"),
    ("ntp.example.com", "ntp.example.com", "ntp.example.com,0x8"),
    ("", "", None),
]
ADMIN_ANSWERS = [
    (["10.0.0.10"], ["10.0.0.10"], 1),
    (["", "10.0.0.300", "10.0.0.10, 10.0.1.0/24"], ["10.0.0.10", "10.0.1.0/24"], 3),
    (["", "", ""], [], 3),  # asked three times, then the preflight stops
    ([], [], 1),  # the console cannot be read: no answer, no loop
]


@pytest.fixture
def setup_helpers(engine: str, tmp_path: Path) -> dict[str, Any]:
    cases = {
        "get": [{"text": t, "section": s, "key": k} for t, s, k, _, _ in TOML_GETS],
        "set": [{"text": t, "section": s, "key": k, "value": v} for t, s, k, v, _ in TOML_SETS],
        "quote": [SITE_NAME, QUOTED_NAME, "tab\there", "bell\u0007"],
        "site": [value for value, _ in SITE_PROBLEMS],
        "time": [value for value, _ in TIME_PROBLEMS],
        "peers": [{"primary": p, "fallback": f} for p, f, _ in TIME_PEERS],
        "admin": [answers for answers, _, _ in ADMIN_ANSWERS],
    }
    path = tmp_path / "setup-in.json"
    path.write_text(json.dumps(cases), encoding="utf-8")
    output = tmp_path / "setup-out.json"
    script = write_script(tmp_path / "setup.ps1", _SETUP_HELPERS_SCRIPT)
    arguments = ["-ModulePath", str(MODULE), "-InputPath", str(path), "-OutputPath", str(output)]
    result = run_powershell(engine, script, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = read_json(output)
    return report


def test_module_reads_one_line_toml_strings_without_python(setup_helpers: dict[str, Any]) -> None:
    for case, (text, _, key, value, error) in zip(setup_helpers["get"], TOML_GETS, strict=True):
        if error is None:
            assert case == {"value": value, "error": None}, (text, case)
            if value is not None:
                section = tomllib.loads(text.replace("\r\n", "\n"))
                found = section.get("clock", section) if key == "ntp_server" else section
                assert found[key] == value, "tomllib reads the same value"
        else:
            assert case["value"] is None, (text, case)
            assert error in case["error"], (text, case)
            assert "edit the file by hand" in case["error"] or "twice" in case["error"]


def test_module_writes_one_line_toml_strings(setup_helpers: dict[str, Any]) -> None:
    results = setup_helpers["set"]
    for case, (text, _, _, _, error) in zip(results, TOML_SETS, strict=True):
        if error is not None:
            assert case["value"] is None
            assert error in case["error"]
        else:
            assert case["error"] is None, (text, case)
    example = tomllib.loads(EXAMPLE_TEXT)
    named, timed, inserted, below_header, appended, crlf, _ = (r["value"] for r in results)
    assert tomllib.loads(named) == {**example, "site_name": QUOTED_NAME}, "only site_name changes"
    assert "# the organisation's name" in named, "the comment after the value stays"
    expected = {**example, "clock": {**example["clock"], "ntp_server": "10.0.0.1"}}
    assert tomllib.loads(timed) == expected
    # A missing key: below the comments that start the file, or right below its table header.
    assert inserted == '# head\n\nsite_name = "Kurum"\n[collector]\nbind = "0.0.0.0"\n'
    assert below_header.startswith('[clock]\nntp_server = "b"\nfallback_server = "a"\n')
    assert tomllib.loads(below_header) == {
        "clock": {"ntp_server": "b", "fallback_server": "a"}, "api": {"port": 1},
    }  # fmt: skip
    # A missing table is added at the end.
    assert tomllib.loads(appended) == {
        "data_dir": "x", "api": {"port": 1}, "clock": {"ntp_server": "c"},
    }  # fmt: skip
    assert crlf == '[clock]\r\nntp_server = "new"   # keep\r\n', "line endings and comment kept"


def test_module_quotes_toml_strings(setup_helpers: dict[str, Any]) -> None:
    originals = [SITE_NAME, QUOTED_NAME, "tab\there", "bell\u0007"]
    for quoted, original in zip(setup_helpers["quote"], originals, strict=True):
        assert tomllib.loads(f"x = {quoted}")["x"] == original, quoted
    assert setup_helpers["quote"][0] == '"Örnek A.Ş."', "Turkish letters are written as they are"


def test_module_checks_organisation_names_like_the_core(setup_helpers: dict[str, Any]) -> None:
    """The rules of arcilogger_core.config.Config.site_name (one line, at most 200)."""
    for problem, (value, expected) in zip(setup_helpers["site"], SITE_PROBLEMS, strict=True):
        if expected is None:
            assert problem == "NULL", (value, problem)
        else:
            assert expected in problem, (value, problem)


def test_module_checks_time_servers(setup_helpers: dict[str, Any]) -> None:
    for problem, (value, expected) in zip(setup_helpers["time"], TIME_PROBLEMS, strict=True):
        if expected is None:
            assert problem == "NULL", (value, problem)
        else:
            assert expected in problem, (value, problem)


def test_module_builds_the_windows_time_peer_list(setup_helpers: dict[str, Any]) -> None:
    """Without a local time source the public fallback is the only source (D2)."""
    for case, (_, _, expected) in zip(setup_helpers["peers"], TIME_PEERS, strict=True):
        if expected is None:
            assert case["value"] is None
            assert "[no time server]" in case["error"]
        else:
            assert case == {"value": expected, "error": None}


def test_module_asks_for_the_admin_address_until_it_is_valid(
    setup_helpers: dict[str, Any],
) -> None:
    for case, (_, values, asked) in zip(setup_helpers["admin"], ADMIN_ANSWERS, strict=True):
        assert case["values"] == values
        assert len(case["prompts"]) == asked
        for prompt in case["prompts"]:
            # Turkish first, English in brackets; placeholder addresses, no example network's.
            assert prompt.startswith("Yönetici PC'sinin IP adresi ya da ağı")
            assert "[admin PC address or network" in prompt
            assert "10.0.0.10" in prompt
            assert "10.0.0.0/24" in prompt


def _write_test_pki(folder: Path) -> dict[str, Path]:
    """A root CA like Caddy's internal one (ECDSA P-256, PEM), a localhost leaf, a foreign root."""
    now = datetime.now(UTC)

    def name(text: str) -> x509.Name:
        return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, text)])

    def root(text: str) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:
        key = ec.generate_private_key(ec.SECP256R1())
        certificate = (
            x509.CertificateBuilder()
            .subject_name(name(text))
            .issuer_name(name(text))
            .public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256())
        )
        return certificate, key

    ca, ca_key = root("ARCILOGGER test root")
    other, _ = root("Some other root")
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    leaf = (
        x509.CertificateBuilder()
        .subject_name(name("localhost"))
        .issuer_name(ca.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    folder.mkdir(parents=True, exist_ok=True)
    paths = {
        "root": folder / "root.crt",
        "other": folder / "other-root.crt",
        "chain": folder / "leaf-chain.pem",
        "key": folder / "leaf.key",
    }
    pem = serialization.Encoding.PEM
    paths["root"].write_bytes(ca.public_bytes(pem))
    paths["other"].write_bytes(other.public_bytes(pem))
    paths["chain"].write_bytes(leaf.public_bytes(pem) + ca.public_bytes(pem))
    paths["key"].write_bytes(
        leaf_key.private_bytes(pem, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    )
    return paths


_HTTPS_HEALTH_SCRIPT = r"""
param([string]$ModulePath, [int]$Port, [string]$Root, [string]$OtherRoot, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force
$out = [ordered]@{}
$health = Test-ArciHttpsHealth -HostName 'localhost' -Port $Port -RootCertificatePath $Root `
    -TimeoutMilliseconds 20000
$out['status'] = $health.Status
$out['body'] = $health.Body
try {
    [void](Test-ArciHttpsHealth -HostName 'localhost' -Port $Port -RootCertificatePath $OtherRoot `
            -TimeoutMilliseconds 20000)
    $out['other_root'] = 'accepted'
}
catch { $out['other_root'] = 'refused: ' + $_.Exception.Message }
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json), $utf8)
"""


def test_https_health_check_speaks_tls12_and_pins_the_root(engine: str, tmp_path: Path) -> None:
    """The installer's health check against a TLS 1.2+ server (Caddy: "protocols tls1.2 tls1.3").

    Windows PowerShell 5.1 (.NET Framework) does not necessarily offer TLS 1.2 unless asked to;
    the check names it. Only a chain ending at the given root (Caddy's own) is accepted.
    """
    pki = _write_test_pki(tmp_path / "pki")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(pki["chain"], pki["key"])

    protocols: list[str | None] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            if isinstance(self.connection, ssl.SSLSocket):
                protocols.append(self.connection.version())
            found = self.path == "/api/v1/health"
            body = b'{"status": "ok"}' if found else b"{}"
            self.send_response(200 if found else 404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: Any) -> None:
            return

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        out = tmp_path / "https-health.json"
        script = write_script(tmp_path / "https-health.ps1", _HTTPS_HEALTH_SCRIPT)
        result = run_powershell(
            engine,
            script,
            "-ModulePath",
            str(MODULE),
            "-Port",
            str(server.server_address[1]),
            "-Root",
            str(pki["root"]),
            "-OtherRoot",
            str(pki["other"]),
            "-OutputPath",
            str(out),
        )
    finally:
        server.shutdown()
        server.server_close()
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(out)
    assert report["status"] == 200, report
    assert '"status": "ok"' in report["body"]
    assert report["other_root"].startswith("refused"), report["other_root"]
    assert protocols == ["TLSv1.2"], "one request, over TLS 1.2"


# --- NTFS rights plan ----------------------------------------------------------------------------


def _acl_plan(engine: str, tmp_path: Path) -> dict[str, Any]:
    plan = tmp_path / "acl.json"
    result = run_powershell(
        engine,
        WINDOWS / "scripts" / "Set-ArciVaultAcl.ps1",
        "-DataRoot",
        str(tmp_path / "data"),
        "-ProgramDataDir",
        str(tmp_path / "programdata"),
        "-InstallDir",
        str(tmp_path / "program"),
        "-PlanOnly",
        "-PlanPath",
        str(plan),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    document: dict[str, Any] = read_json(plan)
    return document


def test_acl_script_follows_the_configured_data_folder(engine: str, tmp_path: Path) -> None:
    """Run by hand, Set-ArciVaultAcl.ps1 uses data_dir of arcilogger.toml: on a computer whose
    data is in C:\\ARCILOGGER it must not create and lock a D:\\ARCILOGGER."""
    script = WINDOWS / "scripts" / "Set-ArciVaultAcl.ps1"
    programdata = tmp_path / "programdata"
    programdata.mkdir()
    data = tmp_path / "system-drive" / "ARCILOGGER"
    (programdata / "arcilogger.toml").write_text(f'data_dir = "{data.as_posix()}"\n', "utf-8")
    plan = tmp_path / "acl.json"
    arguments = ["-InstallDir", str(tmp_path / "program"), "-PlanOnly", "-PlanPath", str(plan)]
    result = run_powershell(engine, script, "-ProgramDataDir", str(programdata), *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(read_json(plan)["data_root"]) == data
    # Without a configuration it asks for -DataRoot instead of picking a drive.
    result = run_powershell(engine, script, "-ProgramDataDir", str(tmp_path / "none"), "-PlanOnly")
    assert result.returncode != 0
    assert "-DataRoot" in result.stdout + result.stderr


def test_acl_script_takes_the_recorded_data_root(engine: str, tmp_path: Path) -> None:
    """Day 0 note D0-2, WP6-2: ArciAdmin can write data_dir, the record only administrators. Run
    by hand (after a console "secret set"), Set-ArciVaultAcl.ps1 reads the record as the
    installer does and never puts the rights of the vault on a folder data_dir names instead."""
    script = WINDOWS / "scripts" / "Set-ArciVaultAcl.ps1"
    programdata = tmp_path / "programdata"
    (programdata / "admin").mkdir(parents=True)
    data = tmp_path / "data" / "ARCILOGGER"
    (programdata / "data-root.txt").write_text(f"{data}\n", "utf-8")
    config = programdata / "arcilogger.toml"
    plan = tmp_path / "acl.json"
    arguments = ["-ProgramDataDir", str(programdata), "-InstallDir", str(tmp_path / "program")]
    arguments += ["-PlanOnly", "-PlanPath", str(plan)]
    config.write_text(f'data_dir = "{data.as_posix()}"\n', "utf-8")
    result = run_powershell(engine, script, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(read_json(plan)["data_root"]) == data
    plan.unlink()
    elsewhere = tmp_path / "elsewhere"
    config.write_text(f'data_dir = "{elsewhere.as_posix()}"\n', "utf-8")
    result = run_powershell(engine, script, *arguments)
    assert result.returncode != 0
    assert "-DataRoot" in result.stdout + result.stderr
    assert not plan.exists(), "nothing is planned for a data_dir the record does not name"
    # A removed record with the administration installed: only -DataRoot names the data root.
    (programdata / "data-root.txt").unlink()
    result = run_powershell(engine, script, *arguments)
    assert result.returncode != 0
    assert "data-root.txt" in result.stdout + result.stderr


def _entries_by_leaf(document: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {re.split(r"[\\/]", e["Path"])[-1]: e for e in document["entries"]}


def _rights(entry: dict[str, Any], sid: str) -> set[str]:
    return {
        part.strip()
        for ace in entry["Aces"]
        if ace["Sid"] == sid
        for part in ace["Rights"].split(",")
    }


def test_acl_plan_least_privilege(engine: str, tmp_path: Path) -> None:
    entries = _entries_by_leaf(_acl_plan(engine, tmp_path))
    collector, ingest, api, worker, web = (
        service_sid(n) for n in ("ArciCollector", "ArciIngest", "ArciApi", "ArciWorker", "ArciWeb")
    )
    tasks = WELL_KNOWN["local_service"]
    for entry in entries.values():
        assert all(ace["Sid"] != USERS_SID for ace in entry["Aces"]), entry["Path"]
    # Data root and configuration folder do not inherit "Users" from the drive.
    assert entries["data"]["Protect"] is True
    assert entries["programdata"]["Protect"] is True
    for protected in ("data", "programdata", "secret.key", "caddy"):
        assert _rights(entries[protected], WELL_KNOWN["system"]) == {"FullControl"}
        assert _rights(entries[protected], WELL_KNOWN["admins"]) == {"FullControl"}
    # Raw evidence: the collector can write but not delete or rename; sealing (tasks) can.
    assert _rights(entries["spool"], collector) == {"ReadAndExecute", "Write"}
    assert _rights(entries["spool"], ingest) == {"ReadAndExecute"}
    assert _rights(entries["spool"], tasks) == {"Modify"}
    # The API reads days that are not sealed yet (GET /raw, the 5651 report, the sealing
    # calendar; MVP-CONTRACTS 6.1): read only, inherited by the collector's files, never a write
    # or delete right.
    assert _rights(entries["spool"], api) == {"ReadAndExecute"}
    assert all(ace["Inherit"] for ace in entries["spool"]["Aces"] if ace["Sid"] == api)
    assert not any(ace["FoldersOnly"] for ace in entries["spool"]["Aces"] if ace["Sid"] == api)
    # The worker evaluates the licence like the API and needs the oldest spool day by file name
    # (licensing contract 3.4): it lists the spool and the source folders, never reads a file
    # (ListDirectory is ReadData on a file, so the entry is inherited by folders only).
    assert _rights(entries["spool"], worker) == {"ListDirectory", "ReadAttributes", "Traverse"}
    worker_spool = [ace for ace in entries["spool"]["Aces"] if ace["Sid"] == worker]
    assert [(ace["Inherit"], ace["FoldersOnly"]) for ace in worker_spool] == [(True, True)]
    assert _rights(entries["spool"], web) == set()
    # The audit spool is sealed like the raw logs: protected, so the collector's rights on the
    # spool do not reach it; the API appends without a Delete right, sealing removes the day.
    audit = entries["_audit"]
    assert audit["Protect"] is True
    assert _rights(audit, collector) == set()
    assert _rights(audit, api) == {"ReadAndExecute", "Write"}
    assert _rights(audit, tasks) == {"Modify"}
    assert _rights(audit, ingest) == {"ReadAndExecute"}
    assert {ace["Sid"] for ace in audit["Aces"]} == {
        WELL_KNOWN["system"],
        WELL_KNOWN["admins"],
        api,
        tasks,
        ingest,
    }
    assert _rights(audit, worker) == set(), "the worker's list right stops at spool\\_audit"
    # Vault: written by the scheduled tasks only, audited; ingest reads sealed days (ADR-0002).
    vault = entries["vault"]
    assert vault["Audit"] is True
    assert _rights(vault, tasks) == {"Modify"}
    assert _rights(vault, api) == _rights(vault, worker) == _rights(vault, ingest)
    assert _rights(vault, api) == {"ReadAndExecute"}
    assert _rights(vault, collector) == _rights(vault, web) == set()
    # Evidence-job state: core trusts state\seal\<day>.json ("sealed"), so only the tasks write it.
    assert _rights(entries["state"], collector) == {"Modify"}, "its own status and alerts"
    for job in ("seal", "replica", "verify", "clock"):
        entry = entries[job]
        assert entry["Protect"] is True, job
        assert _rights(entry, tasks) == {"Modify"}, job
        for writer in (collector, ingest, worker, api, web):
            assert not _rights(entry, writer) & {"Modify", "Write", "FullControl"}, (job, writer)
        assert _rights(entry, collector) == set(), job
        assert _rights(entry, api) == _rights(entry, worker) == {"ReadAndExecute"}, job
    assert _rights(entries["seal"], ingest) == {"ReadAndExecute"}
    assert _rights(entries["replica"], ingest) == set()
    # The collector never needs a secret; Caddy's CA key is Caddy's alone.
    assert _rights(entries["secret.key"], collector) == set()
    assert entries["secret.key"]["Kind"] == "File"
    assert entries["secret.key"]["Protect"] is True
    for reader in (ingest, api, worker, tasks):
        assert _rights(entries["secret.key"], reader) == {"Read"}
    caddy_sids = {ace["Sid"] for ace in entries["caddy"]["Aces"]}
    assert caddy_sids == {WELL_KNOWN["system"], WELL_KNOWN["admins"], web}
    # Database dumps: backups (and backups\db below it, the installer's dumps before an upgrade)
    # stay administrators-only by inheritance from the data root. The scheduled dumps
    # (ADR-0009) are protected: only the Backup task writes and prunes them; no service reads
    # them (password hashes, TOTP secrets encrypted with secret.key, personal data).
    assert entries["backups"]["Aces"] == []
    assert entries["backups"]["Protect"] is False
    assert not any(re.search(r"[\\/]backups[\\/]db$", e["Path"]) for e in entries.values())
    scheduled = entries["scheduled"]
    assert re.split(r"[\\/]", scheduled["Path"])[-2:] == ["backups", "scheduled"]
    assert scheduled["Kind"] == "Directory"
    assert scheduled["Protect"] is True
    assert {ace["Sid"]: ace["Rights"] for ace in scheduled["Aces"]} == {
        WELL_KNOWN["system"]: "FullControl",
        WELL_KNOWN["admins"]: "FullControl",
        tasks: "Modify",
    }
    assert all(ace["Inherit"] for ace in scheduled["Aces"])
    for service in (collector, ingest, api, worker, web):
        assert _rights(scheduled, service) == set(), service
    data_root_scopes = {
        ace["Inherit"] for ace in entries["data"]["Aces"] if ace["Sid"] == collector
    }
    assert data_root_scopes == {False}, "services may only list the data root itself"


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS rights are Windows-only")
def test_acl_applied_keeps_sealed_file_deny_entries(tmp_path: Path) -> None:
    """Apply the rights to temporary folders and read them back (administrator CI runner)."""
    powershell = _windows_admin_powershell()
    data, programdata, program = tmp_path / "data", tmp_path / "programdata", tmp_path / "program"
    sealed = data / "vault" / "2026" / "10" / "12" / "fortigate.log.gz"
    sealed.parent.mkdir(parents=True)
    sealed.write_bytes(b"sealed")
    spool_day = data / "spool" / "core-r1" / "2026-10-13.jsonl"
    spool_day.parent.mkdir(parents=True)
    spool_day.write_bytes(b"raw\n")
    programdata.mkdir()
    (programdata / "secret.key").write_bytes(b"key")
    program.mkdir()
    # What arcilogger_core.immutability.lock() does to a sealed file.
    subprocess.run(["icacls", str(sealed), "/deny", "*S-1-1-0:(DE,WD,AD,WA,WEA)"], check=True)
    result = run_powershell(
        powershell,
        WINDOWS / "scripts" / "Set-ArciVaultAcl.ps1",
        "-DataRoot",
        str(data),
        "-ProgramDataDir",
        str(programdata),
        "-InstallDir",
        str(program),
        "-SkipAudit",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    dump = write_script(
        tmp_path / "dump.ps1",
        r"""
param([string]$ListPath, [string]$OutputPath)
# A Get-Acl that cannot run must fail the test, not write empty rules.
$ErrorActionPreference = 'Stop'
Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue
$out = [ordered]@{}
foreach ($path in [System.IO.File]::ReadAllLines($ListPath)) {
    $acl = Get-Acl -LiteralPath $path
    $sidType = [System.Security.Principal.SecurityIdentifier]
    $out[$path] = [ordered]@{
        protected = $acl.AreAccessRulesProtected
        rules = @($acl.GetAccessRules($true, $true, $sidType) | ForEach-Object {
                [ordered]@{
                    sid = $_.IdentityReference.Value; rights = [int]$_.FileSystemRights
                    type = [string]$_.AccessControlType; inherited = $_.IsInherited
                }
            })
    }
}
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 6))
""",
    )
    out = tmp_path / "acl.json"
    seal_state = data / "state" / "seal"
    audit_spool = data / "spool" / "_audit"
    scheduled = data / "backups" / "scheduled"
    paths = [
        data,
        data / "spool",
        spool_day.parent,
        spool_day,
        programdata / "secret.key",
        sealed,
        seal_state,
        audit_spool,
        scheduled,
    ]
    listing = tmp_path / "paths.txt"
    listing.write_text("\n".join(str(p) for p in paths) + "\n", encoding="utf-8")
    result = run_powershell(powershell, dump, "-ListPath", str(listing), "-OutputPath", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    acls = {Path(k): v for k, v in read_json(out).items()}
    delete, delete_child = 0x10000, 0x40
    collector = service_sid("ArciCollector")
    assert acls[data]["protected"] is True
    assert all(rule["sid"] != USERS_SID for rule in acls[data]["rules"])
    spool_collector = [r for r in acls[data / "spool"]["rules"] if r["sid"] == collector]
    assert spool_collector
    assert all(r["rights"] & (delete | delete_child) == 0 for r in spool_collector)
    # The API reads the spool (days not sealed yet) but can neither write nor delete there.
    write_data, append_data = 0x2, 0x4
    spool_api = [r for r in acls[data / "spool"]["rules"] if r["sid"] == service_sid("ArciApi")]
    assert spool_api, "ArciApi must read the raw spool"
    assert all(r["type"] == "Allow" for r in spool_api)
    assert all(
        r["rights"] & (delete | delete_child | write_data | append_data) == 0 for r in spool_api
    )
    # The worker lists the spool and the source folders (the licence's spool bound) but gets
    # nothing on a raw day file.
    worker = service_sid("ArciWorker")
    for folder in (data / "spool", spool_day.parent):
        assert any(r["sid"] == worker for r in acls[folder]["rules"]), folder
    assert all(r["sid"] != worker for r in acls[spool_day]["rules"])
    assert all(r["sid"] != collector for r in acls[programdata / "secret.key"]["rules"])
    denies = [r for r in acls[sealed]["rules"] if r["type"] == "Deny" and r["sid"] == "S-1-1-0"]
    assert denies, "sealed files keep their deny entry"
    assert not denies[0]["inherited"]
    # The network-facing collector reaches neither the sealing state nor the audit spool.
    for path in (seal_state, audit_spool):
        assert acls[path]["protected"] is True, path
        assert all(r["sid"] != collector for r in acls[path]["rules"]), path
    assert any(r["sid"] == WELL_KNOWN["local_service"] for r in acls[seal_state]["rules"])
    # Scheduled database dumps (ADR-0009): the Backup task writes them, no service reads them.
    assert acls[scheduled]["protected"] is True
    assert any(r["sid"] == WELL_KNOWN["local_service"] for r in acls[scheduled]["rules"])
    services = {service_sid(n) for n in ("ArciCollector", "ArciIngest", "ArciApi", "ArciWorker")}
    assert not any(r["sid"] in services for r in acls[scheduled]["rules"])


def _windows_admin_powershell() -> str:
    # os.name rather than sys.platform: mypy would treat the rest as unreachable off Windows.
    powershell = shutil.which("powershell")
    if os.name != "nt" or powershell is None:
        pytest.skip("Windows PowerShell on Windows only")
    is_admin = subprocess.run(
        [
            powershell,
            "-NoProfile",
            "-Command",
            "([Security.Principal.WindowsPrincipal]"
            "[Security.Principal.WindowsIdentity]::GetCurrent())"
            ".IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)",
        ],
        capture_output=True,
        text=True,
        check=False,
        env=powershell_environment(powershell),
    ).stdout.strip()
    if is_admin != "True":
        pytest.skip("not running as administrator")
    return powershell


@pytest.mark.skipif(sys.platform != "win32", reason="NTFS auditing is Windows-only")
def test_vault_auditing_applied(tmp_path: Path) -> None:
    """The SACL on the vault and the "File System" audit subcategory (ADR-0003)."""
    powershell = _windows_admin_powershell()
    data, programdata, program = tmp_path / "data", tmp_path / "programdata", tmp_path / "program"
    for folder in (data / "vault", programdata, program):
        folder.mkdir(parents=True)
    result = run_powershell(
        powershell,
        WINDOWS / "scripts" / "Set-ArciVaultAcl.ps1",
        "-DataRoot",
        str(data),
        "-ProgramDataDir",
        str(programdata),
        "-InstallDir",
        str(program),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # The script's only output naming auditpol is its warning that auditpol failed. Checked by
    # that word near the start of the line: Windows PowerShell wraps the 126-character warning at
    # the console width (119 columns on the CI runner), and the console code page may garble its
    # Turkish text.
    output = result.stdout + result.stderr
    assert "auditpol" not in output, ("auditpol failed", output)
    dump = write_script(
        tmp_path / "audit.ps1",
        r"""
param([string]$Path, [string]$OutputPath)
# A Get-Acl that cannot run must fail the test, not report "no audit rules".
$ErrorActionPreference = 'Stop'
Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue
$acl = Get-Acl -LiteralPath $Path -Audit
$sidType = [System.Security.Principal.SecurityIdentifier]
$rules = @($acl.GetAuditRules($true, $true, $sidType) | ForEach-Object {
        [ordered]@{
            sid = $_.IdentityReference.Value; rights = [int]$_.FileSystemRights
            flags = [string]$_.AuditFlags
        }
    })
$policy = (& auditpol.exe /get '/subcategory:{0CCE921D-69AE-11D9-BED3-505054503030}' /r) -join "`n"
$report = [ordered]@{ rules = $rules; policy = $policy }
[System.IO.File]::WriteAllText($OutputPath, ($report | ConvertTo-Json -Depth 4))
""",
    )
    out = tmp_path / "audit.json"
    result = run_powershell(powershell, dump, "-Path", str(data / "vault"), "-OutputPath", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(out)
    everyone = [rule for rule in report["rules"] if rule["sid"] == "S-1-1-0"]
    assert everyone, ("no audit entry (SACL) for Everyone on the vault", report["rules"])
    delete, write_data = 0x10000, 0x2
    assert all(rule["rights"] & delete and rule["rights"] & write_data for rule in everyone)
    assert "Success" in everyone[0]["flags"]
    assert "Failure" in everyone[0]["flags"]
    # auditpol /r prints CSV; the File System subcategory is now audited on success and failure.
    assert "Success and Failure" in report["policy"], report["policy"]


_MODULE_PATH_SCRIPT = r"""
param([string]$ModulePath, [string]$Target, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
$before = $env:PSModulePath
Import-Module $ModulePath -Force
Initialize-ArciModulePath
# The two commands that failed on the CI runner (module Microsoft.PowerShell.Security).
$secure = ConvertTo-SecureString 'not a secret' -AsPlainText -Force
$acl = Get-Acl -LiteralPath $Target
$report = [ordered]@{
    before = $before
    after = $env:PSModulePath
    secure_string = ($secure -is [System.Security.SecureString])
    acl = ($null -ne $acl.Owner)
    security_module = [string](Get-Module -Name Microsoft.PowerShell.Security).ModuleBase
}
[System.IO.File]::WriteAllText($OutputPath, ($report | ConvertTo-Json))
"""


def _skip_unless_required(reason: str) -> NoReturn:
    if os.environ.get("ARCILOGGER_REQUIRE_PWSH") == "1":
        pytest.fail(f"{reason} (ARCILOGGER_REQUIRE_PWSH=1)")
    pytest.skip(reason)


def _powershell_7_module_path() -> tuple[str, Path, str]:
    """Windows PowerShell, pwsh's ``Modules`` folder and a PSModulePath with pwsh's in front.

    That is what powershell.exe inherits when a program that pwsh started starts it (the CI
    steps, the setup .exe from a pwsh window; about_PSModulePath). Missing pieces fail the test
    with ARCILOGGER_REQUIRE_PWSH=1 (the Windows CI jobs) and skip it otherwise.
    """
    override = os.environ.get("ARCILOGGER_PWSH")
    windows_powershell = shutil.which("powershell")
    pwsh = override if override and Path(override).is_file() else shutil.which("pwsh")
    if windows_powershell is None or pwsh is None:
        _skip_unless_required("needs Windows PowerShell and PowerShell 7")
    pwsh_modules = Path(pwsh).resolve().parent / "Modules"
    if not (pwsh_modules / "Microsoft.PowerShell.Security").is_dir():
        _skip_unless_required(f"no Microsoft.PowerShell.Security in {pwsh_modules}")
    inherited = next((v for k, v in os.environ.items() if k.upper() == "PSMODULEPATH"), "")
    program_files = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
    polluted = os.pathsep.join(
        [str(program_files / "PowerShell" / "Modules"), str(pwsh_modules), inherited]
    )
    return windows_powershell, pwsh_modules, polluted


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 on Windows only")
def test_installer_scripts_ignore_powershell_7_modules(tmp_path: Path) -> None:
    """Started from PowerShell 7, the installer still loads Windows PowerShell's own modules."""
    windows_powershell, pwsh_modules, polluted = _powershell_7_module_path()
    target = tmp_path / "target.txt"
    target.write_text("x", encoding="utf-8")
    out = tmp_path / "module-path.json"
    script = write_script(tmp_path / "module-path.ps1", _MODULE_PATH_SCRIPT)
    result = run_powershell(
        windows_powershell,
        script,
        "-ModulePath",
        str(MODULE),
        "-Target",
        str(target),
        "-OutputPath",
        str(out),
        env_extra={"PSModulePath": polluted},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(out)
    assert str(pwsh_modules).lower() in report["before"].lower(), "the test starts polluted"
    after = [entry.rstrip("\\").lower() for entry in report["after"].split(os.pathsep)]
    assert str(pwsh_modules).lower() not in after
    assert report["secure_string"] is True
    assert report["acl"] is True
    assert not report["security_module"].lower().startswith(str(pwsh_modules).lower())


_MODULE_IMPORT = re.compile(r"^\s*Import-Module\b.*ArciInstall\.psm1", re.IGNORECASE)


def test_scripts_drop_powershell_7_module_folders_right_after_the_import() -> None:
    """Every script that imports ArciInstall.psm1 calls Initialize-ArciModulePath next.

    The harness starts powershell.exe with a clean module path, so the other tests would not
    notice a missing call; a setup .exe started from a pwsh window would (ADR-0007, section 7).
    """
    importers: set[str] = set()
    for path in sorted(WINDOWS.rglob("*.ps1")):
        lines = path.read_text(encoding="utf-8-sig").splitlines()
        for number, line in enumerate(lines, start=1):
            if not _MODULE_IMPORT.match(line):
                continue
            importers.add(path.name)
            following = [
                text.strip()
                for text in lines[number:]
                if text.strip() and not text.strip().startswith("#")
            ]
            assert following[:1] == ["Initialize-ArciModulePath"], (
                "Initialize-ArciModulePath must follow the import",
                f"{path.relative_to(REPO)}:{number}",
            )
    assert importers >= {
        "Install-Arcilogger.ps1",
        "Build-ArciBundle.ps1",
        "Set-ArciVaultAcl.ps1",
        "Set-ArciFirewall.ps1",
    }, importers


# --- installer dry runs --------------------------------------------------------------------------


#: The release date of the fake bundles (bundle-manifest.json release_date, contract 9.2).
BUNDLE_RELEASE_DATE = "2026-10-20"


def make_fake_bundle(
    root: Path, version: str = "9.9.9", release_date: str | None = BUNDLE_RELEASE_DATE
) -> Path:
    """A minimal bundle: the real scripts and definitions, fake binaries, correct checksums.

    ``release_date`` None: a manifest without it (a bundle of a build before licensing)."""
    bundle = root / "bundle"
    (bundle / "installer").mkdir(parents=True)
    for name in ("Install-Arcilogger.ps1", "ArciInstall.psm1"):
        shutil.copyfile(INSTALLER / name, bundle / "installer" / name)
    for folder, pattern in (("scripts", "*.ps1"), ("tasks", "*.xml"), ("services", "*.xml")):
        (bundle / folder).mkdir()
        for file in (WINDOWS / folder).glob(pattern):
            shutil.copyfile(file, bundle / folder / file.name)
    (bundle / "caddy").mkdir()
    shutil.copyfile(CADDYFILE, bundle / "caddy" / "Caddyfile")
    (bundle / "config").mkdir()
    shutil.copyfile(
        WINDOWS / "arcilogger.example.toml", bundle / "config" / "arcilogger.example.toml"
    )
    (bundle / "web").mkdir()
    (bundle / "web" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (bundle / "licenses").mkdir()
    (bundle / "licenses" / "winsw-LICENSE.txt").write_text("MIT License\n", encoding="utf-8")
    wheels = bundle / "wheels"
    (wheels / "deps").mkdir(parents=True)
    (wheels / "arcilogger").mkdir()
    (wheels / "requirements.txt").write_text("tzdata==2026.5 \\\n    --hash=sha256:00\n")
    (wheels / "deps" / "tzdata-2026.5-py2.py3-none-any.whl").write_bytes(b"fake wheel")
    (wheels / "arcilogger" / f"arcilogger_core-{version}-py3-none-any.whl").write_bytes(b"fake")
    components = []
    for name, file_name in (
        ("python", "cpython-fake-install_only.tar.gz"),
        ("winsw", "WinSW.NET461.exe"),
        ("caddy", "caddy_fake_windows_amd64.zip"),
    ):
        content = f"fake {name} binary".encode()
        path = bundle / "thirdparty" / name / file_name
        path.parent.mkdir(parents=True)
        path.write_bytes(content)
        components.append(
            {
                "name": name,
                "version": "0",
                "role": "bundled",
                "sha256": sha256_bytes(content),
                "path": f"thirdparty/{name}/{file_name}",
            }
        )
    (bundle / "components.json").write_text(
        json.dumps({"components": components}), encoding="utf-8"
    )
    manifest: dict[str, Any] = {
        "product": "ARCILOGGER",
        "version": version,
        "built_at": "2026-10-12T00:00:00Z",
        "git_commit": None,
        "components": components,
        "files": [],
    }
    if release_date is not None:
        manifest["release_date"] = release_date
    (bundle / "bundle-manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    write_sums(bundle)
    return bundle


def write_version(folder: Path, version: str, fingerprint: str) -> Path:
    """What the installer writes last into a program folder it installed."""
    folder.mkdir(parents=True, exist_ok=True)
    record = {"product": "ARCILOGGER", "version": version, "fingerprint": fingerprint}
    (folder / "VERSION.json").write_text(json.dumps(record), encoding="utf-8")
    return folder


#: The server address the dry runs pass unless a test checks how it is remembered or detected
#: (it is not an address of the test machine: the collector-bind check fails for a new install).
SERVER_ADDRESS = "172.20.20.15"
#: An address no test machine has (TEST-NET-2, RFC 5737).
FOREIGN_ADDRESS = "198.51.100.7"


def _setup_arguments(
    extra: tuple[str, ...], site_name: str | None, admin_address: str | None
) -> list[str]:
    """-SiteName and -AdminAddress unless None or already among the arguments."""
    arguments = []
    if site_name is not None and "-SiteName" not in extra:
        arguments += ["-SiteName", site_name]
    if admin_address is not None and "-AdminAddress" not in extra:
        arguments += ["-AdminAddress", admin_address]
    return arguments


def dry_run(
    engine: str,
    tmp_path: Path,
    bundle: Path,
    *extra: str,
    switch: str = "-DryRun",
    install_dir: Path | None = None,
    env_extra: dict[str, str] | None = None,
    server_address: str | None = SERVER_ADDRESS,
    site_name: str | None = SITE_NAME,
    admin_address: str | None = ADMIN_ADDRESS,
    data_root: bool = True,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
    """``data_root`` False: no -DataRoot (the installer takes it from its record or the
    configuration)."""
    plan = tmp_path / f"plan-{len(list(tmp_path.glob('plan-*.json')))}.json"
    server = ["-ServerAddress", server_address] if server_address else []
    server += _setup_arguments(extra, site_name, admin_address)
    if data_root:
        server += ["-DataRoot", str(tmp_path / "data" / "ARCILOGGER")]
    result = run_powershell(
        engine,
        bundle / "installer" / "Install-Arcilogger.ps1",
        switch,
        "-PlanPath",
        str(plan),
        "-InstallDir",
        str(install_dir or tmp_path / "program" / "ARCILOGGER"),
        "-ProgramDataDir",
        str(tmp_path / "programdata" / "ARCILOGGER"),
        *server,
        *extra,
        env_extra=env_extra,
    )
    assert plan.is_file(), result.stdout + result.stderr
    document: dict[str, Any] = read_json(plan)
    return result, document


def assert_dry_run_exit(result: subprocess.CompletedProcess[str], plan: dict[str, Any]) -> None:
    """A dry run returns what the real run would: 2 when that would stop in the preflight."""
    assert plan["dry_run"] is True
    assert plan["would_stop"] == bool(plan["failures"])
    expected = 2 if plan["would_stop"] else 0
    assert result.returncode == expected, result.stdout + result.stderr
    assert plan["exit_code"] == expected


def _ids(document: dict[str, Any]) -> list[str]:
    return [item["id"] for item in document["items"]]


def _before(ids: list[str], *ordered: str) -> None:
    positions = [ids.index(step) for step in ordered]
    assert positions == sorted(positions), list(zip(ordered, positions, strict=True))


def _changes(ids: list[str]) -> list[str]:
    """The planned steps that change the system (no checks, no start, no health check)."""
    passive = ("bundle.", "preflight.", "upgrade.", "program.keep", "program.resume", "config.keep")
    return [
        i for i in ids if not i.startswith(passive) and i not in ("services.start", "health.check")
    ]


def test_dry_run_fresh_install(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle)
    assert_dry_run_exit(result, plan)
    assert plan["mode"] == "install"
    assert plan["bundle"]["version"] == "9.9.9"
    assert plan["bundle"]["release_date"] == BUNDLE_RELEASE_DATE
    assert plan["bundle"]["fingerprint"] == sha256_bytes((bundle / "SHA256SUMS").read_bytes())
    ids = _ids(plan)
    expected = [
        "bundle.verify", "preflight.windows", "preflight.postgresql", "preflight.admin-address",
        "preflight.license",
        "dirs.create", "program.create", "python.extract", "venv.create", "wheels.deps",
        "wheels.app", "web.copy", "caddy.install", "winsw.install", "support.copy",
        "version.write", "config.copy", "config.database-section", "config.validate",
        "secrets.init", "acl.apply", "database.provision", "services.install",
        "services.start-collector", "time.configure", "firewall.apply", "tasks.register",
        "database.upgrade", "services.start", "health.check",
    ]  # fmt: skip
    for step in expected:
        assert step in ids, step
    _before(ids, *expected)
    statuses = {item["id"]: item["status"] for item in plan["items"]}
    assert all(statuses[step] == "planned" for step in expected[expected.index("dirs.create") :])
    assert statuses["preflight.admin-address"] == "ok"
    assert not any(
        i.startswith(("program.remove", "database.backup", "services.stop")) for i in ids
    )
    # The schema migration is the last change: everything that can fail on its own comes first.
    assert _changes(ids)[-1] == "database.upgrade"
    items = {item["id"]: item for item in plan["items"]}
    data_root = str(tmp_path / "data" / "ARCILOGGER")
    # The new configuration gets the data root, as [collector] bind the server address, the
    # organisation name and (none given, nobody to ask) no local time source.
    assert items["config.copy"]["detail"] == {
        "data_dir": data_root.replace(chr(92), "/"),
        "collector_bind": SERVER_ADDRESS,
        "site_name": SITE_NAME,
        "ntp_server": "",
    }
    assert items["preflight.site-name"]["status"] == "ok"
    assert SITE_NAME in items["preflight.site-name"]["text"]
    assert items["preflight.ntp-server"]["status"] == "warn"
    # The new configuration has no log source (the example's are commented out): a warning that
    # says what to add, never a stop.
    sources = items["preflight.sources"]
    assert sources["status"] == "warn", sources
    assert sources["text"].startswith("Log kaynağı yok [no log source]: yeni arcilogger.toml")
    assert "[add the [[sources]] of your firewall and core router" in sources["text"]
    assert "DEVICE-SETUP.md" in sources["text"]
    assert "[the firewall opens 514 to no sender, no log is recorded]" in sources["text"]
    assert sources["text"] in plan["warnings"]
    assert sources["text"] not in plan["failures"]
    # 172.20.20.15 is not an address of the test machine: the collector could never start.
    bind = items["preflight.collector-bind"]
    assert bind["status"] == "fail", bind
    assert f"-ServerAddress {SERVER_ADDRESS}" in bind["text"]
    assert "-ServerAddress <adres>" in bind["text"], "the message says how to fix it"
    assert items["preflight.server-address"]["status"] == "ok"
    rendered = items["winsw.install"]["detail"]
    assert rendered["SITE_ADDRESSES"] == (
        "https://172.20.20.15, https://arcilogger.internal, https://localhost"
    )
    assert rendered["CONFIG"].endswith("arcilogger.toml")
    # No configuration yet: the defaults, which are also those of the example configuration.
    assert rendered["API_UPSTREAM"] == "127.0.0.1:8000"
    assert statuses["preflight.api-endpoint"] == "ok"
    assert "preflight.port-tcp8000" in ids, "the API port is probed like 514 and 443"
    acl_paths = {re.split(r"[\\/]", e["Path"])[-1] for e in items["acl.apply"]["detail"]}
    assert {"spool", "_audit", "vault", "seal", "secret.key", "caddy"} <= acl_paths
    firewall = items["firewall.apply"]["detail"]
    assert firewall["strict_outbound"] == "kept", "the recorded strict mode is not overridden"
    assert firewall["state_file"].endswith("firewall-state.json")
    assert not (tmp_path / "program").exists(), "a dry run changes nothing"
    assert not (tmp_path / "data").exists(), "a dry run changes nothing"


def test_dry_run_without_the_firewall_step_says_every_sender_is_unknown(
    engine: str, tmp_path: Path
) -> None:
    """-SkipFirewall: nothing keeps 514 closed, so without [[sources]] every sender is recorded
    as unknown-<ip> (parser chosen by content) and R15 watches nothing."""
    bundle = make_fake_bundle(tmp_path)
    _, plan = dry_run(engine, tmp_path, bundle, "-SkipFirewall", admin_address=None)
    check = {item["id"]: item for item in plan["items"]}["preflight.sources"]
    assert check["status"] == "warn", check
    assert "(unknown-<ip>)" in check["text"]
    assert "[every sender counts as unknown, no source is watched for silence]" in check["text"]
    assert "514" not in check["text"]


def test_dry_run_passes_strict_outbound_only_when_given(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    _, plan = dry_run(engine, tmp_path, bundle, "-StrictOutbound")
    items = {item["id"]: item for item in plan["items"]}
    assert items["firewall.apply"]["detail"]["strict_outbound"] is True


def test_dry_run_uses_the_remembered_admin_addresses(engine: str, tmp_path: Path) -> None:
    """Without -AdminAddress an upgrade keeps what the firewall state file remembers."""
    bundle = make_fake_bundle(tmp_path)
    install = tmp_path / "programdata" / "ARCILOGGER" / "install"
    install.mkdir(parents=True)
    state = {"strict_outbound": True, "admin_address": ["172.20.10.0/28"]}
    (install / "firewall-state.json").write_text(json.dumps(state), encoding="utf-8")
    _, plan = dry_run(engine, tmp_path, bundle, admin_address=None)
    items = {item["id"]: item for item in plan["items"]}
    assert items["firewall.apply"]["detail"]["admin_address"] == ["172.20.10.0/28"]
    assert items["firewall.apply"]["detail"]["strict_outbound"] == "kept"
    assert "172.20.10.0/28" in items["preflight.admin-address"]["text"]
    _, plan = dry_run(engine, tmp_path, bundle, "-AdminAddress", "172.20.10.37")
    items = {item["id"]: item for item in plan["items"]}
    assert items["firewall.apply"]["detail"]["admin_address"] == ["172.20.10.37"]


def test_dry_run_needs_the_organisation_name_and_the_admin_pc(engine: str, tmp_path: Path) -> None:
    """D1, D3: neither has a default. Nobody at the console (the tests, CI): a clear stop that
    says which parameter to give, with placeholder examples."""
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle, site_name=None, admin_address=None)
    assert_dry_run_exit(result, plan)
    assert result.returncode == 2
    items = {item["id"]: item for item in plan["items"]}
    site = items["preflight.site-name"]
    assert site["status"] == "fail"
    assert site["text"].startswith("Kuruluş adı gerekli [organisation name required]")
    assert '-SiteName "<kuruluş adı>"' in site["text"]
    admin = items["preflight.admin-address"]
    assert admin["status"] == "fail"
    assert "[admin PC address required]" in admin["text"]
    assert "-AdminAddress 10.0.0.10" in admin["text"]
    assert "10.0.0.0/24" in admin["text"]
    assert {site["text"], admin["text"]} <= set(plan["failures"])
    assert "Kurulum bilgileri [setup values]" not in result.stdout, "nobody to ask"
    # Without the firewall step the admin PC is not needed.
    _, plan = dry_run(engine, tmp_path, bundle, "-SkipFirewall", admin_address=None)
    items = {item["id"]: item for item in plan["items"]}
    assert "preflight.admin-address" not in items
    assert not [f for f in plan["failures"] if "-AdminAddress" in f], plan["failures"]


@pytest.mark.parametrize(
    ("arguments", "status", "fragment"),
    [
        ((), "warn", "[no local time source is set]"),
        (("-NtpServer", "10.0.0.1"), "ok", "10.0.0.1 (-NtpServer)"),
        (("-NtpServer", "ntp.example.com"), "ok", "ntp.example.com"),
        (("-NtpServer", "172.20.10"), "fail", "[invalid IPv4 address]"),
        (("-SiteName", "x" * 201), "fail", "[characters, at most 200]"),
    ],
)
def test_dry_run_checks_the_time_source_and_the_name(
    engine: str, tmp_path: Path, arguments: tuple[str, ...], status: str, fragment: str
) -> None:
    """D2: without a local time source only the public fallback is used, with a warning."""
    bundle = make_fake_bundle(tmp_path)
    _, plan = dry_run(engine, tmp_path, bundle, *arguments)
    items = {item["id"]: item for item in plan["items"]}
    check = items["preflight.site-name" if "-SiteName" in arguments else "preflight.ntp-server"]
    assert check["status"] == status, check
    assert fragment in check["text"], check
    if status == "warn":
        assert "time.ume.tubitak.gov.tr" in check["text"], "names the server the clock follows"
        assert "-NtpServer" in check["text"], "and how to set a local one"
        assert check["text"] in plan["warnings"]
    if status == "fail":
        assert check["text"] in plan["failures"]
    if "-NtpServer" in arguments and status == "ok":
        assert items["config.copy"]["detail"]["ntp_server"] == arguments[1]


def _existing_config(tmp_path: Path, body: str) -> Path:
    programdata = tmp_path / "programdata" / "ARCILOGGER"
    programdata.mkdir(parents=True, exist_ok=True)
    data_root = (tmp_path / "data" / "ARCILOGGER").as_posix()
    config = programdata / "arcilogger.toml"
    config.write_text(f'data_dir = "{data_root}"\n{body}', encoding="utf-8")
    return config


def test_dry_run_upgrade_keeps_the_configured_name_and_time_source(
    engine: str, tmp_path: Path
) -> None:
    """D1, D2: an upgrade asks for nothing and changes nothing the configuration already says;
    a given parameter is written into it (only that key)."""
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    _existing_config(tmp_path, 'site_name = "Kurum A.Ş."\n[clock]\nntp_server = "10.0.0.1"\n')
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", site_name=None)
    assert_dry_run_exit(result, plan)
    items = {item["id"]: item for item in plan["items"]}
    assert items["preflight.site-name"]["status"] == "ok"
    assert "Kurum A.Ş. (arcilogger.toml)" in items["preflight.site-name"]["text"]
    assert items["preflight.ntp-server"]["status"] == "ok"
    assert "10.0.0.1 ([clock] ntp_server)" in items["preflight.ntp-server"]["text"]
    assert "config.update" not in items, "nothing to write"
    assert "config.keep" in items
    # Given on the upgrade: written into the existing file, the previous one kept.
    _, plan = dry_run(
        engine, tmp_path, bundle, "-Upgrade", "-SiteName", "Yeni Kurum", "-NtpServer", "10.0.0.2"
    )
    items = {item["id"]: item for item in plan["items"]}
    update = items["config.update"]
    assert update["status"] == "planned"
    assert update["detail"]["keys"] == ["site_name", "clock.ntp_server"]
    assert ".before-" in update["detail"]["backup"]
    ids = _ids(plan)
    _before(ids, "config.keep", "config.update", "config.validate", "database.upgrade")
    # The same values as configured: nothing to write.
    _, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", "-SiteName", "Kurum A.Ş.")
    assert "config.update" not in _ids(plan)


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a symbolic link to the test interpreter acts as the venv python",
)
def test_dry_run_upgrade_keeps_a_configuration_without_sources(engine: str, tmp_path: Path) -> None:
    """No log source is a valid state: an upgrade (or a repair) warns and says what to add
    instead of stopping, and never touches the existing arcilogger.toml."""
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    (program / "venv" / "Scripts").mkdir(parents=True)
    (program / "venv" / "Scripts" / "python.exe").symlink_to(sys.executable)
    config = _existing_config(
        tmp_path, 'site_name = "Kurum A.Ş."\n#[[sources]]\n[clock]\nntp_server = "10.0.0.1"\n'
    )
    before = config.read_bytes()
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", site_name=None)
    assert_dry_run_exit(result, plan)
    items = {item["id"]: item for item in plan["items"]}
    check = items["preflight.sources"]
    assert check["status"] == "warn", check
    assert f"{config} içinde [[sources]] yok [no [[sources]] in it]" in check["text"]
    assert check["text"] not in plan["failures"]
    assert "config.keep" in items
    assert "config.copy" not in items, "an existing configuration is never replaced"
    assert "config.update" not in items, "nothing given, nothing written"
    assert config.read_bytes() == before


_FW_SOURCE = '[[sources]]\nname = "fw"\ntype = "fortigate"\naddress = "{}"\n'


@pytest.mark.parametrize(
    ("sources", "extra", "status", "expected"),
    [
        # Commented out, as the example writes them: a warning that says what to add.
        (
            '#[[sources]]\n#address = "172.31.255.1"\n',
            (),
            "warn",
            "içinde [[sources]] yok [no [[sources]] in it]",
        ),
        # Refused before any change, as Set-ArciFirewall.ps1 would refuse it halfway.
        (_FW_SOURCE.format("172.31.255"), (), "fail", "address geçersiz [invalid]: 172.31.255"),
        (_FW_SOURCE.format("172.31.255.1"), (), "ok", "514 yalnızca [only from] 1 gönderici"),
        (_FW_SOURCE.format("172.31.255.1"), ("-SkipFirewall",), "ok", "[log sources]: 1"),
    ],
    ids=["commented-out", "invalid-address", "one-source", "without-firewall-step"],
)
def test_dry_run_reinstall_reads_the_kept_sources_without_python(
    engine: str,
    tmp_path: Path,
    sources: str,
    extra: tuple[str, ...],
    status: str,
    expected: str,
) -> None:
    """A new installation over the arcilogger.toml a removal kept (the configuration folder stays,
    the program folder and its Python are gone): the sources preflight reads the file without
    Python, so a kept file without [[sources]] gets the warning and the next steps' instruction
    (``$script:SourceCount``), and an invalid address stops the run before any change."""
    bundle = make_fake_bundle(tmp_path)
    config = _existing_config(tmp_path, f'site_name = "Kurum A.Ş."\n{sources}')
    assert not (tmp_path / "program").exists(), "nothing installed: no venv"
    admin = None if "-SkipFirewall" in extra else ADMIN_ADDRESS
    result, plan = dry_run(engine, tmp_path, bundle, *extra, site_name=None, admin_address=admin)
    assert_dry_run_exit(result, plan)
    assert plan["mode"] == "install"
    items = {item["id"]: item for item in plan["items"]}
    check = items["preflight.sources"]
    assert check["status"] == status, check
    assert expected in check["text"]
    if status == "warn":
        assert check["text"].startswith(f"Log kaynağı yok [no log source]: {config} içinde")
        assert check["text"] in plan["warnings"]
    if status == "fail":
        assert check["text"] in plan["failures"]
        assert result.returncode == 2
    else:
        assert check["text"] not in plan["failures"]
    assert "config.keep" in items
    assert "config.copy" not in items, "a kept configuration is never replaced"


def test_dry_run_upgrade_stops_without_an_organisation_name(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    config = _existing_config(tmp_path, 'site_name = ""\n')
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", site_name=None)
    assert result.returncode == 2
    check = {item["id"]: item for item in plan["items"]}["preflight.site-name"]
    assert check["status"] == "fail"
    assert str(config) in check["text"], "names the file"
    assert "-SiteName" in check["text"]
    # A configuration whose keys the installer cannot read safely is not guessed at.
    _existing_config(tmp_path, 'site_name = """Kurum"""\n')
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade")
    assert result.returncode == 2
    check = {item["id"]: item for item in plan["items"]}["preflight.setup-values"]
    assert check["status"] == "fail"
    assert "[not a one-line string: edit the file by hand]" in check["text"]


# Resolve-SetupValues for real (dot-sourced installer) with a person at the console: Read-Host
# answers come from the test, every prompt is recorded.
_SETUP_PROMPT_SCRIPT = r"""
param([string]$Installer, [string]$Base, [string]$DataRoot, [string]$Scenario,
    [string]$InputPath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
$parameters = @{
    InstallDir = (Join-Path $Base 'program/ARCILOGGER')
    ProgramDataDir = (Join-Path $Base 'programdata/ARCILOGGER')
    DataRoot = $DataRoot; BundleRoot = (Join-Path $Base 'bundle'); ServerAddress = '172.20.20.15'
}
if ($Scenario -eq 'given') {
    $parameters['SiteName'] = 'Kurum'
    $parameters['NtpServer'] = ''
    $parameters['AdminAddress'] = @('10.0.0.10')
}
if ($Scenario -eq 'no-firewall') { $parameters['SkipFirewall'] = $true }
. $Installer @parameters
function Test-ArciInteractiveConsole { $Scenario -ne 'quiet' }
$global:ArciAnswers = New-Object 'System.Collections.Generic.Queue[string]'
# Assigned first: Windows PowerShell 5.1 passes a JSON array on as one object.
$answers = [System.IO.File]::ReadAllText($InputPath, [System.Text.Encoding]::UTF8) |
    ConvertFrom-Json
foreach ($answer in @($answers)) { $global:ArciAnswers.Enqueue([string]$answer) }
$global:ArciPrompts = New-Object 'System.Collections.Generic.List[string]'
function global:Read-Host {
    param([Parameter(Position = 0)][string]$Prompt, [switch]$AsSecureString)
    $global:ArciPrompts.Add($Prompt)
    if ($global:ArciAnswers.Count -eq 0) { throw 'no console input' }
    $global:ArciAnswers.Dequeue()
}
Resolve-SetupValues
$report = [ordered]@{
    site = $script:SiteNameValue; site_source = $script:SiteNameSource
    ntp = $script:NtpServerValue; ntp_source = $script:NtpServerSource
    admin = @(Get-EffectiveAdminAddress); prompts = $global:ArciPrompts.ToArray()
    error = $script:ConfigReadError
}
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($OutputPath, ($report | ConvertTo-Json -Depth 4), $utf8)
"""

ORGANISATION_PROMPT = "Kuruluş adı; raporlarda, e-postalarda ve kanıt paketlerinde görünür"
TIME_PROMPT = "Yerel saat kaynağı: ana yönlendiricinin ya da yerel bir NTP sunucusunun"
ADMIN_PROMPT = "Yönetici PC'sinin IP adresi ya da ağı"
SETUP_PROMPT_CASES = [
    # (scenario, configuration, remembered admin, answers, expected values, prompts)
    ("new", None, None, ["", SITE_NAME, "172.20.10", "10.0.0.1", "10.0.0.10"],
     (SITE_NAME, "prompt", "10.0.0.1", "prompt", ["10.0.0.10"]),
     [ORGANISATION_PROMPT] * 2 + [TIME_PROMPT] * 2 + [ADMIN_PROMPT]),
    ("new-enter", None, None, ["Kurum", "", "10.0.0.0/24"],
     ("Kurum", "prompt", "", "prompt", ["10.0.0.0/24"]),
     [ORGANISATION_PROMPT, TIME_PROMPT, ADMIN_PROMPT]),
    ("upgrade", 'site_name = "Kurum"\n[clock]\nntp_server = "10.0.0.1"\n', ["10.0.0.10"], [],
     ("Kurum", "config", "10.0.0.1", "config", ["10.0.0.10"]), []),
    ("empty-name", 'site_name = ""\n[clock]\nntp_server = ""\n', ["10.0.0.10"], ["Kurum"],
     ("Kurum", "prompt", "", "config", ["10.0.0.10"]), [ORGANISATION_PROMPT]),
    ("given", None, None, [], ("Kurum", "parameter", "", "parameter", ["10.0.0.10"]), []),
    ("no-firewall", None, None, ["Kurum", ""], ("Kurum", "prompt", "", "prompt", []),
     [ORGANISATION_PROMPT, TIME_PROMPT]),
    ("quiet", None, None, [], ("", "none", "", "none", []), []),
    # The console cannot be read: every question once, no loop, the preflight stops.
    ("no-answer", None, None, [], ("", "none", "", "none", []),
     [ORGANISATION_PROMPT, TIME_PROMPT, ADMIN_PROMPT]),
]  # fmt: skip


@pytest.mark.parametrize(
    ("scenario", "config", "remembered", "answers", "expected", "prompts"),
    SETUP_PROMPT_CASES,
    ids=[case[0] for case in SETUP_PROMPT_CASES],
)
def test_installer_asks_at_the_console_only_for_what_is_missing(
    engine: str,
    tmp_path: Path,
    scenario: str,
    config: str | None,
    remembered: list[str] | None,
    answers: list[str],
    expected: tuple[str, str, str, str, list[str]],
    prompts: list[str],
) -> None:
    """D1-D3: given, configured or remembered values are never asked again; a missing one is
    asked (again while it is invalid) when a person sits at the console, Turkish first."""
    base = tmp_path / "base"
    make_fake_bundle(base)
    data_root = base / "data" / "ARCILOGGER"
    programdata = base / "programdata" / "ARCILOGGER"
    (programdata / "install").mkdir(parents=True)
    if config is not None:
        text = f'data_dir = "{data_root.as_posix()}"\n{config}'
        (programdata / "arcilogger.toml").write_text(text, encoding="utf-8")
    if remembered is not None:
        state = {"schema": 1, "admin_address": remembered}
        (programdata / "install" / "firewall-state.json").write_text(json.dumps(state), "utf-8")
    answers_path = tmp_path / "answers.json"
    answers_path.write_text(json.dumps(answers), encoding="utf-8")
    output = tmp_path / "prompts.json"
    script = write_script(tmp_path / "prompts.ps1", _SETUP_PROMPT_SCRIPT)
    result = run_powershell(
        engine, script, "-Installer", str(INSTALL_SCRIPT), "-Base", str(base),
        "-DataRoot", str(data_root), "-Scenario", scenario,
        "-InputPath", str(answers_path), "-OutputPath", str(output),
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output)
    assert report["error"] is None
    site, site_source, ntp, ntp_source, admin = expected
    assert (report["site"], report["site_source"]) == (site, site_source)
    assert (report["ntp"], report["ntp_source"]) == (ntp, ntp_source)
    assert report["admin"] == admin
    assert len(report["prompts"]) == len(prompts), report["prompts"]
    for asked, start in zip(report["prompts"], prompts, strict=True):
        assert asked.startswith(start), asked
        assert "[" in asked, "the English text follows in brackets"
    if any(start == TIME_PROMPT for start in prompts):
        assert any("time.ume.tubitak.gov.tr" in asked for asked in report["prompts"])


def _sites(plan: dict[str, Any]) -> str:
    items = {item["id"]: item for item in plan["items"]}
    sites: str = items["winsw.install"]["detail"]["SITE_ADDRESSES"]
    return sites


def test_dry_run_remembers_the_server_address_and_name(engine: str, tmp_path: Path) -> None:
    """install\\state.json keeps -ServerAddress and -ServerName; a given parameter wins."""
    bundle = make_fake_bundle(tmp_path)
    install = tmp_path / "programdata" / "ARCILOGGER" / "install"
    install.mkdir(parents=True)
    state = {"database_provisioned": False, "server_address": "127.0.0.1", "server_name": "logs"}
    (install / "state.json").write_text(json.dumps(state), encoding="utf-8")
    _, plan = dry_run(engine, tmp_path, bundle, server_address=None)
    items = {item["id"]: item for item in plan["items"]}
    assert _sites(plan) == "https://127.0.0.1, https://logs, https://localhost"
    assert "[remembered]" in items["preflight.server-address"]["text"]
    assert items["config.copy"]["detail"]["collector_bind"] == "127.0.0.1"
    # Loopback: the collector can start, but no device reaches it (the CI end-to-end case).
    assert items["preflight.collector-bind"]["status"] == "warn"
    _, plan = dry_run(engine, tmp_path, bundle, "-ServerName", "other.internal")
    assert _sites(plan) == f"https://{SERVER_ADDRESS}, https://other.internal, https://localhost"


def test_dry_run_detects_the_server_address_or_asks_for_it(engine: str, tmp_path: Path) -> None:
    """Neither given nor remembered: the only adapter with a default gateway, or a clear stop."""
    bundle = make_fake_bundle(tmp_path)
    _, plan = dry_run(engine, tmp_path, bundle, server_address=None)
    items = {item["id"]: item for item in plan["items"]}
    check = items["preflight.server-address"]
    if check["status"] == "fail":
        assert "-ServerAddress" in check["text"]
        assert check["text"] in plan["failures"]
        return
    detected = re.search(r"\[server address detected\]: (\S+) ", check["text"])
    assert detected is not None, check
    assert items["config.copy"]["detail"]["collector_bind"] == detected.group(1)
    assert _sites(plan).startswith(f"https://{detected.group(1)}, https://arcilogger.internal")
    # A detected address is one of this computer's: the collector can listen on it.
    assert items["preflight.collector-bind"]["status"] == "ok", items["preflight.collector-bind"]


@pytest.mark.parametrize(
    ("collector", "status"),
    [
        ('[collector]\nbind = "0.0.0.0"\n', "ok"),
        ("[collector]\nudp_port = 514\n", "ok"),  # no bind: the collector's default, 0.0.0.0
        ('[collector]\nbind = "127.0.0.1"\n', "warn"),  # the CI end-to-end configuration
        (f'[collector]\nbind = "{FOREIGN_ADDRESS}"\n', "fail"),
        ("[collector]\nbind = 'arcilogger.internal'\n", "fail"),
    ],
)
def test_dry_run_checks_the_configured_collector_address(
    engine: str, tmp_path: Path, collector: str, status: str
) -> None:
    """[collector] bind of an existing arcilogger.toml must be an address of this computer."""
    bundle = make_fake_bundle(tmp_path)
    programdata = tmp_path / "programdata" / "ARCILOGGER"
    programdata.mkdir(parents=True)
    data_root = (tmp_path / "data" / "ARCILOGGER").as_posix()
    config = programdata / "arcilogger.toml"
    config.write_text(f'data_dir = "{data_root}"\n\n{collector}', encoding="utf-8")
    result, plan = dry_run(engine, tmp_path, bundle, server_address="127.0.0.1")
    assert_dry_run_exit(result, plan)
    check = {item["id"]: item for item in plan["items"]}["preflight.collector-bind"]
    assert check["status"] == status, check
    if status == "fail":
        assert check["text"] in plan["failures"]
        assert str(config) in check["text"], "names the file to change"
        assert WILDCARD in check["text"], "and the values that work"
        assert result.returncode == 2


def test_dry_run_refuses_a_server_address_of_another_computer(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle, server_address=FOREIGN_ADDRESS)
    assert result.returncode == 2, result.stdout + result.stderr
    check = {item["id"]: item for item in plan["items"]}["preflight.collector-bind"]
    assert check["status"] == "fail"
    assert f"-ServerAddress {FOREIGN_ADDRESS}" in check["text"]
    _, plan = dry_run(engine, tmp_path, bundle, server_address="not-an-address")
    items = {item["id"]: item for item in plan["items"]}
    assert items["preflight.server-address-format"]["status"] == "fail"
    assert "preflight.collector-bind" not in items


def test_whatif_is_a_dry_run(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle, switch="-WhatIf")
    assert_dry_run_exit(result, plan)
    statuses = {item["id"]: item["status"] for item in plan["items"]}
    assert statuses["program.create"] == statuses["database.upgrade"] == "planned"
    assert not (tmp_path / "program").exists(), "-WhatIf changes nothing"
    assert not (tmp_path / "data").exists(), "-WhatIf changes nothing"
    assert not (tmp_path / "programdata").exists(), "-WhatIf changes nothing"


def test_dry_run_upgrade_backs_up_first_and_migrates_last(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    programdata = tmp_path / "programdata" / "ARCILOGGER"
    programdata.mkdir(parents=True)
    data_root = (tmp_path / "data" / "ARCILOGGER").as_posix()
    (programdata / "arcilogger.toml").write_text(f'data_dir = "{data_root}"\n', encoding="utf-8")
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade")
    assert_dry_run_exit(result, plan)
    assert plan["mode"] == "upgrade"
    ids = _ids(plan)
    _before(
        ids,
        "upgrade.versions",
        "database.backup",
        "tasks.disable",
        "services.stop",
        # Moved while the services are still registered: if Windows refuses the move,
        # nothing else has changed yet.
        "program.move-previous",
        "services.remove",
        "python.extract",
        "wheels.deps",
        "config.keep",
        "acl.apply",
        "services.install",
        "services.start-collector",
        "firewall.apply",
        "tasks.register",
        "database.upgrade",
        "services.start",
        "health.check",
    )
    assert _changes(ids)[0] == "database.backup", "no change before the backup"
    assert _changes(ids)[-1] == "database.upgrade", "the migration is the last change"
    assert "config.copy" not in ids, "an existing configuration is never replaced"
    assert plan["paths"]["previous_dir"] == str(program) + ".previous"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a symbolic link to the test interpreter acts as the venv python",
)
def test_dry_run_upgrade_checks_firewall_inputs_before_any_change(
    engine: str, tmp_path: Path
) -> None:
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    (program / "venv" / "Scripts").mkdir(parents=True)
    (program / "venv" / "Scripts" / "python.exe").symlink_to(sys.executable)
    programdata = tmp_path / "programdata" / "ARCILOGGER"
    programdata.mkdir(parents=True)
    data_root = (tmp_path / "data" / "ARCILOGGER").as_posix()
    (programdata / "arcilogger.toml").write_text(
        f'data_dir = "{data_root}"\n'
        '[[sources]]\nname = "fw"\ntype = "fortigate"\naddress = "172.31.255"\n',
        encoding="utf-8",
    )
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", "-AdminAddress", "172.20.10")
    assert_dry_run_exit(result, plan)
    assert result.returncode == 2
    statuses = {item["id"]: item["status"] for item in plan["items"]}
    assert statuses["preflight.admin-address"] == "fail"
    assert statuses["preflight.sources"] == "fail"
    assert any("172.31.255" in failure for failure in plan["failures"])


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a symbolic link to the test interpreter acts as the venv python",
)
def test_dry_run_upgrade_points_caddy_at_the_configured_api_port(
    engine: str, tmp_path: Path
) -> None:
    """ArciWeb.xml, the health check and the port probe follow [api] port of arcilogger.toml."""
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    (program / "venv" / "Scripts").mkdir(parents=True)
    (program / "venv" / "Scripts" / "python.exe").symlink_to(sys.executable)
    programdata = tmp_path / "programdata" / "ARCILOGGER"
    programdata.mkdir(parents=True)
    data_root = (tmp_path / "data" / "ARCILOGGER").as_posix()
    (programdata / "arcilogger.toml").write_text(
        f'data_dir = "{data_root}"\n'
        '[[sources]]\nname = "fw"\ntype = "fortigate"\naddress = "172.31.255.1"\n'
        '[api]\nhost = "127.0.0.1"\nport = 18641\n',
        encoding="utf-8",
    )
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade")
    assert_dry_run_exit(result, plan)
    items = {item["id"]: item for item in plan["items"]}
    assert items["winsw.install"]["detail"]["API_UPSTREAM"] == "127.0.0.1:18641"
    assert "127.0.0.1:18641" in items["preflight.api-endpoint"]["text"]
    assert items["preflight.api-proxy"]["status"] == "ok"
    assert "preflight.port-tcp18641" in items
    assert "preflight.port-tcp8000" not in items
    # The rendered service definition carries it (Expand-ArciTemplate fails on a leftover).
    template = (SERVICES / "ArciWeb.xml").read_text(encoding="utf-8")
    assert '<env name="ARCILOGGER_API_UPSTREAM" value="{{API_UPSTREAM}}"/>' in template
    for xml_file in SERVICES.glob("*.xml"):
        assert ':8000"' not in xml_file.read_text(encoding="utf-8"), xml_file.name
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert "http://127.0.0.1:8000" not in script, "no fixed API address in the health check"


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a symbolic link to the test interpreter acts as the venv python",
)
@pytest.mark.parametrize(
    ("mode", "api_table", "status"),
    [
        # Caddy would connect over ::1, uvicorn trusts only 127.0.0.1 (the default list): every
        # request would be recorded as coming from ::1 - refused before anything changes.
        ("-Upgrade", 'host = "::"\n', "fail"),
        ("-Upgrade", 'host = "::1"\nforwarded_allow_ips = "127.0.0.1"\n', "fail"),
        ("-Upgrade", 'host = "::1"\nforwarded_allow_ips = "127.0.0.1,::1"\n', "ok"),
        ("-Upgrade", 'host = "172.20.20.15"\nforwarded_allow_ips = "172.20.20.0/24"\n', "ok"),
        ("-Upgrade", 'host = "localhost"\n', "warn"),
        # A rollback puts back a version that worked: it warns instead of waiting for the fix.
        ("-Rollback", 'host = "::1"\n', "warn"),
    ],
)
def test_dry_run_refuses_an_api_address_the_api_does_not_trust_as_proxy(
    engine: str, tmp_path: Path, mode: str, api_table: str, status: str
) -> None:
    """[api] forwarded_allow_ips must cover the address Caddy connects from (the API host)."""
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    (program / "venv" / "Scripts").mkdir(parents=True)
    (program / "venv" / "Scripts" / "python.exe").symlink_to(sys.executable)
    programdata = tmp_path / "programdata" / "ARCILOGGER"
    programdata.mkdir(parents=True)
    data_root = (tmp_path / "data" / "ARCILOGGER").as_posix()
    (programdata / "arcilogger.toml").write_text(
        f'data_dir = "{data_root}"\n'
        '[[sources]]\nname = "fw"\ntype = "fortigate"\naddress = "172.31.255.1"\n'
        f"[api]\n{api_table}",
        encoding="utf-8",
    )
    result, plan = dry_run(engine, tmp_path, bundle, mode)
    if mode == "-Upgrade":
        assert_dry_run_exit(result, plan)
    check = {item["id"]: item for item in plan["items"]}["preflight.api-proxy"]
    assert check["status"] == status, check
    if status == "fail":
        assert check["text"] in plan["failures"]
        assert "forwarded_allow_ips" in check["text"]
    else:
        assert not [f for f in plan["failures"] if "forwarded_allow_ips" in f], plan["failures"]


def test_dry_run_auto_switches_to_upgrade(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    result, plan = dry_run(engine, tmp_path, bundle, "-Auto")
    assert_dry_run_exit(result, plan)
    assert plan["mode"] == "upgrade"
    assert "program.move-previous" in _ids(plan)


def test_dry_run_resumes_an_interrupted_installation(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    program = tmp_path / "program" / "ARCILOGGER"
    (program / "python").mkdir(parents=True)
    marker = {"product": "ARCILOGGER", "version": "9.9.9", "fingerprint": "f" * 64}
    (program / "INSTALL-IN-PROGRESS.json").write_text(json.dumps(marker), encoding="utf-8")
    result, plan = dry_run(engine, tmp_path, bundle, "-Auto")
    assert_dry_run_exit(result, plan)
    assert plan["mode"] == "install"
    ids = _ids(plan)
    _before(ids, "program.resume", "program.clean-partial", "program.create", "version.write")


def test_dry_run_refuses_to_install_over_a_foreign_folder(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    program = tmp_path / "program" / "ARCILOGGER"
    program.mkdir(parents=True)
    (program / "important.txt").write_text("not ours", encoding="utf-8")
    result, plan = dry_run(engine, tmp_path, bundle)
    assert result.returncode == 2, result.stdout + result.stderr
    assert "program.create" not in _ids(plan)


def test_dry_run_uninstall_removes_only_arcilogger_program_folders(
    engine: str, tmp_path: Path
) -> None:
    bundle = make_fake_bundle(tmp_path)
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    previous = write_version(Path(f"{program}.previous"), "9.9.8", "2" * 64)
    failed = Path(f"{program}.failed-20261012000000")
    failed.mkdir()
    (failed / "INSTALL-IN-PROGRESS.json").write_text(
        json.dumps({"product": "ARCILOGGER"}), encoding="utf-8"
    )
    notes = Path(f"{program}.notes")  # an operator's folder next to it: never touched
    notes.mkdir()
    (notes / "keep.txt").write_text("mine", encoding="utf-8")
    result, plan = dry_run(engine, tmp_path, bundle, "-Uninstall")
    assert_dry_run_exit(result, plan)
    ids = _ids(plan)
    _before(
        ids,
        "tasks.disable",
        "services.stop",
        "services.remove",
        "tasks.unregister",
        "firewall.remove",
    )
    removed = [item["detail"] for item in plan["items"] if item["id"] == "program.remove"]
    assert removed == [str(program), str(previous), str(failed)]
    kept = {item["id"]: item for item in plan["items"] if item["status"] == "kept"}
    assert set(kept) == {"keep.data", "keep.config", "keep.database"}
    assert kept["keep.data"]["detail"] == str(tmp_path / "data" / "ARCILOGGER")
    assert kept["keep.config"]["detail"] == str(tmp_path / "programdata" / "ARCILOGGER")
    assert (notes / "keep.txt").is_file()


@pytest.mark.parametrize("case", ["not-arcilogger", "windows-folder"])
def test_dry_run_uninstall_refuses_folders_it_did_not_create(
    engine: str, tmp_path: Path, case: str
) -> None:
    """A mistyped -InstallDir must never become a recursive delete."""
    bundle = make_fake_bundle(tmp_path)
    target = tmp_path / "target"
    (target / "important").mkdir(parents=True)
    (target / "important" / "file.txt").write_text("keep", encoding="utf-8")
    env_extra = None
    if case == "windows-folder":
        # Even with an ARCILOGGER VERSION.json inside, a Windows folder is never deleted.
        write_version(target, "9.9.9", "1" * 64)
        env_extra = {"PUBLIC": str(target)}
    result, plan = dry_run(
        engine, tmp_path, bundle, "-Uninstall", install_dir=target, env_extra=env_extra
    )
    assert result.returncode == 2, result.stdout + result.stderr
    ids = _ids(plan)
    assert "program.remove" not in ids
    assert "services.stop" not in ids, "refused before any change"
    assert "program.refuse" in ids
    assert (target / "important" / "file.txt").is_file()


def test_dry_run_rollback_plan(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.10", "1" * 64)
    write_version(Path(f"{program}.previous"), "9.9.9", "2" * 64)
    result, plan = dry_run(engine, tmp_path, bundle, "-Rollback", "-RestoreDatabase")
    assert_dry_run_exit(result, plan)
    _before(
        _ids(plan),
        "tasks.disable",
        "services.stop",
        "program.swap",
        "services.remove",
        "database.restore",
        "config.compatible",
        "system.restore",
        "services.install",
        "tasks.register",
        "services.start",
        "health.check",
    )


def test_dry_run_rollback_sets_firewall_and_time_from_the_restored_configuration(
    engine: str, tmp_path: Path
) -> None:
    """ApplySystem leaves with the newer tasks but set the 514 rule and the time peers from the
    newer configuration: before the services start, the restored program's own
    Set-ArciFirewall.ps1 (full mode, the remembered choices of the state file) and
    Set-ArciTime.ps1 run again, so a source the configuration put back lists is let through."""
    bundle = make_fake_bundle(tmp_path)
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.10", "1" * 64)
    write_version(Path(f"{program}.previous"), "9.9.9", "2" * 64)
    state = tmp_path / "programdata" / "ARCILOGGER" / "install" / "firewall-state.json"
    state.parent.mkdir(parents=True)
    state.write_text(json.dumps({"schema": 1, "admin_address": [ADMIN_ADDRESS]}), "utf-8")
    result, plan = dry_run(engine, tmp_path, bundle, "-Rollback")
    assert_dry_run_exit(result, plan)
    ids = _ids(plan)
    _before(ids, "config.compatible", "system.restore", "services.start")
    detail = {item["id"]: item for item in plan["items"]}["system.restore"]["detail"]
    for key, name in (("firewall", "Set-ArciFirewall.ps1"), ("time", "Set-ArciTime.ps1")):
        assert _windows_path(detail[key]) == _windows_path(f"{program}/scripts/{name}"), key
    assert detail["state_file"] == str(state)
    # Without a firewall ARCILOGGER manages, and with -SkipTimeSetup, nothing to set.
    result, plan = dry_run(engine, tmp_path, bundle, "-Rollback", "-SkipFirewall", "-SkipTimeSetup")
    assert_dry_run_exit(result, plan)
    detail = {item["id"]: item for item in plan["items"]}["system.restore"]["detail"]
    assert (detail["firewall"], detail["time"]) == (None, None)


def test_dry_run_rollback_restores_only_the_database_without_previous(
    engine: str, tmp_path: Path
) -> None:
    """After a failed schema migration the previous program is already back: database only."""
    bundle = make_fake_bundle(tmp_path)
    write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    dump = tmp_path / "arcilogger-before.dump"
    dump.write_bytes(b"PGDMP")
    result, plan = dry_run(
        engine, tmp_path, bundle, "-Rollback", "-RestoreDatabase", "-DatabaseDump", str(dump)
    )
    assert_dry_run_exit(result, plan)
    ids = _ids(plan)
    _before(
        ids,
        "rollback.database-only",
        "services.stop",
        "database.restore",
        "services.start-mode",
        "services.start",
    )
    assert not {"program.swap", "services.remove", "tasks.disable"} & set(ids)
    items = {item["id"]: item for item in plan["items"]}
    stopped = items["services.stop"]["text"]
    for name in ("ArciIngest", "ArciApi", "ArciWorker"):
        assert name in stopped, name
    assert "ArciCollector" not in stopped, "the collector keeps receiving syslog"
    assert items["database.restore"]["detail"] == str(dump)


def test_dry_run_rollback_without_previous_version_stops(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    result, plan = dry_run(engine, tmp_path, bundle, "-Rollback")
    assert result.returncode == 2, result.stdout + result.stderr
    assert "program.swap" not in _ids(plan)
    assert "[no previous version]" in result.stdout


def test_dry_run_refuses_a_modified_bundle(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    (bundle / "web" / "index.html").write_text("<script>tampered</script>", encoding="utf-8")
    result, plan = dry_run(engine, tmp_path, bundle)
    assert result.returncode == 2, result.stdout + result.stderr
    assert plan["exit_code"] == 2
    verify = next(item for item in plan["items"] if item["id"] == "bundle.verify")
    assert verify["status"] == "fail"
    assert any("web/index.html" in problem for problem in verify["detail"])
    assert "program.create" not in _ids(plan)


def test_dry_run_refuses_component_not_matching_catalogue(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    catalogue = json.loads((bundle / "components.json").read_text(encoding="utf-8"))
    catalogue["components"][2]["sha256"] = "f" * 63 + "e"
    (bundle / "components.json").write_text(json.dumps(catalogue), encoding="utf-8")
    write_sums(bundle)
    result, plan = dry_run(engine, tmp_path, bundle)
    assert result.returncode == 2, result.stdout + result.stderr
    assert plan["exit_code"] == 2
    assert "caddy" in result.stdout


def test_dry_run_refuses_two_modes(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", "-Uninstall")
    assert result.returncode == 2
    assert plan["items"] == []


# --- licence (contract section 9) ----------------------------------------------------------------
#
# The installer reads a licence file (-LicenseFile) and the product's status file without Python
# and without trusting them (the signature is checked by "arcilogger-db license install" and again
# by the product at run time). The CLI of contract 6.8 and the status file of 4.4 are fixtures here.

LICENSE_ID = "ARCI-L-20261020-7Q3KX9MD"
SERVER_ID = "XW68-7K1Z-5FGV-AGT8"
LICENSE_STATUS_SCHEMA = "arcilogger.license-status/1"
NO_LICENCE_TEXT = "Kurulu lisans yok; lisans durumu kurulumdan sonra geçerli [no licence installed]"
UNCOVERED_SUFFIX = "ücretli modüller kapanacak [paid modules will be off]"
TRIAL_OVER_WARNING = (
    "Bu kurulumun lisansı yok ve deneme süresi kasadaki eski mühürlü günler nedeniyle dolmuş "
    "sayılacak: Kullanım analizi, Zamanlanmış raporlar ve CSV içe aktarma kapanacak; etkin "
    "kullanıcı sayısı 3'ün üzerindeyse yeni kullanıcı eklenemez. 5651 işlevleri etkilenmez. "
    "Yükseltmeden sonra web arayüzünde Ayarlar → Lisans sayfası istek kodunu gösterir (kurulumun "
    "sonunda da yazılır); kodu lisans verene gönderin, gelen .lic dosyasını aynı sayfadan "
    "yükleyin. Lisans yüklenene kadar ücretli modüller kapalı kalır; lisans yüklenip Kullanım "
    "analizi açılınca son günlerin (varsayılan 7 gün, usage.auto_rebuild_days) kullanım özetleri "
    "kendiliğinden, daha eskileri Kullanım sayfasındaki Yeniden hesapla ile yeniden hesaplanır.",
    "[This installation has no licence and its trial counts as over because of older sealed "
    "days: usage analytics, scheduled reports and CSV import will be off; with more than 3 active "
    "users no new user can be added. 5651 functions are not affected. After the upgrade the web "
    "interface shows the request code under Settings → Licence (the end of the installation "
    "prints it too); send it to the licensor and upload the .lic you receive on the same page. "
    "Until the licence is installed the paid modules stay off; once it is installed and usage "
    "analytics is on, the usage rollups of the recent days (7 by default, usage.auto_rebuild_days) "
    "are rebuilt by themselves, older days with Recompute on the Usage page.]",
)
#: The two lines of the next steps that say where the request code goes (contract 9.4).
REQUEST_CODE_MAIL = (
    "      Bu kodu arman@khadangan.com adresine e-postayla gönderin; gelen .lic dosyasını web "
    "arayüzünde Ayarlar → Lisans'tan yükleyin ya da kurulumu -LicenseFile ile yeniden çalıştırın "
    "(docs\\LISANS.md).",
    "      [E-mail this code to arman@khadangan.com; upload the .lic you receive under Settings → "
    "Licence, or run the installer again with -LicenseFile (docs\\LICENSING.md).]",
)


def refusal_lines(version: str, release_date: str, until: str) -> list[str]:
    """The maintenance refusal of contract 9.3, Turkish line then English line."""
    return [
        f"Bu sürüm ({version}, yayın tarihi {release_date}) lisansınızın bakım süresinden "
        f"({until} tarihine kadar) sonra yayımlandı; hiçbir şey değiştirilmedi. Bakımı yenileyip "
        "yeni lisans dosyasını -LicenseFile ile verin ya da yalnızca 5651 Temel ile devam etmek "
        "için -AllowUncoveredUpgrade kullanın.",
        f"[This version ({version}, released {release_date}) was released after your licence's "
        f"maintenance period (until {until}); nothing was changed. Renew maintenance and pass the "
        "new licence file with -LicenseFile, or use -AllowUncoveredUpgrade to continue with 5651 "
        "Temel only.]",
    ]


def license_document(
    *,
    maintenance_until: Any = "2027-10-20",
    valid_until: Any = None,
    license_id: Any = LICENSE_ID,
    drop: tuple[str, ...] = (),
) -> dict[str, Any]:
    """A licence file of contract 2.1 (the signature is not checked by the installer)."""
    payload: dict[str, Any] = {
        "schema": "arcilogger.license/1",
        "product": "ARCILOGGER",
        "license_id": license_id,
        "licensee": "Örnek A.Ş.",
        "issued_at": "2026-10-20T12:00:00Z",
        "server_ids": [SERVER_ID],
        "installation_id": "3f1c2b6e-8a51-4c43-9d0f-5b7e2a61c9d4",
        "modules": ["core_5651", "usage_analytics", "usage_reports"],
        "limits": {"log_sources": 10, "users": 25},
        "maintenance_until": maintenance_until,
        "valid_until": valid_until,
        "request_sha256": "0" * 64,
    }
    for key in drop:
        del payload[key]
    return {
        "payload": payload,
        "signature": {"alg": "Ed25519", "key_id": "arci-2026a", "value": "A" * 86},
    }


def write_license_file(
    path: Path, document: dict[str, Any] | None = None, *, bom: bool = False
) -> Path:
    """As the issuer writes it: json.dumps(..., ensure_ascii=False, indent=2) + newline."""
    text = json.dumps(document or license_document(), ensure_ascii=False, indent=2) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((BOM if bom else b"") + text.encode("utf-8"))
    return path


def status_document(status: str, maintenance_until: str | None, **changes: Any) -> dict[str, Any]:
    """The status file of contract 4.4."""
    licensed = maintenance_until is not None
    document: dict[str, Any] = {
        "schema": LICENSE_STATUS_SCHEMA,
        "written_at": "2026-10-20T10:05:00Z",
        "status": status,
        "license_id": LICENSE_ID if licensed else None,
        "licensee": "Örnek A.Ş." if licensed else None,
        "server_id": SERVER_ID,
        "maintenance_until": maintenance_until,
        "valid_until": None,
        "modules": ["core_5651", "usage_analytics"] if status == "licensed" else ["core_5651"],
        "version": "9.9.9",
        "release_date": "2026-10-01",
    }
    document.update(changes)
    return document


def write_license_status(
    tmp_path: Path, status: str, maintenance_until: str | None, **changes: Any
) -> Path:
    """<data_dir>\\state\\license-status.json of the dry runs' data root."""
    path = tmp_path / "data" / "ARCILOGGER" / "state" / "license-status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(status_document(status, maintenance_until, **changes), ensure_ascii=False)
    path.write_text(text, encoding="utf-8")
    return path


def write_vault_day(tmp_path: Path, day: date) -> Path:
    """A sealed day as the vault holds it: vault\\YYYY\\MM\\DD\\manifest-<day>.json (+ token)."""
    folder = tmp_path / "data" / "ARCILOGGER" / "vault" / f"{day:%Y}" / f"{day:%m}" / f"{day:%d}"
    folder.mkdir(parents=True, exist_ok=True)
    manifest = folder / f"manifest-{day.isoformat()}.json"
    manifest.write_text("{}", encoding="utf-8")
    (folder / f"manifest-{day.isoformat()}.json.zd").write_bytes(b"token")
    return manifest


def site_today() -> date:
    return timeutil.site_day(datetime.now(UTC))


def _items(plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["id"]: item for item in plan["items"]}


def _license_check(plan: dict[str, Any]) -> dict[str, Any]:
    checks: list[dict[str, Any]] = [
        item for item in plan["items"] if item["id"] == "preflight.license"
    ]
    assert len(checks) == 1, plan["items"]
    return checks[0]


def _upgrade_setup(tmp_path: Path) -> Path:
    """An installed 9.9.9 and a bundle 9.9.10 released BUNDLE_RELEASE_DATE."""
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    return bundle


def _assert_nothing_planned(plan: dict[str, Any]) -> None:
    ids = _ids(plan)
    assert _changes(ids) == [], ids
    assert not [i for i in ids if i.startswith(("license.", "services.", "health."))], ids


def test_dry_run_upgrade_refused_after_the_maintenance(engine: str, tmp_path: Path) -> None:
    """A bundle released after the installed licence's maintenance_until stops before any
    change (exit 2), with the published message, Turkish and English, one line each."""
    bundle = _upgrade_setup(tmp_path)
    write_license_status(tmp_path, "licensed", "2026-10-19")
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade")
    assert result.returncode == 2, result.stdout + result.stderr
    assert_dry_run_exit(result, plan)
    check = _license_check(plan)
    assert check["status"] == "fail"
    lines = refusal_lines("9.9.10", BUNDLE_RELEASE_DATE, "2026-10-19")
    assert check["text"].splitlines() == lines
    assert check["text"] in plan["failures"]
    for line in lines:
        assert line in result.stdout
    _assert_nothing_planned(plan)
    assert plan["mode"] == "upgrade"
    assert plan["bundle"]["release_date"] == BUNDLE_RELEASE_DATE
    # version_not_covered (the running version is already uncovered) applies the rule as well.
    write_license_status(tmp_path, "version_not_covered", "2026-09-30")
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade")
    assert result.returncode == 2
    assert _license_check(plan)["text"].splitlines() == refusal_lines(
        "9.9.10", BUNDLE_RELEASE_DATE, "2026-09-30"
    )
    _assert_nothing_planned(plan)


def test_dry_run_allow_uncovered_upgrade_warns_and_continues(engine: str, tmp_path: Path) -> None:
    """-AllowUncoveredUpgrade (C-6): a fix for a 5651 obligation can always be installed; the
    paid modules are then off (the product's state version_not_covered)."""
    bundle = _upgrade_setup(tmp_path)
    write_license_status(tmp_path, "licensed", "2026-10-19")
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", "-AllowUncoveredUpgrade")
    assert_dry_run_exit(result, plan)
    check = _license_check(plan)
    assert check["status"] == "warn"
    lines = check["text"].splitlines()
    assert lines[:2] == refusal_lines("9.9.10", BUNDLE_RELEASE_DATE, "2026-10-19")
    assert UNCOVERED_SUFFIX in lines[2]
    assert check["text"] in plan["warnings"]
    assert check["text"] not in plan["failures"]
    ids = _ids(plan)
    _before(
        ids, "preflight.license", "database.backup", "program.move-previous", "database.upgrade"
    )
    assert "license.install" not in ids


def test_dry_run_covering_license_file_is_installed_after_the_migration(
    engine: str, tmp_path: Path
) -> None:
    """-LicenseFile wins over the status file; its step runs right after the schema migration."""
    bundle = _upgrade_setup(tmp_path)
    write_license_status(tmp_path, "licensed", "2026-10-19")  # the old licence: not covering
    lic = write_license_file(tmp_path / "in" / "yeni lisans.lic")
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", "-LicenseFile", str(lic))
    assert_dry_run_exit(result, plan)
    check = _license_check(plan)
    assert check["status"] == "ok", check
    assert LICENSE_ID in check["text"]
    assert "2027-10-20" in check["text"]
    ids = _ids(plan)
    assert ids.index("license.install") == ids.index("database.upgrade") + 1
    assert _changes(ids)[-2:] == ["database.upgrade", "license.install"]
    step = _items(plan)["license.install"]
    assert step["status"] == "planned"
    assert step["text"] == f"Lisans dosyası [licence file] {LICENSE_ID}"
    assert step["detail"] == {
        "file": str(lic),
        "license_id": LICENSE_ID,
        "maintenance_until": "2027-10-20",
        "valid_until": None,
    }
    # A fresh installation gets it the same way; the same day as the release is covered.
    other = tmp_path / "fresh"
    other.mkdir()
    lic = write_license_file(
        other / "in" / "a.lic", license_document(maintenance_until=BUNDLE_RELEASE_DATE)
    )
    result, plan = dry_run(engine, other, make_fake_bundle(other), "-LicenseFile", str(lic))
    assert_dry_run_exit(result, plan)
    assert _license_check(plan)["status"] == "ok"
    ids = _ids(plan)
    assert ids.index("license.install") == ids.index("database.upgrade") + 1


@pytest.mark.parametrize("switches", ["none", "license-file", "allow-uncovered", "both"])
def test_dry_run_setup_switches_reach_the_script(
    engine: str, tmp_path: Path, switches: str
) -> None:
    """C-6 through ARCILOGGER-Setup.exe: the command line InstallerParameters builds, split as
    powershell.exe splits it, binds -Auto, -LicenseFile (a path with spaces) and
    -AllowUncoveredUpgrade as the script parameters. Without switches the default is unchanged:
    a version the installed licence does not cover is refused (exit 2)."""
    bundle = _upgrade_setup(tmp_path)
    write_license_status(tmp_path, "licensed", "2026-10-19")  # does not cover the bundle
    lic = write_license_file(tmp_path / "Lisans klasörü" / "ARCI-L yeni.lic")
    license_file = str(lic) if switches in ("license-file", "both") else None
    allow = switches in ("allow-uncovered", "both")
    script = str(bundle / "installer" / "Install-Arcilogger.ps1")
    argv = windows_argv(setup_command_line(script, license_file, allow))
    assert argv[:5] == ["-NoProfile", "-ExecutionPolicy", "Bypass", "-File", script]
    passed = argv[5:]
    expected = ["-Auto"]
    if license_file:
        expected += ["-LicenseFile", str(lic)]
    if allow:
        expected.append("-AllowUncoveredUpgrade")
    assert passed == expected
    result, plan = dry_run(engine, tmp_path, bundle, *passed)
    assert_dry_run_exit(result, plan)
    assert plan["mode"] == "upgrade", "-Auto upgrades the installed version"
    check = _license_check(plan)
    ids = _ids(plan)
    if switches == "none":
        assert result.returncode == 2
        assert check["status"] == "fail"
        _assert_nothing_planned(plan)
    elif license_file:
        # The covering licence file wins; it is installed right after the migration.
        assert check["status"] == "ok", check
        assert _items(plan)["license.install"]["detail"]["file"] == str(lic)
        assert ids.index("license.install") == ids.index("database.upgrade") + 1
    else:
        assert check["status"] == "warn"
        assert UNCOVERED_SUFFIX in check["text"]
        assert "database.upgrade" in ids
        assert "license.install" not in ids


def test_dry_run_license_file_not_covering_is_refused(engine: str, tmp_path: Path) -> None:
    """-LicenseFile decides even when the installed licence would cover the bundle."""
    bundle = _upgrade_setup(tmp_path)
    write_license_status(tmp_path, "licensed", "2099-12-31")
    lic = write_license_file(tmp_path / "old.lic", license_document(maintenance_until="2026-10-19"))
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", "-LicenseFile", str(lic))
    assert result.returncode == 2
    check = _license_check(plan)
    assert check["status"] == "fail"
    assert check["text"].splitlines() == refusal_lines("9.9.10", BUNDLE_RELEASE_DATE, "2026-10-19")
    _assert_nothing_planned(plan)


def test_dry_run_without_a_licence_only_informs(engine: str, tmp_path: Path) -> None:
    """No -LicenseFile, no status file: nothing to compare, the run goes on."""
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle)
    assert_dry_run_exit(result, plan)
    check = _license_check(plan)
    assert check == {
        "id": "preflight.license",
        "status": "info",
        "text": NO_LICENCE_TEXT,
        "detail": None,
    }
    assert NO_LICENCE_TEXT in result.stdout
    ids = _ids(plan)
    assert "database.upgrade" in ids
    assert "license.install" not in ids
    # A status file without a licence (trial, trial_expired, ...) is the same.
    for state in ("trial", "trial_expired"):
        write_license_status(tmp_path, state, None)
        _, plan = dry_run(engine, tmp_path, bundle)
        assert _license_check(plan)["status"] == "info"
        assert _license_check(plan)["text"] == NO_LICENCE_TEXT


def test_dry_run_fresh_install_over_a_kept_status_file_applies_the_rule(
    engine: str, tmp_path: Path
) -> None:
    """The data folder (and its status file) survives a removal: a new installation of an
    uncovered version over it is refused like an upgrade."""
    bundle = make_fake_bundle(tmp_path)
    write_license_status(tmp_path, "licensed", "2026-10-01")
    result, plan = dry_run(engine, tmp_path, bundle)
    assert result.returncode == 2
    assert plan["mode"] == "install"
    check = _license_check(plan)
    assert check["status"] == "fail"
    assert check["text"].splitlines() == refusal_lines("9.9.9", BUNDLE_RELEASE_DATE, "2026-10-01")
    _assert_nothing_planned(plan)
    assert not (tmp_path / "program").exists()


@pytest.mark.parametrize(
    ("case", "reason"),
    [
        ("missing", "dosya yok [file not found]"),
        ("not-json", "JSON değil [not JSON]"),
        ("no-maintenance-date", "payload.maintenance_until"),
        ("too-large", "dosya çok büyük"),
    ],
)
def test_dry_run_unreadable_license_file_stops(
    engine: str, tmp_path: Path, case: str, reason: str
) -> None:
    bundle = make_fake_bundle(tmp_path)
    lic = tmp_path / "in" / "customer.lic"
    if case == "not-json":
        lic.parent.mkdir(parents=True)
        lic.write_text("Lisans dosyası değil\n", encoding="utf-8")
    elif case == "no-maintenance-date":
        write_license_file(lic, license_document(drop=("maintenance_until",)))
    elif case == "too-large":
        document = license_document()
        document["payload"]["licensee"] = "Ö" * 40_000
        write_license_file(lic, document)
    result, plan = dry_run(engine, tmp_path, bundle, "-LicenseFile", str(lic))
    assert result.returncode == 2, result.stdout + result.stderr
    assert_dry_run_exit(result, plan)
    check = _license_check(plan)
    assert check["status"] == "fail"
    assert check["text"].startswith("Lisans dosyası okunamadı [licence file unreadable]: ")
    assert reason in check["text"]
    assert check["text"] in result.stdout
    _assert_nothing_planned(plan)


def test_dry_run_bundle_without_release_date_stops(engine: str, tmp_path: Path) -> None:
    """A bundle of a build without RELEASE_DATE: nothing to compare the maintenance with."""
    bundle = make_fake_bundle(tmp_path, release_date=None)
    result, plan = dry_run(engine, tmp_path, bundle)
    assert result.returncode == 2
    check = _license_check(plan)
    assert check["status"] == "fail"
    assert check["text"] == "Paket yayın tarihi yok [bundle has no release_date]"
    assert plan["bundle"]["release_date"] is None
    _assert_nothing_planned(plan)


def test_dry_run_edited_status_file_passes_the_advisory_rule(engine: str, tmp_path: Path) -> None:
    """The status file is the customer's to edit: maintenance_until 2099 gets past the
    preflight. That is accepted (contract 9.3): the product's own state version_not_covered
    switches the paid modules off at run time whatever the installer did (B-T33)."""
    bundle = _upgrade_setup(tmp_path)
    write_license_status(tmp_path, "licensed", "2099-12-31")
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade")
    assert_dry_run_exit(result, plan)
    assert _license_check(plan)["status"] == "ok"
    assert "database.upgrade" in _ids(plan)


@pytest.mark.parametrize("damage", ["not-json", "wrong-schema", "licensed-without-date"])
def test_dry_run_damaged_status_file_counts_as_no_licence(
    engine: str, tmp_path: Path, damage: str
) -> None:
    bundle = _upgrade_setup(tmp_path)
    path = write_license_status(tmp_path, "licensed", "2026-10-19")
    if damage == "not-json":
        path.write_text("{", encoding="utf-8")
    elif damage == "wrong-schema":
        write_license_status(
            tmp_path, "licensed", "2026-10-19", schema="arcilogger.license-status/0"
        )
    else:
        write_license_status(tmp_path, "licensed", None)
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade")
    assert_dry_run_exit(result, plan)
    check = _license_check(plan)
    assert check["status"] == "info"
    assert check["text"].startswith(
        NO_LICENCE_TEXT + " (lisans durum dosyası okunamadı [licence status file unreadable]: "
    )


def _unwrapped(text: str) -> str:
    """Windows PowerShell 5.1 hard-wraps host output at the console width, even inside a word
    (``ARCILOG`` / ``GER``): compare texts without any whitespace."""
    return "".join(text.split())


def test_dry_run_upgrade_without_a_licence_warns_about_old_sealed_days(
    engine: str, tmp_path: Path
) -> None:
    """C-12: an installation without a licence whose vault holds sealed days older than the
    trial starts in trial_expired after the upgrade; the preflight warns (never refuses)."""
    bundle = _upgrade_setup(tmp_path)
    write_vault_day(tmp_path, site_today() - timedelta(days=30))
    write_vault_day(tmp_path, site_today() - timedelta(days=1))
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade")
    assert_dry_run_exit(result, plan)
    check = _license_check(plan)
    assert check["status"] == "warn"
    assert check["text"].splitlines() == [NO_LICENCE_TEXT, *TRIAL_OVER_WARNING]
    assert check["text"] in plan["warnings"]
    for line in TRIAL_OVER_WARNING:
        assert _unwrapped(line) in _unwrapped(result.stdout)
    assert "database.upgrade" in _ids(plan), "a warning, the upgrade goes on"


@pytest.mark.parametrize("vault", ["young", "before-the-product", "with-status-file", "install"])
def test_dry_run_trial_warning_only_when_it_applies(
    engine: str, tmp_path: Path, vault: str
) -> None:
    """No warning for days younger than 14 days, for days before the product's epoch (the
    product ignores them too), when a status file says what the licence state is, or on a
    first installation."""
    bundle = make_fake_bundle(tmp_path) if vault == "install" else _upgrade_setup(tmp_path)
    if vault == "young":
        write_vault_day(tmp_path, site_today() - timedelta(days=14))
    elif vault == "before-the-product":
        write_vault_day(tmp_path, date(2025, 12, 31))
    else:
        write_vault_day(tmp_path, site_today() - timedelta(days=60))
    if vault == "with-status-file":
        write_license_status(tmp_path, "trial_expired", None)
    _, plan = dry_run(engine, tmp_path, bundle, *([] if vault == "install" else ["-Upgrade"]))
    check = _license_check(plan)
    assert check["status"] == "info", check
    assert check["text"] == NO_LICENCE_TEXT


def test_trial_over_warning_gives_only_steps_a_pre_licensing_installation_can_take() -> None:
    """The warning is mostly read when upgrading from a version without licensing: that version
    has no request-code command and no installation id before the migration, so the warning
    sends the operator to the licence page after the upgrade instead of to -LicenseFile. Its
    "7 days" is the worker's own default window: usage.reattribute rebuilds a day without rollups
    once usage_analytics is on again. The installation may have changed usage.auto_rebuild_days
    (even to 0), so the text names the setting and says "default", and sends older days to the
    Usage page's rebuild action by its label in each language."""
    from arcilogger_db.settings import DEFAULT_SETTINGS, INTEGER_RANGES
    from arcilogger_worker.usage import jobs

    turkish, english = TRIAL_OVER_WARNING
    assert "-LicenseFile" not in turkish + english
    assert "Ayarlar → Lisans" in turkish
    assert "Settings → Licence" in english
    assert "5651 işlevleri etkilenmez" in turkish
    assert "5651 functions are not affected" in english
    days = DEFAULT_SETTINGS["usage.auto_rebuild_days"]
    assert f"(varsayılan {days} gün, usage.auto_rebuild_days)" in turkish
    assert f"({days} by default, usage.auto_rebuild_days)" in english
    assert INTEGER_RANGES["usage.auto_rebuild_days"][0] == 0, "configurable, hence 'default'"
    locales = REPO / "web" / "src" / "locales"
    for name, text in (("tr", turkish), ("en", english)):
        usage = json.loads((locales / f"{name}.json").read_text("utf-8"))["usage"]
        action = usage["rebuild"]["action"].rstrip("…")
        assert f"{action} " in text, (name, action)
    assert "Kullanım sayfasındaki Yeniden hesapla" in turkish
    assert "Recompute on the Usage page" in english
    assert 'return "missing" if _has_traffic(session, day)' in inspect.getsource(jobs.stale_reason)
    assert "window = settings.auto_rebuild_days" in inspect.getsource(jobs.reattribute)
    service = (REPO / "worker" / "src" / "arcilogger_worker" / "service.py").read_text("utf-8")
    reattribute = _between(service, 'Job(\n            "usage.reattribute"', "),")
    assert "module=USAGE_MODULE" in reattribute, "it runs again once the module is licensed"
    # The script's single-quoted strings, as the test above reads them from the plan.
    preflight = _function(INSTALL_SCRIPT.read_text(encoding="utf-8-sig"), "Invoke-LicensePreflight")
    for line in TRIAL_OVER_WARNING:
        assert "'" + line.replace("'", "''") + "'" in preflight
    assert "Add-Check 'license' 'warn' $warning" in preflight, "a warning, never a refusal"


def test_dry_run_license_file_without_database_step_is_a_warning(
    engine: str, tmp_path: Path
) -> None:
    """-SkipDatabase: the licence cannot be installed; the command to run later is printed."""
    bundle = make_fake_bundle(tmp_path)
    lic = write_license_file(tmp_path / "in" / "customer.lic")
    result, plan = dry_run(engine, tmp_path, bundle, "-SkipDatabase", "-LicenseFile", str(lic))
    assert_dry_run_exit(result, plan)
    step = _items(plan)["license.install"]
    assert step["status"] == "skipped"
    warning = next(w for w in plan["warnings"] if w.startswith("Lisans dosyası yüklenmedi"))
    assert "(-SkipDatabase)" in warning
    assert f' license install "{lic}" --config "' in warning
    assert _unwrapped(warning) in _unwrapped(result.stdout.replace("WARNING: ", ""))


@pytest.mark.parametrize("mode", ["-Uninstall", "-Rollback"])
@pytest.mark.parametrize("option", ["-LicenseFile", "-AllowUncoveredUpgrade"])
def test_license_options_only_for_install_and_upgrade(
    engine: str, tmp_path: Path, mode: str, option: str
) -> None:
    bundle = make_fake_bundle(tmp_path)
    extra = (
        [option, str(write_license_file(tmp_path / "x.lic"))]
        if option == "-LicenseFile"
        else [option]
    )
    result, plan = dry_run(engine, tmp_path, bundle, mode, *extra)
    assert result.returncode == 2
    assert plan["items"] == []
    assert "[only for an installation or an upgrade]" in result.stdout


def test_dry_run_repair_of_the_installed_bundle_only_warns(engine: str, tmp_path: Path) -> None:
    """Running the installed bundle again (a repair) installs no newer version: an uncovered
    status only warns, so the services and tasks of the running version can always be repaired."""
    bundle = make_fake_bundle(tmp_path)
    fingerprint = sha256_bytes((bundle / "SHA256SUMS").read_bytes())
    write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", fingerprint)
    write_license_status(tmp_path, "version_not_covered", "2026-10-19")
    result, plan = dry_run(engine, tmp_path, bundle)
    assert_dry_run_exit(result, plan)
    assert "program.keep" in _ids(plan)
    check = _license_check(plan)
    assert check["status"] == "warn"
    assert check["text"].splitlines()[:2] == refusal_lines(
        "9.9.9", BUNDLE_RELEASE_DATE, "2026-10-19"
    )
    assert "[the same bundle is installed: repair only]" in check["text"]
    # The same with -Upgrade -Force (the installed bundle once more).
    _, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", "-Force")
    assert plan["mode"] == "upgrade"
    assert _license_check(plan)["status"] == "warn"
    # A different (newer) bundle over it is refused.
    newer = tmp_path / "newer"
    newer.mkdir()
    result, plan = dry_run(engine, tmp_path, make_fake_bundle(newer, version="9.9.10"), "-Upgrade")
    assert result.returncode == 2
    assert _license_check(plan)["status"] == "fail"


# The helper module's licence functions (contract 9.3), checked without Windows.

_LICENSE_PROBE_SCRIPT = r"""
param([string]$ModulePath, [string]$InputPath, [string]$OutputPath, [string]$Python)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
$in = [System.IO.File]::ReadAllText($InputPath) | ConvertFrom-Json
$out = [ordered]@{}

function Invoke-Case([scriptblock]$Action) {
    try { [ordered]@{ ok = $true; value = (& $Action); error = $null } }
    catch { [ordered]@{ ok = $false; value = $null; error = $_.Exception.Message } }
}

$cases = [ordered]@{}
foreach ($property in $in.license_files.PSObject.Properties) {
    $path = $property.Value
    $cases[$property.Name] = Invoke-Case { Read-ArciLicenseFile -Path $path }
}
$out['license_files'] = $cases
$cases = [ordered]@{}
foreach ($property in $in.status_files.PSObject.Properties) {
    $path = $property.Value
    $cases[$property.Name] = Invoke-Case { Read-ArciLicenseStatus -Path $path }
}
$out['status_files'] = $cases
$out['covers'] = @(foreach ($pair in $in.covers) {
        Invoke-Case {
            Test-ArciMaintenanceCovers -ReleaseDate $pair[0] -MaintenanceUntil $pair[1] `
                -Version '1.2.3'
        }
    })
$out['release_dates'] = @(foreach ($text in $in.init_texts) {
        Invoke-Case { Get-ArciProductReleaseDate -InitText $text }
    })
$out['oldest'] = @(foreach ($vault in $in.vaults) { Get-ArciOldestVaultDay -VaultPath $vault })
$culture = [System.Globalization.CultureInfo]::InvariantCulture
$styles = [System.Globalization.DateTimeStyles]'AssumeUniversal, AdjustToUniversal'
$out['site_today'] = @(foreach ($text in $in.utc_times) {
        $utc = [DateTime]::ParseExact($text, 'yyyy-MM-dd HH:mm:ss', $culture, $styles)
        Get-ArciSiteToday -UtcNow $utc
    })
$out['days_between'] = @(foreach ($pair in $in.days_between) {
        Get-ArciDaysBetween -From $pair[0] -To $pair[1]
    })
$wheels = [ordered]@{}
foreach ($property in $in.wheels.PSObject.Properties) {
    # Assigned first, as Build-ArciBundle.ps1 does: the list comes back as one object.
    $problems = Get-ArciWheelTestHelpers -WheelDirectory $property.Value
    $wheels[$property.Name] = $problems
}
$out['wheels'] = $wheels
$clock = [System.Diagnostics.Stopwatch]::StartNew()
$out['timeout'] = Invoke-Case {
    $sleep = @('-I', '-c', 'import time; time.sleep(60)')
    Invoke-ArciProcess -FilePath $Python -ArgumentList $sleep -TimeoutSeconds 1 -AllowFailure
}
$out['timeout_seconds'] = $clock.Elapsed.TotalSeconds
$print = @('-I', '-c', 'print("ARCIREQ1-ABCDE")')
$quick = Invoke-ArciProcess -FilePath $Python -ArgumentList $print -TimeoutSeconds 60
$out['quick'] = $quick.StdOut.Trim()
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 8), $utf8)
"""


def _wheel(folder: Path, name: str, *members: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(folder / name, "w") as wheel:
        for member in members:
            wheel.writestr(member, "")


@pytest.fixture
def license_probe(engine: str, tmp_path: Path) -> dict[str, Any]:
    files = tmp_path / "licenses"
    files.mkdir()
    licence_cases: dict[str, Any] = {
        "valid": license_document(),
        "valid_until": license_document(valid_until="2027-01-31"),
        "no_payload": {"signature": {}},
        "missing_date": license_document(drop=("maintenance_until",)),
        "bad_date": license_document(maintenance_until="2027-02-30"),
        "datetime_date": license_document(maintenance_until="2027-10-20T00:00:00Z"),
        "number_date": license_document(maintenance_until=20271020),
        "missing_valid_until": license_document(drop=("valid_until",)),
        "bad_valid_until": license_document(valid_until="soon"),
        "bad_id": license_document(license_id="ARCI-L-2026-1"),
        "lowercase_id": license_document(license_id=LICENSE_ID.lower()),
        "no_id": license_document(drop=("license_id",)),
    }
    paths = {
        name: str(write_license_file(files / f"{name}.lic", doc))
        for name, doc in licence_cases.items()
    }
    paths["bom"] = str(write_license_file(files / "bom.lic", license_document(), bom=True))
    # Exactly the limit is accepted, one byte more is not (contract 2.1: at most 65,536 bytes).
    base = (json.dumps(license_document(), ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    (files / "limit.lic").write_bytes(base[:-2] + b" " * (65_536 - len(base)) + base[-2:])
    (files / "over.lic").write_bytes(base[:-2] + b" " * (65_537 - len(base)) + base[-2:])
    (files / "not_json.lic").write_text("not a licence", encoding="utf-8")
    (files / "array.lic").write_text("[1, 2]", encoding="utf-8")
    (files / "empty.lic").write_bytes(b"")
    (files / "not_utf8.lic").write_bytes(b'{"payload": "\xff\xfe"}')
    for name in ("limit", "over", "not_json", "array", "empty", "not_utf8"):
        paths[name] = str(files / f"{name}.lic")
    paths["missing"] = str(files / "missing.lic")
    states = tmp_path / "states"
    states.mkdir()
    status_cases = {
        "licensed": status_document("licensed", "2027-10-20"),
        "not_covered": status_document("version_not_covered", "2026-09-30"),
        "trial": status_document("trial", None),
        "wrong_schema": status_document(
            "licensed", "2027-10-20", schema="arcilogger.license-status/2"
        ),
        "licensed_without_date": status_document("licensed", None),
        "bad_date": status_document("licensed", "20.10.2027"),
        "bad_status": status_document("Licensed", "2027-10-20"),
    }
    status_paths = {}
    for name, doc in status_cases.items():
        (states / f"{name}.json").write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        status_paths[name] = str(states / f"{name}.json")
    (states / "bom.json").write_bytes(BOM + json.dumps(status_cases["licensed"]).encode())
    (states / "damaged.json").write_text(
        '{"schema": "arcilogger.license-status/1", ', encoding="utf-8"
    )
    status_paths.update(bom=str(states / "bom.json"), damaged=str(states / "damaged.json"))
    status_paths["missing"] = str(states / "missing.json")
    vault = tmp_path / "vault"
    for relative in (
        "2026/10/01/manifest-2026-10-01.json",
        "2026/09/15/manifest-2026-09-15.json",
        "2025/12/31/manifest-2025-12-31.json",  # before the product's epoch: ignored
        "2026/06/31/manifest-2026-06-31.json",  # no such day
        "2026/07/02/manifest-2026-07-02.json.zd",  # a token without its manifest
        "2026/07/03/notes.json",
        "2026/7/04/manifest-2026-07-04.json",  # not the sealer's folder layout
        "deletions/2026/01/01/manifest-2026-01-01.json",
    ):
        (vault / relative).parent.mkdir(parents=True, exist_ok=True)
        (vault / relative).write_text("{}", encoding="utf-8")
    (tmp_path / "empty-vault").mkdir()
    wheels = tmp_path / "wheels"
    core = "arcilogger_core-0.1.0-py3-none-any.whl"
    db = "arcilogger_db-0.1.0-py3-none-any.whl"
    _wheel(
        wheels / "clean", core, "arcilogger_core/__init__.py", "arcilogger_core/license/state.py"
    )
    _wheel(wheels / "clean", db, "arcilogger_db/__init__.py", "arcilogger_db/licensing.py")
    _wheel(wheels / "core-helper", core, "arcilogger_core/license/testing.py")
    _wheel(wheels / "core-helper", db, "arcilogger_db/__init__.py")
    _wheel(wheels / "db-plugin", core, "arcilogger_core/__init__.py")
    _wheel(wheels / "db-plugin", db, "arcilogger_db/pytest_plugin.py")
    _wheel(wheels / "no-db", core, "arcilogger_core/__init__.py")
    document = {
        "license_files": paths,
        "status_files": status_paths,
        "covers": [
            ["2026-10-20", "2026-10-21"],
            ["2026-10-20", "2026-10-20"],
            ["2026-10-21", "2026-10-20"],
            ["2026-10-20", "2026-13-01"],
            ["20.10.2026", "2027-10-20"],
        ],
        "init_texts": [
            '__version__ = "0.1.0"\nRELEASE_DATE: Final = "2026-10-20"\n',
            '__version__ = "0.1.0"\r\nRELEASE_DATE: Final = "2026-10-21"\r\n',
            '__version__ = "0.1.0"\n',
            'RELEASE_DATE = "2026-10-20"\n',
            '    RELEASE_DATE: Final = "2026-10-20"\n',
            'RELEASE_DATE: Final = "2026-02-30"\n',
        ],
        "vaults": [str(vault), str(tmp_path / "empty-vault"), str(tmp_path / "no-vault")],
        "utc_times": ["2026-10-20 20:59:59", "2026-10-20 21:00:00", "2026-12-31 22:30:00"],
        "days_between": [
            ["2026-10-01", "2026-10-20"],
            ["2026-10-20", "2026-10-01"],
            ["2026-02-28", "2026-03-01"],
        ],
        "wheels": {
            name: str(wheels / name) for name in ("clean", "core-helper", "db-plugin", "no-db")
        },
    }
    input_path = tmp_path / "license-input.json"
    input_path.write_text(json.dumps(document), encoding="utf-8")
    output = tmp_path / "license-output.json"
    script = write_script(tmp_path / "license-probe.ps1", _LICENSE_PROBE_SCRIPT)
    result = run_powershell(
        engine,
        script,
        "-ModulePath",
        str(MODULE),
        "-InputPath",
        str(input_path),
        "-OutputPath",
        str(output),
        "-Python",
        sys.executable,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = read_json(output)
    return report


def test_module_reads_the_license_file_without_python(license_probe: dict[str, Any]) -> None:
    cases = license_probe["license_files"]
    expected = {"LicenseId": LICENSE_ID, "MaintenanceUntil": "2027-10-20", "ValidUntil": None}
    for name in ("valid", "bom", "limit"):
        assert cases[name]["ok"] is True, (name, cases[name])
        value = cases[name]["value"]
        assert {key: value[key] for key in expected} == expected, name
    assert cases["limit"]["value"]["Size"] == 65_536
    assert cases["valid_until"]["value"]["ValidUntil"] == "2027-01-31"
    refused = {
        "missing": "dosya yok [file not found]",
        "over": "dosya çok büyük: 65537 bayt, en çok 65536",
        "not_json": "JSON değil [not JSON]",
        "array": "JSON nesnesi değil [not a JSON object]",
        "empty": "dosya boş [file empty]",
        "not_utf8": "UTF-8 değil [not UTF-8]",
        "no_payload": "payload nesnesi yok [no payload object]",
        "missing_date": "payload.maintenance_until",
        "bad_date": "payload.maintenance_until",
        "datetime_date": "payload.maintenance_until",
        "number_date": "payload.maintenance_until",
        "missing_valid_until": "payload.valid_until yok [missing]",
        "bad_valid_until": "payload.valid_until null ya da",
        "bad_id": "payload.license_id",
        "lowercase_id": "payload.license_id",
        "no_id": "payload.license_id",
    }
    for name, reason in refused.items():
        assert cases[name]["ok"] is False, (name, cases[name])
        assert reason in cases[name]["error"], (name, cases[name]["error"])


def test_module_reads_the_license_status_file(license_probe: dict[str, Any]) -> None:
    cases = license_probe["status_files"]
    for name in ("licensed", "bom"):
        value = cases[name]["value"]
        assert value == {
            "Status": "licensed",
            "MaintenanceUntil": "2027-10-20",
            "LicenseId": LICENSE_ID,
            "Licensee": "Örnek A.Ş.",
            "Version": "9.9.9",
            "ReleaseDate": "2026-10-01",
        }, name
    assert cases["not_covered"]["value"]["Status"] == "version_not_covered"
    assert cases["not_covered"]["value"]["MaintenanceUntil"] == "2026-09-30"
    assert cases["trial"]["value"]["Status"] == "trial"
    assert cases["trial"]["value"]["MaintenanceUntil"] is None
    assert cases["missing"] == {"ok": True, "value": None, "error": None}
    for name in ("damaged", "wrong_schema", "licensed_without_date", "bad_date", "bad_status"):
        assert cases[name]["ok"] is False, (name, cases[name])
    assert "arcilogger.license-status/1" in cases["wrong_schema"]["error"]


def test_module_compares_release_and_maintenance_dates(license_probe: dict[str, Any]) -> None:
    before, same, after, bad_until, bad_release = license_probe["covers"]
    assert before["value"]["Covered"] is True
    assert same["value"]["Covered"] is True, "maintenance_until is inclusive"
    assert after["value"]["Covered"] is False
    assert after["value"]["Text"].splitlines() == refusal_lines("1.2.3", "2026-10-21", "2026-10-20")
    assert "kapsanıyor" in same["value"]["Text"]
    assert bad_until["ok"] is False
    assert bad_release["ok"] is False


def test_module_reads_the_release_date_line(license_probe: dict[str, Any]) -> None:
    ok, crlf, missing, untyped, indented, impossible = license_probe["release_dates"]
    assert ok["value"] == "2026-10-20"
    assert crlf["value"] == "2026-10-21"
    for case in (missing, untyped, indented, impossible):
        assert case["ok"] is False, case
        assert "RELEASE_DATE" in case["error"]


def test_module_finds_the_oldest_sealed_day_by_path_name(license_probe: dict[str, Any]) -> None:
    assert license_probe["oldest"] == ["2026-09-15", None, None]
    assert license_probe["site_today"] == ["2026-10-20", "2026-10-21", "2027-01-01"]
    assert license_probe["days_between"] == [19, -19, 1]


def test_module_finds_test_helpers_in_the_bundle_wheels(license_probe: dict[str, Any]) -> None:
    """D-T9b: Build-ArciBundle.ps1 refuses wheels that would ship the licence test helpers."""
    wheels = license_probe["wheels"]
    assert wheels["clean"] == []
    assert wheels["core-helper"] == [
        "arcilogger_core-0.1.0-py3-none-any.whl: arcilogger_core/license/testing.py"
    ]
    assert wheels["db-plugin"] == [
        "arcilogger_db-0.1.0-py3-none-any.whl: arcilogger_db/pytest_plugin.py"
    ]
    assert wheels["no-db"] == ["wheel yok [missing]: arcilogger_db-*.whl"]


def test_module_process_runner_stops_a_program_that_runs_too_long(
    license_probe: dict[str, Any],
) -> None:
    timeout = license_probe["timeout"]
    assert timeout["ok"] is False, "a time limit throws, also with -AllowFailure"
    assert "[did not finish within 1 s, stopped]" in timeout["error"]
    assert license_probe["timeout_seconds"] < 30
    assert license_probe["quick"] == "ARCIREQ1-ABCDE"


def test_build_writes_the_release_date_and_checks_the_wheels() -> None:
    """Contract 9.2: release_date (RELEASE_DATE, not the build clock) beside version in the
    manifest; the built wheels are checked for the test helpers right after they are built."""
    build = (INSTALLER / "Build-ArciBundle.ps1").read_text(encoding="utf-8-sig")
    module = MODULE.read_text(encoding="utf-8-sig")
    assert "$releaseDate = Get-ProductReleaseDate" in build
    assert "Get-ArciProductReleaseDate -InitText $init" in build
    assert "core\\src\\arcilogger_core\\__init__.py" in _function(build, "Get-ProductReleaseDate")
    assert """'(?m)^RELEASE_DATE: Final = "(?<d>\\d{4}-\\d{2}-\\d{2})"'""" in module
    manifest = build[build.index("$manifest = [pscustomobject]@{") :]
    manifest = manifest[: manifest.index("\n}")]
    assert re.search(r"(?m)^\s+version\s+= \$version$", manifest)
    assert re.search(r"(?m)^\s+release_date\s+= \$releaseDate$", manifest)
    built = build.index("'build', '--all-packages', '--wheel'")
    check = build.index("Get-ArciWheelTestHelpers -WheelDirectory $appWheels")
    assert built < check < build.index("'export', '--frozen'")
    assert "if ($shipped.Count -gt 0) { throw" in build
    # The installer copies it into VERSION.json.
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    record = installer[installer.index("Invoke-Step 'version.write'") :]
    record = record[: record.index("'VERSION.json'")]
    assert re.search(r"release_date\s*=\s*\$script:ReleaseDate", record), record


def test_bundle_never_ships_the_license_issuer() -> None:
    """D-T9: tools\\license-issuer (the owner's signing tool) has no step in the bundle."""
    for path in (INSTALLER / "Build-ArciBundle.ps1", INSTALL_SCRIPT, INSTALLER / "ARCILOGGER.iss"):
        text = path.read_bytes().decode("utf-8-sig").lower()
        assert "license-issuer" not in text, path.name
        assert "licence-issuer" not in text, path.name
    build = (INSTALLER / "Build-ArciBundle.ps1").read_text(encoding="utf-8-sig")
    sources = re.findall(r"Join-Path \$(?:RepoRoot|Windows) '([^']+)'", build)
    assert sources, "the build's sources are found"
    assert not [s for s in sources if s.lower().split("\\")[0] == "tools"], sources


def test_next_steps_show_the_request_code_and_the_licensor() -> None:
    """Contract 9.4: step 6 runs "arcilogger-db license request-code" and names the licensor."""
    steps = _function(INSTALL_SCRIPT.read_text(encoding="utf-8-sig"), "Show-NextSteps")
    assert "@('request-code')" in steps
    assert "@('status', '--json')" in steps
    assert "-TimeoutSeconds 5" in steps
    assert 'license request-code --config "{1}"' in steps
    assert "arman@khadangan.com" in steps
    assert "6. Lisans [licence]" in steps
    invoke = _function(INSTALL_SCRIPT.read_text(encoding="utf-8-sig"), "Invoke-DbLicense")
    assert "(@('license') + $ArgumentList + @('--config', $ConfigPath))" in invoke


def test_license_parameters_are_declared() -> None:
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    block = script[script.index("param(") : script.index("\n)\n")]
    assert re.search(r"(?m)^\s+\[string\]\$LicenseFile,$", block)
    assert re.search(r"(?m)^\s+\[switch\]\$AllowUncoveredUpgrade,$", block)


# --- installer recovery, simulated ---------------------------------------------------------------
#
# The installer is dot-sourced (it then only defines its functions) and its real upgrade, repair,
# rollback, uninstall and recovery functions run against simulated Windows services and
# scheduled tasks, with real folders in a temporary directory. Failures are injected where they
# happen on a real server: a folder Windows refuses to move, a failed program installation, a
# failed schema migration, a failed task registration.

_SIM_SCRIPT = r"""
param([string]$Installer, [string]$Base, [string]$Scenario, [string]$OutputPath)
$ErrorActionPreference = 'Stop'

$program = Join-Path $Base 'program/ARCILOGGER'
$programData = Join-Path $Base 'programdata/ARCILOGGER'
$dataRoot = Join-Path $Base 'data/ARCILOGGER'
$executables = @{
    ArciCollector = 'venv/Scripts/arcilogger-collector.exe'
    ArciIngest    = 'venv/Scripts/arcilogger-ingest.exe'
    ArciApi       = 'venv/Scripts/arcilogger-api.exe'
    ArciWorker    = 'venv/Scripts/arcilogger-worker.exe'
    ArciWeb       = 'caddy/caddy.exe'
    ArciAdmin     = 'venv/Scripts/python.exe'
}
# The versions installed before the run come from before the browser administration (no
# ArciAdmin); the bundle's version (Install-ProgramFiles below) has it.
$oldServices = @('ArciCollector', 'ArciIngest', 'ArciApi', 'ArciWorker', 'ArciWeb')

function Write-Text([string]$Path, [string]$Text) { [System.IO.File]::WriteAllText($Path, $Text) }

function New-FakeProgram {
    # -InstallPath: the program folder its service definitions name (the installer fills in
    # {{INSTALL_DIR}}), also for a version kept aside as .previous.
    param(
        [string]$Folder, [string]$Version, [string]$Fingerprint, [switch]$Partial,
        [string[]]$Services = @($executables.Keys), [string]$InstallPath = $Folder
    )
    foreach ($sub in @('services', 'venv/Scripts', 'tasks', 'caddy', 'installer')) {
        [void](New-Item -ItemType Directory -Path (Join-Path $Folder $sub) -Force)
    }
    $record = @{ product = 'ARCILOGGER'; version = $Version; fingerprint = $Fingerprint }
    $file = if ($Partial) { 'INSTALL-IN-PROGRESS.json' } else { 'VERSION.json' }
    Write-Text (Join-Path $Folder $file) ($record | ConvertTo-Json)
    foreach ($exe in @('arcilogger-core', 'arcilogger-db', 'python')) {
        Write-Text (Join-Path $Folder "venv/Scripts/$exe.exe") 'stub'
    }
    foreach ($name in $Services) {
        Write-Text (Join-Path $Folder $executables[$name]) 'stub'
        Write-Text (Join-Path $Folder "services/$name.exe") 'stub'
        $target = Join-Path $InstallPath $executables[$name]
        $xml = "<service><id>$name</id><executable>$target</executable></service>"
        Write-Text (Join-Path $Folder "services/$name.xml") $xml
    }
    foreach ($task in @('Seal', 'Replica', 'VerifyDaily')) {
        Write-Text (Join-Path $Folder "tasks/$task.xml") '<Task/>'
    }
    Write-Text (Join-Path $Folder 'installer/Install-Arcilogger.ps1') '# stub'
}

function Get-FolderVersion([string]$Folder) {
    foreach ($name in @('VERSION.json', 'INSTALL-IN-PROGRESS.json')) {
        $file = Join-Path $Folder $name
        if (Test-Path -LiteralPath $file) {
            return ([System.IO.File]::ReadAllText($file) | ConvertFrom-Json).version
        }
    }
    $null
}

# --- the installed system before the run ---
$rollback = $Scenario.StartsWith('rollback')
# A rollback leaves a version with ArciAdmin for one from before it.
$currentServices = $(if ($rollback) { @($executables.Keys) } else { $oldServices })
New-FakeProgram -Folder $program -Version '1.0.0' -Fingerprint ('a' * 64) -Services $currentServices
$uninstall = $Scenario.StartsWith('uninstall')
if ($rollback -or $uninstall) {
    $previous = "$program.previous"
    New-FakeProgram -Folder $previous -Version '0.9.0' -Fingerprint ('c' * 64) `
        -Services $oldServices -InstallPath $program
}
if ($uninstall) {
    $failed = "$program.failed-20261012000000"
    New-FakeProgram -Folder $failed -Version '1.1.0' -Fingerprint ('d' * 64) -Partial
    [void](New-Item -ItemType Directory -Path "$program.notes" -Force)
    Write-Text "$program.notes/keep.txt" 'mine'
}
[void](New-Item -ItemType Directory -Path (Join-Path $dataRoot 'vault') -Force)
[void](New-Item -ItemType Directory -Path (Join-Path $programData 'install') -Force)
Write-Text (Join-Path $dataRoot 'vault/sealed.log.gz') 'evidence'
Write-Text (Join-Path $programData 'install/state.json') '{"database_provisioned": true}'
if ($Scenario -eq 'health-port') {
    $toml = "[api]`nhost = `"0.0.0.0`"`nport = 18641`n"
    Write-Text (Join-Path $programData 'arcilogger.toml') $toml
}
if ($Scenario -eq 'uninstall-desktop') {
    # What Set-ArciPower.ps1 recorded on a desktop Windows at the installation.
    Write-Text (Join-Path $programData 'install/power-state.json') '{"schema": 1, "previous": {}}'
}
# The configuration: as the version installed before the run wrote it, or (rollback) as the
# browser changed it after the upgrade (a source switched off), with the copy that upgrade kept.
$configPath = Join-Path $programData 'arcilogger.toml'
$beforeUpgrade = "site_name = `"Kurum`"`n[[sources]]`nname = `"fw`"`naddress = `"172.20.10.1`"`n"
if ($Scenario -in @('success', 'migration', 'program-files')) {
    Write-Text $configPath $beforeUpgrade
}
if ($rollback) {
    $changed = $beforeUpgrade
    if ($Scenario -in @('rollback-incompatible', 'rollback-no-copy')) {
        $changed += "enabled = false`n"
    }
    Write-Text $configPath $changed
    $copy = "$configPath.before-upgrade-20261020-101500"
    if ($Scenario -ne 'rollback-no-copy') { Write-Text $copy $beforeUpgrade }
    $recorded = @{ database_provisioned = $true; config_before_upgrade = $copy } | ConvertTo-Json
    Write-Text (Join-Path $programData 'install/state.json') $recorded
}
$stubScripts = Join-Path $Base 'stubscripts'
[void](New-Item -ItemType Directory -Path $stubScripts -Force)
$stub = 'param([switch]$Remove, [string]$StateFile) ' +
'[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "called.txt"), "remove=$Remove")'
Write-Text (Join-Path $stubScripts 'Set-ArciFirewall.ps1') $stub
$powerStub = 'param([switch]$Remove, [string]$StateFile) ' +
'[System.IO.File]::WriteAllText((Join-Path $PSScriptRoot "power.txt"), ' +
'"remove=$Remove state=$StateFile")'
Write-Text (Join-Path $stubScripts 'Set-ArciPower.ps1') $powerStub

$mode = @{}
switch ($Scenario) {
    { $rollback } { $mode['Rollback'] = $true }
    { $uninstall } { $mode['Uninstall'] = $true }
    'repair' { }
    default { $mode['Upgrade'] = $true }
}
$bundle = Join-Path $Base 'bundle'
$taskWait = if ($Scenario -in @('tasks-busy', 'uninstall-busy-stuck')) { 0 } else { 1 }
. $Installer @mode -InstallDir $program -ProgramDataDir $programData -DataRoot $dataRoot `
    -BundleRoot $bundle -SkipFirewall -SkipTimeSetup -TaskWaitMinutes $taskWait `
    -HealthWaitSeconds 3 -ServerAddress '172.20.20.15'
$ScriptsDir = $stubScripts
# What the preflight found on a desktop Windows (Windows 11, ProductType 1).
if ($Scenario -eq 'desktop-upgrade') { $script:IsClientOs = $true }

$sim = @{
    services = @{}; tasks = @{}; exit = $null; message = $null; error = $null; failed_once = $false
    api_calls = 0; https_calls = 0; api_uri = $null; busy_polls = 0
    calls = (New-Object System.Collections.Generic.List[string])
    registered_from = (New-Object System.Collections.Generic.List[string])
    processes = (New-Object System.Collections.Generic.List[object])
    transcript = $null; redirected = $null
}
foreach ($name in $currentServices) {
    $sim.services[$name] = @{
        status = 'Running'; start = 'auto'; account = "NT SERVICE\$name"; version = '1.0.0'
    }
}
$sim.tasks['Seal'] = 'Ready'
$sim.tasks['Replica'] = 'Disabled'   # switched off by the operator: stays off after a recovery
$sim.tasks['VerifyDaily'] = 'Ready'
# tasks-busy, uninstall-busy-stuck: never finishes in time; uninstall-busy: after a few polls.
if ($Scenario -in @('tasks-busy', 'uninstall-busy-stuck', 'uninstall-busy')) {
    $sim.tasks['Seal'] = 'Running'
}
$fingerprint = if ($Scenario -eq 'repair') { 'a' * 64 } else { 'b' * 64 }
$version = if ($Scenario -eq 'repair') { '1.0.0' } else { '2.0.0' }
$script:BundleInfo = [pscustomobject]@{ Fingerprint = $fingerprint; Files = 1 }
$script:Manifest = [pscustomobject]@{
    version = $version; git_commit = $null; built_at = 'x'; release_date = '2026-10-20'
}
$script:ReleaseDate = '2026-10-20'

# --- simulated Windows ---
function Get-Service {
    param([string]$Name, $ErrorAction)
    if (-not $sim.services.ContainsKey($Name)) { return }
    $service = [pscustomobject]@{ Name = $Name; Status = $sim.services[$Name].status }
    $wait = { param($Status, $Timeout) }
    $service | Add-Member -MemberType ScriptMethod -Name WaitForStatus -Value $wait
    $service
}
function Stop-Service {
    param([string]$Name, [switch]$Force)
    $sim.services[$Name].status = 'Stopped'
    $sim.calls.Add("stop $Name")
}
function Start-Service {
    param([string]$Name)
    $sim.services[$Name].status = 'Running'
    $sim.calls.Add("start $Name")
}
function Get-ScheduledTask {
    param([string]$TaskPath, [string]$TaskName, $ErrorAction)
    if ($Scenario -eq 'uninstall-busy' -and $sim.tasks['Seal'] -eq 'Running') {
        # The sealing run ends after a few polls; it had been disabled meanwhile.
        $sim.busy_polls++
        if ($sim.busy_polls -ge 4) {
            $sim.tasks['Seal'] = 'Disabled'
            $sim.calls.Add('seal instance finished')
        }
    }
    foreach ($key in @($sim.tasks.Keys)) {
        if ($TaskName -and $key -ne $TaskName) { continue }
        [pscustomobject]@{ TaskName = $key; State = $sim.tasks[$key] }
    }
}
function Disable-ScheduledTask {
    param([Parameter(ValueFromPipeline = $true)]$InputObject)
    process {
        # A running instance goes on running: disabling only stops new starts.
        $name = $InputObject.TaskName
        if ($sim.tasks[$name] -ne 'Running') { $sim.tasks[$name] = 'Disabled' }
    }
}
function Enable-ScheduledTask {
    param([Parameter(ValueFromPipeline = $true)]$InputObject)
    process { $sim.tasks[$InputObject.TaskName] = 'Ready' }
}
function Unregister-ScheduledTask {
    param([Parameter(ValueFromPipeline = $true)]$InputObject, [switch]$Confirm)
    process {
        # Unregistering does not stop a running instance either.
        $name = $InputObject.TaskName
        $sim.calls.Add(('unregister {0} state={1}' -f $name, $sim.tasks[$name]))
        $sim.tasks.Remove($InputObject.TaskName)
    }
}
function Remove-Item {
    param([string]$LiteralPath, [switch]$Recurse, [switch]$Force, $ErrorAction)
    # A running evidence job holds python.exe and python312.dll of the program folder open.
    $running = @($sim.tasks.Keys | Where-Object { $sim.tasks[$_] -eq 'Running' })
    $programs = Split-Path -Parent $program
    if ($running.Count -gt 0 -and $LiteralPath.StartsWith($programs)) {
        throw ('simulated: python312.dll is in use by {0}' -f ($running -join ', '))
    }
    $sim.calls.Add(('remove {0}' -f (Split-Path -Leaf $LiteralPath)))
    $parameters = @{ LiteralPath = $LiteralPath; Recurse = $Recurse; Force = $Force }
    if ($PSBoundParameters.ContainsKey('ErrorAction')) { $parameters['ErrorAction'] = $ErrorAction }
    Microsoft.PowerShell.Management\Remove-Item @parameters
}
function Invoke-ArciExe {
    param([string]$FilePath, [string[]]$ArgumentList = @())
    $exe = [System.IO.Path]::GetFileName($FilePath)
    $sim.calls.Add(('exe {0} {1}' -f $exe, ($ArgumentList -join ' ')))
    if ($FilePath -eq 'sc.exe') {
        $service = $ArgumentList[1]
        if ($ArgumentList[0] -eq 'delete') { $sim.services.Remove($service) }
        elseif ($ArgumentList[2] -eq 'obj=') { $sim.services[$service].account = $ArgumentList[3] }
        elseif ($ArgumentList[2] -eq 'start=') { $sim.services[$service].start = $ArgumentList[3] }
        return
    }
    if ($exe -eq 'arcilogger-db.exe') {
        if ($Scenario -eq 'migration') { throw 'simulated: schema migration failed' }
        return
    }
    if ($ArgumentList -contains 'install') {
        $service = [System.IO.Path]::GetFileNameWithoutExtension($FilePath)
        $folder = Split-Path -Parent (Split-Path -Parent $FilePath)
        $sim.services[$service] = @{
            status = 'Stopped'; start = 'delayed-auto'; account = 'LocalSystem'
            version = (Get-FolderVersion $folder)
        }
    }
}
function Rename-Item {
    param([string]$LiteralPath, [string]$NewName)
    $toPrevious = $NewName.EndsWith('.previous')
    if ($Scenario -eq 'move' -and $LiteralPath -eq $InstallDir -and $toPrevious) {
        throw 'simulated: the folder is in use'
    }
    if ($Scenario -eq 'rollback-swap' -and $LiteralPath -eq $PreviousDir) {
        throw 'simulated: the folder is in use'
    }
    Microsoft.PowerShell.Management\Rename-Item -LiteralPath $LiteralPath -NewName $NewName
}
function Register-ArciTaskSet {
    param([string]$Root, [switch]$Replace)
    if ($Scenario -eq 'repair' -and -not $sim.failed_once) {
        $sim.failed_once = $true
        throw 'simulated: task registration failed'
    }
    if ($Replace) { $sim.tasks.Clear() }
    foreach ($xml in Get-ChildItem -LiteralPath (Join-Path $Root 'tasks') -Filter '*.xml') {
        $sim.tasks[$xml.BaseName] = 'Ready'
    }
    $sim.registered_from.Add([string](Get-FolderVersion $Root))
}
function Install-ProgramFiles {
    $partial = $Scenario -eq 'program-files'
    New-FakeProgram -Folder $InstallDir -Version $script:Manifest.version `
        -Fingerprint $script:BundleInfo.Fingerprint -Partial:$partial
    if ($partial) { throw 'simulated: caddy adapt failed' }
}
function Stop-Installer {
    param([int]$Code, [string]$Message)
    $sim.exit = $Code
    $sim.message = $Message
    throw 'SIMULATED-STOP'
}
function Invoke-ConfigSteps { $sim.calls.Add('config') }
# What the restored program's Python makes of arcilogger.toml: a version from before the browser
# administration refuses enabled = false of a log source (extra="forbid").
function Test-ArciProgramConfig {
    param([string]$Python, [string]$ConfigPath)
    $folder = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $Python))
    $sim.calls.Add(('config-check {0}' -f (Get-FolderVersion $folder)))
    if ([System.IO.File]::ReadAllText($ConfigPath).Contains('enabled = false')) {
        return '1 validation error for Config sources.0.enabled Extra inputs are not permitted'
    }
    $null
}
function Unregister-ArciEventSource { $sim.calls.Add('event source removed'); 'removed' }
function Invoke-AclSteps { $sim.calls.Add('acl') }
# ArciAdmin's entries on arcilogger.toml and secrets.json once more (after a secret was stored).
function Restore-ConfigFileAccess {
    $sim.calls.Add('config file rights')
    $entry = [ordered]@{ exe = 'Set-ArciAclEntry'; arguments = 'arcilogger.toml secrets.json' }
    $sim.processes.Add($entry)
}
function Invoke-DatabaseProvision { $sim.calls.Add('provision') }
if (-not $Scenario.StartsWith('health')) {
    function Invoke-HealthCheck { $sim.calls.Add('health') }
}
# Health check: the API answers after a while, Caddy's leaf certificate a little later.
function Start-Sleep {
    param([int]$Seconds, [int]$Milliseconds)
    [System.Threading.Thread]::Sleep(20)
}
function Invoke-WebRequest {
    param([string]$Uri, [switch]$UseBasicParsing, [int]$TimeoutSec)
    $sim.api_calls++
    $sim.api_uri = $Uri
    if ($Scenario -eq 'health-api-down' -or $sim.api_calls -lt 3) { throw 'simulated: refused' }
    [pscustomobject]@{ Content = '{"status": "ok", "version": "2.0.0"}' }
}
function Test-ArciHttpsHealth {
    param([string]$HostName, [string]$RootCertificatePath)
    $sim.https_calls++
    if ($Scenario -eq 'health-api-down') { return [pscustomobject]@{ Status = 502; Body = '' } }
    if ($sim.https_calls -lt 2) { throw 'simulated: no leaf certificate yet' }
    [pscustomobject]@{ Status = 200; Body = '{"status": "ok"}' }
}
function Test-JavaForZamane { }
# arcilogger.toml as the venv's tomllib would read it (the stub python.exe cannot run).
function Read-ArciTomlFile {
    param([string]$Python, [string]$ConfigPath)
    [pscustomobject]@{ api = [pscustomobject]@{ host = '0.0.0.0'; port = 18641 } }
}
function Get-DatabaseSettings {
    [pscustomobject]@{ host = '127.0.0.1'; port = 5432; name = 'arcilogger'; user = 'arcilogger' }
}
function Get-PgCredential {
    $secret = ConvertTo-SecureString 'x' -AsPlainText -Force
    New-Object System.Management.Automation.PSCredential -ArgumentList 'postgres', $secret
}
function Invoke-PgTool {
    param([string]$Tool, [string[]]$ArgumentList, [string]$StdinText, [string[]]$Redact = @())
    $sim.calls.Add("pg $Tool")
    if ($Tool -eq 'pg_dump.exe') {
        Write-Text $ArgumentList[[array]::IndexOf($ArgumentList, '-f') + 1] 'PGDMP simulated'
    }
}
if ($Scenario.StartsWith('first-install')) {
    # The end of a first installation (after the transcript): a person at the console or not.
    function Test-ArciInteractiveConsole { $Scenario -ne 'first-install-quiet' }
    function Read-Host {
        param([Parameter(Position = 0)][string]$Prompt, [switch]$AsSecureString)
        $sim.calls.Add(('read-host secure={0}: {1}' -f [bool]$AsSecureString, $Prompt))
        if ($AsSecureString) {
            return (ConvertTo-SecureString 'kamu-sim-pw-1' -AsPlainText -Force)  # gitleaks:allow
        }
        if ($Prompt -match 'ister misiniz') { return '' }   # Enter: the default answer, yes
        '7654321'
    }
    function Invoke-ArciProcess {
        param([string]$FilePath, [string[]]$ArgumentList, [string]$StdinText,
            [hashtable]$Environment, [string[]]$Redact, [switch]$AllowFailure)
        $record = [ordered]@{
            exe = [System.IO.Path]::GetFileName($FilePath); arguments = ($ArgumentList -join ' ')
            stdin = $StdinText; redact = @($Redact)
        }
        $sim.processes.Add($record)
        $out = '{0} kaydedildi.' -f $ArgumentList[-1]
        $code = 0
        if ($ArgumentList -contains 'setup-token' -and $Scenario -eq 'first-install-admin-exists') {
            # A reinstallation over a kept database: arcilogger-api refuses with exit code 4.
            $out = "Bir yönetici hesabı zaten var; kurulum anahtarı oluşturulmadı.`n" +
            "Kurtarma için: arcilogger-api create-admin`n"
            $code = 4
        }
        elseif ($ArgumentList -contains 'setup-token') {
            $out = "Kurulum anahtarı:`n`n    SIMULATED-SETUP-TOKEN`n`nGeçerlilik: 24 saat.`n"
        }
        elseif ($ArgumentList -contains 'list') { $out = "database.password  (kayıtlı)`n" }
        [pscustomobject]@{ ExitCode = $code; StdOut = $out; StdErr = '' }
    }
}

$licenseScenario = $Scenario.StartsWith('next-steps-licen') -or
    $Scenario.StartsWith('license-install') -or $Scenario -eq 'upgrade-license-refused'
if ($licenseScenario) {
    # A licence file given with -LicenseFile (as the preflight read it), and the answers of
    # "arcilogger-db license ..." (contract 6.8: exit 0 done, 1 refused, 3 database unreachable).
    $LicenseFile = Join-Path $Base 'customer.lic'
    $script:LicenseInfo = [pscustomobject]@{
        LicenseId = 'ARCI-L-20261020-7Q3KX9MD'; MaintenanceUntil = '2027-10-20'; ValidUntil = $null
        Size = 1
    }
    function Invoke-ArciProcess {
        param([string]$FilePath, [string[]]$ArgumentList, [string]$StdinText,
            [hashtable]$Environment, [string[]]$Redact, [switch]$AllowFailure,
            [int]$TimeoutSeconds)
        $exe = [System.IO.Path]::GetFileName($FilePath)
        $sim.processes.Add([ordered]@{
                exe = $exe; arguments = ($ArgumentList -join ' '); timeout = $TimeoutSeconds
                allow_failure = [bool]$AllowFailure
            })
        $sim.calls.Add(('process {0} {1}' -f $exe, ($ArgumentList -join ' ')))
        if ($Scenario.EndsWith('-slow')) {
            $text = '{0}: {1} saniyede bitmedi, durduruldu [did not finish within {1} s, stopped]'
            throw ($text -f $exe, $TimeoutSeconds)
        }
        if ($Scenario.EndsWith('-crash')) {
            throw 'simulated: arcilogger-db.exe could not be started'
        }
        if ($Scenario.EndsWith('-db-down')) {
            $unreachable = "Veritabanına ulaşılamadı.`n[The database cannot be reached.]`n"
            return [pscustomobject]@{ ExitCode = 3; StdOut = ''; StdErr = $unreachable }
        }
        $command = $ArgumentList[1]
        $state = @{
            'next-steps-license-trial' = 'trial'; 'next-steps-license-expired' = 'trial_expired'
            'next-steps-licensed' = 'licensed'; 'next-steps-license-wrong-server' = 'wrong_server'
        }[$Scenario]
        if ($command -eq 'status') {
            $licensee = $(if ($state -eq 'licensed') { '"Örnek A.Ş."' } else { 'null' })
            $json = '{{"status": "{0}", "problem": null, "licensee": {1}}}' -f $state, $licensee
            return [pscustomobject]@{ ExitCode = 0; StdOut = $json; StdErr = '' }
        }
        if ($command -eq 'request-code') {
            $code = "ARCIREQ1-ABCDE-FGHJK-MNPQR-ST`r`n"
            return [pscustomobject]@{ ExitCode = 0; StdOut = $code; StdErr = '' }
        }
        if ($command -eq 'install' -and $Scenario -eq 'license-install-ok') {
            $json = '{"ok": true, "status": {"status": "licensed", "licensee": "Örnek A.Ş."}}'
            return [pscustomobject]@{ ExitCode = 0; StdOut = $json; StdErr = '' }
        }
        $refusal = [ordered]@{
            ok = $false; code = 'license_other_server'
            message_tr = ('Lisans bu sunucu için düzenlenmemiş (bu sunucu: XW68-7K1Z-5FGV-AGT8). ' +
                'Yeni lisans için istek kodunu gönderin.')
            message_en = ('The licence was not issued for this server (this server: ' +
                'XW68-7K1Z-5FGV-AGT8). Send the request code for a new licence.')
            details = [ordered]@{
                server_id = 'XW68-7K1Z-5FGV-AGT8'; allowed = @('AAAA-BBBB-CCCC-DDDD')
            }
        }
        $json = $refusal | ConvertTo-Json -Depth 4
        [pscustomobject]@{ ExitCode = 1; StdOut = $json; StdErr = '' }
    }
}

if ($Scenario.StartsWith('health')) {
    # Caddy's root certificate, as Caddy writes it on its first start.
    [void](New-Item -ItemType Directory -Path (Split-Path -Parent $CaddyRootSource) -Force)
    $rsa = [System.Security.Cryptography.RSA]::Create(2048)
    $request = New-Object System.Security.Cryptography.X509Certificates.CertificateRequest `
        -ArgumentList 'CN=ARCILOGGER test root', $rsa, `
    ([System.Security.Cryptography.HashAlgorithmName]::SHA256), `
    ([System.Security.Cryptography.RSASignaturePadding]::Pkcs1)
    $now = [DateTimeOffset]::UtcNow
    $root = $request.CreateSelfSigned($now.AddDays(-1), $now.AddDays(1))
    $der = $root.Export([System.Security.Cryptography.X509Certificates.X509ContentType]::Cert)
    [System.IO.File]::WriteAllBytes($CaddyRootSource, $der)
}
try {
    switch ($Scenario) {
        'health-slow' { Invoke-HealthCheck }
        'health-api-down' { Invoke-HealthCheck }
        'health-port' { Invoke-HealthCheck }
        'next-steps' { Show-NextSteps }
        # A configuration without [[sources]] (Invoke-SourcesPreflight counted none); the harness
        # runs with -SkipFirewall, the last scenario as if the firewall step had run.
        'next-steps-no-sources' { $script:SourceCount = 0; Show-NextSteps }
        'next-steps-no-sources-firewall' {
            $script:SourceCount = 0
            $SkipFirewall = [switch]$false
            Show-NextSteps
        }
        'next-steps-kept-config' {
            # A new installation over the arcilogger.toml a removal kept: no Python yet, the
            # example sources still commented out. The preflight counts them, the next steps say.
            Microsoft.PowerShell.Management\Remove-Item -LiteralPath $VenvPython
            $kept = "site_name = `"Kurum`"`n#[[sources]]`n#address = `"172.31.255.1`"`n"
            Write-Text $ConfigPath $kept
            Invoke-SourcesPreflight
            Show-NextSteps
        }
        { $_.StartsWith('next-steps-licen') } { Show-NextSteps }
        { $_.StartsWith('license-install') } { Install-LicenseFile }
        'first-install-console' { Complete-FirstInstall }
        'first-install-quiet' { Complete-FirstInstall }
        'first-install-admin-exists' { Complete-FirstInstall }
        'first-install-captured' {
            # What the operator or a Group Policy may record around the installer: a transcript
            # of the whole session, and the PowerShell streams redirected into a file.
            $outer = Join-Path $Base 'outer-transcript.txt'
            Start-Transcript -LiteralPath $outer | Out-Null
            try { Complete-FirstInstall }
            finally { Stop-Transcript | Out-Null }
            $sim.transcript = [System.IO.File]::ReadAllText($outer)
            $sim.redirected = (Complete-FirstInstall *>&1 | Out-String)
        }
        'repair' { Invoke-Install }
        { $rollback } { Invoke-RollbackMode }
        { $uninstall } { Invoke-UninstallMode }
        default { Invoke-Upgrade }
    }
}
catch {
    if ([string]$_.Exception.Message -ne 'SIMULATED-STOP') {
        $sim.error = '{0} @ {1}' -f $_.Exception.Message, $_.ScriptStackTrace
    }
}

$siblings = @(Get-ChildItem -LiteralPath (Split-Path -Parent $program) -Directory | ForEach-Object {
        [ordered]@{ name = $_.Name; version = (Get-FolderVersion $_.FullName) }
    })
$services = [ordered]@{}
foreach ($key in ($sim.services.Keys | Sort-Object)) { $services[$key] = $sim.services[$key] }
$tasks = [ordered]@{}
foreach ($key in ($sim.tasks.Keys | Sort-Object)) { $tasks[$key] = $sim.tasks[$key] }
$statePath = Join-Path $programData 'install/state.json'
$state = $null
if (Test-Path -LiteralPath $statePath) {
    $state = [System.IO.File]::ReadAllText($statePath) | ConvertFrom-Json
}
$called = Join-Path $stubScripts 'called.txt'
# An explicit $null: Windows PowerShell 5.1 turns an empty $(...) into {} in ConvertTo-Json.
$firewall = $null
if (Test-Path -LiteralPath $called) { $firewall = [System.IO.File]::ReadAllText($called) }
$powerCalled = Join-Path $stubScripts 'power.txt'
$power = $null
if (Test-Path -LiteralPath $powerCalled) { $power = [System.IO.File]::ReadAllText($powerCalled) }
$report = [ordered]@{
    exit = $sim.exit; message = $sim.message; error = $sim.error
    services = $services; tasks = $tasks; folders = $siblings; state = $state
    calls = $sim.calls.ToArray(); registered_from = $sim.registered_from.ToArray()
    processes = $sim.processes.ToArray()
    firewall = $firewall; power = $power
    transcript = $sim.transcript; redirected = $sim.redirected
    data_kept = (Test-Path -LiteralPath (Join-Path $dataRoot 'vault/sealed.log.gz'))
    config = $(if (Test-Path $configPath) { [System.IO.File]::ReadAllText($configPath) })
    config_files = @(Get-ChildItem -LiteralPath $programData -File | ForEach-Object { $_.Name })
    config_kept = (Test-Path -LiteralPath $statePath)
    module_path = $env:PSModulePath
    health = [ordered]@{
        exit_code = $script:ExitCode; warnings = $script:Warnings.ToArray()
        api_calls = $sim.api_calls; https_calls = $sim.https_calls; api_uri = $sim.api_uri
        root_copied = (Test-Path -LiteralPath $CaddyRootCopy)
    }
}
Write-Text $OutputPath ($report | ConvertTo-Json -Depth 6)
"""


def simulate(
    engine: str, tmp_path: Path, scenario: str, env_extra: dict[str, str] | None = None
) -> dict[str, Any]:
    output = tmp_path / "simulation.json"
    script = write_script(tmp_path / "simulate.ps1", _SIM_SCRIPT)
    base = tmp_path / "sim"
    base.mkdir()
    result = run_powershell(
        engine,
        script,
        "-Installer",
        str(INSTALL_SCRIPT),
        "-Base",
        str(base),
        "-Scenario",
        scenario,
        "-OutputPath",
        str(output),
        env_extra=env_extra,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = read_json(output)
    assert report["error"] is None, report["error"]
    report["stdout"] = result.stdout
    return report


def _folders(report: dict[str, Any]) -> dict[str, str | None]:
    return {item["name"]: item["version"] for item in report["folders"]}


def _running(report: dict[str, Any]) -> set[str]:
    return {name for name, s in report["services"].items() if s["status"] == "Running"}


#: The services of the versions the harness installed before the run (before the browser
#: administration), and of the bundle's version, which adds the administration agent.
ALL_SERVICES = {"ArciCollector", "ArciIngest", "ArciApi", "ArciWorker", "ArciWeb"}
NEW_SERVICES = ALL_SERVICES | {"ArciAdmin"}


def test_upgrade_succeeds_and_keeps_the_previous_version(engine: str, tmp_path: Path) -> None:
    report = simulate(engine, tmp_path, "success")
    assert report["exit"] is None, report["message"]
    assert _folders(report) == {"ARCILOGGER": "2.0.0", "ARCILOGGER.previous": "1.0.0"}
    assert _running(report) == NEW_SERVICES
    assert {s["version"] for s in report["services"].values()} == {"2.0.0"}
    assert {s["account"] for s in report["services"].values()} == {
        f"NT SERVICE\\{name}" for name in NEW_SERVICES
    }
    assert report["registered_from"] == ["2.0.0"]
    assert report["tasks"] == {"Replica": "Disabled", "Seal": "Ready", "VerifyDaily": "Ready"}
    calls = report["calls"]
    assert calls.index("pg pg_dump.exe") < calls.index("stop ArciCollector"), "backup first"
    migration = next(
        i for i, c in enumerate(calls) if c.startswith("exe arcilogger-db.exe upgrade --config ")
    )
    assert calls[migration].endswith("arcilogger.toml")
    assert migration > max(i for i, c in enumerate(calls) if c.startswith("exe ArciWeb.exe"))
    # The collector and Caddy need no database: they run again before the migration (UDP syslog
    # is lost only while the program is replaced); ingest, API and worker start after it.
    assert calls.index("start ArciCollector") < migration, "collector back before the migration"
    assert calls.index("start ArciWeb") < migration
    for name in ("ArciIngest", "ArciApi", "ArciWorker"):
        assert migration < calls.index(f"start {name}"), f"{name} only on the migrated schema"
    assert calls.count("start ArciCollector") == 1, "a running service is not started again"
    # "secret set database.password" replaces secrets.json after the ACL step: ArciAdmin's
    # entry is put back right after it (contract 2.2).
    assert calls.index("acl") < calls.index("provision") < calls.index("config file rights")
    # ArciAdmin writes the configuration the others load: it starts after all of them.
    starts = [c for c in calls if c.startswith("start ")]
    assert starts[-1] == "start ArciAdmin", starts
    dump = report["state"]["last_dump"]
    assert Path(dump).is_file()
    assert Path(dump).parent == tmp_path / "sim" / "data" / "ARCILOGGER" / "backups" / "db"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell 5.1 on Windows only")
def test_upgrade_started_with_powershell_7_modules_in_front(tmp_path: Path) -> None:
    """The installer itself takes PowerShell 7's module folders out of the path it inherited.

    On the CI runner this upgrade stopped at the database backup (exit 2) because
    ConvertTo-SecureString could not be loaded. The harness does not clean the path here: only
    Initialize-ArciModulePath in Install-Arcilogger.ps1 (run when the harness dot-sources it)
    lets the backup and the rest of the upgrade run.
    """
    windows_powershell, pwsh_modules, polluted = _powershell_7_module_path()
    report = simulate(windows_powershell, tmp_path, "success", env_extra={"PSModulePath": polluted})
    after = [entry.rstrip("\\").lower() for entry in report["module_path"].split(os.pathsep)]
    assert str(pwsh_modules).lower() not in after, report["module_path"]
    assert report["exit"] is None, report["message"]
    assert _folders(report) == {"ARCILOGGER": "2.0.0", "ARCILOGGER.previous": "1.0.0"}
    assert _running(report) == NEW_SERVICES


def test_upgrade_failing_in_the_program_files_restores_the_previous_version(
    engine: str, tmp_path: Path
) -> None:
    report = simulate(engine, tmp_path, "program-files")
    assert report["exit"] == 3
    assert "[previous state restored]" in report["message"]
    folders = _folders(report)
    assert folders.pop("ARCILOGGER") == "1.0.0", "the previous version is back in place"
    assert "ARCILOGGER.previous" not in folders
    (failed_name, failed_version), *rest = folders.items()
    assert not rest
    assert failed_name.startswith("ARCILOGGER.failed-")
    assert failed_version == "2.0.0", "the half-installed new version is kept aside"
    assert _running(report) == ALL_SERVICES
    assert {s["version"] for s in report["services"].values()} == {"1.0.0"}
    assert all(s["start"] != "demand" for s in report["services"].values())
    # The paused evidence jobs run again; a job the operator had switched off stays off.
    assert report["tasks"] == {"Replica": "Disabled", "Seal": "Ready", "VerifyDaily": "Ready"}
    assert report["state"]["last_failure"]["operation"] == "upgrade"
    assert report["state"]["last_failure"]["database_uncertain"] is False
    assert report["state"]["pending_database_restore"] is None
    assert report["data_kept"] is True


def test_upgrade_failing_in_the_migration_runs_only_the_evidence_path(
    engine: str, tmp_path: Path
) -> None:
    """The old program never runs against a half-migrated schema; no syslog is lost."""
    report = simulate(engine, tmp_path, "migration")
    assert report["exit"] == 3
    assert "[only collector and web running]" in report["message"]
    folders = _folders(report)
    assert folders.pop("ARCILOGGER") == "1.0.0"
    assert list(folders.values()) == ["2.0.0"]
    assert next(iter(folders)).startswith("ARCILOGGER.failed-")
    services = report["services"]
    assert _running(report) == {"ArciCollector", "ArciWeb"}
    for name in ("ArciIngest", "ArciApi", "ArciWorker"):
        assert services[name]["status"] == "Stopped", name
        assert services[name]["start"] == "demand", "a reboot must not start it either"
    assert {s["version"] for s in services.values()} == {"1.0.0"}
    # The new version's ArciAdmin was registered before the migration; the previous version has
    # no wrapper for it, so it stays removed (Install-ArciServices skips it with a warning).
    assert "ArciAdmin" not in services
    assert "exe sc.exe delete ArciAdmin" in report["calls"]
    assert "ArciAdmin: hizmet sarmalayıcısı bu sürümde yok" in report["stdout"]
    assert "config-check 1.0.0" in report["calls"], "the restored version loads the configuration"

    # The new version had registered its tasks: those of the restored version replace them,
    # and a job the operator had switched off stays off.
    assert report["registered_from"] == ["2.0.0", "1.0.0"]
    assert report["tasks"] == {"Replica": "Disabled", "Seal": "Ready", "VerifyDaily": "Ready"}
    dump = report["state"]["pending_database_restore"]
    assert dump == report["state"]["last_dump"]
    assert Path(dump).is_file()
    assert report["state"]["last_failure"]["database_uncertain"] is True
    assert "-Rollback -RestoreDatabase -DatabaseDump" in report["stdout"]


def test_upgrade_failing_to_move_the_program_folder_changes_nothing(
    engine: str, tmp_path: Path
) -> None:
    report = simulate(engine, tmp_path, "move")
    assert report["exit"] == 3
    assert _folders(report) == {"ARCILOGGER": "1.0.0"}
    assert _running(report) == ALL_SERVICES
    assert not any(call.startswith("exe sc.exe delete") for call in report["calls"])
    assert report["tasks"] == {"Replica": "Disabled", "Seal": "Ready", "VerifyDaily": "Ready"}


def test_upgrade_waiting_too_long_for_a_task_changes_nothing(engine: str, tmp_path: Path) -> None:
    """A sealing run that does not finish in time: the services keep running, nothing moved."""
    report = simulate(engine, tmp_path, "tasks-busy")
    assert report["exit"] == 3
    assert "[previous state restored]" in report["message"]
    assert _folders(report) == {"ARCILOGGER": "1.0.0"}
    assert _running(report) == ALL_SERVICES
    assert not [c for c in report["calls"] if c.startswith(("stop ", "exe sc.exe delete"))]
    assert report["tasks"]["Replica"] == "Disabled"
    assert report["tasks"]["VerifyDaily"] == "Ready", "paused, then enabled again"


def test_repair_failure_brings_the_services_back(engine: str, tmp_path: Path) -> None:
    report = simulate(engine, tmp_path, "repair")
    assert report["exit"] == 3
    assert "[previous state restored]" in report["message"]
    assert _folders(report) == {"ARCILOGGER": "1.0.0"}
    assert _running(report) == ALL_SERVICES
    assert report["registered_from"] == ["1.0.0"], "registered again after the failed attempt"
    assert report["state"]["last_failure"]["operation"] == "repair"


def test_rollback_that_cannot_swap_keeps_the_current_version(engine: str, tmp_path: Path) -> None:
    report = simulate(engine, tmp_path, "rollback-swap")
    assert report["exit"] == 3
    assert _folders(report) == {"ARCILOGGER": "1.0.0", "ARCILOGGER.previous": "0.9.0"}
    assert _running(report) == NEW_SERVICES
    assert report["tasks"] == {"Replica": "Disabled", "Seal": "Ready", "VerifyDaily": "Ready"}


#: arcilogger.toml as the harness writes it before an upgrade (no key a version before the
#: browser administration would refuse).
CONFIG_BEFORE_UPGRADE = 'site_name = "Kurum"\n[[sources]]\nname = "fw"\naddress = "172.20.10.1"\n'
ROLLED_BACK = (
    "Tarayıcıdan yapılan sonraki değişiklikler geri alındı; denetim kaydında duruyorlar "
    "[later browser changes were rolled back; they remain in the audit log]"
)


def test_rollback_removes_the_administration_agent_and_keeps_sealing(
    engine: str, tmp_path: Path
) -> None:
    """Contract 2.16: a rollback to a version without ArciAdmin stops it first, removes its
    registration and starts the previous version's services; its Python must load the
    configuration first, which it does when the browser added nothing it does not know."""
    report = simulate(engine, tmp_path, "rollback")
    assert report["exit"] is None, report["message"]
    folders = _folders(report)
    assert folders.pop("ARCILOGGER") == "0.9.0"
    assert list(folders.values()) == ["1.0.0"]
    assert next(iter(folders)).startswith("ARCILOGGER.rolled-back-")
    assert set(report["services"]) == ALL_SERVICES, "ArciAdmin is not part of 0.9.0"
    assert _running(report) == ALL_SERVICES
    calls = report["calls"]
    stops = [c for c in calls if c.startswith("stop ")]
    assert stops[0] == "stop ArciAdmin", "the agent stops before the services it configures"
    assert "exe sc.exe delete ArciAdmin" in calls
    assert "ArciAdmin: hizmet sarmalayıcısı bu sürümde yok" in report["stdout"]
    check = calls.index("config-check 0.9.0")
    assert check < next(i for i, c in enumerate(calls) if c.startswith("exe ArciCollector.exe"))
    assert report["config"] == CONFIG_BEFORE_UPGRADE, "a configuration that loads stays"
    assert not [name for name in report["config_files"] if ".refused-" in name]
    assert _unwrapped(ROLLED_BACK) not in _unwrapped(report["stdout"])
    assert report["tasks"] == {"Replica": "Disabled", "Seal": "Ready", "VerifyDaily": "Ready"}


def test_rollback_puts_back_the_configuration_from_before_the_upgrade(
    engine: str, tmp_path: Path
) -> None:
    """The browser switched a source off (enabled = false) after the upgrade: the previous
    version's Config refuses the key (extra="forbid"), so the copy kept before the upgrade comes
    back, the refused file stays beside it and the operator is told what that undoes."""
    report = simulate(engine, tmp_path, "rollback-incompatible")
    assert report["exit"] is None, report["message"]
    assert report["config"] == CONFIG_BEFORE_UPGRADE
    refused = [
        name for name in report["config_files"] if name.startswith("arcilogger.toml.refused-")
    ]
    assert len(refused) == 1, report["config_files"]
    kept = tmp_path / "sim" / "programdata" / "ARCILOGGER" / refused[0]
    assert "enabled = false" in kept.read_text(encoding="utf-8")
    assert _unwrapped(ROLLED_BACK) in _unwrapped(report["stdout"])
    assert any(ROLLED_BACK in warning for warning in report["health"]["warnings"])
    assert _running(report) == ALL_SERVICES
    calls = report["calls"]
    assert calls.count("config-check 0.9.0") == 2, "the copy is checked too"


def test_rollback_without_the_copy_asks_for_the_configuration_to_be_fixed(
    engine: str, tmp_path: Path
) -> None:
    report = simulate(engine, tmp_path, "rollback-no-copy")
    assert report["exit"] == 3
    assert _unwrapped("fix it by hand]") in _unwrapped(report["stdout"])
    assert report["state"]["last_failure"]["operation"] == "rollback"


def test_upgrade_keeps_a_copy_of_the_configuration_for_a_rollback(
    engine: str, tmp_path: Path
) -> None:
    """Contract 2.16: before an upgrade changes anything (after the database backup),
    arcilogger.toml is copied and the copy's name remembered for a later rollback."""
    report = simulate(engine, tmp_path, "success")
    copy = Path(report["state"]["config_before_upgrade"])
    assert re.fullmatch(r"arcilogger\.toml\.before-upgrade-\d{8}-\d{6}", copy.name), copy.name
    assert copy.name in report["config_files"]
    assert copy.read_text(encoding="utf-8") == CONFIG_BEFORE_UPGRADE
    calls = report["calls"]
    assert calls.index("pg pg_dump.exe") < calls.index("stop ArciCollector")


def test_health_check_waits_for_the_api_and_the_certificate(engine: str, tmp_path: Path) -> None:
    """Services report "Running" before Python has imported and Caddy has its certificate."""
    health = simulate(engine, tmp_path, "health-slow")["health"]
    assert health["exit_code"] == 0
    assert health["warnings"] == []
    assert health["api_calls"] == 3, "polled until the API answered"
    assert health["https_calls"] == 2, "polled until the TLS handshake worked"
    assert health["root_copied"] is True


def test_health_check_reports_an_api_that_never_answers(engine: str, tmp_path: Path) -> None:
    health = simulate(engine, tmp_path, "health-api-down")["health"]
    assert health["exit_code"] == 1, "done with warnings"
    assert health["api_calls"] > 3, "polled for -HealthWaitSeconds"
    assert health["https_calls"] == 1, "Caddy answering 502 proves TLS; no further wait"
    assert any(w.startswith("API health") for w in health["warnings"])
    assert any(w == "HTTPS health: HTTP 502" for w in health["warnings"])


def test_health_check_follows_the_configured_api_port(engine: str, tmp_path: Path) -> None:
    """[api] host/port of arcilogger.toml, not a fixed 127.0.0.1:8000 (wildcard -> loopback)."""
    health = simulate(engine, tmp_path, "health-port")["health"]
    assert health["api_uri"] == "http://127.0.0.1:18641/api/v1/health"
    assert health["exit_code"] == 0
    assert health["warnings"] == []


def test_next_steps_print_the_setup_token_command(engine: str, tmp_path: Path) -> None:
    """The first administrator: the exact command with the installed program and config paths."""
    stdout = simulate(engine, tmp_path, "next-steps")["stdout"]
    sim = tmp_path / "sim"
    api_exe = sim / "program" / "ARCILOGGER" / "venv" / "Scripts" / "arcilogger-api.exe"
    config = sim / "programdata" / "ARCILOGGER" / "arcilogger.toml"
    shown = re.search(r'& "([^"]+)" setup-token --config "([^"]+)"', stdout)
    assert shown is not None, stdout
    assert Path(shown.group(1).replace("\\", "/")) == api_exe
    assert Path(shown.group(2).replace("\\", "/")) == config
    assert "https://172.20.20.15/setup" in stdout
    # With the default folders this is the command docs/KURULUM.md gives (decision 7).
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert "$ApiExe = Join-Path $Venv 'Scripts\\arcilogger-api.exe'" in script
    assert "$Venv = Join-Path $InstallDir 'venv'" in script
    assert "Join-Path $env:ProgramFiles 'ARCILOGGER'" in script
    assert "$ConfigPath = Join-Path $ProgramDataDir 'arcilogger.toml'" in script


def test_next_steps_say_which_sources_to_add(engine: str, tmp_path: Path) -> None:
    """Without a log source the next steps name the file, what to add and what happens until
    then; with sources (or when they were not read) they say nothing about them."""
    assert "Log kaynağı yok" not in simulate(engine, tmp_path, "next-steps")["stdout"]
    shown = re.compile(
        r"Log kaynağı yok \[no log source yet\]: .* \[\[sources\]\] kayıtlarını (.+) dosyasına"
    )
    for scenario in ("next-steps-no-sources", "next-steps-no-sources-firewall"):
        run = tmp_path / scenario
        run.mkdir()
        stdout = simulate(engine, run, scenario)["stdout"]
        found = shown.search(stdout)
        assert found is not None, stdout
        config = run / "sim" / "programdata" / "ARCILOGGER" / "arcilogger.toml"
        assert Path(found.group(1).replace("\\", "/")) == config
        assert "to this file; its examples are commented out]" in stdout
        assert "docs\\DEVICE-SETUP.md bölüm 4" in stdout
        if scenario.endswith("-firewall"):
            assert "[until then the firewall opens 514 to no sender" in stdout
            assert "kurulumu yeniden çalıştırın" in stdout
        else:
            assert "[until then every sender counts as unknown" in stdout
            assert "hizmetleri yeniden başlatın" in stdout


def test_next_steps_after_a_reinstall_over_a_kept_configuration(
    engine: str, tmp_path: Path
) -> None:
    """The arcilogger.toml a removal kept, read before Python is installed: without [[sources]]
    the run ends with the preflight warning in its summary and the next steps' instruction."""
    report = simulate(engine, tmp_path, "next-steps-kept-config")
    config = tmp_path / "sim" / "programdata" / "ARCILOGGER" / "arcilogger.toml"
    warnings = report["health"]["warnings"]
    assert len(warnings) == 1, warnings
    assert warnings[0].startswith("Log kaynağı yok [no log source]: ")
    assert Path(warnings[0].split(": ", 1)[1].split(" içinde ")[0].replace("\\", "/")) == config
    assert "Log kaynağı yok [no log source yet]: " in report["stdout"]
    assert "[until then every sender counts as unknown" in report["stdout"]


def _db_license_calls(report: dict[str, Any], tmp_path: Path) -> list[tuple[list[str], int]]:
    """The "arcilogger-db license ..." calls of a simulation: the arguments before --config
    (checked to be the installation's arcilogger.toml) and the time limit."""
    config = tmp_path / "sim" / "programdata" / "ARCILOGGER" / "arcilogger.toml"
    calls = []
    for process in report["processes"]:
        assert process["exe"] == "arcilogger-db.exe", process
        assert process["allow_failure"] is True, "the exit code is evaluated, never thrown"
        arguments, _, path = process["arguments"].rpartition(" --config ")
        assert Path(path.replace("\\", "/")) == config, process
        calls.append((arguments.split(" "), process["timeout"]))
    return calls


def _step_six(stdout: str) -> list[str]:
    lines = stdout.splitlines()
    start = next(i for i, line in enumerate(lines) if line.startswith("   6. Lisans [licence]: "))
    return lines[start:]


@pytest.mark.parametrize(
    ("scenario", "head"),
    [
        (
            "next-steps-license-trial",
            "deneme süresi 14 gündür [14-day trial]. İstek kodu [request code]:",
        ),
        (
            "next-steps-license-expired",
            "deneme süresi doldu [trial over]. İstek kodu [request code]:",
        ),
        (
            "next-steps-license-wrong-server",
            "lisans durumu [licence state] wrong_server. İstek kodu [request code]:",
        ),
    ],
)
def test_next_steps_print_the_request_code(
    engine: str, tmp_path: Path, scenario: str, head: str
) -> None:
    """Contract 9.4: step 6 asks the CLI for the licence state and the request code (5 s each)
    and prints the code on one line with where to send it."""
    report = simulate(engine, tmp_path, scenario)
    lines = _step_six(report["stdout"])
    assert lines[0] == "   6. Lisans [licence]: " + head
    assert lines[1] == "      ARCIREQ1-ABCDE-FGHJK-MNPQR-ST"
    assert lines[2:4] == list(REQUEST_CODE_MAIL)
    assert _db_license_calls(report, tmp_path) == [
        (["license", "status", "--json"], 5),
        (["license", "request-code"], 5),
    ]


def test_next_steps_show_the_licensee_instead_of_the_code(engine: str, tmp_path: Path) -> None:
    report = simulate(engine, tmp_path, "next-steps-licensed")
    lines = _step_six(report["stdout"])
    assert lines[0] == "   6. Lisans [licence]: lisanslı [licensed]: Örnek A.Ş."
    assert "ARCIREQ1" not in report["stdout"]
    assert "arman@khadangan.com" not in report["stdout"]
    assert _db_license_calls(report, tmp_path) == [(["license", "status", "--json"], 5)]


@pytest.mark.parametrize(
    ("scenario", "reason"),
    [
        ("next-steps-license-db-down", "(veritabanına ulaşılamadı [database unreachable])"),
        (
            "next-steps-license-slow",
            "5 saniyede bitmedi, durduruldu [did not finish within 5 s, stopped]",
        ),
        # The simulated arcilogger-db.exe is no program: starting it fails.
        ("next-steps", "("),
    ],
)
def test_next_steps_print_the_request_code_command_when_it_fails(
    engine: str, tmp_path: Path, scenario: str, reason: str
) -> None:
    report = simulate(engine, tmp_path, scenario)
    lines = _step_six(report["stdout"])
    assert lines[0].startswith(
        "   6. Lisans [licence]: istek kodu alınamadı [request code not available] "
    )
    assert reason in lines[0]
    assert lines[0].endswith(
        "; daha sonra yönetici PowerShell'inde çalıştırın "
        "[run it later in an administrator PowerShell]:"
    )
    command = re.fullmatch(r'      & "([^"]+)" license request-code --config "([^"]+)"', lines[1])
    assert command is not None, lines[1]
    sim = tmp_path / "sim"
    db_exe = sim / "program" / "ARCILOGGER" / "venv" / "Scripts" / "arcilogger-db.exe"
    assert Path(command.group(1).replace("\\", "/")) == db_exe
    assert (
        Path(command.group(2).replace("\\", "/"))
        == sim / "programdata" / "ARCILOGGER" / "arcilogger.toml"
    )
    assert lines[2:4] == list(REQUEST_CODE_MAIL)


def test_license_file_is_installed_with_the_cli(engine: str, tmp_path: Path) -> None:
    report = simulate(engine, tmp_path, "license-install-ok")
    lic = tmp_path / "sim" / "customer.lic"
    [(arguments, timeout)] = _db_license_calls(report, tmp_path)
    assert arguments[:2] == ["license", "install"]
    assert Path(arguments[2].replace("\\", "/")) == lic
    assert arguments[3:] == ["--json"]
    assert timeout == 120
    assert (
        f"Lisans yüklendi [licence installed]: {LICENSE_ID}; durum [state]: licensed"
        in report["stdout"]
    )
    assert report["health"]["warnings"] == []


OTHER_SERVER_TR = (
    "Lisans bu sunucu için düzenlenmemiş (bu sunucu: XW68-7K1Z-5FGV-AGT8). Yeni lisans için "
    "istek kodunu gönderin."
)
OTHER_SERVER_EN = (
    "The licence was not issued for this server (this server: XW68-7K1Z-5FGV-AGT8). Send the "
    "request code for a new licence."
)


@pytest.mark.parametrize(
    ("scenario", "reason", "later"),
    [
        ("license-install-refused", f"{OTHER_SERVER_TR} [{OTHER_SERVER_EN}]", False),
        ("license-install-db-down", "veritabanına ulaşılamadı [database unreachable]", True),
        ("license-install-crash", "simulated: arcilogger-db.exe could not be started", True),
    ],
)
def test_license_problems_are_warnings_not_failures(
    engine: str, tmp_path: Path, scenario: str, reason: str, later: bool
) -> None:
    """Exit 1 (refused: the product's message_tr [message_en]), exit 3 (database unreachable) or
    a program that cannot run: a warning, never an exception (simulate checks that none
    escaped), so the installation goes on and ends with exit code 1."""
    report = simulate(engine, tmp_path, scenario)
    [warning] = report["health"]["warnings"]
    prefix = "Lisans dosyası yüklenmedi [licence file not installed]: "
    if later:
        assert warning.startswith(f"{prefix}{reason}. Daha sonra yönetici PowerShell'inde ")
        assert re.search(
            r' license install "[^"]+customer\.lic" --config "[^"]+arcilogger\.toml"$', warning
        )
    else:
        # The same file would be refused again: no command to run later.
        assert warning == prefix + reason


def test_upgrade_with_a_refused_licence_completes(engine: str, tmp_path: Path) -> None:
    """The licence step runs after the migration and never triggers the recovery: the new
    version runs (5651 functions on), the licence state stays what it was."""
    report = simulate(engine, tmp_path, "upgrade-license-refused")
    assert report["exit"] is None, report["message"]
    assert _folders(report) == {"ARCILOGGER": "2.0.0", "ARCILOGGER.previous": "1.0.0"}
    assert _running(report) == NEW_SERVICES
    calls = report["calls"]
    migration = next(
        i for i, c in enumerate(calls) if c.startswith("exe arcilogger-db.exe upgrade --config ")
    )
    licence = next(
        i for i, c in enumerate(calls) if c.startswith("process arcilogger-db.exe license install ")
    )
    assert migration < licence < calls.index("start ArciIngest")
    assert report["health"]["warnings"] == [
        "Lisans dosyası yüklenmedi [licence file not installed]: "
        f"{OTHER_SERVER_TR} [{OTHER_SERVER_EN}]"
    ]
    assert "last_failure" not in report["state"], "no recovery ran"


def test_first_install_shows_the_setup_token_and_stores_kamusm_credentials(
    engine: str, tmp_path: Path
) -> None:
    """After the transcript: the token on the console only, the Kamu SM secrets on stdin."""
    report = simulate(engine, tmp_path, "first-install-console")
    stdout = report["stdout"]
    assert "SIMULATED-SETUP-TOKEN" in stdout
    assert "https://172.20.20.15/setup" in stdout, "with the browser address"
    assert "https://localhost/setup" in stdout
    processes = report["processes"]
    assert [p["arguments"].split(" --config ")[0] for p in processes][:1] == ["setup-token"]
    stored = {
        p["arguments"].rsplit(" ", 1)[-1]: p for p in processes if " secret set " in p["arguments"]
    }
    # The names the core's Kamu SM client reads (arcilogger_core.secretstore).
    assert set(stored) == {KAMUSM_CUSTOMER, KAMUSM_PASSWORD}
    assert stored[KAMUSM_CUSTOMER]["stdin"] == "7654321\n"
    assert stored[KAMUSM_PASSWORD]["stdin"] == "kamu-sim-pw-1\n"  # gitleaks:allow
    for process in stored.values():
        assert set(process["redact"]) == {"7654321", "kamu-sim-pw-1"}, "never printed"
        assert "7654321" not in process["arguments"], "never on a command line"
        assert "kamu-sim-pw-1" not in process["arguments"], "never on a command line"
    # Each "secret set" replaced secrets.json: ArciAdmin's entry comes back after the last one.
    order = [p["exe"] for p in processes]
    assert order.index("Set-ArciAclEntry") > max(
        i for i, p in enumerate(processes) if " secret set " in p["arguments"]
    )
    secure = [c for c in report["calls"] if c.startswith("read-host secure=True")]
    assert len(secure) == 1, "the password is read hidden"
    assert "parola" in secure[0]
    assert "kamu-sim-pw-1" not in stdout


def test_first_install_token_bypasses_transcripts_and_redirection(
    engine: str, tmp_path: Path
) -> None:
    """A transcript or a redirection started outside the installer (by the operator or a Group
    Policy) records Write-Host; the token line goes to the console handle and is not in it."""
    report = simulate(engine, tmp_path, "first-install-captured")
    assert report["stdout"].count("SIMULATED-SETUP-TOKEN") == 2, "shown both times"
    transcript = report["transcript"]
    redirected = report["redirected"]
    # The text around the token is captured (so the capture works), the token itself is not.
    assert "İlk yönetici [first administrator]" in transcript
    assert "İlk yönetici [first administrator]" in redirected
    assert "PowerShell kayıtlarına (transcript, yönlendirme) yazılmadı" in transcript
    assert "SIMULATED-SETUP-TOKEN" not in transcript
    assert "SIMULATED-SETUP-TOKEN" not in redirected
    assert "Geçerlilik: 24 saat." in transcript, "the Turkish lines stay Write-Host"


def test_first_install_over_an_existing_administrator_needs_no_token(
    engine: str, tmp_path: Path
) -> None:
    """A reinstallation over a kept database: setup-token exits 4, which is no failure."""
    report = simulate(engine, tmp_path, "first-install-admin-exists")
    stdout = report["stdout"]
    assert "Bir yönetici hesabı zaten var" in stdout, "the API's own reason"
    assert "Kurulum anahtarı gerekmiyor" in stdout
    assert "create-admin" in stdout
    assert "alınamadı" not in stdout, "no 'fix it and run setup-token' instruction"
    assert "[fix it and run]" not in stdout
    assert "https://localhost/setup" not in stdout


def test_first_install_without_a_person_at_the_console_shows_and_asks_nothing(
    engine: str, tmp_path: Path
) -> None:
    """Redirected output, -NonInteractive or CI: no token in a captured log, no question."""
    report = simulate(engine, tmp_path, "first-install-quiet")
    assert report["processes"] == []
    assert not [c for c in report["calls"] if c.startswith("read-host")]
    assert "SIMULATED-SETUP-TOKEN" not in report["stdout"]


def _power_call(report: dict[str, Any], tmp_path: Path) -> str:
    """How the Set-ArciPower.ps1 stub was called: "remove=True" or "remove=False"."""
    called, _, state = str(report["power"]).partition(" state=")
    expected = tmp_path / "sim" / "programdata" / "ARCILOGGER" / "install" / "power-state.json"
    assert Path(state) == expected, report["power"]
    return called


def test_desktop_upgrade_applies_the_power_settings(engine: str, tmp_path: Path) -> None:
    report = simulate(engine, tmp_path, "desktop-upgrade")
    assert report["exit"] is None, report["message"]
    assert _power_call(report, tmp_path) == "remove=False"
    # On Windows Server (the other simulations) the power settings are not touched.
    server = tmp_path / "server"
    server.mkdir()
    assert simulate(engine, server, "success")["power"] is None


def test_uninstall_restores_the_recorded_power_settings(engine: str, tmp_path: Path) -> None:
    report = simulate(engine, tmp_path, "uninstall-desktop")
    assert report["exit"] is None, report["message"]
    assert _power_call(report, tmp_path) == "remove=True"


def test_uninstall_removes_programs_and_keeps_data(engine: str, tmp_path: Path) -> None:
    report = simulate(engine, tmp_path, "uninstall")
    assert report["exit"] is None, report["message"]
    assert _folders(report) == {"ARCILOGGER.notes": None}, "only ARCILOGGER's program folders"
    assert report["services"] == {}
    assert report["tasks"] == {}
    assert report["firewall"] == "remove=True"
    assert report["power"] is None, "nothing recorded: nothing to restore"
    assert report["data_kept"] is True
    assert report["config_kept"] is True
    calls = report["calls"]
    # The tasks are paused before anything else; installer\ and VERSION.json go last, with the
    # folder itself (a refused file leaves the script that can finish the removal).
    assert calls.index("unregister Seal state=Disabled") < calls.index("remove ARCILOGGER")
    removed = [c for c in calls if c.startswith("remove ")]
    program_removal = removed[: removed.index("remove ARCILOGGER") + 1]
    assert {"remove venv", "remove services", "remove caddy"} <= set(program_removal)
    assert program_removal[-1] == "remove ARCILOGGER"
    assert "remove installer" not in program_removal
    assert "remove VERSION.json" not in program_removal


def test_uninstall_waits_for_a_running_evidence_job(engine: str, tmp_path: Path) -> None:
    """A sealing run holds python.exe of the program folder open: wait for it, then remove."""
    report = simulate(engine, tmp_path, "uninstall-busy")
    assert report["exit"] is None, report["message"]
    calls = report["calls"]
    assert "seal instance finished" in calls
    assert calls.index("seal instance finished") < calls.index("stop ArciWeb")
    assert not [c for c in calls if c.startswith("unregister") and c.endswith("state=Running")]
    assert _folders(report) == {"ARCILOGGER.notes": None}
    assert report["services"] == {}
    assert report["tasks"] == {}


def test_uninstall_with_a_job_that_does_not_finish_changes_nothing(
    engine: str, tmp_path: Path
) -> None:
    report = simulate(engine, tmp_path, "uninstall-busy-stuck")
    assert report["exit"] == 2, "stopped before any change"
    assert "[task still running]" in report["message"]
    assert "[nothing removed]" in report["message"]
    assert not [c for c in report["calls"] if c.startswith(("stop ", "exe sc.exe", "remove "))]
    assert report["firewall"] is None, "the firewall rules stay"
    assert _running(report) == ALL_SERVICES
    assert _folders(report)["ARCILOGGER"] == "1.0.0"
    assert _folders(report)["ARCILOGGER.previous"] == "0.9.0"
    # The paused jobs run again; the one the operator switched off stays off.
    assert report["tasks"]["VerifyDaily"] == "Ready"
    assert report["tasks"]["Replica"] == "Disabled"


def test_setup_uninstaller_reports_a_failed_removal() -> None:
    """The removal script can stop (a task still running): the operator is told how to retry."""
    text = (INSTALLER / "ARCILOGGER.iss").read_bytes()[len(BOM) :].decode("utf-8")
    step = text[text.index("procedure CurUninstallStepChanged") :]
    step = step[: step.index("\nend;")]
    assert "(ResultCode <> 0) and (ResultCode <> 1)" in step
    assert "CustomMessage('UninstallFailed')" in step
    for language in ("turkish", "english"):
        message = re.search(rf"(?m)^{language}\.UninstallFailed=(.+)$", text)
        assert message is not None, language
        assert "%1" in message.group(1)
        assert '-File "%2" -Uninstall' in message.group(1)


# --- database provisioning SQL -------------------------------------------------------------------
#
# Invoke-DatabaseProvision runs for real (dot-sourced installer); only psql and arcilogger-core are
# replaced by recorders, so the test sees exactly the SQL the installer sends to PostgreSQL.

_PROVISION_SCRIPT = r"""
param([string]$Installer, [string]$Base, [string]$DbName, [string]$OutputPath, [switch]$NoTimescale)
$ErrorActionPreference = 'Stop'
$programData = Join-Path $Base 'programdata/ARCILOGGER'
[void](New-Item -ItemType Directory -Path (Join-Path $programData 'install') -Force)
. $Installer -InstallDir (Join-Path $Base 'program/ARCILOGGER') -ProgramDataDir $programData `
    -DataRoot (Join-Path $Base 'data/ARCILOGGER') -BundleRoot (Join-Path $Base 'bundle') `
    -SkipFirewall -SkipTimeSetup
$script:Recorded = New-Object System.Collections.Generic.List[object]
function Get-DatabaseSettings {
    [pscustomobject]@{ host = '127.0.0.1'; port = 5432; name = $DbName; user = $DbName }
}
function Get-PgCredential {
    $secret = ConvertTo-SecureString 'x' -AsPlainText -Force
    New-Object System.Management.Automation.PSCredential -ArgumentList 'postgres', $secret
}
function Invoke-ArciProcess {
    param([string]$FilePath, [string[]]$ArgumentList, [string]$StdinText, [hashtable]$Environment,
        [string[]]$Redact, [switch]$AllowFailure)
    [pscustomobject]@{ ExitCode = 0; StdOut = ''; StdErr = '' }
}
function Invoke-PgTool {
    param([string]$Tool, [string[]]$ArgumentList, [string]$StdinText, [string[]]$Redact = @())
    $database = $ArgumentList[[array]::IndexOf($ArgumentList, '-d') + 1]
    $script:Recorded.Add([ordered]@{ database = $database; sql = $StdinText })
    # TimescaleDB available and preloaded: the installer creates the extension.
    $out = ''
    if ($StdinText -match 'pg_available_extensions') {
        $out = $(if ($NoTimescale) { '0' } else { '1' })
    }
    elseif ($StdinText -match 'shared_preload_libraries') { $out = 'timescaledb' }
    [pscustomobject]@{ ExitCode = 0; StdOut = $out; StdErr = '' }
}
Invoke-DatabaseProvision
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
$json = ConvertTo-Json -InputObject $script:Recorded.ToArray() -Depth 4
[System.IO.File]::WriteAllText($OutputPath, $json, $utf8)
"""


def _provision_sql(
    engine: str, tmp_path: Path, db_name: str = "arcilogger", *, timescale: bool = True
) -> list[dict[str, str]]:
    output = tmp_path / "provision.json"
    script = write_script(tmp_path / "provision.ps1", _PROVISION_SCRIPT)
    base = tmp_path / "provision"
    base.mkdir()
    result = run_powershell(
        engine,
        script,
        "-Installer",
        str(INSTALL_SCRIPT),
        "-Base",
        str(base),
        "-DbName",
        db_name,
        "-OutputPath",
        str(output),
        *([] if timescale else ["-NoTimescale"]),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    recorded: list[dict[str, str]] = read_json(output)
    return recorded


def _first(calls: list[dict[str, str]], *needles: str) -> int:
    return next(i for i, call in enumerate(calls) if all(n in call["sql"] for n in needles))


def test_database_provision_turns_timescaledb_telemetry_off_before_the_extension(
    engine: str, tmp_path: Path
) -> None:
    """ADR-0005 / spec 21: no telemetry, also not in the minute between extension and migration.

    CREATE EXTENSION starts TimescaleDB's scheduler, whose telemetry job runs at once (verified
    with TimescaleDB 2.30.2 on PostgreSQL 16: without the database setting the job ran when the
    extension was created; with it, telemetry_level was 'off' and the job never ran).
    """
    calls = _provision_sql(engine, tmp_path)
    setting = _first(calls, "ALTER DATABASE %I SET timescaledb.telemetry_level")
    extension = _first(calls, "CREATE EXTENSION IF NOT EXISTS timescaledb")
    assert setting < extension, "the database setting exists before the extension"
    assert calls[setting]["database"] == "postgres"
    provisioning = calls[setting]["sql"]
    assert provisioning.index("CREATE DATABASE") < provisioning.index("telemetry_level")
    assert "%L', :'db_name', 'off') \\gexec" in provisioning
    # Server-wide too, once the extension (and so the parameter) is loaded in the session.
    created = calls[extension]["sql"]
    assert calls[extension]["database"] == "arcilogger"
    assert (
        created.index("CREATE EXTENSION IF NOT EXISTS timescaledb;")
        < created.index("ALTER SYSTEM SET timescaledb.telemetry_level = 'off';")
        < created.index("SELECT pg_reload_conf();")
    )


def test_database_is_created_with_the_c_locale_only_when_new(engine: str, tmp_path: Path) -> None:
    """A Windows locale name can change with an update (PostgreSQL BUG #18196); C cannot."""
    calls = _provision_sql(engine, tmp_path, timescale=False)
    provisioning = calls[_first(calls, "CREATE DATABASE")]["sql"]
    create = next(line for line in provisioning.splitlines() if "CREATE DATABASE" in line)
    assert (
        "TEMPLATE template0 ENCODING %L LOCALE %L', :'db_name', :'role_name', 'UTF8', 'C')"
        in create
    )
    assert "WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name')" in create
    assert "ALTER DATABASE %I SET timescaledb" in provisioning
    assert not re.search(r"ALTER DATABASE[^\n]*(LOCALE|COLLATE|CTYPE)", provisioning), (
        "an existing database is never altered"
    )


TEST_PG = os.environ.get("ARCILOGGER_TEST_PG")


@pytest.mark.skipif(not TEST_PG, reason="ARCILOGGER_TEST_PG (a PostgreSQL to create a database on)")
def test_database_create_statement_runs_on_postgresql(engine: str, tmp_path: Path) -> None:
    """The installer's own CREATE DATABASE (\\gexec) against a real server: UTF8, locale C.

    Runs only the statements that need CREATEDB (ARCILOGGER_TEST_PG need not be a superuser):
    the \\set lines and the CREATE DATABASE line of the provisioning script, owned by the test
    user. psql comes from ARCILOGGER_PSQL, PATH or /usr/lib/postgresql/16/bin.
    """
    assert TEST_PG is not None
    psql = (
        os.environ.get("ARCILOGGER_PSQL")
        or shutil.which("psql")
        or next((p for p in ["/usr/lib/postgresql/16/bin/psql"] if Path(p).is_file()), None)
    )
    if not psql:
        pytest.skip("psql not found")
    dsn = urllib.parse.urlsplit(TEST_PG)
    name = f"arci_locale_{uuid.uuid4().hex[:12]}"
    calls = _provision_sql(engine, tmp_path, db_name=name, timescale=False)
    provisioning = calls[_first(calls, "CREATE DATABASE")]["sql"].splitlines()
    owner = dsn.username or "postgres"
    sql = [
        line for line in provisioning if line.startswith("\\set") and "role_verifier" not in line
    ]
    sql = [f"\\set role_name '{owner}'" if "role_name" in line else line for line in sql]
    sql += [line for line in provisioning if "CREATE DATABASE" in line]
    env = {**os.environ, "PGPASSWORD": dsn.password or "", "PGCONNECT_TIMEOUT": "15"}
    base = [psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", dsn.hostname or "127.0.0.1"]
    base += ["-p", str(dsn.port or 5432), "-U", owner, "-d", (dsn.path or "/postgres")[1:]]

    def run(script: str, *extra: str) -> str:
        result = subprocess.run(
            [*base, *extra, "-f", "-"],
            input=script,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    try:
        run("\n".join(sql) + "\n")
        query = (
            "SELECT pg_encoding_to_char(encoding), datcollate, datctype FROM pg_database "
            "WHERE datname = :'name';\n"
        )
        assert run(query, "-At", "-v", f"name={name}") == "UTF8|C|C"
        run("\n".join(sql) + "\n")  # run again: the existing database is left alone
    finally:
        run(f"DROP DATABASE IF EXISTS {name};\n")


TSDB_PG = os.environ.get("ARCILOGGER_TSDB_PG")


@pytest.mark.skipif(
    not TSDB_PG, reason="ARCILOGGER_TSDB_PG (superuser DSN of a throw-away TimescaleDB) not set"
)
def test_database_provision_sql_on_a_timescaledb_server(engine: str, tmp_path: Path) -> None:
    """The installer's own SQL against a real TimescaleDB server (opt-in, changes that server).

    Proves the statements run as written (placeholder setting before the extension exists,
    ALTER SYSTEM after it) and that the telemetry job never ran. The ALTER SYSTEM right after
    CREATE EXTENSION usually wins the race against the scheduler on its own; only the database
    setting before CREATE EXTENSION makes it certain, which the test above checks.

    ARCILOGGER_TSDB_PG: a superuser DSN of a throw-away PostgreSQL 16 with
    shared_preload_libraries = 'timescaledb' (the run sets telemetry off server-wide). psql comes
    from ARCILOGGER_PSQL or PATH.
    """
    assert TSDB_PG is not None
    psql = os.environ.get("ARCILOGGER_PSQL") or shutil.which("psql")
    if not psql:
        pytest.skip("psql not found")
    dsn = urllib.parse.urlsplit(TSDB_PG)
    env = {**os.environ, "PGPASSWORD": dsn.password or "", "PGCONNECT_TIMEOUT": "15"}
    base = [psql, "-X", "-q", "-v", "ON_ERROR_STOP=1", "-h", dsn.hostname or "127.0.0.1"]
    base += ["-p", str(dsn.port or 5432), "-U", dsn.username or "postgres"]
    name = f"arci_tsdb_{uuid.uuid4().hex[:12]}"

    def run(database: str, sql: str, *extra: str) -> str:
        result = subprocess.run(
            [*base, *extra, "-d", database, "-f", "-"],
            input=sql,
            capture_output=True,
            text=True,
            env=env,
            check=False,
            timeout=120,
        )
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    calls = _provision_sql(engine, tmp_path, db_name=name)
    try:
        for call in calls:
            run(call["database"], call["sql"])
        time.sleep(3)  # the scheduler starts its jobs right after CREATE EXTENSION
        assert run(name, "SHOW timescaledb.telemetry_level;", "-At") == "off"
        runs = run(
            name,
            "SELECT count(*) FROM _timescaledb_internal.bgw_job_stat "
            "WHERE job_id = 1 AND total_runs > 0;",
            "-At",
        )
        assert runs == "0", "the telemetry job must never have run"
    finally:
        run("postgres", f"DROP DATABASE IF EXISTS {name} WITH (FORCE);\n")
        run("postgres", f"DROP ROLE IF EXISTS {name};\n")


# --- real installation (Windows CI) --------------------------------------------------------------
#
# The installer workflow's end-to-end job builds a real bundle with Build-ArciBundle.ps1 and points
# ARCILOGGER_E2E_BUNDLE at it. This test then installs, upgrades, rolls back, survives a broken
# upgrade and uninstalls on the (throw-away) runner, as Administrator.
#
# With ARCILOGGER_E2E_DB=1 (the workflow starts the PostgreSQL of the runner image and passes its
# throw-away superuser in ARCILOGGER_E2E_PGUSER / ARCILOGGER_E2E_PGPASSWORD / ARCILOGGER_E2E_PGBIN)
# the installation includes the database, and the test checks what only the real accounts and
# NTFS rights show: ingest, API and worker running as NT SERVICE\<name>; the API, through Caddy,
# reading today's raw spool line and the sealing calendar (with no "problems": the calendar answers
# 200 even when it may not list the spool); "Şimdi mühürle" through Caddy, the API and ArciAdmin
# waking the Seal task, run by Task Scheduler as LOCAL SERVICE, which seals yesterday (vault
# files, token, state, deny entry, spool day removed), and the API then reading that day (raw
# line from the vault, its calendar state and day page from state\seal). Then the browser
# administration of contract 3 (ADR-0014): a site name change reaching "active" (ArciAdmin
# replaces arcilogger.toml and keeps its own entry on it), a log source the collector takes
# without a restart and ApplySystem lets through the syslog rule within 2 minutes, "Kontör
# sorgula" in the Requests task, the agent's entries in the Application log. The rollback goes
# to a program without ArciAdmin, as to a version from before the browser administration: the
# agent and its two tasks are removed, sealing goes on. Without the database the database steps
# are skipped (-SkipDatabase) as before.

E2E_BUNDLE = os.environ.get("ARCILOGGER_E2E_BUNDLE")
E2E_DB = os.environ.get("ARCILOGGER_E2E_DB") == "1"

# Starts the installer with the PostgreSQL superuser as a PSCredential (which cannot be passed on
# a -File command line) or with -SkipDatabase.
_E2E_INSTALL_SCRIPT = r"""
param([string]$Installer, [string]$Mode, [string]$PlanPath, [string]$ServerAddress,
    [string]$AdminAddress, [string]$SourceNetwork)
$ErrorActionPreference = 'Stop'
$parameters = @{ SkipTimeSetup = $true; MinFreeGB = 1; HealthWaitSeconds = 60 }
if ($Mode) { $parameters[$Mode] = $true }
if ($SourceNetwork) { $parameters['SourceNetwork'] = @($SourceNetwork -split ',') }
if ($PlanPath) { $parameters['PlanPath'] = $PlanPath }
if ($ServerAddress) { $parameters['ServerAddress'] = $ServerAddress }
if ($AdminAddress) { $parameters['AdminAddress'] = @($AdminAddress) }
if ($env:ARCILOGGER_E2E_DB -eq '1') {
    # The runner image's throw-away superuser (not a secret); never on a command line.
    $secure = ConvertTo-SecureString -String $env:ARCILOGGER_E2E_PGPASSWORD -AsPlainText -Force
    $parameters['PgSuperuserCredential'] = New-Object System.Management.Automation.PSCredential `
        -ArgumentList $env:ARCILOGGER_E2E_PGUSER, $secure
    if ($env:ARCILOGGER_E2E_PGBIN) { $parameters['PgBinDir'] = $env:ARCILOGGER_E2E_PGBIN }
}
else {
    $parameters['SkipDatabase'] = $true
}
& $Installer @parameters
exit $LASTEXITCODE
"""

# Runs \ARCILOGGER\Seal through Task Scheduler (as LOCAL SERVICE, from its registered definition)
# and reports its result and the ACL of a sealed file. -NoStart: a run something else started
# ("Şimdi mühürle": Event 4110 of ARCILOGGER-Admin), only waited for.
_E2E_SEAL_SCRIPT = r"""
param([string]$SealedFile, [string]$OutputPath, [int]$TimeoutSeconds = 600, [switch]$NoStart)
$ErrorActionPreference = 'Stop'
Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue
$path = '\ARCILOGGER\'
$before = (Get-ScheduledTaskInfo -TaskPath $path -TaskName 'Seal').LastRunTime
# A scheduled run (every 5 minutes) may be running: Start-ScheduledTask then starts nothing
# (IgnoreNew), so wait for a run that started after this point and has finished.
$deadline = [DateTime]::UtcNow.AddSeconds($TimeoutSeconds)
while ((Get-ScheduledTask -TaskPath $path -TaskName 'Seal').State -eq 'Running') {
    if ([DateTime]::UtcNow -gt $deadline) { throw 'Seal is still running' }
    Start-Sleep -Seconds 2
}
if (-not $NoStart) {
    $started = Get-Date
    Start-ScheduledTask -TaskPath $path -TaskName 'Seal'
    while ($true) {
        $info = Get-ScheduledTaskInfo -TaskPath $path -TaskName 'Seal'
        $state = [string](Get-ScheduledTask -TaskPath $path -TaskName 'Seal').State
        $ran = $info.LastRunTime -ge $started.AddSeconds(-1) -and $info.LastRunTime -ne $before
        if ($ran -and $state -ne 'Running') { break }
        if ([DateTime]::UtcNow -gt $deadline) {
            throw ('Seal did not finish: state {0}, last run {1}' -f $state, $info.LastRunTime)
        }
        Start-Sleep -Seconds 2
    }
}
$info = Get-ScheduledTaskInfo -TaskPath $path -TaskName 'Seal'
$task = Get-ScheduledTask -TaskPath $path -TaskName 'Seal'
$denies = @()
if (Test-Path -LiteralPath $SealedFile) {
    $sid = [System.Security.Principal.SecurityIdentifier]
    $denies = @((Get-Acl -LiteralPath $SealedFile).GetAccessRules($true, $false, $sid) |
            Where-Object { [string]$_.AccessControlType -eq 'Deny' } |
            ForEach-Object { $_.IdentityReference.Value })
}
$user = [string]$task.Principal.UserId
if ($user -notmatch '^S-1-') {
    $account = New-Object System.Security.Principal.NTAccount -ArgumentList $user
    $user = $account.Translate([System.Security.Principal.SecurityIdentifier]).Value
}
$report = [ordered]@{
    last_result = [int64]$info.LastTaskResult
    user_sid = $user
    deny_sids = $denies
}
[System.IO.File]::WriteAllText($OutputPath, ($report | ConvertTo-Json -Depth 4))
"""


def _e2e_api_session() -> str:
    """An administrator with a valid session, created in the installed database (no login flow).

    Uses the API's own helpers (``arcilogger_api.testing``); the KVKK notice is accepted so the
    raw-line view answers. Returns the Cookie header for requests through Caddy.
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from arcilogger_api.auth.accounts import accept_privacy, privacy_version
    from arcilogger_api.security import CSRF_COOKIE, SESSION_COOKIE, create_session
    from arcilogger_api.testing import make_user
    from arcilogger_db.models import User

    user = urllib.parse.quote(os.environ["ARCILOGGER_E2E_PGUSER"], safe="")
    password = urllib.parse.quote(os.environ["ARCILOGGER_E2E_PGPASSWORD"], safe="")
    engine = create_engine(f"postgresql+psycopg://{user}:{password}@127.0.0.1:5432/arcilogger")
    try:
        factory = sessionmaker(engine)
        admin = make_user(factory, "admin", username=f"e2e-{uuid.uuid4().hex[:8]}")
        with factory() as session:
            accept_privacy(session, admin.id, privacy_version(session), ip=None)
            issued = create_session(
                session, session.get_one(User, admin.id), mfa_passed=True, user_agent="e2e"
            )
            session.commit()
    finally:
        engine.dispose()
    return f"{SESSION_COOKIE}={issued.token}; {CSRF_COOKIE}={issued.csrf_token}"


def _e2e_database_locale() -> tuple[str, str, str]:
    """Encoding, collation and character type of the installed database (runner superuser)."""
    from sqlalchemy import create_engine, text

    user = urllib.parse.quote(os.environ["ARCILOGGER_E2E_PGUSER"], safe="")
    password = urllib.parse.quote(os.environ["ARCILOGGER_E2E_PGPASSWORD"], safe="")
    engine = create_engine(f"postgresql+psycopg://{user}:{password}@127.0.0.1:5432/postgres")
    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT pg_encoding_to_char(encoding), datcollate, datctype "
                    "FROM pg_database WHERE datname = 'arcilogger'"
                )
            ).one()
    finally:
        engine.dispose()
    return str(row[0]), str(row[1]), str(row[2])


def _https_get(path: str, cafile: Path, cookie: str) -> tuple[int, str]:
    """GET https://localhost<path> through Caddy; (status, body) also for error answers."""
    context = ssl.create_default_context(cafile=str(cafile))
    request = urllib.request.Request(f"https://localhost{path}", headers={"Cookie": cookie})
    try:
        with urllib.request.urlopen(request, context=context, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def _https_send(
    method: str, path: str, cafile: Path, cookie: str, body: Any = None
) -> tuple[int, str]:
    """POST/PUT/DELETE https://localhost<path> through Caddy as the web interface sends it: a
    JSON body and the session's CSRF token in its header (double submit); (status, body)."""
    from arcilogger_api.security import CSRF_COOKIE, CSRF_HEADER

    csrf = next(
        part.split("=", 1)[1] for part in cookie.split("; ") if part.startswith(f"{CSRF_COOKIE}=")
    )
    headers = {"Cookie": cookie, CSRF_HEADER: csrf, "Content-Type": "application/json"}
    data = b"" if body is None else json.dumps(body).encode("utf-8")
    context = ssl.create_default_context(cafile=str(cafile))
    request = urllib.request.Request(
        f"https://localhost{path}", data=data, headers=headers, method=method
    )
    try:
        with urllib.request.urlopen(request, context=context, timeout=60) as response:
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def _e2e_follow(accepted: str, cafile: Path, cookie: str, timeout: int) -> dict[str, Any]:
    """The request a 202 answered (its ``status_url``) until it ends or ``timeout`` seconds
    passed: the last answer of ``GET /admin/requests/{id}``."""
    status_url = json.loads(accepted)["status_url"]
    deadline = time.monotonic() + timeout
    while True:
        status, body = _https_get(status_url, cafile, cookie)
        assert status == 200, (status_url, status, body)
        request: dict[str, Any] = json.loads(body)
        if request["terminal"] or time.monotonic() > deadline:
            return request
        time.sleep(3)


def _e2e_database_checks(
    powershell: str, tmp_path: Path, data: Path, program_data: Path, marker: str, last: str
) -> str:
    """The database services, the API's rights and the Seal task under their real accounts.
    Returns the Cookie header of the administrator's session."""
    state = _e2e_state(powershell, tmp_path)
    services = {service["name"]: service for service in state["services"]}
    for name in ("ArciIngest", "ArciApi", "ArciWorker"):
        assert services[name]["state"] == "Running", (name, last)
        assert services[name]["account"] == f"NT SERVICE\\{name}", services[name]
    # The installer created the database: UTF8 with the C locale (no Windows locale name).
    assert _e2e_database_locale() == ("UTF8", "C", "C"), last
    cafile = program_data / "caddy-root.crt"
    status, body = _https_get("/api/v1/health", cafile, "")
    assert status == 200, (status, body)
    assert json.loads(body)["status"] == "ok", body
    cookie = _e2e_api_session()
    # Today's raw line, read by NT SERVICE\ArciApi from the spool (the day is not sealed yet).
    spool = data / "spool" / "local"
    today_file = next(f for f in spool.glob("*.log") if marker.encode() in f.read_bytes())
    today = date.fromisoformat(today_file.stem)
    status, body = _https_get(f"/api/v1/raw?source=local&day={today}&line=1", cafile, cookie)
    assert status == 200, ("raw line of today", status, body)
    assert "arcilogger-e2e-" in body, body
    # The sealing calendar lists the spool for every unsealed day (this month and the last).
    previous_month = (today.replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    for month in (today.strftime("%Y-%m"), previous_month):
        _e2e_calendar(month, cafile, cookie)

    # Yesterday's spool day, sealed by "Şimdi mühürle" (contract 3, Windows CI): its last
    # attempt failed and the next one is hours away, so the Seal task's own 5-minute runs leave
    # it alone. The API checks the day, ArciAdmin signs the task request and wakes the Seal task
    # (Event 4110), which runs as LOCAL SERVICE and takes the request once.
    yesterday = today - timedelta(days=1)
    now = timeutil.utc_now()
    state_file = data / "state" / "seal" / f"{yesterday}.json"
    failed_attempt = {
        "day": yesterday.isoformat(),
        "status": "pending",
        "attempts": 1,
        "last_attempt_at": timeutil.isoformat_site(now - timedelta(minutes=5), "seconds"),
        "last_error": "the time-stamp server did not answer",
        "last_error_code": "tsa_unavailable",
        "next_attempt_at": timeutil.isoformat_site(now + timedelta(hours=3), "seconds"),
    }
    state_file.write_text(json.dumps(failed_attempt), encoding="utf-8")
    received = datetime(
        yesterday.year, yesterday.month, yesterday.day, 12, 0, tzinfo=timeutil.SITE_TZ
    )
    message = f"<134>arcilogger-e2e-seal {uuid.uuid4().hex}".encode()
    day_file = spool / f"{yesterday}.log"
    day_file.write_bytes(format_line(received, "127.0.0.1", message))
    quiet = received.replace(hour=23, minute=59)
    os.utime(day_file, (quiet.timestamp(), quiet.timestamp()))
    vault_day = data / "vault" / f"{yesterday:%Y}" / f"{yesterday:%m}" / f"{yesterday:%d}"
    sealed = vault_day / "local.log.gz"
    _e2e_wait_for_agent(cafile, cookie, last)
    status, body = _https_send("POST", f"/api/v1/seals/{yesterday}/seal", cafile, cookie)
    assert status == 202, ("seal now", status, body, last)
    # The Seal task takes a request at once, or in its next 5-minute run when one was busy.
    seal_now = _e2e_follow(body, cafile, cookie, timeout=900)
    alerts = data / "state" / "alerts.jsonl"
    detail = alerts.read_text(encoding="utf-8")[-4000:] if alerts.is_file() else "(no alerts)"
    assert seal_now["state"] == "succeeded", (seal_now, detail)
    assert seal_now["result"]["status"] == "sealed", seal_now
    script = write_script(tmp_path / "e2e-seal.ps1", _E2E_SEAL_SCRIPT)
    out = tmp_path / "e2e-seal.json"
    result = run_powershell(
        powershell, script, "-SealedFile", str(sealed), "-OutputPath", str(out), "-NoStart",
        timeout=900,
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(out)
    assert report["user_sid"] == "S-1-5-19", report  # LOCAL SERVICE
    assert report["last_result"] == 0, (report, detail)
    assert sealed.is_file(), (sorted(p.name for p in vault_day.glob("*")), detail)
    assert (vault_day / f"manifest-{yesterday}.json").is_file()
    assert (vault_day / f"manifest-{yesterday}.json.zd").is_file()
    seal_state = json.loads(state_file.read_text("utf-8"))
    assert seal_state["status"] == "sealed", seal_state
    assert seal_state["requested"], "the day records the seal request it served"
    assert not day_file.exists(), "the sealed spool day is removed"
    assert "S-1-1-0" in report["deny_sids"], "the sealed file carries the Everyone deny entry"
    # The API reads the sealed day from the vault, and its seal state from state\seal.
    status, body = _https_get(f"/api/v1/raw?source=local&day={yesterday}&line=1", cafile, cookie)
    assert status == 200, ("raw line of the sealed day", status, body)
    assert "arcilogger-e2e-seal" in body, body
    cells = _e2e_calendar(yesterday.strftime("%Y-%m"), cafile, cookie)
    assert cells[yesterday.isoformat()]["state"] in {"sealed", "verified", "late"}, cells
    status, body = _https_get(f"/api/v1/seals/{yesterday}", cafile, cookie)
    assert status == 200, ("sealed day", status, body)
    assert json.loads(body)["problems"] == [], body
    return cookie


def _e2e_calendar(month: str, cafile: Path, cookie: str) -> dict[str, dict[str, Any]]:
    """The sealing calendar of ``month`` through Caddy, read without a single problem.

    The API answers 200 even when NT SERVICE\\ArciApi may not list the spool or read a state
    file: it reports that in ``problems`` and marks the day "erişilemiyor". Both must be absent,
    or the check would not notice a missing NTFS right. Returns the days by date.
    """
    status, body = _https_get(f"/api/v1/seals/calendar?month={month}", cafile, cookie)
    assert status == 200, ("sealing calendar", month, status, body)
    calendar = json.loads(body)
    assert calendar["problems"] == [], ("sealing calendar", month, calendar["problems"])
    unreadable = [
        day for day in calendar["days"] if "erişilemiyor" in (day.get("message_tr") or "")
    ]
    assert not unreadable, ("sealing calendar", month, unreadable)
    return {str(day["day"]): day for day in calendar["days"]}


_E2E_STATE_SCRIPT = r"""
param([string]$OutputPath)
Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue
# Piped straight from the commands: a variable holding "nothing" would pipe one $null.
$services = @(Get-CimInstance -ClassName Win32_Service -Filter "Name LIKE 'Arci%'" |
        Where-Object { $_ } | ForEach-Object {
            [ordered]@{
                name = $_.Name; state = $_.State; account = $_.StartName; start = $_.StartMode
            }
        })
$tasks = @(Get-ScheduledTask -TaskPath '\ARCILOGGER\' -ErrorAction SilentlyContinue |
        Where-Object { $_ } | ForEach-Object {
            [ordered]@{ name = $_.TaskName; state = [string]$_.State }
        })
$rules = @(Get-NetFirewallRule -Group 'ARCILOGGER' -ErrorAction SilentlyContinue |
        Where-Object { $_ } | ForEach-Object { [string]$_.DisplayName })
$report = [ordered]@{ services = $services; tasks = $tasks; rules = $rules }
[System.IO.File]::WriteAllText($OutputPath, ($report | ConvertTo-Json -Depth 4))
"""


# The rights and registrations of the browser administration (contract 2.2, 2.16) as Windows
# reports them: the allow entries of each path, whether it is protected, the event source and the
# events written under it, the accounts and last results of the Requests, ApplySystem and Seal
# tasks, and the senders of the two syslog rules (RemoteAddress).
_E2E_ADMIN_SCRIPT = r"""
param([string[]]$Paths, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Remove-TypeData -TypeName System.Array -ErrorAction SilentlyContinue
# One text through -File: the paths are separated by commas.
$Paths = @($Paths | ForEach-Object { $_ -split ',' } | Where-Object { $_ })
$sid = [System.Security.Principal.SecurityIdentifier]
$acl = [ordered]@{}
foreach ($path in $Paths) {
    if (-not (Test-Path -LiteralPath $path)) { $acl[$path] = $null; continue }
    $security = Get-Acl -LiteralPath $path
    $rules = @($security.GetAccessRules($true, $true, $sid) | Where-Object { $_ })
    $entries = @($rules | ForEach-Object {
            [ordered]@{
                sid = $_.IdentityReference.Value; rights = [int64][int]$_.FileSystemRights
                allow = ([string]$_.AccessControlType -eq 'Allow'); inherited = [bool]$_.IsInherited
            }
        })
    $protected = [bool]$security.AreAccessRulesProtected
    $owner = $security.GetOwner($sid).Value
    $acl[$path] = [ordered]@{ protected = $protected; owner = $owner; entries = $entries }
}
$source = 'ARCILOGGER-Admin'
$log = $null
if ([System.Diagnostics.EventLog]::SourceExists($source)) {
    $log = [System.Diagnostics.EventLog]::LogNameFromSourceName($source, '.')
}
$events = @()
if ($log) {
    $events = @(Get-WinEvent -FilterHashtable @{ LogName = $log; ProviderName = $source } `
            -MaxEvents 1000 -ErrorAction SilentlyContinue | Where-Object { $_ } |
            ForEach-Object { [int]$_.Id })
}
$principals = [ordered]@{}
$results = [ordered]@{}
foreach ($name in @('Requests', 'ApplySystem', 'Seal')) {
    $task = Get-ScheduledTask -TaskPath '\ARCILOGGER\' -TaskName $name -ErrorAction SilentlyContinue
    if (-not $task) { $principals[$name] = $null; $results[$name] = $null; continue }
    $user = [string]$task.Principal.UserId
    if ($user -notmatch '^S-1-') {
        $account = New-Object System.Security.Principal.NTAccount -ArgumentList $user
        $user = $account.Translate($sid).Value
    }
    $principals[$name] = $user
    $info = Get-ScheduledTaskInfo -TaskPath '\ARCILOGGER\' -TaskName $name
    $results[$name] = [int64]$info.LastTaskResult
}
$syslog = [ordered]@{}
foreach ($name in @('ARCILOGGER Syslog UDP', 'ARCILOGGER Syslog TCP')) {
    $rules = @(Get-NetFirewallRule -Group 'ARCILOGGER' -ErrorAction SilentlyContinue |
            Where-Object { $_ -and [string]$_.DisplayName -eq $name })
    $syslog[$name] = @($rules | Get-NetFirewallAddressFilter |
            ForEach-Object { @($_.RemoteAddress) } | Where-Object { $_ } |
            ForEach-Object { [string]$_ })
}
$report = [ordered]@{
    acl = $acl; event_log = $log; events = $events; principals = $principals; results = $results
    syslog = $syslog
}
[System.IO.File]::WriteAllText($OutputPath, ($report | ConvertTo-Json -Depth 6))
"""

#: FileSystemRights that change a file or a folder's contents.
E2E_WRITE_RIGHTS = (
    0x2 | 0x4 | 0x10 | 0x40 | 0x100 | 0x10000 | 0x40000 | 0x80000 | 0x10000000 | 0x40000000
)


def _e2e_admin_state(powershell: str, tmp_path: Path, paths: list[Path]) -> dict[str, Any]:
    script = write_script(tmp_path / "e2e-admin.ps1", _E2E_ADMIN_SCRIPT)
    out = tmp_path / f"e2e-admin-{time.monotonic_ns()}.json"
    # An empty text after -Paths would not reach a -File script as an argument at all.
    names = ["-Paths", ",".join(str(path) for path in paths)] if paths else []
    result = run_powershell(powershell, script, *names, "-OutputPath", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    state: dict[str, Any] = read_json(out)
    return state


def _e2e_check_admin_rights(
    powershell: str, tmp_path: Path, program_data: Path, data: Path, log: str
) -> None:
    """Contract 2.2 on the real NTFS: the API cannot write the configuration, the secrets, the
    Zamane folder or the vault; ArciAdmin writes the configuration and the secret store through
    explicit file entries and nothing of the private key of the API; each private key has one
    reader; install\\ is protected; the worker reads policy.json (its heartbeat check); the data
    root record is protected, owned by administrators and written by nobody else; the event
    source belongs to the Application log; Requests runs as LOCAL SERVICE and ApplySystem as
    SYSTEM. Checked after the installation stored the database password and so replaced
    secrets.json: the installer gives ArciAdmin its entry back (Restore-ConfigFileAccess)."""
    keys = program_data / "admin" / "keys"
    paths = [
        program_data / "arcilogger.toml", program_data / "secrets.json",
        program_data / "secret.key", program_data / "zamane", program_data / "install",
        keys / "api-ed25519.key", keys / "agent-ed25519.key", data / "vault",
        program_data / "admin" / "policy.json", program_data / "data-root.txt",
    ]  # fmt: skip
    state = _e2e_admin_state(powershell, tmp_path, paths)
    acl = {Path(path).name: facts for path, facts in state["acl"].items()}
    assert all(facts is not None for facts in acl.values()), (acl, log)
    api, agent = service_sid("ArciApi"), service_sid("ArciAdmin")

    def writes(name: str, sid: str, *, explicit: bool = False) -> bool:
        return any(
            entry["allow"] and entry["sid"] == sid and entry["rights"] & E2E_WRITE_RIGHTS
            and not (explicit and entry["inherited"])
            for entry in acl[name]["entries"]
        )  # fmt: skip

    for name in ("arcilogger.toml", "secrets.json", "secret.key", "zamane", "vault"):
        assert not writes(name, api), (name, acl[name], log)
    for name in ("arcilogger.toml", "secrets.json"):
        assert writes(name, agent, explicit=True), (name, acl[name], log)
    assert not writes("secret.key", agent), (acl["secret.key"], log)
    owners = {WELL_KNOWN["system"], WELL_KNOWN["admins"]}
    for name, reader in (("api-ed25519.key", api), ("agent-ed25519.key", agent)):
        sids = {entry["sid"] for entry in acl[name]["entries"] if entry["allow"]}
        assert sids == owners | {reader}, (name, acl[name], log)
    assert acl["install"]["protected"] is True, (acl["install"], log)
    worker = service_sid("ArciWorker")
    assert any(
        entry["allow"] and entry["sid"] == worker and entry["rights"] & 0x1
        for entry in acl["policy.json"]["entries"]
    ), (acl["policy.json"], log)
    record = acl["data-root.txt"]
    assert record["protected"] is True, (record, log)
    assert record["owner"] in {WELL_KNOWN["system"], WELL_KNOWN["admins"]}, (record, log)
    for sid in (agent, api, worker, WELL_KNOWN["local_service"]):
        assert not writes("data-root.txt", sid), (sid, record, log)
    assert state["event_log"] == "Application", (state, log)
    assert state["principals"] == {
        "Requests": WELL_KNOWN["local_service"],
        "ApplySystem": WELL_KNOWN["system"],
        "Seal": WELL_KNOWN["local_service"],
    }, (state, log)


#: The organisation's name and the log source the real installation test sets in the browser
#: (the fictional example network's firewall: nothing sends from it on the runner).
E2E_SITE_NAME = "Örnek A.Ş. (Windows CI)"
E2E_SOURCE = {
    "name": "firewall-01",
    "type": "fortigate",
    "address": "172.20.10.1",
    "heartbeat_minutes": 15,
    "expected": False,
    "transport": "any",
    "enabled": True,
}
#: The senders the runner's installation allows (-SourceNetwork, admin\policy.json): its one
#: source sends from 127.0.0.1, which RFC 1918 alone would leave out of the syslog rules.
E2E_SOURCE_NETWORKS = "127.0.0.1/32,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"


def _e2e_json(path: str, cafile: Path, cookie: str) -> dict[str, Any]:
    status, body = _https_get(path, cafile, cookie)
    assert status == 200, (path, status, body)
    found: dict[str, Any] = json.loads(body)
    return found


def _e2e_wait_for_agent(cafile: Path, cookie: str, log: str) -> None:
    """ArciAdmin's heartbeat is fresh (``GET /admin/status``): until then the API answers
    ``admin_agent_unavailable`` (contract 2.16)."""
    deadline = time.monotonic() + 120
    while (agent := _e2e_json("/api/v1/admin/status", cafile, cookie)["agent"])["state"] != "ok":
        assert time.monotonic() < deadline, (agent, log)
        time.sleep(5)


def _e2e_browser_administration(
    powershell: str, tmp_path: Path, data: Path, program_data: Path, cookie: str, log: str
) -> None:
    """Contract 3, Windows CI: the browser administration through Caddy and the API, applied
    by ArciAdmin, ApplySystem and the Requests task under their real accounts and rights."""
    cafile = program_data / "caddy-root.crt"
    config = program_data / "arcilogger.toml"
    _e2e_wait_for_agent(cafile, cookie, log)

    def submit(method: str, path: str, body: Any = None) -> str:
        status, text = _https_send(method, path, cafile, cookie, body)
        assert status == 202, (method, path, status, text, log)
        return text

    def etag(section: str) -> str:
        found: str = _e2e_json("/api/v1/admin/config", cafile, cookie)["etags"][section]
        return found

    # 1. The site name: ArciAdmin replaces arcilogger.toml (ReplaceFileW), the API and the worker
    #    load it without a restart, and the file keeps ArciAdmin's explicit entry.
    before = sha256_bytes(config.read_bytes())
    accepted = submit(
        "PUT", "/api/v1/admin/config/general",
        {"etag": etag("general"), "site_name": E2E_SITE_NAME},
    )  # fmt: skip
    done = _e2e_follow(accepted, cafile, cookie, timeout=300)
    assert done["state"] == "active", (done, log)
    assert tomllib.loads(config.read_text(encoding="utf-8"))["site_name"] == E2E_SITE_NAME
    assert sha256_bytes(config.read_bytes()) != before
    assert _e2e_json("/api/v1/auth/me", cafile, cookie)["site_name"] == E2E_SITE_NAME
    _e2e_check_admin_rights(powershell, tmp_path, program_data, data, log)
    # NT SERVICE\ArciAdmin writes the Application log: its start and the applied change.
    events = set(_e2e_admin_state(powershell, tmp_path, [])["events"])
    assert {int(Event.AGENT_STARTED), int(Event.CHANGE_APPLIED)} <= events, (events, log)

    # 2. A log source: the collector takes it without a restart (its status reports the new
    #    file), and ApplySystem then adds the sender to both syslog rules, in place, within two
    #    minutes of the request.
    address = str(E2E_SOURCE["address"])
    syslog = _e2e_admin_state(powershell, tmp_path, [])["syslog"]
    assert all(address not in senders for senders in syslog.values()), (syslog, log)
    started = time.monotonic()
    accepted = submit(
        "POST", "/api/v1/admin/config/sources", {"etag": etag("sources"), "source": E2E_SOURCE}
    )
    while not all(address in senders for senders in syslog.values()):
        assert time.monotonic() - started < 120, (syslog, log)
        time.sleep(5)
        syslog = _e2e_admin_state(powershell, tmp_path, [])["syslog"]
    assert all("127.0.0.1" in senders for senders in syslog.values()), (syslog, log)
    collector = json.loads((data / "state" / "collector-status.json").read_bytes())
    assert collector["config"]["sha256"] == sha256_bytes(config.read_bytes()), collector
    done = _e2e_follow(accepted, cafile, cookie, timeout=300)
    assert done["state"] == "active", (done, log)
    steps = {step["step"]: step["state"] for step in done["steps"]}
    assert (steps["reload.collector"], steps["system.firewall"]) == ("done", "done"), steps

    # 3. "Kontör sorgula": the Requests task (LOCAL SERVICE, Event 4111) asks the mock provider.
    done = _e2e_follow(submit("POST", "/api/v1/credits/query"), cafile, cookie, timeout=900)
    assert done["state"] == "succeeded", (done, log)
    assert done["result"]["credits"] >= 0, done
    state = _e2e_admin_state(powershell, tmp_path, [])
    assert state["results"]["Requests"] == 0, (state["results"], log)
    expected = {int(Event.SEAL_REQUESTED), int(Event.TASK_REQUESTED), int(Event.SYSTEM_SYNC_RESULT)}
    assert expected <= set(state["events"]), (state["events"], log)


def _e2e_state(powershell: str, tmp_path: Path) -> dict[str, Any]:
    script = write_script(tmp_path / "e2e-state.ps1", _E2E_STATE_SCRIPT)
    out = tmp_path / f"e2e-state-{time.monotonic_ns()}.json"
    result = run_powershell(powershell, script, "-OutputPath", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    state: dict[str, Any] = read_json(out)
    return state


def _bundle_variant(source: Path, target: Path, readme: str, *, broken: bool = False) -> Path:
    """Another bundle (another fingerprint); ``broken``: a Caddyfile that caddy adapt refuses."""
    shutil.copytree(source, target)
    with (target / "README.txt").open("a", encoding="utf-8") as handle:
        handle.write(readme + "\n")
    if broken:
        with (target / "caddy" / "Caddyfile").open("a", encoding="utf-8") as handle:
            handle.write("\nthis is { not a valid site block\n")
    (target / "SHA256SUMS").unlink()
    write_sums(target)
    return target


def _wait_for_marker(spool: Path, marker: bytes, seconds: int = 60) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if any(marker in file.read_bytes() for file in spool.glob("*.log")):
            return True
        time.sleep(1)
    return False


@pytest.mark.skipif(not E2E_BUNDLE, reason="needs the real bundle the end-to-end job builds")
def test_real_install_upgrade_rollback_and_uninstall(tmp_path: Path) -> None:
    powershell = _windows_admin_powershell()
    assert E2E_BUNDLE is not None
    first = Path(E2E_BUNDLE)
    program = Path(os.environ["PROGRAMFILES"]) / "ARCILOGGER"
    program_data = Path(os.environ["PROGRAMDATA"]) / "ARCILOGGER"
    data = Path(os.environ.get("RUNNER_TEMP", "D:/")) / "ARCILOGGER"
    program_data.mkdir(parents=True, exist_ok=True)
    # The runner has none of the example network's devices: one local sender, the mock
    # time-stamp provider, an organisation name (required) and no local time source (a warning).
    (program_data / "arcilogger.toml").write_text(
        f'site_name = "CI"\ndata_dir = "{data.as_posix()}"\n\n'
        '[[sources]]\nname = "local"\ntype = "generic"\naddress = "127.0.0.1"\n\n'
        '[collector]\nbind = "127.0.0.1"\nudp_port = 514\ntcp_port = 514\n\n'
        '[tsa]\nprovider = "mock"\n',
        encoding="utf-8",
    )
    wrapper = write_script(tmp_path / "e2e-install.ps1", _E2E_INSTALL_SCRIPT)
    runs: list[str] = []
    plans: list[dict[str, Any]] = []

    def record(label: str, result: subprocess.CompletedProcess[str]) -> str:
        text = (
            f"===== Install-Arcilogger.ps1 {label}: exit code {result.returncode} =====\n"
            f"{result.stdout}\n----- stderr -----\n{result.stderr}\n"
        )
        runs.append(text)
        # pytest prints the captured output of a failed test: every installer run is in the log,
        # whatever the exit code (an exit code of 0 or 1 alone proves nothing).
        print(text, flush=True)
        return text

    def run(
        bundle: Path, mode: str = "", server: str = "", admin: str = "", networks: str = ""
    ) -> subprocess.CompletedProcess[str]:
        script = bundle / "installer" / "Install-Arcilogger.ps1"
        plan = tmp_path / f"e2e-plan-{len(plans)}.json"
        arguments = ["-Installer", str(script), "-PlanPath", str(plan)]
        if mode:
            arguments += ["-Mode", mode]
        if server:
            arguments += ["-ServerAddress", server]
        if admin:
            arguments += ["-AdminAddress", admin]
        if networks:
            arguments += ["-SourceNetwork", networks]
        result = run_powershell(powershell, wrapper, *arguments, timeout=1800)
        record(f"-{mode}" if mode else "(install)", result)
        plans.append(read_json(plan) if plan.is_file() else {"warnings": [], "missing": True})
        return result

    def check(check_id: str) -> dict[str, Any]:
        """A preflight check of the last run's plan."""
        items = {item["id"]: item for item in plans[-1].get("items", [])}
        assert f"preflight.{check_id}" in items, (check_id, last())
        found: dict[str, Any] = items[f"preflight.{check_id}"]
        return found

    def health_warnings() -> list[str]:
        """Warnings of the last run about services, the API or Caddy (not, e.g., PG's version)."""
        warnings = [str(w) for w in plans[-1].get("warnings", [])]
        services = ("ArciCollector", "ArciIngest", "ArciApi", "ArciWorker", "ArciWeb", "ArciAdmin")
        prefixes = ("API health", "HTTPS health", "Caddy root", *(f"{s}:" for s in services))
        return [w for w in warnings if w.startswith(prefixes) or "[failed to start]" in w]

    def last() -> str:
        return runs[-1] if runs else "(the installer has not run)"

    def fingerprint() -> str:
        version = program / "VERSION.json"
        assert version.is_file(), f"{version} missing after the installer run\n{last()}"
        document = json.loads(version.read_text(encoding="utf-8-sig"))
        assert document["product"] == "ARCILOGGER"
        return str(document["fingerprint"])

    def siblings(prefix: str) -> list[Path]:
        return [p for p in program.parent.iterdir() if p.name.startswith(f"ARCILOGGER.{prefix}")]

    removal: subprocess.CompletedProcess[str] | None = None
    try:
        # 1. Fresh installation from the bundle the build produced. The runner's adapters are not
        #    known in advance, so the address is given (later runs use the remembered one); the
        #    admin PC has no default either (a documentation address: nobody connects from it);
        #    the source networks of the browser administration include the local sender.
        result = run(first, server="127.0.0.1", admin="192.0.2.10", networks=E2E_SOURCE_NETWORKS)
        assert result.returncode in (0, 1), last()
        assert check("site-name")["status"] == "ok", last()
        assert check("ntp-server")["status"] == "warn", last()
        first_fingerprint = sha256_bytes((first / "SHA256SUMS").read_bytes())
        assert fingerprint() == first_fingerprint, last()
        # The configuration's bind (127.0.0.1) can be listened on: a warning, never a stop.
        assert check("collector-bind")["status"] == "warn", last()
        install_state = json.loads(
            (program_data / "install" / "state.json").read_text(encoding="utf-8-sig")
        )
        assert install_state["server_address"] == "127.0.0.1", install_state
        assert install_state["server_name"] == "arcilogger.internal", install_state
        assert not (program / "INSTALL-IN-PROGRESS.json").exists(), last()
        assert (program / "licenses" / "winsw-LICENSE.txt").is_file(), last()
        state = _e2e_state(powershell, tmp_path)
        services = {service["name"]: service for service in state["services"]}
        # ArciAdmin needs no database: it runs in both modes (contract 2.16).
        for name in ("ArciCollector", "ArciWeb", "ArciAdmin"):
            assert name in services, (name, state, last())
            assert services[name]["state"] == "Running", (name, last())
            assert services[name]["account"] == f"NT SERVICE\\{name}", services[name]
        assert {task["name"] for task in state["tasks"]} >= {
            "Seal", "VerifyDaily", "Replica", "Requests", "ApplySystem",
        }  # fmt: skip
        _e2e_check_admin_rights(powershell, tmp_path, program_data, data, last())
        policy = parse_document(AdminPolicy, (program_data / "admin" / "policy.json").read_bytes())
        assert policy.enabled is True, last()
        assert ",".join(policy.source_networks) == E2E_SOURCE_NETWORKS, last()
        recorded = (program_data / "data-root.txt").read_text(encoding="utf-8").strip()
        assert Path(recorded) == data, (recorded, last())
        assert {"ARCILOGGER Syslog UDP", "ARCILOGGER HTTPS"} <= set(state["rules"]), last()
        # A syslog message reaches the raw spool through the service and its NTFS rights.
        marker = f"arcilogger-e2e-{uuid.uuid4().hex}"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(f"<134>{marker}".encode(), ("127.0.0.1", 514))
        assert _wait_for_marker(data / "spool" / "local", marker.encode()), last()
        # The web interface over HTTPS, trusted through Caddy's own root certificate only.
        context = ssl.create_default_context(cafile=str(program_data / "caddy-root.crt"))
        with urllib.request.urlopen("https://localhost/", context=context, timeout=30) as page:
            assert page.status == 200
            assert "default-src 'self'" in page.headers["Content-Security-Policy"]
            assert b"<html" in page.read().lower()
        if E2E_DB:
            # With the database every service must be healthy (a version warning is fine).
            assert health_warnings() == [], last()
            cookie = _e2e_database_checks(powershell, tmp_path, data, program_data, marker, last())
            _e2e_browser_administration(powershell, tmp_path, data, program_data, cookie, last())

        # 2. Upgrade to another bundle: the previous version is kept.
        second = _bundle_variant(first, tmp_path / "second", "second")
        result = run(second, "Upgrade")
        assert result.returncode in (0, 1), last()
        assert "[remembered]" in check("server-address")["text"], last()
        assert fingerprint() == sha256_bytes((second / "SHA256SUMS").read_bytes()), last()
        previous = json.loads(
            (Path(f"{program}.previous") / "VERSION.json").read_text(encoding="utf-8-sig")
        )
        assert previous["fingerprint"] == first_fingerprint, last()
        if E2E_DB:
            # Backed up first; the database services run again on the migrated schema.
            assert list((data / "backups" / "db").glob("*.dump")), last()
            assert health_warnings() == [], last()

        # 3. Rollback to it, as to a version from before the browser administration (contract
        #    2.16): the kept program loses the agent's service definition, its two tasks and
        #    ApplySystem's script. The rollback stops and removes ArciAdmin, the tasks follow the
        #    restored program, and sealing goes on.
        kept = Path(f"{program}.previous")
        for part in (
            "services/ArciAdmin.exe", "services/ArciAdmin.xml", "tasks/Requests.xml",
            "tasks/ApplySystem.xml", "scripts/Sync-ArciSystem.ps1",
        ):  # fmt: skip
            (kept / part).unlink()
        result = run(second, "Rollback")
        assert result.returncode in (0, 1), last()
        assert fingerprint() == first_fingerprint, last()
        assert siblings("rolled-back-"), last()
        warnings = [str(warning) for warning in plans[-1].get("warnings", [])]
        assert any(warning.startswith("ArciAdmin:") for warning in warnings), last()
        state = _e2e_state(powershell, tmp_path)
        services = {service["name"]: service for service in state["services"]}
        assert "ArciAdmin" not in services, (services, last())
        assert services["ArciCollector"]["state"] == "Running", last()
        tasks = {task["name"]: task["state"] for task in state["tasks"]}
        assert not {"Requests", "ApplySystem"} & set(tasks), (tasks, last())
        assert tasks["Seal"] != "Disabled", (tasks, last())
        script = write_script(tmp_path / "e2e-seal.ps1", _E2E_SEAL_SCRIPT)
        out = tmp_path / "e2e-seal-rollback.json"
        sealing = run_powershell(
            powershell, script, "-SealedFile", str(tmp_path / "none"), "-OutputPath", str(out),
            timeout=900,
        )  # fmt: skip
        assert sealing.returncode == 0, sealing.stdout + sealing.stderr
        assert read_json(out)["last_result"] == 0, (read_json(out), last())

        # 4. A broken upgrade puts the running version back by itself.
        third = _bundle_variant(first, tmp_path / "third", "third", broken=True)
        result = run(third, "Upgrade")
        assert result.returncode == 3, last()
        assert fingerprint() == first_fingerprint, last()
        assert siblings("failed-"), last()
        assert not Path(f"{program}.previous").exists(), last()
        state = _e2e_state(powershell, tmp_path)
        services = {service["name"]: service for service in state["services"]}
        assert services["ArciCollector"]["state"] == "Running", last()
        assert services["ArciWeb"]["state"] == "Running", last()
        assert all(task["state"] != "Disabled" for task in state["tasks"]), last()
        marker = f"arcilogger-e2e-{uuid.uuid4().hex}"
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
            sender.sendto(f"<134>{marker}".encode(), ("127.0.0.1", 514))
        assert _wait_for_marker(data / "spool" / "local", marker.encode()), last()
    finally:
        # 5. Removal, also after a failure above: programs, services, tasks, rules go.
        installed = program / "installer" / "Install-Arcilogger.ps1"
        script = (
            installed if installed.is_file() else first / "installer" / "Install-Arcilogger.ps1"
        )
        removal = run_powershell(powershell, script, "-Uninstall", timeout=1800)
        record("-Uninstall", removal)
    assert removal.returncode in (0, 1), last()
    assert not [p for p in program.parent.iterdir() if p.name.startswith("ARCILOGGER")], last()
    state = _e2e_state(powershell, tmp_path)
    assert state["services"] == []
    assert state["tasks"] == []
    assert state["rules"] == []
    # Data and configuration are never deleted; the event source goes, the admin folder stays.
    assert any((data / "spool" / "local").glob("*.log"))
    assert (program_data / "arcilogger.toml").is_file()
    assert (program_data / "secret.key").is_file()
    assert (program_data / "admin" / "policy.json").is_file()
    assert _e2e_admin_state(powershell, tmp_path, [program_data])["event_log"] is None


# --- firewall ------------------------------------------------------------------------------------


def _firewall(
    engine: str,
    tmp_path: Path,
    config_text: str,
    *extra: str,
    state: Any = None,
    admin_address: str | None = ADMIN_ADDRESS,
) -> tuple[subprocess.CompletedProcess[str], dict[str, Any] | None]:
    """A dry run of Set-ArciFirewall.ps1; -AdminAddress (no default) unless ``admin_address``
    is None or the arguments name one."""
    config = tmp_path / "arcilogger.toml"
    config.write_text(config_text, encoding="utf-8")
    plan = tmp_path / "firewall.json"
    state_file = tmp_path / "firewall-state.json"
    if state is not None:
        state_file.write_text(json.dumps(state), encoding="utf-8")
    result = run_powershell(
        engine,
        WINDOWS / "scripts" / "Set-ArciFirewall.ps1",
        "-ConfigPath",
        str(config),
        "-Python",
        sys.executable,
        "-StateFile",
        str(state_file),
        "-DryRun",
        "-PlanPath",
        str(plan),
        *extra,
        *_setup_arguments(extra, None, admin_address),
    )
    return result, (read_json(plan) if plan.is_file() else None)


STRICT_CONFIG = (
    'data_dir = "x"\n'
    '[[sources]]\nname = "fortigate"\ntype = "fortigate"\naddress = "172.31.255.1"\n'
    '[clock]\nntp_server = "172.20.10.1"\nfallback_server = "10.0.0.9"\n'
    '[replica]\nenabled = true\ntarget = "//10.0.0.4/arcilogger-worm"\n'
)
# IP addresses only, so the plan never depends on DNS.
STRICT_ARGS = (
    "-DnsServer",
    "172.20.20.53",
    "-KamuSmHost",
    "10.0.0.1",
    "-KamuSmCertificateHost",
    "10.0.0.2",
    "-SmtpServer",
    "10.0.0.3:587",
)


def test_firewall_plan_from_sources(engine: str, tmp_path: Path) -> None:
    text = with_example_sources((WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8"))
    result, plan = _firewall(engine, tmp_path, text)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    config = tomllib.loads(text)
    senders = [source["address"] for source in config["sources"]]
    rules = {rule["name"]: rule for rule in plan["rules"]}
    assert set(rules) == {
        "ARCILOGGER Syslog UDP",
        "ARCILOGGER Syslog TCP",
        "ARCILOGGER HTTPS",
        "ARCILOGGER RDP",
        "ARCILOGGER RDP UDP",
    }
    for name, protocol in (("ARCILOGGER Syslog UDP", "UDP"), ("ARCILOGGER Syslog TCP", "TCP")):
        assert rules[name]["protocol"] == protocol
        assert rules[name]["ports"] == ["514"]
        assert rules[name]["remote_address"] == senders
        assert rules[name]["direction"] == "Inbound"
    assert rules["ARCILOGGER HTTPS"]["ports"] == ["443"]
    assert rules["ARCILOGGER RDP"]["ports"] == rules["ARCILOGGER RDP UDP"]["ports"] == ["3389"]
    assert rules["ARCILOGGER RDP UDP"]["protocol"] == "UDP"
    for name in ("ARCILOGGER HTTPS", "ARCILOGGER RDP", "ARCILOGGER RDP UDP"):
        assert rules[name]["remote_address"] == ["172.20.10.37"], name
    assert plan["default_inbound"] == "Block"
    assert plan["default_outbound"] == "Allow"
    assert plan["strict_outbound"] is False
    assert plan["strict_outbound_source"] == "default"
    # Everything else inbound is closed: other allow rules (e.g. WinRM on 5985) are switched off,
    # except Core Networking (DHCP, neighbour discovery, ICMP errors).
    assert plan["other_inbound_rules"].startswith("disabled")
    assert plan["other_outbound_rules"] == "unchanged"
    assert plan["keep_rule_groups"] == ["@FirewallAPI.dll,-25000", "Core Networking"]
    assert all(rule["action"] == "Allow" for rule in plan["rules"])
    if plan["would_disable"] is None:
        assert plan["kept_group_rules"] is None, "no firewall cmdlets off Windows"
    else:
        # Windows: the rules this run would switch off, read from the real firewall.
        assert plan["kept_group_rules"] > 0, "the Core Networking group exists"
        kept = set(plan["keep_rule_groups"])
        for rule in plan["would_disable"]:
            assert rule["direction"] == "Inbound", rule
            assert rule["group"] not in kept, rule
            assert rule["group"] != "ARCILOGGER", rule


def test_firewall_without_sources_keeps_514_closed(engine: str, tmp_path: Path) -> None:
    """A new installation has no [[sources]] (the example's are commented out): a valid state.
    No syslog rule is made, so the default Block keeps 514 closed (nothing is opened in its
    place); the web and remote desktop rules are made as always and the script says what to
    add. The ARCILOGGER rules of an earlier run are replaced by this set (``Remove-NetFirewallRule
    -Group``), so removing the last source closes 514 again."""
    text = (WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8")
    assert "sources" not in tomllib.loads(text)
    result, plan = _firewall(engine, tmp_path, text)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert plan is not None
    assert {rule["name"] for rule in plan["rules"]} == {
        "ARCILOGGER HTTPS",
        "ARCILOGGER RDP",
        "ARCILOGGER RDP UDP",
    }
    assert plan["default_inbound"] == "Block"
    assert all(rule["remote_address"] == [ADMIN_ADDRESS] for rule in plan["rules"])
    # Windows PowerShell 5.1 wraps warnings at the console width: compare the words.
    words = " ".join(output.split())
    assert "[no syslog sender configured: UDP/TCP 514 stays closed" in words
    assert "[[sources]]" in words
    assert "docs\\DEVICE-SETUP.md" in words
    script = (WINDOWS / "scripts" / "Set-ArciFirewall.ps1").read_text(encoding="utf-8-sig")
    assert (
        "Get-NetFirewallRule -Group $Group -ErrorAction SilentlyContinue | Remove-NetFirewallRule"
        in (script)
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Windows Defender Firewall only")
def test_core_networking_group_resource_name(engine: str, tmp_path: Path) -> None:
    """The group kept open is found by its locale-independent resource name (English runner)."""
    script = write_script(
        tmp_path / "group.ps1",
        "param([string]$OutputPath)\n"
        "$rule = Get-NetFirewallRule -DisplayGroup 'Core Networking' | Select-Object -First 1\n"
        "[System.IO.File]::WriteAllText($OutputPath, [string]$rule.Group)\n",
    )
    out = tmp_path / "group.txt"
    result = run_powershell(engine, script, "-OutputPath", str(out))
    assert result.returncode == 0, result.stdout + result.stderr
    assert out.read_text(encoding="utf-8-sig") == "@FirewallAPI.dll,-25000"


def test_firewall_allows_winrm_only_from_the_admin_pc_on_request(
    engine: str, tmp_path: Path
) -> None:
    text = 'data_dir = "x"\n[[sources]]\nname = "a"\ntype = "generic"\naddress = "172.31.255.1"\n'
    result, plan = _firewall(
        engine, tmp_path, text, "-AllowWinRM", "-AdminAddress", "172.20.10.0/28"
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    rules = {rule["name"]: rule for rule in plan["rules"]}
    assert rules["ARCILOGGER WinRM"]["ports"] == ["5985"]
    assert rules["ARCILOGGER WinRM"]["remote_address"] == ["172.20.10.0/28"]


def test_firewall_strict_outbound_plan(engine: str, tmp_path: Path) -> None:
    result, plan = _firewall(
        engine,
        tmp_path,
        STRICT_CONFIG,
        "-StrictOutbound",
        *STRICT_ARGS,
        "-AdminAddress",
        "172.20.10.37",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    assert plan["default_outbound"] == "Block"
    assert plan["other_outbound_rules"].startswith("disabled")
    outbound = {r["name"]: r for r in plan["rules"] if r["direction"] == "Outbound"}
    assert outbound["ARCILOGGER Out NTP"]["remote_address"] == ["172.20.10.1", "10.0.0.9"]
    assert outbound["ARCILOGGER Out NTP"]["protocol"] == "UDP"
    assert outbound["ARCILOGGER Out DNS UDP"]["remote_address"] == ["172.20.20.53"]
    assert outbound["ARCILOGGER Out Kamu SM"]["ports"] == ["80"]
    assert outbound["ARCILOGGER Out Kamu SM"]["remote_address"] == ["10.0.0.1"]
    assert outbound["ARCILOGGER Out Kamu SM certificates"]["ports"] == ["80", "443"]
    assert outbound["ARCILOGGER Out SMTP"]["ports"] == ["587"]
    assert outbound["ARCILOGGER Out Replica"]["remote_address"] == ["10.0.0.4"]
    assert outbound["ARCILOGGER Out Replica"]["ports"] == ["445"]


def test_firewall_strict_outbound_follows_the_tsa_settings(engine: str, tmp_path: Path) -> None:
    text = (
        STRICT_CONFIG + '[tsa]\nprovider = "rfc3161"\nurl = "http://10.0.0.7:8080"\nport = 8080\n'
    )
    result, plan = _firewall(engine, tmp_path, text, "-StrictOutbound", *STRICT_ARGS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    kamusm = next(r for r in plan["rules"] if r["name"] == "ARCILOGGER Out Kamu SM")
    assert kamusm["remote_address"] == ["10.0.0.1", "10.0.0.7"]
    assert kamusm["ports"] == ["8080"]


SMTP_CONFIG = '[smtp]\nenabled = true\nhost = "10.0.0.8"\nport = 465\nsecurity = "ssl"\n'
SMTP_FREE_ARGS = STRICT_ARGS[: STRICT_ARGS.index("-SmtpServer")]


def _smtp_rules(plan: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    assert plan is not None
    return {r["name"]: r for r in plan["rules"] if r["name"].startswith("ARCILOGGER Out SMTP")}


def test_firewall_strict_outbound_follows_the_smtp_settings(engine: str, tmp_path: Path) -> None:
    """The alert e-mail server of [smtp] keeps its rule on every run (an upgrade passes no
    -SmtpServer): only when enabled = true and host is set; port default 587."""
    text = STRICT_CONFIG + SMTP_CONFIG
    result, plan = _firewall(engine, tmp_path, text, "-StrictOutbound", *SMTP_FREE_ARGS)
    assert result.returncode == 0, result.stdout + result.stderr
    smtp = _smtp_rules(plan)
    assert list(smtp) == ["ARCILOGGER Out SMTP"]
    assert smtp["ARCILOGGER Out SMTP"]["remote_address"] == ["10.0.0.8"]
    assert smtp["ARCILOGGER Out SMTP"]["ports"] == ["465"]
    assert smtp["ARCILOGGER Out SMTP"]["protocol"] == "TCP"
    # Another server named with -SmtpServer gets a rule of its own (never host A on port B).
    result, plan = _firewall(engine, tmp_path, text, "-StrictOutbound", *STRICT_ARGS)
    assert result.returncode == 0, result.stdout + result.stderr
    smtp = _smtp_rules(plan)
    assert [(r["remote_address"], r["ports"]) for r in smtp.values()] == [
        (["10.0.0.8"], ["465"]),
        (["10.0.0.3"], ["587"]),
    ]
    for config, expected in (
        ('[smtp]\nenabled = false\nhost = "10.0.0.8"\n', {}),
        ('[smtp]\nenabled = true\nhost = ""\n', {}),
        ('[smtp]\nenabled = true\nhost = "10.0.0.8"\n', {"ARCILOGGER Out SMTP": ["587"]}),
        ("", {}),  # no [smtp] section at all
    ):
        result, plan = _firewall(
            engine, tmp_path, STRICT_CONFIG + config, "-StrictOutbound", *SMTP_FREE_ARGS
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert {name: rule["ports"] for name, rule in _smtp_rules(plan).items()} == expected
    # Not in strict mode: no outbound rule at all.
    result, plan = _firewall(engine, tmp_path, text, *SMTP_FREE_ARGS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _smtp_rules(plan) == {}


def test_firewall_remembers_the_smtp_server(engine: str, tmp_path: Path) -> None:
    state = {
        "schema": 1, "strict_outbound": True, "smtp_server": "10.0.0.3:2525",
        "disabled_rules": [], "previous_profiles": [],
    }  # fmt: skip
    result, plan = _firewall(engine, tmp_path, STRICT_CONFIG, *SMTP_FREE_ARGS, state=state)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    assert plan["smtp_server"] == "10.0.0.3:2525"
    assert _smtp_rules(plan)["ARCILOGGER Out SMTP"]["ports"] == ["2525"]
    # A parameter given on the command line wins over the remembered value.
    result, plan = _firewall(engine, tmp_path, STRICT_CONFIG, *STRICT_ARGS, state=state)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _smtp_rules(plan)["ARCILOGGER Out SMTP"]["remote_address"] == ["10.0.0.3"]
    assert _smtp_rules(plan)["ARCILOGGER Out SMTP"]["ports"] == ["587"]
    invalid = ("-StrictOutbound", *SMTP_FREE_ARGS, "-SmtpServer", "10.0.0.3:99999")
    result, _ = _firewall(engine, tmp_path, STRICT_CONFIG, *invalid)
    assert result.returncode != 0
    assert "[invalid SMTP port]" in result.stdout + result.stderr


_FORGET_SMTP = (
    "param($Engine, $Script, $Config, $Python, $Plan, $State, [switch]$UseFile)\n"
    "# How Windows PowerShell 5.1 passes arguments to a program: an empty one is dropped.\n"
    "$PSNativeCommandArgumentPassing = 'Legacy'\n"
    "if ($UseFile) {\n"
    "    & $Engine -NoProfile -NonInteractive -ExecutionPolicy Bypass -File $Script "
    "-ConfigPath $Config -Python $Python -DryRun -PlanPath $Plan -StateFile $State EXTRA "
    "-SmtpServer ''\n"
    "}\n"
    "else {\n"
    "    $command = \"& '$Script' -ConfigPath '$Config' -Python '$Python' -DryRun \" +\n"
    "        \"-PlanPath '$Plan' -StateFile '$State' EXTRA -SmtpServer ''\"\n"
    "    & $Engine -NoProfile -NonInteractive -ExecutionPolicy Bypass -Command $command\n"
    "}\n"
    "exit $LASTEXITCODE\n"
)
FORGET_SMTP_COMMAND = (
    "powershell -ExecutionPolicy Bypass -Command \"& 'C:\\Program Files\\ARCILOGGER\\scripts\\"
    "Set-ArciFirewall.ps1' -SmtpServer ''\""
)


def test_firewall_forgets_the_smtp_server_with_the_documented_command(
    engine: str, tmp_path: Path
) -> None:
    """`-SmtpServer ''` only arrives through powershell.exe -Command: Windows PowerShell 5.1
    drops the empty argument after -File, and the remembered server would stay."""
    state = {
        "schema": 1, "strict_outbound": True, "smtp_server": "10.0.0.3:2525",
        "admin_address": [ADMIN_ADDRESS], "disabled_rules": [], "previous_profiles": [],
    }  # fmt: skip
    config = tmp_path / "arcilogger.toml"
    config.write_text(STRICT_CONFIG, encoding="utf-8")
    state_file = tmp_path / "firewall-state.json"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    # The DNS and Kamu SM addresses as IP addresses (no DNS lookups), as in the other tests.
    text = _FORGET_SMTP.replace("EXTRA", " ".join(SMTP_FREE_ARGS))
    wrapper = write_script(tmp_path / "forget.ps1", text)

    def forget(plan: Path, *mode: str) -> subprocess.CompletedProcess[str]:
        return run_powershell(
            engine, wrapper, "-Engine", engine,
            "-Script", str(WINDOWS / "scripts" / "Set-ArciFirewall.ps1"),
            "-Config", str(config), "-Python", sys.executable, "-Plan", str(plan),
            "-State", str(state_file), *mode,
        )  # fmt: skip

    plan = tmp_path / "command.json"
    result = forget(plan)
    assert result.returncode == 0, result.stdout + result.stderr
    forgotten = read_json(plan)
    assert forgotten["smtp_server"] is None
    assert list(_smtp_rules(forgotten)) == [], "no -SmtpServer rule left"
    # The -File form does not forget it: the script never gets the empty value.
    plan = tmp_path / "file.json"
    result = forget(plan, "-UseFile")
    assert not plan.is_file() or read_json(plan)["smtp_server"] == "10.0.0.3:2525"
    # The docs give the form that works, next to every mention of the empty value.
    for name in ("INSTALL.md", "KURULUM.md", "ALERTS.md", "UYARILAR.md"):
        doc = (REPO / "docs" / name).read_text(encoding="utf-8")
        assert FORGET_SMTP_COMMAND in doc, name
        for line in doc.splitlines():
            if "-SmtpServer ''" in line:
                assert "-Command" in line, (name, line)
    script = (WINDOWS / "scripts" / "Set-ArciFirewall.ps1").read_text(encoding="utf-8-sig")
    assert "-Command \"& '<path>\\Set-ArciFirewall.ps1' -SmtpServer ''\"" in script


def test_firewall_remembers_strict_outbound(engine: str, tmp_path: Path) -> None:
    """An upgrade runs the script without -StrictOutbound: strict mode must stay on."""
    state = {
        "schema": 1, "strict_outbound": True, "admin_address": [ADMIN_ADDRESS],
        "disabled_rules": [], "previous_profiles": [],
    }  # fmt: skip
    result, plan = _firewall(engine, tmp_path, STRICT_CONFIG, *STRICT_ARGS, state=state)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    assert plan["default_outbound"] == "Block"
    assert plan["strict_outbound_source"] == "state file"
    # Switching it off is explicit (splatted like the installer does).
    wrapper = write_script(
        tmp_path / "off.ps1",
        "param($Script, $Config, $Python, $Plan, $State)\n"
        "& $Script -ConfigPath $Config -Python $Python -DryRun -PlanPath $Plan -StateFile $State "
        "-StrictOutbound:$false\n",
    )
    off_plan = tmp_path / "off.json"
    result = run_powershell(
        engine,
        wrapper,
        "-Script",
        str(WINDOWS / "scripts" / "Set-ArciFirewall.ps1"),
        "-Config",
        str(tmp_path / "arcilogger.toml"),
        "-Python",
        sys.executable,
        "-Plan",
        str(off_plan),
        "-State",
        str(tmp_path / "firewall-state.json"),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    off = read_json(off_plan)
    assert off["default_outbound"] == "Allow"
    assert off["strict_outbound_source"] == "parameter"
    assert not [r for r in off["rules"] if r["direction"] == "Outbound"]


def test_firewall_remembers_admin_addresses_winrm_and_kept_rules(
    engine: str, tmp_path: Path
) -> None:
    state = {
        "schema": 1,
        "strict_outbound": False,
        "admin_address": ["172.20.10.0/28"],
        "allow_winrm": True,
        "keep_rules": ["Zabbix Agent (TCP-In)"],
        "disabled_rules": [],
        "previous_profiles": [],
    }
    result, plan = _firewall(engine, tmp_path, STRICT_CONFIG, state=state, admin_address=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    rules = {rule["name"]: rule for rule in plan["rules"]}
    assert plan["admin_address"] == ["172.20.10.0/28"]
    assert rules["ARCILOGGER RDP"]["remote_address"] == ["172.20.10.0/28"]
    assert rules["ARCILOGGER WinRM"]["remote_address"] == ["172.20.10.0/28"]
    assert plan["keep_rules"] == ["Zabbix Agent (TCP-In)"]
    # A parameter given on the command line wins over the remembered value.
    result, plan = _firewall(
        engine, tmp_path, STRICT_CONFIG, "-AdminAddress", "172.20.10.37", state=state
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    assert plan["admin_address"] == ["172.20.10.37"]


def test_firewall_reads_the_first_state_format(engine: str, tmp_path: Path) -> None:
    state = ["RemoteDesktop-UserMode-In-TCP"]  # a plain list of disabled inbound rules
    result, plan = _firewall(engine, tmp_path, STRICT_CONFIG, state=state)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    assert plan["strict_outbound"] is False


@pytest.mark.parametrize(
    "bad", ["172.20.10.300", "example.com", "10.0.0.0/33", "172.20.10", "127.1", "172.20.010.37"]
)
def test_firewall_rejects_invalid_addresses(engine: str, tmp_path: Path, bad: str) -> None:
    text = 'data_dir = "x"\n[[sources]]\nname = "a"\ntype = "generic"\naddress = "172.31.255.1"\n'
    result, _ = _firewall(engine, tmp_path, text, "-AdminAddress", bad)
    assert result.returncode != 0
    assert bad in result.stdout + result.stderr


def test_firewall_rejects_invalid_sources(engine: str, tmp_path: Path) -> None:
    text = 'data_dir = "x"\n[[sources]]\nname = "a"\ntype = "generic"\naddress = "172.31.255"\n'
    result, plan = _firewall(engine, tmp_path, text)
    assert result.returncode != 0
    assert plan is None, "nothing planned from an invalid sender"
    assert "172.31.255" in result.stdout + result.stderr


def test_firewall_needs_the_admin_pc_and_has_no_default(engine: str, tmp_path: Path) -> None:
    """D3: neither given nor remembered and nobody at the console: a stop that says what to give
    (placeholder examples); -Remove needs no admin address."""
    text = 'data_dir = "x"\n[[sources]]\nname = "a"\ntype = "generic"\naddress = "172.31.255.1"\n'
    result, plan = _firewall(engine, tmp_path, text, admin_address=None)
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert plan is None, "nothing planned without the admin PC"
    assert "[no admin PC address]" in output
    assert "-AdminAddress 10.0.0.10" in output
    assert "172.20." not in output, "no address of the example network as a default"
    result, plan = _firewall(engine, tmp_path, text, "-Remove", admin_address=None)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    assert plan["remove"] is True
    assert plan["rules"] == []


def test_firewall_allows_the_public_time_server_without_a_local_one(
    engine: str, tmp_path: Path
) -> None:
    """D2: no [clock] ntp_server (the default): the fallback, the only time source, stays
    reachable in strict mode."""
    text = 'data_dir = "x"\n[[sources]]\nname = "a"\ntype = "generic"\naddress = "172.31.255.1"\n'
    config = text + '[clock]\nfallback_server = "10.0.0.9"\n'
    result, plan = _firewall(engine, tmp_path, config, "-StrictOutbound", *STRICT_ARGS)
    assert result.returncode == 0, result.stdout + result.stderr
    assert plan is not None
    ntp = next(rule for rule in plan["rules"] if rule["name"] == "ARCILOGGER Out NTP")
    assert ntp["remote_address"] == ["10.0.0.9"]


# --- power settings on desktop Windows (Set-ArciPower.ps1) ---------------------------------------

POWER_SCRIPT = WINDOWS / "scripts" / "Set-ArciPower.ps1"


def _power(engine: str, tmp_path: Path, *args: str) -> Any:
    plan = tmp_path / "power-plan.json"
    state = tmp_path / "power-state.json"
    result = run_powershell(
        engine, POWER_SCRIPT, "-DryRun", "-PlanPath", str(plan), "-StateFile", str(state), *args
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return read_json(plan)


def test_power_plan_switches_sleep_hibernation_and_fast_startup_off(
    engine: str, tmp_path: Path
) -> None:
    plan = _power(engine, tmp_path)
    assert plan["remove"] is False
    assert plan["target"] == {
        "standby_timeout_ac": 0, "hibernate_timeout_ac": 0, "hibernate_enabled": 0,
        "hiberboot_enabled": 0, "network_adapter_power_saving": "Disabled",
    }  # fmt: skip
    assert not (tmp_path / "power-state.json").exists(), "a dry run records nothing"
    if sys.platform != "win32":
        assert plan["current"] is None, "nothing to read without powercfg"


def test_power_plan_keeps_the_values_recorded_first(engine: str, tmp_path: Path) -> None:
    """A later run (an upgrade) and -Remove use the values found before the first change."""
    previous = {
        "scheme": "381b4222-f694-41f0-9685-ff5bb260df2e", "standby_timeout_ac": 1800,
        "hibernate_timeout_ac": 10800, "hibernate_enabled": 1, "hiberboot_enabled": None,
        "adapters": [],
    }  # fmt: skip
    record = {"schema": 1, "previous": previous, "adapters_changed": []}
    (tmp_path / "power-state.json").write_text(json.dumps(record), encoding="utf-8")
    plan = _power(engine, tmp_path)
    assert plan["previous"] == previous
    plan = _power(engine, tmp_path, "-Remove")
    assert plan["remove"] is True
    assert plan["target"] is None
    assert plan["previous"] == previous


def _between(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def _function(text: str, name: str) -> str:
    return _between(text, f"function {name}", "\n}\n")


def test_power_script_records_first_and_restores_what_it_changed() -> None:
    text = POWER_SCRIPT.read_text(encoding="utf-8-sig")
    apply = text[text.index("# --- apply ---") :]
    # Recorded before the first change, so -Remove can always undo what follows.
    assert apply.index("Write-PowerState $state") < apply.index("'/setacvalueindex'")
    assert "[int]$os.ProductType -ne 1" in apply, "Windows Server: nothing changed"
    for change in (
        "@('STANDBYIDLE', 'HIBERNATEIDLE')",
        "'/setactive', $current.scheme",
        "'/hibernate', 'off'",
        "-Name 'HiberbootEnabled' -PropertyType DWord -Value 0",
        "-AllowComputerToTurnOffDevice Disabled -NoRestart",
    ):
        assert change in text, change
    assert "setdcvalueindex" not in text.lower(), "battery (DC) values stay as they are"
    restore = _between(text, "# --- remove ---", "# --- apply ---")
    for undo in (
        "'/setacvalueindex', $scheme, 'SUB_SLEEP', $pair[0]",
        "'/setactive', $scheme",
        "'/hibernate', 'on'",
        "Remove-ItemProperty -LiteralPath $SessionPowerKey -Name 'HiberbootEnabled'",
        "-AllowComputerToTurnOffDevice Enabled -NoRestart",
    ):
        assert undo in restore, undo
    # The record is deleted only once everything was restored.
    assert restore.index("throw (") < restore.index("Remove-Item -LiteralPath $StateFile")


def test_installer_changes_power_settings_only_on_desktop_windows() -> None:
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert "$script:IsClientOs = ($script:OsInfo.ProductType -eq 1)" in script
    tail = _function(script, "Invoke-CommonTail")
    guard = tail.index("if ($script:IsClientOs -and -not $SkipPower)")
    assert guard < tail.index("Invoke-Step 'power.configure'")
    assert "-StateFile $PowerStatePath" in tail
    steps = ("time.configure", "power.configure", "firewall.apply", "database.upgrade")
    positions = [tail.index(f"'{step}'") for step in steps]
    assert positions == sorted(positions), "before the migration, the last change"
    removal = _function(script, "Invoke-UninstallMode")
    restore = removal.index("Invoke-Step 'power.restore'")
    assert removal.index("if (Test-Path -LiteralPath $PowerStatePath)") < restore
    assert "Set-ArciPower.ps1') -Remove -StateFile $PowerStatePath" in removal


def test_installer_handles_token_and_kamusm_secrets_after_the_transcript() -> None:
    """Spec: no secret in a log, a file or the plan; the setup token only on the screen."""
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    stop = _function(script, "Stop-Installer")
    order = ("Save-Plan", "Stop-Transcript", "Complete-FirstInstall", "Wait-ForOperator", "exit")
    positions = [stop.index(step) for step in order]
    assert positions == sorted(positions), order
    assert "$script:FreshInstall -and $Code -le 1" in stop
    # The token and the Kamu SM question are reached only from there.
    assert script.count("Show-SetupToken") == 2
    assert script.count("Read-KamuSmCredentials") == 2
    finish = _function(script, "Complete-FirstInstall")
    quiet = finish.index("if (-not (Test-ArciInteractiveConsole)) { return }")
    assert quiet < finish.index("Show-SetupToken")
    kamusm = _function(script, "Read-KamuSmCredentials")
    assert "Kamu SM müşteri numarası ve parolasını şimdi girmek ister misiniz? [E/h]" in kamusm
    assert "Read-Host -AsSecureString" in kamusm
    names = f"@(@('{KAMUSM_CUSTOMER}', $customer), @('{KAMUSM_PASSWORD}', $password))"
    assert names in kamusm, "the secret names the core's Kamu SM client reads"
    assert '-StdinText ($pair[1] + "`n") -Redact @($customer, $password)' in kamusm
    token = _function(script, "Show-SetupToken")
    assert "@('setup-token', '--config', $ConfigPath)" in token
    # The indented token line goes past the host (no transcript), the API's other lines do not.
    assert "if ($line -match '^\\s+\\S') {" in token
    assert "[Console]::Out.WriteLine($line)" in token
    assert "Write-Host $result.StdOut" not in token
    assert "elseif ($result.ExitCode -eq 4) {" in token, "an administrator exists: no failure"
    for writer in ("Write-InstallState", "Write-ArciUtf8File", "Out-File", "Set-Content"):
        assert writer not in token + kamusm, writer
    wait = _function(script, "Wait-ForOperator")
    assert "if (-not $Auto -or -not (Test-ArciInteractiveConsole)) { return }" in wait
    assert "Kapatmak için Enter''a basın [press Enter to close]" in wait


def test_installer_remembers_the_server_address_and_name() -> None:
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    resolve = _function(script, "Resolve-ServerIdentity")
    order = (
        "if (-not $script:ServerAddressGiven)",
        "$script:ServerAddressSource = 'remembered'",
        "$script:ServerAddressSource = 'config'",
        "$script:ServerDetection = Select-ArciServerAddress",
    )
    positions = [resolve.index(step) for step in order]
    assert positions == sorted(positions), "parameter, state.json, [collector] bind, detection"
    assert "server_address = $ServerAddress; server_name = $ServerName" in script
    # No default pins an address of a network or a drive: the setup .exe passes only -Auto.
    params = script[: script.index("$ErrorActionPreference = 'Stop'")]
    assert "172.20." not in params
    assert "'D:\\ARCILOGGER'" not in params


@pytest.mark.parametrize(
    ("path", "names"),
    [
        (INSTALL_SCRIPT, ("SiteName", "NtpServer", "AdminAddress")),
        (WINDOWS / "scripts" / "Set-ArciFirewall.ps1", ("AdminAddress",)),
    ],
    ids=lambda value: value.name if isinstance(value, Path) else None,
)
def test_organisation_values_have_no_default(path: Path, names: tuple[str, ...]) -> None:
    """Product decisions D1-D3: the organisation name, its time source and its admin PC are
    asked for or given, never taken from a built-in value."""
    script = path.read_text(encoding="utf-8-sig")
    params = _between(script, "param(", "\n)\n")
    for name in names:
        declaration = re.search(rf"\[string(?:\[\])?\]\${name}\b([^,\n]*)", params)
        assert declaration is not None, name
        assert "=" not in declaration.group(1), (name, declaration.group(0))
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", params), "no address as a default"


def test_time_script_has_no_built_in_time_source() -> None:
    """Set-ArciTime.ps1: the local source comes from [clock] ntp_server (or -Primary); without
    one the public fallback is the only source, never an address of some network."""
    script = (WINDOWS / "scripts" / "Set-ArciTime.ps1").read_text(encoding="utf-8-sig")
    params = _between(script, "param(", "\n)\n")
    assert '[string]$Primary = ""' in params
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", params)
    assert "$peers = Get-ArciTimePeerList -Primary $Primary -Fallback $Fallback" in script
    assert script.index("Get-ArciTimePeerList") < script.index("Set-TimeZone"), "checked first"
    assert '/manualpeerlist:"$peers"' in script
    installer = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert "-Path 'clock.ntp_server' -Default '')" in installer
    firewall = (WINDOWS / "scripts" / "Set-ArciFirewall.ps1").read_text(encoding="utf-8-sig")
    assert "-Path 'clock.ntp_server' -Default '')" in firewall


def test_example_configuration_carries_no_organisation_values() -> None:
    """D5: the template of a new arcilogger.toml has no organisation name and no time source
    of its own; its devices are marked as the fictional example network's."""
    text = (WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8")
    example = tomllib.loads(text)
    assert example["site_name"] == ""
    assert example["clock"]["ntp_server"] == ""
    assert example["clock"]["fallback_server"] == "time.ume.tubitak.gov.tr"
    assert example["collector"]["bind"] == WILDCARD, "the installer writes the server address"
    assert "EXAMPLE (ÖRNEK)" in text
    # Its devices are commented out: a first installation configures no device of the example
    # network (they would be alerted as silent and take the parser of a real sender).
    assert "sources" not in example
    assert "#[[sources]]" in text
    assert tomllib.loads(with_example_sources(text))["sources"][0]["name"] == "fortigate"
    assert "<NAS>" in example["replica"]["target"], "a placeholder, not a host name"
    assert example["replica"]["enabled"] is False


def test_setup_and_tasks_name_no_organisation() -> None:
    """D6: the setup's publisher is the product; task descriptions name no host or address."""
    iss = (INSTALLER / "ARCILOGGER.iss").read_bytes()[len(BOM) :].decode("utf-8")
    assert re.search(r"(?m)^AppPublisher=ARCILOGGER\r?$", iss)
    for task in sorted((WINDOWS / "tasks").glob("*.xml")):
        description = re.search(r"<Description>(.*)</Description>", task.read_text("utf-8"))
        assert description is not None, task.name
        assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", description.group(1)), task.name


def test_new_configuration_gets_the_server_address_as_collector_bind() -> None:
    """The bind line of the example's [collector] is the one the installer rewrites."""
    text = (WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8")
    example = tomllib.loads(text)
    assert "bind" in example["collector"]
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    copy = _between(script, "Invoke-Step 'config.copy'", "\n    }\n")
    assert "Set-ExampleCollectorBind -Text $text -Address $bindValue" in copy
    assert "$bindValue = [string]$ServerAddress" in script


_BIND_REWRITE_SCRIPT = r"""
param([string]$ModulePath, [string]$InputPath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
{function}
# Assigned first: Windows PowerShell 5.1 passes a JSON array on as one object.
$texts = [System.IO.File]::ReadAllText($InputPath) | ConvertFrom-Json
$results = @(foreach ($text in $texts) {
        try {
            $new = Set-ExampleCollectorBind -Text $text -Address '192.0.2.9'
            [ordered]@{ text = $new; error = $null }
        }
        catch { [ordered]@{ text = $null; error = $_.Exception.Message } }
    })
[System.IO.File]::WriteAllText($OutputPath, (ConvertTo-Json -InputObject $results -Depth 3))
"""


def test_installer_rewrites_only_the_collector_bind(engine: str, tmp_path: Path) -> None:
    """Another section's bind key (a later version may add one) is left alone."""
    function = _function(INSTALL_SCRIPT.read_text(encoding="utf-8-sig"), "Set-ExampleCollectorBind")
    example = (WINDOWS / "arcilogger.example.toml").read_text(encoding="utf-8")
    texts = [
        example,
        '[api]\nbind = "198.51.100.1"\n\n[collector]\nbind = "0.0.0.0"\nudp_port = 514\n\n'
        '[seal]\nbind = "198.51.100.2"\n',
        '[api]\nbind = "198.51.100.1"\n\n[collector]\nudp_port = 514\n',
        '[collector]\nbind = "0.0.0.0"\nbind = "0.0.0.0"\n',
    ]
    path = tmp_path / "bind-in.json"
    path.write_text(json.dumps(texts), encoding="utf-8")
    output = tmp_path / "bind-out.json"
    script = write_script(
        tmp_path / "bind.ps1", _BIND_REWRITE_SCRIPT.replace("{function}", function + "\n}")
    )
    arguments = ["-ModulePath", str(MODULE), "-InputPath", str(path), "-OutputPath", str(output)]
    result = run_powershell(engine, script, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    rewritten, sections, missing, twice = read_json(output)
    expected = tomllib.loads(example)
    expected["collector"]["bind"] = "192.0.2.9"
    assert tomllib.loads(rewritten["text"]) == expected, "only [collector] bind changes"
    assert tomllib.loads(sections["text"]) == {
        "api": {"bind": "198.51.100.1"},
        "collector": {"bind": "192.0.2.9", "udp_port": 514},
        "seal": {"bind": "198.51.100.2"},
    }
    for case in (missing, twice):
        assert case["text"] is None
        assert "[expected one bind line in [collector]]" in case["error"]


_SETUP_REWRITE_SCRIPT = r"""
param([string]$ModulePath, [string]$InputPath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
{function}
$text = [System.IO.File]::ReadAllText($InputPath, [System.Text.Encoding]::UTF8)
$results = [ordered]@{
    both = (Set-ConfigSetupValues -Text $text -SiteName 'Örnek "Kalite" A.Ş.' -NtpServer '10.0.0.1')
    none = (Set-ConfigSetupValues -Text $text)
    empty = (Set-ConfigSetupValues -Text $text -SiteName 'Kurum' -NtpServer '')
}
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
[System.IO.File]::WriteAllText($OutputPath, ($results | ConvertTo-Json), $utf8)
"""


def test_new_configuration_gets_the_organisation_values(engine: str, tmp_path: Path) -> None:
    """D1, D2: config.copy writes site_name and [clock] ntp_server into the example's text,
    nothing else changes; the step gets them from -SiteName / -NtpServer or the console."""
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    copy = _between(script, "Invoke-Step 'config.copy'", "\n    }\n")
    assert "Set-ConfigSetupValues -Text $text -SiteName $siteValue -NtpServer $ntpValue" in copy
    assert "$siteValue = $script:SiteNameValue" in script
    function = _function(script, "Set-ConfigSetupValues")
    path = tmp_path / "example.toml"
    path.write_bytes(EXAMPLE_TEXT.encode("utf-8"))  # as in the bundle: no newline translation
    output = tmp_path / "rewritten.json"
    text = _SETUP_REWRITE_SCRIPT.replace("{function}", function + "\n}")
    rewrite = write_script(tmp_path / "rewrite.ps1", text)
    arguments = ["-ModulePath", str(MODULE), "-InputPath", str(path), "-OutputPath", str(output)]
    result = run_powershell(engine, rewrite, *arguments)
    assert result.returncode == 0, result.stdout + result.stderr
    rewritten = read_json(output)
    example = tomllib.loads(EXAMPLE_TEXT)
    both = tomllib.loads(rewritten["both"])
    assert both == {
        **example,
        "site_name": 'Örnek "Kalite" A.Ş.',
        "clock": {**example["clock"], "ntp_server": "10.0.0.1"},
    }
    assert rewritten["none"] == EXAMPLE_TEXT, "nothing given, nothing changed"
    assert tomllib.loads(rewritten["empty"]) == {**example, "site_name": "Kurum"}


def test_messages_name_the_real_data_folder() -> None:
    """A desktop has no D: drive: no message may send the operator there."""
    iss = (INSTALLER / "ARCILOGGER.iss").read_bytes()[len(BOM) :].decode("utf-8")
    assert "D:\\ARCILOGGER" not in iss
    for language in ("turkish", "english"):
        message = re.search(rf"(?m)^{language}\.InstallerFailed=(.+)$", iss)
        assert message is not None
        assert "%%TEMP%%\\ARCILOGGER-*.log" in message.group(1), "the log before data exists"
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert "veri klasörü D:\\ARCILOGGER\\db" not in script
    assert "(Join-Path $DataRoot 'db')" in script
    assert "Kurulum günlüğü [install log]: {0}" in _function(script, "Stop-Installer")


def test_auditpol_fallback_works_on_every_edition() -> None:
    """Windows 11 Home has no Local Security Policy: the message gives the command itself."""
    text = (WINDOWS / "scripts" / "Set-ArciVaultAcl.ps1").read_text(encoding="utf-8-sig")
    fallback = _between(text, "if ($LASTEXITCODE -ne 0) {", "\n    }\n")
    assert "auditpol /set /subcategory:" in fallback
    assert "/success:enable /failure:enable" in fallback
    assert "Home" in fallback
    assert "Yerel Güvenlik İlkesi ile açın" not in fallback


_RENAME_SCRIPT = r"""
param([string]$ModulePath, [string]$Root, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
# A virus scanner holding a file open: the first renames are refused, as Windows does.
$global:ArciRefusals = 0
function global:Rename-Item {
    param([string]$LiteralPath, [string]$NewName, $ErrorAction)
    if ($global:ArciRefusals -gt 0) {
        $global:ArciRefusals--
        $text = "Access to the path '$LiteralPath' is denied."
        throw [System.UnauthorizedAccessException]::new($text)
    }
    Microsoft.PowerShell.Management\Rename-Item -LiteralPath $LiteralPath -NewName $NewName `
        -ErrorAction Stop
}
$out = [ordered]@{}

[void](New-Item -ItemType Directory -Path (Join-Path $Root 'a'))
$global:ArciRefusals = 2
Rename-ArciFolder -LiteralPath (Join-Path $Root 'a') -NewName 'b' -DelayMilliseconds 10
$out['retried'] = [ordered]@{
    renamed = (Test-Path -LiteralPath (Join-Path $Root 'b')); refusals_left = $global:ArciRefusals
}

$global:ArciRefusals = 1000
try {
    Rename-ArciFolder -LiteralPath (Join-Path $Root 'b') -NewName 'c' -TimeoutSeconds 0 `
        -DelayMilliseconds 10
    $out['persistent'] = 'renamed'
}
catch { $out['persistent'] = $_.Exception.GetType().Name }
$out['persistent_kept'] = (Test-Path -LiteralPath (Join-Path $Root 'b'))

$global:ArciRefusals = 0
$started = [DateTime]::UtcNow
try {
    Rename-ArciFolder -LiteralPath (Join-Path $Root 'missing') -NewName 'd' -TimeoutSeconds 30
    $out['missing'] = 'renamed'
}
catch { $out['missing'] = 'failed' }
$out['missing_seconds'] = ([DateTime]::UtcNow - $started).TotalSeconds

$out | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $OutputPath -Encoding UTF8
"""


def test_a_folder_in_use_is_renamed_once_windows_lets_go(engine: str, tmp_path: Path) -> None:
    """A broken upgrade failed to put the previous version back: right after the failed run a
    file in the new program folder was still open, and Windows refused the rename. The installer
    now retries every folder swap for a while; a missing folder still fails at once."""
    root = tmp_path / "folders"
    root.mkdir()
    output = tmp_path / "rename-out.json"
    script = write_script(tmp_path / "rename.ps1", _RENAME_SCRIPT)
    result = run_powershell(
        engine, script, "-ModulePath", str(MODULE), "-Root", str(root), "-OutputPath", str(output)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = read_json(output)
    assert report["retried"] == {"renamed": True, "refusals_left": 0}
    assert "Klasör kullanımda, bekleniyor [folder in use, waiting]" in result.stdout
    assert report["persistent"] == "UnauthorizedAccessException"
    assert report["persistent_kept"] is True
    assert report["missing"] == "failed"
    assert report["missing_seconds"] < 5


def test_the_installer_swaps_program_folders_with_retries() -> None:
    script = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    assert "Rename-Item" not in script
    assert script.count("Rename-ArciFolder -LiteralPath") == 6


# --- browser administration (ADR-0014; contract 2.2, 2.4.1, 2.8.3, 2.16) ------------------------

AGENT = service_sid("ArciAdmin")
CREATOR_OWNER = "S-1-3-0"
LIST_ONLY = "ListDirectory, ReadAttributes, Traverse"


def _plan_by_path(document: dict[str, Any], root: Path) -> dict[str, dict[str, Any]]:
    """The ACL plan's entries by their path below ``root`` (forward slashes), in plan order."""
    return {
        Path(entry["Path"].replace("\\", "/")).relative_to(root).as_posix(): entry
        for entry in document["entries"]
    }


def _scope(entry: dict[str, Any], ace: dict[str, Any]) -> str:
    if entry["Kind"] == "File":
        return "file"
    if not ace["Inherit"]:
        return "this folder"
    parts = ["folders" if ace["FoldersOnly"] else "files" if ace["FilesOnly"] else "all"]
    parts += ["inherit-only"] * ace["InheritOnly"] + ["one level"] * ace["OneLevel"]
    return " ".join(parts)


def _grants(entry: dict[str, Any], sid: str) -> set[tuple[str, str]]:
    return {(ace["Rights"], _scope(entry, ace)) for ace in entry["Aces"] if ace["Sid"] == sid}


def _granted(entries: dict[str, dict[str, Any]], sid: str) -> dict[str, set[tuple[str, str]]]:
    return {path: grants for path, e in entries.items() if (grants := _grants(e, sid))}


WRITE_WORDS = ("FullControl", "Modify", "Write", "WriteData", "CreateFiles", "AppendData", "Delete")


def _writes(entries: dict[str, dict[str, Any]], sid: str) -> set[str]:
    return {
        path
        for path, entry in entries.items()
        for rights, _ in _grants(entry, sid)
        if any(word in rights for word in WRITE_WORDS)
    }


def test_acl_plan_gives_the_administration_agent_exactly_its_rights(
    engine: str, tmp_path: Path
) -> None:
    """Every row of contract 2.2 for ArciAdmin (NT SERVICE\\ArciAdmin) and for CREATOR OWNER,
    and nothing else: the agent writes the configuration and the secret store through explicit
    file entries, may only add files where an administrator keeps files (kamusm-roots, smtp-ca,
    the configuration folder itself) and has no right on the spool, the vault, the exports, the
    backups or the evidence-job state."""
    entries = _plan_by_path(_acl_plan(engine, tmp_path), tmp_path)
    paths = list(entries)
    for i, path in enumerate(paths):
        assert not any(path.startswith(child + "/") for child in paths[i + 1 :]), path
    read = "ReadAndExecute"
    assert _granted(entries, AGENT) == {
        "program": {(read, "all")},
        "programdata": {(read, "all"), ("CreateFiles", "this folder")},
        "programdata/arcilogger.toml": {("Modify", "file")},
        "programdata/secrets.json": {("Modify", "file")},
        "programdata/data-root.txt": {("Read", "file")},
        "programdata/secret.key": {("Read", "file")},
        "programdata/kamusm-roots": {("CreateFiles", "this folder")},
        "programdata/smtp-ca": {("CreateFiles", "this folder")},
        "programdata/zamane/web": {("Modify", "all")},
        "programdata/zamane/approved": {(read, "all")},
        "programdata/install": {(read, "all")},
        "programdata/admin": {(LIST_ONLY, "this folder")},
        "programdata/admin/policy.json": {("Read", "file")},
        "programdata/admin/keys/agent-ed25519.key": {("Read", "file")},
        "programdata/admin/keys/keys.json": {("Read", "file")},
        "programdata/admin/inbox": {("Modify", "all")},
        "programdata/admin/work": {("Modify", "all")},
        "programdata/admin/pending": {("Modify", "all")},
        "programdata/admin/state": {("Modify", "all")},
        "programdata/admin/versions": {("Modify", "all")},
        "programdata/admin/status": {("Modify", "all")},
        "programdata/admin/journal": {("Modify", "all")},
        "programdata/admin/system": {(read, "all")},
        "data": {(read, "this folder")},
        "data/state": {(read, "all")},
        "data/state/requests": {("ReadAndExecute, CreateFiles, WriteData", "all")},
        "data/logs": {("Modify", "all")},
    }
    # Whoever created a file may delete it, nothing else: the agent's temporary files of an
    # apply (files of the configuration folder only), its added roots and CA files, the API's
    # own inbox files and the agent's task requests.
    assert _granted(entries, CREATOR_OWNER) == {
        "programdata": {("Delete", "files inherit-only one level")},
        "programdata/kamusm-roots": {("Delete", "files inherit-only")},
        "programdata/smtp-ca": {("Delete", "files inherit-only")},
        "programdata/admin/inbox": {("Delete", "all inherit-only")},
        "data/state/requests": {("Delete", "all inherit-only")},
    }
    for protected in (
        "programdata/install", "programdata/admin", "programdata/admin/keys",
        "programdata/admin/keys/agent-ed25519.key", "programdata/admin/keys/api-ed25519.key",
        "programdata/admin/keys/keys.json", "programdata/zamane/web", "programdata/zamane/approved",
        "data/state/requests", "programdata/data-root.txt",
    ):  # fmt: skip
        assert entries[protected]["Protect"] is True, protected
        assert _rights(entries[protected], WELL_KNOWN["system"]) == {"FullControl"}, protected
        assert _rights(entries[protected], WELL_KNOWN["admins"]) == {"FullControl"}, protected
    # The explicit file entries sit on top of the folder's inherited read rights.
    for unprotected in (
        "programdata/arcilogger.toml", "programdata/secrets.json", "programdata/admin/policy.json",
        "programdata/kamusm-roots", "programdata/smtp-ca", "programdata/admin/inbox",
    ):  # fmt: skip
        assert entries[unprotected]["Protect"] is False, unprotected
    assert "programdata/zamane" not in entries, "zamane\\ keeps the configuration folder's read"
    # The data root record (Day 0 note D0-2): every service and task reads it, nobody but the
    # owners writes it (ArciAdmin may create files in the folder, so the installer re-creates it).
    record = entries["programdata/data-root.txt"]
    readers = {
        service_sid(name) for name in ("ArciCollector", "ArciIngest", "ArciApi", "ArciWorker")
    } | {AGENT, WELL_KNOWN["local_service"]}
    owners = {WELL_KNOWN["system"], WELL_KNOWN["admins"]}
    assert {ace["Sid"] for ace in record["Aces"]} - owners == readers
    assert all(ace["Rights"] == "Read" for ace in record["Aces"] if ace["Sid"] in readers)
    # The worker reads the guard rails: whether the agent's heartbeat is expected (WP3-3).
    assert _grants(entries["programdata/admin/policy.json"], service_sid("ArciWorker")) == {
        ("Read", "file")
    }


def test_acl_plan_keeps_the_api_and_the_tasks_out_of_the_configuration(
    engine: str, tmp_path: Path
) -> None:
    """ArciApi writes the inbox and nothing of the configuration, secret.key, secrets.json or
    zamane\\; each private key is readable by its one service; LOCAL SERVICE reads the public
    keys, the policy and jars.json and works in state\\requests, never in the staging folder."""
    entries = _plan_by_path(_acl_plan(engine, tmp_path), tmp_path)
    api, worker, tasks = (
        service_sid("ArciApi"),
        service_sid("ArciWorker"),
        WELL_KNOWN["local_service"],
    )
    assert _writes(entries, api) == {
        "programdata/admin/inbox", "data/spool/_audit", "data/exports", "data/logs",
    }  # fmt: skip
    assert _grants(entries["programdata/admin/inbox"], api) == {
        ("CreateFiles, WriteData, ReadData, ReadAttributes, ListDirectory", "all")
    }
    for path, readers in (
        ("programdata/admin/keys/agent-ed25519.key", {AGENT}),
        ("programdata/admin/keys/api-ed25519.key", {api}),
        ("programdata/admin/keys/keys.json", {AGENT, api, tasks}),
        ("programdata/admin/keys", set()),
        ("programdata/admin/policy.json", {AGENT, api, worker, tasks}),
    ):
        owners = {WELL_KNOWN["system"], WELL_KNOWN["admins"]}
        assert {ace["Sid"] for ace in entries[path]["Aces"]} - owners == readers, path
    assert _granted(entries, api).keys() >= {
        "programdata/admin", "programdata/admin/status", "programdata/admin/journal",
        "programdata/admin/system", "programdata/zamane/web", "programdata/zamane/approved",
    }  # fmt: skip
    for hidden in ("work", "pending", "state", "versions"):
        assert not _grants(entries[f"programdata/admin/{hidden}"], api), hidden
    assert not _grants(entries["data/state/requests"], api)
    assert not _grants(entries["programdata/install"], api)
    assert set(_granted(entries, worker)) >= {
        "programdata/admin", "programdata/admin/status", "programdata/admin/journal",
        "programdata/admin/system",
    }  # fmt: skip
    assert _grants(entries["data/state/requests"], tasks) == {("Modify", "all")}
    assert _grants(entries["programdata/admin/system"], tasks) == {("ReadAndExecute", "all")}
    assert _grants(entries["programdata/zamane/approved"], tasks) == {("ReadAndExecute", "all")}
    staging = {ace["Sid"] for ace in entries["programdata/zamane/web"]["Aces"]}
    assert staging == {WELL_KNOWN["system"], WELL_KNOWN["admins"], AGENT, api}, "no task right"
    for path in ("programdata/install", "programdata/zamane/web", "programdata/admin/keys"):
        assert not _grants(entries[path], tasks), path
    for sid in (api, worker, tasks):
        assert _grants(entries["programdata/admin"], sid) == {(LIST_ONLY, "this folder")}


_ACE_FLAGS_SCRIPT = r"""
param([string]$ModulePath, [string]$Root, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force
$plan = Get-ArciAclPlan -DataRoot (Join-Path $Root 'data') `
    -ProgramDataDir (Join-Path $Root 'programdata') -InstallDir (Join-Path $Root 'program')
$out = @(foreach ($entry in $plan) {
        foreach ($ace in $entry.Aces) {
            $flags = Get-ArciAceFlags -Kind $entry.Kind -Ace $ace
            [pscustomobject]@{
                path = $entry.Path; sid = $ace.Sid; rights = $ace.Rights
                inheritance = $flags.Inheritance; propagation = $flags.Propagation
            }
        }
    })
[System.IO.File]::WriteAllText($OutputPath, (ConvertTo-Json -InputObject $out -Depth 4))
"""


def test_acl_entries_become_the_windows_inheritance_flags(engine: str, tmp_path: Path) -> None:
    """What Set-ArciAclEntry writes (Get-ArciAceFlags): CREATOR OWNER's Delete reaches the files
    of the configuration folder only (ObjectInherit, InheritOnly, NoPropagateInherit: without
    the last one it would reach the files of every sub-folder, secret.key's folder included),
    a file entry inherits nothing, the worker's list right reaches folders only."""
    output = tmp_path / "flags.json"
    script = write_script(tmp_path / "flags.ps1", _ACE_FLAGS_SCRIPT)
    result = run_powershell(
        engine,
        script,
        "-ModulePath",
        str(MODULE),
        "-Root",
        str(tmp_path),
        "-OutputPath",
        str(output),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    flags = {
        (
            Path(item["path"].replace("\\", "/")).relative_to(tmp_path).as_posix(),
            item["sid"],
            item["rights"],
        ): (
            item["inheritance"],
            item["propagation"],
        )
        for item in read_json(output)
    }
    assert flags[("programdata", CREATOR_OWNER, "Delete")] == (
        "ObjectInherit",
        "InheritOnly, NoPropagateInherit",
    )
    assert flags[("programdata/kamusm-roots", CREATOR_OWNER, "Delete")] == (
        "ObjectInherit",
        "InheritOnly",
    )
    assert flags[("programdata/admin/inbox", CREATOR_OWNER, "Delete")] == (
        "ContainerInherit, ObjectInherit",
        "InheritOnly",
    )
    assert flags[("programdata", AGENT, "CreateFiles")] == ("None", "None")
    assert flags[("programdata/arcilogger.toml", AGENT, "Modify")] == ("None", "None")
    assert flags[("data/spool", service_sid("ArciWorker"), LIST_ONLY)] == (
        "ContainerInherit",
        "None",
    )
    assert flags[("programdata/admin/inbox", AGENT, "Modify")] == (
        "ContainerInherit, ObjectInherit",
        "None",
    )


_POLICY_SCRIPT = r"""
param([string]$ModulePath, [string]$Folder, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force
$java = 'D:\Tools\java\bin\java.exe'
$trusted = @(
    [pscustomobject]@{ path = $java; sha256 = ('A' * 64); argv = @($java, '-Xmx256m') },
    [pscustomobject]@{ path = 'E:\pg\bin\pg_dump.exe'; sha256 = ('b' * 64); argv = $null }
)
Write-ArciUtf8File -Path (Join-Path $Folder 'default.json') -Text (New-ArciAdminPolicy)
$custom = New-ArciAdminPolicy -Enabled $false -SourceNetwork @('172.20.0.0/16', '10.0.0.1') `
    -HoldHours 2 -JarHoldHours 48 -TrustedExecutable $trusted -WrittenAt '2026-10-25T10:00:00+03:00'
Write-ArciUtf8File -Path (Join-Path $Folder 'custom.json') -Text $custom
$refused = $null
try { [void](New-ArciAdminPolicy -SourceNetwork @('172.20.0.1/16')) }
catch { $refused = $_.Exception.Message }
$read = [ordered]@{}
foreach ($file in Get-ChildItem -LiteralPath $Folder -Filter '*.json' | Sort-Object Name) {
    $policy = Read-ArciAdminPolicy -Path $file.FullName
    $read[$file.Name] = [ordered]@{
        valid = $policy.Valid; enabled = $policy.Enabled; problem = $policy.Problem
        sha256 = $policy.Sha256; source_networks = @($policy.SourceNetworks)
        hold_hours = $policy.HoldHours; jar_hold_hours = $policy.JarHoldHours
        executable_roots = @($policy.ExecutableRoots); trusted = @($policy.TrustedExecutables).Count
    }
}
$missing = Read-ArciAdminPolicy -Path (Join-Path $Folder 'none.json')
$report = [ordered]@{
    refused = $refused; read = $read
    missing = [ordered]@{
        exists = $missing.Exists; valid = $missing.Valid; enabled = $missing.Enabled
    }
}
[System.IO.File]::WriteAllText($OutputPath, ($report | ConvertTo-Json -Depth 5))
"""


def _policy_document(**changes: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "schema": "arcilogger.admin-policy/1", "enabled": True,
        "source_networks": ["172.16.0.0/12"], "min_prefix_v4": 16, "max_sources": 500,
        "hold_hours": 0, "jar_hold_hours": 24, "executable_roots": ["C:\\Program Files"],
        "trusted_executables": [], "tsa_test_per_day": 3,
        "written_at": "2026-10-25T10:00:00+03:00", "written_by": "installer",
    }  # fmt: skip
    for key, value in changes.items():
        if value is None:
            document.pop(key)
        else:
            document[key] = value
    return document


#: policy.json files the installer's reader must judge as arcilogger_core.admin.contract does.
POLICY_SAMPLES: dict[str, bytes] = {
    "ok-hand-edited.json": json.dumps(_policy_document(max_sources=50)).encode(),
    "extra-key.json": json.dumps(_policy_document(comment="x")).encode(),
    "float.json": json.dumps(_policy_document())
    .replace('"hold_hours": 0', '"hold_hours": 1.5')
    .encode(),
    "bom.json": BOM + json.dumps(_policy_document()).encode(),
    "range.json": json.dumps(_policy_document(min_prefix_v4=40)).encode(),
    "host-bits.json": json.dumps(_policy_document(source_networks=["172.16.0.1/12"])).encode(),
    "no-writer.json": json.dumps(_policy_document(written_by=None)).encode(),
    "schema.json": json.dumps(_policy_document(schema="arcilogger.admin-policy/2")).encode(),
    "no-schema.json": json.dumps(_policy_document(schema=None)).encode(),
    "bad-sha.json": json.dumps(
        _policy_document(trusted_executables=[{"path": "D:\\java.exe", "sha256": "abc"}])
    ).encode(),
    "string-bool.json": json.dumps(_policy_document(enabled="true")).encode(),
    "jar-hold-zero.json": json.dumps(_policy_document(jar_hold_hours=0)).encode(),
    "naive-time.json": json.dumps(_policy_document(written_at="2026-10-25T10:00:00")).encode(),
    "not-json.json": b"{enabled: true",
}


def test_admin_policy_is_written_and_read_as_the_core_reads_it(engine: str, tmp_path: Path) -> None:
    """New-ArciAdminPolicy writes what arcilogger_core.admin.contract.AdminPolicy accepts
    strictly (contract 2.4.1); Read-ArciAdminPolicy (ApplySystem, the installer) judges every
    file as the core does, and a missing or invalid file means browser administration off."""
    folder = tmp_path / "policies"
    folder.mkdir()
    for name, data in POLICY_SAMPLES.items():
        (folder / name).write_bytes(data)
    output = tmp_path / "policy-report.json"
    script = write_script(tmp_path / "policy.ps1", _POLICY_SCRIPT)
    result = run_powershell(
        engine,
        script,
        "-ModulePath",
        str(MODULE),
        "-Folder",
        str(folder),
        "-OutputPath",
        str(output),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output)
    default = parse_document(AdminPolicy, (folder / "default.json").read_bytes())
    assert default.enabled is True
    assert default.source_networks == ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    assert (default.hold_hours, default.jar_hold_hours, default.min_prefix_v4) == (0, 24, 16)
    assert default.executable_roots == ("C:\\Program Files", "C:\\Program Files (x86)")
    assert default.trusted_executables == ()
    assert default.written_by == "installer"
    assert default.written_at.utcoffset() is not None
    custom = parse_document(AdminPolicy, (folder / "custom.json").read_bytes())
    assert custom.enabled is False
    assert custom.source_networks == ("172.20.0.0/16", "10.0.0.1/32")
    assert (custom.hold_hours, custom.jar_hold_hours) == (2, 48)
    java, dump = custom.trusted_executables
    assert java.sha256 == "a" * 64, "lower case, as the core compares it"
    assert java.argv == ("D:\\Tools\\java\\bin\\java.exe", "-Xmx256m")
    assert dump.argv is None
    assert "-SourceNetwork '172.20.0.1/16'" in report["refused"]
    for name in ["default.json", "custom.json", *POLICY_SAMPLES]:
        data = (folder / name).read_bytes()
        try:
            parse_document(AdminPolicy, data)
            valid = True
        except DocumentError:
            valid = False
        seen = report["read"][name]
        assert seen["valid"] is valid, (name, seen["problem"])
        assert seen["sha256"] == sha256_bytes(data), name
        if not valid:
            assert seen["enabled"] is False, "an invalid file switches the administration off"
            assert seen["source_networks"] == ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"]
    assert report["read"]["custom.json"]["enabled"] is False
    assert report["read"]["custom.json"]["trusted"] == 2
    assert report["read"]["ok-hand-edited.json"]["valid"] is True
    assert report["missing"] == {"exists": False, "valid": False, "enabled": False}


_EVENT_SOURCE_SCRIPT = r"""
param([string]$ModulePath, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force
$calls = New-Object System.Collections.Generic.List[string]
function New-FakeEventLog([bool]$Exists, [string]$Log) {
    @{
        Exists = { param($Source) $calls.Add("exists $Source"); $Exists }.GetNewClosure()
        LogOf  = { param($Source) $Log }.GetNewClosure()
        Create = { param($Source, $LogName) $calls.Add("create $Source $LogName") }
        Delete = { param($Source) $calls.Add("delete $Source") }
        Write  = { param($Source, $Message, $Level, $Id) $calls.Add("write $Source $Id $Level") }
    }
}
$results = [ordered]@{}
$results['new'] = Register-ArciEventSource -EventLog (New-FakeEventLog $false $null)
$results['present'] = Register-ArciEventSource -EventLog (New-FakeEventLog $true 'Application')
$results['moved'] = Register-ArciEventSource -EventLog (New-FakeEventLog $true 'System')
$results['removed'] = Unregister-ArciEventSource -EventLog (New-FakeEventLog $true 'Application')
$results['absent'] = Unregister-ArciEventSource -EventLog (New-FakeEventLog $false $null)
$present = New-FakeEventLog $true 'Application'
$results['written'] = Write-ArciEvent -Id 4106 -Level 'Error' -Message 'x' -EventLog $present
$broken = New-FakeEventLog $false $null
$broken['Write'] = { param($Source, $Message, $Level, $Id) throw 'source not registered' }
$results['not written'] = Write-ArciEvent -Id 4106 -Message 'x' -EventLog $broken
$report = [ordered]@{ results = $results; calls = $calls.ToArray() }
[System.IO.File]::WriteAllText($OutputPath, ($report | ConvertTo-Json -Depth 4))
"""


def test_event_source_is_registered_and_removed_idempotently(engine: str, tmp_path: Path) -> None:
    """Register-ArciEventSource / Unregister-ArciEventSource (contract 2.16) against a fake
    System.Diagnostics.EventLog: ARCILOGGER-Admin for the Application log, created once, moved
    from another log, removed by -Uninstall; Write-ArciEvent never fails its caller."""
    output = tmp_path / "events.json"
    script = write_script(tmp_path / "events.ps1", _EVENT_SOURCE_SCRIPT)
    result = run_powershell(engine, script, "-ModulePath", str(MODULE), "-OutputPath", str(output))
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output)
    assert report["results"] == {
        "new": "created", "present": "present", "moved": "moved", "removed": "removed",
        "absent": "absent", "written": True, "not written": False,
    }  # fmt: skip
    assert report["calls"] == [
        "exists ARCILOGGER-Admin", "create ARCILOGGER-Admin Application",
        "exists ARCILOGGER-Admin",
        "exists ARCILOGGER-Admin", "delete ARCILOGGER-Admin", "create ARCILOGGER-Admin Application",
        "exists ARCILOGGER-Admin", "delete ARCILOGGER-Admin",
        "exists ARCILOGGER-Admin",
        "write ARCILOGGER-Admin 4106 Error",
    ]  # fmt: skip
    module = MODULE.read_text(encoding="utf-8-sig")
    assert "[System.Diagnostics.EventLog]::CreateEventSource($Source, $LogName)" in module
    assert "$script:ArciEventSource = 'ARCILOGGER-Admin'" in module


_ADMIN_HELPERS_SCRIPT = r"""
param([string]$ModulePath, [string]$Root, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
$out = [ordered]@{}
# -InheritOnly: the entry is for what is created below only; -Files: files inherit it (OI).
function Rule(
    [string]$Sid, [int64]$Rights, [bool]$Allow = $true, [switch]$InheritOnly, [switch]$Files
) {
    [pscustomobject]@{
        Sid = $Sid; Rights = $Rights; Allow = $Allow; InheritOnly = [bool]$InheritOnly
        ObjectInherit = [bool]$Files
    }
}
function Fact([string]$Path, [string]$Owner, [object[]]$Rules) {
    [pscustomobject]@{ Path = $Path; Owner = $Owner; Rules = @($Rules) }
}
$users = 'S-1-5-32-545'
$authenticated = 'S-1-5-11'
$facts = @(
    (Fact 'D:\Tools\java\bin\java.exe' 'S-1-5-32-544' @(
            (Rule $users 0x1200a9), (Rule 'S-1-3-0' 0x1f01ff))),
    (Fact 'D:\Tools\java\bin' 'S-1-5-18' @(
            (Rule $users 0x1301bf $false),
            (Rule $authenticated 0x1301bf -InheritOnly -Files),
            (Rule 'S-1-5-32-546' 0x1301bf -InheritOnly))),
    (Fact 'D:\Tools\java' 'S-1-5-21-1-2-3-1001' @()),
    (Fact 'D:\Tools' 'S-1-5-32-544' @(
            (Rule $authenticated 0x4), (Rule $authenticated 0x2), (Rule 'S-1-5-32-547' 0x40))),
    (Fact 'D:\' 'S-1-5-18' @(
            (Rule $users 0x10000000), (Rule $authenticated 0x1301bf -InheritOnly -Files)))
)
$writers = Get-ArciUnsafeWriters -Facts $facts
$out['writers'] = @($writers)
$installer = 'S-1-5-80-956008885-3418522649-1831038044-1853292631-2271478464'
$rules = @((Rule 'S-1-5-18' 0x1f01ff), (Rule $users 0x1200a9), (Rule 'S-1-15-2-1' 0x1200a9))
$java = 'C:\Program Files\Java\bin\java.exe'
$writers = Get-ArciUnsafeWriters -Facts @((Fact $java $installer $rules))
$out['trusted'] = @($writers)
# The default rights of C:\ and C:\Program Files on Windows 10, 11 and Server 2016+ (icacls),
# with a JDK an installer put below Program Files: they inherit from it.
$admins = 'S-1-5-32-544'
$system = 'S-1-5-18'
$packages = 'S-1-15-2-1'
$restricted = 'S-1-15-2-2'
$readExecute = 0x1200a9
$genericReadExecute = -1610612736   # GENERIC_READ | GENERIC_EXECUTE (0xA0000000)
$driveRoot = @(
    (Rule $admins 0x1f01ff), (Rule $system 0x1f01ff), (Rule $users $readExecute),
    (Rule $authenticated 0x1301bf -InheritOnly -Files), (Rule $authenticated 0x4))
$programFiles = @(
    (Rule $installer 0x1f01ff), (Rule $installer 0x10000000 -InheritOnly),
    (Rule $system 0x1301bf), (Rule $system 0x10000000 -InheritOnly -Files),
    (Rule $admins 0x1301bf), (Rule $admins 0x10000000 -InheritOnly -Files),
    (Rule $users $readExecute), (Rule $users $genericReadExecute -InheritOnly -Files),
    (Rule 'S-1-3-0' 0x10000000 -InheritOnly -Files),
    (Rule $packages $readExecute), (Rule $packages $genericReadExecute -InheritOnly -Files),
    (Rule $restricted $readExecute), (Rule $restricted $genericReadExecute -InheritOnly -Files))
$below = @(
    (Rule $installer 0x1f01ff), (Rule $installer 0x10000000 -InheritOnly),
    (Rule $system 0x1f01ff), (Rule $system 0x10000000 -InheritOnly -Files),
    (Rule $admins 0x1f01ff), (Rule $admins 0x10000000 -InheritOnly -Files),
    (Rule $users $readExecute), (Rule $users $genericReadExecute -InheritOnly -Files),
    (Rule 'S-1-3-0' 0x10000000 -InheritOnly -Files),
    (Rule $packages $readExecute), (Rule $packages $genericReadExecute -InheritOnly -Files),
    (Rule $restricted $readExecute), (Rule $restricted $genericReadExecute -InheritOnly -Files))
$file = @((Rule $system 0x1f01ff), (Rule $admins 0x1f01ff), (Rule $users $readExecute),
    (Rule $packages $readExecute), (Rule $restricted $readExecute))
$jdk = 'C:\Program Files\Eclipse Adoptium\jdk-21'
$writers = Get-ArciUnsafeWriters -Facts @(
    (Fact "$jdk\bin\java.exe" $admins $file), (Fact "$jdk\bin" $admins $below),
    (Fact $jdk $admins $below), (Fact 'C:\Program Files\Eclipse Adoptium' $admins $below),
    (Fact 'C:\Program Files' $installer $programFiles), (Fact 'C:\' $installer $driveRoot))
$out['stock'] = @($writers)
# A program in a folder of its own on C:\: the folder inherits C:\'s Modify for Authenticated Users.
$inherited = @((Rule $authenticated 0x1301bf -Files), (Rule $admins 0x1f01ff))
$writers = Get-ArciUnsafeWriters -Facts @(
    (Fact 'C:\Tools\java.exe' $admins @((Rule $authenticated 0x1301bf))),
    (Fact 'C:\Tools' $admins $inherited), (Fact 'C:\' $installer $driveRoot))
$out['tools on the system drive'] = @($writers)

function Tsa([string[]]$Java) { [pscustomobject]@{ tsa = [pscustomobject]@{ java = $Java } } }
$config = [pscustomobject]@{
    tsa    = [pscustomobject]@{
        java = @('D:\Tools\java\bin\java.exe', '-Xmx256m'); zamane_jar = 'D:\Kamu SM\zamane.jar'
    }
    backup = [pscustomobject]@{
        pg_dump    = 'C:\Program Files\PostgreSQL\17\bin\pg_dump.exe'
        pg_restore = 'E:\pg\bin\pg_restore.exe'
    }
}
$records = [ordered]@{}
foreach ($case in @(
        @('configured', $config),
        @('bare', (Tsa @('java'))),
        @('bare with an argument', (Tsa @('java', '-Xmx256m'))),
        @('below the roots', (Tsa @('C:/Program Files/Eclipse Adoptium/jdk-8/bin/java.exe'))),
        @('outside the roots', (Tsa @('D:\jdk\bin\java.exe'))),
        @('no configuration', $null))) {
    $found = Get-ArciExecutableRecords -Config $case[1]
    $records[$case[0]] = @($found)
}
$out['records'] = $records

$zamane = 'C:\ProgramData\ARCILOGGER\zamane'
$sha = 'a1' * 32
$other = 'b2' * 32
$outside = 'D:\Kamu SM\zamane-1.0.jar'
function Plan([string]$Jar, [string]$Sha256, [string[]]$Recorded = @(), [string]$Target) {
    Get-ArciZamaneJarPlan -Jar $Jar -Sha256 $Sha256 -ZamaneDir $zamane -Recorded $Recorded `
        -TargetSha256 $Target
}
$jar = [ordered]@{}
$jar['none'] = Plan '' ''
$jar['missing file'] = Plan "$zamane\zamane.jar" ''
$jar['recorded'] = Plan "$zamane\zamane.jar" $sha @($sha)
$jar['not recorded'] = Plan "$zamane\zamane.jar" $sha @($other)
$jar['approved'] = Plan "$zamane\approved\$sha.jar" $sha @($sha)
$jar['staged'] = Plan "C:/ProgramData/ARCILOGGER/zamane/web/$sha.jar" $sha @($sha)
$jar['outside'] = Plan $outside $sha
$jar['copied before'] = Plan $outside $sha @() $sha
$jar['another file'] = Plan $outside $sha @() $other
# Resolved before it is compared: zamane\..\admin is not zamane\ (Windows resolves it on open).
$jar['dot dot'] = Plan "$zamane\..\admin\work\x\agent.bin" $sha @()
$jar['dot dot inside'] = Plan "$zamane\approved\..\zamane-1.0.jar" $sha @($sha)
$out['jar'] = $jar

$record = Join-Path $Root 'zamane-jar.sha256'
$text = ([string][char]0xFEFF) + "# zamane-1.0.jar, Kamu SM page`r`n" + ('A1' * 32) + `
    "  zamane-1.0.jar`r`n`r`n  " + $other + " *zamane-0.9.jar`n#" + ('c3' * 32) + "`n"
[System.IO.File]::WriteAllText($record, $text)
$hashes = Read-ArciInstallerHashes -Path $record
$out['hashes'] = @($hashes)
$hashes = Read-ArciInstallerHashes -Path (Join-Path $Root 'none.sha256')
$out['no hashes'] = @($hashes)

$sources = [pscustomobject]@{ sources = @(
        [pscustomobject]@{ name = 'fw'; address = '172.20.10.1' },
        [pscustomobject]@{ name = 'old'; address = '172.20.10.2'; enabled = $false },
        [pscustomobject]@{ name = 'core'; address = '172.20.20.0/24'; enabled = $true }) }
$out['source addresses'] = @(Get-ArciSourceAddresses -Config $sources)
$toml = @(
    '[[sources]]', 'name = "fw"', 'address = "172.20.10.1"', '',
    '[[sources]]', 'name = "old"', 'address = "172.20.10.2"', 'enabled = false  # replaced', '',
    '[[sources]]', 'name = "core"', 'enabled = true', 'address = "172.20.20.0/24"', ''
) -join "`n"
$out['source tables'] = Get-ArciSourceTables -Text $toml

$senders = [ordered]@{}
$allowed = @('172.16.0.0/12', '10.0.0.0/8')
foreach ($value in @('172.20.10.1', '172.20.20.0/24', '10.0.0.0/16', '172.20.20.1/24',
        '172.020.10.1', '198.51.100.7', '172.16.0.0/12', 'any', '2001:db8::1')) {
    $senders[$value] = Get-ArciSyslogSenderProblem -Value $value -SourceNetwork $allowed `
        -MinPrefixV4 16
}
$out['senders'] = $senders
$outbound = [ordered]@{}
foreach ($case in @(
        @('smtp', 'ARCILOGGER Out SMTP', 'smtp.ornek.example', 587),
        @('second smtp', 'ARCILOGGER Out SMTP 2', 'smtp2.ornek.example', 465),
        @('ntp', 'ARCILOGGER Out NTP', '172.20.10.1', 123),
        @('kamu sm', 'ARCILOGGER Out Kamu SM', 'zd.kamusm.gov.tr', 80),
        @('replica', 'ARCILOGGER Out Replica', 'nas.ornek.example', 445),
        @('web rule', 'ARCILOGGER HTTPS', 'arcilogger.internal', 443),
        @('dns rule', 'ARCILOGGER Out DNS UDP', '172.20.10.1', 53),
        @('tenth smtp', 'ARCILOGGER Out SMTP 10', 'smtp.ornek.example', 587),
        @('command in the host', 'ARCILOGGER Out SMTP',
            'smtp.ornek.example; Remove-NetFirewallRule', 587),
        @('leading hyphen', 'ARCILOGGER Out NTP', '-ntp.ornek.example', 123),
        @('port 0', 'ARCILOGGER Out SMTP', 'smtp.ornek.example', 0),
        @('port 65536', 'ARCILOGGER Out SMTP', 'smtp.ornek.example', 65536))) {
    $outbound[$case[0]] = Get-ArciOutboundProblem -Rule $case[1] -HostName $case[2] -Port $case[3]
}
$out['outbound'] = $outbound
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 6))
"""


@pytest.fixture
def admin_helpers(engine: str, tmp_path: Path) -> dict[str, Any]:
    root = tmp_path
    output = root / "report.json"
    script = write_script(root / "helpers.ps1", _ADMIN_HELPERS_SCRIPT)
    result = run_powershell(
        engine, script, "-ModulePath", str(MODULE), "-Root", str(root), "-OutputPath", str(output)
    )
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = read_json(output)
    return report


def test_unsafe_writers_of_a_program_to_record(admin_helpers: dict[str, Any]) -> None:
    """Day 0 note D0-3: a program is recorded only where nobody but administrators, SYSTEM and
    TrustedInstaller can change it, plant a file next to it or replace a folder above it
    (Get-ArciUnsafeWriters). On its folder an inherit-only entry that files inherit counts (it
    reaches the program's libraries); on a folder above, only delete, permission and owner
    rights (GENERIC_ALL included) count, never an inherit-only entry or the right to create a
    file or folder there; an owner outside them counts everywhere; a deny entry, a read right
    and CREATOR OWNER never."""
    assert admin_helpers["writers"] == [
        "D:\\Tools\\java\\bin: S-1-5-11",
        "D:\\Tools\\java: S-1-5-21-1-2-3-1001 (sahip [owner])",
        "D:\\Tools: S-1-5-32-547",
        "D:\\: S-1-5-32-545",
    ]
    assert admin_helpers["trusted"] == []


def test_unsafe_writers_accept_the_default_rights_of_windows(admin_helpers: dict[str, Any]) -> None:
    """The stock rights of C:\\ (Authenticated Users: AppendData, and Modify inherit-only) and
    C:\\Program Files let a program below Program Files be recorded; a folder of its own on C:\\
    inherits the Modify and is refused, at the program and at its folder, not at C:\\."""
    assert admin_helpers["stock"] == []
    assert admin_helpers["tools on the system drive"] == [
        "C:\\Tools\\java.exe: S-1-5-11",
        "C:\\Tools: S-1-5-11",
    ]


def test_programs_the_installer_records_for_the_tasks(admin_helpers: dict[str, Any]) -> None:
    """Get-ArciExecutableRecords: what execpolicy would refuse after an upgrade unless the
    installer records it (contract 2.16): tsa.java with arguments (as the whole command) or
    outside Program Files, pg_dump / pg_restore outside it; never the Zamane jar."""
    records = admin_helpers["records"]
    assert records["configured"] == [
        {"What": "tsa.java", "Path": "D:\\Tools\\java\\bin\\java.exe",
         "Argv": ["D:\\Tools\\java\\bin\\java.exe", "-Xmx256m"]},
        {"What": "backup.pg_restore", "Path": "E:\\pg\\bin\\pg_restore.exe", "Argv": None},
    ]  # fmt: skip
    assert records["bare"] == []
    assert records["bare with an argument"] == [
        {"What": "tsa.java", "Path": "java", "Argv": ["java", "-Xmx256m"]}
    ]
    assert records["below the roots"] == []
    assert records["outside the roots"] == [
        {"What": "tsa.java", "Path": "D:\\jdk\\bin\\java.exe", "Argv": None}
    ]
    assert records["no configuration"] == []


def _windows_path(value: str | None) -> str | None:
    return None if value is None else value.replace("/", "\\")


def test_zamane_client_plan_of_an_installation_run(admin_helpers: dict[str, Any]) -> None:
    """Day 0 note D0-14: a client runs from zamane\\ only and with a recorded hash; one from
    outside is copied in (the original stays), a staged upload or a different file of the same
    name is refused, never overwritten."""
    plans = {
        name: (plan["Action"], _windows_path(plan["Target"]))
        for name, plan in admin_helpers["jar"].items()
    }
    zamane = "C:\\ProgramData\\ARCILOGGER\\zamane"
    assert plans == {
        "none": ("none", None),
        "missing file": ("none", None),
        "recorded": ("none", None),
        "not recorded": ("record", None),
        "approved": ("none", None),
        "staged": ("refuse", None),
        "outside": ("copy", f"{zamane}\\zamane-1.0.jar"),
        "copied before": ("copy", f"{zamane}\\zamane-1.0.jar"),
        "another file": ("refuse", f"{zamane}\\zamane-1.0.jar"),
        "dot dot": ("copy", f"{zamane}\\agent.bin"),
        "dot dot inside": ("none", None),
    }
    assert "approve-jar" in admin_helpers["jar"]["staged"]["Reason"]
    assert "not overwritten" in admin_helpers["jar"]["another file"]["Reason"]
    assert "the original stays" in admin_helpers["jar"]["outside"]["Reason"]


def test_installer_hash_record_is_read_as_execpolicy_reads_it(
    admin_helpers: dict[str, Any],
) -> None:
    assert admin_helpers["hashes"] == ["a1" * 32, "b2" * 32]
    assert admin_helpers["no hashes"] == []


def test_a_source_switched_off_gets_no_firewall_opening(admin_helpers: dict[str, Any]) -> None:
    """ADR-0014: [[sources]] enabled = false (Ayarlar → Kaynaklar) closes 514 for the source;
    with and without Python the installer leaves it out."""
    assert admin_helpers["source addresses"] == ["172.20.10.1", "172.20.20.0/24"]
    assert admin_helpers["source tables"] == {
        "Tables": 2,
        "Addresses": ["172.20.10.1", "172.20.20.0/24"],
    }


def test_firewall_sync_checks_senders_and_outbound_rules_again(
    admin_helpers: dict[str, Any],
) -> None:
    """Contract 2.8.3 step 3: ApplySystem checks every value of the system plan again before
    it changes a rule (the plan is not trusted for being well formed)."""
    problems = admin_helpers["senders"]
    assert [value for value, problem in problems.items() if problem is None] == [
        "172.20.10.1",
        "172.20.20.0/24",
        "10.0.0.0/16",
    ]
    assert "[a host address, not the network address]" in problems["172.20.20.1/24"]
    assert "[outside the allowed networks]" in problems["198.51.100.7"]
    assert "[wider than the widest network allowed]" in problems["172.16.0.0/12"]
    for value in ("172.020.10.1", "any", "2001:db8::1"):
        assert "[not an IPv4 address or network]" in problems[value], value
    outbound = admin_helpers["outbound"]
    allowed = {name for name, problem in outbound.items() if problem is None}
    assert allowed == {"smtp", "second smtp", "ntp", "kamu sm", "replica"}
    for name in ("web rule", "dns rule", "tenth smtp"):
        assert "[rule not updated by this step]" in outbound[name], name
    for name in ("command in the host", "leading hyphen"):
        assert "[invalid host name]" in outbound[name], name
    for name in ("port 0", "port 65536"):
        assert "[invalid port]" in outbound[name], name


def _policy_detail(plan: dict[str, Any]) -> AdminPolicy:
    items = {item["id"]: item for item in plan["items"]}
    detail = items["admin.policy"]["detail"]
    assert isinstance(detail, dict), detail
    return parse_document(AdminPolicy, json.dumps(detail, ensure_ascii=False).encode())


def test_dry_run_prepares_the_browser_administration(engine: str, tmp_path: Path) -> None:
    """Contract 2.16: the data root record before ArciAdmin may write the configuration
    folder, secrets.json before its file right, admin\\ and admin\\keys\\ protected before the
    keys exist (D0-7), the policy and the Event Log source; then the rights of every reader."""
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle)
    assert_dry_run_exit(result, plan)
    ids = _ids(plan)
    _before(
        ids,
        "config.copy",
        "config.data-root",
        "config.database-section",
        "executables.record",
        "config.validate",
        "secrets.init",
        "secrets.file",
        "admin.protect",
        "admin.keys",
        "admin.policy",
        "admin.event-source",
        "acl.apply",
        "services.install",
    )
    assert "zamane.jar" not in ids
    items = {item["id"]: item for item in plan["items"]}
    data_root = tmp_path / "data" / "ARCILOGGER"
    assert items["config.data-root"]["detail"] == str(data_root)
    assert "data-root.txt" in items["config.data-root"]["text"]
    assert items["preflight.admin-policy"]["status"] == "ok"
    assert "açık [on]" in items["preflight.admin-policy"]["text"]
    protected = items["admin.protect"]["detail"]
    programdata = tmp_path / "programdata" / "ARCILOGGER"
    assert [Path(entry["Path"]) for entry in protected] == [
        programdata / "admin",
        programdata / "admin" / "keys",
    ]
    owners = {WELL_KNOWN["system"], WELL_KNOWN["admins"]}
    readers = {
        AGENT,
        service_sid("ArciApi"),
        service_sid("ArciWorker"),
        WELL_KNOWN["local_service"],
    }
    for entry in protected:
        assert entry["Protect"] is True
        assert {ace["Sid"] for ace in entry["Aces"]} <= owners | readers
    assert {ace["Sid"] for ace in protected[1]["Aces"]} == owners, (
        "admin\\keys: nobody else until the ACL step names each key's one reader"
    )
    policy = _policy_detail(plan)
    assert policy.enabled is True
    assert policy.source_networks == ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")
    assert (policy.hold_hours, policy.jar_hold_hours) == (0, 24)
    assert policy.trusted_executables == ()
    assert policy.written_by == "installer"
    assert "ArciAdmin" in {Path(name).stem for name in bundle.joinpath("services").glob("*.xml")}
    assert not (tmp_path / "programdata").exists(), "a dry run changes nothing"


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        (("-DisableBrowserAdministration",), {"enabled": False}),
        (
            ("-SourceNetwork", "172.20.0.0/16,10.0.0.1", "-AdminChangeHoldHours", "2",
             "-ZamaneHoldHours", "48"),
            {"source_networks": ("172.20.0.0/16", "10.0.0.1/32"), "hold_hours": 2,
             "jar_hold_hours": 48},
        ),
        (("-SourceNetwork", "172.20.0.1/16"), "-SourceNetwork '172.20.0.1/16'"),
        (("-SourceNetwork", "fe80::/10"), "-SourceNetwork 'fe80::/10'"),
        (("-AdminChangeHoldHours", "169"), "-AdminChangeHoldHours 169"),
        (("-ZamaneHoldHours", "0"), "-ZamaneHoldHours 0"),
    ],
    ids=["disabled", "networks-and-holds", "host-bits", "ipv6", "hold-too-long", "no-jar-hold"],
)  # fmt: skip
def test_dry_run_browser_administration_options(
    engine: str, tmp_path: Path, arguments: tuple[str, ...], expected: dict[str, Any] | str
) -> None:
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle, *arguments)
    assert_dry_run_exit(result, plan)
    check = {item["id"]: item for item in plan["items"]}["preflight.admin-policy"]
    if isinstance(expected, str):
        assert check["status"] == "fail", check
        assert expected in check["text"]
        assert result.returncode == 2
        return
    assert check["status"] == "ok", check
    policy = _policy_detail(plan)
    for key, value in expected.items():
        assert getattr(policy, key) == value, key
    if expected.get("enabled") is False:
        assert "kapalı [off] (-DisableBrowserAdministration)" in check["text"]


def _write_policy(tmp_path: Path, **changes: Any) -> Path:
    path = tmp_path / "programdata" / "ARCILOGGER" / "admin" / "policy.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_policy_document(**changes)), encoding="utf-8")
    return path


def test_dry_run_keeps_what_an_administrator_wrote_into_the_policy(
    engine: str, tmp_path: Path
) -> None:
    """A valid policy.json edited by hand keeps its values; a given parameter changes only
    its own key (and the recorded programs stay)."""
    trusted = [{"path": "D:\\Tools\\java\\bin\\java.exe", "sha256": "c3" * 32}]
    _write_policy(
        tmp_path, enabled=False, max_sources=50, hold_hours=4, source_networks=["172.20.0.0/16"],
        executable_roots=["C:\\Program Files", "D:\\Apps"], trusted_executables=trusted,
    )  # fmt: skip
    bundle = make_fake_bundle(tmp_path)
    _, plan = dry_run(engine, tmp_path, bundle)
    policy = _policy_detail(plan)
    assert policy.enabled is False
    assert (policy.max_sources, policy.hold_hours) == (50, 4)
    assert policy.source_networks == ("172.20.0.0/16",)
    assert policy.executable_roots == ("C:\\Program Files", "D:\\Apps")
    assert [entry.path for entry in policy.trusted_executables] == [
        "D:\\Tools\\java\\bin\\java.exe"
    ]
    _, plan = dry_run(engine, tmp_path, bundle, "-AdminChangeHoldHours", "1")
    policy = _policy_detail(plan)
    assert (policy.enabled, policy.max_sources, policy.hold_hours) == (False, 50, 1)


def test_dry_run_rewrites_an_invalid_policy_and_follows_the_remembered_choices(
    engine: str, tmp_path: Path
) -> None:
    """An invalid policy.json (it means browser administration off for every reader) is
    rewritten with a warning; the choices of an earlier run come from install\\state.json."""
    _write_policy(tmp_path, max_sources=0)
    state = tmp_path / "programdata" / "ARCILOGGER" / "install" / "state.json"
    state.parent.mkdir(parents=True)
    remembered = {"enabled": False, "source_networks": ["192.168.10.0/24"], "hold_hours": 3,
                  "jar_hold_hours": 30}  # fmt: skip
    state.write_text(json.dumps({"admin_policy": remembered}), encoding="utf-8")
    bundle = make_fake_bundle(tmp_path)
    result, plan = dry_run(engine, tmp_path, bundle)
    assert_dry_run_exit(result, plan)
    items = {item["id"]: item for item in plan["items"]}
    warning = items["preflight.admin-policy-file"]
    assert warning["status"] == "warn"
    assert "[invalid, rewritten with the defaults]" in warning["text"]
    assert "max_sources" in warning["text"]
    assert warning["text"] in plan["warnings"]
    policy = _policy_detail(plan)
    assert policy.enabled is False
    assert policy.source_networks == ("192.168.10.0/24",)
    assert (policy.hold_hours, policy.jar_hold_hours, policy.max_sources) == (3, 30, 500)


def _upgrade_with_programs(tmp_path: Path, tsa_and_backup: str) -> Path:
    """An installed version whose venv python is the test interpreter and a configuration
    with the given [tsa] / [backup] tables."""
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    (program / "venv" / "Scripts").mkdir(parents=True)
    (program / "venv" / "Scripts" / "python.exe").symlink_to(sys.executable)
    return _existing_config(
        tmp_path, f'[[sources]]\nname = "fw"\naddress = "172.20.10.1"\n{tsa_and_backup}'
    )


def _programs_toml(java: str, jar: str, pg_dump: str) -> str:
    """[tsa] with java and an argument, a Zamane client, [backup] pg_dump: all outside the
    default trusted folders."""
    lines = [f'java = ["{java}", "-Xmx256m"]', f'zamane_jar = "{jar}"']
    return "[tsa]\n" + "\n".join(lines) + f'\n[backup]\npg_dump = "{pg_dump}"\n'


def _program(path: Path, content: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path.as_posix()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a symbolic link to the test interpreter acts as the venv python",
)
def test_dry_run_upgrade_plans_the_records_of_the_programs_the_tasks_run(
    engine: str, tmp_path: Path
) -> None:
    """Contract 2.16, D0-14: an upgrade records tsa.java with an argument, a pg_dump outside
    Program Files and copies a Zamane client from outside zamane\\ in, so that sealing, credit
    and the backup keep running under execpolicy; a dry run only says so."""
    java = _program(tmp_path / "tools" / "jdk" / "bin" / "java", b"java")
    pg_dump = _program(tmp_path / "tools" / "pg" / "bin" / "pg_dump.exe", b"pg_dump")
    jar = _program(tmp_path / "downloads" / "zamane-1.0.jar", b"zamane client")
    config = _upgrade_with_programs(
        tmp_path,
        _programs_toml(java, jar, pg_dump),
    )
    before = config.read_bytes()
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", site_name=None)
    assert_dry_run_exit(result, plan)
    items = {item["id"]: item for item in plan["items"]}
    check = items["preflight.executables"]
    assert check["status"] == "ok", check
    assert f"tsa.zamane_jar {jar} (copy)" in check["text"].replace("\\", "/")
    detail = items["executables.record"]["detail"]
    records = {record["what"]: record for record in detail["trusted_executables"]}
    assert set(records) == {"tsa.java", "backup.pg_dump"}
    assert records["tsa.java"]["argv"] == [java, "-Xmx256m"]
    assert records["tsa.java"]["sha256"] == sha256_bytes(b"java")
    assert records["backup.pg_dump"]["sha256"] == sha256_bytes(b"pg_dump")
    copy = detail["zamane_jar"]
    assert copy["Action"] == "copy"
    zamane = tmp_path / "programdata" / "ARCILOGGER" / "zamane"
    assert Path(copy["Target"]) == zamane / "zamane-1.0.jar"
    assert copy["Sha256"] == sha256_bytes(b"zamane client")
    policy = _policy_detail(plan)
    assert {(entry.path, entry.sha256) for entry in policy.trusted_executables} == {
        (java, sha256_bytes(b"java")),
        (pg_dump, sha256_bytes(b"pg_dump")),
    }
    assert config.read_bytes() == before, "a dry run changes nothing"
    assert not zamane.exists()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a symbolic link to the test interpreter acts as the venv python",
)
@pytest.mark.parametrize("case", ["staged", "another-file"])
def test_dry_run_upgrade_stops_for_a_zamane_client_it_must_not_use(
    engine: str, tmp_path: Path, case: str
) -> None:
    """A staged upload never runs (it waits for approve-jar); a different file of the same
    name in zamane\\ is never overwritten. Either stops the upgrade before any change, with what
    to do, instead of leaving a version that cannot seal."""
    zamane = tmp_path / "programdata" / "ARCILOGGER" / "zamane"
    if case == "staged":
        jar = _program(zamane / "web" / f"{'a1' * 32}.jar", b"uploaded client")
        needle = "approve-jar"
    else:
        _program(zamane / "zamane-1.0.jar", b"another client")
        jar = _program(tmp_path / "downloads" / "zamane-1.0.jar", b"zamane client")
        needle = "[zamane\\zamane-1.0.jar is a different file and is not overwritten"
    _upgrade_with_programs(tmp_path, f'[tsa]\nzamane_jar = "{jar}"\n')
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", site_name=None)
    assert_dry_run_exit(result, plan)
    assert result.returncode == 2
    failures = [f for f in plan["failures"] if f.startswith("tsa.zamane_jar")]
    assert len(failures) == 1, plan["failures"]
    assert needle in failures[0]


def test_dry_run_uninstall_removes_the_event_source(engine: str, tmp_path: Path) -> None:
    bundle = make_fake_bundle(tmp_path)
    write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    result, plan = dry_run(engine, tmp_path, bundle, "-Uninstall")
    assert_dry_run_exit(result, plan)
    ids = _ids(plan)
    _before(ids, "services.remove", "eventlog.remove")
    kept = {item["id"]: item for item in plan["items"] if item["status"] == "kept"}
    assert "admin\\" in kept["keep.config"]["text"]


_RECORD_SCRIPT = r"""
param(
    [string]$Installer, [string]$Base, [string]$NewJar, [string]$NewJarSha256, [string]$OtherJar,
    [string]$OtherJarSha256, [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
$program = Join-Path $Base 'program/ARCILOGGER'
$programData = Join-Path $Base 'programdata/ARCILOGGER'
$dataRoot = Join-Path $Base 'data/ARCILOGGER'
. $Installer -Upgrade -InstallDir $program -ProgramDataDir $programData -DataRoot $dataRoot `
    -ServerAddress '172.20.20.15' -ZamaneJar $NewJar -ZamaneJarSha256 $NewJarSha256
$out = [ordered]@{}
Resolve-AdminPolicyChoice
# The executables.record step of an upgrade, twice (a repair after it changes nothing).
Update-ExecutableRecords
function Get-Recorded {
    $what = @($script:ExecutableRecords | ForEach-Object { $_.what })
    [ordered]@{ action = $script:ZamaneJarPlan.Action; records = $what }
}
$out['first'] = Get-Recorded
Update-ExecutableRecords
$out['second'] = Get-Recorded
# -ZamaneJar of the same run, then the admin.policy step.
Install-ZamaneJar
Install-ZamaneJar
Write-ArciUtf8File -Path $PolicyPath -Text (Get-AdminPolicyText -Records $script:ExecutableRecords)
# Another file of the same name: never overwritten.
$ZamaneJar = $OtherJar
$ZamaneJarSha256 = $OtherJarSha256
try { Install-ZamaneJar; $out['other'] = 'installed' }
catch { $out['other'] = $_.Exception.Message }
$out['warnings'] = @($script:Warnings)
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 5))
"""


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="a symbolic link to the test interpreter acts as the venv python",
)
def test_upgrade_records_the_programs_so_the_tasks_keep_running(
    engine: str, tmp_path: Path
) -> None:
    """Contract 2.16, D0-14, end to end: after the executables.record, zamane.jar and
    admin.policy steps of an upgrade, arcilogger_core.execpolicy (what the tasks check before
    they run a program) accepts the configured java command, pg_dump and the Zamane client,
    which now runs from its copy in zamane\\; the original outside it is refused."""
    from arcilogger_core.execpolicy import (
        ExecutableRefusedError,
        TrustAnchors,
        check_executable,
        check_java,
        check_zamane_jar,
    )

    java = _program(tmp_path / "tools" / "jdk" / "bin" / "java", b"java")
    pg_dump = _program(tmp_path / "tools" / "pg" / "bin" / "pg_dump", b"pg_dump")
    jar = _program(tmp_path / "downloads" / "zamane-1.0.jar", b"zamane client 1.0")
    new_jar = _program(tmp_path / "downloads" / "zamane-1.1.jar", b"zamane client 1.1")
    other = _program(tmp_path / "elsewhere" / "zamane-1.1.jar", b"not the client")
    config = _upgrade_with_programs(
        tmp_path,
        _programs_toml(java, jar, pg_dump),
    )
    zamane = config.parent / "zamane"
    zamane.mkdir()
    (config.parent / "admin").mkdir()
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    output = tmp_path / "records.json"
    script = write_script(tmp_path / "records.ps1", _RECORD_SCRIPT)
    result = run_powershell(
        engine, script, "-Installer", str(bundle / "installer" / "Install-Arcilogger.ps1"),
        "-Base", str(tmp_path), "-NewJar", new_jar,
        "-NewJarSha256", sha256_bytes(b"zamane client 1.1").upper(), "-OtherJar", other,
        "-OtherJarSha256", sha256_bytes(b"not the client"), "-OutputPath", str(output),
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output)
    assert report["first"] == {"action": "copy", "records": ["tsa.java", "backup.pg_dump"]}
    assert report["second"]["action"] == "none", "the copy is recorded: nothing more to do"
    assert "[a different file, not overwritten: use another file name]" in report["other"]
    copy = zamane / "zamane-1.0.jar"
    assert copy.read_bytes() == b"zamane client 1.0"
    assert Path(jar).is_file(), "the original stays where it is"
    configured = tomllib.loads(config.read_text(encoding="utf-8"))
    assert configured["tsa"]["zamane_jar"] == copy.as_posix()
    assert configured["tsa"]["java"] == [java, "-Xmx256m"], "nothing else is rewritten"
    assert len(list(config.parent.glob("arcilogger.toml.before-*"))) == 1
    lines = (zamane / "zamane-jar.sha256").read_text(encoding="utf-8").splitlines()
    hashes = [line.split()[0] for line in lines if line and not line.startswith("#")]
    assert hashes == [sha256_bytes(b"zamane client 1.0"), sha256_bytes(b"zamane client 1.1")]
    assert (zamane / "zamane-1.1.jar").read_bytes() == b"zamane client 1.1"
    assert any(
        "güvenilen programlara eklendi [recorded as a trusted program]" in w
        for w in report["warnings"]
    )
    anchors = TrustAnchors.of(config)
    assert anchors.policy.written_by == "installer", "policy.json as the installer wrote it"
    assert check_java(configured["tsa"]["java"], anchors) == (java, "-Xmx256m")
    assert check_executable(pg_dump, anchors, what="backup.pg_dump") == pg_dump
    assert check_zamane_jar(configured["tsa"]["zamane_jar"], anchors).path == copy
    assert check_zamane_jar(zamane / "zamane-1.1.jar", anchors).sha256 == sha256_bytes(
        b"zamane client 1.1"
    )
    with pytest.raises(ExecutableRefusedError):
        check_zamane_jar(jar, anchors)
    with pytest.raises(ExecutableRefusedError):
        check_java([java, "-XX:OnError=calc.exe"], anchors)


_PROGRAM_CONFIG_SCRIPT = r"""
param(
    [string]$ModulePath, [string]$Root, [string]$OldPython, [string]$CurrentPython,
    [string]$OutputPath
)
$ErrorActionPreference = 'Stop'
Import-Module $ModulePath -Force
$out = [ordered]@{}
foreach ($name in @('before.toml', 'changed.toml', 'broken.toml')) {
    $file = Join-Path $Root $name
    $out["old $name"] = Test-ArciProgramConfig -Python $OldPython -ConfigPath $file
    $out["current $name"] = Test-ArciProgramConfig -Python $CurrentPython -ConfigPath $file
}
$out['no program'] = Test-ArciProgramConfig -Python (Join-Path $Root 'missing/python') `
    -ConfigPath (Join-Path $Root 'before.toml')
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 3))
"""

#: arcilogger_core.config of a version from before the browser administration, as far as the
#: rollback check needs it: its Config refuses a key it does not know (extra="forbid").
_OLD_CONFIG_MODULE = """
import tomllib


def load_config(path):
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    for number, source in enumerate(data.get("sources", [])):
        for key in sorted(set(source) - {"name", "type", "address"}):
            raise ValueError(
                f"1 validation error for Config\\nsources.{number}.{key}\\n"
                "  Extra inputs are not permitted"
            )
    return data
"""


def _old_program(root: Path) -> Path:
    """A venv (the real interpreter, isolated) with the arcilogger_core of an older version."""
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(root)], check=True)
    python = root / "bin" / "python"
    found = subprocess.run(
        [str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True, capture_output=True, text=True,
    )  # fmt: skip
    package = Path(found.stdout.strip()) / "arcilogger_core"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('__version__ = "0.9.0"\n', encoding="utf-8")
    (package / "config.py").write_text(_OLD_CONFIG_MODULE, encoding="utf-8")
    return python


@pytest.mark.skipif(sys.platform == "win32", reason="a POSIX venv stands for the old program")
def test_rollback_check_loads_the_configuration_with_the_previous_program(
    engine: str, tmp_path: Path
) -> None:
    """Test-ArciProgramConfig runs the program a rollback puts back on arcilogger.toml before
    its services start (contract 2.16): a key the browser added (enabled = false of a source)
    is refused by the older version, not by this one."""
    old = _old_program(tmp_path / "old-venv")
    data = (tmp_path / "data").as_posix()
    source = '[[sources]]\nname = "fw"\ntype = "fortigate"\naddress = "172.20.10.1"\n'
    (tmp_path / "before.toml").write_text(f'data_dir = "{data}"\n{source}', encoding="utf-8")
    (tmp_path / "changed.toml").write_text(
        f'data_dir = "{data}"\n{source}enabled = false\n', encoding="utf-8"
    )
    (tmp_path / "broken.toml").write_text('data_dir = "unterminated\n', encoding="utf-8")
    output = tmp_path / "program-config.json"
    script = write_script(tmp_path / "program-config.ps1", _PROGRAM_CONFIG_SCRIPT)
    result = run_powershell(
        engine, script, "-ModulePath", str(MODULE), "-Root", str(tmp_path),
        "-OldPython", str(old), "-CurrentPython", sys.executable, "-OutputPath", str(output),
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    report = read_json(output)
    assert report["old before.toml"] is None
    assert report["current before.toml"] is None
    assert report["old changed.toml"] == (
        "1 validation error for Config sources.0.enabled Extra inputs are not permitted"
    )
    assert report["current changed.toml"] is None, "this version knows [[sources]] enabled"
    for program in ("old", "current"):
        assert report[f"{program} broken.toml"], program
        assert "\n" not in report[f"{program} broken.toml"], "one line for the log"
    assert "[could not be run]" in report["no program"]


_FIREWALL_SYNC_SCRIPT = r"""
param([string]$Script, [string]$CasesPath, [string]$Root)
$ErrorActionPreference = 'Stop'
$cases = [System.IO.File]::ReadAllText($CasesPath) | ConvertFrom-Json
foreach ($case in $cases) {
    $arguments = @{
        SyncFromConfig = $true; DryRun = $true; PlanPath = (Join-Path $Root ($case.name + '.json'))
        PolicyPath = (Join-Path $Root $case.policy); StateFile = (Join-Path $Root $case.state)
        SourceAddress = @($case.senders | ForEach-Object { [string]$_ })
        Outbound = @($case.outbound)
    }
    # Sources outside the policy (system-plan's problems) and what the syslog rules hold now.
    $given = $case.PSObject.Properties.Name
    if ($given -contains 'keep') { $arguments['KeepSourceAddress'] = @($case.keep) }
    if ($given -contains 'live') { $arguments['LiveSourceAddress'] = @($case.live) }
    $global:LASTEXITCODE = -1
    & $Script @arguments | Out-Host
    $code = Join-Path $Root ($case.name + '.code')
    [System.IO.File]::WriteAllText($code, [string]$global:LASTEXITCODE)
}
"""


def _firewall_sync(
    engine: str, tmp_path: Path, cases: list[dict[str, Any]]
) -> tuple[subprocess.CompletedProcess[str], dict[str, tuple[int, dict[str, Any]]]]:
    (tmp_path / "cases.json").write_text(json.dumps(cases), encoding="utf-8")
    script = write_script(tmp_path / "sync.ps1", _FIREWALL_SYNC_SCRIPT)
    result = run_powershell(
        engine, script, "-Script", str(WINDOWS / "scripts" / "Set-ArciFirewall.ps1"),
        "-CasesPath", str(tmp_path / "cases.json"), "-Root", str(tmp_path),
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    found = {}
    for case in cases:
        code = int((tmp_path / f"{case['name']}.code").read_text(encoding="utf-8"))
        found[case["name"]] = (code, read_json(tmp_path / f"{case['name']}.json"))
    return result, found


SMTP_OUT = {"rule": "ARCILOGGER Out SMTP", "host": "smtp.ornek.example", "port": 587}
NTP_OUT = {"rule": "ARCILOGGER Out NTP", "host": "172.20.10.1", "port": 123}


def test_firewall_sync_updates_the_syslog_rules_in_place_and_nothing_else(
    engine: str, tmp_path: Path
) -> None:
    """Set-ArciFirewall.ps1 -SyncFromConfig (contract 2.8.3 step 4): the senders of the plan go
    into the two syslog rules (narrowed, widened within the policy, removed when none is left);
    in strict mode the plan's outbound rules are updated, an unresolvable host keeps its rule.
    It never names another rule, a profile, 443, 3389 or 5985, and refuses before any change."""
    (tmp_path / "policy.json").write_text(
        json.dumps(_policy_document(source_networks=["172.20.0.0/16"])), encoding="utf-8"
    )
    (tmp_path / "off.json").write_text(json.dumps(_policy_document(enabled=False)), "utf-8")
    (tmp_path / "state.json").write_text(json.dumps({"schema": 1}), encoding="utf-8")
    (tmp_path / "strict.json").write_text(
        json.dumps({"schema": 1, "strict_outbound": True}), encoding="utf-8"
    )
    base: dict[str, Any] = {"policy": "policy.json", "state": "state.json", "outbound": []}
    cases: list[dict[str, Any]] = [
        {**base, "name": "narrowed", "senders": ["172.20.10.1"]},
        {**base, "name": "widened", "senders": ["172.20.10.1", "172.20.20.0/24", "172.20.30.7"]},
        {**base, "name": "none-left", "senders": []},
        {**base, "name": "outside", "senders": ["172.20.10.1", "198.51.100.7"]},
        {**base, "name": "too-wide", "senders": ["172.0.0.0/8"]},
        {**base, "name": "twice", "senders": ["172.20.10.1", "172.20.10.1"]},
        {**base, "name": "not-managed", "senders": ["172.20.10.1"], "state": "none.json"},
        {**base, "name": "disabled", "senders": ["172.20.10.1"], "policy": "off.json"},
        {**base, "name": "strict", "senders": ["172.20.10.1"], "state": "strict.json",
         "outbound": [SMTP_OUT, NTP_OUT]},
        {**base, "name": "not-strict", "senders": ["172.20.10.1"], "outbound": [SMTP_OUT]},
        {**base, "name": "bad-rule", "senders": ["172.20.10.1"], "state": "strict.json",
         "outbound": [{"rule": "ARCILOGGER HTTPS", "host": "172.20.10.37", "port": 443}]},
        # Enabled sources the policy leaves out: kept where both rules hold them, never added.
        {**base, "name": "kept", "senders": ["172.20.10.1"],
         "keep": ["198.51.100.7", "203.0.113.0/24", "100.64.0.9"],
         "live": ["172.20.10.1", "198.51.100.7", "203.0.113.0/255.255.255.0"]},
        {**base, "name": "kept-alone", "senders": [], "keep": ["198.51.100.7"],
         "live": ["198.51.100.7"]},
        {**base, "name": "not-open", "senders": [], "keep": ["198.51.100.7"], "live": []},
    ]  # fmt: skip
    result, found = _firewall_sync(engine, tmp_path, cases)
    codes = {name: code for name, (code, _) in found.items()}
    assert codes == {
        "narrowed": 0, "widened": 0, "none-left": 0, "outside": 2, "too-wide": 2, "twice": 2,
        "not-managed": 3, "disabled": 2, "strict": 0, "not-strict": 0, "bad-rule": 2,
        "kept": 0, "kept-alone": 0, "not-open": 0,
    }  # fmt: skip
    for name, (code, plan) in found.items():
        assert plan["mode"] == "sync", name
        assert plan["exit_code"] == code, name
        assert (plan["profiles"], plan["other_rules"], plan["would_disable"]) == (
            "unchanged", "unchanged", None,
        ), name  # fmt: skip
        named = {change["rule"] for change in plan["changes"]}
        assert named <= {"ARCILOGGER Syslog UDP", "ARCILOGGER Syslog TCP",
                         "ARCILOGGER Out SMTP", "ARCILOGGER Out NTP"}, name  # fmt: skip
        ports = {port for change in plan["changes"] for port in change["ports"]}
        assert not ports & {"443", "3389", "5985"}, name
        if code != 0:
            assert plan["changes"] == [], f"{name}: refused before any change"

    def syslog(name: str) -> list[tuple[str, str, list[str]]]:
        changes = found[name][1]["changes"]
        return [(c["protocol"], c["operation"], c["remote_address"]) for c in changes
                if c["direction"] == "Inbound"]  # fmt: skip

    assert syslog("narrowed") == [("UDP", "set", ["172.20.10.1"]), ("TCP", "set", ["172.20.10.1"])]
    widened = ["172.20.10.1", "172.20.20.0/24", "172.20.30.7"]
    assert syslog("widened") == [("UDP", "set", widened), ("TCP", "set", widened)]
    assert syslog("none-left") == [("UDP", "remove", []), ("TCP", "remove", [])]
    assert any("198.51.100.7" in p and "[outside the allowed networks]" in p
               for p in found["outside"][1]["problems"])  # fmt: skip
    assert (
        "[browser administration off or policy.json invalid]" in found["disabled"][1]["problems"][0]
    )
    assert "[firewall not managed by ARCILOGGER]" in result.stdout
    strict = {c["rule"]: c for c in found["strict"][1]["changes"] if c["direction"] == "Outbound"}
    assert strict["ARCILOGGER Out NTP"]["operation"] == "set"
    assert strict["ARCILOGGER Out NTP"]["remote_address"] == ["172.20.10.1"]
    assert strict["ARCILOGGER Out NTP"]["protocol"] == "UDP"
    assert strict["ARCILOGGER Out SMTP"]["operation"] == "keep", "offline DNS keeps the rule"
    assert strict["ARCILOGGER Out SMTP"]["ports"] == ["587"]
    assert any("[not resolved]" in w for w in found["strict"][1]["warnings"])
    assert [c["direction"] for c in found["not-strict"][1]["changes"]] == ["Inbound", "Inbound"]
    assert any("[strict outbound mode is off" in w for w in found["not-strict"][1]["warnings"])
    assert "[rule not updated by this step]" in found["bad-rule"][1]["problems"][0]
    kept = ["172.20.10.1", "198.51.100.7", "203.0.113.0/24"]
    assert syslog("kept") == [("UDP", "set", kept), ("TCP", "set", kept)]
    assert found["kept"][1]["kept"] == ["198.51.100.7", "203.0.113.0/24"]
    assert found["kept"][1]["not_kept"] == ["100.64.0.9"]
    assert any("100.64.0.9" in w and "[outside the policy networks: not opened]" in w
               for w in found["kept"][1]["warnings"])  # fmt: skip
    assert syslog("kept-alone") == [
        ("UDP", "set", ["198.51.100.7"]),
        ("TCP", "set", ["198.51.100.7"]),
    ]
    assert syslog("not-open") == [("UDP", "remove", []), ("TCP", "remove", [])]
    assert found["not-open"][1]["not_kept"] == ["198.51.100.7"]
    # What the rules hold is read from the rules themselves: the test input only in a dry run.
    refused = run_powershell(
        engine, WINDOWS / "scripts" / "Set-ArciFirewall.ps1", "-SyncFromConfig",
        "-StateFile", str(tmp_path / "state.json"), "-PolicyPath", str(tmp_path / "policy.json"),
        "-LiveSourceAddress", "198.51.100.7",
    )  # fmt: skip
    assert refused.returncode != 0
    assert "[only with -SyncFromConfig -DryRun]" in refused.stdout + refused.stderr
    source = (WINDOWS / "scripts" / "Set-ArciFirewall.ps1").read_text(encoding="utf-8-sig")
    sync = source[
        source.index("function Invoke-SyncFromConfig") : source.index("if ($SyncFromConfig)")
    ]
    for forbidden in ("Set-NetFirewallProfile", "Disable-NetFirewallRule", "Read-Host",
                      "Write-FirewallState"):  # fmt: skip
        assert forbidden not in sync, forbidden
    assert "Set-NetFirewallAddressFilter" in sync


# --- ApplySystem: Sync-ArciSystem.ps1 (contract 2.8.3) -------------------------------------------

SYNC_SCRIPT = WINDOWS / "scripts" / "Sync-ArciSystem.ps1"
KNOWN_CLIENT = b"a Zamane client release (test bytes)"
KNOWN_SHA = sha256_bytes(KNOWN_CLIENT)


def _system_plan_sample() -> dict[str, Any]:
    """The example system plan of contract 2.8.3, as core/tests/test_admin_contract.py keeps it
    (the fixture of these dry runs)."""
    spec = importlib.util.spec_from_file_location(
        "_admin_contract_examples", REPO / "core" / "tests" / "test_admin_contract.py"
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    plan: dict[str, Any] = module.system_plan_sample()
    parse_document(SystemPlan, json.dumps(plan).encode())
    return plan


class SyncSite:
    """A configuration folder as ApplySystem finds it: policy.json (172.20.0.0/16), the
    firewall's state file (strict outbound) and the example plan, its promoted client staged in
    this folder's zamane\\web."""

    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path
        self.programdata = tmp_path / "programdata" / "ARCILOGGER"
        self.system = self.programdata / "admin" / "system"
        self.system.mkdir(parents=True)
        self.web = self.programdata / "zamane" / "web"
        self.web.mkdir(parents=True)
        self.policy = self.programdata / "admin" / "policy.json"
        self.write_policy()
        state = self.programdata / "install" / "firewall-state.json"
        state.parent.mkdir()
        state.write_text(json.dumps({"schema": 1, "strict_outbound": True}), encoding="utf-8")
        self.firewall_state = state
        self.staged = self.web / f"{KNOWN_SHA}.jar"
        self.staged.write_bytes(KNOWN_CLIENT)
        self.plan = _system_plan_sample()
        promotion = self.plan["jars"]["promote"][0]
        promotion["sha256"] = KNOWN_SHA
        promotion["staged"] = str(self.staged)

    def write_policy(self, **changes: Any) -> None:
        document = _policy_document(source_networks=["172.20.0.0/16"], **changes)
        self.policy.write_text(json.dumps(document), encoding="utf-8")

    def write(self, name: str, document: dict[str, Any]) -> None:
        (self.system / name).write_text(json.dumps(document), encoding="utf-8")

    def sync_state(self, staged: list[str] | None = None) -> dict[str, Any]:
        """sync-state.json as a successful run of the current plan leaves it."""
        return {
            "config_sha256": self.plan["config_sha256"],
            "collector_ack_sha256": self.plan["collector_ack_sha256"],
            "policy_sha256": sha256_bytes(self.policy.read_bytes()),
            "staged": [self.staged.name] if staged is None else staged,
        }

    def run(
        self, engine: str, *extra: str, plan: bool = True
    ) -> tuple[subprocess.CompletedProcess[str], dict[str, Any]]:
        plan_path = self.root / "system-plan.json"
        plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
        report_path = self.root / f"sync-{len(list(self.root.glob('sync-*.json')))}.json"
        arguments = ["-ConfigPath", str(self.programdata / "arcilogger.toml")]
        arguments += ["-DryRun", "-PlanPath", str(plan_path)] if plan else []
        result = run_powershell(
            engine, SYNC_SCRIPT, *arguments, "-ReportPath", str(report_path), *extra
        )
        report: dict[str, Any] = read_json(report_path) if report_path.is_file() else {}
        if report:
            assert report["exit_code"] == result.returncode, result.stdout + result.stderr
        return result, report


def _steps(report: dict[str, Any]) -> dict[str, tuple[str, str | None]]:
    return {step["step"]: (step["state"], step["code"]) for step in report["steps"]}


def _events(report: dict[str, Any]) -> list[tuple[int, str]]:
    return [(event["id"], event["level"]) for event in report["events"]]


def _system_status(report: dict[str, Any]) -> SystemStatus:
    return parse_document(SystemStatus, json.dumps(report["status"], ensure_ascii=False).encode())


def _journal(report: dict[str, Any]) -> list[SystemJournalLine]:
    return [
        parse_document(SystemJournalLine, json.dumps(line, ensure_ascii=False).encode())
        for line in report["journal"]
    ]


def _sync_changes(report: dict[str, Any]) -> dict[str, tuple[str, list[str]]]:
    return {c["rule"]: (c["operation"], c["remote_address"]) for c in report["firewall"]["changes"]}


def test_system_sync_dry_run_applies_the_example_plan(engine: str, tmp_path: Path) -> None:
    """The example plan of contract 2.8.3: the two senders into the syslog rules, the strict
    outbound rules (an offline host keeps its rule), the known client promoted; the documents
    the run would write are those of the contract, and a dry run writes none of them. The
    source the plan left out (source_outside_policy) is not opened (the rules do not hold it)
    and is reported: the status code and an R20-admin alert."""
    site = SyncSite(tmp_path)
    result, report = site.run(engine)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["dry_run"] is True
    assert report["fast_path"] is False
    assert _steps(report) == {
        "plan": ("done", None), "check": ("done", None), "system.firewall": ("done", None),
        "system.time": ("skipped", None), "jar.promotion": ("done", None),
    }  # fmt: skip
    senders = ["172.20.10.1", "172.20.20.0/24"]
    changes = _sync_changes(report)
    assert changes["ARCILOGGER Syslog UDP"] == ("set", senders)
    assert changes["ARCILOGGER Syslog TCP"] == ("set", senders)
    assert changes["ARCILOGGER Out NTP"] == ("set", ["172.20.10.1"])
    assert changes["ARCILOGGER Out SMTP"][0] == "keep"
    assert report["jars"]["promote"] == [
        {"sha256": KNOWN_SHA, "staged": str(site.staged),
         "target": str(site.programdata / "zamane" / "approved" / f"{KNOWN_SHA}.jar"),
         "result": "promote"},
    ]  # fmt: skip
    assert _events(report) == [(4107, "Warning"), (4106, "Information")]
    assert report["plan_problems"] == [{"code": "source_outside_policy", "value": "198.51.100.7"}]
    assert report["firewall"]["kept"] == []
    assert report["firewall"]["not_kept"] == ["198.51.100.7"]
    status = _system_status(report)
    assert status.ok is True
    assert status.code == "source_outside_policy"
    assert status.applied_config_sha256 == site.plan["config_sha256"]
    assert status.collector_ack_sha256 == site.plan["collector_ack_sha256"]
    assert status.firewall.managed
    assert status.firewall.strict_outbound
    assert status.firewall.syslog_addresses == tuple(senders)
    assert status.firewall.outbound_hosts == ("smtp.ornek.example", "172.20.10.1")
    assert status.time.peers == "172.20.10.1,0x8 time.ume.tubitak.gov.tr,0xA"
    assert (status.jars.staged, status.jars.promoted) == (0, 1)
    journal = _journal(report)
    assert [line.change for line in journal] == ["firewall", "jar"]
    assert journal[0].firewall is not None
    assert (journal[0].firewall.syslog_addresses_count, journal[0].firewall.outbound_rules) == (
        2,
        2,
    )
    assert journal[1].jar is not None
    assert journal[1].jar.approved_by == "known"
    assert report["alerts"] == [
        {"details": {"section": "tsa", "paths": ["tsa.zamane_jar"]}, "rule": "R20-config-change",
         "severity": "high", "message": "Zamane istemcisi onaylandı [Zamane client promoted]",
         "time": report["alerts"][0]["time"]},
        {"details": {"code": "source_outside_policy"}, "rule": "R20-admin", "severity": "high",
         "message": "Sistem eşitlemesi: log kaynakları yönetim politikasının ağlarının dışında "
                    "[system sync: log sources outside the policy networks]: 198.51.100.7 "
                    "(açılmadı [not opened])",
         "time": report["alerts"][1]["time"]},
    ]  # fmt: skip
    assert sorted(path.name for path in site.system.iterdir()) == [], "a dry run writes nothing"
    assert not (site.programdata / "zamane" / "approved").exists()


def test_system_sync_takes_the_fast_path_when_nothing_changed(engine: str, tmp_path: Path) -> None:
    """The inputs of the last successful run (configuration, collector acknowledgement,
    policy, staged clients) unchanged: nothing is checked or applied, no event is written.
    status.json is written again with what it said and a new time: ArciAdmin's start-up ping
    (Event 4101) is answered by that change (agent.json ``system_task``)."""
    site = SyncSite(tmp_path)
    previous = {
        "schema": "arcilogger.admin-system/1", "at": "2026-10-25T10:10:00+03:00", "ok": True,
        "code": "source_outside_policy", "applied_config_sha256": site.plan["config_sha256"],
        "collector_ack_sha256": site.plan["collector_ack_sha256"],
        "firewall": {"managed": True, "strict_outbound": True,
                     "syslog_addresses": ["172.20.10.1"], "outbound_hosts": ["172.20.10.1"]},
        "time": {"peers": "172.20.10.1,0x8", "changed_at": "2026-10-24T09:00:00+03:00"},
        "jars": {"staged": 0, "promoted": 0},
    }  # fmt: skip
    site.write("status.json", previous)
    site.write("sync-state.json", site.sync_state())
    result, report = site.run(engine)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["fast_path"] is True
    assert _steps(report) == {"plan": ("done", None)}
    assert report["events"] == []
    assert report["journal"] == []
    assert report["alerts"] == []
    status = _system_status(report)
    before = parse_document(SystemStatus, json.dumps(previous).encode())
    assert status.at > before.at
    assert status.model_copy(update={"at": before.at}) == before
    for changed in ("policy", "staged", "config", "status"):
        site = SyncSite(tmp_path / changed)
        if changed != "status":  # without status.json the run is a full one
            site.write("status.json", previous)
        site.write("sync-state.json", site.sync_state())
        if changed == "policy":
            site.write_policy(hold_hours=1)
        elif changed == "staged":
            (site.web / f"{'c3' * 32}.jar").write_bytes(b"another upload")
        else:
            site.plan["config_sha256"] = "d4" * 32
        _, report = site.run(engine)
        assert report["fast_path"] is False, changed


@pytest.mark.parametrize(
    ("before", "senders", "journal"),
    [
        (["172.20.10.1", "172.20.20.0/24", "172.20.30.0/24"], ["172.20.10.1"], 1),
        (["172.20.10.1"], ["172.20.10.1", "172.20.20.0/24", "172.20.40.7"], 3),
        (["172.20.10.1", "172.20.20.0/24"], ["172.20.10.1", "172.20.20.0/24"], None),
        (["172.20.10.1"], [], 0),
    ],
    ids=["narrowed", "widened", "unchanged", "last-source-off"],
)
def test_system_sync_moves_the_syslog_senders_with_the_configuration(
    engine: str, tmp_path: Path, before: list[str], senders: list[str], journal: int | None
) -> None:
    """Contract 2.8.3: what the senders were (status.json) and what they become; a firewall
    journal line only when they change (imported as admin.system)."""
    site = SyncSite(tmp_path)
    previous = {
        "schema": "arcilogger.admin-system/1", "at": "2026-10-25T10:10:00+03:00", "ok": True,
        "code": None, "applied_config_sha256": "e5" * 32, "collector_ack_sha256": None,
        "firewall": {"managed": True, "strict_outbound": True, "syslog_addresses": before,
                     "outbound_hosts": ["smtp.ornek.example", "172.20.10.1"]},
        "time": {"peers": "172.20.10.1,0x8 time.ume.tubitak.gov.tr,0xA", "changed_at": None},
        "jars": {"staged": 0, "promoted": 0},
    }  # fmt: skip
    parse_document(SystemStatus, json.dumps(previous).encode())
    site.write("status.json", previous)
    site.plan["firewall"]["syslog_addresses"] = senders
    site.plan["jars"]["promote"] = []
    result, report = site.run(engine)
    assert result.returncode == 0, result.stdout + result.stderr
    operation = "set" if senders else "remove"
    assert _sync_changes(report)["ARCILOGGER Syslog UDP"] == (operation, senders)
    assert _system_status(report).firewall.syslog_addresses == tuple(senders)
    lines = [line for line in _journal(report) if line.change == "firewall"]
    if journal is None:
        assert lines == []
    else:
        assert len(lines) == 1
        assert lines[0].firewall is not None
        assert lines[0].firewall.syslog_addresses_count == journal


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        (
            "outside-policy",
            "198.51.100.7: izin verilen ağların dışında [outside the allowed networks]",
        ),
        ("too-wide", "172.0.0.0/8: /8, izin verilen en geniş ağ /16"),
        ("bad-host", "[invalid host name]: smtp.ornek.example;calc"),
        ("bad-time", "time.primary: -ntp.ornek.example"),
        ("other-installation", "[not in this installation's zamane\\web]"),
        ("not-known", f"jars.promote: {KNOWN_SHA}"),
    ],
)
def test_system_sync_refuses_a_plan_value_before_any_change(
    engine: str, tmp_path: Path, change: str, problem: str
) -> None:
    """Step 3: every value is checked again; one refused value stops the run before the
    firewall, the time or a client changes (system_plan_invalid, Event 4106 error, R20-admin),
    and status.json keeps the blocks that are still in force."""
    site = SyncSite(tmp_path)
    firewall = site.plan["firewall"]
    if change == "outside-policy":
        firewall["syslog_addresses"] = [*firewall["syslog_addresses"], "198.51.100.7"]
    elif change == "too-wide":
        firewall["syslog_addresses"] = ["172.0.0.0/8"]
    elif change == "bad-host":
        firewall["outbound"][0]["host"] = "smtp.ornek.example;calc"
    elif change == "bad-time":
        site.plan["time"]["primary"] = "-ntp.ornek.example"
    elif change == "other-installation":
        site.plan["jars"]["promote"][0]["staged"] = (
            f"C:\\ProgramData\\ARCILOGGER\\zamane\\web\\{KNOWN_SHA}.jar"
        )
    else:
        site.plan["jars"]["promote"][0]["reason"] = "hold"
    result, report = site.run(engine)
    assert result.returncode == 1, result.stdout + result.stderr
    assert report["code"] == "system_plan_invalid"
    assert _steps(report) == {"plan": ("done", None), "check": ("failed", "system_plan_invalid")}
    assert any(problem in found for found in report["problems"]), report["problems"]
    assert report["firewall"] is None
    assert report["journal"] == []
    assert _events(report) == [(4106, "Error")]
    assert [(a["rule"], a["details"]) for a in report["alerts"]] == [
        ("R20-admin", {"code": "system_plan_invalid"})
    ]
    status = _system_status(report)
    assert (status.ok, status.code) == (False, "system_plan_invalid")
    assert status.firewall.managed is False, "nothing applied yet: what status.json said"


def test_system_sync_reports_a_plan_it_cannot_read(engine: str, tmp_path: Path) -> None:
    site = SyncSite(tmp_path)
    site.plan = {"schema": "arcilogger.system-plan/2"}
    result, report = site.run(engine)
    assert result.returncode == 1
    assert report["code"] == "system_plan_failed"
    assert "[is not arcilogger.system-plan/1]" in report["problems"][0]
    status = _system_status(report)
    assert (status.ok, status.code) == (False, "system_plan_failed")
    assert _events(report) == [(4106, "Error")]
    assert report["alerts"][0]["rule"] == "R20-admin"


def test_system_sync_does_nothing_while_browser_administration_is_off(
    engine: str, tmp_path: Path
) -> None:
    """-DisableBrowserAdministration (or an invalid policy.json): no plan, no change, exit 0;
    the installer's firewall, time and client stay as they are."""
    site = SyncSite(tmp_path)
    site.write_policy(enabled=False)
    result, report = site.run(engine)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _steps(report) == {"policy": ("skipped", None)}
    assert report["status"] is None
    assert report["events"] == []
    site.policy.write_text("{", encoding="utf-8")
    result, report = site.run(engine)
    assert (result.returncode, _steps(report)) == (0, {"policy": ("skipped", None)})


def test_system_sync_reads_a_plan_file_only_in_a_dry_run(engine: str, tmp_path: Path) -> None:
    """The real task always takes its plan from system-plan, never from a file."""
    site = SyncSite(tmp_path)
    plan = tmp_path / "plan.json"
    plan.write_text(json.dumps(site.plan), encoding="utf-8")
    result, report = site.run(engine, "-PlanPath", str(plan), plan=False)
    assert result.returncode != 0
    needle = _unwrapped("-PlanPath yalnızca -DryRun ile [only with -DryRun]")
    assert needle in _unwrapped(result.stdout + result.stderr)
    assert report == {}


def test_system_sync_skips_a_firewall_it_does_not_manage(engine: str, tmp_path: Path) -> None:
    """An installation with -SkipFirewall: the step is skipped (firewall_not_managed, a
    warning), the rest of the plan is applied."""
    site = SyncSite(tmp_path)
    site.firewall_state.unlink()
    result, report = site.run(engine)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _steps(report)["system.firewall"] == ("skipped", "firewall_not_managed")
    assert report["firewall"] is None
    status = _system_status(report)
    assert (status.ok, status.code, status.firewall.managed) == (
        True,
        "firewall_not_managed",
        False,
    )
    assert _steps(report)["jar.promotion"] == ("done", None)


def test_system_sync_applies_a_changed_time_source(engine: str, tmp_path: Path) -> None:
    site = SyncSite(tmp_path)
    site.plan["time"] = {"primary": "ntp.ornek.example", "fallback": "time.ume.tubitak.gov.tr",
                         "changed": True}  # fmt: skip
    result, report = site.run(engine)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _steps(report)["system.time"] == ("done", None)
    peers = "ntp.ornek.example,0x8 time.ume.tubitak.gov.tr,0xA"
    assert report["time"]["peers"] == peers
    status = _system_status(report)
    assert status.time.peers == peers
    assert status.time.changed_at is not None
    times = [line.time for line in _journal(report) if line.change == "time"]
    assert len(times) == 1
    assert times[0] is not None
    assert times[0].peers == peers


@pytest.mark.skipif(sys.platform == "win32", reason="a symbolic link needs a privilege on Windows")
def test_system_sync_refuses_a_staged_client_behind_a_link(engine: str, tmp_path: Path) -> None:
    """A reparse point (a symbolic link or junction) in zamane\\web could make the copy read
    a file elsewhere: the promotion is refused, the run reports system_sync_failed."""
    site = SyncSite(tmp_path)
    elsewhere = tmp_path / "elsewhere.jar"
    elsewhere.write_bytes(KNOWN_CLIENT)
    site.staged.unlink()
    site.staged.symlink_to(elsewhere)
    result, report = site.run(engine)
    assert result.returncode == 1
    assert report["code"] == "system_sync_failed"
    assert report["jars"]["promote"][0]["result"] == "refused: missing or a reparse point"
    assert _steps(report)["jar.promotion"] == ("failed", "system_sync_failed")
    assert _steps(report)["system.firewall"] == ("done", None), "the other steps still run"
    status = _system_status(report)
    assert (status.ok, status.code) == (False, "system_sync_failed")
    assert status.applied_config_sha256 is None, "not applied: what status.json said before"
    assert [a["details"] for a in report["alerts"]] == [{"code": "system_sync_failed"}]
    assert (4107, "Warning") not in _events(report)


def test_system_sync_records_a_new_upload_and_never_runs_it(engine: str, tmp_path: Path) -> None:
    """A staged client that is no known release is only recorded (first_seen, Event 4104);
    it runs after approve-jar on the console. One approved before stays approved, one whose
    content does not match its name is not recorded and reported (payload_mismatch, R20-admin)."""
    site = SyncSite(tmp_path)
    site.plan["problems"] = []
    upload = b"an uploaded client nobody knows"
    (site.web / f"{sha256_bytes(upload)}.jar").write_bytes(upload)
    (site.web / f"{'f6' * 32}.jar").write_bytes(b"content of another hash")
    approved = site.programdata / "zamane" / "approved" / f"{KNOWN_SHA}.jar"
    site.write("jars.json", {
        "schema": "arcilogger.admin-jars/1", "staged": [],
        "approved": [{"sha256": KNOWN_SHA, "path": str(approved),
                      "approved_at": "2026-10-20T09:00:00+03:00",
                      "approved_by": "console:bt-yonetici"}],
    })  # fmt: skip
    parse_document(JarsFile, (site.system / "jars.json").read_bytes())
    result, report = site.run(engine)
    assert result.returncode == 0, result.stdout + result.stderr
    assert report["jars"]["promote"][0]["result"] == "approved already"
    assert report["jars"]["staged"] == [{"sha256": sha256_bytes(upload)}]
    assert [event for event in _events(report) if event[0] != 4106] == [(4104, "Warning")]
    assert "approve-jar" in report["events"][0]["message"]
    assert any("f6f6" in problem and "[not recorded" in problem for problem in report["problems"])
    status = _system_status(report)
    assert (status.ok, status.jars.staged, status.jars.promoted) == (True, 1, 1)
    assert status.code == "payload_mismatch"
    assert [line.change for line in _journal(report)] == ["firewall"]
    assert [(a["rule"], a["details"]) for a in report["alerts"]] == [
        ("R20-admin", {"code": "payload_mismatch"})
    ]
    assert "f6f6" in report["alerts"][0]["message"]


_PROMOTION_SCRIPT = r"""
param([string]$ModulePath, [string]$Root, [string]$OutputPath)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version 2.0
Import-Module $ModulePath -Force
$out = [ordered]@{}
$staged = Join-Path $Root 'staged.jar'
[System.IO.File]::WriteAllBytes($staged, [byte[]](1..64))
$sha = Get-ArciFileSha256 -Path $staged
$approved = Join-Path $Root 'approved'
$target = Join-Path $approved "$sha.jar"
$out['copied'] = Copy-ArciApprovedJar -Source $staged -Target $target -Sha256 $sha
$out['copy hash'] = Get-ArciFileSha256 -Path $target
$out['again'] = Copy-ArciApprovedJar -Source $staged -Target $target -Sha256 $sha
# The staged file changed after it was hashed: the copy does not match and is deleted.
$other = 'ab' * 32
$out['changed'] = Copy-ArciApprovedJar -Source $staged -Target (Join-Path $approved "$other.jar") `
    -Sha256 $other
$out['left'] = @(Get-ChildItem -LiteralPath $approved -Force | ForEach-Object { $_.Name } |
        Sort-Object)
# A file under the hash's name with other content is replaced, never run.
$second = Join-Path $Root 'approved2'
[void](New-Item -ItemType Directory -Path $second)
[System.IO.File]::WriteAllBytes((Join-Path $second "$sha.jar"), [byte[]](9, 9, 9))
$out['replaced'] = Copy-ArciApprovedJar -Source $staged -Target (Join-Path $second "$sha.jar") `
    -Sha256 $sha
$out['replaced hash'] = Get-ArciFileSha256 -Path (Join-Path $second "$sha.jar")

# jars.json: approve-jar wrote meanwhile; both writers' entries stay, the one on disk wins.
function Staged([string]$Sha, [string]$Seen) {
    [pscustomobject][ordered]@{ sha256 = $Sha; first_seen = $Seen }
}
function Approved([string]$Sha, [string]$By) {
    [pscustomobject][ordered]@{
        sha256 = $Sha; path = "C:\z\$Sha.jar"; approved_at = '2026-10-25T10:00:00+03:00'
        approved_by = $By
    }
}
$a = 'a1' * 32
$b = 'b2' * 32
$c = 'c3' * 32
$written = [pscustomobject]@{
    staged = @((Staged $a '2026-10-24T09:00:00+03:00'))
    approved = @((Approved $a 'console:bt-yonetici'))
}
$merged = Merge-ArciJarsDocument -Written $written `
    -Staged @((Staged $a '2026-10-25T09:00:00+03:00'), (Staged $b '2026-10-25T09:05:00+03:00')) `
    -Approved @((Approved $c 'known'), (Approved $a 'known'))
$out['merged'] = $merged
$out['alone'] = Merge-ArciJarsDocument -Written $null `
    -Staged @((Staged $b '2026-10-25T09:05:00+03:00')) -Approved @()

# data-root.txt as the core reads it (config.recorded_data_root), and who may own it.
$data = Join-Path $Root 'data'
$records = [ordered]@{}
function Record([string]$Name, [byte[]]$Bytes, [string]$Owner) {
    $path = Join-Path $Root $Name
    if ($null -ne $Bytes) { [System.IO.File]::WriteAllBytes($path, $Bytes) }
    $arguments = @{ Path = $path }
    if ($Owner) { $arguments['Owner'] = $Owner }
    $found = Read-ArciDataRootRecord @arguments
    $records[$Name] = [ordered]@{
        exists = $found.Exists; value = $found.Value; problem = $found.Problem
    }
}
$utf8 = New-Object System.Text.UTF8Encoding -ArgumentList $false
# The owner as Windows would report it for a file the installer wrote (elevated: Administrators).
$admins = 'S-1-5-32-544'
Record 'good.txt' ([byte[]](@(0xEF, 0xBB, 0xBF) + $utf8.GetBytes("$data`r`n"))) $admins
Record 'two-lines.txt' $utf8.GetBytes("$data`n$data`n") $admins
Record 'relative.txt' $utf8.GetBytes("data`n") $admins
Record 'empty.txt' ([byte[]]@()) $admins
# As Notepad saves "Unicode": UTF-16 with its byte order mark.
$utf16 = [System.Text.Encoding]::Unicode
Record 'utf16.txt' ([byte[]](@($utf16.GetPreamble()) + @($utf16.GetBytes("$data`n")))) $admins
Record 'agent-owned.txt' $utf8.GetBytes("$data`n") (Get-ArciServiceSid 'ArciAdmin')
Record 'missing.txt' $null $admins
$out['records'] = $records

function Facts([string]$Value, [string]$Problem) {
    [pscustomobject]@{ Path = 'data-root.txt'; Exists = $true; Value = $Value; Problem = $Problem }
}
$none = [pscustomobject]@{ Path = 'data-root.txt'; Exists = $false; Value = $null; Problem = $null }
$resolved = [ordered]@{}
foreach ($case in @(
        @('record', (Facts 'D:\ARCILOGGER' $null), 'd:/arcilogger/', '', $false),
        @('moved by the agent', (Facts 'D:\ARCILOGGER' $null), 'E:\ELSEWHERE', '', $false),
        @('moved on purpose', (Facts 'D:\ARCILOGGER' $null), 'E:\ELSEWHERE', 'E:\ELSEWHERE',
            $false),
        @('given differs from the configuration', $none, 'E:\ELSEWHERE', 'F:\OTHER', $false),
        @('record unusable', (Facts $null 'UTF-8 değil [not UTF-8]'), 'D:\ARCILOGGER', '', $false),
        @('record unusable, given', (Facts $null 'UTF-8 değil [not UTF-8]'), 'D:\ARCILOGGER',
            'D:\ARCILOGGER', $false),
        @('before the browser administration', $none, 'D:\ARCILOGGER', '', $false),
        @('record removed', $none, 'D:\ARCILOGGER', '', $true),
        @('first installation', $none, '', '', $false))) {
    $result = Resolve-ArciDataRoot -Record $case[1] -Configured $case[2] -Given $case[3] `
        -AdminInstalled:$case[4]
    $resolved[$case[0]] = [ordered]@{
        path = $result.Path; source = $result.Source; problem = $result.Problem
        replaces = $result.Replaces
    }
}
$out['resolved'] = $resolved

$senders = [ordered]@{}
foreach ($value in @('172.20.10.1', '172.20.10.5/24', '172.20.10.1/32', '10.0.0.0/8',
        ' 172.20.10.1', 'any', '2001:db8::1', '172.20.10')) {
    $senders[$value] = ConvertTo-ArciSyslogSender -Value $value
}
$out['senders'] = $senders
[System.IO.File]::WriteAllText($OutputPath, ($out | ConvertTo-Json -Depth 6))
"""


@pytest.fixture
def promotion(engine: str, tmp_path: Path) -> dict[str, Any]:
    output = tmp_path / "report.json"
    script = write_script(tmp_path / "promotion.ps1", _PROMOTION_SCRIPT)
    result = run_powershell(
        engine, script, "-ModulePath", str(MODULE), "-Root", str(tmp_path), "-OutputPath",
        str(output),
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    report: dict[str, Any] = read_json(output)
    return report


def test_jar_promotion_copies_and_checks_the_copy(promotion: dict[str, Any]) -> None:
    """Contract 2.8.3 step 6 (Copy-ArciApprovedJar): the copy is hashed before it gets its
    name; one that does not match (the staged file changed after it was hashed) is deleted and
    the run fails; a file of other content under the hash's name is replaced."""
    sha = sha256_bytes(bytes(range(1, 65)))
    assert promotion["copied"] is None
    assert promotion["copy hash"] == sha
    assert promotion["again"] is None
    assert "[the copy does not match, deleted]" in promotion["changed"]
    assert promotion["left"] == [f"{sha}.jar"], "no copy and no temporary file stays"
    assert promotion["replaced"] is None
    assert promotion["replaced hash"] == sha


def test_jars_file_keeps_the_entries_of_both_writers(promotion: dict[str, Any]) -> None:
    """Day 0 note D0-16 (Merge-ArciJarsDocument): ApplySystem re-reads jars.json right before
    it writes; an approval approve-jar recorded meanwhile stays, with its approved_by, and so
    does every entry of either side, one per SHA-256."""
    merged = promotion["merged"]
    parse_document(JarsFile, json.dumps(merged).encode())
    a, b, c = "a1" * 32, "b2" * 32, "c3" * 32
    assert [(e["sha256"], e["first_seen"]) for e in merged["staged"]] == [
        (a, "2026-10-24T09:00:00+03:00"), (b, "2026-10-25T09:05:00+03:00"),
    ]  # fmt: skip
    assert [(e["sha256"], e["approved_by"]) for e in merged["approved"]] == [
        (a, "console:bt-yonetici"), (c, "known"),
    ]  # fmt: skip
    assert [e["sha256"] for e in promotion["alone"]["staged"]] == [b]
    assert promotion["alone"]["approved"] == []


def test_data_root_record_is_read_as_the_core_reads_it(
    promotion: dict[str, Any], tmp_path: Path
) -> None:
    """data-root.txt (Day 0 notes D0-2, WP2-1): one line with an absolute path, UTF-8 with or
    without a BOM; anything else fails closed. A record ArciAdmin owns (it may create files in
    that folder) cannot be used either: an owner can change the file's rights."""
    records = promotion["records"]
    data = str(tmp_path / "data")
    assert records["good.txt"] == {"exists": True, "value": data, "problem": None}
    for name in ("two-lines.txt", "relative.txt", "empty.txt"):
        assert "[not one line with an absolute path]" in records[name]["problem"], name
    assert records["utf16.txt"]["problem"] == "UTF-8 değil [not UTF-8]"
    assert "[not owned by Administrators or SYSTEM]" in records["agent-owned.txt"]["problem"]
    assert records["missing.txt"] == {"exists": False, "value": None, "problem": None}


def test_data_root_comes_from_the_record_not_from_the_configuration(
    promotion: dict[str, Any],
) -> None:
    """Resolve-ArciDataRoot: ArciAdmin can write data_dir, so a run never follows a changed one;
    only -DataRoot moves the record, and the record decides when it exists."""
    resolved = promotion["resolved"]
    assert resolved["record"] == {
        "path": "D:\\ARCILOGGER",
        "source": "record",
        "problem": None,
        "replaces": None,
    }
    moved = resolved["moved by the agent"]
    assert moved["path"] is None
    assert "E:\\ELSEWHERE" in moved["problem"]
    assert "D:\\ARCILOGGER" in moved["problem"]
    assert '-DataRoot "E:\\ELSEWHERE"' in moved["problem"]
    assert resolved["moved on purpose"] == {
        "path": "E:\\ELSEWHERE", "source": "parameter", "problem": None,
        "replaces": "D:\\ARCILOGGER",
    }  # fmt: skip
    assert (
        "[differs from data_dir in arcilogger.toml]"
        in (resolved["given differs from the configuration"]["problem"])
    )
    assert "[cannot be used]" in resolved["record unusable"]["problem"]
    assert resolved["record unusable, given"]["path"] == "D:\\ARCILOGGER"
    assert resolved["record unusable, given"]["replaces"] == "(UTF-8 değil [not UTF-8])"
    assert resolved["before the browser administration"]["source"] == "config"
    assert "[the data root record is missing" in resolved["record removed"]["problem"]
    assert resolved["first installation"] == {
        "path": None,
        "source": "auto",
        "problem": None,
        "replaces": None,
    }


def test_source_addresses_as_system_plan_gives_them_to_the_firewall(
    promotion: dict[str, Any],
) -> None:
    """ConvertTo-ArciSyslogSender follows arcilogger_core.admin.systemplan: host bits cleared,
    a /32 as the address; anything but IPv4 is no sender."""
    assert promotion["senders"] == {
        "172.20.10.1": "172.20.10.1", "172.20.10.5/24": "172.20.10.0/24",
        "172.20.10.1/32": "172.20.10.1", "10.0.0.0/8": "10.0.0.0/8",
        " 172.20.10.1": "172.20.10.1", "any": None, "2001:db8::1": None, "172.20.10": None,
    }  # fmt: skip


def _record_setup(root: Path, record: str | None, *, admin: bool = False) -> Path:
    """A bundle and a kept configuration (data_dir = <root>/data/ARCILOGGER), with data-root.txt
    naming ``record`` and, ``admin``, the folder of the browser administration."""
    bundle = make_fake_bundle(root)
    _existing_config(root, 'site_name = "Kurum A.Ş."\n[clock]\nntp_server = "10.0.0.1"\n')
    programdata = root / "programdata" / "ARCILOGGER"
    if record is not None:
        (programdata / "data-root.txt").write_text(record + "\n", encoding="utf-8")
    if admin:
        (programdata / "admin").mkdir()
    return bundle


def test_dry_run_takes_the_data_root_from_its_record(engine: str, tmp_path: Path) -> None:
    """Day 0 note D0-2: ArciAdmin can write data_dir, the record only administrators. A run never
    follows a data_dir that differs from data-root.txt (it would re-record it and point the
    services and tasks at it): it stops before any change and names both; only -DataRoot moves
    the record, and a record removed while the browser administration is installed needs it."""
    data = str(tmp_path / "moved" / "data" / "ARCILOGGER")
    elsewhere = str(tmp_path / "moved" / "elsewhere")
    bundle = _record_setup(tmp_path / "moved", elsewhere)
    result, plan = dry_run(engine, tmp_path / "moved", bundle, data_root=False)
    assert_dry_run_exit(result, plan)
    problem = next(text for text in plan["failures"] if "data-root.txt" in text)
    assert data in problem
    assert elsewhere in problem
    assert f'-DataRoot "{data}"' in problem
    assert plan["paths"]["data_root"] == elsewhere, "the preflight looks at the recorded root"
    # The same move, made deliberately: -DataRoot equal to data_dir writes the record anew.
    result, plan = dry_run(engine, tmp_path / "moved", bundle)
    assert_dry_run_exit(result, plan)
    assert not [text for text in plan["failures"] if "data-root.txt" in text]
    items = {item["id"]: item for item in plan["items"]}
    assert items["preflight.data-root"]["status"] == "warn"
    assert elsewhere in items["preflight.data-root"]["text"]
    assert items["config.data-root"]["detail"] == data
    # The record and data_dir agree: the record decides, nothing is said but where it came from.
    same = tmp_path / "same"
    bundle = _record_setup(same, str(same / "data" / "ARCILOGGER"))
    result, plan = dry_run(engine, same, bundle, data_root=False)
    assert_dry_run_exit(result, plan)
    assert not [text for text in plan["failures"] if "data-root.txt" in text]
    assert plan["paths"]["data_root"] == str(same / "data" / "ARCILOGGER")
    items = {item["id"]: item for item in plan["items"]}
    assert items["preflight.data-root"]["status"] == "ok"
    # A record that cannot be used, or none although the administration folder exists.
    for name, record, admin, words in (
        ("broken", "relative/path", False, "[cannot be used]"),
        ("removed", None, True, "[the data root record is missing"),
    ):
        root = tmp_path / name
        bundle = _record_setup(root, record, admin=admin)
        result, plan = dry_run(engine, root, bundle, data_root=False)
        assert_dry_run_exit(result, plan)
        assert any(words in text for text in plan["failures"]), (name, plan["failures"])


def test_installer_creates_the_data_root_record_anew() -> None:
    """WP6 Day 0 note: the record is deleted and written again (owner: Administrators), never
    rewritten in place: a record ArciAdmin created while it was missing would stay its own."""
    text = INSTALL_SCRIPT.read_text(encoding="utf-8-sig")
    step = _between(text, "Invoke-Step 'config.data-root'", "} $DataRoot")
    assert step.index("Remove-Item -LiteralPath $DataRootRecord") < step.index(
        "Write-ArciUtf8File -Path $DataRootRecord"
    )


@pytest.mark.parametrize(
    ("sources", "extra", "status"),
    [
        (["172.20.10.1", "198.51.100.7"], (), "fail"),
        (["10.0.0.0/8"], (), "fail"),
        (
            ["172.20.10.1", "198.51.100.7"],
            ("-SourceNetwork", "172.16.0.0/12,198.51.100.0/24"),
            None,
        ),
        (["172.20.10.5/24"], (), None),
        (["198.51.100.7"], ("-DisableBrowserAdministration",), "warn"),
        (["198.51.100.7"], ("-SkipFirewall",), "warn"),
    ],
    ids=["outside", "too-wide", "given-networks", "host-bits", "administration-off", "no-firewall"],
)
def test_dry_run_stops_for_a_source_outside_the_policy_networks(
    engine: str, tmp_path: Path, sources: list[str], extra: tuple[str, ...], status: str | None
) -> None:
    """ApplySystem opens 514 only to the senders policy.json allows: an enabled source outside
    the chosen networks (or wider than /16) would lose its opening at the first run after the
    upgrade, with no change in the browser. The preflight stops before any change and names the
    -SourceNetwork to give; where ApplySystem cannot narrow the rule it only warns."""
    bundle = make_fake_bundle(tmp_path, version="9.9.10")
    program = write_version(tmp_path / "program" / "ARCILOGGER", "9.9.9", "1" * 64)
    (program / "venv" / "Scripts").mkdir(parents=True)
    (program / "venv" / "Scripts" / "python.exe").symlink_to(sys.executable)
    body = 'site_name = "Kurum A.Ş."\n[clock]\nntp_server = "10.0.0.1"\n'
    for i, address in enumerate(sources):
        body += f'[[sources]]\nname = "s{i}"\ntype = "fortigate"\naddress = "{address}"\n'
    body += '[[sources]]\nname = "off"\ntype = "generic"\naddress = "203.0.113.9"\n'
    body += "enabled = false\n"
    _existing_config(tmp_path, body)
    result, plan = dry_run(engine, tmp_path, bundle, "-Upgrade", *extra, site_name=None)
    assert_dry_run_exit(result, plan)
    checks = [i for i in plan["items"] if i["id"] == "preflight.admin-policy-sources"]
    if status is None:
        assert checks == []
        return
    assert [check["status"] for check in checks] == [status]
    text = checks[0]["text"]
    assert "203.0.113.9" not in text, "a source switched off gets no opening anyway"
    if sources == ["10.0.0.0/8"]:
        assert "10.0.0.0/8: /8" in text
        assert "min_prefix_v4" in text
        assert "-SourceNetwork 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16." in text
    else:
        assert "198.51.100.7: izin verilen ağların dışında" in text
        if status == "fail":
            assert "-SourceNetwork 10.0.0.0/8,172.16.0.0/12,192.168.0.0/16,198.51.100.7" in text
    assert (text in plan["failures"]) == (status == "fail")
