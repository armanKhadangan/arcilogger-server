"""Test helpers for API tests of every work package (fixtures in ``api/tests/conftest.py``).

They create users and **valid sessions directly in the database** (no login flow), so feature
tests can check permissions without depending on the authentication endpoints.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Any, Final

from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.security import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SESSION_COOKIE,
    create_session,
)
from arcilogger_db.models import ROLES, User

#: ``site_name`` of the tests' fictional organisation. The product default is empty (set at
#: installation), so tests that need a name set this one explicitly.
EXAMPLE_SITE_NAME: Final = "Örnek A.Ş."

_numbers = itertools.count(1)


@dataclass(frozen=True)
class LoggedIn:
    user_id: int
    username: str
    role: str
    session_token: str
    csrf_token: str


def make_user(
    factory: sessionmaker[Session],
    role: str = "admin",
    *,
    username: str | None = None,
    password: str | None = None,
    display_name: str | None = None,
    **columns: Any,
) -> User:
    """Insert a user (committed). Without ``password`` the account cannot log in."""
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    name = username or f"{role}{next(_numbers)}"
    columns.setdefault("must_change_password", False)
    with factory() as session:
        user = User(
            username=name,
            display_name=display_name or f"Test {role} {name}",
            role=role,
            password_hash=PasswordHasher().hash(password) if password else "!",
            **columns,
        )
        session.add(user)
        session.commit()
        session.refresh(user)
        session.expunge(user)
    return user


def login(
    client: TestClient,
    factory: sessionmaker[Session],
    user: User,
    *,
    mfa_passed: bool = True,
    ip: str = "172.20.10.37",
) -> LoggedIn:
    """Create a session row for ``user`` and put its cookies and CSRF header on ``client``."""
    with factory() as session:
        issued = create_session(
            session,
            session.get_one(User, user.id),
            mfa_passed=mfa_passed,
            ip=ip,
            user_agent="pytest",
        )
        session.commit()
    # The session's cookies replace any the client holds, as a browser's login does: a CSRF cookie
    # the server set earlier (for the test host) would otherwise go out beside this one, and which
    # of the two the server reads depends on the HTTP client's cookie order.
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        client.cookies.delete(name)
    client.cookies.set(SESSION_COOKIE, issued.token)
    client.cookies.set(CSRF_COOKIE, issued.csrf_token)
    client.headers[CSRF_HEADER] = issued.csrf_token
    return LoggedIn(
        user_id=user.id,
        username=user.username,
        role=user.role,
        session_token=issued.token,
        csrf_token=issued.csrf_token,
    )
