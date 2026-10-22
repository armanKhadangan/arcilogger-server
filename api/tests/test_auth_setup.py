"""First-run setup (one-time token → first administrator → TOTP enrolment at first sign-in) and
the ``arcilogger-api setup-token`` / ``create-admin`` console commands."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from sqlalchemy import Engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import cli
from arcilogger_api.auth import admin_cli, firstrun
from arcilogger_api.auth.passwords import verify_password
from arcilogger_api.auth.testing import ensure_secret_key, prime_csrf, sync_csrf
from arcilogger_api.security import hash_token
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_db.models import AuditLog, Setting, User
from arcilogger_db.settings import set_setting

PASSWORD = "Mavi kuşlar sabah uçar"
MakeUser = Callable[..., User]


@pytest.fixture(autouse=True)
def _secret_key(api_config: Config) -> None:
    ensure_secret_key(api_config.secrets_dir)


@pytest.fixture
def cli_engine(db_engine: Engine, monkeypatch: pytest.MonkeyPatch) -> Engine:
    """The console commands work on this test's database."""
    monkeypatch.setattr(admin_cli, "engine_for", lambda config: db_engine)
    return db_engine


@pytest.fixture
def config_file(api_config: Config, tmp_path: Path) -> Path:
    path = tmp_path / "arcilogger.toml"
    path.write_text(
        f'data_dir = "{api_config.data_dir.as_posix()}"\n'
        f'secrets_dir = "{api_config.secrets_dir.as_posix()}"\n',
        encoding="utf-8",
    )
    return path


def _audit(factory: sessionmaker[Session], prefix: str = "") -> list[AuditLog]:
    with factory() as session:
        rows = list(session.scalars(select(AuditLog).order_by(AuditLog.id)))
        for row in rows:
            session.expunge(row)
    return [row for row in rows if row.action.startswith(prefix)]


def _issue(factory: sessionmaker[Session]) -> str:
    with factory() as session:
        token, _ = firstrun.issue_setup_token(session, at=datetime.now(UTC))
        session.commit()
    return token


def _setup(client: TestClient, token: str, **overrides: str) -> Response:
    prime_csrf(client)
    body = {
        "token": token,
        "username": "tolga",
        "display_name": "Tolga Ekinci",
        "password": PASSWORD,
        **overrides,
    }
    return client.post("/api/v1/auth/setup/admin", json=body)


def test_first_run_from_token_to_signed_in_administrator(
    client: TestClient,
    cli_engine: Engine,
    config_file: Path,
    app_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert client.get("/api/v1/auth/setup/status").json() == {
        "needs_setup": True,
        "token_active": False,
        "idle_timeout_minutes": 15,
    }
    assert cli.main(["setup-token", "--config", str(config_file)]) == 0
    printed = capsys.readouterr().out
    token = next(line.strip() for line in printed.splitlines() if line.startswith("    "))
    assert len(token) == 43  # 256 random bits
    assert "24 saat" in printed
    with app_session_factory() as session:
        stored = session.scalar(select(Setting).where(Setting.key == firstrun.SETUP_TOKEN_KEY))
        assert stored is not None
        assert stored.value["hash"] == hash_token(token)  # only the hash is stored
        assert token not in json.dumps(stored.value)
        assert stored.updated_by == ":system"
    (created,) = _audit(app_session_factory, "setup.token_create")
    assert (created.actor_username, set(created.details)) == ("system", {"expires_at", "os_user"})
    assert token not in json.dumps(created.details)
    assert client.get("/api/v1/auth/setup/status").json()["token_active"] is True

    response = _setup(client, token, username="Tolga")
    assert response.status_code == 201, response.text
    assert response.json()["user"]["username"] == "tolga"
    assert response.json()["next"] == "login"
    with app_session_factory() as session:
        admin = session.scalars(select(User)).one()
        assert (admin.role, admin.must_change_password, admin.totp_enabled) == (
            "admin",
            False,
            False,
        )
        assert verify_password(admin.password_hash, PASSWORD).ok
        assert session.get(Setting, firstrun.SETUP_TOKEN_KEY) is None  # used up
    (entry,) = _audit(app_session_factory, "setup.admin_create")
    assert entry.details == {"username": "tolga", "role": "admin"}
    assert client.get("/api/v1/auth/setup/status").json() == {
        "needs_setup": False,
        "token_active": False,
        "idle_timeout_minutes": 15,
    }
    # Once only.
    again = _setup(client, token, username="ikinci")
    assert (again.status_code, again.json()["error"]["code"]) == (409, "setup_done")
    # Its own exit code: the installer tells "an administrator exists" apart from a failure.
    assert cli.main(["setup-token", "--config", str(config_file)]) == admin_cli.EXIT_SETUP_DONE == 4
    refused = capsys.readouterr().out
    assert "create-admin" in refused
    assert not [line for line in refused.splitlines() if line.startswith(" ")], "no token line"
    # First sign-in: TOTP enrolment is required for the administrator.
    prime_csrf(client)
    login = client.post("/api/v1/auth/login", json={"username": "tolga", "password": PASSWORD})
    assert login.json()["next"] == "totp_setup"
    sync_csrf(client)
    assert client.get("/api/v1/auth/totp/setup").status_code == 200


@pytest.mark.parametrize("problem", ["mismatch", "expired", "missing"])
def test_invalid_setup_tokens_are_refused_and_audited(
    client: TestClient, app_session_factory: sessionmaker[Session], problem: str
) -> None:
    token = "not-issued"
    if problem != "missing":
        token = _issue(app_session_factory)
    if problem == "mismatch":
        token = token[:-1] + ("A" if token[-1] != "A" else "B")
    if problem == "expired":
        with app_session_factory() as session:
            value = session.get_one(Setting, firstrun.SETUP_TOKEN_KEY).value
            value = {**value, "expires_at": (datetime.now(UTC) - timedelta(1)).isoformat()}
            set_setting(session, firstrun.SETUP_TOKEN_KEY, value, updated_by=":system")
            session.commit()
    response = _setup(client, token)
    assert (response.status_code, response.json()["error"]["code"]) == (403, "setup_token_invalid")
    assert "arcilogger-api setup-token" in response.json()["error"]["message_tr"]
    assert [e.details for e in _audit(app_session_factory, "setup.")] == [{"reason": problem}]
    with app_session_factory() as session:
        assert session.scalars(select(User)).first() is None


def test_setup_validates_name_and_password(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    token = _issue(app_session_factory)
    bad_name = _setup(client, token, username="Ünal Öz")
    assert (bad_name.status_code, bad_name.json()["error"]["code"]) == (422, "username_invalid")
    weak = _setup(client, token, password="Tolga-Ekinci-1")  # gitleaks:allow (a test value)
    assert weak.json()["error"]["code"] == "password_rejected"
    assert [d["rule"] for d in weak.json()["error"]["details"]] == ["personal"]
    control = _setup(client, token, display_name="Tolga\x00")
    assert control.status_code == 422
    with app_session_factory() as session:  # a non-administrator account with that name
        session.add(User(username="tolga", display_name="A", role="viewer", password_hash="!"))
        session.commit()
    taken = _setup(client, token)
    assert (taken.status_code, taken.json()["error"]["code"]) == (409, "username_taken")
    # The token survives refused attempts with a valid token.
    assert _setup(client, token, username="tolga.ekinci").status_code == 201


def test_setup_is_refused_when_any_admin_exists(
    client: TestClient, make_user: MakeUser, app_session_factory: sessionmaker[Session]
) -> None:
    token = _issue(app_session_factory)
    make_user("admin", active=False)
    assert _setup(client, token).json()["error"]["code"] == "setup_done"
    with app_session_factory() as session, pytest.raises(firstrun.SetupDoneError):
        firstrun.issue_setup_token(session, at=datetime.now(UTC))


def test_setup_status_with_a_corrupt_token_value(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    with app_session_factory() as session:
        set_setting(session, firstrun.SETUP_TOKEN_KEY, {"hash": "x"}, updated_by=":system")
        session.commit()
    assert client.get("/api/v1/auth/setup/status").json()["token_active"] is False
    with app_session_factory() as session:
        value = {"hash": "x", "expires_at": "not a date"}
        set_setting(session, firstrun.SETUP_TOKEN_KEY, value, updated_by=":system")
        session.commit()
    assert client.get("/api/v1/auth/setup/status").json()["token_active"] is False


def test_setup_status_tells_the_sign_in_page_the_idle_timeout(
    client: TestClient, app_session_factory: sessionmaker[Session]
) -> None:
    """The sign-in page shows "Oturum N dk hareketsizlikte kapanır" before anybody signed in
    (spec 12.3): the public status carries the current ``auth.idle_minutes``."""
    assert client.get("/api/v1/auth/setup/status").json()["idle_timeout_minutes"] == 15
    with app_session_factory() as session:
        set_setting(session, "auth.idle_minutes", 20, updated_by="tolga")
        session.commit()
    assert client.get("/api/v1/auth/setup/status").json()["idle_timeout_minutes"] == 20


# -- create-admin ---------------------------------------------------------------------------------


def _answers(*values: str) -> Callable[[str], str]:
    queue = list(values)

    def ask(prompt: str) -> str:
        if not queue:
            raise EOFError
        return queue.pop(0)

    return ask


def test_create_admin_command(
    cli_engine: Engine,
    api_config: Config,
    app_session_factory: sessionmaker[Session],
    layout: Layout,
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = admin_cli.create_admin(
        api_config,
        ask=_answers("Kurtarma", "Kurtarma Yöneticisi"),
        ask_secret=_answers(PASSWORD, PASSWORD),
    )
    assert result == 0
    assert "Yönetici 'kurtarma' oluşturuldu" in capsys.readouterr().out
    with app_session_factory() as session:
        user = session.scalars(select(User).where(User.username == "kurtarma")).one()
        assert (user.role, user.display_name, user.must_change_password) == (
            "admin",
            "Kurtarma Yöneticisi",
            False,
        )
    (entry,) = _audit(app_session_factory, "user.create")
    assert entry.details["via"] == "cli"
    assert entry.details["role"] == "admin"
    assert entry.target == f"user:{user.id}"
    # The entry reached the audit spool (the console command flushes after its commit).
    spooled = [
        json.loads(line)
        for path in (layout.spool / "_audit").glob("*.jsonl")
        for line in path.read_bytes().splitlines()
    ]
    assert [e["action"] for e in spooled] == ["user.create"]

    taken = admin_cli.create_admin(
        api_config,
        ask=_answers("kurtarma", "Başka"),
        ask_secret=_answers(PASSWORD, PASSWORD),
    )
    assert taken == 1


@pytest.mark.parametrize(
    ("answers", "secrets", "expected", "message"),
    [
        (("Ünal", "Ad"), (PASSWORD, PASSWORD), 2, "Geçersiz kullanıcı adı"),
        (("ayse", "\x07"), (PASSWORD, PASSWORD), 2, "Geçersiz görünen ad"),
        (("ayse", "Ayşe"), (PASSWORD, "başka bir parola 12"), 2, "Parolalar aynı değil"),
        (("ayse", "Ayşe"), ("qwerty123456", "qwerty123456"), 1, "Parola kabul edilmedi"),
        (("ayse",), (), 2, ""),
        (("ayse", "Ayşe"), (PASSWORD,), 2, ""),
    ],
)
def test_create_admin_refusals(
    cli_engine: Engine,
    api_config: Config,
    app_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
    answers: tuple[str, ...],
    secrets: tuple[str, ...],
    expected: int,
    message: str,
) -> None:
    result = admin_cli.create_admin(
        api_config, ask=_answers(*answers), ask_secret=_answers(*secrets)
    )
    assert result == expected
    assert message in capsys.readouterr().out
    with app_session_factory() as session:
        assert session.scalars(select(User)).first() is None


def test_create_admin_checks_the_organisation_name_only_when_one_is_set(
    cli_engine: Engine,
    api_config: Config,
    app_session_factory: sessionmaker[Session],
    capsys: pytest.CaptureFixture[str],
) -> None:
    named = "Örnek bahçede kuşlar uçar"  # a word of site_name "Örnek A.Ş." (api_config)
    refused = admin_cli.create_admin(
        api_config, ask=_answers("ayse", "Ayşe"), ask_secret=_answers(named, named)
    )
    assert refused == 1
    assert "şirket adını" in capsys.readouterr().out
    unnamed = Config.model_validate({**api_config.model_dump(), "site_name": ""})
    created = admin_cli.create_admin(
        unnamed, ask=_answers("ayse", "Ayşe"), ask_secret=_answers(named, named)
    )
    assert created == 0
    with app_session_factory() as session:
        assert session.scalars(select(User.username)).all() == ["ayse"]


def test_console_commands_report_an_unreachable_database(
    api_config: Config, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class DownEngine:
        def dispose(self) -> None:
            pass

    def down_factory(engine: Any, **info: Any) -> Any:
        def open_session() -> Any:
            raise OperationalError("SELECT 1", {}, Exception("connection refused\nmore"))

        return open_session

    monkeypatch.setattr(admin_cli, "engine_for", lambda config: DownEngine())
    monkeypatch.setattr(admin_cli, "session_factory", down_factory)
    assert admin_cli.setup_token(api_config) == 3
    assert "Veritabanına ulaşılamadı: connection refused" in capsys.readouterr().out

    def broken_key(config: Config) -> Engine:
        raise SecretStoreError("cannot decrypt 'database.password': wrong key file?")

    monkeypatch.setattr(admin_cli, "engine_for", broken_key)
    assert admin_cli.setup_token(api_config) == 2


def test_cli_parses_the_new_commands(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    calls: list[str] = []

    def recorder(name: str) -> Callable[[Config], int]:
        def run(config: Config) -> int:
            calls.append(name)
            return 0

        return run

    monkeypatch.setattr(admin_cli, "setup_token", recorder("token"))
    monkeypatch.setattr(admin_cli, "create_admin", recorder("admin"))
    assert cli.main(["setup-token", "--config", str(config_file)]) == 0
    assert cli.main(["--config", str(config_file), "create-admin"]) == 0
    assert calls == ["token", "admin"]
    assert cli.main(["create-admin", "--config", str(tmp_path / "missing.toml")]) == 2
