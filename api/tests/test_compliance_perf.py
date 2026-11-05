"""Performance guard of Uyum Durumu (Wave 4 contract 3; ``poe test-perf``, both CI jobs): the ten
cards within one second on the perf data of contract 3.

**Data**, written with ``INSERT … SELECT generate_series`` (the technique of
``test_dashboard_api.py``), then ``VACUUM ANALYZE``: ``fg_web`` 300,000 and ``fg_dns`` 500,000
rows on the newest site day and 20,000 / 35,000 on each of the 7 days before, from two FortiGate
sources, with their ``messages`` rows; ``search_counts`` rebuilt with
``searchcounts.rebuild_stream`` as ``test_search_perf.py`` does; 200 active alerts over the ten
cards' rules; ``job_runs`` 17,000 rows a day for 180 days (owner question Q21); today's and
yesterday's ``state/clock`` files (24 samples each); 30 sealed days in the vault (sealed by the
real code, ``arcilogger_worker.vaulttesting``); the collector's status file with 20 sources.

**Timing:** :func:`arcilogger_api.compliance.cards.compute_status` (what the page and the PDF
compute), best of three after one cold run, which is printed too.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.admin_channel import Channel
from arcilogger_api.compliance.cards import compute_status
from arcilogger_api.compliance.models import ComplianceStatus
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ, day_window, isoformat_site
from arcilogger_db.models import Alert, Source
from arcilogger_db.partitions import ensure_partitions
from arcilogger_db.searchcounts import rebuild_stream
from arcilogger_db.testing import pause_compression_jobs
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

pytestmark = pytest.mark.perf

#: The newest site day (a Tuesday) and the cards' "now" late in it.
NEWEST = date(2026, 10, 27)
NOW = datetime(2026, 10, 27, 23, 30, tzinfo=SITE_TZ).astimezone(UTC)
#: Rows per site day: ``(fg_web, fg_dns)`` on the newest day and on each of the 7 before it.
NEWEST_ROWS = (300_000, 500_000)
OLDER_ROWS = (20_000, 35_000)
OLDER_DAYS = 7
FORTIGATES = ("fortigate", "fortigate-2")
SENDERS = {"fortigate": "172.31.255.1", "fortigate-2": "172.31.255.2"}
JOB_RUNS_PER_DAY = 17_000
JOB_RUN_DAYS = 180
ACTIVE_ALERTS = 200
SEALED_DAYS = 30
STATUS_SOURCES = 20
#: The budget of contract 3 (seconds).
BUDGET = 1.0

_COLUMNS = "time, time_received, source, sender, raw_day, raw_line, raw_offset"
#: Rows of one table and day: alternately from the two FortiGate sources, raw line numbers after
#: ``:first`` (web filter lines first, DNS filter lines after them).
_SERIES = (
    "WITH g AS (SELECT n, CAST(:start AS timestamptz) + n * CAST(:step AS interval) AS t, "
    "CASE WHEN n % 2 = 0 THEN 'fortigate' ELSE 'fortigate-2' END AS src, "
    "CAST(CASE WHEN n % 2 = 0 THEN '172.31.255.1' ELSE '172.31.255.2' END AS inet) AS snd, "
    "CAST(:first AS bigint) + n AS line, (n * 7919) % 150 AS c "
    "FROM generate_series(CAST(1 AS bigint), CAST(:rows AS bigint)) AS n) "
)
_COMMON = "t, t, src, snd, :day, line, line * 600"
_CLIENT = "CAST('172.20.' || (ARRAY[41,42,43,60])[c % 4 + 1] || '.' || (10 + c) AS inet)"
MESSAGES_SQL = text(
    _SERIES  # noqa: S608 - fixed SQL fragments
    + f"INSERT INTO messages ({_COLUMNS}, severity, facility, host, type, subtype, text) "
    + f"SELECT {_COMMON}, 6, 23, 'FIREWALL-01', 'utm', :subtype, "
    + '\'logid="0000000000" type="utm" subtype="\' || :subtype || \'" n=\' || n FROM g'
)
TYPED_SQL = {
    "webfilter": text(
        _SERIES  # noqa: S608 - fixed SQL fragments
        + f"INSERT INTO fg_web ({_COLUMNS}, srcip, srcport, dstip, dstport, hostname, url, "
        + "catdesc, action, profile, sessionid) "
        + f"SELECT {_COMMON}, {_CLIENT}, 40000 + n % 20000, "
        + "CAST('203.0.113.' || (n % 200 + 10) AS inet), 443, "
        + "'host-' || (n % 997) || '.example.com', '/sayfa/' || (n % 101), 'Sentetik: Haber', "
        + "CASE WHEN n % 20 = 0 THEN 'blocked' ELSE 'passthrough' END, "
        + "(ARRAY['default','guest','strict'])[n % 3 + 1], n FROM g"
    ),
    "dns": text(
        _SERIES  # noqa: S608 - fixed SQL fragments
        + f"INSERT INTO fg_dns ({_COLUMNS}, srcip, srcport, dstip, dstport, qname, qtype, "
        + "action) "
        + f"SELECT {_COMMON}, {_CLIENT}, 40000 + n % 20000, CAST('172.20.20.53' AS inet), 53, "
        + "'host-' || (n % 997) || '.example.com', 'A', 'pass' FROM g"
    ),
}
JOB_RUNS_SQL = text(
    "INSERT INTO job_runs (job, started_at, finished_at, status) "
    "SELECT (ARRAY['alerts.system','alerts.r15','correlation','searchcounts.verify',"
    "'usage.rollup','alerts.mail','reports.run','evidence.purge'])[n % 8 + 1], "
    "CAST(:start AS timestamptz) + n * CAST(:step AS interval), "
    "CAST(:start AS timestamptz) + n * CAST(:step AS interval) + interval '40 milliseconds', "
    "CASE WHEN n % 500 = 0 THEN 'failed' ELSE 'ok' END "
    "FROM generate_series(CAST(1 AS bigint), CAST(:rows AS bigint)) AS n"
)
#: The dedup key of every card's alerts (contract 2.3.2); a key without a suffix is completed.
ALERT_KEYS = (
    ("R15", "R15:switch-{n}"),
    ("R20-seal", "R20-seal:{yesterday}"),
    ("R20-verify", "R20-verify"),
    ("R20-retention", "R20-retention:{n}"),
    ("R20-credit", "R20-credit"),
    ("R20-clock", "R20-clock:{n}"),
    ("R20-filter", "R20-filter:none"),
    ("R20-lease", "R20-lease:cause-{n}"),
    ("R20-replica", "R20-replica:{n}"),
    ("R20-backup", "R20-backup:{n}"),
)


def _step(start: datetime, end: datetime, rows: int) -> str:
    return f"{(end - start).total_seconds() * 1_000_000 / (rows + 1):.3f} microseconds"


def _load_day(connection: Any, day: date, rows: tuple[int, int]) -> None:
    start, end = day_window(day)
    end = min(end, NOW - timedelta(minutes=5))
    first = 0
    for subtype, count in zip(("webfilter", "dns"), rows, strict=True):
        params = {
            "start": start,
            "step": _step(start, end, count),
            "rows": count,
            "day": day,
            "first": first,
            "subtype": subtype,
        }
        connection.execute(MESSAGES_SQL, params)
        connection.execute(TYPED_SQL[subtype], {k: v for k, v in params.items() if k != "subtype"})
        first += count


def _alerts(yesterday: date) -> list[Alert]:
    """200 active alerts, 20 per card; one key may be open once, the others acknowledged."""
    rows = []
    per_card = ACTIVE_ALERTS // len(ALERT_KEYS)
    for rule, pattern in ALERT_KEYS:
        for n in range(per_card):
            key = pattern.format(n=n, yesterday=yesterday.isoformat())
            open_ = "{n}" in pattern or n == 0
            rows.append(
                Alert(
                    rule=rule,
                    severity="high",
                    state="open" if open_ else "acknowledged",
                    title=f"{rule} ölçüm uyarısı {n}",
                    time=NOW - timedelta(minutes=n * 7),
                    details={},
                    dedup_key=key,
                )
            )
    return rows


def _state_files(layout: Layout) -> None:
    """The clock samples of today and yesterday, the collector's status with 20 sources."""
    for day in (NEWEST, NEWEST - timedelta(days=1)):
        path = layout.clock_samples(day)
        path.parent.mkdir(parents=True, exist_ok=True)
        start, _ = day_window(day)
        samples = [
            {
                "time": isoformat_site(start + timedelta(hours=hour, minutes=5), "seconds"),
                "server": "172.20.10.1",
                "offset_us": 1500 + hour,
                "ok": True,
            }
            for hour in range(24)
        ]
        path.write_text("".join(json.dumps(sample) + "\n" for sample in samples), "utf-8")
    received = isoformat_site(NOW - timedelta(seconds=20))
    layout.collector_status.parent.mkdir(parents=True, exist_ok=True)
    layout.collector_status.write_text(
        json.dumps(
            {
                "started_at": isoformat_site(NOW - timedelta(days=3), "seconds"),
                "updated_at": isoformat_site(NOW - timedelta(seconds=10), "seconds"),
                "sources": {
                    name: {"messages": 1000, "bytes": 600_000, "last_received": received}
                    for name in _source_names()
                },
            }
        ),
        "utf-8",
    )


def _timescale(engine: Engine) -> bool:
    with engine.connect() as connection:
        return bool(
            connection.scalar(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'timescaledb'")
            )
        )


def _source_names() -> list[str]:
    return [*FORTIGATES, *(f"switch-{n}" for n in range(STATUS_SOURCES - len(FORTIGATES)))]


@pytest.fixture
def perf_site(
    db_engine: Engine,
    app_session_factory: sessionmaker[Session],
    api_config: Config,
    layout: Layout,
) -> Iterator[sessionmaker[Session]]:
    began = time.perf_counter()
    days = [NEWEST - timedelta(days=offset) for offset in range(OLDER_DAYS + 1)]
    if _timescale(db_engine):
        pause_compression_jobs(db_engine)  # as test_search_perf.py: no chunk compressed mid-load
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=NEWEST, months_back=1)
    for day in days:
        with db_engine.begin() as connection:
            _load_day(connection, day, NEWEST_ROWS if day == NEWEST else OLDER_ROWS)
    with db_engine.begin() as connection:
        rows = JOB_RUNS_PER_DAY * JOB_RUN_DAYS
        start = NOW - timedelta(days=JOB_RUN_DAYS)
        connection.execute(
            JOB_RUNS_SQL, {"start": start, "step": _step(start, NOW, rows), "rows": rows}
        )
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("ANALYZE"))
    with db_engine.begin() as connection:
        # The oldest two days sit at the edge of the fresh statistics: the recount's join was
        # planned as a nested loop over the typed rows there (about 50 s a file instead of 0.5).
        connection.execute(text("SET LOCAL enable_nestloop = off"))
        for day in days:
            for source in FORTIGATES:
                rebuild_stream(connection, source, day)
    with app_session_factory() as session:
        session.add_all(
            Source(
                name=name,
                type="fortigate" if name in FORTIGATES else "mikrotik",
                address=SENDERS.get(name, f"172.20.10.{10 + index}"),
                last_seen=NOW - timedelta(minutes=1),
            )
            for index, name in enumerate(_source_names())
        )
        session.add_all(_alerts(NEWEST - timedelta(days=1)))
        session.commit()
    with db_engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE"))
    vault = _sealed(api_config)
    _state_files(layout)
    print(f"\ncompliance perf data ready in {time.perf_counter() - began:.0f} s")
    try:
        yield app_session_factory
    finally:
        vault.unlock_all()


def _sealed(config: Config) -> SealedVault:
    first = NEWEST - timedelta(days=SEALED_DAYS)
    vault = make_vault(config.data_dir, tsa_now=at_site(first - timedelta(days=30)))
    vault.seal_days([first + timedelta(days=n) for n in range(SEALED_DAYS)])
    return vault


def _compute(factory: sessionmaker[Session], app: FastAPI, config: Config) -> ComplianceStatus:
    with factory() as session:
        status = compute_status(
            session, Layout(config.data_dir), config, Channel(app.state.arci, config), NOW
        )
        session.rollback()
    return status


def test_compliance_within_one_second(
    perf_site: sessionmaker[Session], app: FastAPI, api_config: Config
) -> None:
    began = time.perf_counter()
    status = _compute(perf_site, app, api_config)
    cold = time.perf_counter() - began
    timings = []
    for _ in range(3):
        began = time.perf_counter()
        _compute(perf_site, app, api_config)
        timings.append(time.perf_counter() - began)
    print(f"\ncompliance: cold {cold:.3f} s, best of three {min(timings):.3f} s")
    # the cards really read the data (no card failed, card 7 counted every row of today)
    assert not [card.id for card in status.cards if "could not be run" in card.reason_en]
    cards = {card.id: card for card in status.cards}
    facts = {fact.key: fact.value_en for fact in cards["content_filter"].facts}
    assert facts["today_web"] == "300000"
    assert facts["today_dns"] == "500000"
    assert len(cards["sources"].alerts) == 5
    assert cards["chain"].tone != "neutral"
    assert min(timings) <= BUDGET, timings
