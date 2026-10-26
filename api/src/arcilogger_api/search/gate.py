"""Gates and limits of Arama (contract 2.6.4, 2.12): searches never crowd out ingest.

* :class:`SearchGate` — at most ``search.max_concurrent`` (default 3, kept at the database pool
  size - 2) searches, pages, statistics, evidence references and exports run at once in this
  process, two per user; a request waits up to 2 s for a slot, then 429 ``search_busy`` with
  ``Retry-After: 2`` (the web app retries once). The slot is taken after the request's
  authentication committed, so a waiting request holds no pooled connection.
* :class:`RunLimiter` — ``search.runs_per_minute`` (30) ``POST /search`` per user
  (``auth.ratelimit.RateLimiter`` keyed by the user id) → 429 ``search_rate_limited``.
* :class:`ExportLocks` — one export per user at a time → 409 ``search_export_running``;
  :func:`check_export_rate` — ``search.export_max_per_hour`` (10) per user, counted from
  ``audit_log`` (``search.export`` entries; ``ix_audit_log_actor_id_time``), so the limit
  survives a restart → 429 ``search_export_rate_limited``.
* :func:`check_range` — ``from < to``, both with an offset (``search_range_invalid``), at most
  ``search.max_days`` apart (``search_range_too_long``).
"""

from __future__ import annotations

import math
import threading
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import monotonic
from typing import Final

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_api.auth.ratelimit import RateLimiter
from arcilogger_api.search.messages import search_error
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import AuditLog

#: How long a request waits for a slot, and the ``Retry-After`` of ``search_busy``.
WAIT_SECONDS: Final = 2.0
RETRY_AFTER: Final = "2"
PER_USER: Final = 2
RUN_WINDOW_SECONDS: Final = 60.0
EXPORT_ACTION: Final = "search.export"
EXPORT_WINDOW: Final = timedelta(hours=1)


@dataclass
class SearchGate:
    """Slots for running searches (see the module text)."""

    wait_seconds: float = WAIT_SECONDS
    per_user: int = PER_USER
    _active: int = 0
    _users: Counter[int] = field(default_factory=Counter)
    _changed: threading.Condition = field(default_factory=threading.Condition, repr=False)

    @property
    def active(self) -> int:
        with self._changed:
            return self._active

    @contextmanager
    def slot(self, user_id: int, limit: int) -> Iterator[None]:
        """Run the block in a slot (``limit``: ``search.max_concurrent``); 429 ``search_busy``
        when none is free within :attr:`wait_seconds`."""
        deadline = monotonic() + self.wait_seconds
        with self._changed:
            while self._active >= limit or self._users[user_id] >= self.per_user:
                left = deadline - monotonic()
                if left <= 0:
                    raise search_error("search_busy", headers={"Retry-After": RETRY_AFTER})
                self._changed.wait(left)
            self._active += 1
            self._users[user_id] += 1
        try:
            yield
        finally:
            with self._changed:
                self._active -= 1
                self._users[user_id] -= 1
                if self._users[user_id] <= 0:
                    del self._users[user_id]
                self._changed.notify_all()


@dataclass
class RunLimiter:
    """``POST /search`` per user and minute (the limit is read per request)."""

    limiter: RateLimiter = field(default_factory=lambda: RateLimiter(30, RUN_WINDOW_SECONDS))

    def hit(self, user_id: int, limit: int) -> None:
        self.limiter.limit = limit
        decision = self.limiter.hit(str(user_id))
        if not decision.allowed:
            seconds = decision.retry_after
            raise search_error(
                "search_rate_limited", {"seconds": seconds}, headers={"Retry-After": str(seconds)}
            )


@dataclass
class ExportLocks:
    """One export per user at a time."""

    _users: set[int] = field(default_factory=set)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    @contextmanager
    def hold(self, user_id: int) -> Iterator[None]:
        with self._lock:
            if user_id in self._users:
                raise search_error("search_export_running")
            self._users.add(user_id)
        try:
            yield
        finally:
            with self._lock:
                self._users.discard(user_id)


def check_export_rate(session: Session, user_id: int, limit: int) -> None:
    """429 ``search_export_rate_limited`` when the user made ``limit`` exports in the last
    hour (counted from the audit log)."""
    since = utc_now() - EXPORT_WINDOW
    count, oldest = session.execute(
        select(func.count(), func.min(AuditLog.time)).where(
            AuditLog.actor_id == user_id,
            AuditLog.time >= since,
            AuditLog.action == EXPORT_ACTION,
        )
    ).one()
    if count < limit:
        return
    seconds = max(1.0, (oldest + EXPORT_WINDOW - utc_now()).total_seconds())
    minutes = max(1, math.ceil(seconds / 60))
    raise search_error(
        "search_export_rate_limited",
        {"n": limit, "minutes": minutes},
        headers={"Retry-After": str(math.ceil(seconds))},
    )


def check_range(start: datetime, end: datetime, max_days: int) -> None:
    """The time range of a search (contract 2.2.1: half-open ``[from, to)`` on device time)."""
    if start.tzinfo is None or end.tzinfo is None or start.utcoffset() is None or start >= end:
        raise search_error("search_range_invalid")
    if end - start > timedelta(days=max_days):
        raise search_error("search_range_too_long", {"days": max_days})
