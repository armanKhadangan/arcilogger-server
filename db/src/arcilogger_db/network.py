"""VLAN of an internal address and the VLAN's department — one rule for every consumer.

Spec 2.2: "Department of a device defaults to the VLAN's name"; spec 9.2: "Department of usage
= person's department if linked, else the VLAN's department". The administrator links
departments to VLANs (``vlans.department_id``); for every VLAN without a link — a staff Wi-Fi,
say — the department **is the VLAN's Turkish name**. The attribution chain (B3) and the usage
reports use :func:`vlan_department`, so they can never disagree.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from arcilogger_core.iputil import canonical_ip
from arcilogger_db.models import Department, Vlan


@dataclass(frozen=True)
class VlanDepartment:
    vlan_id: int | None
    vlan_name: str
    #: Linked department's name, else the VLAN's name (spec 2.2).
    department: str
    #: ``linked`` (vlans.department_id) or ``vlan_name`` (fallback).
    source: str


def find_vlan(session: Session, ip: str) -> Vlan | None:
    """The ``vlans`` row whose subnet contains ``ip`` (most specific first), else ``None``."""
    address = canonical_ip(ip)
    return session.scalars(
        select(Vlan)
        .where(Vlan.subnet.op(">>=")(func.inet(address)))
        .order_by(func.masklen(Vlan.subnet).desc())
        .limit(1)
    ).first()


def vlan_department(session: Session, vlan: Vlan) -> VlanDepartment:
    """Department of devices in ``vlan`` when no person is linked."""
    if vlan.department_id is not None:
        department = session.get(Department, vlan.department_id)
        if department is not None:
            return VlanDepartment(vlan.vlan_id, vlan.name_tr, department.name, "linked")
    return VlanDepartment(vlan.vlan_id, vlan.name_tr, vlan.name_tr, "vlan_name")


def department_for_ip(session: Session, ip: str) -> VlanDepartment | None:
    """:func:`vlan_department` of the VLAN containing ``ip``; ``None`` outside every VLAN."""
    vlan = find_vlan(session, ip)
    return None if vlan is None else vlan_department(session, vlan)
