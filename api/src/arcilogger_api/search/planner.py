"""Which tables a query reads, and how (ADR-0015, contract 2.5.2).

:func:`plan_query` turns a parsed :class:`~arcilogger_api.search.grammar.Query` into a
:class:`Plan` — pure decisions, no SQL (``sql.py`` compiles them):

* **Driver.** A query with a typed field (``srcip``, ``domain``, ``action`` …) is driven by the
  typed tables that have every typed field of it and a kind ``type:`` allows
  (:func:`~arcilogger_api.search.fields.branch_tables`): one :class:`Branch` per table, its
  ``messages``-only conditions (free text, ``host``, ``severity``, ``error``, ``subtype``) checked
  on the line's ``messages`` row by the raw reference. A query without one is driven by
  ``messages`` (one branch, the kinds as a condition on ``type``/``subtype``).
* **Binding.** ``vlan:`` and ``department:`` values are bound once, to the subnets the site's
  VLANs have now (:class:`~arcilogger_api.search.fields.SiteNetwork`); an unknown VLAN or
  department is refused here (``search_vlan_unknown``, ``search_department_unknown``). Pages,
  statistics and the export of a search reuse its plan, so they all see the same binding.
* **Selectivity.** A branch is index-backed when one of its positive conditions is fully
  covered by an index of its table (the "Idx" column of the field catalogue), or when it has no
  condition at all (a backward scan of the primary key). It decides only the notice
  ``query_not_selective`` and what the syntax help and the performance tests expect: every
  query runs the same budgeted window scan (``windows.py``).
* **Summary table.** Whether the statistics may come from ``search_counts``
  (:class:`Rollup`, contract 2.7.2): only ``source``, ``type``, ``action``, ``vlan`` and
  ``department`` (and their negations), every subnet private IPv4 of /24 or shorter. The range
  and the completeness of the counts are checked when the statistics run (``stats.py``).

Branch ranks are the positions in ``models.TIME_SERIES_TABLES`` (``messages`` 0, ``fg_traffic``
1 … ``mt_event`` 14, ``fg_dhcp`` 15, appended by revision ``0017_fortigate_dhcp``): rows of one
moment are ordered by rank, which the cursor relies on.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Final, Literal

from arcilogger_api.search.fields import (
    FIELDS,
    ContainsValue,
    DepartmentValue,
    IpNetwork,
    PatternValue,
    Present,
    SiteNetwork,
    Value,
    VlanValue,
    allowed_kinds,
    branch_tables,
)
from arcilogger_api.search.grammar import Clause, Query
from arcilogger_api.search.messages import notice
from arcilogger_db.kinds import KINDS, mt_event_events
from arcilogger_db.models import TIME_SERIES_TABLES
from arcilogger_db.searchcounts import PRIVATE_NETWORKS

Driver = Literal["messages", "typed"]

#: Fields the summary table can answer (contract 2.7.2).
ROLLUP_FIELDS: Final = frozenset({"source", "type", "action", "vlan", "department"})
#: A range must be longer than this for the summary table (shorter ones count directly).
ROLLUP_MIN_RANGE: Final = timedelta(hours=12)
#: Fields read from the typed table's own columns in a typed branch (every table has them).
SHARED_FIELDS: Final = frozenset({"source", "sender"})


def rank_of(table: str) -> int:
    """The branch rank of a table (``messages`` 0)."""
    return TIME_SERIES_TABLES.index(table)


@dataclass(frozen=True, slots=True)
class Condition:
    """One clause of the query, its ``vlan:``/``department:`` values bound to subnets."""

    clause: Clause
    #: Per value, the subnets it stands for (``vlan``/``department`` only; ``None`` otherwise).
    networks: tuple[tuple[IpNetwork, ...], ...] | None = None
    #: Per value of ``source:``, whether it names a configured network source (which also
    #: finds its members ``<name>-<ip>``); ``None`` for other fields.
    members: tuple[bool, ...] | None = None

    @property
    def field(self) -> str:
        return self.clause.field

    @property
    def negated(self) -> bool:
        return self.clause.negated

    @property
    def values(self) -> tuple[Value, ...]:
        return self.clause.values

    @property
    def present(self) -> bool:
        """``field:*`` (only tests that the field has a value)."""
        return self.clause.values == (Present(),)


@dataclass(frozen=True, slots=True)
class Branch:
    """One table a query reads."""

    table: str
    rank: int
    #: Conditions on the table's own columns (in a typed branch: the typed fields, ``source``
    #: and ``sender``; in the ``messages`` branch: every condition but ``type``).
    own: tuple[Condition, ...]
    #: ``messages``-only conditions of a typed branch, checked on the joined ``messages`` row.
    joined: tuple[Condition, ...]
    #: ``mt_event``: the ``event`` values of the kinds the query allows (``None``: other tables).
    events: tuple[str, ...] | None
    #: Index-backed (see the module text).
    selective: bool


@dataclass(frozen=True, slots=True)
class Rollup:
    """The query as conditions on ``search_counts`` (contract 2.7.2)."""

    #: The kinds the query allows (``kind = ANY(:kinds)``).
    kinds: tuple[str, ...]
    #: A typed-driven query: the branch tables (``tbl = ANY(:tables)``); ``None``: any line.
    tables: tuple[str, ...] | None
    #: The ``source``, ``action``, ``vlan`` and ``department`` conditions.
    conditions: tuple[Condition, ...]


@dataclass(frozen=True, slots=True)
class Plan:
    """What a search reads (contract 2.5.2); built once, reused by its pages and statistics."""

    query: Query
    driver: Driver
    branches: tuple[Branch, ...]
    #: The kinds ``type:`` allows, in the order of ``kinds.KINDS``.
    kinds: tuple[str, ...]
    #: ``None`` when the summary table cannot answer the query.
    rollup: Rollup | None
    #: The notices that follow from the query alone (``vault_only_before`` and
    #: ``counts_incomplete`` depend on the data and are added where they are found).
    notices: tuple[dict[str, Any], ...]

    @property
    def tables(self) -> tuple[str, ...]:
        return tuple(branch.table for branch in self.branches)

    @property
    def ranks(self) -> frozenset[int]:
        return frozenset(branch.rank for branch in self.branches)

    @property
    def selective(self) -> bool:
        """Some branch is index-backed (otherwise a filtered query gets
        ``query_not_selective``)."""
        return any(branch.selective for branch in self.branches)

    def rollup_eligible(self, start: datetime, end: datetime) -> bool:
        """The summary table may answer the statistics of ``[start, end)`` (if it is complete
        for that range: checked when they run)."""
        return self.rollup is not None and end - start > ROLLUP_MIN_RANGE

    def describe(self, start: datetime, end: datetime) -> dict[str, Any]:
        """``SearchPage.plan``."""
        return {
            "driver": self.driver,
            "tables": list(self.tables),
            "kinds": list(self.kinds),
            "selective": self.selective,
            "rollup_eligible": self.rollup_eligible(start, end),
        }


# -- binding --------------------------------------------------------------------------------------


def _bind(clause: Clause, site: SiteNetwork | None) -> Condition:
    if clause.field == "source":
        members = tuple(
            site is not None
            and isinstance(value, PatternValue)
            and value.exact
            and site.is_network_source(value.core)
            for value in clause.values
        )
        return Condition(clause, members=members)
    if clause.field not in ("vlan", "department"):
        return Condition(clause)
    if site is None:  # POST /search/parse: no database work, nothing bound
        return Condition(clause, tuple(() for _ in clause.values))
    networks = tuple(
        site.networks(value, clause.start, clause.end)
        for value in clause.values
        if isinstance(value, VlanValue | DepartmentValue)
    )
    return Condition(clause, networks)


# -- selectivity ----------------------------------------------------------------------------------


def _index_backed(condition: Condition, table: str) -> bool:
    """A positive condition read from an index of ``table`` (the catalogue's "Idx")."""
    if condition.negated or condition.present:
        return False
    return table in FIELDS[condition.field].indexed


def _selective(table: str, conditions: tuple[Condition, ...]) -> bool:
    if not conditions:
        return True  # a backward scan of the primary key, newest first
    return any(_index_backed(condition, table) for condition in conditions)


# -- the summary table ----------------------------------------------------------------------------


def _countable(network: IpNetwork) -> bool:
    """``net <<= subnet`` on the /24 of a private IPv4 client equals ``client <<= subnet``."""
    return (
        isinstance(network, ipaddress.IPv4Network)
        and network.prefixlen <= 24
        and any(network.subnet_of(private) for private in PRIVATE_NETWORKS)
    )


def _rollup(
    conditions: tuple[Condition, ...], kinds: tuple[str, ...], tables: tuple[str, ...] | None
) -> Rollup | None:
    for condition in conditions:
        if condition.field not in ROLLUP_FIELDS:
            return None
        for networks in condition.networks or ():
            if not all(_countable(network) for network in networks):
                return None
    kept = tuple(condition for condition in conditions if condition.field != "type")
    return Rollup(kinds, tables, kept)


# -- notices --------------------------------------------------------------------------------------


def _cased_non_ascii(text: str) -> bool:
    """A letter with a case outside ASCII (İ, ş …): free text matches it as typed."""
    return any(not char.isascii() and char.lower() != char.upper() for char in text)


def _notices(query: Query, branches: tuple[Branch, ...]) -> tuple[dict[str, Any], ...]:
    found: list[dict[str, Any]] = []
    free = [clause for clause in query.clauses if clause.field == "text"]
    terms = [
        value.term for clause in free for value in clause.values if isinstance(value, ContainsValue)
    ]
    if any(_cased_non_ascii(term) for term in terms):
        found.append(notice("text_case"))
    if free:
        found.append(notice("text_truncated"))
    if not query.empty and not any(branch.selective for branch in branches):
        found.append(notice("query_not_selective"))
    if any(clause.field == "department" for clause in query.clauses):
        found.append(notice("department_by_vlan"))
    return tuple(found)


# -- the plan -------------------------------------------------------------------------------------


def _kind_order(kinds: frozenset[str]) -> tuple[str, ...]:
    return tuple(key for key in KINDS if key in kinds)


def plan_query(query: Query, site: SiteNetwork | None = None) -> Plan:
    """The plan of ``query``; :class:`~arcilogger_api.search.messages.SearchQueryError` when no
    table has every typed field (``search_fields_incompatible``) or a VLAN or department is
    unknown. ``site=None`` binds nothing (``POST /search/parse``: no database work)."""
    clauses = query.clauses
    tables = branch_tables(clauses)
    kinds = _kind_order(allowed_kinds(clauses))
    conditions = tuple(_bind(clause, site) for clause in clauses)
    branches: tuple[Branch, ...]
    if tables is None:
        own = tuple(condition for condition in conditions if condition.field != "type")
        branches = (Branch("messages", 0, own, (), None, _selective("messages", conditions)),)
        driver: Driver = "messages"
    else:
        branches = tuple(_typed_branch(table, conditions, kinds) for table in tables)
        driver = "typed"
    rollup = _rollup(conditions, kinds, tables)
    return Plan(query, driver, branches, kinds, rollup, _notices(query, branches))


def _typed_branch(table: str, conditions: tuple[Condition, ...], kinds: tuple[str, ...]) -> Branch:
    own: list[Condition] = []
    joined: list[Condition] = []
    for condition in conditions:
        scope = FIELDS[condition.field].scope
        if condition.field == "type":
            continue  # the table (and mt_event.event) is the kind
        if scope == "typed" or condition.field in SHARED_FIELDS:
            own.append(condition)
        else:
            joined.append(condition)
    events = mt_event_events(kinds) if table == "mt_event" else None
    # mt_event's kind condition on ``event`` is index-backed when ``type:`` narrows it.
    indexed = [*own, *(c for c in conditions if c.field == "type" and table == "mt_event")]
    selective = any(_index_backed(condition, table) for condition in indexed)
    return Branch(table, rank_of(table), tuple(own), tuple(joined), events, selective)


def describe_clauses(query: Query) -> list[dict[str, Any]]:
    """``query.clauses`` of ``SearchPage`` and ``ParseResult``."""
    return [
        {
            "field": clause.field,
            "op": clause.op,
            "values": clause.value_texts(),
            "negated": clause.negated,
            "start": clause.start,
            "end": clause.end,
        }
        for clause in query.clauses
    ]


def describe_query(query: Query) -> dict[str, Any]:
    """``SearchPage.query``."""
    return {
        "normalized": query.normalized,
        "clauses": describe_clauses(query),
        "text": list(query.terms),
    }
