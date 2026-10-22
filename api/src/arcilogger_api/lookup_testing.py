"""Test helpers for the 5651 lookup, report and raw-line endpoints (and later packages: the
evidence API, end-to-end tests, demo data).

* :func:`build_site` — two clients and FortiGate sessions around 12.10.2026 11:23:41 (fictional
  values; the example attribution chain WAN 100.64.12.9:51001 → 172.20.42.57 → "Planlama-PC-14"
  → Selin Kaya / Planlama), written as real spool lines **and** database rows by
  :class:`arcilogger_worker.testing.Evidence`, the lease timeline rebuilt by the worker job, one
  device linked to a person;
* :func:`accept_privacy` — the KVKK notice accepted for a user (person-level endpoints refuse
  without it);
* :func:`seal_day` — seals a day with the core sealer and the mock time-stamp authority (never
  legally valid), so tests can read raw lines from the vault.

**Test support only**: the API service never imports it (it writes spool files and seals with
the mock TSA). It lives in the package, like :mod:`arcilogger_api.testing`, so that the tests of
other packages can build the same site; leaving the test-support modules out of the production
wheel is a build setting of ``api/pyproject.toml``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from arcilogger_api.auth.accounts import accept_privacy as _accept
from arcilogger_api.auth.accounts import privacy_version
from arcilogger_core.config import Config
from arcilogger_core.layout import Layout
from arcilogger_core.seal import Sealer
from arcilogger_core.timeutil import day_window
from arcilogger_core.tsa.mock import MockTsa, MockTsaProvider
from arcilogger_db.models import Department, Device, Person, Source
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.testing import FIREWALL, ROUTER, Evidence, tr

DAY = date(2026, 10, 12)
#: Moment of the requests (the PC's session: srcport 51001 → transport 51001).
AT = tr(DAY, 11, 23, 41)
NOW = tr(DAY + timedelta(days=1), 9)
WAN = "100.64.12.9"
#: The example chain's PC (VLAN 42 "Planlama", lease of dhcp42 at 10:42:18) and a phone on
#: the staff Wi-Fi.
PC, PC_MAC = "172.20.42.57", "00:00:5e:00:53:57"
PC_HOSTNAME = "PLN-PC14"
PHONE, PHONE_MAC = "172.20.60.142", "02:00:5e:00:53:64"
PERSON_NAME = "Selin Kaya"
DEPARTMENT = "Planlama"
DEVICE_NAME = "Planlama-PC-14"


@dataclass(frozen=True)
class Site:
    person_id: int
    #: Traffic row ids: s1 = PC → 203.0.113.10:443 at AT (10 s), s2 = phone's long session
    #: (two interim rows and the final row), s3 = PC → 203.0.113.40:443 at 11:24:36.
    s1: int
    s2: tuple[int, ...]
    s3: int
    dhcp_pc: int
    dhcp_phone: int


def build_site(factory: sessionmaker[Session], layout: Layout, day: date = DAY) -> Site:
    """Write the sample network day (spool + rows, the two sources), rebuild its leases, link
    the PC to a person."""
    at = tr(day, 11, 23, 41)
    with factory() as session:
        # The ingest service keeps ``sources`` up to date; the raw-line view checks names there.
        session.merge(Source(name="fortigate", type="fortigate", address=FIREWALL))
        session.merge(Source(name="core-r1", type="mikrotik", address=ROUTER))
        evidence = Evidence(session, layout)
        department = session.scalars(select(Department).where(Department.name == DEPARTMENT)).one()
        person = Person(name=PERSON_NAME, department_id=department.id, title="Planlama Uzmanı")
        session.add(person)
        session.flush()
        dhcp_pc = evidence.dhcp(
            tr(day, 10, 42, 18), "assigned", PC, PC_MAC, server="dhcp42", hostname=PC_HOSTNAME
        )
        dhcp_phone = evidence.dhcp(
            tr(day, 9, 37, 52), "assigned", PHONE, PHONE_MAC, server="dhcp60", hostname="Telefon-12"
        )
        s1 = evidence.traffic(at, PC, 51001, "203.0.113.10", 443, duration=10)
        google = "203.0.113.20"
        s2 = (
            evidence.traffic(tr(day, 11, 20, 56), PHONE, 51002, google, duration=100, interim=True),
            evidence.traffic(tr(day, 11, 22, 56), PHONE, 51002, google, duration=220, interim=True),
            evidence.traffic(tr(day, 11, 25, 26), PHONE, 51002, google, duration=370),
        )
        s3 = evidence.traffic(tr(day, 11, 24, 36), PC, 51005, "203.0.113.40", 443, duration=5)
        evidence.traffic(tr(day, 11, 34, 56), PC, 62099, "203.0.113.40", 443, duration=5)
        evidence.web(tr(day, 11, 24, 32), PC, 51005, "static.portal.example", "203.0.113.40")
        evidence.dns(tr(day, 11, 14, 56), PHONE, "www.google.com", [google])
        session.commit()
        rebuild_day(session, day, horizon=tr(day + timedelta(days=1), 9))
        device = session.get_one(Device, PC_MAC)
        device.person_id = person.id
        device.name = DEVICE_NAME
        device.type = "pc"
        session.commit()
        return Site(
            person_id=person.id, s1=s1, s2=s2, s3=s3, dhcp_pc=dhcp_pc, dhcp_phone=dhcp_phone
        )


def accept_privacy(factory: sessionmaker[Session], user_id: int) -> None:
    """The user accepted the current KVKK notice version."""
    with factory() as session:
        _accept(session, user_id, privacy_version(session), ip=None)
        session.commit()


def mock_tsa(layout: Layout) -> MockTsa:
    """The mock TSA of ``layout`` (EC keys: created fast; the API's provider reuses them)."""
    return MockTsa(layout.state / "mock-tsa", key_type="ec")


def seal_day(config: Config, layout: Layout, day: date) -> None:
    """Seal ``day`` with the mock TSA (as the nightly task would, one hour after midnight)."""
    sealing = Config.model_validate(
        {
            **config.model_dump(),
            "seal": {"quiet_seconds": 0, "first_day": day.isoformat()},
            "tsa": {"provider": "mock"},
        }
    )
    tsa = mock_tsa(layout)
    sealer = Sealer(
        sealing,
        provider=MockTsaProvider(tsa),
        roots=[tsa.root_certificate],
        clock=lambda: day_window(day)[1] + timedelta(hours=1),
        clock_status=lambda: None,
    )
    outcome = sealer.seal_day(day)
    if outcome.status != "sealed":
        raise RuntimeError(f"sealing {day} failed: {outcome.message}")


def iso(moment: datetime) -> str:
    """ISO-8601 with offset, as a client sends it."""
    return moment.isoformat()
