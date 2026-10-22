"""Strict value conversions shared by the parsers.

Every converter returns the value in the Python type the loader expects
(``arcilogger_db.rowcheck``) or ``None`` when the text is not a valid value. The converters are
deliberately **stricter** than PostgreSQL (``check_row`` may never be looser than the database,
and neither may a parser): an unusual spelling becomes ``None`` + the raw text in ``extra``,
never a batch that fails to load.

They are also on the hot path (tens of thousands of calls per second), so the common cases are
answered by one compiled regular expression or a ``str`` method.
"""

from __future__ import annotations

import ipaddress
import re
from typing import Final

SMALLINT: Final = (-(2**15), 2**15 - 1)
INTEGER: Final = (-(2**31), 2**31 - 1)
BIGINT: Final = (-(2**63), 2**63 - 1)
#: Longest decimal text converted (``int()`` refuses more than 4,300 digits; BIGINT has 19).
_MAX_DIGITS: Final = 20

_OCTET = r"(?:25[0-5]|2[0-4]\d|1\d\d|[1-9]?\d)"
#: Dotted-quad IPv4 without leading zeros (``ipaddress`` rejects them too).
_IPV4: Final = re.compile(rf"{_OCTET}(?:\.{_OCTET}){{3}}")
#: Six hex pairs separated consistently by ``:`` or ``-`` (RouterOS prints ``02:00:5E:00:53:5B``).
_MAC: Final = re.compile(r"[0-9A-Fa-f]{2}([:-])[0-9A-Fa-f]{2}(?:\1[0-9A-Fa-f]{2}){4}")


def to_int(text: str, bounds: tuple[int, int] = BIGINT) -> int | None:
    """Decimal integer within ``bounds`` (ASCII digits, optional ``-``), else ``None``.

    ``int()`` alone would also accept ``" 7"``, ``"+7"``, ``"1_000"`` and non-ASCII digits.
    """
    digits = text[1:] if text[:1] == "-" else text
    if not digits or len(digits) > _MAX_DIGITS or not (digits.isascii() and digits.isdigit()):
        return None
    number = int(text)
    low, high = bounds
    return number if low <= number <= high else None


def to_ip(text: str) -> str | None:
    """Canonical IP address text for an ``INET`` column, else ``None``.

    IPv4 must be a plain dotted quad; IPv6 is normalised by :mod:`ipaddress`. Zone ids
    (``fe80::1%eth0``) and prefixes are refused — PostgreSQL rejects the former and a log field
    never carries the latter.
    """
    if _IPV4.fullmatch(text) is not None:
        return text
    if ":" not in text or "%" in text or "/" in text:
        return None
    try:
        return str(ipaddress.IPv6Address(text))
    except ValueError:
        return None


def to_mac(text: str) -> str | None:
    """``02:00:5E:00:53:5B`` / ``02-00-5e-00-53-5b`` → ``02:00:5e:00:53:5b``, else ``None``.

    The PostgreSQL output layout (lower case, colons), the same normalisation as
    ``arcilogger_worker.lookup.normalize_mac``, so values compare equal everywhere.
    """
    if _MAC.fullmatch(text) is None:
        return None
    return text.lower().replace("-", ":")
