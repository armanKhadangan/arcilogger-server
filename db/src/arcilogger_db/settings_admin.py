"""Administrator changes of database settings, validated and audited.

``arcilogger-db settings set KEY JSON_VALUE --by NAME`` (:mod:`arcilogger_db.cli`) and the web
interface's ``PATCH /settings`` and ``POST /settings/{key}/reset`` (ADR-0014, contract 2.13) call
:func:`change_setting`:

* the key must be a known setting and the value valid for it
  (:func:`arcilogger_db.settings.validate_setting`, normalised);
* the row is written with ``updated_by = NAME`` — never ``:seed``, so ``arcilogger-db upgrade``
  never puts the shipped default back over it (:mod:`arcilogger_db.seed`);
* the same transaction appends ``settings.change`` with ``{"old": …, "new": …}`` to the
  hash-chained audit log (spec 8.5) through the one shared writer
  (:func:`arcilogger_db.audit.append_entry`), with the user's id and address when the web
  interface made the change; the API copies it into the audit spool with its next flush (at start
  and every minute), so it is sealed like every other entry;
* a change of ``nat.egresses`` also keeps the status row ``nat.egresses_changes`` (ADR-0016 D32)
  in the same transaction: for every exit whose content changed (its
  :func:`arcilogger_db.egress.fingerprint`: mode, public and NAT addresses, a changing public
  address), that was added or deleted, the time of the audit entry and the author. A label-,
  interface- or port-preservation-only edit changes nothing there, and no earlier content is
  kept: the audit log holds every old value. A lookup of a moment before the change names the
  exit (``egress_changed_after_event``).

A change made with hand-written SQL is neither validated nor audited, and a row left with
``updated_by = ':seed'`` is reverted by the next upgrade.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_db.audit import append_entry
from arcilogger_db.egress import CHANGES_KEY, EgressValueError, fingerprint, validate_egresses
from arcilogger_db.models import Setting
from arcilogger_db.settings import (
    DEFAULT_SETTINGS,
    SYSTEM_AUTHOR,
    get_setting,
    set_setting,
    validate_setting,
)

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
    actor_id: int | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> SettingChange:
    """Validate, write and audit one change in the caller's (READ COMMITTED) transaction; the
    caller commits. ``actor_id`` and ``ip``: the signed-in user and the request's address (the
    web interface; ``None`` on the console). ``ValueError`` for an invalid actor,
    :class:`~arcilogger_db.settings.SettingValueError` (a ``ValueError``) for an unknown key or
    an invalid value."""
    name = check_actor(actor)
    new = validate_setting(key, value)
    old = get_setting(session, key)
    set_setting(session, key, new, updated_by=name)
    entry = append_entry(
        session,
        actor_id=actor_id,
        actor=name,
        action=AUDIT_ACTION,
        target=f"setting:{key}",
        details={**(details or {}), "old": old, "new": new},
        ip=ip,
    )
    if key == "nat.egresses":
        _record_exit_changes(session, old, new, actor=name, at=entry.time.isoformat())
    return SettingChange(key=key, old=old, new=new, audit_id=entry.id)


def _record_exit_changes(session: Session, old: Any, new: Any, *, actor: str, at: str) -> None:
    """Update ``nat.egresses_changes`` for the exits whose content ``old`` -> ``new`` changed,
    added or deleted (module docstring)."""
    before = old if isinstance(old, dict) else {}
    changes = get_setting(session, CHANGES_KEY)
    record = dict(changes) if isinstance(changes, dict) else {}
    changed: dict[str, tuple[str, bool]] = {}
    for name, exit_ in new.items():
        content = fingerprint(exit_)
        if _content(name, before.get(name)) != content:
            changed[name] = (content, False)
    for name in sorted(before.keys() - new.keys()):
        known = record.get(name)
        old_content = _content(name, before[name]) or (known or {}).get("fingerprint", "")
        changed[name] = (old_content, True)
    for name, (content, deleted) in changed.items():
        record[name] = {
            "fingerprint": content,
            "changed_at": at,
            "changed_by": actor,
            "deleted": deleted,
        }
    if changed:
        set_setting(session, CHANGES_KEY, record, updated_by=SYSTEM_AUTHOR)


def _content(name: str, exit_: Any) -> str | None:
    """The fingerprint of a stored exit; ``None`` for one the rules refuse (stored around the
    validator), which then counts as changed."""
    try:
        return fingerprint(validate_egresses({name: exit_})[name])
    except EgressValueError:
        return None


def reset_setting(
    session: Session,
    key: str,
    *,
    actor: str,
    actor_id: int | None = None,
    ip: str | None = None,
) -> SettingChange:
    """Put the shipped default of ``key`` back (``POST /settings/{key}/reset``): a change like
    any other, audited ``settings.change`` with ``details.reset = true``. The row keeps the
    actor's name, so a later version's new default does not replace the value chosen here."""
    default = copy.deepcopy(DEFAULT_SETTINGS.get(key))
    return change_setting(
        session, key, default, actor=actor, actor_id=actor_id, ip=ip, details={"reset": True}
    )


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
