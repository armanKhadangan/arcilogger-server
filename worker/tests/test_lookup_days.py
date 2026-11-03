"""The raw days a 5651 lookup reads (Wave 5 contract §5, G7: ``lookup.days``).

The days follow the time ranges of the engine's steps: the sessions (± tolerance, the longest
session, the log slack), the domain's DNS look-back and the lease look-back of the DHCP rules (the
D8 probe's window: the longest lease timeout, at least FortiOS's default lease), never fewer than
the asked day ± 1. A test pins each part to the constant of the step it mirrors, so a wider step
fails here instead of letting a lookup read a day the API did not check.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from arcilogger_core.timeutil import day_window, site_day
from arcilogger_worker.correlation.timeline import TimelineRules
from arcilogger_worker.lookup.attribution import UNCONVERTED_WINDOW
from arcilogger_worker.lookup.days import DAYS_AROUND, dhcp_lookback, lookup_days, lookup_range
from arcilogger_worker.lookup.engine import DEFAULT_DNS_LOOKBACK_SECONDS
from arcilogger_worker.lookup.sessions import LOG_SLACK
from arcilogger_worker.lookup.types import LookupRequest, LookupSettings
from arcilogger_worker.testing import tr

DAY = date(2026, 10, 12)


def request(
    *, tolerance_seconds: int | None = None, dest_domain: str | None = None
) -> LookupRequest:
    return LookupRequest(
        at=tr(DAY, 11, 23, 41),
        internal_ip="172.20.42.57",
        tolerance_seconds=tolerance_seconds,
        dest_domain=dest_domain,
    )


def test_the_defaults_read_eight_days_back_and_one_ahead() -> None:
    settings = LookupSettings()
    assert lookup_days(request(), settings) == (DAY - timedelta(days=8), DAY + timedelta(days=1))


def test_the_dhcp_look_back_is_the_d8_window_at_least() -> None:
    settings = LookupSettings()
    assert timedelta(seconds=604_800) == UNCONVERTED_WINDOW  # FortiOS's default lease
    assert dhcp_lookback(settings) == UNCONVERTED_WINDOW
    longer = LookupSettings(lease_server_seconds={"dhcp42": 1_209_600})  # two weeks
    rules = TimelineRules.from_settings(longer)
    assert dhcp_lookback(longer) == timedelta(seconds=rules.longest_window)
    assert rules.longest_window == 1_209_600 * longer.lease_timeout_percent // 100
    first, last = lookup_days(request(), longer)
    assert first == site_day(
        request().at
        - timedelta(seconds=longer.tolerance_seconds + longer.max_session_seconds)
        - LOG_SLACK
        - timedelta(seconds=rules.longest_window)
    )
    assert last == DAY + timedelta(days=1)


def test_the_range_is_the_widest_step() -> None:
    settings = LookupSettings(tolerance_seconds=60, max_session_seconds=86_400)
    asked = request()
    earliest, latest = lookup_range(asked, settings)
    session = timedelta(seconds=60 + 86_400)
    assert earliest == asked.at - session - LOG_SLACK - UNCONVERTED_WINDOW
    assert latest == asked.at + session + LOG_SLACK


def test_the_request_tolerance_and_the_longest_session_widen_the_days() -> None:
    settings = LookupSettings(max_session_seconds=3 * 86_400)
    first, last = lookup_days(request(tolerance_seconds=3600), settings)
    assert (first, last) == (DAY - timedelta(days=10), DAY + timedelta(days=3))


def test_a_long_dns_look_back_widens_the_first_day() -> None:
    settings = LookupSettings()
    weeks = 21 * 86_400
    first, _ = lookup_days(
        request(dest_domain="ornek.example"), settings, dns_lookback_seconds=weeks
    )
    assert first == DAY - timedelta(days=21)
    assert UNCONVERTED_WINDOW.total_seconds() > DEFAULT_DNS_LOOKBACK_SECONDS  # no effect by default


@pytest.mark.parametrize(
    ("hour", "minute", "expected"),
    [
        (
            0,
            0,
            (DAY - timedelta(days=9), DAY + timedelta(days=1)),
        ),  # the look-back crosses midnight
        (23, 59, (DAY - timedelta(days=8), DAY + timedelta(days=2))),  # the sessions after it too
    ],
)
def test_a_moment_near_midnight(hour: int, minute: int, expected: tuple[date, date]) -> None:
    asked = LookupRequest(at=tr(DAY, hour, minute, 0), public_ip="198.51.100.20")
    assert lookup_days(asked, LookupSettings()) == expected


def test_never_fewer_than_the_day_and_one_either_side() -> None:
    # A lookup without look-back at all still reads its day ± 1 (the contract's floor).
    settings = LookupSettings(tolerance_seconds=0, max_session_seconds=0)
    start, _ = day_window(DAY)
    asked = LookupRequest(at=start + timedelta(hours=12), mac="00:00:5e:00:53:57")
    first, last = lookup_days(asked, settings)
    assert first <= DAY - DAYS_AROUND
    assert last == DAY + DAYS_AROUND
