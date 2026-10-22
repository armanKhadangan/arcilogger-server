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

from arcilogger_core.timeutil import utc_now
from arcilogger_db.audit import append_audit
from arcilogger_db.models import Setting
from arcilogger_db.settings import DEFAULT_SETTINGS, SEED_AUTHOR

log = logging.getLogger("arcilogger.db")


@dataclass
class SeedReport:
    settings_inserted: list[str] = field(default_factory=list)
    settings_refreshed: list[str] = field(default_factory=list)
    #: The licence installation row was created by this seeding.
    license_installation_created: bool = False

    @property
    def changed(self) -> bool:
        """Settings inserted or refreshed (the licence installation is reported apart)."""
        return bool(self.settings_inserted or self.settings_refreshed)


def seed(session: Session) -> SeedReport:
    """Write the seed data (the caller commits)."""
    report = SeedReport()
    _seed_settings(session, report)
    session.flush()
    _seed_license_installation(session, report)
    return report


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
