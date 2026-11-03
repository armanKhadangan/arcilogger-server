"""The lookup's days fit one archive reload (Wave 5 contract §5, G5 and G7; W5-G deviations).

With the shipped settings the days of a lookup (:func:`arcilogger_worker.lookup.days.lookup_days`)
reach back as far as one reload holds, whatever the time of day: the 409
``archive_reload_required`` of an old moment names days that ``POST /archive-reloads`` accepts
(never ``archive_reload_too_large``), and the worker's reach is the API's limit.
"""

from __future__ import annotations

from datetime import date, timedelta

from arcilogger_api.archive.service import MAX_DAYS
from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_worker.lookup.days import RELOAD_DAYS, lookup_days
from arcilogger_worker.lookup.types import LookupRequest, LookupSettings
from arcilogger_worker.testing import tr

DAY = date(2026, 10, 12)


def test_the_reach_of_a_logged_lease_is_the_largest_reload() -> None:
    assert RELOAD_DAYS == MAX_DAYS


def test_the_days_of_the_shipped_settings_fit_one_reload_at_any_time_of_day() -> None:
    settings = LookupSettings.from_settings(dict(DEFAULT_SETTINGS))
    assert settings.use_logged_lease
    # Every 15 minutes, and the last two minutes before midnight (the days are then T's day - 11
    # to + 2: the sessions reach into the day after next).
    moments = [tr(DAY, 0) + timedelta(minutes=minutes) for minutes in range(0, 24 * 60, 15)]
    moments += [tr(DAY, 23, 58, 30), tr(DAY, 23, 59, 59)]
    shifted = set()
    for at in moments:
        for request in (
            LookupRequest(at=at, internal_ip="172.20.42.57"),
            LookupRequest(at=at, public_ip="198.51.100.20", public_port=51001, protocol=6),
        ):
            first, last = lookup_days(request, settings)
            assert (last - first).days + 1 == MAX_DAYS, at
            if last == DAY + timedelta(days=2):
                shifted.add(at)
    assert shifted == {tr(DAY, 23, 58, 30), tr(DAY, 23, 59, 59)}
