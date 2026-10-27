"""Test helpers for Canlı Akış (``api/tests/test_live_*.py``, ``tests/test_caddy_streaming.py``).

Starlette's ``TestClient`` collects a whole response before it returns, so an endless
``text/event-stream`` cannot be read through it: :class:`LiveServer` runs the app in a real
Uvicorn server on a free local port, in a thread, and :func:`read_events` parses the stream as
the web app does. Nothing in production imports this module.
"""

from __future__ import annotations

import json
import socket
import threading
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from types import TracebackType
from typing import Any

import httpx2 as httpx
import uvicorn
from fastapi import FastAPI
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth.accounts import accept_privacy, privacy_version
from arcilogger_api.security import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, create_session
from arcilogger_api.testing import LoggedIn, make_user
from arcilogger_core.rawlog import format_line
from arcilogger_db.models import User


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


class LiveServer:
    """``app`` served by Uvicorn on ``127.0.0.1:<free port>`` while the context is open."""

    def __init__(self, app: FastAPI, *, graceful_seconds: int = 1) -> None:
        self.port = free_port()
        self.url = f"http://127.0.0.1:{self.port}"
        config = uvicorn.Config(
            app,
            host="127.0.0.1",
            port=self.port,
            log_level="warning",
            lifespan="on",
            timeout_graceful_shutdown=graceful_seconds,
        )
        self.server = uvicorn.Server(config)
        self.thread = threading.Thread(target=self.server.run, daemon=True)

    def __enter__(self) -> LiveServer:
        self.thread.start()
        deadline = time.monotonic() + 20
        while not self.server.started:
            if not self.thread.is_alive() or time.monotonic() > deadline:
                raise RuntimeError("the test server did not start")
            time.sleep(0.02)
        return self

    def __exit__(
        self,
        kind: type[BaseException] | None,
        error: BaseException | None,
        trace: TracebackType | None,
    ) -> None:
        self.server.should_exit = True
        self.thread.join(timeout=20)


def sign_in(
    client: httpx.Client,
    factory: sessionmaker[Session],
    role: str = "admin",
    *,
    notice: bool = True,
    **user: Any,
) -> LoggedIn:
    """A user with a valid session on ``client`` (cookies and CSRF header), the KVKK notice
    accepted unless ``notice=False``."""
    row: User = make_user(factory, role, **user)
    with factory() as session:
        issued = create_session(
            session, session.get_one(User, row.id), mfa_passed=True, ip="172.20.10.37"
        )
        if notice:
            accept_privacy(session, row.id, privacy_version(session), ip=None)
        session.commit()
    client.cookies.set(SESSION_COOKIE, issued.token)
    client.cookies.set(CSRF_COOKIE, issued.csrf_token)
    client.headers[CSRF_HEADER] = issued.csrf_token
    return LoggedIn(row.id, row.username, row.role, issued.token, issued.csrf_token)


@dataclass(frozen=True)
class Event:
    event: str
    data: dict[str, Any]
    id: str | None = None


def read_events(response: httpx.Response) -> Iterator[Event | None]:
    """The events of a ``text/event-stream`` response; ``None`` for each comment (keep-alive)."""
    event, event_id, data = "message", None, []
    for line in response.iter_lines():
        if line.startswith(":"):
            yield None
        elif line.startswith("event: "):
            event = line[len("event: ") :]
        elif line.startswith("id: "):
            event_id = line[len("id: ") :]
        elif line.startswith("data: "):
            data.append(line[len("data: ") :])
        elif not line and data:
            yield Event(event, json.loads("\n".join(data)), event_id)
            event, event_id, data = "message", None, []


def spool_line(
    message: str | bytes, sender: str = "172.20.10.1", at: datetime | None = None
) -> bytes:
    """One collector line received now (or at ``at``)."""
    body = message.encode() if isinstance(message, str) else message
    return format_line(at or datetime.now(UTC), sender, body)
