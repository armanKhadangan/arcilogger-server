"""``GET /api/v1/system``, ``GET /api/v1/system/disk`` (Wave 4 contract 2.2, 2.5) and
``GET /api/v1/system/database`` (the Veritabanı page, Wave 5 addendum C, C7).

Both need ``system.read`` (administrators, owner question Q2), belong to 5651 Temel (no licence
module, so no licence state blocks them) and are not audited: they read the server's own state
and carry no data about log subjects (contract 0.1 item 4). The web app refreshes them every
30 seconds in the background. The database sizes are measured at most every 5 minutes
(:data:`~arcilogger_api.system.resources.DATABASE_TTL`); everything else per request.
``/system/disk`` is the drives alone, for the Panel's disk card; ``/system/database`` shares
the measured sizes with ``/system``.

This module is not under ``routers/``, so the application does not mount it yet: the Wave 4
switch-on adds ``routers/system.py``, which includes :data:`router`. Its tests mount it
themselves.
"""

from __future__ import annotations

import threading
from typing import Annotated, Final

from fastapi import APIRouter, Depends, FastAPI, Request

from arcilogger_api.deps import ConfigDep, DbSession, LayoutDep, TtlCache
from arcilogger_api.security import CurrentUser, require
from arcilogger_api.system.database import database_details
from arcilogger_api.system.models import (
    DatabaseDetails,
    DatabaseInfo,
    SystemDisk,
    SystemSummary,
)
from arcilogger_api.system.resources import (
    DATABASE_TTL,
    collector_info,
    drive_infos,
    host_info,
    ingest_info,
    measure_database,
    version_info,
    warning_percent,
)
from arcilogger_core.timeutil import site_day, to_site, utc_now

router = APIRouter(prefix="/system", tags=["system"])

ReaderDep = Annotated[CurrentUser, Depends(require("system.read"))]

_CACHE_LOCK: Final = threading.Lock()


def database_cache(app: FastAPI) -> TtlCache[DatabaseInfo]:
    """The measured database sizes of this application (one per API process)."""
    with _CACHE_LOCK:
        cache: TtlCache[DatabaseInfo] | None = getattr(app.state, "system_database", None)
        if cache is None:
            cache = TtlCache[DatabaseInfo](ttl=DATABASE_TTL)
            app.state.system_database = cache
        return cache


@router.get("")
def system_summary(
    _: ReaderDep, request: Request, db: DbSession, layout: LayoutDep, config: ConfigDep
) -> SystemSummary:
    """Host, drives with their forecast, database, collector, ingest and versions."""
    now = to_site(utc_now())
    warning = warning_percent(db)
    database = database_cache(request.app).get_or_compute(
        lambda: measure_database(db, now), keep=lambda info: info.total_bytes is not None
    )
    return SystemSummary(
        checked_at=now,
        host=host_info(),
        drives=drive_infos(layout, config.data_dir, warning=warning, today=site_day(now)),
        database=database,
        collector=collector_info(layout, config, now),
        ingest=ingest_info(layout, config, now),
        version=version_info(),
    )


@router.get("/disk")
def system_disk(_: ReaderDep, db: DbSession, layout: LayoutDep, config: ConfigDep) -> SystemDisk:
    """The drives with their forecast (the Panel's disk card)."""
    now = to_site(utc_now())
    return SystemDisk(
        checked_at=now,
        drives=drive_infos(
            layout, config.data_dir, warning=warning_percent(db), today=site_day(now)
        ),
    )


@router.get("/database")
def system_database(
    _: ReaderDep, request: Request, db: DbSession, layout: LayoutDep, config: ConfigDep
) -> DatabaseDetails:
    """The database: engine and version, size and largest tables, connections, the days it
    keeps, its daily growth and the last backup."""
    now = to_site(utc_now())
    sizes = database_cache(request.app).get_or_compute(
        lambda: measure_database(db, now), keep=lambda info: info.total_bytes is not None
    )
    drives = drive_infos(layout, config.data_dir, warning=warning_percent(db), today=site_day(now))
    return database_details(
        db,
        config=config,
        layout=layout,
        sizes=sizes,
        data_drive=next((drive for drive in drives if drive.role == "data"), None),
        now=now,
        today=site_day(now),
    )
