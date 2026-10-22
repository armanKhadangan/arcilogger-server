"""Request and response models shared by the auth, users and setup endpoints.

Request models forbid unknown fields and bound every string, so oversized input is a 422 before
any work (Argon2 included) is done; validation errors never echo the submitted values
(:mod:`arcilogger_api.errors`).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Annotated, Final, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, StringConstraints

from arcilogger_api.auth.accounts import Step
from arcilogger_api.security import ROLE_LABELS
from arcilogger_db.models import User

Role = Literal["admin", "auditor", "hr", "viewer"]
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


#: Control (NUL breaks PostgreSQL text and the audit chain), format (bidi overrides such as
#: U+202E, zero-width characters: they disguise a value in the admin UI) and lone surrogates
#: (not encodable as UTF-8; the JSON parser lets ``"\ud800"`` through).
_FORBIDDEN_CATEGORIES: Final = frozenset({"Cc", "Cf", "Cs"})


def _printable(value: str) -> str:
    """No control, format or surrogate characters."""
    if any(unicodedata.category(c) in _FORBIDDEN_CATEGORIES for c in value):
        raise ValueError("control characters are not allowed")
    return value


def _email(value: str) -> str:
    if not _EMAIL.match(value):
        raise ValueError("not an e-mail address")
    return value


DisplayName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=200),
    AfterValidator(_printable),
]
Email = Annotated[
    str,
    StringConstraints(strip_whitespace=True, max_length=254),
    AfterValidator(_printable),
    AfterValidator(_email),
]
#: Passwords are never stripped; the policy decides about length, this only bounds the input.
PasswordInput = Annotated[str, Field(min_length=1, max_length=1024)]
UsernameInput = Annotated[str, Field(min_length=1, max_length=256)]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UserSummary(BaseModel):
    id: int
    username: str
    display_name: str
    role: str
    role_label_tr: str
    role_label_en: str

    @classmethod
    def of(cls, user: User) -> UserSummary:
        label_tr, label_en = ROLE_LABELS.get(user.role, (user.role, user.role))
        return cls(
            id=user.id,
            username=user.username,
            display_name=user.display_name,
            role=user.role,
            role_label_tr=label_tr,
            role_label_en=label_en,
        )


class UserOut(UserSummary):
    """A user as administrators see it (never the hashes or the TOTP secret)."""

    email: str | None
    active: bool
    must_change_password: bool
    totp_enabled: bool
    locked: bool
    locked_until: datetime | None
    failed_attempts: int
    last_login: datetime | None
    password_changed_at: datetime | None
    created_at: datetime

    @classmethod
    def of_user(cls, user: User, *, at: datetime) -> UserOut:
        locked = user.locked_until is not None and user.locked_until > at
        return cls(
            **UserSummary.of(user).model_dump(),
            email=user.email,
            active=user.active,
            must_change_password=user.must_change_password,
            totp_enabled=user.totp_enabled,
            locked=locked,
            locked_until=user.locked_until if locked else None,
            failed_attempts=user.failed_attempts,
            last_login=user.last_login,
            password_changed_at=user.password_changed_at,
            created_at=user.created_at,
        )


class SignInState(BaseModel):
    """Where the sign-in stands; ``next`` tells the web app which screen to show."""

    next: Step
    mfa_passed: bool
    must_change_password: bool
    user: UserSummary
