"""The release guard (tools/release/check_release_date.py): a v* tag is built only when
RELEASE_DATE of arcilogger_core was bumped for it (licensing contract 5.1, 9.2)."""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from datetime import date
from pathlib import Path
from types import ModuleType

import pytest

import arcilogger_core

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "release" / "check_release_date.py"
WORKFLOW = ROOT / ".github" / "workflows" / "installer.yml"


@pytest.fixture(scope="module")
def guard() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_release_date_under_test", SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _init(tmp_path: Path, release_date: str, version: str = "1.2.3") -> Path:
    path = tmp_path / "__init__.py"
    path.write_text(
        f'__version__ = "{version}"\n\nRELEASE_DATE: Final = "{release_date}"\n', encoding="utf-8"
    )
    return path


@pytest.mark.parametrize(
    ("release", "committed", "ok"),
    [
        ("2026-11-20", "2026-11-20", True),  # bumped in the release commit
        ("2026-11-06", "2026-11-20", True),  # 14 days: a release branch tested for two weeks
        ("2026-11-05", "2026-11-20", False),  # 15 days: not bumped
        ("2026-10-20", "2027-03-01", False),  # the previous release's date
        ("2026-11-21", "2026-11-20", False),  # in the future
        ("2027-01-01", "2026-12-31", False),  # across a year
    ],
)
def test_check_accepts_only_the_fourteen_days_up_to_the_commit(
    guard: ModuleType, release: str, committed: str, ok: bool
) -> None:
    problem = guard.check(date.fromisoformat(release), date.fromisoformat(committed))
    assert (problem is None) == ok, problem
    if not ok:
        assert release in problem
        assert committed in problem


def test_the_messages_say_what_to_do(guard: ModuleType) -> None:
    old = guard.check(date(2026, 10, 20), date(2026, 12, 1))
    assert "42 days before the tagged commit (2026-12-01, Europe/Istanbul), more than 14" in old
    assert "Bump __version__ and RELEASE_DATE together" in old
    assert "core/src/arcilogger_core/__init__.py" in old
    assert "docs/adr/0007-windows-installer.md, section A1" in old
    future = guard.check(date(2026, 12, 2), date(2026, 12, 1))
    assert "1 day(s) after the tagged commit" in future
    assert "cannot be dated in the future" in future
    assert guard.check(date(2026, 11, 1), date(2026, 12, 1), max_age_days=30) is None


@pytest.mark.parametrize(
    ("committed", "day"),
    [
        ("2026-11-20T23:30:00+03:00", "2026-11-20"),
        ("2026-11-20T21:30:00+00:00", "2026-11-21"),  # 00:30 in Istanbul: the next day
        ("2026-11-20T20:59:59Z", "2026-11-20"),
        ("2026-11-20T15:00:00-08:00", "2026-11-21"),
    ],
)
def test_the_commit_day_is_the_site_day(guard: ModuleType, committed: str, day: str) -> None:
    assert guard.commit_day(committed) == date.fromisoformat(day)


@pytest.mark.parametrize("committed", ["2026-11-20T10:00:00", "yesterday", ""])
def test_a_commit_date_without_offset_is_refused(guard: ModuleType, committed: str) -> None:
    with pytest.raises(guard.GuardError):
        guard.commit_day(committed)


def test_the_release_date_line_is_read_like_the_bundle_script(guard: ModuleType) -> None:
    text = (ROOT / "core" / "src" / "arcilogger_core" / "__init__.py").read_text(encoding="utf-8")
    assert guard.read_release_date(text) == date.fromisoformat(arcilogger_core.RELEASE_DATE)
    assert guard.read_version(text) == arcilogger_core.__version__
    with pytest.raises(guard.GuardError, match="found 0"):
        guard.read_release_date('RELEASE_DATE = "2026-10-20"\n')
    with pytest.raises(guard.GuardError, match="found 2"):
        guard.read_release_date('RELEASE_DATE: Final = "2026-10-20"\n' * 2)
    with pytest.raises(guard.GuardError, match="is not a date"):
        guard.read_release_date('RELEASE_DATE: Final = "2026-02-30"\n')


def test_main_exit_codes(
    guard: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    init = _init(tmp_path, "2026-11-20")
    assert guard.main(["--init", str(init), "--commit-date", "2026-11-20T12:00:00+03:00"]) == 0
    assert "RELEASE_DATE 2026-11-20" in capsys.readouterr().out
    code = guard.main(
        ["--init", str(init), "--commit-date", "2027-01-05T12:00:00+03:00", "--tag", "v1.2.3"]
    )
    assert code == 1
    out = capsys.readouterr().out
    assert out.startswith("::error::Release v1.2.3 (__version__ 1.2.3): RELEASE_DATE 2026-11-20")
    assert guard.main(["--init", str(tmp_path / "missing.py"), "--commit-date", "x"]) == 2
    assert capsys.readouterr().out.startswith("::error::Release date guard:")
    assert guard.main(["--init", str(init), "--commit-date", "2026-11-20"]) == 2


def test_runs_as_a_script_against_this_checkout() -> None:
    """Standard library only, as the release job runs it; HEAD's date comes from git."""
    head = subprocess.run(
        ["git", "log", "-1", "--format=%cI", "HEAD"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if head.returncode != 0:
        pytest.skip("not a git checkout")
    result = subprocess.run(
        [sys.executable, "-I", str(SCRIPT), "--commit-date", head.stdout.strip()],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode in (0, 1), result.stdout + result.stderr
    assert f"__version__ {arcilogger_core.__version__}" in result.stdout
    without = subprocess.run(
        [sys.executable, "-I", str(SCRIPT)], capture_output=True, text=True, check=False
    )
    assert without.returncode == result.returncode, without.stdout + without.stderr


def _release_steps() -> list[str]:
    text = WORKFLOW.read_text(encoding="utf-8")
    job = text[text.index("\n  release:\n") :]
    return re.split(r"(?m)^      - ", job)[1:]


def test_the_release_job_runs_the_guard_for_tags_before_building() -> None:
    steps = _release_steps()
    guard_steps = [
        i for i, s in enumerate(steps) if "python -I tools/release/check_release_date.py" in s
    ]
    assert len(guard_steps) == 1, steps
    step = steps[guard_steps[0]]
    assert "if: startsWith(github.ref, 'refs/tags/v')" in step, "release tags only"
    assert "python -I tools/release/check_release_date.py" in step
    assert '--tag "$env:GITHUB_REF_NAME"' in step
    assert "if ($LASTEXITCODE -ne 0)" in step
    build = next(i for i, s in enumerate(steps) if "Build-ArciBundle.ps1" in s)
    version = next(i for i, s in enumerate(steps) if "Version (a tag must be v<version>)" in s)
    assert version < guard_steps[0] < build
    # The checkout's HEAD is the tagged commit (the default fetch depth of 1 is enough).
    checkout = next(s for s in steps if "actions/checkout@" in s)
    assert "fetch-depth" not in checkout
