"""A chain of gzip and xz days in both verifiers (Wave 5 W5-H; cases in ``sealed_format_cases``).

Both verifiers must agree on every case: an intact chain of mixed formats verifies, and a damaged
file of either format is found by the hash of the stored file or, when that matches (re-stamped),
by decompressing it, never by an exception and never as a shorter file.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from cryptography.hazmat.primitives.serialization import Encoding

from arcilogger_core import sealedfile

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
