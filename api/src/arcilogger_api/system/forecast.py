"""When a drive reaches its warning mark and when it is full (Wave 4 contract 2.5.2, owner
question Q17).

The worker job ``system.sample`` records one sample a day (:mod:`arcilogger_worker.system_jobs`).
Per drive, the newest :data:`WINDOW` samples are fitted with a straight line (least squares, used
bytes against the day): fewer than :data:`MIN_SAMPLES` samples → ``insufficient``; a slope of
zero or less → ``not_growing``; otherwise ``estimated`` with the rounded slope in bytes per day,
the first day the line reaches the warning mark and the first day it reaches the drive's size.

* ``warning_on`` is ``null`` when the drive is already at or above the mark (its use now, the
  live reading the API passes, or without one its newest sample: a measurement decides, not the
  line) or when the mark lies beyond the horizon (below). Whether a drive is above the mark is
  read from ``used_bytes``/``used_percent`` against ``warning_percent``, not from the null.
* The days are never before ``today``: a line that should already have crossed a mark no
  measurement has reached says "today".
* A day further away than :data:`HORIZON_DAYS` is ``null``: a drive that grows by a few bytes a
  day has no date anyone could plan with (and the day would not fit a calendar).

A straight line is a simple model on purpose: it is easy to explain, and a forecast weeks ahead
can only be a rough guide. Days without a sample (the worker was stopped) are gaps on the day
axis, not zeros.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from datetime import date, timedelta
from typing import Any, Final

from arcilogger_api.system.models import DiskForecast

#: Samples the line is fitted to (the newest ones).
WINDOW: Final = 30
#: Fewer samples give no forecast.
MIN_SAMPLES: Final = 7
#: A mark further away than this (about 100 years) gets no date.
HORIZON_DAYS: Final = 36_500


def drive_samples(history: Iterable[dict[str, Any]], path: str) -> list[tuple[date, int]]:
    """``(day, used bytes)`` of one drive from the history entries (``system-history.json``),
    oldest first; an entry with an unreadable day is left out."""
    samples: dict[date, int] = {}
    for entry in history:
        try:
            day = date.fromisoformat(entry["day"])
        except (KeyError, TypeError, ValueError):
            continue
        for drive in entry.get("drives", []):
            if drive.get("path") == path:
                samples[day] = int(drive["used"])
    return sorted(samples.items())


def _first_day(
    intercept: float, slope: float, target: float, start: date, today: date
) -> date | None:
    """The first day (on or after ``today``) the line ``intercept + slope * x`` reaches
    ``target``; ``x`` counts days from ``start``. ``None`` beyond :data:`HORIZON_DAYS`.

    The offset is taken from ``today`` before a date is built: a line that reached the target
    long before the first sample (a slope of a few bytes a day) would otherwise lie before the
    calendar's first day."""
    ahead = math.ceil((target - intercept) / slope) - (today - start).days
    if ahead > HORIZON_DAYS:
        return None
    return today + timedelta(days=max(ahead, 0))


def forecast(
    samples: Sequence[tuple[date, int]],
    *,
    total: int,
    warning_percent: int,
    today: date,
    used_now: int | None = None,
) -> DiskForecast:
    """The forecast of a drive of ``total`` bytes from its daily ``(day, used bytes)`` samples
    (``used_now``: the drive's use at this moment, when measured)."""
    used = sorted(samples)[-WINDOW:]
    if len(used) < MIN_SAMPLES:
        return DiskForecast(
            state="insufficient",
            samples=len(used),
            growth_bytes_per_day=None,
            warning_on=None,
            full_on=None,
        )
    start = used[0][0]
    xs = [float((day - start).days) for day, _ in used]
    ys = [float(value) for _, value in used]
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    spread = sum((x - mean_x) ** 2 for x in xs)
    slope = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / spread
    if slope <= 0:
        return DiskForecast(
            state="not_growing",
            samples=len(used),
            growth_bytes_per_day=None,
            warning_on=None,
            full_on=None,
        )
    intercept = mean_y - slope * mean_x
    warning = total * warning_percent / 100
    already_above = (used_now if used_now is not None else used[-1][1]) >= warning
    return DiskForecast(
        state="estimated",
        samples=len(used),
        growth_bytes_per_day=round(slope),
        warning_on=None if already_above else _first_day(intercept, slope, warning, start, today),
        full_on=_first_day(intercept, slope, total, start, today),
    )
