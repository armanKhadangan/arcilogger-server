"""Performance guards of Arama (contract 3.5; ``poe test-perf``, both CI jobs).

**Data** (``ARCILOGGER_SEARCH_PERF``): ``1`` (default, CI) — the newest day 2,000,000 lines and
29 days of 100,000 before it (4.9 M lines and their typed rows); ``heavy`` — 30 days of
1,000,000 (spec 14's lower bound; on site); ``0`` — skipped. Written with ``INSERT … SELECT
generate_series`` (the technique of ``test_dashboard_api.py``) into ``messages`` and the typed
tables with the same time and raw reference; the mix of docs/INGEST.md (35 % DNS, 27 % traffic,
18 % web, 17 % application control, 1 % FortiGate events, 2 % MikroTik DHCP/ARP/firewall), 150
clients on the example network's VLANs, 5 % of the web rows blocked, a templated key=value text
of about 700 characters per line, plus 4,320 MikroTik events a day in ``mt_event`` (heartbeats
and Wi-Fi, one in a hundred a DHCP offer), and needles: one client with about 13,000 rows over
the 30 days, one rare domain, one rare DNS name, one rare MAC address (DHCP leases and offers)
and one rare free-text token.
``search_counts`` is filled with ``searchcounts.rebuild_stream``; ``VACUUM ANALYZE``; on
TimescaleDB the chunks older than 7 days are compressed before timing (the compression policies
are paused before loading: run against today's date, they would compress whichever chunks
existed when they started).

**Timing:** through the API (``TestClient``), best of three after one warm-up; the cold run is
printed too. The numbers are recorded in docs/ARAMA.md / docs/SEARCH.md ("Performans").

**Plans:** for Q2-Q7 the statement of a whole-range window, as the search sends it (on
TimescaleDB split at each table's compression horizon, ``sql.page_statement``), is explained
(``EXPLAIN (FORMAT JSON)``, each scan's table resolved from its partition or chunk): no ``Seq
Scan`` and no
primary-key range scan of a high-volume table (``messages``, ``fg_traffic``, ``fg_web``,
``fg_dns``, ``fg_app``) — a compressed chunk's ``DecompressChunk`` is allowed — and the indexes
the query is meant to use appear (Q6: ``mt_dhcp``'s MAC index and ``mt_event``'s event index).
Q3's /24 holds about a fifth of the clients, so reading the primary key backwards until the page
is full is the cheapest plan there: only a ``Seq Scan`` is refused. Q10 checks that every
exported row has its typed and ``messages`` columns.
"""

from __future__ import annotations

import csv
import io
import os
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from functools import partial
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.app import create_app
from arcilogger_api.deps import AppState
from arcilogger_api.search import parse_query, sql
from arcilogger_api.search.cursor import bounds
from arcilogger_api.search.fields import SiteNetwork
from arcilogger_api.search.planner import plan_query
from arcilogger_api.search.stats import SiteLabels, run_stats
from arcilogger_api.search.testing import sign_in
from arcilogger_api.search.windows import compression_horizons
from arcilogger_core.config import Config
from arcilogger_core.timeutil import day_window
from arcilogger_db.models import Message
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_db.settings import set_setting
from arcilogger_db.testing import pause_compression_jobs

TIER = os.environ.get("ARCILOGGER_SEARCH_PERF", "1").strip().lower()
pytestmark = [
    pytest.mark.perf,
    pytest.mark.skipif(TIER == "0", reason="ARCILOGGER_SEARCH_PERF=0"),
]

API = "/api/v1/search"
#: The newest day of the data (a Tuesday) and how many days there are.
NEWEST = date(2026, 10, 20)
DAYS = 30
LINES = {"1": (2_000_000, 100_000), "heavy": (1_000_000, 1_000_000)}
NEEDLE = "172.20.41.250"
NEEDLE_PER_DAY = 433  # about 13,000 rows over 30 days
RARE_DOMAIN = "nadir-alan.example.com"
RARE_MAC = "00:00:5e:00:53:fe"
RARE_TOKEN = "zzqnadirjeton"
HIGH_VOLUME = ("messages", "fg_traffic", "fg_web", "fg_dns", "fg_app")
#: MikroTik events a day (one every 20 s), with raw line numbers after the needles'.
EVENTS_PER_DAY = 4_320
EVENT_LINES = 1_000
#: Budgets in seconds (contract 3.5).
BROWSE = 0.5
DAY_ROWS = 2.0
MONTH_ROWS = 10.0
STATS_ROLLUP = 0.5
STATS_DIRECT = 2.0
EXPORT = 10.0
#: A scan may run over the request budget by this much.
OVERRUN = 0.5

#: The FortiGate-like key=value body of a line (about 700 characters with the varying part).
_PADDING = (
    'devname="FIREWALL-01" devid="FGT0000000000000" tz="+0300" vd="root" srcintf="port2" '
    'srcintfrole="lan" dstintf="wan1" dstintfrole="wan" srccountry="Reserved" '
    'dstcountry="Reserved" policytype="policy" poluuid="00000000-0000-0000-0000-000000000000" '
    'policyname="Internet" service="HTTPS" trandisp="snat" transip=100.64.12.9 duration=17 '
    'sentbyte=2048 rcvdbyte=65536 sentpkt=12 rcvdpkt=48 appcat="unscanned" crscore=0 '
    'craction=0 crlevel="low" eventtype="dns-query" level="information" msg="Sentetik test '
    'satırı, yalnızca performans ölçümü için üretildi; doğrulanacak bir şey içermez" '
    'osname="Windows" mastersrcmac="00:00:5e:00:53:00" srcmac="00:00:5e:00:53:00" '
    'srcserver=0 utmaction="allow" countweb=0 countdns=1 countapp=0'
)
_CLIENT = "CAST('172.20.' || (ARRAY[41,42,43,60])[c % 4 + 1] || '.' || (10 + c) AS inet)"
_REMOTE = "CAST('203.0.113.' || (n % 200 + 10) AS inet)"
_SERIES = (
    "WITH g AS (SELECT n, CAST(:start AS timestamptz) + n * CAST(:step AS interval) AS t, "
    "n % 100 AS k, (n * 7919) % 150 AS c "
    "FROM generate_series(CAST(1 AS bigint), CAST(:rows AS bigint)) AS n) "
)
_KIND_TYPE = (
    "CASE WHEN k < 35 THEN 'utm' WHEN k < 62 THEN 'traffic' WHEN k < 80 THEN 'utm' "
    "WHEN k < 97 THEN 'utm' WHEN k = 97 THEN 'event' "
    "ELSE (ARRAY['dhcp','arp','fw'])[n % 3 + 1] END"
)
_KIND_SUBTYPE = (
    "CASE WHEN k < 35 THEN 'dns' WHEN k < 62 THEN 'forward' WHEN k < 80 THEN 'webfilter' "
    "WHEN k < 97 THEN 'app-ctrl' WHEN k = 97 THEN 'system' ELSE NULL END"
)
_SOURCE = "CASE WHEN k >= 98 THEN 'core-r1' ELSE 'fortigate' END"
_SENDER = "CAST(CASE WHEN k >= 98 THEN '172.20.10.1' ELSE '172.31.255.1' END AS inet)"
_COMMON = "t, t, {source}, {sender}, :day, n, n * 750"
_COLUMNS = "time, time_received, source, sender, raw_day, raw_line, raw_offset"
MESSAGES_SQL = text(
    _SERIES  # noqa: S608 - fixed SQL fragments
    + f"INSERT INTO messages ({_COLUMNS}, severity, facility, host, type, subtype, text) "
    + f"SELECT {_COMMON.format(source=_SOURCE, sender=_SENDER)}, 6, 23, 'FIREWALL-01', "
    + f"{_KIND_TYPE}, {_KIND_SUBTYPE}, "
    + "'date=' || to_char(t AT TIME ZONE 'Europe/Istanbul', 'YYYY-MM-DD') || ' time=' || "
    + "to_char(t AT TIME ZONE 'Europe/Istanbul', 'HH24:MI:SS') || ' logid=\"' || "
    + "lpad(CAST(k AS text), 10, '0') || '\" srcip=' || host("
    + _CLIENT
    + ") || ' srcport=' || "
    + "(40000 + n % 20000) || ' dstip=203.0.113.' || (n % 200 + 10) || ' sessionid=' || n || ' "
    + "qname=\"host-' || (n % 997) || '.example.com\" ' || :padding FROM g"
)
FG = "t, t, 'fortigate', CAST('172.31.255.1' AS inet), :day, n, n * 750"
MT = "t, t, 'core-r1', CAST('172.20.10.1' AS inet), :day, n, n * 750"
TYPED_SQL = {
    "fg_dns": (
        f"INSERT INTO fg_dns ({_COLUMNS}, srcip, srcport, dstip, dstport, qname, qtype, action) "  # noqa: S608 - fixed SQL fragments
        f"SELECT {FG}, {_CLIENT}, 40000 + n % 20000, CAST('172.20.20.53' AS inet), 53, "
        "'host-' || (n % 997) || '.example.com', 'A', 'pass' FROM g WHERE k < 35"
    ),
    "fg_traffic": (
        f"INSERT INTO fg_traffic ({_COLUMNS}, srcip, srcport, dstip, dstport, proto, action, "  # noqa: S608 - fixed SQL fragments
        "transip, transport, sessionid, policyid) "
        f"SELECT {FG}, {_CLIENT}, 40000 + n % 20000, {_REMOTE}, "
        "CASE WHEN n % 50 = 37 THEN 3389 ELSE 443 END, 6, "
        "CASE WHEN n % 20 = 17 THEN 'deny' ELSE 'accept' END, CAST('100.64.12.9' AS inet), "
        "40000 + n % 20000, n, 12 FROM g WHERE k >= 35 AND k < 62"
    ),
    "fg_web": (
        f"INSERT INTO fg_web ({_COLUMNS}, srcip, srcport, dstip, dstport, hostname, url, "  # noqa: S608 - fixed SQL fragments
        "catdesc, action, sessionid) "
        f"SELECT {FG}, {_CLIENT}, 40000 + n % 20000, {_REMOTE}, "
        "443, 'host-' || (n % 997) || '.example.com', '/sayfa/' || (n % 101), 'Sentetik: Haber', "
        "CASE WHEN n % 20 = 0 THEN 'blocked' ELSE 'passthrough' END, n FROM g "
        "WHERE k >= 62 AND k < 80"
    ),
    "fg_app": (
        f"INSERT INTO fg_app ({_COLUMNS}, srcip, srcport, dstip, dstport, proto, app, appcat, "  # noqa: S608 - fixed SQL fragments
        "action, hostname, sessionid) "
        f"SELECT {FG}, {_CLIENT}, 40000 + n % 20000, {_REMOTE}, "
        "443, 6, 'Example.Docs', 'General.Interest', 'pass', 'host-' || (n % 997) || "
        "'.example.com', n FROM g WHERE k >= 80 AND k < 97"
    ),
    "fg_event": (
        f"INSERT INTO fg_event ({_COLUMNS}, subtype, level, action, srcip) "  # noqa: S608 - fixed SQL fragments
        f"SELECT {FG}, 'system', 'notice', 'perf-stats', {_CLIENT} FROM g WHERE k = 97"
    ),
    "mt_dhcp": (
        f"INSERT INTO mt_dhcp ({_COLUMNS}, server, vlan, action, ip, mac) "  # noqa: S608 - fixed SQL fragments
        f"SELECT {MT}, 'dhcp41', 41, 'assigned', {_CLIENT}, "
        "CAST('00:00:5e:00:' || lpad(to_hex(c % 200), 2, '0') || ':' || "
        "lpad(to_hex(n % 256), 2, '0') AS macaddr) FROM g WHERE k >= 98 AND n % 3 = 0"
    ),
    "mt_arp": (
        f"INSERT INTO mt_arp ({_COLUMNS}, ip, mac, interface) "  # noqa: S608 - fixed SQL fragments
        f"SELECT {MT}, {_CLIENT}, CAST('00:00:5e:00:53:' || lpad(to_hex(c % 200), 2, '0') "
        "AS macaddr), 'vlan41' FROM g WHERE k >= 98 AND n % 3 = 1"
    ),
    "mt_fw": (
        f"INSERT INTO mt_fw ({_COLUMNS}, chain, src, dst, proto, sport, dport) "  # noqa: S608 - fixed SQL fragments
        f"SELECT {MT}, 'forward', {_CLIENT}, CAST('198.51.100.77' AS inet), 'TCP', "
        "40000 + n % 20000, 443 FROM g WHERE k >= 98 AND n % 3 = 2"
    ),
}


#: Heartbeats and Wi-Fi of the router, one in a hundred a DHCP offer; MACs of the documentation
#: range, never ``RARE_MAC``.
_EVENT = (
    "CASE WHEN n % 100 = 0 THEN 'dhcp_offering' WHEN n % 3 = 0 THEN 'heartbeat' "
    "WHEN n % 2 = 0 THEN 'wifi_connect' ELSE 'wifi_disconnect' END"
)
_EVENT_SERIES = (
    "WITH g AS (SELECT n, CAST(:start AS timestamptz) + n * CAST(:step AS interval) AS t, "  # noqa: S608 - fixed SQL fragments
    "CAST(:first AS bigint) + n AS line, (n * 7919) % 150 AS c "
    "FROM generate_series(CAST(1 AS bigint), CAST(:rows AS bigint)) AS n), "
    f"e AS (SELECT g.*, {_EVENT} AS event FROM g) "
)
_EVENT_COMMON = "t, t, 'core-r1', CAST('172.20.10.1' AS inet), :day, line, line * 750"
EVENT_MESSAGES_SQL = text(
    _EVENT_SERIES  # noqa: S608 - fixed SQL fragments
    + f"INSERT INTO messages ({_COLUMNS}, severity, facility, host, type, text) "
    + f"SELECT {_EVENT_COMMON}, 6, 23, 'core-r1', "
    + "CASE WHEN event = 'dhcp_offering' THEN 'dhcp' WHEN event = 'heartbeat' THEN 'heartbeat' "
    + "ELSE 'wifi' END, 'mikrotik ' || event || ' ' || host("
    + _CLIENT
    + ") FROM e"
)
EVENT_SQL = text(
    _EVENT_SERIES  # noqa: S608 - fixed SQL fragments
    + f"INSERT INTO mt_event ({_COLUMNS}, event, interface, mac, ip) "
    + f"SELECT {_EVENT_COMMON}, event, 'wlan1', "
    + "CAST('00:00:5e:00:53:' || lpad(to_hex(c), 2, '0') AS macaddr), "
    + _CLIENT
    + " FROM e"
)


def _step(span: timedelta, rows: int) -> str:
    return f"{span.total_seconds() * 1_000_000 / (rows + 1):.3f} microseconds"


def _load_day(connection: Any, day: date, rows: int) -> None:
    start, end = day_window(day)
    params = {"start": start, "step": _step(end - start, rows), "rows": rows, "day": day}
    connection.execute(MESSAGES_SQL, {**params, "padding": _PADDING})
    for statement in TYPED_SQL.values():
        connection.execute(text(_SERIES + statement), params)
    events = {
        "start": start,
        "step": _step(end - start, EVENTS_PER_DAY),
        "rows": EVENTS_PER_DAY,
        "day": day,
        "first": rows + EVENT_LINES,
    }
    connection.execute(EVENT_MESSAGES_SQL, events)
    connection.execute(EVENT_SQL, events)


_ROW = (
    "VALUES (:time, :time_received, :source, CAST(:sender AS inet), :raw_day, :raw_line, "
    ":raw_offset"
)


def _needles(connection: Any, day: date, rows: int, newest: bool) -> None:
    """The needle client, the rare domain, DNS name, MAC address and free-text token."""
    start, end = day_window(day)
    first = rows + 1
    lines: list[tuple[str | None, dict[str, Any]]] = []
    step = (end - start) / (NEEDLE_PER_DAY + 1)
    tables = ("fg_web", "fg_traffic", "fg_dns", "fg_app")
    for index in range(NEEDLE_PER_DAY):
        extra: dict[str, Any] = {"srcip": NEEDLE}
        if tables[index % 4] == "fg_web":
            extra |= {
                "hostname": "www.example.com",
                "action": "blocked" if index // 4 % 2 else "passthrough",
            }
        lines.append((tables[index % 4], {"time": start + (index + 1) * step, **extra}))
    rare_step = (end - start) / 3
    for index in range(2):
        moment = start + (index + 1) * rare_step + timedelta(seconds=17)
        lines.append(
            (
                "fg_web",
                {
                    "time": moment,
                    "srcip": "172.20.42.77",
                    "hostname": RARE_DOMAIN,
                    "action": "passthrough",
                },
            )
        )
        lines.append(
            (
                "fg_dns",
                {
                    "time": moment + timedelta(seconds=1),
                    "srcip": "172.20.42.77",
                    "qname": RARE_DOMAIN,
                },
            )
        )
        lines.append(
            (
                "mt_dhcp",
                {
                    "time": moment + timedelta(seconds=2),
                    "ip": "172.20.41.77",
                    "mac": RARE_MAC,
                    "server": "dhcp41",
                    "action": "assigned",
                },
            )
        )
        lines.append(
            (
                "mt_event",
                {
                    "time": moment + timedelta(seconds=3),
                    "event": "dhcp_offering",
                    "ip": "172.20.41.77",
                    "mac": RARE_MAC,
                    "interface": "vlan41",
                },
            )
        )
    if newest:
        for index in range(10):
            lines.append(
                (None, {"time": start + timedelta(hours=2 * index + 1, seconds=3), "token": True})
            )
    for offset, (table, values) in enumerate(lines):
        number = first + offset
        moment = values.pop("time")
        token = values.pop("token", False)
        mikrotik = table in ("mt_dhcp", "mt_event")
        common = {
            "time": moment,
            "time_received": moment,
            "source": "core-r1" if mikrotik else "fortigate",
            "sender": "172.20.10.1" if mikrotik else "172.31.255.1",
            "raw_day": day,
            "raw_line": number,
            "raw_offset": number * 750,
        }
        kind = {
            "fg_web": ("utm", "webfilter"),
            "fg_traffic": ("traffic", "forward"),
            "fg_dns": ("utm", "dns"),
            "fg_app": ("utm", "app-ctrl"),
            "mt_dhcp": ("dhcp", None),
            "mt_event": ("dhcp", None),
            None: ("syslog", None),
        }[table]
        body = f"needle {RARE_TOKEN} " if token else "needle "
        connection.execute(
            text(
                f"INSERT INTO messages ({_COLUMNS}, type, subtype, text) "
                f"{_ROW}, :type, :subtype, :text)"
            ),
            {**common, "type": kind[0], "subtype": kind[1], "text": body + _PADDING},
        )
        if table is not None:
            names = ", ".join(values)
            binds = ", ".join(f":{name}" for name in values)
            connection.execute(
                text(f"INSERT INTO {table} ({_COLUMNS}, {names}) {_ROW}, {binds})"),
                {**common, **values},
            )


def _timescale(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
            )
        )


def _compress_old_chunks(engine: Engine, before: datetime) -> int:
    compressed = 0
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        for table in ("messages", *TYPED_SQL, "mt_event"):
            compressed += len(
                connection.execute(
                    text(
                        "SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks("
                        "CAST(:table AS regclass), older_than => CAST(:before AS timestamptz)) AS c"
                    ),
                    {"table": table, "before": before},
                ).all()
            )
    return compressed


@dataclass
class Site:
    client: TestClient
    app: FastAPI
    engine: Engine
    factory: sessionmaker[Session]
    config: Config
    start: datetime
    end: datetime
    newest: tuple[datetime, datetime]


@pytest.fixture
def site(tmp_path: Any, db_engine: Engine) -> Iterator[Site]:
    newest_rows, other_rows = LINES.get(TIER, LINES["1"])
    days = [NEWEST - timedelta(days=offset) for offset in range(DAYS)]
    began = time.perf_counter()
    timescale = _timescale(db_engine)
    if timescale:
        pause_compression_jobs(db_engine)
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=NEWEST, months_back=2)
    for day in days:
        rows = newest_rows if day == NEWEST else other_rows
        with db_engine.begin() as connection:
            _load_day(connection, day, rows)
            _needles(connection, day, rows, day == NEWEST)
    loaded = time.perf_counter() - began
    # Statistics first: without them the recount's join of fresh tables is planned as a nested
    # loop over millions of rows.
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("ANALYZE"))
    with db_engine.begin() as connection:
        for day in days:
            for source in ("fortigate", "core-r1"):
                rebuild_stream(connection, source, day)
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE"))
    compressed = 0
    if timescale:
        compressed = _compress_old_chunks(db_engine, day_window(NEWEST - timedelta(days=7))[0])
        with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
            connection.execute(text("ANALYZE"))
    with db_engine.connect() as connection:
        total = connection.scalar(select(Message.id).order_by(Message.id.desc()).limit(1))
    print(
        f"\nsearch perf data (tier {TIER}): {total} messages rows, loaded in {loaded:.0f} s, "
        f"ready in {time.perf_counter() - began:.0f} s, {compressed} chunks compressed"
    )
    config = Config.model_validate(
        {
            "site_name": "Örnek A.Ş. (arama ölçümü)",
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
                {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
            ],
        }
    )
    app = create_app(config, db_engine, audit_flush_seconds=None)
    state: AppState = app.state.arci
    factory = state.session_factory()
    with factory() as session:
        set_setting(session, "search.runs_per_minute", 600, updated_by="perf")
        set_setting(session, "search.export_max_per_hour", 100, updated_by="perf")
        session.commit()
    with TestClient(app, base_url="http://testserver") as client:
        who = sign_in(client, factory)
        with factory() as session:
            session.execute(
                text(
                    "UPDATE user_sessions SET reauthenticated_at = now() + interval '1 hour' "
                    "WHERE user_id = :u"
                ),
                {"u": who.user_id},
            )
            session.commit()
        start, _ = day_window(days[-1])
        _, end = day_window(NEWEST)
        yield Site(client, app, db_engine, factory, config, start, end, day_window(NEWEST))


# -- measuring ------------------------------------------------------------------------------------


def measure(call: Callable[[], Any]) -> tuple[float, float, Any]:
    """Cold run, then the best of three after it (the cold run is the warm-up)."""
    began = time.perf_counter()
    result = call()
    cold = time.perf_counter() - began
    best = float("inf")
    for _ in range(3):
        began = time.perf_counter()
        result = call()
        best = min(best, time.perf_counter() - began)
    return cold, best, result


def search(site: Site, q: str, start: datetime, end: datetime, limit: int = 100) -> dict[str, Any]:
    response = site.client.post(
        API, json={"q": q, "from": start.isoformat(), "to": end.isoformat(), "limit": limit}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


@dataclass(frozen=True)
class Scan:
    """One scan of a plan: its table (a partition's or chunk's parent table) and index."""

    node: str
    table: str | None
    index: str | None
    primary: bool
    backward: bool
    #: Below a TimescaleDB ``DecompressChunk`` (a compressed chunk).
    compressed: bool


_SCANS = frozenset(
    {"Seq Scan", "Index Scan", "Index Only Scan", "Bitmap Heap Scan", "Bitmap Index Scan"}
)
_TABLE = text(
    "SELECT coalesce((SELECT p.relname FROM pg_inherits i JOIN pg_class p ON p.oid = i.inhparent "
    "WHERE i.inhrelid = c.oid LIMIT 1), c.relname) FROM pg_class c "
    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :schema AND c.relname = :name"
)
_PRIMARY = text(
    "SELECT x.indisprimary FROM pg_index x JOIN pg_class c ON c.oid = x.indexrelid "
    "JOIN pg_namespace n ON n.oid = c.relnamespace WHERE n.nspname = :schema AND c.relname = :name"
)


def _scans(connection: Any, node: dict[str, Any], compressed: bool) -> Iterator[Scan]:
    compressed = compressed or node.get("Custom Plan Provider") == "DecompressChunk"
    if node["Node Type"] in _SCANS:
        schema = node.get("Schema", "public")
        relation, index = node.get("Relation Name"), node.get("Index Name")
        table = None
        if relation is not None:
            table = connection.scalar(_TABLE, {"schema": schema, "name": relation})
        primary = index is not None and bool(
            connection.scalar(_PRIMARY, {"schema": schema, "name": index})
        )
        backward = node.get("Scan Direction") == "Backward"
        yield Scan(node["Node Type"], table, index, primary, backward, compressed)
    for child in node.get("Plans", []):
        yield from _scans(connection, child, compressed)


def explain(site: Site, q: str, start: datetime, end: datetime) -> list[Scan]:
    """The scans of the plan of the whole-range window of ``q``."""
    with site.factory() as session:
        plan = plan_query(parse_query(q), SiteNetwork.load(session, site.config))
        horizons = compression_horizons(session, 10.0)
    statement = sql.page_statement(plan, start, bounds(None, end, plan.ranks), 101, horizons)
    compiled = statement.compile(
        dialect=site.engine.dialect, compile_kwargs={"render_postcompile": True}
    )
    with site.engine.connect() as connection:
        found: Any = connection.exec_driver_sql(
            "EXPLAIN (FORMAT JSON, VERBOSE) " + compiled.string, compiled.params
        ).scalar()
        return list(_scans(connection, found[0]["Plan"], False))


def check_plan(scans: list[Scan], *indexes: str, primary: bool = False) -> None:
    """No ``Seq Scan`` (nor, unless ``primary``, primary-key range scan) of an uncompressed
    high-volume table; every one of ``indexes`` (patterns) is used."""
    bad = [
        scan
        for scan in scans
        if scan.table in HIGH_VOLUME
        and not scan.compressed
        and (scan.node == "Seq Scan" or (scan.primary and not primary))
    ]
    assert not bad, scans
    for index in indexes:
        assert any(scan.index and re.search(index, scan.index) for scan in scans), (index, scans)


def used(scans: list[Scan]) -> str:
    """The indexes of the high-volume tables' scans (printed with the numbers)."""
    names = sorted({f"{s.table}: {s.index or s.node}" for s in scans if s.table in HIGH_VOLUME})
    return ", ".join(names)


def report(name: str, q: str, cold: float, best: float, budget: float, extra: str = "") -> None:
    print(
        f"{name}: {q!r} cold {cold * 1000:.0f} ms, best {best * 1000:.0f} ms "
        f"(budget {budget * 1000:.0f} ms){extra}"
    )


# -- the guards -----------------------------------------------------------------------------------


def test_search_performance(site: Site) -> None:
    day_start, day_end = site.newest
    month = (site.start, site.end)

    # Q1 browse, first page: a backward scan of the primary key of messages.
    for label, (start, end) in (("1 d", site.newest), ("30 d", month)):
        cold, best, page = measure(partial(search, site, "", start, end))
        report(f"Q1 browse {label}", "", cold, best, BROWSE)
        assert len(page["items"]) == 100
        assert best < BROWSE
    q1 = explain(site, "", day_end - timedelta(hours=1), day_end)
    assert any(s.table == "messages" and s.primary and s.backward for s in q1), q1

    # Q2 the needle client: 1 day and 30 days.
    for label, (start, end), budget in (
        ("1 d", site.newest, DAY_ROWS),
        ("30 d", month, MONTH_ROWS),
    ):
        cold, best, page = measure(partial(search, site, f"srcip:{NEEDLE}", start, end))
        report(f"Q2 srcip {label}", f"srcip:{NEEDLE}", cold, best, budget)
        assert len(page["items"]) == 100
        assert best < budget
    check_plan(explain(site, f"srcip:{NEEDLE}", *month), r"srcip_time")

    # Q3 a /24 for a day.
    cold, best, page = measure(lambda: search(site, "srcip:172.20.41.0/24", day_start, day_end))
    report("Q3 srcip /24 1 d", "srcip:172.20.41.0/24", cold, best, DAY_ROWS)
    assert page["items"]
    assert best < DAY_ROWS
    # A /24 holds about a fifth of the clients: reading the primary key backwards until the page
    # is full is the cheapest plan, so only a sequential scan is refused here.
    q3 = explain(site, "srcip:172.20.41.0/24", day_start, day_end)
    print(f"Q3 plan: {used(q3)}")
    check_plan(q3, primary=True)

    # Q4-Q7 over 30 days.
    for name, q, indexes in (
        (
            "Q4",
            f"srcip:{NEEDLE} type:web action:blocked",
            (r"srcip_time",),
        ),
        # a partition's own index is named after the expression: ``…_reverse_time_idx``
        ("Q5", f"domain:{RARE_DOMAIN} type:web", (r"rhost_time|reverse_time",)),
        # ``mt_event`` has no MAC index: its DHCP events are found by the event index
        ("Q6", f"type:dhcp mac:{RARE_MAC}", (r"mac_time", r"mt_event\w*_event_time")),
        ("Q7", f"qname:{RARE_DOMAIN}", (r"qname_time",)),
    ):
        cold, best, page = measure(partial(search, site, q, *month))
        report(f"{name} 30 d", q, cold, best, MONTH_ROWS)
        assert page["items"], q
        assert page["scan"]["complete"] is True
        assert best < MONTH_ROWS
        check_plan(explain(site, q, *month), *indexes)
    q6 = {item["table"] for item in search(site, f"type:dhcp mac:{RARE_MAC}", *month)["items"]}
    assert q6 == {"mt_dhcp", "mt_event"}

    # Q8 statistics from the summary table, equal to direct counting.
    q8 = "type:web action:blocked department:Finans"
    page = search(site, q8, *month)
    cold, best, rolled = measure(
        lambda: site.client.post(f"{API}/stats", json={"search_id": page["search_id"]}).json()
    )
    report("Q8 stats 30 d", q8, cold, best, STATS_ROLLUP, f", mode {rolled['mode']}")
    assert rolled["mode"] in ("rollup", "hybrid")
    assert best < STATS_ROLLUP
    with site.factory() as session:
        session.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY"))
        network = SiteNetwork.load(session, site.config)
        direct = run_stats(
            session,
            plan_query(parse_query(q8), network),
            *month,
            labels=SiteLabels(network),
            budget_seconds=600,
            sample_rows=10_000_000,
            direct=True,
        )
        session.rollback()
    assert direct["complete"] is True
    assert rolled["total"] == direct["total"]
    assert [b["count"] for b in rolled["histogram"]["buckets"]] == [
        b["count"] for b in direct["histogram"]["buckets"]
    ]

    # Q9 statistics of the needle, counted directly, for a day.
    page = search(site, f"srcip:{NEEDLE}", day_start, day_end)
    cold, best, counted = measure(
        lambda: site.client.post(f"{API}/stats", json={"search_id": page["search_id"]}).json()
    )
    report("Q9 stats 1 d direct", f"srcip:{NEEDLE}", cold, best, STATS_DIRECT)
    assert counted["mode"] == "direct"
    assert counted["total"]["value"] == NEEDLE_PER_DAY
    assert best < STATS_DIRECT

    # Q10 export 10,000 rows of the needle over 30 days.
    with site.factory() as session:
        set_setting(session, "search.export_max_rows", 10_000, updated_by="perf")
        session.commit()
    page = search(site, f"srcip:{NEEDLE}", *month)
    cold, best, response = measure(
        lambda: site.client.post(
            f"{API}/export", json={"search_id": page["search_id"], "format": "csv"}
        )
    )
    report("Q10 export 10,000 rows", f"srcip:{NEEDLE}", cold, best, EXPORT)
    assert response.status_code == 200, response.text
    assert response.headers.get("X-Arci-Export-Truncated") == "rows"
    assert response.content.count(b"\r\n") == 10_001
    exported = list(
        csv.DictReader(io.StringIO(response.content.decode("utf-8-sig"), newline=""), delimiter=";")
    )
    assert len(exported) == 10_000
    # Every row has its typed and its ``messages`` columns: none was written half filled.
    assert all(row["Kaynak IP"] == NEEDLE for row in exported)
    assert all(row["Metin"].startswith("needle ") for row in exported)
    assert best < EXPORT

    # S1-S3 scans stop at the budget and say honestly how far they got.
    budget = 4.0
    for name, q, (start, end), expected in (
        ("S1", RARE_TOKEN, site.newest, 10),
        ("S2", "dstport:3389 action:deny", site.newest, None),
        ("S3", f"ip:{NEEDLE}", month, None),
    ):
        cold, best, page = measure(partial(search, site, q, start, end))
        complete = page["scan"]["complete"]
        report(
            f"{name} scan",
            q,
            cold,
            best,
            budget + OVERRUN,
            f", complete {complete}, {len(page['items'])} rows",
        )
        assert best <= budget + OVERRUN
        if expected is not None and complete:
            assert len(page["items"]) == expected
        if not complete:
            assert page["next_cursor"] is not None
