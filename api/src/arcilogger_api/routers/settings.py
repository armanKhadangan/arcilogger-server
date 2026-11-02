"""``/api/v1/settings`` — Ayarlar → Uygulama ayarları: the database settings (ADR-0014; contract
2.13).

Permission ``settings.manage``; nothing here is licence-gated. These are the ``settings`` table's
values (:data:`arcilogger_db.settings.DEFAULT_SETTINGS`), not the machine configuration: the API
and the worker read them whenever they use them, so a change needs no reload.

* ``GET /settings`` — every visible setting by group (:mod:`arcilogger_db.settings_meta`) with
  its value, shipped default, kind of value, bounds, whether a change needs a re-authentication
  (``REAUTH_KEYS``) or is console-only (``LOCKED_KEYS``), Turkish and English label and help, and
  who changed it when;
* ``PATCH /settings`` ``{"changes": [{"key", "old", "value"}]}`` — **all or nothing** in one
  transaction: every key must be known (422 ``setting_unknown``) and not locked (422
  ``setting_locked``), every value valid (422 ``setting_invalid`` with
  ``details[{key, code, message_tr, message_en}]``, never the submitted value), ``old`` must still
  be the value in force (409 ``setting_conflict``, ``details[{key, current}]``) and a key of
  ``REAUTH_KEYS`` needs a recent re-authentication (403 ``reauth_required``). Each change is
  written by :func:`arcilogger_db.settings_admin.change_setting` with the user's id and address
  (audit ``settings.change {old, new}``); a value equal to the one in force is left alone
  (``unchanged``);
* ``POST /settings/{key}/reset`` — the shipped default back (audited with ``reset: true``);
* ``GET /settings/fortigate/observed?hours=24`` (``hours`` 1 to 72; contract 2.12) — what the
  FortiGate's session records show, for the exit editor's observed panel: the NAT addresses
  (``transip``, ``dstintf``, ``trandisp``) of the trusted ``fg_traffic`` rows of the last
  ``hours`` with an IPv4 NAT address (exits refuse IPv6, as ``R20-nat:unmapped-transip`` reads
  the rows), the 100 busiest groups, each with the exit that claims it by the **current**
  ``nat.egresses`` (the one whose interfaces hold the row's ``dstintf``, else the first by name;
  ``exits_invalid`` and no claims while the stored value cannot be parsed, never a 500), and the
  srcmac rows of the last hour per ``srcintf`` (rows, MACs, the most IPv4 sources of one MAC, as
  the srcmac guard counts them; 50 interfaces). Counts and the firewall's own addresses only: not
  audited. The counts are kept 5 minutes per ``hours`` (:class:`ObservedState`); the claims are
  computed on every request, so an exit saved a moment ago claims its rows at once. The counting
  has a 5-s statement deadline, past it 504 ``fortigate_observed_timeout``.
"""

from __future__ import annotations

import ipaddress
import logging
import threading
from collections.abc import Collection
from datetime import datetime, timedelta
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy import Executable, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from arcilogger_api.deps import DbSession, TtlCache
from arcilogger_api.errors import ApiError
from arcilogger_api.routers.lookup import LookupTimeoutError, _is_timeout, statement_deadline
from arcilogger_api.security import CurrentUser, client_ip, ensure_recent_auth, require
from arcilogger_core.timeutil import to_site, utc_now
from arcilogger_db.egress import Egress, parse_egresses
from arcilogger_db.models import FgTraffic
from arcilogger_db.settings import (
    SETTING_ERRORS,
    SettingValueError,
    get_setting,
    validate_setting,
)
from arcilogger_db.settings_admin import change_setting, list_settings, reset_setting
from arcilogger_db.settings_meta import GROUPS, SETTINGS, group_settings
from arcilogger_worker.lookup import trust

router = APIRouter(prefix="/settings", tags=["settings"])
log = logging.getLogger("arcilogger.api.settings")

SettingsDep = Annotated[CurrentUser, Depends(require("settings.manage"))]
SettingKey = Annotated[str, Path(max_length=100, description="Setting key")]

#: The refusals of this router (contract 2.12), Turkish first.
MESSAGES = {
    "setting_invalid": (
        "Bazı ayar değerleri geçersiz; işaretli ayarları düzeltin.",
        "Some setting values are invalid; correct the marked settings.",
    ),
    "setting_unknown": SETTING_ERRORS["setting_unknown"],
    "setting_locked": (
        "Bu ayar web arayüzünden değiştirilemez (sunucudaki bir dosya yolu).",
        "This setting cannot be changed from the web interface (a file path on the server).",
    ),
    "setting_conflict": (
        "Ayar siz düzenlerken başka biri tarafından değiştirildi; sayfayı yenileyin.",
        "The setting was changed by someone else while you were editing; reload the page.",
    ),
    "fortigate_observed_timeout": (
        "FortiGate kayıtları {seconds} saniyede özetlenemedi; biraz sonra yeniden deneyin.",
        "The FortiGate records could not be summarised within {seconds} s; try again a little "
        "later.",
    ),
}

#: ``GET /settings/fortigate/observed``: the NAT groups and srcmac interfaces listed, how long
#: its counts are kept per ``hours`` and the statement deadline of the counting (contract 2.12).
OBSERVED_ROWS: Final = 100
OBSERVED_INTERFACES: Final = 50
OBSERVED_CACHE_SECONDS: Final = 300.0
OBSERVED_DEADLINE_SECONDS = 5
#: The srcmac part always covers the last hour (the window of the srcmac guard).
SRCMAC_HOURS: Final = 1


def _error(status: int, code: str, details: Any = None, **params: object) -> ApiError:
    text_tr, text_en = MESSAGES[code]
    return ApiError(
        status, code, text_tr.format(**params), text_en.format(**params), details=details
    )


class SettingOut(BaseModel):
    key: str
    value: JsonValue
    default: JsonValue
    is_default: bool
    #: ``boolean``, ``integer``, ``string``, ``string_list``, ``integer_list`` or ``object``.
    type: str
    #: Bounds of an integer setting (``INTEGER_RANGES``); None otherwise or without one.
    min: int | None
    max: int | None
    #: A change needs a recent re-authentication (``REAUTH_KEYS``).
    reauth: bool
    #: Changed on the server console only (``LOCKED_KEYS``: a path on the server).
    locked: bool
    label_tr: str
    label_en: str
    help_tr: str
    help_en: str
    updated_by: str | None
    updated_at: datetime | None


class SettingGroup(BaseModel):
    id: str
    label_tr: str
    label_en: str
    settings: list[SettingOut]


class SettingsOut(BaseModel):
    groups: list[SettingGroup]


class SettingChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(max_length=100)
    #: The value shown when the edit began (409 ``setting_conflict`` when it changed since).
    old: JsonValue
    value: JsonValue


class SettingsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    changes: list[SettingChange] = Field(min_length=1, max_length=200)


class ChangedSetting(BaseModel):
    key: str
    old: JsonValue
    new: JsonValue


class SettingsPatchResult(BaseModel):
    changed: list[ChangedSetting]
    unchanged: list[str]


class ObservedNatRow(BaseModel):
    """One ``(transip, dstintf, trandisp)`` of the trusted ``fg_traffic`` rows of the window."""

    transip: str
    dstintf: str | None
    trandisp: str | None
    sessions: int
    first_seen: datetime
    last_seen: datetime
    #: The exit that claims the address by the stored ``nat.egresses``; None when none does.
    egress: str | None
    #: ``trandisp`` is one of ``nat.snat_trandisp``.
    trandisp_known: bool


class ObservedNat(BaseModel):
    hours: int
    #: More than 100 groups: the busiest are listed.
    truncated: bool
    rows: list[ObservedNatRow]
    #: The listed addresses no exit claims, in the order of the rows.
    unmapped_transips: list[str]
    #: The listed ``trandisp`` values ``nat.snat_trandisp`` does not name.
    unknown_trandisp: list[str]
    #: The stored ``nat.egresses`` cannot be parsed: every row has ``egress: null``.
    exits_invalid: bool = False


class ObservedSrcmacInterface(BaseModel):
    srcintf: str
    #: Trusted rows with srcmac and an IPv4 source in the last hour.
    rows: int
    macs: int
    #: The most distinct IPv4 sources of one MAC (as the srcmac guard counts them).
    max_ips_per_mac: int
    #: Listed in ``lookup.srcmac_interfaces``.
    configured: bool


class ObservedSrcmac(BaseModel):
    hours: int
    #: More than 50 interfaces: those with the most rows are listed.
    truncated: bool
    interfaces: list[ObservedSrcmacInterface]


class FortigateObserved(BaseModel):
    #: When the counts were made (they are kept 5 minutes; the claims are always current).
    generated_at: datetime
    nat: ObservedNat
    srcmac: ObservedSrcmac


@router.get("")
def get_settings(db: DbSession, user: SettingsDep) -> SettingsOut:
    """Every visible database setting, by group (contract 2.13)."""
    rows = {row.key: row for row in list_settings(db)}
    groups = []
    for group in GROUPS:
        items = []
        for meta in group_settings(group.id):
            row = rows[meta.key]
            # ``:seed``, ``:system``, ``:migration``: the shipped value, nobody changed it.
            person = (
                row.updated_by if row.updated_by and not row.updated_by.startswith(":") else None
            )
            items.append(
                SettingOut(
                    key=meta.key,
                    value=row.value,
                    default=meta.default,
                    is_default=row.is_default,
                    type=meta.type,
                    min=meta.minimum,
                    max=meta.maximum,
                    reauth=meta.reauth,
                    locked=meta.locked,
                    label_tr=meta.label_tr,
                    label_en=meta.label_en,
                    help_tr=meta.help_tr,
                    help_en=meta.help_en,
                    updated_by=person,
                    updated_at=row.updated_at if person else None,
                )
            )
        groups.append(
            SettingGroup(
                id=group.id, label_tr=group.label_tr, label_en=group.label_en, settings=items
            )
        )
    return SettingsOut(groups=groups)


def _known(key: str) -> None:
    if key not in SETTINGS:  # the key is not repeated: it is whatever was submitted
        raise _error(422, "setting_unknown")
    if SETTINGS[key].locked:
        raise _error(422, "setting_locked", details=[{"key": key}])


def _write(
    db: Session, request: Request, user: CurrentUser, values: list[tuple[str, Any]], **options: Any
) -> SettingsPatchResult:
    changed: list[ChangedSetting] = []
    unchanged: list[str] = []
    for key, value in values:
        if value == get_setting(db, key):
            unchanged.append(key)
            continue
        if options.get("reset"):
            done = reset_setting(
                db, key, actor=user.username, actor_id=user.id, ip=client_ip(request)
            )
        else:
            done = change_setting(
                db, key, value, actor=user.username, actor_id=user.id, ip=client_ip(request)
            )
        changed.append(ChangedSetting(key=key, old=done.old, new=done.new))
    return SettingsPatchResult(changed=changed, unchanged=unchanged)


@router.patch("")
def patch_settings(
    body: SettingsPatch, request: Request, db: DbSession, user: SettingsDep
) -> SettingsPatchResult:
    """Change several settings at once, all or nothing (contract 2.13)."""
    keys = [change.key for change in body.changes]
    if len(set(keys)) != len(keys):
        raise ApiError(
            422,
            "validation_error",
            details=[{"loc": ["body", "changes"], "type": "duplicate_key", "msg": "a key twice"}],
        )
    for key in keys:
        _known(key)
    problems: list[dict[str, Any]] = []
    values: list[tuple[str, Any]] = []
    for change in body.changes:
        try:
            values.append((change.key, validate_setting(change.key, change.value)))
        except SettingValueError as exc:
            problems.append(
                {
                    "key": change.key,
                    "code": exc.code,
                    "message_tr": exc.message_tr,
                    "message_en": exc.message_en,
                }
            )
    if problems:
        raise _error(422, "setting_invalid", details=problems)
    conflicts = [
        {"key": change.key, "current": current}
        for change in body.changes
        if (current := get_setting(db, change.key)) != change.old
    ]
    if conflicts:
        raise _error(409, "setting_conflict", details=conflicts)
    if any(SETTINGS[key].reauth for key in keys):
        ensure_recent_auth(user)
    return _write(db, request, user, values)


@router.post("/{key}/reset")
def reset_to_default(
    key: SettingKey, request: Request, db: DbSession, user: SettingsDep
) -> SettingsPatchResult:
    """Put the shipped default of one setting back (audited ``settings.change``, ``reset``)."""
    _known(key)
    if SETTINGS[key].reauth:
        ensure_recent_auth(user)
    return _write(db, request, user, [(key, SETTINGS[key].default)], reset=True)


# -- what the FortiGate's records show (the exit editor's observed panel) -------------------------


class ObservedState:
    """The kept counts of ``GET /settings/fortigate/observed``, one cache per ``hours``
    (``app.state.arci_observed``)."""

    def __init__(self) -> None:
        self.ttl = OBSERVED_CACHE_SECONDS
        self.counts: dict[int, TtlCache[dict[str, Any]]] = {}
        self._lock = threading.Lock()

    def cache(self, hours: int) -> TtlCache[dict[str, Any]]:
        with self._lock:  # at most 72 caches: hours is validated before
            cache = self.counts.get(hours)
            if cache is None:
                cache = self.counts[hours] = TtlCache(self.ttl)
            return cache


def observed_state(request: Request) -> ObservedState:
    state: ObservedState | None = getattr(request.app.state, "arci_observed", None)
    if state is None:
        state = request.app.state.arci_observed = ObservedState()
    return state


def observed_nat_query(hours: int, now: datetime, untrusted: Collection[str]) -> Executable:
    """The NAT groups of the trusted ``fg_traffic`` rows of ``[now - hours, now]`` with an IPv4
    NAT address (exits refuse IPv6, as ``R20-nat:unmapped-transip`` reads the rows), busiest
    first; one more than :data:`OBSERVED_ROWS`, to tell a truncated list."""
    sessions = func.count().label("sessions")
    query = (
        select(
            FgTraffic.transip,
            FgTraffic.dstintf,
            FgTraffic.trandisp,
            sessions,
            func.min(FgTraffic.time),
            func.max(FgTraffic.time),
        )
        .where(
            FgTraffic.time >= now - timedelta(hours=hours),
            FgTraffic.time <= now,
            FgTraffic.transip.is_not(None),
            func.family(FgTraffic.transip) == 4,
        )
        .group_by(FgTraffic.transip, FgTraffic.dstintf, FgTraffic.trandisp)
        .order_by(sessions.desc(), FgTraffic.transip, FgTraffic.dstintf, FgTraffic.trandisp)
        .limit(OBSERVED_ROWS + 1)
    )
    if untrusted:
        query = query.where(FgTraffic.source.not_in(sorted(untrusted)))
    return query


def observed_counts(session: Session, hours: int, now: datetime) -> dict[str, Any]:
    """The database part of the observed answer (no setting is read): the NAT groups
    (:func:`observed_nat_query`), and per ``srcintf`` the srcmac rows of the last hour with an
    IPv4 source, their MACs and the most IPv4 sources of one MAC
    (:func:`arcilogger_worker.lookup.trust.srcmac_ip_counts`, the srcmac guard's count)."""
    untrusted = sorted(trust.untrusted_sources(session))
    nat = observed_nat_query(hours, now, untrusted)
    groups = [
        {
            "transip": str(ipaddress.ip_address(str(transip))),
            "dstintf": dstintf,
            "trandisp": trandisp,
            "sessions": int(count),
            "first_seen": to_site(first),
            "last_seen": to_site(last),
        }
        for transip, dstintf, trandisp, count, first, last in session.execute(nat)
    ]
    low = now - timedelta(hours=SRCMAC_HOURS)
    per_mac = trust.srcmac_ip_counts(session, low, now, None, untrusted)
    # The rows srcmac_ip_counts counts: srcmac and srcintf set, an IPv4 source, trusted.
    rows = (
        select(FgTraffic.srcintf, func.count())
        .where(
            FgTraffic.time >= low,
            FgTraffic.time <= now,
            FgTraffic.srcmac.is_not(None),
            FgTraffic.srcintf.is_not(None),
            func.family(FgTraffic.srcip) == 4,
        )
        .group_by(FgTraffic.srcintf)
    )
    if untrusted:
        rows = rows.where(FgTraffic.source.not_in(untrusted))
    counted = {str(interface): int(n) for interface, n in session.execute(rows)}
    macs: dict[str, list[int]] = {}
    for (interface, _mac), addresses in per_mac.items():
        macs.setdefault(interface, []).append(addresses)
    interfaces = [
        {
            "srcintf": interface,
            "rows": n,
            "macs": len(macs.get(interface, [])),
            "max_ips_per_mac": max(macs.get(interface, [0])),
        }
        for interface, n in sorted(counted.items(), key=lambda item: (-item[1], item[0]))
    ]
    return {"generated_at": to_site(now), "nat": groups, "srcmac": interfaces}


def _claim(exits: tuple[Egress, ...], transip: str, dstintf: str | None) -> str | None:
    """The exit that claims ``transip`` as a public or NAT address (``internal`` exits
    included, as the unmapped-address alert reads it): of several, the one whose interfaces
    hold ``dstintf``, else the first by name."""
    try:
        claims = [e for e in exits if e.claims_public(transip) or e.claims_transip(transip)]
    except ValueError:
        return None
    on_line = [e for e in claims if dstintf is not None and dstintf in e.interfaces]
    chosen = on_line or claims
    return chosen[0].name if chosen else None


def _texts(db: Session, key: str, default: list[str]) -> list[str]:
    """A list setting read leniently (a value of another shape gives ``default``, as the lookup
    and the alerts read the settings only they use)."""
    value = get_setting(db, key, default)
    return [str(item) for item in value] if isinstance(value, list) else default


def observed_answer(db: Session, counts: dict[str, Any], hours: int) -> FortigateObserved:
    """The answer from ``counts`` (:func:`observed_counts`) and the settings as they are now:
    the claims, ``nat.snat_trandisp`` and ``lookup.srcmac_interfaces``."""
    exits: tuple[Egress, ...] | None
    try:
        exits = parse_egresses(get_setting(db, "nat.egresses", {}))
    except (TypeError, ValueError):
        log.warning("nat.egresses cannot be parsed; the observed rows name no exit")
        exits = None
    snat = _texts(db, "nat.snat_trandisp", ["snat"])
    listed = set(_texts(db, "lookup.srcmac_interfaces", []))
    rows = [
        ObservedNatRow(
            **group,
            egress=None if exits is None else _claim(exits, group["transip"], group["dstintf"]),
            trandisp_known=group["trandisp"] in snat,
        )
        for group in counts["nat"][:OBSERVED_ROWS]
    ]
    interfaces = counts["srcmac"]
    return FortigateObserved(
        generated_at=counts["generated_at"],
        nat=ObservedNat(
            hours=hours,
            truncated=len(counts["nat"]) > OBSERVED_ROWS,
            rows=rows,
            unmapped_transips=list(dict.fromkeys(r.transip for r in rows if r.egress is None)),
            unknown_trandisp=sorted(
                {r.trandisp for r in rows if r.trandisp is not None and not r.trandisp_known}
            ),
            exits_invalid=exits is None,
        ),
        srcmac=ObservedSrcmac(
            hours=SRCMAC_HOURS,
            truncated=len(interfaces) > OBSERVED_INTERFACES,
            interfaces=[
                ObservedSrcmacInterface(**item, configured=item["srcintf"] in listed)
                for item in interfaces[:OBSERVED_INTERFACES]
            ],
        ),
    )


@router.get("/fortigate/observed")
def fortigate_observed(
    request: Request,
    db: DbSession,
    user: SettingsDep,
    hours: Annotated[int, Query(ge=1, le=72, description="Hours of the NAT part")] = 24,
) -> FortigateObserved:
    """What the FortiGate's session records show: NAT addresses and srcmac interfaces (counts
    only, not audited; contract 2.12)."""

    def compute() -> dict[str, Any]:
        seconds = OBSERVED_DEADLINE_SECONDS
        try:
            with statement_deadline(db, seconds):
                counts = observed_counts(db, hours, utc_now())
        except (LookupTimeoutError, DBAPIError) as exc:
            if isinstance(exc, DBAPIError) and not _is_timeout(exc):
                raise
            db.rollback()
            raise _error(504, "fortigate_observed_timeout", seconds=seconds) from None
        db.execute(text("SET LOCAL statement_timeout TO DEFAULT"))
        return counts

    counts = observed_state(request).cache(hours).get_or_compute(compute)
    return observed_answer(db, counts, hours)
