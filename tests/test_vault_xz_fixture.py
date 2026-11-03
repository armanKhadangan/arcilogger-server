"""A sealed xz day as the sealer wrote it (``tests/fixtures/vault-xz``, Wave 5 W5-H).

The bytes are pinned like evidence. Every reader of a sealed day must read them: the core
verifier and the standalone verifier (in an isolated Python, as an auditor runs it) verify the
day; ARCILOGGER's reader, Python's ``lzma`` and the ``xz`` and 7-Zip tools (when installed; H6)
give the content the manifest records. The fixture's folder says how it was made.
"""

from __future__ import annotations

import hashlib
import json
import lzma
import os
import shutil
import subprocess
from datetime import date
from pathlib import Path

import pytest
from cryptography import x509

from arcilogger_core import sealedfile
from arcilogger_core.chain import verify_day
from arcilogger_core.evidence.runner import run_verifier
from arcilogger_core.layout import Layout

FIXTURE = Path(__file__).parent / "fixtures" / "vault-xz"
DAY = date(2026, 10, 12)
DAY_FOLDER = FIXTURE / "2026" / "10" / "12"
SEALED = DAY_FOLDER / sealedfile.log_name("fortigate", "xz")
ROOTS = FIXTURE / "kokler"


def _pinned() -> list[tuple[str, str]]:
    lines = (FIXTURE / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    return [(name, digest) for digest, name in (line.split("  ", 1) for line in lines)]


def _entry() -> dict[str, object]:
    manifest = json.loads((DAY_FOLDER / f"manifest-{DAY}.json").read_bytes())
    entry: dict[str, object] = manifest["files"][0]
    return entry


def _vault(tmp_path: Path) -> Layout:
    layout = Layout(tmp_path / "data")
    shutil.copytree(DAY_FOLDER, layout.vault_day(DAY))
    return layout


@pytest.mark.parametrize(("name", "digest"), _pinned())
def test_fixture_bytes_unchanged(name: str, digest: str) -> None:
    assert hashlib.sha256((FIXTURE / name).read_bytes()).hexdigest() == digest


def test_every_fixture_file_is_pinned() -> None:
    present = {
        path.relative_to(FIXTURE).as_posix()
        for path in FIXTURE.rglob("*")
        if path.is_file() and path.name not in {"SHA256SUMS", "make_fixture.py"}
    }
    assert present == {name for name, _ in _pinned()}


def test_every_pinned_file_is_in_the_repository() -> None:
    """A pinned file git ignores (``*.pem`` is) would pass here and be missing in CI."""
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z", "--", "."], cwd=FIXTURE, capture_output=True, check=True
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("git is not available")
    tracked = {name.decode() for name in listed.split(b"\0") if name}
    assert {name for name, _ in _pinned()} <= tracked


def test_the_file_has_many_streams_and_every_reader_gives_the_manifest_content() -> None:
    assert len(sealedfile.xz_streams(SEALED)) > 5
    entry = _entry()
    assert sealedfile.digest(SEALED) == (entry["sha256_uncompressed"], entry["lines"])
    content = lzma.decompress(SEALED.read_bytes())  # the standard library reads every stream
    assert hashlib.sha256(content).hexdigest() == entry["sha256_uncompressed"]


def test_the_core_verifier_verifies_the_day(tmp_path: Path) -> None:
    roots = x509.load_pem_x509_certificates((ROOTS / "mock-root.crt").read_bytes())
    check = verify_day(_vault(tmp_path), DAY, roots, deep=True)
    assert check.errors == []


def test_the_standalone_verifier_verifies_the_day_in_an_isolated_python(tmp_path: Path) -> None:
    layout = _vault(tmp_path)
    result = run_verifier(layout.vault, "--json", "--kokler", str(ROOTS), timeout=300)
    report = json.loads(result.stdout)
    failures = [finding for finding in report["findings"] if finding["level"] == "fail"]
    assert (result.returncode, failures) == (0, [])
    assert {"file-ok", "token-ok"} <= {finding["code"] for finding in report["findings"]}


@pytest.mark.parametrize("tool", ["xz", "7z"])
def test_the_auditors_tools_read_every_stream(tool: str) -> None:
    """``xz -dc`` and 7-Zip (H6, sahada doğrula X1) give the whole content, not the first
    stream; skipped where the tool is not installed (the Windows runner has 7-Zip)."""
    command = {"xz": ["-dc"], "7z": ["e", "-so"]}[tool]
    program = shutil.which(tool) or (shutil.which("7za") if tool == "7z" else None)
    for variable in ("ProgramFiles", "ProgramFiles(x86)") if tool == "7z" else ():
        base = os.environ.get(variable)
        if program is None and base and (Path(base) / "7-Zip" / "7z.exe").is_file():
            program = str(Path(base) / "7-Zip" / "7z.exe")
    if program is None:
        pytest.skip(f"{tool} is not installed here")
    out = subprocess.run([program, *command, str(SEALED)], capture_output=True, check=True).stdout
    assert hashlib.sha256(out).hexdigest() == _entry()["sha256_uncompressed"]
