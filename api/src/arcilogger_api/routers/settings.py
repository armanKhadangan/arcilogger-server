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
* ``POST /settings/{key}/reset`` — the shipped default back (audited with ``reset: true``).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from pydantic import BaseModel, ConfigDict, Field, JsonValue
from sqlalchemy.orm import Session

from arcilogger_api.deps import DbSession
from arcilogger_api.errors import ApiError
from arcilogger_api.security import CurrentUser, client_ip, ensure_recent_auth, require
from arcilogger_db.settings import (
    SETTING_ERRORS,
    SettingValueError,
    get_setting,
    validate_setting,
)
from arcilogger_db.settings_admin import change_setting, list_settings, reset_setting
from arcilogger_db.settings_meta import GROUPS, SETTINGS, group_settings

router = APIRouter(prefix="/settings", tags=["settings"])

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
}


def _error(status: int, code: str, details: Any = None) -> ApiError:
    return ApiError(status, code, *MESSAGES[code], details=details)


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
