"""Display formatting — checked against the vectors shared with the web interface."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arcilogger_core.fmt import (
    Lang,
    format_bytes,
    format_date,
    format_datetime,
    format_duration,
    format_number,
)
from arcilogger_core.timeutil import NaiveDatetimeError

VECTORS: dict[str, list[dict[str, Any]]] = json.loads(
    (Path(__file__).parents[2] / "tests" / "fixtures" / "format-vectors.json").read_text(
        encoding="utf-8"
    )
)


def _utc(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


@pytest.mark.parametrize("case", VECTORS["datetime"], ids=lambda c: c["utc"])
def test_datetime_vectors(case: dict[str, Any]) -> None:
    assert format_datetime(_utc(case["utc"])) == case["expected"]


@pytest.mark.parametrize("case", VECTORS["date"], ids=lambda c: c["utc"])
def test_date_vectors(case: dict[str, Any]) -> None:
    assert format_date(_utc(case["utc"])) == case["expected"]


@pytest.mark.parametrize("case", VECTORS["number"], ids=lambda c: f"{c['value']}-{c['lang']}")
def test_number_vectors(case: dict[str, Any]) -> None:
    assert format_number(case["value"], case["decimals"], case["lang"]) == case["expected"]


@pytest.mark.parametrize("case", VECTORS["bytes"], ids=lambda c: f"{c['value']}-{c['lang']}")
def test_bytes_vectors(case: dict[str, Any]) -> None:
    assert format_bytes(case["value"], case["lang"]) == case["expected"]


@pytest.mark.parametrize("case", VECTORS["duration"], ids=lambda c: f"{c['seconds']}-{c['lang']}")
def test_duration_vectors(case: dict[str, Any]) -> None:
    assert format_duration(case["seconds"], case["lang"]) == case["expected"]


def test_plain_date_is_formatted_as_is() -> None:
    assert format_date(date(2026, 9, 23)) == "23.09.2026"


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(NaiveDatetimeError):
        format_datetime(datetime(2026, 9, 23, 9, 44, 7))  # noqa: DTZ001 - the point of the test


@pytest.mark.parametrize(
    ("call", "arg"),
    [
        (format_bytes, -1),
        (format_duration, -1),
        (format_duration, float("nan")),
        (format_number, float("inf")),
    ],
)
def test_invalid_values_raise(call: Any, arg: float) -> None:
    with pytest.raises(ValueError, match=r"."):
        call(arg)


def test_negative_decimals_raise() -> None:
    with pytest.raises(ValueError, match="decimals"):
        format_number(1, -1)


@given(st.integers(min_value=0, max_value=2**62), st.sampled_from(["tr", "en"]))
def test_bytes_always_have_a_known_unit(value: int, lang: Lang) -> None:
    number, unit = format_bytes(value, lang).rsplit(" ", 1)
    assert unit in {"B", "KB", "MB", "GB", "TB", "PB"}
    assert number


@given(st.floats(min_value=0, max_value=10**9, allow_nan=False))
def test_duration_never_shows_more_than_two_units(seconds: float) -> None:
    assert len(format_duration(seconds).split(" ")) in {2, 4}
