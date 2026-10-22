"""Raw spool lines → rows ready for ``COPY`` (pure: no files, no database, no shared state).

For every line of a chunk:

1. :func:`arcilogger_core.rawlog.parse_line` (receive time, sender, exact message bytes). A line
   that is not in the collector's format (e.g. zeros or other bytes left by file-system damage
   after a power cut: no time, a sender that is not ASCII, a receive time Python cannot express
   in UTC) cannot become a ``messages`` row — ``time_received`` and ``sender`` are NOT NULL and
   would have to be invented — so it is only counted as parse failure ``rawlog.invalid`` with
   its raw reference.
2. :func:`arcilogger_ingest.parsers.parse_line` with the folder's configured type (``None`` =
   detection by content). A parser error is counted under the parser's pattern.
3. :func:`arcilogger_db.rowcheck.check_row` on every row (``complete=True``). A typed row that
   fails is dropped and counted as ``rowcheck.<table>``; the ``messages`` row is still loaded
   and carries that pattern in ``parse_error``. If the ``messages`` row itself fails, the
   generic view of the line is tried instead, so a parser bug never costs the message.

**Nothing here raises for a line**, whatever its bytes: one line that crashed the service would
crash it again after every restart and stop ingest for every source (the line is evidence and is
never edited). An exception escaping steps 2 and 3 — a parser or row-check bug the parser's own
guard does not catch — is counted as ``prepare.exception`` (exception type in the detail) and
the generic ``messages`` row is loaded when it can be built; the service logs it at ERROR.

Everything here is a plain function of its arguments and returns picklable values, so the work
can move to a process pool unchanged if one core is ever not enough (docs/INGEST.md).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Final

from arcilogger_core.rawlog import RawLineError, RawRecord
from arcilogger_core.rawlog import parse_line as parse_raw
from arcilogger_core.timeutil import to_utc
from arcilogger_db.rowcheck import check_row
from arcilogger_ingest.parsers import LinePosition, SourceType, get_parser, parse_line

#: Longest ``parse_failures.error`` detail kept.
DETAIL_MAX: Final = 500
PATTERN_RAW: Final = "rawlog.invalid"
PATTERN_ROWCHECK: Final = "rowcheck."
#: An exception escaped the parser or the row checks for this line (a bug; logged at ERROR).
PATTERN_EXCEPTION: Final = "prepare.exception"


@dataclass(frozen=True, slots=True)
class StreamInfo:
    """The spool file a chunk comes from."""

    source: str
    source_type: SourceType | None
    day: date


@dataclass(frozen=True, slots=True)
class Failure:
    """One reason a line was not (fully) loaded, with its raw reference."""

    pattern: str
    line: int
    offset: int
    #: Receive time of the line (``None`` when the line itself is unreadable).
    seen: datetime | None
    detail: str | None


@dataclass(slots=True)
class PreparedLine:
    """One raw line: its position, the rows to load and the failures to count."""

    stream: StreamInfo
    line: int
    offset: int
    received: datetime | None = None
    sender: str | None = None
    #: Parser that handled the line (``fortigate``, ``mikrotik``, ``generic``).
    parser: str | None = None
    #: ``(table, values)``; the ``messages`` row (if any) first.
    rows: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    failures: list[Failure] = field(default_factory=list)


@dataclass(slots=True)
class PreparedChunk:
    """A run of consecutive lines of one stream and where the next line starts."""

    stream: StreamInfo
    lines: list[PreparedLine]
    #: Byte offset after the chunk and the number of lines up to its end (= ``raw_line`` of
    #: its last line): the values ``ingest_offsets`` stores once the chunk is loaded.
    end_offset: int
    end_line: int


def _detail(problems: Sequence[str]) -> str:
    return "; ".join(problems)[:DETAIL_MAX]


def split_lines(data: bytes) -> list[bytes]:
    """Lines of a chunk without their ``\\n`` (a final line without ``\\n`` counts as a line)."""
    if not data:
        return []
    lines = data.split(b"\n")
    if data.endswith(b"\n"):
        lines.pop()
    return lines


def prepare_line(stream: StreamInfo, number: int, offset: int, raw: bytes) -> PreparedLine:
    """Parse and check one raw line (without its ``\\n``); never raises for any input."""
    prepared = PreparedLine(stream=stream, line=number, offset=offset)
    try:
        record = parse_raw(raw)
        # Stored as UTC: e.g. year 1 at +03:00 has no UTC datetime (OverflowError). The
        # collector writes the current time, so such a stamp is damage, like a bad format.
        to_utc(record.received)
    except (RawLineError, ValueError, OverflowError) as exc:  # UnicodeDecodeError: bad sender
        detail = str(exc) if isinstance(exc, RawLineError) else f"{type(exc).__name__}: {exc}"
        prepared.failures.append(Failure(PATTERN_RAW, number, offset, None, detail[:DETAIL_MAX]))
        return prepared
    prepared.received = record.received
    prepared.sender = record.sender
    position = LinePosition(
        source=stream.source,
        source_type=stream.source_type,
        raw_day=stream.day,
        raw_line=number,
        raw_offset=offset,
    )
    try:
        _parse_and_check(prepared, record, position)
    except Exception as exc:  # a bug must cost one line, never stop ingest (module docstring)
        detail = f"{type(exc).__name__}: {exc}"[:DETAIL_MAX]
        prepared.rows.clear()
        prepared.failures = [Failure(PATTERN_EXCEPTION, number, offset, record.received, detail)]
        message = _generic_message(record, position, PATTERN_EXCEPTION)
        if message is not None:
            prepared.rows.append(("messages", message))
    return prepared


def _parse_and_check(prepared: PreparedLine, record: RawRecord, position: LinePosition) -> None:
    number, offset = prepared.line, prepared.offset
    outcome = parse_line(record, position)
    prepared.parser = outcome.parser
    if outcome.error is not None:
        prepared.failures.append(
            Failure(outcome.error, number, offset, record.received, outcome.detail)
        )
    message: dict[str, Any] | None = outcome.message
    problems = check_row("messages", outcome.message, complete=True)
    if problems:
        pattern = PATTERN_ROWCHECK + "messages"
        prepared.failures.append(
            Failure(pattern, number, offset, record.received, _detail(problems))
        )
        message = _generic_message(record, position, pattern)
    typed: list[tuple[str, dict[str, Any]]] = []
    for row in outcome.rows:
        problems = check_row(row.table, row.values, complete=True)
        if problems:
            pattern = PATTERN_ROWCHECK + row.table
            prepared.failures.append(
                Failure(pattern, number, offset, record.received, _detail(problems))
            )
            if message is not None and message.get("parse_error") is None:
                message["parse_error"] = pattern
        else:
            typed.append((row.table, dict(row.values)))
    if message is not None:
        prepared.rows.append(("messages", message))
    prepared.rows.extend(typed)


def _generic_message(
    record: RawRecord, position: LinePosition, pattern: str
) -> dict[str, Any] | None:
    """The generic parser's ``messages`` row, marked with ``pattern``; ``None`` if still invalid."""
    try:
        fallback = dict(get_parser("generic").parse(record, position).message)
        fallback["parse_error"] = pattern
        return None if check_row("messages", fallback, complete=True) else fallback
    except Exception:  # even the generic view failed: the failure is already counted
        return None


def prepare_chunk(stream: StreamInfo, offset: int, line_count: int, data: bytes) -> PreparedChunk:
    """Prepare every line of ``data``, which starts at byte ``offset`` after ``line_count`` lines.

    ``data`` holds complete lines; only the final line of a sealed (final) file may lack its
    ``\\n``, and the end offset then stops at the end of the data.
    """
    lines: list[PreparedLine] = []
    position = offset
    number = line_count
    for raw in split_lines(data):
        number += 1
        lines.append(prepare_line(stream, number, position, raw))
        position += len(raw) + 1
    if data and not data.endswith(b"\n"):
        position -= 1
    return PreparedChunk(stream=stream, lines=lines, end_offset=position, end_line=number)
