"""A chain of gzip and xz days in both verifiers (Wave 5 W5-H; cases in ``sealed_format_cases``).

Both verifiers must agree on every case: an intact chain of mixed formats verifies, and a damaged
file of either format is found by the hash of the stored file or, when that matches (re-stamped),
by decompressing it, never by an exception and never as a shorter file.
"""

from __future__ import annotations

import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core import sealedfile
from arcilogger_core.evidence import RawRef, build_package
from arcilogger_core.evidence.standalone import verify as sv

from .sealed_format_cases import CASES, SealedFormatCase, core_errors, damage, standalone_codes


@pytest.mark.parametrize("case", CASES, ids=[case.name for case in CASES])
def test_both_verifiers_agree_on_sealed_formats(
    world: Any, days: list[Any], tmp_path: Path, case: SealedFormatCase
) -> None:
    for day, compression in zip(days, case.formats, strict=True):
        world.reconfigure(seal={"quiet_seconds": 0, "compression": compression})
        world.busy_day(day)
        world.set_now(day + timedelta(days=1))
        assert [o.status for o in world.sealer().seal_due()] == ["sealed"]
        for path in world.layout.vault_day(day).glob("*.log.*"):
            assert sealedfile.format_of(path) == compression
    damaged = damage(world.layout, days[-1], world.tsa, case.damage)

    errors = core_errors(world.layout, days, [world.tsa.root_certificate])
    roots = tmp_path / "kokler"
    roots.mkdir()
    (roots / "kok.pem").write_bytes(world.tsa.root_certificate.public_bytes(Encoding.PEM))
    passed, failures, warnings = standalone_codes(world.layout, roots, no_lzma=case.no_lzma)

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
    # Without lzma an .xz file is a warning of its own, never a mismatch; with lzma, never named.
    xz_days = any(compression == "xz" for compression in case.formats)
    assert ("xz-unsupported" in warnings) == (case.no_lzma and xz_days), warnings


def test_an_extract_from_an_xz_day_without_lzma_fails_as_xz_unsupported(
    world: Any, days: list[Any], tmp_path: Path
) -> None:
    """#136: an extract cannot be compared without lzma, so it must not pass; it fails as
    ``xz-unsupported`` (not as ``extract-mismatch``), and the day's own file only warns."""
    world.reconfigure(seal={"quiet_seconds": 0, "compression": "xz"})
    for day in days:
        world.busy_day(day)
    world.set_now(days[-1] + timedelta(days=1))
    assert {o.status for o in world.sealer().seal_due()} == {"sealed"}
    info = build_package(
        world.layout,
        days[-1:],
        roots=[world.tsa.root_certificate],
        extract=[RawRef(days[-1], "fortigate", 2)],
        out_dir=tmp_path / "out",
    )
    folder = tmp_path / "pkg"
    with zipfile.ZipFile(info.path) as archive:
        archive.extractall(folder)
    saved = sv.lzma
    sv.lzma = None
    try:
        with sv.open_package(folder) as package:
            report = sv.verify_package(package, deep=True)
    finally:
        sv.lzma = saved
    fails = {(f.code, f.where) for f in report.findings if f.level == "fail"}
    warns = {f.code for f in report.findings if f.level == "warn"}
    assert ("xz-unsupported", "alıntı") in fails
    assert not {code for code, _ in fails} & {"extract-mismatch", "file-content-mismatch"}
    assert "xz-unsupported" in warns
    assert report.passed is False
