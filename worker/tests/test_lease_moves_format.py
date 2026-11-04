"""The two things the lease job stores beside a lease's refs for the missed-move check (issue
#127): the format of ``leases.moves`` (groups, epoch-microsecond times, one order whatever the
input order, a damaged stored value read without raising) and the ``"vd"`` key of a stored
FortiGate ref (written only when known, read back per ref, ignored by ``EvidenceRef``)."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta, timezone
from itertools import permutations
from types import SimpleNamespace
from typing import Any

import pytest

from arcilogger_worker.lookup.refs import (
    REF_VD,
    MoveGroup,
    cut_moves,
    dhcp_ref,
    epoch_us,
    fg_dhcp_move,
    fg_dhcp_ref,
    from_epoch_us,
    merge_moves,
    moves_json,
    parse_moves,
    ref_dict,
    ref_key,
    ref_vds,
    stored_vds,
)
from arcilogger_worker.lookup.types import EvidenceRef

T0 = datetime(2026, 9, 29, 11, 23, 41, 250_000, tzinfo=UTC)
US = epoch_us(T0)
SECOND = 1_000_000


def _row(**fields: Any) -> SimpleNamespace:
    """An ``fg_dhcp`` row as the lease job reads it (``vd`` from ``extra``)."""
    base: dict[str, Any] = {
        "id": 77,
        "time": T0,
        "source": "fortigate",
        "raw_day": date(2026, 9, 29),
        "raw_line": 9,
        "raw_offset": 2048,
        "interface": "vlan44",
        "vd": "root",
        "ip": "172.20.44.58",
        "mac": "00:00:5e:00:53:57",
        "hostname": "PLN-PC14",
        "logid": "0100026001",
        "dhcp_msg": "Ack",
        "lease": 604800,
    }
    return SimpleNamespace(**(base | fields))


def _group(*seconds: int, **fields: Any) -> MoveGroup:
    return replace(fg_dhcp_move(_row(**fields)), times=tuple(US + s * SECOND for s in seconds))


# -- times ----------------------------------------------------------------------------------------


def test_a_time_is_whole_epoch_microseconds_in_utc() -> None:
    assert epoch_us(datetime(1970, 1, 1, tzinfo=UTC)) == 0
    assert epoch_us(T0) == 1_790_681_021_250_000
    assert from_epoch_us(epoch_us(T0)) == T0
    istanbul = T0.astimezone(timezone(timedelta(hours=3)))
    assert epoch_us(istanbul) == epoch_us(T0)  # the instant, not the wall clock
    assert epoch_us(T0 + timedelta(microseconds=1)) == epoch_us(T0) + 1
    assert from_epoch_us(epoch_us(T0)).utcoffset() == timedelta(0)
    with pytest.raises(ValueError, match="timezone-aware"):
        epoch_us(T0.replace(tzinfo=None))


# -- the groups -----------------------------------------------------------------------------------


def test_the_group_of_a_row() -> None:
    group = fg_dhcp_move(_row())
    assert group == MoveGroup(
        ip="172.20.44.58",
        interface="vlan44",
        vd="root",
        source="fortigate",
        logid="0100026001",
        dhcp_msg="Ack",
        times=(US,),
    )
    assert group.key == ("172.20.44.58", "vlan44", "root", "fortigate", "0100026001", "Ack")
    bare = fg_dhcp_move(_row(interface=None, vd=None, logid=None, dhcp_msg=None))
    assert bare.key == ("172.20.44.58", None, None, "fortigate", None, None)


def test_merging_is_a_union_with_each_time_once_in_order() -> None:
    merged = merge_moves([_group(30, 10)], None, [_group(10, 20), _group(5, vd="misafir")], [])
    assert merged == (
        _group(5, vd="misafir"),  # the groups by key
        _group(10, 20, 30),  # one group per key, its times sorted and once each
    )
    assert merge_moves() == merge_moves(None) == merge_moves([_group()]) == ()


def test_one_order_whatever_the_input_order() -> None:
    """The order of every job mode (live, range, single day), so that a lease whose moves did not
    change is not rewritten; ``None`` sorts after every text, also after an empty name."""
    groups = [
        _group(1, vd=None),
        _group(2, vd=""),
        _group(3, vd="root"),
        _group(4, ip="172.20.44.7"),
        _group(5, interface=None),
        _group(6, logid=None),
        _group(7, dhcp_msg=""),
        _group(8, source="fortigate-2"),
    ]
    written = {json.dumps(moves_json(order)) for order in permutations(groups)}
    assert len(written) == 1
    assert [group.vd for group in merge_moves(groups[:3])] == ["", "root", None]
    assert merge_moves(groups)[-1].ip == "172.20.44.7"  # text order of the address


def test_the_stored_value() -> None:
    stored = moves_json([_group(20, 10), _group(5, vd=None, dhcp_msg=None)])
    assert stored == [
        {
            "ip": "172.20.44.58",
            "interface": "vlan44",
            "vd": "root",
            "source": "fortigate",
            "logid": "0100026001",
            "dhcp_msg": "Ack",
            "times": [US + 10 * SECOND, US + 20 * SECOND],
        },
        {
            "ip": "172.20.44.58",
            "interface": "vlan44",
            "vd": None,
            "source": "fortigate",
            "logid": "0100026001",
            "dhcp_msg": None,
            "times": [US + 5 * SECOND],
        },
    ]
    assert moves_json([]) == moves_json([_group()]) == []  # recorded, none
    assert parse_moves(json.loads(json.dumps(stored))) == merge_moves(
        [_group(10, 20), _group(5, vd=None, dhcp_msg=None)]
    )


def test_cutting_keeps_the_times_of_an_open_range() -> None:
    groups = [_group(0, 10, 20), _group(30, vd="misafir")]
    assert cut_moves(groups, US, US + 30 * SECOND) == (_group(10, 20),)  # both ends left out
    assert cut_moves(groups, US - 1, None) == merge_moves(groups)  # one µs before keeps it
    assert cut_moves(groups, None, US + 10 * SECOND) == (_group(0),)
    assert cut_moves(groups, None, None) == merge_moves(groups)
    assert cut_moves(groups, US + 30 * SECOND, None) == ()  # an emptied group goes


def test_seen_in_reads_a_half_open_range() -> None:
    """``(after, until]``: the missed-move check reads the recorded moves after the lease's last
    record up to and including the first moment of the lookup's days."""
    group = _group(10, 20)
    assert group.seen_in(US, US + 10 * SECOND)
    assert not group.seen_in(US + 10 * SECOND, US + 19 * SECOND)
    assert group.seen_in(US + 10 * SECOND, US + 20 * SECOND)
    assert not group.seen_in(US + 20 * SECOND, US + 99 * SECOND)
    assert not group.seen_in(US, US + 9 * SECOND)
    assert not _group().seen_in(0, US)


# -- a damaged stored value -----------------------------------------------------------------------


@pytest.mark.parametrize("value", [None, {}, {"list": []}, "[]", b"[]", 0, True])
def test_null_or_no_list_is_not_recorded(value: Any) -> None:
    assert parse_moves(value) is None


def test_a_damaged_value_only_lacks_moves() -> None:
    good = moves_json([_group(10)])[0]
    stored: list[Any] = [
        None,
        "172.20.44.58",
        [good],
        {**good, "ip": None},
        {**good, "source": 7},
        {**good, "vd": ["root"]},
        {key: value for key, value in good.items() if key != "times"},
        {**good, "times": "1790681031250000"},
        {**good, "times": [US + 40 * SECOND, "x", 1.5, True, None, US + 30 * SECOND]},
        {**good, "times": [US + 10 * SECOND, US + 10 * SECOND]},
        {**good, "vd": "misafir", "times": []},
        {key: value for key, value in good.items() if key != "dhcp_msg"} | {"note": 1},
        good,
    ]
    assert parse_moves(stored) == (
        _group(10, 30, 40),
        _group(10, dhcp_msg=None),  # a missing optional field reads as null
    )
    assert parse_moves([]) == ()


# -- the VDOM of a stored FortiGate ref -----------------------------------------------------------


def _mt_ref() -> EvidenceRef:
    row = _row(server="dhcp42", action="assigned", ip="172.20.42.57")
    return dhcp_ref(row)


def test_a_fortigate_ref_carries_its_vdom_when_it_is_known() -> None:
    ref = fg_dhcp_ref(_row(), "assigned")
    other = fg_dhcp_ref(_row(raw_line=10, time=T0 + timedelta(minutes=1)), "assigned")
    vds = {ref_key(ref): "root", ref_key(other): None, ref_key(_mt_ref()): "root"}
    assert ref_key(ref) == ("fg_dhcp", T0, ref.raw)
    assert ref_key(replace(ref, row_id=9001)) == ref_key(ref)  # a reload changes the id only
    assert ref_dict(ref, vds) == ref.to_dict() | {REF_VD: "root"}
    assert ref_dict(other, vds) == other.to_dict() | {REF_VD: None}  # a site without VDOMs
    assert ref_dict(ref, {}) == ref.to_dict()
    assert ref_dict(_mt_ref(), vds) == _mt_ref().to_dict()  # MikroTik refs have no VDOM
    assert ref_dict(ref, vds)["summary"] == ref.summary == ref.to_dict()["summary"]
    assert EvidenceRef.from_dict(ref_dict(ref, vds)) == ref  # not part of EvidenceRef


def test_the_vdoms_of_a_stored_evidence_list() -> None:
    ref = fg_dhcp_ref(_row(), "assigned")
    other = fg_dhcp_ref(_row(raw_line=10, time=T0 + timedelta(minutes=1)), "assigned")
    legacy = fg_dhcp_ref(_row(raw_line=11, time=T0 + timedelta(minutes=2)), "assigned")
    vds = {ref_key(ref): "root", ref_key(other): None}
    stored = json.loads(json.dumps([ref_dict(r, vds) for r in (_mt_ref(), ref, other, legacy)]))
    assert ref_vds(stored) == ((False, None), (True, "root"), (True, None), (False, None))
    assert stored_vds(stored) == vds
    damaged = [
        {**stored[1], REF_VD: 42},
        {"table": "fg_dhcp", REF_VD: "root"},  # no time: left out of the map
        "fg_dhcp",
        stored[2],
    ]
    assert ref_vds(damaged) == ((False, None), (True, "root"), (False, None), (True, None))
    assert stored_vds(damaged) == {ref_key(other): None}
    nothing: tuple[Any, ...] = (None, {}, "[]")
    for value in nothing:
        assert ref_vds(value) == ()
        assert stored_vds(value) == {}
