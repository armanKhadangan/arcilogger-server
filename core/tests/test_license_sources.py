"""Source rules of the licensing package (licensing contract; A-T3, A-T17, A-T18, A-T19).

* the production key set and revocation list: format, empty today, never extended at run time;
* the evidence path never imports the licence code;
* one place for every licence decision (no second module test, signature check or date rule);
* no private key material anywhere in the repository;
* the test helpers never ship in the wheel.
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tomllib
from pathlib import Path
from types import MappingProxyType

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from arcilogger_core.license import keys, revoked
from arcilogger_core.license.codec import b64url_decode_strict
from arcilogger_core.license.document import KEY_ID_PATTERN, LICENSE_ID_PATTERN

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "core" / "src" / "arcilogger_core" / "license"
SOURCES = sorted(path for path in ROOT.glob("*/src/**/*.py"))
TOOLS = sorted((ROOT / "tools").rglob("*.py"))


def _relative(path: Path) -> str:
    return path.relative_to(ROOT).as_posix()


def _in_package(path: Path) -> bool:
    return PACKAGE in path.parents


# -- A-T3 the production key set and the revocation list ------------------------------------------


def test_the_production_key_set_is_empty_and_well_formed() -> None:
    assert isinstance(keys.TRUSTED_KEYS, MappingProxyType)
    for key_id, value in keys.TRUSTED_KEYS.items():
        assert KEY_ID_PATTERN.fullmatch(key_id), key_id
        assert not key_id.startswith("test-"), key_id
        Ed25519PublicKey.from_public_bytes(b64url_decode_strict(value, 32))
    # Empty until the owner commits the first public key (then this line lists it).
    assert dict(keys.TRUSTED_KEYS) == {}


def test_the_revocation_list_is_empty_and_well_formed() -> None:
    assert isinstance(revoked.REVOKED_LICENSE_IDS, frozenset)
    for license_id in revoked.REVOKED_LICENSE_IDS:
        assert LICENSE_ID_PATTERN.fullmatch(license_id), license_id
    assert not revoked.REVOKED_LICENSE_IDS


@pytest.mark.parametrize("name", ["keys.py", "revoked.py"])
def test_the_key_files_read_nothing_at_run_time(name: str) -> None:
    text = (PACKAGE / name).read_text(encoding="utf-8")
    for forbidden in ("os.environ", "getenv", "load_config", "open(", "import os", "Path("):
        assert forbidden not in text, (name, forbidden)
    tree = ast.parse(text)
    imported = {
        alias.name if isinstance(node, ast.Import) else node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.Import | ast.ImportFrom)
        for alias in node.names
    }
    assert imported <= {"__future__", "collections.abc", "types", "typing"}, imported
    assert "test-" not in text.split('"""', 2)[-1]  # no test key id below the docstring


def _assigned_names(tree: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign | ast.AugAssign):
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name):
                names.add(target.id)
            elif isinstance(target, ast.Attribute):
                names.add(target.attr)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name | ast.Attribute)
            and (node.func.id if isinstance(node.func, ast.Name) else node.func.attr)
            in ("setattr", "delattr")
        ):
            names |= {
                arg.value
                for arg in node.args
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
            }
    return names


def test_only_the_test_helpers_swap_the_key_set_or_the_revocation_list() -> None:
    allowed = {
        "core/src/arcilogger_core/license/keys.py": {"TRUSTED_KEYS"},
        "core/src/arcilogger_core/license/revoked.py": {"REVOKED_LICENSE_IDS"},
        "core/src/arcilogger_core/license/testing.py": {"TRUSTED_KEYS", "REVOKED_LICENSE_IDS"},
    }
    offending = []
    for path in [*SOURCES, *TOOLS]:
        names = _assigned_names(ast.parse(path.read_text(encoding="utf-8")))
        found = names & {"TRUSTED_KEYS", "REVOKED_LICENSE_IDS"}
        if found - allowed.get(_relative(path), set()):
            offending.append(_relative(path))
    assert offending == []


def test_the_verifier_reads_the_key_set_at_call_time() -> None:
    """``from … import TRUSTED_KEYS`` would copy the set at import time (tests could not swap
    it, and a swapped set would never reach the verifier)."""
    for path in [*SOURCES, *TOOLS]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                names = {alias.name for alias in node.names}
                assert not names & {"TRUSTED_KEYS", "REVOKED_LICENSE_IDS"}, _relative(path)


# -- A-T17 the evidence path never imports the licence code --------------------------------------

EVIDENCE_PATH = [
    "arcilogger_collector",
    "arcilogger_collector.service",
    "arcilogger_collector.cli",
    "arcilogger_ingest",
    "arcilogger_ingest.service",
    "arcilogger_ingest.cli",
    "arcilogger_core.cli",
    "arcilogger_core.seal",
    "arcilogger_core.retention",
    "arcilogger_core.replica",
    "arcilogger_core.chain",
    "arcilogger_core.clockcheck",
    "arcilogger_core.credit",
    "arcilogger_core.tsa",
    "arcilogger_core.evidence.builder",
    "arcilogger_core.evidence.runner",
    "arcilogger_core.evidence.standalone.verify",
]


def test_the_evidence_path_never_imports_the_licence_code() -> None:
    check = (
        "import importlib, sys\n"
        f"for name in {EVIDENCE_PATH!r}:\n"
        "    importlib.import_module(name)\n"
        "loaded = [n for n in sys.modules if n.startswith('arcilogger_core.license')]\n"
        "print(','.join(sorted(loaded)))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", check], capture_output=True, check=True, timeout=120
    )
    assert completed.stdout.decode().strip() == ""


def test_the_core_command_line_runs_without_the_licence_package() -> None:
    check = (
        "import sys\n"
        "sys.modules['arcilogger_core.license'] = None\n"
        "import arcilogger_core.cli, arcilogger_core.seal, arcilogger_core.retention\n"
        "import arcilogger_core.evidence.builder, arcilogger_core.evidence.standalone.verify\n"
        "print('ok')\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", check], capture_output=True, check=True, timeout=120
    )
    assert completed.stdout.decode().strip() == "ok"


# -- A-T18 one place for every decision -----------------------------------------------------------

DATE_FIELDS = {"maintenance_until", "valid_until"}


def _mentions_date_field(node: ast.AST) -> bool:
    for child in ast.walk(node):
        if isinstance(child, ast.Attribute) and child.attr in DATE_FIELDS:
            return True
        if isinstance(child, ast.Name) and child.id in DATE_FIELDS:
            return True
        if (
            isinstance(child, ast.Subscript)
            and isinstance(child.slice, ast.Constant)
            and child.slice.value in DATE_FIELDS
        ):
            return True
    return False


def _is_none_test(node: ast.Compare) -> bool:
    """``x is None`` / ``x is not None``: presence for serialisation, not a date rule."""
    return all(isinstance(op, ast.Is | ast.IsNot) for op in node.ops) and all(
        isinstance(item, ast.Constant) and item.value is None for item in node.comparators
    )


def _is_text(node: ast.expr) -> bool:
    """A string literal or an f-string: ``"…" + x`` builds text, it decides nothing."""
    return isinstance(node, ast.JoinedStr) or (
        isinstance(node, ast.Constant) and isinstance(node.value, str)
    )


def _call_name(node: ast.Call) -> str:
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return ""


def _product_sources() -> list[Path]:
    return [path for path in SOURCES if not _in_package(path)]


def test_no_second_module_test_outside_the_licence_package() -> None:
    allowed = {"db/src/arcilogger_db/license_guard.py"}  # the fallback of a broken subsystem
    offending = []
    for path in _product_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
                and node.name == "module_enabled"
                and _relative(path) not in allowed
            ):
                offending.append(f"{_relative(path)}:{node.lineno}")
    assert offending == []


#: Ed25519 also signs the request files of the browser administration (ADR-0014), with keys of
#: their own; that package never imports the licence code (test_independence.py).
ADMIN_CHANNEL = ROOT / "core" / "src" / "arcilogger_core" / "admin"


def test_no_signature_check_outside_the_licence_package() -> None:
    offending = [
        _relative(path)
        for path in _product_sources()
        if ADMIN_CHANNEL not in path.parents
        and re.search(r"Ed25519PublicKey|ed25519", path.read_text(encoding="utf-8"))
    ]
    assert offending == []


def test_no_date_rule_of_a_licence_outside_the_licence_package() -> None:
    """Comparing or computing with ``maintenance_until``/``valid_until`` is a licence decision;
    serialising the values (``arcilogger_db/licensing.py``) is not."""
    allowed = {"db/src/arcilogger_db/licensing.py"}
    offending = [
        f"{_relative(path)}:{line}"
        for path in _product_sources()
        if _relative(path) not in allowed
        for line in date_rule_lines(path.read_text(encoding="utf-8"))
    ]
    assert offending == []


def date_rule_lines(source: str) -> list[int]:
    """Lines that compare or compute with a licence date (A-T18)."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        decision = (isinstance(node, ast.Compare) and not _is_none_test(node)) or (
            isinstance(node, ast.BinOp) and not _is_text(node.left) and not _is_text(node.right)
        )
        if decision and _mentions_date_field(node):
            lines.append(getattr(node, "lineno", 0))
        if (
            isinstance(node, ast.Call)
            and _call_name(node) in ("max", "min", "sorted")
            and any(_mentions_date_field(arg) for arg in node.args)
        ):
            lines.append(node.lineno)
    return lines


TEST_ONLY_ARGUMENTS = {
    "load_license": {"keys"},
    "load_status": {"release_date", "version"},
    "trusted_now": {"release_date"},
}


def test_only_argument_lines(source: str) -> list[int]:
    """Calls that pass an argument only tests and the sample capture may pass (A-T18)."""
    lines = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and _call_name(node) in TEST_ONLY_ARGUMENTS:
            used = {keyword.arg for keyword in node.keywords if keyword.arg}
            unpacked = any(keyword.arg is None for keyword in node.keywords)
            if used & TEST_ONLY_ARGUMENTS[_call_name(node)] or unpacked:
                lines.append(node.lineno)
    return lines


test_only_argument_lines.__test__ = False  # type: ignore[attr-defined]  # a helper


def test_no_product_code_passes_test_only_arguments() -> None:
    offending = [
        f"{_relative(path)}:{line}"
        for path in _product_sources()
        for line in test_only_argument_lines(path.read_text(encoding="utf-8"))
    ]
    assert offending == []


def test_the_source_scans_find_what_they_look_for() -> None:
    decisions = [
        "if status.valid_until < today: pass",
        "late = date.today() > payload.maintenance_until",
        "left = doc['maintenance_until'] - today",
        "end = max(a, status.valid_until)",
        "ok = valid_until == other",
    ]
    for snippet in decisions:
        assert date_rule_lines(snippet) == [1], snippet
    harmless = [
        "x = status.valid_until.isoformat() if status.valid_until is not None else None",
        "text = f'{status.maintenance_until}'",
        "text = 'until ' + status.maintenance_until.isoformat()",
        "row = {'valid_until': status.valid_until}",
        "flag = status.maintenance_expired and status.maintenance_days_left < 3",
    ]
    for snippet in harmless:
        assert date_rule_lines(snippet) == [], snippet
    assert test_only_argument_lines("load_license(data, keys={})") == [1]
    assert test_only_argument_lines("load_status(s, config=c, layout=l, version='x')") == [1]
    assert test_only_argument_lines("trusted_now(l, c, release_date=d)") == [1]
    assert test_only_argument_lines("load_status(s, **options)") == [1]
    assert test_only_argument_lines("load_status(s, config=c, layout=l, clock=k)") == []
    assert test_only_argument_lines("trusted_now(l, c, clock=k)") == []


def test_the_licence_package_itself_never_passes_keys() -> None:
    for path in PACKAGE.glob("*.py"):
        if path.name == "testing.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) == "load_license":
                assert not [k for k in node.keywords if k.arg == "keys"], _relative(path)


# -- A-T19 no private key material in the repository ----------------------------------------------

#: Built from parts so that this file itself does not contain the marker.
PRIVATE_KEY_MARKER = re.compile(rb"-----BEGIN [A-Z0-9 ]*" + b"PRIVATE" + rb" KEY-----")


def _tracked_files() -> list[Path]:
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True, timeout=60
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    return [ROOT / name.decode() for name in listed.split(b"\0") if name]


def test_no_private_key_material_in_tracked_files() -> None:
    offending = []
    for path in _tracked_files():
        if not path.is_file():
            continue
        if PRIVATE_KEY_MARKER.search(path.read_bytes()):
            offending.append(_relative(path))
    assert offending == []


def test_no_private_key_material_in_the_licence_sources() -> None:
    for path in [*PACKAGE.glob("*.py"), *TOOLS, *Path(__file__).parent.glob("test_license_*")]:
        assert not PRIVATE_KEY_MARKER.search(path.read_bytes()), _relative(path)


# -- 9.2 the test helpers never ship -------------------------------------------------------------


def test_the_wheel_excludes_the_test_helpers() -> None:
    project = tomllib.loads((ROOT / "core" / "pyproject.toml").read_text(encoding="utf-8"))
    excluded = project["tool"]["hatch"]["build"]["targets"]["wheel"]["exclude"]
    assert "src/arcilogger_core/license/testing.py" in excluded
    assert (ROOT / "core" / "src" / "arcilogger_core" / "license" / "testing.py").is_file()


def test_the_package_never_imports_the_test_helpers() -> None:
    for path in PACKAGE.glob("*.py"):
        if path.name == "testing.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.endswith("testing"), _relative(path)
                assert "testing" not in {alias.name for alias in node.names}, _relative(path)
            if isinstance(node, ast.Import):
                assert not any(a.name.endswith(".testing") for a in node.names), _relative(path)
