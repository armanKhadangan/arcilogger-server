"""``/api/v1/auth`` — Giriş, TOTP, parola, yeniden doğrulama, oturum, ilk kurulum (spec 12.3
page 1 and 17, 13, 3.3).

Sign-in flow (``next`` in every answer tells the web app what to show):

1. ``POST /auth/login`` (user name + password) → a session. Without a second factor it is
   complete; when TOTP is enrolled or required for the role (``auth.totp_required_roles``) it
   waits for step 2 (``mfa_passed = false``).
2. ``POST /auth/totp`` (code or recovery code) → new session token, ``mfa_passed = true``.
   First enrolment instead: ``GET /auth/totp/setup`` (QR code) and ``POST /auth/totp/confirm``
   (returns the 10 recovery codes once). The same two calls from a full session enrol a second
   factor voluntarily; that needs a recent re-authentication.
3. ``POST /auth/password`` while ``must_change_password`` (everything else answers 403
   ``password_change_required``; ``/auth/me`` and ``/auth/logout`` keep working).
4. ``POST /auth/privacy-notice`` once per notice version for roles that see person-level data
   (endpoints with person-level data enforce it: :mod:`arcilogger_api.auth.privacy`).

Failures are counted and audited **in the same request** and committed before the error
response (:func:`_refuse`), so a refused attempt is never rolled back with the request.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Annotated, Any, NoReturn

from fastapi import APIRouter, Depends, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from arcilogger_api.audit import record_audit
from arcilogger_api.auth import totp
from arcilogger_api.auth.accounts import (
    AuthSettings,
    Step,
    accept_privacy,
    actor_of,
    admin_exists,
    audit_lockout,
    complete_sign_in,
    current_session,
    is_locked,
    load_user_for_update,
    lock_user_management,
    next_step,
    normalize_username,
    open_session,
    privacy_accepted,
    privacy_required,
    privacy_version,
    recovery_codes_left,
    register_failure,
    replace_recovery_codes,
    revoke_sessions,
    rotate_session,
    session_id,
    target_of,
    use_recovery_code,
)
from arcilogger_api.auth.firstrun import (
    check_setup_token,
    consume_setup_token,
    setup_status,
)
from arcilogger_api.auth.messages import auth_error, password_rejected
from arcilogger_api.auth.passwords import (
    REUSED,
    PolicyContext,
    dummy_verify,
    hash_password,
    password_problems,
    verify_password,
)
from arcilogger_api.auth.ratelimit import limits_of
from arcilogger_api.auth.schemas import (
    DisplayName,
    PasswordInput,
    SignInState,
    StrictModel,
    UsernameInput,
    UserSummary,
)
from arcilogger_api.deps import ConfigDep, DbSession
from arcilogger_api.security import (
    CSRF_COOKIE,
    RECENT_AUTH_MINUTES,
    SESSION_COOKIE,
    CurrentUser,
    CurrentUserDep,
    PendingMfaUserDep,
    client_ip,
    ensure_recent_auth,
    hash_token,
    permissions_for,
    require,
    require_recent_auth,
    set_session_cookies,
)
from arcilogger_core.config import Config
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import User, UserSession

router = APIRouter(prefix="/auth", tags=["auth"])

#: Signed in, both factors, password changed (no special permission).
SignedInDep = Annotated[CurrentUser, Depends(require())]
RecentDep = Annotated[CurrentUser, Depends(require_recent_auth())]


# -- helpers --------------------------------------------------------------------------------------


def _refuse(db: Session, status_code: int, code: str, **kwargs: Any) -> NoReturn:
    """Commit the bookkeeping (failure counter, audit entries, revocations), then refuse."""
    db.commit()
    raise auth_error(status_code, code, **kwargs)


def _rate_limit(request: Request, db: Session, bucket: str) -> None:
    ip = client_ip(request)
    decision = limits_of(request).hit(bucket, ip)
    if decision.allowed:
        return
    if decision.first_refusal:
        record_audit(db, actor=None, action="auth.rate_limited", details={"bucket": bucket}, ip=ip)
    _refuse(db, 429, "rate_limited", headers={"Retry-After": str(decision.retry_after)})


def _state(db: Session, user: User, *, mfa_passed: bool) -> SignInState:
    return SignInState(
        next=next_step(db, user, mfa_passed=mfa_passed),
        mfa_passed=mfa_passed,
        must_change_password=user.must_change_password,
        user=UserSummary.of(user),
    )


def _user(db: Session, current: CurrentUser) -> User:
    user = load_user_for_update(db, current.id)
    if user is None:  # pragma: no cover - the session's user was deleted in between
        raise auth_error(401, "session_invalid")
    return user


def _secret(config: Config, user: User) -> str:
    if user.totp_secret_encrypted is None:
        raise auth_error(409, "totp_setup_missing")
    try:
        return totp.decrypt_secret(config.secrets_dir, user.totp_secret_encrypted)
    except totp.SecretKeyUnavailableError as exc:
        raise auth_error(503, "secret_key_missing") from exc


def _policy(config: Config, settings: AuthSettings, *, username: str, name: str) -> PolicyContext:
    return PolicyContext(
        min_length=settings.password_min_length,
        username=username,
        display_name=name,
        site_name=config.site_name,
    )


def _revoke_presented_session(request: Request, db: Session, at: datetime) -> None:
    """A new sign-in from this browser ends the session it still holds (any user)."""
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        db.execute(
            update(UserSession)
            .where(UserSession.token_hash == hash_token(token), UserSession.revoked_at.is_(None))
            .values(revoked_at=at)
            .execution_options(synchronize_session=False)
        )


# -- 1. password ----------------------------------------------------------------------------------


class LoginBody(StrictModel):
    username: UsernameInput
    password: PasswordInput


@router.post("/login")
def login(
    body: LoginBody, request: Request, response: Response, db: DbSession, config: ConfigDep
) -> SignInState:
    """User name + password → session cookies (complete, or waiting for TOTP)."""
    ip = client_ip(request)
    _rate_limit(request, db, "login")
    at = utc_now()
    settings = AuthSettings.load(db)
    username = normalize_username(body.username)
    user = None
    if username is not None:
        user = db.scalars(
            select(User)
            .where(User.username == username)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).one_or_none()
    if user is None:
        dummy_verify(body.password)  # same work as for a real account
        # The typed name is not stored: it may be a password typed into the wrong field, and
        # the audit log is permanent.
        record_audit(
            db, actor=None, action="auth.login_failed", details={"reason": "unknown_user"}, ip=ip
        )
        _refuse(db, 401, "login_failed")
    verified = verify_password(user.password_hash, body.password)
    reason = None
    if is_locked(user, at):
        reason = "locked"
    elif not user.active:
        reason = "inactive"
    elif not verified.ok:
        reason = "bad_password"
    if reason is not None:
        locked_now = reason == "bad_password" and register_failure(user, settings, at)
        record_audit(
            db,
            actor=None,
            action="auth.login_failed",
            target=target_of(user),
            details={"username": user.username, "reason": reason},
            ip=ip,
        )
        if locked_now:
            audit_lockout(db, user, settings, step="password", ip=ip)
        _refuse(db, 401, "login_failed")
    if verified.new_hash is not None:
        user.password_hash = verified.new_hash
    _revoke_presented_session(request, db, at)
    second_factor = user.totp_enabled or settings.totp_required(user.role)
    issued = open_session(db, request, user, mfa_passed=not second_factor, at=at)
    if not second_factor:
        complete_sign_in(user, at)
    state = _state(db, user, mfa_passed=not second_factor)
    record_audit(
        db,
        actor=actor_of(user),
        action="auth.login",
        target=target_of(user),
        details={
            "session": session_id(issued.row),
            "next": state.next,
            "rehashed": verified.new_hash is not None,
        },
        ip=ip,
    )
    set_session_cookies(response, issued, secure=config.api.secure_cookies)
    return state


# -- 2. second factor -----------------------------------------------------------------------------


class CodeBody(StrictModel):
    code: str = Field(min_length=1, max_length=64)


@router.post("/totp")
def totp_step(
    body: CodeBody,
    request: Request,
    response: Response,
    db: DbSession,
    config: ConfigDep,
    current: PendingMfaUserDep,
) -> SignInState:
    """Second factor: a 6-digit TOTP code or a recovery code → new session token."""
    ip = client_ip(request)
    _rate_limit(request, db, "totp")
    if current.mfa_passed:
        raise auth_error(409, "mfa_already_passed")
    at = utc_now()
    settings = AuthSettings.load(db)
    user = _user(db, current)
    session_row = current_session(db, current)
    if is_locked(user, at):
        session_row.revoked_at = at
        record_audit(
            db,
            actor=actor_of(user),
            action="auth.totp_failed",
            target=target_of(user),
            details={"reason": "locked", "step": "sign_in"},
            ip=ip,
        )
        _refuse(db, 401, "account_locked")
    if not user.totp_enabled:
        raise auth_error(409, "totp_not_enabled")
    method = "totp" if totp.looks_like_totp(body.code) else "recovery_code"
    reason = "invalid"
    if method == "totp":
        check = totp.check_totp(
            _secret(config, user), body.code, last_counter=user.totp_last_counter, at=at
        )
        ok = check.ok
        if check.counter is not None:
            user.totp_last_counter = check.counter
        elif check.replay:
            reason = "replay"
    else:
        ok = use_recovery_code(db, user.id, body.code, at=at)
    if not ok:
        locked_now = register_failure(user, settings, at)
        record_audit(
            db,
            actor=actor_of(user),
            action="auth.totp_failed",
            target=target_of(user),
            details={"method": method, "reason": reason, "step": "sign_in"},
            ip=ip,
        )
        if locked_now:
            session_row.revoked_at = at
            audit_lockout(db, user, settings, step="totp", ip=ip)
            _refuse(db, 401, "account_locked")
        _refuse(db, 401, "totp_invalid")
    complete_sign_in(user, at)
    issued = rotate_session(
        db, request, user, session_row, mfa_passed=True, reauthenticated=True, at=at
    )
    details: dict[str, Any] = {"method": method, "session": session_id(issued.row)}
    if method == "recovery_code":
        details["recovery_codes_left"] = recovery_codes_left(db, user.id)
    record_audit(
        db, actor=actor_of(user), action="auth.totp", target=target_of(user), details=details, ip=ip
    )
    set_session_cookies(response, issued, secure=config.api.secure_cookies)
    return _state(db, user, mfa_passed=True)


class TotpSetup(BaseModel):
    """Everything an authenticator app needs; shown once, never logged."""

    secret: str
    otpauth_uri: str
    qr_svg: str
    issuer: str
    account: str
    digits: int
    period: int
    algorithm: str


def _check_enrolment(user: User, current: CurrentUser) -> None:
    """Enrolment is either the sign-in step (session waiting for the second factor, the password
    was just entered) or voluntary from a full session: then it needs a recent
    re-authentication, so an unattended or stolen session cannot bind its own authenticator to
    the account and lock the owner out."""
    if user.totp_enabled:
        raise auth_error(409, "totp_already_enabled")
    if current.mfa_passed:
        if current.must_change_password:
            raise auth_error(403, "password_change_required")
        ensure_recent_auth(current)


@router.get("/totp/setup")
def totp_setup(
    request: Request, db: DbSession, config: ConfigDep, current: PendingMfaUserDep
) -> TotpSetup:
    """Start (or show again) the TOTP enrolment: secret, ``otpauth://`` URI and QR code (SVG).

    Repeated calls show the same pending secret until it is confirmed (the call is idempotent).
    A pending secret that no longer decrypts (e.g. a restored server with a new ``secret.key``)
    is replaced: it was never confirmed, so nothing depends on it. Voluntary enrolment (full
    session) needs a recent re-authentication (403 ``reauth_required``).

    A GET that stores the pending secret on the first call (the contract's ``GET
    /auth/totp/setup``): it is idempotent, changes nothing the user relies on (the second factor
    is enabled only by ``POST /auth/totp/confirm``, which needs the CSRF token), and the
    SameSite=Strict session cookie is never sent with a cross-site request.
    """
    user = _user(db, current)
    _check_enrolment(user, current)
    secret = None
    if user.totp_secret_encrypted is not None:
        try:
            secret = totp.decrypt_secret(config.secrets_dir, user.totp_secret_encrypted)
        except totp.SecretKeyUnavailableError:
            secret = None
    if secret is None:
        secret = totp.new_secret()
        try:
            user.totp_secret_encrypted = totp.encrypt_secret(config.secrets_dir, secret)
        except totp.SecretKeyUnavailableError as exc:
            raise auth_error(503, "secret_key_missing") from exc
        user.totp_last_counter = None
        record_audit(
            db,
            actor=actor_of(user),
            action="auth.totp_setup",
            target=target_of(user),
            ip=client_ip(request),
        )
    uri = totp.provisioning_uri(secret, user.username)
    return TotpSetup(
        secret=secret,
        otpauth_uri=uri,
        qr_svg=totp.qr_svg(uri),
        issuer=totp.ISSUER,
        account=user.username,
        digits=totp.TOTP_DIGITS,
        period=totp.TOTP_PERIOD,
        algorithm="SHA1",
    )


class TotpEnrolled(BaseModel):
    #: Shown once; only their hashes are stored.
    recovery_codes: list[str]
    state: SignInState


@router.post("/totp/confirm")
def totp_confirm(
    body: CodeBody,
    request: Request,
    response: Response,
    db: DbSession,
    config: ConfigDep,
    current: PendingMfaUserDep,
) -> TotpEnrolled:
    """Finish the enrolment with a code from the app; returns the recovery codes once."""
    ip = client_ip(request)
    _rate_limit(request, db, "totp")
    at = utc_now()
    user = _user(db, current)
    _check_enrolment(user, current)
    check = totp.check_totp(_secret(config, user), body.code, last_counter=None, at=at)
    if check.counter is None:
        record_audit(
            db,
            actor=actor_of(user),
            action="auth.totp_failed",
            target=target_of(user),
            details={"method": "totp", "reason": "invalid", "step": "enrolment"},
            ip=ip,
        )
        _refuse(db, 400, "totp_invalid")
    user.totp_enabled = True
    user.totp_last_counter = check.counter  # the confirmation code cannot sign in again
    codes = replace_recovery_codes(db, user.id)
    details: dict[str, Any] = {"recovery_codes": len(codes)}
    if not current.mfa_passed:
        complete_sign_in(user, at)
        issued = rotate_session(
            db,
            request,
            user,
            current_session(db, current),
            mfa_passed=True,
            reauthenticated=True,
            at=at,
        )
        details["session"] = session_id(issued.row)
        set_session_cookies(response, issued, secure=config.api.secure_cookies)
    record_audit(
        db,
        actor=actor_of(user),
        action="auth.totp_enrol",
        target=target_of(user),
        details=details,
        ip=ip,
    )
    return TotpEnrolled(recovery_codes=codes, state=_state(db, user, mfa_passed=True))


class RecoveryCodes(BaseModel):
    recovery_codes: list[str]


@router.post("/recovery-codes")
def regenerate_recovery_codes(request: Request, db: DbSession, current: RecentDep) -> RecoveryCodes:
    """Replace the recovery codes (the old ones stop working); needs a recent re-auth."""
    user = _user(db, current)
    if not user.totp_enabled:
        raise auth_error(409, "recovery_codes_unavailable")
    codes = replace_recovery_codes(db, user.id)
    record_audit(
        db,
        actor=current,
        action="auth.recovery_codes_regenerate",
        target=target_of(user),
        details={"recovery_codes": len(codes)},
        ip=client_ip(request),
    )
    return RecoveryCodes(recovery_codes=codes)


# -- 3. password change and re-authentication -----------------------------------------------------


class PasswordBody(StrictModel):
    current_password: PasswordInput
    new_password: PasswordInput


@router.post("/password")
def change_password(
    body: PasswordBody,
    request: Request,
    response: Response,
    db: DbSession,
    config: ConfigDep,
    current: CurrentUserDep,
) -> SignInState:
    """Change the own password (current one required); ends every other session."""
    ip = client_ip(request)
    _rate_limit(request, db, "reauth")
    at = utc_now()
    settings = AuthSettings.load(db)
    user = _user(db, current)
    session_row = current_session(db, current)
    verified = verify_password(user.password_hash, body.current_password)
    if not verified.ok:
        locked_now = register_failure(user, settings, at)
        record_audit(
            db,
            actor=current,
            action="auth.password_change_failed",
            target=target_of(user),
            details={"reason": "bad_password"},
            ip=ip,
        )
        if locked_now:
            session_row.revoked_at = at
            audit_lockout(db, user, settings, step="password_change", ip=ip)
            _refuse(db, 401, "account_locked")
        _refuse(db, 403, "credentials_invalid")
    problems = password_problems(
        body.new_password,
        _policy(config, settings, username=user.username, name=user.display_name),
    )
    if body.new_password == body.current_password:
        problems.insert(0, REUSED)
    if problems:
        raise password_rejected(problems)
    forced = user.must_change_password
    user.password_hash = hash_password(body.new_password)
    user.password_changed_at = at
    user.must_change_password = False
    user.failed_attempts = 0
    others = revoke_sessions(db, user.id, at=at, keep=session_row.token_hash)
    issued = rotate_session(
        db, request, user, session_row, mfa_passed=True, reauthenticated=False, at=at
    )
    record_audit(
        db,
        actor=current,
        action="auth.password_change",
        target=target_of(user),
        details={
            "forced": forced,
            "other_sessions_revoked": others,
            "session": session_id(issued.row),
        },
        ip=ip,
    )
    set_session_cookies(response, issued, secure=config.api.secure_cookies)
    return _state(db, user, mfa_passed=True)


class ReauthBody(StrictModel):
    password: PasswordInput
    #: Required when TOTP is enrolled.
    code: str | None = Field(default=None, max_length=64)


class Reauthenticated(BaseModel):
    reauthenticated_at: datetime
    valid_minutes: int


@router.post("/reauth")
def reauth(
    body: ReauthBody, request: Request, db: DbSession, config: ConfigDep, current: SignedInDep
) -> Reauthenticated:
    """Confirm password (+ TOTP) before a sensitive action (``require_recent_auth``)."""
    ip = client_ip(request)
    _rate_limit(request, db, "reauth")
    at = utc_now()
    settings = AuthSettings.load(db)
    user = _user(db, current)
    session_row = current_session(db, current)
    reason = None
    counter = None
    verified = verify_password(user.password_hash, body.password)
    if is_locked(user, at):
        reason = "locked"
    elif not verified.ok:
        reason = "bad_password"
    elif user.totp_enabled:
        check = totp.check_totp(
            _secret(config, user), body.code or "", last_counter=user.totp_last_counter, at=at
        )
        counter = check.counter
        if counter is None:
            reason = "replay" if check.replay else "bad_code"
    if reason is not None:
        locked_now = reason != "locked" and register_failure(user, settings, at)
        record_audit(
            db,
            actor=current,
            action="auth.reauth_failed",
            target=target_of(user),
            details={"reason": reason},
            ip=ip,
        )
        if locked_now:
            session_row.revoked_at = at
            audit_lockout(db, user, settings, step="reauth", ip=ip)
            _refuse(db, 401, "account_locked")
        _refuse(db, 403, "credentials_invalid")
    if counter is not None:
        user.totp_last_counter = counter
    if verified.new_hash is not None:
        user.password_hash = verified.new_hash
    user.failed_attempts = 0
    session_row.reauthenticated_at = at
    record_audit(
        db,
        actor=current,
        action="auth.reauth",
        target=target_of(user),
        details={"session": session_id(session_row)},
        ip=ip,
    )
    return Reauthenticated(reauthenticated_at=at, valid_minutes=RECENT_AUTH_MINUTES)


# -- session --------------------------------------------------------------------------------------


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request, db: DbSession, config: ConfigDep, current: PendingMfaUserDep
) -> Response:
    """End this session (also one still waiting for TOTP) and clear the cookies."""
    row = current_session(db, current)
    row.revoked_at = utc_now()
    record_audit(
        db,
        actor=current,
        action="auth.logout",
        target=target_of(current.id),
        details={"session": session_id(row)},
        ip=client_ip(request),
    )
    response = Response(status_code=status.HTTP_204_NO_CONTENT)
    secure = config.api.secure_cookies
    response.delete_cookie(
        SESSION_COOKIE, path="/", secure=secure, httponly=True, samesite="strict"
    )
    response.delete_cookie(CSRF_COOKIE, path="/", secure=secure, samesite="strict")
    return response


class MfaStatus(BaseModel):
    passed: bool
    enabled: bool
    required: bool
    recovery_codes_left: int


class PrivacyStatus(BaseModel):
    version: str
    accepted: bool
    required: bool


class SessionInfo(BaseModel):
    created_at: datetime
    expires_at: datetime
    reauthenticated_at: datetime | None


class Me(BaseModel):
    user: UserSummary
    email: str | None
    #: Empty until the second factor is passed.
    permissions: list[str]
    must_change_password: bool
    mfa: MfaStatus
    privacy_notice: PrivacyStatus
    idle_timeout_minutes: int
    session: SessionInfo
    next: Step
    #: ``site_name`` of arcilogger.toml (the organisation's name); "" while it is not set.
    site_name: str


@router.get("/me")
def me(db: DbSession, config: ConfigDep, current: PendingMfaUserDep) -> Me:
    """The signed-in user, role, permissions and what the web app must show next.

    Also answers a session that still waits for TOTP (``mfa.passed = false``, no permissions),
    so a reloaded sign-in page knows whether to ask for a code or to show the QR enrolment.
    """
    user = db.get_one(User, current.id)
    settings = AuthSettings.load(db)
    row = current_session(db, current)
    version = privacy_version(db)
    absolute = min(row.expires_absolute, row.created_at + timedelta(hours=settings.absolute_hours))
    return Me(
        user=UserSummary.of(user),
        email=user.email,
        permissions=sorted(permissions_for(user.role)) if current.mfa_passed else [],
        must_change_password=user.must_change_password,
        mfa=MfaStatus(
            passed=current.mfa_passed,
            enabled=user.totp_enabled,
            required=settings.totp_required(user.role),
            recovery_codes_left=recovery_codes_left(db, user.id) if user.totp_enabled else 0,
        ),
        privacy_notice=PrivacyStatus(
            version=version,
            accepted=privacy_accepted(db, user.id, version),
            required=privacy_required(user.role),
        ),
        idle_timeout_minutes=settings.idle_minutes,
        session=SessionInfo(
            created_at=row.created_at,
            expires_at=absolute,
            reauthenticated_at=row.reauthenticated_at,
        ),
        next=next_step(db, user, mfa_passed=current.mfa_passed),
        site_name=config.site_name,
    )


class PrivacyBody(StrictModel):
    version: str = Field(min_length=1, max_length=64)


@router.post("/privacy-notice")
def accept_privacy_notice(
    body: PrivacyBody, request: Request, db: DbSession, current: SignedInDep
) -> PrivacyStatus:
    """Record that the user read the KVKK notice of this version (once per version)."""
    version = privacy_version(db)
    if body.version != version:
        raise auth_error(409, "notice_outdated")
    if accept_privacy(db, current.id, version, ip=client_ip(request)):
        record_audit(
            db,
            actor=current,
            action="privacy.accept",
            target=target_of(current.id),
            details={"version": version},
            ip=client_ip(request),
        )
    return PrivacyStatus(version=version, accepted=True, required=privacy_required(current.role))


# -- first-run setup ------------------------------------------------------------------------------


class SetupStatusOut(BaseModel):
    #: No administrator exists yet: the web app shows the setup wizard.
    needs_setup: bool
    #: A setup token was issued and has not expired.
    token_active: bool
    #: ``auth.idle_minutes``: the sign-in page says "Oturum N dk hareketsizlikte kapanır" (spec
    #: 12.3) before anybody is signed in. Not sensitive — the same rule applies to everybody.
    idle_timeout_minutes: int


@router.get("/setup/status")
def setup_status_endpoint(db: DbSession) -> SetupStatusOut:
    """Whether the first administrator still has to be created, and the idle time-out the
    sign-in page announces (no sign-in needed)."""
    found = setup_status(db, at=utc_now())
    return SetupStatusOut(
        needs_setup=found.needs_setup,
        token_active=found.token_active,
        idle_timeout_minutes=AuthSettings.load(db).idle_minutes,
    )


class SetupAdminBody(StrictModel):
    token: str = Field(min_length=1, max_length=256)
    username: UsernameInput
    display_name: DisplayName
    password: PasswordInput


class SetupAdminOut(BaseModel):
    user: UserSummary
    #: Sign in next; TOTP enrolment follows at the first sign-in.
    next: str


@router.post("/setup/admin", status_code=status.HTTP_201_CREATED)
def setup_admin(
    body: SetupAdminBody, request: Request, db: DbSession, config: ConfigDep
) -> SetupAdminOut:
    """Create the first administrator with the one-time token from ``arcilogger-api setup-token``.

    Refused once any administrator exists. The password is final (no forced change); TOTP
    enrolment is required at the first sign-in (role ``admin``).
    """
    ip = client_ip(request)
    _rate_limit(request, db, "setup")
    at = utc_now()
    lock_user_management(db)
    if admin_exists(db):
        raise auth_error(409, "setup_done")
    problem = check_setup_token(db, body.token, at=at)
    if problem is not None:
        record_audit(
            db, actor=None, action="setup.admin_failed", details={"reason": problem}, ip=ip
        )
        _refuse(db, 403, "setup_token_invalid")
    username = normalize_username(body.username)
    if username is None:
        raise auth_error(422, "username_invalid")
    if db.scalar(select(User.id).where(User.username == username)) is not None:
        raise auth_error(409, "username_taken")
    settings = AuthSettings.load(db)
    problems = password_problems(
        body.password, _policy(config, settings, username=username, name=body.display_name)
    )
    if problems:
        raise password_rejected(problems)
    user = User(
        username=username,
        display_name=body.display_name,
        role="admin",
        password_hash=hash_password(body.password),
        must_change_password=False,
        password_changed_at=at,
    )
    db.add(user)
    db.flush()
    consume_setup_token(db)
    record_audit(
        db,
        actor=None,
        action="setup.admin_create",
        target=target_of(user),
        details={"username": username, "role": "admin"},
        ip=ip,
    )
    return SetupAdminOut(user=UserSummary.of(user), next="login")
