"""The CI workflows run the gates the specification and the contracts name.

Text checks (no YAML parser is a project dependency): the ``run:`` lines of each job are read
from the workflow file as written.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import tomllib
from pathlib import Path
from types import ModuleType

import pytest

REPO = Path(__file__).parents[1]
WORKFLOWS = REPO / ".github" / "workflows"


def _jobs(workflow: Path) -> dict[str, str]:
    """Job id -> the text of that job (from its header to the next job)."""
    text = workflow.read_text(encoding="utf-8")
    body = text[text.index("\njobs:\n") :]
    headers = list(re.finditer(r"(?m)^  ([A-Za-z0-9_-]+):\s*$", body))
    jobs = {}
    for index, header in enumerate(headers):
        end = headers[index + 1].start() if index + 1 < len(headers) else len(body)
        jobs[header.group(1)] = body[header.start() : end]
    return jobs


def _runs(job: str) -> list[str]:
    return [m.group(1).strip() for m in re.finditer(r"(?m)^\s+run:\s*(\S.*)$", job)]


def test_linux_job_runs_every_gate_in_order() -> None:
    """Spec 17 coverage gates and the web contracts (OpenAPI types and the E2E samples)."""
    runs = _runs(_jobs(WORKFLOWS / "ci.yml")["python-linux"])
    expected = [
        "uv run poe lint",
        "uv run poe test",
        "uv run python web/scripts/export_api_contract.py --check",
        "uv run python web/scripts/capture_api_samples.py --check",
        "uv run poe test-core",
        "uv run poe test-ingest",
        "uv run poe test-api",
        "uv run poe test-perf",
    ]
    positions = [runs.index(command) for command in expected]
    assert positions == sorted(positions), runs


def test_coverage_gates_have_the_spec_thresholds() -> None:
    tasks = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["poe"]
    gates = {"test-core": ("arcilogger_core", 90), "test-ingest": ("arcilogger_ingest", 85)}
    gates["test-api"] = ("arcilogger_api", 80)
    for name, (package, threshold) in gates.items():
        command = tasks["tasks"][name]
        assert f"--cov={package}" in command, name
        assert f"--cov-fail-under={threshold}" in command, name


def test_windows_end_to_end_job_installs_with_the_database() -> None:
    """The installer E2E runs the database services and a task under their real accounts."""
    job = _jobs(WORKFLOWS / "installer.yml")["installer-e2e"]
    start = job.index("Start the PostgreSQL preinstalled on the runner image")
    e2e = job.index("Install, upgrade, roll back and remove on this runner")
    assert start < e2e
    for variable in ("ARCILOGGER_E2E_PGUSER", "ARCILOGGER_E2E_PGPASSWORD", "ARCILOGGER_E2E_PGBIN"):
        assert f'"{variable}=' in job, variable
    assert re.search(r"(?m)^\s+ARCILOGGER_E2E_DB: '1'$", job[e2e:])
    assert "-k test_real_install_upgrade_rollback_and_uninstall" in job[e2e:]


def _push_paths(workflow: Path) -> list[str]:
    """The ``on: push: paths:`` filter of a workflow, as written (one quoted glob per line)."""
    text = workflow.read_text(encoding="utf-8")
    block = re.search(r"(?m)^on:\n  push:\n    paths:\n((?:      - .*\n)+)", text)
    assert block, "installer.yml: no push paths filter where expected"
    return [line.split("- ", 1)[1].strip().strip("'\"") for line in block.group(1).splitlines()]


def test_windows_end_to_end_runs_when_the_installed_code_changes() -> None:
    """The E2E checks the Python services and tasks under the installed NTFS rights: a change in
    any workspace package or in the locked dependencies triggers it, not only a deploy change
    (a package needing a right the installer does not grant must fail there)."""
    paths = _push_paths(WORKFLOWS / "installer.yml")
    workspace = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    members = workspace["tool"]["uv"]["workspace"]["members"]
    assert members, "no uv workspace members"
    expected = [f"{member}/**" for member in members]
    expected += ["deploy/windows/**", ".github/workflows/installer.yml"]
    expected += ["pyproject.toml", "uv.lock"]
    missing = [path for path in expected if path not in paths]
    assert not missing, missing


def _steps(job: str) -> list[str]:
    """The text of each step of a job, as written (steps start with "      - " in these files)."""
    return re.split(r"(?m)^      - ", job)[1:]


def test_every_action_is_pinned_by_its_commit_sha() -> None:
    for workflow in sorted(WORKFLOWS.glob("*.yml")):
        text = workflow.read_text(encoding="utf-8")
        for action in re.findall(r"(?m)^\s+(?:- )?uses:\s*(\S+)", text):
            assert re.fullmatch(r"[\w.-]+/[\w./-]+@[0-9a-f]{40}", action), (workflow.name, action)


def test_release_job_runs_only_for_tags_and_manual_runs() -> None:
    """A v* tag runs the release job; ordinary branch pushes keep their path filter."""
    workflow = (WORKFLOWS / "installer.yml").read_text(encoding="utf-8")
    trigger = workflow[workflow.index("\non:\n") : workflow.index("\npermissions:")]
    assert re.search(r"(?m)^    branches:\n      - '\*\*'$", trigger), "branch pushes still run"
    assert re.search(r"(?m)^    tags:\n      - 'v\*'$", trigger)
    assert re.search(r"(?m)^  workflow_dispatch:$", trigger)
    release = _jobs(WORKFLOWS / "installer.yml")["release"]
    condition = (
        "if: github.event_name == 'workflow_dispatch' || startsWith(github.ref, 'refs/tags/v')"
    )
    assert condition in release
    assert "needs: [installer, installer-e2e]" in release, "built only when the tests pass"
    assert 'throw "Tag $env:GITHUB_REF_NAME does not name the version' in release


def test_only_the_release_job_may_write() -> None:
    workflow = (WORKFLOWS / "installer.yml").read_text(encoding="utf-8")
    assert re.search(r"(?m)^permissions:\n  contents: read$", workflow)
    jobs = _jobs(WORKFLOWS / "installer.yml")
    assert re.search(r"(?m)^    permissions:\n      contents: write$", jobs["release"])
    for name, job in jobs.items():
        if name != "release":
            assert "permissions:" not in job, name


def test_release_job_builds_the_setup_and_drafts_a_release() -> None:
    """Setup.exe with Inno Setup, the bundle zip, SHA256SUMS.txt and the SBOM: as an artifact
    always, as a DRAFT release only for a tag (docs/adr/0007, amendment 2026-10-19)."""
    steps = _steps(_jobs(WORKFLOWS / "installer.yml")["release"])

    def step(needle: str) -> int:
        found = [index for index, text in enumerate(steps) if needle in text]
        assert len(found) == 1, needle
        return found[0]

    sbom, build = step("anchore/sbom-action@"), step("Build-ArciBundle.ps1")
    sums, upload = step("SHA256SUMS.txt and the job summary"), step("actions/upload-artifact@")
    draft = step("gh release create")
    assert sbom < build < sums < upload < draft
    assert "upload-release-assets: false" in steps[sbom]
    assert "format: spdx-json" in steps[sbom]
    assert "-RequireInno" in steps[build]
    assert "-NoInno" not in steps[build]
    assert "shell: powershell" in steps[build], "Windows PowerShell 5.1, as on the admin PC"
    for name in ("ARCILOGGER-Setup-$version.exe", "ARCILOGGER-$version.zip", ".fingerprint.txt"):
        assert name in steps[build], name
    assert "'{0}  {1}'" in steps[sums], "the format sha256sum -c reads"
    assert "if-no-files-found: error" in steps[upload]
    assert "if: startsWith(github.ref, 'refs/tags/v')" in steps[draft]
    assert "--draft" in steps[draft]
    assert "GH_TOKEN: ${{ github.token }}" in steps[draft]
    assert "if ($LASTEXITCODE -ne 0)" in steps[draft]


# --- E2E sample comparison (web/scripts/capture_api_samples.py) ---------------------------------


@pytest.fixture(scope="module")
def capture() -> ModuleType:
    path = REPO / "web" / "scripts" / "capture_api_samples.py"
    spec = importlib.util.spec_from_file_location("capture_api_samples_under_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _diff(capture: ModuleType, old: object, new: object) -> list[str]:
    found: list[str] = capture.differences(capture.shape(old), capture.shape(new))
    return found


def test_sample_check_sees_a_renamed_enum_value(capture: ModuleType) -> None:
    assert _diff(capture, {"state": "late"}, {"state": "sealed_late"})
    assert _diff(capture, {"outcome": {"mode": "exact"}}, {"outcome": {"mode": "exact_match"}})
    days = {"days": [{"state": "late", "tone": "warning"}, {"state": "sealed", "tone": "ok"}]}
    renamed = {"days": [{"state": "late", "tone": "warn"}, {"state": "sealed", "tone": "ok"}]}
    assert _diff(capture, days, renamed)
    vanished = {"days": [{"state": "sealed", "tone": "ok"}, {"state": "sealed", "tone": "ok"}]}
    assert _diff(capture, days, vanished), "a state the samples show is no longer produced"
    assert _diff(capture, {"code": "raw_access_denied"}, {"code": "raw_io_error"})


def test_sample_check_ignores_messages_times_and_nulls(capture: ModuleType) -> None:
    assert not _diff(capture, {"state": None}, {"state": "late"})
    assert not _diff(capture, {"state": "late"}, {"state": None})
    assert not _diff(capture, {"at": "2026-10-13T04:00:01Z"}, {"at": "2026-10-14T04:00:01Z"})
    assert not _diff(capture, {"message_tr": "Gün mühürlendi."}, {"message_tr": "Başka metin."})
    same = {"days": [{"state": "late"}, {"state": "sealed"}, {"state": "late"}]}
    assert not _diff(capture, same, {"days": [{"state": "sealed"}, {"state": "late"}]})
    # Types still count: a string field becoming a number is a difference.
    assert _diff(capture, {"at": "2026-10-13"}, {"at": 20261013})
