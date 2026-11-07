"""A second copy of the database backups on a share of the replica's NAS (Wave 5 addendum C,
C6).

With ``[backup] mirror_target`` set (``\\\\NAS-01\\arcilogger-yedek``: a share or folder of its
own on the replica's host, never inside the replica's often write-once folder), the Replica
task, after the sealed days, copies every scheduled dump of ``[backup] dir``
(``arcilogger-YYYYmmdd-HHMMSS.dump``) and its ``.json`` sidecar there, connected with
``replica.user`` and its stored password (:mod:`arcilogger_core.smbshare`). The password is
never offered to another host, and the strict outbound rule of the replica covers the copy.

A copy is written under ``.arcilogger-staging\\<name>.partial`` and read back; it gets the
dump's name only when its SHA-256 equals the sidecar's (the Backup task's record), so a cut
connection never leaves half a dump under the final name. A name already there is never
overwritten: the same size counts as copied, another size is reported as a conflict. A dump whose
content no longer matches its sidecar is not copied (it may be damaged) and is reported.

The copy keeps what the backup folder keeps: once every dump of the folder is on the share, a
scheduled dump (and its sidecar) that the Backup task's retention has pruned is deleted from the
share too, so personal data is not kept longer there than on the server. Nothing is deleted
while a dump could not be copied or the folder could not be read, and nothing but
ARCILOGGER's scheduled dump names. A deletion the share refuses is counted, not an error.

Every run writes ``state/backup/mirror.json`` (shown on Ayarlar → Yedekleme): when it finished,
whether it succeeded, how many dumps it copied, how many were already there, how many old copies
it deleted (or could not delete), and the names it could not copy with why. The text never
contains the password or the share's address.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from arcilogger_core.config import Config
from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import isoformat_site

STAGING: Final = ".arcilogger-staging"
STATE_SCHEMA: Final = 1
#: A scheduled dump of the Backup task (``arcilogger_db.backup.BACKUP_NAME``).
DUMP_NAME: Final = re.compile(r"^arcilogger-\d{8}-\d{6}\.dump$")
SIDECAR_SUFFIX: Final = ".json"
CHUNK: Final = 1024 * 1024


def backup_folder(config: Config) -> Path:
    """``[backup] dir``: a relative path is below ``data_dir``."""
    folder = config.backup.dir
    return folder if folder.is_absolute() else config.data_dir / folder


def mirror_state_path(layout: Layout) -> Path:
    return layout.state / "backup" / "mirror.json"


@dataclass
class MirrorOutcome:
    copied: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)
    #: Old copies deleted because the backup folder no longer keeps them, and those the share
    #: refused to delete.
    pruned: list[str] = field(default_factory=list)
    prune_refused: list[str] = field(default_factory=list)
    #: ``{name: why}`` of the dumps that were not copied.
    failed: dict[str, str] = field(default_factory=dict)
    #: Why nothing could be done (the backup folder cannot be read); ``None`` otherwise.
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.failed


def _recorded_sha256(dump: Path) -> str | None:
    try:
        data = json.loads(dump.with_name(dump.name + SIDECAR_SUFFIX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("sha256") if isinstance(data, dict) else None
    return value if isinstance(value, str) and len(value) == 64 else None


def _copy(source: Path, partial: Path) -> str:
    """Copy ``source`` to ``partial``, flushed; the SHA-256 of what was read from the source."""
    digest = hashlib.sha256()
    with source.open("rb") as reader, partial.open("wb") as writer:
        while chunk := reader.read(CHUNK):
            writer.write(chunk)
            digest.update(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    return digest.hexdigest()


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def _why(exc: OSError) -> str:
    """The reason without a path (a share address never reaches the state file)."""
    return exc.strerror or type(exc).__name__


def _mirror_one(dump: Path, target: Path, outcome: MirrorOutcome) -> None:
    name = dump.name
    destination = target / name
    expected = _recorded_sha256(dump)
    if expected is None:
        outcome.failed[name] = "no recorded SHA-256 (sidecar missing or unreadable)"
        return
    if destination.exists():
        if destination.stat().st_size == dump.stat().st_size:
            outcome.present.append(name)
        else:
            outcome.failed[name] = "a different file of this name is on the share"
        return
    staging = target / STAGING
    staging.mkdir(exist_ok=True)
    partial = staging / (name + ".partial")
    source = _copy(dump, partial)
    if source != expected:
        partial.unlink(missing_ok=True)
        outcome.failed[name] = "the dump does not match its recorded SHA-256 (it may be damaged)"
        return
    if _hash(partial) != expected:
        partial.unlink(missing_ok=True)
        outcome.failed[name] = "the copy read back from the share differs"
        return
    sidecar = dump.with_name(name + SIDECAR_SUFFIX)
    (target / sidecar.name).write_bytes(sidecar.read_bytes())
    partial.replace(destination)
    outcome.copied.append(name)


def _prune(kept: set[str], target: Path, outcome: MirrorOutcome) -> None:
    """Delete the scheduled dumps (and sidecars) on the share that the folder no longer keeps."""
    for path in sorted(target.iterdir()):
        if not DUMP_NAME.fullmatch(path.name) or path.name in kept or not path.is_file():
            continue
        try:
            path.unlink()
            path.with_name(path.name + SIDECAR_SUFFIX).unlink(missing_ok=True)
        except OSError:
            outcome.prune_refused.append(path.name)
        else:
            outcome.pruned.append(path.name)


def mirror_backups(source: Path, target: Path) -> MirrorOutcome:
    """Copy the scheduled dumps of ``source`` into the folder ``target`` (the mirror target)
    and delete there the old ones ``source`` no longer keeps."""
    outcome = MirrorOutcome()
    try:
        dumps = sorted(
            path
            for path in source.iterdir()
            if DUMP_NAME.fullmatch(path.name) and path.is_file() and not path.is_symlink()
        )
    except OSError as exc:
        outcome.error = f"the backup folder cannot be read: {_why(exc)}"
        return outcome
    try:
        target.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        outcome.error = f"the folder on the share cannot be made: {_why(exc)}"
        return outcome
    for dump in dumps:
        try:
            _mirror_one(dump, target, outcome)
        except OSError as exc:
            outcome.failed[dump.name] = _why(exc)
    if dumps and not outcome.failed:
        try:
            _prune({dump.name for dump in dumps}, target, outcome)
        except OSError as exc:
            outcome.error = f"the folder on the share cannot be read: {_why(exc)}"
    return outcome


def write_state(layout: Layout, outcome: MirrorOutcome, *, finished: datetime) -> None:
    """``state/backup/mirror.json``, written atomically."""
    state = {
        "schema": STATE_SCHEMA,
        "finished": isoformat_site(finished, "seconds"),
        "ok": outcome.ok,
        "copied": len(outcome.copied),
        "present": len(outcome.present),
        "pruned": len(outcome.pruned),
        "prune_refused": len(outcome.prune_refused),
        "failed": dict(sorted(outcome.failed.items())),
        "error": outcome.error,
    }
    path = mirror_state_path(layout)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(state, ensure_ascii=False) + "\n")


def summary(outcome: MirrorOutcome) -> str:
    """One Turkish line for the console and the alert."""
    if outcome.error is not None:
        return f"Yedek kopyası yapılamadı: {outcome.error}"
    text = f"Yedek kopyası: {len(outcome.copied)} yeni, {len(outcome.present)} zaten var"
    if outcome.pruned:
        text += f", {len(outcome.pruned)} eski kopya silindi"
    if outcome.prune_refused:
        text += f", {len(outcome.prune_refused)} eski kopya silinemedi"
    if outcome.failed:
        text += f", {len(outcome.failed)} kopyalanamadı ({', '.join(sorted(outcome.failed))})"
    return text
