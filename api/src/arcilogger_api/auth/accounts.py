"""Accounts and sessions: lockout, session rotation and revocation, the sign-in steps.

**Lockout** (per account, spec 13): every wrong password — at sign-in, re-authentication or
password change — and every wrong TOTP/recovery code counts; every ``auth.lockout_attempts``
failures lock the account. The lock is **progressive**: ``auth.lockout_minutes`` the first time,
twice as long for each further lock without a complete sign-in in between, up to
:data:`MAX_LOCK_MINUTES` (with the defaults 15, 30, 60 … minutes, then 24 hours). The counter is
reset only by a *complete* sign-in (password and, where needed, TOTP), a successful
re-authentication or password change (both need such a session) or by an administrator (unlock,
password reset), so someone who knows the password but not the second factor gets
5 guesses in 15 minutes, then fewer and fewer — about 1 900 a year instead of 175 000 with a
fixed lock (each guess hits about 3 of the 10⁶ codes). While locked, sign-in is refused even
with the right password and is not counted. Administrators can unlock early
(``POST /users/{id}/unlock``); every lock is audited (``auth.lockout``).

**Sessions** are rotated (new token and CSRF token, the old row revoked) after the TOTP step and
after a password change, so a token seen before cannot be used afterwards (session fixation).
A rotated session keeps the original ``created_at``/``expires_absolute``: the absolute lifetime
counts from the sign-in, not from the last rotation.

**Sign-in steps** (:func:`next_step`): ``totp`` (enter a code) or ``totp_setup`` (first
enrolment) while the session waits for the second factor, then ``password_change`` while
``must_change_password`` is set, then ``privacy_notice`` until the user accepted the current
KVKK notice (roles that see person-level data), else ``done``.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from fastapi import Request
from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth import totp
from arcilogger_api.security import (
    CurrentUser,
    IssuedSession,
    client_ip,
    create_session,
    permissions_for,
)
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import PrivacyNoticeAcceptance, User, UserRecoveryCode, UserSession
from arcilogger_db.settings import get_setting, get_settings

#: Same rule as the ``users.username`` check constraint.
USERNAME_PATTERN: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
#: pg_advisory_xact_lock key serialising user management ("ARCIUSER").
USERS_LOCK_KEY: Final = int.from_bytes(b"ARCIUSER", "big") - 2**63
#: Longest lock of the progressive lockout (unless ``auth.lockout_minutes`` itself is longer).
MAX_LOCK_MINUTES: Final = 24 * 60
#: Seeing any of these means seeing person-level data: the KVKK notice is required (spec 3.3).
#: ``usage.anonymous`` too: it shows names since the owner decision of 2026-10-20.
PRIVACY_PERMISSIONS: Final = frozenset(
    {"usage.person", "usage.anonymous", "lookup.run", "search.read"}
)

Step = Literal["totp", "totp_setup", "password_change", "privacy_notice", "done"]


@dataclass(frozen=True)
class UserActor:
    """Audit actor for a user row (the :class:`arcilogger_api.audit.Actor` protocol)."""

    id: int | None
    username: str


def actor_of(user: User) -> UserActor:
    return UserActor(id=user.id, username=user.username)


def target_of(user: User | int) -> str:
    return f"user:{user if isinstance(user, int) else user.id}"


def normalize_username(text: str) -> str | None:
    """Lower-case ASCII user name, ``None`` when it breaks the rule (no Unicode look-alikes)."""
    value = text.strip().lower()
    if not value.isascii() or not USERNAME_PATTERN.match(value):
        return None
    return value


def load_user_for_update(db: Session, user_id: int) -> User | None:
    """The user row, locked until the transaction ends (counters change atomically).

    ``populate_existing``: if this user is still in the session's identity map (loaded before,
    e.g. by the authentication dependency), the ORM would otherwise keep the attributes read
    *before* waiting for the lock, and a concurrent request's change (``totp_last_counter``,
    ``failed_attempts``) would be lost — two requests could then accept the same TOTP code.
    """
    return db.scalars(
        select(User)
        .where(User.id == user_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).one_or_none()


def lock_user_management(db: Session) -> None:
    """Serialise changes that depend on the set of administrators (last-admin rule, setup)."""
    db.execute(select(func.pg_advisory_xact_lock(USERS_LOCK_KEY)))


def active_admins(db: Session, *, excluding: int | None = None) -> int:
    query = select(func.count()).select_from(User).where(User.role == "admin", User.active)
    if excluding is not None:
        query = query.where(User.id != excluding)
    return int(db.scalar(query) or 0)


def admin_exists(db: Session) -> bool:
    """Any administrator account, active or not (first-run setup is then over)."""
    return bool(db.scalar(select(exists().where(User.role == "admin"))))


# -- settings -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class AuthSettings:
    idle_minutes: int
    absolute_hours: int
    lockout_attempts: int
    lockout_minutes: int
    password_min_length: int
    totp_required_roles: frozenset[str]

    @classmethod
    def load(cls, db: Session) -> AuthSettings:
        values: Mapping[str, Any] = get_settings(db, prefix="auth.")
        roles = values["auth.totp_required_roles"]
        return cls(
            idle_minutes=int(values["auth.idle_minutes"]),
            absolute_hours=int(values["auth.absolute_hours"]),
            lockout_attempts=max(1, int(values["auth.lockout_attempts"])),
            lockout_minutes=max(1, int(values["auth.lockout_minutes"])),
            password_min_length=int(values["auth.password_min_length"]),
            totp_required_roles=frozenset(str(role) for role in roles or ()),
        )

    def totp_required(self, role: str) -> bool:
        return role in self.totp_required_roles


# -- lockout --------------------------------------------------------------------------------------


def is_locked(user: User, at: datetime) -> bool:
    return user.locked_until is not None and user.locked_until > at


def lock_round(user: User, settings: AuthSettings) -> int:
    """How many locks the failures since the last complete sign-in have caused."""
    return user.failed_attempts // settings.lockout_attempts


def lock_minutes(settings: AuthSettings, lock: int) -> int:
    """Length of the ``lock``-th lock since the last complete sign-in (1 = the first)."""
    doubled = settings.lockout_minutes << min(max(lock, 1) - 1, 16)  # x 2 per further lock
    return min(doubled, max(MAX_LOCK_MINUTES, settings.lockout_minutes))


def register_failure(user: User, settings: AuthSettings, at: datetime) -> bool:
    """Count one failed attempt (``user`` locked FOR UPDATE); ``True`` when it locks now.

    The count goes on after a lock expired (it restarts only with a complete sign-in or an
    administrator's unlock), and each ``auth.lockout_attempts``-th failure locks again for
    :func:`lock_minutes` of the new round.
    """
    user.failed_attempts += 1
    if user.failed_attempts % settings.lockout_attempts != 0:
        return False
    minutes = lock_minutes(settings, lock_round(user, settings))
    user.locked_until = at + timedelta(minutes=minutes)
    return True


def audit_lockout(
    db: Session, user: User, settings: AuthSettings, *, step: str, ip: str | None
) -> None:
    """``auth.lockout`` (``lock`` = how many locks since the last complete sign-in; repeated
    locks of one account are worth an alert: someone may be guessing its second factor)."""
    lock = lock_round(user, settings)
    record_audit(
        db,
        actor=None,
        action="auth.lockout",
        target=target_of(user),
        details={
            "username": user.username,
            "attempts": user.failed_attempts,
            "minutes": lock_minutes(settings, lock),
            "lock": lock,
            "step": step,
        },
        ip=ip,
    )


def complete_sign_in(user: User, at: datetime) -> None:
    """Both factors passed: the failure counter starts again."""
    user.failed_attempts = 0
    user.locked_until = None
    user.last_login = at


# -- sessions -------------------------------------------------------------------------------------


def session_id(row: UserSession | str) -> str:
    """Short, non-secret session reference for audit entries (prefix of the token hash)."""
    token_hash = row if isinstance(row, str) else row.token_hash
    return token_hash[:12]


def current_session(db: Session, user: CurrentUser) -> UserSession:
    return db.get_one(UserSession, user.session_token_hash)


def revoke_sessions(db: Session, user_id: int, *, at: datetime, keep: str | None = None) -> int:
    """Revoke the user's open sessions (except ``keep``); returns how many were open."""
    statement = (
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=at)
        .execution_options(synchronize_session="fetch")
    )
    if keep is not None:
        statement = statement.where(UserSession.token_hash != keep)
    return int(db.execute(statement).rowcount or 0)  # type: ignore[attr-defined]


def open_session(
    db: Session, request: Request, user: User, *, mfa_passed: bool, at: datetime
) -> IssuedSession:
    """A new session after the password step (``reauthenticated_at`` = now when complete)."""
    issued = create_session(
        db,
        user,
        mfa_passed=mfa_passed,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        now=at,
    )
    if mfa_passed:
        issued.row.reauthenticated_at = at
    db.flush()
    return issued


def rotate_session(
    db: Session,
    request: Request,
    user: User,
    old: UserSession,
    *,
    mfa_passed: bool,
    reauthenticated: bool,
    at: datetime | None = None,
) -> IssuedSession:
    """Replace ``old`` by a new token (fixation): same lifetime, old row revoked."""
    now = at or utc_now()
    old.revoked_at = now
    issued = create_session(
        db,
        user,
        mfa_passed=mfa_passed,
        ip=client_ip(request),
        user_agent=request.headers.get("user-agent"),
        now=now,
    )
    issued.row.created_at = old.created_at
    issued.row.expires_absolute = old.expires_absolute
    issued.row.reauthenticated_at = now if reauthenticated else None
    db.flush()
    return issued


# -- recovery codes -------------------------------------------------------------------------------


def replace_recovery_codes(db: Session, user_id: int) -> list[str]:
    """New single-use codes (the old ones become invalid); returns them for display once."""
    db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user_id))
    codes = totp.new_recovery_codes()
    db.add_all(
        UserRecoveryCode(user_id=user_id, code_hash=totp.hash_recovery_code(user_id, code))
        for code in codes
    )
    db.flush()
    return codes


def use_recovery_code(db: Session, user_id: int, code: str, *, at: datetime) -> bool:
    """Mark a matching unused code as used; ``False`` when there is none."""
    if totp.normalize_recovery_code(code) is None:
        return False
    row = db.scalars(
        select(UserRecoveryCode)
        .where(
            UserRecoveryCode.user_id == user_id,
            UserRecoveryCode.code_hash == totp.hash_recovery_code(user_id, code),
            UserRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    ).first()
    if row is None:
        return False
    row.used_at = at
    db.flush()
    return True


def recovery_codes_left(db: Session, user_id: int) -> int:
    return int(
        db.scalar(
            select(func.count())
            .select_from(UserRecoveryCode)
            .where(UserRecoveryCode.user_id == user_id, UserRecoveryCode.used_at.is_(None))
        )
        or 0
    )


def clear_totp(db: Session, user: User) -> None:
    """Remove the second factor (admin reset): enrolment starts again at the next sign-in."""
    user.totp_secret_encrypted = None
    user.totp_enabled = False
    user.totp_last_counter = None
    db.execute(delete(UserRecoveryCode).where(UserRecoveryCode.user_id == user.id))


# -- privacy notice (KVKK, spec 3.3) --------------------------------------------------------------


def privacy_required(role: str) -> bool:
    return bool(permissions_for(role) & PRIVACY_PERMISSIONS)


def privacy_version(db: Session) -> str:
    return str(get_setting(db, "privacy.notice_version"))


def privacy_accepted(db: Session, user_id: int, version: str) -> bool:
    return bool(
        db.scalar(
            select(
                exists().where(
                    PrivacyNoticeAcceptance.user_id == user_id,
                    PrivacyNoticeAcceptance.version == version,
                )
            )
        )
    )


def accept_privacy(db: Session, user_id: int, version: str, *, ip: str | None) -> bool:
    """Store the acceptance; ``False`` when this version was accepted before."""
    statement = (
        insert(PrivacyNoticeAcceptance)
        .values(user_id=user_id, version=version, ip=ip)
        .on_conflict_do_nothing(index_elements=["user_id", "version"])
        .returning(PrivacyNoticeAcceptance.id)
    )
    return db.execute(statement).scalar_one_or_none() is not None


# -- steps ----------------------------------------------------------------------------------------


def next_step(db: Session, user: User, *, mfa_passed: bool) -> Step:
    """What the web app shows next for this user and session."""
    if not mfa_passed:
        return "totp" if user.totp_enabled else "totp_setup"
    if user.must_change_password:
        return "password_change"
    if privacy_required(user.role) and not privacy_accepted(db, user.id, privacy_version(db)):
        return "privacy_notice"
    return "done"
