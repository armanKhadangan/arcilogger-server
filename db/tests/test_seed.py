"""Seed data: default settings, idempotence, no shipped network; the tests' example site."""

from __future__ import annotations

import json
import re
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from arcilogger_db import seed as seed_module
from arcilogger_db.engine import engine_from_url
from arcilogger_db.models import Department, Setting, Source, Vlan
from arcilogger_db.seed import seed
from arcilogger_db.settings import DEFAULT_SETTINGS, SEED_AUTHOR, set_setting
from arcilogger_db.testing import (
    EXAMPLE_AUTHOR,
    EXAMPLE_SETTINGS,
    EXAMPLE_VLANS,
    TestServer,
    seed_example_network,
)

#: The example site's plan, transcribed independently of testing.py: (vlan, subnet, name, server).
EXAMPLE_PLAN = {
    (10, "172.20.10.0/24", "Ağ Cihazları", None),
    (20, "172.20.20.0/24", "Sunucular", None),
    (41, "172.20.41.0/24", "Finans", "dhcp41"),
    (42, "172.20.42.0/24", "Planlama", "dhcp42"),
    (43, "172.20.43.0/24", "İdari İşler", "dhcp43"),
    (60, "172.20.60.0/24", "Wi-Fi Personel", "dhcp60"),
    (61, "172.20.61.0/24", "Wi-Fi Misafir", "dhcp61"),
    (None, "172.31.255.0/30", "Transit", None),
}
DEPARTMENT_VLANS = {
    41: "Finans",
    42: "Planlama",
    43: "İdari İşler",
}


# -- the product's seed ---------------------------------------------------------------------------


def test_shipped_defaults_name_no_network_of_any_site() -> None:
    shipped = json.dumps(DEFAULT_SETTINGS, ensure_ascii=False)
    assert not re.search(
        r"192\.168\.|172\.(20|31)\.|100\.64\.|\bdhcp\d+\b|örnek|ornek", shipped, re.I
    )
    assert DEFAULT_SETTINGS["lease.server_seconds"] == {}


def test_a_new_installation_gets_settings_and_no_network(pg_server: TestServer) -> None:
    """``create_template(example_network=False)`` = what ``arcilogger-db upgrade`` leaves."""
    template = pg_server.create_template(timescale="off", example_network=False)
    try:
        with pg_server.database(template) as name:
            engine = engine_from_url(pg_server.url_for(name), poolclass=NullPool)
            try:
                with Session(engine) as session:
                    for model in (Vlan, Department, Source):
                        assert session.scalars(select(model)).all() == [], model
                    rows = {s.key: s for s in session.scalars(select(Setting))}
                    for key, value in DEFAULT_SETTINGS.items():
                        assert (rows[key].value, rows[key].updated_by) == (value, SEED_AUTHOR)
                    assert "seed.network_version" not in rows
                    assert not seed(session).changed
                    assert session.scalars(select(Vlan)).all() == []
            finally:
                engine.dispose()
    finally:
        pg_server.drop_database(template)


def test_default_settings_are_seeded(db_session: Session) -> None:
    rows = {s.key: s for s in db_session.scalars(select(Setting))}
    for key, value in DEFAULT_SETTINGS.items():
        if key in EXAMPLE_SETTINGS:  # the example site's own value (testing.py)
            assert (rows[key].value, rows[key].updated_by) == (EXAMPLE_SETTINGS[key], "example")
            continue
        assert rows[key].value == value, key
        assert rows[key].updated_by == ":seed"
    assert rows["nat.public_ips"].value == []
    assert rows["nat.wan_transip"].value == ""
    assert rows["lookup.tolerance_seconds"].value == 60
    assert rows["lease.timeout_percent"].value == 110
    assert rows["lookup.max_session_seconds"].value == 86400
    assert rows["auth.idle_minutes"].value == 15
    assert rows["auth.absolute_hours"].value == 12
    assert rows["db.retention_days"].value == 400
    # The FortiGate's transit/WAN address is never assumed to be public.
    assert all("100.64.12.9" not in str(row.value) for row in rows.values())


def test_seed_is_idempotent(db_session: Session) -> None:
    report = seed(db_session)
    assert not report.changed


def test_admin_changes_survive_and_seed_defaults_follow_new_versions(
    db_session: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    set_setting(db_session, "lookup.tolerance_seconds", 120, updated_by="tolga")
    db_session.flush()
    changed: dict[str, Any] = {
        **DEFAULT_SETTINGS,
        "lookup.tolerance_seconds": 90,  # admin changed it -> stays 120
        "auth.idle_minutes": 10,  # still the seeded value -> follows
    }
    monkeypatch.setattr(seed_module, "DEFAULT_SETTINGS", changed)
    report = seed(db_session)
    assert report.settings_refreshed == ["auth.idle_minutes"]
    assert db_session.get(Setting, "lookup.tolerance_seconds").value == 120  # type: ignore[union-attr]
    assert db_session.get(Setting, "auth.idle_minutes").value == 10  # type: ignore[union-attr]


def test_seed_never_adds_a_network_but_re_adds_a_deleted_setting(db_session: Session) -> None:
    for model in (Vlan, Department):
        for row in db_session.scalars(select(model)):
            db_session.delete(row)
    setting = db_session.get(Setting, "evidence.keep_days")
    assert setting is not None
    db_session.delete(setting)
    db_session.flush()
    report = seed(db_session)
    assert report.settings_inserted == ["evidence.keep_days"]
    assert report.settings_refreshed == []
    assert db_session.scalars(select(Vlan)).all() == []
    assert db_session.scalars(select(Department)).all() == []


# -- the example site of the tests (arcilogger_db.testing) ----------------------------------------


def test_example_vlans_match_the_plan(db_session: Session) -> None:
    rows = db_session.scalars(select(Vlan)).all()
    assert {(v.vlan_id, v.subnet, v.name_tr, v.dhcp_server) for v in rows} == EXAMPLE_PLAN
    assert len(EXAMPLE_VLANS) == len(EXAMPLE_PLAN)
    by_id = {v.vlan_id: v for v in rows}
    assert by_id[61].guest is True
    # The guest network's DHCP server has its own (short) lease time.
    assert EXAMPLE_SETTINGS["lease.server_seconds"] == {by_id[61].dhcp_server: 3600}
    assert [v.vlan_id for v in rows if v.guest] == [61]
    # The DHCP server name contains the VLAN (dhcp60 -> 60) wherever there is one.
    for vlan in rows:
        if vlan.dhcp_server is not None:
            assert vlan.dhcp_server == f"dhcp{vlan.vlan_id}"


def test_example_departments_from_user_vlans(db_session: Session) -> None:
    departments = {d.id: d.name for d in db_session.scalars(select(Department))}
    assert sorted(departments.values()) == sorted(DEPARTMENT_VLANS.values())
    for vlan in db_session.scalars(select(Vlan)):
        expected = DEPARTMENT_VLANS.get(vlan.vlan_id or -1)
        assert (departments.get(vlan.department_id) if vlan.department_id else None) == expected


def test_example_network_on_empty_tables_creates_everything_once(db_session: Session) -> None:
    for model in (Vlan, Department):
        for row in db_session.scalars(select(model)):
            db_session.delete(row)
    set_setting(db_session, "lease.server_seconds", {}, updated_by=SEED_AUTHOR)
    db_session.flush()
    first = seed_example_network(db_session)
    assert (first.vlans_inserted, first.departments_inserted) == (8, 3)
    assert first.settings_written == ["lease.server_seconds"]
    second = seed_example_network(db_session)
    assert (second.vlans_inserted, second.departments_inserted) == (0, 0)
    assert second.settings_written == []
    stored = db_session.get(Setting, "lease.server_seconds")
    assert stored is not None
    db_session.refresh(stored)
    assert (stored.value, stored.updated_by) == ({"dhcp61": 3600}, EXAMPLE_AUTHOR)
    assert not seed(db_session).changed  # the seed leaves the example's value alone


def test_example_network_keeps_what_an_administrator_changed(db_session: Session) -> None:
    db_session.execute(text("DELETE FROM vlans WHERE subnet = '172.20.20.0/24'"))
    set_setting(db_session, "lease.server_seconds", {"dhcp19": 600}, updated_by="tolga")
    db_session.flush()
    report = seed_example_network(db_session)
    assert (report.vlans_inserted, report.settings_written) == (1, [])
    value = db_session.scalar(select(Setting.value).where(Setting.key == "lease.server_seconds"))
    assert value == {"dhcp19": 600}
