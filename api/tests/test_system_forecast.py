"""The disk forecast of the Sistem page (Wave 4 contract 2.5.2, owner question Q17, 3 W4-B)."""

from __future__ import annotations

from datetime import date, timedelta

from arcilogger_api.system.forecast import MIN_SAMPLES, WINDOW, drive_samples, forecast

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
    # Samples that stopped a week ago, the line crossing the mark since: today, not earlier.
    # The newest sample (795 GiB, a week ago) is below the mark, the line today (830) above it.
    stale = growing(10, start_used=750 * GIB, per_day=5 * GIB, last=TODAY - timedelta(days=7))
    result = forecast(stale, total=TOTAL, warning_percent=80, today=TODAY)
    assert result.warning_on is None
    assert result.full_on == TODAY + timedelta(days=34)  # (1000 - 830) / 5
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
