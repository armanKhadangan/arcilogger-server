"""Tolerant syslog header parsing shared by the parsers.

Accepted (each part optional, in this order): ``<PRI>``; an RFC 5424 header
(``1 TIMESTAMP HOST APP PROCID MSGID SD``); an ISO-8601 timestamp (MikroTik
``syslog-time-format=iso8601``, with or without fraction/offset); a BSD timestamp
(``Sep 23 09:44:57``). Device times are used only when they carry an offset: a time without
one would have to be guessed and stays in ``extra["device_time"]`` instead. A device time is
also not used when it is implausible — further than ``max_skew`` from the receive time, or not
representable as a UTC ``datetime`` at all (:func:`plausible_time`).

The helpers are building blocks: :func:`parse_header` (generic parser) puts them together the
classic way; the MikroTik parser combines them with its own host/topics rules.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

_RFC5424 = re.compile(r"^1 (\S+) (\S+) (\S+) (\S+) (\S+) ")
_ISO = re.compile(
    r"^(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,9})?(?:Z|[+-]\d{2}:?\d{2})?)(?: |$)",
    re.ASCII,
)
_BSD = re.compile(r"^([A-Z][a-z]{2} [ \d]\d \d{2}:\d{2}:\d{2})(?: |$)", re.ASCII)
_TAG = re.compile(r"^([^\s:\[]+)(?:\[\d+\])?: ")
_NIL: Final = "-"

#: Largest accepted distance between a device time and the receive time (both directions).
#: Every device syncs NTP from the local time source (spec 2.4), so a larger distance is a
#: broken clock (a router booted at 1970, a wrong year, a misread ``eventtime`` unit), not a
#: delayed message: the receive time is used and the line is counted as ``<parser>.time``.
#: Generous on purpose — a few hours of skew (wrong time zone on a device) is a clock-check
#: matter, not a parser one (docs/PARSERS.md §7, verify on site).
DEFAULT_MAX_CLOCK_SKEW: Final = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class SyslogHeader:
    facility: int | None
    severity: int | None
    #: Aware device time, or ``None`` when absent or without a time-zone offset.
    time: datetime | None
    #: Timestamp text exactly as sent (also when it could not be used).
    time_text: str | None
    host: str | None
    program: str | None
    #: Everything after the header.
    rest: str


@dataclass(frozen=True, slots=True)
class Rfc5424:
    """The RFC 5424 header fields after the PRI (NIL ``-`` → ``None``)."""

    time_text: str | None
    host: str | None
    app: str | None
    #: The message after the structured data.
    rest: str


def split_pri(text: str) -> tuple[int | None, int | None, str]:
    """``(facility, severity, rest)``; PRI is 1-3 ASCII digits, 0-191 (RFC 5424).

    String operations instead of a regular expression: this runs for every received line.
    """
    if text[:1] != "<":
        return None, None, text
    end = text.find(">", 1, 5)
    digits = text[1:end]
    if end < 2 or not (digits.isascii() and digits.isdigit()):
        return None, None, text
    pri = int(digits)
    if pri > 191:
        return None, None, text
    return pri >> 3, pri & 7, text[end + 1 :]


def split_rfc5424(text: str) -> Rfc5424 | None:
    """The RFC 5424 header of ``text`` (after the PRI), or ``None`` when it is not one."""
    match = _RFC5424.match(text)
    if match is None:
        return None
    stamp, host, app = match.group(1), match.group(2), match.group(3)
    rest = text[match.end() :]
    if rest.startswith(_NIL + " ") or rest == _NIL:
        rest = rest[2:]
    elif rest.startswith("["):
        end = rest.find("] ")
        rest = rest[end + 2 :] if end >= 0 else ""
    return Rfc5424(
        None if stamp == _NIL else stamp,
        None if host == _NIL else host,
        None if app == _NIL else app,
        rest,
    )


def split_timestamp(text: str) -> tuple[str | None, str]:
    """``(timestamp text, rest)`` for a leading ISO-8601 or BSD timestamp, else ``(None, text)``.

    The ISO form is what MikroTik sends with ``syslog-time-format=iso8601`` (fraction and offset
    optional); the BSD form (``Sep 23 09:44:57``) has no year and no offset.
    """
    for pattern in (_ISO, _BSD):
        match = pattern.match(text)
        if match is not None:
            return match.group(1), text[match.end() :]
    return None, text


def parse_time(text: str) -> datetime | None:
    """Aware datetime from ISO-8601 text with an offset; ``None`` otherwise.

    Python 3.11+ ``fromisoformat`` reads ``Z``, ``,`` as decimal separator, ``+0300`` and more
    than six fraction digits (truncated to microseconds) itself.
    """
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    return value if value.utcoffset() is not None else None


def plausible_time(
    device: datetime, received: datetime, max_skew: timedelta | None = DEFAULT_MAX_CLOCK_SKEW
) -> datetime | None:
    """``device`` in UTC when it can be used as the event time, else ``None``.

    Usable means aware, representable in UTC (``0001-01-01T00:00+03:00`` is not: converting it
    raises ``OverflowError``, which used to escape ``Parser.parse``) and — unless ``max_skew``
    is ``None`` — within ``max_skew`` of ``received``. Aware subtraction never overflows, so
    the distance is checked first.
    """
    try:
        if device.utcoffset() is None:
            return None
        if max_skew is not None and abs(device - received) > max_skew:
            return None
        return device.astimezone(UTC)
    except (OverflowError, ValueError):
        return None


def parse_header(text: str) -> SyslogHeader:
    """Split a syslog message into header fields and the rest (never raises)."""
    facility, severity, rest = split_pri(text)
    rfc5424 = split_rfc5424(rest)
    if rfc5424 is not None:
        stamp = rfc5424.time_text
        return SyslogHeader(
            facility,
            severity,
            parse_time(stamp) if stamp is not None else None,
            stamp,
            rfc5424.host,
            rfc5424.app,
            rfc5424.rest,
        )
    time_text, rest = split_timestamp(rest)
    host = program = None
    if time_text is not None:
        host, _, rest = rest.partition(" ")
        tag = _TAG.match(rest)
        if tag is not None:
            program, rest = tag.group(1), rest[tag.end() :]
    return SyslogHeader(
        facility,
        severity,
        parse_time(time_text) if time_text else None,
        time_text,
        host or None,
        program,
        rest,
    )
