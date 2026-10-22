"""What the sealer has finished: sealed days and the files their manifests describe.

The ingest service never imports the sealer (it needs the TSA configuration); it reads the two
files the sealer writes atomically:

* ``state/seal/<day>.json`` — ``{"status": "sealed", …}`` once a day is sealed. The sealer
  deletes the day's spool files *before* it records ``sealed`` (and finishes the deletion on the
  next run after a crash), so "spool file missing" alone does not mean "sealed".
* ``vault/<Y>/<M>/<D>/manifest-<day>.json`` — per source the vault ``path`` (relative to the
  vault), ``bytes_uncompressed`` and ``lines``. A (source, day) is fully ingested when the
  committed offset equals ``bytes_uncompressed``.

A manifest is immutable once its day is sealed, so it is cached; an unsealed day is looked up
again after :data:`RECHECK_SECONDS`.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Final

from arcilogger_core.layout import Layout

RECHECK_SECONDS: Final = 5.0


@dataclass(frozen=True, slots=True)
class SealedFile:
    """One source's file of a sealed day (from the manifest)."""

    source: str
    day: date
    path: Path
    bytes_uncompressed: int
    lines: int


class SealIndex:
    """Cached view of sealed days (``monotonic`` is injectable for tests)."""

    def __init__(self, layout: Layout, *, monotonic: Callable[[], float] = time.monotonic) -> None:
        self._layout = layout
        self._monotonic = monotonic
        self._sealed: dict[date, dict[str, SealedFile]] = {}
        self._checked: dict[date, float] = {}

    def is_sealed(self, day: date) -> bool:
        return self.files(day) is not None

    def files(self, day: date) -> dict[str, SealedFile] | None:
        """Files of a sealed day by source, ``None`` while the day is not sealed."""
        cached = self._sealed.get(day)
        if cached is not None:
            return cached
        now = self._monotonic()
        last = self._checked.get(day)
        if last is not None and now - last < RECHECK_SECONDS:
            return None
        self._checked[day] = now
        found = self._load(day)
        if found is not None:
            self._sealed[day] = found
            self._checked.pop(day, None)
        return found

    def entry(self, day: date, source: str) -> SealedFile | None:
        files = self.files(day)
        return None if files is None else files.get(source)

    def sealed_days(self, since: date) -> list[date]:
        """Days on or after ``since`` whose seal state says ``sealed``, oldest first."""
        folder = self._layout.state / "seal"
        if not folder.is_dir():
            return []
        days = []
        for state_file in folder.glob("*.json"):
            try:
                day = date.fromisoformat(state_file.stem)
            except ValueError:
                continue
            if day >= since and self.is_sealed(day):
                days.append(day)
        return sorted(days)

    def _load(self, day: date) -> dict[str, SealedFile] | None:
        state = _read_json(self._layout.seal_state(day))
        if state is None or state.get("status") != "sealed":
            return None
        manifest = _read_json(self._layout.manifest(day))
        if manifest is None:
            return None
        files: dict[str, SealedFile] = {}
        for entry in manifest.get("files", []):
            files[str(entry["source"])] = SealedFile(
                source=str(entry["source"]),
                day=day,
                path=self._layout.vault / str(entry["path"]),
                bytes_uncompressed=int(entry["bytes_uncompressed"]),
                lines=int(entry["lines"]),
            )
        return files


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):  # missing, being replaced (Windows) or not JSON: look again later
        return None
    return data if isinstance(data, dict) else None
