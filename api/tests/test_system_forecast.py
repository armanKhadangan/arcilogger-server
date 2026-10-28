"""The disk forecast of the Sistem page (Wave 4 contract 2.5.2, owner question Q17, 3 W4-B)."""

from __future__ import annotations

from datetime import date, timedelta

from arcilogger_api.system.forecast import (
    HORIZON_DAYS,
    MIN_SAMPLES,
    WINDOW,
    drive_samples,
    forecast,
)

GIB = 1024**3
TOTAL = 1000 * GIB
TODAY = date(2026, 10, 28)


def growing(
    days: int, *, start_used: int, per_day: int, last: date = TODAY
) -> list[tuple[date, int]]:
    """``days`` daily samples ending on ``last``, growing by ``per_day`` bytes a day."""
    first = last - timedelta(days=days - 1)
    return [(first + timedelta(days=i), start_used + i * per_day) for i in range(days)]


def test_forecast_needs_seven_samples() -> None:
    assert MIN_SAMPLES == 7
    six = growing(6, start_used=100 * GIB, per_day=GIB)
    result = forecast(six, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.model_dump() == {
        "state": "insufficient",
        "samples": 6,
        "growth_bytes_per_day": None,
        "warning_on": None,
        "full_on": None,
    }
    assert forecast([], total=TOTAL, warning_percent=80, today=TODAY).samples == 0
    seven = growing(7, start_used=100 * GIB, per_day=GIB)
    assert forecast(seven, total=TOTAL, warning_percent=80, today=TODAY).state == "estimated"


def test_forecast_is_linear_over_the_last_30() -> None:
    assert WINDOW == 30
    # Ten older samples far off the line must not count: only the newest 30 are fitted.
    recent = growing(30, start_used=300 * GIB, per_day=2 * GIB)
    older = [(recent[0][0] - timedelta(days=10 - i), 900 * GIB) for i in range(10)]
    result = forecast(older + recent, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.state == "estimated"
    assert result.samples == 30
    assert result.growth_bytes_per_day == 2 * GIB
    # Today 358 GiB; 800 GiB (80 %) in 221 days, 1000 GiB in 321 days.
    assert recent[-1] == (TODAY, 358 * GIB)
    assert result.warning_on == TODAY + timedelta(days=221)
    assert result.full_on == TODAY + timedelta(days=321)
    # A part of a day still counts as that day (the line reaches the mark during it).
    uneven = growing(10, start_used=100 * GIB, per_day=3 * GIB)
    result = forecast(uneven, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.warning_on == TODAY + timedelta(days=-(-(800 - 127) // 3))  # 224.33 → 225
    # Missing days are gaps on the day axis, not zeros: every other day, 2 GiB per day.
    sparse = growing(30, start_used=300 * GIB, per_day=2 * GIB)[::2]
    assert len(sparse) == 15
    result = forecast(sparse, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.growth_bytes_per_day == 2 * GIB
    # Unsorted input is sorted by day first.
    shuffled = recent[15:] + recent[:15]
    assert (
        forecast(shuffled, total=TOTAL, warning_percent=80, today=TODAY).growth_bytes_per_day
        == 2 * GIB
    )


def test_forecast_not_growing() -> None:
    flat = growing(10, start_used=400 * GIB, per_day=0)
    shrinking = growing(10, start_used=400 * GIB, per_day=-GIB)
    for samples in (flat, shrinking):
        result = forecast(samples, total=TOTAL, warning_percent=80, today=TODAY)
        assert result.model_dump() == {
            "state": "not_growing",
            "samples": 10,
            "growth_bytes_per_day": None,
            "warning_on": None,
            "full_on": None,
        }


def test_forecast_already_above_the_warning_mark() -> None:
    above = growing(10, start_used=850 * GIB, per_day=GIB)  # 859 GiB today, 80 % = 800 GiB
    result = forecast(above, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.state == "estimated"
    assert result.warning_on is None
    assert result.full_on == TODAY + timedelta(days=141)
    # The line already past the size: full "today", never a day in the past.
    full = growing(10, start_used=995 * GIB, per_day=2 * GIB)
    assert forecast(full, total=TOTAL, warning_percent=80, today=TODAY).full_on == TODAY
    # Samples that stopped a week ago: the newest (795 GiB) is below the mark, the line today
    # (830) above it. No measurement is above, so the mark is reached "today", not earlier.
    stale = growing(10, start_used=750 * GIB, per_day=5 * GIB, last=TODAY - timedelta(days=7))
    result = forecast(stale, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.warning_on == TODAY
    assert result.full_on == TODAY + timedelta(days=34)  # (1000 - 830) / 5
    # The drive now decides when it is measured: above the mark since the morning sample …
    below = growing(10, start_used=790 * GIB, per_day=GIB)  # 799 GiB at today's sample
    assert forecast(below, total=TOTAL, warning_percent=80, today=TODAY).warning_on == (
        TODAY + timedelta(days=1)
    )
    result = forecast(below, total=TOTAL, warning_percent=80, today=TODAY, used_now=812 * GIB)
    assert result.warning_on is None
    # … and below it again after a clean-up, although the newest sample was above.
    result = forecast(above, total=TOTAL, warning_percent=80, today=TODAY, used_now=700 * GIB)
    assert result.warning_on == TODAY  # the line says it is past the mark already
    stopped = growing(10, start_used=700 * GIB, per_day=GIB, last=TODAY - timedelta(days=7))
    result = forecast(stopped, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.warning_on == TODAY + timedelta(days=800 - 709 - 7)


def test_drive_samples_read_one_drive_of_the_history() -> None:
    history = [
        {"day": "2026-10-02", "drives": [{"path": "D:", "total": 10, "used": 6}]},
        {
            "day": "2026-10-01",
            "drives": [
                {"path": "D:", "total": 10, "used": 5},
                {"path": "C:", "total": 9, "used": 1},
            ],
        },
        {"day": "not a day", "drives": [{"path": "D:", "total": 10, "used": 7}]},
    ]
    assert drive_samples(history, "D:") == [(date(2026, 10, 1), 5), (date(2026, 10, 2), 6)]
    assert drive_samples(history, "C:") == [(date(2026, 10, 1), 1)]
    assert drive_samples(history, "E:") == []


def test_a_drive_that_hardly_grows_gets_no_date() -> None:
    """A few bytes a day would put the marks thousands of years ahead (beyond the calendar):
    beyond :data:`HORIZON_DAYS` there is no date, and the answer never fails."""
    for per_day in (1, 1000, 50_000):
        crawling = growing(30, start_used=500 * GIB, per_day=per_day)
        result = forecast(crawling, total=TOTAL, warning_percent=80, today=TODAY)
        assert result.state == "estimated"
        assert result.growth_bytes_per_day == per_day
        assert (result.warning_on, result.full_on) == (None, None)
    noisy = [
        (day, used + (i % 2) * 1000 + i)
        for i, (day, used) in enumerate(growing(30, start_used=200 * GIB, per_day=0))
    ]
    assert forecast(noisy, total=TOTAL, warning_percent=80, today=TODAY).full_on is None
    # Just inside the horizon still gets its date.
    near = growing(30, start_used=0, per_day=TOTAL // (HORIZON_DAYS - 100))
    result = forecast(near, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.full_on is not None
    assert result.full_on <= TODAY + timedelta(days=HORIZON_DAYS)


def test_a_line_past_the_mark_long_ago_after_a_clean_up() -> None:
    """A drive at about 85 % that hardly grows, cleaned up today: the line reached the mark
    long before the first sample (a huge negative offset), the live reading is below it. The
    answer is today, never a day before the calendar's first one."""
    samples = [(TODAY - timedelta(days=30 - i), 85 * 10**9 + 2000 * i) for i in range(30)]
    result = forecast(
        samples, total=100 * 10**9, warning_percent=80, today=TODAY, used_now=75 * 10**9
    )
    assert result.state == "estimated"
    assert result.warning_on == TODAY
    assert result.full_on is None  # 15 GB to go at 2 kB a day: beyond the horizon
