"""The guarded way to the licence status for every shared path (licensing contract 5.2).

The API's routers, the worker's scheduler, ``arcilogger-api create-admin`` and ``arcilogger-db
license`` reach the licence code only through :func:`safe_status`. It imports
:mod:`arcilogger_db.licensing` inside a ``try``, runs :func:`~arcilogger_db.licensing.load_status`
inside a SAVEPOINT and catches **every** exception (an ``ImportError`` of the licence package, a
bug in the evaluation, an ``OSError`` of the fingerprint, a database error inside the
SAVEPOINT): the answer is then a :class:`GuardedStatus` without a status — ``core_5651`` (5651
Temel) only and **no users limit**, so a broken licence subsystem locks only the paid modules and
never refuses a user, a 5651 lookup, an evidence package or any other 5651 obligation.

This module imports no licence code at module level (``LicenseStatus`` only for type checking),
so importing it never fails because of the licence package.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from types import MappingProxyType
from typing import TYPE_CHECKING, Final

from sqlalchemy.orm import Session

from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import utc_now

if TYPE_CHECKING:
    from arcilogger_core.license.state import LicenseStatus

__all__ = [
    "CORE_MODULE",
    "FALLBACK_MESSAGES",
    "UNAVAILABLE",
    "GuardedStatus",
    "license_unavailable_texts",
    "module_not_licensed_texts",
    "safe_status",
]

log = logging.getLogger("arcilogger.license")

#: The module of every licence and every state (``arcilogger_core.license.CORE_MODULE``).
CORE_MODULE: Final = "core_5651"
#: ``GuardedStatus.state`` when the licence subsystem failed.
UNAVAILABLE: Final = "unavailable"

#: Copies of two texts of ``arcilogger_core.license.errors.MESSAGES`` for when that package
#: cannot be loaded (tested equal to the originals, B-T31).
FALLBACK_MESSAGES: Final[Mapping[str, tuple[str, str]]] = MappingProxyType(
    {
        "module_not_licensed": (
            "Bu özellik lisansınızda yok: {module_tr}. Ayrıntılar Ayarlar → Lisans sayfasında.",
            "This feature is not part of your licence: {module_en}. See Settings → Licence.",
        ),
        "license_unavailable": (
            "Lisans bileşeni yüklenemedi: ücretli modüller kapalı. 5651 işlevleri çalışmaya "
            "devam ediyor.",
            "The licence component could not be loaded: paid modules are off. The 5651 "
            "functions keep working.",
        ),
    }
)

_logged: set[str] = set()
_log_lock = threading.Lock()


@dataclass(frozen=True)
class GuardedStatus:
    """The licence status, or why it is not available.

    ``status`` is ``None`` when the licence subsystem failed (``error`` = the exception's class
    name): then only ``core_5651`` is enabled and no users limit applies.
    """

    status: LicenseStatus | None
    error: str | None = None

    def module_enabled(self, key: str) -> bool:
        """``status.module_enabled(key)``; on failure only ``core_5651``."""
        if self.status is None:
            return key == CORE_MODULE
        return self.status.module_enabled(key)

    @property
    def state(self) -> str:
        """``status.state``, or ``"unavailable"``."""
        return self.status.state if self.status is not None else UNAVAILABLE

    @property
    def users_limit(self) -> tuple[int | None, int] | None:
        """(max, used) of ``status.limits["users"]``; ``None`` on failure (no limit)."""
        if self.status is None:
            return None
        users = self.status.limits["users"]
        return users.max, users.used


def _log_once(exc: BaseException) -> None:
    kind = type(exc).__name__
    with _log_lock:
        if kind in _logged:
            return
        _logged.add(kind)
    log.error("licence subsystem unavailable: %s: %s", kind, exc)


def safe_status(
    session: Session,
    *,
    config: Config | None,
    layout: Layout,
    clock: Callable[[], datetime] = utc_now,
) -> GuardedStatus:
    """The status of :func:`arcilogger_db.licensing.load_status`, never an exception.

    Runs inside a SAVEPOINT of ``session`` (its writes — the installation row when it was
    missing — are kept with the caller's transaction; a failure rolls back only the SAVEPOINT).
    """
    try:
        from arcilogger_db import licensing

        with session.begin_nested():
            status = licensing.load_status(session, config=config, layout=layout, clock=clock)
    except Exception as exc:  # every failure locks only the paid modules (contract 5.2)
        _log_once(exc)
        return GuardedStatus(status=None, error=type(exc).__name__)
    return GuardedStatus(status=status)


def _module_names(key: str) -> tuple[str, str]:
    try:
        from arcilogger_core.license.modules import module

        item = module(key)
    except Exception:  # the licence package cannot be loaded, or an unknown key
        return key, key
    return item.name_tr, item.name_en


def _messages() -> Mapping[str, tuple[str, str]]:
    try:
        from arcilogger_core.license.errors import MESSAGES
    except Exception:  # the licence package cannot be loaded
        return FALLBACK_MESSAGES
    return MESSAGES


def module_not_licensed_texts(key: str) -> tuple[str, str]:
    """The ``module_not_licensed`` texts for ``key`` (the module key when the names are not
    available)."""
    name_tr, name_en = _module_names(key)
    text_tr, text_en = _messages().get(
        "module_not_licensed", FALLBACK_MESSAGES["module_not_licensed"]
    )
    return text_tr.format(module_tr=name_tr), text_en.format(module_en=name_en)


def license_unavailable_texts() -> tuple[str, str]:
    """The ``license_unavailable`` texts (always the copies: the package may not load)."""
    return FALLBACK_MESSAGES["license_unavailable"]
