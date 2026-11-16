"""R13 and R14 in a catch-up (#302): a minute of receive time that holds hours of device time.

After an ingest or database outage, or a collector backlog, one slice can hold the logs of hours
of device time, and they arrive faster than autovacuum analyzes the tables: the planner still
sees the statistics from before the burst. #278's guard gives its slice the receive and device
times of one minute and analyzes after writing it, so it covers neither.

Here the guard's slice (:func:`~.test_rules_performance.generated`, 600,000 normal rows and every
attack row, received over one minute) keeps the load generator's own device times, mapped onto
the 6 or 24 hours before the slice's end, and is written after the tables were analyzed empty,
with autovacuum off for them. R13 and R14 count the slice from their kept totals (the previous
slice counted first) within ``rules.statement_timeout_seconds``, and their hits equal one read of
the period.

``ARCILOGGER_RULES_PERF=0`` skips; run with ``-s`` to see the timings.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from datetime import datetime, timedelta
from datetime import time as dtime
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.timeutil import SITE_TZ, site_day
from arcilogger_db.engine import session_factory
from arcilogger_db.partitions import ensure_partitions
from arcilogger_worker.rules import people
from arcilogger_worker.rules.engine import context
from arcilogger_worker.rules.model import Hit
from arcilogger_worker.rules.settings import effective, engine_settings, stored_overrides

from . import test_rules_performance as guard
from .test_rules_performance import DAY, MODE, ROWS, SLICE, SLICE_END, configure, generated

pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(MODE == "0", reason="ARCILOGGER_RULES_PERF=0"),
]
#: The default of ``rules.statement_timeout_seconds``: a slice that takes longer fails.
STATEMENT_TIMEOUT = 20.0


def catch_up_rows(hours: float) -> Iterable[guard.Row]:
    """The guard's slice with the load generator's own device times, mapped onto the ``hours``
    before the slice's end (in their order: a session's logs stay a session); each repetition
    of the normal rows has its own session ids."""
    normal, attack = generated()
    total = ROWS + len(attack)
    step = SLICE / total
    start = SLICE_END - SLICE
    every = total // max(len(attack), 1)
    attack_rows = iter(attack)
    normal_index = 0
    day_start = datetime.combine(DAY, dtime(0), tzinfo=SITE_TZ)
    scale = hours / 24
    for n in range(total):
        repetition = 0
        if n % every == every - 1 and (row := next(attack_rows, None)) is not None:
            table, values = row
        else:
            table, values = normal[normal_index % len(normal)]
            repetition = normal_index // len(normal)
            normal_index += 1
        received = start + step * (n + 1)
        into_day = (values["time"] - day_start).total_seconds()
        device = SLICE_END - timedelta(seconds=(86400 - into_day) * scale)
        moved = {
            **values,
            "time": min(device, received),
            "time_received": received,
            "raw_day": site_day(received),
            "raw_line": n + 1,
            "raw_offset": (n + 1) * 100,
        }
        if repetition and moved.get("sessionid") is not None:
            moved["sessionid"] += repetition * 100_000_000
        yield table, moved


@pytest.fixture
def burst(monkeypatch: pytest.MonkeyPatch) -> None:
    """The guard writes the slice without analyzing after it."""
    monkeypatch.setattr(guard, "text", lambda sql: text("SELECT 1" if sql == "ANALYZE" else sql))


def evaluate(
    sessions: sessionmaker[Session], rule_id: str, start: datetime, end: datetime
) -> tuple[list[Hit], float]:
    """The rule over the slice ``(start, end]``, committed (its totals kept), and its seconds."""
    rule = next(entry for entry in people.RULES if entry.id == rule_id)
    with sessions() as session:
        settings = effective(rule, stored_overrides(session, (rule,))[rule.id])
        ctx = context(settings, engine_settings(session), start, end)
        session.execute(text(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT:g}s'"))
        began = time.perf_counter()
        hits = list(rule.evaluate(session, ctx))
        seconds = time.perf_counter() - began
        session.commit()
    return hits, seconds


def summary(hits: Iterable[Hit]) -> list[tuple[Any, ...]]:
    return sorted(
        (hit.key, hit.count, sorted(hit.values.items()), [ref.row_id for ref in hit.evidence])
        for hit in hits
    )


@pytest.mark.parametrize("hours", [6, 24], ids=["6h", "24h"])
def test_a_catch_up_slice_within_the_statement_timeout(
    db_engine: Engine, burst: None, hours: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
        names = connection.scalars(
            text("SELECT relname FROM pg_class WHERE relname LIKE 'fg\\_%' AND relkind = 'r'")
        ).all()
        for name in names:  # names from the catalog, not from input
            connection.execute(text(f'ALTER TABLE "{name}" SET (autovacuum_enabled = false)'))
        connection.execute(text("ANALYZE"))  # the statistics from before the burst
    monkeypatch.setattr(guard, "slice_rows", lambda: catch_up_rows(hours))
    sessions = session_factory(db_engine)
    guard.write(sessions)
    configure(sessions)
    figures: dict[str, Any] = {}
    for rule_id in ("R13", "R14"):
        # One read of the period (no kept totals), rolled back, against the kept totals: the
        # previous slice counted and committed, then the catch-up slice.
        with sessions() as session:
            session.execute(text("DELETE FROM rule_traffic_state"))
            session.commit()
        rule = next(entry for entry in people.RULES if entry.id == rule_id)
        with sessions() as session:
            settings = effective(rule, stored_overrides(session, (rule,))[rule.id])
            ctx = context(settings, engine_settings(session), SLICE_END - SLICE, SLICE_END)
            began = time.perf_counter()
            once = list(rule.evaluate(session, ctx))
            figures[f"{rule_id}_one_read_s"] = round(time.perf_counter() - began, 2)
            session.rollback()
        evaluate(sessions, rule_id, SLICE_END - 2 * SLICE, SLICE_END - SLICE)
        kept, seconds = evaluate(sessions, rule_id, SLICE_END - SLICE, SLICE_END)
        figures[f"{rule_id}_kept_s"] = round(seconds, 2)
        figures[f"{rule_id}_hits"] = len(kept)
        assert summary(kept) == summary(once), rule_id
        assert seconds < STATEMENT_TIMEOUT, (rule_id, seconds)
    print(f"\nR13/R14 in a catch-up over {hours} h of device time: {figures}")
