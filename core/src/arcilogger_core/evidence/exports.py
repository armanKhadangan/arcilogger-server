"""Housekeeping of ``exports/``: evidence packages are kept 30 days by default.

A package is a copy of sealed data handed to someone; the evidence itself stays in the vault.
Only files this module's builder creates (``ARCILOGGER-kanit-*.zip``) are ever deleted here,
and unfinished builds (``*.zip.part``) after one day.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from arcilogger_core.evidence.builder import PACKAGE_PREFIX
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now

DEFAULT_KEEP_DAYS = 30
PART_KEEP = timedelta(days=1)


def purge_expired(
    layout: Layout, keep_days: int = DEFAULT_KEEP_DAYS, *, now: datetime | None = None
) -> list[Path]:
    """Delete packages older than ``keep_days`` (by modification time); return what was deleted."""
    if keep_days < 0:
        raise ValueError("keep_days cannot be negative")
    folder = layout.exports
    if not folder.is_dir():
        return []
    current = (now or utc_now()).timestamp()
    deleted = []
    for path in sorted(folder.glob(f"{PACKAGE_PREFIX}*")):
        if not path.is_file():
            continue
        if path.name.endswith(".zip"):
            limit = timedelta(days=keep_days)
        elif path.name.endswith(".zip.part"):
            limit = PART_KEEP
        else:
            continue
        if current - path.stat().st_mtime > limit.total_seconds():
            path.unlink()
            deleted.append(path)
    return deleted
