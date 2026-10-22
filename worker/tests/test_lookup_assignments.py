"""The person step reads the assignment history (ADR-0008): the assignment valid at the moment
T names the person, a later change does not; without any history the current owner is used and
the outcome says so; a device unassigned at T names nobody; one person may hold several MACs."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from arcilogger_core.canonical import canonical_json
from arcilogger_db.assignments import Planner, apply, load_history, lock_devices
from arcilogger_db.models import Department, Device, DeviceAssignment, Person
from arcilogger_db.settings import get_settings
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import (
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    PersonInfo,
    attribute,
    run_lookup,
)
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
NOW = tr(DAY + timedelta(days=1), 9)
AT = tr(DAY, 11, 23, 41)
WAN = "100.64.12.9"
#: The example chain's PC (Planlama) and a phone on the staff Wi-Fi.
PC, PC_MAC = "172.20.42.57", "00:00:5e:00:53:57"
PHONE, PHONE_MAC = "172.20.60.142", "02:00:5e:00:53:64"


@dataclass
class People:
    selin: int
    mehmet: int


@pytest.fixture
def people(db_session: Session, evidence: Evidence) -> People:
    """Two clients with leases on DAY (as in the engine tests) and two persons, no links."""

    def department(name: str) -> int:
        return db_session.scalars(select(Department.id).where(Department.name == name)).one()

    selin = Person(name="Selin Kaya", department_id=department("Planlama"))
    mehmet = Person(name="Mehmet Demir", department_id=department("İdari İşler"))
    db_session.add_all([selin, mehmet])
    db_session.flush()
    evidence.dhcp(tr(DAY, 10, 42, 18), "assigned", PC, PC_MAC, server="dhcp42", hostname="PLN-PC14")
    evidence.dhcp(
        tr(DAY, 9, 37, 52), "assigned", PHONE, PHONE_MAC, server="dhcp60", hostname="Telefon-12"
    )
    evidence.traffic(AT, PC, 51001, "203.0.113.10", 443, duration=10)
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=NOW)
    db_session.commit()
    return People(selin=selin.id, mehmet=mehmet.id)


def assign(session: Session, mac: str, person_id: int | None, valid_from: datetime) -> None:
    owners = lock_devices(session, [mac])
    planner = Planner(load_history(session, [mac]), now=NOW, owners=owners)
    planner.assign(mac, person_id, valid_from)
    apply(session, planner.changes, assigned_by="test")
    session.commit()


def lookup(session: Session, **request: object) -> LookupOutcome:
    request.setdefault("at", AT)
    settings = LookupSettings.from_settings(get_settings(session))
    outcome = run_lookup(session, LookupRequest(**request), settings, now=NOW)  # type: ignore[arg-type]
    data = outcome.to_dict()
    assert canonical_json(data)
    assert LookupOutcome.from_dict(json.loads(json.dumps(data))) == outcome
    return outcome


def keys(outcome: LookupOutcome) -> list[str]:
    return [e.key for e in outcome.explanations]


def test_a_later_reassignment_does_not_change_the_person_at_t(
    db_session: Session, people: People
) -> None:
    since = tr(DAY - timedelta(days=10), 9)
    handover = tr(DAY, 20)  # after the requested moment
    assign(db_session, PC_MAC, people.selin, since)
    assign(db_session, PC_MAC, people.mehmet, handover)
    outcome = lookup(db_session, internal_ip=PC)
    chain = outcome.attribution
    assert chain is not None
    assert chain.person is not None
    assert (chain.person.name, chain.person.link) == ("Selin Kaya", "history")
    assert (chain.person.assigned_from, chain.person.assigned_to) == (since, handover)
    assert (chain.department, chain.department_source) == ("Planlama", "person")
    assert "person_link_at_lookup_time" not in keys(outcome)
    note = next(e for e in outcome.explanations if e.key == "person_department_current")
    assert "bugünkü departmanıdır" in note.text_tr
    # The stored outcome carries the three fields in its JSON.
    person = outcome.to_dict()["attribution"]["person"]
    assert person["link"] == "history"
    assert datetime.fromisoformat(person["assigned_from"]) == since
    assert datetime.fromisoformat(person["assigned_to"]) == handover
    # After the handover the new holder is named (the lease is still open then).
    later = attribute(db_session, PC, tr(DAY, 21))
    assert later is not None
    assert later.person is not None
    assert (later.person.name, later.person.assigned_to) == ("Mehmet Demir", None)
    assert later.department == "İdari İşler"


def test_exact_matches_use_the_history_too(db_session: Session, people: People) -> None:
    assign(db_session, PC_MAC, people.mehmet, tr(DAY, 7))
    assign(db_session, PC_MAC, people.selin, tr(DAY, 19))
    settings = LookupSettings.from_settings({**get_settings(db_session), "nat.wan_transip": WAN})
    outcome = run_lookup(
        db_session,
        LookupRequest(at=AT, public_ip=WAN, public_port=51001, protocol=6),
        settings,
        now=NOW,
    )
    assert outcome.mode == "exact"
    (match,) = outcome.matches
    assert match.chain is not None
    assert match.chain.person is not None
    assert match.chain.person.name == "Mehmet Demir"
    assert match.chain.person.link == "history"


def test_without_history_the_current_owner_is_named_and_flagged(
    db_session: Session, people: People
) -> None:
    device = db_session.get_one(Device, PC_MAC)
    device.person_id = people.selin  # a link written before the history existed
    db_session.commit()
    outcome = lookup(db_session, internal_ip=PC)
    assert outcome.attribution is not None
    person = outcome.attribution.person
    assert person is not None
    assert (person.link, person.assigned_from, person.assigned_to) == ("current", None, None)
    assert "person_link_at_lookup_time" in keys(outcome)
    assert "person_department_current" not in keys(outcome)
    note = next(e for e in outcome.explanations if e.key == "person_link_at_lookup_time")
    assert "atama geçmişi kaydı yoktur" in note.text_tr


def test_a_device_unassigned_at_t_names_nobody(db_session: Session, people: People) -> None:
    assign(db_session, PC_MAC, people.selin, tr(DAY, 19))  # only from after T
    outcome = lookup(db_session, internal_ip=PC)
    chain = outcome.attribution
    assert chain is not None
    assert chain.person is None  # never the owner of another time
    assert (chain.department, chain.department_source) == ("Planlama", "vlan")
    chain_keys = [e.key for e in chain.explanations]
    assert "person_unassigned_at_time" in chain_keys
    assert "no_person" not in chain_keys
    assert "person_link_at_lookup_time" not in keys(outcome)
    # Closed before T: also nobody.
    assign(db_session, PC_MAC, None, tr(DAY, 20))
    earlier = attribute(db_session, PC, tr(DAY, 11))
    assert earlier is not None
    assert earlier.person is None


def test_one_person_with_two_macs(db_session: Session, people: People) -> None:
    since = tr(DAY, 6)
    assign(db_session, PC_MAC, people.selin, since)
    assign(db_session, PHONE_MAC, people.selin, since)
    for ip in (PC, PHONE):
        chain = attribute(db_session, ip, AT)
        assert chain is not None
        assert chain.person is not None, ip
        assert (chain.person.person_id, chain.person.link) == (people.selin, "history")
    owned = db_session.scalars(
        select(Device.mac).where(Device.person_id == people.selin).order_by(Device.mac)
    ).all()
    assert owned == [PC_MAC, PHONE_MAC]  # MAC order


def test_a_backfilled_assignment_says_its_start_is_not_known(
    db_session: Session, people: People
) -> None:
    db_session.add(
        DeviceAssignment(
            mac=PC_MAC,
            person_id=people.selin,
            valid_from=tr(DAY, 10, 42, 18),
            assigned_by=":migration",
            backfilled=True,
        )
    )
    db_session.get_one(Device, PC_MAC).person_id = people.selin
    db_session.commit()
    chain = attribute(db_session, PC, AT)
    assert chain is not None
    assert chain.person is not None
    assert chain.person.link == "history"
    note = next(e for e in chain.explanations if e.key == "person_link_backfilled")
    assert "12.10.2026 10:42:18" in note.text_tr
    assert note.params == {"assigned_from": "2026-10-12T07:42:18+00:00"}  # machine form (UTC)


def test_stored_outcomes_without_the_new_fields_still_load() -> None:
    old = {"person_id": 3, "name": "Selin Kaya", "department": "Planlama", "title": None,
           "confidence": "medium", "evidence": []}  # fmt: skip
    person = PersonInfo.from_dict(old)
    assert (person.link, person.assigned_from, person.assigned_to) == ("current", None, None)
    new = PersonInfo(
        person_id=3,
        name="Selin Kaya",
        department=None,
        title=None,
        confidence="medium",
        assigned_from=tr(DAY, 9),
        assigned_to=None,
        link="history",
    )
    data = json.loads(json.dumps(_encode(new)))
    assert data["assigned_from"] == "2026-10-12T06:00:00+00:00"
    assert PersonInfo.from_dict(data) == new
    with pytest.raises(ValueError, match="timezone-aware"):
        PersonInfo(
            person_id=1,
            name="X",
            department=None,
            title=None,
            confidence="medium",
            assigned_from=datetime(2026, 10, 12, 9),  # noqa: DTZ001 - refused on purpose
        )


def _encode(person: PersonInfo) -> dict[str, object]:
    from arcilogger_worker.lookup.types import _encode as encode

    value = encode(person)
    assert isinstance(value, dict)
    return value
