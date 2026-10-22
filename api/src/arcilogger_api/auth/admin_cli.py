"""Console commands for the first administrator and for recovery (``arcilogger-api …``).

* ``setup-token`` — prints a one-time token for the web setup wizard (valid 24 h, only its hash
  is stored). Refused once an administrator exists.
* ``create-admin`` — asks for user name, display name and password (twice, hidden) and creates
  an administrator directly. For recovery, e.g. when every administrator lost the phone and the
  recovery codes; the new administrator enrols TOTP at the first sign-in and can then reset the
  others. Whoever can run it already controls the server (console access, ``secret.key``).

Both write an audit entry naming the Windows account that ran them (``os_user``).

**Users limit** (licensing contract 6.6, [C-14]): while the active users reach the licence's
``users`` limit, ``create-admin`` refuses (exit 1, the ``license_limit`` message, audited as
``license.limit_refused`` with ``"operation": "create_admin"``) — unless it is a true recovery
(no active administrator can sign in: every one is disabled or locked; the new administrator's
``user.create`` entry then says ``"license_over_limit": true``), or the operator names an active
user with ``--deactivate USERNAME``, who is disabled in the same transaction (net zero; audited as
``user.update`` with ``"via": "create_admin"``). A broken licence subsystem never refuses.

Exit codes as ``arcilogger-db``: 0 done, 1 refused (user name taken, password rejected),
2 usage or configuration error, 3 database unreachable; ``setup-token`` only: 4 an administrator
exists already (no token needed, so the installer can tell this apart from a failure; an
uncaught error also exits with 1).
"""

from __future__ import annotations

import getpass
from collections.abc import Callable
from typing import Final

from pydantic import TypeAdapter, ValidationError
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.audit import record_audit
from arcilogger_api.auth.accounts import (
    AuthSettings,
    is_locked,
    lock_user_management,
    normalize_username,
    revoke_sessions,
    target_of,
)
from arcilogger_api.auth.firstrun import TOKEN_HOURS, SetupDoneError, issue_setup_token
from arcilogger_api.auth.passwords import PolicyContext, hash_password, password_problems
from arcilogger_api.auth.schemas import DisplayName
from arcilogger_api.deps import LAYOUT_INFO_KEY
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.secretstore import SecretStoreError
from arcilogger_core.timeutil import to_site, utc_now
from arcilogger_db.engine import make_engine, session_factory
from arcilogger_db.license_guard import safe_status
from arcilogger_db.models import User

EXIT_OK: Final = 0
EXIT_REFUSED: Final = 1
EXIT_USAGE: Final = 2
EXIT_UNREACHABLE: Final = 3
EXIT_SETUP_DONE: Final = 4

Ask = Callable[[str], str]


class _UnreachableError(Exception):
    pass


def engine_for(config: Config) -> Engine:
    """Indirection so tests can supply the engine of their test database."""
    return make_engine(config, application="api-cli", pool_size=1)


def _os_user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # no USERNAME/LOGNAME and no account database entry
        return "unknown"


def _run(config: Config, work: Callable[[Session], int]) -> int:
    """Run ``work`` in one transaction of a session whose audit entries reach the spool."""
    try:
        engine = engine_for(config)
    except SecretStoreError as exc:
        print(f"Gizli anahtar okunamadı: {exc}")
        return EXIT_USAGE
    try:
        factory: sessionmaker[Session] = session_factory(
            engine, **{LAYOUT_INFO_KEY: Layout(config.data_dir)}
        )
        with factory() as db:
            result = work(db)
            if result == EXIT_OK:
                db.commit()
            return result
    except OperationalError as exc:
        reason = str(getattr(exc, "orig", None) or exc).strip().splitlines()[0]
        print(f"Veritabanına ulaşılamadı: {reason}")
        return EXIT_UNREACHABLE
    finally:
        engine.dispose()


def setup_token(config: Config) -> int:
    """Print a new setup token (the previous one stops working)."""
    issued: list[str] = []

    def work(db: Session) -> int:
        try:
            token, expires = issue_setup_token(db, at=utc_now())
        except SetupDoneError:
            print(
                "Bir yönetici hesabı zaten var; kurulum anahtarı oluşturulmadı.\n"
                "Kurtarma için: arcilogger-api create-admin"
            )
            return EXIT_SETUP_DONE
        record_audit(
            db,
            actor=None,
            action="setup.token_create",
            details={"expires_at": expires, "os_user": _os_user()},
        )
        issued.extend([token, f"{to_site(expires):%d.%m.%Y %H:%M}"])
        return EXIT_OK

    result = _run(config, work)
    if result == EXIT_OK:
        token, expires = issued
        print("İlk kurulum anahtarı (yalnızca bir kez gösterilir):\n")
        # Indented, alone on its line: the installer writes indented lines past the PowerShell
        # host, so no transcript records the token (Show-SetupToken, Install-Arcilogger.ps1).
        print(f"    {token}\n")
        print(
            f"Geçerlilik: {TOKEN_HOURS} saat ({expires} tarihine kadar). Web arayüzündeki "
            "kurulum sihirbazına girin; anahtar ilk yönetici oluşturulunca geçersiz olur."
        )
    return result


def _admin_can_sign_in(db: Session) -> bool:
    """Some active administrator is not locked out (else ``create-admin`` is a true recovery)."""
    at = utc_now()
    admins = db.scalars(select(User).where(User.role == "admin", User.active))
    return any(not is_locked(admin, at) for admin in admins)


def _active_users(db: Session) -> int:
    return int(db.scalar(select(func.count()).select_from(User).where(User.active)) or 0)


def _limit_refusal(used: int, maximum: int) -> tuple[str, str]:
    from arcilogger_core.license.errors import LicenseError  # loaded: a limit exists

    error = LicenseError("license_limit", {"used": used, "max": maximum})
    return error.message_tr, error.message_en


def create_admin(
    config: Config,
    *,
    ask: Ask = input,
    ask_secret: Ask = getpass.getpass,
    deactivate: str | None = None,
) -> int:
    """Interactive: create an administrator (recovery when nobody can sign in).

    ``deactivate``: an active user disabled in the same transaction (``--deactivate``), so a
    full users limit is not passed.
    """
    if deactivate is not None and normalize_username(deactivate) is None:
        print("Geçersiz --deactivate kullanıcı adı [invalid --deactivate user name].")
        return EXIT_USAGE
    try:
        username = normalize_username(ask("Kullanıcı adı: "))
        name_text = ask("Görünen ad: ")
    except EOFError:
        return EXIT_USAGE
    if username is None:
        print("Geçersiz kullanıcı adı: 2-64 karakter, küçük harf (a-z), rakam, '.', '_', '-'.")
        return EXIT_USAGE
    try:
        display_name = TypeAdapter(DisplayName).validate_python(name_text)
    except ValidationError:
        print("Geçersiz görünen ad (1-200 karakter, kontrol karakteri olmadan).")
        return EXIT_USAGE
    try:
        password = ask_secret("Parola: ")
        again = ask_secret("Parola (tekrar): ")
    except EOFError:
        return EXIT_USAGE
    if password != again:
        print("Parolalar aynı değil.")
        return EXIT_USAGE

    def work(db: Session) -> int:
        lock_user_management(db)
        if db.scalar(select(User.id).where(User.username == username)) is not None:
            print(f"'{username}' kullanıcı adı kullanılıyor.")
            return EXIT_REFUSED
        retired: User | None = None
        if deactivate is not None:
            retired = db.scalars(
                select(User)
                .where(User.username == normalize_username(deactivate))
                .with_for_update()
            ).first()
            if retired is None or not retired.active:
                print(
                    f"--deactivate: '{deactivate}' adında etkin bir kullanıcı yok "
                    f"[no active user '{deactivate}']."
                )
                return EXIT_REFUSED
        problems = password_problems(
            password,
            PolicyContext(
                min_length=AuthSettings.load(db).password_min_length,
                username=username,
                display_name=display_name,
                site_name=config.site_name,
            ),
        )
        if problems:
            print("Parola kabul edilmedi:")
            for problem in problems:
                print(f"  - {problem.message_tr}")
            return EXIT_REFUSED
        limit = safe_status(db, config=config, layout=Layout(config.data_dir)).users_limit
        maximum = limit[0] if limit is not None else None
        over_limit = False
        if maximum is not None and retired is None:
            used = _active_users(db)
            if used >= maximum:
                if _admin_can_sign_in(db):
                    db.rollback()
                    record_audit(
                        db,
                        actor=None,
                        action="license.limit_refused",
                        target=f"user:{username}",
                        details={
                            "limit": "users",
                            "max": maximum,
                            "used": used,
                            "username": username,
                            "operation": "create_admin",
                            "os_user": _os_user(),
                        },
                    )
                    db.commit()
                    text_tr, text_en = _limit_refusal(used, maximum)
                    print(text_tr)
                    print(f"[{text_en}]")
                    print(
                        "Var olan bir kullanıcıyı --deactivate KULLANICI ile kapatarak yeniden "
                        "deneyin [retry with --deactivate USERNAME to disable an existing user]."
                    )
                    return EXIT_REFUSED
                over_limit = True  # true recovery: no administrator can sign in
        at = utc_now()
        if retired is not None:
            retired.active = False
            revoked = revoke_sessions(db, retired.id, at=at)
            db.flush()
            record_audit(
                db,
                actor=None,
                action="user.update",
                target=target_of(retired),
                details={
                    "changes": {"active": {"old": True, "new": False}},
                    "sessions_revoked": revoked,
                    "via": "create_admin",
                    "os_user": _os_user(),
                },
            )
        user = User(
            username=username,
            display_name=display_name,
            role="admin",
            password_hash=hash_password(password),
            must_change_password=False,
            password_changed_at=at,
        )
        db.add(user)
        db.flush()
        details: dict[str, object] = {
            "username": username,
            "role": "admin",
            "via": "cli",
            "os_user": _os_user(),
        }
        if over_limit:
            details["license_over_limit"] = True
        record_audit(db, actor=None, action="user.create", target=target_of(user), details=details)
        return EXIT_OK

    result = _run(config, work)
    if result == EXIT_OK:
        print(
            f"Yönetici '{username}' oluşturuldu. İlk girişte iki adımlı doğrulama (TOTP) kurulacak."
        )
    return result
