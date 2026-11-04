"""The bounds that keep a 5651 lookup's days within one archive reload (#126).

``lookup.max_session_seconds``, ``lookup.dns_lookback_seconds``, ``lease.default_seconds``, each
server of ``lease.server_seconds``, ``lease.timeout_percent`` and ``arp.interval_seconds`` widen
the days a lookup reads; their upper bounds keep those days at most 14, what one reload holds
(the proof is ``worker/tests/test_lookup_reach.py``). A value above a bound is refused, in the
browser and on the console alike, with a code and Turkish and English texts that name the
bounds, never the value given.
"""

from __future__ import annotations

from typing import Any

import pytest

from arcilogger_db.settings import (
    INTEGER_RANGES,
    LEASE_SERVER_SECONDS,
    SETTING_ERRORS,
    SettingValueError,
    validate_setting,
)

#: (key, (lowest, highest) accepted value).
BOUNDED: list[tuple[str, tuple[int, int]]] = [
    ("lookup.max_session_seconds", (60, 172_800)),
    ("lookup.dns_lookback_seconds", (0, 604_800)),
    ("lease.default_seconds", (60, 604_800)),
    ("lease.timeout_percent", (100, 125)),
    ("arp.interval_seconds", (60, 604_800)),
]


@pytest.mark.parametrize(("key", "bounds"), BOUNDED)
def test_a_value_above_the_bound_is_refused_in_both_languages(
    key: str, bounds: tuple[int, int]
) -> None:
    low, high = bounds
    assert INTEGER_RANGES[key] == bounds
    assert validate_setting(key, high) == high
    with pytest.raises(SettingValueError) as caught:
        validate_setting(key, high + 1)
    refusal = caught.value
    assert (refusal.key, refusal.code) == (key, "setting_out_of_range")
    assert refusal.message_tr == f"{low} ile {high} arasında olmalı."
    assert refusal.message_en == f"Must be between {low} and {high}."
    assert str(refusal) == f"{key}: en az {low}, en çok {high} olmalı"


@pytest.mark.parametrize("seconds", [604_801, 14 * 86_400, 8_640_000])
def test_a_server_lease_above_seven_days_is_refused_in_both_languages(seconds: int) -> None:
    """The issue's example (a 14-day server) and FortiOS's longest lease-time (100 days)."""
    assert LEASE_SERVER_SECONDS == (60, 604_800)
    value: dict[str, Any] = {"dhcp61": 3600, "vlan61": seconds}
    with pytest.raises(SettingValueError) as caught:
        validate_setting("lease.server_seconds", value)
    refusal = caught.value
    assert (refusal.key, refusal.code) == ("lease.server_seconds", "setting_lease_seconds")
    assert (refusal.message_tr, refusal.message_en) == SETTING_ERRORS["setting_lease_seconds"]
    assert refusal.message_tr == (
        "Her DHCP sunucusu için 60 ile 604800 saniye (7 gün) arasında bir süre gerekir."
    )
    assert refusal.message_en == (
        "Each DHCP server needs a time between 60 and 604800 seconds (7 days)."
    )
    assert str(refusal) == "lease.server_seconds: 'vlan61' için en çok 604800 saniye girilebilir"
    assert str(seconds) not in refusal.message_tr + refusal.message_en


def test_a_server_lease_at_the_bounds_is_kept() -> None:
    value = {"dhcp61": 60, "vlan61": 604_800}
    assert validate_setting("lease.server_seconds", value) == value
