"""Test helpers for the devices and persons endpoints (and the sample capture of the web app).

Devices are normally written by the correlation worker and persons by the API; tests insert
them directly. **Test support only**, like :mod:`arcilogger_api.lookup_testing`: the service never
imports it.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_db.models import Department, Device, Person


def department_id(session: Session, name: str) -> int:
    """Id of the (seeded) department ``name``."""
    return session.scalars(select(Department.id).where(Department.name == name)).one()


def add_device(
    session: Session,
    mac: str,
    *,
    last_ip: str | None = None,
    last_vlan: int | None = None,
    hostnames: Sequence[str] = (),
    name: str | None = None,
    first_seen: datetime | None = None,
    last_seen: datetime | None = None,
    randomized: bool = False,
    infrastructure: bool = False,
    device_type: str | None = None,
    vendor: str | None = None,
) -> Device:
    """A device as the worker would have seen it (flushed, not committed)."""
    device = Device(
        mac=mac,
        last_ip=last_ip,
        last_vlan=last_vlan,
        hostnames=list(hostnames),
        name=name,
        first_seen=first_seen,
        last_seen=last_seen,
        randomized=randomized,
        infrastructure=infrastructure,
        type=device_type,
        vendor=vendor,
    )
    session.add(device)
    session.flush()
    return device


def add_person(
    session: Session,
    name: str,
    *,
    department: str | None = None,
    employee_no: str | None = None,
    active: bool = True,
    email: str | None = None,
    title: str | None = None,
) -> Person:
    """A person (flushed, not committed); ``department`` is a department name."""
    person = Person(
        name=name,
        department_id=None if department is None else department_id(session, department),
        employee_no=employee_no,
        active=active,
        email=email,
        title=title,
    )
    session.add(person)
    session.flush()
    return person
