"""The lines the hub keeps and the server-sent events a stream writes (contract 2.14.4).

An :class:`Entry` is one spool line as the hub read it: its raw reference, its receive time and
sender, and — when the parse budget allowed — what ingest's own ``prepare_line`` makes of it
(the :class:`~arcilogger_api.search.matcher.Record` the stream filters match). Its ``line``
event is built once, when the first stream sends it, and shared by every stream (the sequence
number belongs to the hub, not to a stream).

Events are ``text/event-stream`` frames with one JSON object per ``data:`` line
(``json.dumps(..., ensure_ascii=False)``; JSON never contains a raw line feed, and the display
text has no line or paragraph separators either), ``id: <epoch>:<seq>`` on ``line`` events
only, and the comment ``: ka`` as keep-alive.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final, Literal

from arcilogger_api.rawlines import display_text
from arcilogger_api.search import windows
from arcilogger_api.search.fields import SiteNetwork
from arcilogger_api.search.matcher import Record

#: The message of a ``line`` event is cut to this many bytes (``truncated``); ``GET /raw``
#: has the whole line.
TEXT_MAX_BYTES: Final = 8 * 1024
#: Bytes of a spool line the hub keeps (the display text and the free-text pre-filter of
#: later streams; ``messages.text`` holds at most 2,000 characters of it).
RAW_KEEP_BYTES: Final = 16 * 1024
#: Characters of one summary field.
SUMMARY_TEXT_MAX: Final = 500
KEEPALIVE: Final = b": ka\n\n"

#: ``parsed``: ``prepare_line`` ran (``record`` is ``None`` only when even the generic row
#: failed); ``skipped``: not parsed (parse budget, or no open stream could match it);
#: ``invalid``: not in the collector's format (``rawlog.invalid``).
State = Literal["parsed", "skipped", "invalid"]


@dataclass(slots=True, eq=False)
class Entry:
    """One spool line in the hub's buffer."""

    seq: int
    folder: str
    day: date
    line: int
    offset: int
    #: The line as stored, at most :data:`RAW_KEEP_BYTES`.
    raw: bytes
    #: The line is longer than ``raw``.
    cut: bool
    received: datetime | None
    sender: str | None
    state: State
    record: Record | None = None
    kind: str | None = None
    parse_error: str | None = None
    #: Read from the end of a file the hub attached for a new stream: never sent live.
    seeded: bool = False
    _frame: bytes | None = None
    _folded: bytes | None = None

    @property
    def folded(self) -> bytes:
        """The kept bytes with ASCII letters lower-cased (the free-text byte search)."""
        if self._folded is None:
            self._folded = self.raw.lower()
        return self._folded

    def frame(self, epoch: str, site: SiteNetwork) -> bytes:
        """The ``line`` event (built once)."""
        if self._frame is None:
            self._frame = frame("line", line_data(self, site), event_id=f"{epoch}:{self.seq}")
        return self._frame


def frame(event: str, data: Mapping[str, Any], *, event_id: str | None = None) -> bytes:
    """One server-sent event."""
    head = f"id: {event_id}\n" if event_id is not None else ""
    body = json.dumps(data, ensure_ascii=False, separators=(",", ":"), default=str)
    return f"{head}event: {event}\ndata: {body}\n\n".encode()


def _cut(data: bytes, limit: int) -> bytes:
    """At most ``limit`` bytes, not ending inside a UTF-8 character."""
    if len(data) <= limit:
        return data
    end = limit
    while end > 0 and limit - end < 3 and data[end] & 0xC0 == 0x80:
        end -= 1
    return data[:end]


def message_part(raw: bytes) -> bytes:
    """The escaped message of a spool line (the whole line when it is not in spool format)."""
    parts = raw.split(b" ", 2)
    return parts[2] if len(parts) == 3 else raw


def summary(table: str | None, row: Mapping[str, Any] | None, site: SiteNetwork) -> dict[str, Any]:
    """The ``summary`` of a typed row: Arama's own (``search.windows.summary``), so a live line
    and a search row of the same line agree, the VLAN included."""
    return windows.summary(table, row, site, SUMMARY_TEXT_MAX)


def line_data(entry: Entry, site: SiteNetwork) -> dict[str, Any]:
    """The ``data`` of a ``line`` event (``LiveLine``)."""
    record = entry.record
    message = record.message if record is not None else None
    text = message_part(entry.raw) if entry.state != "invalid" else entry.raw
    shown = _cut(text, TEXT_MAX_BYTES)
    return {
        "seq": entry.seq,
        "received": entry.received.isoformat() if entry.received is not None else None,
        "source": entry.folder,
        "sender": entry.sender,
        "day": entry.day.isoformat(),
        "line": entry.line,
        "offset": entry.offset,
        "kind": entry.kind,
        "type": message.get("type") if message is not None else None,
        "subtype": message.get("subtype") if message is not None else None,
        "summary": summary(record.table, record.row, site) if record is not None else {},
        "text": display_text(shown),
        "truncated": len(shown) < len(text) or entry.cut,
        "parse_error": entry.parse_error,
        "parsed": entry.state == "parsed" and record is not None,
        "parse_skipped": entry.state == "skipped",
    }
