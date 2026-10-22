"""In-memory per-IP rate limits for the sign-in endpoints (spec 13: "rate limits on auth").

A sliding window per ``(bucket, client IP)``: at most ``limit`` requests in ``window_seconds``.
The account lockout (``auth.lockout_attempts`` failures → ``auth.lockout_minutes``) protects each
account; these limits stop one address from trying many accounts or burning CPU with Argon2.

The limits live in the API process (one Uvicorn worker on the server; ADR-0001), so a restart
resets them — acceptable, because the per-account lockout is stored in the database. Production
traffic arrives through Caddy, whose ``X-Forwarded-For`` Uvicorn applies (``client_ip``); the
firewall lets only a few administration PCs reach the server, so the defaults are generous enough
for several users behind one address.

Only the first refusal of a window is audited (``auth.rate_limited``), so a flood of requests
cannot flood the audit log.
"""

from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Final

from starlette.requests import Request

#: Requests per window and window length (seconds) per bucket.
DEFAULT_LIMITS: Final = {
    "login": (20, 300.0),
    "totp": (20, 300.0),
    "reauth": (20, 300.0),
    "setup": (10, 300.0),
}
_STATE_ATTRIBUTE: Final = "arci_auth_rate_limits"
_create_lock = threading.Lock()


@dataclass(frozen=True)
class Decision:
    allowed: bool
    #: Seconds until the next request would be allowed (0 when allowed).
    retry_after: int = 0
    #: The first refusal since the address was last allowed (audit only this one).
    first_refusal: bool = False


@dataclass
class RateLimiter:
    limit: int
    window_seconds: float
    clock: Callable[[], float] = time.monotonic
    #: Upper bound of tracked addresses (memory); expired entries are dropped first.
    max_keys: int = 10_000
    _hits: dict[str, deque[float]] = field(default_factory=dict, init=False, repr=False)
    _refused: set[str] = field(default_factory=set, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def hit(self, key: str) -> Decision:
        """Count one request for ``key`` unless the limit is reached."""
        now = self.clock()
        with self._lock:
            hits = self._hits.get(key)
            if hits is None:
                self._make_room(now)
                hits = self._hits[key] = deque()
            while hits and hits[0] <= now - self.window_seconds:
                hits.popleft()
            if len(hits) >= self.limit:
                first = key not in self._refused
                self._refused.add(key)
                retry = max(1, math.ceil(hits[0] + self.window_seconds - now))
                return Decision(allowed=False, retry_after=retry, first_refusal=first)
            hits.append(now)
            self._refused.discard(key)
            return Decision(allowed=True)

    def _make_room(self, now: float) -> None:
        if len(self._hits) < self.max_keys:
            return
        for key in [
            k for k, v in self._hits.items() if not v or v[-1] <= now - self.window_seconds
        ]:
            del self._hits[key]
            self._refused.discard(key)
        while len(self._hits) >= self.max_keys:  # still full: forget the oldest address
            oldest = next(iter(self._hits))
            del self._hits[oldest]
            self._refused.discard(oldest)


@dataclass
class AuthRateLimits:
    """The limiters of one application instance (tests replace them on ``app.state``)."""

    limiters: dict[str, RateLimiter] = field(
        default_factory=lambda: {
            bucket: RateLimiter(limit, window) for bucket, (limit, window) in DEFAULT_LIMITS.items()
        }
    )

    def hit(self, bucket: str, address: str | None) -> Decision:
        return self.limiters[bucket].hit(address or "unknown")


def limits_of(request: Request) -> AuthRateLimits:
    """The application's limiters, created on first use."""
    state = request.app.state
    limits = getattr(state, _STATE_ATTRIBUTE, None)
    if limits is None:
        with _create_lock:
            limits = getattr(state, _STATE_ATTRIBUTE, None)
            if limits is None:
                limits = AuthRateLimits()
                setattr(state, _STATE_ATTRIBUTE, limits)
    if not isinstance(limits, AuthRateLimits):  # pragma: no cover - misconfiguration
        raise TypeError("app.state.arci_auth_rate_limits must be AuthRateLimits")
    return limits


def install_limits(app_state: object, limits: AuthRateLimits) -> None:
    """Replace the limiters of an application (tests, tuning)."""
    setattr(app_state, _STATE_ATTRIBUTE, limits)
