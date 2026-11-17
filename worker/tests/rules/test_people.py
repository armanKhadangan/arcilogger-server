"""People rules R09, R13, R14 (W6-E, contract §2.2.4, §0.1 rule 5 KVKK, §3 T-R, T-A7).

The person comes from the existing attribution: a DHCP lease (rebuilt by the lease job) gives the
address a device, the assignment history gives the device a person. Rows are written through
:class:`arcilogger_worker.testing.Evidence` (raw spool line + typed row, as ingest does). Every
value is fictional (Örnek A.Ş., ``172.20.x``, ``00:00:5E:00:53:xx``, RFC 5737).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime, timedelta
from ipaddress import ip_network
from typing import Any
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import insert
from sqlalchemy.orm import Session

from arcilogger_core.layout import Layout
from arcilogger_core.timeutil import site_day
from arcilogger_db.assignments import Planner, apply, load_history, lock_devices
from arcilogger_db.models import FgDns, FgTraffic, FgWeb, Person
from arcilogger_worker.correlation.leases import rebuild_days
from arcilogger_worker.rules.model import Actions, Hit, RuleContext, RuleDef, RuleSettings
from arcilogger_worker.rules.people import EVIDENCE_LIMIT, GIB, MIB, RULES
from arcilogger_worker.rules.texts import COMMON_FIELDS, TEXTS, title, title_fields
from arcilogger_worker.testing import Evidence

from .cases import CASES, SLICE_END, CaseRow, RuleCase

ZONE = ZoneInfo("Europe/Istanbul")
DAY = date(2026, 10, 14)
#: Slice end: 14.10.2026 15:00 site time (a Wednesday afternoon).
END = datetime(2026, 10, 14, 12, 0, tzinfo=UTC)
START = END - timedelta(seconds=600)
PC1, PC1_MAC = "172.20.43.37", "00:00:5e:00:53:01"
PC2, PC2_MAC = "172.20.43.81", "00:00:5e:00:53:02"
#: An address no lease covers (nobody is attributed).
LOOSE = "172.20.41.77"
#: The fictional person's name: it must never reach a hit.
NAME = "Deneme Kişisi Örnek"
INTERNAL = ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")


def rule(rule_id: str) -> RuleDef:
    return next(entry for entry in RULES if entry.id == rule_id)


def settings_of(entry: RuleDef, params: Mapping[str, object] | None = None) -> RuleSettings:
    values: dict[str, object] = {p.key: p.default for p in entry.params}
    for key, value in (params or {}).items():
        if isinstance(value, list):
            value = tuple(
                ip_network(v) if entry_kind(entry, key) == "networks" else v for v in value
            )
        values[key] = value
    return RuleSettings(
        enabled=True,
        severity=entry.severity,
        window_seconds=entry.window_seconds,
        cooldown_seconds=entry.cooldown_seconds,
        actions=Actions(email=True, webhook=True),
        params=values,
    )


def entry_kind(entry: RuleDef, key: str) -> str:
    return next(p.kind for p in entry.params if p.key == key)


def run(
    session: Session,
    rule_id: str,
    params: Mapping[str, object] | None = None,
    *,
    end: datetime = END,
    start: datetime | None = None,
) -> dict[str, Hit]:
    entry = rule(rule_id)
    ctx = RuleContext(
        settings=settings_of(entry, params),
        start=start or end - timedelta(seconds=600),
        end=end,
        max_device_skew=timedelta(seconds=3600),
        internal_networks=tuple(ip_network(net) for net in INTERNAL),
        zone=ZONE,
    )
    hits = list(entry.evaluate(session, ctx))
    by_key = {hit.key: hit for hit in hits}
    assert len(by_key) == len(hits), "one hit per key"
    for hit in hits:
        missing = title_fields(TEXTS[rule_id]) - COMMON_FIELDS - set(hit.values)
        assert not missing, f"{rule_id}: the title needs {sorted(missing)}"
        turkish, english = title(rule_id, hit.key, hit.count, hit.values)
        assert NAME not in turkish + english
    return by_key


#: Lease renewals of both PCs: shortly before every time a test writes traffic, so no test
#: depends on the lease timeout.
RENEWALS = (
    datetime(2026, 10, 13, 20, 0, tzinfo=UTC),
    datetime(2026, 10, 13, 23, 0, tzinfo=UTC),
    datetime(2026, 10, 14, 6, 30, tzinfo=UTC),
    datetime(2026, 10, 14, 11, 0, tzinfo=UTC),
)


@pytest.fixture
def site(db_session: Session, layout: Layout) -> tuple[Evidence, int]:
    """PC-01 leased to a device assigned to the fictional person; PC-02 leased, nobody's."""
    e = Evidence(db_session, layout)
    for at in RENEWALS:
        e.dhcp(at, "assigned", PC1, PC1_MAC, hostname="PC-01")
        e.dhcp(at, "assigned", PC2, PC2_MAC, hostname="PC-02")
    db_session.commit()
    rebuild_days(db_session, DAY - timedelta(days=1), DAY, now=END + timedelta(hours=1))
    person = Person(name=NAME, title="Uzman")
    db_session.add(person)
    db_session.flush()
    owners = lock_devices(db_session, [PC1_MAC])
    planner = Planner(
        load_history(db_session, [PC1_MAC]), now=RENEWALS[0] + timedelta(minutes=1), owners=owners
    )
    planner.assign(PC1_MAC, int(person.id), RENEWALS[0])
    apply(db_session, planner.changes, assigned_by="test")
    db_session.commit()
    return e, int(person.id)


def blocked(e: Evidence, ip: str, count: int, newest: datetime, *, dns: bool = False) -> list[int]:
    ids = []
    for n in range(count):
        at = newest - timedelta(seconds=30 * (count - 1 - n))
        if dns:
            ids.append(
                e.dns(at, ip, "proxy.example", [], catdesc="Proxy Avoidance", action="blocked")
            )
        else:
            ids.append(e.web(at, ip, 5000 + n, "proxy.example", "198.51.100.20", action="blocked"))
    e.session.commit()
    return ids


def no_name(hit: Hit) -> None:
    """KVKK: the hit names the person by id only (values, evidence, key)."""
    assert NAME not in repr(hit)
    assert "name" not in {str(key) for key in hit.values}


# =================================================================================================
# Registry
# =================================================================================================


def test_people_rules_registered() -> None:
    assert [entry.id for entry in RULES] == ["R09", "R13", "R14"]
    assert all(entry.person_level for entry in RULES)
    assert {entry.id: entry.severity for entry in RULES} == {
        "R09": "low",
        "R13": "medium",
        "R14": "low",
    }
    assert not rule("R09").needs_setup(settings_of(rule("R09")))
    assert not rule("R13").needs_setup(settings_of(rule("R13")))
    assert rule("R14").needs_setup(settings_of(rule("R14"))), "§2.2.4: R14 needs user_networks"
    assert not rule("R14").needs_setup(
        settings_of(rule("R14"), {"user_networks": ["172.20.43.0/24"]})
    )


# =================================================================================================
# R09 — Engelli kategori ısrarı
# =================================================================================================


def test_r09_person_key(site: tuple[Evidence, int], db_session: Session) -> None:
    e, person = site
    ids = blocked(e, PC1, 20, END - timedelta(seconds=5))
    hits = run(db_session, "R09")
    assert set(hits) == {f"person:{person}"}
    hit = hits[f"person:{person}"]
    assert hit.person_id == person
    assert hit.count == 20
    assert hit.values["srcip"] == PC1
    assert [ref.row_id for ref in hit.evidence] == ids
    assert all(ref.table == "fg_web" for ref in hit.evidence)
    no_name(hit)


def test_r09_just_below_the_threshold(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    blocked(e, PC1, 19, END - timedelta(seconds=5))
    assert run(db_session, "R09") == {}


def test_r09_web_and_dns_add_up(site: tuple[Evidence, int], db_session: Session) -> None:
    e, person = site
    blocked(e, PC1, 10, END - timedelta(seconds=300))
    blocked(e, PC1, 10, END - timedelta(seconds=5), dns=True)
    hit = run(db_session, "R09")[f"person:{person}"]
    assert hit.count == 20
    assert {ref.table for ref in hit.evidence} == {"fg_web", "fg_dns"}


def test_r09_nobody_attributed(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    blocked(e, PC2, 20, END - timedelta(seconds=5))  # a lease, a device, nobody assigned
    blocked(e, LOOSE, 20, END - timedelta(seconds=5))  # no lease at all
    hits = run(db_session, "R09")
    assert set(hits) == {f"ip:{PC2}", f"ip:{LOOSE}"}
    assert all(hit.person_id is None for hit in hits.values())


def test_r09_needs_a_row_in_the_slice(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    blocked(e, PC1, 25, START - timedelta(seconds=1))
    assert run(db_session, "R09") == {}


def test_r09_window_and_actions(site: tuple[Evidence, int], db_session: Session) -> None:
    e, person = site
    blocked(e, PC1, 1, END - timedelta(seconds=3601))  # older than the window
    blocked(e, PC1, 19, END - timedelta(seconds=5))
    assert run(db_session, "R09") == {}
    for n in range(5):
        e.web(
            END - timedelta(seconds=2 + n),
            PC1,
            6000 + n,
            "x.example",
            "198.51.100.21",
            action="passthrough",
        )
    e.session.commit()
    assert run(db_session, "R09") == {}
    hits = run(db_session, "R09", {"blocked_actions": ["blocked", "passthrough"]})
    assert hits[f"person:{person}"].count == 24


# =================================================================================================
# R13 — Büyük veri gönderimi
# =================================================================================================


def upload(
    e: Evidence, ip: str, at: datetime, total: int, *, port: int, interim: int | None = None
) -> None:
    """One session: optionally an interim log with ``interim`` bytes sent, then the final log
    with ``total`` (cumulative)."""
    if interim is not None:
        e.traffic(
            at - timedelta(seconds=120),
            ip,
            port,
            sessionid=port,
            interim=True,
            sentbyte=interim,
            sentdelta=interim,
            rcvdbyte=1000,
            rcvddelta=1000,
            durationdelta=60,
        )
    e.traffic(at, ip, port, sessionid=port, sentbyte=total, rcvdbyte=2000, duration=300)
    e.session.commit()


def test_r13_hour(site: tuple[Evidence, int], db_session: Session) -> None:
    e, person = site
    upload(e, PC1, END - timedelta(seconds=10), 1100 * MIB, port=50001, interim=600 * MIB)
    hits = run(db_session, "R13")
    hit = hits[f"person:{person}"]
    assert hit.values["bytes"] == 1100 * MIB, "interim and final logs are not counted twice"
    assert hit.values["mib"] == 1100.0
    assert hit.values["period"] == "hour"
    assert hit.values["srcip"] == PC1
    assert hit.person_id == person
    assert len(hit.evidence) == 2
    no_name(hit)


def test_r13_just_below(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    upload(e, PC1, END - timedelta(seconds=10), 1024 * MIB, port=50001, interim=600 * MIB)
    assert run(db_session, "R13") == {}, "the limit is 'more than' 1 GiB"


def test_r13_day(site: tuple[Evidence, int], db_session: Session) -> None:
    e, person = site
    # 4.5 GiB in the morning (site day), 0.6 GiB now: the hour stays below 1 GiB.
    upload(e, PC1, END - timedelta(hours=5), 4608 * MIB, port=50002)
    upload(e, PC1, END - timedelta(seconds=10), 600 * MIB, port=50003)
    hit = run(db_session, "R13")[f"person:{person}"]
    assert hit.values["period"] == "day"
    assert hit.values["bytes"] == 5208 * MIB
    assert hit.values["hour_bytes"] == 600 * MIB


def test_r13_yesterday_does_not_count(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    # 21:00 site time the day before: another site day.
    upload(e, PC1, datetime(2026, 10, 13, 18, 0, tzinfo=UTC), 4608 * MIB, port=50002)
    upload(e, PC1, END - timedelta(seconds=10), 600 * MIB, port=50003)
    assert run(db_session, "R13") == {}


def test_r13_refused_and_external(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    e.traffic(END - timedelta(seconds=10), PC1, 50004, sessionid=4, action="deny", sentbyte=2 * GIB)
    e.traffic(END - timedelta(seconds=10), "198.51.100.30", 50005, sessionid=5, sentbyte=2 * GIB)
    e.session.commit()
    assert run(db_session, "R13") == {}


def test_r13_nobody_attributed(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    upload(e, LOOSE, END - timedelta(seconds=10), 2 * GIB, port=50006)
    hits = run(db_session, "R13")
    assert set(hits) == {f"ip:{LOOSE}"}
    assert hits[f"ip:{LOOSE}"].person_id is None


# =================================================================================================
# R14 — Gece etkinliği
# =================================================================================================

NIGHT_END = datetime(2026, 10, 14, 0, 0, tzinfo=UTC)  # 03:00 site time
USERS = {"user_networks": ["172.20.43.0/24"]}


def night_traffic(e: Evidence, ip: str, at: datetime, total: int, port: int) -> None:
    e.traffic(
        at, ip, port, sessionid=port, sentbyte=total // 2, rcvdbyte=total - total // 2, duration=60
    )
    e.session.commit()


def test_r14_night(site: tuple[Evidence, int], db_session: Session) -> None:
    e, person = site
    night_traffic(e, PC1, NIGHT_END - timedelta(minutes=50), 6 * MIB, 50010)
    night_traffic(e, PC1, NIGHT_END - timedelta(seconds=10), 5 * MIB, 50011)
    assert run(db_session, "R14", end=NIGHT_END) == {}, "needs setup: no user networks"
    hits = run(db_session, "R14", USERS, end=NIGHT_END)
    hit = hits[f"person:{person}"]
    assert hit.values["bytes"] == 11 * MIB
    assert hit.values["night"] == "2026-10-14"
    assert hit.person_id == person
    no_name(hit)


def test_r14_just_below(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    night_traffic(e, PC1, NIGHT_END - timedelta(minutes=50), 5 * MIB, 50010)
    night_traffic(e, PC1, NIGHT_END - timedelta(seconds=10), 5 * MIB - 1, 50011)
    assert run(db_session, "R14", USERS, end=NIGHT_END) == {}


def test_r14_day_and_other_networks(site: tuple[Evidence, int], db_session: Session) -> None:
    e, _ = site
    night_traffic(e, PC1, END - timedelta(seconds=10), 50 * MIB, 50012)  # 15:00 site time
    night_traffic(e, LOOSE, NIGHT_END - timedelta(seconds=10), 50 * MIB, 50013)  # not a user net
    assert run(db_session, "R14", USERS) == {}
    assert run(db_session, "R14", USERS, end=NIGHT_END) == {}


def test_r14_night_across_midnight(site: tuple[Evidence, int], db_session: Session) -> None:
    e, person = site
    late = datetime(2026, 10, 13, 20, 30, tzinfo=UTC)  # 23:30 site time
    night_traffic(e, PC1, late, 8 * MIB, 50014)
    night_traffic(e, PC1, late + timedelta(minutes=40), 4 * MIB, 50015)  # 00:10
    params = {**USERS, "night_start": 22 * 3600, "night_end": 6 * 3600}
    hits = run(db_session, "R14", params, end=late + timedelta(minutes=45))
    assert hits[f"person:{person}"].values["bytes"] == 12 * MIB
    assert hits[f"person:{person}"].values["night"] == "2026-10-13"


# =================================================================================================
# T-A7 — bound parameters
# =================================================================================================

HOSTILE = ["x'); DELETE FROM persons; --", "%", "_", "a;b", '"', "\\"]


def test_list_parameters_are_bound(site: tuple[Evidence, int], db_session: Session) -> None:
    e, person = site
    blocked(e, PC1, 25, END - timedelta(seconds=5))
    assert run(db_session, "R09", {"blocked_actions": HOSTILE}) == {}
    assert set(run(db_session, "R09", {"blocked_actions": [*HOSTILE, "blocked"]})) == {
        f"person:{person}"
    }
    assert db_session.get(Person, person) is not None


# =================================================================================================
# The W6-E block of cases.py, run here until the engine tests run every case
# =================================================================================================

W6E_CASES = [case for case in CASES if case.rule in {entry.id for entry in RULES}]
FIREWALL = "172.31.255.1"
MODELS: Mapping[str, Any] = {"fg_traffic": FgTraffic, "fg_web": FgWeb, "fg_dns": FgDns}


def write_case_rows(session: Session, rows: Sequence[CaseRow]) -> None:
    """The case rows with the common columns (source, sender, raw reference) filled in."""
    line = 0
    for row in rows:
        for n in range(row.repeat):
            line += 1
            received = SLICE_END + row.received + timedelta(seconds=n)
            session.execute(
                insert(MODELS[row.table]).values(
                    time=received + row.device_offset,
                    time_received=received,
                    source="fortigate",
                    sender=FIREWALL,
                    raw_day=site_day(received),
                    raw_line=line,
                    raw_offset=line * 1000,
                    **row.values,
                )
            )
    session.commit()


@pytest.mark.parametrize("case", W6E_CASES, ids=lambda case: f"{case.rule}-{case.name}")
def test_w6e_cases(db_session: Session, case: RuleCase) -> None:
    write_case_rows(db_session, case.rows)
    params = case.overrides.get("params", {})
    assert isinstance(params, Mapping)
    hits = run(
        db_session,
        case.rule,
        params,
        end=SLICE_END,
        start=SLICE_END - timedelta(seconds=case.slice_seconds or 600),
    )
    assert set(hits) == {expected.key for expected in case.expected}
    for expected in case.expected:
        hit = hits[expected.key]
        assert hit.count == expected.count
        for name, value in expected.values.items():
            assert hit.values[name] == value, name
        if expected.evidence is not None:
            assert len(hit.evidence) == expected.evidence


def test_every_people_rule_has_positive_and_negative_cases() -> None:
    for entry in RULES:
        cases = [case for case in W6E_CASES if case.rule == entry.id]
        assert any(case.positive for case in cases), entry.id
        assert any(not case.positive for case in cases), entry.id


def test_evidence_limit_is_the_largest_setting() -> None:
    assert EVIDENCE_LIMIT == 100
