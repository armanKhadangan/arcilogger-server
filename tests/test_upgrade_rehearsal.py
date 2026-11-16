"""The guard of the upgrade rehearsal (``tools/bench/upgrade_rehearsal.py``, #297): it creates and
drops its ``--database``, so it refuses, before anything connects, every name but its own kind
and the database of ``--url`` itself. No database is needed."""

from __future__ import annotations

import importlib.util
import sys
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "bench" / "upgrade_rehearsal.py"
#: A server nobody reaches (RFC 5737): a refusal must come before any connection.
SERVER = "postgresql://rehearsal:example@192.0.2.10:5432/postgres"


@cache
def rehearsal() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_upgrade_rehearsal", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("name", ["arci_rehearsal", "arci_rehearsal_297", "arci_rehearsal_pg10"])
def test_a_rehearsal_database_is_accepted(name: str) -> None:
    assert rehearsal().check_database(name, SERVER) == name


@pytest.mark.parametrize(
    "name",
    [
        "arcilogger",
        "postgres",
        "template1",
        "rehearsal",
        "my_arci_rehearsal",
        "ARCI_REHEARSAL_1",
        "arci_rehearsal-1",
        'arci_rehearsal"; DROP DATABASE arcilogger; --',
        "arci_rehearsal 1",
        "",
    ],
)
def test_any_other_database_is_refused(name: str) -> None:
    with pytest.raises(ValueError, match="name starts with arci_rehearsal") as refused:
        rehearsal().check_database(name, SERVER)
    assert "reddedildi" in str(refused.value)


def test_the_database_of_the_url_is_refused() -> None:
    server = "postgresql://rehearsal:example@192.0.2.10:5432/arci_rehearsal_297"
    with pytest.raises(ValueError, match="--url connects to this database"):
        rehearsal().check_database("arci_rehearsal_297", server)
    assert rehearsal().check_database("arci_rehearsal_other", server) == "arci_rehearsal_other"


@pytest.mark.parametrize(
    ("database", "url"),
    [
        ("arcilogger", SERVER),
        ("arci_rehearsal_297", SERVER.replace("/postgres", "/arci_rehearsal_297")),
    ],
)
def test_the_script_refuses_before_it_connects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], database: str, url: str
) -> None:
    module = rehearsal()

    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("the script went on after the refusal")

    for name in ("engine_from_url", "template", "create_database", "drop_database"):
        monkeypatch.setattr(module, name, never)
    with pytest.raises(SystemExit) as stopped:
        module.main(["--url", url, "--database", database])
    assert stopped.value.code == 2
    assert "reddedildi" in capsys.readouterr().err


@pytest.mark.parametrize("helper", ["create_database", "drop_database"])
def test_the_helpers_refuse_too(monkeypatch: pytest.MonkeyPatch, helper: str) -> None:
    module = rehearsal()

    def never(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("connected")

    monkeypatch.setattr(module, "engine_from_url", never)
    with pytest.raises(ValueError, match="refused"):
        getattr(module, helper)(SERVER, "arcilogger")
