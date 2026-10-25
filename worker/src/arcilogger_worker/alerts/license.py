"""``R20-license`` — the licence alerts of ``license.check`` (every 5 minutes; contract 7).

No licence state locks a 5651 function; these alerts tell an administrator, before and when it
happens, that the paid modules stop, and when a licence limit or the maintenance needs looking
at. They are decided from the status ``license.check`` has just computed — on the trusted time
(contract 3.3: the trial's, the licence's and the maintenance's days left come from it), never on
the worker's clock — and they never fail that job (:mod:`arcilogger_worker.licensing`). This
module imports no licence code at module level.

=================================  ============================================================
Key                                Condition (severity)
=================================  ============================================================
``R20-license:trial-ending``       the trial ends within :data:`TRIAL_ENDING_DAYS` (3) days
                                   (medium)
``R20-license:expiring``           a time-limited licence (``valid_until``) ends within
                                   :data:`EXPIRING_DAYS` (14) days and paid modules would go off
                                   with it (medium)
``R20-license:off``                paid modules off because of the state: ``trial_expired``,
                                   ``expired``, ``version_not_covered`` (medium), ``invalid``,
                                   ``wrong_server`` (high, the web's red banner); only while a
                                   module the trial or the licence gave is off
``R20-license-limit:<limit>``      ``users`` or ``log_sources`` above the licence's limit, from
                                   the crossing's ``limit`` licence event on (medium)
``R20-license-maintenance``        licensed, and the maintenance ends within 30 days or has
                                   ended (info: upgrades and support only, the licence stays
                                   valid)
=================================  ============================================================

Each condition has one active alert, resolved by itself once the condition is gone (reason
``licensed``, ``license_changed``, ``limit_ok`` or ``maintenance_renewed``). A licence situation
can last for months — a site that runs 5651 Temel only stays ``trial_expired`` —, so an alert
belongs to one **occurrence** of its condition (``details.occurrence``: the licence event of the
state or of the limit crossing, the trial's end, the licence's date): an alert a person closed is
not raised again for the same occurrence, and a new occurrence resolves the active alert and
opens its own.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.fmt import format_date, format_datetime
from arcilogger_core.timeutil import isoformat_site
from arcilogger_db.models import Alert, LicenseEvent
from arcilogger_worker.alerts.store import AUTO_RESOLVED, CLOSED_STATES, active_alert, resolve
from arcilogger_worker.alerts.system import Finding, Outcome, apply
from arcilogger_worker.alerts.texts import LICENSE_OFF

if TYPE_CHECKING:
    from arcilogger_core.license.state import LicenseStatus

RULE: Final = "R20-license"
LIMIT_RULE: Final = "R20-license-limit"
MAINTENANCE_RULE: Final = "R20-license-maintenance"
#: ``R20-license:trial-ending`` from this many days before the end of the trial.
TRIAL_ENDING_DAYS: Final = 3
#: ``R20-license:expiring`` from this many days before the end of ``valid_until``.
EXPIRING_DAYS: Final = 14
#: The states that switch the paid modules off (``R20-license:off``) …
OFF_STATES: Final = frozenset(
    {"trial_expired", "invalid", "wrong_server", "expired", "version_not_covered"}
)
#: … and those of them that mean something is wrong (``high``; the others are foreseen).
HIGH_STATES: Final = frozenset({"invalid", "wrong_server"})
#: ``R20-license-limit`` titles per limit (Turkish, English).
LIMIT_TITLES: Final = {
    "users": ("Kullanıcı sınırı aşıldı", "Users limit exceeded"),
    "log_sources": ("Log kaynağı sınırı aşıldı", "Log sources limit exceeded"),
}
#: ``details`` key of the occurrence an alert belongs to (see the module text).
OCCURRENCE: Final = "occurrence"


def _newest_event(session: Session, kind: str, limit: str | None = None) -> LicenseEvent | None:
    """The newest licence event of ``kind`` (of one ``limit``), as ``license.check`` wrote it."""
    query = select(LicenseEvent).where(LicenseEvent.kind == kind)
    if limit is not None:
        query = query.where(LicenseEvent.details["limit"].astext == limit)
    return session.scalars(query.order_by(LicenseEvent.id.desc()).limit(1)).first()


def _event(event: LicenseEvent) -> str:
    """An occurrence that is a licence event (its id: two can share a time)."""
    return f"license_events:{event.id}"


def _ending(status: LicenseStatus, paid: Sequence[str]) -> list[Finding]:
    """``R20-license``: the trial or a time-limited licence ends soon, and paid modules with it."""
    found = []
    on = [key for key in paid if status.module_enabled(key)]
    if status.state == "trial" and on and status.trial_days_left <= TRIAL_ENDING_DAYS:
        days, ends = status.trial_days_left, isoformat_site(status.trial_ends_at, "seconds")
        found.append(
            Finding(
                key=f"{RULE}:trial-ending",
                rule=RULE,
                severity="medium",
                title=(
                    f"Deneme süresi bitiyor: {days} gün kaldı "
                    f"({format_datetime(status.trial_ends_at)})"
                ),
                details={
                    "case": "trial_ending",
                    "days_left": days,
                    "until": ends,
                    "modules": on,
                    OCCURRENCE: ends,
                    "title_en": f"Trial ending: {days} day(s) left ({ends})",
                },
            )
        )
    left = status.valid_days_left
    if (
        status.state == "licensed"
        and on
        and status.valid_until is not None
        and left is not None
        and 0 <= left <= EXPIRING_DAYS
    ):
        until = status.valid_until.isoformat()
        found.append(
            Finding(
                key=f"{RULE}:expiring",
                rule=RULE,
                severity="medium",
                title=(
                    f"Lisansın süresi bitiyor: {format_date(status.valid_until)} ({left} gün kaldı)"
                ),
                details={
                    "case": "expiring",
                    "days_left": left,
                    "until": until,
                    "modules": on,
                    OCCURRENCE: until,
                    "title_en": f"Licence ending: {until} ({left} day(s) left)",
                },
            )
        )
    return found


def _off(session: Session, status: LicenseStatus, paid: Sequence[str]) -> list[Finding]:
    """``R20-license``: paid modules off because of the licence state."""
    if status.state not in OFF_STATES:
        return []
    # Without a verified licence every paid module of the trial is lost; with one, those it lists
    # (a licence of 5651 Temel only loses nothing: no alert).
    lost = [
        key
        for key in paid
        if not status.module_enabled(key)
        and (status.document is None or status.module_licensed(key))
    ]
    if not lost:
        return []
    case = (
        "trial_clock_inconsistent" if status.problem == "trial_clock_inconsistent" else status.state
    )
    day = {"expired": status.valid_until, "version_not_covered": status.maintenance_until}.get(
        status.state
    )
    head_tr, head_en = LICENSE_OFF[case]
    shown = format_date(day) if day is not None else ""
    until = day.isoformat() if day is not None else None
    event = _newest_event(session, "state")
    return [
        Finding(
            key=f"{RULE}:off",
            rule=RULE,
            severity="high" if status.state in HIGH_STATES else "medium",
            title=f"{head_tr.format(until=shown)}: ücretli modüller kapalı",
            details={
                "case": case,
                "state": status.state,
                "problem": status.problem,
                "modules": lost,
                "until": until,
                # The state's licence event (license.check has just written it when it changed).
                OCCURRENCE: _event(event) if event is not None else status.state,
                "title_en": f"{head_en.format(until=until or '')}: paid modules are off",
            },
        )
    ]


def _limits(session: Session, status: LicenseStatus) -> list[Finding]:
    """``R20-license-limit``: users or log sources above the licence's limit."""
    found = []
    for name, (title_tr, title_en) in LIMIT_TITLES.items():
        use = status.limits[name]
        if not use.exceeded:
            continue
        # From the crossing's licence event on (after a state change it is written one run later).
        event = _newest_event(session, "limit", name)
        if event is None or event.details.get("exceeded") is not True:
            continue
        shown = f"{use.used}/{use.max}"
        details: dict[str, Any] = {"limit": name, "used": use.used, "max": use.max}
        if name == "log_sources":
            details["unconfigured"] = use.unconfigured
        found.append(
            Finding(
                key=f"{LIMIT_RULE}:{name}",
                rule=LIMIT_RULE,
                severity="medium",
                title=f"{title_tr}: {shown}",
                details={
                    **details,
                    OCCURRENCE: _event(event),
                    "title_en": f"{title_en}: {shown}",
                },
            )
        )
    return found


def _maintenance(status: LicenseStatus) -> list[Finding]:
    """``R20-license-maintenance``: a valid licence whose maintenance ends soon or has ended."""
    days = status.maintenance_days_left
    if (
        status.state != "licensed"
        or status.maintenance_until is None
        or days is None
        or not (status.maintenance_ends_soon or status.maintenance_expired)
    ):
        return []
    until, shown = status.maintenance_until.isoformat(), format_date(status.maintenance_until)
    title_tr, title_en = (
        (f"Bakım süresi doldu: {shown}", f"Maintenance ended: {until}")
        if status.maintenance_expired
        else (
            f"Bakım süresi bitiyor: {shown} ({days} gün kaldı)",
            f"Maintenance ending: {until} ({days} day(s) left)",
        )
    )
    return [
        Finding(
            key=MAINTENANCE_RULE,
            rule=MAINTENANCE_RULE,
            severity="info",
            title=title_tr,
            details={"days_left": days, "until": until, OCCURRENCE: until, "title_en": title_en},
        )
    ]


def _owned(key: str) -> bool:
    return key.startswith((f"{RULE}:", f"{LIMIT_RULE}:")) or key == MAINTENANCE_RULE


def _closed_by_person(session: Session, key: str, occurrence: Any) -> bool:
    """The newest alert with ``key`` is one a person closed (resolved without
    ``details.auto_resolved``, or dismissed) for this ``occurrence``."""
    newest = session.scalars(
        select(Alert).where(Alert.dedup_key == key).order_by(Alert.id.desc()).limit(1)
    ).first()
    if newest is None or newest.state not in CLOSED_STATES:
        return False
    details = newest.details or {}
    return AUTO_RESOLVED not in details and details.get(OCCURRENCE) == occurrence


def check_license(session: Session, status: LicenseStatus, *, now: datetime) -> dict[str, Any]:
    """Open and resolve the licence alerts for ``status`` (the caller commits)."""
    from arcilogger_core.license import MODULES  # license.check has loaded the licence code

    paid = [item.key for item in MODULES if not item.always]
    findings = [
        *_ending(status, paid),
        *_off(session, status, paid),
        *_limits(session, status),
        *_maintenance(status),
    ]
    current = {finding.key for finding in findings}
    raised: list[Finding] = []
    replaced: list[str] = []
    for finding in findings:
        occurrence = finding.details[OCCURRENCE]
        existing = active_alert(session, finding.key)
        if existing is None:
            if _closed_by_person(session, finding.key, occurrence):
                continue  # not again for this occurrence
        elif (existing.details or {}).get(OCCURRENCE) != occurrence:
            resolve(existing, reason="license_changed", now=now)  # the new one gets its own
            replaced.append(finding.key)
        raised.append(finding)
    licensed = status.state == "licensed"

    def clear(alert: Alert) -> str | None:
        key = alert.dedup_key or ""
        if key in current:
            return None
        if key.startswith(f"{LIMIT_RULE}:"):
            return "limit_ok"
        if key == MAINTENANCE_RULE:
            return "maintenance_renewed" if licensed else "license_changed"
        return "licensed" if licensed else "license_changed"

    result = apply(session, Outcome(raised, _owned, clear), now=now)
    # Found: also a condition whose alert a person closed; resolved: also the replaced ones.
    return {**result, "found": sorted(current), "resolved": [*replaced, *result["resolved"]]}
