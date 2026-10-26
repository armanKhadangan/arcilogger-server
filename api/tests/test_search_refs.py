"""Evidence from a search (contract 2.13, 3.4): the raw references of the whole search, their
SHA-256 over the canonical list (as audited), the line limit, the time budget and the days that
are not sealed yet; only with ``evidence.create``."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.routers.search import state_of
from arcilogger_api.search.refs import refs_sha256
from arcilogger_api.search.testing import Lines, sign_in
from arcilogger_core.canonical import canonical_json
from arcilogger_core.layout import Layout
from arcilogger_db.models import AuditLog

API = "/api/v1/search"
NOW = datetime.now(UTC).replace(microsecond=0)
START, END = NOW - timedelta(days=3), NOW


@pytest.fixture
def lines(app_session_factory: sessionmaker[Session]) -> Lines:
    found = Lines(app_session_factory)
    for days in (0, 1, 2):
        for hours in (1, 2):
            found.add(NOW - timedelta(days=days, hours=hours), "fg_web", srcip="172.20.41.105")
    found.add(NOW - timedelta(hours=1), "fg_web", srcip="172.20.41.200")
    return found


def search_id(client: TestClient, q: str = "srcip:172.20.41.105") -> str:
    response = client.post(API, json={"q": q, "from": START.isoformat(), "to": END.isoformat()})
    assert response.status_code == 200, response.text
    return str(response.json()["search_id"])


def refs(client: TestClient, search: str, **body: Any) -> Any:
    return client.post(f"{API}/refs", json={"search_id": search, **body})


def test_the_references_of_a_search(
    client: TestClient, app_session_factory: sessionmaker[Session], lines: Lines, layout: Layout
) -> None:
    sign_in(client, app_session_factory)
    written = sorted(lines.written[:6], key=lambda w: w.time, reverse=True)
    sealed = written[-1]
    vault = layout.vault_day(sealed.day)
    vault.mkdir(parents=True, exist_ok=True)
    (vault / f"{sealed.source}.log.gz").write_bytes(b"")
    search = search_id(client)
    response = refs(client, search)
    assert response.status_code == 200, response.text
    answer = response.json()
    assert answer["refs"] == [
        {"source": w.source, "day": w.day.isoformat(), "line": w.line, "offset": w.offset}
        for w in written
    ]
    assert answer["truncated"] is False
    assert answer["complete"] is True
    days = sorted({w.day.isoformat() for w in written} - {sealed.day.isoformat()})
    assert answer["unsealed_days"] == days
    assert answer["sha256"] == hashlib.sha256(canonical_json(answer["refs"])).hexdigest()
    with app_session_factory() as session:
        (entry,) = session.scalars(select(AuditLog).where(AuditLog.action == "search.refs"))
    assert entry.details == {
        "search_id": search,
        "q": "srcip:172.20.41.105",
        "from": entry.details["from"],
        "to": entry.details["to"],
        "refs": 6,
        "truncated": False,
        "complete": True,
        "sha256": answer["sha256"],
    }
    assert entry.target == f"search:{search}"


def test_the_line_limit(
    client: TestClient, app_session_factory: sessionmaker[Session], lines: Lines
) -> None:
    sign_in(client, app_session_factory)
    answer = refs(client, search_id(client), limit=4).json()
    assert len(answer["refs"]) == 4
    assert answer["truncated"] is True
    assert answer["complete"] is True
    exact = refs(client, search_id(client), limit=6).json()
    assert exact["truncated"] is False
    assert (
        refs_sha256(
            [{**ref, "day": datetime.fromisoformat(ref["day"]).date()} for ref in answer["refs"]]
        )
        == answer["sha256"]
    )
    for limit in (0, 10_001):
        assert refs(client, search_id(client), limit=limit).status_code == 422


class FakeClock:
    def __init__(self, step: float) -> None:
        self.step = step
        self.now = 0.0

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_the_time_budget(
    app: FastAPI, client: TestClient, app_session_factory: sessionmaker[Session], lines: Lines
) -> None:
    sign_in(client, app_session_factory)
    search = search_id(client)
    state_of(app).clock = FakeClock(3.0)  # the budget (8 s) ends after a window or two
    answer = refs(client, search).json()
    assert answer["complete"] is False
    assert answer["truncated"] is False
    assert len(answer["refs"]) < 6


@pytest.mark.parametrize("role", ["auditor", "hr", "viewer"])
def test_only_who_may_create_evidence(
    client: TestClient, app_session_factory: sessionmaker[Session], role: str
) -> None:
    sign_in(client, app_session_factory, role)
    response = refs(client, "x")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_the_kvkk_notice(client: TestClient, app_session_factory: sessionmaker[Session]) -> None:
    sign_in(client, app_session_factory, notice=False)
    assert refs(client, "x").json()["error"]["code"] == "privacy_notice_required"
