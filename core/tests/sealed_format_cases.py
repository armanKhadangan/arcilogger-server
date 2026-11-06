"""Sealed-format cases shared by the core verifier and the standalone verifier (Wave 5 W5-H).

Each case is a chain of three sealed days, each day in its own format (the owner may switch
``seal.compression``; old days stay as they are), and what happens to the last day's FortiGate
file afterwards. ``test_sealed_formats.py`` seals the chain with the product's sealer, then runs
:func:`core_errors` (``chain.verify_range``, deep) and :func:`standalone_codes` (the standalone
verifier on the vault folder, deep, read as it is) and asserts the case's outcome for both:
nothing found for an intact chain, the named error and failure code for a damaged file.
A case run ``without_lzma`` takes the standalone verifier's ``lzma`` away, as on a Python built
without it: the files are intact, and only the standalone verifier cannot read the xz days (#136).

"Re-stamped" damage is a file that was already damaged when its manifest was written (or a
careful forger who rewrote the manifest's ``sha256`` and stamped it again): its stored hash
matches, so only decompressing it finds the damage.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from cryptography import x509

from arcilogger_core import sealedfile
from arcilogger_core.canonical import canonical_json
from arcilogger_core.chain import verify_range
from arcilogger_core.evidence.standalone import verify as sv
from arcilogger_core.immutability import is_locked, unlock
from arcilogger_core.layout import Layout
from arcilogger_core.sealedfile import Compression
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider

Damage = Literal["none", "flipped", "flipped-restamped", "truncated-restamped"]


@dataclass(frozen=True)
class SealedFormatCase:
    name: str
    formats: tuple[Compression, Compression, Compression]
    damage: Damage = "none"
    #: Part of the core verifier's error for the last day; None: every day verifies.
    core: str | None = None
    #: The standalone verifier's failure code for the last day; None: the chain passes.
    standalone: str | None = None
    #: Run the standalone verifier as on a Python without the lzma module.
    without_lzma: bool = False


CASES = (
    SealedFormatCase("gzip only (days sealed before W5-H)", ("gzip", "gzip", "gzip")),
    SealedFormatCase("gzip, then xz", ("gzip", "gzip", "xz")),
    SealedFormatCase("xz, back to gzip, xz again", ("xz", "gzip", "xz")),
    SealedFormatCase("xz only", ("xz", "xz", "xz")),
    SealedFormatCase(
        "a changed xz file",
        ("gzip", "gzip", "xz"),
        "flipped",
        core="SHA-256 mismatch",
        standalone="file-hash-mismatch",
    ),
    SealedFormatCase(
        "corrupt xz stream data, re-stamped",
        ("gzip", "gzip", "xz"),
        "flipped-restamped",
        core="cannot be decompressed",
        standalone="file-content-mismatch",
    ),
    SealedFormatCase(
        "a truncated xz file, re-stamped",
        ("gzip", "xz", "xz"),
        "truncated-restamped",
        core="cannot be decompressed",
        standalone="file-content-mismatch",
    ),
    SealedFormatCase(
        "corrupt gzip data, re-stamped",
        ("xz", "xz", "gzip"),
        "flipped-restamped",
        core="cannot be decompressed",
        standalone="file-content-mismatch",
    ),
    SealedFormatCase(
        "intact xz days, read by a Python without lzma",
        ("gzip", "xz", "xz"),
        standalone="xz-unsupported",
        without_lzma=True,
    ),
)


def damage(layout: Layout, day: date, tsa: MockTsa, how: Damage) -> Path | None:
    """Damage the FortiGate file of ``day`` (the chain's last day) as ``how`` says."""
    if how == "none":
        return None
    path = sealedfile.find_log(layout, day, "fortigate")
    assert path is not None
    for locked in (path, layout.manifest(day), layout.token(day)):
        if locked.exists() and is_locked(locked):
            unlock(locked)
    data = bytearray(path.read_bytes())
    if how == "truncated-restamped":
        del data[len(data) // 2 :]
    else:
        data[len(data) // 2] ^= 0x55  # inside the compressed data, past every header
    path.write_bytes(bytes(data))
    if how.endswith("-restamped"):
        manifest = json.loads(layout.manifest(day).read_bytes())
        for entry in manifest["files"]:
            if entry["path"].endswith(path.name):
                entry["sha256"] = hashlib.sha256(data).hexdigest()
                entry["bytes"] = len(data)
        layout.manifest(day).write_bytes(canonical_json(manifest))
        MockTsaProvider(tsa).stamp(layout.manifest(day))
    return path


def core_errors(layout: Layout, days: list[date], roots: list[x509.Certificate]) -> list[str]:
    checks = verify_range(layout, days, roots, deep=True)
    assert [check.errors for check in checks[:-1]] == [[]] * (len(days) - 1)
    return checks[-1].errors


def standalone_codes(layout: Layout, roots_dir: Path) -> tuple[bool, set[str]]:
    with sv.open_package(layout.vault) as package:
        report = sv.verify_package(package, roots_dir=roots_dir, deep=True)
    return report.passed, {finding.code for finding in report.findings if finding.level == "fail"}
