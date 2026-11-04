"""Evidence references for database rows (``EvidenceRef`` with the raw line, neutral summary).

Used by the lease jobs (``leases.evidence``) and the lookup engine (traffic rows of a match), so a
row is always described the same way. Rows are SQLAlchemy result rows that carry the raw
reference columns (``source``, ``raw_day``, ``raw_line``, ``raw_offset``) and ``id``/``time``.

The two DHCP evidence tables (ADR-0016) are defined here once, for the lease timeline, the DHCP
source registry, the trust rules and the lookup: :data:`DHCP_TABLES`, whose order is the
tie-break rank of rows with equal times (:data:`TABLE_RANK`). A DHCP summary is
``<server or interface> <action> <ip> for <mac>[ <host>]…`` for both tables; its action and IP
are read from that fixed part (:func:`dhcp_action`, :func:`dhcp_ip`), never by position, because
a RouterOS server name may hold a space.

Two things the lease job stores beside a lease's refs, read by the missed-move check (issue
#127), are defined here too, so that both sides read them the same way:

* ``leases.moves``: the kept FortiGate ACKs of the lease's MAC for another address that the build
  saw and did not apply, one :class:`MoveGroup` per ``(ip, interface, vd, source, logid,
  dhcp_msg)`` with its times as integer epoch microseconds (UTC; :func:`epoch_us`), sorted, once
  each, and whether the job dropped any of them to keep the value bounded (:class:`Moves`,
  :func:`bound_moves`). :func:`moves_json` writes them in one order (:func:`merge_moves` and
  :func:`cut_moves` keep it), :func:`parse_moves` reads them back and never raises on what it
  finds stored. No row id: ids change when a day is reloaded from the archive, the raw line and
  the times do not.
* the ``"vd"`` key of a stored ``fg_dhcp`` ref (the VDOM of its row, ``null`` without one;
  :func:`ref_dict`, :func:`ref_vds`). It is not part of :class:`EvidenceRef` (the API and the
  stored outcomes never carry it), and the summary stays as :func:`fg_dhcp_ref` writes it.
"""

from __future__ import annotations

import re
from bisect import bisect_right
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from itertools import pairwise
from typing import Any, Final

from arcilogger_worker.lookup.types import EvidenceRef, RawRef

DHCP_TABLE: Final = "mt_dhcp"
FG_DHCP_TABLE: Final = "fg_dhcp"
ARP_TABLE: Final = "mt_arp"
TRAFFIC_TABLE: Final = "fg_traffic"
#: The DHCP evidence tables, MikroTik first: the index is the tie-break rank of equal times.
DHCP_TABLES: Final = (DHCP_TABLE, FG_DHCP_TABLE)
TABLE_RANK: Final[Mapping[str, int]] = {table: rank for rank, table in enumerate(DHCP_TABLES)}

#: ``<action> <ip> for `` of a DHCP summary: the first match is the row's own (the server or
#: interface before it is a log-supplied name; the host name after it can be anything).
_DHCP_SUMMARY = re.compile(r" (assigned|deassigned) (\S+) for ")
_SPACE = re.compile(r"\s")

#: The key of a stored ``fg_dhcp`` ref that holds its row's VDOM (issue #127).
REF_VD: Final = "vd"
#: A ref as the lease job knows it across runs and reloads: table, time and raw line (never the
#: row id, which a reload changes).
RefKey = tuple[str, datetime, RawRef | None]

#: A move group's ``(ip, interface, vd, source, logid, dhcp_msg)``.
MoveKey = tuple[str, str | None, str | None, str, str | None, str | None]

_EPOCH: Final = datetime(1970, 1, 1, tzinfo=UTC)
_MICROSECOND: Final = timedelta(microseconds=1)
#: The most times ``leases.moves`` keeps for one lease (:func:`bound_moves`), the newest. 1,000
#: hold 100 days of ACKs of one other address renewed every 2.4 hours (T1 of a 4.8-hour lease),
#: about 20 KB of JSON; 3.8's guard of the missed-move check decodes and tests 100 leases at the
#: cap, under its 0.2 s (``test_the_recorded_moves_of_a_hundred_chains_are_fast``). A MAC that
#: moves more often keeps its newest 1,000, marked truncated.
MOVE_TIMES_CAP: Final = 1000
#: The keys of a stored move group besides ``times``, in their sort order.
_MOVE_FIELDS: Final = ("ip", "interface", "vd", "source", "logid", "dhcp_msg")
#: The ones that can be ``null``, in the order of :class:`MoveGroup`'s fields.
_OPTIONAL_FIELDS: Final = ("interface", "vd", "logid", "dhcp_msg")


def raw_ref(row: Any) -> RawRef:
    return RawRef(source=row.source, day=row.raw_day, line=row.raw_line, offset=row.raw_offset)


def dhcp_ref(row: Any) -> EvidenceRef:
    """``dhcp42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14`` (as RouterOS logs it)."""
    host = f" {row.hostname}" if row.hostname else ""
    return EvidenceRef(
        table=DHCP_TABLE,
        row_id=row.id,
        time=row.time,
        raw=raw_ref(row),
        summary=f"{row.server} {row.action} {row.ip} for {row.mac}{host}",
    )


def fg_dhcp_ref(row: Any, action: str) -> EvidenceRef:
    """``vlan42 assigned 172.20.42.57 for 00:00:5e:00:53:57 PLN-PC14 (FortiGate DHCP, logid
    0100026001, lease 604800)``.

    ``action`` is the meaning ``dhcp.fortigate_logids`` gives the row's logid; the lease is the
    one the FortiGate logged (``-`` without one), so a stored interval keeps the lease of each
    ACK it cites. A space in the interface name becomes ``_`` (sahada doğrula: FortiOS interface
    names are free text of up to 32 characters). "FortiGate DHCP," stands between the device
    and the logid on purpose: the docs quote summaries, and a logid right after the device name
    reads like a model number.
    """
    interface = _SPACE.sub("_", row.interface) if row.interface else "-"
    host = f" {row.hostname}" if row.hostname else ""
    lease = "-" if row.lease is None else row.lease
    return EvidenceRef(
        table=FG_DHCP_TABLE,
        row_id=row.id,
        time=row.time,
        raw=raw_ref(row),
        summary=(
            f"{interface} {action} {row.ip} for {row.mac}{host} "
            f"(FortiGate DHCP, logid {row.logid}, lease {lease})"
        ),
    )


def _dhcp_part(ref: EvidenceRef) -> re.Match[str] | None:
    if ref.table not in DHCP_TABLES:
        return None
    return _DHCP_SUMMARY.search(ref.summary)


def dhcp_action(ref: EvidenceRef) -> str | None:
    """``assigned`` / ``deassigned`` of a DHCP table's ref; ``None`` for any other ref."""
    match = _dhcp_part(ref)
    return None if match is None else match.group(1)


def dhcp_ip(ref: EvidenceRef) -> str | None:
    """The IP of a DHCP table's ref (the report cross-checks the line that ended a lease with
    it); ``None`` for any other ref."""
    match = _dhcp_part(ref)
    return None if match is None else match.group(2)


def last_dhcp_evidence(
    refs: Sequence[EvidenceRef],
    end: datetime | None,
    end_reason: str | None,
    at: datetime | None = None,
) -> datetime | None:
    """The time of a lease's own newest DHCP record at or before ``at`` (no bound without it).

    ``refs``, ``end`` and ``end_reason`` are the lease's evidence, end and end reason (a stored
    lease or a timeline interval). The line that ended a ``superseded`` lease (a ref at or after
    its end) is another device's ACK, or this MAC's ACK for another address, so it is left out,
    as the report cross-check leaves its MAC claim out; a lease's own RELEASE (end reason
    ``dhcp``) counts. ARP and traffic refs are never DHCP evidence.
    """
    times = [
        ref.time
        for ref in refs
        if ref.table in DHCP_TABLES
        and (at is None or ref.time <= at)
        and not (end_reason == "superseded" and end is not None and ref.time >= end)
    ]
    return max(times, default=None)


def arp_ref(row: Any) -> EvidenceRef:
    """``ARP-LOG 172.20.42.57 00:00:5e:00:53:57 vlan42``."""
    interface = f" {row.interface}" if row.interface else ""
    return EvidenceRef(
        table=ARP_TABLE,
        row_id=row.id,
        time=row.time,
        raw=raw_ref(row),
        summary=f"ARP-LOG {row.ip} {row.mac}{interface}",
    )


def _endpoint(ip: str | None, port: int | None) -> str:
    return f"{ip or '?'}:{port}" if port is not None else str(ip or "?")


def traffic_summary(row: Any) -> str:
    """``172.20.60.183:51001 -> 203.0.113.10:443 proto 6 snat 100.64.12.9:51001``."""
    parts = [f"{_endpoint(row.srcip, row.srcport)} -> {_endpoint(row.dstip, row.dstport)}"]
    if row.proto is not None:
        parts.append(f"proto {row.proto}")
    if row.transip is not None:
        parts.append(f"{row.trandisp or 'nat'} {_endpoint(row.transip, row.transport)}")
    if row.is_interim:
        parts.append("(interim)")
    return " ".join(parts)


def traffic_ref(row: Any) -> EvidenceRef:
    return EvidenceRef(
        table=TRAFFIC_TABLE,
        row_id=row.id,
        time=row.time,
        raw=raw_ref(row),
        summary=traffic_summary(row),
    )


# -- the VDOM of a stored FortiGate ref (issue #127) ----------------------------------------------


def ref_key(ref: EvidenceRef) -> RefKey:
    return (ref.table, ref.time, ref.raw)


def ref_dict(ref: EvidenceRef, vds: Mapping[RefKey, str | None]) -> dict[str, Any]:
    """``ref.to_dict()`` as ``leases.evidence`` stores it: an ``fg_dhcp`` ref whose row's VDOM
    the job knows (``vds``, by :func:`ref_key`) also carries ``"vd"``."""
    item = ref.to_dict()
    if ref.table == FG_DHCP_TABLE and (key := ref_key(ref)) in vds:
        item[REF_VD] = vds[key]
    return item


def _ref_vd(item: Any) -> tuple[bool, str | None]:
    if not isinstance(item, Mapping) or item.get("table") != FG_DHCP_TABLE or REF_VD not in item:
        return (False, None)
    vd = item[REF_VD]
    return (True, vd) if vd is None or isinstance(vd, str) else (False, None)


def ref_vds(items: Any) -> tuple[tuple[bool, str | None], ...]:
    """``(known, vd)`` of every element of a stored ``leases.evidence``, in its order.

    ``known`` is false for a ref of another table, an ``fg_dhcp`` ref written before revision
    ``0022_lease_moves`` (no ``"vd"``) and anything unreadable; ``(True, None)`` is a row without
    a VDOM."""
    if not isinstance(items, Sequence) or isinstance(items, str | bytes):
        return ()
    return tuple(_ref_vd(item) for item in items)


def stored_vds(items: Any) -> dict[RefKey, str | None]:
    """The known VDOMs of a stored ``leases.evidence`` by :func:`ref_key` (the lease job writes
    them again when it rewrites the lease); an unreadable element is left out."""
    found: dict[RefKey, str | None] = {}
    if not isinstance(items, Sequence) or isinstance(items, str | bytes):
        return found
    for item, (known, vd) in zip(items, ref_vds(items), strict=True):
        if not known:
            continue
        try:
            ref = EvidenceRef.from_dict(item)
        except (KeyError, TypeError, ValueError):
            continue
        found[ref_key(ref)] = vd
    return found


# -- the missed moves recorded with a lease (``leases.moves``, issue #127) ------------------------


def epoch_us(moment: datetime) -> int:
    """``moment`` as integer microseconds since 1970-01-01 UTC (a naive one is refused)."""
    if moment.utcoffset() is None:
        raise ValueError("moment must be timezone-aware")
    return (moment - _EPOCH) // _MICROSECOND


def from_epoch_us(value: int) -> datetime:
    return _EPOCH + timedelta(microseconds=value)


def _order(value: str | None) -> tuple[bool, str]:
    # None after every text, the empty name included (one order in every job mode)
    return (value is None, value or "")


@dataclass(frozen=True, slots=True)
class MoveGroup:
    """The ACKs of one lease's MAC for ``ip`` with the same interface, VDOM, source, logid and
    DHCP message: what the missed-move check tests again with today's settings and networks.

    ``times`` are integer epoch microseconds (UTC; :func:`epoch_us`), sorted, once each.
    """

    ip: str
    interface: str | None
    vd: str | None
    source: str
    logid: str | None
    dhcp_msg: str | None
    times: tuple[int, ...] = ()

    @property
    def key(self) -> MoveKey:
        return (self.ip, self.interface, self.vd, self.source, self.logid, self.dhcp_msg)

    def seen_in(self, after: int, until: int) -> bool:
        """Whether one of the times lies in ``(after, until]`` (epoch microseconds)."""
        index = bisect_right(self.times, after)
        return index < len(self.times) and self.times[index] <= until


def _sort_key(group: MoveGroup) -> tuple[tuple[bool, str], ...]:
    return tuple(_order(value) for value in group.key)


def fg_dhcp_move(row: Any) -> MoveGroup:
    """The move group of one ``fg_dhcp`` row: ``time``, ``ip``, ``interface``, ``vd`` (its
    ``extra``'s, as :mod:`~arcilogger_worker.correlation.dhcp_sources` reads it), ``source``,
    ``logid`` and ``dhcp_msg``."""
    return MoveGroup(
        ip=str(row.ip),
        interface=row.interface,
        vd=row.vd,
        source=row.source,
        logid=row.logid,
        dhcp_msg=row.dhcp_msg,
        times=(epoch_us(row.time),),
    )


def merge_moves(*parts: Iterable[MoveGroup] | None) -> tuple[MoveGroup, ...]:
    """The union of ``parts`` (``None`` adds nothing): one group per key, its times sorted and
    once each, the groups in :func:`moves_json`'s order; a group without times is left out."""
    times: dict[MoveKey, set[int]] = {}
    for part in parts:
        for group in part or ():
            times.setdefault(group.key, set()).update(group.times)
    merged = (MoveGroup(*key, times=tuple(sorted(found))) for key, found in times.items() if found)
    return tuple(sorted(merged, key=_sort_key))


def cut_moves(
    groups: Iterable[MoveGroup], after: int | None, before: int | None
) -> tuple[MoveGroup, ...]:
    """The times of ``groups`` in the open range ``(after, before)`` (epoch microseconds; ``None``
    leaves that end open), for example the times inside an interval ``(start, end)``. ``after``
    one microsecond before a moment keeps that moment."""
    kept = (
        MoveGroup(
            *group.key,
            times=tuple(
                time
                for time in group.times
                if (after is None or time > after) and (before is None or time < before)
            ),
        )
        for group in groups
    )
    return merge_moves(kept)


def bound_moves(groups: Sequence[MoveGroup], after: int) -> tuple[tuple[MoveGroup, ...], bool]:
    """``groups`` (in :func:`moves_json`'s order) within the bound of ``leases.moves``: only the
    times after ``after`` (epoch microseconds), and of those at most :data:`MOVE_TIMES_CAP`, the
    newest (of equal times, those of the earlier group); and whether any time was dropped.

    Both cuts keep the newest times, so bounding a value that holds the bounded value of the
    same times gives that value again: a run that adds its range's moves to the stored ones
    stores what a rebuild of every day stores.
    """
    kept = cut_moves(groups, after, None)
    count = sum(len(group.times) for group in kept)
    if count > MOVE_TIMES_CAP:
        newest = sorted(
            ((-time, index) for index, group in enumerate(kept) for time in group.times)
        )[:MOVE_TIMES_CAP]
        times: dict[int, list[int]] = {}
        for time, index in newest:
            times.setdefault(index, []).append(-time)
        kept = merge_moves(
            MoveGroup(*group.key, times=tuple(sorted(times.get(index, ()))))
            for index, group in enumerate(kept)
        )
    return kept, count > MOVE_TIMES_CAP or count < sum(len(group.times) for group in groups)


@dataclass(frozen=True, slots=True)
class Moves:
    """A lease's ``leases.moves``: its groups and whether the lease job dropped any time of them
    to keep the value bounded (:func:`bound_moves`). A truncated value lacks moves, it never
    holds a wrong one: the missed-move check reads it as any other (positive evidence only)."""

    groups: tuple[MoveGroup, ...] = ()
    truncated: bool = False


def moves_json(groups: Iterable[MoveGroup], *, truncated: bool = False) -> dict[str, Any]:
    """``leases.moves`` of ``groups``: ``{"truncated": bool, "groups": [{"ip", "interface", "vd",
    "source", "logid", "dhcp_msg", "times": [epoch µs, …]}, …]}``, the groups merged
    (:func:`merge_moves`) and sorted by the six fields (``None`` after every text), so every job
    mode writes the same value; no group gives ``"groups": []`` (recorded, none; NULL, not
    recorded, is the caller's ``None``)."""
    return {
        "truncated": truncated,
        "groups": [
            {**dict(zip(_MOVE_FIELDS, group.key, strict=True)), "times": list(group.times)}
            for group in merge_moves(groups)
        ],
    }


def _move_group(item: Any) -> MoveGroup | None:
    if not isinstance(item, Mapping):
        return None
    ip, source, times = item.get("ip"), item.get("source"), item.get("times")
    optional = [item.get(name) for name in _OPTIONAL_FIELDS]
    if (
        not isinstance(ip, str)
        or not isinstance(source, str)
        or not all(value is None or isinstance(value, str) for value in optional)
        or not isinstance(times, Sequence)
        or isinstance(times, str | bytes)
    ):
        return None
    interface, vd, logid, dhcp_msg = optional
    return MoveGroup(
        ip=ip,
        interface=interface,
        vd=vd,
        source=source,
        logid=logid,
        dhcp_msg=dhcp_msg,
        times=tuple(t for t in times if isinstance(t, int) and not isinstance(t, bool)),
    )


def _canonical(groups: Sequence[MoveGroup]) -> bool:
    """Whether ``groups`` are as :func:`moves_json` writes them (one group per key, in its order,
    each with times strictly increasing): :func:`merge_moves` would return them unchanged."""
    keys = [_sort_key(group) for group in groups]
    return all(a < b for a, b in pairwise(keys)) and all(
        group.times and all(a < b for a, b in pairwise(group.times)) for group in groups
    )


def parse_moves(value: Any) -> Moves | None:
    """A stored ``leases.moves``, its groups in :func:`moves_json`'s order; ``None`` for NULL
    (not recorded) or a value that is no object with a list of groups. Never raises on what it
    finds: a group without its fields, a time that is no integer, a time twice or out of order
    are left out or put right, so a damaged value can only lack moves; a ``"truncated"`` that is
    not ``false`` reads as truncated. A value as the job writes it is only checked, not merged
    again (the lookup decodes every recorded move it reads)."""
    if not isinstance(value, Mapping):
        return None
    items = value.get("groups")
    if not isinstance(items, Sequence) or isinstance(items, str | bytes):
        return None
    groups = tuple(group for item in items if (group := _move_group(item)) is not None)
    return Moves(
        groups=groups if _canonical(groups) else merge_moves(groups),
        truncated=value.get("truncated") is not False,
    )
