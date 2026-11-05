"""Issue #185: the measurement of ``Attributor._missed_moves``' MAC branch, before any change.

Not a guard (no bound: the #179/#181 guards in ``test_lookup_performance.py`` keep theirs, and
their headroom is read from their own output, not from here): it prints, past pytest's capture,
what the planner does with the branch on the layout the test database has, so the
plain-PostgreSQL job and the TimescaleDB job each log their own figures. It costs a few minutes
on each perf job; ``ARCILOGGER_PERF=0`` (as the guard module) or ``ARCILOGGER_PERF_185=0``
switches it off, and it goes once #185's figures are posted.

**The world** is the recorded-moves guard's (``moves_site`` of the guard module, written with its
statements): 400 days of FortiGate DHCP ACKs on the staff Wi-Fi (MACs ``:00``-``:ff``), the 100
``mt_dhcp`` chains (MACs ``:00``-``:63``) of one lookup at ``MT_AT`` and their leases, each with
the 1,000 recorded times ``_missed_moves`` decodes. Its MAC branch reads every ACK of the chains'
MACs from the first of the lookup's days on. The converted-row guard's ``event_site`` adds
``fg_event`` lines and 240 ``fg_dhcp`` rows of other MACs (``:64``-``:8b``), and its leases record
no moves: only the guards' layout at ``1M`` is one guard's world. :data:`SCALES` writes it at the
guards' rate (one ACK every 75 s: the load generator's FortiGate DHCP share of 1 M lines a day)
and at five times the rows (5 M lines a day, the top of the volume ADR-0005 plans for): the extra
ACKs are other clients' (MACs ``:64``-``:ff``), the chains' MACs keep the rate of a 86,400 s lease,
so the branch's matched rows stay those of the guards and only the table grows.

**The layouts** (plain PostgreSQL; on TimescaleDB the partition calls do nothing and every layout
is the hypertables' one-day chunks, measured once): see :data:`DESCRIPTIONS`.

**As the product runs it**: the API's lookup plans every statement with its values
(``plan_cache_mode = force_custom_plan`` for its transaction, ``routers/lookup.py``), so every
lookup and statement here runs in a transaction that sets it (psycopg prepares a statement on its
fifth execution on a connection; without the setting PostgreSQL may then switch to a generic
plan). Each world prints the captured statement (the MAC branch is its first ``UNION ALL`` part);
the timings, best and median of :data:`RUNS` after a warm-up: the whole lookup, the check
``_missed_moves`` with its Python work, its statement as the lookup sent it, and the statement and
the MAC branch alone on one connection; the plans their executions used on the connection of one
more lookup (not timed; ``pg_prepared_statements``: custom and generic); and ``EXPLAIN (ANALYZE,
BUFFERS)`` of the statement and of the MAC branch alone, run in that lookup's own transaction right
after its statement ran, with the branch's estimate against what it read.
"""

from __future__ import annotations

import functools
import importlib.util
import json
import os
import platform
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
from sqlalchemy import Connection, Engine, event, text, update
from sqlalchemy.orm import Session

from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import Lease, Source
from arcilogger_db.partitions import (
    drop_before,
    ensure_partitions,
    hypertables,
    list_partitions,
    months_back_for,
    retention_days,
)
from arcilogger_db.settings import get_settings
from arcilogger_db.testing import TestServer, pause_compression_jobs
from arcilogger_worker.lookup import LookupOutcome, LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.lookup.attribution import Attributor

pytestmark = pytest.mark.skipif(
    "0" in (os.environ.get("ARCILOGGER_PERF", "1"), os.environ.get("ARCILOGGER_PERF_185", "1")),
    reason="#185 measurement switched off (ARCILOGGER_PERF=0 or ARCILOGGER_PERF_185=0)",
)

GUARDS = Path(__file__).with_name("test_lookup_performance.py")
#: FortiGate DHCP rows of the staff Wi-Fi, as a multiple of the guards' (``DHCP_STEP_SECONDS``),
#: by the volume a day they stand for.
SCALES = {"1M": 1, "5M": 5}
#: Measured runs after the warm-up.
RUNS = 9
#: The window of an earlier version's monthly layout (the 400 days of the guards' world).
MONTHLY_DAYS = 400
#: The statement's text (``_missed_moves``' one with the two ``JOIN fg_dhcp`` branches).
MARKS = ("WITH ORDINALITY", "JOIN fg_dhcp")
#: What the API's lookup sets for its transaction (``search.sql.custom_plans_statement``).
CUSTOM_PLANS = "SELECT set_config('plan_cache_mode', 'force_custom_plan', true)"

#: The other clients' ACKs of a busier site (MACs ``:64``-``:ff``, none of a chain's), spread
#: evenly over the same 400 days.
_BUSIER_DHCP = (
    "INSERT INTO fg_dhcp (time, time_received, source, sender, extra, raw_day, raw_line, "
    "raw_offset, logid, subtype, dhcp_msg, interface, ip, mac, lease, device_time) "
    "SELECT t, t, 'fortigate', '172.31.255.1', '{\"vd\": \"root\"}'::jsonb, :day, "
    "2000000 + g, g * 300, '0100026001', 'system', 'Ack', 'vlan60', "
    "('172.20.60.' || (10 + g % 200))::inet, "
    "('00:00:5e:00:53:' || to_hex(100 + g % 156))::macaddr, 86400, true "
    "FROM generate_series(1, :busier) AS g, "
    "LATERAL (SELECT CAST(:at AS timestamptz) - make_interval(secs => g * "
    "CAST(400 * 86400 AS double precision) / :busier) AS t) AS x"
)


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
    """One :func:`ensure_partitions` call, as the guards' ``_site`` makes it."""
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
        ensure_partitions(connection, today=day)


def _window(site: Engine) -> None:
    """``db.window``'s state once the world is written: everything older than ``db.retention_days``
    before the day dropped (:func:`drop_before`)."""
    with site.begin() as connection:
        drop_before(connection, guards().DAY - timedelta(days=retention_days(connection)))


#: The plain layouts: how each one's partitions are made in an empty site, and what is done once
#: the world is written.
LAYOUTS: dict[str, tuple[Callable[[Engine], None], Callable[[Engine], None] | None]] = {
    "guards": (_as_guards, None),
    "daily": (_daily, _window),
    "monthly": (_monthly, None),
}

DESCRIPTIONS = {
    "guards": "plain PostgreSQL, the guards' layout: one ensure_partitions call (16 daily "
    "partitions around the day, the template's month), the 400 days' other rows in "
    "fg_dhcp_default",
    "daily": "plain PostgreSQL, a site the worker keeps: every daily partition of the window "
    "(db.retention_days back, 7 ahead), then db.window's drop_before of the days before it "
    "(the 400 days' older rows of every table gone)",
    "monthly": f"plain PostgreSQL, an earlier version's site: monthly partitions of its "
    f"{MONTHLY_DAYS}-day window (ensure_partitions with months_back), then the worker's "
    "ensure_partitions run without months_back (daily partitions only for window days no month "
    "covers)",
    "timescaledb": "TimescaleDB, the hypertables' one-day chunks (compression paused)",
}


def _timescale(site: Engine) -> bool:
    with site.connect() as connection:
        return bool(hypertables(connection))


@contextmanager
def _world(
    server: TestServer, template: str, layout: str, factor: int
) -> Iterator[tuple[Engine, bool]]:
    """The recorded-moves guard's world at ``factor`` times its FortiGate DHCP rows, in a new
    database with ``layout``'s partitions, and whether the server is TimescaleDB."""
    g = guards()
    make, after = LAYOUTS[layout]
    with server.database(template) as name:
        site = engine_from_url(server.dsn_for(name), pool_size=5, max_overflow=10)
        try:
            timescale = _timescale(site)
            make(site)
            if g._compression(site):
                pause_compression_jobs(site)
            with Session(site) as session:
                for source, kind, address in g.GUARD_SOURCES:
                    session.add(Source(name=source, type=kind, address=address))
                session.commit()
            busier = (factor - 1) * 400 * 86400 // g.DHCP_STEP_SECONDS
            params = {
                "at": g.GUARD_AT,
                "fg_at": g.FG_AT,
                "mt_at": g.MT_AT,
                "long_at": g.LONG_AT,
                "day": g.DAY,
                "wan": g.WAN,
                "clients": g.CLIENTS,
                "dhcp_step": g.DHCP_STEP_SECONDS,
                "busier": busier,
            }
            began = time.perf_counter()
            with site.begin() as connection:
                for statement in g.MOVES_SITE:
                    connection.execute(text(statement), params)
                if busier:
                    connection.execute(text(_BUSIER_DHCP), params)
            _record_moves(site)
            if after is not None and not timescale:
                after(site)
            with site.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text("VACUUM (ANALYZE)"))
            print(
                f"\n#185 world ({layout}, {factor} x the guards' DHCP rows) written in "
                f"{time.perf_counter() - began:.1f} s"
            )
            yield site, timescale
        finally:
            site.dispose()


def _record_moves(site: Engine) -> None:
    """The moves the recorded-moves guard's ``moves_site`` gives every lease: ``RECORDED_TIMES``
    ACKs an hour apart from the lease's start on, marked truncated."""
    g = guards()
    start = g.GUARD_AT - timedelta(days=150)
    moved = g.MoveGroup(
        ip="172.20.44.58",
        interface="vlan44",
        vd="root",
        source="fortigate",
        logid="0100026001",
        dhcp_msg="Ack",
        times=tuple(g.epoch_us(start + timedelta(hours=k)) for k in range(1, g.RECORDED_TIMES + 1)),
    )
    with Session(site) as session:
        session.execute(update(Lease).values(moves=g.moves_json([moved], truncated=True)))
        session.commit()


@dataclass
class _Runs:
    """The times of one measured thing, a run each."""

    seconds: list[float] = field(default_factory=list)
    #: The server processes (connections) the runs used.
    backends: set[int] = field(default_factory=set)

    def line(self, name: str) -> str:
        best, median = min(self.seconds), statistics.median(self.seconds)
        each = " ".join(f"{value * 1000:.1f}" for value in self.seconds)
        return f"{name:26s} best {best * 1000:8.1f} ms  median {median * 1000:8.1f} ms  ({each})"


def _lookup(site: Engine) -> LookupOutcome:
    """One lookup as the API runs it: every statement planned with its values, rolled back."""
    g = guards()
    with Session(site) as session:
        session.execute(text(CUSTOM_PLANS))
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
    (found,) = [(sql, parameters) for sql, parameters in statements if all(m in sql for m in MARKS)]
    return outcome, *found


@contextmanager
def _statement_timed(site: Engine, runs: _Runs) -> Iterator[None]:
    """Each execution of ``_missed_moves``' statement while open, as the lookup sent it."""
    began: list[float] = []

    def before(*args: Any) -> None:
        if all(m in args[2] for m in MARKS):
            began.append(time.perf_counter())

    def after(*args: Any) -> None:
        if all(m in args[2] for m in MARKS):
            runs.seconds.append(time.perf_counter() - began.pop())
            runs.backends.add(args[1].connection.info.backend_pid)

    event.listen(site, "before_cursor_execute", before)
    event.listen(site, "after_cursor_execute", after)
    try:
        yield
    finally:
        event.remove(site, "after_cursor_execute", after)
        event.remove(site, "before_cursor_execute", before)


def _timed_lookups(site: Engine) -> tuple[_Runs, _Runs, _Runs]:
    """The whole lookup, its ``_missed_moves`` and the statement it sent, :data:`RUNS` times
    after a warm-up."""
    lookups, checks, sent = _Runs(), _Runs(), _Runs()
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
        with _statement_timed(site, sent):
            for _ in range(RUNS):
                spent.clear()
                began = time.perf_counter()
                _lookup(site)
                lookups.seconds.append(time.perf_counter() - began)
                checks.seconds.append(sum(spent))
    assert len(sent.seconds) == RUNS
    return lookups, checks, sent


def _timed_statement(site: Engine, sql: str, parameters: dict[str, Any]) -> _Runs:
    """``sql`` alone on one connection, planned with its values as in the lookup."""
    runs = _Runs()
    with site.begin() as connection:
        connection.execute(text(CUSTOM_PLANS))
        connection.exec_driver_sql(sql, parameters).all()
        for _ in range(RUNS):
            began = time.perf_counter()
            connection.exec_driver_sql(sql, parameters).all()
            runs.seconds.append(time.perf_counter() - began)
    return runs


@dataclass
class _Explained:
    """What one more lookup's transaction showed right after its statement ran."""

    plans: list[str] = field(default_factory=list)
    branch: list[str] = field(default_factory=list)
    figures: dict[str, Any] = field(default_factory=dict)
    prepared: list[str] = field(default_factory=list)
    plan_cache_mode: str = ""
    backend: int = 0


def _explained_lookup(site: Engine, mac_branch: str) -> _Explained:
    """One more lookup (not timed) in which, on its own connection and in its own transaction
    right after ``_missed_moves``' statement ran, ``EXPLAIN (ANALYZE, BUFFERS)`` of the statement
    and of the MAC branch alone runs with the statement's parameters, and the plans the
    statement's executions on that connection used are read (``pg_prepared_statements``)."""
    found = _Explained()

    def after(*args: Any) -> None:
        _connection, cursor, statement, parameters, _context, _many = args
        if not all(m in statement for m in MARKS) or found.plans:
            return
        found.backend = cursor.connection.info.backend_pid
        with cursor.connection.cursor() as other:
            explain = "EXPLAIN (ANALYZE, BUFFERS, FORMAT {}) {}"
            other.execute(explain.format("TEXT", statement), parameters)
            found.plans = [row[0] for row in other.fetchall()]
            other.execute(explain.format("TEXT", mac_branch), parameters)
            found.branch = [row[0] for row in other.fetchall()]
            other.execute(explain.format("JSON", mac_branch), parameters)
            value = other.fetchone()[0]
            found.figures = (json.loads(value) if isinstance(value, str) else value)[0]["Plan"]
            other.execute("SELECT current_setting('plan_cache_mode')")
            found.plan_cache_mode = other.fetchone()[0]
            # psycopg prepares it with $n placeholders: found by its marks.
            other.execute(
                "SELECT name, statement LIKE '%%UNION ALL%%', custom_plans, generic_plans "
                "FROM pg_prepared_statements "
                "WHERE statement LIKE %(ordinality)s AND statement LIKE %(join)s "
                "AND statement NOT LIKE 'EXPLAIN%%'",
                {"ordinality": f"%{MARKS[0]}%", "join": f"%{MARKS[1]}%"},
            )
            prepared = {
                "statement" if whole else "MAC branch alone": (name, custom, generic)
                for name, whole, custom, generic in other.fetchall()
            }
            found.prepared = [
                f"{what} ({name}): {custom} custom, {generic} generic plans"
                if what in prepared
                else f"{what}: not prepared on this connection (planned at each execution)"
                for what in ("statement", "MAC branch alone")
                for name, custom, generic in [prepared.get(what, ("", 0, 0))]
            ]

    event.listen(site, "after_cursor_execute", after)
    try:
        _lookup(site)
    finally:
        event.remove(site, "after_cursor_execute", after)
    assert found.plans
    return found


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
        default = _count(connection, "fg_dhcp_default") if found else 0
        total = _count(connection, "fg_dhcp")
        chunks = _chunks(connection)
    monthly = sum(1 for p in found if p.month is not None)
    daily = sum(1 for p in found if p.day is not None)
    if chunks is not None:
        return f"fg_dhcp: {total} rows in {chunks} chunks"
    return (
        f"fg_dhcp: {total} rows; {monthly} monthly and {daily} daily partitions, "
        f"{default} rows in fg_dhcp_default"
    )


def _count(connection: Connection, table: str) -> int:
    return int(connection.execute(text(f"SELECT count(*) FROM {table}")).scalar_one())  # noqa: S608


def _chunks(connection: Connection) -> int | None:
    if "fg_dhcp" not in hypertables(connection):
        return None
    return int(connection.execute(text("SELECT count(*) FROM show_chunks('fg_dhcp')")).scalar_one())


def _server(site: Engine) -> str:
    with site.connect() as connection:
        version = connection.execute(text("SELECT version()")).scalar_one()
    return f"machine: {platform.system()} {platform.machine()}; server: {version}"


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
    the check decoded the recorded moves, and the branch read every chain's ACKs on the staff
    Wi-Fi and nothing else."""
    g = guards()
    factor = SCALES[scale]
    for layout in LAYOUTS:
        with _world(pg_server, pg_template, layout, factor) as (site, timescale):
            name = "timescaledb" if timescale else layout
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
            with site.begin() as connection:
                connection.execute(text(CUSTOM_PLANS))
                rows = connection.exec_driver_sql(sql, parameters).all()
                branch_rows = connection.exec_driver_sql(mac_branch, parameters).all()
                moves = connection.execute(
                    text("SELECT count(*) FROM leases WHERE moves IS NOT NULL")
                ).scalar_one()
            assert moves == 100
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

            lookups, checks, sent = _timed_lookups(site)
            branch = _timed_statement(site, mac_branch, parameters)
            whole = _timed_statement(site, sql, parameters)
            explained = _explained_lookup(site, mac_branch)
            assert explained.plan_cache_mode == "force_custom_plan"
            _show(
                capsys,
                [
                    f"===== #185 before: {DESCRIPTIONS[name]}; {factor} x the guards' FortiGate "
                    f"DHCP rows ({scale} lines a day) =====",
                    _server(site),
                    _partitions(site),
                    "world: the recorded-moves guard's (moves_site: 1,000 recorded times a "
                    "lease); the converted-row guard's event_site differs (fg_event lines, "
                    "240 more fg_dhcp rows, no recorded moves). The guards' headroom is their "
                    "own output, not these figures.",
                    f"ACKs of the 100 chains' MACs from the first of the lookup's days: {read}; "
                    f"rows returned: {len(rows)}",
                    "--- timings (each transaction: plan_cache_mode = force_custom_plan) ---",
                    lookups.line("whole lookup"),
                    checks.line("_missed_moves"),
                    sent.line("its statement, as sent"),
                    whole.line("statement alone (both)"),
                    branch.line("MAC branch alone"),
                    f"--- the plans used (pg_prepared_statements of the explained lookup's "
                    f"connection, backend {explained.backend}; the timed lookups' statement "
                    f"ran on backends {sorted(sent.backends)}) ---",
                    *explained.prepared,
                    "--- MAC branch figures (in a lookup's transaction) ---",
                    *_figures(explained.figures),
                    "--- statement (as sent; parameters bound) ---",
                    sql,
                    "--- EXPLAIN (ANALYZE, BUFFERS) of the statement, in a lookup's transaction "
                    "right after it ran ---",
                    *explained.plans,
                    "--- EXPLAIN (ANALYZE, BUFFERS) of the MAC branch alone, same transaction ---",
                    *explained.branch,
                    f"===== end of {name}, {scale} =====",
                ],
            )
            if timescale:
                break
