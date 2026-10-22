"""Licence gating of the API (licensing contract 5.2, 6.4, 6.6).

* :func:`require_module` — the router-level dependency of the paid modules' routers
  (``usage_analytics``: ``routers/usage.py``, ``usage_reports``: ``routers/reports.py``,
  ``bulk_import``: ``routers/imports.py``): a signed-in user first (401), then 403
  ``module_not_licensed`` unless the module is enabled. Every other route is 5651 Temel and
  never asks.
* :func:`current_license` / :data:`LicenseDep` — the (cached) status for ``GET /license``; 503
  ``license_unavailable`` when the licence subsystem failed.
* :func:`users_limit_max`, :func:`license_limit_error`, :func:`invalidate_license` — the users
  limit of ``routers/users.py`` (the router recounts the users under its lock).

The status comes from :func:`arcilogger_db.license_guard.safe_status` (never an exception: a
broken licence subsystem gives ``core_5651`` only and no users limit) and is kept
:data:`~arcilogger_api.deps.LICENSE_CACHE_SECONDS` in ``AppState.license_cache`` (a failure is
never kept: the next request tries again). This module
imports no licence code at module level, so the API starts — and every 5651 route works — even
when the licence package cannot be imported.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Annotated, Final

from fastapi import Depends, Request, params
from sqlalchemy.orm import Session

from arcilogger_api.deps import DbSession, get_state
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUserDep
from arcilogger_db.license_guard import (
    GuardedStatus,
    license_unavailable_texts,
    module_not_licensed_texts,
    safe_status,
)

if TYPE_CHECKING:
    from arcilogger_core.license.state import LicenseStatus

__all__ = [
    "MODULE_MARKER",
    "LicenseDep",
    "current_license",
    "guarded_status",
    "invalidate_license",
    "license_limit_error",
    "license_unavailable",
    "require_module",
    "users_limit_max",
]

#: Attribute of the dependency callable returned by :func:`require_module` (route
#: classification test B-T9).
MODULE_MARKER: Final = "__arci_module__"


def guarded_status(request: Request, db: Session, *, commit: bool = False) -> GuardedStatus:
    """The cached guarded status, computed on ``db`` when the cache is empty or expired.

    ``commit``: commit ``db`` after a computation (the installation row, when it was missing,
    then stays even if the request is refused afterwards) — only where nothing else of the
    request is in that transaction yet (the gating dependency, after authentication).
    """
    state = get_state(request)
    computed: list[bool] = []

    def compute() -> GuardedStatus:
        computed.append(True)
        return safe_status(db, config=state.require_config(), layout=state.layout)

    # A failure is not kept: the next request tries again (a passing database hiccup must not
    # lock the paid modules for the whole cache period).
    guarded = state.license_cache.get_or_compute(
        compute, keep=lambda value: value.status is not None
    )
    if commit and computed:
        db.commit()
    return guarded


def invalidate_license(request: Request) -> None:
    """Forget the cached status of this process (after an install, removal, user change)."""
    get_state(request).license_cache.invalidate()


def license_unavailable() -> ApiError:
    """503 ``license_unavailable`` (the licence code cannot be loaded)."""
    text_tr, text_en = license_unavailable_texts()
    return ApiError(503, "license_unavailable", text_tr, text_en)


def require_module(key: str) -> params.Depends:
    """Router-level dependency: needs a signed-in user (401 first), then
    403 module_not_licensed unless load_status(...).module_enabled(key).
    The returned dependency callable carries the attribute __arci_module__ = key."""

    def dependency(request: Request, db: DbSession, user: CurrentUserDep) -> None:
        guarded = guarded_status(request, db, commit=True)
        if guarded.module_enabled(key):
            return
        text_tr, text_en = module_not_licensed_texts(key)
        raise ApiError(
            403,
            "module_not_licensed",
            text_tr,
            text_en,
            details={"module": key, "license_status": guarded.state},
        )

    setattr(dependency, MODULE_MARKER, key)
    dependency.__name__ = f"require_module_{key}"
    marked: params.Depends = Depends(dependency)
    return marked


def current_license(request: Request, db: DbSession) -> LicenseStatus:
    """The cached status; 503 ``license_unavailable`` when the licence subsystem failed."""
    guarded = guarded_status(request, db)
    if guarded.status is None:
        raise license_unavailable()
    return guarded.status


LicenseDep = Annotated["LicenseStatus", Depends(current_license)]


def users_limit_max(request: Request, db: Session) -> int | None:
    """``max`` of the users limit from the cached status (``None``: no limit, also when the
    licence subsystem failed). The caller recounts the active users itself."""
    limit = guarded_status(request, db).users_limit
    return None if limit is None else limit[0]


def license_limit_error(*, used: int, maximum: int) -> ApiError:
    """409 ``license_limit`` with the texts of the licence package (contract 6.5, 6.6)."""
    from arcilogger_core.license.errors import LicenseError  # loaded: a limit exists

    error = LicenseError(
        "license_limit",
        {"used": used, "max": maximum},
        details={"limit": "users", "max": maximum, "used": used},
    )
    return ApiError(
        error.http_status,
        error.code,
        error.message_tr,
        error.message_en,
        details=dict(error.details),
    )
