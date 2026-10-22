"""Spec 14: a 5651 lookup over a ±60 s window must take less than 5 s.

Continuous synthetic traffic (300 clients, sessions of 0-96 s plus one 2-hour session in 500)
that does **not** stop at midnight, so the scan tail of ``lookup.max_session_seconds`` (24 h
after the window) is full; web filter logs (a fifth of them for ``*.google.com``) and DNS
answers for the domain path; DHCP + hourly ARP for the 300 devices. Measured: the lease rebuild
of the first day (with look-ahead into the next), the double-NAT candidate search without
destination (every session that overlaps the window), with a destination IP + port, with a
popular and a rare domain, and an exact lookup. ``EXPLAIN`` shows that every query uses an index
and the bounded time range.

``ARCILOGGER_PERF`` selects the size:

* unset / ``1`` (default, about 30 s on a developer machine, most of it loading the rows):
  1,000,000 ``fg_traffic`` rows a day for 1.1 days, 275,000 web filter rows;
* ``large``: 3,000,000 rows a day for two days (6 M rows), 1,500,000 web filter rows — the
  spec's busy-day volume; takes a few minutes, run it before releases;
* ``0``: skipped.

Timings and plans are printed (run with ``-s`` to see them).
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import UTC, date, timedelta

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import day_window
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.settings import get_settings
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.lookup.sessions import Window, explain_rows
from arcilogger_worker.testing import tr

MODE = os.environ.get("ARCILOGGER_PERF", "1")
pytestmark = pytest.mark.skipif(
    MODE == "0", reason="performance test switched off (ARCILOGGER_PERF=0)"
)

DAY = date(2026, 10, 12)
WAN = "100.64.12.9"
LIMIT_SECONDS = 5.0


@dataclass(frozen=True)
class Size:
    rows_per_day: int
    days: float
    #: Requested moment (hours after DAY 00:00); the scan reaches 24 h further, inside the data.
    at_hour: int

    @property
    def rows(self) -> int:
        return int(self.rows_per_day * self.days)

    @property
    def step_ms(self) -> float:
        return 86_400_000 / self.rows_per_day


SIZE = Size(3_000_000, 2.0, 14) if MODE == "large" else Size(1_000_000, 1.1, 2)


def _load(engine: Engine) -> dict[str, float]:
    start, _ = (t.astimezone(UTC) for t in day_window(DAY))
    timings: dict[str, float] = {}
    with engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
    params = {
        "day": DAY,
        "rows": SIZE.rows,
        "wan": WAN,
        "start": start,
        "step": f"{SIZE.step_ms} milliseconds",
    }
    began = time.perf_counter()
    with engine.begin() as connection:
        # 300 clients: the three department VLANs x 100 addresses; one row every step over the
        # whole span.
        connection.execute(
            text(
                "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, logid, subtype, action, sessionid, proto, srcip, srcport, dstip, "
                "dstport, transip, transport, trandisp, duration, is_interim) "
                "SELECT t, t, 'fortigate', '172.31.255.1', :day, g, g::bigint * 900, "
                "'0000000013', 'forward', 'accept', 1000000 + g, 6, "
                "('172.20.' || (ARRAY[41, 42, 43])[1 + g % 3] || '.' || "
                "(100 + (g / 3) % 100))::inet, "
                "1024 + (g % 60000), ('23.' || (g % 200) || '.' || (g % 250) || '.1')::inet, "
                "CASE WHEN g % 10 = 0 THEN 80 ELSE 443 END, CAST(:wan AS inet), "
                "1024 + (g % 60000), 'snat', CASE WHEN g % 500 = 0 THEN 7200 ELSE g % 97 END, "
                "false FROM generate_series(1, :rows) AS g, "
                "LATERAL (SELECT CAST(:start AS timestamptz) + g * CAST(:step AS interval) "
                "AS t) AS x"
            ),
            params,
        )
        timings["load_traffic"] = time.perf_counter() - began
        # Web filter logs of every fourth session, written during it (one second before its
        # log); every fifth names *.google.com, the rest one of 5,000 rare sites.
        connection.execute(
            text(
                "INSERT INTO fg_web (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, sessionid, srcip, dstip, hostname, action) "
                "SELECT t, t, 'fortigate', '172.31.255.1', :day, k, 0, 1000000 + k, "
                "('172.20.' || (ARRAY[41, 42, 43])[1 + k % 3] || '.' || "
                "(100 + (k / 3) % 100))::inet, "
                "('23.' || (k % 200) || '.' || (k % 250) || '.1')::inet, "
                "CASE WHEN g % 5 = 0 THEN (CASE g % 3 WHEN 0 THEN 'www' WHEN 1 THEN 'mail' "
                "ELSE 'apis' END) || '.google.com' ELSE 'site' || (g % 5000) || '.example' END, "
                "'passthrough' FROM generate_series(1, :rows / 4) AS g, "
                "LATERAL (SELECT g * 4 AS k) AS kk, "
                "LATERAL (SELECT CAST(:start AS timestamptz) + k * CAST(:step AS interval) "
                "- interval '1 second' AS t) AS x"
            ),
            params,
        )
        # DNS answers for www.google.com every 30 s per VLAN (resolvers cache them).
        connection.execute(
            text(
                "INSERT INTO fg_dns (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, srcip, qname, answers) "
                "SELECT t, t, 'fortigate', '172.31.255.1', :day, g, 0, "
                "('172.20.' || (ARRAY[41, 42, 43])[1 + g % 3] || '.' || "
                "(100 + (g / 3) % 100))::inet, "
                "'www.google.com', ARRAY[('23.' || (g % 20) || '.' || (g % 25) || '.1')::inet] "
                "FROM generate_series(1, CAST(:days * 2880 AS integer)) AS g, "
                "LATERAL (SELECT CAST(:start AS timestamptz) + g * interval '30 seconds' "
                "AS t) AS x"
            ),
            {**params, "days": SIZE.days},
        )
        # DHCP at 00:00-00:25 and hourly ARP-LOG for every client over the whole span.
        connection.execute(
            text(
                "INSERT INTO mt_dhcp (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, server, vlan, action, ip, mac, hostname) "
                "SELECT t, t, 'core-r1', '172.20.10.1', :day, d, d * 100, "
                "'dhcp' || (ARRAY[41, 42, 43])[1 + d % 3], (ARRAY[41, 42, 43])[1 + d % 3], "
                "'assigned', ('172.20.' || (ARRAY[41, 42, 43])[1 + d % 3] || '.' || "
                "(100 + (d / 3) % 100))::inet, "
                "('f0:1f:af:00:' || lpad(to_hex(d / 256), 2, '0') || ':' || "
                "lpad(to_hex(d % 256), 2, '0'))::macaddr, 'PC-' || d "
                "FROM generate_series(0, 299) AS d, "
                "LATERAL (SELECT CAST(:start AS timestamptz) + d * interval '5 seconds' "
                "AS t) AS x"
            ),
            params,
        )
        connection.execute(
            text(
                "INSERT INTO mt_arp (time, time_received, source, sender, raw_day, raw_line, "
                "raw_offset, ip, mac, interface) "
                "SELECT t, t, 'core-r1', '172.20.10.1', :day, 1000 + h * 300 + d, 0, "
                "('172.20.' || (ARRAY[41, 42, 43])[1 + d % 3] || '.' || "
                "(100 + (d / 3) % 100))::inet, "
                "('f0:1f:af:00:' || lpad(to_hex(d / 256), 2, '0') || ':' || "
                "lpad(to_hex(d % 256), 2, '0'))::macaddr, "
                "'vlan' || (ARRAY[41, 42, 43])[1 + d % 3] "
                "FROM generate_series(0, 299) AS d, "
                "generate_series(0, CAST(:days * 24 AS integer) - 1) AS h, "
                "LATERAL (SELECT CAST(:start AS timestamptz) + h * interval '1 hour' "
                "+ interval '30 minutes' AS t) AS x"
            ),
            {**params, "days": SIZE.days},
        )
    timings["load_total"] = time.perf_counter() - began
    # What autovacuum would do in production (statistics, visibility map); done here so no
    # autovacuum worker is still busy in the test database when it is dropped.
    with engine.connect() as connection:
        connection.execution_options(isolation_level="AUTOCOMMIT").execute(
            text("VACUUM (ANALYZE) fg_traffic, fg_web, fg_dns, mt_dhcp, mt_arp")
        )
    return timings


def test_lookup_is_fast_on_continuous_data(db_engine: Engine, db_session: Session) -> None:
    timings = _load(db_engine)
    count = db_session.scalar(text("SELECT count(*) FROM fg_traffic"))
    assert count == SIZE.rows

    began = time.perf_counter()
    report = rebuild_day(db_session, DAY, horizon=tr(DAY + timedelta(days=4), 0))
    db_session.commit()
    timings["rebuild_day"] = time.perf_counter() - began
    # 300 DHCP leases (00:00-00:25); the hourly ARP-LOG from 00:30 only extends them.
    assert report.inserted == 300
    assert report.events["traffic_points"] > 0
    assert report.lookahead_chunks >= 1  # the data continues past midnight

    settings = LookupSettings.from_settings({**get_settings(db_session), "nat.wan_transip": WAN})
    at = tr(DAY, SIZE.at_hour)
    exact_port = db_session.scalar(
        text("SELECT transport FROM fg_traffic WHERE time >= :at ORDER BY time LIMIT 1"),
        {"at": at},
    )
    cases = {
        "candidates_no_destination": LookupRequest(
            at=at, public_ip="192.0.2.44", public_port=40001
        ),
        "candidates_destination": LookupRequest(
            at=at, public_ip="192.0.2.44", dest_ip="23.10.10.1", dest_port=80
        ),
        "domain_popular": LookupRequest(at=at, public_ip="192.0.2.44", dest_domain="google.com"),
        "domain_rare": LookupRequest(at=at, public_ip="192.0.2.44", dest_domain="site123.example"),
        "exact": LookupRequest(at=at, public_ip=WAN, public_port=exact_port),
    }
    outcomes = {}
    for name, request in cases.items():
        began = time.perf_counter()
        outcomes[name] = run_lookup(db_session, request, settings)
        timings[name] = time.perf_counter() - began

    window = Window(at, 60, settings.max_session_seconds)
    plans = {
        "candidates_no_destination": explain_rows(db_session, window, transip=WAN, analyze=True),
        "candidates_destination": explain_rows(
            db_session, window, transip=WAN, dstips={"23.10.10.1"}, analyze=True
        ),
        "domain_popular": explain_rows(
            db_session,
            window,
            transip=WAN,
            domain="google.com",
            dstips={"23.1.1.1", "23.2.2.1"},
            analyze=True,
        ),
        "exact": explain_rows(db_session, window, transip=WAN, transport=exact_port),
    }
    print(f"\n--- performance (seconds), {SIZE} ---")
    for name, seconds in timings.items():
        print(f"{name:28s} {seconds:8.3f}")
    for name, outcome in outcomes.items():
        print(f"{name:28s} mode={outcome.mode} matches={len(outcome.matches)}")
    for name, plan in plans.items():
        print(f"\n--- EXPLAIN {name} ---")
        print("\n".join(plan))

    no_destination = outcomes["candidates_no_destination"]
    assert no_destination.mode == "candidates"
    assert no_destination.truncated  # ~1,400 sessions overlap ±60 s; the best 100 are listed
    assert len(no_destination.matches) == settings.max_candidates
    assert all(m.chain is not None for m in no_destination.matches)
    assert outcomes["candidates_destination"].matches
    assert {m.destination_match for m in outcomes["candidates_destination"].matches} == {"ip_port"}
    popular = outcomes["domain_popular"]
    assert popular.mode == "candidates"
    assert {m.destination_match for m in popular.matches} == {"domain"}
    assert outcomes["domain_rare"].mode in ("candidates", "none")
    exact = outcomes["exact"]
    assert exact.mode == "exact"
    assert {m.kind for m in exact.matches} == {"exact"}
    assert any(m.time_distance_ms == 0 for m in exact.matches)  # (ports repeat at 3 M/day)
    for name in cases:
        assert timings[name] < LIMIT_SECONDS, f"{name} took {timings[name]:.2f} s"
    # Every plan reads through an index with the bounded time range (no sequential scan).
    for name, plan in plans.items():
        joined = "\n".join(plan)
        assert "Seq Scan" not in joined, name
        assert "Index" in joined, name
        assert '"time" >=' in joined, name
        assert '"time" <=' in joined, name
    assert "transip_transport_time" in "\n".join(plans["exact"])
    assert "dstip_dstport_time" in "\n".join(plans["candidates_destination"])
