"""Error codes of the authentication, user and setup endpoints with Turkish and English texts.

They complement :data:`arcilogger_api.errors.MESSAGES` (the standard codes). Status semantics
(docs/SECURITY.md):

* **401** — no usable session (the client shows the sign-in screen), or a sign-in step failed
  (``login_failed``, ``totp_invalid`` on ``/auth/totp``) or the account was just locked while
  signed in (``account_locked``; the session was closed).
* **403** — signed in, but this request is refused (``forbidden``, ``csrf_failed``,
  ``password_change_required``, ``reauth_required``, ``privacy_notice_required``,
  ``credentials_invalid``, ``setup_token_invalid``).
* **409** — the request conflicts with the current state (``last_admin``, ``username_taken``…).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from arcilogger_api.auth.passwords import Problem
from arcilogger_api.errors import ApiError

AUTH_MESSAGES: Final[Mapping[str, tuple[str, str]]] = {
    # One text for unknown users, wrong passwords, locked and disabled accounts: the response
    # must not tell an attacker which user names exist.
    "login_failed": (
        "Giriş başarısız: kullanıcı adı veya parola hatalı ya da hesap geçici olarak kilitli.",
        "Sign-in failed: wrong user name or password, or the account is temporarily locked.",
    ),
    "totp_invalid": (
        "Doğrulama kodu hatalı veya süresi geçmiş.",
        "The verification code is wrong or has expired.",
    ),
    "account_locked": (
        "Çok fazla hatalı deneme: hesap geçici olarak kilitlendi ve oturum kapatıldı.",
        "Too many failed attempts: the account is temporarily locked and the session was closed.",
    ),
    "credentials_invalid": (
        "Parola veya doğrulama kodu hatalı.",
        "Wrong password or verification code.",
    ),
    "mfa_already_passed": (
        "İki adımlı doğrulama bu oturumda zaten tamamlandı.",
        "Two-factor authentication is already complete for this session.",
    ),
    "totp_not_enabled": (
        "Bu hesapta iki adımlı doğrulama kurulu değil; önce kurulumu tamamlayın.",
        "Two-factor authentication is not set up for this account; complete the setup first.",
    ),
    "totp_already_enabled": (
        "İki adımlı doğrulama bu hesapta zaten kurulu.",
        "Two-factor authentication is already set up for this account.",
    ),
    "totp_setup_missing": (
        "Önce iki adımlı doğrulama kurulumunu başlatın (QR kodu).",
        "Start the two-factor setup (QR code) first.",
    ),
    "secret_key_missing": (
        "Şifreleme anahtarı okunamıyor; sistem yöneticisine başvurun.",
        "The encryption key cannot be read; contact the system administrator.",
    ),
    "password_rejected": (
        "Parola kabul edilmedi.",
        "The password was not accepted.",
    ),
    "privacy_notice_required": (
        "Kişisel veri içeren bu bölümü açmadan önce KVKK bilgilendirme metnini okuyup "
        "onaylamanız gerekiyor.",
        "Read and accept the KVKK privacy notice before opening this section with personal data.",
    ),
    "notice_outdated": (
        "Bilgilendirme metninin yeni bir sürümü var; sayfayı yenileyip tekrar okuyun.",
        "There is a newer version of the notice; reload the page and read it again.",
    ),
    "setup_done": (
        "İlk kurulum tamamlanmış: zaten bir yönetici hesabı var.",
        "Initial setup is complete: an administrator account already exists.",
    ),
    "setup_token_invalid": (
        "Kurulum anahtarı geçersiz veya süresi dolmuş. Sunucuda 'arcilogger-api setup-token' "
        "komutuyla yeni bir anahtar oluşturun.",
        "The setup token is invalid or has expired. Create a new one on the server with "
        "'arcilogger-api setup-token'.",
    ),
    "username_invalid": (
        "Kullanıcı adı 2-64 karakter olmalı; yalnızca küçük harf (a-z), rakam, nokta, alt çizgi "
        "ve tire içerebilir ve harf ya da rakamla başlamalı.",
        "The user name must be 2-64 characters long, contain only lower-case letters (a-z), "
        "digits, dots, underscores and hyphens, and start with a letter or digit.",
    ),
    "username_taken": ("Bu kullanıcı adı kullanılıyor.", "This user name is already in use."),
    "last_admin": (
        "Son etkin yönetici hesabının rolü değiştirilemez ve hesap devre dışı bırakılamaz.",
        "The role of the last active administrator cannot be changed and the account cannot "
        "be disabled.",
    ),
    "cannot_change_self": (
        "Bu işlem kendi hesabınızda yapılamaz; başka bir yöneticiden isteyin.",
        "This action cannot be applied to your own account; ask another administrator.",
    ),
    "recovery_codes_unavailable": (
        "Kurtarma kodları yalnızca iki adımlı doğrulama kuruluyken oluşturulabilir.",
        "Recovery codes can only be created while two-factor authentication is set up.",
    ),
}


def password_rejected(problems: Sequence[Problem]) -> ApiError:
    """422 ``password_rejected``: the first problem as message, all of them in ``details``."""
    first = problems[0]
    return ApiError(
        422,
        "password_rejected",
        first.message_tr,
        first.message_en,
        details=[problem.as_dict() for problem in problems],
    )


def auth_error(
    status_code: int,
    code: str,
    *,
    headers: Mapping[str, str] | None = None,
    details: Any = None,
) -> ApiError:
    """An :class:`ApiError` with the texts of :data:`AUTH_MESSAGES` (standard codes otherwise)."""
    texts = AUTH_MESSAGES.get(code)
    if texts is None:
        return ApiError(status_code, code, headers=headers, details=details)
    return ApiError(status_code, code, texts[0], texts[1], headers=headers, details=details)
