"""Content-filter evidence of the FortiGate sources (Wave 4 contract 2.4): card 7 of Uyum Durumu
and, from W4-F on, the ``R20-filter`` check. Imports nothing from ``arcilogger_api``.

* **Counts** per site day come from ``search_counts`` (``fg_web`` and ``fg_dns`` lines of the
  enabled, configured FortiGate sources over the last 8 days, index ``(source, hour)``); the hours
  are whole UTC hours, so each falls into exactly one site day.
* **The newest record** is ``max(time)`` of each table within 8 days (time index), and the web
  filter profiles are those of the newest 10 000 ``fg_web`` rows of the last 24 hours. FortiGate
  DNS filter records carry no profile name.
* A site day is **expected** when its ISO weekday is in ``alerts.filter_days`` and it is not a day
  of ``usage.holidays`` while ``alerts.filter_skip_holidays`` is on (owner question Q10: every
  day of the week, the listed holidays not). The **window** is the last
  ``alerts.filter_quiet_hours`` hours; the alert is due only when every site day the window
  touches is expected and neither kind of record arrived in it.

Values an administrator stored are already validated (``arcilogger_db.settings``); a damaged row
falls back to the shipped default, as the usage settings do.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import day_window, require_aware, site_day
from arcilogger_db.models import FgDns, FgWeb, SearchCount, Source
from arcilogger_db.settings import DEFAULT_SETTINGS, INTEGER_RANGES, get_settings
from arcilogger_worker.usage.config import Holidays

DAYS_KEY: Final = "alerts.filter_days"
SKIP_HOLIDAYS_KEY: Final = "alerts.filter_skip_holidays"
QUIET_HOURS_KEY: Final = "alerts.filter_quiet_hours"
HOLIDAYS_KEY: Final = "usage.holidays"
#: The typed tables of the content filter and the kind each one is (``silent_kinds``).
TABLES: Final[Mapping[str, str]] = {"fg_web": "web", "fg_dns": "dns"}
#: How far back counts and the newest record are looked for.
LOOKBACK: Final = timedelta(days=8)
#: Site days reported (today first).
SHOWN_DAYS: Final = 7
#: The web filter profiles: the newest this many rows of this period.
PROFILE_ROWS: Final = 10_000
PROFILE_PERIOD: Final = timedelta(hours=24)

_PROFILES = text(
    "SELECT DISTINCT profile FROM (SELECT profile FROM fg_web WHERE time >= :since "
    "ORDER BY time DESC LIMIT :rows) AS newest WHERE profile IS NOT NULL ORDER BY profile"
)


@dataclass(frozen=True)
class FilterEvidence:
    applies: bool  # an enabled, non-unknown source of type "fortigate" exists
    last_at: datetime | None  # newest `time` of fg_web or fg_dns within 8 days
    today: Mapping[str, int]  # {"fg_web": n, "fg_dns": n}, the site day so far
    days: Sequence[tuple[date, int, int]]  # last 7 site days: (day, web, dns), newest first
    web_profiles: Sequence[str]  # distinct profiles of the newest 10 000 fg_web rows of 24 h
    window_hours: int  # alerts.filter_quiet_hours
    window_expected: bool  # every site day touched by [now - window, now] is expected
    alert_due: bool  # applies and no record in the window and window_expected
    silent_kinds: Sequence[str]  # "web"/"dns" seen in the 7 days but not in the window


@dataclass(frozen=True)
class FilterSettings:
    """The three ``alerts.filter_*`` settings and the holiday list, as the evidence uses them
    (also what card 7 shows as its expected days)."""

    days: tuple[int, ...]  # ISO weekdays, sorted
    skip_holidays: bool
    quiet_hours: int
    holidays: Holidays

    @classmethod
    def from_values(cls, values: Mapping[str, Any]) -> FilterSettings:
        days = values.get(DAYS_KEY)
        if not (isinstance(days, list) and all(type(d) is int and 1 <= d <= 7 for d in days)):
            days = DEFAULT_SETTINGS[DAYS_KEY]
        skip = values.get(SKIP_HOLIDAYS_KEY)
        hours = values.get(QUIET_HOURS_KEY)
        low, high = INTEGER_RANGES[QUIET_HOURS_KEY]
        if type(hours) is not int or hours < low or (high is not None and hours > high):
            hours = DEFAULT_SETTINGS[QUIET_HOURS_KEY]
        return cls(
            days=tuple(sorted(set(days))),
            skip_holidays=skip if isinstance(skip, bool) else DEFAULT_SETTINGS[SKIP_HOLIDAYS_KEY],
            quiet_hours=hours,
            holidays=Holidays.parse(values.get(HOLIDAYS_KEY)),
        )

    @classmethod
    def load(cls, session: Session) -> FilterSettings:
        keys = (DAYS_KEY, SKIP_HOLIDAYS_KEY, QUIET_HOURS_KEY, HOLIDAYS_KEY)
        return cls.from_values(get_settings(session, keys=keys))

    def expected(self, day: date) -> bool:
        """Whether content-filter records are expected on site day ``day``."""
        if day.isoweekday() not in self.days:
            return False
        return not (self.skip_holidays and day in self.holidays)


def fortigate_sources(session: Session) -> list[str]:
    """Names of the enabled, configured (not ``unknown-<ip>``) FortiGate sources."""
    return list(
        session.scalars(
            select(Source.name)
            .where(
                Source.type == "fortigate",
                Source.enabled.is_(True),
                Source.unknown.is_(False),
            )
            .order_by(Source.name)
        )
    )


def _daily_counts(
    session: Session, sources: Sequence[str], since: datetime
) -> dict[date, dict[str, int]]:
    """``{site day: {table: lines}}`` of the two filter tables from ``search_counts``."""
    counts: dict[date, dict[str, int]] = {}
    if not sources:
        return counts
    rows = session.execute(
        select(SearchCount.hour, SearchCount.tbl, func.sum(SearchCount.n))
        .where(
            SearchCount.source.in_(sources),
            SearchCount.hour >= since,
            SearchCount.tbl.in_(list(TABLES)),
        )
        .group_by(SearchCount.hour, SearchCount.tbl)
    )
    for hour, table, lines in rows:
        day = counts.setdefault(site_day(hour), dict.fromkeys(TABLES, 0))
        day[table] += int(lines)
    return counts


def filter_evidence(session: Session, *, now: datetime) -> FilterEvidence:
    """The content-filter evidence at ``now`` (read only; the caller owns the transaction)."""
    require_aware(now)
    settings = FilterSettings.load(session)
    sources = fortigate_sources(session)
    since = now - LOOKBACK
    today = site_day(now)
    counts = _daily_counts(session, sources, since)
    shown = [today - timedelta(days=back) for back in range(SHOWN_DAYS)]
    days = [
        (day, counts.get(day, {}).get("fg_web", 0), counts.get(day, {}).get("fg_dns", 0))
        for day in shown
    ]
    newest = {
        table: session.scalar(select(func.max(model.time)).where(model.time >= since))
        for table, model in (("fg_web", FgWeb), ("fg_dns", FgDns))
    }
    seen = [moment for moment in newest.values() if moment is not None]
    last_at = max(seen) if seen else None
    profiles = session.scalars(
        _PROFILES, {"since": now - PROFILE_PERIOD, "rows": PROFILE_ROWS}
    ).all()

    window_start = now - timedelta(hours=settings.quiet_hours)
    first = site_day(window_start)
    touched = [first + timedelta(days=n) for n in range((today - first).days + 1)]
    window_expected = all(settings.expected(day) for day in touched)
    in_window = last_at is not None and last_at >= window_start
    seven_days = day_window(shown[-1])[0]
    silent = [
        TABLES[table]
        for table, moment in newest.items()
        if moment is not None and seven_days <= moment < window_start
    ]
    return FilterEvidence(
        applies=bool(sources),
        last_at=last_at,
        today={"fg_web": days[0][1], "fg_dns": days[0][2]},
        days=tuple(days),
        web_profiles=tuple(profiles),
        window_hours=settings.quiet_hours,
        window_expected=window_expected,
        alert_due=bool(sources) and not in_window and window_expected,
        silent_kinds=tuple(silent),
    )
