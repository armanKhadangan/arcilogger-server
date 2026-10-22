"""5651 lookup engine against PostgreSQL (spec 9.3-9.5, 17): exact, double-NAT candidates (with
and without destination, interim + final rows grouped), internal IP and MAC lookups, none,
truncation, the data window, IP conflicts and the raw lines behind every chain."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from arcilogger_core.canonical import canonical_json
from arcilogger_core.layout import Layout
from arcilogger_db.models import Department, Device, Person
from arcilogger_db.settings import DEFAULT_SETTINGS, get_settings, set_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.correlation.watermark import set_watermark
from arcilogger_worker.lookup import (
    LookupOutcome,
    LookupRequest,
    LookupSettings,
    attribute,
    run_lookup,
)
from arcilogger_worker.testing import Evidence, tr

DAY = date(2026, 10, 12)
NOW = tr(DAY + timedelta(days=1), 9)
AT = tr(DAY, 11, 23, 41)
WAN = "100.64.12.9"
#: The example chain: a Planlama PC that holds the requested NAT port, its person.
PC, PC_MAC = "172.20.42.57", "00:00:5e:00:53:57"
PHONE, PHONE_MAC = "172.20.60.142", "02:00:5e:00:53:64"


@dataclass
class Site:
    person_id: int
    #: Row ids of the traffic rows.
    s1: int
    s2: list[int]
    s3: int


@pytest.fixture
def site(db_session: Session, evidence: Evidence) -> Site:
    """Two clients and three sessions around 11:23:41 (fictional values)."""
    planning = db_session.scalars(select(Department).where(Department.name == "Planlama")).one()
    person = Person(name="Selin Kaya", department_id=planning.id, title="Planlama Uzmanı")
    db_session.add(person)
    db_session.flush()
    evidence.dhcp(tr(DAY, 10, 42, 18), "assigned", PC, PC_MAC, server="dhcp42", hostname="PLN-PC14")
    evidence.dhcp(
        tr(DAY, 9, 37, 52), "assigned", PHONE, PHONE_MAC, server="dhcp60", hostname="Telefon-12"
    )
    s1 = evidence.traffic(AT, PC, 51001, "203.0.113.10", 443, duration=10)
    google = "203.0.113.20"
    s2 = [  # a long session: two interim updates (cumulative duration) and the final log
        evidence.traffic(tr(DAY, 11, 20, 56), PHONE, 51002, google, duration=100, interim=True),
        evidence.traffic(tr(DAY, 11, 22, 56), PHONE, 51002, google, duration=220, interim=True),
        evidence.traffic(tr(DAY, 11, 25, 26), PHONE, 51002, google, duration=370),
    ]
    s3 = evidence.traffic(tr(DAY, 11, 24, 36), PC, 51005, "203.0.113.40", 443, duration=5)
    evidence.traffic(tr(DAY, 11, 34, 56), PC, 62099, "203.0.113.40", 443, duration=5)  # outside
    evidence.traffic(AT, PC, 5353, "172.20.10.1", 53, transip=None, proto=17)  # no NAT
    evidence.web(tr(DAY, 11, 24, 32), PC, 51005, "static.portal.example", "203.0.113.40")
    evidence.dns(tr(DAY, 11, 14, 56), PHONE, "www.google.com", ["203.0.113.20"])
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=NOW)
    device = db_session.get(Device, PC_MAC)
    assert device is not None
    device.person_id = person.id
    device.name = "Planlama-PC-14"
    db_session.commit()
    return Site(person_id=person.id, s1=s1, s2=s2, s3=s3)


def settings_for(session: Session, **overrides: Any) -> LookupSettings:
    values = get_settings(session)
    values.update(overrides)
    return LookupSettings.from_settings(values)


def lookup(
    session: Session, *, settings: LookupSettings | None = None, **request: Any
) -> LookupOutcome:
    request.setdefault("at", AT)
    outcome = run_lookup(
        session, LookupRequest(**request), settings or settings_for(session), now=NOW
    )
    # Stored as JSON (lookups.outcome): no floats, round trip exact.
    data = outcome.to_dict()
    assert canonical_json(data)
    assert LookupOutcome.from_dict(json.loads(json.dumps(data))) == outcome
    return outcome


def keys(outcome: LookupOutcome) -> list[str]:
    return [e.key for e in outcome.explanations]


def test_exact_match_with_the_fortigate_wan_address(db_session: Session, site: Site) -> None:
    settings = settings_for(db_session, **{"nat.wan_transip": WAN})
    outcome = lookup(
        db_session, settings=settings, public_ip=WAN, public_port=51001, protocol="tcp"
    )
    assert outcome.mode == "exact"
    assert "exact_nat" in keys(outcome)
    (match,) = outcome.matches
    assert (match.kind, match.score, match.time_distance_ms) == ("exact", 100, 0)
    assert match.destination_match == "not_requested"
    traffic = match.traffic
    assert (traffic.srcip, traffic.srcport, traffic.transip, traffic.transport) == (
        PC,
        51001,
        WAN,
        51001,
    )
    assert (traffic.start, traffic.end) == (AT - timedelta(seconds=10), AT)
    chain = match.chain
    assert chain is not None
    assert chain.lease is not None
    assert chain.person is not None
    assert (chain.lease.mac, chain.lease.hostname, chain.lease.confidence) == (
        PC_MAC,
        "PLN-PC14",
        "high",
    )
    assert (chain.person.name, chain.person.department) == ("Selin Kaya", "Planlama")
    assert (chain.department, chain.department_source) == ("Planlama", "person")
    assert (chain.vlan, chain.vlan_name) == (42, "Planlama")
    assert chain.device is not None
    assert chain.device.name == "Planlama-PC-14"
    assert chain.confidence == "medium"  # the person link is administrative
    assert outcome.data_window is not None
    assert outcome.data_window.in_database


def test_exact_lookup_finds_nothing_on_another_port(db_session: Session, site: Site) -> None:
    # The configured public address is the transip the FortiGate logs show (bridge mode).
    settings = settings_for(db_session, **{"nat.public_ips": [WAN]})
    outcome = lookup(db_session, settings=settings, public_ip=WAN, public_port=40001)
    assert outcome.mode == "none"
    assert outcome.matches == ()
    assert {"no_match", "no_match_exact"} <= set(keys(outcome))
    assert "public_ip_not_transip" not in keys(outcome)
    assert outcome.double_nat is False


def _port_reuse(evidence: Evidence, db_session: Session) -> None:
    """The phone's session reuses the PC's NAT port 51001 15 s later, to another destination."""
    evidence.traffic(
        AT + timedelta(seconds=15), PHONE, 51000, "198.51.100.120", 443, transport=51001
    )
    db_session.commit()


def test_exact_lookup_lists_only_sessions_to_the_requested_destination(
    db_session: Session, evidence: Evidence, site: Site
) -> None:
    """NAT port reuse inside the window: the session to another destination is not an exact
    match of the request (spec 3.3), only counted."""
    _port_reuse(evidence, db_session)
    settings = settings_for(db_session, **{"nat.wan_transip": WAN})
    asked = {"public_ip": WAN, "public_port": 51001}
    outcome = lookup(db_session, settings=settings, dest_ip="203.0.113.10", dest_port=443, **asked)
    assert outcome.mode == "exact"
    (match,) = outcome.matches
    assert (match.kind, match.score, match.destination_match) == ("exact", 100, "ip_port")
    assert match.traffic.srcip == PC
    assert "multiple_exact" not in keys(outcome)
    (note,) = [e for e in outcome.explanations if e.key == "exact_other_destinations"]
    assert note.params == {"address": WAN, "port": 51001, "count": 1}
    stored = json.dumps(outcome.to_dict())
    assert PHONE not in stored
    assert PHONE_MAC not in stored
    assert "198.51.100.120" not in stored

    assert "ayrıca 1 oturumda" in note.text_tr
    assert "talepteki hedeften farklı" in note.text_tr

    # No session goes to the requested address: nothing is listed, and the text does not
    # say "also" (no session was kept).
    elsewhere = lookup(db_session, settings=settings, dest_ip="203.0.113.9", **asked)
    assert (elsewhere.mode, elsewhere.matches) == ("none", ())
    assert {"no_match", "destination_not_found", "exact_only_other_destinations"} <= set(
        keys(elsewhere)
    )
    assert "exact_other_destinations" not in keys(elsewhere)
    only = next(e for e in elsewhere.explanations if e.key == "exact_only_other_destinations")
    assert only.params == {"address": WAN, "port": 51001, "count": 2}
    assert "ayrıca" not in only.text_tr
    assert " de " not in only.text_tr
    assert "exact_nat" not in keys(elsewhere)
    assert PHONE not in json.dumps(elsewhere.to_dict())
    assert PC not in json.dumps(elsewhere.to_dict())

    # A destination port nobody used on that NAT port: nothing either.
    by_port = lookup(db_session, settings=settings, dest_port=8443, **asked)
    assert (by_port.mode, by_port.matches) == ("none", ())
    assert "destination_not_found" in keys(by_port)

    # Without a destination every session on the NAT port is listed, as documented.
    everyone = lookup(db_session, settings=settings, **asked)
    assert everyone.mode == "exact"
    assert {m.traffic.srcip for m in everyone.matches} == {PC, PHONE}
    assert "multiple_exact" in keys(everyone)
    assert "exact_other_destinations" not in keys(everyone)


def test_exact_domain_only_request_without_dns_logs_keeps_the_session_unverified(
    db_session: Session, site: Site
) -> None:
    """Domain-only request, no FortiGate DNS/web/app log for the name (resolved by another
    DNS server, DoH, a cached answer): the NAT port still identifies the session. A missing
    log is not another destination, so the answer is never a false "no session"."""
    settings = settings_for(db_session, **{"nat.wan_transip": WAN})
    asked = {"public_ip": WAN, "public_port": 51001, "dest_domain": "akamai.example"}
    outcome = lookup(db_session, settings=settings, **asked)
    assert outcome.mode == "exact"
    (match,) = outcome.matches
    assert (match.kind, match.destination_match, match.traffic.srcip) == (
        "exact",
        "domain_unverified",
        PC,
    )
    assert {"exact_domain_unverified", "exact_nat"} <= set(keys(outcome))
    assert not {"no_match", "destination_not_found", "exact_other_destinations"} & set(
        keys(outcome)
    )
    note = next(e for e in outcome.explanations if e.key == "exact_domain_unverified")
    assert note.params == {"domain": "akamai.example"}
    assert "Hedef doğrulanmadı" in note.text_tr
    # The requested port is a fact the log shows: a session to another port is not kept.
    other_port = lookup(db_session, settings=settings, dest_port=8443, **asked)
    assert (other_port.mode, other_port.matches) == ("none", ())
    assert {"destination_not_found", "exact_only_other_destinations"} <= set(keys(other_port))
    same_port = lookup(db_session, settings=settings, dest_port=443, **asked)
    assert [m.destination_match for m in same_port.matches] == ["domain_unverified"]


def test_exact_domain_only_request_lists_only_the_sessions_tied_to_the_name(
    db_session: Session, evidence: Evidence, site: Site
) -> None:
    """Port reuse: the PC resolved the name to its destination, the phone's session on the same
    NAT port has no log for the name. Only the PC is listed, the phone is counted neutrally."""
    _port_reuse(evidence, db_session)
    evidence.dns(tr(DAY, 11, 22, 56), PC, "akamai.example", ["203.0.113.10"])
    db_session.commit()
    settings = settings_for(db_session, **{"nat.wan_transip": WAN})
    outcome = lookup(
        db_session,
        settings=settings,
        public_ip=WAN,
        public_port=51001,
        dest_domain="akamai.example",
    )
    assert outcome.mode == "exact"
    (match,) = outcome.matches
    assert (match.destination_match, match.traffic.srcip) == ("domain", PC)
    assert "exact_domain_unverified" not in keys(outcome)
    note = next(e for e in outcome.explanations if e.key == "exact_unverified_destinations")
    assert note.params == {"address": WAN, "port": 51001, "count": 1, "domain": "akamai.example"}
    assert "gösterilemedi" in note.text_tr
    assert "gitmedi" not in note.text_tr  # no claim that it went elsewhere
    stored = json.dumps(outcome.to_dict())
    assert PHONE not in stored
    assert "198.51.100.120" not in stored


def test_public_ip_that_is_not_the_transip_falls_back_to_candidates(
    db_session: Session, site: Site
) -> None:
    """nat.public_ips set while the ISP router still translates (or for a moment before the
    switch to bridge mode): the logs show another transip, so the answer is a double-NAT
    candidate search instead of a false "the FortiGate logged no session"."""
    settings = settings_for(db_session, **{"nat.public_ips": ["203.0.113.200"]})
    asked = {"public_ip": "203.0.113.200", "dest_ip": "203.0.113.10", "dest_port": 443}
    outcome = lookup(db_session, settings=settings, public_port=45123, **asked)
    assert outcome.mode == "candidates"
    (match,) = outcome.matches
    assert (match.kind, match.traffic.srcport) == ("candidate", 51001)
    assert {"public_ip_not_transip", "double_nat"} <= set(keys(outcome))
    assert "no_match_exact" not in keys(outcome)
    assert outcome.double_nat is True
    note = next(e for e in outcome.explanations if e.key == "public_ip_not_transip")
    assert note.params == {"address": "203.0.113.200"}
    assert "çift NAT" in note.text_tr
    without_port = lookup(db_session, settings=settings, **asked)
    assert without_port.mode == "candidates"
    assert "port_missing" not in keys(without_port)
    assert "public_ip_not_transip" in keys(without_port)
    # A wrong nat.wan_transip does not narrow the search to an address nobody used either; it
    # has its own explanation (no exact match was possible for this request anyway).
    wrong_wan = settings_for(db_session, **{"nat.wan_transip": "10.9.9.9"})
    found = lookup(
        db_session,
        settings=wrong_wan,
        public_ip="192.0.2.44",
        dest_ip="203.0.113.10",
        dest_port=443,
    )
    assert found.mode == "candidates"
    assert "public_ip_not_transip" not in keys(found)
    wan_note = next(e for e in found.explanations if e.key == "wan_transip_not_seen")
    assert wan_note.params == {"address": "10.9.9.9"}
    assert "kesin eşleşme yerine" not in wan_note.text_tr
    assert "tüm kaynak NAT oturumlarında" in wan_note.text_tr
    assert "double_nat" in keys(found)


def test_another_configured_public_ip_is_not_double_nat(
    db_session: Session, evidence: Evidence, site: Site
) -> None:
    """Dual WAN / IP pool: around the moment the logs show only the FortiGate's other
    configured public address. That is not double NAT: the answer for the requested address
    is "no session", never the other address's sessions as candidates."""
    settings = settings_for(db_session, **{"nat.public_ips": [WAN, "203.0.113.200"]})
    outcome = lookup(
        db_session,
        settings=settings,
        public_ip="203.0.113.200",
        public_port=51001,
        dest_ip="203.0.113.10",
        dest_port=443,
    )
    assert (outcome.mode, outcome.matches) == ("none", ())
    assert {"no_match", "no_match_exact"} <= set(keys(outcome))
    assert "public_ip_not_transip" not in keys(outcome)
    assert outcome.double_nat is False
    assert PC not in json.dumps(outcome.to_dict())


def test_the_double_nat_fallback_leaves_out_the_other_public_addresses(
    db_session: Session, evidence: Evidence, site: Site
) -> None:
    """The requested configured address is not seen (the logs show the private WAN transip:
    double NAT), and a session to the same destination left through another configured public
    address: it is not a candidate for the requested one."""
    evidence.traffic(
        AT, PHONE, 51000, "203.0.113.10", 443, transip="203.0.113.201", transport=40000
    )
    db_session.commit()
    settings = settings_for(db_session, **{"nat.public_ips": ["203.0.113.200", "203.0.113.201"]})
    outcome = lookup(
        db_session,
        settings=settings,
        public_ip="203.0.113.200",
        public_port=45123,
        dest_ip="203.0.113.10",
        dest_port=443,
    )
    assert outcome.mode == "candidates"
    assert {"public_ip_not_transip", "double_nat"} <= set(keys(outcome))
    assert [(m.traffic.srcip, m.traffic.transip) for m in outcome.matches] == [(PC, WAN)]
    assert PHONE not in json.dumps(outcome.to_dict())


def test_a_quiet_period_does_not_contradict_the_configured_address(
    db_session: Session, site: Site
) -> None:
    """No source-NAT row at all near the moment: the setting is trusted (exact mode)."""
    settings = settings_for(db_session, **{"nat.public_ips": ["203.0.113.200"]})
    outcome = lookup(
        db_session, settings=settings, at=tr(DAY, 3), public_ip="203.0.113.200", public_port=1
    )
    assert outcome.mode == "none"
    assert {"no_match", "no_match_exact"} <= set(keys(outcome))
    assert "public_ip_not_transip" not in keys(outcome)
    assert outcome.double_nat is False


def test_double_nat_candidates_without_destination(db_session: Session, site: Site) -> None:
    outcome = lookup(db_session, public_ip="192.0.2.44", public_port=40001)
    assert outcome.mode == "candidates"
    assert outcome.double_nat
    assert keys(outcome)[:2] == ["double_nat", "candidates_without_destination"]
    assert outcome.explanations[0].text_tr.startswith(
        "Kesin eşleşme için ISS modeminin NAT kaydı gerekir; aşağıda zaman ve hedefe göre"
    )
    # Interim + final rows of the phone's session are one candidate, not several. The 11:20:56
    # interim row is before the bounded scan (time >= at - tolerance); the later rows carry the
    # cumulative duration, so the session start is still right.
    rows = sorted(sorted(r.row_id or 0 for r in m.traffic.evidence) for m in outcome.matches)
    assert rows == sorted([[site.s1], site.s2[1:], [site.s3]])
    by_port = {m.traffic.srcport: m for m in outcome.matches}
    phone = by_port[51002]
    assert (phone.traffic.start, phone.traffic.end) == (tr(DAY, 11, 19, 16), tr(DAY, 11, 25, 26))
    assert phone.time_distance_ms == 0
    assert by_port[51001].score == by_port[51002].score == 65  # 40 time + 25 neutral
    assert by_port[51005].time_distance_ms == 50_000  # starts 11:24:31
    assert by_port[51005].score == 25 + 40 * 10 // 60  # 31
    assert [m.traffic.srcport for m in outcome.matches][-1] == 51005
    assert all(m.kind == "candidate" for m in outcome.matches)
    assert phone.chain is not None
    assert phone.chain.lease is not None
    assert phone.chain.lease.mac == PHONE_MAC
    assert (phone.chain.department, phone.chain.department_source) == ("Wi-Fi Personel", "vlan")
    assert phone.chain.device is not None
    assert phone.chain.device.randomized
    assert {"randomized_mac", "no_person"} <= {e.key for e in phone.chain.explanations}


def test_double_nat_candidates_to_the_requested_destination(
    db_session: Session, site: Site
) -> None:
    outcome = lookup(
        db_session,
        public_ip="192.0.2.44",
        public_port=40001,
        dest_ip="203.0.113.10",
        dest_port=443,
    )
    (match,) = outcome.matches
    assert (match.traffic.srcport, match.destination_match, match.score) == (51001, "ip_port", 90)
    other_port = lookup(db_session, public_ip="192.0.2.44", dest_ip="203.0.113.10", dest_port=80)
    assert (other_port.matches[0].destination_match, other_port.matches[0].score) == (
        "ip_other_port",
        70,
    )


def test_candidates_by_domain_via_web_and_dns_logs(db_session: Session, site: Site) -> None:
    web = lookup(db_session, public_ip="192.0.2.44", dest_domain="portal.example")
    (match,) = web.matches
    assert (match.traffic.srcport, match.destination_match) == (51005, "domain")
    dns = lookup(db_session, public_ip="192.0.2.44", dest_domain="google.com", dest_port=443)
    (match,) = dns.matches
    assert (match.traffic.srcport, match.destination_match, match.score) == (51002, "domain", 90)


def test_unknown_destination_lists_nobody(db_session: Session, site: Site) -> None:
    """Spec 2.3: candidates only *to the given destination*. No session goes there → mode
    ``none``; unrelated clients (and their persons) are never put into the answer."""
    outcome = lookup(db_session, public_ip="192.0.2.44", dest_ip="1.1.1.1")
    assert outcome.mode == "none"
    assert outcome.matches == ()
    assert keys(outcome) == [
        "double_nat_no_candidates",
        "no_match",
        "destination_not_found",
        "no_match_candidates",
    ]
    note = outcome.explanations[2]
    assert note.params == {"destination": "1.1.1.1"}
    assert "hedef alanlarını boş bırakarak" in note.text_tr
    assert "destination fields empty" in note.text_en
    # A domain nobody resolved or visited, a port nobody used: the same.
    for request in ({"dest_domain": "never-seen.example"}, {"dest_port": 8443}):
        other = lookup(db_session, public_ip="192.0.2.44", **request)
        assert (other.mode, other.matches) == ("none", ())
        assert "destination_not_found" in keys(other)
    # Without a destination the whole window is listed — on purpose (the opt-in).
    assert len(lookup(db_session, public_ip="192.0.2.44").matches) == 3


@pytest.mark.parametrize(
    ("request_fields", "first_key"),
    [
        ({"public_ip": "192.0.2.44", "public_port": 40001}, "double_nat_no_candidates"),
        ({"public_ip": WAN}, "port_missing"),
        ({"public_ip": "192.0.2.45", "public_port": 1}, "public_ip_not_configured"),
        ({"dest_ip": "203.0.113.40"}, "destination_search"),
    ],
)
def test_nothing_found_never_promises_a_list(
    db_session: Session, site: Site, request_fields: dict[str, Any], first_key: str
) -> None:
    """In mode ``none`` no explanation says candidates are "listed below" (spec 12.4 text only
    together with candidates); each still says why only a candidate search was possible."""
    settings = settings_for(db_session, **{"nat.wan_transip": WAN, "nat.public_ips": []})
    if first_key == "public_ip_not_configured":
        settings = settings_for(db_session, **{"nat.public_ips": ["192.0.2.44"]})
    outcome = lookup(db_session, settings=settings, at=tr(DAY, 3), **request_fields)
    assert outcome.mode == "none"
    assert keys(outcome)[0] == first_key
    assert {"no_match", "no_match_candidates"} <= set(keys(outcome))
    assert "double_nat" not in keys(outcome)
    assert "candidates_without_destination" not in keys(outcome)
    for item in outcome.explanations:
        assert "listelenmiştir" not in item.text_tr, item.key
        assert "listed below" not in item.text_en, item.key
        assert "are listed" not in item.text_en, item.key


def test_popular_domain_does_not_hit_the_bind_parameter_limit(
    db_session: Session, evidence: Evidence, site: Site
) -> None:
    """Web filter logs of more sessions than PostgreSQL accepts bind parameters (65,535): the
    session match is a correlated EXISTS inside the bounded scan, never a list of ids."""
    db_session.execute(
        text(
            "INSERT INTO fg_web (time, time_received, source, sender, raw_day, raw_line, "
            "raw_offset, sessionid, srcip, dstip, hostname) "
            "SELECT CAST(:at AS timestamptz) - g * interval '10 milliseconds', "
            "CAST(:at AS timestamptz), 'fortigate', '172.31.255.1', :day, 100000 + g, 0, "
            "5000000 + g, CAST('172.20.41.' || (g % 200) AS inet), "
            "CAST('142.250.' || (g % 250) || '.' || (g % 200) AS inet), 'www.popular.example' "
            "FROM generate_series(1, 70000) AS g"
        ),
        {"at": AT, "day": DAY},
    )
    evidence.web(tr(DAY, 11, 23, 37), PC, 51001, "www.popular.example", "203.0.113.10")
    db_session.commit()
    outcome = lookup(db_session, public_ip="192.0.2.44", dest_domain="popular.example")
    (match,) = outcome.matches
    assert (match.traffic.srcport, match.destination_match, match.score) == (51001, "domain", 85)


def test_destination_port_only_and_protocol_filter(db_session: Session, site: Site) -> None:
    by_port = lookup(db_session, public_ip="192.0.2.44", dest_port=443)
    assert {m.destination_match for m in by_port.matches} == {"port"}
    assert lookup(db_session, public_ip="192.0.2.44", protocol="udp").mode == "none"


def test_destination_only_request(db_session: Session, site: Site) -> None:
    outcome = lookup(db_session, dest_ip="203.0.113.40")
    assert outcome.mode == "candidates"
    assert keys(outcome)[0] == "destination_search"
    assert [m.traffic.srcport for m in outcome.matches] == [51005]


def test_truncation_keeps_the_best_and_says_so(db_session: Session, site: Site) -> None:
    settings = settings_for(db_session, **{"lookup.max_candidates": 2})
    outcome = lookup(db_session, settings=settings, public_ip="192.0.2.44")
    assert outcome.truncated
    assert len(outcome.matches) == 2
    (note,) = [e for e in outcome.explanations if e.key == "truncated"]
    assert note.params == {"total": 3, "shown": 2}
    assert "3 aday bulundu; en iyi 2 tanesi" in note.text_tr
    assert 51005 not in {m.traffic.srcport for m in outcome.matches}


def test_port_hint_is_off_by_default_and_ranks_when_enabled(
    db_session: Session, site: Site
) -> None:
    plain = lookup(db_session, public_ip="192.0.2.44", public_port=51001)
    assert "port_hint" not in keys(plain)
    set_setting(db_session, "lookup.port_hint", True, updated_by="tolga")
    db_session.commit()
    hinted = lookup(db_session, public_ip="192.0.2.44", public_port=51001)
    assert "port_hint" in keys(hinted)
    assert (hinted.matches[0].traffic.transport, hinted.matches[0].score) == (51001, 70)


def test_public_ip_with_internal_ip_lists_only_that_client(db_session: Session, site: Site) -> None:
    outcome = lookup(db_session, public_ip="192.0.2.44", internal_ip=PHONE)
    assert [m.traffic.srcip for m in outcome.matches] == [PHONE]
    by_mac = lookup(db_session, public_ip="192.0.2.44", mac=PC_MAC)
    assert {m.traffic.srcip for m in by_mac.matches} == {PC}


def test_internal_ip_mac_and_none_modes(db_session: Session, site: Site) -> None:
    internal = lookup(db_session, internal_ip=PC, at=tr(DAY, 12))
    assert internal.mode == "internal"
    assert internal.matches == ()
    assert internal.attribution is not None
    assert internal.attribution.person is not None
    assert internal.result_count == 1

    by_mac = lookup(db_session, mac=PHONE_MAC.upper().replace(":", "-"), at=tr(DAY, 17))
    assert by_mac.attribution is not None
    assert by_mac.attribution.internal_ip == PHONE

    before_phone = lookup(db_session, mac=PHONE_MAC, at=tr(DAY, 9))
    assert before_phone.mode == "none"
    assert keys(before_phone) == ["no_lease_for_mac"]

    nobody = lookup(db_session, internal_ip="172.20.43.200", at=tr(DAY, 12))
    assert nobody.mode == "none"
    assert nobody.attribution is None
    assert keys(nobody) == ["no_lease"]

    early = lookup(db_session, internal_ip=PC, at=tr(DAY, 7))
    (note,) = early.explanations
    assert note.params["after"] == "2026-10-12T07:42:18+00:00"
    assert note.params["before"] is None
    assert "sonra 12.10.2026 10:42:18" in note.text_tr

    wrong_mac = lookup(db_session, internal_ip=PC, mac=PHONE_MAC, at=tr(DAY, 17))
    assert "mac_not_at_ip" in keys(wrong_mac)


def test_attribute_uses_open_leases_only_until_their_timeout(
    db_session: Session, site: Site
) -> None:
    chain = attribute(db_session, PHONE, tr(DAY, 20))
    assert chain is not None
    assert chain.lease is not None
    assert chain.lease.end is None
    # Last evidence: the phone's traffic logged at 11:25:26; + 26 h 24 min = next day 13:49:26.
    assert attribute(db_session, PHONE, tr(DAY + timedelta(days=1), 13, 49, 26)) is not None
    assert attribute(db_session, PHONE, tr(DAY + timedelta(days=1), 13, 49, 27)) is None
    assert attribute(db_session, PHONE, tr(DAY, 9)) is None  # before the assignment


def test_ip_conflict_lists_every_device(
    db_session: Session, evidence: Evidence, site: Site
) -> None:
    rogue = "00:0c:29:00:00:99"
    evidence.arp(tr(DAY, 12), PC, rogue, interface="vlan42")
    evidence.arp(tr(DAY, 13), PC, rogue, interface="vlan42")
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=NOW)
    db_session.commit()
    chain = attribute(db_session, PC, tr(DAY, 12, 30))
    assert chain is not None
    assert chain.conflict
    assert chain.confidence == "low"
    assert chain.explanations[0].key == "ip_conflict"
    assert chain.lease is not None
    assert chain.lease.mac == PC_MAC
    (other,) = chain.alternatives
    assert other.lease is not None
    assert other.lease.mac == rogue
    assert other.lease.confidence == "medium"  # step confidences are kept
    assert "arp_only" in {e.key for e in other.explanations}
    # The other chain is no more reliable than the first one: low, with the conflict note.
    assert other.confidence == "low"
    assert other.explanations[0].key == "ip_conflict"
    preferred = lookup(db_session, internal_ip=PC, mac=rogue, at=tr(DAY, 12, 30))
    assert preferred.attribution is not None
    assert preferred.attribution.lease is not None
    assert preferred.attribution.lease.mac == rogue  # the requested MAC first, still low
    every = [preferred.attribution, *preferred.attribution.alternatives]
    assert len(every) == 2
    assert {c.confidence for c in every} == {"low"}
    assert all(c.explanations[0].key == "ip_conflict" for c in every)
    assert every[1].person is not None  # the PC's person, also only "low" as a chain
    assert "person_link_at_lookup_time" in keys(preferred)


def test_the_person_is_the_assignment_at_lookup_time(db_session: Session, site: Site) -> None:
    """No assignment history yet: after the PC is given to someone else, a lookup of an earlier
    moment names the new holder — and the outcome says that the link is the current one."""
    before = lookup(db_session, internal_ip=PC, at=AT)
    assert before.attribution is not None
    assert before.attribution.person is not None
    assert before.attribution.person.name == "Selin Kaya"
    note = next(e for e in before.explanations if e.key == "person_link_at_lookup_time")
    assert "sorgunun yapıldığı andaki" in note.text_tr
    assert "at lookup time" in note.text_en
    office = db_session.scalars(select(Department).where(Department.name == "İdari İşler")).one()
    mehmet = Person(name="Mehmet Demir", department_id=office.id)
    db_session.add(mehmet)
    db_session.flush()
    device = db_session.get(Device, PC_MAC)
    assert device is not None
    device.person_id = mehmet.id
    db_session.commit()
    after = lookup(db_session, internal_ip=PC, at=AT)
    assert after.attribution is not None
    assert after.attribution.person is not None
    assert (after.attribution.person.name, after.attribution.department) == (
        "Mehmet Demir",
        "İdari İşler",
    )
    assert "person_link_at_lookup_time" in keys(after)
    # No person in the answer, no such note.
    phone = lookup(db_session, internal_ip=PHONE, at=AT)
    assert phone.attribution is not None
    assert phone.attribution.person is None
    assert "person_link_at_lookup_time" not in keys(phone)


def test_guest_network_explanation(db_session: Session, evidence: Evidence) -> None:
    evidence.dhcp(tr(DAY, 10), "assigned", "172.20.61.105", PHONE_MAC, server="dhcp61")
    db_session.commit()
    rebuild_day(db_session, DAY, horizon=NOW)
    chain = attribute(db_session, "172.20.61.105", tr(DAY, 11))
    assert chain is not None
    assert chain.explanations[0].key == "guest_vlan"
    assert chain.department == "Wi-Fi Misafir"


def test_every_chain_points_at_the_raw_lines(
    db_session: Session, evidence: Evidence, site: Site
) -> None:
    settings = settings_for(db_session, **{"nat.wan_transip": WAN})
    outcome = lookup(db_session, settings=settings, public_ip=WAN, public_port=51001)
    (match,) = outcome.matches
    (traffic_ref,) = match.traffic.evidence
    assert traffic_ref.raw is not None
    assert traffic_ref.row_id == site.s1
    line = evidence.raw_line("fortigate", DAY, traffic_ref.raw.line, traffic_ref.raw.offset or 0)
    assert b"srcip=172.20.42.57 srcport=51001" in line
    assert b"transport=51001" in line
    assert match.chain is not None
    assert match.chain.lease is not None
    opening = match.chain.lease.evidence[0]
    assert opening.raw is not None
    line = evidence.raw_line("core-r1", opening.raw.day, opening.raw.line, opening.raw.offset or 0)
    assert line.endswith(b"dhcp42 assigned 172.20.42.57 for 00:00:5E:00:53:57 PLN-PC14\n")
    assert line.startswith(b"2026-10-12T10:42:18.003000+03:00 172.20.10.1 <30>")


def test_data_window_for_days_outside_the_database(
    db_session: Session, layout: Layout, site: Site
) -> None:
    old = DAY - timedelta(days=500)
    outcome = run_lookup(
        db_session,
        LookupRequest(at=tr(old, 12), internal_ip=PC),
        settings_for(db_session),
        now=NOW,
    )
    window = outcome.data_window
    assert window is not None
    assert (window.requested_day, window.in_database, window.in_vault) == (old, False, False)
    assert (window.db_first_day, window.db_last_day) == (DAY, DAY)
    assert {"older_than_db_retention", "not_in_database", "no_lease"} <= set(keys(outcome))

    layout.manifest(old).parent.mkdir(parents=True)
    layout.manifest(old).write_text("{}", encoding="utf-8")
    vaulted = run_lookup(
        db_session,
        LookupRequest(at=tr(old, 12), internal_ip=PC),
        settings_for(db_session),
        layout=layout,
        now=NOW,
    )
    assert vaulted.data_window is not None
    assert vaulted.data_window.only_in_vault
    assert "only_in_vault" in keys(vaulted)
    missing = run_lookup(
        db_session,
        LookupRequest(at=tr(old + timedelta(days=1), 12), internal_ip=PC),
        settings_for(db_session),
        layout=layout,
        now=NOW,
    )
    assert "no_data_for_day" in keys(missing)

    future = lookup(db_session, internal_ip=PC, at=NOW + timedelta(hours=1))
    assert "future_time" in keys(future)


def test_lease_timeline_behind_is_reported(db_session: Session, site: Site) -> None:
    set_watermark(db_session, tr(DAY, 11))
    db_session.commit()
    outcome = lookup(db_session, public_ip="192.0.2.44")
    assert "lease_timeline_behind" in keys(outcome)


def test_shipped_defaults_are_double_nat(db_session: Session) -> None:
    assert LookupSettings.from_settings(DEFAULT_SETTINGS).double_nat
    empty = lookup(db_session, public_ip="192.0.2.44", public_port=1)
    assert empty.mode == "none"
    assert empty.double_nat
    assert {"double_nat_no_candidates", "no_match", "no_match_candidates"} <= set(keys(empty))
    assert "double_nat" not in keys(empty)  # its spec 12.4 text promises candidates below


def test_domain_by_address_only_counts_less(
    db_session: Session, evidence: Evidence, site: Site
) -> None:
    """Another client resolved the name; the PC's session to the same (CDN) address matches the
    domain only through the address."""
    evidence.dns(tr(DAY, 11, 4, 56), PHONE, "www.example.com", ["203.0.113.10"])
    db_session.commit()
    outcome = lookup(db_session, public_ip="192.0.2.44", dest_domain="example.com", dest_port=443)
    (match,) = outcome.matches
    assert (match.traffic.srcip, match.destination_match, match.score) == (
        PC,
        "domain_address",
        80,
    )
