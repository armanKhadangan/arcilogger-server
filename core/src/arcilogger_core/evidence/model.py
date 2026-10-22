"""Data shared by the evidence package builder, the CLI and (later) the API."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

#: Spool source names: configured names (``fortigate``) and per-sender names of network
#: sources and unknown senders (``ap-172.20.10.23``, ``unknown-10.9.9.9``).
SOURCE_NAME = re.compile(r"^[a-z0-9][a-z0-9.-]{0,80}$")


class EvidenceError(RuntimeError):
    """The package cannot be built: a day is not sealed, a vault file changed, bad input."""


@dataclass(frozen=True, order=True)
class RawRef:
    """One raw line of a sealed day: ``line`` is 1-based in the uncompressed sealed file.

    The sealed file ``vault/YYYY/MM/DD/<source>.log.gz`` is the day's spool file compressed
    unchanged, so the line number is the same as in ``spool/<source>/<day>.log``.
    """

    day: date
    source: str
    line: int

    def __post_init__(self) -> None:
        if not SOURCE_NAME.match(self.source):
            raise EvidenceError(f"invalid source name {self.source!r}")
        if self.line < 1:
            raise EvidenceError(f"line numbers start at 1, got {self.line}")

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> RawRef:
        try:
            return cls(
                day=date.fromisoformat(str(data["day"])),
                source=str(data["source"]),
                line=int(data["line"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise EvidenceError(f"invalid raw reference {dict(data)!r}: {exc}") from exc

    def as_json(self) -> dict[str, Any]:
        return {"day": self.day.isoformat(), "source": self.source, "line": self.line}


def load_refs(path: Path) -> list[RawRef]:
    """Raw references from a JSON file: ``[{"day": "2026-10-12", "source": "fortigate",
    "line": 17}, …]`` (or the same list under the key ``"refs"``)."""
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        raise EvidenceError(f"cannot read {path}: {exc}") from exc
    if isinstance(data, dict):
        data = data.get("refs")
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise EvidenceError(f"{path}: expected a list of {{day, source, line}} objects")
    return [RawRef.from_json(item) for item in data]


@dataclass(frozen=True)
class PackageInfo:
    """What was built. ``password`` is returned once to show to the user; never store it."""

    package_id: str
    path: Path
    sha256: str
    size: int
    file_count: int
    password_protected: bool
    days: tuple[date, ...]
    sources: tuple[str, ...] | None
    extract_lines: int
    index_sha256: str
    created_at: datetime
    created_by: str
    lookup_id: str | None
    #: True only if every day's manifest claims the Kamu SM production server **and** its
    #: verified token chains to a root listed in ``tsa.legal_root_fingerprints``.
    legal_grade: bool
    #: SHA-256 of the package's verify.py: to be shown and recorded apart from the package, so
    #: a recipient can confirm the verifier through a channel the package cannot change.
    verifier_sha256: str
    #: Every day's manifest claims the Kamu SM production server (its own statement; the token
    #: verified, but ``legal_grade`` also needs the root listed). False: at least one day was
    #: stamped by a test or mock provider.
    legal_grade_claimed: bool = False
    password: str | None = field(default=None, repr=False, compare=False)

    def as_json(self) -> dict[str, Any]:
        """For the audit log and the API — without the password."""
        return {
            "package_id": self.package_id,
            "file": self.path.name,
            "sha256": self.sha256,
            "size": self.size,
            "file_count": self.file_count,
            "password_protected": self.password_protected,
            "days": [day.isoformat() for day in self.days],
            "sources": list(self.sources) if self.sources is not None else None,
            "extract_lines": self.extract_lines,
            "index_sha256": self.index_sha256,
            "created_at": self.created_at.isoformat(),
            "created_by": self.created_by,
            "lookup_id": self.lookup_id,
            "legal_grade": self.legal_grade,
            "legal_grade_claimed": self.legal_grade_claimed,
            "verifier_sha256": self.verifier_sha256,
        }
