"""``GET /system/database``: the Veritabanı page (Wave 5 addendum C, C7).

What database ARCILOGGER uses (PostgreSQL, with TimescaleDB when the upgrade made hypertables),
its version, how big it is and its largest tables (:func:`~.resources.measure_database`, kept 5
minutes like the Sistem page's), how many connections it has, how many days of logs it keeps
(``db.retention_days``; older days are archived and can be loaded again), how fast it grows (the
worker's daily ``database`` sample in ``state/system-history.json``, the same straight line as a
drive's forecast) and the last backup (``state/backup/last.json``).

Like ``GET /system`` it needs ``system.read``, is not audited and carries no data about log
subjects: names of tables, sizes and counts only. Every reading that fails answers ``null`` on its
own (in a savepoint, logged): the page shows what could be read.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from datetime import date, datetime, timedelta
from pathlib import PureWindowsPath
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from arcilogger_api.system.forecast import database_samples, growth
from arcilogger_api.system.models import (
    DatabaseDetails,
    DatabaseInfo,
    DatabaseSample,
    DriveInfo,
    LastBackup,
)
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_db.settings import get_setting
from arcilogger_worker.system_jobs import read_history

log = logging.getLogger("arcilogger_api.system")

#: Days of samples the page draws.
HISTORY_SHOWN: Final = 30


def _read[T](session: Session, what: str, query: Callable[[], T]) -> T | None:
    try:
        with session.begin_nested():
            return query()
    except SQLAlchemyError as exc:
        log.warning("database %s: %s", what, exc)
        return None


def _text(value: Any) -> str | None:
    return str(value) if value is not None else None


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _moment(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        moment = datetime.fromisoformat(value)
    except ValueError:
        return None
    return moment if moment.tzinfo is not None else None


def last_backup(layout: Layout) -> LastBackup | None:
    """The Backup task's last attempt; ``None`` when it never ran or the file cannot be read.
    ``file`` there is below the data folder (``backups/scheduled/…``) or a full Windows path:
    only its name is shown."""
    try:
        data = json.loads((layout.state / "backup" / "last.json").read_bytes())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    ok = data.get("ok") is True
    file = data.get("file")
    return LastBackup(
        finished=_moment(data.get("finished")),
        ok=ok,
        file=PureWindowsPath(file).name if ok and isinstance(file, str) else None,
        bytes=data["size"] if ok and type(data.get("size")) is int else None,
    )


def database_details(
    session: Session,
    *,
    config: Config,
    layout: Layout,
    sizes: DatabaseInfo,
    data_drive: DriveInfo | None,
    now: datetime,
    today: date,
) -> DatabaseDetails:
    def scalar(sql: str) -> Callable[[], Any]:
        return lambda: session.scalar(text(sql))

    history = database_samples(read_history(layout.system_history))
    first_day = today - timedelta(days=HISTORY_SHOWN - 1)
    return DatabaseDetails(
        checked_at=now,
        storage=sizes.storage,
        server_version=_text(_read(session, "version", scalar("SHOW server_version"))),
        timescaledb_version=_text(
            _read(
                session,
                "timescaledb",
                scalar("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"),
            )
        ),
        name=_text(_read(session, "name", scalar("SELECT current_database()"))),
        host=config.database.host,
        port=config.database.port,
        total_bytes=sizes.total_bytes,
        measured_at=sizes.measured_at,
        tables=sizes.tables,
        connections=_int(
            _read(
                session,
                "connections",
                scalar("SELECT count(*) FROM pg_stat_activity WHERE datname = current_database()"),
            )
        ),
        max_connections=_int(_read(session, "limit", scalar("SHOW max_connections"))),
        window_days=_int(
            _read(session, "window", lambda: get_setting(session, "db.retention_days"))
        ),
        growth=growth(history),
        history=[DatabaseSample(day=day, bytes=size) for day, size in history if day >= first_day],
        data_drive=data_drive,
        last_backup=last_backup(layout),
    )
