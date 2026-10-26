"""``/api/v1/live`` — Canlı Akış, the live tail of the raw spool (spec 12.2, contract 2.14).

Two steps (R7): ``POST /live/streams`` checks everything that can be refused — the query, the
limits, the KVKK notice — answers in plain JSON, audits ``live.open`` and returns a
``stream_id``; ``GET /live/streams/{stream_id}`` then streams ``text/event-stream`` and holds no
database session. The web app reads it with ``fetch`` and ``X-Arci-Background: 1``: the stream,
its keep-alives and its reconnects never count as activity.

=================================  ==========================================  ==============
Endpoint                           Permission                                  Audit
=================================  ==========================================  ==============
``POST /live/streams``             ``search.read`` + KVKK notice, CSRF         ``live.open``
``GET /live/streams/{id}``         ``search.read``; the stream must belong to  —
                                   this user **and** session (else 404)
``DELETE /live/streams/{id}``      the same, CSRF                              ``live.close``
``GET /live/status``               ``search.read`` (support; no person data)   —
=================================  ==========================================  ==============

Limits (settings ``live.*``): ``live.max_streams_total`` streams in the process and
``live.max_streams_per_user`` per user (429 ``live_stream_limit``; a paused stream keeps its
slot for 120 s), ``live.max_lines_per_second`` per stream, ``live.max_minutes`` per stream
(``end {time_limit}``). No licence module gates any of it (5651 Temel). See
:mod:`arcilogger_api.live` for the hub and the stream.
"""

from __future__ import annotations

import secrets
from typing import Annotated, Final

from fastapi import APIRouter, Depends, Header, Path, Request, Response
from starlette.concurrency import run_in_threadpool

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.accounts import privacy_version
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.deps import ConfigDep, DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.live.hub import (
    Connection,
    Hub,
    LiveSettings,
    LiveUnavailableError,
    Stream,
    hub_of,
)
from arcilogger_api.live.messages import live_error
from arcilogger_api.live.schemas import LiveEvents, LiveStatus, LiveStreamIn, LiveStreamOut
from arcilogger_api.live.stream import EventStreamResponse, events
from arcilogger_api.search import SearchQueryError, SiteNetwork, compile_matcher, parse_query
from arcilogger_api.search.messages import query_error
from arcilogger_api.security import CurrentUser, client_ip, require

router = APIRouter(prefix="/live", tags=["live"])

OpenerDep = Annotated[CurrentUser, Depends(require_privacy_notice("search.read"))]
ReaderDep = Annotated[CurrentUser, Depends(require("search.read"))]
#: A ``stream_id`` is ``secrets.token_urlsafe(16)``; anything far longer is no stream of ours.
StreamId = Annotated[str, Path(min_length=1, max_length=64)]
LAST_EVENT_ID_MAX: Final = 128


def _own_stream(hub: Hub, stream_id: str, user: CurrentUser) -> Stream:
    """The open stream ``stream_id`` of this user's session (404 for anyone else's)."""
    stream = hub.get(stream_id)
    if (
        stream is None
        or stream.open_seq is None
        or stream.user_id != user.id
        or stream.token_hash != user.session_token_hash
    ):
        raise live_error("live_stream_not_found")
    return stream


def last_event(value: str | None) -> tuple[str, int] | None:
    """``Last-Event-ID: <epoch>:<seq>`` (``None``: absent or not ours)."""
    if not value:
        return None
    epoch, _, seq = value.strip().rpartition(":")
    if not epoch or not seq.isascii() or not seq.isdigit():
        return None
    return epoch, int(seq)


@router.post("/streams", status_code=201)
async def open_stream(
    body: LiveStreamIn, request: Request, db: DbSession, config: ConfigDep, user: OpenerDep
) -> LiveStreamOut:
    """Open a live tail of the spool lines ``q`` matches (refusals are JSON errors)."""
    hub = hub_of(request.app)
    settings = await run_in_threadpool(LiveSettings.load, db)
    backfill = settings.backfill_lines if body.backfill is None else body.backfill
    if backfill > settings.backfill_lines:
        raise ApiError(
            422,
            "validation_error",
            details=[
                {
                    "loc": ["body", "backfill"],
                    "type": "less_than_equal",
                    "msg": f"at most {settings.backfill_lines} (live.backfill_lines)",
                }
            ],
        )
    try:
        query = parse_query(body.q)
    except SearchQueryError as exc:
        raise query_error(exc) from None
    site = await run_in_threadpool(SiteNetwork.load, db, config)
    try:
        matcher = compile_matcher(query, site)
    except SearchQueryError as exc:
        raise query_error(exc) from None
    total, mine = hub.count_streams(user.id)
    if total >= settings.max_streams_total or mine >= settings.max_streams_per_user:
        raise live_error(
            "live_stream_limit",
            per_user=settings.max_streams_per_user,
            total=settings.max_streams_total,
        )
    version = await run_in_threadpool(privacy_version, db)
    opened_at = hub.now()
    stream = Stream(
        id=secrets.token_urlsafe(16),
        user_id=user.id,
        username=user.username,
        token_hash=user.session_token_hash,
        ip=client_ip(request),
        query=query.normalized,
        matcher=matcher,
        backfill=backfill,
        settings=settings,
        notice_version=version,
        opened_at=opened_at,
        opened_clock=hub.clock(),
        clock=hub.clock,
    )
    try:
        folders = await hub.open(stream, settings, site)
    except LiveUnavailableError:
        raise live_error("live_unavailable") from None
    try:
        await run_in_threadpool(
            lambda: record_audit(
                db,
                actor=user,
                action="live.open",
                target=f"live:{stream.id}",
                details={
                    "stream_id": stream.id,
                    "q": query.normalized,
                    "folders": [folder["folder"] for folder in folders],
                    "backfill": backfill,
                },
                ip=stream.ip,
            )
        )
    except BaseException:
        stream.closed = "not_opened"
        hub.streams.pop(stream.id, None)
        raise
    return LiveStreamOut.model_validate(
        {
            "stream_id": stream.id,
            "epoch": hub.epoch,
            "expires_at": stream.expires_at,
            "folders": folders,
            "limits": {
                "max_lines_per_second": settings.max_lines_per_second,
                "keepalive_seconds": int(hub.timing.keepalive),
                "buffer_lines": settings.buffer_lines,
                "max_minutes": settings.max_minutes,
            },
        }
    )


@router.get(
    "/streams/{stream_id}",
    response_class=EventStreamResponse,
    responses={
        200: {
            "model": LiveEvents,
            "description": "Server-sent events: the ``data`` of each event type.",
            "content": {"text/event-stream": {}},
        }
    },
)
async def stream_events(
    stream_id: StreamId,
    request: Request,
    user: ReaderDep,
    last_event_id: Annotated[
        str | None, Header(alias="Last-Event-ID", max_length=LAST_EVENT_ID_MAX)
    ] = None,
) -> EventStreamResponse:
    """The stream's events; ``Last-Event-ID`` resumes after a pause or a lost connection."""
    hub = hub_of(request.app)
    stream = _own_stream(hub, stream_id, user)
    connection = Connection()
    replay = hub.attach(stream, connection, last_event(last_event_id))
    return EventStreamResponse(
        events(hub, stream, connection, replay), headers={"X-Accel-Buffering": "no"}
    )


@router.delete("/streams/{stream_id}", status_code=204)
async def close_stream(stream_id: StreamId, request: Request, user: ReaderDep) -> Response:
    """Close the stream (``live.close``)."""
    hub = hub_of(request.app)
    stream = _own_stream(hub, stream_id, user)
    await hub.close(stream, "closed")
    return Response(status_code=204)


@router.get("/status")
async def live_status(request: Request, user: ReaderDep) -> LiveStatus:
    """Open streams and what the hub reads (no person data)."""
    return LiveStatus.model_validate(hub_of(request.app).status())
