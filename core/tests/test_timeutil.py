"""Time rules: aware datetimes only, UTC inside, receive-day partitions in Europe/Istanbul."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from arcilogger_core.timeutil import (
    SITE_TZ,
    NaiveDatetimeError,
    day_window,
    isoformat_site,
    require_aware,
    site_day,
    to_site,
    to_utc,
    utc_now,
)


def test_utc_now_is_aware_utc() -> None:
    now = utc_now()
    assert now.utcoffset() == timedelta(0)


def test_naive_datetimes_are_rejected() -> None:
    with pytest.raises(NaiveDatetimeError):
        require_aware(datetime(2026, 10, 12))  # noqa: DTZ001 - the point of the test


def test_site_offset_is_plus_three() -> None:
    dt = to_site(datetime(2026, 9, 23, 6, 44, 7, tzinfo=UTC))
    assert dt.utcoffset() == timedelta(hours=3)
    assert dt.hour == 9


def test_to_utc_round_trip() -> None:
    local = datetime(2026, 9, 23, 9, 44, 7, tzinfo=SITE_TZ)
    assert to_utc(local) == datetime(2026, 9, 23, 6, 44, 7, tzinfo=UTC)


@pytest.mark.parametrize(
    ("utc_text", "expected_day"),
    [
        ("2026-10-12T20:59:59.999999+00:00", date(2026, 10, 12)),  # 23:59:59.999999 local
        ("2026-10-12T21:00:00+00:00", date(2026, 10, 13)),  # 00:00:00 local
        ("2026-10-11T21:00:00+00:00", date(2026, 10, 12)),
    ],
)
def test_site_day_partition_boundary(utc_text: str, expected_day: date) -> None:
    assert site_day(datetime.fromisoformat(utc_text)) == expected_day


def test_day_window_matches_manifest_format() -> None:
    start, end = day_window(date(2026, 10, 12))
    assert start.isoformat() == "2026-10-12T00:00:00+03:00"
    assert end.isoformat() == "2026-10-13T00:00:00+03:00"
    assert end - start == timedelta(days=1)


def test_isoformat_site() -> None:
    dt = datetime(2026, 9, 23, 6, 44, 57, 123456, tzinfo=UTC)
    assert isoformat_site(dt) == "2026-09-23T09:44:57.123456+03:00"
    assert isoformat_site(dt, "seconds") == "2026-09-23T09:44:57+03:00"
