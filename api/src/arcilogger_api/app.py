"""FastAPI application factory.

The API listens on 127.0.0.1 only; Caddy terminates HTTPS and serves the web interface.

**No interactive API docs and no public schema** (deviation from spec 2 "OpenAPI docs at
/api/docs (admin only)", docs/ASSUMPTIONS.md): FastAPI's Swagger page loads its scripts and styles
from a public CDN, which spec 0.4 forbids (closed network, everything bundled) and Caddy's CSP
blocks anyway, and an unauthenticated ``/api/openapi.json`` would describe every endpoint to
anyone on the LAN. The schema is still built in process — ``create_app().openapi()`` — for the
web app's contract export (``web/scripts/export_api_contract.py``) and the tests.

``create_app(config, engine)``: production passes the configuration (``arcilogger-api serve``);
tests also inject an engine on a throwaway database. Without arguments the configuration is
loaded on first use (development: ``uv run poe api-dev``).

With a configuration, the lifespan runs the audit spool flush at start-up and every minute
(``arcilogger_api.audit.run_periodic_flush``), so a committed audit entry reaches the day's spool
file even when its own after-commit flush failed and no further audited action follows.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Final, Literal

from fastapi import APIRouter, FastAPI
from fastapi.telemetry import TelemetryConfig
from pydantic import BaseModel
from sqlalchemy import Engine
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

import arcilogger_core
from arcilogger_api import __version__
from arcilogger_api.audit import FLUSH_INTERVAL_SECONDS, run_periodic_flush
from arcilogger_api.deps import AppState
from arcilogger_api.errors import install_error_handlers
from arcilogger_api.routers import discover
from arcilogger_api.security import CsrfMiddleware
from arcilogger_core.config import Config
from arcilogger_core.timeutil import SITE_TZ_NAME, to_site, utc_now

API_PREFIX = "/api/v1"
#: FastAPI's built-in OpenTelemetry hooks stay off: nothing about requests leaves the server, not
#: even when OTEL_* / FASTAPI_OTEL_AUTO_CONFIGURE environment variables are set (spec 0.4, 21).
TELEMETRY_OFF: Final[TelemetryConfig] = {
    "tracing": False,
    "metrics": False,
    "logs": False,
    "operation_spans": False,
    "auto_configure": False,
}


class Health(BaseModel):
    """Liveness information for monitoring and the installer's smoke test."""

    status: Literal["ok"]
    product: str
    version: str
    core_version: str
    time: datetime
    tz: str


router = APIRouter(prefix=API_PREFIX)


@router.get("/health", tags=["system"])
def health() -> Health:
    """Return ``ok`` while the API process is running (no database access)."""
    return Health(
        status="ok",
        product=arcilogger_core.PRODUCT_NAME,
        version=__version__,
        core_version=arcilogger_core.__version__,
        time=to_site(utc_now()),
        tz=SITE_TZ_NAME,
    )


class ApiHeadersMiddleware:
    """API responses are never cached and never sniffed (Caddy adds HSTS, CSP, frame rules)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not str(scope.get("path", "")).startswith("/api/"):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.setdefault("Cache-Control", "no-store")
                headers.setdefault("X-Content-Type-Options", "nosniff")
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _engine_of(state: AppState) -> Engine:
    state.session_factory()  # creates the engine on first use
    if state.engine is None:  # pragma: no cover - session_factory always sets it
        raise RuntimeError("no database engine")
    return state.engine


def create_app(
    config: Config | None = None,
    engine: Engine | None = None,
    *,
    audit_flush_seconds: float | None = FLUSH_INTERVAL_SECONDS,
) -> FastAPI:
    """Build the ASGI application (``audit_flush_seconds=None``: no background flush)."""
    state = AppState(config=config, engine=engine)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        stop = asyncio.Event()
        flusher = None
        if state.config is not None and audit_flush_seconds:
            flusher = asyncio.create_task(
                run_periodic_flush(
                    lambda: _engine_of(state), state.layout, stop, interval=audit_flush_seconds
                )
            )
        try:
            yield
        finally:
            stop.set()
            if flusher is not None:
                await flusher  # a flush in progress finishes before the engine is disposed
            state.dispose()

    app = FastAPI(
        title="ARCILOGGER API",
        version=__version__,
        # no Swagger/ReDoc page (CDN assets) and no unauthenticated schema (module docstring)
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
        telemetry=TELEMETRY_OFF,
    )
    app.state.arci = state
    install_error_handlers(app)
    app.add_middleware(CsrfMiddleware, secure_cookies=config.api.secure_cookies if config else True)
    app.add_middleware(ApiHeadersMiddleware)
    app.include_router(router)
    for feature in discover():
        app.include_router(feature, prefix=API_PREFIX)
    return app
