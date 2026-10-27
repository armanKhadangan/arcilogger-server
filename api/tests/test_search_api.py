"""``/api/v1/search`` (contract 2.10, 2.6.2, 2.6.4, 2.21, 3.4): the endpoints, their audit entries,
the registry of searches, the gate and the limits, the field catalogue and the parser."""

from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.routers import search as search_router
from arcilogger_api.routers.search import READ_ONLY, state_of
from arcilogger_api.search import FIELDS
from arcilogger_api.search.grammar import LIMITS
from arcilogger_api.search.registry import PER_USER, Entry
from arcilogger_api.search.testing import Lines, sign_in
from arcilogger_api.testing import LoggedIn, login
from arcilogger_core.config import Config
from arcilogger_db.models import AuditLog, Source, User
from arcilogger_db.settings import set_setting

API = "/api/v1/search"
NOW = datetime.now(UTC).replace(microsecond=0)
START, END = NOW - timedelta(hours=6), NOW + timedelta(minutes=1)


@pytest.fixture
def api_config(tmp_path: Any) -> Config:
    """The tests' site with a network source (``ap``) and two configured devices."""
    return Config.model_validate(
        {
            "site_name": "Örnek A.Ş.",
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
                {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
                {"name": "ap", "type": "generic", "address": "172.20.10.48/28", "expected": False},
            ],
        }
    )


def setting(factory: sessionmaker[Session], key: str, value: Any) -> None:
    with factory() as session:
        set_setting(session, key, value, updated_by="test")
        session.commit()


@pytest.fixture
def searcher(client: TestClient, app_session_factory: sessionmaker[Session]) -> LoggedIn:
    setting(app_session_factory, "search.runs_per_minute", 600)
    return sign_in(client, app_session_factory)


@pytest.fixture
def lines(app_session_factory: sessionmaker[Session]) -> Lines:
    found = Lines(app_session_factory)
    found.add(
        NOW - timedelta(hours=1),
        "fg_web",
        srcip="172.20.41.105",
        hostname="www.example.com",
        url="/giris",
        action="blocked",
        catdesc="Sentetik: Haber",
        text='msg="youtube ‮gnp.exe"',
    )
    found.add(
        NOW - timedelta(hours=2),
        "fg_traffic",
        srcip="172.20.41.105",
        dstip="203.0.113.10",
        dstport=443,
        proto=6,
        action="accept",
        policyid=12,
        srcintf="port2",
        dstintf="wan1",
    )
    found.add(
        NOW - timedelta(hours=3),
        "mt_dhcp",
        source="core-r1",
        ip="172.20.42.57",
        mac="00:00:5e:00:53:57",
        action="assigned",
    )
    found.add(
        NOW - timedelta(hours=4),
        None,
        source="ap-172.20.10.50",
        sender="172.20.10.50",
        text="İşlem başarısız " + "x" * 700,
    )
    return found


def run(client: TestClient, q: str = "", **body: Any) -> Any:
    payload = {"q": q, "from": START.isoformat(), "to": END.isoformat(), **body}
    return client.post(API, json=payload)


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
        for row in rows:
            session.expunge(row)
    return rows


# -- POST /search ---------------------------------------------------------------------------------


def test_a_search_answers_its_first_page_and_is_audited(
    client: TestClient, searcher: LoggedIn, lines: Lines, app_session_factory: sessionmaker[Session]
) -> None:
    response = run(client, "srcip:172.20.41.105", limit=1)
    assert response.status_code == 200, response.text
    page = response.json()
    assert set(page) == {
        "search_id",
        "expires_at",
        "query",
        "range",
        "plan",
        "items",
        "next_cursor",
        "scan",
        "notices",
    }
    assert page["query"]["normalized"] == "srcip:172.20.41.105"
    assert page["plan"]["driver"] == "typed"
    assert page["plan"]["selective"] is True
    assert datetime.fromisoformat(page["range"]["from"]) == START
    (row,) = page["items"]
    assert row["table"] == "fg_web"
    assert row["kind"] == "web"
    assert row["summary"]["domain"] == "www.example.com"
    assert row["summary"]["vlan_id"] == 41
    assert row["raw"] == {
        "source": "fortigate",
        "day": lines.written[0].day.isoformat(),
        "line": 1,
        "offset": 0,
    }
    assert page["next_cursor"] is not None
    assert page["scan"]["complete"] is True
    (entry,) = audits(app_session_factory, "search.run")
    assert entry.actor_id == searcher.user_id
    assert entry.target == f"search:{page['search_id']}"
    assert set(entry.details) == {
        "search_id",
        "q",
        "from",
        "to",
        "kinds",
        "driver",
        "tables",
        "rows",
        "complete",
        "outcome",
        "elapsed_ms",
        "saved_id",
    }
    assert entry.details["rows"] == 1
    assert entry.details["outcome"] == "ok"
    assert entry.details["tables"] == page["plan"]["tables"]
    assert "www.example.com" not in str(entry.details)  # inputs and counts, never row content
    # pages of an audited search are not audited again
    more = client.post(
        f"{API}/page", json={"search_id": page["search_id"], "cursor": page["next_cursor"]}
    )
    assert more.status_code == 200
    assert more.json()["items"][0]["table"] == "fg_traffic"
    stats = client.post(f"{API}/stats", json={"search_id": page["search_id"]})
    assert stats.status_code == 200
    assert len(audits(app_session_factory, "search.run")) == 1


def test_row_texts_are_display_safe_and_short(
    client: TestClient, searcher: LoggedIn, lines: Lines
) -> None:
    items = {item["table"]: item for item in run(client, "").json()["items"]}
    assert "‮" not in items["messages"]["text"]
    web = next(item for item in run(client, "type:web").json()["items"])
    assert "\\u202e" in web["text"]
    long = next(item for item in run(client, "source:ap").json()["items"])
    assert len(long["text"]) == 500
    traffic = next(item for item in run(client, "type:traffic").json()["items"])
    assert traffic["summary"]["proto"] == "tcp"
    assert traffic["summary"]["policy"] == "12"
    assert traffic["summary"]["interface"] == "port2 → wan1"


def test_notices(client: TestClient, searcher: LoggedIn, lines: Lines) -> None:
    codes = [
        notice["code"] for notice in run(client, '"İşlem" department:Planlama').json()["notices"]
    ]
    assert codes[:4] == ["text_case", "text_truncated", "department_by_vlan", "vault_only_before"]
    vault = next(n for n in run(client, "").json()["notices"] if n["code"] == "vault_only_before")
    assert vault["params"] == {"date": lines.written[-1].day.isoformat()}
    later = client.post(
        API,
        json={"q": "", "from": (NOW - timedelta(minutes=30)).isoformat(), "to": END.isoformat()},
    )
    assert "vault_only_before" not in [n["code"] for n in later.json()["notices"]]
    assert "query_not_selective" in [
        n["code"] for n in run(client, "-action:accept").json()["notices"]
    ]


@pytest.mark.parametrize(
    ("body", "code", "status"),
    [
        ({"q": "srcip:"}, "search_syntax", 422),
        ({"q": "foo:bar"}, "search_unknown_field", 422),
        ({"q": "srcip:300.1.1.1"}, "search_value_invalid", 422),
        ({"q": "ab"}, "search_term_too_short", 422),
        ({"q": "foo OR bar"}, "search_or_unsupported", 422),
        ({"q": "domain:example.com mac:00:00:5e:00:53:21"}, "search_fields_incompatible", 422),
        ({"q": "vlan:99"}, "search_vlan_unknown", 422),
        ({"q": "department:Yok"}, "search_department_unknown", 422),
        ({"from": END.isoformat(), "to": START.isoformat()}, "search_range_invalid", 422),
        ({"from": None}, "search_range_invalid", 422),
        ({"from": (NOW - timedelta(days=40)).isoformat()}, "search_range_too_long", 422),
        ({"limit": 501}, "validation_error", 422),
        ({"limit": 0}, "validation_error", 422),
        ({"extra": 1}, "validation_error", 422),
        ({"to": "2026-10-26T10:00:00"}, "validation_error", 422),
        ({"q": "x" * 1001}, "validation_error", 422),
        ({"saved_id": 99}, "saved_search_not_found", 404),
    ],
)
def test_refusals(
    client: TestClient, searcher: LoggedIn, body: dict[str, Any], code: str, status: int
) -> None:
    payload = {"q": "", "from": START.isoformat(), "to": END.isoformat(), **body}
    response = client.post(
        API, json={key: value for key, value in payload.items() if value is not None}
    )
    assert response.status_code == status, response.text
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message_tr"]
    assert error["message_en"]


def test_a_refused_query_points_at_its_span(client: TestClient, searcher: LoggedIn) -> None:
    error = run(client, "srcip:172.20.41.5 vlan:99").json()["error"]
    assert error["details"] == {"start": 18, "end": 25, "params": {"vlan": 99}}
    assert "99" in error["message_tr"]


def test_the_page_size_follows_the_setting(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    setting(app_session_factory, "search.page_size_max", 50)
    assert run(client, limit=51).status_code == 422
    assert run(client, limit=50).status_code == 200
    assert run(client).status_code == 200  # the default page (100) is cut to the maximum
    setting(app_session_factory, "search.max_days", 1)
    error = client.post(
        API, json={"q": "", "from": (NOW - timedelta(days=2)).isoformat(), "to": NOW.isoformat()}
    ).json()["error"]
    assert error["code"] == "search_range_too_long"
    assert "1 gün" in error["message_tr"]


# -- the registry ---------------------------------------------------------------------------------


def _page(client: TestClient, search_id: str) -> Any:
    return client.post(f"{API}/stats", json={"search_id": search_id})


def test_a_search_belongs_to_its_user_and_session(
    app: FastAPI,
    client: TestClient,
    searcher: LoggedIn,
    lines: Lines,
    app_session_factory: sessionmaker[Session],
) -> None:
    search_id = run(client).json()["search_id"]
    assert _page(client, search_id).status_code == 200
    with TestClient(app, base_url="http://testserver") as other:
        sign_in(other, app_session_factory, "auditor")
        refused = _page(other, search_id)
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "search_expired"
    with TestClient(app, base_url="http://testserver") as same_user_new_session:
        with app_session_factory() as session:
            user = session.get(User, searcher.user_id)
            assert user is not None
            session.expunge(user)
        login(same_user_new_session, app_session_factory, user)
        assert _page(same_user_new_session, search_id).status_code == 409
    assert _page(client, "unknown-id").status_code == 409


def test_searches_expire_after_thirty_idle_minutes(
    app: FastAPI, client: TestClient, searcher: LoggedIn
) -> None:
    clock = [NOW]
    registry = state_of(app).registry
    registry.clock = lambda: clock[0]
    search_id = run(client).json()["search_id"]
    clock[0] += timedelta(minutes=29)
    assert _page(client, search_id).status_code == 200  # used: the TTL slides
    clock[0] += timedelta(minutes=29)
    assert _page(client, search_id).status_code == 200
    clock[0] += timedelta(minutes=30)
    assert _page(client, search_id).json()["error"]["code"] == "search_expired"


def test_the_oldest_searches_of_a_user_are_dropped(
    app: FastAPI, client: TestClient, searcher: LoggedIn
) -> None:
    ids = [run(client).json()["search_id"] for _ in range(PER_USER + 1)]
    assert _page(client, ids[0]).status_code == 409
    assert all(_page(client, search_id).status_code == 200 for search_id in ids[1:])
    assert len(state_of(app).registry) == PER_USER


def test_the_registry_has_a_total_limit(app: FastAPI) -> None:
    registry = state_of(app).registry
    registry.total = 3
    plan: Any = None
    for index in range(5):
        registry.add(Entry(f"id{index}", index, "h", plan, START, END, 10, None, NOW))
    assert len(registry) == 3
    with pytest.raises(Exception, match="search_expired"):
        registry.get("id0", 0, "h")
    assert registry.get("id4", 4, "h").search_id == "id4"


# -- gate and limits ------------------------------------------------------------------------------


def test_busy_when_every_slot_is_taken(
    app: FastAPI, client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    setting(app_session_factory, "search.max_concurrent", 1)
    gate = state_of(app).gate
    gate.wait_seconds = 0.2
    taken = threading.Event()
    release = threading.Event()

    def hold() -> None:
        with gate.slot(-1, 1):
            taken.set()
            release.wait(5)

    holder = threading.Thread(target=hold)
    holder.start()
    try:
        taken.wait(5)
        response = run(client)
        assert response.status_code == 429
        assert response.json()["error"]["code"] == "search_busy"
        assert response.headers["Retry-After"] == "2"
    finally:
        release.set()
        holder.join()
    assert run(client).status_code == 200
    assert gate.active == 0


def test_two_requests_per_user(app: FastAPI) -> None:
    gate = state_of(app).gate
    gate.wait_seconds = 0.05
    with gate.slot(7, 10), gate.slot(7, 10):
        with pytest.raises(Exception, match="search_busy"), gate.slot(7, 10):
            pass
        with gate.slot(8, 10):
            assert gate.active == 3


def test_runs_per_minute(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    setting(app_session_factory, "search.runs_per_minute", 2)
    assert run(client).status_code == 200
    assert run(client).status_code == 200
    refused = run(client)
    assert refused.status_code == 429
    assert refused.json()["error"]["code"] == "search_rate_limited"
    assert int(refused.headers["Retry-After"]) >= 1


# -- one row --------------------------------------------------------------------------------------


def test_the_event_drawer(
    client: TestClient, searcher: LoggedIn, lines: Lines, app_session_factory: sessionmaker[Session]
) -> None:
    item = run(client, "type:traffic dstport:443").json()["items"][0]
    table, rest = item["key"].split(":", 1)
    time, _, row_id = rest.rpartition(":")
    response = client.get(f"{API}/event", params={"table": table, "time": time, "id": row_id})
    assert response.status_code == 200, response.text
    event = response.json()
    assert event["table"] == "fg_traffic"
    assert event["kind"] == "traffic"
    assert event["fields"]["dstport"] == 443
    assert event["fields"]["srcip"] == "172.20.41.105"
    assert "extra" not in event["fields"]
    assert event["message"]["type"] == "traffic"
    assert event["raw"] == item["raw"]
    (entry,) = audits(app_session_factory, "search.event")
    assert entry.details == {
        "table": "fg_traffic",
        "id": int(row_id),
        "time": entry.details["time"],
        "raw": {**item["raw"]},
    }
    assert entry.target == item["key"]
    message = run(client, "source:ap").json()["items"][0]
    table, rest = message["key"].split(":", 1)
    time, _, row_id = rest.rpartition(":")
    assert (
        client.get(f"{API}/event", params={"table": table, "time": time, "id": row_id})
        .json()["message"]["text"]
        .startswith("İşlem")
    )


def _event_params(item: dict[str, Any]) -> dict[str, Any]:
    table, rest = item["key"].split(":", 1)
    time, _, row_id = rest.rpartition(":")
    return {"table": table, "time": time, "id": row_id}


def test_the_event_drawer_shows_extra_display_safe(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    Lines(app_session_factory).add(
        NOW - timedelta(hours=1),
        "fg_web",
        srcip="172.20.41.77",
        extra={"devname": "FW\u202e01", "liste": ["a\x07b", 3], "iç": {"k\x1b": None}},
    )
    item = run(client, "srcip:172.20.41.77").json()["items"][0]
    event_ = client.get(f"{API}/event", params=_event_params(item)).json()
    assert event_["extra"] == {
        "devname": "FW\\u202e01",
        "liste": ["a\\u0007b", 3],
        "iç": {"k\\u001b": None},
    }


def test_an_event_read_that_runs_out_of_time_is_not_not_found(
    client: TestClient,
    searcher: LoggedIn,
    lines: Lines,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = run(client, "type:traffic dstport:443").json()["items"][0]
    assert item["table"] == "fg_traffic"
    monkeypatch.setattr(search_router, "STATEMENT_SECONDS", 0.3)
    with app_session_factory() as busy:  # a loaded database: the read waits on a lock
        busy.execute(text("LOCK TABLE fg_traffic IN ACCESS EXCLUSIVE MODE"))
        response = client.get(f"{API}/event", params=_event_params(item))
        busy.rollback()
    assert response.status_code == 504, response.text
    error = response.json()["error"]
    assert error["code"] == "search_event_timeout"
    assert "0.3" in error["message_en"]
    assert audits(app_session_factory, "search.event") == []  # no row left the API


@pytest.mark.parametrize(
    ("params", "status", "code"),
    [
        ({"table": "users", "time": NOW.isoformat(), "id": 1}, 404, "search_event_not_found"),
        ({"table": "fg_web", "time": NOW.isoformat(), "id": 999}, 404, "search_event_not_found"),
        ({"table": "fg_web", "time": "2026-10-26T10:00:00", "id": 1}, 422, "validation_error"),
        ({"table": "fg_web", "time": NOW.isoformat(), "id": -1}, 422, "validation_error"),
    ],
)
def test_the_event_drawer_refuses(
    client: TestClient, searcher: LoggedIn, params: dict[str, Any], status: int, code: str
) -> None:
    response = client.get(f"{API}/event", params=params)
    assert response.status_code == status
    assert response.json()["error"]["code"] == code


# -- the language ---------------------------------------------------------------------------------


def test_the_field_catalogue(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        session.add(Source(name="unknown-198.51.100.77", type="fortigate", unknown=True))
        session.commit()
    setting(app_session_factory, "search.max_days", 14)
    catalogue = client.get(f"{API}/fields").json()
    assert [field["name"] for field in catalogue["fields"]] == list(FIELDS)
    srcip = next(field for field in catalogue["fields"] if field["name"] == "srcip")
    assert srcip["aliases"] == ["kaynakip", "istemci"]
    assert srcip["kind"] == "ip"
    assert "fg_web" in srcip["indexed"]
    assert [kind["key"] for kind in catalogue["kinds"]][:2] == ["traffic", "web"]
    assert catalogue["sources"] == [
        {"name": "ap", "type": "generic", "network": True},
        {"name": "core-r1", "type": "mikrotik", "network": False},
        {"name": "fortigate", "type": "fortigate", "network": False},
        {"name": "unknown-198.51.100.77", "type": "fortigate", "network": False},
    ]
    assert catalogue["limits"] == {**LIMITS, "max_days": 14, "page_size_max": 500}


def test_parsing_while_typing(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    sign_in(client, app_session_factory, notice=False)  # no rows: the KVKK notice is not needed
    good = client.post(
        f"{API}/parse", json={"q": "KAYNAKİP:172.20.41.5 -eylem:Deny youtube"}
    ).json()
    assert good["normalized"] == "srcip:172.20.41.5 -action:deny youtube"
    assert good["errors"] == []
    assert good["selective"] is True
    assert [clause["field"] for clause in good["clauses"]] == ["srcip", "action", "text"]
    bad = client.post(f"{API}/parse", json={"q": "srcip:192.0.2.4 foo:bar"}).json()
    assert bad["normalized"] == ""
    (error,) = bad["errors"]
    assert (error["code"], error["start"], error["end"]) == ("search_unknown_field", 16, 23)
    # binding needs the database: an unknown VLAN is found when the search runs
    assert client.post(f"{API}/parse", json={"q": "vlan:99"}).json()["errors"] == []
    body = {"q": "", "from": START.isoformat(), "to": END.isoformat()}
    assert client.post(API, json=body).json()["error"]["code"] == "privacy_notice_required"


def test_the_read_only_statement_is_the_first_of_each_search(
    app: FastAPI, client: TestClient, searcher: LoggedIn
) -> None:
    seen: list[str] = []
    engine = app.state.arci.engine

    def listener(_connection: Any, _cursor: Any, statement: str, *_: Any) -> None:
        seen.append(statement)

    event.listen(engine, "before_cursor_execute", listener)
    try:
        assert run(client).status_code == 200
    finally:
        event.remove(engine, "before_cursor_execute", listener)
    assert READ_ONLY in seen
    first = seen.index(READ_ONLY)
    assert "INSERT INTO audit_log" in " ".join(seen[first:])
    before_audit = seen[
        first : next(i for i, s in enumerate(seen) if "audit_log" in s and i > first)
    ]
    assert not any(
        s.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE")) for s in before_audit
    )


@pytest.fixture(autouse=True)
def _no_leftover_slots(app: FastAPI) -> Iterator[None]:
    yield
    assert state_of(app).gate.active == 0
