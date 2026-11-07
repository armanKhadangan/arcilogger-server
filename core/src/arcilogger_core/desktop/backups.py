"""The "Yedekler / Backups" list of the status window (Wave 5 addendum C, C3).

The database dumps live in folders only administrators may read: Explorer, which runs without
the elevated token, would offer to change their permissions ("Continue"), and a changed
permission is an ``acl_drift`` alert. So the window, which already runs elevated, lists the dumps
itself and saves a copy where the administrator chooses (a USB disk, a share): the copy is
written under a temporary name, hashed again and only then given the dump's name. Nothing in
the backup folders is changed.

Two folders: the scheduled dumps of the Backup task (``[backup] dir``, ``arcilogger-*.dump`` with
a ``.json`` sidecar holding the SHA-256) and the installer's dumps before an upgrade
(``backups\\db``).
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from arcilogger_core.timeutil import to_site

SIDECAR_SUFFIX: Final = ".json"
PARTIAL_SUFFIX: Final = ".partial"
CHUNK: Final = 1024 * 1024


@dataclass(frozen=True)
class Folder:
    path: Path
    title_tr: str
    title_en: str


@dataclass(frozen=True)
class Dump:
    path: Path
    folder: Folder
    modified: datetime
    size: int
    #: The SHA-256 the sidecar records (scheduled dumps), ``None`` without one.
    sha256: str | None


@dataclass(frozen=True)
class Copied:
    path: Path
    sha256: str
    #: True: the copy's hash equals the sidecar's; None: no sidecar, compared with the source.
    recorded: bool | None


class CopyError(Exception):
    """A copy that was not made; the message is shown in the window (Turkish / English)."""


def folders(data_root: Path | None, backup_dir: Path | None) -> list[Folder]:
    found = []
    if backup_dir is not None:
        found.append(Folder(backup_dir, "Zamanlanmış yedekler", "Scheduled backups"))
    if data_root is not None:
        found.append(Folder(data_root / "backups" / "db", "Yükseltme öncesi", "Before an upgrade"))
    return found


def _sidecar_sha256(dump: Path) -> str | None:
    try:
        data = json.loads(dump.with_name(dump.name + SIDECAR_SUFFIX).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    value = data.get("sha256") if isinstance(data, dict) else None
    return value if isinstance(value, str) and len(value) == 64 else None


def list_dumps(places: Iterable[Folder]) -> list[Dump]:
    """Every ``*.dump`` of the folders (not their sub-folders), newest first. A folder that
    cannot be read is skipped (:func:`unreadable` names it)."""
    dumps = []
    for folder in places:
        try:
            entries = list(folder.path.iterdir())
        except OSError:
            continue
        for path in entries:
            if path.suffix != ".dump" or path.is_symlink() or not path.is_file():
                continue
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            dumps.append(Dump(path, folder, modified, stat.st_size, _sidecar_sha256(path)))
    return sorted(dumps, key=lambda dump: dump.modified, reverse=True)


def unreadable(places: Iterable[Folder]) -> list[Folder]:
    """The folders that exist but cannot be read (no administrator rights)."""
    missing = []
    for folder in places:
        try:
            if folder.path.exists():
                next(iter(folder.path.iterdir()), None)
        except OSError:
            missing.append(folder)
    return missing


def size_text(size: int) -> str:
    """ "1,2 GB" (Turkish decimal comma, binary units as Windows Explorer counts them)."""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB"):
        if value < 1024 or unit == "GB":
            text = f"{value:.0f}" if unit == "B" else f"{value:.1f}"
            return f"{text.replace('.', ',')} {unit}"
        value /= 1024
    raise AssertionError("unreachable")


def time_text(moment: datetime) -> str:
    return f"{to_site(moment):%d.%m.%Y %H:%M}"


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def save_copy(
    dump: Dump, destination: Path, progress: Callable[[int], None] | None = None
) -> Copied:
    """Copy ``dump`` (and its sidecar) into the folder ``destination``.

    Refused when a file of that name is already there (never overwritten). The copy is written as
    ``<name>.partial``, flushed to disk, hashed from the disk and renamed only when its hash
    equals the sidecar's (or, without a sidecar, the source's). ``progress`` gets the bytes copied.
    """
    if not destination.is_dir():
        raise CopyError(f"Klasör yok / No such folder: {destination}")
    target = destination / dump.path.name
    partial = destination / (dump.path.name + PARTIAL_SUFFIX)
    if target.exists():
        raise CopyError(f"Bu adla bir dosya zaten var / A file of that name exists: {target}")
    expected = dump.sha256
    source = hashlib.sha256()
    copied = 0
    try:
        with dump.path.open("rb") as reader, partial.open("xb") as writer:
            while chunk := reader.read(CHUNK):
                writer.write(chunk)
                source.update(chunk)
                copied += len(chunk)
                if progress is not None:
                    progress(copied)
            writer.flush()
            os.fsync(writer.fileno())
        written = _hash(partial)
    except FileExistsError as exc:
        raise CopyError(
            f"Yarım kalmış bir kopya var; silip yeniden deneyin / "
            f"A partial copy is there; delete it and try again: {partial}"
        ) from exc
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise CopyError(f"Kopyalanamadı / Not copied: {exc}") from exc
    if written != (expected or source.hexdigest()):
        partial.unlink(missing_ok=True)
        if expected is not None and source.hexdigest() != expected:
            raise CopyError(
                "Yedek dosyası kaydındaki özetle uyuşmuyor; yedek bozulmuş olabilir / "
                "The dump does not match its recorded hash; it may be damaged"
            )
        raise CopyError("Kopya kaynakla aynı değil / The copy differs from the source")
    partial.replace(target)
    sidecar = dump.path.with_name(dump.path.name + SIDECAR_SUFFIX)
    if sidecar.is_file():
        (destination / sidecar.name).write_bytes(sidecar.read_bytes())
    return Copied(target, written, True if expected is not None else None)


def describe(dump: Dump) -> Sequence[str]:
    """The list's columns: name, folder, time, size."""
    return (dump.path.name, dump.folder.title_tr, time_text(dump.modified), size_text(dump.size))
