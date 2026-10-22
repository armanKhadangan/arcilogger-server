"""The data of the scheduled usage reports (group 2 contract section 3, schema 1).

:func:`build_report_data` reads the rollups — nothing else — and returns a JSON-serialisable
dictionary that area R renders into the e-mail, the PDF and the Excel workbook. Read-only and
deterministic: the same rollups and the same ``generated_at`` give the same dictionary.

**Period** ``[start, end)``: the site days it touches. Sites, categories, sessions and the
per-person site table always cover **whole days**; when ``start``/``end`` are not midnights (the
end-of-day report: 00:00-18:30) the time and volume figures and the hourly table are limited to
the hours that start inside the window (``usage_hourly``; an hour that starts before ``end``
counts whole), the blocked attempts to the window, and the first / last activity to the active
minutes that start inside it (the days' ``timeline``; a report run late never shows the evening's
activity, nor the window's end as an activity time) — the notes in ``data_quality`` say so.

**Blocked attempts:** the engine stores at most ``usage.blocked_per_day`` attempts per subject
and day (the newest) and counts all of them in ``usage_daily``. A whole-day report counts every
attempt and lists the stored ones; a part-day window can only count the stored ones. A note in
``data_quality`` names how many subjects had a day over the limit (whole days: only with the
person-level lists, which miss the rest).

**Changes** (``usage.change_*`` settings): company and department active time, download volume
and time per category, compared with the average of the 7 days before the period that have a
rollup (the same hours of the day for a part-day window; categories whole days); only changes of
at least ``usage.change_min_percent`` with at least ``usage.change_min_seconds`` /
``usage.change_min_bytes`` on one side, at most 10, largest first. A multi-day period is compared
by its average per day.

``person_level`` false: ``persons`` and the detail lists are empty. ``pseudonymize``: every
person and device label is a pseudonym (``usage.pseudonym_key``, :mod:`.pseudonyms`) and the MAC
and IP addresses are left out. Persons with ``usage_excluded`` never appear in person-level lists
(their traffic stays in the company and department totals). Every figure is an estimate.

``persons``, ``person_sites`` and ``blocked`` carry the ``subject`` key of their row (the
pseudonym's token with ``pseudonymize``): names are not unique, the renderers match a person's
detail rows by it. ``blocked`` lists at most :data:`BLOCKED_MAX` attempts, oldest first;
``blocked_total`` says how many the period holds (a note in ``data_quality`` when it is cut).
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from sqlalchemy import Connection, text
from sqlalchemy.orm import Session

from arcilogger_core.config import load_config
from arcilogger_core.fmt import format_bytes, format_duration, format_number
from arcilogger_core.timeutil import day_window, isoformat_site, site_day, to_site, utc_now
from arcilogger_worker.usage import query
from arcilogger_worker.usage.config import UNKNOWN_CATEGORY, UsageSettings
from arcilogger_worker.usage.pseudonyms import PseudonymKeyMissingError, load_key
from arcilogger_worker.usage.query import Context, Period, Viewer

SCHEMA: Final = 1
KINDS: Final = ("end_of_day", "full_day", "weekly", "monthly", "custom")
BLOCKED_MAX: Final = 50_000
COMPANY_TOP: Final = 20
PERSON_TOP_SITES: Final = 20
PERSON_TOP_CATEGORIES: Final = 10
CHANGES_MAX: Final = 10
BASELINE_DAYS: Final = 7
METHOD_TR: Final = (
    "Süreler ve hacimler bağlantı kayıtlarından tahmin edilir (1 dakikalık dilimler; arka "
    "planda açık sekmeler de sayılabilir); kesin değer değildir."
)
METHOD_EN: Final = (
    "Durations and volumes are estimated from connection logs (1-minute buckets; tabs left "
    "open in the background may be counted too); they are not exact values."
)


def _iso(value: datetime | None) -> str | None:
    return None if value is None else isoformat_site(value, timespec="seconds")


def _bounds(start: datetime, end: datetime) -> tuple[Period, bool]:
    if end <= start:
        raise ValueError("end must be after start")
    first = site_day(start)
    last = site_day(end - timedelta(microseconds=1))
    whole = start == day_window(first)[0] and end == day_window(last)[1]
    return Period(first, last, "all"), whole


def _session(connection: Connection | Session) -> Session:
    if isinstance(connection, Session):
        return connection
    return Session(bind=connection)


def _viewer(pseudonymize: bool, key: bytes | str | None) -> Viewer:
    if not pseudonymize:
        return Viewer()
    if key is None:
        config = load_config(None)
        key = load_key(Path(config.secrets_dir))
    if key is None:
        raise PseudonymKeyMissingError("usage.pseudonym_key is not in the secret store")
    return Viewer(True, key.encode("utf-8") if isinstance(key, str) else key)


# -- part-day windows -----------------------------------------------------------------------------


def _hour_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    """Hours that start in ``[start, end)``: ``[floor(start), end)``."""
    begin = start.astimezone(UTC).replace(minute=0, second=0, microsecond=0)
    return begin, end


def _window_metrics(session: Session, start: datetime, end: datetime) -> dict[str, dict[str, int]]:
    begin, finish = _hour_window(start, end)
    rows = session.execute(
        text(
            "SELECT subject, sum(online_seconds), sum(active_seconds), sum(download_bytes), "
            "sum(upload_bytes), coalesce(sum(active_seconds) FILTER (WHERE off_hours), 0) "
            "FROM usage_hourly WHERE hour >= :begin AND hour < :finish GROUP BY subject"
        ),
        {"begin": begin, "finish": finish},
    )
    return {
        subject: {
            "online_seconds": int(online),
            "active_seconds": int(active),
            "download_bytes": int(down),
            "upload_bytes": int(up),
            "off_hours_active_seconds": int(off),
        }
        for subject, online, active, down, up, off in rows
    }


def _blocked_counts(session: Session, start: datetime, end: datetime) -> dict[str, int]:
    rows = session.execute(
        text(
            "SELECT subject, count(*) FROM usage_blocked WHERE time >= :start AND time < :end "
            "GROUP BY subject"
        ),
        {"start": start, "end": end},
    )
    return {subject: int(count) for subject, count in rows}


def _window_activity(
    ctx: Context, start: datetime, end: datetime
) -> dict[str, tuple[datetime, datetime]]:
    """First and last activity per subject inside ``[start, end)``.

    The start of the first and the end of the last active minute that starts in the window (the
    days' ``timeline`` runs); the end is cut at ``end``.
    """
    rows = ctx.session.execute(
        text("SELECT day, subject, timeline FROM usage_daily WHERE day >= :first AND day <= :last"),
        {"first": ctx.period.first, "last": ctx.period.last},
    )
    found: dict[str, tuple[datetime, datetime]] = {}
    for day, subject, runs in rows:
        midnight = day_window(day)[0].astimezone(UTC)
        # The minutes of the day that start inside the window: [low, high).
        low = math.ceil((start - midnight) / timedelta(minutes=1))
        high = math.ceil((end - midnight) / timedelta(minutes=1))
        for first_minute, end_minute, _domain in runs:
            first, last = max(first_minute, low), min(end_minute, high)
            if first >= last:
                continue
            begin = midnight + timedelta(minutes=first)
            finish = min(midnight + timedelta(minutes=last), end)
            known = found.get(subject)
            if known is not None:
                begin, finish = min(begin, known[0]), max(finish, known[1])
            found[subject] = (begin, finish)
    return found


def _subject_metrics(
    ctx: Context, start: datetime, end: datetime, whole: bool
) -> dict[str, dict[str, Any]]:
    """Metrics per subject of the report window."""
    if whole:
        return {key: dict(stats.metrics) for key, stats in ctx.stats.items()}
    window = _window_metrics(ctx.session, start, end)
    blocked = _blocked_counts(ctx.session, start, end)
    activity = _window_activity(ctx, start, end)
    found = {}
    for key, stats in ctx.stats.items():
        values = window.get(key)
        if values is None and key not in blocked:
            continue
        first, last = activity.get(key, (None, None))
        found[key] = {
            **(
                values
                or dict.fromkeys(
                    (
                        "online_seconds",
                        "active_seconds",
                        "download_bytes",
                        "upload_bytes",
                        "off_hours_active_seconds",
                    ),
                    0,
                )
            ),
            "sessions": stats.metrics["sessions"],
            "blocked": blocked.get(key, 0),
            "first_activity": first,
            "last_activity": last,
        }
    return found


# -- the report -----------------------------------------------------------------------------------


def build_report_data(
    connection: Connection | Session,
    *,
    start: datetime,
    end: datetime,
    kind: str,
    person_level: bool,
    pseudonymize: bool,
    generated_at: datetime | None = None,
    pseudonym_key: bytes | str | None = None,
) -> dict[str, Any]:
    """Schema-1 report data of ``[start, end)`` (see the module text).

    ``pseudonym_key``: the ``usage.pseudonym_key`` secret (default: read from the secret store
    of the configuration ``arcilogger.toml`` names) — only used with ``pseudonymize``.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown report kind {kind!r}")
    session = _session(connection)
    period, whole = _bounds(start, end)
    viewer = _viewer(pseudonymize, pseudonym_key)
    settings = UsageSettings.load(session)
    ctx = Context(session, period, viewer)
    metrics = _subject_metrics(ctx, start, end, whole)
    sites = query.site_rows(ctx, None, params={})
    categories = query.category_rows(sites, query.category_users(ctx, None))
    company = query.empty_metrics()
    active_users = 0
    for values in metrics.values():
        query.add_metrics(company, values)
        active_users += values["active_seconds"] > 0
    departments = _departments(ctx, metrics)
    data: dict[str, Any] = {
        "schema": SCHEMA,
        "kind": kind,
        "period": {"from": _iso(start), "to": _iso(end)},
        "generated_at": _iso(generated_at or utc_now()),
        "person_level": person_level,
        "pseudonymized": pseudonymize,
        "computed_until": _iso(query.computed_until(session, period)),
        "company": {
            "active_users": active_users,
            "online_seconds": company["online_seconds"],
            "active_seconds": company["active_seconds"],
            "download_bytes": company["download_bytes"],
            "upload_bytes": company["upload_bytes"],
            "sessions": company["sessions"],
            "blocked": company["blocked"],
            "off_hours_active_seconds": company["off_hours_active_seconds"],
            "top_sites": [
                {
                    "domain": row["domain"],
                    "category": row["category"],
                    "seconds": row["seconds"],
                    "share": row["share"],
                    "download_bytes": row["download_bytes"],
                    "upload_bytes": row["upload_bytes"],
                }
                for row in sites
                if row["seconds"] > 0
            ][:COMPANY_TOP],
            "top_categories": [
                {"category": row["category"], "seconds": row["seconds"], "share": row["share"]}
                for row in categories
                if row["seconds"] > 0
            ][:COMPANY_TOP],
        },
        "departments": departments,
        "persons": [],
        "changes": _changes(ctx, settings, start, end, whole, metrics, sites),
        "data_quality": _data_quality(ctx, start, end, whole, metrics, person_level),
        "person_sites": [],
        "person_hours": [],
        "blocked": [],
        "blocked_total": 0,
        "devices": [],
    }
    if person_level:
        _person_level(ctx, data, metrics, start, end, whole)
    return data


def _departments(ctx: Context, metrics: Mapping[str, Mapping[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for key, values in metrics.items():
        name = ctx.infos[key].department_name
        entry = grouped.setdefault(
            name,
            {
                "name": name,
                "active_users": 0,
                "active_seconds": 0,
                "download_bytes": 0,
                "upload_bytes": 0,
                "blocked": 0,
            },
        )
        entry["active_users"] += values["active_seconds"] > 0
        for field in ("active_seconds", "download_bytes", "upload_bytes", "blocked"):
            entry[field] += values[field]
    return sorted(grouped.values(), key=lambda row: (-row["active_seconds"], row["name"]))


def _label(ctx: Context, key: str) -> str:
    return str(ctx.ref(key)["label"])


def _person_level(
    ctx: Context,
    data: dict[str, Any],
    metrics: Mapping[str, Mapping[str, Any]],
    start: datetime,
    end: datetime,
    whole: bool,
) -> None:
    persons = sorted(
        (key for key in metrics if ctx.stats[key].kind == "person" and ctx.visible(key)),
        key=lambda key: (query.fold(_label(ctx, key)), key),
    )
    visible = {key for key in metrics if ctx.visible(key)}
    tops = query.top_sites_by_subject(ctx, persons)
    categories = ctx.categories(d for items in tops.values() for d, _ in items)
    for key in persons:
        values = metrics[key]
        sites = tops.get(key, [])
        by_category: dict[str, float] = defaultdict(float)
        for domain, seconds in sites:
            by_category[categories.get(domain, UNKNOWN_CATEGORY)] += seconds
        ref = ctx.ref(key)
        data["persons"].append(
            {
                "label": ref["label"],
                "subject": ref["key"],
                "department": ctx.infos[key].department,
                "first_activity": _iso(values["first_activity"]),
                "last_activity": _iso(values["last_activity"]),
                "online_seconds": values["online_seconds"],
                "active_seconds": values["active_seconds"],
                "download_bytes": values["download_bytes"],
                "upload_bytes": values["upload_bytes"],
                "sessions": values["sessions"],
                "top_sites": [
                    {"domain": d, "seconds": round(s)} for d, s in sites[:PERSON_TOP_SITES]
                ],
                "top_categories": [
                    {"category": c, "seconds": round(s)}
                    for c, s in sorted(by_category.items(), key=lambda i: (-i[1], i[0]))[
                        :PERSON_TOP_CATEGORIES
                    ]
                ],
                "blocked": values["blocked"],
                "off_hours_active_seconds": values["off_hours_active_seconds"],
            }
        )
    data["person_sites"] = _person_sites(ctx, persons)
    data["person_hours"] = _person_hours(ctx, persons, start, end, whole)
    data["blocked"], data["blocked_total"] = _blocked(ctx, visible, start, end)
    listed, total = len(data["blocked"]), data["blocked_total"]
    if total > listed:
        data["data_quality"]["notes_tr"].append(
            f"Engellenen deneme listesi {format_number(listed)} kayıtta kesildi (dönemde "
            f"{format_number(total)} kayıt; en eskiler listelendi); kişi ve departman sayıları "
            "listelenmeyenleri de sayar."
        )
        data["data_quality"]["notes_en"].append(
            f"The list of blocked attempts was cut at {format_number(listed, lang='en')} rows "
            f"({format_number(total, lang='en')} in the period; the oldest are listed); the "
            "person and department counts include the ones not listed."
        )
    data["devices"] = _devices(ctx, visible, start, end, whole)


def _person_sites(ctx: Context, persons: list[str]) -> list[dict[str, Any]]:
    if not persons:
        return []
    rows = list(
        ctx.session.execute(
            text(
                "SELECT subject, domain, sum(seconds), sum(download_bytes), sum(upload_bytes), "
                "sum(sessions) FROM usage_domain_daily WHERE day >= :first AND day <= :last "
                "AND subject = ANY(:subjects) GROUP BY subject, domain"
            ),
            {"first": ctx.period.first, "last": ctx.period.last, "subjects": persons},
        )
    )
    categories = ctx.categories(row[1] for row in rows)
    refs = {subject: ctx.ref(subject) for subject in persons}
    found: list[dict[str, Any]] = [
        {
            "label": refs[subject]["label"],
            "subject": refs[subject]["key"],
            "department": ctx.infos[subject].department,
            "domain": domain,
            "category": categories.get(domain),
            "seconds": round(float(seconds)),
            "download_bytes": int(down),
            "upload_bytes": int(up),
            "sessions": int(sessions),
        }
        for subject, domain, seconds, down, up, sessions in rows
    ]
    found.sort(
        key=lambda r: (query.fold(r["label"]), -r["seconds"], -r["download_bytes"], r["domain"])
    )
    return found


def _person_hours(
    ctx: Context, persons: list[str], start: datetime, end: datetime, whole: bool
) -> list[dict[str, Any]]:
    if not persons:
        return []
    begin, finish = (ctx.period.start, ctx.period.end) if whole else _hour_window(start, end)
    rows = ctx.session.execute(
        text(
            "SELECT subject, hour, sum(active_seconds), sum(download_bytes), sum(upload_bytes) "
            "FROM usage_hourly WHERE hour >= :begin AND hour < :finish "
            "AND subject = ANY(:subjects) GROUP BY subject, hour"
        ),
        {"begin": begin, "finish": finish, "subjects": persons},
    )
    found: list[dict[str, Any]] = [
        {
            "label": _label(ctx, subject),
            "hour": _iso(hour),
            "active_seconds": int(active),
            "download_bytes": int(down),
            "upload_bytes": int(up),
        }
        for subject, hour, active, down, up in rows
    ]
    found.sort(key=lambda r: (query.fold(r["label"]), r["hour"]))
    return found


def _blocked(
    ctx: Context, visible: set[str], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], int]:
    """The first :data:`BLOCKED_MAX` attempts of the visible subjects and how many there are."""
    rows = list(
        ctx.session.execute(
            text(
                "SELECT time, subject, domain, category, action, source, count(*) OVER () "
                "FROM usage_blocked WHERE time >= :start AND time < :end "
                "AND subject = ANY(:subjects) ORDER BY time, id LIMIT :limit"
            ),
            {"start": start, "end": end, "subjects": sorted(visible), "limit": BLOCKED_MAX},
        )
    )
    refs = {subject: ctx.ref(subject) for subject in {row[1] for row in rows}}
    found = [
        {
            "time": _iso(moment),
            "label": refs[subject]["label"],
            "subject": refs[subject]["key"],
            "domain": domain,
            "category": category,
            "action": action,
            "source": source,
        }
        for moment, subject, domain, category, action, source, _ in rows
    ]
    return found, int(rows[0][6]) if rows else 0


def _devices(
    ctx: Context, visible: set[str], start: datetime, end: datetime, whole: bool
) -> list[dict[str, Any]]:
    rows = ctx.session.execute(
        text(
            "SELECT mac::text, subject, mode() WITHIN GROUP (ORDER BY host(ip)), "
            "mode() WITHIN GROUP (ORDER BY vlan), sum(active_seconds), sum(download_bytes), "
            "sum(upload_bytes) FROM usage_device_daily WHERE day >= :first AND day <= :last "
            "GROUP BY mac, subject"
        ),
        {"first": ctx.period.first, "last": ctx.period.last},
    )
    devices: dict[str, dict[str, Any]] = {}
    for mac, subject, ip, vlan, active, down, up in rows:
        if subject not in visible:
            continue
        ref = query.device_ref(ctx.directory, mac, ip, ctx.viewer)
        entry = devices.setdefault(
            mac,
            {
                "label": ref["label"],
                "mac": ref["mac"],
                "ip": ref["ip"],
                "vlan": None if ctx.viewer.pseudonymize else vlan,
                "persons": [],
                "active_seconds": 0,
                "download_bytes": 0,
                "upload_bytes": 0,
            },
        )
        if ctx.stats[subject].kind == "person":
            entry["persons"].append(_label(ctx, subject))
        entry["active_seconds"] += int(active)
        entry["download_bytes"] += int(down)
        entry["upload_bytes"] += int(up)
    found = []
    for entry in devices.values():
        persons = sorted(set(entry.pop("persons")))
        entry["person"] = ", ".join(persons) if persons else None
        found.append(entry)
    for key in sorted(visible):
        stats = ctx.stats[key]
        if stats.kind != "ip":
            continue
        info = ctx.infos[key]
        found.append(
            {
                "label": _label(ctx, key),
                "mac": None,
                "ip": None if ctx.viewer.pseudonymize else info.ip,
                "vlan": None if ctx.viewer.pseudonymize else stats.hints.get("vlan"),
                "person": None,
                "active_seconds": stats.metrics["active_seconds"],
                "download_bytes": stats.metrics["download_bytes"],
                "upload_bytes": stats.metrics["upload_bytes"],
            }
        )
    found.sort(key=lambda r: (-r["active_seconds"], -r["download_bytes"], r["label"]))
    return found


# -- changes --------------------------------------------------------------------------------------


def _day_values(
    ctx: Context, days: list[date], start: datetime | None, end: datetime | None
) -> dict[date, dict[tuple[str, str], float]]:
    """Per day: ``(scope, metric) → value`` (scope ``company`` / ``department:<name>``)."""
    found: dict[date, dict[tuple[str, str], float]] = {day: defaultdict(float) for day in days}
    if not days:
        return found
    params: dict[str, Any] = {"first": min(days), "last": max(days)}
    if start is not None and end is not None:
        # The same hours of the day as the report window.
        first_minute = to_site(start).hour * 60
        last_minute = end - day_window(site_day(end - timedelta(microseconds=1)))[0]
        params |= {"from_minute": first_minute, "to_minute": last_minute.total_seconds() / 60}
        statement = (
            "SELECT day, subject, sum(active_seconds), sum(download_bytes) FROM usage_hourly "
            "WHERE day >= :first AND day <= :last AND extract(epoch FROM (hour - "
            "(day::timestamp AT TIME ZONE 'Europe/Istanbul'))) / 60 >= :from_minute AND "
            "extract(epoch FROM (hour - (day::timestamp AT TIME ZONE 'Europe/Istanbul'))) / 60 "
            "< :to_minute GROUP BY day, subject"
        )
    else:
        statement = (
            "SELECT day, subject, sum(active_seconds), sum(download_bytes) FROM usage_daily "
            "WHERE day >= :first AND day <= :last GROUP BY day, subject"
        )
    rows = list(ctx.session.execute(text(statement), params))
    infos = _infos_for(ctx, {row[1] for row in rows})
    for day, subject, active, down in rows:
        if day not in found:
            continue
        department = infos[subject].department_name
        for scope in ("company", f"department:{department}"):
            found[day][(scope, "active_seconds")] += float(active)
            found[day][(scope, "download_bytes")] += float(down)
    category_rows = list(
        ctx.session.execute(
            text(
                "SELECT d.day, d.subject, coalesce(c.category, :unknown), sum(d.seconds) "
                "FROM usage_domain_daily d LEFT JOIN domain_categories c ON c.domain = d.domain "
                "WHERE d.day >= :first AND d.day <= :last GROUP BY 1, 2, 3"
            ),
            {"first": min(days), "last": max(days), "unknown": UNKNOWN_CATEGORY},
        )
    )
    infos = _infos_for(ctx, {row[1] for row in category_rows})
    for day, subject, category, seconds in category_rows:
        if day not in found:
            continue
        department = infos[subject].department_name
        for scope in ("company", f"department:{department}"):
            found[day][(scope, f"category:{category}")] += float(seconds)
    return found


def _infos_for(ctx: Context, keys: Iterable[str]) -> dict[str, query.SubjectInfo]:
    missing = {key: {"kind": key.split(":", 1)[0]} for key in keys if key not in ctx.infos}
    if missing:
        hints = ctx.session.execute(
            text(
                "SELECT subject, mode() WITHIN GROUP (ORDER BY vlan) FROM usage_daily "
                "WHERE subject = ANY(:subjects) GROUP BY subject"
            ),
            {"subjects": sorted(missing)},
        )
        for subject, vlan in hints:
            missing[subject]["vlan"] = vlan
        ctx.infos.update(ctx.directory.resolve(missing))
    return ctx.infos


def _changes(
    ctx: Context,
    settings: UsageSettings,
    start: datetime,
    end: datetime,
    whole: bool,
    metrics: Mapping[str, Mapping[str, Any]],
    sites: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    period_days = ctx.period.days()
    computed = {
        row[0]
        for row in ctx.session.execute(
            text("SELECT day FROM usage_days WHERE day >= :first AND day < :last"),
            {
                "first": ctx.period.first - timedelta(days=BASELINE_DAYS),
                "last": ctx.period.first,
            },
        )
    }
    baseline_days = sorted(computed)
    if not baseline_days:
        return []
    # A part of one day is compared with the same hours of the baseline days.
    window = (start, end) if not whole and ctx.period.first == ctx.period.last else (None, None)
    baseline = _day_values(ctx, baseline_days, *window)
    current_days = _day_values(ctx, period_days, *window)
    current: dict[tuple[str, str], float] = defaultdict(float)
    for values in current_days.values():
        for name, value in values.items():
            current[name] += value / len(period_days)
    average: dict[tuple[str, str], float] = defaultdict(float)
    for values in baseline.values():
        for name, value in values.items():
            average[name] += value / len(baseline_days)
    found = []
    for name in sorted(set(current) | set(average)):
        value, base = current.get(name, 0.0), average.get(name, 0.0)
        scope, metric = name
        minimum = (
            settings.change_min_bytes
            if metric == "download_bytes"
            else (settings.change_min_seconds)
        )
        if base <= 0 or max(value, base) < minimum:
            continue
        change = (value - base) / base * 100
        if abs(change) < settings.change_min_percent:
            continue
        found.append(_change(scope, metric, value, base, change))
    found.sort(key=lambda c: (-abs(c["change_percent"]), c["scope"], c["metric"]))
    return found[:CHANGES_MAX]


def _change(scope: str, metric: str, value: float, base: float, change: float) -> dict[str, Any]:
    if scope == "company":
        where_tr, where_en = "Şirket", "Company"
    else:
        where_tr = where_en = scope.split(":", 1)[1]
    if metric == "download_bytes":
        what_tr, what_en = "indirilen veri", "download volume"
        shown = (format_bytes(round(value), "tr"), format_bytes(round(base), "tr"))
        shown_en = (format_bytes(round(value), "en"), format_bytes(round(base), "en"))
    else:
        if metric == "active_seconds":
            what_tr, what_en = "aktif kullanım süresi", "active time"
        else:
            name = metric.split(":", 1)[1]
            what_tr, what_en = f"{name} kategorisinde süre", f"time in the category {name}"
        shown = (format_duration(value, "tr"), format_duration(base, "tr"))
        shown_en = (format_duration(value, "en"), format_duration(base, "en"))
    percent = round(change)
    verb_tr = "arttı" if change > 0 else "azaldı"
    verb_en = "up" if change > 0 else "down"
    return {
        "scope": scope,
        "metric": metric,
        "value": round(value),
        "baseline_7d": round(base),
        "change_percent": round(change, 1),
        "text_tr": (
            f"{where_tr}: {what_tr} 7 günlük ortalamaya göre %{abs(percent)} {verb_tr} "
            f"({shown[0]}; ortalama {shown[1]}, tahmini)"
        ),
        "text_en": (
            f"{where_en}: {what_en} {verb_en} {abs(percent)} % on the 7-day average "
            f"({shown_en[0]}; average {shown_en[1]}, estimated)"
        ),
    }


# -- data quality ---------------------------------------------------------------------------------


def _data_quality(
    ctx: Context,
    start: datetime,
    end: datetime,
    whole: bool,
    metrics: Mapping[str, Mapping[str, Any]],
    person_level: bool,
) -> dict[str, Any]:
    unattributed = sum(
        values["active_seconds"] for key, values in metrics.items() if ctx.stats[key].kind == "ip"
    )
    total = sum(values["active_seconds"] for values in metrics.values())
    notes_tr, notes_en = [METHOD_TR], [METHOD_EN]
    rows = {
        day: (status, until)
        for day, status, until in ctx.session.execute(
            text(
                "SELECT day, status, computed_until FROM usage_days WHERE day >= :first "
                "AND day <= :last"
            ),
            {"first": ctx.period.first, "last": ctx.period.last},
        )
    }
    missing = [day for day in ctx.period.days() if day not in rows]
    if missing:
        days = ", ".join(f"{day:%d.%m.%Y}" for day in missing)
        notes_tr.append(f"Kullanım verisi hesaplanmamış günler: {days}.")
        notes_en.append(f"Days without computed usage data: {days}.")
    for day, (status, until) in sorted(rows.items()):
        if status == "partial" and until is not None:
            moment = f"{to_site(until):%d.%m.%Y %H:%M}"
            notes_tr.append(f"{day:%d.%m.%Y} verisi {moment} itibarıyla hesaplandı (geçici).")
            notes_en.append(f"Data of {day:%d.%m.%Y} computed up to {moment} (provisional).")
    if not whole:
        notes_tr.append(
            "Süre ve hacimler pencereye başlayan saatleri kapsar, ilk / son etkinlik penceredeki "
            "aktif dakikaları; site, kategori, oturum ve cihaz tabloları günün tamamını kapsar."
        )
        notes_en.append(
            "Times and volumes cover the hours starting in the window, first / last activity the "
            "active minutes in it; the site, category, session and device tables cover whole "
            "days."
        )
    limit = _blocked_limit_note(ctx, whole, person_level)
    if limit is not None:
        notes_tr.append(limit[0])
        notes_en.append(limit[1])
    if unattributed and total:
        share = round(unattributed / total * 100)
        notes_tr.append(
            f"Aktif sürenin %{share} kadarı bir cihaza bağlanamadı (yalnızca IP adresi)."
        )
        notes_en.append(f"About {share} % of the active time could not be linked to a device.")
    return {"unattributed_seconds": unattributed, "notes_tr": notes_tr, "notes_en": notes_en}


def _blocked_limit_note(ctx: Context, whole: bool, person_level: bool) -> tuple[str, str] | None:
    """The note on the subjects with a day over ``usage.blocked_per_day`` (stored attempts fewer
    than counted): a whole-day report counts every attempt, only its lists miss some."""
    if whole and not person_level:
        return None
    rows = ctx.session.execute(
        text(
            "SELECT d.subject FROM (SELECT day, subject, sum(blocked) AS counted FROM usage_daily "
            "WHERE day >= :first AND day <= :last GROUP BY day, subject) AS d "
            "LEFT JOIN (SELECT day, subject, count(*) AS stored FROM usage_blocked "
            "WHERE day >= :first AND day <= :last GROUP BY day, subject) AS b "
            "USING (day, subject) WHERE d.counted > coalesce(b.stored, 0)"
        ),
        {"first": ctx.period.first, "last": ctx.period.last},
    )
    over = {str(subject) for (subject,) in rows if not whole or ctx.visible(str(subject))}
    if not over:
        return None
    count = len(over)
    subjects = "person, device or IP address" if count == 1 else "persons, devices or IP addresses"
    note_tr = (
        f"{format_number(count)} kişi, cihaz ya da IP adresinin engellenen denemeleri en az bir "
        "gün günlük kayıt sınırını (usage.blocked_per_day) aştı: "
    )
    note_en = (
        f"The blocked attempts of {format_number(count, lang='en')} {subjects} exceeded the daily "
        "row limit (usage.blocked_per_day) on at least one day: "
    )
    if whole:
        return (
            note_tr + "listeler o günlerin yalnızca en yeni kayıtlarını içerir; sayılar hepsini "
            "sayar.",
            note_en + "the lists hold only the newest rows of those days; the counts include "
            "every attempt.",
        )
    return (
        note_tr + "rapor günün bir bölümünü kapsadığından sayılar ve listeler yalnızca saklanan "
        "kayıtları içerir; gerçek sayı daha yüksek olabilir.",
        note_en + "as the report covers part of a day, the counts and lists hold only the stored "
        "rows; the real number may be higher.",
    )


# -- the fixed example ----------------------------------------------------------------------------


def sample_report_data() -> dict[str, Any]:
    """A fixed, realistic schema-1 example (synthetic Turkish names; area R's tests)."""

    subjects = {"Ayşe Yılmaz": "person:1", "Elif Şahin": "person:2", "Mehmet Kaya": "person:3"}

    def person(
        label: str, department: str, first: str, last: str, active: int, sites: list[Any]
    ) -> dict[str, Any]:
        return {
            "label": label,
            "subject": subjects[label],
            "department": department,
            "first_activity": f"2026-10-14T{first}:00+03:00",
            "last_activity": f"2026-10-14T{last}:00+03:00",
            "online_seconds": active + 5400,
            "active_seconds": active,
            "download_bytes": active * 41_000,
            "upload_bytes": active * 3_100,
            "sessions": active // 9,
            "top_sites": [{"domain": d, "seconds": s} for d, s, _ in sites],
            "top_categories": _sample_categories(sites),
            "blocked": 1 if label == "Mehmet Kaya" else 0,
            "off_hours_active_seconds": 900 if label == "Elif Şahin" else 0,
        }

    ayse_sites = [
        ("portal.example", 7200, "İş Uygulamaları"),
        ("posta.example", 3600, "E-posta"),
        ("haber.example", 1500, "Haber"),
    ]
    mehmet_sites = [
        ("video.example", 4200, "Video"),
        ("portal.example", 2400, "İş Uygulamaları"),
        ("sosyal.example", 1800, "Sosyal Medya"),
    ]
    elif_sites = [("bulut.example", 5400, "Bulut Depolama"), ("posta.example", 2700, "E-posta")]
    persons = [
        person("Ayşe Yılmaz", "Finans", "08:12", "18:04", 12300, ayse_sites),
        person("Elif Şahin", "Planlama", "08:47", "19:21", 8100, elif_sites),
        person("Mehmet Kaya", "İdari İşler", "09:05", "17:58", 8400, mehmet_sites),
    ]
    person_sites = [
        {
            "label": p["label"],
            "subject": p["subject"],
            "department": p["department"],
            "domain": domain,
            "category": category,
            "seconds": seconds,
            "download_bytes": seconds * 40_000,
            "upload_bytes": seconds * 3_000,
            "sessions": seconds // 60,
        }
        for p, sites in zip(persons, (ayse_sites, elif_sites, mehmet_sites), strict=True)
        for domain, seconds, category in sites
    ]
    person_hours = [
        {
            "label": p["label"],
            "hour": f"2026-10-14T{hour:02d}:00:00+03:00",
            "active_seconds": 1500 + 120 * (hour % 3),
            "download_bytes": (1500 + 120 * (hour % 3)) * 41_000,
            "upload_bytes": (1500 + 120 * (hour % 3)) * 3_100,
        }
        for p in persons
        for hour in range(9, 18)
    ]
    total_active = sum(p["active_seconds"] for p in persons) + 1800
    return {
        "schema": SCHEMA,
        "kind": "full_day",
        "period": {"from": "2026-10-14T00:00:00+03:00", "to": "2026-10-15T00:00:00+03:00"},
        "generated_at": "2026-10-15T07:00:12+03:00",
        "person_level": True,
        "pseudonymized": False,
        "computed_until": "2026-10-15T00:00:00+03:00",
        "company": {
            "active_users": 4,
            "online_seconds": sum(p["online_seconds"] for p in persons) + 7200,
            "active_seconds": total_active,
            "download_bytes": sum(p["download_bytes"] for p in persons) + 52_000_000,
            "upload_bytes": sum(p["upload_bytes"] for p in persons) + 1_900_000,
            "sessions": sum(p["sessions"] for p in persons) + 210,
            "blocked": 3,
            "off_hours_active_seconds": 900,
            "top_sites": [
                {
                    "domain": "portal.example",
                    "category": "İş Uygulamaları",
                    "seconds": 9600,
                    "share": 0.3,
                    "download_bytes": 384_000_000,
                    "upload_bytes": 28_800_000,
                },
                {
                    "domain": "posta.example",
                    "category": "E-posta",
                    "seconds": 6300,
                    "share": 0.1969,
                    "download_bytes": 252_000_000,
                    "upload_bytes": 18_900_000,
                },
                {
                    "domain": "bulut.example",
                    "category": "Bulut Depolama",
                    "seconds": 5400,
                    "share": 0.1688,
                    "download_bytes": 216_000_000,
                    "upload_bytes": 16_200_000,
                },
                {
                    "domain": "video.example",
                    "category": "Video",
                    "seconds": 4200,
                    "share": 0.1313,
                    "download_bytes": 168_000_000,
                    "upload_bytes": 12_600_000,
                },
                {
                    "domain": "sosyal.example",
                    "category": "Sosyal Medya",
                    "seconds": 1800,
                    "share": 0.0563,
                    "download_bytes": 72_000_000,
                    "upload_bytes": 5_400_000,
                },
                {
                    "domain": "haber.example",
                    "category": "Haber",
                    "seconds": 1500,
                    "share": 0.0469,
                    "download_bytes": 60_000_000,
                    "upload_bytes": 4_500_000,
                },
                {
                    "domain": "203.0.113.110",
                    "category": None,
                    "seconds": 1800,
                    "share": 0.0563,
                    "download_bytes": 52_000_000,
                    "upload_bytes": 1_900_000,
                },
            ],
            "top_categories": [
                {"category": "İş Uygulamaları", "seconds": 9600, "share": 0.3},
                {"category": "E-posta", "seconds": 6300, "share": 0.1969},
                {"category": "Bulut Depolama", "seconds": 5400, "share": 0.1688},
                {"category": "Video", "seconds": 4200, "share": 0.1313},
                {"category": "Sosyal Medya", "seconds": 1800, "share": 0.0563},
                {"category": "Bilinmiyor", "seconds": 1800, "share": 0.0563},
                {"category": "Haber", "seconds": 1500, "share": 0.0469},
            ],
        },
        "departments": [
            {
                "name": "Finans",
                "active_users": 1,
                "active_seconds": 12300,
                "download_bytes": 504_300_000,
                "upload_bytes": 38_130_000,
                "blocked": 0,
            },
            {
                "name": "İdari İşler",
                "active_users": 1,
                "active_seconds": 8400,
                "download_bytes": 344_400_000,
                "upload_bytes": 26_040_000,
                "blocked": 3,
            },
            {
                "name": "Planlama",
                "active_users": 1,
                "active_seconds": 8100,
                "download_bytes": 332_100_000,
                "upload_bytes": 25_110_000,
                "blocked": 0,
            },
            {
                "name": "Wi-Fi Misafir",
                "active_users": 1,
                "active_seconds": 1800,
                "download_bytes": 52_000_000,
                "upload_bytes": 1_900_000,
                "blocked": 0,
            },
        ],
        "persons": persons,
        "changes": [
            {
                "scope": "company",
                "metric": "category:Video",
                "value": 4200,
                "baseline_7d": 1500,
                "change_percent": 180.0,
                "text_tr": "Şirket: Video kategorisinde süre 7 günlük ortalamaya göre %180 arttı "
                "(1 sa 10 dk; ortalama 25 dk, tahmini)",
                "text_en": "Company: time in the category Video up 180 % on the 7-day average "
                "(1 h 10 min; average 25 min, estimated)",
            },
            {
                "scope": "department:Planlama",
                "metric": "download_bytes",
                "value": 332_100_000,
                "baseline_7d": 1_100_000_000,
                "change_percent": -69.8,
                "text_tr": "Planlama: indirilen veri 7 günlük ortalamaya göre %70 azaldı "
                "(316,7 MB; ortalama 1 GB, tahmini)",
                "text_en": "Planlama: download volume down 70 % on the 7-day average "
                "(316.7 MB; average 1 GB, estimated)",
            },
        ],
        "data_quality": {
            "unattributed_seconds": 1800,
            "notes_tr": [
                METHOD_TR,
                "Aktif sürenin %6 kadarı bir cihaza bağlanamadı (yalnızca IP adresi).",
            ],
            "notes_en": [
                METHOD_EN,
                "About 6 % of the active time could not be linked to a device.",
            ],
        },
        "person_sites": person_sites,
        "person_hours": person_hours,
        "blocked": [
            {
                "time": "2026-10-14T11:15:00+03:00",
                "label": "Mehmet Kaya",
                "subject": "person:3",
                "domain": "bahis.example",
                "category": "Kumar",
                "action": "blocked",
                "source": "web",
            },
            {
                "time": "2026-10-14T11:15:01+03:00",
                "label": "Mehmet Kaya",
                "subject": "person:3",
                "domain": "bahis.example",
                "category": "Kumar",
                "action": "block",
                "source": "dns",
            },
            {
                "time": "2026-10-14T14:40:00+03:00",
                "label": "Mehmet Kaya",
                "subject": "person:3",
                "domain": None,
                "category": "P2P",
                "action": "block",
                "source": "app",
            },
        ],
        "blocked_total": 3,
        "devices": [
            {
                "label": "Ayşe'nin bilgisayarı",
                "mac": "3c:52:82:00:10:01",
                "ip": "172.20.41.21",
                "vlan": 41,
                "person": "Ayşe Yılmaz",
                "active_seconds": 12300,
                "download_bytes": 504_300_000,
                "upload_bytes": 38_130_000,
            },
            {
                "label": "MKAYA-LAPTOP",
                "mac": "3c:52:82:00:10:07",
                "ip": "172.20.43.17",
                "vlan": 43,
                "person": "Mehmet Kaya",
                "active_seconds": 8400,
                "download_bytes": 344_400_000,
                "upload_bytes": 26_040_000,
            },
            {
                "label": "Elif'in telefonu",
                "mac": "a6:21:9b:00:31:0e",
                "ip": "172.20.60.143",
                "vlan": 60,
                "person": "Elif Şahin",
                "active_seconds": 8100,
                "download_bytes": 332_100_000,
                "upload_bytes": 25_110_000,
            },
            {
                "label": "172.20.61.77",
                "mac": None,
                "ip": "172.20.61.77",
                "vlan": 61,
                "person": None,
                "active_seconds": 1800,
                "download_bytes": 52_000_000,
                "upload_bytes": 1_900_000,
            },
        ],
    }


def _sample_categories(sites: list[Any]) -> list[dict[str, Any]]:
    totals: dict[str, int] = defaultdict(int)
    for _, seconds, category in sites:
        totals[category] += seconds
    return [
        {"category": c, "seconds": s}
        for c, s in sorted(totals.items(), key=lambda i: (-i[1], i[0]))
    ]
