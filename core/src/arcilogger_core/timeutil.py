"""Time rules used everywhere in ARCILOGGER.

* Every timestamp handled by ARCILOGGER is timezone-aware. Naive datetimes are a bug and are
  rejected with :class:`NaiveDatetimeError` instead of being guessed.
* Storage and computation use UTC.
* People see Europe/Istanbul (UTC+03:00, no DST since 2016). Older timestamps still convert
  correctly because the IANA database knows Türkiye's historical DST rules.
* Raw log files are partitioned by **receive day in Europe/Istanbul**: a message received at
  2026-10-12T23:59:59+03:00 belongs to day 2026-10-12, one received a second later to
  2026-10-13. :func:`site_day` and :func:`day_window` implement exactly this rule.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

SITE_TZ_NAME = "Europe/Istanbul"
SITE_TZ = ZoneInfo(SITE_TZ_NAME)

TimeSpec = Literal["seconds", "milliseconds", "microseconds"]


class NaiveDatetimeError(ValueError):
    """Raised when a datetime without timezone information reaches ARCILOGGER code."""


def utc_now() -> datetime:
    """Return the current time as an aware UTC datetime."""
    return datetime.now(UTC)


def require_aware(dt: datetime) -> datetime:
    """Return ``dt`` unchanged if it is timezone-aware, otherwise raise."""
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise NaiveDatetimeError(f"naive datetime is not allowed: {dt!r}")
    return dt


def to_utc(dt: datetime) -> datetime:
    """Convert an aware datetime to UTC."""
    return require_aware(dt).astimezone(UTC)


def to_site(dt: datetime) -> datetime:
    """Convert an aware datetime to the site time zone (Europe/Istanbul)."""
    return require_aware(dt).astimezone(SITE_TZ)


def site_day(dt: datetime) -> date:
    """Return the receive-time partition day of ``dt`` (calendar day in Europe/Istanbul)."""
    return to_site(dt).date()


def day_window(day: date) -> tuple[datetime, datetime]:
    """Return the half-open window ``[start, end)`` of a site day as aware datetimes.

    The bounds carry the site offset, e.g. ``2026-10-12T00:00:00+03:00`` to
    ``2026-10-13T00:00:00+03:00``; this is the ``window`` written into daily manifests.
    """
    start = datetime.combine(day, time(0, 0), tzinfo=SITE_TZ)
    end = datetime.combine(day + timedelta(days=1), time(0, 0), tzinfo=SITE_TZ)
    return start, end


def isoformat_site(dt: datetime, timespec: TimeSpec = "microseconds") -> str:
    """RFC 3339 text in the site time zone, e.g. ``2026-09-23T09:44:57.123456+03:00``."""
    return to_site(dt).isoformat(timespec=timespec)
