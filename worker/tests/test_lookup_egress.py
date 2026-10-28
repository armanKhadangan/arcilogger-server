"""Internet exits in the 5651 lookup (ADR-0016; Wave 3 contract 2.8.3, 3.4 "Dual WAN"): the
engine parts of the E-cases — which exit answers a public IP, the log check of an exit's address
and its fallback, the observed and dynamic addresses, the interface rule, the not-seen widening,
the port hint, the per-exit change warning, the internal NAT addresses, untrusted senders in the
probe — and E-j, the legacy equivalence against the answers recorded on the Wave 2 engine
(``goldens/legacy_nat.json``). The PDF assertions of the E-cases are the report tests'.

Values: T1 ``100.64.12.9`` and T2 ``100.64.13.9`` (the FortiGate's addresses behind the two ISP
routers), P1 ``203.0.113.200`` and P2 ``198.51.100.77`` (the lines' public addresses), the
clients ``172.20.42.57`` (line 1) and ``172.20.60.142`` (line 2), the destination
``203.0.113.10:443``.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from arcilogger_db.egress import (
    MIGRATION_KEY,
    legacy_egresses,
    validate_egresses,
)
from arcilogger_db.models import FgTraffic
from arcilogger_db.settings import get_settings, set_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import LookupOutcome, LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.testing import Evidence, tr

GOLDEN = json.loads((Path(__file__).parent / "goldens" / "legacy_nat.json").read_text("utf-8"))
DAY = date(2026, 10, 12)
NOW = tr(DAY + timedelta(days=1), 9)
AT = tr(DAY, 11, 23, 41)
T1, T2 = "100.64.12.9", "100.64.13.9"
P1, P2 = "203.0.113.200", "198.51.100.77"
PC, PC_MAC = "172.20.42.57", "00:00:5e:00:53:57"
PHONE, PHONE_MAC = "172.20.60.142", "02:00:5e:00:53:64"
DEST = "203.0.113.10"


@pytest.fixture
def leases(db_session: Session, evidence: Evidence) -> None:
    """The two clients' leases for the whole day (the attribution chain is not under test)."""
    evidence.dhcp(tr(DAY, 0, 5), "assigned", PC, PC_MAC, server="dhcp42", hostname="PLN-PC14")
    evidence.dhcp(tr(DAY, 0, 7), "assigned", PHONE, PHONE_MAC, server="dhcp60")
    db_session.commit()


def built(session: Session) -> None:
    session.commit()
    rebuild_day(session, DAY, horizon=NOW)
    session.commit()


def exits(session: Session, value: dict[str, Any], *, converted: bool = False) -> LookupSettings:
    """Store ``value`` as ``nat.egresses`` (normalised, as the settings write it); ``converted``:
    with the status row of revision 0018 that keeps the exits *as converted* (D15)."""
    normalised = validate_egresses(value)
    set_setting(session, "nat.egresses", normalised, updated_by=":migration")
    if converted:
        status = {"migration": "0018_nat_egresses", "new": normalised}
        set_setting(session, MIGRATION_KEY, status, updated_by=":migration")
    session.commit()
    return LookupSettings.from_settings(get_settings(session))


def legacy(
    session: Session, public_ips: list[str], wan_transip: str = "", port_hint: bool = False
) -> LookupSettings:
    """The Wave 2 NAT keys as revision 0018 converts them (contract 2.8.2)."""
    return exits(session, legacy_egresses(public_ips, wan_transip, port_hint)[0], converted=True)


def lookup(session: Session, settings: LookupSettings, **request: Any) -> LookupOutcome:
    request.setdefault("at", AT)
    outcome = run_lookup(session, LookupRequest(**request), settings, now=NOW)
    session.rollback()
    data = outcome.to_dict()
    assert LookupOutcome.from_dict(json.loads(json.dumps(data))) == outcome  # E-h
    return outcome


def keys(outcome: LookupOutcome) -> list[str]:
    return [e.key for e in outcome.explanations]


def note(outcome: LookupOutcome, key: str) -> dict[str, Any]:
    return dict(next(e for e in outcome.explanations if e.key == key).params)


def sessions(outcome: LookupOutcome) -> list[tuple[str, int | None, str | None]]:
    return [(m.traffic.srcip, m.traffic.srcport, m.traffic.transip) for m in outcome.matches]


def resolution(outcome: LookupOutcome) -> tuple[str, list[str]]:
    assert outcome.egress is not None
    return outcome.egress.resolution, [e.name for e in outcome.egress.egresses]


def direct(public: list[str], **fields: Any) -> dict[str, Any]:
    return {"mode": "direct", "public": public, **fields}


def isp_nat(transips: list[str], **fields: Any) -> dict[str, Any]:
    return {"mode": "isp_nat", "transips": transips, **fields}


# -- E-j: legacy equivalence ----------------------------------------------------------------------

#: The Wave 2 site of the golden (``test_record_legacy_nat.py``, removed with the switch): router
#: mode (only T1 is the transip), bridge mode (only A), only an unconfigured public pool address,
#: and a quiet moment.
MOMENTS = {
    "router": tr(DAY, 11, 23, 41),
    "bridge": tr(DAY, 14, 23, 41),
    "pool": tr(DAY, 16, 23, 41),
    "quiet": tr(DAY, 3),
}
A, POOL = "203.0.113.200", "198.51.100.250"


def legacy_site(session: Session, evidence: Evidence) -> None:
    """The site the golden was recorded on (the same rows, in the same order)."""
    evidence.dhcp(tr(DAY, 8, 42, 18), "assigned", PC, PC_MAC, server="dhcp42", hostname="PLN-PC14")
    evidence.dhcp(
        tr(DAY, 8, 37, 52), "assigned", PHONE, PHONE_MAC, server="dhcp60", hostname="Telefon-12"
    )
    router, bridge, pool = MOMENTS["router"], MOMENTS["bridge"], MOMENTS["pool"]
    evidence.traffic(router, PC, 51001, DEST, 443, transip=T1)
    evidence.traffic(router + timedelta(seconds=40), PHONE, 51002, "203.0.113.20", transip=T1)
    evidence.traffic(router + timedelta(seconds=20), PC, 51005, "203.0.113.40", transip=T1)
    evidence.traffic(bridge, PC, 52001, DEST, 443, transip=A)
    evidence.traffic(bridge + timedelta(seconds=15), PHONE, 52002, "203.0.113.20", transip=A)
    evidence.traffic(pool, PC, 53001, DEST, 443, transip=POOL)
    evidence.traffic(pool + timedelta(seconds=10), PHONE, 53002, "203.0.113.20", transip=POOL)
    built(session)


def answer(outcome: LookupOutcome) -> dict[str, Any]:
    return {
        "mode": outcome.mode,
        "double_nat": outcome.double_nat,
        "matches": [
            [m.traffic.srcip, m.traffic.srcport, m.traffic.transip, m.traffic.transport]
            + [m.kind, m.score]
            for m in outcome.matches
        ],
    }


def _claimed(case: str) -> set[str]:
    """The fixture's public NAT addresses an exit of the converted ``case`` claims."""
    values = GOLDEN["cases"][case]
    converted, _dropped = legacy_egresses(
        values["public_ips"], values["wan_transip"], values["port_hint"]
    )
    return {a for e in converted.values() if e["mode"] == "direct" for a in e["public"]}


def _differs(
    case: str, request: dict[str, Any], want: dict[str, Any], got: dict[str, Any], outcome: Any
) -> str:
    """The named exception of a difference from the Wave 2 answer (contract 3.4 E-j); a
    difference no rule explains fails the test."""
    address = request.get("public_ip")
    egress = outcome.egress
    if egress is not None and egress.resolution == "observed":
        # D31 (a): the FortiGate itself used the address near the moment: exact on it with a
        # port, candidates over it without one, double_nat false.
        assert all(m[2] == address for m in got["matches"]), (case, request)
        assert got["double_nat"] is False
        if request.get("public_port") is not None:
            assert got["mode"] in ("exact", "none")
        return "observed"
    unclaimed_public = {A, POOL} - _claimed(case)
    kept = [m for m in want["matches"] if m[2] not in unclaimed_public]
    if case == "f" and address not in _claimed(case):
        # Case (f), other P: Wave 2 narrowed it to A's own sessions; now the catch-all's
        # unclaimed private or shared sessions (none on a bridge-mode site).
        kept = [m for m in kept if m[2] not in (A,)]
        assert [m[:4] for m in got["matches"]] == [m[:4] for m in kept], (case, request)
        return "case_f"
    if kept != want["matches"] or (want["mode"] == "candidates" and got["mode"] == "none"):
        # O21: a public NAT address no exit claims is the FortiGate's own address: its sessions
        # are no candidates for another P and never fail an address's check.
        assert [m[:4] for m in got["matches"]] == [m[:4] for m in kept], (case, request)
        return "unclaimed_public"
    # D19 (probe C2): every isp_nat answer, fallback included, is double NAT.
    assert (got["mode"], got["matches"]) == (want["mode"], want["matches"]), (case, request)
    assert (want["double_nat"], got["double_nat"]) == (False, True), (case, request)
    assert egress is not None
    assert egress.resolution in ("public_unknown", "fallback", "transip"), (case, request)
    return "c2_double_nat"


def test_e_j_the_converted_legacy_settings_keep_the_wave_2_answers(
    db_session: Session, evidence: Evidence
) -> None:
    """E-j: for every case of 2.8.2 the exits ``legacy_egresses`` converts give the Wave 2
    answer (mode, matched sessions and their order and scores, ``double_nat``) recorded in
    ``goldens/legacy_nat.json``, except the named exceptions: the D31 (a) observed answer, the
    unclaimed public NAT address (O21), D19's ``double_nat`` (probe C2), case (f) other P, case
    (g) (503 before) and case (h) (a private address in ``nat.public_ips``)."""
    legacy_site(db_session, evidence)
    answers: dict[str, dict[str, dict[str, Any]]] = {}
    seen: dict[str, set[str]] = {}
    for case, values in GOLDEN["cases"].items():
        settings = legacy(
            db_session, values["public_ips"], values["wan_transip"], values["port_hint"]
        )
        answers[case] = {}
        for name, request in GOLDEN["requests"].items():
            fields = {k: v for k, v in request.items() if k != "moment"}
            outcome = lookup(db_session, settings, at=MOMENTS[request["moment"]], **fields)
            got = answers[case][name] = answer(outcome)
            want = GOLDEN["answers"][case][name]
            if case in ("g", "h") or got == want:
                continue
            seen.setdefault(_differs(case, fields, want, got, outcome), set()).add(case)
    # (g): Wave 2 refused every lookup (503); a value stored without validation is dropped, and
    # the rest gives the answers of (a). (h): the private address becomes the NAT address of the
    # exit wan, exactly as Wave 2's nat.wan_transip does: the answers of (c).
    assert all(answer == {"error": "ValueError"} for answer in GOLDEN["answers"]["g"].values())
    assert answers["g"] == answers["a"]
    assert answers["h"] == answers["c"]
    assert GOLDEN["answers"]["h"] != GOLDEN["answers"]["c"]
    # Every exception is exercised; the cases without public NAT addresses or bridge mode keep
    # every Wave 2 answer that no exception names.
    assert set(seen) == {"observed", "unclaimed_public", "c2_double_nat", "case_f"}
    for kept in ("A+port@router", "A@router", "A+port@quiet", "other+dest@router"):
        for case in ("a", "c", "e"):
            assert answers[case][kept] == GOLDEN["answers"][case][kept], (case, kept)


def test_e_j_d2_a_quiet_public_address_falls_back_to_w(
    db_session: Session, evidence: Evidence
) -> None:
    """E-j (d2): legacy (d), A quiet and only W active (a moment before the line was switched to
    bridge mode), no interfaces: the D15 own set leaves W (F's NAT address) out, so the check
    fails and the answer is the fallback over A and W, as Wave 2 gave it."""
    legacy_site(db_session, evidence)
    settings = legacy(db_session, [A], T1)
    outcome = lookup(db_session, settings, public_ip=A, public_port=52001)
    assert outcome.mode == "candidates"
    assert resolution(outcome) == ("fallback", ["genel-1", "wan"])
    assert {m.traffic.transip for m in outcome.matches} == {T1}
    assert "public_ip_not_transip" in keys(outcome)
    assert "egress_search" not in keys(outcome)
    assert outcome.double_nat is True
    assert outcome.egress is not None
    assert outcome.egress.searched.outside_seen == (T1,)
    assert outcome.egress.searched.transips == (A, T1)
    assert [m.egress for m in outcome.matches] == ["wan"] * len(outcome.matches)


# -- E-a, E-b: two ISP NAT lines ------------------------------------------------------------------

HAT_1 = isp_nat([T1], public=[P1], interfaces=["wan1"], label="Ana hat")
HAT_2 = isp_nat([T2], public=[P2], interfaces=["wan2"], label="Yedek hat")


def two_lines(evidence: Evidence, at: datetime = AT) -> None:
    """One session per line near ``at``: line 1 through T1 on wan1, line 2 through T2 on wan2."""
    evidence.traffic(at, PC, 51001, DEST, 443, transip=T1, dstintf="wan1")
    later = at + timedelta(seconds=5)
    evidence.traffic(later, PHONE, 51002, DEST, 443, transip=T2, dstintf="wan2")


def test_e_a_e_b_a_line_lists_only_its_own_sessions(
    db_session: Session, evidence: Evidence
) -> None:
    two_lines(evidence)
    settings = exits(db_session, {"hat-1": HAT_1, "hat-2": HAT_2})
    p2 = lookup(db_session, settings, public_ip=P2, public_port=41000)
    assert p2.mode == "candidates"
    assert sessions(p2) == [(PHONE, 51002, T2)]
    assert resolution(p2) == ("public", ["hat-2"])
    assert p2.double_nat is True
    assert [m.egress for m in p2.matches] == ["hat-2"]
    assert note(p2, "egress_search") == {"egress": "hat-2", "transips": T2, "interfaces": "wan2"}
    assert p2.egress is not None
    assert (p2.egress.searched.transips, p2.egress.searched.interfaces) == ((T2,), ("wan2",))
    assert p2.egress.searched.interface_filter is True
    assert (p2.matches[0].traffic.dstintf, p2.matches[0].traffic.srcintf) == ("wan2", None)
    p1 = lookup(db_session, settings, public_ip=P1)
    assert sessions(p1) == [(PC, 51001, T1)]
    assert resolution(p1) == ("public", ["hat-1"])


# -- E-c … E-c5: the log check of a direct line and its fallback (D15) ---------------------------


def test_e_c_a_quiet_direct_line_is_not_double_natted_by_another_line(
    db_session: Session, evidence: Evidence
) -> None:
    """hat-2 is an ISP NAT line with its own public address: its NAT address is in hat-1's own
    set, so hat-1 quiet while T2 is active is exact (no session), never T2's session."""
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T2)
    settings = exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2], public=[P2])})
    outcome = lookup(db_session, settings, public_ip=P1, public_port=51001)
    assert (outcome.mode, outcome.matches) == ("none", ())
    assert resolution(outcome) == ("public", ["hat-1"])
    assert {"no_match", "no_match_exact"} <= set(keys(outcome))
    assert "public_ip_not_transip" not in keys(outcome)
    assert outcome.double_nat is False


def _double_natted_line_1(evidence: Evidence, at: datetime, line_1: str = "wan1") -> None:
    """Line 1 double-NATed to the unclaimed private X (100.64.12.9) at ``at``; line 2 active."""
    evidence.traffic(at, PC, 51001, DEST, 443, transip=T1, dstintf=line_1)
    later = at + timedelta(seconds=5)
    evidence.traffic(later, PHONE, 51002, DEST, 443, transip=T2, dstintf="wan2")


def test_e_c2_e_c3_a_double_natted_direct_line_falls_back_on_its_own_line(
    db_session: Session, evidence: Evidence
) -> None:
    _double_natted_line_1(evidence, AT)
    _double_natted_line_1(evidence, tr(DAY, 14), line_1="wan2")  # X on line 2's interface
    hat_1 = direct([P1], interfaces=["wan1"])
    hat_2 = isp_nat([T2], interfaces=["wan2"])
    settings = exits(db_session, {"hat-1": hat_1, "hat-2": hat_2})
    for port in (51001, None):  # E-c2 with the port, E-c3 without it
        outcome = lookup(db_session, settings, public_ip=P1, public_port=port)
        assert outcome.mode == "candidates"
        assert sessions(outcome) == [(PC, 51001, T1)]  # never a T2 session
        assert resolution(outcome) == ("fallback", ["hat-1"])  # F is empty: no shared interface
        assert "public_ip_not_transip" in keys(outcome)
        assert not {"egress_search", "port_missing", "no_match_exact"} & set(keys(outcome))
        assert outcome.double_nat is True
        assert outcome.egress is not None
        searched = outcome.egress.searched
        assert (searched.transips, searched.unclaimed, searched.outside_seen) == (
            (P1,),
            True,
            (T1,),
        )
        assert (searched.interfaces, searched.interface_filter) == (("wan1",), True)
        assert [m.egress for m in outcome.matches] == [None]  # an unclaimed NAT address
    # Without any declared interfaces (a new configuration): F is empty (only exits as
    # converted fall into each other), so P1 and the unclaimed addresses, still never T2.
    plain = exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2])})
    outcome = lookup(db_session, plain, public_ip=P1, public_port=51001)
    assert sessions(outcome) == [(PC, 51001, T1)]
    assert resolution(outcome) == ("fallback", ["hat-1"])
    # X on wan2 (another line's private address) while hat-1 declares wan1: rows off its own
    # interfaces do not count, the check passes and P1 + port is exact.
    other_line = lookup(db_session, settings, at=tr(DAY, 14), public_ip=P1, public_port=51001)
    assert (other_line.mode, resolution(other_line)) == ("none", ("public", ["hat-1"]))
    assert "public_ip_not_transip" not in keys(other_line)


def test_e_c4_isp_nat_exits_without_a_public_address_leave_the_own_set(
    db_session: Session, evidence: Evidence
) -> None:
    """An upgraded single-line site whose ISP router still translates: only 100.64.12.9 (on
    wan1) is active near the moment and P1 + port is asked."""
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=T1, dstintf="wan1")
    converted, _dropped = legacy_egresses([P1], "", False)  # genel-1 + tanimsiz
    hat_2 = isp_nat([T1], interfaces=["wan1"])  # saved from the observed panel
    exits(db_session, converted, converted=True)
    settings = exits(db_session, {**converted, "hat-2": hat_2})
    outcome = lookup(db_session, settings, public_ip=P1, public_port=51001)
    assert resolution(outcome) == ("fallback", ["genel-1", "tanimsiz"])
    assert "public_ip_not_transip" in keys(outcome)
    assert "no_match_exact" not in keys(outcome)
    assert sessions(outcome) == []  # T1 is hat-2's: no 100.64.12.9 session is listed
    # Legacy (d) after wan1 is entered on wan only (no longer as converted): the same fallback.
    with_wan, _dropped = legacy_egresses([P1], T1, False)
    exits(db_session, with_wan, converted=True)
    edited = {**with_wan, "wan": {**with_wan["wan"], "interfaces": ["wan1"]}}
    outcome = lookup(db_session, exits(db_session, edited), public_ip=P1, public_port=51001)
    assert resolution(outcome) == ("fallback", ["genel-1"])
    assert "public_ip_not_transip" in keys(outcome)
    assert "no_match_exact" not in keys(outcome)


def test_e_c4_a_real_second_line_never_fails_the_check(
    db_session: Session, evidence: Evidence
) -> None:
    """wan1 declared on hat-1, wan2 on hat-2 and 100.64.12.9's rows on wan2: hat-2 leaves the own
    set (disjoint interfaces) but rows off wan1 do not count, so P1 + port is exact."""
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T1, dstintf="wan2")
    settings = exits(
        db_session,
        {"hat-1": direct([P1], interfaces=["wan1"]), "hat-2": isp_nat([T1], interfaces=["wan2"])},
    )
    outcome = lookup(db_session, settings, public_ip=P1, public_port=51001)
    assert (outcome.mode, resolution(outcome)) == ("none", ("public", ["hat-1"]))
    assert "public_ip_not_transip" not in keys(outcome)


@pytest.mark.parametrize("hat_2_public", [[P2], []])
def test_e_c4_the_same_address_on_both_lines(
    db_session: Session, evidence: Evidence, hat_2_public: list[str]
) -> None:
    """Round 6: line 1's modem was reset to router mode and hands the FortiGate the address line
    2's modem gives it, so near the moment 100.64.12.9 rows lie on wan1 (and wan2) and P1 has
    none: the fallback, never a bare "no session", and no session that left by wan2."""
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=T1, dstintf="wan1")
    later = AT + timedelta(seconds=5)
    evidence.traffic(later, PHONE, 51002, DEST, 443, transip=T1, dstintf="wan2")
    hat_2 = isp_nat([T1], public=hat_2_public, interfaces=["wan2"])
    settings = exits(db_session, {"hat-1": direct([P1], interfaces=["wan1"]), "hat-2": hat_2})
    outcome = lookup(db_session, settings, public_ip=P1, public_port=51001)
    assert resolution(outcome) == ("fallback", ["hat-1"])
    assert "public_ip_not_transip" in keys(outcome)
    assert "no_match_exact" not in keys(outcome)
    assert all(m.traffic.dstintf != "wan2" for m in outcome.matches)
    assert PHONE not in json.dumps(outcome.to_dict())


def _bridge_switch(evidence: Evidence, *, w_until: datetime, a_from: datetime) -> None:
    """Rows of A (bridge mode) and of W (router mode) around a change of the line's mode, and the
    requester's session at 18:19 through W."""
    for minute in range(0, 31, 2):
        at = tr(DAY, 18) + timedelta(minutes=minute)
        if at < w_until:
            evidence.traffic(at, PHONE, 40000 + minute, "203.0.113.20", transip=T1)
        if at >= a_from:
            evidence.traffic(at, PHONE, 41000 + minute, "203.0.113.20", transip=A)
    evidence.traffic(tr(DAY, 18, 19, 7), PC, 51001, DEST, 443, transip=T1, duration=5)


def test_e_c5_seen_but_not_alone_falls_back(db_session: Session, evidence: Evidence) -> None:
    """Legacy (d) as converted, line 1 in bridge mode until its modem is reset to router mode at
    18:14: A rows 18:04-18:14, W rows from 18:14, the requester's session at 18:19 through W."""
    for minute in range(4, 15, 2):
        at = tr(DAY, 18) + timedelta(minutes=minute)
        evidence.traffic(at, PHONE, 41000 + minute, "203.0.113.20", transip=A)
    for minute in range(14, 31, 2):
        at = tr(DAY, 18) + timedelta(minutes=minute)
        evidence.traffic(at, PHONE, 40000 + minute, "203.0.113.20", transip=T1)
    evidence.traffic(tr(DAY, 18, 19, 7), PC, 51001, DEST, 443, transip=T1, duration=5)
    evidence.traffic(tr(DAY, 18, 12), PC, 51001, DEST, 443, transip=A, duration=5)
    settings = legacy(db_session, [A], T1)
    outcome = lookup(db_session, settings, at=tr(DAY, 18, 19, 7), public_ip=A, public_port=51001)
    assert outcome.mode == "candidates"
    assert resolution(outcome) == ("fallback", ["genel-1", "wan"])
    assert (PC, 51001, T1) in sessions(outcome)
    text = next(e for e in outcome.explanations if e.key == "public_ip_not_transip")
    assert "ya da yalnızca bir kısmında görünüyor" in text.text_tr
    assert "or only part of the time" in text.text_en
    assert outcome.egress is not None
    assert outcome.egress.searched.outside_seen == (T1,)
    assert not {"no_match", "no_match_exact", "exact_nat"} & set(keys(outcome))  # discarded
    # A found match stays exact: the requester's session through A:51001 at 18:12.
    found = lookup(db_session, settings, at=tr(DAY, 18, 12), public_ip=A, public_port=51001)
    assert (found.mode, sessions(found)) == ("exact", [(PC, 51001, A)])


def test_e_c5_mirror_and_a_new_configuration(db_session: Session, evidence: Evidence) -> None:
    """The mirror (W rows until 18:24, A rows from 18:24, a switch to bridge mode), and a new
    configuration in which 100.64.12.9 was saved as an ISP NAT exit on wan1 beside the direct
    line on wan1: it shares wan1, so it is in F (and in G), and the fallback lists the
    requester's session (implementation note round 7)."""
    _bridge_switch(evidence, w_until=tr(DAY, 18, 24), a_from=tr(DAY, 18, 24))
    at = tr(DAY, 18, 19, 7)
    mirror = lookup(db_session, legacy(db_session, [A], T1), at=at, public_ip=A, public_port=51001)
    assert resolution(mirror) == ("fallback", ["genel-1", "wan"])
    assert (PC, 51001, T1) in sessions(mirror)
    db_session.execute(update(FgTraffic).values(dstintf="wan1"))
    db_session.commit()
    new = exits(
        db_session,
        {"hat-1": direct([A], interfaces=["wan1"]), "hat-2": isp_nat([T1], interfaces=["wan1"])},
    )
    outcome = lookup(db_session, new, at=at, public_ip=A, public_port=51001)
    assert resolution(outcome) == ("fallback", ["hat-1", "hat-2"])
    assert (PC, 51001, T1) in sessions(outcome)


def test_e_c5_a_line_that_carried_the_address_stays_exact(
    db_session: Session, evidence: Evidence
) -> None:
    """Implementation note round 7: A seen beside W while sessions through A overlap the
    moment (on other ports): the FortiGate held A then, so P:port without a session is a plain
    exact "no session", not the fallback over the other line's users."""
    for minute in range(0, 31, 2):
        at = tr(DAY, 18) + timedelta(minutes=minute)
        evidence.traffic(at, PHONE, 40000 + minute, "203.0.113.20", transip=T1)
        evidence.traffic(at, PC, 42000 + minute, "203.0.113.30", transip=A, duration=150)
    settings = legacy(db_session, [A], T1)
    outcome = lookup(db_session, settings, at=tr(DAY, 18, 19, 7), public_ip=A, public_port=51001)
    assert (outcome.mode, resolution(outcome)) == ("none", ("public", ["genel-1"]))
    assert {"no_match", "no_match_exact"} <= set(keys(outcome))
    assert "public_ip_not_transip" not in keys(outcome)


# -- E-d, E-d2: a line that does not pass the FortiGate ------------------------------------------


def test_e_d_e_d2_a_not_logged_line(db_session: Session, evidence: Evidence) -> None:
    misafir = {"mode": "not_logged", "public": ["198.51.100.90"]}
    settings = exits(db_session, {"hat-1": direct([P1]), "misafir": misafir})
    nobody = lookup(db_session, settings, public_ip="198.51.100.90", public_port=51001)
    assert (nobody.mode, nobody.matches) == ("none", ())
    assert resolution(nobody) == ("public", ["misafir"])
    assert note(nobody, "egress_not_logged") == {"address": "198.51.100.90", "egress": "misafir"}
    assert nobody.double_nat is False
    # The address is the transip of a session overlapping the moment after all: observed.
    evidence.traffic(AT, PC, 51001, DEST, 443, transip="198.51.100.90")
    db_session.commit()
    seen = lookup(db_session, settings, public_ip="198.51.100.90", public_port=51001)
    assert (seen.mode, sessions(seen)) == ("exact", [(PC, 51001, "198.51.100.90")])
    assert resolution(seen) == ("observed", ["misafir"])
    assert {"egress_not_logged_but_seen", "exact_nat"} <= set(keys(seen))
    assert "egress_not_logged" not in keys(seen)
    assert seen.double_nat is False


# -- E-e, E-e2, E-e3: an address no exit claims ---------------------------------------------------


def test_e_e_an_unclaimed_address_goes_to_the_exits_of_unknown_public_address(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=T1)  # an unclaimed private address
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T2)
    other = "192.0.2.44"
    unknown = exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2])})
    outcome = lookup(db_session, unknown, public_ip=other, public_port=41000)
    assert sessions(outcome) == [(PHONE, 51002, T2)]  # only the exit without a public address
    assert resolution(outcome) == ("public_unknown", ["hat-2"])
    assert note(outcome, "egress_public_unknown") == {"address": other, "egresses": "hat-2"}
    assert outcome.double_nat is True
    # An ISP NAT line marked as changing its public address is searched for it too; its listed
    # address is answered as its own, with egress_public_dynamic.
    dynamic = isp_nat([T2], public=[P2], public_dynamic=True)
    settings = exits(db_session, {"hat-1": direct([P1]), "hat-2": dynamic})
    outcome = lookup(db_session, settings, public_ip=other, public_port=41000)
    assert (resolution(outcome), sessions(outcome)) == (
        ("public_unknown", ["hat-2"]),
        [(PHONE, 51002, T2)],
    )
    assert "egress_public_dynamic" not in keys(outcome)
    listed = lookup(db_session, settings, public_ip=P2, public_port=41000)
    assert resolution(listed) == ("public", ["hat-2"])
    assert note(listed, "egress_public_dynamic") == {
        "egress": "hat-2",
        "address": P2,
        "at": AT.astimezone(UTC).isoformat(),
    }
    static = exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2], public=[P2])})
    assert "egress_public_dynamic" not in keys(lookup(db_session, static, public_ip=P2))
    # Every exit has a static public address and the address is not seen: nobody.
    outcome = lookup(db_session, static, public_ip=other, public_port=41000)
    assert (outcome.mode, outcome.matches) == ("none", ())
    assert resolution(outcome) == ("no_egress", ["hat-1", "hat-2"])
    assert note(outcome, "public_ip_no_egress") == {"address": other}
    assert outcome.double_nat is False


def test_e_e2_e_e3_an_address_the_fortigate_used_itself(
    db_session: Session, evidence: Evidence
) -> None:
    """D31 (a): the unclaimed address is the transip of sessions near the moment (a direct line
    whose address changed): exact with a port, candidates without, on that address only."""
    changed = "203.0.113.201"
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=changed)
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T2)  # another person's session
    for settings in (
        exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2], public=[P2])}),
        exits(db_session, {}),  # E-e3: a bridge-mode site that never defined exits
    ):
        exact = lookup(db_session, settings, public_ip=changed, public_port=51001)
        assert (exact.mode, sessions(exact)) == ("exact", [(PC, 51001, changed)])
        assert resolution(exact) == ("observed", [])
        assert {"egress_observed_not_configured", "exact_nat"} <= set(keys(exact))
        assert not {"egress_unconfigured", "double_nat"} & set(keys(exact))
        assert exact.double_nat is False
        assert [m.egress for m in exact.matches] == [None]
        assert PHONE not in json.dumps(exact.to_dict())
        candidates = lookup(db_session, settings, public_ip=changed)
        assert (candidates.mode, sessions(candidates)) == ("candidates", [(PC, 51001, changed)])
        assert "port_missing" in keys(candidates)


# -- E-f, E-g: the search notes and the port hint -------------------------------------------------


def test_e_f_only_isp_nat_and_nat_address_narrowings_say_egress_search(
    db_session: Session, evidence: Evidence
) -> None:
    two_lines(evidence)
    settings = exits(db_session, {"hat-1": direct([P1], interfaces=["wan1"]), "hat-2": HAT_2})
    isp = lookup(db_session, settings, public_ip=P2)
    assert "egress_search" in keys(isp)
    nat = lookup(db_session, settings, public_ip=T2, public_port=51002)
    assert (nat.mode, resolution(nat)) == ("exact", ("transip", ["hat-2"]))
    assert {"egress_search", "exact_nat"} <= set(keys(nat))
    assert nat.double_nat is True  # an ISP NAT exit's own NAT address (legacy (c) keeps it)
    direct_line = lookup(db_session, settings, public_ip=P1)  # quiet direct line: fallback
    assert "egress_search" not in keys(direct_line)
    assert all(m.egress is not None for o in (isp, nat) for m in o.matches)


def test_e_g_the_port_hint_ranks_only_a_preserved_exits_candidates(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=T2)
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T2)
    preserved = isp_nat([T2], public=[P2], port_preservation="preserved")
    settings = exits(db_session, {"hat-2": preserved})
    hinted = lookup(db_session, settings, public_ip=P2, public_port=51002)
    by_port = {m.traffic.srcport: m.score for m in hinted.matches}
    assert by_port[51002] == by_port[51001] + 5
    assert hinted.matches[0].traffic.srcport == 51002
    assert note(hinted, "port_hint") == {"egresses": "hat-2"}
    assert "hat-2 çıkışında" in next(e.text_tr for e in hinted.explanations if e.key == "port_hint")
    exact = lookup(db_session, settings, public_ip=T2, public_port=51002)  # exact never uses it
    assert exact.mode == "exact"
    assert "port_hint" not in keys(exact)
    plain = exits(db_session, {"hat-2": isp_nat([T2], public=[P2])})
    unhinted = lookup(db_session, plain, public_ip=P2, public_port=51002)
    assert "port_hint" not in keys(unhinted)
    assert len({m.score for m in unhinted.matches}) == 1
