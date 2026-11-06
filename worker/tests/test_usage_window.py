"""The usage engine over a range instead of a site day (W5-M, Wave 5 contract §6.2 M2): the
``window`` of :class:`~arcilogger_worker.usage.engine.DayBuilder`. Canlı Durum's last 60 minutes
can cross midnight; each minute then takes its part (K2) from its own day's hours and breaks."""

from __future__ import annotations

from datetime import UTC, date

from arcilogger_core.timeutil import day_window
from arcilogger_worker.testing import tr
from arcilogger_worker.usage.config import UsageSettings
from arcilogger_worker.usage.domains import BackgroundRules, DomainNamer
from arcilogger_worker.usage.engine import DayBuilder

WEDNESDAY, THURSDAY = date(2026, 10, 14), date(2026, 10, 15)


def builder(settings: UsageSettings, day: date, **kwargs: object) -> DayBuilder:
    namer = DomainNamer(settings.psl_file, settings.psl_private_suffixes)
    rules = BackgroundRules.create(
        settings.background_domains, settings.background_appcats, settings.background_ports
    )
    return DayBuilder(day, settings, namer, rules, **kwargs)  # type: ignore[arg-type]


def test_a_window_across_midnight_takes_each_days_own_hours() -> None:
    """23:30-00:30 into a Thursday whose hours (``by_day``) start at midnight: the half hour
    before midnight is Wednesday's off hours, the half hour after it Thursday's work."""
    settings = UsageSettings.from_values(
        {
            "usage.work_hours": {
                "days": [1, 2, 3, 4, 5],
                "start": "08:00",
                "end": "18:00",
                "by_day": {"4": {"start": "00:00", "end": "06:00"}},
            }
        }
    )
    lo, hi = tr(WEDNESDAY, 23, 30).astimezone(UTC), tr(THURSDAY, 0, 30).astimezone(UTC)
    window = builder(settings, THURSDAY, window=(lo, hi))
    assert (window.lo, window.hi, window.buckets) == (lo, hi, 60)
    assert window.parts == ["off"] * 30 + ["work"] * 30
    assert window.part(tr(THURSDAY, 0, 15).timestamp()) == "work"
    assert window.part(tr(WEDNESDAY, 23, 45).timestamp()) == "off"


def test_a_window_of_a_whole_day_is_the_day() -> None:
    """The same buckets and parts as the site day itself (with a break, for the K2 parts)."""
    settings = UsageSettings.from_values(
        {
            "usage.work_hours": {
                "days": [1, 2, 3, 4, 5],
                "start": "08:00",
                "end": "18:00",
                "breaks": [{"start": "12:30", "end": "13:30"}],
            }
        }
    )
    lo, hi = day_window(WEDNESDAY)
    day = builder(settings, WEDNESDAY)
    window = builder(settings, WEDNESDAY, window=(lo, hi))
    assert (window.lo, window.hi, window.buckets) == (day.lo, day.hi, day.buckets)
    assert window.parts == day.parts
