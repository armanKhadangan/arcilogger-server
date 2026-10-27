"""Cursors and the window scan (contract 2.6.1, 2.6.3, 3.2).

* Concatenated pages equal the full ordered list without duplicates or gaps, also with equal
  timestamps across tables (also at exactly ``from``), for any page size, and after ``frontier``
  cursors when the time budget ran out (a clock the test controls); a budget gone before any
  window gives the cursor back.
* A cursor is parsed strictly: anything changed is 422 ``search_cursor_invalid``.
* A first window that times out is halved down to 5 minutes; when even that times out the
  search fails with 504 ``search_timeout``, audited with ``outcome: "timeout"``.
* A branch split at a compression horizon (TimescaleDB) pages exactly like one that is not, on
  any storage mode (the horizons are set by the test); on TimescaleDB the horizon of a table is
  the end of its newest compressed chunk, and nothing anywhere else.
"""

from __future__ import annotations

import base64
import json
import os
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.errors import ApiError
from arcilogger_api.routers.search import state_of
from arcilogger_api.search import parse_query
from arcilogger_api.search.cursor import Bound, Cursor, bounds, decode, encode, utc_text
from arcilogger_api.search.fields import SiteNetwork
from arcilogger_api.search.planner import plan_query
from arcilogger_api.search.testing import Lines, sign_in
from arcilogger_api.search.windows import Storage, read_storage, run_page
from arcilogger_api.testing import LoggedIn
from arcilogger_db.models import TIME_SERIES_TABLES, AuditLog
from arcilogger_db.settings import get_setting, set_setting
from arcilogger_db.testing import pause_compression_jobs

API = "/api/v1/search"
T = datetime(2026, 10, 26, 9, 30, 0, 123456, tzinfo=UTC)
START, END = T - timedelta(days=1), T + timedelta(hours=1)
CLIENT = "172.20.41.5"


@pytest.fixture
def searcher(client: TestClient, app_session_factory: sessionmaker[Session]) -> LoggedIn:
    with app_session_factory() as session:
        set_setting(session, "search.runs_per_minute", 600, updated_by="test")
        session.commit()
    return sign_in(client, app_session_factory)


def _setting(factory: sessionmaker[Session], key: str, value: Any) -> None:
    with factory() as session:
        set_setting(session, key, value, updated_by="test")
        session.commit()


def at_one_time(found: Lines, time: datetime) -> None:
    """Ten rows of the client in five tables, all at ``time``."""
    for table, count in (
        ("fg_traffic", 3),
        ("fg_web", 2),
        ("fg_dns", 2),
        ("mt_dhcp", 1),
        ("mt_event", 2),
    ):
        for _ in range(count):
            values: dict[str, Any] = (
                {"srcip": CLIENT} if table.startswith("fg_") else {"ip": CLIENT}
            )
            if table == "mt_event":
                values["event"] = "dhcp_offering"
            found.add(
                time, table, source="core-r1" if table.startswith("mt_") else "fortigate", **values
            )


@pytest.fixture
def lines(app_session_factory: sessionmaker[Session]) -> Lines:
    """Rows of one client in several tables, many at exactly the same time."""
    found = Lines(app_session_factory)
    at_one_time(found, T)
    for minutes in (0, 1, 1, 59, 61, 120, 600, 601, 1300):
        found.add(T - timedelta(minutes=minutes, seconds=1), "fg_web", srcip=CLIENT)
        found.add(T - timedelta(minutes=minutes, seconds=1), "fg_traffic", srcip=CLIENT)
    found.add(T - timedelta(hours=30), "fg_web", srcip=CLIENT)  # before the range
    found.add(T, "fg_web", srcip="172.20.41.6")  # another client
    return found


def search(
    client: TestClient, q: str, limit: int, start: datetime = START, end: datetime = END
) -> Any:
    response = client.post(
        API, json={"q": q, "from": start.isoformat(), "to": end.isoformat(), "limit": limit}
    )
    assert response.status_code == 200, response.text
    return response.json()


def pages(client: TestClient, first: dict[str, Any], limit: int) -> Iterator[dict[str, Any]]:
    page = first
    yield page
    while page["next_cursor"] is not None:
        response = client.post(
            f"{API}/page",
            json={"search_id": page["search_id"], "cursor": page["next_cursor"], "limit": limit},
        )
        assert response.status_code == 200, response.text
        page = response.json()
        yield page


def keys(client: TestClient, q: str, limit: int) -> list[str]:
    return [
        item["key"]
        for page in pages(client, search(client, q, limit), limit)
        for item in page["items"]
    ]


# -- concatenated pages ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "q", [f"srcip:{CLIENT}", "type:traffic,web,dns,dhcp", f"srcip:{CLIENT} -type:dns"]
)
def test_pages_concatenate_to_the_whole_list(
    client: TestClient, searcher: LoggedIn, lines: Lines, q: str
) -> None:
    whole = keys(client, q, 500)
    assert len(whole) == len(set(whole)) > 20
    times = [key.split(":", 1)[1].rsplit(":", 1)[0] for key in whole]
    assert times == sorted(times, reverse=True)
    for limit in (1, 2, 3, 5, 11):
        assert keys(client, q, limit) == whole, limit


@pytest.mark.parametrize("q", [f"srcip:{CLIENT}", "type:traffic,web,dns,dhcp"])
def test_rows_at_exactly_from_are_paged_too(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session], q: str
) -> None:
    # Device times of whole seconds and a range from site midnight: a page may end at ``from``.
    found = Lines(app_session_factory)
    at_one_time(found, START)
    for minutes in (5, 5, 90):
        found.add(START + timedelta(minutes=minutes), "fg_web", srcip=CLIENT)
    whole = keys(client, q, 500)
    assert len(whole) == len(set(whole)) == 13
    assert sum(f":{utc_text(START)}:" in key for key in whole) == 10
    for limit in (1, 2, 3):
        assert keys(client, q, limit) == whole, limit


def test_a_budget_gone_before_any_window_gives_the_cursor_back(
    app_session_factory: sessionmaker[Session],
) -> None:
    plan = plan_query(parse_query(f"srcip:{CLIENT}"), SiteNetwork())
    cursor = Cursor.last(START, min(plan.ranks), 7)
    with app_session_factory() as session:
        page = run_page(session, plan, START, END, cursor, 5, 0.0)
    assert page.rows == []
    assert page.next_cursor == cursor  # a frontier at ``from`` would end the search here
    assert page.scan.complete is False


def test_equal_times_are_ordered_by_branch_then_id(
    client: TestClient, searcher: LoggedIn, lines: Lines
) -> None:
    first = search(client, f"srcip:{CLIENT}", 500)
    at_t = [
        item for item in first["items"] if item["time"].startswith("2026-10-26T09:30:00.123456")
    ]
    assert [item["table"] for item in at_t] == ["fg_traffic"] * 3 + ["fg_web"] * 2 + [
        "fg_dns"
    ] * 2 + ["mt_dhcp"] + ["mt_event"] * 2
    ids = [int(item["key"].rsplit(":", 1)[1]) for item in at_t if item["table"] == "fg_traffic"]
    assert ids == sorted(ids, reverse=True)


class FakeClock:
    """Seconds that advance by ``step`` on every reading."""

    def __init__(self, step: float) -> None:
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_frontier_cursors_continue_where_the_budget_stopped(
    app: FastAPI,
    client: TestClient,
    searcher: LoggedIn,
    lines: Lines,
    app_session_factory: sessionmaker[Session],
) -> None:
    q = f"srcip:{CLIENT}"
    whole = keys(client, q, 500)
    _setting(app_session_factory, "search.request_budget_ms", 1000)
    state_of(app).clock = FakeClock(0.15)  # about two windows per request
    partial = []
    found = []
    for page in pages(client, search(client, q, 500), 500):
        found += [item["key"] for item in page["items"]]
        if not page["scan"]["complete"]:
            cursor = json.loads(base64.urlsafe_b64decode(page["next_cursor"] + "=="))
            assert cursor["k"] == "frontier"
            frontier = datetime.fromisoformat(cursor["t"])
            assert datetime.fromisoformat(page["scan"]["scanned_from"]) == frontier
            assert all(datetime.fromisoformat(item["time"]) >= frontier for item in page["items"])
            partial.append(page)
    assert len(partial) >= 3
    assert found == whole


# -- a split at the compression horizon -----------------------------------------------------------


def _set_storage(app: FastAPI, storage: Storage) -> None:
    """The storage the search plans for, whatever the database's is."""
    cache = state_of(app).storage
    cache.invalidate()
    cache.get_or_compute(lambda: storage)


@pytest.mark.parametrize(
    "horizon",
    [
        T + timedelta(microseconds=1),  # the ten rows at T are older than it
        T - timedelta(minutes=61, seconds=1),  # rows at exactly the horizon are newer
        T - timedelta(minutes=30),
    ],
)
@pytest.mark.parametrize("q", [f"srcip:{CLIENT}", "type:traffic,web,dns,dhcp"])
def test_a_split_at_a_compression_horizon_pages_the_same(
    app: FastAPI,
    client: TestClient,
    searcher: LoggedIn,
    lines: Lines,
    db_engine: Engine,
    q: str,
    horizon: datetime,
) -> None:
    whole = keys(client, q, 500)
    _set_storage(app, Storage(False, dict.fromkeys(TIME_SERIES_TABLES, horizon)))
    sent: list[str] = []

    def record(_c: Any, _cursor: Any, statement: str, *_args: Any) -> None:
        sent.append(statement)

    event.listen(db_engine, "before_cursor_execute", record)
    try:
        for limit in (500, 1, 2, 3, 5, 11):
            assert keys(client, q, limit) == whole, limit
    finally:
        event.remove(db_engine, "before_cursor_execute", record)
    assert any(" AS split" in statement for statement in sent)


def test_compression_horizons_are_where_the_compressed_chunks_end(
    app_session_factory: sessionmaker[Session], db_engine: Engine
) -> None:
    with app_session_factory() as session:
        status = get_setting(session, "db.timescale") or {}
        storage = read_storage(session, 5.0)
    assert storage.horizons == {}  # nothing compressed (yet)
    assert storage.timescale is (status.get("storage") == "hypertables")
    if not status.get("compression"):
        assert os.environ.get("ARCILOGGER_EXPECT_TIMESCALE") != "1", status
        return  # native partitions: never anything to split
    pause_compression_jobs(db_engine)
    found = Lines(app_session_factory)
    old = T - timedelta(days=20)
    for moment in (old, T):
        found.add(moment, "fg_web", srcip=CLIENT)
    with db_engine.begin() as connection:
        connection.execute(
            text(
                "SELECT compress_chunk(c, if_not_compressed => true) FROM show_chunks("
                "'fg_web', older_than => CAST(:before AS timestamptz)) AS c"
            ),
            {"before": T - timedelta(days=10)},
        )
        end = connection.scalar(
            text(
                "SELECT range_end FROM timescaledb_information.chunks "
                "WHERE hypertable_name = 'fg_web' AND is_compressed"
            )
        )
    plan = plan_query(parse_query(f"srcip:{CLIENT}"), SiteNetwork())
    with app_session_factory() as session:
        horizons = read_storage(session, 5.0).horizons
        assert horizons == {"fg_web": end}
        page = run_page(
            session, plan, T - timedelta(days=30), END, None, 10, 5.0, horizons=horizons
        )
    assert [row["time"] for row in page.rows] == [T, old]


# -- the cursor itself ----------------------------------------------------------------------------


def _raw(data: Any) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).decode().rstrip("=")


def test_cursors_round_trip() -> None:
    for cursor in (Cursor.last(T, 3, 42), Cursor.frontier(T)):
        text_ = encode(cursor)
        assert "=" not in text_
        assert decode(text_, start=START, end=END, ranks={0, 3}) == cursor


GOOD: dict[str, Any] = {
    "v": 1,
    "k": "last",
    "t": "2026-10-26T09:30:00.123456+00:00",
    "b": 2,
    "i": 7,
}


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "not base64 !",
        base64.urlsafe_b64encode(b"\xff\xfe").decode(),
        _raw([1, 2]),
        _raw({**GOOD, "v": 2}),
        _raw({**GOOD, "v": True}),
        _raw({**GOOD, "k": "next"}),
        _raw({**GOOD, "x": 1}),
        _raw({key: value for key, value in GOOD.items() if key != "i"}),
        _raw({**GOOD, "t": "2026-10-26T09:30:00"}),
        _raw({**GOOD, "t": "2026-10-28T09:30:00+00:00"}),
        _raw({**GOOD, "t": "2026-10-20T09:30:00+00:00"}),
        _raw({**GOOD, "t": 5}),
        _raw({**GOOD, "b": 9}),
        _raw({**GOOD, "b": False}),
        _raw({**GOOD, "i": -1}),
        _raw({**GOOD, "i": 1.5}),
        _raw({**GOOD, "i": 2**63}),
        _raw({"v": 1, "k": "frontier", "t": GOOD["t"], "b": 2}),
        base64.urlsafe_b64encode(
            b'{"v":1,"v":1,"k":"frontier","t":"2026-10-26T09:30:00+00:00"}'
        ).decode(),
        "A" * 300,
    ],
)
def test_changed_cursors_are_refused(cursor: str) -> None:
    with pytest.raises(ApiError) as refused:
        decode(cursor, start=START, end=END, ranks={1, 2})
    assert refused.value.status_code == 422
    assert refused.value.code == "search_cursor_invalid"


def test_the_good_cursor_is_accepted() -> None:
    assert decode(_raw(GOOD), start=START, end=END, ranks={1, 2}) == Cursor.last(
        datetime.fromisoformat(GOOD["t"]), 2, 7
    )


def test_a_changed_cursor_through_the_api(
    client: TestClient, searcher: LoggedIn, lines: Lines
) -> None:
    first = search(client, f"srcip:{CLIENT}", 2)
    for cursor in (
        _raw({**GOOD, "b": 0}),
        first["next_cursor"][:-3] + "AAA",
        _raw({**GOOD, "t": "2027-01-01T00:00:00+00:00"}),
    ):
        response = client.post(
            f"{API}/page", json={"search_id": first["search_id"], "cursor": cursor}
        )
        assert response.status_code == 422, cursor
        assert response.json()["error"]["code"] == "search_cursor_invalid"


def test_the_bound_of_each_branch_after_a_cursor() -> None:
    found = bounds(Cursor.last(T, 2, 7), END, [0, 1, 2, 3])
    assert found == {
        0: Bound(T),
        1: Bound(T),
        2: Bound(T, before_id=7),
        3: Bound(T, inclusive=True),
    }
    assert bounds(Cursor.frontier(T), END, [1, 3]) == {1: Bound(T), 3: Bound(T)}
    assert bounds(None, END, [0]) == {0: Bound(END)}


# -- the window scan ------------------------------------------------------------------------------


def test_a_slow_first_window_is_halved(
    app: FastAPI,
    client: TestClient,
    searcher: LoggedIn,
    lines: Lines,
    app_session_factory: sessionmaker[Session],
) -> None:
    spans: list[timedelta] = []

    def slow_while_wide(session: Session, lower: datetime, upper: datetime) -> None:
        spans.append(upper - lower)
        if len(spans) <= 2:  # the first two tries of the first window
            session.execute(text("SELECT pg_sleep(5)"))

    _setting(app_session_factory, "search.request_budget_ms", 2000)
    state_of(app).hook = slow_while_wide
    page = search(client, f"srcip:{CLIENT}", 3)
    assert spans[:4] == [
        timedelta(hours=1),
        timedelta(minutes=30),
        timedelta(minutes=15),
        timedelta(minutes=30),  # the next window doubles again
    ]
    assert len(page["items"]) == 3
    assert page["scan"]["complete"] is True


def test_when_even_five_minutes_time_out_the_search_fails_and_is_audited(
    app: FastAPI,
    client: TestClient,
    searcher: LoggedIn,
    lines: Lines,
    app_session_factory: sessionmaker[Session],
) -> None:
    spans: list[timedelta] = []

    def always_slow(session: Session, lower: datetime, upper: datetime) -> None:
        spans.append(upper - lower)
        session.execute(text("SELECT pg_sleep(5)"))

    _setting(app_session_factory, "search.request_budget_ms", 500)
    state_of(app).hook = always_slow
    response = client.post(
        API, json={"q": f"srcip:{CLIENT}", "from": START.isoformat(), "to": END.isoformat()}
    )
    assert response.status_code == 504
    error = response.json()["error"]
    assert error["code"] == "search_timeout"
    assert "0.5" in error["message_en"]
    assert spans == [timedelta(minutes=m) for m in (60, 30, 15, 7.5, 5)]
    with app_session_factory() as session:
        entry = session.scalars(select(AuditLog).where(AuditLog.action == "search.run")).one()
    assert entry.details["outcome"] == "timeout"
    assert entry.details["search_id"] is None
    assert entry.details["q"] == f"srcip:{CLIENT}"
    assert entry.details["rows"] == 0


def test_a_later_window_that_times_out_ends_the_page_with_a_frontier(
    app: FastAPI,
    client: TestClient,
    searcher: LoggedIn,
    lines: Lines,
    app_session_factory: sessionmaker[Session],
) -> None:
    calls: list[datetime] = []

    def slow_after_the_first(session: Session, lower: datetime, upper: datetime) -> None:
        calls.append(lower)
        if len(calls) > 1:
            session.execute(text("SELECT pg_sleep(5)"))

    _setting(app_session_factory, "search.request_budget_ms", 600)
    state_of(app).hook = slow_after_the_first
    page = search(client, f"srcip:{CLIENT}", 500)
    assert page["scan"]["complete"] is False
    assert page["scan"]["windows"] == 1
    cursor = json.loads(base64.urlsafe_b64decode(page["next_cursor"] + "=="))
    assert cursor == {"v": 1, "k": "frontier", "t": cursor["t"]}
    assert datetime.fromisoformat(cursor["t"]) == calls[0]
    assert page["items"]  # the first hour's rows are shown
