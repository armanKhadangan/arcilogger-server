"""Issue #185: the measurement of ``Attributor._missed_moves``' MAC branch, before any change.

Not a guard (no bound: the #179/#181 guards in ``test_lookup_performance.py`` keep theirs): it
prints, past pytest's capture, what the planner does with the branch on the layout the test
database has, so the plain-PostgreSQL job and the TimescaleDB job each log their own figures.

**The world** is the moves guard's (``MOVES_SITE`` of the guard module, written with its
statements): 400 days of FortiGate DHCP ACKs on the staff Wi-Fi (MACs ``:00``-``:ff``) and the 100
``mt_dhcp`` chains (MACs ``:00``-``:63``) of one lookup at ``MT_AT``, whose MAC branch reads every
ACK of the chains' MACs from the first of the lookup's days on. :data:`SCALES` writes it at the
guards' rate (one ACK every 75 s: the load generator's FortiGate DHCP share of 1 M lines a day)
and at five times that (5 M lines a day, the top of the volume ADR-0005 plans for); the
fictional MAC range has 256 addresses, so a busier site is more ACKs a MAC, not more MACs.

**The layouts** (plain PostgreSQL; on TimescaleDB the partition calls do nothing and every layout
is the hypertables' one-day chunks, measured once):

* ``guards``: as the guards' sites are made (one :func:`ensure_partitions` call, so 16 daily
  partitions around the day, ``DAYS_PER_RUN``; the template's month; the rest, the start of the
  lookup's days included, in ``fg_dhcp_default``): the windows-latest figures of #179/#181;
* ``daily``: a site the worker keeps: every daily partition of the window (``db.retention_days``
  back, 7 ahead), older rows in ``fg_dhcp_default``;
* ``monthly``: an earlier version's site: monthly partitions of its 400-day window (local
  midnight bounds), as ``months_back`` still makes them.

Each prints the captured statement (the MAC branch is its first ``UNION ALL`` part), ``EXPLAIN
(ANALYZE, BUFFERS)`` of the whole statement and of the MAC branch alone with the lookup's own
parameters, the branch's estimate against what it read, and the timings: the whole lookup, the
check (``_missed_moves``, its Python work included) and the MAC branch alone, best and median of
:data:`RUNS` after a warm-up. psycopg prepares a statement on its fifth execution on one
connection, so the runs list every time (a generic plan would show as a step).
"""

from __future__ import annotations

import functools
import importlib.util
import json
import statistics
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from functools import cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session

from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import Source
from arcilogger_db.partitions import (
    ensure_partitions,
    hypertables,
    list_partitions,
    months_back_for,
)
from arcilogger_db.settings import get_settings
from arcilogger_db.testing import TestServer, pause_compression_jobs
from arcilogger_worker.lookup import LookupOutcome, LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.lookup.attribution import Attributor

GUARDS = Path(__file__).with_name("test_lookup_performance.py")
#: ACKs of the staff Wi-Fi: one every this many seconds (the guards' ``DHCP_STEP_SECONDS`` and a
#: fifth of it), by the volume a day it stands for.
SCALES = {"1M": 75, "5M": 15}
#: Measured runs after the warm-up.
RUNS = 9
#: The window of an earlier version's monthly layout (the 400 days of the guards' world).
MONTHLY_DAYS = 400


@cache
def guards() -> ModuleType:
    """The guard module, for its world: its statements, sources and moments (it is the
    #179/#181 guards' world that #185 is about)."""
    spec = importlib.util.spec_from_file_location("lookup_performance_guards_185", GUARDS)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _as_guards(site: Engine) -> None:
    """One :func:`ensure_partitions` call, as the guards' ``_site`` makes it (on TimescaleDB it
    does nothing: the hypertables' chunks)."""
    with site.begin() as connection:
        ensure_partitions(connection, today=guards().DAY)


def _daily(site: Engine) -> None:
    while True:
        with site.begin() as connection:
            if not ensure_partitions(connection, today=guards().DAY).remaining:
                return


def _monthly(site: Engine) -> None:
    day = guards().DAY
    with site.begin() as connection:
        ensure_partitions(connection, today=day, months_back=months_back_for(day, MONTHLY_DAYS))
    with site.begin() as connection:
        ensure_partitions(connection, today=day)  # the worker's run: no day left uncovered


#: The plain layouts, each with how its partitions are made in an empty site.
LAYOUTS: dict[str, Callable[[Engine], None]] = {
    "guards": _as_guards,
    "daily": _daily,
    "monthly": _monthly,
}

DESCRIPTIONS = {
    "guards": "plain PostgreSQL, the guards' layout (16 daily partitions, the rest in "
    "fg_dhcp_default)",
    "daily": "plain PostgreSQL, daily partitions of the whole window (older rows in "
    "fg_dhcp_default)",
    "monthly": f"plain PostgreSQL, monthly partitions of a {MONTHLY_DAYS}-day window",
    "timescaledb": "TimescaleDB, one-day chunks (compression paused)",
}


def _timescale(server: TestServer, template: str) -> bool:
    with server.database(template) as name:
        site = engine_from_url(server.dsn_for(name))
        try:
            with site.connect() as connection:
                return bool(hypertables(connection))
        finally:
            site.dispose()


@contextmanager
def _world(server: TestServer, template: str, layout: str, step: int) -> Iterator[Engine]:
    """The guards' moves world (without the recorded moves) at one ACK every ``step`` seconds,
    in a new database with ``layout``'s partitions."""
    g = guards()
    with server.database(template) as name:
        site = engine_from_url(server.dsn_for(name), pool_size=5, max_overflow=10)
        try:
            LAYOUTS.get(layout, _as_guards)(site)
            if g._compression(site):
                pause_compression_jobs(site)
            with Session(site) as session:
                for source, kind, address in g.GUARD_SOURCES:
                    session.add(Source(name=source, type=kind, address=address))
                session.commit()
            params = {
                "at": g.GUARD_AT,
                "fg_at": g.FG_AT,
                "mt_at": g.MT_AT,
                "long_at": g.LONG_AT,
                "day": g.DAY,
                "wan": g.WAN,
                "clients": g.CLIENTS,
                "dhcp_step": step,
            }
            began = time.perf_counter()
            with site.begin() as connection:
                for statement in g.MOVES_SITE:
                    connection.execute(text(statement), params)
            with site.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text("VACUUM (ANALYZE)"))
            print(
                f"\n#185 world ({layout}, one ACK every {step} s) written in "
                f"{time.perf_counter() - began:.1f} s"
            )
            yield site
        finally:
            site.dispose()


@dataclass
class _Runs:
    """The times of one measured thing, a run each."""

    seconds: list[float] = field(default_factory=list)

    def line(self, name: str) -> str:
        best, median = min(self.seconds), statistics.median(self.seconds)
        each = " ".join(f"{value * 1000:.1f}" for value in self.seconds)
        return f"{name:24s} best {best * 1000:8.1f} ms  median {median * 1000:8.1f} ms  ({each})"


def _lookup(site: Engine) -> LookupOutcome:
    g = guards()
    with Session(site) as session:
        settings = LookupSettings.from_settings(get_settings(session))
        outcome = run_lookup(
            session,
            LookupRequest(at=g.MT_AT, public_ip=g.PUBLIC),
            settings,
            now=g.GUARD_AT + timedelta(days=2),
        )
        session.rollback()
    return outcome


def _captured(site: Engine) -> tuple[LookupOutcome, str, dict[str, Any]]:
    """A lookup with its statements recorded, and ``_missed_moves``' statement."""
    statements: list[tuple[str, dict[str, Any]]] = []

    def executed(*args: Any) -> None:
        _connection, _cursor, statement, parameters, _context, _many = args
        statements.append((statement, dict(parameters)))

    event.listen(site, "before_cursor_execute", executed)
    try:
        outcome = _lookup(site)
    finally:
        event.remove(site, "before_cursor_execute", executed)
    (found,) = [
        (sql, parameters)
        for sql, parameters in statements
        if "WITH ORDINALITY" in sql and "JOIN fg_dhcp" in sql
    ]
    return outcome, *found


def _timed_lookups(site: Engine) -> tuple[_Runs, _Runs]:
    """The whole lookup and its ``_missed_moves``, :data:`RUNS` times after a warm-up."""
    lookups, checks = _Runs(), _Runs()
    spent: list[float] = []
    original = Attributor._missed_moves

    @functools.wraps(original)
    def timed(*args: Any, **kwargs: Any) -> Any:
        began = time.perf_counter()
        try:
            return original(*args, **kwargs)
        finally:
            spent.append(time.perf_counter() - began)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Attributor, "_missed_moves", timed)
        _lookup(site)
        for _ in range(RUNS):
            spent.clear()
            began = time.perf_counter()
            _lookup(site)
            lookups.seconds.append(time.perf_counter() - began)
            checks.seconds.append(sum(spent))
    return lookups, checks


def _timed_statement(site: Engine, sql: str, parameters: dict[str, Any]) -> _Runs:
    runs = _Runs()
    with site.connect() as connection:
        connection.exec_driver_sql(sql, parameters).all()
        for _ in range(RUNS):
            began = time.perf_counter()
            connection.exec_driver_sql(sql, parameters).all()
            runs.seconds.append(time.perf_counter() - began)
    return runs


def _explained(site: Engine, sql: str, parameters: dict[str, Any], form: str) -> Any:
    """``EXPLAIN (ANALYZE, BUFFERS, FORMAT <form>)`` of ``sql``, the second of two (the first
    warms the cache): the lines of the text, or the JSON plan."""
    with site.connect() as connection:
        for _ in range(2):
            rows = connection.exec_driver_sql(
                f"EXPLAIN (ANALYZE, BUFFERS, FORMAT {form}) {sql}", parameters
            ).all()
    if form == "JSON":
        value = rows[0][0]
        return (json.loads(value) if isinstance(value, str) else value)[0]["Plan"]
    return [row[0] for row in rows]


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", ()):
        yield from _nodes(child)


def _reads_fg_dhcp(node: dict[str, Any]) -> bool:
    return any(str(n.get("Relation Name", "")).startswith("fg_dhcp") for n in _nodes(node))


def _figures(plan: dict[str, Any]) -> list[str]:
    """The MAC branch's estimate against what it read: the side of its outermost join that reads
    ``fg_dhcp`` (an Append of the partitions, TimescaleDB's ChunkAppend, or one scan), and every
    join with the rows its filter removed."""
    join = next((node for node in _nodes(plan) if "Join Type" in node), None)
    reading = plan if join is None else next(c for c in join["Plans"] if _reads_fg_dhcp(c))
    scans = [node for node in _nodes(reading) if "Relation Name" in node]
    lines = [
        f"fg_dhcp side: {reading['Node Type']} estimated {reading['Plan Rows']} rows, actual "
        f"{reading['Actual Rows']} x {reading['Actual Loops']} loops, {len(scans)} "
        f"partition/chunk scans ({', '.join(sorted({n['Node Type'] for n in scans}))})"
    ]
    for node in _nodes(plan):
        if "Join Type" in node:
            lines.append(
                f"{node['Node Type']} ({node['Join Type']}): estimated {node['Plan Rows']}, "
                f"actual {node['Actual Rows']} x {node['Actual Loops']}, rows removed by join "
                f"filter {node.get('Rows Removed by Join Filter', 0)}"
            )
    return lines


def _partitions(site: Engine) -> str:
    with site.connect() as connection:
        found = list_partitions(connection, "fg_dhcp")
        default = connection.execute(text("SELECT count(*) FROM fg_dhcp_default")).scalar_one()
        total = connection.execute(text("SELECT count(*) FROM fg_dhcp")).scalar_one()
    monthly = sum(1 for p in found if p.month is not None)
    daily = sum(1 for p in found if p.day is not None)
    return (
        f"fg_dhcp: {total} rows; {monthly} monthly and {daily} daily partitions, "
        f"{default} rows in fg_dhcp_default"
    )


def _show(capsys: pytest.CaptureFixture[str], lines: Sequence[str]) -> None:
    # Past the capture: ``poe test-perf`` runs without ``-s``, and the job log must show it.
    with capsys.disabled():
        print("\n" + "\n".join(lines), flush=True)


@pytest.mark.perf
@pytest.mark.parametrize("scale", sorted(SCALES))
def test_the_missed_moves_mac_branch_is_measured(
    pg_server: TestServer,
    pg_template: str,
    capsys: pytest.CaptureFixture[str],
    scale: str,
) -> None:
    """#185 step 1: the plan and the times of the MAC branch on every layout of this server's
    kind, printed; the lookup's answer is the guards' (100 ``mt_dhcp`` chains, none lowered),
    and the branch read every chain's ACKs on the staff Wi-Fi and nothing else."""
    g = guards()
    step = SCALES[scale]
    layouts = ["timescaledb"] if _timescale(pg_server, pg_template) else list(LAYOUTS)
    for layout in layouts:
        with _world(pg_server, pg_template, layout, step) as site:
            outcome, sql, parameters = _captured(site)
            assert len(outcome.matches) == 100
            for match in outcome.matches:
                chain = match.chain
                assert chain is not None
                assert chain.lease is not None
                assert chain.lease.source_table == "mt_dhcp"
                assert chain.confidence in ("high", "medium")
            assert sql.count(" UNION ALL ") == 1
            mac_branch = sql.split(" UNION ALL ")[0]
            with site.connect() as connection:
                rows = connection.exec_driver_sql(sql, parameters).all()
                branch_rows = connection.exec_driver_sql(mac_branch, parameters).all()
            assert {n for n, *_ in rows} == set(range(1, 101))
            assert {
                (str(ip).startswith("172.20.60."), interface, held)
                for _n, ip, interface, _vd, held in rows
            } == {(True, "vlan60", False)}
            assert sorted(branch_rows) == sorted(rows)
            with site.connect() as connection:
                read = connection.execute(
                    text(
                        "SELECT count(*) FROM fg_dhcp WHERE mac IN (SELECT mac FROM leases "
                        "WHERE source_table = 'mt_dhcp') AND time > :low AND time < :high"
                    ),
                    {"low": g._first_read(site, g.MT_AT), "high": g.MT_AT},
                ).scalar_one()

            lookups, checks = _timed_lookups(site)
            branch = _timed_statement(site, mac_branch, parameters)
            whole = _timed_statement(site, sql, parameters)
            text_plan = _explained(site, sql, parameters, "TEXT")
            branch_plan = _explained(site, mac_branch, parameters, "TEXT")
            figures = _figures(_explained(site, mac_branch, parameters, "JSON"))
            _show(
                capsys,
                [
                    f"===== #185 before: {DESCRIPTIONS[layout]}; one ACK every {step} s "
                    f"({scale} lines a day) =====",
                    _partitions(site),
                    f"ACKs of the 100 chains' MACs from the first of the lookup's days: {read}; "
                    f"rows returned: {len(rows)}",
                    "--- timings ---",
                    lookups.line("whole lookup"),
                    checks.line("_missed_moves"),
                    whole.line("statement (both)"),
                    branch.line("MAC branch alone"),
                    "--- MAC branch figures ---",
                    *figures,
                    "--- statement (as sent; parameters bound) ---",
                    sql,
                    "--- EXPLAIN (ANALYZE, BUFFERS) of the statement ---",
                    *text_plan,
                    "--- EXPLAIN (ANALYZE, BUFFERS) of the MAC branch alone ---",
                    *branch_plan,
                    f"===== end of {layout}, {scale} =====",
                ],
            )
