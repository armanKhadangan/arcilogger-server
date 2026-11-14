"""The dry run of a rule: "Son 24 saatte dene" (Wave 6 contract §2.1.6).

:func:`dry_run` evaluates a rule with candidate settings over ``(now - 24 h - lag, now - lag]``
in slices of ``rules.max_slice_seconds`` (at most the rule's window), the same way the engine
does, in **one read-only transaction** (``SET TRANSACTION READ ONLY``) with
``statement_timeout = rules.dry_run_timeout_seconds``. It counts the alerts that **would** have
opened, with dedup and cooldown applied as if no alert existed: an event rule's alert stays open
(nobody closes it in a dry run), so each key opens at most one; a condition rule's alert resolves
itself after ``clear_seconds`` without a hit, and a later hit opens a new one once the cooldown is
over. It writes nothing and opens no alert. The whole run is bounded by the same timeout
(:class:`DryRunTimeoutError`); one dry run at a time per API process is the API's lock.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_worker.rules import registry
from arcilogger_worker.rules.engine import context, slices, step
from arcilogger_worker.rules.model import RuleDef
from arcilogger_worker.rules.settings import effective, engine_settings

#: How far back a dry run looks.
PERIOD: Final = timedelta(hours=24)
#: Keys listed in the answer.
KEYS_MAX: Final = 20


class DryRunTimeoutError(Exception):
    """The dry run did not finish within ``rules.dry_run_timeout_seconds`` (API:
    ``rule_dry_run_timeout``, 504)."""

    def __init__(self, seconds: int) -> None:
        super().__init__(f"dry run longer than {seconds} s")
        self.seconds = seconds


@dataclass
class _Key:
    count: int
    first_time: datetime
    last_time: datetime
    #: The simulated alert: when it opened and whether it is still active.
    opened_at: datetime
    active: bool = True


@dataclass
class DryRun:
    would_open: int = 0
    keys: dict[str, _Key] = field(default_factory=dict)
    duration_ms: int = 0

    def as_json(self) -> dict[str, Any]:
        """The answer of ``POST /rules/{id}/dry-run``: the first keys by their first hit, each
        with the largest window count of its hits (an alert's ``count``)."""
        ordered = sorted(self.keys.items(), key=lambda item: (item[1].first_time, item[0]))
        return {
            "would_open": self.would_open,
            "keys": [
                {
                    "key": key,
                    "count": entry.count,
                    "first_time": entry.first_time.isoformat(),
                    "last_time": entry.last_time.isoformat(),
                }
                for key, entry in ordered[:KEYS_MAX]
            ],
            "truncated": len(ordered) > KEYS_MAX,
            "duration_ms": self.duration_ms,
        }


def dry_run(session: Session, rule_id: str | RuleDef, candidate: object, now: datetime) -> DryRun:
    """What the rule (an id of the registry, or a rule) with the ``candidate`` settings
    (``rules.<ID>`` shape, checked strictly: :class:`~.settings.RuleSettingsError`) would have
    raised in the last 24 hours. ``session`` must not be in a transaction yet; it is rolled back
    at the end."""
    started = time.monotonic()
    rule = registry.rule(rule_id) if isinstance(rule_id, str) else rule_id
    settings = effective(rule, candidate, strict=True)
    result = DryRun()
    try:
        session.execute(text("SET TRANSACTION READ ONLY"))
        engine = engine_settings(session)
        session.execute(
            text("SELECT set_config('statement_timeout', :value, true)"),
            {"value": str(engine.dry_run_timeout_seconds * 1000)},
        )
        deadline = started + engine.dry_run_timeout_seconds
        horizon = now - engine.lag
        cooldown = timedelta(seconds=settings.cooldown_seconds)
        for start, end in slices(horizon - PERIOD, horizon, step(engine, settings)):
            if time.monotonic() > deadline:
                raise DryRunTimeoutError(engine.dry_run_timeout_seconds)
            try:
                hits = list(rule.evaluate(session, context(settings, engine, start, end)))
            except DBAPIError as exc:
                if type(exc.orig).__name__ == "QueryCanceled":
                    raise DryRunTimeoutError(engine.dry_run_timeout_seconds) from None
                raise
            for hit in hits:
                entry = result.keys.get(hit.key)
                if entry is None:
                    result.keys[hit.key] = _Key(hit.count, hit.time, hit.time, hit.time)
                    result.would_open += 1
                    continue
                entry.count = max(entry.count, hit.count)  # the largest window, as the engine
                entry.last_time = max(entry.last_time, hit.time)
                if not entry.active and hit.time >= entry.opened_at + cooldown:
                    entry.active = True  # a new alert after the cooldown of the closed one
                    entry.opened_at = hit.time
                    result.would_open += 1
            if rule.kind == "condition" and rule.clear_seconds is not None:
                clear = end - timedelta(seconds=rule.clear_seconds)
                for entry in result.keys.values():
                    if entry.active and entry.last_time <= clear:
                        entry.active = False
    finally:
        session.rollback()
    result.duration_ms = int((time.monotonic() - started) * 1000)
    return result
