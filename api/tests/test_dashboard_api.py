"""``GET /api/v1/dashboard/summary`` (spec 12.3 page 2): the boxes per role, their numbers
against fixtures (sources and messages, the seal calendar, the credit, the clock, the alerts)
and the budget of the messages-per-minute query on a few hundred thousand rows."""

from __future__ import annotations

import json
import re
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.routers import dashboard
from arcilogger_api.routers.dashboard import (
    MESSAGE_RATE_BUDGET_SECONDS,
    clock_box,
    credit_box,
    estimated_days,
    message_rate,
)
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import SITE_TZ, isoformat_site, site_day, to_site
from arcilogger_db.engine import session_factory
from arcilogger_db.models import Alert, Message, SealVerification, Source
from arcilogger_db.partitions import ensure_partitions
from arcilogger_worker.vaulttesting import SealedVault, at_site, make_vault

Login = Callable[..., LoggedIn]
API = "/api/v1/dashboard/summary"
D10, D11, D12, D13, D14 = (date(2026, 9, day) for day in range(10, 15))
#: The dashboard's "now": the morning after D14, 07:00:30 site time.
NOW = datetime(2026, 9, 15, 7, 0, 30, tzinfo=SITE_TZ)
BOXES = ("ingest", "seal", "credit", "clock", "alerts")


@pytest.fixture
def factory(app_session_factory: sessionmaker[Session]) -> sessionmaker[Session]:
    return app_session_factory


def frozen(monkeypatch: pytest.MonkeyPatch, now: datetime) -> None:
    monkeypatch.setattr(dashboard, "utc_now", lambda: now.astimezone(UTC))


def moment(value: str | None) -> datetime | None:
    return None if value is None else datetime.fromisoformat(value)


def get(client: TestClient) -> dict[str, Any]:
    response = client.get(API)
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


# -- boxes per role -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("role", "visible"),
    [
        ("admin", {"ingest", "seal", "credit", "clock", "alerts"}),
        ("auditor", {"ingest", "seal", "credit", "clock"}),  # seal.read: seal, credit, clock
        ("viewer", {"ingest"}),
    ],
)
def test_each_box_needs_its_own_permission(
    client: TestClient, login_as: Login, role: str, visible: set[str]
) -> None:
    login_as(client, role)
    body = get(client)
    assert {box for box in BOXES if body[box] is not None} == visible
    assert set(body) == {"generated_at", *BOXES}
    assert len(body["ingest"]["sparkline"]) == 60


def test_hr_has_no_dashboard(client: TestClient, login_as: Login) -> None:
    """hr holds only ``dashboard.usage`` (the usage widgets come from ``/usage``)."""
    login_as(client, "hr")
    response = client.get(API)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_empty_installation(client: TestClient, login_as: Login) -> None:
    login_as(client, "admin")
    body = get(client)
    ingest = body["ingest"]
    assert (ingest["sources_total"], ingest["sources_active"], ingest["silent"]) == (0, 0, [])
    assert ingest["messages_per_minute"] == 0.0
    assert {point["count"] for point in ingest["sparkline"]} == {0}
    assert body["seal"]["yesterday"]["status"] == "no_data"
    assert len(body["seal"]["calendar"]) == 30
    assert body["credit"] == {"remaining": None, "estimated_days": None, "threshold": 500}
    assert body["clock"] == {
        "offset_ms": None,
        "ntp_server": "time.ume.tubitak.gov.tr",  # no local time source: the fallback
        "checked_at": None,
        "ok": None,
    }
    assert body["alerts"]["latest"] == []


# -- the numbers ----------------------------------------------------------------------------------


def add_messages(session: Session, at: datetime, count: int, source: str = "fortigate") -> None:
    for n in range(count):
        session.add(
            Message(
                time=at,
                time_received=at,
                source=source,
                sender="172.31.255.1",
                raw_day=site_day(at),
                raw_line=n + 1,
                raw_offset=0,
                type="traffic",
            )
        )


@pytest.fixture
def site(factory: sessionmaker[Session], layout: Layout) -> None:
    """Six sources (four watched), the collector's status and messages around NOW."""
    with factory() as session:
        for name, heartbeat, last_seen, extra in (
            ("fortigate", 10, NOW - timedelta(minutes=2), {}),
            ("core-r1", 15, NOW - timedelta(minutes=40), {}),  # the collector heard it: active
            ("switch-1", 15, NOW - timedelta(minutes=20), {}),
            ("switch-2", 15, None, {}),
            ("ap-172.20.10.23", 15, NOW - timedelta(hours=5), {"expected": False}),
            ("unknown-172.20.19.9", 15, NOW, {"unknown": True}),
            ("switch-3", 15, None, {"enabled": False}),
        ):
            session.add(
                Source(
                    name=name,
                    type="fortigate" if name == "fortigate" else "mikrotik",
                    heartbeat_minutes=heartbeat,
                    last_seen=last_seen,
                    first_seen=last_seen,
                    **extra,
                )
            )
        end = NOW.replace(second=0)
        start = end - timedelta(minutes=60)
        add_messages(session, start + timedelta(seconds=1), 2)  # the first minute
        add_messages(session, end - timedelta(minutes=5) + timedelta(seconds=10), 3)
        add_messages(session, start - timedelta(seconds=30), 4)  # before the hour
        add_messages(session, end + timedelta(seconds=5), 7)  # the current minute
        session.commit()
    layout.collector_status.parent.mkdir(parents=True, exist_ok=True)
    layout.collector_status.write_text(
        json.dumps(
            {
                "updated_at": isoformat_site(NOW),
                "sources": {
                    "core-r1": {"last_received": isoformat_site(NOW - timedelta(minutes=1))}
                },
            }
        ),
        encoding="utf-8",
    )


@pytest.fixture
def vault(api_config: Config) -> Iterator[SealedVault]:
    """D10 and D11 sealed, D12 sealed three days late, D13 failed (TSA down), D14 waiting."""
    sealed = make_vault(api_config.data_dir, tsa_now=at_site(D10 - timedelta(days=30)))
    sealed.seal_days([D10, D11])
    sealed.write_day(D12)
    assert sealed.seal(D12, now=at_site(D12 + timedelta(days=3), 1, 0)).status == "sealed"
    sealed.write_day(D13)
    assert sealed.fail_seal(D13).status == "pending"
    sealed.write_day(D14)
    yield sealed
    sealed.unlock_all()


def write_credit(layout: Layout, entries: list[tuple[datetime, int | None]]) -> None:
    layout.credit_log.parent.mkdir(parents=True, exist_ok=True)
    with layout.credit_log.open("w", encoding="utf-8") as handle:
        for at, credits in entries:
            line = {"time": isoformat_site(at, "seconds"), "provider": "zamane", "credits": credits}
            if credits is None:
                line["error"] = "zd.kamusm.gov.tr unreachable"
            handle.write(json.dumps(line) + "\n")


def write_clock(layout: Layout, day: date, *samples: dict[str, Any]) -> None:
    path = layout.clock_samples(day)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(sample) + "\n" for sample in samples), encoding="utf-8")


def sample(at: datetime, offset_us: int | None, *, ok: bool = True) -> dict[str, Any]:
    return {
        "time": isoformat_site(at, "seconds"),
        "server": "172.20.10.1" if ok else None,
        "offset_us": offset_us,
        "ok": ok,
    }


def test_dashboard_numbers(
    client: TestClient,
    login_as: Login,
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    layout: Layout,
    site: None,
    vault: SealedVault,
) -> None:
    frozen(monkeypatch, NOW)
    write_credit(layout, [(NOW - timedelta(days=1), 9634)])
    write_clock(layout, site_day(NOW), sample(NOW - timedelta(minutes=20), 3200))
    with factory() as session:
        for severity, state in (("high", "open"), ("medium", "open"), ("low", "acknowledged")):
            session.add(
                Alert(rule="R15", severity=severity, state=state, title="t", time=NOW, details={})
            )
        session.commit()
    login_as(client, "admin")
    body = get(client)
    assert moment(body["generated_at"]) == NOW
    assert body["generated_at"].endswith("+03:00")

    ingest = body["ingest"]
    assert (ingest["sources_total"], ingest["sources_active"]) == (4, 2)
    assert [(item["name"], moment(item["last_seen"])) for item in ingest["silent"]] == [
        ("switch-1", NOW - timedelta(minutes=20)),
        ("switch-2", None),
    ]
    spark = ingest["sparkline"]
    start = NOW.replace(second=0) - timedelta(minutes=60)
    assert (moment(spark[0]["minute"]), spark[0]["count"]) == (start, 2)
    assert spark[0]["minute"].endswith("+03:00")
    assert spark[55]["count"] == 3
    assert moment(spark[-1]["minute"]) == start + timedelta(minutes=59)
    assert sum(point["count"] for point in spark) == 5
    assert ingest["messages_per_minute"] == 0.1  # 5 / 60

    seal = body["seal"]
    assert seal["yesterday"] == {
        "day": "2026-09-14",
        "status": "pending",
        "sealed_at": None,
        "serial_short": None,
        "verified": None,
    }
    calendar = {cell["day"]: cell["status"] for cell in seal["calendar"]}
    assert len(calendar) == 30
    assert min(calendar) == "2026-08-16"
    assert [calendar[day.isoformat()] for day in (D10, D11, D12, D13, D14)] == [
        "sealed",
        "sealed",
        "sealed",  # late, but sealed
        "failed",
        "pending",
    ]
    assert calendar["2026-09-09"] == "no_data"

    assert body["credit"] == {"remaining": 9634, "estimated_days": 9634, "threshold": 500}
    clock = body["clock"]
    assert moment(clock.pop("checked_at")) == NOW - timedelta(minutes=20)
    assert clock == {"offset_ms": 3.2, "ntp_server": "172.20.10.1", "ok": True}
    alerts = body["alerts"]
    assert alerts["open_by_severity"] == {
        "critical": 0,
        "high": 1,
        "medium": 1,
        "low": 0,
        "info": 0,
    }
    assert [item["severity"] for item in alerts["latest"]] == ["low", "medium", "high"]


def test_yesterday_sealed_and_verified(
    client: TestClient,
    login_as: Login,
    monkeypatch: pytest.MonkeyPatch,
    factory: sessionmaker[Session],
    vault: SealedVault,
    layout: Layout,
) -> None:
    frozen(monkeypatch, datetime(2026, 9, 12, 7, 0, tzinfo=SITE_TZ))
    finished = at_site(D12, 2, 0)
    with factory() as session:
        session.add(
            SealVerification(
                day=D11,
                started_at=finished - timedelta(seconds=3),
                finished_at=finished,
                username="ayse",
                deep=True,
                outcome="ok",
            )
        )
        session.commit()
    login_as(client, "auditor")
    yesterday = get(client)["seal"]["yesterday"]
    state = json.loads(layout.seal_state(D11).read_text(encoding="utf-8"))
    assert yesterday["day"] == "2026-09-11"
    assert yesterday["status"] == "sealed"
    assert yesterday["verified"] is True
    assert datetime.fromisoformat(yesterday["sealed_at"]) == datetime.fromisoformat(
        state["sealed_at"]
    )
    serial = state["serial"]
    assert yesterday["serial_short"] == (serial if len(serial) <= 8 else f"{serial[:8]}…")


def test_credit_estimate_measures_the_use(layout: Layout, api_config: Config) -> None:
    newest = NOW - timedelta(days=1)
    write_credit(
        layout,
        [
            (NOW - timedelta(days=45), 5000),  # older than 30 days before the newest check
            (NOW - timedelta(days=20), 1000),
            (NOW - timedelta(days=10), 980),
            (NOW - timedelta(days=9), 2980),  # bought: not negative use
            (NOW - timedelta(days=5), None),  # unreachable: no reading
            (newest, 2964),
        ],
    )
    box = credit_box(layout, api_config)
    # 36 used in 19 days: 2964 / (36 / 19) = 1564.3
    assert box.model_dump() == {"remaining": 2964, "estimated_days": 1564, "threshold": 500}


def test_credit_estimate_without_measurable_use() -> None:
    def entries(*values: tuple[int, int | None]) -> list[dict[str, Any]]:
        return [
            {"time": NOW - timedelta(days=days), "credits": credits} for days, credits in values
        ]

    assert estimated_days([]) is None
    assert estimated_days(entries((3, None))) is None
    assert estimated_days(entries((1, 120))) == 120  # one stamp a day
    assert estimated_days(entries((3, 500), (1, 500))) == 500  # no use measured
    assert estimated_days(entries((1, 500), (0, 499))) == 499  # less than a day apart
    assert estimated_days(entries((10, 100), (0, 0))) == 0
    assert estimated_days(entries((2, 10), (0, -3))) == 0


def test_clock_box_cases(layout: Layout, api_config: Config) -> None:
    api_config = api_config.model_copy(
        update={"clock": api_config.clock.model_copy(update={"ntp_server": "172.20.10.1"})}
    )
    today = site_day(NOW)
    assert clock_box(layout, api_config, NOW).ok is None  # no sample at all
    write_clock(layout, today - timedelta(days=1), sample(NOW - timedelta(hours=1), -800))
    box = clock_box(layout, api_config, NOW)  # just after midnight: yesterday's file
    assert (box.offset_ms, box.ok) == (-0.8, True)
    write_clock(
        layout,
        today,
        sample(NOW - timedelta(minutes=50), 3200),
        sample(NOW - timedelta(minutes=10), 1_500_000),
    )
    box = clock_box(layout, api_config, NOW)
    assert (box.offset_ms, box.ok, box.ntp_server) == (1500.0, False, "172.20.10.1")
    write_clock(layout, today, sample(NOW - timedelta(minutes=10), None, ok=False))
    box = clock_box(layout, api_config, NOW)
    assert (box.offset_ms, box.ok, box.ntp_server) == (None, False, "172.20.10.1")
    write_clock(layout, today, sample(NOW - timedelta(hours=4), 100))
    with layout.clock_samples(today).open("a", encoding="utf-8") as handle:
        handle.write('{"time": "torn\n')
    box = clock_box(layout, api_config, NOW)  # the torn last line is skipped
    assert box.offset_ms == 0.1
    assert box.ok is None  # four hours old: says nothing about now
    assert box.checked_at == to_site(NOW - timedelta(hours=4)).replace(microsecond=0)


def test_alert_box_matches_the_alert_summary(
    client: TestClient, login_as: Login, factory: sessionmaker[Session]
) -> None:
    with factory() as session:
        for n, severity in enumerate(("critical", "high", "high", "low", "info", "medium")):
            session.add(
                Alert(
                    rule="R20-disk",
                    severity=severity,
                    state="open",
                    title="t",
                    time=NOW + timedelta(minutes=n),
                    details={},
                )
            )
        session.commit()
    login_as(client, "admin")
    box = get(client)["alerts"]
    summary = client.get("/api/v1/alerts/summary").json()
    assert box == {key: summary[key] for key in ("open_by_severity", "latest")}
    assert len(box["latest"]) == 5


# -- the messages-per-minute query on a few hundred thousand rows ---------------------------------

LOAD = text(
    "INSERT INTO messages (time, time_received, source, sender, raw_day, raw_line, raw_offset, "
    "type, text) SELECT t, t, 'fortigate', '172.31.255.1', :day, g, 0, 'traffic', 'x' "
    "FROM generate_series(1, :rows) AS g, "
    "LATERAL (SELECT CAST(:start AS timestamptz) + g * CAST(:step AS interval) AS t) AS x"
)


def _load(engine: Engine, start: datetime, span: timedelta, rows: int) -> None:
    with engine.begin() as connection:
        connection.execute(
            LOAD,
            {
                "day": site_day(start),
                "rows": rows,
                "start": start,
                "step": f"{span.total_seconds() * 1000 / (rows + 1)} milliseconds",
            },
        )
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as connection:
        connection.execute(text("VACUUM ANALYZE messages"))


def _best_of_three(engine: Engine, now: datetime) -> tuple[float, int]:
    timings, total = [], 0
    for _ in range(3):
        with session_factory(engine)() as session:
            began = time.perf_counter()
            points, _ = message_rate(session, now)
            timings.append(time.perf_counter() - began)
            total = sum(point.count for point in points)
    return min(timings), total


@pytest.mark.perf
def test_message_rate_stays_within_its_budget(db_engine: Engine) -> None:
    """300,000 messages over the 23 hours before the window, 100,000 in it (about 1,700 a
    minute; spec 12.3 shows 1,284), then 200,000 more in it (5,000 a minute, a busy hour)."""
    now = datetime(2026, 10, 14, 15, 30, 20, tzinfo=UTC)
    end = now.replace(second=0)
    with db_engine.begin() as connection:
        ensure_partitions(connection, today=site_day(now))
    _load(db_engine, end - timedelta(hours=24), timedelta(hours=23), 300_000)
    _load(db_engine, end - timedelta(hours=1), timedelta(hours=1), 100_000)
    with db_engine.connect() as connection:
        plan = "\n".join(
            connection.execute(
                text(
                    "EXPLAIN SELECT date_trunc('minute', time), count(*) FROM messages "
                    "WHERE time >= :s AND time < :e GROUP BY 1"
                ),
                {"s": end - timedelta(hours=1), "e": end},
            ).scalars()
        )
    seconds, total = _best_of_three(db_engine, now)
    print(f"\nmessages per minute, 100,000 of 400,000 rows: {seconds * 1000:.1f} ms\n{plan}")
    assert total == _count(db_engine, end) > 99_000
    # On the primary key (time, id): a month partition's index is "<partition>_pkey", a
    # TimescaleDB chunk's is "<n>_pk_messages".
    assert re.search(r'Index Only Scan using "?\w*(?:_pkey|_pk_messages)\b', plan), plan
    assert seconds < MESSAGE_RATE_BUDGET_SECONDS
    _load(db_engine, end - timedelta(hours=1), timedelta(hours=1), 200_000)
    seconds, total = _best_of_three(db_engine, now)
    print(f"messages per minute, 300,000 of 600,000 rows: {seconds * 1000:.1f} ms")
    assert total == _count(db_engine, end) > 299_000
    assert seconds < MESSAGE_RATE_BUDGET_SECONDS


def _count(engine: Engine, end: datetime) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(
                text("SELECT count(*) FROM messages WHERE time >= :s AND time < :e"),
                {"s": end - timedelta(hours=1), "e": end},
            )
            or 0
        )
