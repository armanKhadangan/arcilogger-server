"""The activity commit is asynchronous, every audit write is not (#119, deviation #119-1).

:func:`arcilogger_api.security._record_activity` commits ``last_seen_at`` with ``SET LOCAL
synchronous_commit = off``: a crash can lose the newest ``last_seen_at``, never an audit entry.
These tests watch every transaction of the app's engine and read ``synchronous_commit`` on the
same database connection (a cursor of the DBAPI connection, which SQLAlchemy's events do not
see): at the first statement of each transaction and just before its commit.

* The activity transaction commits with ``off``; the next transaction on that connection (the
  endpoint's) starts with ``on`` again.
* Every transaction that wrote an audit entry (``arcilogger_db.audit.append_entry``: the chain
  lock, then the ``audit_log`` row) commits with ``on``: sign-in, a search, a settings change, a
  user created, a background request. It is checked at the commit, not at the insert, so an
  audit write that landed in the activity transaction (a dependency writing to the request's
  session before authentication) fails it too.
* Nowhere else in the product or its deployment files is ``synchronous_commit`` set: the
  worker's and ingest's audit writes run on engines these tests do not watch.

The app runs on a pool of one connection: every transaction of a request, the endpoint's
included, runs on the connection the activity was committed on, so a setting that outlived its
transaction would reach the endpoint's audit write.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import Connection, Engine, event, func, select, update
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api import testing as api_testing
from arcilogger_api.app import create_app
from arcilogger_api.audit import record_audit
from arcilogger_api.auth.testing import ensure_secret_key, prime_csrf, sync_csrf
from arcilogger_api.deps import AppState, DbSession
from arcilogger_api.search.testing import sign_in
from arcilogger_api.security import BACKGROUND_HEADER, CurrentUserDep, hash_token
from arcilogger_api.testing import LoggedIn
from arcilogger_core.config import Config
from arcilogger_db.audit import AUDIT_LOCK_KEY
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import AuditLog, UserSession
from arcilogger_db.settings import set_setting

PASSWORD = "Mavi kuşlar sabah uçar"


@dataclass
class Transaction:
    """One transaction of the engine, in the order they ended."""

    #: The DBAPI connection it ran on (its ``id``).
    connection: int
    #: ``synchronous_commit`` at its first statement and just before its commit (``None``:
    #: rolled back).
    first: str | None = None
    committed: str | None = None
    #: It recorded activity (the ``last_seen_at`` update of ``_record_activity``).
    activity: bool = False
    #: Audit entries it wrote: the chain lock taken and ``audit_log`` rows inserted.
    locks: int = 0
    audits: int = 0
    statements: list[str] = field(default_factory=list)


def _setting(connection: Any) -> str:
    with connection.cursor() as cursor:
        cursor.execute("SELECT current_setting('synchronous_commit')")
        row = cursor.fetchone()
    assert row is not None
    return str(row[0])


class Watch:
    """Every transaction of ``engine`` (``ended``) and the setting each one saw."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        #: The transaction in progress of each SQLAlchemy connection (by ``id``).
        self.open: dict[int, Transaction] = {}
        self.ended: list[Transaction] = []
        event.listen(engine, "before_cursor_execute", self._statement)
        event.listen(engine, "commit", self._commit)
        event.listen(engine, "rollback", self._rollback)

    def close(self) -> None:
        event.remove(self.engine, "before_cursor_execute", self._statement)
        event.remove(self.engine, "commit", self._commit)
        event.remove(self.engine, "rollback", self._rollback)

    def clear(self) -> None:
        self.ended.clear()

    def _statement(
        self,
        conn: Connection,
        cursor: Any,
        statement: str,
        parameters: Any,
        context: Any,
        executemany: bool,
    ) -> None:
        current = self.open.get(id(conn))
        if current is None:
            current = self.open[id(conn)] = Transaction(id(cursor.connection))
        flat = " ".join(statement.split())
        # SET TRANSACTION must be a transaction's first statement: read the setting after it.
        if current.first is None and not flat.startswith("SET TRANSACTION"):
            current.first = _setting(cursor.connection)
        current.statements.append(flat)
        values = parameters.values() if isinstance(parameters, dict) else parameters or ()
        if "pg_advisory_xact_lock" in flat and AUDIT_LOCK_KEY in tuple(values):
            current.locks += 1
        if flat.startswith("INSERT INTO audit_log"):
            current.audits += 1
        if flat.startswith("UPDATE user_sessions SET last_seen_at="):
            current.activity = True

    def _commit(self, conn: Connection) -> None:
        current = self.open.pop(id(conn), None)
        if current is not None:
            current.committed = _setting(conn.connection.dbapi_connection)
            self.ended.append(current)

    def _rollback(self, conn: Connection) -> None:
        current = self.open.pop(id(conn), None)
        if current is not None:
            self.ended.append(current)

    def audited(self) -> list[Transaction]:
        return [t for t in self.ended if t.locks or t.audits]

    def activities(self) -> list[Transaction]:
        return [t for t in self.ended if t.activity]

    def after(self, transaction: Transaction) -> Transaction:
        """The next transaction on ``transaction``'s connection."""
        later = self.ended[self.ended.index(transaction) + 1 :]
        return next(t for t in later if t.connection == transaction.connection)


@dataclass
class Site:
    app: FastAPI
    client: TestClient
    factory: sessionmaker[Session]
    watch: Watch


@pytest.fixture
def api_config(tmp_path: Path) -> Config:
    """The tests' site with the FortiGate and the MikroTik (the search reads their tables)."""
    return Config.model_validate(
        {
            "site_name": api_testing.EXAMPLE_SITE_NAME,
            "data_dir": str(tmp_path / "data"),
            "secrets_dir": str(tmp_path),
            "api": {"secure_cookies": False},
            "sources": [
                {"name": "fortigate", "type": "fortigate", "address": "172.31.255.1"},
                {"name": "core-r1", "type": "mikrotik", "address": "172.20.10.1"},
            ],
        }
    )


@pytest.fixture
def site(api_config: Config, db_engine: Engine) -> Iterator[Site]:
    """The app on a pool of one connection, watched; no lifespan, so no background flush."""
    ensure_secret_key(api_config.secrets_dir)
    engine = engine_from_url(db_engine.url, pool_size=1, max_overflow=0, pool_timeout=10)
    app = create_app(api_config, engine)
    state: AppState = app.state.arci
    factory = state.session_factory()
    with factory() as session:
        set_setting(session, "search.runs_per_minute", 600, updated_by="test")
        session.commit()
    watch = Watch(engine)
    try:
        yield Site(app, TestClient(app, base_url="http://testserver"), factory, watch)
    finally:
        watch.close()
        engine.dispose()


def _recent(factory: sessionmaker[Session], who: LoggedIn) -> None:
    """Re-authenticated just now, with activity older than a request's (it gets recorded)."""
    with factory() as session:
        session.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(who.session_token))
            .values(
                reauthenticated_at=datetime.now(UTC),
                last_seen_at=UserSession.last_seen_at - timedelta(minutes=10),
            )
        )
        session.commit()


def _audit_count(factory: sessionmaker[Session]) -> int:
    with factory() as session:
        return int(session.scalar(select(func.count()).select_from(AuditLog)) or 0)


def _search(client: TestClient, **headers: str) -> None:
    now = datetime.now(UTC)
    body = {
        "q": "",
        "from": (now - timedelta(hours=1)).isoformat(),
        "to": (now + timedelta(minutes=1)).isoformat(),
        "limit": 10,
    }
    response = client.post("/api/v1/search", json=body, headers=headers)
    assert response.status_code == 200, response.text


def test_the_activity_commit_alone_is_asynchronous(site: Site) -> None:
    admin = sign_in(site.client, site.factory)
    _recent(site.factory, admin)
    site.watch.clear()
    change = {"changes": [{"key": "lookup.tolerance_seconds", "old": 60, "value": 90}]}
    assert site.client.patch("/api/v1/settings", json=change).status_code == 200
    (activity,) = site.watch.activities()
    assert activity.committed == "off", activity.statements
    assert (activity.locks, activity.audits) == (0, 0)  # no audit entry rides on it
    endpoint = site.watch.after(activity)
    assert endpoint.first == "on", endpoint.statements
    assert endpoint.audits == 1  # the endpoint's transaction: settings.change, synchronous
    assert endpoint.committed == "on"


def test_every_audit_write_commits_synchronously(site: Site) -> None:
    """Representative audited requests, each after its activity commit (on the one
    connection); the sign-in and a background request record no activity."""
    before = _audit_count(site.factory)
    site.watch.clear()
    # Sign-in (auth.login) and a request of that session.
    signing_in = TestClient(site.app, base_url="http://testserver")
    api_testing.make_user(site.factory, "viewer", username="izleyici", password=PASSWORD)
    prime_csrf(signing_in)
    login = {"username": "izleyici", "password": PASSWORD}
    assert signing_in.post("/api/v1/auth/login", json=login).status_code == 200
    sync_csrf(signing_in)
    assert signing_in.get("/api/v1/auth/me").status_code == 200
    # An administrator: a search (search.run), a settings change (settings.change), a user
    # created (user.create), and a background search (no activity recorded).
    admin = sign_in(site.client, site.factory)
    _recent(site.factory, admin)
    # So far: the activity of /auth/me, and this test's own update of last_seen_at above.
    own = len(site.watch.activities())
    assert own == 2
    _search(site.client)
    change = {"changes": [{"key": "lookup.tolerance_seconds", "old": 60, "value": 90}]}
    assert site.client.patch("/api/v1/settings", json=change).status_code == 200
    user = {"username": "ornek.ik", "display_name": "Örnek İK", "role": "hr"}
    assert site.client.post("/api/v1/users", json=user).status_code == 201
    _search(site.client, **{BACKGROUND_HEADER: "1"})

    written = _audit_count(site.factory) - before
    with site.factory() as session:
        actions = Counter(
            session.scalars(select(AuditLog.action).order_by(AuditLog.id).offset(before))
        )
    # The background search writes its search.run too, and records no activity.
    assert actions == Counter(
        {"auth.login": 1, "search.run": 2, "settings.change": 1, "user.create": 1}
    )
    assert written == 5
    assert len(site.watch.activities()) == own + 3  # the search, settings change and new user
    _assert_audits_synchronous(site.watch, written)


def _assert_audits_synchronous(watch: Watch, written: int) -> None:
    """Every one of the ``written`` audit entries was seen being written (none on a connection
    the watch misses), each with its chain lock, and every transaction that wrote one committed
    with ``synchronous_commit`` on."""
    audited = watch.audited()
    assert sum(t.audits for t in audited) == written
    assert all(t.locks == t.audits for t in audited), [t.statements for t in audited]
    assert [t.committed for t in audited] == ["on"] * len(audited), [
        t.statements for t in audited if t.committed != "on"
    ]


def _audit_first(db: DbSession) -> None:
    record_audit(db, actor=None, action="test.before_authentication")


# Module level: with postponed annotations FastAPI resolves ``Depends`` targets here.
AuditFirstDep = Annotated[None, Depends(_audit_first)]


def _audit_first_probe(_: AuditFirstDep, user: CurrentUserDep) -> dict[str, str]:
    return {"user": user.username}


def test_the_check_catches_an_audit_write_in_the_activity_transaction(site: Site) -> None:
    """The hazard of ``_record_activity``'s docstring: a dependency that writes an audit entry
    to the request's session before authentication. That entry is committed with the activity,
    asynchronously, and the check above would see it."""

    site.app.add_api_route("/api/v1/probe/audit-first", _audit_first_probe, methods=["GET"])
    admin = sign_in(site.client, site.factory)
    _recent(site.factory, admin)
    site.watch.clear()
    assert site.client.get("/api/v1/probe/audit-first").status_code == 200
    (activity,) = site.watch.activities()
    assert activity.audits == 1
    assert site.watch.audited() == [activity]
    with pytest.raises(AssertionError):
        _assert_audits_synchronous(site.watch, 1)


#: Where product code and deployment files may set ``synchronous_commit``.
SETTINGS = re.compile(r"synchronous_commit\s*(=|to\b)|'synchronous_commit'", re.IGNORECASE)


def test_synchronous_commit_is_turned_off_only_for_the_activity() -> None:
    """No other engine, connection option, migration or deployment file turns it off: the
    worker's and ingest's audit writes (``append_system_entry``) run on engines these tests do
    not watch."""
    root = Path(__file__).resolve().parents[2]
    found = [
        (path.relative_to(root).as_posix(), line.strip())
        for top in ("api", "db", "worker", "ingest", "collector", "core", "deploy")
        for path in sorted((root / top).rglob("*"))
        if path.is_file()
        and path.suffix in {".py", ".sql", ".ps1", ".conf", ".toml"}
        and "tests" not in path.parts
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if SETTINGS.search(line)
    ]
    assert found == [
        (
            "api/src/arcilogger_api/security.py",
            'db.connection().exec_driver_sql("SET LOCAL synchronous_commit = off")',
        )
    ]
