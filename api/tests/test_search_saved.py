"""Saved searches (contract 2.11, owner decision D10): personal CRUD, names unique per owner
ignoring case, another user's id is 404, the per-user limit, and running one by ``saved_id``."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.search.saved import resolve_preset
from arcilogger_api.search.schemas import Preset
from arcilogger_api.search.testing import Lines, sign_in
from arcilogger_api.testing import LoggedIn
from arcilogger_core.timeutil import SITE_TZ
from arcilogger_db.models import SAVED_SEARCH_PRESETS, AuditLog, SavedSearch
from arcilogger_db.settings import set_setting

API = "/api/v1/search/saved"
NOW = datetime.now(UTC).replace(microsecond=0)


@pytest.fixture
def searcher(client: TestClient, app_session_factory: sessionmaker[Session]) -> LoggedIn:
    return sign_in(client, app_session_factory)


def body(
    name: str = "Finans engellenen", q: str = "vlan:41 action:blocked", **range_: Any
) -> dict[str, Any]:
    return {"name": name, "q": q, "range": range_ or {"preset": "24h"}}


def audits(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
        for row in rows:
            session.expunge(row)
    return rows


def test_create_list_update_delete(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    created = client.post(API, json=body(name="  Finans engellenen  "))
    assert created.status_code == 201, created.text
    saved = created.json()
    assert saved["name"] == "Finans engellenen"
    assert saved["q"] == "vlan:41 action:blocked"
    assert saved["range"] == {"preset": "24h"}
    assert saved["last_run_at"] is None
    assert client.get(API).json() == {"items": [saved]}
    start, end = NOW - timedelta(days=2), NOW - timedelta(days=1)
    changed = client.put(
        f"{API}/{saved['id']}",
        json=body(
            name="Finans dün",
            q="department:Finans",
            **{"from": start.isoformat(), "to": end.isoformat()},
        ),
    )
    assert changed.status_code == 200, changed.text
    after = changed.json()
    assert after["name"] == "Finans dün"
    assert datetime.fromisoformat(after["range"]["from"]) == start
    assert datetime.fromisoformat(after["updated_at"]) >= datetime.fromisoformat(
        saved["updated_at"]
    )
    assert client.delete(f"{API}/{saved['id']}").status_code == 204
    assert client.get(API).json() == {"items": []}
    assert client.delete(f"{API}/{saved['id']}").json()["error"]["code"] == "saved_search_not_found"
    (create,) = audits(app_session_factory, "search.saved_create")
    assert create.details == {
        "saved_id": saved["id"],
        "name": "Finans engellenen",
        "q": "vlan:41 action:blocked",
    }
    (update,) = audits(app_session_factory, "search.saved_update")
    assert update.details["q"] == "department:Finans"
    (delete,) = audits(app_session_factory, "search.saved_delete")
    assert delete.details == {"saved_id": saved["id"], "name": "Finans dün"}


def test_names_are_unique_per_owner_ignoring_case(
    app: FastAPI, client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    first = client.post(API, json=body(name="Finans")).json()
    taken = client.post(API, json=body(name="FINANS"))
    assert taken.status_code == 409
    assert taken.json()["error"]["code"] == "saved_search_name_taken"
    other = client.post(API, json=body(name="Planlama")).json()
    assert (
        client.put(f"{API}/{other['id']}", json=body(name="finans")).json()["error"]["code"]
        == "saved_search_name_taken"
    )
    assert (
        client.put(f"{API}/{first['id']}", json=body(name="finans")).status_code == 200
    )  # its own name
    with TestClient(app, base_url="http://testserver") as auditor:
        sign_in(auditor, app_session_factory, "auditor")
        assert auditor.post(API, json=body(name="Finans")).status_code == 201  # per owner


def test_another_users_saved_searches_are_not_found(
    app: FastAPI, client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    mine = client.post(API, json=body()).json()
    with TestClient(app, base_url="http://testserver") as auditor:
        sign_in(auditor, app_session_factory, "auditor")
        assert auditor.get(API).json() == {"items": []}
        for response in (
            auditor.put(f"{API}/{mine['id']}", json=body(name="başka")),
            auditor.delete(f"{API}/{mine['id']}"),
            auditor.post("/api/v1/search", json={"saved_id": mine["id"]}),
        ):
            assert response.status_code == 404
            assert response.json()["error"]["code"] == "saved_search_not_found"
    assert client.get(API).json()["items"][0]["name"] == mine["name"]


def test_the_per_user_limit(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        set_setting(session, "search.saved_max_per_user", 2, updated_by="test")
        session.commit()
    assert client.post(API, json=body(name="bir")).status_code == 201
    assert client.post(API, json=body(name="iki")).status_code == 201
    refused = client.post(API, json=body(name="üç"))
    assert refused.status_code == 409
    error = refused.json()["error"]
    assert error["code"] == "saved_search_limit"
    assert "2" in error["message_tr"]


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (body(q="foo:bar"), "search_unknown_field"),
        (body(q="srcip:"), "search_syntax"),
        (
            body(**{"from": NOW.isoformat(), "to": (NOW - timedelta(hours=1)).isoformat()}),
            "search_range_invalid",
        ),
        (
            body(**{"from": (NOW - timedelta(days=40)).isoformat(), "to": NOW.isoformat()}),
            "search_range_too_long",
        ),
        (body(name=""), "validation_error"),
        (body(name="x" * 81), "validation_error"),
        (body(q="x" * 1001), "validation_error"),
        (body(preset="2d"), "validation_error"),
        ({**body(), "range": {"preset": "24h", "from": NOW.isoformat()}}, "validation_error"),
        ({**body(), "owner_id": 1}, "validation_error"),
    ],
)
def test_refusals(
    client: TestClient, searcher: LoggedIn, payload: dict[str, Any], code: str
) -> None:
    response = client.post(API, json=payload)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code


def test_running_a_saved_search(
    client: TestClient, searcher: LoggedIn, app_session_factory: sessionmaker[Session]
) -> None:
    lines = Lines(app_session_factory)
    lines.add(NOW - timedelta(hours=2), "fg_web", srcip="172.20.41.105", action="blocked")
    lines.add(NOW - timedelta(days=3), "fg_web", srcip="172.20.41.106", action="blocked")
    saved = client.post(API, json=body(q="vlan:41 action:blocked")).json()
    page = client.post(
        "/api/v1/search", json={"saved_id": saved["id"], "q": "ignored", "limit": 10}
    )
    assert page.status_code == 200, page.text
    answer = page.json()
    assert answer["query"]["normalized"] == "vlan:41 action:blocked"
    assert len(answer["items"]) == 1  # the preset "24h" ends now
    window = datetime.fromisoformat(answer["range"]["to"]) - datetime.fromisoformat(
        answer["range"]["from"]
    )
    assert window == timedelta(hours=24)
    with app_session_factory() as session:
        row = session.get(SavedSearch, saved["id"])
        assert row is not None
        assert row.last_run_at is not None
    run = audits(app_session_factory, "search.run")[-1]
    assert run.details["saved_id"] == saved["id"]
    assert run.details["q"] == "vlan:41 action:blocked"
    fixed = client.post(
        API,
        json=body(
            name="eski",
            **{
                "from": (NOW - timedelta(days=4)).isoformat(),
                "to": (NOW - timedelta(days=2)).isoformat(),
            },
        ),
    ).json()
    old = client.post("/api/v1/search", json={"saved_id": fixed["id"]}).json()
    assert [item["summary"]["srcip"] for item in old["items"]] == ["172.20.41.106"]


def test_presets_are_the_models() -> None:
    assert tuple(Preset.__args__) == SAVED_SEARCH_PRESETS  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("preset", "start", "end"),
    [
        (
            "15m",
            datetime(2026, 10, 26, 0, 5, tzinfo=SITE_TZ) - timedelta(minutes=15),
            datetime(2026, 10, 26, 0, 5, tzinfo=SITE_TZ),
        ),
        (
            "today",
            datetime(2026, 10, 26, 0, 0, tzinfo=SITE_TZ),
            datetime(2026, 10, 26, 0, 5, tzinfo=SITE_TZ),
        ),
        (
            "yesterday",
            datetime(2026, 10, 25, 0, 0, tzinfo=SITE_TZ),
            datetime(2026, 10, 26, 0, 0, tzinfo=SITE_TZ),
        ),
        (
            "30d",
            datetime(2026, 9, 26, 0, 5, tzinfo=SITE_TZ),
            datetime(2026, 10, 26, 0, 5, tzinfo=SITE_TZ),
        ),
    ],
)
def test_presets_resolve_at_site_days(preset: str, start: datetime, end: datetime) -> None:
    # 00:05 site time is still the previous day in UTC: the site's day decides
    now = datetime(2026, 10, 26, 0, 5, tzinfo=SITE_TZ).astimezone(UTC)
    assert resolve_preset(preset, now) == (start, end)
    assert now.date() == date(2026, 10, 25)


def test_today_just_after_midnight_is_not_empty() -> None:
    midnight = datetime(2026, 10, 26, 0, 0, tzinfo=SITE_TZ)
    start, end = resolve_preset("today", midnight)
    assert start == midnight
    assert end > start
