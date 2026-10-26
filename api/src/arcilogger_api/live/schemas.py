"""Requests, answers and stream events of ``/api/v1/live`` (contract 2.14.4).

The request body is strict (``extra="forbid"``). The events of ``GET /live/streams/{id}`` travel
as server-sent events, one JSON object per ``data:`` line; their shapes are declared here too
(:class:`LiveEvents`, the documented answer of that endpoint), so the web app's types are
checked against them like any other answer (``web/src/generated/api-schemas.json``).

:class:`RawRef` and :class:`SearchSummary` are the shapes Arama gives a raw reference and a
row's typed fields (``search/schemas.py`` of the search API, contract 2.10): a live line, a
Kaynaklar parse failure and the Panel's parse-failure box carry the very same objects.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

#: Longest query (the search language's own limit, ``search.LIMITS["max_chars"]``).
QUERY_MAX = 1000
#: Most lines a stream can ask to see from before it opened (``live.backfill_lines`` ≤ 200).
BACKFILL_MAX = 200


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Answer(BaseModel):
    model_config = ConfigDict(populate_by_name=True)


# -- shared with Arama ----------------------------------------------------------------------------


class RawRef(Answer):
    """A raw line: line ``line`` of ``spool/<source>/<day>.log`` (or the sealed file), which
    starts at byte ``offset``."""

    source: str
    day: date
    line: int
    offset: int


class SearchSummary(Answer):
    """The typed fields of a row, for the "Özet" column (only those the row has)."""

    srcip: str | None = None
    srcport: int | None = None
    dstip: str | None = None
    dstport: int | None = None
    natip: str | None = None
    natport: int | None = None
    action: str | None = None
    domain: str | None = None
    url: str | None = None
    app: str | None = None
    category: str | None = None
    user: str | None = None
    mac: str | None = None
    proto: str | None = None
    policy: str | None = None
    interface: str | None = None
    vlan_id: int | None = None


# -- requests and answers -------------------------------------------------------------------------


class LiveStreamIn(StrictModel):
    #: A query of the search language (empty: every line); ``from``/``to`` do not apply.
    q: Annotated[str, StringConstraints(max_length=QUERY_MAX)] = ""
    #: Matching lines from before the stream opened, shown first: at most
    #: ``live.backfill_lines``, which is also the default.
    backfill: Annotated[int, Field(ge=0, le=BACKFILL_MAX)] | None = None


class LiveFolder(Answer):
    """A spool folder the stream follows: lines after ``start_line`` of its ``day`` are new."""

    folder: str
    day: date
    start_line: int


class LiveLimits(Answer):
    max_lines_per_second: int
    keepalive_seconds: int
    buffer_lines: int
    max_minutes: int


class LiveStreamOut(Answer):
    stream_id: str
    #: Changes when the API restarts; ``Last-Event-ID`` is ``<epoch>:<seq>``.
    epoch: str
    #: When the stream ends with ``time_limit`` unless it is closed before.
    expires_at: datetime
    folders: list[LiveFolder]
    limits: LiveLimits


class LiveHubStatus(Answer):
    running: bool
    #: Spool files followed.
    files: int
    lines_per_second: float
    parsed_per_second: float
    #: Lines not parsed (parse budget) since the hub started.
    skipped_total: int


class LiveStatus(Answer):
    streams: int
    max_streams: int
    hub: LiveHubStatus


# -- stream events --------------------------------------------------------------------------------


class LiveHello(Answer):
    """``event: hello`` — the first event of every connection."""

    stream_id: str
    epoch: str
    #: The KVKK notice version the viewer accepted (a newer one ends the stream).
    notice_version: str
    folders: list[LiveFolder]
    limits: LiveLimits


class LiveLine(Answer):
    """``event: line`` (``id: <epoch>:<seq>``) — one raw line of the spool.

    ``parsed`` fields (``kind``, ``type``, ``subtype``, ``summary``, ``parse_error``) are a
    preview: ingest's row may differ in details. ``parsed: false`` with ``parse_skipped: true``:
    the server had no parse budget left for the line; ``parse_error: "rawlog.invalid"``: the
    line is not in the collector's format ("biçim dışı"). The whole line: ``GET /raw`` with
    ``source``, ``day``, ``line``, ``offset``.
    """

    seq: int
    received: datetime | None
    #: The spool folder (the source name ingest gives the line).
    source: str
    sender: str | None
    day: date
    line: int
    offset: int
    kind: str | None
    type: str | None
    subtype: str | None
    summary: SearchSummary
    #: The message, display-safe (escapes visible), at most 8 KiB.
    text: str
    truncated: bool
    parse_error: str | None
    parsed: bool
    parse_skipped: bool


class LiveDropped(Answer):
    """``event: dropped`` (at most once a second) — matching lines not shown since ``since``."""

    rate_limited: int
    slow_client: int
    parse_budget: int
    behind: int
    since: datetime
    message_tr: str
    message_en: str


class LiveGap(Answer):
    """``event: gap`` — lines in between may be missing (open them in Arama)."""

    reason: Literal["resume_too_late", "hub_restarted", "file_replaced"]
    #: Matching lines known to be missing; ``null`` when the server cannot tell.
    missed: int | None
    message_tr: str
    message_en: str


class LiveEnd(Answer):
    """``event: end`` — the stream is closed; the web app does not reconnect."""

    code: str
    message_tr: str
    message_en: str


class LiveEvents(Answer):
    """The ``data:`` of each event of ``GET /live/streams/{id}`` (``text/event-stream``)."""

    hello: LiveHello
    line: LiveLine
    dropped: LiveDropped
    gap: LiveGap
    end: LiveEnd
