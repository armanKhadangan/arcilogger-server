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
from arcilogger_worker.testing import tr

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
