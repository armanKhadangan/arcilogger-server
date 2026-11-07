"""``GET /system/backups``: the database dumps and their copy on the NAS (Wave 5 addendum C,
C5-C6), for Ayarlar → Yedekleme.

The services cannot read the backup folder (the dumps hold password hashes and personal data):
the Backup task writes the folder's list to ``state/backup/list.json`` after every successful run
and the Replica task the copy's result to ``state/backup/mirror.json``; this module reads both.
A file that is missing or cannot be read answers an empty list or ``null``. Names, times, sizes
and hashes only: never a dump's content.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from arcilogger_api.system.models import BackupDump, BackupMirrorState, BackupsInfo
from arcilogger_core.backupmirror import backup_folder, mirror_state_path
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout


def _read(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def _count(value: Any) -> int:
    return value if type(value) is int and value >= 0 else 0


def _dumps(listed: dict[str, Any] | None) -> list[BackupDump]:
    dumps = []
    for item in (listed or {}).get("dumps") or []:
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            continue
        sha256 = item.get("sha256")
        dumps.append(
            BackupDump(
                name=item["name"],
                taken=_moment(item.get("taken")),
                bytes=_count(item.get("size")),
                sha256=sha256 if isinstance(sha256, str) else None,
            )
        )
    return dumps


def _mirror(state: dict[str, Any] | None) -> BackupMirrorState | None:
    if state is None:
        return None
    failed = state.get("failed")
    error = state.get("error")
    return BackupMirrorState(
        finished=_moment(state.get("finished")),
        ok=state.get("ok") is True,
        copied=_count(state.get("copied")),
        present=_count(state.get("present")),
        pruned=_count(state.get("pruned")),
        prune_refused=_count(state.get("prune_refused")),
        failed={
            str(name): str(why)
            for name, why in (failed.items() if isinstance(failed, dict) else ())
        },
        error=error if isinstance(error, str) else None,
    )


def backups_info(config: Config, layout: Layout, now: datetime) -> BackupsInfo:
    listed = _read(layout.state / "backup" / "list.json")
    mirror = config.backup.mirror_text
    return BackupsInfo(
        checked_at=now,
        enabled=config.backup.enabled,
        folder=str(backup_folder(config)),
        list_updated=_moment((listed or {}).get("updated")),
        dumps=_dumps(listed),
        mirror_target=mirror,
        mirror=_mirror(_read(mirror_state_path(layout))) if mirror is not None else None,
    )
