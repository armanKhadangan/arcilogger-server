"""Properties of the lease timeline over both DHCP tables (ADR-0016) — pure, no database.

Random interleavings of MikroTik and FortiGate DHCP events (the lease varying per ACK,
cross-table ACKs of one pair, MACs moving to another address before and after their first
lease's deadline), ARP sightings and traffic:

* feeding in random chunks, with ``expire`` between them (the incremental job), gives the
  one-shot build;
* ``bounded()`` of an open interval (a seed) keeps its evidence, ``fg_dhcp`` refs included;
* ``refs.last_dhcp_evidence`` of every DHCP interval, without a moment and for any moment inside
  it, is the time of one of its own DHCP records at or before that moment, never the record
  that superseded it.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from itertools import count
from typing import Literal

from hypothesis import given, settings
from hypothesis import strategies as st

from arcilogger_db.settings import DEFAULT_SETTINGS
from arcilogger_db.testing import EXAMPLE_SETTINGS
from arcilogger_worker.correlation.timeline import (
    ArpSighting,
    DhcpEvent,
    Evidence,
    LeaseInterval,
    LeaseTimeline,
    TimelineRules,
    TrafficSeen,
    bounded,
    build_timeline,
)
from arcilogger_worker.lookup.refs import DHCP_TABLES, last_dhcp_evidence
from arcilogger_worker.lookup.types import EvidenceRef, LookupSettings
from arcilogger_worker.testing import tr

DAY = date(2026, 10, 12)
START = tr(DAY, 0)
HORIZON = START + timedelta(days=5)
SETTINGS = {**DEFAULT_SETTINGS, **EXAMPLE_SETTINGS, "lease.server_seconds": {"dhcp42": 7200}}
RULES = {
    extends: TimelineRules.from_settings(
        LookupSettings.from_settings({**SETTINGS, "dhcp.fortigate_traffic_extends": extends})
    )
    for extends in (False, True)
}
#: The addresses and the network each lies in (the last in none).
IPS = {
    "172.20.42.57": "172.20.42.0/24",
    "172.20.42.58": "172.20.42.0/24",
    "172.20.43.59": "172.20.43.0/24",
    "10.9.9.9": None,
}
MACS = ("00:00:5e:00:53:01", "00:00:5e:00:53:02", "00:00:5e:00:53:03")
KINDS = ("mt_assigned", "mt_deassigned", "fg_assigned", "fg_deassigned", "arp", "traffic")

raw_event = st.tuples(
    st.integers(min_value=0, max_value=4 * 24 * 60),  # minute
    st.sampled_from(KINDS),
    st.sampled_from(sorted(IPS)),
    st.sampled_from(MACS),
    st.sampled_from((None, 0, 3600, 43200, 86400)),  # the lease an ACK logs (0: unusable)
    st.sampled_from(("vlan42", "vlan43")),
    st.sampled_from(("root", "vdom-2")),
)


def _events(raw: list[tuple[int, str, str, str, int | None, str, str]]) -> list[Evidence]:
    ids = {"mt_dhcp": count(1), "fg_dhcp": count(1), "mt_arp": count(1), "fg_traffic": count(1)}
    events: list[Evidence] = []

    def ref(table: str, time: datetime, summary: str) -> EvidenceRef:
        return EvidenceRef(table=table, row_id=next(ids[table]), time=time, raw=None,
                           summary=summary)  # fmt: skip

    for minute, kind, ip, mac, lease, interface, vd in raw:
        time = START + timedelta(minutes=minute)

        if kind == "arp":
            events.append(
                ArpSighting(time, ip, mac, ref=ref("mt_arp", time, f"ARP-LOG {ip} {mac}"))
            )
        elif kind == "traffic":
            events.append(TrafficSeen(time, ip, ref=ref("fg_traffic", time, f"{ip} traffic")))
        else:
            action: Literal["assigned", "deassigned"] = (
                "assigned" if kind.endswith("_assigned") else "deassigned"
            )
            if kind.startswith("mt_"):
                events.append(
                    DhcpEvent(
                        time,
                        action,
                        ip,
                        mac,
                        server="dhcp42",
                        ref=ref("mt_dhcp", time, f"dhcp42 {action} {ip} for {mac}"),
                        origin=("mt_dhcp", None),
                        network=IPS[ip],
                    )
                )
            else:
                usable = lease if lease else None
                events.append(
                    DhcpEvent(
                        time,
                        action,
                        ip,
                        mac,
                        server=interface,
                        ref=ref(
                            "fg_dhcp",
                            time,
                            f"{interface} {action} {ip} for {mac} "
                            f"(FortiGate DHCP, logid 0100026001, lease {lease})",
                        ),
                        table="fg_dhcp",
                        lease_seconds=usable,
                        origin=("fg_dhcp", vd),
                        network=IPS[ip],
                    )
                )
    return events


def _order(intervals: list[LeaseInterval]) -> list[LeaseInterval]:
    return sorted(intervals, key=lambda i: (i.start, i.ip, i.mac, i.source_table, i.end or HORIZON))


@settings(max_examples=300, deadline=None)
@given(
    raw=st.lists(raw_event, max_size=60),
    cuts=st.lists(st.integers(min_value=1, max_value=4 * 24 * 60), max_size=8),
    extends=st.booleans(),
)
def test_chunked_feeding_with_expiry_equals_the_one_shot_build(
    raw: list[tuple[int, str, str, str, int | None, str, str]], cuts: list[int], extends: bool
) -> None:
    rules = RULES[extends]
    events = _events(raw)
    whole = build_timeline(events, rules, horizon=HORIZON)
    timeline = LeaseTimeline(rules)
    low = START
    for cut in sorted({START + timedelta(minutes=c) for c in cuts}):
        timeline.feed(e for e in events if low <= e.time < cut)
        timeline.expire(cut)
        # Every open interval is a seed the next run starts from: bounded() keeps its evidence
        # (the opening, the continuations of its own table, fg_dhcp included, the latest ARP
        # and traffic rows).
        for interval in timeline.open_intervals():
            assert bounded(interval) == interval.evidence
        low = cut
    timeline.feed(e for e in events if low <= e.time < HORIZON)
    assert _order(timeline.finish(HORIZON)) == _order(whole)


def _own(interval: LeaseInterval, events: list[Evidence]) -> set[EvidenceRef]:
    """The DHCP records of the interval itself: its pair's records of its table, and a release
    of the pair (whichever table logged it)."""
    return {
        event.ref
        for event in events
        if isinstance(event, DhcpEvent)
        and event.ref is not None
        and (event.ip, event.mac) == (interval.ip, interval.mac)
        and (event.table == interval.source_table or event.action == "deassigned")
    }


@settings(max_examples=300, deadline=None)
@given(
    raw=st.lists(raw_event, max_size=60),
    moments=st.lists(st.floats(min_value=0, max_value=1), min_size=1, max_size=4),
)
def test_the_last_dhcp_evidence_is_the_leases_own(
    raw: list[tuple[int, str, str, str, int | None, str, str]], moments: list[float]
) -> None:
    events = _events(raw)
    for interval in build_timeline(events, RULES[False], horizon=HORIZON):
        if interval.source_table not in DHCP_TABLES:
            continue
        own = _own(interval, events)
        end = interval.end or HORIZON
        for at in [None, *(interval.start + (end - interval.start) * share for share in moments)]:
            found = last_dhcp_evidence(interval.evidence, interval.end, interval.end_reason, at)
            if found is None:
                # Only an interval superseded at its own start has no record before its end.
                assert interval.end == interval.start
                continue
            assert at is None or found <= at
            assert any(ref.time == found and ref in own for ref in interval.evidence)
            closing = interval.evidence[-1]
            if interval.end_reason == "superseded" and closing.time == interval.end:
                assert found < interval.end
