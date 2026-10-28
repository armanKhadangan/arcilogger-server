"""Spec 14: a 5651 lookup over a ±60 s window must take less than 5 s.

Continuous synthetic traffic (300 clients, sessions of 0-96 s plus one 2-hour session in 500)
that does **not** stop at midnight, so the scan tail of ``lookup.max_session_seconds`` (24 h
after the window) is full; web filter logs (a fifth of them for ``*.google.com``) and DNS
answers for the domain path; DHCP + hourly ARP for the 300 devices. Measured: the lease rebuild
of the first day (with look-ahead into the next), the double-NAT candidate search without
destination (every session that overlaps the window), with a destination IP + port, with a
popular and a rare domain, and an exact lookup. ``EXPLAIN`` shows that every query uses an index
and the bounded time range.

**Which chunks.** :func:`test_lookup_is_fast_on_continuous_data` measures uncompressed data: on
plain PostgreSQL every partition, on TimescaleDB uncompressed chunks only (the compression
policies are paused before loading, see :func:`_load`). On TimescaleDB every chunk older than
7 days is compressed, and most 5651 requests ask about such days, so
:func:`test_lookup_is_fast_on_compressed_chunks` (TimescaleDB with compression only, the Linux CI
job) compresses the loaded chunks itself and checks the same answers and the same 5 s budget
there. Its plans are not those of uncompressed chunks: a compressed chunk's btree indexes hold
only its uncompressed rows, its batches are found by their time range (TimescaleDB's ``_ts_meta``
columns) and decompressed, and a small chunk is read whole. One thing is checked: the domain
search's correlated web/app sub-query bounds the batches by the session's own start. Without
that bound it read every batch of the chunk for each session of the window; the default size
stayed under 0.6 s, ``large`` took 6-7 s (docs/CORRELATION.md section 6).

``ARCILOGGER_PERF`` selects the size:

* unset / ``1`` (default, about 30 s on a developer machine, most of it loading the rows):
  1,000,000 ``fg_traffic`` rows a day for 1.1 days, 275,000 web filter rows;
* ``large``: 3,000,000 rows a day for two days (6 M rows), 1,500,000 web filter rows — the
  spec's busy-day volume; takes a few minutes, run it before releases;
* ``0``: skipped.

Timings and plans are printed (run with ``-s`` to see them).

**Wave 3 guards** (contract 3.8; marked ``perf``, so ``poe test-perf`` runs them, alone): the
lookup's srcmac guard and sightings, the D8 probe, the post-lease checks, the clock measurement
and the DHCP-origin query, each under its own bound, on two sites written once for the module
(see the comment above :data:`GUARD_AT`).
"""

from __future__ import annotations

import functools
import os
import time
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from ipaddress import IPv4Network
from typing import Any

import pytest
from sqlalchemy import Engine, event, text
from sqlalchemy.orm import Session

from arcilogger_core.timeutil import day_window
from arcilogger_db.egress import legacy_egresses
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import Source
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.settings import get_settings, set_setting
from arcilogger_db.testing import TestServer, pause_compression_jobs
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import (
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    attribution,
    run_lookup,
)
from arcilogger_worker.lookup import engine as lookup_engine
from arcilogger_worker.lookup.attribution import Attributor
from arcilogger_worker.lookup.sessions import Window, explain_rows
from arcilogger_worker.lookup.trust import untrusted_sources
from arcilogger_worker.testing import tr

MODE = os.environ.get("ARCILOGGER_PERF", "1")
pytestmark = pytest.mark.skipif(
    MODE == "0", reason="performance test switched off (ARCILOGGER_PERF=0)"
)

DAY = date(2026, 10, 12)
WAN = "100.64.12.9"
#: The converted exit's NAT address (the candidate searches).
WAN_EXIT = (IPv4Network(f"{WAN}/32"),)
LIMIT_SECONDS = 5.0
#: The tables :func:`_load` fills.
LOADED = ("fg_traffic", "fg_web", "fg_dns", "mt_dhcp", "mt_arp")


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


def _compression(engine: Engine) -> bool:
    """Whether the hypertables are compressed after 7 days (TimescaleDB with compression)."""
    with engine.connect() as connection:
        status = connection.scalar(text("SELECT value FROM settings WHERE key = 'db.timescale'"))
    return bool((status or {}).get("compression"))


def _load(engine: Engine) -> dict[str, float]:
    start, _ = (t.astimezone(UTC) for t in day_window(DAY))
    timings: dict[str, float] = {}
    with engine.begin() as connection:
        ensure_partitions(connection, today=DAY)
    if _compression(engine):
        # The compression policies compress every chunk older than 7 days by the server's now(),
        # and DAY is a fixed past day: a policy the template left due runs when this database's
        # TimescaleDB scheduler starts (up to a minute after the copy was created) and would
        # compress the rows loaded here under the lookups, at a moment no test chose. The policies
        # are paused before the load; the test of compressed chunks compresses them itself.
        pause_compression_jobs(engine)
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
            text(f"VACUUM (ANALYZE) {', '.join(LOADED)}")
        )
    return timings


def _rebuild_leases(session: Session, timings: dict[str, float]) -> None:
    began = time.perf_counter()
    report = rebuild_day(session, DAY, horizon=tr(DAY + timedelta(days=4), 0))
    session.commit()
    timings["rebuild_day"] = time.perf_counter() - began
    # 300 DHCP leases (00:00-00:25); the hourly ARP-LOG from 00:30 only extends them.
    assert report.inserted == 300
    assert report.events["traffic_points"] > 0
    assert report.lookahead_chunks >= 1  # the data continues past midnight


@dataclass(frozen=True)
class Lookups:
    settings: LookupSettings
    window: Window
    exact_port: int
    outcomes: dict[str, LookupOutcome]


def _lookups(session: Session, timings: dict[str, float]) -> Lookups:
    """Runs the measured lookups, each timed into ``timings``."""
    exits, _dropped = legacy_egresses([], WAN, False)  # Wave 2's nat.wan_transip
    settings = LookupSettings.from_settings({**get_settings(session), "nat.egresses": exits})
    at = tr(DAY, SIZE.at_hour)
    exact_port = session.scalar(
        text("SELECT transport FROM fg_traffic WHERE time >= :at ORDER BY time LIMIT 1"),
        {"at": at},
    )
    assert exact_port is not None
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
        outcomes[name] = run_lookup(session, request, settings)
        timings[name] = time.perf_counter() - began
    return Lookups(settings, Window(at, 60, settings.max_session_seconds), exact_port, outcomes)


def _print(title: str, timings: dict[str, float], lookups: Lookups) -> None:
    print(f"\n--- performance (seconds), {SIZE}, {title} ---")
    for name, seconds in timings.items():
        print(f"{name:28s} {seconds:8.3f}")
    for name, outcome in lookups.outcomes.items():
        print(f"{name:28s} mode={outcome.mode} matches={len(outcome.matches)}")


def _check_answers_and_budget(timings: dict[str, float], lookups: Lookups) -> None:
    outcomes = lookups.outcomes
    no_destination = outcomes["candidates_no_destination"]
    assert no_destination.mode == "candidates"
    assert no_destination.truncated  # ~1,400 sessions overlap ±60 s; the best 100 are listed
    assert len(no_destination.matches) == lookups.settings.max_candidates
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
    for name in outcomes:
        assert timings[name] < LIMIT_SECONDS, f"{name} took {timings[name]:.2f} s"


def test_lookup_is_fast_on_continuous_data(db_engine: Engine, db_session: Session) -> None:
    timings = _load(db_engine)
    count = db_session.scalar(text("SELECT count(*) FROM fg_traffic"))
    assert count == SIZE.rows
    _rebuild_leases(db_session, timings)
    lookups = _lookups(db_session, timings)
    window, exact_port = lookups.window, lookups.exact_port
    plans = {
        "candidates_no_destination": explain_rows(
            db_session, window, transip_in=WAN_EXIT, analyze=True
        ),
        "candidates_destination": explain_rows(
            db_session, window, transip_in=WAN_EXIT, dstips={"23.10.10.1"}, analyze=True
        ),
        "candidates_unclaimed": explain_rows(
            db_session, window, unclaimed=(), dstips={"23.10.10.1"}, analyze=True
        ),
        "domain_popular": explain_rows(
            db_session,
            window,
            transip_in=WAN_EXIT,
            domain="google.com",
            dstips={"23.1.1.1", "23.2.2.1"},
            analyze=True,
        ),
        "exact": explain_rows(db_session, window, transip=WAN, transport=exact_port),
    }
    _print("uncompressed", timings, lookups)
    for name, plan in plans.items():
        print(f"\n--- EXPLAIN {name} ---")
        print("\n".join(plan))

    _check_answers_and_budget(timings, lookups)
    # Every plan reads through an index with the bounded time range (no sequential scan).
    for name, plan in plans.items():
        joined = "\n".join(plan)
        assert "Seq Scan" not in joined, name
        assert "Index" in joined, name
        assert '"time" >=' in joined, name
        assert '"time" <=' in joined, name
    assert "transip_transport_time" in "\n".join(plans["exact"])
    assert "dstip_dstport_time" in "\n".join(plans["candidates_destination"])


def test_lookup_is_fast_on_compressed_chunks(db_engine: Engine, db_session: Session) -> None:
    """Spec 14 on the chunks most 5651 requests read on TimescaleDB: compressed ones.

    The same data, leases and lookups as above; the leases are rebuilt first, as the worker did
    when the day was new, then every chunk of the loaded tables is compressed, as the policy
    does 7 days later. The answers and the 5 s budget are those of the uncompressed test; of
    the plans only the domain search's is checked (see the module docstring). On the Linux CI
    job (``ARCILOGGER_EXPECT_TIMESCALE=1``) a server without compression fails instead of
    skipping.
    """
    if not _compression(db_engine):
        if os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") == "1":
            pytest.fail("TimescaleDB with compression expected (ARCILOGGER_EXPECT_TIMESCALE=1)")
        pytest.skip("TimescaleDB with compression only (the Linux CI job runs this)")
    timings = _load(db_engine)
    _rebuild_leases(db_session, timings)
    began = time.perf_counter()
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for table in LOADED:
            connection.execute(
                text(
                    "SELECT compress_chunk(c, if_not_compressed => true) "
                    "FROM show_chunks(CAST(:table AS regclass)) AS c"
                ),
                {"table": table},
            )
        # Statistics of the compressed chunks, as autovacuum gathers them after the policy ran.
        connection.execute(text(f"ANALYZE {', '.join(LOADED)}"))
        chunks = connection.execute(
            text(
                "SELECT hypertable_name, count(*), count(*) FILTER (WHERE is_compressed) "
                "FROM timescaledb_information.chunks WHERE hypertable_name = ANY(:tables) "
                "GROUP BY hypertable_name"
            ),
            {"tables": list(LOADED)},
        ).all()
    timings["compress"] = time.perf_counter() - began
    # Every loaded row is in a compressed chunk: the lookups below read no uncompressed one.
    compressed = {table: (total, done) for table, total, done in chunks}
    assert set(compressed) == set(LOADED)
    assert all(total == done for total, done in compressed.values()), compressed
    lookups = _lookups(db_session, timings)
    plan = explain_rows(
        db_session,
        lookups.window,
        transip_in=WAN_EXIT,
        domain="google.com",
        dstips={"23.1.1.1", "23.2.2.1"},
        analyze=True,
    )
    _print("compressed", timings, lookups)
    print("\n--- EXPLAIN domain_popular ---")
    print("\n".join(plan))

    _check_answers_and_budget(timings, lookups)
    # The bound on the session's start (``time - make_interval(… duration)``) reaches the batches'
    # time range: with COALESCE in it TimescaleDB left it out, and each session read them all.
    assert any("_ts_meta" in line and "make_interval" in line for line in plan)


# -- Wave 3: the lookup guards of contract 3.8 that the lookup engine owns (WP-C2) -----------------
#
# Two sites, each written once into its own database for the module (``INSERT … SELECT
# generate_series``) and read by every guard of it; each guard runs its lookup once to warm the
# caches, then times the engine's own functions (what ships) on a second run.
#
# * The **traffic site** (a FortiGate-only site, srcmac on): 1 M ``fg_traffic`` rows a day from
#   2 h before the moment to the end of the internal sightings' scan (24 h after the window) of
#   240 clients in VLANs 41-43 with srcmac (VLAN 43's interface is not listed), the FortiGate DHCP
#   rows of 400 days at the load generator's rate, the leases of 400 days (one a day, then a
#   7-day lease for the last 30), and ``messages`` of two FortiGate sources at 1 M lines a day each
#   over two hours around the moment with the ``search_counts`` of 400 days.
# * The **event site** (``fg_event`` before the upgrade): :data:`EVENT_ROWS` ``system`` lines a
#   day over the 100-day window 3.8 names for the D8 probe (half of them FortiGate DHCP lines of
#   other clients), one line every 5 minutes for the 300 days before; FortiGate DHCP rows of
#   400 days at the load generator's rate; 100 ``mt_dhcp`` chains whose last DHCP record is 150
#   days old (kept open by traffic), 20 ``fg_dhcp`` chains half a 7-day lease after their ACK,
#   20 ``fg_dhcp`` chains half a 30-day lease after theirs (:data:`LONG_LEASE`), and 20
#   addresses without any DHCP row, each group with sessions at its own moment. Every
#   query read is bounded by time and prunes the partitions before the window, so those 300
#   days are written sparse to keep the database small.
#
# ``ARCILOGGER_PERF_EVENTS`` sets the event lines a day: the default is a twentieth of 1 M
# FortiGate lines, the share the alert guard writes (``test_alerts_lease_performance``); the load
# generator writes 0.3 % (perf statistics and, before the upgrade, DHCP). The ``fg_event`` queries
# can only use ``ix_fg_event_subtype_time``, so their cost grows with the ``system`` lines in
# their window (contract 3.8, round-7 note "3.8 guard for the round-6 post-lease query"): on the
# 100-day window the probe and the post-lease query missed their 0.5 s here, so the lookup reads
# the 2.8.3 fallback, the last 604,800 s (``attribution.UNCONVERTED_WINDOW``; deviation c2), except
# for an ``fg_dhcp`` chain, whose post-lease window starts at its last ACK uncapped (half a lease:
# the 30-day guard). The converted-row query reads ``fg_dhcp`` on its MAC and address indexes and
# keeps the 100 days of 2.7.

#: Moments of the guards: the traffic site's and the event site's lookups.
GUARD_AT = tr(DAY, 14)
#: The event site's four groups have their sessions at their own moments (one search each).
PROBE_AT, FG_AT, MT_AT, LONG_AT = (
    GUARD_AT,
    GUARD_AT + timedelta(minutes=10),
    GUARD_AT + timedelta(minutes=20),
    GUARD_AT + timedelta(minutes=30),
)
#: The lease time of the event site's long-lease chains: 30 days (FortiOS allows up to 100), so
#: the window of their ``fg_event`` post-lease check, from their last ACK, is about 15 days.
LONG_LEASE = 30 * 86400
#: ``fg_event`` ``system`` lines a day of the event site (see above).
EVENT_ROWS = int(os.environ.get("ARCILOGGER_PERF_EVENTS", "50000"))
#: FortiGate DHCP lines a day: the load generator's share (``--dhcp-server fortigate``: 1,234 of
#: 1,071,102 lines in 7 days) of 1 M lines a day, one every 75 s.
DHCP_STEP_SECONDS = 75
#: Clients of the traffic site; the first three (one per VLAN) have no lease and no DHCP row.
CLIENTS = 240
#: The public address of every guard lookup: no exit is configured, so it is the implicit
#: double-NAT exit's candidate search (2.8.3 ``unconfigured``).
PUBLIC = "198.51.100.20"
#: The site's FortiGate and a second one (an HA pair's other member; the clock guard measures
#: two sources), and the core router.
GUARD_SOURCES = (
    ("fortigate", "fortigate", "172.31.255.1"),
    ("fortigate-2", "fortigate", "172.31.255.2"),
    ("core-r1", "mikrotik", "172.20.10.1"),
)

#: Every statement takes its values as bound parameters; a client ``c`` (0 … CLIENTS - 1) has
#: the address ``172.20.<41 + c % 3>.<100 + c / 3>``, the MAC ``00:00:5e:00:53:<c>`` and the
#: FortiGate interface ``vlan<41 + c % 3>`` (the ``LATERAL`` sub-query ``k`` of each statement).
TRAFFIC_SITE = (
    # 1 M sessions a day, each of its own (sessionid), round robin over the clients.
    "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "logid, subtype, action, sessionid, proto, srcip, srcport, srcintf, srcmac, dstip, dstport, "
    "dstintf, transip, transport, trandisp, duration, is_interim) "
    "SELECT t, t, 'fortigate', '172.31.255.1', :day, g, g::bigint * 900, '0000000013', "
    "'forward', 'accept', 1000000 + g, 6, k.ip, 1024 + (g % 60000), k.interface, k.mac, "
    "('23.' || (g % 200) || '.' || (g % 250) || '.1')::inet, 443, 'wan1', CAST(:wan AS inet), "
    "1024 + (g % 60000), 'snat', CASE WHEN g % 487 = 0 THEN 7200 ELSE g % 97 END, false "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT g % :clients AS c, "
    "CAST(:at AS timestamptz) - interval '2 hours' + g * CAST(:step AS interval) AS t) AS x, "
    "LATERAL (SELECT ('172.20.' || (41 + c % 3) || '.' || (100 + c / 3))::inet AS ip, "
    "('00:00:5e:00:53:' || lpad(to_hex(c), 2, '0'))::macaddr AS mac, "
    "'vlan' || (41 + c % 3) AS interface) AS k",
    # FortiGate DHCP ACKs of 400 days (none of the first three clients): a 1-day lease logged
    # until 30 days ago, a 7-day lease since.
    "INSERT INTO fg_dhcp (time, time_received, source, sender, extra, raw_day, raw_line, "
    "raw_offset, logid, subtype, dhcp_msg, interface, ip, mac, lease, device_time) "
    "SELECT t, t, 'fortigate', '172.31.255.1', '{\"vd\": \"root\"}'::jsonb, :day, g, g * 300, "
    "'0100026001', 'system', 'Ack', k.interface, k.ip, k.mac, "
    "CASE WHEN t < CAST(:at AS timestamptz) - interval '30 days' THEN 86400 ELSE 604800 END, "
    "true "
    "FROM generate_series(1, 400 * 86400 / :dhcp_step) AS g, "
    "LATERAL (SELECT 3 + g % (:clients - 3) AS c, CAST(:at AS timestamptz) "
    "+ interval '25 hours' - g * make_interval(secs => :dhcp_step) AS t) AS x, "
    "LATERAL (SELECT ('172.20.' || (41 + c % 3) || '.' || (100 + c / 3))::inet AS ip, "
    "('00:00:5e:00:53:' || lpad(to_hex(c), 2, '0'))::macaddr AS mac, "
    "'vlan' || (41 + c % 3) AS interface) AS k",
    # The leases: one a day for 370 days, then an open 7-day lease for the last 30 days; the
    # evidence is the ACK that opened each (``EvidenceRef.to_dict``).
    'INSERT INTO leases (ip, mac, vlan, server, start, "end", end_reason, confidence, '
    "source_table, last_evidence, lease_seconds, evidence) "
    "SELECT k.ip, k.mac, 41 + c % 3, k.interface, s, "
    "CASE WHEN d = 30 THEN NULL ELSE s + interval '1 day' END, "
    "CASE WHEN d = 30 THEN 'open' ELSE 'timeout' END, 'high', 'fg_dhcp', "
    "CASE WHEN d = 30 THEN CAST(:at AS timestamptz) + interval '24 hours' ELSE s END, w.lease, "
    "jsonb_build_array(jsonb_build_object('table', 'fg_dhcp', 'row_id', c + 1, 'time', s, "
    "'raw', jsonb_build_object('source', 'fortigate', 'day', :day, 'line', c + 1, 'offset', 0), "
    "'summary', k.interface || ' assigned ' || host(k.ip) || ' for ' || k.mac "
    "|| ' (FortiGate DHCP, logid 0100026001, lease ' || w.lease || ')')) "
    "FROM generate_series(3, :clients - 1) AS c, generate_series(30, 399) AS d, "
    "LATERAL (SELECT CAST(:at AS timestamptz) - d * interval '1 day' AS s, "
    "CASE WHEN d = 30 THEN 604800 ELSE 86400 END AS lease) AS w, "
    "LATERAL (SELECT ('172.20.' || (41 + c % 3) || '.' || (100 + c / 3))::inet AS ip, "
    "('00:00:5e:00:53:' || lpad(to_hex(c), 2, '0'))::macaddr AS mac, "
    "'vlan' || (41 + c % 3) AS interface) AS k",
    # Every line as a message of both FortiGates (two sources) around the moment, the device
    # clock 1 s behind the receive time.
    "INSERT INTO messages (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "type, text) "
    "SELECT t - interval '1 second', t, s.name, s.address, :day, g, g::bigint * 400, 'traffic', '' "
    "FROM (VALUES ('fortigate', CAST('172.31.255.1' AS inet)), "
    "('fortigate-2', CAST('172.31.255.2' AS inet))) AS s(name, address), "
    "generate_series(1, :rows_4h) AS g, "
    "LATERAL (SELECT CAST(:at AS timestamptz) - interval '2 hours' "
    "+ g * CAST(:step AS interval) AS t) AS x",
    # The search counts of 400 days (the clock check picks the sources of an empty search
    # from them).
    "INSERT INTO search_counts (hour, source, raw_day, kind, tbl, action, net, n) "
    "SELECT h, s.name, CAST(h AT TIME ZONE 'Europe/Istanbul' AS date), s.kind, s.tbl, s.action, "
    "CAST('172.20.' || v || '.0/24' AS cidr), s.n "
    "FROM generate_series(CAST(:at AS timestamptz) - interval '400 days', "
    "CAST(:at AS timestamptz) + interval '25 hours', interval '1 hour') AS h, "
    "(VALUES ('fortigate', 'traffic', 'fg_traffic', 'accept', 13889), "
    "('fortigate', 'event', 'fg_dhcp', NULL, 16), "
    "('fortigate-2', 'traffic', 'fg_traffic', 'accept', 1000)) AS s(name, kind, tbl, action, n), "
    "generate_series(41, 43) AS v",
)

EVENT_SITE = (
    # The probe's 100-day window (and a day before it): half FortiGate DHCP lines of other
    # clients on the staff Wi-Fi (MACs :a0-:ff, none of a chain's), half other system events.
    "INSERT INTO fg_event (time, time_received, source, sender, extra, raw_day, raw_line, "
    "raw_offset, logid, subtype) "
    "SELECT t, t, 'fortigate', '172.31.255.1', CASE WHEN g % 2 = 0 THEN jsonb_build_object("
    "'ip', '172.20.60.' || (10 + g % 200), "
    "'mac', '00:00:5e:00:53:' || to_hex(160 + g % 96), 'dhcp_msg', 'Ack', "
    "'lease', '86400', 'interface', 'vlan60') ELSE '{}'::jsonb END, :day, g, g::bigint * 300, "
    "CASE WHEN g % 2 = 0 THEN '0100026001' ELSE '0100040704' END, 'system' "
    "FROM generate_series(1, 101 * :events) AS g, "
    "LATERAL (SELECT CAST(:at AS timestamptz) + interval '1 hour' "
    "- g * (interval '1 day' / :events) AS t) AS x",
    # The 300 days before: the performance statistics every 5 minutes.
    "INSERT INTO fg_event (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "logid, subtype) "
    "SELECT t, t, 'fortigate', '172.31.255.1', :day, g, g::bigint * 300, '0100040704', 'system' "
    "FROM generate_series(1, 299 * 288) AS g, "
    "LATERAL (SELECT CAST(:at AS timestamptz) - interval '101 days' "
    "- g * interval '5 minutes' AS t) AS x",
    # FortiGate DHCP of 400 days on the staff Wi-Fi (VLAN 60, another configured network than
    # the chains' own), the chains' MACs among them.
    "INSERT INTO fg_dhcp (time, time_received, source, sender, extra, raw_day, raw_line, "
    "raw_offset, logid, subtype, dhcp_msg, interface, ip, mac, lease, device_time) "
    "SELECT t, t, 'fortigate', '172.31.255.1', '{\"vd\": \"root\"}'::jsonb, :day, g, g * 300, "
    "'0100026001', 'system', 'Ack', 'vlan60', ('172.20.60.' || (10 + g % 200))::inet, "
    "('00:00:5e:00:53:' || lpad(to_hex(g % 256), 2, '0'))::macaddr, 86400, true "
    "FROM generate_series(1, 400 * 86400 / :dhcp_step) AS g, "
    "LATERAL (SELECT CAST(:at AS timestamptz) - g * make_interval(secs => :dhcp_step) AS t) AS x",
    # The 20 fg_dhcp chains (VLAN 42, MACs :64-:77): an ACK every half lease of 7 days, the last
    # one half a lease (less a minute) before their moment.
    "INSERT INTO fg_dhcp (time, time_received, source, sender, extra, raw_day, raw_line, "
    "raw_offset, logid, subtype, dhcp_msg, interface, ip, mac, lease, device_time) "
    "SELECT t, t, 'fortigate', '172.31.255.1', '{\"vd\": \"root\"}'::jsonb, :day, i * 10 + k, 0, "
    "'0100026001', 'system', 'Ack', 'vlan42', ('172.20.42.' || (100 + i))::inet, "
    "('00:00:5e:00:53:' || to_hex(100 + i))::macaddr, 604800, true "
    "FROM generate_series(0, 19) AS i, generate_series(0, 8) AS k, "
    "LATERAL (SELECT CAST(:fg_at AS timestamptz) - interval '302340 seconds' "
    "- k * interval '302400 seconds' AS t) AS x",
    # The 20 long-lease fg_dhcp chains (VLAN 42, .140-.159, MACs :78-:8b): an ACK every half
    # lease of 30 days, the last one half a lease (less a minute) before their moment.
    "INSERT INTO fg_dhcp (time, time_received, source, sender, extra, raw_day, raw_line, "
    "raw_offset, logid, subtype, dhcp_msg, interface, ip, mac, lease, device_time) "
    "SELECT t, t, 'fortigate', '172.31.255.1', '{\"vd\": \"root\"}'::jsonb, :day, "
    "500 + i * 10 + k, 0, '0100026001', 'system', 'Ack', 'vlan42', "
    "('172.20.42.' || (140 + i))::inet, "
    "('00:00:5e:00:53:' || to_hex(120 + i))::macaddr, :long_lease, true "
    "FROM generate_series(0, 19) AS i, generate_series(0, 2) AS k, "
    "LATERAL (SELECT CAST(:long_at AS timestamptz) - make_interval(secs => :long_lease / 2 - 60) "
    "- k * make_interval(secs => :long_lease / 2) AS t) AS x",
    # The 100 mt_dhcp chains (VLANs 41 and 43, MACs :00-:63): assigned 150 days ago.
    "INSERT INTO mt_dhcp (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "server, vlan, action, ip, mac) "
    "SELECT CAST(:at AS timestamptz) - interval '150 days', "
    "CAST(:at AS timestamptz) - interval '150 days', 'core-r1', '172.20.10.1', :day, j, 0, "
    "'dhcp' || v, v, 'assigned', ('172.20.' || v || '.' || (100 + j % 50))::inet, "
    "('00:00:5e:00:53:' || lpad(to_hex(j), 2, '0'))::macaddr "
    "FROM generate_series(0, 99) AS j, LATERAL (SELECT CASE WHEN j < 50 THEN 41 ELSE 43 END "
    "AS v) AS x",
    # Their leases, open: traffic kept them alive (last evidence an hour ago).
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
    # The fg_dhcp chains' leases, open since their first ACK of the 30 days.
    "INSERT INTO leases (ip, mac, vlan, server, start, end_reason, confidence, source_table, "
    "last_evidence, lease_seconds, evidence) "
    "SELECT ip, mac, 42, 'vlan42', s, 'open', 'high', 'fg_dhcp', "
    "CAST(:fg_at AS timestamptz) - interval '302340 seconds', 604800, "
    "jsonb_build_array(jsonb_build_object('table', 'fg_dhcp', 'row_id', i * 10 + 8, 'time', s, "
    "'raw', jsonb_build_object('source', 'fortigate', 'day', :day, 'line', i * 10 + 8, "
    "'offset', 0), 'summary', 'vlan42 assigned ' || host(ip) || ' for ' || mac "
    "|| ' (FortiGate DHCP, logid 0100026001, lease 604800)')) "
    "FROM generate_series(0, 19) AS i, LATERAL (SELECT CAST(:fg_at AS timestamptz) "
    "- interval '302340 seconds' - 8 * interval '302400 seconds' AS s) AS x, "
    "LATERAL (SELECT ('172.20.42.' || (100 + i))::inet AS ip, "
    "('00:00:5e:00:53:' || to_hex(100 + i))::macaddr AS mac) AS k",
    # The long-lease chains' leases, open since their first ACK.
    "INSERT INTO leases (ip, mac, vlan, server, start, end_reason, confidence, source_table, "
    "last_evidence, lease_seconds, evidence) "
    "SELECT ip, mac, 42, 'vlan42', s, 'open', 'high', 'fg_dhcp', "
    "CAST(:long_at AS timestamptz) - make_interval(secs => :long_lease / 2 - 60), :long_lease, "
    "jsonb_build_array(jsonb_build_object('table', 'fg_dhcp', 'row_id', 500 + i * 10 + 2, "
    "'time', s, 'raw', jsonb_build_object('source', 'fortigate', 'day', :day, "
    "'line', 500 + i * 10 + 2, 'offset', 0), 'summary', 'vlan42 assigned ' || host(ip) "
    "|| ' for ' || mac || ' (FortiGate DHCP, logid 0100026001, lease ' || :long_lease || ')')) "
    "FROM generate_series(0, 19) AS i, LATERAL (SELECT CAST(:long_at AS timestamptz) "
    "- make_interval(secs => :long_lease / 2 - 60) "
    "- 2 * make_interval(secs => :long_lease / 2) AS s) AS x, "
    "LATERAL (SELECT ('172.20.42.' || (140 + i))::inet AS ip, "
    "('00:00:5e:00:53:' || to_hex(120 + i))::macaddr AS mac) AS k",
    # The sessions: 20 addresses without any DHCP row (172.20.42.200-219), the 20 fg_dhcp
    # chains, the 100 mt_dhcp chains and the 20 long-lease chains, each group at its moment.
    "INSERT INTO fg_traffic (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "logid, subtype, action, sessionid, proto, srcip, srcport, srcintf, dstip, dstport, dstintf, "
    "transip, transport, trandisp, duration, is_interim) "
    "SELECT t, t, 'fortigate', '172.31.255.1', :day, n, n::bigint * 900, '0000000013', "
    "'forward', 'accept', 2000000 + n, 6, ip, 40000 + n, "
    "'vlan' || split_part(host(ip), '.', 3), ('198.51.100.' || (n % 200 + 1))::inet, 443, "
    "'wan1', CAST(:wan AS inet), 50000 + n, 'snat', 5, false "
    "FROM (SELECT n, CAST(:at AS timestamptz) + (n % 20) * interval '1 second' AS t, "
    "('172.20.42.' || (200 + n))::inet AS ip FROM generate_series(0, 19) AS n "
    "UNION ALL SELECT 100 + i, CAST(:fg_at AS timestamptz) + i * interval '1 second', "
    "('172.20.42.' || (100 + i))::inet FROM generate_series(0, 19) AS i "
    "UNION ALL SELECT 200 + j, CAST(:mt_at AS timestamptz) + (j % 30) * interval '1 second', "
    "('172.20.' || CASE WHEN j < 50 THEN 41 ELSE 43 END || '.' || (100 + j % 50))::inet "
    "FROM generate_series(0, 99) AS j "
    "UNION ALL SELECT 300 + i, CAST(:long_at AS timestamptz) + i * interval '1 second', "
    "('172.20.42.' || (140 + i))::inet FROM generate_series(0, 19) AS i) AS s",
)


def _site(server: TestServer, template: str, statements: Sequence[str]) -> Iterator[Engine]:
    """A database of the module with ``statements`` written into it (see the comment above)."""
    with server.database(template) as name:
        site = engine_from_url(server.dsn_for(name), pool_size=5, max_overflow=10)
        try:
            with site.begin() as connection:
                ensure_partitions(connection, today=DAY)
            if _compression(site):
                pause_compression_jobs(site)  # as in _load: no policy compresses under a guard
            with Session(site) as session:
                for source, kind, address in GUARD_SOURCES:
                    session.add(Source(name=source, type=kind, address=address))
                session.commit()
            params = {
                "at": GUARD_AT,
                "fg_at": FG_AT,
                "mt_at": MT_AT,
                "long_at": LONG_AT,
                "long_lease": LONG_LEASE,
                "day": DAY,
                "wan": WAN,
                "rows": 1_000_000 * 27 // 24,
                "rows_4h": 1_000_000 // 6,
                "step": "86.4 milliseconds",
                "clients": CLIENTS,
                "events": EVENT_ROWS,
                "dhcp_step": DHCP_STEP_SECONDS,
            }
            began = time.perf_counter()
            with site.begin() as connection:
                for statement in statements:
                    connection.execute(text(statement), params)
            with site.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
                connection.execute(text("VACUUM (ANALYZE)"))
            print(f"\nguard site written in {time.perf_counter() - began:.1f} s")
            yield site
        finally:
            site.dispose()


@pytest.fixture(scope="module")
def traffic_site(pg_server: TestServer, pg_template: str) -> Iterator[Engine]:
    for site in _site(pg_server, pg_template, TRAFFIC_SITE):
        with Session(site) as session:
            set_setting(
                session, "lookup.srcmac_interfaces", ["vlan41", "vlan42"], updated_by="tolga"
            )
            session.commit()
        yield site


@pytest.fixture(scope="module")
def event_site(pg_server: TestServer, pg_template: str) -> Iterator[Engine]:
    yield from _site(pg_server, pg_template, EVENT_SITE)


@dataclass
class _Timed:
    """The wall time of one function of the engine over the measured run, and its calls."""

    seconds: float = 0.0
    calls: list[tuple[Any, ...]] = field(default_factory=list)


class _Timer:
    """Times the engine's own functions (patched for the test only), so a guard measures the
    code that ships, its Python work included."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.monkeypatch = monkeypatch
        self.timed: dict[str, _Timed] = {}

    def time(self, owner: Any, name: str) -> _Timed:
        found = self.timed.setdefault(name, _Timed())
        original = getattr(owner, name)

        @functools.wraps(original)
        def timed(*args: Any, **kwargs: Any) -> Any:
            began = time.perf_counter()
            try:
                return original(*args, **kwargs)
            finally:
                found.seconds += time.perf_counter() - began
                found.calls.append((*args, *kwargs.values()))

        self.monkeypatch.setattr(owner, name, timed)
        return found

    def report(self, title: str) -> None:
        print(f"\n--- guard (seconds), {title} ---")
        for name, timed in self.timed.items():
            print(f"{name:28s} {timed.seconds:8.3f}  ({len(timed.calls)} calls)")


@pytest.fixture
def timer(monkeypatch: pytest.MonkeyPatch) -> _Timer:
    return _Timer(monkeypatch)


class _Executed:
    """The statements an engine sends while recorded, with their bound parameters: a guard
    checks what its query read, not only that it was fast (a query that reads nothing is fast
    too)."""

    def __init__(self) -> None:
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self._site: Engine | None = None

    def record(self, site: Engine) -> None:
        self._site = site
        event.listen(site, "before_cursor_execute", self._executed)

    def stop(self) -> None:
        if self._site is not None:
            event.remove(self._site, "before_cursor_execute", self._executed)
            self._site = None

    def _executed(self, *args: Any) -> None:
        _connection, _cursor, statement, parameters, _context, _many = args
        self.statements.append((statement, dict(parameters)))

    def one(self, *parts: str) -> tuple[str, dict[str, Any]]:
        """The one recorded statement that contains every one of ``parts``."""
        (found,) = [
            (sql, params) for sql, params in self.statements if all(p in sql for p in parts)
        ]
        return found


def _moments(parameters: dict[str, Any]) -> list[datetime]:
    """Every moment bound to a statement, those in arrays too."""
    values = [
        v for value in parameters.values() for v in (value if isinstance(value, list) else [value])
    ]
    return [value for value in values if isinstance(value, datetime)]


@pytest.fixture
def executed() -> Iterator[_Executed]:
    recorded = _Executed()
    try:
        yield recorded
    finally:
        recorded.stop()


def _guard_lookup(site: Engine, request: LookupRequest, measure: Any) -> LookupOutcome:
    """``request`` run once to warm the caches, then again with ``measure()`` installing the
    timers (a lookup only reads; each run is rolled back)."""
    outcome = None
    for warm in (True, False):
        if not warm:
            measure()
        with Session(site) as session:
            settings = LookupSettings.from_settings(get_settings(session))
            outcome = run_lookup(session, request, settings, now=GUARD_AT + timedelta(days=2))
            session.rollback()
    assert outcome is not None
    return outcome


def _keys(outcome: LookupOutcome) -> list[str]:
    return [e.key for e in outcome.explanations]


# -- the traffic site ---------------------------------------------------------------------------


@pytest.mark.perf
def test_the_srcmac_guard_counts_two_hours_fast(traffic_site: Engine, timer: _Timer) -> None:
    """3.8: the srcmac guard query (``trust.srcmac_ip_counts`` as the lookup calls it, for the
    MACs of 100 candidates) over 2 h at 1 M rows a day, under 1 s; with the query for a row
    used outside that hour (``srcmac_addresses_within``; a long session logged at its end)."""
    counts: list[_Timed] = []

    def measure() -> None:
        counts.append(timer.time(lookup_engine, "srcmac_ip_counts"))
        counts.append(timer.time(lookup_engine, "srcmac_addresses_within"))

    outcome = _guard_lookup(traffic_site, LookupRequest(at=GUARD_AT, public_ip=PUBLIC), measure)
    timer.report("srcmac guard")
    assert outcome.mode == "candidates"
    assert len(outcome.matches) == 100
    (call,) = counts[0].calls
    _session, low, high, interfaces, _untrusted, macs = call
    assert (low, high) == (GUARD_AT - timedelta(hours=1), GUARD_AT + timedelta(hours=1))
    assert set(interfaces) == {"vlan41", "vlan42"}
    assert len(macs) > 50  # the candidates on the listed interfaces (two VLANs of three)
    # The guard passed (one address per MAC): a lease chain and its srcmac agree.
    assert any(
        "srcmac_agrees" in [e.key for e in m.chain.explanations] for m in outcome.matches if m.chain
    )
    seconds = sum(timed.seconds for timed in counts)
    assert seconds < 1.0, f"srcmac guard took {seconds:.3f} s"


@pytest.mark.perf
def test_the_srcmac_other_lease_check_is_fast(traffic_site: Engine, timer: _Timer) -> None:
    """3.8: the srcmac other-lease check (``Attributor.leases_of_macs`` of the guard, every
    considered MAC over the hour around the moment; 400 days of leases), under 0.1 s."""
    leases: list[_Timed] = []

    def measure() -> None:
        leases.append(timer.time(Attributor, "leases_of_macs"))

    outcome = _guard_lookup(traffic_site, LookupRequest(at=GUARD_AT, public_ip=PUBLIC), measure)
    timer.report("srcmac other-lease check")
    assert len(outcome.matches) == 100
    (timed,) = leases
    (call,) = timed.calls
    assert len(call[1]) > 50
    assert timed.seconds < 0.1, f"the other-lease check took {timed.seconds:.3f} s"


#: Internal lookups without a lease (the first three clients): on VLAN 43, whose interface is not
#: listed (no row may be used: the scan reads the address's whole day), on VLAN 41 (a srcmac
#: chain) and by the MAC of the VLAN 42 client alone.
SIGHTINGS = {
    "unlisted_interface": LookupRequest(at=GUARD_AT, internal_ip="172.20.43.100"),
    "listed_interface": LookupRequest(at=GUARD_AT, internal_ip="172.20.41.100"),
    "mac_only": LookupRequest(at=GUARD_AT, mac="00:00:5e:00:53:01"),
}


@pytest.mark.perf
@pytest.mark.parametrize("case", sorted(SIGHTINGS))
def test_internal_srcmac_sightings_are_fast(traffic_site: Engine, timer: _Timer, case: str) -> None:
    """3.8: internal-mode srcmac sightings (``srcmac_sightings`` of an address,
    ``srcmac_rows_of_mac`` of a MAC) at 1 M rows a day, under 0.5 s."""
    sightings: list[_Timed] = []

    def measure() -> None:
        sightings.append(timer.time(lookup_engine, "srcmac_sightings"))
        sightings.append(timer.time(lookup_engine, "srcmac_rows_of_mac"))

    outcome = _guard_lookup(traffic_site, SIGHTINGS[case], measure)
    timer.report(f"internal srcmac sightings, {case}")
    assert sum(len(timed.calls) for timed in sightings) == 1
    chain = outcome.attribution
    if case == "unlisted_interface":
        assert chain is None
        assert "no_lease" in _keys(outcome)
    else:
        assert chain is not None
        assert chain.mac_source == "srcmac"
        assert chain.internal_ip == (
            "172.20.41.100" if case == "listed_interface" else "172.20.42.100"
        )
    seconds = sum(timed.seconds for timed in sightings)
    assert seconds < 0.5, f"{case}: the sightings took {seconds:.3f} s"


@pytest.mark.perf
def test_the_source_clock_of_an_empty_search_is_fast(traffic_site: Engine, timer: _Timer) -> None:
    """3.8: the per-lookup clock measurement (2.7) for two sources over [T - 1 h, T + 1 h] of
    ``messages`` at 1 M lines a day each, under 0.2 s, with the ``search_counts`` choice of the
    searched sources of an empty result (a destination no session went to)."""
    parts: list[_Timed] = []

    def measure() -> None:
        parts.append(timer.time(lookup_engine._Lookup, "_searched_sources"))
        parts.append(timer.time(lookup_engine._Lookup, "_clock"))

    request = LookupRequest(at=GUARD_AT, public_ip=PUBLIC, dest_ip="203.0.113.250", dest_port=443)
    outcome = _guard_lookup(traffic_site, request, measure)
    timer.report("source clock, empty search")
    assert (outcome.mode, outcome.matches) == ("none", ())
    assert "destination_not_found" in _keys(outcome)
    searched, clock = parts
    assert len(searched.calls) == 1
    assert sorted(call[1] for call in clock.calls) == ["fortigate", "fortigate-2"]
    assert "source_clock_offset" not in _keys(outcome)  # 1 s behind: measured, within the limit
    seconds = searched.seconds + clock.seconds
    assert seconds < 0.2, f"the clock measurement took {seconds:.3f} s"


@pytest.mark.perf
def test_the_dhcp_origins_of_twenty_chains_are_one_fast_query(
    traffic_site: Engine, timer: _Timer
) -> None:
    """3.8: ``usable_dhcp_origins`` for 20 chains in 3 VLANs over ± W of a 7-day lease
    (604,800 s, the FortiGate's own end, D6) at 1 M lines a day, one query per DHCP table
    (D27), under 0.2 s; through the lookup's own ``Attributor.checked``."""
    ips = [f"172.20.{41 + c % 3}.{100 + c // 3}" for c in range(3, 23)]
    origins: list[_Timed] = []
    for warm in (True, False):
        if not warm:
            origins.append(timer.time(attribution, "usable_dhcp_origins"))
        with Session(traffic_site) as session:
            settings = LookupSettings.from_settings(get_settings(session))
            attributor = Attributor(session, settings, untrusted_sources(session), lookup=True)
            chains = [attributor.chain(ip, GUARD_AT) for ip in ips]
            assert all(chain is not None for chain in chains)
            checked = attributor.checked([chain for chain in chains if chain is not None])
            session.rollback()
    timer.report("usable_dhcp_origins")
    (timed,) = origins
    (call,) = timed.calls
    _session, _settings, _untrusted, subnets, low, high = call
    assert sorted(subnets) == ["172.20.41.0/24", "172.20.42.0/24", "172.20.43.0/24"]
    assert high - low >= timedelta(seconds=2 * 604_800)
    assert all("dhcp_sources_overlap" not in [e.key for e in c.explanations] for c in checked)
    assert timed.seconds < 0.2, f"usable_dhcp_origins took {timed.seconds:.3f} s"


# -- the event site -----------------------------------------------------------------------------


@pytest.mark.perf
def test_the_d8_probe_of_twenty_addresses_is_fast(
    event_site: Engine, timer: _Timer, executed: _Executed
) -> None:
    """3.8: the ``fortigate_dhcp_not_converted`` probe of 2.8.3 (the newest DHCP-shaped row per
    IP in ``fg_event`` and ``fg_dhcp``) for 20 addresses with no DHCP row at all (the worst
    case: the scan reads every ``system`` line of the window), under 0.5 s; on the fallback
    window of 2.8.3 (deviation c2), which the query was bound to and which holds the site's
    ``system`` lines of 7 days."""
    probe: list[_Timed] = []

    def measure() -> None:
        probe.append(timer.time(lookup_engine._Lookup, "_newest_dhcp"))
        executed.record(event_site)

    outcome = _guard_lookup(event_site, LookupRequest(at=PROBE_AT, public_ip=PUBLIC), measure)
    timer.report(f"D8 probe, {EVENT_ROWS} system lines a day")
    assert outcome.mode == "candidates"
    assert [m.chain for m in outcome.matches] == [None] * 20
    assert "chain_missing" in _keys(outcome)
    assert "fortigate_dhcp_not_converted" not in _keys(outcome)
    (timed,) = probe
    (call,) = timed.calls
    assert len(call[1]) == 20
    _sql, parameters = executed.one("FROM fg_event", "DISTINCT ON")
    moments = _moments(parameters)
    low, high = min(moments), max(moments)
    assert (low, high) == (PROBE_AT - attribution.UNCONVERTED_WINDOW, PROBE_AT)
    with event_site.connect() as connection:
        lines = connection.execute(
            text(
                "SELECT count(*) FROM fg_event WHERE subtype = 'system' "
                "AND time >= :low AND time <= :high"
            ),
            {"low": low, "high": high},
        ).scalar_one()
    assert lines >= 7 * EVENT_ROWS
    assert timed.seconds < 0.5, f"the D8 probe took {timed.seconds:.3f} s"


@pytest.mark.perf
def test_the_lease_checks_of_twenty_fortigate_chains_are_fast(
    event_site: Engine, timer: _Timer
) -> None:
    """3.8: the round-5 lease checks of 2.7 for 20 ``fg_dhcp`` chains: the per-moment
    ``fg_dhcp`` row of each chain and the post-lease ``fg_event`` query over half a 7-day
    lease, under 0.5 s together."""
    parts: list[_Timed] = []

    def measure() -> None:
        parts.append(timer.time(Attributor, "_row_record"))
        parts.append(timer.time(Attributor, "_unconverted_lines"))

    outcome = _guard_lookup(event_site, LookupRequest(at=FG_AT, public_ip=PUBLIC), measure)
    timer.report(f"round-5 lease checks, {EVENT_ROWS} system lines a day")
    chains = [m.chain for m in outcome.matches]
    assert len(chains) == 20
    for chain in chains:
        assert chain is not None
        assert chain.lease is not None
        assert chain.lease.source_table == "fg_dhcp"
        # Half a lease after the ACK (less a minute): read from the rows, nothing to warn of.
        assert chain.lease.last_dhcp_evidence is not None
        assert chain.at - chain.lease.last_dhcp_evidence > timedelta(days=3.4)
        assert chain.confidence in ("high", "medium")
    records, lines = parts
    assert len(records.calls) == 20
    assert len(lines.calls) == 1
    seconds = records.seconds + lines.seconds
    assert seconds < 0.5, f"the round-5 lease checks took {seconds:.3f} s"


@pytest.mark.perf
def test_the_post_lease_query_of_long_fortigate_leases_is_fast(
    event_site: Engine, timer: _Timer, executed: _Executed
) -> None:
    """3.8 and the manager's review of PR #30, item 4: the ``fg_event`` post-lease query of 20
    ``fg_dhcp`` chains with a 30-day lease, half a lease after their last ACK. An ``fg_dhcp``
    chain's window starts at its last record and has no 7-day fallback (deviation c2), so the
    query reads about 15 days of the site's ``system`` lines; under 0.5 s."""
    lines: list[_Timed] = []

    def measure() -> None:
        lines.append(timer.time(Attributor, "_unconverted_lines"))
        executed.record(event_site)

    outcome = _guard_lookup(event_site, LookupRequest(at=LONG_AT, public_ip=PUBLIC), measure)
    timer.report(f"post-lease query of 30-day leases, {EVENT_ROWS} system lines a day")
    chains = [m.chain for m in outcome.matches]
    assert len(chains) == 20
    half = timedelta(seconds=LONG_LEASE // 2 - 60)
    for chain in chains:
        assert chain is not None
        assert chain.lease is not None
        assert chain.lease.source_table == "fg_dhcp"
        assert chain.lease.last_dhcp_evidence == LONG_AT - half
        assert chain.confidence in ("high", "medium")
    (timed,) = lines
    assert len(timed.calls) == 1
    _sql, parameters = executed.one("FROM fg_event", "extra ->> ")
    moments = _moments(parameters)
    low, high = min(moments), max(moments)
    assert (low, high) == (LONG_AT - half, max(chain.at for chain in chains if chain))
    with event_site.connect() as connection:
        read = connection.execute(
            text(
                "SELECT count(*) FROM fg_event WHERE subtype = 'system' "
                "AND time > :low AND time <= :high"
            ),
            {"low": low, "high": high},
        ).scalar_one()
    assert read >= 14 * EVENT_ROWS
    assert timed.seconds < 0.5, f"the post-lease query took {timed.seconds:.3f} s"


@pytest.mark.perf
def test_the_post_lease_query_of_a_hundred_chains_is_fast(
    event_site: Engine, timer: _Timer
) -> None:
    """3.8: the round-6 post-lease query of 2.7 as one query for 100 chains
    (``lookup.max_candidates``), ``mt_dhcp`` chains whose windows reach the cap (their last
    DHCP record is 150 days old; the cap is the fallback, 604,800 s, deviation c2), by IP or
    MAC, under 0.5 s."""
    lines: list[_Timed] = []

    def measure() -> None:
        lines.append(timer.time(Attributor, "_unconverted_lines"))

    outcome = _guard_lookup(event_site, LookupRequest(at=MT_AT, public_ip=PUBLIC), measure)
    timer.report(f"round-6 post-lease query, {EVENT_ROWS} system lines a day")
    chains = [m.chain for m in outcome.matches]
    assert len(chains) == 100
    for chain in chains:
        assert chain is not None
        assert chain.lease is not None
        assert chain.lease.source_table == "mt_dhcp"
        assert chain.lease.last_dhcp_evidence is not None
        assert chain.at - chain.lease.last_dhcp_evidence > attribution.UNCONVERTED_WINDOW
        assert chain.confidence in ("high", "medium")
    (timed,) = lines
    assert len(timed.calls) == 1
    assert timed.seconds < 0.5, f"the post-lease query took {timed.seconds:.3f} s"


@pytest.mark.perf
def test_the_converted_row_query_of_a_hundred_chains_is_fast(
    event_site: Engine, timer: _Timer, executed: _Executed
) -> None:
    """3.8: the round-7 converted-row query of 2.7 for the MACs of 100 chains on
    ``ix_fg_dhcp_mac_time`` over the 100-day cap (no fallback), at the load generator's
    FortiGate DHCP rate over 400 days (the chains' MACs on the staff Wi-Fi: read, every chain's
    rows returned, and no move under D33's key), with its branch for the chains' addresses on
    ``ix_fg_dhcp_ip_time`` (deviation c2) in the same statement, under 0.2 s. The addresses are
    no planning constant (``ARRAY(SELECT unnest(…))``): on TimescaleDB the 100 days are 100
    one-day chunks, each planned with the statement's lists."""
    moves: list[_Timed] = []

    def measure() -> None:
        moves.append(timer.time(Attributor, "_missed_moves"))
        executed.record(event_site)

    outcome = _guard_lookup(event_site, LookupRequest(at=MT_AT, public_ip=PUBLIC), measure)
    timer.report("round-7 converted-row query")
    assert len(outcome.matches) == 100
    for match in outcome.matches:
        chain = match.chain
        assert chain is not None
        assert chain.lease is not None
        assert chain.lease.last_dhcp_evidence is not None
        assert chain.at - chain.lease.last_dhcp_evidence > attribution.MISSED_MOVE_WINDOW
    assert all(m.chain is not None and m.chain.confidence != "low" for m in outcome.matches)
    (timed,) = moves
    assert len(timed.calls) == 1
    sql, parameters = executed.one("WITH ORDINALITY", "JOIN fg_dhcp")
    executed.stop()
    assert sql.count("JOIN fg_dhcp") == 2
    assert "fg_dhcp.ip = ANY (array((SELECT unnest(" in sql
    moments = _moments(parameters)
    assert min(moments) == MT_AT - attribution.MISSED_MOVE_WINDOW
    with event_site.connect() as connection:
        rows = connection.exec_driver_sql(sql, parameters).all()
    # The MAC branch reads every chain's rows; the address branch (deviation c2) reads the
    # chains' own addresses, which no FortiGate row names (a healthy site returns none).
    assert {n for n, _ip, _interface, _vd, _held in rows} == set(range(1, 101))
    assert {
        (str(ip).startswith("172.20.60."), interface, held) for _n, ip, interface, _vd, held in rows
    } == {(True, "vlan60", False)}
    assert timed.seconds < 0.2, f"the converted-row query took {timed.seconds:.3f} s"
