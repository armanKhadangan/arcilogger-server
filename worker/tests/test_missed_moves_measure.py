"""Issue #185: the measurement of ``Attributor._missed_moves``' MAC branch (W5-G-26).

Not a guard (no bound: the #179/#181 guards in ``test_lookup_performance.py`` keep theirs, and
their headroom is read from their own output, not from here; the plan W5-G-26 chose is pinned by
``test_missed_moves_plan.py``): it prints, past pytest's capture, what the planner does with the
branch on the layout the test database has. It measures the code as it is and, side by side in
the same transactions, the form before W5-G-26 (:func:`_forms`). It costs a few minutes on each
perf job. While
#185's pull request is a draft it runs in ``test-perf``, so its CI logs the TimescaleDB and the
Windows figures (#185, Q2); ``ARCILOGGER_PERF=0`` (as the guard module) or
``ARCILOGGER_PERF_185=0`` switches it off. Before the pull request is ready it becomes opt-in
(only with ``ARCILOGGER_PERF_185=1``)::

    uv run --frozen poe test-perf worker/tests/test_missed_moves_measure.py

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
lookup and statement here runs in a transaction that sets it (psycopg prepares a statement on the
execution after its ``prepare_threshold``-th on a connection, the sixth by default; without the
setting PostgreSQL may then switch to a generic plan). Each world prints the captured statement
(the MAC branch is its first ``UNION ALL`` part); the timings, best and median of :data:`RUNS`: the
whole lookup, the check ``_missed_moves`` with its Python work and its statement as the lookup sent
it, after a warm-up lookup, and the statement and the MAC branch alone on one connection, after a
warm-up that leaves each prepared (:func:`_warmed`); the plans the executions of each form used
on the connection of one more lookup (not timed; ``pg_prepared_statements``: custom and generic);
and ``EXPLAIN (ANALYZE, BUFFERS)`` of the statement and of the MAC branch alone, run in that
lookup's own transaction right after its statement ran, with the branch's estimate against what it
read (``EXPLAIN``'s rows are a loop's average: printed with the loops, ``x``).

**Old against new** (W5-G-26): the old form is the captured statement with its MAC branch's
conversion test written back from ``CASE WHEN (<kept>) THEN true ELSE false END`` to the bare
``<kept>`` (``CASE ... END IS NULL``), an exact text rewrite that fails when the wrapper is not
found once in the MAC branch (the IP branch carries the bare test as it was). Both forms' MAC
branches return the same rows; both are explained in the explained lookup's transaction (the
branch's estimate against what it read, its joins and the rows their filters removed, and the
statement's ``Gather`` nodes, each marked when it is in an InitPlan or a SubPlan), and the MAC
branch and the statement of both are timed on one connection in one transaction, old and new
alternating over :data:`RUNS` rounds after a warm-up that leaves both prepared, so that neither
is parsed in a timed round (:func:`_alternated`). Each world's summary has an "old → new" line.

**Twice a world** (:data:`STAGES`): as the module loads it (written, then one ``VACUUM (ANALYZE)``
of the whole database, as the guards' ``_site`` does), and again after an explicit ``ANALYZE`` of
``fg_dhcp`` and of every one of its partitions (``fg_dhcp_default`` included) or chunks, with the
statistics ``fg_dhcp`` holds before each; a summary of both closes each scale. It answers whether
the branch's misestimate is a world measured before its statistics were gathered (#185, Q1).
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
#: The MAC branch's conversion test as W5-G-26 writes it: ``CASE WHEN (<kept>) THEN true ELSE
#: false END`` around the bare test (``<kept>`` is ``CASE WHEN (...) ... END IS NULL``).
WRAPPER_OPEN = "CASE WHEN ("
WRAPPER_CLOSE = ") THEN true ELSE false END"
BARE_OPEN, BARE_CLOSE = "CASE WHEN (", " END IS NULL"
#: What the API's lookup sets for its transaction (``search.sql.custom_plans_statement``).
CUSTOM_PLANS = "SELECT set_config('plan_cache_mode', 'force_custom_plan', true)"
#: The forms of ``_missed_moves``' statement psycopg prepared on its connection (with ``$n``
#: placeholders, found by their marks), whether each is the whole statement, and whether it is the
#: new form (only the new one carries the wrapper's close, :func:`_forms`).
PREPARED = (
    "SELECT statement LIKE '%%UNION ALL%%', statement LIKE %(new)s, name, custom_plans, "
    "generic_plans FROM pg_prepared_statements "
    "WHERE statement LIKE %(ordinality)s AND statement LIKE %(join)s "
    "AND statement NOT LIKE 'EXPLAIN%%'"
)
#: The forms :data:`PREPARED` tells apart, as the output names them.
STATEMENT, BRANCH = "statement", "MAC branch alone"
FORMS = (STATEMENT, BRANCH, f"old: {STATEMENT}", f"old: {BRANCH}")
#: Each world is measured at each stage, in this order (#185, Q1).
LOADED, ANALYZED = "as loaded", "after ANALYZE"
STAGES = {
    LOADED: "as the module loads it: the world written, then one VACUUM (ANALYZE) of the whole "
    "database (the guards' _site does the same)",
    ANALYZED: "after ANALYZE fg_dhcp: the parent (its inherited statistics) and every partition, "
    "fg_dhcp_default included (TimescaleDB: the hypertable and every chunk), on top of the above",
}

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


@dataclass(frozen=True)
class _Forms:
    """``_missed_moves``' statement and its MAC branch as sent (new), and both with the MAC
    branch's conversion test written back to the bare test (old, before W5-G-26)."""

    sql: str
    branch: str
    old_sql: str
    old_branch: str
    #: The bare test (``<kept>``) the rewrite leaves.
    bare: str


def _forms(sql: str) -> _Forms:
    """The old form of ``sql``: its MAC branch's ``CASE WHEN (<kept>) THEN true ELSE false END``
    replaced by ``<kept>``, by text. Fails unless the MAC branch has the wrapper exactly once and
    the IP branch carries the same ``<kept>`` bare (the one test, rendered with the same
    parameters in both)."""
    assert sql.count(" UNION ALL ") == 1
    branch, held = sql.split(" UNION ALL ")
    opening = WRAPPER_OPEN + BARE_OPEN
    closing = BARE_CLOSE + WRAPPER_CLOSE
    assert branch.count(opening) == 1, f"the MAC branch's wrapper opens {branch.count(opening)} x"
    assert branch.count(closing) == 1, f"the MAC branch's wrapper closes {branch.count(closing)} x"
    start = branch.index(opening)
    end = branch.index(closing) + len(closing)
    wrapper = branch[start:end]
    bare = wrapper[len(WRAPPER_OPEN) : -len(WRAPPER_CLOSE)]
    assert wrapper == WRAPPER_OPEN + bare + WRAPPER_CLOSE
    assert bare.startswith(BARE_OPEN)
    assert bare.endswith(BARE_CLOSE)
    assert bare.count("(") == bare.count(")"), "the bare test's parentheses do not pair"
    assert held.count(opening) == 0, "the IP branch has the wrapper too"
    assert held.count(closing) == 0, "the IP branch has the wrapper too"
    assert held.count(bare) == 1, "the IP branch does not carry the bare test once"
    assert sql.count(wrapper) == 1
    old_branch = branch.replace(wrapper, bare)
    old_sql = sql.replace(wrapper, bare)
    assert old_sql == f"{old_branch} UNION ALL {held}"
    assert old_branch.count(bare) == 1
    assert WRAPPER_CLOSE not in old_sql
    return _Forms(sql=sql, branch=branch, old_sql=old_sql, old_branch=old_branch, bare=bare)


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


def _prepared(driver: Any) -> dict[str, tuple[str, int, int]]:
    """The forms of ``_missed_moves``' statement prepared on ``driver`` (a psycopg connection),
    by their :data:`FORMS` name: the prepared statement's name, its custom and generic plans.
    Fails when one form is prepared twice (the output would then show one of them at random)."""
    with driver.cursor() as cursor:
        cursor.execute(
            PREPARED,
            {
                "new": f"%{WRAPPER_CLOSE}%",
                "ordinality": f"%{MARKS[0]}%",
                "join": f"%{MARKS[1]}%",
            },
        )
        rows = cursor.fetchall()
    found: dict[str, tuple[str, int, int]] = {}
    for whole, new, name, custom, generic in rows:
        what = ("" if new else "old: ") + (STATEMENT if whole else BRANCH)
        assert what not in found, f"{what} prepared twice: {found[what][0]} and {name}"
        found[what] = (name, custom, generic)
    return found


def _warmed(connection: Connection, forms: dict[str, str], parameters: dict[str, Any]) -> None:
    """Each of ``forms`` (its :data:`FORMS` name: its text) executed on ``connection`` until psycopg
    has prepared it (``prepare_threshold`` + 1 executions: a timed run is never parsed), or once
    when the connection prepares nothing; fails unless each is then prepared."""
    driver = connection.connection.driver_connection
    assert driver is not None
    threshold = driver.prepare_threshold
    for sql in forms.values():
        for _ in range(1 if threshold is None else threshold + 1):
            connection.exec_driver_sql(sql, parameters).all()
    if threshold is not None:
        prepared = _prepared(driver)
        assert set(forms) <= set(prepared), f"not prepared: {sorted(set(forms) - set(prepared))}"


def _timed_statement(site: Engine, what: str, sql: str, parameters: dict[str, Any]) -> _Runs:
    """``sql`` (``what`` of :data:`FORMS`) alone on one connection, planned with its values as in
    the lookup, after :func:`_warmed`."""
    runs = _Runs()
    with site.begin() as connection:
        connection.execute(text(CUSTOM_PLANS))
        _warmed(connection, {what: sql}, parameters)
        for _ in range(RUNS):
            began = time.perf_counter()
            connection.exec_driver_sql(sql, parameters).all()
            runs.seconds.append(time.perf_counter() - began)
    return runs


def _alternated(
    site: Engine, what: str, old: str, new: str, parameters: dict[str, Any]
) -> tuple[_Runs, _Runs]:
    """``old`` and ``new`` (``what`` of :data:`FORMS`, new) on one connection in one transaction,
    planned with their values as in the lookup: :func:`_warmed` (both prepared, whatever the
    connection ran before), then :data:`RUNS` rounds of both, old first in the odd rounds (the
    first, the third, ...) and new first in the even ones."""
    assert old != new
    runs = {old: _Runs(), new: _Runs()}
    with site.begin() as connection:
        connection.execute(text(CUSTOM_PLANS))
        _warmed(connection, {f"old: {what}": old, what: new}, parameters)
        for each in range(RUNS):
            for sql in (old, new) if each % 2 == 0 else (new, old):
                began = time.perf_counter()
                connection.exec_driver_sql(sql, parameters).all()
                runs[sql].seconds.append(time.perf_counter() - began)
    return runs[old], runs[new]


@dataclass
class _Explained:
    """What one more lookup's transaction showed right after its statement ran."""

    plans: list[str] = field(default_factory=list)
    branch: list[str] = field(default_factory=list)
    figures: dict[str, Any] = field(default_factory=dict)
    #: The whole statement's plan (``FORMAT JSON``).
    statement: dict[str, Any] = field(default_factory=dict)
    #: The same of the old form (:func:`_forms`), explained in the same transaction.
    old_plans: list[str] = field(default_factory=list)
    old_branch: list[str] = field(default_factory=list)
    old_figures: dict[str, Any] = field(default_factory=dict)
    old_statement: dict[str, Any] = field(default_factory=dict)
    prepared: list[str] = field(default_factory=list)
    plan_cache_mode: str = ""
    backend: int = 0


def _explained_lookup(site: Engine, forms: _Forms) -> _Explained:
    """One more lookup (not timed) in which, on its own connection and in its own transaction
    right after ``_missed_moves``' statement ran, ``EXPLAIN (ANALYZE, BUFFERS)`` of the statement
    and of the MAC branch alone runs with the statement's parameters, then the same of their old
    forms, and the plans the statement's executions on that connection used are read
    (``pg_prepared_statements``)."""
    found = _Explained()

    def after(*args: Any) -> None:
        _connection, cursor, statement, parameters, _context, _many = args
        if not all(m in statement for m in MARKS) or found.plans:
            return
        # The old forms are this statement's: the lookup sent the one captured before.
        assert statement == forms.sql
        found.backend = cursor.connection.info.backend_pid
        with cursor.connection.cursor() as other:

            def explained(sql: str) -> tuple[list[str], dict[str, Any]]:
                explain = "EXPLAIN (ANALYZE, BUFFERS, FORMAT {}) {}"
                other.execute(explain.format("TEXT", sql), parameters)
                lines = [row[0] for row in other.fetchall()]
                other.execute(explain.format("JSON", sql), parameters)
                value = other.fetchone()[0]
                return lines, (json.loads(value) if isinstance(value, str) else value)[0]["Plan"]

            found.plans, found.statement = explained(statement)
            found.branch, found.figures = explained(forms.branch)
            found.old_plans, found.old_statement = explained(forms.old_sql)
            found.old_branch, found.old_figures = explained(forms.old_branch)
            other.execute("SELECT current_setting('plan_cache_mode')")
            found.plan_cache_mode = other.fetchone()[0]
            prepared = _prepared(cursor.connection)
            found.prepared = [
                f"{what} ({name}): {custom} custom, {generic} generic plans"
                if what in prepared
                else f"{what}: not prepared on this connection (planned at each execution)"
                for what in FORMS
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
    """Whether the node scans a table: the MAC branch's only one is ``fg_dhcp`` (a partition,
    ``fg_dhcp_default`` or, on TimescaleDB, a ``_hyper_*`` chunk; ``windows`` is a function)."""
    return any("Relation Name" in n for n in _nodes(node))


def _shape(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """The side of the MAC branch's outermost join that reads ``fg_dhcp`` (an Append of the
    partitions, TimescaleDB's ChunkAppend, or one scan), and that join (none: one scan)."""
    join = next((node for node in _nodes(plan) if "Join Type" in node), None)
    if join is None:
        return plan, None
    reading = next((c for c in join["Plans"] if _reads_fg_dhcp(c)), None)
    assert reading is not None, f"no side of the {join['Node Type']} reads a table"
    return reading, join


def _figures(plan: dict[str, Any]) -> list[str]:
    """The MAC branch's estimate against what it read: the side of its outermost join that reads
    ``fg_dhcp``, and every join with the rows its filter removed (``EXPLAIN``'s actual and removed
    rows are a loop's average: x the loops is the total)."""
    reading, _join = _shape(plan)
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
                f"filter {node.get('Rows Removed by Join Filter', 0)} x {node['Actual Loops']}"
            )
    return lines


def _parallel(plan: dict[str, Any]) -> str:
    """A plan's ``Gather`` and ``Gather Merge`` nodes with their workers, each one in an InitPlan
    or a SubPlan (the IP branch's ``ARRAY(SELECT unnest(...))`` is one) marked as such: only the
    others run part of the tree the statement's rows come from."""
    found: list[str] = []

    def walk(node: dict[str, Any], sub: bool) -> None:
        sub = sub or node.get("Parent Relationship") in ("InitPlan", "SubPlan")
        if node["Node Type"] in ("Gather", "Gather Merge"):
            found.append(
                f"{node['Node Type']} ({node.get('Workers Launched', 0)} of "
                f"{node.get('Workers Planned', 0)} workers"
                f"{', in an InitPlan/SubPlan' if sub else ''})"
            )
        for child in node.get("Plans", ()):
            walk(child, sub)

    walk(plan, False)
    return ", ".join(found) if found else "no Gather"


def _times(runs: _Runs) -> str:
    return f"{min(runs.seconds) * 1000:.1f}/{statistics.median(runs.seconds) * 1000:.1f}"


def _old_new(
    explained: _Explained, branches: tuple[_Runs, _Runs], statements: tuple[_Runs, _Runs]
) -> str:
    """The old form's figures against the new one's, on one line."""
    (old_reading, old_join), (new_reading, new_join) = (
        _shape(explained.old_figures),
        _shape(explained.figures),
    )

    def join(node: dict[str, Any] | None) -> str:
        return "no join" if node is None else f"{node['Node Type']} ({node['Join Type']})"

    def removed(node: dict[str, Any] | None) -> str:
        if node is None:
            return "0"
        return f"{node.get('Rows Removed by Join Filter', 0)} x {node['Actual Loops']}"

    def actual(node: dict[str, Any]) -> str:
        return f"{node['Actual Rows']} x {node['Actual Loops']}"

    return (
        f"old → new: fg_dhcp est {old_reading['Plan Rows']} → {new_reading['Plan Rows']} "
        f"(actual {actual(old_reading)} → {actual(new_reading)}); "
        f"{join(old_join)} → {join(new_join)}, join filter removed {removed(old_join)} → "
        f"{removed(new_join)}; MAC branch best/median {_times(branches[0])} → "
        f"{_times(branches[1])} ms; statement best/median {_times(statements[0])} → "
        f"{_times(statements[1])} ms; statement {_parallel(explained.old_statement)} → "
        f"{_parallel(explained.statement)}"
    )


def _summary(plan: dict[str, Any]) -> str:
    """The figures of :func:`_figures` on one line: the ``fg_dhcp`` side and the outermost join."""
    reading, join = _shape(plan)
    found = (
        f"fg_dhcp {reading['Node Type']} est {reading['Plan Rows']} / actual "
        f"{reading['Actual Rows']} x {reading['Actual Loops']}"
    )
    if join is None:
        return f"{found}; no join"
    return (
        f"{found}; {join['Node Type']}, join filter removed "
        f"{join.get('Rows Removed by Join Filter', 0)} x {join['Actual Loops']}"
    )


def _children(connection: Connection) -> list[str]:
    """``fg_dhcp``'s partitions (``fg_dhcp_default`` included) or TimescaleDB chunks, quoted."""
    return [
        str(name)
        for name in connection.execute(
            text(
                "SELECT inhrelid::regclass::text FROM pg_inherits "
                "WHERE inhparent = 'fg_dhcp'::regclass ORDER BY 1"
            )
        ).scalars()
    ]


def _analyze_fg_dhcp(site: Engine) -> float:
    """``ANALYZE fg_dhcp``, then each of its partitions or chunks by name; the seconds it took."""
    began = time.perf_counter()
    with site.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("ANALYZE fg_dhcp"))
        for child in _children(connection):
            connection.execute(text(f"ANALYZE {child}"))
    return time.perf_counter() - began


def _statistics(site: Engine) -> str:
    """What the planner has on ``fg_dhcp``: the parent's inherited column statistics and row
    count, and how many partitions or chunks were ever analyzed (and when, the latest)."""
    with site.connect() as connection:
        inherited, reltuples = connection.execute(
            text(
                "SELECT (SELECT count(*) FROM pg_stats WHERE tablename = 'fg_dhcp' "
                "AND inherited), (SELECT reltuples FROM pg_class WHERE oid = 'fg_dhcp'::regclass)"
            )
        ).one()
        children, analyzed, latest = connection.execute(
            text(
                "SELECT count(*), count(coalesce(s.last_analyze, s.last_autoanalyze)), "
                "max(greatest(s.last_analyze, s.last_autoanalyze)) "
                "FROM pg_inherits AS i LEFT JOIN pg_stat_all_tables AS s ON s.relid = i.inhrelid "
                "WHERE i.inhparent = 'fg_dhcp'::regclass"
            )
        ).one()
    return (
        f"statistics: fg_dhcp's inherited pg_stats on {inherited} columns, reltuples "
        f"{reltuples:.0f}; {analyzed} of {children} partitions/chunks analyzed (latest {latest})"
    )


def _partitions(site: Engine) -> str:
    with site.connect() as connection:
        total = _count(connection, "fg_dhcp")
        chunks = _chunks(connection)
        if chunks is not None:  # a hypertable: no partitions, no fg_dhcp_default
            return f"fg_dhcp: {total} rows in {chunks} chunks"
        found = list_partitions(connection, "fg_dhcp")
        default = _count(connection, "fg_dhcp_default") if found else 0
    monthly = sum(1 for p in found if p.month is not None)
    daily = sum(1 for p in found if p.day is not None)
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


def _measured(
    site: Engine,
    capsys: pytest.CaptureFixture[str],
    *,
    name: str,
    scale: str,
    stage: str,
) -> list[str]:
    """One world's measurement at one stage, printed; its summary lines (the second: old → new)."""
    g = guards()
    factor = SCALES[scale]
    outcome, sql, parameters = _captured(site)
    assert len(outcome.matches) == 100
    for match in outcome.matches:
        chain = match.chain
        assert chain is not None
        assert chain.lease is not None
        assert chain.lease.source_table == "mt_dhcp"
        assert chain.confidence in ("high", "medium")
    assert sql.count(" UNION ALL ") == 1
    forms = _forms(sql)
    mac_branch = forms.branch
    with site.begin() as connection:
        connection.execute(text(CUSTOM_PLANS))
        rows = connection.exec_driver_sql(sql, parameters).all()
        branch_rows = connection.exec_driver_sql(mac_branch, parameters).all()
        old_rows = connection.exec_driver_sql(forms.old_sql, parameters).all()
        old_branch_rows = connection.exec_driver_sql(forms.old_branch, parameters).all()
        moves = connection.execute(
            text("SELECT count(*) FROM leases WHERE moves IS NOT NULL")
        ).scalar_one()
    assert moves == 100
    assert {n for n, *_ in rows} == set(range(1, 101))
    assert {
        (str(ip).startswith("172.20.60."), interface, held) for _n, ip, interface, _vd, held in rows
    } == {(True, "vlan60", False)}
    assert sorted(branch_rows) == sorted(rows)
    # The wrapper changes the plan, not the rows (W5-G-26).
    assert sorted(old_branch_rows) == sorted(branch_rows)
    assert sorted(old_rows) == sorted(rows)
    with site.connect() as connection:
        read = connection.execute(
            text(
                "SELECT count(*) FROM fg_dhcp WHERE mac IN (SELECT mac FROM leases "
                "WHERE source_table = 'mt_dhcp') AND time > :low AND time < :high"
            ),
            {"low": g._first_read(site, g.MT_AT), "high": g.MT_AT},
        ).scalar_one()

    lookups, checks, sent = _timed_lookups(site)
    branch = _timed_statement(site, BRANCH, mac_branch, parameters)
    whole = _timed_statement(site, STATEMENT, sql, parameters)
    branches = _alternated(site, BRANCH, forms.old_branch, mac_branch, parameters)
    statements = _alternated(site, STATEMENT, forms.old_sql, sql, parameters)
    explained = _explained_lookup(site, forms)
    assert explained.plan_cache_mode == "force_custom_plan"
    old_new = _old_new(explained, branches, statements)
    _show(
        capsys,
        [
            f"===== #185 {stage}: {DESCRIPTIONS[name]}; {factor} x the guards' FortiGate "
            f"DHCP rows ({scale} lines a day) =====",
            f"stage: {STAGES[stage]}",
            _server(site),
            _partitions(site),
            _statistics(site),
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
            f"whole statement: {_parallel(explained.statement)}",
            "--- old (before W5-G-26: the MAC branch's test bare) against new (as sent): old and "
            "new alternating on one connection, one transaction, both prepared before the "
            "first timed round ---",
            branches[0].line("old: MAC branch alone"),
            branches[1].line("new: MAC branch alone"),
            statements[0].line("old: statement alone"),
            statements[1].line("new: statement alone"),
            "old: MAC branch figures (same transaction as the new one's):",
            *_figures(explained.old_figures),
            f"old: whole statement: {_parallel(explained.old_statement)}",
            old_new,
            "--- statement (as sent; parameters bound) ---",
            sql,
            "--- EXPLAIN (ANALYZE, BUFFERS) of the statement, in a lookup's transaction "
            "right after it ran ---",
            *explained.plans,
            "--- EXPLAIN (ANALYZE, BUFFERS) of the MAC branch alone, same transaction ---",
            *explained.branch,
            "--- old: the MAC branch's test written back to the bare one (the IP branch "
            "unchanged) ---",
            forms.bare,
            "--- old: EXPLAIN (ANALYZE, BUFFERS) of the statement, same transaction ---",
            *explained.old_plans,
            "--- old: EXPLAIN (ANALYZE, BUFFERS) of the MAC branch alone, same transaction ---",
            *explained.old_branch,
            f"===== end of {name}, {scale}, {stage} =====",
        ],
    )
    median = statistics.median
    return [
        f"{name:11s} {stage:13s} {_summary(explained.figures)}; MAC branch alone best "
        f"{min(branch.seconds) * 1000:.1f} / median {median(branch.seconds) * 1000:.1f} ms; "
        f"statement as sent median {median(sent.seconds) * 1000:.1f} ms; whole lookup median "
        f"{median(lookups.seconds) * 1000:.1f} ms",
        f"{name:11s} {stage:13s} {old_new}",
    ]


@pytest.mark.perf
@pytest.mark.parametrize("scale", sorted(SCALES))
def test_the_missed_moves_mac_branch_is_measured(
    pg_server: TestServer,
    pg_template: str,
    capsys: pytest.CaptureFixture[str],
    scale: str,
) -> None:
    """#185 step 1: the plan and the times of the MAC branch on every layout of this server's
    kind, as loaded and after ``ANALYZE fg_dhcp``, printed; the lookup's answer is the guards'
    (100 ``mt_dhcp`` chains, none lowered), the check decoded the recorded moves, and the branch
    read every chain's ACKs on the staff Wi-Fi and nothing else; the form before W5-G-26 returned
    the same rows and is measured beside it."""
    summary: list[str] = []
    for layout in LAYOUTS:
        with _world(pg_server, pg_template, layout, SCALES[scale]) as (site, timescale):
            name = "timescaledb" if timescale else layout
            for stage in STAGES:
                if stage == ANALYZED:
                    seconds = _analyze_fg_dhcp(site)
                    summary.append(f"{name:11s} ANALYZE fg_dhcp and its children: {seconds:.1f} s")
                summary.extend(_measured(site, capsys, name=name, scale=scale, stage=stage))
            if timescale:
                break
    _show(
        capsys,
        [
            f"===== #185 summary, {scale}: as loaded against after ANALYZE fg_dhcp; old (bare "
            "test) → new (W5-G-26) =====",
            *summary,
        ],
    )
