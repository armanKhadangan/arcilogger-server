"""The log format samples are treated like evidence: their bytes must never change."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

FORMAT = Path(__file__).parent / "fixtures" / "format"


def _pinned() -> list[tuple[str, str]]:
    lines = (FORMAT / "SHA256SUMS").read_text(encoding="ascii").splitlines()
    return [(name.strip(), digest) for digest, name in (line.split("  ", 1) for line in lines)]


@pytest.mark.parametrize(("name", "digest"), _pinned())
def test_fixture_bytes_unchanged(name: str, digest: str) -> None:
    data = (FORMAT / name).read_bytes()
    assert b"\r" not in data, f"{name}: CR found — line endings were converted"
    assert hashlib.sha256(data).hexdigest() == digest


def test_every_fixture_is_pinned() -> None:
    pinned = {name for name, _ in _pinned()}
    present = {p.name for p in FORMAT.iterdir() if p.suffix in {".log", ".txt"}}
    assert present == pinned
