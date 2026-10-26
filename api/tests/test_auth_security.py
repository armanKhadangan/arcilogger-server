"""Security properties of every B5 endpoint (spec 17 "auth bypass attempts, CSRF, IDOR"):

* each route is classified below; a new route that is not fails the test, so nothing is added
  without deciding who may call it;
* CSRF is required on every mutating request, and bound to the session where there is one;
* 401 (no usable session / second factor missing) versus 403 (signed in, refused);
* the role matrix of the new endpoints, IDOR on user records;
* ``must_change_password`` blocks everything except /auth/me, /auth/password and /auth/logout;
* the KVKK notice is enforced by the server (``require_privacy_notice``);
* a request never holds two pooled database connections (activity is committed on the
  request's own session), so concurrent requests cannot starve the pool.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, select, update
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from arcilogger_api import testing as api_testing
from arcilogger_api.app import create_app
from arcilogger_api.auth.accounts import privacy_required
from arcilogger_api.auth.privacy import require_privacy_notice
from arcilogger_api.auth.testing import ensure_secret_key
from arcilogger_api.deps import AppState
from arcilogger_api.security import (
    BACKGROUND_HEADER,
    CSRF_COOKIE,
    CSRF_HEADER,
    CurrentUser,
    hash_token,
)
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import ROLES, User, UserSession
from arcilogger_db.settings import set_setting

Login = Callable[..., LoggedIn]
MakeUser = Callable[..., User]
ADMIN_ONLY = frozenset({"admin"})
AUDIT_READERS = frozenset({"admin", "auditor"})
EVERYONE = frozenset(ROLES)


@dataclass(frozen=True)
class Rule:
    """``access``: ``public`` (no session), ``pending`` (also before TOTP), ``signed_in``."""

    access: str
    roles: frozenset[str] = EVERYONE
    #: Usable while ``must_change_password`` is set.
    during_password_change: bool = False


RULES: dict[tuple[str, str], Rule] = {
    ("POST", "/api/v1/auth/login"): Rule("public"),
    ("GET", "/api/v1/auth/setup/status"): Rule("public"),
    ("POST", "/api/v1/auth/setup/admin"): Rule("public"),
    ("POST", "/api/v1/auth/totp"): Rule("pending", during_password_change=True),
    ("GET", "/api/v1/auth/totp/setup"): Rule("pending"),
    ("POST", "/api/v1/auth/totp/confirm"): Rule("pending"),
    ("GET", "/api/v1/auth/me"): Rule("pending", during_password_change=True),
    ("POST", "/api/v1/auth/logout"): Rule("pending", during_password_change=True),
    ("POST", "/api/v1/auth/password"): Rule("signed_in", during_password_change=True),
    ("POST", "/api/v1/auth/reauth"): Rule("signed_in"),
    ("POST", "/api/v1/auth/recovery-codes"): Rule("signed_in"),
    ("POST", "/api/v1/auth/privacy-notice"): Rule("signed_in"),
    ("GET", "/api/v1/users"): Rule("signed_in", ADMIN_ONLY),
    ("POST", "/api/v1/users"): Rule("signed_in", ADMIN_ONLY),
    ("GET", "/api/v1/users/{user_id}"): Rule("signed_in", ADMIN_ONLY),
    ("PATCH", "/api/v1/users/{user_id}"): Rule("signed_in", ADMIN_ONLY),
    ("POST", "/api/v1/users/{user_id}/reset-password"): Rule("signed_in", ADMIN_ONLY),
    ("POST", "/api/v1/users/{user_id}/reset-totp"): Rule("signed_in", ADMIN_ONLY),
    ("POST", "/api/v1/users/{user_id}/unlock"): Rule("signed_in", ADMIN_ONLY),
    ("GET", "/api/v1/audit"): Rule("signed_in", AUDIT_READERS),
    ("GET", "/api/v1/audit/verify"): Rule("signed_in", AUDIT_READERS),
}
OWNED_PREFIXES = ("/api/v1/auth", "/api/v1/users", "/api/v1/audit")


@pytest.fixture(autouse=True)
def _secret_key(api_config: Config) -> None:
    ensure_secret_key(api_config.secrets_dir)


#: Valid bodies, so that a refusal is about access and never a 422 for a missing field.
BODIES: dict[tuple[str, str], dict[str, Any]] = {
    ("POST", "/api/v1/auth/login"): {"username": "nobody", "password": "Yanlış parola 12"},
    ("POST", "/api/v1/auth/setup/admin"): {
        "token": "t",
        "username": "neu",
        "display_name": "N",
        "password": "Yanlış parola 12",
    },
    ("POST", "/api/v1/auth/totp"): {"code": "123456"},
    ("POST", "/api/v1/auth/totp/confirm"): {"code": "123456"},
    ("POST", "/api/v1/auth/password"): {
        "current_password": "Yanlış parola 12",
        "new_password": "Sarı limon ağacı 1923",
    },
    ("POST", "/api/v1/auth/reauth"): {"password": "Yanlış parola 12"},
    ("POST", "/api/v1/auth/privacy-notice"): {"version": "1"},
    ("POST", "/api/v1/users"): {"username": "neu", "display_name": "N", "role": "viewer"},
    ("PATCH", "/api/v1/users/{user_id}"): {"display_name": "Yeni"},
}


def _routes(app: FastAPI) -> list[tuple[str, str]]:
    """Every method and path of the B5 routers (from the OpenAPI description)."""
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    return sorted(
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(OWNED_PREFIXES)
        for method in operations
    )


def _call(client: TestClient, method: str, path: str, target: int) -> Response:
    url = path.replace("{user_id}", str(target))
    if method == "GET":
        return client.get(url)
    return client.request(method, url, json=BODIES.get((method, path), {}))


def test_every_route_is_classified(app: FastAPI) -> None:
    assert _routes(app) == sorted(RULES)


def test_csrf_is_required_on_every_mutating_request(
    app: FastAPI, client: TestClient, login_as: Login, make_user: MakeUser
) -> None:
    target = make_user("viewer").id
    login_as(client, "admin")
    mutating = [key for key in RULES if key[0] != "GET"]
    del client.headers[CSRF_HEADER]
    for method, path in mutating:
        response = _call(client, method, path, target)
        assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_failed"), (
            method,
            path,
        )
    # Cookie and header agree but belong to no session (e.g. planted): refused wherever a session
    # is involved; the public endpoints only have the double-submit check.
    client.cookies.set(CSRF_COOKIE, "planted")
    client.headers[CSRF_HEADER] = "planted"
    for method, path in mutating:
        if RULES[(method, path)].access == "public":
            continue
        response = _call(client, method, path, target)
        assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_failed"), (
            method,
            path,
        )


#: The browser administration's prefixes (ADR-0014): every change is CSRF-checked too.
ADMIN_PREFIXES = ("/api/v1/admin", "/api/v1/settings")
ADMIN_ACTIONS = (("POST", "/api/v1/seals/{day}/seal"), ("POST", "/api/v1/credits/query"))


def test_csrf_is_required_on_every_change_of_the_browser_administration(
    app: FastAPI, client: TestClient, login_as: Login
) -> None:
    paths: dict[str, dict[str, Any]] = app.openapi()["paths"]
    mutating = [
        (method.upper(), path)
        for path, operations in paths.items()
        if path.startswith(ADMIN_PREFIXES)
        for method in operations
        if method.upper() != "GET"
    ]
    assert len(mutating) >= 4
    login_as(client, "admin")
    del client.headers[CSRF_HEADER]
    for method, path in [*mutating, *ADMIN_ACTIONS]:
        url = (
            path.replace("{day}", "2026-10-12")
            .replace("{request_id}", "0" * 32)
            .replace("{key}", "lookup.tolerance_seconds")
            .replace("{name}", "core-r1")
        )
        response = client.request(method, url, json={})
        assert (response.status_code, response.json()["error"]["code"]) == (403, "csrf_failed"), (
            method,
            path,
        )


def test_no_session_is_401_everywhere_but_the_public_endpoints(
    client: TestClient, make_user: MakeUser
) -> None:
    target = make_user("viewer").id
    client.get("/api/v1/health")
    client.headers[CSRF_HEADER] = client.cookies[CSRF_COOKIE]
    for (method, path), rule in RULES.items():
        response = _call(client, method, path, target)
        if rule.access == "public":
            code = response.json().get("error", {}).get("code")
            assert code != "not_authenticated", (method, path)
        else:
            assert (response.status_code, response.json()["error"]["code"]) == (
                401,
                "not_authenticated",
            ), (method, path)


def test_pending_second_factor_reaches_only_the_totp_endpoints(
    client: TestClient, login_as: Login, make_user: MakeUser
) -> None:
    target = make_user("viewer").id
    login_as(client, "admin", mfa_passed=False)
    for (method, path), rule in RULES.items():
        if rule.access != "signed_in":
            continue
        response = _call(client, method, path, target)
        assert (response.status_code, response.json()["error"]["code"]) == (
            401,
            "mfa_required",
        ), (method, path)
    assert client.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("role", ROLES)
def test_role_matrix_of_the_new_endpoints(
    client: TestClient, login_as: Login, make_user: MakeUser, role: str
) -> None:
    target = make_user("viewer").id
    login_as(client, role)
    for (method, path), rule in RULES.items():
        if rule.access != "signed_in" or path == "/api/v1/auth/password":
            continue
        response = _call(client, method, path, target)
        code = response.json().get("error", {}).get("code") if response.content else None
        if role in rule.roles:
            assert code not in ("forbidden", "not_authenticated"), (role, method, path, code)
        else:
            assert (response.status_code, code) == (403, "forbidden"), (role, method, path)


@pytest.mark.parametrize("role", ["auditor", "hr", "viewer"])
def test_no_idor_on_user_records(
    client: TestClient,
    login_as: Login,
    make_user: MakeUser,
    app_session_factory: sessionmaker[Session],
    role: str,
) -> None:
    """A non-administrator can neither read nor change any user record — not even its own."""
    victim = make_user("admin")
    me = login_as(client, role)
    for user_id in (victim.id, me.user_id):
        assert client.get(f"/api/v1/users/{user_id}").status_code == 403
        promote = client.patch(f"/api/v1/users/{user_id}", json={"role": "admin"})
        assert promote.json()["error"]["code"] == "forbidden"
        for action in ("reset-password", "reset-totp", "unlock"):
            assert client.post(f"/api/v1/users/{user_id}/{action}").status_code == 403
    with app_session_factory() as session:
        roles = {row.id: row.role for row in session.execute(select(User.id, User.role))}
    assert roles[me.user_id] == role
    assert roles[victim.id] == "admin"


def test_forced_password_change_blocks_everything_else(
    client: TestClient, login_as: Login, make_user: MakeUser
) -> None:
    target = make_user("viewer").id
    login_as(client, "admin", must_change_password=True)
    for (method, path), rule in RULES.items():
        if rule.access == "public" or rule.during_password_change:
            continue
        response = _call(client, method, path, target)
        assert (response.status_code, response.json()["error"]["code"]) == (
            403,
            "password_change_required",
        ), (method, path)
    assert client.get("/api/v1/auth/me").json()["next"] == "password_change"
    # /auth/totp answers 409 (second factor already passed), it changes nothing.
    assert client.post("/api/v1/auth/totp", json={"code": "123456"}).status_code == 409
    assert client.post("/api/v1/auth/logout").status_code == 204


def test_activity_is_recorded_even_when_a_request_is_refused(
    client: TestClient, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    """``last_seen_at`` is committed on its own (security.py), also for a 403 answer."""
    session = login_as(client, "viewer")
    with app_session_factory() as db:
        db.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(session.session_token))
            .values(last_seen_at=UserSession.last_seen_at - timedelta(minutes=10))
        )
        db.commit()
    with app_session_factory() as db:
        aged = db.get_one(UserSession, hash_token(session.session_token)).last_seen_at
    assert client.get("/api/v1/users").status_code == 403
    with app_session_factory() as db:
        seen = db.get_one(UserSession, hash_token(session.session_token)).last_seen_at
    assert seen - aged > timedelta(minutes=9)


# -- the KVKK notice is enforced by the server ----------------------------------------------------

PersonLevelDep = Annotated[CurrentUser, Depends(require_privacy_notice("lookup.run"))]
RecentPersonLevelDep = Annotated[
    CurrentUser, Depends(require_privacy_notice("lookup.run", recent=True))
]
AnyRoleDep = Annotated[CurrentUser, Depends(require_privacy_notice())]


def _with_probes(app: FastAPI) -> FastAPI:
    """Stand-ins for the person-level endpoints of later packages."""

    def person_level(user: PersonLevelDep) -> dict[str, str]:
        return {"user": user.username}

    def recent_person_level(user: RecentPersonLevelDep) -> dict[str, str]:
        return {"user": user.username}

    def any_role(user: AnyRoleDep) -> dict[str, str]:
        return {"user": user.username}

    app.add_api_route("/api/v1/probe/person", person_level, methods=["GET"])
    app.add_api_route("/api/v1/probe/recent", recent_person_level, methods=["GET"])
    app.add_api_route("/api/v1/probe/any", any_role, methods=["GET"])
    return app


def _code(response: Response) -> tuple[int, str | None]:
    return response.status_code, response.json().get("error", {}).get("code")


def test_person_level_endpoints_require_the_privacy_notice(
    app: FastAPI, login_as: Login, app_session_factory: sessionmaker[Session]
) -> None:
    _with_probes(app)
    auditor = TestClient(app, base_url="http://testserver")
    signed_in = login_as(auditor, "auditor")
    assert _code(auditor.get("/api/v1/probe/person")) == (403, "privacy_notice_required")
    assert _code(auditor.get("/api/v1/probe/any")) == (403, "privacy_notice_required")
    version = auditor.get("/api/v1/auth/me").json()["privacy_notice"]["version"]
    assert auditor.post("/api/v1/auth/privacy-notice", json={"version": version}).status_code == 200
    assert auditor.get("/api/v1/probe/person").json() == {"user": signed_in.username}
    # Recent re-authentication on top of the notice.
    assert _code(auditor.get("/api/v1/probe/recent")) == (403, "reauth_required")
    with app_session_factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(signed_in.session_token))
            .values(reauthenticated_at=datetime.now(UTC))
        )
        session.commit()
    assert auditor.get("/api/v1/probe/recent").status_code == 200
    # A new notice version asks again.
    with app_session_factory() as session:
        set_setting(session, "privacy.notice_version", f"{version}-2", updated_by="admin")
        session.commit()
    assert _code(auditor.get("/api/v1/probe/person")) == (403, "privacy_notice_required")

    # Permissions come first: hr may not run lookups, notice or not.
    hr = TestClient(app, base_url="http://testserver")
    login_as(hr, "hr")
    assert _code(hr.get("/api/v1/probe/person")) == (403, "forbidden")
    # Every role sees person-level data since the owner decision of 2026-10-20 (the viewer's
    # usage.anonymous shows names): the viewer accepts the notice too.
    assert {role for role in ROLES if privacy_required(role)} == set(ROLES)
    viewer = TestClient(app, base_url="http://testserver")
    login_as(viewer, "viewer")
    assert _code(viewer.get("/api/v1/probe/any")) == (403, "privacy_notice_required")
    version = viewer.get("/api/v1/auth/me").json()["privacy_notice"]["version"]
    assert viewer.post("/api/v1/auth/privacy-notice", json={"version": version}).status_code == 200
    assert viewer.get("/api/v1/probe/any").status_code == 200
    # Without a session it stays 401; a forced password change stays 403 first.
    assert _code(TestClient(app, base_url="http://testserver").get("/api/v1/probe/any")) == (
        401,
        "not_authenticated",
    )
    changing = TestClient(app, base_url="http://testserver")
    login_as(changing, "auditor", must_change_password=True)
    assert _code(changing.get("/api/v1/probe/person")) == (403, "password_change_required")


# -- database connections -------------------------------------------------------------------------


@pytest.fixture
def small_pool_app(
    api_config: Config, db_engine: Engine
) -> Iterator[Callable[..., tuple[FastAPI, Engine]]]:
    """The app on this test's database, with an engine whose pool the test chooses."""
    engines: list[Engine] = []

    def build(**pool: Any) -> tuple[FastAPI, Engine]:
        engine = engine_from_url(db_engine.url, **pool)
        engines.append(engine)
        return create_app(api_config, engine), engine

    yield build
    for engine in engines:
        engine.dispose()


def _factory(app: FastAPI) -> sessionmaker[Session]:
    state: AppState = app.state.arci
    return state.session_factory()


def test_a_request_never_holds_two_pooled_connections(
    small_pool_app: Callable[..., tuple[FastAPI, Engine]],
) -> None:
    """With one connection in the pool every authenticated request still works, and at once:
    recording activity must not wait for a second connection (pool starvation, see below)."""
    app, _engine = small_pool_app(pool_size=1, max_overflow=0, pool_timeout=3)
    factory = _factory(app)
    client = TestClient(app, base_url="http://testserver")  # no lifespan: no background flush
    signed_in = api_testing.login(client, factory, api_testing.make_user(factory, "admin"))
    with factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(signed_in.session_token))
            .values(last_seen_at=UserSession.last_seen_at - timedelta(minutes=10))
        )
        session.commit()
    started = time.monotonic()
    assert client.get("/api/v1/auth/me").status_code == 200
    assert client.get("/api/v1/users").status_code == 200
    assert client.get("/api/v1/users", headers={BACKGROUND_HEADER: "1"}).status_code == 200
    assert time.monotonic() - started < 2.5  # no request waited for the pool timeout
    with factory() as session:
        seen = session.get_one(UserSession, hash_token(signed_in.session_token)).last_seen_at
    assert datetime.now(UTC) - seen < timedelta(minutes=1)


def test_concurrent_requests_do_not_exhaust_the_connection_pool(
    small_pool_app: Callable[..., tuple[FastAPI, Engine]],
) -> None:
    """More simultaneous requests than connections (SQLAlchemy's default pool: 5 + 10
    overflow): requests may queue for a connection, but none fails. When recording activity
    took a second connection, every request held one and waited for another, and most of them
    failed with 500 after ``pool_timeout`` (found in review: 33 of 40)."""
    app, engine = small_pool_app(pool_size=5, max_overflow=10, pool_timeout=10)
    factory = _factory(app)
    count = 32
    clients = [TestClient(app, base_url="http://testserver") for _ in range(count)]
    for each in clients:
        api_testing.login(each, factory, api_testing.make_user(factory, "viewer"))
    barrier = threading.Barrier(count, timeout=30)
    results: list[tuple[int, str]] = []
    lock = threading.Lock()

    def call(each: TestClient) -> None:
        barrier.wait()
        try:
            response = each.get("/api/v1/auth/me")
            outcome = (response.status_code, response.text[:200])
        except Exception as exc:  # a pool timeout raises through TestClient
            outcome = (0, repr(exc)[:200])
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=call, args=(each,)) for each in clients]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    failures = [outcome for outcome in results if outcome[0] != 200]
    assert len(results) == count
    assert failures == []
    assert isinstance(engine.pool, QueuePool)
    assert engine.pool.checkedout() == 0
