"""The database layer of the offline licensing (licensing contract 4 and 5.4).

:func:`load_status` is the one entry point of the API, the worker and ``arcilogger-db
license``: it gathers the inputs (the active licence file, this server's id, the trusted time,
the installation's trial start and its lower bounds, the usage counts) and lets
:func:`arcilogger_core.license.state.evaluate` decide. Nothing here compares module lists,
dates or signatures: the core package decides, this module stores and serialises.

**Containment** (contract 5.2): only this module and :mod:`arcilogger_core.license` import the
licence code at module level. Shared paths (the API's routers, the worker's scheduler, the
seed, ``arcilogger-db`` with its Backup task and ``upgrade``) reach it only through
:func:`arcilogger_db.license_guard.safe_status`, so a broken licence subsystem locks only the
paid modules and never a 5651 obligation.

**Storage** (migration ``0014_license``, all three tables append-only):

* ``license_installation`` row 1: the installation id and the trial start
  (:func:`ensure_installation`, backdated to the oldest database trace when one exists);
* ``license_files``: each installed file's exact bytes (``content``); the other columns are an
  index only — every licence field that is shown, audited or decided on comes from ``content``,
  verified again (:func:`arcilogger_core.license.document.load_license`) at that moment;
* ``license_events``: ``install``/``remove`` (the newest decides the active licence),
  ``refuse``, ``limit`` and ``state``.

Times are the Python clock (``utc_now``, or the caller's ``clock``), never the database's
``now()``.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Final, Literal
from uuid import uuid4

from sqlalchemy import func, or_, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

import arcilogger_core
from arcilogger_core.config import Config
from arcilogger_core.fsutil import atomic_write_text
from arcilogger_core.layout import Layout
from arcilogger_core.license import (
    LICENSOR_EMAIL,
    LICENSOR_NAME,
    MODULES,
    PRODUCT_EPOCH,
    SOURCE_ACTIVE_DAYS,
    FingerprintError,
    Installation,
    LicenseDocument,
    LicenseError,
    LicenseInputs,
    LicenseStatus,
    check_applicable,
    current_identity,
    current_server_id,
    evaluate,
    file_lower_bounds,
    load_license,
    make_request_code,
    module_keys,
    new_request,
    status_override,
)
from arcilogger_core.license import (
    release_date as current_release_date,
)
from arcilogger_core.license import trusted_time as _trusted_time
from arcilogger_core.timeutil import isoformat_site, to_utc, utc_now
from arcilogger_db.audit import SYSTEM_ACTOR, append_audit, jsonable
from arcilogger_db.models import (
    AuditLog,
    LicenseEvent,
    LicenseFile,
    LicenseInstallation,
    Source,
    User,
)

__all__ = [
    "HISTORY_LIMIT",
    "STATUS_FILE",
    "AuditWriter",
    "active_license",
    "check_and_record",
    "db_lower_bounds",
    "ensure_installation",
    "install_license",
    "load_status",
    "record_refusal",
    "remove_license",
    "request_info",
    "status_json",
    "usage_counts",
    "verify_license_file",
    "write_status_file",
]

#: ``(action, target, details)``: writes one audit entry in the caller's transaction (the API
#: passes ``record_audit`` with the signed-in user, the CLI and the worker ``append_audit``).
AuditWriter = Callable[[str, str | None, Mapping[str, Any]], None]
Clock = Callable[[], datetime]

#: The installer's status file under ``state/`` (contract 4.4; never trusted by the product).
STATUS_FILE: Final = "license-status.json"
#: ``GET /license`` admin details: the newest events, newest first.
HISTORY_LIMIT: Final = 20
#: ``license_installation`` has exactly this one row.
INSTALLATION_ROW: Final = 1
LIMIT_NAMES: Final = ("log_sources", "users")
#: Audit actions (contract 6.7).
TRIAL_START: Final = "license.trial_start"

#: Forwards :func:`load_status`'s own ``release_date`` (passed only by tests and the sample
#: capture, which the A-T18 scan checks at *their* ``load_status`` calls) to the trusted time.
_trusted_now_at_release = _trusted_time.trusted_now


# -- installation and lower bounds ----------------------------------------------------------------


def _system_writer(session: Session) -> AuditWriter:
    """Audit entries of the system (actor ``system``; reach the spool by the API's flush)."""

    def write(action: str, target: str | None, details: Mapping[str, Any]) -> None:
        append_audit(session, action=action, actor=SYSTEM_ACTOR, target=target, details=details)

    return write


def _installation_row(session: Session) -> LicenseInstallation | None:
    return session.scalars(
        select(LicenseInstallation)
        .where(LicenseInstallation.id == INSTALLATION_ROW)
        .execution_options(populate_existing=True)
    ).first()


def _aware(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return to_utc(value) if value.utcoffset() is not None else None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        return to_utc(parsed) if parsed.utcoffset() is not None else None
    return None


def db_lower_bounds(session: Session) -> dict[str, datetime]:
    """{"audit", "trial_start_audit", "license_events"} of 3.4 (missing keys when absent)."""
    bounds: dict[str, datetime] = {}
    audit = _aware(session.scalar(select(func.min(AuditLog.time))))
    if audit is not None:
        bounds["audit"] = audit
    starts = [
        moment
        for value in session.scalars(
            select(AuditLog.details["trial_started_at"].astext).where(
                AuditLog.action == TRIAL_START
            )
        )
        if (moment := _aware(value)) is not None
    ]
    if starts:
        bounds["trial_start_audit"] = min(starts)
    events = _aware(session.scalar(select(func.min(LicenseEvent.at))))
    if events is not None:
        bounds["license_events"] = events
    return bounds


def ensure_installation(
    session: Session, *, now: datetime, audit: AuditWriter | None = None
) -> Installation:
    """Row 1 of license_installation; creates it (backdated per 3.4) when missing.

    A new row's ``trial_started_at`` is ``now``, or the oldest database trace (audit log,
    ``license.trial_start`` entries, licence events) when one exists: a database whose row was
    deleted keeps its trial start. The creation is audited as ``license.trial_start`` (actor
    ``system`` unless ``audit`` is given).
    """
    row = _installation_row(session)
    if row is None:
        moment = to_utc(now)
        bounds = db_lower_bounds(session)
        recreated = "trial_start_audit" in bounds or "license_events" in bounds
        usable = [value for value in bounds.values() if value >= PRODUCT_EPOCH]
        started = min([moment, *usable])
        inserted = session.execute(
            insert(LicenseInstallation)
            .values(id=INSTALLATION_ROW, installation_id=uuid4(), trial_started_at=started)
            .on_conflict_do_nothing()
            .returning(LicenseInstallation.installation_id)
        ).scalar_one_or_none()
        if inserted is not None:
            writer = audit or _system_writer(session)
            writer(
                TRIAL_START,
                "license",
                {
                    "installation_id": str(inserted),
                    "trial_started_at": started,
                    "backdated": started < moment,
                    "recreated": recreated,
                },
            )
        row = _installation_row(session)
        if row is None:  # pragma: no cover - inserted above or by a concurrent writer
            raise RuntimeError("license_installation row 1 is missing")
    return Installation(
        installation_id=str(row.installation_id), trial_started_at=to_utc(row.trial_started_at)
    )


# -- the active licence ---------------------------------------------------------------------------


def _active(session: Session) -> tuple[LicenseEvent, LicenseFile] | None:
    """The newest ``install``/``remove`` event and its file, when it is ``install``."""
    event = session.scalars(
        select(LicenseEvent)
        .where(LicenseEvent.kind.in_(("install", "remove")))
        .order_by(LicenseEvent.id.desc())
        .limit(1)
    ).first()
    if event is None or event.kind != "install" or event.license_file_id is None:
        return None
    file = session.get(LicenseFile, event.license_file_id)
    return None if file is None else (event, file)


def active_license(session: Session) -> LicenseFile | None:
    """The active licence file (contract 4.1), or ``None``."""
    found = _active(session)
    return None if found is None else found[1]


@dataclass(frozen=True)
class _Readable:
    """What a stored licence file says about itself, read again from ``content``."""

    document: LicenseDocument | None
    license_id: str | None
    key_id: str | None


def _read(content: bytes) -> _Readable:
    try:
        document = load_license(bytes(content))
    except LicenseError as exc:
        return _Readable(None, exc.license_id, exc.key_id)
    return _Readable(document, document.payload.license_id, document.key_id)


# -- counts ---------------------------------------------------------------------------------------


def usage_counts(session: Session, *, config: Config | None, now: datetime) -> tuple[int, int, int]:
    """(users_used, log_sources_used, log_sources_unconfigured) as defined in 3.1.

    A log source counts while it is a configured device (an exact ``[[sources]]`` entry), or
    for :data:`~arcilogger_core.license.SOURCE_ACTIVE_DAYS` days after its last log; ``unknown``
    and ``enabled`` are not filters (an unconfigured sender is collected like any other).
    """
    users = session.scalar(select(func.count()).select_from(User).where(User.active)) or 0
    configured = sorted(
        {source.name for source in (config.sources if config else ()) if not source.is_network}
    )
    since = to_utc(now) - timedelta(days=SOURCE_ACTIVE_DAYS)
    recent = Source.last_seen >= since
    counted = or_(Source.name.in_(configured), recent) if configured else recent
    total, unconfigured = session.execute(
        select(func.count(), func.count().filter(Source.unknown.is_(True)))
        .select_from(Source)
        .where(counted)
    ).one()
    return int(users), int(total or 0), int(unconfigured or 0)


def _server_id() -> tuple[str | None, str | None]:
    try:
        return current_server_id(), None
    except FingerprintError as exc:
        return None, str(exc) or type(exc).__name__


# -- the status -----------------------------------------------------------------------------------


def load_status(
    session: Session,
    *,
    config: Config | None,
    layout: Layout,
    clock: Clock = utc_now,
    release_date: date | None = None,
    version: str | None = None,
) -> LicenseStatus:
    """The entry point of API, worker and CLI. Returns status_override() when a test set one.
    release_date/version: only tests and the sample capture pass them (A-T18 scan).

    Never raises for vault, fingerprint or token problems (they become inputs); it may raise
    for database errors (shared paths call it only through ``license_guard.safe_status``).
    """
    override = status_override()
    if override is not None:
        return override
    trusted = _trusted_now_at_release(layout, config, clock=clock, release_date=release_date)
    installation = ensure_installation(session, now=trusted.system)
    found = _active(session)
    users, sources, unconfigured = usage_counts(session, config=config, now=trusted.now)
    bounds = {**file_lower_bounds(layout), **db_lower_bounds(session)}
    server_id, server_id_error = _server_id()
    return evaluate(
        LicenseInputs(
            installation=installation,
            stored=bytes(found[1].content) if found is not None else None,
            server_id=server_id,
            server_id_error=server_id_error,
            trusted=trusted,
            lower_bounds=bounds,
            users_used=users,
            log_sources_used=sources,
            log_sources_unconfigured=unconfigured,
            version=version if version is not None else arcilogger_core.__version__,
            release_date=release_date if release_date is not None else current_release_date(),
        )
    )


# -- install, verify, refuse, remove --------------------------------------------------------------


def verify_license_file(
    session: Session,
    data: bytes,
    *,
    config: Config | None,
    layout: Layout,
    clock: Clock = utc_now,
) -> LicenseDocument:
    """The checks of install_license without writing (CLI `license verify`).

    Steps 1-6 of 2.1 (:func:`load_license`), then this server: server id, ``valid_until`` and
    the maintenance period against this version (:func:`check_applicable`). Raises
    :class:`LicenseError`.
    """
    document = load_license(data)
    server_id, server_id_error = _server_id()
    check_applicable(
        document,
        server_id=server_id,
        server_id_error=server_id_error,
        trusted=_trusted_time.trusted_now(layout, config, clock=clock),
        release_date=current_release_date(),
    )
    return document


def _install_details(document: LicenseDocument) -> dict[str, Any]:
    payload = document.payload
    return {
        "license_id": payload.license_id,
        "sha256": document.sha256,
        "payload_sha256": document.payload_sha256,
        "key_id": document.key_id,
        "licensee": payload.licensee,
        "modules": list(payload.modules),
        "limits": {"log_sources": payload.log_sources, "users": payload.users},
        "maintenance_until": payload.maintenance_until.isoformat(),
        "valid_until": payload.valid_until.isoformat() if payload.valid_until else None,
        "server_ids": list(payload.server_ids),
    }


#: Fields of the ``install`` event that never go into the sealed audit log (contract 1.7, 6.7).
_EVENT_ONLY: Final = frozenset({"licensee", "server_ids"})


def _file_row(session: Session, document: LicenseDocument, *, actor: str, at: datetime) -> int:
    """The ``license_files`` row of these exact bytes (inserted once, never changed)."""
    payload = document.payload
    inserted = session.execute(
        insert(LicenseFile)
        .values(
            sha256=document.sha256,
            payload_sha256=document.payload_sha256,
            content=document.raw,
            license_id=payload.license_id,
            key_id=document.key_id,
            licensee=payload.licensee,
            issued_at=payload.issued_at,
            first_installed_at=at,
            first_installed_by=actor,
        )
        .on_conflict_do_nothing(index_elements=["sha256"])
        .returning(LicenseFile.id)
    ).scalar_one_or_none()
    if inserted is not None:
        return int(inserted)
    return int(
        session.execute(
            select(LicenseFile.id).where(LicenseFile.sha256 == document.sha256)
        ).scalar_one()
    )


def install_license(
    session: Session,
    data: bytes,
    *,
    config: Config | None,
    layout: Layout,
    actor: str,
    source: Literal["web", "cli"],
    audit: AuditWriter,
    clock: Clock = utc_now,
) -> LicenseStatus:
    """Verify ``data`` (:func:`verify_license_file`), store it and make it the active licence.

    Order (contract 6.2): parse + signature → applicability → store (the bytes once) + event
    ``install`` + audit ``license.install``. Raises :class:`LicenseError` before writing
    anything; the caller records the refusal (:func:`record_refusal`) in its own transaction.
    The same licence again (same canonical payload, even reformatted) is an ``install`` with
    ``"unchanged": true``. Returns the new status.
    """
    document = verify_license_file(session, data, config=config, layout=layout, clock=clock)
    at = to_utc(clock())
    previous = _active(session)
    before = _read(previous[1].content) if previous is not None else None
    unchanged = (
        before is not None
        and before.document is not None
        and before.document.payload_sha256 == document.payload_sha256
    )
    file_id = _file_row(session, document, actor=actor, at=at)
    details = {
        **_install_details(document),
        "replaced_license_id": before.license_id if before is not None else None,
        "unchanged": unchanged,
    }
    session.add(
        LicenseEvent(
            at=at,
            kind="install",
            source=source,
            actor=actor,
            license_file_id=file_id,
            details=jsonable(details),
        )
    )
    session.flush()
    audit(
        "license.install",
        f"license:{document.payload.license_id}",
        {key: value for key, value in details.items() if key not in _EVENT_ONLY},
    )
    return load_status(session, config=config, layout=layout, clock=clock)


def record_refusal(
    session: Session,
    error: LicenseError,
    data: bytes | None,
    *,
    actor: str,
    source: Literal["web", "cli"],
    audit: AuditWriter,
    size: int | None = None,
    clock: Clock = utc_now,
) -> None:
    """Event ``refuse`` + audit ``license.refuse`` (in the caller's transaction).

    ``data`` ``None``: the body was refused before it was read (too large); ``size`` is then
    the declared ``Content-Length`` or the bytes read so far, and ``sha256`` is null.
    """
    details = {
        "code": error.code,
        "sha256": hashlib.sha256(data).hexdigest() if data is not None else None,
        "size": len(data) if data is not None else size,
        "license_id": error.license_id,
        "key_id": error.key_id,
    }
    session.add(
        LicenseEvent(at=to_utc(clock()), kind="refuse", source=source, actor=actor, details=details)
    )
    session.flush()
    audit("license.refuse", "license", details)


def remove_license(
    session: Session,
    *,
    actor: str,
    source: Literal["web", "cli"],
    audit: AuditWriter,
    clock: Clock = utc_now,
) -> LicenseFile:
    """Event ``remove`` + audit ``license.remove``; LicenseError license_not_installed."""
    found = _active(session)
    if found is None:
        raise LicenseError("license_not_installed")
    _, file = found
    content = bytes(file.content)
    license_id = _read(content).license_id
    details = {"license_id": license_id, "sha256": hashlib.sha256(content).hexdigest()}
    session.add(
        LicenseEvent(
            at=to_utc(clock()),
            kind="remove",
            source=source,
            actor=actor,
            license_file_id=file.id,
            details=details,
        )
    )
    session.flush()
    audit("license.remove", f"license:{license_id}" if license_id else "license", details)
    return file


# -- license.check: state changes and limit crossings ---------------------------------------------


def _newest(session: Session, kind: str, limit: str | None = None) -> LicenseEvent | None:
    query = select(LicenseEvent).where(LicenseEvent.kind == kind)
    if limit is not None:
        query = query.where(LicenseEvent.details["limit"].astext == limit)
    return session.scalars(query.order_by(LicenseEvent.id.desc()).limit(1)).first()


def _enabled_list(status: LicenseStatus) -> list[str]:
    return [key for key in module_keys() if status.module_enabled(key)]


def check_and_record(
    session: Session, status: LicenseStatus, *, audit: AuditWriter, clock: Clock = utc_now
) -> list[str]:
    """Worker: state change / limit crossings since the last events -> events + audit; returns
    kinds.
    Limit events are evaluated only for limits whose max is not None; a state change creates no
    limit event; each limit compares with the last `limit` event of the same limit.

    A state change is a different state, problem or set of enabled modules than the newest
    ``state`` event (the first run records the state with ``from`` null). A limit is recorded
    when it is exceeded without an earlier event, or when ``exceeded`` differs from that
    limit's newest event (``license.limit_exceeded`` / ``license.limit_ok``).
    """
    at = to_utc(clock())
    recorded: list[str] = []
    enabled = _enabled_list(status)
    last = _newest(session, "state")
    before: Mapping[str, Any] = last.details if last is not None else {}
    if (
        last is None
        or before.get("to") != status.state
        or before.get("problem") != status.problem
        or before.get("enabled_to") != enabled
    ):
        details = {
            "from": before.get("to"),
            "to": status.state,
            "enabled_from": before.get("enabled_to"),
            "enabled_to": enabled,
            "problem": status.problem,
        }
        session.add(
            LicenseEvent(at=at, kind="state", source="worker", actor=SYSTEM_ACTOR, details=details)
        )
        session.flush()
        audit("license.state", "license", details)
        recorded.append("state")
        return recorded
    for name in LIMIT_NAMES:
        use = status.limits[name]
        if use.max is None:
            continue
        previous = _newest(session, "limit", name)
        exceeded = use.exceeded
        if previous is None and not exceeded:
            continue
        if previous is not None and bool(previous.details.get("exceeded")) == exceeded:
            continue
        session.add(
            LicenseEvent(
                at=at,
                kind="limit",
                source="worker",
                actor=SYSTEM_ACTOR,
                details={"limit": name, "exceeded": exceeded, "max": use.max, "used": use.used},
            )
        )
        session.flush()
        audit(
            "license.limit_exceeded" if exceeded else "license.limit_ok",
            "license",
            {"limit": name, "max": use.max, "used": use.used},
        )
        recorded.append("limit")
    return recorded


# -- serialisation --------------------------------------------------------------------------------


def _site(value: datetime | None) -> str | None:
    return isoformat_site(value, "seconds") if value is not None else None


def request_info(status: LicenseStatus, config: Config | None) -> dict[str, Any]:
    """The request code of this installation (``request_code`` null without a server id)."""
    organization = config.site_name if config is not None else ""
    code = None
    if status.server_id is not None:
        try:
            identity = current_identity()
        except FingerprintError:
            identity = None
        if identity is not None:
            code = make_request_code(
                new_request(
                    identity,
                    installation_id=status.installation_id,
                    organization=organization,
                    now=status.trusted.system,
                    product_version=status.version,
                )
            )
    return {
        "request_code": code,
        "server_id": status.server_id,
        "installation_id": status.installation_id,
        "organization": organization,
        "product_version": status.version,
    }


def _limits(status: LicenseStatus) -> dict[str, Any]:
    return {
        name: {
            "max": use.max,
            "used": use.used,
            "unconfigured": use.unconfigured,
            "exceeded": use.exceeded,
            "full": use.full,
        }
        for name in LIMIT_NAMES
        for use in (status.limits[name],)
    }


def _history(session: Session) -> list[dict[str, Any]]:
    events = session.scalars(
        select(LicenseEvent).order_by(LicenseEvent.id.desc()).limit(HISTORY_LIMIT)
    )
    history = []
    for event in events:
        details = dict(event.details or {})
        license_id = (
            details.get("license_id") if event.kind in ("install", "remove", "refuse") else None
        )
        history.append(
            {
                "at": _site(event.at),
                "kind": event.kind,
                "source": event.source,
                "actor": event.actor,
                "license_id": license_id if isinstance(license_id, str) else None,
                "details": details,
            }
        )
    return history


def _details(
    status: LicenseStatus, session: Session | None, config: Config | None
) -> dict[str, Any]:
    document = status.document
    payload = document.payload if document is not None else None
    found = _active(session) if session is not None else None
    readable = (
        _Readable(document, payload.license_id, document.key_id)
        if document is not None and payload is not None
        else (_read(found[1].content) if found is not None else _Readable(None, None, None))
    )
    trusted = status.trusted
    request = request_info(status, config)
    return {
        "license_id": readable.license_id,
        "key_id": readable.key_id,
        "issued_at": _site(payload.issued_at) if payload is not None else None,
        "server_ids": list(payload.server_ids) if payload is not None else None,
        "installed_at": _site(found[0].at) if found is not None else None,
        "installed_by": found[0].actor if found is not None else None,
        "server_id": status.server_id,
        "server_id_error": status.server_id_error,
        "installation_id": status.installation_id,
        "organization": request["organization"],
        "request_code": request["request_code"],
        "time": {
            "now": _site(trusted.now),
            "source": trusted.source,
            "tsa_time": _site(trusted.tsa),
            "vault_day_end": _site(trusted.vault_day_end),
            "system_time": _site(trusted.system),
            "clock_behind": trusted.clock_behind,
        },
        "history": _history(session) if session is not None else [],
    }


def status_json(
    status: LicenseStatus,
    *,
    details: bool,
    session: Session | None = None,
    config: Config | None = None,
) -> dict[str, Any]:
    """The ``GET /license`` document (contract 6.1); ``details`` for ``settings.manage``.

    Every licence field comes from the verified document of ``status`` (the re-verified
    ``content``), never from the index columns of ``license_files``.
    """
    document = status.document
    payload = document.payload if document is not None else None
    maintenance = None
    if payload is not None and status.maintenance_until is not None:
        maintenance = {
            "until": status.maintenance_until.isoformat(),
            "days_left": status.maintenance_days_left,
            "expired": status.maintenance_expired,
            "ends_soon": status.maintenance_ends_soon,
            "covers_version": status.release_date <= status.maintenance_until,
        }
    trial = None
    if status.state in ("trial", "trial_expired"):
        trial = {
            "started_at": _site(status.trial_started_at),
            "ends_at": _site(status.trial_ends_at),
            "days_left": status.trial_days_left,
        }
    return {
        "status": status.state,
        "problem": status.problem,
        "modules": [
            {
                "key": item.key,
                "name_tr": item.name_tr,
                "name_en": item.name_en,
                "description_tr": item.description_tr,
                "description_en": item.description_en,
                "enabled": status.module_enabled(item.key),
                "always": item.always,
                "licensed": status.module_licensed(item.key),
            }
            for item in MODULES
        ],
        "trial": trial,
        "licensee": payload.licensee if payload is not None else None,
        "maintenance": maintenance,
        "valid_until": status.valid_until.isoformat() if status.valid_until else None,
        "limits": _limits(status),
        "product": {
            "name": arcilogger_core.PRODUCT_NAME,
            "version": status.version,
            "release_date": status.release_date.isoformat(),
        },
        "licensor": {"name": LICENSOR_NAME, "email": LICENSOR_EMAIL},
        "checked_at": _site(status.trusted.system),
        "details": _details(status, session, config) if details else None,
    }


def write_status_file(layout: Layout, status: LicenseStatus, *, clock: Clock = utc_now) -> None:
    """``state/license-status.json`` for the installer (contract 4.4), written atomically."""
    document = status.to_status_file(written_at=to_utc(clock()))
    atomic_write_text(
        layout.state / STATUS_FILE, json.dumps(document, ensure_ascii=False, indent=2) + "\n"
    )
