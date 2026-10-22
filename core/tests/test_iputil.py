"""PostgreSQL-compatible IP text (``arcilogger_core.iputil``)."""

from __future__ import annotations

import ipaddress

import pytest
from hypothesis import given
from hypothesis import strategies as st

from arcilogger_core.iputil import canonical_ip

# Expected values are PostgreSQL 16's ``host(x::inet)`` output.
CASES = [
    ("172.20.10.37", "172.20.10.37"),
    (" 100.64.12.9 ", "100.64.12.9"),
    ("::", "::"),
    ("::1", "::1"),
    ("1::", "1::"),
    ("2001:DB8::1", "2001:db8::1"),
    ("2001:db8:0:0:1:0:0:1", "2001:db8::1:0:0:1"),
    ("fe80::1:0:0:1", "fe80::1:0:0:1"),
    ("2001:db8:0:1:1:1:1:1", "2001:db8:0:1:1:1:1:1"),
    ("::ffff:192.0.2.1", "::ffff:192.0.2.1"),
    ("::ffff:c000:201", "::ffff:192.0.2.1"),
    ("::ffff:0:0", "::ffff:0.0.0.0"),
    ("::c000:201", "::192.0.2.1"),
    ("::1:0:0:0", "::1:0:0:0"),
]


@pytest.mark.parametrize(("value", "expected"), CASES)
def test_matches_postgresql_text(value: str, expected: str) -> None:
    assert canonical_ip(value) == expected


@pytest.mark.parametrize("value", ["", "999.1.1.1", "172.20.10.0/24", "fe80::1%eth0", "x"])
def test_invalid_addresses_raise(value: str) -> None:
    with pytest.raises(ValueError, match=r".+"):
        canonical_ip(value)


@given(st.integers(min_value=0, max_value=2**128 - 1))
def test_round_trips_to_the_same_address(number: int) -> None:
    text = canonical_ip(str(ipaddress.IPv6Address(number)))
    assert ipaddress.IPv6Address(text) == ipaddress.IPv6Address(number)
    assert canonical_ip(text) == text
