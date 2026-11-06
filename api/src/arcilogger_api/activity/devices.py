"""Who is sending and receiving data now (Wave 5 contract §6.2, M2), answered from the worker's
rolling snapshot (``state/activity.json``, :mod:`arcilogger_worker.activity`; the M4 decision on
#201): per minute and subject the bytes, the destinations and applications and the sessions
started, counted by the usage engine.

At answer time, as ``/usage``: subjects resolved by
:class:`~arcilogger_worker.usage.query.Directory` and shown by
:func:`~arcilogger_worker.usage.query.subject_ref` (names, or pseudonyms for the viewer); a
person with ``usage_excluded`` is not a row, but their traffic counts in the company's totals and
minutes. The window and the chart both end with the minute under way.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy.orm import Session

from arcilogger_api.activity.models import (
    ActivityApp,
    ActivityDevices,
    ActivityDomain,
    ActivityMinute,
    ActivityRow,
    ActivityTotals,
    ActivityWindow,
)
from arcilogger_api.usage_models import SubjectRef
from arcilogger_worker.usage.query import Directory, Viewer, subject_ref

#: Minutes of the company's chart (``per_minute``).
PER_MINUTE: Final = 60
#: Most subjects in ``rows``.
MAX_ROWS: Final = 200
#: Most entries of ``top_domains`` and ``top_apps``.
TOP: Final = 3
MINUTE: Final = timedelta(minutes=1)
#: A snapshot older than this is not current (the worker or its thread stopped): the answer
#: says ``warming_up``.
STALE: Final = timedelta(seconds=60)
#: The pause before reading the snapshot a second time.
RETRY_SECONDS: Final = 0.05


def read_snapshot(path: Path) -> dict[str, Any] | None:
    """The snapshot, or ``None`` while there is none (the worker has not written it yet). A read
    that meets the worker's replace of the file (Windows) is tried once more."""
    for attempt in range(2):
        try:
            data = json.loads(path.read_bytes())
        except FileNotFoundError:
            return None
        except (OSError, ValueError):
            if attempt == 0:
                time.sleep(RETRY_SECONDS)
            continue
        return data if isinstance(data, dict) else None
    return None


def devices_now(
    db: Session, viewer: Viewer, minutes: ActivityWindow, now: datetime, snapshot_path: Path
) -> ActivityDevices:
    """``GET /activity/devices`` for the last ``minutes`` minutes before ``now``."""
    end = now.astimezone(UTC).replace(second=0, microsecond=0) + MINUTE
    window_from = end - minutes * MINUTE
    snapshot = read_snapshot(snapshot_path) or {}
    names = {int(i): tuple(name) for i, name in snapshot.get("destinations", {}).items()}
    app_names = {int(i): str(name) for i, name in snapshot.get("apps", {}).items()}
    #: minute → subject → [download, upload, sessions, [[destination, bytes]], [[app, bytes]]]
    by_minute: dict[datetime, Mapping[str, Any]] = {}
    for entry in snapshot.get("minutes", []):
        moment = datetime.fromisoformat(entry["minute"])
        if end - PER_MINUTE * MINUTE <= moment < end:
            by_minute[moment] = entry["subjects"]

    down: defaultdict[str, float] = defaultdict(float)
    up: defaultdict[str, float] = defaultdict(float)
    sessions: defaultdict[str, int] = defaultdict(int)
    domains: dict[str, defaultdict[tuple[str, str], float]] = {}
    apps: dict[str, defaultdict[str, float]] = {}
    last: dict[str, datetime] = {}
    for moment, subjects in by_minute.items():
        if moment < window_from:
            continue
        for key, (received, sent, started, volumes, by_app) in subjects.items():
            down[key] += received
            up[key] += sent
            sessions[key] += started
            if received or sent:
                last[key] = max(last.get(key, moment), moment)
            for index, volume in volumes:
                domain, kind = names[index]
                domains.setdefault(key, defaultdict(float))[(domain, kind)] += volume
            for index, volume in by_app:
                apps.setdefault(key, defaultdict(float))[app_names[index]] += volume
    active = sorted(
        key for key in set(down) | set(sessions) if down[key] + up[key] or sessions[key]
    )
    hints = snapshot.get("subjects", {})
    infos = Directory(db).resolve({key: hints.get(key, {}) for key in active})
    data_until = _moment(snapshot.get("data_until"))
    visible = [key for key in active if not infos[key].excluded]
    visible.sort(key=lambda key: (-down[key], -up[key], key))
    seconds = minutes * 60
    rows = []
    for key in visible[:MAX_ROWS]:
        newest = last.get(key, window_from) + MINUTE
        rows.append(
            ActivityRow(
                subject=SubjectRef.model_validate(subject_ref(infos[key], viewer)),
                download_bytes=round(down[key]),
                upload_bytes=round(up[key]),
                download_bps=down[key] * 8 / seconds,
                upload_bps=up[key] * 8 / seconds,
                sessions=sessions[key],
                top_domains=[
                    ActivityDomain.model_validate(
                        {"domain": domain, "kind": kind, "bytes": round(volume)}
                    )
                    for (domain, kind), volume in _top(domains.get(key))
                ],
                top_apps=[
                    ActivityApp(app=app, bytes=round(volume)) for app, volume in _top(apps.get(key))
                ],
                last_seen=newest if data_until is None else min(newest, data_until),
            )
        )
    per_minute = []
    for index in range(PER_MINUTE):
        moment = end - (PER_MINUTE - index) * MINUTE
        cells = by_minute.get(moment, {})
        per_minute.append(
            ActivityMinute(
                minute=moment,
                download_bytes=round(sum(cell[0] for cell in cells.values())),
                upload_bytes=round(sum(cell[1] for cell in cells.values())),
            )
        )
    total_down, total_up = sum(down.values()), sum(up.values())
    return ActivityDevices(
        window=minutes,
        rows=rows,
        more=len(visible) > MAX_ROWS,
        totals=ActivityTotals(
            download_bytes=round(total_down),
            upload_bytes=round(total_up),
            download_bps=total_down * 8 / seconds,
            upload_bps=total_up * 8 / seconds,
            sessions=sum(sessions.values()),
            subjects=len(active),
        ),
        per_minute=per_minute,
        data_until=data_until,
        warming_up=bool(snapshot.get("warming_up", True)) or _stale(snapshot, now),
        generated_at=now,
    )


def _stale(snapshot: Mapping[str, Any], now: datetime) -> bool:
    written = _moment(snapshot.get("generated_at"))
    return written is None or now.astimezone(UTC) - written > STALE


def _moment(value: Any) -> datetime | None:
    return datetime.fromisoformat(value) if isinstance(value, str) else None


def _top[T](counter: Mapping[T, float] | None) -> list[tuple[T, float]]:
    if not counter:
        return []
    return sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))[:TOP]
