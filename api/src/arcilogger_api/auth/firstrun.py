"""First-run setup: the one-time token that lets the first administrator be created.

On a fresh installation nobody can sign in. Someone with access to the server console runs
``arcilogger-api setup-token``; it prints a random token valid for :data:`TOKEN_HOURS` hours and
stores **only its SHA-256** (setting ``setup.admin_token``, an internal key like
``audit.spool_watermark``, written by ``:system``). The web wizard sends the token with the first
administrator's user name, display name and password (``POST /auth/setup/admin``); the token is
deleted in the same transaction, under a row lock, so it works exactly once.

Both steps refuse once **any** administrator account exists. If every administrator is locked
out (lost phone and recovery codes), ``arcilogger-api create-admin`` on the server console
creates another one (docs/SECURITY.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Final, Literal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from arcilogger_api.auth.accounts import admin_exists
from arcilogger_api.security import hash_token, new_token, tokens_equal
from arcilogger_db.models import Setting
from arcilogger_db.settings import SYSTEM_AUTHOR, set_setting

SETUP_TOKEN_KEY: Final = "setup.admin_token"  # noqa: S105 - a setting name, not a token
TOKEN_HOURS: Final = 24

TokenProblem = Literal["missing", "expired", "mismatch"]


class SetupDoneError(RuntimeError):
    """An administrator already exists."""


@dataclass(frozen=True)
class SetupStatus:
    needs_setup: bool
    token_active: bool


def issue_setup_token(db: Session, *, at: datetime) -> tuple[str, datetime]:
    """Store the hash of a new token (replacing an older one); returns ``(token, expires)``."""
    if admin_exists(db):
        raise SetupDoneError("an administrator already exists")
    token = new_token()
    expires = at + timedelta(hours=TOKEN_HOURS)
    value = {"hash": hash_token(token), "expires_at": expires.isoformat()}
    set_setting(db, SETUP_TOKEN_KEY, value, updated_by=SYSTEM_AUTHOR)
    return token, expires


def _stored(db: Session, *, lock: bool) -> dict[str, str] | None:
    query = select(Setting.value).where(Setting.key == SETUP_TOKEN_KEY)
    if lock:
        query = query.with_for_update()
    value = db.scalar(query)
    if not isinstance(value, dict) or not {"hash", "expires_at"} <= value.keys():
        return None
    return {"hash": str(value["hash"]), "expires_at": str(value["expires_at"])}


def _expired(stored: dict[str, str], at: datetime) -> bool:
    try:
        return datetime.fromisoformat(stored["expires_at"]) <= at
    except ValueError:
        return True


def setup_status(db: Session, *, at: datetime) -> SetupStatus:
    if admin_exists(db):
        return SetupStatus(needs_setup=False, token_active=False)
    stored = _stored(db, lock=False)
    return SetupStatus(
        needs_setup=True, token_active=stored is not None and not _expired(stored, at)
    )


def check_setup_token(db: Session, token: str, *, at: datetime) -> TokenProblem | None:
    """``None`` when ``token`` is the valid one; locks the row until the transaction ends."""
    stored = _stored(db, lock=True)
    if stored is None:
        return "missing"
    if not tokens_equal(hash_token(token), stored["hash"]):
        return "mismatch"
    if _expired(stored, at):
        return "expired"
    return None


def consume_setup_token(db: Session) -> None:
    db.execute(delete(Setting).where(Setting.key == SETUP_TOKEN_KEY))
