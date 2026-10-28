"""Seed data: the default settings.

Seeding runs after every ``arcilogger-db upgrade`` and is idempotent:

* **Settings:** missing keys are inserted with ``updated_by = ':seed'``. A row still owned by the
  seed follows a changed shipped default; a value an administrator changed (``updated_by`` is a
  user name, which can never start with ``:``) is never touched.
* **No network:** ARCILOGGER ships no VLAN plan, no departments and no log sources — they are the
  site's own data, entered by its administrator (log sources: ``[[sources]]`` of
  ``arcilogger.toml``). A new installation starts with empty ``vlans`` and ``departments``
  tables; rows an earlier version seeded (marker setting ``seed.network_version``) are kept as
  they are. The tests' fictional example site is :func:`arcilogger_db.testing.seed_example_network`.
* **The conversion of the legacy NAT keys** (revision ``0018_nat_egresses``, ADR-0016 D18): no
  revision writes audit entries (the audit chain is application code), so the migration leaves a
  status row ``nat.egresses_migration`` with ``audited: false`` and the seed turns it into the
  one ``settings.change`` entry of ``nat.egresses`` (target ``setting:nat.egresses``, as
  :func:`arcilogger_db.settings_admin.change_setting` writes it) and sets ``audited: true`` in
  the same transaction, so a crash in between writes it at the next upgrade, never twice. The
  seed converts nothing itself: legacy rows edited by the previous version after a rollback are
  named by the alert ``R20-nat:legacy-changed`` and entered in the exits by hand.
* **Licence installation:** the installation id and the trial start (``license_installation``
  row 1, :func:`arcilogger_db.licensing.ensure_installation`, audited ``license.trial_start``),
  once. Guarded (licensing contract 4.1, 5.2): the licence code is imported inside a ``try`` and
  runs in a SAVEPOINT, so any failure logs one warning and the upgrade continues (the first
  status read creates the row later).
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from arcilogger_core.timeutil import utc_now
from arcilogger_db.audit import append_audit
from arcilogger_db.egress import MIGRATION_KEY
from arcilogger_db.models import Setting
from arcilogger_db.settings import DEFAULT_SETTINGS, SEED_AUTHOR

log = logging.getLogger("arcilogger.db")


@dataclass
class SeedReport:
    settings_inserted: list[str] = field(default_factory=list)
    settings_refreshed: list[str] = field(default_factory=list)
    #: The licence installation row was created by this seeding.
    license_installation_created: bool = False
    #: The conversion of the legacy NAT keys was audited by this seeding.
    egresses_audited: bool = False

    @property
    def changed(self) -> bool:
        """Settings inserted or refreshed (the licence installation is reported apart)."""
        return bool(self.settings_inserted or self.settings_refreshed)


def seed(session: Session) -> SeedReport:
    """Write the seed data (the caller commits)."""
    report = SeedReport()
    _seed_settings(session, report)
    session.flush()
    _audit_egresses_migration(session, report)
    _seed_license_installation(session, report)
    return report


def _audit_egresses_migration(session: Session, report: SeedReport) -> None:
    """The one ``settings.change`` entry of the legacy NAT conversion (module docstring)."""
    row = session.get(Setting, MIGRATION_KEY)
    if row is None or not isinstance(row.value, dict) or row.value.get("audited") is not False:
        return
    status = row.value
    append_audit(
        session,
        action="settings.change",
        target="setting:nat.egresses",
        details={
            "old": None,
            "new": status.get("new"),
            "migration": status.get("migration"),
            "from": status.get("from"),
            "dropped": status.get("dropped"),
            "conversion_failed": status.get("conversion_failed"),
        },
    )
    row.value = {**status, "audited": True}
    flag_modified(row, "value")
    session.flush()
    report.egresses_audited = True


def _seed_license_installation(session: Session, report: SeedReport) -> None:
    """Row 1 of ``license_installation``, guarded: never fails the upgrade (contract 4.1)."""
    try:
        from arcilogger_db import licensing

        with session.begin_nested():
            created: list[bool] = []

            def audit(action: str, target: str | None, details: Mapping[str, Any]) -> None:
                created.append(True)
                append_audit(session, action=action, target=target, details=details)

            licensing.ensure_installation(session, now=utc_now(), audit=audit)
            session.flush()
        report.license_installation_created = bool(created)
    except Exception as exc:  # the licence code must never stop an upgrade
        log.warning("license: installation row not created: %s", exc)


def _seed_settings(session: Session, report: SeedReport) -> None:
    existing = {row.key: row for row in session.scalars(select(Setting))}
    for key, default in DEFAULT_SETTINGS.items():
        row = existing.get(key)
        if row is None:
            session.add(Setting(key=key, value=default, updated_by=SEED_AUTHOR))
            report.settings_inserted.append(key)
        elif row.updated_by == SEED_AUTHOR and row.value != default:
            row.value = default
            report.settings_refreshed.append(key)
