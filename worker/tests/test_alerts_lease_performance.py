"""Cost of the ADR-0016 alert checks at 1 M FortiGate lines a day (contract 3.8).

A site day is written straight into the tables (``INSERT … SELECT generate_series``): 1 M
FortiGate sessions and 1 M messages in the 24 hours before the check, 30 days of FortiGate DHCP
rows of 400 clients, their leases, a quiet MikroTik feed, the lease runs of 7 days and an exit.
Then ``R20-lease`` and ``R20-nat`` run as ``alerts.system`` runs them, twice, and every statement
of the second run is timed: each under :data:`LIMIT_SECONDS`, the ``source-clock`` sample
(``messages``) under :data:`CLOCK_SECONDS`, the unclaimed NAT address count (``transip``) under
:data:`TRANSIP_SECONDS`. ``ARCILOGGER_ALERT_PERF_ROWS`` sets the daily rows (``0`` skips); run with
``-s`` to see the slowest statements.
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, Source
from arcilogger_db.settings import set_setting
from arcilogger_worker.alerts import system
from arcilogger_worker.alerts.system import run_system_checks
from arcilogger_worker.testing import tr

ROWS = int(os.environ.get("ARCILOGGER_ALERT_PERF_ROWS", "1000000"))
pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(ROWS == 0, reason="ARCILOGGER_ALERT_PERF_ROWS=0"),
]
DAY = date(2026, 10, 20)
NOW = tr(DAY, 12)
CLIENTS = 400
LIMIT_SECONDS = 1.0
CLOCK_SECONDS = 0.5
TRANSIP_SECONDS = 0.2

#: Every query takes its values as bound parameters; the row counts and spans are constants.
SITE = (
    # 1 M sessions of 400 clients in the day, NAT to the exit's address but 1 in 50.
    """
    INSERT INTO fg_traffic (time, time_received, source, sender, extra, raw_day, raw_line,
                            raw_offset, srcip, srcport, srcintf, srcmac, dstip, dstport, proto,
                            action, transip, transport, trandisp, duration, is_interim)
    SELECT t, t, 'fortigate', '192.0.2.1', '{}'::jsonb, :day, g, g * 400,
           '172.20.42.0'::inet + (g % :clients), 40000 + g % 20000, 'vlan42',
           ('00:00:5e:00:53:' || lpad(to_hex(g % 200), 2, '0'))::macaddr,
           '198.51.100.0'::inet + (g % 250), 443, 6, 'accept',
           CASE WHEN g % 50 = 0 THEN '203.0.113.99'::inet ELSE '203.0.113.10'::inet END,
           30000 + g % 30000, 'snat', g % 600, false
    FROM generate_series(1, :rows) AS g,
         LATERAL (SELECT :now - g * (interval '1 day' / :rows)) AS s(t)
    """,
    # The same lines as messages, the device clock 1 s behind the receive time.
    """
    INSERT INTO messages (time, time_received, source, sender, extra, raw_day, raw_line,
                          raw_offset, type, text)
    SELECT t - interval '1 second', t, 'fortigate', '192.0.2.1', '{}'::jsonb, :day, g, g * 400,
           'traffic', ''
    FROM generate_series(1, :rows) AS g,
         LATERAL (SELECT :now - g * (interval '1 day' / :rows)) AS s(t)
    """,
    # 30 days of FortiGate DHCP: two ACKs a day per client, a refused logid now and then.
    """
    INSERT INTO fg_dhcp (time, time_received, source, sender, extra, raw_day, raw_line,
                         raw_offset, logid, subtype, dhcp_msg, interface, ip, mac, hostname,
                         lease, device_time)
    SELECT t, t, 'fortigate', '192.0.2.1', '{"vd": "root"}'::jsonb, :day, g, g * 300,
           CASE WHEN g % 97 = 0 THEN '0100026099' ELSE '0100026001' END, 'system', 'Ack',
           'vlan42', '172.20.42.0'::inet + (g % :clients),
           ('00:00:5e:00:5' || (g % :clients / 256) || ':' || lpad(to_hex(g % :clients % 256), 2,
            '0'))::macaddr,
           NULL, 86400, true
    FROM generate_series(1, :clients * 60) AS g,
         LATERAL (SELECT :now - g * (interval '30 days' / (:clients * 60))) AS s(t)
    """,
    # System events of the day (the unconverted query reads them).
    """
    INSERT INTO fg_event (time, time_received, source, sender, extra, raw_day, raw_line,
                          raw_offset, logid, subtype)
    SELECT t, t, 'fortigate', '192.0.2.1', '{"ip": "x", "mac": "y", "dhcp_msg": "Ack"}'::jsonb,
           :day, g, g * 200, CASE WHEN g % 2 = 0 THEN '0100026001' ELSE '0100032001' END,
           'system'
    FROM generate_series(1, :rows / 20) AS g,
         LATERAL (SELECT :now - g * (interval '1 day' / (:rows / 20))) AS s(t)
    """,
    # A MikroTik feed that stopped ten days ago (none:mt_arp, none:mt_dhcp look back for it).
    """
    INSERT INTO mt_arp (time, time_received, source, sender, extra, raw_day, raw_line,
                        raw_offset, ip, mac, interface)
    SELECT t, t, 'core-r1', '192.0.2.2', '{}'::jsonb, :day, g, g * 100,
           '172.20.41.0'::inet + (g % 200), '00:00:5e:00:53:20', 'vlan41'
    FROM generate_series(1, 20000) AS g,
         LATERAL (SELECT :now - interval '10 days' - g * interval '1 minute') AS s(t)
    """,
    """
    INSERT INTO mt_dhcp (time, time_received, source, sender, extra, raw_day, raw_line,
                         raw_offset, server, action, ip, mac)
    SELECT t, t, 'core-r1', '192.0.2.2', '{}'::jsonb, :day, g, g * 100, 'dhcp41',
           CASE WHEN g % 3 = 0 THEN 'deassigned' ELSE 'assigned' END,
           '172.20.41.0'::inet + (g % 200), '00:00:5e:00:53:21'
    FROM generate_series(1, 5000) AS g,
         LATERAL (SELECT :now - interval '10 days' - g * interval '5 minutes') AS s(t)
    """,
    # The clients' leases: one open each, and 29 days of ended ones (timeout, a day long).
    """
    INSERT INTO leases (ip, mac, vlan, server, hostname, start, "end", end_reason, confidence,
                        source_table, last_evidence, lease_seconds, evidence)
    SELECT '172.20.42.0'::inet + c, ('00:00:5e:00:5' || (c / 256) || ':' ||
           lpad(to_hex(c % 256), 2, '0'))::macaddr, 42, 'vlan42', NULL, s,
           CASE WHEN d = 0 THEN NULL ELSE s + interval '1 day' END,
           CASE WHEN d = 0 THEN 'open' ELSE 'timeout' END, 'high', 'fg_dhcp', s, 86400,
           jsonb_build_array(jsonb_build_object(
               'table', 'fg_dhcp', 'row_id', c + 1, 'time', s,
               'raw', jsonb_build_object('source', 'fortigate', 'day', :day, 'line', c + 1,
                                         'offset', 0),
               'summary', 'vlan42 assigned ' || host('172.20.42.0'::inet + c) || ' for x'))
    FROM generate_series(0, :clients - 1) AS c, generate_series(0, 29) AS d,
         LATERAL (SELECT :now - interval '2 hours' - d * interval '1 day') AS w(s)
    """,
    # The lease runs of 7 days (no renewal ACK seen) and their search summary.
    """
    INSERT INTO job_runs (job, started_at, finished_at, status, details)
    SELECT 'leases.incremental', t, t, 'ok', '{"stats": {"fg_renewal_ack": 0}}'::jsonb
    FROM generate_series(1, 7 * 24 * 12) AS g,
         LATERAL (SELECT :now - g * interval '5 minutes') AS s(t)
    """,
    """
    INSERT INTO search_counts (hour, source, raw_day, kind, tbl, n)
    SELECT :now - g * interval '1 hour', 'fortigate', :day, 'traffic', 'fg_traffic', :rows / 24
    FROM generate_series(1, 24) AS g
    """,
)


@pytest.fixture
def site(db_engine: Engine, db_session: Session) -> None:
    for name, kind, address in (("fortigate", "fortigate", "192.0.2.1"),
                                ("core-r1", "mikrotik", "192.0.2.2")):  # fmt: skip
        if db_session.get(Source, name) is None:
            db_session.add(Source(name=name, type=kind, address=address))
    exits = {"hat-1": {"mode": "direct", "public": ["203.0.113.10"]}}
    set_setting(db_session, "nat.egresses", exits, updated_by="tolga")
    set_setting(db_session, "lookup.srcmac_interfaces", ["vlan42"], updated_by="tolga")
    db_session.commit()
    params = {"now": NOW, "day": DAY, "rows": ROWS, "clients": CLIENTS}
    started = time.perf_counter()
    with db_engine.begin() as connection:
        for statement in SITE:
            connection.execute(text(statement), params)
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("ANALYZE"))
    print(f"\nsite written in {time.perf_counter() - started:.1f} s")


@pytest.fixture
def timings(db_engine: Engine) -> Iterator[list[tuple[float, str]]]:
    found: list[tuple[float, str]] = []

    def before(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        conn.info.setdefault("started", []).append(time.perf_counter())

    def after(conn: Any, cursor: Any, statement: str, *args: Any) -> None:
        found.append((time.perf_counter() - conn.info["started"].pop(), statement))

    event.listen(db_engine, "before_cursor_execute", before)
    event.listen(db_engine, "after_cursor_execute", after)
    yield found
    event.remove(db_engine, "before_cursor_execute", before)
    event.remove(db_engine, "after_cursor_execute", after)


def _limit(statement: str) -> float:
    if "FROM messages" in statement:
        return CLOCK_SECONDS
    if "fg_traffic.transip" in statement and "GROUP BY" in statement:
        return TRANSIP_SECONDS
    return LIMIT_SECONDS


@pytest.mark.usefixtures("site")
def test_the_lease_and_nat_checks_at_a_million_lines_a_day(
    db_engine: Engine,
    layout: Layout,
    monkeypatch: pytest.MonkeyPatch,
    timings: list[tuple[float, str]],
) -> None:
    checks = tuple(
        (name, check) for name, check in system.CHECKS if name in ("lease_evidence", "nat_exits")
    )
    monkeypatch.setattr(system, "CHECKS", checks)
    sessions: sessionmaker[Session] = session_factory(db_engine)
    config = Config(data_dir=layout.root, secrets_dir=layout.root)
    report = run_system_checks(sessions, layout, config, now=NOW)  # warm the caches
    assert not report.errors
    timings.clear()
    started = time.perf_counter()
    report = run_system_checks(sessions, layout, config, now=NOW + timedelta(minutes=5))
    total = time.perf_counter() - started
    assert not report.errors
    slowest = sorted(timings, reverse=True)
    print(f"{len(timings)} statements in {total:.2f} s; slowest:")
    for seconds, statement in slowest[:8]:
        print(f"  {seconds * 1000:7.1f} ms  {' '.join(statement.split())[:150]}")
    slow = [
        (round(seconds, 3), " ".join(statement.split())[:200])
        for seconds, statement in timings
        if seconds > _limit(statement)
    ]
    assert slow == []
    # The site's causes were found (so the queries ran on the data, not on an early return).
    with sessions() as session:
        found = set(session.scalars(select(Alert.dedup_key).where(Alert.state == "open")))
    assert {
        "R20-lease:fortigate-refused",
        "R20-lease:none:mt_arp",
        "R20-lease:fortigate-unverified",
        "R20-nat:unmapped-transip",
    } <= found
