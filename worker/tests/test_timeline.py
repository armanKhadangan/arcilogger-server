"""Lease timeline rules (spec 9.1, 17 "correlation tests") — pure, no database.

Golden scenarios with hand-computed results: no deassign (timeout), supersede, deassign, renewal
silence, static ARP devices (merge, medium confidence), per-server lease time (dhcp61 1 h),
traffic only extends, conflicts by ARP, equal-time ordering, seeds.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

import pytest

from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.testing import EXAMPLE_SETTINGS
from arcilogger_worker.correlation.conflicts import find_conflicts
from arcilogger_worker.correlation.timeline import (
    ArpSighting,
    DhcpEvent,
    Evidence,
    LeaseInterval,
    LeaseTimeline,
    TimelineRules,
    TrafficSeen,
    build_timeline,
    evidence_key,
)
from arcilogger_worker.lookup.types import EvidenceRef, LookupSettings
from arcilogger_worker.testing import TR, tr

DAY = date(2026, 10, 12)
# The example site's settings: the shipped defaults plus its guest DHCP server's 1 h leases.
SETTINGS = {**DEFAULT_SETTINGS, **EXAMPLE_SETTINGS}
RULES = TimelineRules.from_settings(LookupSettings.from_settings(SETTINGS))
IP = "172.20.43.120"
A = "f0:1f:af:00:00:01"
B = "f0:1f:af:00:00:02"
HORIZON = tr(DAY + timedelta(days=4), 0)

_ids = iter(range(1, 1_000_000))


def ref(table: str, time: datetime, summary: str = "") -> EvidenceRef:
    return EvidenceRef(table=table, row_id=next(_ids), time=time, raw=None, summary=summary)


def assigned(time: datetime, mac: str = A, ip: str = IP, **kw: object) -> DhcpEvent:
    return DhcpEvent(
        time, "assigned", ip, mac, server=str(kw.get("server", "dhcp43")), vlan=43,
        hostname=kw.get("hostname"),  # type: ignore[arg-type]
        ref=ref("mt_dhcp", time, f"assigned {ip} {mac}"),
    )  # fmt: skip


def deassigned(time: datetime, mac: str = A, ip: str = IP) -> DhcpEvent:
    return DhcpEvent(
        time, "deassigned", ip, mac, server="dhcp43", ref=ref("mt_dhcp", time, "deassigned")
    )


def arp(time: datetime, mac: str = A, ip: str = IP) -> ArpSighting:
    return ArpSighting(time, ip, mac, vlan=43, ref=ref("mt_arp", time, "ARP-LOG"))


def traffic(time: datetime, ip: str = IP) -> TrafficSeen:
    return TrafficSeen(time, ip, ref=ref("fg_traffic", time, "traffic"))


def build(*events: Evidence, horizon: datetime = HORIZON) -> list[LeaseInterval]:
    return build_timeline(events, RULES, horizon=horizon)


def test_rules_from_shipped_settings() -> None:
    # Spec 9.1: lease time x 1.1 after the last evidence; dhcp61 has 1 h leases.
    assert RULES.timeout("dhcp43") == 95040  # 26 h 24 min
    assert RULES.timeout("dhcp61") == 3960  # 1 h 6 min
    assert RULES.timeout(None) == 95040
    assert (RULES.arp_interval, RULES.arp_grace) == (3600, 3960)
    assert RULES.shortest_timeout == 3960
    assert RULES.longest_window == 95040
    with pytest.raises(ValueError, match="arp_grace"):
        TimelineRules(arp_interval=3600, arp_grace=60)
    with pytest.raises(ValueError, match="positive"):
        TimelineRules(server_timeouts={"dhcp1": 0})


def test_no_deassign_times_out_lease_time_x_1_1_after_the_assignment() -> None:
    (lease,) = build(assigned(tr(DAY, 8), hostname="PC-07"))
    assert (lease.start, lease.end) == (tr(DAY, 8), tr(DAY + timedelta(days=1), 10, 24))
    assert lease.end_reason == "timeout"
    assert lease.confidence == "high"
    assert lease.hostname == "PC-07"
    assert lease.source_table == "mt_dhcp"
    assert lease.last_evidence == tr(DAY, 8)


def test_traffic_extends_and_the_timeout_counts_from_the_last_evidence() -> None:
    (lease,) = build(assigned(tr(DAY, 8)), traffic(tr(DAY, 20)), traffic(tr(DAY, 19, 30)))
    assert lease.end == tr(DAY + timedelta(days=1), 22, 24)  # 20:00 + 26 h 24 min
    assert lease.last_traffic == tr(DAY, 20)
    assert [r.table for r in lease.evidence] == ["mt_dhcp", "fg_traffic"]  # opening + latest


def test_deassign_closes_with_reason_dhcp() -> None:
    (lease,) = build(assigned(tr(DAY, 8)), traffic(tr(DAY, 12)), deassigned(tr(DAY, 17)))
    assert (lease.end, lease.end_reason) == (tr(DAY, 17), "dhcp")
    assert [r.summary for r in lease.evidence] == [
        "assigned 172.20.43.120 " + A,
        "traffic",
        "deassigned",
    ]


def test_deassign_of_another_mac_or_without_lease_is_ignored() -> None:
    timeline = LeaseTimeline(RULES)
    timeline.feed([deassigned(tr(DAY, 7)), assigned(tr(DAY, 8)), deassigned(tr(DAY, 9), mac=B)])
    (lease,) = timeline.finish(tr(DAY, 10))
    assert lease.is_open
    assert lease.end_reason == "open"
    assert timeline.stats["orphan_deassign"] == 2


def test_supersede_closes_the_old_mac_at_the_new_assignment() -> None:
    old, new = build(assigned(tr(DAY, 8)), assigned(tr(DAY, 12), mac=B), horizon=tr(DAY, 13))
    assert (old.mac, old.end, old.end_reason) == (A, tr(DAY, 12), "superseded")
    assert old.evidence[-1].summary == f"assigned {IP} {B}"  # the superseding row is cited
    assert (new.mac, new.start, new.end, new.end_reason) == (B, tr(DAY, 12), None, "open")
    assert find_conflicts([old, new]) == []  # touching, not overlapping


def test_renewal_silence_keeps_one_open_interval() -> None:
    """RouterOS logs no renewals: hourly ARP-LOG keeps the lease alive for days."""
    sightings = [arp(tr(DAY, 9) + timedelta(hours=h)) for h in range(72)]
    intervals = build(assigned(tr(DAY, 8)), *sightings, horizon=tr(DAY + timedelta(days=3), 9))
    (lease,) = intervals
    assert lease.is_open
    assert lease.start == tr(DAY, 8)
    assert lease.last_evidence == tr(DAY + timedelta(days=3), 8)
    # Opening row + only the latest ARP row (evidence stays small).
    assert [r.table for r in lease.evidence] == ["mt_dhcp", "mt_arp"]


def test_repeated_assignment_of_the_same_pair_is_a_continuation() -> None:
    (lease,) = build(
        assigned(tr(DAY, 8), hostname="PC-07"),
        assigned(tr(DAY, 20), hostname="PC-07-NEW"),
        horizon=tr(DAY, 21),
    )
    assert lease.is_open
    assert lease.start == tr(DAY, 8)
    assert lease.hostname == "PC-07-NEW"
    assert len(lease.evidence) == 2


def test_guest_server_lease_time_one_hour() -> None:
    guest = "172.20.61.105"
    (lease,) = build(assigned(tr(DAY, 10), ip=guest, server="dhcp61"))
    assert lease.end == tr(DAY, 11, 6)  # 1 h x 1.1
    assert lease.server == "dhcp61"


def test_traffic_never_creates_an_interval() -> None:
    assert build(traffic(tr(DAY, 9)), traffic(tr(DAY, 10))) == []


def test_static_device_arp_intervals_merge_and_are_medium_confidence() -> None:
    printer = "172.20.10.237"
    mac = "00:1b:a9:00:00:10"
    hours = [9, 10, 11, 12]  # hourly ARP-LOG
    jittered = [
        arp(tr(DAY, h) + timedelta(seconds=30 * i), mac=mac, ip=printer)
        for i, h in enumerate(hours)
    ]
    later = [arp(tr(DAY, 16), mac=mac, ip=printer), arp(tr(DAY, 17, 5), mac=mac, ip=printer)]
    first, second = build(*jittered, *later, traffic(tr(DAY, 14), ip=printer))
    # [first_seen, last_seen + 1 h]; the gap 12:01:30 → 16:00 is longer than the grace → split.
    assert (first.start, first.end, first.end_reason) == (tr(DAY, 9), tr(DAY, 13, 1, 30), "arp")
    assert first.confidence == "medium"
    assert first.source_table == "mt_arp"
    assert first.last_traffic is None  # traffic does not extend ARP intervals
    assert (second.start, second.end) == (tr(DAY, 16), tr(DAY, 18, 5))  # 65 min gap merged


def test_arp_of_a_dhcp_client_without_lease_creates_an_arp_interval() -> None:
    """A lease assigned before the logs start: ARP-LOG still proves the IP-MAC pair."""
    arp_interval, dhcp = build(arp(tr(DAY, 7)), arp(tr(DAY, 8)), assigned(tr(DAY, 8, 30)))
    assert (arp_interval.source_table, arp_interval.end, arp_interval.end_reason) == (
        "mt_arp",
        tr(DAY, 8, 30),
        "superseded",
    )
    assert (dhcp.source_table, dhcp.start, dhcp.confidence) == ("mt_dhcp", tr(DAY, 8, 30), "high")


def test_deassign_closes_an_arp_interval_of_the_same_pair() -> None:
    (lease,) = build(arp(tr(DAY, 7)), deassigned(tr(DAY, 7, 40)))
    assert (lease.source_table, lease.end, lease.end_reason) == ("mt_arp", tr(DAY, 7, 40), "dhcp")


def test_late_deassign_of_an_arp_interval_claims_only_one_interval() -> None:
    """08:05 is inside the grace (66 min) but after last + 1 h: the ARP interval ends at 08:00,
    not at the release (it never claims more than its last sighting allows)."""
    (lease,) = build(arp(tr(DAY, 7)), deassigned(tr(DAY, 8, 5)), horizon=tr(DAY, 9))
    assert (lease.end, lease.end_reason) == (tr(DAY, 8), "arp")


def test_stale_arp_entry_after_a_release_opens_nothing() -> None:
    """The router's ARP table still lists a released client for a while: a sighting within
    ``release_grace`` (300 s) after its ``deassigned`` is no evidence; a later one is."""
    timeline = LeaseTimeline(RULES)
    timeline.feed(
        [
            assigned(tr(DAY, 9)),
            deassigned(tr(DAY, 10)),
            arp(tr(DAY, 10, 2)),  # stale entry of the departed client
            assigned(tr(DAY, 10, 30), mac=B),
        ]
    )
    old, new = timeline.finish(tr(DAY, 11))
    assert (old.mac, old.end, old.end_reason) == (A, tr(DAY, 10), "dhcp")
    assert (new.mac, new.is_open) == (B, True)
    assert timeline.stats["ignored_arp_after_release"] == 1
    # Exactly at the grace it is still stale; one second later it is a sighting again.
    edge = build(assigned(tr(DAY, 9)), deassigned(tr(DAY, 10)), arp(tr(DAY, 10, 5)))
    assert len(edge) == 1
    later = build(assigned(tr(DAY, 9)), deassigned(tr(DAY, 10)), arp(tr(DAY, 10, 5, 1)))
    assert [(i.source_table, i.start) for i in later][1] == ("mt_arp", tr(DAY, 10, 5, 1))
    # Another MAC's sighting is not affected (that would hide an IP conflict).
    other = build(deassigned(tr(DAY, 10)), arp(tr(DAY, 10, 1), mac=B), horizon=tr(DAY, 11))
    assert [i.mac for i in other] == [B]


def test_release_grace_carries_across_feeds_and_can_be_switched_off() -> None:
    """A release just before the feed starts (incremental run, midnight of a rebuild) is
    passed in ``releases`` so the result does not depend on where the feed was cut."""
    release = deassigned(tr(DAY, 10))
    primed = build_timeline([arp(tr(DAY, 10, 2))], RULES, horizon=HORIZON, releases=[release])
    assert primed == []
    off = TimelineRules.from_settings(LookupSettings.from_settings(SETTINGS), release_grace=0)
    (stale,) = build_timeline(
        [assigned(tr(DAY, 9)), release, arp(tr(DAY, 10, 2))], off, horizon=tr(DAY, 12)
    )[1:]
    assert stale.source_table == "mt_arp"
    with pytest.raises(ValueError, match="release_grace"):
        TimelineRules(release_grace=-1)
    with pytest.raises(ValueError, match="release_grace"):
        TimelineRules(arp_interval=3600, arp_grace=3960, release_grace=3601)


def test_arp_interval_superseded_late_claims_only_one_interval() -> None:
    (lease, _) = build(arp(tr(DAY, 7)), assigned(tr(DAY, 8, 3), mac=B), horizon=tr(DAY, 9))
    # 08:03 is within the grace (66 min) but after last + 1 h: the ARP interval ends at 08:00.
    assert (lease.end, lease.end_reason) == (tr(DAY, 8), "arp")


def test_assignment_at_the_same_instant_absorbs_a_zero_length_arp_interval() -> None:
    timeline = LeaseTimeline(RULES)
    timeline.feed([assigned(tr(DAY, 8)), arp(tr(DAY, 8))])
    (lease,) = timeline.finish(tr(DAY, 9))
    assert lease.source_table == "mt_dhcp"
    assert timeline.stats["dropped_zero_length_arp"] == 1


def test_arp_of_another_mac_while_a_lease_is_open_is_an_ip_conflict() -> None:
    dhcp, rogue = build(
        assigned(tr(DAY, 8)), arp(tr(DAY, 9), mac=B), arp(tr(DAY, 10), mac=B), horizon=tr(DAY, 11)
    )
    assert dhcp.is_open
    assert rogue.is_open
    (conflict,) = find_conflicts([dhcp, rogue])
    assert conflict.ip == IP
    assert conflict.macs == (A, B)
    assert (conflict.start, conflict.end) == (tr(DAY, 9), None)
    assert conflict.dedup_key == f"ip_conflict:{IP}:{A},{B}:2026-10-12"
    # Infrastructure MACs (router proxy-ARP, CORE-R1 on many VLANs) are no conflict.
    assert find_conflicts([dhcp, rogue], infrastructure={B}) == []


def test_release_and_reassignment_in_the_same_second() -> None:
    """``deassigned`` sorts before ``assigned``: A ends with dhcp, B starts — no supersede."""
    old, new = build(
        assigned(tr(DAY, 8)), assigned(tr(DAY, 9), mac=B), deassigned(tr(DAY, 9)),
        horizon=tr(DAY, 10),
    )  # fmt: skip
    assert (old.end, old.end_reason) == (tr(DAY, 9), "dhcp")
    assert new.mac == B
    assert new.is_open


def test_evidence_must_not_go_back_in_time_across_batches() -> None:
    timeline = LeaseTimeline(RULES)
    timeline.feed([assigned(tr(DAY, 9))])
    with pytest.raises(ValueError, match="time order"):
        timeline.feed([traffic(tr(DAY, 8))])


def test_seeds_continue_and_time_out() -> None:
    seed = LeaseInterval(
        ip=IP,
        mac=A,
        vlan=43,
        server="dhcp43",
        hostname="PC-07",
        start=tr(DAY - timedelta(days=3), 8),
        end=None,
        end_reason="open",
        confidence="high",
        source_table="mt_dhcp",
        last_evidence=tr(DAY - timedelta(days=1), 23),
        evidence=(ref("mt_dhcp", tr(DAY - timedelta(days=3), 8), "opening"),),
        row_id=41,
    )
    (extended,) = build_timeline([traffic(tr(DAY, 6))], RULES, horizon=tr(DAY, 7), seeds=[seed])
    assert (extended.row_id, extended.start, extended.is_open) == (41, seed.start, True)
    assert extended.last_evidence == tr(DAY, 6)
    (expired,) = build_timeline([], RULES, horizon=tr(DAY + timedelta(days=1), 2), seeds=[seed])
    assert (expired.end, expired.end_reason) == (tr(DAY + timedelta(days=1), 1, 24), "timeout")
    with pytest.raises(ValueError, match="open"):
        LeaseTimeline(RULES, [expired])


def test_build_timeline_ignores_evidence_at_or_after_the_horizon() -> None:
    (lease,) = build(assigned(tr(DAY, 8)), deassigned(tr(DAY, 9)), horizon=tr(DAY, 9))
    assert lease.is_open  # the deassign at the horizon is not part of the data yet


def test_alive_until_exactly_the_deadline() -> None:
    deadline = tr(DAY, 10) + timedelta(seconds=3960)
    lease, *_ = build(
        assigned(tr(DAY, 10), ip="172.20.61.37", server="dhcp61"),
        traffic(deadline, ip="172.20.61.37"),
    )
    assert lease.last_evidence == deadline  # an event exactly at the deadline still counts
    (closed,) = build_timeline(
        [assigned(tr(DAY, 10), ip="172.20.61.37", server="dhcp61")],
        RULES,
        horizon=deadline + timedelta(microseconds=1),
    )
    assert closed.end == deadline


def test_evidence_order_at_equal_times() -> None:
    t = tr(DAY, 9)
    events: list[Evidence] = [assigned(t), deassigned(t), arp(t), traffic(t)]
    ranks = [evidence_key(e)[1] for e in sorted(events, key=evidence_key)]
    assert ranks == [0, 1, 2, 3]


# -- FortiGate DHCP (ADR-0016) --------------------------------------------------------------------

NET42 = "172.20.42.0/24"
NET61 = "172.20.61.0/24"
ROOT = ("fg_dhcp", "root")
PC57 = "00:00:5e:00:53:57"
PC31 = "00:00:5e:00:53:31"
FG_RULES = TimelineRules.from_settings(LookupSettings.from_settings(SETTINGS))
EXTENDING = TimelineRules.from_settings(
    LookupSettings.from_settings({**SETTINGS, "dhcp.fortigate_traffic_extends": True})
)


def fg_assigned(
    time: datetime,
    mac: str = PC57,
    ip: str = "172.20.42.57",
    *,
    lease: int | None = 86400,
    interface: str = "vlan42",
    vd: str | None = "root",
    network: str | None = NET42,
    hostname: str | None = None,
) -> DhcpEvent:
    lease_text = "-" if lease is None else lease
    return DhcpEvent(
        time, "assigned", ip, mac, server=interface, vlan=None, hostname=hostname,
        ref=ref("fg_dhcp", time, f"{interface} assigned {ip} for {mac} (FortiGate DHCP, logid "
                f"0100026001, lease {lease_text})"),
        table="fg_dhcp", lease_seconds=lease, origin=("fg_dhcp", vd), network=network,
    )  # fmt: skip


def fg_deassigned(time: datetime, mac: str = PC57, ip: str = "172.20.42.57") -> DhcpEvent:
    return DhcpEvent(
        time, "deassigned", ip, mac, server="vlan42",
        ref=ref("fg_dhcp", time, f"vlan42 deassigned {ip} for {mac} (FortiGate DHCP, logid "
                "0100026002, lease -)"),
        table="fg_dhcp", origin=ROOT, network=NET42,
    )  # fmt: skip


def mt_assigned(
    time: datetime, mac: str = PC57, ip: str = "172.20.42.57", *, vlan: int = 42
) -> DhcpEvent:
    return DhcpEvent(
        time, "assigned", ip, mac, server="dhcp42", vlan=vlan,
        ref=ref("mt_dhcp", time, f"dhcp42 assigned {ip} for {mac}"),
        origin=("mt_dhcp", None), network=NET42,
    )  # fmt: skip


def every(start: datetime, end: datetime, step: timedelta, ip: str) -> list[TrafficSeen]:
    found, moment = [], start
    while moment < end:
        found.append(traffic(moment, ip=ip))
        moment += step
    return found


def run(
    *events: Evidence, rules: TimelineRules = FG_RULES, horizon: datetime = HORIZON
) -> tuple[list[LeaseInterval], LeaseTimeline]:
    timeline = LeaseTimeline(rules)
    timeline.feed(e for e in events if e.time < horizon)
    return timeline.finish(horizon), timeline


def test_a_fortigate_ack_opens_a_high_lease_on_its_interface() -> None:
    ((lease,), _) = run(fg_assigned(tr(DAY, 9), hostname="PLN-PC14"), horizon=tr(DAY, 10))
    assert (lease.source_table, lease.confidence, lease.server) == ("fg_dhcp", "high", "vlan42")
    assert (lease.lease_seconds, lease.origin, lease.network) == (86400, ROOT, NET42)
    assert (lease.hostname, lease.is_open, lease.vlan) == ("PLN-PC14", True, None)


def test_a_renewal_is_a_continuation_with_the_new_lease_time() -> None:
    ((lease,), timeline) = run(
        fg_assigned(tr(DAY, 9)),
        fg_assigned(tr(DAY, 21), lease=43200),
        horizon=tr(DAY + timedelta(days=1), 8),
    )
    assert (lease.start, lease.is_open, lease.lease_seconds) == (tr(DAY, 9), True, 43200)
    assert lease.last_evidence == tr(DAY, 21)
    assert timeline.stats["continued"] == 1
    # It ends 43200 s after the renewal, the lease the FortiGate gave last.
    ((ended,), _) = run(fg_assigned(tr(DAY, 9)), fg_assigned(tr(DAY, 21), lease=43200))
    assert (ended.end, ended.end_reason) == (tr(DAY + timedelta(days=1), 9), "timeout")
    # A renewal that logs no usable lease keeps the one it had.
    ((kept,), _) = run(fg_assigned(tr(DAY, 9)), fg_assigned(tr(DAY, 21), lease=None))
    assert (kept.lease_seconds, kept.end) == (86400, tr(DAY + timedelta(days=1), 21))


def test_a_release_closes_a_fortigate_lease_with_dhcp() -> None:
    ((lease,), _) = run(fg_assigned(tr(DAY, 9)), fg_deassigned(tr(DAY, 17)))
    assert (lease.end, lease.end_reason, lease.last_evidence) == (tr(DAY, 17), "dhcp", tr(DAY, 17))


def test_a_logged_lease_ends_at_the_fortigates_own_end() -> None:
    """D6: lease_seconds after the last DHCP evidence, no timeout_percent margin; a fallback
    lease (``None``) keeps the server's or default time x timeout_percent."""
    ((week,), _) = run(fg_assigned(tr(DAY, 9), lease=604800), horizon=tr(DAY + timedelta(8), 0))
    assert (week.end, week.end_reason) == (tr(DAY + timedelta(days=7), 9), "timeout")
    ((fallback,), _) = run(fg_assigned(tr(DAY, 9), lease=None))
    assert fallback.end == tr(DAY + timedelta(days=1), 11, 24)  # 86400 x 1.1
    ((guest,), _) = run(
        fg_assigned(tr(DAY, 9), ip="172.20.61.20", interface="dhcp61", lease=None, network=NET61)
    )
    assert guest.end == tr(DAY, 10, 6)  # lease.server_seconds keys the interface name too
    assert FG_RULES.alive_window("fg_dhcp", "vlan42", 604800) == 604800
    assert FG_RULES.alive_window("fg_dhcp", "vlan42") == 95040
    assert FG_RULES.alive_window("mt_arp", "vlan42", 604800) == FG_RULES.arp_grace
    assert FG_RULES.longest_window_with([None, 604800, 3600]) == 604800
    assert FG_RULES.longest_window_with([]) == FG_RULES.longest_window


@pytest.mark.parametrize("lease", [None, 3600, 604800])
@pytest.mark.parametrize("server", ["vlan42", "dhcp61", None])
def test_the_timeline_and_the_lookup_compute_one_window(lease: int | None, server: str) -> None:
    settings = LookupSettings.from_settings(SETTINGS)
    assert FG_RULES.alive_window("fg_dhcp", server, lease) == settings.lease_timeout_seconds(
        server, lease
    )


def test_traffic_never_extends_a_fortigate_lease_by_default() -> None:
    """D26: a printer set statically to the address keeps sending traffic for two days after
    the client's ACK; the lease still ends 86400 s after the ACK."""
    flood = every(
        tr(DAY, 9, 5), tr(DAY + timedelta(days=2), 9), timedelta(minutes=5), "172.20.42.57"
    )
    ((lease,), _) = run(fg_assigned(tr(DAY, 9)), *flood, horizon=tr(DAY + timedelta(days=3), 0))
    assert (lease.end, lease.end_reason) == (tr(DAY + timedelta(days=1), 9), "timeout")
    assert lease.last_evidence == tr(DAY, 9)
    # The traffic stays evidence (its latest row before the end).
    assert lease.last_traffic == tr(DAY + timedelta(days=1), 9)
    assert [r.table for r in lease.evidence] == ["fg_dhcp", "fg_traffic"]
    # So do ARP sightings of the pair: they move no FortiGate deadline either.
    ((with_arp,), _) = run(
        fg_assigned(tr(DAY, 9)), arp(tr(DAY, 20), mac=PC57, ip="172.20.42.57"),
        horizon=tr(DAY + timedelta(days=3), 0),
    )  # fmt: skip
    assert (with_arp.end, with_arp.last_evidence) == (tr(DAY + timedelta(days=1), 9), tr(DAY, 9))
    assert [r.table for r in with_arp.evidence] == ["fg_dhcp", "mt_arp"]
    # With dhcp.fortigate_traffic_extends the same feed keeps it open (the RouterOS rule).
    ((open_lease,), _) = run(
        fg_assigned(tr(DAY, 9)), *flood, rules=EXTENDING, horizon=tr(DAY + timedelta(days=2), 9)
    )
    assert open_lease.is_open
    # Traffic still extends MikroTik leases, and never an ARP interval.
    ((mikrotik,), _) = run(mt_assigned(tr(DAY, 9)), *flood, horizon=tr(DAY + timedelta(days=2), 9))
    assert mikrotik.is_open
    ((arp_only,), _) = run(arp(tr(DAY, 9), mac=PC57, ip="172.20.42.57"), *flood)
    assert (arp_only.end, arp_only.end_reason) == (tr(DAY, 10), "arp")


def test_a_fortigate_ack_of_a_pair_open_as_mikrotik_starts_its_own_lease() -> None:
    """D5, a migration: the FortiGate ACK of the pair supersedes the MikroTik lease; a printer
    set statically to the address afterwards keeps nothing open."""
    move = datetime(2026, 10, 1, 9, 20, tzinfo=TR)
    flood = every(move + timedelta(minutes=5), move + timedelta(days=3), timedelta(minutes=5),
                  "172.20.42.57")  # fmt: skip
    (old, new), timeline = run(
        mt_assigned(move - timedelta(days=1)), fg_assigned(move), *flood,
        horizon=move + timedelta(days=4),
    )  # fmt: skip
    assert (old.source_table, old.end, old.end_reason) == ("mt_dhcp", move, "superseded")
    assert timeline.stats["superseded_across_sources"] == 1
    assert (new.source_table, new.start, new.lease_seconds) == ("fg_dhcp", move, 86400)
    assert (new.end, new.end_reason) == (move + timedelta(seconds=86400), "timeout")
    # The reverse opens a MikroTik lease without a logged lease time.
    (fortigate, mikrotik), timeline = run(
        fg_assigned(tr(DAY, 9)), mt_assigned(tr(DAY, 10)), horizon=tr(DAY, 11)
    )
    assert (fortigate.end, fortigate.end_reason) == (tr(DAY, 10), "superseded")
    assert (mikrotik.source_table, mikrotik.lease_seconds, mikrotik.is_open) == (
        "mt_dhcp", None, True,
    )  # fmt: skip
    assert timeline.stats["superseded_across_sources"] == 1


def test_a_client_that_moves_to_another_address_ends_its_lease_there() -> None:
    """D33: a DHCPDECLINE is not logged; the declining client's next ACK on the same
    interface, origin and network ends its lease of the declined address."""
    declined, taken = tr(DAY, 8, 41, 10), tr(DAY, 8, 41, 14)
    (first, second), timeline = run(
        fg_assigned(declined, PC31), fg_assigned(taken, PC31, "172.20.42.58"), horizon=tr(DAY, 12)
    )
    assert (first.ip, first.end, first.end_reason) == ("172.20.42.57", taken, "superseded")
    assert first.evidence[-1].time == taken  # the ACK that moved it is cited
    assert (second.ip, second.is_open) == ("172.20.42.58", True)
    assert timeline.stats["fg_mac_moved"] == 1
    # The printer's traffic on .57 afterwards opens nothing.
    intervals, _ = run(
        fg_assigned(declined, PC31), fg_assigned(taken, PC31, "172.20.42.58"),
        traffic(tr(DAY, 10, 12), ip="172.20.42.57"), horizon=tr(DAY, 12),
    )  # fmt: skip
    assert [i.ip for i in intervals if i.is_open] == ["172.20.42.58"]


@pytest.mark.parametrize(
    "second",
    [
        {"interface": "vlan43"},  # a laptop with one address on each of two interfaces
        {"vd": "vdom-2"},  # one MAC on two VDOMs
        {"ip": "172.20.43.58", "network": "172.20.43.0/24"},  # two relayed networks (F22)
        {"ip": "10.10.0.5", "network": None},  # an address in no configured network
    ],
)
def test_a_move_elsewhere_closes_nothing(second: dict[str, object]) -> None:
    ip = str(second.pop("ip", "172.20.42.58"))
    intervals, timeline = run(
        fg_assigned(tr(DAY, 8), PC31),
        fg_assigned(tr(DAY, 8, 1), PC31, ip, **second),  # type: ignore[arg-type]
        horizon=tr(DAY, 12),
    )
    assert [i.is_open for i in intervals] == [True, True]
    assert timeline.stats["fg_mac_moved"] == 0


def test_a_move_closes_on_a_network_entered_without_a_vlan_number() -> None:
    """Round 6: the network is the subnet (a flat LAN entered without a VLAN id included)."""
    flat = "172.20.42.0/24"  # its vlans row has vlan_id NULL: the key is still the subnet
    (first, second), timeline = run(
        fg_assigned(tr(DAY, 8), PC31, network=flat),
        fg_assigned(tr(DAY, 8, 1), PC31, "172.20.42.58", network=flat),
        horizon=tr(DAY, 12),
    )
    assert (first.end_reason, second.is_open, timeline.stats["fg_mac_moved"]) == (
        "superseded", True, 1,
    )  # fmt: skip


def test_a_mikrotik_assignment_never_moves_a_client() -> None:
    intervals, _ = run(
        mt_assigned(tr(DAY, 8), PC31), mt_assigned(tr(DAY, 8, 1), PC31, "172.20.42.58"),
        horizon=tr(DAY, 12),
    )  # fmt: skip
    assert [i.is_open for i in intervals] == [True, True]
    # Nor does it end the client's FortiGate lease on another address.
    intervals, _ = run(
        fg_assigned(tr(DAY, 8), PC31), mt_assigned(tr(DAY, 8, 1), PC31, "172.20.42.58"),
        horizon=tr(DAY, 12),
    )  # fmt: skip
    assert [i.is_open for i in intervals] == [True, True]


def test_a_fortigate_ack_ends_the_clients_mikrotik_lease_on_its_old_address() -> None:
    """D33, the MikroTik variant (a migration): compared by the network on both sides, whatever
    VLAN number the MikroTik row carries."""
    move = datetime(2026, 10, 1, 9, 20, tzinfo=TR)
    for vlan in (42, 142):  # 142: the number dhcp.server_vlan_regex gave, not the subnet's
        (old, new), timeline = run(
            mt_assigned(move - timedelta(hours=2), vlan=vlan),
            fg_assigned(move, ip="172.20.42.120"),
            traffic(move + timedelta(hours=1), ip="172.20.42.57"),  # a printer on .57 now
            horizon=move + timedelta(hours=3),
        )
        assert (old.ip, old.end, old.end_reason) == ("172.20.42.57", move, "superseded")
        assert (new.ip, new.is_open) == ("172.20.42.120", True)
        assert timeline.stats["superseded_across_sources"] == 1
    # An ACK for an address of another network closes nothing.
    intervals, _ = run(
        mt_assigned(move - timedelta(hours=2)),
        fg_assigned(move, ip="172.20.43.120", interface="vlan43", network="172.20.43.0/24"),
        horizon=move + timedelta(hours=3),
    )
    assert [i.is_open for i in intervals] == [True, True]


@pytest.mark.parametrize("minute_by_minute", [False, True])
def test_a_move_after_the_deadline_finds_the_lease_timed_out(minute_by_minute: bool) -> None:
    """D33 applies the other address's deadline first: the lease of .20 ended at 10:00 (lease
    3600), not at the ACK of .35 at 14:00 — fed at once or minute by minute alike."""
    events = [
        fg_assigned(tr(DAY, 9), "00:00:5e:00:53:21", "172.20.61.20", lease=3600,
                    interface="vlan61", network=NET61),
        fg_assigned(tr(DAY, 14), "00:00:5e:00:53:21", "172.20.61.35", lease=3600,
                    interface="vlan61", network=NET61),
    ]  # fmt: skip
    timeline = LeaseTimeline(FG_RULES)
    if minute_by_minute:
        moment = tr(DAY, 8)
        while moment < tr(DAY, 15):
            timeline.feed(e for e in events if moment <= e.time < moment + timedelta(minutes=1))
            moment += timedelta(minutes=1)
            timeline.expire(moment)
    else:
        timeline.feed(events)
    first, second = timeline.finish(tr(DAY, 15))
    assert (first.end, first.end_reason) == (tr(DAY, 10), "timeout")
    assert timeline.stats["fg_mac_moved"] == 0
    # No lease of .20 covers 12:00 (a MAC-only lookup for :21 at that moment finds none).
    assert first.end is not None
    assert not first.start <= tr(DAY, 12) < first.end
    assert second.is_open


def test_an_arp_entry_right_after_a_fortigate_release_opens_nothing() -> None:
    intervals, timeline = run(
        fg_assigned(tr(DAY, 9)), fg_deassigned(tr(DAY, 10)),
        arp(tr(DAY, 10, 2), mac=PC57, ip="172.20.42.57"), horizon=tr(DAY, 11),
    )  # fmt: skip
    assert [(i.source_table, i.end_reason) for i in intervals] == [("fg_dhcp", "dhcp")]
    assert timeline.stats["ignored_arp_after_release"] == 1


def test_rows_of_both_tables_at_one_instant_order_by_table() -> None:
    """Rows of two tables may share an id: MikroTik first (``TABLE_RANK``), whatever the id."""
    t = tr(DAY, 9)
    mikrotik = DhcpEvent(t, "assigned", "172.20.42.57", PC57, ref=EvidenceRef(
        table="mt_dhcp", row_id=7, time=t, raw=None, summary="dhcp42 assigned"))  # fmt: skip
    fortigate = DhcpEvent(t, "assigned", "172.20.42.57", PC31, table="fg_dhcp", ref=EvidenceRef(
        table="fg_dhcp", row_id=7, time=t, raw=None, summary="vlan42 assigned"))  # fmt: skip
    assert sorted([fortigate, mikrotik], key=evidence_key) == [mikrotik, fortigate]
    for order in ([fortigate, mikrotik], [mikrotik, fortigate]):
        intervals = build_timeline(order, FG_RULES, horizon=tr(DAY, 10))
        assert {(i.mac, i.source_table, i.end_reason) for i in intervals} == {
            (PC57, "mt_dhcp", "superseded"),
            (PC31, "fg_dhcp", "open"),
        }


def test_supersedes_across_dhcp_origins_are_counted() -> None:
    _, timeline = run(mt_assigned(tr(DAY, 8)), fg_assigned(tr(DAY, 9), PC31), horizon=tr(DAY, 10))
    assert timeline.stats["superseded_across_sources"] == 1
    _, timeline = run(fg_assigned(tr(DAY, 8)), fg_assigned(tr(DAY, 9), PC31, vd="vdom-2"),
                      horizon=tr(DAY, 10))  # fmt: skip
    assert timeline.stats["superseded_across_sources"] == 1
    # The same server giving the address to another client is no supersede across sources.
    _, timeline = run(fg_assigned(tr(DAY, 8)), fg_assigned(tr(DAY, 9), PC31), horizon=tr(DAY, 10))
    assert timeline.stats["superseded_across_sources"] == 0
    _, timeline = run(mt_assigned(tr(DAY, 8)), mt_assigned(tr(DAY, 9), PC31), horizon=tr(DAY, 10))
    assert timeline.stats["superseded_across_sources"] == 0


def test_a_renewal_ack_is_counted_from_two_fifths_of_the_lease_on() -> None:
    _, timeline = run(
        fg_assigned(tr(DAY, 9)), fg_assigned(tr(DAY, 9, 1)), fg_assigned(tr(DAY, 18, 37)),
        horizon=tr(DAY, 20),
    )  # fmt: skip
    # 09:01 is a repeat a minute later; 18:37 is 9 h 36 min after it (40 % of 24 h = 9.6 h).
    assert (timeline.stats["continued"], timeline.stats["fg_renewal_ack"]) == (2, 1)
    _, timeline = run(fg_assigned(tr(DAY, 9)), fg_assigned(tr(DAY, 18, 35)), horizon=tr(DAY, 20))
    assert timeline.stats["fg_renewal_ack"] == 0  # 9 h 35 min < 9.6 h
