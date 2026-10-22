"""VLAN lookup by address and the department rule of spec 2.2 / 9.2."""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session

from arcilogger_db.models import Department, Vlan
from arcilogger_db.network import department_for_ip, find_vlan, vlan_department


@pytest.mark.parametrize(
    ("ip", "vlan_id", "department", "source"),
    [
        ("172.20.43.23", 43, "İdari İşler", "linked"),
        ("172.20.41.200", 41, "Finans", "linked"),
        (" 172.20.42.57 ", 42, "Planlama", "linked"),
        # Staff Wi-Fi has no linked department: the VLAN's name is the department (spec 2.2).
        ("172.20.60.120", 60, "Wi-Fi Personel", "vlan_name"),
        ("172.20.61.101", 61, "Wi-Fi Misafir", "vlan_name"),
        ("172.31.255.2", None, "Transit", "vlan_name"),
    ],
)
def test_department_for_ip_follows_the_vlan(
    db_session: Session, ip: str, vlan_id: int | None, department: str, source: str
) -> None:
    found = department_for_ip(db_session, ip)
    assert found is not None
    assert (found.vlan_id, found.department, found.source) == (vlan_id, department, source)


def test_outside_every_vlan_and_invalid_input(db_session: Session) -> None:
    assert find_vlan(db_session, "100.64.12.9") is None
    assert department_for_ip(db_session, "8.8.8.8") is None
    with pytest.raises(ValueError, match="does not appear to be an IPv4 or IPv6 address"):
        find_vlan(db_session, "172.20.60")


def test_most_specific_subnet_wins_and_admin_links_apply(db_session: Session) -> None:
    db_session.add(Department(name="Depo"))
    db_session.flush()
    depot = db_session.query(Department).filter_by(name="Depo").one()
    db_session.add(Vlan(subnet="172.20.60.0/25", vlan_id=None, name_tr="Depo Wi-Fi"))
    db_session.flush()
    narrow = find_vlan(db_session, "172.20.60.37")
    assert narrow is not None
    assert narrow.name_tr == "Depo Wi-Fi"
    wide = find_vlan(db_session, "172.20.60.200")
    assert wide is not None
    assert wide.vlan_id == 60
    wide.department_id = depot.id  # an administrator links staff Wi-Fi to a department
    db_session.flush()
    assert vlan_department(db_session, wide).department == "Depo"
    assert vlan_department(db_session, wide).source == "linked"
