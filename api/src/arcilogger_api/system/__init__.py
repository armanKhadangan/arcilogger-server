"""Sistem: the server's resources and the disk forecast (Wave 4 contract 2.5, package W4-B).

* :mod:`~arcilogger_api.system.models` — the JSON (mirrored by ``web/src/lib/system-types.ts``);
* :mod:`~arcilogger_api.system.resources` — host, drives, database, collector, ingest, versions;
* :mod:`~arcilogger_api.system.forecast` — when a drive reaches its warning mark and is full;
* :mod:`~arcilogger_api.system.routes` — ``GET /system`` and ``GET /system/disk`` (not mounted
  until the Wave 4 switch-on adds ``routers/system.py``).
"""
