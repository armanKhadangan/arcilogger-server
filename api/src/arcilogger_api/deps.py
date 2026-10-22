"""Application state and request dependencies (configuration, layout, database session).

``create_app(config, engine)`` stores an :class:`AppState` on ``app.state.arci``. Without a
configuration (e.g. ``uvicorn --factory`` in development) the configuration file is loaded on
the first request that needs it (``ARCILOGGER_CONFIG`` or the default path); the health
endpoint never needs it.

Use :data:`DbSession` in endpoints: **one session per request**, committed after the endpoint
returned and *before* the response is sent (so a failed commit is a 5xx, not a silent loss),
rolled back when the endpoint raised.

``AppState.license_cache`` keeps the licence status (:mod:`arcilogger_api.licensing`) for
:data:`LICENSE_CACHE_SECONDS`; ``POST``/``DELETE /license`` and every user create or reactivate
invalidate it in this process (licensing contract 4.3).
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Annotated, Final

from fastapi import Depends, Request
from pydantic import ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.errors import ApiError
from arcilogger_core.config import Config, load_config
from arcilogger_core.layout import Layout
from arcilogger_db.engine import make_engine, session_factory
from arcilogger_db.license_guard import GuardedStatus

log = logging.getLogger("arcilogger.api")

#: ``Session.info`` key under which the audit module finds the spool layout.
LAYOUT_INFO_KEY = "arci.layout"
#: How long the API keeps a computed licence status (contract 4.3).
LICENSE_CACHE_SECONDS: Final = 30.0


@dataclass
class TtlCache[T]:
    """One value kept ``ttl`` seconds (monotonic clock); thread-safe.

    :meth:`invalidate` also discards a value computed while it ran: a computation that started
    before the invalidation never stores its (possibly stale) result.
    """

    ttl: float
    clock: Callable[[], float] = time.monotonic
    _value: T | None = None
    _expires: float = 0.0
    _generation: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def get(self) -> T | None:
        with self._lock:
            if self._value is not None and self.clock() < self._expires:
                return self._value
            return None

    def get_or_compute(
        self, compute: Callable[[], T], *, keep: Callable[[T], bool] | None = None
    ) -> T:
        """The kept value, else ``compute()`` — kept unless ``keep`` says no (e.g. a failure,
        which is then computed again by the next caller)."""
        with self._lock:
            if self._value is not None and self.clock() < self._expires:
                return self._value
            generation = self._generation
        value = compute()
        with self._lock:
            if generation == self._generation and (keep is None or keep(value)):
                self._value, self._expires = value, self.clock() + self.ttl
        return value

    def put(self, value: T) -> None:
        with self._lock:
            self._generation += 1
            self._value, self._expires = value, self.clock() + self.ttl

    def invalidate(self) -> None:
        with self._lock:
            self._generation += 1
            self._value, self._expires = None, 0.0


@dataclass
class AppState:
    config: Config | None = None
    engine: Engine | None = None
    _factory: sessionmaker[Session] | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)
    #: The guarded licence status (``arcilogger_db.license_guard.GuardedStatus``).
    license_cache: TtlCache[GuardedStatus] = field(
        default_factory=lambda: TtlCache[GuardedStatus](LICENSE_CACHE_SECONDS)
    )

    def require_config(self) -> Config:
        with self._lock:
            if self.config is None:
                try:
                    self.config = load_config()
                except (OSError, ValidationError, ValueError) as exc:
                    log.error("configuration not available: %s", exc)
                    raise ApiError(503, "unavailable") from exc
            return self.config

    @property
    def layout(self) -> Layout:
        return Layout(self.require_config().data_dir)

    def session_factory(self) -> sessionmaker[Session]:
        config = self.require_config()
        with self._lock:
            if self._factory is None:
                if self.engine is None:
                    self.engine = make_engine(config, application="api")
                self._factory = session_factory(
                    self.engine, **{LAYOUT_INFO_KEY: Layout(config.data_dir)}
                )
            return self._factory

    def dispose(self) -> None:
        if self.engine is not None:
            self.engine.dispose()


def get_state(request: Request) -> AppState:
    state: AppState = request.app.state.arci
    return state


def get_config(request: Request) -> Config:
    return get_state(request).require_config()


def get_layout(request: Request) -> Layout:
    return get_state(request).layout


def get_session_factory(request: Request) -> sessionmaker[Session]:
    """For work in its own transaction (e.g. auditing a failed login that is then refused)."""
    return get_state(request).session_factory()


def get_session(request: Request) -> Iterator[Session]:
    session = get_session_factory(request)()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


#: One database session per request (commit before the response; rollback on error).
DbSession = Annotated[Session, Depends(get_session, scope="function")]
ConfigDep = Annotated[Config, Depends(get_config)]
LayoutDep = Annotated[Layout, Depends(get_layout)]
SessionFactoryDep = Annotated[sessionmaker[Session], Depends(get_session_factory)]
