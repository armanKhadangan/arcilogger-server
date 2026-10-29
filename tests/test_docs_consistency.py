"""The operator and reference documents match the code they describe.

Documentation is part of the product here: the operator installs and runs the server from
docs/KURULUM.md and docs/ILK-ADIMLAR.md alone (offline bundle), and an auditor reads the verifier
output with docs/DENETCI-KILAVUZU.md. These checks fail when the code moves and the documents do
not (or the other way round): links, the commands the operator types, the tables, settings, jobs
and verifier codes the documents list, and the legal wording rules of the build specification.
"""

from __future__ import annotations

import argparse
import ast
import codecs
import importlib
import importlib.util
import inspect
import ipaddress
import itertools
import json
import re
import subprocess
import sys
import textwrap
import tomllib
import typing
from collections.abc import Callable, Iterator, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import pytest
from fastapi.routing import APIRoute
from pydantic import BaseModel

from arcilogger_api import app as api_app
from arcilogger_api.app import API_PREFIX
from arcilogger_api.cli import _build_parser as api_parser
from arcilogger_api.licensing import MODULE_MARKER
from arcilogger_api.routers import discover
from arcilogger_api.routers.audit import AuditEntry
from arcilogger_api.routers.devices import assign_devices
from arcilogger_api.routers.usage import MAX_LIMIT as USAGE_MAX_LIMIT
from arcilogger_api.routers.usage import PERSON_PERMISSIONS
from arcilogger_api.routers.vlans import router as vlans_router
from arcilogger_api.security import ROLE_PERMISSIONS
from arcilogger_collector.service import unknown_source_name
from arcilogger_core.admin.contract import EVENT_SOURCE, Event
from arcilogger_core.cli import _build_parser as core_parser
from arcilogger_core.cli import main as core_main
from arcilogger_core.config import BackupConfig, Config, IngestConfig, WorkerConfig
from arcilogger_core.license import LICENSOR_EMAIL, LICENSOR_NAME, TRIAL_DAYS
from arcilogger_core.license.errors import HTTP_STATUS, MESSAGES
from arcilogger_core.license.modules import CORE_MODULE, MODULES
from arcilogger_core.license.state import (
    SOURCE_ACTIVE_DAYS,
    TRIAL_CLOCK_TOLERANCE,
    UNLICENSED_USERS,
)
from arcilogger_db.cli import _build_parser as db_parser
from arcilogger_db.models import LICENSE_MAX_BYTES, Base
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_worker.alerts.mail import MAX_LISTED, MailItem, render_text, subject
from arcilogger_worker.alerts.texts import (
    DESCRIBERS,
    LEASE_CAUSES,
    NAT_CAUSES,
    RULES,
    describe,
    source_name,
)
from arcilogger_worker.cli import _build_parser as worker_parser
from arcilogger_worker.reports.runner import REPORT_RULE, UNLICENSED_NOTE
from arcilogger_worker.service import default_jobs
from arcilogger_worker.usage.query import USERS_MAX

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
INSTALLER = ROOT / "deploy" / "windows" / "installer"
#: The PowerShell scripts whose parameters the documents name.
SCRIPTS = {
    "Install-Arcilogger.ps1": INSTALLER / "Install-Arcilogger.ps1",
    "Set-ArciFirewall.ps1": INSTALLER.parent / "scripts" / "Set-ArciFirewall.ps1",
}
VERIFY_PY = ROOT / "core" / "src" / "arcilogger_core" / "evidence" / "standalone" / "verify.py"

#: The customer's licence guide (offline licensing, ADR-0013), Turkish and English.
LICENSE_DOCS = ("LISANS.md", "LICENSING.md")
#: Operator documents shipped in the offline bundle and read on the server.
OPERATOR_DOCS = (
    "KURULUM.md",
    "INSTALL.md",
    "ILK-ADIMLAR.md",
    "FIRST-STEPS.md",
    "DEVICE-SETUP.md",
    "kamusm.md",
    "DENETCI-KILAVUZU.md",
    *LICENSE_DOCS,
)
#: The database backup runbooks (ADR-0009), read on the server like the operator documents.
BACKUP_DOCS = ("YEDEKLEME.md", "BACKUP.md")
#: The guide to the Settings pages (browser administration, ADR-0014), read like the operator
#: documents.
SETTINGS_DOCS = ("AYARLAR.md", "SETTINGS.md")
LEGAL_DRAFTS = ("legal/aydinlatma-metni-taslak.md", "legal/saklama-imha-politikasi-taslak.md")
DRAFT_MARKER = "**Taslak — hukuk müşaviri onayı gerekir**"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _doc(name: str) -> str:
    return _read(DOCS / name)


def _flat(text: str) -> str:
    """Whitespace collapsed, so a phrase is found across line breaks."""
    return " ".join(text.split())


def _section(text: str, start: str, end: str) -> str:
    begin = text.index(start)
    return text[begin : text.index(end, begin)]


def _code_blocks(text: str) -> list[str]:
    return re.findall(r"^```[^\n]*\n(.*?)^```", text, flags=re.MULTILINE | re.DOTALL)


# -- links ----------------------------------------------------------------------------------------


def _markdown_files() -> list[Path]:
    files = [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted(DOCS.rglob("*.md"))]
    return files + sorted((ROOT / "tools").rglob("README.md"))


@pytest.mark.parametrize("path", _markdown_files(), ids=lambda p: p.relative_to(ROOT).as_posix())
def test_relative_markdown_links_resolve(path: Path) -> None:
    broken = []
    for target in re.findall(r"\]\(([^)\s]+)\)", _read(path)):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        file_part = target.split("#", 1)[0]
        if not (path.parent / file_part).exists():
            broken.append(target)
    assert broken == [], f"{path.name}: broken relative links {broken}"


# -- first administrator: one command, everywhere (findings: first admin) ------------------------


def _installer_command() -> str:
    """The setup-token command, built from the installer's own default paths."""
    script = _read(INSTALLER / "Install-Arcilogger.ps1")
    install_dir = re.search(r"\[string\]\$InstallDir = .*?else \{ '([^']+)' \}", script)
    program_data = re.search(r"\[string\]\$ProgramDataDir = .*?else \{ '([^']+)' \}", script)
    assert install_dir is not None, "installer default -InstallDir not found"
    assert program_data is not None, "installer default -ProgramDataDir not found"
    assert "$Venv = Join-Path $InstallDir 'venv'" in script
    assert "Join-Path $Venv 'Scripts\\arcilogger-core.exe'" in script
    assert "$ConfigPath = Join-Path $ProgramDataDir 'arcilogger.toml'" in script
    exe = install_dir.group(1) + "\\venv\\Scripts\\arcilogger-api.exe"
    config = program_data.group(1) + "\\arcilogger.toml"
    return f'& "{exe}" setup-token --config "{config}"'


SETUP_COMMAND = _installer_command()


@pytest.mark.parametrize("name", ["KURULUM.md", "INSTALL.md", "ILK-ADIMLAR.md", "FIRST-STEPS.md"])
def test_operator_docs_give_the_full_setup_token_command(name: str) -> None:
    text = _doc(name)
    assert SETUP_COMMAND in text
    assert "create-admin" in text
    flat = _flat(text)
    for stale in (
        "milestone M6",
        "ilk yönetici hesabının nasıl oluşturulduğu (M6)",
        "how the first administrator is created (M6)",
        "first-run step of the authentication module",
    ):
        assert stale not in flat, f"{name}: {stale!r}"


def test_security_doc_and_web_setup_page_show_the_same_command() -> None:
    assert SETUP_COMMAND in _doc("SECURITY.md")
    page = _read(ROOT / "web" / "src" / "pages" / "setup" / "setup-page.tsx")
    literal = re.search(r"const SETUP_TOKEN_COMMAND =\s*'([^']*)';", page)
    assert literal is not None
    assert literal.group(1).replace("\\\\", "\\") == SETUP_COMMAND
    assert 'code="arcilogger-api setup-token"' not in page


@pytest.mark.parametrize("name", OPERATOR_DOCS + BACKUP_DOCS + SETTINGS_DOCS)
def test_operator_code_blocks_never_call_a_program_by_its_bare_name(name: str) -> None:
    """The programs are not on PATH on the server: a bare command fails there."""
    bare = re.compile(r"^\s*arcilogger-(core|api|ingest|db|worker|collector)\b")
    offending = [
        line.strip()
        for block in _code_blocks(_doc(name))
        for line in block.splitlines()
        if bare.match(line)
    ]
    assert offending == [], f"{name}: {offending}"


def test_offline_bundle_ships_the_whole_docs_tree() -> None:
    """The bundle copies every document of docs\\ with its subfolders (adr\\, legal\\), so the
    links between the operator documents work on the offline server too."""
    script = _read(INSTALLER / "Build-ArciBundle.ps1")
    assert "$docsSource = (Resolve-Path -LiteralPath (Join-Path $RepoRoot 'docs'))" in script
    assert "Get-ChildItem -LiteralPath $docsSource -Recurse -File -Filter '*.md'" in script
    assert "$target = Join-Path $docsStage $_.FullName.Substring($docsSource.Length + 1)" in script
    required = re.search(r"foreach \(\$doc in @\(([^)]*)\)\)", script)
    assert required is not None
    names = set(re.findall(r"'([^']+)'", required.group(1)))
    assert set(OPERATOR_DOCS) <= names
    assert all((DOCS / name).is_file() for name in names), names
    # The installer copies the bundle's docs folder with its subfolders.
    install = _read(INSTALLER / "Install-Arcilogger.ps1")
    assert (
        "foreach ($folder in @('installer', 'scripts', 'tasks', 'config', 'docs', 'licenses'))"
        in install
    )
    assert "Copy-Item -LiteralPath $source -Destination $InstallDir -Recurse -Force" in install


@pytest.mark.parametrize(
    "path", sorted(DOCS.rglob("*.md")), ids=lambda p: p.relative_to(ROOT).as_posix()
)
def test_bundled_documents_link_only_inside_the_bundle(path: Path) -> None:
    """A link from docs\\ to a file outside it (README.md, tools\\) is dead in the bundle."""
    outside = []
    for target in re.findall(r"\]\(([^)\s]+)\)", _read(path)):
        if re.match(r"^[a-z]+:", target) or target.startswith("#"):
            continue
        resolved = (path.parent / target.split("#", 1)[0]).resolve()
        if DOCS.resolve() not in resolved.parents or not resolved.is_file():
            outside.append(target)
    assert outside == [], f"{path.name}: links that leave docs/ {outside}"


def _show_next_steps() -> str:
    return _section(_read(INSTALLER / "Install-Arcilogger.ps1"), "function Show-NextSteps", "\n}\n")


def test_installer_next_steps_put_the_roots_before_the_connection_test() -> None:
    steps = _show_next_steps()
    assert steps.index("kamusm-roots") < steps.index("secret set kamusm.customer")
    assert steps.index("kamusm-roots") < steps.index("tsa-test")
    assert "docs\\ILK-ADIMLAR.md" in steps


# -- first steps after the installation ----------------------------------------------------------


@pytest.mark.parametrize("name", ["ILK-ADIMLAR.md", "FIRST-STEPS.md"])
def test_first_steps_cover_the_path_to_the_first_lookup(name: str) -> None:
    text = _doc(name)
    for needed in (
        SETUP_COMMAND,
        "Restart-Service ArciApi",
        "first_day",
        "legal_root_fingerprints",
        "& $db settings set",
        "Set-ArciFirewall.ps1",
        "alerts.jsonl",
        "secret.key",
        "5651",
        "legal/saklama-imha-politikasi-taslak.md",
    ):
        assert needed in text, f"{name}: {needed!r} missing"


@pytest.mark.parametrize("name", ["ILK-ADIMLAR.md", "FIRST-STEPS.md"])
def test_first_steps_leave_the_chain_start_to_the_owner(name: str) -> None:
    """Both options with their trade-offs; neither is presented as "the legal one" (spec 0/21)."""
    section = _flat(_section(_doc(name), "## 3.", "## 4."))
    for steering in ("temiz bir yasal zincir", "clean legal chain", "önerilen", "recommended"):
        assert steering not in section, steering
    assert "first_day" in section
    assert ("sahibin kararıdır" in section) or ("the owner's" in section)
    # Pilot stamps verify only while their root stays pinned (K10, verify on site).
    assert "kamusm-roots" in section
    assert "K10" in section


@pytest.mark.parametrize("name", ["ILK-ADIMLAR.md", "FIRST-STEPS.md", "kamusm.md"])
def test_mock_stamps_are_not_described_as_merely_test_stamped(name: str) -> None:
    """The mock root is trusted only while provider = "mock" (decision 5): after the switch to
    Kamu SM, mock-stamped days fail verification instead of staying "test stamped"."""
    text = _flat(_doc(name))
    for stale in (
        "mock damgalı günler zincirde **kalıcı olarak** test damgalı",
        "mock damgalı günlerin yasal geçerliliği **hiçbir zaman** olmaz ve bu işaret kalıcıdır",
        "mock-stamped days **never** have legal validity, and the mark is permanent",
    ):
        assert stale not in text, stale
    assert 'provider = "mock"' in text
    assert "R20-verify" in text


#: Where the evidence path and the services raise alerts (``state/alerts.jsonl``).
ALERT_PACKAGES = ("core", "collector", "ingest", "worker", "api", "db")


def _alert_codes() -> set[str]:
    """Every alert rule the code can write: rule-id constants (``R20-…``, also behind module
    constants such as ``ALERT_RULE``) and the literal rules given to ``raise_alert`` or a
    service's ``_alert`` wrapper (``new-log-source``)."""
    codes: set[str] = set()
    for package in ALERT_PACKAGES:
        for path in sorted((ROOT / package / "src").rglob("*.py")):
            for node in ast.walk(ast.parse(_read(path))):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and re.fullmatch(r"R\d{2}-[a-z]+(?:-[a-z]+)*", node.value)
                ):
                    codes.add(node.value)
                if not isinstance(node, ast.Call):
                    continue
                func = node.func
                name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                position = {"raise_alert": 1, "_alert": 0}.get(name or "")
                if position is not None and len(node.args) > position:
                    rule = node.args[position]
                    if isinstance(rule, ast.Constant) and isinstance(rule.value, str):
                        codes.add(rule.value)
    return codes


@pytest.mark.parametrize("name", ["ILK-ADIMLAR.md", "FIRST-STEPS.md"])
def test_first_steps_explain_every_alert_code(name: str) -> None:
    codes = _alert_codes()
    assert {"R20-seal", "R20-verify", "R20-retention", "new-log-source"} <= codes
    section = _section(_doc(name), "## 9.", "## 10.")
    documented = set(re.findall(r"`([A-Za-z0-9-]+)`", section))
    assert sorted(codes - documented) == [], f"add these alert codes to {name} section 9"


SEAL_CONFIG = """
data_dir = "{data}"

[tsa]
{tsa}
"""


@pytest.fixture
def _restore_logging() -> typing.Iterator[None]:
    """``arcilogger-core`` replaces the root logger's handlers (``force=True``)."""
    import logging

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    yield
    for handler in root.handlers:
        if handler not in handlers:
            handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


@pytest.mark.usefixtures("_restore_logging")
@pytest.mark.parametrize(
    ("tsa", "error", "cause"),
    [
        (
            'provider = "zamane"\nzamane_jar = "{jar}"',
            "customer number / password not stored",
            "credentials",
        ),
        ('provider = "zamane"', "tsa.zamane_jar is not configured", "zamane-jar"),
        (
            'provider = "rfc3161"\nurl = "http://127.0.0.1:9"',
            "no pinned root certificates",
            "roots",
        ),
    ],
    ids=["no-credentials", "no-zamane-jar", "no-roots"],
)
def test_sealing_configuration_errors_alert_once_a_day(
    tmp_path: Path, tsa: str, error: str, cause: str
) -> None:
    """What ILK-ADIMLAR / FIRST-STEPS sections 3, 7 and 9 and the KURULUM / INSTALL
    troubleshooting row tell the operator: a configuration error stops the Seal task before
    sealing starts — exit code 1 (Task Scheduler result 0x1), the error in logs\\core.log on
    every run, and **one** ``R20-seal-config`` alert per day and cause (the task runs every 5
    minutes). Should this change, update those documents."""
    data = tmp_path / "data"
    config = tmp_path / "arcilogger.toml"
    jar = (tmp_path / "tss-client-console.jar").as_posix()
    config.write_text(
        SEAL_CONFIG.format(data=data.as_posix(), tsa=tsa.format(jar=jar)), encoding="utf-8"
    )
    assert core_main(["--config", str(config), "seal"]) == 1
    assert core_main(["--config", str(config), "seal"]) == 1  # the next run: no second alert
    lines = (data / "state" / "alerts.jsonl").read_text(encoding="utf-8").splitlines()
    alerts = [json.loads(line) for line in lines]
    assert [(a["rule"], a["severity"], a["details"]["cause"]) for a in alerts] == [
        ("R20-seal-config", "high", cause)
    ]
    assert "Sealing cannot start" in alerts[0]["message"]
    log = (data / "logs" / "core.log").read_text(encoding="utf-8")
    assert log.count("command seal failed") == 2
    assert error in log
    for name in ("ILK-ADIMLAR.md", "FIRST-STEPS.md", "KURULUM.md", "INSTALL.md"):
        assert "core.log" in _doc(name), name
        assert "R20-seal-config" in _doc(name), name


# -- configuration keys and commands the documents tell the operator to use ----------------------

#: Names the documents use that parallel branches of this release add (orchestrator decisions 4,
#: 5, 6 and 9, the evidence decision 1 and the product decisions of 2026-10-20, ASSUMPTIONS
#: P1-P4). The documents are merged together with those branches; once a name exists here its
#: entry must be removed, which makes the checks below strict again
#: (test_pending_names_are_removed_once_they_exist).
PENDING_FROM_PARALLEL_BRANCHES: dict[str, str] = {}


def _model_of(annotation: object) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    for argument in typing.get_args(annotation):
        model = _model_of(argument)
        if model is not None:
            return model
    return None


def _config_keys() -> set[str]:
    keys = set()
    for section, field in Config.model_fields.items():
        model = _model_of(field.annotation)
        if model is not None:
            keys |= {f"{section}.{key}" for key in model.model_fields}
    return keys


def _documented_config_keys() -> set[str]:
    """``[section] key`` and ``[[section]] key`` in every document, plus keys the documents
    give only in prose or TOML snippets."""
    found = {"tsa.legal_root_fingerprints", "sources.transport", "seal.first_day"}
    for path in [ROOT / "README.md", *sorted(DOCS.rglob("*.md"))]:
        for match in re.finditer(r"`\[\[?([a-z_]+)\]\]? ([a-z_]+)\b", _read(path)):
            found.add(f"{match.group(1)}.{match.group(2)}")
    return found


def _subcommand(parser: argparse.ArgumentParser, *names: str) -> argparse.ArgumentParser | None:
    for name in names:
        actions = [a for a in parser._actions if isinstance(a, argparse._SubParsersAction)]
        choices = actions[0].choices if actions else {}
        if name not in choices:
            return None
        parser = choices[name]
    return parser


def _script_parameter(script: str, name: str) -> str | None:
    """What follows ``$name`` on its line in the ``param(…)`` block of a PowerShell script: ``","``
    for a parameter without a default, ``" = …,"`` for one with a default; None when the script
    has no such parameter."""
    block = re.search(r"^param\($(.*?)^\)$", _read(SCRIPTS[script]), flags=re.MULTILINE | re.DOTALL)
    assert block is not None, f"{script}: no param block"
    found = re.search(rf"^\s*\[[\w.\[\]]+\]\${name}\b(.*)$", block.group(1), flags=re.MULTILINE)
    return None if found is None else found.group(1)


def _exists(name: str) -> bool:
    kind, _, what = name.partition(":")
    if kind == "config":
        return what in _config_keys()
    if kind == "installer":  # a parameter declared without a default
        script, _, parameter = what.partition(" -")
        declaration = _script_parameter(script, parameter)
        return declaration is not None and "=" not in declaration
    if kind == "empty-default":  # a configuration key whose default is empty
        section, _, key = what.rpartition(".")
        field = Config.model_fields[section or key]
        default = getattr(field.default, key) if section else field.default
        return bool(default == "")
    if kind == "attr":
        module, _, attribute = what.rpartition(".")
        return hasattr(importlib.import_module(module), attribute)
    if kind == "admin":  # a console command of python -I -m arcilogger_core.admin
        if importlib.util.find_spec("arcilogger_core.admin.__main__") is None:
            return False
        command = [sys.executable, "-I", "-m", "arcilogger_core.admin", *what.split(), "--help"]
        return subprocess.run(command, capture_output=True, check=False).returncode == 0
    if what == "arcilogger-core config-check":
        return _subcommand(core_parser(), "config-check") is not None
    if what == "arcilogger-db settings":
        return _subcommand(db_parser(), "settings") is not None
    if what == "arcilogger-core evidence build --include-audit-log":
        build = _subcommand(core_parser(), "evidence", "build")
        assert build is not None
        return any("--include-audit-log" in a.option_strings for a in build._actions)
    raise AssertionError(name)


def test_documented_configuration_keys_exist() -> None:
    """``arcilogger.toml`` rejects unknown keys: a misnamed key in a document stops every
    service and task that reads the file."""
    keys = _config_keys()
    assert {"tsa.roots_dir", "seal.first_day", "ingest.workers"} <= keys
    missing = sorted(
        key
        for key in _documented_config_keys()
        if key not in keys and f"config:{key}" not in PENDING_FROM_PARALLEL_BRANCHES
    )
    assert missing == [], f"documented but not in arcilogger_core.config: {missing}"


def test_documented_commands_exist() -> None:
    for name in (
        "cli:arcilogger-db settings",
        "cli:arcilogger-core evidence build --include-audit-log",
    ):
        assert _exists(name) or name in PENDING_FROM_PARALLEL_BRANCHES, name
    assert "& $db settings set" in _doc("ILK-ADIMLAR.md")
    assert "--include-audit-log" in _doc("DENETCI-KILAVUZU.md")


def test_pending_names_are_removed_once_they_exist() -> None:
    implemented = sorted(name for name in PENDING_FROM_PARALLEL_BRANCHES if _exists(name))
    assert implemented == [], (
        f"now implemented, remove from PENDING_FROM_PARALLEL_BRANCHES: {implemented}"
    )


@pytest.mark.parametrize("name", BACKUP_DOCS)
def test_backup_runbooks_match_the_commands_and_keys(name: str) -> None:
    """Every ``arcilogger-db backup`` action and every ``[backup]`` key is in both runbooks,
    with the state file, the alert and the offline copy of the key."""
    text = _doc(name)
    backup = _subcommand(db_parser(), "backup")
    assert backup is not None
    (actions,) = [a for a in backup._actions if isinstance(a, argparse._SubParsersAction)]
    assert set(actions.choices) == {"run", "list", "verify", "prune", "restore"}
    for action in actions.choices:
        assert f"& $db backup {action}" in text, action
    for key in BackupConfig.model_fields:
        assert f"`[backup] {key}`" in text, key
    for needed in (
        "state\\backup\\last.json",
        "R20-backup",
        "secret.key",
        "--target-db",
        "backups\\db",
    ):
        assert needed in text, needed
    for name_ in ("ILK-ADIMLAR.md", "FIRST-STEPS.md"):
        section = _section(_doc(name_), "## 10.", "## 11.")
        assert "& $db backup run" in section, name_
        assert ("YEDEKLEME.md" in section) or ("BACKUP.md" in section), name_


@pytest.mark.parametrize("name", ["KURULUM.md", "INSTALL.md"])
def test_kamusm_roots_come_before_the_connection_test(name: str) -> None:
    """tsa-test spends a credit before it checks the roots: roots first."""
    section = _section(_doc(name), "## 10.", "## 11.")
    assert section.index("kamusm-roots") < section.index("& $core tsa-test")
    assert "Restart-Service ArciApi" in section
    assert 'url = "http://zd.kamusm.gov.tr"' in section
    assert "legal_root_fingerprints" in section


@pytest.mark.parametrize("name", ["KURULUM.md", "INSTALL.md"])
def test_install_guides_name_the_permanent_kamusm_flows(name: str) -> None:
    text = _doc(name)
    assert "zd.kamusm.gov.tr" in _section(text, "#", "## 1.")
    assert "Add-MpPreference -ExclusionPath 'D:\\ARCILOGGER\\db'" in text
    assert "Restart-Service ArciCollector, ArciIngest, ArciApi, ArciWorker" in text


def test_no_document_says_the_server_never_needs_the_internet() -> None:
    for name in ("ASSUMPTIONS.md", "adr/0007-windows-installer.md", "KURULUM.md", "INSTALL.md"):
        flat = _flat(_doc(name))
        for stale in (
            "no internet access at all",
            "internet access only during installation",
            "Sunucunun internete çıkması gerekmez",
            "yalnızca güncelleme sırasında internete çıkar",
            "The server needs no internet access",
        ):
            assert stale not in flat, f"{name}: {stale!r}"


def test_device_setup_explains_the_firewall_in_front_of_unknown_senders() -> None:
    section = _section(_doc("DEVICE-SETUP.md"), "## 4.", "İlk gerçek MikroTik")
    assert "unknown-<ip>" in section
    assert "Set-ArciFirewall.ps1" in section
    assert "Restart-Service ArciCollector" in section
    assert 'transport = "tcp"' in section


def test_oui_file_is_recommended_where_the_worker_can_read_it() -> None:
    text = _doc("CORRELATION.md")
    assert "**`C:\\ProgramData\\ARCILOGGER\\oui.csv`**" in text
    assert "`D:\\ARCILOGGER\\config\\oui.csv`" not in text
    # The reason the document gives: the installer's ACL plan grants the worker inherited read
    # on ProgramData, but only "this folder" on the data root.
    plan = _read(INSTALLER / "ArciInstall.psm1")
    program_data = _section(plan, "(New-Entry $ProgramDataDir 'Directory'", "(New-Entry (")
    data_root = _section(plan, "(New-Entry $DataRoot 'Directory'", "(New-Entry (")
    worker = "-Who 'NT SERVICE\\ArciWorker' -Rights $read"
    assert f"{worker})" in program_data
    assert f"{worker} -ThisFolderOnly)" in data_root


# -- README, CHANGELOG, ARCHITECTURE, decisions ---------------------------------------------------


def _placeholder_labels() -> tuple[set[str], set[str]]:
    # The set of built pages: `BUILT` in routes.tsx, or `BUILT_PAGES` once it moved to nav.ts.
    nav = _read(ROOT / "web" / "src" / "nav.ts")
    sources = _read(ROOT / "web" / "src" / "routes.tsx") + "\n" + nav
    built = re.search(r"const BUILT(?:_PAGES)?\b[^=]*= new Set\(\[([^\]]*)\]\)", sources)
    assert built is not None
    built_keys = set(re.findall(r"'(\w+)'", built.group(1)))
    main_menu = nav.split("export const NAV_ITEMS", 1)[1].split("export const UI_KIT_ITEM", 1)[0]
    keys = re.findall(r"key: '(\w+)'", main_menu)
    labels = json.loads(_read(ROOT / "web" / "src" / "locales" / "tr.json"))["nav"]
    placeholders = {labels[key] for key in keys if key not in built_keys}
    working = {labels[key] for key in keys if key in built_keys}
    return placeholders, working


def test_readme_status_names_the_real_placeholders() -> None:
    readme = _read(ROOT / "README.md")
    after = _flat(readme).split("Yer tutucu sayfalar:", 1)[1]
    placeholder_part = after.split("Çalışan sayfalar:", 1)[0]
    placeholders, working = _placeholder_labels()
    for label in placeholders:
        assert label in placeholder_part, label
    for label in working:
        assert label not in placeholder_part, label
    assert "| sırada |" not in readme
    assert "docs/KURULUM.md" in readme
    assert "docs/ILK-ADIMLAR.md" in readme


def test_changelog_describes_the_shipped_version() -> None:
    version = tomllib.loads(_read(ROOT / "pyproject.toml"))["project"]["version"]
    changelog = _read(ROOT / "CHANGELOG.md")
    assert f"version {version}" in changelog
    assert "Known limitations" in changelog


def test_architecture_and_decisions_are_current() -> None:
    architecture = _doc("ARCHITECTURE.md")
    for stale in ("M1 tamam", "ADR in M3", "PostgreSQL 16 + TimescaleDB"):
        assert stale not in architecture, stale
    assert "reportlab" in architecture
    assert "exports\\reports\\" in architecture
    assert "Status: proposed" not in _doc("adr/0005-database-and-mvp-contracts.md")
    assert "planned" in _doc("adr/0003-integrity-on-windows.md")
    adr7 = _section(_doc("adr/0007-windows-installer.md"), "### 8.", "## Consequences")
    assert "To be agreed between the core and ingest owners" not in adr7
    assumptions = _doc("ASSUMPTIONS.md")
    for row in ("| D3 |", "| D4 |", "| D5 |"):
        assert row in assumptions
    contracts = _flat(_doc("MVP-CONTRACTS.md"))
    for stale in ("before B2 is merged", "Until the owner decides", "(stub, exit 2"):
        assert stale not in contracts, stale


def test_security_docs_match_the_implemented_reauthentication() -> None:
    security = _flat(_doc("SECURITY.md"))
    assert "ileride kanıt indirme" not in security
    assert "move the flush after the connection release" not in security
    assert "later: evidence download" not in _flat(_doc("roles.md"))


# -- reference tables -----------------------------------------------------------------------------


def test_data_model_lists_every_table() -> None:
    text = _doc("DATA-MODEL.md")
    missing = [table for table in Base.metadata.tables if f"`{table}`" not in text]
    assert missing == []


def test_usage_docs_give_the_page_size_of_a_whole_list() -> None:
    hooks = _read(ROOT / "web" / "src" / "lib" / "api-hooks.ts")
    found = re.search(r"^export const ALL_PAGES_LIMIT = ([\d_]+);$", hooks, flags=re.MULTILINE)
    assert found is not None
    limit = int(found[1])
    assert limit == USAGE_MAX_LIMIT
    assert "export const ALL_PAGES_MAX = 1;" in hooks
    wu3 = next(line for line in _doc("ASSUMPTIONS.md").splitlines() if line.startswith("| WU3 |"))
    assert f"in one request (`limit` {limit:,}" in wu3
    for name in ("MVP-CONTRACTS.md", "USAGE.md"):
        assert f"`limit` 1-{limit:,}" in _flat(_doc(name)), name
    assert f"`limit` 1-{limit:,}".replace(",", ".") in _flat(_doc("KULLANIM.md"))


def test_assumptions_give_the_columns_of_a_site_or_category_users() -> None:
    """U13, a default the owner confirms, says what ``query._restricted_users`` answers: a
    category's users have no online time, no user has blocked attempts or a first/last activity
    there, and the detail lists :data:`USERS_MAX` of them (MVP-CONTRACTS section 12)."""
    u13 = next(line for line in _doc("ASSUMPTIONS.md").splitlines() if line.startswith("| U13 |"))
    assert "(online = raw time, active = time on it)" not in u13
    for fact in (
        "online = a site's raw time",
        "**0 for a category**",
        "blocked attempts 0 and no first/last activity",
        f"the {USERS_MAX} users with the most time (`users_total` counts all)",
        "the exports all of them",
    ):
        assert fact in u13, fact
    contracts = _flat(_doc("MVP-CONTRACTS.md"))
    assert f"`users` ≤ {USERS_MAX}" in contracts
    assert "active = time in it, online 0" in contracts


def test_correlation_lists_every_worker_job_and_setting() -> None:
    text = _doc("CORRELATION.md")
    jobs = [job.name for job in default_jobs(60)]
    assert [name for name in jobs if f"`{name}`" not in text] == []
    assert "hook only" not in text
    prefixes = ("nat.", "lookup.", "lease.", "dhcp.", "arp.", "devices.", "worker.", "db.")
    settings = [key for key in DEFAULT_SETTINGS if key.startswith(prefixes)]
    assert [key for key in settings if f"`{key}`" not in text] == []
    assert "not yet in `DEFAULT_SETTINGS`" not in text


def test_ingest_and_worker_keys_are_documented() -> None:
    contracts = _doc("MVP-CONTRACTS.md")
    ingest_row = next(line for line in contracts.splitlines() if line.startswith("| `[ingest]`"))
    worker_row = next(line for line in contracts.splitlines() if line.startswith("| `[worker]`"))
    ingest_doc = _doc("INGEST.md")
    example = _read(ROOT / "deploy" / "windows" / "arcilogger.example.toml")
    for field in IngestConfig.model_fields:
        assert f"`{field}`" in ingest_row, field
        assert f"`{field}`" in ingest_doc, field
        assert re.search(rf"^{field} = ", example, flags=re.MULTILINE), field
    for field in WorkerConfig.model_fields:
        assert f"`{field}`" in worker_row, field


# -- auditor guide: every verifier code -----------------------------------------------------------

LEVELS = {"fail", "warn", "info", "ok"}


def _template(node: ast.JoinedStr) -> str:
    """An f-string as text, independent of the quotes ast.unparse picks (they differ by patch)."""
    parts = []
    for value in node.values:
        if isinstance(value, ast.Constant):
            parts.append(str(value.value))
        else:
            assert isinstance(value, ast.FormattedValue)
            parts.append("{" + ast.unparse(value.value) + "}")
    return "".join(parts)


def _verifier_codes() -> set[str]:
    """Every finding code verify.py can print, from its source (static and f-string codes)."""
    source = _read(VERIFY_PY)
    tree = ast.parse(source)
    codes: set[str] = set()
    # Hyphenated string constants are codes, except encoding names ("utf-8", "latin-1").
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            value = node.value
            if re.fullmatch(r"[a-z]+(?:-[a-z0-9]+)+", value):
                try:
                    codecs.lookup(value)
                except LookupError:
                    codes.add(value)
    # Single-word codes and f-string codes, from the reporting calls.
    kinds: set[str] = set()
    kinds_with_hash: set[str] = set()
    templates: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        if name == "_check_stamped_record" and len(node.args) >= 4:
            kind = node.args[3]
            assert isinstance(kind, ast.Constant)
            assert isinstance(kind.value, str)
            kinds.add(kind.value)
            if len(node.args) >= 5 or any(k.arg == "token_sha256" for k in node.keywords):
                kinds_with_hash.add(kind.value)
            continue
        position = 1 if name == "add" else 0
        if name not in LEVELS | {"add", "CheckError"} or len(node.args) <= position:
            continue
        argument = node.args[position]
        if isinstance(argument, ast.Constant) and isinstance(argument.value, str):
            codes.add(argument.value)
        elif isinstance(argument, ast.JoinedStr):
            templates.append(_template(argument))
    for node in ast.walk(tree):  # findings returned as (level, code, tr, en) tuples
        if isinstance(node, ast.Tuple) and len(node.elts) == 4:
            level, code = node.elts[0], node.elts[1]
            if (
                isinstance(level, ast.Constant)
                and level.value in LEVELS
                and isinstance(code, ast.Constant)
                and isinstance(code.value, str)
            ):
                codes.add(code.value)
    expansions = {
        "{kind}-ok": {f"{kind}-ok" for kind in kinds},
        "{kind}-token-mismatch": {f"{kind}-token-mismatch" for kind in kinds_with_hash},
        "{key.replace('_', '-')}-missing": {"report-missing", "report-token-missing"},
    }
    assert 'for key in ("report", "report_token"):' in source
    unknown = [template for template in templates if template not in expansions]
    assert unknown == [], f"new dynamic verifier codes, extend this test: {unknown}"
    for template in templates:
        codes |= expansions[template]
    return codes


def test_auditor_guide_explains_every_verifier_code() -> None:
    codes = _verifier_codes()
    assert {"report-missing", "report-token-omitted", "token-ok", "crl-revoked"} <= codes
    guide = _doc("DENETCI-KILAVUZU.md")
    documented = set(re.findall(r"`([a-z0-9-]+)`", guide))
    assert sorted(codes - documented) == [], "add these codes to DENETCI-KILAVUZU.md section 7"


def test_auditor_guide_covers_web_packages_and_the_report() -> None:
    guide = _flat(_doc("DENETCI-KILAVUZU.md"))
    assert "yazılacak (web arayüzü ile, M6)" not in guide
    for needed in ("evidence.create", "evidence.download", "report_note", "X-Arci-Sha256"):
        assert needed in guide, needed


def test_auditor_guide_states_what_the_database_deletes() -> None:
    """Retention and KVKK decisions are taken on this section: the parsed log tables have no
    deletion job yet, but the usage rollups (names, sites, URLs) are deleted after
    ``usage.retention_days`` by the worker job ``usage.retention`` — in both languages."""
    assert "usage.retention" in {job.name for job in default_jobs(60)}
    days = DEFAULT_SETTINGS["usage.retention_days"]
    turkish, _, english = _doc("DENETCI-KILAVUZU.md").partition('<a id="english"></a>')
    tr = _flat(_section(turkish, "## 2. Ne saklanır", "## 3. "))
    en = _flat(_section(english, "**Retention.**", "**Evidence package.**"))
    assert f"**{days} gün** (`usage.retention_days`)" in tr
    assert f"{days} days (`usage.retention_days`)" in en
    for text in (tr, en):
        assert "`usage.retention`" in text
    assert "ayrıştırılmış kayıtlardan hiçbir şey silinmez" in tr
    assert "no deletion job exists yet for them" in en


# -- legal wording (spec 0 rule 6, spec 3.3) ------------------------------------------------------

#: Positive legal claims (spec 0 rule 6: never claim compliance; "designed to support" only).
COMPLIANCE_CLAIMS = (
    "yasal olarak uyumlu",
    "kanuna uygundur",
    "mevzuata uygundur",
    "legally compliant",
    "fully compliant",
    "yasal geçerli olan tek yol",
)
#: The subset checked in every document (others may quote the rule itself, e.g. "never claim
#: legally compliant").
CLAIMS_EVERYWHERE = ("yasal olarak uyumludur", "mevzuata tam uyumlu", "yasal geçerli olan tek yol")


@pytest.mark.parametrize("name", LEGAL_DRAFTS)
def test_legal_drafts_exist_marked_as_drafts_without_claims(name: str) -> None:
    text = _doc(name)
    assert text.lstrip().splitlines()[0] == DRAFT_MARKER
    lowered = text.lower()
    assert [claim for claim in COMPLIANCE_CLAIMS if claim in lowered] == []
    assert "5651 yükümlülüklerini desteklemek için tasarlanmıştır" in _flat(text)


def test_retention_draft_states_what_the_system_really_keeps() -> None:
    text = _doc("legal/saklama-imha-politikasi-taslak.md")
    for fact in ("730", "760", "`[retention] enabled = false`", "30 gün", "exports\\reports\\"):
        assert fact in text, fact
    assert "veritabanı imha işi yoktur" in _flat(text)


def test_legal_drafts_are_linked_where_people_look() -> None:
    target = "legal/saklama-imha-politikasi-taslak.md"
    assert f"docs/{target}" in _read(ROOT / "README.md")
    for name in ("DENETCI-KILAVUZU.md", "KURULUM.md", "ILK-ADIMLAR.md", "DATA-MODEL.md"):
        assert target in _doc(name), name


def test_both_legal_drafts_name_email_as_the_way_records_leave_the_server() -> None:
    """The retention draft's measures (section 5) say what the information text says in its
    section 6: named usage reports and alert e-mails leave through the configured SMTP server,
    only SHA-256 hashes go to Kamu SM; counsel is asked about the mail provider in both."""
    retention = _doc("legal/saklama-imha-politikasi-taslak.md")
    measures = _flat(_section(retention, "## 5.", "## 6."))
    assert "yurt dışına aktarım yok" not in measures
    information = _flat(_section(_doc("legal/aydinlatma-metni-taslak.md"), "## 6.", "## 7."))
    for text in (measures, information):
        for fact in ("İstisna e-postadır", "SMTP sunucusu", "yalnızca SHA-256", "silemez"):
            assert fact in text, fact
    for fact in ("zamanlanmış kullanım raporları (kişi adlarıyla", "uyarı e-postaları"):
        assert fact in measures, fact
    assert "aydınlatma metni bölüm 6" in measures
    questions = _flat(_section(retention, "## 6.", "## 7."))
    assert "E-posta sağlayıcısının şirket dışında ya da yurt dışında" in questions
    information_questions = _doc("legal/aydinlatma-metni-taslak.md").partition("## 9.")[2]
    assert "e-posta sağlayıcısı (şirket dışında ya da yurt dışında" in _flat(information_questions)


def test_documents_say_who_sees_which_device_a_person_used() -> None:
    """Only ``devices.read`` (administrators) opens the person registry and the assignment
    history of the devices page, but ``audit.read`` shows every assignment change in the audit
    log (``device.assign``: MAC, previous and new person number, start, who made it), every usage
    role sees the persons of the usage page with their devices, report recipients get the devices
    with their persons, and ``lookup.run`` shows the assignment of the moment: the information
    text (section 5) and roles.md must not say "administrators only"."""

    def holders(*permissions: str) -> set[str]:
        return {role for role, held in ROLE_PERMISSIONS.items() if held & set(permissions)}

    assert holders("devices.read") == {"admin"}
    assert set(ROLE_PERMISSIONS) == {"admin", "auditor", "hr", "viewer"}
    assert holders(*PERSON_PERMISSIONS) == set(ROLE_PERMISSIONS)
    assert holders("lookup.run") == {"admin", "auditor"}
    assert holders("audit.read") == {"admin", "auditor"}
    # GET /audit returns an entry's target and details as written, and an assignment's entry
    # holds the device and the persons before and after it.
    assert {"actor", "target", "details"} <= set(AuditEntry.model_fields)
    assign = inspect.getsource(assign_devices)
    assert 'action="device.assign"' in assign
    assert '{"mac": change.mac, "from": change.previous, "to": change.person_id}' in assign
    access = _flat(_section(_doc("legal/aydinlatma-metni-taslak.md"), "## 5.", "## 6."))
    assert "Kişi listesini ve cihaz-kişi atamalarını yalnızca Yönetici görür" not in access
    assert "atandığı, atamayı yapan) yalnızca Yönetici görür" not in access
    for fact in (
        "**atama geçmişini** (cihazın hangi tarihten hangi tarihe kime atandığı, atamayı yapan) "
        "Cihazlar ve Kişiler sayfasında yalnızca Yönetici görür ve değiştirir",
        "denetim kaydını okuyan roller (`audit.read`: Yönetici, Denetçi)",
        "`device.assign`: cihazın MAC adresi, önceki ve yeni kişinin numarası",
        "kullanım görme yetkisi olan her role (dört rolün hepsi)",
        "**her kişinin dönemde kullandığı cihazları** (cihaz adı, MAC, IP)",
        "zamanlanmış raporların Excel dosyası (Cihazlar sayfası) alıcılarına",
        "5651 sorgusu yapan roller (Yönetici, Denetçi)",
    ):
        assert fact in access, fact
    roles = _flat(_section(_doc("roles.md"), "## Cihazlar ve Kişiler", "| Endpoint |"))
    for stale in ("kişi listesini görmez", "not the person list"):
        assert stale not in roles, stale
    for fact in (
        "`audit.read` (Yönetici, Denetçi) Denetim Kaydı sayfasında",
        "`device.assign`: cihazın MAC adresi, önceki ve yeni kişinin numarası",
        "`usage.anonymous`: dört rolün hepsi) Kullanım sayfasının Kişi sekmesinde",
        "her kişinin dönemde kullandığı cihazları (cihaz adı, MAC, IP)",
        "`lookup.run` (Yönetici, Denetçi)",
        "`audit.read` (admin, auditor) sees every assignment change on the audit log page",
        "`device.assign`: the device's MAC address, the previous and the new person's number",
        "`usage.anonymous`: all four roles) sees on the usage page's person tab",
        "the devices each one used in the period (device name, MAC, IP)",
        "`lookup.run` (admin, auditor)",
    ):
        assert fact in roles, fact


#: Every IP address an alert e-mail can carry, by case: the rules whose sentence
#: (``alerts/texts.py``) holds one, and a source name with the sender's address in it (a sender
#: inside a configured network, or one that is not configured), which ``alerts/mail.py`` puts on
#: the "Kaynak" line and into the subject of any alert.
ALERT_MAIL_IP_CASES: dict[str, tuple[str, ...]] = {
    "new sender": ("new-log-source", "new_source"),
    "wrong transport": ("wrong-transport",),
    "IP conflict": ("ip_conflict",),
    "source name": (),
}
#: Where a text says what leaves the server in an alert e-mail, and how it names each case.
ALERT_MAIL_IPS: dict[str, tuple[str, str, str, dict[str, str]]] = {
    "UYARILAR-5": (
        "docs/UYARILAR.md",
        "## 5.",
        "## 6.",
        {
            "new sender": "yeni göndericinin IP adresi (`new-log-source`, `new_source`)",
            "wrong transport": "taşıma yolu izin verilmeyen bir mesajın gönderici adresi",
            "IP conflict": "çakışan IP adresi (`ip_conflict`)",
            "source name": "yapılandırılmamış bir göndericinin adı IP adresini taşır",
        },
    ),
    "UYARILAR-7": (
        "docs/UYARILAR.md",
        "## 7.",
        "## 8.",
        {
            "new sender": "(yeni gönderici,",
            "wrong transport": "taşıma yolu izin verilmeyen gönderici",
            "IP conflict": "IP çakışması,",
            "source name": "adında IP adresi olan kaynak",
        },
    ),
    "ALERTS-5": (
        "docs/ALERTS.md",
        "## 5.",
        "## 6.",
        {
            "new sender": "a new sender's IP address (`new-log-source`, `new_source`)",
            "wrong transport": "the sender address of a message refused for its transport",
            "IP conflict": "the conflicting IP address of an IP conflict (`ip_conflict`)",
            "source name": "or of a sender that is not configured, holds its IP address",
        },
    ),
    "ALERTS-7": (
        "docs/ALERTS.md",
        "## 7.",
        "## 8.",
        {
            "new sender": "(a new sender,",
            "wrong transport": "a sender refused for its transport",
            "IP conflict": "an IP conflict,",
            "source name": "a source whose name holds its address",
        },
    ),
    "ASSUMPTIONS-E5": (
        "docs/ASSUMPTIONS.md",
        "| E5 |",
        "\n",
        {
            "new sender": "a new sender's IP address",
            "wrong transport": "the sender address of a message refused for its transport",
            "IP conflict": "the conflicting IP address of an IP conflict",
            "source name": "or of a sender that is not configured holds its IP address",
        },
    ),
    "ADR-0010-4": (
        "docs/adr/0010-alerts-and-email.md",
        "4. **An e-mail carries no log content.**",
        "5. **Delivery semantics.**",
        {
            "new sender": "the sender IP address of a new source",
            "wrong transport": "or of a message refused for its transport",
            "IP conflict": "the IP address of an IP conflict",
            "source name": "or of one that is not configured holds its IP address",
        },
    ),
    "texts.py": (
        "worker/src/arcilogger_worker/alerts/texts.py",
        '"""What an alert e-mail may say',
        '"""\n',
        {
            "new sender": "the IP address of a new sender",
            "wrong transport": "of a message refused for its transport (``wrong-transport``)",
            "IP conflict": "or of an IP conflict",
            "source name": "one that is not configured is named with its IP address",
        },
    ),
    "aydinlatma-6": (
        "docs/legal/aydinlatma-metni-taslak.md",
        "## 6.",
        "## 7.",
        {
            "new sender": "yeni bir log göndericisinin IP adresi",
            "wrong transport": "taşıma yolu izin verilmeyen bir mesajın gönderici adresi",
            "IP conflict": "çakışan iç IP adresi",
            "source name": "yapılandırılmamış bir göndericinin kaynak adı da IP adresini içerir",
        },
    ),
}
#: Where a text says how many IP addresses an alert e-mail holds: one per alert, and one e-mail
#: lists several alerts (``alerts.email_digest_seconds``, ASSUMPTIONS E3).
ALERT_MAIL_IP_COUNT: dict[str, tuple[str, str, str, tuple[str, ...]]] = {
    "UYARILAR-7": (
        "docs/UYARILAR.md",
        "## 7.",
        "## 8.",
        ("her uyarı için bir IP adresi", "bir e-posta birden çok uyarıyı listeleyebilir"),
    ),
    "ALERTS-7": (
        "docs/ALERTS.md",
        "## 7.",
        "## 8.",
        ("one IP address per alert", "one e-mail can list several alerts"),
    ),
    "aydinlatma-6": (
        "docs/legal/aydinlatma-metni-taslak.md",
        "## 6.",
        "## 7.",
        ("her uyarı için bir IP adresi", "bir e-posta birden çok uyarıyı listeleyebilir"),
    ),
    "saklama-5": (
        "docs/legal/saklama-imha-politikasi-taslak.md",
        "## 5.",
        "## 6.",
        ("her uyarı için bir IP adresi", "bir e-posta birden çok uyarıyı listeleyebilir"),
    ),
}
MAIL_IP: Final = "192.0.2.10"
#: A value of each shape a rule's sentence takes from ``details``: a number, a flag, a day, a
#: moment, a list of days or of numbers, a name.
DETAIL_SHAPES: Final[tuple[Any, ...]] = (
    1,
    True,
    "2026-10-20",
    "2026-10-20T08:00:00+03:00",
    ["2026-10-20"],
    [1],
    "fortigate",
)
#: Detail keys holding a name ARCILOGGER gives, which the probe below leaves out: a source name is
#: a case of its own (it can hold the sender's address), a worker job's name is fixed in the code.
NAME_KEYS: Final = frozenset({"source", "job"})
_ABSENT: Final = object()


class _Details(Mapping[str, Any]):
    """Alert details from ``given`` that note every key a rule's sentence asks for."""

    def __init__(self, given: Mapping[str, Any], asked: set[str]) -> None:
        self.given, self.asked = given, asked

    def __getitem__(self, key: str) -> Any:
        self.asked.add(key)
        return self.given[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self.given)

    def __len__(self) -> int:
        return len(self.given)


def _compared_words(describer: Callable[..., object]) -> list[str]:
    """The strings a describer compares a fact with (``case == "failed"``)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(describer)))
    return sorted(
        {
            node.value
            for compare in ast.walk(tree)
            if isinstance(compare, ast.Compare)
            for node in (compare.left, *compare.comparators)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        }
    )


def _address_keys(describer: Callable[[Mapping[str, Any]], tuple[str, str] | None]) -> set[str]:
    """The keys of ``details`` whose IP address can show up in a rule's sentence.

    Every key the describer reads holds, in turn, nothing, a value of each of
    :data:`DETAIL_SHAPES` or a word it compares with — so a sentence that also needs a number, a
    flag or a day is reached — while one key at a time holds :data:`MAIL_IP` (or a list of it),
    whatever helper checks it. Keys read only on some branch are found as the branch is taken.
    :data:`NAME_KEYS` are left out (checked on their own, below).
    """
    values = (_ABSENT, *DETAIL_SHAPES, *_compared_words(describer))
    keys: list[str] = []
    found: set[str] = set()
    while True:
        asked: set[str] = set()
        for combination in itertools.product(values, repeat=len(keys)):
            base = {
                key: value
                for key, value in zip(keys, combination, strict=True)
                if value is not _ABSENT
            }
            probes: list[dict[str, Any]] = [{}] + [
                {key: address}
                for key in keys
                if key not in NAME_KEYS
                for address in (MAIL_IP, [MAIL_IP])
            ]
            for probe in probes:
                try:
                    sentence = describer(_Details(base | probe, asked))
                except (AttributeError, LookupError, TypeError, ValueError):
                    continue  # a shape the rule never gets: no sentence
                if sentence is not None and any(MAIL_IP in text for text in sentence):
                    found |= set(probe)
        if asked <= set(keys):
            return found
        keys += sorted(asked - set(keys))


def test_the_documented_cases_are_every_ip_address_an_alert_email_carries() -> None:
    """Fails when a rule's sentence in ``alerts/texts.py`` can show an IP address taken from a
    detail that :data:`ALERT_MAIL_IP_CASES` (and so the texts of :data:`ALERT_MAIL_IPS`) does not
    name: found by :func:`_address_keys` for every rule, and by a call of ``_ip`` in a describer."""
    probed = {rule for rule, describer in DESCRIBERS.items() if _address_keys(describer)}
    checked = {rule for rule, fact in DESCRIBERS.items() if "_ip(" in inspect.getsource(fact)}
    documented = {rule for rules in ALERT_MAIL_IP_CASES.values() for rule in rules}
    assert probed | checked == documented, "a new case: extend ALERT_MAIL_IP_CASES and the docs"
    for rule in documented:
        details = {"address": MAIL_IP, "ip": MAIL_IP, "source": "fortigate"}
        assert all(MAIL_IP in sentence for sentence in describe(rule, details)), rule
    # A job name is a job of the worker's code (R20-job leaves manual runs out), not an address.
    for job in default_jobs(60):
        with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6 address"):
            ipaddress.ip_address(job.name)
    # A source name can hold the sender's address; every alert e-mail shows its source name.
    network = Config.model_validate(
        {
            "data_dir": "/x",
            "secrets_dir": "/x",
            "sources": [{"name": "ap", "type": "mikrotik", "address": "192.0.2.0/24"}],
        }
    )
    when = datetime(2026, 10, 20, 8, tzinfo=UTC)
    for name in (network.source_name_for(MAIL_IP), unknown_source_name(MAIL_IP)):
        assert name is not None
        assert MAIL_IP in name
        assert source_name({"source": name}) == name
        item = MailItem(1, "new", "R20-spool", "critical", "open", when, None, {"source": name})
        assert name in subject([item])
        assert name in render_text([item], "https://arcilogger.internal")


@pytest.mark.parametrize("where", sorted(ALERT_MAIL_IPS))
def test_texts_name_every_ip_address_an_alert_email_carries(where: str) -> None:
    path, start, end, phrases = ALERT_MAIL_IPS[where]
    assert sorted(phrases) == sorted(ALERT_MAIL_IP_CASES)
    section = _flat(_section(_read(ROOT / path), start, end))
    assert [phrase for phrase in phrases.values() if phrase not in section] == []


@pytest.mark.parametrize("where", sorted(ALERT_MAIL_IP_COUNT))
def test_texts_say_an_alert_email_holds_one_ip_address_per_alert(where: str) -> None:
    """Alerts of one digest window go into one e-mail, each with its own sentence: a new sender
    and an IP conflict give one e-mail with two addresses, so no text may say "one IP address"
    alone."""
    when = datetime(2026, 10, 20, 8, tzinfo=UTC)
    items = [
        MailItem(1, "new", "new-log-source", "high", "open", when, None, {"address": MAIL_IP}),
        MailItem(2, "new", "ip_conflict", "high", "open", when, None, {"ip": "192.0.2.11"}),
    ]
    assert MAX_LISTED > 1
    mail = render_text(items, "https://arcilogger.internal")
    assert MAIL_IP in mail
    assert "192.0.2.11" in mail
    path, start, end, phrases = ALERT_MAIL_IP_COUNT[where]
    section = _flat(_section(_read(ROOT / path), start, end))
    assert [phrase for phrase in phrases if phrase not in section] == []


@pytest.mark.parametrize(
    "path",
    [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted(DOCS.rglob("*.md"))],
    ids=lambda p: p.relative_to(ROOT).as_posix(),
)
def test_no_document_claims_legal_compliance(path: Path) -> None:
    lowered = _flat(_read(path)).lower()
    assert [claim for claim in CLAIMS_EVERYWHERE if claim in lowered] == []


# -- no organisation's data (product decisions of 2026-10-20, ASSUMPTIONS P1-P8) -----------------

#: What the installation guides and the first-steps guides tell the operator about the
#: organisation's own values: each of them exists in the code (or its branch is pending).
ORGANISATION_VALUES: Final = (
    "installer:Install-Arcilogger.ps1 -SiteName",
    "installer:Install-Arcilogger.ps1 -NtpServer",
    "installer:Install-Arcilogger.ps1 -AdminAddress",
    "installer:Set-ArciFirewall.ps1 -AdminAddress",
    "empty-default:site_name",
    "empty-default:clock.ntp_server",
    "cli:arcilogger-core config-check",
)
#: How each installation guide says that a parameter has no default.
NO_DEFAULT: Final = {"KURULUM.md": "| `{}` | — (yok", "INSTALL.md": "`{}` (no default"}


@pytest.mark.parametrize("name", ["KURULUM.md", "INSTALL.md"])
def test_install_guides_ask_for_the_organisation_values(name: str) -> None:
    """ARCILOGGER ships no organisation's name, time source or admin address: section 7.1 says
    which installer parameter takes each one, that none has a default, where it is written and how
    ``config-check`` reports what is still missing."""
    for item in ORGANISATION_VALUES:
        assert _exists(item) or item in PENDING_FROM_PARALLEL_BRANCHES, item
    text = _doc(name)
    section = _flat(_section(text, "### 7.1", "## 8."))
    for needed in (
        "-SiteName",
        "-NtpServer",
        "-AdminAddress",
        "`site_name`",
        "`[clock] ntp_server`",
        "`[clock] fallback_server`",
        'arcilogger-core.exe" config-check',
        "`10.0.0.10`",
        "arcilogger.toml.before-",
        "[[sources]]",
    ):
        assert needed in section, needed
    for parameter in ("-SiteName", "-NtpServer", "-AdminAddress"):
        assert NO_DEFAULT[name].format(parameter) in _flat(text), parameter
        assert f"{parameter} " in _flat(_section(text, "## 7.", "### 7.1")), parameter


#: What the first-steps guides say about the organisation's values, departments and VLANs.
FIRST_STEPS_NETWORK: Final = {
    "ILK-ADIMLAR.md": (
        "ARCILOGGER hiçbir departman ya da VLAN planı getirmez",
        "**VLAN ekle**",
        "ana bilgisayar bitleri sıfır",
        "Henüz VLAN yok: ağınızı ekleyin",
        "`lease.server_seconds`",
    ),
    "FIRST-STEPS.md": (
        "ARCILOGGER ships no departments and no VLAN plan",
        "**VLAN ekle**",
        "host bits zero",
        "Henüz VLAN yok: ağınızı ekleyin",
        "`lease.server_seconds`",
    ),
}
#: What the documents said while the VLAN list had no entry screen (before ``POST /vlans``).
VLAN_ENTRY_STALE: Final = (
    "VLAN listesi girilemez",
    "arayüzden girilemez",
    "VLAN eklemek için bu sürümde ekran",
    "The VLAN list cannot be entered",
    "cannot be entered in the interface",
    "no VLAN entry screen",
    "has no entry screen",
    "No VLAN entry",
)


@pytest.mark.parametrize("name", sorted(FIRST_STEPS_NETWORK))
def test_first_steps_start_from_an_empty_network(name: str) -> None:
    """The seed ships no VLANs and no departments: the guides check the organisation's values and
    have the departments and the VLANs entered on the devices page (section 5)."""
    text = _doc(name)
    assert "& $core config-check" in _section(text, "## 0.", "## 1.")
    flat = _flat(text)
    for needed in FIRST_STEPS_NETWORK[name]:
        assert needed in flat, needed
    for stale in ("kablolu kullanıcı VLAN'larınınkiler hazırdır", "those of the wired user VLANs"):
        assert stale not in flat, stale


def test_no_document_says_vlans_cannot_be_entered() -> None:
    """``/api/v1/vlans`` adds, edits and deletes VLANs and the web page offers it: no document may
    still send the administrator to SQL or a later release for the network."""
    paths = sorted({*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md"), ROOT / "web/README.md"})
    for path in paths:
        flat = _flat(path.read_text(encoding="utf-8"))
        for stale in VLAN_ENTRY_STALE:
            assert stale not in flat, (path.relative_to(ROOT).as_posix(), stale)
    routes = {
        (method, route.path)
        for route in vlans_router.routes
        if isinstance(route, APIRoute)
        for method in route.methods or ()
    }
    for method in ("GET", "POST", "PATCH", "DELETE"):
        assert (method, "/vlans") in routes, method


def test_data_model_says_the_seed_ships_no_network() -> None:
    name = "attr:arcilogger_db.testing.seed_example_network"
    assert _exists(name) or name in PENDING_FROM_PARALLEL_BRANCHES
    section = _flat(_section(_doc("DATA-MODEL.md"), "## Seed data", "## Schema changes"))
    assert "**no network**" in section
    assert "`arcilogger_db.testing.seed_example_network`" in section
    assert "`lease.server_seconds = {}`" in section
    assert re.search(r"\b192\.168\.\d", section) is None


#: Placeholders of the legal templates (the company fills them in).
LEGAL_PLACEHOLDERS: Final = ("[Şirket unvanı]", "[adres]", "[VERBİS kayıt no]")
#: An address of a private network (RFC 1918): a network plan of one organisation.
PRIVATE_ADDRESS: Final = re.compile(
    r"(?<![\d.])(?:10|192\.168|172\.(?:1[6-9]|2\d|3[01]))(?:\.\d{1,3}){2,3}(?![\d.])"
)
#: Host names of the documentation's fictional example network: fine in examples and tests, not in
#: the documents every organisation follows at its own installation.
EXAMPLE_HOSTS: Final = ("CORE-R1", "CORE-S1", "FIREWALL-01", "NAS-01", "ADMIN-PC")


@pytest.mark.parametrize("name", LEGAL_DRAFTS)
def test_legal_drafts_are_templates_without_an_organisation(name: str) -> None:
    text = _doc(name)
    assert [p for p in LEGAL_PLACEHOLDERS if p not in text] == []
    assert re.findall(r"`<[^`]*>`", text) == [], "placeholders are written [like this]"
    assert "Örnek A.Ş." not in text
    assert PRIVATE_ADDRESS.findall(text) == []
    assert [host for host in EXAMPLE_HOSTS if host in text] == []


@pytest.mark.parametrize("name", OPERATOR_DOCS + BACKUP_DOCS)
def test_operator_documents_carry_no_network_of_an_organisation(name: str) -> None:
    """Every organisation installs from these documents: an address or a host name of one network
    in them would be copied into another. They use placeholders (`<arcilogger>`, `10.0.0.10`) or
    refer to the example configuration."""
    text = _doc(name)
    assert re.findall(r"\b192\.168\.\d{1,3}\.\d{1,3}", text) == []
    assert [host for host in EXAMPLE_HOSTS if host in text] == []
    assert "Örnek A.Ş." not in text


#: A device model or a firmware release more precise than the one the commands were written for:
#: such a detail describes one installation, not the product.
MODEL_OR_RELEASE: Final = re.compile(
    r"\bFortiOS \d+\.\d+\.\d+|\bRouterOS \d+\.\d+"
    r"|\b(?:Forti(?:Gate|WiFi|Switch|AP)|MikroTik|Ubiquiti|UniFi|Cisco|Meraki|Aruba|Juniper|SonicWall"
    r"|Sophos|WatchGuard|Zyxel|TP-Link|Netgear|QNAP|Synology|Dell|HPE?|Lenovo|Grandstream|Yealink)"
    r"\s+[A-Za-z]{0,4}-?\d{2,4}[A-Za-z0-9+-]*"
)


@pytest.mark.parametrize(
    "path",
    [ROOT / "README.md", ROOT / "CHANGELOG.md", *sorted(DOCS.rglob("*.md"))],
    ids=lambda p: p.relative_to(ROOT).as_posix(),
)
def test_documents_name_no_device_model_or_firmware_release(path: Path) -> None:
    assert MODEL_OR_RELEASE.findall(_read(path)) == []


# -- offline licensing (licensing contract sections 11 and 12 D, ADR-0013) -----------------------

#: The customer guide, Turkish first.
LISANS, LICENSING = LICENSE_DOCS
#: Per guide: the language of its module names and the words of its worker-job table.
LICENSE_LANGUAGE: Final = {
    LISANS: ("tr", "atlanır", "her zaman çalışır"),
    LICENSING: ("en", "skipped", "always runs"),
}
#: What no text may say about the product or its licensing (spec 0 rule 6; contract 3.5): no claim
#: of legal compliance, and no claim that the licensing cannot be copied, broken or tampered with.
OVERSTATEMENTS: Final = (
    "legally compliant",
    "yasal olarak uyumlu",
    "kopyalanamaz",
    "kırılamaz",
    "tamper-proof",
    "tamper proof",
)
#: The placeholders the guides use where the installer's refusal has its values.
REFUSAL_PLACEHOLDERS: Final = ("<version>", "<release_date>", "<until>")


def _module_name(key: str | None, language: str) -> str:
    """The registry name of ``key`` (``None``: 5651 Temel) in ``language``."""
    entry = next(item for item in MODULES if item.key == (key or CORE_MODULE))
    return entry.name_tr if language == "tr" else entry.name_en


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split(" | ")]


def _table_rows(text: str, first_cell: str) -> dict[str, list[str]]:
    """Markdown table rows whose whole first cell matches ``first_cell`` (a regex with one
    group), as {group: cells}; a row named twice fails."""
    rows: dict[str, list[str]] = {}
    for line in text.splitlines():
        if not line.startswith("| "):
            continue
        cells = _cells(line)
        found = re.fullmatch(first_cell, cells[0])
        if found is None:
            continue
        assert found.group(1) not in rows, f"row {found.group(1)!r} twice"
        rows[found.group(1)] = cells
    return rows


def _after(text: str, start: str) -> str:
    """Everything from ``start`` (a heading) to the end of ``text``."""
    return text[text.index(start) :]


def _api_areas() -> dict[str, str | None]:
    """Every first path segment of the API → the module gating it (``None``: 5651 Temel), from
    the ``__arci_module__`` markers of the routers' dependencies (contract table 1.3)."""
    areas: dict[str, str | None] = {}
    for route in api_app.router.routes:  # the app's own system router (/health)
        if isinstance(route, APIRoute):
            areas["/" + route.path.removeprefix(API_PREFIX).split("/")[1]] = None
    for router in discover():
        modules = {
            getattr(dependency.call, MODULE_MARKER)
            for route in router.routes
            if isinstance(route, APIRoute)
            for dependency in route.dependant.dependencies
            if hasattr(dependency.call, MODULE_MARKER)
        }
        assert len(modules) <= 1, router.prefix
        if router.routes:
            # One area per first path segment (``/admin`` holds ``/admin/requests``, …).
            area = "/" + router.prefix.split("/")[1]
            module = next(iter(modules), None)
            assert areas.get(area, module) == module, router.prefix
            areas[area] = module
    return areas


def _installer_literal(script: str, start: str) -> str:
    """The one single-quoted PowerShell literal that starts with ``start``, unescaped."""
    assert script.count("'" + start) == 1, start
    begin = script.index("'" + start) + 1
    end = begin
    while True:
        end = script.index("'", end)
        if script.startswith("''", end):
            end += 2
            continue
        return script[begin:end].replace("''", "'")


def test_licence_guides_are_operator_documents_in_the_bundle() -> None:
    """LISANS.md / LICENSING.md are read on the offline server: in the bundle's required list
    (test_offline_bundle_ships_the_whole_docs_tree) and named by the installer's next steps."""
    assert set(LICENSE_DOCS) <= set(OPERATOR_DOCS)
    steps = _show_next_steps()
    for name in LICENSE_DOCS:
        assert (DOCS / name).is_file(), name
        assert f"docs\\{name}" in steps, name


def test_installer_names_only_documents_that_exist() -> None:
    script = _read(INSTALLER / "Install-Arcilogger.ps1")
    named = set(re.findall(r"docs\\([A-Za-z0-9-]+\.md)", script))
    assert {"ILK-ADIMLAR.md", "FIRST-STEPS.md", *LICENSE_DOCS} <= named
    assert sorted(name for name in named if not (DOCS / name).is_file()) == []


@pytest.mark.parametrize("name", LICENSE_DOCS)
def test_licence_guides_name_the_licensor(name: str) -> None:
    text = _doc(name)
    assert f"**{LICENSOR_NAME}, {LICENSOR_EMAIL}**" in text
    assert f"**{LICENSOR_EMAIL}**" in _section(text, "### 4.2", "### 4.3")
    for first_steps in ("ILK-ADIMLAR.md", "FIRST-STEPS.md"):
        assert f"**{LICENSOR_EMAIL}**" in _after(_doc(first_steps), "## 17."), first_steps


@pytest.mark.parametrize("name", LICENSE_DOCS)
def test_licence_guides_list_every_error_code_with_both_texts(name: str) -> None:
    """Every code of ``errors.MESSAGES`` with its HTTP status and both message templates, exactly
    as the API, the CLI and the issuer print them (placeholders in braces)."""
    rows = _table_rows(_after(_doc(name), "## 13."), r"`([a-z_]+)`")
    assert sorted(rows) == sorted(MESSAGES), "the error table lists exactly errors.MESSAGES"
    for code, (text_tr, text_en) in MESSAGES.items():
        assert rows[code] == [f"`{code}`", str(HTTP_STATUS.get(code, "—")), text_tr, text_en]


@pytest.mark.parametrize("name", LICENSE_DOCS)
def test_licence_guides_list_the_modules(name: str) -> None:
    language = LICENSE_LANGUAGE[name][0]
    rows = _table_rows(_section(_doc(name), "## 2.", "### 2.1"), r"`([a-z0-9_]+)`")
    assert sorted(rows) == sorted(item.key for item in MODULES)
    for item in MODULES:
        cells = rows[item.key]
        names = [item.name_tr, item.name_en] if language == "tr" else [item.name_en, item.name_tr]
        assert cells[1:3] == names, item.key
        for required in item.requires:
            assert _module_name(required, language) in cells[-1], item.key
    assert next(item for item in MODULES if item.key == CORE_MODULE).always


@pytest.mark.parametrize("name", LICENSE_DOCS)
def test_licence_guides_classify_every_api_area(name: str) -> None:
    """Section 2.2: every area of the API with the module that gates it, so the 5651-Temel list
    of contract 1.3 is complete; a new router fails here until the guides classify it."""
    language = LICENSE_LANGUAGE[name][0]
    areas = _api_areas()
    assert areas["/usage"] == "usage_analytics"
    assert areas["/reports"] == "usage_reports"
    assert areas["/imports"] == "bulk_import"
    assert areas["/lookup"] is None
    assert areas["/license"] is None
    rows = _table_rows(_section(_doc(name), "### 2.2", "### 2.3"), r"`(/[a-z]+)`")
    assert sorted(rows) == sorted(areas)
    for area, module_key in areas.items():
        assert rows[area][-1] == _module_name(module_key, language), area


@pytest.mark.parametrize("name", LICENSE_DOCS)
def test_licence_guides_classify_every_worker_job(name: str) -> None:
    """Section 2.3 (contract 1.4): every worker job, the gated ones with their module and skipped,
    every other one (5651 Temel, ``license.check``, data minimisation) always running."""
    language, skipped, always = LICENSE_LANGUAGE[name]
    section = _section(_doc(name), "### 2.3", "### 2.4")
    rows = _table_rows(section, r"`([a-z0-9_]+\.[a-z0-9_]+)`")
    jobs = default_jobs(60)
    assert sorted(rows) == sorted(job.name for job in jobs)
    paid = {_module_name(item.key, language) for item in MODULES if item.key != CORE_MODULE}
    for job in jobs:
        cells = rows[job.name]
        if job.module is not None:
            assert cells[1:] == [_module_name(job.module, language), skipped], job.name
        else:
            assert cells[2] == always, job.name
            assert not any(module in cells[1] for module in paid), job.name
    gated = {job.name for job in jobs if job.module is not None}
    assert {"usage.daily", "reports.run_due"} <= gated
    assert "license.check" not in gated
    assert "usage.retention" not in gated  # personal data is deleted on time, licensed or not


def test_licence_guides_give_the_numbers_of_the_code() -> None:
    lisans, licensing = _flat(_doc(LISANS)), _flat(_doc(LICENSING))
    assert f"**{TRIAL_DAYS} gün**" in lisans
    assert f"**{TRIAL_DAYS} days**" in licensing
    assert f"son **{SOURCE_ACTIVE_DAYS} gün**" in lisans
    assert f"last **{SOURCE_ACTIVE_DAYS} days**" in licensing
    minutes = int(TRIAL_CLOCK_TOLERANCE.total_seconds() // 60)
    assert f"{minutes} dakikadan fazla" in lisans
    assert f"more than {minutes} minutes" in licensing
    assert LICENSE_MAX_BYTES == 64 * 1024
    assert "en çok 64 KiB" in lisans
    assert "at most 64 KiB" in licensing
    assert "65.536 bayt" in _flat(_doc("KURULUM.md"))
    assert "65,536 bytes" in _flat(_doc("INSTALL.md"))
    for name in LICENSE_DOCS:
        users = [
            _cells(line)
            for line in _section(_doc(name), "### 6.2", "## 7.").splitlines()
            if line.startswith("| ") and "`trial_expired`" in _cells(line)[0]
        ]
        assert [cells[1] for cells in users] == [f"**{UNLICENSED_USERS}**"], name


@pytest.mark.parametrize("name", LICENSE_DOCS)
def test_licence_guides_quote_the_installers_licence_texts(name: str) -> None:
    """The maintenance refusal (contract 9.3) and the warning for an installation without a
    licence ([C-12]), both lines as the installer prints them, and step 6 of the next steps."""
    module = _read(INSTALLER / "ArciInstall.psm1")
    installer = _read(INSTALLER / "Install-Arcilogger.ps1")
    refusal = [
        _installer_literal(module, "Bu sürüm ({0}, yayın tarihi {1})"),
        _installer_literal(module, "[This version ({0}, released {1})"),
    ]
    warning = [
        _installer_literal(installer, "Bu kurulumun lisansı yok"),
        _installer_literal(installer, "[This installation has no licence"),
    ]
    text = _doc(name)
    for template in refusal:
        assert template.format(*REFUSAL_PLACEHOLDERS) in text, template
    for line in warning:
        assert line in text, line
    trial = "deneme süresi 14 gündür [14-day trial]"
    assert f"'{trial}. İstek kodu [request code]:'" in installer
    assert f"6. Lisans [licence]: {trial}" in _flat(text)
    assert TRIAL_DAYS == 14


#: ``arcilogger-db license …`` in the documents: by ``$db``, by the full path or by name.
LICENSE_COMMAND: Final = re.compile(
    r"(?:\$db|arcilogger-db(?:\.exe)?\"?) license ([a-z][a-z-]*)([^\n`|#]*)"
)


def test_documented_licence_commands_exist() -> None:
    """Every ``arcilogger-db license <action> [--options]`` the documents give exists with its
    options and the guides name every action; ``create-admin --deactivate`` and the one gated
    worker command exist too."""
    licence = _subcommand(db_parser(), "license")
    assert licence is not None
    (actions,) = [a for a in licence._actions if isinstance(a, argparse._SubParsersAction)]
    assert set(actions.choices) == {"status", "request-code", "verify", "install", "remove"}
    used: dict[str, set[str]] = {}
    names = (*LICENSE_DOCS, "KURULUM.md", "INSTALL.md", "ILK-ADIMLAR.md", "FIRST-STEPS.md")
    for name in names:
        for action, rest in LICENSE_COMMAND.findall(_doc(name)):
            assert action in actions.choices, (name, action)
            options = {
                option for a in actions.choices[action]._actions for option in a.option_strings
            }
            flags = set(re.findall(r"(?<![\w-])--[a-z][a-z-]*", rest))
            assert sorted(flags - options) == [], (name, action)
            used.setdefault(name, set()).add(action)
    for name in LICENSE_DOCS:
        assert used[name] == set(actions.choices), name
        assert "create-admin --deactivate" in _doc(name), name
    create_admin = _subcommand(api_parser(), "create-admin")
    assert create_admin is not None
    assert any("--deactivate" in a.option_strings for a in create_admin._actions)
    assert _subcommand(worker_parser(), "usage", "rebuild") is not None


#: The installer's licence parameters (contract 9.1), both without a default.
LICENSE_PARAMETERS: Final = ("-LicenseFile", "-AllowUncoveredUpgrade")


@pytest.mark.parametrize("name", ["KURULUM.md", "INSTALL.md"])
def test_install_guides_describe_the_licence(name: str) -> None:
    for parameter in LICENSE_PARAMETERS:
        assert _exists(f"installer:Install-Arcilogger.ps1 {parameter}"), parameter
    text = _doc(name)
    flat = _flat(text)
    guide = LISANS if name == "KURULUM.md" else LICENSING
    assert f"]({guide})" in text
    assert NO_DEFAULT[name].format("-LicenseFile") in flat
    for needed in (
        *(f"`{parameter}`" for parameter in LICENSE_PARAMETERS),
        "python.exe -I -X utf8 -u -m arcilogger_",
        "python.exe -I -m arcilogger_core",
        "license.install",
        "release_date",
        "license status",
    ):
        assert needed in flat, needed
    upgrade = _flat(_section(text, "## 13.", "## 14."))
    for needed in ("-AllowUncoveredUpgrade", "-LicenseFile", "5651 Temel", "14 ", guide):
        assert needed in upgrade, needed


#: Contract 11: step 2 of the persons and assignments in the first-steps guides.
CSV_IMPORT_MODULE: Final = {
    "ILK-ADIMLAR.md": (
        "### Kişiler ve cihaz-kişi atamaları",
        "CSV içe aktarma Toplu içe aktarma modülü gerektirir; elle giriş her zaman açıktır",
    ),
    "FIRST-STEPS.md": (
        "### Persons and device assignments",
        "CSV import needs the Bulk import module; manual entry always works",
    ),
}


@pytest.mark.parametrize("name", sorted(CSV_IMPORT_MODULE))
def test_first_steps_describe_the_licence(name: str) -> None:
    heading, phrase = CSV_IMPORT_MODULE[name]
    text = _doc(name)
    assert phrase in _flat(_section(text, heading, "## 6."))
    licence = _after(text, "## 17.")
    guide = LISANS if name == "ILK-ADIMLAR.md" else LICENSING
    for needed in ("& $db license request-code", "& $db license install", f"]({guide})"):
        assert needed in licence, needed


@pytest.mark.parametrize(
    ("name", "module_key", "guide"),
    [
        ("KULLANIM.md", "usage_analytics", "LISANS.md"),
        ("USAGE.md", "usage_analytics", "LICENSING.md"),
        ("KULLANIM-RAPORLARI.md", "usage_reports", "LISANS.md"),
        ("USAGE-REPORTS.md", "usage_reports", "LICENSING.md"),
    ],
)
def test_usage_documents_name_their_module_at_the_top(
    name: str, module_key: str, guide: str
) -> None:
    head = _flat(_doc(name).split("\n\n", 2)[1])  # the opening quote block
    language = "tr" if guide == LISANS else "en"
    assert head.startswith("> ")
    assert f"**{_module_name(module_key, language)}**" in head
    assert f"]({guide})" in head


@pytest.mark.parametrize("name", ["UYARILAR.md", "ALERTS.md"])
def test_alert_documents_give_r20_report_to_scheduled_reports(name: str) -> None:
    """``R20-report`` belongs to Zamanlanmış kullanım raporları and is resolved with
    ``UNLICENSED_NOTE`` when the module goes off; every other alert is 5651 Temel."""
    assert REPORT_RULE == "R20-report"
    language = "tr" if name == "UYARILAR.md" else "en"
    text = _doc(name)
    rows = _table_rows(_section(text, "## 2.", "## 3."), r"`([A-Za-z0-9_-]+)`")
    assert _module_name("usage_reports", language) in rows[REPORT_RULE][1]
    assert UNLICENSED_NOTE in rows[REPORT_RULE][1]
    intro = _flat(_section(text, "## 2.", "| Kod |" if language == "tr" else "| Code |"))
    assert "5651 Temel" in intro
    assert f"`{REPORT_RULE}`" in intro


def _audit_actions_in_code() -> set[str]:
    """The ``license.*`` audit actions the code writes (string constants, without job names)."""
    jobs = {job.name for job in default_jobs(60)}
    actions = set()
    for package in ("db", "api", "worker"):
        for path in sorted((ROOT / package / "src").rglob("*.py")):
            for node in ast.walk(ast.parse(_read(path))):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and re.fullmatch(r"license\.[a-z_]+", node.value)
                    and node.value not in jobs
                ):
                    actions.add(node.value)
    return actions


@pytest.mark.parametrize("name", [*LICENSE_DOCS, "SECURITY.md"])
def test_documents_list_every_licence_audit_action(name: str) -> None:
    actions = _audit_actions_in_code()
    assert {"license.install", "license.refuse", "license.limit_refused"} <= actions
    documented = {
        action
        for line in _doc(name).splitlines()
        if line.startswith("| ")
        for action in re.findall(r"`(license\.[a-z_]+)`", _cells(line)[0])
    }
    assert sorted(actions - documented) == [], name


#: Per document: where it states the threat model's list (c) (contract 3.5), and the words that
#: must be in it: identity spoofing, the full fresh install, the database owner's triggers, the
#: clock with hidden vault days, SQL on computed usage tables, a patched checker.
THREAT_MODEL: Final[dict[str, tuple[str, str, tuple[str, ...]]]] = {
    "adr/0013-offline-licensing.md": (
        "**Never closable offline:**",
        "The remedies",
        (
            "MachineGuid",
            "SMBIOS UUID",
            "full fresh install",
            "triggers",
            "clock",
            "SQL",
            "patching",
        ),
    ),
    "SECURITY.md": (
        "What the licensing **cannot** protect against",
        "The remedies",
        (
            "MachineGuid",
            "SMBIOS UUID",
            "full fresh install",
            "triggers",
            "clock",
            "SQL",
            "patching",
        ),
    ),
    "LISANS.md": (
        "## 11.",
        "## 12.",
        ("MachineGuid", "SMBIOS UUID", "sıfırdan", "yalnızca ekleme", "saati geri", "SQL"),
    ),
    "LICENSING.md": (
        "## 11.",
        "## 12.",
        ("MachineGuid", "SMBIOS UUID", "from scratch", "append-only", "clock back", "SQL"),
    ),
}


@pytest.mark.parametrize("name", sorted(THREAT_MODEL))
def test_documents_state_what_the_licensing_cannot_prevent(name: str) -> None:
    start, end, words = THREAT_MODEL[name]
    text = _doc(name)
    section = _flat(_section(text, start, end))
    assert [word for word in words if word not in section] == []
    remedies = _flat(_after(text, start))[: len(section) + 300]
    assert ("are contractual" in remedies) or ("sözleşmeseldir" in remedies), name


def test_security_doc_and_adr_state_no_secrets_and_no_telemetry() -> None:
    security = _flat(_section(_doc("SECURITY.md"), "## 13.", "What the licensing"))
    for needed in ("**public** keys", "No telemetry", "python.exe -I -X utf8 -u -m", ".pth"):
        assert needed in security, needed
    adr = _flat(_doc("adr/0013-offline-licensing.md"))
    for rejected in (
        "**Online activation**",
        "**Hardware dongle**",
        "**Licence in the configuration file**",
        "**A domain prefix on the signed message**",
    ):
        assert rejected in adr, rejected
    assert "reaches only installations that upgrade" in adr


def test_adr_index_lists_every_decision() -> None:
    index = _doc("adr/README.md")
    files = sorted(path.name for path in (DOCS / "adr").glob("[0-9][0-9][0-9][0-9]-*.md"))
    assert "0013-offline-licensing.md" in files
    assert [name for name in files if f"| [{name[:4]}]({name}) |" not in index] == []


def test_adr_0016_cites_fortios_by_major_and_minor_release() -> None:
    """ADR-0016 (Wave 3 contract 3.9): the Fortinet documents are cited as "FortiOS 7.6 …" and
    the community articles by title and number, never with a third release number; the evidence
    summary it quotes keeps "FortiGate DHCP," between the device and the logid; every decision
    D1-D33 and the specification deviations are recorded."""
    text = _doc("adr/0016-fortigate-dhcp-and-internet-exits.md")
    flat = _flat(text)
    for source in ("FortiOS 7.6 log reference", "FortiOS 7.6 CLI reference"):
        assert source in flat, source
    assert re.findall(r"FortiOS \d+\.\d+\.\d+", text) == []
    assert MODEL_OR_RELEASE.findall(text) == []
    assert "(FortiGate DHCP, logid 0100026001, lease 604800)" in flat
    assert [n for n in range(1, 34) if f"**D{n} " not in text] == []
    assert flat.count("This departs from SPEC 9.1") == 2  # srcmac (D10), traffic (D26)
    assert [f"O{n}" for n in range(1, 23) if f"| O{n} |" not in text] == []


def test_assumptions_record_every_licensing_decision() -> None:
    """Rows L1-L8 (the owner's brief) and C-1-C-16 (the contract's decisions) with the owner's
    answers: C-1, C-6 and C-11 confirmed (C-11 with the users limit of the code), the others
    accepted as written."""
    section = _section(_doc("ASSUMPTIONS.md"), "## Licensing (owner 2026-10-20)", "\n## ")
    rows = _table_rows(section, r"([LC]-?\d+)")
    expected = [f"L{n}" for n in range(1, 9)] + [f"C-{n}" for n in range(1, 17)]
    assert sorted(rows) == sorted(expected)
    for row, cells in rows.items():
        if row.startswith("L"):
            assert cells[2] == "owner decision", row
        elif row in {"C-1", "C-6", "C-11"}:
            assert cells[2].startswith("**confirmed by the owner** (2026-10-20)"), row
        else:
            assert cells[2] == "accepted as written (owner 2026-10-20)", row
    assert f"**{UNLICENSED_USERS} users**" in rows["C-11"][1]
    assert rows["C-11"][2].endswith(f"with {UNLICENSED_USERS} users")
    assert "`bulk_import`" in rows["C-1"][1]
    assert "`-AllowUncoveredUpgrade`" in rows["C-6"][1]


def test_changelog_describes_the_offline_licensing() -> None:
    changelog = _read(ROOT / "CHANGELOG.md")
    heading = "### Offline licensing"
    assert changelog.index("## [Unreleased]") < changelog.index(heading)
    assert changelog.index(heading) < changelog.index("### Added — MVP, version")
    section = _flat(_section(changelog, heading, "\n### "))
    for needed in ("5651", "`0014_license`", "`license.check`", "-LicenseFile", "LISANS.md"):
        assert needed in section, needed


def _overstatement_texts() -> list[Path]:
    return [
        ROOT / "README.md",
        ROOT / "CHANGELOG.md",
        *sorted(DOCS.rglob("*.md")),
        ROOT / "tools" / "license-issuer" / "README.md",
        *sorted((ROOT / "web" / "src" / "locales").glob("*.json")),
    ]


def _fold(text: str) -> str:
    """Lower case with the Turkish dotless and dotted i folded, whitespace collapsed."""
    return _flat(text).lower().replace("i̇", "i").replace("ı", "i")


@pytest.mark.parametrize(
    "path", _overstatement_texts(), ids=lambda p: p.relative_to(ROOT).as_posix()
)
def test_no_text_claims_compliance_or_an_unbreakable_licence(path: Path) -> None:
    text = _fold(_read(path))
    assert [phrase for phrase in OVERSTATEMENTS if _fold(phrase) in text] == []


# -- browser administration (ADR-0014) ------------------------------------------------------------

#: The installer parameters that set the limits of the browser administration (admin\policy.json,
#: contract 2.16): declared without a default, so a choice once given is remembered.
ADMIN_POLICY_PARAMETERS: Final = (
    "SourceNetwork",
    "AdminChangeHoldHours",
    "ZamaneHoldHours",
    "DisableBrowserAdministration",
)


@pytest.mark.parametrize("name", SETTINGS_DOCS)
def test_settings_guides_name_the_limits_and_the_console_path(name: str) -> None:
    """AYARLAR.md / SETTINGS.md: the installer's limits exist as its parameters, the console
    commands exist (or their branch is pending), every event of the source ARCILOGGER-Admin is
    explained with its number, and the wording rule holds."""
    text = _doc(name)
    unquoted = re.sub(r"^> ?", "", text, flags=re.MULTILINE)
    assert "5651 yükümlülüklerini desteklemek için tasarlanmıştır" in _flat(unquoted)
    other = "SETTINGS.md" if name == "AYARLAR.md" else "AYARLAR.md"
    assert f"]({other})" in text
    for parameter in ADMIN_POLICY_PARAMETERS:
        assert f"`-{parameter}`" in text, parameter
        assert _exists(f"installer:Install-Arcilogger.ps1 -{parameter}"), parameter
    for command in ("approve-jar", "status", "versions"):
        assert f"arcilogger_core.admin {command}" in text, command
        name_ = f"admin:{command}"
        assert _exists(name_) or name_ in PENDING_FROM_PARALLEL_BRANCHES, name_
    for needed in ("admin\\policy.json", "ApplySystem", "NT SERVICE\\ArciAdmin", EVENT_SOURCE):
        assert needed in text, needed
    for code in ("R20-admin", "R20-config-change", "firewall_not_managed"):
        assert f"`{code}`" in text, code
    explained = {int(number) for number in re.findall(r"\b(41\d\d)\b", text)}
    assert {int(event) for event in Event} <= explained


def test_installer_and_first_steps_point_to_the_settings_guide() -> None:
    steps = _show_next_steps()
    assert "docs\\AYARLAR.md" in steps
    for name in ("ILK-ADIMLAR.md", "FIRST-STEPS.md", "KURULUM.md", "INSTALL.md"):
        guide = "AYARLAR.md" if name in ("ILK-ADIMLAR.md", "KURULUM.md") else "SETTINGS.md"
        assert f"]({guide})" in _doc(name), name


#: Section 2's severity words, per document (the first word of the severity cell).
SEVERITY_WORDS: Final[dict[str, set[str]]] = {
    "UYARILAR.md": {"Kritik", "Yüksek", "Orta", "Düşük", "Bilgi"},
    "ALERTS.md": {"Critical", "High", "Medium", "Low", "Info"},
}


@pytest.mark.parametrize("name", ["UYARILAR.md", "ALERTS.md"])
def test_alert_documents_list_every_lease_and_nat_cause(name: str) -> None:
    """Section 2 has a row for every ``R20-lease`` cause and the four ``R20-nat`` causes
    (ADR-0016, contract 2.6.3), each with a severity and the cause's one-line action of
    ``alerts/texts.py`` (the e-mail line); the rules' names are in the section's opening."""
    language = 0 if name == "UYARILAR.md" else 1
    section = _section(_doc(name), "## 2.", "## 3.")
    rows = _table_rows(section, r"`(R20-(?:lease|nat):[a-z-]+)(?::<[^`>]+>)?`")
    assert set(NAT_CAUSES) == {"conversion-failed", "invalid", "legacy-changed", "unmapped-transip"}
    expected = {f"R20-lease:{cause}" for cause in LEASE_CAUSES}
    expected |= {f"R20-nat:{cause}" for cause in NAT_CAUSES}
    assert sorted(rows) == sorted(expected)
    for key, cells in rows.items():
        rule, _, cause = key.partition(":")
        action = (LEASE_CAUSES if rule == "R20-lease" else NAT_CAUSES)[cause][language]
        assert re.split(r"[\s(;,]", cells[2])[0] in SEVERITY_WORDS[name], key
        assert cells[4].startswith(action), key
    intro = _flat(section.split("\n| ", 1)[0])
    for rule in ("R20-lease", "R20-nat"):
        assert f"`{rule}`" in intro
        assert (RULES[rule].name_tr, RULES[rule].name_en)[language] in intro, rule


#: The header of the reading table (Wave 3 contract 2.15, deviation d-23): one bilingual table, in
#: the report guide and in the auditor guide.
READING_TABLE_HEADER: Final = (
    "| PDF'te görülen / What the PDF shows | Kesin olan (kayıtta) / Exact (in the records) "
    "| Ayarlara dayanan / Configured | Varsayılan / Assumed |"
)


def _reading_table(name: str) -> list[list[str]]:
    """The cells of every row of ``name``'s reading table (the header and its rule left out)."""
    lines = _doc(name).splitlines()
    assert lines.count(READING_TABLE_HEADER) == 1, name
    start = lines.index(READING_TABLE_HEADER) + 2
    rows = list(itertools.takewhile(lambda line: line.startswith("| "), lines[start:]))
    return [_cells(line) for line in rows]


@pytest.mark.parametrize("name", ["RAPOR.md", "DENETCI-KILAVUZU.md"])
def test_reading_tables_cover_every_key_and_exit_label(name: str) -> None:
    """Wave 3 contract 3.9: the reading table of RAPOR.md and DENETCI-KILAVUZU.md names every
    explanation key the PDF prints (``READING_TABLE_KEYS``) in backticks in a row's first column,
    and no other key; and every Turkish exit label of the 2.12 label table is in a first
    column."""
    from arcilogger_api.reports.texts import EXIT_LABELS, READING_TABLE_KEYS

    rows = _reading_table(name)
    assert rows, name
    assert [row for row in rows if len(row) != 4] == [], name
    firsts = [row[0] for row in rows]
    named = [key for first in firsts for key in re.findall(r"`([a-z0-9_]+)`", first)]
    assert sorted(set(READING_TABLE_KEYS) - set(named)) == [], name
    assert sorted(set(named) - set(READING_TABLE_KEYS)) == [], name
    assert len(named) == len(set(named)), name
    labels = [tr for tr, _en in EXIT_LABELS.values()]
    assert [label for label in labels if not any(label in first for first in firsts)] == [], name
