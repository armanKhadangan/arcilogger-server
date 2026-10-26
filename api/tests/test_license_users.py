"""The licence's users limit (licensing contract 6.6, [C-11], [C-14]; test B-T12).

``POST /users`` and re-enabling a user are refused with 409 ``license_limit`` while the active
users reach the limit (recounted under the user-management lock, so quick repeated creates
cannot pass it); the refusal is audited. The first administrator, sign-in, deactivation and
existing users are never limited. ``arcilogger-api create-admin`` respects the limit except for
a true recovery or with ``--deactivate``.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update
from sqlalchemy.orm import Session, sessionmaker

import arcilogger_db
from arcilogger_api import cli as api_cli
from arcilogger_api.auth import admin_cli, firstrun
from arcilogger_api.auth.testing import ensure_secret_key, prime_csrf
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_core.license.testing import make_license
from arcilogger_db.models import AuditLog, User

pytestmark = pytest.mark.license_live

Login = Callable[..., LoggedIn]
USERS = "/api/v1/users"
PASSWORD = "Sarı limon ağacı 1923"


def error(response: Any) -> tuple[int, str]:
    return response.status_code, response.json()["error"]["code"]


def new_user(client: TestClient, name: str) -> Any:
    return client.post(USERS, json={"username": name, "display_name": name, "role": "viewer"})


def active_users(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(User).where(User.active)) or 0)


def licensed(client: TestClient, licence: Any, users: int) -> None:
    data = make_license(
        server_ids=[licence.server_id], users=users, license_id=f"ARCI-L-20261001-TEST00{users:02d}"
    )
    response = licence.upload(client, data)
    assert response.status_code == 200, response.text


def test_the_limit_refuses_create_and_reactivate(
    client: TestClient,
    login_as: Login,
    make_user: Callable[..., User],
    licence: Any,
    app_session_factory: sessionmaker[Session],
) -> None:
    admin = licence.admin(client, login_as)
    make_user("viewer")
    disabled = make_user("viewer", active=False)
    licensed(client, licence, 3)
    assert new_user(client, "ucuncu").status_code == 201  # 2 active -> 3
    response = new_user(client, "dorduncu")
    assert error(response) == (409, "license_limit")
    body = response.json()["error"]
    assert body["details"] == {"limit": "users", "max": 3, "used": 3}
    assert "(3/3)" in body["message_tr"]
    assert "(3/3)" in body["message_en"]
    (refused,) = licence.audits("license.limit_refused")
    assert refused.target == "user:dorduncu"
    assert refused.actor_username == admin.username
    assert refused.details == {
        "limit": "users",
        "max": 3,
        "used": 3,
        "username": "dorduncu",
        "operation": "create",
    }
    response = client.patch(f"{USERS}/{disabled.id}", json={"active": True})
    assert error(response) == (409, "license_limit")
    assert licence.audits("license.limit_refused")[-1].details["operation"] == "reactivate"
    # Deactivating one user makes room again.
    with app_session_factory() as session:
        third = session.scalars(select(User).where(User.username == "ucuncu")).one()
        third_id = third.id
    assert client.patch(f"{USERS}/{third_id}", json={"active": False}).status_code == 200
    assert new_user(client, "dorduncu").status_code == 201
    assert active_users(app_session_factory) == 3


def test_quick_repeated_creates_are_recounted(
    client: TestClient, login_as: Login, make_user: Callable[..., User], licence: Any
) -> None:
    licence.admin(client, login_as)
    licensed(client, licence, 3)
    make_user("viewer")  # 2 active
    # The cache still holds the count of the upload (1): the create recounts in its lock.
    assert client.get("/api/v1/license").json()["limits"]["users"]["used"] == 1
    assert new_user(client, "bir").status_code == 201
    assert error(new_user(client, "iki")) == (409, "license_limit")  # recounted, not cached
    assert client.get("/api/v1/license").json()["limits"]["users"]["full"] is True


def test_existing_users_keep_working_over_the_limit(
    app: Any,
    client: TestClient,
    login_as: Login,
    make_user: Callable[..., User],
    licence: Any,
) -> None:
    licence.admin(client, login_as)
    make_user("viewer", username="izleyici", password=PASSWORD)
    make_user("auditor")
    licensed(client, licence, 1)  # a smaller licence: 3 active users, limit 1
    document = client.get("/api/v1/license").json()
    assert document["limits"]["users"] == {
        "max": 1,
        "used": 3,
        "unconfigured": 0,
        "exceeded": True,
        "full": True,
    }
    other = TestClient(app, base_url="http://testserver")
    other.get("/api/v1/health")
    other.headers["X-CSRF-Token"] = other.cookies["arci_csrf"]
    response = other.post("/api/v1/auth/login", json={"username": "izleyici", "password": PASSWORD})
    assert response.status_code == 200, response.text
    assert client.get(USERS).status_code == 200
    assert client.get("/api/v1/dashboard/summary").status_code == 200


def test_the_first_administrator_is_never_limited(
    client: TestClient,
    make_user: Callable[..., User],
    licence: Any,
    api_config: Config,
    app_session_factory: sessionmaker[Session],
) -> None:
    ensure_secret_key(api_config.secrets_dir)
    licence.trial_over()  # unlicensed: users max 3 ...
    for _ in range(3):  # ... and already 3 active users, none of them an administrator
        make_user("viewer")
    with app_session_factory() as session:
        token, _ = firstrun.issue_setup_token(session, at=datetime.now(UTC))
        session.commit()
    prime_csrf(client)
    response = client.post(
        "/api/v1/auth/setup/admin",
        json={"token": token, "username": "ilk", "display_name": "İlk", "password": PASSWORD},
    )
    assert response.status_code == 201, response.text
    assert active_users(app_session_factory) == 4
    assert licence.audits("license.limit_refused") == []


def test_removing_the_licence_keeps_a_limit(
    client: TestClient, login_as: Login, make_user: Callable[..., User], licence: Any
) -> None:
    licence.admin(client, login_as)
    licence.trial_over()
    licensed(client, licence, 10)
    make_user("viewer")
    make_user("viewer")
    assert client.delete("/api/v1/license").status_code == 204
    document = client.get("/api/v1/license").json()
    assert document["status"] == "trial_expired"
    assert document["limits"]["users"]["max"] == 3  # not unlimited
    assert error(new_user(client, "fazla")) == (409, "license_limit")


# -- create-admin ---------------------------------------------------------------------------------


def answers(*values: str) -> Callable[[str], str]:
    queue = list(values)

    def ask(prompt: str) -> str:
        return queue.pop(0)

    return ask


@pytest.fixture
def cli_engine(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    monkeypatch.setattr(admin_cli, "engine_for", lambda config: db_engine)
    return db_engine


def full_limit(
    client: TestClient, login_as: Login, make_user: Callable[..., User], licence: Any
) -> None:
    """Limit 3 with 3 active users, one of them an administrator who can sign in."""
    licence.admin(client, login_as)
    make_user("viewer", username="izleyici")
    make_user("auditor", username="denetci")
    licensed(client, licence, 3)


def create_admin(config: Config, name: str, **options: Any) -> int:
    result: int = admin_cli.create_admin(
        config,
        ask=answers(name, "Kurtarma Yöneticisi"),
        ask_secret=answers(PASSWORD, PASSWORD),
        **options,
    )
    return result


def user_entries(factory: sessionmaker[Session], action: str) -> list[AuditLog]:
    with factory() as session:
        rows = list(
            session.scalars(select(AuditLog).where(AuditLog.action == action).order_by(AuditLog.id))
        )
        for row in rows:
            session.expunge(row)
        return rows


def test_create_admin_respects_the_limit(
    client: TestClient,
    login_as: Login,
    make_user: Callable[..., User],
    licence: Any,
    api_config: Config,
    cli_engine: Engine,
    app_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    full_limit(client, login_as, make_user, licence)
    assert create_admin(api_config, "kurtarma") == admin_cli.EXIT_REFUSED
    out = capsys.readouterr().out
    assert "Kullanıcı sayısı lisans sınırında (3/3)" in out
    assert "[The number of users is at the licence limit (3/3)" in out
    (refused,) = licence.audits("license.limit_refused")
    assert refused.details["operation"] == "create_admin"
    assert refused.target == "user:kurtarma"
    assert "os_user" in refused.details
    assert active_users(app_session_factory) == 3


def test_create_admin_with_deactivate_is_net_zero(
    client: TestClient,
    login_as: Login,
    make_user: Callable[..., User],
    licence: Any,
    api_config: Config,
    cli_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    full_limit(client, login_as, make_user, licence)
    assert create_admin(api_config, "kurtarma", deactivate="izleyici") == admin_cli.EXIT_OK
    assert active_users(app_session_factory) == 3
    with app_session_factory() as session:
        retired = session.scalars(select(User).where(User.username == "izleyici")).one()
        assert retired.active is False
    update_entry = user_entries(app_session_factory, "user.update")[-1]
    assert update_entry.details["via"] == "create_admin"
    assert update_entry.details["changes"] == {"active": {"old": True, "new": False}}
    assert create_admin(api_config, "ikinci", deactivate="yok-boyle") == admin_cli.EXIT_REFUSED


def test_create_admin_recovery_passes_the_limit(
    client: TestClient,
    login_as: Login,
    make_user: Callable[..., User],
    licence: Any,
    api_config: Config,
    cli_engine: Engine,
    app_session_factory: sessionmaker[Session],
) -> None:
    licence.admin(client, login_as)  # the only administrator ...
    make_user("viewer")
    make_user("auditor")
    licensed(client, licence, 3)
    with app_session_factory() as session:  # ... is locked out
        session.execute(
            update(User)
            .where(User.role == "admin")
            .values(locked_until=datetime.now(UTC) + timedelta(hours=1))
        )
        session.commit()
    assert create_admin(api_config, "kurtarma") == admin_cli.EXIT_OK
    created = user_entries(app_session_factory, "user.create")[-1]
    assert created.details["license_over_limit"] is True
    assert active_users(app_session_factory) == 4


def test_create_admin_option_is_parsed(monkeypatch: pytest.MonkeyPatch, api_config: Config) -> None:
    seen: list[Any] = []

    def create(config: Config, **options: Any) -> int:
        seen.append(options)
        return 0

    monkeypatch.setattr(api_cli, "load_config", lambda path: api_config)
    monkeypatch.setattr(admin_cli, "create_admin", create)
    # The configuration's own folder (the secret store must be next to it, ADR-0014 D0-2).
    config = ["--config", str(api_config.secrets_dir / "arcilogger.toml")]
    assert api_cli.main([*config, "create-admin", "--deactivate", "izleyici"]) == 0
    assert api_cli.main([*config, "create-admin"]) == 0
    assert seen == [{"deactivate": "izleyici"}, {}]


def test_create_admin_and_users_without_the_licence_code(
    client: TestClient,
    login_as: Login,
    make_user: Callable[..., User],
    licence: Any,
    api_config: Config,
    cli_engine: Engine,
    app_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """B-T31: a broken licence subsystem never refuses a user (no limit at all)."""
    full_limit(client, login_as, make_user, licence)
    for name in [n for n in sys.modules if n.startswith("arcilogger_core.license")]:
        monkeypatch.setitem(sys.modules, name, None)
    monkeypatch.setitem(sys.modules, "arcilogger_db.licensing", None)
    monkeypatch.delattr(arcilogger_db, "licensing", raising=False)
    licence.invalidate()
    assert create_admin(api_config, "kurtarma") == admin_cli.EXIT_OK
    assert new_user(client, "bir-daha").status_code == 201
    assert active_users(app_session_factory) == 5
    assert licence.audits("license.limit_refused") == []
