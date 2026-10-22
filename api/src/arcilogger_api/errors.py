"""One error format for every endpoint (spec 16: "consistent error format with Turkish messages")::

    {"error": {"code": "forbidden", "message_tr": "…", "message_en": "…"}}

Validation errors (422) add ``"details": [{"loc": [...], "type": "...", "msg": "..."}]`` — never
the submitted values (they may contain passwords). Raise :class:`ApiError` in endpoints; plain
``HTTPException``\\ s and unexpected exceptions are converted by the handlers installed with
:func:`install_error_handlers`.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, Final, cast

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import OperationalError
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("arcilogger.api")

#: code → (Turkish, English). Feature modules may add their own codes with explicit texts.
MESSAGES: Final[Mapping[str, tuple[str, str]]] = {
    "bad_request": ("İstek geçersiz.", "The request is invalid."),
    "not_authenticated": ("Oturum açmanız gerekiyor.", "You need to sign in."),
    "session_invalid": (
        "Oturum bulunamadı; lütfen yeniden giriş yapın.",
        "Session not found; please sign in again.",
    ),
    "session_expired": (
        "Oturumun süresi doldu; lütfen yeniden giriş yapın.",
        "The session has expired; please sign in again.",
    ),
    "session_idle": (
        "Oturum hareketsizlik nedeniyle kapandı; lütfen yeniden giriş yapın.",
        "The session was closed after inactivity; please sign in again.",
    ),
    "session_revoked": (
        "Oturum kapatıldı; lütfen yeniden giriş yapın.",
        "The session was closed; please sign in again.",
    ),
    "user_inactive": ("Kullanıcı hesabı devre dışı.", "The user account is disabled."),
    "mfa_required": (
        "İki adımlı doğrulama gerekiyor.",
        "Two-factor authentication is required.",
    ),
    "password_change_required": (
        "Devam etmeden önce parolanızı değiştirmeniz gerekiyor.",
        "You need to change your password before continuing.",
    ),
    "forbidden": ("Bu işlem için yetkiniz yok.", "You do not have permission for this action."),
    "csrf_failed": (
        "Güvenlik doğrulaması (CSRF) başarısız; sayfayı yenileyip tekrar deneyin.",
        "Security check (CSRF) failed; reload the page and try again.",
    ),
    "not_found": ("Kayıt bulunamadı.", "Not found."),
    "method_not_allowed": ("Bu yöntem desteklenmiyor.", "Method not allowed."),
    "conflict": ("Kayıt başka bir işlemle çakışıyor.", "The record conflicts with another change."),
    "too_large": ("İstek çok büyük.", "The request is too large."),
    "validation_error": ("Gönderilen veriler geçersiz.", "The submitted data is invalid."),
    "rate_limited": (
        "Çok fazla deneme yapıldı; lütfen biraz sonra tekrar deneyin.",
        "Too many attempts; please try again later.",
    ),
    "internal_error": (
        "Beklenmeyen bir hata oluştu; ayrıntılar sistem kaydında.",
        "An unexpected error occurred; details are in the system log.",
    ),
    "unavailable": (
        "Hizmet şu anda kullanılamıyor (veritabanı?).",
        "The service is unavailable right now (database?).",
    ),
}

_STATUS_CODES: Final[Mapping[int, str]] = {
    400: "bad_request",
    401: "not_authenticated",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "too_large",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    503: "unavailable",
}


class ApiError(Exception):
    """An error response in the standard format."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message_tr: str | None = None,
        message_en: str | None = None,
        *,
        headers: Mapping[str, str] | None = None,
        details: Any = None,
    ) -> None:
        default_tr, default_en = MESSAGES.get(code, MESSAGES["internal_error"])
        self.status_code = status_code
        self.code = code
        self.message_tr = message_tr or default_tr
        self.message_en = message_en or default_en
        self.headers = dict(headers or {})
        self.details = details
        super().__init__(f"{status_code} {code}")

    def body(self) -> dict[str, Any]:
        error: dict[str, Any] = {
            "code": self.code,
            "message_tr": self.message_tr,
            "message_en": self.message_en,
        }
        if self.details is not None:
            error["details"] = self.details
        return {"error": error}

    def response(self) -> JSONResponse:
        return JSONResponse(self.body(), status_code=self.status_code, headers=self.headers)


def error_response(status_code: int, code: str) -> JSONResponse:
    return ApiError(status_code, code).response()


async def _api_error(_: Request, exc: Exception) -> JSONResponse:
    return cast(ApiError, exc).response()


async def _http_error(_: Request, raised: Exception) -> JSONResponse:
    exc = cast(StarletteHTTPException, raised)
    detail: Any = exc.detail  # FastAPI's HTTPException also accepts a dict
    if isinstance(detail, dict) and {"code", "message_tr", "message_en"} <= detail.keys():
        error = ApiError(
            exc.status_code, detail["code"], detail["message_tr"], detail["message_en"]
        )
    else:
        error = ApiError(exc.status_code, _STATUS_CODES.get(exc.status_code, "bad_request"))
    error.headers = dict(exc.headers or {})
    return error.response()


async def _validation_error(_: Request, raised: Exception) -> JSONResponse:
    exc = cast(RequestValidationError, raised)
    details = [
        {"loc": list(item.get("loc", ())), "type": item.get("type"), "msg": item.get("msg")}
        for item in exc.errors()
    ]
    return ApiError(422, "validation_error", details=details).response()


async def _database_unavailable(request: Request, exc: Exception) -> JSONResponse:
    log.error("database unavailable on %s %s: %s", request.method, request.url.path, exc)
    return error_response(503, "unavailable")


async def _unexpected(request: Request, exc: Exception) -> JSONResponse:
    log.error("unhandled error on %s %s", request.method, request.url.path, exc_info=exc)
    return error_response(500, "internal_error")


def install_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(ApiError, _api_error)
    app.add_exception_handler(StarletteHTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(OperationalError, _database_unavailable)
    app.add_exception_handler(Exception, _unexpected)
