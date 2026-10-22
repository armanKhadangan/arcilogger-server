"""Roles, permissions, sessions and CSRF (build specification section 13).

**Roles** (``users.role``): ``admin`` Yönetici, ``auditor`` Denetçi, ``hr`` İK Raporları,
``viewer`` Rapor İzleyici. **Permissions** follow the columns of the spec 13 table; see
:data:`ROLE_PERMISSIONS` and docs/MVP-CONTRACTS.md for the interpretation of each cell.

**Sessions:** the browser holds a random token in the HttpOnly cookie ``arci_session``; the
database stores only its SHA-256 (``user_sessions.token_hash``). A session ends when revoked,
after ``auth.idle_minutes`` without a request, or ``auth.absolute_hours`` after login (settings
are read on every request, so a lowered value applies at once). Requests the web app makes on
its own (dashboard refresh every 30 s, polling) send ``X-Arci-Background: 1``: they are
authenticated as usual but do **not** count as activity, so an open dashboard tab cannot keep
a session alive past the idle timeout (spec 13).

**CSRF** (double submit, bound to the session): every POST/PUT/PATCH/DELETE under ``/api/`` must
send the header ``X-CSRF-Token`` equal to the cookie ``arci_csrf`` (checked by
:class:`CsrfMiddleware` for every request), and for an authenticated request its SHA-256 must
equal ``user_sessions.csrf_token_hash`` (checked in :func:`get_current_user`). Comparisons are
constant-time. The middleware hands out an ``arci_csrf`` cookie to clients that have none, so the
login form can send one too.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.cookies import SimpleCookie
from typing import Annotated, Final

from fastapi import Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_core.iputil import canonical_ip
from arcilogger_core.timeutil import utc_now
from arcilogger_db.models import User, UserSession
from arcilogger_db.settings import get_settings

SESSION_COOKIE: Final = "arci_session"
CSRF_COOKIE: Final = "arci_csrf"
CSRF_HEADER: Final = "X-CSRF-Token"
#: Set to ``1`` on automatic requests (refresh, polling): they do not reset the idle timer.
BACKGROUND_HEADER: Final = "X-Arci-Background"
UNSAFE_METHODS: Final = frozenset({"POST", "PUT", "PATCH", "DELETE"})
TOKEN_BYTES: Final = 32

# -- roles and permissions -----------------------------------------------------------------------

ROLE_LABELS: Final[Mapping[str, tuple[str, str]]] = {
    "admin": ("Yönetici", "Administrator"),
    "auditor": ("Denetçi", "Auditor"),
    "hr": ("İK Raporları", "HR reports"),
    "viewer": ("Rapor İzleyici", "Report viewer"),
}

#: Permission → (Turkish, English) description. Columns of the spec 13 table first.
PERMISSIONS: Final[Mapping[str, tuple[str, str]]] = {
    # Panel
    "dashboard.view": ("Panel (tam)", "Dashboard (full)"),
    "dashboard.usage": ("Panel: kullanım kutuları", "Dashboard: usage widgets"),
    # Arama
    "search.read": ("Arama ve canlı akış (okuma)", "Search and live tail (read)"),
    "search.export": ("Arama sonuçlarını dışa aktarma", "Export search results"),
    # 5651 Sorgu
    "lookup.run": ("5651 sorgusu ve resmi rapor", "5651 lookup and official report"),
    # Kişi raporları / Anonim raporlar — both show names (owner decision 2026-10-20); pseudonyms
    # only with usage.pseudonymize_anonymous_role, for usage.anonymous without usage.person
    "usage.person": ("Kişi bazlı kullanım raporları", "Person-level usage reports"),
    "usage.anonymous": (
        "Kullanım raporları (adlarla; takma ad isteğe bağlı)",
        "Usage reports (with names; pseudonyms optional)",
    ),
    # Mühür/Kanıt
    "seal.read": ("Mühür takvimi, manifest ve belirteç indirme", "Seal calendar and downloads"),
    "seal.verify": ("Mühür doğrulama", "Verify seals"),
    "seal.manage": ("Şimdi mühürle", "Seal now"),
    "evidence.read": ("Kanıt paketlerini listeleme", "List evidence packages"),
    "evidence.download": ("Kanıt paketi indirme", "Download evidence packages"),
    "evidence.create": ("Kanıt paketi oluşturma", "Create evidence packages"),
    # Ayarlar / Kullanıcılar / Denetim
    "settings.manage": ("Ayarlar", "Settings"),
    "users.manage": ("Kullanıcılar ve roller", "Users and roles"),
    "audit.read": ("Denetim kaydı", "Audit log"),
    # Pages not in the spec table: administrators only until the owner decides otherwise.
    "sources.read": ("Kaynaklar", "Sources"),
    "sources.manage": ("Kaynakları düzenleme", "Edit sources"),
    "devices.read": ("Cihazlar ve kişiler", "Devices and persons"),
    "devices.manage": ("Cihaz ve kişi düzenleme", "Edit devices and persons"),
    "alerts.read": ("Güvenlik uyarıları", "Security alerts"),
    "alerts.manage": ("Uyarı durumu ve kurallar", "Alert state and rules"),
    "reports.manage": ("Rapor zamanlamaları", "Report schedules"),
    "compliance.read": ("Uyum durumu", "Compliance status"),
    "system.read": ("Sistem durumu", "System status"),
}
ALL_PERMISSIONS: Final = frozenset(PERMISSIONS)

ROLE_PERMISSIONS: Final[Mapping[str, frozenset[str]]] = {
    "admin": ALL_PERMISSIONS,
    "auditor": frozenset(
        {
            "dashboard.view",
            "dashboard.usage",
            "search.read",  # "✔ (read)": no export
            "lookup.run",
            "usage.anonymous",
            "seal.read",  # "✔ (verify/download)"
            "seal.verify",
            "evidence.read",
            "evidence.download",
            "audit.read",
        }
    ),
    "hr": frozenset(
        {
            "dashboard.usage",  # "✔ (usage only)"
            "usage.person",
            "usage.anonymous",
        }
    ),
    "viewer": frozenset({"dashboard.view", "dashboard.usage", "usage.anonymous"}),
}


def permissions_for(role: str) -> frozenset[str]:
    return ROLE_PERMISSIONS.get(role, frozenset())


# -- tokens ---------------------------------------------------------------------------------------


def new_token() -> str:
    """A fresh random token (URL-safe, 256 bits)."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def hash_token(token: str) -> str:
    """Lower-case hex SHA-256: what the database stores instead of the token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def tokens_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))


@dataclass(frozen=True)
class IssuedSession:
    token: str
    csrf_token: str
    row: UserSession


def create_session(
    db: Session,
    user: User,
    *,
    mfa_passed: bool,
    ip: str | None = None,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> IssuedSession:
    """Insert a ``user_sessions`` row; returns the tokens for the cookies (never stored)."""
    at = now or utc_now()
    hours = int(get_settings(db, keys=["auth.absolute_hours"])["auth.absolute_hours"])
    token, csrf = new_token(), new_token()
    row = UserSession(
        token_hash=hash_token(token),
        user_id=user.id,
        created_at=at,
        last_seen_at=at,
        expires_absolute=at + timedelta(hours=hours),
        ip=ip,
        user_agent=(user_agent or "")[:500] or None,
        mfa_passed=mfa_passed,
        csrf_token_hash=hash_token(csrf),
    )
    db.add(row)
    db.flush()
    return IssuedSession(token=token, csrf_token=csrf, row=row)


def set_session_cookies(
    response: Response, issued: IssuedSession, *, secure: bool, max_age: int | None = None
) -> None:
    """``arci_session`` (HttpOnly) and ``arci_csrf`` (readable by the web app), SameSite=Strict.

    Without ``max_age`` both are browser-session cookies; the server-side expiry rules apply.
    """
    response.set_cookie(
        SESSION_COOKIE,
        issued.token,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=max_age,
    )
    response.set_cookie(
        CSRF_COOKIE,
        issued.csrf_token,
        httponly=False,
        secure=secure,
        samesite="strict",
        path="/",
        max_age=max_age,
    )


def client_ip(request: Request) -> str | None:
    """The client's IP address (Uvicorn applies ``X-Forwarded-For`` from Caddy), else ``None``."""
    host = request.client.host if request.client else None
    if not host:
        return None
    try:
        return canonical_ip(host)
    except ValueError:
        return None


# -- current user ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class CurrentUser:
    id: int
    username: str
    display_name: str
    role: str
    permissions: frozenset[str]
    must_change_password: bool
    mfa_passed: bool
    session_token_hash: str
    reauthenticated_at: datetime | None

    def has(self, permission: str) -> bool:
        return permission in self.permissions


def _record_activity(db: Session, row: UserSession, now: datetime) -> None:
    """Record activity (``last_seen_at``) and commit it at once, before the endpoint runs.

    * **Committed now**, not with the request: the update locks the session row, and kept until
      the request ends that lock could deadlock with a request revoking this user's sessions
      (role change, password or TOTP reset) that holds a lock this request waits for (the user
      row, the user-management lock). Found by
      ``test_admins_demoting_each_other_at_once_leave_one_admin``.
    * **On the request's own session**, not on a second connection: a request must never hold
      two pooled connections at once. With N concurrent requests each holding one and waiting
      for a second, the pool runs dry and every one of them fails after ``pool_timeout``
      (``test_concurrent_requests_do_not_exhaust_the_connection_pool``).

    The commit returns the connection to the pool; the endpoint's queries start a new
    transaction. Everything read so far stays usable (the caller copied it into
    :class:`CurrentUser` before). A dependency that runs before authentication must therefore
    not write to the request's session (none does): its changes would be committed here too.
    """
    row.last_seen_at = now
    db.commit()


def _authenticate(request: Request, db: Session, *, allow_pending_mfa: bool) -> CurrentUser:
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        raise ApiError(401, "not_authenticated")
    found = db.execute(
        select(UserSession, User)
        .join(User, User.id == UserSession.user_id)
        .where(UserSession.token_hash == hash_token(token))
    ).one_or_none()
    if found is None:
        raise ApiError(401, "session_invalid")
    session_row, user = found
    now = utc_now()
    auth = get_settings(db, keys=["auth.idle_minutes", "auth.absolute_hours"])
    if session_row.revoked_at is not None:
        raise ApiError(401, "session_revoked")
    absolute_end = min(
        session_row.expires_absolute,
        session_row.created_at + timedelta(hours=int(auth["auth.absolute_hours"])),
    )
    if now >= absolute_end:
        raise ApiError(401, "session_expired")
    if now - session_row.last_seen_at > timedelta(minutes=int(auth["auth.idle_minutes"])):
        raise ApiError(401, "session_idle")
    if not user.active:
        raise ApiError(401, "user_inactive")
    if not session_row.mfa_passed and not allow_pending_mfa:
        raise ApiError(401, "mfa_required")
    if request.method in UNSAFE_METHODS:
        header = request.headers.get(CSRF_HEADER, "")
        if not header or not tokens_equal(hash_token(header), session_row.csrf_token_hash):
            raise ApiError(403, "csrf_failed")
    current = CurrentUser(
        id=user.id,
        username=user.username,
        display_name=user.display_name,
        role=user.role,
        permissions=permissions_for(user.role),
        must_change_password=user.must_change_password,
        mfa_passed=session_row.mfa_passed,
        session_token_hash=session_row.token_hash,
        reauthenticated_at=session_row.reauthenticated_at,
    )
    if request.headers.get(BACKGROUND_HEADER) != "1":
        _record_activity(db, session_row, now)
    return current


def get_current_user(request: Request, db: DbSession) -> CurrentUser:
    """The signed-in user (password and, where required, TOTP passed)."""
    return _authenticate(request, db, allow_pending_mfa=False)


def get_current_user_pending_mfa(request: Request, db: DbSession) -> CurrentUser:
    """Also accepts a session whose TOTP step is still open (only for the TOTP endpoints)."""
    return _authenticate(request, db, allow_pending_mfa=True)


CurrentUserDep = Annotated[CurrentUser, Depends(get_current_user)]
PendingMfaUserDep = Annotated[CurrentUser, Depends(get_current_user_pending_mfa)]


def require(*permissions: str) -> Callable[[CurrentUser], CurrentUser]:
    """Dependency: the current user must hold **all** ``permissions``.

    ``user: Annotated[CurrentUser, Depends(require("lookup.run"))]``
    """
    unknown = set(permissions) - ALL_PERMISSIONS
    if unknown:
        raise ValueError(f"unknown permission(s): {', '.join(sorted(unknown))}")

    def dependency(user: CurrentUserDep) -> CurrentUser:
        if user.must_change_password:
            raise ApiError(403, "password_change_required")
        if not all(user.has(permission) for permission in permissions):
            raise ApiError(403, "forbidden")
        return user

    return dependency


#: How long a complete sign-in or ``POST /auth/reauth`` counts as recent (spec 13).
RECENT_AUTH_MINUTES: Final = 5
REAUTH_REQUIRED_TEXTS: Final = (
    "Bu işlem için parolanızı yeniden girmeniz gerekiyor.",
    "Please confirm your password to continue with this action.",
)


def require_recent_auth(
    *permissions: str, minutes: int = RECENT_AUTH_MINUTES
) -> Callable[[CurrentUser], CurrentUser]:
    """Dependency for sensitive actions (spec 13: credentials, retention, evidence download).

    Like :func:`require` (all ``permissions``, password changed), and the user proved the
    password (+ TOTP where enrolled) within ``minutes``: at sign-in or with ``POST
    /api/v1/auth/reauth``. Otherwise 403 ``reauth_required``; the web app then asks for the
    password, calls ``/auth/reauth`` and repeats the request::

        user: Annotated[CurrentUser, Depends(require_recent_auth("evidence.download"))]
    """
    if minutes < 1:
        raise ValueError("minutes must be at least 1")
    allowed = require(*permissions)

    # A default instead of Annotated[...]: with postponed annotations FastAPI resolves an
    # annotation in the module globals, where the local ``allowed`` does not exist.
    def dependency(user: CurrentUser = Depends(allowed)) -> CurrentUser:  # noqa: B008
        ensure_recent_auth(user, minutes=minutes)
        return user

    return dependency


def ensure_recent_auth(user: CurrentUser, *, minutes: int = RECENT_AUTH_MINUTES) -> None:
    """403 ``reauth_required`` unless ``user`` proved the password within ``minutes``.

    For endpoints that need a recent re-authentication only in some cases (voluntary TOTP
    enrolment); everything else uses :func:`require_recent_auth`.
    """
    confirmed = user.reauthenticated_at
    if confirmed is None or utc_now() - confirmed > timedelta(minutes=minutes):
        raise ApiError(403, "reauth_required", *REAUTH_REQUIRED_TEXTS)


# -- CSRF middleware ------------------------------------------------------------------------------


class CsrfMiddleware:
    """Double-submit check for every unsafe ``/api/`` request; issues the cookie when missing."""

    def __init__(self, app: ASGIApp, *, secure_cookies: bool = True) -> None:
        self.app = app
        self.secure_cookies = secure_cookies

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        cookie = request.cookies.get(CSRF_COOKIE, "")
        if request.method in UNSAFE_METHODS and request.url.path.startswith("/api/"):
            header = request.headers.get(CSRF_HEADER, "")
            if not cookie or not header or not tokens_equal(cookie, header):
                response = ApiError(403, "csrf_failed").response()
                await response(scope, receive, send)
                return
        if cookie:
            await self.app(scope, receive, send)
            return

        async def send_with_cookie(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                if not _sets_cookie(headers, CSRF_COOKIE):
                    headers.append("set-cookie", self._cookie_header(new_token()))
            await send(message)

        await self.app(scope, receive, send_with_cookie)

    def _cookie_header(self, token: str) -> str:
        cookie: SimpleCookie = SimpleCookie()
        cookie[CSRF_COOKIE] = token
        morsel = cookie[CSRF_COOKIE]
        morsel["path"] = "/"
        morsel["samesite"] = "Strict"
        if self.secure_cookies:
            morsel["secure"] = True
        return morsel.OutputString()


def _sets_cookie(headers: MutableHeaders, name: str) -> bool:
    return any(value.startswith(f"{name}=") for value in headers.getlist("set-cookie"))
