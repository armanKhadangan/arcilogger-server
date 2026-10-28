"""Revision ``0018_nat_egresses`` (Wave 3 contract 2.2.2, 2.8.2, 3.2; ADR-0016 D18, D32): the
conversion of the legacy NAT keys for every case of 2.8.2 (``tests/fixtures/egress-cases.json``),
the status rows ``nat.egresses_migration`` and ``nat.egresses_changes``, the legacy rows kept or
deleted, the seed's one audit entry, the frozen converter and validator equal to the
application's, the downgrade (as converted, and lossy after an administrator's change) and the
re-upgrade after a rollback (nothing converted again; ``R20-nat:legacy-changed``'s condition)."""

from __future__ import annotations

import importlib.util
import json
import logging
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from alembic import command
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from arcilogger_core.canonical import MAX_SAFE_INTEGER
from arcilogger_db import egress
from arcilogger_db.audit import verify_audit_chain
from arcilogger_db.egress import (
    CHANGES_KEY,
    LEGACY_KEYS,
    MIGRATION_KEY,
    EgressValueError,
    fingerprint,
    legacy_egresses,
    parse_egresses,
    validate_egresses,
)
from arcilogger_db.migrate import alembic_config, current_revisions, upgrade
from arcilogger_db.models import AuditLog
from arcilogger_db.settings_admin import change_setting

BEFORE = "0017_fortigate_dhcp"
REVISION = "0018_nat_egresses"
CASES = json.loads(
    (Path(__file__).parents[2] / "tests" / "fixtures" / "egress-cases.json").read_text("utf-8")
)
LEGACY = CASES["legacy"]
DEFAULTS: dict[str, Any] = {"nat.public_ips": [], "nat.wan_transip": "", "lookup.port_hint": False}
#: When the administrator last changed the legacy rows (fictional).
CHANGED = datetime(2026, 9, 14, 7, 30, tzinfo=UTC)


def _frozen() -> ModuleType:
    """The revision module itself (migrations are not importable as a package)."""
    path = Path(egress.__file__).parent / "migrations" / "versions" / "v0018_nat_egresses.py"
    spec = importlib.util.spec_from_file_location("v0018_nat_egresses", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


FROZEN = _frozen()


# -- helpers --------------------------------------------------------------------------------------


def _migrate(engine: Engine, revision: str) -> None:
    """Only the migration (no seed), so its own writes can be compared."""
    with engine.begin() as connection:
        if revision == BEFORE:
            command.downgrade(alembic_config(connection), revision)
        else:
            command.upgrade(alembic_config(connection), revision)


def _at_0017(engine: Engine, *, egresses_row: bool = False) -> None:
    """The database as Wave 2 left it: at ``0017``, the seed's legacy rows, no exits."""
    _migrate(engine, BEFORE)
    with engine.begin() as connection:
        connection.execute(text("DELETE FROM settings WHERE key = 'nat.egresses'"))
        if egresses_row:  # a seed of this wave already ran (an interrupted upgrade)
            _put(connection, "nat.egresses", {}, ":seed", CHANGED - timedelta(days=30))
        for key, default in DEFAULTS.items():
            _put(connection, key, default, ":seed", CHANGED - timedelta(days=60))


def _put(connection: Any, key: str, value: Any, author: str, at: datetime) -> None:
    connection.execute(
        text(
            "INSERT INTO settings (key, value, updated_by, updated_at) VALUES (:key, "
            "CAST(:value AS jsonb), :author, :at) ON CONFLICT (key) DO UPDATE SET value = "
            "EXCLUDED.value, updated_by = EXCLUDED.updated_by, updated_at = EXCLUDED.updated_at"
        ),
        {"key": key, "value": json.dumps(value), "author": author, "at": at},
    )


def _administrator(engine: Engine, values: Mapping[str, Any], author: str = "tolga") -> None:
    """``values`` saved by an administrator, one minute apart (the last one at CHANGED)."""
    with engine.begin() as connection:
        for index, (key, value) in enumerate(reversed(list(values.items()))):
            _put(connection, key, value, author, CHANGED - timedelta(minutes=index))


def _rows(engine: Engine) -> dict[str, tuple[Any, str, datetime]]:
    with engine.connect() as connection:
        found = connection.execute(
            text("SELECT key, value, updated_by, updated_at FROM settings")
        ).all()
    return {key: (value, author, at) for key, value, author, at in found}


def _audit(engine: Engine) -> list[AuditLog]:
    with Session(engine) as session:
        return list(
            session.scalars(
                select(AuditLog)
                .where(AuditLog.target == "setting:nat.egresses")
                .order_by(AuditLog.id)
            )
        )


def _warnings(caplog: Any) -> list[logging.LogRecord]:
    """The warnings the test captured: alembic's INFO lines name the revision too, and reach the
    capture whenever the root logger lets INFO through (``--log-level``, a command line run
    earlier in the same process)."""
    return [r for r in caplog.records if r.levelno >= logging.WARNING]


def _legacy_values(row: Mapping[str, Any]) -> dict[str, Any]:
    """The administrator's rows of a fixture row: every key that differs from the default, and
    ``nat.wan_transip`` when all are defaults (an administrator saved a default)."""
    values = {
        "nat.public_ips": row["public_ips"],
        "nat.wan_transip": row["wan_transip"],
        "lookup.port_hint": row["port_hint"],
    }
    changed = {key: value for key, value in values.items() if value != DEFAULTS[key]}
    return changed or {"nat.wan_transip": ""}


def _legacy_changed(engine: Engine) -> list[str]:
    """The condition of ``R20-nat:legacy-changed`` (D18): administrator-owned legacy rows
    written after ``nat.egresses``."""
    with engine.connect() as connection:
        return list(
            connection.execute(
                text(
                    "SELECT key FROM settings WHERE key = ANY(:keys) AND updated_by NOT LIKE "
                    "':%' AND updated_at > COALESCE((SELECT updated_at FROM settings WHERE key "
                    "= 'nat.egresses'), '-infinity') ORDER BY key"
                ),
                {"keys": list(LEGACY_KEYS)},
            ).scalars()
        )


# -- the frozen copies ----------------------------------------------------------------------------


@pytest.mark.parametrize("row", LEGACY, ids=[r["name"] for r in LEGACY])
def test_the_frozen_converter_is_the_applications(row: dict[str, Any]) -> None:
    args = (row["public_ips"], row["wan_transip"], row["port_hint"])
    assert FROZEN.legacy_egresses(*args) == legacy_egresses(*args)


@pytest.mark.parametrize("case", CASES["cases"], ids=[c["name"] for c in CASES["cases"]])
def test_the_frozen_validator_is_the_applications(case: dict[str, Any]) -> None:
    try:
        expected: Any = validate_egresses(case["value"])
    except EgressValueError as exc:
        expected = exc.code
    try:
        frozen: Any = FROZEN.validate_egresses(case["value"])
    except FROZEN.RefusedError as exc:
        frozen = exc.code
    assert frozen == expected
    if isinstance(frozen, dict):
        for exit_ in frozen.values():
            assert FROZEN.fingerprint(exit_) == fingerprint(exit_)


def test_the_frozen_constants_are_the_applications() -> None:
    assert FROZEN.NOT_PUBLIC == egress.NOT_PUBLIC
    assert FROZEN.PRIVATE_OR_SHARED == egress.PRIVATE_OR_SHARED
    assert FROZEN.FIELDS == egress.EGRESS_FIELDS
    assert FROZEN.MODES == egress.EGRESS_MODES
    assert FROZEN.FINGERPRINT_FIELDS == egress.FINGERPRINT_FIELDS
    assert (FROZEN.MIGRATION_KEY, FROZEN.CHANGES_KEY) == (MIGRATION_KEY, CHANGES_KEY)
    assert FROZEN.LEGACY_KEYS == LEGACY_KEYS
    assert (FROZEN.DIRECT_LABEL, FROZEN.WAN_LABEL, FROZEN.CATCH_ALL_LABEL) == (
        egress.LEGACY_DIRECT_LABEL,
        egress.LEGACY_WAN_LABEL,
        egress.LEGACY_CATCH_ALL_LABEL,
    )


# -- the upgrade ----------------------------------------------------------------------------------


@pytest.mark.parametrize("row", LEGACY, ids=[r["name"] for r in LEGACY])
def test_the_case_matrix(db_engine: Engine, row: dict[str, Any], caplog: Any) -> None:
    _at_0017(db_engine)
    administrator = _legacy_values(row)
    _administrator(db_engine, administrator)
    before = _rows(db_engine)
    with caplog.at_level(logging.WARNING):
        _migrate(db_engine, REVISION)
    rows = _rows(db_engine)
    expected = row["exits"] if row["valid"] else {}
    value, author, at = rows["nat.egresses"]
    assert (value, author, at) == (expected, ":migration", CHANGED)
    assert validate_egresses(value) == value  # never a value the rules refuse
    parse_egresses(value)
    status, status_author, _ = rows[MIGRATION_KEY]
    assert status_author == ":migration"
    raw = {
        "nat.public_ips": row["public_ips"],
        "nat.wan_transip": row["wan_transip"],
        "lookup.port_hint": row["port_hint"],
    }
    assert status == {
        "migration": REVISION,
        "from": row.get("from", raw),  # a number canonical JSON refuses: its text
        "new": expected,
        "dropped": row["dropped"],
        "conversion_failed": not row["valid"],
        "audited": False,
    }
    changes, changes_author, _ = rows[CHANGES_KEY]
    assert changes_author == ":migration"
    assert changes == {
        name: {
            "fingerprint": fingerprint(exit_),
            "changed_at": CHANGED.isoformat(),
            "changed_by": "tolga",
            "deleted": False,
        }
        for name, exit_ in expected.items()
    }
    for key in LEGACY_KEYS:  # the administrator's rows are kept as they were, the seed's go
        if key in administrator:
            assert rows[key] == before[key], key
        else:
            assert key not in rows, key
    if not row["valid"]:
        (warning,) = [r for r in _warnings(caplog) if "0018_nat_egresses" in r.getMessage()]
        assert "setting_" in warning.getMessage()  # the rule, never a value
        for address in [*row["public_ips"], row["wan_transip"]]:
            if address:
                assert str(address) not in warning.getMessage()


def test_the_latest_change_and_its_author(db_engine: Engine) -> None:
    """``updated_at`` of ``nat.egresses`` and the change record take the administrator's latest
    legacy row, its author included."""
    _at_0017(db_engine)
    with db_engine.begin() as connection:
        _put(connection, "nat.public_ips", ["203.0.113.200"], "ayse", CHANGED - timedelta(days=3))
        _put(connection, "nat.wan_transip", "100.64.12.9", "tolga", CHANGED)
        _put(connection, "lookup.port_hint", True, "ayse", CHANGED - timedelta(days=1))
    _migrate(db_engine, REVISION)
    rows = _rows(db_engine)
    assert rows["nat.egresses"][2] == CHANGED
    changes = rows[CHANGES_KEY][0]
    assert sorted(changes) == ["genel-1", "wan"]
    assert {entry["changed_by"] for entry in changes.values()} == {"tolga"}
    assert rows["nat.egresses"][0]["wan"]["port_preservation"] == "preserved"


def test_a_seed_owned_exits_row_is_replaced(db_engine: Engine) -> None:
    """``nat.egresses`` written by the seed (an upgrade interrupted after its seed) is converted
    over; one an administrator saved is left alone."""
    _at_0017(db_engine, egresses_row=True)
    _administrator(db_engine, {"nat.wan_transip": "100.64.12.9"})
    _migrate(db_engine, REVISION)
    assert sorted(_rows(db_engine)["nat.egresses"][0]) == ["wan"]


def test_exits_already_set_are_left_alone(db_engine: Engine) -> None:
    _at_0017(db_engine)
    _administrator(db_engine, {"nat.public_ips": ["203.0.113.200"]})
    mine = {"hat-1": {"mode": "direct", "public": ["203.0.113.200"]}}
    with db_engine.begin() as connection:
        _put(connection, "nat.egresses", mine, "ayse", CHANGED + timedelta(days=1))
    before = _rows(db_engine)
    _migrate(db_engine, REVISION)
    rows = _rows(db_engine)
    assert rows["nat.egresses"] == before["nat.egresses"]
    assert MIGRATION_KEY not in rows
    assert CHANGES_KEY not in rows
    assert rows["nat.public_ips"] == before["nat.public_ips"]
    assert "nat.wan_transip" not in rows  # the seed's row


def test_the_shipped_defaults_convert_nothing(db_engine: Engine) -> None:
    """Only the seed's legacy rows: they are deleted, no status row is written, and the seed's
    ``nat.egresses`` (an empty object, the implicit catch-all) applies."""
    _at_0017(db_engine)
    _migrate(db_engine, REVISION)
    rows = _rows(db_engine)
    assert not set(rows) & {*LEGACY_KEYS, MIGRATION_KEY, CHANGES_KEY, "nat.egresses"}
    upgrade(db_engine)
    rows = _rows(db_engine)
    assert rows["nat.egresses"][:2] == ({}, ":seed")
    assert not set(rows) & {*LEGACY_KEYS, MIGRATION_KEY, CHANGES_KEY}
    assert _audit(db_engine) == []


def test_the_seed_writes_the_audit_entry_once(db_engine: Engine) -> None:
    _at_0017(db_engine)
    _administrator(
        db_engine, {"nat.public_ips": ["203.0.113.200"], "nat.wan_transip": "100.64.12.9"}
    )
    # The migration committed, then the upgrade stopped before its seed (one transaction in
    # arcilogger-db upgrade; two here): the next upgrade writes the entry, once.
    _migrate(db_engine, REVISION)
    assert _audit(db_engine) == []
    result = upgrade(db_engine)
    assert result.seed.egresses_audited
    converted, _ = legacy_egresses(["203.0.113.200"], "100.64.12.9", False)
    (entry,) = _audit(db_engine)
    assert (entry.action, entry.target) == ("settings.change", "setting:nat.egresses")
    assert entry.details == {
        "old": None,
        "new": converted,
        "migration": REVISION,
        "from": {
            "nat.public_ips": ["203.0.113.200"],
            "nat.wan_transip": "100.64.12.9",
            "lookup.port_hint": False,
        },
        "dropped": [],
        "conversion_failed": False,
    }
    assert _rows(db_engine)[MIGRATION_KEY][0]["audited"] is True
    again = upgrade(db_engine)
    assert not again.seed.egresses_audited
    assert len(_audit(db_engine)) == 1
    with Session(db_engine) as session:
        assert verify_audit_chain(session).ok


def test_the_audit_entry_names_what_was_dropped(db_engine: Engine) -> None:
    _at_0017(db_engine)
    _administrator(db_engine, {"nat.public_ips": ["not an address", "2001:db8::44"]})
    upgrade(db_engine)
    (entry,) = _audit(db_engine)
    assert entry.details["new"] == {}
    assert entry.details["dropped"] == [
        {"value": "not an address", "reason": "invalid"},
        {"value": "2001:db8::44", "reason": "ipv6"},
    ]


def test_numbers_canonical_json_refuses_never_stop_the_upgrade(db_engine: Engine) -> None:
    """Legacy values stored without validation (2.8.2 (g)) include numbers an audit entry
    cannot hold (a float, an integer beyond 2^53): the status row keeps them as text, so the
    seed's entry is written and the next upgrade does not fail at the seed."""
    (row,) = [r for r in LEGACY if r["name"] == "g: numbers stored without validation"]
    _at_0017(db_engine)
    legacy = {"nat.public_ips": row["public_ips"], "lookup.port_hint": row["port_hint"]}
    _administrator(db_engine, legacy)
    assert upgrade(db_engine).seed.egresses_audited
    (entry,) = _audit(db_engine)
    assert entry.details["from"] == row["from"]
    assert entry.details["dropped"] == [{"value": "1.5", "reason": "invalid"}]
    assert _rows(db_engine)[MIGRATION_KEY][0]["audited"] is True
    assert not upgrade(db_engine).seed.egresses_audited
    assert len(_audit(db_engine)) == 1
    assert FROZEN.MAX_SAFE_INTEGER == MAX_SAFE_INTEGER
    with Session(db_engine) as session:
        assert verify_audit_chain(session).ok


# -- the downgrade --------------------------------------------------------------------------------


def test_the_downgrade_gives_back_the_kept_rows(db_engine: Engine) -> None:
    _at_0017(db_engine)
    _administrator(
        db_engine, {"nat.public_ips": ["203.0.113.200"], "nat.wan_transip": "100.64.12.9"}
    )
    kept = {key: row for key, row in _rows(db_engine).items() if key in LEGACY_KEYS}
    upgrade(db_engine)
    _migrate(db_engine, BEFORE)
    rows = _rows(db_engine)
    assert not set(rows) & {"nat.egresses", MIGRATION_KEY, CHANGES_KEY}
    assert {key: rows[key] for key in ("nat.public_ips", "nat.wan_transip")} == {
        key: kept[key] for key in ("nat.public_ips", "nat.wan_transip")
    }
    # Up again: the same conversion (the audit entry of the first upgrade stays the only one).
    upgrade(db_engine)
    assert (
        _rows(db_engine)["nat.egresses"][0]
        == legacy_egresses(["203.0.113.200"], "100.64.12.9", False)[0]
    )
    with db_engine.connect() as connection:
        assert current_revisions(connection) == (REVISION,)


def test_the_downgrade_rebuilds_the_legacy_rows_after_a_change(
    db_engine: Engine, caplog: Any
) -> None:
    """After an administrator changed the exits, the legacy rows are rebuilt from them where
    they allow it, the rest named in the log (lossy)."""
    _at_0017(db_engine)
    _administrator(db_engine, {"nat.wan_transip": "100.64.12.9"})
    upgrade(db_engine)
    exits = {
        "hat-1": {"mode": "direct", "public": ["203.0.113.200", "203.0.113.208/29"]},
        "hat-2": {
            "mode": "isp_nat",
            "public": ["198.51.100.77"],
            "transips": ["100.64.13.9"],
            "port_preservation": "preserved",
        },
        "hat-3": {"mode": "isp_nat", "transips": ["100.64.14.9"], "interfaces": ["wan3"]},
        "misafir": {"mode": "not_logged", "public": ["198.51.100.90"]},
        "dmz": {"mode": "internal", "transips": ["172.20.99.1"]},
    }
    with Session(db_engine) as session:
        change_setting(session, "nat.egresses", exits, actor="ayse")
        session.commit()
    with caplog.at_level(logging.WARNING):
        _migrate(db_engine, BEFORE)
    rows = _rows(db_engine)
    assert rows["nat.public_ips"][:2] == (["203.0.113.200"], ":migration")
    assert rows["nat.wan_transip"][:2] == ("100.64.13.9", ":migration")
    assert rows["lookup.port_hint"][:2] == (True, ":migration")
    assert not set(rows) & {"nat.egresses", MIGRATION_KEY, CHANGES_KEY}
    (warning,) = [m for r in _warnings(caplog) if "0018_nat_egresses" in (m := r.getMessage())]
    for lost in ("hat-1 (public networks)", "hat-2 (public)", "hat-3", "misafir", "dmz"):
        assert lost in warning


# -- the re-upgrade after a rollback (D18) --------------------------------------------------------


def test_a_legacy_edit_during_a_rollback_is_named_never_converted(db_engine: Engine) -> None:
    _at_0017(db_engine)
    _administrator(db_engine, {"nat.wan_transip": "100.64.12.9"})
    upgrade(db_engine)
    exits_before = _rows(db_engine)["nat.egresses"]
    entries = len(_audit(db_engine))
    assert _legacy_changed(db_engine) == []  # written together, not later
    # -Rollback without -RestoreDatabase: the previous version runs on this schema and its
    # administrator edits nat.wan_transip there.
    with db_engine.begin() as connection:
        _put(connection, "nat.wan_transip", "100.64.12.10", "tolga", datetime.now(UTC))
    upgrade(db_engine)  # the re-upgrade: the database is at 0018, only the seed runs
    rows = _rows(db_engine)
    assert rows["nat.egresses"] == exits_before
    assert len(_audit(db_engine)) == entries
    assert _legacy_changed(db_engine) == ["nat.wan_transip"]
    # Saving the exits closes the condition.
    with Session(db_engine) as session:
        change_setting(session, "nat.egresses", exits_before[0], actor="tolga")
        session.commit()
    assert _legacy_changed(db_engine) == []


def test_a_legacy_edit_on_a_site_with_the_shipped_defaults(db_engine: Engine) -> None:
    """No status row was written (nothing converted); the previous version's seed puts its
    defaults back and its administrator sets nat.wan_transip: named the same way."""
    _at_0017(db_engine)
    upgrade(db_engine)
    with db_engine.begin() as connection:
        for key, default in DEFAULTS.items():
            _put(connection, key, default, ":seed", datetime.now(UTC))
        _put(connection, "nat.wan_transip", "100.64.12.9", "tolga", datetime.now(UTC))
    upgrade(db_engine)
    rows = _rows(db_engine)
    assert rows["nat.egresses"][:2] == ({}, ":seed")
    assert MIGRATION_KEY not in rows
    assert _audit(db_engine) == []
    assert _legacy_changed(db_engine) == ["nat.wan_transip"]
