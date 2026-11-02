"""``state/ingest-status.json``: what the ingest service is doing, for the dashboard/System page.

Written atomically (:func:`arcilogger_core.fsutil.atomic_write_text`) every few seconds and when
the service stops, so a reader never sees half a file. Layout::

    {
      "version": "0.1.0", "pid": 4242, "state": "running",   # starting | running |
                                                            # database_unavailable | stopping |
                                                            # stopped | failed (crashed; the
                                                            # wrapper restarts the service)
      "started_at": "…+03:00", "updated_at": "…+03:00",
      "database": {"ok": true, "last_error": null},
      "parse_workers": {"configured": 2, "active": 2,   # active 0: parsing in the service
                        "restarts": 0, "last_error": null},  # process until the pool is
                                                              # replaced (pool.Preparer)
      "totals": {"lines": 123456, "rows": 150000, "parse_failures": 12, "batches": 40,
                 "lines_per_second": 9876.5},
      "sources": {
        "fortigate": {
          "day": "2026-10-12",              # newest day not finished yet
          "byte_offset": 1048576, "line_number": 1311,   # committed position in that day
          "read_from": "spool",             # or "vault" (spool file already deleted)
          "lag_bytes": 0,                   # bytes not loaded yet, all unfinished days
          "lag_seconds": 0.0,               # now - receive time of the last loaded line
                                            # (null when unknown), 0 when caught up
          "lines_per_second": 51.2,
          "last_received": "…+03:00",
          "unfinished_days": ["2026-10-11", "2026-10-12"],
          "last_error": null
        }
      },
      "config": {"sha256": "…", "loaded_at": "…+03:00",   # the configuration in use
                 "error": null, "restart_keys": []}       # (configwatch, ADR-0014); absent
    }                                                     # without a watched file

Times are Europe/Istanbul (like ``collector-status.json``). Rates and lags are estimates over
the last status interval.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.timeutil import isoformat_site, utc_now
from arcilogger_ingest import __version__

if TYPE_CHECKING:
    from arcilogger_ingest.service import IngestService, Stream

log = logging.getLogger("arcilogger.ingest")


def _size(service: IngestService, stream: Stream) -> int | None:
    if stream.size is not None:
        return stream.size
    entry = service.seals.entry(stream.info.day, stream.info.source)
    if entry is not None:
        return entry.bytes_uncompressed
    try:
        return service.layout.spool_file(stream.info.source, stream.info.day).stat().st_size
    except OSError:
        return None


def build_status(service: IngestService) -> dict[str, Any]:
    now = utc_now()
    counters = service.counters
    sources: dict[str, dict[str, Any]] = {}
    for stream in sorted(service.streams.values(), key=lambda s: (s.info.source, s.info.day)):
        name = stream.info.source
        size = _size(service, stream)
        lag = None if size is None else max(size - stream.offset, 0)
        entry = sources.setdefault(
            name,
            {
                "lag_bytes": 0,
                "unfinished_days": [],
                "last_error": None,
                "last_received": None,
                "lag_seconds": None,
            },
        )
        entry.update(
            day=stream.info.day.isoformat(),
            byte_offset=stream.offset,
            line_number=stream.line,
            read_from=stream.origin,
        )
        entry["unfinished_days"].append(stream.info.day.isoformat())
        if entry["lag_bytes"] is not None:
            entry["lag_bytes"] = None if lag is None else entry["lag_bytes"] + lag
        if stream.last_received is not None:
            entry["last_received"] = isoformat_site(stream.last_received)
            entry["_last"] = stream.last_received
        if stream.error is not None:
            entry["last_error"] = stream.error
    for name, entry in sources.items():
        last = entry.pop("_last", None)
        if entry["lag_bytes"] == 0:
            entry["lag_seconds"] = 0.0
        elif last is not None:
            entry["lag_seconds"] = round(max((now - last).total_seconds(), 0.0), 1)
        per_source = counters.per_source.get(name)
        entry["lines_per_second"] = round(per_source.rate, 1) if per_source else 0.0
    status: dict[str, Any] = {
        "version": __version__,
        "pid": os.getpid(),
        "state": service.state,
        "started_at": isoformat_site(service.started_at, "seconds"),
        "updated_at": isoformat_site(now, "seconds"),
        "database": {"ok": service.db_ok, "last_error": service.db_error},
        "parse_workers": service.parse_workers(),
        "totals": {
            "lines": counters.lines,
            "rows": counters.rows,
            "parse_failures": counters.failures,
            "batches": counters.batches,
            "lines_per_second": round(counters.rate, 1),
        },
        "sources": dict(sorted(sources.items())),
    }
    if service.config_watch is not None:
        status["config"] = service.config_watch.status_block()
    return status


class StatusWriter:
    """Writes the status file; a failed write is logged once and retried next time.

    On Windows the atomic rename fails while a reader holds the file open without delete
    sharing; the service must not stop for that.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._failing = False

    def write(self, service: IngestService) -> None:
        text = json.dumps(build_status(service), ensure_ascii=False, indent=2) + "\n"
        try:
            atomic_write_text(self.path, text)
        except OSError as exc:
            if not self._failing:
                log.warning("could not write %s: %s", self.path.name, exc)
            self._failing = True
            return
        self._failing = False


def read_status(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
