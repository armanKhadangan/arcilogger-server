"""Performance guards of the Wave 3 endpoints (contract 3.8, ``poe test-perf``).

* ``GET /settings/fortigate/observed``: its counts over 24 hours of ``fg_traffic`` at 1 M rows
  a day (after a day of older rows in the month before) within 2 s, its deadline 5 s;
  the NAT query scans no partition (or TimescaleDB chunk) that holds only rows outside the
  window.
* ``GET /sources`` ``lease_evidence``: the usable-row counts of the DHCP and ARP tables through
  ``trust.kept`` within 0.5 s with 30 days of DHCP and ARP rows and 30 days of leases, and the
  srcmac count of three interfaces within 1 s at 1 M ``fg_traffic`` rows a day.

The rows are written with ``INSERT … SELECT generate_series`` (fictional addresses: 172.20.x
clients, the MACs ``00:00:5e:00:53:xx``, RFC 5737 destinations); the timings are the best of
three and printed with the plans.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from arcilogger_api import sourcehealth
from arcilogger_api.routers.settings import observed_counts, observed_nat_query
from arcilogger_core.timeutil import site_day
from arcilogger_db.engine import session_factory
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.settings import set_setting
from arcilogger_db.testing import pause_compression_jobs

#: Early in a month, so the older rows lie in the partition of the month before.
NOW = datetime(2026, 10, 2, 12, 0, 0, tzinfo=UTC)
OBSERVED_BUDGET_SECONDS = 2.0
LEASE_EVIDENCE_BUDGET_SECONDS = 0.5
SRCMAC_BUDGET_SECONDS = 1.0
ROWS_A_DAY = 1_000_000
CLIENTS = 3000
#: The load generator's FortiGate DHCP rate (one ACK every 75 s), as the worker's guards use it.
DHCP_STEP_SECONDS = 75
SRCMAC_INTERFACES = ("vlan41", "vlan42", "vlan43")

#: Sessions of ``:rows`` rows from ``:start`` every ``:step``; client ``c`` = g % CLIENTS has
#: the address ``172.20.<41 + c % 3>.<c / 3 % 250>`` on ``vlan<41 + c % 3>`` and (70 % of the
#: rows) its MAC; four NAT addresses on three interfaces, two ``trandisp`` values.
TRAFFIC = text(
    "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "logid, subtype, action, sessionid, proto, srcip, srcport, srcintf, srcmac, dstip, dstport, "
    "dstintf, transip, transport, trandisp, duration, is_interim) "
    "SELECT t, t, 'fortigate', '172.31.255.1', :day, g, g::bigint * 900, '0000000013', "
    "'forward', 'accept', g, 6, k.ip, 1024 + (g % 60000), k.interface, "
    "CASE WHEN g % 10 < 7 THEN k.mac END, ('203.0.113.' || (g % 250 + 1))::inet, 443, "
    "n.dstintf, n.transip, 1024 + (g % 60000), "
    "CASE WHEN g % 21 = 0 THEN 'snat-pool' ELSE 'snat' END, 10, false "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT g % :clients AS c, "
    "CAST(:start AS timestamptz) + g * CAST(:step AS interval) AS t) AS x, "
    "LATERAL (SELECT ('172.20.' || (41 + c % 3) || '.' || (c / 3 % 250))::inet AS ip, "
    "('00:00:5e:00:53:' || lpad(to_hex(c % 256), 2, '0'))::macaddr AS mac, "
    "'vlan' || (41 + c % 3) AS interface) AS k, "
    "LATERAL (SELECT (ARRAY['100.64.12.9', '100.64.13.9', '100.64.14.9', '198.51.100.7'])"
    "[1 + g % 4]::inet AS transip, (ARRAY['wan1', 'wan2', 'wan3', 'wan1'])[1 + g % 4] AS dstintf"
    ") AS n"
)

#: 30 days of DHCP and ARP rows and leases: FortiGate ACKs at the load generator's rate (every
#: 50th from an unconfigured sender, every 40th with an unmapped logid), MikroTik DHCP every
#: 75 s (a third of them ``offering``), ARP-LOG lines of 500 clients an hour, one lease a day
#: per client and table.
LEASE_SITE = (
    "INSERT INTO fg_dhcp (time, time_received, source, sender, extra, raw_day, raw_line, "
    "raw_offset, logid, subtype, dhcp_msg, interface, ip, mac, lease, device_time) "
    "SELECT t, t, CASE WHEN g % 50 = 0 THEN 'unknown-192.0.2.7' ELSE 'fortigate' END, "
    "'172.31.255.1', '{\"vd\": \"root\"}'::jsonb, :day, g, g * 300, "
    "CASE WHEN g % 40 = 0 THEN '0100026099' ELSE '0100026001' END, 'system', 'Ack', "
    "'vlan' || (41 + c % 3), ('172.20.' || (41 + c % 3) || '.' || (c / 3 % 250))::inet, "
    "('00:00:5e:00:53:' || lpad(to_hex(c % 256), 2, '0'))::macaddr, 86400, true "
    "FROM generate_series(1, 30 * 86400 / :dhcp_step) AS g, "
    "LATERAL (SELECT g % :clients AS c, "
    "CAST(:now AS timestamptz) - g * make_interval(secs => :dhcp_step) AS t) AS x",
    "INSERT INTO mt_dhcp (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "server, vlan, action, ip, mac) "
    "SELECT t, t, 'core-r1', '172.20.10.1', :day, g, 0, 'dhcp43', 43, "
    "(ARRAY['assigned', 'deassigned', 'offering'])[1 + g % 3], "
    "('172.20.43.' || (g % 250))::inet, ('00:00:5e:00:53:' || lpad(to_hex(g % 256), 2, '0'))"
    "::macaddr FROM generate_series(1, 30 * 86400 / :dhcp_step) AS g, "
    "LATERAL (SELECT CAST(:now AS timestamptz) - g * make_interval(secs => :dhcp_step) AS t) AS x",
    "INSERT INTO mt_arp (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "ip, mac, interface) "
    "SELECT t, t, 'core-r1', '172.20.10.1', :day, h * 500 + c, 0, "
    "('172.20.44.' || (c % 250))::inet, ('00:00:5e:00:53:' || lpad(to_hex(c % 256), 2, '0'))"
    "::macaddr, 'vlan44' "
    "FROM generate_series(1, 30 * 24) AS h, generate_series(0, 499) AS c, "
    "LATERAL (SELECT CAST(:now AS timestamptz) - h * interval '1 hour' "
    "+ c * interval '1 second' AS t) AS x",
    'INSERT INTO leases (ip, mac, vlan, start, "end", end_reason, confidence, source_table) '
    "SELECT ('172.20.' || (41 + c % 3) || '.' || (c / 3 % 250))::inet, "
    "('00:00:5e:00:53:' || lpad(to_hex(c % 256), 2, '0'))::macaddr, 41 + c % 3, s, "
    "CASE WHEN d = 0 THEN NULL ELSE s + interval '1 day' END, "
    "CASE WHEN d = 0 THEN 'open' ELSE 'timeout' END, 'high', tbl "
    "FROM generate_series(0, 999) AS c, generate_series(0, 29) AS d, "
    "unnest(ARRAY['mt_dhcp', 'fg_dhcp', 'mt_arp']) AS tbl, "
    "LATERAL (SELECT CAST(:now AS timestamptz) - (d + 1) * interval '1 day' AS s) AS x",
    "INSERT INTO sources (name, type, address, unknown) "
    "VALUES ('unknown-192.0.2.7', 'fortigate', '192.0.2.7', true)",
    "INSERT INTO alerts (rule, severity, state, title, dedup_key) "
    "VALUES ('R20-lease', 'medium', 'open', 'Kira kanıtı', 'R20-lease:none:mt_dhcp')",
)


def _prepare(engine: Engine) -> None:
    with engine.begin() as connection:
        ensure_partitions(connection, today=site_day(NOW))
        timescale = connection.scalar(text("SELECT value FROM settings WHERE key = 'db.timescale'"))
    if (timescale or {}).get("compression"):
        # NOW is a fixed past day: a compression policy must not compress the rows under the
        # guard (as the worker's guards do).
        pause_compression_jobs(engine)


def _traffic(engine: Engine, start: datetime, hours: float) -> None:
    rows = int(ROWS_A_DAY * hours / 24)
    with engine.begin() as connection:
        connection.execute(
            TRAFFIC,
            {
                "day": site_day(start),
                "rows": rows,
                "start": start,
                "step": f"{86_400_000 / ROWS_A_DAY} milliseconds",
                "clients": CLIENTS,
            },
        )


def _analyze(engine: Engine, *tables: str) -> None:
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for table in tables:
            connection.execute(text(f"VACUUM ANALYZE {table}"))


def _best_of_three[T](engine: Engine, run: Callable[[Session], T]) -> tuple[float, T]:
    timings, result = [], None
    for _ in range(3):
        with session_factory(engine)() as session:
            began = time.perf_counter()
            result = run(session)
            timings.append(time.perf_counter() - began)
    assert result is not None
    return min(timings), result


def _plan(engine: Engine, statement: Any) -> list[dict[str, Any]]:
    """The scan nodes of ``EXPLAIN (FORMAT JSON)`` of ``statement``."""
    with engine.connect() as connection:
        compiled = statement.compile(connection, compile_kwargs={"render_postcompile": True})
        raw = connection.exec_driver_sql(
            f"EXPLAIN (FORMAT JSON) {compiled}", compiled.params
        ).scalar_one()
    plan = raw if isinstance(raw, list) else json.loads(raw)
    nodes: list[dict[str, Any]] = []

    def walk(node: dict[str, Any]) -> None:
        if "Relation Name" in node:
            nodes.append(node)
        for child in node.get("Plans", []):
            walk(child)

    walk(plan[0]["Plan"])
    return nodes


def _outside_only(engine: Engine, low: datetime, high: datetime) -> set[str]:
    """The partitions or chunks of ``fg_traffic`` that hold rows outside ``[low, high]`` and
    none inside it."""
    query = (
        "SELECT DISTINCT c.relname FROM (SELECT DISTINCT tableoid FROM fg_traffic WHERE {}) AS t "
        "JOIN pg_class AS c ON c.oid = t.tableoid"
    )
    with engine.connect() as connection:
        outside = set(
            connection.execute(
                text(query.format("time < :low OR time > :high")), {"low": low, "high": high}
            ).scalars()
        )
        inside = set(
            connection.execute(
                text(query.format("time >= :low AND time <= :high")), {"low": low, "high": high}
            ).scalars()
        )
    return outside - inside


@pytest.mark.perf
def test_the_observed_summary_of_a_day_is_fast(db_engine: Engine) -> None:
    """A day of older rows in the month before, then the 24 hours of the window at
    1 M rows a day (4 NAT addresses on 3 interfaces, 2 ``trandisp`` values, srcmac on 70 % of
    the rows on 3 interfaces)."""
    _prepare(db_engine)
    low = NOW - timedelta(hours=24)
    _traffic(db_engine, NOW - timedelta(days=3), 24)  # 29 to 30 September
    _traffic(db_engine, low - timedelta(hours=1), 25)
    _analyze(db_engine, "fg_traffic")

    seconds, counts = _best_of_three(db_engine, lambda s: observed_counts(s, 24, NOW))
    nodes = _plan(db_engine, observed_nat_query(24, NOW, ()))
    scanned = {node["Relation Name"] for node in nodes}
    print(
        f"\nobserved endpoint, 24 h of 1 M rows a day: {seconds * 1000:.1f} ms\n"
        + "\n".join(f"{node['Node Type']} on {node['Relation Name']}" for node in nodes)
    )
    assert sum(group["sessions"] for group in counts["nat"]) > 990_000
    assert len(counts["nat"]) == 4 * 2
    assert {item["srcintf"] for item in counts["srcmac"]} == set(SRCMAC_INTERFACES)
    assert all(item["max_ips_per_mac"] >= 1 for item in counts["srcmac"])
    pruned = _outside_only(db_engine, low, NOW)
    assert pruned, "the load put no rows outside the window"
    assert not scanned & pruned, (scanned, pruned)
    assert seconds < OBSERVED_BUDGET_SECONDS


@pytest.mark.perf
def test_lease_evidence_is_fast(db_engine: Engine) -> None:
    """30 days of DHCP and ARP rows and leases; 1 M ``fg_traffic`` rows a day with srcmac, a day
    of them in the month before (pruned by the srcmac count)."""
    _prepare(db_engine)
    params = {
        "now": NOW,
        "day": site_day(NOW),
        "dhcp_step": DHCP_STEP_SECONDS,
        "clients": CLIENTS,
    }
    with db_engine.begin() as connection:
        for statement in LEASE_SITE:
            connection.execute(text(statement), params)
    _traffic(db_engine, NOW - timedelta(days=3), 24)  # 29 to 30 September
    _traffic(db_engine, NOW - timedelta(hours=26), 26)
    _analyze(db_engine, "fg_dhcp", "mt_dhcp", "mt_arp", "leases", "fg_traffic", "alerts")

    seconds, card = _best_of_three(db_engine, lambda s: sourcehealth.lease_evidence(s, NOW))
    print(f"\nlease_evidence without the srcmac count: {seconds * 1000:.1f} ms")
    assert card["srcmac"] == {"interfaces": [], "rows_last_hour": None}
    fortigate = card["fg_dhcp"]
    assert fortigate["rows"] > 1000
    assert fortigate["refused_rows"] > 0
    assert (card["state"], card["none_tables"]) == ("refused", ["mt_dhcp"])
    assert card["mt_dhcp"]["rows"] > 0
    assert card["mt_arp"]["rows"] == 24 * 500
    assert [card[t]["leases_current"] for t in ("mt_dhcp", "fg_dhcp", "mt_arp")] == [2000] * 3
    assert seconds < LEASE_EVIDENCE_BUDGET_SECONDS

    untrusted = frozenset({"unknown-192.0.2.7"})
    count_seconds, count = _best_of_three(
        db_engine,
        lambda s: sourcehealth.srcmac_rows_last_hour(s, SRCMAC_INTERFACES, untrusted, NOW),
    )
    nodes = _plan(db_engine, sourcehealth.srcmac_query(SRCMAC_INTERFACES, untrusted, NOW))
    print(
        f"srcmac rows of the last hour, 3 interfaces: {count_seconds * 1000:.1f} ms\n"
        + "\n".join(f"{node['Node Type']} on {node['Relation Name']}" for node in nodes)
    )
    assert count is not None
    assert 0.69 * ROWS_A_DAY / 24 < count < 0.71 * ROWS_A_DAY / 24
    assert (pruned := _outside_only(db_engine, NOW - sourcehealth.LAST_HOUR, NOW)), (
        "the load put no rows outside the hour"
    )
    assert not {node["Relation Name"] for node in nodes} & pruned, (nodes, pruned)
    assert count_seconds < SRCMAC_BUDGET_SECONDS

    # With the interfaces configured the card carries the same count.
    with session_factory(db_engine)() as session:
        set_setting(session, "lookup.srcmac_interfaces", list(SRCMAC_INTERFACES), updated_by="t")
        session.commit()
        card = sourcehealth.lease_evidence(session, NOW)
    assert card["srcmac"] == {"interfaces": list(SRCMAC_INTERFACES), "rows_last_hour": count}
