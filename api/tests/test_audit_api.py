"""``/api/v1/audit``: list with filters and cursor pagination (newest first), chain verification
including tampering, and who may read it (spec 8.5, 12.3 page 14)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import testing as api_testing
from arcilogger_api.audit import SystemActor, record_audit
from arcilogger_api.lookup_testing import accept_privacy
from arcilogger_api.testing import LoggedIn
from arcilogger_db.models import AuditLog, User

Login = Callable[..., LoggedIn]
TOLGA = SystemActor(username="tolga", id=7)
DENIZ = SystemActor(username="deniz", id=8)


def _write(factory: sessionmaker[Session], entries: list[dict[str, Any]]) -> list[int]:
    ids = []
    for entry in entries:
        with factory() as session:
            ids.append(record_audit(session, **entry).id)
            session.commit()
    return ids


def _backdate(engine: Engine, entry_id: int, when: datetime) -> None:
    """Move an entry in time (the guard trigger is lifted; the chain then no longer matches)."""
    with engine.begin() as connection:
        connection.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
        connection.execute(
            text("UPDATE audit_log SET time = :t WHERE id = :id"), {"t": when, "id": entry_id}
        )
        connection.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))


@pytest.fixture
def login_as(login_as: Login, app_session_factory: sessionmaker[Session]) -> Login:
    """Signed in with the KVKK notice accepted (the entries are person-level data)."""

    def factory(client: TestClient, role: str = "admin", **user: Any) -> LoggedIn:
        who = login_as(client, role, **user)
        accept_privacy(app_session_factory, who.user_id)
        return who

    return factory


@pytest.fixture
def entries(app_session_factory: sessionmaker[Session]) -> list[int]:
    return _write(
        app_session_factory,
        [
            {"actor": TOLGA, "action": "auth.login", "target": "user:7", "ip": "172.20.10.37"},
            {"actor": TOLGA, "action": "lookup.run", "target": "lookup:1", "details": {"n": 1}},
            {"actor": DENIZ, "action": "auth.login", "target": "user:8"},
            {"actor": None, "action": "auth.login_failed", "details": {"reason": "unknown_user"}},
            {"actor": TOLGA, "action": "settings.change", "target": "setting:x"},
        ],
    )


def test_list_is_newest_first_with_cursor_pagination(
    client: TestClient, login_as: Login, entries: list[int]
) -> None:
    login_as(client, "auditor")
    page = client.get("/api/v1/audit", params={"limit": 2}).json()
    assert [item["id"] for item in page["items"]] == [entries[4], entries[3]]
    assert page["next_cursor"] == entries[3]
    second = client.get("/api/v1/audit", params={"limit": 2, "cursor": page["next_cursor"]}).json()
    assert [item["id"] for item in second["items"]] == [entries[2], entries[1]]
    last = client.get("/api/v1/audit", params={"limit": 2, "cursor": second["next_cursor"]}).json()
    assert [item["id"] for item in last["items"]] == [entries[0]]
    assert last["next_cursor"] is None
    first = last["items"][0]
    assert set(first) == {
        "id", "time", "actor_id", "actor", "action", "target", "target_masked", "details", "ip",
        "prev_hash", "hash",
    }  # fmt: skip
    assert (first["actor"], first["actor_id"], first["ip"]) == ("tolga", 7, "172.20.10.37")
    # Only a usage subject is ever masked (test_usage_api.py), for a reader who sees pseudonyms.
    assert (first["target"], first["target_masked"]) == ("user:7", False)
    assert first["prev_hash"] == "0" * 64
    assert datetime.fromisoformat(first["time"]).utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("params", "expected"),
    [
        ({"actor": "tolga"}, [4, 1, 0]),
        ({"actor": "system"}, [3]),
        ({"action": "auth."}, [3, 2, 0]),
        ({"action": "auth.login"}, [3, 2, 0]),  # prefix: also auth.login_failed
        ({"action": "auth.login_"}, [3]),  # "_" is literal, not a LIKE wildcard
        ({"action": "lookup"}, [1]),
        ({"target": "user:7"}, [0]),
        ({"target": "user:"}, []),  # exact, not prefix
        ({"actor": "tolga", "action": "auth."}, [0]),
    ],
)
def test_filters(
    client: TestClient,
    login_as: Login,
    entries: list[int],
    params: dict[str, str],
    expected: list[int],
) -> None:
    login_as(client, "admin")
    found = client.get("/api/v1/audit", params=params).json()["items"]
    assert [item["id"] for item in found] == [entries[i] for i in expected]


def test_time_range_filter(
    client: TestClient, login_as: Login, entries: list[int], db_engine: Engine
) -> None:
    login_as(client, "admin")
    base = datetime(2026, 10, 1, 9, 0, tzinfo=UTC)
    for offset, entry_id in enumerate(entries):
        _backdate(db_engine, entry_id, base + timedelta(hours=offset))
    params = {"from": "2026-10-01T13:00:00+03:00", "to": "2026-10-01T13:00:00Z"}
    found = client.get("/api/v1/audit", params=params).json()["items"]
    # 10:00 UTC inclusive .. 13:00 UTC exclusive → entries at 10:00, 11:00, 12:00
    assert [item["id"] for item in found] == [entries[3], entries[2], entries[1]]
    naive = client.get("/api/v1/audit", params={"from": "2026-10-01T13:00:00"})
    assert naive.status_code == 422  # a time without offset is ambiguous
    reversed_range = client.get(
        "/api/v1/audit", params={"from": "2026-10-02T00:00:00Z", "to": "2026-10-01T00:00:00Z"}
    )
    assert reversed_range.status_code == 422


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 501},
        {"cursor": 0},
        {"cursor": 2**63},  # past bigint: 422, never a database error and a dropped connection
        {"cursor": 10**30},
        {"action": "a" * 101},
        {"actor": "a" * 65},
    ],
)
def test_parameters_are_bounded(
    client: TestClient, login_as: Login, params: dict[str, Any]
) -> None:
    login_as(client, "admin")
    assert client.get("/api/v1/audit", params=params).status_code == 422


def test_verify_reports_an_intact_chain(
    client: TestClient,
    login_as: Login,
    entries: list[int],
    app_session_factory: sessionmaker[Session],
) -> None:
    login_as(client, "auditor")
    status = client.get("/api/v1/audit/verify").json()
    with app_session_factory() as session:
        last = session.scalars(select(AuditLog.hash).order_by(AuditLog.id.desc())).first()
    assert (status["ok"], status["entries"], status["first_bad_id"]) == (True, 5, None)
    assert (status["last_hash"], status["errors"]) == (last, [])
    assert datetime.fromisoformat(status["verified_at"]).tzinfo is not None


def test_verify_detects_tampering(
    client: TestClient, login_as: Login, entries: list[int], db_engine: Engine
) -> None:
    login_as(client, "auditor")
    with db_engine.begin() as connection:
        connection.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update_delete"))
        connection.execute(
            text("UPDATE audit_log SET actor_username = 'mallory' WHERE id = :id"),
            {"id": entries[1]},
        )
        connection.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": entries[3]})
        connection.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_no_update_delete"))
    status = client.get("/api/v1/audit/verify").json()
    assert status["ok"] is False
    assert status["first_bad_id"] == entries[1]
    assert status["entries"] == 4
    assert any(f"#{entries[1]}: hash does not match" in error for error in status["errors"])
    assert any(f"#{entries[4]}: prev_hash" in error for error in status["errors"])


def test_empty_log(client: TestClient, login_as: Login) -> None:
    login_as(client, "admin")
    assert client.get("/api/v1/audit").json() == {"items": [], "next_cursor": None}
    status = client.get("/api/v1/audit/verify").json()
    assert (status["ok"], status["entries"], status["last_hash"]) == (True, 0, None)


@pytest.mark.parametrize(("role", "allowed"), [("admin", True), ("auditor", True), ("hr", False),
                                               ("viewer", False)])  # fmt: skip
def test_only_audit_readers(client: TestClient, login_as: Login, role: str, allowed: bool) -> None:
    login_as(client, role)
    for path in ("/api/v1/audit", "/api/v1/audit/verify"):
        response = client.get(path)
        if allowed:
            assert response.status_code == 200
        else:
            assert (response.status_code, response.json()["error"]["code"]) == (403, "forbidden")


def test_entries_need_the_privacy_notice(
    client: TestClient,
    app_session_factory: sessionmaker[Session],
    make_user: Callable[..., User],
    entries: list[int],
) -> None:
    """Lookup entries carry IP/MAC addresses and lease/person ids (spec 3.3): like every other
    person-level endpoint, the list waits for the accepted KVKK notice; the chain check (counts
    and hashes only) does not."""
    who = api_testing.login(client, app_session_factory, make_user("auditor"))
    response = client.get("/api/v1/audit", params={"action": "lookup."})
    assert (response.status_code, response.json()["error"]["code"]) == (
        403,
        "privacy_notice_required",
    )
    assert client.get("/api/v1/audit/verify").status_code == 200
    accept_privacy(app_session_factory, who.user_id)
    found = client.get("/api/v1/audit", params={"action": "lookup."}).json()["items"]
    assert [item["id"] for item in found] == [entries[1]]
