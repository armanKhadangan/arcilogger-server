"""One open ``GET /live/streams/{id}``: its events, its session check and its end (contract
2.14.4-2.14.6, R7, R12, R16).

The endpoint authenticates through its dependencies and then returns an
:class:`EventStreamResponse` built by hand; its request's database session is closed before the
body streams (``DbSession`` has ``scope="function"``), so an open stream holds no database
connection. The body (:func:`events`):

1. ``hello``, then the session check, then what :meth:`Hub.attach` prepared — the backfill on the
   first connection, or the lines missed while paused (with a ``gap`` when some are gone);
2. the stream's lines as the hub queues them, ``dropped`` at most once a second while lines were
   counted instead of shown, and the comment ``: ka`` after 15 s without a write;
3. every 30 s :func:`stream_problem` in a short session of its own: what
   :func:`~arcilogger_api.security.get_current_user` would refuse (revoked, expired, **idle** —
   the stream never counts as activity, so an unattended screen is signed out after
   ``auth.idle_minutes`` like every page), ``search.read`` still held, the current KVKK notice
   still accepted. A problem ends the stream with that code; the web app then follows its usual
   sign-in or notice flow.

A pause closes the connection: the stream lingers (:attr:`Timing.linger`) and a resume with
``Last-Event-ID`` replays from the hub's buffer. :class:`EventStreamResponse` always listens for
the client going away (also on ASGI 2.4 servers, where Starlette's streaming response does not),
so a paused or closed tab frees the hub's work at once; when the server stops and cancels the
response it still tries to write ``end {server_stopping}`` (1 s, best effort).
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator, Callable
from datetime import datetime

import anyio
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from arcilogger_api.auth.accounts import privacy_accepted, privacy_required, privacy_version
from arcilogger_api.live.events import KEEPALIVE, Entry, frame
from arcilogger_api.live.hub import Connection, Hub, Stream
from arcilogger_api.live.messages import end_texts
from arcilogger_api.security import permissions_for, session_problem
from arcilogger_db.models import User

log = logging.getLogger("arcilogger.api.live")


def stream_problem(factory: sessionmaker[Session], stream: Stream, now: datetime) -> str | None:
    """Why ``stream`` may not go on at ``now`` (an ``end`` code), ``None`` when it may."""
    with factory() as db:
        problem = session_problem(db, stream.token_hash, now)
        if problem is not None:
            return problem
        user = db.get(User, stream.user_id)
        if user is None or "search.read" not in permissions_for(user.role):
            return "permission_changed"
        if user.must_change_password:
            return "password_change_required"
        if privacy_required(user.role) and not privacy_accepted(db, user.id, privacy_version(db)):
            return "privacy_notice_required"
    return None


async def _check(hub: Hub, stream: Stream) -> str | None:
    """:func:`stream_problem` in a worker thread; a database that does not answer ends nothing
    (the next check runs 30 s later)."""
    try:
        return await anyio.to_thread.run_sync(
            stream_problem, hub.state.session_factory(), stream, hub.now()
        )
    except (SQLAlchemyError, OSError) as exc:
        log.warning("live tail %s: session check failed: %s", stream.id, exc)
        return None


def hello(hub: Hub, stream: Stream) -> bytes:
    limits = {
        "max_lines_per_second": stream.settings.max_lines_per_second,
        "keepalive_seconds": int(hub.timing.keepalive),
        "buffer_lines": stream.settings.buffer_lines,
        "max_minutes": stream.settings.max_minutes,
    }
    folders = [{**folder, "day": folder["day"].isoformat()} for folder in hub.folders_of(stream)]
    return frame(
        "hello",
        {
            "stream_id": stream.id,
            "epoch": hub.epoch,
            "notice_version": stream.notice_version,
            "folders": folders,
            "limits": limits,
        },
    )


async def events(
    hub: Hub, stream: Stream, connection: Connection, replay: list[Entry | bytes]
) -> AsyncIterator[bytes]:
    """The body of the response (see the module documentation)."""
    clock: Callable[[], float] = hub.clock
    timing = hub.timing
    try:
        yield hello(hub, stream)
        problem = await _check(hub, stream)
        if problem is not None:
            hub.end(stream, problem)
        else:
            for replayed in replay:
                if isinstance(replayed, Entry):
                    yield replayed.frame(hub.epoch, hub.site)
                    stream.sent(replayed)
                else:
                    yield replayed
        last_write = clock()
        next_check = clock() + timing.session_check
        next_dropped = clock()
        while not connection.stopped:
            if connection.controls:
                yield connection.controls.popleft()
                last_write = clock()
                if connection.ending and not connection.controls:
                    return
                continue
            due = min(last_write + timing.keepalive, next_check)
            if stream.pending:
                due = min(due, next_dropped)
            item: Entry | None = None
            if not connection.queue.empty():
                item = connection.queue.get_nowait()
            elif due > clock():
                with contextlib.suppress(TimeoutError), anyio.fail_after(due - clock()):
                    item = await connection.queue.get()
            if isinstance(item, Entry) and not connection.stopped:
                yield item.frame(hub.epoch, hub.site)
                stream.sent(item)
                last_write = clock()
            now = clock()
            if stream.pending and now >= next_dropped:
                data = stream.take_dropped()
                if data is not None:
                    yield frame("dropped", data)
                    last_write = now
                next_dropped = now + timing.dropped
            if now >= next_check:
                next_check = now + timing.session_check
                problem = await _check(hub, stream)
                if problem is not None:
                    hub.end(stream, problem)
                    continue
            if clock() - last_write >= timing.keepalive:
                yield KEEPALIVE
                last_write = clock()
    finally:
        hub.detach(stream, connection)


def stopping_frame() -> bytes:
    message_tr, message_en = end_texts("server_stopping")
    return frame(
        "end", {"code": "server_stopping", "message_tr": message_tr, "message_en": message_en}
    )


class EventStreamResponse(StreamingResponse):
    """``text/event-stream`` that notices a client going away at once (see the module text)."""

    media_type = "text/event-stream"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        try:
            async with anyio.create_task_group() as group:

                async def run() -> None:
                    with contextlib.suppress(OSError):  # the client went away (ASGI 2.4)
                        await self.stream_response(send)
                    group.cancel_scope.cancel()

                group.start_soon(run)
                await self.listen_for_disconnect(receive)
                group.cancel_scope.cancel()
        except anyio.get_cancelled_exc_class():
            # The server stops: one last event, then the cancellation goes on.
            with anyio.move_on_after(1, shield=True), contextlib.suppress(Exception):
                await send(
                    {"type": "http.response.body", "body": stopping_frame(), "more_body": False}
                )
            raise
        finally:
            # A body cancelled while the server was writing waits at a ``yield``: close it now,
            # so the stream is detached (or finished) at once, not when it is collected.
            close = getattr(self.body_iterator, "aclose", None)
            if close is not None:
                with anyio.CancelScope(shield=True):
                    await close()
