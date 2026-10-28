"""``system.sample``: one disk sample a day for the Sistem page's forecast (Wave 4 contract 2.5.2).

A daily job after 00:30 (the Wave 4 switch-on registers it in :mod:`arcilogger_worker.service`).
It appends ``{"day": "YYYY-MM-DD", "drives": [{"path", "total", "used"}]}`` for the site day of
the run to ``state/system-history.json`` (:attr:`~arcilogger_core.layout.Layout.system_history`)
= ``{"schema": 1, "days": [...]}``: an entry of the same day is replaced (a retry, a second run
after a restart), the newest :data:`HISTORY_DAYS` days are kept, and the file is written
atomically, so a reader never sees half of it. The drives are those of the R20 disk check
(:func:`arcilogger_worker.alerts.system.drives`): the data drive and, when it is another device,
the system drive, by their label; the byte counts are integers.

The file holds nothing but drive labels and byte counts. A file that cannot be read (damaged, of
another schema) is started again from this sample, which is logged; the forecast then waits for
seven new samples (:mod:`arcilogger_api.system.forecast`).
"""

from __future__ import annotations

import json
import logging
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.timeutil import site_day
from arcilogger_worker.alerts.system import drives

if TYPE_CHECKING:
    from arcilogger_worker.service import JobContext

log = logging.getLogger("arcilogger_worker.system")

HISTORY_SCHEMA: Final = 1
#: Days kept in the history (the forecast uses the newest 30).
HISTORY_DAYS: Final = 120


def _drive_entry(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and isinstance(value.get("path"), str)
        and type(value.get("total")) is int
        and type(value.get("used")) is int
    )


def _day_entry(value: Any) -> bool:
    if not isinstance(value, dict) or not isinstance(value.get("day"), str):
        return False
    drives_ = value.get("drives")
    return isinstance(drives_, list) and all(_drive_entry(drive) for drive in drives_)


def read_history(path: Path) -> list[dict[str, Any]]:
    """The day entries of the history file, oldest first; ``[]`` when there is none or it cannot
    be read (an entry of another shape is left out)."""
    try:
        data = json.loads(path.read_bytes())
    except FileNotFoundError:
        return []
    except (OSError, ValueError) as exc:
        log.warning("%s cannot be read: %s", path, exc)
        return []
    if not isinstance(data, dict) or data.get("schema") != HISTORY_SCHEMA:
        log.warning("%s is not a schema %d history", path, HISTORY_SCHEMA)
        return []
    days = data.get("days")
    if not isinstance(days, list):
        return []
    return sorted((entry for entry in days if _day_entry(entry)), key=lambda entry: entry["day"])


def sample_drives(data_dir: Path) -> list[dict[str, Any]]:
    """``{"path", "total", "used"}`` of the data drive and the system drive (bytes)."""
    sampled: list[dict[str, Any]] = []
    for label, path in drives(data_dir):
        try:
            usage = shutil.disk_usage(path)
        except OSError as exc:
            log.warning("disk usage of %s: %s", path, exc)
            continue
        if usage.total <= 0:
            continue
        sampled.append({"path": label, "total": usage.total, "used": usage.total - usage.free})
    return sampled


def job_system_sample(ctx: JobContext) -> Mapping[str, Any]:
    """Today's (site day) disk sample into ``state/system-history.json``."""
    day = site_day(ctx.now).isoformat()
    sampled = sample_drives(ctx.layout.root)
    path = ctx.layout.system_history
    days = [entry for entry in read_history(path) if entry["day"] != day]
    days.append({"day": day, "drives": sampled})
    days.sort(key=lambda entry: entry["day"])
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path, json.dumps({"schema": HISTORY_SCHEMA, "days": days[-HISTORY_DAYS:]}, indent=1)
    )
    return {"day": day, "drives": len(sampled)}
