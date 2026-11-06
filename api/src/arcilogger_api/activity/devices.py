"""Who is sending and receiving data now (Wave 5 contract §6.2, M2), from the FortiGate traffic
logs, counted by the usage engine itself (:mod:`arcilogger_worker.usage.engine`).

The engine reads a range instead of a site day (:func:`~arcilogger_worker.usage.engine.read_range`
into a :class:`~arcilogger_worker.usage.engine.DayBuilder` with a ``window``): its increment
rule (an interim log's deltas at its own time, the final log only the rest), its spreading of
each increment over the minutes it covers, its attribution of an address to a person, device or
address, and its destinations and background rules are those of Kullanım, so a day of this
counting adds up to the day's usage rollup. Two ranges are read: the last ``window`` minutes for
the subjects and the totals (a session counts once, at its first minute in the range), and the
last :data:`PER_MINUTE` minutes for the company's minute-by-minute chart. Both end with the
minute under way.

Subjects as in ``/usage`` (:class:`~arcilogger_worker.usage.query.Directory`,
:func:`~arcilogger_worker.usage.query.subject_ref`): names or pseudonyms by the viewer; a person
with ``usage_excluded`` is not a row, but their traffic counts in the company's totals and
minutes.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

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
from arcilogger_core.timeutil import site_day
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.domains import BackgroundRules, DomainNamer
from arcilogger_worker.usage.engine import BUCKET, DayBuilder, read_range
from arcilogger_worker.usage.query import Directory, Viewer, subject_ref

#: Minutes of the company's chart (``per_minute``).
PER_MINUTE: Final = 60
#: Most subjects in ``rows``.
MAX_ROWS: Final = 200
#: Most entries of ``top_domains`` and ``top_apps``.
TOP: Final = 3


def count_range(
    db: Session, lo: datetime, hi: datetime, *, settings: UsageSettings | None = None
) -> DayBuilder:
    """The usage engine's buckets of ``[lo, hi)`` (whole minutes), read from the logs."""
    settings = settings or UsageSettings.load(db)
    namer = DomainNamer(settings.psl_file, settings.psl_private_suffixes)
    rules = BackgroundRules.create(
        settings.background_domains, settings.background_appcats, settings.background_ports
    )
    builder = DayBuilder(
        site_day(hi - timedelta(seconds=BUCKET)), settings, namer, rules, window=(lo, hi)
    )
    read_range(db, builder, settings=settings)
    return builder


def _moment(builder: DayBuilder, index: int) -> datetime:
    return datetime.fromtimestamp(builder.lo_ts + index * BUCKET, UTC)


def devices_now(
    db: Session, viewer: Viewer, minutes: ActivityWindow, now: datetime
) -> ActivityDevices:
    """``GET /activity/devices`` for the last ``minutes`` minutes before ``now``."""
    settings = UsageSettings.load(db)
    end = now.astimezone(UTC).replace(second=0, microsecond=0) + timedelta(seconds=BUCKET)
    recent = count_range(db, end - timedelta(minutes=minutes), end, settings=settings)
    hour = count_range(db, end - timedelta(minutes=PER_MINUTE), end, settings=settings)
    seconds = minutes * 60

    down: defaultdict[str, float] = defaultdict(float)
    up: defaultdict[str, float] = defaultdict(float)
    domains: dict[str, defaultdict[tuple[str, str], float]] = {}
    apps: dict[str, defaultdict[str, float]] = {}
    last: dict[str, int] = {}
    for key, buckets in recent.cells.items():
        for index, cell in buckets.items():
            for flow_id, (_, received, sent) in cell.items():
                if not received and not sent:
                    continue
                down[key] += received
                up[key] += sent
                last[key] = max(last.get(key, index), index)
                flow = recent.flow_list[flow_id]
                if not flow.background:
                    domains.setdefault(key, defaultdict(float))[(flow.domain, flow.kind)] += (
                        received + sent
                    )
                if flow.app:
                    apps.setdefault(key, defaultdict(float))[flow.app] += received + sent
    sessions = {key: sum(counter.values()) for key, counter in recent.flow_sessions.items()}
    active = sorted(
        {key for key in down if down[key] + up[key] > 0} | {k for k, n in sessions.items() if n}
    )

    hints = {
        key: {
            "kind": recent.subjects[key].kind,
            "vlan": recent.subjects[key].vlan,
            "mac": recent.subjects[key].mac,
            "ip": _most(recent.subject_ips.get(key)),
        }
        for key in active
    }
    infos = Directory(db).resolve(hints)
    data_until = None if hour.newest is None else datetime.fromtimestamp(hour.newest, UTC)
    visible = [key for key in active if not infos[key].excluded]
    visible.sort(key=lambda key: (-down[key], -up[key], key))
    rows = []
    for key in visible[:MAX_ROWS]:
        newest = _moment(recent, last.get(key, 0) + 1)
        rows.append(
            ActivityRow(
                subject=SubjectRef.model_validate(subject_ref(infos[key], viewer)),
                download_bytes=round(down[key]),
                upload_bytes=round(up[key]),
                download_bps=down[key] * 8 / seconds,
                upload_bps=up[key] * 8 / seconds,
                sessions=sessions.get(key, 0),
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
    total_down, total_up = sum(down.values()), sum(up.values())
    per_minute = []
    for index in range(PER_MINUTE):
        received = sent = 0.0
        for buckets in hour.cells.values():
            for _, r, s in buckets.get(index, {}).values():
                received += r
                sent += s
        per_minute.append(
            ActivityMinute(
                minute=_moment(hour, index),
                download_bytes=round(received),
                upload_bytes=round(sent),
            )
        )
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
        generated_at=now,
    )


def _top[T](counter: Mapping[T, float] | None) -> list[tuple[T, float]]:
    if not counter:
        return []
    return sorted(counter.items(), key=lambda item: (-item[1], str(item[0])))[:TOP]


def _most(counter: Counter[str] | None) -> str | None:
    return counter.most_common(1)[0][0] if counter else None
