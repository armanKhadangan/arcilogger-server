"""The administration policy as the agent and the API use it (ADR-0014; contract 2.4.1).

:func:`arcilogger_core.admin.contract.load_policy` gives ``admin\\policy.json`` its one meaning
(a missing, unreadable or invalid file is :data:`~arcilogger_core.admin.contract.FALLBACK_POLICY`,
browser administration off). This module adds what goes beyond that: the SHA-256 of the bytes in
force (the agent's heartbeat reports it, so a changed policy is visible) and the hold times of
the guarded changes (contract 2.3).
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from arcilogger_core.admin.contract import (
    FALLBACK_POLICY,
    AdminPolicy,
    DocumentError,
    parse_document,
)


@dataclass(frozen=True)
class PolicyFile:
    """``policy.json`` as read now."""

    policy: AdminPolicy
    #: Of the bytes in force; None when the fallback is in force.
    sha256: str | None
    #: Why the fallback is in force (for the log), else None.
    error: str | None = None

    @property
    def enabled(self) -> bool:
        return self.policy.enabled


def read_policy(path: Path) -> PolicyFile:
    """``policy.json`` at ``path``, or the fallback (never raises)."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        return PolicyFile(FALLBACK_POLICY, None, f"{type(exc).__name__}: {exc}")
    try:
        return PolicyFile(parse_document(AdminPolicy, data), hashlib.sha256(data).hexdigest())
    except DocumentError as exc:
        return PolicyFile(FALLBACK_POLICY, None, str(exc).splitlines()[0])


def hold_until(policy: AdminPolicy, now: datetime) -> datetime | None:
    """When a guarded change (retention, replica) takes effect; None: at once
    (``hold_hours = 0``, the default)."""
    if policy.hold_hours == 0:
        return None
    return now + timedelta(hours=policy.hold_hours)


def jar_approvable_at(policy: AdminPolicy, first_seen: datetime) -> datetime:
    """The earliest time ``approve-jar`` accepts a staged Zamane client that is not a known
    release (contract Day 0 note D0-16): time alone never approves it."""
    return first_seen + timedelta(hours=policy.jar_hold_hours)
