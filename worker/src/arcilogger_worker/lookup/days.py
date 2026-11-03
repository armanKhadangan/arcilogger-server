"""The days a 5651 lookup reads (Wave 5 contract §5, G7).

The database keeps the last ``db.retention_days`` days of log rows; an older day comes back from
the sealed vault on request ("arşivden yeniden yükle", G4). Before a lookup runs, the API asks
:func:`lookup_days` which site days the engine can read for it, and answers 409
``archive_reload_required`` when one of them is outside the window and not loaded: a lookup never
answers "not found" for a day it could not see. The days are days of device time (``time``: the
partitions the window job drops); a reload of them loads the raw days (``raw_day``, the spool
files) two days either side as well, whose rows reach into them (ArciIngest).

With the 30-day window and the defaults below, a lookup reads back twelve days before the asked
moment: a moment more than about 18 days old needs a reload once the window job has run.

The days are those of the widest time range any step of :mod:`.engine` reads, around the asked
moment ``T`` with the tolerance ``t`` (the request's, else ``lookup.tolerance_seconds``) and the
longest session ``S`` (``lookup.max_session_seconds``):

* **at least the day of ``T`` ± 1** (the contract's floor);
* the sessions: device times from ``T - t - S`` (a session logged at its end began up to ``S``
  before) to ``T + t + S`` (a session that spans the moment is logged after it), with the
  ``LOG_SLACK`` of the web/app/DNS logs tied to them; the domain's DNS answers from
  ``lookup.dns_lookback_seconds`` before ``T - t``;
* the **lease look-back of the DHCP rules** applied to the chains of those sessions: the widest
  DHCP window the engine reads before a chain's moment, which is the D8 probe's
  (``max(TimelineRules.longest_window, UNCONVERTED_WINDOW)``: the longest configured lease timeout
  or ARP grace, at least the 604,800 s of FortiOS's default lease). The two-origin check (D27) and
  a FortiGate lease's newest record read within it too;
* with ``dhcp.fortigate_use_logged_lease`` (the default), **the logged lease time of a FortiGate
  lease** (D6), which may be anything up to the FortiOS maximum of 8,640,000 s (100 days): the
  lookup does not know it before it reads the lease, so the days reach back as far as one reload
  holds (:data:`RELOAD_DAYS`, the 14 days of G5), never less than the rules above. With the
  defaults (60 s, 1 day, 1.1-day leases) the days are ``T``'s day - 12 to ``T``'s day + 1,
  fourteen days (``T``'s day - 11 to + 2 when ``T`` lies within two minutes of midnight before
  the next day; without the setting ``T``'s day - 8 to + 1).

A look-back of the engine that reaches before the first of these days (the 100-day cap of the
missed-move check, round 7; a logged lease longer than what a reload holds, through the lease's
newest record, the post-lease checks, D27 and the ARP interval after a RELEASE) reads only from
the start of that day, :func:`reads_from`; the one read that reaches forward past the moment, D27
(``T + W``, W up to a logged lease time), reads only up to the end of the last day,
:func:`reads_until`. Either way the answer says so (``lookback_clamped_to_window``, W5-G
deviations): its confidence and its notes rest only on rows a reload brings back, so a lookup
gives the same answer before a drop and after the reload (G7) and never changes silently. The
``leases``, devices and assignments the chains rest on are not log rows: the window job never
drops them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Final

from arcilogger_core.timeutil import day_window, site_day
from arcilogger_db.models import LEASE_SECONDS_RANGE
from arcilogger_worker.lookup.attribution import UNCONVERTED_WINDOW
from arcilogger_worker.lookup.engine import DEFAULT_DNS_LOOKBACK_SECONDS
from arcilogger_worker.lookup.sessions import LOG_SLACK
from arcilogger_worker.lookup.types import LookupRequest, LookupSettings

#: The floor of G7: the day of the asked moment and one day on either side.
DAYS_AROUND = timedelta(days=1)
#: The most days one reload holds (G5, ``archive_reload_too_large``; the API's
#: ``archive.service.MAX_DAYS``): the reach of a logged FortiGate lease time.
RELOAD_DAYS: Final = 14
#: The longest logged lease time a FortiGate lease runs on (D6: the FortiOS maximum).
LOGGED_LEASE_MAX: Final = timedelta(seconds=LEASE_SECONDS_RANGE[1])


def dhcp_lookback(settings: LookupSettings) -> timedelta:
    """How far before a chain's moment the DHCP steps of a lookup read (module text)."""
    # The lease timeline imports this package (its refs), so its rules are imported here, as
    # the engine's D8 probe does; the same grace as there (the window does not depend on it).
    from arcilogger_worker.correlation.timeline import TimelineRules

    grace = min(settings.arp_release_grace_seconds, settings.arp_interval_seconds)
    rules = TimelineRules.from_settings(settings, release_grace=grace)
    return max(timedelta(seconds=rules.longest_window), UNCONVERTED_WINDOW)


def lookup_range(
    request: LookupRequest,
    settings: LookupSettings,
    *,
    dns_lookback_seconds: int = DEFAULT_DNS_LOOKBACK_SECONDS,
) -> tuple[datetime, datetime]:
    """The widest device-time range the lookup reads, ``(earliest, latest)`` (module text),
    the logged lease time of a FortiGate lease aside (:func:`lookup_days`)."""
    around = timedelta(seconds=_tolerance(request, settings))
    session = timedelta(seconds=settings.max_session_seconds)
    low = request.at - around
    earliest = min(
        low - session - LOG_SLACK - dhcp_lookback(settings),
        low - timedelta(seconds=max(0, dns_lookback_seconds)),
    )
    return earliest, request.at + around + session + LOG_SLACK


def _tolerance(request: LookupRequest, settings: LookupSettings) -> int:
    """The tolerance of the lookup: the request's, else ``lookup.tolerance_seconds``."""
    return (
        request.tolerance_seconds
        if request.tolerance_seconds is not None
        else settings.tolerance_seconds
    )


def lookup_days(
    request: LookupRequest,
    settings: LookupSettings,
    *,
    dns_lookback_seconds: int = DEFAULT_DNS_LOOKBACK_SECONDS,
) -> tuple[date, date]:
    """The site days ``(first, last)`` of device time, both included, whose log rows the
    lookup may read: the one place that names them (G7; module text)."""
    earliest, latest = lookup_range(request, settings, dns_lookback_seconds=dns_lookback_seconds)
    day = site_day(request.at)
    first = min(site_day(earliest), day - DAYS_AROUND)
    last = max(site_day(latest), day + DAYS_AROUND)
    if settings.use_logged_lease:
        # A logged lease time is known only once its lease is read: as far back as one reload
        # holds, at most the FortiOS maximum (module text).
        chains = timedelta(seconds=_tolerance(request, settings) + settings.max_session_seconds)
        logged = site_day(request.at - chains - LOG_SLACK - LOGGED_LEASE_MAX)
        first = min(first, max(logged, last - timedelta(days=RELOAD_DAYS - 1)))
    return first, last


def reads_from(
    request: LookupRequest,
    settings: LookupSettings,
    *,
    dns_lookback_seconds: int = DEFAULT_DNS_LOOKBACK_SECONDS,
) -> datetime:
    """The earliest device time whose log rows the lookup reads: the start of the first day of
    :func:`lookup_days`. A look-back of the engine reaching before it starts here (module
    text)."""
    first, _ = lookup_days(request, settings, dns_lookback_seconds=dns_lookback_seconds)
    return day_window(first)[0]


def reads_until(
    request: LookupRequest,
    settings: LookupSettings,
    *,
    dns_lookback_seconds: int = DEFAULT_DNS_LOOKBACK_SECONDS,
) -> datetime:
    """The latest device time whose log rows the lookup reads: the last microsecond of the last
    day of :func:`lookup_days` (the database's precision). A read of the engine reaching past
    it ends here (module text)."""
    _, last = lookup_days(request, settings, dns_lookback_seconds=dns_lookback_seconds)
    return day_window(last)[1] - timedelta(microseconds=1)
