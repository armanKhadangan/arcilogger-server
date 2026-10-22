"""Generic parser: keeps the syslog header fields and the message text (spec 7.2)."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar

from arcilogger_core.rawlog import RawRecord
from arcilogger_ingest.parsers.base import LinePosition, Parsed, Parser, decode_message
from arcilogger_ingest.parsers.syslog import DEFAULT_MAX_CLOCK_SKEW, parse_header, plausible_time


def parse_generic(
    record: RawRecord, max_clock_skew: timedelta | None = DEFAULT_MAX_CLOCK_SKEW
) -> Parsed:
    """The generic view of any line: header fields + text, ``messages`` only.

    A header time without an offset is not guessed; one further than ``max_clock_skew`` from
    the receive time (or not representable in UTC) is not used either and makes the line a
    ``generic.time`` failure. Either way the receive time is used and the text is kept in
    ``extra["device_time"]``.
    """
    header = parse_header(decode_message(record.message))
    extra: dict[str, Any] = {}
    time = None
    error = detail = None
    if header.time_text is not None:
        if header.time is None:
            extra["device_time"] = header.time_text  # no offset: not used as event time
        else:
            time = plausible_time(header.time, record.received, max_clock_skew)
            if time is None:
                extra["device_time"] = header.time_text
                error, detail = "generic.time", "implausible device time"
    return Parsed(
        message={
            "type": "syslog",
            "subtype": None,  # every parser emits every column (copy_rows uses the first row's)
            "severity": header.severity,
            "facility": header.facility,
            "host": header.host,
            "program": header.program,
            "text": header.rest,
            "extra": extra,
        },
        time=time,
        error=error,
        detail=detail,
    )


class GenericParser(Parser):
    name: ClassVar[str] = "generic"
    tables: ClassVar[frozenset[str]] = frozenset()

    def __init__(self, *, max_clock_skew: timedelta | None = DEFAULT_MAX_CLOCK_SKEW) -> None:
        #: Largest accepted distance between header time and receive time (``None`` = any).
        self.max_clock_skew = max_clock_skew

    def parse_record(self, record: RawRecord, position: LinePosition) -> Parsed:
        return parse_generic(record, self.max_clock_skew)
