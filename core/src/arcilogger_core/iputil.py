"""IP address text exactly as PostgreSQL prints an ``inet`` host address.

Addresses are hashed (audit chain) and compared as text after a round trip through PostgreSQL,
so the text must not depend on the Python version: ``str(ipaddress.IPv6Address(...))`` printed
IPv4-mapped addresses as ``::ffff:c000:201`` in older 3.12 patch releases and as
``::ffff:192.0.2.1`` in newer ones. This module follows PostgreSQL's own rules
(``src/port/inet_net_ntop.c``): RFC 5952 compression of the longest run of at least two zero
groups (first one on a tie), lower-case hex, and dotted IPv4 for IPv4-mapped
(``::ffff:a.b.c.d``) and IPv4-compatible (``::a.b.c.d``) addresses.
"""

from __future__ import annotations

import ipaddress


def canonical_ip(value: str) -> str:
    """Canonical text of a single IP address (no prefix); raises ``ValueError`` if invalid."""
    address = ipaddress.ip_address(value.strip())
    if isinstance(address, ipaddress.IPv4Address):
        return str(address)
    if address.scope_id is not None:
        raise ValueError(f"zone ids are not valid in PostgreSQL inet values: {value!r}")
    return _ipv6_text(address.packed)


def _ipv6_text(packed: bytes) -> str:
    words = [int.from_bytes(packed[i : i + 2], "big") for i in range(0, 16, 2)]

    # Longest run of zero words (the first one wins a tie); a single zero is not compressed.
    best_base, best_len = -1, 0
    cur_base, cur_len = -1, 0
    for index, word in enumerate(words):
        if word == 0:
            if cur_base == -1:
                cur_base, cur_len = index, 1
            else:
                cur_len += 1
        elif cur_base != -1:
            if cur_len > best_len:
                best_base, best_len = cur_base, cur_len
            cur_base = -1
    if cur_base != -1 and cur_len > best_len:
        best_base, best_len = cur_base, cur_len
    if best_len < 2:
        best_base = -1

    parts: list[str] = []
    for index, word in enumerate(words):
        if best_base != -1 and best_base <= index < best_base + best_len:
            if index == best_base:
                parts.append(":")
            continue
        if index != 0:
            parts.append(":")
        encapsulated_ipv4 = (
            index == 6
            and best_base == 0
            and (best_len == 6 or (best_len == 5 and words[5] == 0xFFFF))
        )
        if encapsulated_ipv4:
            parts.append(str(ipaddress.IPv4Address(packed[12:])))
            break
        parts.append(f"{word:x}")
    if best_base != -1 and best_base + best_len == 8:
        parts.append(":")
    return "".join(parts)
