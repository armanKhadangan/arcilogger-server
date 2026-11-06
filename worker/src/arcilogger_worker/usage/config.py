"""The ``usage.*`` settings as the engine uses them (defaults: ``arcilogger_db.settings``).

:class:`UsageSettings` is read once per run (:meth:`UsageSettings.load`); values an
administrator stored are already validated (``arcilogger_db.settings.validate_setting``), but
the engine still falls back to the shipped default for anything it cannot use, so a damaged
row never stops the rollups. :meth:`UsageSettings.fingerprint` is the part of the settings a
day's rollup depends on: when it changes, the recent days are rebuilt (:mod:`.jobs`).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal

from sqlalchemy.orm import Session

from arcilogger_core.timeutil import day_window, to_site
from arcilogger_db.settings import DEFAULT_SETTINGS, get_settings, validate_setting

#: Label of a domain without a category (spec 10.2).
UNKNOWN_CATEGORY: Final = "Bilinmiyor"
_HHMM = re.compile(r"^([01][0-9]|2[0-3]):([0-5][0-9])$")
_HOLIDAY = re.compile(r"^(?:(\d{4})-)?(\d{2})-(\d{2})$")


def _int(values: Mapping[str, Any], key: str, low: int = 0) -> int:
    value = values.get(key)
    if type(value) is int and value >= low:
        return value
    return int(DEFAULT_SETTINGS[key])


def _strings(values: Mapping[str, Any], key: str) -> tuple[str, ...]:
    value = values.get(key)
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return tuple(value)
    return tuple(DEFAULT_SETTINGS[key])


def _minutes(text: str) -> int | None:
    match = _HHMM.match(text)
    return None if match is None else int(match.group(1)) * 60 + int(match.group(2))


#: The part of the day a minute belongs to (contract W5 §6 K2): work hours, a break inside them
#: ("mola"), or off hours (outside them, a day off or a holiday).
Part = Literal["work", "break", "off"]
PARTS: Final[tuple[Part, ...]] = ("work", "break", "off")
#: Breaks of a day: ``[start, end)`` in minutes after local midnight, sorted.
Breaks = tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class DayHours:
    """The hours of one work day: ``[start, end)`` in minutes after local midnight, and breaks."""

    start: int
    end: int
    breaks: Breaks = ()

    def part(self, minute: int) -> Part:
        if not self.start <= minute < self.end:
            return "off"
        return "break" if any(first <= minute < last for first, last in self.breaks) else "work"


@dataclass(frozen=True)
class WorkHours:
    """Work hours: ISO weekdays, ``[start, end)`` in minutes after local midnight, the breaks
    and the weekdays with their own hours (``by_day``, sorted by weekday)."""

    days: frozenset[int]
    start: int
    end: int
    breaks: Breaks = ()
    by_day: tuple[tuple[int, DayHours], ...] = ()

    @classmethod
    def parse(cls, value: Any) -> WorkHours:
        """The stored value, checked by the server's own rules; the shipped default if it
        breaks one (a damaged row never stops the rollups)."""
        for candidate in (value, DEFAULT_SETTINGS["usage.work_hours"]):
            if not isinstance(candidate, dict):
                continue
            try:
                checked = validate_setting("usage.work_hours", candidate)
            except ValueError:
                continue
            by_day = tuple(
                (int(weekday), _day_hours(hours))
                for weekday, hours in checked.get("by_day", {}).items()
            )
            general = _day_hours(checked)
            return cls(
                frozenset(checked["days"]), general.start, general.end, general.breaks, by_day
            )
        raise AssertionError("the shipped usage.work_hours default is invalid")  # pragma: no cover

    def hours(self, weekday: int) -> DayHours | None:
        """The hours of ISO weekday ``weekday``; ``None`` on a day off."""
        if weekday not in self.days:
            return None
        for day, hours in self.by_day:
            if day == weekday:
                return hours
        return DayHours(self.start, self.end, self.breaks)

    def fingerprint(self) -> list[Any]:
        """For :meth:`UsageSettings.fingerprint`: unchanged for a value without breaks and
        ``by_day``, so an upgrade alone rebuilds no day."""
        result: list[Any] = [sorted(self.days), self.start, self.end]
        if self.breaks or self.by_day:
            result.append([list(item) for item in self.breaks])
            result.append(
                [
                    [day, hours.start, hours.end, [list(item) for item in hours.breaks]]
                    for day, hours in self.by_day
                ]
            )
        return result


def _day_hours(value: Mapping[str, Any]) -> DayHours:
    breaks = tuple(
        (_minutes(item["start"]) or 0, _minutes(item["end"]) or 0)
        for item in value.get("breaks", [])
    )
    return DayHours(_minutes(value["start"]) or 0, _minutes(value["end"]) or 0, breaks)


@dataclass(frozen=True)
class Holidays:
    """Every-year dates (``MM-DD``) and single dates (``YYYY-MM-DD``)."""

    yearly: frozenset[tuple[int, int]]
    dates: frozenset[date]

    @classmethod
    def parse(cls, value: Any) -> Holidays:
        items = value if isinstance(value, list) else DEFAULT_SETTINGS["usage.holidays"]
        yearly: set[tuple[int, int]] = set()
        dates: set[date] = set()
        for item in items:
            match = _HOLIDAY.match(item) if isinstance(item, str) else None
            if match is None:
                continue
            month, day = int(match.group(2)), int(match.group(3))
            try:
                if match.group(1):
                    dates.add(date(int(match.group(1)), month, day))
                else:
                    date(2000, month, day)
                    yearly.add((month, day))
            except ValueError:
                continue
        return cls(frozenset(yearly), frozenset(dates))

    def __contains__(self, day: object) -> bool:
        return isinstance(day, date) and (day in self.dates or (day.month, day.day) in self.yearly)


DEFAULT_WORK_HOURS: Final = WorkHours.parse(None)
DEFAULT_HOLIDAYS: Final = Holidays.parse(None)


@dataclass(frozen=True)
class UsageSettings:
    active_bytes_threshold: int = 2048
    background_session_bytes: int = 2048
    dns_window_seconds: int = 600
    session_overlap_seconds: int = 1800
    background_domains: tuple[str, ...] = tuple(DEFAULT_SETTINGS["usage.background_domains"])
    background_appcats: tuple[str, ...] = tuple(DEFAULT_SETTINGS["usage.background_appcats"])
    background_ports: tuple[int, ...] = tuple(DEFAULT_SETTINGS["usage.background_ports"])
    work_hours: WorkHours = DEFAULT_WORK_HOURS
    holidays: Holidays = DEFAULT_HOLIDAYS
    psl_file: str = ""
    psl_private_suffixes: bool = False
    auto_rebuild_days: int = 7
    retention_days: int = 400
    urls_per_day: int = 1000
    blocked_per_day: int = 5000
    pseudonymize_anonymous_role: bool = False
    change_min_percent: int = 50
    change_min_seconds: int = 3600
    change_min_bytes: int = 1073741824

    @classmethod
    def from_values(cls, values: Mapping[str, Any]) -> UsageSettings:
        ports = values.get("usage.background_ports")
        if not (isinstance(ports, list) and all(type(p) is int for p in ports)):
            ports = DEFAULT_SETTINGS["usage.background_ports"]
        psl_file = values.get("usage.psl_file")
        return cls(
            active_bytes_threshold=_int(values, "usage.active_bytes_threshold", 1),
            background_session_bytes=_int(values, "usage.background_session_bytes"),
            dns_window_seconds=_int(values, "usage.dns_window_seconds"),
            session_overlap_seconds=_int(values, "usage.session_overlap_seconds"),
            background_domains=tuple(
                p.lower() for p in _strings(values, "usage.background_domains")
            ),
            background_appcats=_strings(values, "usage.background_appcats"),
            background_ports=tuple(ports),
            work_hours=WorkHours.parse(values.get("usage.work_hours")),
            holidays=Holidays.parse(values.get("usage.holidays")),
            psl_file=psl_file.strip() if isinstance(psl_file, str) else "",
            psl_private_suffixes=values.get("usage.psl_private_suffixes") is True,
            auto_rebuild_days=_int(values, "usage.auto_rebuild_days"),
            retention_days=_int(values, "usage.retention_days", 1),
            urls_per_day=_int(values, "usage.urls_per_day"),
            blocked_per_day=_int(values, "usage.blocked_per_day"),
            pseudonymize_anonymous_role=values.get("usage.pseudonymize_anonymous_role") is True,
            change_min_percent=_int(values, "usage.change_min_percent", 1),
            change_min_seconds=_int(values, "usage.change_min_seconds"),
            change_min_bytes=_int(values, "usage.change_min_bytes"),
        )

    @classmethod
    def load(cls, session: Session) -> UsageSettings:
        return cls.from_values(get_settings(session, prefix="usage."))

    # -- work hours -------------------------------------------------------------------------------

    def part_of(self, moment: datetime) -> Part:
        """The part of the day of ``moment`` (site time): ``work``, ``break`` or ``off``."""
        local = to_site(moment)
        hours = None if local.date() in self.holidays else self.work_hours.hours(local.isoweekday())
        return "off" if hours is None else hours.part(local.hour * 60 + local.minute)

    def is_off_hours(self, moment: datetime) -> bool:
        """Outside the work hours (or on a holiday) at ``moment`` (site time); a break is not."""
        return self.part_of(moment) == "off"

    def has_breaks(self, days: Iterable[date]) -> bool:
        """Whether a work day among ``days`` (not a holiday) has a break."""
        for day in days:
            hours = None if day in self.holidays else self.work_hours.hours(day.isoweekday())
            if hours is not None and hours.breaks:
                return True
        return False

    def day_parts(self, day: date) -> list[Part]:
        """For every minute of the site day ``day``: its part (:data:`PARTS`)."""
        start, end = day_window(day)
        count = int((end - start).total_seconds() // 60)
        return [self.part_of(start + timedelta(minutes=k)) for k in range(count)]

    def fingerprint(self) -> dict[str, Any]:
        """What a day's rollup depends on (the reporting thresholds are left out)."""
        return {
            "active_bytes_threshold": self.active_bytes_threshold,
            "background_session_bytes": self.background_session_bytes,
            "dns_window_seconds": self.dns_window_seconds,
            "session_overlap_seconds": self.session_overlap_seconds,
            "background_domains": list(self.background_domains),
            "background_appcats": list(self.background_appcats),
            "background_ports": list(self.background_ports),
            "work_hours": self.work_hours.fingerprint(),
            "holidays": sorted(f"{m:02d}-{d:02d}" for m, d in self.holidays.yearly)
            + sorted(day.isoformat() for day in self.holidays.dates),
            "psl_file": self.psl_file,
            "psl_private_suffixes": self.psl_private_suffixes,
            "urls_per_day": self.urls_per_day,
            "blocked_per_day": self.blocked_per_day,
        }
