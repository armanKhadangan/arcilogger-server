"""The parser interface: one raw spool line in, normalised database rows out.

Contract (docs/MVP-CONTRACTS.md, "Parsers"):

* Input: the :class:`~arcilogger_core.rawlog.RawRecord` of one spool line (receive time, sender
  IP, the exact received bytes) and its :class:`LinePosition` (source name and type, spool day,
  1-based line number, byte offset of the line start).
* A concrete parser implements :meth:`Parser.parse_record` and returns :class:`Parsed` with only
  what it understood: the ``messages`` fields (type, subtype, severity, facility, program, host,
  text), the device time, typed rows (:class:`ParsedRow`, keys = column names of the table) and,
  when it could not understand the line, an error/pattern name.
* :meth:`Parser.parse` (do not override) completes every row with the common columns — ``time``,
  ``time_received``, ``source``, ``sender``, ``raw_day``, ``raw_line``, ``raw_offset``, ``extra``
  — and turns a parser exception into a parse failure instead of stopping ingest.
* The ingest service loads :attr:`ParseOutcome.message` into ``messages`` and every
  :attr:`ParseOutcome.rows` entry into its table; ``error`` goes to ``parse_failures``.

Text handed to the database is derived from :func:`decode_message`, which never contains NUL.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar, Final, Literal

from arcilogger_core.rawlog import RawRecord
from arcilogger_core.timeutil import to_utc

SourceType = Literal["fortigate", "mikrotik", "generic"]
SOURCE_TYPES: Final[tuple[SourceType, ...]] = ("fortigate", "mikrotik", "generic")

#: ``messages.text`` keeps at most this many characters (the raw line keeps everything).
MESSAGE_TEXT_MAX: Final = 2000
#: URLs are truncated to this many characters (spec 7.3 ``fg_web.url``).
URL_MAX: Final = 2000
#: Columns the framework fills; a parser's own values never override the raw reference.
RAW_REFERENCE_COLUMNS: Final = (
    "time_received",
    "source",
    "sender",
    "raw_day",
    "raw_line",
    "raw_offset",
)


@dataclass(frozen=True, slots=True)
class LinePosition:
    """Where a raw line is: ``spool/<source>/<raw_day>.log`` line ``raw_line`` at byte offset."""

    source: str
    #: Configured type of the source; ``None`` for an unknown sender (detected by content).
    source_type: SourceType | None
    raw_day: date
    raw_line: int
    raw_offset: int

    def __post_init__(self) -> None:
        if self.raw_line < 1:
            raise ValueError("raw_line is 1-based")
        if self.raw_offset < 0:
            raise ValueError("raw_offset must not be negative")


@dataclass(frozen=True, slots=True)
class ParsedRow:
    """One row for ``table``; keys of ``values`` are column names of that table."""

    table: str
    values: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class Parsed:
    """What a concrete parser understood (common columns are added by :meth:`Parser.parse`)."""

    #: ``messages`` columns: ``type`` (required), ``subtype``, ``severity``, ``facility``,
    #: ``program``, ``host``, ``text``, ``extra``.
    message: Mapping[str, Any]
    rows: tuple[ParsedRow, ...] = ()
    #: Device/event time (aware); ``None`` = the receive time is used.
    time: datetime | None = None
    #: Pattern/error name when the line was not (fully) understood, e.g. ``fortigate.kv``.
    error: str | None = None
    #: Short detail for ``parse_failures.error`` (no secrets; the raw line is the evidence).
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    """Complete rows for one raw line, ready for loading."""

    parser: str
    #: The complete ``messages`` row.
    message: dict[str, Any]
    #: Complete typed rows.
    rows: tuple[ParsedRow, ...] = field(default_factory=tuple)
    error: str | None = None
    detail: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None

    def all_rows(self) -> tuple[ParsedRow, ...]:
        """``messages`` first, then the typed rows."""
        return (ParsedRow("messages", self.message), *self.rows)


def decode_message(data: bytes) -> str:
    """Received bytes as text: UTF-8 (invalid bytes → U+FFFD), NUL → U+FFFD.

    PostgreSQL text cannot hold NUL; the exact bytes stay in the raw spool line.
    """
    return data.decode("utf-8", "replace").replace("\x00", "�")


def truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit]


class Parser(ABC):
    """Base class of every parser (``fortigate``, ``mikrotik``, ``generic``)."""

    #: Registry name; equals the source type it handles.
    name: ClassVar[str]
    #: Typed tables this parser may emit (checked against the models by a contract test).
    tables: ClassVar[frozenset[str]] = frozenset()

    @abstractmethod
    def parse_record(self, record: RawRecord, position: LinePosition) -> Parsed:
        """Parser-specific work; may raise — :meth:`parse` turns that into a parse failure."""

    def fallback(self, record: RawRecord, position: LinePosition) -> Parsed:
        """Result used when :meth:`parse_record` raised: the generic syslog view of the line."""
        from arcilogger_ingest.parsers.generic import parse_generic  # circular at import time

        return parse_generic(record)

    def parse(self, record: RawRecord, position: LinePosition) -> ParseOutcome:
        """Parse one line and complete every row (never raises for bad input)."""
        try:
            parsed = self.parse_record(record, position)
        except Exception as exc:  # a parser bug must not stop ingest; the line is counted
            generic = self.fallback(record, position)
            parsed = Parsed(
                message=generic.message,
                time=generic.time,
                error=f"{self.name}.exception",
                detail=f"{type(exc).__name__}: {exc}"[:500],
            )
        return complete(self.name, parsed, record, position)


def _describe(value: datetime) -> str:
    try:
        return value.isoformat()
    except (OverflowError, ValueError):  # a tzinfo whose utcoffset() fails
        return repr(value)


def complete(
    parser: str, parsed: Parsed, record: RawRecord, position: LinePosition
) -> ParseOutcome:
    """Add the common columns to the message row and to every typed row.

    Never raises for a bad device time: a parser should only return plausible times
    (``syslog.plausible_time``), but a time that cannot be converted to UTC (year 1 with a
    positive offset, year 9999 with a negative one, a naive value) must not stop ingest — this
    runs outside the ``try`` of :meth:`Parser.parse`, and ingest would fail on that line again
    after every restart. The receive time is used instead, the device time is kept in
    ``messages.extra["device_time"]`` and the line is counted as ``<parser>.time``.
    """
    received = to_utc(record.received)  # a naive receive time is a caller bug: raise
    time = received
    error, detail = parsed.error, parsed.detail
    message_values = parsed.message
    if parsed.time is not None:
        try:
            time = to_utc(parsed.time)
        except (OverflowError, ValueError):  # NaiveDatetimeError is a ValueError
            extra = dict(message_values.get("extra") or {})
            extra.setdefault("device_time", _describe(parsed.time))
            message_values = {**message_values, "extra": extra}
            if error is None:
                error, detail = f"{parser}.time", "device time not representable in UTC"
    raw_reference = {
        "time_received": received,
        "source": position.source,
        "sender": record.sender,
        "raw_day": position.raw_day,
        "raw_line": position.raw_line,
        "raw_offset": position.raw_offset,
    }
    message: dict[str, Any] = {
        "type": "unknown",
        "text": "",
        "extra": {},
        **message_values,
        "time": time,
        "parse_error": error,
        **raw_reference,
    }
    message["text"] = truncate(str(message["text"]), MESSAGE_TEXT_MAX)
    rows = tuple(
        ParsedRow(row.table, {"time": time, "extra": {}, **row.values, **raw_reference})
        for row in parsed.rows
    )
    return ParseOutcome(parser=parser, message=message, rows=rows, error=error, detail=detail)
