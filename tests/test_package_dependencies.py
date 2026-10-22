"""Every workspace package declares the ARCILOGGER packages it imports.

In the development venv every package is installed, so a missing declaration only shows up
when a single wheel is installed on the server (e.g. the API without the lookup types).
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("core", "collector", "db", "ingest", "worker", "api")
IMPORT = re.compile(r"^\s*(?:from|import)\s+(arcilogger_[a-z]+)", re.MULTILINE)


def _declared(package: str) -> set[str]:
    project = tomllib.loads((ROOT / package / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    names = {re.split(r"[ <>=!~;\[]", dep, maxsplit=1)[0] for dep in dependencies}
    return {name.replace("-", "_") for name in names if name.startswith("arcilogger-")}


def _imported(package: str) -> set[str]:
    found: set[str] = set()
    for path in (ROOT / package / "src").rglob("*.py"):
        found |= set(IMPORT.findall(path.read_text(encoding="utf-8")))
    return found


@pytest.mark.parametrize("package", PACKAGES)
def test_imports_are_declared_dependencies(package: str) -> None:
    own = f"arcilogger_{package}"
    assert _imported(package) - {own} <= _declared(package)


def test_the_api_ships_with_the_lookup_types() -> None:
    assert "arcilogger_worker" in _declared("api")
