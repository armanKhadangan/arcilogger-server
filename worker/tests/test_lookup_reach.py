"""Every lookup's days fit one archive reload (#126: Wave 5 contract §5, G5 and G7).

A day the database window dropped comes back from the vault in a reload of at most 14 days
(``archive_reload_too_large``). A lookup whose days (``lookup.days.lookup_days``) were longer could
never be answered once they are dropped, so the settings that widen the days have upper bounds
(``arcilogger_db.settings.INTEGER_RANGES``) under which they never exceed
:data:`~arcilogger_worker.lookup.days.RELOAD_DAYS`: proved here with every setting at its bound
and over a grid of the accepted values, at moments all around the day, also on the site's
23-hour days of before 2016. The bounds stay at or above FortiOS's default lease-time and the
shipped defaults. A value stored before the bounds (or written around the validator) makes a
lookup whose days to bring back from the vault are more than one reload holds refused, naming
the settings above their bounds (``lookup.days.check_reload``; the API's 503 is tested in
``api/tests/test_archive_api.py``).
"""

from __future__ import annotations

import itertools
from collections.abc import Iterator, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Final

import pytest

from arcilogger_core.timeutil import day_window
from arcilogger_db.settings import (
    DEFAULT_SETTINGS,
    INTEGER_RANGES,
    LEASE_SERVER_SECONDS,
    validate_setting,
)
from arcilogger_worker.lookup.attribution import UNCONVERTED_WINDOW
from arcilogger_worker.lookup.days import (
    RELOAD_DAYS,
    LookupReachError,
    above_bounds,
    check_reload,
    lookup_days,
)
from arcilogger_worker.lookup.types import MAX_TOLERANCE_SECONDS, LookupRequest, LookupSettings

#: The integer settings that widen the days and fill ``LookupSettings`` (:func:`settings_of`);
#: the DNS look-back is passed beside them, as the API does, and ``lease.server_seconds`` is an
#: object.
WIDENING: Final = (
    "lookup.tolerance_seconds",
    "lookup.max_session_seconds",
    "lease.default_seconds",
    "lease.timeout_percent",
    "arp.interval_seconds",
)
DNS: Final = "lookup.dns_lookback_seconds"
#: A day of 2026 and the site's 23-hour day of 2015 (summer time began, Europe/Istanbul), whose
#: lookups' days span it.
DAYS = (date(2026, 10, 12), date(2015, 3, 29), date(2015, 4, 7))


def low_of(key: str) -> int:
    return INTEGER_RANGES[key][0]


def high(key: str) -> int:
    bound = INTEGER_RANGES[key][1]
    assert bound is not None, f"{key} has no upper bound"
    return bound


def moments(day: date, step: timedelta = timedelta(minutes=10)) -> Iterator[datetime]:
    """Every ``step`` of ``day`` (UTC moments, so a 23-hour day is walked as it is), and its
    last second."""
    start, end = day_window(day)
    at = start.astimezone(UTC)
    while at < end:
        yield at
        at += step
    yield end.astimezone(UTC) - timedelta(seconds=1)


def asked(at: datetime, tolerance_seconds: int | None = None) -> LookupRequest:
    return LookupRequest(
        at=at,
        internal_ip="172.20.42.57",
        dest_domain="ornek.example",
        tolerance_seconds=tolerance_seconds,
    )


def settings_of(
    values: Mapping[str, int], *, server: int | None, use_logged_lease: bool
) -> LookupSettings:
    """The lookup settings of ``values`` (keys of :data:`WIDENING`), with one DHCP server of
    ``server`` seconds in ``lease.server_seconds`` (``None``: none)."""
    return LookupSettings(
        tolerance_seconds=values["lookup.tolerance_seconds"],
        max_session_seconds=values["lookup.max_session_seconds"],
        lease_default_seconds=values["lease.default_seconds"],
        lease_server_seconds={} if server is None else {"dhcp42": server},
        lease_timeout_percent=values["lease.timeout_percent"],
        arp_interval_seconds=values["arp.interval_seconds"],
        use_logged_lease=use_logged_lease,
    )


def widest(*, use_logged_lease: bool) -> LookupSettings:
    """Every setting that widens the days at its bound, a server of ``lease.server_seconds``
    too."""
    return settings_of(
        {key: high(key) for key in WIDENING},
        server=high("lease.default_seconds"),
        use_logged_lease=use_logged_lease,
    )


def count(first: date, last: date) -> int:
    return (last - first).days + 1


@pytest.mark.parametrize("use_logged_lease", [True, False])
def test_every_setting_at_its_bound_fits_one_reload(use_logged_lease: bool) -> None:
    settings = widest(use_logged_lease=use_logged_lease)
    assert high("lookup.tolerance_seconds") == MAX_TOLERANCE_SECONDS  # the request's bound
    longest = 0
    for day in DAYS:
        for at in moments(day, timedelta(minutes=2)):
            for tolerance in (None, MAX_TOLERANCE_SECONDS):
                request = asked(at, tolerance)
                days = lookup_days(request, settings, dns_lookback_seconds=high(DNS))
                assert count(*days) <= RELOAD_DAYS, (at, days)
                check_reload(*days, settings, dns_lookback_seconds=high(DNS))
                longest = max(longest, count(*days))
    assert above_bounds(settings, high(DNS)) == []
    # Some moment needs the whole reload: a wider step of the engine fails above.
    assert longest == RELOAD_DAYS


def test_the_accepted_values_never_need_more_than_one_reload() -> None:
    """A grid of each widening setting's lower bound, default and upper bound (and the
    servers' lease time), at moments around the day."""
    keys = [*WIDENING, DNS]
    choices = [(low_of(key), int(DEFAULT_SETTINGS[key]), high(key)) for key in keys]
    servers = (None, *(bound for bound in LEASE_SERVER_SECONDS if bound is not None))
    instants = [at for day in DAYS for at in moments(day, timedelta(hours=3))]
    checked = 0
    for values in itertools.product(*choices):
        chosen = dict(zip(keys, values, strict=True))
        for server, use_logged_lease in itertools.product(servers, (True, False)):
            settings = settings_of(chosen, server=server, use_logged_lease=use_logged_lease)
            for at in instants:
                days = lookup_days(asked(at), settings, dns_lookback_seconds=chosen[DNS])
                assert count(*days) <= RELOAD_DAYS, (chosen, server, at, days)
                checked += 1
    assert checked > 100_000


def test_the_bounds_stay_above_fortios_default_lease_and_the_shipped_values() -> None:
    """7 days is FortiOS's default DHCP ``lease-time`` (604,800 s, DEVICE-SETUP.md section 2.4;
    the D8 probe's fallback, ``UNCONVERTED_WINDOW``): a FortiGate interface's lease time can be
    entered in ``lease.server_seconds`` (the fallback when its logged lease cannot be used), and
    so can the shipped ones (one day; ARP-LOG every hour)."""
    fortios = int(UNCONVERTED_WINDOW.total_seconds())
    assert fortios == 604_800
    assert high("lease.default_seconds") >= fortios
    assert INTEGER_RANGES["lease.default_seconds"] == LEASE_SERVER_SECONDS
    assert validate_setting("lease.server_seconds", {"vlan61": fortios}) == {"vlan61": fortios}
    for key in [*WIDENING, DNS]:
        low, top = INTEGER_RANGES[key]
        assert top is not None
        assert low <= DEFAULT_SETTINGS[key] < top, key
        assert validate_setting(key, top) == top


#: A moment of the site's day.
MOMENT: Final = datetime(2026, 10, 12, 8, 23, 41, tzinfo=UTC)


@pytest.mark.parametrize(
    ("settings", "dns", "named"),
    [
        # The issue's example: a 14-day lease of one DHCP server.
        (
            LookupSettings(lease_server_seconds={"dhcp42": 14 * 86_400}),
            3600,
            ["lease.server_seconds"],
        ),
        # The longest of several servers counts.
        (
            LookupSettings(lease_server_seconds={"dhcp41": 3600, "dhcp42": 14 * 86_400}),
            3600,
            ["lease.server_seconds"],
        ),
        (
            LookupSettings(lease_default_seconds=2 * 86_400, lease_timeout_percent=1000),
            3600,
            ["lease.timeout_percent"],
        ),
        (LookupSettings(max_session_seconds=7 * 86_400), 3600, ["lookup.max_session_seconds"]),
        (LookupSettings(arp_interval_seconds=14 * 86_400), 3600, ["arp.interval_seconds"]),
        (LookupSettings(), 21 * 86_400, ["lookup.dns_lookback_seconds"]),
        (
            LookupSettings(lease_default_seconds=14 * 86_400, max_session_seconds=7 * 86_400),
            21 * 86_400,
            ["lookup.max_session_seconds", "lookup.dns_lookback_seconds", "lease.default_seconds"],
        ),
    ],
)
def test_a_stored_value_above_its_bound_refuses_a_reload_longer_than_one(
    settings: LookupSettings, dns: int, named: list[str]
) -> None:
    """Stored before the bounds, or written around the validator: the lookup's days are more
    than one reload holds. Should all of them be gone, the reload the 409 asks for would be
    refused, so the lookup is refused, naming the settings above their bounds."""
    first, last = lookup_days(asked(MOMENT), settings, dns_lookback_seconds=dns)
    assert count(first, last) > RELOAD_DAYS
    assert above_bounds(settings, dns) == named
    with pytest.raises(LookupReachError) as refused:
        check_reload(first, last, settings, dns_lookback_seconds=dns)
    error = refused.value
    assert (error.first, error.last, error.days, error.settings) == (
        first,
        last,
        count(first, last),
        named,
    )
    assert str(error) == (
        f"the lookup needs {count(first, last)} days back from the archive ({first} to {last}), "
        f"more than one reload holds ({RELOAD_DAYS}): {', '.join(named)} above its bound"
    )


def test_the_refusal_is_about_the_days_to_bring_back() -> None:
    """One reload holds 14 days: 14 days to bring back are no refusal, 15 are, whatever the
    settings (a value above its bound whose days are in the database, or partly so, blocks no
    lookup); with no setting above its bound the text says so."""
    wide = LookupSettings(lease_server_seconds={"dhcp42": 14 * 86_400})
    last = MOMENT.date()
    fourteen = last - timedelta(days=RELOAD_DAYS - 1)
    check_reload(fourteen, last, wide)
    check_reload(last, last, wide)
    with pytest.raises(LookupReachError) as refused:
        check_reload(fourteen - timedelta(days=1), last, LookupSettings())
    assert refused.value.days == RELOAD_DAYS + 1
    assert str(refused.value).endswith(": no setting above its bound")


def test_a_value_above_its_bound_that_still_fits_is_named_but_not_refused() -> None:
    """A stored timeout percentage above 125 whose leases stay short reads no more than one
    reload holds: it is above its bound, but nothing is refused."""
    settings = LookupSettings(lease_timeout_percent=200)
    days = lookup_days(asked(MOMENT), settings)
    assert count(*days) <= RELOAD_DAYS
    assert above_bounds(settings) == ["lease.timeout_percent"]
    check_reload(*days, settings)
