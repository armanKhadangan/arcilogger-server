"""The statistics of a search: total, histogram and five facets (contract 2.7).

Three modes (``SearchStats.mode``):

* ``rollup`` — every count from the hourly summary table ``search_counts`` (written by ingest in
  the transaction that loads the rows, so it is exact): a query the summary table can answer
  (:class:`~arcilogger_api.search.planner.Rollup`), a range longer than 12 hours and every file
  of it counted (``searchcounts.counts_complete``; otherwise ``counts_incomplete``);
* ``hybrid`` — the same, with the partial hours at both edges of the range counted directly (at
  most two scans of under an hour each);
* ``direct`` — everything else, counted over the plan's SQL in windows newest first, like the
  rows, under ``search.stats_budget_ms``; the facets over the newest
  ``search.facet_sample_rows`` matches (``sampled``). When the budget runs out the answer says
  how far it got (``complete: false``, ``covered_from``): the histogram shades the rest and the
  total reads "≥ N".

Buckets (:func:`bucket_seconds`) are aligned to site midnight. Facets are the top ten values
plus ``other``: ``source``, ``kind``, ``action``, ``vlan`` and ``department`` — the VLAN of the
client address's /24 when it is a private IPv4 address (as ``search_counts.net``; a /24 that
holds several smaller VLANs is "Birden çok VLAN"), the department that VLAN belongs to (by
VLAN, owner decision D1). Counted directly over ``messages`` rows only ``source`` and ``kind``
are known (the others are ``unavailable``: "Bir tür seçin").
"""

from __future__ import annotations

import ipaddress
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from time import monotonic
from typing import Any, Final, Literal

from sqlalchemy import RowMapping
from sqlalchemy.orm import Session

from arcilogger_api.search import sql
from arcilogger_api.search.fields import SiteNetwork
from arcilogger_api.search.messages import notice
from arcilogger_api.search.planner import Plan
from arcilogger_api.search.windows import (
    FIRST_WINDOW,
    GROW_SHARE,
    MIN_WINDOW,
    Clock,
    WindowHook,
    WindowTimeoutError,
    run_statement,
)
from arcilogger_db.kinds import KINDS
from arcilogger_db.searchcounts import counts_complete, hour_of, net_of

Mode = Literal["rollup", "hybrid", "direct"]
FacetName = Literal["source", "kind", "action", "vlan", "department"]
FACETS: Final[tuple[FacetName, ...]] = ("source", "kind", "action", "vlan", "department")
#: Values listed per facet; the rest is ``other``.
TOP_VALUES: Final = 10
#: ``(longest range, bucket seconds)``; a longer range has 6-hour buckets (contract 2.7.3).
BUCKETS: Final = (
    (timedelta(hours=2), 60),
    (timedelta(hours=12), 300),
    (timedelta(days=3), 3600),
    (timedelta(days=14), 3 * 3600),
)
LONGEST_BUCKET: Final = 6 * 3600
#: The facet value of a /24 that holds several (smaller) VLANs, and its labels.
MULTIPLE: Final = "multiple"
MULTIPLE_VLAN_LABELS: Final = ("Birden çok VLAN", "Several VLANs")
MULTIPLE_DEPARTMENT_LABELS: Final = ("Birden çok departman", "Several departments")
_STATE: Final = {"source", "kind", "action", "net"}


def bucket_seconds(span: timedelta) -> int:
    """The histogram bucket of a range (contract 2.7.3)."""
    for longest, seconds in BUCKETS:
        if span <= longest:
            return seconds
    return LONGEST_BUCKET


def bucket_starts(start: datetime, end: datetime, seconds: int) -> list[datetime]:
    """Every bucket of ``[start, end)``, the first one aligned to site midnight before
    ``start``."""
    step = timedelta(seconds=seconds)
    first = sql.BUCKET_ORIGIN + ((start - sql.BUCKET_ORIGIN) // step) * step
    starts = []
    at = first
    while at < end:
        starts.append(at)
        at += step
    return starts


@dataclass
class Tally:
    """Counts gathered from grouped statements (``sql.GROUPING`` names the sets)."""

    buckets: Counter[datetime] = field(default_factory=Counter)
    values: dict[str, Counter[Any]] = field(default_factory=lambda: {k: Counter() for k in _STATE})
    #: Rows the facets are counted over.
    facet_rows: int = 0

    def add(self, rows: Sequence[RowMapping], *, buckets: bool = True, facets: bool = True) -> int:
        """Add grouped ``rows``; returns the rows they count."""
        total = 0
        for row in rows:
            count = int(row["n"])
            grouping = int(row["g"])
            if grouping == sql.GROUPING["bucket"]:
                total += count
                if buckets:
                    self.buckets[row["bucket"]] += count
                continue
            if not facets:
                continue
            for name in _STATE:
                if grouping == sql.GROUPING[name] and row[name] is not None:
                    self.values[name][row[name]] += count
        if facets:
            self.facet_rows += total
        return total


# -- VLANs and departments of a /24 ---------------------------------------------------------------


@dataclass(frozen=True)
class SiteLabels:
    """The site's VLANs and departments as facet values."""

    site: SiteNetwork
    #: VLAN id → its Turkish name (``vlans.name_tr``).
    vlan_names: Mapping[int, str] = field(default_factory=dict)

    def _network(self, net: Any) -> ipaddress.IPv4Network | None:
        """A /24 of the summary table as a network; ``None`` for none or a public one."""
        if net is None:
            return None
        network = ipaddress.ip_network(str(net), strict=False)
        if not isinstance(network, ipaddress.IPv4Network):
            return None
        return network if net_of(network.network_address) is not None else None

    def _vlans_of(self, network: ipaddress.IPv4Network) -> tuple[list[int], list[int]]:
        """The VLANs holding ``network`` (most specific first) and those smaller than it."""
        holding: list[tuple[int, int]] = []
        smaller: list[int] = []
        for vlan, subnet in self.site.vlans.items():
            if not isinstance(subnet, ipaddress.IPv4Network):
                continue
            if network.subnet_of(subnet):
                holding.append((-subnet.prefixlen, vlan))
            elif subnet.subnet_of(network):
                smaller.append(vlan)
        return [vlan for _, vlan in sorted(holding)], smaller

    def vlan_of_net(self, net: Any) -> int | str | None:
        """The VLAN of a /24 (``search_counts.net``), :data:`MULTIPLE` for a /24 that holds
        smaller VLANs, ``None`` for none or a public network."""
        network = self._network(net)
        if network is None:
            return None
        holding, smaller = self._vlans_of(network)
        if smaller:
            return MULTIPLE
        return holding[0] if holding else None

    def department_of_vlans(self, vlans: Sequence[int]) -> str | None:
        names = {
            department.name
            for department in self.site.departments
            if any(vlan in department.vlans for vlan in vlans)
        }
        if len(names) > 1:
            return MULTIPLE
        return names.pop() if names else None

    def department_of_net(self, net: Any) -> str | None:
        """The department of the VLAN of a /24 (:data:`MULTIPLE` when its VLANs have several)."""
        network = self._network(net)
        if network is None:
            return None
        holding, smaller = self._vlans_of(network)
        if smaller:
            return self.department_of_vlans([*smaller, *holding[:1]])
        return self.department_of_vlans(holding[:1])

    def vlan_labels(self, value: int | str) -> tuple[str, str]:
        if value == MULTIPLE:
            return MULTIPLE_VLAN_LABELS
        name = self.vlan_names.get(int(value))
        label = f"VLAN {value}" + (f" ({name})" if name else "")
        return label, label


def _facet(
    counts: Counter[Any],
    status: str,
    labels: Any,
) -> dict[str, Any]:
    ordered = sorted(counts.items(), key=lambda item: (-item[1], str(item[0])))
    values = []
    for value, count in ordered[:TOP_VALUES]:
        label_tr, label_en = labels(value)
        values.append(
            {"value": str(value), "label_tr": label_tr, "label_en": label_en, "count": count}
        )
    return {
        "status": status,
        "values": values,
        "other": sum(count for _, count in ordered[TOP_VALUES:]),
    }


def _unavailable() -> dict[str, Any]:
    return {"status": "unavailable", "values": [], "other": 0}


def facets(tally: Tally, labels: SiteLabels, *, typed: bool, sampled: bool) -> dict[str, Any]:
    """``SearchStats.facets`` of a tally (``typed``: the action and the /24 are known)."""
    status = "sampled" if sampled else "ok"
    nets = tally.values["net"]
    vlans: Counter[Any] = Counter()
    departments: Counter[Any] = Counter()
    for net, count in nets.items():
        vlan = labels.vlan_of_net(net)
        if vlan is not None:
            vlans[vlan] += count
        department = labels.department_of_net(net)
        if department is not None:
            departments[department] += count

    def kind_labels(key: Any) -> tuple[str, str]:
        kind = KINDS.get(str(key))
        return (kind.label_tr, kind.label_en) if kind else (str(key), str(key))

    def plain(value: Any) -> tuple[str, str]:
        return str(value), str(value)

    def department_labels(value: Any) -> tuple[str, str]:
        return MULTIPLE_DEPARTMENT_LABELS if value == MULTIPLE else plain(value)

    found = {
        "source": _facet(tally.values["source"], status, plain),
        "kind": _facet(tally.values["kind"], status, kind_labels),
    }
    if typed:
        found["action"] = _facet(tally.values["action"], status, plain)
        found["vlan"] = _facet(vlans, status, labels.vlan_labels)
        found["department"] = _facet(departments, status, department_labels)
    else:
        found.update({name: _unavailable() for name in ("action", "vlan", "department")})
    return found


# -- counting -------------------------------------------------------------------------------------


@dataclass
class Counted:
    """What one way of counting found."""

    mode: Mode
    tally: Tally
    covered_from: datetime
    covered_to: datetime
    complete: bool
    sampled: bool = False
    #: The action and the /24 are known for every counted row.
    typed: bool = True


def _ceil_hour(time: datetime) -> datetime:
    floor = hour_of(time)
    return floor if floor == time else floor + timedelta(hours=1)


def _rollup(
    session: Session,
    plan: Plan,
    start: datetime,
    end: datetime,
    seconds: int,
    deadline: float,
    clock: Clock,
) -> Counted:
    if plan.rollup is None:  # pragma: no cover - checked by the caller
        raise ValueError("the summary table cannot answer this plan")
    tally = Tally()
    first_full, last_full = _ceil_hour(start), hour_of(end)
    complete = True
    covered_from, covered_to = start, end
    keys = plan.driver == "messages"  # the edges need each line's typed row, as the table has it
    edges = 0
    if last_full < end:
        edges += 1
        try:
            tally.add(
                run_statement(
                    session,
                    sql.count_statement(plan, last_full, end, seconds, keys=keys),
                    max(deadline - clock(), 0.001),
                )
            )
        except WindowTimeoutError:
            complete, covered_to = False, last_full
    try:
        tally.add(
            run_statement(
                session,
                sql.rollup_statement(plan.rollup, first_full, last_full, seconds),
                max(deadline - clock(), 0.001),
            )
        )
    except WindowTimeoutError:
        return Counted("rollup", Tally(), end, end, False)
    if start < first_full:
        edges += 1
        try:
            tally.add(
                run_statement(
                    session,
                    sql.count_statement(plan, start, first_full, seconds, keys=keys),
                    max(deadline - clock(), 0.001),
                )
            )
        except WindowTimeoutError:
            complete, covered_from = False, first_full
    return Counted("hybrid" if edges else "rollup", tally, covered_from, covered_to, complete)


def _direct(
    session: Session,
    plan: Plan,
    start: datetime,
    end: datetime,
    seconds: int,
    deadline: float,
    sample_rows: int,
    clock: Clock,
    hook: WindowHook | None,
) -> Counted:
    tally = Tally()
    upper = end
    size = FIRST_WINDOW
    windows = 0
    sampled = False
    while upper > start:
        left = deadline - clock()
        if left <= 0:
            break
        lower = max(start, upper - size)
        first = windows == 0
        statement = sql.count_statement(plan, lower, upper, seconds)
        asked = clock()
        try:
            rows = run_statement(
                session,
                statement,
                left / 2 if first and size > MIN_WINDOW else left,
                hook=hook,
                window=(lower, upper),
            )
        except WindowTimeoutError:
            if first and size > MIN_WINDOW:
                size = max(MIN_WINDOW, size / 2)
                continue
            break
        in_window = tally.add(rows, facets=False)
        if not sampled:
            if tally.facet_rows + in_window <= sample_rows:
                tally.add(rows, buckets=False)
            else:
                need = sample_rows - tally.facet_rows
                sampled = True
                try:
                    newest = run_statement(
                        session,
                        sql.count_statement(plan, lower, upper, seconds, limit=need),
                        max(deadline - clock(), 0.001),
                    )
                    tally.add(newest, buckets=False)
                except WindowTimeoutError:
                    pass  # the facets stay those of the newer windows
        windows += 1
        upper = lower
        if clock() - asked < GROW_SHARE * (deadline - clock()):
            size *= 2
    return Counted(
        "direct",
        tally,
        upper,
        end,
        upper <= start,
        sampled=sampled,
        typed=plan.driver == "typed",
    )


def run_stats(
    session: Session,
    plan: Plan,
    start: datetime,
    end: datetime,
    *,
    labels: SiteLabels,
    budget_seconds: float,
    sample_rows: int,
    clock: Clock = monotonic,
    hook: WindowHook | None = None,
    direct: bool = False,
) -> dict[str, Any]:
    """``SearchStats`` of ``[start, end)`` without its ``search_id`` (``direct``: never from
    the summary table — the tests compare the two)."""
    began = clock()
    deadline = began + budget_seconds
    seconds = bucket_seconds(end - start)
    notices: list[dict[str, Any]] = []
    counted: Counted | None = None
    if not direct and plan.rollup_eligible(start, end):
        if counts_complete(session.connection(), start, end):
            counted = _rollup(session, plan, start, end, seconds, deadline, clock)
        else:
            notices.append(notice("counts_incomplete"))
    if counted is None:
        counted = _direct(session, plan, start, end, seconds, deadline, sample_rows, clock, hook)
    tally = counted.tally
    histogram = [
        {"start": bucket, "count": tally.buckets.get(bucket, 0)}
        for bucket in bucket_starts(start, end, seconds)
    ]
    total = sum(tally.buckets.values())
    return {
        "mode": counted.mode,
        "complete": counted.complete,
        "covered_from": counted.covered_from,
        "covered_to": counted.covered_to,
        "total": {"value": total, "exact": counted.complete},
        "histogram": {"bucket_seconds": seconds, "buckets": histogram},
        "facets": facets(tally, labels, typed=counted.typed, sampled=counted.sampled),
        "sampled": counted.sampled,
        "sample_rows": tally.facet_rows if counted.sampled else None,
        "notices": [*plan.notices, *notices],
        "elapsed_ms": int((clock() - began) * 1000),
    }
