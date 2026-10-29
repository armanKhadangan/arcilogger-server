"""Internet exits in the 5651 lookup (ADR-0016; Wave 3 contract 2.8.3, 3.4 "Dual WAN"): the
engine parts of the E-cases — which exit answers a public IP, the log check of an exit's address
and its fallback, the observed and dynamic addresses, the interface rule, the not-seen widening,
the port hint, the per-exit change warning, the internal NAT addresses, untrusted senders in the
probe and on a match — and E-j, the legacy equivalence against the answers recorded on the
Wave 2 engine (``goldens/legacy_nat.json``). The PDF assertions of the E-cases are the report
tests'.

Values: T1 ``100.64.12.9`` and T2 ``100.64.13.9`` (the FortiGate's addresses behind the two ISP
routers), P1 ``203.0.113.200`` and P2 ``198.51.100.77`` (the lines' public addresses), the
clients ``172.20.42.57`` (line 1) and ``172.20.60.142`` (line 2), the destination
``203.0.113.10:443``.
"""

from __future__ import annotations

import ast
import json
import re
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import update
from sqlalchemy.orm import Session

from arcilogger_db import egress as egress_module
from arcilogger_db.egress import (
    CHANGES_KEY,
    MIGRATION_KEY,
    EgressValueError,
    fingerprint,
    legacy_egresses,
    validate_egresses,
)
from arcilogger_db.models import FgTraffic, Setting, Source
from arcilogger_db.settings import SYSTEM_AUTHOR, get_setting, get_settings, set_setting
from arcilogger_db.settings_admin import change_setting
from arcilogger_worker.correlation.leases import rebuild_day
from arcilogger_worker.lookup import LookupOutcome, LookupRequest, LookupSettings, run_lookup
from arcilogger_worker.lookup import engine as engine_module
from arcilogger_worker.lookup import sessions as sessions_module
from arcilogger_worker.lookup import texts as texts_module
from arcilogger_worker.lookup.types import EXPLANATIONS
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
#: When a test's exits were entered: before the day of the evidence (E-k4 changes them later).
ENTERED = tr(DAY - timedelta(days=30), 9)
#: Explanation keys the engine never emits again (2.11); stored lookups still render them.
RETIRED = {"public_ip_not_configured", "wan_transip_not_seen"}


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
    """Store ``value`` as ``nat.egresses`` (normalised, as the settings write it), entered before
    the day (:data:`ENTERED`, with its change record); ``converted``: with the status row of
    revision 0018 that keeps the exits *as converted* (D15)."""
    normalised = validate_egresses(value)
    set_setting(session, "nat.egresses", normalised, updated_by=":migration")
    entered = ENTERED.astimezone(UTC).isoformat()
    record = {
        name: {
            "fingerprint": fingerprint(exit_),
            "changed_at": entered,
            "changed_by": ":migration",
            "deleted": False,
        }
        for name, exit_ in normalised.items()
    }
    set_setting(session, CHANGES_KEY, record, updated_by=SYSTEM_AUTHOR)
    keys = ("nat.egresses", CHANGES_KEY)
    session.execute(update(Setting).where(Setting.key.in_(keys)).values(updated_at=ENTERED))
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
    assert not RETIRED & {e.key for e in outcome.explanations}
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
            [
                m.traffic.srcip,
                m.traffic.srcport,
                m.traffic.transip,
                m.traffic.transport,
                m.kind,
                m.score,
            ]
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


def test_e_c5_a_moment_within_the_tolerance_of_the_switch(
    db_session: Session, evidence: Evidence
) -> None:
    """Legacy (d) as converted, the moment 25 s after line 1's modem was reset to router mode
    (A's last session 18:13:00-18:14:00, W from 18:14): a session through A that ended before
    the moment is no evidence that the FortiGate held A then, so the answer is the fallback
    with the requester's session through W, never a bare "no session"; a session through A on
    the requested port inside the tolerance stays exact. The mirror: the moment 20 s before the
    first session through A (a switch to bridge mode)."""
    for minute in range(4, 15, 2):
        at = tr(DAY, 18) + timedelta(minutes=minute)
        evidence.traffic(at, PHONE, 41000 + minute, "203.0.113.20", transip=A, duration=60)
    for minute in range(14, 31, 2):
        at = tr(DAY, 18) + timedelta(minutes=minute)
        evidence.traffic(at, PHONE, 40000 + minute, "203.0.113.20", transip=T1)
    evidence.traffic(tr(DAY, 18, 14, 25), PC, 51001, DEST, 443, transip=T1, duration=5)
    settings = legacy(db_session, [A], T1)
    request = {"public_ip": A, "public_port": 51001, "tolerance_seconds": 60}
    outcome = lookup(db_session, settings, at=tr(DAY, 18, 14, 25), **request)
    assert (outcome.mode, resolution(outcome)) == ("candidates", ("fallback", ["genel-1", "wan"]))
    assert (PC, 51001, T1) in sessions(outcome)
    assert "public_ip_not_transip" in keys(outcome)
    assert not {"no_match", "no_match_exact"} & set(keys(outcome))
    # The requester's session through A:51001 ended 45 s before the moment: exact.
    evidence.traffic(tr(DAY, 18, 13, 40), PC, 51001, DEST, 443, transip=A, duration=5)
    db_session.commit()
    found = lookup(db_session, settings, at=tr(DAY, 18, 14, 25), **request)
    assert (found.mode, sessions(found)) == ("exact", [(PC, 51001, A)])
    # The mirror, on the same line an hour later.
    for minute in range(0, 24, 2):
        at = tr(DAY, 19) + timedelta(minutes=minute)
        evidence.traffic(at, PHONE, 40100 + minute, "203.0.113.20", transip=T1)
    evidence.traffic(tr(DAY, 19, 23, 50), PC, 51001, DEST, 443, transip=T1, duration=5)
    for minute in range(24, 41, 2):
        at = tr(DAY, 19) + timedelta(minutes=minute, seconds=20)
        evidence.traffic(at, PHONE, 41100 + minute, "203.0.113.20", transip=A)
    db_session.commit()
    mirror = lookup(db_session, settings, at=tr(DAY, 19, 23, 50), **request)
    assert resolution(mirror) == ("fallback", ["genel-1", "wan"])
    assert (PC, 51001, T1) in sessions(mirror)


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


def test_e_e_the_exits_of_unknown_public_address_keep_their_interfaces_and_widen(
    db_session: Session, evidence: Evidence
) -> None:
    """A quiet line without a public address: its search widens to the unclaimed addresses
    (O5), on its own interfaces only."""
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip="100.64.12.77", dstintf="wan2")
    evidence.traffic(AT, PC, 51001, DEST, 443, transip="100.64.12.78", dstintf="wan1")
    hat_2 = isp_nat([T2], interfaces=["wan2"])
    settings = exits(db_session, {"hat-1": direct([P1], interfaces=["wan1"]), "hat-2": hat_2})
    outcome = lookup(db_session, settings, public_ip="192.0.2.44")
    assert resolution(outcome) == ("public_unknown", ["hat-2"])
    assert sessions(outcome) == [(PHONE, 51002, "100.64.12.77")]
    assert {"egress_public_unknown", "egress_transip_not_seen"} <= set(keys(outcome))
    assert outcome.egress is not None
    assert (outcome.egress.searched.interfaces, outcome.egress.searched.unclaimed) == (
        ("wan2",),
        True,
    )


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


# -- E-h, E-i: the outcome fields and IPv6 --------------------------------------------------------

#: Fields an outcome stored before Wave 3 (or before ADR-0016) does not have.
NEWER_FIELDS = {
    "egress",
    "mac_source",
    "srcintf",
    "dstintf",
    "srcmac",
    "untrusted_sources",
    "unconverted_dhcp",
    "alternatives",
    "lease_seconds",
    "last_dhcp_evidence",
    "source_table",
}


def _older(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _older(v) for k, v in value.items() if k not in NEWER_FIELDS}
    if isinstance(value, list):
        return [_older(v) for v in value]
    return value


@pytest.mark.usefixtures("leases")
def test_e_h_the_exit_fields_round_trip_and_old_outcomes_load(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=T2, srcintf="vlan42", dstintf="wan2")
    built(db_session)
    outcome = lookup(db_session, exits(db_session, {"hat-2": HAT_2}), public_ip=P2)  # round trip
    match = outcome.matches[0]
    assert (match.egress, match.traffic.srcintf, match.traffic.dstintf) == (
        "hat-2",
        "vlan42",
        "wan2",
    )
    assert match.chain is not None
    assert match.chain.mac_source == "lease"
    assert outcome.egress is not None
    assert [e.name for e in outcome.egress.egresses] == ["hat-2"]
    stored = _older(json.loads(json.dumps(outcome.to_dict())))
    assert "egress" not in stored
    assert "egress" not in stored["matches"][0]
    old = LookupOutcome.from_dict(stored)
    assert old.egress is None
    assert old.matches[0].egress is None
    assert (old.matches[0].traffic.srcintf, old.matches[0].traffic.dstintf) == (None, None)
    assert old.matches[0].chain is not None
    assert old.matches[0].chain.mac_source == "lease"
    assert (old.mode, old.double_nat, sessions(old)) == (
        outcome.mode,
        outcome.double_nat,
        sessions(outcome),
    )


def test_e_i_an_ipv6_exit_is_refused(db_session: Session) -> None:
    for value in (
        {"hat-1": direct(["2001:db8::1"])},
        {"hat-2": isp_nat(["2001:db8::/64"])},
    ):
        with pytest.raises(EgressValueError) as refused:
            validate_egresses(value)
        assert refused.value.code == "setting_ipv6_not_supported"
    # A value stored around the validator: reading the exits fails (the router's 503).
    set_setting(db_session, "nat.egresses", {"hat-1": direct(["2001:db8::1"])}, updated_by="x")
    db_session.commit()
    settings = LookupSettings.from_settings(get_settings(db_session))
    with pytest.raises(ValueError, match=r"nat\.egresses"):
        settings.egresses()


# -- E-k … E-k4: interfaces, a session's exit and the change warning ------------------------------

SHARED = {
    "hat-1": isp_nat([T1], public=[P1], interfaces=["wan1"]),
    "hat-2": isp_nat([T1], public=[P2], interfaces=["wan2"]),
}


def test_e_k_two_exits_sharing_a_nat_address_split_by_interface(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=T1, dstintf="wan1")
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T1, dstintf="wan2")
    evidence.traffic(AT, PC, 51003, DEST, 443, transip=T1, dstintf="wan3")
    settings = exits(db_session, SHARED)
    line_1 = lookup(db_session, settings, public_ip=P1)
    assert sessions(line_1) == [(PC, 51001, T1)]
    assert [m.egress for m in line_1.matches] == ["hat-1"]
    line_2 = lookup(db_session, settings, public_ip=P2)
    assert sessions(line_2) == [(PHONE, 51002, T1)]
    assert [m.egress for m in line_2.matches] == ["hat-2"]
    # The shared NAT address itself (exact, no interface condition): a wan3 row has no exit.
    for port, egress in ((51003, None), (51001, "hat-1"), (51002, "hat-2")):
        exact = lookup(db_session, settings, public_ip=T1, public_port=port)
        assert exact.mode == "exact"
        assert [m.egress for m in exact.matches] == [egress]


def test_e_k_the_interface_condition_drops_only_on_evidence(
    db_session: Session, evidence: Evidence
) -> None:
    """Rows of the exit's own NAT address only on an interface no exit lists: dropped with
    ``egress_interface_not_seen``; a shared address keeps it (``…_kept``), and rows on the other
    exit's interface are no evidence either; no row at all keeps it without a note."""
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T2, dstintf="wan3")
    hat_2 = isp_nat([T2], public=[P2], interfaces=["wan2"])
    settings = exits(db_session, {"hat-1": direct([P1], interfaces=["wan1"]), "hat-2": hat_2})
    dropped = lookup(db_session, settings, public_ip=P2)
    assert sessions(dropped) == [(PHONE, 51002, T2)]
    assert note(dropped, "egress_interface_not_seen") == {
        "egress": "hat-2",
        "interfaces": "wan2",
        "at": AT.astimezone(UTC).isoformat(),
    }
    assert dropped.egress is not None
    assert dropped.egress.searched.interface_filter is False
    assert "egress_interface_not_seen_kept" not in keys(dropped)
    # A shared NAT address: rows on wan3 and on hat-1's wan1, none on wan2: the condition stays.
    late = AT + timedelta(hours=2)
    evidence.traffic(late, PC, 51001, DEST, 443, transip=T1, dstintf="wan3")
    evidence.traffic(late, PC, 51005, DEST, 443, transip=T1, dstintf="wan1")
    shared = exits(db_session, SHARED)
    kept = lookup(db_session, shared, at=late, public_ip=P2)
    assert (kept.mode, kept.matches) == ("none", ())
    assert note(kept, "egress_interface_not_seen_kept")["egress"] == "hat-2"
    assert "egress_interface_not_seen" not in keys(kept)
    assert kept.egress is not None
    assert kept.egress.searched.interface_filter is True
    # An idle line: no row of its addresses near the moment keeps the condition, no note.
    idle = lookup(db_session, settings, at=tr(DAY, 4), public_ip=P2)
    assert not {"egress_interface_not_seen", "egress_interface_not_seen_kept"} & set(keys(idle))
    assert idle.egress is not None
    assert (idle.egress.searched.interfaces, idle.egress.searched.interface_filter) == (
        ("wan2",),
        True,
    )


def test_e_k2_a_nat_address_with_a_port_falls_back_when_the_check_fails(
    db_session: Session, evidence: Evidence
) -> None:
    other = "100.64.12.77"
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=other)
    settings = exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2], public=[P2])})
    outcome = lookup(db_session, settings, public_ip=T2, public_port=51001)
    assert resolution(outcome) == ("fallback", ["hat-2"])
    assert sessions(outcome) == [(PC, 51001, other)]
    assert "public_ip_not_transip" in keys(outcome)
    assert outcome.double_nat is True
    assert outcome.egress is not None
    assert (outcome.egress.searched.transips, outcome.egress.searched.unclaimed) == ((T2,), True)
    # Only an unclaimed public address active: the check passes; a long session through T2
    # logged after the probe window is the exact answer.
    later = tr(DAY, 15)
    evidence.traffic(later, PC, 51001, DEST, 443, transip="198.51.100.250")
    evidence.traffic(
        later + timedelta(minutes=30), PHONE, 51002, DEST, 443, transip=T2, duration=2400
    )
    exact = lookup(db_session, settings, at=later, public_ip=T2, public_port=51002)
    assert resolution(exact) == ("transip", ["hat-2"])
    assert (exact.mode, sessions(exact)) == ("exact", [(PHONE, 51002, T2)])
    assert exact.double_nat is True


def test_e_k3_a_session_that_changes_exit(db_session: Session, evidence: Evidence) -> None:
    evidence.traffic(
        AT - timedelta(seconds=30), PC, 51001, DEST, 443, transip=T1, dstintf="wan1", interim=True
    )
    evidence.traffic(AT + timedelta(seconds=60), PC, 51001, DEST, 443, transip=T1, dstintf="wan2")
    settings = exits(db_session, SHARED)
    outcome = lookup(db_session, settings, public_ip=T1, public_port=51001)
    assert (outcome.mode, len(outcome.matches)) == ("exact", 1)
    assert outcome.matches[0].egress == "hat-1"  # the interim row is nearest the moment
    assert note(outcome, "session_changed_exit") == {"count": 1, "egresses": "hat-1, hat-2"}
    # Interim row through T1, final row through T2: two sessions, each with its own exit.
    later = tr(DAY, 15)
    evidence.traffic(
        later - timedelta(seconds=30),
        PHONE,
        51002,
        DEST,
        443,
        transip=T1,
        dstintf="wan1",
        interim=True,
    )
    evidence.traffic(
        later + timedelta(seconds=30), PHONE, 51002, DEST, 443, transip=T2, dstintf="wan2"
    )
    unknown = exits(
        db_session,
        {"hat-1": isp_nat([T1], interfaces=["wan1"]), "hat-2": isp_nat([T2], interfaces=["wan2"])},
    )
    split = lookup(db_session, unknown, at=later, public_ip="192.0.2.44")
    assert resolution(split) == ("public_unknown", ["hat-1", "hat-2"])
    assert sorted(m.egress or "" for m in split.matches) == ["hat-1", "hat-2"]
    assert "session_changed_exit" not in keys(split)


BASE = {
    "hat-1": direct([P1], interfaces=["wan1"]),
    "hat-2": isp_nat([T2], public=[P2], interfaces=["wan2"]),
}


def edit(session: Session, value: dict[str, Any]) -> LookupSettings:
    """An administrator's change of ``nat.egresses`` (validated, audited, the change record
    kept, ``settings_admin``), made now: after every moment of these tests."""
    change_setting(session, "nat.egresses", value, actor="yonetici")
    session.commit()
    return LookupSettings.from_settings(get_settings(session))


def changed(session: Session, name: str, at: datetime, by: str = "yonetici") -> None:
    """Move exit ``name``'s last content change to ``at`` (by ``by``)."""
    record = dict(get_setting(session, CHANGES_KEY) or {})
    record[name] = {**record[name], "changed_at": at.astimezone(UTC).isoformat(), "changed_by": by}
    set_setting(session, CHANGES_KEY, record, updated_by=SYSTEM_AUTHOR)
    session.commit()


def warning(outcome: LookupOutcome) -> tuple[str, str] | None:
    found = [e for e in outcome.explanations if e.key == "egress_changed_after_event"]
    return (found[0].text_tr, found[0].text_en) if found else None


def test_e_k4_a_content_change_of_the_exit_used_is_named(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T2, dstintf="wan2")
    evidence.traffic(AT, PC, 51001, DEST, 443, transip="203.0.113.201", dstintf="wan1")
    settings = exits(db_session, BASE)
    first = lookup(db_session, settings, public_ip=P2)
    assert warning(first) is None
    assert first.egress is not None
    assert [(c.name, c.changed_by, c.after_event) for c in first.egress.changes] == [
        ("hat-2", ":migration", False)
    ]
    hat_2 = BASE["hat-2"]
    for unrecorded in (
        {"label": "Yedek hat"},
        {"interfaces": ["wan2", "wan3"]},
        {"port_preservation": "preserved"},
    ):
        outcome = lookup(
            db_session, edit(db_session, {**BASE, "hat-2": {**hat_2, **unrecorded}}), public_ip=P2
        )
        assert warning(outcome) is None, unrecorded
    # Another exit's change while P is claimed: none.
    other = {**BASE, "hat-1": direct(["203.0.113.202"], interfaces=["wan1"])}
    outcome = lookup(db_session, edit(db_session, other), public_ip=P2)
    assert warning(outcome) is None
    for content in (
        {"public": [P2, "198.51.100.78"]},
        {"transips": [T2, "100.64.13.10"]},
        {"public_dynamic": True},
        {"mode": "direct", "transips": []},
    ):
        exits(db_session, BASE)
        settings = edit(db_session, {**BASE, "hat-2": {**hat_2, **content}})
        outcome = lookup(db_session, settings, public_ip=P2)
        text = warning(outcome)
        assert text is not None, content
        assert "hat-2 (" in text[0]
        assert ", yonetici; şimdi: genel 198.51.100.77" in text[0]
        assert ", yonetici; now: public 198.51.100.77" in text[1]
        assert outcome.egress is not None
        assert outcome.egress.changed_after_event is True
        # An observed answer gives none whatever changed.
        observed = lookup(db_session, settings, public_ip="203.0.113.201", public_port=51001)
        assert resolution(observed) == ("observed", [])
        assert warning(observed) is None
        # A lookup after the change gives none.
        changed(db_session, "hat-2", tr(DAY, 6))
        assert warning(lookup(db_session, settings, public_ip=P2)) is None
    # The upgrade as author, in words.
    changed(db_session, "hat-2", tr(DAY, 12), by=":migration")
    text = warning(lookup(db_session, settings, public_ip=P2))
    assert text is not None
    assert "yükseltme (eski NAT ayarlarından aktarıldı)" in text[0]
    assert "upgrade (converted from the old NAT settings)" in text[1]
    assert ":migration" not in text[0] + text[1]


def test_e_k4_an_address_no_exit_claims_names_every_exit_changed_after(
    db_session: Session,
) -> None:
    other = "192.0.2.44"
    # (1) The catch-all deleted after the moment.
    converted, _dropped = legacy_egresses([P1], "", False)
    exits(db_session, converted, converted=True)
    assert resolution(lookup(db_session, legacy(db_session, [P1]), public_ip=other))[0] == (
        "public_unknown"
    )
    settings = edit(db_session, {"genel-1": converted["genel-1"]})
    outcome = lookup(db_session, settings, public_ip=other)
    assert resolution(outcome) == ("no_egress", ["genel-1"])
    text = warning(outcome)
    assert text is not None
    assert "tanimsiz (" in text[0]
    assert "yonetici; silindi)" in text[0]
    assert "yonetici; deleted)" in text[1]
    assert outcome.egress is not None
    assert [(c.name, c.deleted, c.after_event) for c in outcome.egress.changes] == [
        ("genel-1", False, False),
        ("tanimsiz", True, True),
    ]
    # (2) hat-2's public address edited twice after the moment, A -> B -> C; asked for A.
    a, b, c = P2, "198.51.100.78", "198.51.100.79"
    exits(db_session, BASE)
    hat_2 = BASE["hat-2"]
    edit(db_session, {**BASE, "hat-2": {**hat_2, "public": [b]}})
    settings = edit(db_session, {**BASE, "hat-2": {**hat_2, "public": [c]}})
    outcome = lookup(db_session, settings, public_ip=a)
    assert resolution(outcome) == ("no_egress", ["hat-1", "hat-2"])
    text = warning(outcome)
    assert text is not None
    assert f"yonetici; şimdi: genel {c}, NAT {T2})" in text[0]
    assert "hat-1 (" not in text[0]
    # (3) Renumbered A -> B, its interfaces added later (no content change): still named.
    exits(db_session, BASE)
    edit(db_session, {**BASE, "hat-2": {**hat_2, "public": [b]}})
    settings = edit(
        db_session, {**BASE, "hat-2": {**hat_2, "public": [b], "interfaces": ["wan2", "wan4"]}}
    )
    text = warning(lookup(db_session, settings, public_ip=a))
    assert text is not None
    assert "hat-2 (" in text[0]
    # (4) An exit outside the searched ones, written around change_setting (no entry in the
    # change record): the nat.egresses row's time and author stand in for its change.
    catch_all = {**BASE, "tanimsiz": {"mode": "isp_nat"}}
    exits(db_session, catch_all)
    hat_3 = isp_nat(["100.64.14.9"], public=["198.51.100.80"])
    by_hand = validate_egresses({**catch_all, "hat-3": hat_3})
    set_setting(db_session, "nat.egresses", by_hand, updated_by="yonetici")
    db_session.commit()
    settings = LookupSettings.from_settings(get_settings(db_session))
    outcome = lookup(db_session, settings, public_ip=other)
    assert resolution(outcome) == ("public_unknown", ["tanimsiz"])
    text = warning(outcome)
    assert text is not None
    assert "hat-3 (" in text[0]
    assert "hat-1 (" not in text[0]
    assert outcome.egress is not None
    assert [(c.name, c.changed_by, c.after_event) for c in outcome.egress.changes] == [
        ("tanimsiz", ":migration", False),
        ("hat-3", "yonetici", True),
    ]


# -- E-l, E-m, E-n, E-n2: unknown trandisp, unmapped addresses, the not-seen widening -------------


def test_e_l_an_unlisted_trandisp(db_session: Session, evidence: Evidence) -> None:
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip=T2, trandisp="snat+dnat")
    evidence.traffic(AT, PC, 51001, DEST, 443, transip="100.64.12.77", trandisp="snat+dnat")
    settings = exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2], public=[P2])})
    found = lookup(db_session, settings, public_ip=P2)  # the exit filter finds it
    assert sessions(found) == [(PHONE, 51002, T2)]
    assert "nat_unknown_trandisp" not in keys(found)
    catch_all = exits(db_session, {"hat-1": direct([P1]), "tanimsiz": {"mode": "isp_nat"}})
    counted = lookup(db_session, catch_all, public_ip="192.0.2.44")
    assert resolution(counted) == ("public_unknown", ["tanimsiz"])
    assert counted.matches == ()
    assert note(counted, "nat_unknown_trandisp") == {"count": 2, "values": "snat+dnat"}
    # Nor is such a row evidence against a direct line (the D15 check reads the listed values).
    quiet = lookup(db_session, catch_all, public_ip=P1, public_port=51009)
    assert (quiet.mode, resolution(quiet)) == ("none", ("public", ["hat-1"]))
    assert "public_ip_not_transip" not in keys(quiet)
    # A value outside the setting's pattern is never printed.
    evidence.traffic(AT, PC, 51003, DEST, 443, transip="100.64.12.78", trandisp="SNAT x")
    db_session.commit()
    masked = lookup(db_session, catch_all, public_ip="192.0.2.44")
    assert note(masked, "nat_unknown_trandisp") == {"count": 3, "values": "…, snat+dnat"}


def test_e_m_an_unclaimed_nat_address_near_the_moment_is_named(
    db_session: Session, evidence: Evidence
) -> None:
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=T1)
    evidence.traffic(AT, PHONE, 51002, DEST, 443, transip="198.51.100.250")
    settings = exits(db_session, {"hat-2": isp_nat([T2], public=[P2])})
    outcome = lookup(db_session, settings, public_ip=P2)
    assert note(outcome, "egress_unmapped_seen")["addresses"] == f"{T1}, 198.51.100.250"
    # Never the requested address itself (an observed one), never without exits.
    observed = lookup(db_session, settings, public_ip="198.51.100.250", public_port=51002)
    assert note(observed, "egress_unmapped_seen")["addresses"] == T1
    none = lookup(db_session, exits(db_session, {}), public_ip=P2)
    assert "egress_unmapped_seen" not in keys(none)


def test_e_m_an_isp_nat_lines_public_address_the_fortigate_used_is_named(
    db_session: Session, evidence: Evidence
) -> None:
    """Line 2's ISP router was switched to bridge mode and the exit was not edited: the
    FortiGate holds P2 itself and logs the requester's session with it while T2 is quiet. The
    search over T2 finds nobody, and the warning names P2 (never a bare "nobody")."""
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=T1, dstintf="wan1")
    evidence.traffic(AT, PHONE, 41000, DEST, 443, transip=P2, dstintf="wan2")
    hat_1 = isp_nat([T1], public=[P1], interfaces=["wan1"])
    hat_2 = isp_nat([T2], public=[P2], interfaces=["wan2"])
    settings = exits(db_session, {"hat-1": hat_1, "hat-2": hat_2})
    outcome = lookup(db_session, settings, public_ip=P2, public_port=41000)
    assert (outcome.mode, resolution(outcome)) == ("none", ("public", ["hat-2"]))
    assert note(outcome, "egress_unmapped_seen")["addresses"] == P2
    assert outcome.egress is not None
    assert outcome.egress.searched.unclaimed_seen == (P2,)


def test_e_n_an_isp_nat_line_not_seen_is_widened(db_session: Session, evidence: Evidence) -> None:
    other = "100.64.12.77"
    evidence.traffic(AT, PC, 51001, DEST, 443, transip=other)
    settings = exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2], public=[P2])})
    widened = lookup(db_session, settings, public_ip=P2)
    assert sessions(widened) == [(PC, 51001, other)]
    assert note(widened, "egress_transip_not_seen") == {
        "egress": "hat-2",
        "transips": T2,
        "at": AT.astimezone(UTC).isoformat(),
        "seen": other,
        "interfaces": "-",
    }
    assert "egress_search" not in keys(widened)
    assert widened.egress is not None
    assert widened.egress.searched.unclaimed is True
    quiet = lookup(db_session, settings, at=tr(DAY, 4), public_ip=P2)
    assert (quiet.mode, keys(quiet).count("egress_search")) == ("none", 1)
    assert "egress_transip_not_seen" not in keys(quiet)


#: The E-o site: hat-1 direct ``.200`` on wan1, a mail server's SNAT through the unconfigured
#: public ``.201`` every minute, one long session through ``.200:51001`` logged at its end.
O_AT = tr(DAY, 2, 10)
MAIL, MAIL_NAT = "172.20.10.25", "203.0.113.201"


def e_o_site(evidence: Evidence) -> None:
    for minute in range(61):
        at = tr(DAY, 1, 40) + timedelta(minutes=minute)
        evidence.traffic(
            at, MAIL, 30000 + minute, "198.51.100.25", 25, transip=MAIL_NAT, dstintf="wan1"
        )
    evidence.traffic(
        tr(DAY, 2, 40), PC, 51001, "198.51.100.10", 443, transip=P1, duration=2700, dstintf="wan1"
    )


def test_e_o_a_long_session_through_a_quiet_direct_line(
    db_session: Session, evidence: Evidence
) -> None:
    e_o_site(evidence)
    settings = exits(db_session, {"hat-1": direct([P1], interfaces=["wan1"])})
    exact = lookup(db_session, settings, at=O_AT, public_ip=P1, public_port=51001)
    assert resolution(exact) == ("public", ["hat-1"])
    assert (exact.mode, sessions(exact)) == ("exact", [(PC, 51001, P1)])
    assert exact.double_nat is False
    assert not {"double_nat", "public_ip_not_transip"} & set(keys(exact))
    assert note(exact, "egress_unmapped_seen")["addresses"] == MAIL_NAT
    candidates = lookup(db_session, settings, at=O_AT, public_ip=P1)
    assert candidates.mode == "candidates"
    assert sessions(candidates) == [(PC, 51001, P1)]
    assert "port_missing" in keys(candidates)
    mail = lookup(db_session, settings, at=O_AT, public_ip=MAIL_NAT)
    assert resolution(mail) == ("observed", [])


def test_e_o_the_fallback_searches_every_unclaimed_private_address(
    db_session: Session, evidence: Evidence
) -> None:
    e_o_site(evidence)
    privates = [f"100.64.12.{n}" for n in range(71, 77)]  # six: one more than the probe names
    for n, address in enumerate(privates):
        evidence.traffic(
            O_AT + timedelta(seconds=n),
            PHONE,
            52000 + n,
            DEST,
            443,
            transip=address,
            dstintf="wan1",
        )
    settings = exits(db_session, {"hat-1": direct([P1], interfaces=["wan1"])})
    outcome = lookup(db_session, settings, at=O_AT, public_ip=P1, public_port=51001)
    assert resolution(outcome) == ("fallback", ["hat-1"])
    found = {transip for _ip, _port, transip in sessions(outcome)}
    assert found == {P1, *privates}  # never a .201 session
    assert outcome.egress is not None
    assert len(outcome.egress.searched.unclaimed_seen) == 5
    assert outcome.egress.searched.outside_seen == tuple(privates[:5])


def test_e_n2_the_widening_keeps_the_interfaces(db_session: Session, evidence: Evidence) -> None:
    e_o_site(evidence)
    evidence.traffic(O_AT, PHONE, 52001, DEST, 443, transip="100.64.12.77", dstintf="wan1")
    hat_2 = isp_nat([T2], public=[P2], interfaces=["wan2"])
    settings = exits(db_session, {"hat-1": direct([P1], interfaces=["wan1"]), "hat-2": hat_2})
    # Idle line 2: widened on wan2 only, nobody.
    idle = lookup(db_session, settings, at=O_AT, public_ip=P2)
    assert (idle.mode, idle.matches) == ("none", ())
    assert note(idle, "egress_transip_not_seen")["interfaces"] == "wan2"
    assert not {"egress_interface_not_seen", "egress_interface_not_seen_kept"} & set(keys(idle))
    assert idle.egress is not None
    assert (idle.egress.searched.interfaces, idle.egress.searched.unclaimed) == (("wan2",), True)
    # Line 2's own long session overlapping the moment is found, and only it.
    evidence.traffic(
        tr(DAY, 2, 40), PC, 53001, DEST, 443, transip=T2, duration=2520, dstintf="wan2"
    )
    db_session.commit()
    own = lookup(db_session, settings, at=O_AT, public_ip=P2)
    assert sessions(own) == [(PC, 53001, T2)]
    assert "egress_transip_not_seen" in keys(own)
    # hat-1's fallback (E-c2) stays on wan1.
    fallback = lookup(db_session, settings, at=O_AT, public_ip=P1, public_port=51001)
    assert resolution(fallback) == ("fallback", ["hat-1"])
    assert {m.traffic.dstintf for m in fallback.matches} == {"wan1"}
    assert {(PC, 51001, P1), (PHONE, 52001, "100.64.12.77")} <= set(sessions(fallback))
    # A renamed interface: T2's rows near the moment carry port2, which no exit lists.
    renamed = tr(DAY, 4, 10)
    evidence.traffic(renamed, PC, 54001, DEST, 443, transip=T2, dstintf="port2")
    db_session.commit()
    outcome = lookup(db_session, settings, at=renamed, public_ip=P2)
    assert sessions(outcome) == [(PC, 54001, T2)]
    assert note(outcome, "egress_interface_not_seen")["interfaces"] == "wan2"
    assert outcome.egress is not None
    assert outcome.egress.searched.interface_filter is False


# -- E-q: internal NAT (D13) ----------------------------------------------------------------------

DMZ_NAT = "172.20.99.1"


def test_e_q_an_internal_nat_address(db_session: Session, evidence: Evidence) -> None:
    """LAN -> DMZ sessions through 172.20.99.1 every minute. An ``internal`` exit counts as an
    exit (2.8.3 as written), so an ``unconfigured`` search cannot meet a DMZ exit; E-q's clause
    on it is not exercised (contract deviations, c1)."""
    for minute in range(-20, 21):
        at = AT + timedelta(minutes=minute)
        evidence.traffic(
            at, PC, 40000 + minute + 20, "172.20.99.10", 8443, transip=DMZ_NAT, dstintf="dmz"
        )
    dmz = {"mode": "internal", "transips": [DMZ_NAT], "interfaces": ["dmz"]}
    settings = exits(db_session, {"hat-1": direct([P1]), "dmz": dmz})
    quiet = lookup(db_session, settings, public_ip=P1, public_port=51001)  # the check passes
    assert (quiet.mode, resolution(quiet)) == ("none", ("public", ["hat-1"]))
    assert not {"public_ip_not_transip", "egress_unmapped_seen"} & set(keys(quiet))
    itself = lookup(db_session, settings, public_ip=DMZ_NAT, public_port=40020)
    assert (itself.mode, resolution(itself)) == ("none", ("transip", ["dmz"]))
    assert note(itself, "egress_internal") == {"address": DMZ_NAT, "egress": "dmz"}
    assert "no_match_exact" not in keys(itself)  # no search
    unknown = exits(db_session, {"hat-1": direct([P1]), "hat-2": isp_nat([T2]), "dmz": dmz})
    searched = lookup(db_session, unknown, public_ip="192.0.2.44")
    assert resolution(searched) == ("public_unknown", ["hat-2"])
    assert searched.matches == ()
    assert "egress_unmapped_seen" not in keys(searched)
    # Without the dmz exit the address is unclaimed: named and searched in the fallback.
    plain = exits(db_session, {"hat-1": direct([P1])})
    outcome = lookup(db_session, plain, public_ip=P1, public_port=51001)
    assert resolution(outcome) == ("fallback", ["hat-1"])
    assert note(outcome, "egress_unmapped_seen")["addresses"] == DMZ_NAT
    assert {transip for *_rest, transip in sessions(outcome)} == {DMZ_NAT}


# -- E-p1: an untrusted sender in the probe -------------------------------------------------------

FORGED = "unknown-192.0.2.250"


def forged(session: Session, row: int) -> None:
    """Make the row ``row`` a line of an unconfigured sender (``trust.untrusted_sources``)."""
    if session.get(Source, FORGED) is None:
        session.add(Source(name=FORGED, type="generic", address="192.0.2.250", unknown=True))
    session.execute(update(FgTraffic).where(FgTraffic.id == row).values(source=FORGED))
    session.commit()


def test_e_p1_a_forged_line_never_decides_the_probe(
    db_session: Session, evidence: Evidence
) -> None:
    settings = exits(db_session, {"hat-1": direct([P1])})
    # Never observed: an unclaimed address only a forged line shows.
    forged(db_session, evidence.traffic(AT, PC, 51001, DEST, 443, transip="203.0.113.201"))
    outcome = lookup(db_session, settings, public_ip="203.0.113.201", public_port=51001)
    assert (outcome.mode, resolution(outcome)) == ("none", ("no_egress", ["hat-1"]))
    # Never fails a check: a forged private NAT address beside a quiet P1.
    forged(db_session, evidence.traffic(AT, PHONE, 51002, DEST, 443, transip="100.64.12.77"))
    outcome = lookup(db_session, settings, public_ip=P1, public_port=51001)
    assert resolution(outcome) == ("public", ["hat-1"])
    assert "public_ip_not_transip" not in keys(outcome)
    assert "egress_unmapped_seen" not in keys(outcome)
    # Never passes one: a forged line through P1 while a trusted private NAT address is active.
    later = tr(DAY, 15)
    forged(db_session, evidence.traffic(later, PC, 51001, DEST, 443, transip=P1))
    evidence.traffic(later, PHONE, 51002, DEST, 443, transip="100.64.12.78")
    db_session.commit()
    outcome = lookup(db_session, settings, at=later, public_ip=P1, public_port=51001)
    assert resolution(outcome) == ("fallback", ["hat-1"])
    assert "public_ip_not_transip" in keys(outcome)


# -- E-p2: an untrusted sender on a match ---------------------------------------------------------


def _twenty(session: Session, evidence: Evidence) -> int:
    """Twenty sessions through T1 near the moment, the forged line among them (its row id)."""
    for n in range(19):
        evidence.traffic(AT + timedelta(seconds=n), f"172.20.42.{100 + n}", 52000 + n, transip=T1)
    row = evidence.traffic(AT, PC, 51001, DEST, 443, transip=T1)
    forged(session, row)
    return row


def test_e_p2_a_forged_session_is_marked_on_its_match(
    db_session: Session, evidence: Evidence
) -> None:
    """The session search keeps every sender (D24), so the forged line matching the request is
    listed, marked on its own match, and named by the outcome; among 20 candidates only that
    match is marked."""
    settings = exits(db_session, {"hat-1": isp_nat([T1], public=[P1])})
    _twenty(db_session, evidence)
    outcome = lookup(db_session, settings, public_ip=P1, public_port=51001)
    assert (outcome.mode, len(outcome.matches)) == ("candidates", 20)
    marked = [m for m in outcome.matches if m.untrusted_sources]
    assert [(m.traffic.srcip, m.traffic.srcport) for m in marked] == [(PC, 51001)]
    assert marked[0].untrusted_sources == (FORGED,)
    assert note(outcome, "session_from_unknown_source") == {"count": 1, "sources": FORGED}
    assert "source_configured_later" not in keys(outcome)


def test_e_p2_a_sender_configured_later_is_named_instead(
    db_session: Session, evidence: Evidence
) -> None:
    """Once a FortiGate source covers the sender's address (D24's exception), its rows are
    trusted: no match is marked, and the outcome says that the sender was configured later."""
    settings = exits(db_session, {"hat-1": isp_nat([T1], public=[P1])})
    _twenty(db_session, evidence)
    db_session.add(Source(name="fortigate-2", type="fortigate", address="192.0.2.248/29"))
    db_session.commit()
    outcome = lookup(db_session, settings, public_ip=P1, public_port=51001)
    assert len(outcome.matches) == 20
    assert not any(m.untrusted_sources for m in outcome.matches)
    assert "session_from_unknown_source" not in keys(outcome)
    assert note(outcome, "source_configured_later") == {"sources": FORGED}


# -- the keys -----------------------------------------------------------------------------------


def test_the_exit_rules_share_one_definition_of_not_public() -> None:
    assert vars(sessions_module)["NOT_PUBLIC"] is egress_module.NOT_PUBLIC
    assert vars(engine_module)["NOT_PUBLIC"] is egress_module.NOT_PUBLIC


#: The explanation keys of the internet exits that C1's engine emits (2.11).
EXIT_KEYS = {
    "egress_search",
    "egress_public_unknown",
    "public_ip_no_egress",
    "egress_not_logged",
    "egress_transip_not_seen",
    "egress_interface_not_seen",
    "egress_interface_not_seen_kept",
    "egress_unmapped_seen",
    "egress_unconfigured",
    "port_hint",
    "nat_unknown_trandisp",
    "public_ip_not_transip",
    "egress_observed_not_configured",
    "egress_changed_after_event",
    "session_changed_exit",
    "egress_not_logged_but_seen",
    "egress_public_dynamic",
    "egress_internal",
}


def test_every_exit_key_is_emitted_and_the_retired_ones_never_are() -> None:
    source = Path(engine_module.__file__).read_text("utf-8")
    catalogue = set(EXPLANATIONS) | set(texts_module.ENGINE_EXPLANATIONS)
    assert EXIT_KEYS - catalogue == set()
    assert {key for key in EXIT_KEYS if f'"{key}"' not in source} == set()
    assert RETIRED - catalogue == set()  # stored lookups still render
    assert {key for key in RETIRED if key in source} == set()


#: The tone of every key of the 2.11 table (the web interface shows it, ``chain-text.ts``).
TONES_2_11 = {
    "no_lease": "info",
    "no_lease_for_mac": "info",
    "chain_missing": "warning",
    "fortigate_dhcp_not_converted": "warning",
    "srcmac_only": "warning",
    "srcmac_agrees": "info",
    "srcmac_mismatch": "warning",
    "srcmac_ambiguous": "warning",
    "srcmac_shared": "warning",
    "srcmac_other_lease": "warning",
    "srcmac_infrastructure": "info",
    "egress_search": "info",
    "egress_public_unknown": "warning",
    "public_ip_no_egress": "warning",
    "egress_not_logged": "warning",
    "egress_transip_not_seen": "warning",
    "egress_interface_not_seen": "warning",
    "egress_interface_not_seen_kept": "warning",
    "egress_unmapped_seen": "warning",
    "egress_unconfigured": "info",
    "port_hint": "warning",
    "nat_unknown_trandisp": "warning",
    "public_ip_not_transip": "warning",
    "fortigate_format_unverified": "info",
    "lease_renewal_not_seen": "warning",
    "fortigate_dhcp_not_converted_since_lease": "warning",
    "fortigate_dhcp_no_network": "warning",
    "egress_observed_not_configured": "warning",
    "egress_changed_after_event": "warning",
    "session_changed_exit": "warning",
    "egress_not_logged_but_seen": "warning",
    "dhcp_sources_overlap": "warning",
    "fortigate_format_verified": "info",
    "session_from_unknown_source": "warning",
    "source_configured_later": "info",
    "egress_public_dynamic": "warning",
    "egress_internal": "warning",
    "source_clock_offset": "warning",
}
#: The keys of earlier waves and the retired ones, with the tone the web interface gives them
#: today; ``info`` is information by design.
TONES_BEFORE = {
    "double_nat": "warning",
    "double_nat_no_candidates": "warning",
    "ip_conflict": "danger",
    "truncated": "warning",
    "only_in_vault": "warning",
    "not_in_database": "warning",
    "older_than_db_retention": "warning",
    "no_data_for_day": "warning",
    "lease_timeline_behind": "warning",
    "future_time": "warning",
    "exact_domain_unverified": "warning",
    "exact_nat": "success",
    "wan_transip_not_seen": "warning",
    "public_ip_not_configured": "info",
    **dict.fromkeys(
        (
            "arp_only",
            "candidates_without_destination",
            "destination_not_found",
            "destination_search",
            "exact_only_other_destinations",
            "exact_other_destinations",
            "exact_unverified_destinations",
            "guest_vlan",
            "infrastructure_device",
            "mac_multiple_ips",
            "mac_not_at_ip",
            "multiple_exact",
            "no_match",
            "no_match_candidates",
            "no_match_exact",
            "no_person",
            "person_department_current",
            "person_link_at_lookup_time",
            "person_link_backfilled",
            "person_unassigned_at_time",
            "port_missing",
            "randomized_mac",
        ),
        "info",
    ),
}
WEB_TONES = (
    Path(__file__).resolve().parents[2] / "web" / "src" / "pages" / "lookup" / "chain-text.ts"
)


def _emitted_keys() -> set[str]:
    """Every catalogue key written in a module of the lookup engine (the catalogue itself, in
    ``texts.py`` and ``types.py``, aside)."""
    catalogue = set(EXPLANATIONS) | set(texts_module.ENGINE_EXPLANATIONS)
    found: set[str] = set()
    for module in Path(engine_module.__file__).parent.glob("*.py"):
        if module.name in ("texts.py", "types.py"):
            continue
        for node in ast.walk(ast.parse(module.read_text("utf-8"))):
            if isinstance(node, ast.Constant) and node.value in catalogue:
                found.add(node.value)
    return found


def test_every_key_the_engine_can_emit_has_a_tone_or_is_info_by_design() -> None:
    """2.11: a new key without a tone fails here; the web map only names the keys that are not
    information, so every tone the test and the web both give must agree."""
    tones = TONES_BEFORE | TONES_2_11
    assert len(tones) == len(TONES_BEFORE) + len(TONES_2_11)  # one tone per key
    catalogue = set(EXPLANATIONS) | set(texts_module.ENGINE_EXPLANATIONS)
    assert catalogue == set(tones)
    emitted = _emitted_keys()
    assert emitted - set(tones) == set()
    assert emitted & RETIRED == set()  # in no module of the engine
    block = WEB_TONES.read_text("utf-8").split("EXPLANATION_TONE")[1].split("};")[0]
    web = dict(re.findall(r"^\s+(\w+): '(\w+)',$", block, re.MULTILINE))
    assert "exact_nat" in web  # the map was read
    assert {key: tone for key, tone in web.items() if tones.get(key) != tone} == {}
    assert {key for key, tone in tones.items() if tone == "info"} & set(web) == set()
