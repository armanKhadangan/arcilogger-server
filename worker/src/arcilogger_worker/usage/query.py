"""Reading the usage rollups: the answers of ``/api/v1/usage`` and the report data.

Everything works on whole site days (``Period``: ``from``/``to`` inclusive) and the part of the
day (``hours``: ``all`` = both rows of every subject and day, ``work`` / ``off`` = one of them).
Names, departments and categories are joined here, at read time (:class:`Directory`,
``domain_categories``), so they are always the current ones:

* **department** of a subject (spec 9.2, the attribution rule): the person's department, else the
  department of the subject's VLAN (the one with the most online minutes, else that of its
  blocked attempts and URLs) — the linked one, else the VLAN's name
  (:func:`arcilogger_db.network.vlan_department`); none → ``Bilinmiyor``;
* **category** of a domain: ``domain_categories`` (FortiGuard ``catdesc``), else ``Bilinmiyor``
  (``null`` in site rows).

Persons with ``usage_excluded`` never appear in person-level lists or details; their traffic still
counts in company, department, site and category totals. A :class:`Viewer` with ``pseudonymize``
sees pseudonyms (:mod:`.pseudonyms`) instead of names, MAC and IP addresses.

All durations are seconds, all volumes bytes, all of them estimates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy import text
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import day_window
from arcilogger_worker.usage.config import UNKNOWN_CATEGORY
from arcilogger_worker.usage.pseudonyms import pseudonym, token

Hours = Literal["all", "work", "off"]
#: Text key of the method note in the web locales (``usage.method_note``).
NOTICE_KEY: Final = "usage.method_note"
#: Department of subjects without one.
UNKNOWN_DEPARTMENT: Final = "Bilinmiyor"
SUBJECT_SORTS: Final = (
    "active_seconds",
    "online_seconds",
    "download_bytes",
    "upload_bytes",
    "blocked",
    "label",
)
SITE_SORTS: Final = (
    "seconds",
    "raw_seconds",
    "download_bytes",
    "upload_bytes",
    "sessions",
    "users",
    "domain",
)
#: Most users of a site or category, host names of a site and sites of a category in their
#: detail answers (the exports hold them all).
USERS_MAX: Final = 200
HOSTNAMES_MAX: Final = 50
CATEGORY_SITES_MAX: Final = 100
#: Same character table as ``arcilogger_api.textfold`` (Turkish-aware case folding).
_FOLD: Final = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZİıŞĞÜÖÇÂÎÛ", "abcdefghijklmnopqrstuvwxyziişğüöçâîû"
)


def fold(value: str) -> str:
    return value.translate(_FOLD)


@dataclass(frozen=True)
class Period:
    first: date
    last: date
    hours: Hours = "all"

    @property
    def parts(self) -> list[bool]:
        return {"all": [False, True], "work": [False], "off": [True]}[self.hours]

    @property
    def start(self) -> datetime:
        return day_window(self.first)[0]

    @property
    def end(self) -> datetime:
        return day_window(self.last)[1]

    def days(self) -> list[date]:
        return [self.first + timedelta(days=n) for n in range((self.last - self.first).days + 1)]

    def hour_starts(self) -> list[datetime]:
        start, end = self.start.astimezone(UTC), self.end.astimezone(UTC)
        count = int((end - start).total_seconds() // 3600)
        return [start + timedelta(hours=n) for n in range(count)]

    def params(self) -> dict[str, Any]:
        return {"first": self.first, "last": self.last, "parts": self.parts}

    def to_json(self) -> dict[str, Any]:
        return {"from": self.first.isoformat(), "to": self.last.isoformat(), "hours": self.hours}


@dataclass(frozen=True)
class Viewer:
    """How subjects are shown: names (default) or pseudonyms."""

    pseudonymize: bool = False
    key: bytes | None = None


# -- subjects -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SubjectInfo:
    key: str
    kind: str
    label: str
    person_id: int | None
    mac: str | None
    ip: str | None
    hostname: str | None
    department: str | None
    department_source: str | None
    excluded: bool

    @property
    def department_name(self) -> str:
        return self.department or UNKNOWN_DEPARTMENT


@dataclass
class Directory:
    """Current names, devices and departments (loaded on demand, cached per request)."""

    session: Session
    _vlans: dict[int, str] | None = field(default=None, init=False)
    _persons: dict[int, tuple[str, str | None, bool]] = field(default_factory=dict, init=False)
    _devices: dict[str, tuple[str | None, str | None, str | None]] = field(
        default_factory=dict, init=False
    )

    def vlan_departments(self) -> dict[int, str]:
        if self._vlans is None:
            rows = self.session.execute(
                text(
                    "SELECT v.vlan_id, coalesce(d.name, v.name_tr) FROM vlans v "
                    "LEFT JOIN departments d ON d.id = v.department_id "
                    "WHERE v.vlan_id IS NOT NULL"
                )
            )
            self._vlans = {int(vlan): str(name) for vlan, name in rows}
        return self._vlans

    def _load_persons(self, ids: Iterable[int]) -> None:
        missing = sorted({i for i in ids if i not in self._persons})
        if not missing:
            return
        rows = self.session.execute(
            text(
                "SELECT p.id, p.name, d.name, p.usage_excluded FROM persons p "
                "LEFT JOIN departments d ON d.id = p.department_id WHERE p.id = ANY(:ids)"
            ),
            {"ids": missing},
        )
        for person_id, name, department, excluded in rows:
            self._persons[int(person_id)] = (str(name), department, bool(excluded))

    def _load_devices(self, macs: Iterable[str]) -> None:
        missing = sorted({m for m in macs if m not in self._devices})
        if not missing:
            return
        rows = self.session.execute(
            text(
                "SELECT mac::text, name, hostnames[1], host(last_ip) FROM devices "
                "WHERE mac = ANY(CAST(:macs AS macaddr[]))"
            ),
            {"macs": missing},
        )
        for mac, name, hostname, last_ip in rows:
            self._devices[str(mac)] = (name, hostname, last_ip)
        for mac in missing:
            self._devices.setdefault(mac, (None, None, None))

    def device_label(self, mac: str) -> tuple[str, str | None]:
        """(label, host name) of a device: its name, else a host name, else the MAC."""
        self._load_devices([mac])
        name, hostname, _ = self._devices[mac]
        return (name or hostname or mac), hostname

    def resolve(self, hints: Mapping[str, Mapping[str, Any]]) -> dict[str, SubjectInfo]:
        """Subject key → info; ``hints`` per key: ``kind``, ``vlan``, ``ip``, ``mac``."""
        self._load_persons(int(key.split(":", 1)[1]) for key in hints if key.startswith("person:"))
        self._load_devices(key.split(":", 1)[1] for key in hints if key.startswith("device:"))
        vlans = self.vlan_departments()
        found = {}
        for key, hint in hints.items():
            kind, _, ident = key.partition(":")
            vlan = hint.get("vlan")
            vlan_department = vlans.get(int(vlan)) if vlan is not None else None
            department, source = vlan_department, ("vlan" if vlan_department else None)
            person_id = mac = hostname = None
            excluded = False
            ip = hint.get("ip")
            if kind == "person":
                person_id = int(ident)
                name, person_department, excluded = self._persons.get(
                    person_id, (f"#{person_id}", None, False)
                )
                label = name
                if person_department:
                    department, source = person_department, "person"
            elif kind == "device":
                mac = ident
                label, hostname = self.device_label(mac)
            else:
                label = ident
                ip = ident
            found[key] = SubjectInfo(
                key, kind, label, person_id, mac, ip, hostname, department, source, excluded
            )
        return found


def subject_ref(info: SubjectInfo, viewer: Viewer) -> dict[str, Any]:
    if viewer.pseudonymize:
        if viewer.key is None:
            raise ValueError("pseudonyms need the key")
        key, label = pseudonym(viewer.key, info.key)
        return {
            "kind": info.kind,
            "key": key,
            "label": label,
            "pseudonymous": True,
            "mac": None,
            "ip": None,
            "hostname": None,
        }
    return {
        "kind": info.kind,
        "key": info.key,
        "label": info.label,
        "pseudonymous": False,
        "mac": info.mac,
        "ip": info.ip,
        "hostname": info.hostname,
    }


def device_ref(directory: Directory, mac: str, ip: str | None, viewer: Viewer) -> dict[str, Any]:
    label, hostname = directory.device_label(mac)
    info = SubjectInfo(f"device:{mac}", "device", label, None, mac, ip, hostname, None, None, False)
    return subject_ref(info, viewer)


# -- metrics --------------------------------------------------------------------------------------


def empty_metrics() -> dict[str, Any]:
    return {
        "online_seconds": 0,
        "active_seconds": 0,
        "download_bytes": 0,
        "upload_bytes": 0,
        "sessions": 0,
        "blocked": 0,
        "off_hours_active_seconds": 0,
        "first_activity": None,
        "last_activity": None,
    }


def add_metrics(total: dict[str, Any], other: Mapping[str, Any]) -> dict[str, Any]:
    for name in (
        "online_seconds",
        "active_seconds",
        "download_bytes",
        "upload_bytes",
        "sessions",
        "blocked",
        "off_hours_active_seconds",
    ):
        total[name] += other[name]
    for name, pick in (("first_activity", min), ("last_activity", max)):
        values = [v for v in (total[name], other[name]) if v is not None]
        total[name] = pick(values) if values else None
    return total


@dataclass
class SubjectStats:
    key: str
    kind: str
    hints: dict[str, Any]
    metrics: dict[str, Any]


_SUBJECTS = """
SELECT subject, min(kind), mode() WITHIN GROUP (ORDER BY vlan), mode() WITHIN GROUP
       (ORDER BY host(ip)), max(mac::text),
       sum(online_seconds), sum(active_seconds), sum(download_bytes), sum(upload_bytes),
       sum(sessions), sum(blocked), coalesce(sum(active_seconds) FILTER (WHERE off_hours), 0),
       min(first_activity), max(last_activity)
FROM usage_daily
WHERE day >= :first AND day <= :last AND off_hours = ANY(:parts) {extra}
GROUP BY subject
"""


def subject_stats(
    session: Session, period: Period, subjects: Sequence[str] | None = None
) -> dict[str, SubjectStats]:
    extra = "" if subjects is None else "AND subject = ANY(:subjects)"
    params: dict[str, Any] = period.params()
    if subjects is not None:
        params["subjects"] = list(subjects)
    found = {}
    for row in session.execute(text(_SUBJECTS.format(extra=extra)), params):
        (key, kind, vlan, ip, mac, online, active, down, up, sessions, blocked, off, first,
         last) = row  # fmt: skip
        found[key] = SubjectStats(
            key,
            kind,
            {"kind": kind, "vlan": vlan, "ip": ip, "mac": mac},
            {
                "online_seconds": int(online),
                "active_seconds": int(active),
                "download_bytes": int(down),
                "upload_bytes": int(up),
                "sessions": int(sessions),
                "blocked": int(blocked),
                "off_hours_active_seconds": int(off),
                "first_activity": first,
                "last_activity": last,
            },
        )
    return found


def computed_until(session: Session, period: Period) -> datetime | None:
    value = session.execute(
        text(
            "SELECT computed_until FROM usage_days WHERE day >= :first AND day <= :last "
            "ORDER BY day DESC LIMIT 1"
        ),
        {"first": period.first, "last": period.last},
    ).scalar_one_or_none()
    return value if isinstance(value, datetime) else None


def envelope(session: Session, period: Period) -> dict[str, Any]:
    """The fields every answer carries."""
    return {
        "period": period.to_json(),
        "estimated": True,
        "computed_until": computed_until(session, period),
        "notice_key": NOTICE_KEY,
    }


# -- context of one request -----------------------------------------------------------------------


@dataclass
class Context:
    """One request: the period, the viewer, the subjects (resolved once) and a department."""

    session: Session
    period: Period
    viewer: Viewer = field(default_factory=Viewer)
    department: str | None = None
    directory: Directory = field(init=False)
    stats: dict[str, SubjectStats] = field(init=False)
    infos: dict[str, SubjectInfo] = field(init=False)

    def __post_init__(self) -> None:
        self.directory = Directory(self.session)
        self.stats = subject_stats(self.session, self.period)
        self.infos = self.directory.resolve({k: s.hints for k, s in self.stats.items()})

    @property
    def scope(self) -> list[str] | None:
        """Subject keys of the department filter (``None``: everyone)."""
        if self.department is None:
            return None
        return sorted(
            key for key, info in self.infos.items() if info.department_name == self.department
        )

    def visible(self, key: str) -> bool:
        """May appear in person-level lists (not an excluded person)."""
        info = self.infos.get(key)
        return info is not None and not info.excluded

    def ref(self, key: str) -> dict[str, Any]:
        return subject_ref(self.infos[key], self.viewer)

    def categories(self, domains: Iterable[str]) -> dict[str, str]:
        wanted = sorted(set(domains))
        if not wanted:
            return {}
        rows = self.session.execute(
            text("SELECT domain, category FROM domain_categories WHERE domain = ANY(:domains)"),
            {"domains": wanted},
        )
        return {str(domain): str(category) for domain, category in rows}

    def scope_sql(self, subjects: Sequence[str] | None) -> tuple[str, dict[str, Any]]:
        params = self.period.params()
        if subjects is None:
            return "", params
        return "AND subject = ANY(:subjects)", {**params, "subjects": list(subjects)}


# -- sites, categories, applications --------------------------------------------------------------

_SITES = """
SELECT domain, min(kind), sum(seconds), sum(raw_seconds), sum(download_bytes),
       sum(upload_bytes), sum(sessions), count(DISTINCT subject), bool_and(background)
FROM usage_domain_daily
WHERE day >= :first AND day <= :last AND off_hours = ANY(:parts) {extra}
GROUP BY domain
"""


def _share(seconds: float, total: float) -> float:
    return round(seconds / total, 4) if total > 0 else 0.0


def site_rows(
    ctx: Context, subjects: Sequence[str] | None, *, extra: str = "", params: Mapping[str, Any]
) -> list[dict[str, Any]]:
    """Site rows (domain, service or address), most time first."""
    scope, base = ctx.scope_sql(subjects)
    rows = list(ctx.session.execute(text(_SITES.format(extra=scope + extra)), {**base, **params}))
    categories = ctx.categories(row[0] for row in rows)
    total = sum(float(row[2]) for row in rows)
    found = []
    for domain, kind, seconds, raw, down, up, sessions, users, background in rows:
        found.append(
            {
                "domain": domain,
                "kind": kind,
                "category": categories.get(domain),
                "seconds": round(float(seconds)),
                "raw_seconds": int(raw),
                "share": _share(float(seconds), total),
                "download_bytes": int(down),
                "upload_bytes": int(up),
                "sessions": int(sessions),
                "users": int(users),
                "background": bool(background),
            }
        )
    found.sort(key=lambda row: (-row["seconds"], -row["download_bytes"], row["domain"]))
    return found


def category_rows(
    sites: Sequence[Mapping[str, Any]], users: Mapping[str, int]
) -> list[dict[str, Any]]:
    """Category rows from site rows (the share of the active time of the same scope)."""
    total = sum(row["seconds"] for row in sites)
    grouped: dict[str, dict[str, Any]] = {}
    for row in sites:
        name = row["category"] or UNKNOWN_CATEGORY
        entry = grouped.setdefault(
            name,
            {"category": name, "seconds": 0, "download_bytes": 0, "upload_bytes": 0},
        )
        entry["seconds"] += row["seconds"]
        entry["download_bytes"] += row["download_bytes"]
        entry["upload_bytes"] += row["upload_bytes"]
    found = [
        {**entry, "share": _share(entry["seconds"], total), "users": users.get(name, 0)}
        for name, entry in grouped.items()
    ]
    found.sort(key=lambda row: (-row["seconds"], -row["download_bytes"], row["category"]))
    return found


def category_users(ctx: Context, subjects: Sequence[str] | None) -> dict[str, int]:
    """Distinct subjects per category (with any traffic in it)."""
    scope, params = ctx.scope_sql(subjects)
    extra = "AND d.subject = ANY(:subjects)" if scope else ""
    rows = ctx.session.execute(
        text(
            "SELECT coalesce(c.category, :unknown), count(DISTINCT d.subject) "  # noqa: S608 - fixed SQL fragments
            "FROM usage_domain_daily d LEFT JOIN domain_categories c ON c.domain = d.domain "
            "WHERE d.day >= :first AND d.day <= :last AND d.off_hours = ANY(:parts) "
            f"{extra} GROUP BY 1"
        ),
        {**params, "unknown": UNKNOWN_CATEGORY},
    )
    return {str(name): int(count) for name, count in rows}


def app_rows(ctx: Context, subjects: Sequence[str] | None) -> list[dict[str, Any]]:
    scope, params = ctx.scope_sql(subjects)
    rows = ctx.session.execute(
        text(
            "SELECT app, max(appcat), sum(seconds), sum(bytes) FROM usage_app_daily "  # noqa: S608 - fixed SQL fragments
            "WHERE day >= :first AND day <= :last AND off_hours = ANY(:parts) "
            f"{scope} GROUP BY app"
        ),
        params,
    )
    found = [
        {"app": app, "appcat": appcat, "seconds": round(float(seconds)), "bytes": int(volume)}
        for app, appcat, seconds, volume in rows
    ]
    found.sort(key=lambda row: (-row["seconds"], -row["bytes"], row["app"]))
    return found


def top_sites_by_subject(
    ctx: Context, subjects: Sequence[str], *, domains: Sequence[str] | None = None
) -> dict[str, list[tuple[str, float]]]:
    """Per subject: ``[(domain, seconds)]`` with time, most first."""
    if not subjects:
        return {}
    extra = "" if domains is None else "AND domain = ANY(:domains)"
    params: dict[str, Any] = {**ctx.period.params(), "subjects": list(subjects)}
    if domains is not None:
        params["domains"] = list(domains)
    rows = ctx.session.execute(
        text(
            "SELECT subject, domain, sum(seconds) FROM usage_domain_daily "  # noqa: S608 - fixed SQL fragments
            "WHERE day >= :first AND day <= :last AND off_hours = ANY(:parts) "
            f"AND subject = ANY(:subjects) {extra} GROUP BY subject, domain"
        ),
        params,
    )
    found: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for subject, domain, seconds in rows:
        if float(seconds) > 0:
            found[subject].append((domain, float(seconds)))
    for items in found.values():
        items.sort(key=lambda item: (-item[1], item[0]))
    return found


# -- rows of the API ------------------------------------------------------------------------------


def subject_row(
    ctx: Context,
    key: str,
    metrics: Mapping[str, Any],
    sites: Sequence[tuple[str, float]],
    categories: Mapping[str, str],
) -> dict[str, Any]:
    by_category: dict[str, float] = defaultdict(float)
    for domain, seconds in sites:
        by_category[categories.get(domain, UNKNOWN_CATEGORY)] += seconds
    info = ctx.infos[key]
    return {
        "subject": ctx.ref(key),
        "department": info.department,
        "metrics": dict(metrics),
        "top_sites": [{"domain": d, "seconds": round(s)} for d, s in sites[:10]],
        "top_categories": [
            {"category": c, "seconds": round(s)}
            for c, s in sorted(by_category.items(), key=lambda i: (-i[1], i[0]))[:5]
        ],
    }


def subject_rows(
    ctx: Context, keys: Sequence[str], metrics: Mapping[str, Mapping[str, Any]]
) -> list[dict[str, Any]]:
    tops = top_sites_by_subject(ctx, keys)
    categories = ctx.categories(d for items in tops.values() for d, _ in items)
    return [subject_row(ctx, key, metrics[key], tops.get(key, []), categories) for key in keys]


def department_rows(ctx: Context, keys: Iterable[str] | None = None) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for key in ctx.stats if keys is None else keys:
        info, stats = ctx.infos[key], ctx.stats[key]
        name = info.department_name
        entry = grouped.setdefault(
            name,
            {
                "department": name,
                "source": "vlan",
                "active_users": 0,
                "metrics": empty_metrics(),
            },
        )
        if info.department_source == "person":
            entry["source"] = "person"
        if stats.metrics["active_seconds"] > 0:
            entry["active_users"] += 1
        add_metrics(entry["metrics"], stats.metrics)
    rows = list(grouped.values())
    rows.sort(key=lambda row: (-row["metrics"]["active_seconds"], row["department"]))
    return rows


def hour_points(ctx: Context, subjects: Sequence[str] | None) -> list[dict[str, Any]]:
    scope, params = ctx.scope_sql(subjects)
    rows = ctx.session.execute(
        text(
            "SELECT hour, count(DISTINCT subject) FILTER (WHERE active_seconds > 0), "  # noqa: S608 - fixed SQL fragments
            "sum(active_seconds), sum(download_bytes), sum(upload_bytes) FROM usage_hourly "
            "WHERE day >= :first AND day <= :last AND off_hours = ANY(:parts) "
            f"{scope} GROUP BY hour"
        ),
        params,
    )
    found = {
        hour: (int(users), int(active), int(down), int(up))
        for hour, users, active, down, up in rows
    }
    points = []
    for hour in ctx.period.hour_starts():
        users, active, down, up = found.get(hour, (0, 0, 0, 0))
        points.append(
            {
                "hour": hour,
                "active_users": users,
                "active_seconds": active,
                "download_bytes": down,
                "upload_bytes": up,
            }
        )
    return points


def company(ctx: Context) -> dict[str, Any]:
    """``GET /usage/company``."""
    metrics = empty_metrics()
    active_users = 0
    for stats in ctx.stats.values():
        add_metrics(metrics, stats.metrics)
        active_users += stats.metrics["active_seconds"] > 0
    sites = site_rows(ctx, None, params={})
    users = category_users(ctx, None)
    return {
        **envelope(ctx.session, ctx.period),
        "active_users": active_users,
        "metrics": metrics,
        "hourly": hour_points(ctx, None),
        "top_sites": [row for row in sites if row["seconds"] > 0][:10],
        "top_categories": [row for row in category_rows(sites, users) if row["seconds"] > 0][:10],
        "top_apps": app_rows(ctx, None)[:10],
        "departments": department_rows(ctx),
    }


def _matches(info: SubjectInfo, needle: str, viewer: Viewer) -> bool:
    if viewer.pseudonymize:
        if viewer.key is None:
            return False
        return needle in fold(pseudonym(viewer.key, info.key)[1])
    texts = [info.label, info.mac or "", info.ip or "", info.hostname or ""]
    return any(needle in fold(value) for value in texts)


def _sort_value(row: Mapping[str, Any], sort: str) -> Any:
    if sort == "label":
        return fold(row["subject"]["label"])
    return row["metrics"][sort]


def subjects_page(
    ctx: Context,
    *,
    kind: str,
    q: str | None,
    sort: str,
    order: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """``GET /usage/subjects``: persons, devices (with bare addresses) or departments."""
    scope = ctx.scope
    keys = [key for key in ctx.stats if scope is None or key in scope]
    if kind == "department":
        rows = department_rows(ctx, keys)
        if q:
            needle = fold(q.strip())
            rows = [row for row in rows if needle in fold(row["department"])]
        if sort == "label":
            rows.sort(key=lambda row: fold(row["department"]), reverse=order == "desc")
        else:
            rows.sort(key=lambda row: row["metrics"][sort], reverse=order == "desc")
        return {
            **envelope(ctx.session, ctx.period),
            "items": rows[offset : offset + limit],
            "total": len(rows),
            "limit": limit,
            "offset": offset,
        }
    kinds = {"person"} if kind == "person" else {"device", "ip"}
    selected = [key for key in keys if ctx.stats[key].kind in kinds and ctx.visible(key)]
    if q:
        needle = fold(q.strip())
        selected = [key for key in selected if _matches(ctx.infos[key], needle, ctx.viewer)]

    def sort_key(key: str) -> Any:
        if sort == "label":
            return fold(ctx.ref(key)["label"])
        return ctx.stats[key].metrics[sort]

    selected.sort(key=lambda key: fold(ctx.ref(key)["label"]))
    selected.sort(key=sort_key, reverse=order == "desc")
    page = selected[offset : offset + limit]
    items = subject_rows(ctx, page, {key: ctx.stats[key].metrics for key in page})
    return {
        **envelope(ctx.session, ctx.period),
        "items": items,
        "total": len(selected),
        "limit": limit,
        "offset": offset,
    }


# -- one subject ----------------------------------------------------------------------------------


class NotFoundError(LookupError):
    """No such subject in the period (or not visible to the viewer)."""


def find_subject(ctx: Context, key: str) -> str:
    """The real subject key of ``key`` (a pseudonym token for pseudonymous viewers)."""
    if ctx.viewer.pseudonymize:
        if not key.startswith(("p:", "d:")) or ctx.viewer.key is None:
            raise NotFoundError(key)
        wanted = key[2:]
        for candidate in ctx.stats:
            if token(ctx.viewer.key, candidate) == wanted and ctx.visible(candidate):
                return candidate
        raise NotFoundError(key)
    if key.startswith("device:"):
        key = "device:" + key.split(":", 1)[1].lower()
    if key not in ctx.stats or not ctx.visible(key):
        raise NotFoundError(key)
    return key


def _merge_timeline(
    runs: Iterable[Sequence[Any]], day: date, categories: Mapping[str, str]
) -> list[dict[str, Any]]:
    start_of_day = day_window(day)[0].astimezone(UTC)
    spans: list[list[Any]] = []
    for first, end, domain in sorted(runs, key=lambda r: (r[0], r[1])):
        category = categories.get(domain)
        if spans and spans[-1][1] == first and spans[-1][2] == category:
            spans[-1][1] = end
        else:
            spans.append([first, end, category])
    return [
        {
            "start": start_of_day + timedelta(minutes=first),
            "end": start_of_day + timedelta(minutes=end),
            "category": category,
        }
        for first, end, category in spans
    ]


def subject_detail(ctx: Context, key: str) -> dict[str, Any]:
    """``GET /usage/subjects/{key}`` (``key`` from :func:`find_subject`)."""
    session, period = ctx.session, ctx.period
    params = {**period.params(), "subject": key}
    stats = ctx.stats[key]
    info = ctx.infos[key]
    where = "day >= :first AND day <= :last AND off_hours = ANY(:parts) AND subject = :subject"
    # Sites with their host names.
    rows = list(
        session.execute(
            text(
                "SELECT domain, kind, seconds, raw_seconds, download_bytes, upload_bytes, "  # noqa: S608 - fixed SQL fragments
                f"sessions, background, hosts FROM usage_domain_daily WHERE {where}"
            ),
            params,
        )
    )
    sites: dict[str, dict[str, Any]] = {}
    hosts: dict[tuple[str, str], list[float]] = {}
    for domain, kind, seconds, raw, down, up, sessions, background, host_map in rows:
        entry = sites.setdefault(
            domain,
            {
                "domain": domain,
                "kind": kind,
                "seconds": 0.0,
                "raw_seconds": 0,
                "download_bytes": 0,
                "upload_bytes": 0,
                "sessions": 0,
                "background": True,
            },
        )
        entry["seconds"] += float(seconds)
        entry["raw_seconds"] += int(raw)
        entry["download_bytes"] += int(down)
        entry["upload_bytes"] += int(up)
        entry["sessions"] += int(sessions)
        entry["background"] = entry["background"] and bool(background)
        for hostname, (host_seconds, volume, count) in (host_map or {}).items():
            total = hosts.setdefault((hostname, domain), [0.0, 0, 0])
            total[0] += float(host_seconds)
            total[1] += int(volume)
            total[2] += int(count)
    categories = ctx.categories(sites)
    active = sum(entry["seconds"] for entry in sites.values())
    site_list = []
    for entry in sites.values():
        site_list.append(
            {
                **entry,
                "seconds": round(entry["seconds"]),
                "category": categories.get(entry["domain"]),
                "share": _share(entry["seconds"], active),
                "users": 1,
            }
        )
    site_list.sort(key=lambda row: (-row["seconds"], -row["download_bytes"], row["domain"]))
    category_list = category_rows(
        site_list, {row["category"] or UNKNOWN_CATEGORY: 1 for row in site_list}
    )
    hostnames: list[dict[str, Any]] = [
        {
            "hostname": hostname,
            "domain": domain,
            "seconds": round(values[0]),
            "bytes": int(values[1]),
            "sessions": int(values[2]),
        }
        for (hostname, domain), values in hosts.items()
    ]
    hostnames.sort(key=lambda row: (-row["seconds"], -row["bytes"], row["hostname"]))
    apps = app_rows(ctx, [key])
    # URLs (newest first), blocked attempts (newest first).
    urls = [
        {
            "url": url,
            "hostname": hostname,
            "hits": int(hits),
            "first_seen": first,
            "last_seen": last,
            "category": category,
            "blocked": bool(blocked),
        }
        for url, hostname, hits, first, last, category, blocked in session.execute(
            text(
                "SELECT url, hostname, sum(hits), min(first_seen), max(last_seen), "  # noqa: S608 - fixed SQL fragments
                "(array_agg(category ORDER BY last_seen DESC))[1], bool_or(blocked) "
                f"FROM usage_urls WHERE {where} GROUP BY url, hostname "
                "ORDER BY max(last_seen) DESC, url LIMIT 1000"
            ),
            params,
        )
    ]
    blocked = blocked_rows(ctx, [key], limit=5000)
    hourly = subject_hours(ctx, key)
    timeline: list[dict[str, Any]] = []
    if period.first == period.last:
        statement = text(f"SELECT timeline FROM usage_daily WHERE {where}")  # noqa: S608
        runs = [run for (value,) in session.execute(statement, params) for run in value]
        timeline = _merge_timeline(runs, period.first, ctx.categories(r[2] for r in runs))
    devices, per_device = _devices(ctx, key)
    return {
        **envelope(session, period),
        "subject": ctx.ref(key),
        "department": info.department,
        "devices": devices,
        "metrics": stats.metrics,
        "timeline": timeline,
        "sites": site_list,
        "categories": category_list,
        "apps": apps,
        "hostnames": hostnames,
        "urls": urls,
        "blocked": blocked,
        "hourly": hourly,
        "per_device": per_device,
        "heatmap": heatmap(ctx, key),
    }


def blocked_rows(
    ctx: Context, subjects: Sequence[str] | None, *, limit: int, newest_first: bool = True
) -> list[dict[str, Any]]:
    scope, params = ctx.scope_sql(subjects)
    order = "DESC" if newest_first else "ASC"
    rows = ctx.session.execute(
        text(
            "SELECT time, subject, domain, hostname, category, action, source, url, app, "  # noqa: S608 - fixed SQL fragments
            "mac::text FROM usage_blocked WHERE day >= :first AND day <= :last "
            f"AND off_hours = ANY(:parts) {scope} ORDER BY time {order}, id {order} LIMIT :limit"
        ),
        {**params, "limit": limit},
    )
    return [
        {
            "time": moment,
            "subject": subject,
            "domain": domain,
            "category": category,
            "action": action,
            "source": source,
            "hostname": hostname,
            "url": url,
            "app": app,
            "mac": mac,
        }
        for moment, subject, domain, hostname, category, action, source, url, app, mac in rows
    ]


def subject_hours(ctx: Context, key: str) -> list[dict[str, Any]]:
    rows = ctx.session.execute(
        text(
            "SELECT hour, online_seconds, active_seconds, download_bytes, upload_bytes, "
            "top_domain FROM usage_hourly WHERE day >= :first AND day <= :last "
            "AND off_hours = ANY(:parts) AND subject = :subject"
        ),
        {**ctx.period.params(), "subject": key},
    )
    found: dict[datetime, list[Any]] = {}
    for hour, online, active, down, up, top in rows:
        entry = found.setdefault(hour, [0, 0, 0, 0, None, -1])
        entry[0] += int(online)
        entry[1] += int(active)
        entry[2] += int(down)
        entry[3] += int(up)
        if top is not None and int(active) > entry[5]:
            entry[4], entry[5] = top, int(active)
    points = []
    for hour in ctx.period.hour_starts():
        online, active, down, up, top, _ = found.get(hour, [0, 0, 0, 0, None, -1])
        points.append(
            {
                "hour": hour,
                "active_seconds": active,
                "online_seconds": online,
                "download_bytes": down,
                "upload_bytes": up,
                "top_domain": top,
            }
        )
    return points


def heatmap(ctx: Context, key: str | None, *, days: int = 30) -> list[dict[str, Any]]:
    """Active minutes per hour for the ``days`` days ending at the period's last day."""
    first = ctx.period.last - timedelta(days=days - 1)
    params: dict[str, Any] = {"first": first, "last": ctx.period.last, "parts": ctx.period.parts}
    extra = ""
    if key is not None:
        extra, params["subject"] = "AND subject = :subject", key
    rows = ctx.session.execute(
        text(
            "SELECT day, hour, sum(active_seconds) FROM usage_hourly WHERE day >= :first "  # noqa: S608 - fixed SQL fragments
            f"AND day <= :last AND off_hours = ANY(:parts) {extra} GROUP BY day, hour"
        ),
        params,
    )
    found: dict[tuple[date, int], int] = {}
    for day, hour, active in rows:
        local = hour.astimezone(day_window(day)[0].tzinfo)
        found[(day, local.hour)] = found.get((day, local.hour), 0) + int(active) // 60
    return [
        {"day": day, "hour": hour, "active_minutes": found.get((day, hour), 0)}
        for day in (first + timedelta(days=n) for n in range(days))
        for hour in range(24)
    ]


def _devices(ctx: Context, key: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if ctx.stats[key].kind != "person":
        return [], []
    rows = list(
        ctx.session.execute(
            text(
                "SELECT mac::text, mode() WITHIN GROUP (ORDER BY host(ip)), sum(online_seconds), "
                "sum(active_seconds), sum(download_bytes), sum(upload_bytes), sum(sessions), "
                "sum(blocked), coalesce(sum(active_seconds) FILTER (WHERE off_hours), 0), "
                "min(first_activity), max(last_activity), jsonb_agg(top_domains) "
                "FROM usage_device_daily WHERE day >= :first AND day <= :last "
                "AND off_hours = ANY(:parts) AND subject = :subject GROUP BY mac"
            ),
            {**ctx.period.params(), "subject": key},
        )
    )
    devices, per_device = [], []
    for mac, ip, online, active, down, up, sessions, blocked, off, first, last, tops in sorted(
        rows
    ):
        ref = device_ref(ctx.directory, mac, ip, ctx.viewer)
        seconds: dict[str, float] = defaultdict(float)
        for day_top in tops or []:
            for domain, value in day_top:
                seconds[domain] += float(value)
        devices.append(ref)
        per_device.append(
            {
                "device": ref,
                "metrics": {
                    "online_seconds": int(online),
                    "active_seconds": int(active),
                    "download_bytes": int(down),
                    "upload_bytes": int(up),
                    "sessions": int(sessions),
                    "blocked": int(blocked),
                    "off_hours_active_seconds": int(off),
                    "first_activity": first,
                    "last_activity": last,
                },
                "top_sites": [
                    {"domain": d, "seconds": round(s)}
                    for d, s in sorted(seconds.items(), key=lambda i: (-i[1], i[0]))[:10]
                    if s > 0
                ],
            }
        )
    return devices, per_device


# -- sites and categories -------------------------------------------------------------------------


def sites_page(
    ctx: Context,
    *,
    q: str | None,
    category: str | None,
    sort: str,
    order: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    """``GET /usage/sites``."""
    rows = site_rows(ctx, ctx.scope, params={})
    if q:
        needle = fold(q.strip())
        rows = [row for row in rows if needle in fold(row["domain"])]
    if category is not None:
        rows = [row for row in rows if (row["category"] or UNKNOWN_CATEGORY) == category]
    if sort == "domain":
        rows.sort(key=lambda row: row["domain"], reverse=order == "desc")
    elif sort != "seconds" or order != "desc":
        rows.sort(key=lambda row: row["domain"])
        rows.sort(key=lambda row: row[sort], reverse=order == "desc")
    return {
        **envelope(ctx.session, ctx.period),
        "items": rows[offset : offset + limit],
        "total": len(rows),
        "limit": limit,
        "offset": offset,
    }


_USERS = """
SELECT subject, sum(seconds), sum(raw_seconds), sum(download_bytes), sum(upload_bytes),
       sum(sessions), coalesce(sum(seconds) FILTER (WHERE off_hours), 0)
FROM usage_domain_daily
WHERE day >= :first AND day <= :last AND off_hours = ANY(:parts) {where}
GROUP BY subject
"""
_USER_SITES = """
SELECT subject, domain, seconds FROM (
    SELECT subject, domain, sum(seconds) AS seconds,
           row_number() OVER (PARTITION BY subject ORDER BY sum(seconds) DESC, domain) AS rank
    FROM usage_domain_daily
    WHERE day >= :first AND day <= :last AND off_hours = ANY(:parts) {where}
          AND subject = ANY(:shown)
    GROUP BY subject, domain
) ranked
WHERE rank <= 10 AND seconds > 0
"""


def _restricted_users(
    ctx: Context,
    where: str,
    params: Mapping[str, Any],
    categories: Mapping[str, str],
    *,
    raw_is_online: bool,
    limit: int | None,
) -> tuple[list[dict[str, Any]], int]:
    """Subject rows whose metrics are those of a site or category only, and how many there are.

    ``where`` selects the domains (``AND domain …``). ``active_seconds`` is the time on them,
    ``off_hours_active_seconds`` its part off hours; ``online_seconds`` is the raw time of one
    site (``raw_is_online``: every minute with its traffic) and 0 for a category — the rollups do
    not keep the minutes with traffic on any of its sites, and the sum of their raw times would
    count a minute once per site. ``blocked`` 0 and no first/last activity: neither is kept per
    site. Most time first; ``limit`` rows at most (``None``: all of them).
    """
    scope, base = ctx.scope_sql(ctx.scope)
    values = {**base, **params}
    per: dict[str, dict[str, Any]] = {}
    statement = text(_USERS.format(where=where + " " + scope))
    for subject, seconds, raw, down, up, sessions, off in ctx.session.execute(statement, values):
        if not ctx.visible(subject):
            continue
        metrics = empty_metrics()
        metrics["online_seconds"] = int(raw) if raw_is_online else 0
        metrics["active_seconds"] = round(float(seconds))
        metrics["download_bytes"] = int(down)
        metrics["upload_bytes"] = int(up)
        metrics["sessions"] = int(sessions)
        metrics["off_hours_active_seconds"] = round(float(off))
        per[subject] = metrics
    keys = sorted(per, key=lambda k: (-per[k]["active_seconds"], -per[k]["download_bytes"], k))
    shown = keys if limit is None else keys[:limit]
    tops: dict[str, list[tuple[str, float]]] = defaultdict(list)
    if shown:
        statement = text(_USER_SITES.format(where=where + " " + scope))
        for subject, domain, seconds in ctx.session.execute(statement, {**values, "shown": shown}):
            tops[subject].append((domain, float(seconds)))
    found = []
    for key in shown:
        sites = sorted(tops[key], key=lambda item: (-item[1], item[0]))
        found.append(subject_row(ctx, key, per[key], sites, categories))
    return found, len(keys)


def site_detail(ctx: Context, domain: str, *, capped: bool = True) -> dict[str, Any] | None:
    """``GET /usage/sites/{domain}`` (``None`` when the domain has no rows).

    ``capped``: at most :data:`HOSTNAMES_MAX` host names and :data:`USERS_MAX` users, most time
    first (``users_total`` counts them all); the exports pass ``False`` and get every one.
    """
    scope, params = ctx.scope_sql(ctx.scope)
    params = {**params, "domain": domain}
    rows = list(
        ctx.session.execute(
            text(
                "SELECT hosts FROM usage_domain_daily WHERE day >= :first AND day <= :last "  # noqa: S608 - fixed SQL fragments
                f"AND off_hours = ANY(:parts) AND domain = :domain {scope}"
            ),
            params,
        )
    )
    if not rows:
        return None
    totals = site_rows(ctx, ctx.scope, extra=" AND domain = :domain", params={"domain": domain})
    hosts: dict[str, list[float]] = {}
    for (host_map,) in rows:
        for hostname, (seconds, volume, count) in (host_map or {}).items():
            entry = hosts.setdefault(hostname, [0.0, 0, 0])
            entry[0] += float(seconds)
            entry[1] += int(volume)
            entry[2] += int(count)
    total = totals[0]
    total["share"] = _share(total["seconds"], _active_total(ctx))
    categories = ctx.categories([domain])
    hostnames = sorted(hosts.items(), key=lambda i: (-i[1][0], -i[1][1], i[0]))
    users, users_total = _restricted_users(
        ctx,
        "AND domain = :domain",
        {"domain": domain},
        categories,
        raw_is_online=True,
        limit=USERS_MAX if capped else None,
    )
    return {
        **envelope(ctx.session, ctx.period),
        "domain": domain,
        "category": categories.get(domain),
        "totals": total,
        "hostnames": [
            {"hostname": h, "seconds": round(v[0]), "bytes": int(v[1]), "sessions": int(v[2])}
            for h, v in (hostnames[:HOSTNAMES_MAX] if capped else hostnames)
        ],
        "users": users,
        "users_total": users_total,
    }


def _active_total(ctx: Context) -> float:
    scope = ctx.scope
    return float(
        sum(
            stats.metrics["active_seconds"]
            for key, stats in ctx.stats.items()
            if scope is None or key in scope
        )
    )


def categories_list(ctx: Context) -> dict[str, Any]:
    """``GET /usage/categories``."""
    sites = site_rows(ctx, ctx.scope, params={})
    return {
        **envelope(ctx.session, ctx.period),
        "items": category_rows(sites, category_users(ctx, ctx.scope)),
    }


def category_detail(ctx: Context, category: str, *, capped: bool = True) -> dict[str, Any] | None:
    """``GET /usage/categories/{category}`` (``None`` when nothing is in it).

    ``capped``: at most :data:`CATEGORY_SITES_MAX` sites and :data:`USERS_MAX` users, most time
    first (``sites_total`` and ``users_total`` count them all); the exports pass ``False``.
    """
    sites = site_rows(ctx, ctx.scope, params={})
    rows = category_rows(sites, category_users(ctx, ctx.scope))
    totals = next((row for row in rows if row["category"] == category), None)
    if totals is None:
        return None
    in_category = [row for row in sites if (row["category"] or UNKNOWN_CATEGORY) == category]
    domains = [row["domain"] for row in in_category]
    users, users_total = _restricted_users(
        ctx,
        "AND domain = ANY(:domains)",
        {"domains": domains},
        dict.fromkeys(domains, category),
        raw_is_online=False,
        limit=USERS_MAX if capped else None,
    )
    return {
        **envelope(ctx.session, ctx.period),
        "category": category,
        "totals": totals,
        "sites": in_category[:CATEGORY_SITES_MAX] if capped else in_category,
        "sites_total": len(in_category),
        "users": users,
        "users_total": users_total,
    }
