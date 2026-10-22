"""The raw spool line format — the legal evidence as written by the collector.

One received syslog message = one line::

    <receive time, RFC 3339, microseconds, +03:00> <sender IP> <message, escaped>\\n

Escaping is **reversible**: every byte below 0x20, DEL (0x7F) and ``#`` itself is written as
``#`` followed by three octal digits (``\\n`` → ``#012``, ``#`` → ``#043``). All other bytes,
including non-UTF-8 bytes, are written unchanged. :func:`unescape` therefore restores the exact
bytes that arrived on the wire, and a message can never break the one-line-per-message rule.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime

from arcilogger_core.timeutil import NaiveDatetimeError, isoformat_site, require_aware

_ESCAPE = re.compile(rb"[\x00-\x1f\x7f#]")
_UNESCAPE = re.compile(rb"#([0-7]{3})")
_BAD_HASH = re.compile(rb"#(?![0-7]{3})")


class RawLineError(ValueError):
    """A spool line does not follow the documented format."""


@dataclass(frozen=True, slots=True)
class RawRecord:
    received: datetime
    sender: str
    message: bytes


def escape(message: bytes) -> bytes:
    """Escape control characters and ``#`` as ``#ooo`` (octal)."""
    return _ESCAPE.sub(lambda m: b"#%03o" % m.group()[0], message)


def unescape(data: bytes) -> bytes:
    """Inverse of :func:`escape`; raises :class:`RawLineError` on a malformed escape."""
    if _BAD_HASH.search(data):
        raise RawLineError("'#' not followed by three octal digits")
    if re.search(rb"[\x00-\x1f\x7f]", data):
        raise RawLineError("unescaped control character")
    return _UNESCAPE.sub(lambda m: bytes([int(m.group(1), 8)]), data)


def format_line(received: datetime, sender: str, message: bytes) -> bytes:
    """Build one spool line (with the trailing ``\\n``)."""
    if " " in sender or not sender:
        raise ValueError(f"invalid sender: {sender!r}")
    stamp = isoformat_site(require_aware(received), "microseconds")
    return f"{stamp} {sender} ".encode("ascii") + escape(message) + b"\n"


def parse_line(line: bytes) -> RawRecord:
    """Parse one spool line (with or without the trailing ``\\n``)."""
    body = line[:-1] if line.endswith(b"\n") else line
    parts = body.split(b" ", 2)
    if len(parts) != 3:
        raise RawLineError("expected '<time> <sender> <message>'")
    stamp, sender, escaped = parts
    try:
        received = require_aware(datetime.fromisoformat(stamp.decode("ascii")))
    except (UnicodeDecodeError, ValueError, NaiveDatetimeError) as exc:
        raise RawLineError(f"bad receive time: {stamp!r}") from exc
    try:
        sender_text = sender.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RawLineError(f"bad sender: {sender!r}") from exc
    return RawRecord(received=received, sender=sender_text, message=unescape(escaped))


def received_at(line: bytes) -> datetime:
    """Fast path: only the receive time of a spool line."""
    end = line.find(b" ")
    if end <= 0:
        raise RawLineError("missing receive time")
    try:
        return require_aware(datetime.fromisoformat(line[:end].decode("ascii")))
    except (UnicodeDecodeError, ValueError, NaiveDatetimeError) as exc:
        raise RawLineError(f"bad receive time: {line[:end]!r}") from exc
