"""The licence state: THE decision (licensing contract section 3).

:func:`evaluate` turns the inputs (the stored licence bytes, this server's id, the trusted time,
the installation's trial start and its lower bounds, the usage counts) into one
:class:`LicenseStatus`. :meth:`LicenseStatus.module_enabled` is the one answer to "is module X
enabled"; nothing else compares module lists or dates.

Whatever the state, ``core_5651`` (5651 Temel) is enabled: an expired, missing, invalid or
other-server licence locks only the paid modules.
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from types import MappingProxyType
from typing import Any, Final, Literal

import arcilogger_core
from arcilogger_core.license._constants import PRODUCT_EPOCH, TRIAL_DAYS
from arcilogger_core.license.document import LicenseDocument, load_license
from arcilogger_core.license.errors import LicenseError
from arcilogger_core.license.modules import CORE_MODULE, MODULES, module_keys
from arcilogger_core.license.request import format_utc_seconds
from arcilogger_core.license.trusted_time import TrustedTime, site_day_end
from arcilogger_core.timeutil import site_day, to_utc

__all__ = [
    "PRODUCT_EPOCH",
    "SOURCE_ACTIVE_DAYS",
    "STATUS_FILE_SCHEMA",
    "TRIAL_CLOCK_TOLERANCE",
    "TRIAL_DAYS",
    "UNLICENSED_USERS",
    "Installation",
    "LicenseInputs",
    "LicenseStateName",
    "LicenseStatus",
    "LimitUse",
    "check_applicable",
    "evaluate",
    "status_override",
]

LicenseStateName = Literal[
    "trial",
    "trial_expired",
    "licensed",
    "invalid",
    "wrong_server",
    "expired",
    "version_not_covered",
]

#: Users limit without a usable licence document (``trial_expired``, ``invalid``) [C-11].
UNLICENSED_USERS: Final = 3
#: A log source counts while configured, or for 30 days after its last log (3.1, [C-9]).
SOURCE_ACTIVE_DAYS: Final = 30
#: A trial start (or a database trace) further than this after the trusted time is a clock
#: inconsistency (3.4, [C-7]).
TRIAL_CLOCK_TOLERANCE: Final = timedelta(minutes=10)
#: ``maintenance_ends_soon`` from this many days before the end.
MAINTENANCE_SOON_DAYS: Final = 30
STATUS_FILE_SCHEMA: Final = "arcilogger.license-status/1"

#: The trial lower bounds of 3.4: day names (clipped to the trusted time) and exact times
#: (checked against it).
DAY_BOUNDS: Final = frozenset({"vault", "spool"})
EXACT_BOUNDS: Final = frozenset({"audit", "trial_start_audit", "license_events"})
LOWER_BOUND_NAMES: Final = DAY_BOUNDS | EXACT_BOUNDS

_REGISTRY: Final = frozenset(module_keys())
_ALWAYS: Final = frozenset(item.key for item in MODULES if item.always)
_STATE_OF_PROBLEM: Final[Mapping[str, LicenseStateName]] = {
    "license_server_id_unavailable": "wrong_server",
    "license_other_server": "wrong_server",
    "license_expired": "expired",
    "license_version_not_covered": "version_not_covered",
}
_DAY: Final = timedelta(days=1)


@dataclass(frozen=True)
class LimitUse:
    max: int | None
    used: int
    unconfigured: int = 0  # log sources only: counted senders with unknown = true

    @property
    def exceeded(self) -> bool:
        return self.max is not None and self.used > self.max

    @property
    def full(self) -> bool:
        return self.max is not None and self.used >= self.max


@dataclass(frozen=True)
class Installation:
    installation_id: str
    trial_started_at: datetime


@dataclass(frozen=True)
class LicenseInputs:
    installation: Installation
    stored: bytes | None
    server_id: str | None
    server_id_error: str | None
    trusted: TrustedTime  # from trusted_now; evaluate adds the licence floor
    lower_bounds: Mapping[str, datetime]  # names of 3.4: vault, spool, audit, ...
    users_used: int
    log_sources_used: int
    log_sources_unconfigured: int
    version: str
    release_date: date


@dataclass(frozen=True)
class LicenseStatus:
    state: LicenseStateName
    problem: str | None
    document: LicenseDocument | None  # set when the signature verified
    enabled: frozenset[str]
    installation_id: str
    trial_started_at: datetime  # effective (3.4)
    trial_inconsistent: bool  # the clock check of 3.4 failed
    trial_ends_at: datetime
    trial_days_left: int
    trusted: TrustedTime  # incl. the licence floor
    server_id: str | None
    server_id_error: str | None
    limits: Mapping[str, LimitUse]  # keys "log_sources", "users"; max per the table of 3.2
    maintenance_until: date | None
    maintenance_days_left: int | None
    maintenance_expired: bool
    maintenance_ends_soon: bool
    valid_until: date | None
    valid_days_left: int | None  # site days to valid_until (0: its last day); None: no limit
    version: str
    release_date: date

    def module_enabled(self, key: str) -> bool:
        """THE answer to "is module X enabled". ValueError for a key not in MODULES."""
        if key not in _REGISTRY:
            raise ValueError(f"unknown module key {key!r}")
        return key in self.enabled

    def module_licensed(self, key: str) -> bool:
        """Whether the verified licence document lists ``key`` (false without a document)."""
        if key not in _REGISTRY:
            raise ValueError(f"unknown module key {key!r}")
        return self.document is not None and key in self.document.payload.modules

    def to_status_file(self, *, written_at: datetime) -> dict[str, Any]:
        """The installer's status file ``state/license-status.json`` (section 4.4)."""
        payload = self.document.payload if self.document is not None else None
        return {
            "schema": STATUS_FILE_SCHEMA,
            "written_at": format_utc_seconds(written_at),
            "status": self.state,
            "license_id": payload.license_id if payload else None,
            "licensee": payload.licensee if payload else None,
            "server_id": self.server_id,
            "maintenance_until": payload.maintenance_until.isoformat() if payload else None,
            "valid_until": (
                payload.valid_until.isoformat() if payload and payload.valid_until else None
            ),
            "modules": [key for key in module_keys() if key in self.enabled],
            "version": self.version,
            "release_date": self.release_date.isoformat(),
        }


def _passed(day: date, now: datetime) -> bool:
    """``now`` is at or after the end of ``day`` (inclusive dates, site time)."""
    return now >= site_day_end(day)


def check_applicable(
    document: LicenseDocument,
    *,
    server_id: str | None,
    server_id_error: str | None,
    trusted: TrustedTime,
    release_date: date,
    version: str | None = None,
) -> None:
    """Upload/CLI check after load_license: raises LicenseError license_server_id_unavailable,
    license_other_server, license_expired, license_version_not_covered (that order).

    The licence's ``issued_at`` raises the trusted time (the vendor's clock signed it).
    ``version`` defaults to this version (``arcilogger_core.__version__``).
    """
    payload = document.payload
    license_id, key_id = payload.license_id, document.key_id  # for refusal records
    if server_id is None:
        raise LicenseError(
            "license_server_id_unavailable",
            reason=server_id_error,
            license_id=license_id,
            key_id=key_id,
        )
    if server_id not in payload.server_ids:
        raise LicenseError(
            "license_other_server",
            {"server_id": server_id},
            details={"server_id": server_id, "allowed": list(payload.server_ids)},
            license_id=license_id,
            key_id=key_id,
        )
    now = trusted.with_floor(payload.issued_at, "license_issued").now
    if payload.valid_until is not None and _passed(payload.valid_until, now):
        raise LicenseError(
            "license_expired",
            {"date": payload.valid_until},
            details={"valid_until": payload.valid_until.isoformat()},
            license_id=license_id,
            key_id=key_id,
        )
    if release_date > payload.maintenance_until:
        shown = version if version is not None else arcilogger_core.__version__
        raise LicenseError(
            "license_version_not_covered",
            {"version": shown, "release_date": release_date, "until": payload.maintenance_until},
            details={
                "version": shown,
                "release_date": release_date.isoformat(),
                "maintenance_until": payload.maintenance_until.isoformat(),
            },
            license_id=license_id,
            key_id=key_id,
        )


def _licensed_modules(listed: Iterable[str]) -> frozenset[str]:
    """``core_5651`` plus every listed registry key whose ``requires`` are enabled too."""
    wanted = set(listed)
    enabled = set(_ALWAYS) | {CORE_MODULE}
    changed = True
    while changed:
        changed = False
        for item in MODULES:
            if item.key in enabled or item.key not in wanted:
                continue
            if all(required in enabled for required in item.requires):
                enabled.add(item.key)
                changed = True
    return frozenset(enabled)


@dataclass(frozen=True)
class _Trial:
    started_at: datetime
    ends_at: datetime
    days_left: int
    inconsistent: bool


def _ceil_days(delta: timedelta) -> int:
    return -((-delta) // _DAY)


def _trial(inputs: LicenseInputs, now: datetime) -> _Trial:
    row = to_utc(inputs.installation.trial_started_at)
    bounds: dict[str, datetime] = {}
    for name, value in inputs.lower_bounds.items():
        if name not in LOWER_BOUND_NAMES:
            raise ValueError(f"unknown trial lower bound {name!r}")
        moment = to_utc(value)
        if moment < PRODUCT_EPOCH:
            continue
        bounds[name] = min(moment, now) if name in DAY_BOUNDS else moment
    limit = now + TRIAL_CLOCK_TOLERANCE
    inconsistent = row > limit or any(
        bounds[name] > limit for name in EXACT_BOUNDS if name in bounds
    )
    started = min([row, *bounds.values()])
    ends = started + timedelta(days=TRIAL_DAYS)
    days_left = 0 if inconsistent or now >= ends else min(TRIAL_DAYS, _ceil_days(ends - now))
    return _Trial(started_at=started, ends_at=ends, days_left=days_left, inconsistent=inconsistent)


def evaluate(inputs: LicenseInputs) -> LicenseStatus:
    """THE decision (section 3): state, problem, enabled modules, limits and flags."""
    trusted = inputs.trusted
    document: LicenseDocument | None = None
    problem: str | None = None
    state: LicenseStateName = "trial_expired"
    if inputs.stored is not None:
        try:
            document = load_license(inputs.stored)
        except LicenseError as exc:
            state, problem = "invalid", exc.code
        else:
            trusted = trusted.with_floor(document.payload.issued_at, "license_issued")
            try:
                check_applicable(
                    document,
                    server_id=inputs.server_id,
                    server_id_error=inputs.server_id_error,
                    trusted=trusted,
                    release_date=inputs.release_date,
                    version=inputs.version,
                )
            except LicenseError as exc:
                state, problem = _STATE_OF_PROBLEM[exc.code], exc.code
            else:
                state = "licensed"

    now = trusted.now
    trial = _trial(inputs, now)
    if inputs.stored is None:
        if trial.inconsistent:
            state, problem = "trial_expired", "trial_clock_inconsistent"
        elif now < trial.ends_at:
            state = "trial"

    if state == "trial":
        enabled = frozenset(_REGISTRY)
    elif state == "licensed" and document is not None:
        enabled = _licensed_modules(document.payload.modules)
    else:
        enabled = frozenset(_ALWAYS | {CORE_MODULE})

    maintenance_until: date | None = None
    maintenance_days_left: int | None = None
    maintenance_expired = False
    maintenance_ends_soon = False
    valid_until: date | None = None
    valid_days_left: int | None = None
    users_max: int | None
    sources_max: int | None
    if document is not None:
        payload = document.payload
        maintenance_until = payload.maintenance_until
        maintenance_days_left = (maintenance_until - site_day(now)).days
        maintenance_expired = _passed(maintenance_until, now)
        maintenance_ends_soon = 0 <= maintenance_days_left <= MAINTENANCE_SOON_DAYS
        valid_until = payload.valid_until
        if valid_until is not None:
            valid_days_left = (valid_until - site_day(now)).days
        users_max, sources_max = payload.users, payload.log_sources
    elif state == "trial":
        users_max = sources_max = None
    else:
        users_max, sources_max = UNLICENSED_USERS, None

    return LicenseStatus(
        state=state,
        problem=problem,
        document=document,
        enabled=enabled,
        installation_id=inputs.installation.installation_id,
        trial_started_at=trial.started_at,
        trial_inconsistent=trial.inconsistent,
        trial_ends_at=trial.ends_at,
        trial_days_left=trial.days_left,
        trusted=trusted,
        server_id=inputs.server_id,
        server_id_error=inputs.server_id_error,
        limits=MappingProxyType(
            {
                "log_sources": LimitUse(
                    sources_max, inputs.log_sources_used, inputs.log_sources_unconfigured
                ),
                "users": LimitUse(users_max, inputs.users_used),
            }
        ),
        maintenance_until=maintenance_until,
        maintenance_days_left=maintenance_days_left,
        maintenance_expired=maintenance_expired,
        maintenance_ends_soon=maintenance_ends_soon,
        valid_until=valid_until,
        valid_days_left=valid_days_left,
        version=inputs.version,
        release_date=inputs.release_date,
    )


def status_override() -> LicenseStatus | None:
    """The status set by testing.override_status. Returns None unless
    sys.modules.get("arcilogger_core.license.testing") is loaded (it is not shipped, 9.2)."""
    testing = sys.modules.get("arcilogger_core.license.testing")
    if testing is None:
        return None
    value = getattr(testing, "_override", None)
    return value if isinstance(value, LicenseStatus) else None
