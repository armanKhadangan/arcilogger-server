"""MAC address facts that need no lookup table (shared by correlation and the lookup engine)."""

from __future__ import annotations

import re

_HEX = re.compile(r"[^0-9a-f]")


def is_randomized(mac: str) -> bool:
    """Locally administered unicast address: second hex digit 2, 6, A or E.

    Phones ("private Wi-Fi address") and Windows ("random hardware addresses") use such
    addresses; they can change per network or over time, so they identify a device less
    reliably than a manufacturer address (spec 9.2).
    """
    digits = _HEX.sub("", mac.lower())
    if len(digits) != 12:
        raise ValueError(f"invalid MAC address {mac!r}")
    return int(digits[:2], 16) & 0x03 == 0x02
