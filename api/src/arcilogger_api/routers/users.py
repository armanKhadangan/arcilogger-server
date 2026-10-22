"""``/api/v1/users`` — Kullanıcılar ve roller (spec 13, 12.3 page 15).

Only ``users.manage`` (administrators). Every change is audited (``user.*``) and every change
that alters what an account may do — creation, role or active state, password or TOTP reset —
additionally needs a **recent re-authentication** (``require_recent_auth``, spec 13: sensitive
credential actions); unlocking does not.

Guards: the last active administrator can be neither demoted nor disabled, and nobody changes
their own role or active state or resets their own password/TOTP here (``/auth/password``
exists for the own password). Changes that depend on the set of administrators run under an
advisory lock, so two administrators demoting each other at the same moment cannot leave the
system without one. A role change, deactivation, password or TOTP reset ends all sessions of
that user (new rights and a new second factor apply at the next sign-in).

**Users limit** (licensing contract 6.6): creating a user, or re-enabling a disabled one, is
refused with 409 ``license_limit`` while the active users already reach the licence's ``users``
limit. ``max`` comes from the cached licence status (``None``: no limit — also when the licence
subsystem failed); the active users are recounted under the user-management lock, so quick
repeated requests cannot pass it. The refusal is audited (``license.limit_refused``) in its own
transaction. Existing users over the limit keep working; deactivation, role changes and sign-in
are never limited.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.accounts import (
    AuthSettings,
    active_admins,
    clear_totp,
    is_locked,
    load_user_for_update,
    lock_user_management,
    normalize_username,
    revoke_sessions,
    target_of,
)
from arcilogger_api.auth.messages import auth_error
from arcilogger_api.auth.passwords import PolicyContext, hash_password, temporary_password
from arcilogger_api.auth.schemas import DisplayName, Email, Role, StrictModel, UserOut
from arcilogger_api.deps import ConfigDep, DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.licensing import invalidate_license, license_limit_error, users_limit_max
from arcilogger_api.security import CurrentUser, client_ip, require, require_recent_auth
from arcilogger_core.config import Config
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import User

router = APIRouter(prefix="/users", tags=["users"])

ManagerDep = Annotated[CurrentUser, Depends(require("users.manage"))]
RecentManagerDep = Annotated[CurrentUser, Depends(require_recent_auth("users.manage"))]
#: User number in a path: 1 … 2^63-1 (PostgreSQL ``bigint``); larger values answer 422, never a
#: database error.
UserId = Annotated[int, Path(ge=1, le=2**63 - 1, description="User number")]


def _get(db: Session, user_id: int) -> User:
    user = load_user_for_update(db, user_id)
    if user is None:
        raise ApiError(404, "not_found")
    return user


def _check_users_limit(
    request: Request, db: Session, current: CurrentUser, *, username: str, operation: str
) -> None:
    """409 ``license_limit`` when one more active user would pass the licence's users limit.

    Call under the user-management lock: the active users are recounted in this transaction.
    The refusal is audited in its own transaction (this one is rolled back first).
    """
    maximum = users_limit_max(request, db)
    if maximum is None:
        return
    used = int(db.scalar(select(func.count()).select_from(User).where(User.active)) or 0)
    if used < maximum:
        return
    db.rollback()
    record_audit(
        db,
        actor=current,
        action="license.limit_refused",
        target=f"user:{username}",
        details={
            "limit": "users",
            "max": maximum,
            "used": used,
            "username": username,
            "operation": operation,
        },
        ip=client_ip(request),
    )
    db.commit()
    raise license_limit_error(used=used, maximum=maximum)


def _temporary_password(db: Session, config: Config, *, username: str, name: str) -> str:
    """A random password that passes the policy, as long as ``auth.password_min_length``
    requires (it is shown to the administrator once)."""
    context = PolicyContext(
        min_length=AuthSettings.load(db).password_min_length,
        username=username,
        display_name=name,
        site_name=config.site_name,
    )
    return temporary_password(context)


class UserPage(BaseModel):
    items: list[UserOut]
    #: Pass as ``cursor`` for the next page; ``null`` on the last page.
    next_cursor: str | None


@router.get("")
def list_users(
    db: DbSession,
    current: ManagerDep,
    cursor: Annotated[str | None, Query(max_length=64)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    role: Role | None = None,
    active: bool | None = None,
) -> UserPage:
    """Users sorted by user name (cursor = last user name of the previous page)."""
    query = select(User).order_by(User.username).limit(limit + 1)
    if cursor is not None:
        query = query.where(User.username > cursor)
    if role is not None:
        query = query.where(User.role == role)
    if active is not None:
        query = query.where(User.active.is_(active))
    rows = list(db.scalars(query))
    at = utc_now()
    more = len(rows) > limit
    rows = rows[:limit]
    return UserPage(
        items=[UserOut.of_user(row, at=at) for row in rows],
        next_cursor=rows[-1].username if more else None,
    )


@router.get("/{user_id}")
def get_user(user_id: UserId, db: DbSession, current: ManagerDep) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise ApiError(404, "not_found")
    return UserOut.of_user(user, at=utc_now())


class UserCreate(StrictModel):
    username: str = Field(min_length=1, max_length=64)
    display_name: DisplayName
    email: Email | None = None
    role: Role


class CreatedUser(BaseModel):
    user: UserOut
    #: Shown once; the user must change it at the first sign-in.
    temporary_password: str


@router.post("", status_code=status.HTTP_201_CREATED)
def create_user(
    body: UserCreate,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    current: RecentManagerDep,
) -> CreatedUser:
    """New account with a temporary password (forced change and, per role, TOTP enrolment)."""
    username = normalize_username(body.username)
    if username is None:
        raise auth_error(422, "username_invalid")
    # Generated and hashed (~75 ms) before the user-management lock, which every other user
    # change waits for.
    password = _temporary_password(db, config, username=username, name=body.display_name)
    password_hash = hash_password(password)
    lock_user_management(db)
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise auth_error(409, "username_taken")
    _check_users_limit(request, db, current, username=username, operation="create")
    user = User(
        username=username,
        display_name=body.display_name,
        email=body.email,
        role=body.role,
        password_hash=password_hash,
        must_change_password=True,
    )
    db.add(user)
    db.flush()
    db.refresh(user)
    record_audit(
        db,
        actor=current,
        action="user.create",
        target=target_of(user),
        details={"username": username, "role": body.role, "active": True},
        ip=client_ip(request),
    )
    invalidate_license(request)  # the users count of the licence page changed
    return CreatedUser(user=UserOut.of_user(user, at=utc_now()), temporary_password=password)


class UserUpdate(StrictModel):
    """Only the fields sent are changed; ``email: null`` removes the address."""

    display_name: DisplayName | None = None
    email: Email | None = None
    role: Role | None = None
    active: bool | None = None


@router.patch("/{user_id}")
def update_user(
    user_id: UserId,
    body: UserUpdate,
    request: Request,
    db: DbSession,
    current: RecentManagerDep,
) -> UserOut:
    """Change display name, e-mail, role or active state (audited with old and new values)."""
    lock_user_management(db)
    user = _get(db, user_id)
    sent = body.model_fields_set
    changes: dict[str, dict[str, Any]] = {}
    for field in ("display_name", "email", "role", "active"):
        if field not in sent:
            continue
        new = getattr(body, field)
        if new is None and field != "email":
            raise ApiError(422, "validation_error", details=[{"loc": ["body", field]}])
        old = getattr(user, field)
        if new != old:
            changes[field] = {"old": old, "new": new}
    if not changes:
        return UserOut.of_user(user, at=utc_now())
    rights_change = "role" in changes or "active" in changes
    if rights_change and user.id == current.id:
        raise auth_error(409, "cannot_change_self")
    losing_admin = (
        user.role == "admin"
        and user.active
        and (
            changes.get("role", {}).get("new", "admin") != "admin"
            or changes.get("active", {}).get("new", True) is False
        )
    )
    if losing_admin and active_admins(db, excluding=user.id) == 0:
        raise auth_error(409, "last_admin")
    reactivating = changes.get("active", {}).get("new") is True
    if reactivating:
        _check_users_limit(request, db, current, username=user.username, operation="reactivate")
    for field, change in changes.items():
        setattr(user, field, change["new"])
    details: dict[str, Any] = {"changes": changes}
    at = utc_now()
    if rights_change:
        details["sessions_revoked"] = revoke_sessions(db, user.id, at=at)
    db.flush()
    record_audit(
        db,
        actor=current,
        action="user.update",
        target=target_of(user),
        details=details,
        ip=client_ip(request),
    )
    if "active" in changes:
        invalidate_license(request)  # the users count of the licence page changed
    return UserOut.of_user(user, at=at)


class TemporaryPassword(BaseModel):
    user: UserOut
    temporary_password: str


@router.post("/{user_id}/reset-password")
def reset_password(
    user_id: UserId,
    request: Request,
    db: DbSession,
    config: ConfigDep,
    current: RecentManagerDep,
) -> TemporaryPassword:
    """New temporary password (forced change at the next sign-in); also unlocks the account."""
    user = _get(db, user_id)
    if user.id == current.id:
        raise auth_error(409, "cannot_change_self")
    password = _temporary_password(db, config, username=user.username, name=user.display_name)
    at = utc_now()
    user.password_hash = hash_password(password)
    user.must_change_password = True
    user.password_changed_at = at
    user.failed_attempts = 0
    user.locked_until = None
    revoked = revoke_sessions(db, user.id, at=at)
    record_audit(
        db,
        actor=current,
        action="user.password_reset",
        target=target_of(user),
        details={"sessions_revoked": revoked},
        ip=client_ip(request),
    )
    return TemporaryPassword(user=UserOut.of_user(user, at=at), temporary_password=password)


@router.post("/{user_id}/reset-totp")
def reset_totp(
    user_id: UserId, request: Request, db: DbSession, current: RecentManagerDep
) -> UserOut:
    """Remove the second factor and the recovery codes (lost phone); enrolment at next sign-in."""
    user = _get(db, user_id)
    if user.id == current.id:
        raise auth_error(409, "cannot_change_self")
    was_enabled = user.totp_enabled
    clear_totp(db, user)
    at = utc_now()
    revoked = revoke_sessions(db, user.id, at=at)
    record_audit(
        db,
        actor=current,
        action="user.totp_reset",
        target=target_of(user),
        details={"was_enabled": was_enabled, "sessions_revoked": revoked},
        ip=client_ip(request),
    )
    return UserOut.of_user(user, at=at)


@router.post("/{user_id}/unlock")
def unlock_user(user_id: UserId, request: Request, db: DbSession, current: ManagerDep) -> UserOut:
    """End a lockout early and reset the failure counter."""
    user = _get(db, user_id)
    at = utc_now()
    was_locked = is_locked(user, at)
    user.failed_attempts = 0
    user.locked_until = None
    record_audit(
        db,
        actor=current,
        action="user.unlock",
        target=target_of(user),
        details={"was_locked": was_locked},
        ip=client_ip(request),
    )
    return UserOut.of_user(user, at=at)
