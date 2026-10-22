"""Second copy of every sealed day (build specification 8.3).

The day's vault folder is copied to ``replica.target`` (a shared folder on a NAS, ideally with WORM
enabled), then every copied file is hashed again. Files already present with the same hash are
skipped; a file that exists with **different** content is never overwritten — it is reported as a
critical alert, because on a WORM share it means the replica was tampered with or corrupted.

Like ``rsync``, a file is first written under a temporary name and renamed only once its hash
matches: a copy interrupted by a power cut, a network drop or the task's time limit never leaves
a half file under the final name. The temporary files live in ``<target>/.arcilogger-staging/``,
outside the day folders, so a leftover that cannot be deleted (WORM) never looks like an unsealed
file of a day. A destination that is a strict prefix of the source was left by an interrupted copy
of an earlier version: it holds nothing the source lacks, so it is reported (high) and replaced
when the share allows it. Every other file of the day is still copied when one file fails.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from arcilogger_core.alerts import Severity, raise_alert
from arcilogger_core.chain import sealed_days
from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.layout import Layout
from arcilogger_core.seal import sha256_file
from arcilogger_core.timeutil import isoformat_site, utc_now

#: Folder for files being copied, directly under the replica target (outside every day folder).
STAGING: Final = ".arcilogger-staging"
ALERT_RULE: Final = "R20-replica"
CHUNK: Final = 1024 * 1024


@dataclass(frozen=True)
class ReplicaOutcome:
    day: date
    status: str  # "copied" | "up-to-date" | "failed"
    copied: int = 0
    message: str = ""


class _CopyError(OSError):
    """A copy whose hash did not match the source (already removed)."""


def _alert(
    layout: Layout, key: str, severity: Severity, message: str, facts: dict[str, str]
) -> None:
    """One alert per problem: the replica task runs every hour and would repeat it."""
    seen_path = layout.state / "replica" / "alerts-raised.txt"
    try:
        seen = set(seen_path.read_text(encoding="utf-8").splitlines())
    except OSError:
        seen = set()
    if key in seen:
        return
    raise_alert(layout.alerts, ALERT_RULE, severity, message, facts)
    seen_path.parent.mkdir(parents=True, exist_ok=True)
    with seen_path.open("a", encoding="utf-8") as handle:
        handle.write(key + "\n")


def _is_prefix(partial: Path, source: Path) -> bool:
    """Whether ``partial`` is shorter than ``source`` and equals its beginning."""
    size = partial.stat().st_size
    if size >= source.stat().st_size:
        return False
    with partial.open("rb") as left, source.open("rb") as right:
        remaining = size
        while remaining:
            chunk = left.read(min(CHUNK, remaining))
            if not chunk or chunk != right.read(len(chunk)):
                return False
            remaining -= len(chunk)
    return True


def _stage(source: Path, staging: Path, digest: str) -> Path:
    """Copy ``source`` into ``staging`` under a temporary name, flushed and hash-checked."""
    part = staging / f"{source.name}.{os.getpid()}.part"
    try:
        with source.open("rb") as raw, part.open("wb") as out:
            shutil.copyfileobj(raw, out, CHUNK)
            out.flush()
            os.fsync(out.fileno())
        if sha256_file(part) != digest:
            raise _CopyError(f"{source.name}: the copy does not match the source")
    except BaseException:
        _remove(part)
        raise
    return part


def _remove(path: Path) -> None:
    """Best effort: a leftover that cannot be removed (WORM) only wastes space."""
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def _clean_staging(staging: Path) -> None:
    if staging.is_dir():
        for leftover in staging.glob("*.part"):
            _remove(leftover)


def _place(part: Path, destination: Path, *, replace: bool) -> None:
    """Move a checked copy to its final name; never over a file unless ``replace``."""
    if replace:
        part.replace(destination)
        return
    if destination.exists():
        raise FileExistsError(f"{destination.name} appeared while it was being copied")
    part.rename(destination)  # on Windows never overwrites


def replicate_day(layout: Layout, day: date, target: Path) -> ReplicaOutcome:
    source_dir = layout.vault_day(day)
    if not layout.token(day).exists():
        return ReplicaOutcome(day, "failed", message="day is not sealed")
    target_dir = target / source_dir.relative_to(layout.vault)
    staging = target / STAGING / day.isoformat()
    shown = day.strftime("%d.%m.%Y")
    copied = 0
    hashes: dict[str, str] = {}
    problems: list[str] = []
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        staging.mkdir(parents=True, exist_ok=True)
        _clean_staging(staging)
        sources = sorted(p for p in source_dir.iterdir() if p.is_file())
    except OSError as exc:
        _alert(
            layout,
            f"{day.isoformat()}:unreachable:{type(exc).__name__}",
            "high",
            f"Replika kopyalanamadı ({shown}): {exc}",
            {"day": day.isoformat()},
        )
        return ReplicaOutcome(day, "failed", 0, str(exc))
    for source in sources:
        try:
            digest = sha256_file(source)
            hashes[source.name] = digest
            destination = target_dir / source.name
            replace = False
            if destination.exists():
                if sha256_file(destination) == digest:
                    continue
                if not _is_prefix(destination, source):
                    _alert(
                        layout,
                        f"{day.isoformat()}:differs:{source.name}:{sha256_file(destination)}",
                        "critical",
                        f"Replika farklı ({shown} {source.name}); üzerine yazılmadı.",
                        {"day": day.isoformat(), "file": source.name},
                    )
                    problems.append(f"{source.name}: replica differs, not overwritten")
                    continue
                _alert(
                    layout,
                    f"{day.isoformat()}:partial:{source.name}",
                    "high",
                    f"Replikada yarım kalmış kopya ({shown} {source.name}); kaynak dosyanın "
                    "başıyla aynı, tamamlanmış kopyayla değiştiriliyor.",
                    {"day": day.isoformat(), "file": source.name},
                )
                replace = True
            part = _stage(source, staging, digest)
            try:
                _place(part, destination, replace=replace)
            except OSError:
                _remove(part)
                raise
            if sha256_file(destination) != digest:
                problems.append(f"{source.name}: copy verification failed")
                continue
            copied += 1
        except OSError as exc:
            _alert(
                layout,
                f"{day.isoformat()}:copy:{source.name}:{type(exc).__name__}",
                "high",
                f"Replika kopyalanamadı ({shown} {source.name}): {exc}",
                {"day": day.isoformat(), "file": source.name},
            )
            problems.append(f"{source.name}: {exc}")
    if problems:
        return ReplicaOutcome(day, "failed", copied, "; ".join(problems))
    atomic_write_text(
        layout.replica_state(day),
        json.dumps(
            {
                "day": day.isoformat(),
                "status": "ok",
                "target": str(target_dir),
                "verified_at": isoformat_site(utc_now(), "seconds"),
                "files": hashes,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return ReplicaOutcome(day, "copied" if copied else "up-to-date", copied)


def replicate_due(layout: Layout, target: Path) -> list[ReplicaOutcome]:
    """Copy every sealed day that has no successful replica record yet."""
    outcomes = []
    for day in sealed_days(layout):
        if layout.replica_state(day).exists():
            continue
        outcomes.append(replicate_day(layout, day, target))
    return outcomes


def probe(target: Path) -> str:
    """Write, read back and remove a small file in the target's staging folder (the replica
    connection test); returns a short description of what was checked."""
    folder = target / STAGING
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"probe-{os.getpid()}.tmp"
    data = os.urandom(64)
    path.write_bytes(data)
    try:
        if path.read_bytes() != data:
            raise OSError("the probe file read back differs")
    finally:
        _remove(path)
    return f"{folder}: yazma, okuma ve silme çalışıyor"
