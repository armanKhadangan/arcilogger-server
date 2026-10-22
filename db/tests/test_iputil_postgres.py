"""``canonical_ip`` produces exactly PostgreSQL's ``inet`` text (audit hashes depend on it)."""

from __future__ import annotations

import ipaddress
import random

from sqlalchemy import text
from sqlalchemy.orm import Session

from arcilogger_core.iputil import canonical_ip


def _samples() -> list[str]:
    rng = random.Random(5651)  # noqa: S311 - reproducible test data, not cryptography
    fixed = [
        "172.20.10.37",
        "0.0.0.0",  # noqa: S104 - an address value, not a bind
        "255.255.255.255",
        "::",
        "::1",
        "1::",
        "::ffff:192.0.2.1",
        "::ffff:0:0",
        "::c000:201",
        "::1:0:0:0",
        "2001:db8:0:0:1:0:0:1",
        "fe80::1:0:0:1",
        "1:0:0:2:0:0:0:3",
        "1:0:0:0:2:0:0:3",
        "0:0:1::",
        "::ffff:1:2",
    ]
    randoms = []
    for _ in range(300):
        words = [rng.choice([0, 0, 0, rng.randrange(1, 0x10000)]) for _ in range(8)]
        randoms.append(str(ipaddress.IPv6Address(b"".join(w.to_bytes(2, "big") for w in words))))
    randoms += [str(ipaddress.IPv4Address(rng.randrange(2**32))) for _ in range(50)]
    return fixed + randoms


def test_canonical_ip_equals_postgresql_host_text(db_session: Session) -> None:
    samples = _samples()
    rows = db_session.execute(
        text(
            "SELECT value, host(CAST(value AS inet)) FROM unnest(CAST(:values AS text[])) AS value"
        ),
        {"values": samples},
    ).all()
    mismatches = [
        (value, pg, canonical_ip(value)) for value, pg in rows if pg != canonical_ip(value)
    ]
    assert mismatches == []
    assert len(rows) == len(samples)
