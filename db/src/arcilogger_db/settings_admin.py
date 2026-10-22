"""Administrator changes of database settings, validated and audited (until the M6 settings page).

``arcilogger-db settings set KEY JSON_VALUE --by NAME`` (:mod:`arcilogger_db.cli`) calls
:func:`change_setting`, and the settings page of a later version will call the same function:

* the key must be a known setting and the value valid for it
  (:func:`arcilogger_db.settings.validate_setting`, normalised);
* the row is written with ``updated_by = NAME`` — never ``:seed``, so ``arcilogger-db upgrade``
  never puts the shipped default back over it (:mod:`arcilogger_db.seed`);
* the same transaction appends ``settings.change`` with ``{"old": …, "new": …}`` to the
  hash-chained audit log (spec 8.5) through the one shared writer
  (:func:`arcilogger_db.audit.append_entry`); the API copies it into the audit spool with its next
  flush (at start and every minute), so it is sealed like every other entry.

A change made with hand-written SQL is neither validated nor audited, and a row left with
``updated_by = ':seed'`` is reverted by the next upgrade.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_db.audit import append_entry
from arcilogger_db.models import Setting
from arcilogger_db.settings import DEFAULT_SETTINGS, get_setting, set_setting, validate_setting

AUDIT_ACTION: Final = "settings.change"
#: Who made the change (a person's name or user name; ``:`` is reserved for internal authors).
_ACTOR = re.compile(r"^[^\s:\x00-\x1f\x7f][^\x00-\x1f\x7f]{0,99}$")


@dataclass(frozen=True)
class SettingChange:
    key: str
    old: Any
    new: Any
    audit_id: int

    @property
    def changed(self) -> bool:
        return bool(self.old != self.new)


def check_actor(actor: str) -> str:
    """The ``--by`` name, stripped; ``ValueError`` for an empty, internal or unprintable one."""
    name = actor.strip()
    if not _ACTOR.match(name):
        raise ValueError(
            "değişikliği yapan kişinin adı gerekir (--by): boş olamaz, ':' ile başlayamaz, "
            "kontrol karakteri içeremez, en çok 100 karakter"
        )
    return name


def change_setting(
    session: Session,
    key: str,
    value: Any,
    *,
    actor: str,
    details: dict[str, Any] | None = None,
) -> SettingChange:
    """Validate, write and audit one change in the caller's (READ COMMITTED) transaction; the
    caller commits. ``ValueError`` for an unknown key, an invalid value or actor."""
    name = check_actor(actor)
    new = validate_setting(key, value)
    old = get_setting(session, key)
    set_setting(session, key, new, updated_by=name)
    entry = append_entry(
        session,
        actor_id=None,
        actor=name,
        action=AUDIT_ACTION,
        target=f"setting:{key}",
        details={**(details or {}), "old": old, "new": new},
    )
    return SettingChange(key=key, old=old, new=new, audit_id=entry.id)


@dataclass(frozen=True)
class SettingRow:
    key: str
    value: Any
    #: ``None`` when no row is stored (the shipped default applies).
    updated_by: str | None
    updated_at: Any

    @property
    def is_default(self) -> bool:
        return bool(self.value == DEFAULT_SETTINGS.get(self.key))


def list_settings(session: Session) -> list[SettingRow]:
    """Every known setting with its current value and who set it (internal keys left out)."""
    stored = {row.key: row for row in session.scalars(select(Setting))}
    rows = []
    for key in sorted(DEFAULT_SETTINGS):
        row = stored.get(key)
        if row is None:
            rows.append(SettingRow(key, get_setting(session, key), None, None))
        else:
            rows.append(SettingRow(key, row.value, row.updated_by, row.updated_at))
    return rows
