"""A chain of gzip and xz days in both verifiers (Wave 5 W5-H; cases in ``sealed_format_cases``).

Both verifiers must agree on every case: an intact chain of mixed formats verifies, and a damaged
file of either format is found by the hash of the stored file or, when that matches (re-stamped),
by decompressing it, never by an exception and never as a shorter file.
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core import sealedfile
from arcilogger_core.evidence.builder import build_package
from arcilogger_core.evidence.model import RawRef
from arcilogger_core.evidence.standalone import VERIFY_PY
from arcilogger_core.evidence.standalone import verify as sv

from .sealed_format_cases import CASES, SealedFormatCase, core_errors, damage, standalone_codes


def _seal_chain(world: Any, days: list[Any], formats: tuple[str, ...], roots: Path) -> None:
    for day, compression in zip(days, formats, strict=True):
        world.reconfigure(seal={"quiet_seconds": 0, "compression": compression})
        world.busy_day(day)
        world.set_now(day + timedelta(days=1))
        assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
        for path in world.layout.vault_day(day).glob("*.log.*"):
            assert sealedfile.format_of(path) == compression
    roots.mkdir()
    (roots / "kok.pem").write_bytes(world.tsa.root_certificate.public_bytes(Encoding.PEM))


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_both_verifiers_agree_on_sealed_formats(
    world: Any,
    days: list[Any],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: SealedFormatCase,
) -> None:
    roots = tmp_path / "kokler"
    _seal_chain(world, days, case.formats, roots)
    damaged = damage(world.layout, days[-1], world.tsa, case.damage)

    errors = core_errors(world.layout, days, [world.tsa.root_certificate])
    if case.without_lzma:
        monkeypatch.setattr(sv, "lzma", None)
    passed, failures = standalone_codes(world.layout, roots)

    if case.core is None:
        assert errors == []
    else:
        assert damaged is not None
        assert len(errors) == 1, errors
        assert errors[0].startswith(damaged.relative_to(world.layout.vault).as_posix()), errors
        assert case.core in errors[0]
    if case.standalone is None:
        assert (passed, failures) == (True, set())
    else:
        assert (passed, failures) == (False, {case.standalone})


#: Runs verify.py as a script after taking lzma away, as a Python built without it would.
WITHOUT_LZMA = (
    "import runpy, sys; sys.modules['lzma'] = sys.modules['_lzma'] = None; "
    "sys.argv = sys.argv[1:]; runpy.run_path(sys.argv[0], run_name='__main__')"
)


def test_a_python_without_lzma_names_the_xz_days_it_could_not_read(
    world: Any, days: list[Any], tmp_path: Path
) -> None:
    """#136 item 2: without lzma, intact xz days are ``xz-unsupported``, never a mismatch that
    reads as tampering; the stored files still match their manifests, and gzip days verify."""
    roots = tmp_path / "kokler"
    _seal_chain(world, days, ("gzip", "xz", "xz"), roots)
    command = [sys.executable, "-I", "-S", "-c", WITHOUT_LZMA, str(VERIFY_PY)]
    result = subprocess.run(
        [*command, str(world.layout.vault), "--kokler", str(roots), "--json"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=300,
        check=False,
    )
    assert result.returncode == 1, result.stdout + result.stderr
    report = json.loads(result.stdout)
    failures = [f for f in report["findings"] if f["level"] == "fail"]
    logs = [p for day in days for p in sorted(world.layout.vault_day(day).glob("*.log.*"))]
    xz_logs = [p for p in logs if sealedfile.format_of(p) == "xz"]
    expected = []
    for log in xz_logs:
        path = log.relative_to(world.layout.vault).as_posix()  # 2026/10/11/core-r1.log.xz
        expected.append(("xz-unsupported", path[:10].replace("/", "-"), path))
    assert len(expected) == 4
    assert [(f["code"], f["where"], f["en"].split(":")[0]) for f in failures] == expected
    assert all("does not mean the file was changed" in f["en"] for f in failures)
    # the stored files still match their stamped manifests, gzip days included
    ok = {f["tr"].split(":")[0] for f in report["findings"] if f["code"] == "file-ok"}
    assert {p.relative_to(world.layout.vault).as_posix() for p in logs} <= ok


def test_an_extract_from_an_xz_day_without_lzma_is_xz_unsupported(
    world: Any, days: list[Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """#136 item 2: an extract taken from an xz day cannot be compared without lzma either; it
    is ``xz-unsupported`` at "alıntı", not ``extract-mismatch``. With lzma the package passes."""
    roots = tmp_path / "kokler"
    _seal_chain(world, days, ("gzip", "xz", "xz"), roots)
    info = build_package(
        world.layout,
        days,
        roots=[world.tsa.root_certificate],
        extract=[RawRef(days[2], "fortigate", 1), RawRef(days[0], "fortigate", 2)],
        out_dir=tmp_path / "out",
    )
    with sv.open_package(info.path) as package:
        assert sv.verify_package(package, roots_dir=roots).passed
    monkeypatch.setattr(sv, "lzma", None)
    with sv.open_package(info.path) as package:
        report = sv.verify_package(package, roots_dir=roots)
    failures = [(f.code, f.where) for f in report.findings if f.level == "fail"]
    assert ("xz-unsupported", "alıntı") in failures
    assert {code for code, _ in failures} == {"xz-unsupported"}
    assert not report.passed
    # the extract of the gzip day is still compared, line by line
    assert [f.tr.split(":")[0] for f in report.findings if f.code == "extract-ok"] == [
        f"extract/{days[0]}/fortigate.txt"
    ]
