"""Issue #185 (W5-G-26): the plan of ``Attributor._missed_moves``' MAC branch, pinned.

The branch tests each ``fg_dhcp`` row with :func:`.trust.kept` (``CASE ... END IS NULL``),
written ``CASE WHEN <kept> THEN true ELSE false END``: PostgreSQL has no statistics on either
expression and estimates ``IS NULL`` of one at 0.005 and a boolean one at 0.5. With the bare
test it estimated the chains' ACKs at about 26 for 5,699 and, on the guards' layout, took a
nested loop that filtered every ACK against each of the 100 windows (564,201 rows removed by its
join filter, 80-115 ms against 12 ms for the hash join on PostgreSQL 16 here, on this module's
world). The wrapper is a planner fact, not a rule of the language: a version that folds it back
to the bare test brings the misestimate back, and this module fails then.

**The world** is the recorded-moves guard's (``test_lookup_performance.py``) cut to what the
branch reads: the staff Wi-Fi's FortiGate ACKs (MACs ``:00``-``:ff``, one every 75 s) for
:data:`DHCP_DAYS` days, 100 ``mt_dhcp`` chains (MACs ``:00``-``:63``) with their open leases and
one session each at :data:`AT`, laid out as the guards lay it out (one ``ensure_partitions``
call: 16 daily partitions, the older days in ``fg_dhcp_default``, the window's first four days
among them). On TimescaleDB it is the hypertable's one-day chunks. A lookup of :data:`PUBLIC` at
:data:`AT` sends the statement; its MAC branch (the first ``UNION ALL`` part) is explained with
its parameters, planned with them as the API plans it.

**Its size**, the guards' 400 days, is the fewest measured with which the bare test reproduces
the default-partition misestimate's nested loop in every run: the planner takes it when its cost is
within the 1 % fuzz of the hash join's, that is when the bitmap scan of the window's first days
costs enough, and that cost grows with the pages of ``fg_dhcp_default`` (every day before the
partitions). On PostgreSQL 16 here (the bare test loaded from before the wrapper, each run with
its own ``ANALYZE`` sample) 400 days nested-looped in 50 runs of 50; below that the plan comes
and goes with the sample (370 and 380 days in 62 of 64 runs, 360 in 17 of 21, 330 to 350 in 14
of 19, 320 and fewer in none), and one ACK every 100 s over 400 days hashed. Built, it is about
15 s, so the module is ``perf``: it runs in ``test-perf`` on both CI runners (Linux with
TimescaleDB, and Windows), not in every other job.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, case, event, false, select, text, true
from sqlalchemy.orm import Session

from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import FgDhcp, Source
from arcilogger_db.partitions import ensure_partitions, hypertables
from arcilogger_db.settings import get_settings
from arcilogger_db.testing import TestServer, pause_compression_jobs
from arcilogger_worker.lookup import LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.lookup.days import reads_from
from arcilogger_worker.lookup.trust import kept, untrusted_sources
from arcilogger_worker.testing import tr

pytestmark = pytest.mark.perf

DAY = date(2026, 10, 12)
#: The lookup's moment: the 100 chains' sessions, 30 s of them from it on.
AT = tr(DAY, 14, 20)
#: The public address of the lookup (no exit configured: the double-NAT candidate search).
PUBLIC = "198.51.100.20"
WAN = "100.64.12.9"
#: Days of staff Wi-Fi ACKs before :data:`AT`, the guards': why not fewer, see the module's
#: docstring.
DHCP_DAYS = 400
DHCP_STEP_SECONDS = 75
#: What the API's lookup sets for its transaction (``search.sql.custom_plans_statement``).
CUSTOM_PLANS = "SELECT set_config('plan_cache_mode', 'force_custom_plan', true)"
#: The statement's text (``_missed_moves``' one with the two ``JOIN fg_dhcp`` branches).
MARKS = ("WITH ORDINALITY", "JOIN fg_dhcp")

SOURCES = (
    ("fortigate", "fortigate", "172.31.255.1"),
    ("core-r1", "mikrotik", "172.20.10.1"),
)
WORLD = (
    # The staff Wi-Fi's ACKs (vlan60), none of a chain's address.
    "INSERT INTO fg_dhcp (time, time_received, source, sender, extra, raw_day, raw_line, "
    "raw_offset, logid, subtype, dhcp_msg, interface, ip, mac, lease, device_time) "
    "SELECT t, t, 'fortigate', '172.31.255.1', '{\"vd\": \"root\"}'::jsonb, :day, g, g * 300, "
    "'0100026001', 'system', 'Ack', 'vlan60', ('172.20.60.' || (10 + g % 200))::inet, "
    "('00:00:5e:00:53:' || lpad(to_hex(g % 256), 2, '0'))::macaddr, 86400, true "
    "FROM generate_series(1, :days * 86400 / :step) AS g, "
    "LATERAL (SELECT CAST(:at AS timestamptz) - g * make_interval(secs => :step) AS t) AS x",
    # The 100 mt_dhcp chains (VLANs 41 and 43), assigned 150 days ago.
    "INSERT INTO mt_dhcp (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "server, vlan, action, ip, mac) "
    "SELECT CAST(:at AS timestamptz) - interval '150 days', "
    "CAST(:at AS timestamptz) - interval '150 days', 'core-r1', '172.20.10.1', :day, j, 0, "
    "'dhcp' || v, v, 'assigned', ('172.20.' || v || '.' || (100 + j % 50))::inet, "
    "('00:00:5e:00:53:' || lpad(to_hex(j), 2, '0'))::macaddr "
    "FROM generate_series(0, 99) AS j, LATERAL (SELECT CASE WHEN j < 50 THEN 41 ELSE 43 END "
    "AS v) AS x",
    # Their leases, open.
    "INSERT INTO leases (ip, mac, vlan, server, start, end_reason, confidence, source_table, "
    "last_evidence, evidence) "
    "SELECT ip, mac, v, 'dhcp' || v, s, 'open', 'high', 'mt_dhcp', "
    "CAST(:at AS timestamptz) - interval '1 hour', "
    "jsonb_build_array(jsonb_build_object('table', 'mt_dhcp', 'row_id', j + 1, 'time', s, "
    "'raw', jsonb_build_object('source', 'core-r1', 'day', :day, 'line', j, 'offset', 0), "
    "'summary', 'dhcp' || v || ' assigned ' || host(ip) || ' for ' || mac)) "
    "FROM generate_series(0, 99) AS j, LATERAL (SELECT CASE WHEN j < 50 THEN 41 ELSE 43 END "
    "AS v, CAST(:at AS timestamptz) - interval '150 days' AS s) AS x, "
    "LATERAL (SELECT ('172.20.' || v || '.' || (100 + j % 50))::inet AS ip, "
    "('00:00:5e:00:53:' || lpad(to_hex(j), 2, '0'))::macaddr AS mac) AS k",
    # A session of each chain at the moment.
    "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "logid, subtype, action, sessionid, proto, srcip, srcport, srcintf, dstip, dstport, dstintf, "
    "transip, transport, trandisp, duration, is_interim) "
    "SELECT t, t, 'fortigate', '172.31.255.1', :day, j, j::bigint * 900, '0000000013', "
    "'forward', 'accept', 2000000 + j, 6, ip, 40000 + j, "
    "'vlan' || split_part(host(ip), '.', 3), ('198.51.100.' || (j % 200 + 1))::inet, 443, "
    "'wan1', CAST(:wan AS inet), 50000 + j, 'snat', 5, false "
    "FROM generate_series(0, 99) AS j, LATERAL (SELECT CAST(:at AS timestamptz) + (j % 30) * "
    "interval '1 second' AS t, ('172.20.' || CASE WHEN j < 50 THEN 41 ELSE 43 END || '.' || "
    "(100 + j % 50))::inet AS ip) AS x",
)


@pytest.fixture(scope="module")
def site(pg_server: TestServer, pg_template: str) -> Iterator[Engine]:
    with pg_server.database(pg_template) as name:
        engine = engine_from_url(pg_server.dsn_for(name), pool_size=2, max_overflow=2)
        try:
            with engine.begin() as connection:
                ensure_partitions(connection, today=DAY)  # as the guards' _site
                timescale = bool(hypertables(connection))
            if timescale:
                pause_compression_jobs(engine)  # no chunk of the world is compressed
            with Session(engine) as session:
                for source, kind, address in SOURCES:
                    session.add(Source(name=source, type=kind, address=address))
                session.commit()
            params = {
                "at": AT,
                "day": DAY,
                "wan": WAN,
                "days": DHCP_DAYS,
                "step": DHCP_STEP_SECONDS,
            }
            with engine.begin() as connection:
                for statement in WORLD:
                    connection.execute(text(statement), params)
            with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text("VACUUM (ANALYZE)"))
            yield engine
        finally:
            engine.dispose()


def _statement(site: Engine) -> tuple[str, dict[str, Any]]:
    """``_missed_moves``' statement as a lookup of :data:`PUBLIC` at :data:`AT` sends it."""
    sent: list[tuple[str, dict[str, Any]]] = []

    def executed(*args: Any) -> None:
        statement, parameters = args[2], args[3]
        if all(mark in statement for mark in MARKS):
            sent.append((statement, dict(parameters)))

    event.listen(site, "before_cursor_execute", executed)
    try:
        with Session(site) as session:
            session.execute(text(CUSTOM_PLANS))
            settings = LookupSettings.from_settings(get_settings(session))
            outcome = run_lookup(
                session, LookupRequest(at=AT, public_ip=PUBLIC), settings, now=AT + timedelta(1)
            )
            session.rollback()
    finally:
        event.remove(site, "before_cursor_execute", executed)
    assert len(outcome.matches) == 100
    (found,) = sent
    return found


def _explained(site: Engine, sql: str, parameters: dict[str, Any]) -> dict[str, Any]:
    with site.begin() as connection:
        connection.execute(text(CUSTOM_PLANS))
        value = connection.exec_driver_sql(
            f"EXPLAIN (ANALYZE, FORMAT JSON) {sql}", parameters
        ).scalar_one()
    plan: dict[str, Any] = (json.loads(value) if isinstance(value, str) else value)[0]["Plan"]
    return plan


def _nodes(plan: dict[str, Any]) -> Iterator[dict[str, Any]]:
    yield plan
    for child in plan.get("Plans", ()):
        yield from _nodes(child)


def _reads_fg_dhcp(node: dict[str, Any]) -> bool:
    """Whether the node scans a table: the MAC branch's only one is ``fg_dhcp`` (a partition,
    ``fg_dhcp_default`` or, on TimescaleDB, a ``_hyper_*`` chunk; ``windows`` is a function)."""
    return any("Relation Name" in n for n in _nodes(node))


def _estimates(site: Engine) -> tuple[int, int, int]:
    """The planner's estimate of the window's ACKs (every one kept) with the bare ``kept``
    test and with the wrapped one, and how many there are."""
    with Session(site) as session:
        settings = LookupSettings.from_settings(get_settings(session))
        untrusted = untrusted_sources(session)
    low = reads_from(LookupRequest(at=AT, public_ip=PUBLIC), settings)
    test = kept(settings, untrusted)
    estimated: list[int] = []
    actual = 0
    for condition in (test, case((test, true()), else_=false())):
        query = select(FgDhcp.id).where(FgDhcp.time > low, FgDhcp.time < AT, condition)
        compiled = query.compile(site, compile_kwargs={"render_postcompile": True})
        plan = _explained(site, str(compiled), dict(compiled.params))
        estimated.append(int(plan["Plan Rows"]))
        actual = int(plan["Actual Rows"])
    bare, wrapped = estimated
    return bare, wrapped, actual


def test_the_mac_branch_filters_no_ack_per_window(site: Engine) -> None:
    """W5-G-26: the MAC branch is no Nested Loop whose join filter removes rows, which tests
    every ACK against every window (the bare test's plan: 564,201 removed), on every layout
    built (TimescaleDB included); which join it takes instead (a Hash Join on the MAC today, a
    Merge Join or another one later) is the planner's. Every ACK of a chain's MAC in the
    lookup's days is in its window (for another address), so a join that matches them on the
    MAC removes none.

    First the reason, on this server: the window's ACKs are estimated at about 0.5 % of them
    with the bare ``kept`` test and at about half with the wrapped one. If the bare side fails,
    the misestimate is gone and the wrapper may go (W5-G-26); if the wrapped side fails, the
    planner folds the wrapper and the branch is planned on the misestimate again."""
    bare, wrapped, actual = _estimates(site)
    assert actual > 10_000
    assert bare < actual / 10, ("bare kept test", bare, actual)
    assert wrapped > actual / 10, ("wrapped kept test", wrapped, actual)

    sql, parameters = _statement(site)
    assert sql.count(" UNION ALL ") == 1
    plan = _explained(site, sql.split(" UNION ALL ")[0], parameters)
    loops = [
        (node["Node Type"], node.get("Rows Removed by Join Filter", 0))
        for node in _nodes(plan)
        if node["Node Type"] == "Nested Loop"
    ]
    assert not any(removed > 0 for _loop, removed in loops), f"{loops}\n{json.dumps(plan)}"
    joins = [node for node in _nodes(plan) if "Join Type" in node and _reads_fg_dhcp(node)]
    assert joins, json.dumps(plan, indent=1)
    outer = joins[0]
    # 5,699 ACKs of the chains' MACs (a parallel plan's figures are a worker's: times loops).
    assert outer["Actual Rows"] * outer["Actual Loops"] > 5_000, outer
