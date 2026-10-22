"""The KVKK privacy notice, enforced on endpoints with person-level data (spec 3.3).

Roles that can see person-level data (:data:`.accounts.PRIVACY_PERMISSIONS`: ``usage.person``,
``usage.anonymous``, ``lookup.run``, ``search.read`` — today all four roles: admin, auditor, hr
and viewer) accept the current notice version once (``POST /api/v1/auth/privacy-notice``; the
sign-in answers ``next = "privacy_notice"`` until then). The web app shows the notice, but the
**server** decides: endpoints that return person-level data use :func:`require_privacy_notice`
instead of ``require`` and answer 403 ``privacy_notice_required`` until the user accepted the
current version — so an API client or a bug in the web app cannot skip it::

    user: Annotated[CurrentUser, Depends(require_privacy_notice("lookup.run"))]
    user: Annotated[CurrentUser, Depends(require_privacy_notice("lookup.run", recent=True))]

``recent=True`` combines it with :func:`arcilogger_api.security.require_recent_auth`. Raising
``privacy.notice_version`` asks everyone again.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends
from sqlalchemy.orm import Session

from arcilogger_api.auth.accounts import privacy_accepted, privacy_required, privacy_version
from arcilogger_api.auth.messages import auth_error
from arcilogger_api.deps import DbSession
from arcilogger_api.security import CurrentUser, require, require_recent_auth


def ensure_privacy_notice(db: Session, user: CurrentUser) -> None:
    """403 ``privacy_notice_required`` unless ``user``'s role needs no notice or the user
    accepted the current version."""
    if privacy_required(user.role) and not privacy_accepted(db, user.id, privacy_version(db)):
        raise auth_error(403, "privacy_notice_required")


def require_privacy_notice(*permissions: str, recent: bool = False) -> Callable[..., CurrentUser]:
    """Dependency: like ``require(*permissions)`` (or ``require_recent_auth`` with ``recent``),
    and the current privacy notice accepted (:func:`ensure_privacy_notice`).

    Permissions are checked first: a user without them gets 403 ``forbidden``, not the notice.
    """
    allowed = require_recent_auth(*permissions) if recent else require(*permissions)

    # A default instead of Annotated[...]: FastAPI resolves postponed annotations in the module
    # globals, where the local ``allowed`` does not exist.
    def dependency(db: DbSession, user: CurrentUser = Depends(allowed)) -> CurrentUser:  # noqa: B008
        ensure_privacy_notice(db, user)
        return user

    return dependency
