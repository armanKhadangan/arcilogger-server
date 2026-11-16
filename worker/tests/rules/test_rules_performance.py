"""The rule engine's performance guard (Wave 6 contract §2.1.7, T-A6).

One 60-s slice of **10,000 messages per second** (600,000 parsed rows) in the load generator's
table mix, with every security rule enabled and configured for the generator's site: the job
``rules.run`` must finish the slice in at most :data:`BUDGET_SECONDS` on the Linux CI runner
(plain PostgreSQL layout).

The rows are the generator's (``tools/loadgen``, synthetic) through the real parsers: a working
day with every attack scenario (``--scenario all``). The normal site's rows are repeated, in
their order, until the slice holds :data:`ROWS`. Every scenario row is put into the slice as
well, so each rule has something to find. R14's night covers the slice. The receive times are
spread evenly over the slice; each row's device time is two seconds earlier. The ``messages``
table is not written: no rule reads it.

The second test is the guard's other half: ingest's throughput gate
(``ingest/tests/test_throughput.py``, :data:`INGEST_FLOOR`) holds while ``rules.run`` evaluates
that slice again and again in its own process, as the worker service would.

``ARCILOGGER_RULES_PERF=0`` skips; run with ``-s`` to see the timings per rule.
"""

from __future__ import annotations

import importlib.util
import multiprocessing
import os
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import replace
from datetime import date, datetime, timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, insert, text, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.rawlog import RawRecord
from arcilogger_core.timeutil import SITE_TZ, site_day
from arcilogger_db.engine import engine_from_url, session_factory
from arcilogger_db.models import Base, RuleState
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.settings import set_setting
from arcilogger_ingest.parsers import LinePosition, SourceType, parse_line
from arcilogger_ingest.testing import World, measure_throughput
from arcilogger_worker.rules import registry
from arcilogger_worker.rules.engine import run_rules
from arcilogger_worker.rules.model import Hit, RuleContext, RuleDef

MODE = os.environ.get("ARCILOGGER_RULES_PERF", "1")
pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(MODE == "0", reason="ARCILOGGER_RULES_PERF=0"),
]
LOADGEN = Path(__file__).resolve().parents[3] / "tools" / "loadgen" / "arcilogger_loadgen.py"
#: A Wednesday; the slice ends at 10:30 site time.
DAY = date(2026, 10, 14)
SLICE_END = datetime(2026, 10, 14, 10, 30, tzinfo=SITE_TZ)
SLICE = timedelta(seconds=60)
#: 10,000 messages per second for the 60-s slice.
ROWS = 600_000
#: Seconds for ``rules.run`` on the slice (contract §2.1.7).
BUDGET_SECONDS = 20.0
#: People of the generated day: enough for the table mix, which is what the rows repeat.
USERS = 40
INSERT_BATCH = 10_000
#: ``rules.lag_seconds`` (default): with ``now`` this much after the slice end, the horizon is
#: the slice end.
LAG = timedelta(seconds=120)
#: Ingest's throughput gate (``ingest/tests/test_throughput.py``): its lines, settings and floor.
INGEST_LINES = 20_000
INGEST_FLOOR = 2_000


@cache
def loadgen() -> ModuleType:
    spec = importlib.util.spec_from_file_location("arcilogger_loadgen_rules", LOADGEN)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve their module by name
    spec.loader.exec_module(module)
    return module


Row = tuple[str, dict[str, Any]]


@cache
def generated() -> tuple[tuple[Row, ...], tuple[Row, ...]]:
    """The parsed rows of the generated day: the normal site's and the attack scenarios'."""
    lg = loadgen()
    settings = lg.Settings(users=USERS, scenarios=lg.parse_scenarios("all"))
    out = next(lg.Simulation(settings).run(DAY))
    sources = {source.name: source for source in settings.sources}
    normal: list[Row] = []
    attack: list[Row] = []
    for number, message in enumerate(out.messages, 1):
        source = sources[message.source]
        kind: SourceType = source.type
        outcome = parse_line(
            RawRecord(message.received, source.address, message.wire),
            LinePosition(message.source, kind, DAY, number, number * 100),
        )
        assert outcome.error is None, (message.wire, outcome.error, outcome.detail)
        found = attack if message.kind.startswith("scenario.") else normal
        found += [(row.table, dict(row.values)) for row in outcome.rows if row.table != "messages"]
    return tuple(normal), tuple(attack)


def slice_rows() -> Iterable[Row]:
    """:data:`ROWS` normal rows and every attack row, receive times spread over the slice."""
    normal, attack = generated()
    total = ROWS + len(attack)
    step = SLICE / total
    start = SLICE_END - SLICE
    # The attack rows sit among the normal ones, evenly, in their own order.
    every = total // max(len(attack), 1)
    attack_rows = iter(attack)
    normal_index = 0
    for n in range(total):
        if n % every == every - 1 and (row := next(attack_rows, None)) is not None:
            table, values = row
        else:
            table, values = normal[normal_index % len(normal)]
            normal_index += 1
        received = start + step * (n + 1)
        yield (
            table,
            {
                **values,
                "time": received - timedelta(seconds=2),
                "time_received": received,
                "raw_day": site_day(received),
                "raw_line": n + 1,
                "raw_offset": (n + 1) * 100,
            },
        )


def write(sessions: sessionmaker[Session]) -> Counter[str]:
    """The slice's rows, in batches per table; the devices the DHCP rows name (one new)."""
    counts: Counter[str] = Counter()
    pending: dict[str, list[dict[str, Any]]] = defaultdict(list)
    lg = loadgen()
    macs: dict[str, datetime] = {}
    with sessions() as session:

        def flush(table: str) -> None:
            session.execute(insert(Base.metadata.tables[table]), pending.pop(table))

        for table, values in slice_rows():
            pending[table].append(values)
            counts[table] += 1
            if table == "mt_dhcp" and values.get("mac"):
                new = str(values.get("ip")) == lg.NEW_DEVICE_IP
                macs.setdefault(str(values["mac"]), values["time_received"] if new else _morning())
            if len(pending[table]) >= INSERT_BATCH:
                flush(table)
        for table in list(pending):
            flush(table)
        session.execute(
            insert(Base.metadata.tables["devices"]),
            [
                {"mac": mac, "created_at": at, "first_seen": at, "last_seen": at}
                for mac, at in macs.items()
            ],
        )
        session.commit()
        session.execute(text("ANALYZE"))
        session.commit()
    counts["devices"] = len(macs)
    return counts


def _morning() -> datetime:
    return datetime(2026, 10, 14, 7, 0, tzinfo=SITE_TZ)


def configure(sessions: sessionmaker[Session]) -> None:
    """Every rule on: the two that need setup (switched off until then) set up for the
    generator's site, R14's night around the slice; each rule's watermark at the slice start."""
    lg = loadgen()
    with sessions() as session:
        set_setting(
            session,
            "rules.R08",
            {"enabled": True, "params": {"allowed_dns_servers": [f"{lg.DNS_SERVER}/32"]}},
            updated_by=":system",
        )
        set_setting(
            session,
            "rules.R14",
            {
                "enabled": True,
                "params": {
                    "user_networks": ["172.20.41.0/24", "172.20.42.0/24", "172.20.43.0/24"],
                    "night_start": 9 * 3600,
                    "night_end": 12 * 3600,
                },
            },
            updated_by=":system",
        )
        for rule in registry.RULES:
            session.add(
                RuleState(rule=rule.id, watermark=SLICE_END - SLICE, failures=0, alerts_opened=0)
            )
        session.commit()


def timed(rule: RuleDef, spent: dict[str, float]) -> RuleDef:
    """``rule`` whose evaluation adds its seconds to ``spent``."""

    def evaluate(session: Session, context: RuleContext) -> Iterable[Hit]:
        started = time.perf_counter()
        hits = list(rule.evaluate(session, context))
        spent[rule.id] = spent.get(rule.id, 0.0) + time.perf_counter() - started
        return hits

    return replace(rule, evaluate=evaluate)


def test_one_slice_of_ten_thousand_messages_per_second(db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
    sessions = session_factory(db_engine)
    started = time.perf_counter()
    counts = write(sessions)
    written = time.perf_counter() - started
    configure(sessions)
    spent: dict[str, float] = {}
    rules = [timed(rule, spent) for rule in registry.RULES]
    started = time.perf_counter()
    report = run_rules(sessions, now=SLICE_END + LAG, rules=rules)
    elapsed = time.perf_counter() - started
    details = report.details()
    per_rule = ", ".join(f"{rule} {seconds:.2f}" for rule, seconds in sorted(spent.items()))
    print(
        f"\nrules.run on one 60-s slice: {elapsed:.2f} s (budget {BUDGET_SECONDS:g} s); "
        f"rows {sum(counts.values()) - counts['devices']} written in {written:.0f} s "
        f"{dict(sorted(counts.items()))}; opened {details['opened']}; per rule (s): {per_rule}"
    )
    assert details["failed"] == []
    assert details["slices"] == len(registry.RULES)
    assert sum(counts.values()) - counts["devices"] >= ROWS
    assert elapsed <= BUDGET_SECONDS


def _rules_again_and_again(url: str, stop: Any, runs: Any) -> None:
    """The worker's side: ``rules.run`` on the slice until ``stop``, each run from the slice
    start again (a forked process: its own engine and connections)."""
    engine = engine_from_url(url)
    sessions = session_factory(engine)
    try:
        while not stop.is_set():
            with sessions() as session:
                session.execute(update(RuleState).values(watermark=SLICE_END - SLICE))
                session.commit()
            details = run_rules(sessions, now=SLICE_END + LAG).details()
            assert details["failed"] == [], details
            runs.value += 1
    finally:
        engine.dispose()


@pytest.mark.skipif(
    "fork" not in multiprocessing.get_all_start_methods(), reason="the Linux CI job runs this"
)
def test_ingest_keeps_its_floor_while_the_rules_run(tmp_path: Path, db_engine: Engine) -> None:
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
    sessions = session_factory(db_engine)
    write(sessions)
    configure(sessions)
    world = World.create(tmp_path)
    world.reconfigure(ingest={"batch_rows": 5000, "batch_seconds": 0.5, "poll_seconds": 0.5})
    # Typed loosely: the Windows type check knows no fork context (the test skips there).
    context: Any = multiprocessing.get_context("fork")
    stop = context.Event()
    runs = context.Value("i", 0)
    url = db_engine.url.render_as_string(hide_password=False)
    worker = context.Process(target=_rules_again_and_again, args=(url, stop, runs))
    worker.start()
    try:
        result = measure_throughput(world, db_engine, lines=INGEST_LINES)
    finally:
        stop.set()
        worker.join(timeout=120)
    print(f"\ningest throughput while rules.run ran {runs.value} times: {result.summary()}")
    assert worker.exitcode == 0
    assert runs.value >= 1
    assert result.lines == INGEST_LINES
    assert result.lines_per_second >= INGEST_FLOOR, result.summary()
